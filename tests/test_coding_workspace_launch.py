"""The launch of a parent-owned workspace: its budget, Apple's git, the files it cannot carry, the bytes it needs and a hostile index."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import resource
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from poolhouse import coding_workspace
from poolhouse.activity import source_snapshot as source

REPO = Path(__file__).resolve().parent.parent
TEXT = b"x = 1\n"


def git(root: Path, *args: str) -> None:
    subprocess.run([str(source.actual_git()), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=root, check=True,
                   capture_output=True, env={**source.git_environment(), "HOME": str(root)})


@pytest.fixture(autouse=True)
def private_snapshot_storage(tmp_path, monkeypatch):
    storage = tmp_path / "snapshot-storage"
    storage.mkdir(mode=0o700)
    monkeypatch.setattr(tempfile, "tempdir", str(storage))


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "ok.txt").write_bytes(TEXT)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_bytes(TEXT)
    git(root, "init", "-q")
    git(root, "add", "-A")
    return root


def staged(root: Path, **options) -> coding_workspace.Workspace:
    return coding_workspace.Workspace(root, **options)


def nobody(capture, revision):
    return contextlib.nullcontext()


MEASURE = """
import contextlib, json, resource, sys
from pathlib import Path
from poolhouse import coding_workspace

def cpu():
    own, kids = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    return own.ru_utime + own.ru_stime + kids.ru_utime + kids.ru_stime

timings, began = {}, cpu()
workspace = coding_workspace.Workspace(Path(sys.argv[1]))
timings["Workspace()"], began = cpu() - began, cpu()
stage = workspace.begin()
timings["begin()"], began = cpu() - began, cpu()
(stage.physical / "README.md").write_text("changed\\n")
workspace.finish(stage, lambda capture, revision: contextlib.nullcontext(), writable=True)
timings["finish()"] = cpu() - began
print(json.dumps(timings))
"""
"""A fresh interpreter does the launch, so what is measured is the workspace and not the test run's guards around every call it makes."""


def cpu() -> float:
    """The CPU seconds this process and the children it has waited for have used, so a machine busy with other work is not a failure."""
    own, kids = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    return own.ru_utime + own.ru_stime + kids.ru_utime + kids.ru_stime


@pytest.mark.slow
def test_stage_and_begin_finish_stay_inside_the_launch_budget(tmp_path):
    names = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True).stdout.split(b"\0")
    root = tmp_path / "project"
    root.mkdir()
    for name in filter(None, names):
        if (REPO / os.fsdecode(name)).is_file() and not (REPO / os.fsdecode(name)).is_symlink():
            (root / os.fsdecode(name)).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / os.fsdecode(name), root / os.fsdecode(name))
    git(root, "init", "-q")
    began = cpu()
    subprocess.run(["cp", "-R", str(root), str(tmp_path / "cp-target")], check=True)
    for each in root.rglob("*"):
        if each.is_file() and ".git" not in each.relative_to(root).parts:
            hashlib.sha256(each.read_bytes()).digest()
    git(tmp_path / "cp-target", "add", "-A")
    baseline = cpu() - began
    git(root, "add", "-A")
    done = subprocess.run([sys.executable, "-c", MEASURE, str(root)], capture_output=True, check=True, text=True, timeout=600,
                          env={**os.environ, "PYTHONPATH": str(REPO / "src"), "TMPDIR": tempfile.tempdir})
    timings = json.loads(done.stdout.splitlines()[-1])
    for phase, took in timings.items():
        assert took < 5.0, f"{phase} took {took:.1f} CPU seconds; the launch budget is 5 s ({timings}, baseline {baseline:.1f} s)"
        assert took < 2 * baseline, f"{phase} took {took:.1f} s against {baseline:.1f} s for cp -R plus hashing plus the index of the same files"


