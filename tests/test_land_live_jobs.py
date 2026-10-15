"""Active and uncertain test jobs prevent integration deletion."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import land_live_jobs
import land_recover
import testjobs

from poolhouse import lock

PROCESS_GUARD = land_live_jobs.processes_use


@pytest.fixture
def protected(tmp_path, monkeypatch):
    tree = tmp_path / "integration"
    tree.mkdir()
    jobs = testjobs.Jobs(tmp_path / "store" / "fixture")
    jobs.write("fixture-job", "spec.json", {"id": "fixture-job", "root": str(tree)})
    monkeypatch.setattr(land_live_jobs.reuse, "reuse_base", lambda: tmp_path / "store")
    monkeypatch.setattr(land_live_jobs.testruns, "scope", lambda root: "fixture")
    monkeypatch.setattr(land_live_jobs, "processes_use", lambda paths: False)
    return jobs, tree, tmp_path


@pytest.mark.parametrize("status", [{}, {"state": "queued"}, {"state": "running"}, {"state": "unknown"},
                                     {"state": "done"}, {"state": "cancelled"}])
def test_active_or_uncertain_status_protects_tree(protected, status):
    jobs, tree, root = protected
    jobs.write("fixture-job", "status.json", status)
    with land_live_jobs.cleanup_allowed(root, {tree}) as allowed:
        assert not allowed


def test_missing_status_and_malformed_spec_protect(protected):
    jobs, tree, root = protected
    with land_live_jobs.cleanup_allowed(root, {tree}) as allowed:
        assert not allowed
    jobs.write("fixture-job", "spec.json", {})
    with land_live_jobs.cleanup_allowed(root, {tree}) as allowed:
        assert not allowed


def test_terminal_reused_pid_cannot_hold_tree(protected):
    jobs, tree, root = protected
    jobs.write("fixture-job", "status.json", {"state": "done", "pid": os.getpid(), "started": 1})
    with land_live_jobs.cleanup_allowed(root, {tree}) as allowed:
        assert allowed


def test_terminal_live_exact_process_protects_tree(protected):
    jobs, tree, root = protected
    import psutil
    jobs.write("fixture-job", "status.json", {"state": "failed", "pid": os.getpid(),
               "started": psutil.Process().create_time()})
    with land_live_jobs.cleanup_allowed(root, {tree}) as allowed:
        assert not allowed


def test_lock_contention_never_waits_or_removes_tree(protected):
    jobs, tree, root = protected
    with (jobs.folder / "lock").open("a") as handle:
        assert lock.take(handle)
        try:
            with land_live_jobs.cleanup_allowed(root, {tree}) as allowed:
                assert not allowed
        finally:
            lock.release(handle)
    assert tree.exists()


def test_cleanup_holds_same_job_admission_lock(protected):
    jobs, tree, root = protected
    jobs.write("fixture-job", "status.json", {"state": "done", "pid": os.getpid(), "started": 1})
    with land_live_jobs.cleanup_allowed(root, {tree}) as allowed:
        assert allowed
        with (jobs.folder / "lock").open("a") as other:
            assert not lock.take(other)


def test_bound_active_job_skips_existing_destructive_sweep(protected, monkeypatch):
    jobs, tree, root = protected
    jobs.write("fixture-job", "status.json", {"state": "running"})
    monkeypatch.setattr(land_recover.lg, "trees", lambda root: [SimpleNamespace(path=root, branch="dev"),
                         SimpleNamespace(path=tree, branch="land/current")])
    monkeypatch.setattr(land_recover, "_sweep", lambda root: pytest.fail("protected tree deletion"))
    assert land_recover.sweep(root) == []
    assert tree.exists()


@pytest.mark.parametrize("status", [None, {"state": []}, {"state": "done", "pid": 1, "started": float("nan")}])
def test_foreign_valid_root_does_not_protect_candidates(protected, status):
    jobs, tree, root = protected
    jobs.write("fixture-job", "spec.json", {"id": "fixture-job", "root": str(root / "other")})
    if status is not None:
        jobs.write("fixture-job", "status.json", status)
    with land_live_jobs.cleanup_allowed(root, {tree}) as allowed:
        assert allowed


def test_symlink_record_cannot_authorize_cleanup(protected):
    jobs, tree, root = protected
    path = jobs.path("fixture-job") / "spec.json"
    target = root / "record.json"
    target.write_text(json.dumps({"id": "fixture-job", "root": str(root / "other")}))
    path.unlink()
    path.symlink_to(target)
    with land_live_jobs.cleanup_allowed(root, {tree}) as allowed:
        assert not allowed


def test_child_cwd_protects_after_unrecorded_launcher_exit(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path)
    try:
        assert land_live_jobs.processes_use({tmp_path.resolve()})
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_child_script_path_protects_with_cwd_elsewhere(tmp_path):
    tree = tmp_path / "integration"
    tree.mkdir()
    script = tree / "worker.py"
    script.write_text("import time; time.sleep(30)")
    child = subprocess.Popen([sys.executable, str(script)], cwd=tmp_path)
    try:
        assert land_live_jobs.processes_use({tree.resolve()})
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_oversized_record_retains_tree(protected):
    jobs, tree, root = protected
    (jobs.path("fixture-job") / "spec.json").write_bytes(b" " * 65537)
    with land_live_jobs.cleanup_allowed(root, {tree}) as allowed:
        assert not allowed


@pytest.mark.parametrize("filename", ["spec.json", "lock"])
def test_fifo_file_refuses_without_blocking(protected, filename):
    jobs, tree, root = protected
    path = jobs.folder / "lock" if filename == "lock" else jobs.path("fixture-job") / filename
    path.unlink(missing_ok=True)
    os.mkfifo(path)
    code = """import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import land_live_jobs
