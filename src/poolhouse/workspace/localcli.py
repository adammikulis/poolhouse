"""``poolhouse-workspace agent start|stop|list``: a local model that joins a board as a session."""

from __future__ import annotations

import argparse
import math
import os
import time
from pathlib import Path

from poolhouse import authority, harnessing, node_launch, person as terminal, roles
from poolhouse.board import person, person_key, place, session as sessions
from poolhouse.board.client import Client, Denied
from poolhouse.command import flag
from poolhouse.log import say, warn
from poolhouse.serve import provenance
from poolhouse.workspace import (
    backlog,
    issuepump,
    limits,
    localagent as la,
    localeffort as le,
    localloop,
    localmodel,
    localprofile as lp,
    localstart as ls,
    plain,
    task_launch,
    task_scheduler,
)

__all__ = ["ACTIONS", "OPTIONS", "command", "run"]

ACTIONS = ("start", "stop", "list", "backlog", "schedule", "supersede-issue", "resume-issue")
READY_WAIT_S = 180.0


def _limit(value: str) -> int | None:
    if value.lower() == "none":
        return None
    amount = int(value)
    if amount <= 0:
        raise argparse.ArgumentTypeError("a limit must be a positive integer or none")
    return amount


def _seconds(value: str) -> float | None:
    if value.lower() == "none":
        return None
    amount = float(value)
    if not math.isfinite(amount) or amount <= 0:
        raise argparse.ArgumentTypeError("task seconds must be positive and finite, or none")
    return amount


OPTIONS = [
    flag("action", choices=ACTIONS, help="start a local model as an agent, stop one, or list them"),
    flag("target", nargs="?", default="", metavar="NAME", help="stop: the agent to stop"),
    flag("--task", default="", help="existing assigned task authorizing worker management"),
    flag("--model", default=localmodel.AUTO, metavar="auto|ID",
         help="auto: the best downloaded Qwen model that fits this machine, or the id "
              "of a downloaded model"),
    flag("--name", default="", help="the agent's workspace name (default: local- and the model's short name)"),
    flag("--role", default="", choices=list(roles.ROLES),
         help="what it may do; coding defaults to plan-and-go, chat to the standard role"),
    flag("--project", default="", metavar="PATH", help="the project folder it works for"),
    flag("--repo", default="", metavar="OWNER/REPO", help="project issue source; defaults to the project's GitHub origin"),
    flag("--issue", type=int, default=0, help="supersede-issue: obsolete repository issue number"),
    flag("--reason", default="", help="supersede-issue: current owner decision superseding the issue"),
    flag("--effort", default=le.DEFAULT, choices=[*le.LEVELS, le.AUTO],
         help="how much the model thinks (off is fastest); auto picks per task; default: %(default)s"),
    flag("--max-effort", default=le.DEFAULT_MAX, choices=list(le.LEVELS),
         help="the most effort the model may give itself with set_effort; default: %(default)s"),
    flag("--max-output-tokens", type=_limit, default=None,
         help="maximum generated tokens per response; default none"),
    flag("--max-rounds", type=_limit, default=None, help="task turns; default none"),
    flag("--max-tool-calls", type=_limit, default=None, help="tool calls per task; default none"),
    flag("--max-model-calls", type=_limit, default=None, help="model calls per task; default none"),
    flag("--max-task-seconds", type=_seconds, default=None, help="task wall time in seconds; default none"),
    flag("--orders-from", default=",".join(la.DEFAULT_ORDERS_FROM), metavar="NAMES",
         help="sessions it takes tasks from besides the person, its starter and any lead (comma list of names)"),
    flag("--profile", default="coding", choices=["chat", "coding"],
         help="coding: durable project task queue; chat: interactive message loop"),
    flag("--harness", default="pi", choices=["pi", "codex", "claude"], help="native coding harness"),
    flag("--ctx", default="", metavar="TOKENS", help="context to serve (profile default), or such as 32k, 128k or 256k"),
    flag("--for", dest="lease_for", default="", metavar="TEXT",
         help="why the model is leased, one line, shown by `poolhouse-serve status|leases|history`"),
    flag("--no-wait", action="store_true", help="return as soon as the agent is started"),
]


