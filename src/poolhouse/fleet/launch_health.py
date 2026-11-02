"""Authenticate an existing daemon without creating or repairing installation state."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
from pathlib import Path

from poolhouse.files import stat_signature
from poolhouse.http import Options, ServerError, open_stream

from . import app_identity

MAX_BYTES = 1024 * 1024
TIMEOUT_S = 5.0


def valid(value: object) -> bool:
    """The desktop app's health schema; this alone is not authentication."""
    return (isinstance(value, dict) and value.get('ok') is True
            and isinstance(value.get('name'), str)
            and type(value.get('slots')) is int and 0 <= value['slots'] < 2**64
            and type(value.get('free')) is int and 0 <= value['free'] < 2**64
            and type(value.get('busy')) is bool
            and isinstance(value.get('proof'), str)
            and len(value['proof']) == 64
            and all(char in '0123456789abcdef' for char in value['proof']))


def _read_existing(root: Path) -> bytes:
    """Read the app handshake key without changing its supported directory or file."""
    path = Path(root).absolute()
    if not path.is_absolute() or '..' in path.parts:
        raise OSError('An installed absolute identity directory is required')
    descriptors, anchors = [], []
    try:
        at = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(at)
        for name in path.parts[1:]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=at)
            descriptors.append(child)
            anchors.append((at, name, child, stat_signature(os.fstat(child))[:4]))
            at = child
        directory = os.fstat(at)
        if directory.st_uid != os.geteuid() or stat.S_IMODE(directory.st_mode) & 0o022:
            raise OSError('App identity directory is writable or belongs to another account')
        fd = os.open(app_identity.PROOF_KEY_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=at)
        descriptors.append(fd)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) & 0o077):
            raise OSError('Installation identity is not a private regular file')
        data = os.read(fd, 257)
        text = data.decode('ascii').strip()
        if (len(data) > 256 or len(text) != 64
                or any(char not in '0123456789abcdefABCDEF' for char in text)
                or stat_signature(os.fstat(fd)) != stat_signature(before)
                or stat_signature(os.stat(app_identity.PROOF_KEY_FILE, dir_fd=at, follow_symlinks=False)) != stat_signature(before)):
            raise OSError('Installation identity changed or is malformed')
        for parent, name, child, before in anchors:
            if (stat_signature(os.stat(name, dir_fd=parent, follow_symlinks=False))[:4] != before
                    or stat_signature(os.fstat(child))[:4] != before):
                raise OSError('Installation identity directory changed')
        return bytes.fromhex(text)
    except (OSError, ValueError, UnicodeError) as exc:
        raise OSError('Existing installation identity is unavailable') from exc
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def authenticated(port: int, root: Path) -> dict | None:
    """Fresh challenge proof under the requested root's existing private secret."""
    endpoint = f'http://127.0.0.1:{port}/health'
    challenge = secrets.token_hex(16)

    def guard(url: str) -> str:
        if url != endpoint:
            raise ValueError('Health cannot leave its loopback endpoint')
        return url

    try:
        with open_stream(endpoint, options=Options(
                headers={app_identity.HEADER: challenge}, timeout=TIMEOUT_S,
                follow_redirects=False, guard=guard)) as response:
            if response.status != 200:
                return None
            body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            return None
        value = json.loads(body)
        # Read after the response, matching the desktop handshake on first use.
        key = _read_existing(root)
        expected = hmac.new(key, challenge.encode('ascii'), hashlib.sha256).hexdigest()
        if valid(value) and hmac.compare_digest(value['proof'], expected):
            return value
    except (ServerError, OSError, ValueError, UnicodeError):
        pass
    return None
