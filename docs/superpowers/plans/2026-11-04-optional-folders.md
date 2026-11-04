# Optional Folders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the required `~/GoogleDrive` folder, so that sync covers only the home folders the user ticks, minus any files or subfolders they exclude.

**Architecture:** `home_folders` and `excluded` in settings become the only definition of what syncs, and one function, `selection.includes(path, settings)`, answers that question everywhere. Local and Drive scans prune excluded paths before any safety check. A one-time migration removes `local_dir` from existing installs without disturbing sync history.

**Tech Stack:** Python 3.11 standard library, `unittest`, SQLite, QML (Quickshell), plain JS in `Model.js`.

**Spec:** `docs/superpowers/specs/2026-11-04-optional-folders-design.md`

## Global Constraints

- Unticking or excluding never changes a file on either side and never sends a Drive write.
- Ticking starts no transfer.
- Clear the baseline before saving settings, so a crash leads to a merge, never a deletion.
- Selection changes hold `paths.lock()` and refuse to run during a sync.
- Top-level hidden home folders can never be ticked.
- Old `~/.GoogleDrive-omarchy-drive-backups` folders are never moved or deleted.
- `~/GoogleDrive` itself is never moved or deleted by the app.
- Never send a provider description, token or request URL to the UI (`http.py` already enforces this).
- No new dependencies.
- Do not run a real sync, touch `~/.config/omarchy/plugins/`, or call real Drive before Task 8, and only then with the user's approval.
- Commit on branch `optional-folders`. Do not push. End every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

Each line has a test in the task named.

1. **Names with spaces, apostrophes and non-ASCII characters** (`Attendance Sheet(Java) - term 2.xlsx`, `Bob's Files`, `Résumé`): ticking, excluding, `tree` and `reveal` all work, and an exclusion matches exactly. Tasks 1 and 6.
2. **Stale or hand-edited settings** (an `excluded` entry for a path that no longer exists, a non-string entry, a `home_folders` entry with no `identity` key): no traceback and never a deletion. A stale entry is harmless; a malformed one gives a clear `Blocked`. Tasks 1 and 3.
3. **`tree` on a path that vanished, is a file, passes through a symlink, or attempts traversal**: returns `{"ok": false, ...}`, follows nothing, and shows no traceback. Task 6.
4. **Using the panel offline**: ticking a home folder and excluding a path work without network; ticking a Drive-only folder with no cached listing gives an actionable message; a sync with nothing ticked sends no writes. Tasks 3 and 6.
5. **Status polling while the sync lock is held**: after migration, `status` needs no lock and works during a sync; before migration, it returns `ok: false` with the reason instead of crashing. Task 4.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `omarchy_drive/selection.py` | What syncs: `includes`, path validation, subtree test | Rewrite |
| `omarchy_drive/storage.py` | `Blocked.path`, `Paths.backups`, baseline and identity helpers | Modify |
| `omarchy_drive/drive.py` | Scan pruning, 412 handling | Modify |
| `omarchy_drive/home_folders.py` | Ticking, excluding, validating, pairing | Rewrite |
| `omarchy_drive/engine.py` | Scan ticked folders only, new backup location | Modify |
| `omarchy_drive/migrate.py` | One-time removal of `local_dir` | Create |
| `omarchy_drive/activity.py` | `blockedPath` in status | Modify |
| `omarchy_drive/cli.py` | Setup, doctor, open, tooltip, migration call | Modify |
| `omarchy_drive/panel.py` | `folders`, `tree`, `select`, `exclude`, `include`, `status` | Modify |
| `Service.qml`, `Panel.qml`, `Model.js` | One Folders list with expanding rows | Modify |
| `README.md`, `docs/STANDALONE_SETUP.md`, `CHANGELOG.md` | Remove the `~/GoogleDrive` default | Modify |

The suite cannot stay fully green between Tasks 3 and 6, because `panel.py` still uses the old settings until Task 6. Each task names the test modules that must pass. From the end of Task 6 onward, the whole suite must pass.

Run tests from the repo root. `tests/test_panel.py` uses top-level imports (`from test_api import ...`), so use discovery:

```bash
python3 -m unittest discover -s tests -p 'test_NAME.py'   # one module
python3 -m unittest discover -s tests                     # everything
```

---

### Task 1: Selection rules and storage helpers

**Files:**
- Modify: `omarchy_drive/selection.py`, `omarchy_drive/storage.py`
- Create: `tests/test_selection.py`

**Interfaces:**
- Produces, in `selection.py`:
  - `in_subtree(path: str, root: str) -> bool`
  - `includes(path: str, settings: dict) -> bool`
  - `valid_path(value: str) -> str`, which returns the value or raises `Blocked`
  - `folder_name(value: str) -> str` (unchanged)
