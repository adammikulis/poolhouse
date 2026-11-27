"""A directory a session may read, resolved once and granted as the same object that was checked.

A path is resolved to its real path, each component is then opened with ``O_NOFOLLOW`` (a symlink in place of a component resolves to
nothing), the final directory is held as a descriptor, its device and inode are recorded, every refusal runs on that descriptor, and the
profile rule is built from that one path, never from a second resolution of the declaration (docs/design-isolated-launcher.md, 8.2). The
directory is opened again by that path after the proof, and a different identity is a deviation (`Resolved.unchanged`). PROBE P-bind: where a
backend can bind a directory by descriptor (`F_GETPATH` on macOS, `/proc/self/fd` on Linux) it should, and until then this holds.
"""

from __future__ import annotations

import errno
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from poolhouse import home

__all__ = ["MANAGERS", "Declaration", "Identity", "Resolved", "declarations", "declared", "interpreter_roots", "reach", "resolve",
           "system_toolchain"]

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,39}")
"""What a version declaration may name: one plain token, never a path."""


@dataclass(frozen=True, slots=True)
class Identity:
    """Which directory it is: the device and inode a descriptor reports."""

    device: int
    inode: int


@dataclass(slots=True)
class Resolved:
    """A directory held open: its own path, its identity and the executables it holds. Close it when the launch has ended."""

    path: str
    identity: Identity
    descriptor: int

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1

    def unchanged(self) -> bool:
        """Whether the directory at ``path`` is still the one that was checked: opened again without following a link, it has the same
        identity. A directory that was replaced under the same name, or a component that became a link, is not."""
        try:
            again = _open(self.path)
        except OSError:
            return False
        try:
            return _identity(again) == self.identity
        finally:
            os.close(again)


def _identity(descriptor: int) -> Identity:
    info = os.fstat(descriptor)
    return Identity(info.st_dev, info.st_ino)


def _open(path: str) -> int:
    """The descriptor of the directory ``path``, each component opened without following a link."""
    if not Path(path).is_absolute():
        raise OSError(errno.EINVAL, "a directory is named by an absolute path", path)
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(path).parts[1:]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
    except OSError:
        os.close(descriptor)
        raise
    return descriptor


def resolve(path: str | os.PathLike[str]) -> Resolved | None:
    """``path`` resolved once, or None when it names no directory that can be opened without following a link. The one `Resolved` is
    what is checked and what is granted: its path is the real path whose every component was opened, and nothing resolves it again."""
    if os.name != "posix":
        return None
    real = os.path.realpath(path)
    try:
        descriptor = _open(real)
    except OSError:
        return None
    return Resolved(real, _identity(descriptor), descriptor)


MANAGERS: dict[str, tuple[str, str]] = {
    ".python-version": ("python", ".pyenv/versions/{name}"),
    ".nvmrc": ("node", ".nvm/versions/node/v{name}"),
    "rust-toolchain": ("cargo", ".rustup/toolchains/{name}-*"),
}
"""A declaration file of a project, the program a toolchain directory must hold for it, and where the person's own version manager
installs that version under the home folder (each manager's documented layout)."""


def _holds_program(directory: Path, program: str) -> bool:
    return any(os.access(found, os.X_OK) for found in directory.glob(f"bin/{program}*") if found.is_file())


@dataclass(frozen=True, slots=True)
class Declaration:
    """What a project's own file names as a toolchain folder: the real path it names, and why it is refused (empty when it is granted)."""

    subject: str
    refused: str = ""


HOMEBREW = ("bin", "lib", "opt", "Cellar", "share", "include", "Frameworks")
"""What of `/opt/homebrew` and `/usr/local` is the system toolchain: not their `etc` or `var` (8.2)."""
COMMAND_LINE_TOOLS = "/Library/Developer/CommandLineTools"


def system_toolchain() -> tuple[str, ...]:
    """The system toolchain every session may read and run, as the directories this machine has (5.2 and 8.2): the command-line developer
    tools, and the program and library trees of Homebrew and `/usr/local`."""
    found = [COMMAND_LINE_TOOLS] if sys.platform == "darwin" else []
    for prefix in ("/opt/homebrew", "/usr/local"):
        found += [f"{prefix}/{name}" for name in HOMEBREW]
    return tuple(os.path.realpath(path) for path in found if Path(path).is_dir() and os.path.realpath(path) == path)


VERSION_FOLDERS = (".pyenv/versions", ".local/share/uv/python")
"""Where the person's own version managers keep one folder per interpreter, under the home folder (each manager's documented layout)."""
SYSTEM_VERSION_FOLDERS = ("/Library/Frameworks/Python.framework/Versions",)
"""Where the system installer keeps one folder per interpreter."""
NOT_INSTALLED = "the interpreter folder a virtual environment names is not one a version manager or the system installed"
"""Why a `home =` outside the closed list is refused: a fact of the folder, never the text the project chose."""


