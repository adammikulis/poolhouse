"""Worktrees as git states them, on real repositories: landed or work, close and sweep."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from poolhouse import trees
from poolhouse.serve.process import working_directories

REPO = Path(__file__).resolve().parent.parent
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
IGNORED = "__pycache__/\nbuild/\ndist/\n.pytest_cache/\n.coverage\n*.key\n"


def git(where: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(where), *args], env=ENV, text=True, capture_output=True, check=False)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def commit(where: Path, name: str) -> None:
    (where / name).write_text(name)
    git(where, "add", name)
    git(where, "commit", "-qm", "c", "--no-verify")


def make_repo(tmp_path: Path) -> Path:
    """A repository on branch dev with one commit and a .gitignore."""
    root = tmp_path / "main"
    root.mkdir()
    git(root, "init", "-q", "-b", "dev")
    (root / ".gitignore").write_text(IGNORED)
    git(root, "add", ".gitignore")
    commit(root, "base")
    return root


@pytest.fixture
def main(tmp_path):
    return make_repo(tmp_path)


def tree(main: Path, name: str) -> Path:
    """A worktree beside the checkout on a new branch of the same name."""
    path = main.parent / name
    git(main, "worktree", "add", "-q", "-b", name, str(path))
    return path


LATER = 3600.0
OLD = 2 * 86400.0


def visible() -> list[str | None]:
    """The working directories of this user's processes that can be read: the same on every host, whatever else runs on it."""
    return [d for d in working_directories() if d is not None]


def standing(main: Path) -> dict[str, dict]:
    return {Path(r["path"]).name: r for r in trees.rows(main, time.time())}


def sweep(main: Path, later: float = LATER) -> list[str]:
    """The trees a sweep closes ``later`` seconds from now."""
    closed, stayed = trees.sweep(main, time.time() + later, visible)
    assert not stayed
    return [Path(done["path"]).name for done in closed]


def test_a_clean_tree_with_every_commit_in_development_is_landed(main):
    tree(main, "fresh")
    row = standing(main)["fresh"]
    assert row["landed"] and row["unlanded"] == 0 and row["dirty"] == 0 and row["branch"] == "fresh"
    assert not (main / ".git" / "poolhouse-trees.json").exists()


def test_a_commit_development_lacks_makes_the_tree_work_until_development_holds_it(main):
    path = tree(main, "work")
    commit(path, "f1")
    row = standing(main)["work"]
    assert not row["landed"] and row["unlanded"] == 1 and row["ahead"] == 1
    assert sweep(main) == [] and path.exists()
    git(main, "merge", "-q", "--ff-only", "work")
    assert standing(main)["work"]["landed"]
    assert sweep(main) == ["work"] and not path.exists()


def test_a_commit_development_holds_as_a_patch_is_landed_and_close_says_the_branch_stayed(main):
    path = tree(main, "picked")
    commit(path, "f1")
    commit(main, "advance")
    git(main, "cherry-pick", "picked")
    assert git(main, "merge-base", "dev", "picked") != git(main, "rev-parse", "picked")
    assert standing(main)["picked"]["landed"]
    done = trees.close(main, path, ("landed", ""))
    assert not path.exists() and "branch_kept" in done
    assert git(main, "branch", "--list", "picked")


def test_a_merge_development_lacks_is_unlanded_even_when_every_patch_is_in(main):
    other, merged = tree(main, "other"), tree(main, "merged")
    commit(other, "f1")
    git(main, "merge", "-q", "--ff-only", "other")
    git(merged, "merge", "-q", "--no-ff", "-m", "merge", "other")
    row = standing(main)["merged"]
    assert row["unlanded"] == 1 and not row["landed"]
    assert sweep(main) == ["other"] and merged.exists()


