"""A real node process on a short temporary root, and the sessions and commands that talk to it.

Import `node_binary` and `workspace_node` into a test module to use them. The state directory is
under /tmp with a short name because a Unix socket path is limited to about 100 characters, which
a pytest `tmp_path` can exceed. The node binary is built once per session with
``cargo build -p poolhouse-node``.
"""

from __future__ import annotations

import fcntl
import os
import pty
import pwd
import select
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from poolhouse import node_binary as node_binary_module, node_launch, node_supervise
from poolhouse.board import (
    client as board_client,
    credentials,
    grants as board_grants,
    person_key,
    session as board_session,
)
from poolhouse.fleet.onboard.manifest import Signer
from poolhouse.fleet.onboard.signing import SigningKeys

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")
STRIPPED = ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE",
            "POOLHOUSE_WORKSPACE_TOKEN", "POOLHOUSE_WORKSPACE_DENYLIST", "POOLHOUSE_WORKSPACE_AGENT", "POOLHOUSE_BOARD")
BOARD = "demo"
PASSPHRASE = "a passphrase for the person's test key"


def _cargo_env() -> tuple[str, dict[str, str]]:
    """The cargo binary and an environment where it finds the account's toolchain (HOME is moved in tests)."""
    account = Path(pwd.getpwuid(os.getuid()).pw_dir)
    found = shutil.which("cargo") or str(account / ".cargo" / "bin" / "cargo")
    return found, {**os.environ, "HOME": str(account)}


@pytest.fixture(scope="session")
def node_binary(tmp_path_factory) -> Path:
    """The node binary, built once for the session (and once across parallel workers), and copied to a folder of
    this session's own: another worker's build rewrites the built file, and a test reading it then finds none."""
    cargo, env = _cargo_env()
    target = ROOT / "app" / "target"
    target.mkdir(parents=True, exist_ok=True)
    with (target / ".test-build.lock").open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        done = subprocess.run([cargo, "build", "-p", "poolhouse-node"], cwd=ROOT / "app", env=env, capture_output=True,
                              text=True, check=False)
        if done.returncode:
            pytest.fail(f"cargo build -p poolhouse-node failed:\n{done.stderr[-2000:]}", pytrace=False)
        mine = tmp_path_factory.mktemp("node") / "poolhouse-node"
        shutil.copy2(target / "debug" / "poolhouse-node", mine)
    return mine


class NoKeystore:
    """A keystore that is not there: a test's key goes in a file under the passphrase of the environment, and the real
    keystore (or a test's fake one, with state a child process would then find without its master) is never asked."""

    def available(self) -> bool:
        return False


def allow(carrier: board_session.Session, person: Signer, *actions: str, to: dict | None = None, **limits: Any) -> list[str]:
    """``person`` (a test key, enrolled on the node first) grants the session ``carrier`` (or ``to``) every action of the
    registry, or just ``actions``, within ``limits`` (``resource``, ``uses``, ``lasts_ms``); the grant ids. The carrier
    only carries the request."""
    held = board_grants.listing(carrier)
    if board_grants.fingerprint(person.public) not in held.persons:
        board_grants.enrol(carrier.client, carrier.token, board_grants.enrolment(person, "a test person"))
    named = to or {"kind": "session", "name": carrier.name}
    return [board_grants.issue(carrier, person, board_grants.Want(a, named, **limits))["gid"] for a in actions or held.registry["actions"]]


@dataclass
class Member:
    """A session registered on the node's board."""

    name: str
    token: str
    parent: str = ""
    session: str = ""

    def env(self) -> dict[str, str]:
        return {"POOLHOUSE_WORKSPACE_AGENT": self.name}


