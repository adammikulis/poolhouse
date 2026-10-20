"""Purpose-bound native selections; injected callbacks retain resource authority."""
from __future__ import annotations

import copy
import hashlib
import hmac
import re
import secrets
import time
from pathlib import Path

from poolhouse.studio.store import Conflict, digest

from .launch_secret import BYTES
from .session import TTL_S

TOKEN = re.compile(r'[A-Za-z0-9_-]{32,128}')
IDENTITY = re.compile(r'[a-f0-9]{32}')
ERROR = 'This selection ended. Choose it again.'


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

    def __init__(self, records, *, epoch, current, create_resource, clock=time.time):
        purpose = records.purpose
        if purpose not in {"studio-source", "coding-project"}:
            raise ValueError("Unsupported native selection purpose")
        self.records, self.purpose = records, purpose
        self.epoch, self.current, self.create_resource, self.clock = epoch, current, create_resource, clock
        self.boot = secrets.token_hex(16)
        self.responses = {}

    def _get(self, ident, nonce):
        if not isinstance(ident, str) or not IDENTITY.fullmatch(ident):
            raise ValueError(ERROR)
        row = self.records.read(ident)
        if not row or row.get('version') != 1 or row.get('purpose') != self.purpose or not hmac.compare_digest(row['nonce_hash'], _token(nonce)):
            raise PermissionError(ERROR)
        if row['boot'] != self.boot or self.clock() >= row['expires_at'] or not self.current(row['principal'], row['epoch']):
            row['state'] = 'expired'
            self.records.write(row)
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
        if (self.purpose == 'coding-project' and mode != 'folder') or not isinstance(mode, str) or mode not in {'file', 'folder'} or not isinstance(request_key, str) or not IDENTITY.fullmatch(request_key):
            raise ValueError('Choose a file or folder in the app')
        if (not isinstance(principal, dict) or set(principal) != {'id', 'scope', 'owner'}
                or not all(isinstance(principal[key], str) and principal[key] for key in ('id', 'scope'))
                or type(principal['owner']) is not bool):
            raise PermissionError(ERROR)
        principal = copy.deepcopy(principal)
        epoch = self.epoch(principal)
        if not isinstance(epoch, str) or not epoch or not self.current(principal, epoch):
            raise PermissionError(ERROR)
        ident = digest({'purpose': self.purpose, 'principal': principal['id'], 'scope': principal['scope'], 'key': request_key})[:32]
        with self.records.lock:
            prior = self.records.read(ident)
            if prior:
                if prior['mode'] != mode or prior['principal'] != principal:
                    raise Conflict('This selection request already names another choice')
                response = self.responses.get(ident)
                if response is None:
                    raise PermissionError(ERROR)
                self._get(ident, response['nonce'])
                return copy.deepcopy(response)
            nonce = secrets.token_urlsafe(BYTES)
            self.records.write({'version': 1, 'purpose': self.purpose, 'id': ident, 'scope': principal['scope'], 'principal': principal,
                                         'epoch': epoch, 'mode': mode, 'state': 'issued', 'boot': self.boot,
                                         'nonce_hash': _token(nonce), 'expires_at': self.clock() + TTL_S})
            self.responses[ident] = {'request_id': ident, 'nonce': nonce}
            return copy.deepcopy(self.responses[ident])

    def begin(self, ident, nonce):
        with self.records.lock:
            row = self._get(ident, nonce)
            if row['state'] != 'issued':
                return self._public(row)
            claim = secrets.token_urlsafe(BYTES)
            row.update(state='choosing', claim_hash=_token(claim))
            self.records.write(row)
            return {'status': 'choosing', 'mode': row['mode'], 'claim': claim}

    @staticmethod
    def _claim(row, claim):
        if not hmac.compare_digest(row.get('claim_hash', ''), _token(claim)):
            raise PermissionError(ERROR)

    def complete(self, ident, nonce, claim, path):
        if not isinstance(path, str) or not path or '\x00' in path or not Path(path).is_absolute() or '..' in Path(path).parts:
            raise ValueError('Choose the original source through the native file picker')
        with self.records.lock:
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
            self.records.write(row)
            source = _source(self.create_resource(row['principal'], path, row['mode'], ident))
            if not self.current(row['principal'], row['epoch']):
                raise PermissionError(ERROR)
            row.update(state='complete', source=source)
            self.records.write(row)
            return self._public(row)

    def cancel(self, ident, nonce, claim):
        with self.records.lock:
            row = self._get(ident, nonce)
            self._claim(row, claim)
            if row['state'] == 'completing':
                raise Conflict('This selection needs its original completion to be reconciled')
            if row['state'] == 'choosing':
                row['state'] = 'cancelled'
                self.records.write(row)
            return self._public(row)


