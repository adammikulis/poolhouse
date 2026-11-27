"""The choke point of the isolated launcher: `prepare` builds one confinement and proves it, and `Confinement.popen` is the only way a coding harness
starts (docs/design-isolated-launcher.md, I1, I2, 3.3 and 10).

A launcher gives `prepare` a `Plan`: the harness and the project, the model it was leased, the handlers of the hook and the workspace verbs (closed
over the launch's own helper identity), and `render`, which makes the harness's files and command line from the parts only `prepare` knows (the home,
the bridge's socket, the client). `prepare` stages the project, starts the model bridge and the session bridge, writes the rendered files read-only,
builds the allow-list environment and the profile, and runs the proof. A profile that does not hold raises `IsolationFailed` and nothing else starts;
a computer that cannot confine raises `IsolationRefused` before anything is made. `Confinement.popen` starts the harness under that one profile,
followed from its first instruction, and attests the live process; `close` ends every process of the session, publishes the last turn and removes
what the launch made. Nothing here asks, waits or reads a terminal.
"""

from __future__ import annotations

import contextlib
import json
import os
import select
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from poolhouse import activity, coding_policy, home as state_home
from poolhouse.isolation import client as client_module
from poolhouse.isolation.attestation import attest
from poolhouse.isolation.capability import require_capable
from poolhouse.isolation.environment import Inputs, environment
from poolhouse.isolation.grants import Grant
from poolhouse.isolation.model_bridge import Lease, ModelBridge
from poolhouse.isolation.notices import Notice
from poolhouse.isolation.profile import Profile, build, search_path
from poolhouse.isolation.proof import Proof, Services, run as run_proof
from poolhouse.isolation.publish import bind
from poolhouse.isolation.refusals import IsolationFailed
from poolhouse.isolation.session_bridge import Handlers, SessionBridge, Terms
from poolhouse.isolation.spec import Spec
from poolhouse.isolation.workspace import Launch, Published, Session, Turn, tally
from poolhouse.sandbox import Started, process_tree, spawn
from poolhouse.sandbox.run import HOOKS

__all__ = ["SERVER", "Confinement", "Context", "Made", "Plan", "Rendering", "install_tree", "prepare"]