- Produces, in `storage.py`:
  - `Blocked(message, path=None)` with a `.path` attribute
  - `Paths.backups -> Path`, equal to `state / 'backups'`
  - `Paths.configured()`, requiring only `folder_id`
  - `Store.forget_subtree(path: str) -> None`
  - `Store.stored_identity() -> str | None`
  - `Store.replace_identity(value: str) -> None`

- [ ] **Step 1: Write the failing tests**

```python
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

class StoreHelperTests(unittest.TestCase):
    def test_forget_subtree_keeps_look_alikes(self):
        # Remember 'Work/Exam', 'Work/Exam/a' and 'Work/Exam 2/b', forget 'Work/Exam',
        # then assert set(store.baseline()) == {'Work/Exam 2/b'}.
        ...

    def test_replace_identity_then_identity_accepts_the_new_value(self):
        # store.identity('old'); store.replace_identity('new'); store.identity('new') raises nothing;
        # store.identity('old') raises Blocked; stored_identity() == 'new'.
        ...

class ConfiguredTests(unittest.TestCase):
    def test_configured_needs_folder_id_only(self):
        # atomic_json(paths.settings, {'folder_id': 'x'}); paths.configured()['folder_id'] == 'x'.
        ...

class BlockedPathTests(unittest.TestCase):
    def test_path_attribute(self):
        self.assertEqual(Blocked('m', path='Work/a').path, 'Work/a')
        self.assertIsNone(Blocked('m').path)
```

Fill in the three commented bodies as described.

- [ ] **Step 2: Run them to confirm they fail**

Run: `python3 -m unittest discover -s tests -p 'test_selection.py'`
Expected: errors on `in_subtree`, `valid_path` and the new `Store` methods.

- [ ] **Step 3: Implement**

`includes` splits the first path component and checks membership in `settings.get('home_folders', {})`, then returns false if `in_subtree(path, entry)` holds for any `settings.get('excluded', [])`. A non-string entry raises `Blocked('Your folder settings contain an invalid exclusion. Restore settings.json.')`. `in_subtree` is `path == root or path.startswith(root + '/')`. `forget_subtree` deletes the row equal to `path` plus rows matching a range or a literal prefix; do not use `LIKE` on the raw path, because `_` and `%` are wildcards in it.

- [ ] **Step 4: Run them to confirm they pass**

Run: `python3 -m unittest discover -s tests -p 'test_selection.py'`
Expected: OK. `includes` keeps its old callers broken until Task 3, which is expected.

- [ ] **Step 5: Commit**

```bash
git add omarchy_drive/selection.py omarchy_drive/storage.py tests/test_selection.py
git commit -m "Add exact-path selection rules and storage helpers"
```

---

### Task 2: Drive scan pruning and readable 412s

**Files:**
- Modify: `omarchy_drive/drive.py`
- Test: `tests/test_api.py`, `tests/test_integration.py`

**Interfaces:**
- Consumes: `Blocked(message, path=None)`.
- Produces:
  - `Drive.scan(folder_id, included=None)`, where `included(path) -> bool` and `None` means include everything
  - `CHANGED_ON_DRIVE`, a module constant holding the 412 message

- [ ] **Step 1: Write the failing tests**

In `tests/test_api.py`:

```python
def test_scan_skips_unticked_and_excluded_without_listing_them(self):
    # root lists keep (folder) and skip (folder); keep lists a.txt and gone (folder).
    # included = lambda p: p.partition('/')[0] == 'keep' and p != 'keep/gone'
    # Assert set(result) == {'keep', 'keep/a.txt'} and exactly 3 HTTP calls (get root, list root, list keep).

def test_problems_inside_excluded_paths_do_not_block(self):
    # 'keep' lists two items named 'dup', one named '.omarchy-drive-tmp-x', and a binary with no md5Checksum named 'nosum'.
    # With all of them excluded by included(), scan returns only {'keep'}.

def test_duplicate_blocked_names_the_path(self):
    # Same listing with nothing excluded: assertRaises(Blocked) and exception.path == 'keep/dup'.

def test_stale_overwrite_and_trash_give_a_readable_block(self):
    # A PUT/PATCH raising APIError(412) surfaces as Blocked with message CHANGED_ON_DRIVE.
    # After an overwrite 412, the pending upload record has no 'session' key.
```

In `tests/test_integration.py`, change `test_drive_edit_racing_an_overwrite_is_not_clobbered` and `test_drive_edit_racing_a_trash_is_not_trashed` to `assertRaisesRegex(Blocked, 'changed on Drive')`, keeping their assertions on Drive content and the `trashed` flag.

- [ ] **Step 2: Run them to confirm they fail**

Run: `python3 -m unittest discover -s tests -p 'test_api.py'`
Expected: `scan` rejects `included`, and the 412 surfaces as `APIError`.

- [ ] **Step 3: Implement**

