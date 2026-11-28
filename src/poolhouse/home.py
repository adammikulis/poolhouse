"""Where this machine keeps poolhouse's state and its cache.

`state()` names anything under the state root; `cache()` names anything under the cache
root. Both read the environment when they are called, so a caller that moves a root sees
the move without reloading a module. `machine_id()` is the identity this machine is kept
under wherever its name would not tell two machines apart.

The account's own roots (`account_roots`) are not for everyone. These functions hand them to
the installed Poolhouse and to the repository's own entry points (`ENTRY_POINT`), and raise
`RealRootRefused` for a test run, a process a test run started, and Poolhouse run from a
source checkout, whichever way the path was spelled.
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

__all__ = ["CACHE_ENV", "ENTRY_POINT", "OVERRIDES", "ROOT_ENV", "RealRootRefused", "account_roots", "cache", "device_id", "expand",
           "guarded", "home", "machine_asset", "machine_id", "moved", "named_cache", "named_home", "real_roots", "state", "under_test",
           "user_home", "using_real_roots"]

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

TEST_PROCESS_ENV = "POOLHOUSE_TEST_PROCESS"
"""Set by a test run so every process it starts knows it is part of one. It only takes the account's roots away; nothing it can hold
grants them."""

_PACKAGE = Path(__file__).resolve().parent
_CHECKOUT = _PACKAGE.parents[1]
"""The folder this copy of the code sits in: the checkout, when it is one."""
ENTRY_POINT = "POOLHOUSE_ENTRY_POINT"
"""The name a program of the repository assigns at its module level, to a sentence saying what it needs the account's own roots for, when
it cannot do its work from a checkout without them (a hook, the test runner, the landing check). The grant is this declaration in code:
it counts only while that file is the program the process was started as, so importing it grants nothing, and no variable or argument
stands in for it."""
ENTRY_POINT_FOLDERS = ("scripts", "src/poolhouse", "examples")
"""Where a file that declares `ENTRY_POINT` must sit, in this copy of the code."""
ASSETS = {"llama.cpp": 0, "tools": 0, "guard": 1}
"""What a real-model test may read from the account's roots, by first name: 0 is under the state root, 1 under the cache root."""


class RealRootRefused(RuntimeError):
    """This process may not use the account's own Poolhouse state or cache; the message says why and what to do."""


def user_home() -> Path:
    """The account's home directory."""
    return Path.home()


def account_roots() -> tuple[Path, Path]:
    """The names of the account's default state and cache roots, for comparing against and protecting. Open nothing under them: a path
    to use comes from `state`, `cache` or `guarded`. A process that cannot read the password database (a strict box) gets the
    roots under its home directory."""
    try:
        account = Path(pwd.getpwuid(os.getuid()).pw_dir) if os.name == "posix" else user_home()
    except (KeyError, OSError):
        account = user_home()
    account = account.resolve()
    return account / DEFAULT_NAME, account / ".cache" / CACHE_NAME


_ACCOUNT_ROOTS: tuple[Path, Path] | None = None
with contextlib.suppress(RuntimeError, OSError):  # no home to be found: importing still works, and the first use says so
    _ACCOUNT_ROOTS = account_roots()
"""The roots refused below, read once: a caller that replaces `account_roots` or ``HOME`` later moves nothing."""


def _refused_roots() -> tuple[Path, Path]:
    return _ACCOUNT_ROOTS or account_roots()


def expand(path: str | Path) -> Path:
    """A path somebody named, with a leading ``~`` resolved; ``~name`` of an account that does not exist stays as written."""
    try:
        return Path(path).expanduser()
    except RuntimeError:
        return Path(path)


def under_test() -> bool:
    """Whether this process is a test run: the one place a root may be redirected for what a person owns (`guarded`)."""
    return "pytest" in sys.modules or "_pytest" in sys.modules


def _main_is_a_script() -> bool:
    """Whether the program this process runs is one of this checkout's scripts (which may import pytest to read a selection)."""
    file = getattr(sys.modules.get("__main__"), "__file__", None)
    return bool(file) and Path(file).resolve().is_relative_to(_CHECKOUT / "scripts")


def _mark_test_process() -> None:
    os.environ[TEST_PROCESS_ENV] = "1"


if under_test() and not _main_is_a_script():
    _mark_test_process()


def _is_entry_point() -> bool:
    """Whether this process is a program that declared `ENTRY_POINT`. Never for a test run or a process one started, nor for a process with
    no ``HOME``, whose environment was thrown away."""
    main = sys.modules.get("__main__")
    purpose, file = getattr(main, ENTRY_POINT, None), getattr(main, "__file__", None)
    if not isinstance(purpose, str) or not purpose.strip() or not file:
        return False
    if TEST_PROCESS_ENV in os.environ or "PYTEST_CURRENT_TEST" in os.environ or (os.name == "posix" and not os.environ.get("HOME")):
        return False
    program = Path(file).resolve()
    return any(program.is_relative_to(_CHECKOUT / folder) for folder in ENTRY_POINT_FOLDERS)


