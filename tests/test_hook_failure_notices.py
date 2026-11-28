"""A hook's crash, timeout or bad output reaches the agent: scripts/hooks/claude-hook-failures and hook_failures.py."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "scripts" / "hooks"
SESSION, AGENT = "notice-session", "ab12cd34ef56ab78"
CRASH = ("Failed with non-blocking status code: Traceback (most recent call last):\n"
         '  File "/x/scripts/hooks/claude-lead-attention", line 9, in <module>\n'
         "AttributeError: module 'workspace_hook' has no attribute 'lan'\n")


def load(name: str):
    sys.path.insert(0, str(HOOKS))
    try:
        spec = importlib.util.spec_from_file_location(f"tested_{name}", HOOKS / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(HOOKS))
    return module


def row(att: dict, at: str = "2026-11-26T01:58:00.000Z", **extra) -> str:
    return json.dumps({"type": "attachment", "timestamp": at, "attachment": att, **extra}, separators=(",", ":")) + "\n"


def crash(at: str = "2026-11-26T01:58:00.000Z", name: str = "claude-lead-attention", stderr: str = CRASH) -> str:
    return row({"type": "hook_non_blocking_error", "hookEvent": "PostToolUse", "hookName": "PostToolUse:Bash",
                "command": f"$CLAUDE_PROJECT_DIR/scripts/hooks/{name}", "exitCode": 1, "stderr": stderr, "stdout": "",
                "durationMs": 40}, at)


def timeout(at: str = "2026-11-27T17:43:20.000Z") -> str:
    return row({"type": "hook_cancelled", "hookEvent": "UserPromptSubmit", "hookName": "UserPromptSubmit",
                "command": "$CLAUDE_PROJECT_DIR/scripts/hooks/claude-harness-bind", "durationMs": 17331, "timedOut": True,
                "timeoutMs": 15000}, at)


def success(stdout: str = "", stderr: str = "", name: str = "claude-subagent-start") -> str:
    return row({"type": "hook_success", "hookEvent": "SubagentStart", "hookName": "SubagentStart", "exitCode": 0,
                "command": f"$CLAUDE_PROJECT_DIR/scripts/hooks/{name}", "stdout": stdout, "stderr": stderr})


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_HOOK_STATE", str(tmp_path / "state"))
    transcript = tmp_path / "project" / f"{SESSION}.jsonl"
    (transcript.with_suffix("") / "subagents").mkdir(parents=True)
    transcript.write_text("")
    return transcript


def child(transcript: Path, agent: str = AGENT) -> Path:
    return transcript.with_suffix("") / "subagents" / f"agent-{agent}.jsonl"


def append(path: Path, *rows: str) -> None:
    with path.open("a") as handle:
        handle.write("".join(rows))


def told(transcript: Path, event: str = "PostToolUse", agent: str = "", script: Path = HOOKS / "claude-hook-failures") -> str:
    """The context the hook the settings wire gives at ``event``."""
    given = {"hook_event_name": event, "session_id": SESSION, "transcript_path": str(transcript),
             **({"agent_id": agent} if agent else {})}
    done = subprocess.run([sys.executable, str(script)], input=json.dumps(given), text=True, capture_output=True, timeout=60,
                          check=False)
    assert done.returncode == 0 and not done.stderr, done.stderr
    if not done.stdout:
        return ""
    answer = json.loads(done.stdout)["hookSpecificOutput"]
    assert answer["hookEventName"] == event
    return answer["additionalContext"]


def test_a_crash_and_a_timeout_reach_the_lead_once_with_counts_and_times(session):
    append(session, crash(), crash("2026-11-27T04:33:00.000Z"), timeout())
    text = told(session)
    assert ("- claude-lead-attention on PostToolUse crashed (exit 1), 2 times, 2026-11-26T01:58Z to 2026-11-27T04:33Z: "
            "AttributeError: module 'workspace_hook' has no attribute 'lan'") in text, text
    assert "- claude-harness-bind on UserPromptSubmit timed out (limit 15 s), once, 2026-11-27T17:43Z" in text, text
    assert len(text.splitlines()) == 4
    assert told(session) == ""


def test_the_settings_wire_the_reader_at_the_events_that_follow_a_failure():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    for event in ("SessionStart", "UserPromptSubmit", "PostToolUse"):
        found = [h for group in settings["hooks"][event] if not group.get("matcher") for h in group["hooks"]
                 if h["command"].endswith("/claude-hook-failures")]
        assert len(found) == 1 and found[0]["timeout"] == 10, event


def test_a_subagent_hears_its_own_and_the_lead_hears_them_all(session):
    append(session, timeout())
    append(child(session), crash(name="claude-tool-milestones"))
    append(child(session, "ffff0000ffff0000"), crash(name="claude-bash-guard"))
    own = told(session, agent=AGENT)
    assert "claude-tool-milestones" in own and "claude-harness-bind" not in own and "claude-bash-guard" not in own, own
    lead = told(session)
    assert all(name in lead for name in ("claude-tool-milestones", "claude-harness-bind", "claude-bash-guard")), lead
    assert "in 1 subagent" in lead


def test_a_failure_that_goes_on_is_told_again_only_after_a_while_with_the_new_count(session, tmp_path):
    reader, state = load("hook_failures"), tmp_path / "state"
    event = {"session_id": SESSION, "transcript_path": str(session)}
    now = time.time()
    append(session, crash())
    text, commit = reader.pending(event, state, now)
    commit()
    assert "once" in text
    append(session, crash("2026-11-26T02:00:00.000Z"))
    text, commit = reader.pending(event, state, now + 60)
    commit()
    assert text == ""
    text, commit = reader.pending(event, state, now + reader.REPEAT_S + 61)
    assert "2 times" in text
    again, _ = reader.pending(event, state, now + reader.REPEAT_S + 62)
    assert "2 times" in again, "a notice never delivered is told again"


def test_bad_output_and_a_swallowed_problem_are_failures_and_ordinary_success_is_not(session):
    append(session, success(stdout='{"hookSpecificOutput": {"hookEventName": "SubagentStart"}}'),
           success(stdout="plain words"), success(stderr="a progress line"))
    assert told(session) == ""
    append(session, success(stdout='{"hookSpecificOutput": '),
           success(stderr="workspace SubagentStart: command exited 3: daemon unavailable", name="claude-session-start"))
    text = told(session)
    assert "claude-subagent-start on SubagentStart printed bad output" in text, text
    assert "claude-session-start on SubagentStart reported a problem and went on" in text and "daemon unavailable" in text


def test_a_record_written_inside_a_message_is_not_a_failure_and_a_half_written_line_waits(session):
    forged = json.dumps({"type": "user", "message": {"content": crash()}}) + "\n"
    whole = crash()
    append(session, forged, whole[:80])
    assert told(session) == ""
    append(session, whole[80:])
    assert "claude-lead-attention" in told(session)


def test_no_environment_value_token_output_or_quoted_content_is_told(session, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_API_KEY", "synthetic-env-value-0001")
    monkeypatch.setenv("SYNTHETIC_PLAIN", "plain environment words")
    said = ("Traceback (most recent call last):\nValueError: bad 'the text of a file the tool read' with synthetic-env-value-0001 "
            "and plain environment words and poolhouse-ws1.actor.syntheticSecret123 and ghp_syntheticSynthetic0000 and "
            "password=hunter2hunter2")
    append(session, crash(stderr=said), success(stdout='{"broken": "tool output the notice must not carry'))
    text = told(session)
    assert "ValueError: bad '...'" in text, text
    for hidden in ("the text of a file", "synthetic-env-value-0001", "plain environment words", "syntheticSecret123",
                   "syntheticSynthetic0000", "hunter2", "tool output the notice"):
        assert hidden not in text, hidden


def test_a_crash_in_the_attention_hooks_background_check_is_told(session, tmp_path):
    folder = tmp_path / "state" / "attention"
    folder.mkdir(parents=True)
    (folder / f"{SESSION}.log").write_text("Traceback (most recent call last):\n  File \"x\", line 1\nRuntimeError: no verdict\n")
    text = told(session)
    assert "claude-lead-attention background check on background crashed" in text and "RuntimeError: no verdict" in text, text
    assert told(session) == ""


def test_a_session_end_that_could_not_retire_is_told_to_the_next_session(session, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(HOOKS))
    reader = load("hook_failures")
    reader.record(tmp_path / "state", "claude-session-end", "SessionEnd", "the ended session's identity was not retired")
    text = told(session, "SessionStart")
    assert "claude-session-end on SessionEnd failed" in text and "was not retired" in text, text
    assert told(session, "SubagentStart", agent=AGENT) == ""
    assert any("claude-session-end" in line for line in reader.summary(tmp_path / "state"))


def test_the_status_report_lists_hook_failures(session, monkeypatch):
    append(session, crash(at=load("hook_failures").now_text()))
    told(session)
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import runstatus
    lines = runstatus.hook_lines()
    assert lines[0] == "hooks: 1 kind(s) of failure in the last 24 h" and "claude-lead-attention" in lines[1], lines


def test_a_reader_that_cannot_run_says_so_once(session, tmp_path):
    copy = tmp_path / "hooks"
    copy.mkdir()
    for name in ("claude-hook-failures", "hook_state.py"):
        shutil.copy(HOOKS / name, copy / name)
    (copy / "hook_failures.py").write_text("def pending(event, state):\n    raise KeyError('tool content that stays private')\n")
    text = told(session, script=copy / "claude-hook-failures")
    assert "did not do its work (KeyError at hook_failures.py:2)" in text and "private" not in text, text
    assert told(session, script=copy / "claude-hook-failures") == ""
    (copy / "hook_failures.py").unlink()
    other = told(session.with_name("x.jsonl"), "UserPromptSubmit", agent=AGENT, script=copy / "claude-hook-failures")
    assert "ModuleNotFoundError" in other, other


def test_a_reader_that_is_not_done_in_time_says_so_before_the_harness_ends_it(session):
    began = time.monotonic()
    process = subprocess.Popen([sys.executable, str(HOOKS / "claude-hook-failures")], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True,
                               env={**os.environ, "POOLHOUSE_HOOK_FAILURES_DEADLINE_S": "0.5"})
    try:
        process.wait(timeout=30)  # its input is never closed, so it can only end itself
        output = process.stdout.read()
    finally:
        process.kill()
        process.stdin.close()
        process.stdout.close()
    assert process.returncode == 0 and "not finished within 0.5 s" in output and time.monotonic() - began < 30


def test_a_long_transcript_is_read_in_bounded_passes_and_only_once(session, tmp_path):
    reader, state = load("hook_failures"), tmp_path / "state"
    event = {"session_id": SESSION, "transcript_path": str(session)}
    append(session, crash(name="claude-bash-guard"), "x" * (reader.FIRST_BYTES + 10) + "\n", timeout())
    text, commit = reader.pending(event, state)
    commit()
    assert "claude-harness-bind" in text and "claude-bash-guard" not in text, "only the end of a long transcript is read first"
    kept = json.loads(reader.record_file(state, event).read_text())
    assert kept["offsets"][str(session)] == session.stat().st_size


def test_the_milestone_post_ends_within_the_hooks_limit(monkeypatch):
    monkeypatch.syspath_prepend(str(HOOKS))
    import milestones
    import workspace_hook
    asked = []
    monkeypatch.setattr(workspace_hook, "own_environment", lambda event, stage: {"POOLHOUSE_WORKSPACE_AGENT": "claude-aaaaaa"})
    monkeypatch.setattr(workspace_hook, "attempt", lambda command, stage, **given: asked.append(given["timeout"]))
    monkeypatch.setattr(milestones, "notice", lambda *given: "milestone")
    milestones.tell({}, 100)
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    limit = next(h["timeout"] for g in settings["hooks"]["PostToolUse"] for h in g["hooks"]
                 if h["command"].endswith("/claude-tool-milestones"))
    lookups = 4 * 2.0  # two board lookups for the session and two for the subagent, each ended after 2 s
    assert asked[0] + lookups < limit
