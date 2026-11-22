"""Names and archives that came from somewhere else: a file name a remote server chose, an
entry in a downloaded archive. Nothing here lets one reach outside the directory it is put in."""

from __future__ import annotations

import posixpath
import stat
import tarfile
import unicodedata
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath, PureWindowsPath

__all__ = ["MOST_ENTRIES", "MOST_UNPACKED", "Unsafe", "check_links", "safe_filename", "safe_join",
           "unpack"]

MOST_ENTRIES = 20_000
MOST_UNPACKED = 8 << 30
MOST_NAME = 200
MOST_HOPS = 40
DEVICES = {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)),
           *(f"lpt{n}" for n in range(1, 10))}
FORBIDDEN = set('<>:"/\\|?*')


class Unsafe(ValueError):
    """A name or an archive that would write outside where it was told to."""


def safe_filename(name: str) -> str:
    """``name`` if it is one plain file name; `Unsafe` for a path, ``.``, ``..``, an empty
    name, a control character, a character Windows forbids, a trailing dot or space, a
    Windows device name (``nul``, ``con.txt``), or a name over 200 characters."""
    if not isinstance(name, str) or not name or name != name.strip():
        raise Unsafe("a file name has text in it, with no spaces at either end")
    if name in (".", "..") or len(name) > MOST_NAME:
        raise Unsafe(f"{name[:40]!r} is not a usable file name")
    if any(ch in FORBIDDEN or unicodedata.category(ch).startswith("C") for ch in name):
        raise Unsafe(f"{name[:40]!r} holds a character a file name cannot")
    if name.endswith("."):
        raise Unsafe(f"{name[:40]!r} ends with a dot")
    if name.split(".")[0].lower() in DEVICES:
        raise Unsafe(f"{name[:40]!r} is a device name on Windows")
    return name


def _parts(relative: str) -> tuple[str, ...]:
    """The parts of an archive path, each a `safe_filename`; `Unsafe` if it is absolute."""
    if relative.startswith(("/", "\\")) or PureWindowsPath(relative).drive:
        raise Unsafe(f"{relative[:60]!r} is an absolute path")
    parts = PurePosixPath(relative.replace("\\", "/")).parts
    for part in parts:
        safe_filename(part)
    return parts


def safe_join(root: Path, relative: str) -> Path:
    """``relative`` under ``root``, with every part a `safe_filename`; `Unsafe` if it is
    absolute, climbs out, or reaches the outside through a symlink already on disk."""
    parts = _parts(relative)
    target = (root / Path(*parts)).resolve() if parts else root.resolve()
    if root.resolve() not in (target, *target.parents):
        raise Unsafe(f"{relative[:60]!r} leaves {root}")
    return target


def unpack(archive: Path, into: Path, *, max_bytes: int = MOST_UNPACKED,
           max_entries: int = MOST_ENTRIES) -> list[Path]:
    """Extract a zip or tar archive into ``into``, returning what was written.

    Every entry name must pass `safe_join`; hard links, devices and anything but files,
    directories and (in a tar) contained symlinks are refused; the entry count and the
    unpacked size are capped. A tar's symlinks are made last, as symlinks, under the rule of
    `check_links`; a zip's symlinks are refused.
    """
    into.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        return _zip(archive, into, max_bytes, max_entries)
    return _tar(archive, into, max_bytes, max_entries)


def _zip(archive: Path, into: Path, most: int, entries: int) -> list[Path]:
    written: list[Path] = []
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        if len(infos) > entries:
            raise Unsafe(f"the archive holds {len(infos)} entries; the most taken is {entries}")
        if sum(i.file_size for i in infos) > most:
            raise Unsafe(f"the archive unpacks to more than {most} bytes")
        for info in infos:
            kind = stat.S_IFMT(info.external_attr >> 16)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise Unsafe(f"{info.filename[:60]!r} is a link or a device")
            target = safe_join(into, info.filename.rstrip("/"))
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                copied = 0
                while block := src.read(1 << 20):
                    copied += len(block)
                    if copied > info.file_size:
                        raise Unsafe(f"{info.filename[:60]!r} is larger than it says")
                    out.write(block)
            written.append(target)
    return written


