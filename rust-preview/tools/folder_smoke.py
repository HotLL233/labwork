"""Verify total-folder discovery/association using disposable copies only."""
from contextlib import closing
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import month_smoke as server

ROOT = Path(__file__).resolve().parents[1]


def main():
    original = (ROOT.parent / 'runtime/rust-preview/workbooks/D006.xlsx').read_bytes()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        server.PORT = probe.getsockname()[1]
    checks = {}
    with tempfile.TemporaryDirectory(prefix='labwork-folder-') as directory:
        base = Path(directory)
        home, root = base / 'data', base / '全部项目'
        sources = []

        def fixture(project, year, month, filename='原表.xlsx'):
            path = root / project / year / month / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(original)
            sources.append(path)
            return path

        fixture('EF-理化', '2025年', '12月')
        ef = fixture('EF-理化', '2026年', '2026.01')
        fixture('D006-理化', '2026', '01')
        fixture('中文项目', '2026', '2026-01')
        fixture('忽略备份', '2026', '01')
        lock = root / 'EF-理化' / '2026年' / '2026.01' / '~$原表.xlsx'
        lock.write_bytes(b'Excel lock, not an XLSX')
        process = None
        with (base / 'server.log').open('wb') as log:
            try:
                process = server.start(home, log)
                initial = server.ok('/api/catalog')
                initial_month = initial['active_month']

                def scan():
                    return server.ok('/api/source-folder/scan', {'path': str(root)})

                def bind(plan):
                    return server.api('/api/source-folder/bind', {'path': str(root), 'fingerprint': plan['fingerprint']})

                before = server.tree_snapshot(home)
                plan = scan()
                assert server.tree_snapshot(home) == before, 'Scan must not create mirrors, names, or links'
                assert plan['can_bind'] and len(plan['entries']) == 4, plan
                assert {entry['project'] for entry in plan['entries']} >= {'EF', 'D006'}
                assert {entry['month'] for entry in plan['entries']} == {'2025-12', '2026-01'}
                assert plan['latest_month'] == '2026-01'
                checks['multiple_projects_chinese_names_cross_year_and_month_formats'] = True
                checks['scan_has_no_data_writes_and_ignores_locks_backups'] = True

                stale = plan
                ef.write_bytes(original + b'changed')
                assert bind(stale)[0] == 409
                assert server.ok('/api/catalog')['projects'] == []
                ef.write_bytes(original)
                checks['changed_source_invalidates_confirmation'] = True

                duplicate = fixture('EF-理化', '2026年', '2026.01', '另一表.xlsx')
                ambiguous = scan()
                assert not ambiguous['can_bind']
                assert sum(entry['state'] == 'conflict' for entry in ambiguous['entries']) == 2
                assert bind(ambiguous)[0] == 409
                duplicate.unlink()
                sources.remove(duplicate)
                checks['same_project_month_multiple_files_rejected_without_partial_import'] = True

                collision = fixture('EF-另一项目', '2026', '01')
                assert not scan()['can_bind']
                collision.unlink()
                sources.remove(collision)
                checks['project_code_collision_not_guessed'] = True

                unexpected = root / '不在年份月份内.xlsx'
                unexpected.write_bytes(original)
                misplaced = scan()
                assert not misplaced['can_bind']
                assert any(entry['path'] == str(unexpected) and entry['state'] == 'conflict' for entry in misplaced['entries'])
                unexpected.unlink()
                checks['misplaced_workbooks_reported_instead_of_silently_skipped'] = True

                outside = base / 'outside'
                outside.mkdir()
                (outside / 'outside.xlsx').write_bytes(original)
                junction = root / 'junction'
                created = subprocess.run([os.environ.get('COMSPEC', r'C:\Windows\System32\cmd.exe'), '/d', '/c', 'mklink', '/J', str(junction), str(outside)], capture_output=True)
                assert created.returncode == 0, created.stdout + created.stderr
                try:
                    reparsed = scan()
                    assert not reparsed['can_bind'] and reparsed['issues']
                    assert server.api('/api/source-folder/scan', {'path': str(junction)})[0] == 400
                    assert (outside / 'outside.xlsx').read_bytes() == original
                finally:
                    junction.rmdir()
                checks['directory_junctions_and_reparsed_roots_rejected'] = True

                # Deny replacement of the names file after mirrors have been staged/published.
                names = home / 'project-names.json'
                names.write_text('{}', encoding='utf-8')
                plan = scan()
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
                kernel.CreateFileW.restype = wintypes.HANDLE
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                handle = kernel.CreateFileW(str(names), 0x80000000, 1, None, 3, 0, None)
                assert handle != wintypes.HANDLE(-1).value, ctypes.get_last_error()
                try:
                    status, response = bind(plan)
                    assert status in (400, 409), (status, response)
                finally:
                    kernel.CloseHandle(handle)
                assert server.ok('/api/catalog')['months'] == [initial_month]
                assert not list((home / 'months').rglob('*.xlsx'))
                with closing(sqlite3.connect(home / 'preview.sqlite3')) as db:
                    assert db.execute('SELECT count(*) FROM source_links').fetchone()[0] == 0
                assert server.ok('/api/catalog')['source_folder'] is None
                checks['mid_batch_failure_rolls_back_files_links_names_and_empty_month'] = True

                plan = scan()
                status, result = bind(plan)
                assert status == 200, (status, result)
                assert result['imported'] == 4 and result['linked'] == result['skipped'] == 0
                assert result['month'] == '2026-01'
                assert result['archived_empty_month'] == initial_month
                assert Path(result['empty_month_backup']).is_dir()
                catalog = server.ok('/api/catalog')
                assert catalog['source_folder'] == str(root)
                assert catalog['months'] == ['2025-12', '2026-01'] and catalog['writable']
                assert len(catalog['projects']) == 3
                assert len(server.ok('/api/catalog', month='2025-12')['projects']) == 1
                assert not server.ok('/api/catalog', month='2025-12')['writable']
                checks['associations_saved_latest_source_month_writable_old_month_readonly'] = True
                checks['unused_bootstrap_month_archived_without_deleting_history'] = True

                before = {str(p): p.read_bytes() for p in (home / 'months').rglob('*.xlsx')}
                assert len(before) == 4 and all(data == original for data in before.values())
                checks['initial_mirrors_exactly_match_original_bytes'] = True
                repeated = scan()
                assert repeated['can_bind'] and all(entry['state'] == 'linked' for entry in repeated['entries'])
                status, result = bind(repeated)
                assert status == 200 and result['skipped'] == 4 and result['imported'] == result['linked'] == 0
                assert before == {str(p): p.read_bytes() for p in (home / 'months').rglob('*.xlsx')}
                checks['repeated_scan_bind_idempotent_and_no_duplicate_mirrors'] = True

                old_root = root
                moved_root = base / '搬迁后的全部项目'
                root.rename(moved_root)
                sources = [moved_root / path.relative_to(old_root) for path in sources]
                ef = moved_root / ef.relative_to(old_root)
                lock = moved_root / lock.relative_to(old_root)
                root = moved_root
                old_project = root / 'EF-理化'
                new_project = root / 'EF-移动后名称'
                old_project.rename(new_project)
                sources = [new_project / path.relative_to(old_project) if path.is_relative_to(old_project) else path for path in sources]
                ef = new_project / ef.relative_to(old_project)
                lock = new_project / lock.relative_to(old_project)
                ef.write_bytes(original + b'different-moved-file')
                unsafe_move = scan()
                assert not unsafe_move['can_bind'] and bind(unsafe_move)[0] == 409
                with closing(sqlite3.connect(home / 'preview.sqlite3')) as db:
                    assert all(str(old_root) in row[0] for row in db.execute('SELECT path FROM source_links'))
                assert before == {str(p): p.read_bytes() for p in (home / 'months').rglob('*.xlsx')}
                ef.write_bytes(original)
                move = scan()
                assert move['can_bind'] and len(move['entries']) == 4, move
                assert all(entry['state'] == 'relink' and entry['previous_path'] for entry in move['entries'])
                assert {entry['project'] for entry in move['entries']} == {entry['project'] for entry in repeated['entries']}
                status, result = bind(move)
                assert status == 200 and result['relinked'] == result['linked'] == 4 and result['imported'] == result['skipped'] == 0
                assert before == {str(p): p.read_bytes() for p in (home / 'months').rglob('*.xlsx')}
                moved_again = scan()
                assert moved_again['can_bind'] and all(entry['state'] == 'linked' for entry in moved_again['entries'])
                checks['moved_root_and_renamed_folder_reuse_existing_projects_after_hash_and_structure_check'] = True
                checks['changed_moved_file_rejected_without_partial_reassociation'] = True
                checks['confirmed_migration_only_changes_links_not_mirrors_or_originals'] = True

                # A pre-existing single-file association must not be silently replaced.
                other = base / 'different.xlsx'
                other.write_bytes(original)
                server.ok('/api/workbook/EF/source', {'path': str(other)}, '2026-01')
                ef.write_bytes(original + b'changed-other-source')
                conflict = scan()
                assert not conflict['can_bind'] and any(entry['project'] == 'EF' and entry['month'] == '2026-01' and entry['state'] == 'conflict' for entry in conflict['entries'])
                assert bind(conflict)[0] == 409
                ef.write_bytes(original)
                server.ok('/api/workbook/EF/source', {'path': str(ef)}, '2026-01')
                checks['existing_different_source_link_not_replaced'] = True

                # Existing, unlinked month mirrors can be associated without replacing data.
                server.ok('/api/workbook/EF/source', {'path': None}, '2026-01')
                plan = scan()
                assert next(entry for entry in plan['entries'] if entry['project'] == 'EF' and entry['month'] == '2026-01')['state'] == 'link'
                status, result = bind(plan)
                assert status == 200 and result['linked'] == 1 and result['skipped'] == 3
                assert before == {str(p): p.read_bytes() for p in (home / 'months').rglob('*.xlsx')}
                checks['existing_unlinked_mirror_associated_without_overwrite'] = True

                with closing(sqlite3.connect(home / 'preview.sqlite3')) as db, db:
                    db.execute("INSERT INTO previews VALUES('folder-pending',0,?,'[]','','','[]','writing','{}')", [json.dumps({'month': '2026-01', 'project': 'EF'})])
                pending = scan()
                assert not pending['can_bind'] and pending['issues']
                assert bind(pending)[0] == 409
                with closing(sqlite3.connect(home / 'preview.sqlite3')) as db, db:
                    db.execute("DELETE FROM previews WHERE id='folder-pending'")
                checks['unfinished_writes_block_batch_association'] = True

                for invalid in ['relative', r'\\server\share', str(home), str(base / 'missing')]:
                    assert server.api('/api/source-folder/scan', {'path': invalid})[0] == 400, invalid
                checks['relative_network_platform_and_missing_roots_rejected'] = True

                server.stop(process)
                process = server.start(home, log)
                assert server.ok('/api/catalog')['source_folder'] == str(root)
                assert len(server.ok('/api/catalog')['projects']) == 3
                checks['restart_retains_root_and_associations'] = True
                assert all(path.read_bytes() == original for path in sources)
                assert lock.read_bytes() == b'Excel lock, not an XLSX'
                checks['all_original_workbooks_byte_for_byte_unchanged'] = True
                version = server.ok('/api/catalog')['version']
            except BaseException:
                log.flush()
                print((base / 'server.log').read_text(encoding='utf-8', errors='replace'))
                raise
            finally:
                server.stop(process)
    result = {'ok': True, 'version': version, 'executable_sha256': hashlib.sha256(server.startup.EXE.read_bytes()).hexdigest(), **checks}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    (ROOT.parent / 'runtime/folder-smoke-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
