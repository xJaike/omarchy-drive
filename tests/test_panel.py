import contextlib
import io
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.parse

from omarchy_drive import home_folders, panel
from omarchy_drive.drive import FOLDER
from omarchy_drive.storage import Paths, Store, Blocked, Offline, atomic_json, read_json

ROOT = Path(__file__).resolve().parents[1]
DOC = 'application/vnd.google-apps.document'
SHORTCUT = 'application/vnd.google-apps.shortcut'


def folder(name, id=None):
    return dict(id=id or name + '-id', name=name, mimeType=FOLDER)


def file(name, size=1, mime='text/plain'):
    return dict(id=name + '-id', name=name, mimeType=mime, md5Checksum='hash', size=str(size))


def doc(name, mime=DOC):
    return dict(id=name + '-id', name=name, mimeType=mime)


class FakeDrive:
    """Answers listings by parent ID, so tests state Drive's shape rather than a call order."""

    def __init__(self, listings, usage='10', limit='100'):
        self.listings, self.usage, self.limit = listings, usage, limit
        self.calls = []
        self.sleep = lambda _: None

    def json(self, method, url, **kwargs):
        self.calls.append((method, url))
        parsed = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parsed.query)
        if 'q' in query:
            parent = re.match(r"'(.*)' in parents", query['q'][0]).group(1)
            return {'files': self.listings.get(parent, [])}, {}
        if parsed.path.endswith('/about'):
            return {'storageQuota': {'usage': self.usage, 'limit': self.limit}}, {}
        return {'id': 'root', 'mimeType': FOLDER}, {}


class OfflineDrive:
    sleep = staticmethod(lambda _: None)

    def json(self, method, url, **kwargs):
        raise Offline('Cannot reach Google Drive. Your files stay local; the next sync will retry.')


class PanelCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.home = base / 'home'
        self.home.mkdir()
        # The app's directories sit outside home so they do not show up as folder rows.
        self.env = patch.dict(os.environ, {
            'HOME': str(self.home), 'XDG_CONFIG_HOME': str(base / 'config'),
            'XDG_STATE_HOME': str(base / 'state'), 'XDG_DATA_HOME': str(base / 'data')})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.paths = Paths()
        self.paths.prepare()
        self.cache = self.paths.state / 'panel-cache.json'
        atomic_json(self.paths.settings, {'folder_id': 'root', 'folder_name': 'Omarchy'})
        atomic_json(self.paths.tokens, {'refresh_token': 'private'})

    def drive(self, listings=None):
        return patch('omarchy_drive.panel.HTTP', return_value=FakeDrive(listings or {}))

    def offline(self):
        return patch('omarchy_drive.panel.HTTP', return_value=OfflineDrive())

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = panel.main(list(argv))
        return code, json.loads(out.getvalue()), err.getvalue()

    def tick(self, *names):
        settings = read_json(self.paths.settings)
        for name in names:
            home_folders.record_identity(settings, name, self.home / name)
        atomic_json(self.paths.settings, settings)

    def set_excluded(self, *entries):
        atomic_json(self.paths.settings, dict(read_json(self.paths.settings), excluded=list(entries)))

    def make(self, *relative):
        for path in relative:
            target = self.home / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'12345')


