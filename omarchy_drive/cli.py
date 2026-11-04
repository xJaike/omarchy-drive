import argparse
import datetime
import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
import termios
import time
import tty

from . import __version__
from .auth import Tokens, authorize
from .drive import Drive, FOLDER
from .engine import Engine
from .http import HTTP, APIError
from .storage import Paths, Store, DriveError, AuthRequired, Blocked, Offline, atomic_json, read_json

GUIDE = '''Google account setup (one time)

1. Open https://console.cloud.google.com/ and create or select a project.
2. Enable the Google Drive API for that project.
3. In Google Auth Platform, configure Branding and Audience.
   For a personal External app in Testing, add your Google account as a test user.
4. Under Data Access, add https://www.googleapis.com/auth/drive.
5. Under Clients, create an OAuth client with application type Desktop app.
6. Download its JSON file, then run:

   omarchy-drive setup --client ~/Downloads/client_secret_....json

Your browser will ask you to approve Google Drive access. This app uses full
Drive scope to sync the existing Omarchy folder; its file operations are scoped
to the folder ID chosen during setup. Tokens stay in your private local config.
Google may expire refresh tokens for External apps left in Testing after seven
days. Set the app's publishing status appropriately for continued personal use.

The default pair is ~/GoogleDrive and Drive/Omarchy. Existing files are merged;
conflicting content is kept under a conflict-copy name. Setup itself transfers
no files. Preview the first sync before enabling the background timer.
'''


def safe(value):
    return ''.join(c if c.isprintable() else ' ' for c in str(value))


def unit(action, *args):
    try:
        return subprocess.call(['systemctl', '--user', action, *args])
    except FileNotFoundError:
        raise Blocked('systemd is unavailable. You can still run omarchy-drive sync manually.')


def context(paths):
    settings = paths.configured()
    store = Store(paths.state)
    def progress(direction, name, done, total):
        paths.record('running', direction + ': ' + name, current=dict(direction=direction, name=name, bytes=done, total=total))
    drive = Drive(HTTP(Tokens(paths)), store, paths.state, progress)
    return settings, store, drive


def setup(args, paths):
    if not args.client and not args.continue_setup:
        print(GUIDE)
        return 0
    if paths.settings.exists():
        existing = read_json(paths.settings)
        if existing.get('folder_id') and (args.folder_id or args.folder != 'Omarchy' or args.local != str(Path.home() / 'GoogleDrive')):
            raise Blocked('Already configured. Use setup --client PATH without folder overrides to reconnect.')
    else:
        existing = {}
    with paths.lock():
        if args.continue_setup:
            if not paths.client.exists() or not paths.tokens.exists():
                raise AuthRequired('No saved sign-in exists. Run setup --client PATH first.')
        else:
            authorize(paths, Path(args.client).expanduser())
        store = Store(paths.state)
        try:
            drive = Drive(HTTP(Tokens(paths)), store, paths.state)
            folder_id = existing.get('folder_id') or args.folder_id or drive.root(args.folder, args.create_folder)
            item = drive.get(folder_id)
            if item.get('trashed') or item.get('mimeType') != FOLDER:
                raise Blocked('The configured Drive ID must be a folder outside Trash.')
            local = Path(existing.get('local_dir', args.local)).expanduser().absolute()
            if local.is_symlink() or os.path.ismount(local):
                raise Blocked('Choose an ordinary local directory, not a mount or symbolic link.')
            local.mkdir(parents=True, exist_ok=True)
            from .engine import scan_local
            scan_local(local)
            settings = dict(existing, local_dir=str(local), folder_id=folder_id, folder_name=item['name'], max_deletes=20)
            atomic_json(paths.settings, settings)
            paths.record('ready', 'Connected. Preview the first sync: omarchy-drive preview')
        finally:
            store.close()
    print('Connected. No files were transferred. Next: omarchy-drive preview')
    return 0


