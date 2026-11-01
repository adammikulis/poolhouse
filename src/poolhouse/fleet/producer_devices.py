"""Node-authorized compute device listing and fixed software-producer transport."""
from __future__ import annotations

import copy
import os
import re
import stat
import threading
from urllib.parse import urlsplit

from poolhouse import home
from poolhouse.files import stat_signature
from poolhouse.studio.store import Conflict, digest

from . import studio_epoch, studio_pool, tls
from .producer_member import FIELDS
from .producer_offers import _process, _text
from .remote import Beacon, Member
from .service_pool import Pool
from .studio_transport import MemberExecution

_HEX32 = re.compile('[0-9a-f]{32}')
_HEX64 = re.compile('[0-9a-f]{64}')
_OFFER = {'id', 'revision', 'state', 'process', 'receiver', 'context', 'implementation', 'label', 'device_label'}


def _machine():
    path = home.state('machine-id')
    with studio_epoch.directory(path.parent) as directory:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != os.geteuid()
                    or before.st_mode & 0o022 or not 0 < before.st_size <= 256):
                raise PermissionError('The existing machine identity is unavailable.')
            raw = os.read(fd, 257)
            after = os.fstat(fd)
            named = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            if stat_signature(before) != stat_signature(after) or stat_signature(before) != stat_signature(named):
                raise PermissionError('The existing machine identity changed.')
            value = raw.decode('ascii').strip()
            if not re.fullmatch('[0-9a-f]{16}', value):
                raise PermissionError('The existing machine identity is invalid.')
            return value
        finally:
            os.close(fd)


def _address(member):
    beacon = member.beacon
    return (member.base_url, member.token, beacon.cert, beacon.machine, beacon.host, beacon.hostname, beacon.port)


