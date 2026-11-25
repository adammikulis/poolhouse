"""Bounded source inventory and raw Git tree digests."""
from __future__ import annotations

import ctypes
import hashlib
import ntpath
import os
import posixpath
import selectors
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from poolhouse import home

if os.name == "posix":
    import grp
from poolhouse.platform import start_process, terminate_process_group

OUTPUT_LIMIT = 8 * 1024 * 1024
INDEX_LIMIT = 32 * 1024 * 1024
FILE_LIMIT = 16 * 1024 * 1024
TOTAL_LIMIT = 256 * 1024 * 1024
"""The ceilings of a bounded snapshot (the test kernel and delegation take one); a coding workspace takes none (`SourceSnapshot(strict=False)`)."""
ZEROS = bytes(65536)
"""A block of zeros a capture does not write, so a sparse file stays sparse in its copy."""
STALL_S = 30.0
"""Seconds a capture may go without a file visited or a byte read before it is judged stalled. No recorded reason: it is the 30 s deadline the
capture used to have as a total, kept as a stretch with no progress, so a slow disk is not a failure and a stalled one is (docs/design-isolated-launcher.md, W4)."""
GIT_ROOTS = (Path("/opt/homebrew"), Path("/usr/local"))
APPLE_GIT = Path("/Library/Developer/CommandLineTools/usr/bin/git")
MACH_O = (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
          b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca")


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_uid, info.st_mode, info.st_nlink)


def _optional_identity(path: Path) -> tuple[int, ...] | None:
    try:
        return _identity(path.lstat())
    except FileNotFoundError:
        return None


def _optional_directory(path: Path) -> tuple[int, int] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    return info.st_dev, info.st_ino


@dataclass(frozen=True)
class CaptureEntry:
    path: bytes
    mode: bytes
    sha256: bytes
    size: int


@dataclass(frozen=True)
class Capture:
    """A frozen copy of a working tree. ``left_out`` names each path the capture could not carry and why (a submodule, a link that leaves the
    tree, a special file, a hard-linked or foreign-owned file) and ``notes`` each file it carried with a change (group and world write bits
    cleared); no path is left out or changed without a name here."""

    directory: Path
    entries: tuple[CaptureEntry, ...]
    missing: tuple[bytes, ...]
    excluded: tuple[bytes, ...]
    tree_hash: str
    left_out: tuple[tuple[bytes, str], ...] = ()
    notes: tuple[tuple[bytes, str], ...] = ()


def validate_storage(base: Path, protected: tuple[Path, ...]) -> Path:
    base = base.resolve(strict=True)
    identities = {(info.st_dev, info.st_ino) for root in protected if root.exists() for info in (root.stat(),)}
    if any(base.is_relative_to(root.resolve()) or root.resolve().is_relative_to(base) for root in protected):
        raise RuntimeError("source snapshot: storage overlaps protected roots")
    for parent in (base, *base.parents):
        info = parent.stat()
        sticky = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        if ((info.st_dev, info.st_ino) in identities or info.st_uid not in (0, os.getuid())
                or (info.st_mode & 0o022 and not sticky)):
            raise RuntimeError("source snapshot: storage ancestry is foreign, writable or protected")
    return base


def protected_roots(environment: dict[str, str]) -> tuple[Path, ...]:
    roots = {root.resolve() for root in home.account_roots()}
    roots.update(home.expand(environment[key]).resolve()
                 for key in ("POOLHOUSE_HOME", "POOLHOUSE_CACHE") if environment.get(key))
    return tuple(sorted(roots))


def private_namespace(environment: dict[str, str], prefix: str) -> Path:
    base = validate_storage(Path(tempfile.gettempdir()), protected_roots(environment))
    directory = Path(tempfile.mkdtemp(prefix=prefix, dir=base)).resolve()
    directory.chmod(0o700)
    return directory


def git_environment() -> dict[str, str]:
    env = {"PATH": os.defpath, "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
           "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": ""}
    if sys.platform == "win32":
        windows = _windows_directory()
        env.update(SYSTEMROOT=windows, WINDIR=windows, PATH=ntpath.join(windows, "System32") + ";" + windows)
    return env


