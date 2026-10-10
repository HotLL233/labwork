"""Verify persisted login sessions against an isolated server and database."""
import argparse
import contextlib
import hashlib
import json
import re
import secrets
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAN_IP = '172.28.10.177'


def run(exe):
    with tempfile.TemporaryDirectory(prefix='labwork-auth-test-') as directory:
        folder = Path(directory)
        with socket.socket() as listener:
            listener.bind((LAN_IP, 0))
            port = listener.getsockname()[1]
        base = f'http://{LAN_IP}:{port}'
        password = 'test-only-' + secrets.token_hex(16)
        original_password = password
        config = folder / 'labwork.json'
        home = folder / 'data'
        log_path = folder / 'server.log'
        process = None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def save_config():
            config.write_text(json.dumps({'port': port, 'data_dir': str(home), 'lan_ip': LAN_IP,
                                          'access_password': password}), encoding='utf-8')

        def request(path, cookie=None, body=None, headers=None):
            fields = {'Origin': base, **(headers or {})}
            if cookie is not None:
                fields['Cookie'] = cookie
            data = None
            if body is not None:
                fields['Content-Type'] = 'application/json'
                data = json.dumps(body).encode()
            req = urllib.request.Request(base + path, data=data, headers=fields)
            try:
                response = opener.open(req, timeout=5)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                return response.status, response.headers, response.read()

        def expect(code, path, **kwargs):
            response = request(path, **kwargs)
            assert response[0] == code, (path, code, response[0])
            return response

        def stop():
            nonlocal process
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=10)
                process = None

        def start():
            nonlocal process
            with log_path.open('ab') as log:
                process = subprocess.Popen([str(exe), '--headless', '--config', str(config)],
                                           stdout=log, stderr=log)
            for _ in range(100):
                assert process.poll() is None, 'isolated server exited before readiness'
                try:
                    expect(200, '/login')
                    return
                except (OSError, urllib.error.URLError):
                    time.sleep(.1)
            raise AssertionError('isolated server readiness timeout')

        def login(remember):
            _, headers, _ = expect(200, '/api/login', body={'password': password, 'remember': remember})
            cookie = headers['Set-Cookie']
            assert 'HttpOnly' in cookie and 'SameSite=Strict' in cookie
            if remember:
                assert 'Max-Age=2592000' in cookie
            else:
                assert 'Max-Age' not in cookie and 'Expires=' not in cookie
            pair = cookie.split(';', 1)[0]
            assert pair.startswith('labwork_session=')
            return pair

        def rows():
            with contextlib.closing(sqlite3.connect(f'{(home / "preview.sqlite3").as_uri()}?mode=ro', uri=True)) as db:
                return db.execute('SELECT token_hash,expires,password_hash FROM login_sessions').fetchall()

        def token_hash(cookie):
            return hashlib.sha256(cookie.split('=', 1)[1].encode()).hexdigest()

        save_config()
        try:
            start()
            remembered = login(True)
            temporary = login(False)
            expect(200, '/api/catalog', cookie=remembered)
            expect(200, '/api/catalog', cookie=temporary)
            stored = rows()
            assert len(stored) == 1 and stored[0][0] == token_hash(remembered)
            assert 2591900 < stored[0][1] - int(time.time()) <= 2592000
            assert stored[0][2] == hashlib.sha256(password.encode()).hexdigest()
            for path in home.glob('preview.sqlite3*'):
                data = path.read_bytes()
                assert password.encode() not in data
                assert remembered.split('=', 1)[1].encode() not in data
                assert temporary.split('=', 1)[1].encode() not in data
            stop(); start()
            expect(200, '/api/catalog', cookie=remembered)
            expect(401, '/api/catalog', cookie=temporary)
            page = expect(200, '/', cookie=remembered)[2].decode()
            token = re.search(r'<meta name="labwork-token" content="([^"]+)"', page).group(1)
            expect(403, '/api/logout', cookie=remembered, body={})
            expect(403, '/api/logout', cookie=remembered, body={},
                   headers={'x-labwork-token': token, 'Origin': 'http://evil.example'})
            expect(200, '/api/catalog', cookie=remembered)
            _, headers, _ = expect(200, '/api/logout', cookie=remembered, body={},
                                    headers={'x-labwork-token': token})
            assert 'Max-Age=0' in headers['Set-Cookie']
            expect(401, '/api/catalog', cookie=remembered)
            assert not rows()
            stop(); start()
            expect(401, '/api/catalog', cookie=remembered)
            changed_cookie = login(True)
            stop()
            password = 'changed-test-only-' + secrets.token_hex(16)
            save_config(); start()
            expect(401, '/api/catalog', cookie=changed_cookie)
            assert not rows()
            expect(200, '/api/catalog', cookie=login(True))
        finally:
            stop()
        assert password not in log_path.read_text(encoding='utf-8', errors='replace')
        assert original_password not in log_path.read_text(encoding='utf-8', errors='replace')
        result = dict.fromkeys(['remember_cookie_30_days', 'temporary_cookie_no_max_age',
                               'remember_survives_restart', 'temporary_revoked_on_restart',
                               'logout_requires_csrf', 'logout_revokes_persisted_session',
                               'password_change_revokes_old_session', 'db_hashes_only',
                               'actual_private_ip_connection', 'test_processes_stopped'], True)
        target = ROOT.parent / 'runtime/auth-smoke-result.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, default=ROOT / 'target/release/labwork-preview.exe')
    run(parser.parse_args().exe.resolve())
