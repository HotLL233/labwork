"""Verify source writeback using disposable copies, never original workbooks."""
import hashlib
from contextlib import closing
import ctypes
from ctypes import wintypes
import io
import json
import socket
import sqlite3
import tempfile
import zipfile
from pathlib import Path

import month_smoke as server

server.PORT = 19852
ROOT = Path(__file__).resolve().parents[1]


def changed_copy(data):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in server.parts(data).items():
            if name != 'docProps/source-smoke.xml':
                archive.writestr(name, content)
        archive.writestr('docProps/source-smoke.xml', '<external-change/>')
    return output.getvalue()


def main():
    fixture = ROOT.parent / 'runtime/rust-preview/workbooks/D006.xlsx'
    original = fixture.read_bytes()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', server.PORT))
    with tempfile.TemporaryDirectory(prefix='labwork-source-') as directory:
        folder = Path(directory)
        home = folder / 'data'
        source = folder / 'D006-理化' / '2026' / '2026.07' / 'D006-理化-202607.xlsx'
        source.parent.mkdir(parents=True)
        source.write_bytes(original)
        alternate = folder / '另一个原表.xlsx'
        alternate.write_bytes(original)
        process = None
        with (folder / 'server.log').open('wb') as log:
            try:
                process = server.start(home, log)
                server.ok('/api/workbook/D006', original)
                catalog = server.ok('/api/catalog')
                month = catalog['active_month']
                working = home / 'months' / month / 'workbooks/D006.xlsx'
                endpoint = '/api/workbook/D006/source'

                def preview(batch):
                    return server.ok('/api/preview', {'project': 'D006', 'method': 'water', 'entries': [{'date': month + '-10', 'batch': batch, 'measurement': {'result': '1.234'}}]}, month)

                def commit(result):
                    return server.api('/api/commit/' + result['id'], {}, month)

                def bind(path):
                    return server.ok(endpoint, {'path': str(path) if path else None}, month)

                unlinked = preview('UNLINKED-MUST-NOT-WRITE')
                assert commit(unlinked)[0] == 409
                assert source.read_bytes() == working.read_bytes() == original
                with closing(sqlite3.connect(home / 'preview.sqlite3')) as db, db:
                    legacy_request = db.execute('SELECT request FROM previews WHERE id=?', [unlinked['id']]).fetchone()[0]
                    empty_month = json.loads(legacy_request)
                    empty_month['month'] = ''
                    db.execute("UPDATE previews SET state='writing',request=? WHERE id=?", [json.dumps(empty_month), unlinked['id']])
                assert server.api('/api/workbook/D006/remove', {})[0] == 409
                assert server.api(endpoint, {'path': None})[0] == 409
                assert server.api('/api/month-rules/D006', {'rules': catalog['monthly_rules']['D006']})[0] == 409
                with closing(sqlite3.connect(home / 'preview.sqlite3')) as db, db:
                    db.execute("UPDATE previews SET state='preview',request=? WHERE id=?", [legacy_request, unlinked['id']])

                imported_source = folder / 'IMPORTED-理化' / month[:4] / month.replace('-', '.') / ('IMPORTED-理化-' + month.replace('-', '') + '.xlsx')
                imported_source.parent.mkdir(parents=True)
                imported_source.write_bytes(original)
                imported = server.ok('/api/workbook/IMPORTED/source-import', {'path': str(imported_source), 'name': '服务器导入原文件'}, month)
                assert imported['source_link']['path'] == str(imported_source) and imported['code'] == 'IMPORTED'
                assert (working.parent / 'IMPORTED.xlsx').read_bytes() == imported_source.read_bytes() == original
                assert server.api('/api/workbook/DUPLICATE/source-import', {'path': str(imported_source)}, month)[0] == 409
                assert not (working.parent / 'DUPLICATE.xlsx').exists()
                assert server.api('/api/workbook/IMPORTED/source-import', {'path': str(alternate)}, month)[0] == 409
                assert (working.parent / 'IMPORTED.xlsx').read_bytes() == original
                imported_project = next(p for p in server.ok('/api/catalog')['projects'] if p['code'] == 'IMPORTED')
                assert imported_project['source_link'] and imported_project['name'] == '服务器导入原文件'

                names_file = home / 'project-names.json'
                previous_names = names_file.read_bytes()
                names_file.write_bytes(b'not valid json')
                try:
                    assert server.api('/api/workbook/FAILED/source-import', {'path': str(alternate), 'name': '失败应回滚'}, month)[0] == 400
                    assert not (working.parent / 'FAILED.xlsx').exists()
                    with closing(sqlite3.connect(home / 'preview.sqlite3')) as db:
                        assert db.execute("SELECT count(*) FROM source_links WHERE project='FAILED'").fetchone()[0] == 0
                    assert alternate.read_bytes() == original
                finally:
                    names_file.write_bytes(previous_names)

                for invalid in ['relative.xlsx', str(folder / 'missing.xlsx'), str(working), r'\\server\share\source.xlsx']:
                    assert server.api(endpoint, {'path': invalid}, month)[0] in (400, 409), invalid
                wrong = folder / 'wrong.txt'
                wrong.write_bytes(original)
                assert server.api(endpoint, {'path': str(wrong)}, month)[0] == 400
                mismatch = folder / '不同工作表.xlsx'
                output = io.BytesIO()
                with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
                    for name, content in server.parts(original).items():
                        if name == 'xl/workbook.xml':
                            content = content.replace('水分'.encode(), '不匹配'.encode())
                        archive.writestr(name, content)
                mismatch.write_bytes(output.getvalue())
                assert server.api(endpoint, {'path': str(mismatch)}, month)[0] in (400, 409)

                bind(source)
                assert source.read_bytes() == original and working.read_bytes() == original
                project = next(p for p in server.ok('/api/catalog')['projects'] if p['code'] == 'D006')
                assert project['source_link'], project
                server.ok('/api/workbook/OTHER', original)
                assert server.api('/api/workbook/OTHER/source', {'path': str(source)}, month)[0] == 409

                result = preview('SOURCE-SYNC-01')
                assert source.read_bytes() == original and working.read_bytes() == original
                candidate = server.ok('/api/download/' + result['id'])
                status, response = commit(result)
                assert status == 200, (status, response)
                assert response['source_sync']['state'] == 'synced', response
                assert source.read_bytes() == working.read_bytes() == candidate
                assert any(p.read_bytes() == original for p in (home / 'backups').rglob('*') if p.is_file()), 'Missing original backup'
                backup_count = len([p for p in (home / 'backups').rglob('*') if p.is_file()])
                assert commit(result)[0] == 200
                assert source.read_bytes() == working.read_bytes() == candidate
                assert len([p for p in (home / 'backups').rglob('*') if p.is_file()]) == backup_count

                def lost_qualified_attributes(data):
                    output = io.BytesIO(); changed = False
                    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
                        for name, content in server.parts(data).items():
                            if name.endswith('.xml'):
                                modified = content.replace(b'mc:Ignorable=', b'Ignorable=')
                                changed |= modified != content
                                content = modified
                            archive.writestr(name, content)
                    assert changed, 'Fixture must include a qualified compatibility attribute'
                    return output.getvalue()

                damaged_pending = preview('GRID10-DAMAGED-PREVIEW')
                damaged_path = home / 'previews' / (damaged_pending['id'] + '.xlsx')
                damaged_bytes = lost_qualified_attributes(damaged_path.read_bytes())
                damaged_path.write_bytes(damaged_bytes)
                with closing(sqlite3.connect(home / 'preview.sqlite3')) as db, db:
                    snapshot = json.loads(db.execute('SELECT method_snapshot FROM previews WHERE id=?', [damaged_pending['id']]).fetchone()[0])
                    assert snapshot['preserves_layout'] is True and snapshot['workspace_before_hash']
                    db.execute('UPDATE previews SET after_hash=? WHERE id=?', [hashlib.sha256(damaged_bytes).hexdigest(), damaged_pending['id']])
                assert commit(damaged_pending)[0] in (400, 409)
                assert source.read_bytes() == working.read_bytes() == candidate
                with closing(sqlite3.connect(home / 'preview.sqlite3')) as db, db:
                    db.execute("UPDATE previews SET state='writing' WHERE id=?", [damaged_pending['id']])
                working.write_bytes(damaged_bytes)
                assert commit(damaged_pending)[0] in (400, 409)
                assert source.read_bytes() == candidate and working.read_bytes() == damaged_bytes
                working.write_bytes(candidate)
                with closing(sqlite3.connect(home / 'preview.sqlite3')) as db, db:
                    db.execute('DELETE FROM previews WHERE id=?', [damaged_pending['id']])
                compatible = preview('GRID10-VALID-CANDIDATE')
                candidate = server.ok('/api/download/' + compatible['id'])
                assert commit(compatible)[0] == 200
                assert source.read_bytes() == working.read_bytes() == candidate

                damaged_source = lost_qualified_attributes(candidate)
                source.write_bytes(damaged_source)
                damaged_request = {'project': 'D006', 'method': 'water', 'entries': [{'date': month + '-10', 'batch': 'DAMAGED-SOURCE-REJECTED', 'measurement': {'result': '1.234'}}]}
                assert server.api('/api/preview', damaged_request, month)[0] in (400, 409)
                assert source.read_bytes() == damaged_source and working.read_bytes() == candidate
                method_sheet = next(item for item in project['methods'] if item['key'] == 'water')['sheet']
                damaged_format = {'sheet': method_sheet, 'range': 'A1', 'font_color': '#123456', 'fingerprint': hashlib.sha256(damaged_source).hexdigest()}
                assert server.api('/api/workbook/D006/format', damaged_format, month)[0] in (400, 409)
                assert source.read_bytes() == damaged_source and working.read_bytes() == candidate
                source.write_bytes(candidate)

                def grid9_snapshot(result, state):
                    with closing(sqlite3.connect(home / 'preview.sqlite3')) as db, db:
                        value = json.loads(db.execute('SELECT method_snapshot FROM previews WHERE id=?', [result['id']]).fetchone()[0])
                        assert value.pop('preserves_layout') is True
                        assert value.get('workspace_before_hash'), 'Must simulate grid.9, not a much older preview'
                        db.execute('UPDATE previews SET method_snapshot=?,state=? WHERE id=?', [json.dumps(value), state, result['id']])

                grid9_pending = preview('GRID9-PREVIEW-REJECTED')
                grid9_snapshot(grid9_pending, 'preview')
                assert commit(grid9_pending)[0] == 409
                assert source.read_bytes() == working.read_bytes() == candidate

                grid9_writing = preview('GRID9-WRITING-REJECTED')
                grid9_bytes = server.ok('/api/download/' + grid9_writing['id'])
                grid9_snapshot(grid9_writing, 'writing')
                working.write_bytes(grid9_bytes)
                assert commit(grid9_writing)[0] == 409
                assert source.read_bytes() == candidate and working.read_bytes() == grid9_bytes
                source.write_bytes(grid9_bytes)
                assert commit(grid9_writing)[0] == 200
                candidate = grid9_bytes
                assert source.read_bytes() == working.read_bytes() == candidate

                def legacy_snapshot(result, state):
                    with closing(sqlite3.connect(home / 'preview.sqlite3')) as db, db:
                        value = json.loads(db.execute('SELECT method_snapshot FROM previews WHERE id=?', [result['id']]).fetchone()[0])
                        value.pop('workspace_before_hash')
                        db.execute('UPDATE previews SET method_snapshot=?,state=? WHERE id=?', [json.dumps(value), state, result['id']])

                legacy_pending = preview('LEGACY-PREVIEW-REJECTED')
                legacy_snapshot(legacy_pending, 'preview')
                assert commit(legacy_pending)[0] == 409
                assert source.read_bytes() == working.read_bytes() == candidate

                legacy_writing = preview('LEGACY-WRITING-RECOVERY')
                legacy_bytes = server.ok('/api/download/' + legacy_writing['id'])
                legacy_snapshot(legacy_writing, 'writing')
                working.write_bytes(legacy_bytes)
                assert commit(legacy_writing)[0] == 409
                assert source.read_bytes() == candidate and working.read_bytes() == legacy_bytes
                source.write_bytes(legacy_bytes)
                assert commit(legacy_writing)[0] == 200
                candidate = legacy_bytes
                assert source.read_bytes() == working.read_bytes() == candidate

                interrupted = preview('SOURCE-INTERRUPTED')
                interrupted_bytes = server.ok('/api/download/' + interrupted['id'])
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
                kernel.CreateFileW.restype = wintypes.HANDLE
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                # Match Excel's exclusive write/delete lock while allowing original reads.
                handle = kernel.CreateFileW(str(source), 0x80000000, 1, None, 3, 0, None)
                assert handle != wintypes.HANDLE(-1).value, ctypes.get_last_error()
                try:
                    assert commit(interrupted)[0] in (400, 409)
                    assert working.read_bytes() == interrupted_bytes and source.read_bytes() == candidate
                    assert server.api(endpoint, {'path': str(alternate)}, month)[0] == 409
                    assert server.api('/api/preview', {'project': 'D006', 'method': 'water', 'entries': [{'date': month + '-10', 'batch': 'BLOCKED', 'measurement': {'result': '1'}}]}, month)[0] == 409
                finally:
                    kernel.CloseHandle(handle)
                server.stop(process)
                process = server.start(home, log)
                assert commit(interrupted)[0] == 200
                candidate = interrupted_bytes
                assert source.read_bytes() == working.read_bytes() == candidate

                # An Excel edit before preview is authoritative, even when the monthly mirror is older.
                output = io.BytesIO()
                with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
                    for name, content in server.parts(changed_copy(candidate)).items():
                        archive.writestr(name, content.replace(b'SOURCE-SYNC-01', b'SOURCE-ONLY-01'))
                source_only = output.getvalue()
                source.write_bytes(source_only)
                assert source.read_bytes() != working.read_bytes()
                authoritative = preview('SOURCE-AUTHORITATIVE-APPEND')
                assert source.read_bytes() == source_only and working.read_bytes() == candidate
                authoritative_bytes = server.ok('/api/download/' + authoritative['id'])
                assert any(b'SOURCE-ONLY-01' in data for name, data in server.parts(authoritative_bytes).items() if name.startswith('xl/worksheets/'))
                assert server.parts(authoritative_bytes)['docProps/source-smoke.xml'] == b'<external-change/>'
                assert commit(authoritative)[0] == 200
                candidate = authoritative_bytes
                assert source.read_bytes() == working.read_bytes() == candidate

                pending = preview('SOURCE-CONFLICT')
                external = changed_copy(candidate)
                source.write_bytes(external)
                assert commit(pending)[0] == 409
                assert source.read_bytes() == external and working.read_bytes() == candidate

                moved = source.with_suffix('.moved')
                source.rename(moved)
                try:
                    missing = next(p for p in server.ok('/api/catalog')['projects'] if p['code'] == 'D006')
                    assert missing['source_error'] and missing['source_link']['path'] == str(source)
                    assert server.api('/api/preview', {'project': 'D006', 'method': 'water', 'entries': [{'date': month + '-10', 'batch': 'MISSING-SOURCE', 'measurement': {'result': '1'}}]}, month)[0] in (400, 409)
                    assert moved.read_bytes() == external and working.read_bytes() == candidate
                    bind(alternate)
                    assert next(p for p in server.ok('/api/catalog')['projects'] if p['code'] == 'D006')['source_error'] is None
                    assert alternate.read_bytes() == original
                finally:
                    moved.rename(source)

                bind(source)
                stale = preview('SOURCE-STALE-BINDING')
                bind(alternate)
                assert commit(stale)[0] == 409
                assert alternate.read_bytes() == original and working.read_bytes() == candidate
                bind(None)
                unlinked = preview('SOURCE-UNLINKED')
                status, response = commit(unlinked)
                assert status == 409, (status, response)
                assert source.read_bytes() == external and alternate.read_bytes() == original

                bind(source)
                catalog = server.ok('/api/catalog')
                for code, rules in catalog['monthly_rules'].items():
                    server.ok('/api/month-rules/' + code, {'rules': rules})
                year, number = map(int, month.split('-'))
                following = f'{year + (number == 12):04d}-{number % 12 + 1:02d}'
                next_source = folder / 'D006-理化' / following[:4] / following.replace('-', '.') / ('D006-理化-' + following.replace('-', '') + '.xlsx')
                next_source.parent.mkdir(parents=True, exist_ok=True)
                next_source.write_bytes(b'existing-file-must-not-be-overwritten')
                blocked_preview = server.ok('/api/month/preview', {'month': following})
                assert not blocked_preview['can_create'] and blocked_preview['issues'] and blocked_preview['view']['rows']
                assert blocked_preview['original_view']['rows']
                assert server.api('/api/month', {'month': following, 'fingerprint': blocked_preview['fingerprint']})[0] == 409
                assert server.api('/api/month', {'month': following})[0] == 409
                assert next_source.read_bytes() == b'existing-file-must-not-be-overwritten'
                assert not (home / 'months' / following).exists()
                next_source.unlink()
                server.new_month(following)
                assert next_source.is_file()
                assert next_source.read_bytes() == (home / 'months' / following / 'workbooks/D006.xlsx').read_bytes()
                assert source.read_bytes() == external
                assert server.api(endpoint, {'path': None}, month)[0] == 409
                assert server.api(endpoint, {'path': str(source)}, following)[0] == 409
                cross_year = str(int(following[:4]) + 1) + '-01'
                next_bytes = next_source.read_bytes()
                server.new_month(cross_year)
                january_source = folder / 'D006-理化' / cross_year[:4] / cross_year.replace('-', '.') / ('D006-理化-' + cross_year.replace('-', '') + '.xlsx')
                assert january_source.read_bytes() == (home / 'months' / cross_year / 'workbooks/D006.xlsx').read_bytes()
                assert next_source.read_bytes() == next_bytes and source.read_bytes() == external
                server.stop(process)
                process = server.start(home, log)
                old_project = next(p for p in server.ok('/api/catalog', month=month)['projects'] if p['code'] == 'D006')
                assert old_project['source_link'], old_project

                # Remove means archive the imported monthly mirror, never delete the original.
                latest = server.ok('/api/catalog')
                latest_project = next(p for p in latest['projects'] if p['code'] == 'D006')
                latest_working = home / 'months' / cross_year / 'workbooks/D006.xlsx'
                before_latest = latest_working.read_bytes()
                removed_preview = server.ok('/api/preview', {'project': 'D006', 'method': 'water', 'entries': [{'date': cross_year + '-10', 'batch': 'REMOVED-PREVIEW', 'measurement': {'result': '1.234'}}]})
                rules_path = home / 'month-rules-v2.json'
                before_rules = rules_path.read_bytes()
                old_history = server.ok('/api/history', month=month)
                old_backups = {p: p.read_bytes() for p in (home / 'backups').rglob('*') if p.is_file()}
                with closing(sqlite3.connect(home / 'preview.sqlite3')) as db, db:
                    db.execute("UPDATE previews SET state='writing' WHERE id=?", [removed_preview['id']])
                assert server.api('/api/workbook/D006/remove', {})[0] == 409
                assert server.api('/api/month/preview', {'month': cross_year[:4] + '-02'})[0] == 409
                assert server.api('/api/month', {'month': cross_year[:4] + '-02'})[0] == 409
                assert server.api('/api/month-rules/D006', {'rules': latest['monthly_rules']['D006']})[0] == 409
                assert latest_working.read_bytes() == january_source.read_bytes() == before_latest
                with closing(sqlite3.connect(home / 'preview.sqlite3')) as db, db:
                    db.execute("UPDATE previews SET state='preview' WHERE id=?", [removed_preview['id']])

                # A replacement failure after mirror archival must roll back SQL and mirror.
                handle = kernel.CreateFileW(str(rules_path), 0x80000000, 1, None, 3, 0, None)
                assert handle != wintypes.HANDLE(-1).value, ctypes.get_last_error()
                try:
                    assert server.api('/api/workbook/D006/remove', {})[0] in (400, 409)
                    assert latest_working.read_bytes() == january_source.read_bytes() == before_latest
                    assert rules_path.read_bytes() == before_rules
                    with closing(sqlite3.connect(home / 'preview.sqlite3')) as db:
                        assert db.execute('SELECT state FROM previews WHERE id=?', [removed_preview['id']]).fetchone()[0] == 'preview'
                        assert db.execute('SELECT count(*) FROM source_links WHERE month=? AND project=?', [cross_year, 'D006']).fetchone()[0] == 1
                finally:
                    kernel.CloseHandle(handle)
                removed = server.ok('/api/workbook/D006/remove', {})
                archive = Path(removed['archive_path'])
                assert (archive / 'D006.xlsx').read_bytes() == before_latest
                metadata = json.loads((archive / 'project.json').read_text(encoding='utf-8'))
                assert metadata['source_link']['path'] == str(january_source) and metadata['rules']
                assert not latest_working.exists()
                assert 'D006' not in {p['code'] for p in server.ok('/api/catalog')['projects']}
                assert 'D006' not in json.loads(rules_path.read_text(encoding='utf-8'))
                assert january_source.read_bytes() == before_latest
                assert server.ok('/api/history', month=month) == old_history
                assert all(path.read_bytes() == data for path, data in old_backups.items())
                assert next(p for p in server.ok('/api/catalog', month=month)['projects'] if p['code'] == 'D006')['source_link'] == old_project['source_link']
                assert server.api('/api/workbook/D006/remove', {})[0] == 400
                server.ok('/api/workbook/D006/source-import', {'path': str(january_source), 'name': latest_project['name']})
                reimported = next(p for p in server.ok('/api/catalog')['projects'] if p['code'] == 'D006')
                assert not reimported['rules_confirmed']
                assert server.api('/api/commit/' + removed_preview['id'], {})[0] == 409
                assert latest_working.read_bytes() == january_source.read_bytes() == before_latest
                assert next(r for r in server.ok('/api/history') if r['id'] == removed_preview['id'])['state'] == 'removed'

                missing_project = next(p for p in server.ok('/api/catalog')['projects'] if p['code'] == 'IMPORTED')
                missing_source = Path(missing_project['source_link']['path'])
                missing_bytes = missing_source.read_bytes()
                missing_moved = missing_source.with_suffix('.moved')
                missing_source.rename(missing_moved)
                try:
                    removed_missing = server.ok('/api/workbook/IMPORTED/remove', {})
                    assert not any(p['code'] == 'IMPORTED' for p in server.ok('/api/catalog')['projects'])
                    assert (Path(removed_missing['archive_path']) / 'IMPORTED.xlsx').read_bytes() == missing_bytes
                    assert missing_moved.read_bytes() == missing_bytes
                finally:
                    missing_moved.rename(missing_source)
                server.ok('/api/workbook/UPLOAD_ONLY', original)
                upload_only = server.ok('/api/workbook/UPLOAD_ONLY/remove', {})
                assert (Path(upload_only['archive_path']) / 'UPLOAD_ONLY.xlsx').read_bytes() == original
                assert not any(p['code'] == 'UPLOAD_ONLY' for p in server.ok('/api/catalog')['projects'])

                # A flat workbook adopts a reviewed project/year/month tree, using its own old date.
                flat_source = folder / '平铺' / 'K001-理化-202501.xlsx'
                flat_source.parent.mkdir()
                flat_source.write_bytes(original)
                server.ok('/api/workbook/FLAT/source-import', {'path': str(flat_source), 'name': '../非法:项目名'})
                unlinked_source = folder / '另一个平铺' / '其它-2025.01.xlsx'
                unlinked_source.parent.mkdir()
                unlinked_source.write_bytes(original)
                server.ok('/api/workbook/FLAT_DOT/source-import', {'path': str(unlinked_source), 'name': '其它项目'})
                after_january = cross_year[:4] + '-02'
                snapshot = server.tree_snapshot(home)
                flat_preview = server.ok('/api/month/preview', {'month': after_january, 'project': 'FLAT'})
                target = flat_source.parent / 'K001-理化' / after_january[:4] / after_january.replace('-', '.') / ('K001-理化-' + after_january.replace('-', '') + '.xlsx')
                target_dot = unlinked_source.parent / '其它项目' / after_january[:4] / after_january.replace('-', '.') / ('其它-' + after_january.replace('-', '.') + '.xlsx')
                assert next(p['source_path'] for p in flat_preview['projects'] if p['code'] == 'FLAT') == str(target)
                assert next(p['source_path'] for p in flat_preview['projects'] if p['code'] == 'FLAT_DOT') == str(target_dot)
                assert flat_preview['can_create'] and flat_preview['view']['rows']
                after_preview = server.tree_snapshot(home)
                assert snapshot == after_preview, {name: (snapshot.get(name), after_preview.get(name)) for name in snapshot.keys() | after_preview.keys() if snapshot.get(name) != after_preview.get(name)}
                assert not target.parent.exists() and flat_source.read_bytes() == original
                target.parent.parent.parent.write_bytes(b'not-a-directory')
                ancestor_blocked = server.ok('/api/month/preview', {'month': after_january, 'project': 'FLAT'})
                assert not ancestor_blocked['can_create'] and ancestor_blocked['issues'] and ancestor_blocked['view']['rows']
                target.parent.parent.parent.unlink()
                target.parent.mkdir(parents=True)
                target.write_bytes(b'never-overwrite')
                blocked_flat = server.ok('/api/month/preview', {'month': after_january, 'project': 'FLAT'})
                assert not blocked_flat['can_create'] and blocked_flat['issues'] and blocked_flat['view']['rows']
                assert server.api('/api/month', {'month': after_january, 'fingerprint': blocked_flat['fingerprint']})[0] == 409
                assert target.read_bytes() == b'never-overwrite' and flat_source.read_bytes() == original
                target.unlink()
                reviewed = server.ok('/api/month/preview', {'month': after_january, 'project': 'FLAT'})
                server.ok('/api/month', {'month': after_january, 'fingerprint': reviewed['fingerprint']})
                assert target.read_bytes() == (home / 'months' / after_january / 'workbooks/FLAT.xlsx').read_bytes()
                assert target_dot.read_bytes() == (home / 'months' / after_january / 'workbooks/FLAT_DOT.xlsx').read_bytes()
                assert flat_source.read_bytes() == unlinked_source.read_bytes() == original
                server.verify_month(original, target.read_bytes(), server.ok('/api/catalog', month=cross_year)['monthly_rules']['FLAT'])
            except BaseException:
                log.flush()
                print((folder / 'server.log').read_text(encoding='utf-8', errors='replace'))
                raise
            finally:
                server.stop(process)
    assert fixture.read_bytes() == original
    report = {'isolated_source_copies_only': True, 'binding_no_write': True, 'unlinked_commit_rejected': True, 'legacy_preview_cannot_overwrite_original': True, 'damaged_source_preview_and_format_rejected_without_write': True, 'grid10_damaged_preview_and_incomplete_write_rejected': True, 'valid_grid10_preview_remains_compatible': True, 'grid9_preview_cannot_replay_format_damage': True, 'grid9_incomplete_write_only_finalize_when_both_files_already_saved': True, 'legacy_incomplete_write_requires_matching_saved_original': True, 'missing_original_does_not_block_reassociation': True, 'server_import_and_binding_one_step': True, 'server_import_failure_leaves_no_project_or_link': True, 'server_import_duplicate_rejected': True, 'source_is_authoritative_before_preview': True, 'source_only_previous_data_preserved': True, 'manually_entered_result_saved_to_associated_original': True, 'preview_no_write': True, 'commit_syncs_source_and_monthly_copy': True, 'source_backup_verified': True, 'retry_no_duplicate_write': True, 'interrupted_sync_recovers_and_blocks_new_writes': True, 'external_change_blocks_both_writes': True, 'binding_change_invalidates_preview': True, 'unlink_stops_source_writes': True, 'unsafe_paths_rejected': True, 'duplicate_binding_rejected': True, 'historical_month_read_only': True, 'new_month_follows_source_project_year_month_tree': True, 'cross_year_source_inheritance': True, 'existing_next_month_source_never_overwritten': True, 'old_source_preserved_on_new_month': True, 'binding_persists_restart': True, 'original_fixture_sha256': hashlib.sha256(original).hexdigest()}
    report.update({'version': latest['version'], 'executable_sha256': hashlib.sha256(server.startup.EXE.read_bytes()).hexdigest(), 'remove_project_archives_mirror_and_link_metadata': True, 'remove_project_preserves_original_history_and_backups': True, 'removed_preview_cannot_replay_after_reimport': True, 'remove_missing_original_allowed': True, 'remove_unlinked_upload_allowed': True, 'remove_failure_rolls_back_mirror_rules_and_sql': True, 'pending_write_blocks_removal': True, 'pending_write_blocks_template_preview_create_and_rules': True})
    report.update({'flat_path_preview_and_safe_month_tree': True, 'flat_source_filename_uses_its_actual_old_date': True, 'illegal_project_label_does_not_escape_target_tree': True, 'blocked_destination_still_renders_preview': True, 'blocked_ancestor_file_still_renders_preview': True, 'automatic_rules_create_without_saved_confirmations': True})
    report['limits'] = ['Preview snapshots include business files, rules, SQLite database and WAL; exclude the running lock and SQLite shared-memory reader marks.']
    (ROOT.parent / 'runtime/source-smoke-result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
