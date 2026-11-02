"""An explicit owner source barrier can settle an original receipt, never repeat its data."""
from __future__ import annotations

import copy

from poolhouse.studio.producer_log import ProducerLog

from .producer_connections import _BINDING


class OwnerBarrier:
    """Run only inside the source configuration/admission transaction after disabling its generation."""

    def __init__(self, service, publications):
        self.service, self.publications, self.store = service, publications, service.store
        self.admission = publications.admission

    def _owner(self, principal, context, old_source, new_source):
        if (self.service.authority._owner(principal) != context
                or old_source['scope'] != context['scope'] or new_source['scope'] != context['scope']
                or old_source['id'] != new_source['id'] or old_source['generation'] == new_source['generation']
                or self.store.get('source', new_source['id'], context['scope']) != new_source):
            raise PermissionError('The explicit source revocation barrier changed.')

    def _original(self, row, old_source, intent, context):
        binding = row['binding']
        if (set(binding) != _BINDING | {'subscription', 'grant'}
                or {key: binding[key] for key in _BINDING} != intent.get('binding')
                or any(binding.get(name) != value for name, value in {
                    'source': old_source['id'], 'generation': old_source['generation'], 'scope': context['scope'],
                    'pool': context['pool']}.items())
                or any(intent['context'].get(name) != context[name] for name in ('scope', 'pool', 'device'))):
            raise PermissionError('The pending publication belongs to another original source.')
        with self.service.authority.lock:
            grant = self.service.authority.graph.get_doc('studio.authority.grant:' + binding['grant'])
        sub = self.store.get('subscription', binding['subscription'], context['scope'])
        if (not grant or grant.get('id') != binding['grant'] or grant.get('subscription') != sub['id'] or sub['source_id'] != old_source['id']
                or grant.get('manifest', {}).get('source') != old_source
                or any(grant.get(name) != context[name] for name in ('scope', 'pool', 'device'))):
            raise PermissionError('The original publication execution tuple changed.')
        self.admission._intent(row)

    def settle(self, principal, old_source, intent, new_source):
        """Use current owner proof rather than the source capability that this operation just revoked."""
        context = self.service.authority._owner(principal)
        with self.store.lock, self.service.authority.lock, self.store.graph.transaction():
            self._owner(principal, context, old_source, new_source)
            pointer_key = 'studio.producer-pending:' + old_source['id']
            pointer = self.store.graph.get_doc(pointer_key)
            if pointer is None:
                return None
            if not isinstance(pointer, dict) or set(pointer) != {'key', 'binding'}:
                raise PermissionError('The original pending publication index changed.')
            row = self.store.graph.get_doc(pointer['key'])
            if not row:
                raise PermissionError('The original pending publication is missing.')
            key, _ = ProducerLog._keys(row['binding'], row['event_id'])
            if pointer != {'key': key, 'binding': row['binding']}:
                raise PermissionError('The original pending publication identity changed.')

            def allowed(actual):
                self._owner(principal, context, old_source, new_source)
                self._original(actual, old_source, intent, context)
                return True

            def receipt(actual):
                allowed(actual)
                event = self.admission._inbox(actual)
                allowed(actual)
                if event is None:
                    return None
                return {'source': actual['binding']['source'], 'generation': actual['binding']['generation'],
                        'event_id': actual['event_id'], 'wire_digest': actual['wire_digest'],
                        'id': event['id'], 'status': event['state']}

            def negative(actual):
                allowed(actual)
                marker = self.store.graph.get_doc(self.admission._marker(actual))
                if self.admission._inbox(actual) is not None or marker != self.admission._proof(actual):
                    return None
                allowed(actual)
                return copy.deepcopy(marker)

            allowed(row)
            log = ProducerLog(self.store, current=lambda _: False, receipt=receipt, negative=negative)
            proof = receipt(row)
            if proof is not None:
                result = log.reconcile(row['binding'], row['event_id'])
            elif row.get('attempt') is not None:
                self.store.graph.put_doc(self.admission._marker(row), self.admission._proof(row))
                result = log.reject(row['binding'], row['event_id'], row['attempt'])
            else:
                result = log.discard(row['binding'], row['event_id'], authorize=allowed)
            if result['state'] not in {'done', 'rejected', 'discarded'}:
                raise PermissionError('The original publication needs exact receipt reconciliation.')
            if self.store.graph.get_doc(pointer_key) == pointer:
                self.store.graph.delete_doc(pointer_key)
            allowed(result)
            return self.publications._public(result)