@pytest.mark.parametrize("kind", ["modified", "staged", "untracked", "ignored-key", "nested-repo", "nested-repo-in-build"])
def test_dirty_content_makes_the_tree_work_and_nothing_removes_it(main, kind):
    path = tree(main, "held")
    if kind == "modified":
        (path / "base").write_text("changed")
    elif kind == "staged":
        (path / "new").write_text("new")
        git(path, "add", "new")
    elif kind == "untracked":
        (path / "note").write_text("unsaved")
    elif kind == "ignored-key":
        (path / "private.key").write_text("secret")
    elif kind == "nested-repo":
        (path / "vendor").mkdir()
        git(path / "vendor", "init", "-q")
    else:
        (path / "build" / "inner").mkdir(parents=True)
        git(path / "build" / "inner", "init", "-q")
    assert not standing(main)["held"]["landed"] and standing(main)["held"]["dirty"] >= 1
    assert sweep(main) == []
    for decision in (("landed", ""), ("bundle", "")):
        with pytest.raises(trees.Refused):
            trees.close(main, path, decision)
    assert path.exists() and git(main, "branch", "--list", "held")


def test_files_a_build_or_a_test_run_recreates_do_not_make_a_tree_work(main):
    path = tree(main, "built")
    for name in ("__pycache__/m.pyc", "src/__pycache__/n.pyc", "build/out.o", "dist/w.whl", ".pytest_cache/v", ".coverage"):
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text("x")
    row = standing(main)["built"]
    assert row["landed"] and row["dirty"] == 0
    assert sweep(main) == ["built"] and not path.exists()


def test_an_unfinished_merge_keeps_the_tree(main):
    path = tree(main, "merging")
    other = tree(main, "rival")
    commit(path, "same")
    (other / "same").write_text("different")
    git(other, "add", "same")
    git(other, "commit", "-qm", "c", "--no-verify")
    done = subprocess.run(["git", "-C", str(path), "merge", "rival"], env=ENV, capture_output=True, text=True, check=False)
    assert done.returncode != 0
    assert not standing(main)["merging"]["landed"] and sweep(main) == []
    assert path.exists()


def test_sweep_removes_a_landed_tree_and_its_branch_once_it_is_old(main):
    path = tree(main, "done")
    commit(path, "f1")
    git(main, "merge", "-q", "--ff-only", "done")
    assert sweep(main, 0.0) == [] and path.exists()
    assert sweep(main, OLD) == ["done"]
    assert not path.exists() and not git(main, "branch", "--list", "done")


def test_sweep_keeps_a_young_landed_tree(main):
    path = tree(main, "young")
    assert sweep(main, 0.0) == [] and path.exists()
    assert sweep(main, trees.MIN_AGE_S - 60) == [] and path.exists()
    assert sweep(main, trees.MIN_AGE_S + 60) == ["young"]


def test_sweep_keeps_locked_trees_integration_trees_and_work(main):
    locked, work = tree(main, "locked"), tree(main, "work")
    git(main, "worktree", "add", "-q", "-b", "land/batch", str(main.parent / "batch"))
    batch = main.parent / "batch"
    git(main, "worktree", "lock", "--reason", "foreign operation", str(locked))
    commit(work, "f1")
    assert sweep(main) == []
    assert locked.exists() and work.exists() and batch.exists()


def test_sweep_removes_a_detached_landed_tree(main):
    path = main.parent / "loose"
    git(main, "worktree", "add", "-q", "--detach", str(path))
    assert standing(main)["loose"]["branch"] == "(detached)"
    assert sweep(main) == ["loose"] and not path.exists()


def test_sweep_reports_a_landed_tree_it_could_not_remove(main, monkeypatch):
    path = tree(main, "stuck")
    original = trees.git

    def failing(root, *args):
        if args[:2] == ("worktree", "remove"):
            raise RuntimeError("git worktree remove failed (exit 128): busy")
        return original(root, *args)

    monkeypatch.setattr(trees, "git", failing)
    closed, stayed = trees.sweep(main, time.time() + LATER, visible)
    assert closed == [] and stayed == [(str(path.resolve()), "git worktree remove failed (exit 128): busy")]
    assert path.exists()


