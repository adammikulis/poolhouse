"""Messages as a reader gets them: fenced as data, and held back when their text matches an injection pattern.

The node keeps what was written. This module decides, for the session reading, what it is shown:
text with a hard injection marker, or a soft one from a sender without good standing, is replaced
by a placeholder until a person releases it, as the node's quarantine records.
"""

from __future__ import annotations

from typing import Any

from poolhouse.board.session import Agent, Entry, Session
from poolhouse.workspace import limits
from poolhouse.workspace.boardapi import Held
from poolhouse.workspace.identity import AGENT, Identity
from poolhouse.workspace.modelid import describe
from poolhouse.workspace.screen import fence, marker_tiers
from poolhouse.workspace.standing import record_injection, sender_standing

__all__ = ["PLACEHOLDER", "advanced", "hold", "judge", "names", "present", "shown"]

PLACEHOLDER = "[held in quarantine as {qid}: {why}. A person releases it; until then it is not shown]"


def names(s: Session) -> dict[str, Agent]:
    """Every identity of the board by name, retired ones too."""
    return {a.name: a for a in s.agents(True)}


def _text(e: Entry) -> str:
    subject = str(e.fields.get("subject", ""))
    return f"subject: {subject}\n{e.text}" if subject else e.text


def judge(e: Entry) -> tuple[list[str], bool, bool]:
    """``(markers, hard, held)``: the injection markers in ``e``, whether any is hard, and whether they hold it:
    a hard marker always does, a soft one only when its sender does not have good standing."""
    hard, soft = marker_tiers(f"{e.fields.get('subject', '')}\n{e.text}")
    held = bool(hard) or (bool(soft) and sender_standing(Identity(e.sender, AGENT)) != "good")
    return sorted([*hard, *soft]), bool(hard), held


def hold(s: Session, entries: list[Entry]) -> dict[str, dict[str, Any]]:
    """The node's quarantine record of each entry of ``entries`` that must be held, by entry id. What the
    session wrote itself is never held from it."""
    flagged = []
    for e in entries:
        flags, hard, held = judge(e)
        if held and e.sender != s.name:
            flagged.append((e.id, flags))
            if hard:
                record_injection(Identity(e.sender, AGENT))
    return s.hold(flagged) if flagged else {}


def shown(e: Entry, models: dict[str, Agent], cap: int = 0, held: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """A message as a reader gets it: fenced as untrusted data, never as an instruction; a held one as its
    placeholder until a person releases it. With ``cap`` the body is cut and says where the rest is."""
    kind, body = str(e.fields.get("type", "note")), _text(e)
    who = models.get(e.sender)
    model, state = (who.model, who.model_state) if who else ("", "")
    record = (held or {}).get(e.id)
    if cap and len(body) > cap and not record:
        body = f"{body[:cap]}…({len(body) - cap} more chars; {e.ref})"
    screened = fence(body, f"workspace:{e.sender}#{e.ref}", f"agent {e.sender} ({describe(model, state)}), {kind}")
    quarantined = bool(record) and record["state"] == "quarantined"
    text = PLACEHOLDER.format(qid=record["qid"], why=", ".join(record["flags"])) if quarantined and record else screened.text
    return {"seq": e.ref, "type": kind, "from": e.sender, "from_name": e.sender, "from_role": "agent", "from_model": model,
            "from_model_state": state, "to": e.fields.get("to", ""), "ts": e.at_ms / 1000, "reply_to": e.fields.get("reply_id", ""),
            "trust": "agent-claimed", "authority": "none",
            "state": record["state"] if record else "clear", "held": record["qid"] if record else "",
            "flags": sorted(set(record["flags"] if record else ()) | set(screened.markers)), "text": text}


def present(s: Session, entries: list[Entry], limit: int, wide: bool, raw: bool = False) -> Held:
    """``entries`` as a reader gets them: by default at most the owner's few, each cut short and all within
    one total; ``limit`` sets how many, ``wide`` shows everything uncut. ``.held`` counts the ones left
    for next time. With ``raw`` the unfenced text of each clear message is added."""
    lim = limits.load(limits.root())
    known, out, used = names(s), Held(), 0
    take = limit or (len(entries) if wide else lim.read_items)
    shown_to = entries[:take]
    records = hold(s, shown_to)
    for e in shown_to:
        item = shown(e, known, 0 if wide else lim.read_item_chars, records)
        if not wide and out and used + len(item["text"]) > lim.read_total_chars:
            break
        used += len(item["text"])
        if raw and item["state"] == "clear":
            item["raw"] = e.text
        out.append(item)
    out.held = len(entries) - len(out)
    return out


def advanced(read: list[Entry], before: dict[str, int]) -> dict[str, int]:
    """``before`` moved forward over the entries ``read``."""
    moved = dict(before)
    for e in read:
        moved[e.origin] = max(moved.get(e.origin, 0), e.number)
    return moved
