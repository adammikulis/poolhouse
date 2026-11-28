"""Development-linked (editable) installs of Poolhouse in the Python interpreters of this machine, for `poolhouse-doctor`.

Such an install makes every program of an interpreter import a checkout's own files, and `poolhouse.home` refuses it the account's own
folders. `findings` reads package metadata on disk: it starts no interpreter and changes nothing.
"""

from __future__ import annotations

import json
import os
import re
import site
import sys
from collections.abc import Iterator, Mapping
from importlib.metadata import distributions
from pathlib import Path
from urllib.parse import unquote, urlsplit

from poolhouse.checks import Finding

__all__ = ["findings", "interpreters", "own_interpreter"]

_PROGRAM = re.compile(r"python(3(\.\d+)?)?(\.exe)?", re.IGNORECASE)
_PACKAGES = ("Lib/site-packages",) if os.name == "nt" else ("lib/python3*/site-packages", "lib/python3*/dist-packages",
                                                            "lib/python3/dist-packages")


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


def interpreters(environ: Mapping[str, str] | None = None) -> list[tuple[Path, Path]]:
    """(interpreter, package folder) for this interpreter, every one on ``PATH`` and every pyenv version; each folder once."""
    environ = os.environ if environ is None else environ
    found = own_interpreter()
    pyenv = [Path(environ["PYENV_ROOT"])] if environ.get("PYENV_ROOT") else []
    for entry in (one for one in map(Path, environ.get("PATH", "").split(os.pathsep)) if one.is_absolute()):
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


def _checkout_read_from(packages: Path) -> str:
    """The folder a development-linked Poolhouse in ``packages`` reads its code from, else empty."""
    for found in distributions(name="poolhouse", path=[str(packages)]):
        try:
            said = json.loads(found.read_text("direct_url.json") or "{}")
            if said.get("dir_info", {}).get("editable"):
                named = unquote(urlsplit(said["url"]).path)
                return str(Path(named.lstrip("/") if os.name == "nt" else named))
        except (OSError, ValueError, KeyError, AttributeError):
            continue
    return ""


def findings(environ: Mapping[str, str] | None = None) -> list[Finding]:
    """One finding for each interpreter that holds a development-linked Poolhouse, with the command that removes it."""
    out: list[Finding] = []
    for program, packages in interpreters(environ):
        source = _checkout_read_from(packages)
        if not source:
            continue
        remove = ([str(program), "-m", "pip", "uninstall", "-y", "poolhouse"] if (packages / "pip").is_dir()
                  else ["uv", "pip", "uninstall", "--python", str(program), "poolhouse"])
        out.append(Finding(
            name="python: development install", good=False, said=f"{program} runs Poolhouse straight from {source}", fix=remove,
            note="every program of that interpreter reads that folder's code, and Poolhouse refuses such a copy your own Poolhouse "
                 "folders; the command removes it, with its poolhouse commands, from that interpreter alone"))
    return out
