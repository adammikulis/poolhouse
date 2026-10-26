"""Who is on the other end of a connection to the daemon, and whether that is enough.

A request that arrives over TLS from another machine carries the certificate its sender showed
in the handshake. It is answered only when that certificate is a member's (`pool_roster`) and,
when the request is signed with a pool's secret, a member of that same pool. A sender that
showed no certificate (a phone, a browser) may use only the credential of its own device.
The check runs on every request, so a device put out of the pool is refused at its next one
even on a connection it opened before.
"""

from __future__ import annotations

import hashlib
import ipaddress
import ssl
from collections.abc import Callable, Iterable
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from poolhouse.macauth import Authenticator, Verdict

from . import tls
from .service_pool import Pool

__all__ = ["ANONYMOUS", "LOCAL", "MEMBER", "STRANGER", "Screen", "Screened", "identify", "pool_of"]

LOCAL, ANONYMOUS, MEMBER, STRANGER = "local", "anonymous", "member", "stranger"


def identify(connection: Any, pool: Pool) -> tuple[str, str]:
    """``(kind, fingerprint)``: ``local`` for a plain connection from this machine; over TLS,
    ``anonymous`` for a caller with no certificate, ``member`` for one showing a current member's,
    ``stranger`` for any other (a device put out since its handshake)."""
    if not isinstance(connection, ssl.SSLSocket):
        return LOCAL, ""
    der = connection.getpeercert(binary_form=True)
    if not der:
        return ANONYMOUS, ""
    fingerprint = hashlib.sha256(der).hexdigest()
    try:
        pool.authorize(fingerprint)
    except PermissionError:
        return STRANGER, fingerprint
    return MEMBER, fingerprint


@dataclass(frozen=True, slots=True)
class Screened:
    """What the daemon made of a request: who sent it, the verdict on its signature, and the
    status and reason to answer with when it may not be served."""

    kind: str
    member: str
    verdict: Verdict | None = None
    refusal: tuple[int, str] | None = None
    group: str = ""
    generation: str = ""
    owner_service: bool = False


class Screen:
    """The checks a request must pass, which differ by who sent it and by route."""

    def __init__(self, secrets_now: Callable[[], Iterable[str]], device_secrets: Callable[[], Iterable[str]],
                 pool: Pool, watch: Callable[[Any], Any],
                 identity: Callable[[], tls.Identity] | None = None) -> None:
        self.pool = pool
        self.identity = identity or tls.local
        self.pool_secret = watch(Authenticator(secrets_now))
        self.workspace = Authenticator(lambda: [*secrets_now(), *device_secrets()])
        self.enrolled = Authenticator(device_secrets)
        self._request_secret: ContextVar[str] = ContextVar("service_request_secret", default="")
        self.service = watch(Authenticator(lambda: [self._request_secret.get()]))

    def check(self, handler: Any, body: bytes | None) -> Screened:
        """Screen the request ``handler`` (a request handler) is serving, whose body was ``body``."""
        kind, member = identify(handler.connection, self.pool)
        workspace = handler.path.split("?")[0].startswith("/workspace/v1/")
        if kind == STRANGER:
            return Screened(kind, member, refusal=(403, "this device is not a member of the pool"))
        if kind == ANONYMOUS and not workspace:
            return Screened(kind, member, refusal=(401, "a request from another machine presents its device certificate"))
        if kind == MEMBER:
            try:
                current = self.pool.authorize(member)
                if member == self.identity().fingerprint:
                    own = self.pool.own(self.identity())
                    if (not ipaddress.ip_address(handler.client_address[0]).is_loopback
                            or handler.connection.getsockname()[1] != urlsplit(own.endpoint).port
                            or own != current):
                        raise PermissionError("Local compute ownership requires this service's loopback endpoint.")
                    verdict = self.pool_secret.check(handler.command, handler.path, handler.headers, body, handler.client_address[0])
                    return Screened(kind, member, verdict, group=own.pool, generation=own.generation, owner_service=True)
                secret = self.pool.secret(member, self.identity())
            except PermissionError:
                return Screened(STRANGER, member, refusal=(403, "current pool compute authorization is unavailable"))
            selected = self._request_secret.set(secret)
            try:
                verdict = self.service.check(handler.command, handler.path, handler.headers, body, handler.client_address[0])
            finally:
                self._request_secret.reset(selected)
            return Screened(kind, member, verdict, group=current.pool, generation=current.generation)
        checking = self.enrolled if kind == ANONYMOUS else self.workspace if workspace else self.pool_secret
        verdict = checking.check(handler.command, handler.path, handler.headers, body, handler.client_address[0])
        return Screened(kind, member, verdict)


def pool_of(secret: str, pool_key_path: Path | str | None) -> str | None:
    """Local owner credentials do not identify a pool."""
    return None
