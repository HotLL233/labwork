"""Exercise the real Chinese installer with isolated settings and retained data."""
import hashlib,json,socket,subprocess,time,tomllib,urllib.request,uuid,winreg
from pathlib import Path

root=Path(__file__).resolve().parents[1]
version=tomllib.loads((root/'Cargo.toml').read_text(encoding='utf-8'))['package']['version']
installer=Path('C:/labwork/releases')/f'v{version}/installer'/f'理化检测平台-{version}-Windows-x64-安装包.exe'
key=fr'Software\Microsoft\Windows\CurrentVersion\Uninstall\LabworkRustPreview-{version}_is1'
try:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,key):pass
except FileNotFoundError:pass
else:raise RuntimeError('This version is already installed; refuse to replace its registration during test')
test=Path('C:/labwork/tmp')/('rust-installer-'+uuid.uuid4().hex[:8]);test.mkdir(parents=True)
app=test/'installed';data=test/'外部数据'
with socket.socket() as probe:
    probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
def run(exe,*args):
    return subprocess.run([str(exe),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-',*args],timeout=90).returncode
code=run(installer,f'/DIR={app}',f'/PORT={port}',f'/DATADIR={data}',f'/GROUP=Labwork installer test {test.name}',f'/LOG={test / "install.log"}')
assert code==0,code
assert hashlib.sha256((app/'labwork-preview.exe').read_bytes()).digest()==hashlib.sha256((root/'target/release/labwork-preview.exe').read_bytes()).digest()
config=json.loads((app/'labwork.json').read_text(encoding='utf-8-sig'))
assert config=={'port':port,'data_dir':str(data)},config
assert (data/'.labwork-rust-preview').is_file() and (data/'workbooks').is_dir()
assert not list(app.glob('*.cmd'))
assert (app/'labwork.ico').is_file()
assert run(installer,f'/DIR={app}',f'/LOG={test / "reinstall.log"}')==0
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
finally:
    process.terminate();process.wait(timeout=10)
sentinel=data/'retain-test.txt';sentinel.write_text('preserve data',encoding='utf-8')
assert run(app/'unins000.exe',f'/LOG={test / "uninstall.log"}')==0
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
result={'install':True,'installed_binary_hash':True,'custom_port':True,'external_data_config':True,'reinstall_preserves_settings':True,'installed_server_http':True,'no_cmd_entry':True,'uninstall':True,'external_data_preserved':True,'unmarked_data_rejected':True,'invalid_ports_rejected':True,'logs':str(test),'installer_bytes':installer.stat().st_size}
(installer.parent/'安装验收.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
