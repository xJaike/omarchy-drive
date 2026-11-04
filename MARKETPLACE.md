### Repository URL

https://github.com/xJaike/omarchy-drive

### Category

Productivity

### Tags

Bar, Quickshell, System

### Suggest a missing tag

Cloud sync

### Maintainer notes

Plugin ID: `io.github.xjaike.omarchy-drive`

Standalone Google Drive sync for Linux with offline files, a live terminal
application, and an optional Omarchy bar widget. The engine talks directly to
Google Drive v3; it owns its OAuth flow, SQLite sync history, resumable transfers,
content-based conflict handling, and deletion safeguards.

Requires Python 3.11+ and a Google Desktop OAuth client created by the user.
Scheduled sync uses systemd user services. No external sync client is required.
Defaults to ~/GoogleDrive and the Omarchy folder in My Drive. Installation leaves
sync paused; removal retains files, backups, credentials, and state.

Desktop preview: not yet captured. Live Google account testing requires OAuth
setup; automated protocol and reconciliation tests are included.
