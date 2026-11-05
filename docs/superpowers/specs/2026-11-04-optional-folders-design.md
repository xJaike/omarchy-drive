# Optional folders: design

Date: 2026-11-04
Status: awaiting review

## Goal

`~/GoogleDrive` stops being special. The user decides which home folders sync,
and can exclude any file or subfolder inside them. A fresh install syncs nothing
until something is ticked.

### Decisions made with the user

- Every visible home folder can be ticked, including `~/GoogleDrive`.
- A ticked folder includes files added to it later. "Nothing automatic" means
  that nothing outside the selection ever syncs, not that each new file needs
  selecting.
- Each ticked `~/X` pairs with `Drive/<parent>/X` (approach A). Free pairing of
  any local folder with any Drive folder was considered and deferred.
- Exclusions are exact paths. There are no patterns or built-in exclusions.
- One problem folder stops the whole sync, as it does today.

### Success criteria

1. With `~/GoogleDrive` deleted, sync runs normally.
2. Unticking or excluding anything never changes a file on either side.
3. After migration, this machine's `omarchy-drive preview` shows 0 changes.
4. A fresh setup followed by a sync transfers nothing.
5. An unsyncable item inside an excluded path cannot stop a sync.

### Out of scope

- Different local and Drive names, or more than one Drive parent folder.
- Changing the Drive parent folder from the panel after setup.
- Exclusion patterns, and a preview button in the panel.
- Skipping only the affected folder when one folder has a problem.
- Syncing hidden top-level home folders such as `.config`, which can hold
  credentials.
- Syncing loose files directly in the Drive parent folder. They stay on Drive.

## Settings

| Key | Change |
|---|---|
| `local_dir` | Removed |
| `selected_folders` | Removed |
| `root_files` | Removed |
| `home_folders` | The only record of what syncs: `{name: {identity: [dev, ino] or null}}` |
| `excluded` | New: sorted, unique list of relative paths such as `Work/Teaching Files/Exam` |
| `folder_id`, `folder_name`, `max_deletes`, `sync_interval_min` | Unchanged |

`Paths.configured()` requires only `folder_id`. A `null` identity means a
Drive-only folder that has not been created locally yet.

`selection.includes(path, settings)` returns true only when the first path
component is in `home_folders` and neither the path nor any ancestor is in
`excluded`. Sync-history (baseline) rows that fail this test are ignored, so they
can never produce a deletion.

## Sync engine

### Local and Drive scanning

- `snapshot()` scans each ticked home folder. It no longer scans a shared root.
- `scan_local` takes the top-level name as a path prefix, plus the exclusion
  test. It prunes excluded paths during the walk, before the symlink, file-type
  and nested-mount checks, and before hashing.
- `Drive.scan` takes the same test in place of `selected` and `root_files`. It
  skips unticked top-level names and excluded paths before the name-safety,
  temporary-prefix, duplicate and checksum checks.
- A name containing `/` cannot form an exclusion path, so inside an included
  folder it still blocks until renamed.

### Folder pairing

- `home_folders.validate(name, entry, backups)` no longer takes a sync root.
  It keeps the checks for a visible name, an ordinary folder, no symlink or mount,
  and a matching identity.
- It requires the home folder to share a filesystem with the backup directory,
  because `archive_source` moves files with `os.rename`.
- A `null` identity passes when the path does not exist. If the path exists as an
  ordinary folder when the sync starts, its identity is recorded and the two sides
  merge.
- Syncing a Drive-only folder uses the existing `mkdir-local` action, then records
  the new folder's identity in settings.
- `Engine.local_target` and `local_stop` lose their fallback to `self.root`.

### Identity check

`Store.identity` stores the Drive folder ID only. Each home folder's
`[dev, ino]` remains its own moved-or-replaced check.

### Backups

`archive_root` becomes `paths.state / 'backups'`, created with mode 0700. Backup
paths inside it keep the current format. Old
`~/.GoogleDrive-omarchy-drive-backups` folders are never moved or deleted.

