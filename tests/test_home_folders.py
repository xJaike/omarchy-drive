import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from omarchy_drive.drive import digest
from omarchy_drive.engine import Engine
from omarchy_drive.home_folders import choices, mappings, record_identity, select, set_excluded, validate
from omarchy_drive.panel import reveal
from omarchy_drive.storage import Paths, Store, Blocked, Offline, atomic_json
from tests.test_engine import MemoryDrive, md5


class HomeFolderCase(unittest.TestCase):
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
        self.documents = self.home/'Documents'
        self.documents.mkdir()
        atomic_json(self.paths.settings, {'folder_id': 'root'})
        self.store = Store(self.paths.state)
        self.addCleanup(self.store.close)
        self.drive = MemoryDrive()

    def engine(self):
        return Engine(self.paths, self.paths.configured(), self.store, self.drive)

    def enable(self):
        select(self.paths, 'Documents', True)

    def settings(self, **values):
        atomic_json(self.paths.settings, dict({'folder_id': 'root'}, **values))

    def cache(self, **rows):
        atomic_json(self.paths.state/'panel-cache.json', {'updated': 1, 'folders': [{'name': n, 'where': w} for n, w in rows.items()]})

    def drive_folder(self, name):
        self.drive.files[name] = {'id': 'folder-' + name, 'kind': 'dir', 'hash': None, 'name': name}

    def writes(self):
        return [call for call in self.drive.calls if call[0] != 'download']


