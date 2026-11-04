from pathlib import Path
import tempfile
import unittest

from omarchy_drive.activity import Activity, current_lines, detail_lines
from omarchy_drive.cli import status
from omarchy_drive.engine import Action
from omarchy_drive.storage import Paths, Store, atomic_json, read_json


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
        atomic_json(self.paths.settings, {'local_dir': '/home/example/GoogleDrive', 'folder_name': 'Omarchy', 'folder_id': 'root'})
        self.now = 0
        self.activity = Activity(self.paths, lambda: self.now)

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
        self.assertIn('Upload', displayed['tooltip'])
        self.assertIn('Download: Pictures/photo.jpg', displayed['tooltip'])
        self.assertIn('ETA 2s', displayed['tooltip'])

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
        displayed = status(self.paths)
        self.assertEqual(displayed['kind'], 'interrupted')
        self.assertIsNone(displayed['current'])
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
        self.assertIn('↑ Uploaded: Projects/earlier.txt', displayed['tooltip'])
        self.assertIn('↓ Downloaded: Photos/earlier.jpg', displayed['tooltip'])
        self.assertNotIn('0 uploaded', displayed['tooltip'])

    def test_tooltip_wraps_long_paths_and_removes_terminal_controls(self):
        self.activity.start()
        self.activity.phase('upload', 'Long/' + 'a' * 180 + '\x1b.pdf', 100)
        with self.paths.lock():
            displayed = status(self.paths)
        self.assertTrue(all(len(line) <= 84 for line in displayed['tooltip'].splitlines()))
        self.assertNotIn('\x1b', displayed['tooltip'])
