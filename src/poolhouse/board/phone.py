"""The phone side of the node as a client reaches it: the one-time code a person's command leaves, where the node listens for
phones, and the phones it holds (docs/phone-approval.md).

The node reads the hash of the code from a file in its state directory and from no request, so only a process that can write
that directory can make one, as for the first person key (docs/node.md, "Standing grants").
"""

from __future__ import annotations

import hashlib
import secrets
import time
from pathlib import Path
from typing import Any

from poolhouse.board.session import Session
from poolhouse.files import write_json
from poolhouse.platform import private_file

__all__ = ["LISTEN_FILE", "OFFER_FILE", "PORT", "describe", "expect_phone", "find", "listen_on", "phones"]

OFFER_FILE = "phone_offer.json"
LISTEN_FILE = "phone.json"
PORT = 7449
"""The port a phone talks to when no other is chosen."""


def expect_phone(state: Path, *, now_ms: int | None = None) -> str:
    """Leave in the node's state directory the hash of a fresh one-time code and return its secret.

    The node adds one phone with that secret, within ten minutes, once.
    """
    secret = secrets.token_urlsafe(32)
    path = Path(state) / OFFER_FILE
    write_json(path, {"sha256": hashlib.sha256(secret.encode()).hexdigest(), "at_ms": int(time.time() * 1000) if now_ms is None else now_ms})
    private_file(path)
    return secret


def listen_on(state: Path, address: str) -> None:
    """Make the node listen for phones on ``address`` from its next start: `0.0.0.0:7449` is every network this device is on."""
    path = Path(state) / LISTEN_FILE
    write_json(path, {"listen": address})
    private_file(path)


def phones(session: Session) -> list[dict[str, Any]]:
    """The phones the node holds, removed ones included, as a client may read them (never a token)."""
    return [dict(p) for p in session.call("phones")["phones"]]


def find(held: list[dict[str, Any]], name: str) -> dict[str, Any]:
    """The one active phone called ``name``, or its fingerprint or id; `ValueError` when none or several match."""
    found = [p for p in held if p["state"] != "removed" and name in (p["name"], p["id"], p["fingerprint"])]
    if len(found) != 1:
        raise ValueError(f"{'no' if not found else 'more than one'} phone is called {name}: `poolhouse-workspace phone list` shows them")
    return found[0]


def describe(config: dict[str, Any]) -> str:
    """The node's listener and the phones it holds as one line for a person."""
    where = ", ".join(config.get("endpoints") or []) or "no address on this network"
    return f"listening on port {config['port']} ({where}); {config['phones']} phone(s) held" if config.get("listening") else "not listening for phones"
