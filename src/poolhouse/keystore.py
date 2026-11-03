"""The only module that talks to the operating system's keystore.

One item per OS user holds a random 32-byte master key. Each purpose (memory vault, fleet
signing key, wrapped credentials) gets a subkey: HKDF-SHA256 over the master with the purpose,
owner and context in ``info``; `wrap` and `unwrap` bind purpose and owner as AAD.

The machine's own keystore is used only by the installed software on the real home (`poolhouse.real_machine`), whatever the
environment says. Nothing touches it at import, construction or `status`. The master is read once per process. Every backend
call passes a gate: hourly ceiling, cross-process lock. Reads of an existing item and the changes have separate hourly
ceilings (a change the keystore refused is not counted; the unlock command is never turned away). A refusal is told in the
system's words and remembered by nothing, so the next request asks again. A background process never creates the master and
reads it only after ``poolhouse-security unlock``. Each backend call is a sentinel event (purpose, outcome), never a value.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import random
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NamedTuple

from poolhouse import (
    home,
    keystore_guard,
    person,
    platform,
    private_path,
    real_machine,
    sentinel,
)
from poolhouse.files import read_json, write_json
from poolhouse.lock import Busy, only_one
from poolhouse.log import warn
from poolhouse.sentinel import human
from poolhouse.sentinel.events import Event, Severity

__all__ = ["ENV_NONINTERACTIVE", "NOTICE", "RATE_WINDOW_S", "READ_CEILING",
           "UNLOCK_COMMAND", "WRITE_CEILING", "Keystore", "KeystoreBusy", "KeystoreDenied", "KeystoreError",
           "KeystoreLocked", "KeystoreMissing", "KeystoreUnavailable", "Wires", "aead", "default", "hkdf",
           "interactive", "is_real", "os_user", "scrypt_key"]

SERVICE = "poolhouse"
ENV_NONINTERACTIVE = "POOLHOUSE_NONINTERACTIVE"
REAL_BACKENDS = ("keyring.backends.",)
"""Every backend `keyring` ships talks to the machine's own keystore except the ones below: a new one is real until named safe."""
NOT_REAL_BACKENDS = ("keyring.backends.fail", "keyring.backends.null", "keyring.backends.chainer")
UNLOCK_COMMAND = "poolhouse-security unlock"
READ_CEILING = 600
WRITE_CEILING = 5
NEAR_CEILING = 0.8
RATE_WINDOW_S = 3600.0
FLIGHT_WAIT_S = 180.0
STATE_WAIT_S = 10.0
SCRYPT_N = 1 << 15
NOTICE = ("Poolhouse will ask your computer to store one encryption key so your memory and fleet "
          "identity stay private. You will see one Keychain prompt.")
_SALT = b"poolhouse/keystore/v1"
_MAGIC = b"MKW1"
_VERSION = 1

_JITTER = random.SystemRandom()
logger = logging.getLogger("poolhouse.keystore")
logger.addHandler(logging.NullHandler())


def is_real(ring: Any) -> bool:
    """Whether ``ring`` (or any backend a chainer holds) talks to the machine's own keystore."""
    parts = getattr(ring, "backends", None) or [ring]
    return any(type(part).__module__.startswith(REAL_BACKENDS) and not type(part).__module__.startswith(NOT_REAL_BACKENDS)
               for part in parts)


class KeystoreError(RuntimeError):
    """The keystore cannot be used, so whatever needed a key stays locked."""


class KeystoreDenied(KeystoreError):
    """The operating system refused, or the person declined the prompt."""


class KeystoreBusy(KeystoreError):
    """Too many keystore operations this hour, or another process holds the one prompt."""


class KeystoreLocked(KeystoreError):
    """A background process asked before a person ran the unlock command."""


class KeystoreMissing(KeystoreError):
    """The keystore holds no master key and the caller did not ask for one to be made."""


class KeystoreUnavailable(KeystoreError):
    """This machine has no usable keystore, or a library the keystore needs is missing."""


def os_user() -> str:
    """The OS account this process runs as: its uid and login name."""
    try:
        import pwd
        uid = os.getuid()
        try:
            return f"{uid}:{pwd.getpwuid(uid).pw_name}"
        except KeyError:
            return f"{uid}:"
    except ImportError:
        import getpass
        return "0:" + getpass.getuser()


