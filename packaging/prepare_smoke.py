"""Generate isolated packaging test configs; never shipped inside release archives."""
import argparse
import json
import secrets
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=['windows', 'docker'])
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    creds = json.loads((root / 'credentials.json').read_text()) if (root / 'credentials.json').exists() else {'username': 'package_test', 'password': secrets.token_urlsafe(24)}
    (root / 'credentials.json').write_text(json.dumps(creds), encoding='utf-8')
    if args.kind == 'windows':
        request = {'host': 'localhost', 'port': '18443', 'networks': '127.0.0.0/8',
                   'pg_port': '15439', 'web_port': '18809', **creds}
        (root / 'request.json').write_text(json.dumps(request), encoding='utf-8')
    else:
        for path in (Path(__file__).parent / 'docker').iterdir():
            if path.is_file():
                shutil.copy2(path, root / path.name)
        (root / 'config').mkdir(exist_ok=True)
        password = secrets.token_hex(32)
        config = {'APP_ENV': 'prod', 'SECRET_KEY': secrets.token_urlsafe(48), 'RUNTIME_DIR': '/runtime',
            'WORKBOOK_ROOT': '/runtime/workbooks', 'DATABASE_URL': f'postgresql://labwork:{password}@db:5432/labwork_prod',
            'ALLOWED_HOSTS': 'localhost,127.0.0.1', 'ALLOWED_NETWORKS': '127.0.0.0/8,172.16.0.0/12,192.168.0.0/16',
            'TRUSTED_PROXIES': '172.30.87.10/32', 'WEB_BIND': '0.0.0.0', 'WEB_PORT': '18801',
            'HTTPS_ENABLED': 'true', 'CSRF_TRUSTED_ORIGINS': 'https://localhost:18445',
            'WRITEBACK_ENABLED': 'true', 'ADMIN_USERNAME': creds['username'], 'ADMIN_PASSWORD': creds['password']}
        (root / 'config/config.json').write_text(json.dumps(config), encoding='utf-8')
        (root / '.env').write_text(f'LABWORK_HOST=localhost\nHTTPS_PORT=18445\nPOSTGRES_PASSWORD={secrets.token_hex(32)}\nLABWORK_DB_PASSWORD={password}\nDOCKER_SUBNET=172.30.87.0/24\nPROXY_IP=172.30.87.10\nRUNTIME_VOLUME=labwork-smoke_runtime\n', encoding='utf-8')


if __name__ == '__main__':
    main()
