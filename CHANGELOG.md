# Standalone backend fork

## Unreleased: optional folders

`~/GoogleDrive` is no longer special. You choose which home folders sync, and can
exclude any file or subfolder inside them. A fresh setup syncs nothing until
something is ticked.

- One **Folders** list replaces the "Choose home folders" picker and "Keep on
  disk". Rows expand in place, and a ticked `~/X` pairs with `Drive/<parent>/X`.
- Exclude exact paths inside a folder, including one that is not ticked yet.
  Unticking or excluding changes no file on either side and sends no Drive write.
  Ticking or including again merges, and ticking starts no transfer.
- Items that cannot sync are skipped automatically: symlinks, sockets, FIFOs and
  devices, nested mounts, unreadable files and folders, a Google Doc or shortcut
  sharing a name with a local file, and Drive names that cannot be local paths. A
  skip changes neither side and is not saved as an exclusion. Once the item can sync
  it merges. A file replaced by a symlink no longer risks its Drive copy going to
  Trash.
- The items a sync skipped are listed in the panel, in
  `~/.local/state/omarchy-drive-v2/cannot-sync.txt`, by `omarchy-drive skipped` and in
  `omarchy-drive preview`.
- A problem a skip cannot settle offers **Exclude this**, or **Untick this** for a
  ticked folder. The panel never does either by itself.
- Removed: `setup --local`, the `~/GoogleDrive` default, the loose-files setting,
  the panel's Open button, the "remains on disk" note, and the "Folder" row. Loose
  files directly in the Drive parent folder stay on Drive.
- The Backups button is always shown while connected. New backups go to
  `~/.local/state/omarchy-drive-v2/backups/`. Old
  `~/.GoogleDrive-omarchy-drive-backups` folders stay where they were; the app
  never moves or deletes them.
- Settings migrate once. If `~/GoogleDrive` still holds files, the app stops, changes
  nothing, and names what to move into your home folder. `~/GoogleDrive` itself is
  left in place.

To undo the migration, follow these steps in order, because this version migrates
again as soon as it reads the restored settings, and its bar panel reads them every
few seconds. Folders you ticked and exclusions you added since the migration are
forgotten.

1. Make sure no sync is running. Keep it that way until the last step, and do not
   press **Sync now**:

   ```bash
   systemctl --user disable --now omarchy-drive-sync.timer
   systemctl --user stop omarchy-drive-sync.service
   ```

2. Check that `~/GoogleDrive` is still the original folder. It must be: the previous
   version checks the folder's identity, so a recreated `~/GoogleDrive` fails that
   check even if it holds the same files. If you deleted it, the undo cannot be
   completed this way, so stop here, stay on this version, and run
   `omarchy-drive resume`.
3. Put the previous version of the app back.
4. Move every folder you took out of `~/GoogleDrive` for the migration back into it.
   Otherwise the previous version sees those folders as deleted locally and plans to
   move their Drive copies to Trash.
5. Copy the saved settings back:

   ```bash
   cp ~/.config/omarchy-drive/settings.json.pre-migration ~/.config/omarchy-drive/settings.json
   ```

6. Set the `identity` value in the `metadata` table of
   `~/.local/state/omarchy-drive-v2/sync.sqlite3` to the exact contents of
   `identity.pre-migration`. Skip this step if that file does not exist, because then
   the migration did not change the value.

   ```bash
   python3 - <<'EOF'
   from pathlib import Path
   import sqlite3
   home = Path.home()
   identity = (home / '.config/omarchy-drive/identity.pre-migration').read_text()
   db = sqlite3.connect(home / '.local/state/omarchy-drive-v2/sync.sqlite3')
   with db:
       db.execute("UPDATE metadata SET value = ? WHERE key = 'identity'", (identity,))
   db.close()
   EOF
   ```

7. Preview the result, and look for `trash-remote` lines:

   ```bash
   omarchy-drive preview
   ```

   Do not continue if any `trash-remote` line appears that you did not intend. Put
   the missing folder back and preview again.
8. Delete both saved files:

   ```bash
   rm -f ~/.config/omarchy-drive/settings.json.pre-migration ~/.config/omarchy-drive/identity.pre-migration
   ```

9. Only after that, turn automatic sync back on:

   ```bash
   omarchy-drive resume
   ```

If you use `XDG_CONFIG_HOME` or `XDG_STATE_HOME`, change these paths to match.

## 2.1.0 — 2026-11-04

- Fork the Homin Luo interface under `io.github.xjaike.google-drive`, preserving
  upstream MIT credits and the icon, panel, folder picker and keyboard controls.
- Replace the external sync engine with our direct Google Drive API backend,
  OAuth flow, SQLite history, resumable transfers and conflict-safe merging.
