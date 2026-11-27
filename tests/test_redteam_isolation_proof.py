"""The proof, attacked: the real probe under the real Seatbelt, a profile weakened by one rule at a time, a hostile project, and the attestation.

The attacker is the code a coding harness runs. A mutant is the generated Seatbelt profile with one rule deleted or widened: the proof must stop
the launch naming the attempt that got through, and a control that fails (a bridge that cannot be reached) is a deviation too.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest

from poolhouse.isolation import IsolationFailed, attest, capability, probe, proof
from poolhouse.isolation.capability import drop_cache
from poolhouse.platform import start_process
from poolhouse.sandbox import run
from poolhouse.sandbox.seatbelt import Seatbelt
from tests.isolation_kit import interpreter, person_home, world
from tests.sandbox_kit import policy

pytest_plugins = ["tests.sandbox_kit"]


class Weakened(Seatbelt):
    """The real backend with one rule of the generated profile changed on its way to the kernel."""

    def __init__(self, edit):
        self.edit = edit

    def wrap(self, argv, policy, **kept):
        made = super().wrap(argv, policy, **kept)
        made.argv[2] = self.edit(made.argv[2])
        return made


def drop(prefix):
    def edit(text):
        kept = [line for line in text.splitlines() if not line.startswith(prefix)]
        assert len(kept) < len(text.splitlines()), f"no rule begins {prefix}"
        return "\n".join(kept) + "\n"
    return edit


def swap(pattern, new):
    def edit(text):
        found, count = re.subn(pattern, new, text, count=1)
        assert count, f"no rule matches {pattern}"
        return found
    return edit


def add(rule):
    return lambda text: text + rule + "\n"


def reads_in_the_home(inside=""):
    """A rule that grants a read of the person's home (or a folder in it), as the home is when the profile is generated."""
    return lambda text: text + f'(allow file-read* (subpath "{Path.home()}{inside}"))\n'


MUTANTS = {
    "the default is allow": (swap(r"\(deny default", "(allow default"), "P01"),
    "any signal is allowed": (swap(r"\(allow signal \(target children\)\)", "(allow signal)"), "P11"),
    "process info of others is readable": (drop("(deny process-info-pidinfo)"), "P12"),
    "everything under /private/var is readable": (add('(allow file-read* (subpath "/private/var"))'), "P01"),
    "everything under /private/var is writable": (add('(allow file-write* (subpath "/private/var"))'), "P03"),
    "any loopback port is reachable": (swap(r"localhost:\d+", "localhost:*"), "P07"),
    "any outbound network is allowed": (add("(allow network-outbound)"), "P07"),
    "a socket can be bound": (add("(allow network-bind)"), "P09"),
    "any unix socket is reachable": (swap(r'\(remote unix-socket \(literal "[^"]+"\)\)', "(remote unix-socket)"), "P10"),
    "registry property reads are allowed": (drop("(deny iokit-get-properties)"), "P22"),
    "the host uuid call is allowed": (drop("(deny syscall-unix"), "P22"),
    "the control names are writable": (drop("(deny file-write* (subpath"), "P04"),
    "every system name is readable": (swap(r"\(allow sysctl-read[^\n]*", "(allow sysctl-read)"), "P12"),
    "the home folder is readable": (reads_in_the_home(), "P02"),
    "the keychain folder is readable": (reads_in_the_home("/Library/Keychains"), "P17"),
    "any mach service is reachable": (add("(allow mach-lookup)"), "P17"),
    "any program may start": (add('(allow process-exec (subpath "/"))'), "P18"),
    "a terminal device is open to it": (add('(allow file-read* file-write* file-ioctl (regex #"^/dev/ttys[0-9]+$"))'), "P14"),
    "the bridge is not reachable": (swap(r'\(allow network-outbound \(remote ip "localhost:\d+"\)\)', ""), "P20"),
}


