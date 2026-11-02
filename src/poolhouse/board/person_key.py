"""The person's grant key: kept beside the node, and used only for what a person confirms at their terminal (docs/node.md,
"Standing grants", "Key custody").

Every signature it makes is read back from the bytes being signed and put to the person as a typed `yes` on the terminal device
(`person.confirm`, `/dev/tty`), whatever stdin and stdout are. A command a person is running asks for what it needs; when a refusal
says a grant is missing, it offers to sign one for exactly that, good for one use and two minutes. A process an agent marked is
refused before the question is put. The typed `yes` stops accidents and an agent that does not open a terminal of its own; it is
not a barrier against a process of the same operating-system user that allocates a terminal of its own, imports this code, or
reads the key file and its passphrase or the keychain item. A per-use user-presence prompt (Touch ID, a hardware key) or a signing
helper under another operating-system identity would be, and neither is built.
"""

from __future__ import annotations

import getpass
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from poolhouse import errors, person
from poolhouse.board import grants
from poolhouse.board.client import Client
from poolhouse.board.session import Session
from poolhouse.fleet.onboard.signing import ConfirmedKeys, KeyStoreError
from poolhouse.log import warn
from poolhouse.node_health import Refused, call

__all__ = ["KEY_DIR", "held", "kept", "keys", "provider", "retrying"]

KEY_DIR = "person"
LASTS_MS = 120_000


def tty_passphrase(prompt: str) -> str:
    """The passphrase of the key file, typed on the terminal device; the environment is never read for it."""
    with person.controlling_terminal() as found:
        if found is None:
            raise KeyStoreError("the person's key is unlocked at a terminal, and this process has none")
    return getpass.getpass(prompt)


def _remembered(ask: Callable[[str], str]) -> Callable[[str], str]:
    """``ask``, answered once for the life of this process."""
    typed: list[str] = []

    def passphrase(prompt: str) -> str:
        if not typed:
            typed.append(ask(prompt))
        return typed[0]
    return passphrase


def keys(state: Path) -> ConfirmedKeys:
    """The person's key of the node whose state directory is ``state``, made on first use of its public half."""
    return ConfirmedKeys(Path(state) / KEY_DIR, describe=grants.describe, passphrase=_remembered(tty_passphrase))


def kept(state: Path) -> bool:
    """Whether a person's key is kept for the node whose state directory is ``state``."""
    return (Path(state) / KEY_DIR / "signing.json").exists()


def held(state: Path, *, attended: bool = False) -> ConfirmedKeys | None:
    """The person's key when one is kept here and this process is a person's: no agent marker, and a terminal on stdin and
    stdout, or a screen the person is sitting at (``attended``); else None. Using it still asks on the terminal device."""
    if person.marked() or not kept(state):
        return None
    if not attended and not (person.is_terminal(sys.stdin) and person.is_terminal(sys.stdout)):
        return None
    return keys(state)


def provider(state: Path, *, attended: bool = False, acting: Callable[[str], tuple[str, str]] | None = None) -> Callable[[str, dict[str, Any]], str]:
    """What a client asks after a refusal that names a missing grant: sign it for one use when the person says yes. The grant is for
    the session behind the token, or, with ``acting`` (a board to the name and token of the person's own session of this terminal on it),
    for that session, since the token of another terminal's session spends no grant here. The token to send the request again with, or an
    empty string."""
    def allow(token: str, hint: dict[str, Any]) -> str:
        key = held(state, attended=attended)
        if key is None:
            return ""
        try:
            name, carrier = acting(hint["board"]) if acting is not None else (hint["name"], token)
            if grants.listing(Session(Client(state), hint["board"], carrier, name)).pool_wide(hint["action"]):
                warn(f"grant: {grants.POOL_GRANTS}")
                return ""
            want = grants.Want(hint["action"], {"kind": "session", "name": name}, hint["resource"], hint["device"], LASTS_MS, 1)
            call(state, "grant_issue", grants.sign_issue(key, hint["board"], want), board=hint["board"], token=carrier)
        except (OSError, ValueError, RuntimeError, errors.Error) as err:
            warn(f"grant: the person's key could not allow {hint['action']}: {err}")
            return ""
        return carrier
    return allow


def retrying[T](state: Path, token: str, send: Callable[[], T], *, attended: bool = False) -> T:
    """``send()``, once more after the person's key allowed what a refusal named."""
    try:
        return send()
    except Refused as err:
        if not token or not err.grant or not provider(state, attended=attended)(token, err.grant):
            raise
        return send()