## Selection changes

All of these hold the sync lock, and the panel disables them during a sync.

| Action | Effect |
|---|---|
| Tick a home folder | Record its identity, clear its baseline, keep its exclusions |
| Tick a Drive-only folder | Record a `null` identity, clear its baseline |
| Untick a folder | Remove it from `home_folders`, clear its baseline, keep its exclusions |
| Exclude a path | Add to `excluded`, clear the baseline for that subtree |
| Include a path | Remove that exact entry, clear the baseline for that subtree |

- The baseline is cleared before settings are saved. A crash between the two
  leads to a merge on the next sync, never a deletion.
- A cleared baseline is what makes re-ticking a merge: identical files are
  remembered, one-sided files are copied, and differing files keep both versions.
- Excluding a folder keeps entries already nested under it. Rows beneath an
  excluded folder show as excluded and cannot be ticked individually.
- Paths are validated as relative POSIX paths with no `.`, `..` or empty parts,
  and the first component must be a single folder name.
- An unticked folder can be expanded and given exclusions before it is ticked.
- Ticking starts no transfer.

## Setup and migration

### Fresh setup

`setup` asks for the Google account and Drive folder only. The `--local` argument
and the `~/GoogleDrive` default are removed. `home_folders` starts empty.

### Migration

Runs once, under the sync lock, before the first sync or panel read that finds
`local_dir` in settings.

1. If the stored identity's Drive folder differs from `folder_id`, stop and
   change nothing.
2. If `local_dir` contains anything other than `RCLONE_TEST` or app temporary
   files, stop, change nothing, and name the items to move into the home folder.
   This check repeats on each run until the folder is clear.
3. Save `settings.json` to `settings.json.pre-migration`.
4. Rewrite the identity row to the new format.
5. Atomically write settings without `local_dir`, `selected_folders` and
   `root_files`.

A crash between steps 4 and 5 re-runs the migration safely, because every step
gives the same result when repeated. Baseline rows keep their `Work/...` keys, so
ticked folders need no re-merge. `~/GoogleDrive` itself is left in place.

## Panel

### Backend (`omarchy_drive/panel.py`)

- `folders`: one row per name across visible home folders and Drive folders:
  `name`, `where` (`home`, `drive` or `both`), `selected`, `localBytes`, `approx`,
  and `error` when it cannot be ticked. Sizes use the existing time-limited
  `disk_inventory`.
- New `tree --path P`: lists one level from disk and Drive, resolving Drive folder
  IDs along the path. Rows carry `name`, `kind`, `where`, `size`, `excluded`,
  `inheritedExclusion`, and `unsupported` with a reason. It hashes nothing and
  follows no symlinks. If Drive is unreachable it returns local rows and
  `driveAvailable: false`.
- New `exclude --path P` and `include --path P`.
- `select` and `home-select` merge into one tick command.
- Removed: `--root-files`, `--no-root-files`, `rootFile*`, `rootLocal*` and
  `stale*` fields, and `folderPath`.
- `status` adds `tickedCount` and `blockedPath`. With nothing ticked it reports
  `No folders selected`.
- `reveal` resolves through `home_folders` only.

### Interface (`Panel.qml`, `Service.qml`, `Model.js`)

- One **Folders** list replaces the "Choose home folders" picker and the
  "Keep on disk" list.
- Rows expand in place and load children on demand.
- Google Docs and shortcuts are greyed out. Symlinks, sockets and similar items
  show a warning and can be excluded.
- Rows that cannot be ticked, such as a hidden Drive folder or a home file with
  the same name as a Drive folder, are greyed out with the reason.
- Removed: `RootFilesRow`, the "remains on disk" note, and the open-folder button.
- The header shows how many folders are ticked.
- `o` opens the folder under the cursor.
- The Backups button is always available.
- Offline, the list shows local rows and a note that Drive could not be read.

## Errors

