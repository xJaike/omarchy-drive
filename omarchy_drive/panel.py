"""Small JSON bridge between the upstream QML interface and our backend."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from .activity import detail_lines, safe
from .auth import Tokens
from .cli import status as backend_status
from .drive import API, BAD_NAME, Drive, FOLDER, GOOGLE_DOC, query_url, usable_name
from .http import HTTP
from .engine import MOUNT, SPECIAL, SYMLINK, TEMP_PREFIX, UNREADABLE, local_path
from .migrate import migrate
from . import home_folders
from .selection import includes, valid_path
from .storage import Paths, Store, DriveError, Blocked, Offline, atomic_json, read_json

ROOT = Path(__file__).resolve().parents[1]
SERVICE = 'omarchy-drive-sync.service'
TIMER = 'omarchy-drive-sync.timer'


def systemctl(*args, check=True):
    result = subprocess.run(['systemctl', '--user', *args], capture_output=True, text=True, timeout=35)
    if check and result.returncode:
        raise Blocked('Could not change the sync service. Install helpers with: bash ' + str(ROOT / 'install.sh'))
    return result


def disk_inventory(root, budget=1):
    """Bound local size estimates; no hashing or following symlinks in UI polls."""
    sizes, counts, deadline, approximate = {}, {}, time.monotonic() + budget, False
    if not root.is_dir() or root.is_symlink():
        return sizes, counts, approximate
    for directory, folders, files in os.walk(root, followlinks=False):
        folders[:] = [name for name in folders if not (Path(directory) / name).is_symlink()]
        relative = Path(directory).relative_to(root)
        group = relative.parts[0] if relative.parts else ''
        for name in files:
            path = Path(directory) / name
            if path.is_symlink() or name.startswith('.omarchy-drive-tmp-') or name == 'RCLONE_TEST':
                continue
            try:
                sizes[group] = sizes.get(group, 0) + path.stat().st_size
                counts[group] = counts.get(group, 0) + 1
            except OSError:
                approximate = True
            if time.monotonic() > deadline:
                return sizes, counts, True
    return sizes, counts, approximate


def drive_folder_error(name, paths):
    """Why a folder that is only on Drive cannot be ticked, or ''."""
    try:
        # Not ticked yet counts as an entry with no identity, as home_folders.select treats it.
        home_folders.validate(name, {}, paths)
    except (Blocked, OSError) as problem:
        if not name.startswith('.') and os.path.lexists(Path.home() / name):
            return f'~/{name} is not an ordinary folder, so this Drive folder cannot sync. Rename or move it first.'
        return str(problem)
    return ''


def folder_list(settings, paths, drive_names, available, old):
    ticked = settings.get('home_folders', {})
    rows = {row['name']: row for row in home_folders.choices(settings, paths)}
    for name in drive_names - rows.keys():
        rows[name] = dict(name=name, selected=False, error=drive_folder_error(name, paths))
    total, approximate = 0, False
    for name, row in rows.items():
        source = Path.home() / name
        at_home = source.is_dir() and not source.is_symlink()
        entry = ticked.get(name)
        if at_home:
            row['where'] = 'both' if name in drive_names else 'home'
        else:
            # A ticked folder with no identity was chosen from Drive and has not been created yet.
            never_local = isinstance(entry, dict) and entry.get('identity') is None
            row['where'] = 'drive' if name in drive_names or never_local else 'home'
        sizes, _, partial = disk_inventory(source, budget=0.15) if at_home and not row['error'] else ({}, {}, False)
        row.update(localBytes=sum(sizes.values()), approx=partial, error=safe(row['error']))
        if row['selected']:
            total += row['localBytes']
            approximate |= partial
    return dict(ok=True, updated=time.time(), driveAvailable=available,
                folders=sorted(rows.values(), key=lambda row: row['name'].casefold()),
                tickedCount=len(ticked), localBytes=total, localBytesApprox=approximate, lastError='',
                usedBytes=old.get('usedBytes', 0), quotaBytes=old.get('quotaBytes', 0), quotaKnown=old.get('quotaKnown', False))


def cache_is_current(cache, paths):
    """A read that started before the last settings change holds old ticks, however late it finished."""
    updated = cache.get('updated', 0)
    return time.time() - updated < 15 and updated >= paths.settings.stat().st_mtime


def folders(paths, refresh=True):
    started = time.time()
    settings = paths.configured()
    cache_file = paths.state / 'panel-cache.json'
    old = read_json(cache_file)
    if not refresh or cache_is_current(old, paths):
        return old
    store = Store(paths.state)
    try:
        try:
            drive = Drive(HTTP(Tokens(paths)), store, paths.state)
            root = drive.get(settings['folder_id'])
            if root.get('trashed') or root.get('mimeType') != FOLDER:
                raise Blocked('Restore the configured Drive folder before syncing.')
            names = {item['name'] for item in drive.children(settings['folder_id'])
                     if item['mimeType'] == FOLDER and usable_name(item['name'])}
        except Offline:
            return folder_list(settings, paths, set(), False, old)
        value = folder_list(settings, paths, names, True, old)
        try:
            quota, _ = drive.http.json('GET', query_url(API + 'about', fields='storageQuota'), retry=True)
            quota = quota.get('storageQuota', {})
            value.update(usedBytes=int(quota.get('usage', 0)), quotaBytes=int(quota.get('limit', 0)), quotaKnown='limit' in quota)
        except DriveError:
            pass  # folder_list already carried the last known values.
        value['updated'] = started
        atomic_json(cache_file, value)
        return value
    finally:
        store.close()


def local_details(entry):
    """Describe a directory entry without following it or reading it."""
    if entry.is_symlink():
        return dict(kind='unsupported', size=0, unsupported=SYMLINK)
    if entry.is_dir(follow_symlinks=False):
        if os.path.ismount(entry.path):
            return dict(kind='unsupported', size=0, unsupported=MOUNT)
        return dict(kind='dir', size=0, unsupported='' if os.access(entry.path, os.R_OK | os.X_OK) else UNREADABLE)
    if entry.is_file(follow_symlinks=False):
        return dict(kind='file', size=entry.stat(follow_symlinks=False).st_size,
                    unsupported='' if os.access(entry.path, os.R_OK) else UNREADABLE)
    return dict(kind='unsupported', size=0, unsupported=SPECIAL)


def drive_details(item):
    if not usable_name(item['name']):
        return dict(kind='unsupported', size=0, unsupported=BAD_NAME)
    if item['mimeType'] == FOLDER:
        return dict(kind='dir', size=0, unsupported='')
    if item['mimeType'].startswith('application/vnd.google-apps.'):
        return dict(kind='unsupported', size=0, unsupported=GOOGLE_DOC)
    return dict(kind='file', size=int(item.get('size', 0)), unsupported='')


def list_local(folder, path):
    try:
        with os.scandir(folder) as entries:
            return {entry.name: local_details(entry) for entry in entries if not entry.name.startswith(TEMP_PREFIX)}
    except OSError as error:
        raise Blocked(f'{path} could not be read. Fix its permissions or exclude it.') from error


def list_drive(paths, settings, path):
    """Children of the Drive folder at path by name, or None when no Drive folder has that path."""
    store = Store(paths.state)
    try:
        drive = Drive(HTTP(Tokens(paths)), store, paths.state)
        parent = settings['folder_id']
        # Names are compared here, never placed in a query string, so quotes in them need no escaping.
        for part in path.split('/'):
            parent = next((item['id'] for item in drive.children(parent)
                           if item['name'] == part and item['mimeType'] == FOLDER), None)
            if parent is None:
                return None
        return {item['name']: item for item in drive.children(parent)}
    finally:
        store.close()


def tree(paths, path):
    """One level of a home folder and its Drive partner. Hashes nothing and follows no symlinks."""
    settings = paths.configured()
    valid_path(path)
    first = path.partition('/')[0]
    if first.startswith('.'):
        raise Blocked('Hidden home folders cannot be synced. Choose a visible folder.')
    folder = local_path(Path.home(), path)
    present = os.path.lexists(folder)
    if present and not folder.is_dir():
        raise Blocked(f'{path} is not a folder.')
    excluded = settings.get('excluded', [])
    # Asks only about exclusions: an unticked folder can be given exclusions before it is ticked.
    inherited = not includes(path, {'home_folders': {first: {}}, 'excluded': excluded})
    local = list_local(folder, path) if present else {}
    try:
        remote, available = list_drive(paths, settings, path), True
    except Offline:
        remote, available = {}, False
    if remote is None:
        if not present:
            raise Blocked(f'{path} is not in your home folder or on Drive.')
        remote = {}
    rows = []
    for name in local.keys() | remote.keys():
        mine, theirs = local.get(name), drive_details(remote[name]) if name in remote else None
        # A problem on the local side decides the row; otherwise a Drive side that cannot sync does.
        details = theirs if mine is None or (theirs and theirs['unsupported'] and not mine['unsupported']) else mine
        rows.append(dict(details, name=name, path=f'{path}/{name}',
                         where='both' if mine and theirs else 'home' if mine else 'drive',
                         excluded=f'{path}/{name}' in excluded, inheritedExclusion=inherited,
                         # Sync skips these by itself, so there is nothing to choose.
                         excludable=not inherited and not details['unsupported']))
    rows.sort(key=lambda row: (row['name'].casefold(), row['name']))
    return dict(ok=True, path=path, driveAvailable=available, rows=rows)


def status(paths):
    view = backend_status(paths)
    settings = read_json(paths.settings)
    cache = read_json(paths.state / 'panel-cache.json')
    installed = (Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'systemd/user' / SERVICE).is_file()
    authenticated = bool(settings.get('folder_id') and paths.tokens.is_file() and view['kind'] != 'auth')
    kind = view['kind']
    syncing = kind == 'running'
    timer = systemctl('is-enabled', TIMER, check=False).stdout.strip() in ('enabled', 'enabled-runtime') if installed else False
    ticked_count = len(settings.get('home_folders', {}))
    if kind in ('ready', 'success') and not ticked_count:
        label, icon = 'No folders selected', 'ready'
    elif kind == 'success' and not timer:
        label, icon = 'Up to date · automatic sync paused', 'paused'
    else:
        label = {'running': 'Syncing', 'success': 'Up to date', 'ready': 'Ready to sync',
                 'auth': 'Sign in required', 'setup': 'Connect Google Drive', 'offline': 'Offline',
                 'blocked': 'Needs attention', 'interrupted': 'Sync interrupted'}.get(kind, 'Needs attention')
        icon = {'running': (view.get('current') or {}).get('direction', 'sync'),
                'success': 'success', 'auth': 'setup', 'setup': 'setup', 'offline': 'offline',
                'ready': 'ready'}.get(kind, 'error')
    current = view.get('current')
    if syncing and current:
        label = {'upload': 'Uploading', 'download': 'Downloading', 'backup': 'Backing up', 'prepare': 'Preparing'}.get(current['direction'], 'Syncing')
    return dict(ok=True, installed=installed, authenticated=authenticated, syncing=syncing,
        timerEnabled=timer, baseline=bool(view.get('last_success')), statusText=label, iconState=icon,
        cloudFolderName=settings.get('folder_name', 'Omarchy'),
        cloudUrl='https://drive.google.com/drive/folders/' + settings['folder_id'] if authenticated else 'https://drive.google.com/drive/my-drive',
        tickedCount=ticked_count, blockedPath=view.get('blockedPath', '') if kind == 'blocked' else '',
        localBytes=cache.get('localBytes', 0), localBytesApprox=cache.get('localBytesApprox', False),
        usedBytes=cache.get('usedBytes', 0), quotaBytes=cache.get('quotaBytes', 0), quotaKnown=cache.get('quotaKnown', False),
        lastResult='success' if kind == 'success' else 'error' if kind in ('blocked','offline','error','auth','interrupted') else '',
        lastFinishedTs=view.get('last_success', 0),
        warning='Sync scope: Drive/' + settings.get('folder_name', 'Omarchy') + '. Unselected files stay on disk.',
        lastError=view['message'] if kind in ('blocked','offline','error','auth','interrupted') else '',
        detailsTooltip=view['tooltip'].replace('Left: dashboard · Middle: sync now · Right: open folder', 'Left: panel · Right: refresh').replace('Automatic sync: enabled' if not timer else 'Automatic sync: paused', 'Automatic sync: paused' if not timer else 'Automatic sync: enabled'), activityText='\n'.join(detail_lines(view)),
        current=current, recent=view.get('recent', []), queue=view.get('queue', []), pending=view.get('pending', 0),
        checking=view.get('checking') if syncing else None, active=view.get('active') if syncing else None, total=view.get('total', 0), completed=view.get('completed', 0),
        queued=view.get('queued', 0),
        skipped=view.get('skipped', []), skippedCount=view.get('skippedCount', 0),
        syncIntervalMin=settings.get('sync_interval_min', 1))


def timer(paths, args):
    paths.configured()
    if not (Path.home() / '.local/bin/omarchy-drive-sync').exists():
        raise Blocked('Install scheduled-sync helpers first: bash ' + str(ROOT / 'install.sh'))
    if args.enable:
        interval = max(1, min(1440, args.interval))
        directory = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'systemd/user' / (TIMER + '.d')
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / 'interval.conf'
        path.write_text(f'[Timer]\nOnUnitInactiveSec=\nOnUnitInactiveSec={interval}min\n')
        systemctl('daemon-reload')
        systemctl('enable', '--now', TIMER)
        with paths.lock():
            settings = paths.configured()
            settings['sync_interval_min'] = interval
            atomic_json(paths.settings, settings)
    else:
        systemctl('disable', '--now', TIMER)
    value = read_json(paths.status)
    atomic_json(paths.status, dict(value, scheduled=args.enable))
    return {'ok': True, 'timerEnabled': args.enable}


def reveal(paths, relative):
    """Reveal a containing directory only; never launch a downloaded file."""
    first, _, rest = relative.partition('/')
    root = home_folders.mappings(paths.configured(), paths).get(first)
    if root is None:
        raise Blocked('This path is not inside a ticked home folder.')
    path = local_path(root, rest) if rest else root
    directory = path if path == root else path.parent
    while directory != root and not directory.is_dir():
        directory = directory.parent
    if not directory.is_dir():
        raise Blocked('The containing folder is unavailable.')
    result = subprocess.run(['xdg-open', str(directory)], capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise Blocked('Could not open the containing folder. Check your default file manager.')
    return {'ok': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Standalone Drive panel backend')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('status', 'folders', 'sync', 'open-backups'):
        commands.add_parser(name)
    for name in ('reveal', 'exclude', 'include', 'tree'):
        commands.add_parser(name).add_argument('--path', required=True)
    selection = commands.add_parser('select')
    selection.add_argument('--name', required=True)
    enabled = selection.add_mutually_exclusive_group(required=True)
    enabled.add_argument('--enable', action='store_true')
    enabled.add_argument('--disable', dest='enable', action='store_false')
    schedule = commands.add_parser('timer')
    switch = schedule.add_mutually_exclusive_group(required=True)
    switch.add_argument('--enable', action='store_true')
    switch.add_argument('--disable', dest='enable', action='store_false')
    schedule.add_argument('--interval', type=int, default=1)
    args = parser.parse_args(argv)
    paths = Paths()
    paths.prepare()
    try:
        # Backups stay reachable while a stopped migration waits for the user. Opening them reads no settings.
        if args.command != 'open-backups':
            migrate(paths)
        if args.command == 'status':
            result = status(paths)
        elif args.command == 'folders':
            result = folders(paths)
        elif args.command == 'open-backups':
            directory = paths.backups
            if directory.is_symlink():
                raise Blocked('The backup directory cannot be a symbolic link.')
            directory.mkdir(exist_ok=True, mode=0o700)
            directory.chmod(0o700)
            subprocess.run(['xdg-open', str(directory)], check=False)
            result = {'ok': True}
        elif args.command == 'reveal':
            result = reveal(paths, args.path)
        elif args.command == 'select':
            result = home_folders.select(paths, args.name, args.enable)
        elif args.command in ('exclude', 'include'):
            result = home_folders.set_excluded(paths, args.path, args.command == 'exclude')
        elif args.command == 'tree':
            result = tree(paths, args.path)
        elif args.command == 'timer':
            result = timer(paths, args)
        else:
            paths.configured()
            if not (Path.home() / '.local/bin/omarchy-drive-sync').exists():
                raise Blocked('Install helpers first: bash ' + str(ROOT / 'install.sh'))
            systemctl('start', '--no-block', SERVICE)
            result = {'ok': True}
        print(json.dumps(result))
        return 0
    except (DriveError, OSError, sqlite3.Error, subprocess.TimeoutExpired, ValueError) as error:
        message = safe(error) if isinstance(error, DriveError) else 'Could not complete the local Drive operation.'
        print(json.dumps({'ok': False, 'lastError': message}))
        print(message, file=sys.stderr)
        return 1
