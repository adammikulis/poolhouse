"""Files as the node stores them: sealed here, so the node never sees what a file says.

The key is the keystore's subkey for ``board-files``, cut with a salt kept beside the session's
tokens. The node keeps the sealed bytes under the hash of what it was given and hands them only to a
session that may read a message carrying the file; opening them needs this device's key.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from poolhouse import keystore

try:
    from cryptography.exceptions import InvalidTag
except ImportError:
    InvalidTag = ValueError  # type: ignore[assignment,misc]

__all__ = ["HANDLE", "Unavailable", "handle_of", "open_sealed", "seal"]

PURPOSE = "board-files"
HANDLE = 12
_MAGIC = b"PBF1"
_AAD = b"poolhouse/board-files/v1|"
FAILURES = (OSError, RuntimeError, ValueError, TypeError, KeyError, keystore.KeystoreError)


class Unavailable(RuntimeError):
    """The key cannot be had or the bytes do not open, so nothing is sealed or read."""


def handle_of(data: bytes) -> tuple[str, str]:
    """``(sha256 hex, handle)`` for ``data``: the handle is the first 12 hex characters."""
    digest = hashlib.sha256(data).hexdigest()
    return digest, digest[:HANDLE]


def _cipher(folder: Path) -> object:
    try:
        return keystore.aead(keystore.default().salted_subkey(PURPOSE, folder))
    except FAILURES as exc:
        raise Unavailable(f"the files key cannot be had ({type(exc).__name__}); nothing is read or written") from exc


def seal(folder: Path, data: bytes, digest: str) -> bytes:
    """``data`` encrypted under this device's files key, bound to its hash ``digest``. The nonce is cut from the hash,
    so the same bytes seal to the same bytes (the node keeps them once) and no two contents share a nonce."""
    nonce = bytes.fromhex(digest[:24])
    return _MAGIC + nonce + _cipher(folder).encrypt(nonce, data, _AAD + digest.encode())  # type: ignore[attr-defined]


def open_sealed(folder: Path, sealed: bytes, digest: str) -> bytes:
    """The bytes ``seal`` made from content with hash ``digest``; `Unavailable` when they are not those."""
    if not sealed.startswith(_MAGIC):
        raise Unavailable("not a sealed board file")
    try:
        data = bytes(_cipher(folder).decrypt(sealed[4:16], sealed[16:], _AAD + digest.encode()))  # type: ignore[attr-defined]
    except InvalidTag as exc:
        raise Unavailable("a board file failed its integrity check, or was sealed under another key") from exc
    if hashlib.sha256(data).hexdigest() != digest:
        raise Unavailable("a board file does not match its hash")
    return data
