"""Shared transfer activity for bar hover details and the dashboard."""
import datetime
import sqlite3
import time
from .storage import read_json

ACTION_LABELS = {
    'upload': 'Upload', 'download': 'Download', 'conflict': 'Keep both versions',
    'mkdir-local': 'Create local folder', 'mkdir-remote': 'Create Drive folder',
    'archive-local': 'Archive locally', 'trash-remote': 'Move to Drive Trash',
}
DIRECTIONS = {'upload': 'Uploading', 'download': 'Downloading', 'backup': 'Backing up',
              'prepare': 'Preparing', 'verify': 'Verifying'}


def safe(value):
    return ''.join(c if c.isprintable() else ' ' for c in str(value))


def size(value):
    value = float(value or 0)
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if abs(value) < 1024 or unit == 'TiB':
            return f'{value:.1f} {unit}'
        value /= 1024


def duration(seconds):
    if seconds is None:
        return ''
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return f'{seconds // 3600}h {(seconds % 3600) // 60}m'
    if seconds >= 60:
        return f'{seconds // 60}m {seconds % 60}s'
    return f'{seconds}s'


def current_lines(current):
    if not current:
        return []
    title = current.get('label') or DIRECTIONS.get(current.get('direction'), 'Working on')
    if current.get('state') == 'failed':
        title = 'Stopped · ' + title
    lines = [title + ': ' + safe(current.get('name', ''))]
    if current.get('direction') in ('upload', 'download', 'backup'):
        total, done = current.get('total', 0), current.get('bytes', 0)
        metrics = f'{current.get("percent", 0):.0f}% · {size(done)} / {size(total)}'
        if current.get('speed', 0) > 0:
            metrics += ' · ' + size(current['speed']) + '/s'
        if current.get('eta') is not None:
            metrics += ' · ETA ' + duration(current['eta'])
        lines.append(metrics)
    return lines


def summary_line(data):
    if 'summary' not in data:
        return 'Transfer totals will appear after the next sync.'
    counts = data.get('summary', {})
    parts = [f'{counts.get("uploaded", 0)} uploaded', f'{counts.get("downloaded", 0)} downloaded']
    for key, title in (('conflicts', 'conflicts kept'), ('removed', 'archived/trashed'), ('folders', 'folders created')):
        if counts.get(key):
            parts.append(f'{counts[key]} {title}')
    return ' · '.join(parts)


def detail_lines(data, queue_limit=5, recent_limit=5):
    current = data.get('current')
    lines = ['CURRENT FILE', *current_lines(current), ''] if current else []
    if data.get('total'):
        lines.append(f'Changes: {data.get("completed", 0)}/{data["total"]} · {data.get("pending", 0)} remaining')
    lines.append(summary_line(data))
    queue = data.get('queue', [])
    if queue:
        lines += ['', f'WAITING FILES ({data.get("queued", len(queue))})']
        for item in queue[:queue_limit]:
            lines.append(ACTION_LABELS.get(item['action'], item['action']) + ': ' + safe(item['path']))
        more = data.get('queued', len(queue)) - min(len(queue), queue_limit)
        if more > 0:
            lines.append(f'+ {more} more changes')
    recent = data.get('recent', [])
    if recent:
        lines += ['', 'RECENT FILES']
        for item in recent[:recent_limit]:
            stamp = datetime.datetime.fromtimestamp(item['time']).strftime('%H:%M:%S')
            action = {'upload': '↑ Uploaded', 'download': '↓ Downloaded', 'conflict': 'Kept both versions',
                      'mkdir-local': 'Created local folder', 'mkdir-remote': 'Created Drive folder',
                      'archive-local': 'Archived locally', 'trash-remote': 'Moved to Drive Trash'}.get(item['action'], item['action'])
            if item.get('state') == 'failed':
                action = ACTION_LABELS.get(item['action'], item['action']) + ' failed'
            lines.append(f'{stamp} · {action}: {safe(item["path"])}')
    return lines


def recent_events(paths, limit=20):
    """Read existing history so an upgrade does not leave the hover list empty."""
    database = paths.state / 'sync.sqlite3'
    if not database.exists():
        return []
    try:
        connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=.1)
        try:
            rows = connection.execute('SELECT time,message FROM events ORDER BY id DESC LIMIT ?', (limit * 2,)).fetchall()
        finally:
            connection.close()
    except (sqlite3.Error, OSError):
        return []
    result = []
    for stamp, message in rows:
        action, separator, path = message.partition(': ')
        if separator and action in ACTION_LABELS:
            result.append(dict(action=action, path=path, state='completed', size=0, time=stamp))
        if len(result) == limit:
            break
    return result


