"""One-attempt input publication, with deduplication across producer replacements."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
import time

from poolhouse.graph.guard import MAX_BODY
from poolhouse.json_objects import unique_object

from .store import Conflict, digest

EVENT_ID = re.compile(r'[A-Za-z0-9_.-]{1,128}\Z')


def wire_record(raw: bytes) -> tuple[str, str]:
    """Pin exact bytes before interpreting the bounded, finite JSON object."""
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BODY:
        raise ValueError('A bounded JSON object is required.')
    checksum = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode('utf-8')
        record = json.loads(text, object_pairs_hook=unique_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
        if not isinstance(record, dict):
            raise ValueError('A JSON object is required.')
        json.dumps(record, allow_nan=False)
    except (ValueError, UnicodeError, OverflowError, RecursionError) as exc:
        raise ValueError('A finite JSON object with unique keys is required.') from exc
    return text, checksum


class ProducerLog:
    """Private daemon mechanics, not a publisher-facing authorization API.

    Every caller must first prove its current process capability or historical receipt access.
    Reads, recovery, reconciliation and nonqueued claims deliberately do not require the old
    execution binding to remain active. They never authorize a send or return raw records to an
    SDK. The receipt callback independently authorizes and verifies the original inbox tuple.
    """

    def __init__(self, store, *, current, receipt):
        self.store, self.graph, self.lock = store, store.graph, store.lock
        self.current, self.receipt = current, receipt
        self.instance = secrets.token_hex(32)

    def _allowed(self, binding):
        if self.current(copy.deepcopy(binding)) is not True:
            raise PermissionError('The approved producer is no longer authorized.')

    @staticmethod
    def _keys(binding, event_id):
        if (not isinstance(binding, dict) or not isinstance(binding.get('source'), str)
                or not re.fullmatch('[0-9a-f]{32}', binding['source'])
                or not isinstance(binding.get('generation'), str)
                or not re.fullmatch('[0-9a-f]{32}', binding['generation'])
                or not isinstance(binding.get('scope'), str) or not binding['scope']
                or not isinstance(event_id, str) or not EVENT_ID.fullmatch(event_id)):
            raise ValueError('A server-owned source binding and stable event identity are required.')
        # A replacement must not turn a previously attempted event into new work.
        key = 'studio.producer-event:' + digest({'source': binding['source'], 'event': event_id})
        return key, 'studio.producer-pending:' + binding['source']

    def _put(self, key, row):
        self.graph.put_doc(key, row)
        return copy.deepcopy(row)

    def stage(self, binding, event_id, raw):
        text, checksum = wire_record(raw)
        key, pending = self._keys(binding, event_id)
        fixed = {'binding': copy.deepcopy(binding), 'event_id': event_id, 'raw': text, 'wire_digest': checksum}
        with self.lock, self.graph.transaction():
            self._allowed(binding)
            previous = self.graph.get_doc(key)
            if previous:
                if any(previous.get(name) != value for name, value in fixed.items()):
                    raise Conflict('This producer event identity already names different bytes or a different binding.')
                self._allowed(binding)
                return copy.deepcopy(previous)
            pointer = self.graph.get_doc(pending)
            if pointer:
                raise Conflict('An earlier producer publication must be reconciled before another event.')
            row = {**fixed, 'state': 'queued', 'timestamp': str(int(time.time())), 'not_sent': True}
            self._put(key, row)
            self.graph.put_doc(pending, {'key': key, 'binding': copy.deepcopy(binding)})
            self._allowed(binding)
            return copy.deepcopy(row)

    def get(self, binding, event_id):
        """Read only after the daemon has independently authorized this exact historical binding."""
        key, _ = self._keys(binding, event_id)
        with self.lock:
            row = self.graph.get_doc(key)
            if not row or row['binding'] != binding or row.get('event_id') != event_id:
                raise PermissionError('No publication belongs to this exact producer binding.')
            if (not isinstance(row.get('raw'), str)
                    or hashlib.sha256(row['raw'].encode()).hexdigest() != row.get('wire_digest')):
                raise PermissionError('The publication bytes no longer match its intent.')
            return copy.deepcopy(row)

    def claim(self, binding, event_id):
        """Return send permission once; nonqueued rows require the caller's existing read authority."""
        key, pending = self._keys(binding, event_id)
        with self.lock, self.graph.transaction():
            row = self.get(binding, event_id)
            if row['state'] != 'queued':
                return row, False
            if self.graph.get_doc(pending) != {'key': key, 'binding': binding}:
                raise PermissionError('The pending publication identity changed.')
            if row.get('not_sent') is not True or 'attempt' in row:
                row.update(state='interrupted', not_sent=False, reason='Publication attempt evidence requires reconciliation.')
                return self._put(key, row), False
            try:
                self._allowed(binding)
            except (PermissionError, OSError, ValueError):
                row.update(state='blocked', not_sent=True, reason='Publication authority is unavailable.')
                return self._put(key, row), False
            row.update(state='intent', not_sent=False,
                       attempt={'instance': self.instance, 'token': secrets.token_hex(32)})
            self._put(key, row)
            # A revocation race rolls back the intent; it cannot leave an exposed claim.
            self._allowed(binding)
            return copy.deepcopy(row), True

    def interrupt(self, binding, event_id, attempt):
        key, _ = self._keys(binding, event_id)
        with self.lock, self.graph.transaction():
            row = self.get(binding, event_id)
            if row['state'] == 'intent' and row.get('attempt') == attempt:
                row.update(state='interrupted', reason='The publication outcome is unconfirmed.')
                return self._put(key, row)
            return row

    def discard(self, binding, event_id, *, authorize):
        """A separately authenticated owner may abandon only a provably unattempted event."""
        key, pending = self._keys(binding, event_id)
        with self.lock, self.graph.transaction():
            row = self.get(binding, event_id)
            if authorize(copy.deepcopy(row)) is not True:
                raise PermissionError('Current source-owner authorization is required.')
            if row['state'] == 'discarded':
                if authorize(copy.deepcopy(row)) is not True:
                    raise PermissionError('Source-owner authorization changed.')
                return row
            if row['state'] not in {'queued', 'blocked'} or row.get('not_sent') is not True or 'attempt' in row:
                raise Conflict('An attempted publication cannot be discarded as unsent.')
            if self.graph.get_doc(pending) != {'key': key, 'binding': binding}:
                raise PermissionError('The pending publication identity changed.')
            row.update(state='discarded', reason='The source owner abandoned this unattempted publication.')
            self._put(key, row)
            self.graph.delete_doc(pending)
            if authorize(copy.deepcopy(row)) is not True:
                raise PermissionError('Source-owner authorization changed.')
            return copy.deepcopy(row)

    def reconcile(self, binding, event_id):
        """Only actual exact inbox evidence can settle an attempted publication."""
        key, pending = self._keys(binding, event_id)
        row = self.get(binding, event_id)
        if row['state'] not in {'intent', 'interrupted'}:
            return row
        proof = self.receipt(copy.deepcopy(row))
        expected = {'source': binding['source'], 'generation': binding['generation'],
                    'event_id': event_id, 'wire_digest': row['wire_digest']}
        if (not isinstance(proof, dict) or set(proof) != {*expected, 'id', 'status'}
                or any(proof.get(name) != value for name, value in expected.items())
                or not isinstance(proof['id'], str) or not re.fullmatch('[0-9a-f]{64}', proof['id'])
                or proof['status'] not in {'queued', 'running', 'done', 'failed', 'interrupted', 'cancelled', 'quarantined'}):
            return self.interrupt(binding, event_id, row.get('attempt'))
        with self.lock, self.graph.transaction():
            actual = self.get(binding, event_id)
            if actual != row:
                return actual
            actual.update(state='done', receipt=copy.deepcopy(proof))
            self._put(key, actual)
            pointer = self.graph.get_doc(pending)
            if pointer != {'key': key, 'binding': binding}:
                raise PermissionError('The pending publication identity changed.')
            self.graph.delete_doc(pending)
            return copy.deepcopy(actual)

    def recover(self, binding, event_id):
        """Recovery addresses an exact indexed publication and never resubmits it."""
        row = self.get(binding, event_id)
        if row['state'] == 'intent' and row['attempt']['instance'] != self.instance:
            return self.interrupt(binding, event_id, row['attempt'])
        return row
