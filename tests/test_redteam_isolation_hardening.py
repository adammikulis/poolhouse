"""The isolated launcher's library, attacked after its own review (docs/notes/attack-verdict-isolation-l2-l3.md): each finding is a probe that
failed before its fix, run on real files, the real Seatbelt, real sockets and a real node. Nothing waits for anyone and nothing is answered.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from poolhouse.isolation import Arguments, attest, decide_launch, toolchain
from poolhouse.isolation.grants import Request, Rule, decide
from poolhouse.isolation.profile import build
from poolhouse.isolation.refusals import sentence
from poolhouse.platform import start_process
from poolhouse.sandbox import run
from poolhouse.sandbox.seatbelt import Seatbelt
from tests.isolation_kit import (
    REGISTRY,
    SOURCE,
    confined_python,
    hostile_project,
    person_home,
    world,
)
from tests.sandbox_kit import policy

pytest_plugins = ["tests.sandbox_kit"]
ISSUER = "user test pid 1"


def nobody(project: str) -> bool:
    return False


def shim(folder: Path, name: str = "python3.12") -> Path:
    """An executable named like a Python in ``folder``: what a tool folder holds when a person keeps a shim of an interpreter in it."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text("#!/bin/sh\nexit 0\n")
    (folder / name).chmod(0o755)
    return folder / name


# H1: a project file chose a read-and-run grant of any folder that holds `bin/python*`


@pytest.mark.parametrize("where", ["home/.local/bin", "elsewhere/prefix/bin", "home/opt/tool/bin"])
def test_a_pyvenv_cfg_that_names_a_folder_outside_the_closed_list_grants_nothing_and_is_refused_with_the_reason(tmp_path, monkeypatch, where):
    house = person_home(tmp_path, monkeypatch)
    named = tmp_path / where
    shim(named)
    keyring = named.parent / "share" / "somekeyring"
    keyring.mkdir(parents=True)
    (keyring / "login.db").write_text("a secret\n")
    project = Path(hostile_project(tmp_path))
    (project / ".venv").mkdir()
    (project / ".venv" / "pyvenv.cfg").write_text(f"home = {named}\nversion = 3.12\n")
    assert toolchain.declared(project) == [], "a folder the project names is not a toolchain because it holds a Python"
    seen = []
    launch = decide_launch(Arguments(), project=str(project), on_event=lambda name, fields: seen.append(name), registered=nobody)
    refused = [each for each in launch.decisions if not each.granted]
    assert launch.grants == () and not launch.widened and seen == [], "nothing was granted and nothing widened"
    assert len(refused) == 1 and refused[0].line == sentence("declared_elsewhere"), "the launch goes on, and says why it went on without it"
    assert str(house) not in refused[0].line and str(named) not in refused[0].line
    old_way = Request("toolchain", str(named.parent), ISSUER, "declaration", str(project))
    assert not decide(old_way, Rule(str(project), registry=REGISTRY, scope="s")).granted, "the parent of the shim's folder is not a declaration"


def test_a_relative_home_or_one_that_climbs_out_of_the_closed_list_is_refused_too(tmp_path, monkeypatch):
    house = person_home(tmp_path, monkeypatch)
    shim(house / ".local" / "bin")
    versions = house / ".pyenv" / "versions"
    versions.mkdir(parents=True)
    project = Path(hostile_project(tmp_path))
    (project / ".venv").mkdir()
    for text in ("bin", "../../.local/bin", f"{versions}/../../.local/bin", f"{versions}/missing/bin", ""):
        (project / ".venv" / "pyvenv.cfg").write_text(f"home = {text}\n")
        assert toolchain.declared(project) == [], text


