"""Verify source writeback using disposable copies, never original workbooks."""
import hashlib
import ctypes
from ctypes import wintypes
import io
import json
import socket
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

                pending = preview('SOURCE-CONFLICT')
                external = changed_copy(candidate)
                source.write_bytes(external)
                assert commit(pending)[0] == 409
                assert source.read_bytes() == external and working.read_bytes() == candidate

                bind(source)
                stale = preview('SOURCE-STALE-BINDING')
                bind(alternate)
                assert commit(stale)[0] == 409
                assert alternate.read_bytes() == original and working.read_bytes() == candidate
                bind(None)
                unlinked = preview('SOURCE-UNLINKED')
                status, response = commit(unlinked)
                assert status == 200 and response['source_sync']['state'] == 'unlinked', (status, response)
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
                assert server.api('/api/month', {'month': following})[0] == 409
                assert next_source.read_bytes() == b'existing-file-must-not-be-overwritten'
                assert not (home / 'months' / following).exists()
                next_source.unlink()
                server.ok('/api/month', {'month': following})
                assert next_source.is_file()
                assert next_source.read_bytes() == (home / 'months' / following / 'workbooks/D006.xlsx').read_bytes()
                assert source.read_bytes() == external
                assert server.api(endpoint, {'path': None}, month)[0] == 409
                assert server.api(endpoint, {'path': str(source)}, following)[0] == 409
                cross_year = str(int(following[:4]) + 1) + '-01'
                next_bytes = next_source.read_bytes()
                server.ok('/api/month', {'month': cross_year})
                january_source = folder / 'D006-理化' / cross_year[:4] / cross_year.replace('-', '.') / ('D006-理化-' + cross_year.replace('-', '') + '.xlsx')
                assert january_source.read_bytes() == (home / 'months' / cross_year / 'workbooks/D006.xlsx').read_bytes()
                assert next_source.read_bytes() == next_bytes and source.read_bytes() == external
                server.stop(process)
                process = server.start(home, log)
                old_project = next(p for p in server.ok('/api/catalog', month=month)['projects'] if p['code'] == 'D006')
                assert old_project['source_link'], old_project
            except BaseException:
                log.flush()
                print((folder / 'server.log').read_text(encoding='utf-8', errors='replace'))
                raise
            finally:
                server.stop(process)
    assert fixture.read_bytes() == original
    report = {'isolated_source_copies_only': True, 'binding_no_write': True, 'preview_no_write': True, 'commit_syncs_source_and_monthly_copy': True, 'source_backup_verified': True, 'retry_no_duplicate_write': True, 'interrupted_sync_recovers_and_blocks_new_writes': True, 'external_change_blocks_both_writes': True, 'binding_change_invalidates_preview': True, 'unlink_stops_source_writes': True, 'unsafe_paths_rejected': True, 'duplicate_binding_rejected': True, 'historical_month_read_only': True, 'new_month_follows_source_project_year_month_tree': True, 'cross_year_source_inheritance': True, 'existing_next_month_source_never_overwritten': True, 'old_source_preserved_on_new_month': True, 'binding_persists_restart': True, 'original_fixture_sha256': hashlib.sha256(original).hexdigest()}
    (ROOT.parent / 'runtime/source-smoke-result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