def _windows_directory() -> str:
    """Obtain the Windows system directory from the OS, not the inherited environment."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel.GetWindowsDirectoryW
    function.argtypes = [ctypes.POINTER(ctypes.c_wchar), ctypes.c_uint]
    function.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    length = function(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise OSError(ctypes.get_last_error(), "could not determine the Windows directory")
    value = buffer.value
    if not ntpath.isabs(value) or not ntpath.splitdrive(value)[0]:
        raise ValueError("invalid Windows directory")
    return value


def _git_package(alias: Path, root: Path) -> Path:
    package = root / "opt/git"
    before = package.lstat()
    if not stat.S_ISLNK(before.st_mode) or before.st_uid not in (0, os.getuid()):
        raise RuntimeError("source snapshot: Git package alias is not owned")
    target = package.readlink()
    if len(str(target)) > 4096:
        raise RuntimeError("source snapshot: Git package alias exceeds bound")
    if target.parent not in (Path("../Cellar/git"), root / "Cellar/git"):
        raise RuntimeError("source snapshot: Git package alias has uncontrolled redirects")
    direct = root / "Cellar/git" / target.name
    if direct.resolve(strict=True) != direct:
        raise RuntimeError("source snapshot: Git package alias has uncontrolled redirects")
    resolved = direct / "bin/git"
    if resolved.resolve(strict=True) != resolved:
        raise RuntimeError("source snapshot: Git image has uncontrolled redirects")
    after = package.lstat()
    identities = {(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_uid, info.st_mode)
                  for info in (before, after)}
    if len(identities) != 1 or package.readlink() != target or alias.resolve(strict=True) != resolved:
        raise RuntimeError("source snapshot: Git package alias changed during verification")
    return resolved


def _git_image(alias: Path, root: Path) -> Path:
    resolved = _git_package(alias, root)
    admin = grp.getgrnam("admin").gr_gid
    for parent in {*resolved.parents, *alias.parents}:
        info = parent.stat()
        entry = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or (stat.S_ISLNK(entry.st_mode) and parent != root / "opt/git"):
            raise RuntimeError("source snapshot: Git ancestry is not a direct directory")
        index = parent in (root / "opt", root / "Cellar") and info.st_gid == admin
        sticky = parent == Path("/private/tmp") and info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        if (info.st_uid not in (0, os.getuid()) or (info.st_mode & 0o002 and not sticky)
                or (info.st_mode & 0o020 and not index and not sticky)):
            raise RuntimeError("source snapshot: Git ancestry is foreign or writable")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(resolved, flags)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid not in (0, os.getuid())
                or before.st_mode & 0o022 or not before.st_mode & 0o111 or before.st_nlink != 1):
            raise RuntimeError("source snapshot: Git image is not owned and protected")
        if stream.read(4) not in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
                                  b"\xfe\xed\xfa\xce", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
                                  b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"):
            raise RuntimeError("source snapshot: Git image is not Mach-O")
        identities = {(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                       info.st_uid, info.st_mode, info.st_nlink)
                      for info in (before, os.fstat(stream.fileno()), resolved.stat())}
        if len(identities) != 1 or alias.resolve(strict=True) != resolved:
            raise RuntimeError("source snapshot: Git image changed during verification")
    return resolved


def _apple_git(image: Path) -> Path:
    """The git of Apple's command-line developer tools: a root-owned Mach-O in directories only root writes, reached with no link in its path.
    The `/usr/bin/git` selector shim is never this file and is never executed."""
    if image.resolve(strict=True) != image:
        raise RuntimeError("source snapshot: Apple's Git is reached through a link")
    for parent in image.parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError("source snapshot: Apple's Git ancestry is not owned by root and closed to writers")
    descriptor = os.open(image, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_mode & 0o022 or not before.st_mode & 0o111
                or before.st_nlink != 1 or stream.read(4) not in MACH_O):
            raise RuntimeError("source snapshot: Apple's Git is not a root-owned Mach-O program")
        if _identity(before) != _identity(os.fstat(stream.fileno())) or _identity(image.lstat()) != _identity(before):
            raise RuntimeError("source snapshot: Apple's Git changed during verification")
    return image


def actual_git() -> Path:
    """Resolve fixed owned Git assets; macOS runs a Homebrew git or the one of Apple's command-line developer tools, never the Apple selector shim."""
    if sys.platform != "darwin":
        program = shutil.which("git", path=os.defpath)
        if program is None:
            raise RuntimeError("source snapshot: Git unavailable")
        return Path(program).resolve(strict=True)
    for root in GIT_ROOTS:
        alias = root / "opt" / "git" / "bin" / "git"
        if alias.is_symlink() or alias.exists():
            return _git_image(alias, root)
    if APPLE_GIT.exists():
        return _apple_git(APPLE_GIT)
    raise RuntimeError("source snapshot: owned Git unavailable; Apple shim refused")


