"""The terminal the rig's person types its yes at: a terminal of this device that the node writes on and reads itself.

On Mac and Linux that is a pseudo-terminal (`pty`), named to the node by its device path. On Windows it is a ConPTY pseudo-console (`pywinpty`),
named by the id of the process that runs in it: the node is detached from any console, so it attaches to that one (`sys::Console` in the node).
Both are the node as shipped, with no switch that answers for anyone: the person types `yes` where the node wrote its question.
"""

from __future__ import annotations

import os
import sys
import threading
import time


def screen_says(master: int, text: str, seconds: float) -> bool:
    """Whether the pseudo-terminal's person side shows ``text`` within ``seconds``."""
    import select

    end, shown = time.monotonic() + seconds, b""
    while time.monotonic() < end and text.encode() not in shown:
        if select.select([master], [], [], 0.2)[0]:
            shown += os.read(master, 4096)
    return text.encode() in shown


class PtyTerminal:
    """A pseudo-terminal: the node is given the device path of its person side and the rig types at its master side."""

    ENTER = "\n"

    def __init__(self) -> None:
        import pty

        self.master, self.slave = pty.openpty()
        self.name = os.ttyname(self.slave)

    def shows(self, text: str, seconds: float) -> bool:
        return screen_says(self.master, text, seconds)

    def types(self, text: str) -> None:
        os.write(self.master, text.encode())

    def close(self) -> None:
        os.close(self.master)
        os.close(self.slave)


class ConPtyTerminal:
    """A ConPTY pseudo-console with one idle process in it: the node is given that process's id and attaches to its console; the rig reads what the
    node wrote and types at the pseudo-console's input. The process only sleeps, so nothing else reads the keys the node waits for."""

    ENTER = "\r"
    COLUMNS = 200

    def __init__(self) -> None:
        try:
            from winpty import PtyProcess
        except ImportError as exc:  # pragma: no cover - only where pywinpty is missing
            raise RuntimeError("answering the node's question on Windows needs a ConPTY: python -m pip install pywinpty") from exc
        self.shown, self.lock = "", threading.Lock()
        self.process = PtyProcess.spawn([sys.executable, "-c", "import time; time.sleep(600)"], dimensions=(30, self.COLUMNS))
        self.name = str(self.process.pid)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        """Collect what the pseudo-console prints. It asks the host where the cursor is when it starts, and waits for the answer."""
        while True:
            try:
                got = self.process.read(4096)
            except (EOFError, OSError):  # the pseudo-console closed
                return
            with self.lock:
                self.shown += got
            if "\x1b[6n" in got:
                self.types("\x1b[1;1R")

    def shows(self, text: str, seconds: float) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            with self.lock:
                if text in self.shown:
                    return True
            time.sleep(0.1)
        return False

    def types(self, text: str) -> None:
        self.process.write(text)

    def close(self) -> None:
        try:
            self.process.terminate(force=True)
            self.process.close(force=True)
        except OSError:  # already gone
            pass


def at_terminal(platform: str | None = None) -> PtyTerminal | ConPtyTerminal:
    """The terminal of this device: a ConPTY on Windows, a pseudo-terminal everywhere else. ``platform`` is read from `sys.platform` when not given."""
    return ConPtyTerminal() if (platform or sys.platform) == "win32" else PtyTerminal()
