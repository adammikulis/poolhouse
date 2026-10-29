"""Test-run tree hashes, local artifacts and activity evidence."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from poolhouse.activity import gate
from tests.activity_support import entries, person, ring

__all__ = ["person", "ring"]
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "test"


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    (root / "test_a.py").write_text("def test_one():\n    assert True\n\ndef test_two():\n    assert True\n")
    return root


def script():
    loader = importlib.machinery.SourceFileLoader("scripts_test_runner", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


def pytest_in(_root: Path) -> list[str]:
    return [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_a.py"]


def recorded(repo: Path, status: int = 0) -> bool:
    junit = repo.parent / "result.xml"
    junit.write_text('<testsuite tests="1" failures="1"><testcase><failure/></testcase></testsuite>' if status else
                     '<testsuite tests="2" failures="0"><testcase/><testcase/></testsuite>')
    return gate.record_run(gate.Run(repo, "full", pytest_in(repo), junit, status, 0.1))


def test_the_tree_hash_follows_the_content_and_ignores_the_real_index(repo):
    first = gate.tree_hash(repo)
    assert len(first) == 40 and gate.tree_hash(repo) == first
    (repo / "test_a.py").write_text("def test_one():\n    assert False\n")
    assert gate.tree_hash(repo) != first
    assert subprocess.run(["git", "-C", str(repo), "status", "--short"], capture_output=True,
                          text=True).stdout.startswith("??")


def from_an_empty_index(root: Path) -> str:
    """The tree hash computed the long way: every file hashed into an index that starts empty."""
    env = {**os.environ, "GIT_INDEX_FILE": str(root.parent / "empty-index")}
    (root.parent / "empty-index").unlink(missing_ok=True)
    for step in (["add", "-A"], ["write-tree"]):
        done = subprocess.run(["git", "-C", str(root), *step], env=env, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def test_the_tree_hash_equals_the_long_way_in_every_state_of_the_checkout(repo):
    git(repo, "add", "test_a.py")
    git(repo, "commit", "-q", "-m", "first")
    (repo / ".gitignore").write_text("ignored.txt\n")
    (repo / "extra.py").write_text("x = 1\n")
    git(repo, "add", "extra.py")
    assert gate.tree_hash(repo) == from_an_empty_index(repo)
    (repo / "extra.py").write_text("x = 2\n")
    (repo / "test_a.py").write_text("def test_one():\n    assert False\n")
    (repo / "ignored.txt").write_text("not counted\n")
    (repo / "new.txt").write_text("untracked\n")
    first = gate.tree_hash(repo)
    assert first == from_an_empty_index(repo)
    (repo / "ignored.txt").write_text("still not counted\n")
    assert gate.tree_hash(repo) == first
    (repo / "extra.py").unlink()
    assert gate.tree_hash(repo) == from_an_empty_index(repo) != first


def test_a_same_size_rewrite_in_the_second_the_file_was_added_is_seen_after_the_clock_moves_on(repo):
    (repo / "f").write_text("aaaa")
    git(repo, "add", "f")
    (repo / "f").write_text("bbbb")
    time.sleep(2.2)
    assert gate.tree_hash(repo) == from_an_empty_index(repo)


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_an_entry_the_index_marks_as_unchanged_is_hashed_from_the_file(repo, flag):
    (repo / "f").write_text("aaaa")
    git(repo, "add", "f")
    git(repo, "update-index", flag, "f")
    (repo / "f").write_text("bbbbb")
    assert gate.tree_hash(repo) == from_an_empty_index(repo)


def test_the_tree_hash_starts_from_the_checkouts_index_so_unchanged_files_are_not_rehashed(repo, monkeypatch):
    git(repo, "add", "test_a.py")
    seen = []
    original = subprocess.run

    def run(command, *args, **kwargs):
        index = (kwargs.get("env") or {}).get("GIT_INDEX_FILE")
        if index and "add" in command:
            seen.append(Path(index).stat().st_size if Path(index).exists() else 0)
        return original(command, *args, **kwargs)

    monkeypatch.setattr(gate.subprocess, "run", run)
    gate.tree_hash(repo)
    assert seen and seen[0] > 0


def test_the_tree_hash_is_empty_outside_a_checkout(tmp_path):
    assert gate.tree_hash(tmp_path / "nowhere") == ""


def test_a_green_run_is_recorded_against_its_tree_and_found_as_evidence(person, repo):
    assert recorded(repo)
    [e] = [x for x in entries() if x.kind == "test.result"]
    tree = gate.tree_hash(repo)
    assert (e.outcome, e.refs["tree"], e.refs["tier"], e.subject) == ("pass", tree, "full", f"tree:{tree[:12]}")
    assert (e.meta["passed"], e.meta["failed"], e.meta["exit"]) == (2, 0, 0) and e.meta["seconds"] >= 0
    assert gate.evidence(tree, "full").id == e.id
    assert gate.evidence(tree, "slow") is None


def test_a_red_run_is_not_evidence_and_a_changed_tree_has_none(person, repo):
    (repo / "test_a.py").write_text("def test_one():\n    assert False\n")
    assert recorded(repo, 1)
    [e] = [x for x in entries() if x.kind == "test.result"]
    assert (e.outcome, e.meta["failed"], e.meta["passed"]) == ("fail", 1, 0)
    assert gate.evidence(e.refs["tree"]) is None
    (repo / "test_a.py").write_text("def test_one():\n    assert True\n")
    assert gate.evidence(gate.tree_hash(repo)) is None


def test_the_same_command_on_the_same_tree_has_the_same_command_hash(person, repo):
    recorded(repo)
    recorded(repo)
    one, two = [x for x in entries() if x.kind == "test.result"]
    assert one.refs["command"] == two.refs["command"] and one.refs["tree"] == two.refs["tree"]


def test_runner_writes_local_evidence_without_user_activity(repo, monkeypatch, capsys):
    runner = script()
    runner.ROOT = repo

    def execute(command, workers, *, env):
        junit = Path(next(x.split("=", 1)[1] for x in command if x.startswith("--junitxml=")))
        junit.write_text('<testsuite tests="3" failures="1"><testcase/><testcase/><testcase><failure/></testcase></testsuite>')
        return 1

    def forbidden(*args, **kwargs):
        pytest.fail("local test evidence accessed user activity")

    monkeypatch.setattr(runner.testslots_runner, "run_pytest", execute)
    monkeypatch.setattr(gate.writer, "record", forbidden)
    monkeypatch.setattr(gate.writer, "directory", forbidden)
    assert runner.run(runner.pytest_command(2, "test_a.py"), tier="quick") == 1
    artifact = Path(capsys.readouterr().out.split("test: evidence ", 1)[1].strip())
    try:
        payload = json.loads(artifact.read_text())
        assert payload["refs"]["tree"] == gate.tree_hash(repo)
        assert payload["refs"]["tier"] == "quick"
        assert payload["outcome"] == "fail"
        assert payload["meta"]["passed"] == 2
        assert payload["meta"]["failed"] == payload["meta"]["exit"] == 1
        assert payload["run"]["requested_command"] == runner.pytest_command(2, "test_a.py")
        assert payload["run"]["runner_interpreter"] == sys.executable
        assert payload["run"]["root"] == str(repo.resolve())
        assert payload["run"]["tree_after"] == payload["refs"]["tree"]
        if os.name == "nt":
            import win32security

            from poolhouse.windows_private import _user

            descriptor = win32security.GetNamedSecurityInfo(
                str(artifact), win32security.SE_FILE_OBJECT, win32security.OWNER_SECURITY_INFORMATION)
            assert descriptor.GetSecurityDescriptorOwner() == _user()
        else:
            assert artifact.stat().st_mode & 0o777 == 0o600
        assert payload["process"] == {"pid": os.getpid(), "started": psutil.Process().create_time()}
        assert not artifact.with_name(artifact.name.removesuffix(".result.json") + ".xml").exists()
    finally:
        artifact.unlink(missing_ok=True)


def test_runner_refuses_pass_when_source_changes_during_execution(repo, monkeypatch, capsys):
    runner = script()
    runner.ROOT = repo
    before = gate.tree_hash(repo)

    def execute(command, workers, *, env):
        (repo / "test_a.py").write_text("def test_changed(): pass\n")
        return 0

    monkeypatch.setattr(runner.testslots_runner, "run_pytest", execute)
    assert runner.run(runner.pytest_command(1, "test_a.py"), tier="quick") == 4
    captured = capsys.readouterr()
    assert "source tree changed" in captured.err
    artifact = Path(captured.out.split("test: evidence ", 1)[1].strip())
    try:
        payload = json.loads(artifact.read_text())
        assert payload["outcome"] == "fail"
        assert payload["refs"]["tree"] == before
        assert payload["run"]["tree_after"] != before
    finally:
        artifact.unlink(missing_ok=True)


def test_runner_does_not_report_success_without_a_valid_junit(repo, monkeypatch, capsys):
    runner = script()
    runner.ROOT = repo
    monkeypatch.setattr(runner.testslots_runner, "run_pytest", lambda *_args, **_kwargs: 0)
    assert runner.run(runner.pytest_command(1, "test_a.py"), tier="quick") != 0
    captured = capsys.readouterr()
    artifact = Path(captured.out.split("test: evidence ", 1)[1].strip())
    try:
        assert json.loads(artifact.read_text())["outcome"] != "pass"
    finally:
        artifact.unlink(missing_ok=True)


@pytest.mark.parametrize("xml", [
    '<testsuite tests="1" failures="1"/>',
    '<testsuite tests="1" errors="1"/>',
    '<testsuite tests="0" failures="0"/>',
    '<testsuites><testsuite tests="1" failures="0"><testcase/></testsuite>'
    '<testsuite tests="1" failures="1"><testcase><failure/></testcase></testsuite></testsuites>',
    '<testsuite tests="1" failures="0"><testcase><failure/></testcase></testsuite>',
])
def test_zero_exit_cannot_override_a_failing_or_empty_junit(repo, monkeypatch, capsys, xml):
    runner = script()
    runner.ROOT = repo

    def execute(command, workers, *, env):
        junit = Path(next(x.split("=", 1)[1] for x in command if x.startswith("--junitxml=")))
        junit.write_text(xml)
        return 0

    monkeypatch.setattr(runner.testslots_runner, "run_pytest", execute)
    assert runner.run(runner.pytest_command(1, "test_a.py"), tier="quick") != 0
    captured = capsys.readouterr()
    artifact = Path(captured.out.split("test: evidence ", 1)[1].strip())
    try:
        assert json.loads(artifact.read_text())["outcome"] != "pass"
    finally:
        artifact.unlink(missing_ok=True)


def test_all_skipped_is_not_recorded_as_a_passing_execution(tmp_path):
    junit = tmp_path / "result.xml"
    junit.write_text('<testsuite tests="1" failures="0" skipped="1"><testcase><skipped/></testcase></testsuite>')
    artifact = tmp_path / "evidence.json"
    gate.record_run(gate.Run(tmp_path, "all", ["pytest"], junit, 0, 0.1), artifact)
    assert json.loads(artifact.read_text())["outcome"] == "skipped"
