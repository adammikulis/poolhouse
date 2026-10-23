"""Credentials: one resolver for every token and key this library needs.

`get(name)` looks in order at the explicit argument, the environment variable ``NAME``, the
file named by ``NAME_FILE``, the credentials file, Hugging Face's own token file for
``HF_TOKEN``, and the values wrapped under the user's keystore master key (`poolhouse.keystore`). Values are never logged or
put in an exception; `describe` and `status` say where a credential came from, not what it is.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import os
import tomllib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from poolhouse import home, keystore, lock
from poolhouse.credentials.environment import child_environment
from poolhouse.credentials.reading import (
    INSECURE_ENV,
    CredentialError,
    clean,
    read_text,
    valid_name,
)
from poolhouse.credentials.writing import write
from poolhouse.files import read_json, reading, write_json
from poolhouse.json_objects import unique_object

__all__ = ["FILE_ENV", "INSECURE_ENV", "CredentialError", "Secret", "WrappedCredentialError", "child_environment",
           "describe", "file_path", "get", "set", "status", "unset"]

logger = logging.getLogger(__name__)

FILE_ENV = "POOLHOUSE_CREDENTIALS_FILE"
HF_NAMES = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")
KNOWN = ("HF_TOKEN", "ANTHROPIC_API_KEY")
PURPOSE = "credentials"
_WRAPPED_FAILURES = {
    "invalid_wrapped": "the stored credential is not a valid encrypted credential record",
    "cannot_open": "the stored credential cannot be opened with the current encryption key or credential context; its bytes may have changed",
    "missing_master": "the encryption key for the stored credential is missing from the OS keystore",
    "locked": "the OS keystore is not ready to open the stored credential",
    "denied": "the OS keystore refused access to the stored credential",
    "busy": "the OS keystore is busy; the stored credential has not been opened",
    "unavailable": "the OS keystore is unavailable to this installed holder",
}


class WrappedCredentialError(CredentialError):
    """A fixed, value-free reason an existing encrypted credential could not be opened."""

    def __init__(self, reason: str):
        if reason not in _WRAPPED_FAILURES:
            raise ValueError("unknown encrypted credential failure")
        self.reason = reason
        super().__init__(_WRAPPED_FAILURES[reason])


class Secret(str):
    """A credential. It is a ``str`` where it is used and says nothing where it is printed."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "Secret(<redacted>)"

    def __reduce__(self) -> Any:
        raise TypeError("a Secret is not pickled")


def file_path() -> Path:
    """Where the credentials file is: ``POOLHOUSE_CREDENTIALS_FILE``, else under the state root."""
    named = os.environ.get(FILE_ENV)
    return home.expand(named) if named else home.state("credentials.toml")


def hf_token_path() -> Path:
    """Where Hugging Face keeps the token ``huggingface-cli login`` saved."""
    named = os.environ.get("HF_TOKEN_PATH")
    if named:
        return home.expand(named)
    root = os.environ.get("HF_HOME")
    base = home.expand(root) if root else home.user_home() / ".cache" / "huggingface"
    return base / "token"


def _from_env(name: str) -> str | None:
    value = os.environ.get(name)
    return clean(value, f"environment variable {name}") if value and value.strip() else None


def _from_named_file(name: str) -> str | None:
    named = os.environ.get(f"{name}_FILE")
    if not named:
        return None
    return clean(read_text(home.expand(named), private=False, anchor=Path("/")),
                 f"the file {name}_FILE names")


def _entries(path: Path) -> dict[str, str]:
    """Every credential in the file at ``path``; empty when the file is not there."""
    if not path.exists() and not path.is_symlink():
        return {}
    text = read_text(path)
    try:
        table = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise CredentialError(f"{path} is not valid TOML") from None
    odd = sorted(key for key, value in table.items() if not isinstance(value, str))
    if odd:
        raise CredentialError(f"{path} holds entries that are not strings: {', '.join(odd)}")
    return {key: clean(value, f"{key} in {path}") for key, value in table.items()}


def _from_file(name: str) -> str | None:
    return _entries(file_path()).get(name)


def _from_hf_file(name: str) -> str | None:
    if name not in HF_NAMES:
        return None
    path = hf_token_path()
    if not path.exists():
        return None
    text = read_text(path, private=False, anchor=path.parent)
    return clean(text, "Hugging Face's token file")


def wrapped_path() -> Path:
    """The file holding the values stored with ``--keychain``, each wrapped under the keystore."""
    return home.state("keystore", "credentials.json")


def _wrapped(*, strict: bool = False) -> dict[str, str]:
    if strict:
        try:
            with reading(wrapped_path()) as source:
                record = json.load(source, object_pairs_hook=unique_object)
        except FileNotFoundError:
            if wrapped_path().is_symlink():
                raise WrappedCredentialError("invalid_wrapped") from None
            return {}
        except (OSError, ValueError):
            raise WrappedCredentialError("invalid_wrapped") from None
        if (not isinstance(record, dict) or type(record.get("schema_version")) is not int
                or record["schema_version"] != 1 or not isinstance(record.get("values"), dict)):
            raise WrappedCredentialError("invalid_wrapped")
        return record["values"]
    table = read_json(wrapped_path(), {}).get("values", {})
    return table if isinstance(table, dict) else {}


