"""The tree a test shard runs: packed on the sending side, checked and unpacked on the receiving one."""

from __future__ import annotations

import gzip
import hashlib
import io
import re
import subprocess
import tarfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MOST_PACKED = 24 << 20
MOST_UNPACKED = 256 << 20
MOST_MEMBERS = 20000
EXECUTABLE = 0o111
SHIPPED = re.compile(r"^(src|tests|scripts|contracts|patches|packaging|docs|app)/|^[^/]+$")
"""The top-level places a shard may carry; hidden directories such as .git are never among them."""


class TreeError(ValueError):
    """A tree that cannot be packed, or that a receiving device refuses to unpack."""


@dataclass(frozen=True)
class Commit:
    """One commit of the checkout at ``root``, to pack as it was committed."""

    root: Path
    id: str


def tracked_paths(root: Path) -> list[str]:
    """Tracked and untracked-but-not-ignored files under ``root`` that exist now, sorted."""
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=root,
                         capture_output=True, check=True).stdout
    names = sorted({name.decode() for name in out.split(b"\0") if name})
    return [name for name in names if (root / name).is_file() and not (root / name).is_symlink()
            and SHIPPED.match(name) and not name.startswith(".")]


def commit_id(root: Path, commit: str) -> str:
    """The full id of the commit ``commit`` names in the checkout at ``root``, or TreeError."""
    found = subprocess.run(["git", "rev-parse", "--verify", "--quiet", "--end-of-options", f"{commit}^{{commit}}"], cwd=root,
                           capture_output=True, check=False)
    if found.returncode:
        raise TreeError(f"no commit {commit[:80]!r} in this checkout")
    return found.stdout.decode().strip()


def working_files(root: Path) -> Iterator[tuple[str, bool, bytes]]:
    """``(name, executable, content)`` of each file of the working tree that travels."""
    for name in tracked_paths(root):
        path = root / name
        yield name, bool(path.stat().st_mode & EXECUTABLE), path.read_bytes()


def committed_files(root: Path, commit: str) -> Iterator[tuple[str, bool, bytes]]:
    """The same for the commit ``commit``, read from git's objects: nothing of the working tree is read."""
    listed = subprocess.run(["git", "ls-tree", "-r", "-z", "--full-tree", commit_id(root, commit)], cwd=root,
                            capture_output=True, check=True).stdout
    wanted = []
    for row in filter(None, listed.split(b"\0")):
        about, _, raw = row.partition(b"\t")
        mode, kind, blob = about.split()
        name = raw.decode()
        if kind == b"blob" and mode in (b"100644", b"100755") and SHIPPED.match(name) and not name.startswith("."):
            wanted.append((name, mode == b"100755", blob))
    wanted.sort()
    contents = io.BytesIO(subprocess.run(["git", "cat-file", "--batch"], cwd=root, check=True, capture_output=True,
                                         input=b"".join(blob + b"\n" for _, _, blob in wanted)).stdout)
    for name, executable, _ in wanted:
        size = int(contents.readline().split()[2])
        yield name, executable, contents.read(size)
        contents.read(1)


def pack(source: Path | Commit) -> tuple[bytes, str]:
    """The gzip tar of a working tree (a path) or of one commit, and the sha256 of those bytes.

    The archive holds no time and no owner, so the same files are the same bytes: whoever holds a commit can
    check the tree digest a result names."""
    files = committed_files(source.root, source.id) if isinstance(source, Commit) else working_files(source)
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", compresslevel=6, fileobj=buffer, mtime=0) as squeezed, \
            tarfile.open(fileobj=squeezed, mode="w") as archive:
        for name, executable, content in files:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o755 if executable else 0o644
            archive.addfile(info, io.BytesIO(content))
    data = buffer.getvalue()
    if len(data) > MOST_PACKED:
        raise TreeError(f"the packed tree is {len(data)} bytes; at most {MOST_PACKED} travel in one request")
    return data, hashlib.sha256(data).hexdigest()


def clean_name(name: str) -> str:
    """``name`` as a relative POSIX path that stays inside the tree, or TreeError."""
    path = PurePosixPath(name)
    bad = (not name or "\0" in name or "\\" in name or path.is_absolute() or ".." in path.parts
           or ":" in path.parts[0] or name != path.as_posix() or name.startswith("."))
    if bad or not SHIPPED.match(name):
        raise TreeError(f"the tree names a path a shard does not carry: {name[:80]!r}")
    return name


def check_inflation(data: bytes) -> None:
    """Refuse a single gzip stream that inflates past what a shard may carry, before any tar header is read.

    The digest only says the bytes are the sender's, not that they are small once inflated, and reading
    the member list inflates the whole stream: a small bomb would otherwise cost the receiver unbounded CPU.
    """
    limit = MOST_UNPACKED + MOST_MEMBERS * 1024
    inflater, total, pending = zlib.decompressobj(wbits=31), 0, data
    while pending and not inflater.eof:
        chunk = inflater.decompress(pending, 1 << 20)
        total += len(chunk)
        if total > limit:
            raise TreeError("the tree inflates to more than a shard may carry")
        pending = inflater.unconsumed_tail
    if inflater.unused_data:
        raise TreeError("the tree is more than one compressed stream")


def members(data: bytes, wanted: str) -> list[tarfile.TarInfo]:
    """The members of the packed tree once its digest is ``wanted`` and each one is a plain file."""
    if len(data) > MOST_PACKED:
        raise TreeError("the packed tree is larger than a shard may carry")
    if hashlib.sha256(data).hexdigest() != wanted:
        raise TreeError("the tree does not match its digest")
    try:
        check_inflation(data)
    except zlib.error as exc:
        raise TreeError(f"the tree is not a readable archive: {exc}") from None
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            found = archive.getmembers()
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise TreeError(f"the tree is not a readable archive: {exc}") from None
    if len(found) > MOST_MEMBERS or sum(member.size for member in found) > MOST_UNPACKED:
        raise TreeError("the tree holds more than a shard may carry")
    for member in found:
        if not member.isreg():
            raise TreeError(f"the tree holds something other than a plain file: {member.name[:80]!r}")
        clean_name(member.name)
    return found


def unpack(data: bytes, wanted: str, destination: Path) -> list[str]:
    """Write the verified tree under ``destination``; the names written."""
    found = members(data, wanted)
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in found:
            target = (root / member.name).resolve()
            if not target.is_relative_to(root):
                raise TreeError(f"the tree escapes its folder: {member.name[:80]!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise TreeError(f"cannot read {member.name[:80]!r}")
            target.write_bytes(source.read())
            target.chmod(0o755 if member.mode & EXECUTABLE else 0o644)
    return [member.name for member in found]
