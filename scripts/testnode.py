"""The node binary every test run uses: built once by the runner before the run's source tree is stamped, into a folder no checkout
holds and every checkout shares.

The folder is named by a key: the cargo workspace's files (``app/Cargo.toml``, ``app/Cargo.lock``, the node crate and the app
crate) and the compiler that builds them. The runner stages those files at a fixed path under the cache and builds there, in a
cargo target folder of its own, so a build never writes under a checkout and every checkout reuses the dependencies another
compiled. ``scripts/testnode.py build`` prints the binary's path for a run that is not started by ``scripts/test``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import testartifacts

from poolhouse import home, node_binary, worktreerules
from poolhouse.files import writing
from poolhouse.lock import only_one
from poolhouse.redact.secrets import env_secrets, redact

ENV = "DEV_TEST_NODE_BINARY"
"""Output of the runner, input of the node fixture: the binary this run's tests start."""
FOLDER = "test-node"
CARGO_FOLDER = "test-cargo"
CARGO_ENV = "DEV_TEST_CARGO_DIR"
"""Names the shared Rust build folder instead of the account's ``~/.cache/test-cargo``."""
FIXTURES = ("tests/fixtures/phone-approval-vectors.json", "contracts/node/vectors.json",
            "contracts/node/native_boundaries/transcript-vectors.json")
"""Files of the repository, outside the Rust workspace, that a Rust test includes at compile time."""
MEMBERS = ("Cargo.toml", "Cargo.lock", "poolhouse-node", "phone-governance-jni", "src-tauri", "poolhouse-authority", "poolhouse-manifest",
           "native-host/NativeIdentity.c", "native-host/NativeIdentity.h", "native-host/NativeStaticImage.c", "native-host/NativeStaticImage.h", "native-host/NativeCaller.c", "native-host/NativeCaller.h",
           "native-host/MachCode.c", "native-host/MachCode.h", "native-host/MachSender.c", "native-host/MachSender.h", "native-host/NativeRequest.h",
           "native-host/AuthorityChannel.c", "native-host/AuthorityChannel.h",
           "native-service/Inspection.swift", "native-service/Bridge.swift",
           "native-service/generated/PassiveServiceData.swift", "native-service/generated/PassiveServiceData.h")
KEY_LENGTH = 20
IDLE_S = 3 * 24 * 3600
BUILD_TIMEOUT_S = 1800.0
PROBE_TIMEOUT_S = 15.0
NODE_MARKERS = ("node_kit", "runner_binary")


class BuildError(RuntimeError):
    """The node binary could not be built."""


def account() -> Path:
    """The account's home directory, whatever HOME says."""
    return home.account_roots()[0].parent


def tool(name: str) -> str:
    """The path of a Rust tool: the one on PATH, else the one rustup installed for the account."""
    return shutil.which(name) or str(account() / ".cargo" / "bin" / name)


def account_environment() -> dict[str, str]:
    """This environment with HOME the account's, where rustup keeps its toolchains (a test run moves HOME)."""
    return {**os.environ, "HOME": str(account())}


def version(name: str, executable: str, arguments: list[str]) -> str:
    """The version output of a usable Rust tool, before build admission or cache writes."""
    try:
        done = subprocess.run([executable, *arguments], capture_output=True, text=True, check=False,
                              timeout=PROBE_TIMEOUT_S, env=account_environment())
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BuildError(f"{name} could not report its version; install or select a Rust toolchain (rustup.rs): "
                         f"{redact(str(error), env_secrets(os.environ))[0][:300]}") from error
    if done.returncode or not re.match(rf"{name} \d+\.\d+\.\d+(?:[-+][\w.-]+)?(?:\s|$)", done.stdout):
        detail = redact(done.stderr or done.stdout, env_secrets(os.environ))[0].strip()[:300]
        raise BuildError(f"{name} could not report its version; install or select a Rust toolchain (rustup.rs): {detail}")
    return done.stdout


def needed(root: Path, files: list[str], facts=None) -> bool:
    """Whether a run of ``files`` can start the node binary: one of them, or what they import, names the node kit; true when no file is named.

    ``facts`` is the shared import-facts file: a source whose bytes it holds is not parsed again, and the facts this call
    computed are written back to it.
    """
    if not files:
        return True
    import testreuse_key as keys

    closures = keys.Closures(root, facts)
    names = {*closures.closure(files)[0], *(c for f in files for c in keys.conftests(root, f))}
    closures.save()
    markers = (*NODE_MARKERS, ENV)
    return any(marker in keys.text_of(root / name) for name in names if name.startswith("tests/") for marker in markers)


