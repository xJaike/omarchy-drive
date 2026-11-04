import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from omarchy_drive.auth import Tokens, authorize, load_client
from omarchy_drive.drive import CHANGED_ON_DRIVE, Drive, FOLDER
from omarchy_drive.http import HTTP, APIError
from omarchy_drive.storage import AuthRequired, Blocked, Offline, Paths, Store, atomic_json


class Response(io.BytesIO):
    def __init__(self, body=b'', code=200, headers=None):
        super().__init__(body)
        self.code = code
        self.headers = headers or {}


class ScriptHTTP:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.sleep = lambda _: None

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return response(method, url, kwargs)
        return response

    def json(self, method, url, **kwargs):
        _, headers, body = self.request(method, url, **kwargs)
        return json.loads(body), headers


def json_response(value, headers=None, status=200):
    return status, headers or {}, json.dumps(value).encode()


class APITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)
        self.addCleanup(self.store.close)
        self.item = dict(id='file-1', name='a.txt', mimeType='text/plain', size='6',
                         md5Checksum=hashlib.md5(b'abcdef').hexdigest(), version='2', parents=['root'])

    def adapter(self, responses):
        self.http = ScriptHTTP(responses)
        return Drive(self.http, self.store, self.root)

    def test_paginated_scan_and_nested_folders(self):
        child = dict(id='folder', name='sub', mimeType=FOLDER)
        drive = self.adapter([
            json_response({'id': 'root', 'mimeType': FOLDER}),
            json_response({'files': [child], 'nextPageToken': 'page-2'}),
            json_response({'files': [self.item]}),
            json_response({'files': []}),
        ])
        result = drive.scan('root')
        self.assertEqual(set(result), {'sub', 'a.txt'})
        self.assertIn('pageToken=page-2', self.http.calls[2][1])

    def test_incomplete_or_duplicate_listing_blocks(self):
        for body in ({'incompleteSearch': True, 'files': []}, {'files': [self.item, self.item]}):
            drive = self.adapter([json_response({'mimeType': FOLDER}), json_response(body)])
            with self.assertRaises(Blocked):
                drive.scan('root')

    def test_traversal_name_blocks(self):
        for name in ('../escape', '..', '.', '/absolute', '\x00bad', '.omarchy-drive-tmp-secret'):
            drive = self.adapter([json_response({'mimeType': FOLDER}),
                                  json_response({'files': [dict(self.item, name=name)]})])
            with self.subTest(name=name), self.assertRaises(Blocked):
                drive.scan('root')

    def test_google_document_and_shortcut_are_marked_unsupported(self):
        item = dict(self.item, mimeType='application/vnd.google-apps.document')
        drive = self.adapter([json_response({'mimeType': FOLDER}), json_response({'files': [item]})])
        self.assertEqual(drive.scan('root')['a.txt']['kind'], 'unsupported')

    def test_download_resumes_partial_bytes_and_checks_version(self):
        partial = self.root / 'download.part'
        partial.write_bytes(b'abc')
        drive = self.adapter([
            json_response(self.item),
            Response(b'def', 206, {'Content-Range': 'bytes 3-5/6'}),
            json_response(self.item),
        ])
        drive.download(self.item, partial)
        self.assertEqual(partial.read_bytes(), b'abcdef')
        self.assertEqual(self.http.calls[1][2]['headers']['Range'], 'bytes=3-')

    def test_download_restarts_if_range_is_ignored(self):
        partial = self.root / 'download.part'
        partial.write_bytes(b'abc')
        drive = self.adapter([json_response(self.item), Response(b'abcdef'), json_response(self.item)])
        drive.download(self.item, partial)
        self.assertEqual(partial.read_bytes(), b'abcdef')

    def test_bad_checksum_does_not_publish_download(self):
        target = self.root / 'download.part'
        drive = self.adapter([json_response(self.item), Response(b'wrong!')])
        with self.assertRaises(Blocked):
            drive.download(self.item, target)
        self.assertFalse(target.exists())

    def test_version_change_stops_download_before_content_request(self):
        drive = self.adapter([json_response(dict(self.item, version='3'))])
        with self.assertRaises(Blocked):
            drive.download(self.item, self.root / 'part')
        self.assertEqual(len(self.http.calls), 1)

    def test_resumable_upload_uses_confirmed_offset_after_restart(self):
        source = self.root / 'source'
        source.write_bytes(b'abcdef')
        key = 'upload:root:a.txt:' + self.item['md5Checksum']
        session = 'https://www.googleapis.com/upload/drive/v3/files?upload_id=private-session'
        self.store.save_pending(key, {'id': 'file-1', 'session': session})
        drive = self.adapter([(308, {'Range': 'bytes=0-2'}, b''), json_response(self.item)])
        item = drive.upload(source, 'root', 'a.txt', self.item['md5Checksum'])
        self.assertEqual(item['hash'], self.item['md5Checksum'])
        self.assertEqual(self.http.calls[1][2]['data'], b'def')
        self.assertEqual(self.http.calls[1][2]['headers']['Content-Range'], 'bytes 3-5/6')
        self.assertFalse(self.store.pending(key))

    def test_unknown_create_result_uses_same_id_and_does_not_duplicate(self):
        source = self.root / 'source'
        source.write_bytes(b'abcdef')
        drive = self.adapter([json_response({'ids': ['file-1']}), Offline('response lost')])
        with self.assertRaises(Offline):
            drive.upload(source, 'root', 'a.txt', self.item['md5Checksum'])
        key = 'upload:root:a.txt:' + self.item['md5Checksum']
        self.assertEqual(self.store.pending(key)['id'], 'file-1')
        drive = self.adapter([APIError(409), json_response(self.item)])
        drive.upload(source, 'root', 'a.txt', self.item['md5Checksum'])
        self.assertEqual(self.http.calls[0][2]['data']['id'], 'file-1')
        self.assertFalse(self.store.pending(key))

    def test_upload_never_sends_token_to_provider_supplied_host(self):
        source = self.root / 'source'
        source.write_bytes(b'abcdef')
        drive = self.adapter([json_response({'ids': ['file-1']}), (200, {'Location': 'https://attacker.example/upload'}, b'')])
        with self.assertRaises(Blocked):
            drive.upload(source, 'root', 'a.txt', self.item['md5Checksum'])
        self.assertEqual(len(self.http.calls), 2)

    def v2_item(self, **changes):
        return dict(dict(id='file-1', title='a.txt', version='2', md5Checksum=self.item['md5Checksum'],
                         fileSize='6', etag='"etag-2"', labels={'trashed': False}), **changes)

    def test_trash_is_conditional_on_the_v2_etag(self):
        drive = self.adapter([json_response(self.item), json_response(self.v2_item()),
                              json_response(self.v2_item(labels={'trashed': True}))])
        drive.trash(self.item)
        method, url, kwargs = self.http.calls[2]
        self.assertEqual(method, 'PATCH')
        self.assertTrue(url.startswith('https://www.googleapis.com/drive/v2/files/file-1?'))
        self.assertEqual(kwargs['headers'], {'If-Match': '"etag-2"'})
        self.assertEqual(kwargs['data'], {'labels': {'trashed': True}})

    def test_overwrite_is_conditional_on_the_v2_etag(self):
        source = self.root / 'source'
        source.write_bytes(b'abcdef')
        checksum = self.item['md5Checksum']
        existing = dict(self.item, size='3')
        session = 'https://www.googleapis.com/upload/drive/v2/files/file-1?uploadType=resumable&upload_id=x'
        drive = self.adapter([
            json_response(existing), json_response(self.v2_item()), (200, {'Location': session}, b''),
            (308, {}, b''), json_response(existing),
            json_response({'id': 'file-1', 'md5Checksum': checksum, 'version': '3'}),
        ])
        result = drive.upload(source, 'root', 'a.txt', checksum, existing)
        method, url, kwargs = self.http.calls[2]
        self.assertEqual(method, 'PUT')
        self.assertTrue(url.startswith('https://www.googleapis.com/upload/drive/v2/files/file-1?'))
        self.assertEqual(kwargs['headers']['If-Match'], '"etag-2"')
        # The v2 reply is partial: the result keeps the file's identity and reports the new content.
        self.assertEqual((result['name'], result['version'], result['size'], result['hash']), ('a.txt', '3', 6, checksum))

    def test_overwrite_never_sends_token_to_provider_supplied_host(self):
        source = self.root / 'source'
        source.write_bytes(b'abcdef')
        drive = self.adapter([json_response(self.item), json_response(self.v2_item()),
                              (200, {'Location': 'https://attacker.example/upload/drive/v2/files/file-1'}, b'')])
        with self.assertRaises(Blocked):
            drive.upload(source, 'root', 'a.txt', self.item['md5Checksum'], self.item)
        self.assertEqual(len(self.http.calls), 3)

    def test_trash_blocks_before_writing_when_precondition_is_unusable(self):
        for label, v2 in (('no etag', self.v2_item(etag=None)), ('version moved', self.v2_item(version='3')),
                          ('content moved', self.v2_item(md5Checksum='other'))):
            drive = self.adapter([json_response(self.item), json_response(v2)])
            with self.subTest(label), self.assertRaises(Blocked):
                drive.trash(self.item)
            self.assertEqual(len(self.http.calls), 2)

    def test_scan_skips_unticked_and_excluded_without_listing_them(self):
        folder = lambda id, name: dict(self.item, id=id, name=name, mimeType=FOLDER, md5Checksum=None)
        drive = self.adapter([
            json_response({'mimeType': FOLDER}),
            json_response({'files': [folder('keep-id', 'keep'), folder('skip-id', 'skip')]}),
            json_response({'files': [dict(self.item), folder('gone-id', 'gone')]}),
        ])
        result = drive.scan('root', lambda p: p.partition('/')[0] == 'keep' and p != 'keep/gone')
        self.assertEqual(set(result), {'keep', 'keep/a.txt'})
        self.assertEqual(len(self.http.calls), 3)

    def excluded_problem_listing(self):
        folder = dict(self.item, id='keep-id', name='keep', mimeType=FOLDER, md5Checksum=None)
        problems = [dict(self.item, id='d1', name='dup'), dict(self.item, id='d2', name='dup'),
                    dict(self.item, id='t', name='.omarchy-drive-tmp-x'),
                    dict(self.item, id='n', name='nosum', md5Checksum=None)]
        return [json_response({'mimeType': FOLDER}), json_response({'files': [folder]}),
                json_response({'files': problems})]

    def test_problems_inside_excluded_paths_do_not_block(self):
        drive = self.adapter(self.excluded_problem_listing())
        self.assertEqual(set(drive.scan('root', lambda p: p == 'keep')), {'keep'})

    def test_duplicate_blocked_names_the_path(self):
        drive = self.adapter(self.excluded_problem_listing())
        with self.assertRaises(Blocked) as caught:
            drive.scan('root')
        self.assertEqual(caught.exception.path, 'keep/dup')

    def test_stale_overwrite_and_trash_give_a_readable_block(self):
        source = self.root / 'source'
        source.write_bytes(b'abcdef')
        checksum = self.item['md5Checksum']
        key = 'upload:root:a.txt:' + checksum
        session = 'https://www.googleapis.com/upload/drive/v2/files/file-1?uploadType=resumable&upload_id=x'
        # 412 while creating the resumable session.
        drive = self.adapter([json_response(self.item), json_response(self.v2_item()), APIError(412)])
        with self.assertRaises(Blocked) as caught:
            drive.upload(source, 'root', 'a.txt', checksum, self.item)
        self.assertEqual(str(caught.exception), CHANGED_ON_DRIVE)
        # 412 on a saved session: the session is dropped from the pending record.
        self.store.save_pending(key, {'id': 'file-1', 'session': session, 'expected_version': '2'})
        drive = self.adapter([APIError(412)])
        with self.assertRaises(Blocked) as caught:
            drive.upload(source, 'root', 'a.txt', checksum, self.item)
        self.assertEqual(str(caught.exception), CHANGED_ON_DRIVE)
        self.assertNotIn('session', self.store.pending(key))
        self.assertEqual(self.store.pending(key)['id'], 'file-1')
        # 412 on trash.
        drive = self.adapter([json_response(self.item), json_response(self.v2_item()), APIError(412)])
        with self.assertRaises(Blocked) as caught:
            drive.trash(self.item)
        self.assertEqual(str(caught.exception), CHANGED_ON_DRIVE)

    def test_completed_upload_with_bad_hash_keeps_pending_record(self):
        source = self.root / 'source'
        source.write_bytes(b'abcdef')
        key = 'upload:root:a.txt:' + self.item['md5Checksum']
        self.store.save_pending(key, {'id': 'file-1', 'session': 'https://www.googleapis.com/upload/drive/v3/files?upload_id=x'})
        drive = self.adapter([json_response(dict(self.item, md5Checksum='wrong'))])
        with self.assertRaises(Blocked):
            drive.upload(source, 'root', 'a.txt', self.item['md5Checksum'])
        self.assertTrue(self.store.pending(key))


