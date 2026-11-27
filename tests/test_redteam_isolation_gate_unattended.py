"""A helper launches, widens and removes as the lead does; an agent with nobody at it (a delegate, a local agent of a board) and anything it
starts does none of that, whatever the node finds above it and whatever its environment says. The runner is the real one, started as production
starts it, on a real node inside the lead's own process tree, with a command that dropped every marker.

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

ATTEMPTS = '''
import pytest  # a test run: the state root stays this test's
import argparse, json
from poolhouse import claude, delegate, harnessing
from poolhouse.isolation import Arguments, decide_launch, remove_project, require_widening


def in_place():
    said = []
    words = harnessing.parser("opencode", "OpenCode", claude.DEFAULT_PORT, claude.DEFAULT_SLOTS).parse_args(["--project", PROJECT])
    if not harnessing.checked(words, "opencode", said.append, interactive=False):
        raise PermissionError(" ".join(said))


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
    ("remove", lambda: remove_project(PROJECT)))}))
'''

STAND_IN = r'''#!{python}
# PLUMBING ONLY: not OpenCode, and no model
import json, os, re, subprocess

config = json.load(open(os.environ["OPENCODE_CONFIG"]))
cfg = json.loads(re.search(r"^const cfg = (.*);$", open(os.path.join(config["plugins"][0], "index.js")).read(), re.M).group(1))
subprocess.run([*cfg["hook"], "post"], input=json.dumps({{"loaded": True}}), capture_output=True, text=True, check=True)
# what a model's shell does to look like a terminal: a command with an environment that names no agent
done = subprocess.run([{python!r}, "-c", {program!r}], env={env!r}, capture_output=True, text=True, timeout=120)
said = next((each for each in done.stdout.splitlines() if each.startswith("ASKED")), "ASKED" + json.dumps(done.stderr[-2000:]))
for kind, part in (("step_start", {{}}), ("text", {{"text": said}}), ("step_finish", {{"reason": "stop", "tokens": {{"input": 1, "output": 1}}}})):
    print(json.dumps({{"type": kind, "sessionID": "plumbing", "part": part}}), flush=True)
'''

PATCHES = '''# PLUMBING ONLY: no model is served; the runner is given an address that is never asked and the script in OpenCode's place
import pytest  # a test run: the state root stays this test's
import contextlib, types
from pathlib import Path
from poolhouse import claude, delegate_confined, harnessing, isolation


@contextlib.contextmanager
def leased(_model, _say):
    try:
        isolation.require_attended("the runner's own launch")
        Path({own!r}).write_text("passed")
    except Exception as why:
        Path({own!r}).write_text(f"{{type(why).__name__}}: {{why}}")
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


def test_a_delegate_and_what_it_starts_launch_widen_register_and_remove_nothing_though_the_node_places_them_under_the_lead(world, tmp_path):
    node, lead, project, copy, program, bare = world
    patches, script, own = tmp_path / "patches", tmp_path / "release" / "opencode", tmp_path / "runner-own"
    patches.mkdir()
    script.parent.mkdir()
    script.write_text(STAND_IN.format(python=os.path.realpath(sys.executable), program=program, env=bare))
    script.chmod(0o755)
    (patches / "sitecustomize.py").write_text(PATCHES.format(own=str(own), script=str(script), model=kit.MODEL))
    assert started("--task", "try", "--project", project, "--permissions", "yolo") == 0
    [name] = agent_names(node, lead)
    ran = subprocess.run([sys.executable, "-m", delegate_run.__name__, name], capture_output=True, text=True, timeout=180, check=False,
                         env={**os.environ, "PYTHONPATH": os.pathsep.join((str(patches), kit.SOURCE))})
    row = store.result(name)
    assert row.get("state") == "done", (row, ran.stdout, ran.stderr[-2000:], store.log_file(name).read_text())
    told = heard(row["text"])
    assert set(told) == {"widen", "launch", "register", "delegate", "in place", "remove"}
    assert {act: said for act, said in told.items() if NOBODY not in said or "passed" in said} == {}, told
    assert policies.key_of(project) and not policies.key_of(copy), "nothing was removed and nothing registered"
    assert own.read_text() == "passed", "the runner is nothing it started: it launches the agent it was made to run"
    control = subprocess.run([sys.executable, "-c", program], env=bare, capture_output=True, text=True, timeout=120, check=False)
    free = heard(control.stdout)
    assert free.pop("delegate").startswith("ValueError: give the task as text"), control.stdout
    assert set(free.values()) == {"passed"}, "the control: the same command in the lead's tree with no runner above it"


def test_the_runners_that_are_nobodys_are_the_modules_that_start_a_delegate_and_a_local_agent():
    assert set(ancestry.UNATTENDED) == {delegate_run.__name__, localharness.RUNNER, localstart.LOOP}