`Blocked` gains an optional `path`, written to status as `blockedPath`. The panel
offers **Exclude this** only for problems tied to a stable path. It never excludes
anything by itself.

| Situation | Behaviour | Exclude this |
|---|---|---|
| Ticked folder missing or replaced | Stops before changes, names the folder, offers untick | No |
| Ticked folder with sync history gone from Drive | Stops before changes, names the folder, offers untick | No, offers Untick this |
| Symlink, socket or nested mount | Stops, names the path | Yes |
| Local path clashes with a Google Doc | Stops, names the path | Yes |
| File and folder type conflict | Stops, names the path | Yes |
| Duplicate Drive names | Stops, names the path; excluding skips all duplicates | Yes |
| App temporary prefix on a Drive file | Stops, names the path | Yes |
| Drive name containing `/` | Stops, asks for a rename | No |
| File changed during sync | Existing "run sync again" message | No |
| 412 on overwrite or trash | `Blocked`: the file changed on Drive, nothing was overwritten, the next sync reconciles it | No |
| Deletion limit reached | Stops, names the most affected folder, asks whether it was moved, and mentions `--allow-many-deletes` only after that | No |

## Testing

### Automated

The fake Drive server keeps observed behaviour: v3 sends no ETag and ignores
`If-Match`; v2 exposes `etag` and enforces it.

- **Selection:** `includes` with ancestors, and prefix look-alikes (`Work` against
  `Work2`, `Exam` against `Exam 2`).
- **No side effects:** unticking and excluding change no file on either side and
  send no Drive write.
- **Merge:** re-ticking covers identical, one-sided and differing files, with no
  trash action in any case.
- **Pruning:** a socket, a symlink, an unreadable folder or duplicate Drive names
  inside an excluded path cannot stop a sync.
- **Pairing:** a missing or replaced folder stops before any write. A Drive-only
  folder is created on first sync and its identity recorded.
- **Migration:** an empty `local_dir` gives 0 actions with the baseline kept. A
  `local_dir` with files changes nothing. A mismatched identity stops. Re-running
  after a simulated crash gives the same result.
- **Setup:** a fresh setup followed by a sync sends no writes.
- **Errors:** each row of the table above, including `blockedPath`.
- **Panel:** `tree` rows, offline behaviour and path validation.
- **Existing tests:** about 25 references to the old settings are updated across
  `test_home_folders`, `test_integration`, `test_activity`, `test_panel`,
  `test_engine` and `test_recent_files`. `test_engine` also needs a new harness:
  its 18 tests sync from a shared root, so their paths gain a home-folder prefix.

### Real-environment checks

Each runs only after the user approves it at that point.

1. **Real Drive writes:** with a temporary Drive folder and temporary home, confirm
   a v2 overwrite and trash succeed and that stale writes get a 412. This also closes
   the gap left by the v2 write fix, which has not yet run against real Drive.
2. **Trial migration:** run on a copy of this machine's settings and sync database.
   It must show 0 changes before the real one runs.
3. **Panel:** open the list in the running shell and check expanding, ticking,
   excluding and the keyboard cursor.

## Rollout

The running plugin at `~/.config/omarchy/plugins/io.github.xjaike.google-drive` is
a separate git checkout with its own uncommitted changes. Installing is a separate
step: show what would change, then ask. The previous settings stay available as
`settings.json.pre-migration`, so the change can be undone.

`README.md`, `docs/STANDALONE_SETUP.md` and the `setup` guide text drop their
references to the default `~/GoogleDrive` pair.

## Known limits

- Local and Drive names must match, under one Drive parent folder.
- A renamed excluded path is no longer excluded.
- A folder cannot be excluded while keeping one file inside it. The alternative is
  to leave it ticked and untick everything else, though later additions will sync.
- A small subfolder moved out of a ticked folder still counts as deleted: Drive
  copies go to Trash with a local backup, subject to the deletion limit.
- One problem folder pauses sync for all folders until it is resolved.