@pytest.mark.skipif(sys.platform != "darwin", reason="Apple's command-line tools exist on macOS only")
def test_apples_command_line_tools_git_is_accepted_and_neither_the_shim_nor_a_git_anyone_wrote_is(tmp_path, monkeypatch):
    monkeypatch.setattr(source, "GIT_ROOTS", ())
    assert source.APPLE_GIT.exists(), "Apple's command-line developer tools are not installed on this Mac"
    assert source.actual_git() == source.APPLE_GIT != Path("/usr/bin/git")
    assert source.git_output(["--version"], cwd=tmp_path).startswith(b"git version")
    copy = tmp_path / "git"
    shutil.copy("/bin/ls", copy)
    copy.chmod(0o755)
    with pytest.raises(RuntimeError, match=r"root-owned|root and closed to writers"):
        source._apple_git(copy)
    monkeypatch.setattr(source, "APPLE_GIT", tmp_path / "absent")
    with pytest.raises(RuntimeError, match="Apple shim refused"):
        source.actual_git()


@pytest.mark.parametrize("mask", [0o000, 0o022, 0o077])
def test_a_link_inside_the_tree_is_carried_and_a_group_writable_file_is_copied_closed_whatever_the_umask(project, mask):
    (project / "link-in").symlink_to("src/app.py")
    (project / "loose.txt").write_bytes(TEXT)
    old = os.umask(mask)
    try:
        (project / "loose.txt").chmod(0o666)
    finally:
        os.umask(old)
    git(project, "add", "-A")
    workspace = staged(project)
    captured = workspace.last or workspace.initial
    assert (workspace.master / "link-in").is_symlink() and (workspace.master / "link-in").readlink().as_posix() == "src/app.py"
    assert not (workspace.master / "loose.txt").stat().st_mode & 0o077, "the copy has the group and world write bits cleared"
    assert any(path == b"loose.txt" for path, _ in captured.notes) and workspace.left_out() == ()
    stage = workspace.begin()
    assert (stage.physical / "link-in").is_symlink() and (stage.physical / "ok.txt").read_bytes() == TEXT


def test_special_files_in_the_stage_are_left_out_and_named(project, tmp_path):
    os.mkfifo(project / "pipe")
    here = Path.cwd()
    os.chdir(project)
    try:
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind("sock")
            os.link(project / "ok.txt", project / "twin.txt")
            (project / "link-out").symlink_to("../outside")
            (project / "link-abs").symlink_to("/etc/hosts")
            (project / "sub").mkdir()
            git(project, "-C", "sub", "init", "-q")
            git(project, "-C", "sub", "-c", "user.name=t", "-c", "user.email=t@e.invalid", "commit", "-q", "--allow-empty", "-m", "x")
            git(project, "add", "-A")
            workspace = staged(project)
    finally:
        os.chdir(here)
    left = dict(workspace.left_out())
    for name in (b"pipe", b"sock", b"link-out", b"link-abs", b"sub", b"ok.txt", b"twin.txt"):
        assert name in left, (name, sorted(left))
    assert "special file" in left[b"pipe"] and "special file" in left[b"sock"] and "submodule" in left[b"sub"]
    assert "leaves the tree" in left[b"link-out"] and "more than one link" in left[b"twin.txt"]
    assert not any((workspace.master / name.decode()).exists() for name in left)
    assert (workspace.master / "src" / "app.py").read_bytes() == TEXT


def test_a_project_larger_than_the_old_ceilings_stages_and_a_sparse_file_stages_by_the_blocks_it_holds(project):
    with (project / "sparse.bin").open("wb") as stream:
        stream.truncate(source.FILE_LIMIT + (8 << 20))
    (project / "dense.bin").write_bytes(os.urandom(2 << 20))
    git(project, "add", "-A")
    workspace = staged(project, free=lambda _directory: 256 << 20)
    stage = workspace.begin()
    assert (stage.physical / "sparse.bin").stat().st_size == source.FILE_LIMIT + (8 << 20), "a file over the old per-file cap is staged"
    assert (stage.physical / "sparse.bin").stat().st_blocks * 512 < 1 << 20, "and it stays sparse"
    smaller = staged(project, free=lambda _directory: 1 << 20)
    with pytest.raises(ValueError, match=r"needs \d+ bytes.*has 1048576 free"):
        smaller.begin()
    assert not any(each.name.startswith("tool-") for each in smaller.directory.iterdir()), "a refused stage leaves nothing"
    tight = staged(project, free=lambda _directory: 4 << 20)
    assert tight.begin().physical.exists(), "the sparse file counts what it holds, the dense one its size"


