import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from omarchy_drive.engine import Engine
from omarchy_drive.migrate import migrate
from omarchy_drive import panel
from omarchy_drive.storage import Blocked, Paths, Store, atomic_json
from tests.test_engine import MemoryDrive


class MigrateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        environment = patch.dict(os.environ, {
            'HOME': str(self.home), 'XDG_CONFIG_HOME': str(self.home/'.config'),
            'XDG_STATE_HOME': str(self.home/'.state'), 'XDG_DATA_HOME': str(self.home/'.data')})
        environment.start()
        self.addCleanup(environment.stop)
        self.paths = Paths()
        self.paths.prepare()
        self.local = self.home/'GoogleDrive'
        self.local.mkdir()
        self.work = self.home/'Work'
        self.work.mkdir()
        (self.work/'a.txt').write_text('a')
        info = self.work.stat()
        self.old = {'local_dir': str(self.local), 'folder_id': 'root', 'folder_name': 'Omarchy', 'max_deletes': 20,
                    'selected_folders': ['Work'], 'root_files': True,
                    'home_folders': {'Work': {'identity': [info.st_dev, info.st_ino]}}}
        atomic_json(self.paths.settings, self.old)
        self.drive = MemoryDrive()
        self.drive.put('Work/a.txt', b'a')
        self.drive.files['Work'] = {'id': 'folder-Work', 'kind': 'dir', 'hash': None, 'name': 'Work'}
        self.store = Store(self.paths.state)
        self.addCleanup(self.store.close)
        Engine(self.paths, self.paths.configured(), self.store, self.drive).sync()
        self.baseline = self.store.baseline()
        self.set_identity('root')

    def set_identity(self, folder, old_format=True):
        self.store.replace_identity(json.dumps([str(self.local), 1, 2, folder]) if old_format else folder)

    def backup(self):
        return self.paths.settings.with_name('settings.json.pre-migration')

    def test_empty_local_dir_keeps_baseline_and_gives_no_changes(self):
        self.assertTrue(migrate(self.paths))
        settings = self.paths.configured()
        for key in ('local_dir', 'selected_folders', 'root_files'):
            self.assertNotIn(key, settings)
        self.assertEqual(settings['home_folders'], self.old['home_folders'])
        self.assertEqual(self.store.baseline(), self.baseline)
        self.assertEqual(self.store.stored_identity(), 'root')
        self.assertTrue(self.local.exists())
        actions = Engine(self.paths, settings, self.store, self.drive).snapshot()
        self.assertEqual({a.kind for a in actions}, {'remember'})
        self.assertFalse(migrate(self.paths))

    def test_contents_in_local_dir_change_nothing_and_name_them(self):
        (self.local/'Work2').mkdir()
        (self.local/'notes.txt').write_text('n')
        before = self.paths.settings.read_bytes()
        identity = self.store.stored_identity()
        with self.assertRaises(Blocked) as caught:
            migrate(self.paths)
        self.assertIn('Move these out of', str(caught.exception))
        self.assertIn('notes.txt', str(caught.exception))
        self.assertIn('Work2', str(caught.exception))
        self.assertEqual(self.paths.settings.read_bytes(), before)
        self.assertEqual(self.store.stored_identity(), identity)
        self.assertFalse(self.backup().exists())

    def test_message_names_at_most_five_items(self):
        for i in range(8):
            (self.local/f'item{i}').write_text('x')
        with self.assertRaises(Blocked) as caught:
            migrate(self.paths)
        self.assertEqual(str(caught.exception).count('item'), 5)

    def test_only_marker_and_temp_files_count_as_empty(self):
        (self.local/'RCLONE_TEST').write_text('')
        (self.local/'.omarchy-drive-tmp-abc').write_text('')
        self.assertTrue(migrate(self.paths))

    def test_missing_local_dir_counts_as_empty(self):
        self.local.rmdir()
        self.assertTrue(migrate(self.paths))
        self.assertNotIn('local_dir', self.paths.configured())

    def test_identity_for_another_drive_folder_stops(self):
        self.set_identity('other')
        before = self.paths.settings.read_bytes()
        with self.assertRaises(Blocked):
            migrate(self.paths)
        self.assertEqual(self.paths.settings.read_bytes(), before)
        self.assertFalse(self.backup().exists())
        self.assertIn('other', self.store.stored_identity())

    def test_already_migrated_is_a_no_op_without_the_lock(self):
        migrate(self.paths)
        with self.paths.lock():
            self.assertFalse(migrate(self.paths))

    def test_needs_migration_while_locked_reports_rather_than_crashes(self):
        output = io.StringIO()
        with self.paths.lock(), contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = panel.main(['status'])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(output.getvalue())['ok'])
        self.assertIn('local_dir', self.paths.configured())

    def test_crash_after_identity_rewrite_recovers(self):
        self.set_identity('root', old_format=False)
        self.assertTrue(migrate(self.paths))
        self.assertNotIn('local_dir', self.paths.configured())
        self.assertEqual(self.store.stored_identity(), 'root')

    def test_original_settings_are_preserved_across_reruns(self):
        migrate(self.paths)
        self.assertEqual(json.loads(self.backup().read_text()), self.old)
        atomic_json(self.paths.settings, dict(self.old, max_deletes=5))
        migrate(self.paths)
        self.assertEqual(json.loads(self.backup().read_text()), self.old)
