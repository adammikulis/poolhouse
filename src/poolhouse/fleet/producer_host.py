"""Own one inert publisher child per durable receiver-issued source command."""
from __future__ import annotations

import copy
import re
import secrets
import threading

from poolhouse.studio.store import Conflict, digest

from .membership import fingerprint_of
from .producer_bridge import BridgeCapability
from .producer_process import OwnedProducer


def _command(body, *, preparing=False):
    expected = {'source', 'command_id', 'offer_id', 'revision'} if preparing else {'source', 'command_id'}
    if (not isinstance(body, dict) or set(body) != expected
            or any(not isinstance(body[name], str) or not re.fullmatch('[a-f0-9]{' + str(size) + '}', body[name])
                   for name, size in (('source', 32), ('command_id', 64)))
            or (preparing and (not isinstance(body['offer_id'], str) or not re.fullmatch('[a-f0-9]{32}', body['offer_id'])
                               or type(body['revision']) is not int or body['revision'] != 1))):
        raise ValueError('Select the exact current software source command.')
    return body


class ProducerHost:
    """Only retained children and receiver owner intents can activate a data capability.

    The fixed transport adapter derives enrolled destinations and actual sealed caller identities.
    Its control() returns the current immutable daemon descriptor; request() performs one sealed
    member request outside the store lock. Persisted launch records never authorize adoption.
    """

    def __init__(self, root, service, registry, transport):
        self.root, self.store, self.registry, self.transport = root, service.store, registry, transport
        self.lock, self.rows = threading.RLock(), {}
        self.control = copy.deepcopy(transport.control())

    @staticmethod
    def _key(receiver, body):
        if (not isinstance(receiver, dict) or set(receiver) != {'certificate', 'pool'}
                or not isinstance(receiver['certificate'], str) or not receiver['certificate']
                or not isinstance(receiver['pool'], str) or not re.fullmatch('[a-f0-9]{64}', receiver['pool'])):
            raise PermissionError('An actual enrolled receiver is required.')
        return 'studio.producer-host:' + digest({'receiver': receiver, 'source': body['source'], 'command': body['command_id']})

    def _local(self, row):
        try:
            if row['stopped'] or self.transport.control() != self.control:
                return False
            return self.registry.inspect(row['offer']['id'], row['offer']['revision']) == row['offer'] and not row['stopped']
        except (OSError, ValueError, RuntimeError):
            return False

    def _authorization(self, receiver, body, stage, binding=None):
        challenge = secrets.token_hex(32)
        request = {'source': body['source'], 'command_id': body['command_id'], 'stage': stage,
                   'challenge': challenge, 'binding_digest': digest(binding) if binding is not None else None}
        reply = self.transport.request(receiver, 'producer-authorize', request)
        if (not isinstance(reply, dict) or set(reply) != {'authorized', 'challenge', 'stage', 'intent'}
                or reply['authorized'] is not True or reply['challenge'] != challenge or reply['stage'] != stage):
            raise PermissionError('The receiver did not prove this source command.')
        intent = reply['intent']
        if (not isinstance(intent, dict) or intent.get('source') != body['source']
                or intent.get('command_id') != body['command_id']
                or intent.get('context', {}).get('pool') != receiver['pool']
                or intent.get('context', {}).get('active') is not True
                or intent.get('receiver', {}).get('certificate') != receiver['certificate']
                or intent.get('context', {}).get('device') != fingerprint_of(receiver['certificate'])
                or (binding is not None and intent.get('binding') != binding)):
            raise PermissionError('The receiver source identity changed.')
        for name, size in (('generation', 32), ('epoch', 64)):
            if not isinstance(intent.get(name), str) or not re.fullmatch('[a-f0-9]{' + str(size) + '}', intent[name]):
                raise ValueError('The receiver source generation is invalid.')
        scope = intent['context'].get('scope')
        if not isinstance(scope, str) or not scope or len(scope.encode()) > 256:
            raise ValueError('The receiver source scope is invalid.')
        return copy.deepcopy(intent)

    def _response(self, row):
        if (not self._local(row) or row.get('binding') is None
                or row['owned'].matches(row['binding']['bridge']) is not True):
            raise InterruptedError('The original publisher process needs reconciliation.')
        return {'binding': copy.deepcopy(row['binding']), 'active': row['phase'] == 'active'}

    @staticmethod
    def _intent_identity(intent):
        return {key: copy.deepcopy(intent[key]) for key in ('source', 'command_id', 'generation', 'epoch',
                'context', 'receiver', 'offer', 'revision', 'request_key')}

    @staticmethod
    def _offer_use(receiver, body):
        return {'receiver': copy.deepcopy(receiver), 'source': body['source'], 'command_id': body['command_id']}

    def consumed(self, offer_id):
        with self.store.lock:
            return self.store.graph.get_doc('studio.producer-offer-use:' + offer_id) is not None

    def _retained(self, key, receiver, body, offer):
        with self.lock, self.store.lock:
            previous = self.store.graph.get_doc(key)
            if previous is None:
                return None
            row = self.rows.get(key)
            if row is None:
                raise InterruptedError('The original publisher launch cannot be adopted or repeated.')
            if (row['receiver'] != receiver or row['offer'] != offer or row['source'] != body['source']
                    or row['command_id'] != body['command_id'] or previous.get('intent') != row['intent']
                    or previous.get('control') != self.control
                    or self.store.graph.get_doc('studio.producer-offer-use:' + offer['id']) != self._offer_use(receiver, body)):
                raise Conflict('This command already names another publisher.')
        intent = self._authorization(receiver, body, 'inspect')
        if (self._intent_identity(intent) != row['intent']
                or (intent.get('binding') is not None and intent['binding'] != row['binding'])
                or (intent.get('binding') is None and (intent.get('phase') != 'prepare_started' or row['phase'] != 'prepared'))):
            raise Conflict('The retained source command changed.')
        return self._response(row)

    def _claim_offer(self, key, fixed):
        """Consume offer and possible launch together, before any process side effect."""
        with self.store.lock, self.store.graph.transaction():
            offer_key = 'studio.producer-offer-use:' + fixed['offer']['id']
            use = self._offer_use(fixed['receiver'], fixed)
            claimed = self.store.graph.get_doc(offer_key)
            if claimed is not None and claimed != use:
                raise Conflict('This software offer already belongs to another immutable source command.')
            previous = self.store.graph.get_doc(key)
            if previous:
                if claimed != use:
                    raise PermissionError('The original software offer intent requires reconciliation.')
                if any(previous.get(name) != value for name, value in fixed.items()):
                    raise Conflict('This command already names another publisher.')
                if key not in self.rows:
                    raise InterruptedError('The original publisher launch cannot be adopted or repeated.')
                return self._response(self.rows[key])
            if claimed is not None:
                raise PermissionError('The original software offer intent requires reconciliation.')
            self.store.graph.put_doc(offer_key, use)
            self.store.graph.put_doc(key, {**fixed, 'phase': 'launching'})
            return None

    def prepare(self, receiver, body):
        body = _command(body, preparing=True)
        key = self._key(receiver, body)
        offer = self.registry.inspect(body['offer_id'], body['revision'])
        expected_context = {'pool': self.control['pool'], 'runtime': self.control['runtime'],
                            'daemon_instance': self.control['daemon']['instance'],
                            'device_certificate': self.control['certificate']}
        if offer['context'] != expected_context or receiver['pool'] != self.control['pool']:
            raise PermissionError('The offer does not belong to this current producer daemon.')
        if offer['receiver']['certificate'] != receiver['certificate'] or offer['receiver']['pool'] != receiver['pool']:
            raise PermissionError('The software offer belongs to another receiver.')
        retained = self._retained(key, receiver, body, offer)
        if retained is not None:
            return retained
        intent = self._authorization(receiver, body, 'prepare')
        if intent.get('offer') != offer or self.registry.inspect(body['offer_id'], body['revision']) != offer:
            raise PermissionError('The offered process changed during authorization.')
        fixed = {'receiver': copy.deepcopy(receiver), 'source': body['source'], 'command_id': body['command_id'],
                 'offer': offer, 'generation': intent['generation'], 'epoch': intent['epoch'],
                 'scope': intent['context']['scope'], 'control': self.control, 'intent': self._intent_identity(intent)}
        with self.lock:
            existing = self._claim_offer(key, fixed)
            if existing is not None:
                return existing
            row = {**fixed, 'phase': 'launching', 'stopped': False, 'binding': None, 'cap': None}
            self.rows[key] = row
            owned = OwnedProducer(self.root, sdk=offer['process'], control=self.control, current=lambda: self._local(row))
            row['owned'] = owned
            prepared = False
            try:
                proof = owned.prepare()
                if not self._local(row):
                    raise PermissionError('The publisher ended during launch.')
                binding = {'source': body['source'], 'generation': intent['generation'], 'scope': fixed['scope'],
                    'epoch': intent['epoch'], 'pool': receiver['pool'], 'receiver': receiver['certificate'],
                    'producer': self.control['certificate'], 'instance': self.control['daemon']['instance'],
                    'runtime': self.control['runtime'], 'implementation': offer['implementation'], 'sdk': offer['process'],
                    'bridge': proof['process'], 'listener': {'path': proof['path'], **proof['inode']}, 'activation': proof['nonce']}
                row.update(binding=binding, phase='prepared',
                           cap=BridgeCapability(binding, owned=owned.matches, current=lambda _: self._local(row)))
                with self.store.lock, self.store.graph.transaction():
                    self.store.graph.put_doc(key, {**fixed, 'phase': 'prepared', 'binding': binding})
                response = self._response(row)
                prepared = True
                return response
            finally:
                if not prepared:
                    row['stopped'] = True
                    owned.close()

    def _find(self, receiver, body):
        key = self._key(receiver, _command(body))
        row = self.rows.get(key)
        if row is None:
            raise InterruptedError('The original publisher process is unavailable; it will not be replaced.')
        return row

    def status(self, receiver, body):
        with self.lock:
            return self._response(self._find(receiver, body))

    def activate(self, receiver, body):
        with self.lock:
            row = self._find(receiver, body)
            binding = copy.deepcopy(self._response(row)['binding'])
            already_active = row['phase'] == 'active'
            if not already_active and row['phase'] != 'prepared':
                raise InterruptedError('The activation outcome needs its exact process receipt.')
            if not already_active:
                row['phase'] = 'authorizing'
        activated = False
        try:
            if already_active:
                self._authorization(receiver, body, 'current', binding)
                if not self._local(row) or not row['cap'].result_current(binding['bridge']):
                    raise PermissionError('The original activated publisher was revoked.')
                activated = True
                return {'binding_digest': digest(binding), 'active': True}
            self._authorization(receiver, body, 'ready', binding)
            if not self._local(row):
                raise PermissionError('The publisher was revoked during authorization.')
            row['phase'] = 'activating'
            acknowledgement = row['owned'].activate(binding, row['cap'].secret)
            self._authorization(receiver, body, 'current', binding)
            if not self._local(row):
                raise PermissionError('The publisher was revoked during activation.')
            row['cap'].activate(binding['bridge'], acknowledgement)
            with self.lock:
                if row['stopped'] or not row['cap'].result_current(binding['bridge']):
                    raise PermissionError('The activated publisher is no longer current.')
                row['phase'] = 'active'
            activated = True
            return {'binding_digest': digest(binding), 'active': True}
        finally:
            if not activated:
                self._stop(row)

    @staticmethod
    def _stop(row):
        row['stopped'] = True
        if row.get('cap') is not None:
            row['cap'].revoke()
        if row.get('owned') is not None:
            row['owned'].close()

    def revoke(self, receiver, body):
        with self.lock:
            row = self._find(receiver, body)
            self._stop(row)
            return {'revoked': True}

    def accepts(self, process):
        with self.lock:
            return any(row['phase'] == 'active' and self._local(row) and row['owned'].matches(process)
                       for row in self.rows.values())

    def bridge(self, process, action, request):
        with self.lock:
            candidates = [row for row in self.rows.values() if row.get('binding') == request.get('binding')]
            if len(candidates) != 1:
                raise PermissionError('No owned publisher matches this data channel.')
            row = candidates[0]
            checked = row['cap'].verify(process, request)
            if checked['action'] != action:
                raise PermissionError('The fixed publisher action changed.')
            command = {'source': row['source'], 'command_id': row['command_id']}
        self._authorization(row['receiver'], command, 'current', row['binding'])
        if not row['cap'].result_current(process):
            raise PermissionError('The publisher was revoked before dispatch.')
        result = self.transport.request(row['receiver'], action.replace('bridge-', 'producer-'), checked)
        self._authorization(row['receiver'], command, 'current', row['binding'])
        if not row['cap'].result_current(process):
            raise PermissionError('The publisher was revoked before its receipt.')
        return result

    def close(self):
        with self.lock:
            for row in self.rows.values():
                self._stop(row)
