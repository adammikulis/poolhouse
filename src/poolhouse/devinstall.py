"""Stray copies of Poolhouse in the Python interpreters of this machine: development-linked (editable) installs, which make every
program of an interpreter import a checkout's own files, and other installed copies that could be run instead of this one.

`copies` reads package metadata on disk, starting no interpreter and changing nothing; `findings` is what `poolhouse-doctor` and the
installers show. `remove` runs one copy's removal, and only when a person asks for that copy.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import site
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from importlib.metadata import distributions
from pathlib import Path
from urllib.parse import unquote, urlsplit

from poolhouse import home
from poolhouse.checks import Finding

__all__ = ["Copy", "copies", "findings", "interpreters", "own_interpreter", "remove", "usual_folders"]

_PROGRAM = re.compile(r"python(3(\.\d+)?)?(\.exe)?", re.IGNORECASE)
_PACKAGES = ("Lib/site-packages",) if os.name == "nt" else ("lib/python3*/site-packages", "lib/python3*/dist-packages",
                                                            "lib/python3/dist-packages")
REMOVAL_WAIT_S = 120


@dataclass(frozen=True)
class Copy:
    """One stray copy: the interpreter that holds it, where its code is read from, and the command that removes only it."""

    id: str
    python: str
    source: str
    linked: bool
    command: tuple[str, ...]

    @property
    def said(self) -> str:
        if self.linked:
            return f"{self.python} runs Poolhouse straight from {self.source} (a development link)"
        return f"{self.python} holds another copy of Poolhouse, in {self.source}"


def _packages(prefix: Path) -> Iterator[Path]:
    for pattern in _PACKAGES:
        yield from (found for found in sorted(prefix.glob(pattern)) if found.is_dir())


def _beside(program: Path, packages: Path) -> Path:
    """The interpreter next to ``program`` that reads ``packages``, where one folder holds several versions."""
    named = program.with_name(packages.parent.name + program.suffix)
    return named if named.name.startswith("python3.") and named.is_file() else program


def _seen_by(program: Path) -> Iterator[tuple[Path, Path]]:
    """(interpreter, package folder) for one interpreter program."""
    folder = program.parent
    prefix = folder if os.name == "nt" and not (folder.parent / "pyvenv.cfg").is_file() else folder.parent
    prefixes = [prefix]
    if not (prefix / "pyvenv.cfg").is_file():  # a link to an interpreter elsewhere reads that one's packages too
        real = Path(os.path.realpath(program)).parent
        prefixes.append(real if os.name == "nt" else real.parent)
    for one in prefixes:
        yield from ((_beside(program, packages), packages) for packages in _packages(one))


def _pyenv(root: Path) -> Iterator[tuple[Path, Path]]:
    for version in sorted((root / "versions").glob("*")):
        program = version / "python.exe" if os.name == "nt" else version / "bin" / "python"
        yield from ((program, packages) for packages in _packages(version))


def own_interpreter() -> list[tuple[Path, Path]]:
    """The interpreter running this, and the account's own package folder for its version."""
    found = [(Path(sys.executable), packages) for packages in _packages(Path(sys.prefix))]
    version = f"python{sys.version_info[0]}.{sys.version_info[1]}"
    return [*found, (Path(version), Path(site.getusersitepackages()))]


def usual_folders(environ: Mapping[str, str]) -> list[Path]:
    """Where interpreters are commonly installed, looked at too: a window opened from the desktop has a short ``PATH``."""
    if os.name == "nt":
        local = Path(environ.get("LOCALAPPDATA") or home.user_home() / "AppData" / "Local")
        return sorted((local / "Programs" / "Python").glob("Python3*"))
    return [Path("/opt/homebrew/bin"), Path("/usr/local/bin"), home.user_home() / ".pyenv" / "shims"]


