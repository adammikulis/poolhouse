"""The person's grant key: kept beside the node, created on first use, and used to sign the grants a refusal names.

Nothing asks anyone. A command that is refused for a missing grant gets one signed for exactly that, good for one use and two
minutes, when policy allows it: a process with no agent marker may have any grant, and an agent may have every grant except
those that end another device's place in the pool, change who governs it, or release what a scan failed (`AGENT_REFUSED`).
Those are refused at once with the reason, and every use and refusal is audited with who asked (`poolhouse.caller`).
The key is held in the OS keystore, else in a file encrypted under ``POOLHOUSE_SIGNING_PASSPHRASE`` (never asked for at a terminal).
It is not a barrier against code of the same operating-system user that reads the key file or the keychain item.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from poolhouse import caller, errors
from poolhouse.board import grants
from poolhouse.board.client import Client
from poolhouse.board.session import Session
from poolhouse.fleet.onboard.signing import UNLOCK_ENV, KeyStoreError, SigningKeys
from poolhouse.log import warn
from poolhouse.node_health import Refused, call

__all__ = ["AGENT_REFUSED", "KEY_DIR", "agent_refusal", "kept", "keys", "no_prompt", "provider", "retrying"]

KEY_DIR = "person"
LASTS_MS = 120_000
AGENT_REFUSED = {
    "member_remove": "it ends another device's place in the pool",
    "admin_manage": "it changes who governs the pool",
    "land_trust": "it widens what the pool trusts to land",
    "quarantine_release": "what failed a scan is deleted and fetched again, never released",
}
"""The grants an agent is refused at once, with why: the automatic floor under every grant that policy issues."""


def agent_refusal(action: str) -> str:
    """Why an agent is refused a grant for ``action``, or an empty string when policy allows it."""
    return AGENT_REFUSED.get(action, "")


def no_prompt(prompt: str) -> str:
    """The passphrase of the key file, from the environment; never asked for at a terminal."""
    import os
    told = os.environ.get(UNLOCK_ENV, "")
    if not told:
        raise KeyStoreError(f"this machine has no OS keystore, so the key is kept in a file under a passphrase: set {UNLOCK_ENV} ({prompt.strip(': ')})")
    return told


def keys(state: Path) -> SigningKeys:
    """The person's key of the node whose state directory is ``state``, made on first use of its public half."""
    return SigningKeys(Path(state) / KEY_DIR, passphrase=no_prompt)


def kept(state: Path) -> bool:
    """Whether a person's key is kept for the node whose state directory is ``state``."""
    return (Path(state) / KEY_DIR / "signing.json").exists()


def provider(state: Path, *, acting: Callable[[str], tuple[str, str]] | None = None) -> Callable[[str, dict[str, Any]], str]:
    """What a client asks after a refusal that names a missing grant: sign it for one use when policy allows. The grant is for
    the session behind the token, or, with ``acting`` (a board to the name and token of the person's own session of this terminal on it),
    for that session, since the token of another terminal's session spends no grant here. The token to send the request again with, or an
    empty string."""
    def allow(token: str, hint: dict[str, Any]) -> str:
        action = str(hint["action"])
        if caller.marked() and (why := agent_refusal(action)):
            caller.record("grant.refused", action=action, why=why)
            warn(f"grant: {action} is refused to an agent: {why}")
            return ""
        try:
            key = keys(state)
            name, carrier = acting(hint["board"]) if acting is not None else (hint["name"], token)
            if grants.listing(Session(Client(state), hint["board"], carrier, name)).pool_wide(action):
                warn(f"grant: {grants.POOL_GRANTS}")
                return ""
            want = grants.Want(action, {"kind": "session", "name": name}, hint["resource"], hint["device"], LASTS_MS, 1)
            call(state, "grant_issue", grants.sign_issue(key, hint["board"], want), board=hint["board"], token=carrier)
        except (OSError, ValueError, RuntimeError, errors.Error) as err:
            warn(f"grant: the person's key could not allow {action}: {err}")
            return ""
        caller.record("grant.issued", action=action, to=name, resource=str(hint["resource"]))
        return carrier
    return allow


def retrying[T](state: Path, token: str, send: Callable[[], T]) -> T:
    """``send()``, once more after policy allowed what a refusal named."""
    try:
        return send()
    except Refused as err:
        if not token or not err.grant or not provider(state)(token, err.grant):
            raise
        return send()
