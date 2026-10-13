"""Check Windows startup using isolated data and graceful tray shutdown only."""
import ctypes
from ctypes import wintypes
import json
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / 'target/release/labwork.exe'
user32 = ctypes.WinDLL('user32', use_last_error=True)
CALLBACK = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [CALLBACK, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
STOP = int(re.search(r'const STOP: usize = (\d+);', (ROOT / 'src/tray.rs').read_text(encoding='utf-8'))[1])


def tray_window(pid):
    windows = []

    @CALLBACK
    def inspect(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, name, len(name))
        if owner.value == pid and name.value == 'LabworkRustTray':
            windows.append(hwnd)
        return True

    user32.EnumWindows(inspect, 0)
    return windows[0] if windows else None


def api(port, path='/api/catalog', headers=None, body=None):
    request = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=body, headers=headers or {})
    with urllib.request.urlopen(request, timeout=2) as response:
        return response.read()


def rejected(port, headers, path='/api/catalog', body=None):
    try:
        api(port, path, headers, body)
    except urllib.error.HTTPError as error:
        assert error.code == 403, (error.code, error.read())
    else:
        raise AssertionError('Expected HTTP 403')


def running(exe, arguments, port, expected_home, log, minimum_seconds=0):
    process = subprocess.Popen([str(exe), *arguments], stdout=log, stderr=log, cwd=exe.parent)
    started = time.monotonic()
    try:
        for _ in range(100):
            assert process.poll() is None, f'Startup failed: {process.returncode}'
            try:
                catalog = json.loads(api(port))
                break
            except (OSError, urllib.error.URLError):
                time.sleep(.1)
        else:
            raise AssertionError('API did not start')
        assert catalog['port'] == port, catalog
        actual = catalog['data_directory'].removeprefix('\\\\?\\')
        assert Path(actual).resolve() == expected_home.resolve(), actual
        assert tray_window(process.pid), f'No tray window for PID {process.pid}'
        token = re.search(r'name="labwork-token" content="([a-f0-9]+)"', api(port, '/').decode())[1]
        rejected(port, {'Host': '127.0.0.1:18802'})
        rejected(port, {'Origin': 'http://127.0.0.1:18802', 'X-Labwork-Token': token, 'Content-Type': 'application/json'}, '/api/preview', b'{"project":"D006","method":"chloride"}')
        while time.monotonic() - started < minimum_seconds:
            assert process.poll() is None, 'Tray process exited while idle'
            time.sleep(.1)
        assert json.loads(api(port))['port'] == port
    finally:
        if process.poll() is None:
            window = tray_window(process.pid)
            assert window, f'Cannot gracefully stop test PID {process.pid}; no process was force-killed'
            assert user32.PostMessageW(window, 0x111, STOP, 0), ctypes.get_last_error()
            process.wait(timeout=15)
        assert process.returncode == 0, process.returncode


def fails(exe, arguments, log):
    process = subprocess.Popen([str(exe), '--headless', *arguments], stdout=log, stderr=log, cwd=exe.parent)
    process.wait(timeout=10)
    assert process.returncode != 0, arguments


def main():
    content = EXE.read_bytes()
    pe_offset = struct.unpack_from('<I', content, 0x3c)[0]
    assert content[pe_offset:pe_offset + 4] == b'PE\0\0'
    subsystem = struct.unpack_from('<H', content, pe_offset + 24 + 68)[0]
    assert subsystem == 2, f'Expected Windows GUI subsystem, got {subsystem}'
    # Reserve no production port; fail immediately if our isolated test ports are busy.
    for port in (18806, 18807):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))
    with tempfile.TemporaryDirectory(prefix='labwork-startup-') as folder:
        home = Path(folder)
        exe = home / 'labwork.exe'
        shutil.copy2(EXE, exe)
        config = home / 'labwork.json'
        config.write_text(json.dumps({'port': 18806, 'data_dir': str(home / 'configured-data')}), encoding='utf-8')
        with (home / 'startup.log').open('wb') as log:
            try:
                running(exe, [], 18806, home / 'configured-data', log, minimum_seconds=5)
                explicit = home / 'custom.json'
                explicit.write_text(json.dumps({'port': 18807, 'data_dir': str(home / 'explicit-data')}), encoding='utf-8')
                running(exe, ['--config', str(explicit)], 18807, home / 'explicit-data', log)
                running(exe, ['--config', str(explicit), '--port', '18806', '--home', str(home / 'override-data')], 18806, home / 'override-data', log)
                for invalid in ('0', '65536', 'abc'):
                    fails(exe, ['--port', invalid], log)
                with socket.socket() as occupied:
                    occupied.bind(('127.0.0.1', 18806))
                    occupied.listen()
                    fails(exe, ['--port', '18806'], log)
                unknown = home / 'unknown-data'
                unknown.mkdir()
                (unknown / 'keep.txt').write_text('untouched', encoding='utf-8')
                fails(exe, ['--home', str(unknown)], log)
                assert (unknown / 'keep.txt').read_text(encoding='utf-8') == 'untouched'
                assert not (unknown / 'preview.sqlite3').exists()
            except BaseException:
                log.flush()
                print((home / 'startup.log').read_text(encoding='utf-8', errors='replace'))
                raise
    result = {'gui_subsystem': subsystem, 'default_tray_alive_seconds': 5, 'adjacent_config': True, 'explicit_config': True, 'cli_overrides': True, 'pid_scoped_graceful_stop': True, 'headless_errors_nonzero': True, 'host_origin_port_guard': True, 'unknown_data_preserved': True}
    (ROOT.parent / 'runtime/startup-smoke-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
