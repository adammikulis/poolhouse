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
from dataclasses import dataclass
from pathlib import Path

from poolhouse import home

__all__ = ["MANAGERS", "Identity", "Resolved", "declared", "resolve"]

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


def _venv_prefix(project: Path) -> list[Path]:
    """The directory a virtual environment's interpreter belongs to, from ``home =`` of its ``pyvenv.cfg``; a file that names anything
    else, or a directory with no interpreter, names nothing."""
    config = project / ".venv" / "pyvenv.cfg"
    try:
        lines = config.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    named = next((line.split("=", 1)[1].strip() for line in lines if line.partition("=")[0].strip() == "home" and "=" in line), "")
    where = Path(named)
    prefix = where.parent if where.name == "bin" else where
    return [prefix] if named and where.is_absolute() and _holds_program(prefix, "python") else []


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


def declared(project: str | os.PathLike[str]) -> list[str]:
    """The real paths of the toolchain directories the project declares by its own files (``.python-version``, ``.venv/pyvenv.cfg``,
    ``rust-toolchain``, ``.nvmrc``): each the version directory of the person's own version manager, never its parent. A declaration
    that names a directory with no program of the declared kind, or anything that is not a plain version name, names nothing."""
    root = Path(project)
    return list(dict.fromkeys(os.path.realpath(each) for each in (*_venv_prefix(root), *_managed(root))))
