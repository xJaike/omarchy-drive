import contextlib
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time


class DriveError(Exception):
    kind = 'error'


class Blocked(DriveError):
    kind = 'blocked'


class AuthRequired(DriveError):
    kind = 'auth'


class Offline(DriveError):
    kind = 'offline'


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {} if default is None else default
    except (ValueError, OSError) as error:
        raise Blocked(f'Cannot read {Path(path).name}; restore it from a backup.') from error


class Paths:
    def __init__(self):
        self.config = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'omarchy-drive'
        self.state = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'omarchy-drive-v2'
        self.settings = self.config / 'settings.json'
        self.client = self.config / 'client.json'
        self.tokens = self.config / 'tokens.json'
        self.status = self.state / 'status.json'

    def prepare(self):
        for folder in (self.config, self.state):
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            folder.chmod(0o700)

    def configured(self):
        value = read_json(self.settings)
        if not value.get('folder_id') or not value.get('local_dir'):
            raise AuthRequired('Connect your Google account first: omarchy-drive setup')
        return value

    @contextlib.contextmanager
    def lock(self):
        self.prepare()
        with (self.state / 'sync.lock').open('a') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise Blocked('Another sync or setup is running.') from error
            yield

    def record(self, kind, message, **extra):
        try:
            data = read_json(self.status)
        except Blocked:
            data = {}  # Telemetry is replaceable; sync history is never silently reset.
        data.update(kind=kind, message=message, updated=time.time(), **extra)
        if kind == 'success':
            data['last_success'] = time.time()
        atomic_json(self.status, data)


class Store:
    def __init__(self, state):
        self.db = sqlite3.connect(Path(state) / 'sync.sqlite3')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS baseline(path TEXT PRIMARY KEY, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS pending(key TEXT PRIMARY KEY, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, time REAL NOT NULL, message TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')

    def close(self):
        self.db.close()

    def baseline(self):
        return {path: json.loads(data) for path, data in self.db.execute('SELECT path,data FROM baseline')}

    def remember(self, path, entry):
        with self.db:
            if entry is None:
                self.db.execute('DELETE FROM baseline WHERE path=?', (path,))
            else:
                self.db.execute('INSERT OR REPLACE INTO baseline VALUES (?,?)', (path, json.dumps(entry)))

    def pending(self, key):
        row = self.db.execute('SELECT data FROM pending WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_pending(self, key, value):
        with self.db:
            if value is None:
                self.db.execute('DELETE FROM pending WHERE key=?', (key,))
            else:
                self.db.execute('INSERT OR REPLACE INTO pending VALUES (?,?)', (key, json.dumps(value)))

    def identity(self, value):
        row = self.db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()
        if row and row[0] != value:
            raise Blocked('The sync folder or account changed. Use a separate state directory.')
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('identity',?)", (value,))

    def event(self, message):
        with self.db:
            self.db.execute('INSERT INTO events(time,message) VALUES (?,?)', (time.time(), message))
            self.db.execute('DELETE FROM events WHERE id < (SELECT MAX(id)-500 FROM events)')
