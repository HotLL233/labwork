"""Package the tested local Rust validation build, without business files."""
import hashlib
import json
import re
import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

root=Path(__file__).resolve().parents[1]
project=tomllib.loads((root/'Cargo.toml').read_text(encoding='utf-8'))['package']
if project['name']!='labwork':raise RuntimeError('Package project name must be labwork')
version=project['version']
if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)',version):raise RuntimeError('Release version must contain only three numeric components')
repo=root.parent
release=Path('C:/labwork/releases')/f'labwork-v{version}'
if release.exists():raise RuntimeError('Release already exists; do not overwrite published files')
exe=root/'target/release/labwork.exe'
exe_sha256=hashlib.sha256(exe.read_bytes()).hexdigest()
checks=json.loads((repo/'runtime/rust-smoke-result.json').read_text(encoding='utf-8'))
if checks.get('methods')!=14 or checks.get('method_views_show_all_rows') is not True or not all(checks.values()):raise RuntimeError('File regression did not pass')
monthly=json.loads((repo/'runtime/month-smoke-result.json').read_text(encoding='utf-8'))
if not monthly or not all(monthly.values()):raise RuntimeError('Month regression did not pass')
if not all(monthly.get(key) is True for key in ('new_month_preview_no_filesystem_writes','preview_uses_actual_blank_template_and_pagination','new_month_confirmation_requires_current_fingerprint','source_rules_catalog_changes_invalidate_month_preview')):raise RuntimeError('Month template preview regression did not pass')
if not all(monthly.get(key) is True for key in ('no_manual_rule_confirm_required','automatic_template_rules_used','unknown_sheets_preserved_by_default','adjustments_clear_keep_and_fingerprint_checked','original_and_candidate_same_page_visible','fingerprint_required_atomic_month_creation')):raise RuntimeError('Automatic month template regression did not pass')
unsupported_formula=json.loads((repo/'runtime/unsupported-formula-preservation.json').read_text(encoding='utf-8'))
if (unsupported_formula.get('version')!=version or unsupported_formula.get('executable_sha256')!=exe_sha256
    or unsupported_formula.get('unsupported_formula_sheets_preserved_by_default') is not True
    or unsupported_formula.get('explicit_input_clear_preserves_round_formula_and_fixed_parameter') is not True):
    raise RuntimeError('Unsupported formula template preservation regression did not pass for this executable')
installer_settings=json.loads((repo/'runtime/installer-settings-regression.json').read_text(encoding='utf-8'))
installer_firewall=json.loads((repo/'runtime/installer-firewall-regression.json').read_text(encoding='utf-8'))
for report,helper,required in (
    (installer_settings,'installer-settings.ps1',('inherited_password_exact','password_not_logged_or_in_arguments','different_data_requires_new_password','malformed_old_config_rejected','same_version_write_preserves_old_on_failure')),
    (installer_firewall,'installer-firewall.ps1',('stable_port_rule','subnet_only','existing_rule_no_elevation','canceled_permission_not_success','removed_only_owned_rule','actual_parameter_set_validation')),
):
    if (report.get('ok') is not True or report.get('version')!=version or report.get('executable_sha256')!=exe_sha256
        or report.get('helper_sha256')!=hashlib.sha256((root/'packaging'/helper).read_bytes()).hexdigest()
        or not all(report.get(key) is True for key in required)):
        raise RuntimeError(f'Installer helper regression did not pass for the release source: {helper}')
