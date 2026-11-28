"""The git history a stage is given (`isolation.history`): every command that builds a tree works in it, though it holds no byte of a credential.

Real git on real repositories and the launcher's real staging; no confinement and no model. Every credential here is a marked fake.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from poolhouse.activity import source_snapshot
from tests.isolation_world import Sink, begin, git, private_storage, write

pytestmark = pytest.mark.redteam

CANARY = "NOT-A-REAL-CREDENTIAL-canary"
HELD = {"contracts/evidence/credentials.json": f'{{"key": "{CANARY}-evidence"}}\n', ".env": f"TOKEN={CANARY}-env\n",
        "deploy/only/.env": f"TOKEN={CANARY}-alone\n"}
"""Committed files under credential names: one beside an ordinary file, one at the root, one alone in its folders."""
PLAIN = {"contracts/evidence/schema.json": "{}\n", "pkg/__init__.py": "", "config/.env": "", "src/app.py": "x = 1\n"}
"""Committed files the stage is given, and one empty file under a credential name, whose content an ordinary empty file shares."""


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    private_storage(tmp_path, monkeypatch)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    for name, text in {**HELD, **PLAIN}.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    git(root, "init", "-q")
    git(root, "add", "-f", "-A")
    git(root, "commit", "-qm", "first")
    (root / ".env").write_text(f"TOKEN={CANARY}-env-two\n")
    git(root, "add", "-f", ".env")
    git(root, "commit", "-qm", "a commit that changed only a credential")
    (root / "src" / "app.py").write_text("x = 2\n")
    git(root, "commit", "-qam", "base")
    return root


def run(where, *words, env=None, given=b""):
    return subprocess.run([str(source_snapshot.actual_git()), *words], cwd=where, env={**source_snapshot.git_environment(), **(env or {})},
                          input=given, capture_output=True, timeout=60, check=False)


def said(where, *words, env=None):
    done = run(where, *words, env=env)
    assert done.returncode == 0, (words, done.stderr.decode())
    return done.stdout.decode()


def snapshot(stage, tmp_path, name):
    """What a coding tool's snapshot does with the stage: a repository of its own that borrows the stage's objects and starts from a copy of its
    index, takes the files as they are now, and writes the tree."""
    made = tmp_path / name
    said(tmp_path, "init", "-q", "--bare", str(made))
    (made / "objects" / "info" / "alternates").write_text(f"{stage / '.git' / 'objects'}\n")
    shutil.copyfile(stage / ".git" / "index", made / "index")
    words = ("--git-dir", str(made), "--work-tree", str(stage))
    said(stage, *words, "add", "-A")
    return said(stage, *words, "write-tree").strip()


def test_a_snapshot_and_a_tree_of_the_stage_are_written_and_no_byte_of_a_committed_credential_is_in_it_by_any_name(project, tmp_path):
    stage = begin(project, tmp_path, Sink()).stage.physical
    assert not [name for name in HELD if (stage / name).exists()]
    assert said(stage, "write-tree").strip(), "the stage's own index is a tree"
    first = snapshot(stage, tmp_path, "snapshot-one")
    every = [said(project, "rev-parse", f"{commit}:{name}").strip() for commit in ("HEAD", "HEAD~2") for name in HELD]
    for plain in ({}, {"GIT_NO_REPLACE_OBJECTS": "1"}):
        assert [oid for oid in every if run(stage, "cat-file", "-e", oid, env=plain).returncode == 0] == [], plain
    assert CANARY.encode() not in run(stage, "cat-file", "--batch-all-objects", "--batch").stdout
    assert run(stage, "cat-file", "-p", "HEAD:.env", env={"GIT_NO_REPLACE_OBJECTS": "1"}).returncode, "the original tree names it and has no bytes"
    (stage / "contracts" / "evidence" / "schema.json").write_text('{"changed": 1}\n')   # beside the credential's name
    said(stage, "add", "-A")
    assert said(stage, "write-tree").strip() != first and snapshot(stage, tmp_path, "snapshot-two") != first


def test_the_stage_shows_no_change_and_no_deletion_of_a_credential_in_its_status_diffs_or_history(project, tmp_path):
    stage = begin(project, tmp_path, Sink()).stage.physical
    assert said(stage, "status", "--porcelain") == "" and said(stage, "diff", "HEAD") == "" and said(stage, "diff", "--cached") == ""
    log = said(stage, "log", "--name-status", "--format=%s")
    assert [line for line in log.splitlines() if line.startswith("D\t")] == [] and ".env\n" not in log.replace("config/.env\n", ""), log
    assert said(stage, "log", "-p", "--format=%s") and said(stage, "show", "HEAD~1"), "a commit that changed only a credential is shown"
    assert said(stage, "rev-parse", "HEAD") == said(project, "rev-parse", "HEAD"), "the commits are the project's own"
    listed = said(stage, "ls-tree", "-r", "--name-only", "HEAD").split()
    assert sorted(listed) == ["contracts/evidence/schema.json", "pkg/__init__.py", "src/app.py"] == sorted(said(stage, "ls-files").split())
    assert said(stage, "cat-file", "-p", "HEAD:pkg/__init__.py") == "", "content an ordinary file also holds is there"


def test_a_publication_takes_the_credential_the_stage_never_had_for_neither_a_deletion_nor_a_change(project, tmp_path):
    session = begin(project, tmp_path, Sink())
    stage = session.stage.physical
    assert snapshot(stage, tmp_path, "snapshot")
    said(stage, "add", "-A")
    write(session, "src/app.py", "x = 3\n")
    turn = session.publish_turn()
    assert turn.applied == ("src/app.py",) and turn.refused == () and turn.conflicts == (), turn
    assert {name: (project / name).read_text() for name in ("contracts/evidence/credentials.json", "deploy/only/.env")} == {
        name: HELD[name] for name in ("contracts/evidence/credentials.json", "deploy/only/.env")}
    assert (project / ".env").read_text() == f"TOKEN={CANARY}-env-two\n"
    shutil.rmtree(stage / "contracts")
    turn = session.publish_turn()
    assert turn.refused == () and (project / "contracts" / "evidence" / "credentials.json").exists(), "removing its folder removes only what the stage had"
    assert not (project / "contracts" / "evidence" / "schema.json").exists()