- In `scan`, build `path` with plain string joining (`f'{prefix}/{name}' if prefix else name`), then call `included(path)`, and only after that run `component`, the temporary-prefix, duplicate and checksum checks. Pass `path=path` on the duplicate and temporary-prefix `Blocked`s.
- `CHANGED_ON_DRIVE = 'This file changed on Drive during sync. Nothing was overwritten or trashed; the next sync will reconcile it.'`
- Catch 412 at three sites: resumable-session creation in `upload`, the session `except APIError` branch in `upload`, and `trash`. In `upload`, pop `session` from `pending` and save it before raising.

- [ ] **Step 4: Run them to confirm they pass**

Run: `python3 -m unittest discover -s tests -p 'test_api.py'`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add omarchy_drive/drive.py tests/test_api.py tests/test_integration.py
git commit -m "Prune excluded paths from Drive scans and explain 412 conflicts"
```

---

### Task 3: Engine syncs ticked folders only

**Files:**
- Rewrite: `omarchy_drive/home_folders.py`
- Modify: `omarchy_drive/engine.py`
- Test: `tests/test_engine.py`, `tests/test_home_folders.py`, `tests/test_integration.py`

**Interfaces:**
- Consumes: `selection.includes`, `in_subtree`, `valid_path`, `folder_name`; `Paths.backups`; `Store.forget_subtree`; `Drive.scan(folder_id, included)`.
- Produces, in `home_folders.py`:
  - `validate(name, entry, state) -> Path`, where `entry` is `None` when unticked, and `state` is the directory whose filesystem must match
  - `mappings(settings, paths) -> dict[str, Path]`
  - `choices(settings, paths) -> list[dict]` with `name`, `selected`, `error`, for visible home folders and ticked names
  - `select(paths, name, enabled) -> dict`
  - `set_excluded(paths, path, excluded: bool) -> dict`
  - `record_identity(settings, name, source) -> None`
- Produces, in `engine.py`:
  - `scan_local(root, prefix='', included=None) -> dict` with keys that include `prefix`
  - `Engine.archive_root`, equal to `paths.backups`

Rules that the implementer cannot infer:

- An `entry` whose identity is `None` or missing passes `validate` when the path does not exist. If the path exists, it must be an ordinary folder.
- `select(paths, name, True)` on a missing folder succeeds only if `panel-cache.json` lists `name` with `where` of `drive`; otherwise it raises `Blocked('Refresh the folder list, then tick this Drive folder again.')`.
- `set_excluded` rejects a path with fewer than two components, because excluding a whole folder is what unticking does.
- `select` and `set_excluded` follow the same order: validate, `store.forget_subtree`, `atomic_json`, then delete `panel-cache.json`. Neither makes a network call.
- `Store.identity` receives `settings['folder_id']`.
- `snapshot()`: for each mapping whose source exists, record a missing identity, add `{name: {'kind': 'dir'}}`, then merge `scan_local(source, name + '/', included)`. A source that does not exist adds nothing, so `plan` emits `mkdir-local`.
- The `mkdir-local` branch of `sync` calls `record_identity` for top-level paths and writes settings with `atomic_json(self.paths.settings, self.settings)`. The sync already holds the lock.
- `local_target` and `local_stop` raise `Blocked` when the first component is not ticked.
- Prune before the symlink, file-type and nested-mount checks in `scan_local`, and before hashing. Pass `path=` the full prefixed path on those `Blocked`s. An unreadable folder inside an excluded path must not reach `scan_error`, which pruning `folders` in place before descending ensures.

- [ ] **Step 1: Convert the engine test harness**

In `tests/test_engine.py`:

- `setUp` sets `self.home = self.base / 'home'`, `self.local = self.home / 'Sync'`, patches `HOME` to `self.home`, and sets `self.settings = {'folder_id': 'root', 'max_deletes': 20, 'home_folders': {'Sync': {'identity': [dev, ino]}}}` from `self.local.stat()`.
- `write()` keeps writing under `self.local`, so its callers are unchanged.
- Every `self.drive.files[...]` and `self.drive.put(...)` path, and every expected baseline key, gains a `Sync/` prefix.
- Expected change counts rise by one in the first sync where the `Sync` folder must be created on Drive (`mkdir-remote`); adjust those assertions by hand after checking that each extra action is that folder.
- `MemoryDrive.scan(self, root, included=None)` filters with `included`.

Run: `python3 -m unittest discover -s tests -p 'test_engine.py'`
Expected: fails until Step 4, with failures caused by the missing engine changes rather than harness errors.

- [ ] **Step 2: Write the failing behaviour tests**

In `tests/test_home_folders.py`, replace the `local_dir` setup with the same `HOME` approach, and delete `test_existing_explicit_selection_is_preserved` and `test_initial_all_folders_selection_uses_known_cloud_folders`, whose behaviour no longer exists. In `test_hidden_symlink_and_overlapping_folders_are_rejected`, `GoogleDrive` is now valid: remove it from the rejected names and expect it in `choices` when it exists. Add:

```python
def test_google_drive_is_an_ordinary_folder(self):
    # With ~/GoogleDrive existing and unticked, choices() lists it with selected False and no error;
    # select(paths, 'GoogleDrive', True) succeeds; sync pairs it with Drive path 'GoogleDrive'.

