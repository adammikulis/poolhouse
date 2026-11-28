"""What the reviews of a pull request hold before it merges: who may review it, findings with a severity, standing rejects and the
attack verdict. `scripts/lead-review` records with this and the merge guard (`release_check`) judges with it.

A verdict is a `land-review` entry of the board, stamped by the node with who gave it. The branch name it is written under says
what it is (`label`): the head branch for the ordinary review, `ATTACK` before it for an attack review and `RESOLVED` before it
for the answer to the reject of a reviewer whose session has ended. The findings go in the comment on the pull request (`comment`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from typing import TYPE_CHECKING, Any

from poolhouse.workspace import landing, review_depth

if TYPE_CHECKING:
    from poolhouse.board.session import Session

__all__ = ["ATTACK", "BLOCKING", "RESOLVED", "SEVERITIES", "Refused", "answerable", "attackers", "authors", "check", "comment",
           "findings", "held", "independent", "independent_attacker", "label", "parents", "read_record", "rejects"]

ATTACK = "attack-review/"
RESOLVED = "resolved-review/"
SEVERITIES = ("critical", "high", "medium", "low")
BLOCKING = SEVERITIES[:-1]
"""The severities that block a merge; a finding of any other is a follow-up."""
MARKER = "<!-- poolhouse-review "
"""What the first line of a review's comment starts with: its record, as JSON."""
BRANCH_CHARACTERS = 128
"""The longest branch name the node takes for a review (app/poolhouse-node/src/landing.rs, `plain_branch`)."""
PLAIN = re.compile(r"(?!(main|master)$)[A-Za-z0-9][A-Za-z0-9._/-]{0,127}")
AUTHOR = re.compile(r"^[ \t]*Author-agent:[ \t]*([A-Za-z0-9][A-Za-z0-9_.-]*)[ \t\r]*$", re.M | re.I)


class Refused(RuntimeError):
    """Why a review is not recorded."""


def authors(body: str) -> list[str]:
    """The agents a pull request's description names in `Author-agent:` lines."""
    return AUTHOR.findall(body)


def label(branch: str, kind: str = "") -> str:
    """The branch name a verdict on the head of ``branch`` is written under: ``kind`` is `ATTACK`, `RESOLVED` or empty for the
    ordinary review, whose name never starts like either of the others."""
    if not kind and (branch.startswith((ATTACK, RESOLVED)) or not PLAIN.fullmatch(branch)):
        kind = "review/"
    return (kind + branch)[:BRANCH_CHARACTERS]


def findings(raw: list[str]) -> list[dict[str, str]]:
    """Each ``SEVERITY: what is wrong`` of ``raw`` as a finding."""
    found = []
    for each in raw:
        severity, _, text = each.partition(":")
        severity, text = severity.strip().lower(), text.strip()
        if severity not in SEVERITIES or not text:
            raise Refused(f"a finding is `SEVERITY: what is wrong`, SEVERITY one of {', '.join(SEVERITIES)}")
        found.append({"severity": severity, "text": text})
    return found


def check(verdict: str, found: list[dict[str, str]]) -> None:
    """Raise unless ``verdict`` is the one its findings give: a reject exactly when one of them blocks."""
    blocking = [one for one in found if one["severity"] in BLOCKING]
    if verdict == "accept" and blocking:
        raise Refused(f"a {blocking[0]['severity']} finding blocks the merge, so the verdict is reject")
    if verdict == "reject" and not blocking:
        raise Refused(f"a reject names what blocks the merge with --finding ({', '.join(BLOCKING)}); "
                      "low findings are follow-ups, recorded with an accept")


def parents(session: Session) -> dict[str, str]:
    """Each session of the board with the one that started it (empty for a main session)."""
    return {a.name: a.parent for a in session.agents(retired=True)}


def spawners(name: str, parents: dict[str, str]) -> list[str]:
    """The sessions that started ``name``, the nearest first."""
    above: list[str] = []
    while (name := parents.get(name, "")) and name not in above:
        above.append(name)
    return above


def independent(reviewer: str, parents: dict[str, str], authors: list[str]) -> None:
    """Raise unless ``reviewer`` may accept work of ``authors`` in the ordinary review; ``parents`` maps each session of the board to
    the one that started it (empty for a main session)."""
    for author in authors:
        if reviewer == author:
            raise Refused(f"{reviewer} is an author of this pull request and does not accept its own work")
        above = spawners(author, parents)
        if author in parents and not above:
            continue    # a main session's work: any other agent may have read it, one it started too
        if author in spawners(reviewer, parents):
            raise Refused(f"{reviewer} was started by its author {author}, so its accept is not independent")
        if parents.get(reviewer) and reviewer not in above:
            raise Refused(f"{author} is reviewed by the agent that spawned it ({', '.join(above) or 'unknown to this board'}) "
                          "or by a main session")


