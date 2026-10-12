"""A stand-in for the part of `winpty.PtyProcess` (pywinpty) that the pool check's Windows terminal uses (`tests/pool_check/terminal.py`), so the route
that types at a ConPTY pseudo-console is run where there is no Windows: output the test feeds, input the rig wrote, a process id and an end."""

from __future__ import annotations

import queue
from collections.abc import Sequence


class ScriptedConPty:
    """One pseudo-console process: `spawn` makes it, `feed` is what the console prints, `written` is what was typed at it."""

    last: ScriptedConPty | None = None

    def __init__(self, argv: Sequence[str], dimensions: tuple[int, int]) -> None:
        self.argv, self.dimensions, self.pid = list(argv), dimensions, 4242
        self.out: queue.Queue[str | None] = queue.Queue()
        self.written: list[str] = []
        self.ended = False
        ScriptedConPty.last = self

    @classmethod
    def spawn(cls, argv: Sequence[str], dimensions: tuple[int, int] = (24, 80)) -> ScriptedConPty:
        return cls(argv, dimensions)

    def feed(self, text: str) -> None:
        """Print ``text`` on the console, as the node does when it writes its question."""
        self.out.put(text)

    def read(self, size: int = 1024) -> str:
        got = self.out.get(timeout=5)
        if got is None:
            raise EOFError
        return got

    def write(self, text: str) -> None:
        self.written.append(text)

    def terminate(self, force: bool = False) -> None:
        self.ended = True
        self.out.put(None)

    def close(self, force: bool = False) -> None:
        pass