class FolderTests(PanelCase):
    def rows(self, listings=None):
        with self.drive(listings):
            value = panel.folders(self.paths)
        return value, {row['name']: row for row in value['folders']}

    def test_folders_merges_home_and_drive(self):
        (self.home / 'Work').mkdir()
        (self.home / 'Downloads').mkdir()
        self.tick('Work')
        value, _ = self.rows({'root': [folder('Work'), folder('Archive')]})
        got = [(row['name'], row['where'], row['selected']) for row in value['folders']]
        self.assertEqual(got, [('Archive', 'drive', False), ('Downloads', 'home', False), ('Work', 'both', True)])
        self.assertEqual(value['tickedCount'], 1)
        self.assertTrue(value['ok'])
        self.assertTrue(value['driveAvailable'])

    def test_rows_sort_by_case_folded_name(self):
        (self.home / 'alpha').mkdir()
        (self.home / 'Gamma').mkdir()
        value, _ = self.rows({'root': [folder('Beta'), folder('delta')]})
        self.assertEqual([row['name'] for row in value['folders']], ['alpha', 'Beta', 'delta', 'Gamma'])

    def test_rows_and_totals_match_the_panel_contract(self):
        self.make('Work/a', 'Downloads/big')
        self.tick('Work')
        value, rows = self.rows({'root': [folder('Work'), folder('Archive')]})
        self.assertEqual(set(rows['Work']), {'name', 'where', 'selected', 'localBytes', 'approx', 'error'})
        self.assertEqual(rows['Work']['localBytes'], 5)
        self.assertEqual(rows['Downloads']['localBytes'], 5)
        self.assertEqual(rows['Archive']['localBytes'], 0)
        self.assertFalse(rows['Work']['approx'])
        self.assertEqual(value['localBytes'], 5, 'only ticked folders count towards the total')
        self.assertFalse(value['localBytesApprox'])
        self.assertEqual((value['usedBytes'], value['quotaBytes'], value['quotaKnown']), (10, 100, True))
        self.assertGreater(value['updated'], 0)
        for removed in ('selectedCount', 'rootFiles', 'rootFileCount', 'rootFileBytes', 'rootLocalBytes',
                        'rootLocalCount', 'staleBytes', 'staleCount'):
            self.assertNotIn(removed, value)
        for removed in ('stale', 'onDisk', 'missing', 'id'):
            self.assertNotIn(removed, rows['Work'])

    def test_partial_size_is_marked_approximate(self):
        self.make('Work/a')
        self.tick('Work')
        with patch('omarchy_drive.panel.disk_inventory', return_value=({'': 5}, {'': 1}, True)):
            value, rows = self.rows()
        self.assertTrue(rows['Work']['approx'])
        self.assertTrue(value['localBytesApprox'])

    def test_a_read_overlapping_a_selection_change_is_not_served_afterwards(self):
        (self.home / 'Work').mkdir()
        start = self.paths.settings.stat().st_mtime + 10
        clock = {'now': start}
        real = panel.folder_list

        def tick_during_the_read(*args):
            # select deletes the cache; the read in progress then writes it again with old ticks.
            with self.drive({'root': [folder('Work')]}):
                home_folders.select(self.paths, 'Work', True)
            os.utime(self.paths.settings, (start + 1, start + 1))
            clock['now'] = start + 2
            return real(*args)

        with patch('omarchy_drive.panel.time.time', lambda: clock['now']):
            with patch('omarchy_drive.panel.folder_list', side_effect=tick_during_the_read):
                _, rows = self.rows({'root': [folder('Work')]})
            self.assertFalse(rows['Work']['selected'], 'the overlapping read holds the old tick')
            self.assertEqual(read_json(self.cache)['updated'], start, 'the cache is dated from the start of its read')
            clock['now'] = start + 3
            _, rows = self.rows({'root': [folder('Work')]})
        self.assertTrue(rows['Work']['selected'])

    def test_a_read_started_after_the_last_change_is_served_from_cache(self):
        self.make('Work/a')
        with self.drive({'root': [folder('Work')]}):
            first = panel.folders(self.paths)
        with patch('omarchy_drive.panel.HTTP', side_effect=AssertionError('must not read Drive')):
            self.assertEqual(panel.folders(self.paths), first)

    def test_a_fresh_cache_is_returned_without_network(self):
        (self.home / 'Work').mkdir()
        first, _ = self.rows()
        self.assertEqual(read_json(self.cache), first)
        with patch('omarchy_drive.panel.HTTP') as http:
            self.assertEqual(panel.folders(self.paths), first)
        http.assert_not_called()

    def test_quota_failure_keeps_the_last_known_values(self):
        atomic_json(self.cache, {'updated': 1, 'usedBytes': 7, 'quotaBytes': 70, 'quotaKnown': True})
        fake = FakeDrive({})
        real = fake.json
        def no_quota(method, url, **kwargs):
            if '/about' in url:
                raise Offline('gone')
            return real(method, url, **kwargs)
        fake.json = no_quota
        with patch('omarchy_drive.panel.HTTP', return_value=fake):
            value = panel.folders(self.paths)
        self.assertEqual((value['usedBytes'], value['quotaBytes'], value['quotaKnown']), (7, 70, True))
        self.assertTrue(value['driveAvailable'])

    def test_trashed_drive_folder_stops_the_list(self):
        fake = FakeDrive({})
        real = fake.json
        fake.json = lambda method, url, **kw: ({'id': 'root', 'trashed': True, 'mimeType': FOLDER}, {}) \
            if 'files/root' in url else real(method, url, **kw)
        with patch('omarchy_drive.panel.HTTP', return_value=fake), self.assertRaisesRegex(Blocked, 'Restore'):
            panel.folders(self.paths)

    def test_google_drive_appears_as_ordinary_row(self):
        (self.home / 'GoogleDrive').mkdir()
        _, rows = self.rows()
        self.assertEqual(rows['GoogleDrive']['where'], 'home')
        self.assertFalse(rows['GoogleDrive']['selected'])
        self.assertEqual(rows['GoogleDrive']['error'], '')

    def test_name_clashes_are_greyed_with_a_reason(self):
        (self.home / 'Archive').write_text('a file')
        (self.home / 'target').mkdir()
        (self.home / 'Link').symlink_to(self.home / 'target')
        value, rows = self.rows({'root': [folder('Archive'), folder('Link'), folder('.hidden')]})
        for name in ('Archive', 'Link', '.hidden'):
            with self.subTest(name=name):
                self.assertFalse(rows[name]['selected'])
                self.assertTrue(rows[name]['error'])
                self.assertEqual(rows[name]['where'], 'drive')
                self.assertEqual(rows[name]['localBytes'], 0)
        # Nothing is ticked, so the reason must not tell the user to untick.
        self.assertNotIn('Untick', rows['Archive']['error'])
        self.assertNotIn('Untick', rows['Link']['error'])
        self.assertEqual(value['tickedCount'], 0)

    def test_folder_name_that_cannot_be_a_path_is_not_listed(self):
        (self.home / 'Work').mkdir()
        _, rows = self.rows({'root': [folder('a/b'), folder('..'), file('x/y')]})
        self.assertEqual(set(rows), {'Work'})

    def test_loose_drive_files_get_no_row(self):
        _, rows = self.rows({'root': [file('loose.txt'), doc('Notes'), folder('Real')]})
        self.assertEqual(set(rows), {'Real'})

    def test_drive_only_folder_can_be_ticked_from_the_listing(self):
        self.rows({'root': [folder('Archive')]})
        self.assertEqual(self.run_main('select', '--name', 'Archive', '--enable')[0], 0)
        self.assertEqual(read_json(self.paths.settings)['home_folders'], {'Archive': {'identity': None}})

    def test_ticked_folder_with_a_problem_stays_ticked_so_it_can_be_unticked(self):
        (self.home / 'Work').mkdir()
        self.tick('Work')
        (self.home / 'Work').rmdir()
        (self.home / 'Work').write_text('now a file')
        value, rows = self.rows({'root': [folder('Work')]})
        self.assertTrue(rows['Work']['selected'])
        self.assertIn('Untick', rows['Work']['error'])
        self.assertEqual(rows['Work']['where'], 'drive')
        self.assertEqual(value['tickedCount'], 1)

    def test_ticked_folder_gone_from_both_sides_is_still_listed(self):
        (self.home / 'Work').mkdir()
        self.tick('Work')
        (self.home / 'Work').rmdir()
        _, rows = self.rows()
        self.assertTrue(rows['Work']['selected'])
        self.assertIn('missing', rows['Work']['error'])
        self.assertEqual(rows['Work']['where'], 'home')

    def test_control_characters_never_reach_a_reason(self):
        (self.home / 'bad\x1bname').write_text('a file')
        _, rows = self.rows({'root': [folder('bad\x1bname')]})
        self.assertNotIn('\x1b', rows['bad\x1bname']['error'])
        self.assertTrue(rows['bad\x1bname']['error'])

    def test_folders_offline_returns_local_rows(self):
        (self.home / 'Work').mkdir()
        self.tick('Work')
        atomic_json(self.cache, {'updated': 1, 'folders': [{'name': 'Old', 'where': 'drive'}],
                                 'usedBytes': 7, 'quotaBytes': 70, 'quotaKnown': True})
        before = self.cache.read_bytes()
        with self.offline():
            value = panel.folders(self.paths)
        self.assertTrue(value['ok'])
        self.assertFalse(value['driveAvailable'])
        self.assertEqual([(row['name'], row['where'], row['selected']) for row in value['folders']],
                         [('Work', 'home', True)])
        self.assertEqual(value['tickedCount'], 1)
        self.assertEqual((value['usedBytes'], value['quotaBytes'], value['quotaKnown']), (7, 70, True))
        self.assertEqual(self.cache.read_bytes(), before)

    def test_folders_offline_with_no_cache_writes_none(self):
        (self.home / 'Work').mkdir()
        with self.offline():
            value = panel.folders(self.paths)
        self.assertEqual((value['usedBytes'], value['quotaBytes'], value['quotaKnown']), (0, 0, False))
        self.assertFalse(self.cache.exists())

    def test_ticked_drive_only_folder_is_a_drive_row_while_offline(self):
        atomic_json(self.paths.settings, dict(read_json(self.paths.settings),
                                              home_folders={'Archive': {'identity': None}}))
        with self.offline():
            value = panel.folders(self.paths)
        row = value['folders'][0]
        self.assertEqual((row['name'], row['where'], row['selected'], row['error']), ('Archive', 'drive', True, ''))

    def test_offline_when_the_http_client_cannot_start(self):
        (self.home / 'Work').mkdir()
        with patch('omarchy_drive.panel.HTTP', side_effect=Offline('no route')):
            value = panel.folders(self.paths)
        self.assertFalse(value['driveAvailable'])


