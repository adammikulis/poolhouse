"""The lead is told when a subagent reaches 100 tool calls and every 50 after; the count is shared with the wrap-up hook."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest_plugins = ["node_kit"]
ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "scripts" / "hooks"
NATIVE = "abcdef12345"
LEAD = "native-lead-1"


@pytest.fixture
def board(tmp_path, monkeypatch, workspace_node):
    """A fake `poolhouse-workspace` that records each call, a board with a lead and its subagent, and a private hook state."""
    directory = tmp_path / "bin"
    directory.mkdir()
    executable = directory / "poolhouse-workspace"
    executable.write_text(f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
path = Path(os.environ['HOOK_CALLS'])
calls = json.loads(path.read_text()) if path.exists() else []
calls.append(dict(argv=sys.argv[1:], agent=os.environ.get('POOLHOUSE_WORKSPACE_AGENT')))
path.write_text(json.dumps(calls))
if os.environ.get('HOOK_FAILURE'):
    print(os.environ['HOOK_FAILURE'], file=sys.stderr)
    raise SystemExit(3)
if sys.argv[1:2] == ['spawn']:
    print(json.dumps(dict(id='claude-aaaaaa', parent='claude-bbbbbb', board='demo')))
""")
    executable.chmod(0o700)
    (directory / "git").symlink_to(shutil.which("git"))
    monkeypatch.setenv("PATH", str(directory))
    monkeypatch.setenv("HOOK_CALLS", str(tmp_path / "calls.json"))
    monkeypatch.setenv("POOLHOUSE_HOOK_STATE", str(tmp_path / "hookstate"))
    monkeypatch.delenv("POOLHOUSE_TOOL_MILESTONE_EVERY", raising=False)
    monkeypatch.syspath_prepend(str(HOOKS))
    monkeypatch.chdir(tmp_path)
    lead = workspace_node.member(LEAD)
    child = workspace_node.member(NATIVE, parent=lead)
    return child.name, tmp_path


def event(tmp_path: Path, agent: str = NATIVE, **more: object) -> dict:
    """The PostToolUse event a tool call inside a subagent delivers (verified shape: agent_id and agent_type are present)."""
    session = tmp_path / "projects" / "session.jsonl"
    return {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": LEAD, "transcript_path": str(session),
            "cwd": str(tmp_path), "agent_id": agent, "agent_type": "general-purpose", **more}


def call(payload: dict, script: str = "claude-wrap-up"):
    return subprocess.run([sys.executable, str(HOOKS / script)], input=json.dumps(payload), text=True, capture_output=True,
                          timeout=60, check=False)


def preload(agent: str, calls: int) -> None:
    import wrap_up
    target = Path(f"{wrap_up.stem(agent)}.count")
    target.write_bytes(b"." * calls)


def posts(tmp_path: Path) -> list[list[str]]:
    path = tmp_path / "calls.json"
    every = json.loads(path.read_text()) if path.exists() else []
    return [c["argv"] for c in every if c["argv"][:2] == ["board", "post"]]


def describe(tmp_path: Path, text: str = "Build the counter") -> None:
    meta = tmp_path / "projects" / "session" / "subagents" / f"agent-{NATIVE}.meta.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps({"agentType": "general-purpose", "description": text, "spawnDepth": 1}))


def test_a_repeated_event_is_counted_once(board):
    import wrap_up
    _, tmp_path = board
    for _ in range(3):
        assert call(event(tmp_path, tool_use_id="toolu_one")).returncode == 0
    assert call(event(tmp_path, tool_use_id="toolu_two")).returncode == 0
    assert Path(f"{wrap_up.stem(NATIVE)}.count").stat().st_size == 2


@pytest.mark.parametrize(("before", "level"), [(98, None), (99, 100), (100, None), (148, None), (149, 150), (199, 200), (299, 300)])
def test_the_lead_is_told_at_100_and_every_50_after(board, before, level):
    import wrap_up
    name, tmp_path = board
    preload(NATIVE, before)
    for step in range(100, before + 1, 50):
        Path(f"{wrap_up.stem(NATIVE)}.milestone-{step}").touch()
    done = call(event(tmp_path, tool_use_id="toolu_x"))
    assert done.returncode == 0 and not done.stderr
    sent = posts(tmp_path)
    if level is None:
        assert sent == []
        return
    assert len(sent) == 1
    assert sent[0][:6] == ["board", "post", "#general", sent[0][3], "--type", "status"] and sent[0][6:] == ["--subject", "milestone"]
    assert name in sent[0][3] and f"{level} tool calls" in sent[0][3] and len(sent[0][3]) <= 200