def _wait(base: Path, name: str, seconds: float) -> int:
    """Poll the status file until the agent is idle or failed; 0 ready, 1 failed or timed out."""
    began, shown = time.monotonic(), ""
    while time.monotonic() - began < seconds:
        row = next((r for r in ls.listing(base) if r["name"] == name), None)
        if row is None or not row["running"]:
            warn(f"{name} stopped before it was ready; see {la.log_file(base, name)}")
            return 1
        line = f"  {row['state']}: {row['detail']}".rstrip(": ")
        if line != shown:
            say(line)
            shown = line
        if row["state"] == "idle":
            say(f"{name} is ready: send it a task with `poolhouse-workspace send {row['identity']} task TEXT`")
            return 0
        if row["state"] == "failed":
            warn(f"{name} could not load its model: {row['detail']}")
            return 1
        time.sleep(1.0)
    say(f"{name} is still loading; `poolhouse-workspace agent list` shows when it is ready")
    return 0


def _session(args: argparse.Namespace) -> sessions.Session:
    """The registered native Codex caller, an explicitly selected session, or the person on the board of its project.
    Native lookup never creates a project or falls back to a person."""
    state = node_launch.default_state()
    if os.environ.get("CODEX_THREAD_ID", "").strip():
        client = Client(state, autostart=False)
        board = args.board or str(client.call("project_resolve", path=str(Path(args.project or ".").resolve()))["board"])
        return sessions.connect(token_file=args.token_file, agent=args.agent, board=board, client=client)
    client = Client(state, person=person_key.provider(state, acting=person.acting(state)))
    named = args.agent or args.token_file or os.environ.get(sessions.AGENT_ENV, "")
    if named:
        return sessions.connect(token_file=args.token_file, agent=args.agent, board=args.board, client=client)
    if terminal.marked():
        raise Denied("denied", "an agent starts a local agent as a session of its own: run it as a registered session")
    where = Path(args.project or ".")
    return person.on(client, place.resolve(client, where, args.board))


def _start(args: argparse.Namespace, s: sessions.Session, base: Path) -> int:
    if args.agent:
        if not args.task or not (args.name or args.target):
            raise ValueError('agent start requires an existing worker name and --task')
        got = task_launch.start(s, base, args.name or args.target, args.task)
        runner = la.load(base, got.name)
        caps = localloop.caps_of(runner)
        if caps.seconds is None:
            warn("Task wall time is None. Tasks could run indefinitely until you cancel them.")
        say(f'{got.name}: {runner.model_name}; {runner.ctx} context; {runner.role}; {runner.harness}; '
            f'effort {runner.effort}, ceiling {runner.max_effort}; '
            f'{caps.rounds} turns, {caps.calls} tool calls, {caps.steps} model calls, {caps.seconds} seconds wall time')
        return 0 if args.no_wait else _wait(base, got.name, READY_WAIT_S)
    selected_profile = lp.profile(args.profile)
    if selected_profile.name == "coding":
        harnessing.require_isolated_execution()
    selected_context = lp.parse_ctx(args.ctx) or selected_profile.ctx
    project = args.project or ("." if selected_profile.name == "coding" else "")
    selected_role = args.role or (la.PLAN_AND_GO if selected_profile.name == "coding" else roles.DEFAULT)
    pick = localmodel.choose(args.model, selection=localmodel.Selection(
        coding=selected_profile.name == "coding", context=selected_context))
    if not pick.ok:
        warn(pick.problem)
        if pick.hint:
            say(f"fetch it with: {pick.hint}")
        return 1
    if getattr(args, "max_task_seconds", None) is None:
        warn("Task wall time is None. Tasks could run indefinitely until you cancel them.")
    say(f"model: {pick.name} ({pick.note})")
    try:
        got = ls.start(s, ls.Ask(args.model, args.name, selected_role, args.effort, args.max_effort,
                                 args.profile, selected_context, project, la.check_orders(args.orders_from.split(",")), args.harness,
                                 repo=args.repo, max_output_tokens=args.max_output_tokens,
                                 task_caps={"rounds": getattr(args, "max_rounds", None),
                                            "calls": getattr(args, "max_tool_calls", None),
                                            "steps": getattr(args, "max_model_calls", None),
                                            "seconds": getattr(args, "max_task_seconds", None)}), base=base, pick=pick)
    except ls.Unavailable as err:
        warn(str(err))
        return 1
    if got.already:
        say(f"{got.name} is already running (pid {got.pid}) on {got.model} as {got.role}")
        return 0
    say(f"it takes tasks from: the person, {s.name} which started it, any lead, "
        + ", ".join(la.check_orders(args.orders_from.split(",")))
        + " (and their delegates); everything else is information only")
    say(f"started {got.name} as {got.identity} (pid {got.pid}) on {got.model} as {got.role}; "
        f"log: {la.log_file(base, got.name)}")
    return 0 if args.no_wait else _wait(base, got.name, READY_WAIT_S)


