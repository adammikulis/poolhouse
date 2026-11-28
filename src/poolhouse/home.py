"""Where this machine keeps poolhouse's state and its cache.

`state()` names anything under the state root; `cache()` names anything under the cache
root. Both read the environment when they are called, so a caller that moves a root sees
the move without reloading a module. `machine_id()` is the identity this machine is kept
under wherever its name would not tell two machines apart.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import secrets
import sys
from importlib import import_module
from pathlib import Path

from poolhouse import lazy
from poolhouse.files import promote

if os.name == "posix":
    import pwd

__all__ = ["CACHE_ENV", "OVERRIDES", "ROOT_ENV", "account_roots", "cache", "device_id", "expand", "guarded", "home", "machine_id",
           "moved", "real_roots", "state", "under_test", "user_home", "using_real_roots"]

ROOT_ENV = "POOLHOUSE_HOME"
"""Moves the state root."""
DEFAULT_NAME = ".poolhouse"
CACHE_NAME = "poolhouse"

CACHE_ENV = "POOLHOUSE_CACHE"
"""Moves the cache root."""

OVERRIDES = {
    "bench": "POOLHOUSE_BENCH_HOME",
    "ingest": "POOLHOUSE_INGEST_HOME",
    "jobs": "POOLHOUSE_JOBS_HOME",
    "train": "POOLHOUSE_TRAIN_HOME",
    "web": "POOLHOUSE_WEB_PROFILE",
    "fit.json": "POOLHOUSE_FIT_FILE",
    "profiles.json": "POOLHOUSE_PROFILES_FILE",
    "limits.json": "POOLHOUSE_LIMITS_FILE",
    "rates.json": "POOLHOUSE_RATES",
}
"""The variable that moves one name out of the state root, by that name."""


def user_home() -> Path:
    """The account's home directory."""
    return Path.home()


def account_roots() -> tuple[Path, Path]:
    """Return the actual account's default state and cache roots."""
    account = Path(pwd.getpwuid(os.getuid()).pw_dir) if os.name == "posix" else user_home()
    account = account.resolve()
    return account / DEFAULT_NAME, account / ".cache" / CACHE_NAME


def expand(path: str | Path) -> Path:
    """A path somebody named, with a leading ``~`` resolved; ``~name`` of an account that does not exist stays as written."""
    try:
        return Path(path).expanduser()
    except RuntimeError:
        return Path(path)


def under_test() -> bool:
    """Whether this process is a test run: the one place a root may be redirected for what a person owns (`guarded`)."""
    return "pytest" in sys.modules or "_pytest" in sys.modules


_REAL: contextvars.ContextVar[bool] = contextvars.ContextVar("poolhouse_real_roots", default=False)


class _RealRoots(contextlib.ContextDecorator):
    """Within the block or the decorated function, `guarded` names the account's own roots and not the ones the environment moves (a test run
    keeps its own). The launch gate, the project policy and the node's attestation of who is asking run inside it."""

    def _recreate_cm(self) -> _RealRoots:
        return _RealRoots()

    def __enter__(self) -> _RealRoots:
        self._token = _REAL.set(True)
        return self

    def __exit__(self, *_ignored: object) -> None:
        _REAL.reset(self._token)


using_real_roots = _RealRoots
real_roots = _RealRoots()


def guarded(*parts: str) -> Path:
    """A path under the root of what a person owns and an agent must not redirect: the authority registry, its audit log, the project policies
    and the node's state. Everything else asks `state`, which the environment may move. Within `using_real_roots` (the launch gate and what it
    reads) it is the account's own state root (the account's home from the password database, whatever ``HOME`` or ``POOLHOUSE_HOME`` say),
    and the state root of the environment only in a test run, so a test keeps its own isolated state."""
    if _REAL.get() and not under_test():
        return account_roots()[0].joinpath(*parts)
    return state(*parts)


def home() -> Path:
    """The directory this machine keeps poolhouse's state in."""
    named = os.environ.get(ROOT_ENV)
    return expand(named) if named else user_home() / DEFAULT_NAME


def state(*parts: str) -> Path:
    """A path under the state root, honouring the variable that moves its first part."""
    named = OVERRIDES.get(parts[0]) if parts else None
    moved = os.environ.get(named) if named else None
    if moved:
        return expand(moved).joinpath(*parts[1:])
    return home().joinpath(*parts)


def cache(*parts: str) -> Path:
    """A path under this machine's poolhouse cache directory."""
    named = os.environ.get(CACHE_ENV)
    root = expand(named) if named else user_home() / ".cache" / CACHE_NAME
    return root.joinpath(*parts)


def moved(*parts: str) -> Path:
    """A state path, taking an older copy under the cache root across the first time it is
    asked for, and reading it where it is when the move fails."""
    current = state(*parts)
    if current.exists():
        return current
    older = cache(*parts)
    if not older.exists():
        return current
    try:
        current.parent.mkdir(parents=True, exist_ok=True)
        promote(older, current)
    except OSError:
        return older
    return current


def machine_id() -> str:
    """This machine's identity: minted once under the state root and read from there after."""
    path = state("machine-id")
    try:
        held = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        held = ""
    if held:
        return held
    path.parent.mkdir(parents=True, exist_ok=True)
    draft = path.with_name(f".machine-id.{os.getpid()}.{secrets.token_hex(4)}")
    draft.write_text(secrets.token_hex(8), encoding="utf-8")
    try:
        os.link(draft, path)  # fails when another process minted first; theirs is kept
    except FileExistsError:
        pass
    finally:
        draft.unlink()
    return path.read_text(encoding="utf-8").strip()


def device_id():
    """Fail explicitly when the host provider cannot identify this device."""
    try:
        try:
            machineid = import_module("machineid")
        except ModuleNotFoundError:   # installed the first time a device is identified
            lazy.load("poolhouse.provision", "ensure")("coordinator", "machineid")
            machineid = import_module("machineid")
        identity = machineid.hashed_id("poolhouse")
    except (ImportError, OSError, RuntimeError) as exc:
        raise RuntimeError(f"physical device identity unavailable: {exc}") from exc
    if not isinstance(identity, str) or len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
        raise RuntimeError("physical device identity provider returned an invalid app-scoped hash")
    return identity
