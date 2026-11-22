"""One generation at a time per accelerator device, across every process on this machine.

A request to a loopback port on the lease registry takes a lease of the device from the node's lease table (`poolhouse.leasetable`):
the GPU, or the request line of a device that has none. The node orders the requests, and drops the one whose process ended, so a
crashed holder frees the line. `poolhouse.http` calls `turn` for every generation or embedding request. ``POOLHOUSE_PARALLEL_REQUESTS=1``
or `parallel` lets requests run at once and logs that it did.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import threading
import time
import urllib.parse
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from poolhouse import home, leasetable
from poolhouse.node_health import Refused

__all__ = ["DEFAULT_WAIT_S", "ENV_PARALLEL", "ENV_WAIT", "QueueTimeout", "device_of", "is_generation",
           "parallel", "snapshot", "turn"]

logger = logging.getLogger(__name__)

ENV_WAIT = "POOLHOUSE_REQUEST_WAIT_S"
"""Seconds a request waits for its turn before it gives up."""

ENV_PARALLEL = "POOLHOUSE_PARALLEL_REQUESTS"
"""Set to ``1`` to let requests to one device run at the same time."""

DEFAULT_WAIT_S = 600.0
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
GENERATING = ("/chat/completions", "/completions", "/completion", "/embeddings",
              "/embedding", "/infill", "/v1/messages")
"""Endpoints that run the model; the last path segments a request is queued on."""

POLL_S = 0.25
LEASE_S = 180
RENEW_S = 60.0
"""A turn's lease ends with its process, or `LEASE_S` after it was last renewed, which the turn does every `RENEW_S` while it runs."""
_held = threading.local()
_parallel: contextvars.ContextVar[str] = contextvars.ContextVar("poolhouse_parallel", default="")
_told: set[str] = set()


class QueueTimeout(TimeoutError):
    """A request waited its whole allowance and was never at the front of the line."""


def is_generation(url: str) -> bool:
    """Whether ``url`` is an endpoint that runs the model."""
    path = urllib.parse.urlsplit(url).path.rstrip("/")
    return path.endswith(GENERATING)