@dataclass
class WorkspaceNode:
    """A node running on ``state``, a client of it, and the board the tests use."""

    state: Path
    client: board_client.Client
    binary: Path
    board: str = BOARD
    members: dict[str, Member] = field(default_factory=dict)
    person: Signer = field(default_factory=Signer.generate)
    """The key of the test person: enrolled on this node by the first `allow`."""

    def member(self, native_id: str = "main", *, model: str = "claude-sonnet-5-5", harness: str = "claude-code",
               parent: Member | None = None) -> Member:
        """Register a native session (as a subagent of ``parent``) and keep its token where the CLI finds it."""
        made = board_session.register(self.client, self.board, board_session.Native(model, harness, native_id),
                                      parent.token if parent else "")
        found = Member(made.name, made.token, made.parent, native_id)
        self.members[native_id] = found
        return found

    def adopt(self, name: str, parent: str = "") -> Member:
        """The session ``name`` a hook or command registered, with the token it kept in its private file."""
        return Member(name, credentials.load(self.state, self.board, name), parent)

    def keep_person_key(self, who: Member) -> Any:
        """A person's key kept beside this node as `grants-setup` leaves it, and enrolled on it (``who`` only carries the
        request): a command run at a terminal signs for what it needs with it, and a process an agent started cannot."""
        keys = SigningKeys(self.state / person_key.KEY_DIR, ks=NoKeystore())
        proof = board_grants.enrolment(keys, "a test person")
        held = board_grants.listing(self.session(who)).persons
        if board_grants.fingerprint(keys.public) not in held:
            board_grants.enrol(self.client, who.token, board_grants.approve(proof, self.person) if held else proof)
        return keys

    def session(self, who: Member) -> board_session.Session:
        """The typed session of ``who`` on this node."""
        return board_session.Session(self.client, self.board, who.token, who.name)

    def allow(self, who: Member, *actions: str, to: dict | None = None, **limits: Any) -> list[str]:
        """The test person grants ``who`` (or ``to``) every action of the registry, or just ``actions``; the grant ids."""
        return allow(self.session(who), self.person, *actions, to=to, **limits)

    def env(self, who: Member | None = None, extra: dict[str, str] | None = None) -> dict[str, str]:
        """The environment of a command run for ``who``: this node, this board, no marker of another agent."""
        env = {k: v for k, v in os.environ.items() if k not in STRIPPED}
        env.update({"PYTHONPATH": SRC, "POOLHOUSE_HOME": str(self.state.parent), "POOLHOUSE_BOARD": self.board,
                    "POOLHOUSE_SIGNING_PASSPHRASE": PASSPHRASE, **(who.env() if who else {}), **(extra or {})})
        return env

    def cli(self, *argv: str, who: Member | None = None, env: dict[str, str] | None = None, stdin: str | None = None,
            timeout: float = 90, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        """Run ``poolhouse-workspace`` as a subprocess, as ``who``."""
        return subprocess.run([sys.executable, "-m", "poolhouse.workspace.cli", *argv], env=self.env(who, env),
                              capture_output=True, text=True, timeout=timeout, check=False, input=stdin, cwd=cwd)

    def terminal(self, *argv: str, who: Member | None = None, timeout: float = 90, env: dict[str, str] | None = None) -> tuple[int, str, str]:
        """Run ``poolhouse-workspace`` with a pseudo-terminal on stdin and stdout, as a person at a screen would:
        the exit status, what it printed there and what it wrote to stderr."""
        leader, follower = pty.openpty()
        with tempfile.TemporaryFile("w+") as errors:
            proc = subprocess.Popen([sys.executable, "-m", "poolhouse.workspace.cli", *argv], env=self.env(who, env), stdin=follower,
                                    stdout=follower, stderr=errors, text=True)
            chunks: list[bytes] = []
            end = time.monotonic() + timeout
            while proc.poll() is None and time.monotonic() < end:  # a terminal holds about a screenful: read while it runs
                ready, _, _ = select.select([leader], [], [], 0.1)
                if ready:
                    chunks.append(os.read(leader, 65536))
            if proc.poll() is None:
                proc.kill()
                proc.wait()
                raise subprocess.TimeoutExpired(argv, timeout)
            while select.select([leader], [], [], 0.2)[0]:  # read before the slave closes: macOS drops what is unread then
                chunks.append(os.read(leader, 65536))
            errors.seek(0)
            message = errors.read()
        os.close(follower)
        os.close(leader)
        return proc.returncode, b"".join(chunks).decode(), message

    def stop(self) -> None:
        """Stop the node and its supervisor and wait for them to go."""
        node_launch.stop_node(self.state)


@pytest.fixture
def workspace_node(node_binary, monkeypatch):
    """A running node on a short temporary root; in-process clients and child commands find it by the environment."""
    root = Path(tempfile.mkdtemp(prefix="ml", dir="/tmp"))
    state = root / "node"
    state.mkdir(mode=0o700)
    monkeypatch.setenv("POOLHOUSE_HOME", str(root))
    monkeypatch.setenv("POOLHOUSE_BOARD", BOARD)
    monkeypatch.setenv("POOLHOUSE_SIGNING_PASSPHRASE", PASSPHRASE)
    for name in STRIPPED:
        if name != "POOLHOUSE_BOARD":
            monkeypatch.delenv(name, raising=False)
    node_supervise.point(state, node_binary, node_binary_module.sha256(node_binary))
    node = WorkspaceNode(state, board_client.Client(state), node_binary)
    node_launch.ensure_node(state)
    try:
        yield node
    finally:
        node.stop()
        shutil.rmtree(root, ignore_errors=True)
