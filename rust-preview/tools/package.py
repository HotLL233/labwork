"""Package the tested local Rust validation build, without business files."""
import hashlib
import json
import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

root=Path(__file__).resolve().parents[1]
project=tomllib.loads((root/'Cargo.toml').read_text(encoding='utf-8'))['package']
if project['name']!='labwork':raise RuntimeError('Package project name must be labwork')
version=project['version']
repo=root.parent
release=Path('C:/labwork/releases')/f'labwork-v{version}'
if release.exists():raise RuntimeError('Release already exists; do not overwrite published files')
exe=root/'target/release/labwork.exe'
checks=json.loads((repo/'runtime/rust-smoke-result.json').read_text(encoding='utf-8'))
if checks.get('methods')!=14 or not all(checks.values()):raise RuntimeError('File regression did not pass')
monthly=json.loads((repo/'runtime/month-smoke-result.json').read_text(encoding='utf-8'))
if not monthly or not all(monthly.values()):raise RuntimeError('Month regression did not pass')
auth=json.loads((repo/'runtime/auth-smoke-result.json').read_text(encoding='utf-8'))
if not auth or not all(auth.values()):raise RuntimeError('Login persistence regression did not pass')
formula=json.loads((repo/'runtime/formula-smoke-result.json').read_text(encoding='utf-8'))
if not formula.get('cases') or not all(formula.values()) or formula.get('calculated_results_saved_to_associated_original') is not True:raise RuntimeError('Workbook formula regression did not pass')
imports=json.loads((repo/'runtime/import-smoke-result.json').read_text(encoding='utf-8'))
if not imports or not all(imports.values()):raise RuntimeError('Workbook upload validation regression did not pass')
lan=json.loads((repo/'runtime/lan-smoke-result.json').read_text(encoding='utf-8'))
if lan.get('ok') is not True or lan.get('version')!=version:raise RuntimeError('LAN access regression did not pass for this version')
startup=json.loads((repo/'runtime/startup-smoke-result.json').read_text(encoding='utf-8'))
if not startup or not all(startup.values()):raise RuntimeError('Startup regression did not pass')
format_checks=json.loads((repo/'runtime/format-smoke-result.json').read_text(encoding='utf-8'))
if not format_checks or not all(format_checks.values()) or format_checks.get('explicit_date_format_converts_compact_date_for_excel_1900_and_1904') is not True:raise RuntimeError('Workbook format regression did not pass')
source_checks=json.loads((repo/'runtime/source-smoke-result.json').read_text(encoding='utf-8'))
if (not source_checks or not all(source_checks.values())
    or source_checks.get('commit_syncs_source_and_monthly_copy') is not True
    or source_checks.get('unlinked_commit_rejected') is not True
    or source_checks.get('server_import_and_binding_one_step') is not True
    or source_checks.get('server_import_failure_leaves_no_project_or_link') is not True
    or source_checks.get('source_only_previous_data_preserved') is not True
    or source_checks.get('grid9_preview_cannot_replay_format_damage') is not True
    or source_checks.get('grid9_incomplete_write_only_finalize_when_both_files_already_saved') is not True
    or source_checks.get('cross_year_source_inheritance') is not True
    or source_checks.get('existing_next_month_source_never_overwritten') is not True):
    raise RuntimeError('Source synchronization regression did not pass')
preserved=json.loads((repo/'runtime/preserve-format-smoke-result.json').read_text(encoding='utf-8'))
if preserved.get('ok') is not True or preserved.get('version')!=version:raise RuntimeError('Original workbook layout regression did not pass for this version')
original_layout=json.loads((repo/'runtime/original-format-preservation.json').read_text(encoding='utf-8'))
if (original_layout.get('version')!=version or original_layout.get('executable_sha256')!=hashlib.sha256(exe.read_bytes()).hexdigest()
    or original_layout.get('workbooks')!=12 or original_layout.get('method_previews')!=138 or original_layout.get('isolated_source_commits')!=12
    or original_layout.get('business_originals_hash_unchanged') is not True or not original_layout.get('checks')
    or not all(original_layout['checks'].values()) or original_layout.get('failures')!=[] or original_layout.get('api_errors')!=[]):
    raise RuntimeError('All-original layout preservation regression did not pass for this executable')
