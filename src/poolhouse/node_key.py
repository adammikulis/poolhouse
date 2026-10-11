"""Where the node's device key is kept, and how it reaches the node: `poolhouse node key`.

On a device with an operating-system keystore the key is wrapped under a key of that keystore (`poolhouse.keystore`, purpose
``device-key``, owned by the state directory) and kept as ``device.key.wrapped``, which opens only on this computer and for
this directory: a copy of the state directory is a stranger. Without a keystore it is kept as ``device.key.enc`` under the
passphrase in ``POOLHOUSE_DEVICE_KEY_PASSPHRASE``, or, when there is neither, in the plain file ``device.key`` that the node writes and
`pool_status` calls weak. ``device.custody.json`` says which, and the fingerprint the key must have. Once the key is kept elsewhere
``device.key`` holds a note that is not a key, so a node that does not know of the keystore stops on it instead of making another identity.

The supervisor asks `release` for the key and gives it to the node on its standard input; the node never writes it. A key that
is in a file moves into the keystore only when a person runs `poolhouse node key --migrate`. A device never gets another key by
losing its record: a state directory that holds the files of a device and no record of its key is refused.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from poolhouse import keystore
from poolhouse.files import write_json, writing
from poolhouse.fleet.onboard import signing
from poolhouse.platform import private_file

__all__ = ["FILE", "KEYSTORE", "NOTE", "PASSPHRASE", "PASSPHRASE_ENV", "KeyUnavailable", "Released", "fingerprint", "migrate", "release", "status"]

logger = logging.getLogger(__name__)

PURPOSE = "device-key"
PLAIN, WRAPPED, ENCRYPTED, RECORD = "device.key", "device.key.wrapped", "device.key.enc", "device.custody.json"
IDENTITY = (WRAPPED, ENCRYPTED, "device.cert")
"""The files that say a state directory is a device's: a new key is made only where none of them is."""
PASSPHRASE_ENV = "POOLHOUSE_DEVICE_KEY_PASSPHRASE"  # noqa: S105 - the name of a variable, not a secret
KEYSTORE, PASSPHRASE, FILE = "keystore", "passphrase", "file"
NOTE = "kept elsewhere: see device.custody.json; this file is not a key\n"
"""What ``device.key`` holds once the key is kept elsewhere (the node writes the same words)."""


class KeyUnavailable(OSError):
    """The key of the device cannot be had now, and the node is not started without it; the message says why in plain words.
    ``denied`` is True when the person or the system said no to the keystore, which is not asked again and again."""

    def __init__(self, message: str, *, denied: bool = False) -> None:
        super().__init__(message)
        self.denied = denied


