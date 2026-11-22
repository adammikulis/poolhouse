"""``poolhouse-security review``: everything sentinel holds, as a list.

Viewing is not privileged: `held_items` and `table` work anywhere, for an agent too, and only
ever print what `explain` has made safe. Acting is `poolhouse-security quarantine release|purge ID`,
which the quarantine gate decides at once.
"""

from __future__ import annotations

from poolhouse.sentinel import explain
from poolhouse.sentinel.explain import Item
from poolhouse.sentinel.store import State, Store

__all__ = ["held_items", "hint", "table"]

COMMAND = "poolhouse-security review"


def held_items(store: Store, now: float) -> list[Item]:
    """Every quarantined and watched subject, newest first, numbered from 1."""
    rows = [r for r in store.records() if r.state in (State.QUARANTINED, State.WATCH)]
    rows.sort(key=lambda r: (r.updated, r.created, r.id), reverse=True)
    return [explain.describe(r, n, now) for n, r in enumerate(rows, 1)]


def hint(items: list[Item]) -> str:
    """``2 held: member 127.0.0.1 (forged traffic), server :51089. Review them: ...``, or
    an empty string when nothing is held."""
    if not items:
        return ""
    names = ", ".join(f"{i.name}" + (f" ({explain.short_code(i.code)})" if i.code else "")
                      for i in items[:3])
    more = f" and {len(items) - 3} more" if len(items) > 3 else ""
    return f"{len(items)} held: {names}{more}. Review them: {COMMAND}"


def table(items: list[Item]) -> list[str]:
    """The listing ``review --list`` prints: four lines per subject."""
    if not items:
        return ["nothing is held"]
    out: list[str] = []
    for i in items:
        out += [f"{i.number:>2}  {i.state.upper():<11} {i.name}   {i.age}",
                f"    why:    {i.why}", f"    blocks: {i.blocks}", f"    next:   {i.advice}"]
    return out
