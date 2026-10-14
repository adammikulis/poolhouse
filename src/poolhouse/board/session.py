"""A session on a board: the node's methods for one token, answered as small dataclasses."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from poolhouse.board import credentials, place
from poolhouse.board.client import Client, Conflict, Denied

__all__ = ["AGENT_ENV", "TOKEN_ENV", "Agent", "Claim", "Entry", "Native", "Note", "Page", "Registration", "Sent",
           "Session", "connect", "find", "register"]

AGENT_ENV = "POOLHOUSE_WORKSPACE_AGENT"
PAGE = 1000
CHUNK = 256 * 1024
TOKEN_ENV = "POOLHOUSE_WORKSPACE_TOKEN"  # noqa: S105 - the name of a variable, not a credential


@dataclass(frozen=True, slots=True)
class Entry:
    """One board entry as the node folded it. ``ref`` is the short id of a message of this device."""

    id: str
    ref: str
    kind: str
    sender: str
    foreign: bool
    channel: str
    fields: dict[str, Any]
    at_ms: int
    gone: bool = False
    counter: int = 0

    @property
    def text(self) -> str:
        return str(self.fields.get("body", ""))

    @property
    def origin(self) -> str:
        """The log this entry is in: the part of its id between the board and the sequence number."""
        return self.id.rsplit(":", 2)[-2]

    @property
    def number(self) -> int:
        """The sequence number of this entry in its log."""
        return int(self.id.rsplit(":", 1)[-1])


@dataclass(frozen=True, slots=True)
class Sent:
    """A message its sender wrote and whether every device that syncs the board holds it."""

    entry: Entry
    status: str


@dataclass(frozen=True, slots=True)
class Page:
    entries: list[Entry]
    cursor: dict[str, int]


@dataclass(frozen=True, slots=True)
class Agent:
    name: str
    parent: str
    family: str
    model: str
    model_state: str
    harness: str
    retired: bool
    seen_ms: int
    retired_reason: str = ""
    idle: bool = False


@dataclass(frozen=True, slots=True)
class Claim:
    kind: str
    key: str
    owner: str
    expires_in_s: int
    expiring_soon: bool
    lease: str
    pid: int
    changed: bool = True
    capped: bool = False


@dataclass(frozen=True, slots=True)
class Note:
    id: str
    ref: str
    kind: str
    title: str
    body: str
    author: str
    source: str
    tags: list[str]
    trust: str
    stale: bool
    ttl_days: int
    supersedes: list[str]
    superseded_by: str
    verify_cmd: str
    last_verified: dict[str, Any] | None
    binding: bool = False


@dataclass(frozen=True, slots=True)
class Native:
    """A session as its harness knows it: the model it says it runs, the harness and its own session id."""

    model: str
    harness: str
    id: str


@dataclass(frozen=True, slots=True)
class Registration:
    name: str
    token: str
    parent: str
    created: bool
    board: str


def _entry(raw: dict[str, Any]) -> Entry:
    seq = str(raw["seq"])
    return Entry(raw["id"], seq if not raw["foreign"] else raw["id"], raw["kind"], raw["sender"], raw["foreign"],
                 raw["channel"], raw["fields"], int(raw["hlc"][0]), bool(raw.get("gone", False)), int(raw["hlc"][1]))


def _agent(raw: dict[str, Any]) -> Agent:
    return Agent(raw["name"], raw["parent"], raw["family"], raw["model"], raw["model_state"], raw["harness"],
                 raw["retired"], raw["seen_ms"], raw.get("retired_reason", ""),
                 bool(raw.get("idle", False)))


def _claim(raw: dict[str, Any], changed: bool = True) -> Claim:
    return Claim(raw["kind"], raw["key"], raw["owner"], raw["expires_in_s"], raw["expiring_soon"], raw["lease"],
                 raw["pid"], changed)


def _note(raw: dict[str, Any]) -> Note:
    names = ("id", "ref", "kind", "title", "body", "author", "source", "tags", "trust", "stale", "ttl_days",
             "supersedes", "superseded_by", "verify_cmd", "last_verified")
    return Note(*(raw[n] for n in names))


@dataclass(slots=True)
class Session:
    """The caller's token on one board of one node."""

    client: Client
    board: str
    token: str
    named: str = ""
    clock: Callable[[], float] = time.time

    def call(self, method: str, **params: Any) -> Any:
        """One node method as this session."""
        return self.client.call(method, self.board, self.token, **params)

    @property
    def name(self) -> str:
        """The name the node gives this token."""
        if not self.named:
            self.named = str(self.call("whoami")["name"])
        return self.named

    def whoami(self, model: str = "", harness: str = "") -> Agent:
        """Who the token is; ``model`` and ``harness`` record what the session says it runs on."""
        params = {k: v for k, v in (("model", model), ("harness", harness)) if v}
        got = self.call("whoami", **params)
        self.named = got["name"]
        return _agent(got["identity"])

    def agents(self, retired: bool = False) -> list[Agent]:
        return [_agent(a) for a in self.call("agents", retired=retired)["agents"]]

    def retire(self, target: str = "", reason: str = "") -> dict[str, Any]:
        """End ``target`` (default this session): its tokens, leases and name, and its subagents when it is a main session.

        ``reason`` is ``done`` (the default, an ordinary end) or, only for a subagent and by its parent, ``revoked``
        or ``forged``: an end for cause, which the landing runner holds against the work it asked for.
        """
        return dict(self.call("retire", target=target, **({"reason": reason} if reason else {})))

    def register(self, native: Native) -> Registration:
        """Register a native session as a subagent of this token's owner."""
        return register(self.client, self.board, native, self.token)

    def post(self, to: str, kind: str, body: str, subject: str = "", reply_id: str = "") -> dict[str, Any]:
        """Write a message to ``#general``, ``#announcements``, a room or one session."""
        fields = {"type": kind, "to": to, "body": body, "subject": subject}
        if reply_id:
            fields["reply_id"] = reply_id
        return dict(self.call("post", kind="message", fields=fields))

    def post_file(self, to: str, body: str, file: dict[str, Any], reply_id: str = "") -> dict[str, Any]:
        """Write a message of type `file` carrying the description of a file the node already holds."""
        fields: dict[str, Any] = {"type": "file", "to": to, "body": body, "subject": "", "file": file}
        if reply_id:
            fields["reply_id"] = reply_id
        return dict(self.call("post", kind="message", fields=fields))

    def read(self, since: dict[str, int] | None = None, **filters: Any) -> Page:
        """Entries after ``since``; the page's cursor is the ``since`` of the next read of this question.

        ``filters`` are ``kind``, ``channel``, ``by`` (a sender), ``limit``, ``inbox`` (messages to this
        session and what its subscriptions send there), ``digest`` (what its digest subscriptions collected),
        ``mentions`` (messages that name this session), ``conversation`` (messages between this session
        and one other) and ``thread`` (a message and every reply to it or to a reply). ``head`` reads
        nothing and answers the cursor of now."""
        params = {("with" if k == "conversation" else k): v for k, v in filters.items() if v}
        if since:
            params["since"] = since
        got = self.call("read", **params)
        return Page([_entry(e) for e in got["entries"]], got["cursor"])

    def read_all(self, since: dict[str, int] | None = None, **filters: Any) -> list[Entry]:
        """Every entry the question matches after ``since``, read a page at a time, oldest first."""
        found: list[Entry] = []
        cursor: dict[str, int] = dict(since or {})
        while True:
            page = self.read(cursor, limit=PAGE, **filters)
            found += page.entries
            if len(page.entries) < PAGE:
                return found
            cursor = page.cursor

    def store(self, sealed: bytes) -> str:
        """Send sealed bytes to the node in chunks; the id the node keeps them under."""
        upload, sent = uuid.uuid4().hex, 0
        while True:
            chunk = sealed[sent:sent + CHUNK]
            got = self.call("file_put", upload=upload, offset=sent, total=len(sealed), data=chunk.hex())
            sent += len(chunk)
            if "blob" in got:
                return str(got["blob"])

    def fetch(self, blob: str) -> bytes:
        """The sealed bytes the node keeps as ``blob``, if this session may read a message that carries them."""
        out = bytearray()
        while True:
            got = self.call("file_get", blob=blob, offset=len(out))
            out += bytes.fromhex(got["data"])
            if len(out) >= got["size"] or not got["data"]:
                return bytes(out)

    def files(self) -> list[Entry]:
        """The file messages this session may read, oldest first, each marked `gone` when its file was deleted."""
        return self.read_all(files=True)

    def delete_file(self, handle: str) -> dict[str, Any]:
        """Shred a file's bytes; only a session with that grant may."""
        return dict(self.call("file_delete", handle=handle))

    def status(self) -> dict[str, Any]:
        """What the node says of this board: its sessions, entries by kind and whether its logs are whole."""
        return dict(self.call("status")["board"])

    def scratch_new(self, folder: str, ttl_s: int = 0) -> str:
        """Make a private scratch folder; its absolute path."""
        return str(self.call("scratch_new", folder=folder, **({"ttl_s": ttl_s} if ttl_s else {}))["path"])

    def scratch_ls(self, owner: str = "") -> list[dict[str, Any]]:
        """The scratch folders of this session, or of a session below it, with size and expiry."""
        return list(self.call("scratch_ls", **({"owner": owner} if owner else {}))["folders"])

    def scratch_path(self, folder: str, relative: str = "", owner: str = "") -> str:
        """A path inside a scratch folder; refused when it would leave it."""
        params = {k: v for k, v in (("relative", relative), ("owner", owner)) if v}
        return str(self.call("scratch_path", folder=folder, **params)["path"])

    def scratch_rm(self, folder: str, owner: str = "") -> bool:
        """Delete a scratch folder; whether there was one."""
        return bool(self.call("scratch_rm", folder=folder, **({"owner": owner} if owner else {}))["removed"])

    def head(self) -> dict[str, int]:
        """The cursor of now: a read since it returns only what is written after."""
        return dict(self.call("read", head=True)["cursor"])

    def rooms(self) -> list[dict[str, Any]]:
        """The rooms this session may see, with whether it is a member."""
        return list(self.call("rooms")["rooms"])

    def room(self, op: str, name: str, who: str = "", title: str = "", private: bool = False) -> dict[str, Any]:
        """Make, join, leave or add ``who`` to the room ``name``."""
        params = {k: v for k, v in (("who", who), ("title", title), ("private", private)) if v}
        return dict(self.call("room", op=op, room=name, **params))

    def subscribe(self, stype: str, target: str = "", mode: str = "inbox", force: bool = False) -> dict[str, Any]:
        """Choose what reaches this session's inbox or digest: a room, thread, sender, kind or mentions."""
        return dict(self.call("subscribe", stype=stype, target=target, mode=mode, **({"force": True} if force else {})))

    def unsubscribe(self, stype: str, target: str = "") -> dict[str, Any]:
        """Drop one subscription; the messages stay."""
        return dict(self.call("unsubscribe", stype=stype, target=target))

    def subs(self) -> list[dict[str, Any]]:
        """This session's subscriptions."""
        return list(self.call("subs")["subs"])

    def outbox(self, limit: int = 0) -> list[Sent]:
        """The messages this session sent, oldest first, each `synced` or `provisional`."""
        got = self.call("outbox", **({"limit": limit} if limit else {}))
        return [Sent(_entry(e), e["status"]) for e in got["entries"]]

    def origin(self) -> str:
        """The id of this device's log of the board."""
        return str(self.call("status")["board"]["origin"])

    def hold(self, flagged: list[tuple[str, list[str]]]) -> dict[str, dict[str, Any]]:
        """Report entries whose text matched an injection pattern (id and marker names): the node keeps
        one record of each on this device. Answers each entry's quarantine record by entry id."""
        got = self.call("quarantine_hold", entries=[{"id": i, "flags": f} for i, f in flagged])
        return {item["id"]: item for item in got["items"]}

    def held(self) -> list[dict[str, Any]]:
        """The entries held on this device that this session may read, without their text."""
        return list(self.call("quarantine_list")["items"])

    def free(self, qid: str) -> dict[str, Any]:
        """Let the held entry ``qid`` be shown; only a session with that grant may."""
        return dict(self.call("quarantine_release", qid=qid)["item"])

    def note_add(self, kind: str, title: str, body: str, **extra: Any) -> dict[str, Any]:
        """Write a note; ``extra`` may hold ``source``, ``tags``, ``supersedes`` (note references),
        ``verify_cmd`` and ``ttl_days``."""
        return dict(self.call("post", kind="note", fields={"nkind": kind, "title": title, "body": body, **extra}))

    def notes(self, ref: str = "", query: str = "", kind: str = "", everything: bool = False, limit: int = 10) -> list[Note]:
        params = {k: v for k, v in (("ref", ref), ("query", query), ("kind", kind), ("all", everything)) if v}
        return [_note(n) for n in self.call("notes", limit=limit, **params)["notes"]]

    def note_verify(self, ref: str, exit_code: int, out_sha: str) -> Note:
        return _note(self.call("note_verify", note=ref, exit=exit_code, out_sha=out_sha)["notes"][0])

    def audit(self, event: str, subject: str, detail: str = "") -> None:
        """Tell the board of an event of this session: ``event`` is `agent.effort` or `authority.set_denied`."""
        self.call("audit_event", event=event, subject=subject, detail=detail)

    def claim(self, kind: str, key: str, ttl_s: int = 0, pid: int = 0, local: bool = False) -> Claim:
        """Take a claim; `Conflict` when another session holds it. A ``local`` claim waits only for this device's
        sessions: a pool-wide claim another device holds on the key does not keep it from being granted."""
        params = {k: v for k, v in (("ttl_s", ttl_s), ("pid", pid), ("local", local)) if v}
        try:
            got = self.call("claim", kind=kind, key=key, **params)
        except Denied as err:
            if " is held by " in str(err):
                raise Conflict(err.code, str(err)) from None
            raise
        return _claim(got["claim"], got["changed"])

    def reserve(self, resources: list[tuple[str, str]], ttl_s: int = 0, pid: int = 0) -> list[Claim]:
        """Take every one of ``resources`` (kind and key) or none, in one call of the node: `Conflict` when another
        session holds one of them, and nothing of the set is then taken."""
        params = {k: v for k, v in (("ttl_s", ttl_s), ("pid", pid)) if v}
        try:
            got = self.call("reserve", resources=[{"kind": kind, "key": key} for kind, key in dict.fromkeys(resources)], **params)
        except Denied as err:
            if " is held by " in str(err):
                raise Conflict(err.code, str(err)) from None
            raise
        return [_claim(c["claim"], c["changed"]) for c in got["claims"]]

    def release(self, kind: str, key: str, held_by: str = "") -> bool:
        """Give a claim up; ``held_by`` names a subagent of this session whose claim it frees."""
        params = {"held_by": held_by} if held_by else {}
        return bool(self.call("release", kind=kind, key=key, **params)["released"])

    def claims(self, kind: str = "") -> list[Claim]:
        return [_claim(c) for c in self.call("claims", **({"kind": kind} if kind else {}))["claims"]]

    def renew_claims(self, ttl_s: int) -> list[Claim]:
        """Extend every claim this session holds by ``ttl_s``, never past eight hours after it was taken; each is marked
        `capped` when that held it back."""
        renewed = []
        for c in (c for c in self.claims() if c.owner == self.name):
            try:
                capped = bool(self.call("lease_renew", id=c.lease, ttl_s=ttl_s)["capped"])
            except Denied as err:
                if "lifetime" not in str(err):
                    raise
                capped = True
            renewed.append(replace(c, capped=capped))
        return renewed

    def cursor(self, question: str) -> dict[str, int]:
        return credentials.cursors(self.client.state, self.board, self.whoami().name).get(question, {})

    def keep(self, question: str, cursor: dict[str, int]) -> None:
        credentials.remember(self.client.state, self.board, self.whoami().name, question, cursor)


