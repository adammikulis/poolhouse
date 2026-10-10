"""Serving a placement on each member, and showing what each member then serves."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from poolhouse.log import say

from .pausing import member_clients
from .remote import MemberError

__all__ = ["apply_plan", "serving_table"]


def apply_plan(placement: Any, rows: Sequence[dict[str, Any]], *,
               pool_key_path: Path | str | None = None,
               say: Callable[[str], None] = say) -> list[dict[str, Any]]:
    """``POST /serve`` on each placed member, and what each answered.

    Each answer is ``{"member", "model", "slots", "status", "served" | "error", "serving"}``;
    ``serving`` is the member's ``/health`` serving rows after the call.
    """
    clients = member_clients(rows, pool_key_path=pool_key_path, timeout=600.0)
    out: list[dict[str, Any]] = []
    for row in placement.rows:
        answer: dict[str, Any] = {"member": row.member, "model": row.model, "slots": row.slots}
        member = clients.get(row.member)
        if member is None:
            answer.update(status=0, error="no daemon answered for this member")
            out.append(answer)
            say(f"{row.member}: {row.model}: {answer['error']}")
            continue
        try:
            served = member._json("POST", "/serve", {"model": row.model, "context": row.context,
                                                   "parallel": row.slots})
            answer.update(status=201, served=served)
            say(f"{row.member}: {row.model}: {served.get('slots', row.slots)} slot(s) on "
                f"port {served.get('port', '?')}")
        except MemberError as exc:
            status, body = _refusal(str(exc))
            answer.update(status=status, error=body.get("error") or str(exc))
            say(f"{row.member}: {row.model}: {answer['error']}")
        try:
            answer["serving"] = list(member.health().get("serving") or [])
        except (MemberError, OSError, ValueError):
            answer["serving"] = []
        out.append(answer)
    return out


def serving_table(applied: Sequence[dict[str, Any]]) -> str:
    """What each member serves after an apply, as text."""
    lines = [f"{'MEMBER':<16} SERVING"]
    for answer in applied:
        cells = [f"{m}:{one.get('port', '?')} ({int(one.get('slots') or 1)} slot(s))"
                 for one in answer.get("serving") or [] for m in one.get("models") or []]
        lines.append(f"{answer['member']:<16} {', '.join(cells) or '-'}")
    return "\n".join(lines)


def _refusal(message: str) -> tuple[int, dict[str, Any]]:
    """The status and JSON body out of a `MemberError`'s message; ``(0, {})`` for none."""
    found = re.search(r"-> (\d{3}): (\{.*\})", message, re.S)
    if not found:
        return 0, {}
    try:
        return int(found.group(1)), json.loads(found.group(2))
    except ValueError:
        return int(found.group(1)), {}
