"""Fixed producer composition; source configuration and observed process liveness stay separate."""
from __future__ import annotations

import copy
import threading
from pathlib import Path

from poolhouse import real_machine
from poolhouse.studio.store import digest

from .job_records import process_started
from .producer_connections import Connections
from .producer_control import ControlServer
from .producer_devices import Devices
from .producer_holder import installed_root
from .producer_host import ProducerHost
from .producer_location import Location
from .producer_offers import OfferRegistry
from .producer_publications import Publications
from .producer_source_controls import SourceControls


class Runtime:
    """A retained daemon generation. Construction infrastructure grants no source or workflow."""

    def __init__(self, ui, service, local):
        self.lock, self.closed = threading.RLock(), False
        self.server = self.host = self.location = None
        self.root = Path(ui.root)
        if self.root.resolve().is_relative_to(installed_root().parent.resolve()):
            real_machine.require('the installed software producer service')
        complete = False
        try:
            self.location = Location(self.root)
            self._components(ui, service, local)
            self.loaded_identity = ui.studio_identity()
            self.server = ControlServer(self.root, identity=self.loaded_identity, current=self._current,
                dispatch=self.dispatch, bridge=lambda process: self.host is not None and self.host.accepts(process))
            self.registry = OfferRegistry(context=self._offer_context, receivers=self.devices.receivers,
                alive=lambda process: process_started(process['pid']) == process['started'],
                implementation=self.runtime, device_label='Current device ' + self.context['device'][:8])
            self.host = ProducerHost(self.root, service, self.registry, self.devices)
            self.location.publish(self.server.bootstrap_path)
            ui.studio_producers = self
            ui.studio_producer_admission = self.publications.admission
            complete = True
        finally:
            if not complete:
                self.close()

    def _current(self):
        with self.lock:
            if self.closed:
                raise PermissionError('The original software producer daemon changed.')
            location = self.location
        valid = (self.service.authority._context() == self.context
                 and self.service.engine.implementation == self.runtime
                 and self.ui.studio_identity() is self.loaded_identity
                 and location.check() is True)
        with self.lock:
            if not valid or self.closed or self.location is not location:
                raise PermissionError('The original software producer daemon changed.')
        return {'pool': self.context['pool'], 'runtime': self.runtime}

    def control(self):
        self._current()
        with self.lock:
            server = self.server
            if self.closed or server is None:
                raise PermissionError('The software producer control service is unavailable.')
        binding = server._binding()
        with self.lock:
            if self.closed or self.server is not server:
                raise PermissionError('The original software producer daemon changed.')
        return binding

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            host, server, location = self.host, self.server, self.location
        try:
            if host is not None:
                host.close()
        finally:
            try:
                if server is not None:
                    server.close()
            finally:
                if location is not None:
                    location.close()

    def _components(self, ui, service, local):
        self.ui, self.service, self.local = ui, service, local
        self.context = copy.deepcopy(service.authority._context())
        self.runtime = service.engine.implementation
        self.devices = Devices(ui, service, self.control)
        self.connections = Connections(service, self.devices, identity=self.identity, custody=self._custody)
        self.publications = Publications(ui, service, self.connections, local)
        self.controls = SourceControls(self)

    def _offer_context(self):
        control = self.control()
        return {'pool': control['pool'], 'runtime': control['runtime'],
                'daemon_instance': control['daemon']['instance'], 'device_certificate': control['certificate']}

    def identity(self):
        value = self.control()
        return {'certificate': value['certificate'], 'instance': value['daemon']['instance'], 'runtime': value['runtime']}

    def _receiver(self, caller):
        self.devices.refresh()
        values = [row for row in self.devices.receivers()
                  if row['certificate'] == caller['certificate'] and row['pool'] == caller['pool']]
        if len(values) != 1:
            raise PermissionError('The intended producer receiver is not currently enrolled.')
        return values[0]

    def member(self, caller, action, request):
        self._current()
        if action == 'producer-offers':
            receiver = self._receiver(caller)
            return {'offers': [offer for offer in self.registry.discover(receiver)
                               if not self.host.consumed(offer['id'])]}
        if action == 'producer-inspect':
            offer = self.registry.inspect(request['offer_id'], request['revision'])
            if (offer['receiver'] != self._receiver(caller) or self.host.consumed(offer['id'])):
                raise PermissionError('This software offer is unavailable for a new source.')
            return {'offer': offer}
        if action == 'producer-authorize':
            return self.connections.authorize(caller, request)
        if action in {'producer-publish', 'producer-receipt', 'producer-retry'}:
            return self.publications.receive(caller, action, request)
        methods = {'producer-connect': self.host.prepare, 'producer-activate': self.host.activate,
                   'producer-status': self.host.status, 'producer-revoke': self.host.revoke}
        if action not in methods:
            raise ValueError('Unknown fixed producer operation.')
        return methods[action](caller, request)

    @staticmethod
    def _public_offer(offer, state=None):
        return {'id': offer['id'], 'revision': offer['revision'], 'label': offer['label'],
                'device_label': offer['device_label'], 'software_label': offer['label'],
                'state': state or offer['state']}

    def _channel(self, offer):
        with self.host.lock:
            rows = [row for row in self.host.rows.values() if row['offer']['id'] == offer['id']]
            if len(rows) > 1:
                raise PermissionError('The software offer has conflicting channel evidence.')
            if not rows:
                return None
            row = rows[0]
            if row['offer'] != offer or row['phase'] != 'active' or not self.host._local(row):
                return None
            binding = row['binding']
            if not row['cap'].result_current(binding['bridge']):
                return None
            return {'path': binding['listener']['path'], 'process': copy.deepcopy(binding['bridge'])}

    def dispatch(self, action, request, process):
        self._current()
        if action == 'receivers':
            self.devices.refresh()
            return {'receivers': self.registry.receivers_for(process)}
        if action == 'offer':
            return {'offer': self._public_offer(self.registry.offer(process, request))}
        if action == 'offer-status':
            offer = self.registry.status(process, request['offer_id'])
            channel = self._channel(offer) if offer['state'] == 'offered' else None
            state = 'connected' if channel else 'unavailable' if self.host.consumed(offer['id']) else offer['state']
            return {'offer': self._public_offer(offer, state), 'channel': channel}
        if action == 'withdraw':
            offer = self.registry.withdraw(process, request['offer_id'])
            with self.host.lock:
                rows = [copy.copy(row) for row in self.host.rows.values() if row['offer']['id'] == offer['id']]
            for row in rows:
                self.host.revoke(row['receiver'], {'source': row['source'], 'command_id': row['command_id']})
            return {'offer': self._public_offer(offer)}
        if action in {'bridge-publish', 'bridge-receipt', 'bridge-retry'}:
            return self.host.bridge(process, action, request)
        raise ValueError('Unknown fixed producer channel operation.')

    def _marker(self, source):
        return self.service.store.graph.get_doc('studio.webhook-producer:' + source)

    def current(self, source, generation):
        try:
            self._current()
            with self.service.store.lock:
                intent = self.service.store.graph.get_doc(Connections._key(source))
                return bool(intent and intent['phase'] == 'ready' and intent['generation'] == generation
                    and self.connections.configured(source, generation)
                    and self._marker(source) == {'command_id': intent['command_id'],
                        'binding_digest': digest(intent['binding']), 'context': intent['context']})
        except (OSError, ValueError, RuntimeError, KeyError):
            return False

    def ready(self, source, generation):
        if not self.current(source, generation):
            return False
        accepted = False

        def evidence(credential):
            nonlocal accepted
            accepted = credential['source'] == source and credential['generation'] == generation
            return accepted

        try:
            self.ui.studio_webhooks.provision_registered(source, generation, evidence, scope=self.context['scope'])
            return accepted and self.current(source, generation)
        except (OSError, ValueError, RuntimeError, KeyError):
            return False

    def available(self, source):
        if not self.ready(source['id'], source['generation']):
            return False
        with self.service.store.lock:
            intent = self.service.store.graph.get_doc(Connections._key(source['id']))
        try:
            status = self.devices.status(intent)
            return (status == {'binding': intent['binding'], 'active': True}
                    and self.ready(source['id'], source['generation']))
        except (OSError, ValueError, RuntimeError, KeyError):
            return False

    def providers(self):
        self._current()
        return {'id': 'software-producer', 'label': 'Connected software producer', 'available': True,
                'requirements': [], 'offers': self.devices.discover()}

    def describe(self, source):
        self._current()
        actual = self.service.store.get('source', source['id'], self.context['scope'])
        with self.service.store.lock:
            intent = self.service.store.graph.get_doc(Connections._key(source['id']))
        available = self.available(actual)
        phase = intent['phase'] if intent else 'unavailable'
        state = 'ready' if available else 'revoked' if phase == 'revoked' else 'connecting' if phase == 'intent' else 'unavailable'
        check = phase in {'prepare_started', 'activation_started', 'authorized'}
        if check:
            state = 'interrupted'
        offer = intent.get('offer') if intent else None
        producer = ({'id': offer['id'], 'label': offer['label'], 'device_label': offer['device_label'],
                     'software_label': offer['label']} if offer else None)
        reason = '' if available else intent.get('reason') if intent else 'Select a live software offer to connect.'
        reason = reason or 'The original software process is unavailable.'
        return {**actual, 'available': available, 'test_supported': False,
                'requirements': [] if available else [reason],
                'connection': {'state': state, 'producer': producer, 'reason': '' if available else reason,
                    'can_connect': True, 'can_rotate': bool(intent), 'can_revoke': bool(intent and phase != 'revoked'),
                    'can_check': check, 'pending': {'idempotency_key': intent['request_key']} if check else None}}

    def action(self, principal, source, action, body):
        return self.controls.action(principal, source, action, body)

    def _custody(self, principal, intent, binding):
        with self.service.store.lock, self.service.authority.lock, self.service.store.graph.transaction():
            if (self.service.authority._owner(principal) != intent['context']
                    or not self.connections._current(intent)):
                raise PermissionError('The source owner changed before credential custody.')
            self.ui.studio_webhooks.register(principal, intent['source'], intent['generation'])
            marker = {'command_id': intent['command_id'], 'binding_digest': digest(binding), 'context': intent['context']}
            self.service.store.graph.put_doc('studio.webhook-producer:' + intent['source'], marker)
            if (self.service.authority._owner(principal) != intent['context']
                    or not self.connections._current(intent)):
                raise PermissionError('The source owner changed before custody committed.')
            return True


def start(ui, service, local):
    """Only the installed fixed account service may open production infrastructure."""
    real_machine.require('the installed software producer service')
    if Path(ui.root).absolute() != installed_root():
        raise PermissionError('Use the installed software producer service.')
    return Runtime(ui, service, local)
