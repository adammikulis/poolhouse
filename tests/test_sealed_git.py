"""The git commands the push check runs read commits as stored, whatever the checkout's configuration, attributes and environment say."""

from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest
import sealed_git
from git_world import AGENT_PUSH, DEV, ROOT, World

from poolhouse.board import session
from poolhouse.workspace import landing

pytest_plugins = ["node_kit"]

AUDITED = ("scripts/hooks/pushed",)


@pytest.fixture
def world(tmp_path, workspace_node):
    return World(tmp_path, workspace_node)


def run(tree, *args, env=None, stdin=None, check=True):
    """``git -C tree args`` in the test's own environment plus ``env``; the completed process."""
    return subprocess.run(["git", "-C", str(tree), *args], capture_output=True, text=True, check=check, input=stdin,
                          env={**os.environ, **(env or {})})


def common_path(tree) -> Path:
    """The git directory the worktree ``tree`` shares."""
    return Path(run(tree, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())


def push(world, tree, sha: str, env) -> subprocess.CompletedProcess[str]:
    """``git push`` of ``sha`` to the development branch as an agent, through the installed hook."""
    return subprocess.run(["git", "push", "origin", f"{sha}:refs/heads/{DEV}"], cwd=tree, capture_output=True, text=True, check=False,
                          env={**world.env(world.pusher), **AGENT_PUSH, **env})


def test_python_that_does_not_parse_is_found_under_a_name_git_would_quote(world):
    world.install_hook()
    tree, sha = world.start("quoted", {"tests/test_caf\u00e9.py": "def (:\n"})
    refused = push(world, tree, sha, {})
    assert refused.returncode != 0 and "does not parse" in refused.stderr and "no-data-files" not in refused.stderr, refused.stderr
    assert world.proj.origin_head() != sha


def test_a_file_under_a_name_git_would_quote_is_pushed_when_it_is_ordinary_and_refused_when_it_is_data(world):
    world.install_hook()
    tree, sha = world.start("quoted-data", {"docs/donn\u00e9es.csv": "a,b\n"})
    refused = push(world, tree, sha, {})
    assert refused.returncode != 0 and "docs/donn\u00e9es.csv: a .csv file is data or weights" in refused.stderr, refused.stderr
    ordinary, fine = world.start("quoted-doc", {"docs/r\u00e9sum\u00e9.md": "text\n"})
    done = push(world, ordinary, fine, {})
    assert done.returncode == 0, done.stderr
    assert world.proj.origin_head() == fine


def test_a_command_that_reads_commits_is_refused_in_the_checkout(world):
    tree, sha = world.start("repository-context", {"docs/a.md": "changed\n"})
    for args in (("diff-tree", "-r", sha), ("merge-tree", "--write-tree", sha, sha), ("rev-list", sha), ("log", sha), ("show", sha)):
        with pytest.raises(ValueError, match="sealed"):
            sealed_git.git(tree, *args)
    assert sealed_git.sealed(tree, "rev-list", "--count", sha).stdout.strip() == "2"


def test_a_sealed_command_stores_nothing_in_the_checkout_unless_it_is_to(world):
    tree, _ = world.start("reads", {"docs/a.md": "changed\n"})
    kept = sealed_git.sealed(tree, "hash-object", "-w", "--stdin", stdin="read only\n").stdout.strip()
    assert run(tree, "cat-file", "-e", kept, check=False).returncode != 0
    stored = sealed_git.sealed(tree, "hash-object", "-w", "--stdin", stdin="stored\n", write=True).stdout.strip()
    assert run(tree, "cat-file", "-e", stored, check=False).returncode == 0


def test_a_shallow_file_does_not_cut_the_history_the_push_check_reads(world):
    tree, sha = world.start("shallow", {"docs/a.md": "changed\n"})
    (common_path(tree) / "shallow").write_text(f"{sha}\n", encoding="utf-8")
    assert run(tree, "rev-list", "--count", sha).stdout.strip() == "1", "the shallow file does not act on this clone"
    assert sealed_git.sealed(tree, "rev-list", "--count", sha).stdout.strip() == "2"


def test_only_the_git_helper_starts_git_for_the_push_check():
    found = {path: [n.lineno for n in ast.walk(ast.parse((ROOT / path).read_text(encoding="utf-8")))
                    if isinstance(n, ast.Constant) and n.value == "git"] for path in AUDITED}
    assert not any(found.values()), f"these files start git themselves, on these lines; use scripts/sealed_git.py: {found}"


def reject(world, name: str, sha: str, verdict: str = "reject") -> None:
    """A review of ``sha`` by the independent reviewer."""
    landing.review(world.node.session(world.reviewer), name, sha, verdict)


def test_a_standing_reject_of_a_pushed_commit_refuses_the_push_until_the_reviewer_replaces_it(world):
    world.install_hook()
    tree, sha = world.start("rejected", {"docs/a.md": "changed\n"})
    reject(world, "rejected", sha)
    refused = push(world, tree, sha, {})
    assert refused.returncode != 0 and f"{sha[:12]}, which an independent reviewer rejected" in refused.stderr, refused.stderr
    assert world.proj.origin_head() != sha
    reject(world, "rejected", sha, "accept")
    done = push(world, tree, sha, {})
    assert done.returncode == 0, done.stderr
    assert world.proj.origin_head() == sha


def test_a_reject_of_an_earlier_new_commit_refuses_a_push_whose_tip_is_another_commit(world):
    world.install_hook()
    tree, first = world.start("series", {"docs/a.md": "first\n"})
    world.proj.write(tree, "docs/b.md", "second\n")
    tip = world.proj.commit(tree, "feat: second")
    reject(world, "series", first)
    refused = push(world, tree, tip, {})
    assert refused.returncode != 0 and first[:12] in refused.stderr, refused.stderr
    assert world.proj.origin_head() != tip


def test_an_accept_a_review_of_another_commit_and_no_review_never_stop_a_push(world):
    world.install_hook()
    tree, sha = world.start("fine", {"docs/a.md": "changed\n"})
    _, other = world.start("elsewhere", {"docs/c.md": "other\n"})
    reject(world, "elsewhere", other)
    reject(world, "fine", sha, "accept")
    done = push(world, tree, sha, {})
    assert done.returncode == 0, done.stderr
    assert world.proj.origin_head() == sha


def test_a_board_that_cannot_be_asked_is_a_warning_and_the_push_goes_on(world, monkeypatch, capsys):
    loader = importlib.machinery.SourceFileLoader("pushed_hook", str(ROOT / "scripts/hooks/pushed"))
    hook = importlib.util.module_from_spec(importlib.util.spec_from_loader("pushed_hook", loader))
    loader.exec_module(hook)
    tree, sha = world.start("quiet", {"docs/a.md": "changed\n"})
    reject(world, "quiet", sha)
    monkeypatch.chdir(tree)

    def refuse(**_):
        raise OSError("no node")

    monkeypatch.setattr(session, "connect", refuse)
    assert hook.rejected(sha) == []
    assert "the board could not be asked for reviews (no node)" in capsys.readouterr().err
