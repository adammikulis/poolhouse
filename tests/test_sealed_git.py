"""The git commands the push check runs read commits as stored, whatever the checkout's configuration, attributes and environment say."""

from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest
import sealed_git
from git_world import AGENT_PUSH, DEV, ROOT, World, git

from poolhouse import node_launch
from poolhouse.board import client, place, session
from poolhouse.workspace import landing

pytest_plugins = ["node_kit"]

AUDITED = ("scripts/hooks/pushed",)
SENSITIVE = "src/poolhouse/credentials/example.py"


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


@pytest.fixture
def hook():
    """The push hook's module, loaded from its file."""
    loader = importlib.machinery.SourceFileLoader("pushed_hook", str(ROOT / "scripts/hooks/pushed"))
    loaded = importlib.util.module_from_spec(importlib.util.spec_from_loader("pushed_hook", loader))
    loader.exec_module(loaded)
    return loaded


def test_a_board_that_cannot_be_asked_is_a_warning_and_the_push_goes_on(world, hook, monkeypatch, capsys):
    tree, sha = world.start("quiet", {"docs/a.md": "changed\n"})
    reject(world, "quiet", sha)
    monkeypatch.chdir(tree)

    def refuse(**_):
        raise OSError("no node")

    monkeypatch.setattr(session, "connect", refuse)
    assert hook.rejected(sha) == []
    assert "the board could not be asked for reviews (no node)" in capsys.readouterr().err


def as_pusher(world, monkeypatch) -> None:
    """Run this process as the pusher of ``world``: its node, its board and its name."""
    monkeypatch.delenv(session.SUBAGENT_ENV, raising=False)
    monkeypatch.delenv(session.TOKEN_ENV, raising=False)
    for name, value in world.env(world.pusher).items():
        if name in {"POOLHOUSE_HOME", place.BOARD_ENV, session.AGENT_ENV}:
            monkeypatch.setenv(name, value)


def series(world, monkeypatch) -> tuple[str, str, str]:
    """Three commits on a branch, run as the pusher inside the checkout; the commits oldest first."""
    as_pusher(world, monkeypatch)
    tree, first = world.start("series3", {"docs/a.md": "first\n"})
    world.proj.write(tree, "docs/b.md", "second\n")
    second = world.proj.commit(tree, "feat: second")
    world.proj.write(tree, "docs/c.md", "third\n")
    third = world.proj.commit(tree, "feat: third")
    monkeypatch.chdir(tree)
    return first, second, third


def test_an_error_on_one_commit_keeps_the_rejects_found_on_the_others(world, hook, monkeypatch, capsys):
    first, second, third = series(world, monkeypatch)
    reject(world, "series3", third)
    reject(world, "series3", first)
    real = landing.accepts

    def accepts(asked, sha):
        if sha == second:
            raise ValueError("the node sent something odd")
        return real(asked, sha)

    monkeypatch.setattr(landing, "accepts", accepts)
    assert sorted(hook.rejected(third)) == sorted([first, third])
    assert f"{second[:12]}" in capsys.readouterr().err


def test_a_node_that_goes_away_mid_list_stops_the_questions_and_keeps_the_rejects_found(world, hook, monkeypatch, capsys):
    first, second, third = series(world, monkeypatch)
    reject(world, "series3", third)
    reject(world, "series3", first)
    real, asked_about = landing.accepts, []

    def accepts(asked, sha):
        asked_about.append(sha)
        if sha == second:
            raise OSError("the node went away")
        return real(asked, sha)

    monkeypatch.setattr(landing, "accepts", accepts)
    assert hook.rejected(third) == [third]
    assert asked_about == [third, second]
    assert "the node went away" in capsys.readouterr().err


class HungNode:
    """A listener on a node's socket that takes every connection and never answers."""

    def __init__(self) -> None:
        self.state = Path(tempfile.mkdtemp(prefix="hn"))
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.state / "node.sock"))
        self.listener.listen(8)
        self.held: list[socket.socket] = []
        threading.Thread(target=self.take, daemon=True).start()

    def take(self) -> None:
        while True:
            try:
                self.held.append(self.listener.accept()[0])
            except OSError:
                return

    def close(self) -> None:
        self.listener.close()
        for connection in self.held:
            connection.close()
        shutil.rmtree(self.state, ignore_errors=True)


