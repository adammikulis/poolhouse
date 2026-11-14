"""HKDF-SHA256, on its own so a caller that only derives a key does not load the keystore."""

from __future__ import annotations

import hashlib
import hmac

__all__ = ["SALT", "hkdf"]

SALT = b"poolhouse/keystore/v1"


def hkdf(key: bytes, info: bytes, *, salt: bytes = SALT, length: int = 32) -> bytes:
    """HKDF-SHA256 (RFC 5869) of ``key``."""
    prk = hmac.new(salt or b"\0" * 32, key, hashlib.sha256).digest()
    out, block = b"", b""
    for counter in range(1, -(-length // 32) + 1):
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
    return out[:length]
