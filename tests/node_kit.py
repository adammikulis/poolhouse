"""A real node process on a short temporary root, and the sessions and commands that talk to it.

Import `node_binary` and `workspace_node` into a test module to use them. The state directory is
under /tmp with a short name because a Unix socket path is limited to about 100 characters, which
a pytest `tmp_path` can exceed. The node binary is the one the runner built before the run (``scripts/testnode.py``).
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import termios
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import node_orphans
import pytest
import testnode
from pool_check import wire

from poolhouse import node_binary as node_binary_module, node_launch, node_pool, node_supervise
from poolhouse.board import (
    client as board_client,
    credentials,
    grants as board_grants,
    person_key,
    session as board_session,
)
from poolhouse.fleet.onboard.manifest import Signer
from poolhouse.fleet.onboard.signing import ConfirmedKeys

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")
STRIPPED = ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE",
            "POOLHOUSE_WORKSPACE_TOKEN", "POOLHOUSE_WORKSPACE_DENYLIST", "POOLHOUSE_WORKSPACE_AGENT", "POOLHOUSE_BOARD")
BOARD = "demo"
PASSPHRASE = "a passphrase for the person's test key"
TYPED = {"new signing key: ": PASSPHRASE, "signing key passphrase: ": PASSPHRASE, "Type yes": "yes"}
"""What a person types at the terminal: the passphrase of the key file when asked, and yes to each signature."""


def runner_binary() -> Path:
    """The node binary the runner built before this run (``scripts/testnode.py``); a run the runner did not start has none."""
    named = os.environ.get(testnode.ENV, "")
    if not named or not Path(named).is_file():
        pytest.fail(f"the node binary is built by the runner before a run, and {testnode.ENV} names none ({named or 'unset'}): "
                    "run the tests with `scripts/test`", pytrace=False)
    return Path(named)


@pytest.fixture(scope="session")
def node_binary(tmp_path_factory) -> Path:
    """The runner's node binary, copied to a folder of this session's own."""
    node_orphans.reap()
    mine = tmp_path_factory.mktemp("node") / "poolhouse-node"
    shutil.copy2(runner_binary(), mine)
    return mine


class NoKeystore:
    """A keystore that is not there: a test's key goes in a file under the passphrase of the environment, and the real
    keystore (or a test's fake one, with state a child process would then find without its master) is never asked."""

    def available(self) -> bool:
        return False


class SaysYes(ConfirmedKeys):
    """The person's key with the question answered yes and no terminal: the kit's stand-in for a person typing `yes`."""

    def _ask(self, prompt: str) -> bool:
        return True


def person_keys(directory: Path, cls: type[ConfirmedKeys] = ConfirmedKeys) -> ConfirmedKeys:
    """The person's key in ``directory`` the way `person_key.keys` makes it, in a file under the kit's passphrase (never the keystore)."""
    return cls(directory, describe=board_grants.describe, ks=NoKeystore(), passphrase=lambda _prompt: PASSPHRASE)


def enrol_first(client: board_client.Client, token: str, key: Signer | ConfirmedKeys) -> dict:
    """The first person key on the node, after the person types yes to the question the node writes on a terminal of its own
    (`wire.answer_presence`): the same steps as a person's, against the node as shipped."""
    said = wire.answer_presence(client.state, token, "key", key.public.hex())
    assert said["ok"] and said["result"]["state"] == "confirmed", f"the person's yes was not taken: {said}"
    return board_grants.enrol(client, token, {**board_grants.enrolment(key, "a test person"), "secret": said["result"]["secret"]})


def enrol_as_phone_key(node: WorkspaceNode, key: Signer) -> None:
    """Write ``key`` into the person keys file of ``node`` marked as a phone's own enrolment marks a key, and start the node again, which
    reads the file only when it starts: the node then derives the key as strong. Nothing but a file the account owns can do this."""
    node.stop()
    path = node.state / "persons.json"
    held = json.loads(path.read_text()) if path.exists() else {}
    held[board_grants.fingerprint(key.public)] = {"public": key.public.hex(), "label": "a test phone", "at_ms": 1, "alg": "ed25519", "phone": True}
    path.write_text(json.dumps(held))
    node.start()


def allow(carrier: board_session.Session, person: Signer, *actions: str, to: dict | None = None, **limits: Any) -> list[str]:
    """``person`` (a test key, enrolled on the node first) grants the session ``carrier`` (or ``to``) every action of the
    registry, or just ``actions``, within ``limits`` (``resource``, ``uses``, ``lasts_ms``); the grant ids. The carrier
    only carries the request."""
    held = board_grants.listing(carrier)
    if board_grants.fingerprint(person.public) not in held.persons:
        enrol_first(carrier.client, carrier.token, person)
    named = to or {"kind": "session", "name": carrier.name}
    return [board_grants.issue(carrier, person, board_grants.Want(a, named, **limits))["gid"] for a in actions or held.registry["actions"]]


def _read(descriptor: int) -> bytes:
    """What is waiting on a terminal's leader side; nothing once the terminal has hung up."""
    try:
        return os.read(descriptor, 65536)
    except OSError:
        return b""


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

    def keep_person_key(self, who: Member) -> ConfirmedKeys:
        """A person's key kept beside this node as `grants-setup` leaves it, and enrolled on it (``who`` only carries the
        request): a command run at a terminal signs for what it needs with it once the person types yes, and a process an
        agent marked is refused before the question. The key returned says yes to every question; the one a command reads asks on its terminal."""
        keys = person_keys(self.state / person_key.KEY_DIR, SaysYes)
        held = board_grants.listing(self.session(who)).persons
        if board_grants.fingerprint(keys.public) in held:
            return keys
        if held:
            board_grants.enrol(self.client, who.token, board_grants.approve(board_grants.enrolment(keys, "a test person"), self.person, board_grants.here(self.client)))
        else:
            enrol_first(self.client, who.token, keys)
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
                    **(who.env() if who else {}), **(extra or {})})
        return env

    def cli(self, *argv: str, who: Member | None = None, env: dict[str, str] | None = None, stdin: str | None = None,
            timeout: float = 90, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        """Run ``poolhouse-workspace`` as a subprocess, as ``who``."""
        return subprocess.run([sys.executable, "-m", "poolhouse.workspace.cli", *argv], env=self.env(who, env),
                              capture_output=True, text=True, timeout=timeout, check=False, input=stdin, cwd=cwd)

    def terminal(self, *argv: str, who: Member | None = None, timeout: float = 90, env: dict[str, str] | None = None,
                 typed: dict[str, str] | None = None, program: list[str] | None = None) -> tuple[int, str, str]:
        """Run ``poolhouse-workspace`` with a pseudo-terminal on stdin and stdout that is also its controlling terminal, as a
        person at a screen would: the exit status, what it printed there and what it wrote to stderr. ``typed`` maps a piece
        of text to what the person types each time it appears (`TYPED` is the passphrase and yes). ``program`` runs in place of
        the workspace command: a shell is the leader of the terminal session, as a person's shell is."""
        leader, follower = pty.openpty()
        with tempfile.TemporaryFile("w+") as errors:
            proc = subprocess.Popen(program or [sys.executable, "-m", "poolhouse.workspace.cli", *argv], env=self.env(who, env), stdin=follower,
                                    stdout=follower, stderr=errors, text=True, start_new_session=True,
                                    preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
            chunks: list[bytes] = []
            seen = dict.fromkeys(typed or {}, 0)
            end = time.monotonic() + timeout
            while proc.poll() is None and time.monotonic() < end:  # a terminal holds about a screenful: read while it runs
                ready, _, _ = select.select([leader], [], [], 0.1)
                if ready:
                    data = _read(leader)
                    if not data:  # the session that owned the terminal ended and the terminal hung up
                        break
                    chunks.append(data)
                    shown = b"".join(chunks).decode(errors="replace")
                    for text, reply in (typed or {}).items():
                        while (at := shown.find(text, seen[text])) >= 0:
                            seen[text] = at + len(text)
                            os.write(leader, f"{reply}\n".encode())
            try:
                proc.wait(timeout=max(0.5, end - time.monotonic()))
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise
            while select.select([leader], [], [], 0.2)[0] and (data := _read(leader)):  # read before the slave closes: macOS drops what is unread then
                chunks.append(data)
            errors.seek(0)
            message = errors.read()
        os.close(follower)
        os.close(leader)
        return proc.returncode, b"".join(chunks).decode(), message

    def start(self) -> dict:
        """Start the node (again), asking the person on a terminal it reads itself like every node of the tests."""
        return node_launch.ensure_node(self.state, extra=[*node_pool.network_args(), *wire.PRESENCE])

    def stop(self) -> None:
        """Stop the node and its supervisor and wait for them to go."""
        node_launch.stop_node(self.state)


WRITE_LIMITS = {"sends": 1_000_000, "subagent_sends": 1_000_000}
"""What a test node's sessions may write: fixtures post whole pages of messages, so the send rate is out of the way. A test module
that checks the limits sets ``NODE_WRITE_LIMITS`` to its own (``{}`` is the node's defaults)."""


@pytest.fixture
def workspace_node(node_binary, monkeypatch, request):
    """A running node on a short temporary root; in-process clients and child commands find it by the environment."""
    root = Path(tempfile.mkdtemp(prefix="ml")).resolve()
    state = root / "node"
    state.mkdir(mode=0o700)
    node_orphans.claim(root)
    (state / "write-limits.json").write_text(json.dumps(getattr(request.module, "NODE_WRITE_LIMITS", WRITE_LIMITS)))
    monkeypatch.setenv("POOLHOUSE_HOME", str(root))
    monkeypatch.setenv("POOLHOUSE_BOARD", BOARD)
    for name in STRIPPED:
        if name != "POOLHOUSE_BOARD":
            monkeypatch.delenv(name, raising=False)
    node_supervise.point(state, node_binary, node_binary_module.sha256(node_binary))
    node = WorkspaceNode(state, board_client.Client(state), node_binary)
    node.start()
    try:
        yield node
    finally:
        monkeypatch.undo()
        node.stop()
        shutil.rmtree(root, ignore_errors=True)
