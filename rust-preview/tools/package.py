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
monthly=json.loads((repo/'runtime/month-smoke-result.json').read_text(encoding='utf-8'))
if not monthly or not all(monthly.values()):raise RuntimeError('Month regression did not pass')
auth=json.loads((repo/'runtime/auth-smoke-result.json').read_text(encoding='utf-8'))
if not auth or not all(auth.values()):raise RuntimeError('Login persistence regression did not pass')
formula=json.loads((repo/'runtime/formula-smoke-result.json').read_text(encoding='utf-8'))
if not formula.get('cases') or not all(formula.values()):raise RuntimeError('Workbook formula regression did not pass')
ui_directory=repo/'runtime/ui-qa/grid7'
ui=json.loads((ui_directory/'result.json').read_text(encoding='utf-8'))
if ui.get('ok') is not True:raise RuntimeError('Browser regression did not pass')
generic_source=json.loads((repo/'runtime/generic-workbook-regression.json').read_text(encoding='utf-8'))
if (generic_source.get('version')!=version or generic_source.get('calculation_and_write_roundtrips')!=123
    or generic_source.get('total_sheets')!=137 or generic_source.get('source_files_unchanged') is not True
    or generic_source.get('candidate_results_readback_verified') is not True
    or generic_source.get('month_formula_inputs_cleared_fixed_headers_preserved') is not True
    or not generic_source.get('workbooks')
    or any(book.get('original_unchanged') is not True for book in generic_source['workbooks'])):
    raise RuntimeError('Full workbook regression did not pass')
generic={key:generic_source[key] for key in ('version','calculation_and_write_roundtrips','total_sheets','source_files_unchanged','candidate_results_readback_verified','month_formula_inputs_cleared_fixed_headers_preserved')}
generic['workbook_count']=len(generic_source['workbooks'])
commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
release.mkdir(parents=True)
portable=release/f'labwork-{version}-Rust-Windows-x64.zip'
with zipfile.ZipFile(portable,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    z.write(exe,'labwork-preview.exe')
    z.write(root/'README.md','README.md')
    z.write(root/'packaging/安装说明.txt','安装说明.txt')
    z.writestr('labwork.json',json.dumps({'port':18810,'data_dir':'data','lan_ip':'','access_password':''},ensure_ascii=False))
    z.writestr('data/.labwork-rust-preview','local isolated preview only')
    z.writestr('data/workbooks/','')
with zipfile.ZipFile(portable) as z:
    if any(n.endswith(('.xlsx','.sqlite3')) for n in z.namelist()):raise RuntimeError('Business data found in portable package')
subprocess.run(['git','archive','--format=zip','-o',str(release/f'labwork-{version}-Source.zip'),commit],cwd=repo,check=True)
for source in [root/'README.md',repo/'docs/表格修订版-grid.7.md',repo/'runtime/rust-smoke-result.json',repo/'runtime/month-smoke-result.json',repo/'runtime/auth-smoke-result.json',repo/'runtime/formula-smoke-result.json']:
    shutil.copyfile(source,release/source.name)
shutil.copytree(ui_directory,release/'browser-qa')
(release/'generic-workbook-regression.json').write_text(json.dumps(generic,indent=2),encoding='utf-8')
manifest={'version':version,'git_commit':commit,'branch':'fix/rust-grid','exe_bytes':exe.stat().st_size,'portable_zip_bytes':portable.stat().st_size,'listen':'127.0.0.1:18810','scope':'LAN validation with shared access password; not a complete production platform','regression':checks,'month_regression':monthly,'auth_regression':auth,'formula_regression':formula,'generic_workbook_regression':generic,'browser_regression':ui,'browser_evidence':'browser-qa/result.json','native_tray_visual_checked':False,'unit_tests_note':'cargo test --locked passed tray::tests::tray_registers_stops_and_cleans_up (automatic tray registration and cleanup, not visual inspection)','office_recalculation_checked':False}
(release/'发布信息.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
(release/'SHA256SUMS.txt').write_text('\n'.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(release).as_posix()}' for p in sorted(release.rglob('*')) if p.is_file())+'\n',encoding='utf-8')
print(json.dumps({'release':str(release),'exe_bytes':exe.stat().st_size,'zip_bytes':portable.stat().st_size},ensure_ascii=False))