def _own_token(client: Client, board: str, native: Native) -> str:
    """The token this client keeps for the native session, or an empty string."""
    try:
        name = str(client.call("session_lookup", board, harness=native.harness, session=native.id)["name"])
        return credentials.load(client.state, board, name)
    except Denied:
        return ""


def register(client: Client, board: str, native: Native, parent_token: str = "") -> Registration:
    """Register a native session on ``board`` (as a subagent of ``parent_token``'s owner when given);
    its token is kept in a private file for later commands. A session registered before keeps presenting its own token, which
    is how the node takes a session it holds no lineage for."""
    own = "" if parent_token else _own_token(client, board, native)
    try:
        got = client.call("register", board, parent_token or own, model=native.model, harness=native.harness, session=native.id)
    except Denied:
        if not own:
            raise
        got = client.call("register", board, "", model=native.model, harness=native.harness, session=native.id)
    credentials.store(client.state, board, got["name"], got["token"], got["parent"])
    return Registration(got["name"], got["token"], got["parent"], got["created"], board)


def find(harness: str, native_id: str, *, cwd: Path | None = None, client: Client | None = None) -> str:
    """The name the node gave the native session ``native_id`` of ``harness`` on this directory's board,
    or an empty string when it has none."""
    node = client or Client()
    try:
        return str(node.call("session_lookup", place.resolve(node, cwd), harness=harness, session=native_id)["name"])
    except Denied:
        return ""


def connect(*, token_file: str = "", agent: str = "", board: str = "", cwd: Path | None = None,
            client: Client | None = None) -> Session:
    """The session of the caller: its token from a file, the agent named (or $POOLHOUSE_WORKSPACE_AGENT),
    or $POOLHOUSE_WORKSPACE_TOKEN, on the board of the directory it runs in."""
    node = client or Client()
    where = place.resolve(node, cwd, board)
    named = agent or os.environ.get(AGENT_ENV, "")
    if token_file:
        token = Path(token_file).expanduser().read_text(encoding="utf-8").strip()
    elif named:
        token = credentials.load(node.state, where, named)
    elif os.environ.get(TOKEN_ENV, "").strip():
        token = os.environ[TOKEN_ENV].strip()
    else:
        raise Denied("denied", "no token: run as a registered session ($POOLHOUSE_WORKSPACE_AGENT) or give --token-file")
    return Session(node, where, token, named)
