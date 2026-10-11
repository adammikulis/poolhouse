"""Where the node's device key is kept, and how it reaches the node: `poolhouse node key`.

On a device with an operating-system keystore the key is wrapped under a key of that keystore (`poolhouse.keystore`, purpose
``device-key``, owned by the state directory) and kept as ``device.key.wrapped``, which opens only on this computer and for
this directory: a copy of the state directory is a stranger. Without a keystore it is kept as ``device.key.enc`` under the
passphrase in ``POOLHOUSE_DEVICE_KEY_PASSPHRASE``, or, when there is neither, in the plain file ``device.key`` that the node writes and
`pool_status` calls weak. ``device.custody.json`` says which, and the fingerprint the key must have.

The supervisor asks `release` for the key and gives it to the node on its standard input; the node never writes it. A key that
is in a file moves into the keystore only when a person runs `poolhouse node key --migrate`.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poolhouse import keystore
from poolhouse.files import read_json, write_json, writing
from poolhouse.fleet.onboard import signing
from poolhouse.platform import private_file

__all__ = ["FILE", "KEYSTORE", "PASSPHRASE", "PASSPHRASE_ENV", "KeyUnavailable", "Released", "fingerprint", "migrate", "release", "status"]

logger = logging.getLogger(__name__)

PURPOSE = "device-key"
PLAIN, WRAPPED, ENCRYPTED, RECORD = "device.key", "device.key.wrapped", "device.key.enc", "device.custody.json"
PASSPHRASE_ENV = "POOLHOUSE_DEVICE_KEY_PASSPHRASE"  # noqa: S105 - the name of a variable, not a secret
KEYSTORE, PASSPHRASE, FILE = "keystore", "passphrase", "file"


class KeyUnavailable(OSError):
    """The key of the device cannot be had now, and the node is not started without it; the message says why in plain words."""


@dataclass(frozen=True)
class Released:
    """The key a node is started with and where it is kept."""

    seed: bytes
    custody: str

    def argv(self) -> list[str]:
        """The arguments that tell the node to read its key from standard input."""
        return ["--key-stdin", "--key-custody", self.custody]

    def line(self) -> bytes:
        """What goes to the node's standard input."""
        return self.seed.hex().encode() + b"\n"