def interpreters(environ: Mapping[str, str] | None = None) -> list[tuple[Path, Path]]:
    """(interpreter, package folder) for this interpreter, every one on ``PATH`` or in a usual place, and every pyenv version; each
    package folder once."""
    environ = os.environ if environ is None else environ
    found = own_interpreter()
    pyenv = [Path(environ["PYENV_ROOT"])] if environ.get("PYENV_ROOT") else []
    search = [*map(Path, environ.get("PATH", "").split(os.pathsep)), *usual_folders(environ)]
    for entry in (one for one in search if one.is_absolute()):
        if entry.name == "shims" and (entry.parent / "versions").is_dir():
            pyenv.append(entry.parent)
            continue
        try:
            programs = sorted(p for p in entry.iterdir() if _PROGRAM.fullmatch(p.name))
        except OSError:
            continue
        for program in programs:
            found += _seen_by(program)
    for root in pyenv:
        found += _pyenv(root)
    once: dict[str, tuple[Path, Path]] = {}
    for program, packages in found:
        once.setdefault(os.path.realpath(packages), (program, packages))
    return list(once.values())


def _installed_by_poolhouse(packages: Path, environ: Mapping[str, str]) -> bool:
    """Whether ``packages`` belongs to the installed Poolhouse itself: the one running this, a runtime, or the installers' own folder."""
    own = [Path(sys.prefix), home.named_home(), home.user_home() / home.DEFAULT_NAME, Path("/opt/poolhouse")]
    own += [Path(environ[name]) for name in ("POOLHOUSE_PREFIX",) if environ.get(name)]
    own += [Path(environ["LOCALAPPDATA"]) / "poolhouse"] if environ.get("LOCALAPPDATA") else []
    real = Path(os.path.realpath(packages))
    return any(real.is_relative_to(os.path.realpath(folder)) for folder in own)


def _held(packages: Path) -> tuple[str, bool] | None:
    """(where its code is read from, whether by a development link) of the Poolhouse in ``packages``, or None."""
    for found in distributions(name="poolhouse", path=[str(packages)]):
        try:
            said = json.loads(found.read_text("direct_url.json") or "{}")
            if said.get("dir_info", {}).get("editable"):
                named = unquote(urlsplit(said["url"]).path)
                return str(Path(named.lstrip("/") if os.name == "nt" else named)), True
        except (OSError, ValueError, KeyError, AttributeError):
            continue
        return str(packages), False
    return None


def copies(environ: Mapping[str, str] | None = None) -> list[Copy]:
    """Every stray copy of Poolhouse this machine's interpreters hold."""
    environ = os.environ if environ is None else environ
    out: list[Copy] = []
    for program, packages in interpreters(environ):
        held = _held(packages)
        if held is None or (not held[1] and _installed_by_poolhouse(packages, environ)):
            continue
        command = ((str(program), "-m", "pip", "uninstall", "-y", "poolhouse") if (packages / "pip").is_dir()
                   else ("uv", "pip", "uninstall", "--python", str(program), "poolhouse"))
        key = hashlib.sha256(os.path.realpath(packages).encode()).hexdigest()[:16]
        out.append(Copy(key, str(program), held[0], held[1], command))
    return out


def findings(environ: Mapping[str, str] | None = None) -> list[Finding]:
    """One finding for each stray copy, with the command that removes it; none on a machine that has none."""
    return [Finding(
        name="python: development install" if one.linked else "python: another copy of Poolhouse", good=False, said=one.said,
        fix=list(one.command),
        note=("every program of that interpreter reads that folder's code, and Poolhouse refuses such a copy your own Poolhouse "
              "folders" if one.linked else "a program of that interpreter would run this copy instead of the installed Poolhouse")
             + "; the command removes only Poolhouse, with its poolhouse commands, from that interpreter. Keep it if you meant it")
        for one in copies(environ)]


def remove(wanted: str, environ: Mapping[str, str] | None = None) -> str:
    """Remove the one stray copy whose `Copy.id` is ``wanted``; empty when it is gone, else what the removal said. Only a copy a
    fresh look still finds is removed."""
    found = {one.id: one for one in copies(environ)}
    if wanted not in found:
        return "that copy is not on this computer any more"
    with tempfile.TemporaryDirectory() as empty:  # nothing in the working folder can stand in for pip
        env = {name: value for name, value in os.environ.items() if name not in ("PYTHONPATH", "PYTHONHOME")}
        try:
            done = subprocess.run(list(found[wanted].command), cwd=empty, env=env, capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL, timeout=REMOVAL_WAIT_S, check=False)
        except (OSError, subprocess.TimeoutExpired) as why:
            return str(why)
    if wanted in {one.id for one in copies(environ)}:
        return (done.stderr or done.stdout or "the removal did not take it away").strip()[-600:]
    return ""
