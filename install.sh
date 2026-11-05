#!/usr/bin/env bash
# Install standalone helpers; leave scheduled transfers paused until sign-in.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
for dependency in python3 systemctl git; do
  command -v "$dependency" >/dev/null || { echo "Missing dependency: $dependency" >&2; exit 1; }
done
python3 -c 'import sys; sys.exit("Python 3.11 or newer is required") if sys.version_info < (3, 11) else None'
bins="$HOME/.local/bin"
units="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
applications="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
backup="${XDG_STATE_HOME:-$HOME/.local/state}/omarchy-drive-v2/install-backups/$(date +%Y%m%dT%H%M%S)-$$"
mkdir -p "$bins" "$units" "$applications" "$backup"
# Retire the old engine before replacing its helpers. Never run both engines.
systemctl --user disable --now omarchy-drive-sync.timer 2>/dev/null || true
systemctl --user stop omarchy-drive-sync.service 2>/dev/null || true
for name in omarchy-drive omarchy-drive-sync omarchy-drive-setup; do
  target="$bins/$name"
  if [[ -e "$target" || -L "$target" ]]; then mv "$target" "$backup/$name"; fi
  ln -s "$root/bin/$name" "$target"
done
for name in omarchy-drive-sync.service omarchy-drive-sync.timer; do
  if [[ -e "$units/$name" ]]; then cp -a "$units/$name" "$backup/$name"; fi
  install -m 644 "$root/systemd/$name" "$units/$name"
done
if [[ -e "$applications/omarchy-drive.desktop" ]]; then
  cp -a "$applications/omarchy-drive.desktop" "$backup/omarchy-drive.desktop"
fi
install -m 644 "$root/omarchy-drive.desktop" "$applications/omarchy-drive.desktop"
systemctl --user daemon-reload
systemctl --user reset-failed omarchy-drive-sync.service 2>/dev/null || true
python3 "$root/bin/omarchy-drive" pause >/dev/null 2>&1 || true
echo "Installed Omarchy Google Drive 2.1. Previous helpers are in $backup"
echo 'The old sync engine is stopped. No transfers were started.'
echo 'Next: omarchy-drive setup'
