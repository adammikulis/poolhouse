"""What the tests of `poolhouse-delegate` share: a real node with a project and a lead session, and a `detach` that records instead of running.

    pytest_plugins = ["node_kit", "delegate_kit"]
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from harness_kit import checkouts

from poolhouse import caller, delegate, jobs, node_launch
from poolhouse.board import place, session


@pytest.fixture
def lead_node(workspace_node, tmp_path, monkeypatch):
    """A node, a project checkout it knows, and a lead session whose native id the command's environment names."""
    for key in (*caller.AGENT_MARKERS, session.AGENT_ENV, session.TOKEN_ENV, session.SUBAGENT_ENV, "POOLHOUSE_SESSION_ID",
                "POOLHOUSE_SESSION_HARNESS", "CODEX_THREAD_ID", place.BOARD_ENV):
        monkeypatch.delenv(key, raising=False)
    project = checkouts(tmp_path / "work").primary
    workspace_node.board = place.resolve(workspace_node.client, project)
    lead = workspace_node.member("lead-main")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", lead.session)
    monkeypatch.setattr(node_launch, "default_state", lambda: workspace_node.state)
    monkeypatch.delenv("POOLHOUSE_AUTHORITY_FLOOR", raising=False)
    monkeypatch.chdir(project)
    return workspace_node, lead, project


@pytest.fixture
def detached(monkeypatch):
    """`jobs.detach` as a record of what it was asked to run: the "process" is a sleeping child, no module runs and nothing leases a model."""
    asked: list[dict] = []
    children: list[subprocess.Popen] = []

    def fake(module, argv, **more):
        asked.append({"module": module, "argv": list(argv), "same_interpreter": more["same_interpreter"]})
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
        children.append(child)
        jobs.record(more["kind"], pid=child.pid, argv=argv, log=str(more["log"]), home=more["home"])
        return jobs.Detached(pid=child.pid, log=more["log"], command=(module, *argv), started="now")

    monkeypatch.setattr(delegate.jobs, "detach", fake)
    yield asked
    for child in children:
        child.kill()
        child.wait()


def said(capsys) -> str:
    seen = capsys.readouterr()
    return seen.out + seen.err


def started(*words: str, checked: bool = False, tree: bool = False) -> int:
    """`poolhouse-delegate start` on ``words``; unless ``checked``, of one attempt with no self-check, which is what a test of a single run reads;
    unless ``tree``, editing the project folder itself, which is where the tests of a run read what it changed."""
    plain = () if checked else ("--no-review", "--resumes", "0")
    return delegate.main(["start", *words, *plain, *(() if tree else ("--in-place",))])


def agent_names(node, lead):
    return [a.name for a in node.session(lead).agents() if a.parent == lead.name]
