"""The wrap-up hook counts a subagent's tool calls and warns it at 120 and 140; the stop hook clears the count."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "scripts" / "hooks"
AGENT = "agent-wrap-1"


def stem(state: Path, agent: str) -> Path:
    return state / "wrap-up" / hashlib.sha256(agent.encode()).hexdigest()[:24]


def call(state: Path, script: str = "claude-wrap-up", **event: object):
    environment = {**os.environ, "POOLHOUSE_HOOK_STATE": str(state), "PATH": "/usr/bin:/bin"}
    return subprocess.run([sys.executable, str(HOOKS / script)], input=json.dumps({"hook_event_name": "PostToolUse", **event}),
                          text=True, capture_output=True, timeout=30, check=False, env=environment)


def preload(state: Path, agent: str, calls: int) -> None:
    """Leave the state a subagent has after ``calls`` counted tool calls, none of them warned."""
    target = Path(f"{stem(state, agent)}.count")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"." * calls)


def context(done) -> str:
    assert done.returncode == 0 and not done.stderr
    if not done.stdout:
        return ""
    output = json.loads(done.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "PostToolUse"
    return output["additionalContext"]


@pytest.mark.parametrize(("before", "warned"), [(118, False), (119, True), (120, False), (138, False), (139, True)])
def test_warning_is_due_exactly_at_120_and_140(tmp_path, before, warned):
    """The call that makes the count 120 and the call that makes it 140 carry the text; their neighbours do not."""
    preload(tmp_path, AGENT, before)
    if before > 119:
        Path(f"{stem(tmp_path, AGENT)}.warned-120").touch()
    text = context(call(tmp_path, agent_id=AGENT))
    assert bool(text) is warned
    if warned:
        assert str(before + 1) in text and "commit" in text and "push" in text and "report" in text


def test_a_run_of_calls_warns_once_at_120_and_once_at_140(tmp_path):
    texts = {}
    for number in range(1, 146):
        text = context(call(tmp_path, agent_id=AGENT))
        if text:
            texts[number] = text
    assert sorted(texts) == [120, 140]
    assert "120 tool calls" in texts[120] and "140 tool calls" in texts[140]


def test_each_subagent_has_its_own_count_and_the_main_session_has_none(tmp_path):
    preload(tmp_path, AGENT, 119)
    assert context(call(tmp_path, agent_id="agent-other")) == ""
    assert context(call(tmp_path)) == ""
    assert context(call(tmp_path, agent_id="")) == ""
    assert context(call(tmp_path, agent_id=AGENT))
    assert Path(f"{stem(tmp_path, 'agent-other')}.count").stat().st_size == 1
    assert len(list((tmp_path / "wrap-up").iterdir())) == 3


def test_a_bad_event_is_ignored(tmp_path):
    environment = {**os.environ, "POOLHOUSE_HOOK_STATE": str(tmp_path)}
    for text in ("", "not json", "[1]", "null"):
        done = subprocess.run([sys.executable, str(HOOKS / "claude-wrap-up")], input=text, text=True, capture_output=True,
                              timeout=30, check=False, env=environment)
        assert done.returncode == 0 and not done.stdout


def test_the_stop_hook_clears_the_count_and_the_warnings(tmp_path):
    preload(tmp_path, AGENT, 120)
    Path(f"{stem(tmp_path, AGENT)}.warned-120").touch()
    preload(tmp_path, "agent-other", 5)
    done = call(tmp_path, "claude-subagent-stop", hook_event_name="SubagentStop", agent_id=AGENT, cwd=str(tmp_path))
    assert done.returncode == 0
    assert not list((tmp_path / "wrap-up").glob(f"{stem(tmp_path, AGENT).name}.*"))
    assert Path(f"{stem(tmp_path, 'agent-other')}.count").stat().st_size == 5
    preload(tmp_path, AGENT, 119)
    assert context(call(tmp_path, agent_id=AGENT))


def test_the_hook_is_wired_as_a_post_tool_use_hook():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    commands = [hook["command"] for group in settings["hooks"]["PostToolUse"] if not group.get("matcher")
                for hook in group["hooks"]]
    assert "$CLAUDE_PROJECT_DIR/scripts/hooks/claude-wrap-up" in commands
    assert os.access(HOOKS / "claude-wrap-up", os.X_OK)


def test_each_call_costs_a_bounded_amount(tmp_path):
    """A call imports nothing from poolhouse, leaves one small file per subagent and takes well under a second."""
    program = (f"import runpy, sys; sys.argv = ['claude-wrap-up']; sys.path.insert(0, {str(HOOKS)!r})\n"
               f"try:\n    runpy.run_path({str(HOOKS / 'claude-wrap-up')!r}, run_name='__main__')\nexcept SystemExit:\n    pass\n"
               "print(sorted(name for name in sys.modules if name.split('.')[0] in ('poolhouse', 'workspace_hook')))")
    environment = {**os.environ, "POOLHOUSE_HOOK_STATE": str(tmp_path)}
    done = subprocess.run([sys.executable, "-c", program], input=json.dumps({"agent_id": AGENT}), text=True,
                          capture_output=True, timeout=30, check=False, env=environment)
    assert done.stdout.strip() == "[]", done.stderr
    started = time.monotonic()
    for _ in range(40):
        call(tmp_path, agent_id=AGENT)
    assert (time.monotonic() - started) / 40 < 0.5
    assert len(list((tmp_path / "wrap-up").iterdir())) == 1 and Path(f"{stem(tmp_path, AGENT)}.count").stat().st_size == 41
