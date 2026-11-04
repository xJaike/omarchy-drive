import contextlib
import io
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import tempfile
import termios
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import MagicMock, patch

from omarchy_drive.auth import authorize
from omarchy_drive.storage import AuthRequired, Blocked, Paths, Store, atomic_json, read_json

ROOT = Path(__file__).resolve().parents[1]


class AppTests(unittest.TestCase):
    def environment(self, directory):
        return dict(os.environ, HOME=directory, XDG_CONFIG_HOME=directory + '/config',
                    XDG_DATA_HOME=directory + '/data', XDG_STATE_HOME=directory + '/state')

    def test_setup_status_and_sync_without_credentials_do_not_use_network(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(directory)
            guide = subprocess.check_output([str(ROOT / 'bin/omarchy-drive'), 'setup'], env=env).decode()
            self.assertIn('Desktop app', guide)
            data = json.loads(subprocess.check_output([str(ROOT / 'bin/omarchy-drive'), 'status'], env=env))
            self.assertEqual(data['kind'], 'setup')
            result = subprocess.run([str(ROOT / 'bin/omarchy-drive'), 'sync'], env=env, capture_output=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn(b'Connect your Google account', result.stderr)
            self.assertFalse((Path(directory) / 'GoogleDrive').exists())

    def test_dashboard_quit_restores_terminal_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            master, slave = pty.openpty()
            try:
                before = termios.tcgetattr(slave)
                child = subprocess.Popen([str(ROOT / 'bin/omarchy-drive')], env=self.environment(directory),
                                         stdin=slave, stdout=slave, stderr=slave)
                self.addCleanup(lambda: child.poll() is None and child.kill())
                output = b''
                deadline = time.monotonic() + 5
                while b'[Q] Quit' not in output and time.monotonic() < deadline:
                    if select.select([master], [], [], .1)[0]:
                        output += os.read(master, 65536)
                self.assertIn(b'[Q] Quit', output)
                os.write(master, b'q')
                self.assertEqual(child.wait(timeout=3), 0)
                self.assertEqual(termios.tcgetattr(slave), before)
            finally:
                os.close(master)
                os.close(slave)

    def test_install_remove_preserves_files_and_has_no_engine_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            fakebin = home / 'fakebin'
            fakebin.mkdir()
            fake = fakebin / 'systemctl'
            fake.write_text('#!/bin/sh\nexit 0\n')
            fake.chmod(0o755)
            env = self.environment(directory)
            env['PATH'] = str(fakebin) + ':' + os.environ['PATH']
            (home / 'GoogleDrive').mkdir()
            keep = home / 'GoogleDrive/keep.txt'
            keep.write_text('keep')
            old = home / '.local/bin/omarchy-drive-sync'
            old.parent.mkdir(parents=True)
            old.write_text('old engine')
            result = subprocess.run(['bash', str(ROOT / 'install.sh')], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(old.is_symlink())
            backups = list((home / 'state/omarchy-drive-v2/install-backups').glob('*/omarchy-drive-sync'))
            self.assertEqual(backups[0].read_text(), 'old engine')
            self.assertTrue((home / 'data/applications/omarchy-drive.desktop').exists())
            subprocess.run(['bash', str(ROOT / 'uninstall.sh')], env=env, check=True, capture_output=True)
            self.assertFalse(old.exists())
            self.assertEqual(keep.read_text(), 'keep')
            self.assertFalse((home / 'config/systemd/user/omarchy-drive-sync.timer').exists())

    def test_lock_prevents_a_second_sync(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, self.environment(directory)):
            paths = Paths()
            with paths.lock():
                result = subprocess.run([str(ROOT / 'bin/omarchy-drive'), 'sync'], capture_output=True)
                self.assertEqual(result.returncode, 1)
                self.assertIn(b'Another sync', result.stderr)
                self.assertFalse(paths.status.exists())

    def test_runtime_never_executes_an_external_sync_engine(self):
        for file in [*ROOT.glob('bin/*'), *ROOT.glob('omarchy_drive/*.py'), ROOT / 'install.sh']:
            if not file.is_file():
                continue
            text = file.read_text().lower()
            self.assertNotRegex(text, r'\brclone\b', str(file))
            self.assertNotIn('insync', text, str(file))


class OAuthCallbackTests(unittest.TestCase):
    def test_pkce_state_and_loopback_exchange(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'XDG_CONFIG_HOME': directory + '/config', 'XDG_STATE_HOME': directory + '/state'}):
            paths = Paths()
            client = Path(directory) / 'download.json'
            atomic_json(client, {'installed': {'client_id': 'client', 'client_secret': 'secret'}})
            captured, threads, errors = {}, [], []
            def browser(url):
                params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
                captured.update({k: v[0] for k, v in params.items()})
                def callback():
                    try:
                        target = captured['redirect_uri']
                        try:
                            urllib.request.urlopen(target + '?state=wrong&code=bad', timeout=3)
                            errors.append('Wrong OAuth state was accepted')
                        except urllib.error.HTTPError as error:
                            if error.code != 400:
                                errors.append('Wrong callback status')
                            error.close()
                        with urllib.request.urlopen(target + '?' + urllib.parse.urlencode({
                                'state': captured['state'], 'code': 'accepted'}), timeout=3) as response:
                            if response.code != 200:
                                errors.append('Callback failed')
                    except Exception as error:
                        errors.append(str(error))
                thread = threading.Thread(target=callback)
                threads.append(thread)
                thread.start()
                return True
            def exchange(value):
                import base64, hashlib
                expected = base64.urlsafe_b64encode(hashlib.sha256(value['code_verifier'].encode()).digest()).rstrip(b'=').decode()
                self.assertEqual(expected, captured['code_challenge'])
                self.assertEqual(value['code'], 'accepted')
                self.assertEqual(captured['code_challenge_method'], 'S256')
                return {'access_token': 'access', 'refresh_token': 'refresh', 'expires_in': 3600}
            with patch('omarchy_drive.auth.webbrowser.open', browser), patch('omarchy_drive.auth.exchange', exchange), contextlib.redirect_stdout(io.StringIO()):
                authorize(paths, client, timeout=5)
            for thread in threads:
                thread.join(timeout=5)
            self.assertFalse(errors, errors)
            self.assertEqual(read_json(paths.tokens)['refresh_token'], 'refresh')
            self.assertEqual(paths.tokens.stat().st_mode & 0o777, 0o600)


class ContinueSetupTests(unittest.TestCase):
    def test_continue_uses_saved_sign_in_without_browser(self):
        from omarchy_drive.cli import main
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'XDG_CONFIG_HOME': directory + '/config', 'XDG_STATE_HOME': directory + '/state'}):
            paths = Paths()
            paths.prepare()
            atomic_json(paths.client, {'installed': {'client_id': 'client', 'client_secret': 'secret'}})
            atomic_json(paths.tokens, {'refresh_token': 'saved'})
            with patch('omarchy_drive.cli.authorize') as authorize_mock, patch('omarchy_drive.cli.Drive') as drive_class, contextlib.redirect_stdout(io.StringIO()):
                drive_class.return_value.root.return_value = 'folder'
                drive_class.return_value.get.return_value = {'name': 'Omarchy', 'mimeType': 'application/vnd.google-apps.folder'}
                result = main(['setup', '--continue'])
            self.assertEqual(result, 0)
            authorize_mock.assert_not_called()
            self.assertEqual(read_json(paths.settings)['folder_id'], 'folder')

    def test_setup_parser_has_no_local_option(self):
        from omarchy_drive.cli import parser
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parser().parse_args(['setup', '--local', '/x'])

    def test_fresh_setup_ticks_nothing(self):
        from omarchy_drive.cli import main
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'HOME': directory, 'XDG_CONFIG_HOME': directory + '/config', 'XDG_STATE_HOME': directory + '/state'}):
            paths = Paths()
            paths.prepare()
            atomic_json(paths.client, {'installed': {'client_id': 'client', 'client_secret': 'secret'}})
            atomic_json(paths.tokens, {'refresh_token': 'saved'})
            with patch('omarchy_drive.cli.Drive') as drive_class, contextlib.redirect_stdout(io.StringIO()):
                drive_class.return_value.root.return_value = 'folder'
                drive_class.return_value.get.return_value = {'name': 'Omarchy', 'mimeType': 'application/vnd.google-apps.folder'}
                self.assertEqual(main(['setup', '--continue']), 0)
            settings = read_json(paths.settings)
            self.assertEqual({k: settings[k] for k in ('folder_id', 'folder_name', 'max_deletes')},
                             {'folder_id': 'folder', 'folder_name': 'Omarchy', 'max_deletes': 20})
            self.assertNotIn('local_dir', settings)
            self.assertFalse(settings.get('home_folders'))
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), ['config', 'state'])

    def test_doctor_accepts_a_drive_only_ticked_folder(self):
        from omarchy_drive.cli import doctor
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'HOME': directory, 'XDG_CONFIG_HOME': directory + '/config', 'XDG_STATE_HOME': directory + '/state'}):
            paths = Paths()
            paths.prepare()
            atomic_json(paths.settings, {'folder_id': 'root', 'home_folders': {'Cloud': {'identity': None}}})
            output = io.StringIO()
            with patch('omarchy_drive.cli.context') as make, contextlib.redirect_stdout(output):
                make.return_value = (paths.configured(), Store(paths.state), MagicMock(scan=MagicMock(return_value={})))
                self.assertEqual(doctor(paths), 0)
            self.assertIn('Local scan: OK (0 items)', output.getvalue())

    def test_doctor_works_without_local_dir(self):
        from omarchy_drive.cli import doctor
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'HOME': directory, 'XDG_CONFIG_HOME': directory + '/config', 'XDG_STATE_HOME': directory + '/state'}):
            (Path(directory) / 'Work').mkdir()
            (Path(directory) / 'Work/a.txt').write_text('a')
            paths = Paths()
            paths.prepare()
            info = (Path(directory) / 'Work').stat()
            atomic_json(paths.settings, {'folder_id': 'root', 'home_folders': {'Work': {'identity': [info.st_dev, info.st_ino]}}})
            output = io.StringIO()
            with patch('omarchy_drive.cli.context') as make, contextlib.redirect_stdout(output):
                store = Store(paths.state)
                drive = MagicMock(scan=MagicMock(return_value={}))
                make.return_value = (paths.configured(), store, drive)
                self.assertEqual(doctor(paths), 0)
            self.assertIn('Local scan: OK (2 items)', output.getvalue())

    def test_continue_without_tokens_returns_instructions(self):
        from omarchy_drive.cli import main
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'XDG_CONFIG_HOME': directory + '/config', 'XDG_STATE_HOME': directory + '/state'}), contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(main(['setup', '--continue']), 1)
            self.assertIn('No saved sign-in', errors.getvalue())