def run_sync(args, paths):
    with paths.lock():
        settings, store, drive = context(paths)
        try:
            engine = Engine(paths, settings, store, drive)
            if args.command == 'preview':
                # Show large deletion plans without executing them.
                actions = engine.snapshot(allow_many_deletes=True)
                changed = [a for a in actions if a.kind not in ('remember', 'forget')]
                for action in changed:
                    print(safe(f'{action.kind:15} {action.path}' + (' — ' + action.note if action.note else '')))
                print(f'{len(changed)} proposed changes. No files were uploaded, overwritten, or deleted.')
                return 0
            changed = engine.sync(args.allow_many_deletes)
            print(f'Up to date. Applied {len(changed)} changes.')
            return 0
        finally:
            store.close()


def status(paths):
    data = read_json(paths.status)
    if not paths.settings.exists():
        data.update(kind='setup', message='Connect Google Drive: omarchy-drive setup')
    data.setdefault('kind', 'ready')
    data.setdefault('message', 'Ready to sync')
    if data['kind'] == 'running':
        # A stale status after a crash must not appear as an active transfer.
        try:
            with paths.lock():
                data.update(kind='interrupted', message='Previous sync was interrupted. Run sync to resume.')
        except Blocked:
            pass
    labels = {'setup': 'Drive setup', 'auth': 'Drive sign-in', 'offline': 'Drive offline',
              'blocked': 'Drive blocked', 'error': 'Drive !', 'success': 'Drive ✓',
              'ready': 'Drive ready', 'running': 'Drive ↻', 'interrupted': 'Drive retry'}
    data['label'] = labels.get(data['kind'], 'Drive …')
    settings = read_json(paths.settings)
    data['tooltip'] = '\n'.join([f'Omarchy Drive {__version__} · Direct Google Drive API',
        data['message'], settings.get('local_dir', '~/GoogleDrive') + ' ↔ Drive/' + settings.get('folder_name', 'Omarchy'),
        'Left: dashboard · Middle: sync now · Right: open folder'])
    return data


def doctor(paths):
    print('Omarchy Drive', __version__, '— direct Google Drive API')
    print('OAuth client:', 'configured' if paths.client.exists() else 'missing; run omarchy-drive setup')
    print('Google sign-in:', 'configured' if paths.tokens.exists() else 'not connected')
    with paths.lock():
        settings, store, drive = context(paths)
        try:
            from .engine import scan_local
            local = scan_local(settings['local_dir'])
            remote = drive.scan(settings['folder_id'])
            print(f'Local scan: OK ({len(local)} items)')
            print(f'Drive access: OK ({len(remote)} items)')
            print(f'Sync baseline: {len(store.baseline())} files; first-run merging is safe without a baseline')
            print('Next: omarchy-drive preview')
        finally:
            store.close()
    return 0


def watch(paths):
    if not sys.stdin.isatty():
        print(json.dumps(status(paths)))
        return 0
    settings = termios.tcgetattr(sys.stdin)
    actions = {'s': 'sync', 'v': 'preview', 'd': 'doctor', 'p': 'pause', 'r': 'resume', 'c': 'setup', 'o': 'open'}
    try:
        tty.setcbreak(sys.stdin.fileno())
        while True:
            data = status(paths)
            last = data.get('last_success')
            stamp = datetime.datetime.fromtimestamp(last).strftime('%b %d %H:%M:%S') if last else 'not yet synced'
            lines = [f'OMARCHY DRIVE {__version__}  ·  ' + data['kind'].upper(),
                     'Standalone Google Drive sync', '', data['message'], 'Last successful sync: ' + stamp,
                     'Automatic sync: ' + ('every minute' if data.get('scheduled') else 'paused')]
            current = data.get('current')
            if current and data['kind'] == 'running':
                total, done = current['total'], current['bytes']
                fraction = min(1, done / total) if total else 0
                lines += ['[' + '█' * int(fraction * 30) + '░' * (30 - int(fraction * 30)) + f'] {fraction:.0%}',
                          current['name'], f'{done:,} / {total:,} bytes']
            lines += ['', '[S] Sync  [V] Preview  [D] Diagnose  [C] Connect',
                      '[P] Pause schedule  [R] Resume  [O] Open folder  [Q] Quit', '', 'RECENT ACTIVITY']
            db = paths.state / 'sync.sqlite3'
            if db.exists():
                # URI read-only connection avoids creating or mutating a database from the UI.
                with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as conn:
                    events = conn.execute('SELECT time,message FROM events ORDER BY id DESC LIMIT 10').fetchall()
                lines += [datetime.datetime.fromtimestamp(t).strftime('%H:%M:%S') + '  ' + m for t, m in reversed(events)]
            print('\033[2J\033[H' + '\n'.join(safe(line) for line in lines), flush=True)
            if not select.select([sys.stdin], [], [], 1)[0]:
                continue
            key = sys.stdin.read(1).lower()
            if key == 'q':
                return 0
            if key in actions:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)
                try:
                    # Run the same entry point so interactive and scheduled sync share all logic.
                    subprocess.call([sys.executable, '-m', 'omarchy_drive', actions[key]], cwd=Path(__file__).resolve().parents[1])
                    input('\nPress Enter to return…')
                finally:
                    tty.setcbreak(sys.stdin.fileno())
    except KeyboardInterrupt:
        return 0
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)


