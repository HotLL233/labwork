"""Exercise direct worksheet edits using disposable files and an isolated server."""
import argparse
from contextlib import closing
import ctypes
from ctypes import wintypes
from datetime import date, timedelta
import hashlib
import io
import json
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

from format_smoke import fixture, parts, NS, ROOT
_BASE = fixture()


def fixture():
    return _BASE


def modified(data, updates):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as original, zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for member in original.infolist():
            archive.writestr(member, updates.get(member.filename, original.read(member)))
    return output.getvalue()


def editable_fixture(date1904=False, protected=False):
    data = fixture(); members = parts(data)
    sheet = members['xl/worksheets/sheet1.xml'].decode()
    sheet = sheet.replace('<cols>', '<sheetViews><sheetView workbookViewId="0"><pane ySplit="2" topLeftCell="A3" state="frozen"/></sheetView></sheetViews><cols>')
    sheet = sheet.replace('<row r="3">', '<row r="3" ht="33" customHeight="1">')
    sheet = sheet.replace('<c r="A3">', '<c r="A3" s="1">')
    sheet = sheet.replace('</row></sheetData>', '<c r="E3"><f>ROUND(C3*2,2)</f><v>2.47</v></c><c r="F3"><f t="shared" si="0" ref="F3:F4">C3/2</f><v>0.61725</v></c><c r="G3" t="inlineStr"><is><t>合并</t></is></c><c r="I3" t="d"><v>2026-01-01</v></c></row><row r="4"><c r="C4"><v>2</v></c><c r="F4"><f t="shared" si="0"/><v>1</v></c></row></sheetData>')
    sheet = sheet.replace('</worksheet>', '<mergeCells count="1"><mergeCell ref="G3:H3"/></mergeCells><pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/></worksheet>')
    if protected: sheet = sheet.replace('</sheetData>', '</sheetData><sheetProtection sheet="1"/>')
    styles = members['xl/styles.xml'].decode().replace('<cellXfs count="1">', '<cellXfs count="2">').replace('</cellXfs>', '<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>')
    book = members['xl/workbook.xml']
    if date1904: book = book.replace(b'<sheets>', b'<workbookPr date1904="1"/><sheets>')
    return modified(data, {'xl/worksheets/sheet1.xml': sheet.encode(), 'xl/styles.xml': styles.encode(), 'xl/workbook.xml': book})


def semantic(node):
    return (node.tag, sorted(node.attrib.items()), node.text if node.text and node.text.strip() else None, tuple(semantic(c) for c in node))


def cell(data, ref):
    return ET.fromstring(parts(data)['xl/worksheets/sheet1.xml']).find(f'.//s:c[@r="{ref}"]', NS)


def value(data, ref):
    node = cell(data, ref)
    return ''.join(node.find('s:is', NS).itertext()) if node.get('t') == 'inlineStr' else node.findtext('s:v', default='', namespaces=NS)


def assert_layout(before, after):
    old, new = parts(before), parts(after)
    assert old['xl/styles.xml'] == new['xl/styles.xml'], 'style bytes changed'
    for name in old:
        if name not in ('xl/workbook.xml', 'xl/worksheets/sheet1.xml'): assert old[name] == new[name], name
    a, b = (ET.fromstring(p['xl/worksheets/sheet1.xml']) for p in (old, new))
    for tag in ('sheetViews', 'cols', 'mergeCells', 'pageMargins'):
        left, right = a.find('s:' + tag, NS), b.find('s:' + tag, NS)
        assert (semantic(left) if left is not None else None) == (semantic(right) if right is not None else None), tag
    for row in a.findall('s:sheetData/s:row', NS):
        assert row.attrib == b.find(f's:sheetData/s:row[@r="{row.get("r")}"]', NS).attrib
    for original in a.findall('.//s:c', NS):
        current = b.find(f'.//s:c[@r="{original.get("r")}"]', NS)
        assert {k:v for k,v in original.attrib.items() if k != 't'} == {k:v for k,v in current.attrib.items() if k != 't'}
        formula = original.find('s:f', NS)
        if formula is not None: assert semantic(formula) == semantic(current.find('s:f', NS)), original.get('r')


