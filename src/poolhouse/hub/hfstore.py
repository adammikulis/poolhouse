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
import secrets
import stat
from contextlib import ExitStack, suppress
from pathlib import Path

from poolhouse import files, hub
from poolhouse.hub import remote
from poolhouse.safenames import safe_filename, safe_join

COMMIT = re.compile(r"[a-f0-9]{40}")
DIGEST = re.compile(r"[a-f0-9]{40}|[a-f0-9]{64}")

__all__ = [
    "COMMIT",
    "DIGEST",
    "Placement",
    "blob_name",
    "cache_problem",
    "digest_of",
    "git_blob_sha1",
    "identify",
    "link",
    "main_commit",
    "point_ref",
    "repo_root",
    "same_bytes",
    "tidy",
    "volume",
]


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


class Placement:
    """One descriptor-anchored publication and its owned temporary names."""

    def __init__(self, path, blob, name, *, current=None):
        if not DIGEST.fullmatch(name):
            raise ValueError(f"{name!r} is not a digest a blob can be named by")
        self.path, self.blob, self.name, self.current = Path(path).absolute(), Path(blob), name, current
        self.stack = ExitStack()
        self.parts = {}
        self.published = None
        self.complete = False
        self.closed = False
        self.anchors = []
        self.result = None

    def __enter__(self):
        entered = False
        try:
            self.check()
            self.parent = self.blob.parent.absolute()
            if '..' in self.parent.parts:
                raise PermissionError('Model publication cannot traverse parent directories')
            self.directory = os.open(self.parent.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self.stack.callback(os.close, self.directory)
            self.anchors = []
            for part in self.parent.parts[1:]:
                with suppress(FileExistsError):
                    os.mkdir(part, mode=0o700, dir_fd=self.directory)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.directory)
                self.stack.callback(os.close, child)
                self.anchors.append((self.directory, part, child, files.stat_signature(os.fstat(child))[:4]))
                self.directory = child
            info = os.fstat(self.directory)
            if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise PermissionError('The model publication directory must belong to this account')
            self.source = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            self.stack.callback(os.close, self.source)
            self._regular(os.fstat(self.source))
            self.check()
            entered = True
            return self
        finally:
            if not entered:
                self.closed = True
                self.stack.close()

    def __exit__(self, *_error):
        if self.closed:
            return
        self.closed = True
        with ExitStack() as cleanup:
            cleanup.callback(self.stack.close)
            for name, identity in self.parts.items():
                cleanup.callback(self._unlink, name, identity)
            if not self.complete and self.published is not None:
                cleanup.callback(self._unlink, self.blob.name, self.published)

    @staticmethod
    def _regular(info):
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise PermissionError('Model publication requires an owned regular file')

    def check(self):
        if self.closed:
            raise PermissionError("The model publication is closed")
        if self.current is not None and self.current() is not True:
            raise PermissionError('Current access is required to finish the model download')
        for parent, name, child, original in self.anchors:
            if (files.stat_signature(os.fstat(child))[:4] != original
                    or files.stat_signature(os.stat(name, dir_fd=parent, follow_symlinks=False))[:4] != original):
                raise PermissionError('The model publication directory changed')

    def _unlink(self, name, identity):
        try:
            info = os.stat(name, dir_fd=self.directory, follow_symlinks=False)
            if files.stat_signature(info)[:2] == identity:
                os.unlink(name, dir_fd=self.directory)
        except FileNotFoundError:
            pass

    def _hash(self, handle):
        before = files.stat_signature(os.fstat(handle))
        self._regular(os.fstat(handle))
        result = hashlib.sha256() if len(self.name) == 64 else hashlib.sha1(usedforsecurity=False)
        if len(self.name) == 40:
            result.update(b'blob %d\0' % os.fstat(handle).st_size)
        os.lseek(handle, 0, os.SEEK_SET)
        while block := os.read(handle, 1 << 20):
            self.check()
            result.update(block)
        self.check()
        if files.stat_signature(os.fstat(handle)) != before:
            raise PermissionError('Model bytes changed during verification')
        return result.hexdigest()

    def _target(self, identity=None):
        handle = os.open(self.blob.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.directory)
        try:
            original = files.stat_signature(os.fstat(handle))
            if identity is not None and original[:2] != identity:
                raise PermissionError('The model publication target changed')
            answer = self._hash(handle)
            self.check()
            if files.stat_signature(os.stat(self.blob.name, dir_fd=self.directory, follow_symlinks=False)) != original:
                raise PermissionError('The model publication target changed')
            self.target_stamp = original
            self.target_info = os.fstat(handle)
            return answer
        finally:
            os.close(handle)

    def _existing(self):
        return 'have' if self._target() == self.name else f'{self.blob.name[:12]} is in the cache with other bytes; it is not replaced'

    def _copy(self):
        part = self.blob.name + '.' + secrets.token_hex(16) + '.incomplete'
        handle = os.open(part, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.directory)
        self.stack.callback(os.close, handle)
        identity = files.stat_signature(os.fstat(handle))[:2]
        self.parts[part] = identity
        source = files.stat_signature(os.fstat(self.source))
        os.lseek(self.source, 0, os.SEEK_SET)
        with os.fdopen(os.dup(handle), 'wb') as output:
            while block := os.read(self.source, 1 << 20):
                self.check()
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if files.stat_signature(os.fstat(self.source)) != source or self._hash(handle) != self.name:
            raise PermissionError('The staged model bytes changed')
        self.check()
        if files.stat_signature(os.stat(part, dir_fd=self.directory, follow_symlinks=False))[:2] != identity:
            raise PermissionError('The staged model file changed')
        os.link(part, self.blob.name, src_dir_fd=self.directory, dst_dir_fd=self.directory, follow_symlinks=False)
        return identity

    def _publish(self, copy):
        self.check()
        try:
            os.stat(self.blob.name, dir_fd=self.directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            return self._existing()
        if self._hash(self.source) != self.name:
            return f'{self.path.name} did not hash to {self.name[:12]}'
        self.check()
        source = files.stat_signature(os.fstat(self.source))[:2]
        if files.stat_signature(self.path.stat(follow_symlinks=False))[:2] != source:
            raise PermissionError('The source model file changed')
        try:
            os.link(self.path, self.blob.name, dst_dir_fd=self.directory, follow_symlinks=False)
            self.published = source
        except FileExistsError:
            return self._existing()
        except OSError as error:
            if error.errno not in (errno.EXDEV, errno.EPERM, errno.EMLINK, errno.ENOTSUP):
                raise
            if not copy:
                return f'{self.path.name} is on another volume than the cache and is not copied'
            try:
                self.published = self._copy()
            except FileExistsError:
                return self._existing()
        if self._target(self.published) != self.name:
            raise PermissionError('The published model bytes changed')
        self.check()
        os.fsync(self.directory)
        if files.stat_signature(os.stat(self.blob.name, dir_fd=self.directory, follow_symlinks=False)) != self.target_stamp:
            raise PermissionError('The model publication target changed')
        return 'new'


    def publish(self, *, copy: bool = True) -> str:
        """Validate and retain a publication; it remains owned until explicit commit."""
        self.check()
        if self.result is None:
            self.result = self._publish(copy)
        elif self.result in ('new', 'have') and self._target(self.published) != self.name:
            raise PermissionError('The model publication changed')
        return self.result

    def commit(self):
        """Verify original bytes and current access after caller work, then keep them."""
        self.check()
        if self.result not in ('new', 'have'):
            raise ValueError('No verified model publication is ready to commit')
        for name, identity in list(self.parts.items()):
            self._unlink(name, identity)
            del self.parts[name]
        if self._target(self.published) != self.name:
            raise PermissionError('The published model bytes changed')
        self.check()
        os.fsync(self.directory)
        if files.stat_signature(os.stat(self.blob.name, dir_fd=self.directory, follow_symlinks=False)) != self.target_stamp:
            raise PermissionError('The model publication target changed')
        self.complete = True
        return self.target_info


def volume(path: Path) -> Path:
    """The nearest folder of ``path`` that exists, which says what volume a file there would be on."""
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def tidy(root: Path) -> None:
    """Remove the folders under ``root`` that a move emptied, and ``root`` itself when nothing is left in it."""
    for here, _dirs, _names in os.walk(root, topdown=False):
        try:
            Path(here).rmdir()
        except OSError:
            continue


def cache_problem(cache: Path) -> str:
    """Why the hub cache cannot be written, or '': judged off the nearest folder that exists, so a plan
    creates nothing."""
    near = volume(cache)
    if not near.is_dir() or not os.access(near, os.W_OK | os.X_OK):
        return f"the hub cache {cache} cannot be written ({near} is not a writable folder)"
    return ""
