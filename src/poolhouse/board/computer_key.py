"""The person's admin key for one pool on this computer, and the ticket it signs under (docs/design-phone-free-pools.md, 2.3).

The key is an Ed25519 key the node makes for one pool's card and gives to the person's own session once, when the person says yes at the computer's
own prompt (so a key an agent holds is never the one a yes enrols); it is sealed under the account's keystore (under a passphrase typed at the
person's terminal where there is none) in ``<state>/person/pools/<handle>/``, a place the harness's deny lists keep from a process an agent started (the
account's own files: see `credentials.person_secret` for what that does not cover). The node keeps no
copy and nothing here is a switch that signs without the person: a key signs
only the bytes a **ticket** names. The ticket is the node's answer to the person's yes at the computer's own prompt, for one change the node
drafted and wrote the prompt's sentence for. ``sign_bytes`` shows the node the SHA-256 of what it is about to sign; the node answers only for a
payload the ticket names, once, within two minutes, to the session the ticket was given to. A process that holds this key's file can still
unseal it without any prompt: that is what the pool's `computer` level says it is (the vouching computer's word), and it is why every
governance act is held seven days.
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
    """The key was asked to sign without the person's yes for those bytes."""


def _no_passphrase(_prompt: str) -> str:
    raise KeyStoreError("a pool's key is kept in the keystore and never under a passphrase typed to an agent")


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
        """The signature over ``data``, once the node confirms the person's yes was for exactly these bytes."""
        if not self.ticket:
            raise TicketRequired("The computer's key signs only what the person said yes to at the computer's own prompt, and no yes was given.")
        try:
            self._client.call("ticket_spend", "", self._token, ticket=self.ticket, payload=hashlib.sha256(data).hexdigest())
        except Denied as err:
            raise TicketRequired(str(err)) from None
        return self._keys.sign_bytes(data)

    def approve(self, card: str, *, voice: presence.Voice = presence.SILENT) -> None:
        """Raise the computer's own prompt for the card the node drafted and wait for the person's yes; the yes is this key's ticket. Raises
        `Denied` with one plain sentence on a no or no answer."""
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
        return SigningKeys(directory, passphrase=_no_passphrase, ks=self._ks, say=lambda _text: None)

    def _store(self) -> keystore.Keystore:
        return self._ks or keystore.default()

    def sealed(self) -> bool:
        """Whether a key made here is kept under the account's keystore. Where it is not, the pool says it is approved by a typed yes and the key
        is kept in a file under a passphrase typed at a terminal."""
        return self._store().available()

    def adopt(self, seed: bytes) -> ComputerKey:
        """Keep the key whose secret bytes the node gave this person's session once, when the person said yes for its card: sealed under the
        keystore where there is one, else under a passphrase typed at the person's terminal."""
        directory = self.directory / secrets.token_hex(16)
        directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        keys = SigningKeys(directory, passphrase=_no_passphrase if self.sealed() else person_key.tty_passphrase, ks=self._ks, say=lambda _text: None)
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
