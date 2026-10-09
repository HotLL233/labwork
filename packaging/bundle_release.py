"""Package prebuilt artifacts with explicit allowlists; business data is never collected."""
import argparse
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--images', type=Path, required=True)
    args = parser.parse_args()
    dest = args.out.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    exe = ROOT / 'build/installer/Labwork-0.0.1-Windows-x64-Setup.exe'
    shutil.copy2(exe, dest / exe.name)
    for name in ('v0.0.1-安装与维护.md', 'v0.0.1-验证记录.md'):
        shutil.copy2(ROOT / 'docs' / name, dest / name)
    tags = ['labwork:0.0.1', 'labwork-postgres:17.11', 'labwork-gateway:0.0.1', 'python:3.12-slim']
    components = {'version': '0.0.1', 'architecture': 'linux/amd64',
        'images': [{'tag': tag, **json.loads(subprocess.check_output(['docker', 'image', 'inspect', tag,
            '--format', '{{json .}}'], text=True))} for tag in tags]}
    # Inspect data is narrowed before shipping; no daemon/machine configuration is exported.
    components['images'] = [{key: item[key] for key in ('tag', 'Id', 'RepoDigests', 'Architecture', 'Os')} for item in components['images']]
    (dest / 'Docker组件.json').write_text(json.dumps(components, indent=2), encoding='utf-8')
    docker_zip = dest / 'Labwork-0.0.1-Docker-x64.zip'
    with zipfile.ZipFile(docker_zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=4) as archive:
        archive.write(args.images, 'images.tar')
        for name in ('compose.yaml', 'backup.ps1', 'Caddyfile', 'init-db.sh', 'Dockerfile.postgres', 'Dockerfile.gateway'):
            archive.write(ROOT / 'packaging/docker' / name, name)
        for name in ('v0.0.1-安装与维护.md', 'v0.0.1-验证记录.md', 'Docker组件.json'):
            archive.write(dest / name, name)
        for file in (ROOT / 'build/windows/releases/0.0.1/licenses').iterdir():
            archive.write(file, 'licenses/' + file.name)
        archive.write(ROOT / 'build/windows/releases/0.0.1/postgres/commandlinetools_3rd_party_licenses.txt', 'licenses/PostgreSQL-third-party.txt')
        archive.write(ROOT / 'requirements.lock', 'requirements.lock')
    with zipfile.ZipFile(docker_zip) as archive:
        assert archive.testzip() is None
        assert not any(name.endswith(('.env', '.xlsx', '.sqlite3')) or 'config.json' in name for name in archive.namelist())
    lines = []
    for path in sorted(dest.iterdir()):
        if path.is_file() and path.name != 'SHA256SUMS.txt':
            with path.open('rb') as stream:
                lines.append(hashlib.file_digest(stream, 'sha256').hexdigest() + '  ' + path.name)
    (dest / 'SHA256SUMS.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'发布包完成：{dest}')


if __name__ == '__main__':
    main()
