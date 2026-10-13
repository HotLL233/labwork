"""Check installer password inheritance using only isolated JSON files."""
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import uuid

root = Path(__file__).resolve().parents[1]
helper = root / 'packaging/installer-settings.ps1'
secrets = ['旧口令-test-"\\-tab\t-newline\n-end', '替换口令-test-"\\-tab\t-newline\n-end']
checks = {}
with tempfile.TemporaryDirectory(prefix='labwork-settings-') as folder:
    test = Path(folder)
    app = test / '中文安装目录'
    app.mkdir()
    target = app / 'labwork.json'
    data = test / '同一数据目录'
    old = {'port': 18810, 'data_dir': str(data), 'lan_ip': '192.168.1.50', 'access_password': secrets[0]}
    target.write_text(json.dumps(old, ensure_ascii=False), encoding='utf-8-sig')

    def invoke(mode='Check', password='', directory=data, previous=target, registered=app, lan_ip=old['lan_ip']):
        request = test / ('request-' + uuid.uuid4().hex + '.json')
        request.write_text(json.dumps({**old, 'data_dir': str(directory), 'lan_ip': lan_ip, 'access_password': password}, ensure_ascii=False), encoding='utf-8')
        command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(helper),
                   '-Mode', mode, '-RequestFile', str(request), '-OldConfig', str(previous), '-RegisteredRoot', str(registered), '-TargetFile', str(target) if mode == 'Write' else '']
        assert all(secret not in ' '.join(command) for secret in secrets), 'Password in helper command'
        try:
            result = subprocess.run(command, capture_output=True, timeout=20)
            assert not result.stdout and not result.stderr, 'Helper wrote output instead of a numeric status'
            return result.returncode
        finally:
            request.unlink()

    assert invoke() == 0
    assert invoke(previous='', registered='') == 10
    assert invoke(password=secrets[1], previous='', registered='') == 0
    assert invoke(previous='', registered='', lan_ip='') == 0
    assert invoke('Write') == 0
    assert json.loads(target.read_text(encoding='utf-8-sig')) == old, 'Inherited config differs'
    checks['inherited_password_exact'] = True
    assert invoke(directory=test / '新数据目录') == 10
    checks['different_data_requires_new_password'] = True
    for damaged in ('{broken-json-containing-test-password', json.dumps({**old, 'port': 'bad'}), json.dumps({**old, 'access_password': 'short'})):
        target.write_text(damaged, encoding='utf-8')
        assert invoke() == 10
        assert target.read_text(encoding='utf-8') == damaged
    checks['malformed_old_config_rejected'] = True
    target.write_text(json.dumps(old, ensure_ascii=False), encoding='utf-8')
    assert invoke(previous=test / 'missing.json') == 10
    assert invoke(registered=test / 'elsewhere') == 10
    checks['missing_or_outside_registered_config_rejected'] = True
    assert invoke(password='short') == 10
    assert invoke(password='x' * 257) == 10
    assert invoke('Write', password=secrets[1]) == 0
    expected = {**old, 'access_password': secrets[1]}
    assert json.loads(target.read_text(encoding='utf-8')) == expected
    checks['new_password_and_special_characters_saved_exactly'] = True
    assert invoke('Write') == 0
    assert json.loads(target.read_text(encoding='utf-8')) == expected
    # Windows denies replacement while an isolated config has an exclusive handle.
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    before = target.read_bytes()
    handle = kernel32.CreateFileW(str(target), 0x80000000, 0, None, 3, 0, None)
    assert handle != wintypes.HANDLE(-1).value
    try:
        assert invoke('Write', password=secrets[0]) == 20
    finally:
        assert kernel32.CloseHandle(handle)
    assert target.read_bytes() == before
    assert not list(app.glob('*.tmp'))
    checks['same_version_write_preserves_old_on_failure'] = True
    checks['password_not_logged_or_in_arguments'] = True
    assert not list(test.glob('request-*.json'))
    checks['temporary_request_files_deleted'] = True
report = {'ok': True, 'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest(), **checks}
output = root.parent / 'runtime/installer-settings-regression.json'
output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report))
