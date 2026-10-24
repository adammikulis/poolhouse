"""Start, stop and list local agents. Callers have established that a person asked: the terminal
command checks for a person at a terminal, the browser route for the person's session."""

from __future__ import annotations

import contextlib
import os
import signal
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, TypedDict, Unpack

from poolhouse import agent_dependency, harnessing, jobs, roles
from poolhouse.board import place
from poolhouse.board.client import NodeError
from poolhouse.board.session import Native, Registration, Session
from poolhouse.lock import held
from poolhouse.serve import broker_wire
from poolhouse.serve.process import pid_exists, started_at
from poolhouse.workspace import (
    backlog,
    issuepump,
    limits,
    localagent as la,
    localeffort as le,
    localharness as lh,
    localloop,
    localmodel,
    localprofile as lp,
)

__all__ = ["Ask", "Started", "Stopped", "Unavailable", "listing", "pause", "start", "stop"]

LOOP = "poolhouse.workspace.localloop"
STOP_WAIT_S = 20.0
HARNESSES = ("pi", "codex", "claude")


class Unavailable(ValueError):
    """No model can be started: ``hint`` is the one command that would fix it."""

    def __init__(self, problem: str, hint: str = "") -> None:
        super().__init__(problem + (f"; run: {hint}" if hint else ""))
        self.problem, self.hint = problem, hint


@dataclass(frozen=True, slots=True)
class Ask:
    """What the person chose in the start form or on the command line."""

    model: str = localmodel.AUTO
    name: str = ""
    role: str = roles.DEFAULT
    effort: str = "off"
    max_effort: str = le.DEFAULT_MAX
    profile: str = "chat"
    ctx: int = 0
    project: str = ""
    orders_from: tuple[str, ...] = la.DEFAULT_ORDERS_FROM
    harness: str = lh.PI
    repo: str = ""
    max_output_tokens: int | None = None
    task_caps: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Started:
    """The agent that is running after `start`; ``already`` is true when it was running before and ``identity`` is the
    name its session has on the board."""

    name: str
    pid: int
    model: str
    role: str
    already: bool = False
    identity: str = ""


@dataclass(frozen=True, slots=True)
class Stopped:
    """What `stop` did: the process ended (``forced`` when it had to be killed) and the lease it held."""

    name: str
    was_running: bool
    forced: bool = False
    lease_released: bool = False
    notes: list[str] = field(default_factory=list)


def _output_tokens(value: int | None) -> int | None:
    """Validate the positive generated-token limit."""
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError('maximum output tokens must be a positive integer')
    return value


def _running(have: la.Agent, chosen: localmodel.Pick) -> Started:
    if have.model != chosen.ref:
        raise ValueError(f"{have.name} already runs {have.model_name}; stop and restart that same name to change models")
    return Started(have.name, have.pid, have.model_name, have.role, already=True, identity=have.identity)


def _check_board(s: Session, project: str) -> None:
    """Refuse a project that belongs to another board than the session that starts the agent."""
    if project and place.existing(s.client, Path(project)) != s.board:
        raise ValueError("the project belongs to another board than the session that starts the agent")


def _register(s: Session, name: str, chosen: localmodel.Pick, harness: str) -> Registration:
    """The agent's session on the board, as a subagent of the session that starts it."""
    try:
        return s.register(Native(localmodel.model_identity(chosen.name), harness, la.native_id(s.name, name)))
    except NodeError as err:
        raise ValueError(f"{name} could not join the board {s.board}: {err}") from err


class StartOptions(TypedDict, total=False):
    """What `start` takes besides the session and the request: where the agent's files are, a model already chosen, how to
    detach the loop, and whether a saved registration is kept."""

    base: Path
    pick: localmodel.Pick
    spawn: Callable[..., Any]
    reuse: bool


