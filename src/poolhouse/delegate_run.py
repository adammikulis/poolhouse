"""The process behind one delegate: the model's sessions on the lead's task (the work and its self-check) and the two tools that reach the lead.

``python -m poolhouse.delegate_run NAME`` is what `poolhouse-delegate start` detaches. It hands the task to `poolhouse.delegate_confined` (OpenCode
in the isolated launcher's confinement), or on a computer that cannot sandbox it to `poolhouse.delegate_unboxed`, writes the result and tells the
lead on the board. While it runs it keeps the run's record (`delegate_recorder`) and tells the lead what goes wrong (`delegate_notices`).
"""

from __future__ import annotations

import dataclasses
import shutil
import signal
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from poolhouse import (
    delegate_confined,
    delegate_grade,
    delegate_notices,
    delegate_recorder,
    delegate_refusals,
    delegate_store as store,
    delegate_tree,
    delegate_unboxed,
    harness,
    lead_bridge,
    node_launch,
    safetext,
)
from poolhouse.board import credentials
from poolhouse.board.client import Client, NodeError
from poolhouse.board.session import Native, Session
from poolhouse.errors import Denied
from poolhouse.log import say
from poolhouse.workspace import limits, nudge_fence
from poolhouse.workspace.harness_session import HarnessSession

__all__ = ["run"]

def converse(rec: store.Delegate, bridge: lead_bridge.Bridge, mine: HarnessSession) -> harness.Answer:
    """The task on the model, in the tier the lead asked for: in the confinement (`delegate_confined`), else on the folder itself."""
    return (delegate_confined if rec.sandboxed else delegate_unboxed).converse(rec, bridge, mine, say)


def ended(answer: harness.Answer) -> str:
    """The state a run that gave ``answer`` ended in: `rejected` when its self-check did not accept the work. Else a run that edits is `done` only
    when all it changed was applied and no write of it was refused: `unapplied` when it tried to change files and nothing was applied (with no
    sandbox, no writing call went through), `partial` when some changes were made and others were not."""
    if answer.is_error:
        return "failed"
    if answer.review is not None and not answer.review.accepted:
        return "rejected"
    found, refused = answer.published, bool(answer.refused_writes)
    if found is None:
        return ("partial" if answer.wrote else "unapplied") if refused else "done"
    kept_back = bool(found.refused or found.conflicts or found.deferred or refused)
    if not found.applied and (kept_back or answer.tried):
        return "unapplied"
    return "partial" if kept_back or (answer.untested and found.applied) else "done"


def changes(state: str, found: Any, refused: Sequence[delegate_refusals.Refused] = (), review: Any = None) -> str:
    """What became of a run's changes, in this process's words, from the publications' counts, the writes it was refused and what its self-check
    came to; empty when there is nothing to warn of."""
    writes = delegate_refusals.said(refused)
    if state == "rejected":
        held = "nothing was applied to the project" if found is not None else review.where or "the answer is not accepted"
        kept = f"; kept patches: {', '.join(found.patches)}" if found is not None and found.patches else ""
        return "; ".join(each for each in (review.said(), held, writes) if each) + kept
    if state == "done" or (found is None and not writes):
        return ""
    where = "applied to the project" if found is not None else "made in the folder"
    nothing = "nothing was applied to the project" if found is not None else "nothing was changed in the folder"
    head = {"unapplied": nothing, "partial": f"not every change was {where}"}.get(state, "")
    if found is None:
        return "; ".join(each for each in (head, writes) if each)
    counts = f"{len(found.applied)} applied, {len(found.refused)} refused, {len(found.conflicts)} in conflict, {len(found.withheld)} withheld"
    kept = f"; kept patches: {', '.join(found.patches)}" if found.patches else ""
    return "; ".join(each for each in (head, counts, writes, found.deferred) if each) + kept


