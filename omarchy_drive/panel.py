"""Small JSON bridge between the upstream QML interface and our backend."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from .activity import detail_lines
from .auth import Tokens
from .cli import status as backend_status
from .drive import API, Drive, FOLDER, component, query_url
from .http import HTTP
from .engine import local_path
from . import home_folders
from .selection import folder_name
from .storage import Paths, Store, DriveError, Blocked, atomic_json, read_json

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


def folders(paths, refresh=True):
    settings = paths.configured()
    cache_file = paths.state / 'panel-cache.json'
    old = read_json(cache_file)
    if not refresh or time.time() - old.get('updated', 0) < 15:
        return old
    store = Store(paths.state)
    try:
        drive = Drive(HTTP(Tokens(paths)), store, paths.state)
        root = drive.get(settings['folder_id'])
        if root.get('trashed') or root.get('mimeType') != FOLDER:
            raise Blocked('Restore the configured Drive folder before syncing.')
        items = list(drive.children(settings['folder_id']))
        names = [component(item['name']) for item in items]
        if len(names) != len(set(names)):
            raise Blocked('Duplicate names in the Drive folder. Rename duplicates before syncing.')
        local = Path(settings['local_dir'])
        linked = home_folders.mappings(settings)
        sizes, counts, approximate = disk_inventory(local)
        for name, source in linked.items():
            linked_sizes, linked_counts, partial = disk_inventory(source, budget=0.15)
            sizes[name] = sum(linked_sizes.values())
            counts[name] = sum(linked_counts.values())
            approximate |= partial
        selected = settings.get('selected_folders')
        rows = []
        cloud_names = set()
        for item in items:
            if item['mimeType'] != FOLDER:
                continue
            name = item['name']
            cloud_names.add(name)
            active = selected is None or name in selected
            rows.append(dict(id=item['id'], name=name, selected=active, localBytes=sizes.get(name, 0),
                             onDisk=(local / name).is_dir() or name in linked, stale=not active and (local / name).exists(),
                             missing=False, approx=approximate))
        for path in local.iterdir() if local.is_dir() else []:
            if path.is_dir() and not path.is_symlink() and path.name not in cloud_names:
                active = selected is None or path.name in selected
                rows.append(dict(name=path.name, selected=active, localBytes=sizes.get(path.name, 0),
                                 onDisk=True, stale=not active, missing=True, approx=approximate))
        for name in linked.keys() - cloud_names:
            rows.append(dict(name=name, selected=True, localBytes=sizes.get(name, 0), onDisk=True,
                             stale=False, missing=True, approx=approximate))
        root_items = [item for item in items if not item['mimeType'].startswith('application/vnd.google-apps.') and item['name'] != 'RCLONE_TEST']
        stale = [item for item in rows if item['stale']]
        value = dict(ok=True, updated=time.time(), folders=sorted(rows, key=lambda r: r['name'].casefold()),
            rootFiles=settings.get('root_files', True), rootFileCount=len(root_items),
            rootFileBytes=sum(int(item.get('size', 0)) for item in root_items),
            rootLocalBytes=sizes.get('', 0), rootLocalCount=counts.get('', 0),
            localBytes=sum(sizes.values()), localBytesApprox=approximate,
            staleBytes=sum(item['localBytes'] for item in stale), staleCount=len(stale),
            lastError='', selectedCount=sum(item['selected'] for item in rows))
        if not settings.get('root_files', True):
            value['staleBytes'] += sizes.get('', 0)
            value['staleCount'] += counts.get('', 0)
        try:
            quota, _ = drive.http.json('GET', query_url(API + 'about', fields='storageQuota'), retry=True)
            quota = quota.get('storageQuota', {})
            value.update(usedBytes=int(quota.get('usage', 0)), quotaBytes=int(quota.get('limit', 0)), quotaKnown='limit' in quota)
        except DriveError:
            for key in ('usedBytes', 'quotaBytes', 'quotaKnown'):
                value[key] = old.get(key, False if key == 'quotaKnown' else 0)
        atomic_json(cache_file, value)
        return value
    finally:
        store.close()


def status(paths):
    view = backend_status(paths)
    settings = read_json(paths.settings)
    cache = read_json(paths.state / 'panel-cache.json')
    installed = (Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'systemd/user' / SERVICE).is_file()
    authenticated = bool(settings.get('folder_id') and paths.tokens.is_file() and view['kind'] != 'auth')
    kind = view['kind']
    syncing = kind == 'running'
    timer = systemctl('is-enabled', TIMER, check=False).stdout.strip() in ('enabled', 'enabled-runtime') if installed else False
    if kind == 'success' and not timer:
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
    selected = settings.get('selected_folders')
    rows = cache.get('folders', [])
    selected_count = len(selected) if selected is not None else len(rows)
    return dict(ok=True, installed=installed, authenticated=authenticated, syncing=syncing,
        timerEnabled=timer, baseline=bool(view.get('last_success')), statusText=label, iconState=icon,
        folderPath=settings.get('local_dir', str(Path.home() / 'GoogleDrive')),
        cloudFolderName=settings.get('folder_name', 'Omarchy'),
        cloudUrl='https://drive.google.com/drive/folders/' + settings['folder_id'] if authenticated else 'https://drive.google.com/drive/my-drive',
        selectedCount=selected_count, rootFiles=settings.get('root_files', True),
        localBytes=cache.get('localBytes', 0), localBytesApprox=cache.get('localBytesApprox', False),
        usedBytes=cache.get('usedBytes', 0), quotaBytes=cache.get('quotaBytes', 0), quotaKnown=cache.get('quotaKnown', False),
        lastResult='success' if kind == 'success' else 'error' if kind in ('blocked','offline','error','auth','interrupted') else '',
        lastFinishedTs=view.get('last_success', 0),
        warning='Sync scope: Drive/' + settings.get('folder_name', 'Omarchy') + '. Unselected files stay on disk.',
        lastError=view['message'] if kind in ('blocked','offline','error','auth','interrupted') else '',
        detailsTooltip=view['tooltip'].replace('Left: dashboard · Middle: sync now · Right: open folder', 'Left: panel · Right: refresh').replace('Automatic sync: enabled' if not timer else 'Automatic sync: paused', 'Automatic sync: paused' if not timer else 'Automatic sync: enabled'), activityText='\n'.join(detail_lines(view)),
        homeFolders=home_folders.choices(settings) if authenticated else [], current=current, recent=view.get('recent', []), queue=view.get('queue', []), pending=view.get('pending', 0),
        syncIntervalMin=settings.get('sync_interval_min', 1))


def select(paths, args):
    if args.remove and args.remove in paths.configured().get('home_folders', {}):
        return home_folders.select(paths, args.remove, False)
    with paths.lock():
        settings = paths.configured()
        cache = read_json(paths.state / 'panel-cache.json')
        selected = settings.get('selected_folders')
        if args.add or args.remove:
            if selected is None:
                if not cache.get('updated'):
                    raise Blocked('Refresh the folder list before changing selection.')
                local = Path(settings['local_dir'])
                selected = sorted({row['name'] for row in cache.get('folders', [])} |
                                  {p.name for p in local.iterdir() if p.is_dir() and not p.is_symlink()})
            selected = set(selected)
            if args.add:
                selected.add(folder_name(args.add))
            if args.remove:
                selected.discard(folder_name(args.remove))
            settings['selected_folders'] = sorted(selected)
        if args.root_files is not None:
            settings['root_files'] = args.root_files
        # Reset only the affected baselines; re-selection merges existing files
        # rather than propagating deletions that happened while excluded.
        store = Store(paths.state)
        try:
            for path in store.baseline():
                if args.remove and (path == args.remove or path.startswith(args.remove + '/')) or args.root_files is False and '/' not in path:
                    store.remember(path, None)
        finally:
            store.close()
        atomic_json(paths.settings, settings)
        (paths.state / 'panel-cache.json').unlink(missing_ok=True)
    return {'ok': True}


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
    home = commands.add_parser('home-select')
    home.add_argument('--name', required=True)
    enabled = home.add_mutually_exclusive_group(required=True)
    enabled.add_argument('--enable', action='store_true')
    enabled.add_argument('--disable', action='store_true')
    show = commands.add_parser('reveal')
    show.add_argument('--path', required=True)
    selection = commands.add_parser('select')
    change = selection.add_mutually_exclusive_group()
    change.add_argument('--add')
    change.add_argument('--remove')
    roots = selection.add_mutually_exclusive_group()
    roots.add_argument('--root-files', dest='root_files', action='store_true')
    roots.add_argument('--no-root-files', dest='root_files', action='store_false')
    selection.set_defaults(root_files=None)
    schedule = commands.add_parser('timer')
    switch = schedule.add_mutually_exclusive_group(required=True)
    switch.add_argument('--enable', action='store_true')
    switch.add_argument('--disable', dest='enable', action='store_false')
    schedule.add_argument('--interval', type=int, default=1)
    args = parser.parse_args(argv)
    paths = Paths()
    paths.prepare()
    try:
        if args.command == 'status':
            result = status(paths)
        elif args.command == 'folders':
            result = folders(paths)
        elif args.command == 'open-backups':
            settings = paths.configured()
            local = Path(settings['local_dir'])
            directory = local.parent / ('.' + local.name + '-omarchy-drive-backups')
            if directory.is_symlink():
                raise Blocked('The backup directory cannot be a symbolic link.')
            directory.mkdir(exist_ok=True, mode=0o700)
            subprocess.run(['xdg-open', str(directory)], check=False)
            result = {'ok': True}
        elif args.command == 'home-select':
            result = home_folders.select(paths, args.name, args.enable)
        elif args.command == 'reveal':
            result = reveal(paths, args.path)
        elif args.command == 'select':
            result = select(paths, args)
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
        message = str(error) if isinstance(error, DriveError) else 'Could not complete the local Drive operation.'
        print(json.dumps({'ok': False, 'lastError': message}))
        print(message, file=sys.stderr)
        return 1
