"""Google Drive v3 adapter: pagination, conditional writes, resumable transfers."""
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import urllib.parse
from .http import APIError
from .storage import Blocked, DriveError, Offline

API = 'https://www.googleapis.com/drive/v3/'
UPLOAD = 'https://www.googleapis.com/upload/drive/v3/'
FOLDER = 'application/vnd.google-apps.folder'
FIELDS = 'id,name,mimeType,md5Checksum,size,modifiedTime,version,trashed,parents'
CHUNK = 8 * 1024 * 1024


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'md5').hexdigest()


def encode(value):
    return urllib.parse.quote(str(value), safe='')


def query_url(base, **params):
    return base + '?' + urllib.parse.urlencode(params)


def component(name):
    if not isinstance(name, str) or name in ('', '.', '..') or '/' in name or '\x00' in name:
        raise Blocked('Drive contains a file name that cannot safely be stored locally.')
    return name


class Drive:
    def __init__(self, http, store, state, progress=None):
        self.http, self.store, self.state = http, store, Path(state)
        self.progress = progress or (lambda *args: None)

    def get(self, file_id):
        value, headers = self.http.json('GET', query_url(API + 'files/' + encode(file_id), fields=FIELDS), retry=True)
        value['_etag'] = headers.get('ETag')
        return value

    def children(self, parent):
        token = None
        seen = set()
        # IDs are opaque; escape query literals as required by Drive query syntax.
        parent = parent.replace('\\', '\\\\').replace("'", "\\'")
        while True:
            params = dict(q=f"'{parent}' in parents and trashed = false", spaces='drive', pageSize=1000,
                          fields=f'nextPageToken,incompleteSearch,files({FIELDS})')
            if token:
                params['pageToken'] = token
            value, _ = self.http.json('GET', query_url(API + 'files', **params), retry=True)
            if value.get('incompleteSearch'):
                raise Blocked('Google returned an incomplete listing. No sync changes were applied.')
            yield from value.get('files', [])
            token = value.get('nextPageToken')
            if not token:
                return
            if token in seen:
                raise Blocked('Google returned a repeated listing page.')
            seen.add(token)

    def root(self, name, create=False):
        matches = [item for item in self.children('root') if item['name'] == name]
        if len(matches) > 1:
            raise Blocked(f'More than one Drive folder is named {name}. Choose a folder by ID.')
        if matches:
            if matches[0]['mimeType'] != FOLDER:
                raise Blocked('The requested Drive folder name belongs to a regular file.')
            return matches[0]['id']
        if not create:
            raise Blocked(f'Drive folder {name} does not exist. Use setup --create-folder to create it.')
        return self.mkdir('root', name, 'root:' + name)['id']

    def scan(self, folder_id, selected=None, root_files=True):
        root = self.get(folder_id)
        if root.get('trashed') or root.get('mimeType') != FOLDER:
            raise Blocked('The configured Google Drive folder is missing or in Trash. Restore it before syncing.')
        result, pending, visited = {}, [('', folder_id)], {folder_id}
        while pending:
            prefix, parent = pending.pop()
            for item in self.children(parent):
                name = component(item['name'])
                path = str(PurePosixPath(prefix) / name)
                if name.startswith('.omarchy-drive-tmp-'):
                    raise Blocked('A Drive file uses the app temporary-file prefix. Rename it before syncing.')
                if path in result:
                    raise Blocked(f'Duplicate Drive names at {path}. Rename the duplicates before syncing.')
                folder = item['mimeType'] == FOLDER
                if not prefix and (folder and selected is not None and name not in selected or not folder and not root_files):
                    continue
                unsupported = item['mimeType'].startswith('application/vnd.google-apps.') and not folder
                if not folder and not unsupported and not item.get('md5Checksum'):
                    raise Blocked(f'Drive did not return a checksum for {path}. Sync stopped before changes.')
                result[path] = dict(item, kind='dir' if folder else 'unsupported' if unsupported else 'file',
                                    hash=item.get('md5Checksum'), size=int(item.get('size', 0)))
                if folder:
                    if item['id'] in visited:
                        raise Blocked('Google Drive returned a folder cycle.')
                    visited.add(item['id'])
                    pending.append((path, item['id']))
        return result

    def new_id(self):
        value, _ = self.http.json('GET', query_url(API + 'files/generateIds', count=1, space='drive', type='files'), retry=True)
        return value['ids'][0]

    def mkdir(self, parent, name, key):
        pending = self.store.pending(key)
        if not pending:
            pending = {'id': self.new_id()}
            self.store.save_pending(key, pending)
        body = dict(id=pending['id'], name=component(name), mimeType=FOLDER, parents=[parent])
        try:
            result, _ = self.http.json('POST', query_url(API + 'files', fields=FIELDS), data=body, expected=(200, 201))
        except APIError as error:
            if error.status != 409:
                raise
            result = self.get(pending['id'])
            if (result.get('trashed') or result.get('name') != name or result.get('mimeType') != FOLDER
                    or parent not in result.get('parents', [])):
                raise Blocked('A pending folder creation conflicts with its Drive ID.') from error
        self.store.save_pending(key, None)
        return dict(result, kind='dir', hash=None)

    def unchanged(self, expected):
        current = self.get(expected['id'])
        if (current.get('trashed') or current.get('version') != expected.get('version')
                or current.get('md5Checksum') != expected.get('md5Checksum')
                or current.get('name') != expected.get('name') or current.get('parents') != expected.get('parents')):
            raise Blocked('A Drive file changed during sync. Run sync again to reconcile the new version.')
        return current

    def condition(self, expected):
        current = self.unchanged(expected)
        if not current.get('_etag'):
            raise Blocked('Google did not provide a version precondition. Refusing to overwrite or trash this file.')
        return {'If-Match': current['_etag']}

    def download(self, item, target):
        self.unchanged(item)
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        size = int(item.get('size', 0))
        offset = target.stat().st_size if target.exists() else 0
        if offset > size:
            target.unlink()
            offset = 0
        headers = {'Range': f'bytes={offset}-'} if offset else {}
        if offset != size or not target.exists():
            response = self.http.request('GET', query_url(API + 'files/' + encode(item['id']), alt='media'),
                                         headers=headers, expected=(200, 206), stream=True, retry=True)
            with response:
                if response.code == 206:
                    content_range = response.headers.get('Content-Range', '')
                    if not content_range.startswith(f'bytes {offset}-'):
                        raise Blocked('Google returned an unexpected download range.')
                else:
                    offset = 0
                try:
                    with target.open('ab' if offset else 'wb') as output:
                        while True:
                            chunk = response.read(CHUNK)
                            if not chunk:
                                break
                            output.write(chunk)
                            offset += len(chunk)
                            self.progress('download', item['name'], offset, size)
                        output.flush()
                        os.fsync(output.fileno())
                except (TimeoutError, ConnectionError, OSError, http.client.IncompleteRead) as error:
                    raise Offline('Download interrupted. It will resume on the next sync.') from error
        if target.stat().st_size != size or digest(target) != item['md5Checksum']:
            target.unlink(missing_ok=True)
            raise Blocked('Downloaded content failed verification. The local original was retained.')
        self.unchanged(item)
        return target

    def upload(self, source, parent, name, checksum, existing=None):
        source = Path(source)
        key = 'upload:' + parent + ':' + name + ':' + checksum
        pending = self.store.pending(key)
        if not pending:
            pending = {'id': existing['id'] if existing else self.new_id()}
            self.store.save_pending(key, pending)
        if existing and pending.get('session') and pending.get('expected_version') != existing.get('version'):
            pending.pop('session', None)
            self.store.save_pending(key, pending)
        if existing and pending['id'] != existing['id']:
            raise Blocked('A pending upload belongs to another Drive file.')
        total = source.stat().st_size
        result = None
        # Retry incomplete chunks by querying the session, never assume an offset.
        for attempt in range(8):
            if not pending.get('session'):
                condition = self.condition(existing) if existing else {}
                body = {} if existing else dict(id=pending['id'], name=component(name), parents=[parent])
                endpoint = UPLOAD + 'files' + ('/' + encode(pending['id']) if existing else '')
                try:
                    _, headers, _ = self.http.request('PATCH' if existing else 'POST',
                        query_url(endpoint, uploadType='resumable', fields=FIELDS), data=body,
                        headers=dict(condition, **{'X-Upload-Content-Type': 'application/octet-stream',
                                                   'X-Upload-Content-Length': str(total)}), expected=(200, 201))
                except APIError as error:
                    if error.status != 409 or existing:
                        raise
                    result = self.get(pending['id'])
                    if result.get('md5Checksum') != checksum or result.get('trashed'):
                        raise Blocked('A previous upload has unexpected content.') from error
                    break
                session = headers.get('Location', '')
                parsed = urllib.parse.urlsplit(session)
                if (parsed.scheme != 'https' or parsed.hostname != 'www.googleapis.com' or
                        not parsed.path.startswith('/upload/drive/v3/files')):
                    raise Blocked('Google returned an untrusted upload session.')
                pending['session'] = session
                pending['expected_version'] = existing.get('version') if existing else None
                self.store.save_pending(key, pending)
            try:
                status, headers, body = self.http.request('PUT', pending['session'], data=b'',
                    headers={'Content-Range': f'bytes */{total}'}, expected=(200, 201, 308))
                if status in (200, 201):
                    result = json.loads(body)
                    break
                received = headers.get('Range', '')
                offset = int(received.rsplit('-', 1)[1]) + 1 if received else 0
                if offset < 0 or offset > total:
                    raise Blocked('Google returned an invalid upload offset.')
                if existing:
                    self.unchanged(existing)
                with source.open('rb') as stream:
                    stream.seek(offset)
                    while offset < total or total == 0:
                        chunk = stream.read(CHUNK)
                        end = offset + len(chunk) - 1
                        range_value = f'bytes {offset}-{end}/{total}' if total else 'bytes */0'
                        status, chunk_headers, body = self.http.request('PUT', pending['session'], data=chunk,
                            headers={'Content-Type': 'application/octet-stream', 'Content-Range': range_value},
                            expected=(200, 201, 308))
                        offset += len(chunk)
                        self.progress('upload', name, offset, total)
                        if status in (200, 201):
                            result = json.loads(body)
                            break
                        acknowledged = chunk_headers.get('Range', '')
                        received_offset = int(acknowledged.rsplit('-', 1)[1]) + 1 if acknowledged else 0
                        if received_offset != offset:
                            # Ask the server for a confirmed offset before sending more.
                            break
                        if not chunk:
                            raise Blocked('Upload did not finish after the last byte.')
                if result:
                    break
            except APIError as error:
                if error.status in (404, 410):
                    pending.pop('session', None)
                    self.store.save_pending(key, pending)
                    # An update session may have finished before its response was lost.
                    if existing:
                        current = self.get(existing['id'])
                        if current.get('md5Checksum') == checksum:
                            result = current
                            break
                elif error.status not in (429, 500, 502, 503, 504):
                    raise
                self.http.sleep(min(8, 2 ** attempt))
            except Offline:
                if attempt >= 2:
                    raise
                self.http.sleep(min(8, 2 ** attempt))
        if not result:
            raise Offline('Upload is incomplete. Its saved session will resume on the next sync.')
        if result.get('md5Checksum') != checksum or result.get('id') != pending['id']:
            raise Blocked('Uploaded content failed verification. Sync history was not advanced.')
        self.store.save_pending(key, None)
        return dict(result, hash=checksum, kind='file', size=int(result.get('size', total)))

    def trash(self, expected):
        # Only Drive Trash is used; this app never permanently deletes remote files.
        self.http.json('PATCH', query_url(API + 'files/' + encode(expected['id']), fields='id,trashed'),
                       data={'trashed': True}, headers=self.condition(expected))
