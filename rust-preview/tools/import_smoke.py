"""Workbook binding regression; sends read-only fixture bytes to an isolated instance."""
import concurrent.futures
import hashlib
import io
import json
import re
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import startup_smoke as startup

PORT = 18808
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / 'runtime/rust-preview/workbooks'


def request(path, body=None, token='', origin=None):
    headers = {'X-Labwork-Token': token, 'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}
    if origin:
        headers['Origin'] = origin
    req = urllib.request.Request(f'http://127.0.0.1:{PORT}{path}', data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def main():
    sources = {name: (SOURCE / f'{name}.xlsx').read_bytes() for name in ('D006', 'Q')}
    fingerprints = {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()}
    large_workbook = io.BytesIO(sources['D006'])
    with zipfile.ZipFile(large_workbook, 'a') as archive:
        archive.writestr('docProps/import-test-padding.xml', '<padding>' + secrets.token_hex(1024 * 1024) + '</padding>', compress_type=zipfile.ZIP_STORED)
    sources['D006'] = large_workbook.getvalue()
    assert 1024 * 1024 < len(sources['D006']) < 20 * 1024 * 1024
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', PORT))
    with tempfile.TemporaryDirectory(prefix='labwork-import-') as folder:
        home = Path(folder) / 'data'
        with (Path(folder) / 'server.log').open('wb') as log:
            process = subprocess.Popen([str(startup.EXE), '--home', str(home), '--port', str(PORT)], stdout=log, stderr=log)
            try:
                for _ in range(100):
                    assert process.poll() is None, process.returncode
                    try:
                        status, page = request('/')
                        if status == 200:
                            break
                    except OSError:
                        pass
                    time.sleep(.1)
                else:
                    raise AssertionError('API did not start')
                token = re.search(rb'name="labwork-token" content="([a-f0-9]+)"', page)[1].decode()
                active=json.loads(request('/api/catalog')[1])
                working=home/'months'/active['active_month']/'workbooks'
                path = '/api/workbook/D006'
                assert request(path, sources['D006'])[0] == 403
                assert request(path, sources['D006'], token, 'http://127.0.0.1:18802')[0] == 403
                assert request('/api/workbook/BAD%20CODE', sources['D006'], token)[0] == 400
                assert request('/api/workbook/A3', sources['D006'], token)[0] == 200
                assert request(path, b'not an xlsx', token)[0] == 400
                # Any valid workbook can be bound; methods are enabled only when its layout matches.
                assert request(path, b'0' * (20 * 1024 * 1024 + 1), token)[0] == 413
                bomb = io.BytesIO()
                with zipfile.ZipFile(bomb, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr('oversized.xml', b'0' * (101 * 1024 * 1024))
                assert request(path, bomb.getvalue(), token)[0] == 400
                too_many = io.BytesIO()
                with zipfile.ZipFile(too_many, 'w') as archive:
                    for index in range(3001):
                        archive.writestr(f'{index}.xml', b'')
                assert request(path, too_many.getvalue(), token)[0] == 400
                with zipfile.ZipFile(io.BytesIO(sources['D006'])) as source:
                    members = {name: source.read(name) for name in source.namelist()}
                sheet_name = next(name for name in members if name.startswith('xl/worksheets/') and name.endswith('.xml'))
                for element in ('<cols><col min="0" max="1"/></cols>', '<mergeCells><mergeCell ref="B8:A4"/></mergeCells>'):
                    tree = ET.fromstring(members[sheet_name])
                    tree.append(ET.fromstring(element))
                    malformed = io.BytesIO()
                    with zipfile.ZipFile(malformed, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                        for name, data in members.items():
                            archive.writestr(name, ET.tostring(tree) if name == sheet_name else data)
                    assert request(path, malformed.getvalue(), token)[0] == 400
                    assert request('/api/catalog')[0] == 200
                for bad_members in (
                    {**members, '../outside.xml': b'bad'},
                    {**members, 'xl/vbaProject.bin': b'macro'},
                    {**members, '[Content_Types].xml': members['[Content_Types].xml'].replace(b'spreadsheetml.sheet.main+xml', b'spreadsheetml.template.main+xml')},
                ):
                    output = io.BytesIO()
                    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                        for name, data in bad_members.items():
                            archive.writestr(name, data)
                    assert request(path, output.getvalue(), token)[0] == 400
                missing_header = io.BytesIO()
                with zipfile.ZipFile(missing_header, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                    for name, data in members.items():
                        if name.startswith('xl/worksheets/') and name.endswith('.xml'):
                            root = ET.fromstring(data)
                            for row in root.iter('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row'):
                                for cell in list(row):
                                    if cell.attrib.get('r') == 'B1':
                                        row.remove(cell)
                            data = ET.tostring(root)
                        archive.writestr(name, data)
                assert request('/api/workbook/RAW_ONLY', missing_header.getvalue(), token)[0] == 200
                assert not (working / 'D006.xlsx').exists()
                status, body = request(path, sources['D006'], token)
                assert status == 200, (status, body)
                assert json.loads(body) == {'available': True, 'name': 'D006.xlsx'}
                assert (working / 'D006.xlsx').read_bytes() == sources['D006']
                assert request(path, sources['Q'], token)[0] == 409
                assert (working / 'D006.xlsx').read_bytes() == sources['D006']
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda _: request('/api/workbook/Q', sources['Q'], token)[0], range(2)))
                assert sorted(results) == [200, 409], results
                catalog = json.loads(request('/api/catalog')[1])
                assert all(next(p for p in catalog['projects'] if p['code'] == name)['available'] for name in sources)
                for name in sources:
                    assert request(f'/api/sheet/{name}/chloride')[0] == 200
                assert not list((working).glob('*.tmp'))
                assert not list((working).glob('.import-*'))
            except BaseException:
                log.flush()
                print((Path(folder) / 'server.log').read_text(encoding='utf-8', errors='replace'))
                raise
            finally:
                if process.poll() is None:
                    window = startup.tray_window(process.pid)
                    assert window, f'Cannot gracefully stop PID {process.pid}'
                    assert startup.user32.PostMessageW(window, 0x111, startup.STOP, 0)
                    process.wait(timeout=15)
                assert process.returncode == 0, process.returncode
    for name, fingerprint in fingerprints.items():
        assert hashlib.sha256((SOURCE / f'{name}.xlsx').read_bytes()).hexdigest() == fingerprint
    print(json.dumps({'bind_D006_Q': True, 'valid_body_above_global_1MiB': True, 'large_workbook_bytes': len(sources['D006']), 'catalog_and_sheet_available': True, 'no_overwrite': True, 'concurrent_first_bind': True, 'invalid_rejected_dynamic_projects_supported': True, 'size_and_zip_bomb_rejected': True, 'macro_template_traversal_rejected': True, 'malformed_columns_merges_rejected_without_crash': True, 'token_origin_guard': True, 'originals_unchanged': True, 'temporary_files_cleaned': True}, indent=2))


if __name__ == '__main__':
    main()
