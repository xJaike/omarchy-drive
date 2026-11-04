import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from omarchy_drive import panel
from omarchy_drive.drive import Drive, FOLDER
from omarchy_drive.storage import Paths, Store, Blocked, atomic_json, read_json
from test_api import ScriptHTTP, json_response
import test_engine as engine_helpers

ROOT = Path(__file__).resolve().parents[1]


class SelectionTests(unittest.TestCase):
    setUp = engine_helpers.EngineTests.setUp
    engine = engine_helpers.EngineTests.engine
    write = engine_helpers.EngineTests.write

    def test_deselected_files_are_not_deleted_or_overwritten(self):
        self.write('keep/a', b'a')
        self.write('skip/b', b'b')
        self.write('root.txt', b'root')
        self.engine().sync()
        self.settings.update(selected_folders=['keep'], root_files=False)
        del self.drive.files['skip/b']
        self.drive.put('root.txt', b'cloud root edit', self.drive.files['root.txt']['id'])
        self.engine().sync()
        self.assertEqual((self.local / 'skip/b').read_bytes(), b'b')
        self.assertEqual((self.local / 'root.txt').read_bytes(), b'root')
        self.assertNotIn('skip/b', self.drive.files)

    def test_remote_scan_only_descends_into_selected_folders(self):
        item = dict(id='file', name='a', mimeType='text/plain', md5Checksum='hash', size='1')
        http = ScriptHTTP([
            json_response({'id': 'root', 'mimeType': FOLDER}),
            json_response({'files': [dict(id='keep-id',name='keep',mimeType=FOLDER),
                                     dict(id='skip-id',name='skip',mimeType=FOLDER),
                                     dict(item, name='root-file')]}),
            json_response({'files': [item]}),
        ])
        drive = Drive(http, self.store, self.paths.state)
        result = drive.scan('root', selected=['keep'], root_files=False)
        self.assertEqual(set(result), {'keep', 'keep/a'})
        self.assertEqual(len(http.calls), 3)


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {
            'HOME': self.tmp.name, 'XDG_CONFIG_HOME': self.tmp.name + '/config',
            'XDG_STATE_HOME': self.tmp.name + '/state', 'XDG_DATA_HOME': self.tmp.name + '/data'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.paths = Paths()
        self.paths.prepare()
        self.local = Path(self.tmp.name) / 'GoogleDrive'
        self.local.mkdir()
        atomic_json(self.paths.settings, {'folder_id': 'root', 'local_dir': str(self.local), 'folder_name': 'Omarchy'})
        atomic_json(self.paths.tokens, {'refresh_token': 'private'})

    def test_fresh_status_does_not_need_installed_helpers_or_network(self):
        self.paths.settings.unlink()
        with patch('omarchy_drive.panel.HTTP') as http:
            value = panel.status(self.paths)
        self.assertTrue(value['ok'])
        self.assertFalse(value['installed'])
        self.assertFalse(value['authenticated'])
        self.assertEqual(value['iconState'], 'setup')
        http.assert_not_called()

    def test_disabling_root_files_keeps_all_folder_selection(self):
        store = Store(self.paths.state)
        store.remember('root.txt', {'id':'root-file', 'hash':'a'})
        store.remember('folder/a', {'id':'a', 'hash':'a'})
        store.close()
        panel.select(self.paths, argparse.Namespace(add=None, remove=None, root_files=False))
        settings = read_json(self.paths.settings)
        self.assertIsNone(settings.get('selected_folders'))
        self.assertFalse(settings['root_files'])
        store = Store(self.paths.state)
        try:
            self.assertEqual(set(store.baseline()), {'folder/a'})
        finally:
            store.close()

    def test_deselection_resets_only_affected_baseline(self):
        atomic_json(self.paths.state / 'panel-cache.json', {'updated': 1, 'folders':[{'name':'keep'}, {'name':'skip'}]})
        store = Store(self.paths.state)
        store.remember('keep/a', {'id':'a', 'hash':'a'})
        store.remember('skip/b', {'id':'b', 'hash':'b'})
        store.close()
        panel.select(self.paths, argparse.Namespace(add=None, remove='skip', root_files=None))
        self.assertEqual(read_json(self.paths.settings)['selected_folders'], ['keep'])
        store = Store(self.paths.state)
        try:
            self.assertEqual(set(store.baseline()), {'keep/a'})
        finally:
            store.close()
        self.assertFalse((self.paths.state / 'panel-cache.json').exists())

    def test_selection_refuses_to_race_a_running_sync(self):
        original = self.paths.settings.read_bytes()
        with self.paths.lock(), self.assertRaises(Blocked):
            panel.select(self.paths, argparse.Namespace(add='a', remove=None, root_files=None))
        self.assertEqual(self.paths.settings.read_bytes(), original)

    def test_existing_connection_uses_backend_folder_not_upstream_default(self):
        value = panel.status(self.paths)
        self.assertTrue(value['authenticated'])
        self.assertEqual(value['folderPath'], str(self.local))
        self.assertEqual(value['cloudFolderName'], 'Omarchy')
        self.assertNotIn('private', json.dumps(value))

    def test_missing_helpers_give_actionable_sync_error(self):
        result = subprocess.run(['python3', str(ROOT / 'gdrive-sync.py'), 'sync'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)['ok'])
        self.assertIn('Install helpers', result.stderr)

    def test_folder_json_matches_panel_contract(self):
        settings = read_json(self.paths.settings)
        settings['selected_folders'] = ['keep']
        atomic_json(self.paths.settings, settings)
        (self.local / 'keep').mkdir()
        (self.local / 'keep/a').write_bytes(b'local')
        fake = ScriptHTTP([
            json_response({'id':'root','mimeType':FOLDER}),
            json_response({'files':[dict(id='keep', name='keep', mimeType=FOLDER),dict(id='skip',name='skip',mimeType=FOLDER)]}),
            json_response({'storageQuota': {'usage':'10','limit':'100'}}),
        ])
        with patch('omarchy_drive.panel.HTTP', return_value=fake):
            value = panel.folders(self.paths)
        self.assertEqual(value['selectedCount'], 1)
        self.assertEqual(value['folders'][0]['name'], 'keep')
        self.assertTrue(value['folders'][0]['selected'])
        self.assertFalse(value['folders'][1]['selected'])
        self.assertEqual(value['localBytes'], 5)
        self.assertEqual(value['quotaBytes'], 100)
        with patch('omarchy_drive.panel.HTTP') as no_network:
            panel.folders(self.paths)
        no_network.assert_not_called()
