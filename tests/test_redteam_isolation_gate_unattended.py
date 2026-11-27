"""A helper launches, widens and removes as the lead does; an agent with nobody at it (a delegate, a local agent of a board) and what the process
table shows below it do none of that, whatever the node finds above it and whatever its environment says. The runner is the real one, started as
production starts it, on a real node inside the lead's own process tree, with a command that dropped every marker, at depths past what the node
reads of a process tree.

PLUMBING ONLY: no model is served and no OpenCode runs. A marked script stands in OpenCode's place and runs the attempts the way a model's
shell would; a `sitecustomize` of the runner's gives it a server address that is never asked.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from delegate_kit import agent_names, started

from poolhouse import delegate_run, delegate_store as store
from poolhouse.isolation import ancestry, policy as policies
from poolhouse.workspace import localharness, localstart
from tests import isolation_kit as kit
from tests.confined_kit import repository
from tests.isolation_world import git
from tests.sandbox_kit import AbsentBackend

pytest_plugins = ["node_kit", "delegate_kit"]
CAPABILITY = sys.modules["poolhouse.isolation.capability"]
NOBODY = "runs with nobody at it"
SHELLS = '''# runs the command with as many shells between it and the caller as the first word says
n=$1; shift
if [ "$n" -gt 0 ]; then /bin/sh "$0" $((n-1)) "$@"; exit $?; fi
exec "$@"
'''

ATTEMPTS = '''
import pytest  # a test run: the state root stays this test's
import argparse, json
from pathlib import Path
from poolhouse import claude, delegate, harnessing
from poolhouse.isolation import Arguments, decide_launch, policy, remove_project, require_widening
from poolhouse.workspace import localroute, localstart


def in_place():
    said = []
    words = harnessing.parser("opencode", "OpenCode", claude.DEFAULT_PORT, claude.DEFAULT_SLOTS).parse_args(["--project", PROJECT])
    if not harnessing.checked(words, "opencode", said.append, interactive=False):
        raise PermissionError(" ".join(said))


def web():
    port = 4321
    headers = {"host": f"127.0.0.1:{port}", "origin": f"http://127.0.0.1:{port}", "content-type": "application/json"}
    asked = localroute.Request("POST", "/agents/start", headers, port)
    status, _, body = localroute.respond(Path(PROJECT), lambda: None, asked, b'{"profile": "chat", "role": "no such role"}')
    raise RuntimeError(f"{status} {body.decode()}")


def asked(call):
    try:
        call()
    except Exception as why:
        return f"{type(why).__name__}: {why}"
    return "passed"


none = argparse.Namespace(task="", project="", name="", parent="", model="", max_turns=None, edit=False, permissions="")
print("ASKED" + json.dumps({name: asked(call) for name, call in (
    ("widen", lambda: require_widening("a wider launch", None, PROJECT)),
    ("launch", lambda: decide_launch(Arguments(), project=PROJECT)),
    ("register", lambda: decide_launch(Arguments(project=COPY), project=COPY)),
    ("delegate", lambda: delegate.start(none)),   # no task: a caller that may start one is told so, and nothing starts
    ("in place", in_place),
    ("local agent", lambda: localstart.start(None, localstart.Ask(role="no such role"))),   # no such role: nothing starts
    ("web start", web),
    ("policy", lambda: policy.save(PROJECT, policy.ProjectPolicy(in_place=True))),
    ("remove", lambda: remove_project(PROJECT)))}))
'''

STAND_IN = r'''#!{python}
# PLUMBING ONLY: not OpenCode, and no model
import json, os, re, subprocess

config = json.load(open(os.environ["OPENCODE_CONFIG"]))
cfg = json.loads(re.search(r"^const cfg = (.*);$", open(os.path.join(config["plugins"][0], "index.js")).read(), re.M).group(1))
subprocess.run([*cfg["hook"], "post"], input=json.dumps({{"loaded": True}}), capture_output=True, text=True, check=True)
# what a model's shell does to look like a terminal: a command with an environment that names no agent, as deep as the case asks
done = subprocess.run(["/bin/sh", {shells!r}, {depth!r}, {python!r}, "-c", {program!r}], env={env!r}, capture_output=True, text=True, timeout=150)
said = next((each for each in done.stdout.splitlines() if each.startswith("ASKED")), "ASKED" + json.dumps(done.stderr[-2000:]))
for kind, part in (("step_start", {{}}), ("text", {{"text": said}}), ("step_finish", {{"reason": "stop", "tokens": {{"input": 1, "output": 1}}}})):
    print(json.dumps({{"type": kind, "sessionID": "plumbing", "part": part}}), flush=True)
'''

PATCHES = '''# PLUMBING ONLY: no model is served; the runner is given an address that is never asked and the script in OpenCode's place
import pytest  # a test run: the state root stays this test's
import contextlib, os, subprocess, sys, types
from pathlib import Path
from poolhouse import claude, delegate_confined, harnessing, isolation


def ask(into):
    try:
        isolation.require_attended("a launch")
        Path(into).write_text("passed")
    except Exception as why:
        Path(into).write_text(f"{{type(why).__name__}}: {{why}}")


if os.environ.get("ASK_AS_A_CHILD"):   # the child the runner starts with its own arguments: it asks, and runs nothing else
    ask({twin!r})
    os._exit(0)


@contextlib.contextmanager
def leased(_model, _say):
    ask({own!r})
    subprocess.run(sys.orig_argv, env={{**os.environ, "ASK_AS_A_CHILD": "1"}}, check=True, timeout=60)
    yield "http://127.0.0.1:9", types.SimpleNamespace(serving=types.SimpleNamespace(slot_context=8192)), "canned.gguf"


delegate_confined.leased = leased
harnessing.binary_for = lambda _name, **_: {script!r}
claude.alias_of = lambda _url, _model: {model!r}
'''


@pytest.fixture
def world(lead_node, detached, tmp_path, monkeypatch):
    """A lead on a platform with no sandbox, its registered project, a repository the node does not hold, and the attempts as one program."""
    node, lead, project = lead_node
    monkeypatch.setattr(CAPABILITY, "_family", lambda: "win32")
    kit.without_a_sandbox(monkeypatch, AbsentBackend)
    kit.person_home(tmp_path, monkeypatch)
    monkeypatch.setenv("POOLHOUSE_HOME", str(node.state.parent))
    project = project.resolve()
    copy = repository(tmp_path / "unheld").resolve()
    git(copy, "commit", "-q", "-m", "one file")
    program = f"PROJECT, COPY = {str(project)!r}, {str(copy)!r}\n" + ATTEMPTS
    bare = {"PATH": "/usr/bin:/bin", "PYTHONPATH": kit.SOURCE, "POOLHOUSE_HOME": str(node.state.parent), "HOME": os.environ["HOME"]}
    return node, lead, str(project), str(copy), program, bare


def heard(text: str) -> dict[str, str]:
    return json.loads(next(each for each in text.splitlines() if each.startswith("ASKED"))[len("ASKED"):])


def ran_delegate(world, tmp_path, depth: int) -> tuple[dict[str, str], str, str]:
    """A delegate run by the real runner as a process of its own: what its command ``depth`` shells down was told, what the runner was told of
    itself, and what a child with the runner's own arguments was told."""
    node, lead, project, _copy, program, bare = world
    patches, script, shells = tmp_path / "patches", tmp_path / "release" / "opencode", tmp_path / "shells.sh"
    own, twin = tmp_path / "runner-own", tmp_path / "runner-twin"
    patches.mkdir()
    script.parent.mkdir()
    shells.write_text(SHELLS)
    script.write_text(STAND_IN.format(python=os.path.realpath(sys.executable), program=program, env=bare, shells=str(shells), depth=str(depth)))
    script.chmod(0o755)
    (patches / "sitecustomize.py").write_text(PATCHES.format(own=str(own), twin=str(twin), script=str(script), model=kit.MODEL))
    assert started("--task", "try", "--project", project, "--permissions", "yolo") == 0
    [name] = agent_names(node, lead)
    ran = subprocess.run([sys.executable, "-m", delegate_run.__name__, name], capture_output=True, text=True, timeout=240, check=False,
                         env={**os.environ, "PYTHONPATH": os.pathsep.join((str(patches), kit.SOURCE))})
    row = store.result(name)
    assert row.get("state") == "done", (row, ran.stdout, ran.stderr[-2000:], store.log_file(name).read_text())
    return heard(row["text"]), own.read_text(), twin.read_text()


