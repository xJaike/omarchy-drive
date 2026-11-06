// Run with: node tests/model_flatten.test.js
const assert = require('node:assert/strict');
const model = require('../Model.js');

const folders = [{name: 'Docs'}, {name: 'Work'}];
const children = {
  Work: [{name: 'Sub', path: 'Work/Sub', kind: 'dir'}, {name: 'a', path: 'Work/a', kind: 'file'}],
  'Work/Sub': [{name: 'b', path: 'Work/Sub/b', kind: 'file'}],
};
const flat = expanded => model.flattenRows(folders, children, expanded);
const paths = rows => rows.map(row => row.path);
const depths = rows => rows.map(row => row.depth);

// flattenRows: nothing expanded.
assert.deepEqual(paths(flat({})), ['Docs', 'Work']);
assert.deepEqual(depths(flat({})), [0, 0]);

// flattenRows: one level, children in place.
assert.deepEqual(paths(flat({Work: true})), ['Docs', 'Work', 'Work/Sub', 'Work/a']);
assert.deepEqual(depths(flat({Work: true})), [0, 0, 1, 1]);

// flattenRows: two levels, the grandchild directly after its parent.
assert.deepEqual(paths(flat({Work: true, 'Work/Sub': true})), ['Docs', 'Work', 'Work/Sub', 'Work/Sub/b', 'Work/a']);
assert.deepEqual(depths(flat({Work: true, 'Work/Sub': true})), [0, 0, 1, 2, 1]);

// flattenRows: a collapsed parent hides an expanded child.
assert.deepEqual(paths(flat({'Work/Sub': true})), ['Docs', 'Work']);

// flattenRows: expanded with no children loaded yet.
assert.deepEqual(paths(flat({Docs: true})), ['Docs', 'Work']);

// flattenRows: a folder named after an Object.prototype member must not read inherited values.
assert.deepEqual(paths(model.flattenRows([{name: 'constructor'}, {name: 'toString'}], {}, {})), ['constructor', 'toString']);

// flattenRows: rows are copies, so a row's depth is never written into the shared list.
assert.equal(flat({})[0].name, 'Docs');
assert.equal(folders[0].depth, undefined);
assert.equal(folders[0].path, undefined);

// openPaths: expanded rows on screen, parents first; a path under a closed or vanished parent is not.
assert.deepEqual(model.openPaths(folders, children, {Work: true, 'Work/Sub': true}), ['Work', 'Work/Sub']);
assert.deepEqual(model.openPaths(folders, children, {'Work/Sub': true}), []);
assert.deepEqual(model.openPaths(folders, children, {Work: true, 'Work/Gone': true}), ['Work']);
assert.deepEqual(model.openPaths(folders, {}, {Gone: true, Docs: true}), ['Docs']);

// withExclusion: changes the one row, in a copy, ahead of the re-fetch that confirms it.
const listed = [{path: 'Work/a', excluded: false}, {path: 'Work/b', excluded: false}];
assert.deepEqual(model.withExclusion(listed, 'Work/a', true), [{path: 'Work/a', excluded: true}, {path: 'Work/b', excluded: false}]);
assert.equal(listed[0].excluded, false);

// canExpand: top-level rows, unless they cannot be used.
assert.equal(model.canExpand({depth: 0, name: 'Work', error: ''}), true);
assert.equal(model.canExpand({depth: 0, name: 'Work', error: '~/Work is missing.'}), false);
// canExpand: child folders, but never one with an unsupported reason, whatever its kind says.
assert.equal(model.canExpand({depth: 1, kind: 'dir', unsupported: ''}), true);
assert.equal(model.canExpand({depth: 1, kind: 'dir', unsupported: 'Nested mounts do not sync'}), false);
assert.equal(model.canExpand({depth: 1, kind: 'unsupported', unsupported: 'Symbolic links do not sync'}), false);
assert.equal(model.canExpand({depth: 1, kind: 'file', unsupported: ''}), false);

