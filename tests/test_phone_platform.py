"""Linked phones on each operating system (docs/phone-sessions.md): the written hooks run as real processes with no shell and no PATH,
speak UTF-8 whatever the console uses, end with the harness that started them, and the listener's port and addresses are the right
ones on macOS and on Windows."""

from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath, PureWindowsPath

import psutil
import pytest
from phone_kit import linked

import poolhouse
from poolhouse import agent_hooks, phone_hook
from poolhouse.cli import phone as command
from poolhouse.fleet import phone as phones
from poolhouse.fleet.phone import PhoneService, server
from poolhouse.fleet.ui import UI
from poolhouse.phone_mailbox import Store

SESSION = "7c1e"
MAIN = f"claude-{SESSION}"
WORDS = "gr\u00fc\u00dfe \u2013 \u65e5\u672c\u8a9e \u2713"


@pytest.fixture
def state(tmp_path, monkeypatch):
    """An isolated state folder, shared by this process and the hook processes it starts."""
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "state"))
    return Store()


def _environment(**more):
    """What a hook process of this source tree is started with."""
    found = {**os.environ, "PYTHONPATH": str(Path(poolhouse.__file__).resolve().parent.parent), "CLAUDE_CODE_ENTRYPOINT": "cli",
             **more}
    found.pop("PYTHONUTF8", None)
    return found


def _event(tmp_path, **more):
    return {"session_id": SESSION, "cwd": str(tmp_path), "transcript_path": str(tmp_path / "none.jsonl"), **more}


