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

![Native recent-files view with sample data](docs/images/recent-files.png)

*The actual `RecentFiles.qml` component rendered with sample files. No personal
account or file history is shown.*

## Full setup documentation

Start with the [illustrated setup guide](docs/STANDALONE_SETUP.md). It covers
plugin installation, Google Cloud project selection, Drive API activation,
OAuth consent, Desktop client creation, browser sign-in, folder selection,
first-sync review, automatic sync and troubleshooting.

![Google Drive backend setup overview](docs/images/setup-overview.png)

*Original setup diagram, not a Google Cloud screenshot. Navigation checked
2026-11-04. See the [visual guide index](docs/README.md) for individual figures
and current official references.*

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

Use your own **Desktop OAuth client**, created in a Google Cloud project you
control. Existing standalone-backend users keep their saved connection, folder
pair and sync history; credentials from other sync clients are not imported.

| Stage | Procedure | Visual guide |
| --- | --- | --- |
| 1. Project | Create/select a Cloud project and keep it selected for the next stages. | [Project and API](docs/images/google-project-api.png) |
| 2. API | Enable **Google Drive API** in that project. Sign-in approval alone does not enable it. | [Activation instructions](docs/STANDALONE_SETUP.md#5-enable-the-google-drive-api) |
| 3. Consent | Configure **Branding → Audience → Data Access**. For an External Testing app, add your account as a test user and add the Drive scope. | [Consent settings](docs/images/google-consent.png) |
| 4. Client | Under **Clients**, create a **Desktop app** OAuth client and download its JSON. | [Desktop client](docs/images/google-desktop-client.png) |
| 5. Connect | Keep the terminal open, sign in in the browser, then check and preview before syncing. | [Connection sequence](docs/images/connect-and-sync.png) |

Create an **Omarchy** folder at the root of My Drive. Rename your downloaded
client file to `omarchy-drive-client.json` for the examples below, then run:

```bash
chmod 600 ~/Downloads/omarchy-drive-client.json
~/.local/bin/omarchy-drive setup --client ~/Downloads/omarchy-drive-client.json
~/.local/bin/omarchy-drive doctor
~/.local/bin/omarchy-drive preview
```

Before applying the preview, choose folders in the panel. First setup transfers
no file content. When the plan looks right, run the first sync:

```bash
~/.local/bin/omarchy-drive sync
```

After success, enable the panel's automatic-sync switch if desired. Keep it off
while reviewing the initial setup.

The required scope is `https://www.googleapis.com/auth/drive`; it grants broad
Drive access. Sync operations are scoped by the configured folder ID. A Web app
client, API key or service-account JSON cannot replace a Desktop OAuth client.

If Google sign-in succeeds but setup reports **403 accessNotConfigured**, enable
the Drive API in the client's project, allow activation to take effect, then run:

```bash
~/.local/bin/omarchy-drive setup --continue
```

For an External app left in Testing, refresh tokens can expire after seven days.
See the full guide for publishing-status considerations, reconnection, custom
folder pairs and troubleshooting. Publishing is distinct from Google verification.

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

## Recent files and transfers

The current transfer has a separate progress card with direction, filename,
parent folder, percentage, transferred bytes, speed and ETA. Backup transfers
are identified separately from uploads and downloads.

Recent files show their latest activity in individual rows: filename, parent
folder, status and relative time. Long names are elided in the middle; hover
for the full path, exact timestamp, known size and any saved error. Four rows
are shown initially; **Show history** expands the retained history.

Click a row, or focus it and press Enter/Space, to show its containing local
folder. Missing files fall back to the nearest existing parent. This action
never executes or opens a downloaded file. The failed row’s **retry icon**, when available,
rechecks and retries the complete sync rather than targeting an individual file.

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

The panel follows Omarchy’s active theme automatically, including popup colors,
status accents, muted text, and the shell font. Theme changes apply live.

### Sync folders from your home directory

Open the Drive panel and click **Choose home folders**. Click a folder such as
`~/Documents` to enable two-way sync with `Drive/Omarchy/Documents`. Its files
stay in their original location; no symlinks or extra local copies are created.
The existing `~/GoogleDrive` folder continues syncing alongside these folders.
The next automatic sync transfers the selected content, or click **Sync now**.

Click a selected folder again to stop syncing it. Both local and cloud files
remain. Selecting it again starts a merge and preserves conflicting versions.
Existing cloud folders with the same name are merged using the usual conflict
protection. A same-name folder already inside `~/GoogleDrive` must be resolved
before linking its home-folder counterpart.

Only visible folders directly inside your home directory are offered. Hidden
folders, the home directory itself, symlinks, and folders on another filesystem
are excluded. A selected folder that disappears or is replaced blocks syncing
instead of being treated as deleted content. Unsupported symlinks or special
files inside a selected folder also block syncing; check **Preview** in the CLI
after selecting large project trees. Normal two-way deletion rules apply while
a folder is selected: cloud deletions preserve local backup copies, and local
deletions move the cloud copy to Trash.
