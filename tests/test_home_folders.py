import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from omarchy_drive.engine import Engine
from omarchy_drive.home_folders import choices, mappings, select
from omarchy_drive.panel import reveal
from omarchy_drive.storage import Paths, Store, Blocked, atomic_json
from tests.test_engine import MemoryDrive


class HomeFolderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        environment = patch.dict(os.environ, {'HOME': str(self.home), 'XDG_CONFIG_HOME': str(self.home/'.config'), 'XDG_STATE_HOME': str(self.home/'.state')})
        environment.start()
        self.addCleanup(environment.stop)
        self.paths = Paths()
        self.paths.prepare()
        self.root = self.home/'GoogleDrive'
        self.root.mkdir()
        self.documents = self.home/'Documents'
        self.documents.mkdir()
        atomic_json(self.paths.settings, {'local_dir': str(self.root), 'folder_id': 'root', 'selected_folders': []})
        self.store = Store(self.paths.state)
        self.addCleanup(self.store.close)
        self.drive = MemoryDrive()

    def engine(self):
        return Engine(self.paths, self.paths.configured(), self.store, self.drive)

    def enable(self):
        select(self.paths, 'Documents', True)

    def test_two_way_sync_uses_original_location(self):
        (self.documents/'local.txt').write_text('local')
        self.enable()
        self.engine().sync()
        self.assertEqual(self.drive.files['Documents/local.txt']['content'], b'local')
        self.drive.put('Documents/cloud.txt', b'cloud')
        self.engine().sync()
        self.assertEqual((self.documents/'cloud.txt').read_bytes(), b'cloud')
        self.assertFalse((self.root/'Documents').exists())
        self.assertEqual(self.engine().sync(), [])

    def test_cloud_deletion_backs_up_original_home_file(self):
        (self.documents/'report.txt').write_text('keep me')
        self.enable()
        self.engine().sync()
        del self.drive.files['Documents/report.txt']
        self.engine().sync()
        self.assertFalse((self.documents/'report.txt').exists())
        copies = list((self.home/'.GoogleDrive-omarchy-drive-backups').rglob('report.txt'))
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0].read_text(), 'keep me')

    def test_deselect_keeps_files_and_reselect_merges(self):
        (self.documents/'report.txt').write_text('first')
        self.enable()
        self.engine().sync()
        select(self.paths, 'Documents', False)
        (self.documents/'report.txt').write_text('offline edits')
        self.assertEqual(self.engine().sync(), [])
        self.assertEqual(self.drive.files['Documents/report.txt']['content'], b'first')
        self.enable()
        self.engine().sync()
        self.assertTrue(list(self.documents.glob('report.conflict-local-*.txt')))
        self.assertEqual((self.documents/'report.txt').read_text(), 'first')

    def test_missing_or_replaced_source_never_propagates_deletion(self):
        (self.documents/'report.txt').write_text('safe')
        self.enable()
        self.engine().sync()
        self.documents.rename(self.home/'moved')
        with self.assertRaises(Blocked):
            self.engine().sync()
        self.documents.mkdir()
        with self.assertRaisesRegex(Blocked, 'replaced'):
            self.engine().sync()
        self.assertEqual(self.drive.files['Documents/report.txt']['content'], b'safe')
        self.assertTrue(next(row for row in choices(self.paths.configured()) if row['name'] == 'Documents')['error'])

    def test_hidden_symlink_and_overlapping_folders_are_rejected(self):
        (self.home/'.ssh').mkdir()
        (self.home/'Link').symlink_to(self.documents)
        (self.root/'Documents').mkdir()
        for name in ('.ssh', 'Link', 'GoogleDrive', 'Documents', '../Documents', '/etc'):
            with self.subTest(name=name), self.assertRaises(Blocked):
                select(self.paths, name, True)
        self.assertEqual(choices(self.paths.configured()), [])

    def test_reveal_uses_home_folder_and_rejects_traversal(self):
        self.enable()
        with patch('omarchy_drive.panel.subprocess.run') as run:
            run.return_value.returncode = 0
            reveal(self.paths, 'Documents/report.txt')
            self.assertEqual(run.call_args.args[0], ['xdg-open', str(self.documents)])
            with self.assertRaises(Blocked):
                reveal(self.paths, 'Documents/../outside')

    def test_internal_symlink_blocks_sync_before_upload(self):
        self.enable()
        (self.documents/'link').symlink_to(self.home)
        with self.assertRaisesRegex(Blocked, 'Unsupported local file'):
            self.engine().sync()
        self.assertEqual(self.drive.calls, [])

    def test_existing_explicit_selection_is_preserved(self):
        atomic_json(self.paths.settings, {'local_dir': str(self.root), 'folder_id': 'root', 'selected_folders': ['Work']})
        self.enable()
        self.assertEqual(self.paths.configured()['selected_folders'], ['Documents', 'Work'])
        select(self.paths, 'Documents', False)
        self.assertEqual(self.paths.configured()['selected_folders'], ['Work'])

    def test_local_deletion_trashes_only_the_mapped_cloud_file(self):
        (self.documents/'report.txt').write_text('tracked')
        self.enable()
        self.engine().sync()
        (self.documents/'report.txt').unlink()
        self.engine().sync()
        self.assertNotIn('Documents/report.txt', self.drive.files)
        self.assertIn(('trash', 'Documents/report.txt'), self.drive.calls)

    def test_initial_all_folders_selection_uses_known_cloud_folders(self):
        atomic_json(self.paths.settings, {'local_dir': str(self.root), 'folder_id': 'root'})
        with self.assertRaisesRegex(Blocked, 'Refresh'):
            self.enable()
        self.assertNotIn('home_folders', self.paths.configured())
        atomic_json(self.paths.state/'panel-cache.json', {'updated': 1, 'folders': [{'name': 'Work'}]})
        self.enable()
        self.assertEqual(self.paths.configured()['selected_folders'], ['Documents', 'Work'])
