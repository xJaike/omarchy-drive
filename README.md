# Omarchy Drive

> Fork of [hominluo/omarchy-google-drive](https://github.com/hominluo/omarchy-google-drive),
> adapted to use our standalone Google Drive API backend instead of rclone.

This version retains the original panel, folder picker, keyboard navigation and
Google Drive icon by **Homin Luo**, including work credited upstream to
**Wesley Cole**. The replacement sync backend is by **xJaike**. This is an
independent fork, not an official upstream release.

**No rclone, Insync, or FUSE is required.** See [NOTICE.md](NOTICE.md) for provenance
and trademark information, and [LICENSE](LICENSE) for the retained MIT copyright
notices.

The bar is icon-only. Its badge changes for uploading/downloading, syncing,
up-to-date, paused, offline and error states. Hover for filenames, per-file
percentage, transfer rate, ETA, waiting files and recent activity. Left-click
opens the panel; right-click refreshes it.

## Install on Omarchy

Requires Omarchy Quattro, Python 3.11+ with SQLite, systemd user services, a
browser, and `xdg-open`. No Python packages need installing.

```bash
omarchy plugin add https://github.com/xJaike/omarchy-drive.git --enable
```

Open the panel and choose **Install and connect**. This opens a terminal, installs
the user sync helpers, and displays the Google account setup steps. Installation
leaves scheduled sync paused and starts no file transfers.

Alternatively:

```bash
bash ~/.config/omarchy/plugins/io.github.xjaike.google-drive/install.sh
```

Adding this plugin alone displays status and setup guidance using its bundled
backend. It does not require a pre-existing `omarchy-drive` executable. Keep the
installed plugin directory in place while its helper links are installed.

Disable other Drive plugins and sync clients targeting the same folder before
syncing. This fork has its own plugin ID: `io.github.xjaike.google-drive`.

## Google account setup

Use your own Google Desktop OAuth client. If you already used our standalone
backend, this fork reuses its existing Google connection, folder pair and SQLite
history. It does not read credentials from another sync client.

1. Create/select a project in [Google Cloud Console](https://console.cloud.google.com/).
2. Enable the **Google Drive API** in that project. OAuth sign-in alone does not
   activate the API.
3. Configure **Google Auth Platform → Branding, Audience, Data Access**. For a
   personal Testing app, add your Google account as a test user and add scope
   `https://www.googleapis.com/auth/drive`.
4. Create a **Desktop app** under **Clients** and download its JSON.
5. Create one **Omarchy** folder at the root of My Drive, then connect:

   ```bash
   ~/.local/bin/omarchy-drive setup --client ~/Downloads/YOUR_DESKTOP_CLIENT.json
   ```

6. Review proposed changes before syncing:

   ```bash
   ~/.local/bin/omarchy-drive preview
   ```

For a disabled-API error after successful sign-in, enable the Drive API in the
client's project, allow activation to take effect, then run:

```bash
~/.local/bin/omarchy-drive setup --continue
```

The full installation, OAuth, troubleshooting and backup guide is in
[docs/STANDALONE_SETUP.md](docs/STANDALONE_SETUP.md). External Google apps left in
Testing can have seven-day refresh-token expiry; that guide explains the relevant
Google publishing settings and reconnection steps.

## Panel controls

| Control | Behavior |
| --- | --- |
| Folder checkbox | Include/exclude a top-level directory inside the configured Drive folder |
| Loose files checkbox | Include/exclude regular files directly inside that folder |
| Sync now / `s` | Start one sync through the standalone systemd service |
| Automatic sync switch / `a` | Enable or pause the timer |
| Open / `o` | Open the configured local offline folder |
| Drive web / `b` | Open the configured cloud folder in your browser |
| Backups / `c` | Open the local backup directory |
| `j`, `k`, arrows, Enter, Space | Navigate and toggle folders |
| Escape | Close the panel |

Selection is scoped to the existing backend folder pair, by default
`~/GoogleDrive` ↔ **My Drive/Omarchy**. It does not silently expand access or start
syncing the entire My Drive account. The scope is displayed in the panel.
Existing installations keep their current selection behavior; without an
explicit selection, all files in that configured pair are included.

Deselecting an item stops syncing it and **retains local and cloud copies**.
Re-selecting merges content rather than applying deletions from an old excluded
baseline. No cleanup button permanently removes deselected files.

The upstream **Browse all** mount is replaced by **Drive web**. This backend has
no virtual filesystem or on-demand FUSE mount. Native Google Docs/Sheets/Slides
and shortcuts remain available in the browser and are skipped in offline sync.

## Sync behavior

- Content checksums detect changes. Conflicting local content is kept as a
  conflict copy; the cloud copy takes the original name.
- **Deletions of tracked, selected files propagate both ways.** Removed or
  overwritten content is backed up locally; cloud deletions use Drive Trash.
- Complete scans, checksum verification, conditional cloud updates, a single
  sync lock, per-file SQLite checkpoints, resumable transfers and bulk-deletion
  limits are handled by the standalone engine.
- Local backups are outside the sync folder, e.g.
  `~/.GoogleDrive-omarchy-drive-backups/`. They have no automatic retention cleanup.
- Automatic sync survives panel/shell closure and runs through user services.
- Preview is read-only for file content. Run it before adopting this client for
  important data and keep an independent backup.

Config is in `~/.config/omarchy-drive/`; state is in
`~/.local/state/omarchy-drive-v2/`. XDG base-directory overrides are supported.
The status poll reads local state; the separate folder-list command contacts
Google and caches folder metadata and storage quota. Local byte sizes are bounded
estimates. Neither API credentials nor runtime state belong in the repository.

## Settings

```bash
omarchy bar set io.github.xjaike.google-drive syncIntervalMin 5 --json
omarchy bar set io.github.xjaike.google-drive refreshIntervalSec 2 --json
```

Toggle automatic sync off and on to apply a new sync interval. Folder paths and
cloud IDs are owned by the backend's initial `setup` command, rather than being
changed by bar presentation settings. See the full setup guide for custom pairs.

## Update

```bash
omarchy plugin update io.github.xjaike.google-drive
bash ~/.config/omarchy/plugins/io.github.xjaike.google-drive/install.sh
```

The installer pauses scheduled sync while replacing helpers. Review with
`~/.local/bin/omarchy-drive preview`, then re-enable the panel's automatic switch
when ready. Preserve local Git changes before updating.

## Remove

Remove helpers before removing their source directory:

```bash
bash ~/.config/omarchy/plugins/io.github.xjaike.google-drive/uninstall.sh
omarchy plugin remove io.github.xjaike.google-drive
```

Local/cloud files, Google credentials, sync state and backups remain. Removing
only the bar plugin does not remove its user service. Keep modified unit files
for review if the uninstaller reports them.

## Validation

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q omarchy_drive gdrive-sync.py configure.py
bash -n install.sh uninstall.sh
omarchy plugin validate .
```

Tests use temporary files, a local Drive protocol server, and fake user services.
They do not contact a real Google account. End-to-end account checks require your
own OAuth client. This is an independent fork; upstream's sync-engine behavior
and release claims do not automatically apply to it.

License: [MIT](LICENSE).
