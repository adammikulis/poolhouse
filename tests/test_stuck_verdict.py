"""The stuck-run line the lead's attention hook adds: scripts/hooks/stuck_verdict.py and its place in claude-lead-attention."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "scripts" / "hooks"
sys.path.insert(0, str(HOOKS))
spec = importlib.util.spec_from_file_location("stuck_verdict", HOOKS / "stuck_verdict.py")
stuck_verdict = importlib.util.module_from_spec(spec)
sys.modules["stuck_verdict"] = stuck_verdict
spec.loader.exec_module(stuck_verdict)


def command(tmp_path: Path, body: str) -> list[str]:
    """A command that appends a line to ``calls`` and then runs ``body``."""
    script = tmp_path / "fake_status.py"
    script.write_text(f"import pathlib, sys, time\nopen({str(tmp_path / 'calls')!r}, 'a').write('x\\n')\n{body}\n", encoding="utf-8")
    return [sys.executable, str(script)]


def calls(tmp_path: Path) -> int:
    path = tmp_path / "calls"
    return len(path.read_text().splitlines()) if path.exists() else 0


def test_a_stuck_verdict_is_returned(tmp_path):
    cmd = command(tmp_path, "print('STUCK: github run 7 is in_progress after 31 min')")
    assert stuck_verdict.check(tmp_path / "state", cmd) == "STUCK: github run 7 is in_progress after 31 min"


def test_an_ok_verdict_adds_nothing(tmp_path):
    assert stuck_verdict.check(tmp_path / "state", command(tmp_path, "print('ok')")) == ""


def test_the_answer_is_cached_for_thirty_seconds(tmp_path):
    cmd = command(tmp_path, "print('STUCK: a')")
    now = time.time()
    assert stuck_verdict.check(tmp_path / "state", cmd, now=now) == "STUCK: a"
    assert stuck_verdict.check(tmp_path / "state", cmd, now=now + 299) == "STUCK: a"
    assert calls(tmp_path) == 1
    assert stuck_verdict.check(tmp_path / "state", cmd, now=now + 301) == "STUCK: a"
    assert calls(tmp_path) == 2


def test_a_command_that_outlasts_the_limit_is_ended_and_adds_nothing(tmp_path):
    cmd = command(tmp_path, "time.sleep(30)")
    began = time.monotonic()
    assert stuck_verdict.check(tmp_path / "state", cmd, timeout=0.5) == ""
    assert time.monotonic() - began < 5
    assert stuck_verdict.check(tmp_path / "state", cmd, timeout=0.5) == "" and calls(tmp_path) == 1, "a slow answer is cached too"


@pytest.mark.parametrize("body", ["sys.exit(3)", "print('')", "sys.stderr.write('boom')"])
def test_a_command_that_fails_or_says_nothing_adds_nothing(tmp_path, body):
    assert stuck_verdict.check(tmp_path / "state", command(tmp_path, body)) == ""


def test_a_missing_command_adds_nothing(tmp_path):
    assert stuck_verdict.check(tmp_path / "state", [str(tmp_path / "nowhere")]) == ""


def test_the_default_command_is_scripts_status_with_the_verdict_option():
    assert stuck_verdict.default_command() == [sys.executable, str(HOOKS.parents[1] / "scripts" / "status"), "--verdict"]


def test_the_hook_text_starts_with_the_verdict(tmp_path, monkeypatch):
    fake = command(tmp_path, "print('STUCK: local run x')")
    monkeypatch.setenv("POOLHOUSE_STATUS_COMMAND", json.dumps(fake))
    monkeypatch.setenv("POOLHOUSE_HOOK_STATE", str(tmp_path / "hookstate"))
    monkeypatch.setenv("POOLHOUSE_ATTENTION_EVERY_S", "0")
    done = subprocess.run([sys.executable, str(HOOKS / "claude-lead-attention")],
                          input=json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "hi"}),
                          text=True, capture_output=True, timeout=90, check=False)
    assert done.returncode == 0, done.stderr
    context = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
    assert context.splitlines()[0] == "STUCK: local run x", context


def test_the_hook_is_silent_when_the_verdict_is_ok_and_nothing_is_owed(tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_STATUS_COMMAND", json.dumps(command(tmp_path, "print('ok')")))
    monkeypatch.setenv("POOLHOUSE_HOOK_STATE", str(tmp_path / "hookstate"))
    monkeypatch.setenv("POOLHOUSE_ATTENTION_EVERY_S", "0")
    done = subprocess.run([sys.executable, str(HOOKS / "claude-lead-attention")],
                          input=json.dumps({"hook_event_name": "PostToolUse", "session_id": "s2"}),
                          text=True, capture_output=True, timeout=90, check=False)
    assert done.returncode == 0 and done.stdout == ""