def _stop(args: argparse.Namespace, base: Path, caller: sessions.Session | None = None) -> int:
    if not args.target:
        raise ValueError("agent stop needs the agent's name; `agent list` shows them")
    done = (task_launch.stop(caller or _session(args), base, args.target, args.task) if args.agent
            else ls.stop(base, args.target))
    say(f"stopped {done.name}" + (" (it had to be killed)" if done.forced else "")
        + ("; its model lease is released" if done.lease_released else ""))
    for note in done.notes:
        warn(note)
    return 0


def _list(base: Path) -> int:
    rows = ls.listing(base)
    if not rows:
        say("no local agent is running; `poolhouse-workspace agent start` starts one")
        return 0
    for r in rows:
        last = r["last_message"]
        heard = f", last from {plain.line(last.get('from', ''), 40)}" if last else ""
        say(f"{plain.line(r['name'], 48)}  {r['state']}  {plain.line(r['model'], 40)}  {r['role']}  "
            f"{r['tasks']} tasks, {r['steps']} steps{heard}"
            + (f"  {plain.line(r['detail'], 120)}" if r["detail"] else ""))
    return 0


def command(args: argparse.Namespace) -> int:
    """Do the agent action; native callers manage existing assigned tasks."""
    return run(args, limits.root())


def run(args: argparse.Namespace, base: Path) -> int:
    """Do the ``agent`` action with the local agents' records in ``base``."""
    if args.action == "list":
        return _list(base)
    caller = _session(args) if os.environ.get("CODEX_THREAD_ID", "").strip() else None
    if caller is not None:
        args.agent = caller.name  # Only the authenticated native session normalizes legacy dispatch.
    if args.action == 'schedule':
        if not args.target or not args.agent:
            raise ValueError('agent schedule requires an existing worker and authenticated session')
        return task_scheduler.watch(caller or _session(args), base, la.check_name(args.target), task=args.task)
    if args.action in ("supersede-issue", "resume-issue"):
        if not args.target:
            raise ValueError("supersede-issue needs the existing worker name")
        if not args.agent:
            authority.require("workspace.agents", "supersede a repository issue")
        decision = backlog.supersede if args.action == "supersede-issue" else backlog.resume
        decision(caller or _session(args), base, args.target, args.issue, args.reason)
        say(f"issue {args.issue}: {args.action} decision recorded")
        return 0
    if not (args.agent and args.action in ("start", "stop")):
        authority.require("workspace.agents", f"{args.action} a local agent")
    if args.action == "backlog":
        if not args.target:
            raise ValueError("agent backlog needs the existing worker's name")
        worker = la.load(base, args.target)
        project = args.project or (worker.project if worker else "")
        repo = args.repo or (backlog.repository(project) if project else "")
        s = caller or _session(args)
        backlog.configure(s, base, args.target, repo, project)
        issuepump.start(s, base, args.target)
        say(f"{args.target} is pulling open issues from {repo} into its task queue")
        return 0
    provenance.told(args.lease_for)
    if args.action == "start":
        return _start(args, caller or _session(args), base)
    return _stop(args, base, caller=caller)