def interpreter_roots() -> frozenset[str]:
    """The closed list of folders a virtual environment's ``home =`` may name as its interpreter's prefix: each real folder (never a link) that
    sits directly in a version-manager or system install folder, and the prefix of the interpreter that runs this code. Nothing a project
    holds adds to it."""
    found = {os.path.realpath(sys.base_prefix)}
    folders = [str(home.user_home() / each) for each in VERSION_FOLDERS] + list(SYSTEM_VERSION_FOLDERS)
    for folder in folders:
        try:
            entries = list(Path(folder).iterdir())
        except OSError:
            continue
        found.update(os.path.realpath(entry) for entry in entries if entry.is_dir() and not entry.is_symlink())
    return frozenset(found)


def reach(root: str) -> tuple[str, ...]:
    """What of a granted toolchain folder a session reads and runs. An interpreter's folder is only its ``bin`` and its ``lib/pythonX.Y``: the
    rest of a prefix (``etc``, ``var``, ``share``, a keyring a tool keeps beside it) is not the interpreter's; any other toolchain is its folder."""
    base = Path(root)
    if not _holds_program(base, "python"):
        return (root,)
    libs = sorted(str(each) for each in base.glob("lib/python[0-9]*") if each.is_dir() and not each.is_symlink())
    return (str(base / "bin"), *libs)


PROGRAMS = ("python3", "python", "node", "cargo")
"""The programs the proof knows how to run with `--version`: an interpreter's, and those `MANAGERS` names."""


def runnable(folders: Iterable[str]) -> tuple[str, ...]:
    """The program the proof runs for each granted toolchain folder, the first of `PROGRAMS` in its ``bin``; none for a folder that holds none."""
    first = (next((each for each in (Path(folder) / "bin" / name for name in PROGRAMS) if each.is_file() and os.access(each, os.X_OK)), None)
             for folder in folders)
    return tuple(str(each) for each in first if each is not None)


def _system(path: Path) -> bool:
    """Whether every session already reads and runs ``path``: it lies in the system toolchain (Homebrew, the command-line tools)."""
    return any(path == Path(each) or Path(each) in path.parents for each in system_toolchain())


def _venv(project: Path) -> list[Declaration]:
    """The interpreter folder of a virtual environment, from ``home =`` of its ``pyvenv.cfg``: granted only when it is one of the closed
    list (`interpreter_roots`) that holds a Python; a folder already in the system toolchain names nothing new; anything else is refused with
    the reason, and the launch goes on without it."""
    config = project / ".venv" / "pyvenv.cfg"
    try:
        lines = config.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    named = next((line.split("=", 1)[1].strip() for line in lines if line.partition("=")[0].strip() == "home" and "=" in line), "")
    if not named:
        return []
    if not Path(named).is_absolute():
        return [Declaration(named, NOT_INSTALLED)]
    real = Path(os.path.realpath(named))
    prefix = real.parent if real.name == "bin" else real
    if _system(prefix):
        return []
    if str(prefix) not in interpreter_roots() or not _holds_program(prefix, "python"):
        return [Declaration(str(prefix), NOT_INSTALLED)]
    return [Declaration(str(prefix))]


def _managed(project: Path) -> list[Path]:
    found: list[Path] = []
    for file, (program, layout) in MANAGERS.items():
        try:
            text = (project / file).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        first = text.split(None, 1)[0].strip().removeprefix("v") if text.strip() else ""
        if not NAME.fullmatch(first):
            continue
        for candidate in sorted(home.user_home().glob(layout.format(name=first))):
            if _holds_program(candidate, program):
                found.append(candidate)
    return found


def declarations(project: str | os.PathLike[str]) -> list[Declaration]:
    """Every toolchain folder the project declares by its own files (``.python-version``, ``.venv/pyvenv.cfg``, ``rust-toolchain``,
    ``.nvmrc``), each the version folder of a version manager or of the system and never its parent, and each declaration the closed list
    refuses with the reason. A declaration that names a folder with no program of the declared kind, or anything that is not a plain version
    name, names nothing."""
    root = Path(project)
    found = [*_venv(root), *(Declaration(os.path.realpath(each)) for each in _managed(root))]
    return list({each.subject: each for each in found}.values())


def declared(project: str | os.PathLike[str]) -> list[str]:
    """The real paths of the toolchain folders the project declares that are granted: the declarations the closed list did not refuse."""
    return [each.subject for each in declarations(project) if not each.refused]
