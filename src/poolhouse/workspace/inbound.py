"""Messages as a reader gets them: fenced as data, and held back when their text matches an injection pattern.

The node keeps what was written. This module decides, for the session reading, what it is shown:
text with a hard injection marker, or a soft one from a sender without good standing, is replaced
by a placeholder until a person releases it, as the node's quarantine records.
"""

from __future__ import annotations

from typing import Any

from poolhouse.board.session import Agent, Entry, Session
from poolhouse.workspace import limits
from poolhouse.workspace.files import NOT_AVAILABLE, REF, human_size
from poolhouse.workspace.modelid import describe, safe
from poolhouse.workspace.plain import data_line
from poolhouse.workspace.screen import fence, marker_tiers
from poolhouse.workspace.standing import record_injection, sender_standing

__all__ = ["PLACEHOLDER", "Held", "advanced", "hold", "judge", "judge_text", "names", "present", "shown"]

PLACEHOLDER = "[held in quarantine as {qid}: {why}. A person releases it; until then it is not shown]"


class Held(list):  # type: ignore[type-arg]
    """A list of results that also says how many more were held back by the default caps."""

    held: int = 0


class Seen(dict[str, dict[str, Any]]):
    """What a reader knows about entries it is about to show: the node's quarantine record of each held one, by entry
    id, and in ``files`` the file messages its text names, by handle."""

    files: dict[str, Entry]


def names(s: Session) -> dict[str, Agent]:
    """Every identity of the board by name, retired ones too."""
    return {a.name: a for a in s.agents(True)}


def _text(e: Entry) -> str:
    subject = str(e.fields.get("subject", ""))
    return f"subject: {subject}\n{e.text}" if subject else e.text


def judge_text(text: str, sender: str) -> tuple[list[str], bool, bool]:
    """``(markers, hard, held)``: the injection markers in ``text`` written by ``sender``, whether any is hard, and whether
    they hold it: a hard marker always does, a soft one only when its sender does not have good standing."""
    hard, soft = marker_tiers(text)
    held = bool(hard) or (bool(soft) and sender_standing(sender) != "good")
    return sorted([*hard, *soft]), bool(hard), held


def judge(e: Entry) -> tuple[list[str], bool, bool]:
    """``judge_text`` of the subject and body of ``e`` and its sender."""
    return judge_text(f"{e.fields.get('subject', '')}\n{e.text}", e.sender)


def hold(s: Session, entries: list[Entry]) -> Seen:
    """The node's quarantine record of each entry of ``entries`` that must be held, by entry id, and the files
    their text names. What the session wrote itself is never held from it."""
    flagged = []
    for e in entries:
        flags, hard, held = judge(e)
        if held and e.sender != s.name:
            flagged.append((e.id, flags))
            if hard:
                record_injection(e.sender)
    seen = Seen(s.hold(flagged) if flagged else {})
    named = any(REF.search(e.text) for e in entries)
    seen.files = {e.fields["file"]["id"]: e for e in s.files() if not e.gone} if named else {}
    return seen


def _refs(text: str, files: dict[str, Entry]) -> str:
    """``text`` with each ``file:HANDLE`` shown as its name and size when the reader may read it, else as unavailable."""
    def one(m: Any) -> str:
        found = files.get(m.group(1))
        if found is None:
            return NOT_AVAILABLE.format(m.group(1))
        meta = found.fields["file"]
        return f"file:{m.group(1)} ({data_line(meta['name'], 60)}, {human_size(int(meta['size']))})"
    return REF.sub(one, text) if "file:" in text else text


def shown(e: Entry, models: dict[str, Agent], cap: int = 0, held: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """A message as a reader gets it: fenced as untrusted data, never as an instruction; a held one as its
    placeholder until a person releases it. With ``cap`` the body is cut and says where the rest is."""
    kind, body = str(e.fields.get("type", "note")), _text(e)
    if held is not None and e.id not in held:
        body = _refs(body, getattr(held, "files", {}))
    who = models.get(e.sender)
    model, state = (safe(who.model), who.model_state) if who else ("", "")
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
