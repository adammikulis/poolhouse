"""One scan joins two computers. Either side shows a code, the other reads it (a QR code, or the same text sent by a messenger).

The code carries the shower's certificate fingerprint, a secret good for one use and a few minutes, and the private addresses it
answers joining on. The scanner connects, takes only the certificate with that fingerprint, and sends its own certificate under a
MAC of the secret. Both then show the same four words, made from the secret and both certificates; each side keeps the other only
when the person there says the words match. A copied code is therefore not enough: whoever uses it first makes the shower show words
the real scanner never sees.

The shower listens only while the code is good, on one private address, and answers one request kind.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import http.client
import json
import secrets
import ssl
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Any

from poolhouse.fleet import tls
from poolhouse.guest import wire
from poolhouse.guest.store import Store, clean_name, fingerprint

__all__ = ["CODE_MOST", "JoinError", "Joins", "words_for"]

SCHEME = "poolhouse://guest?"
CODE_MOST = 300
NAME_IN_CODE = 24
TTL_S = 600
MOST_TTL_S = 3600
MOST_WRONG = 5
ROLES = {"host": "guest", "guest": "host"}

_WORDS = """acorn amber anchor apple arrow aspen badge baker barn basil beach berry birch bison bloom board bottle branch brass
bread brick bridge brook broom bucket cabin cactus camel candle canoe canyon carpet cedar chalk cherry cliff clock cloud clover coast
cobalt comet copper coral cotton crane crayon creek crow crown daisy delta desert donkey dragon drum eagle ember engine falcon feather
fern ferry field fig flame flint flute forest fossil fox garden garlic gecko ginger glacier globe goat granite grape gravel hammer
harbor harp hazel helmet heron hill honey horse island ivory jacket jasmine jelly jungle kettle kite ladder lagoon lake lantern lemon
lily linen lizard lotus lunar magnet mango maple marble meadow melon meteor mint mitten monkey moss mountain needle nest nickel"""
WORDS = tuple(_WORDS.split())


class JoinError(ValueError):
    """Why a joining did not happen, in words that name no secret."""


def _mac(secret: bytes, *parts: object) -> str:
    return hmac.new(secret, json.dumps(parts, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()


def words_for(secret: bytes, shower: bytes, scanner: bytes) -> list[str]:
    """The four words both screens show."""
    digest = hmac.new(secret, b"words\0" + hashlib.sha256(shower).digest() + hashlib.sha256(scanner).digest(), hashlib.sha256).digest()
    return [WORDS[b % len(WORDS)] for b in digest[:4]]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _read_code(code: str) -> tuple[str, str, float, bytes, str, int]:
    """``(the shower's role, its key's fingerprint, when the code ends, the secret, host, port)`` of a code."""
    try:
        if not code.startswith(SCHEME) or len(code) > CODE_MOST:
            raise ValueError
        read = {k: v[0] for k, v in urllib.parse.parse_qs(code[len(SCHEME):], strict_parsing=True).items()}
        shower, pin, expires = read["r"], read["f"], float(read["e"])
        secret = base64.urlsafe_b64decode(read["s"] + "==")
        where = urllib.parse.urlsplit("//" + read["a"])
        host, port = where.hostname or "", int(where.port or 0)
        if read["v"] != "1" or shower not in ROLES or len(secret) != 16 or not port:
            raise ValueError
    except (ValueError, KeyError):
        raise JoinError("that is not a Poolhouse guest code") from None
    return shower, pin, expires, secret, host, port


@dataclass
class _Session:
    id: str
    role: str                    # what this computer is: "host" or "guest"
    secret: bytes
    expires: float
    state: str = "waiting"       # waiting, confirm, done, refused, expired
    words: list[str] = field(default_factory=list)
    name: str = ""
    der: bytes = b""
    at: tuple[str, ...] = ()
    error: str = ""
    wrong: int = 0
    server: Any = None

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "state": self.state, "words": list(self.words), "name": self.name, "role": self.role,
                "expires": int(self.expires), "error": self.error}


class Joins:
    """The joins this process has under way. ``at`` is where this computer, as a host, answers its guests; ``listen`` is the
    private address a shown code is answered on."""

    def __init__(self, store: Store | None = None) -> None:
        self.store = store or Store()
        self._held: dict[str, _Session] = {}
        self._lock = threading.Lock()

    def _new(self, role: str, secret: bytes, expires: float) -> _Session:
        if role not in ROLES:
            raise JoinError("a joining is made as a host or as a guest")
        session = _Session(uuid.uuid4().hex[:12], role, secret, expires)
        with self._lock:
            self._held[session.id] = session
        return session

    def _current(self, session_id: str) -> _Session | None:
        session = self._held.get(session_id)
        if session is not None and session.state in ("waiting", "confirm") and time.time() >= session.expires:
            session.state = "expired"
            self._stop(session)
        return session

    @staticmethod
    def _stop(session: _Session) -> None:
        server, session.server = session.server, None
        if server is not None:
            threading.Thread(target=server.close, daemon=True).start()

    def status(self, session_id: str) -> dict[str, Any]:
        session = self._current(session_id)
        if session is None and (row := self.store.held(session_id)):
            return {"id": session_id, "state": "confirm", "words": row["words"], "name": row["name"], "role": row["role"],
                    "expires": int(row["expires"]), "error": ""}
        if session is None:
            return {"id": session_id, "state": "expired", "words": [], "name": "", "role": "", "expires": 0, "error": ""}
        return session.public()

    def show(self, role: str, name: str, *, listen: str, at: tuple[str, ...] = (), ttl_s: int = TTL_S) -> dict[str, Any]:
        """A new code for the other side to read, and the listener that answers it."""
        ttl = max(30, min(int(ttl_s), MOST_TTL_S))
        session = self._new(role, secrets.token_bytes(16), time.time() + ttl)
        identity = self.store.identity()
        name = clean_name(name)[:NAME_IN_CODE]
        joins = self

        class Handler(wire.Quiet):
            def do_GET(self) -> None:
                wire.reply(self, 404, {"error": "no such thing here"})

            def do_POST(self) -> None:
                body = wire.body(self, 4096)
                if self.path != "/join" or body is None:
                    return wire.reply(self, 404, {"error": "no such thing here"})
                code, said = joins._answer(session, identity.der, name, at, body)
                wire.reply(self, code, said)
                if code == 200:
                    joins._stop(session)

        session.server = wire.Server(listen, 0, Handler, lambda: tls.server_context(identity))
        session.server.start()
        timer = threading.Timer(ttl + 1, lambda: self._current(session.id))
        timer.daemon = True
        timer.start()
        where = f"[{listen}]" if ":" in listen else listen
        code = SCHEME + urllib.parse.urlencode(
            {"v": 1, "r": role, "f": fingerprint(identity.der), "s": _b64(session.secret), "e": int(session.expires),
             "a": f"{where}:{session.server.port}", "n": name}, safe=":[]")
        if len(code) > CODE_MOST:
            self._stop(session)
            raise JoinError("the code would be too long to scan; use a shorter name")
        return {**session.public(), "code": code}

    def _answer(self, session: _Session, own: bytes, name: str, at: tuple[str, ...], body: bytes) -> tuple[int, dict]:
        with self._lock:
            if self._current(session.id) is None or session.state != "waiting":
                return 409, {"error": "this code has been used or is no longer good"}
            try:
                said = json.loads(body)
                der = base64.b64decode(said["cert"], validate=True)
                their_name, their_at = str(said["name"]), [str(a) for a in said["at"]][:4]
                right = hmac.compare_digest(
                    str(said["mac"]), _mac(session.secret, "ask", session.role, fingerprint(own), said["cert"], their_name, their_at))
            except (ValueError, KeyError, TypeError):
                right = False
            if not right:
                session.wrong += 1
                if session.wrong >= MOST_WRONG:
                    session.state, session.error = "refused", "too many wrong answers; show a new code"
                    self._stop(session)
                return 403, {"error": "that answer does not fit this code"}
            session.der, session.name = der, clean_name(their_name)
            session.at = tuple(their_at) if ROLES[session.role] == "host" else ()
            session.words, session.state = words_for(session.secret, own, der), "confirm"
            self._hold(session)
            mine = list(at) if session.role == "host" else []
            return 200, {"name": name, "at": mine, "mac": _mac(session.secret, "ack", said["cert"], name, mine)}

    def scan(self, code: str, name: str, *, at: tuple[str, ...] = ()) -> dict[str, Any]:
        """Answer the code another computer shows. ``at`` is where this one answers guests, when it is the host."""
        shower, pin, expires, secret, host, port = _read_code(code)
        if time.time() >= expires:
            raise JoinError("that code is no longer good; ask for a new one")
        role = ROLES[shower]
        session = self._new(role, secret, min(expires, time.time() + MOST_TTL_S))
        identity = self.store.identity()
        cert = base64.b64encode(identity.der).decode()
        name = clean_name(name)
        mine = list(at) if role == "host" else []
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE   # the certificate is held to the code's fingerprint below, before anything is sent
        connection = http.client.HTTPSConnection(wire.private_address(host), port, timeout=20, context=context)
        try:
            connection.connect()
            der = connection.sock.getpeercert(binary_form=True) or b""
            if not hmac.compare_digest(fingerprint(der), pin):
                raise JoinError("the computer at that address is not the one that showed the code")
            body = {"cert": cert, "name": name, "at": mine, "mac": _mac(secret, "ask", shower, pin, cert, name, mine)}
            connection.request("POST", "/join", json.dumps(body), {"Content-Type": "application/json"})
            response = connection.getresponse()
            said = json.loads(response.read(4096))
            if response.status != 200:
                raise JoinError(str(said.get("error") or "the other computer refused")[:200])
            their_name, their_at = str(said["name"]), [str(a) for a in said["at"]][:4]
            if not hmac.compare_digest(str(said["mac"]), _mac(secret, "ack", cert, their_name, their_at)):
                raise JoinError("the other computer did not prove it showed the code")
        except JoinError as why:
            session.state, session.error = "refused", str(why)
            raise
        except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError) as why:
            session.state = "refused"
            session.error = f"the other computer could not be reached ({type(why).__name__}); is it on, and on your network?"
            raise JoinError(session.error) from None
        finally:
            with contextlib.suppress(OSError):
                connection.close()
        session.der, session.name = der, clean_name(their_name)
        session.at = tuple(their_at) if shower == "host" else ()
        session.words, session.state = words_for(secret, der, identity.der), "confirm"
        self._hold(session)
        return session.public()

    def _hold(self, session: _Session) -> None:
        self.store.hold(session.id, {"role": session.role, "name": session.name, "cert": base64.b64encode(session.der).decode(),
                                     "at": list(session.at), "words": session.words, "expires": session.expires})

    def confirm(self, session_id: str, match: bool) -> dict[str, Any]:
        """The person here says whether the words are the same on both screens, from this process or a later one. Saying it twice
        changes nothing."""
        with self._lock:
            row = self.store.held(session_id, take=True)
            if row is None:
                return self.status(session_id)
            session = self._current(session_id)
            if match is True:
                self.store.add(ROLES[row["role"]], row["name"], base64.b64decode(row["cert"]), tuple(row["at"]))
            state = "done" if match is True else "refused"
            error = "" if match is True else "the words did not match; nothing was allowed"
            if session is not None:
                session.state, session.error = state, error
                self._stop(session)
        return {"id": session_id, "state": state, "error": error, "words": row["words"], "name": row["name"], "role": row["role"],
                "expires": int(row["expires"])}

    def close(self) -> None:
        for session in list(self._held.values()):
            self._stop(session)