class TreeTests(PanelCase):
    def tree(self, path, listings=None):
        with self.drive(listings):
            value = panel.tree(self.paths, path)
        return value, {row['name']: row for row in value['rows']}

    def test_tree_merges_both_sides_one_level(self):
        self.make('Work/a.txt', 'Work/both/inner.txt')
        value, rows = self.tree('Work', {
            'root': [folder('Work')],
            'Work-id': [folder('both'), file('b.txt', 9), doc('Notes')],
            'both-id': [file('deep.txt')]})
        self.assertEqual([row['name'] for row in value['rows']], ['a.txt', 'b.txt', 'both', 'Notes'])
        self.assertTrue(value['ok'])
        self.assertTrue(value['driveAvailable'])
        self.assertEqual(value['path'], 'Work')
        self.assertEqual(set(rows['a.txt']), {'name', 'path', 'kind', 'where', 'size', 'excluded',
                                              'inheritedExclusion', 'unsupported', 'excludable'})
        self.assertEqual((rows['a.txt']['where'], rows['a.txt']['kind'], rows['a.txt']['size']), ('home', 'file', 5))
        self.assertEqual((rows['b.txt']['where'], rows['b.txt']['kind'], rows['b.txt']['size']), ('drive', 'file', 9))
        self.assertEqual((rows['both']['where'], rows['both']['kind'], rows['both']['path']), ('both', 'dir', 'Work/both'))
        self.assertEqual(rows['Notes']['kind'], 'unsupported')
        self.assertEqual(rows['Notes']['unsupported'], 'Google Docs and shortcuts do not sync')
        self.assertNotIn('inner.txt', rows)
        self.assertNotIn('deep.txt', rows)

    def test_tree_lists_only_the_requested_folder_from_drive(self):
        self.make('Work/both/inner.txt')
        fake = FakeDrive({'root': [folder('Work')], 'Work-id': [folder('both')], 'both-id': [file('deep.txt')]})
        with patch('omarchy_drive.panel.HTTP', return_value=fake):
            panel.tree(self.paths, 'Work')
        listed = [urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['q'][0] for _, url in fake.calls]
        self.assertEqual(len(listed), 2)
        self.assertFalse(any('both-id' in query for query in listed))

    def test_tree_walks_down_a_nested_path(self):
        self.make('Work/sub/here.txt')
        _, rows = self.tree('Work/sub', {
            'root': [folder('Work')], 'Work-id': [folder('sub', 'sub-id')], 'sub-id': [file('there.txt')]})
        self.assertEqual({name: row['where'] for name, row in rows.items()}, {'here.txt': 'home', 'there.txt': 'drive'})
        self.assertEqual(rows['here.txt']['path'], 'Work/sub/here.txt')

    def test_tree_handles_unusual_names(self):
        self.make("Work/Bob's Files/x", 'Work/Résumé.pdf', "Bob's Files/x")
        listings = {'root': [folder('Work'), folder("Bob's Files", 'top')],
                    'Work-id': [folder("Bob's Files", 'bob'), file('Résumé.pdf')],
                    'bob': [file('y')], 'top': [file('z')]}
        _, rows = self.tree('Work', listings)
        self.assertEqual(rows["Bob's Files"]['where'], 'both')
        self.assertEqual(rows["Bob's Files"]['path'], "Work/Bob's Files")
        self.assertEqual(rows['Résumé.pdf']['where'], 'both')
        self.assertEqual(rows['Résumé.pdf']['path'], 'Work/Résumé.pdf')
        fake = FakeDrive(listings)
        with patch('omarchy_drive.panel.HTTP', return_value=fake):
            nested = panel.tree(self.paths, "Work/Bob's Files")
            top = panel.tree(self.paths, "Bob's Files")
        self.assertEqual({row['name']: row['where'] for row in nested['rows']}, {'x': 'home', 'y': 'drive'})
        self.assertEqual({row['name']: row['where'] for row in top['rows']}, {'x': 'home', 'z': 'drive'})
        self.assertFalse(any('Bob' in urllib.parse.unquote_plus(url) for _, url in fake.calls),
                         'names are matched in Python, never in a query string')

    def test_tree_sorts_by_case_folded_name(self):
        self.make('Work/b', 'Work/C', 'Work/a')
        value, _ = self.tree('Work', {'root': [folder('Work')], 'Work-id': [file('D')]})
        self.assertEqual([row['name'] for row in value['rows']], ['a', 'b', 'C', 'D'])

    def test_tree_marks_exclusions(self):
        self.make('Work/x/inner/deep', 'Work/y')
        self.set_excluded('Work/x')
        _, rows = self.tree('Work')
        self.assertTrue(rows['x']['excluded'])
        self.assertFalse(rows['x']['inheritedExclusion'])
        self.assertFalse(rows['y']['excluded'])
        _, rows = self.tree('Work/x')
        self.assertTrue(rows['inner']['inheritedExclusion'])
        self.assertFalse(rows['inner']['excluded'])
        _, rows = self.tree('Work/x/inner')
        self.assertTrue(rows['deep']['inheritedExclusion'])

    def test_exclusion_look_alikes_do_not_match(self):
        self.make('Work/x/a', 'Work/x 2/a', 'Work/x2/a')
        self.set_excluded('Work/x')
        _, rows = self.tree('Work')
        self.assertFalse(rows['x 2']['excluded'])
        self.assertFalse(self.tree('Work/x 2')[1]['a']['inheritedExclusion'])
        self.assertFalse(self.tree('Work/x2')[1]['a']['inheritedExclusion'])

    def test_nested_exclusion_inside_an_excluded_folder_shows_both(self):
        self.make('Work/x/y')
        self.set_excluded('Work/x', 'Work/x/y')
        _, rows = self.tree('Work/x')
        self.assertTrue(rows['y']['excluded'])
        self.assertTrue(rows['y']['inheritedExclusion'])

    def test_invalid_exclusion_entry_is_refused(self):
        self.make('Work/x')
        self.set_excluded(5)
        with self.assertRaisesRegex(Blocked, 'invalid exclusion'):
            self.tree('Work')

    def test_tree_works_on_an_unticked_folder(self):
        self.make('Downloads/a')
        self.set_excluded('Downloads/a')
        _, rows = self.tree('Downloads')
        self.assertTrue(rows['a']['excluded'])

    def test_local_specials_get_a_reason_and_are_never_followed(self):
        self.make('outside/secret', 'Work/plain')
        (self.home / 'Work/link').symlink_to(self.home / 'outside')
        (self.home / 'Work/filelink').symlink_to(self.home / 'outside/secret')
        (self.home / 'Work/dangling').symlink_to(self.home / 'nothing')
        os.mkfifo(self.home / 'Work/pipe')
        cwd = os.getcwd()
        os.chdir(self.home / 'Work')  # A relative bind keeps the socket path under the length limit.
        self.addCleanup(os.chdir, cwd)
        server = socket.socket(socket.AF_UNIX)
        self.addCleanup(server.close)
        server.bind('sock')
        _, rows = self.tree('Work')
        for name, reason in (('link', 'Symbolic'), ('filelink', 'Symbolic'), ('dangling', 'Symbolic'),
                             ('pipe', 'FIFOs'), ('sock', 'Sockets')):
            with self.subTest(name=name):
                self.assertEqual(rows[name]['kind'], 'unsupported')
                self.assertIn(reason, rows[name]['unsupported'])
                self.assertEqual(rows[name]['size'], 0)
                self.assertTrue(rows[name]['excludable'])
        self.assertEqual(rows['plain']['unsupported'], '')

    def test_nested_mount_gets_a_reason(self):
        self.make('Work/mounted/a')
        real = os.path.ismount
        with patch('omarchy_drive.panel.os.path.ismount', side_effect=lambda p: str(p).endswith('mounted') or real(p)):
            _, rows = self.tree('Work')
        self.assertEqual(rows['mounted']['kind'], 'unsupported')
        self.assertTrue(rows['mounted']['unsupported'])
        self.assertTrue(rows['mounted']['excludable'])

    def test_tree_hashes_nothing(self):
        self.make('Work/a')
        (self.home / 'Work/a').chmod(0)  # Reading it would fail, so listing it proves it was not opened.
        with patch('omarchy_drive.drive.hashlib.file_digest', side_effect=AssertionError('hashed')):
            _, rows = self.tree('Work')
        self.assertEqual(rows['a']['size'], 5)

    def test_app_temporary_files_are_not_listed(self):
        self.make('Work/.omarchy-drive-tmp-abc', 'Work/.hidden-but-synced')
        _, rows = self.tree('Work')
        self.assertEqual(set(rows), {'.hidden-but-synced'})

    def test_excludable_follows_rule_r3(self):
        self.make('Work/x/inner', 'Work/plain', 'Work/dir/a')
        self.set_excluded('Work/x')
        listings = {'root': [folder('Work')], 'Work-id': [
            doc('Notes'), doc('Link', SHORTCUT), file('remote.txt'), folder('remote-dir')]}
        _, rows = self.tree('Work', listings)
        self.assertFalse(rows['Notes']['excludable'])
        self.assertFalse(rows['Link']['excludable'])
        self.assertEqual(rows['Link']['unsupported'], 'Google Docs and shortcuts do not sync')
        for name in ('plain', 'dir', 'remote.txt', 'remote-dir', 'x'):
            with self.subTest(name=name):
                self.assertTrue(rows[name]['excludable'])
        self.assertFalse(self.tree('Work/x')[1]['inner']['excludable'])

    def test_local_file_clashing_with_a_google_doc_can_be_excluded(self):
        # Sync stops on this clash, and spec row "Local path clashes with a Google Doc" offers excluding it.
        self.make('Work/Notes')
        _, rows = self.tree('Work', {'root': [folder('Work')], 'Work-id': [doc('Notes')]})
        row = rows['Notes']
        self.assertEqual((row['where'], row['kind']), ('both', 'unsupported'))
        self.assertEqual(row['unsupported'], 'Google Docs and shortcuts do not sync')
        self.assertTrue(row['excludable'])

    def test_local_problem_wins_over_the_drive_side(self):
        self.make('outside/a')
        (self.home / 'Work').mkdir()
        (self.home / 'Work/link').symlink_to(self.home / 'outside')
        _, rows = self.tree('Work', {'root': [folder('Work')], 'Work-id': [doc('link')]})
        self.assertEqual(rows['link']['where'], 'both')
        self.assertEqual(rows['link']['kind'], 'unsupported')
        self.assertNotIn('Google Docs', rows['link']['unsupported'])

    def test_local_file_cannot_be_expanded_through_a_drive_folder_of_the_same_name(self):
        self.make('Work/both')
        _, rows = self.tree('Work', {'root': [folder('Work')], 'Work-id': [folder('both')]})
        self.assertEqual((rows['both']['where'], rows['both']['kind']), ('both', 'file'))

    def test_drive_names_that_cannot_form_a_path_are_shown_but_not_excludable(self):
        (self.home / 'Work').mkdir()
        _, rows = self.tree('Work', {'root': [folder('Work')], 'Work-id': [folder('a/b'), folder('..')]})
        for name in ('a/b', '..'):
            with self.subTest(name=name):
                self.assertEqual(rows[name]['kind'], 'unsupported')
                self.assertTrue(rows[name]['unsupported'])
                self.assertFalse(rows[name]['excludable'])

    def test_duplicate_drive_names_give_one_row(self):
        (self.home / 'Work').mkdir()
        value, _ = self.tree('Work', {'root': [folder('Work')], 'Work-id': [file('same'), file('same')]})
        self.assertEqual([row['name'] for row in value['rows']], ['same'])

    def test_drive_only_folder_lists_drive_rows(self):
        value, rows = self.tree('Archive', {'root': [folder('Archive')], 'Archive-id': [file('a')]})
        self.assertEqual(rows['a']['where'], 'drive')
        self.assertTrue(value['driveAvailable'])

    def test_home_folder_with_no_drive_match_has_only_home_rows(self):
        self.make('Work/x/a')
        value, rows = self.tree('Work/x', {'root': [folder('Work')], 'Work-id': [file('x')]})
        self.assertTrue(value['ok'])
        self.assertTrue(value['driveAvailable'])
        self.assertEqual(rows['a']['where'], 'home')

    def test_walk_goes_through_folders_only(self):
        # Drive allows a file and a folder with one name. A file's ID must not be used as a parent.
        (self.home / 'Work/x').mkdir(parents=True)
        listings = {'root': [folder('Work')], 'Work-id': [file('x'), folder('x', 'folder-x')],
                    'x-id': [file('wrong')], 'folder-x': [file('right')]}
        self.assertEqual(set(self.tree('Work/x', listings)[1]), {'right'})
        del listings['Work-id'][1]
        self.assertEqual(self.tree('Work/x', listings)[1], {})

    def test_tree_rejects_unsafe_paths(self):
        self.make('Work/a.txt', 'outside/secret', '.ssh/id_rsa')
        (self.home / 'Work/link').symlink_to(self.home / 'outside')
        (self.home / 'toplink').symlink_to(self.home / 'outside')
        listings = {'root': [folder('Work'), folder('.ssh')], 'Work-id': []}
        for path in ('Work/../..', '/etc', '.ssh', '.ssh/x', 'Work/link', 'Work/link/x', 'toplink',
                     'Work/a.txt', 'Work/gone', 'NotAFolder', '', 'Work/', 'Work//a.txt', 'Work/./a.txt'):
            with self.subTest(path=path), self.drive(listings):
                code, value, err = self.run_main('tree', '--path', path)
                self.assertEqual(code, 1)
                self.assertFalse(value['ok'])
                self.assertNotIn('Traceback', err)
                self.assertNotIn('rows', value)

    def test_a_file_is_refused_as_not_a_folder(self):
        self.make('Work/a.txt')
        os.mkfifo(self.home / 'Work/pipe')
        for path in ('Work/a.txt', 'Work/pipe'):
            with self.subTest(path=path), self.drive():
                self.assertIn('is not a folder', self.run_main('tree', '--path', path)[1]['lastError'])

    def test_unsafe_local_paths_are_rejected_without_network(self):
        self.make('Work/a.txt', 'outside/secret')
        (self.home / 'Work/link').symlink_to(self.home / 'outside')
        for path in ('Work/../..', '/etc', '.ssh', 'Work/link', 'Work/a.txt'):
            with self.subTest(path=path), patch('omarchy_drive.panel.HTTP') as http:
                self.assertEqual(self.run_main('tree', '--path', path)[0], 1)
                http.assert_not_called()

    def test_unreadable_folder_names_itself(self):
        self.make('Work/locked/a')
        locked = self.home / 'Work/locked'
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)
        with self.drive():
            code, value, _ = self.run_main('tree', '--path', 'Work/locked')
        self.assertEqual(code, 1)
        self.assertIn('Work/locked', value['lastError'])

    def test_tree_offline_returns_local_rows(self):
        self.make('Work/a')
        with self.offline():
            value = panel.tree(self.paths, 'Work')
        self.assertTrue(value['ok'])
        self.assertFalse(value['driveAvailable'])
        self.assertEqual([(row['name'], row['where']) for row in value['rows']], [('a', 'home')])

    def test_tree_offline_on_a_drive_only_path_is_empty_rather_than_an_error(self):
        with self.offline():
            value = panel.tree(self.paths, 'Archive')
        self.assertTrue(value['ok'])
        self.assertFalse(value['driveAvailable'])
        self.assertEqual(value['rows'], [])

    def test_tree_needs_a_connection_to_be_set_up(self):
        self.paths.settings.unlink()
        code, value, _ = self.run_main('tree', '--path', 'Work')
        self.assertEqual(code, 1)
        self.assertIn('Connect', value['lastError'])