def sources(app: Path, *, existing_only: bool = True) -> list[str]:
    """Sorted nonignored Cargo workspace paths, optionally retaining missing and symlink entries."""
    try:
        done = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *MEMBERS], cwd=app,
                              capture_output=True, text=True, check=False, env=worktreerules._git_environment())
    except OSError as error:
        raise BuildError(f"git did not run: {error}") from error
    if done.returncode:
        raise BuildError(f"the sources of {app} are listed by git, and it said: {done.stderr.strip()[-300:]}")
    return sorted({name for name in done.stdout.split("\0") if name and (not existing_only or (app / name).is_file())})


def digest(path: Path) -> str:
    """The SHA-256 of one file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def key_of(app: Path, files: list[str], toolchain: str, *, salts: tuple[str, ...] = ()) -> str:
    """Hash a versioned, unambiguous inventory and compiler identity."""
    encoded = [(os.fsencode(name), name) for name in files]
    if (len({name for name, _ in encoded}) != len(encoded)
            or any(not raw or b"\0" in raw or Path(name).is_absolute() or any(part in ("", ".", "..") for part in name.split("/"))
                   or (os.name == "nt" and (Path(name).drive or "\\" in name)) for raw, name in encoded)):
        raise BuildError("Invalid or duplicate native source path")
    encoded.sort(key=lambda item: item[0])
    value = hashlib.sha256(b"poolhouse-native-source-key-v2\0")
    value.update(len(encoded).to_bytes(8, "big"))
    for raw, name in encoded:
        info = (app / name).lstat()
        value.update(len(raw).to_bytes(8, "big"))
        value.update(raw)
        value.update((info.st_mode & 0o170111).to_bytes(4, "big"))
        value.update(info.st_size.to_bytes(8, "big"))
        value.update(bytes.fromhex(digest(app / name)))
    identity = os.fsencode(toolchain)
    value.update(len(identity).to_bytes(8, "big"))
    value.update(identity)
    value.update(len(salts).to_bytes(8, "big"))
    for salt in salts:
        identity = os.fsencode(salt)
        value.update(len(identity).to_bytes(8, "big"))
        value.update(identity)
    return value.hexdigest()[:KEY_LENGTH]


def stage(app: Path, files: list[str], work: Path) -> None:
    """Make ``work`` hold exactly ``files`` of ``app``, rewriting only what differs so the compiler sees only real edits."""
    for name in files:
        target = work / name
        if not target.is_file() or digest(target) != digest(app / name):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(app / name, target)
    wanted = set(files)
    for found in [p for p in work.rglob("*") if p.is_file()]:
        if found.relative_to(work).as_posix() not in wanted:
            found.unlink()


def stage_fixtures(repository: Path, work: Path) -> None:
    """Copy the repository files in ``FIXTURES`` beside the staged workspace, where the tests' compile-time includes look for them."""
    for name in FIXTURES:
        target = work / name
        if (repository / name).is_file() and (not target.is_file() or digest(target) != digest(repository / name)):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repository / name, target)


def prune(base: Path, keep: str) -> None:
    """Remove the binaries nothing has asked for in ``IDLE_S`` seconds; one that goes while this looks is not an error."""
    for folder in base.iterdir():
        try:
            idle = len(folder.name) == KEY_LENGTH and folder.name != keep and time.time() - folder.stat().st_mtime > IDLE_S
        except FileNotFoundError:
            continue
        if idle:
            shutil.rmtree(folder, ignore_errors=True)


class SharedBuild(NamedTuple):
    """A staged copy of the Rust workspace and the cargo target folder that compiles it."""

    staged: Path
    target: Path


def cargo_root() -> Path:
    """The folder the Rust tests build in: outside every checkout, and outside the poolhouse state and cache roots a test may not write."""
    named = os.environ.get(CARGO_ENV)
    return Path(named) if named else account() / ".cache" / CARGO_FOLDER


def node_folder(cache: Path | None = None) -> Path:
    """The folder that holds the built node binaries, one per key: under the Rust build folder unless ``cache`` names another."""
    return (cache or cargo_root()) / FOLDER


def workspace_wrapper(workspace: Path) -> None:
    """Pin workspace artifact hashes to this content-keyed staging directory."""
    if any(name in os.environ for name in ('RUSTC_WORKSPACE_WRAPPER', 'CARGO_BUILD_RUSTC_WORKSPACE_WRAPPER')):
        raise BuildError('Native tests require their maintained workspace compiler wrapper, without environment overrides')
    wrapper = workspace / 'rustc-workspace'
    configuration = workspace / '.cargo' / 'config.toml'
    if any(path.is_symlink() for path in (wrapper, configuration, configuration.parent)):
        raise BuildError('Native compiler controls cannot contain links')
    controls = ((wrapper, '#!/bin/sh\nexec "$@"\n', 0o700),
                (configuration, '[build]\nrustc-workspace-wrapper = ' + json.dumps(str(wrapper)) + '\nincremental = false\n', 0o600))
    for path, content, mode in controls:
        if path.exists() and not path.is_file():
            raise BuildError('Native compiler controls must be regular files')
        if not path.exists() or path.read_text() != content or path.stat().st_mode & 0o777 != mode:
            with writing(path) as temporary:
                temporary.write_text(content)
                temporary.chmod(mode)


