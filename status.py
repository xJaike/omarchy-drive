#!/usr/bin/env python3
import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from progress import snapshot, summary

state = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'omarchy-drive-sync'
result = state / 'status.json'
try:
    data = json.loads(result.read_text())
except (OSError, ValueError):
    data = {}

if len(sys.argv) > 1 and sys.argv[1] == 'record':
    state.mkdir(parents=True, exist_ok=True)
    data.update(status=sys.argv[2], updated=time.time())
    if sys.argv[2] == 'success':
        data['last_success'] = time.time()
    if len(sys.argv) > 3:
        data['exit_code'] = int(sys.argv[3])
    temp = result.with_suffix('.tmp')
    temp.write_text(json.dumps(data))
    temp.replace(result)
    sys.exit(0)

def unit_value(unit, prop):
    try:
        p = subprocess.run(['systemctl', '--user', 'show', unit, '--property=' + prop, '--value'],
                           capture_output=True, text=True, timeout=3)
        return p.stdout.strip() if p.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''

running = False
try:
    with (state / 'run.lock').open('r') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            running = True
except OSError:
    pass
service = unit_value('omarchy-drive-sync.service', 'ActiveState')
timer = unit_value('omarchy-drive-sync.timer', 'ActiveState')
initialized = (state / 'initialized').exists()
if running or service in ('active', 'activating', 'deactivating'):
    kind, label = 'running', 'Drive ↻'
elif not initialized:
    kind, label = 'setup', 'Drive setup'
elif data.get('status') == 'error' or service == 'failed':
    kind, label = 'error', 'Drive !'
elif data.get('status') == 'running':
    kind, label = 'error', 'Drive !'
elif timer == 'inactive':
    kind, label = 'paused', 'Drive paused'
elif data.get('last_success'):
    kind, label = 'success', 'Drive ✓'
else:
    kind, label = 'waiting', 'Drive …'
messages = {
    'running': 'Sync in progress', 'setup': 'Setup needed: run ~/.local/bin/omarchy-drive-setup',
    'error': 'Sync failed or was interrupted. Left-click for details.',
    'paused': 'Automatic sync is paused', 'success': 'Last sync completed successfully',
    'waiting': 'Waiting for a recorded sync result',
}
last = data.get('last_success')
stamp = datetime.datetime.fromtimestamp(last).astimezone().strftime('%b %d, %I:%M %p') if last else 'not recorded yet'
stats, events = snapshot()
details = []
if stats:
    details.append(summary(stats))
    if kind == 'running':
        total = stats.get('totalBytes', 0)
        if total:
            label = f'Drive {min(100, stats.get("bytes", 0) / total * 100):.0f}%'
        for item in stats.get('transferring', [])[:5]:
            details.append(f'{item.get("percentage", 0):.0f}% · {item.get("name", "")}')
for _, name, msg in events[-3:]:
    details.append((name + ': ' if name else '') + msg[:180])
tooltip = '\n'.join([
    'omarchy-drive · ' + messages[kind], '~/GoogleDrive ↔ Google Drive/Omarchy',
    'Last successful sync: ' + stamp,
    'Automatic sync: ' + ('every 5 minutes' if timer == 'active' else 'paused' if timer == 'inactive' else 'status unavailable'),
    *details,
    'Left: live progress · Middle: sync now · Right: open folder',
])
print(json.dumps({'kind': kind, 'label': label, 'tooltip': tooltip}))