class SelectionCommandTests(PanelCase):
    def baseline(self):
        store = Store(self.paths.state)
        try:
            return set(store.baseline())
        finally:
            store.close()

    def remember(self, *rows):
        store = Store(self.paths.state)
        try:
            for path in rows:
                store.remember(path, {'id': path, 'hash': 'a'})
        finally:
            store.close()

    def test_select_exclude_and_include_need_no_network(self):
        (self.home / 'Work').mkdir()
        with patch('omarchy_drive.panel.HTTP', side_effect=AssertionError('network')) as http:
            for argv in (('select', '--name', 'Work', '--enable'), ('exclude', '--path', 'Work/a'),
                         ('include', '--path', 'Work/a'), ('select', '--name', 'Work', '--disable')):
                with self.subTest(argv=argv):
                    code, value, _ = self.run_main(*argv)
                    self.assertEqual((code, value), (0, {'ok': True}))
        http.assert_not_called()

    def test_select_ticks_and_unticks(self):
        (self.home / 'Work').mkdir()
        self.remember('Work/a', 'Work2/b')
        self.run_main('select', '--name', 'Work', '--enable')
        self.assertIn('Work', read_json(self.paths.settings)['home_folders'])
        self.remember('Work/a')
        self.run_main('select', '--name', 'Work', '--disable')
        self.assertEqual(read_json(self.paths.settings)['home_folders'], {})
        self.assertEqual(self.baseline(), {'Work2/b'})
        self.assertTrue((self.home / 'Work').is_dir())

    def test_exclude_and_include_change_the_settings_and_that_baseline(self):
        self.remember('Work/x/a', 'Work/x2/b')
        atomic_json(self.cache, {'updated': time.time()})
        self.run_main('exclude', '--path', 'Work/x')
        self.assertEqual(read_json(self.paths.settings)['excluded'], ['Work/x'])
        self.assertEqual(self.baseline(), {'Work/x2/b'})
        self.assertFalse(self.cache.exists())
        self.run_main('include', '--path', 'Work/x')
        self.assertEqual(read_json(self.paths.settings)['excluded'], [])

    def test_whole_folder_exclusion_is_refused_with_a_reason(self):
        code, value, _ = self.run_main('exclude', '--path', 'Work')
        self.assertEqual(code, 1)
        self.assertIn('untick', value['lastError'])

    def test_selection_commands_refuse_during_sync(self):
        (self.home / 'Work').mkdir()
        original = self.paths.settings.read_bytes()
        for argv in (('select', '--name', 'Work', '--enable'), ('select', '--name', 'Work', '--disable'),
                     ('exclude', '--path', 'Work/a'), ('include', '--path', 'Work/a')):
            with self.subTest(argv=argv), self.paths.lock():
                code, value, _ = self.run_main(*argv)
                self.assertEqual(code, 1)
                self.assertIn('Another sync', value['lastError'])
                self.assertEqual(self.paths.settings.read_bytes(), original)
        with self.paths.lock():
            for call in (lambda: home_folders.select(self.paths, 'Work', True),
                         lambda: home_folders.set_excluded(self.paths, 'Work/a', True)):
                with self.assertRaises(Blocked):
                    call()
        self.assertEqual(self.paths.settings.read_bytes(), original)

    def test_old_commands_are_gone(self):
        (self.home / 'Work').mkdir()
        original = self.paths.settings.read_bytes()
        for argv in (['home-select', '--name', 'Work', '--enable'], ['select', '--root-files'],
                     ['select', '--no-root-files'], ['select', '--add', 'Work'], ['select', '--remove', 'Work']):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                panel.main(argv)
        self.assertEqual(self.paths.settings.read_bytes(), original)

    def test_select_needs_exactly_one_direction(self):
        for argv in (['select', '--name', 'Work'], ['select', '--name', 'Work', '--enable', '--disable']):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                panel.main(argv)


