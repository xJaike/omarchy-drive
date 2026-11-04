import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from omarchy_drive.drive import FOLDER
from omarchy_drive.engine import Engine, plan, scan_local
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

    def put(self, path, data, file_id=None):
        self.serial += 1
        item = dict(id=file_id or f'file-{self.serial}', kind='file', hash=md5(data),
                    md5Checksum=md5(data), size=len(data), version=str(self.serial), name=Path(path).name)
        self.files[path] = dict(item, content=data)
        return item

    def scan(self, root):
        if not self.root_exists:
            raise Blocked('Remote root is missing')
        return {path: {k: v for k, v in item.items() if k != 'content'} for path, item in self.files.items()}

    def mkdir(self, parent, name, key):
        self.serial += 1
        prefix = next((p for p, i in self.files.items() if i['id'] == parent), '')
        path = str(Path(prefix) / name)
        item = dict(id=f'dir-{self.serial}', name=name, kind='dir', hash=None)
        self.files[path] = item
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
        self.paths = Paths()
        self.paths.config = self.base / 'config'
        self.paths.state = self.base / 'state'
        self.paths.status = self.paths.state / 'status.json'
        self.paths.prepare()
        self.local = self.base / 'GoogleDrive'
        self.local.mkdir()
        self.settings = {'local_dir': str(self.local), 'folder_id': 'root', 'max_deletes': 20}
        self.store = Store(self.paths.state)
        self.addCleanup(self.store.close)
        self.drive = MemoryDrive()

    def engine(self):
        return Engine(self.paths, self.settings, self.store, self.drive)

    def write(self, path, content):
        target = self.local / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def test_first_run_merges_both_trees_and_then_converges(self):
        self.write('local.txt', b'local')
        self.drive.put('cloud.txt', b'cloud')
        self.engine().sync()
        self.assertEqual((self.local / 'cloud.txt').read_bytes(), b'cloud')
        self.assertEqual(self.drive.files['local.txt']['content'], b'local')
        self.assertEqual(self.engine().sync(), [])
        self.assertEqual(len(self.store.baseline()), 2)

    def test_conflicting_initial_files_keep_both_versions(self):
        self.write('notes.txt', b'local notes')
        self.drive.put('notes.txt', b'cloud notes')
        self.engine().sync()
        copy = f'notes.conflict-local-{md5(b"local notes")[:12]}.txt'
        self.assertEqual((self.local / 'notes.txt').read_bytes(), b'cloud notes')
        self.assertEqual((self.local / copy).read_bytes(), b'local notes')
        self.assertEqual(self.drive.files[copy]['content'], b'local notes')
        self.assertEqual(self.engine().sync(), [])

    def test_both_sides_edit_after_success_keep_both_versions(self):
        self.write('a', b'baseline')
        self.engine().sync()
        self.write('a', b'local edit')
        self.drive.put('a', b'remote edit', self.drive.files['a']['id'])
        self.engine().sync()
        self.assertEqual((self.local / 'a').read_bytes(), b'remote edit')
        self.assertTrue(any(p.read_bytes() == b'local edit' for p in self.local.iterdir()))
        self.assertEqual(self.engine().sync(), [])

    def test_one_sided_edits_and_backups(self):
        self.write('a', b'baseline')
        self.engine().sync()
        self.write('a', b'local edit')
        self.engine().sync()
        self.assertEqual(self.drive.files['a']['content'], b'local edit')
        self.drive.put('a', b'remote edit', self.drive.files['a']['id'])
        self.engine().sync()
        self.assertEqual((self.local / 'a').read_bytes(), b'remote edit')
        backups = list(self.engine().archive_root.rglob('*'))
        contents = [p.read_bytes() for p in backups if p.is_file()]
        self.assertIn(b'baseline', contents)
        self.assertIn(b'local edit', contents)

    def test_remote_deletion_archives_local_copy(self):
        self.write('a', b'keep in backup')
        self.engine().sync()
        del self.drive.files['a']
        self.engine().sync()
        self.assertFalse((self.local / 'a').exists())
        self.assertFalse(self.store.baseline())
        self.assertTrue(any(p.read_bytes() == b'keep in backup' for p in self.engine().archive_root.rglob('*') if p.is_file()))

    def test_local_deletion_trashes_remote_and_keeps_backup(self):
        self.write('a', b'keep')
        self.engine().sync()
        (self.local / 'a').unlink()
        self.engine().sync()
        self.assertNotIn('a', self.drive.files)
        self.assertIn(('trash', 'a'), self.drive.calls)
        self.assertFalse(self.store.baseline())

    def test_deleted_on_one_side_edited_on_other_preserves_edits(self):
        self.write('a', b'old')
        self.engine().sync()
        self.write('a', b'new local')
        del self.drive.files['a']
        self.engine().sync()
        self.assertEqual(self.drive.files['a']['content'], b'new local')
        (self.local / 'a').unlink()
        self.drive.put('a', b'new cloud', self.drive.files['a']['id'])
        self.engine().sync()
        self.assertEqual((self.local / 'a').read_bytes(), b'new cloud')

    def test_lost_upload_response_reconciles_without_duplicates(self):
        self.write('a', b'content')
        self.drive.fail_after_upload = True
        with self.assertRaises(Offline):
            self.engine().sync()
        self.assertFalse(self.store.baseline())
        self.engine().sync()
        self.assertEqual(list(self.drive.files), ['a'])
        self.assertEqual(self.store.baseline()['a']['hash'], md5(b'content'))

    def test_local_edit_during_download_is_not_overwritten(self):
        self.write('a', b'base')
        self.engine().sync()
        self.drive.put('a', b'cloud edit', self.drive.files['a']['id'])
        self.drive.before_download = lambda: self.write('a', b'editing now')
        with self.assertRaises(Blocked):
            self.engine().sync()
        self.assertEqual((self.local / 'a').read_bytes(), b'editing now')
        self.assertEqual(self.store.baseline()['a']['hash'], md5(b'base'))

    def test_scan_failure_does_not_mutate_either_tree(self):
        self.write('a', b'a')
        self.drive.root_exists = False
        with self.assertRaises(Blocked):
            self.engine().sync()
        self.assertEqual(self.drive.calls, [])
        self.assertEqual((self.local / 'a').read_bytes(), b'a')

    def test_preview_does_not_transfer_or_change_baseline(self):
        self.write('a', b'a')
        self.drive.put('b', b'b')
        actions = self.engine().snapshot()
        self.assertEqual({a.kind for a in actions}, {'upload', 'download'})
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
        self.assertIn('a', self.drive.files)

    def test_nested_folders_create_before_file_transfers(self):
        self.write('nested/deep/a.txt', b'a')
        self.engine().sync()
        self.assertEqual(self.drive.files['nested/deep/a.txt']['content'], b'a')

    def test_symlinks_and_type_conflicts_block_before_transfer(self):
        (self.local / 'link').symlink_to(self.base / 'outside')
        with self.assertRaises(Blocked):
            self.engine().sync()
        (self.local / 'link').unlink()
        self.write('folder', b'file')
        self.drive.files['folder'] = {'id': 'folder', 'kind': 'dir', 'hash': None}
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
        self.assertEqual(len(self.drive.files), 24)
        self.engine().sync(allow_many_deletes=True)
        self.assertEqual(len(self.drive.files), 17)

    def test_conflict_copy_collision_never_overwrites_other_content(self):
        self.write('a.txt', b'local')
        self.drive.put('a.txt', b'remote')
        self.write(f'a.conflict-local-{md5(b"local")[:12]}.txt', b'other')
        with self.assertRaises(Blocked):
            self.engine().sync()
        self.assertEqual((self.local / 'a.txt').read_bytes(), b'local')

    def test_new_file_appearing_at_download_publish_is_not_overwritten(self):
        import os
        self.drive.put('a', b'cloud')
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
            self.drive.put('a', b'cloud edit', self.drive.files['a']['id'])
            self.engine().sync()
            handle.seek(0)
            handle.write(b'late edit from open application')
            handle.flush()
        finally:
            handle.close()
        self.assertEqual((self.local / 'a').read_bytes(), b'cloud edit')
        originals = list(self.engine().archive_root.rglob('a.original'))
        self.assertTrue(any(p.read_bytes() == b'late edit from open application' for p in originals))
