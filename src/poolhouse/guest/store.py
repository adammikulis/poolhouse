"""What guest access keeps on disk: this computer's guest identity, the computers it was joined with, and counts. Never content.

A record is one other computer. ``role`` is what it is to this one: ``guest`` (it asks models here) or ``host`` (this computer asks
models there). Its certificate is the identity; a record is usable only once the person here confirmed the joining words, until it is
revoked or expires.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import time
import urllib.parse
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from poolhouse import home
from poolhouse.files import write_json
from poolhouse.fleet import tls
from poolhouse.lock import only_one
from poolhouse.safetext import utf8
from poolhouse.windows_private import restrict

__all__ = ["Limits", "Record", "Store", "clean_name", "fingerprint", "is_guest_endpoint"]

NAME_MOST = 64
LIFETIME_S = 30 * 86400


@dataclass(frozen=True, slots=True)
class Limits:
    """What one guest may use. Every field is a ceiling."""

    requests_per_minute: int = 30
    output_tokens_per_request: int = 8192
    tokens_per_day: int = 2_000_000
    request_bytes: int = 8 << 20

    @classmethod
    def read(cls, raw: object, base: Limits | None = None) -> Limits:
        """``base`` with the fields ``raw`` names; `ValueError` for a field that is not a whole number above zero."""
        held = asdict(base or cls())
        if not isinstance(raw, dict) or set(raw) - set(held):
            raise ValueError(f"limits are {', '.join(held)}")
        for name, value in raw.items():
            if type(value) is not int or not 0 < value <= 1 << 40:
                raise ValueError(f"{name} is a whole number above zero")
            held[name] = value
        return cls(**held)


@dataclass(frozen=True, slots=True)
class Record:
    """Another computer: what it is to this one, its certificate and where it answers."""

    id: str
    role: str
    name: str
    cert: str
    created: float
    expires: float
    at: tuple[str, ...] = ()
    confirmed: bool = False
    revoked: float = 0.0
    limits: Limits = field(default_factory=Limits)

    @property
    def der(self) -> bytes:
        return base64.b64decode(self.cert)

    def active(self, now: float | None = None) -> bool:
        return self.confirmed and not self.revoked and (time.time() if now is None else now) < self.expires


def clean_name(value: object) -> str:
    """A name another computer gave itself, as one printable line of at most `NAME_MOST` characters; it is a label, never trusted."""
    text = utf8(value) if isinstance(value, str) else ""
    return "".join(c for c in text if c.isprintable()).strip()[:NAME_MOST] or "unnamed"


class Store:
    """The guest state under the state root (``guest/``), readable by this account alone."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else home.state("guest")

    def folder(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "nt":
            restrict(path)
        else:
            path.chmod(0o700)
        return path

    def identity(self) -> tls.Identity:
        """The certificate this computer shows as a host and as a guest. Its name says nothing about the computer or its owner."""
        return tls.identity(self.folder("identity"), "poolhouse-guest")

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        self.folder()
        with only_one(self.root / "guest.lock", wait=True, timeout=30.0, announce=lambda text: None):
            yield

    def _read(self, name: str) -> dict[str, Any]:
        try:
            found = json.loads((self.root / name).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(found, dict):
            raise ValueError(f"{self.root / name} is damaged")
        return found

    def _write(self, name: str, value: dict[str, Any]) -> None:
        self.folder()
        path = self.root / name
        write_json(path, value)
        path.chmod(0o600)

    def stamp(self) -> tuple[int, int]:
        """What changes when the records do."""
        try:
            found = (self.root / "computers.json").stat()
        except FileNotFoundError:
            return (0, 0)
        return (found.st_mtime_ns, found.st_size)

    def records(self) -> list[Record]:
        out = []
        for one in self._read("computers.json").values():
            out.append(Record(**{**one, "at": tuple(one.get("at") or ()), "limits": Limits(**one["limits"])}))
        return sorted(out, key=lambda r: (r.created, r.id))

    def record(self, record_id: str) -> Record | None:
        return next((r for r in self.records() if r.id == record_id), None)

    def by_certificate(self, der: bytes, role: str) -> Record | None:
        """The active record of the computer holding exactly ``der``."""
        return next((r for r in self.records() if r.role == role and r.active() and r.der == der), None)

    def _put(self, record: Record) -> Record:
        held = self._read("computers.json")
        held[record.id] = {**asdict(record), "at": list(record.at)}
        self._write("computers.json", held)
        return record

    def add(self, role: str, name: str, der: bytes, at: tuple[str, ...] = (), *, lifetime_s: float = LIFETIME_S) -> Record:
        """A confirmed joining. An earlier record of the same computer in the same role is revoked."""
        if role not in ("guest", "host"):
            raise ValueError("a joined computer is a guest or a host")
        now = time.time()
        with self.locked():
            for old in self.records():
                if old.role == role and old.der == der and not old.revoked:
                    self._put(replace(old, revoked=now))
            return self._put(Record(id=uuid.uuid4().hex[:12], role=role, name=clean_name(name),
                                    cert=base64.b64encode(der).decode(), created=now, expires=now + lifetime_s,
                                    at=tuple(at), confirmed=True))

    def change(self, record_id: str, **fields: Any) -> Record:
        with self.locked():
            found = self.record(record_id)
            if found is None:
                raise ValueError("no joined computer has that id")
            return self._put(replace(found, **fields))

    def revoke(self, record_id: str) -> Record:
        """Nothing more is accepted from, or sent to, this computer. Done at once; a second call changes nothing."""
        found = self.record(record_id)
        if found is not None and found.revoked:
            return found
        return self.change(record_id, revoked=time.time())

    # -- joins waiting for the person here to compare the words --

    def hold(self, join_id: str, row: dict[str, Any]) -> None:
        with self.locked():
            held = {k: v for k, v in self._read("waiting.json").items() if v["expires"] > time.time()}
            self._write("waiting.json", {**held, join_id: row})

    def held(self, join_id: str, *, take: bool = False) -> dict[str, Any] | None:
        """The join waiting under ``join_id``, removed with ``take``; None when there is none or it is no longer good."""
        with self.locked():
            held = self._read("waiting.json")
            row = held.get(join_id)
            if row is not None and take:
                del held[join_id]
                self._write("waiting.json", held)
        return row if row is not None and row["expires"] > time.time() else None

    # -- counts --

    def counts(self, record_id: str) -> dict[str, float]:
        held = self._read("counts.json").get(record_id) or {}
        today = int(time.time() // 86400)
        out = {k: held.get(k, 0) for k in ("requests", "refused", "input_tokens", "output_tokens", "last_used")}
        return {**out, "tokens_today": held.get("tokens_today", 0) if held.get("day") == today else 0}

    def count(self, record_id: str, *, requests: int = 0, refused: int = 0, input_tokens: int = 0, output_tokens: int = 0) -> None:
        """Add to a guest's counts: numbers only, nothing of what was asked."""
        with self.locked():
            held = self._read("counts.json")
            now = self.counts(record_id)
            held[record_id] = {
                "requests": now["requests"] + int(requests), "refused": now["refused"] + int(refused),
                "input_tokens": now["input_tokens"] + int(input_tokens),
                "output_tokens": now["output_tokens"] + int(output_tokens),
                "tokens_today": now["tokens_today"] + int(input_tokens) + int(output_tokens),
                "day": int(time.time() // 86400), "last_used": time.time()}
            self._write("counts.json", held)


def is_guest_endpoint(url: str, store: Store | None = None) -> bool:
    """Whether ``url`` is a guest connection of this computer: what answers there is another person's computer."""
    try:
        parts = urllib.parse.urlsplit(url if "//" in url else "//" + url)
        port = parts.port
    except ValueError:
        return False
    if (parts.hostname or "").lower() not in ("127.0.0.1", "localhost", "::1") or not port:
        return False
    return ((store or Store()).root / "endpoints" / str(port)).is_file()


def fingerprint(der: bytes) -> str:
    """A certificate's sha256 as 43 characters of URL-safe base64."""
    return base64.urlsafe_b64encode(hashlib.sha256(der).digest()).decode().rstrip("=")