CLIENT = "client.py"
SERVER = "lead"
"""The name of the one MCP server a harness has: its tools are `mcp__lead__ask` and `mcp__lead__note` (OpenCode: `lead_ask`, `lead_note`)."""
SYSTEM_ROOTS = frozenset({"/", "/usr", "/usr/local", "/opt", "/opt/homebrew", "/Library", "/System", "/bin", "/sbin"})
"""A directory that is a system tree, never a harness's own install tree."""
HARNESS_DIRECTORIES = {"claude": ".claude-config", "codex": ".codex-home", "pi": ".pi-config", "opencode": ".opencode-config"}
"""Where each harness keeps what it writes, inside the agent home."""
Decide = Callable[[str, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class Context:
    """What a harness's files and command line are made from: the places `prepare` made, and the one client the harness's hooks, its shims and its
    MCP server run. The launcher's own identity and the bridge's token appear in none of it: the socket path and the launcher's process id are the
    whole of what the client needs."""

    home: Path
    config: Path
    stage: str
    python: str
    client: str
    socket: str
    pid: int
    base_url: str
    alias: str
    window: int
    token: str = ""
    """The bridge token the harness holds, for a harness that reads its key from a file and not from a variable (Pi). Never printed, never logged."""

    def client_argv(self, *words: str) -> list[str]:
        """The command a confined process runs to reach the session bridge: ``words`` are the client's own (`hook pre`, `workspace inbox`, `mcp`)."""
        return [self.python, "-I", self.client, "--socket", self.socket, "--pid", str(self.pid), *words]

    def hook(self, event: str) -> str:
        """The shell line a harness runs for a hook event."""
        return shlex.join(self.client_argv("hook", event))

    def lead_server(self) -> dict[str, Any]:
        """The ``mcpServers`` entry that is the harness's way to its lead: the bridge's client as a stdio MCP server, named `lead` (`SERVER`), with the
        two tools `ask` and `note` and no other. Claude Code and Pi read it from a file of this shape, Codex and OpenCode from their own settings."""
        command, *arguments = self.client_argv("mcp")
        return {"command": command, "args": arguments}

    def lead_servers(self, timeout_s: float) -> str:
        """The JSON file (``{"mcpServers": {"lead": ...}}``) of Claude Code; ``timeout_s`` is how long it waits for one call of the server's tools
        (its per-server ``timeout``, in milliseconds)."""
        return json.dumps({"mcpServers": {SERVER: {**self.lead_server(), "timeout": int(timeout_s * 1000)}}})

    def file(self, name: str) -> Path:
        """Where the rendered file ``name`` is, inside the agent home."""
        return self.home / name


@dataclass(frozen=True, slots=True)
class Rendering:
    """What a harness needs written before it starts: its command line (binary first) and its files by name inside the agent home. A file is written
    read-only and refused a write for the whole session."""

    argv: tuple[str, ...]
    files: Mapping[str, str] = field(default_factory=dict)
    listen: tuple[int, ...] = ()
    """Loopback ports the harness's own server binds and is reached on (OpenCode's), chosen by the launcher: the profile opens these and no other."""


@dataclass(frozen=True, slots=True)
class Plan:
    """One launch, as a launcher gives it to the choke point."""

    harness: str
    binary: str
    project: str
    policy: coding_policy.Policy
    upstream: str
    lease: Lease
    key: str
    agent: str
    decide: Callable[[str], Decide]
    workspace: Callable[[str, Mapping[str, Any]], Any]
    render: Callable[[Context], Rendering]
    notice: Notice
    grants: tuple[Grant, ...] = ()
    conversation: str = ""
    extras: Mapping[str, str] = field(default_factory=dict)
    interactive: bool = False


class Admission:
    """Which processes the session bridge answers: before the harness exists, the proof's own probe is the only client; once it runs, a process
    that is in the harness's tree (`sandbox.process_tree`, which follows every fork from the first instruction) and no other."""

    def __init__(self) -> None:
        self.tree: process_tree.Tree | None = None

    def __call__(self, pid: int) -> bool:
        return self.tree is None or pid in self.tree


@dataclass(slots=True)
class Parts:
    """What a launch has made so far, so that an abandoned one can undo it: the services started, the directories to remove, who the session bridge
    answers and what each publication said."""

    stack: list[Any] = field(default_factory=list)
    scratch: list[str] = field(default_factory=list)
    admission: Admission = field(default_factory=Admission)
    turns: list[Turn] = field(default_factory=list)


@dataclass(slots=True)
class Made:
    """What `prepare` made for one confinement: its profile and proof, the staged project, the services it started, the directories it must remove,
    and what each step took."""

    profile: Profile
    proof: Proof
    session: Session
    services: list[Any]
    scratch: list[str]
    admission: Admission
    home: Path
    config: Path
    argv: tuple[str, ...]
    timing: dict[str, float] = field(default_factory=dict)
    turns: list[Turn] = field(default_factory=list)
    """What each publication did, in order: one at every `Stop` event of the harness, and the last at the end of the session."""


def install_tree(binary: str) -> str:
    """The files of the harness itself a session may read and run: the release directory of ``<release>/bin/<name>`` (Codex), else the one
    executable (Claude Code), and never a system tree."""
    real = Path(os.path.realpath(binary))
    release = real.parent.parent
    if real.parent.name == "bin" and str(release) not in SYSTEM_ROOTS and not str(release).startswith(("/usr/", "/System/")):
        return str(release)
    return str(real)


def _private(path: Path, mode: int) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(mode)
    return path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(stat.S_IRUSR)


def _events(name: str, fields: dict[str, object]) -> None:
    if HOOKS.events is not None:
        with contextlib.suppress(OSError, ValueError, RuntimeError):
            HOOKS.events(name, fields)


class Confinement:
    """One confinement, proven. ``argv`` and ``env`` are exactly what runs and what it runs with; ``popen`` starts it. Close it, whatever happens."""

    def __init__(self, plan: Plan, made: Made, cwd: str) -> None:
        self.plan, self.made, self.cwd = plan, made, cwd
        self.profile, self.proof, self.session, self.argv = made.profile, made.proof, made.session, made.argv
        self.home, self.config, self.timing = made.home, made.config, made.timing
        self.env = dict(made.profile.policy.env)
        self.agent = plan.agent
        self._started: Started | None = None
        self.turn: Turn | None = None
        self.closed = False
        self.stall = max(made.proof.seconds, 1.0)
        """The seconds with no process ending that stop the end of the session's tree: what the proof's own run of the profile took."""

    def __enter__(self) -> Confinement:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def process(self) -> subprocess.Popen[bytes] | None:
        """The harness, once `popen` has started it."""
        return None if self._started is None else self._started.process

    def popen(self, *, stdin: Any = subprocess.DEVNULL, stdout: Any = subprocess.PIPE, stderr: Any = subprocess.PIPE) -> subprocess.Popen[bytes]:
        """Start the harness under the profile that was proved, followed from its first instruction, and attest the live process: the operating
        system's own report that it is inside the confinement it was started in (10.3). One harness runs in a confinement."""
        if self._started is not None or self.closed:
            raise RuntimeError("a confinement starts its harness once")
        self._started = spawn(self.argv, self.profile.policy, cwd=self.cwd, stdin=stdin, stdout=stdout, stderr=stderr)
        self.made.admission.tree = self._started.tree
        process = self._started.process
        deadline, seen = time.monotonic() + self.stall, attest(process.pid)
        while not seen.confined and process.poll() is None and time.monotonic() < deadline:
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=0.005)   # the harness may still be applying its profile: a look at the operating system again, not a wait for anyone
            seen = attest(process.pid)
        if not seen.confined and process.poll() is None:
            self._end()
            _events("isolation.attestation_failed", {"severity": "warning", "policy": "isolation", "digest": self.profile.digest})
            raise IsolationFailed("attestation", "I9", self.profile.digest, seen.fact)
        return process

    def run(self, stdin: IO[bytes] | None = None, out: IO[bytes] | None = None, err: IO[bytes] | None = None) -> int:
        """Start the harness and relay its output to ``out`` and ``err`` (this process's own, by default) and ``stdin`` to it; its exit status. The
        harness is given pipes and never this process's terminal."""
        out, err = out or sys.stdout.buffer, err or sys.stderr.buffer
        process = self.popen(stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL)

        def copy(source: IO[bytes], sink: IO[bytes]) -> None:
            for chunk in iter(lambda: source.read1(65536), b""):
                sink.write(chunk)
                sink.flush()

        pumps = [threading.Thread(target=copy, args=(process.stdout, out), daemon=True), threading.Thread(target=copy, args=(process.stderr, err), daemon=True)]
        if stdin is not None:
            def feed() -> None:
                """Hand this process's own standard input on while the harness runs, and stop when it has ended: a read that waits for a person
                is never left blocked at exit."""
                try:
                    while process.poll() is None:
                        if select.select([stdin], [], [], 0.2)[0]:
                            chunk = os.read(stdin.fileno(), 65536)
                            if not chunk:
                                break
                            process.stdin.write(chunk)
                            process.stdin.flush()
                except (OSError, ValueError):
                    pass
                with contextlib.suppress(OSError, ValueError):
                    process.stdin.close()
            pumps.append(threading.Thread(target=feed, daemon=True))
        for pump in pumps:
            pump.start()
        status = process.wait()
        for pump in pumps:
            pump.join(timeout=self.stall)
        return status

    def said(self) -> list[str]:
        """The words of every publication of this session that had something to say (what was applied, what was refused and where the patch is),
        in order: the line a terminal launch ends with."""
        return [turn.text for turn in self.made.turns if turn.text]

    def published(self) -> Published:
        """What every publication of this session came to, as data: no caller reads it from `said`."""
        return tally(self.made.turns)

    def waiting_for_model(self) -> bool:
        """Whether a request of the harness is with the model server now."""
        return any(each.busy() for each in self.made.services if isinstance(each, ModelBridge))

    def end_processes(self) -> None:
        """End every process of the session's tree, so none that outlived the harness holds its pipes; nothing is published."""
        if self._started is not None and self._started.tree is not None:
            self._end()

    def _end(self) -> process_tree.Ended:
        started = self._started
        if started is None:
            return process_tree.Ended()
        return process_tree.end(started.tree, stalled=self.stall)

    def close(self, *, publish: bool = True, keep_patch: bool = False) -> Turn:
        """End every process of the session, publish the last turn (the checks decide, not the exit status), stop the services and remove what the
        launch made. With ``publish`` false (the session is stopped by the one that started it, or it failed) nothing more is published: the stage's
        unpublished work is dropped with the stage, after it is kept as a patch when ``keep_patch`` says so. Safe to call twice; the first call
        decides."""
        if self.closed:
            return self.turn or Turn()
        self.closed = True
        started = self._started
        try:
            tree = started.tree if started else None
            self.turn = (self.session.finish(tree, stalled=self.stall, ended=None if started else process_tree.Ended()) if publish
                         else self.session.discard(tree, stalled=self.stall, keep_patch=keep_patch))
            self.made.turns.append(self.turn)
        finally:
            if started is not None:
                started.release()
            for service in self.made.services:
                with contextlib.suppress(OSError, RuntimeError, ValueError):
                    service.stop()
            for path in self.made.scratch:
                shutil.rmtree(path, ignore_errors=True)
            activity.record("isolation.launch", subject=self.plan.agent, outcome="ended",
                            meta={"digest": self.profile.digest, "applied": len(self.turn.applied) if self.turn else 0,
                                  "refused": len(self.turn.refused) if self.turn else 0})
        return self.turn


def _services(plan: Plan, session: Session, parts: Parts) -> tuple[ModelBridge, SessionBridge]:
    """Start the model bridge and the session bridge. A service that cannot start refuses the launch."""
    model = ModelBridge(plan.upstream, plan.lease, key=plan.key, events=_events)
    model.start()
    parts.stack.append(model)

    def decide(event: str, payload: dict[str, Any]) -> dict[str, Any]:
        """A `stop` event is the end of a turn: the parent publishes what verified before the hook answers."""
        if event == "stop":
            parts.turns.append(session.publish_turn())
        return answer(event, payload)

    answer = plan.decide(str(session.stage.physical))
    root = tempfile.mkdtemp(prefix="pp-", dir="/tmp")
    parts.scratch.append(root)
    bridge = SessionBridge(Handlers(decide, plan.workspace, activity.record, _events), Terms(plan.lease.window), root=root, admit=parts.admission)
    bridge.start()
    parts.stack.append(bridge)
    return model, bridge


def _abandon(session: Session, stack: list[Any], scratch: list[str]) -> None:
    """Undo a launch that did not reach a proved confinement: the services stop, the stage goes, the directories are removed."""
    for service in stack:
        with contextlib.suppress(OSError, RuntimeError, ValueError):
            service.stop()
    with contextlib.suppress(Exception):
        session.finish(None, stalled=1.0, ended=process_tree.Ended())
    for path in scratch:
        shutil.rmtree(path, ignore_errors=True)


def prepare(plan: Plan) -> Confinement:
    """The one door to a harness: stage the project, start the bridges, write the harness's files, build the profile and prove it. Raises
    `IsolationRefused` before anything is made when this computer cannot confine, and `IsolationFailed` when the proof finds the confinement open;
    in both the harness does not exist."""
    began = time.monotonic()
    require_capable(Spec(plan.harness, "", "", "", 0, project=plan.project, interactive=plan.interactive))
    rules = tuple(bind(plan.project, grant.subject) for grant in plan.grants if grant.kind == "publish")
    session = Session.begin(Launch(plan.project, str(state_home.state("isolation")), plan.agent, plan.notice, plan.conversation, rules))
    parts = Parts()
    stack, scratch = parts.stack, parts.scratch
    staged = time.monotonic()
    proved: Proof | None = None
    try:
        agent_home = Path(os.path.realpath(tempfile.mkdtemp(prefix="poolhouse-home-")))
        scratch.append(str(agent_home))
        for name in ("bin", "tmp", HARNESS_DIRECTORIES[plan.harness]):
            _private(agent_home / name, 0o700)
        model, bridge = _services(plan, session, parts)
        client = agent_home / "bin" / CLIENT
        _write(client, Path(client_module.__file__).read_text(encoding="utf-8"))
        stage = str(session.stage.physical)
        context = Context(agent_home, agent_home / HARNESS_DIRECTORIES[plan.harness], stage, os.path.realpath(sys.executable), str(client), bridge.path,
                          os.getpid(), f"http://127.0.0.1:{model.port}", plan.lease.alias, plan.lease.window, model.token)
        rendering = plan.render(context)
        argv = (os.path.realpath(rendering.argv[0]), *rendering.argv[1:])   # the harness is run by its real path: its link is the person's
        frozen = [str(client), str(agent_home / "bin")]
        for name, text in rendering.files.items():
            _write(agent_home / name, text)
            frozen.append(str(agent_home / name))
        spec = Spec(plan.harness, stage, str(agent_home), install_tree(plan.binary), model.port, bridge.path, 0, session.patches, plan.project,
                    plan.policy, plan.interactive, tuple(frozen), rendering.listen)
        env = environment(Inputs(plan.harness, str(agent_home), search_path(spec, plan.grants), context.base_url, model.token, plan.lease.alias,
                                 plan.lease.window, plan.interactive, {}, plan.extras))
        profile = build(spec, plan.grants, env)
        proved = run_proof(profile, Services(model.port, bridge.path, tuple(grant.subject for grant in plan.grants if grant.kind == "toolchain")))
    finally:
        if proved is None:
            _abandon(session, stack, scratch)
    activity.record("isolation.launch", subject=plan.agent, outcome="proved",
                    meta={"digest": profile.digest, "proof_seconds": proved.seconds, "grants": [grant.fingerprint for grant in plan.grants]})
    timing = {"stage": round(staged - began, 3), "proof": proved.seconds, "prepare": round(time.monotonic() - began, 3)}
    return Confinement(plan, Made(profile, proved, session, stack, scratch, parts.admission, agent_home, context.config, argv, timing, parts.turns), stage)
