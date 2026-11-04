#!/usr/bin/env bash
# Explicit helper installation; adding the bar plugin alone changes no sync jobs.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
for dependency in rclone python3 flock systemctl mountpoint; do
  command -v "$dependency" >/dev/null || { echo "Missing dependency: $dependency" >&2; exit 1; }
done
bins="$HOME/.local/bin"
units="$HOME/.config/systemd/user"
backup="${XDG_STATE_HOME:-$HOME/.local/state}/omarchy-drive-sync/install-backups/$(date +%Y%m%dT%H%M%S)-$$"
mkdir -p "$bins" "$units" "$backup"
for name in omarchy-drive-sync omarchy-drive-setup; do
  target="$bins/$name"
  if [[ -e "$target" || -L "$target" ]]; then mv "$target" "$backup/$name"; fi
  ln -s "$root/bin/$name" "$target"
done
for name in omarchy-drive-sync.service omarchy-drive-sync.timer; do
  if [[ -e "$units/$name" ]]; then cp -a "$units/$name" "$backup/$name"; fi
  install -m 644 "$root/systemd/$name" "$units/$name"
done
systemctl --user daemon-reload
echo "Installed sync helpers. Previous files were saved in $backup"
echo 'No sync was started. Next run: ~/.local/bin/omarchy-drive-setup'