class Devices:
    """No listed address, offer or certificate creates source authority.

    The factory supplies its actual service and existing RAM identity. Refresh and requests
    must run outside the shared store lock; receivers is deliberately a local cache read.
    Each transport independently rechecks the node's current pool, service binding and cached address.
    """

    def __init__(self, ui, service, control):
        self.ui, self.service, self._control = ui, service, control
        self.lock, self._members, self._offers = threading.RLock(), {}, {}
        self._control_anchor = None
        self.anchor = copy.deepcopy(service.authority._context())
        self.identity = ui.studio_identity()
        if not isinstance(self.identity, tls.Identity) or self.identity.fingerprint != self.anchor['device']:
            raise PermissionError('The existing daemon identity is unavailable.')
        self.machine = _machine()
        self._current()
        own = self._self()
        self._members[self.identity.beacon] = (own, _address(own))

    def _current(self):
        if (self.service.authority._context() != self.anchor or self.ui.studio_identity() is not self.identity
                or _machine() != self.machine):
            raise PermissionError('The selected producer pool or daemon changed.')
        if studio_pool.current() != self.anchor['pool']:
            raise PermissionError('The selected producer pool changed.')
        try:
            return Pool().own(self.identity)
        except PermissionError as exc:
            raise PermissionError('The current daemon is not enrolled in this pool.') from exc

    def _self(self):
        endpoint = self.ui.studio_endpoint()
        location = urlsplit(endpoint or '')
        if (location.scheme != 'https' or location.hostname != '127.0.0.1'
                or location.username or location.password or location.path not in ('', '/')
                or location.query or location.fragment or not location.port or _machine() != self.machine):
            raise PermissionError('The existing encrypted daemon listener is unavailable.')
        name = self.ui.name or 'This device'
        if not _text(name):
            raise PermissionError('The daemon device label is invalid.')
        token = getattr(self.ui, 'studio_token', lambda: '')()
        if not token:
            raise PermissionError('The existing daemon owner credential is unavailable.')
        beacon = Beacon(name=name, port=location.port, host='127.0.0.1', machine=self.machine, cert=self.identity.beacon)
        return Member(endpoint, token, beacon=beacon, timeout=5, identity=self.identity)

    def _network(self):
        if self.service.store.lock._is_owned():
            raise RuntimeError('Producer network operations cannot hold the Studio store lock.')

    def _allowed(self, member, expected):
        selected = self._current()
        if _address(member) != expected:
            raise PermissionError('The listed producer device changed.')
        member.check_current()
        with self.lock:
            cached = self._members.get(member.beacon.cert)
        if (member.pool != selected.pool or cached is None or cached[0] is not member or cached[1] != expected):
            raise PermissionError('The producer device is no longer a current node-authorized service.')
        if member.beacon.cert == self.identity.beacon and _address(self._self()) != expected:
            raise PermissionError('The current encrypted daemon listener changed.')
        return True

    def refresh(self):
        self._network()
        selected = self._current()
        own = self._self()
        found = {self.identity.beacon: (own, _address(own))}
        for member in Member.discover(timeout=5):
            beacon = member.beacon
            if not beacon or not beacon.cert or not beacon.machine or member.pool != selected.pool:
                raise PermissionError('The node returned an invalid compute device.')
            if beacon.cert == self.identity.beacon:
                continue
            previous = found.get(beacon.cert)
            if previous and previous[1] != _address(member):
                raise Conflict('A producer certificate has ambiguous device addresses.')
            found[beacon.cert] = (member, _address(member))
        if self._current() != selected:
            raise PermissionError('The pool changed during the device listing.')
        with self.lock:
            self._members = {cert: self._members.get(cert, value)
                             if self._members.get(cert, (None, None))[1] == value[1] else value
                             for cert, value in found.items()}
        return self.receivers()

    def _receiver(self, member):
        return {'id': digest({'pool': self.anchor['pool'], 'certificate': member.beacon.cert})[:32],
                'certificate': member.beacon.cert, 'pool': self.anchor['pool'],
                'label': member.name, 'device_label': member.name}

    def receivers(self):
        self._current()
        with self.lock:
            members = list(self._members.values())
        result = []
        for member, expected in members:
            try:
                self._allowed(member, expected)
            except PermissionError:
                continue
            value = self._receiver(member)
            if not _text(value['label']):
                raise PermissionError('The enrolled device label is invalid.')
            result.append(value)
        self._current()
        return result

    def request(self, receiver, action, payload):
        self._network()
        if (not isinstance(receiver, dict) or set(receiver) != {'certificate', 'pool'}
                or receiver['pool'] != self.anchor['pool'] or action not in FIELDS
                or not isinstance(payload, dict) or set(payload) != FIELDS[action]):
            raise ValueError('Choose an enrolled device and fixed producer request.')
        with self.lock:
            cached = self._members.get(receiver['certificate'])
        if cached is None:
            raise PermissionError('This device is not a current node-authorized compute service.')
        member, expected = cached
        self._allowed(member, expected)
        transport = MemberExecution(member, lambda: self._allowed(member, expected), identity=self.identity)
        return transport.request(action, payload)

    def discover(self):
        receivers = self.refresh()
        own = next((row for row in receivers if row['certificate'] == self.identity.beacon), None)
        if own is None:
            raise PermissionError('The intended current receiver is unavailable.')
        offers, mappings = [], {}
        for receiver in receivers:
            target = {key: receiver[key] for key in ('certificate', 'pool')}
            response = self.request(target, 'producer-offers', {})
            if not isinstance(response, dict) or set(response) != {'offers'} or not isinstance(response['offers'], list):
                raise ValueError('The enrolled producer returned invalid offers.')
            with self.lock:
                expected = self._members[receiver['certificate']][1]
            for offer in response['offers']:
                if (not isinstance(offer, dict) or set(offer) != {'id', 'revision', 'label', 'device_label', 'software_label', 'available', 'reason'}
                        or not isinstance(offer['id'], str) or not _HEX32.fullmatch(offer['id'])
                        or type(offer['revision']) is not int or offer['revision'] != 1
                        or offer['available'] is not True or offer['reason'] != ''
                        or any(not _text(offer[name]) for name in ('label', 'device_label', 'software_label'))):
                    raise ValueError('The enrolled producer returned an invalid offer.')
                mapping = {'receiver': target, 'address': expected, 'metadata': copy.deepcopy(offer), 'intended': own}
                with self.lock:
                    previous = self._offers.get(offer['id'])
                if offer['id'] in mappings or (previous is not None and previous != mapping):
                    raise Conflict('A software offer identity has conflicting device evidence.')
                mappings[offer['id']] = mapping
                offers.append(copy.deepcopy(offer))
        self._current()
        with self.lock:
            self._offers.update(mappings)
        return offers

    def inspect(self, ident, revision):
        if not isinstance(ident, str) or not _HEX32.fullmatch(ident) or type(revision) is not int or revision != 1:
            raise ValueError('Select the exact current software offer.')
        with self.lock:
            mapping = copy.deepcopy(self._offers.get(ident))
            current = self._members.get(mapping['receiver']['certificate']) if mapping else None
        if not mapping or not current or current[1] != mapping['address']:
            raise PermissionError('This offer is not from a current node-authorized compute service.')
        response = self.request(mapping['receiver'], 'producer-inspect', {'offer_id': ident, 'revision': revision})
        offer = response.get('offer')
        if (set(response) != {'offer'} or not isinstance(offer, dict) or set(offer) != _OFFER
                or offer['id'] != ident or type(offer['revision']) is not int or offer['revision'] != revision
                or offer['state'] != 'offered' or offer['receiver'] != mapping['intended']
                or not isinstance(offer['context'], dict) or set(offer['context']) != {'pool', 'runtime', 'daemon_instance', 'device_certificate'}
                or offer['context']['pool'] != self.anchor['pool']
                or offer['context']['device_certificate'] != mapping['receiver']['certificate']
                or any(not isinstance(offer['context'][name], str) or not _HEX64.fullmatch(offer['context'][name]) for name in ('runtime', 'daemon_instance'))
                or not isinstance(offer['implementation'], str) or not _HEX64.fullmatch(offer['implementation'])
                or offer['label'] != mapping['metadata']['label'] or offer['device_label'] != mapping['metadata']['device_label']):
            raise PermissionError('The software offer belongs to another process, device or receiver.')
        _process(offer['process'])
        return copy.deepcopy(offer)

    def _intent(self, intent, action):
        offer = intent['offer']
        target = {'certificate': offer['context']['device_certificate'], 'pool': offer['context']['pool']}
        body = {name: intent[name] for name in ('source', 'command_id')}
        if (not isinstance(body['source'], str) or not _HEX32.fullmatch(body['source'])
                or not isinstance(body['command_id'], str) or not _HEX64.fullmatch(body['command_id'])
                or not isinstance(offer['id'], str) or not _HEX32.fullmatch(offer['id'])
                or type(offer['revision']) is not int or offer['revision'] != 1):
            raise ValueError('The persisted software connection command is invalid.')
        if action == 'producer-connect':
            body.update(offer_id=offer['id'], revision=offer['revision'])
        return self.request(target, action, body)

    def prepare(self, intent):
        return self._intent(intent, 'producer-connect')

    def activate(self, intent):
        return self._intent(intent, 'producer-activate')

    def status(self, intent):
        return self._intent(intent, 'producer-status')

    def revoke(self, intent):
        return self._intent(intent, 'producer-revoke')

    def control(self):
        self._current()
        value = self._control()
        if (not isinstance(value, dict) or set(value) != {'certificate', 'control_path', 'daemon', 'pool', 'runtime'}
                or value['certificate'] != self.identity.beacon or value['pool'] != self.anchor['pool']):
            raise PermissionError('The existing daemon control identity changed.')
        daemon = value['daemon']
        location = value['control_path']
        if (not isinstance(daemon, dict) or set(daemon) != {'pid', 'started', 'instance'}
                or not isinstance(daemon['instance'], str) or not _HEX64.fullmatch(daemon['instance'])
                or not isinstance(value['runtime'], str) or not _HEX64.fullmatch(value['runtime'])
                or not isinstance(location, str) or not location.startswith('/')
                or '..' in location.split('/') or '\0' in location or len(location.encode()) > 103):
            raise PermissionError('The existing daemon control descriptor is invalid.')
        _process({name: daemon[name] for name in ('pid', 'started')})
        self._current()
        with self.lock:
            if self._control_anchor is None:
                self._control_anchor = copy.deepcopy(value)
            if value != self._control_anchor:
                raise PermissionError('The original daemon control instance changed.')
        return copy.deepcopy(value)
