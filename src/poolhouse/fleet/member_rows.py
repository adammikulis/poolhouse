"""What a screen does with the rows of `roster.describe`: tell two devices of one name apart, and build the bench sweep line from a pick."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

__all__ = ["named_apart", "sweep_argv"]


def named_apart(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``rows``, each ``name`` two machines share followed by ``#`` and the shortest prefix of four or more
    characters of its ``machine`` that tells them apart."""
    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_name.setdefault(str(row.get("name") or ""), []).append(row)
    for name, same in by_name.items():
        machines = {str(row.get("machine") or "") for row in same}
        if len(machines) < 2:
            continue
        width = 4
        while len({m[:width] for m in machines}) < len(machines):
            width += 1
        for row in same:
            row["name"] = f"{name}#{str(row.get('machine') or '')[:width] or '?'}"
    return rows


def sweep_argv(models: Sequence[str], *, members: Sequence[str] = (), sample: int = 0,
               label: str = "", extra: Sequence[str] = ()) -> list[str]:
    """The ``python -m poolhouse.bench`` line that spreads ``models`` over the fleet, one job each.

    Built here rather than in the page, so the command the page shows is the command the daemon runs and the one a person
    could paste. ``--detach`` is not on it: the caller detaches, and hands the log back.
    """
    if not models:
        raise ValueError("a sweep needs at least one model")
    argv = ["sweep", "--fleet"]
    for m in models:
        argv += ["--serve", str(m)]
    if members:
        argv += ["--members", ",".join(members)]
    if sample:
        argv += ["--sample", str(int(sample))]
    if label:
        argv += ["--label", label]
    argv += [str(a) for a in extra]
    return argv
