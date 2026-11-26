"""`runtime status` names the processes that began before the selected runtime and still run older code.

The processes are real children of the test, found by the real process scan; only the verification
time is chosen, as the selected runtime's mark would record it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from poolhouse import runtime_age, runtime_stale


@pytest.fixture
def daemon(tmp_path: Path):
    """A long-running child whose command line names a poolhouse program, and the runtime prefix it was not started from."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", "poolhouse-fakedaemon"])
    try:
        deadline = time.monotonic() + 10
        while not any(p.pid == child.pid for p in runtime_stale.running()) and time.monotonic() < deadline:
            time.sleep(0.05)
        yield child, tmp_path / "runtimes" / "selected"
    finally:
        child.kill()
        child.wait()


def test_a_daemon_started_before_the_selected_runtime_is_named(daemon) -> None:
    child, prefix = daemon
    lines = runtime_stale.stale_lines(prefix, time.time() + 600)
    assert any(f"pid {child.pid} " in line and "poolhouse-fakedaemon" in line for line in lines)
    assert lines[-1].startswith("note")


def test_a_daemon_started_after_the_selected_runtime_is_not_named(daemon) -> None:
    child, prefix = daemon
    lines = runtime_stale.stale_lines(prefix, time.time() - 600)
    assert not any(f"pid {child.pid} " in line for line in lines)


def test_a_process_running_from_the_selected_runtime_is_not_named(daemon) -> None:
    child, _ = daemon
    prefix = Path(sys.executable).parent.parent
    old = runtime_stale.older_than(runtime_stale.running(), prefix, time.time() + 600)
    assert child.pid not in [p.pid for p in old]


def test_a_runtime_that_was_never_verified_names_nothing(daemon) -> None:
    _, prefix = daemon
    assert runtime_stale.stale_lines(prefix, 0.0) == []


@pytest.fixture
def selected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A runtime selection in an isolated runtimes directory; its prefix is verified when the test says."""
    from poolhouse import runtime

    root = tmp_path / "runtimes"
    root.mkdir()
    monkeypatch.setattr(runtime, "directory", lambda: root)
    prefix = root / ("c" * 40) / "prefix"
    prefix.mkdir(parents=True)
    (root / "selected.json").write_text(json.dumps({"prefix": str(prefix)}), encoding="utf-8")

    def verified(at: float) -> None:
        (prefix / "verified.json").write_text(json.dumps({"verified_at": at}), encoding="utf-8")

    return verified


def test_a_process_knows_it_began_before_the_selected_runtime(selected, daemon) -> None:
    selected(time.time() + 600)
    assert runtime_age.is_older()
    assert runtime_age.is_older(daemon[0].pid)


def test_a_process_is_not_older_than_a_runtime_verified_before_it_began(selected, daemon) -> None:
    selected(time.time() - 3600)
    assert not runtime_age.is_older()
    assert not runtime_age.is_older(daemon[0].pid)


@pytest.mark.parametrize(("answer", "said"), [("gone", "broker replaced"), ("busy", "replaced when that work ends"),
                                              ("current", ""), ("no broker", "")])
def test_ensure_says_what_became_of_the_broker_that_runs_older_code(monkeypatch, answer, said) -> None:
    from poolhouse import runtime_cli, runtime_deploy
    from poolhouse.serve import broker_wire

    monkeypatch.setattr(broker_wire, "replace_stale", lambda: answer)
    outcome = runtime_cli._with_broker(runtime_deploy.Outcome("switched", "a" * 40, "selected"))
    assert outcome.action == "switched" and outcome.detail.startswith("selected")
    assert said in outcome.detail and (bool(said) == (outcome.detail != "selected"))


def test_ensure_does_not_fail_because_the_broker_cannot_be_reached(monkeypatch) -> None:
    from poolhouse import runtime_cli, runtime_deploy
    from poolhouse.serve import broker_wire

    def refused() -> str:
        raise broker_wire.BrokerError("access denied")

    monkeypatch.setattr(broker_wire, "replace_stale", refused)
    outcome = runtime_cli._with_broker(runtime_deploy.Outcome("switched", "a" * 40, ""))
    assert outcome.action == "switched" and "access denied" in outcome.detail


def test_nothing_is_older_when_no_runtime_is_selected_or_the_process_is_gone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from poolhouse import runtime

    monkeypatch.setattr(runtime, "directory", lambda: tmp_path / "none")
    assert not runtime_age.is_older()
    assert not runtime_age.is_older(2 ** 22 + 1)
