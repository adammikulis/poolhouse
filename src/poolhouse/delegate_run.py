"""The process behind one delegate: the model session on the lead's task and the two tools that reach the lead.

``python -m poolhouse.delegate_run NAME`` is what `poolhouse-delegate start` detaches. It hands the task to `poolhouse.delegate_confined` (OpenCode
in the isolated launcher's confinement), writes the result and tells the lead on the board.
"""

from __future__ import annotations

import shutil
import signal
import sys
import time
from typing import Any

from poolhouse import delegate_confined, delegate_store as store, harness, lead_bridge, node_launch
from poolhouse.board import credentials
from poolhouse.board.client import Client, NodeError
from poolhouse.board.session import Native, Session
from poolhouse.errors import Denied
from poolhouse.log import say
from poolhouse.workspace import limits, nudge_fence
from poolhouse.workspace.harness_session import HarnessSession

__all__ = ["run"]

def converse(rec: store.Delegate, bridge: lead_bridge.Bridge, mine: HarnessSession) -> harness.Answer:
    """The task on the model, in the confinement (`delegate_confined`), read-only or editing as the lead asked."""
    return delegate_confined.converse(rec, bridge, mine, say)


def ended(answer: harness.Answer) -> str:
    """The state a run that gave ``answer`` ended in. A run that publishes is `done` only when all it changed was applied: `unapplied` when it tried
    to change files and nothing was applied, `partial` when some changes were applied and others were not."""
    if answer.is_error:
        return "failed"
    found = answer.published
    if found is None:
        return "done"
    kept_back = bool(found.refused or found.conflicts or found.deferred)
    if not found.applied and (kept_back or answer.tried):
        return "unapplied"
    return "partial" if kept_back else "done"


def changes(state: str, found: Any) -> str:
    """What became of a run's changes, in this process's words and from the publications' counts; empty when there is nothing to warn of."""
    if found is None or state == "done":
        return ""
    head = {"unapplied": "nothing was applied to the project", "partial": "not every change was applied to the project"}.get(state, "")
    counts = f"{len(found.applied)} applied, {len(found.refused)} refused, {len(found.conflicts)} in conflict, {len(found.withheld)} withheld"
    kept = f"; kept patches: {', '.join(found.patches)}" if found.patches else ""
    return "; ".join(each for each in (head, counts, found.deferred) if each) + kept


def _outcome(answer: harness.Answer | None, why: str, began: float) -> dict[str, Any]:
    spent = answer.spent if answer else harness.Usage()
    found = answer.published if answer else None
    published = {name: list(getattr(found, name, ())) for name in ("applied", "refused", "conflicts", "withheld", "patches", "runs_later")}
    return {**published, "text": answer.text if answer else "", "error": why, "turns": spent.turns, "input_tokens": spent.input_tokens,
            "output_tokens": spent.output_tokens, "cache_read_tokens": spent.cache_read_tokens,
            "cache_creation_tokens": spent.cache_creation_tokens, "model_seconds": spent.seconds,
            "seconds": round(time.time() - began, 2), "subtype": spent.subtype}


def _tell(bridge: lead_bridge.Bridge, state: str, row: dict[str, Any]) -> None:
    """The lead's notice that the delegate ended: the start of the result, and the command that prints all of it."""
    said = str(row["text"] or row["error"])
    short = said if len(said) <= nudge_fence.MESSAGE_CHARS // 2 else said[: nudge_fence.MESSAGE_CHARS // 2] + "..."
    parts = [str(row["error"] if row["text"] else ""), str(row["detail"]), short]   # why and what was not applied come first, so a cut never drops them
    body = "\n".join(each for each in parts if each)
    try:
        bridge.post("status", f"{state}: {body}\nall of it: poolhouse-delegate result {bridge.name}", subject=f"delegate {state}")
    except ValueError as why:
        say(f"the lead was not told: {why}")


def _end(*_ignored: object) -> None:
    lead_bridge.STOPPED.set()
    raise SystemExit(143)


def run(name: str) -> int:
    """Run delegate ``name`` to its end; the exit status is 0 when it ended `done` (`ended`), 2 for a name with no record and 1 otherwise."""
    rec = store.load(name)
    if rec is None:
        say(f"no delegate {name}")
        return 2
    signal.signal(signal.SIGTERM, _end)
    node = Client(node_launch.default_state())
    lead = Session(node, rec.board, credentials.load(node.state, rec.board, rec.lead), rec.lead)
    mine = HarnessSession(name, board=rec.board, minted=True, base=limits.root(), parent=lead, state=node.state, native=Native(*rec.native))
    bridge = lead_bridge.Bridge(mine.session(), rec.lead, name, lambda state, detail="": store.set_status(name, state, detail))
    began, answer, why, state = time.time(), None, "", "failed"
    try:
        answer = converse(rec, bridge, mine)
        state, why = ended(answer), answer.why
    except (KeyboardInterrupt, SystemExit):
        state, why = "stopped", "stopped by the lead"
    except (Denied, NodeError, OSError, RuntimeError, ValueError, ImportError) as error:
        why = f"{type(error).__name__}: {error}"
        say(why)
    row = {**_outcome(answer, why, began), "detail": changes(state, answer.published if answer else None)}
    store.save_result(name, {**row, "state": state})
    store.set_status(name, state, why or row["detail"])
    say(f"{state}: {row['turns']} turn(s), {row['seconds']}s")
    _tell(bridge, state, row)
    mine.revoke()
    if rec.workdir != rec.source:
        shutil.rmtree(rec.workdir, ignore_errors=True)
    return 0 if state == "done" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run(*sys.argv[1:]))
