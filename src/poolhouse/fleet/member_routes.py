"""The daemon's routes about who is in its pools: who is asking, and the exchange of records.

``GET /fleet/v1/self`` says which device the daemon took the caller to be. ``POST
/fleet/v1/members`` takes a member's record of one pool, merges it (a revocation is never
undone) and answers with this machine's, so one round trip leaves both holding the union.
Both are for a caller that presented a member's certificate.
"""

from __future__ import annotations

import json
from typing import Any

from .pool_roster import Pool

__all__ = ["answer"]

SELF, MEMBERS = "/fleet/v1/self", "/fleet/v1/members"


def answer(handler: Any, roster: Pool, method: str, path: str, body: bytes | None) -> bool:
    """Handle a members route for the caller `handler._guard` accepted; False for any other path."""
    if path == SELF and method == "GET":
        member = handler._member
        handler._send(200, {"fingerprint": member, "groups": sorted(roster.groups_of(member)) if member else []})
        return True
    if path != MEMBERS or method != "POST":
        return False
    member = handler._member
    try:
        asked = json.loads(body or b"{}")
        group, rows = str(asked["group"]), asked["rows"]
        if not isinstance(rows, list):
            raise TypeError("rows")
    except (ValueError, KeyError, TypeError):
        handler._send(400, {"error": "send {group, rows}"})
        return True
    if not member or group not in roster.groups_of(member):
        handler._send(403, {"error": "only a device in that pool exchanges its membership"})
        return True
    roster.merge(group, rows, member)
    handler._send(200, {"group": group, "rows": roster.export(group)})
    return True