auth=json.loads((repo/'runtime/auth-smoke-result.json').read_text(encoding='utf-8'))
if not auth or not all(auth.values()):raise RuntimeError('Login persistence regression did not pass')
formula=json.loads((repo/'runtime/formula-smoke-result.json').read_text(encoding='utf-8'))
if not formula.get('cases') or not all(formula.values()) or formula.get('calculated_results_saved_to_associated_original') is not True:raise RuntimeError('Workbook formula regression did not pass')
imports=json.loads((repo/'runtime/import-smoke-result.json').read_text(encoding='utf-8'))
if not imports or not all(imports.values()):raise RuntimeError('Workbook upload validation regression did not pass')
lan=json.loads((repo/'runtime/lan-smoke-result.json').read_text(encoding='utf-8'))
if lan.get('ok') is not True or lan.get('version')!=version:raise RuntimeError('LAN access regression did not pass for this version')
startup=json.loads((repo/'runtime/startup-smoke-result.json').read_text(encoding='utf-8'))
if not startup or not all(startup.values()):raise RuntimeError('Startup regression did not pass')
format_checks=json.loads((repo/'runtime/format-smoke-result.json').read_text(encoding='utf-8'))
if not format_checks or not all(format_checks.values()) or format_checks.get('explicit_date_format_converts_compact_date_for_excel_1900_and_1904') is not True:raise RuntimeError('Workbook format regression did not pass')
source_checks=json.loads((repo/'runtime/source-smoke-result.json').read_text(encoding='utf-8'))
cell_edits=json.loads((repo/'runtime/cell-edit-smoke-result.json').read_text(encoding='utf-8'))
if (cell_edits.get('ok') is not True or cell_edits.get('version')!=version or cell_edits.get('executable_sha256')!=exe_sha256
    or not all(cell_edits.get(key) is True for key in ('preview_does_not_write_source_or_mirror', 'direct_edit_source_and_mirror_confirmed', 'layout_styles_and_formulas_preserved', 'formula_and_shared_formula_readonly', 'merged_nonanchor_rejected', 'protected_cells_rejected', 'leading_zero_batch_preserved', 'formula_like_text_is_literal', 'zero_recalculates_supported_formulas', 'unknown_formula_cache_invalidated', 'date_1900_1904_and_iso_types_preserved', 'draft_repreview_and_revert_no_write', 'calculation_preview_cells_amendable', 'source_and_mirror_conflicts_rejected', 'stale_or_other_project_preview_rejected', 'validation_limits_rejected', 'write_backup_and_idempotent_confirmation', 'source_lock_recovers_through_history', 'pending_write_blocks_new_edits', 'history_month_readonly'))):
    raise RuntimeError('Cell editing regression did not pass for this executable')
folder_checks=json.loads((repo/'runtime/folder-smoke-result.json').read_text(encoding='utf-8'))
if (folder_checks.get('ok') is not True or folder_checks.get('version')!=version
    or folder_checks.get('executable_sha256')!=exe_sha256
    or not all(folder_checks.get(key) is True for key in (
        'server_directory_browser_local_roots_children_parent_and_data_exclusion',
        'directory_browser_limit_is_visible_not_silent',
        'recursive_depth_and_scan_file_count_size_limits_enforced',
        'flexible_nested_directories_filename_dates_and_selected_project_year_month_roots',
        'conflicting_dates_and_ambiguous_projects_never_guessed',
        'empty_unknown_unselectable_and_duplicate_ids_rejected',
        'selected_subset_only_imported',
        'multiple_projects_chinese_names_cross_year_and_month_formats',
        'scan_has_no_data_writes_and_ignores_locks_backups',
        'changed_source_invalidates_confirmation',
        'duplicate_project_month_requires_single_explicit_choice',
        'project_code_collision_not_guessed',
        'unrecognized_candidates_skippable_without_importing',
        'directory_junctions_and_reparsed_roots_rejected',
        'mid_batch_failure_rolls_back_files_links_names_and_empty_month',
        'associations_saved_latest_source_month_writable_old_month_readonly',
        'unused_bootstrap_month_archived_without_deleting_history',
        'initial_mirrors_exactly_match_original_bytes',
        'repeated_scan_bind_idempotent_and_no_duplicate_mirrors',
        'moved_root_and_renamed_folder_reuse_existing_projects_after_hash_and_structure_check',
        'selected_changed_moved_file_rejected_without_partial_reassociation',
        'confirmed_migration_only_changes_links_not_mirrors_or_originals',
        'existing_different_source_link_not_replaced',
        'existing_unlinked_mirror_associated_without_overwrite',
        'unfinished_writes_block_batch_association',
        'relative_network_platform_and_missing_roots_rejected',
        'restart_retains_root_and_associations',
        'all_original_workbooks_byte_for_byte_unchanged'))):
    raise RuntimeError('Total-folder association regression did not pass for this executable')
