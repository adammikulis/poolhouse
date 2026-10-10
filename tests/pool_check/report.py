"""PASS or FAIL per step, with the exact failing output, and the exit status of the whole check."""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field

from poolhouse.log import say


@dataclass
class Result:
    """One step: its name, whether it held, how long it took and (when it did not) everything the failure said."""

    name: str
    ok: bool
    seconds: float
    detail: str = ""
    skipped: bool = False


@dataclass
class Report:
    """The steps run so far; ``step`` runs one and prints its line at once, so a long check shows progress."""

    results: list[Result] = field(default_factory=list)
    emit: Callable[[str], None] = say

    def step(self, name: str, body: Callable[[], str | None]) -> Result:
        """Run ``body``; an AssertionError or any other exception is a FAIL carrying its message and the frame it came from."""
        began = time.monotonic()
        note = ""
        try:
            note = body() or ""
        except Skip as why:
            result = Result(name, True, time.monotonic() - began, str(why), skipped=True)
        except AssertionError as failure:
            result = Result(name, False, time.monotonic() - began, _tail(failure))
        except Exception as failure:  # noqa: BLE001 - every defect a step finds is reported, never raised past the rig
            result = Result(name, False, time.monotonic() - began, _tail(failure, trace=True))
        else:
            result = Result(name, True, time.monotonic() - began, note)
        self.results.append(result)
        self.emit(self.line(result))
        return result

    @staticmethod
    def line(result: Result) -> str:
        word = "SKIP" if result.skipped else "PASS" if result.ok else "FAIL"
        head = f"{word}  {result.name}  ({result.seconds:.1f}s)"
        if result.skipped or not result.ok:
            return head + "\n" + "\n".join(f"      {row}" for row in result.detail.splitlines())
        return head + ("\n" + "\n".join(f"      {row}" for row in result.detail.splitlines()) if result.detail else "")

    @property
    def failed(self) -> list[Result]:
        return [r for r in self.results if not r.ok]

    def summary(self) -> str:
        ran = [r for r in self.results if not r.skipped]
        text = f"{len(ran) - len(self.failed)} of {len(ran)} steps passed"
        skipped = len(self.results) - len(ran)
        text += f", {skipped} skipped" if skipped else ""
        return text + (f"; FAILED: {', '.join(r.name for r in self.failed)}" if self.failed else "")


class Skip(Exception):
    """The step cannot run here, and says why (a missing binary, too few devices); never a pass in disguise."""


def _tail(failure: BaseException, trace: bool = False) -> str:
    text = str(failure).strip() or type(failure).__name__
    if trace:
        text += "\n" + "".join(traceback.format_tb(failure.__traceback__)[-4:]).strip()
    return text[-4000:]