def _same_or_inside(path: str, root: str) -> bool:
    """Whether ``path`` is ``root`` or under it, by name or because one of its folders is the very folder the root is (or would be) in,
    however that folder is spelled (macOS's ``/System/Volumes/Data``, a bind mount)."""
    fold = str.casefold if sys.platform != "linux" else str  # the usual file systems of macOS and Windows ignore case
    if fold(path) == fold(root) or fold(path).startswith(fold(root).rstrip(os.sep) + os.sep):
        return True
    base, below = Path(root), []
    while not base.exists() and base != base.parent:  # a root not made yet is known by the nearest folder that is
        below.insert(0, fold(base.name))
        base = base.parent
    for folder in (Path(path), *Path(path).parents):
        with contextlib.suppress(OSError):
            if folder.samefile(base):
                return [fold(part) for part in Path(path).relative_to(folder).parts[:len(below)]] == below
    return False


def _is_installed_copy() -> bool:
    """Whether this is the installed Poolhouse: a packaged app, or a copy under ``site-packages`` (which every runtime is). A checkout, an
    export, a worktree and a development-linked (editable) install, which runs a checkout's own files, are none of these."""
    return bool(getattr(sys, "frozen", False)) or _PACKAGE.parent.name in ("site-packages", "dist-packages")


def _root_refusal() -> str:
    """Why this process may not use the account's own roots; empty when it may."""
    if _is_entry_point():
        return ""
    if under_test():
        _mark_test_process()
    if under_test() or TEST_PROCESS_ENV in os.environ or "PYTEST_CURRENT_TEST" in os.environ:
        return "this is a test run, or a process one started"
    if _is_installed_copy():
        return ""
    return "this is Poolhouse run from source, not the installed one"


def _allowed_path(path: Path) -> Path:
    """``path``, unless it lies in one of the account's own roots and this process may not use them."""
    why = _root_refusal()
    if not why:
        return path
    real = os.path.realpath(path)
    for root in _refused_roots():
        if _same_or_inside(real, os.path.realpath(root)):
            raise RealRootRefused(f"{path} is the account's own Poolhouse folder and is refused: {why}. Name scratch folders with "
                                  f"{ROOT_ENV} and {CACHE_ENV}.")
    return path


def machine_asset(*parts: str) -> Path:
    """Something the account's Poolhouse downloaded or built, for a real-model test to read and run and never to write: only the names in
    `ASSETS`, and only for a file of this checkout's ``tests``."""
    caller = Path(sys._getframe(1).f_code.co_filename).resolve()
    if not caller.is_relative_to(_CHECKOUT / "tests"):
        raise RealRootRefused(f"{caller} is not a test of {_CHECKOUT}; only its real-model tests read the account's downloaded files")
    if not parts or parts[0] not in ASSETS or any(part in ("", "..") or "/" in part or os.sep in part for part in parts):
        raise RealRootRefused(f"{'/'.join(parts)!r} is not a downloaded file a test may read; only {', '.join(ASSETS)} are")
    return _refused_roots()[ASSETS[parts[0]]].joinpath(*parts)


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
    and the state root of the environment only in a test run, so a test keeps its own isolated state.

    The account's own root is refused here to a copy run from source without the grant; the test-process variable plays no part, so no
    environment moves or blocks what the installed launch gate reads."""
    if _REAL.get() and not under_test():
        path = account_roots()[0].joinpath(*parts)
        return path if _is_installed_copy() else _allowed_path(path)
    return state(*parts)


def named_home() -> Path:
    """The state root the environment names, unchecked: for comparing against and protecting. Open nothing under it; `home` is for that."""
    named = os.environ.get(ROOT_ENV)
    return expand(named) if named else user_home() / DEFAULT_NAME


def named_cache() -> Path:
    """The cache root the environment names, unchecked, as `named_home` is for the state root."""
    named = os.environ.get(CACHE_ENV)
    return expand(named) if named else user_home() / ".cache" / CACHE_NAME


def home() -> Path:
    """The directory this machine keeps poolhouse's state in."""
    return _allowed_path(named_home())


def state(*parts: str) -> Path:
    """A path under the state root, honouring the variable that moves its first part."""
    named = OVERRIDES.get(parts[0]) if parts else None
    moved = os.environ.get(named) if named else None
    if moved:
        return _allowed_path(expand(moved).joinpath(*parts[1:]))
    return _allowed_path(named_home().joinpath(*parts))


def cache(*parts: str) -> Path:
    """A path under this machine's poolhouse cache directory."""
    return _allowed_path(named_cache().joinpath(*parts))


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
