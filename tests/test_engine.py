import copy
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from omarchy_drive.drive import FOLDER
from omarchy_drive.engine import Engine, UNREADABLE, plan, scan_local
from omarchy_drive.home_folders import identity_of, record_identity
from omarchy_drive.storage import Blocked, Offline, Paths, Store, read_json


def md5(data):
    return hashlib.md5(data).hexdigest()


class MemoryDrive:
    """Drive model at the API boundary; engine and filesystem are real."""
    def __init__(self):
        self.files = {}
        self.serial = 0
        self.calls = []
        self.fail_after_upload = False
        self.before_download = None
        self.root_exists = True
        self.unusable = {}
        self.listing = lambda path: None

    def put(self, path, data, file_id=None):
        self.serial += 1
        item = dict(id=file_id or f'file-{self.serial}', kind='file', hash=md5(data),
                    md5Checksum=md5(data), size=len(data), version=str(self.serial), name=Path(path).name)
        self.files[path] = dict(item, content=data)
        return item

    def scan(self, root, included=None, skipped=None):
        if not self.root_exists:
            raise Blocked('Remote root is missing')
        if skipped is not None:
            skipped.update(self.unusable)
        listed = {path: {k: v for k, v in item.items() if k != 'content'} for path, item in self.files.items()
                  if included is None or included(path)}
        for path in listed:
            self.listing(path)
        return listed

    def mkdir(self, parent, name, key):
        self.serial += 1
        prefix = next((p for p, i in self.files.items() if i['id'] == parent), '')
        path = str(Path(prefix) / name)
        item = dict(id=f'dir-{self.serial}', name=name, kind='dir', hash=None)
        self.files[path] = item
        self.calls.append(('mkdir', path))
        return item

    def download(self, item, target):
        if self.before_download:
            action, self.before_download = self.before_download, None
            action()
        current = next(i for i in self.files.values() if i['id'] == item['id'])
        if current['hash'] != item['hash']:
            raise Blocked('Remote content changed')
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(current['content'])
        self.calls.append(('download', item['id']))
        return target

    def upload(self, source, parent, name, checksum, existing=None):
        prefix = next((p for p, i in self.files.items() if i['id'] == parent), '')
        path = str(Path(prefix) / name)
        item = self.put(path, Path(source).read_bytes(), existing['id'] if existing else None)
        self.calls.append(('upload', path))
        if self.fail_after_upload:
            self.fail_after_upload = False
            raise Offline('Response lost after Google accepted the upload')
        return item

    def trash(self, item):
        path = next(p for p, i in self.files.items() if i['id'] == item['id'])
        self.calls.append(('trash', path))
        del self.files[path]


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / 'home'
        self.local = self.home / 'Sync'
        self.local.mkdir(parents=True)
        # Patch before Paths(): it derives settings and backups from these, and the engine writes both.
        environment = patch.dict(os.environ, {
            'HOME': str(self.home), 'XDG_CONFIG_HOME': str(self.base / 'config'),
            'XDG_STATE_HOME': str(self.base / 'state'), 'XDG_DATA_HOME': str(self.base / 'data')})
        environment.start()
        self.addCleanup(environment.stop)
        self.paths = Paths()
        self.paths.prepare()
        info = self.local.stat()
        self.settings = {'folder_id': 'root', 'max_deletes': 20, 'home_folders': {'Sync': {'identity': [info.st_dev, info.st_ino]}}}
        self.store = Store(self.paths.state)
        self.addCleanup(self.store.close)
        self.drive = MemoryDrive()

    def engine(self):
        return Engine(self.paths, self.settings, self.store, self.drive)

    def write(self, path, content):
        target = self.local / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def test_gitignore_excludes_both_sides_and_reinclusion_merges(self):
        self.write('secret.log', b'original')
        self.engine().sync()
        self.write('.gitignore', b'*.log\ncache/\n')
        self.write('secret.log', b'local edit')
        self.write('cache/large', b'ignored')
        self.drive.put('Sync/cloud.log', b'cloud')
        actions = self.engine().sync()
        self.assertFalse(any(a.path.endswith('.log') or '/cache' in a.path for a in actions))
        self.assertEqual(self.drive.files['Sync/secret.log']['content'], b'original')
        self.assertFalse((self.local / 'cloud.log').exists())
        self.assertNotIn('Sync/secret.log', self.store.baseline())
        # Removing an ignored local file must not delete the old Drive copy on re-inclusion.
        (self.local / 'secret.log').unlink()
        self.write('.gitignore', b'')
        self.engine().sync()
        self.assertEqual((self.local / 'secret.log').read_bytes(), b'original')
        self.assertEqual((self.local / 'cloud.log').read_bytes(), b'cloud')

    def test_gitignore_nested_rules_and_negation_without_git_repository(self):
        self.write('.gitignore', b'*.log\n!keep.log\nbuild/\n/root.txt\n')
        self.write('project/.gitignore', b'*.tmp\n!important.tmp\n')
        for path in ('drop.log', 'keep.log', 'build/file', 'root.txt', 'project/root.txt',
                     'project/drop.tmp', 'project/important.tmp'):
            self.write(path, b'data')
        self.engine().sync()
        for path in ('drop.log', 'build/file', 'root.txt', 'project/drop.tmp'):
            self.assertNotIn('Sync/' + path, self.drive.files)
        for path in ('keep.log', 'project/root.txt', 'project/important.tmp', 'project/.gitignore'):
            self.assertIn('Sync/' + path, self.drive.files)

    def test_gitignored_conflict_copy_blocks_before_mutation(self):
        self.write('a', b'local')
        self.write('.gitignore', b'*.conflict-local-*\n')
        self.drive.put('Sync/a', b'cloud')
        with self.assertRaisesRegex(Blocked, 'excluded'):
            self.engine().sync()
        self.assertEqual((self.local / 'a').read_bytes(), b'local')

    def test_first_run_merges_both_trees_and_then_converges(self):
        self.write('local.txt', b'local')
        self.drive.put('Sync/cloud.txt', b'cloud')
        self.engine().sync()
        self.assertEqual((self.local / 'cloud.txt').read_bytes(), b'cloud')
        self.assertEqual(self.drive.files['Sync/local.txt']['content'], b'local')
        self.assertEqual(self.engine().sync(), [])
        self.assertEqual(len(self.store.baseline()), 2)
        data = read_json(self.paths.status)
        self.assertEqual(data['summary']['uploaded'], 0)
        self.assertEqual(sorted((row['action'], row['path']) for row in data['recent']),
                         [('download', 'Sync/cloud.txt'), ('mkdir-remote', 'Sync'), ('upload', 'Sync/local.txt')])

    def test_conflicting_initial_files_keep_both_versions(self):
        self.write('notes.txt', b'local notes')
        self.drive.put('Sync/notes.txt', b'cloud notes')
        self.engine().sync()
        copy = f'notes.conflict-local-{md5(b"local notes")[:12]}.txt'
        self.assertEqual((self.local / 'notes.txt').read_bytes(), b'cloud notes')
        self.assertEqual((self.local / copy).read_bytes(), b'local notes')
        self.assertEqual(self.drive.files['Sync/' + copy]['content'], b'local notes')
        data = read_json(self.paths.status)
        self.assertEqual(data['summary']['uploaded'], 1)
        self.assertEqual(data['summary']['downloaded'], 1)
        self.assertEqual(data['summary']['conflicts'], 1)
        self.assertEqual(self.engine().sync(), [])

    def test_both_sides_edit_after_success_keep_both_versions(self):
        self.write('a', b'baseline')
        self.engine().sync()
        self.write('a', b'local edit')
        self.drive.put('Sync/a', b'remote edit', self.drive.files['Sync/a']['id'])
        self.engine().sync()
        self.assertEqual((self.local / 'a').read_bytes(), b'remote edit')
        self.assertTrue(any(p.read_bytes() == b'local edit' for p in self.local.iterdir()))
        self.assertEqual(self.engine().sync(), [])

    def test_one_sided_edits_and_backups(self):
        self.write('a', b'baseline')
        self.engine().sync()
        self.write('a', b'local edit')
        self.engine().sync()
        self.assertEqual(self.drive.files['Sync/a']['content'], b'local edit')
        self.drive.put('Sync/a', b'remote edit', self.drive.files['Sync/a']['id'])
        self.engine().sync()
        self.assertEqual((self.local / 'a').read_bytes(), b'remote edit')
        backups = list(self.engine().archive_root.rglob('*'))
        contents = [p.read_bytes() for p in backups if p.is_file()]
        self.assertIn(b'baseline', contents)
        self.assertIn(b'local edit', contents)

    def test_remote_deletion_archives_local_copy(self):
        self.write('a', b'keep in backup')
        self.engine().sync()
        del self.drive.files['Sync/a']
        self.engine().sync()
        self.assertFalse((self.local / 'a').exists())
        self.assertFalse(self.store.baseline())
        self.assertTrue(any(p.read_bytes() == b'keep in backup' for p in self.engine().archive_root.rglob('*') if p.is_file()))

    def test_local_deletion_trashes_remote_and_keeps_backup(self):
        self.write('a', b'keep')
        self.engine().sync()
        (self.local / 'a').unlink()
        self.engine().sync()
        self.assertNotIn('Sync/a', self.drive.files)
        self.assertIn(('trash', 'Sync/a'), self.drive.calls)
        self.assertFalse(self.store.baseline())

    def test_deleted_on_one_side_edited_on_other_preserves_edits(self):
        self.write('a', b'old')
        self.engine().sync()
        self.write('a', b'new local')
        del self.drive.files['Sync/a']
        self.engine().sync()
        self.assertEqual(self.drive.files['Sync/a']['content'], b'new local')
        (self.local / 'a').unlink()
        self.drive.put('Sync/a', b'new cloud', self.drive.files['Sync/a']['id'])
        self.engine().sync()
        self.assertEqual((self.local / 'a').read_bytes(), b'new cloud')

    def test_lost_upload_response_reconciles_without_duplicates(self):
        self.write('a', b'content')
        self.drive.fail_after_upload = True
        with self.assertRaises(Offline):
            self.engine().sync()
        self.assertFalse(self.store.baseline())
        self.engine().sync()
        self.assertEqual(sorted(self.drive.files), ['Sync', 'Sync/a'])
        self.assertEqual(self.store.baseline()['Sync/a']['hash'], md5(b'content'))

    def test_local_edit_during_download_is_not_overwritten(self):
        self.write('a', b'base')
        self.engine().sync()
        self.drive.put('Sync/a', b'cloud edit', self.drive.files['Sync/a']['id'])
        self.drive.before_download = lambda: self.write('a', b'editing now')
        with self.assertRaises(Blocked):
            self.engine().sync()
        self.assertEqual((self.local / 'a').read_bytes(), b'editing now')
        self.assertEqual(self.store.baseline()['Sync/a']['hash'], md5(b'base'))

    def test_scan_failure_does_not_mutate_either_tree(self):
        self.write('a', b'a')
        self.drive.root_exists = False
        with self.assertRaises(Blocked):
            self.engine().sync()
        self.assertEqual(self.drive.calls, [])
        self.assertEqual((self.local / 'a').read_bytes(), b'a')

    def test_preview_does_not_transfer_or_change_baseline(self):
        self.write('a', b'a')
        self.drive.put('Sync/b', b'b')
        actions = self.engine().snapshot()
        self.assertEqual(sorted((a.kind, a.path) for a in actions),
                         [('download', 'Sync/b'), ('mkdir-remote', 'Sync'), ('upload', 'Sync/a')])
        self.assertEqual(self.drive.calls, [])
        self.assertFalse(self.store.baseline())
        self.assertFalse((self.local / 'b').exists())

    def test_recreated_local_root_stops_instead_of_deleting_cloud(self):
        self.write('a', b'a')
        self.engine().sync()
        self.local.rename(self.base / 'old')
        self.local.mkdir()
        with self.assertRaises(Blocked):
            self.engine().sync()
        self.assertIn('Sync/a', self.drive.files)

    def test_nested_folders_create_before_file_transfers(self):
        self.write('nested/deep/a.txt', b'a')
        self.engine().sync()
        self.assertEqual(self.drive.files['Sync/nested/deep/a.txt']['content'], b'a')
        self.assertEqual([call for call in self.drive.calls if call[0] == 'mkdir'],
                         [('mkdir', 'Sync'), ('mkdir', 'Sync/nested'), ('mkdir', 'Sync/nested/deep')])

    def test_type_conflicts_block_before_transfer(self):
        self.write('folder', b'file')
        self.drive.files['Sync/folder'] = {'id': 'folder', 'kind': 'dir', 'hash': None}
        with self.assertRaises(Blocked):
            self.engine().sync()
        self.assertEqual(self.drive.calls, [])

    def test_deletion_limit_applies_before_any_mutation(self):
        for i in range(24):
            self.write(str(i), b'a')
        self.engine().sync()
        for i in range(7):
            (self.local / str(i)).unlink()
        self.drive.calls.clear()
        with self.assertRaises(Blocked):
            self.engine().sync()
        self.assertFalse(self.drive.calls)
        self.assertEqual(len(self.drive.files), 25)  # 24 files and the Sync folder.
        self.engine().sync(allow_many_deletes=True)
        self.assertEqual(len(self.drive.files), 18)

    def test_conflict_copy_collision_never_overwrites_other_content(self):
        self.write('a.txt', b'local')
        self.drive.put('Sync/a.txt', b'remote')
        self.write(f'a.conflict-local-{md5(b"local")[:12]}.txt', b'other')
        with self.assertRaises(Blocked):
            self.engine().sync()
        self.assertEqual((self.local / 'a.txt').read_bytes(), b'local')

    def test_new_file_appearing_at_download_publish_is_not_overwritten(self):
        import os
        self.drive.put('Sync/a', b'cloud')
        real_link = os.link
        def race(source, target, *args, **kwargs):
            Path(target).write_bytes(b'new local edit')
            return real_link(source, target, *args, **kwargs)
        with patch('omarchy_drive.engine.os.link', race), self.assertRaises(Blocked):
            self.engine().sync()
        self.assertEqual((self.local / 'a').read_bytes(), b'new local edit')
        self.assertFalse(self.store.baseline())

    def test_local_original_inode_is_retained_when_cloud_replaces_it(self):
        self.write('a', b'base')
        self.engine().sync()
        handle = (self.local / 'a').open('r+b')
        try:
            self.drive.put('Sync/a', b'cloud edit', self.drive.files['Sync/a']['id'])
            self.engine().sync()
            handle.seek(0)
            handle.write(b'late edit from open application')
            handle.flush()
        finally:
            handle.close()
        self.assertEqual((self.local / 'a').read_bytes(), b'cloud edit')
        originals = list(self.engine().archive_root.rglob('a.original'))
        self.assertTrue(any(p.read_bytes() == b'late edit from open application' for p in originals))

    def test_only_a_top_level_marker_is_ignored(self):
        # Ruling R4: the comparison is on the full path, so the marker at the old root stays on Drive.
        self.drive.put('RCLONE_TEST', b'marker')
        self.write('RCLONE_TEST', b'ordinary file')
        self.engine().sync()
        self.assertFalse((self.home / 'RCLONE_TEST').exists())
        self.assertEqual(self.drive.files['RCLONE_TEST']['content'], b'marker')
        self.assertEqual(self.drive.files['Sync/RCLONE_TEST']['content'], b'ordinary file')
        self.assertEqual(scan_local(self.local), {})

    def test_scan_local_prefixes_keys_and_prunes_before_any_check(self):
        self.write('keep/a.txt', b'a')
        self.write('skip/b.txt', b'b')
        (self.local / 'skip' / 'link').symlink_to(self.base)
        (self.local / 'keep' / 'bad').symlink_to(self.base)
        included = lambda path: not path.startswith('Sync/skip') and path != 'Sync/keep/bad'
        result = scan_local(self.local, 'Sync/', included)
        self.assertEqual(set(result), {'Sync/keep', 'Sync/keep/a.txt'})
        skipped = {}
        scan_local(self.local, 'Sync/', lambda path: not path.startswith('Sync/skip'), skipped)
        self.assertEqual(list(skipped), ['Sync/keep/bad'])

    def test_scan_local_reports_each_file_before_reading_it_and_nothing_pruned(self):
        self.write('keep/a.txt', b'a')
        self.write('b.txt', b'b')
        self.write('skip/c.txt', b'c')
        seen = []
        scan_local(self.local, 'Sync/', lambda path: not path.startswith('Sync/skip'),
                   progress=lambda path, size: seen.append((path, size)))
        self.assertEqual(sorted(seen), [('Sync/b.txt', 1), ('Sync/keep/a.txt', 1)])

    def test_store_identity_is_the_drive_folder_only(self):
        self.engine().snapshot()
        self.assertEqual(self.store.stored_identity(), 'root')
        self.settings['folder_id'] = 'another-folder'
        with self.assertRaisesRegex(Blocked, 'changed'):
            self.engine().snapshot()

    def test_archive_root_is_the_state_backup_directory(self):
        self.assertEqual(self.engine().archive_root, self.paths.backups)

    def test_local_target_refuses_an_unticked_folder(self):
        (self.home / 'Other').mkdir()
        engine = self.engine()
        self.assertEqual(engine.local_target('Sync/a/b'), self.local / 'a' / 'b')
        for call in (engine.local_target, engine.local_stop):
            for path in ('Other', 'Other/a', 'loose.txt', '../Sync/a'):
                with self.subTest(call=call.__name__, path=path), self.assertRaises(Blocked):
                    call(path)

    def test_excluded_conflict_copy_name_is_never_uploaded(self):
        self.write('a.txt', b'local')
        self.drive.put('Sync/a.txt', b'remote')
        self.settings['excluded'] = [f'Sync/a.conflict-local-{md5(b"local")[:12]}.txt']
        with self.assertRaisesRegex(Blocked, 'excluded'):
            self.engine().sync()
        self.assertEqual((self.local / 'a.txt').read_bytes(), b'local')
        self.assertEqual(self.drive.files['Sync/a.txt']['content'], b'remote')
        self.assertFalse([call for call in self.drive.calls if call[0] == 'upload'])

    def test_snapshot_changes_no_settings_so_a_second_one_still_merges(self):
        self.settings['home_folders']['Sync'] = {}
        self.drive.files['Sync'] = {'id': 'sync', 'kind': 'dir', 'hash': None, 'name': 'Sync'}
        row = self.drive.put('Sync/stale', b'cloud')
        self.store.remember('Sync/stale', {'id': row['id'], 'hash': row['hash']})
        before = copy.deepcopy(self.settings)
        engine = self.engine()
        first, second = engine.snapshot(), engine.snapshot()
        self.assertEqual([(action.kind, action.path) for action in first], [('download', 'Sync/stale')])
        self.assertEqual(second, first)
        self.assertEqual(self.engine().snapshot(), first)
        self.assertEqual(self.settings, before)

    def test_identity_is_recorded_when_the_sync_commits_to_the_merge(self):
        self.settings['home_folders']['Sync'] = {}
        self.engine().sync()
        self.assertEqual(self.settings['home_folders']['Sync'], {'identity': identity_of(self.local)})
        self.assertEqual(read_json(self.paths.settings)['home_folders']['Sync'], {'identity': identity_of(self.local)})

    def run_with_unreadable(self, path, mode=0):
        target = self.local / path
        target.chmod(mode)
        self.addCleanup(target.chmod, 0o700)
        engine = self.engine()
        engine.sync()
        return engine

    @unittest.skipIf(os.geteuid() == 0, 'root can read every file')
    def test_unreadable_folder_is_skipped_and_the_rest_syncs(self):
        self.write('deep/locked/a', b'a')
        self.write('deep/open', b'o')
        engine = self.run_with_unreadable('deep/locked')
        self.assertEqual(engine.skipped, {'Sync/deep/locked': UNREADABLE})
        self.assertEqual(self.drive.files['Sync/deep/open']['content'], b'o')
        self.assertNotIn('Sync/deep/locked', self.drive.files)

    @unittest.skipIf(os.geteuid() == 0, 'root can read every file')
    def test_unreadable_ticked_folder_is_skipped_by_its_own_name(self):
        self.write('a', b'a')
        self.engine().sync()
        self.drive.calls.clear()
        engine = self.run_with_unreadable('.')
        self.assertEqual(engine.skipped, {'Sync': UNREADABLE})
        self.assertEqual(self.drive.calls, [])
        self.assertIn('Sync/a', self.drive.files)

    @unittest.skipIf(os.geteuid() == 0, 'root can read every file')
    def test_unreadable_file_is_skipped_and_the_rest_syncs(self):
        self.write('sub/secret', b'x')
        self.write('sub/open', b'y')
        engine = self.run_with_unreadable('sub/secret')
        self.assertEqual(engine.skipped, {'Sync/sub/secret': UNREADABLE})
        self.assertNotIn('Sync/sub/secret', self.drive.files)
        self.assertEqual(self.drive.files['Sync/sub/open']['content'], b'y')

    @unittest.skipIf(os.geteuid() == 0, 'root can read every file')
    def test_folder_that_lists_but_cannot_be_entered_skips_its_items(self):
        self.write('listed/a', b'a')
        engine = self.run_with_unreadable('listed', 0o400)
        self.assertEqual(engine.skipped, {'Sync/listed/a': UNREADABLE})
        self.assertNotIn('Sync/listed/a', self.drive.files)

    @unittest.skipIf(os.geteuid() == 0, 'root can read every file')
    def test_tracked_file_that_becomes_unreadable_stays_on_drive(self):
        self.write('a', b'a')
        self.engine().sync()
        self.drive.calls.clear()
        self.run_with_unreadable('a')
        self.assertEqual(self.drive.calls, [])
        self.assertEqual(self.drive.files['Sync/a']['content'], b'a')

    def test_unsupported_local_items_are_skipped_with_a_reason_and_the_rest_syncs(self):
        (self.local / 'link').symlink_to(self.base / 'outside')
        (self.local / 'dangling').symlink_to(self.base / 'nothing')
        os.mkfifo(self.local / 'pipe')
        self.write('mounted/inner', b'i')
        self.write('good', b'g')
        real = os.path.ismount
        with patch('omarchy_drive.engine.os.path.ismount', side_effect=lambda p: str(p).endswith('mounted') or real(p)):
            engine = self.engine()
            engine.sync()
        self.assertEqual(set(engine.skipped), {'Sync/link', 'Sync/dangling', 'Sync/pipe', 'Sync/mounted'})
        self.assertIn('Symbolic', engine.skipped['Sync/link'])
        self.assertIn('FIFOs', engine.skipped['Sync/pipe'])
        self.assertIn('mounts', engine.skipped['Sync/mounted'])
        self.assertEqual(sorted(self.drive.files), ['Sync', 'Sync/good'])

    def test_nothing_inside_a_nested_mount_is_read(self):
        self.write('mounted/inner', b'i')
        real = os.path.ismount
        with patch('omarchy_drive.engine.os.path.ismount', side_effect=lambda p: str(p).endswith('mounted') or real(p)), \
                patch('omarchy_drive.engine.digest', side_effect=AssertionError('read inside a mount')):
            self.assertEqual(scan_local(self.local, 'Sync/', None, {}), {})

    def test_google_doc_clashing_with_a_local_file_leaves_both_alone(self):
        self.write('doc', b'local')
        self.write('good', b'g')
        self.drive.files['Sync'] = {'id': 'sync', 'kind': 'dir', 'hash': None}
        self.drive.files['Sync/doc'] = {'id': 'doc', 'kind': 'unsupported', 'hash': None}
        engine = self.engine()
        engine.sync()
        self.assertIn('Google Docs', engine.skipped['Sync/doc'])
        self.assertEqual((self.local / 'doc').read_bytes(), b'local')
        self.assertEqual(self.drive.files['Sync/doc']['id'], 'doc')
        self.assertEqual([call for call in self.drive.calls if 'Sync/doc' in call], [])
        self.assertEqual(self.drive.files['Sync/good']['content'], b'g')

    def test_google_doc_only_on_drive_is_not_listed(self):
        self.drive.files['Sync'] = {'id': 'sync', 'kind': 'dir', 'hash': None}
        self.drive.files['Sync/doc'] = {'id': 'doc', 'kind': 'unsupported', 'hash': None}
        engine = self.engine()
        engine.sync()
        self.assertEqual(engine.skipped, {})

    def test_drive_item_with_an_unusable_name_is_listed(self):
        self.drive.unusable = {'Sync/a/b': 'bad name'}
        engine = self.engine()
        engine.sync()
        self.assertEqual(engine.skipped, {'Sync/a/b': 'bad name'})

    def test_unusable_drive_name_never_shields_the_real_path_it_looks_like(self):
        self.write('a/b', b'real')
        self.drive.unusable = {'Sync/a/b': 'bad name'}
        self.engine().sync()
        self.assertEqual(self.drive.files['Sync/a/b']['content'], b'real')

    def replace_with_symlink(self, path):
        target = self.local / path
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        target.symlink_to(self.base / 'outside')

    def test_tracked_file_replaced_by_a_symlink_keeps_its_drive_copy(self):
        self.write('a', b'a')
        self.engine().sync()
        self.drive.calls.clear()
        self.replace_with_symlink('a')
        self.assertEqual(self.engine().sync(), [])
        self.assertEqual(self.drive.calls, [])
        self.assertEqual(self.drive.files['Sync/a']['content'], b'a')

    def test_tracked_folder_replaced_by_a_symlink_keeps_everything_under_it_on_drive(self):
        for number in range(30):  # Enough to trip the deletion limit if these counted as deletions.
            self.write(f'big/{number}', b'x')
        self.engine().sync()
        self.drive.calls.clear()
        self.replace_with_symlink('big')
        self.assertEqual(self.engine().sync(), [])
        self.assertEqual(self.drive.calls, [])
        self.assertIn('Sync/big/7', self.drive.files)

    def test_drive_file_is_not_downloaded_over_a_new_local_symlink(self):
        self.drive.put('Sync/a', b'cloud')
        (self.local / 'a').symlink_to(self.base / 'outside')
        engine = self.engine()
        engine.sync()
        self.assertTrue((self.local / 'a').is_symlink())
        self.assertIn('Sync/a', engine.skipped)

    def test_removing_the_symlink_restores_the_file_and_never_trashes_it(self):
        self.write('a', b'a')
        self.engine().sync()
        self.replace_with_symlink('a')
        self.engine().sync()
        (self.local / 'a').unlink()
        self.engine().sync()
        self.assertEqual((self.local / 'a').read_bytes(), b'a')
        self.assertEqual(self.drive.files['Sync/a']['content'], b'a')
        self.assertNotIn('trash', [call[0] for call in self.drive.calls])

    def test_real_file_replacing_the_symlink_merges_keeping_both_versions(self):
        self.write('a', b'a')
        self.engine().sync()
        self.replace_with_symlink('a')
        self.engine().sync()
        (self.local / 'a').unlink()
        self.write('a', b'new')
        engine = self.engine()
        engine.sync()
        self.assertEqual(engine.skipped, {})
        copy_name = f'a.conflict-local-{md5(b"new")[:12]}'
        self.assertEqual((self.local / copy_name).read_bytes(), b'new')
        self.assertEqual(self.drive.files['Sync/a']['content'], b'a')

    def test_preview_keeps_the_history_of_a_skipped_path(self):
        self.write('a', b'a')
        self.engine().sync()
        self.replace_with_symlink('a')
        engine = self.engine()
        engine.snapshot()
        self.assertIn('Sync/a', engine.skipped)
        self.assertIn('Sync/a', self.store.baseline())

    def test_skipping_records_no_exclusion(self):
        (self.local / 'link').symlink_to(self.base / 'outside')
        self.engine().sync()
        self.assertNotIn('excluded', self.settings)

    def test_excluded_item_is_not_listed_as_skipped(self):
        (self.local / 'link').symlink_to(self.base / 'outside')
        self.settings['excluded'] = ['Sync/link']
        engine = self.engine()
        engine.sync()
        self.assertEqual(engine.skipped, {})

    def test_list_file_names_each_item_with_its_reason(self):
        (self.local / 'link').symlink_to(self.base / 'outside')
        os.mkfifo(self.local / 'pipe')
        self.engine().sync()
        lines = (self.paths.state / 'cannot-sync.txt').read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith('Sync/link — Symbolic'))
        self.assertTrue(lines[1].startswith('Sync/pipe — '))

    def test_list_file_strips_control_characters_from_names(self):
        (self.local / 'li\nnk\x1b').symlink_to(self.base / 'outside')
        self.engine().sync()
        lines = (self.paths.state / 'cannot-sync.txt').read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith('Sync/li nk  — '))

    def test_list_file_goes_away_once_everything_can_sync(self):
        link = self.local / 'link'
        link.symlink_to(self.base / 'outside')
        self.engine().sync()
        link.unlink()
        self.engine().sync()
        self.assertFalse((self.paths.state / 'cannot-sync.txt').exists())
        self.assertEqual(read_json(self.paths.status)['skippedCount'], 0)

    def test_status_counts_every_item_but_carries_a_bounded_list(self):
        for number in range(60):
            (self.local / f'link{number:02}').symlink_to(self.base / 'outside')
        self.engine().sync()
        data = read_json(self.paths.status)
        self.assertEqual(data['skippedCount'], 60)
        self.assertEqual(len(data['skipped']), 50)
        self.assertEqual(data['skipped'][0]['path'], 'Sync/link00')
        self.assertIn('Symbolic', data['skipped'][0]['reason'])
        self.assertEqual(len((self.paths.state / 'cannot-sync.txt').read_text().splitlines()), 60)

    def test_finished_sync_says_items_could_not_sync(self):
        (self.local / 'link').symlink_to(self.base / 'outside')
        self.engine().sync()
        self.assertIn('1 item cannot sync', read_json(self.paths.status)['message'])

    def test_finished_sync_with_nothing_skipped_keeps_its_plain_message(self):
        self.engine().sync()
        self.assertEqual(read_json(self.paths.status)['kind'], 'success')
        self.assertNotIn('cannot sync', read_json(self.paths.status)['message'])

    def reset_scenario(self):
        for child in self.local.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        self.drive.files.clear()
        self.store.forget_subtree('Sync')

    def test_each_stable_problem_reports_its_path(self):
        def local_file_drive_folder():
            self.write('clash', b'file')
            self.drive.files['Sync/clash'] = {'id': 'clash', 'kind': 'dir', 'hash': None}
        def local_folder_drive_file():
            (self.local / 'clash').mkdir()
            self.drive.put('Sync/clash', b'file')
        def tracked_file_became_folder():
            (self.local / 'clash').mkdir()
            self.store.remember('Sync/clash', {'id': 'x', 'hash': 'y'})
            self.drive.files['Sync'] = {'id': 'sync', 'kind': 'dir', 'hash': None}  # A tracked file means Drive had its folder.
        table = ((local_file_drive_folder, 'Sync/clash'),
                 (local_folder_drive_file, 'Sync/clash'), (tracked_file_became_folder, 'Sync/clash'))
        for arrange, path in table:
            with self.subTest(problem=arrange.__name__):
                self.reset_scenario()
                arrange()
                with self.assertRaises(Blocked) as caught:
                    self.engine().sync()
                self.assertEqual(caught.exception.path, path)
                self.assertIn(path, str(caught.exception))
                self.assertEqual(self.drive.calls, [])

    def test_transient_problems_report_no_path(self):
        self.write('a', b'base')
        self.engine().sync()
        self.drive.put('Sync/a', b'cloud edit', self.drive.files['Sync/a']['id'])
        self.drive.before_download = lambda: self.write('a', b'editing now')
        with self.assertRaisesRegex(Blocked, 'changed during sync') as changed:
            self.engine().sync()
        self.assertIsNone(changed.exception.path)
        self.local.rename(self.base / 'old')
        self.local.mkdir()
        with self.assertRaisesRegex(Blocked, 'replaced') as replaced:
            self.engine().sync()
        self.assertIsNone(replaced.exception.path)

    def deletions(self, **folders):
        paths = [f'{folder}/{i}' for folder, count in folders.items() for i in range(count)]
        row = {'id': 'x', 'hash': 'y'}
        return plan({}, {path: dict(row, kind='file') for path in paths}, {path: row for path in paths})

    def test_deletion_limit_names_the_most_affected_folder(self):
        with self.assertRaises(Blocked) as caught:
            self.deletions(Music=1, Work=25)
        message = str(caught.exception)
        self.assertEqual(message, '26 deletions need review, mostly in Work. If you moved that folder, restore it or untick it. '
                                  'To apply them anyway, review omarchy-drive preview, then run sync --allow-many-deletes.')
        self.assertLess(message.index('Work'), message.index('--allow-many-deletes'))
        self.assertIsNone(caught.exception.path)

    def test_deletion_limit_breaks_a_tie_alphabetically(self):
        with self.assertRaisesRegex(Blocked, 'mostly in Alpha'):
            self.deletions(Zed=11, Alpha=11)

    def test_deletion_limit_stops_a_real_sync_naming_the_folder(self):
        (self.home / 'Music').mkdir()
        record_identity(self.settings, 'Music', self.home / 'Music')
        for i in range(24):
            self.write(str(i), b'a')
        (self.home / 'Music' / 'song').write_bytes(b'b')
        self.engine().sync()
        for i in range(7):
            (self.local / str(i)).unlink()
        (self.home / 'Music' / 'song').unlink()
        self.drive.calls.clear()
        with self.assertRaisesRegex(Blocked, 'mostly in Sync'):
            self.engine().sync()
        self.assertEqual(self.drive.calls, [])
