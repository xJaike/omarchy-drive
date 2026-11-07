import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from omarchy_drive.activity import Activity, current_lines, detail_lines
from omarchy_drive.cli import main, status
from omarchy_drive.engine import Action
from omarchy_drive.storage import Blocked, Paths, Store, atomic_json, read_json
from tests.test_engine import MemoryDrive


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = Paths()
        root = Path(self.tmp.name)
        self.paths.config = root / 'config'
        self.paths.state = root / 'state'
        self.paths.settings = self.paths.config / 'settings.json'
        self.paths.status = self.paths.state / 'status.json'
        self.paths.prepare()
        atomic_json(self.paths.settings, {'folder_name': 'Omarchy', 'folder_id': 'root'})
        self.now = 0
        self.activity = Activity(self.paths, lambda: self.now)

    def test_out_of_order_completions_keep_queue_order_and_counts(self):
        actions = [Action('upload', f'Work/{i}') for i in range(1000)]
        self.activity.start()
        self.activity.planned(actions)
        self.activity.begin(actions[17])
        data = read_json(self.paths.status)
        self.assertEqual(data['queued'], 999)
        self.assertNotIn('Work/17', [item['path'] for item in data['queue']])
        self.activity.complete(actions[17])
        self.activity.begin(actions[0])
        self.activity.complete(actions[0])
        data = read_json(self.paths.status)
        self.assertEqual((data['pending'], data['queued'], data['completed']), (998, 998, 2))
        self.assertEqual([item['path'] for item in data['queue']],
                         [action.path for action in actions[1:22] if action.path != 'Work/17'])

    def test_shared_state_tracks_direction_full_path_queue_and_rate(self):
        self.activity.start()
        actions = [Action('upload', 'Projects/report.pdf'), Action('download', 'Pictures/photo.jpg')]
        self.activity.planned(actions)
        self.activity.begin(actions[0])
        self.activity.phase('upload', actions[0].path, 1000)
        self.activity.transfer('upload', 'report.pdf', 100, 1000)
        self.now = 2
        self.activity.transfer('upload', 'report.pdf', 500, 1000)
        data = read_json(self.paths.status)
        self.assertEqual(data['current']['name'], 'Projects/report.pdf')
        self.assertEqual(data['current']['percent'], 50)
        self.assertEqual(data['current']['speed'], 200)
        self.assertEqual(data['current']['eta'], 2.5)
        self.assertEqual(data['queued'], 1)
        self.assertEqual(data['queue'][0]['path'], 'Pictures/photo.jpg')
        with self.paths.lock():
            displayed = status(self.paths)
        self.assertEqual(displayed['label'], 'Drive ↑ 50%')
        # The panel shows the files; the tooltip stays at the status and summary.
        self.assertEqual(displayed['tooltip'].splitlines(), ['Omarchy Drive · Sync in progress', '0 uploaded · 0 downloaded'])

    def test_tooltip_is_the_status_and_summary(self):
        self.activity.start()
        self.now = 1
        self.activity.checked('local', 'Work/a.txt')
        with self.paths.lock():
            self.assertEqual(status(self.paths)['tooltip'].splitlines(),
                             ['Omarchy Drive · Checking files', '0 uploaded · 0 downloaded'])
        self.activity.planned([Action('upload', 'Work/a.txt')])
        self.activity.file_done('upload', 'Work/a.txt', 10)
        self.activity.finish()
        self.assertEqual(status(self.paths)['tooltip'].splitlines(),
                         ['Omarchy Drive · Everything is up to date', '1 uploaded · 0 downloaded'])
        self.activity.start()
        self.activity.failure('offline', 'Google Drive is unreachable. The next sync will retry.')
        self.assertEqual(status(self.paths)['tooltip'].splitlines(),
                         ['Omarchy Drive · Offline', 'Google Drive is unreachable. The next sync will retry.',
                          '0 uploaded · 0 downloaded'])

    def test_tooltip_before_any_sync_is_the_status_alone(self):
        self.assertEqual(status(self.paths)['tooltip'].splitlines(), ['Omarchy Drive · Ready to sync'])

    def test_backup_download_is_distinct_from_user_download(self):
        self.activity.start()
        self.activity.phase('backup', 'report.pdf', 100)
        self.activity.transfer('download', 'report.pdf', 75, 100)
        self.assertEqual(self.activity.data['current']['direction'], 'backup')
        self.assertEqual(self.activity.data['summary']['downloaded'], 0)
        self.assertIn('Backing up', '\n'.join(current_lines(self.activity.data['current'])))

    def test_completed_files_and_failure_keep_accurate_counts(self):
        actions = [Action('upload', 'done.txt'), Action('download', 'broken.txt')]
        self.activity.start()
        self.activity.planned(actions)
        self.activity.begin(actions[0])
        self.activity.file_done('upload', 'done.txt', 10)
        self.activity.complete(actions[0])
        self.activity.begin(actions[1])
        self.activity.phase('download', 'broken.txt', 20)
        self.activity.failure('offline', 'Network unavailable')
        data = read_json(self.paths.status)
        self.assertEqual(data['completed'], 1)
        self.assertEqual(data['summary']['uploaded'], 1)
        self.assertEqual(data['summary']['downloaded'], 0)
        self.assertEqual(data['recent'][0]['path'], 'broken.txt')
        self.assertEqual(data['recent'][0]['state'], 'failed')
        self.assertEqual(data['recent'][1]['path'], 'done.txt')
        self.assertEqual(data['current']['state'], 'failed')
        self.assertIn('Stopped', '\n'.join(detail_lines(data)))
        self.assertNotIn('last_success', data)

    def test_idle_poll_resets_stale_totals_and_keeps_recent_files(self):
        self.activity.start()
        action = Action('upload', 'first.txt')
        self.activity.planned([action])
        self.activity.begin(action)
        self.activity.file_done('upload', 'first.txt')
        self.activity.complete(action)
        self.activity.finish()
        second = Activity(self.paths)
        second.start()
        second.planned([])
        second.finish()
        data = read_json(self.paths.status)
        self.assertEqual(data['summary']['uploaded'], 0)
        self.assertEqual(data['total'], 0)
        self.assertEqual(data['recent'][0]['path'], 'first.txt')

    def test_queue_is_bounded_and_reports_exact_remaining_count(self):
        self.activity.start()
        actions = [Action('upload', f'{i}.txt') for i in range(100)]
        self.activity.planned(actions)
        self.activity.begin(actions[0])
        data = read_json(self.paths.status)
        self.assertEqual(len(data['queue']), 20)
        self.assertEqual(data['queued'], 99)
        self.assertIn('+ 94 more changes', detail_lines(data, queue_limit=5))

    def test_checking_reports_the_path_and_count_and_clears_when_planned(self):
        self.activity.start()
        self.activity.checked('local', 'Work/a.txt')
        self.now = 1
        self.activity.checked('local', 'Work/b.txt')
        data = read_json(self.paths.status)
        self.assertEqual(data['checking'], {'side': 'local', 'path': 'Work/b.txt', 'count': 2})
        self.assertIn('Checking your files · 2 checked', detail_lines(data))
        self.assertIn('Work/b.txt', detail_lines(data))
        self.activity.checked('drive', 'Work')
        self.assertEqual(self.activity.data['checking'], {'side': 'drive', 'path': 'Work', 'count': 1})
        self.assertIn('Checking Google Drive · 1 checked', detail_lines(self.activity.data))
        self.activity.planned([])
        self.assertIsNone(read_json(self.paths.status)['checking'])

    def test_checking_writes_at_most_four_times_a_second(self):
        self.activity.start()
        with patch.object(self.paths, 'record', wraps=self.paths.record) as record:
            for i in range(100):
                self.activity.checked('local', f'{i}.txt')
            self.assertEqual(record.call_count, 0)
            self.now = .3
            self.activity.checked('local', 'last.txt')
            self.assertEqual(record.call_count, 1)
        self.assertEqual(read_json(self.paths.status)['checking']['count'], 101)

    def test_a_large_file_is_reported_before_it_is_read(self):
        # Hashing a large file can take minutes, so its name must not wait behind the four-a-second limit.
        self.activity.start()
        self.activity.checked('local', 'small.txt', 10)
        self.activity.checked('local', 'backup.sql', 8 * 1024 ** 3)
        self.assertEqual(read_json(self.paths.status)['checking']['path'], 'backup.sql')

    def test_checking_outside_a_sync_writes_nothing(self):
        # preview scans with an Activity it never starts, and must leave the status file alone.
        self.now = 10
        self.activity.checked('local', 'a.txt')
        self.assertFalse(self.paths.status.exists())

    def test_finishing_failing_or_restarting_clears_checking(self):
        for end in (self.activity.finish, lambda: self.activity.failure('offline', 'Offline'), self.activity.start):
            self.activity.start()
            self.now += 1
            self.activity.checked('local', 'a.txt')
            end()
            self.assertIsNone(read_json(self.paths.status)['checking'])

    def test_resumed_bytes_do_not_inflate_transfer_speed(self):
        self.activity.start()
        self.activity.phase('download', 'large.zip', 1000)
        self.activity.transfer('download', 'large.zip', 800, 1000)
        self.now = 2
        self.activity.transfer('download', 'large.zip', 900, 1000)
        self.assertEqual(self.activity.data['current']['speed'], 50)

    def test_stale_running_state_never_claims_an_active_transfer(self):
        self.activity.start()
        self.activity.phase('download', 'old.txt', 100)
        self.now += 1
        self.activity.checked('local', 'old.txt')
        displayed = status(self.paths)
        self.assertEqual(displayed['kind'], 'interrupted')
        self.assertIsNone(displayed['current'])
        self.assertIsNone(displayed['checking'])
        self.assertNotIn('Downloading: old.txt', displayed['tooltip'])

    def test_legacy_history_appears_in_hover_before_next_sync(self):
        store = Store(self.paths.state)
        try:
            store.event('upload: Projects/earlier.txt')
            store.event('download: Photos/earlier.jpg')
        finally:
            store.close()
        atomic_json(self.paths.status, {'kind': 'success', 'message': 'Up to date'})
        displayed = status(self.paths)
        self.assertEqual([(row['action'], row['path']) for row in displayed['recent']],
                         [('download', 'Photos/earlier.jpg'), ('upload', 'Projects/earlier.txt')])
        self.assertNotIn('0 uploaded', displayed['tooltip'])

    def test_tooltip_wraps_long_paths_and_removes_terminal_controls(self):
        self.activity.start()
        self.activity.failure('blocked', 'Cannot sync Long/' + 'a' * 180 + '\x1b.pdf')
        displayed = status(self.paths)
        self.assertTrue(all(len(line) <= 84 for line in displayed['tooltip'].splitlines()))
        self.assertNotIn('\x1b', displayed['tooltip'])

    def test_failure_writes_and_start_clears_blocked_path(self):
        self.activity.start()
        self.activity.failure('blocked', 'm', path='Work/a')
        self.assertEqual(read_json(self.paths.status)['blockedPath'], 'Work/a')
        Activity(self.paths).start()
        self.assertIsNone(read_json(self.paths.status).get('blockedPath'))

    def test_failure_with_no_path_replaces_an_earlier_path(self):
        self.activity.failure('blocked', 'm', path='Work/a')
        Activity(self.paths).failure('offline', 'n')
        self.assertIsNone(read_json(self.paths.status).get('blockedPath'))


