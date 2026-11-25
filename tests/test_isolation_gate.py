"""Who may widen a coding session: the confined, the agents that are not the lead, the lead, a terminal, and the `prod` preset.

Real processes and a real node: a confined caller is a real child under the real Seatbelt with a real pty, and the lead and a subagent are
sessions registered on a real node. A refusal is immediate and carries a plain sentence; nothing waits and no test answers anything.
"""

from __future__ import annotations

import json
import os
import pty
import select
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from poolhouse import authority, caller, node_launch, sentinel
from poolhouse.caller import AgentRefused
from poolhouse.isolation import (
    Arguments,
    IsolationRefused,
    check_on,
    decide_launch,
    policy as policies,
    require_widening,
)
from poolhouse.isolation.policy import ProjectPolicy
from poolhouse.isolation.refusals import sentence
from poolhouse.platform import start_process
from poolhouse.sandbox import wrapped
from tests.isolation_kit import SOURCE, person_home, registered_project
from tests.sandbox_kit import policy

pytest_plugins = ["node_kit"]
MARKERS = ("CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE", "CODEX_THREAD_ID", "CLAUDE_CODE_SESSION_ID", "POOLHOUSE_SESSION_ID")
EVERY = Arguments(read=("/usr/share",), toolchain=("/usr/bin",), allow_net=("pypi.org",), publish=("src/app.py",), in_place=True,
                  project="/tmp", protect=("/tmp/x",))


@pytest.fixture
def terminal(tmp_path, monkeypatch, workspace_node):
    """A process outside any agent on a real node: no marker and no session above it, the gates delegated, a home with credential folders, the
    node's root as the state root, and a git project the node holds."""
    for name in (*MARKERS, "POOLHOUSE_WORKSPACE_AGENT", "POOLHOUSE_SUBAGENT", authority.FLOOR_ENV):
        monkeypatch.delenv(name, raising=False)
    person_home(tmp_path, monkeypatch)
    monkeypatch.setenv("POOLHOUSE_HOME", str(workspace_node.state.parent))
    monkeypatch.setenv("POOLHOUSE_BOARD", workspace_node.board)
    return registered_project(tmp_path, workspace_node)


@pytest.fixture
def lead(terminal, workspace_node, monkeypatch):
    """The live main agent session of a real node: marked, named, authenticated by its token."""
    actor = workspace_node.member("lead")
    monkeypatch.setattr(node_launch, "default_state", lambda: workspace_node.state)
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_AGENT", actor.name)
    monkeypatch.setenv("CLAUDECODE", "1")
    return actor


@pytest.fixture
def subagent(lead, workspace_node, monkeypatch):
    """A helper of the lead on the same node, as a subagent runs the launcher in its own shell."""
    kid = workspace_node.member("kid", parent=lead)
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_AGENT", kid.name)
    return kid


def widen(project: str, **changes) -> object:
    return decide_launch(Arguments(read=("/usr/share",), **changes), project=project)


PROGRAM = """
import pytest  # a test run: the state root may be moved to the node of the test
import json, os, sys
from poolhouse import caller
from poolhouse.isolation import Arguments, decide_launch
os.dup2(1, 7)
try:
    os.setsid()
except OSError:
    pass
for name in %(markers)r:
    os.environ.pop(name, None)
said = {"tty": [os.isatty(each) for each in (0, 1, 2, 7)]}
try:
    caller.refuse_agent("a launcher control", "the person's own", {})
    said["refuse_agent"] = "passed"
except caller.AgentRefused as refused:
    said["refuse_agent"] = "refused"
for label, arguments in %(arguments)s:
    try:
        decide_launch(arguments, project=%(project)r, env={}, registered=lambda path: True)
        said[label] = "passed"
    except caller.AgentRefused:
        said[label] = "refused"
os.write(7, ("\\n" + json.dumps(said) + "\\n").encode())
"""


def pty_report(project: str, tmp_path: Path, confined: bool, root: Path | None = None) -> dict:
    """What a process that holds a pty as all of its stdio, a master answering yes and no marker finds when it asks to widen a session."""
    arguments = [(label, f"Arguments({field}={value!r})") for label, field, value in (
        ("read", "read", ("/usr/share",)), ("toolchain", "toolchain", ("/usr/bin",)), ("net", "allow_net", ("pypi.org",)),
        ("publish", "publish", ("src/app.py",)), ("in-place", "in_place", True), ("project", "project", "/tmp"), ("protect", "protect", ("/tmp/x",)),
        ("on", "on", "http://127.0.0.1:1"), ("as", "parent", "somebody"))]
    code = PROGRAM % {"markers": MARKERS, "project": project, "arguments": "[" + ", ".join(f"({a!r}, {b})" for a, b in arguments) + "]"}
    argv = [os.path.realpath(sys.executable), "-I", "-c", f"import sys; sys.path.insert(0, {SOURCE!r})\n" + code]
    env = {"POOLHOUSE_HOME": str(root or tmp_path / "state"), "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring", "HOME": str(tmp_path),
           "PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"}
    full, tag, backend = wrapped(argv, policy(read=[SOURCE, tmp_path], python=True, env=env)) if confined else (argv, "", None)
    master, slave = pty.openpty()
    child = start_process(full, stdin=slave, stdout=slave, stderr=slave, env=env, cwd=str(tmp_path))
    os.close(slave)
    os.write(master, b"yes\n")
    heard, deadline = b"", time.monotonic() + 60
    while time.monotonic() < deadline:
        if select.select([master], [], [], 0.2)[0]:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            heard += chunk
        elif child.poll() is not None:
            break
    child.wait(timeout=10)
    os.close(master)
    if backend is not None:
        backend.finish(tag)
    shown = [line for line in heard.decode(errors="replace").splitlines() if line.startswith("{")]
    assert shown, f"the program said nothing: {heard.decode(errors='replace')[-1500:]}"
    return json.loads(shown[-1])


