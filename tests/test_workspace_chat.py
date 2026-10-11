"""The person's terminal chat on the node: a room or a conversation streamed both ways, and who may use it."""

from __future__ import annotations

import io
import os
import pty
import queue
import select
import subprocess
import sys
import threading
import time

import pytest
from local_kit import waited

from poolhouse.board import person
from poolhouse.errors import Denied
from poolhouse.workspace import chat

pytest_plugins = ["node_kit"]
SECRET = "token sk-ant-abcdefghijklmnopqrstuvwxyz0123"


def started(me, target):
    """A chat of the person's session ``me`` running in a thread: the lines it prints, a way to type and a way to stop it."""
    typed, shown, stop = queue.Queue(), [], threading.Event()
    done = threading.Thread(target=chat.run, args=(me, target, chat.Console(iter(typed.get, None), shown.append, stop)), daemon=True)
    done.start()
    return typed, shown, done


def test_chat_streams_a_board_both_ways_and_sends_what_is_typed(workspace_node):
    node = workspace_node
    alice = node.session(node.member("alice"))
    alice.room("create", "#ops")
    alice.post("#ops", "note", "earlier\x07 line")
    me = person.on(node.client, node.board)
    typed, shown, done = started(me, chat.Target(board="#ops", backlog=5))
    waited(lambda: any("earlier" in line for line in shown))
    assert not any("\x07" in line for line in shown)
    began = time.monotonic()
    alice.post("#ops", "note", "live <b>message</b>")
    waited(lambda: any("live <b>message</b>" in line for line in shown))
    assert time.monotonic() - began < 2
    typed.put("hello from the person\n")
    typed.put("   \n")
    waited(lambda: any(f"{me.name}: hello from the person" in line for line in shown))
    assert [e.text for e in alice.read_all(channel="#ops") if e.sender == me.name] == ["hello from the person"]
    typed.put("/quit\n")
    done.join(10)
    assert not done.is_alive()


def test_chat_with_an_agent_shows_the_conversation_and_reports_a_refusal_without_ending(workspace_node):
    node = workspace_node
    alice = node.session(node.member("alice"))
    me = person.on(node.client, node.board)
    typed, shown, done = started(me, chat.Target(dm=alice.name))
    typed.put("one\n")
    typed.put(SECRET + "\n")
    typed.put("two\n")
    waited(lambda: any("! not sent" in line for line in shown))
    waited(lambda: any("two" in line for line in shown))
    assert [e.text for e in alice.read_all(inbox=True)] == ["one", "two"]
    assert not any("sk-ant" in line for line in shown)
    alice.post(me.name, "note", "an answer")
    waited(lambda: any(f"{alice.name}: an answer" in line for line in shown))
    typed.put("/quit\n")
    done.join(10)
    assert not done.is_alive()


def test_chat_refuses_an_agent_session_and_the_command_refuses_an_agent_process(workspace_node):
    node = workspace_node
    agent = node.member("alice")
    with pytest.raises(Denied, match="for the person"):
        chat.run(node.session(agent), chat.Target(board="#general"), chat.Console(io.StringIO(""), print))
    marked = node.cli("chat", "--room", "#general", who=agent, env={"CLAUDECODE": "1"})
    assert marked.returncode == 3 and "person" in marked.stderr + marked.stdout
    assert node.cli("chat", "--room", "#general", who=agent).returncode == 3, "a pipe is not a terminal"


def test_chat_follows_exactly_one_room_or_one_agent(workspace_node):
    me = person.on(workspace_node.client, workspace_node.board)
    for target in (chat.Target(), chat.Target(board="#general", dm="someone")):
        with pytest.raises(ValueError, match="one board"):
            chat.run(me, target, chat.Console(io.StringIO(""), print))


def test_the_chat_command_runs_for_a_person_at_a_terminal(workspace_node):
    node = workspace_node
    alice = node.session(node.member("alice"))
    alice.room("create", "#ops")
    leader, follower = pty.openpty()
    proc = subprocess.Popen([sys.executable, "-m", "poolhouse.workspace.cli", "chat", "--room", "#ops"], stdin=follower,
                            stdout=follower, stderr=follower, env=node.env(), close_fds=True)
    os.close(follower)
    seen = b""

    def pump(until: bytes, seconds: float = 20.0) -> bool:
        nonlocal seen
        end = time.monotonic() + seconds
        while time.monotonic() < end and until not in seen:
            if select.select([leader], [], [], 0.2)[0]:
                seen += os.read(leader, 4096)
        return until in seen

    def joined() -> bool:
        return proc.poll() is not None or any(r["name"] == "#ops" and len(r["members"]) == 2 for r in alice.rooms())

    try:
        waited(joined, 30)
        assert proc.poll() is None, pump(b"\0", 0.5) or seen.decode()
        alice.post("#ops", "note", "pty hello")
        assert pump(b"pty hello")
        os.write(leader, b"typed by the person\n")
        waited(lambda: any(e.text == "typed by the person" for e in alice.read_all(channel="#ops")))
        os.write(leader, b"/quit\n")
        proc.wait(timeout=20)
        assert proc.returncode == 0
    finally:
        proc.kill()
        os.close(leader)
    assert alice.token.encode() not in seen
