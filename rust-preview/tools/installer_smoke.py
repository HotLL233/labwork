"""Exercise the real Chinese installer with isolated settings and retained data."""
import argparse,hashlib,json,socket,subprocess,time,tomllib,urllib.request,uuid,winreg
from pathlib import Path

root=Path(__file__).resolve().parents[1]
version=tomllib.loads((root/'Cargo.toml').read_text(encoding='utf-8'))['package']['version']
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--installer',type=Path)
parser.add_argument('--app-id',default='LabworkRustPlatform')
parser.add_argument('--report-dir',type=Path)
args=parser.parse_args()
installer=args.installer or Path('C:/labwork/releases')/f'v{version}/installer'/f'labwork-{version}-Windows-x64-Setup.exe'
if args.app_id!='LabworkRustPlatform':
    assert args.app_id.startswith('LabworkRustPlatform-QA-') and args.app_id in installer.stem,'QA installer filename must identify its isolated AppId'
key=fr'Software\Microsoft\Windows\CurrentVersion\Uninstall\{args.app_id}_is1'
def product_registration():
    values={}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Uninstall\LabworkRustPlatform_is1') as handle:
            for index in range(winreg.QueryInfoKey(handle)[1]):
                name,value,kind=winreg.EnumValue(handle,index);values[name]=(value,kind)
    except FileNotFoundError:pass
    return values
original_registration=product_registration()
try:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,key):pass
except FileNotFoundError:pass
else:raise RuntimeError('This version is already installed; refuse to replace its registration during test')
test=Path('C:/labwork/tmp')/('rust-installer-'+uuid.uuid4().hex[:8]);test.mkdir(parents=True)
install_root=test/'installed';app=install_root/'versions'/version;data=test/'外部数据'
with socket.socket() as probe:
    probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
