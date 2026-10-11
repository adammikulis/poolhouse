"""The person's grant key: kept beside the node, and used only for what a person confirms at their terminal (docs/node.md,
"Standing grants").

Every signature it makes is read back from the bytes being signed and needs a typed `yes` on the terminal device itself
(`person.confirm`), whatever stdin and stdout are. A command a person is running asks for what it needs; when a refusal says a
grant is missing, it offers to sign one for exactly that, good for one use and two minutes. A process an agent started is
refused before the question is put.
"""

from __future__ import annotations

import getpass
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from poolhouse import person
from poolhouse.board import grants
from poolhouse.fleet.onboard.signing import ConfirmedKeys, KeyStoreError
from poolhouse.log import warn
from poolhouse.node_health import Refused, call

__all__ = ["KEY_DIR", "PersonKey", "held", "kept", "keys", "provider", "retrying"]

KEY_DIR = "person"
LASTS_MS = 120_000

Ask = Callable[[str], bool]


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


class PersonKey:
    """The person's signing key behind the question every signature is put to."""

    def __init__(self, store: ConfirmedKeys, ask: Ask | None = None) -> None:
        self.store, self.ask = store, ask or person.confirm
        self._approved: list[str] = []

    @property
    def public(self) -> bytes:
        return self.store.public

    @property
    def meta_path(self) -> Path:
        return self.store.meta_path

    def approve(self, lines: Sequence[str], header: str) -> None:
        """Put ``lines`` to the person once: the signatures that read back as exactly these need no second question."""
        shown = "\n".join(f"  {number}. {line}" for number, line in enumerate(lines, 1))
        if not self.ask(f"{header}\n{shown}\nType yes to sign {'it' if len(lines) == 1 else 'all of them'}: "):
            raise KeyStoreError("nothing was signed: the person did not type yes")
        self._approved = list(lines)

    def sign_bytes(self, data: bytes) -> bytes:
        """The signature over ``data`` once the person has read what it does and typed yes on the terminal."""
        line = grants.describe(data)
        if line in self._approved:
            self._approved.remove(line)
            return self.store.sign_bytes(data, confirm=lambda _: True)
        return self.store.sign_bytes(data, confirm=lambda _: self.ask(f"Your person key is asked to sign:\n  {line}\nType yes to sign it: "))


def keys(state: Path, ask: Ask | None = None) -> PersonKey:
    """The person's key of the node whose state directory is ``state``, made on first use of its public half."""
    return PersonKey(ConfirmedKeys(Path(state) / KEY_DIR, passphrase=_remembered(tty_passphrase)), ask)


def kept(state: Path) -> bool:
    """Whether a person's key is kept for the node whose state directory is ``state``."""
    return (Path(state) / KEY_DIR / "signing.json").exists()


def held(state: Path, *, attended: bool = False, ask: Ask | None = None) -> PersonKey | None:
    """The person's key when one is kept here and this process is a person's: no agent marker, and a terminal on stdin and
    stdout, or a screen the person is sitting at (``attended``); else None. Using it still asks on the terminal device."""
    if person.marked() or not kept(state):
        return None
    if not attended and not (person.is_terminal(sys.stdin) and person.is_terminal(sys.stdout)):
        return None
    return keys(state, ask)


def provider(state: Path, *, attended: bool = False, ask: Ask | None = None) -> Callable[[str, dict[str, Any]], bool]:
    """What a client asks after a refusal that names a missing grant: sign it for the session behind the token, when the person says yes."""
    def allow(token: str, hint: dict[str, Any]) -> bool:
        key = held(state, attended=attended, ask=ask)
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
