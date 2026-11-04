"""Run the whole engine through real HTTP against a local Drive protocol server."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.parse
import urllib.request

from omarchy_drive.drive import Drive, FOLDER
from omarchy_drive.engine import Engine
from omarchy_drive.http import APIError, HTTP
from omarchy_drive.storage import Blocked, Paths, Store


class DriveServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(('127.0.0.1', 0), DriveHandler)
        self.items = {'root': {'id': 'root', 'name': 'Omarchy', 'mimeType': FOLDER, 'version': '1', 'parents': []}}
        self.content = {}
        self.sessions = {}
        self.counter = 0
        self.drop_upload_response = False
        self.requests = []

    def folder(self, name, parent='root'):
        self.counter += 1
        file_id = f'folder-{self.counter}'
        self.items[file_id] = dict(id=file_id, name=name, parents=[parent], mimeType=FOLDER,
                                  version=str(self.counter), trashed=False)
        return file_id

    def put(self, name, content, file_id=None, parent=None):
        self.counter += 1
        file_id = file_id or f'id-{self.counter}'
        if parent is None:  # An edit stays in its folder.
            parent = self.items[file_id]['parents'][0] if file_id in self.items else 'root'
        self.items[file_id] = dict(id=file_id, name=name, parents=[parent], mimeType='application/octet-stream',
                                  version=str(self.counter), size=str(len(content)),
                                  md5Checksum=hashlib.md5(content).hexdigest(), trashed=False)
        self.content[file_id] = content
        return file_id


def etag(item):
    return '"v' + item['version'] + '"'


def v2_resource(item):
    return dict(id=item['id'], title=item['name'], version=item['version'], md5Checksum=item.get('md5Checksum'),
                fileSize=item.get('size'), etag=etag(item), labels={'trashed': bool(item.get('trashed'))})


class DriveHandler(BaseHTTPRequestHandler):
    """Mirrors observed Drive behaviour: v3 has no ETag and ignores If-Match; v2 has both."""
    def log_message(self, *args):
        pass

    def send(self, value=None, status=200, headers=None, raw=None):
        body = raw if raw is not None else json.dumps(value or {}).encode()
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, str(value))
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def parse(self):
        parsed = urllib.parse.urlsplit(self.path)
        self.server.requests.append(self.command + ' ' + urllib.parse.unquote(self.path))
        return parsed.path, urllib.parse.parse_qs(parsed.query)

    def body(self):
        return self.rfile.read(int(self.headers.get('Content-Length', 0)))

    def do_GET(self):
        path, query = self.parse()
        if path == '/drive/v3/files/generateIds':
            self.server.counter += 1
            return self.send({'ids': ['generated-' + str(self.server.counter)]})
        if path == '/drive/v3/files':
            parent = query['q'][0].split("'")[1]
            items = [item for item in self.server.items.values() if parent in item.get('parents', []) and not item.get('trashed')]
            return self.send({'files': items})
        file_id = path.rsplit('/', 1)[1]
        item = self.server.items.get(file_id)
        if not item:
            return self.send(status=404)
        if query.get('alt') == ['media']:
            content = self.server.content[file_id]
            if self.headers.get('Range'):
                offset = int(self.headers['Range'].split('=')[1].split('-')[0])
                return self.send(status=206, headers={'Content-Range': f'bytes {offset}-{len(content)-1}/{len(content)}'}, raw=content[offset:])
            return self.send(raw=content)
        if path.startswith('/drive/v2/'):
            return self.send(v2_resource(item))
        return self.send(item)

    def do_POST(self):
        path, _ = self.parse()
        metadata = json.loads(self.body())
        if path.startswith('/upload/'):
            if metadata['id'] in self.server.items:
                return self.send(status=409)
            key = str(len(self.server.sessions) + 1)
            self.server.sessions[key] = dict(metadata=metadata, content=b'')
            return self.send(headers={'Location': 'https://www.googleapis.com/upload/drive/v3/files?session=' + key})
        self.server.items[metadata['id']] = dict(metadata, version='1')
        return self.send(self.server.items[metadata['id']])

    def do_PATCH(self):
        path, _ = self.parse()
        file_id = path.rsplit('/', 1)[1]
        item = self.server.items[file_id]
        metadata = json.loads(self.body())
        if path.startswith('/drive/v2/'):
            if self.headers.get('If-Match') != etag(item):
                return self.send(status=412)
            item['trashed'] = metadata['labels']['trashed']
            return self.send(v2_resource(item))
        if path.startswith('/upload/'):
            key = str(len(self.server.sessions) + 1)
            self.server.sessions[key] = dict(metadata=dict(item, **metadata), content=b'')
            return self.send(headers={'Location': 'https://www.googleapis.com/upload/drive/v3/files?session=' + key})
        item.update(metadata)
        return self.send(item)

    def do_PUT(self):
        path, query = self.parse()
        if 'session' not in query:
            self.body()
            item = self.server.items[path.rsplit('/', 1)[1]]
            if not path.startswith('/upload/drive/v2/') or self.headers.get('If-Match') != etag(item):
                return self.send(status=412)
            key = str(len(self.server.sessions) + 1)
            self.server.sessions[key] = dict(metadata=dict(item), content=b'', v2=True)
            return self.send(headers={'Location': f'https://www.googleapis.com/upload/drive/v2/files/{item["id"]}?session={key}'})
        session = self.server.sessions[query['session'][0]]
        chunk = self.body()
        total = int(self.headers['Content-Range'].rsplit('/', 1)[1])
        if self.headers['Content-Range'].startswith('bytes */') and total:
            if session.get('done'):
                return self.send(self.server.items[session['metadata']['id']])
            headers = {'Range': f'bytes=0-{len(session["content"])-1}'} if session['content'] else {}
            return self.send(status=308, headers=headers)
        if total:
            offset = int(self.headers['Content-Range'].split()[1].split('-')[0])
            session['content'] = session['content'][:offset] + chunk
        if len(session['content']) < total:
            return self.send(status=308, headers={'Range': f'bytes=0-{len(session["content"])-1}'})
        metadata = session['metadata']
        self.server.put(metadata['name'], session['content'], metadata['id'], metadata['parents'][0])
        session['done'] = True
        if self.server.drop_upload_response:
            self.server.drop_upload_response = False
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        item = self.server.items[metadata['id']]
        return self.send(v2_resource(item) if session.get('v2') else item)


class LoopbackOpener:
    """Only tests rewrite validated Google URLs to the local protocol server."""
    def __init__(self, server):
        self.port = server.server_port
        self.opener = urllib.request.build_opener()

    def open(self, request, **kwargs):
        url = urllib.parse.urlsplit(request.full_url)
        local = f'http://127.0.0.1:{self.port}' + url.path + ('?' + url.query if url.query else '')
        return self.opener.open(urllib.request.Request(local, data=request.data, headers=dict(request.headers), method=request.method), **kwargs)


class WholeAppIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.home = base / 'home'
        self.local = self.home / 'Sync'
        self.local.mkdir(parents=True)
        # Patch before Paths(): it derives settings and backups from these, and the engine writes both.
        environment = patch.dict(os.environ, {
            'HOME': str(self.home), 'XDG_CONFIG_HOME': str(base / 'config'),
            'XDG_STATE_HOME': str(base / 'state'), 'XDG_DATA_HOME': str(base / 'data')})
        environment.start()
        self.addCleanup(environment.stop)
        self.paths = Paths()
        self.paths.prepare()
        self.store = Store(self.paths.state)
        self.addCleanup(self.store.close)
        self.server = DriveServer()
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.drive = Drive(HTTP(opener=LoopbackOpener(self.server), sleep=lambda _: None), self.store, self.paths.state)

    def engine(self, folder=None, excluded=()):
        folder = folder or self.local
        info = folder.stat()
        return Engine(self.paths, {'folder_id': 'root', 'excluded': list(excluded),
                                   'home_folders': {folder.name: {'identity': [info.st_dev, info.st_ino]}}},
                      self.store, self.drive)

    def test_complete_two_way_flow_through_google_protocol(self):
        (self.local / 'local.txt').write_bytes(b'local')
        (self.local / 'empty.txt').touch()
        cloud_id = self.server.put('cloud.txt', b'cloud', parent=self.server.folder('Sync'))
        self.engine().sync()
        self.assertEqual((self.local / 'cloud.txt').read_bytes(), b'cloud')
        self.assertEqual(self.engine().sync(), [])
        (self.local / 'cloud.txt').write_bytes(b'edited locally')
        self.engine().sync()
        self.assertEqual(self.server.content[cloud_id], b'edited locally')
        self.server.put('cloud.txt', b'edited in Drive', cloud_id)
        self.engine().sync()
        self.assertEqual((self.local / 'cloud.txt').read_bytes(), b'edited in Drive')
        (self.local / 'cloud.txt').unlink()
        self.engine().sync()
        self.assertTrue(self.server.items[cloud_id]['trashed'])
        self.assertEqual(self.engine().sync(), [])

    def race_after_precondition(self, file_id, content):
        """Simulate a Drive edit landing after the ETag was read but before the write."""
        original = self.drive.condition
        def racing(expected):
            header = original(expected)
            self.server.put('cloud.txt', content, file_id)
            return header
        self.drive.condition = racing

    def test_drive_edit_racing_an_overwrite_is_not_clobbered(self):
        cloud_id = self.server.put('cloud.txt', b'cloud', parent=self.server.folder('Sync'))
        self.engine().sync()
        (self.local / 'cloud.txt').write_bytes(b'edited locally')
        self.race_after_precondition(cloud_id, b'edited in Drive')
        with self.assertRaisesRegex(Blocked, 'changed on Drive'):
            self.engine().sync()
        self.assertEqual(self.server.content[cloud_id], b'edited in Drive')

    def test_drive_edit_racing_a_trash_is_not_trashed(self):
        cloud_id = self.server.put('cloud.txt', b'cloud', parent=self.server.folder('Sync'))
        self.engine().sync()
        (self.local / 'cloud.txt').unlink()
        self.race_after_precondition(cloud_id, b'edited in Drive')
        with self.assertRaisesRegex(Blocked, 'changed on Drive'):
            self.engine().sync()
        self.assertFalse(self.server.items[cloud_id]['trashed'])

    def test_lost_chunk_response_resumes_saved_server_session(self):
        (self.local / 'test.txt').write_bytes(b'some bytes')
        self.server.drop_upload_response = True
        self.engine().sync()
        matches = [item for item in self.server.items.values() if item['name'] == 'test.txt']
        self.assertEqual(len(matches), 1)
        self.assertEqual(self.server.content[matches[0]['id']], b'some bytes')
        self.assertEqual(self.engine().sync(), [])

    def test_home_folder_mapping_through_google_protocol(self):
        documents = self.home / 'Documents'
        documents.mkdir()
        (documents / 'local.txt').write_bytes(b'local home file')
        self.engine(documents).sync()
        folder = next(item for item in self.server.items.values() if item['name'] == 'Documents')
        uploaded = next(item for item in self.server.items.values() if item['name'] == 'local.txt')
        self.assertEqual(uploaded['parents'], [folder['id']])
        self.assertEqual(self.server.content[uploaded['id']], b'local home file')
        self.server.put('cloud.txt', b'cloud home file', parent=folder['id'])
        self.engine(documents).sync()
        self.assertEqual((documents / 'cloud.txt').read_bytes(), b'cloud home file')
        self.assertEqual(self.engine(documents).sync(), [])

    def test_unticked_drive_folder_is_never_listed(self):
        ticked = self.server.folder('Sync')
        other = self.server.folder('Other')
        other_file = self.server.put('private.txt', b'private', parent=other)
        loose = self.server.put('loose.txt', b'loose')
        self.server.put('kept.txt', b'kept', parent=ticked)
        self.engine().sync()
        self.assertEqual((self.local / 'kept.txt').read_bytes(), b'kept')
        for file_id in (other, other_file, loose):
            self.assertFalse([request for request in self.server.requests if file_id in request])
        self.assertEqual(sorted(path.name for path in self.home.iterdir()), ['Sync'])

    def test_excluded_paths_are_not_listed_read_or_written(self):
        excluded = ('Sync/Private', 'Sync/secret.txt')
        ticked = self.server.folder('Sync')
        private = self.server.folder('Private', parent=ticked)
        inner = self.server.put('inner.txt', b'cloud inner', parent=private)
        secret = self.server.put('secret.txt', b'cloud secret', parent=ticked)
        self.server.put('kept.txt', b'kept', parent=ticked)
        (self.local / 'Private').mkdir()
        (self.local / 'Private' / 'local-inner.txt').write_bytes(b'local inner')
        (self.local / 'secret.txt').write_bytes(b'local secret')
        (self.local / 'new.txt').write_bytes(b'new')

        self.engine(excluded=excluded).sync()

        self.assertEqual((self.local / 'kept.txt').read_bytes(), b'kept')
        new = next(item for item in self.server.items.values() if item['name'] == 'new.txt')
        self.assertEqual(self.server.content[new['id']], b'new')
        for file_id in (private, inner, secret):
            self.assertFalse([request for request in self.server.requests if file_id in request], file_id)
        self.assertEqual(self.server.content[secret], b'cloud secret')
        self.assertEqual(self.server.content[inner], b'cloud inner')
        self.assertEqual([item['name'] for item in self.server.items.values() if item['name'] == 'local-inner.txt'], [])
        self.assertEqual(len([item for item in self.server.items.values() if item['name'] == 'secret.txt']), 1)
        self.assertEqual((self.local / 'secret.txt').read_bytes(), b'local secret')
        self.assertFalse((self.local / 'Private' / 'inner.txt').exists())
        self.assertEqual(self.engine(excluded=excluded).sync(), [])
