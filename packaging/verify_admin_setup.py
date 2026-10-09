"""Administrator-only isolated reproduction. Never changes the actual installed database."""
import ctypes
import importlib.util
import json
import os
import secrets
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / 'build/windows/releases/0.0.1'


def private_directory(path):
    path.mkdir(parents=True)
    subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', '*S-1-5-32-544:(OI)(CI)F',
        '*S-1-5-18:(OI)(CI)F', '*S-1-5-19:(OI)(CI)M'], check=True, capture_output=True)


def sddl(path):
    code = '[IO.Directory]::GetAccessControl($env:LABWORK_CHECK_PATH,[Security.AccessControl.AccessControlSections]::Access).GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::Access)'
    return subprocess.check_output(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', code],
        env=dict(os.environ, LABWORK_CHECK_PATH=str(path)), text=True).strip()


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise SystemExit('请从管理员 PowerShell 执行；本检查只使用新建的隔离目录。')
    report_path = Path('C:/labwork/tmp/admin-r1-check.json')
    report = {}
    try:
        base = Path('C:/labwork/tmp') / ('windows-r1-admin-' + secrets.token_hex(6))
        base.mkdir()
        broken = base / 'unpatched'
        private_directory(broken)
        password = secrets.token_hex(24)
        (broken / 'password.txt').write_text(password)
        result = subprocess.run([str(RELEASE / 'postgres/bin/initdb.exe'), '-D', str(broken / 'database'),
            '-U', 'postgres', '--encoding=UTF8', '--locale=C', '--auth=scram-sha-256', f'--pwfile={broken / "password.txt"}'],
            capture_output=True)
        (base / 'unpatched-error.log').write_bytes(result.stdout + result.stderr)
        (broken / 'password.txt').unlink()
        report['unpatched_exit'] = result.returncode
        report['unpatched_error'] = result.stderr.decode('utf-8', errors='replace')[-2000:]
        assert result.returncode != 0, '旧权限设置未重现错误，需检查平台差异'

        home = base / 'fixed'
        private_directory(home)
        initial_acl = sddl(home)
        request = {'host': 'localhost', 'port': str(free_port()), 'networks': '127.0.0.0/8',
            'pg_port': str(free_port()), 'web_port': str(free_port()), 'username': 'isolated_admin',
            'password': secrets.token_urlsafe(24)}
        request_path = home / 'request.json'
        request_path.write_text(json.dumps(request), encoding='utf-8')
        control_path = RELEASE / 'app/packaging/windows/control.py'
        result = subprocess.run([str(RELEASE / 'python/python.exe'), str(control_path), 'setup',
            '--home', str(home), '--request', str(request_path)], env=dict(os.environ, PYTHONUTF8='1'))
        report['fixed_exit'] = result.returncode
        (base / 'fixed-setup.log').write_bytes((home / 'logs/setup.log').read_bytes())
        assert result.returncode == 0, '修订版初始化失败，请查看隔离检查日志'
        assert sddl(home) == initial_acl, '目录原权限未恢复'
        assert (home / 'schema.json').exists()
        assert 'ADMIN_PASSWORD' not in json.loads((home / 'config.json').read_text(encoding='utf-8'))
        spec = importlib.util.spec_from_file_location('checked_control', control_path)
        control = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(control)
        with control.postgres_access(home):
            control.pg(home, 'start')
            try:
                import psycopg
                config = json.loads((home / 'config.json').read_text(encoding='utf-8'))
                with psycopg.connect(config['DATABASE_URL']) as conn:
                    assert conn.execute('SELECT count(*) FROM records_project').fetchone()[0] == 12
                    assert conn.execute('SELECT count(*) FROM records_method').fetchone()[0] == 14
            finally:
                control.pg(home, 'stop')
        assert sddl(home) == initial_acl
        report.update(acl_restored=True, projects=12, methods=14, isolated_path=str(base))
        # Existing installation metadata only; never read credentials or change files.
        installed = Path('C:/ProgramData/Labwork')
        database = installed / 'database'
        report['existing_install'] = {'config_exists': (installed / 'config.json').exists(),
            'schema_exists': (installed / 'schema.json').exists(), 'database_exists': database.exists(),
            'database_entries': sorted(p.name for p in database.iterdir()) if database.exists() else []}
        print('PASS: elevated initialization, PostgreSQL connection, and original ACL restoration.')
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
