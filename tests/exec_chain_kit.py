"""A project checkout and a home folder with credentials in them, and the helpers the harness exec-chain tests share."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from poolhouse import coding_policy, harnesshook
from poolhouse.harnesspolicy import after_downloads, decide
from poolhouse.interventions import Call
from poolhouse.net import git

ROLES = ("guarded", "guarded")
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
    return after_downloads(decide(coding_policy.Policy(role), Call("Bash", args), roots=world.roots), "Bash", args, world.roots, fetched)


def refuses(got):
    """A refusal at once, by the profile, of a call the classifier labels destructive or unsure."""
    return (got.action == "deny" and got.label in ("destructive", "unsure")
            and "an agent never opens one" not in got.reason)


def stops(got):
    return got.action == "deny"


@pytest.fixture
def raised():
    """Run the hook on a payload and return the reason it gives for refusing it (the words the agent is told)."""
    def run(payload, role="guarded", roots=("/w",)):
        out = harnesshook.pre(payload, harnesshook.Rail(coding_policy.Policy(role), "local-test", roots, ()))["hookSpecificOutput"]
        assert out["permissionDecision"] == "deny", out
        return out["permissionDecisionReason"]

    return run


@pytest.fixture
def session():
    """A hook session: each call returns the hook's answer."""
    def run(command, roots, agent="local-test", role="guarded", name="Bash"):
        payload = {"tool_name": name, "tool_input": {"command": command}}
        out = harnesshook.pre(payload, harnesshook.Rail(coding_policy.Policy(role), agent, roots, ()))
        return out["hookSpecificOutput"]["permissionDecision"]

    return run
