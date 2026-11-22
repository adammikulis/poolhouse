"""This computer's admin key for one pool, and the ticket it signs under (docs/design-phone-free-pools.md, 2.3).

The key is an Ed25519 key the node makes for one pool's card and gives once to the session that asked (the node asks nobody: `--presence auto`
answers as it is asked, so the key is enrolled and handed over at once); it is sealed under the account's keystore, or under the passphrase of
``POOLHOUSE_SIGNING_PASSPHRASE`` where there is none, in ``<state>/person/pools/<handle>/``, a place the harness's deny lists keep from a process an
agent started (the account's own files: see `credentials.person_secret` for what that does not cover). The node keeps no copy. A key signs only
the bytes a **ticket** names. The ticket is the node's answer to the ask for one change the node drafted. ``sign_bytes`` shows the node the
SHA-256 of what it is about to sign; the node answers only for a payload the ticket names, once, within two minutes, to the session the ticket was
given to. A process that holds this key's file can still unseal it without any ticket: that is what the pool's `computer` level says it is (the
vouching computer's word), and it is why every governance act is held seven days.
"""

from __future__ import annotations

import contextlib
import hashlib
import secrets
import sys
from pathlib import Path

from poolhouse import errors, keystore
from poolhouse.board import person_key, presence
from poolhouse.board.client import Client, Denied
from poolhouse.fleet.onboard.signing import KeyStoreError, SigningKeys

__all__ = ["ComputerKey", "PoolKeys", "TicketRequired"]

POOLS_DIR = Path("person") / "pools"


class TicketRequired(errors.Error):
    """The key was asked to sign without a ticket for those bytes."""


class ComputerKey:
    """The key of one pool: its public half is free, its signature needs a ticket."""

    def __init__(self, keys: SigningKeys, client: Client, token: str) -> None:
        self._keys, self._client, self._token = keys, client, token
        self.ticket = ""

    @property
    def public(self) -> bytes:
        return self._keys.public

    def use(self, token: str) -> ComputerKey:
        """The key, asking and signing as the session ``token`` (the person's own): a ticket is good for that session alone."""
        self._token = token
        return self

    @property
    def directory(self) -> Path:
        return self._keys.directory

    def sign_bytes(self, data: bytes) -> bytes:
        """The signature over ``data``, once the node confirms the ticket was made for exactly these bytes."""
        if not self.ticket:
            raise TicketRequired("The computer's key signs only the change the node drafted and made a ticket for, and it has none.")
        try:
            self._client.call("ticket_spend", "", self._token, ticket=self.ticket, payload=hashlib.sha256(data).hexdigest())
        except Denied as err:
            raise TicketRequired(str(err)) from None
        return self._keys.sign_bytes(data)

    def approve(self, card: str, *, voice: presence.Voice = presence.SILENT) -> None:
        """Ask the node for the card it drafted and keep the answer as this key's ticket. Raises `Denied` with one plain sentence when the node
        does not make one."""
        self.ticket = str(presence.ask_card(self._client, self._token, "card", presence.Card(card), voice)["secret"])

    def forget(self) -> None:
        """Drop the sealed key of a pool that was not made, so an abandoned founding leaves nothing behind."""
        for name in ("signing.json", "signing.key.wrapped", "signing.key.enc"):
            (self._keys.directory / name).unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            self._keys.directory.rmdir()


class PoolKeys:
    """The keys of the pools this account's computer governs, found by the public key a charter lists."""

    def __init__(self, state: Path, client: Client, token: str, *, ks: keystore.Keystore | None = None) -> None:
        self.directory = Path(state) / POOLS_DIR
        self.client, self.token, self._ks = client, token, ks

    def _keys(self, directory: Path) -> SigningKeys:
        """The key kept in ``directory``: under the keystore, or in a file whose passphrase is the environment's, never a prompt."""
        return SigningKeys(directory, passphrase=person_key.no_prompt, ks=self._ks, say=lambda _text: None)

    def _store(self) -> keystore.Keystore:
        return self._ks or keystore.default()

    def sealed(self) -> bool:
        """Whether a key made here is kept under the account's keystore. Where it is not, the key is kept in a file under the passphrase of
        ``POOLHOUSE_SIGNING_PASSPHRASE``."""
        return self._store().available()

    def check(self) -> None:
        """Raise `KeyStoreError` when a key made now could not be kept: there is no keystore and the environment holds no passphrase. It is asked
        before the node makes a key, since a key the node enrolled and nobody kept is lost."""
        if not self.sealed():
            person_key.no_prompt("passphrase for the pool's key")

    def adopt(self, seed: bytes) -> ComputerKey:
        """Keep the key whose secret bytes the node gave this session once, for its card: sealed under the keystore where there is one, else
        under the passphrase of the environment."""
        directory = self.directory / secrets.token_hex(16)
        directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        keys = self._keys(directory)
        keys.adopt(seed)
        return ComputerKey(keys, self.client, self.token)

    def find(self, listed: set[str]) -> ComputerKey | None:
        """The key of this computer whose fingerprint (SHA-256 of its public half) a charter lists, if any."""
        if not self.directory.is_dir():
            return None
        for directory in sorted(self.directory.iterdir()):
            if not (directory / "signing.json").is_file():
                continue
            key = ComputerKey(self._keys(directory), self.client, self.token)
            try:
                if hashlib.sha256(key.public).hexdigest() in listed:
                    return key
            except (KeyStoreError, OSError, ValueError):
                continue
        return None


def on_a_computer() -> bool:
    """Whether this process runs where the node can raise a prompt of the operating system: macOS, Linux or Windows."""
    return sys.platform in ("darwin", "linux", "win32")