def test_a_tree_of_small_files_stages_by_progress_and_a_stalled_capture_is_judged_by_its_stall(project, monkeypatch):
    for index in range(1500):
        (project / "many").mkdir(exist_ok=True)
        (project / "many" / f"f{index}.txt").write_bytes(b"%d\n" % index)
    git(project, "add", "-A")
    storage = Path(coding_workspace.source_snapshot.private_namespace(dict(os.environ), "poolhouse-launch-test-"))
    snapshot = source.SourceSnapshot(project, storage, strict=False)
    captured = snapshot.capture(storage / "out")
    assert snapshot.files >= 1500 and snapshot.bytes >= sum(entry.size for entry in captured.entries) and len(captured.entries) == 1502
    assert snapshot.strict is False
    strict = source.SourceSnapshot(project, storage)
    (project / "huge.bin").write_bytes(b"\0" * (source.FILE_LIMIT + 1))
    git(project, "add", "-A")
    with pytest.raises(RuntimeError, match="exceed bounds"):
        source.SourceSnapshot(project, storage).capture(storage / "bounded")
    assert source.SourceSnapshot(project, storage, strict=False).capture(storage / "unbounded").entries, "no ceiling for a coding stage"
    strict.close()
    snapshot.moved -= source.STALL_S + 1
    with pytest.raises(TimeoutError, match="no file visited"):
        snapshot.advance(1)
    snapshot.moved = time.monotonic()
    snapshot.advance(1)
    snapshot.deadline = 0
    snapshot.advance(1)
    with pytest.raises(TimeoutError, match="capture deadline exceeded"):
        _expired(project, storage)


def _expired(project, storage):
    bounded = source.SourceSnapshot(project, storage)
    bounded.deadline = 0
    bounded.advance(1)


def hostile_index(names: list[bytes]) -> bytes:
    """A version 2 index naming each of ``names`` for the empty blob, with a correct checksum, so Git reads it and the paths are the attack."""
    empty = bytes.fromhex("e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")
    rows = b"".join(
        (b"\0" * 24 + (0o100644).to_bytes(4, "big") + b"\0" * 12 + empty + min(len(name), 0xFFF).to_bytes(2, "big") + name + b"\0").ljust(
            (62 + len(name) + 8) // 8 * 8, b"\0") for name in names)
    body = b"DIRC" + (2).to_bytes(4, "big") + len(names).to_bytes(4, "big") + rows
    return body + hashlib.sha1(body, usedforsecurity=False).digest()


@pytest.mark.parametrize("name", [b"../escape", b".git/hooks/x", b"/etc/escape", pytest.param(b"a" * 4096, id="a-name-of-4096-bytes"), b"a/../../escape",
                                  b"x/./y", b"src//app.py"])
def test_a_hostile_index_in_the_stage_changes_nothing(project, tmp_path, name):
    workspace = staged(project)
    stage = workspace.begin()
    (stage.physical / ".git" / "index").write_bytes(hostile_index([name]))
    before = sorted(each.name for each in workspace.directory.iterdir())
    master = (workspace.master / "src" / "app.py").read_bytes()
    with pytest.raises((RuntimeError, ValueError, OSError)):
        workspace.finish(stage, nobody, writable=True)
    assert not (tmp_path / "escape").exists() and not Path("/etc/escape").exists()
    assert (workspace.master / "src" / "app.py").read_bytes() == master == (project / "src" / "app.py").read_bytes()
    assert workspace.revision == 0 and stat.S_ISDIR(project.stat().st_mode)
    assert sorted(each.name for each in workspace.directory.iterdir() if not each.name.startswith(("result-", "current-", "source-git-", "paths-"))) == [
        name for name in before if not name.startswith(("result-", "current-", "source-git-", "paths-"))], "no new stage, no new master"