def test_close_landed_refuses_work_and_names_the_other_ways(main):
    path = tree(main, "work")
    commit(path, "f1")
    (path / "scratch.txt").write_text("unsaved")
    with pytest.raises(trees.Refused, match=r"1 unlanded commits, 1 dirty files.*--bundle / --abandon"):
        trees.close(main, path, ("landed", ""))
    assert (path / "scratch.txt").read_text() == "unsaved"


def test_abandon_needs_a_reason_and_keeps_the_branch_with_it(main):
    path = tree(main, "stale")
    commit(path, "f1")
    with pytest.raises(trees.Refused, match="reason"):
        trees.close(main, path, ("abandon", " "))
    done = trees.close(main, path, ("abandon", "superseded by another branch"))
    assert not path.exists() and done["mode"] == "abandon"
    assert git(main, "rev-parse", "stale") == done["head"]
    assert git(main, "config", "branch.stale.description") == "abandoned: superseded by another branch"


def test_abandon_will_not_drop_unlanded_commits_that_belong_to_no_branch(main):
    path = main.parent / "loose"
    git(main, "worktree", "add", "-q", "--detach", str(path))
    commit(path, "f1")
    with pytest.raises(trees.Refused, match="--bundle"):
        trees.close(main, path, ("abandon", "why"))
    assert path.exists()


def test_the_bundle_is_created_and_verified_before_the_tree_goes(main):
    path = tree(main, "keep")
    commit(path, "f1")
    head = git(path, "rev-parse", "HEAD")
    done = trees.close(main, path, ("bundle", ""))
    assert not path.exists()
    bundle = Path(done["bundle"])
    git(main, "bundle", "verify", str(bundle))
    assert head in git(main, "bundle", "list-heads", str(bundle))
    assert git(main, "rev-parse", "keep") == head
    assert git(main, "config", "branch.keep.description") == f"bundled: {bundle}"
    assert not git(main, "for-each-ref", "refs/poolhouse")


def test_a_bundle_that_does_not_hold_the_tip_leaves_the_tree_and_no_reference(main, monkeypatch):
    path = tree(main, "unproven")
    commit(path, "f1")
    original = trees.git

    def empty(root, *args):
        return "" if args[:2] == ("bundle", "list-heads") else original(root, *args)

    monkeypatch.setattr(trees, "git", empty)
    with pytest.raises(trees.Refused, match="does not hold"):
        trees.close(main, path, ("bundle", ""))
    assert path.exists() and not git(main, "for-each-ref", "refs/poolhouse")


def test_a_commit_made_while_the_bundle_was_written_keeps_the_tree(main, monkeypatch):
    path = tree(main, "race")
    commit(path, "first")
    original = trees.bundle

    def moved(primary, row, tip):
        saved = original(primary, row, tip)
        commit(path, "second")
        return saved

    monkeypatch.setattr(trees, "bundle", moved)
    with pytest.raises(trees.Refused, match="changed HEAD"):
        trees.close(main, path, ("bundle", ""))
    assert path.exists() and git(main, "rev-parse", "race") == git(path, "rev-parse", "HEAD")


def test_bundle_refuses_dirty_work_and_a_tree_with_nothing_to_bundle(main):
    dirty, clean = tree(main, "dirty"), tree(main, "clean")
    commit(dirty, "f1")
    (dirty / "x").write_text("x")
    with pytest.raises(trees.Refused, match="dirty"):
        trees.close(main, dirty, ("bundle", ""))
    with pytest.raises(trees.Refused, match="no unlanded"):
        trees.close(main, clean, ("bundle", ""))
    assert dirty.exists() and clean.exists()


def test_close_never_removes_the_primary_checkout_or_the_tree_it_runs_in(main, monkeypatch):
    path = tree(main, "inside")
    with pytest.raises(trees.Refused, match="outside the tree"):
        trees.close(main, main, ("abandon", "why"))
    monkeypatch.chdir(path)
    with pytest.raises(trees.Refused, match="outside the tree"):
        trees.close(main, path, ("landed", ""))
    assert path.exists()