def git_output(args: list[str], *, cwd: Path, seconds: float = 15) -> bytes:
    """Git's output for ``args``; ``seconds`` is how long it may go without writing a byte, not a total."""
    program = actual_git()
    if seconds <= 0:
        raise RuntimeError("source snapshot: Git or deadline unavailable")
    command = [str(program), "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull, *args]
    process = start_process(command, cwd=cwd, env=git_environment(), close_fds=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + seconds
    value = bytearray()
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not ready.select(remaining):
                    raise TimeoutError("source snapshot: Git inventory deadline exceeded")
                block = os.read(process.stdout.fileno(), 65536)
                if not block:
                    break
                value.extend(block)
                deadline = time.monotonic() + seconds
                if len(value) > OUTPUT_LIMIT:
                    raise RuntimeError("source snapshot: Git inventory output exceeds bound")
        if process.wait(timeout=max(.001, deadline - time.monotonic())):
            raise RuntimeError("source snapshot: Git inventory failed")
        return bytes(value)
    finally:
        process.stdout.close()
        if process.poll() is None:
            terminate_process_group(process, force=True)
            process.wait(timeout=5)


def metadata_directory(root: Path) -> Path:
    marker = root / ".git"
    info = marker.lstat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise RuntimeError("source snapshot: repository metadata is foreign or writable")
    if stat.S_ISDIR(info.st_mode):
        return marker
    if not stat.S_ISREG(info.st_mode) or info.st_size > 4096 or info.st_nlink != 1:
        raise RuntimeError("source snapshot: invalid worktree pointer")
    fd = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        pinned = os.fstat(stream.fileno())
        if (pinned.st_dev, pinned.st_ino, pinned.st_size, pinned.st_mtime_ns) != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns):
            raise RuntimeError("source snapshot: worktree pointer changed")
        text = stream.read(4097).decode().strip()
    if not text.startswith("gitdir: ") or "\n" in text:
        raise RuntimeError("source snapshot: malformed worktree pointer")
    directory = (root / text[8:]).resolve(strict=True)
    validate_storage(directory, protected_roots(dict(os.environ)))
    info = directory.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise RuntimeError("source snapshot: Git index directory is foreign or writable")
    return directory


class SourceSnapshot:
    """Inventory a worktree using private Git metadata without configured filters."""

    def __init__(self, root: Path, storage: Path, *, known_paths: tuple[bytes, ...] = (), strict: bool = True):
        self.root = validate_storage(root, protected_roots(dict(os.environ)))
        storage = validate_storage(storage, ())
        owner = storage.stat()
        if owner.st_uid != os.getuid() or owner.st_mode & 0o077:
            raise RuntimeError("source snapshot: private owned storage required")
        self.strict = strict
        self.deadline = time.monotonic() + 30
        self.total = 0
        self.moved, self.files, self.bytes = time.monotonic(), 0, 0
        self.parents: dict[Path, tuple[int, int] | None] = {}
        self.chain: list[tuple[bytes, int]] = []
        self.directory = Path(tempfile.mkdtemp(prefix="source-git-", dir=storage))
        with ExitStack() as cleanup:
            cleanup.callback(self.close)
            self.prepare()
            self._retain_paths(known_paths)
            cleanup.pop_all()

    def prepare(self) -> None:
        for name in ("objects", "refs"):
            (self.directory / name).mkdir(mode=0o700)
        (self.directory / "HEAD").write_text("ref: refs/heads/source\n")
        (self.directory / "config").write_text("[core]\n repositoryformatversion = 0\n bare = false\n")
        index = metadata_directory(self.root) / "index"
        self.index = index
        self.index_identity = _optional_identity(index)
        if self.index_identity is not None:
            fd = os.open(index, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or info.st_mode & 0o022 or info.st_nlink != 1 or info.st_size > INDEX_LIMIT):
                    raise RuntimeError("source snapshot: invalid bounded owned index")
                value = stream.read(INDEX_LIMIT + 1)
                final = os.fstat(stream.fileno())
                if (final.st_size, final.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
                    raise RuntimeError("source snapshot: index changed during read")
            if (len(value) > INDEX_LIMIT or len(value) < 12 or value[:4] != b"DIRC"
                    or int.from_bytes(value[4:8], "big") not in (2, 3, 4)):
                raise RuntimeError("source snapshot: malformed or oversized index")
            (self.directory / "index").write_bytes(value)
        self.entries = {}
        for row in self.command(["ls-files", "--stage", "-z"]).split(b"\0"):
            if not row:
                continue
            header, name = row.split(b"\t", 1)
            mode, digest, stage = header.split()
            if stage != b"0" or mode not in (b"100644", b"100755", b"120000", b"160000") or len(digest) != 40:
                raise RuntimeError("source snapshot: ambiguous staged source")
            if name in self.entries:
                raise RuntimeError("source snapshot: duplicate staged source")
            self.path(name)
            self.entries[name] = mode, bytes.fromhex(digest.decode())
        self.tracked = tuple(self.entries)
        self.untracked = self.command(["ls-files", "--others", "--exclude-standard", "-z"])
        self.specials = () if self.strict else self._specials()
        for name in self.untracked.split(b"\0"):
            if name:
                self.path(name)
                self.entries.setdefault(name, (b"100644", b""))

    def _specials(self) -> tuple[bytes, ...]:
        """The FIFOs, sockets and device nodes Git never lists, found by walking every directory but `.git` and the ones Git ignores, so
        that none is left out of a capture without a name."""
        listed = self.command(["ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z"])
        ignored = {name.rstrip(b"/") for name in listed.split(b"\0") if name.endswith(b"/")}
        found, pending = [], [(self.root, b"")]
        while pending:
            here, prefix = pending.pop()
            self.advance(1)
            with os.scandir(here) as scan:
                for entry in scan:
                    name = prefix + os.fsencode(entry.name)
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name != ".git" and name not in ignored:
                            pending.append((Path(entry.path), name + b"/"))
                    elif not entry.is_file(follow_symlinks=False) and not entry.is_symlink():
                        found.append(name)
        return tuple(sorted(found))

    def _retain_paths(self, names: tuple[bytes, ...]) -> None:
        if (type(names) is not tuple or any(type(name) is not bytes for name in names)
                or sum(len(name) + 1 for name in names) > OUTPUT_LIMIT
                or names != tuple(sorted(set(names)))):
            raise RuntimeError("source snapshot: retained paths must be bounded canonical byte names")
        if not names:
            return
        folded = {unicodedata.normalize("NFD", os.fsdecode(name)).casefold(): name for name in self.entries}
        for name in names:
            if b"\0" in name or any(part.lower() == b".git" for part in name.split(b"/")):
                raise RuntimeError("source snapshot: retained path is not source content")
            self.path(name)
            key = unicodedata.normalize("NFD", os.fsdecode(name)).casefold()
            if key in folded and folded[key] != name:
                raise RuntimeError("source snapshot: retained path has a case or normalization alias")
            folded[key] = name
            self.entries.setdefault(name, (b"100644", b""))
        self.tracked = tuple(sorted(set(self.tracked).union(names)))

    def command(self, args: list[str]) -> bytes:
        return git_output(["--git-dir=" + str(self.directory), "--work-tree=" + str(self.root), *args], cwd=self.root, seconds=STALL_S)

    def advance(self, files: int = 0, size: int = 0) -> None:
        """Count progress: an unbounded capture is judged stalled when none has been made for `STALL_S`, never by how long it takes in all (a
        bounded one keeps its total deadline)."""
        now = time.monotonic()
        if self.strict and now >= self.deadline:
            raise TimeoutError("source snapshot: capture deadline exceeded")
        if now - self.moved > STALL_S:
            raise TimeoutError("source snapshot: no file visited and no byte read for the stall limit")
        self.moved, self.files, self.bytes = now, self.files + files, self.bytes + size

    def path(self, name: bytes) -> Path:
        """The path of ``name`` under the root. Each distinct parent directory is validated once per snapshot and its identity kept, so a
        directory replaced while the capture runs is found by `_verify_capture` and not paid for again at every file."""
        parts = name.split(b"/")
        if not name or any(part in (b"", b".", b"..") for part in parts):
            raise RuntimeError("source snapshot: invalid source pathname")
        path = self.root / os.fsdecode(name)
        if path.parent not in self.parents:
            self._check_directories(path.parent)
        return path

    def _check_directories(self, parent: Path) -> None:
        """Validate the directories between the root (checked once, with everything above it, when the snapshot began) and ``parent``, each one
        the first time it is met: a real directory, no link, owned by this account or root and closed to other writers (a sticky directory
        of root's excepted), as `validate_storage` asks of every ancestor."""
        unseen = []
        for each in (parent, *parent.parents):
            if each == self.root:
                break
            if each in self.parents:
                break
            unseen.append(each)
        else:
            raise RuntimeError("source snapshot: redirected source parent")
        for each in reversed(unseen):
            try:
                info = each.lstat()
            except FileNotFoundError:
                self.parents[each] = None
                continue
            sticky = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid()) or (info.st_mode & 0o022 and not sticky)):
                raise RuntimeError("source snapshot: redirected source parent or storage ancestry is foreign or writable")
            self.parents[each] = info.st_dev, info.st_ino

    def blob(self, path: Path, info, target: BinaryIO | None = None) -> tuple[bytes, bytes]:
        if self.strict:
            if info.st_size > FILE_LIMIT or self.total + info.st_size > TOTAL_LIMIT:
                raise RuntimeError("source snapshot: source bytes exceed bounds")
            self.total += info.st_size
        digest = hashlib.sha1(f"blob {info.st_size}\0".encode(), usedforsecurity=False)
        content = hashlib.sha256()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            initial = os.fstat(stream.fileno())
            if _identity(initial) != _identity(info):
                raise RuntimeError("source snapshot: source changed before read")
            read = 0
            for block in iter(lambda: stream.read(65536), b""):
                read += len(block)
                if read > info.st_size:
                    raise RuntimeError("source snapshot: source grew during read")
                self.advance(size=len(block))
                digest.update(block)
                content.update(block)
                if target is not None:
                    if block == ZEROS[:len(block)]:
                        target.seek(len(block), os.SEEK_CUR)
                    else:
                        target.write(block)
            if target is not None:
                target.truncate(read)
            final = os.fstat(stream.fileno())
            if (read != info.st_size or _identity(final) != _identity(initial)
                    or _identity(path.lstat()) != _identity(initial)):
                raise RuntimeError("source snapshot: source changed during read")
        return digest.digest(), content.digest()

    def tree_hash(self) -> str:
        tree = {}
        for name, (mode, digest) in self.entries.items():
            self.advance(1)
            path = self.path(name)
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if info.st_uid != os.getuid() or (info.st_nlink != 1 and not stat.S_ISDIR(info.st_mode)):
                raise RuntimeError("source snapshot: foreign or hardlinked source")
            if stat.S_ISLNK(info.st_mode):
                value = os.fsencode(path.readlink())
                digest = hashlib.sha1(f"blob {len(value)}\0".encode() + value, usedforsecurity=False).digest()
                mode = b"120000"
            elif stat.S_ISREG(info.st_mode):
                if info.st_mode & 0o022:
                    raise RuntimeError("source snapshot: writable source file")
                digest, _ = self.blob(path, info)
                mode = b"100755" if info.st_mode & 0o111 else b"100644"
            elif mode != b"160000" or not stat.S_ISDIR(info.st_mode):
                raise RuntimeError("source snapshot: unsupported source node")
            cursor = tree
            parts = name.split(b"/")
            for part in parts[:-1]:
                cursor = cursor.setdefault(part, {})
            cursor[parts[-1]] = mode, digest
        return tree_digest(tree).hex()

    def capture(self, target: Path, *, excluded: tuple[bytes, ...] = ()) -> Capture:
        """Copy regular source files into a new private directory and freeze their manifest."""
        excluded = tuple(sorted(set(excluded)))
        parent = validate_storage(target.parent, protected_roots(dict(os.environ)))
        if parent != target.parent or target.name in ("", ".", ".."):
            raise RuntimeError("source snapshot: capture target must be canonical")
        for root in (self.root, self.directory):
            if target.is_relative_to(root) or root.is_relative_to(target):
                raise RuntimeError("source snapshot: capture overlaps source or metadata")
        for name in excluded:
            self.path(name)
        target.mkdir(mode=0o700)
        created = target.lstat()

        def remove() -> None:
            if target.exists() and (target.lstat().st_dev, target.lstat().st_ino) == (created.st_dev, created.st_ino):
                shutil.rmtree(target)

        with ExitStack() as cleanup:
            cleanup.callback(remove)
            root_state = _identity(self.root.lstat())
            inventory = tuple(self.entries.items())
            tracked = self.tracked
            self.total = 0
            self.moved = time.monotonic()
            with ExitStack() as stack:
                descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                stack.callback(os.close, descriptor)
                stack.callback(self._release)
                if _identity(os.fstat(descriptor)) != _identity(created):
                    raise RuntimeError("source snapshot: capture target changed")
                result = self._capture_files(descriptor, excluded)
                self._verify_capture(root_state, inventory, tracked, result[3])
                self._verify_output(descriptor, result[0])
                if _identity(target.lstat()) != _identity(os.fstat(descriptor)):
                    raise RuntimeError("source snapshot: capture target changed")
            captured = Capture(target, tuple(result[0]), tuple(result[1]), excluded, tree_digest(result[2]).hex(),
                               tuple(result[4]), tuple(result[5]))
            cleanup.pop_all()
            return captured

    def _left_out(self, name: bytes, mode: bytes, info: os.stat_result, path: Path) -> str:
        """Why a path cannot be carried into a capture, which lists it by name and says so, or empty when it can."""
        if mode == b"160000" or stat.S_ISDIR(info.st_mode):
            return "a submodule"
        if stat.S_ISLNK(info.st_mode):
            return "" if self._inside(name, str(path.readlink())) else "a link that leaves the tree"
        if not stat.S_ISREG(info.st_mode):
            return "a special file (a FIFO, a socket or a device)"
        if info.st_uid != os.getuid():
            return "a file owned by another account"
        return "a file with more than one link" if info.st_nlink != 1 else ""

    @staticmethod
    def _inside(name: bytes, target: str) -> bool:
        """Whether a link at ``name`` to ``target`` is relative and stays in the tree and out of the Git metadata."""
        if not target or "\0" in target or Path(target).is_absolute():
            return False
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(os.fsdecode(name)), target))
        return not (joined == ".." or joined.startswith("../") or any(part.lower() == ".git" for part in joined.split("/")))

    def _capture_files(self, descriptor: int, excluded: tuple[bytes, ...]) -> tuple:
        entries, missing, tree, states, left, notes = [], [], {}, {}, [], []
        for name, (mode, _) in sorted(self.entries.items()):
            self.advance(1)
            if any(part.lower() == b".git" for part in name.split(b"/")):
                raise RuntimeError("source snapshot: Git metadata is not source content")
            if any(name == prefix or name.startswith(prefix + b"/") for prefix in excluded):
                continue
            path = self.path(name)
            try:
                info = path.lstat()
            except FileNotFoundError:
                if name not in self.tracked:
                    raise RuntimeError("source snapshot: untracked source disappeared") from None
                missing.append(name)
                states[name] = None
                continue
            states[name] = _identity(info)
            if self.strict and (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                                or info.st_mode & 0o022 or mode == b"160000"):
                raise RuntimeError("source snapshot: capture requires owned protected regular files")
            if why := self._left_out(name, mode, info, path):
                left.append((name, why))
                continue
            if stat.S_ISLNK(info.st_mode):
                mode, digest, content, size = self._link(descriptor, name, str(path.readlink()))
            else:
                if info.st_mode & 0o022:
                    notes.append((name, "copied with the group and world write bits cleared"))
                mode = b"100755" if info.st_mode & 0o111 else b"100644"
                with self._capture_file(descriptor, name, mode) as stream:
                    digest, content = self.blob(path, info, stream)
                    stream.flush()
                    output = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(output.st_mode) or output.st_nlink != 1
                            or output.st_uid != os.getuid() or output.st_mode & 0o077
                            or output.st_size != info.st_size):
                        raise RuntimeError("source snapshot: unsafe capture output")
                size = info.st_size
            entries.append(CaptureEntry(name, mode, content, size))
            cursor = tree
            parts = name.split(b"/")
            for part in parts[:-1]:
                cursor = cursor.setdefault(part, {})
            cursor[parts[-1]] = mode, digest
        left += [(name, "a special file (a FIFO, a socket or a device)") for name in self.specials
                 if not any(name == prefix or name.startswith(prefix + b"/") for prefix in excluded)]
        return entries, missing, tree, states, left, notes

    def _link(self, descriptor: int, name: bytes, target: str) -> tuple[bytes, bytes, bytes, int]:
        """Carry the link ``name`` to ``target`` as a link: its mode, its Git digest, its content digest and its size."""
        value = os.fsencode(target)
        os.symlink(target, name.split(b"/")[-1], dir_fd=self._capture_directory(descriptor, name))
        return (b"120000", hashlib.sha1(f"blob {len(value)}\0".encode() + value, usedforsecurity=False).digest(),
                hashlib.sha256(value).digest(), len(value))

    def _capture_directory(self, descriptor: int, name: bytes) -> int:
        """The descriptor of the directory a captured ``name`` goes in, made one component at a time without following a link. The chain of
        directories open for the file before is kept and shared, so a directory is opened and checked once for all the files in it."""
        parts = name.split(b"/")[:-1]
        shared = 0
        while shared < min(len(parts), len(self.chain)) and self.chain[shared][0] == parts[shared]:
            shared += 1
        for _, held in self.chain[shared:]:
            os.close(held)
        del self.chain[shared:]
        parent = self.chain[-1][1] if self.chain else descriptor
        for part in parts[shared:]:
            with suppress(FileExistsError):
                os.mkdir(part, 0o700, dir_fd=parent)
            parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            info = os.fstat(parent)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                os.close(parent)
                raise RuntimeError("source snapshot: unsafe capture directory")
            self.chain.append((part, parent))
        return parent

    def _release(self) -> None:
        for _, held in self.chain:
            os.close(held)
        self.chain.clear()

    def _capture_file(self, descriptor: int, name: bytes, mode: bytes) -> BinaryIO:
        fd = os.open(name.split(b"/")[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o700 if mode == b"100755" else 0o600, dir_fd=self._capture_directory(descriptor, name))
        return os.fdopen(fd, "wb")

    def _verify_capture(self, root_state: tuple, inventory: tuple, tracked: tuple, states: dict) -> None:
        if (tuple(self.entries.items()) != inventory or self.tracked != tracked
                or _identity(self.root.lstat()) != root_state):
            raise RuntimeError("source snapshot: capture inventory changed")
        current_index = _optional_identity(self.index)
        if current_index != self.index_identity:
            raise RuntimeError("source snapshot: source index changed")
        if self.command(["ls-files", "--others", "--exclude-standard", "-z"]) != self.untracked:
            raise RuntimeError("source snapshot: source inventory changed")
        if any(_optional_directory(parent) != held for parent, held in self.parents.items()):
            raise RuntimeError("source snapshot: a source directory was replaced during the capture")
        for name, before in states.items():
            self.advance(1)
            if before != _optional_identity(self.root / os.fsdecode(name)):
                raise RuntimeError("source snapshot: source changed after capture")

    def _verify_output(self, descriptor: int, entries: list[CaptureEntry]) -> None:
        def fail(error: OSError) -> None:
            raise RuntimeError("source snapshot: capture traversal failed") from error

        expected = {entry.path: entry for entry in entries}
        directories = {os.fsencode(parent) for entry in entries for parent in Path(os.fsdecode(entry.path)).parents
                       if str(parent) != "."}
        seen = set()
        root = os.fstat(descriptor)
        if root.st_uid != os.getuid() or root.st_mode & 0o077:
            raise RuntimeError("source snapshot: unsafe capture root")
        for relative, dirs, files, parent in os.fwalk(".", dir_fd=descriptor, follow_symlinks=False, onerror=fail):
            links = []
            for name in dirs:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    links.append(name)
                elif (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                        or info.st_mode & 0o077 or os.fsencode(str(Path(relative) / name)) not in directories):
                    raise RuntimeError("source snapshot: unsafe capture directory")
            for name in [*files, *links]:
                path = os.fsencode(str(Path(relative) / name))
                entry = expected.get(path)
                if entry is None:
                    raise RuntimeError("source snapshot: unexpected capture output")
                self._verify_entry(name, parent, entry)
                seen.add(path)
        if seen != set(expected):
            raise RuntimeError("source snapshot: capture output disappeared")

    def _verify_entry(self, name: str, parent: int, entry: CaptureEntry) -> None:
        """Read one captured file or link back and check it is what the capture recorded."""
        self.advance(1)
        if entry.mode == b"120000":
            value = os.fsencode(os.readlink(name, dir_fd=parent))
            if hashlib.sha256(value).digest() != entry.sha256 or len(value) != entry.size:
                raise RuntimeError("source snapshot: capture output changed")
            return
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_uid != os.getuid() or before.st_mode & 0o077
                    or bool(before.st_mode & 0o100) != (entry.mode == b"100755")
                    or before.st_size != entry.size):
                raise RuntimeError("source snapshot: unsafe capture output")
            digest, size = hashlib.sha256(), 0
            for block in iter(lambda: stream.read(65536), b""):
                size += len(block)
                if size > entry.size:
                    raise RuntimeError("source snapshot: capture output changed")
                self.advance(size=len(block))
                digest.update(block)
            if (_identity(before) != _identity(os.fstat(stream.fileno()))
                    or digest.digest() != entry.sha256 or size != entry.size):
                raise RuntimeError("source snapshot: capture output changed")

    def close(self) -> None:
        shutil.rmtree(self.directory)


def tree_digest(tree: dict) -> bytes:
    rows = []
    for name, value in sorted(tree.items(), key=lambda item: item[0] + (b"/" if isinstance(item[1], dict) else b"")):
        mode, digest = (b"40000", tree_digest(value)) if isinstance(value, dict) else value
        rows.append(mode + b" " + name + b"\0" + digest)
    value = b"".join(rows)
    return hashlib.sha1(f"tree {len(value)}\0".encode() + value, usedforsecurity=False).digest()
