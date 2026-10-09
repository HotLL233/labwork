import argparse
import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def copytree(source, dest):
    shutil.copytree(source, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'tests.py'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--downloads', type=Path, required=True)
    args = parser.parse_args()
    payload = ROOT / 'build/windows'
    release = payload / 'releases/0.0.1'
    app = release / 'app'
    app.mkdir(parents=True, exist_ok=True)
    for name in ('labwork', 'records', 'templates', 'staticfiles'):
        copytree(ROOT / name, app / name)
    copytree(ROOT / 'packaging/windows', app / 'packaging/windows')
    for name in ('manage.py', 'runtime.py', 'release.json', 'requirements.lock'):
        shutil.copy2(ROOT / name, app / name)
    copytree(args.python, release / 'python')
    copytree(ROOT / '.venv/Lib/site-packages', release / 'python/Lib/site-packages')
    with zipfile.ZipFile(args.downloads / 'postgresql-win.zip') as archive:
        for info in archive.infolist():
            if info.filename.startswith(('pgsql/bin/', 'pgsql/lib/', 'pgsql/share/')) and not info.is_dir():
                target = release / 'postgres' / info.filename.removeprefix('pgsql/')
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open('wb') as dest:
                    shutil.copyfileobj(source, dest)
        for name in ('server_license.txt', 'commandlinetools_3rd_party_licenses.txt'):
            target = release / 'postgres' / name
            target.write_bytes(archive.read('pgsql/' + name))
    shutil.copy2(args.downloads / 'caddy.exe', release / 'caddy.exe')
    shutil.copy2(args.downloads / 'winsw.exe', payload / 'LabworkService.exe')
    licenses = release / 'licenses'
    licenses.mkdir(exist_ok=True)
    for name, url in {'Caddy-Apache-2.0.txt': 'https://raw.githubusercontent.com/caddyserver/caddy/v2.11.7/LICENSE',
                      'WinSW-MIT.txt': 'https://raw.githubusercontent.com/winsw/winsw/v2.12.0/LICENSE.txt',
                      'PostgreSQL.txt': 'https://raw.githubusercontent.com/postgres/postgres/REL_17_STABLE/COPYRIGHT'}.items():
        with urllib.request.urlopen(url, timeout=40) as response:
            (licenses / name).write_bytes(response.read())
    shutil.copy2(args.python / 'LICENSE.txt', licenses / 'Python.txt')
    shutil.copy2(ROOT / 'docs/v0.0.1-安装与维护.md', payload / '安装与维护.md')
    manifest = {'version': '0.0.1', 'python': '3.12.13', 'postgresql': '17.11', 'caddy': '2.11.7', 'winsw': '2.12.0',
                'sources': {'PostgreSQL': 'https://get.enterprisedb.com/postgresql/postgresql-17.11-1-windows-x64-binaries.zip',
                    'WinSW': 'https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe',
                    'Caddy': 'https://caddyserver.com/api/download?os=windows&arch=amd64'},
                'download_sha256': {path.name: hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()
                                    for path in args.downloads.iterdir() if path.is_file()}}
    (release / 'components.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    env = dict(os.environ, APP_ENV='test', RUNTIME_DIR=str(ROOT / 'runtime/bundle-check'), LABWORK_CONFIG='')
    subprocess.run([str(release / 'python/python.exe'), str(app / 'runtime.py'), 'check'], cwd=app, env=env, check=True)
    print(f'Windows 离线服务端负载：{payload}')


if __name__ == '__main__':
    main()
