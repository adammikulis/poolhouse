"""Owner-issued source connections, with custody before actual process activation."""
from __future__ import annotations

import copy
import re

from poolhouse.studio.store import Conflict, digest

from . import studio_epoch

PROVIDER = 'software-producer'
_HEX32 = re.compile('[0-9a-f]{32}\\Z')
_HEX64 = re.compile('[0-9a-f]{64}\\Z')
_BINDING = {'source', 'generation', 'scope', 'epoch', 'pool', 'receiver', 'producer',
            'instance', 'runtime', 'implementation', 'sdk', 'bridge', 'listener', 'activation'}


class Connections:
    """Fixed daemon callbacks do network work only outside the durable store lock.

    Neither a member request nor stored browser principal can create an owner
    intent. Only register/connect use the actual current owner interface. The
    software authorizer later reads that bounded intent under its current epoch.
    """

    def __init__(self, service, transport, *, identity, custody):
        self.service, self.authority, self.store = service, service.authority, service.store
        self.identity, self.transport, self.custody = identity, transport, custody

    @staticmethod
    def _key(source):
        return 'studio.producer-connection:' + source

    def _source(self, ident, scope):
        row = self.store.get('source', ident, scope)
        if row.get('provider') != PROVIDER:
            raise ValueError('Select a software producer source.')
        return row

    def _current(self, intent):
        context = self.authority._context()
        source = self._source(intent['source'], intent['context']['scope'])
        return (context == intent['context'] and self.identity() == intent['receiver']
                and studio_epoch.generation(self.authority.root, context['pool']) == intent['epoch']
                and source['generation'] == intent['generation']
                and source['revision'] == intent['revision']
                and source['connection_id'] == intent['command_id'])

    def configured(self, source, generation):
        with self.store.lock:
            intent = self.store.graph.get_doc(self._key(source))
            return bool(intent and intent['generation'] == generation and intent['phase'] != 'revoked'
                        and self._current(intent))

    def _offer(self, selected, context, receiver):
        if (not isinstance(selected, dict) or set(selected) != {'id', 'revision'}
                or not isinstance(selected['id'], str) or not _HEX32.fullmatch(selected['id'])
                or type(selected['revision']) is not int or selected['revision'] != 1):
            raise ValueError('Select the current software offer.')
        offer = self.transport.inspect(selected['id'], selected['revision'])
        if (not isinstance(offer, dict) or offer.get('id') != selected['id']
                or type(offer.get('revision')) is not int or offer.get('revision') != selected['revision']
                or offer.get('state') != 'offered'
                or not isinstance(offer.get('receiver'), dict) or not isinstance(offer.get('context'), dict)
                or offer.get('receiver', {}).get('certificate') != receiver['certificate']
                or offer.get('receiver', {}).get('pool') != context['pool']
                or offer.get('context', {}).get('pool') != context['pool']):
            raise PermissionError('The software offer belongs to another receiver or pool.')
        return copy.deepcopy(offer)

    def register(self, principal, body):
        expected = {'kind', 'provider', 'label', 'producer', 'idempotency_key'}
        if (not isinstance(body, dict) or set(body) != expected or body['kind'] != 'webhook'
                or body['provider'] != PROVIDER or not isinstance(body['label'], str)
                or not body['label'].strip() or len(body['label'].encode()) > 256):
            raise ValueError('Choose a software offer and name its source.')
        context = self.authority._owner(principal)
        if (not isinstance(body['idempotency_key'], str) or not body['idempotency_key']
                or len(body['idempotency_key']) > 256):
            raise ValueError('A source creation identity is required.')
        request_key = 'studio.producer-create:' + digest({'scope': context['scope'], 'principal': principal['id'],
                                                         'key': body['idempotency_key']})
        with self.store.lock:
            previous = self.store.graph.get_doc(request_key)
            if previous:
                if previous['request'] != digest(body):
                    raise Conflict('This creation identity already names another source.')
                source = self._source(previous['source'], context['scope'])
                intent = self.store.graph.get_doc(self._key(source['id']))
                if not intent or not self._current(intent):
                    raise PermissionError('The original source connection was revoked.')
            else:
                intent = None
        if intent is not None:
            return self._progress(principal, source, intent, first=False)
        epoch = self.authority.selection_epoch(principal, create=True)
        receiver = self.identity()
        if (not isinstance(receiver, dict) or set(receiver) != {'certificate', 'instance', 'runtime'}
                or not isinstance(receiver['certificate'], str) or not receiver['certificate']
                or any(not isinstance(receiver[key], str) or not _HEX64.fullmatch(receiver[key])
                       for key in ('instance', 'runtime'))):
            raise PermissionError('Current receiver software identity is unavailable.')
        offer = self._offer(body['producer'], context, receiver)
        command = digest({'scope': context['scope'], 'principal': principal['id'],
                          'key': body['idempotency_key'], 'action': 'connect'})
        generation = digest({'command': command, 'epoch': epoch})[:32]
        with self.store.lock, studio_epoch.changing(self.authority.root) as (_, epochs), self.store.graph.transaction():
            if (self.authority._owner(principal) != context or self.identity() != receiver
                    or epochs['pools'][context['pool']]['epoch'] != epoch):
                raise PermissionError('The source owner authority changed.')
            source = self.store.create('source', principal, {'kind': 'webhook', 'provider': PROVIDER,
                'label': body['label'], 'selection_epoch': epoch, 'generation': generation,
                'connection_id': command, 'available': False, 'formats': ['json'], 'requirements': []}, body['idempotency_key'])
            key = self._key(source['id'])
            intent = self.store.graph.get_doc(key)
            fixed = {'command_id': command, 'source': source['id'], 'revision': source['revision'],
                     'generation': generation, 'context': context, 'epoch': epoch,
                     'receiver': receiver, 'offer': offer, 'request_key': body['idempotency_key']}
            if intent:
                if any(intent.get(name) != value for name, value in fixed.items()):
                    raise Conflict('This connection identity already names another software offer.')
                first = False
            else:
                intent = {**fixed, 'phase': 'intent', 'binding': None, 'reason': ''}
                self.store.graph.put_doc(key, intent)
                first = True
            self.store.graph.put_doc(request_key, {'source': source['id'], 'request': digest(body)})
        return self._progress(principal, source, intent, first=first)

    def _binding(self, intent, value):
        offer = intent['offer']
        if not isinstance(value, dict) or set(value) != _BINDING:
            raise ValueError('The producer returned an invalid source binding.')
        fixed = {'source': intent['source'], 'generation': intent['generation'],
                 'scope': intent['context']['scope'], 'epoch': intent['epoch'],
                 'pool': intent['context']['pool'], 'receiver': intent['receiver']['certificate'],
                 'producer': offer['context']['device_certificate'],
                 'instance': offer['context']['daemon_instance'], 'runtime': offer['context']['runtime'],
                 'implementation': offer['implementation'], 'sdk': offer['process']}
        if any(value.get(name) != expected for name, expected in fixed.items()):
            raise PermissionError('The producer source or process binding changed.')
        process, listener = value['bridge'], value['listener']
        if (not isinstance(process, dict) or set(process) != {'pid', 'started'}
                or type(process['pid']) is not int or process['pid'] <= 0
                or type(process['started']) not in (int, float) or not 0 < process['started'] < float('inf')
                or not isinstance(listener, dict) or set(listener) != {'path', 'dev', 'ino'}
                or not isinstance(listener['path'], str) or not listener['path'].startswith('/')
                or '..' in listener['path'].split('/') or '\0' in listener['path']
                or len(listener['path'].encode()) > 103
                or any(type(listener[name]) is not int or listener[name] < 0 for name in ('dev', 'ino'))
                or not isinstance(value['activation'], str) or not _HEX64.fullmatch(value['activation'])):
            raise ValueError('The owned producer listener is invalid.')
        return copy.deepcopy(value)

    def _commit(self, principal, intent, **changes):
        with self.store.lock, studio_epoch.changing(self.authority.root), self.store.graph.transaction():
            actual = self.store.graph.get_doc(self._key(intent['source']))
            if (actual != intent or self.authority._owner(principal) != intent['context']
                    or not self._current(intent)):
                raise PermissionError('The source connection changed during provisioning.')
            result = {**intent, **copy.deepcopy(changes)}
            self.store.graph.put_doc(self._key(intent['source']), result)
            return result

    def _progress(self, principal, source, intent, *, first):
        # Persist possible dispatch BEFORE contacting the producer. A repeated
        # request can only inspect this exact command, never start another child.
        try:
            if first:
                intent = self._commit(principal, intent, phase='prepare_started')
                response = self.transport.prepare(copy.deepcopy(intent))
            else:
                response = self.transport.status(copy.deepcopy(intent))
            if not isinstance(response, dict) or set(response) != {'binding', 'active'} or type(response['active']) is not bool:
                raise ValueError('The producer response is invalid.')
            binding = self._binding(intent, response['binding'])
            if response['active'] and intent['phase'] in {'intent', 'prepare_started', 'interrupted'}:
                raise PermissionError('The producer activated before receiver authorization.')
            if intent['binding'] is not None and intent['binding'] != binding:
                raise Conflict('This connection command already names another owned process.')
            if intent['phase'] in {'intent', 'prepare_started', 'interrupted'}:
                if self.custody(copy.deepcopy(principal), copy.deepcopy(intent), copy.deepcopy(binding)) is not True:
                    raise PermissionError('The receiver could not verify source credential custody.')
                intent = self._commit(principal, intent, binding=binding, phase='authorized', reason='')
            if intent['phase'] == 'authorized':
                # The child receives authority ONLY after the receiver's durable
                # current-owner custody CAS above. Lost ACK never repeats activation.
                intent = self._commit(principal, intent, phase='activation_started')
                response = self.transport.activate(copy.deepcopy(intent))
                if response != {'binding_digest': digest(binding), 'active': True}:
                    raise ValueError('The producer activation is unconfirmed.')
            if intent['phase'] == 'activation_started' and not first and not response['active']:
                return source
            if response.get('active') is True:
                self._commit(principal, intent, phase='ready', reason='')
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            with self.store.lock, self.store.graph.transaction():
                actual = self.store.graph.get_doc(self._key(source['id']))
                if actual == intent and self._current(intent):
                    self.store.graph.put_doc(self._key(source['id']), {**intent, 'reason': 'This connection needs its exact process receipt.'})
            if isinstance(exc, PermissionError):
                raise
        return self._source(source['id'], source['scope'])

    def check(self, principal, source, body):
        self.authority._owner(principal)
        source = self._source(source['id'], principal['scope'])
        if (not isinstance(body, dict) or set(body) != {'revision', 'idempotency_key'}
                or type(body['revision']) is not int or body['revision'] != source['revision']
                or not isinstance(body['idempotency_key'], str) or not 0 < len(body['idempotency_key']) <= 256):
            raise Conflict('Refresh the current source connection.')
        with self.store.lock:
            intent = self.store.graph.get_doc(self._key(source['id']))
            if not intent or intent['request_key'] != body['idempotency_key'] or not self._current(intent):
                raise Conflict('This is not the pending source connection.')
        return self._progress(principal, source, intent, first=False)

    def authorize(self, producer, body):
        expected = {'source', 'command_id', 'stage', 'challenge', 'binding_digest'}
        if (not isinstance(body, dict) or set(body) != expected
                or not isinstance(body['stage'], str) or body['stage'] not in {'prepare', 'ready', 'current'}
                or not isinstance(body['source'], str) or not _HEX32.fullmatch(body['source'])
                or not isinstance(body['command_id'], str) or not _HEX64.fullmatch(body['command_id'])
                or (body['binding_digest'] is not None and (not isinstance(body['binding_digest'], str)
                    or not _HEX64.fullmatch(body['binding_digest'])))
                or not isinstance(body['challenge'], str) or not _HEX64.fullmatch(body['challenge'])):
            raise ValueError('Invalid source authorization challenge.')
        with self.store.lock:
            intent = self.store.graph.get_doc(self._key(body['source']))
            if (not intent or intent['command_id'] != body['command_id'] or not self._current(intent)
                    or producer != {'certificate': intent['offer']['context']['device_certificate'],
                                    'pool': intent['context']['pool']}):
                raise PermissionError('No current source intent belongs to this producer.')
            if body['stage'] == 'prepare':
                valid = intent['phase'] == 'prepare_started' and body['binding_digest'] is None
            else:
                valid = (intent['phase'] in {'authorized', 'activation_started', 'ready'}
                         and intent['binding'] is not None and body['binding_digest'] == digest(intent['binding']))
            if not valid:
                raise PermissionError('The producer stage does not match its owner intent.')
            return {'authorized': True, 'challenge': body['challenge'], 'intent': copy.deepcopy(intent)}
