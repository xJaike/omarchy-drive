"""One-time move from the single local_dir layout to ticked home folders."""
import json
import os
from pathlib import Path
import tempfile

from .engine import IGNORED, TEMP_PREFIX
from .storage import Blocked, Store, atomic_json, read_json

OLD_KEYS = ('local_dir', 'selected_folders', 'root_files')


def old_identity(value):
    """The folder ID in an old-format [path, dev, ino, folder_id] row, else None."""
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed[3] if isinstance(parsed, list) and len(parsed) == 4 else None


def leftovers(local):
    try:
        names = os.listdir(local)
    except FileNotFoundError:
        return []
    return sorted(name for name in names if name not in IGNORED and not name.startswith(TEMP_PREFIX))


def keep(path, text):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def migrate(paths):
    """Return whether anything changed. Stops with Blocked, changing nothing, until it is safe."""
    if 'local_dir' not in read_json(paths.settings):
        return False
    with paths.lock():
        settings = read_json(paths.settings)
        if 'local_dir' not in settings:
            return False
        folder_id = settings.get('folder_id')
        store = Store(paths.state)
        try:
            stored = store.stored_identity()
            previous = old_identity(stored)
            if stored is not None and folder_id not in (stored, previous):
                raise Blocked('The sync history belongs to a different Drive folder. Nothing was changed.')
            local = Path(settings['local_dir']).expanduser()
            found = leftovers(local)
            if found:
                shown = ', '.join(found[:5]) + (' and more' if len(found) > 5 else '')
                home = Path.home()
                try:
                    where = '~/' + str(local.relative_to(home))
                except ValueError:
                    where = str(local)
                raise Blocked(f'Move these out of {where} into your home folder, then tick them: {shown}')
            backup = paths.settings.with_name(paths.settings.name + '.pre-migration')
            if not backup.exists():
                atomic_json(backup, settings)
            saved = backup.with_name('identity.pre-migration')
            if previous is not None and not saved.exists():
                keep(saved, stored)
            if previous is not None:
                store.replace_identity(folder_id)
            atomic_json(paths.settings, {k: v for k, v in settings.items() if k not in OLD_KEYS})
        finally:
            store.close()
        return True
