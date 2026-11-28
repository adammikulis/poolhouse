"""Scheduled jobs: who may make one, what a job may run, what is fixed when it is made, how its record is kept and who gets a
process for it. Nothing is measured and no model is loaded; the bench lines are read by the real parser in a process of its own.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from poolhouse import (
    authority,
    caller,
    delegate,
    delegate_record,
    delegate_store,
    joblines,
    jobs,
    runtime,
    runtime_store,
    schedule_record as record,
)
from poolhouse.bench import keep
from poolhouse.cli import daemon as daemon_command
from poolhouse.fleet import daemon
from poolhouse.isolation import ancestry
from poolhouse.lock import only_one
from poolhouse.schedule import cli, code, execute, runner, spawn, steps, who
from poolhouse.serve import broker_wire

REPO = Path(__file__).resolve().parents[1]
ME = {"who": "user tester pid 1", "sessions": []}
AGENT = ancestry.Session("board-1", "claude-lead", "", "claude-code")
DEFINITION = REPO / "docs" / "examples" / "qwen3.8-27b-quants.queue"


@pytest.fixture
def unboxed(monkeypatch):
    """The suite runs under the real-state write denial, itself a sandbox (tests/test_schedule_process.py runs the real check
    outside it); these tests are of what is decided after that check."""
    monkeypatch.setattr(who, "_sandboxed", lambda: False)
    monkeypatch.setattr(ancestry, "unattended", lambda: False)


@pytest.fixture
def terminal(monkeypatch, unboxed):
    for name in caller.AGENT_MARKERS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ancestry, "attest", lambda: ancestry.Ancestry(()))


def job_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "night.queue"
    path.write_text(text, encoding="utf-8")
    return path


def scheduled(tmp_path: Path, text: str, *, at: float | None = None, **asked) -> dict:
    now = time.time()
    at = now + 3600 if at is None else at
    return cli.add_job(job_file(tmp_path, text), cli.Asked(at, max(at, now) + 3600, checkout=str(REPO), **asked), identity=ME,
                       cwd=tmp_path, begin=lambda _job: None)


def stored(at: float | None = None, **fields) -> dict:
    """A kept job with one delegate step, for what concerns its record and its process alone."""
    now = time.time()
    job = {"name": "kept", "at": now + 3600 if at is None else at, "latest": now + 7200, "max_hours": 1.0, "created": now,
           "steps": [], "by": {"who": "user tester pid 1", "board": "", "agent": "", "cwd": "/"}, "code": {}, "pins": {},
           "child": None, **fields}
    return record.create(job)


def audited(event: str) -> list[dict]:
    return [row for row in caller.audit_rows() if row["event"] == event]


# -- who may schedule -----------------------------------------------------------------------

def test_a_sandboxed_process_schedules_nothing_and_the_refusal_is_audited(tmp_path, monkeypatch):
    monkeypatch.setattr(who, "_sandboxed", lambda: True)
    with pytest.raises(caller.AgentRefused, match="runs in a sandbox"):
        cli.add_job(job_file(tmp_path, "quants report --set qwen3.8-27b\n"),
                    cli.Asked(time.time() + 60, time.time() + 120, checkout=str(REPO)), cwd=tmp_path)
    assert record.jobs() == []
    [row] = audited("refused")
    assert row["action"] == "scheduling a job" and "sandbox" in row["why"] and row["who"]


@pytest.mark.skipif(sys.platform != "darwin", reason="the sandbox question is macOS's")
@pytest.mark.parametrize(("state", "boxed"), [("none", False), ("allows", True), ("refuses", True), ("unknown", True)])
def test_any_sandbox_counts_one_that_only_denies_a_few_things_too(monkeypatch, state, boxed):
    monkeypatch.setattr(who.confinement, "confined", lambda: False)
    monkeypatch.setattr(who.confinement, "seatbelt_state", lambda _pid: state)
    assert who._sandboxed() is boxed


def test_what_an_unattended_agent_started_schedules_nothing(unboxed, monkeypatch):
    monkeypatch.setattr(ancestry, "unattended", lambda: True)
    with pytest.raises(caller.AgentRefused, match="nobody at it"):
        who.admit("scheduling a job")


def test_an_agent_the_node_places_in_a_session_and_a_terminal_may_schedule(terminal, monkeypatch):
    assert who.admit("scheduling a job")["sessions"] == []
    monkeypatch.setattr(ancestry, "attest", lambda: ancestry.Ancestry((AGENT,)))
    monkeypatch.setenv("CLAUDECODE", "1")
    assert who.admit("scheduling a job")["sessions"][0]["name"] == "claude-lead"


def test_a_marked_process_in_no_session_and_a_caller_the_node_cannot_read_are_refused(terminal, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(caller.AgentRefused, match="no session above it"):
        who.admit("scheduling a job")

    def unread():
        raise ancestry.Unattested("Refused: the node did not answer.")

    monkeypatch.setattr(ancestry, "attest", unread)
    with pytest.raises(caller.AgentRefused, match="did not answer"):
        who.admit("scheduling a job")
    assert len(audited("refused")) == 2


# -- what a job may run ---------------------------------------------------------------------

@pytest.mark.parametrize("line", [
    "rm -rf /tmp/x", "sh -c 'echo hi'", "queue other.queue", "stop", "forget night", "bench", "bench python -c 1",
    "delegate --task x", "delegate --model m", "delegate --model --task --task x", "delegate --model m --task x --permissions yolo",
    "delegate --model m --task x --in-place", "remove-model /etc/passwd", "remove-model hf:a/b/../c.gguf",
    "remove-model hf:a/b/c.gguf hf:a/b/d.gguf"])
def test_a_line_that_is_not_one_of_the_three_kinds_is_refused_with_its_line_number(tmp_path, line):
    with pytest.raises(joblines.LineError, match="line 2"):
        steps.parse(f"# a night\n{line}\n", base=tmp_path)


def test_a_job_file_reads_variables_smoke_and_then_delegates_and_removals(tmp_path):
    (tmp_path / "task.md").write_text("say what the tests check", encoding="utf-8")
    found = steps.parse("set S=qwen3.8-27b\nsmoke: quants build-tool\nthen: bench quants report --set ${S}\n"
                        "delegate --model one --model two --task-file task.md --hours 1\n"
                        "remove-model hf:owner/repo/BF16/m-00001-of-00002.gguf\n", base=tmp_path)
    assert [(step["kind"], step["role"]) for step in found] == [("bench", "smoke"), ("bench", "then"), ("delegate", "step"),
                                                                 ("remove-model", "step")]
    assert found[1]["argv"] == ["quants", "report", "--set", "qwen3.8-27b"]
    assert found[2]["delegate"]["models"] == ["one", "two"] and found[2]["delegate"]["task"] == "say what the tests check"
    assert found[3]["ref"] == "hf:owner/repo/BF16/m-00001-of-00002.gguf"


@pytest.mark.parametrize(("line", "flag"), [
    ("quants fidelity --set qwen3.8-27b --tool /tmp/evil", "--tool"),
    ("quants fidelity --set qwen3.8-27b --tool-arg=--evil", "--tool-arg"),
    ("quants speed --set qwen3.8-27b --binary /tmp/evil", "--binary"),
    ("speed --serve m.gguf --bin /tmp/evil", "--binary"),
    ("run --client evil:make --label x", "--client"),
    ("run --ask evil:ask --label x", "--ask"),
    ("sweep --serve m.gguf --detach", "--detach")])
def test_a_bench_line_that_names_something_to_run_or_leaves_the_job_is_refused_and_leaves_no_job(tmp_path, line, flag):
    with pytest.raises(joblines.LineError) as why:
        scheduled(tmp_path, f"{line}\n")
    assert "line 1" in str(why.value) and (flag in str(why.value) or "unrecognized" in str(why.value)), why.value
    assert record.jobs() == [] and not [path for path in record.root().iterdir() if path.is_dir()]


def test_a_bench_line_the_parser_does_not_take_is_refused_when_scheduled(tmp_path):
    with pytest.raises(joblines.LineError, match="--no-such-flag"):
        scheduled(tmp_path, "quants report --set qwen3.8-27b --no-such-flag\n")


def test_the_planned_comparison_file_checks_and_runs_no_removal(tmp_path):
    found = steps.parse(DEFINITION.read_text(encoding="utf-8"), base=tmp_path)
    assert [step["argv"][1] for step in found] == ["speed", "build-tool", "fidelity", "report"]
    assert all(step["kind"] == "bench" for step in found)
    read = code.check_bench([step["argv"] for step in found], python=sys.executable, copy=REPO, cwd=tmp_path)
    for step, one in zip(found, read, strict=True):
        steps.judge(step, one)
    config = json.loads((REPO / "docs" / "experiments" / "quants-qwen3.8.json").read_text(encoding="utf-8"))
    assert set(config["sets"]["qwen3.8-27b"]["quants"]) == {"BF16", "UD-Q4_K_XL", "UD-Q4_K_S", "UD-IQ4_XS", "UD-Q3_K_XL",
                                                            "UD-IQ3_S", "UD-IQ3_XXS", "UD-Q2_K_XL"}


# -- what is fixed when it is scheduled -----------------------------------------------------

def test_the_copy_of_the_code_finds_the_comparisons_config_and_fixed_text_inside_itself(tmp_path):
    copy = tmp_path / "code"
    code.snapshot(REPO, copy)
    said = subprocess.run(
        [sys.executable, "-P", "-B", "-c",
         "from poolhouse import bench; from poolhouse.bench import quants;"
         "a = bench._parser().parse_args(['quants', 'fidelity', '--set', 'qwen3.8-27b']);"
         "plan = quants.load_comparison(a); print(quants.ROOT); print(len(plan.chosen(a.only, reference=True)));"
         "print(sorted(quants.corpus(a)))"],
        capture_output=True, text=True, cwd=tmp_path, env=code.environment(copy), timeout=120, check=False)
    assert said.returncode == 0, said.stderr
    assert said.stdout.split("\n")[:3] == [str(copy.resolve()), "8", "['code', 'prose']"], said.stdout

def test_scheduling_fixes_the_lines_the_code_and_the_input_files_in_storage_for_this_account_alone(tmp_path, monkeypatch):
    (tmp_path / "code.txt").write_text("fixed text", encoding="utf-8")
    (tmp_path / "model.gguf").write_bytes(b"0" * 64)
    monkeypatch.setattr(code, "COPY_MOST", 32)
    job = scheduled(tmp_path, "set S=qwen3.8-27b\nquants fidelity --set ${S} --corpus code.txt --model M=model.gguf "
                              "--reference M\n", name="night")
    job_file(tmp_path, "quants report --set another\n")
    (tmp_path / "code.txt").write_text("changed afterwards", encoding="utf-8")
    kept = record.load(job["id"])
    [step] = kept["steps"]
    copied = Path(step["argv"][step["argv"].index("--corpus") + 1])
    assert copied.read_text(encoding="utf-8") == "fixed text" and record.folder(job["id"]) in copied.parents
    assert copied.name == "code.txt" and "qwen3.8-27b" in step["argv"]
    large = str(tmp_path.resolve() / "model.gguf")
    assert f"M={large}" in step["argv"] and large in kept["pins"]
    assert (record.folder(job["id"]) / "code" / "src" / "poolhouse" / "bench" / "quants.py").is_file()
    assert (record.folder(job["id"]) / "code" / "docs" / "experiments" / "quants-qwen3.8.json").is_file()
    assert kept["code"]["python"] == sys.executable and kept["code"]["copied"] and len(kept["code"]["commit"]) == 40
    assert code.changed(kept["pins"]) == ""
    (tmp_path / "model.gguf").write_bytes(b"1" * 65)
    assert "is not the file it was" in code.changed(kept["pins"])
    if os.name == "posix":
        assert stat.S_IMODE(record.folder(job["id"]).stat().st_mode) == 0o700
        assert stat.S_IMODE((record.folder(job["id"]) / "job.json").stat().st_mode) == 0o600
        assert stat.S_IMODE(record.root().stat().st_mode) == 0o700
    [row] = audited("schedule.create")
    assert row["job"] == job["id"] and row["by"] == ME["who"] and len(row["steps_sha256"]) == 64 and row["commit"]


def test_a_latest_start_that_has_passed_schedules_nothing(tmp_path):
    with pytest.raises(ValueError, match="has passed"):
        cli.add_job(job_file(tmp_path, "delegate --model m --task x\n"), cli.Asked(time.time() - 120, time.time() - 60),
                    identity=ME, cwd=tmp_path, begin=lambda _job: None)
    assert record.jobs() == []


def test_the_notice_goes_to_the_session_that_scheduled_it(tmp_path, monkeypatch):
    monkeypatch.delenv(authority.FLOOR_ENV, raising=False)
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_AGENT", "claude-helper")
    sessions = [{"board": "b1", "name": "claude-lead", "parent": "", "harness": "claude-code"},
                {"board": "b1", "name": "claude-helper", "parent": "claude-lead", "harness": "claude-code"}]
    job = cli.add_job(job_file(tmp_path, "delegate --model m --task x\n"), cli.Asked(time.time() + 60, time.time() + 120),
                      identity={"who": "agent", "sessions": sessions}, cwd=tmp_path, begin=lambda _job: None)
    assert (job["by"]["board"], job["by"]["agent"]) == ("b1", "claude-helper")


def test_a_gate_held_back_from_agents_refuses_an_agent_the_delegate_it_would_start_later(tmp_path, monkeypatch):
    for name in (*caller.AGENT_MARKERS, authority.FLOOR_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(caller, "confined", lambda: False)
    authority.set_state(["isolation.grant"], authority.PERSON, by="claude-lead")
    file = job_file(tmp_path, f"delegate --model m --task x --project {tmp_path}\n")
    asked = cli.Asked(time.time() + 60, time.time() + 120)
    agent = {"who": "agent", "sessions": [{"board": "b1", "name": "claude-lead", "parent": "", "harness": "claude-code"}]}
    with pytest.raises(caller.AgentRefused, match="held back from agents"):
        cli.add_job(file, asked, identity=agent, cwd=tmp_path, begin=lambda _job: None)
    assert record.jobs() == []
    assert cli.add_job(file, asked, identity=ME, cwd=tmp_path, begin=lambda _job: None)["state"] == "waiting"


# -- the record -----------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "posix", reason="POSIX modes and links")
@pytest.mark.parametrize("tamper", ["group", "folder", "link"])
def test_a_record_another_account_could_have_written_is_never_read_or_run(tmp_path, tamper, unboxed):
    job = stored()
    path = record.folder(job["id"]) / "job.json"
    if tamper == "group":
        path.chmod(0o660)
    elif tamper == "folder":
        path.parent.chmod(0o770)
    else:
        held = tmp_path / "elsewhere.json"
        held.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        held.chmod(0o600)
        path.unlink()
        path.symlink_to(held)
    with pytest.raises(record.Refused):
        record.load(job["id"])
    [listed] = record.jobs()
    assert listed["refused"] and listed["state"] == "failed"
    assert spawn.ensure(begin=lambda _job: pytest.fail("a process was started for it")) == []


@pytest.mark.parametrize("name", ["..", "../x", "s-1", "", "s-20261128-0000000g"])
def test_only_a_jobs_id_names_a_folder(name):
    with pytest.raises(record.Refused):
        record.folder(name)


def test_cancelling_a_waiting_job_with_no_process_ends_it_and_an_ended_job_stays_as_it_ended():
    job = stored()
    found = record.cancel(job["id"], "user tester")
    assert found["state"] == "cancelled" and "user tester" in found["why"]
    assert record.cancel(job["id"], "someone else")["why"] == found["why"]
    assert [row["by"] for row in audited("schedule.cancel")] == ["user tester", "someone else"]


def test_a_job_a_process_holds_is_asked_to_cancel_and_left_for_it_to_end():
    job = stored()
    with only_one(record.folder(job["id"]) / record.RUNNER_LOCK):
        found = record.cancel(job["id"], "user tester")
        assert found["state"] == "waiting" and found["cancel"]["by"] == "user tester"
        assert record.view(found)["held"]
    assert "no process holds this job" in record.view(record.load(job["id"]))["why"]


# -- the processes --------------------------------------------------------------------------

def test_every_job_that_has_not_ended_gets_one_process_and_not_a_second(unboxed):
    waiting, held, ended = stored(), stored(), stored()
    record.update(ended["id"], lambda job: job.update(state="done", ended=time.time(), notice={"sent": time.time(), "error": ""}))
    started: list[str] = []

    def begin(job: str) -> None:
        started.append(job)
        (record.folder(job) / "spawned").touch()

    with only_one(record.folder(held["id"]) / record.RUNNER_LOCK):
        assert spawn.ensure(begin=begin) == [waiting["id"]]
        assert spawn.ensure(begin=begin) == []
        assert spawn.ensure(begin=begin, now=time.time() + spawn.AGAIN_S + 1) == [waiting["id"]]


def test_a_notice_that_was_not_sent_is_tried_again_after_a_while_and_not_forever(unboxed):
    job = stored()
    now = time.time()
    record.update(job["id"], lambda kept: kept.update(state="done", ended=now, notice={"sent": 0.0, "tried": now, "error": "x"}))
    assert spawn.ensure(begin=lambda _job: None, now=now + 60) == []
    assert spawn.ensure(begin=lambda _job: None, now=now + spawn.NOTICE_AGAIN_S + 1) == [job["id"]]
    assert spawn.ensure(begin=lambda _job: None, now=now + spawn.NOTICE_DAYS * 86400 + 1) == []


def test_a_caller_that_may_not_schedule_starts_no_process_of_a_job(monkeypatch):
    stored()
    monkeypatch.setattr(who, "_sandboxed", lambda: True)
    assert spawn.ensure(begin=lambda _job: pytest.fail("started from a sandbox")) == []


def test_a_jobs_process_keeps_where_the_account_and_models_are_and_nothing_of_who_scheduled_it(tmp_path):
    given = {"HOME": "/h", "HF_HOME": "/h/hf", "POOLHOUSE_HOME": "/h/.poolhouse", "CLAUDECODE": "1", "PYTHONPATH": "/evil",
             "POOLHOUSE_WORKSPACE_TOKEN": "t", "POOLHOUSE_WORKSPACE_AGENT": "a", "DYLD_INSERT_LIBRARIES": "/evil.dylib",
             "PATH": os.pathsep.join(["/usr/bin", "bin", ".", str(tmp_path), "/tmp/x"])}
    kept = spawn.environment(given)
    assert kept == {"HOME": "/h", "HF_HOME": "/h/hf", "POOLHOUSE_HOME": "/h/.poolhouse", "PATH": "/usr/bin",
                    "PYTHONUNBUFFERED": "1"}


def test_the_daemon_looks_after_jobs_while_it_serves_and_stops_when_it_ends(tmp_path, monkeypatch):
    """`DaemonRuntime.loop` itself, with its listener and its security scanner as stand-ins: no port is opened."""
    looking, seen = threading.Event(), []

    def scheduler(stop: threading.Event) -> None:
        seen.append(stop)
        looking.set()

    class Listener:
        def serve_forever(self):
            assert looking.wait(10) and not seen[0].is_set()

        def server_close(self):
            pass

    class Held:
        def stop(self):
            pass

        close = stop

    monkeypatch.setattr(daemon.guarded, "start", lambda *_args: Held())
    served = object.__new__(daemon.DaemonRuntime)
    served.root, served.interface, served.httpd, served.control = tmp_path, None, Listener(), Held()
    served.scheduler, served._shutdown_jobs = scheduler, lambda: None
    served.loop()
    assert seen and seen[0].is_set()


def test_the_daemon_command_hands_the_daemon_what_keeps_the_jobs(monkeypatch):
    given: dict = {}
    monkeypatch.setattr(daemon, "run", lambda argv, **kept: given.update(kept) or 0)
    monkeypatch.setattr(daemon_command.runtime, "forward", lambda *_args: False)
    assert daemon_command.main([]) == 0 and given["scheduler"] is spawn.keep


def test_keeping_jobs_looks_at_once_and_goes_on_after_a_failure_until_it_is_told_to_stop(monkeypatch):
    stop, looked = threading.Event(), []

    def ensure():
        looked.append(1)
        if len(looked) == 1:
            raise OSError("the state could not be read")
        stop.set()

    monkeypatch.setattr(spawn, "ensure", ensure)
    spawn.keep(stop, every=0.01)
    assert len(looked) == 2


# -- a job that can be run ------------------------------------------------------------------

def installed(tmp_path: Path, name: str, *, schedules: bool) -> runtime.Runtime:
    """An installed runtime as its files show it, with scheduled jobs or from before them."""
    prefix = tmp_path / name
    package = prefix / "lib" / "python3.13" / "site-packages" / "poolhouse"
    (package / "schedule").mkdir(parents=True) if schedules else package.mkdir(parents=True)
    (package / "__init__.py").touch()
    if schedules:
        (package / "schedule" / "cli.py").touch()
    return runtime.Runtime(prefix, ("1" if schedules else "0") * 40, "0", "x")


def test_a_job_is_refused_at_once_when_the_selected_runtime_has_no_scheduled_jobs(tmp_path, monkeypatch):
    old = installed(tmp_path, "old", schedules=False)
    monkeypatch.setattr(runtime, "available", lambda: old)
    with pytest.raises(ValueError, match=r"has no scheduled jobs.*runtime ensure"):
        scheduled(tmp_path, "quants report --set qwen3.8-27b\n")
    assert record.jobs() == []


def test_a_job_no_process_takes_is_not_reported_as_scheduled(tmp_path, monkeypatch):
    started: list[str] = []
    monkeypatch.setattr(spawn, "start", started.append)
    monkeypatch.setattr(spawn, "HELD_WAIT_S", 0.3, raising=False)
    with pytest.raises(ValueError, match="no process took it"):
        cli.add_job(job_file(tmp_path, "delegate --model m --task x\n"), cli.Asked(time.time() + 600, time.time() + 1200),
                    identity=ME, cwd=tmp_path)
    [job] = record.jobs()
    assert started == [job["id"]] and job["state"] == "failed" and "no process took it" in job["why"]
    assert audited("schedule.end")[0]["state"] == "failed"
    assert spawn.ensure(begin=lambda _job: pytest.fail("a job that was refused is started later")) == []


@pytest.mark.parametrize("schedules", [True, False])
def test_a_jobs_process_runs_on_the_selected_runtime_only_when_that_has_scheduled_jobs(tmp_path, monkeypatch, schedules):
    chosen = installed(tmp_path, "selected", schedules=schedules)
    monkeypatch.setattr(runtime, "available", lambda: chosen)
    asked: dict = {}
    monkeypatch.setattr(jobs, "detach", lambda module, argv, **kept: asked.update(kept, argv=argv))
    job = stored()
    spawn.start(job["id"])
    assert asked["argv"] == ["hold", job["id"]] and asked["same_interpreter"] is (not schedules)


def test_a_runtime_deploy_keeps_what_a_job_that_has_not_ended_runs_from(tmp_path):
    root = tmp_path / "runtimes"
    trees = []
    for index in range(7):
        prefix = root / (str(index) * 40) / "p"
        (prefix / "bin").mkdir(parents=True)
        (prefix / "bin" / "python").symlink_to(sys.executable)
        runtime_store.mark_created(prefix, "probe", "ensure")
        runtime_store.mark_verified(runtime.Runtime(prefix, str(index) * 40, "0", "x"))
        (prefix / "verified.json").write_text(json.dumps({"verified_at": 1000.0 + index, "commit": str(index) * 40,
                                                          "version": "0", "identity": "x"}), encoding="utf-8")
        trees.append(prefix)
    stored(code={"python": str(trees[0] / "bin" / "python")})
    stored(runner={"pid": 1, "started": 1.0, "python": str(trees[1] / "bin" / "python"), "at": 0.0})
    ended = stored(code={"python": str(trees[2] / "bin" / "python")})
    record.update(ended["id"], lambda kept: kept.update(state="done"))
    gone = runtime_store.collect({trees[6]}, root=root)
    assert trees[0].is_dir() and trees[1].is_dir() and set(gone) == {trees[2], trees[3]}


def test_a_jobs_steps_and_process_use_the_environment_it_was_scheduled_in(tmp_path, monkeypatch):
    """Whoever starts its process again, the daemon after a restart say, has another PATH and another model cache."""
    cache = str(Path.home() / "models-elsewhere")
    monkeypatch.setenv("HF_HUB_CACHE", cache)
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/sbin", "/usr/bin"]))
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_TOKEN", "a-session-token")
    job = scheduled(tmp_path, "delegate --model m --task x\n")
    assert "a-session-token" not in json.dumps(record.load(job["id"]))
    monkeypatch.delenv("HF_HUB_CACHE")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("HF_HOME", "/another/hf")
    monkeypatch.setenv("PATH", "/bin")
    seen: dict = {}

    class Looked:
        def __init__(self, _job):
            pass

        def run(self):
            seen.update(os.environ)
            return "done"

    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setattr(runner.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(who, "refused_here", lambda: "")
    monkeypatch.setattr(runner, "Runner", Looked)
    assert runner.hold(job["id"]) == 0
    assert seen["HF_HUB_CACHE"] == cache and seen["PATH"] == os.pathsep.join(["/usr/sbin", "/usr/bin"])
    assert "HF_HOME" not in seen and "CLAUDECODE" not in seen and seen["POOLHOUSE_HOME"] == os.environ["POOLHOUSE_HOME"]
    held = record.load(job["id"])["runner"]
    assert held["pid"] == os.getpid() and held["python"] == sys.executable


def test_a_process_keeps_its_job_when_the_successor_it_started_does_not_take_it(monkeypatch):
    job = stored()
    started: list[str] = []
    outcomes = iter(["handover", "done"])
    drove: list[str] = []

    class Driven:
        def __init__(self, found):
            drove.append(found)

        def run(self):
            return next(outcomes)

    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setattr(runner.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(who, "refused_here", lambda: "")
    monkeypatch.setattr(runner, "Runner", Driven)
    monkeypatch.setattr(spawn, "start", started.append)
    monkeypatch.setattr(spawn, "HELD_WAIT_S", 0.3, raising=False)
    assert runner.hold(job["id"]) == 0
    assert started == [job["id"]] and drove == [job["id"], job["id"]]


def test_a_bench_result_of_a_jobs_copy_names_the_commit_it_was_taken_from(tmp_path):
    job = scheduled(tmp_path, "quants report --set qwen3.8-27b\n")
    copy = record.folder(job["id"]) / "code"
    said = subprocess.run([sys.executable, "-P", "-B", "-c", "from poolhouse.bench import keep; print(keep._commit())"],
                          capture_output=True, text=True, cwd=tmp_path, env=code.environment(copy), timeout=120, check=False)
    assert said.returncode == 0, said.stderr
    stamped = said.stdout.strip()
    assert stamped and job["code"]["commit"].startswith(stamped.split()[0])
    assert stamped.endswith(" (dirty)") is job["code"]["uncommitted"]


def test_a_job_knows_which_of_its_bench_lines_measure(tmp_path):
    job = scheduled(tmp_path, "quants speed --set qwen3.8-27b\nquants build-tool\nquants --set qwen3.8-27b fidelity\n"
                              "quants report --set qwen3.8-27b\nquants plan --set qwen3.8-27b\n")
    assert [step["measures"] for step in job["steps"]] == [True, False, True, False, False]


def test_a_kept_run_leaves_a_receipt_of_what_in_it_can_be_used(tmp_path, monkeypatch):
    receipt = tmp_path / "kept.jsonl"
    monkeypatch.setenv("POOLHOUSE_BENCH_RECEIPT", str(receipt))
    keep.save(tmp_path / "runs.ladybug", [{"repeat": 0, "contended": True}, {"repeat": 1, "contended": False, "errors": 0},
                                          {"repeat": 2, "contended": False, "errors": 1}, {"error": "refused"}],
              kind="speed", label="set:one")
    [line] = [json.loads(one) for one in receipt.read_text(encoding="utf-8").splitlines()]
    assert (line["label"], line["rows"], line["usable"]) == ("set:one", 4, 1)


def test_a_full_disk_does_not_stop_a_step_from_being_watched(tmp_path):
    class Full:
        def write(self, _data):
            raise OSError(28, "No space left on device")

        flush = write

    execute.Run({"id": "s-20261127-00000000"}, Full(), lambda: "").say("a line")


def test_adding_a_job_names_what_holds_the_device_now(tmp_path, monkeypatch, capsys, unboxed):
    held = {"claims": {}, "servers": [{"port": 8080, "model": "held.gguf", "holders": [{"pid": 4242, "label": "up"}],
                                       "loading": False, "ours": True}]}
    monkeypatch.setattr(broker_wire, "status", lambda **_kept: held)
    monkeypatch.chdir(tmp_path)
    file = job_file(tmp_path, "delegate --model m --task x\n")
    assert cli.main(["add", str(file), "--at", "+30m", "--dry-run"]) == 0
    said = capsys.readouterr().out
    assert "blocking now" in said and "port 8080" in said and "pid 4242" in said


# -- times and words ------------------------------------------------------------------------

def test_times_are_read_as_the_next_one_and_a_window_as_a_length():
    now = time.mktime((2026, 11, 27, 23, 30, 0, 0, 0, -1))
    assert cli.moment("2026-11-28 23:07", now) == time.mktime((2026, 11, 28, 23, 7, 0, 0, 0, -1))
    assert cli.moment("23:07", now) == time.mktime((2026, 11, 28, 23, 7, 0, 0, 0, -1))
    assert cli.moment("23:45", now) == time.mktime((2026, 11, 27, 23, 45, 0, 0, 0, -1))
    assert cli.moment("+30m", now) == now + 1800 and cli.moment("now", now) == now and cli.span("2h") == 7200
    for bad in ("tomorrow", "25:00", "2h30m"):
        with pytest.raises(ValueError):
            cli.moment(bad, now)


def test_a_computer_without_autostart_is_told_what_a_restart_does_to_a_waiting_job():
    said = cli.autostart_note("not-prepared")
    assert "Autostart is not enabled" in said and "reported missed" in said
    assert "Autostart is enabled" in cli.autostart_note("current") and "awake" in cli.autostart_note("current")


def test_a_delegate_step_passes_words_the_delegate_command_takes_and_reads_the_name_it_prints(capsys):
    asked = {"task": "--edit is data here", "project": "/tmp", "edit": True, "kind": "edit", "max_turns": 3, "no_review": True}
    words = execute.start_words(asked, "Some-Model-Q4.gguf", "night-1")
    args = delegate.COMMANDS.parser().parse_args(words)
    assert (args.task, args.model, args.name, args.edit, args.permissions, args.in_place) == (
        "--edit is data here", "Some-Model-Q4.gguf", "night-1", True, "", False)
    rec = delegate_store.Delegate("claude-5e11ed", "lead-1", "board", "task", "/tmp/work", "Plumbing-Q4.gguf",
                                  review_rounds=0)
    delegate._announce(rec, 4321, "")
    found = execute.STARTED.search(capsys.readouterr().out)
    assert found and (found[1] or found[2]) == "claude-5e11ed"
    assert "done" not in delegate_record.LIVE
