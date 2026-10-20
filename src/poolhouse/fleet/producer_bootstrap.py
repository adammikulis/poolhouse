"""Internal software-installation bootstrap, independent of source or owner authority.

Only the fixed installed holder supplies roots and current daemon metadata. The
SDK receives public bindings after both channel checks; it never receives the
installation secret. A copied software secret can forge this bootstrap proof.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import re
import stat
import time
from pathlib import Path

from poolhouse import unix_socket

from .app_identity import PROOF_KEY_FILE

DOMAIN = b'poolhouse/producer-bootstrap/v1\n'
LIFETIME = 30
CLOCK_SKEW = 5
_HEX = re.compile('[0-9a-f]{64}')
_CURRENT = {'certificate', 'control_path', 'daemon', 'pool', 'runtime'}
_FIELDS = _CURRENT | {'version', 'nonce', 'issued', 'expires'}


class BootstrapUnavailable(OSError):
    """The existing installation or its live daemon cannot be authenticated."""


def _signature(row):
    return (row.st_dev, row.st_ino, row.st_mode, row.st_uid, row.st_nlink,
            row.st_size, row.st_mtime_ns, row.st_ctime_ns)


def read_existing(root):
    """Read an existing private installation secret without creating or repairing it."""
    path = Path(root)
    if not path.is_absolute() or '..' in path.parts:
        raise BootstrapUnavailable('An installed absolute identity directory is required')
    descriptors, anchors = [], []
    try:
        at = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(at)
        for name in path.parts[1:]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=at)
            descriptors.append(child)
            anchors.append((at, name, child, _signature(os.fstat(child))[:4]))
            at = child
        directory = os.fstat(at)
        if directory.st_uid != os.geteuid() or stat.S_IMODE(directory.st_mode) & 0o077:
            raise BootstrapUnavailable('Installation identity directory is not private')
        fd = os.open(PROOF_KEY_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=at)
        descriptors.append(fd)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) & 0o077):
            raise BootstrapUnavailable('Installation identity is not a private regular file')
        data = os.read(fd, 65)
        if (len(data) != 64 or not _HEX.fullmatch(data.decode('ascii'))
                or _signature(os.fstat(fd)) != _signature(before)
                or _signature(os.stat(PROOF_KEY_FILE, dir_fd=at, follow_symlinks=False)) != _signature(before)):
            raise BootstrapUnavailable('Installation identity changed or is malformed')
        for parent, name, child, before in anchors:
            if (_signature(os.stat(name, dir_fd=parent, follow_symlinks=False))[:4] != before
                    or _signature(os.fstat(child))[:4] != before):
                raise BootstrapUnavailable('Installation identity directory changed')
        return bytes.fromhex(data.decode('ascii'))
    except (OSError, ValueError, UnicodeError) as exc:
        raise BootstrapUnavailable('Existing installation identity is unavailable') from exc
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def _canonical(binding):
    return json.dumps(binding, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _binding(value, nonce):
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise BootstrapUnavailable('Invalid bootstrap binding')
    if type(value['version']) is not int or value['version'] != 1:
        raise BootstrapUnavailable('Invalid bootstrap version')
    for key in ('nonce', 'pool', 'runtime'):
        if not isinstance(value[key], str) or not _HEX.fullmatch(value[key]):
            raise BootstrapUnavailable('Invalid bootstrap identity')
    if value['nonce'] != nonce:
        raise BootstrapUnavailable('Bootstrap challenge does not match')
    daemon = value['daemon']
    if (not isinstance(daemon, dict) or set(daemon) != {'pid', 'started', 'instance'}
            or type(daemon['pid']) is not int or daemon['pid'] <= 0
            or type(daemon['started']) not in (int, float)
            or not 0 < daemon['started'] < float('inf')
            or not isinstance(daemon['instance'], str) or not _HEX.fullmatch(daemon['instance'])):
        raise BootstrapUnavailable('Invalid bootstrap process identity')
    location = value['control_path']
    if (not isinstance(location, str) or not location or len(location.encode()) > 103
            or '\0' in location or not Path(location).is_absolute() or '..' in Path(location).parts):
        raise BootstrapUnavailable('Invalid bootstrap control location')
    certificate = value['certificate']
    try:
        if not isinstance(certificate, str) or not 0 < len(certificate) <= 16384:
            raise ValueError('Invalid certificate size')
        der = base64.b64decode(certificate, validate=True)
        if not der or base64.b64encode(der).decode() != certificate:
            raise ValueError('Invalid certificate encoding')
    except (ValueError, TypeError) as exc:
        raise BootstrapUnavailable('Invalid bootstrap certificate') from exc
    issued, expires = value['issued'], value['expires']
    now = time.time()
    if (type(issued) is not int or type(expires) is not int
            or not 0 < expires - issued <= LIFETIME or issued > now + CLOCK_SKEW or expires < now):
        raise BootstrapUnavailable('Bootstrap proof has expired')
    return copy.deepcopy(value)


class BootstrapHolder:
    """Answer nonce-only requests from actual server-owned metadata, never request fields."""

    def __init__(self, root, *, current):
        self.root, self.current = root, current

    def answer(self, nonce):
        if not isinstance(nonce, str) or not _HEX.fullmatch(nonce):
            raise BootstrapUnavailable('Invalid bootstrap challenge')
        current = self.current()
        if not isinstance(current, dict) or set(current) != _CURRENT:
            raise BootstrapUnavailable('Current daemon identity is unavailable')
        issued = int(time.time())
        binding = _binding({**current, 'version': 1, 'nonce': nonce,
                            'issued': issued, 'expires': issued + LIFETIME}, nonce)
        mac = hmac.new(read_existing(self.root), DOMAIN + _canonical(binding), hashlib.sha256).hexdigest()
        return {'binding': binding, 'mac': mac}


def authenticate(root, nonce, reply, bootstrap_channel):
    """Authenticate all metadata and the bootstrap server before using the control location."""
    if not isinstance(reply, dict) or set(reply) != {'binding', 'mac'}:
        raise BootstrapUnavailable('Invalid bootstrap response')
    binding = _binding(reply['binding'], nonce)
    supplied = reply['mac']
    if not isinstance(supplied, str) or not _HEX.fullmatch(supplied):
        raise BootstrapUnavailable('Invalid bootstrap proof')
    expected = hmac.new(read_existing(root), DOMAIN + _canonical(binding), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        raise BootstrapUnavailable('Bootstrap proof does not match this installation')
    process = {key: binding['daemon'][key] for key in ('pid', 'started')}
    if not unix_socket.matches(bootstrap_channel, process):
        raise BootstrapUnavailable('Bootstrap server process does not match')
    return binding


def verify_control(binding, tls_channel):
    """Verify exact control DER and process; only then may the holder return public metadata."""
    binding = _binding(binding, binding.get('nonce') if isinstance(binding, dict) else None)
    process = {key: binding['daemon'][key] for key in ('pid', 'started')}
    try:
        if (not unix_socket.matches(tls_channel, process)
                or tls_channel.getpeercert(binary_form=True) != base64.b64decode(binding['certificate'])
                or not unix_socket.matches(tls_channel, process)):
            raise BootstrapUnavailable('Control server identity does not match')
    except (OSError, AttributeError, ValueError) as exc:
        raise BootstrapUnavailable('Control server identity is unavailable') from exc
    return binding
