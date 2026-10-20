"""One-use native selections; resource authority belongs to the injected source registry."""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path

from poolhouse.json_objects import unique_object
from poolhouse.studio.store import Conflict, digest

from . import invite_routes
from .launch_secret import BYTES, HEADER
from .session import TTL_S

NATIVE = '/ui/studio/native-picks/'
TOKEN = re.compile(r'[A-Za-z0-9_-]{32,128}')
IDENTITY = re.compile(r'[a-f0-9]{32}')
ERROR = 'This file selection ended. Choose the source again in Studio.'


def _token(value):
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise ValueError(ERROR)
    return hashlib.sha256(value.encode()).hexdigest()


def _source(value):
    if (not isinstance(value, dict) or set(value) != {'id', 'title'}
            or not isinstance(value['id'], str) or not re.fullmatch(r'[A-Za-z0-9_:-]{1,256}', value['id'])
            or not isinstance(value['title'], str) or not 1 <= len(value['title']) <= 1024
            or any(char in value['title'] for char in '/\\') or any(ord(char) < 32 for char in value['title'])):
        raise ValueError('The source registry returned an invalid selection')
    return copy.deepcopy(value)


class NativePicks:
    """Persist nonce state; callbacks enforce actual authority and create resources idempotently."""

    def __init__(self, store, *, epoch, current, create_watch, clock=time.time):
        self.store, self.epoch, self.current, self.create_watch, self.clock = store, epoch, current, create_watch, clock
        self.boot = secrets.token_hex(16)
        self.responses = {}

    def _get(self, ident, nonce):
        if not isinstance(ident, str) or not IDENTITY.fullmatch(ident):
            raise ValueError(ERROR)
        row = self.store.graph.get_doc('studio.native-pick:' + ident)
        if not row or not hmac.compare_digest(row['nonce_hash'], _token(nonce)):
            raise PermissionError(ERROR)
        if row['boot'] != self.boot or self.clock() >= row['expires_at'] or not self.current(row['principal'], row['epoch']):
            row['state'] = 'expired'
            self.store.put('native-pick', row)
            raise PermissionError(ERROR)
        return row

    @staticmethod
    def _public(row):
        if row['state'] == 'complete':
            return {'status': 'complete', 'source': _source(row['source'])}
        if row['state'] == 'cancelled':
            return {'status': 'cancelled'}
        if row['state'] in {'choosing', 'completing'}:
            return {'status': 'choosing'}
        raise PermissionError(ERROR)

    def request(self, principal, mode, request_key):
        if not isinstance(mode, str) or mode not in {'file', 'folder'} or not isinstance(request_key, str) or not IDENTITY.fullmatch(request_key):
            raise ValueError('Choose a file or folder through Studio')
        if (not isinstance(principal, dict) or set(principal) != {'id', 'scope', 'owner'}
                or not all(isinstance(principal[key], str) and principal[key] for key in ('id', 'scope'))
                or type(principal['owner']) is not bool):
            raise PermissionError(ERROR)
        principal = copy.deepcopy(principal)
        epoch = self.epoch(principal)
        if not isinstance(epoch, str) or not epoch or not self.current(principal, epoch):
            raise PermissionError(ERROR)
        ident = digest({'principal': principal['id'], 'scope': principal['scope'], 'key': request_key})[:32]
        with self.store.lock:
            prior = self.store.graph.get_doc('studio.native-pick:' + ident)
            if prior:
                if prior['mode'] != mode or prior['principal'] != principal:
                    raise Conflict('This selection request already names another choice')
                response = self.responses.get(ident)
                if response is None:
                    raise PermissionError(ERROR)
                self._get(ident, response['nonce'])
                return copy.deepcopy(response)
            nonce = secrets.token_urlsafe(BYTES)
            self.store.put('native-pick', {'version': 1, 'id': ident, 'scope': principal['scope'], 'principal': principal,
                                         'epoch': epoch, 'mode': mode, 'state': 'issued', 'boot': self.boot,
                                         'nonce_hash': _token(nonce), 'expires_at': self.clock() + TTL_S})
            self.responses[ident] = {'request_id': ident, 'nonce': nonce}
            return copy.deepcopy(self.responses[ident])

    def begin(self, ident, nonce):
        with self.store.lock:
            row = self._get(ident, nonce)
            if row['state'] != 'issued':
                return self._public(row)
            claim = secrets.token_urlsafe(BYTES)
            row.update(state='choosing', claim_hash=_token(claim))
            self.store.put('native-pick', row)
            return {'status': 'choosing', 'mode': row['mode'], 'claim': claim}

    @staticmethod
    def _claim(row, claim):
        if not hmac.compare_digest(row.get('claim_hash', ''), _token(claim)):
            raise PermissionError(ERROR)

    def complete(self, ident, nonce, claim, path):
        if not isinstance(path, str) or not path or '\x00' in path or not Path(path).is_absolute() or '..' in Path(path).parts:
            raise ValueError('Choose the original source through the native file picker')
        with self.store.lock:
            row = self._get(ident, nonce)
            self._claim(row, claim)
            selection = digest({'mode': row['mode'], 'path': path})
            if row['state'] in {'complete', 'completing'} and row.get('selection') != selection:
                raise Conflict('This native selection already names another source')
            if row['state'] == 'complete':
                return self._public(row)
            if row['state'] not in {'choosing', 'completing'}:
                raise PermissionError(ERROR)
            # Persist the exact selection before the idempotent registry call. A lost acknowledgement
            # can only reconcile this same source; it cannot mint a different grant or reopen a picker.
            row.update(state='completing', selection=selection)
            self.store.put('native-pick', row)
            source = _source(self.create_watch(row['principal'], path, row['mode'], ident))
            if not self.current(row['principal'], row['epoch']):
                raise PermissionError(ERROR)
            row.update(state='complete', source=source)
            self.store.put('native-pick', row)
            return self._public(row)

    def cancel(self, ident, nonce, claim):
        with self.store.lock:
            row = self._get(ident, nonce)
            self._claim(row, claim)
            if row['state'] == 'completing':
                raise Conflict('This selection needs its original completion to be reconciled')
            if row['state'] == 'choosing':
                row['state'] = 'cancelled'
                self.store.put('native-pick', row)
            return self._public(row)


