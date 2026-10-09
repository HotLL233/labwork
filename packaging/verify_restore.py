"""Restore a packaging-test snapshot into a separate test database and directory."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import secrets
from pathlib import Path
from urllib.parse import urlparse, unquote


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    args = parser.parse_args()
    home, snapshot = args.home.resolve(), args.snapshot.resolve()
    if not home.is_relative_to(Path(__file__).resolve().parent.parent / 'runtime'):
        raise ValueError('恢复检查只允许开发工作区 runtime 中的隔离安装测试数据')
    active = json.loads((home / 'active.json').read_text())
    release = Path(active['release'])
    admin = json.loads((home / 'database-admin.json').read_text())
    config = json.loads((snapshot / 'config.json').read_text())
    restore_root = home.parent / 'restore-smoke'
    restore_root.mkdir(exist_ok=True)
    for folder in ('workbooks', 'backups'):
        shutil.copytree(snapshot / folder, restore_root / folder, dirs_exist_ok=True)
    import psycopg
    from psycopg import sql
    database = 'labwork_test_restore_' + secrets.token_hex(4)
    with psycopg.connect(host='127.0.0.1', port=admin['port'], user='postgres', password=admin['password'],
                         dbname='postgres', autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {} OWNER labwork').format(sql.Identifier(database)))
    env = dict(os.environ, PGPASSWORD=unquote(urlparse(config['DATABASE_URL']).password))
    subprocess.run([str(release / 'postgres/bin/pg_restore.exe'), '-h', '127.0.0.1', '-p', str(admin['port']),
        '-U', 'labwork', '--no-owner', '--no-privileges', '-d', database, str(snapshot / 'database.dump')],
        env=env, check=True, capture_output=True)
    config.update(APP_ENV='test', RUNTIME_DIR=str(restore_root), WORKBOOK_ROOT=str(restore_root / 'workbooks'),
                  DATABASE_URL=config['DATABASE_URL'].replace('/labwork_prod', '/' + database))
    path = restore_root / 'config.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    os.environ.update(LABWORK_CONFIG=str(path), DJANGO_SETTINGS_MODULE='labwork.settings')
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import django
    django.setup()
    from datetime import date
    from django.contrib.auth import get_user_model
    from records.calculations import calculate
    from records.models import Record, Workbook, Writeback
    from records.services import process_job, submit, writer_lock
    from records.xlsx import fingerprint, workbook_path
    assert Record.objects.count() == 14
    assert Writeback.objects.filter(status='done').count() == 14
    for book in Workbook.objects.all():
        assert fingerprint(workbook_path(book)) == book.fingerprint
    original = Record.objects.filter(method__kind='titration').first()
    values, output = calculate(original.method.kind, original.measurements[0], original.method.params)
    record = Record.objects.create(project=original.project, workbook=original.workbook, method=original.method,
        detected_on=date.today(), batch='RESTORE-ONLY', author=get_user_model().objects.first(),
        measurements=[values], outputs=[output], method_snapshot=original.method_snapshot)
    submit(record, record.author)
    with writer_lock():
        process_job(Writeback.objects.get(record=record))
    assert Writeback.objects.get(record=record).status == 'done'
    result = {'restored_records': 14, 'restored_tasks': 14, 'workbook_fingerprints': 'match',
              'continued_writeback': 'done', 'database': database}
    (restore_root / 'report.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('Restore passed: records, tasks, workbook hashes and a new append.')


if __name__ == '__main__':
    main()
