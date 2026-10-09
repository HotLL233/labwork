"""Exercise the actual installer in a new test directory, retaining logs and data."""
import hashlib,json,subprocess,uuid,winreg
from pathlib import Path

root=Path(__file__).resolve().parents[1]
version='0.0.2-alpha.1-grid.3'
installer=Path('C:/labwork/releases')/f'v{version}/installer'/f'Labwork-Rust-{version}-Setup.exe'
key=fr'Software\Microsoft\Windows\CurrentVersion\Uninstall\LabworkRustPreview-{version}_is1'
try:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,key):pass
except FileNotFoundError:pass
else:raise RuntimeError('This version is already installed; refuse to replace its registration during test')
test=Path('C:/labwork/tmp')/('rust-installer-'+uuid.uuid4().hex[:8])
test.mkdir(parents=True)
app=test/'installed'
def run(exe,*args):
    return subprocess.run([str(exe),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-',*args],timeout=90).returncode
code=run(installer,f'/DIR={app}',f'/GROUP=Labwork installer test {test.name}',f'/LOG={test / "install.log"}')
assert code==0,code
assert hashlib.sha256((app/'labwork-preview.exe').read_bytes()).digest()==hashlib.sha256((root/'target/release/labwork-preview.exe').read_bytes()).digest()
assert (app/'data/.labwork-rust-preview').is_file()
assert (app/'data/workbooks').is_dir()
assert (app/'启动平台.cmd').is_file()
sentinel=app/'data/retain-test.txt';sentinel.write_text('preserve data',encoding='utf-8')
assert run(app/'unins000.exe',f'/LOG={test / "uninstall.log"}')==0
assert sentinel.read_text(encoding='utf-8')=='preserve data'
assert (app/'data/.labwork-rust-preview').exists()
assert not (app/'labwork-preview.exe').exists()
bad=test/'unmarked';(bad/'data').mkdir(parents=True)
(bad/'data/sentinel.txt').write_text('unchanged')
rejected=run(installer,f'/DIR={bad}',f'/LOG={test / "rejected.log"}')
assert rejected!=0
assert (bad/'data/sentinel.txt').read_text()=='unchanged'
assert not (bad/'labwork-preview.exe').exists()
result={'install':True,'installed_binary_hash':True,'uninstall':True,'data_preserved':True,'unmarked_data_rejected':True,'logs':str(test),'installer_bytes':installer.stat().st_size}
(installer.parent/'安装验收.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
