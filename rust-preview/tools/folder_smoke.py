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
from urllib.parse import quote
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

                def bind(plan, selected=None):
                    request = {'path': plan['root'], 'fingerprint': plan['fingerprint']}
                    if selected is not None:
                        request['selected'] = selected
                    return server.api('/api/source-folder/bind', request)

                def browse(path=None):
                    return server.ok('/api/source-folder/browse' + (f'?path={quote(str(path))}' if path is not None else ''))

                drives = browse()
                assert drives['roots'] and drives['path'] is drives['parent'] is None
                assert drives['directories'] and all(entry['selectable'] for entry in drives['directories'])
                tree = browse(base)
                assert tree['path'] == str(base) and tree['parent'] == str(base.parent)
                assert str(root) in {entry['path'] for entry in tree['directories']}
                assert str(home) not in {entry['path'] for entry in tree['directories']}
                assert all('xlsx' not in entry['name'] for entry in browse(root)['directories'])
                assert server.api('/api/source-folder/browse?path=' + quote(str(home)))[0] == 400
                for invalid in ['relative', r'\\server\share', str(base / 'missing')]:
                    assert server.api('/api/source-folder/browse?path=' + quote(invalid))[0] == 400
                checks['server_directory_browser_local_roots_children_parent_and_data_exclusion'] = True

                breadth = base / 'many-directories'
                breadth.mkdir()
                for number in range(501):
                    (breadth / f'dir-{number:03}').mkdir()
                limited = browse(breadth)
                assert limited['truncated'] and len(limited['directories']) == 500
                checks['directory_browser_limit_is_visible_not_silent'] = True

                flexible = base / '灵活目录'
                flexible_sources = []
                for relative in ['分类/AA-理化/2026/8月/AA-理化-202608.xlsx', 'BB-理化/附加/2026.09/BB-理化-2026.09.xlsx', 'CC-理化2026年10月.xlsx']:
                    path = flexible / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(original)
                    flexible_sources.append(path)
                for relative in ['坏项目/2026/7月/坏项目-202608.xlsx', '分类/无法确定/2026/7月/原表.xlsx', '没有年月.xlsx']:
                    path = flexible / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(original)
                flexible_plan = server.ok('/api/source-folder/scan', {'path': str(flexible)})
                valid = [entry for entry in flexible_plan['entries'] if entry['selectable']]
                assert {(entry['project'], entry['month']) for entry in valid} == {('AA', '2026-08'), ('BB', '2026-09'), ('CC', '2026-10')}, flexible_plan
                assert sum(not entry['selectable'] for entry in flexible_plan['entries']) == 3
                for selected_root in [flexible / '分类/AA-理化', flexible / '分类/AA-理化/2026', flexible / '分类/AA-理化/2026/8月']:
                    single = server.ok('/api/source-folder/scan', {'path': str(selected_root)})
                    assert len(single['entries']) == 1 and single['entries'][0]['project'] == 'AA' and single['entries'][0]['month'] == '2026-08', single
                checks['flexible_nested_directories_filename_dates_and_selected_project_year_month_roots'] = True
                checks['conflicting_dates_and_ambiguous_projects_never_guessed'] = True

                limits = base / 'limits'
                limits.mkdir()
                for number in range(1001):
                    (limits / f'file-{number:04}.xlsx').write_bytes(b'count-only')
                assert server.api('/api/source-folder/scan', {'path': str(limits)})[0] == 400
                for path in limits.iterdir():
                    path.unlink()
                oversized = limits / 'EF-202607.xlsx'
                with oversized.open('wb') as output:
                    output.truncate(128 * 1024 * 1024 + 1)
                assert server.api('/api/source-folder/scan', {'path': str(limits)})[0] == 400
                oversized.unlink()
                deep = limits.joinpath(*[f'level-{number}' for number in range(9)], 'EF-202607.xlsx')
                deep.parent.mkdir(parents=True)
                deep.write_bytes(original)
                depth_plan = server.ok('/api/source-folder/scan', {'path': str(limits)})
                assert not depth_plan['entries'] and depth_plan['issues'] and not depth_plan['can_bind']
                assert deep.read_bytes() == original
                checks['recursive_depth_and_scan_file_count_size_limits_enforced'] = True

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
                duplicate_entries = [entry for entry in ambiguous['entries'] if entry['project'] == 'EF' and entry['month'] == '2026-01']
                assert ambiguous['can_bind'] and len(duplicate_entries) == 2
                assert all(entry['selectable'] and not entry['selected'] for entry in duplicate_entries)
                assert bind(ambiguous, [entry['id'] for entry in ambiguous['entries']])[0] == 409
                assert bind(ambiguous, [entry['id'] for entry in duplicate_entries])[0] == 409
                assert bind(ambiguous, [duplicate_entries[0]['id']] * 2)[0] == 409
                duplicate.unlink()
                sources.remove(duplicate)
                checks['duplicate_project_month_requires_single_explicit_choice'] = True

                collision = fixture('EF-另一项目', '2026', '01')
                collided = scan()
                assert collided['can_bind'] and any(not entry['selectable'] and entry['path'] == str(collision) for entry in collided['entries'])
                collision.unlink()
                sources.remove(collision)
                checks['project_code_collision_not_guessed'] = True

                unexpected = root / '不在年份月份内.xlsx'
                unexpected.write_bytes(original)
                misplaced = scan()
                assert misplaced['can_bind']
                invalid = next(entry for entry in misplaced['entries'] if entry['path'] == str(unexpected))
                assert not invalid['selectable'] and not invalid['selected']
                assert bind(misplaced, [invalid['id']])[0] == 409
                assert bind(misplaced, [])[0] == 409
                assert bind(misplaced, ['unknown-id'])[0] == 409
                checks['unrecognized_candidates_skippable_without_importing'] = True
                checks['empty_unknown_unselectable_and_duplicate_ids_rejected'] = True

                outside = base / 'outside'
                outside.mkdir()
                (outside / 'outside.xlsx').write_bytes(original)
                junction = root / 'junction'
                created = subprocess.run([os.environ.get('COMSPEC', r'C:\Windows\System32\cmd.exe'), '/d', '/c', 'mklink', '/J', str(junction), str(outside)], capture_output=True)
                assert created.returncode == 0, created.stdout + created.stderr
                try:
                    reparsed = scan()
                    assert reparsed['can_bind'] and reparsed['issues']
                    assert str(junction) not in {entry['path'] for entry in browse(root)['directories']}
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
                assert result['unselected'] == 1 and unexpected.read_bytes() == original
                unexpected.unlink()
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
                assert unsafe_move['can_bind'] and bind(unsafe_move, [entry['id'] for entry in unsafe_move['entries']])[0] == 409
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
                checks['selected_changed_moved_file_rejected_without_partial_reassociation'] = True
                checks['confirmed_migration_only_changes_links_not_mirrors_or_originals'] = True

                # A pre-existing single-file association must not be silently replaced.
                other = base / 'different.xlsx'
                other.write_bytes(original)
                server.ok('/api/workbook/EF/source', {'path': str(other)}, '2026-01')
                ef.write_bytes(original + b'changed-other-source')
                conflict = scan()
                assert conflict['can_bind'] and any(entry['project'] == 'EF' and entry['month'] == '2026-01' and entry['state'] == 'conflict' for entry in conflict['entries'])
                assert bind(conflict, [entry['id'] for entry in conflict['entries']])[0] == 409
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

                for invalid in ['relative', r'\\server\share', str(home), str(base), str(base / 'missing')]:
                    assert server.api('/api/source-folder/scan', {'path': invalid})[0] == 400, invalid
                checks['relative_network_platform_and_missing_roots_rejected'] = True

                server.stop(process)
                process = server.start(home, log)
                assert server.ok('/api/catalog')['source_folder'] == str(root)
                assert len(server.ok('/api/catalog')['projects']) == 3
                checks['restart_retains_root_and_associations'] = True

                # Explicit choices never import the other valid or unrecognized candidates.
                flexible_plan = server.ok('/api/source-folder/scan', {'path': str(flexible)})
                chosen = next(entry for entry in flexible_plan['entries'] if entry['project'] == 'AA')
                status, selected_result = bind(flexible_plan, [chosen['id']])
                assert status == 200 and selected_result['imported'] == 1 and selected_result['unselected'] == 5
                with closing(sqlite3.connect(home / 'preview.sqlite3')) as db:
                    assert db.execute("SELECT count(*) FROM source_links WHERE project IN ('AA','BB','CC')").fetchone()[0] == 1
                    assert db.execute("SELECT path FROM source_links WHERE project='AA'").fetchone()[0] == chosen['path']
                assert not (home / 'months/2026-09/workbooks/BB.xlsx').exists()
                assert not (home / 'months/2026-10/workbooks/CC.xlsx').exists()
                assert all(path.read_bytes() == original for path in flexible_sources)
                checks['selected_subset_only_imported'] = True
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
