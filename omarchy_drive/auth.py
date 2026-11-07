import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import secrets
import time
import threading
import urllib.parse
import webbrowser
from .http import HTTP, APIError
from .storage import AuthRequired, Blocked, atomic_json, read_json, synchronized

TOKEN_URL = 'https://oauth2.googleapis.com/token'
SCOPE = 'https://www.googleapis.com/auth/drive'


def load_client(path):
    value = read_json(path)
    client = value.get('installed', {})
    if not client.get('client_id') or not client.get('client_secret'):
        raise AuthRequired('Choose a Google OAuth client JSON for a Desktop app, not a web app.')
    return {'client_id': client['client_id'], 'client_secret': client['client_secret']}


def exchange(data, http=None):
    try:
        value, _ = (http or HTTP()).json('POST', TOKEN_URL,
            data=urllib.parse.urlencode(data).encode(),
            headers={'Content-Type': 'application/x-www-form-urlencoded'})
    except APIError as error:
        if error.status in (400, 401):
            raise AuthRequired('Google authorization failed or expired. Run: omarchy-drive setup') from error
        raise
    if not value.get('access_token'):
        raise AuthRequired('Google did not return an access token.')
    return value


class Tokens:
    def __init__(self, paths, http=None):
        self._lock = threading.RLock()
        self.paths = paths
        self.http = http
        self.value = read_json(paths.tokens)

    @synchronized
    def access_token(self):
        if not self.value.get('refresh_token'):
            raise AuthRequired('Connect Google Drive first: omarchy-drive setup')
        if self.value.get('expires_at', 0) < time.time() + 60:
            self.refresh()
        return self.value['access_token']

    @synchronized
    def refresh(self):
        if not self.value.get('refresh_token'):
            raise AuthRequired('Connect Google Drive first: omarchy-drive setup')
        value = exchange(dict(load_client(self.paths.client), grant_type='refresh_token',
                              refresh_token=self.value['refresh_token']), self.http)
        self.value.update(value)
        self.value['expires_at'] = time.time() + value.get('expires_in', 3600)
        atomic_json(self.paths.tokens, self.value)


def authorize(paths, client_path, timeout=180):
    client = load_client(client_path)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    state = secrets.token_urlsafe(32)
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            values = urllib.parse.parse_qs(parsed.query)
            valid = (parsed.path == '/callback' and
                     secrets.compare_digest(values.get('state', [''])[0], state))
            if not valid:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'Invalid sign-in callback.')
                return
            result.update(code=values.get('code', [''])[0], error=values.get('error', [''])[0])
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'You can close this window and return to Omarchy Drive.')

    with HTTPServer(('127.0.0.1', 0), Callback) as server:
        server.timeout = 1
        redirect = f'http://127.0.0.1:{server.server_port}/callback'
        url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urllib.parse.urlencode({
            'client_id': client['client_id'], 'redirect_uri': redirect, 'response_type': 'code',
            'scope': SCOPE, 'access_type': 'offline', 'prompt': 'consent',
            'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'})
        print('Opening Google sign-in. If the browser does not open, visit:\n' + url, flush=True)
        webbrowser.open(url)
        deadline = time.monotonic() + timeout
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if not result.get('code'):
        raise AuthRequired('Sign-in was cancelled or timed out. Run setup again.')
    token = exchange(dict(client, code=result['code'], code_verifier=verifier,
                          redirect_uri=redirect, grant_type='authorization_code'))
    if not token.get('refresh_token'):
        raise AuthRequired('Google did not grant offline access. Run setup again.')
    granted = set(token.get('scope', SCOPE).split())
    if SCOPE not in granted:
        raise Blocked('Drive access was not granted. Sign in again and allow Drive access.')
    paths.prepare()
    atomic_json(paths.client, {'installed': client})
    token['expires_at'] = time.time() + token.get('expires_in', 3600)
    atomic_json(paths.tokens, token)
