"""An upgrade changes the signed identity macOS keeps its answers against only when it brings other code.

The node binary is built again only when the node's own sources differ, and a runtime is built from the interpreter the Poolhouse
executable was copied from while that one is there unchanged. No cargo, codesign or keychain runs here.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from poolhouse import node_build, runtime, runtime_deploy, runtime_identity


@pytest.fixture
def cargo(tmp_path, monkeypatch):
    """Plumbing only: stands for a cargo build that never gives the same bytes twice, and a node that answers."""
    built: list[Path] = []

    def build(source, *, cache=None, timeout=0.0):
        made = tmp_path / f"built-{len(built)}"
        made.write_bytes(f"plumbing-only node binary number {len(built)}".encode())
        built.append(source)
        return made

    monkeypatch.setattr(node_build.node_binary, "build", build)
    monkeypatch.setattr(node_build.node_launch, "smoke", lambda prefix: None)
    return built


def release(tmp_path: Path, commit: str, node: str, other: str = "") -> dict:
    """The node record of a runtime built from a source tree whose node reads ``node``."""
    source = tmp_path / f"source-{commit}"
    (source / "app" / "poolhouse-node" / "src").mkdir(parents=True)
    (source / "app" / "Cargo.lock").write_text("locked")
    (source / "app" / "poolhouse-node" / "src" / "main.rs").write_text(node)
    (source / "app" / "target").mkdir()
    (source / "app" / "target" / "leftover").write_text(commit)
    (source / "README.md").write_text(other)
    prefix = tmp_path / f"runtime-{commit}"
    return node_build.add_to(runtime.Runtime(prefix, commit * 40, "0", runtime.identity()), source, commit * 40)


def test_an_upgrade_that_leaves_the_node_alone_keeps_the_same_node_binary(tmp_path, cargo):
    first = release(tmp_path, "a", "fn main() {}", "one")
    second = release(tmp_path, "b", "fn main() {}", "two")
    assert second["sha256"] == first["sha256"] and len(cargo) == 1, "other bytes would be another app to macOS"
    assert second["commit"] == "b" * 40


def test_an_upgrade_that_changes_the_node_builds_it(tmp_path, cargo):
    first = release(tmp_path, "a", "fn main() {}")
    second = release(tmp_path, "b", "fn main() { serve() }")
    assert second["sha256"] != first["sha256"] and len(cargo) == 2


def test_a_kept_node_binary_that_was_changed_is_built_again(tmp_path, cargo):
    release(tmp_path, "a", "fn main() {}")
    [kept] = [one for one in (runtime.directory() / node_build.KEPT).rglob("poolhouse-node*") if one.suffix != ".json"]
    kept.write_bytes(b"something else")
    second = release(tmp_path, "b", "fn main() {}")
    assert len(cargo) == 2 and (tmp_path / "built-1").read_bytes() == kept.read_bytes()
    assert second["sha256"] != ""


def interpreter_with_record(tmp_path: Path) -> Path:
    """A plumbing-only interpreter file, recorded as the one the Poolhouse executable was copied from."""
    held = tmp_path / "held" / "python3.13"
    held.parent.mkdir()
    held.write_bytes(struct.pack("<IiiIIIII", 0xFEEDFACF, 0x0100000C, 0, 2, 0, 0, 0, 0))
    resources = runtime_identity.bundle_path() / "Contents" / "Resources"
    resources.mkdir(parents=True)
    record = {"interpreter": str(held), "sha256": runtime_identity.digest(runtime_identity.parts(held))}
    (resources / "source.json").write_text(json.dumps(record))
    return held


def test_an_upgrade_run_by_another_python_builds_from_the_one_the_executable_was_made_from(tmp_path, monkeypatch):
    held = interpreter_with_record(tmp_path)
    other = tmp_path / "other-python"
    other.write_bytes(b"x")
    monkeypatch.setattr(runtime_deploy.Environment, "host_python", lambda self: other)
    assert runtime_deploy.host_python() == held
    held.write_bytes(held.read_bytes() + b"\0" * 8)  # Python was upgraded in place: the executable has to follow it
    assert runtime_deploy.host_python() == other
    held.unlink()
    assert runtime_deploy.host_python() == other
