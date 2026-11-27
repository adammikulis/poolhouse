"""What Poolhouse can install, it installs the first time something needs it.

`module` and `ensure` put the requirements of one of this project's extras into the environment this
process runs in (a runtime Poolhouse made, or the project's virtual environment) with the installer a
runtime is built with (`net.uvinstall`, else pip through `net.packages`): from the package cache
alone first, so what was installed once installs again offline, then from the package index. The
requirements are the ones the project declares, never a name taken from input. One process installs at
a time and the others wait for it. A system Python is never written to, and nothing is installed when
Poolhouse is upgraded: only when something needs it.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys
import threading
import time
import tomllib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, requires, version
from pathlib import Path
from types import ModuleType

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from poolhouse import lazy, lock
from poolhouse.log import warn

__all__ = ["SWITCH", "Unavailable", "download_refusal", "ensure", "module", "refusal", "requirements", "target"]

SWITCH = "POOLHOUSE_INSTALL_AT_NEED"
"""``off`` stops every first-use install and download; nothing turns one on that is not otherwise allowed."""
LOCK = ".poolhouse-install.lock"
WAIT_S = 1800.0
INSTALL_S = 1800.0
BEAT_S = 15.0
"""Seconds between the lines saying an install is still running."""


class Unavailable(ModuleNotFoundError):
    """A module missing here that could not be installed; the message says why and ``name`` names it."""


def download_refusal() -> str:
    """Why nothing may be fetched from the internet at first use, or an empty string when it may."""
    if os.environ.get(SWITCH, "").strip().lower() == "off":
        return f"installing at first use is switched off ({SWITCH}=off)"
    saved = lazy.load("poolhouse.fleet.runtime_paths", "default_root")() / "settings.json"
    if saved.is_file() and lazy.load("poolhouse.fleet.settings", "Settings").load(saved).download_sources == "lan":
        return "this device's download source is LAN only (Settings, Downloads), so nothing is fetched from the internet"
    return ""


def refusal() -> str:
    """Why a missing extra could not be installed here, known without trying; empty when it would be tried."""
    if os.environ.get(SWITCH, "").strip().lower() == "off":
        return f"installing at first use is switched off ({SWITCH}=off)"
    try:
        target()
    except Unavailable as exc:
        return str(exc)
    return ""


def target() -> Path:
    """The interpreter whose environment may be installed into. Raises `Unavailable` for one that may not."""
    if getattr(sys, "frozen", False):
        raise Unavailable("this is the packaged app, whose libraries ship inside it; libraries for jobs are "
                          "installed into its own environment instead")
    if sys.prefix == sys.base_prefix:
        raise Unavailable(f"{sys.executable} is a system Python and Poolhouse installs no system packages; run this "
                          "from Poolhouse's own runtime or the project's virtual environment")
    return Path(sys.executable)


def _table() -> dict[str, list[str]]:
    """Every extra's declared requirements, and the base ones under ``""``: the source checkout's own
    ``pyproject.toml``, else the installed distribution's metadata."""
    checkout = lazy.load("poolhouse.paths", "source_checkout")()
    if checkout is not None:
        project = tomllib.loads((checkout / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        return {"": list(project.get("dependencies", [])), **project.get("optional-dependencies", {})}
    try:
        base = [line for line in requires("poolhouse") or [] if "extra ==" not in line]
        return {"": base, **lazy.load("poolhouse.installed", "declared")()}
    except PackageNotFoundError as exc:
        raise Unavailable("this copy of Poolhouse records no requirements: it is neither a source checkout "
                          "nor an installed distribution") from exc


def requirements(extra: str, table: dict[str, list[str]] | None = None) -> list[str]:
    """What installing ``extra`` asks for on this interpreter, the extras it names inside ``poolhouse[...]``
    followed. ``poolhouse`` itself is never among them: it is not installed from an index."""
    table = _table() if table is None else table
    out: list[str] = []
    seen: set[str] = set()
    waiting = [extra]
    while waiting:
        name = waiting.pop(0)
        if name in seen:
            continue
        seen.add(name)
        if name not in table:
            raise Unavailable(f"this Poolhouse declares no {name!r} extra")
        for text in table[name]:
            want = Requirement(text)
            if want.marker is not None and not want.marker.evaluate({"extra": name}):
                continue
            if canonicalize_name(want.name) == "poolhouse":
                waiting.extend(sorted(want.extras))
                continue
            want.marker = None
            if str(want) not in out:
                out.append(str(want))
    return out


def _kept(table: dict[str, list[str]]) -> list[str]:
    """The declared ranges that what is installed already satisfies, asked for again so that an install
    cannot move a package Poolhouse is using out of its range."""
    names = ["", *(one.extra for one in lazy.load("poolhouse.installed", "STANDARD"))]
    out = []
    for text in (row for name in names if name in table for row in requirements(name, table)):
        want = Requirement(text)
        try:
            here = version(want.name)
        except PackageNotFoundError:
            continue
        if not want.url and want.specifier.contains(here, prereleases=True):
            out.append(text)
    return out


def _lock(python: Path | None) -> Path:
    """The lock beside the environment being installed into: this one's, else ``python``'s (``<prefix>/bin/python``)."""
    return (Path(sys.prefix) if python is None else python.parent.parent) / LOCK


def _present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False
    except ValueError:
        return name in sys.modules


@contextmanager
def _beating(what: str) -> Iterator[None]:
    """Say every `BEAT_S` seconds that ``what`` is still running."""
    done = threading.Event()
    began = time.monotonic()

    def beat() -> None:
        while not done.wait(BEAT_S):
            warn(f"still {what} ({time.monotonic() - began:.0f}s)")

    thread = threading.Thread(target=beat, daemon=True, name="install-progress")
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join()


def _install(python: Path, specs: list[str]) -> subprocess.CompletedProcess:
    """Install ``specs`` into ``python``'s environment: the cache alone first, then the index."""
    uv = lazy.load("poolhouse.net.uvinstall", "install")
    environment = lazy.load("poolhouse.runtime", "installer_environment")()
    cached = uv(python, specs, timeout=INSTALL_S, environment=environment, offline=True)
    if cached is not None and not cached.returncode:
        return cached
    if refused := download_refusal():
        raise Unavailable(f"they are not in this device's package cache, and {refused}")
    if cached is not None:
        return uv(python, specs, timeout=INSTALL_S, environment=environment)
    if not _present("pip"):
        subprocess.run([str(python), "-m", "ensurepip"], capture_output=True, timeout=INSTALL_S, check=False,
                       env=environment)
    return lazy.load("poolhouse.net.packages", "run")(python, ["install", *specs], timeout=INSTALL_S,
                                                      env=environment)


def _supply(extra: str, wanted: tuple[str, ...], python: Path | None,
            run: Callable[[Path, list[str]], subprocess.CompletedProcess] | None) -> None:
    if os.environ.get(SWITCH, "").strip().lower() == "off":
        raise Unavailable(f"the {extra!r} libraries ({', '.join(wanted)}) are not here, and installing at first "
                          f"use is switched off ({SWITCH}=off)")
    held = _lock(python)
    python = python or target()
    try:
        with lock.only_one(held, wait=True, timeout=WAIT_S, announce=warn, note=f"installing the {extra} libraries"):
            _install_missing(extra, wanted, python, run or _install)
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as exc:
        raise Unavailable(f"the {extra!r} libraries could not be installed: {exc}") from exc


def _install_missing(extra: str, wanted: tuple[str, ...], python: Path,
                     run: Callable[[Path, list[str]], subprocess.CompletedProcess]) -> None:
    """Under the lock: install the extra unless what was wanted arrived while this process waited."""
    importlib.invalidate_caches()
    if all(map(_present, wanted)):
        return
    table = _table()
    asked = requirements(extra, table)
    if not asked:
        raise Unavailable(f"the {extra!r} libraries have no release for this Python and system")
    warn(f"installing the {extra} libraries, needed for the first time: {', '.join(asked)}")
    with _beating(f"installing the {extra} libraries"):
        done = run(python, [*asked, *(kept for kept in _kept(table) if kept not in asked)])
    if done.returncode:
        said = "\n".join(f"{done.stdout or ''}\n{done.stderr or ''}".strip().splitlines()[-12:])
        raise Unavailable(f"the {extra!r} libraries could not be installed (the installer exited "
                          f"{done.returncode}):\n{said}")
    importlib.invalidate_caches()
    if gone := [name for name in wanted if not _present(name)]:
        raise Unavailable(f"installing the {extra!r} libraries did not bring {', '.join(gone)}")
    warn(f"installed the {extra} libraries")


def ensure(extra: str, *modules: str, python: Path | None = None,
           run: Callable[[Path, list[str]], subprocess.CompletedProcess] | None = None) -> None:
    """Have ``modules`` importable here, installing the extra ``extra`` when one is missing.

    ``python`` and ``run`` replace the interpreter written to and the installer. Raises `Unavailable`.
    """
    held = _lock(python)
    if all(map(_present, modules)) and not (held.is_file() and lock.held_by(held)):
        return
    try:
        _supply(extra, modules, python, run)
    except Unavailable as exc:
        exc.name = next((name for name in modules if not _present(name)), modules[0])
        raise


def module(name: str, extra: str) -> ModuleType:
    """The module ``name``, the extra ``extra`` installed first when it is not here. Raises `Unavailable`."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        missing = exc.name or name
    try:
        _supply(extra, tuple(dict.fromkeys((missing, name))), None, None)
    except Unavailable as exc:
        exc.name = missing
        raise
    return importlib.import_module(name)
