#!/usr/bin/env python3
"""Read bounded rclone JSON logs; also provide a live terminal dashboard."""
import datetime
import json
import os
from pathlib import Path
import sys
import time

STATE = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'omarchy-drive-sync'

def snapshot():
    stats, events = {}, []
    try:
        with (STATE / 'current.jsonl').open('rb') as f:
            f.seek(0, 2)
            start = max(0, f.tell() - 262144)
            f.seek(start)
            if start:
                f.readline()
            lines = f.read().decode('utf-8', errors='replace').splitlines()
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if 'stats' in entry:
                stats = entry['stats']
            else:
                msg = entry.get('msg', '').strip()
                if msg:
                    events.append((entry.get('time', '')[11:19], entry.get('object', ''), msg))
    except OSError:
        pass
    return stats, events[-15:]

def size(n):
    value = float(n or 0)
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if abs(value) < 1024 or unit == 'TiB':
            return f'{value:.1f} {unit}'
        value /= 1024

def summary(stats):
    total, done = stats.get('totalBytes', 0), stats.get('bytes', 0)
    percent = f'{min(100, done / total * 100):.0f}%' if total else 'checking files'
    eta = stats.get('eta')
    return (f'{percent} · {size(done)} / {size(total)} · {size(stats.get("speed"))}/s'
            + (f' · ETA {int(eta)}s' if eta is not None else '')
            + f' · Files {stats.get("transfers", 0)}/{stats.get("totalTransfers", 0)}')

def safe(text):
    # File names and log messages must never become terminal control sequences.
    return ''.join(c if c.isprintable() else ' ' for c in str(text))

def watch():
    try:
        while True:
            stats, events = snapshot()
            try:
                result = json.loads((STATE / 'status.json').read_text())
            except (OSError, ValueError):
                result = {}
            lines = ['OMARCHY-DRIVE  ·  ' + result.get('status', 'waiting').upper(),
                     '~/GoogleDrive ↔ Google Drive/Omarchy',
                     'Updated ' + datetime.datetime.now().strftime('%H:%M:%S') + '  ·  Ctrl+C to close', '',
                     summary(stats) if stats else 'Waiting for transfer statistics (available from the next sync).', '',
                     'TRANSFERRING']
            transfers = stats.get('transferring', []) if result.get('status') == 'running' else []
            for item in transfers:
                lines.append(f'{item.get("percentage", 0):3.0f}%  {size(item.get("bytes"))} / {size(item.get("size"))}  {size(item.get("speedAvg", item.get("speed")))}/s  {item.get("name", "")}')
            if not transfers:
                lines.append('No files transferring right now.')
            lines += ['', 'RECENT FILES AND ACTIVITY']
            lines += [f'{stamp}  {name + ": " if name else ""}{msg}' for stamp, name, msg in events]
            print('\033[2J\033[H' + '\n'.join(safe(line) for line in lines), flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    watch()
