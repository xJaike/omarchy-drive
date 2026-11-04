# Omarchy Drive 2

A standalone Google Drive sync application for Linux. Its own Python engine talks
directly to the Google Drive v3 API. It does not run, import, or depend on rclone
or Insync. Python 3.11+ and its standard library are enough for manual sync; the
optional background schedule uses systemd user services. The optional bar widget
uses Omarchy Quattro.

The default sync pair is `~/GoogleDrive` ↔ `Google Drive/Omarchy`. Regular files
are available offline. Existing files on both sides are merged on the first sync.

## Install and connect

```bash
bash install.sh
omarchy-drive setup
```

The installer stops the previous sync timer/service, backs up replaced helpers,
and installs the new commands and application-menu launcher. It starts no sync.
It leaves old sync state, old credentials, local files, and cloud files intact.
Do not run another sync client against the same local directory concurrently.

Every independent Drive client needs a Google OAuth application. This project
ships no shared client credentials. Create your own once:

1. Open [Google Cloud Console](https://console.cloud.google.com/), create/select a
   project, and enable the **Google Drive API**.
2. In **Google Auth Platform**, configure **Branding** and **Audience**. For a
   personal External app in Testing, add your account as a test user.
3. Under **Data Access**, add `https://www.googleapis.com/auth/drive`.
4. Under **Clients**, create a **Desktop app** OAuth client and download its JSON.
5. Run the following with the downloaded file's actual path:

```bash
omarchy-drive setup --client ~/Downloads/client_secret_example.json
```

Sign in using the browser. The app uses a loopback callback, OAuth state
validation, and PKCE. Tokens refresh automatically and are saved with private
permissions under `~/.config/omarchy-drive/`. It never reads another client's
credentials. Full Drive scope is needed to access your existing folder; the
engine limits its file operations to the chosen folder ID.

Google can expire refresh tokens after seven days when an External consent app
remains in Testing; configure the consent app's publishing status appropriately
for ongoing use. See [Google's installed-app OAuth documentation](https://developers.google.com/identity/protocols/oauth2/native-app).

The remote folder must already exist. To create it explicitly, add
`--create-folder`. To select a specific existing folder or a different local
folder on first setup, use `--folder-id ID` and `--local /absolute/path`.
Setup transfers no file content. Reconnect with the same `setup --client PATH`
command; omit folder overrides when reconnecting.

If browser sign-in succeeds but setup reports `accessNotConfigured`, enable the
Google Drive API in the same Cloud project that owns your OAuth client. Sign-in
and API activation are separate. After activation takes effect, resume without
signing in again:

```bash
omarchy-drive setup --continue
```

## First sync

```bash
omarchy-drive preview    # Show proposed changes; no content changes
omarchy-drive sync       # Apply the merge
omarchy-drive resume     # Enable automatic checks every minute
omarchy-drive            # Open the live dashboard
```

You can also launch **Omarchy Drive** from the application menu.

| Dashboard key | Action |
| --- | --- |
| S | Sync now |
| V | Preview proposed changes |
| D | Check local files and Drive access |
| C | Show connection instructions |
| P / R | Pause / resume scheduled sync |
| O | Open local folder |
| Q | Close dashboard; scheduled sync continues |

All actions also work as shell commands: `omarchy-drive doctor`, `pause`,
`resume`, `open`, `status`, and `setup`. Status returns JSON for the optional bar.
An existing bar widget in a separate plugin checkout must be updated to this
version to show the new engine's status.

## Sync behavior

- Only a complete, successful local and cloud scan can produce a sync plan.
  Missing local roots, replaced local roots, inaccessible cloud folders,
  incomplete listings, duplicate Drive names, symbolic links, special files,
  and file/folder collisions stop the run before applying a plan.
- Changes are detected by content checksum, not which device's clock is newer.
- On first sync, differing files are both kept. The local copy is preserved as
  `name.conflict-local-CHECKSUM.ext` on both sides; the Drive copy takes the
  original path. The same rule applies when both copies change later.
- **File deletions propagate in both directions.** A cloud deletion archives an
  unchanged local copy; a local deletion moves an unchanged cloud file to Drive
  Trash. An edit competing with a deletion is preserved and restored to the
  other side. The app never permanently deletes anything from Drive.
- Replaced and removed file content is backed up under a private sibling folder,
  e.g. `~/.GoogleDrive-omarchy-drive-backups/`. This is outside the sync folder
  and on the same filesystem. There is no automatic backup cleanup.
- More than 20 file deletions, or more than 25% of a baseline with at least 20
  files, stop the plan. After reviewing `preview`, an intended bulk deletion can
  be applied with `omarchy-drive sync --allow-many-deletes`.
- Empty directories are created on both sides and retained. Folder removal and
  renames reconcile through file operations; empty old folders can remain.
- Native Google Docs/Sheets/Slides and shortcuts are skipped. This version targets
  My Drive folders. Shared drives and selective-sync filters are not implemented.
- Full folder listings and local hashes run each cycle. This first standalone
  version favors predictable reconciliation over large-drive indexing speed.

## Interrupted transfers and safety boundaries

Uploads use saved resumable sessions and pre-generated Drive IDs. If a server
accepted a create but its response was lost, retries reuse that ID. Downloads
resume into private partial files, verify checksums and cloud versions, then
publish through an atomic local replacement. Old local and cloud content is
backed up before overwrite. Cloud overwrites and trash operations require a
provider ETag and send `If-Match`; if no ETag is returned, the app refuses the
mutation rather than guessing about concurrency.

A SQLite write-ahead-log database checkpoints each completed file. A later
failure retains those checkpoints and pending sessions. The next run reconciles
actual content with the last saved baseline; it does not require a global repair
command. If the database is lost, first-run merging preserves both sides rather
than inferring deletions. A corrupt database is never silently reset.

The app cannot make editing a file in multiple applications/devices atomic.
It checks again before overwrite, keeps backups, and stops on detected concurrent
changes. Keep other sync clients off this folder. Back up important files
independently before adopting any new sync client.

Config: `~/.config/omarchy-drive/` (`$XDG_CONFIG_HOME` supported).
State: `~/.local/state/omarchy-drive-v2/` (`$XDG_STATE_HOME` supported).
Status and activity contain filenames; credentials and session URLs should never
be shared. Keep the project checkout in place while installed: helpers link to it.

## Validation and current limits

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q omarchy_drive
bash -n install.sh uninstall.sh
omarchy plugin validate .
```

Tests cover reconciliation using real temporary files, API protocol behavior,
OAuth refresh and callback validation, interrupted transfers, conflict
preservation, deletion limits, and installation. Live sign-in and an actual
Google account transfer still require your own OAuth client; automated tests do
not establish live Google compatibility or long-term production reliability.

## Remove

```bash
bash uninstall.sh
```

The uninstaller stops the schedule and removes owned helper links, unmodified
units, and the application-menu entry. Local files, cloud files, backups, state,
and credentials remain. Remove an optional Omarchy bar plugin separately.
