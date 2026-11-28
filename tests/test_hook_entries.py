"""Every hook `.claude/settings.json` wires, run as it is written, through one session on a real isolated board.

A hook that crashes is shown to the person only, so this is where a crash is found: the exit status, the error output, the
shape of the answer, the time limit the settings give it, and what its background check wrote. A hook added to the settings
without a case here fails the test.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from poolhouse.net import git

pytest_plugins = ["node_kit"]
ROOT = Path(__file__).resolve().parents[1]
SESSION, AGENT = "entry-session-1", "a1b2c3d4e5f6a7b8"
UNLIMITED_S = 60.0
"""What a hook the settings give no limit may take here."""


def wired() -> list[tuple[str, str, float]]:
    """``(event, command, limit)`` of every hook the settings run, in the order a session raises the events."""
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    order = ["SessionStart", "UserPromptSubmit", "SubagentStart", "PreToolUse", "PostToolUse", "SubagentStop", "SessionEnd"]
    assert set(settings["hooks"]) <= set(order), "an event this test raises no case for is wired"
    return [(event, hook["command"], float(hook.get("timeout", UNLIMITED_S)))
            for event in order for group in settings["hooks"].get(event, []) for hook in group["hooks"]]


def events(event: str, name: str, project: Path) -> list[dict]:
    """The inputs the harness would give ``name`` at ``event``: the main session's, and a subagent's where it has them."""
    base = {"hook_event_name": event, "session_id": SESSION, "cwd": str(project),
            "transcript_path": str(project / f"{SESSION}.jsonl")}
    child = {**base, "agent_id": AGENT, "agent_type": "general-purpose"}
    call = {"tool_use_id": "toolu_entry", "tool_name": "Read", "tool_input": {"file_path": str(project / "note.txt")},
            "tool_response": {"ok": True}}
    tools = {"claude-bash-guard": ("Bash", {"command": "git status"}),
             "claude-edit-guard": ("Write", {"file_path": str(ROOT / "src" / "poolhouse" / "entry_case.py"),
                                             "content": '"""A case."""\n'}),
             "claude-tool-guard": ("Read", {"file_path": str(project / "note.txt")})}
    if event == "PreToolUse":
        tool, given = tools[name]
        return [{**one, "tool_use_id": "toolu_entry", "tool_name": tool, "tool_input": given} for one in (base, child)]
    return {"SessionStart": [{**base, "source": "startup", "model": "claude-sonnet-4-6"}],
            "UserPromptSubmit": [{**base, "prompt": "hello"}],
            "SubagentStart": [child],
            "PostToolUse": [{**base, **call}, {**child, **call}],
            "SubagentStop": [child],
            "SessionEnd": [{**base, "reason": "other"}]}[event]


def test_every_wired_hook_runs_end_to_end_within_its_limit(tmp_path, monkeypatch, workspace_node):
    shim = tmp_path / "shim" / "poolhouse-workspace"
    shim.parent.mkdir()
    shim.write_text(f"#!{sys.executable}\nimport sys\nsys.argv[0] = 'poolhouse-workspace'\n"
                    "from poolhouse.workspace.cli import main\nsys.exit(main())\n")
    shim.chmod(0o700)
    monkeypatch.setenv("PATH", os.pathsep.join([str(shim.parent), str(Path(sys.executable).parent),
                                                str(Path(shutil.which("git")).parent)]))
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    state = tmp_path / "hookstate"
    monkeypatch.setenv("POOLHOUSE_HOOK_STATE", str(state))
    monkeypatch.setenv("POOLHOUSE_GUARD_CACHE", str(tmp_path / "guard-cache"))
    # The attention check is due at every event and is waited for, so what it does is seen here; no status command is named.
    monkeypatch.setenv("POOLHOUSE_ATTENTION_EVERY_S", "0")
    monkeypatch.setenv("POOLHOUSE_ATTENTION_WAIT_S", "12")
    for name in ("POOLHOUSE_STATUS_COMMAND", "CLAUDE_ENV_FILE", "CLAUDE_CONFIG_DIR", "POOLHOUSE_GUARD", "CODEX_THREAD_ID",
                 "CODEX_SESSION_ID", "POOLHOUSE_AGENT"):
        monkeypatch.delenv(name, raising=False)
    owner = workspace_node.member("notification-project-owner")
    workspace_node.allow(owner, "source_add")
    workspace_node.client.call("project_add", "", owner.token, id=workspace_node.board, kind="folder", path=str(tmp_path))
    project = tmp_path / "project"
    git.run(["init", "-b", "development", str(project)])
    git.run(["-c", "user.name=Hook fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-m",
             "fixture project"], cwd=project)
    (project / f"{SESSION}.jsonl").write_text("")
    monkeypatch.chdir(project)

    failures, ran = [], set()
    for event, command, limit in wired():
        script = Path(command.replace("$CLAUDE_PROJECT_DIR", str(ROOT)))
        assert os.access(script, os.X_OK), f"{command} cannot be run"
        for given in events(event, script.name, project):
            if script.name == "claude-tool-milestones" and given.get("agent_id"):
                (state / "tool-count").mkdir(parents=True, exist_ok=True)
                for count in (state / "tool-count").glob("*.count"):
                    count.write_bytes(b"." * 99)  # this call reaches the milestone, so the post to the board runs too
            who = f"{event} {script.name}{' (subagent)' if given.get('agent_id') else ''}"
            began = time.monotonic()
            try:
                done = subprocess.run([str(script)], input=json.dumps(given), text=True, capture_output=True, timeout=limit,
                                      check=False)
            except subprocess.TimeoutExpired:
                failures.append(f"{who}: not finished within {limit:g} s")
                continue
            ran.add((event, command))
            if done.returncode != 0 or "Traceback" in done.stderr:
                failures.append(f"{who}: exit {done.returncode} after {time.monotonic() - began:.1f} s: {done.stderr[-600:]}")
            if done.stdout.strip():
                answer = json.loads(done.stdout)
                said = answer.get("hookSpecificOutput", {})
                assert said.get("hookEventName", event) == event, f"{who}: answered for another event"
                assert "did not do its work" not in done.stdout, f"{who}: {done.stdout}"
        for log in state.glob("attention/*.log"):
            if "Traceback" in (text := log.read_text(errors="replace")):
                failures.append(f"{event} {script.name}: its background check crashed: {text[-600:]}")
                log.unlink()
    assert not failures, "\n".join(failures)
    assert ran == {(event, command) for event, command, _ in wired()}
