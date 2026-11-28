"""A real broker over a test's own node, and the broker's own methods in the shape of `broker_wire` for one process of it.

Name the plugin beside ``serve_node`` and use the fixture ``device_broker``. PLUMBING ONLY: a server it starts is the canned
protocol server (`poolhouse.testing.canned`), which marks every reply `[CANNED RESPONSE, NOT A MODEL]`; no model runs.
"""

from __future__ import annotations

import subprocess
import sys

import psutil
import pytest

from poolhouse.serve import LlamaServerBackend, ServerManager
from poolhouse.serve.broker import Broker
from poolhouse.testing.canned import canned_binary

SLEEP = "import time;time.sleep(120)"


@pytest.fixture
def device_broker(tmp_path, serve_leases):
    """``(broker, process)``: ``process(code)`` starts a Python process of this test, ended with it."""
    broker = Broker(ServerManager(LlamaServerBackend(binary=canned_binary(tmp_path)),
                                  state_file=tmp_path / "servers.json"), scan=lambda: [])
    children: list[subprocess.Popen] = []

    def process(code: str = SLEEP) -> subprocess.Popen:
        child = subprocess.Popen([sys.executable, "-I", "-c", code], stdout=subprocess.PIPE, text=True)
        children.append(child)
        return child

    yield broker, process
    for held in list(broker.servers.values()):
        broker.stop(held.port, force=True)
    for child in children:
        for under in _below(child.pid):
            under.kill()
        child.kill()
        child.wait()


def _below(pid: int) -> list:
    try:
        return psutil.Process(pid).children(recursive=True)
    except psutil.Error:
        return []


class Wire:
    """The broker's own methods in the shape of `broker_wire`, for the process ``pid``."""

    def __init__(self, broker: Broker, pid: int) -> None:
        self.broker, self.pid = broker, pid

    def claim(self, name, info, *, timeout=0.0):
        return self.broker.claim(name, self.pid, info, timeout=timeout)

    def unclaim(self, name):
        return self.broker.unclaim(name, self.pid)

    def status(self):
        return self.broker.snapshot()

    def stop(self, port):
        return self.broker.stop(port)

    def replace_stale(self):
        return "current"