- Reuse an existing standalone connection and configured folder pair.
- Add safe selection inside that pair; deselected copies are retained and
  re-selection starts a merging baseline.
- Show icon-only state badges and live per-file hover/panel activity.
- Replace the browse mount with a browser link and destructive cleanup with an
  explicit local-backups action.
- Include account setup from the panel, tested user-service helpers, and CI.

The history below describes the original upstream implementation.

# Changelog

## 1.0.3 — 2026-11-01

Correctness fixes from a full review of the backend, the QML front end and the
systemd units.

- A folder inside `$HOME` no longer becomes unusable when `$HOME` is reached
  through a symlink (`/home` → `/var/home` on ostree installs); both the
  written and the resolved home are accepted.
- A selected folder that Drive no longer lists stays ticked and is marked
  "Not in Drive", so it can be unticked again. It is no longer counted as
  reclaimable space or deleted by cleanup while it is still selected.
- The systemd units follow the configured folder, remote, mount and interval.
  Changing one in the bar settings used to leave the timer syncing the old
  path; the interval drop-in is now rewritten only when it differs.
- Cleanup takes the same lock as a sync run, so bisync can no longer write
  into a tree that is being emptied. A removal that fails after some entries
  are already gone reports `partial: true`, and the whole run is bounded by a
  one-hour deadline.
- Loose files at the top of Drive are counted, shown, and cleaned up when
  "Loose files at the top of Drive" is off — they used to be invisible to
  both the size totals and cleanup.
- A failed `status` no longer flips the panel to "rclone required": the last
  good connection state is kept, and the error is reported instead.
- Turning on the browse mount restarts an enabled-but-dead unit and fails
  loudly when the mount never comes up; the panel re-reads the status six
  seconds later so a mount that disappears is not shown as "Browsing".
- The QML watchdogs now sit above the backend's own worst-case budgets
  (status 150s, folders 240s, control 70 min); a healthy but slow run is no
  longer killed and reported as a timeout.

## 1.0.2 — 2026-09-18

Security hardening after the second marketplace review.

- rclone is run through one bounded runner: its own session (process
  group), a deadline, and a byte cap on each pipe. A child that overruns any
  of them is killed with its group and reported with no output at all; a
  watchdog SIGTERM from the widget kills the running rclone too, and every
  child carries `PR_SET_PDEATHSIG` as a backstop.
- The Drive folder listing (`rclone lsjson`) is streamed and parsed one
  entry at a time under an 8 MiB transfer cap, a 5000-entry cap and a
  255-byte name cap. Past any cap there is no listing, never a partial one.
  The loose-file count uses `rclone size` with the sync's own filter rules,
  which answers with one small object however many files there are.
- Folder names are validated once (`valid_drive_name`) everywhere they
  enter: the listing, `select --add`, and `selection.json` itself. A name
  with a newline could previously add a rule to the rclone filters file.
  The widget passes `--add=NAME`, so a folder called `-Something` is a
  folder, not an option.
- `rclone check` in cleanup runs under a stderr cap; cleanup refusals now
  reach the panel.
- `sync.log` and `mount.log` roll over at 2 MiB; tails are read by seeking,
  never by reading the whole file. Whether a run needs `--resync` is
  decided by bisync's exit code plus rclone's own timestamped ERROR line,
  not by a substring a remote file name could contain.
- The panel renders every string as plain text (a folder called
  `<img src=…>` no longer fetches anything); the on-disk size walk has an
  entry and time budget and is shown as `≈` when cut short; `rclone about`
  is asked at most once a minute.
- Names may start or end with whitespace (Drive allows it and the filter
  rule carries it); only control characters, `/`, `.` and `..` are refused.
  Each top-level folder gets its own walk budget under one clock, and the
  panel keys the "still on disk" notice on a folder count as well as bytes,
  so one huge folder cannot hide another. A SIGTERM to the helper forwards
  SIGTERM to rclone and waits before killing, so a `systemctl stop` lets
  bisync drop its lock. Resync detection recognises rclone's critical-error
  line with and without `--resilient`.
- Unit files escape `%` and `$`; paths with control characters are
  refused. The mount point and the sync root must be real directories of
  yours (a symlinked sync root is refused); the browse mount caps its VFS
  cache at 2 GB and logs at NOTICE level. The state directory and every
  file in it are tightened to 0700/0600 on each run.

## 1.0.1 — 2026-09-17

State writes and cleanup made race-resistant: unpredictable
`O_CREAT|O_EXCL|O_NOFOLLOW` temps, descriptor-relative renames, and cleanup
that verifies and removes one directory identity.

## 1.0.0 — 2026-09-09

First release.