class StatusTests(PanelCase):
    def test_fresh_status_does_not_need_installed_helpers_or_network(self):
        self.paths.settings.unlink()
        with patch('omarchy_drive.panel.HTTP') as http:
            value = panel.status(self.paths)
        self.assertTrue(value['ok'])
        self.assertFalse(value['installed'])
        self.assertFalse(value['authenticated'])
        self.assertEqual(value['iconState'], 'setup')
        self.assertEqual(value['statusText'], 'Connect Google Drive')
        http.assert_not_called()

    def test_existing_connection_uses_backend_folder_not_upstream_default(self):
        value = panel.status(self.paths)
        self.assertTrue(value['authenticated'])
        self.assertEqual(value['cloudFolderName'], 'Omarchy')
        self.assertEqual(value['cloudUrl'], 'https://drive.google.com/drive/folders/root')

    def test_old_status_fields_are_gone(self):
        value = panel.status(self.paths)
        for removed in ('folderPath', 'selectedCount', 'rootFiles', 'homeFolders'):
            self.assertNotIn(removed, value)

    def test_status_reports_ticked_count_and_blocked_path(self):
        (self.home / 'Work').mkdir()
        (self.home / 'Other').mkdir()
        self.tick('Work', 'Other')
        atomic_json(self.paths.status, {'kind': 'blocked', 'message': 'stopped', 'blockedPath': 'Work/a'})
        value = panel.status(self.paths)
        self.assertEqual(value['blockedPath'], 'Work/a')
        self.assertEqual(value['tickedCount'], 2)
        self.assertEqual(value['statusText'], 'Needs attention')
        self.assertNotIn('private', json.dumps(value))

    def test_blocked_path_is_only_reported_while_blocked(self):
        for kind in ('success', 'setup', 'auth', 'offline', 'ready', 'error', 'interrupted'):
            with self.subTest(kind=kind):
                atomic_json(self.paths.status, {'kind': kind, 'message': 'x', 'blockedPath': 'Work/old'})
                self.assertEqual(panel.status(self.paths)['blockedPath'], '')
        atomic_json(self.paths.status, {'kind': 'blocked', 'message': 'x'})
        self.assertEqual(panel.status(self.paths)['blockedPath'], '')

    def test_nothing_ticked_says_so(self):
        for kind in ('ready', 'success'):
            with self.subTest(kind=kind):
                atomic_json(self.paths.status, {'kind': kind, 'message': 'x'})
                value = panel.status(self.paths)
                self.assertEqual(value['statusText'], 'No folders selected')
                self.assertEqual(value['tickedCount'], 0)
                self.assertEqual(value['iconState'], 'ready')

    def test_nothing_ticked_does_not_hide_a_problem(self):
        for kind, text in (('blocked', 'Needs attention'), ('offline', 'Offline'), ('auth', 'Sign in required')):
            with self.subTest(kind=kind):
                atomic_json(self.paths.status, {'kind': kind, 'message': 'x'})
                self.assertEqual(panel.status(self.paths)['statusText'], text)

    def test_a_ticked_folder_keeps_the_normal_label(self):
        (self.home / 'Work').mkdir()
        self.tick('Work')
        self.assertEqual(panel.status(self.paths)['statusText'], 'Ready to sync')

    def test_status_command_prints_json_without_the_token(self):
        code, value, _ = self.run_main('status')
        self.assertEqual(code, 0)
        self.assertTrue(value['ok'])
        self.assertNotIn('private', json.dumps(value))


