"""Selection is exclusion, never deletion. Re-enabling starts a merging baseline."""
from .storage import Blocked


def includes(path, entry, settings):
    selected = settings.get('selected_folders')
    first, separator, _ = path.partition('/')
    if separator or entry.get('kind') == 'dir' or selected is not None and first in selected:
        return selected is None or first in selected
    return settings.get('root_files', True)


def folder_name(value):
    if value in ('', '.', '..') or '/' in value or '\x00' in value:
        raise Blocked('Select a single top-level folder name.')
    return value
