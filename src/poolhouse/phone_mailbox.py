"""The linked phones of this account and what they send to agents (docs/phone-api.md, docs/phone-sessions.md).

One folder, ``<state>/person/phones``: the folder agents are kept out of (`poolhouse.protectedwrites`). It holds the linked phones'
public keys, the signed requests that wait for an agent, what happened to them, and the live state the harness hooks report.

A message speaks as the person only because a linked phone's key signed it: `Store.claim` checks the signature, the session the signed path
names, the age and that the phone is still linked, at the moment of delivery, in the process that hands it to the agent. A file somebody else
wrote here, a workspace message or another agent's words never pass that check.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from poolhouse import home, jsonl
from poolhouse.files import read_json, versioned, write_json
from poolhouse.lock import only_one

__all__ = ["MESSAGE_LIFE_S", "MOST_PHONES", "MOST_TEXT", "SESSION", "Message", "Phone", "Refused", "Signed", "Store", "check_key",
           "key_words", "verify"]

VERSION = 1
MOST_PHONES = 8
MOST_TEXT = 8_000
"""Characters of one message: with its heading it stays under the 10000 a harness hands an agent whole."""
MOST_WAITING = 20
"""Messages one session holds undelivered."""
MESSAGE_LIFE_S = 24 * 3600
MOST_EVENTS_BYTES = 1 << 20
SESSION = re.compile(r"(?:claude|subagent|delegate|agent)-[A-Za-z0-9._-]{1,160}\Z")
"""A session id as the phone API names it; never a path."""
PHONE_ID = re.compile(r"ph-[0-9a-f]{12}\Z")
QUESTION_ID = re.compile(r"q-[0-9a-f]{16}\Z")
REQUEST_LABEL = "poolhouse/phone-request/v1"
PREFIX = "/phone/v1/sessions/"


class Refused(Exception):
    """Why something a phone sent does not count, in one sentence."""


@dataclass(frozen=True, slots=True)
class Phone:
    """One linked phone. ``key`` is its public key: base64 of the DER SubjectPublicKeyInfo of an ECDSA P-256 key."""

    id: str
    name: str
    key: str
    linked_at: float
    seen_at: float = 0.0
    pool_use: bool = False
    """Whether the pool may send work to this phone; off until someone switches it on."""

    @property
    def words(self) -> str:
        return key_words(self.key)


@dataclass(frozen=True, slots=True)
class Signed:
    """A request exactly as a phone signed it."""

    pin: str
    phone: str
    method: str
    path: str
    time_ms: int
    nonce: str
    body: str
    signature: str

    def bytes(self) -> bytes:
        """The bytes the phone signs for this request."""
        body = hashlib.sha256(self.body.encode()).hexdigest()
        return "\n".join((REQUEST_LABEL, self.pin, self.phone, self.method, self.path, str(self.time_ms), self.nonce, body)).encode()


@dataclass(frozen=True, slots=True)
class Message:
    """A verified message: the person's words, the phone that signed them and the session they are addressed to."""

    id: str
    target: str
    text: str
    phone: Phone
    sent_at: float


def _public_key(key: str) -> Any:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    try:
        found = load_der_public_key(base64.b64decode(key, validate=True))
    except (ValueError, TypeError) as exc:
        raise Refused("That key is not an ECDSA P-256 public key.") from exc
    if not isinstance(found, ec.EllipticCurvePublicKey) or found.curve.name != "secp256r1":
        raise Refused("That key is not an ECDSA P-256 public key.")
    return found


def check_key(key: str) -> None:
    """`Refused` unless ``key`` is a P-256 public key in base64 DER."""
    if len(key) > 200:
        raise Refused("That key is not an ECDSA P-256 public key.")
    _public_key(key)


def verify(key: str, message: bytes, signature: str) -> bool:
    """Whether ``signature`` (base64 DER, ECDSA with SHA-256) is ``key``'s signature of ``message``."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    try:
        _public_key(key).verify(base64.b64decode(signature, validate=True), message, ec.ECDSA(hashes.SHA256()))
    except (Refused, InvalidSignature, ValueError, TypeError):
        return False
    return True


def key_words(key: str) -> str:
    """The first 24 hex digits of the SHA-256 of a key's DER, in groups of four: what a person compares across two screens."""
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:24]
    return " ".join(digest[i:i + 4] for i in range(0, 24, 4))