try:
    land_live_jobs.regular_fd(Path(sys.argv[2]), 0)
except (OSError, ValueError):
    pass
else:
    raise AssertionError('FIFO accepted')
"""
    result = subprocess.run([sys.executable, "-c", code, str(Path(land_live_jobs.__file__).parent), str(path)],
                            capture_output=True, timeout=3, check=False)
    assert result.returncode == 0, result.stderr
    with land_live_jobs.cleanup_allowed(root, {tree}) as allowed:
        assert not allowed


def test_symlink_lock_refuses_without_touching_target(protected):
    jobs, tree, root = protected
    target = root / "other-lock"
    target.write_text("unchanged")
    (jobs.folder / "lock").symlink_to(target)
    with land_live_jobs.cleanup_allowed(root, {tree}) as allowed:
        assert not allowed
    assert target.read_text() == "unchanged"


@pytest.mark.parametrize("raw", [None, "relative", 3])
def test_terminal_gone_launcher_does_not_require_root(protected, raw):
    jobs, tree, root = protected
    jobs.write("fixture-job", "spec.json", {"id": "fixture-job", "root": raw})
    jobs.write("fixture-job", "status.json", {"state": "done", "pid": os.getpid(), "started": 1})
    with land_live_jobs.cleanup_allowed(root, {tree}) as allowed:
        assert allowed


@pytest.mark.parametrize("started", [float("nan"), float("inf"), -1, True,
    pytest.param(10 ** 1000, id="overflowing-integer")])
def test_invalid_birth_never_proves_launcher_gone(protected, started):
    jobs, tree, root = protected
    jobs.write("fixture-job", "status.json", {"state": "done", "pid": os.getpid(), "started": started})
    with land_live_jobs.cleanup_allowed(root, {tree}) as allowed:
        assert not allowed


def test_live_same_birth_launcher_with_missing_root_protects(protected):
    import psutil
    jobs, tree, root = protected
    jobs.write("fixture-job", "spec.json", {"id": "fixture-job"})
    jobs.write("fixture-job", "status.json", {"state": "done", "pid": os.getpid(),
               "started": psutil.Process().create_time()})
    with land_live_jobs.cleanup_allowed(root, {tree}) as allowed:
        assert not allowed


def test_uninspectable_launcher_with_missing_root_protects(protected, monkeypatch):
    import psutil
    jobs, tree, root = protected
    jobs.write("fixture-job", "spec.json", {"id": "fixture-job"})
    jobs.write("fixture-job", "status.json", {"state": "done", "pid": os.getpid(), "started": 1})
    def denied(pid):
        raise psutil.AccessDenied(pid)
    monkeypatch.setattr(land_live_jobs.psutil, "Process", denied)
    with land_live_jobs.cleanup_allowed(root, {tree}) as allowed:
        assert not allowed


@pytest.mark.parametrize("kind", ["identity", "symlink", "fifo", "oversized"])
def test_unsafe_spec_still_protects_after_launcher_gone(protected, kind):
    jobs, tree, root = protected
    jobs.write("fixture-job", "status.json", {"state": "done", "pid": os.getpid(), "started": 1})
    path = jobs.path("fixture-job") / "spec.json"
    if kind == "identity":
        jobs.write("fixture-job", "spec.json", {"id": "other-job"})
    elif kind == "oversized":
        path.write_bytes(b" " * 65537)
    else:
        path.unlink()
        if kind == "fifo":
            os.mkfifo(path)
        else:
            target = root / "foreign.json"
            target.write_text('{"id":"fixture-job"}')
            path.symlink_to(target)
    with land_live_jobs.cleanup_allowed(root, {tree}) as allowed:
        assert not allowed


def test_surviving_candidate_child_protects_terminal_unscoped_job(protected, monkeypatch):
    jobs, tree, root = protected
    jobs.write("fixture-job", "spec.json", {"id": "fixture-job"})
    jobs.write("fixture-job", "status.json", {"state": "done", "pid": os.getpid(), "started": 1})
    monkeypatch.setattr(land_live_jobs, "processes_use", PROCESS_GUARD)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tree)
    try:
        with land_live_jobs.cleanup_allowed(root, {tree}) as allowed:
            assert not allowed
    finally:
        child.terminate()
        child.wait(timeout=5)