def _written(project, name):
    held = json.loads((project / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    return next(hook for groups in held["hooks"].values() for group in groups for hook in group["hooks"] if hook["args"][-1] == name)


def test_a_written_hook_runs_without_a_shell_or_a_path_and_imports_nothing_of_the_project(tmp_path, state):
    project = tmp_path / "pro ject ü"
    (project / "poolhouse").mkdir(parents=True)
    (project / "poolhouse" / "__init__.py").write_text("raise SystemExit('the project was imported')\n", encoding="utf-8")
    agent_hooks.phone(project)
    hook = _written(project, "session-start")
    assert Path(hook["command"]).is_absolute() and hook["args"][-3:] == ["poolhouse.cli.phone", "hook", "session-start"]
    done = subprocess.run([hook["command"], *hook["args"]], input=json.dumps(_event(project)).encode(), cwd=project,
                          env=_environment(PATH=""), capture_output=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    assert b"carries that authority" in done.stdout
    assert state.session(MAIN)["connected"] is True


def test_hooks_speak_utf8_whatever_the_console_uses(tmp_path, state):
    key = linked(state)
    state.send(MAIN, key.message(MAIN, WORDS))
    run = [sys.executable, "-P", "-m", "poolhouse.cli.phone", "hook"]
    console = _environment(PYTHONIOENCODING="cp1252")
    woke = subprocess.run([*run, "listen"], input=json.dumps(_event(tmp_path), ensure_ascii=False).encode(), env=console,
                          capture_output=True, timeout=60, check=False)
    assert woke.returncode == 2 and WORDS in woke.stderr.decode("utf-8"), woke.stderr
    ended = _event(tmp_path, last_assistant_message=f"fertig {WORDS}")
    done = subprocess.run([*run, "stop"], input=json.dumps(ended, ensure_ascii=False).encode(), env=console, capture_output=True,
                          timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    assert [e["text"] for e in state.events(MAIN) if e["type"] == "reply"] == [f"fertig {WORDS}"]


def test_a_listener_ends_with_its_harness_though_a_launcher_between_them_lives(tmp_path, state):
    """A Windows virtual environment's python.exe is a launcher that runs the real interpreter as its child and outlives the harness."""
    listener = [sys.executable, "-P", "-m", "poolhouse.cli.phone", "hook", "listen"]
    launcher = f"import subprocess, sys; sys.exit(subprocess.run({listener!r}).returncode)"
    harness = ("import subprocess, sys, time\n"
               f"started = subprocess.Popen([sys.executable, '-c', {launcher!r}], stdin=subprocess.PIPE)\n"
               f"started.stdin.write({json.dumps(_event(tmp_path)).encode()!r})\n"
               "started.stdin.close()\n"
               "time.sleep(300)\n")
    run = subprocess.Popen([sys.executable, "-c", harness], env=_environment())
    tree = []
    try:
        until = time.monotonic() + 60
        while not state.session(MAIN).get("listener", {}).get("pid"):
            assert time.monotonic() < until and run.poll() is None, "no listener started"
            time.sleep(0.2)
        listening = psutil.Process(state.session(MAIN)["listener"]["pid"])
        tree = psutil.Process(run.pid).children(recursive=True)
        time.sleep(2 * phone_hook.LOOK_S)
        assert listening.is_running(), "the listener ended while its harness lived"
        run.kill()
        run.wait(timeout=30)
        listening.wait(timeout=30)
    finally:
        for each in [*tree, run]:
            with contextlib.suppress(psutil.Error, OSError):
                each.kill()


def test_a_worktrees_session_counts_as_its_project_with_either_separator():
    windows = r"C:\Users\a\proj\.claude\worktrees\agent-1\sub"
    assert phones.project_folder(windows, PureWindowsPath) == r"C:\Users\a\proj"
    assert phones.project_folder("/work/proj/.claude/worktrees/agent-1", PurePosixPath) == "/work/proj"
    assert phones.project_folder("/work/proj/.claude", PurePosixPath) == "/work/proj/.claude"


def test_the_address_the_computer_reaches_its_network_by_comes_first():
    found = ["127.0.0.1", "100.64.0.9", "172.20.0.1", "192.168.1.5"]
    assert server.ordered(found, "192.168.1.5") == ["192.168.1.5", "172.20.0.1", "100.64.0.9", "127.0.0.1"]
    assert server.ordered(found, "") == ["172.20.0.1", "192.168.1.5", "100.64.0.9", "127.0.0.1"]


def test_no_other_process_can_take_the_listeners_port(tmp_path, state):
    service = PhoneService(UI(name="Studio", gated=False), None, store=Store(tmp_path / "phones"), port=0,
                           addresses=lambda: ["127.0.0.1"])
    try:
        linked(service.store)
        service.sync()
        taker = socket.socket()
        with taker:
            taker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with pytest.raises(OSError):
                taker.bind(("127.0.0.1", service.listening["port"]))
    finally:
        service.close()


def test_a_start_brings_the_hooks_of_connected_projects_to_this_installation(tmp_path, state):
    project = tmp_path / "work"
    agent_hooks.phone(_made(project), run=[str(tmp_path / "moved" / "python"), "-m", "poolhouse.cli.phone"])
    store = Store(tmp_path / "phones")
    store.project(str(project.resolve()), True)
    service = PhoneService(UI(name="Studio", gated=False), None, store=store, port=0, addresses=lambda: ["127.0.0.1"])
    try:
        service.sync()
        assert _written(project, "stop")["command"] == agent_hooks.phone_command()[0]
    finally:
        service.close()


def _made(folder):
    folder.mkdir(parents=True)
    return folder


def test_a_console_that_cannot_draw_the_code_still_shows_it(monkeypatch):
    class Daemon:
        def call(self, method, path, payload=None):
            return {"link": "poolhouse://link?v=1", "endpoints": ["192.168.1.5:7450"], "code": "ABCDE-FGHJK"}

    monkeypatch.setattr(command, "_Daemon", Daemon)
    monkeypatch.setattr(command, "_persons", lambda _action: None)
    shown = io.BytesIO()
    console = io.TextIOWrapper(shown, encoding="cp1252")
    monkeypatch.setattr("sys.stdout", console)
    assert command.main(["link"]) == 0
    console.flush()
    assert b"ABCDE-FGHJK" in shown.getvalue() and b"192.168.1.5:7450" in shown.getvalue()