def independent_attacker(reviewer: str, parents: dict[str, str], authors: list[str]) -> bool:
    """Whether ``reviewer`` may give the attack verdict on work of ``authors``: it is none of them, and none of them started it,
    through any number of subagents."""
    return reviewer not in authors and not set(authors) & set(spawners(reviewer, parents))


def attackers(session: Session, sha: str, branch: str, authors: list[str]) -> list[str]:
    """The agents other than ``session`` whose attack accept of ``sha`` stands and is independent of ``authors``; a session that
    held the claim on ``branch``, or one it started, is none of them."""
    found = landing.accepts(session, sha, branch if PLAIN.fullmatch(branch) else "")
    above = parents(session)
    return sorted(who for who, entry in landing.live(session, found).items()
                  if entry["verdict"] == "accept" and _under(entry, ATTACK) and independent_attacker(who, above, authors))


def _ended(session: Session) -> set[str]:
    return {a.name for a in session.agents(retired=True) if a.retired}


def _may_answer(who: str, above: dict[str, str], authors: list[str], attack: bool) -> bool:
    if attack:
        return independent_attacker(who, above, authors)
    try:
        independent(who, above, authors)
    except Refused:
        return False
    return True


def _under(entry: dict[str, Any], kind: str) -> bool:
    return str(entry.get("branch")).startswith(kind)


def rejects(session: Session, commits: list[str], authors: list[str], answering: Collection[str] = ()) -> dict[str, list[str]]:
    """The commits of ``commits`` that carry a standing reject of a reviewer other than ``session``, each with who rejected it.

    A reject stands until its reviewer accepts the commit. The reject of a reviewer whose session has ended stands until another
    reviewer independent of ``authors`` answers it by name: a commit of ``answering`` (by ``session`` now) or an accept under
    `RESOLVED`; the answer to an attack reject is independent as an attack review is (`independent_attacker`).
    """
    above, gone, out = parents(session), _ended(session), {}
    for sha in commits:
        found = landing.live(session, landing.accepts(session, sha))
        answers = [who for who, entry in found.items() if entry["verdict"] == "accept" and _under(entry, RESOLVED)]
        standing = sorted(
            name for name, entry in found.items() if entry["verdict"] == "reject" and not (name in gone and (
                sha in answering or any(_may_answer(who, above, authors, _under(entry, ATTACK)) for who in answers))))
        if standing:
            out[sha] = standing
    return out


def answerable(session: Session, sha: str, attack: bool) -> None:
    """Raise unless a review by ``session`` (an attack review when ``attack``) may answer the reject standing on ``sha``."""
    found = landing.live(session, landing.accepts(session, sha))
    standing = {who: entry for who, entry in found.items() if entry["verdict"] == "reject"}
    if not standing:
        raise Refused(f"no other reviewer's reject stands on {sha[:12]}")
    if here := sorted(set(standing) - _ended(session)):
        raise Refused(f"{', '.join(here)} rejected {sha[:12]} and is still here: ask it to accept the commit")
    if not attack and any(_under(entry, ATTACK) for entry in standing.values()):
        raise Refused(f"the reject of {sha[:12]} is an attack review's: an attack review answers it (--attack)")


def comment(record: dict[str, Any], text: str) -> str:
    """The comment that records a review on its pull request: ``record`` on the first line, then ``text``."""
    line = json.dumps(record, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e")
    return f"{MARKER}{line} -->\n{text}"


def read_record(body: str) -> dict[str, Any] | None:
    """The record a review's comment holds, or None for any other comment."""
    first = body.split("\n", 1)[0].rstrip()
    if not first.startswith(MARKER) or not first.endswith(" -->"):
        return None
    try:
        found = json.loads(first[len(MARKER):-len(" -->")])
    except ValueError:
        return None
    return found if isinstance(found, dict) and found.get("verdict") in ("accept", "reject") else None


def held(session: Session, root: str, number: str, found: dict[str, Any], merger: str) -> str:
    """Why pull request ``number`` (``found``, as `gh pr view` says it) may not merge at its head, or an empty string: a reject of
    the head stands, or the change needs an attack review and no independent agent other than ``merger`` accepted it as one."""
    head, names = str(found.get("headRefOid")), authors(str(found.get("body") or ""))
    if standing := rejects(session, [head], names):
        return f"a reject of {head[:7]} by {', '.join(standing[head])} stands: a new commit answers its findings"
    base = review_depth.fetched(root, number, str(found.get("baseRefName")), head)
    reasons = review_depth.attack_reasons(root, base, head)
    if not reasons:
        return ""
    if not names and not (found.get("author") or {}).get("is_bot"):
        return f"pull request {number} names no author: its description needs an `Author-agent: NAME` line"
    if set(attackers(session, head, str(found.get("headRefName")), names)) - {merger}:
        return ""
    return (f"{head[:7]} needs an attack review ({reasons[0]}; {len(reasons)} such paths) and has no accepting attack verdict from "
            "an agent that is no author, was started by no author and is not the one merging: such an agent records one with "
            f"`scripts/lead-review {number} {head} --attack --verdict accept --note TEXT`")