def test_sync_works_with_google_drive_deleted(self):
    # Tick Documents, sync, rmdir ~/GoogleDrive, sync again: no error and no trash calls.

def test_nothing_ticked_sends_no_writes(self):
    # Files exist in ~/Documents and in the drive under 'Documents/x'; settings have no home_folders.
    # sync() returns [] and drive.calls == [].

def test_untick_and_exclude_change_nothing_on_either_side(self):
    # Tick and sync, record drive.calls and file bytes. Exclude 'Documents/a.txt' then sync; untick then sync.
    # Assert no new calls, both copies unchanged, including after a local edit made while excluded.

def test_retick_and_reinclude_only_merge(self):
    # Table: identical file -> remembered; drive-only -> downloaded; local-only -> uploaded;
    # differing -> .conflict-local- copy exists. Assert no ('trash', ...) call in any case, for both retick and reinclude.

def test_exclusions_survive_untick(self):
    # set_excluded, select False, select True: the exclusion is still in settings.

def test_excluded_problem_items_do_not_block(self):
    # Inside an excluded 'Documents/bad': a symlink, a FIFO, and a chmod 000 folder (restore mode in cleanup).
    # Sync succeeds. With the exclusion removed, sync raises Blocked with .path == 'Documents/bad/link'.

def test_excluded_content_is_not_hashed(self):
    # Patch omarchy_drive.engine.digest and assert it is never called with the excluded file.

def test_drive_only_folder_is_created_and_identity_recorded(self):
    # Cache lists Archive with where='drive'; ~/Archive missing; select True stores identity None.
    # Ticking alone creates nothing. After sync: ~/Archive exists, files downloaded, identity is [dev, ino].

def test_drive_only_tick_without_cached_listing_is_actionable(self):
    # No panel-cache.json: assertRaisesRegex(Blocked, 'Refresh') and settings unchanged.

def test_excluding_a_whole_folder_is_rejected(self):
    # set_excluded(paths, 'Documents', True) raises Blocked.

def test_entry_without_identity_key_does_not_crash(self):
    # home_folders {'Documents': {}} with ~/Documents existing: sync succeeds and records the identity.

def test_home_folder_on_another_filesystem_is_blocked(self):
    # Patch os.stat-derived st_dev via a helper or Path.stat so the devices differ: select raises Blocked naming the disk rule.

def test_backups_go_to_state_directory(self):
    # After a cloud deletion, the backup is under paths.state/'backups'
    # and nothing named '*-omarchy-drive-backups' exists in home. Replaces the path in test_cloud_deletion_backs_up_original_home_file.
```

Add to `tests/test_integration.py` a case with a subfolder and a file excluded, confirming through the protocol server that neither is listed, read or written. Update its `engine()` and the home-folder test to the new settings shape.

- [ ] **Step 3: Run them to confirm they fail**

Run: `python3 -m unittest discover -s tests -p 'test_home_folders.py'`
Expected: failures and errors from the old `local_dir` behaviour.

- [ ] **Step 4: Implement `home_folders.py` and `engine.py`** following the rules above.

Remove `self.root` from `Engine`. `plan()` does not change in this task.

- [ ] **Step 5: Run them to confirm they pass**

```bash
python3 -m unittest discover -s tests -p 'test_engine.py'
python3 -m unittest discover -s tests -p 'test_home_folders.py'
python3 -m unittest discover -s tests -p 'test_integration.py'
python3 -m unittest discover -s tests -p 'test_api.py'
python3 -m unittest discover -s tests -p 'test_selection.py'
```

Expected: all OK. `test_panel`, `test_recent_files`, `test_activity` and `test_cli` may fail until Task 6.

- [ ] **Step 6: Commit**

```bash
git add omarchy_drive/home_folders.py omarchy_drive/engine.py tests/test_engine.py tests/test_home_folders.py tests/test_integration.py
git commit -m "Sync only ticked home folders and honour exclusions"
```

---

### Task 4: Migration, setup and CLI

**Files:**
- Create: `omarchy_drive/migrate.py`, `tests/test_migrate.py`
- Modify: `omarchy_drive/cli.py`, `omarchy_drive/panel.py` (the `main` call only)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Store.stored_identity`, `Store.replace_identity`, `atomic_json`, `read_json`.
- Produces: `migrate.migrate(paths) -> bool`, which returns whether it changed anything and raises `Blocked` when it must stop. `cli.main` and `panel.main` call it right after `paths.prepare()`, inside their existing `try` blocks, so a `Blocked` is reported through their normal error output.

Rules:

