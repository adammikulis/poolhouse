"""Fixed serving dependencies required by GGUF formats outside upstream llama.cpp."""
from __future__ import annotations

import os
import platform
import stat
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from poolhouse import net
from poolhouse.files import promote
from poolhouse.hub.modelfile import Limits, scan_gguf
from poolhouse.lock import only_one
from poolhouse.serve import grant, provider_archive
from poolhouse.serve.backend import Lease, LlamaServerBackend, ServerFailed
from poolhouse.serve.binary import BinaryNotFound, child_env, managed_root
from poolhouse.serve.build_platform import verify_libraries

# Publisher release and complete archive digest, independently verified against official sources.
REVISION = '73bfe6cc485554822f80c473a3c0c4f25e80f41b'
RELEASE = 'prism-b10787-73bfe6c'
PREFIX = 'llama-' + RELEASE
ARCHIVE_SHA256 = '59230b0d3e90715095a3f1b9a42bb6723308e7cf3d2d3ef562f49213e95fcfc1'
URL = f'https://github.com/PrismML-Eng/llama.cpp/releases/download/{RELEASE}/{PREFIX}-bin-macos-arm64.tar.gz'


def identity(model) -> tuple:
    path = Path(str(model)).expanduser().resolve()
    info = path.stat()
    return str(path), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@lru_cache(maxsize=128)
def _requires(fingerprint: tuple) -> bool:
    path = Path(fingerprint[0])
    if not path.is_file():
        return False
    answer = False
    with path.open('rb') as source:
        magic = source.read(4)
    if magic == b'GGUF':
        kinds = {t.kind for t in scan_gguf(path, tensors=True, limits=Limits(keep_array=0)).tensors}
        answer = bool(kinds.intersection((142, 143)))
        if answer and kinds - {*range(43), 142, 143}:
            raise ServerFailed('The model combines unsupported tensor formats')
    if identity(path) != fingerprint:
        raise ServerFailed('The model changed while its serving format was read')
    return answer


def required(model) -> bool:
    """Choose a fixed provider from bounded tensor metadata, never from the model name."""
    try:
        before = identity(model)
        answer = _requires(before)
        if identity(model) != before:
            raise ServerFailed('The model changed while its serving format was read')
        return answer
    except (FileNotFoundError, IsADirectoryError):
        return False


def directory() -> Path:
    return managed_root() / 'providers' / RELEASE


def _location(root: Path) -> None:
    for path in (root, *root.parents):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        shared_temporary = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid())
                or (info.st_mode & 0o022 and not shared_temporary)):
            raise ServerFailed('The serving runtime location has an untrusted directory or alias')


def _sync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def existing() -> Path:
    """Read-only verification; discovery and preview never install anything."""
    root = directory()
    _location(root)
    try:
        entries = provider_archive.contents(root / 'release.tar.gz', ARCHIVE_SHA256, PREFIX)
        return provider_archive.verify(root, entries)
    except FileNotFoundError as exc:
        raise BinaryNotFound('The required Bonsai runtime will be prepared when serving starts') from exc
    except (ValueError, OSError) as exc:
        raise ServerFailed(str(exc)) from exc


def environment(extra=None) -> dict[str, str]:
    result = {key: value for key, value in child_env(directory() / 'llama-server').items()
              if not key.startswith(('DYLD_', 'LD_', 'GGML_', 'LLAMA_'))}
    if (extra or {}).get('LLAMA_SERVER_SLOTS_DEBUG') == '1':
        result['LLAMA_SERVER_SLOTS_DEBUG'] = '1'
    return result


def install(lease: Lease) -> Path:
    """Prepare only the fixed dependency while the broker owns an admitted start."""
    grant.require(lease, Lease)
    if platform.system() != 'Darwin' or platform.machine().lower() not in ('arm64', 'aarch64'):
        raise ServerFailed('This Bonsai packing needs a runtime currently supplied for Apple Silicon')
    root = directory()
    if root.exists() or root.is_symlink():
        binary = existing()
        verify_libraries(root, {item.name for item in root.iterdir()} - {'release.tar.gz'}, environment())
        return binary
    with tempfile.TemporaryDirectory(prefix='.prism-', dir=root.parent) as temporary:
        stage = Path(temporary) / 'payload'
        stage.mkdir(mode=0o700)
        archive = Path(temporary) / 'download' / 'release.tar.gz'
        net.download(URL, archive, net.Want(sha256=ARCHIVE_SHA256, require_digest=True,
                     library_links=True, size=11573797, max_bytes=32 << 20,
                     purpose='fixed Bonsai serving runtime'))
        entries = provider_archive.contents(archive, ARCHIVE_SHA256, PREFIX)
        promote(archive, stage / 'release.tar.gz')
        for name, entry in entries.items():
            path = stage / name
            with path.open('xb') as output:
                output.write(entry.data)
                output.flush()
                os.fsync(output.fileno())
            path.chmod(0o700 if entry.executable else 0o600)
        provider_archive.verify(stage, entries)
        verify_libraries(stage, entries, environment())
        _sync(stage / 'release.tar.gz')
        _sync(stage)
        grant.require(lease, Lease)
        promote(stage, root)
        _sync(root.parent)
    return existing()


class PrismBackend(LlamaServerBackend):
    provider = 'prism'

    @property
    def binary(self) -> Path:
        return existing()

    def environment(self, extra=None):
        return environment(extra)

    def start(self, spec, *, lease, **starting):
        grant.require(lease, Lease)
        before = identity(spec.model)
        parent = directory().parent
        _location(parent)
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if parent.is_symlink() or parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o022:
            raise ServerFailed('The serving runtime directory is not privately owned')
        with only_one(parent / '.prepare.lock'):
            try:
                install(lease)
            except (ValueError, OSError, subprocess.SubprocessError, net.Blocked) as exc:
                raise ServerFailed(f'The Bonsai serving runtime could not be prepared: {exc}') from exc
            if identity(spec.model) != before:
                raise ServerFailed('The model changed while its serving runtime was prepared')
            grant.require(lease, Lease)
            return super().start(spec, lease=lease, **starting)


def for_model(backend, model, cached):
    """The fixed format adapter for an ordinary llama backend, preserving custom engines."""
    if type(backend) is not LlamaServerBackend or not required(model):
        return backend
    if 'prism' not in cached:
        cached['prism'] = PrismBackend(quiet=backend.quiet, sandboxed=backend.sandboxed)
    return cached['prism']
