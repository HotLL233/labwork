"""Exercise dynamic projects, browser-local month selection and safe monthly copies."""
import hashlib
import io
import json
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import startup_smoke as startup

ROOT = Path(__file__).resolve().parents[1]
PORT = 19848
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
token = ''


def api(path, body=None, month=None):
    headers = {'X-Labwork-Token': token}
    if month:
        headers['X-Labwork-Month'] = month
    if body is not None and not isinstance(body, bytes):
        headers['Content-Type'] = 'application/json'
        body = json.dumps(body).encode()
    req = urllib.request.Request(f'http://127.0.0.1:{PORT}{path}', data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = response.read()
            return response.status, json.loads(data) if response.headers.get_content_type() == 'application/json' else data
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def ok(path, body=None, month=None):
    status, result = api(path, body, month)
    assert status == 200, (path, status, result)
    return result


def parts(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def sheet_paths(members):
    relations = {e.attrib['Id']: e.attrib['Target'] for e in ET.fromstring(members['xl/_rels/workbook.xml.rels'])}
    return {s.attrib['name']: 'xl/' + relations[s.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']] for s in ET.fromstring(members['xl/workbook.xml']).find('s:sheets', NS)}


def verify_month(before, after, rules):
    original, blank = parts(before), parts(after)
    paths = sheet_paths(original)
    changed = set()
    for rule in rules:
        path = paths[rule['sheet']]
        if not rule['columns']:
            assert original[path] == blank[path]
            continue
        changed.add(path)
        old = ET.fromstring(original[path])
        new = ET.fromstring(blank[path])
        cells = {c.attrib['r']: c for c in new.findall('.//s:c', NS)}
        for cell in old.findall('.//s:c', NS):
            ref = cell.attrib['r']
            actual = cells[ref]
            column, row = re.fullmatch(r'([A-Z]+)(\d+)', ref).groups()
            assert cell.attrib.get('s') == actual.attrib.get('s')
            formula = cell.find('s:f', NS)
            if formula is not None:
                counterpart = actual.find('s:f', NS)
                assert counterpart is not None and formula.text == counterpart.text and formula.attrib == counterpart.attrib
                assert actual.find('s:v', NS) is None
            elif int(row) >= rule['start_row'] and column in rule['columns']:
                assert actual.find('s:v', NS) is None and actual.find('s:is', NS) is None
            else:
                assert list(cell.itertext()) == list(actual.itertext()), ref
        assert [m.attrib for m in old.findall('.//s:mergeCell', NS)] == [m.attrib for m in new.findall('.//s:mergeCell', NS)]
    for name, data in original.items():
        if name not in changed:
            assert blank[name] == data, name


def start(home, log):
    global token
    process = subprocess.Popen([str(startup.EXE), '--home', str(home), '--port', str(PORT)], stdout=log, stderr=log)
    for _ in range(100):
        assert process.poll() is None, process.returncode
        try:
            page = ok('/').decode()
            token = re.search(r'name="labwork-token" content="([a-f0-9]+)"', page)[1]
            return process
        except OSError:
            time.sleep(.1)
    raise AssertionError('API failed to start')


def stop(process):
    if process and process.poll() is None:
        window = startup.tray_window(process.pid)
        assert window, f'No graceful-stop window for PID {process.pid}'
        assert startup.user32.PostMessageW(window, 0x111, startup.STOP, 0)
        process.wait(timeout=15)
        assert process.returncode == 0


def main():
    inventory = json.loads(Path(r'C:\labwork\tmp\physchem-research\workbook-inventory.json').read_text(encoding='utf-8-sig'))
    ef_path = Path(next(i['Path'] for i in inventory if i['Name'].startswith('EF-')))
    ef = ef_path.read_bytes()
    ef_hash = hashlib.sha256(ef).hexdigest()
    source = ROOT.parent / 'runtime/rust-preview/workbooks'
    d006, q = (source / 'D006.xlsx').read_bytes(), (source / 'Q.xlsx').read_bytes()
    expanded = parts(d006)
    water_path = next(path for name, path in sheet_paths(expanded).items() if name.strip() == '水分')
    tree = ET.fromstring(expanded[water_path])
    row = ET.SubElement(tree.find('s:sheetData', NS), '{' + NS['s'] + '}row', r='250')
    cell = ET.SubElement(row, '{' + NS['s'] + '}c', r='C250')
    ET.SubElement(cell, '{' + NS['s'] + '}v').text = '42'
    expanded[water_path] = ET.tostring(tree)
    padded = io.BytesIO()
    with zipfile.ZipFile(padded, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in expanded.items():
            archive.writestr(name, data)
    migrated = padded.getvalue()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', PORT))
    with tempfile.TemporaryDirectory(prefix='labwork-month-') as folder:
        home = Path(folder) / 'data'
        (home / 'workbooks').mkdir(parents=True)
        (home / '.labwork-rust-preview').write_text('isolated test')
        (home / 'workbooks/D006.xlsx').write_bytes(migrated)
        process = None
        with (Path(folder) / 'server.log').open('wb') as log:
            try:
                process = start(home, log)
                catalog = ok('/api/catalog')
                old = catalog['active_month']
                year, month = map(int, old.split('-'))
                next_month = f'{year + (month == 12):04d}-{month % 12 + 1:02d}'
                old_dir = home / 'months' / old / 'workbooks'
                assert (old_dir / 'D006.xlsx').read_bytes() == migrated
                assert len(catalog['projects']) == 1
                assert api('/api/month', {'month': next_month})[0] == 400
                assert not (home / 'months' / next_month).exists()
                assert not list((home / 'months').glob('.new-*'))
                ok('/api/workbook/EF?name=' + urllib.parse.quote('乙方理化项目'), ef)
                ok('/api/workbook/NEW_LAB?name=' + urllib.parse.quote('自定义项目'), q)
                catalog = ok('/api/catalog')
                projects = {p['code']: p for p in catalog['projects']}
                assert set(projects) == {'D006', 'EF', 'NEW_LAB'}
                assert projects['EF']['name'] == '乙方理化项目'
                assert len(projects['EF']['sheets']) == 20
                for code, project in projects.items():
                    for sheet in project['sheets']:
                        view = ok(f"/api/book-sheet/{code}/{sheet['key']}")['view']
                        assert view['name'] == sheet['name']
                water = next(s for s in projects['D006']['sheets'] if s['name'].strip() == '水分')
                page = ok(f"/api/book-sheet/D006/{water['key']}")['view']
                assert len(page['rows']) == 200 and page['next_row'] == 201
                page2 = ok(f"/api/book-sheet/D006/{water['key']}?start=201")['view']
                assert page2['rows'][0]['number'] == 201 and page2['next_row'] is None
                preview = ok('/api/preview', {'project': 'D006', 'method': 'water', 'entries': [{'date': old + '-10', 'batch': 'OLD-PENDING', 'measurement': {'result': '1'}}]})
                originals = {p.name: p.read_bytes() for p in old_dir.glob('*.xlsx')}
                rules = catalog['monthly_rules']
                for code in projects:
                    assert len(rules[code]) == len(projects[code]['sheets'])
                    ok('/api/month-rules/' + code, {'rules': rules[code]})
                ok('/api/month', {'month': next_month})
                new_dir = home / 'months' / next_month / 'workbooks'
                for name, data in originals.items():
                    assert (old_dir / name).read_bytes() == data
                    verify_month(data, (new_dir / name).read_bytes(), rules[Path(name).stem])
                    code = Path(name).stem
                    assert ok(f'/api/workbook/{code}/download?month={next_month}') == (new_dir / name).read_bytes()
                    assert ok(f'/api/workbook/{code}/download?month={old}') == data
                for invalid in ('2026-13', '../workbooks', '1900-01'):
                    assert api('/api/workbook/EF/download?month=' + urllib.parse.quote(invalid, safe=''))[0] == 400
                assert ok('/api/catalog')['active_month'] == next_month
                assert ok('/api/catalog', month=old)['active_month'] == old
                assert not ok('/api/catalog', month=old)['writable']
                assert ok('/api/catalog')['writable']
                assert ok('/api/history') == []
                assert [record['id'] for record in ok('/api/history', month=old)] == [preview['id']]
                assert api('/api/preview', {'project': 'D006', 'method': 'water', 'entries': [{'date': old + '-10', 'batch': 'WRONG-MONTH', 'measurement': {'result': '1'}}]})[0] == 400
                assert api('/api/workbook/LATE', q, month=old)[0] == 409
                assert api('/api/preview', {'project': 'D006', 'method': 'water'}, month=old)[0] == 409
                assert api('/api/commit/' + preview['id'], {}, month=old)[0] == 409
                hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in new_dir.glob('*.xlsx')}
                ok('/api/month', {'month': next_month})
                assert hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in new_dir.glob('*.xlsx')}
                assert api('/api/month', {'month': '1900-01'})[0] == 400
                stop(process)
                process = start(home, log)
                restarted = ok('/api/catalog')
                assert restarted['active_month'] == next_month and restarted['months'] == [old, next_month]
                assert next(p for p in restarted['projects'] if p['code'] == 'NEW_LAB')['name'] == '自定义项目'
                assert (home / 'workbooks/D006.xlsx').read_bytes() == migrated
            except BaseException:
                log.flush()
                print((Path(folder) / 'server.log').read_text(encoding='utf-8', errors='replace'))
                raise
            finally:
                stop(process)
    assert hashlib.sha256(ef_path.read_bytes()).hexdigest() == ef_hash
    assert (source / 'D006.xlsx').read_bytes() == d006
    assert (source / 'Q.xlsx').read_bytes() == q
    result = {'legacy_copy_preserved': True, 'dynamic_projects_and_chinese_names': True, 'all_sheets_browsable': True, 'raw_200_row_pagination': True, 'rules_required_atomic_month_creation': True, 'new_month_clears_confirmed_constants': True, 'formulas_styles_parameters_preserved': True, 'old_month_hash_unchanged': True, 'browser_local_month_selection': True, 'month_scoped_history_and_dates': True, 'month_download_matches_disk': True, 'download_rejects_invalid_months': True, 'old_month_writes_rejected': True, 'repeated_month_no_overwrite': True, 'backwards_month_rejected': True, 'restart_retains_latest': True, 'originals_unchanged': True}
    print(json.dumps(result, indent=2))
    (ROOT.parent / 'runtime/month-smoke-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
