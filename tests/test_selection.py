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


class GitignoreTests(unittest.TestCase):
    def test_sibling_batch_checks_shared_ancestors_once(self):
        from omarchy_drive.gitignore import selection
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / 'nested').mkdir()
            settings = {'home_folders': {'Work': {}}, 'excluded': ['Work/nested/excluded']}
            calls = []
            original = Path.is_symlink
            def is_symlink(path):
                calls.append(path)
                return original(path)
            with selection(settings, {'Work': source}) as included:
                with patch.object(Path, 'is_symlink', is_symlink):
                    included.prepare([(f'Work/nested/{i}', False) for i in range(100)])
                self.assertEqual(calls.count(source), 1)
                self.assertEqual(calls.count(source / 'nested'), 1)
                self.assertTrue(included('Work/nested/1'))
                self.assertFalse(included('Work/nested/excluded/deeper'))

    def test_missing_historical_paths_are_checked_in_one_batch(self):
        from omarchy_drive.gitignore import selection
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / '.gitignore').write_text('*.log\n', encoding='utf-8')
            settings = {'home_folders': {'Work': {}}}
            with selection(settings, {'Work': source}) as included:
                with patch.object(included, 'git', wraps=included.git) as git:
                    paths = [f'Work/missing-{number}.log' for number in range(100)]
                    included.prepare([(path, False) for path in paths])
                    self.assertTrue(all(not included(path) for path in paths))
                    self.assertEqual(git.call_count, 1)

    def test_cloud_only_directory_patterns_and_unusual_names(self):
        from omarchy_drive.gitignore import selection
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / '.gitignore').write_text('cache/\n*.log\n!keep.log\n', encoding='utf-8')
            settings = {'home_folders': {'Work': {}}}
            with selection(settings, {'Work': source}) as included:
                included.prepare([('Work/cache', True), ('Work/cache.txt', False),
                                  ('Work/Résumé\nnotes.log', False)])
                self.assertFalse(included('Work/cache', True))
                self.assertFalse(included('Work/cache/file'))
                self.assertTrue(included('Work/cache.txt'))
                self.assertFalse(included('Work/Résumé\nnotes.log'))
                self.assertTrue(included('Work/keep.log'))

    def test_repository_index_and_private_excludes_are_not_used(self):
        import subprocess
        from omarchy_drive.gitignore import selection
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            subprocess.run(['git', 'init', '-q', str(source)], check=True)
            (source / 'tracked.log').write_text('tracked')
            subprocess.run(['git', '-C', str(source), 'add', 'tracked.log'], check=True)
            (source / '.gitignore').write_text('*.log\n')
            (source / '.git/info/exclude').write_text('private.txt\n')
            settings = {'home_folders': {'Work': {}}}
            with selection(settings, {'Work': source}) as included:
                self.assertFalse(included('Work/tracked.log'))
                self.assertTrue(included('Work/private.txt'))