def fingerprint(seed: bytes) -> str:
    """The fingerprint of the device whose key is ``seed``: SHA-256 of its public key, as hex."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    public = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(public).hexdigest()


def _owner(state: Path) -> str:
    return "node:" + hashlib.sha256(str(state.resolve()).encode()).hexdigest()[:16]


def _record(state: Path) -> dict[str, Any]:
    held = read_json(state / RECORD, {})
    return held if isinstance(held, dict) else {}


def _private(path: Path, data: bytes) -> None:
    with writing(path) as tmp:
        tmp.write_bytes(data)
        private_file(tmp)


def _recorded(state: Path, custody: str, seed: bytes) -> None:
    write_json(state / RECORD, {"custody": custody, "fingerprint": fingerprint(seed)})
    private_file(state / RECORD)


def _keep(state: Path, ks: keystore.Keystore, seed: bytes) -> None:
    """Wrap ``seed`` under the keystore for this directory, prove it opens, and record where it is."""
    blob = ks.wrap(PURPOSE, _owner(state), seed)
    if ks.unwrap(PURPOSE, _owner(state), blob) != seed:
        raise keystore.KeystoreError("the wrapped key did not open again")
    _private(state / WRAPPED, blob)
    _recorded(state, KEYSTORE, seed)


def _seal(state: Path, seed: bytes, passphrase: str) -> None:
    (state / ENCRYPTED).unlink(missing_ok=True)
    signing.seal_file(state / ENCRYPTED, seed, passphrase)
    _recorded(state, PASSPHRASE, seed)


def _unwrapped(state: Path, ks: keystore.Keystore | None) -> bytes:
    try:
        return (ks or keystore.default()).unwrap(PURPOSE, _owner(state), (state / WRAPPED).read_bytes())
    except FileNotFoundError:
        raise KeyUnavailable(f"the key of this device is kept in the keystore and {state / WRAPPED} is gone") from None
    except keystore.KeystoreError as why:
        raise KeyUnavailable(f"the key of this device is kept in this computer's keystore, which would not give it up: {why}") from why


def _opened(state: Path) -> bytes:
    passphrase = os.environ.get(PASSPHRASE_ENV, "")
    if not passphrase:
        raise KeyUnavailable(f"the key of this device is kept under a passphrase: set {PASSPHRASE_ENV} to start the node")
    try:
        return signing.open_file(state / ENCRYPTED, passphrase)
    except (signing.KeyStoreError, OSError) as why:
        raise KeyUnavailable(f"the key of this device would not open with the passphrase in {PASSPHRASE_ENV}: {why}") from why


def _born(state: Path, ks: keystore.Keystore | None) -> Released | None:
    """The key of a device that has none: in the keystore when one is ready and needs nothing of a person, else under the
    passphrase when one is given, else nothing (the node makes a key file and says it is weak)."""
    ks = ks or keystore.default()
    if ks.available() and ks.status()["provisioned"]:
        seed = os.urandom(32)
        try:
            _keep(state, ks, seed)
        except keystore.KeystoreError as why:
            logger.warning("the device key is not kept in the keystore: %s", why)
        else:
            return Released(seed, KEYSTORE)
    if passphrase := os.environ.get(PASSPHRASE_ENV, ""):
        seed = os.urandom(32)
        _seal(state, seed, passphrase)
        return Released(seed, PASSPHRASE)
    return None


def release(state: Path, *, ks: keystore.Keystore | None = None) -> Released | None:
    """The key the node of ``state`` is to be started with, or None when it keeps its own key file. `KeyUnavailable` when the key is
    kept in the keystore or under a passphrase and cannot be had now."""
    held = _record(state).get("custody")
    if held == KEYSTORE:
        return Released(_unwrapped(state, ks), KEYSTORE)
    if held == PASSPHRASE:
        return Released(_opened(state), PASSPHRASE)
    return None if (state / PLAIN).exists() else _born(state, ks)


def migrate(state: Path, *, ks: keystore.Keystore | None = None) -> dict[str, Any]:
    """Move the key in ``device.key`` into the keystore, or under the passphrase of ``POOLHOUSE_DEVICE_KEY_PASSPHRASE`` when
    this computer has no keystore; the key file is deleted once the new copy has opened again. What happened, as a dict."""
    plain = state / PLAIN
    if not plain.is_file():
        return {"moved": False, "custody": _record(state).get("custody", FILE), "why": "this device has no key file to move"}
    try:
        seed = bytes.fromhex(plain.read_text(encoding="utf-8").strip())
        fingerprint(seed)
    except ValueError:
        raise KeyUnavailable(f"{plain} does not hold a device key") from None
    ks = ks or keystore.default()
    try:
        if ks.available():
            _keep(state, ks, seed)
            custody = KEYSTORE
        elif passphrase := os.environ.get(PASSPHRASE_ENV, ""):
            _seal(state, seed, passphrase)
            custody = PASSPHRASE
        else:
            raise KeyUnavailable(f"this computer has no keystore; to keep the key under a passphrase set {PASSPHRASE_ENV} and run this again")
    except keystore.KeystoreError as why:
        raise KeyUnavailable(f"the keystore would not take the key, so the key file stays: {why}") from why
    plain.unlink()
    return {"moved": True, "custody": custody, "fingerprint": fingerprint(seed)}


def status(state: Path) -> dict[str, Any]:
    """Where the key of the device of ``state`` is kept, from the files alone."""
    held = _record(state)
    custody = str(held.get("custody") or (FILE if (state / PLAIN).exists() else "none"))
    return {"custody": custody, "weak": custody != KEYSTORE, "fingerprint": held.get("fingerprint", ""),
            "keystore": keystore.default().status()["provisioned"]}

