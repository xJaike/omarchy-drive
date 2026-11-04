#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
units="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
applications="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
for name in omarchy-drive-sync.service omarchy-drive-sync.timer; do
  if [[ -f "$units/$name" ]] && ! cmp -s "$root/systemd/$name" "$units/$name"; then
    echo "Modified unit retained: $units/$name. Review it before removal." >&2
    exit 1
  fi
done
systemctl --user disable --now omarchy-drive-sync.timer
systemctl --user stop omarchy-drive-sync.service
for name in omarchy-drive omarchy-drive-sync omarchy-drive-setup; do
  target="$HOME/.local/bin/$name"
  if [[ -L "$target" && "$(readlink "$target")" == "$root/bin/$name" ]]; then rm -- "$target"; fi
done
for name in omarchy-drive-sync.service omarchy-drive-sync.timer; do
  if [[ -f "$units/$name" ]]; then rm -- "$units/$name"; fi
done
if cmp -s "$root/omarchy-drive.desktop" "$applications/omarchy-drive.desktop"; then
  rm -- "$applications/omarchy-drive.desktop"
fi
systemctl --user daemon-reload
echo 'Removed app helpers and schedule. Local files, cloud files, backups, database, and Google credentials were retained.'
