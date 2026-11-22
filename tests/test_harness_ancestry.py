"""The harness ancestry check the pre-push hook makes: an agent that dropped its marker variables is still found above its processes,
by the name of its harness or because a session hook recorded the process. The hooks run as child processes of the test, which
stands in for the harness; nothing is asked and nothing waits."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import threading
from pathlib import Path

import psutil
import pytest
from harness_support import (
    HOOKS,
    ROOT,
    SESSION,
    bind,
    environment,
    recorded,
    run_hook,
    session_event,
    under_a_harness,
)

from poolhouse.workspace import harness_ancestry

NAMED = harness_ancestry.under_harness()


def sleeper() -> subprocess.Popen:
    """A process that stays until the test ends it."""
    return subprocess.Popen(["sleep", "60"])


def started(process: subprocess.Popen) -> float:
    return psutil.Process(process.pid).create_time()


def end(*processes: subprocess.Popen) -> None:
    for process in processes:
        process.kill()
        process.wait()


@pytest.mark.skipif(NAMED, reason="this run is itself under a harness that is known by its name")
def test_the_process_a_prompt_hook_runs_under_is_recorded_and_then_found_though_its_name_says_nothing(tmp_path):
    state = tmp_path / "state"
    assert under_a_harness(state).returncode == 1, "nothing is recorded yet"
    done = bind(state)
    assert done.returncode == 0 and done.stdout == "" and done.stderr == ""
    (row,) = recorded(state)
    me = psutil.Process()
    assert (row["pid"], row["created"], row["session"]) == (me.pid, me.create_time(), SESSION)
    assert under_a_harness(state).returncode == 0


@pytest.mark.skipif(NAMED, reason="this run is itself under a harness that is known by its name")
def test_a_record_of_one_state_root_does_not_make_a_process_under_another_one_a_harness(tmp_path):
    bind(tmp_path / "one")
    assert under_a_harness(tmp_path / "one").returncode == 0
    assert under_a_harness(tmp_path / "two").returncode == 1


@pytest.mark.skipif(NAMED, reason="this run is itself under a harness that is known by its name")
@pytest.mark.parametrize("change", [{"agent_id": "sub-1"}, {"hook_event_name": "PostToolUse"}, {"hook_event_name": "SessionEnd"},
                                    {"hook_event_name": ""}, {"session_id": ""}, {"session_id": "bad id\n"},
                                    {"session_id": "x" * 257}, {"session_id": 7}])
def test_an_event_that_is_not_a_main_session_start_or_prompt_records_nothing(tmp_path, change):
    state = tmp_path / "state"
    assert bind(state, **change).returncode == 0
    assert recorded(state) == [] and under_a_harness(state).returncode == 1


@pytest.mark.skipif(NAMED, reason="this run is itself under a harness that is known by its name")
def test_the_session_start_hook_records_the_process_too(tmp_path):
    state = tmp_path / "state"
    done = run_hook("claude-session-start", session_event("SessionStart"), state)
    assert done.returncode == 0, done.stderr
    assert [r["pid"] for r in recorded(state)] == [os.getpid()]
    assert under_a_harness(state).returncode == 0


def test_a_hook_that_cannot_record_says_so_on_stderr_and_never_blocks(tmp_path):
    held = tmp_path / "state"
    held.write_text("a file where the state folder should be")
    done = bind(held)
    assert done.returncode == 0 and "workspace UserPrompt:" in done.stderr
    garbage = subprocess.run([sys.executable, str(HOOKS / "claude-harness-bind")], input="{not json", text=True,
                             capture_output=True, timeout=30, check=False, env=environment(tmp_path / "other"))
    assert garbage.returncode == 0


def test_the_record_is_private_and_holds_only_what_the_check_needs(tmp_path):
    state = tmp_path / "state"
    bind(state)
    assert stat.S_IMODE((state / "person" / "harness-sessions.json").stat().st_mode) == 0o600
    assert set(recorded(state)[0]) == {"pid", "created", "session"}


def test_a_process_that_ended_is_forgotten_at_the_next_record(tmp_path):
    where = tmp_path / "harness.json"
    gone, live = sleeper(), sleeper()
    try:
        gone_at, live_at = started(gone), started(live)
        harness_ancestry.bind("old", gone.pid, gone_at, where)
        assert harness_ancestry.bound(where) == {(gone.pid, gone_at)}
        end(gone)
        harness_ancestry.bind("new", live.pid, live_at, where)
        assert harness_ancestry.bound(where) == {(live.pid, live_at)}
    finally:
        end(gone, live)


def test_a_reused_process_number_with_another_start_time_is_not_the_recorded_process(tmp_path):
    where = tmp_path / "harness.json"
    live = sleeper()
    try:
        harness_ancestry.bind("stale", live.pid, started(live) + 5.0, where)
        me = psutil.Process()
        harness_ancestry.bind("fresh", me.pid, me.create_time(), where)
        assert harness_ancestry.bound(where) == {(me.pid, me.create_time())}
    finally:
        end(live)


def child_says(where: Path) -> str:
    """Whether a child of this process, given the record at ``where``, finds a harness above itself."""
    code = ("import sys; from pathlib import Path; from poolhouse.workspace import harness_ancestry as h; "
            "print(h.under_harness(h.bound(Path(sys.argv[1]))))")
    done = subprocess.run([sys.executable, "-c", code, str(where)], capture_output=True, text=True, timeout=60, check=True,
                          env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    return done.stdout.strip()


@pytest.mark.skipif(NAMED, reason="this run is itself under a harness that is known by its name")
def test_only_the_recorded_process_with_its_own_start_time_is_a_harness(tmp_path):
    where = tmp_path / "harness.json"
    me = psutil.Process()
    assert child_says(where) == "False"
    harness_ancestry.bind("sess", me.pid, me.create_time() + 5.0, where)
    assert child_says(where) == "False", "the same process number started at another time is another process"
    harness_ancestry.bind("sess", me.pid, me.create_time(), where)
    assert child_says(where) == "True"


@pytest.mark.parametrize("text", ["", "not json", "[]", '{"version": 2, "processes": [{"pid": 1, "created": 1.0}]}',
                                  '{"version": 1, "processes": "x"}', '{"version": 1, "processes": [{"pid": "x", "created": 1}]}',
                                  '{"processes": [{"pid": 1, "created": 1.0}]}'])
def test_an_unreadable_record_means_the_names_decide_and_the_next_record_repairs_it(tmp_path, text):
    where = tmp_path / "harness.json"
    where.write_text(text)
    assert harness_ancestry.bound(where) == set()
    me = psutil.Process()
    harness_ancestry.bind("sess", me.pid, me.create_time(), where)
    assert harness_ancestry.bound(where) == {(me.pid, me.create_time())}


def test_the_record_keeps_only_the_newest_processes(tmp_path, monkeypatch):
    monkeypatch.setattr(harness_ancestry, "MOST", 3)
    where = tmp_path / "harness.json"
    processes = [sleeper() for _ in range(5)]
    try:
        for process in processes:
            harness_ancestry.bind("sess", process.pid, started(process), where)
        assert harness_ancestry.bound(where) == {(p.pid, started(p)) for p in processes[-3:]}
    finally:
        end(*processes)


def test_hooks_that_record_at_once_lose_none_of_them(tmp_path):
    where = tmp_path / "harness.json"
    processes = [sleeper() for _ in range(6)]
    try:
        threads = [threading.Thread(target=harness_ancestry.bind, args=(f"s{n}", p.pid, started(p), where))
                   for n, p in enumerate(processes)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        assert harness_ancestry.bound(where) == {(p.pid, started(p)) for p in processes}
    finally:
        end(*processes)


@pytest.mark.parametrize("name,found", [("claude", True), ("codex", True), ("claude-code", True), ("codex-cli", True),
                                        ("2.1.325", False), ("node", False), ("python3.13", False), ("zsh", False)])
def test_a_harness_is_known_by_its_name_whatever_the_record_says(monkeypatch, name, found):
    """The names are matched against the processes above this one; the ancestors are the only thing stood in for."""
    monkeypatch.setattr(harness_ancestry, "ancestors", lambda pid=None: [(4242, 1.0, name)])
    assert harness_ancestry.under_harness() is found
    assert harness_ancestry.under_harness({(4242, 1.0)}) is True


def test_a_shell_between_the_hook_and_the_harness_is_skipped_when_the_harness_is_found(monkeypatch):
    monkeypatch.setattr(harness_ancestry, "ancestors", lambda pid=None: [(10, 1.0, "zsh"), (11, 2.0, "env"), (12, 3.0, "2.1.325")])
    assert harness_ancestry.harness_parent() == (12, 3.0)
    monkeypatch.setattr(harness_ancestry, "ancestors", lambda pid=None: [(10, 1.0, "zsh")])
    assert harness_ancestry.harness_parent() is None
