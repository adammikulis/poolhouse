"""A stale worktree stops the session that owns it: no landing gate counts it, the owner is refused more work, and
the doctor closes what a decision keeps without loss. Real git repositories."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from test_trees import REPO, commit, git, make_repo, tree

from poolhouse import trees, worktreerules

sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "hooks"))
import gates
import tree_watch

HOUR = 3600.0


@pytest.fixture
def main(tmp_path):
    return make_repo(tmp_path)


def left_behind(main: Path, name: str, owner: str = "left", stopped: float = 1.0, files: tuple[str, ...] = ()) -> Path:
    """A tree with one unlanded commit whose owner stopped at ``stopped``."""
    path = tree(main, name, owner=owner)
    commit(path, f"{name}.txt")
    for extra in files:
        (path / extra).parent.mkdir(parents=True, exist_ok=True)
        (path / extra).write_text("built")
    trees.update(main, lambda data: data["trees"][str(path.resolve())].update(state="finished", stopped=stopped))
    return path


def test_an_orphan_elsewhere_on_the_machine_fails_no_gate(main):
    path = left_behind(main, "stale")
    assert [Path(r["path"]).name for r in trees.past_grace(trees.rows(main, time.time()), trees.current_policy(main),
                                                           time.time())] == ["stale"]
    (main / "contracts").mkdir()
    for name in ("glossary-rules.toml", "glossary-allowlist.toml"):
        (main / "contracts" / name).write_text((REPO / "contracts" / name).read_text())
    (main / "pyproject.toml").write_text((REPO / "pyproject.toml").read_text())
    found = gates.run(main)
    assert "orphan-trees" not in {checker.NAME for checker in gates.checkers()}
    assert [f for sites in found.values() for f in sites if "stale" in f.path or "stale" in f.detail] == []
    assert path.exists()


def test_a_session_owes_its_own_late_trees_and_nobody_elses(main):
    left_behind(main, "stale", owner="a")
    left_behind(main, "other", owner="b")
    now = time.time()
    assert [Path(r["path"]).name for r in trees.owing(main, "a", now)] == ["stale"]
    assert trees.owing(main, "nobody", now) == [] and trees.owing(main, "", now) == []
    said = trees.refusal("a", trees.owing(main, "a", now))
    assert said.startswith("a owns 1 worktree ") and f"scripts/worktrees close {Path(main.parent / 'stale').resolve()} --bundle" in said
    assert "other" not in said and trees.refusal("a", []) == ""


def test_the_tree_on_the_branch_being_landed_and_a_tree_within_the_grace_are_not_owed(main):
    left_behind(main, "stale", owner="a")
    left_behind(main, "fresh", owner="a", stopped=time.time())
    now = time.time()
    assert [Path(r["path"]).name for r in trees.owing(main, "a", now)] == ["stale"]
    assert trees.owing(main, "a", now, keep="stale") == []


def test_a_dirty_orphan_is_owed_with_the_abandon_command(main):
    path = left_behind(main, "stale", owner="a")
    (path / "wip.txt").write_text("unsaved")
    assert f"scripts/worktrees close {path.resolve()} --abandon 'reason'" in trees.refusal("a", trees.owing(main, "a", time.time()))


def test_the_doctor_bundles_a_clean_orphan_keeps_its_work_and_leaves_a_dirty_one(main):
    clean = left_behind(main, "clean", owner="a")
    head = git(clean, "rev-parse", "HEAD")
    dirty = left_behind(main, "dirty", owner="b")
    (dirty / "wip.txt").write_text("unsaved")
    said: list[str] = []
    done = trees.mend(main, time.time(), said.append)
    assert [d["branch"] for d in done] == ["clean"] and done[0]["mode"] == "bundle"
    assert not clean.exists() and dirty.exists() and (dirty / "wip.txt").read_text() == "unsaved"
    git(main, "bundle", "verify", done[0]["bundle"])
    assert head in git(main, "bundle", "list-heads", done[0]["bundle"])
    assert git(main, "rev-parse", "clean") == head
    assert said == [f"queue doctor: closed clean (bundle): {clean.resolve()}"]


def test_the_doctor_leaves_a_tree_within_the_grace_and_never_abandons(main):
    soon = left_behind(main, "soon", owner="a", stopped=time.time() - HOUR)
    old = left_behind(main, "old", owner="b")
    (old / "wip.txt").write_text("unsaved")
    assert trees.mend(main, time.time(), lambda _: None) == []
    assert soon.exists() and old.exists()
    assert trees.recorded(main)["history"] == []


def test_the_doctor_closes_a_landed_tree_past_the_grace(main):
    path = tree(main, "done", owner="a")
    commit(path, "f1")
    git(main, "merge", "-q", "--ff-only", "done")
    trees.update(main, lambda data: data["trees"][str(path.resolve())].update(state="finished", stopped=1.0))
    done = trees.mend(main, time.time(), lambda _: None)
    assert [(d["branch"], d["mode"]) for d in done] == [("done", "landed")]
    assert not path.exists()


def test_a_second_doctor_pass_changes_nothing(main):
    clean = left_behind(main, "clean", owner="a")
    dirty = left_behind(main, "dirty", owner="b")
    (dirty / "wip.txt").write_text("unsaved")
    assert len(trees.mend(main, time.time(), lambda _: None)) == 1
    before = (trees.registry_path(main).read_text(), git(main, "worktree", "list"), sorted(p.name for p in
              (trees.registry_path(main).parent / "poolhouse-bundles").iterdir()))
    said: list[str] = []
    assert trees.mend(main, time.time(), said.append) == [] and said == []
    assert (trees.registry_path(main).read_text(), git(main, "worktree", "list"), sorted(p.name for p in
            (trees.registry_path(main).parent / "poolhouse-bundles").iterdir())) == before
    assert not clean.exists() and dirty.exists()


def repo_ignoring(tmp_path: Path, patterns: str) -> Path:
    """A repository whose .gitignore holds ``patterns``."""
    main = make_repo(tmp_path)
    (main / ".gitignore").write_text(patterns)
    git(main, "add", ".gitignore")
    git(main, "commit", "-qm", "ignore", "--no-verify")
    return main


BUILT_FILES = ("dist/app.whl", "tests/support/vendor/lib/x.py", "pkg/__pycache__/m.pyc", ".pytest_cache/v/cache")
IGNORED = "dist/\ntests/support/vendor/\n__pycache__/\n.pytest_cache/\n"


def test_close_discards_built_files_and_caches(tmp_path):
    main = repo_ignoring(tmp_path, IGNORED)
    path = left_behind(main, "built", owner="a", files=BUILT_FILES)
    done = trees.close(main, path, ("bundle", ""), time.time())
    assert done["mode"] == "bundle" and not path.exists()


def test_built_files_and_caches_do_not_stop_the_doctor(tmp_path):
    main = repo_ignoring(tmp_path, IGNORED)
    path = left_behind(main, "built", owner="a", files=BUILT_FILES)
    done = trees.mend(main, time.time(), lambda _: None)
    assert [d["branch"] for d in done] == ["built"] and not path.exists()


def test_an_ignored_file_that_is_not_rebuildable_still_blocks_a_removal(tmp_path):
    main = repo_ignoring(tmp_path, "*.key\n")
    path = left_behind(main, "secret", owner="a", files=("deploy.key",))
    with pytest.raises(trees.Refused, match=r"deploy\.key"):
        trees.close(main, path, ("bundle", ""), time.time())
    said: list[str] = []
    assert trees.mend(main, time.time(), said.append) == [] and path.exists()
    assert len(said) == 1 and "deploy.key" in said[0] and said[0].startswith("queue doctor: left secret")


def test_the_refusal_helper_of_the_hooks_names_the_session_behind_the_event(main, monkeypatch):
    path = left_behind(main, "stale", owner="worker-a")
    names = {"agent-1": "worker-a", "session-1": "lead", "agent-2": ""}
    monkeypatch.setattr(tree_watch.workspace_hook, "agent_name", lambda native, cwd="": names.get(native, ""))
    sub = {"cwd": str(main), "session_id": "session-1", "agent_id": "agent-1"}
    assert f"scripts/worktrees close {path.resolve()} --bundle" in tree_watch.late_refusal(sub)
    assert tree_watch.late_refusal({"cwd": str(main), "session_id": "session-1"}) == ""
    assert tree_watch.late_refusal({"cwd": str(main), "session_id": "session-1", "agent_id": "agent-2"}) == ""
    assert tree_watch.late_refusal({**sub, "cwd": str(main.parent / "nowhere")}) == ""


@pytest.mark.parametrize("command, adds", [
    ("git worktree add -b x ../x main", True), ("cd /tmp && git -C repo worktree add ../y", True),
    ("git worktree list", False), ("echo git worktree add", False), ("git commit -m 'worktree add'", False),
    ("git log --grep worktree add foo", False)])
def test_a_command_that_adds_a_worktree_is_told_from_one_that_does_not(command, adds):
    assert worktreerules.adds_a_worktree(command) is adds


def test_an_agent_call_asks_for_a_tree_by_its_isolation_or_its_definition(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "isolated.md").write_text("---\nname: isolated\nisolation: worktree\n---\nbody\n")
    (agents / "plain.md").write_text("---\nname: plain\nmodel: sonnet\n---\nbody\n")
    (agents.parent / "escaped.md").write_text("---\nisolation: worktree\n---\n")
    here = str(tmp_path)
    assert tree_watch.makes_a_tree({"isolation": "worktree"}, here)
    assert tree_watch.makes_a_tree({"subagent_type": "isolated"}, here)
    assert not tree_watch.makes_a_tree({"subagent_type": "plain"}, here)
    assert not tree_watch.makes_a_tree({"subagent_type": "../escaped"}, here)
    assert not tree_watch.makes_a_tree({"subagent_type": "missing"}, here) and not tree_watch.makes_a_tree({}, here)
