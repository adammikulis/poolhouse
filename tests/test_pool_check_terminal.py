"""Where the rig's person types its yes: a pseudo-terminal on Mac and Linux, a ConPTY pseudo-console on Windows (`pool_check.terminal`).

The Windows route is run here against a stand-in for `pywinpty` (nothing here is Windows); what it must do on a real ConPTY is checked by the
Windows jobs of `.github/workflows/pool-check.yml`. The Unix route is real: an actual pseudo-terminal the test reads and types at.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest
from pool_check import terminal, wire

from poolhouse.board import presence
from poolhouse.testing.conpty import ScriptedConPty

QUESTION = "\r\nallow it?\r\nType yes to allow it, anything else to refuse: "


@pytest.fixture
def conpty(monkeypatch):
    monkeypatch.setitem(sys.modules, "winpty", types.SimpleNamespace(PtyProcess=ScriptedConPty))
    ScriptedConPty.last = None
    return ScriptedConPty


def test_the_terminal_is_a_conpty_on_windows_and_a_pseudo_terminal_elsewhere(conpty):
    on_windows = terminal.at_terminal("win32")
    try:
        assert isinstance(on_windows, terminal.ConPtyTerminal) and on_windows.name == "4242", "the node is given the id of the process in the console, not a device path"
    finally:
        on_windows.close()
    assert ScriptedConPty.last.ended, "closing the terminal ends the process that holds the pseudo-console"
    for platform in ("darwin", "linux"):
        elsewhere = terminal.at_terminal(platform)
        try:
            assert isinstance(elsewhere, terminal.PtyTerminal) and Path(elsewhere.name).exists(), platform
        finally:
            elsewhere.close()


def test_the_platform_is_read_from_sys_when_none_is_named(monkeypatch, conpty):
    monkeypatch.setattr(sys, "platform", "win32")
    chosen = terminal.at_terminal()
    monkeypatch.undo()
    chosen.close()
    assert isinstance(chosen, terminal.ConPtyTerminal)
    elsewhere = terminal.at_terminal()
    elsewhere.close()
    assert isinstance(elsewhere, terminal.PtyTerminal), "this machine's own platform picks the pseudo-terminal"


def test_the_conpty_terminal_shows_what_the_node_wrote_answers_the_cursor_query_and_types_a_carriage_return(conpty):
    on_windows = terminal.at_terminal("win32")
    try:
        console = ScriptedConPty.last
        console.feed("\x1b[6n")
        deadline = time.monotonic() + 5
        while "\x1b[1;1R" not in console.written and time.monotonic() < deadline:
            time.sleep(0.05)
        assert "\x1b[1;1R" in console.written, "a ConPTY waits for its host to say where the cursor is"
        assert not on_windows.shows("Type yes", 0.2)
        console.feed(QUESTION)
        assert on_windows.shows("Type yes", 5)
        on_windows.types("yes" + on_windows.ENTER)
        assert console.written[-1] == "yes\r"
        assert console.argv[0] == sys.executable and console.dimensions[1] >= 120, "an idle process, in a console wide enough that the question does not wrap"
    finally:
        on_windows.close()


def test_the_unix_terminal_is_a_real_pseudo_terminal_the_node_could_write_to_and_read_from():
    here = terminal.at_terminal("linux")
    try:
        with Path(here.name).open("wb", buffering=0) as node_side:
            node_side.write(b"\r\nType yes to allow it: ")
        assert here.shows("Type yes", 5)
        here.types("yes" + here.ENTER)
        with Path(here.name).open("rb", buffering=0) as node_side:
            got = b""
            deadline = time.monotonic() + 5
            while b"\n" not in got and time.monotonic() < deadline:
                got += node_side.read(16)
        assert got.strip() == b"yes"
    finally:
        here.close()


class AskingNode:
    """A node that asks the way the real one does: writes its question where it was told to and reads the line typed there, and says confirmed
    only for `yes` and Enter."""

    def __init__(self, on_windows: bool) -> None:
        self.on_windows, self.terminal, self.device, self.typed = on_windows, "", -1, b""

    def call(self, _state, method, _board="", _token="", params=None):
        if method == "presence_ask":
            self.terminal = params["terminal"]
            if self.on_windows:
                ScriptedConPty.last.feed(QUESTION)
            else:
                self.device = os.open(self.terminal, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
                os.write(self.device, QUESTION.encode())
            return {"ok": True, "result": {"state": "asking"}}
        if self.on_windows:
            yes = "".join(ScriptedConPty.last.written) == "yes\r"
        else:
            with contextlib.suppress(BlockingIOError):
                self.typed += os.read(self.device, 64)
            yes = self.typed.strip() == b"yes"
        if yes and self.device >= 0:
            os.close(self.device)
        return {"ok": True, "result": {"state": "confirmed" if yes else "asking", "secret": "s"}}


def test_the_rigs_person_answers_with_a_conpty_on_windows_and_a_pseudo_terminal_elsewhere(monkeypatch, conpty):
    windows_node = AskingNode(on_windows=True)
    monkeypatch.setattr(wire, "call", windows_node.call)
    with monkeypatch.context() as on_windows:
        on_windows.setattr(sys, "platform", "win32")
        said = wire.answer_presence(Path(), "tok", "key", "ab")
    assert said["result"]["state"] == "confirmed", said
    assert windows_node.terminal == "4242" and ScriptedConPty.last.written[-1] == "yes\r" and ScriptedConPty.last.ended
    unix_node = AskingNode(on_windows=False)
    monkeypatch.setattr(wire, "call", unix_node.call)
    said = wire.answer_presence(Path(), "tok", "key", "ab")
    assert said["result"]["state"] == "confirmed" and unix_node.terminal.startswith("/dev/"), (said, unix_node.terminal)


@pytest.mark.parametrize("attached", [1, 0])
def test_a_client_on_windows_names_the_console_it_runs_in_by_its_process_id_and_none_when_it_has_none(monkeypatch, attached):
    import ctypes

    kernel = types.SimpleNamespace(GetConsoleProcessList=lambda _slot, _count: attached)
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=kernel), raising=False)
    with monkeypatch.context() as on_windows:
        on_windows.setattr(sys, "platform", "win32")
        assert presence.terminal_name() == (str(os.getpid()) if attached else "")


def test_wire_runs_as_a_script_where_only_the_rigs_own_directory_is_on_the_path():
    """The rig runs `wire.py` by its path on every device (a container has only the rig's directory), so its terminal module is found beside it."""
    done = subprocess.run([sys.executable, str(Path(wire.__file__)), "--help"], capture_output=True, text=True, timeout=60, check=False, cwd=Path(wire.__file__).parent)
    assert done.returncode == 0 and "presence" in done.stdout, done.stderr
