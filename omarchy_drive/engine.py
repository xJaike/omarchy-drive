"""Content-based two-way reconciliation, independent of any third-party sync tool."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import time
import uuid
from .drive import digest
from .activity import Activity
from .selection import includes
from .home_folders import mappings
from .storage import Blocked, atomic_json

IGNORED = {'RCLONE_TEST'}  # Legacy access marker; it is never needed by this engine.
TEMP_PREFIX = '.omarchy-drive-tmp-'


def sync_directories(directory, stop):
    """Persist published names before committing the corresponding baseline."""
    directory, stop = Path(directory), Path(stop)
    while True:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        if directory == stop:
            return
        directory.relative_to(stop)
        directory = directory.parent


def local_path(root, relative):
    parts = PurePosixPath(relative).parts
    if not parts or any(x in ('', '.', '..') for x in parts) or PurePosixPath(relative).is_absolute():
        raise Blocked('Unsafe sync path.')
    path = Path(root)
    if path.is_symlink():
        raise Blocked('The sync folder cannot be a symbolic link.')
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise Blocked(f'Symbolic links are not supported: {relative}')
    return path


def scan_local(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir() or os.path.ismount(root):
        raise Blocked('The local sync folder must exist as an ordinary local directory. Restore it before syncing.')
    result = {}
    def scan_error(error):
        raise Blocked('A local folder could not be read. No changes were applied.') from error
    for directory, folders, files in os.walk(root, followlinks=False, onerror=scan_error):
        for name in list(folders) + files:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if name.startswith(TEMP_PREFIX) or relative in IGNORED:
                if name in folders:
                    folders.remove(name)
                continue
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise Blocked(f'Unsupported local file type: {relative}. Move it outside the sync folder.')
            if stat.S_ISDIR(info.st_mode):
                if os.path.ismount(path):
                    raise Blocked(f'A nested mount is not supported: {relative}')
                result[relative] = {'kind': 'dir'}
            else:
                checksum = digest(path)
                after = path.stat()
                if (info.st_size, info.st_mtime_ns, info.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                    raise Blocked(f'A local file changed during scanning: {relative}. Retry sync.')
                result[relative] = dict(kind='file', hash=checksum, size=info.st_size)
    return result


@dataclass(frozen=True)
class Action:
    kind: str
    path: str
    note: str = ''


def plan(local, remote, baseline, max_deletes=20, allow_many_deletes=False):
    actions = []
    for path in sorted(set(local) | set(remote) | set(baseline), key=lambda p: (p.count('/'), p)):
        if path in IGNORED:
            continue
        left, right, prior = local.get(path), remote.get(path), baseline.get(path)
        if right and right['kind'] == 'unsupported':
            if left:
                raise Blocked(f'A Google document or shortcut conflicts with a local path: {path}')
            continue
        if left and right and left['kind'] != right['kind']:
            raise Blocked(f'File/folder type conflict: {path}. Rename one side before syncing.')
        if left and left['kind'] == 'dir' or right and right['kind'] == 'dir':
            if prior:
                raise Blocked(f'A tracked file became a folder: {path}')
            if not left:
                actions.append(Action('mkdir-local', path))
            elif not right:
                actions.append(Action('mkdir-remote', path))
            continue
        if left and right:
            if left['hash'] == right['hash']:
                actions.append(Action('remember', path))
            elif not prior:
                actions.append(Action('conflict', path, 'Keep both existing versions'))
            else:
                local_changed = left['hash'] != prior['hash']
                remote_changed = right['hash'] != prior['hash'] or right['id'] != prior['id']
                if local_changed and not remote_changed:
                    actions.append(Action('upload', path))
                elif remote_changed and not local_changed:
                    actions.append(Action('download', path))
                else:
                    actions.append(Action('conflict', path, 'Both sides changed; keep both versions'))
        elif left:
            if prior and left['hash'] == prior['hash']:
                actions.append(Action('archive-local', path, 'Drive deletion; move local copy to backups'))
            else:
                actions.append(Action('upload', path, 'Preserve local edits' if prior else 'New local file'))
        elif right:
            if prior and right['hash'] == prior['hash'] and right['id'] == prior['id']:
                actions.append(Action('trash-remote', path, 'Local deletion; move Drive copy to Trash'))
            else:
                actions.append(Action('download', path, 'Preserve cloud edits' if prior else 'New Drive file'))
        elif prior:
            actions.append(Action('forget', path))
    deletions = sum(a.kind in ('archive-local', 'trash-remote') for a in actions)
    if not allow_many_deletes and (deletions > max_deletes or len(baseline) >= 20 and deletions / len(baseline) > .25):
        raise Blocked(f'{deletions} deletions need review. Run omarchy-drive preview, then use sync --allow-many-deletes only if intended.')
    # Make new content durable before processing removals. Parent directories first.
    priorities = {'mkdir-local': 0, 'mkdir-remote': 0, 'remember': 1, 'upload': 2,
                  'download': 2, 'conflict': 2, 'archive-local': 3, 'trash-remote': 3, 'forget': 4}
    return sorted(actions, key=lambda a: (priorities[a.kind], a.path.count('/'), a.path))


class Engine:
    def __init__(self, paths, settings, store, drive, activity=None):
        self.paths, self.settings, self.store, self.drive = paths, settings, store, drive
        self.root = Path(settings['local_dir'])
        self.archive_root = self.root.parent / ('.' + self.root.name + '-omarchy-drive-backups')
        self.run_id = time.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]
        self.remote, self.local = {}, {}
        self.activity = activity or Activity(paths)
        self.drive.progress = self.activity.transfer

    def local_target(self, path):
        first, _, rest = path.partition('/')
        source = mappings(self.settings).get(first)
        if source is not None:
            return local_path(source, rest) if rest else source
        return local_path(self.root, path)

    def local_stop(self, path):
        first = path.partition('/')[0]
        source = mappings(self.settings).get(first)
        return source.parent if source is not None else self.root.parent

    def snapshot(self, allow_many_deletes=False):
        sources = mappings(self.settings)
        self.local = scan_local(self.root)
        for name, source in sources.items():
            self.local[name] = {'kind': 'dir'}
            self.local.update({name + '/' + path: item for path, item in scan_local(source).items()})
        # Build complete listings before any local or cloud mutation.
        # Only recurse into selected remote directories. Local scanning remains
        # complete so unsupported file types cannot masquerade as deletions.
        self.remote = self.drive.scan(self.settings['folder_id'],
            selected=self.settings.get('selected_folders'), root_files=self.settings.get('root_files', True))
        self.local = {p: item for p, item in self.local.items() if includes(p, item, self.settings)}
        info = self.root.stat()
        self.store.identity(json.dumps([str(self.root.resolve()), info.st_dev, info.st_ino, self.settings['folder_id']]))
        baseline = {p: item for p, item in self.store.baseline().items() if includes(p, item, self.settings)}
        return plan(self.local, self.remote, baseline, self.settings.get('max_deletes', 20), allow_many_deletes)

    def checked_local(self, path, expected):
        target = self.local_target(path)
        if expected is None:
            if target.exists():
                raise Blocked(f'A local file appeared during sync: {path}. Retry sync.')
        elif not target.is_file() or digest(target) != expected['hash']:
            raise Blocked(f'A local file changed during sync: {path}. Retry sync.')
        return target

    def staging(self, path, expected):
        source = self.checked_local(path, expected)
        directory = self.paths.state / 'staging'
        directory.mkdir(exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(fd, 'wb') as output, source.open('rb') as stream:
                shutil.copyfileobj(stream, output, 1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            if digest(name) != expected['hash']:
                raise Blocked(f'A local file changed while preparing upload: {path}')
            self.checked_local(path, expected)
            return Path(name)
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise

    def backup(self, path, source, expected_hash):
        if self.archive_root.is_symlink():
            raise Blocked('The backup directory must not be a symbolic link.')
        self.archive_root.mkdir(exist_ok=True, mode=0o700)
        self.archive_root.chmod(0o700)
        target = local_path(self.archive_root, self.run_id + '/' + uuid.uuid4().hex + '/' + path)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(source, target)
        if digest(target) != expected_hash:
            raise Blocked(f'Could not verify the backup for {path}. Original retained.')
        with target.open('rb') as stream:
            os.fsync(stream.fileno())
        sync_directories(target.parent, self.archive_root.parent)
        return target

    def archive_source(self, path, expected):
        target = self.checked_local(path, expected)
        backup = self.backup(path, target, expected['hash'])
        self.checked_local(path, expected)
        original = backup.with_name(backup.name + '.original')
        # Both locations are siblings of the sync root on the same filesystem.
        # Retain the actual inode too, including writes racing this rename.
        os.rename(target, original)
        sync_directories(original.parent, self.archive_root.parent)
        sync_directories(target.parent, self.local_stop(path))
        if digest(original) != expected['hash']:
            try:
                os.link(original, target)
            except FileExistsError:
                pass
            raise Blocked(f'A local edit raced sync: {path}. It was retained in the local folder or backups.')
        return original

    def remember(self, path, item):
        self.store.remember(path, {'id': item['id'], 'hash': item['hash']})

    def parent_id(self, path):
        parent = str(PurePosixPath(path).parent)
        return self.settings['folder_id'] if parent == '.' else self.remote[parent]['id']

    def download(self, path, item, expected):
        self.activity.phase('download', path, item['size'])
        key = hashlib.sha256((item['id'] + ':' + item['hash']).encode()).hexdigest()
        partial = self.paths.state / 'downloads' / (key + '.part')
        self.drive.download(item, partial)
        target = self.checked_local(path, expected)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=TEMP_PREFIX)
        try:
            with os.fdopen(fd, 'wb') as output, partial.open('rb') as stream:
                shutil.copyfileobj(stream, output, 1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            self.checked_local(path, expected)
            if expected:
                self.archive_source(path, expected)
            try:
                # Publish complete data exclusively; a newly appeared local file
                # must never be overwritten after archiving the old original.
                os.link(temporary, target)
            except FileExistsError as error:
                raise Blocked(f'A local file appeared during download: {path}. Both versions were retained.') from error
            sync_directories(target.parent, self.local_stop(path))
            self.remember(path, item)
            self.local[path] = dict(kind='file', hash=item['hash'], size=item['size'])
            partial.unlink(missing_ok=True)
            self.activity.file_done('download', path, item['size'])
        finally:
            Path(temporary).unlink(missing_ok=True)

    def upload(self, path, expected, existing):
        self.activity.phase('prepare', path, expected['size'], 'Preparing upload')
        staged = self.staging(path, expected)
        try:
            if existing:
                # Preserve the cloud version independently of Drive revision retention.
                key = hashlib.sha256((existing['id'] + existing['hash']).encode()).hexdigest()
                old = self.paths.state / 'downloads' / (key + '.part')
                self.activity.phase('backup', path, existing['size'])
                self.drive.download(existing, old)
                self.backup(path + '.cloud-before-upload', old, existing['hash'])
                old.unlink(missing_ok=True)
            self.activity.phase('upload', path, expected['size'])
            item = self.drive.upload(staged, self.parent_id(path), PurePosixPath(path).name, expected['hash'], existing)
            self.remote[path] = item
            # If the user edited during upload, the next run detects that edit.
            self.remember(path, item)
            self.activity.file_done('upload', path, expected['size'])
        finally:
            staged.unlink(missing_ok=True)

    def conflict(self, path, left, right):
        original = PurePosixPath(path)
        name = original.stem + '.conflict-local-' + left['hash'][:12] + original.suffix
        conflict = str(original.with_name(name))
        target = self.local_target(conflict)
        remote_copy = self.remote.get(conflict)
        if target.exists() and (not target.is_file() or digest(target) != left['hash']):
            raise Blocked(f'The conflict-copy name is already occupied: {conflict}')
        if remote_copy and (remote_copy['kind'] != 'file' or remote_copy['hash'] != left['hash']):
            raise Blocked(f'The Drive conflict-copy name is occupied: {conflict}')
        if not target.exists():
            staged = self.staging(path, left)
            fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=TEMP_PREFIX)
            try:
                with os.fdopen(fd, 'wb') as output, staged.open('rb') as source:
                    shutil.copyfileobj(source, output)
                    output.flush()
                    os.fsync(output.fileno())
                # Link publishes a complete copy and refuses to overwrite a new file.
                os.link(temporary, target)
                sync_directories(target.parent, self.local_stop(path))
            finally:
                Path(temporary).unlink(missing_ok=True)
                staged.unlink(missing_ok=True)
        self.local[conflict] = dict(left)
        if not remote_copy:
            self.upload(conflict, left, None)
        else:
            self.remember(conflict, remote_copy)
        self.download(path, right, left)
        self.store.event(f'Conflict kept both versions: {path} and {conflict}')

    def sync(self, allow_many_deletes=False):
        if not self.activity.started:
            self.activity.start()
        actions = self.snapshot(allow_many_deletes)
        changed = [a for a in actions if a.kind not in ('remember', 'forget')]
        self.activity.planned(changed)
        completed = 0
        for action in actions:
            path = action.path
            left, right = self.local.get(path), self.remote.get(path)
            if left and right and left['kind'] == right['kind'] == 'file' and left['hash'] == right['hash']:
                self.remember(path, right)
                if action.kind not in ('remember', 'forget'):
                    self.activity.complete(action, skipped=True)
                continue
            if action.kind not in ('remember', 'forget'):
                self.activity.begin(action)
            if action.kind == 'mkdir-local':
                target = self.local_target(path)
                target.mkdir(parents=True, exist_ok=True)
                self.local[path] = {'kind': 'dir'}
            elif action.kind == 'mkdir-remote':
                item = self.drive.mkdir(self.parent_id(path), PurePosixPath(path).name, 'folder:' + path)
                self.remote[path] = item
            elif action.kind == 'remember':
                # A prior action may have resolved this conflict copy already.
                self.remember(path, right)
            elif action.kind == 'forget':
                self.store.remember(path, None)
            elif action.kind == 'upload':
                self.upload(path, left, right)
            elif action.kind == 'download':
                self.download(path, right, left)
            elif action.kind == 'conflict':
                self.conflict(path, left, right)
            elif action.kind == 'archive-local':
                self.archive_source(path, left)
                self.store.remember(path, None)
                self.local.pop(path, None)
            elif action.kind == 'trash-remote':
                self.checked_local(path, None)
                key = hashlib.sha256((right['id'] + right['hash']).encode()).hexdigest()
                old = self.paths.state / 'downloads' / (key + '.part')
                self.activity.phase('backup', path, right['size'])
                self.drive.download(right, old)
                self.backup(path + '.cloud-before-trash', old, right['hash'])
                self.drive.trash(right)
                old.unlink(missing_ok=True)
                self.store.remember(path, None)
                self.remote.pop(path, None)
            if action.kind not in ('remember', 'forget'):
                completed += 1
                self.store.event(action.kind + ': ' + path)
                self.activity.complete(action)
        self.activity.finish()
        return changed
