"""Receive authenticated software data through one durable, attempt-bound local webhook."""
from __future__ import annotations

import copy

from poolhouse.studio.producer_log import ProducerLog
from poolhouse.studio.store import Conflict

from .producer_admission import Admission
from .producer_bridge import _checked
from .producer_connections import _BINDING, Connections


class Publications:
    """Source data authority never selects a workflow grant, endpoint or resource.

    Only the protected producer daemon may forward its retained bridge's checked request. The
    receiver independently derives the current subscription and keeps credentials in its holder.
    These local callbacks never inspect transient SDK availability or make member requests.
    """

    def __init__(self, ui, service, connections, sender):
        self.ui, self.service, self.connections, self.sender = ui, service, connections, sender
        self.store = service.store
        self.admission = Admission(service, current=self.current, receipt_current=self.receipt_current)
        self.log = ProducerLog(service.store, current=self.current, receipt=self.admission.receipt,
                               negative=self.admission.negative)

    def _configured(self, binding):
        if not isinstance(binding, dict) or set(binding) != _BINDING:
            return False
        intent = self.store.graph.get_doc(Connections._key(binding['source']))
        return bool(intent and intent.get('binding') == binding and intent['phase'] == 'ready'
                    and self.connections.configured(binding['source'], binding['generation']))

    @staticmethod
    def _source_binding(binding):
        if not isinstance(binding, dict) or set(binding) != _BINDING | {'subscription', 'grant'}:
            raise PermissionError('A receiver-owned publication binding is required.')
        return {key: copy.deepcopy(binding[key]) for key in _BINDING}

    def _subscription(self, binding):
        sub = self.store.get('subscription', binding['subscription'], binding['scope'])
        if (sub['source_id'] != binding['source'] or sub.get('grant') != binding['grant']
                or sub.get('service_principal', {}).get('grant_id') != binding['grant']):
            raise PermissionError('The original subscription authority changed.')
        return sub

    def current(self, binding):
        with self.store.lock:
            source = self._source_binding(binding)
            if not self._configured(source):
                return False
            sub = self._subscription(binding)
            return sub['state'] == 'active' and self.service.authorized(sub, sub['service_principal']) is True

    def receipt_current(self, row):
        with self.store.lock:
            binding = row['binding']
            if not self._configured(self._source_binding(binding)):
                return False
            sub = self._subscription(binding)
            return self.service.authorized(sub, sub['service_principal']) is True

    def _event_binding(self, source):
        with self.store.lock:
            pointer = self.store.graph.get_doc('studio.webhook-subscription:' + source['source'])
            if (not pointer or pointer.get('scope') != source['scope']
                    or pointer.get('generation') != source['generation']):
                raise PermissionError('Start this software source before publishing records.')
            binding = {**copy.deepcopy(source), 'subscription': pointer['subscription'], 'grant': pointer['grant']}
            if self.current(binding) is not True:
                raise PermissionError('Start this current software source before publishing records.')
            return binding

    def _original(self, source, event_id):
        key, _ = ProducerLog._keys(source, event_id)
        with self.store.lock:
            row = self.store.graph.get_doc(key)
            if row is None:
                return None
            if self._source_binding(row['binding']) != source:
                raise Conflict('This source event identity belongs to another immutable connection.')
            if self.receipt_current(row) is not True:
                raise PermissionError('The original publication receipt is no longer available.')
            return self.log.get(row['binding'], event_id)

    def _authorize_send(self, row):
        if self.current(row['binding']) is not True:
            raise PermissionError('Publication authority changed before transport.')

    @staticmethod
    def _public(row):
        result = {'event_id': row['event_id'], 'state': row['state']}
        if row.get('receipt'):
            result['receipt'] = {key: row['receipt'][key] for key in ('id', 'status')}
        return result

    def _send(self, row):
        credential = []
        try:
            self._authorize_send(row)
            self.ui.studio_webhooks.provision_registered(row['binding']['source'], row['binding']['generation'],
                lambda value: credential.append(value) is None, scope=row['binding']['scope'])
            intent = {**row['binding'], **{key: row[key] for key in ('event_id', 'raw', 'wire_digest', 'timestamp', 'attempt')}}
            status, reply = self.sender._send(intent, credential[0], lambda: self._authorize_send(row))
        except (OSError, ValueError, RuntimeError, KeyError):
            status, reply = None, None
        finally:
            credential.clear()
        try:
            self._authorize_send(row)
            proof = self.admission.receipt(row)
            accepted = (proof is not None and isinstance(reply, dict) and set(reply) == {'id', 'status'}
                        and reply['id'] == proof['id'] and isinstance(reply['status'], str)
                        and reply['status'] in {'queued', 'running', 'done', 'failed', 'interrupted', 'cancelled', 'quarantined'})
            if status == 200 and accepted:
                return self.log.reconcile(row['binding'], row['event_id'])
            unknown = self.log.interrupt(row['binding'], row['event_id'], row['attempt'])
            if proof is None and self.admission.negative(row) is not None:
                return self.log.reject(row['binding'], row['event_id'], row['attempt'])
            return unknown
        except (OSError, ValueError, RuntimeError, KeyError):
            self.log.interrupt(row['binding'], row['event_id'], row['attempt'])
            raise

    def receive(self, caller, action, request):
        checked = _checked(request)
        if checked['action'].replace('bridge-', 'producer-') != action:
            raise PermissionError('The fixed producer action changed.')
        source = checked['binding']
        with self.store.lock:
            if (not self._configured(source)
                    or caller != {'certificate': source['producer'], 'pool': source['pool']}):
                raise PermissionError('This publisher does not own the configured source capability.')
        event_id = checked['event_id']
        row = self._original(source, event_id)
        if action == 'producer-publish':
            if row is None:
                row = self.log.stage(self._event_binding(source), event_id, checked['raw'].encode())
            elif row['raw'] != checked['raw'] or row['wire_digest'] != checked['wire_digest']:
                raise Conflict('This source event identity already names different bytes.')
        elif row is None:
            raise KeyError('No original publication has this event identity.')
        if action == 'producer-receipt':
            return self._public(self.log.reconcile(row['binding'], event_id))
        retry_key = checked['retry_key']
        if action == 'producer-retry':
            self.log.retry(row['binding'], event_id, retry_key)
        else:
            row = self.log.reconcile(row['binding'], event_id)
        row, acquired = self.log.claim(row['binding'], event_id, retry_key=retry_key)
        return self._public(self._send(row) if acquired else row)
