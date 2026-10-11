"""The node binary a test run starts is built once by the runner, outside every checkout, under a key of the sources that make it."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path

import node_kit
import pytest
import testnode
from test_gatescope import load_runner

MAIN = "fn main() { println!(\"{} node\", env!(\"CARGO_PKG_NAME\")); }\n"


@dataclass
class Site:
    """A checkout holding a cargo workspace with a node crate that has no dependencies, and the recorder standing in for cargo."""

    root: Path
    log: Path
    shim: Path

    @property
    def source(self) -> Path:
        return self.root / "app" / "poolhouse-node" / "src" / "main.rs"

    def build(self, cache: Path) -> Path:
        return testnode.ensure(self.root, cache=cache, cargo=str(self.shim))

    def builds(self) -> int:
        return len(self.log.read_text().splitlines()) if self.log.exists() else 0

    def files(self) -> dict[str, tuple[int, int]]:
        return {p.relative_to(self.root).as_posix(): (p.stat().st_mtime_ns, p.stat().st_size) for p in self.root.rglob("*") if p.is_file()}


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "checkout"
    (root / "app" / "poolhouse-node" / "src").mkdir(parents=True)
    (root / "app" / "Cargo.toml").write_text('[workspace]\nmembers = ["poolhouse-node"]\nresolver = "2"\n')
    (root / "app" / "poolhouse-node" / "Cargo.toml").write_text('[package]\nname = "poolhouse-node"\nversion = "0.0.0"\nedition = "2021"\n')
    (root / "app" / "poolhouse-node" / "src" / "main.rs").write_text(MAIN)
    log, shim = tmp_path / "cargo-calls", tmp_path / "cargo"
    shim.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexec "{testnode.tool("cargo")}" "$@"\n')
    shim.chmod(0o755)
    subprocess.run([testnode.tool("cargo"), "generate-lockfile", "--offline"], cwd=root / "app", check=True, capture_output=True,
                   env=testnode.account_environment())
    return Site(root, log, shim)


def says(binary: Path) -> str:
    return subprocess.run([binary], capture_output=True, text=True, check=True).stdout


def test_a_build_writes_nothing_under_the_checkout_and_makes_a_working_binary(site, tmp_path):
    before = site.files()
    binary = site.build(tmp_path / "cache")
    assert says(binary) == "poolhouse-node node\n"
    assert site.files() == before and not (site.root / "app" / "target").exists()
    assert tmp_path / "cache" in binary.parents


def test_the_same_sources_reuse_the_binary_in_this_checkout_and_in_another(site, tmp_path):
    cache = tmp_path / "cache"
    first = site.build(cache)
    assert site.build(cache) == first and site.builds() == 1
    other = tmp_path / "another-worktree"
    shutil.copytree(site.root, other)
    assert testnode.ensure(other, cache=cache, cargo=str(site.shim)) == first and site.builds() == 1


def test_changed_sources_get_another_key_and_a_new_build(site, tmp_path):
    cache = tmp_path / "cache"
    first = site.build(cache)
    site.source.write_text(MAIN.replace("node", "node again"))
    second = site.build(cache)
    assert second != first and site.builds() == 2 and first.is_file()
    assert says(second) == "poolhouse-node node again\n"
    (site.root / "app" / "Cargo.toml").write_text((site.root / "app" / "Cargo.toml").read_text() + "# more\n")
    assert site.build(cache) not in (first, second)


def test_files_outside_the_cargo_workspace_do_not_change_the_key(site, tmp_path):
    first = site.build(tmp_path / "cache")
    (site.root / "app" / "android").mkdir()
    (site.root / "app" / "android" / "build.gradle").write_text("x")
    (site.root / "app" / "target").mkdir()
    (site.root / "app" / "target" / "stale").write_text("x")
    (site.root / "README.md").write_text("x")
    assert site.build(tmp_path / "cache") == first and site.builds() == 1


def test_another_compiler_is_another_key(site):
    app = site.root / "app"
    files = testnode.sources(app)
    assert testnode.key_of(app, files, "rustc 1.94.0") != testnode.key_of(app, files, "rustc 1.95.0")
    assert testnode.key_of(app, files, "rustc 1.94.0") == testnode.key_of(app, files, "rustc 1.94.0")


def test_a_binary_nothing_has_asked_for_in_days_is_removed_and_the_current_one_is_not(site, tmp_path):
    cache = tmp_path / "cache"
    old = site.build(cache)
    site.source.write_text(MAIN.replace("node", "newer"))
    long_ago = time.time() - testnode.IDLE_S - 60
    os.utime(old.parent, (long_ago, long_ago))
    current = site.build(cache)
    assert current.is_file() and not old.exists()


def test_a_build_that_fails_says_so_and_leaves_no_binary(site, tmp_path):
    site.source.write_text("fn main( {\n")
    with pytest.raises(testnode.BuildError, match="cargo build -p poolhouse-node failed"):
        site.build(tmp_path / "cache")
    assert not list((tmp_path / "cache" / testnode.FOLDER).glob("?" * testnode.KEY_LENGTH))


def test_the_node_fixture_uses_the_runners_binary_and_never_runs_cargo(tmp_path, monkeypatch):
    made = tmp_path / "poolhouse-node"
    made.write_text("binary")
    monkeypatch.setenv(testnode.ENV, str(made))
    monkeypatch.setenv("PATH", str(tmp_path / "no-tools"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail(f"the fixture started {a}"))
    assert node_kit.runner_binary() == made
    assert "cargo" not in Path(node_kit.__file__).read_text()


def test_no_test_builds_the_node_inside_the_checkout():
    built_here = re.compile(r"node_binary(?:_module)?\.build\(ROOT|cargo.{0,60}ROOT / .app.")
    elsewhere = {Path(__file__).name, "test_node_windows.py"}
    files = [p for p in sorted(Path(__file__).parent.glob("*.py")) if p.name not in elsewhere]
    assert [p.name for p in files if built_here.search(p.read_text())] == []
    assert [p.name for p in files if 'packaging["node"]' in p.read_text() and "CARGO_TARGET_DIR" not in p.read_text()] == []


def test_a_run_the_runner_did_not_start_has_no_node_and_is_told_to_use_the_runner(tmp_path, monkeypatch):
    monkeypatch.delenv(testnode.ENV, raising=False)
    with pytest.raises(pytest.fail.Exception, match=r"scripts/test"):
        node_kit.runner_binary()
    monkeypatch.setenv(testnode.ENV, str(tmp_path / "gone"))
    with pytest.raises(pytest.fail.Exception, match=r"scripts/test"):
        node_kit.runner_binary()


def test_the_runner_builds_the_node_before_the_gate_runs_a_step_and_names_it_for_pytest(tmp_path, monkeypatch):
    runner = load_runner()
    order = []
    monkeypatch.setattr(runner.testnode, "ensure", lambda *a, **k: order.append("build") or tmp_path / "node")
    monkeypatch.setattr(runner.gatescope, "unreached", lambda root, base: "")
    monkeypatch.setattr(runner, "gate_steps", lambda workers, rest: order.append("steps") or [])
    monkeypatch.setitem(os.environ, testnode.ENV, "")
    assert runner.gate(Namespace(full=False, base="0.2dev", workers=0, artifact_output=False), []) == 0
    assert order == ["build", "steps"] and os.environ[testnode.ENV] == str(tmp_path / "node")


def test_a_gate_the_diff_does_not_reach_builds_nothing(monkeypatch):
    runner = load_runner()
    monkeypatch.setattr(runner.testnode, "ensure", lambda *a, **k: pytest.fail("built for a skipped gate"))
    monkeypatch.setattr(runner.gatescope, "unreached", lambda root, base: "the change reaches no test (x)")
    assert runner.gate(Namespace(full=False, base="0.2dev", workers=0, artifact_output=False), []) == 0


def test_a_failed_build_stops_the_run_before_any_test_with_the_cargo_error(monkeypatch, capsys):
    runner = load_runner()

    def broken(*a, **k):
        raise testnode.BuildError("cargo build -p poolhouse-node failed:\nerror[E0432]")

    monkeypatch.setattr(runner.testnode, "ensure", broken)
    assert runner.build_node() == 4
    assert "error[E0432]" in capsys.readouterr().err


def test_the_build_command_prints_the_binary_for_a_run_the_runner_does_not_start(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(testnode, "ensure", lambda root, **k: tmp_path / "poolhouse-node")
    assert testnode.main(["build"]) == 0 and capsys.readouterr().out.strip() == str(tmp_path / "poolhouse-node")
    assert testnode.main(["nonsense"]) == 2
