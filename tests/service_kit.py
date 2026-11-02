"""Real node processes whose pool authorizes compute services: the one place tests get node-bound HTTPS services.

A test asks for ``one_node``, ``two_nodes`` or ``three_nodes``: that many of the runner's real node binary, joined into one pool. Nothing about
a service binding is stood in for. The kit makes a TLS service identity per node, binds each to its node at the endpoint the test gives it, with the
person's approval of that exact binding (`service_draft`, an approval of the resource, `service_bind`), and has the nodes sync. The first node lists every service; each other node lists its own and the first's, because
nodes learn a service only from the node that owns it and only a joiner and its host reach each other.

This process is the first node's device: ``Pool()`` reads node 0 and ``tls.local()`` is its service identity. Another node's side is reached
with ``Pool(nodes.devices[i].state)`` and ``nodes.ids[i]``, the way a daemon on that node would hold them. The node states are under /tmp, which on
macOS is a link, so a service identity lives under the test's own folder instead.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from pathlib import Path

import pytest
from testfarm_kit import (  # noqa: F401
    BOARD,
    Device,
    approve,
    devices,
    enter,
    found,
    join,
    node_binary,
    skip_unless_possible,
    until,
)

from poolhouse import node_health
from poolhouse.fleet import service_pool, tls

BINDINGS = itertools.count(1)
"""Names the session that carries each binding."""


@dataclass
class Nodes:
    """Joined nodes, each with the TLS identity of the compute service it will bind."""

    devices: list[Device]
    ids: list[tls.Identity]

    @property
    def a(self) -> Device:
        return self.devices[0]

    @property
    def ida(self) -> tls.Identity:
        return self.ids[0]

    @property
    def b(self) -> Device:
        return self.devices[1]

    @property
    def idb(self) -> tls.Identity:
        return self.ids[1]

    def bind(self, device: Device, identity: tls.Identity, endpoint: str) -> dict:
        """Bind ``identity``'s certificate at ``endpoint`` on ``device``'s node, behind the person's approval of that exact binding."""
        params = {"cert": identity.der.hex(), "endpoint": endpoint}
        # A session may make only so many requests; each binding is made by a session of its own.
        got = device.client.call("register", BOARD, "", model="claude-sonnet-5-5", harness="claude-code",
                                 session=f"{device.name}-bind-{next(BINDINGS)}")
        asking = Device(device.root, device.state, device.client, got["token"], got["name"], device.person)
        draft = asking.call("service_draft", token=asking.token, **params)
        approve(asking, "service_bind", narrowed={"resource": draft["resource"], "uses": 1})
        return asking.call("service_bind", token=asking.token, **params)

    def serve(self, *ports: int, host: str = "127.0.0.1") -> None:
        """Bind each node's service at its port on ``host`` and wait until every node lists them all."""
        for device, identity, port in zip(self.devices, self.ids, ports, strict=True):
            self.bind(device, identity, f"https://{host}:{port}")
        for number, device in enumerate(self.devices):
            until("the services on " + device.name, 30, lambda device=device, number=number: self._synced(device, number))

    def _synced(self, device: Device, number: int) -> bool:
        """The first node lists every service; each other node lists its own and the first's, because it reaches no other node."""
        device.call("sync_now", token=device.token)
        listed = {row.fingerprint for row in service_pool.Pool(device.state).services()}
        wanted = self.ids if number == 0 else [self.ids[0], self.ids[number]]
        return {identity.fingerprint for identity in wanted} <= listed

    def fingerprint(self, device: Device) -> str:
        """The device fingerprint the pool names ``device`` by."""
        return node_health.call(Path(device.state), "pool_status")["fingerprint"]

    def pool_id(self) -> str:
        return node_health.call(Path(self.a.state), "pool_status")["pool"]


def build(node_binary, tmp_path, monkeypatch, count: int):  # noqa: F811
    skip_unless_possible()
    names = [chr(ord("a") + number) for number in range(count)]
    with devices(node_binary, *names) as made:
        if count == 1:
            found(made[0])
        else:
            join(made[0], made[1])
            for later in made[2:]:
                enter(made[0], later)
        ids = [tls.identity(tmp_path / f"service-{name}", name) for name in names]
        monkeypatch.setattr(node_health, "default_state", lambda: made[0].state)  # this process is the first node's device; the tools and the fleet both read it here
        monkeypatch.setattr(tls, "local", lambda: ids[0])
        yield Nodes(made, ids)


def shared(node_binary, tmp_path_factory, count: int, *ports: int):  # noqa: F811
    """Nodes built once for a module of tests that only read the pool, with their services bound at ``ports``."""
    patches = pytest.MonkeyPatch()
    try:
        for nodes in build(node_binary, tmp_path_factory.mktemp("services"), patches, count):
            nodes.serve(*ports)
            yield nodes
    finally:
        patches.undo()


@pytest.fixture(scope="module")
def studio_node(node_binary, tmp_path_factory):  # noqa: F811
    """One node in a pool of its own for a module of Studio tests; a test binds the service identity it serves with."""
    patches = pytest.MonkeyPatch()
    try:
        yield from build(node_binary, tmp_path_factory.mktemp("services"), patches, 1)
    finally:
        patches.undo()


@pytest.fixture
def one_node(node_binary, tmp_path, monkeypatch):  # noqa: F811
    yield from build(node_binary, tmp_path, monkeypatch, 1)


@pytest.fixture
def two_nodes(node_binary, tmp_path, monkeypatch):  # noqa: F811
    yield from build(node_binary, tmp_path, monkeypatch, 2)


@pytest.fixture
def three_nodes(node_binary, tmp_path, monkeypatch):  # noqa: F811
    yield from build(node_binary, tmp_path, monkeypatch, 3)


def pool_id(device: Device) -> str:
    return node_health.call(Path(device.state), "pool_status")["pool"]
