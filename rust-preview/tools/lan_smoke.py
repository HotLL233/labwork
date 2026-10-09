"""Isolated LAN access regression; no existing service or workbook is modified."""
import http.cookiejar
import json
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAN_IP = "172.28.10.177"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def free_port():
    with socket.socket() as listener:
        listener.bind(("0.0.0.0", 0))
        return listener.getsockname()[1]


def run():
    password = "test-only-" + secrets.token_hex(16)
    with tempfile.TemporaryDirectory(prefix="labwork-lan-test-") as folder:
        folder = Path(folder)
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        lan = f"http://{LAN_IP}:{port}"
        config = folder / "labwork.json"
        config.write_text(json.dumps({"port": port, "data_dir": str(folder / "data"),
                                      "lan_ip": LAN_IP, "access_password": password}), encoding="utf-8")
        cookies = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                            urllib.request.HTTPCookieProcessor(cookies))

        def request(path, body=None, headers=None, origin=base):
            request_headers = dict(headers or {})
            data = None
            if body is not None:
                data = json.dumps(body).encode()
                request_headers["Content-Type"] = "application/json"
            req = urllib.request.Request(origin + path, data=data, headers=request_headers)
            try:
                response = opener.open(req, timeout=5)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                return response.status, response.headers, response.read()

        def expect(status, path, **kwargs):
            result = request(path, **kwargs)
            assert result[0] == status, (path, status, result[0], result[2][:200])
            return result

        log_path = folder / "server.log"
        with log_path.open("wb") as log:
            command = [str(ROOT / "target/release/labwork-preview.exe"), "--headless", "--config", str(config)]
            process = subprocess.Popen(command, stdout=log, stderr=log)
            try:
                for attempt in range(100):
                    assert process.poll() is None, "server stopped before readiness"
                    try:
                        expect(200, "/login")
                        break
                    except (OSError, urllib.error.URLError):
                        time.sleep(.1)
                else:
                    raise AssertionError("server readiness timeout")
                response = request("/")
                assert response[0] in (302, 303, 307, 308) and response[1]["Location"] == "/login"
                expect(401, "/api/catalog")
                for path in ("/login", "/login.js", "/app.css"):
                    assert expect(200, path)[2], path
                expect(401, "/api/catalog", origin=lan)
                expect(403, "/api/login", body={"password": password})
                expect(403, "/api/login", body={"password": password}, headers={"Origin": "http://evil.example"})
                expect(401, "/api/login", body={"password": "wrong-password"}, headers={"Origin": base})
                _, headers, _ = expect(200, "/api/login", body={"password": password}, headers={"Origin": base})
                cookie = headers["Set-Cookie"]
                assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
                catalog = json.loads(expect(200, "/api/catalog")[2])
                assert catalog["lan_url"] == lan and catalog["port"] == port
                expect(200, "/")
                expect(403, "/api/catalog", headers={"Host": "evil.example"})
                expect(403, "/api/login", body={"password": password}, headers={"Origin": "http://evil.example"})
                expect(200, "/api/login", origin=lan, body={"password": password}, headers={"Origin": lan})
                assert json.loads(expect(200, "/api/catalog", origin=lan)[2])["lan_url"] == lan
                other_port = free_port()
                duplicate = subprocess.run(command + ["--port", str(other_port)], stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, timeout=10)
                assert duplicate.returncode != 0, "same data directory accepted a second writer"
                assert password.encode() not in duplicate.stdout
                for _ in range(5):
                    expect(401, "/api/login", body={"password": "wrong-password"}, headers={"Origin": base})
                expect(429, "/api/login", body={"password": password}, headers={"Origin": base})
                expect(200, "/api/catalog")
            finally:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=10)
        assert password not in log_path.read_text(encoding="utf-8", errors="replace"), "password leaked to log"
        print(json.dumps({"ok": True, "version": catalog["version"], "checks": [
            "anonymous_redirect_and_api_401", "public_login_assets", "actual_lan_connection",
            "login_origin_required", "bad_password", "http_only_same_site_cookie", "authenticated_catalog",
            "host_origin_rejection", "lan_login", "same_data_second_instance_rejected",
            "five_failures_rate_limit", "password_not_logged"]}, ensure_ascii=False))


if __name__ == "__main__":
    run()
