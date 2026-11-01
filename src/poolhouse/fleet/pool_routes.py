"""``/ui/pool*`` and ``/ui/fleet``: this device's pool as its node reports it, acting on it, and the machines in it.

Every pool action here is the node's own (`poolhouse.api.pool`): the page founds a pool, makes the short code that adds a device,
joins another device's pool and removes a member by asking the node, which asks the person where only they can answer. Nothing in
this module holds a pool key, a passphrase or a membership record.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from poolhouse import errors
from poolhouse.api import pool as pool_api
from poolhouse.board.client import NodeError

from .pausing import minutes_of

__all__ = ["PoolRoutes"]

FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
POLICIES = ("secure", "open")


class _Refused(ValueError):
    """The request did not name what the action needs."""


def _text(req: dict[str, Any], key: str, *, most: int = 200, required: bool = True) -> str:
    value = req.get(key, "")
    if not isinstance(value, str) or len(value) > most or (required and not value.strip()):
        raise _Refused(f"{key} must be {'a' if required else 'an optional'} text of at most {most} characters")
    return value.strip()


def _whole(req: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = req.get(key, default)
    if type(value) is not int or not low <= value <= high:
        raise _Refused(f"{key} must be a whole number from {low} to {high}")
    return value


class PoolRoutes:
    """This device's pool, acting on it, and the machines in it."""

    def route(self) -> bool:
        if self.path == "/ui/pool" and self.method == "GET":
            return self._guarded(self._status)
        if self.path == "/ui/pool/found" and self.method == "POST":
            return self._guarded(self._found)
        if self.path == "/ui/pool/add-device" and self.method == "POST":
            return self._guarded(self._add_device)
        if self.path == "/ui/pool/join" and self.method == "POST":
            return self._guarded(self._join)
        if self.path == "/ui/pool/remove-device" and self.method == "POST":
            return self._guarded(self._remove)
        if self.path == "/ui/pool" and self.method == "DELETE":
            return self._guarded(lambda: {"left": pool_api.leave()})
        if self.path == "/ui/fleet" and self.method == "GET":
            self.send(200, self.ui.fleet())
            return True
        if self.path == "/ui/fleet/pause" and self.method == "POST":
            return self._pause_fleet()
        return super().route()

    def _guarded(self, act) -> bool:
        """Answer with what ``act`` returns, or the one sentence the node or the request gave for refusing."""
        try:
            answer = act()
        except _Refused as exc:
            self.send(400, {"error": str(exc)})
        except pool_api.Refusal as exc:
            self.send(409, {"error": str(exc), "needs": exc.needs, "where": exc.where, "then": exc.then})
        except (errors.Error, NodeError, OSError, ValueError) as exc:
            self.send(400, {"error": str(exc)})
        else:
            self.ui.refresh()
            self.send(200, answer)
        return True

    def _status(self) -> dict[str, Any]:
        """The pool as the node reports it; asks nothing of a node that is not running, and starts none."""
        if self.ui.pool(force=True) is None:
            return {"pool": None, "error": "No node answers on this device, so it has no pool to show."}
        return {"pool": asdict(pool_api.status())}

    def _found(self) -> dict[str, Any]:
        req = self.body()
        policy = req.get("policy", "secure")
        if policy not in POLICIES:
            raise _Refused("policy must be secure or open")
        said: list[str] = []
        made = pool_api.found(_text(req, "name", most=64), policy, say=said.append)
        return {"pool": asdict(made.pool), "already": made.already, "say": made.say, "asked": said}

    def _add_device(self) -> dict[str, Any]:
        return dict(pool_api.add_device(_whole(self.body(), "ttl_s", 300, 30, 3600)))

    def _join(self) -> dict[str, Any]:
        req = self.body()
        code = _text(req, "code", required=False)
        fingerprint = _text(req, "fingerprint", most=64, required=False)
        if bool(code) == bool(fingerprint) or (fingerprint and not FINGERPRINT.match(fingerprint)):
            raise _Refused("give the code the other device showed, or for an open pool its 64 hex digit fingerprint")
        return {"joined": pool_api.join(_text(req, "host"), code, port=_whole(req, "port", pool_api.DEFAULT_PORT, 1, 65535),
                                        fingerprint=fingerprint)}

    def _remove(self) -> dict[str, Any]:
        return {"removed": asdict(pool_api.remove(_text(self.body(), "device", most=128)))}

    def _pause_fleet(self) -> bool:
        req = self.body()
        span = str(req.get("for") or "")
        minutes = minutes_of(span)
        if span and minutes is None:
            self.send(400, {"error": f"{span!r} is not a length of time; "
                                     "try '2h', '90m' or '45s'"})
            return True
        self.send(200, self.ui.pause_fleet(resume=bool(req.get("resume")),
                                           minutes=minutes,
                                           reason=str(req.get("reason") or "")))
        return True