def test_a_pty_given_as_stdio_is_not_a_person(tmp_path, terminal, workspace_node):
    """The attacker owns the pty its stdio is, its master answers yes, it dropped the markers and left its session: asked from outside a
    confinement the very same calls pass as a terminal's do, and from inside one every control and the caller check refuse at once."""
    (tmp_path / "state").mkdir(exist_ok=True)
    outside = pty_report(terminal, tmp_path, confined=False, root=workspace_node.state.parent)
    assert outside["tty"] == [True] * 4 and outside["refuse_agent"] == "passed", "the control: without a confinement this is a terminal"
    assert outside["read"] == "passed" and outside["in-place"] == "passed", outside
    inside = pty_report(terminal, tmp_path, confined=True)
    assert inside["tty"] == [True] * 4, "the confined process does hold a pty as all of its stdio and a duplicate of it"
    assert inside["refuse_agent"] == "refused"
    assert {label: state for label, state in inside.items() if label not in ("tty", "on", "as") and state != "refused"} == {}
    assert inside["on"] == "refused" and inside["as"] == "refused"


def test_a_launch_with_no_widening_asks_the_gate_nothing_and_signals_nothing(terminal):
    seen = []
    launch = decide_launch(Arguments(), project=terminal, on_event=lambda name, fields: seen.append(name))
    assert not launch.widened and launch.grants == () and seen == []


def test_a_non_loopback_server_is_refused_for_every_caller_and_a_numeric_loopback_one_is_not(terminal):
    for url in ("http://10.0.0.5:8080", "http://localhost:8080", "http://example.test", "http://[::2]:1", "not a url", ""):
        with pytest.raises(IsolationRefused, match="can only use a model server on this computer"):
            check_on(url)
        if url:
            with pytest.raises(IsolationRefused):
                decide_launch(Arguments(on=url), project=terminal)
    for url in ("http://127.0.0.1:8080", "http://127.5.5.5:1", "http://[::1]:8080"):
        check_on(url)
        assert not decide_launch(Arguments(on=url), project=terminal).widened


def test_a_launcher_run_by_any_agent_but_the_lead_or_inside_a_confinement_refuses_every_widening_argument_and_unregistered_projects_and_a_non_loopback_on(
        terminal, tmp_path, monkeypatch, request):
    for field, value in (("read", ("/usr/share",)), ("toolchain", ("/usr/bin",)), ("allow_net", ("pypi.org",)), ("publish", ("src/app.py",)),
                         ("in_place", True), ("project", "/tmp"), ("protect", ("/tmp/x",))):
        with pytest.raises(AgentRefused):  # a caller outside the lead: marked, and no session the node knows
            monkeypatch.setenv("CLAUDECODE", "1")
            decide_launch(Arguments(**{field: value}), project=terminal)
        monkeypatch.delenv("CLAUDECODE")
    assert not decide_launch(Arguments(), project=terminal).widened, "no widening argument: the default reach is not refused"
    (tmp_path / "state").mkdir(exist_ok=True)
    inside = pty_report(terminal, tmp_path, confined=True)
    assert {state for label, state in inside.items() if label != "tty"} == {"refused"}, "a confined caller gets none of the arguments or controls"
    subagent = request.getfixturevalue("subagent")
    with pytest.raises(AgentRefused, match="only the session that launches coding agents"):
        decide_launch(EVERY, project=terminal)
    assert subagent.parent, "a helper of the lead, which the node knows by its parent"


def test_the_lead_passes_what_its_task_names_and_is_refused_a_project_that_is_not_registered_or_a_server_outside_this_computer(lead, terminal):
    launch = decide_launch(Arguments(allow_net=("pypi.org",)), project=terminal)
    assert launch.widened and [each.granted for each in launch.decisions] == [True]
    with pytest.raises(IsolationRefused, match="not a registered project"):
        decide_launch(Arguments(project="/definitely/not/registered"), project=terminal)
    decide_launch(Arguments(project=terminal), project=terminal)
    with pytest.raises(IsolationRefused, match="model server on this computer"):
        decide_launch(Arguments(on="http://192.0.2.7:1"), project=terminal)


