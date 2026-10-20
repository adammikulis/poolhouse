"""Receiver-owned admission and definitive rejection of exact producer attempts."""
from __future__ import annotations

import copy
import json
import re

from poolhouse.studio.producer_log import ProducerLog, wire_record
from poolhouse.studio.store import Conflict, digest


class Admission:
    """Private callbacks share the receiver inbox transaction; no network status is evidence.

    The factory supplies current source/grant authorization and separately authorized historical
    receipt access. Neither callback may perform network operations or reconstruct a person.
    """

    def __init__(self, service, *, current, receipt_current):
        self.service, self.store = service, service.store
        self.graph, self.lock = self.store.graph, self.store.lock
        self.current, self.receipt_current = current, receipt_current

    def _allowed(self, row, *, historical=False):
        allowed = self.receipt_current(copy.deepcopy(row)) if historical else self.current(copy.deepcopy(row['binding']))
        if allowed is not True:
            raise PermissionError('The original producer publication is no longer authorized.')

    @staticmethod
    def _proof(row):
        return {'source': row['binding']['source'], 'generation': row['binding']['generation'],
                'event_id': row['event_id'], 'wire_digest': row['wire_digest'],
                'binding_digest': digest(row['binding']), 'attempt': copy.deepcopy(row['attempt']),
                'status': 'not_admitted'}

    @staticmethod
    def _marker(row):
        return 'studio.producer-negative:' + digest(Admission._proof(row))

    def _intent(self, row):
        key, _ = ProducerLog._keys(row['binding'], row['event_id'])
        actual = self.graph.get_doc(key)
        if (not isinstance(actual, dict) or any(actual.get(name) != row.get(name)
                for name in ('binding', 'event_id', 'raw', 'wire_digest'))):
            raise PermissionError('The original publication intent changed.')
        _, checksum = wire_record(actual['raw'].encode())
        if checksum != actual['wire_digest']:
            raise Conflict('The publication bytes no longer match their intent.')
        return actual

    def _inbox(self, row):
        binding = row['binding']
        key = 'studio.inbox:' + digest({'subscription': binding['subscription'], 'source': binding['source'],
                                      'generation': binding['generation'], 'id': row['event_id']})
        prior = self.graph.get_doc(key)
        if prior is None:
            return None
        if prior.get('wire_digest') != row['wire_digest'] or prior.get('digest') != digest(json.loads(row['raw'])):
            raise Conflict('The original producer inbox names different content.')
        event = self.store.get('stream-event', prior['event'], binding['scope'])
        expected = {'subscription': binding['subscription'], 'source': binding['source'],
                    'generation': binding['generation'], 'source_event': row['event_id'],
                    'digest': prior['digest']}
        envelope = event.get('envelope', {})
        if (any(event.get(name) != value for name, value in expected.items())
                or event.get('principal', {}).get('grant_id') != binding['grant']
                or any(envelope.get(name) != value for name, value in {
                    'id': event['id'], 'subscription': binding['subscription'], 'source': binding['source'],
                    'generation': binding['generation'], 'digest': prior['digest'],
                    'payload': json.loads(row['raw'])}.items())):
            raise PermissionError('The original producer inbox identity changed.')
        return event

    def receipt(self, row):
        """Historical access is independently proved before and after exact inbox lookup."""
        with self.lock, self.graph.transaction():
            self._allowed(row, historical=True)
            self._intent(row)
            event = self._inbox(row)
            self._allowed(row, historical=True)
            if event is None:
                return None
            return {'source': row['binding']['source'], 'generation': row['binding']['generation'],
                    'event_id': row['event_id'], 'wire_digest': row['wire_digest'],
                    'id': event['id'], 'status': event['state']}

    def negative(self, row):
        """Absence is useful only alongside this receiver's terminal exact-attempt marker."""
        with self.lock, self.graph.transaction():
            self._allowed(row, historical=True)
            actual = self._intent(row)
            proof = self._proof(row)
            if (actual.get('attempt') != row.get('attempt')
                    and not any(item.get('attempt') == row.get('attempt') and item.get('rejection') == proof
                                for item in actual.get('history', []))):
                raise PermissionError('No such original publication attempt exists.')
            if self._inbox(row) is not None:
                self._allowed(row, historical=True)
                return None
            marker = self.graph.get_doc(self._marker(row))
            self._allowed(row, historical=True)
            return copy.deepcopy(marker) if marker == self._proof(row) else None

    def _row(self, sub, incoming):
        if (not isinstance(incoming, dict) or set(incoming) != {'id', 'generation', 'payload', 'digest', 'attempt'}
                or not isinstance(incoming['attempt'], str) or not re.fullmatch('[a-f0-9]{64}', incoming['attempt'])):
            raise ValueError('An exact authenticated producer attempt is required.')
        key, _ = ProducerLog._keys({'source': sub['source_id'], 'generation': incoming['generation'],
                                   'scope': sub['scope']}, incoming['id'])
        row = self.graph.get_doc(key)
        if not row:
            raise PermissionError('No receiver-owned publication intent exists.')
        binding = row['binding']
        if (any(binding.get(name) != value for name, value in {
                'source': sub['source_id'], 'generation': incoming['generation'], 'scope': sub['scope'],
                'subscription': sub['id']}.items())
                or row['event_id'] != incoming['id'] or row['wire_digest'] != incoming['digest']):
            raise PermissionError('The publication does not belong to this subscription.')
        self._intent(row)
        attempts = [row.get('attempt'), *(item.get('attempt') for item in row.get('history', []))]
        if not any(isinstance(attempt, dict) and digest(attempt) == incoming['attempt'] for attempt in attempts):
            raise PermissionError('No such original signed publication attempt exists.')
        _, checksum = wire_record(row['raw'].encode())
        if checksum != row['wire_digest'] or digest(json.loads(row['raw'])) != digest(incoming['payload']):
            raise Conflict('The publication bytes changed before admission.')
        return row

    def admit(self, subscription, incoming):
        """Return rejection through the outer transaction; the HTTP caller raises only after commit."""
        with self.lock, self.graph.transaction():
            sub = self.store.get('subscription', subscription['id'], subscription['scope'])
            row = self._row(sub, incoming)
            self._allowed(row, historical=True)
            event = self._inbox(row)
            if event is not None:
                self._allowed(row, historical=True)
                return event
            source = self.store.get('source', sub['source_id'], sub['scope'])
            if (source.get('kind') != 'webhook' or source.get('provider') != 'software-producer'
                    or source.get('id') != row['binding']['source']
                    or source.get('generation') != row['binding']['generation']
                    or sub.get('grant') != row['binding']['grant']):
                raise PermissionError('The configured producer source changed.')
            self._allowed(row)
            attempt = row.get('attempt')
            if (not isinstance(attempt, dict) or set(attempt) != {'instance', 'token'}
                    or any(not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value)
                           for value in attempt.values()) or digest(attempt) != incoming['attempt']):
                raise PermissionError('This signed publication attempt is no longer eligible.')
            marker = self.graph.get_doc(self._marker(row))
            if marker is not None:
                if marker != self._proof(row):
                    raise PermissionError('The rejection evidence changed.')
                self._allowed(row)
                return {'rejected': True}
            key, pending = ProducerLog._keys(row['binding'], row['event_id'])
            if (row['state'] not in {'intent', 'interrupted'}
                    or self.graph.get_doc(pending) != {'key': key, 'binding': row['binding']}
                    or sub['state'] != 'active'
                    or self.service.authorized(sub, sub['service_principal']) is not True):
                raise PermissionError('This producer attempt cannot enter the inbox.')
            if sub.get('inflight'):
                self.graph.put_doc(self._marker(row), self._proof(row))
                result = {'rejected': True}
            else:
                result = self.service.ingest(sub, {name: value for name, value in incoming.items() if name != 'attempt'})
            self._allowed(row)
            if self.store.get('source', source['id'], source['scope']) != source:
                raise PermissionError('The producer source changed before admission committed.')
            return result