- With no `local_dir` in settings, return `False` immediately without taking the lock. This is what lets status work during a sync.
- Otherwise take `paths.lock()` and follow the five numbered steps in the spec's Migration section, in order.
- The stored identity is JSON `[path, dev, ino, folder_id]`. If it exists, is in that form, and its last item differs from `folder_id`, stop. A value that already equals `folder_id` passes, because a crash may have happened after step 4.
- The empty check ignores `RCLONE_TEST` and names starting with `.omarchy-drive-tmp-`. A missing `local_dir` counts as empty.
- The stop message for a non-empty folder names up to five items, for example: `Move these out of ~/GoogleDrive into your home folder, then tick them: Work, notes.txt`.
- `settings.json.pre-migration` is written only if it does not already exist, so a re-run cannot replace the original.

- [ ] **Step 1: Write the failing tests**

```python
class MigrateTests(unittest.TestCase):
    def test_empty_local_dir_keeps_baseline_and_gives_no_changes(self):
        # Old settings with Work ticked, old-format identity row, baseline rows.
        # migrate() is True; settings lack local_dir/selected_folders/root_files and keep home_folders;
        # baseline unchanged; stored identity == folder_id; ~/GoogleDrive still exists;
        # Engine(...).snapshot() against MemoryDrive holding identical files gives only 'remember' actions.

    def test_contents_in_local_dir_change_nothing_and_name_them(self):
        # settings bytes unchanged, identity unchanged, no .pre-migration file, Blocked message names the items.

    def test_only_marker_and_temp_files_count_as_empty(self): ...
    def test_missing_local_dir_counts_as_empty(self): ...

    def test_identity_for_another_drive_folder_stops(self):
        # Old identity row's folder differs: Blocked and nothing changed.

    def test_already_migrated_is_a_no_op_without_the_lock(self):
        # Hold paths.lock() in the test; migrate() returns False and raises nothing.

    def test_needs_migration_while_locked_reports_rather_than_crashes(self):
        # Hold paths.lock(); panel.main(['status']) exits 1 and prints JSON with ok False.

    def test_crash_after_identity_rewrite_recovers(self):
        # replace_identity already applied, settings still old: migrate() succeeds with the same result.

    def test_original_settings_are_preserved_across_reruns(self):
        # The .pre-migration content equals the first original, even after a re-run.
```

In `tests/test_cli.py`:

```python
def test_setup_parser_has_no_local_option(self):
    # parser().parse_args(['setup', '--local', '/x']) exits with an error.

def test_fresh_setup_ticks_nothing(self):
    # Run setup with authorize and Drive patched: settings have folder_id, folder_name, max_deletes,
    # and no local_dir; home_folders is {} or absent; no directory is created in HOME.

def test_doctor_works_without_local_dir(self):
    # With a ticked folder and a patched context: doctor returns 0.
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `python3 -m unittest discover -s tests -p 'test_migrate.py'`
Expected: import error on `omarchy_drive.migrate`.

- [ ] **Step 3: Implement `migrate.py`, and update `cli.py`**

In `cli.py`:

- Remove `--local` and the `local` handling in `setup`, and remove the folder-override check's dependence on it.
- `doctor` scans through `mappings(settings, paths)` with `includes`.
- `open` runs `xdg-open` on `Path.home()`.
- The tooltip line becomes `f'{count} folders ↔ Drive/{folder_name}'`.
- The `GUIDE` ending reads: `Setup transfers no files and selects no folders. Tick the home folders you want in the panel, then preview the first sync.`

- [ ] **Step 4: Run them to confirm they pass**

```bash
python3 -m unittest discover -s tests -p 'test_migrate.py'
python3 -m unittest discover -s tests -p 'test_cli.py'
```

Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add omarchy_drive/migrate.py omarchy_drive/cli.py omarchy_drive/panel.py tests/test_migrate.py tests/test_cli.py
git commit -m "Migrate existing installs off local_dir and simplify setup"
```

---

### Task 5: Path-specific errors

**Files:**
- Modify: `omarchy_drive/engine.py`, `omarchy_drive/activity.py`, `omarchy_drive/cli.py`
- Test: `tests/test_engine.py`, `tests/test_activity.py`

**Interfaces:**
- Consumes: `Blocked.path`.
- Produces:
  - `Activity.failure(kind, message, path=None)` writing `blockedPath` to status
  - `Activity.start()` clearing `blockedPath`
  - `cli.error_details(error)`, which still returns `(kind, message)`; callers pass `getattr(error, 'path', None)` to `failure`

- [ ] **Step 1: Write the failing tests**

```python
def test_each_stable_problem_reports_its_path(self):
    # Table, asserting Blocked.path:
    #   Sync/link (symlink)                     -> 'Sync/link'
    #   local file named like a Google Doc      -> that path
    #   file on one side, folder on the other   -> that path

def test_transient_problems_report_no_path(self):
    # A local file changing during sync, and a ticked folder replaced: Blocked.path is None.

def test_deletion_limit_names_the_most_affected_folder(self):
    # Two ticked folders with deletions, 25 in Work and 1 in Music: the message contains 'Work' and 'moved',
    # and 'Work' appears before '--allow-many-deletes'.

def test_failure_writes_and_start_clears_blocked_path(self):
    # In test_activity.py: failure('blocked', 'm', path='Work/a') writes blockedPath;
    # start() on a new Activity removes it; failure with no path leaves it None.
```