def check_links(entries: Iterable[tuple[str, str, str]]) -> None:
    """Refuse an archive whose symlinks could reach outside it or whose names collide.

    ``entries`` is ``(name, kind, linkname)`` for every entry, ``kind`` being ``"file"``,
    ``"dir"`` or ``"link"`` (``linkname`` is only read for a link). The archive is read as a
    tree and nothing is touched on disk. A link is accepted only if its target is a relative
    path that, resolved through the archive's own links, stays inside the root and names an
    entry the archive holds: no absolute target, no ``..`` out of the root, no chain or cycle
    that escapes or never ends, nothing dangling. No entry may be named twice (two
    directories may) or sit under a link or a file, so nothing is ever written through a link.
    """
    kinds: dict[str, str] = {}
    links: dict[str, str] = {}
    for name, kind, linkname in entries:
        key = "/".join(_parts(name.rstrip("/")))
        if not key:
            if kind != "dir":
                raise Unsafe(f"{name[:60]!r} names the root")
            continue
        if key in kinds and not (kinds[key] == kind == "dir"):
            raise Unsafe(f"{name[:60]!r} occurs more than once")
        kinds[key] = kind
        if kind == "link":
            if (not linkname or linkname.startswith(("/", "\\")) or "\\" in linkname
                    or PureWindowsPath(linkname).drive):
                raise Unsafe(f"a link to {linkname[:60]!r} is not a relative path")
            links[key] = linkname
    held = set(kinds)
    for key in kinds:
        for parent in PurePosixPath(key).parents:
            above = str(parent)
            if above != "." and kinds.get(above, "dir") != "dir":
                raise Unsafe(f"{key[:60]!r} passes through a file or link")
            held.add(above)
    for key, linkname in links.items():
        here = posixpath.dirname(key)
        reached = _reach(f"{here}/{linkname}" if here else linkname, links)
        if reached and reached not in held:
            raise Unsafe(f"{key[:60]!r} links to {linkname[:60]!r}, which the archive does not hold")


def _reach(path: str, links: dict[str, str]) -> str:
    """Where ``path`` lands inside the archive once every link on it is followed."""
    done: list[str] = []
    todo = path.split("/")
    hops = 0
    while todo:
        part = todo.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not done:
                raise Unsafe(f"a link to {path[:60]!r} leaves the archive")
            done.pop()
            continue
        done.append(part)
        here = "/".join(done)
        if here in links:
            hops += 1
            if hops > MOST_HOPS:
                raise Unsafe(f"a link to {path[:60]!r} never ends")
            done.pop()
            todo = links[here].split("/") + todo
    return "/".join(done)


def _tar(archive: Path, into: Path, most: int, entries: int) -> list[Path]:
    written: list[Path] = []
    with tarfile.open(archive) as tf:
        members = tf.getmembers()
        if len(members) > entries:
            raise Unsafe(f"the archive holds {len(members)} entries; the most taken is {entries}")
        if sum(m.size for m in members if m.isfile()) > most:
            raise Unsafe(f"the archive unpacks to more than {most} bytes")
        for member in members:
            if not (member.isfile() or member.isdir() or member.issym()):
                raise Unsafe(f"{member.name[:60]!r} is a hard link or a device")
        check_links((m.name, "dir" if m.isdir() else "link" if m.issym() else "file", m.linkname)
                    for m in members)
        links: list[tarfile.TarInfo] = []
        for member in members:
            target = safe_join(into, member.name.rstrip("/"))
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if member.issym():
                links.append(member)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tf.extractfile(member)
            if source is None:
                raise Unsafe(f"{member.name[:60]!r} cannot be read")
            with source, target.open("wb") as out:
                while block := source.read(1 << 20):
                    out.write(block)
            if member.mode & 0o111:
                target.chmod(0o755)
            written.append(target)
        for member in links:
            name = PurePosixPath(member.name.rstrip("/"))
            parent = safe_join(into, name.parent.as_posix())
            parent.mkdir(parents=True, exist_ok=True)
            target = parent / name.name
            target.unlink(missing_ok=True)
            target.symlink_to(member.linkname)
            written.append(target)
    return written
