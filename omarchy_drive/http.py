"""Bounded HTTPS requests; retry reads, never blindly replay a mutation."""
import json
import http.client
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from .storage import AuthRequired, Blocked, DriveError, Offline


class APIError(DriveError):
    def __init__(self, status, reason='requestFailed'):
        self.status, self.reason = status, reason
        if status == 403 and reason in ('accessNotConfigured', 'SERVICE_DISABLED', 'serviceDisabled'):
            self.kind = 'blocked'
            message = ('Google sign-in succeeded, but the Google Drive API is disabled or not yet active '
                       'in your OAuth client project. Enable it at '
                       'https://console.cloud.google.com/apis/library/drive.googleapis.com '
                       'then retry: omarchy-drive setup --continue')
        else:
            message = f'Google API returned HTTP {status} ({reason}).'
        super().__init__(message)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTP:
    def __init__(self, token=None, opener=None, sleep=time.sleep):
        self.token = token
        self.opener = opener or urllib.request.build_opener(NoRedirect)
        self.sleep = sleep

    def request(self, method, url, *, data=None, headers=None, expected=(200,), stream=False, retry=False):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname not in ('www.googleapis.com', 'oauth2.googleapis.com')
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise Blocked('Refusing an untrusted Google API endpoint.')
        headers = dict(headers or {})
        if isinstance(data, dict):
            data = json.dumps(data).encode()
            headers.setdefault('Content-Type', 'application/json')
        refreshed = False
        for attempt in range(5):
            if self.token:
                headers['Authorization'] = 'Bearer ' + self.token.access_token()
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                response = self.opener.open(request, timeout=30)
            except urllib.error.HTTPError as error:
                response = error
            except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError, http.client.HTTPException) as error:
                if retry and attempt < 4:
                    self.sleep(min(8, 2 ** attempt) + random.random())
                    continue
                raise Offline('Cannot reach Google Drive. Your files stay local; the next sync will retry.') from error
            status = response.code
            if status in expected:
                if stream:
                    return response
                try:
                    with response:
                        body = response.read()
                        return status, response.headers, body
                except (OSError, http.client.HTTPException) as error:
                    if retry and attempt < 4:
                        self.sleep(min(8, 2 ** attempt))
                        continue
                    raise Offline('Google interrupted the response. The next sync will retry.') from error
            with response:
                body = response.read(65536)
            if status == 401 and self.token and not refreshed:
                self.token.refresh()
                refreshed = True
                continue
            try:
                content = json.loads(body)
                reason = content.get('error', {})
                if isinstance(reason, dict):
                    reason = (reason.get('errors') or [{}])[0].get('reason', reason.get('status', 'requestFailed'))
            except (ValueError, AttributeError, IndexError):
                reason = 'requestFailed'
            # Never include provider descriptions or request URLs: they may contain credentials.
            reason = str(reason) if str(reason).replace('_', '').isalnum() else 'requestFailed'
            if retry and attempt < 4 and (status in (429, 500, 502, 503, 504) or
                    status == 403 and reason in ('rateLimitExceeded', 'userRateLimitExceeded')):
                self.sleep(min(8, 2 ** attempt) + random.random())
                continue
            if status == 401:
                raise AuthRequired('Google sign-in expired. Run: omarchy-drive setup')
            raise APIError(status, reason)
        raise Offline('Google Drive is temporarily unavailable.')

    def json(self, method, url, **kwargs):
        _, headers, body = self.request(method, url, **kwargs)
        try:
            return (json.loads(body) if body else {}), headers
        except ValueError as error:
            raise Blocked('Google returned an invalid API response. Retry sync.') from error