Update the existing `test_activity.py:22` settings to drop `local_dir`.

- [ ] **Step 2: Run them to confirm they fail**

Run: `python3 -m unittest discover -s tests -p 'test_activity.py'`
Expected: `failure()` rejects `path`.

- [ ] **Step 3: Implement**

- Pass `path=path` on the type-conflict and Google-Doc `Blocked`s in `plan`.
- The deletion message becomes: `{n} deletions need review, mostly in {folder}. If you moved that folder, restore it or untick it. To apply them anyway, review omarchy-drive preview, then run sync --allow-many-deletes.`
- `{folder}` is the first path component with the most deletions, using alphabetical order to break ties.
- Update `run_sync` and `main` in `cli.py` to pass the path through.

- [ ] **Step 4: Run them to confirm they pass**

```bash
python3 -m unittest discover -s tests -p 'test_engine.py'
python3 -m unittest discover -s tests -p 'test_activity.py'
```

Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add omarchy_drive/engine.py omarchy_drive/activity.py omarchy_drive/cli.py tests/test_engine.py tests/test_activity.py
git commit -m "Report the exact path and folder when sync stops"
```

---

### Task 6: Panel backend

**Files:**
- Modify: `omarchy_drive/panel.py`
- Test: `tests/test_panel.py`, `tests/test_recent_files.py`

**Interfaces:**
- Consumes: `home_folders.choices`, `mappings`, `select`, `set_excluded`; `selection.includes`, `in_subtree`, `valid_path`; `Drive.children`; `engine.local_path`.
- Produces:
  - `folders(paths, refresh=True) -> dict` with `ok`, `updated`, `driveAvailable`, `folders`, `tickedCount`, `localBytes`, `localBytesApprox`, and the quota keys. Each row has `name`, `where`, `selected`, `localBytes`, `approx`, `error`.
  - `tree(paths, path) -> dict` with `ok`, `path`, `driveAvailable`, `rows`. Each row has `name`, `path`, `kind` (`dir`, `file`, `unsupported`), `where`, `size`, `excluded`, `inheritedExclusion`, `unsupported` (a reason or `''`).
  - CLI commands: `select --name N --enable|--disable`, `exclude --path P`, `include --path P`, `tree --path P`. `home-select` and `--root-files`/`--no-root-files` are removed.
  - `status(paths)` adds `tickedCount` and `blockedPath`. It removes `folderPath`, `selectedCount`, `rootFiles` and `homeFolders`.

Rules:

- `folders` catches `Offline`, returns local rows with `driveAvailable: false`, and does not overwrite `panel-cache.json`.
- A home file or symlink with the same name as a Drive folder, and a hidden Drive folder, get `selected: false` and a non-empty `error`.
- `tree` validates with `valid_path`, and the first component must be a visible home folder or a Drive folder. It rejects a path where `local_path` finds a symlink.
- `tree` uses `os.scandir` with `follow_symlinks=False`, hashes nothing, and sorts rows by case-folded name. Local symlinks, sockets and FIFOs get a reason in `unsupported`. Google Docs and shortcuts get `Google Docs and shortcuts do not sync`.
- `tree` walks to the Drive folder by listing children at each level and matching the name in Python, not in a query string. A name that matches no Drive folder gives no Drive rows and is not an error.
- With nothing ticked, `status` reports the label `No folders selected`.
- `open-backups` uses `paths.backups`. `reveal` resolves only through `mappings` and raises `Blocked` for anything else.

- [ ] **Step 1: Write the failing tests**

Replace `SelectionTests`, and the tests in `PanelTests` that use `root_files` or `selected_folders`, then add:

```python
def test_folders_merges_home_and_drive(self):
    # Home: Work, Downloads. Drive: Work, Archive. Work ticked.
    # want rows: Archive where=drive selected=False; Downloads where=home selected=False; Work where=both selected=True.
    # tickedCount == 1.

def test_google_drive_appears_as_ordinary_row(self):
    # ~/GoogleDrive exists: row where=home, selected False, error ''.

def test_name_clashes_are_greyed_with_a_reason(self):
    # ~/Archive is a file while Drive has Archive; Drive has .hidden: both have a non-empty error and selected False.

def test_folders_offline_returns_local_rows(self):
    # HTTP raising Offline: ok True, driveAvailable False, local rows present, cache file unchanged.

def test_tree_merges_both_sides_one_level(self):
    # ~/Work has a.txt, both/; Drive Work has both/, b.txt, a Google Doc.
    # Rows sorted, with where set correctly; the Doc has unsupported set; nothing is listed from inside both/.

