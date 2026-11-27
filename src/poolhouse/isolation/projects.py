"""The projects of this computer that a launch registers and that a session removes (docs/design-isolated-launcher.md, 8.3).

A coding session works on a confined copy of one git project and publishes its plain changes: naming a project is the base reach of a launch, so
the launching session's own project, or the one the person asked it to work on, is registered by the launch itself with the node's place verb.
What no session is given (`places`) is refused at once with its reason, and registering widens nothing: every widening still comes from the task
or the person's policy (8.3). Nothing here asks anyone or waits.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from pathlib import Path

from poolhouse import activity
from poolhouse.board import client as board_client, place
from poolhouse.isolation import ancestry, places
from poolhouse.isolation.refusals import IsolationRefused, sentence

__all__ = ["REGISTERED", "REMOVED", "register", "remove"]

REGISTERED = "isolation.project_registered"
REMOVED = "isolation.project_removed"
SHOWN = 120
"""How much of the node's own reason a sentence repeats."""
Emit = Callable[[str, dict[str, object]], None]


def _digest(text: str) -> str:
    """A short digest that names a folder in the record and the signal without copying its path."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _reach(real: str) -> tuple[str, Path]:
    """The board id and the main working tree of the git project the folder ``real`` is in; a folder none of the refusals of `places` covers and
    that is no git project is refused as such. Both the folder and the tree it belongs to are checked, because the node registers the tree."""
    found = place.board_id(Path(real))
    for each in (real, *(() if found is None else (os.path.realpath(found[1]),))):
        if key := places.project_refusal(each):
            raise IsolationRefused(sentence(key))
    if found is None:
        raise IsolationRefused(sentence("not_a_repository"))
    return found


def register(path: str, *, issuer: str, source: str, emit: Emit) -> str:
    """Register the git project at ``path`` with the node and return its board. The folder and the repository's main working tree must be ones
    a coding session may work in (`places.project_refusal`); the registration is logged and sent to the sentinel as one `isolation.project_registered`
    signal. A refusal, the node's included, is `IsolationRefused` with a plain reason."""
    real = os.path.realpath(path)
    _, tree = _reach(real)
    try:
        board = place.add(board_client.Client(ancestry.node_state()), Path(real))
    except (OSError, ValueError, KeyError, board_client.Denied, board_client.Invalid) as why:
        raise IsolationRefused(sentence("project_not_added", why=str(why)[:SHOWN])) from why
    facts = {"project": board, "place": _digest(str(tree)), "issuer": issuer, "source": source}
    activity.record(REGISTERED, subject=board, outcome="registered", meta=facts)
    emit(REGISTERED, {"severity": "notice", "policy": "isolation", **facts})
    return board


def remove(target: str, *, issuer: str) -> str:
    """Forget the registered project ``target`` names (its id, or a folder of it, one that is gone included) and return its board. The caller has
    been checked by the launch gate (`gate.remove_project`) before this function. A target that names no project is `IsolationRefused`."""
    client = board_client.Client(ancestry.node_state())
    try:
        board = place.remove(client, target)
    except (OSError, ValueError, KeyError, board_client.Denied, board_client.Invalid) as why:
        raise IsolationRefused(sentence("project_not_removed", why=str(why)[:SHOWN])) from why
    activity.record(REMOVED, subject=board, outcome="removed", meta={"project": board, "issuer": issuer})
    return board
