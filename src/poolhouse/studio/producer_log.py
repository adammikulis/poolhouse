"""Attempt-bound input publication, with deduplication across producer replacements."""
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

    def __init__(self, store, *, current, receipt, negative):
        self.store, self.graph, self.lock = store, store.graph, store.lock
        self.current, self.receipt, self.negative = current, receipt, negative
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
            row = {**fixed, 'state': 'queued', 'timestamp': str(int(time.time())), 'not_sent': True,
                   'revision': 0, 'command': None, 'command_not_attempted': True}
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

    def claim(self, binding, event_id, *, retry_key=None):
        """Return send permission once; nonqueued rows require the caller's existing read authority."""
        key, pending = self._keys(binding, event_id)
        with self.lock, self.graph.transaction():
            row = self.get(binding, event_id)
            if row['state'] != 'queued' or row.get('command') != retry_key:
                return row, False
            if retry_key is not None:
                command = self.graph.get_doc(self._command_key(binding, event_id, retry_key))
                if not self._same_command(command, row):
                    return row, False
            elif row.get('revision') != 0:
                return row, False
            if self.graph.get_doc(pending) != {'key': key, 'binding': binding}:
                raise PermissionError('The pending publication identity changed.')
            if (row.get('command_not_attempted') is not True or 'attempt' in row
                    or (retry_key is None and row.get('not_sent') is not True)):
                row.update(state='interrupted', not_sent=False, reason='Publication attempt evidence requires reconciliation.')
                return self._put(key, row), False
            try:
                self._allowed(binding)
            except (PermissionError, OSError, ValueError):
                row.update(state='blocked', reason='Publication authority is unavailable.')
                return self._put(key, row), False
            row.update(state='intent', not_sent=False, command_not_attempted=False,
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
            retry_command = (row.get('command') is not None and self._same_command(
                self.graph.get_doc(self._command_key(binding, event_id, row['command'])), row))
            if (row['state'] not in {'queued', 'blocked'} or 'attempt' in row
                    or row.get('command_not_attempted') is not True
                    or (row.get('not_sent') is not True and not retry_command)):
                raise Conflict('An attempted publication cannot be discarded as unsent.')
            if self.graph.get_doc(pending) != {'key': key, 'binding': binding}:
                raise PermissionError('The pending publication identity changed.')
            row.update(state='discarded', command_not_attempted=False,
                       cancelled_command={'key': row['command'], 'revision': row['revision']},
                       reason='The source owner abandoned this unattempted command.')
            self._put(key, row)
            self.graph.delete_doc(pending)
            if authorize(copy.deepcopy(row)) is not True:
                raise PermissionError('Source-owner authorization changed.')
            return copy.deepcopy(row)

    def _settle(self, binding, event_id, row):
        proof = self.receipt(copy.deepcopy(row))
        expected = {'source': binding['source'], 'generation': binding['generation'],
                    'event_id': event_id, 'wire_digest': row['wire_digest']}
        if (not isinstance(proof, dict) or set(proof) != {*expected, 'id', 'status'}
                or any(proof.get(name) != value for name, value in expected.items())
                or not isinstance(proof['id'], str) or not re.fullmatch('[0-9a-f]{64}', proof['id'])
                or proof['status'] not in {'queued', 'running', 'done', 'failed', 'interrupted', 'cancelled', 'quarantined'}):
            return None
        key, pending = self._keys(binding, event_id)
        row.update(state='done', receipt=copy.deepcopy(proof), command_not_attempted=False)
        self._put(key, row)
        if self.graph.get_doc(pending) == {'key': key, 'binding': binding}:
            self.graph.delete_doc(pending)
        return copy.deepcopy(row)

    def reconcile(self, binding, event_id):
        """Actual inbox evidence wins even after a definitive rejection or queued retry."""
        with self.lock, self.graph.transaction():
            row = self.get(binding, event_id)
            if row['state'] in {'done', 'discarded'} or row.get('not_sent') is True:
                return row
            settled = self._settle(binding, event_id, row)
            return settled if settled is not None else self.interrupt(binding, event_id, row.get('attempt'))

    def _negative(self, row):
        expected = {'source': row['binding']['source'], 'generation': row['binding']['generation'],
                    'event_id': row['event_id'], 'wire_digest': row['wire_digest'],
                    'binding_digest': digest(row['binding']), 'attempt': row.get('attempt'),
                    'status': 'not_admitted'}
        actual = self.negative(copy.deepcopy(row))
        attempt = expected['attempt']
        if (not isinstance(attempt, dict) or set(attempt) != {'instance', 'token'}
                or any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)
                       for value in attempt.values())
                or actual != expected or not isinstance(actual, dict)):
            raise Conflict('Definitive receiver evidence for this exact attempt is required.')
        return copy.deepcopy(actual)

    def reject(self, binding, event_id, attempt):
        """The trusted receiver checks its marker and absence of inbox in this same transaction."""
        key, pending = self._keys(binding, event_id)
        with self.lock, self.graph.transaction():
            row = self.get(binding, event_id)
            settled = self._settle(binding, event_id, row)
            if settled is not None:
                return settled
            if row['state'] == 'rejected' and row.get('attempt') == attempt:
                self._negative(row)
                return row
            if row['state'] not in {'intent', 'interrupted'} or row.get('attempt') != attempt:
                raise Conflict('The publication attempt changed.')
            if self.graph.get_doc(pending) != {'key': key, 'binding': binding}:
                raise PermissionError('The pending publication identity changed.')
            evidence = self._negative(row)
            row.update(state='rejected', not_sent=False, rejection=evidence)
            self._put(key, row)
            self.graph.delete_doc(pending)
            self._negative(row)
            return copy.deepcopy(row)

    @staticmethod
    def _command_key(binding, event_id, retry_key):
        if not isinstance(retry_key, str) or not re.fullmatch('[0-9a-f]{32}', retry_key):
            raise ValueError('A stable retry command identity is required.')
        return 'studio.producer-retry:' + digest({'source': binding['source'], 'event': event_id, 'retry': retry_key})

    @staticmethod
    def _same_command(command, row):
        if not isinstance(command, dict) or set(command) != {'row'} or not isinstance(command['row'], dict):
            return False
        original = command['row']
        fields = ('binding', 'event_id', 'raw', 'wire_digest', 'revision', 'command', 'history')
        return (original.get('state') == 'queued' and original.get('not_sent') is False
                and original.get('command_not_attempted') is True
                and all(original.get(name) == row.get(name) for name in fields))

    def retry(self, binding, event_id, retry_key):
        """Queue an explicit command; repeated keys return its original snapshot, never send authority."""
        key, pending = self._keys(binding, event_id)
        command_key = self._command_key(binding, event_id, retry_key)
        with self.lock, self.graph.transaction():
            self._allowed(binding)
            row = self.get(binding, event_id)
            previous = self.graph.get_doc(command_key)
            if previous:
                if previous['row']['binding'] != binding:
                    raise Conflict('This retry command belongs to another binding.')
                self._allowed(binding)
                return copy.deepcopy(previous['row'])
            settled = self._settle(binding, event_id, row)
            if settled is not None:
                self._allowed(binding)
                return settled
            if row['state'] != 'rejected':
                raise Conflict('Only a definitively rejected publication can be retried.')
            original = copy.deepcopy(row)
            evidence = self._negative(original)
            if self.graph.get_doc(pending):
                raise Conflict('An earlier producer publication must be reconciled first.')
            history = [*row.get('history', []), {'attempt': row['attempt'], 'rejection': evidence,
                                                 'revision': row['revision'], 'command': row['command']}]
            row.pop('attempt')
            row.pop('rejection')
            row.update(state='queued', revision=row['revision'] + 1, command=retry_key,
                       command_not_attempted=True, history=history, timestamp=str(int(time.time())))
            self._put(key, row)
            self.graph.put_doc(command_key, {'row': row})
            self.graph.put_doc(pending, {'key': key, 'binding': copy.deepcopy(binding)})
            self._negative(original)
            self._allowed(binding)
            return copy.deepcopy(row)

    def recover(self, binding, event_id):
        """Recovery addresses an exact indexed publication and never resubmits it."""
        row = self.get(binding, event_id)
        if row['state'] == 'intent' and row['attempt']['instance'] != self.instance:
            return self.interrupt(binding, event_id, row['attempt'])
        return row
