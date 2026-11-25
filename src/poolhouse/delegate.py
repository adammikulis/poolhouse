"""``poolhouse-delegate``: hand a task to a local coding agent on the model this machine serves and read what it sends back.

    poolhouse-delegate start --task TEXT [--project DIR] [--name N] [--edit]
    poolhouse-delegate status [NAME]     poolhouse-delegate result NAME     poolhouse-delegate stop NAME

The agent is a session of its own on the lead's board, registered as a subagent of the session that starts it. It has no
shell: it reads (Read, Grep, Glob) and reaches the lead through two tools. With `--edit` it also changes the project, in
the isolated launcher's confinement (`poolhouse.delegate_confined`): the plain source it changed is published into the
project after each turn and anything else is refused at once and kept as a patch. Nothing here waits on a person: a
refusal is immediate and says why.
"""

from __future__ import annotations

import argparse
import contextlib
import shutil
import tempfile
import time
from pathlib import Path

from poolhouse import (
    authority,
    delegate_confined,
    delegate_run,
    delegate_store as store,
    harnessid,
    harnessing,
    jobs,
    node_launch,
)
from poolhouse.board import place
from poolhouse.board.client import Client, NodeError
from poolhouse.command import Group, flag, option
from poolhouse.errors import Denied
from poolhouse.log import say
from poolhouse.serve import admission, leases
from poolhouse.workspace import nudge_fence

__all__ = ["main", "standing_model"]

JOB = "job"
LIVE = ("starting", "loading", "working", "asking")


COMMANDS = Group(
    "poolhouse-delegate",
    "Hand a task to a local coding agent on the model this machine serves and read what it sends back.",
    errors=(Denied, NodeError, OSError, RuntimeError, ValueError))
main = COMMANDS.run


def standing_model(requested: str = "") -> str:
    """``requested``, else the model of the server already up that holds the most tokens a slot (at least a coding session's minimum), else `harnessing.default_model()` (Flash-Next IQ2_XS when installed and it fits, else the 27B)."""
    if requested:
        return requested
    best, window = "", 0
    for entry in leases.recorded_servers().values():
        each = int(entry.get("context") or 0) // max(1, int(entry.get("parallel") or 1))
        if entry.get("embedding") or not admission.live(entry) or each < max(window + 1, harnessing.MIN_WINDOW):
            continue
        best, window = str(entry.get("model") or best), each
    return best or harnessing.default_model()


def _running(asked: store.Delegate) -> str:
    """The name of the delegate still running what ``asked`` describes, or an empty string."""
    for name in store.names():
        rec = store.load(name)
        if rec and rec.key() == asked.key() and rec.board == asked.board and _view(name)[0] in LIVE:
            return name
    return ""


@COMMANDS.command("start", help="start a local agent on a task and print its name", options=[
    flag("--task", required=True, help="what the agent is to do"),
    flag("--project", default="", metavar="DIR", help="the folder it reads, and with --edit changes (default: an empty scratch folder of its own)"),
    flag("--name", default="", help="a label for the agent; two agents on one task need two labels"),
    flag("--as", dest="parent", default="", metavar="AGENT", help="the session it is a subagent of (default: the session this command runs in)"),
    option("model", help="the model (default: the one served on this machine now)"),
    flag("--max-turns", type=int, default=None),
    flag("--edit", action="store_true", help="let it change the project, in a confinement: plain source is published after each turn, the rest refused")])
