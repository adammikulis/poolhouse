"""The one notice for a source that was reliable and just was not: sentinel's single-flight
dialog (one at a time machine-wide, no quiet after one: a source that diverges right after a dialog is asked at once, and a
dialog nobody answered is asked again at the next poll; off when ``POOLHOUSE_NOTIFY`` says so) with a button that does something."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from poolhouse import desktop
from poolhouse.reputation.store import Ledger, Standing
from poolhouse.sentinel.heads_up import LATER_S, SHOWN_MOST, HeadsUp, Wires

__all__ = ["BLOCK", "BUTTONS", "LATER", "WATCHING", "Notifier"]

LATER, WATCHING, BLOCK = "Later", "Keep watching", "Block it"
BUTTONS = (LATER, WATCHING, BLOCK)
"""The safe button first, so Escape and closing the window mean Later."""

WHAT = {
    "hash_change": "served a file that differs from the one it served before",
    "cert_or_key_change": "presented a different certificate or key",
    "scan_hit": "served something a scanner flagged",
    "injection_flagged": "served text that reads like an instruction to an assistant",
    "denial": "was refused or failed authentication",
    "redirect_change": "now redirects somewhere new",
    "ip_change": "moved to a different address range",
}


def _id(held: Standing) -> str:
    return f"{held.kind}:{held.key}"


def _because(held: Standing) -> str:
    last = held.events[-1][1] if held.events else ""
    return WHAT.get(str(last), "behaved differently")


class Notifier(HeadsUp):
    """Raises the dialog for sources the ledger stepped down to watch."""

    def __init__(self, state: Path, *, clock: Callable[[], float], ledger: Ledger,
                 wires: Wires | None = None) -> None:
        super().__init__(state, clock=clock, store=ledger, wires=wires)  # type: ignore[arg-type]
        self.ledger = ledger

    def on_divergence(self) -> None:
        """Ask now unless the dialog is off. Never raises."""
        if self.enabled():
            self.spawn(self.prompt)

    answers = (LATER, WATCHING, BLOCK)

    def _pending(self, memo: dict[str, Any], *, everything: bool) -> list[Any]:
        now = self.clock()
        return [r for r in self.ledger.queued() if everything or memo["later"].get(_id(r), 0.0) <= now]

    def _ask(self, everything: bool, seen: set[str]) -> str:
        now, memo = self.clock(), self._load()
        rows = [r for r in (self._pending(memo, everything=everything) if self.enabled() else []) if _id(r) not in seen]
        if not rows:
            return ""
        shown = rows[:SHOWN_MOST]
        seen.update(_id(r) for r in shown)
        names = "; ".join(f"{desktop.clean(r.key, 60)} ({r.kind}) {_because(r)}" for r in shown)
        more = f" {len(rows) - len(shown)} more are not listed." if len(rows) > len(shown) else ""
        title = "Poolhouse changed how it treats a source it trusted"
        body = (f"{names}.{more} It had a clean record and now poolhouse asks before using it. "
                "Keep watching leaves it that way. Block it refuses it until you clear it. "
                "Later asks again in a few hours.")
        answer = (self.choose or self._desktop)(title, body, BUTTONS)
        for held in shown:
            if answer == BLOCK:
                self.ledger.block(held.kind, held.key)
            elif answer == WATCHING:
                self.ledger.settle_notice(held.kind, held.key, "done")
        if answer == LATER:
            memo["later"].update(dict.fromkeys((_id(r) for r in shown), now + LATER_S))
        self._save(memo)
        return str(answer)
