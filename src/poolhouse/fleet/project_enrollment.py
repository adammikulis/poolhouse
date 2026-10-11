"""Pool-scoped admission of a local project registration."""

import hmac

from .discovery import derive_token, memberships


def _matched(opening, rows):
    if opening is None or not opening[2]:
        return None
    found = [member for member in rows
             if hmac.compare_digest(opening[1].secret, derive_token(member.key))]
    return found[0] if len(found) == 1 else None


def local(opening, path, source: str) -> bool:
    """Whether an authenticated loopback request proves the active Dev membership."""
    if source not in {"127.0.0.1", "::1"}:
        return False
    rows = memberships(path)
    member = _matched(opening, rows)
    return bool(rows and member is rows[0] and getattr(member, "mode", "prod") == "dev")
