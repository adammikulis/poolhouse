"""The pinned Strata source and engine, put in Poolhouse's state the first time a Strata server starts: the source checked by the
digest of its content, the engine built from it on a Mac, and on Windows the release's prebuilt engine checked by its SHA-256.
Strata's own installers and update feed are never run."""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import tarfile
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from poolhouse import home, safelaunch
from poolhouse.files import promote, read_json, sha256_file, write_json
from poolhouse.lock import only_one
from poolhouse.serve.backend import ServerFailed
from poolhouse.serve.binary import child_env
from poolhouse.serve.build_platform import cmake_program
from poolhouse.serve.logs import tail
from poolhouse.serve.strata import fetch, pins

__all__ = ["Engine", "content_digest", "ensure", "existing", "extract", "home_of", "platform_kind", "root"]

KEPT = ("engine", "terminal", "notices", "LICENSE")
"""The parts of the repository Poolhouse keeps: the engine, its server and tools, and the licences."""
RECORD = "INSTALL.json"
MOST_BYTES = 1 << 30
Say = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class Engine:
    """An installed engine: its directory, the source tree (the server and tools are in ``source/terminal``) and the program."""

    home: Path
    source: Path
    binary: Path


def root() -> Path:
    """Where everything of Strata is kept on this device."""
    return home.state("strata")


def home_of(pin: dict) -> Path:
    return root() / "engine" / pin["commit"][:12]


def platform_kind() -> str:
    """``metal`` on Apple silicon, ``cuda`` on 64-bit Windows; `ServerFailed` on anything else."""
    system, machine = platform.system(), platform.machine().lower()
    if system == "Darwin" and machine in ("arm64", "aarch64"):
        return "metal"
    if system == "Windows" and machine in ("amd64", "x86_64"):
        return "cuda"
    raise ServerFailed(f"Poolhouse serves Strata on Apple silicon and on 64-bit Windows with an NVIDIA card, not on {system} {machine}")


def content_digest(entries: Iterable[tuple[str, str]]) -> str:
    """One SHA-256 over ``(relative path, SHA-256 of the file)`` pairs, in path order."""
    whole = hashlib.sha256()
    for name, digest in sorted(entries):
        whole.update(f"{name}\0{digest}\n".encode())
    return whole.hexdigest()


def _tree(source: Path) -> Iterable[tuple[str, str]]:
    for where, folders, names in os.walk(source):
        for name in (*folders, *names):
            if (Path(where) / name).is_symlink():
                raise ServerFailed(f"the installed Strata source has an alias at {Path(where) / name}")
        for name in names:
            path = Path(where) / name
            yield path.relative_to(source).as_posix(), sha256_file(path)


def extract(archive: Path, into: Path, keep: tuple[str, ...] = ()) -> None:
    """``archive`` (a ``.tar.gz``) unpacked into ``into`` with its one top directory removed, keeping the ``keep`` entries (all of
    them for none). Only regular files and directories are taken, and no name leaves ``into``."""
    total = 0
    with tarfile.open(archive) as opened:
        for member in opened:
            parts = PurePosixPath(member.name).parts
            if member.isdir() or len(parts) < 2 or (keep and parts[1] not in keep):
                continue
            inner = parts[1:]
            if (not member.isfile() or PurePosixPath(member.name).is_absolute() or "\\" in member.name
                    or any(part in ("..", ".") or ":" in part for part in inner)):
                raise ServerFailed(f"{archive.name} holds an entry that is not a plain file: {member.name!r}")
            total += member.size
            if total > 4 * MOST_BYTES:
                raise ServerFailed(f"{archive.name} unpacks to more than {4 * MOST_BYTES} bytes")
            target = into.joinpath(*inner)
            target.parent.mkdir(parents=True, exist_ok=True)
            with opened.extractfile(member) as stream, target.open("xb") as out:
                shutil.copyfileobj(stream, out)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)


def existing(pin: dict | None = None) -> Engine | None:
    """The installed engine, its source and program checked against what was recorded; None when it is not installed. Raises
    `ServerFailed` for one that has changed. Installs nothing."""
    held = pin or pins.load()
    engine = held["engine"]
    where = home_of(engine)
    record = read_json(where / RECORD, None)
    if record is None or record.get("commit") != engine["commit"]:
        return None
    source = where / "source"
    binary = where / record["binary"]
    if content_digest(_tree(source)) != engine["source_content_sha256"]:
        raise ServerFailed(f"the Strata source in {source} is not the pinned one any more; remove {where} and it is installed again")
    if not binary.is_file() or sha256_file(binary) != record["binary_sha256"]:
        raise ServerFailed(f"the Strata engine {binary} changed since it was installed; remove {where} and it is installed again")
    return Engine(where, source, binary)


