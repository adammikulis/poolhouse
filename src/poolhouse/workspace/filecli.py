"""The `attach` and `file` commands of `poolhouse-workspace`: files as the node keeps them.

A file is sealed here under this device's key, so the node holds bytes it cannot read; the message
that carries it describes it (name, size, type, hash) and never holds the content. Reading a file
opens the sealed bytes again, and text that matches an injection pattern is held back like a message.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poolhouse.board import credentials, sealed
from poolhouse.board.session import Entry, Session
from poolhouse.command import flag
from poolhouse.errors import Denied
from poolhouse.person import require_person
from poolhouse.workspace import limits, plain
from poolhouse.workspace.files import (
    HOUR_S,
    NOT_AVAILABLE,
    classify,
    clean_name,
    human_size,
    out_path,
    read_source,
    scan_verdict,
    valid_handle,
)
from poolhouse.workspace.inbound import Held
from poolhouse.workspace.screen import Refused, fence, injection_markers, refusals

__all__ = ["ATTACH", "FILE", "TABLE", "Where", "attach", "file", "listing", "post", "save", "search", "show"]

ATTACH = [
    flag("path", help="a file in your worktree, or - to read stdin"),
    flag("--to", required=True, help="a board such as #general, an agent id, or thread:SEQ"),
    flag("--name", default="", help="the name shown (default: the file's own name)"),
    flag("--note", default="", help="one sentence; put detail in the file"),
    flag("--reply-to", default=""), flag("--derived-from", default="", help="a file handle or message number this came from")]
FILE = [
    flag("target", nargs="*", help="a file handle, or: list, search QUERY, delete HANDLE"),
    flag("--meta", action="store_true", help="metadata only (the default)"),
    flag("--text", action="store_true", help="the text, fenced as data and cut short"),
    flag("--out", default="", help="write the bytes to a new file inside your worktree"),
    flag("--limit", type=int, default=0), flag("--all", action="store_true"),
    flag("--room", default="", help="only files in this room"), flag("--by", default=""), flag("--derived-from", default="")]
LISTED = 50


@dataclass(frozen=True, slots=True)
class Where:
    """Conditions on which files a listing or search covers: the room, the sender and the file one was derived from."""

    room: str = ""
    by: str = ""
    derived_from: str = ""


def _keys(s: Session) -> Path:
    return credentials.private(s.client.state, s.board, "files")


def _destination(s: Session, to: str, reply_to: str) -> tuple[str, str]:
    """``(recipient, reply)``: the room or session a file goes to, and the message it answers."""
    if to.startswith("thread:"):
        rows = s.read_all(thread=to[7:])
        if not rows:
            raise ValueError(f"no thread {plain.line(to[7:], 20)}")
        head = rows[0]
        place = str(head.fields.get("to", ""))
        return (place if place.startswith("#") or head.sender == s.name else head.sender), head.ref
    if to == "#announcements" or not to:
        raise ValueError("attach to a board such as #general, an agent id, or thread:SEQ")
    if to.startswith("#"):
        if not any(r["name"] == to and r["member"] for r in s.rooms()):
            raise Denied(f"no room {plain.line(to, 48)} that {s.name} belongs to; join it first")
    elif to not in {a.name for a in s.agents()}:
        raise ValueError(f"no session {plain.line(to, 48)} on this board")
    return to, reply_to


def _source(entries: list[Entry], ref: str) -> str:
    """The reference a ``--derived-from`` names: a file handle or a message number the caller can read."""
    if not ref:
        return ""
    ref = ref.removeprefix("file:")
    if valid_handle(ref):
        _reach(entries, ref)
        return f"file:{ref}"
    found = next((e for e in entries if e.ref == ref), None)
    if found is None:
        raise Denied(f"{plain.line(ref, 20)} is not available to you")
    return f"msg:{found.ref}"


def _caps(s: Session, to: str, size: int, entries: list[Entry]) -> None:
    lim, now = limits.load(limits.root()), s.clock()
    if size > lim.file_bytes:
        raise Refused(f"the file is {size} bytes; the limit is {lim.file_bytes}")
    recent = sum(int(e.fields["file"]["size"]) for e in entries if e.sender == s.name and e.at_ms / 1000 > now - HOUR_S)
    if recent + size > lim.file_bytes_per_hour:
        raise Refused(f"{s.name} posted {recent} bytes of files in the last hour; this one is {size} and the limit is {lim.file_bytes_per_hour} an hour")
    here = sum(1 for e in entries if e.channel == to) if to.startswith("#") else 0
    if here >= lim.files_per_board:
        raise Refused(f"{to} holds {here} files; the limit is {lim.files_per_board}")


def _stdin(cap: int) -> tuple[bytes, str]:
    """What standard input holds, at most one byte over ``cap``."""
    return sys.stdin.buffer.read(cap + 1), ""


def attach(args: argparse.Namespace, s: Session) -> Any:
    """Post PATH (or stdin) as a file message."""
    cap = limits.load(limits.root()).file_bytes
    data, own = _stdin(cap) if args.path == "-" else read_source(args.path, cap, [limits.root(), s.client.state])
    return post(s, args.to, data, args.name or own, note=args.note, reply_to=args.reply_to, derived_from=args.derived_from)


def post(s: Session, to: str, data: bytes, name: str, **about: str) -> dict[str, Any]:
    """Seal ``data`` under this device's key and post it as a file message to a room, a session or ``thread:REF``;
    ``about`` may hold the ``note``, the ``reply_to`` message and the ``derived_from`` file or message."""
    lim = limits.load(limits.root())
    note = about.get("note", "").strip()
    if "\n" in note or len(note) > lim.file_note_chars:
        raise Refused(f"the note is one sentence of at most {lim.file_note_chars} characters; put detail in the file and keep the note to a sentence")
    if not data:
        raise Refused("the file is empty")
    name = clean_name(name or "stdin.txt", lim.file_name_chars)
    to, reply = _destination(s, to, about.get("reply_to", ""))
    entries = [e for e in s.files() if not e.gone]
    source = _source(entries, about.get("derived_from", ""))
    _caps(s, to, len(data), entries)
    kind, is_text = classify(name, data)
    text = data.decode("utf-8") if is_text else ""
    if is_text and (why := refusals(text, limits.denylist_path(limits.root()))):
        raise Refused(f"the file was not stored: {'; '.join(why)}. Remove it and attach again.")
    if not is_text and (why := scan_verdict(_keys(s) / "scan", kind, data)):
        raise Refused(f"the file was refused: {why}")
    digest, handle = sealed.handle_of(data)
    try:
        blob = s.store(sealed.seal(_keys(s), data, digest))
    except sealed.Unavailable as err:
        raise Refused(f"the file store is not available, so nothing was posted: {err}") from err
    meta = {"id": handle, "blob": blob, "sha": digest, "name": name, "size": len(data), "type": kind, "text": is_text}
    if source:
        meta["derived_from"] = source
    line = f"file: {name} {human_size(len(data))} sha:{digest[:4]}… (file {handle})"
    sent = s.post_file(to, f"{line}\n{note}" if note else line, meta, reply)
    return {"seq": sent["id"].rsplit(":", 1)[-1], "file": handle, "handle": f"file:{handle}", "name": name, "size": len(data),
            "type": kind, "held": "", "to": to, "authority": "none"}


def _reach(entries: list[Entry], handle: str) -> Entry:
    """The first message that lets the caller read ``handle``; `Denied` with one wording for a file that is
    unknown, deleted or not theirs."""
    found = next((e for e in entries if e.fields["file"]["id"] == handle and not e.gone), None) if valid_handle(handle) else None
    if found is None:
        raise Denied(NOT_AVAILABLE.format(plain.line(handle, 16)))
    return found


def _line(e: Entry) -> str:
    meta = e.fields["file"]
    return f"file: {plain.data_line(meta['name'], 80)} {human_size(int(meta['size']))} sha:{meta['sha'][:4]}… (file {meta['id']})"


def _item(e: Entry, extra: str = "") -> dict[str, Any]:
    meta = e.fields["file"]
    place = e.channel if e.channel.startswith("#") and e.channel != "#dm" else "a conversation"
    line = f"{_line(e)} by {e.sender} in {plain.data_line(place, 48)}" + (f": {extra}" if extra else "")
    return {"id": meta["id"], "handle": f"file:{meta['id']}", "name": plain.data_line(meta["name"], 80), "size": meta["size"], "by": e.sender,
            "line": line, "text": fence(line, "workspace:files", "file names and notes written by agents").text, "authority": "none"}


def _readable(s: Session, where: Where) -> list[Entry]:
    """The files the caller may read that the conditions of ``where`` keep, newest message first, one message each."""
    seen: set[str] = set()
    out = []
    derived = where.derived_from.removeprefix("file:")
    for e in sorted(s.files(), key=lambda e: (-e.at_ms, e.id)):
        handle = e.fields["file"]["id"]
        if e.gone or handle in seen:
            continue
        seen.add(handle)
        if (where.room and e.channel != where.room) or (where.by and e.sender != where.by) \
                or (derived and e.fields["file"].get("derived_from") != f"file:{derived}"):
            continue
        out.append(e)
    return out


def _content(s: Session, e: Entry) -> bytes:
    meta = e.fields["file"]
    try:
        return sealed.open_sealed(_keys(s), s.fetch(meta["blob"]), meta["sha"])
    except sealed.Unavailable as err:
        raise Refused(f"file {meta['id']} cannot be read: {err}") from err


def _held(s: Session, e: Entry, text: str) -> None:
    """`Denied` while text that matches an injection pattern is held and no person released it."""
    flags = injection_markers(text)
    if flags:
        record = s.hold([(e.id, flags)])[e.id]
        if record["state"] == "quarantined":
            raise Denied(f"file {e.fields['file']['id']} is held as {record['qid']} until a person releases it")


def _search(s: Session, rows: list[Entry], query: str) -> list[tuple[Entry, str]]:
    words = [w for w in " ".join(query.split()).casefold().split(" ") if w][:6]
    lim = limits.load(limits.root())
    ranked = []
    for e in rows:
        meta = e.fields["file"]
        note = e.text.split("\n", 1)[1] if "\n" in e.text else ""
        text = ""
        if meta["text"]:
            try:
                text = _content(s, e).decode("utf-8")[:lim.file_index_chars]
            except (Refused, UnicodeDecodeError):
                text = ""
        fields = {"text": text, "note": note, "name": str(meta["name"])}
        score = sum(3 * fields["name"].casefold().count(w) + fields["note"].casefold().count(w) + fields["text"].casefold().count(w) for w in words)
        if score:
            ranked.append((score, meta["id"], e, _snippet(fields, words, lim.file_snippet_chars)))
    ranked.sort(key=lambda r: (-r[0], r[1]))
    return [(e, snippet) for _, _, e, snippet in ranked]


def _snippet(fields: dict[str, str], words: list[str], width: int) -> str:
    for key in ("text", "note", "name"):
        body = " ".join(fields[key].split())
        low = body.casefold()
        at = min((low.find(w) for w in words if w in low), default=-1)
        if at >= 0:
            start = max(at - width // 4, 0)
            return plain.data_line(("…" if start else "") + body[start:start + width], width + 1)
    return ""


def listing(s: Session, where: Where, limit: int = 0, widen: bool = False) -> Held:
    """The files the caller may read that ``where`` keeps, newest first: a few by default, ``limit`` of them, or every one with ``widen``."""
    rows = _readable(s, where)
    take = len(rows) if widen else limit if limit > 0 else limits.load(limits.root()).file_search_results
    out = Held(_item(e) for e in rows[:take])
    out.held = len(rows) - len(out)
    return out


def search(s: Session, query: str, where: Where, limit: int = 0) -> Held:
    """The files whose name, note or text holds the words of ``query``, best first, each with a snippet."""
    hits = _search(s, _readable(s, where), query)
    take = min(limit if limit > 0 else limits.load(limits.root()).file_search_results, LISTED)
    out = Held(_item(e, snippet) for e, snippet in hits[:take])
    out.held = len(hits) - len(out)
    return out


def _entry(s: Session, handle: str) -> Entry:
    """The message that lets the caller read the file ``handle`` (``file:`` and 12 hex characters)."""
    handle = handle.removeprefix("file:")
    if not valid_handle(handle):
        raise ValueError("name one file handle (12 hex characters), or list, search QUERY, delete HANDLE")
    return _reach(s.files(), handle)


def show(s: Session, handle: str, text: bool = False, limit: int = 0, widen: bool = False) -> dict[str, Any]:
    """Describe the file ``handle``; with ``text`` its fenced text, cut to ``limit`` characters unless ``widen``."""
    e = _entry(s, handle)
    return _text(s, e, limit, widen) if text else _meta(s, e)


def save(s: Session, handle: str, out: str) -> dict[str, Any]:
    """Write the bytes of the file ``handle`` to the new path ``out`` inside the working folder."""
    return _save(s, _entry(s, handle), out)


def file(args: argparse.Namespace, s: Session) -> Any:
    """Describe, read, list, search or delete files."""
    first, rest = (args.target[0] if args.target else "list"), args.target[1:]
    where = Where(args.room, args.by, args.derived_from)
    if first == "list":
        return listing(s, where, args.limit, args.all)
    if first == "search":
        return search(s, " ".join(rest), where, args.limit)
    if first == "delete":
        require_person("file delete")
        if not rest or not valid_handle(rest[0]):
            raise ValueError("a file handle is 12 hex characters")
        return s.delete_file(rest[0])
    if rest:
        raise ValueError("name one file handle (12 hex characters), or list, search QUERY, delete HANDLE")
    return save(s, first, args.out) if args.out else show(s, first, args.text, args.limit, args.all)


def _meta(s: Session, e: Entry) -> dict[str, Any]:
    meta = e.fields["file"]
    record = next((i for i in s.held() if i["id"] == e.id), None)
    return {"id": meta["id"], "handle": f"file:{meta['id']}", "name": plain.data_line(meta["name"], 80), "size": meta["size"], "type": meta["type"],
            "is_text": bool(meta["text"]), "by": e.sender, "in": e.channel if e.channel != "#dm" else "a conversation", "message": e.ref,
            "state": record["state"] if record else "clear", "line": _line(e), "authority": "none"}


def _text(s: Session, e: Entry, limit: int, widen: bool) -> dict[str, Any]:
    meta = e.fields["file"]
    if not meta["text"]:
        raise Refused(f"file {meta['id']} is binary: only --out writes it, --meta describes it")
    text = _content(s, e).decode("utf-8")
    _held(s, e, text)
    shown, cut = plain.text(text, len(text) if widen else limit if limit > 0 else limits.load(limits.root()).file_text_chars)
    screened = fence(shown, f"workspace:file:{meta['id']}", f"file posted by {e.sender}")
    return {"id": meta["id"], "name": plain.data_line(meta["name"], 80), "size": meta["size"], "text": screened.text, "flags": list(screened.markers),
            "authority": "none", "held": max(len(text) - len(shown), 0) if cut else 0}


def _save(s: Session, e: Entry, target: str) -> dict[str, Any]:
    meta = e.fields["file"]
    path = out_path(target, [Path.cwd()])
    data = _content(s, e)
    if meta["text"]:
        _held(s, e, data.decode("utf-8", errors="replace"))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(data)
    return {"id": meta["id"], "wrote": str(path), "size": len(data), "authority": "none"}


TABLE = (
    ("attach", "post a file to a board, an agent or a thread; the message carries a handle, never the content", ATTACH, attach),
    ("file", "a file by handle (--meta, --text, --out PATH), or: list, search QUERY, delete HANDLE (a person)", FILE, file),
)