def test_tree_handles_unusual_names(self):
    # Drive and disk both hold "Bob's Files" and 'Résumé.pdf': where=both, path preserved exactly.

def test_tree_marks_exclusions(self):
    # excluded ['Work/x']: tree('Work') row x has excluded True; tree('Work/x') rows have inheritedExclusion True.

def test_tree_rejects_unsafe_paths(self):
    # For 'Work/../..', '/etc', '.ssh', 'Work/link' (symlink), 'Work/a.txt' (a file), 'Work/gone':
    # main(['tree', '--path', p]) exits 1 and stdout parses to ok False, with no traceback on stderr.

def test_tree_offline_returns_local_rows(self):
    # Offline: ok True, driveAvailable False.

def test_select_exclude_and_include_need_no_network(self):
    # Patch panel.HTTP to raise on construction: select, exclude and include all succeed.

def test_selection_commands_refuse_during_sync(self):
    # Hold the lock; each command raises Blocked and settings bytes are unchanged.

def test_status_reports_ticked_count_and_blocked_path(self):
    # status file blockedPath 'Work/a': status()['blockedPath'] == 'Work/a'; tickedCount matches;
    # nothing ticked -> statusText 'No folders selected'; the token value never appears in the JSON.

def test_open_backups_uses_state_directory(self):
    # Patch subprocess.run: xdg-open receives str(paths.backups), which exists with mode 0o700.
```

In `tests/test_recent_files.py`, move the fixture to a ticked `~/Documents` and change `test_missing_file_falls_back_to_nearest_existing_parent` to use a missing file inside it. Add a case where an unticked first component raises `Blocked`.

- [ ] **Step 2: Run them to confirm they fail**

Run: `python3 -m unittest discover -s tests -p 'test_panel.py'`
Expected: failures and errors from the old contract.

- [ ] **Step 3: Implement `panel.py`** per the rules above.

- [ ] **Step 4: Run the whole suite**

Run: `python3 -m unittest discover -s tests`
Expected: OK. The entire suite must pass from this point onward.

- [ ] **Step 5: Commit**

```bash
git add omarchy_drive/panel.py tests/test_panel.py tests/test_recent_files.py
git commit -m "Add one folder list, tree view and exclusions to the panel backend"
```

---

### Task 7: Panel interface and docs

**Files:**
- Modify: `Service.qml`, `Panel.qml`, `Model.js`, `README.md`, `docs/STANDALONE_SETUP.md`, `CHANGELOG.md`
- Create: `tests/model_flatten.test.js`

**Interfaces:**
- Consumes: the Task 6 JSON contract.
- Produces, in `Model.js`: `flattenRows(folders, childrenByPath, expanded) -> array` of rows, adding `depth` and `path`, where a top-level row's `path` is its `name`.
- Produces, in `Service.qml`:
  - `treeChildren`, `expandedPaths`, `treeLoading`, `driveAvailable`, `blockedPath`
  - `toggleExpanded(path)`, `toggleExcluded(row)`, `excludeBlocked()`, `openFolder(name)`

Rules:

- `toggleFolder` sends `select --name N --enable|--disable`.
- Expanding starts a `tree` process; only one runs at a time, and further requests wait in a queue. Close and clear `treeChildren` after any selection change or when `refreshFolders` completes, so rows are never stale.
- Each `Panel.qml` row shows a checkbox, an arrow for folders, the name indented by depth, a label and a size.
- Top-level labels are `Home + Drive`, `Home only`, and `Drive only`, with `will create ~/Name` added when a Drive-only row is ticked.
- Child labels are `excluded`, or the `unsupported` reason. Rows with `inheritedExclusion` or an `error` or `unsupported` value (other than local symlinks, sockets and FIFOs, which can be excluded) have a disabled checkbox.
- Keyboard: Up and Down move through the flattened rows; Right expands and Left collapses; Space toggles the tick; `o` opens the cursor's top-level folder when it exists locally.
- Remove the `root` focus section so that Down from the header goes to the first row.
- Remove `RootFilesRow`, the "Choose home folders" picker and `homePickerOpen`, the "remains on disk" note, the Open button at `Panel.qml:322`, the "Folder" `InfoPair` and `displayFolder`, `setRootFiles`, the `rootFile*`, `rootLocal*` and `stale*` properties, and `Model.rootFilesMeta`.
- `driveActive` becomes `drive.authenticated && drive.tickedCount > 0`.
- The Backups button moves to the panel's action row and is always visible while authenticated.
- `folderSummary` drops `root files` and reads `tickedCount`.
- When `drive.blockedPath` is non-empty and sync is not running, show the error with an **Exclude this** button that calls `excludeBlocked()`. Offline, show `Drive could not be read. Showing local folders only.`

- [ ] **Step 1: Write the failing test for `flattenRows`**

In `tests/model_flatten.test.js`, using `node:assert` and `require('../Model.js')`, since `Model.js` already sets `module.exports` under Node (add `flattenRows` to those exports):

```js
const folders = [{name: 'Docs'}, {name: 'Work'}];
const children = {Work: [{name: 'Sub', path: 'Work/Sub', kind: 'dir'}, {name: 'a', path: 'Work/a', kind: 'file'}],
                  'Work/Sub': [{name: 'b', path: 'Work/Sub/b', kind: 'file'}]};
