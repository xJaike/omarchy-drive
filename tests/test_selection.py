import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from omarchy_drive.selection import includes, valid_path
from omarchy_drive.storage import Blocked, Paths, Store, atomic_json


class IncludesTests(unittest.TestCase):
    settings = {'home_folders': {'Work': {'identity': [1, 2]}}, 'excluded': ['Work/Exam', 'Work/Résumé.pdf', "Work/Bob's Files"]}

    def test_table(self):
        for path, want in [
            ('Work', True), ('Work/a.txt', True), ('Work/Exam', False), ('Work/Exam/q.pdf', False),
            ('Work/Exam 2/q.pdf', True), ('Work/Examples', True), ('Work2/a.txt', False),
            ('Other/a.txt', False), ('loose.txt', False), ('Work/Résumé.pdf', False),
            ("Work/Bob's Files/a", False), ('work/a.txt', False),
        ]:
            with self.subTest(path=path):
                self.assertEqual(includes(path, self.settings), want)

    def test_nothing_ticked_includes_nothing(self):
        self.assertFalse(includes('Work/a.txt', {}))

    def test_stale_exclusion_is_harmless(self):
        settings = {'home_folders': {'Work': {}}, 'excluded': ['Work/gone/forever']}
        self.assertTrue(includes('Work/a.txt', settings))

    def test_malformed_exclusion_blocks(self):
        with self.assertRaises(Blocked):
            includes('Work/a.txt', {'home_folders': {'Work': {}}, 'excluded': [7]})


class ValidPathTests(unittest.TestCase):
    def test_accepts(self):
        for value in ('Work/a', 'Work/Teaching Files/x y.txt', 'Work/Résumé', "Work/Bob's"):
            self.assertEqual(valid_path(value), value)

    def test_rejects(self):
        for value in ('', '/etc', 'Work/../x', 'Work/./x', 'Work//x', 'Work/', 'Work\x00/x', 7):
            with self.subTest(value=value), self.assertRaises(Blocked):
                valid_path(value)


class StorageCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        home = Path(temporary.name)
        environment = patch.dict(os.environ, {'HOME': str(home), 'XDG_CONFIG_HOME': str(home/'.config'), 'XDG_STATE_HOME': str(home/'.state')})
        environment.start()
        self.addCleanup(environment.stop)
        self.paths = Paths()
        self.paths.prepare()
        self.store = Store(self.paths.state)
        self.addCleanup(self.store.close)


class StoreHelperTests(StorageCase):
    def test_forget_subtree_keeps_look_alikes(self):
        for path in ('Work/Exam', 'Work/Exam/a', 'Work/Exam 2/b'):
            self.store.remember(path, {'kind': 'file'})
        self.store.forget_subtree('Work/Exam')
        self.assertEqual(set(self.store.baseline()), {'Work/Exam 2/b'})

    def test_forget_subtree_treats_wildcards_literally(self):
        for path in ('Work/a_b', 'Work/a_b/x', 'Work/aXb/y', 'Work/a%b/z'):
            self.store.remember(path, {'kind': 'file'})
        self.store.forget_subtree('Work/a_b')
        self.assertEqual(set(self.store.baseline()), {'Work/aXb/y', 'Work/a%b/z'})

    def test_replace_identity_then_identity_accepts_the_new_value(self):
        self.store.identity('old')
        self.store.replace_identity('new')
        self.store.identity('new')
        with self.assertRaises(Blocked):
            self.store.identity('old')
        self.assertEqual(self.store.stored_identity(), 'new')

    def test_stored_identity_none_when_unset(self):
        self.assertIsNone(self.store.stored_identity())


class ConfiguredTests(StorageCase):
    def test_configured_needs_folder_id_only(self):
        atomic_json(self.paths.settings, {'folder_id': 'x'})
        self.assertEqual(self.paths.configured()['folder_id'], 'x')

    def test_backups_path(self):
        self.assertEqual(self.paths.backups, self.paths.state / 'backups')


class BlockedPathTests(unittest.TestCase):
    def test_path_attribute(self):
        self.assertEqual(Blocked('m', path='Work/a').path, 'Work/a')
        self.assertIsNone(Blocked('m').path)
