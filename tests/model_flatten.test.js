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
assert.equal(model.childChecked({excludable: true, excluded: false}), true);
assert.equal(model.childChecked({excludable: true, excluded: true}), false);
assert.equal(model.childChecked({excludable: false, excluded: false, inheritedExclusion: true}), false);
assert.equal(model.childChecked({excludable: false, excluded: false, unsupported: 'Google Docs and shortcuts do not sync'}), false);

// rowWarns: a ticked row that cannot sync, or an item that would stop a sync; a Doc is only greyed out.
assert.equal(model.rowWarns({depth: 0, selected: true, error: 'Missing'}), true);
assert.equal(model.rowWarns({depth: 0, selected: false, error: 'Hidden'}), false);
assert.equal(model.rowWarns({depth: 1, excludable: true, excluded: false, unsupported: 'Symbolic links do not sync'}), true);
assert.equal(model.rowWarns({depth: 1, excludable: true, excluded: true, unsupported: 'Symbolic links do not sync'}), false);
assert.equal(model.rowWarns({depth: 1, excludable: false, excluded: false, unsupported: 'Google Docs and shortcuts do not sync'}), false);
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

console.log('Model flatten checks passed');