def _registry() -> dict[str, Any]:
    try:
        parsed = json.loads(home.moved("servers.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def device_of(url: str) -> str:
    """The device a request to ``url`` queues in, or "" when it is not queued: the host is
    not this machine, or no server in the lease registry listens on that port."""
    parts = urllib.parse.urlsplit(url)
    if (parts.hostname or "").lower() not in LOOPBACK or not parts.port:
        return ""
    for key, entry in _registry().items():
        if not isinstance(entry, dict):
            continue
        try:
            port = int(entry.get("port", key))
        except (TypeError, ValueError):
            continue
        if port == parts.port:
            return str(entry.get("device") or "gpu")
    return ""


def _wait_s(wait_s: float | None) -> float:
    if wait_s is not None:
        return wait_s
    try:
        return float(os.environ.get(ENV_WAIT) or DEFAULT_WAIT_S)
    except ValueError:
        return DEFAULT_WAIT_S


def _allowed_parallel() -> str:
    """Who asked for parallel requests, or "" when nobody did."""
    named = _parallel.get()
    if named:
        return named
    return f"{ENV_PARALLEL}=1" if os.environ.get(ENV_PARALLEL, "").strip() in ("1", "true", "yes") else ""


@contextmanager
def parallel(reason: str) -> Iterator[None]:
    """Let requests sent inside the block run beside other requests to the same device.

    ``reason`` names the caller and is logged the first time it is used.
    """
    token = _parallel.set(reason or "parallel")
    try:
        yield
    finally:
        _parallel.reset(token)


def _resource(device: str) -> dict[str, Any]:
    return leasetable.gpu() if device == "gpu" else leasetable.line(device)


def _front(table: leasetable.Table, device: str) -> str:
    """Who holds the device now, in words, for a request that gave up waiting."""
    try:
        rows = _rows(table.listing()["leases"]).get(device, [])
    except (OSError, ValueError, KeyError):
        return "another process"
    held = next((row for row in rows if row["running"]), None)
    return f"pid {held['pid']} for {time.time() - held['since']:.1f}s" if held else "another process"


def _asked(table: leasetable.Table, device: str, tag: str) -> dict[str, Any]:
    try:
        return table.acquire([_resource(device)], leasetable.Terms(tag=tag, pid=os.getpid(), ttl_s=LEASE_S, quiet=True))
    except Refused as why:
        raise QueueTimeout(f"the {device} device takes no request now: {why}") from why
    except OSError as why:
        raise QueueTimeout(f"the lease table of this device did not answer, so the {device} device cannot be queued on: {why}") from why


def _await(table: leasetable.Table, device: str, tag: str, wait_s: float | None, cancelled: Any) -> str:
    """The id of the lease of ``device`` once it is held."""
    got = _asked(table, device, tag)
    allowance, began, held = _wait_s(wait_s), time.monotonic(), False
    try:
        while got["state"] != "held":
            if cancelled is not None and cancelled():
                raise QueueTimeout("request cancelled while waiting for its turn")
            waited = time.monotonic() - began
            if waited >= allowance:
                raise QueueTimeout(
                    f"waited {waited:.1f}s for its turn on the {device} device; the front is {_front(table, device)}. "
                    f"{ENV_WAIT} sets how long to wait; {ENV_PARALLEL}=1 sends in parallel")
            try:
                got = table.wait(got["id"], min(POLL_S, allowance - waited))
            except Refused:
                got = _asked(table, device, tag)
        held = True
        return str(got["id"])
    finally:
        if not held:
            with contextlib.suppress(OSError, Refused):
                table.release(got["id"])


@contextmanager
def turn(url: str, *, wait_s: float | None = None, cancelled=None) -> Iterator[str | None]:
    """Hold the front of the line for the device ``url`` is in while the block runs.

    A URL that is not queued, a thread that already holds the device, and a caller inside
    `parallel` run at once. Raises `QueueTimeout` after ``wait_s`` (else ``POOLHOUSE_REQUEST_WAIT_S``,
    else ten minutes) naming the request that held the front.

    Only a newly acquired turn yields its actual ticket. Bypasses yield None; the
    ticket is trusted local ownership evidence, never a public authorization grant.
    """
    device = device_of(url)
    if not device:
        yield
        return
    reason = _allowed_parallel()
    if reason:
        if reason not in _told:
            _told.add(reason)
            logger.warning("requests to the %s device run in parallel (%s); they are not queued",
                           device, reason)
        yield
        return
    mine: set[str] = _held.__dict__.setdefault("devices", set())
    if device in mine:
        yield
        return

    table = leasetable.default()
    lease = _await(table, device, f"turn.{os.getpid()}.{uuid.uuid4().hex[:8]}", wait_s, cancelled)
    mine.add(device)
    done = threading.Event()
    threading.Thread(target=_kept, args=(table, lease, done), daemon=True).start()
    try:
        yield lease
    finally:
        done.set()
        mine.discard(device)
        _given_back(table, lease, device)


def _kept(table: leasetable.Table, lease: str, done: threading.Event) -> None:
    """Renew a turn's lease every `RENEW_S` until the turn is over."""
    while not done.wait(RENEW_S):
        with contextlib.suppress(OSError, Refused):
            table.renew(lease, ttl_s=LEASE_S)


def _given_back(table: leasetable.Table, lease: str, device: str) -> None:
    """Give a turn's lease back, trying three times; one that is not given back ends `LEASE_S` after its last renewal."""
    failure: Exception | None = None
    for pause in (0.0, 0.2, 0.4):
        time.sleep(pause)
        try:
            table.release(lease)
            return
        except (OSError, Refused) as why:
            failure = why
    logger.warning("the turn on the %s device was not given back (%s); it ends within %s seconds", device, failure, LEASE_S)


def _rows(leases: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """The turns in a lease listing by device, the one that holds the device first and the rest in line order."""
    out: dict[str, list[dict[str, Any]]] = {}
    for one in leases:
        if not (one.get("quiet") and str(one.get("tag", "")).startswith("turn.")):
            continue
        named = one["resources"][0] if one.get("resources") else {}
        device = "gpu" if named.get("type") == "gpu" else str(named.get("name", "")).removeprefix("line:")
        out.setdefault(device, []).append({"pid": one.get("pid", 0), "ticket": one["id"], "running": one["state"] == "held",
                                           "since": (one.get("granted_ms") or one.get("since_ms") or 0) / 1000,
                                           "position": one.get("position", 0)})
    for rows in out.values():
        rows.sort(key=lambda row: (not row["running"], row["position"]))
    return out


def snapshot() -> dict[str, list[dict[str, Any]]]:
    """Each device's line, oldest first; the first entry with ``running`` true holds the turn. Nothing when the node cannot be asked."""
    try:
        return _rows(leasetable.default().listing()["leases"])
    except (OSError, ValueError, KeyError):
        return {}