class Activity:
    def __init__(self, paths, clock=time.monotonic):
        self.paths, self.clock = paths, clock
        self.started = False
        self.failed = False
        self.data = read_json(paths.status)
        if 'recent' not in self.data:
            self.data['recent'] = recent_events(paths)
        self.waiting = []
        self.active = None
        self.sample = None
        self.last_write = float('-inf')

    def write(self, kind='running', message=None):
        if message is not None:
            self.data['message'] = message
        self.data['kind'] = kind
        self.paths.record(kind, self.data.get('message', ''), **{
            k: v for k, v in self.data.items() if k not in ('kind', 'message', 'updated', 'last_success', 'scheduled')})
        self.last_write = self.clock()

    def start(self):
        self.started = True
        self.data.update(current=None, queue=[], pending=0, queued=0, active=None, total=0, completed=0,
                         phase='scanning', started=time.time(), finished=None, last_error=None, blockedPath=None,
                         summary=dict(uploaded=0, downloaded=0, conflicts=0, removed=0, folders=0))
        self.write(message='Checking local files and Google Drive…')

    def planned(self, actions):
        self.waiting = [{'action': a.kind, 'path': a.path} for a in actions]
        self.data.update(total=len(actions), pending=len(actions), queued=len(actions), queue=self.waiting[:20], phase='syncing')
        self.write(message=f'Syncing {len(actions)} changes')

    def begin(self, action):
        self.active = {'action': action.kind, 'path': action.path}
        self.data['active'] = self.active
        self.data['queue'] = self.waiting[1:21]
        self.data['queued'] = max(0, len(self.waiting) - 1)
        self.phase('prepare', action.path, label=ACTION_LABELS.get(action.kind, 'Working on'))

    def phase(self, direction, path, total=0, label=None):
        self.sample = None
        self.data['current'] = dict(direction=direction, name=path, bytes=0, total=total,
                                    percent=0, speed=0, eta=None, label=label or DIRECTIONS.get(direction, 'Working on'))
        title = label or DIRECTIONS.get(direction, 'Working on')
        self.write(message=title + ': ' + path)

    def transfer(self, direction, name, done, total):
        current = self.data.get('current')
        if not current:
            self.phase(direction, name, total)
            current = self.data['current']
        # Backing up a cloud version is a download, but is not a user-file download.
        if current['direction'] != 'backup' and current['direction'] != direction:
            self.phase(direction, current['name'], total)
            current = self.data['current']
        now = self.clock()
        speed = current.get('speed', 0)
        if self.sample:
            elapsed, count = now - self.sample[0], done - self.sample[1]
            if elapsed > 0 and count > 0:
                measured = count / elapsed
                speed = .65 * speed + .35 * measured if speed else measured
        self.sample = (now, done)
        current.update(bytes=done, total=total, percent=min(100, done / total * 100) if total else 100,
                       speed=speed, eta=(total - done) / speed if speed > 0 else None)
        if now - self.last_write >= .25 or done == total:
            self.write()

    def file_done(self, direction, path, size_bytes=0):
        self.data['summary']['uploaded' if direction == 'upload' else 'downloaded'] += 1
        self.add_recent(direction, path, size_bytes=size_bytes)
        self.write()

    def add_recent(self, action, path, state='completed', size_bytes=0):
        entry = dict(action=action, path=path, state=state, size=size_bytes, time=time.time())
        if state == 'failed':
            entry['error'] = self.data.get('last_error', '')
        self.data['recent'] = [entry, *self.data.get('recent', [])][:20]

    def complete(self, action, skipped=False):
        self.data['completed'] += 1
        self.waiting = [item for item in self.waiting if item['path'] != action.path]
        self.data.update(pending=len(self.waiting), queued=len(self.waiting), queue=self.waiting[:20], current=None, active=None)
        if not skipped:
            counts = self.data['summary']
            if action.kind in ('mkdir-local', 'mkdir-remote'):
                counts['folders'] += 1
            if action.kind in ('archive-local', 'trash-remote'):
                counts['removed'] += 1
            if action.kind == 'conflict':
                counts['conflicts'] += 1
            if action.kind not in ('upload', 'download', 'conflict'):
                self.add_recent(action.kind, action.path)
        self.write(message=f'Syncing · {self.data["completed"]}/{self.data["total"]} changes complete')

    def finish(self):
        self.data.update(phase='finished', current=None, active=None, queue=[], pending=0, queued=0, finished=time.time())
        changed = bool(self.data.get('total'))
        message = 'Up to date · ' + summary_line(self.data) if changed else 'Everything is up to date'
        self.write('success', message)

    def failure(self, kind, message, path=None):
        self.failed = True
        active = self.data.get('active')
        self.data['last_error'] = message
        self.data['blockedPath'] = path
        if active:
            self.add_recent(active['action'], active['path'], state='failed')
        self.data.update(phase='interrupted' if kind == 'interrupted' else 'failed', last_error=message, finished=time.time())
        if self.data.get('current'):
            self.data['current']['state'] = 'failed'
        self.write(kind, message)
