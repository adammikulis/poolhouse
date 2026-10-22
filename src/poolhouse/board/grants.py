"""Standing grants as a client sees them: the grants of a board as a session reads them, and the signing a person's key
does to issue or revoke one (docs/node.md, "Standing grants").

The node counts a grant only when a person key enrolled on it signed the grant, so nothing here can make one for a
caller that does not hold that key. On a device in a pool that holds for the actions that stay on the device; a grant that reaches
beyond it counts only from an approval an admin key of the pool signed on a phone, so a typed ``yes`` signs none. The key is passed in as an object with ``public`` (32 bytes) and ``sign_bytes``.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from poolhouse.board import presence
from poolhouse.board.client import Client
from poolhouse.board.session import Session

__all__ = ["DEFAULT_ACTIONS", "Grant", "Listing", "Signs", "Want", "Where", "approval_bytes", "approve", "canonical", "default_bundle", "describe", "duration_ms",
           "enrol", "enrol_first", "enrol_line", "enrolment", "fingerprint", "here", "issue", "list_bytes", "listing", "log", "rebind",
           "revoke", "sign_approval", "sign_issue", "sign_list", "sign_rebind", "sign_revoke", "sign_unenroll", "unenrol", "want_line", "when"]

SIGN_DOMAIN = b"poolhouse/grant/v1\n"
ENROLL_DOMAIN = b"poolhouse/person-enroll/v1\n"
APPROVE_DOMAIN = b"poolhouse/person-approve/v1\n"
UNENROLL_DOMAIN = b"poolhouse/person-unenroll/v1\n"
REBIND_DOMAIN = b"poolhouse/session-rebind/v1\n"
APPROVAL_DOMAIN = b"poolhouse/approval/v1\n"
LIST_DOMAIN = b"poolhouse/approval-list/v1\n"
FRESH_MS = 5 * 60 * 1000
"""How long the node takes a person's signature as fresh."""
SPANS = {"s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
MOST_DAYS = 366


class Signs(Protocol):
    """A person's key: its public half and a signature over bytes."""

    @property
    def public(self) -> bytes: ...

    def sign_bytes(self, data: bytes) -> bytes: ...


def canonical(value: Any) -> str:
    """``value`` as the node writes it before hashing or signing: sorted keys, no spaces."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(public: bytes) -> str:
    """The name the node knows a person key by: SHA-256 of its public key."""
    return hashlib.sha256(public).hexdigest()


def duration_ms(text: str) -> int:
    """``30d``, ``12h``, ``90m`` or ``45s`` as milliseconds; at most a year."""
    found = re.fullmatch(r"([1-9][0-9]{0,6})([smhd])", text.strip())
    if not found:
        raise ValueError("a time is a whole number and s, m, h or d: 90m, 12h, 30d")
    ms = int(found[1]) * SPANS[found[2]]
    if ms > MOST_DAYS * SPANS["d"]:
        raise ValueError(f"a grant lasts at most {MOST_DAYS} days")
    return ms


def _plain(value: Any, *, lines: bool = False) -> None:
    """Refuse text the node would not read back byte for byte: grants are plain ASCII (a request's text may break lines)."""
    if isinstance(value, str) and (not value.isascii() or any(ord(c) < 32 and not (lines and c == "\n") for c in value)):
        raise ValueError("a grant is plain ASCII text")
    if isinstance(value, dict):
        for key, item in value.items():
            _plain(key, lines=lines)
            _plain(item, lines=lines)
    elif isinstance(value, list):
        for item in value:
            _plain(item, lines=lines)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _params(key: Signs, body: dict[str, Any], domain: bytes) -> dict[str, Any]:
    _plain(body)
    sig = key.sign_bytes(domain + canonical(body).encode()).hex()
    return {**{k: v for k, v in body.items() if k not in ("op", "board")}, "sig": sig}


@dataclass(frozen=True, slots=True)
class Want:
    """One grant a person asks for: ``to`` is `{"kind": "session", "name": ...}`, a class or an account."""

    action: str
    to: dict[str, Any]
    resource: str = "*"
    device: str = "*"
    lasts_ms: int = 3_600_000
    uses: int | None = None
    review: bool = False


def sign_issue(key: Signs, board: str, want: Want, *, now_ms: int | None = None, gid: str = "") -> dict[str, Any]:
    """The `grant_issue` params for ``want``, signed by ``key``."""
    at = _now_ms() if now_ms is None else now_ms
    body: dict[str, Any] = {"op": "issue", "board": board, "gid": gid or "g-" + secrets.token_hex(8), "to": want.to,
                            "action": want.action, "scope": {"board": board, "device": want.device, "resource": want.resource},
                            "at_ms": at, "until_ms": at + want.lasts_ms, "person": fingerprint(key.public)}
    if want.uses is not None:
        body["uses"] = want.uses
    if want.review:
        body["conditions"] = {"review": True}
    return _params(key, body, SIGN_DOMAIN)


def sign_revoke(key: Signs, board: str, gid: str, reason: str = "", *, now_ms: int | None = None) -> dict[str, Any]:
    """The `grant_revoke` params for the grant ``gid``, signed by ``key``."""
    body: dict[str, Any] = {"op": "revoke", "board": board, "gid": gid, "at_ms": _now_ms() if now_ms is None else now_ms,
                            "person": fingerprint(key.public)}
    if reason:
        body["reason"] = reason
    return _params(key, body, SIGN_DOMAIN)


def issue(session: Session, key: Signs, want: Want, *, now_ms: int | None = None) -> dict[str, Any]:
    """Ask the node to put ``want`` on the board, signed by ``key`` at ``now_ms``; the session only carries the request."""
    return dict(session.call("grant_issue", **sign_issue(key, session.board, want, now_ms=now_ms)))


def revoke(session: Session, key: Signs, gid: str, reason: str = "") -> dict[str, Any]:
    return dict(session.call("grant_revoke", **sign_revoke(key, session.board, gid, reason)))


def sign_distrust(key: Signs, pool: str, subject: str, since: int, reason: str = "") -> dict[str, Any]:
    """The `grant_distrust` params that cut the admin key ``subject`` (listed at epoch ``since`` in the pool ``pool``) off, signed by ``key``."""
    body: dict[str, Any] = {"op": "distrust", "board": "pool", "key": subject, "since": since, "pool": pool,
                            "at_ms": _now_ms(), "person": fingerprint(key.public)}
    if reason:
        body["reason"] = reason
    return _params(key, body, SIGN_DOMAIN)


def distrust(session: Session, key: Signs, subject: str, reason: str = "") -> dict[str, Any]:
    """Ask the node to stop counting the admin key ``subject`` of this device's pool, at once, on every board."""
    status = session.client.call("pool_status", "", session.token)
    listed = {a["fingerprint"]: a for a in status["governance"].get("admins", [])}
    if subject not in listed:
        raise RuntimeError("this pool does not list that key: `poolhouse-workspace pool status` shows the admin keys it lists")
    return dict(session.client.call("grant_distrust", "", session.token, **sign_distrust(key, status["pool"], subject, listed[subject]["since"], reason)))


def enrolment(key: Signs, about: str, *, now_ms: int | None = None) -> dict[str, Any]:
    """A key's own proof that it holds its private half, to carry to a node: valid for five minutes."""
    body = {"op": "enroll", "public": key.public.hex(), "about": about, "at_ms": _now_ms() if now_ms is None else now_ms}
    _plain(body)
    return {k: v for k, v in body.items() if k != "op"} | {"proof": key.sign_bytes(ENROLL_DOMAIN + canonical(body).encode()).hex()}


@dataclass(frozen=True, slots=True)
class Where:
    """Where a person's signature is good: the node (its device fingerprint) and the terminal it is made in (``anchor``, the process
    at the top of the signing process's lineage: the shell of the terminal)."""

    node: str
    anchor: dict[str, int]


def here(client: Client) -> Where:
    """The node ``client`` reaches and the terminal this process runs in, as the node reads them."""
    lineage = client.call("lineage")["lineage"]
    if not lineage:
        raise ValueError("the node could not read the process this runs in, so a signature cannot be made for its terminal")
    top = lineage[-1]
    return Where(str(client.call("hello")["device"]), {"pid": int(top["pid"]), "start": int(top["start"])})


def approve(proof: dict[str, Any], approver: Signs, where: Where, *, now_ms: int | None = None) -> dict[str, Any]:
    """The proof of a new key with the approval of a key the node already holds: for the node and the terminal ``where`` names
    only, and good once, for five minutes."""
    if not isinstance(proof, dict) or not {"public", "about", "at_ms", "proof"} <= proof.keys():
        raise ValueError("a carried key is what `person-proof` printed: public, about, at_ms and proof")
    at, by = _now_ms() if now_ms is None else now_ms, fingerprint(approver.public)
    body = {"op": "approve", "public": proof["public"], "about": proof["about"], "at_ms": proof["at_ms"], "approved_ms": at, "by": by,
            "node": where.node, "nonce": secrets.token_hex(16), "anchor": where.anchor}
    _plain(body)
    sig = approver.sign_bytes(APPROVE_DOMAIN + canonical(body).encode()).hex()
    return {**proof, "by": by, "approved_ms": at, "nonce": body["nonce"], "anchor": where.anchor, "approval": sig}


def enrol(client: Client, token: str, params: dict[str, Any]) -> dict[str, Any]:
    """Put a person key on the node: ``params`` from `enrolment` with the secret of a `presence.ask` (the first key) or `approve` (every later one)."""
    return dict(client.call("person_enroll", "", token, **params))


def enrol_first(client: Client, token: str, key: Signs, about: str, voice: presence.Voice = presence.SILENT) -> dict[str, Any]:
    """Put the first person key on the node after the person says yes to the node's own question (`presence.ask`): the secret is the node's, made
    when the person answered, and nothing this process writes can make one."""
    secret = str(presence.ask(client, token, "key", key.public.hex(), voice)["secret"])
    return enrol(client, token, {**enrolment(key, about), "secret": secret})


def sign_unenroll(key: Signs, fingerprint_: str, where: Where, *, now_ms: int | None = None) -> dict[str, Any]:
    """The `person_unenroll` params removing the enrolled key ``fingerprint_`` from the node and for the terminal ``where`` names,
    signed by ``key``, an enrolled key, and good once."""
    at, by = _now_ms() if now_ms is None else now_ms, fingerprint(key.public)
    body = {"op": "unenroll", "fingerprint": fingerprint_, "by": by, "at_ms": at, "node": where.node, "nonce": secrets.token_hex(16), "anchor": where.anchor}
    _plain(body)
    sent = {k: v for k, v in body.items() if k not in ("op", "node")}
    return sent | {"sig": key.sign_bytes(UNENROLL_DOMAIN + canonical(body).encode()).hex()}


def sign_rebind(key: Signs, board: str, name: str, where: Where, *, now_ms: int | None = None) -> dict[str, Any]:
    """The `rebind` params moving the main session ``name`` of ``board`` to the process that sends them, on the node and from the
    terminal ``where`` names, signed by ``key``."""
    body = {"op": "rebind", "board": board, "name": name, "node": where.node, "nonce": secrets.token_hex(16), "at_ms": _now_ms() if now_ms is None else now_ms,
            "anchor": where.anchor}
    _plain(body)
    return {"at_ms": body["at_ms"], "nonce": body["nonce"], "anchor": where.anchor, "by": fingerprint(key.public),
            "sig": key.sign_bytes(REBIND_DOMAIN + canonical(body).encode()).hex()}


def rebind(client: Client, board: str, harness: str, session: str, key: Signs) -> dict[str, Any]:
    """Move the main session ``session`` of ``harness`` on ``board`` to this process's terminal, signed by ``key``; the tokens it held end."""
    name = str(client.call("session_lookup", board, harness=harness, session=session)["name"])
    return dict(client.call("rebind", board, "", harness=harness, session=session, **sign_rebind(key, board, name, here(client))))


def unenrol(client: Client, token: str, key: Signs, fingerprint_: str) -> dict[str, Any]:
    """Take the key ``fingerprint_`` off the node, signed by ``key``; the node keeps its last key."""
    return dict(client.call("person_unenroll", "", token, **sign_unenroll(key, fingerprint_, here(client))))


@dataclass(frozen=True, slots=True)
class Grant:
    """One grant as the board stands: what was signed, how much was used, and whether it holds."""

    gid: str
    action: str
    to: dict[str, Any]
    scope: dict[str, Any]
    at_ms: int
    until_ms: int
    uses: int | None
    used: int
    review: bool
    status: str
    why: str
    person: str
    entry: str
    carried_by: str
    mine: bool


@dataclass(frozen=True, slots=True)
class Listing:
    grants: list[Grant]
    ignored: list[dict[str, Any]]
    persons: list[str]
    registry: dict[str, list[str]] = field(default_factory=dict)
    now_ms: int = 0
    device: str = ""
    governance: str = "unfounded"
    authority: list[str] = field(default_factory=list)
    may_issue: list[str] = field(default_factory=list)
    may_revoke: list[str] = field(default_factory=list)
    distrusted: list[str] = field(default_factory=list)

    def pool_wide(self, action: str) -> bool:
        """Whether a device in a pool counts a grant of ``action`` only from an admin key's approval: every action but the device-local ones."""
        return self.governance == "governed" and action not in self.registry.get("device_local", ())


def listing(session: Session) -> Listing:
    """Every grant of the session's board with its state, the entries that name none that count, and the registry."""
    got = session.call("grants")
    rows = [Grant(**{k: g[k] for k in Grant.__dataclass_fields__}) for g in got["grants"]]
    return Listing(rows, got["ignored"], got["persons"], got["registry"], got["now_ms"], got["device"], got["governance"], got["authority"], got["may_issue"],
                   got["may_revoke"], got.get("distrusted", []))


def log(session: Session, limit: int = 50) -> list[dict[str, Any]]:
    """The newest grant entries: issues, revocations and uses, with who used what for which target."""
    return list(session.call("grant_log", limit=limit)["log"])


DEFAULT_ACTIONS = ("task_authority", "note_verify", "shard_call")
DESCRIPTIONS = {
    "task_authority": "act for the person on tasks: assign, review in strict mode, set the enforcement mode, record a landing",
    "note_verify": "record that the command of a note was run and what it printed (subagents included)",
    "shard_call": "run tests on another device of the pool that allowed this device",
}


def default_bundle(who: Sequence[dict[str, Any]], device: str, days: int = 30) -> list[tuple[Want, str]]:
    """The grants a person approves once at setup, each with a line saying what it lets the lead do.

    ``who`` names the lead's main sessions: `{"kind": "session", "name": ...}` for one session, or a class. Each grant lasts
    ``days`` days on ``device`` and is revoked by its own id. `note_verify` also reaches the subagents of the sessions named.
    """
    if not 1 <= days <= MOST_DAYS:
        raise ValueError(f"days is from 1 to {MOST_DAYS}")
    out = []
    for action in DEFAULT_ACTIONS:
        for to in who:
            widened = {**to, "subagents": True} if to["kind"] == "session" else {**to, "main": False}
            out.append((Want(action, widened if action == "note_verify" else to, "*", device, days * SPANS["d"]), DESCRIPTIONS[action]))
    return out


def _grantee(to: dict[str, Any]) -> str:
    if to["kind"] == "session":
        return f"the session {to['name']}" + (" and its subagents" if to.get("subagents") else "")
    if to["kind"] == "account":
        return f"every session whose verified model is {to['model']}"
    kind = "every" if not to.get("main", True) else "every main"
    return f"{kind} session that says it is a {to['family']}" + (" and is verified" if to.get("verified") else " (registration is open: any process can say so)")


def _span(ms: int) -> str:
    for unit, size in (("day", 86_400_000), ("hour", 3_600_000), ("minute", 60_000), ("second", 1000)):
        if ms >= size and ms % size == 0:
            return f"{ms // size} {unit}{'' if ms == size else 's'}"
    return f"{ms} ms"


def when(ms: int) -> str:
    """``ms`` since the epoch as a UTC date and time to the second."""
    return time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(ms / 1000))


def _issue_line(board: str, grant: dict[str, Any]) -> str:
    uses, device, resource = grant["uses"], grant["device"], grant["resource"]
    used = "as often as it is needed" if uses is None else f"{uses} use{'' if uses == 1 else 's'}"
    where = "any device" if device == "*" else f"device {device}"
    return (f"allow {_grantee(grant['to'])} to {grant['action']} on {'anything' if resource == '*' else resource} (board {board}, {where}), "
            f"for {_span(grant['lasts_ms'])}, ending {when(grant['until_ms'])}, {used}" + (", each use behind an independent review" if grant["review"] else ""))


def want_line(board: str, want: Want, at_ms: int) -> str:
    """What a person reads before signing ``want`` on ``board`` at ``at_ms``."""
    return _issue_line(board, {"to": want.to, "action": want.action, "device": want.device, "resource": want.resource,
                               "lasts_ms": want.lasts_ms, "until_ms": at_ms + want.lasts_ms, "uses": want.uses, "review": want.review})


def enrol_line(public: bytes, about: str) -> str:
    """What a person reads before a key is enrolled as a person key."""
    return f"let the key {public.hex()} ({about}) act as a person key on the node"


def approval_bytes(challenge: dict[str, Any], decision: str, key: str) -> bytes:
    """The bytes the key ``key`` signs to give ``decision`` (`approve` or `deny`) on the ``challenge`` of a request."""
    _plain(challenge, lines=True)
    return APPROVAL_DOMAIN + canonical({**challenge, "decision": decision, "key": key}).encode()


def list_bytes(node: str, at_ms: int) -> bytes:
    """The bytes a person's key signs to read every request that waits on the node whose certificate fingerprint is ``node``."""
    return LIST_DOMAIN + canonical({"op": "approval_list", "node": node, "at_ms": at_ms}).encode()


def sign_list(key: Signs, node: str, at_ms: int) -> dict[str, Any]:
    """The `approval_pending` params that let the person's ``key`` read every request that waits: a session reads only its own."""
    return {"key": fingerprint(key.public), "sig": key.sign_bytes(list_bytes(node, at_ms)).hex(), "at_ms": at_ms}


DECISIONS = ("approve", "approve_repeat", "deny", "block")
DECISION_WORDS = {"approve": "APPROVE", "approve_repeat": "APPROVE AGAIN (it was declined before)", "deny": "DENY", "block": "DENY AND BLOCK"}


def sign_approval(key: Signs, request: dict[str, Any], decision: str) -> dict[str, Any]:
    """The `approval_decide` params answering the ``request`` (as the node showed it) with ``decision``, signed by ``key``.

    ``decision`` is approve, approve_repeat (a scope that was declined before), deny, or block (deny, and the agent that asked cannot
    ask again for the time the text names). A number to match is not signed: the caller adds it as ``pick`` once the person chose.
    """
    if decision not in DECISIONS:
        raise ValueError("a decision is approve, approve_repeat, deny or block")
    fingerprint_ = fingerprint(key.public)
    bytes_ = approval_bytes(request["challenge"], decision, fingerprint_)
    return {"id": request["id"], "decision": decision, "key": fingerprint_, "sig": key.sign_bytes(bytes_).hex(),
            "text_sha256": hashlib.sha256(str(request["text"]).encode()).hexdigest()}


def describe(data: bytes) -> str:
    """What signing ``data`` does, in words, read from the bytes themselves; `ValueError` for anything a person key does not sign."""
    domain, _, rest = data.partition(b"\n")
    try:
        body = json.loads(rest)
        if domain + b"\n" == SIGN_DOMAIN and body["op"] == "issue":
            scope = body["scope"]
            return _issue_line(body["board"], {"to": body["to"], "action": body["action"], "device": scope["device"], "resource": scope["resource"],
                                               "lasts_ms": body["until_ms"] - body["at_ms"], "until_ms": body["until_ms"], "uses": body.get("uses"),
                                               "review": bool(body.get("conditions", {}).get("review"))})
        if domain + b"\n" == SIGN_DOMAIN and body["op"] == "revoke":
            return f"end the grant {body['gid']} on board {body['board']}" + (f" ({body['reason']})" if body.get("reason") else "")
        if domain + b"\n" == ENROLL_DOMAIN:
            return enrol_line(bytes.fromhex(body["public"]), body["about"])
        if domain + b"\n" == APPROVE_DOMAIN and body["op"] == "approve":
            return (f"{enrol_line(bytes.fromhex(body['public']), body['about'])} {body['node']}, approved by the key {body['by']}, "
                    f"once, until {when(body['approved_ms'] + FRESH_MS)}, in the terminal whose shell is process {body['anchor']['pid']}")
        if domain + b"\n" == APPROVAL_DOMAIN and body["op"] == "approval":
            shown = "\n".join(f"      {line}" for line in str(body["text"]).split("\n"))
            return f"{DECISION_WORDS.get(str(body['decision']), str(body['decision']).upper())} the request {body['id']} of {body['session'] or 'a phone'} on board {body['board']}, which reads:\n{shown}"
        if domain + b"\n" == LIST_DOMAIN and body["op"] == "approval_list":
            return f"READ every request that waits for you on the node {body['node']}, with the words each one carries, so that you can answer one; good for thirty seconds"
        if domain + b"\n" == REBIND_DOMAIN and body["op"] == "rebind":
            return (f"move the session {body['name']} of board {body['board']} to the terminal this command runs in (the shell is process "
                    f"{body['anchor']['pid']}), on the node {body['node']}, and end the tokens it holds now; once, until {when(body['at_ms'] + FRESH_MS)}")
        if domain + b"\n" == UNENROLL_DOMAIN and body["op"] == "unenroll":
            return (f"take the person key {body['fingerprint']} off the node {body['node']}, signed by the key {body['by']}, once, "
                    f"until {when(body['at_ms'] + FRESH_MS)}, in the terminal whose shell is process {body['anchor']['pid']}")
    except (KeyError, TypeError, AttributeError, ValueError):
        pass
    raise ValueError("a person key signs a grant, the end of a grant, the enrolment or removal of a key, the move of a session or the answer to a request, and this is none of them")