def make_interpreter(root: Path) -> Path:
    """An interpreter folder as a version manager lays it out: `bin`, `lib/python3.99`, and the rest of a prefix beside them."""
    (root / "bin").mkdir(parents=True)
    program = root / "bin" / "python3.99"
    program.write_text("#!/bin/sh\necho ran\n")
    program.chmod(0o755)
    (root / "lib" / "python3.99" / "test" / "certdata").mkdir(parents=True)
    (root / "lib" / "python3.99" / "os.py").write_text("x = 1\n")
    (root / "lib" / "python3.99" / "test" / "certdata" / "cacert.pem").write_text("a public certificate\n")
    for name in ("share/keyring/login.db", "etc/secret.cfg", "var/state.db"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("belongs to something else\n")
    return root


READS = """
import json, subprocess
root = %r
seen = {}
for name in ("lib/python3.99/os.py", "bin/python3.99", "share/keyring/login.db", "etc/secret.cfg", "var/state.db"):
    try:
        open(root + "/" + name, "rb").read()
        seen[name] = "read"
    except OSError:
        seen[name] = "denied"
seen["run"] = subprocess.run([root + "/bin/python3.99"], capture_output=True, text=True).stdout.strip()
print("SEEN" + json.dumps(seen))
"""


def test_a_declared_interpreter_is_granted_as_its_bin_and_its_lib_python_only_and_the_real_seatbelt_refuses_the_rest(tmp_path, monkeypatch):
    house = person_home(tmp_path, monkeypatch)
    root = make_interpreter(house / ".pyenv" / "versions" / "3.99.0")
    project = Path(hostile_project(tmp_path))
    (project / ".python-version").write_text("3.99.0\n")
    seen = []
    launch = decide_launch(Arguments(), project=str(project), on_event=lambda name, fields: seen.append((name, fields)), registered=nobody)
    assert [each.subject for each in launch.grants] == [os.path.realpath(root)], "the person's own version folder is granted, and the certificate in its tests does not stop it"
    assert seen and seen[0][0] == "isolation.widened" and seen[0][1]["kinds"] == ["toolchain"], "a declaration is signalled to the sentinel like any widening"
    assert launch.widened
    assert toolchain.reach(os.path.realpath(root)) == (os.path.realpath(root / "bin"), os.path.realpath(root / "lib" / "python3.99"))
    with world(tmp_path / "w") as w:
        profile = build(w.spec, launch.grants, {"PATH": "/usr/bin:/bin"})
        done = run([os.path.realpath(sys.executable), "-I", "-c", READS % os.path.realpath(root)], profile.policy, diagnose="never")
    found = json.loads(next(line for line in done.stdout.splitlines() if line.startswith("SEEN"))[4:])
    assert found == {"lib/python3.99/os.py": "read", "bin/python3.99": "read", "share/keyring/login.db": "denied", "etc/secret.cfg": "denied",
                     "var/state.db": "denied", "run": "ran"}, found
    assert str(Path(os.path.realpath(root)) / "bin") in profile.path
    for grant in launch.grants:
        grant.held.close()


def test_a_credential_inside_a_toolchains_reach_refuses_it_and_a_certificate_in_test_data_does_not(tmp_path, monkeypatch):
    house = person_home(tmp_path, monkeypatch)
    root = make_interpreter(house / ".pyenv" / "versions" / "3.98.0")
    rule = Rule(hostile_project(tmp_path), registry=REGISTRY, scope="s")
    plain = decide(Request("toolchain", str(root), ISSUER, "task", rule.project), rule)
    assert plain.granted, plain.cause
    plain.grant.held.close()
    (root / "lib" / "python3.99" / ".netrc").write_text("machine example login me password secret\n")
    refused = decide(Request("toolchain", str(root), ISSUER, "task", rule.project), rule)
    assert not refused.granted and "credential" in refused.cause
    (root / "lib" / "python3.99" / ".netrc").unlink()
    (root / "bin" / "id_ed25519").write_text("not a key\n")
    assert not decide(Request("toolchain", str(root), ISSUER, "task", rule.project), rule).granted


def test_the_interpreter_that_runs_this_code_is_on_the_closed_list():
    assert os.path.realpath(sys.base_prefix) in toolchain.interpreter_roots()


# M1: the two answers about one profile agree


ALLOW_DEFAULT = '(version 1)(allow default)(deny file-write* (subpath "/private/nonexistent-poolhouse-write-denial"))'
ASK = "import sys\nsys.path.insert(0, %r)\nfrom poolhouse.sandbox.confinement import confined\nprint('ANSWER', confined())\n"


def answered(text: str) -> bool:
    line = next(each for each in text.splitlines() if each.startswith("ANSWER "))
    return line.split()[1] == "True"


def test_confined_and_the_attestation_give_one_answer_for_the_same_profile(tmp_path):
    held = policy(env={"PATH": "/usr/bin:/bin"})
    deny_list = ["/usr/bin/sandbox-exec", "-p", ALLOW_DEFAULT]
    children = {
        "none": ["/bin/sleep", "30"],
        "allows": [*deny_list, "/bin/sleep", "30"],
        "refuses": Seatbelt().wrap(["/bin/sleep", "30"], held.validated()).argv,
    }
    living = {name: start_process(argv, stdin=-3, stdout=-3, stderr=-3) for name, argv in children.items()}
    try:
        deadline = time.monotonic() + 5
        while not attest(living["refuses"].pid).confined and time.monotonic() < deadline:
            time.sleep(0.02)
        facts = {name: attest(child.pid) for name, child in living.items()}
    finally:
        for child in living.values():
            child.kill()
            child.wait()
    own = {"none": answered(subprocess.run([os.path.realpath(sys.executable), "-I", "-c", ASK % SOURCE], capture_output=True, text=True,
                                          timeout=60).stdout),
           "allows": answered(subprocess.run([*deny_list, os.path.realpath(sys.executable), "-I", "-c", ASK % SOURCE], capture_output=True,
                                             text=True, timeout=60).stdout),
           "refuses": answered(confined_python(ASK % SOURCE, tmp_path).stdout)}
    assert own == {"none": False, "allows": False, "refuses": True}, own
    for name in living:
        assert facts[name].confined == own[name], f"the two answers differ for one profile: {name} {facts[name]} {own[name]}"
    assert "confines nothing" in facts["allows"].fact, "a deny-list profile on a harness is attested as not confined, and the fact says so"


@pytest.mark.parametrize("plan", [False, True])
def test_every_profile_the_engine_builds_refuses_by_default_and_a_process_in_it_is_confined_and_attested(tmp_path, plan):
    from poolhouse.sandbox.confinement import seatbelt_state
    with world(tmp_path, plan=plan) as w:
        made = Seatbelt().wrap(["/bin/sleep", "30"], w.profile.policy.validated())
        assert "(deny default" in made.argv[2] and "(allow default" not in made.argv[2]
        child = start_process(made.argv, stdin=-3, stdout=-3, stderr=-3)
        try:
            deadline = time.monotonic() + 5
            while seatbelt_state(child.pid) != "refuses" and time.monotonic() < deadline:
                time.sleep(0.02)
            assert seatbelt_state(child.pid) == "refuses" and attest(child.pid).confined
        finally:
            child.kill()
            child.wait()
