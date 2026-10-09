"""Package the tested local Rust validation build, without business files."""
import hashlib
import json
import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

root=Path(__file__).resolve().parents[1]
version=tomllib.loads((root/'Cargo.toml').read_text(encoding='utf-8'))['package']['version']
repo=root.parent
release=Path('C:/labwork/releases')/f'v{version}'
if release.exists():raise RuntimeError('Release already exists; do not overwrite published files')
exe=root/'target/release/labwork-preview.exe'
checks=json.loads((repo/'runtime/rust-smoke-result.json').read_text(encoding='utf-8'))
if checks.get('methods')!=14 or not all(checks.values()):raise RuntimeError('File regression did not pass')
commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
release.mkdir(parents=True)
portable=release/f'Labwork-{version}-Rust-Windows-x64.zip'
with zipfile.ZipFile(portable,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    z.write(exe,'labwork-preview.exe')
    z.write(root/'README.md','README.md')
    z.writestr('data/.labwork-rust-preview','local isolated preview only')
    z.writestr('data/workbooks/','')
    z.writestr('启动预览.cmd','@echo off\r\nchcp 65001 >nul\r\ncd /d "%~dp0"\r\necho 浏览器访问 http://127.0.0.1:18804\r\necho 此版本仅用于本机副本验证，按 Ctrl+C 停止。\r\n"%~dp0labwork-preview.exe" --home "%~dp0data"\r\npause\r\n')
with zipfile.ZipFile(portable) as z:
    if any(n.endswith(('.xlsx','.sqlite3')) for n in z.namelist()):raise RuntimeError('Business data found in portable package')
subprocess.run(['git','archive','--format=zip','-o',str(release/f'Labwork-{version}-Source.zip'),commit],cwd=repo,check=True)
for source in [root/'README.md',repo/'docs/表格修订版-grid.1.md',repo/'runtime/rust-smoke-result.json']:
    shutil.copyfile(source,release/source.name)
manifest={'version':version,'git_commit':commit,'branch':'fix/rust-grid','exe_bytes':exe.stat().st_size,'portable_zip_bytes':portable.stat().st_size,'listen':'127.0.0.1:18804','scope':'local validation, not a production installer','regression':checks,'browser_and_office_check':False}
(release/'发布信息.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
(release/'SHA256SUMS.txt').write_text('\n'.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}' for p in sorted(release.iterdir()) if p.is_file())+'\n',encoding='utf-8')
print(json.dumps({'release':str(release),'exe_bytes':exe.stat().st_size,'zip_bytes':portable.stat().st_size},ensure_ascii=False))