def test_close_refuses_a_path_that_is_not_a_worktree(main, tmp_path):
    with pytest.raises(trees.Refused, match="not a worktree"):
        trees.close(main, tmp_path, ("landed", ""))


def dead_pid() -> int:
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    return done.pid


def test_a_lock_naming_a_dead_process_does_not_hold_the_tree(main):
    path = tree(main, "stale")
    git(main, "worktree", "lock", "--reason", f"claude agent x (pid {dead_pid()} start Mon)", str(path))
    assert not next(t for t in trees.listing(main) if t.path == path.resolve()).locked
    assert trees.close(main, path, ("landed", ""))["branch"] == "stale"
    assert not path.exists()


def test_a_lock_naming_a_live_process_holds_the_tree(main):
    path = tree(main, "live")
    git(main, "worktree", "lock", "--reason", f"claude agent x (pid {os.getpid()} start Mon)", str(path))
    assert next(t for t in trees.listing(main) if t.path == path.resolve()).locked
    with pytest.raises(RuntimeError):
        trees.close(main, path, ("landed", ""))
    assert path.exists()


def test_close_preserves_a_locked_tree(main):
    path = tree(main, "locked")
    git(main, "worktree", "lock", "--reason", "foreign operation", str(path))
    with pytest.raises(RuntimeError):
        trees.close(main, path, ("landed", ""))
    assert path.exists() and git(main, "branch", "--list", "locked")


def test_close_uses_nonforce_remove_and_checks_the_tree_is_gone(main, monkeypatch):
    path = tree(main, "remove")
    original = trees.git
    calls = []

    def refuse(root, *args):
        calls.append(args)
        if args[:2] == ("worktree", "remove"):
            assert "--force" not in args
            return ""
        return original(root, *args)

    monkeypatch.setattr(trees, "git", refuse)
    with pytest.raises(trees.Refused, match="remains"):
        trees.close(main, path, ("landed", ""))
    assert path.exists() and not any("-D" in call for call in calls)


def test_rebuildable_caches_allow_a_normal_landed_close(main, monkeypatch):
    path = tree(main, "cached")
    (path / "__pycache__").mkdir()
    (path / "__pycache__" / "cache.pyc").write_bytes(b"cache")
    original = trees.git
    calls = []

    def checked(root, *args):
        calls.append(args)
        assert "--force" not in args and "-D" not in args
        return original(root, *args)

    monkeypatch.setattr(trees, "git", checked)
    assert trees.close(main, path, ("landed", ""))["mode"] == "landed"
    assert ("branch", "-d", "cached") in calls and not path.exists()


SCRIPT = ("import runpy, sys\n"
          "namespace = runpy.run_path(sys.argv[1], run_name='worktrees_under_test')\n"
          "script = namespace['main'].__globals__\n"
          "listing = script['working_directories']\n"
          "script['working_directories'] = lambda: [d for d in listing() if d is not None]\n"
          "raise SystemExit(script['main'](sys.argv[2:]))\n")


def run(main: Path, *args: str) -> subprocess.CompletedProcess:
    """scripts/worktrees as a command, with the processes whose directory cannot be read left out of its listing."""
    return subprocess.run([sys.executable, "-c", SCRIPT, str(REPO / "scripts" / "worktrees"), *args], cwd=main, env=ENV, text=True,
                          capture_output=True, check=False)


def test_the_script_lists_work_and_landed_trees(main):
    work = tree(main, "work")
    commit(work, "f1")
    (work / "note").write_text("x")
    tree(main, "fresh")
    out = run(main)
    assert out.returncode == 0, out.stderr
    assert "work  +1 -0" in out.stdout and "[work unlanded=1 dirty=1]" in out.stdout
    assert "[landed unlanded=0 dirty=0]" in out.stdout