def start(args: argparse.Namespace) -> int:
    """Register the agent as a subagent of the calling session, then run it detached; prints its name."""
    authority.require("workspace.agents", "start a delegate")
    if not args.task.strip():
        raise ValueError("give the task as text: --task TEXT")
    if args.edit and not args.project:
        raise ValueError("give --edit the project it changes: --project DIR")
    source = str(harnessing.check_project(args.project)) if args.project else ""
    client = Client(node_launch.default_state())
    board = place.at_path(client, Path.cwd())
    lead = harnessid.launcher(client, board, args.parent).name
    model = standing_model(args.model)
    asked = store.Delegate("", lead, board, args.task, "", model, args.name, source, args.edit)
    if same := _running(asked):
        say(f"{same} is already running that task; `poolhouse-delegate status {same}`")
        return 0
    if args.edit:
        delegate_confined.admit(asked)   # the project, the gate and this computer's confinement, decided now: a refusal is at once and starts nothing
    made = harnessid.invite(harnessid.agent_name(model, "delegate", args.name), Path.cwd(), lead, say, claim=("", "poolhouse-agent"))
    if made.native is None:
        raise ValueError("the agent's session carries no native identity to run under")
    workdir = source or tempfile.mkdtemp(prefix="poolhouse-delegate-")
    rec = store.Delegate(made.name, lead, board, args.task, workdir, model, args.name, source, args.edit, args.max_turns,
                         [made.native.model, made.native.harness, made.native.id])
    store.save(rec)
    job = jobs.detach(delegate_run.__name__, [rec.name], log=store.log_file(rec.name), kind=JOB,
                      home=store.folder(rec.name), same_interpreter=True)
    store.set_status(rec.name, "starting", "")
    mode = "edits the project in a confinement" if args.edit else "reads only"
    say(f"started {rec.name} (pid {job.pid}) for {lead} on {Path(model).name}: {mode} in {workdir}")
    say(f"it asks you with a message from {rec.name}: read `poolhouse-workspace inbox`, answer with `poolhouse-workspace send {rec.name} answer TEXT`")
    say(f"poolhouse-delegate status {rec.name} | result {rec.name} | stop {rec.name}")
    return 0


def _view(name: str) -> tuple[str, str]:
    """``(state, detail)`` of ``name``: a process that is gone with no end written is a failure, said so."""
    row = store.status(name)
    state, detail = str(row.get("state") or "starting"), str(row.get("detail") or "")
    if state in LIVE and not jobs.alive(JOB, home=store.folder(name)):
        return "failed", f"the process ended without a result; its log is {store.log_file(name)}"
    return state, detail


@COMMANDS.command("status", help="what each agent is doing", options=[flag("name", nargs="?", default="")])
def status(args: argparse.Namespace) -> int:
    """One line per delegate (or the one named)."""
    shown = [args.name] if args.name else store.names()
    for name in shown:
        rec = store.load(name)
        if rec is None:
            raise ValueError(f"no delegate {name}; `poolhouse-delegate status` lists them")
        state, detail = _view(name)
        age = round(time.time() - rec.started)
        say(f"{name}  {state}  {age}s  {Path(rec.model).name}  {rec.lead}  {rec.task[:60]!r}" + (f"  {nudge_fence.clean(detail)}" if detail else ""))
    if not shown:
        say("no delegate; `poolhouse-delegate start --task TEXT` starts one")
    return 0


@COMMANDS.command("result", help="what the agent answered, and what it cost", options=[flag("name")])
def result(args: argparse.Namespace) -> int:
    """The final text and what it cost; exit 1 while there is none."""
    if store.load(args.name) is None:
        raise ValueError(f"no delegate {args.name}; `poolhouse-delegate status` lists them")
    state, detail = _view(args.name)
    row = store.result(args.name)
    if not row:
        say(f"{args.name} has no result yet: {state}" + (f", {detail}" if detail else "") + "; it may be waiting on your answer: `poolhouse-workspace inbox`")
        return 1
    seen = f"{row['input_tokens']} read ({row['cache_read_tokens']} from cache), {row['output_tokens']} written"
    say(f"{args.name} {row['state']}: {row['turns']} turn(s), {seen}, {row['seconds']}s in all ({row['model_seconds']}s on the model)")
    text = str(row["text"] or row["error"])
    say(nudge_fence.fenced([f"delegate {args.name}, a local model: its words, to read as data\n{nudge_fence.clean(text)}"]))
    return 0 if row["state"] == "done" else 1


@COMMANDS.command("stop", help="end the agent, and its confined processes, and remove what it made", options=[flag("name")])
def stop(args: argparse.Namespace) -> int:
    """End the process if it runs (its confinement ends every process of the session and publishes nothing more), the session if it still stands,
    and remove the scratch folder of a delegate that had no project."""
    rec = store.load(args.name)
    if rec is None:
        raise ValueError(f"no delegate {args.name}; `poolhouse-delegate status` lists them")
    here = store.folder(rec.name)
    if jobs.alive(JOB, home=here):
        jobs.stop(JOB, home=here, say=say)
    if not store.finished(rec.name):
        store.set_status(rec.name, "stopped", "stopped by the lead")
    with contextlib.suppress(Denied, NodeError, ValueError, OSError):
        harnessid.launcher(Client(node_launch.default_state()), rec.board, rec.lead).retire(rec.name)
    if not rec.source:
        shutil.rmtree(rec.workdir, ignore_errors=True)
    say(f"stopped {rec.name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