def _outcome(answer: harness.Answer | None, why: str, began: float) -> dict[str, Any]:
    spent = answer.spent if answer else harness.Usage()
    found = answer.published if answer else None
    published = {name: list(getattr(found, name, ())) for name in ("applied", "refused", "conflicts", "withheld", "patches", "runs_later")}
    published["refused_writes"] = [dataclasses.asdict(each) for each in (answer.refused_writes if answer else ())]
    published["review"] = dataclasses.asdict(answer.review) if answer and answer.review else None
    return {**published, "text": answer.text if answer else "", "error": why, "turns": spent.turns, "input_tokens": spent.input_tokens,
            "output_tokens": spent.output_tokens, "cache_read_tokens": spent.cache_read_tokens,
            "cache_creation_tokens": spent.cache_creation_tokens, "model_seconds": spent.seconds,
            "seconds": round(time.time() - began, 2), "subtype": spent.subtype}


def _tell(bridge: lead_bridge.Bridge, state: str, row: dict[str, Any], kind: str = "status") -> None:
    """The lead's notice that the delegate ended: the start of the result, and the command that prints all of it. A card's work is a `handoff`, a
    kind the lead's hooks put in front of it at once."""
    said = str(row["text"] or row["error"])
    short = said if len(said) <= nudge_fence.MESSAGE_CHARS // 2 else said[: nudge_fence.MESSAGE_CHARS // 2] + "..."
    review = row.get("review") or {}
    still = "".join(f"open finding: {each['what'][:100]}\n" for each in ([] if review.get("accepted") else review.get("findings", []))[:3])
    checked = f"self-check accepted in round {review['rounds']} of {review['allowed']}" if review.get("accepted") else still.strip()
    parts = [str(row["error"] if row["text"] else ""), str(row["detail"]), checked, short]   # why and what was not applied first: a cut never drops them
    body = "\n".join(each for each in parts if each)
    waiting = " (review waiting)" if (row.get("tree") or {}).get("commit") else ""
    try:
        bridge.post(kind, f"{state}: {body}\nall of it: poolhouse-delegate result {bridge.name}", subject=f"delegate {state}{waiting}")
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
    record = delegate_recorder.of(name)
    record.add("run", lead=rec.lead, model=Path(rec.model).name, tier=delegate_confined.tier(rec), edit=rec.edit, sandboxed=rec.sandboxed,
               task=rec.task)
    teller = delegate_notices.Teller(name, lambda about, text: bridge.post("status", text, subject=f"delegate notice: {about}"))
    teller.start()
    try:
        delegate_grade.started(rec)
        answer = converse(rec, bridge, mine)
        state, why = ended(answer), answer.why
    except (KeyboardInterrupt, SystemExit) as stop:
        state, why, answer = "stopped", delegate_confined.STOPPED_WHY, getattr(stop, "answer", None)
    except (Denied, NodeError, OSError, RuntimeError, ValueError, ImportError) as error:
        why = f"{type(error).__name__}: {error}"
        say(why)
    finally:
        teller.close()
    found, refused = (answer.published, answer.refused_writes) if answer else (None, ())
    detail = changes(state, found, refused, answer.review if answer else None)
    if answer and answer.untested:   # first, so a cut notice never drops it
        detail = "; ".join(each for each in (f"untested: the sandbox could not start the project's tests ({answer.untested})", detail) if each)
    row = safetext.utf8({**_outcome(answer, why, began), "detail": detail})   # the model's text reaches the board
    row["phases"] = store.graded(name)["run"].get("phases", [])
    if rec.branch:   # commit what stands to its branch, keep a patch of anything else, and remove the worktree
        state, said, row["tree"] = delegate_tree.settled(rec, state, found.applied if found else ())
        row["detail"] = "; ".join(each for each in (said, row["detail"]) if each)
    store.save_result(name, {**row, "state": state})
    delegate_grade.ended(name, state, row)
    store.set_status(name, state, why or row["detail"])
    record.add("end", state=state, seconds=row["seconds"], turns=row["turns"],
               detail="\n".join(each for each in (why, row["detail"]) if each))
    say(f"{state}: {row['turns']} turn(s), {row['seconds']}s")
    _tell(bridge, state, row, "handoff" if rec.card else "status")
    mine.revoke()
    if rec.workdir != rec.source and not rec.branch:
        shutil.rmtree(rec.workdir, ignore_errors=True)
    return 0 if state == "done" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run(*sys.argv[1:]))