def parser():
    app = argparse.ArgumentParser(description='Standalone Google Drive sync — no external sync engine')
    app.add_argument('--version', action='version', version=__version__)
    commands = app.add_subparsers(dest='command')
    for name in ('watch', 'status', 'preview', 'doctor', 'pause', 'resume', 'open'):
        commands.add_parser(name)
    sync = commands.add_parser('sync')
    sync.add_argument('--allow-many-deletes', action='store_true', help='Apply a large deletion plan after reviewing preview')
    connect = commands.add_parser('setup', help='Show Google Cloud instructions or connect a Desktop OAuth client')
    authentication = connect.add_mutually_exclusive_group()
    authentication.add_argument('--client', help='Path to your downloaded Desktop OAuth client JSON')
    authentication.add_argument('--continue', dest='continue_setup', action='store_true',
                                help='Finish setup using the previously saved Google sign-in')
    connect.add_argument('--folder', default='Omarchy')
    connect.add_argument('--folder-id')
    connect.add_argument('--create-folder', action='store_true')
    connect.add_argument('--local', default=str(Path.home() / 'GoogleDrive'))
    return app


def main(argv=None):
    args = parser().parse_args(argv)
    paths = Paths()
    paths.prepare()
    command = args.command or 'watch'
    try:
        if command == 'setup':
            return setup(args, paths)
        if command in ('sync', 'preview'):
            return run_sync(args, paths)
        if command == 'status':
            print(json.dumps(status(paths)))
            return 0
        if command == 'doctor':
            return doctor(paths)
        if command == 'pause':
            code = unit('disable', '--now', 'omarchy-drive-sync.timer')
            if code == 0:
                data = read_json(paths.status)
                atomic_json(paths.status, dict(data, scheduled=False))
            print('Scheduled sync paused. Active transfers can finish.')
            return code
        if command == 'resume':
            paths.configured()
            code = unit('enable', '--now', 'omarchy-drive-sync.timer')
            if code == 0:
                data = read_json(paths.status)
                atomic_json(paths.status, dict(data, scheduled=True))
            print('Scheduled sync enabled; it checks every minute.')
            return code
        if command == 'open':
            folder = read_json(paths.settings).get('local_dir', str(Path.home() / 'GoogleDrive'))
            return subprocess.call(['xdg-open', folder])
        return watch(paths)
    except KeyboardInterrupt:
        if command == 'sync':
            paths.record('interrupted', 'Sync interrupted. Completed files are saved; run sync to resume.', current=None)
        return 130
    except (DriveError, sqlite3.Error, OSError, ValueError) as error:
        if isinstance(error, DriveError):
            message, kind = str(error), error.kind
        elif isinstance(error, sqlite3.Error):
            message, kind = 'Cannot read the sync database. Your files were retained; restore the database from backup.', 'blocked'
        else:
            message, kind = 'A local file could not be read or stored. Check permissions and available disk space.', 'blocked'
        if command == 'sync' and not (isinstance(error, Blocked) and 'Another sync' in str(error)):
            paths.record(kind, message, current=None)
        print(kind.upper() + ': ' + safe(message), file=sys.stderr)
        return 1