def test_the_script_reports_an_unreadable_tree_and_exits_1(main):
    broken = tree(main, "broken")
    tree(main, "fine")
    (broken / ".git").write_text("not a gitdir pointer")
    out = run(main)
    assert out.returncode == 1 and "unreadable:" in out.stdout and "[landed unlanded=0 dirty=0]" in out.stdout


def test_the_script_closes_and_sweeps(main):
    work, done = tree(main, "work"), tree(main, "done")
    commit(work, "f1")
    assert run(main, "close", str(work), "--landed").returncode == 2 and work.exists()
    assert run(main, "close", str(work), "--abandon", "test").returncode == 0 and not work.exists()
    first = run(main, "sweep").stdout
    assert "swept" not in first and first.startswith("primary-sync:") and done.exists()
    out = run(main, "sweep", "--min-age", "0")
    assert out.returncode == 0 and "swept" in out.stdout and not done.exists()


@pytest.mark.parametrize("name", ["-evil", "--git-dir=x", "a b", "line\nfeed", "quote'\"", "$(touch x)"])
def test_a_tree_named_like_an_option_or_a_command_is_only_a_path_to_git(tmp_path, name):
    hostile = tmp_path / name
    hostile.mkdir()
    git(hostile, "init", "-q")
    assert Path(trees.git(hostile, "rev-parse", "--show-toplevel")).name == hostile.resolve().name
    with pytest.raises(RuntimeError):
        trees.git(tmp_path / ("missing " + name), "status")
    assert not (tmp_path / "x").exists()


HIDING = ["--assume-unchanged", "--skip-worktree"]


@pytest.mark.parametrize("flag", HIDING)
@pytest.mark.parametrize("edit", ["modified", "deleted"])
def test_an_edit_the_index_hides_still_makes_the_tree_work_and_nothing_removes_it(main, flag, edit):
    path = tree(main, "hidden")
    git(path, "update-index", flag, "base")
    if edit == "modified":
        (path / "base").write_text("unsaved edit")
    else:
        (path / "base").unlink()
    assert git(path, "status", "--porcelain") == ""
    row = standing(main)["hidden"]
    assert not row["landed"] and row["dirty"] == 1
    assert sweep(main, OLD) == []
    for decision in (("landed", ""), ("bundle", "")):
        with pytest.raises(trees.Refused):
            trees.close(main, path, decision)
    assert path.exists() and git(main, "branch", "--list", "hidden")
    assert (path / "base").exists() == (edit == "modified")


def test_an_intent_to_add_file_makes_the_tree_work(main):
    # exempt: git status already lists an intent-to-add entry, so this holds on the code before the change too
    path = tree(main, "intent")
    (path / "new").write_text("n")
    git(path, "add", "-N", "new")
    assert not standing(main)["intent"]["landed"] and sweep(main, OLD) == [] and path.exists()


def test_a_submodule_edit_hidden_by_the_ignore_setting_makes_the_tree_work(main, tmp_path):
    inner = tmp_path / "inner"
    inner.mkdir()
    git(inner, "init", "-q", "-b", "dev")
    commit(inner, "x")
    git(main, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(inner), "sub")
    git(main, "commit", "-qm", "sub", "--no-verify")
    path = tree(main, "withsub")
    git(path, "-c", "protocol.file.allow=always", "submodule", "update", "--init", "-q")
    git(path, "config", "submodule.sub.ignore", "all")
    (path / "sub" / "x").write_text("unsaved edit")
    assert git(path, "status", "--porcelain") == ""
    assert not standing(main)["withsub"]["landed"] and sweep(main, OLD) == []
    assert (path / "sub" / "x").read_text() == "unsaved edit"


def test_sweep_leaves_a_tree_a_live_process_is_working_in(main):
    busy, idle = tree(main, "busy"), tree(main, "idle")
    (busy / "deep").mkdir()
    process = subprocess.Popen(["sleep", "60"], cwd=busy / "deep")
    try:
        assert sweep(main, OLD) == ["idle"]
        assert busy.exists() and not idle.exists() and git(main, "branch", "--list", "busy")
    finally:
        process.kill()
        process.wait()
    assert sweep(main, OLD) == ["busy"]


