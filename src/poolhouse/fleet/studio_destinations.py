"""Owner-registered delivery endpoints and explicit historical delivery actions.

Registry membership is one resource proof, never execution authority. The stream factory
checks the current service grant and its pinned manifest before using ``current``.
"""
from __future__ import annotations

import copy
import ipaddress
import json
import re
import secrets
import time

from poolhouse import httpguard
from poolhouse.json_objects import unique_object
from poolhouse.studio.store import Conflict, digest

_ID = re.compile(r'[a-f0-9]{32}\Z')
_DELIVERY = re.compile(r'[a-f0-9]{64}\Z')
_PUBLIC = ('id', 'revision', 'url', 'label', 'enabled')


def _configuration(body):
    label, url = body.get('label'), body.get('url')
    if (not isinstance(label, str) or not 1 <= len(label) <= 200 or label != label.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in label)):
        raise ValueError('Give the destination a short, single-line name.')
    try:
        if not isinstance(url, str) or len(url) > 4096 or url != url.strip():
            raise ValueError()
        parts, host, _ = httpguard.split(url)
        if parts.scheme != 'https' or parts.fragment or '@' in parts.netloc or '%' in host or not host.isascii():
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not httpguard.allowed_address(str(address)):
            raise ValueError()
        if host == 'localhost' or host.endswith('.localhost'):
            raise ValueError()
    except (ValueError, TypeError, OSError):
        raise ValueError('Choose an exact public HTTPS destination without embedded credentials.') from None
    return {'label': label, 'url': url}


def _public(row):
    return {key: copy.deepcopy(row[key]) for key in _PUBLIC}


