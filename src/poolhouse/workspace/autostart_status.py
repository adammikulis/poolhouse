"""The autostart state in `poolhouse-workspace status`, and the one board line a drifted unit earns."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from typing import Any

from poolhouse.board.client import NodeError
from poolhouse.errors import Denied
from poolhouse.fleet import autostart_check, autostart_ledger
from poolhouse.workspace.screen import Refused

__all__ = ["REPEAT_S", "fields"]

REPEAT_S = 24 * 3600
LIMIT = 190


def _post(announce: Callable[[str, str], Any], report: autostart_check.Report, now: float) -> None:
    """Announce a drift under the caller's own identity unless the same drift was announced within a day."""
    ledger = autostart_ledger.read()
    seen = hashlib.sha256("\n".join(report.reasons).encode()).hexdigest()[:16]
    last = ledger["reported"]
    if last.get("fingerprint") == seen and now - float(last.get("at", 0)) < REPEAT_S:
        return
    text = f"autostart drifted: {report.reasons[0]}"[:LIMIT].replace("\n", " ")
    try:
        announce("blocked", text)
    except (Denied, Refused, NodeError):
        return
    autostart_ledger.write({**ledger, "reported": {"fingerprint": seen, "at": now}})


def fields(announce: Callable[[str, str], Any], *, now: float = 0.0) -> dict[str, Any]:
    """``{"autostart": {...}}`` for a status listing; reports a drift to the board once a day."""
    try:
        report = autostart_check.check()
    except (OSError, ValueError, RuntimeError, KeyError):
        return {"autostart": {"state": "unknown", "reasons": [], "manifest": ""}}
    if report.state == "drifted":
        _post(announce, report, now or time.time())
    return {"autostart": {"state": report.state, "reasons": list(report.reasons[:3]), "manifest": report.manifest}}