// rowEnabled: a top-level error disables the row unless it is ticked, so it can still be unticked.
assert.equal(model.rowEnabled({depth: 0, selected: false, error: ''}), true);
assert.equal(model.rowEnabled({depth: 0, selected: false, error: 'hidden'}), false);
assert.equal(model.rowEnabled({depth: 0, selected: true, error: '~/Work is missing.'}), true);
assert.equal(model.rowEnabled({selected: false, error: 'hidden'}), false);
// rowEnabled: child rows follow excludable.
assert.equal(model.rowEnabled({depth: 1, excludable: true, excluded: false, inheritedExclusion: false}), true);
assert.equal(model.rowEnabled({depth: 1, excludable: false, excluded: false, inheritedExclusion: false}), false);
assert.equal(model.rowEnabled({depth: 2, excludable: false, excluded: false, inheritedExclusion: true}), false);
assert.equal(model.rowEnabled({depth: 2, excludable: false, excluded: true, inheritedExclusion: true}), false);
// rowEnabled: a Google Doc keeping an old exclusion can still be cleared.
assert.equal(model.rowEnabled({depth: 1, excludable: false, excluded: true, inheritedExclusion: false}), true);

// includesOnToggle: decided by the exclusion itself, not by excludable.
assert.equal(model.includesOnToggle({excludable: false, excluded: true, inheritedExclusion: false}), true);
assert.equal(model.includesOnToggle({excludable: true, excluded: true, inheritedExclusion: false}), true);
assert.equal(model.includesOnToggle({excludable: false, excluded: true, inheritedExclusion: true}), false);
assert.equal(model.includesOnToggle({excludable: true, excluded: false, inheritedExclusion: false}), false);

// childChecked: ticked means it syncs, so an exclusion or a Doc reads as unticked.
assert.equal(model.childChecked({excludable: true, excluded: false}, true), true);
assert.equal(model.childChecked({excludable: true, excluded: true}, true), false);
assert.equal(model.childChecked({excludable: false, excluded: false, inheritedExclusion: true}, true), false);
assert.equal(model.childChecked({excludable: false, excluded: false, unsupported: 'Google Docs and shortcuts do not sync'}, true), false);
// childChecked: nothing under an unticked folder syncs, so none of it reads as ticked.
assert.equal(model.childChecked({excludable: true, excluded: false}, false), false);
assert.equal(model.childChecked({excludable: true, excluded: false}), false);
// topName: the ticked folder a row belongs to.
assert.equal(model.topName('Work/Sub/b'), 'Work');
assert.equal(model.topName('Work'), 'Work');

// rowTickable: a row that can change, while nothing is running.
assert.equal(model.rowTickable({depth: 0, selected: false, error: ''}, false, false), true);
assert.equal(model.rowTickable({depth: 0, selected: false, error: ''}, true, false), false);
assert.equal(model.rowTickable({depth: 0, selected: false, error: ''}, false, true), false);
assert.equal(model.rowTickable({depth: 1, excludable: true}, true, false), false);
assert.equal(model.rowTickable({depth: 1, excludable: true}, false, true), false);
assert.equal(model.rowTickable({depth: 0, selected: false, error: 'hidden'}, false, false), false);
assert.equal(model.rowTickable(null, false, false), false);
// rowTickable: expanding is decided separately and stays available.
assert.equal(model.canExpand({depth: 0, name: 'Work', error: ''}), true);

// withTicks: ticks the backend accepted, kept in the list whatever the next read does.
const ticked = [{name: 'Docs', selected: false, where: 'home'}, {name: 'Work', selected: true}, {name: 'constructor', selected: true}];
assert.deepEqual(model.withTicks(ticked, {Docs: true, Work: false}),
  [{name: 'Docs', selected: true, where: 'home'}, {name: 'Work', selected: false}, {name: 'constructor', selected: true}]);
assert.equal(ticked[0].selected, false);
// withTicks: no accepted tick leaves the same list, so its rows are not rebuilt.
assert.equal(model.withTicks(ticked, {}), ticked);

// emptyNote: nothing can be called empty when Drive went unread.
const offlineNote = 'Drive could not be read. Showing local folders only.';
assert.equal(model.emptyNote(false, offlineNote), 'Empty');
assert.equal(model.emptyNote(true, offlineNote), offlineNote);

