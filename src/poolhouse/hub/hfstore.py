"""The Hugging Face hub cache as poolhouse's one model store.

``hub.hub_cache()`` is the folder (``$HF_HUB_CACHE``, ``$HF_HOME/hub``, ``$XDG_CACHE_HOME/huggingface/hub``
or ``~/.cache/huggingface/hub``). A repository there is laid out the way ``huggingface_hub`` writes it, so
``hf``, transformers and every other tool read what poolhouse downloads and the other way round:

    models--owner--repo/blobs/<sha256 | git sha1>      the bytes, once, named by their digest
    models--owner--repo/snapshots/<commit>/<path>      a relative symlink into blobs/
    models--owner--repo/refs/<revision>                the commit a name such as ``main`` was resolved to

Nothing else keeps models: ``<state>/models`` no longer exists as a store.
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import shutil
from pathlib import Path

from poolhouse import files, hub
from poolhouse.hub import remote
from poolhouse.safenames import safe_filename, safe_join

COMMIT = re.compile(r"[a-f0-9]{40}")
DIGEST = re.compile(r"[a-f0-9]{40}|[a-f0-9]{64}")

__all__ = ["COMMIT", "blob_name", "digest_of", "git_blob_sha1", "identify", "into_blobs", "link", "main_commit",
           "point_ref", "repo_root", "same_bytes"]


def repo_root(repo: str) -> Path:
    """The cache folder of ``owner/name``."""
    owner, _, name = repo.partition("/")
    for part in (owner, name):
        safe_filename(part)
    return hub.hub_cache() / f"models--{owner}--{name}"


def git_blob_sha1(path: Path, size: int) -> str:
    """The git object id of a file of ``size`` bytes: what the Hub calls a small file's oid."""
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(f"blob {size}\0".encode())
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def blob_name(one: remote.RemoteFile) -> str:
    """What the cache calls ``one``'s bytes: its LFS sha256, else its git blob id. Refuses a file
    the Hub listed with neither, since a name that is not a digest could not be checked."""
    name = one.sha256 or one.oid
    if not DIGEST.fullmatch(name):
        raise OSError(f"the Hub listed no digest for {one.path}; it cannot be placed in the Hub cache")
    return name


def link(target: Path, blob: Path) -> None:
    """Make the snapshot file ``target`` a relative symlink to ``blob``. A link already right stays,
    a real file there (one ``poolhouse-models snapshot`` wrote) is left, a wrong link is replaced."""
    target.parent.mkdir(parents=True, exist_ok=True)
    want = os.path.relpath(blob, target.parent)
    if target.is_symlink():
        if target.readlink().as_posix() == want:
            return
    elif target.exists():
        return
    staged = target.with_name(target.name + ".linking")
    staged.unlink(missing_ok=True)
    try:
        staged.symlink_to(want)
    except OSError:
        staged.hardlink_to(blob)
    files.promote(staged, target)


def main_commit(repo: str) -> str:
    """The commit ``refs/main`` of ``repo`` names, or '' when there is none or the file holds anything
    but a commit id: the cache is a folder another tool writes, and what is read from it is not trusted."""
    try:
        text = (repo_root(repo) / "refs" / "main").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return text if COMMIT.fullmatch(text) else ""


def point_ref(repo: str, revision: str, commit: str) -> None:
    """Record that ``revision`` meant ``commit``, as the cache does for a branch or tag name."""
    if COMMIT.fullmatch(revision):
        return
    for part in revision.split("/"):
        safe_filename(part)
    files.write_text(safe_join(repo_root(repo), f"refs/{revision}"), commit)


def identify(path: Path | str) -> tuple[str, str, str] | None:
    """``(repo, commit, file)`` for a path inside ``models--owner--name/snapshots/<commit>/``, with ``file``
    empty for the snapshot folder itself; None for any other path."""
    parts = Path(path).parts
    for at, part in enumerate(parts[:-2]):
        owner, _, name = part[len("models--"):].partition("--")
        if part.startswith("models--") and owner and name and parts[at + 1] == "snapshots" \
                and COMMIT.fullmatch(parts[at + 2]):
            return f"{owner}/{name}", parts[at + 2], "/".join(parts[at + 3:])
    return None


def same_bytes(one: Path, other: Path, *, chunk: int = 1 << 22) -> bool:
    """Whether two files hold the same bytes, read through any link."""
    if one.stat().st_size != other.stat().st_size:
        return False
    with one.open("rb") as left, other.open("rb") as right:
        while block := left.read(chunk):
            if block != right.read(len(block)):
                return False
    return True


def digest_of(path: Path, name: str) -> str:
    """``path``'s digest in the form ``name`` is written in: sha256 for an LFS file, else the object id."""
    return files.sha256_file(path) if len(name) == 64 else git_blob_sha1(path, path.stat().st_size)


def _existing(blob: Path, path: Path, name: str) -> str:
    """``'have'`` when the blob already in the cache is these bytes, else why it is not."""
    if blob.stat().st_size == path.stat().st_size and digest_of(blob, name) == name:
        return "have"
    return f"{blob.name[:12]} is in the cache with other bytes; it is not replaced"


def into_blobs(path: Path, blob: Path, name: str, *, copy: bool = True) -> str:
    """Put ``path``'s bytes at ``blob``, never replacing one: ``'new'``, ``'have'`` when a blob of that name
    was already there with the same bytes, or the reason it could not be. A link is made when ``path`` is on
    the cache's volume; otherwise the bytes are copied, or refused when ``copy`` is false."""
    if not DIGEST.fullmatch(name):
        raise ValueError(f"{name!r} is not a digest a blob can be named by")
    blob.parent.mkdir(parents=True, exist_ok=True)
    if blob.exists():
        return _existing(blob, path, name)
    try:
        os.link(path, blob)
        return "new"
    except FileExistsError:
        return _existing(blob, path, name)
    except OSError as exc:
        if exc.errno not in (errno.EXDEV, errno.EPERM, errno.EMLINK, errno.ENOTSUP):
            raise
    if not copy:
        return f"{path.name} is on another volume than the cache and is not copied"
    staged = blob.with_name(f"{blob.name}.{os.getpid()}.incomplete")
    try:
        shutil.copyfile(path, staged)
        if digest_of(staged, name) != name:
            return f"the copy of {path.name} did not hash to {name[:12]}"
        try:
            os.link(staged, blob)
        except FileExistsError:
            return _existing(blob, path, name)
        return "new"
    finally:
        staged.unlink(missing_ok=True)