class BlockedPathFromCommandTests(unittest.TestCase):
    """main and run_sync must hand the error's path to the status file, with a real engine and filesystem."""
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
        atomic_json(self.paths.settings, {'folder_id': 'root', 'home_folders': {'Work': {}}})
        (self.home/'Work').mkdir()
        self.drive = MemoryDrive()

    def clash(self):
        (self.home/'Work'/'clash').write_bytes(b'file')
        self.drive.files['Work/clash'] = {'id': 'clash', 'kind': 'dir', 'hash': None}

    def sync(self):
        def context(paths):
            store = Store(paths.state)
            self.addCleanup(store.close)
            return paths.configured(), store, self.drive
        with patch('omarchy_drive.cli.context', context), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(['sync'])

    def test_sync_failure_reaches_status_with_its_path(self):
        self.clash()
        self.assertEqual(self.sync(), 1)
        self.assertEqual(read_json(self.paths.status)['blockedPath'], 'Work/clash')

    def test_failure_before_the_sync_starts_reaches_status_with_its_path(self):
        with patch('omarchy_drive.cli.migrate', side_effect=Blocked('m', path='Work/a')):
            self.assertEqual(self.sync(), 1)
        self.assertEqual(read_json(self.paths.status)['blockedPath'], 'Work/a')

    def test_sync_reports_each_item_it_checks_on_both_sides(self):
        (self.home/'Work'/'a.txt').write_bytes(b'a')
        self.drive.files['Work'] = {'id': 'folder-Work', 'kind': 'dir', 'hash': None, 'name': 'Work'}
        self.drive.put('Work/b.txt', b'b')
        calls = []
        original = Activity.checked
        def spy(activity, side, path, size=0):
            calls.append((side, path))
            return original(activity, side, path, size)
        with patch.object(Activity, 'checked', spy):
            self.assertEqual(self.sync(), 0)
        self.assertIn(('local', 'Work/a.txt'), calls)
        self.assertIn(('drive', 'Work/b.txt'), calls)
        self.assertIsNone(read_json(self.paths.status)['checking'])

    def test_next_sync_clears_the_path(self):
        self.clash()
        self.sync()
        (self.home/'Work'/'clash').unlink()
        del self.drive.files['Work/clash']
        self.assertEqual(self.sync(), 0)
        self.assertIsNone(read_json(self.paths.status).get('blockedPath'))

    def test_skipped_command_prints_the_list(self):
        (self.home/'Work'/'link').symlink_to(self.home)
        self.sync()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(['skipped']), 0)
        self.assertIn('Work/link — Symbolic', output.getvalue())
        self.assertIn('1 item cannot sync', output.getvalue())

    def test_skipped_command_says_when_the_list_is_empty(self):
        self.sync()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(['skipped']), 0)
        self.assertIn('Every item could sync', output.getvalue())

    def test_preview_lists_skipped_items_and_writes_no_list_file(self):
        (self.home/'Work'/'link').symlink_to(self.home)
        output = io.StringIO()
        def context(paths):
            store = Store(paths.state)
            self.addCleanup(store.close)
            return paths.configured(), store, self.drive
        with patch('omarchy_drive.cli.context', context), contextlib.redirect_stdout(output):
            self.assertEqual(main(['preview']), 0)
        self.assertIn('Work/link — Symbolic', output.getvalue())
        self.assertIn('1 item cannot sync', output.getvalue())
        self.assertFalse((self.paths.state/'cannot-sync.txt').exists())

    def test_blocked_sync_keeps_the_last_list(self):
        (self.home/'Work'/'link').symlink_to(self.home)
        self.sync()
        self.drive.root_exists = False
        self.assertEqual(self.sync(), 1)
        self.assertEqual(read_json(self.paths.status)['skippedCount'], 1)
        self.assertTrue((self.paths.state/'cannot-sync.txt').exists())

    def test_tooltip_does_not_call_a_sync_with_skipped_items_everything(self):
        (self.home/'Work'/'link').symlink_to(self.home)
        self.sync()
        tooltip = status(self.paths)['tooltip']
        self.assertEqual(tooltip.splitlines(), ['Omarchy Drive · Up to date', '0 uploaded · 0 downloaded · 1 folders created · 1 item cannot sync'])

    def test_doctor_reports_items_that_cannot_sync(self):
        (self.home/'Work'/'link').symlink_to(self.home)
        output = io.StringIO()
        def context(paths):
            store = Store(paths.state)
            self.addCleanup(store.close)
            return paths.configured(), store, self.drive
        with patch('omarchy_drive.cli.context', context), contextlib.redirect_stdout(output):
            self.assertEqual(main(['doctor']), 0)
        self.assertIn('1 item cannot sync', output.getvalue())

