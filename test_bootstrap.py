import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
import bootstrap


class BootstrapTests(unittest.TestCase):
    def test_launcher_delegates_venv_repair(self):
        text = (Path(__file__).parent / 'Start.command').read_text()
        self.assertIn('bootstrap.py', text)
        self.assertNotIn('python3 -m venv .venv', text)

    def test_reuses_a_venv_with_working_pip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            py = root / '.venv' / 'bin' / 'python3'
            py.parent.mkdir(parents=True)
            py.write_text('#!/bin/sh\n')
            py.chmod(py.stat().st_mode | stat.S_IEXEC)
            calls = []
            ok, message = bootstrap.ensure_venv(root, run=lambda *args, **kwargs: calls.append(args), pip_ok=lambda path: True, version='3.12')
            self.assertTrue(ok)
            self.assertEqual(message, '')
            self.assertEqual(calls, [])

    def test_broken_venv_is_removed_and_a_failed_create_leaves_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            broken = root / '.venv' / 'bin' / 'python3'
            broken.parent.mkdir(parents=True)
            broken.write_text('#!/bin/sh\nexit 1\n')
            broken.chmod(broken.stat().st_mode | stat.S_IEXEC)

            def run(cmd, **kwargs):
                dest = Path(cmd[-1])
                (dest / 'bin').mkdir(parents=True)
                (dest / 'bin' / 'python3').write_text('partial')
                return subprocess.CompletedProcess(cmd, 1, '', 'ensurepip is not available')

            ok, message = bootstrap.ensure_venv(root, run=run, pip_ok=lambda path: False, version='3.12')
            self.assertFalse(ok)
            self.assertFalse((root / '.venv').exists())
            self.assertIn('sudo apt install python3.12-venv', message)
            self.assertIn('does not need administrator privileges', message)

    def test_recreate_keeps_a_usable_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def run(cmd, **kwargs):
                dest = Path(cmd[-1])
                py = dest / 'bin' / 'python3'
                py.parent.mkdir(parents=True)
                py.write_text('#!/bin/sh\n')
                py.chmod(py.stat().st_mode | stat.S_IEXEC)
                return subprocess.CompletedProcess(cmd, 0, '', '')

            ok, message = bootstrap.ensure_venv(root, run=run, pip_ok=lambda path: True, version='3.12')
            self.assertTrue(ok)
            self.assertEqual(message, '')
            self.assertTrue((root / '.venv' / 'bin' / 'python3').is_file())


if __name__ == '__main__':
    unittest.main()