if (not source_checks or not all(source_checks.values())
    or source_checks.get('commit_syncs_source_and_monthly_copy') is not True
    or source_checks.get('unlinked_commit_rejected') is not True
    or source_checks.get('server_import_and_binding_one_step') is not True
    or source_checks.get('server_import_failure_leaves_no_project_or_link') is not True
    or source_checks.get('source_only_previous_data_preserved') is not True
    or source_checks.get('damaged_source_preview_and_format_rejected_without_write') is not True
    or source_checks.get('grid10_damaged_preview_and_incomplete_write_rejected') is not True
    or source_checks.get('valid_grid10_preview_remains_compatible') is not True
    or source_checks.get('grid9_preview_cannot_replay_format_damage') is not True
    or source_checks.get('grid9_incomplete_write_only_finalize_when_both_files_already_saved') is not True
    or source_checks.get('cross_year_source_inheritance') is not True
    or source_checks.get('existing_next_month_source_never_overwritten') is not True):
    raise RuntimeError('Source synchronization regression did not pass')
if not all(source_checks.get(key) is True for key in ('remove_project_archives_mirror_and_link_metadata','remove_project_preserves_original_history_and_backups','removed_preview_cannot_replay_after_reimport','remove_missing_original_allowed','pending_write_blocks_removal','remove_failure_rolls_back_mirror_rules_and_sql','remove_unlinked_upload_allowed','pending_write_blocks_template_preview_create_and_rules')):raise RuntimeError('Project removal and retained data regression did not pass')
if not all(source_checks.get(key) is True for key in ('flat_path_preview_and_safe_month_tree','flat_source_filename_uses_its_actual_old_date','illegal_project_label_does_not_escape_target_tree','blocked_destination_still_renders_preview','blocked_ancestor_file_still_renders_preview','automatic_rules_create_without_saved_confirmations')):raise RuntimeError('Automatic source template and destination regression did not pass')
preserved=json.loads((repo/'runtime/preserve-format-smoke-result.json').read_text(encoding='utf-8'))
if preserved.get('ok') is not True or preserved.get('version')!=version:raise RuntimeError('Original workbook layout regression did not pass for this version')
original_layout=json.loads((repo/'runtime/original-format-preservation.json').read_text(encoding='utf-8'))
if (original_layout.get('version')!=version or original_layout.get('executable_sha256')!=exe_sha256
    or original_layout.get('workbooks')!=12 or original_layout.get('method_previews')!=138 or original_layout.get('isolated_source_commits')!=12
    or original_layout.get('business_originals_hash_unchanged') is not True or not original_layout.get('checks')
    or not all(original_layout['checks'].values()) or original_layout.get('failures')!=[] or original_layout.get('api_errors')!=[]):
    raise RuntimeError('All-original layout preservation regression did not pass for this executable')
native_excel=json.loads((repo/'runtime/native-excel-layout.json').read_text(encoding='utf-8'))
if (native_excel.get('ok') is not True or native_excel.get('version')!=version
    or native_excel.get('executable_sha256')!=exe_sha256
    or native_excel.get('normal_open_no_repair') is not True
    or native_excel.get('fixed_column_widths_preserved') is not True
    or native_excel.get('frozen_panes_preserved') is not True):
    raise RuntimeError('Native Excel open and original layout regression did not pass for this executable')
native_blank=json.loads((repo/'runtime/native-excel-blank-calculations.json').read_text(encoding='utf-8'))
if (native_blank.get('ok') is not True or native_blank.get('version')!=version
    or native_blank.get('executable_sha256')!=exe_sha256
    or not all(native_blank.get(key) is True for key in (
        'blank_formula_results_empty','filled_inputs_recalculate','zero_input_valid',
        'true_division_by_zero_visible','source_hashes_unchanged','owned_excel_exited',
        'repeated_month_guards_not_nested','formula_arithmetic_preserved',
        'example_and_fixed_cells_preserved','keep_formula_cache_preserved',
        'unknown_formula_preserved','methods_calculate_and_commit','metadata_preserved','copies_only'))):
    raise RuntimeError('Native Excel blank calculation regression did not pass for this executable')