def test_a_proof_with_the_profile_the_harness_gets_passes_and_names_every_attempt(tmp_path):
    with world(tmp_path) as w:
        done = proof.run(w.profile, w.services, lan=False)
    assert [each.id for each in done.lines] == [f"P{n:02d}" for n in range(1, 25)] and all(each.ok for each in done.lines)
    assert done.digest == w.profile.digest and done.seconds < proof.BUDGET_SECONDS
    assert {each.guarantee for each in done.lines} >= {f"G{n}" for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 13)} | {"controls", "exposures", "I17"}
    held = json.dumps([each.observed for each in done.lines])
    assert os.environ.get("HOME", "x") not in held and "ghp_" not in held, "a line names what it saw and holds no value the parent protects"


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_a_proof_with_a_weakened_profile_stops_the_launch(tmp_path, monkeypatch, name):
    edit, attempt = MUTANTS[name]
    person_home(tmp_path / "person", monkeypatch)
    with world(tmp_path) as w, pytest.raises(IsolationFailed) as stopped:
        proof.run(w.profile, w.services, lan=False, via=Weakened(edit), budget=20.0)
    assert stopped.value.attempt == attempt, (name, stopped.value.attempt, stopped.value.detail)
    assert stopped.value.digest == w.profile.digest and "so the coding agent was not started" in str(stopped.value)


def test_a_read_only_stage_must_be_read_only_and_a_stage_that_is_writable_when_it_must_not_be_is_a_deviation(tmp_path):
    with world(tmp_path, plan=True) as w:
        assert w.profile.stage not in w.profile.policy.write
        assert proof.run(w.profile, w.services, lan=False).lines[19].ok
        wider = replace(w.profile, policy=replace(w.profile.policy, write=(*w.profile.policy.write, w.profile.stage)))
        with pytest.raises(IsolationFailed) as stopped:
            proof.run(wider, w.services, lan=False, budget=20.0)
        assert stopped.value.attempt == "P20", "a stage that was to be read-only and is not"


def test_a_proof_whose_environment_holds_a_name_beyond_the_allow_list_stops_the_launch(tmp_path):
    with world(tmp_path) as w:
        wider = replace(w.profile, policy=replace(w.profile.policy, env={**w.profile.policy.env, "GH_TOKEN": "x"}))
        with pytest.raises(IsolationFailed) as stopped:
            proof.run(wider, w.services, lan=False, budget=20.0)
    assert stopped.value.attempt == "P19" and "GH_TOKEN" not in str(stopped.value)


def slowed(tmp_path, monkeypatch, attempt: str, first: str) -> None:
    """The real probe with ``attempt`` running the statement ``first`` before it tries anything."""
    source = Path(probe.__file__).read_text(encoding="utf-8")
    before = f"def {attempt.lower()}():\n"
    assert source.count(before) == 1
    held = tmp_path / "slow_probe.py"
    held.write_text(source.replace(before, f"{before}    import time\n\n    {first}\n"), encoding="utf-8")
    monkeypatch.setattr(probe, "__file__", str(held))


def test_a_proof_waits_for_a_probe_that_takes_longer_than_its_fixed_cost(tmp_path, monkeypatch):
    slowed(tmp_path, monkeypatch, "P22", "time.sleep(6)")
    with world(tmp_path) as w:
        done = proof.run(w.profile, w.services, lan=False)
    assert [each.id for each in done.lines] == list(proof.ATTEMPTS) and all(each.ok for each in done.lines)
    assert proof.WAIT_SECONDS == 180.0


def test_a_probe_that_takes_over_a_minute_still_finds_the_bystander_running(tmp_path, monkeypatch):
    """The other process the box must not signal or read: ended, it could be signalled by a correct box, and would hold no arguments to read."""
    slowed(tmp_path, monkeypatch, "P11", "time.sleep(61)")
    with world(tmp_path) as w:
        done = proof.run(w.profile, w.services, lan=False)
    assert [each.id for each in done.lines] == list(proof.ATTEMPTS) and all(each.ok for each in done.lines)