@dataclass(frozen=True)
class Released:
    """The key a node is started with and where it is kept."""

    seed: bytes = field(repr=False)
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
    """What the state directory recorded of its key; empty when it recorded nothing, `KeyUnavailable` when the record cannot be read."""
    try:
        held = json.loads((state / RECORD).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise KeyUnavailable(f"{state / RECORD} cannot be read, so where the key of this device is kept is not known; a person starts over by moving "
                             "this state directory aside, and the device then joins the pool again as a new one") from None
    return held if isinstance(held, dict) else {}


def _file(state: Path) -> str:
    """What ``device.key`` is: ``key`` (hex of a key), ``note`` (this module's note), ``other`` or ``none``."""
    try:
        text = (state / PLAIN).read_text(encoding="utf-8")
    except FileNotFoundError:
        return "none"
    except (OSError, ValueError):
        return "other"
    if text.startswith("kept "):
        return "note"
    try:
        return "key" if len(bytes.fromhex(text.strip())) == 32 else "other"
    except ValueError:
        return "other"


def _private(path: Path, data: bytes) -> None:
    with writing(path) as tmp:
        tmp.write_bytes(data)
        private_file(tmp)


def _recorded(state: Path, custody: str, seed: bytes) -> None:
    write_json(state / RECORD, {"custody": custody, "fingerprint": fingerprint(seed)})
    private_file(state / RECORD)
    _private(state / PLAIN, NOTE.encode())


def _keep(state: Path, ks: keystore.Keystore, seed: bytes) -> None:
    """Wrap ``seed`` under the keystore for this directory, prove it opens, and record where it is."""
    blob = ks.wrap(PURPOSE, _owner(state), seed)
    if ks.unwrap(PURPOSE, _owner(state), blob) != seed:
        raise keystore.KeystoreError("the wrapped key did not open again")
    _private(state / WRAPPED, blob)
    _recorded(state, KEYSTORE, seed)


def _seal(state: Path, seed: bytes, passphrase: str) -> None:
    signing.seal_file(state / ENCRYPTED, seed, passphrase)
    _recorded(state, PASSPHRASE, seed)


def _unwrapped(state: Path, ks: keystore.Keystore) -> bytes:
    try:
        return ks.unwrap(PURPOSE, _owner(state), (state / WRAPPED).read_bytes())
    except FileNotFoundError:
        raise KeyUnavailable(f"the key of this device is kept in the keystore and {state / WRAPPED} is gone; a person starts over by moving this "
                             "state directory aside, and the device then joins the pool again as a new one") from None
    except keystore.KeystoreError as why:
        raise KeyUnavailable(f"the key of this device would not open: this computer's keystore did not give it ({why}); a copy of a state "
                             "directory, or one that was moved, is not the device it was made for",
                             denied=isinstance(why, keystore.KeystoreDenied)) from why


def _opened(state: Path) -> bytes:
    passphrase = os.environ.get(PASSPHRASE_ENV, "")
    if not passphrase:
        raise KeyUnavailable(f"the key of this device is kept under a passphrase: set {PASSPHRASE_ENV} to start the node")
    try:
        return signing.open_file(state / ENCRYPTED, passphrase)
    except (signing.KeyStoreError, OSError) as why:
        raise KeyUnavailable(f"the key of this device would not open with the passphrase in {PASSPHRASE_ENV}: {why}") from why


def _born(state: Path, ks: keystore.Keystore) -> Released | None:
    """The key of a device that has none: in the keystore when one is ready (its master key exists, or a person at a screen would
    be asked the one question that makes it), else under the passphrase when one is given, else nothing (the node makes a key file
    and says it is weak)."""
    ready = ks.status()
    if ks.available() and (ready["provisioned"] or ready["interactive"]):
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
    kept in the keystore or under a passphrase and cannot be had now, and when the directory is a device's and says nothing of its key."""
    ks = ks or keystore.default()
    try:
        held = _record(state).get("custody")
        if held == KEYSTORE:
            return Released(_unwrapped(state, ks), KEYSTORE)
        if held == PASSPHRASE:
            return Released(_opened(state), PASSPHRASE)
        kind = _file(state)
        if kind == "key":
            return None
        if kind == "note" or (kind == "none" and any((state / name).exists() for name in IDENTITY)):
            raise KeyUnavailable("this state directory is a device's and holds no record of where its key is kept, so no key is made for it; a person "
                                 "starts over by moving it aside, and the device then joins the pool again as a new one")
        return _born(state, ks) if kind == "none" else None
    finally:
        ks.lock()


def _leftover_is(state: Path, ks: keystore.Keystore, seed: bytes) -> bool:
    """Whether a wrapped key a move that did not finish left holds ``seed``."""
    try:
        return _unwrapped(state, ks) == seed
    except KeyUnavailable:
        return False


def migrate(state: Path, *, ks: keystore.Keystore | None = None) -> dict[str, Any]:
    """Move the key in ``device.key`` into the keystore, or under the passphrase of ``POOLHOUSE_DEVICE_KEY_PASSPHRASE`` when
    this computer has no keystore; the key file becomes a note once the new copy has opened again. What happened, as a dict."""
    plain = state / PLAIN
    kind = _file(state)
    if kind == "other":
        raise KeyUnavailable(f"{plain} does not hold a device key")
    if kind != "key":
        return {"moved": False, "custody": _record(state).get("custody", FILE), "why": "this device has no key file to move"}
    try:
        seed = bytes.fromhex(plain.read_text(encoding="utf-8").strip())
        fingerprint(seed)
    except ValueError:
        raise KeyUnavailable(f"{plain} does not hold a device key") from None
    ks = ks or keystore.default()
    try:
        if ks.available():
            if (state / WRAPPED).exists() and not _leftover_is(state, ks, seed):
                raise KeyUnavailable(f"{state / WRAPPED} is here and is not this device's key, so nothing is moved; a person moves it aside")
            _keep(state, ks, seed)
            custody = KEYSTORE
        elif passphrase := os.environ.get(PASSPHRASE_ENV, ""):
            if (state / ENCRYPTED).exists():
                raise KeyUnavailable(f"{state / ENCRYPTED} is here already, so nothing is moved; a person moves it aside")
            _seal(state, seed, passphrase)
            custody = PASSPHRASE
        else:
            raise KeyUnavailable(f"this computer has no keystore; to keep the key under a passphrase set {PASSPHRASE_ENV} and run this again")
    except keystore.KeystoreError as why:
        raise KeyUnavailable(f"the keystore would not take the key, so the key file stays: {why}") from why
    finally:
        ks.lock()
    return {"moved": True, "custody": custody, "fingerprint": fingerprint(seed)}


def status(state: Path) -> dict[str, Any]:
    """Where the key of the device of ``state`` is kept, from the files alone."""
    try:
        held = _record(state)
    except KeyUnavailable:
        held = {"custody": "unknown"}
    custody = str(held.get("custody") or {"key": FILE, "note": "unknown", "other": "unknown"}.get(_file(state), "none"))
    return {"custody": custody, "weak": custody != KEYSTORE, "fingerprint": held.get("fingerprint", ""),
            "keystore": keystore.default().status()["provisioned"]}