@pytest.mark.parametrize("depth", [0, 40, 70], ids=["its own command", "past what the node reads", "seventy shells down"])
def test_a_delegate_and_what_it_starts_launch_widen_register_and_remove_nothing_though_the_node_places_them_under_the_lead(world, tmp_path, depth):
    _node, _lead, project, copy, program, bare = world
    told, own, _twin = ran_delegate(world, tmp_path, depth)
    assert set(told) == {"widen", "launch", "register", "delegate", "in place", "local agent", "web start", "policy", "remove"}
    assert {act: said for act, said in told.items() if NOBODY not in said or "passed" in said} == {}, told
    assert told["web start"].startswith("RuntimeError: 403 "), "the web route says it is refused, not that it broke"
    assert policies.key_of(project) and not policies.key_of(copy) and policies.load(project) is None, "nothing removed, registered or written"
    assert own == "passed", "the runner is nothing it started: it launches the agent it was made to run"
    control = subprocess.run([sys.executable, "-c", program], env=bare, capture_output=True, text=True, timeout=120, check=False)
    free = heard(control.stdout)
    assert free.pop("delegate").startswith("ValueError: give the task as text"), control.stdout
    assert free.pop("local agent").startswith("ValueError: no role"), control.stdout
    assert free.pop("web start").startswith("RuntimeError: 400 "), control.stdout
    assert free.pop("policy").startswith("AgentRefused: writing"), "a session is in this tree, and the policy is the person's"
    assert set(free.values()) == {"passed"}, "the control: the same command in the lead's tree with no runner above it"


def test_a_child_of_the_runner_with_the_runners_own_arguments_is_still_one_it_started(world, tmp_path):
    _told, own, twin = ran_delegate(world, tmp_path, 0)
    assert own == "passed" and NOBODY in twin, (own, twin)


def test_the_runners_that_are_nobodys_are_the_modules_that_start_a_delegate_and_a_local_agent():
    assert set(ancestry.UNATTENDED) == {delegate_run.__name__, localharness.RUNNER, localstart.LOOP}
