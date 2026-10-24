"""Authenticate an existing daemon without creating or repairing installation state."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from pathlib import Path

from poolhouse.http import Options, ServerError, open_stream

from . import app_identity, producer_bootstrap

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
        key = producer_bootstrap.read_existing(root)
        expected = hmac.new(key, challenge.encode('ascii'), hashlib.sha256).hexdigest()
        if valid(value) and hmac.compare_digest(value['proof'], expected):
            return value
    except (ServerError, OSError, ValueError, UnicodeError):
        pass
    return None
