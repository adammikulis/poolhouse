"""The pinned release archive a test inspects, fetched by the runner into a folder every checkout shares; the tests reach no network.

The address, size and SHA-256 are the ones `poolhouse.serve.llamacpp_provider` installs from, which is imported only for a run that may need
the archive. The fetch is `poolhouse.net.download` in a process of its own whose Poolhouse state and cache are a temporary folder, so it
records nothing in the account's. ``scripts/testarchive.py download PATH`` is that process.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import testapp
import testnode

from poolhouse.lock import only_one

ENV = "POOLHOUSE_PRISM_ARCHIVE"
"""Output of the runner, input of the test that inspects the archive."""
FOLDER_ENV = "DEV_TEST_ARCHIVE_DIR"
"""Names the folder that holds the archives instead of the account's ``~/.cache/test-archives``."""
FOLDER = "test-archives"
FETCH_TIMEOUT_S = 600.0
"""The longest the runner waits for the one archive of a few megabytes."""


class FetchError(RuntimeError):
    """The pinned archive could not be fetched."""


def _provider():
    from poolhouse.serve import llamacpp_provider

    return llamacpp_provider


def needed(root: Path, files: list[str] | None, facts=None) -> bool:
    """Whether a run of ``files`` on this computer reads the archive: one of the files, or what they import, names `ENV` (true when no file is
    named), and the publisher supplies the runtime for this computer."""
    if files:
        import testreuse_key as keys

        closures = keys.Closures(root, facts)
        names = {*closures.closure(files)[0], *(c for f in files for c in keys.conftests(root, f))}
        closures.save()
        if not any(ENV in keys.text_of(root / name) for name in names if name.startswith("tests/")):
            return False
    return _provider().supported()


def folder() -> Path:
    named = os.environ.get(FOLDER_ENV)
    return Path(named) if named else testnode.account() / ".cache" / FOLDER


def held(target: Path) -> bool:
    """Whether ``target`` is a plain file with the pinned bytes."""
    return testapp.regular(target) and testapp.checksum(target) == _provider().ARCHIVE_SHA256


def isolated(target: Path) -> None:
    """Fetch the archive to ``target`` in a process whose Poolhouse state and cache are temporary."""
    with tempfile.TemporaryDirectory(prefix="poolhouse-archive-") as state:
        environment = {**os.environ, "POOLHOUSE_HOME": str(Path(state) / "state"), "POOLHOUSE_CACHE": str(Path(state) / "cache")}
        try:
            done = subprocess.run([sys.executable, str(Path(__file__).resolve()), "download", str(target)], env=environment,
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=FETCH_TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired as error:
            raise FetchError(f"{_provider().URL} did not arrive within {error.timeout:.0f} s") from error
    if done.returncode:
        said = (done.stderr or done.stdout).strip().splitlines()
        raise FetchError(f"{_provider().URL} could not be fetched: {said[-1][:500] if said else f'exit {done.returncode}'}")


def ensure(*, fetch: Callable[[Path], None] = isolated, say: Callable[[str], None] = lambda _text: None) -> Path:
    """The pinned archive: the one the folder already holds, else fetched now. A file that is not the pinned bytes is never returned."""
    base = folder() / _provider().ARCHIVE_SHA256[:20]
    target = base / "release.tar.gz"
    if held(target):
        return target
    if folder().is_symlink() or base.is_symlink():
        raise FetchError(f"the archive folder {base} cannot be a link")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    with only_one(base / "fetch.lock", announce=say, note="fetching the pinned release archive"):
        if not held(target):
            target.unlink(missing_ok=True)
            say(f"test: fetching {_provider().URL}")
            fetch(target)
            if not held(target):
                target.unlink(missing_ok=True)
                raise FetchError(f"{_provider().URL} did not give the pinned bytes")
    return target


def supply(root: Path, files: list[str] | None, facts, *, say: Callable[[str], None]) -> None:
    """Name the archive in this process's environment for a run that reads it; a value already set is kept. `FetchError` when it is needed
    and cannot be had."""
    if not os.environ.get(ENV) and needed(root, files, facts):
        os.environ[ENV] = str(ensure(say=say))


def download(target: Path) -> None:
    """The pinned archive saved at ``target`` after every check of `poolhouse.net.download`, with nothing else left beside it."""
    from poolhouse import net

    provider = _provider()
    net.download(provider.URL, target, provider.archive_want(), pipeline=net.default())
    for beside in target.parent.glob(target.name + ".fetch*"):
        beside.unlink()


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "download":
        raise SystemExit("usage: scripts/testarchive.py download PATH")
    download(Path(sys.argv[2]))