native_edits=json.loads((repo/'runtime/native-excel-cell-edits.json').read_text(encoding='utf-8'))
if native_edits.get('native_edit_layout_preserved') is not True:raise RuntimeError('Native Excel edited workbook dimensions and frozen panes changed')
if (native_edits.get('ok') is not True or native_edits.get('version')!=version or native_edits.get('executable_sha256')!=exe_sha256
    or not all(native_edits.get(key) is True for key in ('copies_only', 'normal_open_no_repair', 'source_hashes_unchanged', 'business_originals_hash_unchanged', 'owned_excel_exited', 'cell_styles_preserved', 'styles_xml_byte_preserved', 'sheet_metadata_preserved', 'existing_row_attributes_preserved', 'only_target_or_required_calculation_parts_changed', 'direct_edit_commit_matches_preview', 'candidate_edit_commit_matches_preview', 'date_native_display_correct', 'leading_zero_batch_preserved', 'supported_formula_cache_recalculated', 'unknown_formula_cache_cleared_with_warning', 'formula_and_protected_cells_rejected', 'merged_non_anchor_rejected'))):
    raise RuntimeError('Native Excel cell editing regression did not pass for this executable')
ui_directory=repo/'runtime/ui-qa/v0.0.9'
if any(path.suffix.lower() in ('.xlsx','.sqlite3') for path in ui_directory.rglob('*') if path.is_file()):raise RuntimeError('Browser evidence contains business files or a database')
ui=json.loads((ui_directory/'result.json').read_text(encoding='utf-8'))
if ui.get('ok') is not True or ui.get('version')!=version or ui.get('executable_sha256')!=exe_sha256 or ui.get('manual_source_write') is not True:raise RuntimeError('Browser regression did not pass for this executable')
if not set(('unlink_retains_project_source','remove_import_retains_source_history','remove_last_empty_catalog','month_preview_actual_blank_headers_formulas','month_preview_cancel_no_files','month_preview_confirm','month_preview_responsive','sheet_rows_continuous_after_preview_commit_format','compact_header_preserves_visible_entry_actions_and_target')).issubset(ui.get('checks',[])):raise RuntimeError('Project management, compact header, continuous rows and month preview browser regression did not pass')
if not set(('no_rule_editor','month_preview_original_comparison','month_preview_clear_keep_adjustments','month_preview_failed_adjustment_retains_table','month_preview_blocked_destination_still_renders')).issubset(ui.get('checks',[])):raise RuntimeError('Automatic template preview browser regression did not pass')
if not set(('folder_scanning_and_binding','scan_no_write','repeat_idempotent')).issubset(ui.get('checks',[])):raise RuntimeError('Total-folder association browser regression did not pass')
if not set(('server_folder_browser', 'folder_filter_keeps_selection', 'folder_selected_ids', 'cell_edit_preview_confirm_source', 'cell_edit_discard_guard', 'formula_cells_readonly', 'mobile_cell_edit')).issubset(ui.get('checks',[])):raise RuntimeError('Folder selection and direct cell editing browser regression did not pass')

generic_source=json.loads((repo/'runtime/generic-workbook-regression.json').read_text(encoding='utf-8'))
if (generic_source.get('version')!=version or generic_source.get('executable_sha256')!=exe_sha256 or generic_source.get('calculation_and_write_roundtrips')!=124
    or generic_source.get('total_sheets')!=138 or generic_source.get('source_files_unchanged') is not True
    or generic_source.get('candidate_results_readback_verified') is not True
    or generic_source.get('month_formula_inputs_cleared_fixed_headers_preserved') is not True
    or not generic_source.get('workbooks')
    or any(book.get('original_unchanged') is not True for book in generic_source['workbooks'])):
    raise RuntimeError('Full workbook regression did not pass')
generic={key:generic_source[key] for key in ('version','executable_sha256','calculation_and_write_roundtrips','total_sheets','source_files_unchanged','candidate_results_readback_verified','month_formula_inputs_cleared_fixed_headers_preserved')}
generic['workbook_count']=len(generic_source['workbooks'])
for report in (checks,monthly,auth,formula,imports,lan,startup,format_checks,source_checks,preserved):
    if report.get('version')!=version or report.get('executable_sha256')!=exe_sha256:raise RuntimeError('API regression report does not match the release executable')
