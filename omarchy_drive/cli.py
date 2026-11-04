import argparse
import datetime
import json
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
import termios
import time
import tty

from . import __version__
from .activity import Activity, detail_lines, recent_events
from .auth import Tokens, authorize
from .drive import Drive, FOLDER
from .engine import Engine
from .migrate import migrate
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

Setup transfers no files and selects no folders. Tick the home folders you want in the panel, then preview the first sync.
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
    drive = Drive(HTTP(Tokens(paths)), store, paths.state)
    return settings, store, drive


def setup(args, paths):
    if not args.client and not args.continue_setup:
        print(GUIDE)
        return 0
    if paths.settings.exists():
        existing = read_json(paths.settings)
        if existing.get('folder_id') and (args.folder_id or args.folder != 'Omarchy'):
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
            settings = dict(existing, folder_id=folder_id, folder_name=item['name'], max_deletes=20)
            atomic_json(paths.settings, settings)
            paths.record('ready', 'Connected. Preview the first sync: omarchy-drive preview')
        finally:
            store.close()
    print('Connected. No files were transferred. Next: omarchy-drive preview')
    return 0


def error_details(error):
    if isinstance(error, KeyboardInterrupt):
        return 'interrupted', 'Sync interrupted. Completed files are saved; run sync to resume.'
    if isinstance(error, DriveError):
        return error.kind, str(error)
    if isinstance(error, sqlite3.Error):
        return 'blocked', 'Cannot read the sync database. Your files were retained; restore the database from backup.'
    return 'blocked', 'A local file could not be read or stored. Check permissions and available disk space.'


def run_sync(args, paths):
    with paths.lock():
        activity = Activity(paths) if args.command == 'sync' else None
        args._activity = activity
        if activity:
            activity.start()
        store = None
        try:
            settings, store, drive = context(paths)
            engine = Engine(paths, settings, store, drive, activity)
            if args.command == 'preview':
                actions = engine.snapshot(allow_many_deletes=True)
                changed = [a for a in actions if a.kind not in ('remember', 'forget')]
                for action in changed:
                    print(safe(f'{action.kind:15} {action.path}' + (' — ' + action.note if action.note else '')))
                print(f'{len(changed)} proposed changes. No files were uploaded, overwritten, or deleted.')
                return 0
            changed = engine.sync(args.allow_many_deletes)
            print(f'Up to date. Applied {len(changed)} changes.')
            return 0
        except (DriveError, sqlite3.Error, OSError, ValueError, KeyboardInterrupt) as error:
            if activity:
                activity.failure(*error_details(error))
            raise
        finally:
            if store is not None:
                store.close()


def status(paths):
    data = read_json(paths.status)
    if 'recent' not in data:
        data['recent'] = recent_events(paths)
    if data.get('current'):
        current = dict(data['current'])
        if 'percent' not in current:
            total = current.get('total', 0)
            current['percent'] = min(100, current.get('bytes', 0) / total * 100) if total else 0
        data['current'] = current
    if not paths.settings.exists():
        data.update(kind='setup', message='Connect Google Drive: omarchy-drive setup')
    data.setdefault('kind', 'ready')
    data.setdefault('message', 'Ready to sync')
    if data['kind'] == 'running':
        # A stale status after a crash must not appear as an active transfer.
        try:
            with paths.lock():
                data.update(kind='interrupted', message='Previous sync was interrupted. Run sync to resume.', current=None)
        except Blocked:
            pass
    labels = {'setup': 'Drive setup', 'auth': 'Drive sign-in', 'offline': 'Drive offline',
              'blocked': 'Drive blocked', 'error': 'Drive !', 'success': 'Drive ✓',
              'ready': 'Drive ready', 'running': 'Drive ↻', 'interrupted': 'Drive retry'}
    data['label'] = labels.get(data['kind'], 'Drive …')
    if data['kind'] == 'running' and data.get('current'):
        current = data['current']
        prefix = {'upload': 'Drive ↑', 'download': 'Drive ↓', 'backup': 'Drive backup'}.get(current['direction'], 'Drive ↻')
        data['label'] = prefix + (f' {current.get("percent", 0):.0f}%' if current['direction'] in ('upload', 'download', 'backup') else '')
    settings = read_json(paths.settings)
    last = data.get('last_success')
    stamp = datetime.datetime.fromtimestamp(last).strftime('%b %d %H:%M:%S') if last else 'not yet synced'
    heading = {
        'running': 'Checking files' if data.get('phase') == 'scanning' else 'Sync in progress',
        'success': 'Everything is up to date', 'offline': 'Offline', 'auth': 'Sign-in required',
        'blocked': 'Sync needs attention', 'interrupted': 'Sync interrupted', 'setup': 'Setup needed',
    }.get(data['kind'], data['message'])
    lines = ['Omarchy Drive · ' + heading,
             f'{len(settings.get("home_folders", {}))} folders ↔ Drive/{settings.get("folder_name", "Omarchy")}',
             'Automatic sync: ' + ('enabled' if data.get('scheduled') else 'paused'),
             'Last successful sync: ' + stamp]
    if data['kind'] not in ('running', 'success'):
        lines += ['', data['message']]
    lines += ['', *detail_lines(data), '', 'Left: dashboard · Middle: sync now · Right: open folder']
    # Bound tooltip width so long paths cannot push the hover card off screen.
    import textwrap
    data['tooltip'] = '\n'.join(part for line in lines for part in
                               (textwrap.wrap(safe(line), width=84, break_on_hyphens=False) or ['']))

    return data


def doctor(paths):
    print('Omarchy Drive', __version__, '— direct Google Drive API')
    print('OAuth client:', 'configured' if paths.client.exists() else 'missing; run omarchy-drive setup')
    print('Google sign-in:', 'configured' if paths.tokens.exists() else 'not connected')
    with paths.lock():
        settings, store, drive = context(paths)
        try:
            from .engine import scan_local
            from .home_folders import mappings
            from .selection import includes
            included = lambda path: includes(path, settings)
            local = {}
            for name, source in mappings(settings, paths).items():
                if included(name):
                    local[name] = {'kind': 'dir'}
                    local.update(scan_local(source, name + '/', included))
            remote = drive.scan(settings['folder_id'], included)
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
            lines += ['', *detail_lines(data, queue_limit=10, recent_limit=10)]
            lines += ['', '[S] Sync  [V] Preview  [D] Diagnose  [C] Connect',
                      '[P] Pause schedule  [R] Resume  [O] Open folder  [Q] Quit']
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
    return app


def main(argv=None):
    args = parser().parse_args(argv)
    paths = Paths()
    paths.prepare()
    command = args.command or 'watch'
    try:
        migrate(paths)
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
            return subprocess.call(['xdg-open', str(Path.home())])
        return watch(paths)
    except KeyboardInterrupt as error:
        activity = getattr(args, '_activity', None)
        if command == 'sync' and not (activity and activity.failed):
            Activity(paths).failure(*error_details(error))
        return 130
    except (DriveError, sqlite3.Error, OSError, ValueError) as error:
        kind, message = error_details(error)
        activity = getattr(args, '_activity', None)
        if command == 'sync' and not (activity and activity.failed) and not (isinstance(error, Blocked) and 'Another sync' in str(error)):
            Activity(paths).failure(kind, message)
        print(kind.upper() + ': ' + safe(message), file=sys.stderr)
        return 1
