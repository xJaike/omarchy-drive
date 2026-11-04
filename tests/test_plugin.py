import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('drive_progress', ROOT / 'progress.py')
progress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(progress)


class ProgressTests(unittest.TestCase):
    def test_partial_log_and_terminal_escaping(self):
        with tempfile.TemporaryDirectory() as directory:
            progress.STATE = Path(directory)
            stats = {'bytes': 50, 'totalBytes': 100, 'transfers': 1, 'totalTransfers': 2}
            (progress.STATE / 'current.jsonl').write_text(
                json.dumps({'stats': stats}) + '\n' +
                json.dumps({'object': 'a.txt', 'msg': 'Copied (new)'}) + '\n{partial')
            actual, events = progress.snapshot()
            self.assertEqual(actual, stats)
            self.assertEqual(events[-1][1], 'a.txt')
            self.assertIn('50%', progress.summary(actual))
            self.assertNotIn('\x1b', progress.safe('file\x1b[2J\nname'))

    def test_setup_without_credentials_or_network(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, XDG_STATE_HOME=directory)
            value = subprocess.check_output(['python3', str(ROOT / 'status.py')], env=env)
            self.assertEqual(json.loads(value)['kind'], 'setup')


class InstallTests(unittest.TestCase):
    def test_install_remove_preserves_user_files(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            fakebin = home / 'fakebin'
            fakebin.mkdir()
            for tool in ('systemctl', 'rclone'):
                command = fakebin / tool
                command.write_text('#!/bin/sh\nexit 0\n')
                command.chmod(0o755)
            env = dict(os.environ, HOME=directory, XDG_STATE_HOME=str(home / 'state'),
                       PATH=str(fakebin) + ':' + os.environ['PATH'])
            local = home / 'GoogleDrive'
            local.mkdir()
            (local / 'keep.txt').write_text('keep this')
            old = home / '.local/bin/omarchy-drive-sync'
            old.parent.mkdir(parents=True)
            old.write_text('previous helper')
            subprocess.run(['bash', str(ROOT / 'install.sh')], env=env, check=True, capture_output=True)
            self.assertTrue(old.is_symlink())
            backups = list((home / 'state/omarchy-drive-sync/install-backups').glob('*/omarchy-drive-sync'))
            self.assertEqual(backups[0].read_text(), 'previous helper')
            subprocess.run(['bash', str(ROOT / 'uninstall.sh')], env=env, check=True, capture_output=True)
            self.assertFalse(old.is_symlink())
            self.assertEqual((local / 'keep.txt').read_text(), 'keep this')
            self.assertFalse((home / '.config/systemd/user/omarchy-drive-sync.timer').exists())


@unittest.skipUnless(shutil.which('rclone'), 'rclone not installed')
class BisyncTests(unittest.TestCase):
    def test_empty_initialization_then_two_way_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            a, b = base / 'local', base / 'remote'
            a.mkdir()
            b.mkdir()
            for folder in (a, b):
                (folder / 'RCLONE_TEST').touch()
            command = ['rclone', 'bisync', str(a), str(b), '--workdir', str(base / 'state'),
                       '--check-access', '--resilient', '--recover']
            subprocess.run(command + ['--resync', '--resync-mode', 'newer'], check=True, capture_output=True)
            (a / 'local.txt').write_text('offline edit')
            (b / 'cloud.txt').write_text('cloud edit')
            subprocess.run(command, check=True, capture_output=True)
            self.assertEqual((b / 'local.txt').read_text(), 'offline edit')
            self.assertEqual((a / 'cloud.txt').read_text(), 'cloud edit')
            subprocess.run(command, check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