def _chosen(ask: Ask, folder_: str, pick: localmodel.Pick | None) -> tuple[lp.Profile, localmodel.Pick, int]:
    """The profile, the model and the context length of a request, after the refusals: no suitable model, a model that
    would not fit, a coding agent without a project or a known harness."""
    prof = lp.profile(ask.profile)
    if prof.name == "chat":
        problem = agent_dependency.problem()
        if problem:
            raise Unavailable(problem, "pip install 'poolhouse[agents]'")
    ctx = ask.ctx or prof.ctx
    chosen = pick or localmodel.choose(
        ask.model, selection=localmodel.Selection(coding=prof.name == "coding", context=ctx))
    if not chosen.ok:
        raise Unavailable(chosen.problem, chosen.hint)
    if prof.name == "coding":
        if ask.harness not in HARNESSES:
            raise ValueError("coding harness is pi, codex or claude")
        if not folder_:
            raise ValueError("a coding agent works in a project folder")
    return prof, chosen, ctx


def start(s: Session, ask: Ask, **options: Unpack[StartOptions]) -> Started:
    """Join a local model to the board of ``s`` as a subagent of that session and run its loop detached; the same name
    again reports the agent already running. Raises `Unavailable` when no suitable model is downloaded or it would
    not fit, ValueError for a bad name, role or project."""
    if ask.profile == "coding":
        harnessing.require_isolated_execution()
    base, reuse = options.get("base") or limits.root(), options.get("reuse", False)
    output_tokens = _output_tokens(ask.max_output_tokens)
    task_caps = asdict(localloop.checked_caps(ask.task_caps))
    role = roles.get(ask.role).name
    ceiling = le.valid(ask.max_effort)
    effort = le.clamp(le.valid(ask.effort, allow_auto=True), ceiling) if ask.effort != le.AUTO else le.AUTO
    folder_ = la.check_project(ask.project, base)
    orders = la.check_orders(list(ask.orders_from))
    prof, chosen, ctx = _chosen(ask, folder_, options.get("pick"))
    _check_board(s, folder_)
    if not ctx:
        try:
            ctx = localmodel.context_for(chosen)
        except (OSError, ValueError) as error:
            raise Unavailable(str(error)) from error
    problem, hint = lp.admit(chosen.ref or chosen.name, ctx)
    if problem:
        raise Unavailable(problem, hint)
    name = la.check_name(ask.name or ("local-coding" if prof.name == "coding" else "local-agent"))
    harness = ask.harness if prof.name == "coding" else lh.OWN
    with held(la.folder(base) / "start.lock"):
        have = la.load(base, name)
        if have is not None and la.alive(have):
            return _running(have, chosen)
        if reuse and have is not None and have.identity:
            if have.model != chosen.ref:
                raise ValueError("task launch must preserve the saved worker model")
            registered = have.identity
        else:
            registered = _register(s, name, chosen, harness).name
        la.stop_file(base, name).unlink(missing_ok=True)
        agent = la.Agent(name=name, identity=registered, board=s.board, model=chosen.ref, model_name=chosen.name,
                         size_bytes=chosen.size_bytes, role=role, profile=prof.name, harness=harness, ctx=ctx,
                         effort=effort, max_effort=ceiling, project=folder_, orders_from=orders, started=time.time(),
                         extra={**(dict(have.extra) if have else {}), "task_caps": task_caps,
                                "explicit_output_limit": True, "explicit_task_limits": True},
                         max_output_tokens=output_tokens)
        la.save(base, agent)
        entry = lh.RUNNER if prof.name == "coding" else LOOP
        job = (options.get("spawn") or jobs.detach)(entry, [name], log=la.log_file(base, name), kind=name, home=la.folder(base) / "jobs")
        la.save(base, replace(agent, pid=job.pid, process_started=started_at(job.pid) or 0.0, log=str(job.log)))
    repo = ask.repo or (backlog.repository(folder_) if prof.name == "coding" and folder_ else "")
    if repo:
        issuepump.configure_and_start(s, base, name, repo, folder_)
    return Started(name, job.pid, chosen.name, role, identity=registered)