class Store:
    """The phone folder. Every change takes the folder's lock, so the daemon and the hooks of several sessions take turns."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else home.guarded("person", "phones")

    # -- files ---------------------------------------------------------------
    def _made(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts)
        for folder in (self.root, path.parent):
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    @contextmanager
    def locked(self) -> Iterator[None]:
        with only_one(self._made(".lock"), timeout=10.0, announce=lambda _said: None):
            yield

    def _write(self, path: Path, value: dict[str, Any]) -> None:
        write_json(path, versioned(value, VERSION), indent=None)
        if os.name != "nt":
            path.chmod(0o600)

    def audit(self, phone: str, action: str, target: str = "") -> None:
        """One line of who asked for what; never a message's words."""
        jsonl.append(self._made("audit.jsonl"), [{"at": time.time(), "phone": phone, "action": action, "target": target[:200]}])

    # -- phones ----------------------------------------------------------------
    def phones(self) -> dict[str, Phone]:
        held = read_json(self.root / "phones.json", {})
        rows = held.get("phones", {}) if isinstance(held, dict) else {}
        found = {}
        for key, row in rows.items():
            try:
                found[key] = Phone(str(row["id"]), str(row["name"]), str(row["key"]), float(row["linked_at"]),
                                   float(row.get("seen_at") or 0.0), row.get("pool_use") is True)
            except (KeyError, TypeError, ValueError):
                continue
        return found

    def _save(self, phones: dict[str, Phone]) -> None:
        self._write(self._made("phones.json"), {"phones": {key: asdict(each) for key, each in phones.items()}})

    def link(self, name: str, key: str) -> Phone:
        """Add a phone whose link proof was checked; a phone linked again with the same key replaces its old row."""
        check_key(key)
        with self.locked():
            kept = {i: p for i, p in self.phones().items() if p.key != key}
            if len(kept) >= MOST_PHONES:
                raise Refused(f"This computer has {MOST_PHONES} linked phones; remove one first.")
            phone = Phone("ph-" + secrets.token_hex(6), name, key, time.time())
            self._save({**kept, phone.id: phone})
        self.audit(phone.id, "link", name)
        return phone

    def unlink(self, phone: str, by: str = "") -> bool:
        with self.locked():
            phones = self.phones()
            if phones.pop(phone, None) is None:
                return False
            self._save(phones)
        self.audit(by or phone, "unlink", phone)
        return True

    def change(self, phone: str, *, pool_use: bool | None = None, seen_at: float | None = None) -> Phone:
        with self.locked():
            phones = self.phones()
            held = phones.get(phone)
            if held is None:
                raise Refused("This phone is not linked to this computer.")
            new = Phone(held.id, held.name, held.key, held.linked_at, held.seen_at if seen_at is None else seen_at,
                        held.pool_use if pool_use is None else pool_use)
            phones[phone] = new
            self._save(phones)
        return new

    def signer(self, signed: Signed) -> Phone:
        """The linked phone that signed ``signed``; `Refused` for any other signature."""
        phone = self.phones().get(signed.phone)
        if phone is None:
            raise Refused("This phone is not linked to this computer.")
        if not verify(phone.key, signed.bytes(), signed.signature):
            raise Refused("The signature does not match this phone's key.")
        return phone

    # -- messages ----------------------------------------------------------------
    @staticmethod
    def _target(session: str) -> str:
        if not SESSION.match(session):
            raise Refused("That is not a session id.")
        return session

    @staticmethod
    def _words(signed: Signed, target: str) -> str:
        if signed.method != "POST" or signed.path != f"{PREFIX}{target}/messages":
            raise Refused("That signature is not for a message to this session.")
        try:
            text = json.loads(signed.body)["text"]
        except (ValueError, KeyError, TypeError) as exc:
            raise Refused("A message is {\"text\": \"...\"}.") from exc
        if not isinstance(text, str) or not text.strip() or len(text) > MOST_TEXT:
            raise Refused(f"A message is 1 to {MOST_TEXT} characters of text.")
        return text

    def send(self, target: str, signed: Signed) -> str:
        """Queue a phone's message for ``target``; its id."""
        self._target(target)
        phone = self.signer(signed)
        text = self._words(signed, target)
        ident = "m-" + hashlib.sha256(signed.bytes()).hexdigest()[:20]
        with self.locked():
            folder = self.root / "mail" / target
            if folder.is_dir() and sum(1 for _ in folder.glob("m-*.json")) >= MOST_WAITING:
                raise Refused("This session has too many messages waiting; wait until it takes them.")
            if (self.root / "sent" / f"{ident}.json").exists():
                raise Refused("That message was already delivered.")
            self._write(self._made("mail", target, f"{ident}.json"), asdict(signed))
        self.event(target, "message", message=ident, text=text)
        self.audit(phone.id, "message", target)
        return ident

    def waiting(self, target: str) -> int:
        folder = self.root / "mail" / self._target(target)
        return sum(1 for _ in folder.glob("m-*.json")) if folder.is_dir() else 0

    def addressed(self) -> list[str]:
        """The sessions that have a message waiting."""
        folder = self.root / "mail"
        return sorted(p.name for p in folder.iterdir() if SESSION.match(p.name) and any(p.glob("m-*.json"))) if folder.is_dir() else []

    def claim(self, target: str, *, now: float | None = None) -> list[Message]:
        """Take every message that waits for ``target``, oldest first. Each is checked here (signature, session, age, phone still
        linked) and leaves the queue exactly once; one that fails is dropped with the reason as an event."""
        self._target(target)
        now = time.time() if now is None else now
        folder = self.root / "mail" / target
        if not folder.is_dir():
            return []
        taken: list[Message] = []
        dropped: list[tuple[str, str]] = []
        with self.locked():
            for path in folder.glob("m-*.json"):
                row = read_json(path, {})
                ident = path.stem
                try:
                    row.pop("version", None)
                    signed = Signed(**row)
                    if ident != "m-" + hashlib.sha256(signed.bytes()).hexdigest()[:20]:
                        raise Refused("The message was changed after it was signed.")
                    phone = self.signer(signed)
                    text = self._words(signed, target)
                    if now - signed.time_ms / 1000 > MESSAGE_LIFE_S:
                        raise Refused("It waited more than a day.")
                    if (self.root / "sent" / f"{ident}.json").exists():
                        raise Refused("It was already delivered.")
                except (Refused, TypeError, AttributeError) as why:
                    dropped.append((ident, str(why) if isinstance(why, Refused) else "It could not be read."))
                    path.unlink(missing_ok=True)
                    continue
                self._write(self._made("sent", f"{ident}.json"), {"target": target, "at": now})
                path.unlink(missing_ok=True)
                taken.append(Message(ident, target, text, phone, signed.time_ms / 1000))
            self._forget_sent(now)
        for ident, why in dropped:
            self.event(target, "dropped", message=ident, text=why)
        taken.sort(key=lambda m: (m.sent_at, m.id))
        for message in taken:
            self.event(target, "delivered", message=message.id)
        return taken

    def _forget_sent(self, now: float) -> None:
        """Delivery marks older than a message can live are no longer needed to refuse a copy."""
        for path in (self.root / "sent").glob("m-*.json"):
            if now - path.stat().st_mtime > 2 * MESSAGE_LIFE_S:
                path.unlink(missing_ok=True)

    # -- events ------------------------------------------------------------------
    def event(self, session: str, what: str, /, **fields: Any) -> dict[str, Any]:
        """Record something of type ``what`` that happened in ``session`` and that its transcript does not hold."""
        row = {"id": f"m:{time.time_ns()}", "at": time.time(), "type": what, **fields}
        path = self._made("events", f"{self._target(session)}.jsonl")
        with self.locked():
            if path.exists() and path.stat().st_size > MOST_EVENTS_BYTES:
                kept = path.read_text("utf-8", errors="replace").splitlines(keepends=True)
                path.write_text("".join(kept[len(kept) // 2:]), "utf-8")
            jsonl.append(path, [row])
        return row

    def events(self, session: str) -> list[dict[str, Any]]:
        path = self.root / "events" / f"{self._target(session)}.jsonl"
        try:
            lines = path.read_text("utf-8", errors="replace").splitlines()
        except OSError:
            return []
        found = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and "id" in row and "type" in row:
                found.append(row)
        return found

    def eventful(self) -> list[str]:
        """The sessions that have events."""
        folder = self.root / "events"
        return sorted(p.stem for p in folder.glob("*.jsonl") if SESSION.match(p.stem)) if folder.is_dir() else []

    # -- sessions the hooks report -----------------------------------------------
    def session(self, session: str) -> dict[str, Any]:
        """What the hooks last reported of ``session``; empty when none did."""
        held = read_json(self.root / "sessions" / f"{self._target(session)}.json", {})
        return held if isinstance(held, dict) else {}

    def report(self, session: str, **fields: Any) -> dict[str, Any]:
        path = self._made("sessions", f"{self._target(session)}.json")
        with self.locked():
            held = {**self.session(session), **fields, "updated": time.time()}
            held.pop("version", None)
            self._write(path, held)
        return held

    def sessions(self) -> dict[str, dict[str, Any]]:
        folder = self.root / "sessions"
        names = sorted(p.stem for p in folder.glob("*.json") if SESSION.match(p.stem)) if folder.is_dir() else []
        return {name: self.session(name) for name in names}

    # -- questions ---------------------------------------------------------------
    def ask(self, session: str, questions: list[dict[str, Any]], wait_s: float) -> str:
        ident = "q-" + secrets.token_hex(8)
        row = {"question": ident, "session": self._target(session), "questions": questions, "asked_at": time.time(),
               "expires_at": time.time() + wait_s}
        self._write(self._made("questions", f"{ident}.json"), row)
        self.event(session, "question", question=ident, questions=questions, expires_at=row["expires_at"])
        return ident

    def open_questions(self, now: float | None = None) -> list[dict[str, Any]]:
        now = time.time() if now is None else now
        folder = self.root / "questions"
        found = []
        for path in sorted(folder.glob("q-*.json")) if folder.is_dir() else []:
            if path.name.endswith(".answer.json"):
                continue
            row = read_json(path, {})
            if row and float(row.get("expires_at") or 0) > now and not (folder / f"{path.stem}.answer.json").exists():
                row.pop("version", None)
                found.append(row)
        return found

    def _answers(self, signed: Signed, session: str, question: str) -> dict[str, str]:
        if signed.method != "POST" or signed.path != f"{PREFIX}{session}/questions/{question}/answer":
            raise Refused("That signature is not for an answer to this question.")
        try:
            answers = json.loads(signed.body)["answers"]
        except (ValueError, KeyError, TypeError) as exc:
            raise Refused("An answer is {\"answers\": {\"<question>\": \"<answer>\"}}.") from exc
        if (not isinstance(answers, dict) or not 1 <= len(answers) <= 8
                or not all(isinstance(k, str) and isinstance(v, str) and 0 < len(v) <= 4000 for k, v in answers.items())):
            raise Refused("An answer gives each question a text of at most 4000 characters.")
        return answers

    def answer(self, session: str, question: str, signed: Signed) -> None:
        self._target(session)
        phone = self.signer(signed)
        if not QUESTION_ID.match(question) or not any(q["question"] == question and q["session"] == session for q in self.open_questions()):
            raise Refused("That question is not open any more.")
        self._answers(signed, session, question)
        self._write(self._made("questions", f"{question}.answer.json"), asdict(signed))
        self.audit(phone.id, "answer", session)

    def answered(self, session: str, question: str) -> tuple[dict[str, str], Phone] | None:
        """The verified answer to ``question``, once a linked phone gave one."""
        path = self.root / "questions" / f"{question}.answer.json"
        row = read_json(path, {})
        if not row:
            return None
        row.pop("version", None)
        try:
            signed = Signed(**row)
            return self._answers(signed, session, question), self.signer(signed)
        except (Refused, TypeError):
            return None

    def close_question(self, session: str, question: str, by: str) -> None:
        for name in (f"{question}.json", f"{question}.answer.json"):
            (self.root / "questions" / name).unlink(missing_ok=True)
        self.event(session, "answered", question=question, by=by)

    # -- connected projects --------------------------------------------------------
    def projects(self) -> dict[str, bool]:
        """Project folder -> whether it is connected; False remembers that the person turned it off."""
        held = read_json(self.root / "projects.json", {})
        rows = held.get("projects", {}) if isinstance(held, dict) else {}
        return {str(k): v is True for k, v in rows.items()}

    def project(self, folder: str, connected: bool) -> None:
        with self.locked():
            self._write(self._made("projects.json"), {"projects": {**self.projects(), folder: connected}})