// panelNotice: a command that failed never hides a sync error, and both show.
const notice = values => model.panelNotice(Object.assign({actionStatus: '', controlError: '', lastError: '', foldersError: '', warning: ''}, values));
assert.equal(notice({lastError: 'Sync stopped', controlError: 'Command failed'}), 'Sync stopped\nCommand failed');
assert.equal(notice({lastError: 'Sync stopped'}), 'Sync stopped');
assert.equal(notice({controlError: 'Command failed'}), 'Command failed');
// panelNotice: the same problem reaching both is shown once, however each stream ended it.
assert.equal(notice({lastError: 'Move these out\n', controlError: 'Move these out'}), 'Move these out');
// panelNotice: a change under way, then the quieter lines.
assert.equal(notice({actionStatus: 'Applying change…', lastError: 'Sync stopped'}), 'Applying change…');
assert.equal(notice({lastError: 'Sync stopped', foldersError: 'No list'}), 'Sync stopped');
assert.equal(notice({foldersError: 'No list', warning: 'Scope'}), 'No list');
assert.equal(notice({warning: 'Scope'}), 'Scope');
assert.equal(notice({}), '');

// rowWarns: a ticked row that cannot sync. An item inside a folder is skipped, so it is only greyed out.
assert.equal(model.rowWarns({depth: 0, selected: true, error: 'Missing'}), true);
assert.equal(model.rowWarns({depth: 0, selected: false, error: 'Hidden'}), false);
assert.equal(model.rowWarns({depth: 1, excludable: false, excluded: false, unsupported: 'Symbolic links do not sync'}), false);
assert.equal(model.rowWarns({depth: 1, selected: true, error: 'Why'}), false);
assert.equal(model.rowWarns({depth: 1, excludable: true, excluded: false, unsupported: ''}), false);

// rowLabel: top level.
assert.equal(model.rowLabel({depth: 0, where: 'both'}, true), 'Home + Drive');
assert.equal(model.rowLabel({depth: 0, where: 'home'}, false), 'Home only');
assert.equal(model.rowLabel({depth: 0, where: 'drive', name: 'Work'}, false), 'Drive only');
assert.equal(model.rowLabel({depth: 0, where: 'drive', name: 'Work'}, true), 'Drive only · will create ~/Work');
assert.equal(model.rowLabel({depth: 0, where: 'home', error: 'Why not'}, false), 'Why not');
// rowLabel: children.
assert.equal(model.rowLabel({depth: 1, excluded: true, unsupported: 'Reason'}), 'excluded');
assert.equal(model.rowLabel({depth: 2, excluded: false, inheritedExclusion: true}), 'excluded');
assert.equal(model.rowLabel({depth: 1, excluded: false, unsupported: 'Reason'}), 'Reason');
assert.equal(model.rowLabel({depth: 1, excluded: false, unsupported: ''}), '');

// rowSize: nothing where there is no local total to show.
assert.equal(model.rowSize({depth: 0, where: 'home', localBytes: 2000, approx: false}), '2 KB');
assert.equal(model.rowSize({depth: 0, where: 'both', localBytes: 2000, approx: true}), '≈ 2 KB');
assert.equal(model.rowSize({depth: 0, where: 'drive', localBytes: 0}), '');
assert.equal(model.rowSize({depth: 0, where: 'home', localBytes: 0, error: 'Why not'}), '');
assert.equal(model.rowSize({depth: 1, kind: 'file', size: 2000}), '2 KB');
assert.equal(model.rowSize({depth: 1, kind: 'file', size: 0}), '0 B');
assert.equal(model.rowSize({depth: 1, kind: 'dir', size: 0}), '');
assert.equal(model.rowSize({depth: 1, kind: 'unsupported', size: 0}), '');

// skippedHeading: counts the items, singular and plural; nothing when there are none.
assert.equal(model.skippedHeading(0), '');
assert.equal(model.skippedHeading(1), '1 item cannot sync');
assert.equal(model.skippedHeading(3), '3 items cannot sync');

// skippedMore: how many the panel leaves out, and where the rest are.
assert.equal(model.skippedMore(3, 3), '');
assert.equal(model.skippedMore(60, 50), '+ 10 more · omarchy-drive skipped');

