"""Explicit owner source replacement disables the old generation before any remote cleanup."""
from __future__ import annotations

import copy
import time

from poolhouse.studio.store import Conflict, digest

from . import studio_epoch
from .producer_connections import Connections
from .producer_recovery import OwnerBarrier


class SourceControls:
    def __init__(self, runtime):
        self.runtime, self.service = runtime, runtime.service
        self.store, self.connections = self.service.store, runtime.connections
        self.barrier = OwnerBarrier(self.service, runtime.publications)

    @staticmethod
    def _body(action, body):
        fields = {'revision', 'idempotency_key'} | ({'producer'} if action == 'connect' else set())
        if (action not in {'connect', 'rotate', 'revoke'} or not isinstance(body, dict) or set(body) != fields
                or type(body['revision']) is not int or body['revision'] < 1
                or not isinstance(body['idempotency_key'], str) or not 0 < len(body['idempotency_key']) <= 256):
            raise ValueError('Choose a current source revision and action identity.')

    def _command(self, principal, source, action, body):
        context = self.service.authority._owner(principal)
        self._body(action, body)
        actual = self.store.get('source', source['id'], context['scope'])
        if actual.get('provider') != 'software-producer':
            raise ValueError('Choose a connected software source.')
        key = 'studio.producer-source-command:' + digest({'scope': context['scope'], 'source': actual['id'],
                                                        'action': action, 'key': body['idempotency_key']})
        with self.store.lock:
            previous = self.store.graph.get_doc(key)
            if previous:
                if previous['request'] != digest(body):
                    raise Conflict('This source action already names another configuration.')
                return context, actual, key, True
            if actual['revision'] != body['revision']:
                raise Conflict('Refresh the current software source revision.')
        return context, actual, key, False

    def _replace(self, principal, source, action, body, selected):
        context, receiver, epoch, offer, command_key = selected
        with self.store.lock, self.service.authority.lock, studio_epoch.changing(self.service.authority.root) as (_, epochs), self.store.graph.transaction():
            if (self.service.authority._owner(principal) != context or self.runtime.identity() != receiver
                    or epochs['pools'][context['pool']]['epoch'] != epoch):
                raise PermissionError('The source owner configuration changed.')
            previous = self.store.graph.get_doc(command_key)
            if previous:
                if previous['request'] != digest(body):
                    raise Conflict('This source action already names another configuration.')
                return self.store.get('source', source['id'], context['scope']), None, None
            if self.store.get('source', source['id'], context['scope']) != source:
                raise Conflict('Refresh the current software source revision.')
            old = self.store.graph.get_doc(Connections._key(source['id']))
            if not old:
                raise PermissionError('The original software source configuration is missing.')
            command = digest({'source': source['id'], 'generation': source['generation'], 'action': action,
                              'key': body['idempotency_key'], 'epoch': epoch})
            generation = digest({'command': command, 'source': source['id']})[:32]
            updated = {**source, 'generation': generation, 'revision': source['revision'] + 1,
                       'selection_epoch': epoch, 'connection_id': command, 'available': False}
            intent = {'source': source['id'], 'command_id': command, 'revision': updated['revision'],
                      'generation': generation, 'context': context, 'epoch': epoch, 'receiver': receiver,
                      'offer': copy.deepcopy(offer if offer is not None else old['offer']),
                      'request_key': body['idempotency_key'], 'phase': 'intent' if action == 'connect' else 'revoked',
                      'binding': None, 'reason': '' if action == 'connect' else 'Choose a fresh live software offer to connect again.'}
            self.store.graph.put_doc('studio.producer-connection-history:' + old['command_id'], old)
            self.store.store.put('source', updated)
            self.store.graph.put_doc(Connections._key(source['id']), intent)
            receipt = self.barrier.settle(principal, source, old, updated)
            if self.service.authority._owner(principal) != context:
                raise PermissionError('Source owner authorization changed before commit.')
            self.store.graph.put_doc(command_key, {'request': digest(body), 'source': updated, 'receipt': receipt})
            self.store.graph.put_doc('studio.producer-source-audit:' + command, {'actor': 'studio-daemon',
                'action': action, 'source': source['id'], 'old_generation': source['generation'],
                'generation': generation, 'epoch': epoch, 'at': time.time()})
            return updated, old, intent

    def action(self, principal, source, action, body):
        if action == 'connection-status':
            return self.connections.check(principal, source, body)
        context, source, command_key, repeated = self._command(principal, source, action, body)
        if repeated:
            return source
        epoch = self.service.authority.selection_epoch(principal, create=True)
        receiver = self.runtime.identity()
        offer = self.connections._offer(body['producer'], context, receiver) if action == 'connect' else None
        updated, old, intent = self._replace(principal, source, action, body,
                                           (context, receiver, epoch, offer, command_key))
        if intent is None:
            return updated
        # Receiver generation/config is already irreversibly disabled. Remote
        # cleanup uncertainty cannot restore source ingress or old node grants.
        try:
            if self.runtime.devices.revoke(old) != {'revoked': True}:
                raise ValueError('The original producer cleanup is unconfirmed.')
        except (OSError, ValueError, RuntimeError):
            with self.store.lock, self.store.graph.transaction():
                if self.store.graph.get_doc(Connections._key(source['id'])) == intent:
                    intent = {**intent, 'reason': 'The previous source is disabled; its producer cleanup is unconfirmed.'}
                    self.store.graph.put_doc(Connections._key(source['id']), intent)
        if action == 'connect':
            return self.connections._progress(principal, updated, intent, first=True)
        return updated
