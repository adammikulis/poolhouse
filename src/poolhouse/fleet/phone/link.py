"""Linking a phone (docs/phone-api.md, section 2): a one-time code shown on this computer, proved by the phone over the connection it
pinned. The code reaches the phone through the person's eyes (a QR code or typing it), so whoever sits between the two cannot make the
proof, and the computer's proof back shows it knows the code too."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import quote

from poolhouse.phone_mailbox import Phone, Refused, Store, check_key, verify

__all__ = ["ALPHABET", "CODE_LENGTH", "LIFE_S", "Linking", "Offer", "TooMany", "code_key", "link_uri", "message", "normalised"]

ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
"""Digits and capitals with none that reads as another: no 0, 1, I, L, O or U."""
CODE_LENGTH = 10
"""About 49 bits: with the slow key below and `LIFE_S`, a recorded proof cannot be searched in time."""
LIFE_S = 600
ITERATIONS = 200_000
SALT = "poolhouse/phone-link/v1|"
LINK = "poolhouse/phone-link/v1"
LINKED = "poolhouse/phone-linked/v1"
MOST_NAME = 48
MOST_FAILURES = 10
"""Wrong proofs one address may send in `LIFE_S`; the code itself stays good for the person."""


class TooMany(Refused):
    """Too many wrong proofs from one address."""


def normalised(code: str) -> str:
    return "".join(c for c in code.upper() if c.isalnum())


def code_key(code: str, pin: str) -> bytes:
    """The key both ends derive from the code and the certificate the phone sees."""
    return hashlib.pbkdf2_hmac("sha256", normalised(code).encode(), (SALT + pin).encode(), ITERATIONS, 32)


def message(label: str, *parts: str) -> bytes:
    return "\n".join((label, *parts)).encode()


def _mac(key: bytes, said: bytes) -> str:
    return base64.b64encode(hmac.new(key, said, hashlib.sha256).digest()).decode()


def link_uri(pin: str, code: str, endpoints: Sequence[str], name: str, expires: float) -> str:
    """What the QR code holds."""
    parts = [f"v=1&pin={pin}&code={code}", *(f"at={quote(each, safe='')}" for each in endpoints),
             f"name={quote(name[:MOST_NAME], safe='')}", f"expires={int(expires)}"]
    return "poolhouse://link?" + "&".join(parts)


@dataclass(frozen=True, slots=True)
class Offer:
    code: str
    expires: float
    key: bytes

    @property
    def shown(self) -> str:
        return f"{self.code[:5]}-{self.code[5:]}"


class Linking:
    """The one open offer of this computer, and what was tried against it. Kept in memory only: no file holds a code."""

    def __init__(self, store: Store, pin: str, clock: Callable[[], float] = time.time,
                 linked: Callable[[Phone], None] = lambda _phone: None) -> None:
        self.store, self.pin, self.clock, self.linked = store, pin, clock, linked
        self._offer: Offer | None = None
        self._failures: dict[str, list[float]] = {}

    def offer(self) -> Offer:
        """A new code in place of any open one."""
        code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
        self._offer = Offer(code, self.clock() + LIFE_S, code_key(code, self.pin))
        return self._offer

    def open(self) -> Offer | None:
        if self._offer is not None and self._offer.expires <= self.clock():
            self._offer = None
        return self._offer

    def cancel(self) -> None:
        self._offer = None

    def link(self, source: str, name: object, key: object, proof: object, signature: object) -> tuple[Phone, str]:
        """Add the phone that proves it holds the open code and its own key; the code is spent. Returns the phone and this computer's
        proof for it."""
        now = self.clock()
        self._failures = {a: kept for a, times in self._failures.items() if (kept := [t for t in times if now - t < LIFE_S])}
        if len(self._failures.get(source, [])) >= MOST_FAILURES:
            raise TooMany("Too many wrong codes came from this address; wait ten minutes.")
        if not all(isinstance(each, str) for each in (name, key, proof, signature)):
            raise Refused("A link names a name, a key, a proof and a signature.")
        name, key, proof, signature = str(name).strip(), str(key), str(proof), str(signature)
        if not 1 <= len(name) <= MOST_NAME or not name.isprintable():
            raise Refused(f"A phone's name is 1 to {MOST_NAME} printable characters.")
        check_key(key)
        offer = self.open()
        said = message(LINK, self.pin, key, name)
        if offer is None or not hmac.compare_digest(proof.encode(), _mac(offer.key, said).encode()) or not verify(key, said, signature):
            self._failures.setdefault(source, []).append(now)
            raise Refused("That code is not open on this computer. Show a new one and try again.")
        self._offer = None
        phone = self.store.link(name, key)
        self.linked(phone)
        return phone, _mac(offer.key, message(LINKED, self.pin, key, phone.id))
