"""A project checkout and a home folder with credentials in them, and the helpers the harness exec-chain tests share."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from poolhouse import harnesshook, requests
from poolhouse.harnesspolicy import after_downloads, decide
from poolhouse.net import git
from poolhouse.requests import store

ROLES = ("approve-first", "plan-and-go")
BODY = "import json\n\n\ndef load(path):\n    return json.loads(path.read_text())\n"


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for sub in (".ssh", ".aws", ".config/gh", ".poolhouse/node", ".gnupg", ".kube", ".docker", "Library/Keychains"):
        (home / sub).mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("private")
    proj = tmp_path / "proj"
    for sub in (".git/hooks", "src", "tests", "scripts", "docs"):
        (proj / sub).mkdir(parents=True)
    git.run(["init", "-q", str(proj)])
    (proj / ".env").write_text("TOKEN=1\n")
    (proj / "src" / "app.py").write_text("print(1)\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("POOLHOUSE_HOME", str(home / ".poolhouse"))
    return SimpleNamespace(home=home, proj=proj, roots=(str(proj),), tmp=tmp_path)


def bash(world, role, command, fetched=()):
    args = {"command": command}
    return after_downloads(decide(role, "Bash", args, roots=world.roots), "Bash", args, world.roots, fetched)


def asks(got):
    return got.action == "ask" and got.kind == "tool_call_destructive"


def stops(got):
    return got.action in ("ask", "deny")


class _Waiting:
    def __init__(self, approved):
        self.approved = approved

    def wait(self, timeout):
        return SimpleNamespace(approved=self.approved, why="", state="allowed" if self.approved else "expired")


@pytest.fixture
def raised(monkeypatch):
    """Run the hook on a payload and return the request the person would be shown, with its text escaped and cut as the inbox does."""
    def run(payload, role="plan-and-go", roots=("/w",)):
        seen = []
        monkeypatch.setattr(requests, "raise_request", lambda ask, inbox=None: seen.append(ask) or _Waiting(False))
        harnesshook.pre(payload, harnesshook.Rail(role, "local-test", roots, (), 0.2), requests.Inbox(memory=True))
        return store.build(seen[0], 0.0)

    return run


@pytest.fixture
def session(monkeypatch):
    """A hook session: each call returns the hook's answer and the requests raised so far; the person allows every request."""
    seen = []
    monkeypatch.setattr(requests, "raise_request", lambda ask, inbox=None: seen.append(ask) or _Waiting(True))

    def run(command, roots, agent="local-test", role="plan-and-go", name="Bash"):
        payload = {"tool_name": name, "tool_input": {"command": command}}
        out = harnesshook.pre(payload, harnesshook.Rail(role, agent, roots, (), 0.2), requests.Inbox(memory=True))
        return out["hookSpecificOutput"]["permissionDecision"], len(seen)

    return run