ui_directory=repo/'runtime/ui-qa/grid10'
if any(path.suffix.lower() in ('.xlsx','.sqlite3') for path in ui_directory.rglob('*') if path.is_file()):raise RuntimeError('Browser evidence contains business files or a database')
ui=json.loads((ui_directory/'result.json').read_text(encoding='utf-8'))
if ui.get('ok') is not True or ui.get('version')!=version or ui.get('manual_source_write') is not True:raise RuntimeError('Browser regression did not pass for this version')
generic_source=json.loads((repo/'runtime/generic-workbook-regression.json').read_text(encoding='utf-8'))
if (generic_source.get('version')!=version or generic_source.get('calculation_and_write_roundtrips')!=124
    or generic_source.get('total_sheets')!=138 or generic_source.get('source_files_unchanged') is not True
    or generic_source.get('candidate_results_readback_verified') is not True
    or generic_source.get('month_formula_inputs_cleared_fixed_headers_preserved') is not True
    or not generic_source.get('workbooks')
    or any(book.get('original_unchanged') is not True for book in generic_source['workbooks'])):
    raise RuntimeError('Full workbook regression did not pass')
generic={key:generic_source[key] for key in ('version','calculation_and_write_roundtrips','total_sheets','source_files_unchanged','candidate_results_readback_verified','month_formula_inputs_cleared_fixed_headers_preserved')}
generic['workbook_count']=len(generic_source['workbooks'])
commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
branch=subprocess.check_output(['git','branch','--show-current'],cwd=repo,text=True).strip()
if branch!='fix/grid10-preserve-workbook-format':raise RuntimeError('Package from the isolated grid10 branch only')
release.mkdir(parents=True)
portable=release/f'labwork-v{version}-Windows-x64.zip'
with zipfile.ZipFile(portable,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    z.write(exe,'labwork.exe')
    z.write(root/'README.md','README.md')
    z.write(root/'packaging/安装说明.txt','安装说明.txt')
    z.writestr('labwork.json',json.dumps({'port':18810,'data_dir':'data','lan_ip':'','access_password':''},ensure_ascii=False))
    z.writestr('data/.labwork-rust-preview','local isolated preview only')
    z.writestr('data/workbooks/','')
with zipfile.ZipFile(portable) as z:
    if any(n.endswith(('.xlsx','.sqlite3')) for n in z.namelist()):raise RuntimeError('Business data found in portable package')
subprocess.run(['git','archive','--format=zip','-o',str(release/f'labwork-v{version}-Source.zip'),commit],cwd=repo,check=True)
for source in [root/'README.md',repo/'docs/表格修订版-grid.10.md',repo/'docs/命名规范.md',repo/'runtime/preserve-format-smoke-result.json',repo/'runtime/original-format-preservation.json',repo/'runtime/rust-smoke-result.json',repo/'runtime/month-smoke-result.json',repo/'runtime/auth-smoke-result.json',repo/'runtime/formula-smoke-result.json',repo/'runtime/format-smoke-result.json',repo/'runtime/source-smoke-result.json',repo/'runtime/import-smoke-result.json',repo/'runtime/lan-smoke-result.json',repo/'runtime/startup-smoke-result.json']:
    shutil.copyfile(source,release/source.name)
shutil.copytree(ui_directory,release/'browser-qa')
(release/'generic-workbook-regression.json').write_text(json.dumps(generic,indent=2),encoding='utf-8')
manifest={'project':'labwork','release_name':f'labwork-v{version}','product_name':f'labwork-v{version}','binary_name':'labwork.exe','version':version,'git_commit':commit,'branch':branch,'exe_bytes':exe.stat().st_size,'portable_zip_bytes':portable.stat().st_size,'listen':'127.0.0.1:18810','scope':'LAN validation with associated server-local workbook as source of truth','regression':checks,'month_regression':monthly,'auth_regression':auth,'formula_regression':formula,'format_regression':format_checks,'source_regression':source_checks,'import_regression':imports,'lan_regression':lan,'startup_regression':startup,'generic_workbook_regression':generic,'preserve_original_format_regression':preserved,'all_original_format_preservation':original_layout,'browser_regression':ui,'browser_evidence':'browser-qa/result.json','native_tray_visual_checked':False,'unit_tests_note':'cargo test --locked passed tray::tests::tray_registers_stops_and_cleans_up (automatic tray registration and cleanup, not visual inspection)','office_recalculation_checked':False}
(release/'发布信息.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
(release/'SHA256SUMS.txt').write_text('\n'.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(release).as_posix()}' for p in sorted(release.rglob('*')) if p.is_file())+'\n',encoding='utf-8')
print(json.dumps({'release':str(release),'exe_bytes':exe.stat().st_size,'zip_bytes':portable.stat().st_size},ensure_ascii=False))
