"""Repeat checks avoid content reads without hiding local edits."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from omarchy_drive.drive import digest
from omarchy_drive.engine import plan, scan_local
from omarchy_drive.storage import Blocked, Store


class ScanCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'files'
        self.root.mkdir()
        self.file = self.root / 'a'
        self.file.write_bytes(b'old')
        self.cache = {}
        scan_local(self.root, fresh=self.cache)

    def test_persistent_cache_avoids_all_unchanged_content_reads(self):
        store = Store(self.temporary.name)
        store.save_local_hashes(self.cache)
        store.close()
        store = Store(self.temporary.name)
        self.addCleanup(store.close)
        with patch('omarchy_drive.engine.digest', side_effect=AssertionError('read unchanged file')):
            result = scan_local(self.root, cache=store.local_hashes())
        self.assertEqual(result['a']['hash'], digest(self.file))

    def test_same_size_edit_with_restored_mtime_is_rehashed(self):
        before = self.file.stat()
        self.file.write_bytes(b'new')
        os.utime(self.file, ns=(before.st_atime_ns, before.st_mtime_ns))
        with patch('omarchy_drive.engine.digest', wraps=digest) as hashing:
            result = scan_local(self.root, cache=self.cache)
        hashing.assert_called_once()
        self.assertEqual(result['a']['hash'], digest(self.file))

    def test_replacement_and_new_file_are_rehashed_and_deleted_entries_pruned(self):
        self.file.unlink()
        (self.root / 'b').write_bytes(b'new')
        fresh = {}
        with patch('omarchy_drive.engine.digest', wraps=digest) as hashing:
            result = scan_local(self.root, cache=self.cache, fresh=fresh)
        hashing.assert_called_once()
        self.assertEqual(set(result), {'b'})
        self.assertEqual(set(fresh), {str(self.root / 'b')})

    def test_edit_during_hash_still_blocks(self):
        def changing(path):
            checksum = digest(path)
            path.write_bytes(b'changed')
            return checksum
        with patch('omarchy_drive.engine.digest', side_effect=changing):
            with self.assertRaises(Blocked):
                scan_local(self.root, fresh={})

    def test_unchanged_baseline_does_not_write_database(self):
        store = Store(self.temporary.name)
        self.addCleanup(store.close)
        entry = {'id': 'a', 'hash': 'abc'}
        store.remember('a', entry)
        changes = store.db.total_changes
        store.remember('a', entry)
        self.assertEqual(store.db.total_changes, changes)

    def test_cache_updates_only_changed_and_removed_rows(self):
        store = Store(self.temporary.name)
        self.addCleanup(store.close)
        entries = {str(i): {'hash': str(i), 'signature': [i]} for i in range(100)}
        store.save_local_hashes(entries)
        changes = store.db.total_changes
        store.save_local_hashes(entries)
        self.assertEqual(store.db.total_changes, changes)
        entries.pop('0')
        entries['1'] = {'hash': 'edited', 'signature': [101]}
        entries['new'] = {'hash': 'new', 'signature': [102]}
        store.save_local_hashes(entries)
        self.assertEqual(store.db.total_changes - changes, 3)
        self.assertEqual(store.local_hashes(), entries)

    def test_unchanged_files_produce_no_bookkeeping_actions(self):
        local = {'a': {'kind': 'file', 'hash': 'abc'}}
        remote = {'a': {'kind': 'file', 'hash': 'abc', 'id': 'id-1'}}
        baseline = {'a': {'hash': 'abc', 'id': 'id-1'}}
        self.assertEqual(plan(local, remote, baseline), [])
        # Matching content with a changed Drive identity still advances history.
        remote['a']['id'] = 'id-2'
        self.assertEqual([action.kind for action in plan(local, remote, baseline)], ['remember'])
        self.assertEqual([action.kind for action in plan(local, remote, {})], ['remember'])