def test_a_node_that_does_not_answer_holds_the_push_check_for_its_timeout_and_starts_no_node(world, hook, monkeypatch, capsys):
    hung, started, result = HungNode(), [], []
    monkeypatch.setattr(node_launch, "default_state", lambda: hung.state)
    monkeypatch.delenv(session.SUBAGENT_ENV, raising=False)
    monkeypatch.delenv(session.AGENT_ENV, raising=False)
    monkeypatch.setenv(session.TOKEN_ENV, "a-token-the-hung-node-never-reads")
    monkeypatch.setenv(place.BOARD_ENV, "demo")
    monkeypatch.setattr(node_launch, "ensure_node", lambda *args, **kwargs: started.append(args))
    monkeypatch.setattr(hook, "ASK_TIMEOUT_S", 1.0, raising=False)
    tree, sha = world.start("hung", {"docs/a.md": "changed\n"})
    monkeypatch.chdir(tree)
    asking = threading.Thread(target=lambda: result.append(hook.rejected(sha)), daemon=True)
    began = time.monotonic()
    try:
        asking.start()
        asking.join(15)
        assert not asking.is_alive(), "the push check is still waiting for a node that does not answer"
    finally:
        hung.close()
    assert time.monotonic() - began < 15 and result == [[]]
    assert started == [], "the push check started a node"
    assert "did not answer" in capsys.readouterr().err


def test_a_push_with_no_session_to_ask_as_says_so_on_stderr_and_goes_on(world, hook, monkeypatch, capsys):
    tree, sha = world.start("nobody", {"docs/a.md": "changed\n"})
    monkeypatch.chdir(tree)

    def refuse(**_):
        raise client.Denied("denied", "no token")

    monkeypatch.setattr(session, "connect", refuse)
    assert hook.rejected(sha) == []
    assert "the board could not be asked for reviews (no token)" in capsys.readouterr().err


def test_the_message_for_a_standing_reject_says_how_to_clear_it(world):
    world.install_hook()
    tree, sha = world.start("worded", {"docs/a.md": "changed\n"})
    reject(world, "worded", sha)
    refused = push(world, tree, sha, {})
    assert refused.returncode != 0 and "replace that commit" in refused.stderr and "new id" in refused.stderr, refused.stderr
    assert "Fix it in a new commit" not in refused.stderr, refused.stderr


def test_a_replace_ref_does_not_change_what_the_push_check_reads(world):
    tree, sha = world.start("replaced", {SENSITIVE: "VALUE = 1\n"})
    base = world.proj.origin_head()
    git(tree, "replace", sha, git(tree, "commit-tree", "-p", base, "-m", "docs: nothing", f"{base}^{{tree}}"))
    assert sealed_git.sealed(tree, "diff-tree", "-r", "--name-only", base, sha).stdout.strip() == SENSITIVE


def test_a_grafts_file_does_not_change_the_history_the_push_check_reads(world):
    tree, sha = world.start("grafted", {"docs/a.md": "changed\n"})
    info = common_path(tree) / "info"
    info.mkdir(exist_ok=True)
    (info / "grafts").write_text(f"{sha}\n", encoding="utf-8")
    assert sealed_git.sealed(tree, "rev-list", "--count", sha).stdout.strip() == "2"


def test_a_variable_that_moves_the_repository_does_not_reach_the_git_calls(world, monkeypatch, tmp_path):
    tree, _ = world.start("moved-repo", {"docs/a.md": "changed\n"})
    elsewhere = tmp_path / "elsewhere"
    git(tmp_path, "init", "-q", "-b", "other", str(elsewhere))
    monkeypatch.setenv("GIT_DIR", str(elsewhere / ".git"))
    assert sealed_git.out(tree, "branch", "--show-current") == "moved-repo"


def test_a_variable_that_adds_configuration_does_not_reach_the_git_calls(world, monkeypatch):
    tree, _ = world.start("configured-git", {"docs/a.md": "changed\n"})
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.editor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "/nowhere")
    assert sealed_git.git(tree, "config", "--get", "core.editor", check=False).stdout.strip() == ""