def test_sweep_leaves_every_tree_while_a_process_of_this_user_hides_its_directory(main, monkeypatch):
    path = tree(main, "hidden")
    process = subprocess.Popen(["sleep", "60"], cwd=path)
    real = psutil.Process.cwd

    def cwd(self):
        if self.pid == process.pid:
            raise psutil.AccessDenied(self.pid)
        return real(self)

    monkeypatch.setattr(psutil.Process, "cwd", cwd)
    try:
        assert None in working_directories()
        closed, stayed = trees.sweep(main, time.time() + OLD, working_directories)
        assert closed == [] and stayed == [] and path.exists()
    finally:
        process.kill()
        process.wait()


def test_sweep_removes_a_young_tree_and_keeps_its_branch(main):
    path = tree(main, "fresh")
    closed, stayed = trees.sweep(main, time.time() + LATER, visible)
    assert not stayed and not path.exists()
    assert "day" in closed[0]["branch_kept"] and git(main, "branch", "--list", "fresh")
    again = tree(main, "older")
    closed, _ = trees.sweep(main, time.time() + OLD, visible)
    assert "branch_kept" not in closed[0] and not git(main, "branch", "--list", "older") and not again.exists()


def test_one_unreadable_tree_is_reported_and_the_rest_are_listed(main):
    broken, fine = tree(main, "broken"), tree(main, "fine")
    (broken / ".git").write_text("not a gitdir pointer")
    found = standing(main)
    assert found["fine"]["landed"] and "unreadable" not in found["fine"]
    row = found["broken"]
    assert not row["landed"] and row["unreadable"] and row["branch"] == "broken" and row["path"] == str(broken.resolve())
    assert sweep(main, OLD) == ["fine"] and broken.exists() and fine.parent == broken.parent


@pytest.mark.parametrize("kind", ["modified", "staged", "untracked", "ignored-key", "nested-repo", "hidden"])
def test_abandon_removes_a_dirty_tree_after_writing_what_it_held_next_to_the_bundles(main, kind):
    path = tree(main, "held")
    if kind == "modified":
        (path / "base").write_text("changed text")
    elif kind == "staged":
        (path / "new").write_text("new text")
        git(path, "add", "new")
    elif kind == "untracked":
        (path / "note").write_text("unsaved text")
    elif kind == "ignored-key":
        (path / "private.key").write_text("secret text")
    elif kind == "nested-repo":
        (path / "vendor").mkdir()
        git(path / "vendor", "init", "-q")
        (path / "vendor" / "inner").write_text("inner text")
    else:
        git(path, "update-index", "--assume-unchanged", "base")
        (path / "base").write_text("hidden text")
    done = trees.close(main, path, ("abandon", "not wanted"))
    assert not path.exists() and git(main, "config", "branch.held.description") == "abandoned: not wanted"
    saved = [Path(name) for name in done["salvage"]]
    assert saved and all(p.exists() and p.parent.name == "poolhouse-bundles" for p in saved)
    blob = b"".join(p.read_bytes() for p in saved)
    assert {"modified": b"changed text", "staged": b"new text", "untracked": b"unsaved text",
            "ignored-key": b"secret text", "nested-repo": b"inner text", "hidden": b"hidden text"}[kind] in blob


def test_abandon_of_a_clean_tree_writes_nothing(main):
    path = tree(main, "clean")
    assert trees.close(main, path, ("abandon", "why"))["salvage"] == []
    assert not (Path(git(main, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "poolhouse-bundles").exists()


def test_abandon_keeps_the_tree_when_what_it_held_cannot_be_written(main, monkeypatch):
    path = tree(main, "unsaved")
    (path / "note").write_text("unsaved text")

    def refuse(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(trees.tarfile, "open", refuse)
    with pytest.raises(trees.Refused, match="disk full"):
        trees.close(main, path, ("abandon", "why"))
    assert (path / "note").read_text() == "unsaved text"
