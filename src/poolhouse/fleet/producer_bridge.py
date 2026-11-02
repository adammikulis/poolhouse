"""Private bridge capabilities authenticate data requests, never software identities."""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import secrets
import threading
from collections import deque

from poolhouse.studio.producer_log import EVENT_ID, wire_record
from poolhouse.studio.store import digest

from .job_records import MAX_RECORDS

DOMAIN = b'poolhouse/producer-bridge/v2\n'
_ACTIONS = {'bridge-publish', 'bridge-receipt', 'bridge-retry'}
_FIELDS = {'version', 'action', 'binding', 'event_id', 'raw', 'wire_digest', 'nonce', 'retry_key'}


def record_bytes(record):
    """The SDK object has one exact wire representation before durable admission."""
    raw = json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    wire_record(raw)
    return raw


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _checked(value):
    if (not isinstance(value, dict) or set(value) != _FIELDS
            or type(value['version']) is not int or value['version'] != 2
            or not isinstance(value['action'], str) or value['action'] not in _ACTIONS
            or not isinstance(value['binding'], dict)
            or not isinstance(value['event_id'], str) or not EVENT_ID.fullmatch(value['event_id'])
            or not isinstance(value['nonce'], str) or len(value['nonce']) != 64
            or any(character not in '0123456789abcdef' for character in value['nonce'])):
        raise ValueError('Invalid private producer request.')
    retry_key = value['retry_key']
    if value['action'] == 'bridge-retry':
        if (not isinstance(retry_key, str) or len(retry_key) != 32
                or any(character not in '0123456789abcdef' for character in retry_key)):
            raise ValueError('A stable retry command identity is required.')
    elif retry_key is not None:
        raise ValueError('Only deliberate retry can supply a retry command identity.')
    if value['action'] == 'bridge-publish':
        if not isinstance(value['raw'], str):
            raise ValueError('Exact publication bytes are required.')
        _, checksum = wire_record(value['raw'].encode('utf-8'))
        if value['wire_digest'] != checksum:
            raise ValueError('Publication bytes changed.')
    elif value['raw'] is not None or value['wire_digest'] is not None:
        raise ValueError('Receipt and retry cannot supply replacement bytes.')
    return copy.deepcopy(value)


def signed_request(secret, binding, action, event_id, payload=None):
    """Called only inside the owned bridge with its inherited private capability."""
    if not isinstance(secret, bytes) or len(secret) != 32:
        raise ValueError('An inherited private bridge capability is required.')
    raw = payload if action == 'bridge-publish' else None
    retry_key = payload if action == 'bridge-retry' else None
    if ((action == 'bridge-publish' and not isinstance(raw, bytes))
            or (action == 'bridge-receipt' and payload is not None)):
        raise ValueError('Invalid private producer payload.')
    value = {'version': 2, 'action': action, 'binding': copy.deepcopy(binding),
             'event_id': event_id, 'raw': raw.decode('utf-8') if raw is not None else None,
             'wire_digest': hashlib.sha256(raw).hexdigest() if raw is not None else None,
             'nonce': secrets.token_hex(32), 'retry_key': retry_key}
    value = _checked(value)
    return {**value, 'mac': hmac.new(secret, DOMAIN + _canonical(value), hashlib.sha256).hexdigest()}


class BridgeCapability:
    """RAM-only proof retained with one actual owned child and immutable source binding.

    The factory must supply the retained Popen-generation check and full current
    source/epoch/receiver/grant checks. A kernel process alone never grants a source.
    The bounded nonce cache supplements the durable source/event ledger; eviction
    cannot authorize replay because that ledger never resends an attempted event.
    """

    def __init__(self, binding, *, owned, current):
        if not isinstance(binding, dict) or not binding:
            raise ValueError('A persisted immutable source binding is required.')
        # Reject nonfinite/nonserializable private bindings before any capability exists.
        digest(binding)
        self.binding = copy.deepcopy(binding)
        self.owned, self.current = owned, current
        self.secret = secrets.token_bytes(32)
        self.lock = threading.RLock()
        self.nonces, self.order = set(), deque()
        self.active = False
        self.revoked = False

    def _allowed(self, process):
        if (self.revoked or self.owned(copy.deepcopy(process)) is not True or self.revoked
                or self.current(copy.deepcopy(self.binding)) is not True or self.revoked):
            raise PermissionError('The owned producer source is no longer authorized.')

    def activate(self, process, acknowledgement):
        """Only owner-intent code may activate after persisting the actual child binding."""
        with self.lock:
            self._allowed(process)
            if self.revoked:
                raise PermissionError('The private producer capability was revoked.')
            if acknowledgement != digest(self.binding):
                raise PermissionError('Producer activation does not match the persisted source.')
            self._allowed(process)
            self.active = True

    def revoke(self):
        with self.lock:
            self.active = False
            self.revoked = True

    def verify(self, process, request):
        """Authenticate before the daemon interprets or stages any record bytes."""
        with self.lock:
            self._allowed(process)
            if not self.active or not isinstance(request, dict) or set(request) != _FIELDS | {'mac'}:
                raise PermissionError('No active private producer capability.')
            value = _checked({key: request[key] for key in _FIELDS})
            if value['binding'] != self.binding:
                raise PermissionError('Producer request names a different source binding.')
            expected = hmac.new(self.secret, DOMAIN + _canonical(value), hashlib.sha256).hexdigest()
            if not isinstance(request['mac'], str) or not hmac.compare_digest(expected, request['mac']):
                raise PermissionError('Invalid private producer proof.')
            if value['nonce'] in self.nonces:
                raise PermissionError('This private producer request was already consumed.')
            self._allowed(process)
            self.nonces.add(value['nonce'])
            self.order.append(value['nonce'])
            if len(self.order) > MAX_RECORDS:
                self.nonces.remove(self.order.popleft())
            return value

    def result_current(self, process):
        with self.lock:
            if not self.active:
                raise PermissionError('The private producer capability was revoked.')
            self._allowed(process)
            return True
