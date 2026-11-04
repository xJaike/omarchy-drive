import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from omarchy_drive import home_folders
from omarchy_drive.panel import reveal
from omarchy_drive.storage import Paths, Blocked, atomic_json

ROOT = Path(__file__).resolve().parents[1]


class RevealTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {'HOME':self.tmp.name, 'XDG_CONFIG_HOME':self.tmp.name+'/config', 'XDG_STATE_HOME':self.tmp.name+'/state'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.paths = Paths()
        self.paths.prepare()
        self.root = Path(self.tmp.name) / 'Documents'
        self.root.mkdir()
        settings = {'folder_id':'root'}
        home_folders.record_identity(settings, 'Documents', self.root)
        atomic_json(self.paths.settings, settings)

    def test_reveals_parent_without_executing_file(self):
        parent = self.root / 'Reports'
        parent.mkdir()
        file = parent / 'click-me.desktop'
        file.write_text('[Desktop Entry]\nExec=anything\n')
        with patch('omarchy_drive.panel.subprocess.run') as run:
            run.return_value.returncode = 0
            self.assertTrue(reveal(self.paths, 'Documents/Reports/click-me.desktop')['ok'])
        self.assertEqual(run.call_args.args[0], ['xdg-open',str(parent)])
        self.assertIn('timeout',run.call_args.kwargs)

    def test_missing_file_falls_back_to_nearest_existing_parent(self):
        with patch('omarchy_drive.panel.subprocess.run') as run:
            run.return_value.returncode = 0
            reveal(self.paths, 'Documents/Removed/subfolder/file.pdf')
        self.assertEqual(run.call_args.args[0], ['xdg-open',str(self.root)])

    def test_unticked_first_component_is_blocked(self):
        (Path(self.tmp.name) / 'Pictures').mkdir()
        for path in ('Pictures', 'Pictures/a.jpg', 'Removed/subfolder/file.pdf'):
            with self.subTest(path=path), patch('omarchy_drive.panel.subprocess.run') as run:
                with self.assertRaises(Blocked):
                    reveal(self.paths, path)
                run.assert_not_called()

    def test_traversal_and_symlinks_never_open_outside_sync_root(self):
        (self.root/'link').symlink_to(Path(self.tmp.name))
        for path in ('../outside', '/etc/passwd', 'Documents/link/outside', 'Documents/../../outside'):
            with self.subTest(path=path), patch('omarchy_drive.panel.subprocess.run') as run:
                with self.assertRaises(Blocked):
                    reveal(self.paths,path)
                run.assert_not_called()

    def test_file_manager_failure_is_actionable(self):
        with patch('omarchy_drive.panel.subprocess.run') as run:
            run.return_value.returncode = 1
            with self.assertRaisesRegex(Blocked,'file manager'):
                reveal(self.paths,'Documents/file.txt')


@unittest.skipUnless(shutil.which('node'), 'node is not installed')
class ActivityModelTests(unittest.TestCase):
    def test_recent_rows_are_safe_deduplicated_and_sorted(self):
        script = r'''
const assert = require('node:assert/strict');
const model = require('./Model.js');
const rows = model.recentFileRows([
  null,
  {path:'Documents/a.pdf', action:'upload', state:'failed',time:1,error:'Network error'},
  {path:'Documents/a.pdf', action:'download',state:'completed',time:3,size:100},
  {path:'Photos/long\u001b.jpg',action:'upload',state:'failed',time:2},
]);
assert.equal(rows.length,2);
assert.equal(rows[0].label,'Downloaded');
assert.equal(rows[0].name,'a.pdf');
assert.equal(rows[0].parent,'Documents');
assert.equal(rows[0].failed,false);
assert.equal(rows[1].label,'Upload failed');
assert.equal(model.fileName('a\nb.txt'),'a b.txt');
assert.equal(model.parentPath('root.txt'),'Synced folder');
assert.deepEqual(model.recentFileRows('bad'),[]);
assert.equal(model.transferEta(3),'3s');
assert.equal(model.transferEta(80),'1m 20s');
assert.ok(model.wrappedPath('a'.repeat(120)).split('\n').every(line => line.length<=56));
console.log('Model checks passed');
'''
        result = subprocess.run(['node','-e',script],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