def test_an_attempt_never_answered_stops_the_launch_and_says_it_did_not_answer_in_time(tmp_path, monkeypatch):
    slowed(tmp_path, monkeypatch, "P22", "time.sleep(60)")
    with world(tmp_path) as w, pytest.raises(IsolationFailed) as stopped:
        proof.run(w.profile, w.services, lan=False, budget=8.0)
    assert stopped.value.attempt == "P22"
    assert "P22 did not answer in time" in str(stopped.value) and "allowed" not in str(stopped.value), str(stopped.value)
    assert "so the coding agent was not started" in str(stopped.value)


def test_an_attempt_that_got_through_is_reported_as_allowed_though_a_later_one_never_answers(tmp_path, monkeypatch):
    slowed(tmp_path, monkeypatch, "P22", "time.sleep(60)")
    with world(tmp_path) as w, pytest.raises(IsolationFailed) as stopped:
        proof.run(w.profile, w.services, lan=False, via=Weakened(add("(allow network-outbound)")), budget=8.0)
    assert stopped.value.attempt == "P07" and "it allowed P07" in str(stopped.value), str(stopped.value)


def test_a_probe_that_ends_without_answering_is_not_called_slow(tmp_path, monkeypatch):
    slowed(tmp_path, monkeypatch, "P22", "os._exit(0)")
    with world(tmp_path) as w, pytest.raises(IsolationFailed) as stopped:
        proof.run(w.profile, w.services, lan=False)
    assert stopped.value.attempt == "P22" and "in time" not in str(stopped.value), str(stopped.value)


@pytest.mark.parametrize(("call", "expected"), [("said_by", "late\n"), ("tool", True)])
def test_a_command_of_the_probe_that_is_slow_is_waited_for_and_not_taken_for_a_refusal(call, expected):
    """A command cut off after a short time of its own would read as nothing obtained, or as a program the box refused."""
    assert getattr(probe, call)(["/bin/sh", "-c", "sleep 3; echo late"]) == expected


def test_what_a_command_cut_off_by_its_wait_had_printed_is_searched():
    assert probe.said_by(["/bin/sh", "-c", "echo early; exec sleep 30"], 1.0) == "early\n"


def test_the_parent_waits_for_the_identifiers_it_searches_the_probes_answer_for():
    assert proof._said(["/bin/sh", "-c", "sleep 6; echo late"]) == "late\n"


def test_a_probe_that_is_not_answered_inside_the_budget_is_a_deviation_and_the_proof_waits_no_longer(tmp_path):
    with world(tmp_path) as w:
        began = time.monotonic()
        with pytest.raises(IsolationFailed) as stopped:
            proof.run(w.profile, w.services, lan=False, budget=0.4)
    assert time.monotonic() - began < 3 and stopped.value.attempt.startswith("P")


def test_a_failed_proof_drops_the_capability_report_and_tells_the_sentinel_and_the_log(tmp_path):
    from poolhouse import sentinel
    node = sentinel.default()
    heard = []
    node.bus.subscribe(heard.append)
    first = capability()
    with world(tmp_path) as w, pytest.raises(IsolationFailed):
        proof.run(w.profile, w.services, lan=False, via=Weakened(add("(allow network-outbound)")), budget=20.0)
    assert capability() is not first
    assert [event.severity.name for event in heard if event.kind == "isolation.proof_failed"] == ["WARNING"]
    drop_cache()