@contextlib.contextmanager
def _rewriting() -> Iterator[None]:
    """Hold the lock for a read-modify-write of the wrapped file, so two ``--keychain`` commands
    at once do not lose one value."""
    wrapped_path().parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with lock.rewriting(wrapped_path()):
            yield
    except lock.Busy as exc:
        raise CredentialError(f"another poolhouse command is changing {wrapped_path().name}; "
                              "try again in a moment") from exc
    except OSError as exc:
        raise CredentialError(str(exc)) from exc


def _keep(name: str, value: str) -> None:
    ks = keystore.default()
    blob = base64.b64encode(ks.wrap(PURPOSE, name, value.encode())).decode()
    with _rewriting():
        write_json(wrapped_path(), {"schema_version": 1, "values": {**_wrapped(), name: blob}})
        wrapped_path().chmod(0o600)


def _open(name: str, *, strict: bool = False) -> str | None:
    """Open the encrypted item; an admitted holder uses ``strict`` to distinguish absence from failure."""
    wrapped = _wrapped(strict=True) if strict else _wrapped()
    if name not in wrapped:
        return None
    held = wrapped[name]
    if not strict and held is None:
        return None
    try:
        if strict and not isinstance(held, str):
            raise ValueError("invalid encrypted credential record")
        return clean(keystore.default().unwrap(PURPOSE, name, base64.b64decode(held, validate=strict)).decode(),
                     "the keychain entry")
    except (keystore.KeystoreError, ValueError, CredentialError) as exc:
        if strict:
            reason = next((reason for kind, reason in (
                (keystore.KeystoreLocked, "locked"), (keystore.KeystoreDenied, "denied"),
                (keystore.KeystoreBusy, "busy"), (keystore.KeystoreMissing, "missing_master"),
                (keystore.KeystoreUnavailable, "unavailable"), (keystore.KeystoreError, "cannot_open"),
            ) if isinstance(exc, kind)), "invalid_wrapped")
            raise WrappedCredentialError(reason) from None
        if isinstance(exc, CredentialError):
            raise
        logger.debug("keystore unavailable: %s", type(exc).__name__)
        return None


SOURCES: tuple[tuple[str, Callable[[str], str | None]], ...] = (
    ("environment", _from_env),
    ("environment file", _from_named_file),
    ("credentials file", _from_file),
    ("huggingface token file", _from_hf_file),
    ("keychain", _open),
)


def _lookup(name: str, *, read: bool = True) -> tuple[str, str] | None:
    names = HF_NAMES if name in HF_NAMES else (name,)
    for source, reader in SOURCES:
        for alias in names:
            found = reader(alias) if read or source != "keychain" else ("-" if alias in _wrapped() else None)
            if found:
                return source, found
    return None


def get(name: str, explicit: str | None = None, *, required: bool = False) -> Secret | None:
    """The credential ``name``: ``explicit`` if given, else the first source that holds it.

    None when nothing does, or `CredentialError` when ``required``. A source that exists but
    cannot be trusted (a file other users can read) raises rather than being skipped.
    """
    valid_name(name)
    if explicit is not None and explicit.strip():
        return Secret(clean(explicit, "the explicit value"))
    found = _lookup(name)
    if found is not None:
        return Secret(found[1])
    if required:
        raise CredentialError(
            f"no credential {name}: set {name} or {name}_FILE, or run: poolhouse credentials "
            f"set {name}")
    return None


def status(name: str) -> dict[str, Any]:
    """``{"present": bool, "source": str | None}`` for ``name``, and ``"error"`` when a source
    exists but cannot be used. Never the value."""
    valid_name(name)
    try:
        found = _lookup(name, read=False)
    except CredentialError as exc:
        return {"present": False, "source": None, "error": str(exc)}
    return {"present": found is not None, "source": found[0] if found else None}


def describe() -> list[dict[str, Any]]:
    """One row per credential this machine knows of: its name, where it comes from and
    whether that source can be used. Never a value."""
    names = list(KNOWN)
    with contextlib.suppress(CredentialError):
        names += sorted({*_entries(file_path())} - {*names})
    return [{"name": name, **status(name)} for name in names]


def set(name: str, value: str, *, keychain: bool = False) -> str:
    """Store ``value`` as ``name`` in the credentials file (or the OS keychain); returns where."""
    valid_name(name)
    value = clean(value, "the value")
    if keychain:
        try:
            _keep(name, value)
        except keystore.KeystoreError as exc:
            raise CredentialError(str(exc)) from exc
        return "keychain"
    path = file_path()
    entries = _entries(path)
    entries[name] = value
    write(path, entries)
    return str(path)


def unset(name: str, *, keychain: bool = False) -> bool:
    """Remove ``name`` from the credentials file (or the keychain). Whether it was there."""
    valid_name(name)
    if keychain:
        with _rewriting():
            table = _wrapped()
            if name not in table:
                return False
            del table[name]
            write_json(wrapped_path(), {"schema_version": 1, "values": table})
        return True
    path = file_path()
    entries = _entries(path)
    if name not in entries:
        return False
    del entries[name]
    write(path, entries)
    return True
