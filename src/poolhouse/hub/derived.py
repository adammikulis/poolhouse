"""Files poolhouse makes from a Hub repository's files, kept in the hub cache beside their source.

A derived file has no repository on the Hub. It lives in one of poolhouse's own, laid out as the cache lays out
any repository: ``poolhouse-derived/<owner>.<name>.<recipe>`` (the source repository and the recipe that made
the file), ``snapshots/<revision>/weights.safetensors`` and ``provenance.json``. The revision is a 40-character
id computed from the source's repository, commit and file and the recipe, so every tool that names the repository
and revision reads the same file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from poolhouse import files
from poolhouse.hub import hfstore

__all__ = ["FILE", "FORMAT", "OWNER", "RECORD", "Source", "lookup", "place", "repo_id", "revision_of", "save",
           "source_of"]

OWNER = "poolhouse-derived"
FILE = "weights.safetensors"
RECORD = "provenance.json"
FORMAT = "poolhouse-derived/1"


@dataclass(frozen=True, slots=True)
class Source:
    """What a derived file was made from: a repository, its commit, and the file read in it ('' for all of it).
    A folder outside the hub cache is ``local/<name>`` with a fingerprint of its files in place of a commit."""

    repo: str
    revision: str
    file: str = ""


def _slug(text: str) -> str:
    return re.sub(r"\.{2,}", ".", re.sub(r"[^A-Za-z0-9_.]+", "-", text)).strip("-.") or "folder"


def _fingerprint(path: Path) -> str:
    rows = [str(path.resolve())]
    base = path.parent if path.is_file() else path
    for one in sorted(p for p in ([path] if path.is_file() else path.rglob("*")) if p.is_file()):
        stat = one.stat()
        rows.append(f"{one.relative_to(base)}:{stat.st_size}:{stat.st_mtime_ns}")
    return hashlib.sha1("\n".join(rows).encode(), usedforsecurity=False).hexdigest()


def source_of(path: Path) -> Source:
    """The `Source` a file or folder is: its repository and commit when it is in a hub cache snapshot, else a
    local one fingerprinted by the names, sizes and modification times of its files."""
    found = hfstore.identify(path)
    if found:
        return Source(*found)
    return Source(f"local/{_slug(path.name)}", _fingerprint(path))


def repo_id(source: Source, recipe: str) -> str:
    """The derived repository that holds what ``recipe`` made from ``source``'s repository."""
    owner, _, name = source.repo.partition("/")
    return f"{OWNER}/{owner}.{_slug(name)}.{recipe}"


def revision_of(source: Source, recipe: str) -> str:
    """The commit id of what ``recipe`` made from this ``source``."""
    text = "\0".join((FORMAT, source.repo, source.revision, source.file, recipe))
    return hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()


def _folder(source: Source, recipe: str) -> tuple[Path, Path]:
    root = hfstore.repo_root(repo_id(source, recipe))
    return root, root / "snapshots" / revision_of(source, recipe)


def lookup(source: Source, recipe: str) -> Path | None:
    """The derived file for ``source`` and ``recipe`` when it is in the cache and its record agrees, else None."""
    folder = _folder(source, recipe)[1]
    try:
        held = json.loads((folder / RECORD).read_text(encoding="utf-8"))
        size = (folder / FILE).stat().st_size
    except (OSError, ValueError):
        return None
    right = (isinstance(held, dict) and held.get("format") == FORMAT and held.get("recipe") == recipe
             and held.get("source") == asdict(source) and held.get("size") == size)
    return folder / FILE if right else None


def _record(root: Path, folder: Path, record: dict[str, object]) -> None:
    text = json.dumps(record, indent=2, sort_keys=True)
    blob = root / "blobs" / hashlib.sha256(text.encode()).hexdigest()
    if not blob.exists():
        files.write_text(blob, text)
    hfstore.link(folder / RECORD, blob)


def place(source: Source, recipe: str, path: Path, *, origin: str = "") -> Path:
    """Move ``path`` into the cache as what ``recipe`` made from ``source``; returns the cached file.

    The file is linked in, never copied, so ``path`` must be on the cache's volume (`OSError` otherwise); its old
    name is removed once the cached one reads back as the same bytes. The record is written last: a file with
    no record is not found by `lookup`.
    """
    root, folder = _folder(source, recipe)
    name, size = files.sha256_file(path), path.stat().st_size
    with hfstore.Placement(path, root / "blobs" / name, name) as placement:
        got = placement.publish(copy=False)
        if got in ("new", "have"):
            placement.commit()
    if got not in ("new", "have"):
        raise OSError(got)
    target = folder / FILE
    hfstore.link(target, root / "blobs" / name)
    if not target.samefile(path) and not hfstore.same_bytes(target, path):
        raise OSError(f"{target} does not read back as {path}")
    _record(root, folder, {"format": FORMAT, "source": asdict(source), "recipe": recipe, "file": FILE,
                           "size": size, "sha256": name, "origin": origin or "made by poolhouse"})
    hfstore.point_ref(repo_id(source, recipe), "main", folder.name)
    path.unlink()
    return target


def save(source: Source, recipe: str, produce: Callable[[Path], None]) -> Path:
    """Run ``produce`` on a staging path (it ends in ``.safetensors``) and keep what it wrote as the derived file
    of ``source`` and ``recipe``; returns the cached file. A producer that raises leaves nothing behind."""
    root = _folder(source, recipe)[0]
    stage = root / ".staging" / f"{os.getpid()}-{secrets.token_hex(4)}.safetensors"
    stage.parent.mkdir(parents=True, exist_ok=True)
    try:
        produce(stage)
        return place(source, recipe, stage)
    finally:
        stage.unlink(missing_ok=True)