def shared_build(app: Path, *, root: Path | None = None, source_key_extra: str = "") -> SharedBuild:
    """The workspace of ``app`` staged at a fixed path and the target folder every run with the same ``Cargo.lock`` and compiler shares, so
    only the crates whose files changed are compiled again; a run that stages different sources gets its own staged copy.
    App-only native toolchain metadata may salt the source key without splitting Rust dependency targets."""
    toolchain = version("rustc", tool("rustc"), ["--version", "--verbose"])
    files = sources(app)
    salts = (("extra:" + source_key_extra,) if source_key_extra else ())
    key = key_of(app, files, toolchain, salts=salts)
    locked = hashlib.sha256(b"\n".join([(app / "Cargo.lock").read_bytes(), toolchain.encode()])).hexdigest()[:KEY_LENGTH]
    base = root or cargo_root()
    staged, target = base / "workspaces" / key / "app", base / "targets" / locked
    target.mkdir(parents=True, exist_ok=True)
    with only_one(base / "stage.lock", announce=lambda _text: None, note="staging the Rust workspace for the tests"):
        workspace_wrapper(staged.parent)
        stage(app, files, staged)
        stage_fixtures(app.parent, staged.parent)
        for folder in (staged.parent, target):
            os.utime(folder)
        prune(base / "workspaces", key)
        prune(base / "targets", locked)
        testartifacts.prune_when_due(target, app, files, IDLE_S)
    return SharedBuild(staged, target)


def compile_node(work: Path, target: Path, cargo: str) -> Path:
    """Build the node from the staged workspace; the binary cargo wrote."""
    try:
        done = subprocess.run([cargo, "build", "--locked", "-p", "poolhouse-node"], cwd=work, capture_output=True, text=True,
                              timeout=BUILD_TIMEOUT_S, env={**account_environment(), "CARGO_TARGET_DIR": str(target)}, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BuildError(f"cargo build -p poolhouse-node did not run: {error}") from error
    made = target / "debug" / node_binary.name()
    if done.returncode or not made.is_file():
        raise BuildError(f"cargo build -p poolhouse-node failed:\n{(done.stderr or done.stdout).strip()[-2000:]}")
    return made


def ensure(root: Path, *, cache: Path | None = None, cargo: str = "",
           admit: Callable[[], contextlib.AbstractContextManager] = contextlib.nullcontext,
           say: Callable[[str], None] = lambda _text: None) -> Path:
    """The node binary of the sources under ``root/app``: the one the key already names, else built now under ``admit``."""
    toolchain = version("rustc", tool("rustc"), ["--version", "--verbose"])
    compiler = cargo or tool("cargo")
    version("cargo", compiler, ["--version"])
    base = node_folder(cache)
    base.mkdir(parents=True, exist_ok=True)
    app = root / "app"
    files = sources(app)
    key = key_of(app, files, toolchain)
    found = base / key / node_binary.name()
    if not found.is_file():
        with only_one(base / "build.lock", announce=say, note="building the node for the tests"):
            if not found.is_file():
                say(f"test: building the node binary for sources {key}")
                with admit():
                    stage(app, files, base / "workspace" / "app")
                    made = compile_node(base / "workspace" / "app", base / "target", compiler)
                    staging = base / f".{key}.{os.getpid()}"
                    shutil.rmtree(staging, ignore_errors=True)
                    staging.mkdir()
                    shutil.copy2(made, staging / node_binary.name())
                    (staging / "key.json").write_text(json.dumps({"key": key, "toolchain": toolchain, "files": len(files)}))
                    staging.rename(base / key)
    try:
        os.utime(base / key)
    except FileNotFoundError:  # another run pruned it between the look and the touch
        return ensure(root, cache=cache, cargo=cargo, admit=admit, say=say)
    prune(base, key)
    return found


def main(argv: list[str]) -> int:
    """``build``: print the path of the node binary for this checkout, building it when the key has none."""
    if argv != ["build"]:
        print("usage: scripts/testnode.py build", file=sys.stderr)
        return 2
    try:
        print(ensure(Path(__file__).resolve().parent.parent, say=lambda text: print(text, file=sys.stderr, flush=True)))
    except BuildError as error:
        print(f"testnode: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
