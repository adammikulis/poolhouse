"""Source-scoped software credentials and authenticated, inert Studio event ingress."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import ssl
import stat
import time
from contextlib import contextmanager, suppress
from pathlib import Path

from poolhouse import lock
from poolhouse.files import promote
from poolhouse.graph.guard import MAX_BODY
from poolhouse.json_objects import unique_object
from poolhouse.studio.store import Conflict

from .studio_epoch import directory

PREFIX = '/studio/ingest/'
HEADERS = {'event': 'X-Poolhouse-Event-ID', 'time': 'X-Poolhouse-Timestamp',
           'generation': 'X-Poolhouse-Generation', 'signature': 'X-Poolhouse-Signature'}
HEX = re.compile(r'[a-f0-9]{32}')
EVENT = re.compile(r'[A-Za-z0-9_.:-]{1,256}')
SHA = re.compile(r'[a-f0-9]{64}')
ERROR = 'This source event was not accepted.'
SKEW = 300
PROTOCOL = 'webhook-v1'
PRODUCER_PROTOCOL = 'producer-attempt-v1'
ATTEMPT_HEADER = 'X-Poolhouse-Attempt'


def _identity(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError(ERROR)
    return value


def _private(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077
            or info.st_uid != os.getuid() or info.st_size > 4096):
        raise PermissionError(ERROR)


def _frame(source, generation, event, timestamp, raw):
    return ('poolhouse-studio-webhook-v1\nPOST\n' + PREFIX + source + '\n' + timestamp
            + '\n' + event + '\n' + hashlib.sha256(raw).hexdigest() + '\n' + generation).encode('ascii')


def _attempt_frame(source, fields, raw):
    """Bind a receiver-owned admission attempt independently of timestamp resolution."""
    if not isinstance(fields.get('attempt'), str) or not SHA.fullmatch(fields['attempt']):
        raise PermissionError(ERROR)
    return ('poolhouse-studio-producer-webhook-v1\nPOST\n' + PREFIX + source + '\n' + fields['time']
            + '\n' + fields['event'] + '\n' + hashlib.sha256(raw).hexdigest() + '\n'
            + fields['generation'] + '\n' + fields['attempt']).encode('ascii')


def _payload(raw):
    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(ERROR)
        return number
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BODY:
        raise ValueError(ERROR)
    value = json.loads(raw, object_pairs_hook=unique_object, parse_float=finite,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError(ERROR)))
    if not isinstance(value, dict):
        raise ValueError(ERROR)
    return value


class WebhookSources:
    """Keep raw keys inside software provisioning; callbacks own source and execution authority."""

    def __init__(self, root, *, current, admit, configure, clock=time.time):
        self.root = Path(root).absolute()
        self.current, self.admit, self.configure, self.clock = current, admit, configure, clock
        self.provider_current = lambda source, generation: False
        with self._directory():
            pass

    @contextmanager
    def _directory(self):
        with directory(self.root) as parent:
            with suppress(FileExistsError):
                os.mkdir('studio-webhooks', mode=0o700, dir_fd=parent)
            descriptor = os.open('studio-webhooks', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                info = os.fstat(descriptor)
                if info.st_mode & 0o077 or info.st_uid != os.getuid():
                    raise PermissionError(ERROR)
                yield descriptor
                named = os.stat('studio-webhooks', dir_fd=parent, follow_symlinks=False)
                if (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino):
                    raise PermissionError(ERROR)
            finally:
                os.close(descriptor)

    def _read(self, descriptor, source):
        fd = os.open(source + '.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(fd)
            _private(before)
            row = json.loads(stream.read(4097), object_pairs_hook=unique_object)
            after = os.fstat(fd)
            named = os.stat(source + '.json', dir_fd=descriptor, follow_symlinks=False)
            def signature(info):
                return info.st_dev, info.st_ino, info.st_ctime_ns, info.st_mtime_ns, info.st_size, info.st_nlink
            if signature(before) != signature(after) or signature(before) != signature(named):
                raise PermissionError(ERROR)
        if (not isinstance(row, dict) or set(row) != {'version', 'source', 'scope', 'generation', 'key_id', 'secret'}
                or type(row['version']) is not int or row['version'] != 1 or row['source'] != source
                or not isinstance(row['scope'], str) or not row['scope']
                or not isinstance(row['secret'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', row['secret'])):
            raise PermissionError(ERROR)
        _identity(row['generation'])
        _identity(row['key_id'])
        return row

    def register(self, principal, source, generation):
        source, generation = _identity(source), _identity(generation)
        if self.configure(principal, source, generation) is not True:
            raise PermissionError(ERROR)
        with self._directory() as descriptor:
            fd = os.open(source + '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
            acquired = False
            try:
                _private(os.fstat(fd))
                if not lock.take(fd):
                    raise BlockingIOError(ERROR)
                acquired = True
                try:
                    row = self._read(descriptor, source)
                except FileNotFoundError:
                    row = None
                if row and row['scope'] != principal['scope']:
                    raise PermissionError(ERROR)
                if not row or row['generation'] != generation:
                    row = {'version': 1, 'source': source, 'scope': principal['scope'], 'generation': generation,
                           'key_id': secrets.token_hex(16), 'secret': secrets.token_urlsafe(32)}
                    self._write(descriptor, source, row)
                if self.configure(principal, source, generation) is not True:
                    raise PermissionError(ERROR)
                return {'key_id': row['key_id'], 'generation': generation}
            finally:
                if acquired:
                    lock.release(fd)
                os.close(fd)

    @staticmethod
    def _write(descriptor, source, row):
        name = source + '.' + secrets.token_hex(16) + '.part'
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(json.dumps(row, allow_nan=False).encode())
                stream.flush()
                os.fsync(stream.fileno())
            promote(name, source + '.json', directory_fd=descriptor)
            os.fsync(descriptor)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(name, dir_fd=descriptor)

    def configure_sender(self, principal, source, generation, deliver):
        """Provision an authorized software connector, never an HTTP-supplied callback or browser."""
        source, generation = _identity(source), _identity(generation)
        if self.configure(principal, source, generation) is not True:
            raise PermissionError(ERROR)
        with self._directory() as descriptor:
            row = self._read(descriptor, source)
        if row['scope'] != principal['scope'] or row['generation'] != generation:
            raise PermissionError(ERROR)
        if deliver({'source': source, 'generation': generation, 'secret': row['secret']}) is not True:
            raise PermissionError(ERROR)
        if self.configure(principal, source, generation) is not True:
            raise PermissionError(ERROR)
        return {'key_id': row['key_id'], 'generation': generation}

    def provision_registered(self, source, generation, deliver, *, scope):
        """A fixed daemon registry proves software delegation, never a stored owner session."""
        source, generation = _identity(source), _identity(generation)
        if self.provider_current(source, generation) is not True:
            raise PermissionError(ERROR)
        with self._directory() as descriptor:
            row = self._read(descriptor, source)
        if row['scope'] != scope or row['generation'] != generation or deliver({key: row[key] for key in ('source', 'generation', 'secret')}) is not True:
            raise PermissionError(ERROR)
        if self.provider_current(source, generation) is not True:
            raise PermissionError(ERROR)

    def _active(self, source, generation):
        current = self.current(source)
        if (not isinstance(current, dict) or current.get('enabled') is not True
                or current.get('protocol') not in {PROTOCOL, PRODUCER_PROTOCOL}
                or current.get('generation') != generation or not current.get('subscription')
                or not isinstance(current.get('scope'), str) or not current['scope']):
            raise PermissionError(ERROR)
        return current

    def accept(self, source, headers, raw):
        source = _identity(source)
        generation = _identity(headers['generation'])
        event, timestamp, signature = headers['event'], headers['time'], headers['signature']
        if (not isinstance(event, str) or not EVENT.fullmatch(event) or not isinstance(timestamp, str)
                or not re.fullmatch(r'[0-9]{1,12}', timestamp) or str(int(timestamp)) != timestamp
                or abs(self.clock() - int(timestamp)) > SKEW
                or not isinstance(signature, str) or not SHA.fullmatch(signature)):
            raise PermissionError(ERROR)
        # Do not read a key for an unbound, inactive or revoked source.
        current = self._active(source, generation)
        with self._directory() as descriptor:
            row = self._read(descriptor, source)
        if row['scope'] != current['scope'] or row['generation'] != generation:
            raise PermissionError(ERROR)
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BODY:
            raise ValueError(ERROR)
        attempt = headers.get('attempt')
        if current['protocol'] == PRODUCER_PROTOCOL:
            frame = _attempt_frame(source, headers, raw)
        else:
            if attempt is not None:
                raise PermissionError(ERROR)
            frame = _frame(source, generation, event, timestamp, raw)
        expected = hmac.new(row['secret'].encode(), frame, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise PermissionError(ERROR)
        payload = _payload(raw)
        if self._active(source, generation) != current:
            raise PermissionError(ERROR)
        incoming = {'id': event, 'generation': generation, 'payload': payload,
                    'digest': hashlib.sha256(raw).hexdigest()}
        if current['protocol'] == PRODUCER_PROTOCOL:
            incoming['attempt'] = attempt
        result = self.admit(source, incoming)
        if (not isinstance(result, dict) or not isinstance(result.get('id'), str)
                or not SHA.fullmatch(result['id']) or result.get('state') not in {'queued', 'running', 'quarantined', 'done', 'failed', 'interrupted', 'cancelled'}):
            raise ValueError(ERROR)
        return {'id': result['id'], 'status': result['state']}


def public(ui, handler, raw):
    """An existing TLS listener may dispatch this exact path without granting UI or member authority."""
    if not handler.path.startswith(PREFIX):
        return False
    path = re.fullmatch(re.escape(PREFIX) + r'([a-f0-9]{32})', handler.path)
    status, result = 403, {'error': ERROR}
    if not path:
        status = 404
    elif handler.command != 'POST':
        status = 405
    elif not isinstance(handler.connection, ssl.SSLSocket) or handler.connection.version() != 'TLSv1.3':
        status = 403
    elif getattr(ui, 'studio_webhooks', None) is None:
        status = 503
    else:
        try:
            headers = {}
            for key, name in HEADERS.items():
                values = handler.headers.get_all(name, [])
                if len(values) != 1:
                    raise PermissionError(ERROR)
                headers[key] = values[0]
            attempts = handler.headers.get_all(ATTEMPT_HEADER, [])
            if len(attempts) > 1:
                raise PermissionError(ERROR)
            headers['attempt'] = attempts[0] if attempts else None
            if (handler.headers.get_all('Transfer-Encoding') or handler.headers.get_all('Content-Encoding')
                    or len(handler.headers.get_all('Content-Length', [])) != 1
                    or handler.headers.get('Content-Length') != str(len(raw or b''))
                    or len(handler.headers.get_all('Content-Type', [])) != 1
                    or handler.headers.get('Content-Type').lower() != 'application/json'):
                raise ValueError(ERROR)
            result = ui.studio_webhooks.accept(path[1], headers, raw)
            status = 200
        except PermissionError:
            status = 403
        except BlockingIOError:
            status = 429
        except Conflict:
            status = 409
        except (ValueError, OSError, KeyError, TypeError, RecursionError):
            status = 400
    handler._send(status, result)
    return True
