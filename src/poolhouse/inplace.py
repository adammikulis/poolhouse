"""How a launched coding harness starts: in place by default, or in the sandbox a launch asked for.

In place, the harness runs on the project folder itself with the person's own settings, MCP servers, programs and network, and is pointed at the
served model by the arguments and the environment of this one run. Nothing of the person's or the project's configuration is written: what a run needs
written is in a private folder under the state root, removed when the run ends. `Direct` is what a runner is given in place of a confinement.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from poolhouse import harnessing, isolation, platform, serverkeys
from poolhouse.board import session as sessions
from poolhouse.serve.process import kill_process_tree

__all__ = ["IDENTITY", "Context", "Direct", "Rendering", "environment", "run", "started"]

IDENTITY = (sessions.AGENT_ENV, sessions.SUBAGENT_ENV, sessions.TOKEN_ENV, "CODEX_THREAD_ID", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID",
            "POOLHOUSE_SESSION_ID", "POOLHOUSE_SESSION_HARNESS")
"""What names the session a launch was typed in; the harness it starts has a session of its own."""


@dataclass(frozen=True, slots=True)
class Context:
    """What the arguments and files of a run in place are made from: the run's private folder and the served model."""

    files: Path
    base_url: str
    key: str
    alias: str
    window: int

    def file(self, name: str) -> Path:
        return self.files / name


@dataclass(frozen=True, slots=True)
class Rendering:
    """A harness's command line, the files it names (written to the run's folder), the variables set over the person's own and those taken out."""

    argv: tuple[str, ...]
    files: Mapping[str, str] = field(default_factory=dict)
    env: Mapping[str, str] = field(default_factory=dict)
    drop: tuple[str, ...] = ()


class Direct:
    """One harness run on the folder itself. It has the part of `isolation.Confinement` a runner uses; ``busy`` says whether a request of the harness
    is with the model server, for a caller that watches for progress."""

    config: Path | None = None
    """No folder of the launcher's: the harness keeps what it writes in its own folders."""

    def __init__(self, argv: Sequence[str], env: Mapping[str, str], cwd: str | Path, busy: Callable[[], int] = lambda: 0) -> None:
        self.argv, self.env, self.cwd, self.busy = tuple(argv), dict(env), str(cwd), busy
        self.process: subprocess.Popen[bytes] | None = None

    def popen(self, *, stdin: Any = None, stdout: Any = None, stderr: Any = None) -> subprocess.Popen[bytes]:
        """Start the harness; a stream that is not given is this process's own, the terminal of an interactive run among them."""
        if self.process is not None:
            raise RuntimeError("a run starts its harness once")
        self.process = platform.start_grouped(self.argv, cwd=self.cwd, env=self.env, stdin=stdin, stdout=stdout, stderr=stderr)
        return self.process

    def run(self) -> int:
        """Start the harness on this process's own streams and wait for it; an interrupt typed meanwhile is the harness's to answer."""
        process = self.popen()
        if threading.current_thread() is not threading.main_thread():
            return process.wait()
        before = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            return process.wait()
        finally:
            signal.signal(signal.SIGINT, before)

    def end_processes(self) -> None:
        if self.process is not None and self.process.poll() is None:
            kill_process_tree(self.process.pid, grace_s=2)

    def close(self, *, publish: bool = True, keep_patch: bool = False) -> None:
        """End what still runs; there is nothing to publish, since every change is already in the folder."""
        self.end_processes()

    def waiting_for_model(self) -> bool:
        return bool(self.busy())

    def passed(self) -> str:
        return ""


def environment(base: Mapping[str, str], agent: str, made: Rendering, extras: Mapping[str, str]) -> dict[str, str]:
    """The person's own environment, marked as an agent's, with the harness's own workspace session (``agent``, when there is one) in place of the
    one the launch was typed in, and what points the harness at the model."""
    env = {name: value for name, value in base.items() if name not in IDENTITY and name not in made.drop}
    env["POOLHOUSE_AGENT"] = "1"
    if agent:
        env[sessions.AGENT_ENV] = agent
    return {**env, **made.env, **extras}


@contextlib.contextmanager
def run(how: harnessing.Harness, session: harnessing.Session, render: Callable[[Context], Rendering]) -> Iterator[Direct]:
    """The run in place for the block; its private folder is removed when the block ends."""
    files = harnessing.session_files(None)
    try:
        base_url, alias, window = how.served
        made = render(Context(files.path, base_url, serverkeys.for_url(base_url) or "local", alias, window))
        for name, text in made.files.items():
            files.write(name, text)
        helper = session.session
        env = environment(os.environ, helper.name if getattr(helper, "minted", False) else "", made, how.extras)
        direct = Direct(made.argv, env, session.cwd)
        try:
            yield direct
        finally:
            direct.close()
    finally:
        files.release()


@contextlib.contextmanager
def started(args: Any, session: harnessing.Session, how: harnessing.Harness,
            renders: tuple[Callable[[isolation.Context], isolation.Rendering], Callable[[Context], Rendering]]) -> Iterator[Any]:
    """What the launch's runner is given, for the block: the proved confinement of a ``--sandbox`` launch (``renders[0]``), else the run in place
    (``renders[1]``). One line says which, and what the agent may do."""
    boxed, direct = renders
    stance = f"permissions {args.permissions if args.sandbox or args.asked else 'your own settings'}; mode {args.execution_mode}"
    if args.sandbox:
        with harnessing.confined(args, session, how, boxed) as confinement:
            how.say(f"{stance}; working on a copy of {session.cwd}, in a sandbox with no network but the model; changes arrive when the turn ends")
            yield confinement
        return
    with run(how, session, direct) as running:
        how.say(f"{stance}; working in {session.cwd} itself, on {how.served[1]}, with your own settings, MCP servers and network")
        yield running
