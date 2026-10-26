"""Current service and owner request guards for the compute daemon."""

from __future__ import annotations

import hmac

from poolhouse import sealing
from poolhouse.macauth import parts

from . import callers


class ServiceGuard:
    """Authenticate node-bound member requests and independent owner requests."""

    _member = ""
    _group = ""
    _service_key = ""
    _service_generation = ""
    _owner_service = False

    def _service_current(self) -> None:
        if self._member:
            current = self._roster.authorize(self._member)
            if current.pool != self._group or current.generation != self._service_generation:
                raise PermissionError("The pool compute binding changed during the request.")
            if self._owner_service:
                if current != self._roster.own(self._screen.identity()) or self._service_key not in self._service_secrets():
                    raise PermissionError("Local compute ownership changed during the request.")
                return
            if not hmac.compare_digest(self._roster.secret(self._member, self._screen.identity()), self._service_key):
                raise PermissionError("The compute service generation changed during the request.")

    def _guard(self, body: bytes | None = None) -> bool:
        """Whether this request is signed by a secret this machine answers to, and when it
        came over TLS from another machine, by a device that is a member; the answer is
        sent when it is not. ``body`` is what the request carried."""
        seen = self._screen.check(self, body)
        verdict, self._member, self._group = seen.verdict, seen.member, ""
        if seen.refusal is not None:
            self.close_connection = seen.kind == callers.STRANGER
            self._send(seen.refusal[0], {"error": seen.refusal[1]})
            return False
        if verdict.ok:
            self._service_key = verdict.secret if self._member else ""
            self._service_generation = seen.generation
            self._owner_service = seen.owner_service
            self._group = seen.group
            mode = self.headers.get(sealing.HEADER, "")
            self._opening = (sealing.box_key(verdict.secret), verdict, mode == "2",
                             self.headers) if mode in ("1", "2") else None
            return True
        if verdict.locked:
            self._send(429, {"error": verdict.reason}, headers={"Retry-After": "60"})
        else:
            self._send(401, {"error": verdict.reason})
        return False

    def _unsealed(self, body: bytes | None) -> bytes | None:
        """What a signed request's body says, or None once a refusal has been sent: a body
        is sealed under the key derived from the secret that signed it."""
        if not body:
            return body
        opening = self._sealing()
        if opening is None:
            self._send(400, {"error": "a request body is sent sealed"})
            return None
        key, verdict, _ = opening
        host, target = parts(f"//{self.headers.get('Host', '')}{self.path}")
        try:
            return sealing.open_(key, body, sealing.request_data(
                self.command, target, host, verdict.at, verdict.nonce))
        except sealing.SealError:
            self._send(400, {"error": "the body did not authenticate"})
            return None