def _body(context, keys):
    length = int(context.header('Content-Length', '0') or 0)
    if not 0 <= length <= 64 * 1024:
        raise ValueError('Invalid selection request size')
    body = json.loads(context.handler.rfile.read(length) or b'{}', object_pairs_hook=unique_object)
    if not isinstance(body, dict) or set(body) != set(keys):
        raise ValueError('Invalid selection fields')
    return body


class NativePickRoutes:
    """Only exact native callbacks get the launch-secret guard; ordinary Studio remains guarded."""

    def public_route(self):
        if not self.path.startswith(NATIVE):
            return super().public_route()
        match = re.fullmatch(re.escape(NATIVE) + r'([a-f0-9]{32})/(begin|complete|cancel)', self.path)
        if not match:
            self.send(404, {'error': 'No such native selection'})
            return True
        if self.method != 'POST':
            self.send(405, {'error': 'POST required'})
            return True
        ui = self.ui
        if (ui.launch is None or not invite_routes.local(self.client_ip) or not ui.host_ok(self.host_header)
                or self.header('Origin') or self.header('Sec-Fetch-Site') or not ui.launch.matches(self.header(HEADER))):
            self.send(403, {'error': 'Use the native app to choose an original source'})
            return True
        picks = getattr(ui, 'studio_native_picks', None)
        if picks is None:
            self.send(409, {'error': 'Original source selection is not available yet'})
            return True
        ident, action = match.groups()
        keys = {'begin': ('nonce',), 'cancel': ('nonce', 'claim'), 'complete': ('nonce', 'claim', 'path')}[action]
        try:
            request = _body(self, keys)
            response = getattr(picks, action)(ident, **request)
            self.send(200, response, {'Cache-Control': 'no-store'})
        except PermissionError:
            self.send(403, {'error': ERROR})
        except (ValueError, OSError, KeyError, Conflict):
            self.send(409, {'error': ERROR})
        return True

    def route(self):
        if self.path != '/ui/studio/sources/pick':
            return super().route()
        if self.method != 'POST':
            self.send(405, {'error': 'POST required'})
            return True
        picks = getattr(self.ui, 'studio_native_picks', None)
        if picks is None:
            self.send(409, {'error': 'Original source selection is not available yet'})
            return True
        try:
            engine, principal = self._service()
            if not engine.authorize(principal, None, {}):
                raise PermissionError(ERROR)
            request = _body(self, ('mode', 'request_key'))
            self.send(200, picks.request(principal, **request), {'Cache-Control': 'no-store'})
        except PermissionError:
            self.send(403, {'error': ERROR})
        except (ValueError, OSError, KeyError, Conflict):
            self.send(409, {'error': ERROR})
        return True