class HTTPTests(unittest.TestCase):
    def test_untrusted_endpoints_are_rejected_before_network(self):
        class Opener:
            def open(self, *args, **kwargs):
                raise AssertionError('Network should not be used')
        http = HTTP(opener=Opener())
        for url in ('http://www.googleapis.com/drive', 'https://attacker.example/',
                    'https://www.googleapis.com.attacker.example/', 'https://user@www.googleapis.com/',
                    'https://www.googleapis.com:444/'):
            with self.subTest(url=url), self.assertRaises(Blocked):
                http.request('GET', url)

    def test_reads_retry_but_writes_do_not_blindly_replay(self):
        class Opener:
            def __init__(self):
                self.calls = 0
            def open(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise urllib.error.URLError('offline')
                return Response(b'{}')
        opener = Opener()
        HTTP(opener=opener, sleep=lambda _: None).request('GET', 'https://www.googleapis.com/drive/v3/files', retry=True)
        self.assertEqual(opener.calls, 2)
        opener = Opener()
        with self.assertRaises(Offline):
            HTTP(opener=opener, sleep=lambda _: None).request('POST', 'https://www.googleapis.com/drive/v3/files', data={})
        self.assertEqual(opener.calls, 1)

    def test_401_refreshes_once_and_rebuilds_authorization(self):
        class Token:
            value = 'old'
            def access_token(self): return self.value
            def refresh(self): self.value = 'new'
        class Opener:
            def __init__(self): self.headers = []
            def open(self, request, **kwargs):
                self.headers.append(request.get_header('Authorization'))
                return Response(b'{}', 401 if len(self.headers) == 1 else 200)
        opener = Opener()
        HTTP(Token(), opener).request('GET', 'https://www.googleapis.com/drive/v3/files')
        self.assertEqual(opener.headers, ['Bearer old', 'Bearer new'])

    def test_error_text_does_not_expose_provider_descriptions(self):
        class Opener:
            def open(self, *args, **kwargs):
                return Response(json.dumps({'error': {'message': 'secret-token', 'errors': [{'reason': 'forbidden'}]}}).encode(), 403)
        with self.assertRaises(APIError) as caught:
            HTTP(opener=Opener()).request('GET', 'https://www.googleapis.com/drive/v3/files')
        self.assertNotIn('secret-token', str(caught.exception))


class AuthTests(unittest.TestCase):
    def test_desktop_client_validation_and_private_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'client.json'
            atomic_json(path, {'web': {'client_id': 'wrong'}})
            with self.assertRaises(AuthRequired):
                load_client(path)
            atomic_json(path, {'installed': {'client_id': 'desktop', 'client_secret': 'secret'}})
            self.assertEqual(load_client(path)['client_id'], 'desktop')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_refresh_preserves_refresh_token_and_rejects_revocation(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = Paths()
            paths.client = Path(directory) / 'client.json'
            paths.tokens = Path(directory) / 'tokens.json'
            atomic_json(paths.client, {'installed': {'client_id': 'desktop', 'client_secret': 'secret'}})
            atomic_json(paths.tokens, {'refresh_token': 'refresh'})
            token = Tokens(paths, ScriptHTTP([json_response({'access_token': 'fresh', 'expires_in': 3600})]))
            self.assertEqual(token.access_token(), 'fresh')
            self.assertEqual(token.value['refresh_token'], 'refresh')
            token.http = ScriptHTTP([APIError(400, 'invalid_grant')])
            with self.assertRaises(AuthRequired):
                token.refresh()


class APIActivationTests(unittest.TestCase):
    def test_disabled_api_explains_separate_activation_and_resume(self):
        error = APIError(403, 'accessNotConfigured')
        self.assertEqual(error.kind, 'blocked')
        self.assertIn('Google Drive API is disabled', str(error))
        self.assertIn('setup --continue', str(error))
