"""Selection is exclusion, never deletion. Re-enabling starts a merging baseline."""
from .storage import Blocked

IGNORED = {'RCLONE_TEST'}  # Legacy access marker; it is never needed by this engine.


def in_subtree(path, root):
    return path == root or path.startswith(root + '/')


def under_any(path, roots):
    """Whether path is one of roots or lies beneath one. Walks the path, so the cost does not grow with roots."""
    parts = path.split('/')
    return any('/'.join(parts[:depth]) in roots for depth in range(1, len(parts) + 1))


def includes(path, settings):
    if path.partition('/')[0] not in settings.get('home_folders', {}):
        return False
    for excluded in settings.get('excluded', []):
        if not isinstance(excluded, str):
            raise Blocked('Your folder settings contain an invalid exclusion. Restore settings.json.')
        if in_subtree(path, excluded):
            return False
    return True


def valid_path(value):
    if not isinstance(value, str) or not value or value.startswith('/') or '\x00' in value \
            or any(part in ('', '.', '..') for part in value.split('/')):
        raise Blocked('Select a folder or file inside a synced home folder.')
    return value


def folder_name(value):
    if value in ('', '.', '..') or '/' in value or '\x00' in value:
        raise Blocked('Select a single top-level folder name.')
    return value