class MainTests(PanelCase):
    def test_missing_helpers_give_actionable_sync_error(self):
        result = subprocess.run(['python3', str(ROOT / 'gdrive-sync.py'), 'sync'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)['ok'])
        self.assertIn('Install helpers', result.stderr)

    def test_error_messages_are_sanitised_on_both_streams(self):
        message = 'A Drive name\x1b[31m with\ncontrol\x07 characters'
        with patch('omarchy_drive.panel.status', side_effect=Blocked(message)):
            code, value, err = self.run_main('status')
        self.assertEqual(code, 1)
        for text in (value['lastError'], err.rstrip('\n')):
            self.assertTrue(text.isprintable(), repr(text))
            self.assertIn('A Drive name', text)
            self.assertIn('characters', text)

    def test_local_errors_stay_generic(self):
        with patch('omarchy_drive.panel.status', side_effect=OSError('/secret/path')):
            code, value, err = self.run_main('status')
        self.assertEqual(code, 1)
        self.assertNotIn('/secret/path', value['lastError'] + err)

    def test_folders_command_prints_the_listing(self):
        (self.home / 'Work').mkdir()
        with self.drive():
            code, value, _ = self.run_main('folders')
        self.assertEqual(code, 0)
        self.assertEqual(value['folders'][0]['name'], 'Work')

    def test_tree_command_prints_the_listing(self):
        self.make('Work/a')
        with self.drive():
            code, value, _ = self.run_main('tree', '--path', 'Work')
        self.assertEqual(code, 0)
        self.assertEqual(value['rows'][0]['path'], 'Work/a')

    def test_open_backups_uses_state_directory(self):
        with patch('omarchy_drive.panel.subprocess.run') as run:
            code, value, _ = self.run_main('open-backups')
        self.assertEqual((code, value), (0, {'ok': True}))
        self.assertEqual(run.call_args.args[0], ['xdg-open', str(self.paths.backups)])
        self.assertTrue(self.paths.backups.is_dir())
        self.assertEqual(stat.S_IMODE(self.paths.backups.stat().st_mode), 0o700)

    def test_open_backups_tightens_an_existing_folder_and_needs_no_connection(self):
        self.paths.settings.unlink()
        self.paths.backups.mkdir(mode=0o755)
        self.paths.backups.chmod(0o755)
        with patch('omarchy_drive.panel.subprocess.run') as run:
            code, _, _ = self.run_main('open-backups')
        self.assertEqual(code, 0)
        run.assert_called_once()
        self.assertEqual(stat.S_IMODE(self.paths.backups.stat().st_mode), 0o700)

    def test_open_backups_works_while_migration_is_blocked(self):
        self.make('GoogleDrive/keep.txt')
        atomic_json(self.paths.settings, {'folder_id': 'root', 'local_dir': str(self.home / 'GoogleDrive')})
        before = self.paths.settings.read_bytes()
        with patch('omarchy_drive.panel.subprocess.run') as run:
            code, value, _ = self.run_main('open-backups')
        self.assertEqual((code, value), (0, {'ok': True}))
        self.assertEqual(run.call_args.args[0], ['xdg-open', str(self.paths.backups)])
        self.assertEqual(self.paths.settings.read_bytes(), before)
        # Only this command skips migrating.
        code, value, _ = self.run_main('status')
        self.assertEqual(code, 1)
        self.assertIn('keep.txt', value['lastError'])
        self.assertEqual(self.paths.settings.read_bytes(), before)

    def test_open_backups_refuses_a_symlink(self):
        target = self.home / 'elsewhere'
        target.mkdir()
        self.paths.backups.symlink_to(target)
        with patch('omarchy_drive.panel.subprocess.run') as run:
            code, value, _ = self.run_main('open-backups')
        self.assertEqual(code, 1)
        self.assertIn('symbolic link', value['lastError'])
        run.assert_not_called()