// blockedAction: a top-level name can only be unticked.
assert.equal(model.blockedAction('Work/socket'), 'Exclude this');
assert.equal(model.blockedAction('Work'), 'Untick this');

// indexOfPath: the row itself, else its nearest visible ancestor, so a closed tree keeps the cursor nearby.
const closed = flat({});
assert.equal(model.indexOfPath(flat({Work: true}), 'Work/a'), 3);
assert.equal(model.indexOfPath(closed, 'Work/Sub/b'), 1);
assert.equal(model.indexOfPath(closed, 'Gone/x'), -1);

// parseTree: a usable shape from good and bad output.
assert.deepEqual(model.parseTree('{"ok":true,"path":"Work","driveAvailable":false,"rows":[{"name":"a"}]}').rows, [{name: 'a'}]);
assert.equal(model.parseTree('{"ok":true,"path":"Work","driveAvailable":false,"rows":[]}').driveAvailable, false);
assert.equal(model.parseTree('not json').ok, false);
assert.deepEqual(model.parseTree('not json').rows, []);
assert.equal(model.parseTree('').ok, false);

// folderSummary: counts ticked folders and no longer mentions root files.
assert.equal(model.folderSummary({tickedCount: 0, localBytes: 0}), 'No folders · 0 B on disk');
assert.equal(model.folderSummary({tickedCount: 1, localBytes: 2000}), '1 folder · 2 KB on disk');
assert.equal(model.folderSummary({tickedCount: 3, localBytes: 2000, localBytesApprox: true}), '3 folders · ≈ 2 KB on disk');

// Removed helpers stay removed.
for (const name of ['rootFilesMeta', 'folderMeta', 'folderGlyph', 'shortHomePath'])
  assert.equal(model[name], undefined, name);

// Sync progress: the checking header, the position in the plan, the active glyph and the waiting rows.
assert.equal(model.checkingHeading({side: 'local', count: 12480}), 'CHECKING YOUR FILES · 12,480 CHECKED');
assert.equal(model.checkingHeading({side: 'drive', count: 7}), 'CHECKING GOOGLE DRIVE · 7 CHECKED');
assert.equal(model.groupDigits(1234567), '1,234,567');
assert.equal(model.groupDigits(999), '999');
assert.equal(model.syncHeading(120, 3), 'SYNCING · 4 OF 120');
assert.equal(model.syncHeading(120, 120), 'SYNCING · 120 OF 120');
assert.equal(model.syncHeading(0, 0), 'ACTIVE TRANSFER');
assert.equal(model.activeGlyph({action: 'download'}, {direction: 'prepare'}), 'circle-arrow-down');
assert.equal(model.activeGlyph({action: 'upload'}, {direction: 'backup'}), 'circle-arrow-up');
assert.equal(model.activeGlyph({action: 'mkdir-remote'}, {direction: 'prepare'}), 'folder-open');
assert.equal(model.activeGlyph({action: 'conflict'}, {direction: 'download'}), 'circle-arrow-down');
assert.equal(model.activeGlyph(null, {direction: 'upload'}), 'circle-arrow-up');
assert.equal(model.activeGlyph(null, null), 'rotate-cw');
assert.equal(model.hasBytes({total: 10}), true);
assert.equal(model.hasBytes({total: 0}), false);
assert.equal(model.hasBytes(null), false);
const waiting = model.waitingRows([{action: 'upload', path: 'Docs/a.txt'}, {action: 'mkdir-remote', path: 'Docs/New'},
  {action: 'download', path: 'b.txt'}, {action: 'upload', path: 'c.txt'}], 3);
assert.deepEqual(waiting.map(row => [row.name, row.parent, row.glyph]),
  [['a.txt', 'Docs', '↑'], ['New', 'Docs', '+'], ['b.txt', 'Synced folder', '↓']]);
assert.deepEqual(model.waitingRows(null, 3), []);
assert.equal(model.waitingMore(117, 3), '+ 114 more');
assert.equal(model.waitingMore(3, 3), '');

console.log('Model flatten checks passed');