class Destinations:
    """Revisioned endpoint configuration; registering never resolves or contacts a host."""

    def __init__(self, service):
        self.service, self.store = service, service.engine.store
        self.lock, self.graph = self.store.lock, self.store.graph
        self.graph.query('CREATE NODE TABLE IF NOT EXISTS StudioDestinationIndex(id STRING, scope STRING, PRIMARY KEY(id))')

    def _owner(self, principal):
        return self.service.authority._owner(principal)

    def _get(self, principal, ident):
        if not isinstance(ident, str) or not _ID.fullmatch(ident):
            raise KeyError('No such delivery destination.')
        return self.store.get('destination', ident, principal['scope'])

    def get(self, principal, ident):
        self._owner(principal)
        with self.lock:
            row = self._get(principal, ident)
            self._owner(principal)
            return _public(row)

    def list(self, principal):
        self._owner(principal)
        with self.lock:
            rows = self.graph.query('MATCH (a:StudioDestinationIndex) WHERE a.scope=$scope RETURN a.id AS id',
                                    {'scope': principal['scope']})
            result = [_public(self._get(principal, row['id'])) for row in rows]
            self._owner(principal)
            return result

    def snapshot(self, principal, ident):
        row = self.get(principal, ident)
        if row['enabled'] is not True:
            raise PermissionError('This delivery destination was revoked.')
        return {key: row[key] for key in ('id', 'revision', 'url')}

    def current(self, principal, snapshot):
        """Pure exact registry proof; caller must separately verify grant membership."""
        if (not isinstance(snapshot, dict) or set(snapshot) != {'id', 'revision', 'url'}
                or type(snapshot['revision']) is not int or snapshot['revision'] < 1):
            return False
        try:
            with self.lock:
                row = self._get(principal, snapshot['id'])
                return row['enabled'] is True and all(row[key] == snapshot[key] for key in snapshot)
        except (KeyError, ValueError, TypeError):
            return False

    def _command(self, principal, action, ident, body):
        identity = body.get('idempotency_key')
        if not isinstance(identity, str) or not identity or len(identity) > 256:
            raise ValueError('A destination action identity is required.')
        key = 'studio.destination-command:' + digest({'scope': principal['scope'], 'principal': principal['id'],
                                                     'action': action, 'destination': ident, 'key': identity})
        previous = self.graph.get_doc(key)
        if previous:
            if previous['request'] != digest(body):
                raise Conflict('This action identity already names another destination request.')
            version = self.graph.get_doc(previous['version'])
            if not version or version['scope'] != principal['scope']:
                raise RuntimeError('Destination history requires reconciliation.')
            return key, _public(version)
        return key, None

    def _record(self, principal, action, key, body, row):
        self._owner(principal)
        self.store.put('destination', row)
        version = 'studio.destination-version:' + row['id'] + ':' + str(row['revision'])
        self.graph.put_doc(version, row)
        self.graph.query('MERGE (a:StudioDestinationIndex {id:$id}) SET a.scope=$scope',
                         {'id': row['id'], 'scope': row['scope']})
        self.graph.put_doc(key, {'request': digest(body), 'version': version})
        self.graph.put_doc(key + ':audit', {'actor': 'studio-daemon', 'principal': principal['id'],
                                           'destination': row['id'], 'revision': row['revision'], 'action': action})
        return _public(row)

    def mutate(self, principal, action, body, ident=None):
        self._owner(principal)
        fields = {'label', 'url', 'idempotency_key'} if action == 'create' else (
            {'label', 'url', 'revision', 'idempotency_key'} if action == 'update' else {'revision', 'idempotency_key'})
        if action not in {'create', 'update', 'revoke'} or not isinstance(body, dict) or set(body) != fields:
            raise ValueError('Choose the registered destination and its current revision.')
        configuration = _configuration(body) if action != 'revoke' else {}
        if action != 'create' and (type(body['revision']) is not int or body['revision'] < 1):
            raise ValueError('A current destination revision is required.')
        with self.lock, self.graph.transaction():
            self._owner(principal)
            key, previous = self._command(principal, action, ident, body)
            if previous is not None:
                return previous
            if action == 'create':
                row = {**configuration, 'id': secrets.token_hex(16), 'scope': principal['scope'],
                       'revision': 1, 'enabled': True, 'created_at': time.time()}
            else:
                row = self._get(principal, ident)
                if row['revision'] != body['revision']:
                    raise Conflict('This destination changed elsewhere. Reload its current revision.')
                if action == 'update' and not row['enabled']:
                    raise PermissionError('Register a new destination after revocation.')
                changed = row['enabled'] if action == 'revoke' else any(row[key] != value for key, value in configuration.items())
                if changed:
                    row.update(configuration, revision=row['revision'] + 1, enabled=action != 'revoke')
            return self._record(principal, action, key, body, row)


def create(ui, service):
    registry = Destinations(service)
    service.destination_registry = ui.studio_destinations = registry
    return registry


def _body(handler):
    try:
        length = int(handler.header('Content-Length', '0') or 0)
        if not 0 <= length <= 16384:
            raise ValueError()
        body = json.loads(handler.handler.rfile.read(length) or b'{}', object_pairs_hook=unique_object)
        if not isinstance(body, dict):
            raise ValueError()
        return body
    except (ValueError, TypeError, UnicodeError):
        raise ValueError('Send a bounded JSON destination or delivery action.') from None


def _delivery_owner(service, principal, row):
    service.authority._owner(principal)
    if row['scope'] != principal['scope'] or service.delivery_owner(principal, row) is not True:
        raise PermissionError('Current owner access to this exact delivery is required.')
    return True


def public_delivery(row):
    # Output bodies, principal/grant records and transport internals never leave this adapter.
    result = {key: copy.deepcopy(row[key]) for key in ('id', 'event', 'subscription', 'state', 'not_sent') if key in row}
    result['destination'] = {key: row['destination'][key] for key in ('id', 'revision', 'url')}
    result['request_digest'] = row['envelope']['request_digest']
    if 'reconciliation' in row:
        result['reconciliation'] = {key: row['reconciliation'][key] for key in (
            'evidence', 'outcome', 'actor', 'weaker_than_receiver_receipt') if key in row['reconciliation']}
    return result