if subprocess.check_output(['git','status','--porcelain'],cwd=repo,text=True).strip():raise RuntimeError('Commit the reviewed release source before packaging')
commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
branch=subprocess.check_output(['git','branch','--show-current'],cwd=repo,text=True).strip()
if branch!='feat/v0.0.9-select-edit':raise RuntimeError('Package from the isolated v0.0.9 branch only')
release.mkdir(parents=True)
portable=release/f'labwork-v{version}-Windows-x64.zip'
with zipfile.ZipFile(portable,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    z.write(exe,'labwork.exe')
    z.write(root/'README.md','README.md')
    z.write(root/'packaging/安装说明.txt','安装说明.txt')
    z.writestr('labwork.json',json.dumps({'port':18810,'data_dir':'data','lan_ip':'','access_password':''},ensure_ascii=False))
    z.writestr('data/.labwork-rust-preview','local isolated preview only')
    z.writestr('data/workbooks/','')
with zipfile.ZipFile(portable) as z:
    if any(n.endswith(('.xlsx','.sqlite3')) for n in z.namelist()):raise RuntimeError('Business data found in portable package')
subprocess.run(['git','archive','--format=zip','-o',str(release/f'labwork-v{version}-Source.zip'),commit],cwd=repo,check=True)
for source in [root/'README.md',repo/'docs/版本更新-v0.0.9.md',repo/'docs/命名规范.md',repo/'runtime/preserve-format-smoke-result.json',repo/'runtime/original-format-preservation.json',repo/'runtime/native-excel-layout.json',repo/'runtime/native-excel-blank-calculations.json',repo/'runtime/unsupported-formula-preservation.json',repo/'runtime/installer-settings-regression.json',repo/'runtime/installer-firewall-regression.json',repo/'runtime/rust-smoke-result.json',repo/'runtime/month-smoke-result.json',repo/'runtime/auth-smoke-result.json',repo/'runtime/formula-smoke-result.json',repo/'runtime/format-smoke-result.json',repo/'runtime/source-smoke-result.json',repo/'runtime/folder-smoke-result.json',repo/'runtime/cell-edit-smoke-result.json',repo/'runtime/native-excel-cell-edits.json',repo/'runtime/import-smoke-result.json',repo/'runtime/lan-smoke-result.json',repo/'runtime/startup-smoke-result.json']:
    shutil.copyfile(source,release/source.name)
shutil.copytree(ui_directory,release/'browser-qa')
(release/'generic-workbook-regression.json').write_text(json.dumps(generic,indent=2),encoding='utf-8')
manifest={'project':'labwork','release_name':f'labwork-v{version}','product_name':'labwork','binary_name':'labwork.exe','version':version,'git_commit':commit,'branch':branch,'executable_sha256':exe_sha256,'exe_bytes':exe.stat().st_size,'portable_zip_bytes':portable.stat().st_size,'listen':'127.0.0.1:18810','scope':'LAN validation with associated server-local workbook as source of truth','regression':checks,'month_regression':monthly,'unsupported_formula_preservation':unsupported_formula,'installer_settings_regression':installer_settings,'installer_firewall_regression':installer_firewall,'installer_firewall_live_admin_checked':False,'auth_regression':auth,'formula_regression':formula,'format_regression':format_checks,'source_regression':source_checks,'import_regression':imports,'lan_regression':lan,'startup_regression':startup,'generic_workbook_regression':generic,'preserve_original_format_regression':preserved,'all_original_format_preservation':original_layout,'native_excel_layout_regression':native_excel,'browser_regression':ui,'browser_evidence':'browser-qa/result.json','native_tray_visual_checked':False,'unit_tests_note':'cargo test --locked passed tray::tests::tray_registers_stops_and_cleans_up (automatic tray registration and cleanup, not visual inspection)','native_excel_normal_open_checked':True,'native_excel_column_widths_and_frozen_panes_checked':True,'complete_office_formula_recalculation_checked':False}
manifest['cell_edit_regression']=cell_edits
manifest['native_excel_cell_edits_regression']=native_edits
manifest['folder_association_regression']=folder_checks
manifest['native_excel_blank_calculations_regression']=native_blank
(release/'发布信息.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
(release/'SHA256SUMS.txt').write_text('\n'.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(release).as_posix()}' for p in sorted(release.rglob('*')) if p.is_file())+'\n',encoding='utf-8')
print(json.dumps({'release':str(release),'exe_bytes':exe.stat().st_size,'zip_bytes':portable.stat().st_size},ensure_ascii=False))
