"""Real nodes that stand for two or more computers of one person, started with the flags a computer's own node gets.

The device's node is started with ``--lan`` alone (`node_pool.network_args`). One machine cannot hold two nodes on the same LAN ports, so each
node here listens on a loopback address of its own and is given no other flag: in particular no sync interval, which the node chooses itself.
Each computer has its own state root and its own keystore (a file standing in for the operating system's, so the node's key is the keystore's
as on a person's computer); both are this process's while a call is made for it (`Computer.use`).
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import keyring
import node_orphans
import pytest
from node_kit import SRC, STRIPPED
from onboard_support import FileKeyring

from poolhouse import (
    keystore,
    node_binary as node_binary_module,
    node_health,
    node_launch,
    node_supervise,
)
from poolhouse.board.client import Client

BOARD = "demo"
HERE = Path(__file__).resolve().parent


@dataclass
class Computer:
    """One computer: its state root, its keystore's file and its running node."""

    root: Path
    state: Path
    port: int
    monkeypatch: pytest.MonkeyPatch

    def use(self) -> Computer:
        """Make this computer the one `ph.pool` and the page speak to."""
        _home(self.monkeypatch, self.root)
        return self

    def status(self) -> dict:
        return node_health.call(self.state, "pool_status")

    def names(self) -> set[str]:
        """The names of the active members of this computer's pool, as its node lists them."""
        return {m["name"] for m in self.status()["members"] if m["status"] == "active"}

    def session(self, native: str) -> tuple[Client, str]:
        """A session on this computer's board, as an agent there registers."""
        client = Client(self.state)
        return client, client.call("register", BOARD, "", model="claude-sonnet-5-5", harness="claude-code", session=native)["token"]


def _home(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setenv("POOLHOUSE_HOME", str(root))
    monkeypatch.setenv("POOLHOUSE_TEST_KEYRING", str(root / "keyring.json"))


@contextlib.contextmanager
def computers(binary: Path, monkeypatch: pytest.MonkeyPatch, *hosts: str) -> Iterator[list[Computer]]:
    """One running computer per listening host (``127.0.0.1`` or ``[::1]``), each stopped and removed when the block ends."""
    for name in STRIPPED:
        monkeypatch.delenv(name, raising=False)
    for name, value in {"POOLHOUSE_BOARD": BOARD, "PYTHON_KEYRING_BACKEND": "onboard_support.FileKeyring", "PYTHONPATH": f"{SRC}:{HERE}"}.items():
        monkeypatch.setenv(name, value)
    made: list[Computer] = []
    roots: list[Path] = []
    before = keyring.get_keyring()
    keyring.set_keyring(FileKeyring())
    try:
        for host in hosts:
            root = Path(tempfile.mkdtemp(prefix="ml", dir="/tmp")).resolve()
            roots.append(root)
            state = root / "node"
            state.mkdir(mode=0o700)
            node_orphans.claim(root)
            _home(monkeypatch, root)
            keystore.Keystore(wires=keystore.Wires(interactive=lambda: True)).provision()  # as after a computer's first run
            node_supervise.point(state, binary, node_binary_module.sha256(binary))
            node_launch.ensure_node(state, extra=["--listen", f"{host}:0"])
            said = node_health.call(state, "pool_status")
            assert said["device_key"]["custody"] == "keystore", said["device_key"]
            made.append(Computer(root, state, int(said["listen"].rsplit(":", 1)[1]), monkeypatch))
        yield made
    finally:
        for root in roots:
            _home(monkeypatch, root)
            node_launch.stop_node(root / "node")
            shutil.rmtree(root, ignore_errors=True)
        keyring.set_keyring(before)
