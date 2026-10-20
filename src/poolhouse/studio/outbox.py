"""Durable one-attempt delivery intents; unknown sends are never replayed."""
from __future__ import annotations

import copy
import hashlib
import json
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor

from poolhouse.graph.guard import MAX_BODY

from .store import Conflict, digest


def body_digest(body: dict) -> str:
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(encoded) > MAX_BODY:
        raise ValueError('The selected outputs exceed the delivery body size.')
    return hashlib.sha256(encoded).hexdigest()


class Outbox:
    """CAS-owned sends and a separate current-authorization check at every boundary."""

    def __init__(self, store, *, authorize, send) -> None:
        self.store, self.graph, self.lock = store, store.graph, store.lock
        self.authorize, self.send = authorize, send
        self.instance = secrets.token_hex(32)
        self.closed = False
        self.stop = threading.Event()
        self.pool = ThreadPoolExecutor(thread_name_prefix='studio-delivery')
        self.futures = {}
        self.thread = None
        self.graph.query('CREATE NODE TABLE IF NOT EXISTS StudioOutboxActive(id STRING, scope STRING, state STRING, PRIMARY KEY(id))')
        self._recover()

    def _put(self, row: dict) -> dict:
        self.store.put('delivery', row)
        if row['state'] in {'done', 'skipped'}:
            self.graph.query('MATCH (a:StudioOutboxActive {id:$id}) DELETE a', {'id': row['id']})
        else:
            self.graph.query('MERGE (a:StudioOutboxActive {id:$id}) SET a.scope=$scope, a.state=$state',
                             {key: row[key] for key in ('id', 'scope', 'state')})
        return copy.deepcopy(row)

    def _indexed(self, state: str) -> list[dict]:
        rows = self.graph.query('MATCH (a:StudioOutboxActive) WHERE a.state=$state RETURN a.id AS id, a.scope AS scope', {'state': state})
        result = []
        for pointer in rows:
            try:
                row = self.store.get('delivery', pointer['id'], pointer['scope'])
                if row['state'] != state or row['id'] != pointer['id']:
                    raise ValueError('Delivery index identity changed.')
                result.append(row)
            except (ValueError, KeyError, TypeError):
                self.graph.put_doc('studio.outbox-index-error:' + pointer['id'],
                                   {'error': 'Delivery index requires reconciliation.', 'delivery': pointer['id']})
        return result

    def _recover(self) -> None:
        with self.lock, self.graph.transaction():
            for row in self._indexed('intent'):
                row.update(state='interrupted', reason='An earlier delivery outcome is unconfirmed.')
                self._put(row)

    def stage(self, event: dict, destination: dict, body: dict, *, skipped: bool = False) -> dict:
        if type(skipped) is not bool:
            raise ValueError('A delivery skip must come from its actual workflow state.')
        checksum = body_digest(body)
        ident = digest({'event': event['id'], 'subscription': event['subscription'],
                        'destination': destination['id'], 'revision': destination['revision']})
        fixed = {'id': ident, 'scope': event['scope'], 'event': event['id'], 'subscription': event['subscription'],
                 'principal': event['principal'], 'destination': destination, 'skipped': skipped,
                 'envelope': {'delivery_id': ident, 'request_digest': checksum, 'body': body}}
        with self.lock, self.graph.transaction():
            previous = self.graph.get_doc('studio.delivery:' + ident)
            if previous:
                if any(previous.get(key) != value for key, value in fixed.items()):
                    raise Conflict('This delivery identity already names different content.')
                return copy.deepcopy(previous)
            row = {**copy.deepcopy(fixed), 'state': 'skipped' if skipped else 'queued', 'attempt': None, 'not_sent': True}
            return self._put(row)

    def _allowed(self, row: dict) -> bool:
        return not self.closed and self.authorize(row) is True

    def attempt(self, ident: str, scope: str) -> dict:
        with self.lock, self.graph.transaction():
            row = self.store.get('delivery', ident, scope)
            if row['state'] != 'queued':
                return row
            try:
                if not self._allowed(row):
                    row.update(state="blocked", not_sent=True, reason="Current delivery authority is unavailable.")
                    return self._put(row)
            except (ValueError, OSError, RuntimeError):
                row.update(state='blocked', not_sent=True, reason='Current delivery authority is unavailable.')
                return self._put(row)
            token = secrets.token_hex(32)
            row.update(state='intent', attempt={'instance': self.instance, 'token': token}, not_sent=False)
            self._put(row)
        try:
            result = self.send(row['destination'], row['envelope'], authorize=lambda: self._allowed(row))
        except (ValueError, OSError, RuntimeError):
            result = {'state': 'interrupted', 'reason': 'Delivery outcome is unconfirmed.'}
        try:
            if not self._allowed(row) and isinstance(result, dict) and result.get('state') == 'done':
                result = {'state': 'interrupted', 'reason': 'Authority changed before receipt confirmation.'}
        except (ValueError, OSError, RuntimeError):
            result = {'state': 'interrupted', 'reason': 'Authority changed before receipt confirmation.'}
        if not isinstance(result, dict) or result.get('state') not in {'done', 'blocked', 'interrupted'}:
            result = {'state': 'interrupted', 'reason': 'Delivery outcome is unconfirmed.'}
        with self.lock, self.graph.transaction():
            current = self.store.get('delivery', ident, scope)
            if current['state'] != 'intent' or current.get('attempt') != {'instance': self.instance, 'token': token}:
                return current
            current.update(state=result['state'], outcome=copy.deepcopy(result), not_sent=result['state'] == 'blocked')
            return self._put(current)

    def cancel_event(self, event: dict) -> list[dict]:
        """Stop admission; a send intent remains unknown even when cancellation is requested."""
        with self.lock, self.graph.transaction():
            rows = []
            for ident in event.get('deliveries', []):
                row = self.store.get('delivery', ident, event['scope'])
                if row['event'] != event['id'] or row['principal'] != event['principal']:
                    raise PermissionError('The event delivery identity changed.')
                if row['state'] == 'queued':
                    row.update(state='blocked', not_sent=True, reason='Delivery cancelled before dispatch.')
                elif row['state'] == 'intent':
                    row.update(state='interrupted', not_sent=False, reason='A cancelled delivery outcome is unconfirmed.')
                rows.append(self._put(row))
            return rows

    def retry(self, principal: dict, ident: str, request: dict, *, owner) -> dict:
        with self.lock, self.graph.transaction():
            row = self.store.get('delivery', ident, principal['scope'])
            if (owner(principal, row) is not True
                    or set(request) != {'delivery_id', 'request_digest', 'destination_revision', 'idempotency_key'}
                    or request['delivery_id'] != ident or request['request_digest'] != row['envelope']['request_digest']
                    or request['destination_revision'] != row['destination']['revision']):
                raise PermissionError('Current authorization for this exact delivery is required.')
            key = self._command(row, principal, 'retry', request)
            if self.graph.get_doc(key):
                return row
            if row['state'] != 'blocked' or row.get('not_sent') is not True or not self._allowed(row):
                raise PermissionError('Only a reconciled, unsent delivery can be retried.')
            row.update(state='queued', attempt=None)
            self.graph.put_doc(key, {'request': digest(request)})
            return self._put(row)

    def reconcile(self, principal: dict, ident: str, request: dict, *, owner) -> dict:
        required = {'delivery_id', 'request_digest', 'destination_revision', 'outcome', 'evidence', 'idempotency_key'}
        with self.lock, self.graph.transaction():
            row = self.store.get('delivery', ident, principal['scope'])
            if (set(request) != required or owner(principal, row) is not True
                    or request['delivery_id'] != ident or request['request_digest'] != row['envelope']['request_digest']
                    or request['destination_revision'] != row['destination']['revision']
                    or request['evidence'] != 'owner_attestation' or request['outcome'] not in {'delivered', 'not_delivered'}):
                raise PermissionError('Reconciliation must attest this exact delivery and destination.')
            key = self._command(row, principal, 'reconcile', request)
            if self.graph.get_doc(key):
                return row
            if row['state'] != 'interrupted':
                raise ValueError('This delivery has no uncertain outcome to reconcile.')
            row.update(state='done' if request['outcome'] == 'delivered' else 'blocked',
                       not_sent=request['outcome'] == 'not_delivered',
                       reconciliation={'evidence': 'owner_attestation', 'outcome': request['outcome'],
                                       'actor': 'studio-daemon', 'weaker_than_receiver_receipt': True})
            self.graph.put_doc(key, {'request': digest(request)})
            return self._put(row)

    def _command(self, row: dict, principal: dict, action: str, request: dict) -> str:
        identity = request.get('idempotency_key')
        if not isinstance(identity, str) or not identity or len(identity) > 256:
            raise ValueError('A delivery action identity is required.')
        key = 'studio.delivery-command:' + digest({'delivery': row['id'], 'principal': principal['id'], 'action': action, 'key': identity})
        previous = self.graph.get_doc(key)
        if previous and previous['request'] != digest(request):
            raise Conflict('This action identity already names another delivery request.')
        return key

    def start(self) -> None:
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, name='studio-outbox', daemon=True)
            self.thread.start()

    def _loop(self) -> None:
        while not self.stop.wait(0.25):
            with self.lock:
                self.futures = {key: future for key, future in self.futures.items() if not future.done()}
                for row in self._indexed('queued'):
                    if row['id'] not in self.futures:
                        self.futures[row['id']] = self.pool.submit(self.attempt, row['id'], row['scope'])

    def close(self) -> None:
        self.closed = True
        self.stop.set()
        if self.thread:
            self.thread.join()
        self.pool.shutdown(wait=True, cancel_futures=True)