class HomeFolderTests(HomeFolderCase):
    def test_two_way_sync_uses_original_location(self):
        (self.documents/'local.txt').write_text('local')
        self.enable()
        self.engine().sync()
        self.assertEqual(self.drive.files['Documents/local.txt']['content'], b'local')
        self.drive.put('Documents/cloud.txt', b'cloud')
        self.engine().sync()
        self.assertEqual((self.documents/'cloud.txt').read_bytes(), b'cloud')
        self.assertEqual(self.engine().sync(), [])

    def test_cloud_deletion_backs_up_original_home_file(self):
        (self.documents/'report.txt').write_text('keep me')
        self.enable()
        self.engine().sync()
        del self.drive.files['Documents/report.txt']
        self.engine().sync()
        self.assertFalse((self.documents/'report.txt').exists())
        copies = list(self.paths.backups.rglob('report.txt'))
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
        with self.assertRaisesRegex(Blocked, 'is missing'):
            self.engine().sync()
        self.documents.mkdir()
        with self.assertRaisesRegex(Blocked, 'replaced'):
            self.engine().sync()
        self.assertEqual(self.drive.files['Documents/report.txt']['content'], b'safe')
        self.assertTrue(next(row for row in choices(self.paths.configured(), self.paths) if row['name'] == 'Documents')['error'])

    def test_hidden_and_symlink_folders_are_rejected(self):
        (self.home/'.ssh').mkdir()
        (self.home/'Link').symlink_to(self.documents)
        (self.home/'GoogleDrive').mkdir()
        (self.home/'file.txt').write_text('a file')
        self.cache(**{'.ssh': 'drive', '.hidden': 'drive', 'file.txt': 'drive'})
        for name in ('.ssh', '.hidden', 'Link', 'file.txt', '../Documents', '/etc'):
            with self.subTest(name=name), self.assertRaises(Blocked):
                select(self.paths, name, True)
        self.assertEqual(choices(self.paths.configured(), self.paths), [
            dict(name='Documents', selected=False, error=''),
            dict(name='GoogleDrive', selected=False, error='')])

    def assert_folder_problem(self, attempt, message):
        with self.assertRaises(Blocked) as caught:
            attempt()
        self.assertEqual(str(caught.exception), message)
        self.assertIsNone(caught.exception.path)  # A path would offer Exclude this, which cannot fix these.

    def test_ticked_folder_problems_offer_unticking(self):
        (self.documents/'report.txt').write_text('safe')
        self.enable()
        self.engine().sync()
        self.documents.rename(self.home/'moved')
        self.assert_folder_problem(self.engine().sync, '~/Documents is missing. Restore it, or untick it to stop syncing.')
        self.documents.write_text('a file now')
        self.assert_folder_problem(self.engine().sync, '~/Documents is not an ordinary folder. Untick it, or restore it as a folder.')
        self.documents.unlink()
        self.documents.mkdir()
        self.assert_folder_problem(self.engine().sync, '~/Documents was replaced. Untick it and tick it again to merge safely.')
        self.assertEqual(self.drive.files['Documents/report.txt']['content'], b'safe')

    def test_ticking_something_that_is_not_a_folder_never_says_restore_alone(self):
        (self.home/'file.txt').write_text('a file')
        (self.home/'Link').symlink_to(self.documents)
        (self.home/'Broken').symlink_to(self.home/'nowhere')
        (self.home/'Mounted').mkdir()
        for name in ('file.txt', 'Link', 'Broken', 'Mounted'):
            with self.subTest(name=name), patch.object(Path, 'is_mount', lambda path: path.name == 'Mounted'):
                self.assert_folder_problem(lambda: select(self.paths, name, True),
                                           f'~/{name} is not an ordinary folder. Untick it, or restore it as a folder.')

    def test_symlinked_home_is_not_an_ordinary_folder(self):
        link = self.home/'.linked-home'
        link.symlink_to(self.home)
        with patch.dict(os.environ, {'HOME': str(link)}):
            self.assert_folder_problem(lambda: validate('Documents', None, self.paths.state),
                                       '~/Documents is not an ordinary folder. Untick it, or restore it as a folder.')

    def test_reserved_marker_name_cannot_be_ticked(self):
        marker = self.home/'RCLONE_TEST'
        marker.mkdir()
        before = self.paths.settings.read_bytes()
        with self.assertRaisesRegex(Blocked, 'RCLONE_TEST'):
            select(self.paths, 'RCLONE_TEST', True)
        self.assertEqual(self.paths.settings.read_bytes(), before)
        row = next(row for row in choices(self.paths.configured(), self.paths) if row['name'] == 'RCLONE_TEST')
        self.assertIn('RCLONE_TEST', row['error'])

    def test_hand_edited_reserved_marker_entry_blocks_sync_without_a_traceback(self):
        marker = self.home/'RCLONE_TEST'
        marker.mkdir()
        (marker/'inner.txt').write_bytes(b'x')
        self.settings(home_folders={'RCLONE_TEST': {}})
        with self.assertRaisesRegex(Blocked, 'RCLONE_TEST'):
            self.engine().sync()
        self.assertEqual(self.drive.calls, [])
        select(self.paths, 'RCLONE_TEST', False)  # Unticking must always work.
        self.assertEqual(self.paths.configured()['home_folders'], {})

    def test_reveal_uses_home_folder_and_rejects_traversal(self):
        self.enable()
        with patch('omarchy_drive.panel.subprocess.run') as run:
            run.return_value.returncode = 0
            reveal(self.paths, 'Documents/report.txt')
            self.assertEqual(run.call_args.args[0], ['xdg-open', str(self.documents)])
            with self.assertRaises(Blocked):
                reveal(self.paths, 'Documents/../outside')

    def test_reveal_walks_up_and_never_launches_a_file(self):
        (self.documents/'sub').mkdir()
        (self.documents/'sub'/'click-me.desktop').write_text('[Desktop Entry]\n')
        self.enable()
        with patch('omarchy_drive.panel.subprocess.run') as run:
            run.return_value.returncode = 0
            reveal(self.paths, 'Documents/sub/click-me.desktop')
            self.assertEqual(run.call_args.args[0], ['xdg-open', str(self.documents/'sub')])
            reveal(self.paths, 'Documents/gone/deep/file.pdf')
            self.assertEqual(run.call_args.args[0], ['xdg-open', str(self.documents)])
            reveal(self.paths, 'Documents')
            self.assertEqual(run.call_args.args[0], ['xdg-open', str(self.documents)])

    def test_reveal_refuses_anything_not_ticked(self):
        (self.home/'Other').mkdir()
        (self.documents/'link').symlink_to(self.home)
        self.enable()
        for path in ('Other', 'Other/a.txt', 'loose.txt', '/etc/passwd', '../outside', 'Documents/link/x'):
            with self.subTest(path=path), patch('omarchy_drive.panel.subprocess.run') as run:
                with self.assertRaises(Blocked):
                    reveal(self.paths, path)
                run.assert_not_called()

    def test_internal_symlink_blocks_sync_before_upload(self):
        self.enable()
        (self.documents/'link').symlink_to(self.home)
        with self.assertRaisesRegex(Blocked, 'Unsupported local file') as caught:
            self.engine().sync()
        self.assertEqual(caught.exception.path, 'Documents/link')
        self.assertEqual(self.drive.calls, [])

    def test_local_deletion_trashes_only_the_mapped_cloud_file(self):
        (self.documents/'report.txt').write_text('tracked')
        self.enable()
        self.engine().sync()
        (self.documents/'report.txt').unlink()
        self.engine().sync()
        self.assertNotIn('Documents/report.txt', self.drive.files)
        self.assertIn(('trash', 'Documents/report.txt'), self.drive.calls)

    def test_mappings_resolve_ticked_folders_only(self):
        (self.home/'Other').mkdir()
        self.enable()
        self.assertEqual(mappings(self.paths.configured(), self.paths), {'Documents': self.documents})

    def test_google_drive_is_an_ordinary_folder(self):
        folder = self.home/'GoogleDrive'
        folder.mkdir()
        (folder/'local.txt').write_text('local')
        self.drive.put('GoogleDrive/cloud.txt', b'cloud')
        row = next(row for row in choices(self.paths.configured(), self.paths) if row['name'] == 'GoogleDrive')
        self.assertEqual(row, dict(name='GoogleDrive', selected=False, error=''))
        select(self.paths, 'GoogleDrive', True)
        self.engine().sync()
        self.assertEqual(self.drive.files['GoogleDrive/local.txt']['content'], b'local')
        self.assertEqual((folder/'cloud.txt').read_text(), 'cloud')

    def test_sync_works_with_google_drive_deleted(self):
        (self.home/'GoogleDrive').mkdir()
        (self.documents/'a.txt').write_text('a')
        self.enable()
        self.engine().sync()
        (self.home/'GoogleDrive').rmdir()
        self.assertEqual(self.engine().sync(), [])
        self.assertFalse([call for call in self.drive.calls if call[0] == 'trash'])
        self.assertFalse((self.home/'GoogleDrive').exists())

    def test_nothing_ticked_sends_no_writes(self):
        (self.documents/'local.txt').write_text('local')
        self.drive.put('Documents/x', b'x')
        self.assertEqual(self.engine().sync(), [])
        self.assertEqual(self.drive.calls, [])
        self.assertEqual(list(self.documents.iterdir()), [self.documents/'local.txt'])

    def test_untick_and_exclude_change_nothing_on_either_side(self):
        (self.documents/'a.txt').write_bytes(b'a')
        (self.documents/'b.txt').write_bytes(b'b')
        self.enable()
        self.engine().sync()
        calls = list(self.drive.calls)
        files = {path: dict(item) for path, item in self.drive.files.items()}

        set_excluded(self.paths, 'Documents/a.txt', True)
        self.assertEqual(self.engine().sync(), [])
        self.assertEqual((self.documents/'a.txt').read_bytes(), b'a')
        (self.documents/'a.txt').write_bytes(b'edited while excluded')
        self.assertEqual(self.engine().sync(), [])
        self.assertEqual((self.documents/'a.txt').read_bytes(), b'edited while excluded')

        select(self.paths, 'Documents', False)
        self.assertEqual(self.engine().sync(), [])
        (self.documents/'b.txt').write_bytes(b'edited while unticked')
        self.assertEqual(self.engine().sync(), [])

        self.assertEqual(self.drive.calls, calls)
        self.assertEqual(self.drive.files, files)
        self.assertEqual((self.documents/'b.txt').read_bytes(), b'edited while unticked')

    def merge_scenario(self, root, hide, show):
        folder = self.home/root
        folder.mkdir(exist_ok=True)
        for name in ('same.txt', 'gone-local.txt', 'gone-drive.txt', 'differs.txt'):
            (folder/name).write_bytes(b'original')
        self.enable()
        self.engine().sync()
        hide()
        self.assertEqual(self.engine().sync(), [])
        (folder/'gone-local.txt').unlink()
        del self.drive.files[root + '/gone-drive.txt']
        (folder/'differs.txt').write_bytes(b'local edit')
        self.drive.calls.clear()
        show()
        self.assertEqual(self.drive.calls, [])  # Changing the selection itself starts no transfer.
        self.engine().sync()
        return folder

    def assert_merged(self, root, folder):
        self.assertEqual(self.store.baseline()[root + '/same.txt']['hash'], md5(b'original'))
        self.assertEqual((folder/'gone-local.txt').read_bytes(), b'original')  # Drive-only: downloaded.
        self.assertEqual(self.drive.files[root + '/gone-drive.txt']['content'], b'original')  # Local-only: uploaded.
        copy = f'differs.conflict-local-{md5(b"local edit")[:12]}.txt'
        self.assertEqual((folder/copy).read_bytes(), b'local edit')  # Differing: both kept.
        self.assertEqual((folder/'differs.txt').read_bytes(), b'original')
        self.assertFalse([call for call in self.drive.calls if call[0] == 'trash'])
        self.assertEqual(self.engine().sync(), [])

    def test_retick_only_merges(self):
        folder = self.merge_scenario('Documents', lambda: select(self.paths, 'Documents', False), self.enable)
        self.assert_merged('Documents', folder)

    def test_reinclude_only_merges(self):
        root = 'Documents/keep'
        folder = self.merge_scenario(root, lambda: set_excluded(self.paths, root, True), lambda: set_excluded(self.paths, root, False))
        self.assert_merged(root, folder)

    def test_identical_file_after_retick_transfers_nothing(self):
        (self.documents/'same.txt').write_bytes(b'same')
        self.enable()
        self.engine().sync()
        select(self.paths, 'Documents', False)
        self.enable()
        self.assertFalse(self.store.baseline())
        self.drive.calls.clear()
        self.assertEqual(self.engine().sync(), [])
        self.assertEqual(self.drive.calls, [])
        self.assertEqual(self.store.baseline()['Documents/same.txt']['hash'], md5(b'same'))

    def test_exclusions_survive_untick(self):
        set_excluded(self.paths, 'Documents/x', True)
        self.enable()
        select(self.paths, 'Documents', False)
        self.assertEqual(self.paths.configured()['excluded'], ['Documents/x'])
        self.enable()
        self.assertEqual(self.paths.configured()['excluded'], ['Documents/x'])

    def test_exclusions_are_sorted_unique_and_exact(self):
        for path in ('Documents/b', 'Documents/a', 'Documents/b', 'Documents/b/inner'):
            set_excluded(self.paths, path, True)
        self.assertEqual(self.paths.configured()['excluded'], ['Documents/a', 'Documents/b', 'Documents/b/inner'])
        set_excluded(self.paths, 'Documents/b', False)
        self.assertEqual(self.paths.configured()['excluded'], ['Documents/a', 'Documents/b/inner'])
        set_excluded(self.paths, 'Documents/never-excluded', False)
        self.assertEqual(self.paths.configured()['excluded'], ['Documents/a', 'Documents/b/inner'])

    def test_exclusion_clears_only_that_subtree_of_the_baseline(self):
        for path in ('Documents/Exam/a', 'Documents/Exam 2/b', 'Documents/Exam', 'Documents/other'):
            self.store.remember(path, {'id': 'x', 'hash': 'y'})
        set_excluded(self.paths, 'Documents/Exam', True)
        self.assertEqual(set(self.store.baseline()), {'Documents/Exam 2/b', 'Documents/other'})

    def test_selection_clears_the_baseline_before_saving(self):
        # A crash between the two must lead to a merge, never a deletion.
        (self.documents/'a.txt').write_bytes(b'a')
        self.enable()
        self.engine().sync()
        for change in (lambda: set_excluded(self.paths, 'Documents/a.txt', True), lambda: select(self.paths, 'Documents', False)):
            self.store.remember('Documents/a.txt', {'id': 'x', 'hash': 'y'})
            before = self.paths.settings.read_bytes()
            with patch('omarchy_drive.home_folders.atomic_json', side_effect=OSError('crash')), self.assertRaises(OSError):
                change()
            self.assertEqual(self.paths.settings.read_bytes(), before)
            self.assertFalse(self.store.baseline())
        self.drive.calls.clear()
        (self.documents/'a.txt').unlink()
        self.engine().sync()
        self.assertFalse(self.writes())
        self.assertEqual((self.documents/'a.txt').read_bytes(), b'a')  # Merged back, never trashed.

    def test_selection_changes_delete_the_folder_list_cache(self):
        for change in (self.enable, lambda: set_excluded(self.paths, 'Documents/a', True), lambda: select(self.paths, 'Documents', False)):
            self.cache(Other='drive')
            change()
            self.assertFalse((self.paths.state/'panel-cache.json').exists())

    def test_selection_changes_refuse_during_a_sync(self):
        before = self.paths.settings.read_bytes()
        with self.paths.lock():
            for change in (self.enable, lambda: select(self.paths, 'Documents', False), lambda: set_excluded(self.paths, 'Documents/a', True)):
                with self.assertRaisesRegex(Blocked, 'Another sync'):
                    change()
        self.assertEqual(self.paths.settings.read_bytes(), before)

    def test_excluded_problem_items_do_not_block(self):
        bad = self.documents/'bad'
        bad.mkdir()
        (bad/'link').symlink_to(self.home)  # Points at a folder, so the walk reports it before the FIFO.
        os.mkfifo(bad/'pipe')
        locked = bad/'locked'
        locked.mkdir()
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)
        self.enable()
        set_excluded(self.paths, 'Documents/bad', True)
        (self.documents/'good.txt').write_bytes(b'good')
        self.engine().sync()
        self.assertEqual(self.drive.files['Documents/good.txt']['content'], b'good')
        self.assertFalse([path for path in self.drive.files if 'bad' in path])
        set_excluded(self.paths, 'Documents/bad', False)
        with self.assertRaises(Blocked) as caught:
            self.engine().sync()
        self.assertEqual(caught.exception.path, 'Documents/bad/link')

    def test_problems_blocked_with_their_path_when_included(self):
        bad = self.documents/'bad'
        bad.mkdir()
        os.mkfifo(bad/'pipe')
        self.enable()
        with self.assertRaisesRegex(Blocked, 'Documents/bad/pipe') as caught:
            self.engine().sync()
        self.assertEqual(caught.exception.path, 'Documents/bad/pipe')
        self.assertEqual(self.drive.calls, [])

    @unittest.skipIf(os.geteuid() == 0, 'root can read every folder')
    def test_unreadable_folder_only_blocks_when_included(self):
        locked = self.documents/'locked'
        locked.mkdir()
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)
        self.enable()
        with self.assertRaisesRegex(Blocked, 'could not be read') as caught:
            self.engine().sync()
        self.assertEqual(caught.exception.path, 'Documents/locked')
        self.assertEqual(self.drive.calls, [])
        set_excluded(self.paths, 'Documents/locked', True)
        self.engine().sync()

    def test_excluded_content_is_not_hashed(self):
        (self.documents/'skip').mkdir()
        hidden = self.documents/'skip'/'large.bin'
        hidden.write_bytes(b'x')
        secret = self.documents/'secret.bin'
        secret.write_bytes(b'y')
        kept = self.documents/'kept.bin'
        kept.write_bytes(b'z')
        self.enable()
        set_excluded(self.paths, 'Documents/skip', True)
        set_excluded(self.paths, 'Documents/secret.bin', True)
        with patch('omarchy_drive.engine.digest', wraps=digest) as hashed:
            self.engine().sync()
        seen = {Path(call.args[0]) for call in hashed.call_args_list}
        self.assertIn(kept, seen)
        self.assertNotIn(hidden, seen)
        self.assertNotIn(secret, seen)

    def test_drive_files_under_an_exclusion_are_not_downloaded(self):
        self.enable()
        self.drive.put('Documents/skip/cloud.txt', b'cloud')
        self.drive.put('Documents/kept.txt', b'kept')
        set_excluded(self.paths, 'Documents/skip', True)
        self.engine().sync()
        self.assertFalse((self.documents/'skip').exists())
        self.assertEqual((self.documents/'kept.txt').read_bytes(), b'kept')

    def test_drive_only_folder_is_created_and_identity_recorded(self):
        self.cache(Archive='drive')
        self.drive_folder('Archive')
        self.drive.put('Archive/x.txt', b'x')
        select(self.paths, 'Archive', True)
        self.assertEqual(self.paths.configured()['home_folders'], {'Archive': {'identity': None}})
        self.assertFalse((self.home/'Archive').exists())  # Ticking alone creates nothing.
        self.assertEqual(self.drive.calls, [])
        self.engine().sync()
        archive = self.home/'Archive'
        self.assertEqual((archive/'x.txt').read_bytes(), b'x')
        info = archive.stat()
        self.assertEqual(self.paths.configured()['home_folders']['Archive'], {'identity': [info.st_dev, info.st_ino]})
        self.assertEqual(self.engine().sync(), [])

    def test_drive_only_folder_replaced_after_creation_is_blocked(self):
        self.cache(Archive='drive')
        self.drive_folder('Archive')
        self.drive.put('Archive/x.txt', b'x')
        select(self.paths, 'Archive', True)
        self.engine().sync()
        (self.home/'Archive').rename(self.home/'moved')
        (self.home/'Archive').mkdir()
        self.drive.calls.clear()
        with self.assertRaisesRegex(Blocked, 'replaced'):
            self.engine().sync()
        self.assertEqual(self.drive.calls, [])
        self.assertIn('Archive/x.txt', self.drive.files)

    def test_ticked_name_that_is_not_a_folder_on_drive_stops_before_any_action(self):
        unsupported = {'id': 'doc', 'kind': 'unsupported', 'hash': None, 'name': 'Archive'}
        for label, arrange in (('file', lambda: self.drive.put('Archive', b'not a folder')),
                               ('google-doc', lambda: self.drive.files.update(Archive=unsupported))):
            for local_folder in (False, True):
                with self.subTest(drive=label, local_folder=local_folder):
                    self.drive.files.clear()
                    arrange()
                    if local_folder:
                        (self.home/'Archive').mkdir()
                    self.settings(home_folders={'Archive': {'identity': None}})
                    drive_before = dict(self.drive.files)
                    settings_before = self.paths.settings.read_bytes()
                    with self.assertRaisesRegex(Blocked, 'Archive') as caught:
                        self.engine().sync()
                    self.assertEqual(caught.exception.path, 'Archive')
                    self.assertEqual(self.drive.files, drive_before)
                    self.assertEqual(self.drive.calls, [])
                    self.assertEqual(self.paths.settings.read_bytes(), settings_before)
                    if local_folder:
                        self.assertEqual(list((self.home/'Archive').iterdir()), [])
                        (self.home/'Archive').rmdir()
                    self.assertFalse(os.path.lexists(self.home/'Archive'))

    def test_drive_only_tick_of_a_drive_file_also_stops_in_preview(self):
        self.drive.put('Archive', b'not a folder')
        self.settings(home_folders={'Archive': {'identity': None}})
        with self.assertRaises(Blocked) as caught:
            self.engine().snapshot()
        self.assertEqual(caught.exception.path, 'Archive')

    def test_folder_created_by_hand_after_a_drive_only_tick_merges(self):
        self.cache(Archive='drive')
        self.drive_folder('Archive')
        self.drive.put('Archive/cloud.txt', b'cloud')
        select(self.paths, 'Archive', True)
        archive = self.home/'Archive'
        archive.mkdir()
        (archive/'local.txt').write_bytes(b'local')
        self.engine().sync()
        self.assertEqual((archive/'cloud.txt').read_bytes(), b'cloud')
        self.assertEqual(self.drive.files['Archive/local.txt']['content'], b'local')
        info = archive.stat()
        self.assertEqual(self.paths.configured()['home_folders']['Archive'], {'identity': [info.st_dev, info.st_ino]})
        # The recorded identity is what makes a later replacement a stop rather than mass deletion.
        archive.rename(self.home/'moved')
        archive.mkdir()
        with self.assertRaisesRegex(Blocked, 'replaced'):
            self.engine().sync()

    def test_drive_only_tick_without_cached_listing_is_actionable(self):
        before = self.paths.settings.read_bytes()
        with self.assertRaisesRegex(Blocked, 'Refresh'):
            select(self.paths, 'Archive', True)
        self.assertEqual(self.paths.settings.read_bytes(), before)

    def test_drive_only_tick_needs_a_drive_row_in_the_cache(self):
        for rows in (dict(Archive='home'), dict(Archive='both'), dict(Other='drive')):
            self.cache(**rows)
            with self.subTest(rows=rows), self.assertRaisesRegex(Blocked, 'Refresh'):
                select(self.paths, 'Archive', True)

    def test_ticked_folder_that_went_missing_cannot_become_drive_only(self):
        self.enable()
        self.documents.rmdir()
        self.cache(Documents='drive')
        with self.assertRaisesRegex(Blocked, 'is missing'):
            self.enable()
        select(self.paths, 'Documents', False)  # Unticking must always work.
        self.assertEqual(self.paths.configured()['home_folders'], {})

    def test_excluding_a_whole_folder_is_rejected(self):
        before = self.paths.settings.read_bytes()
        with self.assertRaises(Blocked):
            set_excluded(self.paths, 'Documents', True)
        self.assertEqual(self.paths.settings.read_bytes(), before)

    def test_unsafe_exclusion_paths_are_rejected(self):
        for path in ('', '/etc/passwd', 'Documents/../x', 'Documents/./x', 'Documents//x', 'Documents/', 'Documents/a\x00'):
            with self.subTest(path=path), self.assertRaises(Blocked):
                set_excluded(self.paths, path, True)

    def test_unusual_names_tick_and_exclude_exactly(self):
        (self.home/"Bob's Files").mkdir()
        (self.home/"Bob's Files"/'Résumé 100%_final.pdf').write_bytes(b'r')
        (self.home/"Bob's Files"/'Résumé 100%Xfinal.pdf').write_bytes(b'k')
        select(self.paths, "Bob's Files", True)
        set_excluded(self.paths, "Bob's Files/Résumé 100%_final.pdf", True)
        self.engine().sync()
        self.assertNotIn("Bob's Files/Résumé 100%_final.pdf", self.drive.files)
        self.assertEqual(self.drive.files["Bob's Files/Résumé 100%Xfinal.pdf"]['content'], b'k')

    def test_entry_without_identity_key_does_not_crash(self):
        self.settings(home_folders={'Documents': {}})
        (self.documents/'a.txt').write_bytes(b'a')
        self.engine().sync()
        info = self.documents.stat()
        self.assertEqual(self.paths.configured()['home_folders']['Documents'], {'identity': [info.st_dev, info.st_ino]})
        self.assertEqual(self.drive.files['Documents/a.txt']['content'], b'a')

    def test_entry_without_identity_never_deletes_from_stale_history(self):
        # Nothing proves stale rows describe this folder, so both sides merge.
        for identity in ({}, {'identity': None}):
            with self.subTest(identity=identity):
                self.drive.calls.clear()
                self.drive_folder('Documents')
                self.drive.put('Documents/gone-local.txt', b'cloud')
                row = self.drive.files['Documents/gone-local.txt']
                self.store.remember('Documents/gone-local.txt', {'id': row['id'], 'hash': row['hash']})
                (self.documents/'gone-drive.txt').write_bytes(b'local')
                self.store.remember('Documents/gone-drive.txt', {'id': 'old', 'hash': md5(b'local')})
                self.settings(home_folders={'Documents': identity})
                self.engine().sync()
                self.assertFalse([call for call in self.drive.calls if call[0] == 'trash'])
                self.assertEqual((self.documents/'gone-local.txt').read_bytes(), b'cloud')
                self.assertEqual(self.drive.files['Documents/gone-drive.txt']['content'], b'local')
                self.assertEqual(self.store.baseline()['Documents/gone-drive.txt']['id'], self.drive.files['Documents/gone-drive.txt']['id'])
                for name in ('gone-local.txt', 'gone-drive.txt'):
                    (self.documents/name).unlink()
                    self.drive.files.pop('Documents/' + name, None)
                    self.store.remember('Documents/' + name, None)

    def test_entry_without_identity_and_folder_missing_never_trashes(self):
        self.documents.rmdir()
        self.drive_folder('Documents')
        self.drive.put('Documents/x.txt', b'x')
        row = self.drive.files['Documents/x.txt']
        self.store.remember('Documents/x.txt', {'id': row['id'], 'hash': row['hash']})
        self.settings(home_folders={'Documents': {}})
        self.engine().sync()
        self.assertFalse([call for call in self.drive.calls if call[0] == 'trash'])
        self.assertEqual((self.documents/'x.txt').read_bytes(), b'x')

    def test_interrupted_merge_cannot_reuse_stale_history(self):
        # The identity is saved before the merge finishes, so the stale rows must already be gone.
        self.drive_folder('Documents')
        self.drive.put('Documents/x.txt', b'x')
        row = self.drive.files['Documents/x.txt']
        self.store.remember('Documents/x.txt', {'id': row['id'], 'hash': row['hash']})
        self.settings(home_folders={'Documents': {}})
        def lost_connection():
            raise Offline('Connection lost')
        self.drive.before_download = lost_connection
        with self.assertRaises(Offline):
            self.engine().sync()
        self.assertTrue(self.paths.configured()['home_folders']['Documents']['identity'])
        self.assertFalse(self.store.baseline())
        self.engine().sync()
        self.assertFalse([call for call in self.drive.calls if call[0] == 'trash'])
        self.assertEqual((self.documents/'x.txt').read_bytes(), b'x')

    def test_history_outside_the_selection_is_ignored(self):
        # Rows for an excluded path or an unticked folder never reach plan, not even as a 'forget'.
        info = self.documents.stat()
        self.settings(home_folders={'Documents': {'identity': [info.st_dev, info.st_ino]}}, excluded=['Documents/skip'])
        rows = {'Documents/skip/a': {'id': 'x', 'hash': 'y'}, 'Other/b': {'id': 'x', 'hash': 'y'}, 'loose': {'id': 'x', 'hash': 'y'}}
        for path, row in rows.items():
            self.store.remember(path, row)
        self.assertEqual([(action.kind, action.path) for action in self.engine().sync()], [('mkdir-remote', 'Documents')])
        self.assertEqual(self.store.baseline(), rows)

    def test_preview_records_no_identity_on_disk(self):
        self.settings(home_folders={'Documents': {}})
        before = self.paths.settings.read_bytes()
        self.store.remember('Documents/x', {'id': 'x', 'hash': 'y'})
        self.engine().snapshot()
        self.assertEqual(self.paths.settings.read_bytes(), before)
        self.assertEqual(set(self.store.baseline()), {'Documents/x'})

    def test_blocked_sync_records_no_identity(self):
        self.settings(home_folders={'Documents': {}})
        (self.documents/'link').symlink_to(self.home)
        before = self.paths.settings.read_bytes()
        with self.assertRaises(Blocked):
            self.engine().sync()
        self.assertEqual(self.paths.settings.read_bytes(), before)

    def test_malformed_entries_block_without_a_traceback(self):
        for entry in ('text', 7, ['list'], True):
            self.settings(home_folders={'Documents': entry})
            with self.subTest(entry=entry), self.assertRaisesRegex(Blocked, 'settings'):
                self.engine().sync()
        self.assertEqual(self.drive.calls, [])

    def test_wrong_identity_shape_blocks(self):
        self.settings(home_folders={'Documents': {'identity': 'nonsense'}})
        with self.assertRaisesRegex(Blocked, 'replaced'):
            self.engine().sync()

    def test_hand_excluded_whole_folder_sends_no_writes(self):
        info = self.documents.stat()
        self.settings(home_folders={'Documents': {'identity': [info.st_dev, info.st_ino]}}, excluded=['Documents'])
        (self.documents/'a.txt').write_bytes(b'a')
        self.drive.put('Documents/b.txt', b'b')
        self.assertEqual(self.engine().sync(), [])
        self.assertEqual(self.drive.calls, [])

    def test_stale_and_malformed_exclusions(self):
        self.enable()
        settings = self.paths.configured()
        self.settings(home_folders=settings['home_folders'], excluded=['Documents/gone/forever'])
        (self.documents/'a.txt').write_bytes(b'a')
        self.engine().sync()
        self.assertIn('Documents/a.txt', self.drive.files)
        self.settings(home_folders=settings['home_folders'], excluded=[7])
        self.drive.calls.clear()
        with self.assertRaisesRegex(Blocked, 'invalid exclusion'):
            self.engine().sync()
        with self.assertRaisesRegex(Blocked, 'invalid exclusion'):
            set_excluded(self.paths, 'Documents/x', True)
        self.assertEqual(self.drive.calls, [])

    def state_on_another_disk(self):
        real, state = Path.stat, self.paths.state
        def fake(candidate, **kwargs):
            info = real(candidate, **kwargs)
            if candidate == state:
                return SimpleNamespace(st_dev=info.st_dev + 1, st_ino=info.st_ino, st_mode=info.st_mode)
            return info
        return patch.object(Path, 'stat', fake)

    def test_home_folder_on_another_filesystem_is_blocked(self):
        with self.state_on_another_disk(), self.assertRaisesRegex(Blocked, 'same disk'):
            self.enable()
        self.assertNotIn('home_folders', self.paths.configured())

    def test_drive_only_folder_on_another_filesystem_is_blocked_before_creation(self):
        self.cache(Archive='drive')
        with self.state_on_another_disk(), self.assertRaisesRegex(Blocked, 'same disk'):
            select(self.paths, 'Archive', True)
        self.assertNotIn('home_folders', self.paths.configured())

    def test_validate_entry_rules(self):
        state = self.paths.state
        self.assertEqual(validate('Documents', None, state), self.documents)
        for entry in (None, {'identity': [1, 2]}):
            with self.subTest(entry=entry), self.assertRaises(Blocked):
                validate('Missing', entry, state)
        for entry in ({}, {'identity': None}):
            with self.subTest(entry=entry):
                self.assertEqual(validate('Missing', entry, state), self.home/'Missing')
        (self.home/'Broken').symlink_to(self.home/'nowhere')
        (self.home/'file.txt').write_text('x')
        for name in ('Broken', 'file.txt', 'Documents/sub', '.hidden'):
            with self.subTest(name=name), self.assertRaises(Blocked):
                validate(name, {}, state)

    def test_record_identity_on_a_settings_dict(self):
        settings = {}
        record_identity(settings, 'Documents', self.documents)
        info = self.documents.stat()
        self.assertEqual(settings, {'home_folders': {'Documents': {'identity': [info.st_dev, info.st_ino]}}})
        json.dumps(settings)

    def test_choices_keep_a_problem_row_visible_with_its_reason(self):
        self.enable()
        self.documents.rmdir()
        rows = choices(self.paths.configured(), self.paths)
        self.assertEqual([row['name'] for row in rows], ['Documents'])
        self.assertTrue(rows[0]['selected'])
        self.assertEqual(rows[0]['error'], '~/Documents is missing. Restore it, or untick it to stop syncing.')

    def test_choices_list_a_drive_only_tick_without_error(self):
        self.cache(Archive='drive')
        select(self.paths, 'Archive', True)
        row = next(row for row in choices(self.paths.configured(), self.paths) if row['name'] == 'Archive')
        self.assertEqual(row, dict(name='Archive', selected=True, error=''))

    def test_backups_go_to_state_directory(self):
        (self.documents/'report.txt').write_text('keep me')
        self.enable()
        self.engine().sync()
        del self.drive.files['Documents/report.txt']
        self.engine().sync()
        backups = self.paths.state/'backups'
        self.assertEqual(len(list(backups.rglob('report.txt'))), 1)
        self.assertEqual(stat.S_IMODE(backups.stat().st_mode), 0o700)
        self.assertFalse([path for path in self.home.iterdir() if path.name.endswith('-omarchy-drive-backups')])

    def test_old_backup_folder_is_left_alone(self):
        old = self.home/'.GoogleDrive-omarchy-drive-backups'
        old.mkdir()
        (old/'kept').write_text('old')
        (self.documents/'report.txt').write_text('keep me')
        self.enable()
        self.engine().sync()
        del self.drive.files['Documents/report.txt']
        self.engine().sync()
        self.assertEqual([path.name for path in old.iterdir()], ['kept'])