// Nothing expanded -> paths ['Docs', 'Work'] with depth 0.
// expanded {Work: true} -> ['Docs', 'Work', 'Work/Sub', 'Work/a'], depths [0, 0, 1, 1].
// expanded {Work: true, 'Work/Sub': true} -> adds 'Work/Sub/b' at depth 2, directly after 'Work/Sub'.
// expanded {'Work/Sub': true} with Work collapsed -> ['Docs', 'Work'].
// expanded {Docs: true} with no loaded children -> ['Docs', 'Work'].
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node tests/model_flatten.test.js`
Expected: `flattenRows` is not defined.

- [ ] **Step 3: Implement `flattenRows`, then the QML changes**

- [ ] **Step 4: Run the checks**

```bash
node tests/model_flatten.test.js
python3 -m unittest discover -s tests
grep -n 'rootFiles\|setRootFiles\|staleBytes\|staleCount\|folderPath\|homeFolders\|selectHomeFolder\|selectedCount\|displayFolder\|homePickerOpen' Panel.qml Service.qml Model.js BarWidget.qml DriveTooltip.qml
```

Expected: both test runs pass and `grep` prints nothing. `qmllint` is not installed here, so Task 8's panel check is the only proof that the QML loads.

- [ ] **Step 5: Update the docs**

- `README.md` and `docs/STANDALONE_SETUP.md`: describe ticking home folders and excluding items, and remove `~/GoogleDrive` as a default, the `--local` option, the root-files setting and the old backup location. State that old backups stay where they were. Add the known limits from the spec.
- `CHANGELOG.md`: add an entry at the top, including how to undo the migration with `settings.json.pre-migration`.

Run: `grep -n 'GoogleDrive\|--local\|root files' README.md docs/STANDALONE_SETUP.md omarchy_drive/cli.py`
Expected: only mentions that explain the migration or the old backup location.

- [ ] **Step 6: Commit**

```bash
git add Service.qml Panel.qml Model.js README.md docs/STANDALONE_SETUP.md CHANGELOG.md tests/model_flatten.test.js
git commit -m "Show one expandable Folders list in the panel"
```

---

### Task 8: Real-environment checks and install

Each numbered step needs the user's approval when you reach it. **Stop and ask first.** Keep scripts in the session scratchpad; none are committed. Never run a sync with `--allow-many-deletes`.

- [ ] **Step 1: Real Drive writes (ask first).** Using the user's sign-in and temporary `HOME` and `XDG_STATE_HOME` directories, create a new Drive folder named `omarchy-drive-test-<random>`, containing one test file. Using `Drive` directly, confirm:
  - Overwriting with a current ETag succeeds, and the content checksum changes.
  - An overwrite with a stale `If-Match` gets 412, and the content is unchanged.
  - A trash with a stale `If-Match` gets 412, and the file is not trashed.
  - A trash with a current ETag succeeds.

  Afterwards, move the test folder to Trash and report all four results. If either stale write is not rejected, stop and report it, because the spec's protection against edits arriving mid-sync would not hold.

- [ ] **Step 2: Trial migration (ask first).** Copy `~/.config/omarchy-drive/settings.json`, `tokens.json`, `client.json` and the state database into temporary `XDG_CONFIG_HOME` and `XDG_STATE_HOME` directories. With `HOME` unchanged, run `migrate`, then `omarchy-drive preview` from the repo.

  Expected: `0 proposed changes`, unless the user has changed files since the last sync. Explain any difference before continuing. Confirm with `sha256sum` that the real `settings.json` did not change.

- [ ] **Step 3: Show what installing would change (ask first).** Compare the repo with `~/.config/omarchy/plugins/io.github.xjaike.google-drive`, listing every file that would change and the plugin checkout's own uncommitted changes. The earlier comparison showed it matches in code apart from the docs and tests; confirm that again. Then install only with approval. Afterwards, run `omarchy-drive preview` and expect 0 changes.

- [ ] **Step 4: Panel check (ask first).** With the user, reload the shell and go through:
  - the list loads, with `GoogleDrive` unticked and `Documents`, `Music`, `Pictures`, `Work` ticked
  - expanding rows, and using the keyboard (Up, Down, Left, Right, Space, `o`)
  - excluding and re-including a test file, then running preview after each
  - the Backups button

- [ ] **Step 5: Success criteria.** Confirm each of the spec's five success criteria, and report any not demonstrated. Deleting `~/GoogleDrive` is the user's own step, then one sync to confirm criterion 1.