def run(exe,*args):
    if Path(exe)==installer and not any(arg.upper().startswith('/GROUP=') for arg in args):
        args=(*args,f'/GROUP=Labwork installer test {test.name}')
    return subprocess.run([str(exe),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-',*args],timeout=90).returncode
code=run(installer,f'/DIR={install_root}',f'/PORT={port}',f'/DATADIR={data}',f'/GROUP=Labwork installer test {test.name}',f'/LOG={test / "install.log"}')
assert code==0,code
assert hashlib.sha256((app/'labwork-preview.exe').read_bytes()).digest()==hashlib.sha256((root/'target/release/labwork-preview.exe').read_bytes()).digest()
config=json.loads((app/'labwork.json').read_text(encoding='utf-8-sig'))
assert config=={'port':port,'data_dir':str(data),'lan_ip':'','access_password':''},config
assert (data/'.labwork-rust-preview').is_file() and (data/'workbooks').is_dir()
assert not list(app.glob('*.cmd'))
assert (app/'labwork.ico').is_file()
older=install_root/'versions'/'0.0.2-alpha.1-grid.6';older.mkdir()
(older/'retain.exe').write_bytes(b'previous version sentinel')
assert run(installer,f'/DIR={install_root}',f'/LOG={test / "reinstall.log"}')==0
assert (older/'retain.exe').read_bytes()==b'previous version sentinel'
assert json.loads((app/'labwork.json').read_text(encoding='utf-8-sig'))==config
process=subprocess.Popen([str(app/'labwork-preview.exe'),'--headless'],cwd=test,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
    for attempt in range(80):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/catalog',timeout=1) as response:
                catalog=json.load(response)
            break
        except (OSError,ValueError):
            assert process.poll() is None,'Installed server exited before responding'
            time.sleep(.1)
    else:raise AssertionError('Installed server did not use configured port')
    assert catalog['version']==version,catalog
    assert (data/'preview.sqlite3').is_file()
    assert run(installer,f'/DIR={install_root}',f'/DATADIR={data}',f'/LOG={test / "running-rejected.log"}')!=0
finally:
    process.terminate();process.wait(timeout=10)
sentinel=data/'retain-test.txt';sentinel.write_text('preserve data',encoding='utf-8')
assert run(install_root/'unins000.exe',f'/LOG={test / "uninstall.log"}')==0
assert sentinel.read_text(encoding='utf-8')=='preserve data'
assert (data/'.labwork-rust-preview').exists() and (data/'preview.sqlite3').exists()
assert not (app/'labwork-preview.exe').exists()
bad=test/'unmarked';bad.mkdir();(bad/'sentinel.txt').write_text('unchanged')
rejected=run(installer,f'/DIR={test / "bad-app"}',f'/DATADIR={bad}',f'/LOG={test / "rejected.log"}')
assert rejected!=0 and (bad/'sentinel.txt').read_text()=='unchanged'
assert not (test/'bad-app/labwork-preview.exe').exists()
for invalid in ('1023','65536','abc'):
    assert run(installer,f'/DIR={test / "bad-port"}',f'/PORT={invalid}',f'/LOG={test / ("port-"+invalid+".log")}')!=0
assert not (test/'bad-port/labwork-preview.exe').exists()
for index,invalid in enumerate(('0.0.0.0','127.0.0.1','8.8.8.8','172.32.0.1','192.168.1.999','10.01.2.3')):
    assert run(installer,f'/DIR={test / "bad-lan"}',f'/LANIP={invalid}',f'/LOG={test / ("lan-"+str(index)+".log")}')!=0
secret_file=test/'access-password.txt';secret=uuid.uuid4().hex
secret_file.write_text('x'*257,encoding='utf-8')
assert run(installer,f'/DIR={test / "long-password"}','/LANIP=192.168.1.50',f'/ACCESSPASSFILE={secret_file}',f'/LOG={test / "long-password.log"}')!=0
secret_file.write_text(secret,encoding='utf-8')
assert run(installer,f'/DIR={test / "short-password"}','/LANIP=192.168.1.50',f'/LOG={test / "short-password.log"}')!=0
lan_root=test/'lan-installed';lan_app=lan_root/'versions'/version;lan_data=test/'内网数据'
assert run(installer,f'/DIR={lan_root}',f'/PORT={port}',f'/DATADIR={lan_data}','/LANIP=192.168.1.50',f'/ACCESSPASSFILE={secret_file}',f'/LOG={test / "lan-install.log"}')==0
lan_config=json.loads((lan_app/'labwork.json').read_text(encoding='utf-8-sig'))
assert lan_config=={'port':port,'data_dir':str(lan_data),'lan_ip':'192.168.1.50','access_password':secret}
assert secret not in (test/'lan-install.log').read_text(encoding='utf-8-sig')
assert run(lan_root/'unins000.exe',f'/LOG={test / "lan-uninstall.log"}')==0
assert (lan_data/'.labwork-rust-preview').exists()
secret_file.unlink()
result={'install':True,'installed_binary_hash':True,'custom_port':True,'external_data_config':True,'reinstall_preserves_settings':True,'older_version_files_preserved':True,'installed_server_http':True,'no_cmd_entry':True,'uninstall':True,'external_data_preserved':True,'running_data_rejected':True,'unmarked_data_rejected':True,'invalid_ports_rejected':True,'invalid_lan_addresses_rejected':True,'lan_password_required':True,'long_password_rejected':True,'lan_config_saved':True,'password_not_in_install_log':True,'logs':str(test),'installer_bytes':installer.stat().st_size}
if args.app_id!='LabworkRustPlatform':
    assert product_registration()==original_registration,'Real product registration changed during isolated QA'
result.update({'app_id':args.app_id,'qa_same_source_isolated_install':args.app_id!='LabworkRustPlatform','real_registration_untouched':args.app_id!='LabworkRustPlatform','installer_path':str(installer)})
report_dir=args.report_dir or installer.parent
report_dir.mkdir(parents=True,exist_ok=True)
(report_dir/'安装验收.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
