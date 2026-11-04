"""Explicit home-folder mappings; no symlinks and no implicit home scan."""
from pathlib import Path
from .selection import folder_name
from .storage import Blocked, Store, atomic_json


def validate(root, name, entry):
    folder_name(name)
    home = Path.home()
    source = home / name
    if name.startswith('.') or source == root or root.is_relative_to(source) or source.is_relative_to(root):
        raise Blocked('Choose a visible home folder outside the existing sync folder.')
    if home.is_symlink() or source.is_symlink() or not source.is_dir() or source.is_mount():
        raise Blocked(f'Restore ~/{name} as an ordinary folder before syncing.')
    if (root / name).exists() or (root / name).is_symlink():
        raise Blocked(f'{name} already exists in the sync folder. Resolve that overlap before linking it.')
    info = source.stat()
    if info.st_dev != root.stat().st_dev:
        raise Blocked('Home folders must be on the same filesystem as the sync folder for safe backups.')
    if entry is not None and [info.st_dev, info.st_ino] != entry.get('identity'):
        raise Blocked(f'~/{name} was replaced. Deselect and select it again to merge safely.')
    return source


def mappings(settings):
    root = Path(settings['local_dir'])
    return {name: validate(root, name, entry) for name, entry in settings.get('home_folders', {}).items()}


def choices(settings):
    root = Path(settings['local_dir'])
    selected = settings.get('home_folders', {})
    names = set(selected)
    for path in Path.home().iterdir():
        if not path.name.startswith('.') and path.is_dir() and not path.is_symlink():
            names.add(path.name)
    rows = []
    for name in sorted(names, key=str.casefold):
        error = ''
        try:
            validate(root, name, selected.get(name))
        except (Blocked, OSError) as problem:
            error = str(problem)
        if name in selected or not error:
            rows.append(dict(name=name, selected=name in selected, error=error))
    return rows


def select(paths, name, enabled):
    folder_name(name)
    with paths.lock():
        settings = paths.configured()
        selected = dict(settings.get('home_folders', {}))
        if enabled:
            source = validate(Path(settings['local_dir']), name, selected.get(name))
            info = source.stat()
            selected[name] = {'identity': [info.st_dev, info.st_ino]}
        else:
            selected.pop(name, None)
        # Use an explicit selection from now on so deselection can never turn
        # the old cloud folder into a new download in ~/GoogleDrive.
        active = settings.get('selected_folders')
        if active is None:
            from .storage import read_json
            cache = read_json(paths.state / 'panel-cache.json')
            if not cache.get('updated'):
                raise Blocked('Refresh the Drive folder list before selecting home folders.')
            active = {row['name'] for row in cache.get('folders', [])}
            active |= {p.name for p in Path(settings['local_dir']).iterdir() if p.is_dir() and not p.is_symlink()}
        active = set(active)
        (active.add if enabled else active.discard)(name)
        settings.update(home_folders=selected, selected_folders=sorted(active))
        store = Store(paths.state)
        try:
            # Every mapping change starts a merge, including re-selection.
            for path in store.baseline():
                if path == name or path.startswith(name + '/'):
                    store.remember(path, None)
        finally:
            store.close()
        atomic_json(paths.settings, settings)
        (paths.state / 'panel-cache.json').unlink(missing_ok=True)
    return {'ok': True}
