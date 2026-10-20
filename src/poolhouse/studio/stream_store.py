"""Transactional stream inboxes and bounded active-work indexes."""
from __future__ import annotations

import copy
import re
import secrets
import time

from .events import checked_event
from .store import Conflict, digest


class StreamStore:
    """Keep source cursors, durable events and history outside editor graphs."""

    def __init__(self, store) -> None:
        self.store, self.graph, self.lock = store, store.graph, store.lock
        for table in ('StudioSubscriptionActive', 'StudioSourceIndex', 'StudioSubscriptionIndex'):
            self.graph.query(f'CREATE NODE TABLE IF NOT EXISTS {table}(id STRING, scope STRING, PRIMARY KEY(id))')

    def get(self, kind: str, ident: str, scope: str) -> dict:
        return self.store.get(kind, ident, scope)

    def indexed(self, kind: str, scope: str) -> list[dict]:
        table = {'source': 'StudioSourceIndex', 'subscription': 'StudioSubscriptionIndex'}[kind]
        with self.lock:
            rows = self.graph.query(f'MATCH (a:{table}) WHERE a.scope=$scope RETURN a.id AS id', {'scope': scope})
            return [self.get(kind, row['id'], scope) for row in rows]

    def create(self, kind: str, principal: dict, config: dict, key: str) -> dict:
        if kind not in {'source', 'subscription'} or not isinstance(key, str) or not key or len(key) > 256:
            raise ValueError('A source or subscription creation identity is required.')
        ident = 'studio.stream-create:' + digest({'scope': principal['scope'], 'principal': principal['id'], 'kind': kind, 'key': key})
        fingerprint = digest(config)
        with self.lock, self.graph.transaction():
            prior = self.graph.get_doc(ident)
            if prior:
                if prior['digest'] != fingerprint:
                    raise Conflict('This creation identity already names another configuration.')
                return self.get(kind, prior['id'], principal['scope'])
            row = {**copy.deepcopy(config), 'id': secrets.token_hex(16), 'scope': principal['scope'],
                   'owner': principal['id'], 'revision': 1, 'created_at': time.time()}
            if kind == 'subscription':
                row.update(state='paused', next_sequence=1, cursor=None, inflight=None, counts={})
            self.store.put(kind, row)
            table = 'StudioSourceIndex' if kind == 'source' else 'StudioSubscriptionIndex'
            self.graph.query(f'MERGE (a:{table} {{id:$id}}) SET a.scope=$scope', {'id': row['id'], 'scope': row['scope']})
            self.graph.put_doc(ident, {'digest': fingerprint, 'id': row['id']})
            return row

    def put_subscription(self, row: dict) -> dict:
        with self.lock, self.graph.transaction():
            self.store.put('subscription', row)
            if row['state'] == 'active' or row.get('inflight'):
                self.graph.query('MERGE (a:StudioSubscriptionActive {id:$id}) SET a.scope=$scope', row_params(row))
            else:
                self.graph.query('MATCH (a:StudioSubscriptionActive {id:$id}) DELETE a', {'id': row['id']})
            return copy.deepcopy(row)

    def active(self) -> list[dict]:
        with self.lock:
            rows = self.graph.query('MATCH (a:StudioSubscriptionActive) RETURN a.id AS id, a.scope AS scope')
            result = []
            for row in rows:
                try:
                    sub = self.get('subscription', row['id'], row['scope'])
                    if sub['state'] != 'active' and not sub.get('inflight'):
                        raise ValueError('Subscription active index requires reconciliation.')
                    result.append(sub)
                except (KeyError, ValueError, TypeError):
                    self.graph.put_doc('studio.stream-index-error:' + row['id'],
                                       {'subscription': row['id'], 'error': 'Active subscription requires reconciliation.'})
            return result

    def admit(self, subscription: dict, incoming: dict, principal: dict, authorize) -> dict:
        """Acknowledge only after event, dedupe pointer and source cursor commit."""
        if (not isinstance(incoming, dict) or set(incoming) - {'id', 'generation', 'payload', 'error', 'cursor', 'digest'}
                or any(not isinstance(incoming.get(name), str) or not incoming[name] or len(incoming[name]) > 256
                       for name in ('id', 'generation'))
                or ('payload' in incoming) == ('error' in incoming)
                or ('digest' in incoming and not re.fullmatch('[0-9a-f]{64}', str(incoming['digest'])))):
            raise ValueError('Invalid source event envelope.')
        with self.lock, self.graph.transaction():
            sub = self.get('subscription', subscription['id'], subscription['scope'])
            if sub.get('grant') != principal.get('grant_id') or authorize(sub, principal) is not True:
                raise PermissionError('Subscription authority changed before admission.')
            key = 'studio.inbox:' + digest({'subscription': sub['id'], 'source': sub['source_id'],
                                            'generation': incoming['generation'], 'id': incoming['id']})
            prior = self.graph.get_doc(key)
            body_digest = digest(incoming.get('payload', {'error': incoming.get('error')}))
            if prior:
                if prior['digest'] != body_digest or prior.get('wire_digest') != incoming.get('digest'):
                    raise Conflict('This source event identity already names different content.')
                return self.get('stream-event', prior['event'], sub['scope'])
            if any(sub.get(name) != subscription.get(name) for name in ('revision', 'cursor', 'source_id', 'grant')):
                raise Conflict('The source cursor or subscription changed during intake.')
            if sub['state'] != 'active':
                raise PermissionError('This subscription is paused.')
            if sub.get('inflight'):
                raise BlockingIOError('The previous record is still being processed.')
            sequence = sub['next_sequence']
            event_id = digest({'subscription': sub['id'], 'sequence': sequence})
            row = {'id': event_id, 'scope': sub['scope'], 'subscription': sub['id'], 'sequence': sequence,
                   'source': sub['source_id'], 'generation': incoming['generation'], 'source_event': incoming['id'],
                   'principal': copy.deepcopy(principal), 'received_at': time.time(), 'digest': body_digest,
                   'state': 'quarantined' if 'error' in incoming else 'queued'}
            if 'error' in incoming:
                row['error'] = incoming['error']
            else:
                envelope = checked_event({'id': event_id, 'subscription': sub['id'], 'sequence': sequence,
                                          'source': sub['source_id'], 'generation': incoming['generation'],
                                          'payload': incoming['payload'], 'digest': body_digest,
                                          'input_node': sub['input_node'], 'output_nodes': sub['output_nodes']})
                row['envelope'] = envelope
                sub['inflight'] = event_id
            sub.update(next_sequence=sequence + 1, cursor=copy.deepcopy(incoming.get('cursor', sub.get('cursor'))))
            self.store.put('stream-event', row)
            self.graph.put_doc(key, {'digest': body_digest, 'wire_digest': incoming.get('digest'), 'event': event_id})
            self.graph.put_doc(sequence_key(sub['id'], sequence), {'id': event_id})
            self.put_subscription(sub)
            return copy.deepcopy(row)

    def page(self, sub: dict, after: int = 0, *, limit: int = 50, tail: bool = False) -> dict:
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Select a valid event page.')
        with self.lock:
            current = self.get('subscription', sub['id'], sub['scope'])
            end = current['next_sequence']
            start = max(1, end - limit) if tail else after + 1
            events = []
            for sequence in range(start, min(end, start + limit)):
                pointer = self.graph.get_doc(sequence_key(sub['id'], sequence))
                if not pointer:
                    raise ValueError('Event history requires reconciliation.')
                row = self.get('stream-event', pointer['id'], sub['scope'])
                if row['subscription'] != sub['id'] or row['sequence'] != sequence:
                    raise ValueError('Event history identity does not match.')
                events.append(row)
            return {'events': events, 'next_cursor': events[-1]['sequence'] if events else after}


def row_params(row: dict) -> dict:
    return {'id': row['id'], 'scope': row['scope']}


def sequence_key(subscription: str, sequence: int) -> str:
    return 'studio.event-sequence:' + subscription + ':' + str(sequence)
