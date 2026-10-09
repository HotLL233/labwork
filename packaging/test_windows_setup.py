"""Run on Windows: python packaging/test_windows_setup.py."""
import ctypes
import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent / 'windows'))
import control


class WindowsSetupTests(unittest.TestCase):
    def test_unspecified_address_rejected_before_initialization(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            home = root / 'data'
            home.mkdir()
            request = home / 'request.json'
            request.write_text(json.dumps({'host': '0.0.0.0'}))
            with patch.object(control, 'pg') as pg, patch.object(control, 'run') as run:
                with self.assertRaisesRegex(ValueError, '0.0.0.0'):
                    control.setup(home, request)
            pg.assert_not_called()
            run.assert_not_called()
            self.assertFalse(request.exists())
            self.assertFalse((home / 'database').exists())

    def test_service_start_and_stop_arguments_are_distinct(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            home = root / 'data'
            home.mkdir()
            (home / 'config.json').write_text('{}')
            request = home / 'request.json'
            request.write_text(json.dumps({'host': 'localhost', 'port': '8443',
                'networks': '127.0.0.0/8', 'username': 'admin', 'password': ''}))
            with patch.object(control, 'RELEASE', root / 'releases/0.0.1'), \
                    patch.object(control, 'run'), patch.object(control, 'pg'):
                control.setup(home, request)
            service = ET.parse(root / 'LabworkService.xml').getroot()
            self.assertIsNone(service.find('arguments'))
            self.assertIn('supervisor.py', service.findtext('startarguments'))
            self.assertIn('shutdown', service.findtext('stoparguments'))

    def test_original_acl_restored_after_success_and_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder) / 'test data'
            (home / 'logs').mkdir(parents=True)
            child = home / 'logs' / 'existing.log'
            child.write_text('unchanged')
            def acl(path):
                return subprocess.check_output(['icacls', str(path)])
            before = (acl(home), acl(child))
            # Exercise the elevated branch with real icacls on this owned test directory.
            with patch.object(ctypes.windll.shell32, 'IsUserAnAdmin', return_value=True):
                with control.postgres_access(home):
                    (home / 'new.log').write_text('test')
                with self.assertRaisesRegex(RuntimeError, 'simulated initdb failure'):
                    with control.postgres_access(home):
                        raise RuntimeError('simulated initdb failure')
            self.assertEqual(before, (acl(home), acl(child)))
            self.assertEqual(child.read_text(), 'unchanged')
            self.assertFalse(list((home / 'logs').glob('.postgres-acl-*')))

    def test_child_error_output_reaches_setup_log(self):
        with tempfile.TemporaryDirectory() as folder:
            logfile = Path(folder) / 'setup.log'
            with logfile.open('w', encoding='utf-8') as stream:
                with redirect_stdout(stream), redirect_stderr(stream):
                    with self.assertRaises(subprocess.CalledProcessError):
                        control.run(sys.executable, '-c', 'import sys; print("initdb-error-detail",file=sys.stderr); sys.exit(1)')
            self.assertIn('initdb-error-detail', logfile.read_text())


if __name__ == '__main__':
    unittest.main()