def ensure(say: Say = lambda _line: None, pin: dict | None = None) -> Engine:
    """The pinned engine, installed first when it is not here. Called while a Strata server is starting."""
    held = pin or pins.load()
    kind = platform_kind()
    where = home_of(held["engine"])
    where.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with only_one(where.parent / ".install.lock"):
        if found := existing(held):
            return found
        if where.exists():
            shutil.rmtree(where)
        where.mkdir(mode=0o700)
        say(f"installing Strata {held['engine']['version']} (commit {held['engine']['commit'][:12]})")
        source = _source(where, held["engine"], say)
        binary = _metal(where, source, held, say) if kind == "metal" else _windows(where, held, say)
        write_json(where / RECORD, {"commit": held["engine"]["commit"], "binary": binary.relative_to(where).as_posix(),
                                    "binary_sha256": sha256_file(binary)})
        found = existing(held)
        if found is None:
            raise ServerFailed(f"the Strata installation in {where} did not record itself")
        return found


def _source(where: Path, engine: dict, say: Say) -> Path:
    archive = where / "source.tar.gz"
    fetch.whole(engine["source_url"], archive,
                fetch.pinned(engine["source_sha256"], "Strata source at its pinned commit", nested=True))
    say("checking the Strata source against its pinned digest")
    staged = where / "source.new"
    extract(archive, staged, KEPT)
    if (got := content_digest(_tree(staged))) != engine["source_content_sha256"]:
        shutil.rmtree(staged)
        archive.unlink()
        raise ServerFailed(f"the downloaded Strata source is not the pinned commit: its content digest is {got}")
    promote(staged, where / "source")
    archive.unlink()
    return where / "source"


def _metal(where: Path, source: Path, pin: dict, say: Say) -> Path:
    """The engine built from ``source`` with Apple's compiler, cmake and the pinned metal-cpp headers."""
    found = safelaunch.which("xcrun")
    if not found or subprocess.run([found, "--find", "clang++"], capture_output=True, timeout=60).returncode:
        raise ServerFailed("building Strata needs Apple's Command Line Tools, which only the account holder can install "
                           "(an Apple licence): macOS offers them under System Settings, General, Software Update")
    headers = where / "metal-cpp.zip"
    fetch.whole(pin["metal_cpp"]["url"], headers, fetch.pinned(pin["metal_cpp"]["sha256"], "Apple's metal-cpp headers"))
    with zipfile.ZipFile(headers) as opened:
        for name in opened.namelist():
            if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
                raise ServerFailed(f"{headers.name} names a path outside itself: {name!r}")
            opened.extract(name, where / "metal-cpp")
    cmake = cmake_program()
    build = where / "build"
    log = where / "build.log"
    say(f"building the Strata engine for Metal (a few minutes, once); log: {log}")
    steps = ([cmake, "-S", str(source / "engine" / "metal"), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release",
              f"-DMETAL_CPP_DIR={where / 'metal-cpp' / 'metal-cpp'}"],
             [cmake, "--build", str(build), "--target", "strata", "--parallel", str(os.cpu_count() or 4)])
    with log.open("wb") as out:
        for step in steps:
            if subprocess.run(step, stdout=out, stderr=subprocess.STDOUT, env=child_env(cmake), cwd=where, timeout=3600).returncode:
                raise ServerFailed(f"the Strata engine did not build\n--- {log} ---\n{tail(log)}")
    return build / "strata"


def _windows(where: Path, pin: dict, say: Say) -> Path:
    """The release's prebuilt engine, checked against its pinned SHA-256. It is a third-party binary with no build provenance."""
    release = pin["windows"]
    archive = where / release["asset"]
    say(f"downloading the prebuilt Strata engine ({release['bytes'] / 1e6:.0f} MB, checked by SHA-256)")
    fetch.whole(release["url"], archive, fetch.pinned(release["sha256"], "prebuilt Strata engine", size=release["bytes"]))
    staged = where / "release"
    with tarfile.open(archive) as opened:
        opened.extractall(staged, filter="data")
    archive.unlink()
    found = sorted(staged.rglob("strata.exe"))
    if len(found) != 1:
        raise ServerFailed(f"the Strata release {release['asset']} holds {len(found)} strata.exe files, not one")
    return found[0]
