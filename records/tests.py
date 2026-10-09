import csv
import json
import os
import subprocess
import sys
import shutil
import tempfile
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from .calculations import calculate, formulas
from .models import Method, Project, Record, Workbook, Writeback
from .services import process_job, register, seed_demo, submit, writer_lock
from .xlsx import browse_sheet, fingerprint, read_archive, workbook_path, xml, N


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PlatformFlowTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workbooks = self.root / 'workbooks'
        self.workbooks.mkdir()
        self.override = override_settings(RUNTIME_DIR=self.root, WORKBOOK_ROOT=self.workbooks, WRITEBACK_ENABLED=True)
        self.override.enable()
        seed_demo()
        self.user = get_user_model().objects.create_user('tester', password='testing-long-password')
        self.other = get_user_model().objects.create_user('outsider', password='another-long-password')
        self.project = Project.objects.get(code='D006')
        self.project.members.add(self.user)
        self.book = Workbook.objects.get(project=self.project)
        self.method = Method.objects.get(project=self.project, key='total_n')
        self.client.force_login(self.user)

    def tearDown(self):
        self.override.disable()
        self.temp.cleanup()

    def record(self, book=None, method=None):
        book, method = book or self.book, method or self.method
        raw = {'titration': {'mass': '1', 'volume': '10'}, 'instrument': {'result': '15'},
            'water_diluted': {'result': '60', 'original_mass': '10', 'total_mass': '20', 'water_result': '100'},
            'fluoride': {'mass1': '2', 'total1': '20', 'mass2': '5', 'total2': '50', 'reading': '1.2'},
            'loss': {'mass': '100', 'before': '2000', 'after1': '1995', 'after2': '1995'},
            'melting': {'start': '150', 'end': '153'}}[method.kind]
        values, output = calculate(method.kind, raw, method.params)
        return Record.objects.create(project=book.project, workbook=book, method=method, author=self.user,
            detected_on=date(2026, 10, 9), batch='TEST-ONLY-001', measurements=[values], outputs=[output],
            method_snapshot={'version': method.version, 'kind': method.kind, 'params': method.params})

    def test_calculations_and_invalid_boundaries(self):
        _, result = calculate('titration', {'mass': '1', 'volume': '10'}, self.method.params)
        self.assertEqual(result['display'], '7.14841')
        self.assertEqual(result['result'], '7.14840636')
        _, result = calculate('fluoride', {'mass1': 2, 'total1': 20, 'mass2': 5, 'total2': 50, 'reading': 1.2}, {})
        self.assertEqual(result['result'], '120.0')
        _, result = calculate('loss', {'mass': 100, 'before': 2000, 'after1': 1995, 'after2': 1995}, {})
        self.assertEqual(result['result'], '5.00')
        _, result = calculate('water_diluted', {'result': 60, 'original_mass': 10, 'total_mass': 20, 'water_result': 100}, {})
        self.assertEqual(result['result'], '20')
        for raw in ({'mass': 0, 'volume': 10}, {'mass': 1, 'volume': 'NaN'}, {'mass': 1, 'volume': 0}):
            with self.assertRaises(ValueError):
                calculate('titration', raw, self.method.params)

    def test_first_admin_and_environment_guards(self):
        from django.core.management import call_command
        get_user_model().objects.all().delete()
        with override_settings(ADMIN_USERNAME='first_admin', ADMIN_PASSWORD='unique-long-initial-password'):
            call_command('bootstrap')
        self.assertTrue(get_user_model().objects.get(username='first_admin').check_password('unique-long-initial-password'))
        base = dict(os.environ, LABWORK_CONFIG='', RUNTIME_DIR=str(self.root), WORKBOOK_ROOT=str(self.workbooks),
            APP_ENV='prod', SECRET_KEY='independent-secret-' + '1'*40, ALLOWED_HOSTS='localhost')
        for overrides in ({'DATABASE_URL': ''}, {'DATABASE_URL': 'postgresql://test:test@localhost/labwork_dev'},
                          {'SECRET_KEY': 'short'}, {'ALLOWED_HOSTS': '*'},
                          {'APP_ENV': 'test', 'WORKBOOK_ROOT': str(self.root.parent)}):
            result = subprocess.run([sys.executable, '-c', 'import labwork.settings'],
                env=dict(base, **overrides), cwd=settings.BASE_DIR, capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_browser_flow_idempotence_permissions_and_csrf(self):
        token = str(uuid.uuid4())
        data = {'book': self.book.pk, 'method': self.method.pk, 'token': token, 'detected_on': '2026-10-09',
            'batch': 'TEST-ONLY-BROWSER', 'mass_1': '1', 'volume_1': '10', 'version': 1, 'action': 'submit'}
        response = self.client.post('/records/new/', data)
        self.assertEqual(response.status_code, 302)
        self.client.post('/records/new/', data)
        record = Record.objects.get(token=token)
        self.assertEqual(Record.objects.filter(token=token).count(), 1)
        self.assertEqual(Writeback.objects.filter(record=record).count(), 1)
        for url in ['/', f'/records/{record.pk}/', f'/workbooks/{self.book.pk}/', f'/workbooks/{self.book.pk}/download/']:
            self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/records/{record.pk}/').status_code, 404)
        self.assertEqual(self.client.get(f'/workbooks/{self.book.pk}/download/').status_code, 404)
        from django.test import Client
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post('/records/new/', data).status_code, 403)
        from django.contrib.auth.models import Group
        self.user.groups.add(Group.objects.create(name='只读人员'))
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/records/new/').status_code, 403)
        self.assertEqual(self.client.post(f'/records/{record.pk}/revise/', {'reason': 'not allowed'}).status_code, 403)
        self.assertEqual(self.client.get(f'/records/{record.pk}/').status_code, 200)

    def test_writeback_preserves_parts_and_recovers_without_duplicates(self):
        record = submit(self.record(), self.user)
        job = record.writeback
        source = workbook_path(self.book)
        original, _, _, sheets = read_archive(source)
        process_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status, 'done', job.message)
        after, _, _, _ = read_archive(source)
        modified = sheets['总氮']
        self.assertEqual([key for key in original if original[key] != after[key]], [modified])
        rows = browse_sheet(source, '总氮')
        self.assertTrue(any(c['value'] == record.batch for row in rows for c in row['cells']))
        saved_hash = fingerprint(source)
        job.status = 'writing'
        job.save()
        process_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status, 'done')
        self.assertEqual(fingerprint(source), saved_hash)
        self.assertTrue((self.root / job.backup).exists())

    def test_occupied_retry_and_external_conflict(self):
        record = submit(self.record(), self.user)
        with patch('records.services.os.replace', side_effect=PermissionError('locked')):
            process_job(record.writeback)
        job = Writeback.objects.get(record=record)
        self.assertEqual(job.status, 'occupied')
        path = workbook_path(self.book)
        with path.open('ab') as stream:
            stream.write(b'external-change')
        process_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status, 'conflict')
        before = path.read_bytes()
        self.client.post(f'/records/{record.pk}/retry/')
        job.refresh_from_db()
        self.assertEqual(job.status, 'conflict')
        self.assertEqual(path.read_bytes(), before)

    def test_method_requires_confirmation_and_keeps_snapshot(self):
        path = self.workbooks / 'real-copy.xlsx'
        shutil.copyfile(workbook_path(self.book), path)
        real_book = register(self.project, path)
        record = self.record(book=real_book)
        with self.assertRaises(ValueError):
            submit(record, self.user)
        self.method.confirmed = True
        self.method.basis = 'test-approved-only'
        self.method.save()
        record = submit(record, self.user)
        self.method.params = dict(self.method.params, c='1')
        self.method.version += 1
        self.method.save()
        record.refresh_from_db()
        self.assertEqual(record.method_snapshot['params']['c'], '0.5128')
        process_job(record.writeback)
        self.assertEqual(Writeback.objects.get(record=record).status, 'done')
        draft = self.record()
        draft.method_snapshot['version'] = 1
        draft.save()
        with self.assertRaisesRegex(ValueError, '方法已更新'):
            submit(draft, self.user)

    def test_lock_and_environment_scope(self):
        with writer_lock():
            with self.assertRaises(RuntimeError):
                with writer_lock():
                    pass
        self.book.relative_path = '../outside.xlsx'
        with self.assertRaises(ValueError):
            workbook_path(self.book)
        self.assertEqual(self.client.get('/', REMOTE_ADDR='203.0.113.1').status_code, 403)
        self.assertEqual(self.client.get('/', REMOTE_ADDR='203.0.113.1', HTTP_X_REAL_IP='127.0.0.1').status_code, 403)
        self.assertEqual(self.client.get('/', REMOTE_ADDR='127.0.0.1', HTTP_X_REAL_IP='203.0.113.1').status_code, 403)

    def test_file_queue_orders_parallel_measurements_and_revisions(self):
        record = self.record()
        record.measurements *= 2
        record.outputs *= 2
        record.save()
        submit(record, self.user)
        second = submit(self.record(), self.user)
        from django.core.management import call_command
        call_command('writeback', once=True)
        first_job = Writeback.objects.get(record=record)
        second_job = Writeback.objects.get(record=second)
        self.assertEqual(first_job.rows, [4, 5])
        self.assertEqual(second_job.rows, [6])
        self.client.post(f'/records/{record.pk}/revise/', {'reason': 'test correction'})
        revised = Record.objects.get(supersedes=record)
        self.assertEqual(revised.status, 'draft')
        self.assertEqual(record.author, revised.author)
        self.assertEqual(self.client.post(f'/records/{record.pk}/review/').status_code, 403)

    def test_source_copies_preserve_real_objects_when_available(self):
        inventory = settings.BASE_DIR / 'docs' / '工作簿功能清单.csv'
        with inventory.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        from .xlsx import SHEET_NAMES
        cases = [(method.project.code, method.key) for method in Method.objects.select_related('project')]
        checked = 0
        for code, key in cases:
            source = Path(next(item['源文件路径'] for item in rows if item['项目代码_按文件名暂定'] == code))
            if not source.exists():
                continue
            original_hash = fingerprint(source)
            path = self.workbooks / f'{code}-{key}-source-copy.xlsx'
            shutil.copyfile(source, path)
            project = Project.objects.get(code=code)
            method = Method.objects.get(project=project, key=key)
            method.confirmed = True
            method.save()
            book = register(project, path)
            self.assertIn(key, book.mapping)
            original, _, _, sheets = read_archive(path)
            record = self.record(book=book, method=method)
            submit(record, self.user)
            process_job(Writeback.objects.get(record=record))
            job = Writeback.objects.get(record=record)
            self.assertEqual(job.status, 'done', job.message)
            after, _, _, _ = read_archive(path)
            sheet = next(name for name in sheets if name.strip() == SHEET_NAMES[key])
            self.assertEqual([name for name in original if original[name] != after[name]], [sheets[sheet]])
            self.assertEqual(fingerprint(source), original_hash)
            checked += 1
        if not checked:
            self.skipTest('Original source workbooks unavailable; generated workbook tests still cover writeback')
        self.assertEqual(checked, 14)
