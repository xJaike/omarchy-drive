#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
units="$HOME/.config/systemd/user"
# Refuse to stop or delete units that another installer has replaced.
for name in omarchy-drive-sync.service omarchy-drive-sync.timer; do
  if [[ -f "$units/$name" ]] && ! cmp -s "$root/systemd/$name" "$units/$name"; then
    echo "Modified unit retained: $units/$name. Review it before removal." >&2
    exit 1
  fi
done
systemctl --user disable --now omarchy-drive-sync.timer
systemctl --user stop omarchy-drive-sync.service
for name in omarchy-drive-sync omarchy-drive-setup; do
  target="$HOME/.local/bin/$name"
  if [[ -L "$target" && "$(readlink "$target")" == "$root/bin/$name" ]]; then rm -- "$target"; fi
done
for name in omarchy-drive-sync.service omarchy-drive-sync.timer; do
  if [[ -f "$units/$name" ]]; then rm -- "$units/$name"; fi
done
systemctl --user daemon-reload
echo 'Removed sync helpers and timer. Files, backups, state, and Google credentials were retained.'
echo 'Remove the bar plugin with: omarchy plugin remove io.github.xjaike.omarchy-drive'