def run(exe):
    with tempfile.TemporaryDirectory(prefix='labwork-cell-edit-') as directory:
        folder = Path(directory); home = folder/'data'; process = None; token = ''
        with socket.socket() as sock: sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def api(path, body=None, month=None):
            headers = {'Origin': base, 'X-Labwork-Token': token}
            if month: headers['X-Labwork-Month'] = month
            if body is not None and not isinstance(body, bytes): headers['Content-Type'] = 'application/json'; body = json.dumps(body).encode()
            try: response = opener.open(urllib.request.Request(base+path, data=body, headers=headers), timeout=20)
            except urllib.error.HTTPError as error: response = error
            with response:
                data = response.read()
                return response.status, json.loads(data) if response.headers.get_content_type() == 'application/json' else data
        def ok(path, body=None, month=None):
            status, data = api(path, body, month); assert status == 200, (path, status, data); return data
        def import_source(code, data):
            source = folder/(code+'.xlsx'); source.write_bytes(data)
            ok('/api/workbook/'+code+'/source-import', {'path': str(source)})
            return source, home/'months'/month/'workbooks'/(code+'.xlsx')
        def view(code): return ok('/api/book-sheet/'+code+'/s0', month=month)
        def payload(code, changes, **kwargs): return {'sheet':'格式测试', 'fingerprint':view(code)['fingerprint'], 'changes':[{'ref':k,'value':v} for k,v in changes], **kwargs}
        def edit(code, changes, **kwargs): return ok('/api/workbook/'+code+'/edit-preview', payload(code, changes, **kwargs), month)
        try:
            with (folder/'server.log').open('wb') as log: process = subprocess.Popen([str(exe), '--headless', '--home', str(home), '--port', str(port)], stdout=log, stderr=log)
            for _ in range(100):
                assert process.poll() is None, 'isolated server exited'
                try: page = ok('/').decode(); break
                except OSError: time.sleep(.1)
            else: raise AssertionError('server readiness timeout')
            token = re.search(r'name="labwork-token" content="([^"]+)"', page)[1]
            month = ok('/api/catalog')['active_month']
            ok('/api/workbook/UNLINKED',fixture())
            unlinked=edit('UNLINKED',[('C3','2')]); assert api('/api/commit/'+unlinked['id'],{},month)[0] == 409
            assert (home/'months'/month/'workbooks/UNLINKED.xlsx').read_bytes()==fixture()
            source, mirror = import_source('CELLS', editable_fixture())
            original = source.read_bytes(); initial_view = view('CELLS')
            by_ref = {c['ref']:c for r in initial_view['view']['rows'] for c in r['cells']}
            assert by_ref['F4']['readonly'] and by_ref['D3']['readonly'] and by_ref['H3']['readonly']
            pending = edit('CELLS', [('A3', month+'-15'), ('B3', '000123'), ('C3', '0'), ('G3', '=2+3'), ('I3', month+'-16')])
            assert source.read_bytes() == mirror.read_bytes() == original, 'preview wrote business files'
            candidate = ok(pending['download']); assert_layout(original, candidate)
            assert value(candidate,'B3') == '000123' and cell(candidate,'B3').get('t') == 'inlineStr'
            assert value(candidate,'G3') == '=2+3' and cell(candidate,'G3').find('s:f', NS) is None
            assert value(candidate,'D3') == value(candidate,'F3') == '0'
            assert value(candidate,'E3') == '' and pending['warnings']
            assert cell(candidate,'I3').get('t') == 'd' and value(candidate,'I3') == month+'-16'
            epoch = date(1899,12,30); assert value(candidate,'A3') == str((date.fromisoformat(month+'-15')-epoch).days)
            # Creating another preview can revert a draft value without touching either original.
            revised = edit('CELLS', [('C3','2')], preview_id=pending['id'])
            assert value(ok(revised['download']),'D3') == '4' and value(ok(pending['download']),'D3') == '0'
            assert source.read_bytes() == mirror.read_bytes() == original
            committed = ok('/api/commit/'+pending['id'], {}, month)
            assert committed['source_sync']['state'] == 'synced' and source.read_bytes() == mirror.read_bytes() == candidate
            assert ok('/api/commit/'+pending['id'], {}, month)['state'] == 'done'
            backups = [p.read_bytes() for p in (home/'backups').glob('*.xlsx')]; assert original in backups
            assert api('/api/commit/'+revised['id'], {}, month)[0] == 409
            stale = payload('CELLS',[('C3','3')]); stale['fingerprint'] = initial_view['fingerprint']; assert api('/api/workbook/CELLS/edit-preview',stale,month)[0] == 409
            for changes in ([('D3','3')],[('F4','3')],[('H3','3')],[('A0','2')],[('XFE1','2')],[('A1048577','2')],[('C3','1'),('C3','2')],[('C3','NaN')],[('C3','x'*2001)]):
                assert api('/api/workbook/CELLS/edit-preview',payload('CELLS',changes),month)[0] == 400, changes
            assert api('/api/workbook/CELLS/edit-preview',payload('CELLS',[(f'A{i}','') for i in range(1,1002)]),month)[0] == 400
            assert source.read_bytes() == mirror.read_bytes() == candidate
            protected, protected_mirror = import_source('PROTECTED', editable_fixture(protected=True))
            assert api('/api/workbook/PROTECTED/edit-preview',payload('PROTECTED',[('C3','2')]),month)[0] == 400
            assert protected.read_bytes() == protected_mirror.read_bytes()
            date_source, _ = import_source('DATE1904', editable_fixture(date1904=True))
            date_preview = edit('DATE1904',[('A3',month+'-15')]); date_candidate = ok(date_preview['download'])
            assert value(date_candidate,'A3') == str((date.fromisoformat(month+'-15')-date(1904,1,1)).days)
            assert date_source.read_bytes() == editable_fixture(date1904=True)
            # A calculated draft can be amended in its cells before one confirmation.
            calculated_source, calculated_mirror = import_source('CALCULATED', fixture())
            generated = ok('/api/preview', {'project':'CALCULATED','method':'sheet_0','entries':[{'date':month+'-10','batch':'DRAFT','measurement':{'col_C':'3'}}]}, month)
            row = generated['rows'][0]
            amended = edit('CALCULATED',[(f'C{row}','4')],preview_id=generated['id'])
            amended_bytes = ok(amended['download']); assert value(amended_bytes,f'D{row}') == '8'
            assert calculated_source.read_bytes() == calculated_mirror.read_bytes() == fixture()
            ok('/api/commit/'+amended['id'],{},month); assert calculated_source.read_bytes() == calculated_mirror.read_bytes() == amended_bytes
            assert api('/api/workbook/CELLS/edit-preview',payload('CELLS',[('C3','3')],preview_id=generated['id']),month)[0] == 409
            # Conflicts are detected before replacing the mirror or original.
            conflict_source, conflict_mirror = import_source('CONFLICT', editable_fixture())
            conflict = edit('CONFLICT',[('C3','5')]); external = modified(conflict_source.read_bytes(), {'xl/workbook.xml':parts(conflict_source.read_bytes())['xl/workbook.xml'].replace(b'<sheets>',b'<!--external--><sheets>')})
            conflict_source.write_bytes(external); assert api('/api/commit/'+conflict['id'],{},month)[0] == 409
            assert conflict_source.read_bytes() == external and conflict_mirror.read_bytes() == editable_fixture()
            mirror_source, mirror_file = import_source('MIRROR', editable_fixture()); stale_mirror = edit('MIRROR',[('C3','5')]); mirror_file.write_bytes(external)
            assert api('/api/commit/'+stale_mirror['id'],{},month)[0] == 409 and mirror_source.read_bytes() == editable_fixture() and mirror_file.read_bytes() == external
            # A Windows lock exercises the same recoverable source-sync transaction as calculation writes.
            lock_source, lock_mirror = import_source('LOCKED', editable_fixture()); locked = edit('LOCKED',[('C3','6')]); locked_bytes = ok(locked['download'])
            kernel = ctypes.WinDLL('kernel32',use_last_error=True); kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]; kernel.CreateFileW.restype=wintypes.HANDLE; kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            handle=kernel.CreateFileW(str(lock_source),0x80000000,1,None,3,0,None); assert handle != wintypes.HANDLE(-1).value
            try:
                assert api('/api/commit/'+locked['id'],{},month)[0] in (400,409)
                assert lock_source.read_bytes() == editable_fixture() and lock_mirror.read_bytes() == locked_bytes
                record = next(r for r in ok('/api/history',month=month) if r['id']==locked['id']); assert record['state']=='writing' and record['request']['operation']=='edit'
                assert api('/api/workbook/LOCKED/edit-preview',payload('LOCKED',[('C3','7')]),month)[0] == 409
            finally: kernel.CloseHandle(handle)
            ok('/api/commit/'+locked['id'],{},month); assert lock_source.read_bytes() == lock_mirror.read_bytes() == locked_bytes
            zero_source, _ = import_source('ZERO', fixture()); zero=edit('ZERO',[('C3','0')]); assert value(ok(zero['download']),'D3')=='0'
            old_month = (date.fromisoformat(month+'-01')-timedelta(days=1)).strftime('%Y-%m'); old_directory=home/'months'/old_month/'workbooks'; old_directory.mkdir(parents=True); (old_directory/'CELLS.xlsx').write_bytes(original)
            assert api('/api/workbook/CELLS/edit-preview',payload('CELLS',[('C3','5')]),old_month)[0] == 409
            with closing(sqlite3.connect(home/'preview.sqlite3')) as db:
                assert all(json.loads(snapshot).get('preserves_layout') for snapshot, in db.execute("SELECT method_snapshot FROM previews WHERE json_extract(request,'$.operation')='edit'"))
        finally:
            if process is not None:
                if process.poll() is None: process.terminate()
                process.wait(timeout=10)
        checks = dict.fromkeys(['preview_does_not_write_source_or_mirror','direct_edit_source_and_mirror_confirmed','layout_styles_and_formulas_preserved','formula_and_shared_formula_readonly','merged_nonanchor_rejected','protected_cells_rejected','leading_zero_batch_preserved','formula_like_text_is_literal','zero_recalculates_supported_formulas','unknown_formula_cache_invalidated','date_1900_1904_and_iso_types_preserved','draft_repreview_and_revert_no_write','calculation_preview_cells_amendable','source_and_mirror_conflicts_rejected','stale_or_other_project_preview_rejected','validation_limits_rejected','write_backup_and_idempotent_confirmation','source_lock_recovers_through_history','pending_write_blocks_new_edits','history_month_readonly'],True)
        result={'ok':True,'version':re.search(r'^version\s*=\s*"([^"]+)"',(ROOT/'Cargo.toml').read_text(),re.M)[1], 'executable_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(), **checks}
        output=ROOT.parent/'runtime/cell-edit-smoke-result.json'; output.parent.mkdir(parents=True,exist_ok=True); output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--exe',type=Path,default=ROOT/'target/release/labwork.exe'); run(parser.parse_args().exe.resolve())