def _ended(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while pid_exists(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def stop(base: Path, name: str, *, release: Callable[[str], Any] | None = None,
         wait_s: float = STOP_WAIT_S) -> Stopped:
    """Stop the owned process and holder while keeping its session and saved preferences."""
    agent = la.load(base, la.check_name(name))
    if agent is None:
        raise ValueError(f"no local agent called {name}; `agent list` shows them")
    la.stop_file(base, name).write_text("stop\n", encoding="utf-8")
    lease = str((la.status_of(base, name).get("lease") or {}).get("id") or "")
    owned = bool(release) or _owns_lease(agent, lease)
    running, forced, notes = la.alive(agent), False, []
    if running:
        with contextlib.suppress(ProcessLookupError):
            os.kill(agent.pid, signal.SIGTERM)
        if not _ended(agent.pid, wait_s):
            forced = True
            with contextlib.suppress(ProcessLookupError):
                os.kill(agent.pid, signal.SIGKILL)
            _ended(agent.pid, 5.0)
    freed = False
    if lease and owned:
        try:
            freed = bool((release or _release)(lease))
        except (OSError, RuntimeError) as err:
            notes.append(f"the lease was not released now ({err}); it ends with the process")
    la.save(base, replace(agent, pid=0, process_started=0.0))
    la.Status(base, name).update(state='stopped', detail='Session and preferences retained', lease={})
    for path in (la.stop_file(base, name), la.pause_file(base, name), la.folder(base) / "jobs" / f"{name}.json"):
        path.unlink(missing_ok=True)
    return Stopped(name, running, forced, freed, notes)


def _owns_lease(agent: la.Agent, lease: str) -> bool:
    if not lease or not agent.pid:
        return False
    try:
        status = broker_wire.status(start=False)
    except (OSError, RuntimeError):
        return False
    return any(holder.get("lease") == lease and holder.get("pid") == agent.pid
               and holder.get("pid_started") == agent.process_started
               for server in status.get("servers", []) for holder in server.get("holders", []))


def _release(lease: str) -> bool:
    return bool(broker_wire.release(lease))


def pause(base: Path, name: str, paused: bool) -> dict[str, Any]:
    """Pause or resume queued tasks; an active task finishes before pausing."""
    name = la.check_name(name)
    if la.load(base, name) is None:
        raise ValueError(f"no local agent called {name}")
    flag = la.pause_file(base, name)
    if paused:
        flag.write_text("paused\n", encoding="utf-8")
    else:
        flag.unlink(missing_ok=True)
    return {"name": name, "paused": paused}


def listing(base: Path) -> list[dict[str, Any]]:
    """Every local agent: its model, role, state, steps, last message, memory held and lease."""
    out = []
    for name in la.names(base):
        agent = la.load(base, name)
        if agent is None:
            continue
        status, live = la.status_of(base, name), la.alive(agent)
        if live and float(status.get("beat") or 0) < agent.started:
            status = {}
        state = str(status.get("state") or "starting") if live else (
            "failed" if status.get("state") == "failed" else "stopped")
        out.append({
            "name": name, "identity": agent.identity or name, "model": agent.model_name, "role": agent.role, "effort": status.get("effort") or agent.effort, "max_effort": agent.max_effort, "profile": agent.profile, "harness": agent.harness, "ctx": agent.ctx,
            "max_output_tokens": agent.max_output_tokens,
            "project": Path(agent.project).name if agent.project else "", "running": live,
            "state": state, "detail": str(status.get("detail") or ""),
            "steps": int(status.get("steps") or 0), "tasks": int(status.get("tasks") or 0),
            "ignored": int(status.get("ignored") or 0),
            "last_message": status.get("last_message") or {},
            "backlog": issuepump.status(base, name),
            "memory_bytes": agent.size_bytes if live and state != "failed" else 0,
            "lease": bool((status.get("lease") or {}).get("id")) and live,
            "paused": la.pause_file(base, name).exists(), "orders_from": list(agent.orders_from), "started": agent.started,
            "beat": float(status.get("beat") or 0.0)})
    return out
