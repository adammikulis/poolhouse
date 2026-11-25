"""How the first person key or a change to the pool is made by the node (docs/node.md, "Standing grants").

The node makes it, never the client. A node started as the launcher starts it (`--presence auto`, the default) asks nobody: the one-time secret
or ticket is made as it is asked for, kept in the node's memory and handed once to the session that asked, so neither a file in the state
directory nor a request can make one; who may ask is the node's rule (the device's own session for the first key, a live main session
also for a pool's card), and a secret is bound to the key it was made for. Nothing here waits for anybody: a node somebody started by hand with
`--presence os` or `terminal` answers that it is asking its user, and `ask` refuses at once with that reason, since an answer would need a person.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from poolhouse.board.client import Client, Denied

__all__ = ["Card", "ask", "ask_card"]

ASKS_A_USER = ("This node was started to ask its user first (`--presence os` or `terminal`), and nothing here waits for a person: "
               "start it as the launcher does, which asks nobody.")


class Card(NamedTuple):
    """A change the node drafted (``id``). Asking for it enrols the key the node made for it, when it made one."""

    id: str


def ask(client: Client, token: str, public: str) -> dict[str, Any]:
    """Ask the node for the one-time secret that lets the session ``token`` add the first person key: the node's reply, with the `secret`.
    The device's own session asks; the node refuses any other.

    ``public`` is the public key (hex) of the first key: the secret is for that key and no other. Raises `Denied` when the node refuses the asker,
    a key is enrolled already, or the node asks its user instead of making the secret.
    """
    return _asked(client, token, purpose="key", public=public, card="")


def ask_card(client: Client, token: str, purpose: str, card: Card) -> dict[str, Any]:
    """Ask the node to answer the change ``card`` names (``purpose`` `pool_key` for a card that made a key for this computer, `card` for any other):
    the reply's `secret` is the ticket a signer shows the node for each payload it signs (a `pool_key` reply also carries the new key's `seed`,
    once). Raises `Denied` as `ask` does."""
    return _asked(client, token, purpose=purpose, card=card.id)


def _asked(client: Client, token: str, **params: str) -> dict[str, Any]:
    said = client.call("presence_ask", "", token, **params)
    if said["state"] != "confirmed" or not isinstance(said.get("secret"), str):
        raise Denied("denied", f"The node made no secret (it says {said['state']}). {ASKS_A_USER}")
    return dict(said)
