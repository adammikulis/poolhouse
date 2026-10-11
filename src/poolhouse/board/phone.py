"""The phone side of the node as a client reaches it: where the node listens for phones and the phones it holds (docs/phone-approval.md).

The one-time code of the first phone is made by the node when the person says yes to its own question (`board.presence`) and is kept in
its memory; no file in the state directory makes one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from poolhouse.board.session import Session
from poolhouse.files import write_json
from poolhouse.platform import private_file

__all__ = ["LISTEN_FILE", "PORT", "describe", "find", "listen_on", "phones"]

LISTEN_FILE = "phone.json"
PORT = 7449
"""The port a phone talks to when no other is chosen."""


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
