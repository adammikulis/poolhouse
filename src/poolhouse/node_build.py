"""Put the node into a runtime being built: compile it from the same commit unless it was built from the same node sources before,
record its checksum, prove it answers.

macOS keeps its answer about the node's app (Local Network, the firewall) against the app's signature, which changes with the
binary's bytes unless the user added a certificate, so a binary already built from these sources is used again.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from poolhouse import node_binary, node_launch, runtime
from poolhouse.files import read_json, write_json, writing

CACHE = "cargo-target"
KEPT = "node-binaries"
"""The folder under the runtimes that holds the last binary built, in a folder named by the checksum of its sources."""


def add_to(built: runtime.Runtime, source: Path, commit: str, *, timeout: float = node_binary.BUILD_TIMEOUT) -> dict:
    """Install the node of ``source`` in the runtime tree and start it once on a scratch state to see it answer.

    The cargo target directory is shared by every build on this machine, so a later commit compiles only what changed.
    Returns the binary's record; raises when the build fails or the node does not answer.
    """
    record = node_binary.install(built.prefix, _binary(source, timeout=timeout), commit=commit)
    node_launch.smoke(built.prefix)
    return record


def source_digest(source: Path) -> str:
    """One checksum over the platform and every file the node is built from: the paths and bytes under ``app``, without cargo's output."""
    root = source / "app"
    seen = hashlib.sha256(node_binary.target().encode())
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root)
        if path.is_file() and "target" not in name.parts:
            seen.update(f"{name.as_posix()}\0{node_binary.sha256(path)}\n".encode())
    return seen.hexdigest()


def _binary(source: Path, *, timeout: float) -> Path:
    """The node binary of ``source``: the one kept from a build of the same files, else a new build, which is kept in its place."""
    kept = runtime.directory() / KEPT / source_digest(source)
    held = kept / node_binary.name()
    if held.is_file() and read_json(kept / node_binary.RECORD, {}).get("sha256") == node_binary.sha256(held):
        return held
    made = node_binary.build(source, cache=runtime.directory() / CACHE, timeout=timeout)
    shutil.rmtree(kept.parent, ignore_errors=True)  # one is kept: a runtime holds its own copy
    kept.mkdir(mode=0o700, parents=True)
    with writing(held) as temporary:
        shutil.copyfile(made, temporary)
        temporary.chmod(0o700)
    write_json(kept / node_binary.RECORD, {"sha256": node_binary.sha256(held)})
    return held


def mark(prefix: Path) -> dict:
    """The fields `mark_verified` adds for a tree's node binary: its checksum (nothing on a tree with none)."""
    try:
        return {"node_sha256": node_binary.record_of(prefix)["sha256"]}
    except OSError:
        return {}