TERMINAL = """
import pytest  # a test run: the state root may be moved to the node of the test
from poolhouse.isolation import Arguments, decide_launch
launch = decide_launch(Arguments(allow_net=("pypi.org",)), project=%(project)r)
print("TERMINAL-PASSED" if launch.widened else "TERMINAL-NOT-WIDENED")
"""


def test_in_the_prod_preset_the_grant_gate_reads_person_and_the_lead_is_refused_at_once_while_a_terminal_outside_any_agent_passes(
        terminal, workspace_node, tmp_path, monkeypatch, request):
    authority.set_state(["isolation.grant"], authority.PERSON, by="lead", env={})
    assert authority.state_of("isolation.grant") == authority.PERSON
    assert not [each for each in authority.show()["gates"] if each["gate"] == "isolation.grant" and each["state"] != authority.PERSON]
    require_widening("a wider launch", {}, terminal)
    lead = request.getfixturevalue("lead")
    authority.set_state(["isolation.grant"], authority.PERSON, by=lead.name)
    began = time.monotonic()
    with pytest.raises(AgentRefused, match="held back from agents"):
        require_widening("a wider launch", None, terminal)
    assert time.monotonic() - began < 2
    assert any(row["event"] == "refused" and "agent" in row["who"] for row in caller.audit_rows())
    monkeypatch.delenv("CLAUDECODE")
    with pytest.raises(AgentRefused, match="held back from agents"):
        require_widening("a wider launch", {}, terminal)  # no marker at all, in the process tree of the lead: still an agent
    code, shown, errors = workspace_node.terminal(program=[sys.executable, "-c", TERMINAL % {"project": terminal}])
    assert "TERMINAL-PASSED" in shown, (code, shown, errors)
    assert authority.state_of("isolation.grant") == authority.PERSON, "a terminal outside any agent passes the person gate"


def test_the_floor_of_the_suite_reads_every_gate_as_the_persons(terminal, monkeypatch):
    monkeypatch.setenv(authority.FLOOR_ENV, "person")
    assert authority.state_of("isolation.grant") == authority.PERSON
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(AgentRefused, match="held back from agents"):
        require_widening("a wider launch")


def become(kind: str, request) -> None:
    if kind == "lead":
        request.getfixturevalue("lead")


@pytest.mark.parametrize("kind", ["terminal", "lead"])
@pytest.mark.parametrize("source", ["task", "policy"])
def test_the_caps_hold_for_every_caller_and_every_source(terminal, request, monkeypatch, kind, source):
    named = ProjectPolicy(net=("evil.example", "pypi.org"), publish=("Makefile", "src/app.py", "package.json", ".github/CODEOWNERS"),
                          read=("/etc",), in_place=True)
    policies.save(terminal, named)
    become(kind, request)
    attempts = [Arguments(in_place=True, allow_net=("pypi.org",)), Arguments(allow_net=("evil.example",)), Arguments(publish=("Makefile",)),
                Arguments(publish=("package.json",)), Arguments(publish=(".github/CODEOWNERS",)), Arguments(read=("/etc",))]
    for arguments in attempts:
        launch = decide_launch(replace(arguments, source=source), project=terminal)
        assert not launch.widened and [each for each in launch.decisions if each.grant is not None] == [], arguments
    in_place_only = decide_launch(Arguments(in_place=True, source=source), project=terminal)
    assert in_place_only.widened and in_place_only.unusual, "alone it is allowed by a policy that names it, and it is marked unusual"


def test_every_widened_launch_sends_the_sentinel_one_signal_and_in_place_and_net_with_publish_are_marked_unusual(terminal):
    node = sentinel.default()
    heard = []
    node.bus.subscribe(heard.append)
    asked = {}
    first = decide_launch(Arguments(allow_net=("pypi.org",), read=("/usr/share",)), project=terminal, on_event=lambda n, f: asked.update(n=n, f=f))
    ours = [event for event in heard if event.kind == "isolation.widened"]
    assert first.widened and len(ours) == 1 and asked["n"] == "isolation.widened"
    assert ours[0].severity.name == "NOTICE" and not first.unusual
    assert set(ours[0].evidence["kinds"]) <= {"net", "read"} and "pypi.org" not in json.dumps(ours[0].evidence), "digests, never subjects"
    for arguments in (Arguments(in_place=True), Arguments(allow_net=("pypi.org",), publish=("src/app.py",))):
        heard.clear()
        launch = decide_launch(arguments, project=terminal)
        assert launch.unusual and len([event for event in heard if event.kind == "isolation.widened"]) == 1
        assert next(event for event in heard if event.kind == "isolation.widened").severity.name == "WARNING"
    heard.clear()
    refused = decide_launch(Arguments(allow_net=("evil.example",)), project=terminal)
    assert not refused.widened and [event for event in heard if event.kind == "isolation.widened"] == []
    assert sentence("confined_caller").startswith("Refused:")
