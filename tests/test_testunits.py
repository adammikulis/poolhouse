"""Rust integration-test targets are compiled once, outside the tests' timers, and then run as the files cargo wrote."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import threading
import time

import pytest
import testnode
import testunits
from native_cargo import NodeUnits
from test_testnode import site  # noqa: F401  (fixture)

ALPHA = "#[test]\nfn alpha() {}\n"
BETA = """#[test]
fn beta() {
    let package = std::path::PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap());
    assert_eq!(package.file_name().unwrap(), "poolhouse-node");
    assert_eq!(std::env::current_dir().unwrap().canonicalize().unwrap(), package.canonicalize().unwrap());
}
"""


@pytest.fixture
def specimen(site, tmp_path, monkeypatch):  # noqa: F811
    tests = site.root / "app" / "poolhouse-node" / "tests"
    tests.mkdir()
    (tests / "alpha.rs").write_text(ALPHA)
    (tests / "beta.rs").write_text(BETA)
    (site.root / "app" / "poolhouse-node" / "src" / "lib.rs").write_text("#[cfg(test)]\nmod tests {\n    #[test]\n    fn unit() {}\n}\n")
    monkeypatch.setenv(testnode.CARGO_ENV, str(tmp_path / "cache"))
    return site


def local(argv, work, environment):
    done = subprocess.run(argv, cwd=work, env=environment, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    return done.stdout


def compiled(specimen, targets):
    build = testnode.shared_build(specimen.root / "app")
    environment = {**testnode.account_environment(), "CARGO_TARGET_DIR": str(build.target)}
    return build, testunits.build(testnode.tool("cargo"), build.staged, targets, local, environment)


def test_each_requested_target_gets_its_own_executable_that_passes(specimen):
    _, made = compiled(specimen, ["alpha", "beta"])
    assert sorted(made) == ["alpha", "beta"] and made["alpha"] != made["beta"]
    done = subprocess.run([made["alpha"]], capture_output=True, text=True, check=False)
    assert done.returncode == 0 and "test result: ok. 1 passed" in done.stdout, done.stdout + done.stderr


def test_the_library_unit_tests_are_a_target_of_their_own(specimen):
    _, made = compiled(specimen, [testunits.LIB, "alpha"])
    assert sorted(made) == ["alpha", testunits.LIB]
    done = subprocess.run([made[testunits.LIB], "unit"], capture_output=True, text=True, check=False)
    assert done.returncode == 0 and "test result: ok. 1 passed" in done.stdout, done.stdout + done.stderr


def test_a_target_nobody_asked_for_is_not_built(specimen):
    _, made = compiled(specimen, ["alpha"])
    assert sorted(made) == ["alpha"]


def test_the_same_sources_name_the_same_files_without_compiling_again(specimen):
    reports = []

    def recording(argv, work, environment):
        reports.append(local(argv, work, environment))
        return reports[-1]

    build = testnode.shared_build(specimen.root / "app")
    environment = {**testnode.account_environment(), "CARGO_TARGET_DIR": str(build.target)}
    first = testunits.build(testnode.tool("cargo"), build.staged, ["alpha", "beta"], recording, environment)
    second = testunits.build(testnode.tool("cargo"), build.staged, ["alpha", "beta"], recording, environment)
    rows = [json.loads(line) for line in reports[1].splitlines() if line.startswith("{")]
    tests = [row for row in rows if row.get("reason") == "compiler-artifact" and row["target"]["kind"] == ["test"]]
    assert second == first and len(tests) == 2 and all(row["fresh"] for row in tests)


def test_a_target_cargo_reported_no_executable_for_is_an_error(specimen):
    _, made = compiled(specimen, ["alpha"])
    line = '{"reason":"build-finished","success":true}\n'
    with pytest.raises(testnode.BuildError, match="no test executable for beta"):
        testunits.executables(line, ["beta"])
    gone = made["alpha"].with_name("vanished")
    row = ('{"reason":"compiler-artifact","target":{"kind":["test"],"name":"alpha","src_path":"/x/poolhouse-node/tests/alpha.rs"},'
           f'"profile":{{"test":true}},"executable":"{gone}"}}\n')
    with pytest.raises(testnode.BuildError, match="no test executable for alpha"):
        testunits.executables(row, ["alpha"])


def test_a_built_target_runs_while_another_cargo_holds_the_target_folder(specimen, tmp_path):
    (tmp_path / "work").mkdir()
    units = NodeUnits(tmp_path / "work", tmp_path, app=specimen.root / "app")
    units.prepare(["alpha", "beta"])
    outcome = []
    with (units.target / "debug" / ".cargo-lock").open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        runner = threading.Thread(target=lambda: outcome.append(units.run_targets(["alpha", "beta"])), daemon=True)
        runner.start()
        runner.join(60)
        finished = not runner.is_alive()
        fcntl.flock(held, fcntl.LOCK_UN)
    runner.join(60)
    assert finished, "running built targets waited for cargo's lock on the target folder"
    assert outcome and outcome[0].count("test result: ok. 1 passed") == 2


def test_the_products_a_build_used_are_marked_used_so_pruning_leaves_them(specimen):
    _, made = compiled(specimen, ["alpha"])
    old = time.time() - 30 * 24 * 3600
    products = [made["alpha"], made["alpha"].with_suffix(".d")]
    for path in products:
        os.utime(path, (old, old))
    compiled(specimen, ["alpha"])
    assert all(path.stat().st_mtime > time.time() - 600 for path in products)


def test_a_snapshot_is_built_without_incremental_state_nothing_would_reuse(specimen):
    build, _ = compiled(specimen, ["alpha"])
    assert "incremental = false" in (build.staged.parent / ".cargo" / "config.toml").read_text()
    assert not list((build.target / "debug" / "incremental").glob("alpha-*"))
