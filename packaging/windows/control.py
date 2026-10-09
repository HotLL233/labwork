"""Windows service setup and maintenance. All business data lives outside releases."""
import argparse
import ipaddress
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

RELEASE = Path(__file__).resolve().parents[3]
APP = RELEASE / 'app'
PYTHON = RELEASE / 'python/python.exe'


def run(*args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def pg(home, command, release=RELEASE):
    args = [release / 'postgres/bin/pg_ctl.exe', command, '-D', home / 'database', '-w', '-t', '45']
    if command == 'start':
        args += ['-l', home / 'logs/postgres.log']
    elif command == 'stop':
        args += ['-m', 'fast']
    run(*args, stdout=subprocess.DEVNULL)


def setup(home, request_path):
    request = read(request_path)
    try:
        host = request['host'].strip()
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.\-]{0,252}', host):
            raise ValueError('服务器地址格式不正确')
        port = int(request['port'])
        if not 1024 <= port <= 65535:
            raise ValueError('HTTPS 端口须为 1024–65535')
        networks = request['networks']
        for item in networks.split(','):
            ipaddress.ip_network(item.strip())
        if not re.fullmatch(r'[\w.@+\-]{1,150}', request['username']):
            raise ValueError('管理员用户名格式不正确')
        if not (home / 'config.json').exists() and len(request['password']) < 12:
            raise ValueError('管理员密码至少 12 位')
        for name in ('logs', 'workbooks', 'backups', 'certificates'):
            (home / name).mkdir(parents=True, exist_ok=True)
        config_path = home / 'config.json'
        if not config_path.exists():
            if (home / 'database').exists():
                raise ValueError('发现未完成初始化的数据库。请保留日志和目录，联系维护人员核查；禁止覆盖')
            super_password, password = secrets.token_hex(32), secrets.token_hex(32)
            pwfile = home / '.init-password'
            pwfile.write_text(super_password, encoding='ascii')
            try:
                run(RELEASE / 'postgres/bin/initdb.exe', '-D', home / 'database', '-U', 'postgres',
                    '--encoding=UTF8', '--locale=C', '--auth=scram-sha-256', f'--pwfile={pwfile}',
                    stdout=subprocess.DEVNULL)
            finally:
                pwfile.unlink(missing_ok=True)
            pg_port = int(request.get('pg_port', 15432))
            with (home / 'database/postgresql.conf').open('a', encoding='utf-8') as stream:
                stream.write(f"\nlisten_addresses = '127.0.0.1'\nport = {pg_port}\n")
            # SQL role credentials are never shell arguments or logged command lines.
            pg(home, 'start')
            try:
                import psycopg
                from psycopg import sql
                with psycopg.connect(host='127.0.0.1', port=pg_port, user='postgres', password=super_password,
                                     dbname='postgres', autocommit=True) as conn:
                    conn.execute(sql.SQL('CREATE ROLE labwork LOGIN PASSWORD {}').format(sql.Literal(password)))
                    conn.execute('CREATE DATABASE labwork_prod OWNER labwork')
            finally:
                pg(home, 'stop')
            save(home / 'database-admin.json', {'password': super_password, 'port': pg_port})
            save(config_path, {'APP_ENV': 'prod', 'RUNTIME_DIR': str(home), 'WORKBOOK_ROOT': str(home / 'workbooks'),
                'DATABASE_URL': f'postgresql://labwork:{password}@127.0.0.1:{pg_port}/labwork_prod',
                'SECRET_KEY': secrets.token_urlsafe(48), 'ALLOWED_HOSTS': f'{host},localhost,127.0.0.1',
                'ALLOWED_NETWORKS': f'{networks},127.0.0.0/8,::1/128', 'HTTPS_ENABLED': 'true',
                'CSRF_TRUSTED_ORIGINS': f'https://{host}:{port}', 'WRITEBACK_ENABLED': 'true',
                'WEB_PORT': str(request.get('web_port', 18801)), 'ADMIN_USERNAME': request['username'],
                'ADMIN_PASSWORD': request['password'], 'PUBLIC_URL': f'https://{host}:{port}'})
            (home / 'Caddyfile').write_text('''{
    auto_https disable_redirects
    admin off
    skip_install_trust
}
''' + f'https://{host}:{port} {{\n    tls internal\n    reverse_proxy 127.0.0.1:{request.get("web_port", 18801)} {{\n        header_up X-Real-IP {{remote_host}}\n        header_up X-Forwarded-Proto https\n    }}\n}}\n', encoding='utf-8')
        pg(home, 'start')
        try:
            env = dict(os.environ, LABWORK_CONFIG=str(config_path))
            run(PYTHON, APP / 'runtime.py', 'initialize', env=env, cwd=APP, stdout=sys.stdout, stderr=sys.stderr)
        finally:
            pg(home, 'stop')
        save(home / 'active.json', {'release': str(RELEASE), 'version': '0.0.1', 'schema': 1, 'postgres_major': 17})
        installed_root = RELEASE.parents[1]
        values = {key: escape(str(value)) for key, value in {'python': PYTHON, 'script': Path(__file__),
            'home': home, 'logs': home / 'logs', 'supervisor': APP / 'packaging/windows/supervisor.py'}.items()}
        service = r'''<service>
  <id>Labwork</id><name>理化检测平台</name><description>局域网理化检测、计算与工作簿回写</description>
  <executable>{python}</executable><arguments>"{supervisor}" --home "{home}"</arguments>
  <stopexecutable>{python}</stopexecutable><stoparguments>"{script}" shutdown --home "{home}"</stoparguments>
  <serviceaccount><username>NT AUTHORITY\LocalService</username></serviceaccount>
  <startmode>Automatic</startmode><stoptimeout>60 sec</stoptimeout>
  <onfailure action="restart" delay="15 sec"/><logpath>{logs}</logpath>
  <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>8</keepFiles></log>
</service>'''.format(**values)
        (installed_root / 'LabworkService.xml').write_text(service, encoding='utf-8')
        print('初始化完成；数据与程序版本已隔离。')
    finally:
        request_path.unlink(missing_ok=True)


