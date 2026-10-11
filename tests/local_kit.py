"""Local agents on a real node: the person's session, a lead and others on one board, and a worker registered under the person."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse import node_launch
from poolhouse.board import person, session as sessions
from poolhouse.workspace import limits, localagent as la

MODEL = "qwen3.8-27b"


def build(node, monkeypatch) -> SimpleNamespace:
    """The node's board with the person's session, a lead (`task_authority` granted), a stranger and the saved-agent folder:
    ``kit.worker(name, **fields)`` registers and saves a local agent started by the person."""
    monkeypatch.setattr(node_launch, "default_state", lambda: node.state)
    base = limits.root()
    kit = SimpleNamespace(node=node, base=base, person=person.on(node.client, node.board))
    kit.lead = node.member("lead")
    node.allow(kit.lead, "task_authority")
    kit.stranger = node.member("stranger")
    kit.session = lambda who: node.session(who)

    def worker(name: str = "local-t", **fields) -> la.Agent:
        made = kit.person.register(sessions.Native(MODEL, fields.get("harness", "poolhouse-agent"), la.native_id(kit.person.name, name)))
        agent = la.Agent(name=name, identity=made.name, board=node.board, **{"model": "m", "model_name": "m", **fields})
        la.save(base, agent)
        return agent

    kit.worker = worker
    return kit


@pytest.fixture
def team(workspace_node, monkeypatch):
    """`build` on the running node."""
    return build(workspace_node, monkeypatch)


def waited(check, seconds: float = 20):
    """True once ``check()`` is; AssertionError when it never is."""
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return True
        time.sleep(0.05)
    raise AssertionError("did not happen")


def answers(s: sessions.Session, to: str, seq: str, seconds: float = 20):
    """The reply to message ``seq`` that session ``s`` receives, waiting up to ``seconds``."""
    end = time.time() + seconds
    while time.time() < end:
        got = [e for e in s.read_all(inbox=True) if e.fields.get("reply_id") == seq]
        if got:
            return got[0]
        time.sleep(0.05)
    raise AssertionError(f"no reply to {seq} for {to}")


__all__ = ["MODEL", "Path", "answers", "build", "team", "waited"]
