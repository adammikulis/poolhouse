"""Observational process offers; source authority is granted separately by an owner."""
from __future__ import annotations

import copy
import math
import re
import secrets
import threading

from poolhouse.studio.store import Conflict

_HEX = re.compile('[0-9a-f]{64}\\Z')
_CONTEXT = {'pool', 'runtime', 'daemon_instance', 'device_certificate'}
_RECEIVER = {'id', 'certificate', 'pool', 'label', 'device_label'}


def _process(value):
    if (not isinstance(value, dict) or set(value) != {'pid', 'started'}
            or type(value['pid']) is not int or value['pid'] <= 0
            or type(value['started']) not in (int, float)
            or not math.isfinite(value['started']) or value['started'] <= 0):
        raise ValueError('A live transport process is required.')
    return copy.deepcopy(value)


def _text(value, maximum=256):
    return isinstance(value, str) and 0 < len(value.encode('utf-8')) <= maximum


class OfferRegistry:
    """RAM-only process evidence, never a credential or enrollment API.

    Transport supplies freshly kernel-verified processes. Only a trusted daemon
    caller, after independent owner authorization, may use inspect for enrollment.
    Receiver discovery is supplied by the current enrolled-device registry; SDK
    requests cannot provide certificates, endpoints, process identity or code.
    """

    def __init__(self, *, context, receivers, alive, implementation, device_label):
        if not isinstance(implementation, str) or not _HEX.fullmatch(implementation):
            raise ValueError('A verified implementation identity is required.')
        if not _text(device_label):
            raise ValueError('A server-owned producer device label is required.')
        self.context, self.receivers, self.alive = context, receivers, alive
        self.implementation = implementation
        self.device_label = device_label
        self.lock = threading.RLock()
        self.anchor = self._context()
        self.rows, self.keys = {}, {}

    def _context(self):
        value = self.context()
        if (not isinstance(value, dict) or set(value) != _CONTEXT
                or any(not isinstance(value[key], str) or not _HEX.fullmatch(value[key])
                       for key in ('pool', 'runtime', 'daemon_instance'))
                or not _text(value['device_certificate'], 32768)):
            raise PermissionError('Current software context is unavailable.')
        return copy.deepcopy(value)

    def _receivers(self):
        values = self.receivers()
        if not isinstance(values, list):
            raise PermissionError('Current receiver discovery is unavailable.')
        result = {}
        for row in values:
            if (not isinstance(row, dict) or set(row) != _RECEIVER
                    or not _text(row['id']) or row['id'] in result
                    or not _text(row['certificate'], 32768)
                    or row['pool'] != self.anchor['pool']
                    or not _text(row['label']) or not _text(row['device_label'])):
                raise PermissionError('Current receiver discovery is invalid.')
            result[row['id']] = copy.deepcopy(row)
        return result

    def _check(self, process, receiver):
        if (self._context() != self.anchor or self.alive(copy.deepcopy(process)) is not True
                or self._receivers().get(receiver['id']) != receiver
                or self._context() != self.anchor):
            raise PermissionError('The software process or intended receiver changed.')

    def receivers_for(self, process):
        process = _process(process)
        with self.lock:
            if self._context() != self.anchor or self.alive(process) is not True:
                raise PermissionError('The software process is unavailable.')
            values = self._receivers()
            for receiver in values.values():
                self._check(process, receiver)
            if self._context() != self.anchor or self.alive(process) is not True:
                raise PermissionError('The software process changed.')
            return list(values.values())

    def offer(self, process, request):
        process = _process(process)
        if (not isinstance(request, dict) or set(request) != {'receiver_id', 'label'}
                or not _text(request['receiver_id']) or not _text(request['label'])):
            raise ValueError('Choose an intended receiver and a software label.')
        with self.lock:
            receiver = self._receivers().get(request['receiver_id'])
            if receiver is None:
                raise PermissionError('The intended receiver is not enrolled.')
            self._check(process, receiver)
            key = (process['pid'], process['started'], receiver['id'])
            previous = self.rows.get(self.keys.get(key))
            if previous:
                if previous['label'] != request['label']:
                    raise Conflict('This software offer already has a different label.')
                return copy.deepcopy(previous)
            row = {'id': secrets.token_hex(16), 'revision': 1, 'state': 'offered',
                   'process': process, 'receiver': receiver, 'context': self.anchor,
                   'implementation': self.implementation, 'label': request['label'],
                   'device_label': self.device_label}
            self._check(process, receiver)
            self.rows[row['id']] = copy.deepcopy(row)
            self.keys[key] = row['id']
            return copy.deepcopy(row)

    def _owned(self, process, ident):
        row = self.rows.get(ident)
        if not row or row['process'] != process:
            raise PermissionError('No offer belongs to this software process.')
        self._check(process, row['receiver'])
        return row

    def status(self, process, ident):
        process = _process(process)
        with self.lock:
            row = self._owned(process, ident)
            self._check(process, row['receiver'])
            return copy.deepcopy(row)

    def withdraw(self, process, ident):
        process = _process(process)
        with self.lock:
            row = self._owned(process, ident)
            result = {**row, 'state': 'withdrawn'}
            self._check(process, row['receiver'])
            self.rows[ident] = result
            return copy.deepcopy(result)

    def inspect(self, ident, revision):
        """Return private enrollment evidence only to independently authorized daemon code."""
        with self.lock:
            row = self.rows.get(ident)
            if not row or type(revision) is not int or row['revision'] != revision:
                raise Conflict('Refresh the current software offer.')
            if row['state'] != 'offered':
                raise PermissionError('The software offer was withdrawn.')
            self._check(row['process'], row['receiver'])
            result = copy.deepcopy(row)
            self._check(row['process'], row['receiver'])
            return result

    def discover(self, receiver):
        """Owner-facing metadata only, filtered to the exact intended current receiver."""
        with self.lock:
            if self._context() != self.anchor or self._receivers().get(receiver.get('id')) != receiver:
                raise PermissionError('The intended receiver is unavailable.')
            result = []
            for row in self.rows.values():
                if row['receiver'] != receiver or row['state'] != 'offered':
                    continue
                try:
                    self._check(row['process'], receiver)
                except PermissionError:
                    continue
                result.append({'id': row['id'], 'revision': row['revision'], 'label': row['label'],
                               'device_label': row['device_label'], 'software_label': row['label'],
                               'available': True, 'reason': ''})
            if self._context() != self.anchor or self._receivers().get(receiver['id']) != receiver:
                raise PermissionError('The intended receiver changed.')
            return result
