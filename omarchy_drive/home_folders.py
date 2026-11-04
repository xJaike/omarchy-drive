"""Ticked home folders and exclusions; no symlinks and no implicit home scan."""
import os
from pathlib import Path
from .selection import IGNORED, folder_name, valid_path
from .storage import Blocked, Store, atomic_json, read_json


def validate(name, entry, state):
    """Check ~/name is safe to pair. entry is None when unticked; a ticked entry with no identity may be missing."""
    folder_name(name)
    if name.startswith('.'):
        raise Blocked('Hidden home folders cannot be synced. Choose a visible folder.')
    if name in IGNORED:
        raise Blocked(f'~/{name} is a name Omarchy Drive reserves, so it cannot sync. Choose another folder.')
    if entry is not None and not isinstance(entry, dict):
        raise Blocked(f'Your folder settings for {name} are invalid. Restore settings.json.')
    identity = entry.get('identity') if entry else None
    home = Path.home()
    source = home / name
    drive_only = entry is not None and identity is None and not os.path.lexists(source)
    if not drive_only and not os.path.lexists(source):
        raise Blocked(f'~/{name} is missing. Restore it, or untick it to stop syncing.')
    if home.is_symlink() or not drive_only and (source.is_symlink() or not source.is_dir() or source.is_mount()):
        raise Blocked(f'~/{name} is not an ordinary folder. Untick it, or restore it as a folder.')
    info = (home if drive_only else source).stat()
    # archive_source moves originals into the backup directory with os.rename.
    if info.st_dev != Path(state).stat().st_dev:
        raise Blocked('Home folders must be on the same disk as the app state folder, because backups move files there.')
    if identity is not None and [info.st_dev, info.st_ino] != identity:
        raise Blocked(f'~/{name} was replaced. Untick it and tick it again to merge safely.')
    return source


def mappings(settings, paths):
    return {name: validate(name, entry, paths.state) for name, entry in settings.get('home_folders', {}).items()}


def choices(settings, paths):
    selected = settings.get('home_folders', {})
    names = set(selected)
    for path in Path.home().iterdir():
        if not path.name.startswith('.') and path.is_dir() and not path.is_symlink():
            names.add(path.name)
    rows = []
    for name in sorted(names, key=str.casefold):
        error = ''
        try:
            validate(name, selected.get(name), paths.state)
        except (Blocked, OSError) as problem:
            error = str(problem)
        rows.append(dict(name=name, selected=name in selected, error=error))
    return rows


def record_identity(settings, name, source):
    info = Path(source).stat()
    settings.setdefault('home_folders', {})[name] = {'identity': [info.st_dev, info.st_ino]}


def listed_only_on_drive(paths, name):
    rows = read_json(paths.state / 'panel-cache.json').get('folders', [])
    return any(row.get('name') == name and row.get('where') == 'drive' for row in rows)


def save_selection(paths, settings, path):
    """Clear the baseline before saving, so a crash leads to a merge, never a deletion."""
    store = Store(paths.state)
    try:
        store.forget_subtree(path)
    finally:
        store.close()
    atomic_json(paths.settings, settings)
    (paths.state / 'panel-cache.json').unlink(missing_ok=True)


def select(paths, name, enabled):
    folder_name(name)
    with paths.lock():
        settings = paths.configured()
        selected = settings.setdefault('home_folders', {})
        if enabled:
            # Not ticked yet counts as an entry with no identity, so a Drive-only folder can pass;
            # a ticked folder that went missing still fails its own identity check.
            source = validate(name, selected.get(name, {}), paths.state)
            if source.exists():
                record_identity(settings, name, source)
            elif listed_only_on_drive(paths, name):
                selected[name] = {'identity': None}
            else:
                raise Blocked('Refresh the folder list, then tick this Drive folder again.')
        else:
            selected.pop(name, None)
        save_selection(paths, settings, name)
    return {'ok': True}


def set_excluded(paths, path, excluded):
    valid_path(path)
    if '/' not in path:
        raise Blocked('To stop syncing a whole folder, untick it.')
    with paths.lock():
        settings = paths.configured()
        entries = settings.get('excluded', [])
        if not all(isinstance(entry, str) for entry in entries):
            raise Blocked('Your folder settings contain an invalid exclusion. Restore settings.json.')
        entries = set(entries)
        (entries.add if excluded else entries.discard)(path)
        settings['excluded'] = sorted(entries)
        save_selection(paths, settings, path)
    return {'ok': True}