def test_the_interval_is_a_setting(board, monkeypatch):
    _, tmp_path = board
    monkeypatch.setenv("POOLHOUSE_TOOL_MILESTONE_EVERY", "10")
    import wrap_up
    preload(NATIVE, 109)
    Path(f"{wrap_up.stem(NATIVE)}.milestone-100").touch()
    assert call(event(tmp_path, tool_use_id="toolu_y")).returncode == 0
    assert len(posts(tmp_path)) == 1 and "110 tool calls" in posts(tmp_path)[0][3]


def test_a_run_of_calls_announces_each_milestone_once(board):
    _, tmp_path = board
    for number in range(1, 161):
        assert call(event(tmp_path, tool_use_id=f"toolu_{number}")).returncode == 0
        if number == 100:
            assert len(posts(tmp_path)) == 1
    texts = [sent[3] for sent in posts(tmp_path)]
    assert len(texts) == 2 and "100 tool calls" in texts[0] and "150 tool calls" in texts[1]


def test_a_delivery_twice_at_the_milestone_posts_once(board):
    _, tmp_path = board
    preload(NATIVE, 99)
    first = call(event(tmp_path, tool_use_id="toolu_edge"))
    second = call(event(tmp_path, tool_use_id="toolu_edge"))
    assert first.returncode == second.returncode == 0
    assert len(posts(tmp_path)) == 1


def test_a_second_call_past_the_line_does_not_announce_again(board):
    """Two calls that both see a count at or past 100 (parallel tool calls) share one announcement."""
    _, tmp_path = board
    preload(NATIVE, 101)
    for number in range(2):
        assert call(event(tmp_path, tool_use_id=f"toolu_late_{number}")).returncode == 0
    assert len(posts(tmp_path)) == 1


def test_the_notice_names_the_subagent_what_it_does_and_how_long_it_has_run(board):
    import subagent_records
    name, tmp_path = board
    describe(tmp_path)
    subagent_records.save(NATIVE, name=name, started=time.time() - 3 * 3600 - 7 * 60)
    preload(NATIVE, 99)
    assert call(event(tmp_path, tool_use_id="toolu_text")).returncode == 0
    text = posts(tmp_path)[0][3]
    assert name in text and "Build the counter" in text and "100 tool calls" in text and re.search(r"3h0[67]m", text)


def test_the_notice_is_posted_by_the_subagent_as_itself(board):
    name, tmp_path = board
    preload(NATIVE, 99)
    assert call(event(tmp_path, tool_use_id="toolu_who")).returncode == 0
    callers = [c["agent"] for c in json.loads((tmp_path / "calls.json").read_text()) if c["argv"][:2] == ["board", "post"]]
    assert callers == [name]


def test_the_main_session_is_never_announced(board):
    _, tmp_path = board
    payload = event(tmp_path, tool_use_id="toolu_main")
    del payload["agent_id"], payload["agent_type"]
    assert call(payload).returncode == 0
    assert posts(tmp_path) == []


def test_a_board_that_refuses_is_loud_and_retried_then_handed_to_the_subagent(board, monkeypatch):
    """A refused post warns, is tried again by the next calls, and after three tries the subagent is told to say so."""
    _, tmp_path = board
    monkeypatch.setenv("HOOK_FAILURE", "Denied: board unavailable")
    preload(NATIVE, 99)
    first = call(event(tmp_path, tool_use_id="toolu_f1"))
    assert first.returncode == 0 and "board unavailable" in first.stderr and not first.stdout
    second = call(event(tmp_path, tool_use_id="toolu_f2"))
    third = call(event(tmp_path, tool_use_id="toolu_f3"))
    assert second.returncode == third.returncode == 0 and not second.stdout
    context = json.loads(third.stdout)["hookSpecificOutput"]
    assert context["hookEventName"] == "PostToolUse" and "lead" in context["additionalContext"]
    assert "board unavailable" in context["additionalContext"]
    attempts = len(posts(tmp_path))
    assert call(event(tmp_path, tool_use_id="toolu_f4")).stdout == ""
    assert len(posts(tmp_path)) == attempts


