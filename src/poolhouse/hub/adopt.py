"""Taking a folder of a Hub repository's files into the hub cache.

A `Folder` holds the files of ``repo`` at ``revision`` somewhere else on the volume. Each file is vouched for
before it is touched: by the Hub's own listing when the Hub answers, else by the record its download left
(`net.provenance`), else, for a weights file, by its structure. A vouched file is linked into ``blobs/`` under
its digest and into ``snapshots/<revision>/``; nothing is copied. A file the cache already holds is compared
byte for byte before its twin is removed, and a file nothing vouches for stays where it is.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from poolhouse import files
from poolhouse.hub import hfstore, modelfile, remote
from poolhouse.net import provenance, sniff

__all__ = ["Folder", "Listing", "Row", "listing", "structure_problem", "take"]

Listing = dict[str, remote.RemoteFile]
PARTIAL = (".part", ".incomplete", ".linking")
WEIGHTS = (".safetensors", ".gguf")
INDEX = "model.safetensors.index.json"
SIDECAR = ".provenance.json"


@dataclass(frozen=True, slots=True)
class Folder:
    """The files of ``repo`` at ``revision`` in ``path``. With ``keep`` a file's name stays in the folder (a view
    whose files are links); otherwise its name is removed once the cache holds it, and a file that cannot be
    vouched for is ``blocked`` rather than ``derived``."""

    repo: str
    revision: str
    path: Path
    keep: bool = False


@dataclass(frozen=True, slots=True)
class Row:
    """What happens to one file: ``link`` (a new blob), ``have`` (the cache holds these bytes; this name is the
    duplicate), ``adopted`` (already linked), ``derived`` (not the Hub's bytes; kept) or ``blocked``."""

    rel: str
    path: str
    size: int
    action: str
    how: str = ""
    why: str = ""

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def listing(folder: Folder) -> tuple[Listing | None, str]:
    """The Hub's files of the folder's repository at its revision, and why they could not be had ('' when they were)."""
    try:
        return {f.path: f for f in remote.listing(folder.repo, folder.revision)}, ""
    except (remote.RemoteError, OSError, ValueError) as exc:
        return None, f"{folder.repo} could not be read from {remote.endpoint()} ({exc})"


def _missing(path: Path, rel: str, index: Path) -> list[str]:
    try:
        weight_map = json.loads(index.read_text(encoding="utf-8")).get("weight_map", {})
        header = modelfile.safetensors_header(path)
    except (OSError, ValueError, modelfile.NotAModelFile):
        return []
    return [name for name, shard in weight_map.items() if shard == rel and name not in header]


def structure_problem(path: Path, rel: str = "", index: Path | None = None) -> str:
    """Why ``path`` is not a whole weights file, or ''. Its tensors (or GGUF header) must read as a file of that
    kind and fit inside it, and every tensor ``index`` maps to ``rel`` must be in it."""
    found = sniff.sniff(path)
    if not found.ok:
        return found.problems[0] if found.problems else "does not read as a whole file"
    missing = _missing(path, rel, index) if index else []
    return f"lacks {len(missing)} tensors its index maps to it, such as {missing[0]}" if missing else ""


def _record(path: Path) -> dict | None:
    try:
        held = json.loads(provenance.sidecar(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return held if isinstance(held, dict) else None


def _by_hub(path: Path, size: int, one: remote.RemoteFile) -> tuple[str, str]:
    if size != one.size:
        return "", f"is {size} bytes, the Hub lists {one.size}"
    try:
        name = hfstore.blob_name(one)
    except OSError as exc:
        return "", str(exc)
    if hfstore.digest_of(path, name) != name:
        return "", "differs from the digest the Hub lists"
    return name, "the Hub's listing"


def _by_record(path: Path, size: int, held: dict) -> tuple[str, str]:
    digest = str(held.get("sha256", ""))
    if held.get("outcome") != "kept" or held.get("size") != size or digest != held.get("expected_sha256"):
        return "", "its download record does not vouch for it"
    if files.sha256_file(path) != digest:
        return "", "differs from the digest its download record names"
    etag = str((held.get("headers") or {}).get("etag", "")).removeprefix("W/").strip('"')
    if len(etag) == 40 and hfstore.DIGEST.fullmatch(etag):
        same = hfstore.git_blob_sha1(path, size) == etag
        return (etag, "its download record") if same else ("", "differs from the object id its download record names")
    return digest, "its download record"


def _vouch(rel: str, path: Path, size: int, listed: Listing | None, folder: Folder) -> tuple[str, str]:
    """``(blob name, what vouches)`` for a file, or ``('', why nothing does)``."""
    if listed is not None:
        one = listed.get(rel)
        return _by_hub(path, size, one) if one else ("", f"is not a file of {folder.repo} at {folder.revision[:8]}")
    held = _record(path)
    if held is not None:
        return _by_record(path, size, held)
    if not rel.endswith(WEIGHTS):
        return "", "cannot be checked without the Hub"
    problem = structure_problem(path, rel, folder.path / INDEX)
    return ("", problem) if problem else (files.sha256_file(path), "its structure, not the Hub's listing")


def _blob_of(target: Path) -> Path:
    """The ``blobs/`` entry a snapshot file is a link to, or the file itself when it is not a link."""
    return Path(os.path.normpath(target.parent / target.readlink())) if target.is_symlink() else target


def _take_name(path: Path, target: Path) -> None:
    """Remove ``path``, a name of what ``target`` holds; its download record goes beside the blob unless the blob
    has one."""
    note, kept = provenance.sidecar(path), provenance.sidecar(_blob_of(target))
    if note.is_file() and not kept.exists():
        files.promote(note, kept)
    path.unlink()


def _blocked(row: Row, why: str) -> Row:
    return Row(row.rel, row.path, row.size, "blocked", row.how, why)


def _place(row: Row, name: str, folder: Folder, apply: bool) -> Row:
    """Decide, and when ``apply`` do, the cache side of one vouched file."""
    root = hfstore.repo_root(folder.repo)
    path, blob, target = Path(row.path), root / "blobs" / name, root / "snapshots" / folder.revision / row.rel
    twin = target if target.exists() else blob if blob.exists() else None
    if twin is not None and not hfstore.same_bytes(twin, path):
        return _blocked(row, f"{twin} holds other bytes; it is not replaced")
    action = "have" if twin is not None else "link"
    if not apply:
        return Row(row.rel, row.path, row.size, action, row.how)
    if twin is not target:
        got = hfstore.into_blobs(path, blob, name, copy=False)
        if got not in ("new", "have"):
            return _blocked(row, got)
        hfstore.link(target, blob)
    if not (target.is_file() and (target.samefile(path) or hfstore.same_bytes(target, path))):
        return _blocked(row, f"{target} does not read back as {path.name}")
    if not folder.keep:
        _take_name(path, target)
    return Row(row.rel, row.path, row.size, action, row.how)


def _one(rel: str, path: Path, listed: Listing | None, folder: Folder, apply: bool) -> Row:
    here, root = os.fspath(path), hfstore.repo_root(folder.repo)
    if path.is_symlink() or rel.endswith(PARTIAL):
        return Row(rel, here, 0, "blocked", why="is not a finished file")
    size = path.stat().st_size
    if (twin := root / "snapshots" / folder.revision / rel).exists() and twin.samefile(path):
        if apply and not folder.keep:
            _take_name(path, twin)
        return Row(rel, here, size, "adopted" if folder.keep else "have", "the same file as the cache's")
    if path.stat().st_dev != hfstore.volume(root).stat().st_dev:
        return Row(rel, here, size, "blocked", why="is on another volume than the hub cache and is not copied")
    name, how = _vouch(rel, path, size, listed, folder)
    if not name:
        return Row(rel, here, size, "derived" if folder.keep else "blocked", why=how)
    return _place(Row(rel, here, size, "", how), name, folder, apply)


def take(folder: Folder, listed: Listing | None, *, apply: bool,
         on_row: Callable[[Row], None] | None = None) -> list[Row]:
    """The fate of every file in ``folder``; with ``apply`` the vouched ones are linked into the cache and, unless
    the folder keeps its names, removed from it. Without ``apply`` nothing changes. ``listed`` is the Hub's
    listing (`listing`), or None to vouch by download record and structure."""
    rows: list[Row] = []
    for here, dirs, names in os.walk(folder.path):
        dirs.sort()
        for name in sorted(n for n in names if not n.endswith(SIDECAR)):
            path = Path(here) / name
            rows.append(_one(path.relative_to(folder.path).as_posix(), path, listed, folder, apply))
            if on_row:
                on_row(rows[-1])
    if apply and not folder.keep:
        hfstore.tidy(folder.path)
    return rows
