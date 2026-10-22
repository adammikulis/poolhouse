"""Every device a test run may go to, whichever way it is reached, and one object that sends to any of them.

A device is either another device of this node's pool (`client.Shards`, which the node carries and its person allowed) or another
member of a pool in dev mode (`dev.DevShards`, which needs no step on either device). A device listed both ways is asked by the node
first and by the pool in dev mode when the node's answer is no.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import dev
from .client import ShardError, Shards, pool_devices

__all__ = ["Farm", "open_farm"]

NODE = "node"


class Farm:
    """The devices of a run and the calls to them, by the fingerprint each row carries."""

    def __init__(self, rows: list[dict], node: Callable[[], Shards], pool: dev.DevShards) -> None:
        self.devices = rows
        self._node, self._pool, self._shards = node, pool, None
        self._routes: dict[str, list[tuple[str, str]]] = {row["fingerprint"]: [(row["via"], row["fingerprint"])] for row in rows}
        for row in rows:
            for other in row.get("also", []):
                self._routes[row["fingerprint"]].append((other["via"], other["fingerprint"]))
        self._chosen: dict[str, tuple[str, str]] = {}

    def _transport(self, via: str) -> Any:
        if via == NODE:
            self._shards = self._shards or self._node()
            return self._shards
        return self._pool

    @property
    def session(self) -> Any:
        """The registered session on this device's node, through which a result is posted to the board."""
        return self._transport(NODE).session

    def _route(self, device: str) -> tuple[Any, str]:
        via, fingerprint = self._chosen.get(device) or self._routes[device][0]
        return self._transport(via), fingerprint

    def capability(self, device: str) -> dict:
        """What ``device`` says about taking shards, from the first way of reaching it that says yes (else the last answer)."""
        caps: dict = {"accepts": False, "reason": "no way of reaching it"}
        for via, fingerprint in self._routes[device]:
            try:
                caps = self._transport(via).capability(fingerprint)
            except ShardError as exc:
                caps = {"accepts": False, "reason": f"did not answer: {exc}"}
            if caps["accepts"]:
                self._chosen[device] = (via, fingerprint)
                break
        return caps

    def send(self, device: str, root: Path, tier: str, files: list[str], timeout_s: int) -> str:
        """Pack the tree at ``root``, send it to ``device`` and start the run; the shard id."""
        transport, fingerprint = self._route(device)
        return transport.send(fingerprint, root, tier, files, timeout_s)

    def wait(self, device: str, shard: str, **kwargs: Any) -> dict:
        """The result of the shard, or ShardError when it failed, was cancelled, or its device went away."""
        transport, fingerprint = self._route(device)
        return transport.wait(fingerprint, shard, **kwargs)

    def cancel(self, device: str, shard: str) -> dict:
        """Stop a shard and everything it started."""
        transport, fingerprint = self._route(device)
        return transport.cancel(fingerprint, shard)


def merged(node_rows: list[dict], pool_rows: list[dict]) -> list[dict]:
    """The rows of both kinds; a device of the same name in both is one row that remembers the other way to reach it."""
    pooled = {row["name"]: row for row in pool_rows}
    rows = [{**row, "also": [pooled.pop(row["name"])] if row["name"] in pooled else []} for row in node_rows]
    return rows + [{**row, "also": []} for row in pooled.values()]


def open_farm(session: Callable[[], Shards] | None = None) -> Farm:
    """The farm of this device: its node's pool and its pool in dev mode; ShardError when neither has another device and the node is down."""
    problem = ""
    try:
        node_rows = pool_devices()
    except ShardError as exc:
        node_rows, problem = [], str(exc)
    pool = dev.DevShards()
    pool_rows = [{"name": m.beacon.name, "fingerprint": fp, "addr": m.base_url, "connected": True, "reach": "direct",
                  "reach_hint": "", "via": dev.VIA} for fp, m in pool.members.items() if m.beacon]
    rows = merged(node_rows, pool_rows)
    if not rows and problem:
        raise ShardError(problem)
    return Farm(rows, session or Shards, pool)