def test_project_content_cannot_change_the_probe(tmp_path):
    with world(tmp_path) as w:
        stage = Path(w.profile.stage)
        hostile = "import os\nopen(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'POISONED'), 'w').write('x')\n"
        for name in ("json.py", "socket.py", "os.py", "sitecustomize.py", "usercustomize.py", "http.py", "ctypes.py"):
            (stage / name).write_text(hostile + "raise SystemExit(0)\n")
        (stage / "evil.pth").write_text("import os; open(os.path.join(os.path.dirname(__file__), 'POISONED-PTH'), 'w').write('x')\n")
        (stage / "canary").write_text("a file named like the parent's canary\n")
        (stage / ".claude").write_text("")
        for each in (stage / ".claude",):
            each.unlink()
        # the attack is real: the same files change a plain interpreter started in the stage
        plain = policy(read=[stage], write=[stage], python=True, env={"HOME": str(tmp_path)})
        done = run([interpreter(), "-c", "import json"], plain, cwd=str(stage), diagnose="never")
        assert (stage / "POISONED").exists() and done.returncode == 0, "control: a module in the stage replaces the standard library"
        (stage / "POISONED").unlink()
        shown = proof.run(w.profile, w.services, lan=False)
        assert all(each.ok for each in shown.lines) and shown.lines[0].observed == "denied", "the probe tried its attempts and was not replaced"
        assert not list(stage.glob("POISONED*")), "nothing in the stage ran"


def test_a_proof_has_no_flag_variable_or_argument_that_skips_it(monkeypatch):
    assert set(inspect.signature(proof.run).parameters) == {"profile", "services", "via", "budget", "lan"}, "none of them leaves an attempt out"
    package = Path(proof.__file__).parent
    source = "".join(path.read_text() for path in package.glob("*.py"))
    for word in ("AllowUnsandboxed", "SKIP_PROOF", "NO_PROOF", "unsandboxed="):
        assert word not in source, word
    assert [path.name for path in package.glob("*.py") if "os.environ" in path.read_text()] == ["probe.py"], "no variable of the launcher decides"
    for name in ("POOLHOUSE_UNSANDBOXED", "POOLHOUSE_SKIP_PROOF", "POOLHOUSE_ISOLATION", "POOLHOUSE_ALLOW_UNSANDBOXED"):
        monkeypatch.setenv(name, "1")
    assert subprocess.DEVNULL and not (set(inspect.signature(proof.run).parameters) & {"skip", "unsandboxed", "allow"})


def test_a_live_process_in_the_confinement_is_attested_and_one_outside_it_is_not(tmp_path):
    held = policy(env={"PATH": "/usr/bin:/bin"})
    wrapped = Seatbelt().wrap(["/bin/sleep", "30"], held.validated())
    inside = start_process(wrapped.argv, stdin=-3, stdout=-3, stderr=-3)
    outside = start_process(["/bin/sleep", "30"], stdin=-3, stdout=-3, stderr=-3)
    try:
        deadline = time.monotonic() + 5
        while not attest(inside.pid).confined and time.monotonic() < deadline:
            time.sleep(0.02)
        assert attest(inside.pid).confined and "sandbox applies" in attest(inside.pid).fact
        assert not attest(outside.pid).confined and attest(outside.pid).fact == "no sandbox applies to the process"
        assert not attest(2**22).confined, "a pid that does not exist is not attested"
    finally:
        for each in (inside, outside):
            each.kill()
            each.wait()


def test_the_proof_runs_the_twenty_four_attempts_inside_the_fixed_cost_the_design_gives(tmp_path):
    assert proof.BUDGET_SECONDS == 5.0 and tuple(f"P{n:02d}" for n in range(1, 25)) == proof.ATTEMPTS
    with world(tmp_path) as w:
        began = time.monotonic()
        done = proof.run(w.profile, w.services, lan=False)
    assert done.seconds <= time.monotonic() - began < proof.BUDGET_SECONDS


@pytest.mark.lan_network
def test_the_proof_with_a_listener_on_this_machines_own_address_passes_and_a_widened_network_is_stopped_at_the_first_attempt(tmp_path):
    with world(tmp_path) as w:
        done = proof.run(w.profile, w.services)
        assert next(each for each in done.lines if each.id == "P08").ok
        with pytest.raises(IsolationFailed) as stopped:
            proof.run(w.profile, w.services, via=Weakened(add("(allow network-outbound)")), budget=20.0)
    assert stopped.value.attempt == "P07"