def backup(home):
    active = read(home / 'active.json')
    release = Path(active['release'])
    admin = read(home / 'database-admin.json')
    if (home / 'supervisor.running').exists():
        raise ValueError('请先停止 Labwork 服务，再创建一致性备份')
    dest = home / 'snapshots' / datetime.now().strftime('%Y%m%d-%H%M%S')
    dest.mkdir(parents=True)
    pg(home, 'start', release)
    try:
        env = dict(os.environ, PGPASSWORD=admin['password'])
        run(release / 'postgres/bin/pg_dump.exe', '-h', '127.0.0.1', '-p', admin['port'], '-U', 'postgres',
            '-Fc', '-f', dest / 'database.dump', 'labwork_prod', env=env)
        for folder in ('workbooks', 'backups'):
            shutil.copytree(home / folder, dest / folder)
        for filename in ('config.json', 'schema.json', 'active.json', 'Caddyfile', 'database-admin.json'):
            shutil.copy2(home / filename, dest / filename)
    finally:
        pg(home, 'stop', release)
    print(f'一致性备份完成：{dest}')


def rollback(home, version):
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('版本格式错误')
    if (home / 'supervisor.running').exists():
        raise ValueError('请先停止 Labwork 服务，并执行 backup')
    target = RELEASE.parent / version
    if not (target / 'app/release.json').exists():
        raise ValueError('目标已发布版本不存在；不能退回方案文档版本')
    metadata = read(target / 'app/release.json')
    if metadata.get('schema') != read(home / 'schema.json')['schema'] or metadata.get('postgres_major') != 17:
        raise ValueError('目标数据库结构或 PostgreSQL 主版本不兼容，禁止自动降级')
    save(home / 'active.json', {'release': str(target), **metadata})
    print('版本切换完成，请启动服务并检查 /health/。业务数据没有倒退。')


def main():
    parser = argparse.ArgumentParser(description='理化检测平台维护')
    parser.add_argument('command', choices=['setup', 'shutdown', 'backup', 'rollback'])
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--request', type=Path)
    parser.add_argument('--version')
    args = parser.parse_args()
    home = args.home.resolve()
    if args.command == 'setup':
        from contextlib import redirect_stdout, redirect_stderr
        (home / 'logs').mkdir(parents=True, exist_ok=True)
        with (home / 'logs/setup.log').open('a', encoding='utf-8') as logfile:
            with redirect_stdout(logfile), redirect_stderr(logfile):
                try:
                    setup(home, args.request)
                except Exception:
                    import traceback
                    traceback.print_exc()
                    raise SystemExit(1)
    elif args.command == 'shutdown':
        (home / 'stop.request').touch()
        import time
        for _ in range(50):
            if not (home / 'supervisor.running').exists():
                return
            time.sleep(1)
        raise RuntimeError('服务未能在 50 秒内停止，请检查日志')
    elif args.command == 'backup':
        backup(home)
    else:
        rollback(home, args.version)


if __name__ == '__main__':
    main()
