"""The person's grant key: kept beside the node, and used only by a process a person is running (docs/node.md,
"Standing grants").

A command a person types, or the person's own screen, asks for what it needs; when a refusal says a grant is missing,
the key signs one for exactly that, good for one use and two minutes. A process an agent started has no key here.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from poolhouse import person
from poolhouse.board import grants
from poolhouse.fleet.onboard.signing import SigningKeys
from poolhouse.log import warn
from poolhouse.node_health import Refused, call

__all__ = ["KEY_DIR", "held", "kept", "keys", "provider", "retrying"]

KEY_DIR = "person"
LASTS_MS = 120_000


def keys(state: Path) -> SigningKeys:
    """The person's key of the node whose state directory is ``state``, made on first use of its public half."""
    return SigningKeys(Path(state) / KEY_DIR)


def kept(state: Path) -> bool:
    """Whether a person's key is kept for the node whose state directory is ``state``."""
    return (Path(state) / KEY_DIR / "signing.json").exists()


def held(state: Path, *, attended: bool = False) -> SigningKeys | None:
    """The person's key when one is kept here and this process is a person's: no agent marker, and a terminal on
    stdin and stdout, or a screen the person is sitting at (``attended``); else None."""
    if person.marked() or not kept(state):
        return None
    if not attended and not (person.is_terminal(sys.stdin) and person.is_terminal(sys.stdout)):
        return None
    return keys(state)


def provider(state: Path, *, attended: bool = False) -> Callable[[str, dict[str, Any]], bool]:
    """What a client asks after a refusal that names a missing grant: sign it for the session behind the token."""
    def allow(token: str, hint: dict[str, Any]) -> bool:
        key = held(state, attended=attended)
        if key is None:
            return False
        want = grants.Want(hint["action"], {"kind": "session", "name": hint["name"]}, hint["resource"], hint["device"], LASTS_MS, 1)
        try:
            call(state, "grant_issue", grants.sign_issue(key, hint["board"], want), board=hint["board"], token=token)
        except (OSError, ValueError, RuntimeError) as err:
            warn(f"grant: the person's key could not allow {hint['action']}: {err}")
            return False
        return True
    return allow


def retrying[T](state: Path, token: str, send: Callable[[], T], *, attended: bool = False) -> T:
    """``send()``, once more after the person's key allowed what a refusal named."""
    try:
        return send()
    except Refused as err:
        if not token or not err.grant or not provider(state, attended=attended)(token, err.grant):
            raise
        return send()