def test_a_refusal_that_clears_is_announced_on_the_next_call(board, monkeypatch):
    _, tmp_path = board
    preload(NATIVE, 99)
    monkeypatch.setenv("HOOK_FAILURE", "Denied: board unavailable")
    assert "board unavailable" in call(event(tmp_path, tool_use_id="toolu_r1")).stderr
    monkeypatch.delenv("HOOK_FAILURE")
    assert call(event(tmp_path, tool_use_id="toolu_r2")).returncode == 0
    assert len(posts(tmp_path)) == 2
    assert call(event(tmp_path, tool_use_id="toolu_r3")).returncode == 0
    assert len(posts(tmp_path)) == 2


def test_an_unnamed_subagent_fails_loudly(board):
    _, tmp_path = board
    preload("agent-without-a-name", 99)
    done = call(event(tmp_path, agent="agent-without-a-name", tool_use_id="toolu_n"))
    assert done.returncode == 0 and "milestone" in done.stderr
    assert posts(tmp_path) == []


def test_the_stop_hook_forgets_the_milestones(board):
    import wrap_up
    _, tmp_path = board
    preload(NATIVE, 99)
    assert call(event(tmp_path, tool_use_id="toolu_s")).returncode == 0
    assert list(wrap_up.stem(NATIVE).parent.glob(f"{wrap_up.stem(NATIVE).name}.*"))
    assert call({**event(tmp_path), "hook_event_name": "SubagentStop"}, "claude-subagent-stop").returncode == 0
    assert not list(wrap_up.stem(NATIVE).parent.glob(f"{wrap_up.stem(NATIVE).name}.*"))


def test_the_start_hook_records_when_the_subagent_began(board):
    import subagent_records
    _, tmp_path = board
    before = time.time()
    done = call({"hook_event_name": "SubagentStart", "agent_id": NATIVE, "agent_type": "general-purpose",
                 "session_id": LEAD, "cwd": str(tmp_path)}, "claude-subagent-start")
    assert done.returncode == 0
    assert before <= subagent_records.load(NATIVE)["started"] <= time.time()


def test_the_settings_call_the_hook_that_posts(board):
    """The command PostToolUse names in the tracked settings is run as written and posts the notice."""
    _, tmp_path = board
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    commands = [hook["command"] for group in settings["hooks"]["PostToolUse"] if not group.get("matcher")
                for hook in group["hooks"]]
    wired = [c for c in commands if c.endswith("/claude-wrap-up")]
    assert len(wired) == 1
    preload(NATIVE, 99)
    path = os.pathsep.join([os.environ["PATH"], str(Path(sys.executable).parent)])
    done = subprocess.run([wired[0].replace("$CLAUDE_PROJECT_DIR", str(ROOT))], input=json.dumps(event(tmp_path, tool_use_id="toolu_w")),
                          text=True, capture_output=True, timeout=60, check=False, env={**os.environ, "PATH": path})
    assert done.returncode == 0 and len(posts(tmp_path)) == 1
    assert os.access(HOOKS / "claude-wrap-up", os.X_OK)
    assert settings["hooks"]["PostToolUse"][-1]["hooks"][-1]["timeout"] >= 15


def test_the_lead_finds_the_notice_in_its_inbox(workspace_node, tmp_path, monkeypatch):
    """On a real board, the notice a subagent posts reaches the inbox of the lead that follows it."""
    shim = tmp_path / "shim" / "poolhouse-workspace"
    shim.parent.mkdir()
    shim.write_text(f"#!{sys.executable}\nimport sys\nsys.argv[0] = 'poolhouse-workspace'\n"
                    "from poolhouse.workspace.cli import main\nsys.exit(main())\n")
    shim.chmod(0o700)
    monkeypatch.setenv("PATH", os.pathsep.join([str(shim.parent), str(Path(sys.executable).parent), str(Path(shutil.which("git")).parent)]))
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    monkeypatch.setenv("POOLHOUSE_HOOK_STATE", str(tmp_path / "hookstate"))
    monkeypatch.syspath_prepend(str(HOOKS))
    monkeypatch.chdir(tmp_path)
    lead = workspace_node.member(LEAD)
    child = workspace_node.member(NATIVE, parent=lead)
    workspace_node.session(lead).subscribe("agent", child.name, force=True)
    preload(NATIVE, 99)
    done = call(event(tmp_path, tool_use_id="toolu_real"))
    assert done.returncode == 0 and not done.stderr, done.stderr
    bodies = [e.text for e in workspace_node.session(lead).read(inbox=True).entries]
    assert any(child.name in body and "100 tool calls" in body for body in bodies), bodies
