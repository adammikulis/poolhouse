"""Real node processes whose pool authorizes compute services: the one place tests get node-bound HTTPS services.

A test asks for ``two_nodes`` (two joined nodes) or ``one_node`` (one node in a pool of its own). Each node is the runner's real
node binary; nothing about a service binding is stood in for. The kit makes the service certificates, binds each to its node with the
person's approval (`service_draft`, an approval of that exact resource, `service_bind`) and has the nodes sync, so the
node of device A lists B's service and B's node lists A's.

This process is device A: ``Pool()`` reads A's node and ``tls.local()`` is A's service identity. B's side is reached with
``Pool(two_nodes.b.state)`` and ``two_nodes.idb``, the way a daemon on B would hold them. The node states are under /tmp, which on macOS is a
link, so a service identity lives under the test's own folder instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from testfarm_kit import (  # noqa: F401
    Device,
    approve,
    devices,
    found,
    join,
    node_binary,
    skip_unless_possible,
    until,
)

from poolhouse import node_health
from poolhouse.fleet import service_pool, tls


@dataclass
class Nodes:
    """Two joined nodes, each with the TLS identity of the compute service it will bind."""

    a: Device
    b: Device
    ida: tls.Identity
    idb: tls.Identity

    def bind(self, device: Device, identity: tls.Identity, endpoint: str) -> dict:
        """Bind ``identity``'s certificate at ``endpoint`` on ``device``'s node, behind the person's approval of that exact binding."""
        params = {"cert": identity.der.hex(), "endpoint": endpoint}
        draft = device.call("service_draft", token=device.token, **params)
        approve(device, "service_bind", narrowed={"resource": draft["resource"], "uses": 1})
        return device.call("service_bind", token=device.token, **params)

    def serve(self, port_a: int, port_b: int) -> None:
        """Bind both services at their listening ports and wait until each node lists both."""
        self.bind(self.a, self.ida, f"https://127.0.0.1:{port_a}")
        self.bind(self.b, self.idb, f"https://127.0.0.1:{port_b}")
        for device in (self.a, self.b):
            until("both services on " + device.name, 30, lambda device=device: self._synced(device))

    def _synced(self, device: Device) -> bool:
        device.call("sync_now", token=device.token)
        return len(service_pool.Pool(device.state).services()) == 2

    def pool(self, device: Device) -> service_pool.Pool:
        return service_pool.Pool(device.state)


@pytest.fixture
def two_nodes(node_binary, tmp_path, monkeypatch):  # noqa: F811
    skip_unless_possible()
    with devices(node_binary, "a", "b") as (a, b):
        join(a, b)
        ida = tls.identity(tmp_path / "service-a", "a")
        idb = tls.identity(tmp_path / "service-b", "b")
        monkeypatch.setattr("poolhouse.node_launch.default_state", lambda: a.state)
        monkeypatch.setattr(tls, "local", lambda: ida)
        yield Nodes(a, b, ida, idb)


@pytest.fixture
def one_node(node_binary, tmp_path, monkeypatch):  # noqa: F811
    """One node in a pool of its own; this process is its device, bound to ``endpoint`` by ``one_node.bind``."""
    skip_unless_possible()
    with devices(node_binary, "a") as (a,):
        found(a)
        ida = tls.identity(tmp_path / "service-a", "a")
        monkeypatch.setattr("poolhouse.node_launch.default_state", lambda: a.state)
        monkeypatch.setattr(tls, "local", lambda: ida)
        yield Nodes(a, a, ida, ida)


def pool_id(device: Device) -> str:
    return node_health.call(Path(device.state), "pool_status")["pool"]
