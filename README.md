# omarchy-drive

Google Drive sync status for the Omarchy Quattro bar, with full local offline
copies, transfer progress, and a live file activity dashboard.

Plugin ID: `io.github.xjaike.omarchy-drive` · License: MIT

## Requirements

- Omarchy Quattro shell (developed on Omarchy 4.0.6).
- Python 3, rclone, systemd user services, and util-linux (`flock`, `mountpoint`).
- An authenticated Google Drive rclone remote named `omarchy-drive`.
- A folder named **Omarchy** at the root of My Drive.

This initial release uses a fixed sync pair: `~/GoogleDrive` ↔
`omarchy-drive:Omarchy`. It is a sync client, not a mounted cloud folder.

## Install

```bash
omarchy pkg add rclone
omarchy plugin add https://github.com/xJaike/omarchy-drive.git --enable
bash ~/.config/omarchy/plugins/io.github.xjaike.omarchy-drive/install.sh
```

Adding the plugin only displays status. The explicit installer creates two
helper symlinks in `~/.local/bin` and two user systemd units. Existing files
with these names are backed up under `~/.local/state/omarchy-drive-sync/`.
The installer uses no sudo and starts no transfers.

## Connect Google Drive

Run `rclone config` and create a Google Drive remote named `omarchy-drive`
with full Drive access. Create your own OAuth client ID following the
[rclone instructions](https://rclone.org/drive/#making-your-own-client-id);
rclone's shared client ID is being retired. Google sign-in runs in your browser.
Credentials remain in your rclone config and are never included in this plugin.

Create the **Omarchy** folder at the root of My Drive if it does not exist.
Then run:

```bash
~/.local/bin/omarchy-drive-setup
```

The first setup expects an empty local folder. It verifies the remote folder,
downloads regular files, and enables sync every five minutes while logged in.
Files work offline after downloading; offline edits sync after reconnecting.
Google Docs, Sheets, and Slides are excluded.

## Use

| Bar display | Meaning |
| --- | --- |
| Drive ↻ | Checking files or syncing without byte statistics yet |
| Drive 42% | Transfer progress for the current rclone phase |
| Drive ✓ | Last recorded sync succeeded |
| Drive ! | Last sync failed or was interrupted |
| Drive paused | Automatic timer is stopped |
| Drive setup | Initial setup has not completed |

Hover for transfer speed, file counts, filenames, and the last successful sync.
Left-click opens a live terminal dashboard; middle-click starts a sync;
right-click opens the local directory. Progress refreshes every second and may
restart between bisync phases as more work is discovered. Successful sync does
not mean continuous cloud monitoring.

```bash
systemctl --user stop omarchy-drive-sync.timer     # Pause scheduled sync
systemctl --user start omarchy-drive-sync.timer    # Resume
~/.local/bin/omarchy-drive-sync                    # Sync in this terminal
```

## Recovery and file behavior

**Deletions propagate in both directions.** Conflicts retain both versions.
Replaced or deleted files are backed up locally under
`~/.local/state/omarchy-drive-sync/backups/` and in Drive under
`Omarchy-sync-backups/`. Backups have no automatic retention cleanup.

Keep the `RCLONE_TEST` file in both folders. It prevents empty-baseline failures
and lets rclone verify access before changing files. Scheduled runs do not
automatically resync after a serious failure.

To recover a known empty or invalid baseline:

```bash
~/.local/bin/omarchy-drive-sync --repair
```

This archives the listings and merges both trees using the newer version when
the same path differs, backing up replaced versions. It can restore previously
deleted files. Review both folders before using recovery.

Progress logs (`current.jsonl`, `previous.jsonl`) and status live under
`~/.local/state/omarchy-drive-sync/`, or `$XDG_STATE_HOME` if set.
Logs contain filenames and activity; review them before sharing.

## Remove

Remove helpers before removing the plugin directory:

```bash
bash ~/.config/omarchy/plugins/io.github.xjaike.omarchy-drive/uninstall.sh
omarchy plugin remove io.github.xjaike.omarchy-drive
```

This stops sync and removes owned helper links and unmodified service files.
Local files, cloud files, backups, sync state, and rclone credentials remain.
Removing only the bar plugin does not stop scheduled sync.

## Development

```bash
omarchy plugin validate .
python3 -m unittest discover -s tests -v
bash -n install.sh uninstall.sh bin/omarchy-drive-sync bin/omarchy-drive-setup
```

The status reader only inspects local state and user service status; it does not
access Drive. The sync helpers perform cloud changes with the user's rclone
credentials. No credentials, file contents, or runtime logs belong in this repo.