def hkdf(key: bytes, info: bytes, *, salt: bytes = _SALT, length: int = 32) -> bytes:
    """HKDF-SHA256 (RFC 5869) of ``key``."""
    prk = hmac.new(salt or b"\0" * 32, key, hashlib.sha256).digest()
    out, block = b"", b""
    for counter in range(1, -(-length // 32) + 1):
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
    return out[:length]


def scrypt_key(passphrase: str, salt: bytes, n: int = SCRYPT_N) -> bytes:
    """A 32-byte key from a passphrase, for the explicit passphrase fallback."""
    try:
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except ImportError as exc:
        raise KeystoreUnavailable("this needs `pip install cryptography`") from exc
    return Scrypt(salt=salt, length=32, n=n, r=8, p=1).derive(passphrase.encode())


def _desktop() -> bool:
    if sys.platform == "darwin":
        return not os.environ.get("SSH_CONNECTION") and os.environ.get("XPC_SERVICE_NAME", "0") in ("0", "")
    if sys.platform == "win32":
        return not keystore_guard.service_session()
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def interactive() -> bool:
    """Whether a person could answer an OS prompt: a terminal or a desktop session, and no agent
    marker (``POOLHOUSE_NONINTERACTIVE``, ``POOLHOUSE_AGENT``, ``CLAUDECODE``) set."""
    if person.marked():
        return False
    try:
        if sys.stdin.isatty():
            return True
    except (AttributeError, ValueError, OSError):
        pass
    return _desktop()


def _fields(*parts: bytes) -> bytes:
    """``parts`` joined so that no two different tuples give the same bytes."""
    return b"".join(len(part).to_bytes(4, "big") + part for part in parts)


def _aad(purpose: str, owner: str) -> bytes:
    return _fields(b"poolhouse/keystore/v1", purpose.encode(), owner.encode())


class Wires(NamedTuple):
    """What a `Keystore` calls out to: ``clock``, ``say`` for the first-use sentence, ``bus``
    for audit events (None: the sentinel's), ``interactive`` (None: the module's), ``sleep`` for
    the backoff near the read ceiling."""

    clock: Callable[[], float] = time.time
    say: Callable[[str], None] = warn
    bus: Any = None
    interactive: Callable[[], bool] | None = None
    sleep: Callable[[float], None] = time.sleep


class Keystore:
    """The master key of one OS user, and the subkeys cut from it."""

    def __init__(self, *, user: str | None = None, directory: Path | None = None,
                 ceiling: int = READ_CEILING, write_ceiling: int = WRITE_CEILING,
                 wires: Wires | None = None) -> None:
        self.user = user or os_user()
        self.account = "master/" + self.user
        self._fixed, self._ceiling, self._write_ceiling = directory, ceiling, write_ceiling
        self._clock, self._say, self._bus, self._interactive, self._sleep = wires or Wires()
        self._key: bytearray | None = None
        self._noticed = False
        self.created = False
        self._told: set[tuple[str, str]] = set()
        self._mutex = threading.RLock()

    # -- state files; none is read or made until something needs it --
    @property
    def directory(self) -> Path:
        return self._fixed or home.state("keystore")

    def _path(self, name: str) -> Path:
        return self.directory / name

    def _ensure_dir(self) -> None:
        if private_path.windows_mount(self.directory):
            raise KeystoreUnavailable(
                f"{self.directory} is on a Windows drive mounted into WSL, where file locks and "
                "permissions do not work. Move POOLHOUSE_HOME onto the Linux filesystem (under your "
                "home directory) and run the command again.")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not platform.private_dir(self.directory):
            logger.warning("could not restrict %s to your account with icacls; it keeps the "
                           "permissions it inherited", self.directory)
        human.protect(self.directory)

    def _doc(self, name: str) -> dict[str, Any]:
        doc = read_json(self._path(name), {})
        return doc if isinstance(doc, dict) else {}

    def _put_doc(self, name: str, doc: dict[str, Any]) -> None:
        self._ensure_dir()
        write_json(self._path(name), {"schema_version": _VERSION, **doc})
        self._path(name).chmod(0o600)

    @contextmanager
    def _held(self, name: str, wait_s: float) -> Iterator[None]:
        self._ensure_dir()
        try:
            with only_one(self._path(name), timeout=wait_s, announce=lambda _m: None):
                yield
        except Busy as exc:
            raise KeystoreBusy("another poolhouse process has a keystore prompt open; answer that one and this "
                               "request asks again when it is done") from exc

    def _is_interactive(self) -> bool:
        return (self._interactive or interactive)()

    # -- what is known without touching the keystore --
    def status(self) -> dict[str, Any]:
        """Provisioning and rate state from the state files; never a backend call."""
        reads, writes = self._window(self._clock())
        return {"account": self.account, "provisioned": bool(self._doc("provisioned.json").get("at")),
                "cached": self._key is not None, "reads_this_hour": len(reads),
                "writes_this_hour": len(writes), "read_ceiling": self._ceiling,
                "write_ceiling": self._write_ceiling, "interactive": self._is_interactive()}

    def _window(self, now: float) -> tuple[list[float], list[float]]:
        """The read and write times inside the last hour; an older file's single list counts as reads."""
        doc = self._doc("rate.json")
        pick = [float(t) for t in doc.get("reads", doc.get("ops", [])) if now - float(t) < RATE_WINDOW_S]
        writes = [float(t) for t in doc.get("writes", []) if now - float(t) < RATE_WINDOW_S]
        return pick, writes

    def pending_notice(self) -> str:
        """The first-use sentence when a prompt may still come and the person has not been
        told, else an empty string. For a chat or tool reply."""
        return "" if self._noticed or self._path("noticed.json").exists() else NOTICE

    def available(self) -> bool:
        """Whether this machine has a usable keystore backend (no item is touched)."""
        try:
            self._ring()
        except KeystoreUnavailable:
            return False
        return True

    def backend(self) -> str:
        """The keyring backend this machine would use, by module and class, or why there is none
        (backends are chosen by import and priority; no item is read or made)."""
        try:
            ring = self._ring().get_keyring()
        except KeystoreUnavailable as exc:
            return f"none: {exc}"
        return f"{type(ring).__module__}.{type(ring).__name__}"

    def _ring(self) -> Any:
        try:
            import keyring
            import keyring.errors
        except ImportError as exc:
            raise KeystoreUnavailable("the keystore needs `pip install keyring cryptography`") from exc
        try:
            usable = float(getattr(keyring.get_keyring(), "priority", 0)) > 0
        except (keyring.errors.KeyringError, AttributeError, TypeError, ValueError, OSError):
            usable = False
        why = keystore_guard.unsafe_backend(keyring.get_keyring()) if usable else ""
        if why:
            raise KeystoreUnavailable(f"Poolhouse will not keep its key in this keyring backend: {why}")
        if usable and is_real(keyring.get_keyring()) and (barred := real_machine.refusal()):
            raise KeystoreUnavailable(f"this computer's own keystore is not used here: {barred}")
        if not usable:
            raise KeystoreUnavailable("this machine has no usable OS keystore (macOS Keychain, "
                                      "Linux Secret Service)")
        return keyring

    # -- the gate every backend call passes --
    def _event(self, kind: str, purpose: str, outcome: str,
               severity: Severity = Severity.INFO) -> None:
        if kind == "refused":
            if (purpose, outcome) in self._told:
                return
            self._told.add((purpose, outcome))
        try:
            bus = self._bus or sentinel.default().bus
            bus.emit(Event(f"keystore.{kind}", severity, "keystore", f"purpose:{purpose}",
                           {"outcome": outcome}, self._clock()))
        except (OSError, ValueError, RuntimeError) as exc:
            logger.debug("keystore event not recorded: %s", type(exc).__name__)

    def _gate(self, purpose: str, *, person: bool = False) -> None:
        if person or self._is_interactive():
            return
        if self._doc("provisioned.json").get("at"):
            return
        self._event("refused", purpose, "noninteractive", Severity.NOTICE)
        raise KeystoreLocked("Poolhouse has no encryption key for this background process. A person "
                             f"runs `{UNLOCK_COMMAND}` once in a terminal to create it.")

    def _spend(self, kind: str, purpose: str, *, person: bool = False) -> None:
        """Count one read, or check the count of changes (`_wrote` counts a change once the keystore made it), and record the
        process that asked. A person's own read (the unlock command) is counted and never turned away."""
        write = kind != "read"
        with self._held("state.lock", STATE_WAIT_S):
            now = self._clock()
            reads, writes = self._window(now)
            used, ceiling = (writes, self._write_ceiling) if write else (reads, self._ceiling)
            if len(used) >= ceiling and (write or not person):
                self._event("refused", purpose, "busy", Severity.WARNING)
                raise KeystoreBusy(_over(ceiling, write))
            if not write:
                used.append(now)
                self._put_doc("rate.json", {"reads": reads, "writes": writes,
                                            "writer_pid": os.getpid(), "writer_at": now})
            near = len(used) / ceiling >= NEAR_CEILING and not write
        if near:
            self._sleep(_JITTER.uniform(0.05, 0.25))

    def _wrote(self) -> None:
        """Count one change the keystore made or found already made; a change it refused changed nothing and is not counted."""
        with self._held("state.lock", STATE_WAIT_S):
            now = self._clock()
            reads, writes = self._window(now)
            writes.append(now)
            self._put_doc("rate.json", {"reads": reads, "writes": writes, "writer_pid": os.getpid(), "writer_at": now})

    def _tell(self) -> None:
        if self._noticed:
            return
        self._noticed = True
        if not self._path("noticed.json").exists():
            self._say(NOTICE)
            self._put_doc("noticed.json", {"at": self._clock()})

    def _call(self, kind: str, purpose: str, work: Callable[[Any], Any], *, person: bool = False) -> Any:
        """One backend call, gated, counted, audited. A refusal raises with the system's words and is remembered by nothing."""
        ring = self._ring()
        self._spend(kind, purpose, person=person)
        self._tell()
        person_here = self._is_interactive()
        if not person_here:
            keystore_guard.forbid_prompts()
        try:
            out = keystore_guard.bounded(lambda: work(ring), keystore_guard.PERSON_WAIT_S if person_here
                                         else keystore_guard.BACKGROUND_WAIT_S)
        except ring.errors.PasswordDeleteError:
            self._event(kind, purpose, "absent")
            self._wrote()
            return None
        except (ring.errors.KeyringError, OSError, TimeoutError) as exc:
            self._event("denied", purpose, type(exc).__name__, Severity.WARNING)
            raise KeystoreDenied(_refused(exc)) from exc
        self._event(kind, purpose, "absent" if kind == "read" and out is None else "ok")
        if kind != "read":
            self._wrote()
        return out

    @contextmanager
    def _flight(self, purpose: str, *, person: bool = False) -> Iterator[None]:
        self._ring()
        with self._mutex:
            self._gate(purpose, person=person)
            with self._held("flight.lock", FLIGHT_WAIT_S):
                self._gate(purpose, person=person)
                yield

    # -- the master key --
    def _master(self, purpose: str, *, create: bool, person: bool = False) -> bytes:
        with self._mutex:
            if self._key is not None:
                return bytes(self._key)
        with self._flight(purpose, person=person):
            if self._key is not None:
                return bytes(self._key)
            found = self._call("read", purpose, lambda r: r.get_password(SERVICE, self.account), person=person)
            key = _parse(found)
            if key is None:
                if not create:
                    raise KeystoreMissing("the OS keystore holds no poolhouse key")
                if not (person or self._is_interactive()):
                    raise KeystoreLocked("Poolhouse has no encryption key for this background process. "
                                         f"A person runs `{UNLOCK_COMMAND}` once in a terminal.")
                if not person and self._doc("provisioned.json").get("at"):
                    raise KeystoreMissing("the OS keystore no longer holds the key poolhouse made here, so "
                                          "everything wrapped under it stays locked; poolhouse will not make a "
                                          f"second one by itself. Restore the item, or `{UNLOCK_COMMAND}` "
                                          "to start over.")
                key = os.urandom(32)
                stored = "v1:" + base64.b64encode(key).decode()
                self._call("create", purpose, lambda r: r.set_password(SERVICE, self.account, stored))
                self.created = True
            self._key = bytearray(key)
            if not self._doc("provisioned.json").get("at"):
                self._put_doc("provisioned.json", {"at": self._clock(), "account": self.account})
            return key


    def provision(self) -> bool:
        """Make the master key if there is none (for the unlock command, a person present);
        whether it was made now."""
        with self._mutex:
            self.created = False
        self._master("unlock", create=True, person=True)
        return self.created

    def subkey(self, purpose: str, owner: str = "", *, context: bytes = b"", create: bool = True) -> bytes:
        """32 bytes derived from the master key for ``purpose`` and ``owner``; another purpose
        or owner gives a different key. ``create=False`` raises `KeystoreMissing` rather than
        making a master."""
        master = self._master(purpose, create=create)
        info = _fields(purpose.encode(), owner.encode(), context)
        return hkdf(master, info)

    def salted_subkey(self, purpose: str, directory: Path) -> bytes:
        """The subkey of ``purpose`` for this OS user, cut with the salt kept in ``directory`` (made on first use)."""
        salt = directory / "salt"
        if not salt.exists():
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(salt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, os.urandom(16))
            finally:
                os.close(fd)
        return self.subkey(purpose, os_user(), context=salt.read_bytes())

    def wrap(self, purpose: str, owner: str, plain: bytes) -> bytes:
        """``plain`` encrypted under the subkey of ``purpose`` and ``owner``."""
        cipher = aead(self.subkey(purpose, owner))
        nonce = os.urandom(12)
        return _MAGIC + nonce + cipher.encrypt(nonce, plain, _aad(purpose, owner))

    def unwrap(self, purpose: str, owner: str, blob: bytes) -> bytes:
        """What `wrap` sealed for the same purpose and owner, else `KeystoreError`."""
        if not blob.startswith(_MAGIC) or len(blob) < len(_MAGIC) + 12 + 16:
            raise KeystoreError("not a wrapped value")
        from cryptography.exceptions import InvalidTag
        cipher = aead(self.subkey(purpose, owner, create=False))
        try:
            return cipher.decrypt(blob[4:16], blob[16:], _aad(purpose, owner))
        except InvalidTag:
            raise KeystoreError("the wrapped value does not open under this purpose and owner") from None

    def lock(self) -> None:
        """Wipe the master key from memory; the next use reads the keystore again."""
        with self._mutex:
            if self._key is not None:
                self._key[:] = b"\0" * len(self._key)
            self._key = None

    def reset(self) -> None:
        """Delete the master item and the state files. Everything wrapped under it is lost."""
        self.lock()
        with self._flight("reset", person=True):
            self._call("delete", "reset", lambda r: r.delete_password(SERVICE, self.account), person=True)
        for name in ("provisioned.json", "noticed.json"):
            self._path(name).unlink(missing_ok=True)
        self._noticed = False








def _refused(cause: BaseException) -> str:
    """What the keystore did, in its own words: a prompt nobody answered is not a no, and a failure is not a refusal."""
    said = str(cause).strip() or type(cause).__name__
    if isinstance(cause, TimeoutError):
        what = "the keystore's prompt was not answered: it did not answer in time"
    elif isinstance(cause, OSError):
        what = f"the keystore could not be used ({said})"
    else:
        what = f"the keystore said: {said}"
    return (f"Poolhouse could not get its key, because {what}, so encrypted data stays locked. Nothing is remembered: "
            f"the next request asks the keystore again and your computer shows its prompt again. To set the key up now, "
            f"run `{UNLOCK_COMMAND}` in a terminal.")


def _over(ceiling: int, write: bool) -> str:
    """The count that stopped a call, what it means and what to do; never only a wait."""
    what = "changed" if write else "read"
    return (f"Poolhouse has already {what} the keystore {ceiling} times in the last hour, which only something repeating in a "
            f"loop does, so this call was not made and nothing is waiting for the person. Stop what is repeating; the count "
            f"falls as the hour passes, and `{UNLOCK_COMMAND}` in a terminal is never turned away by it.")


def _parse(held: Any) -> bytes | None:
    if not isinstance(held, str) or not held.startswith("v1:"):
        return None
    try:
        key = base64.b64decode(held[3:], validate=True)
    except ValueError:
        return None
    return key if len(key) == 32 else None


def aead(key: bytes) -> Any:
    """AES-256-GCM under ``key``."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise KeystoreUnavailable("the keystore needs `pip install cryptography`") from exc
    return AESGCM(key)


_DEFAULTS: dict[tuple[str, int], Keystore] = {}
_LOCK = threading.Lock()


def default() -> Keystore:
    """The process's keystore for the state root and keyring backend in force now."""
    try:
        import keyring
        backend = id(keyring.get_keyring())
    except ImportError:
        backend = 0
    key = (str(home.state("keystore")), backend)
    with _LOCK:
        if key not in _DEFAULTS:
            _DEFAULTS[key] = Keystore()
        return _DEFAULTS[key]