def delivery_view(service, principal, row):
    _delivery_owner(service, principal, row)
    result = public_delivery(row)
    result['can_reconcile'] = row['state'] == 'interrupted'
    try:
        result['can_retry'] = row['state'] == 'blocked' and row.get('not_sent') is True and service.delivery_current(row) is True
    except (ValueError, OSError, RuntimeError, KeyError):
        result['can_retry'] = False
    result['reason'] = ('Confirm the receiver outcome before retrying.' if result['can_reconcile'] else
                        'Ready for an explicit retry.' if result['can_retry'] else
                        'The original permission or destination must remain current.' if row['state'] == 'blocked' else
                        'Skipped by the workflow condition; nothing was sent.' if row['state'] == 'skipped' else
                        'Delivery is complete.' if row['state'] == 'done' else 'Waiting for delivery confirmation.')
    return result


def _delivery_request(handler, action):
    body = _body(handler)
    fields = {'delivery_id', 'request_digest', 'destination_revision', 'idempotency_key'}
    if action == 'reconcile':
        fields |= {'outcome', 'evidence'}
    if (set(body) != fields or not isinstance(body.get('delivery_id'), str)
            or not _DELIVERY.fullmatch(body['delivery_id']) or not isinstance(body.get('request_digest'), str)
            or not _DELIVERY.fullmatch(body['request_digest']) or type(body.get('destination_revision')) is not int
            or body['destination_revision'] < 1 or not isinstance(body.get('idempotency_key'), str)
            or not 1 <= len(body['idempotency_key']) <= 256):
        raise ValueError('Select the exact recorded delivery, digest and destination revision.')
    return body


def route(handler, service, principal):
    parts = handler.path.removeprefix('/ui/studio/').split('/')
    if not parts or parts[0] not in {'destinations', 'deliveries'} or not handler.path.startswith('/ui/studio/'):
        return False
    service.authority._owner(principal)
    registry = service.destination_registry
    if parts[0] == 'destinations':
        if len(parts) == 1 and handler.method == 'GET':
            handler.send(200, {'destinations': registry.list(principal)})
        elif len(parts) == 1 and handler.method == 'POST':
            handler.send(201, {'destination': registry.mutate(principal, 'create', _body(handler))})
        elif len(parts) == 2 and handler.method == 'GET':
            handler.send(200, {'destination': registry.get(principal, parts[1])})
        elif len(parts) == 2 and handler.method in {'PUT', 'DELETE'}:
            handler.send(200, {'destination': registry.mutate(principal, 'update' if handler.method == 'PUT' else 'revoke', _body(handler), parts[1])})
        else:
            return False
        return True
    if len(parts) not in {2, 3} or not _DELIVERY.fullmatch(parts[1]):
        raise KeyError('No such delivery.')
    row = service.store.get('delivery', parts[1], principal['scope'])
    _delivery_owner(service, principal, row)
    if len(parts) == 2 and handler.method == 'GET':
        handler.send(200, {'delivery': delivery_view(service, principal, row)})
    elif len(parts) == 3 and handler.method == 'POST' and parts[2] in {'retry', 'reconcile'}:
        def owner(caller, value):
            return _delivery_owner(service, caller, value)
        if parts[2] == 'retry':
            if service.delivery_current(row) is not True:
                raise PermissionError('The original delivery grant and destination must still be current.')
            row = service.outbox.retry(principal, row['id'], _delivery_request(handler, parts[2]), owner=owner)
        else:
            row = service.outbox.reconcile(principal, row['id'], _delivery_request(handler, parts[2]), owner=owner)
        service.delivery_settled(principal, row)
        handler.send(202 if parts[2] == 'retry' else 200, {'delivery': delivery_view(service, principal, row)})
    else:
        return False
    return True
