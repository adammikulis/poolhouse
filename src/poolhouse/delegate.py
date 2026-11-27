"""``poolhouse-delegate``: hand a task to a local coding agent on the model this machine serves and read what it sends back.

    poolhouse-delegate start --task TEXT [--project DIR] [--name N] [--edit] [--permissions TIER]
    poolhouse-delegate status [NAME]     poolhouse-delegate result NAME     poolhouse-delegate stop NAME

The agent is a session of its own on the lead's board, registered as a subagent of the session that starts it. It is
OpenCode, in the `read-only` tier unless the lead chooses another: it reads and searches its folder, with no shell, and
reaches the lead through two tools. With `--edit` (the `guarded` tier) or `--permissions` it also changes the project and
runs a shell. It runs in the isolated launcher's confinement (`poolhouse.delegate_confined`): its whole change is published
into the project when it ends, except `.git`, coding tools' settings, credentials and links that lead out, which are
refused at once and kept as a patch. On a platform that has no sandbox it runs on the folder itself under its tier's
rules, with a warning (`poolhouse.delegate_unboxed`); a computer whose sandbox cannot be used refuses it with the reason.
Nothing here waits on a person: a refusal is immediate and says why.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from poolhouse import (
    authority,
    coding_policy,
    delegate_confined,
    delegate_run,
    delegate_store as store,
    delegate_unboxed,
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


def tier_of(edit: bool, permissions: str) -> str:
    """The tier a delegate runs in: ``permissions`` when named, `guarded` for ``--edit``, else `read-only`."""
    if edit and permissions == coding_policy.READ_ONLY:
        raise ValueError("--edit changes files and --permissions read-only does not: give one of them")
    return permissions or (coding_policy.GUARDED if edit else coding_policy.READ_ONLY)


def _folder(source: str, edit: bool) -> str:
    """The folder a delegate is confined to a stage of: its project, or, for one with none or one that only reads a folder that is no git project's
    root, a git repository of its own under the temporary folder, holding a copy of that folder (links are copied as links). A confinement stages
    a git project and nothing else; one that edits such a folder is refused by it."""
    if source and (edit or os.path.lexists(Path(source) / ".git")):
        return source
    made = tempfile.mkdtemp(prefix="poolhouse-delegate-")
    try:
        if source:
            shutil.copytree(source, made, symlinks=True, dirs_exist_ok=True)
        subprocess.run([shutil.which("git") or "git", "init", "--quiet", made], check=True, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        shutil.rmtree(made, ignore_errors=True)
        raise
    return made


@COMMANDS.command("start", help="start a local agent on a task and print its name", options=[
    flag("--task", required=True, help="what the agent is to do"),
    flag("--project", default="", metavar="DIR", help="the folder it reads, and with --edit changes (default: an empty scratch folder of its own; with --edit, the project this command runs in)"),
    flag("--name", default="", help="a label for the agent; two agents on one task need two labels"),
    flag("--as", dest="parent", default="", metavar="AGENT", help="the session it is a subagent of (default: the session this command runs in)"),
    option("model", help="the model (default: the one served on this machine now)"),
    flag("--max-turns", type=int, default=None),
    flag("--edit", action="store_true", help="let it change the project (the guarded tier); in a sandbox its change is published into the project when it ends"),
    flag("--permissions", default="", choices=("", *coding_policy.PERMISSIONS),
         help="what it may do (default: read-only; --edit is guarded). A platform with no sandbox runs a higher tier on the folder itself, with a warning")])
def start(args: argparse.Namespace) -> int:
    """Register the agent as a subagent of the calling session, then run it detached; prints its name."""
    authority.require("workspace.agents", "start a delegate")
    if not args.task.strip():
        raise ValueError("give the task as text: --task TEXT")
    tier = tier_of(args.edit, args.permissions)
    edit = tier != coding_policy.READ_ONLY
    source = str(harnessing.check_project(args.project)) if args.project or edit else ""   # an editing delegate works on the project this runs in by default
    client = Client(node_launch.default_state())
    board = place.at_path(client, Path.cwd())
    lead = harnessid.launcher(client, board, args.parent).name
    model = standing_model(args.model)
    asked = store.Delegate("", lead, board, args.task, "", model, args.name, source, edit, permissions=tier)
    if same := _running(asked):
        say(f"{same} is already running that task; `poolhouse-delegate status {same}`")
        return 0
    why = delegate_confined.unsandboxable()
    if why:   # on the folder itself, or an empty one of its own
        workdir = source or tempfile.mkdtemp(prefix="poolhouse-delegate-")
    else:
        workdir = _folder(source, edit)
        try:   # the folder and the gate of a sandboxed delegate, decided now: a refusal is at once and starts nothing
            delegate_confined.admit(replace(asked, workdir=workdir), args.project if workdir == source else "")
        except (Denied, OSError, RuntimeError, ValueError):
            if workdir != source:
                shutil.rmtree(workdir, ignore_errors=True)
            raise
    made = harnessid.invite(harnessid.agent_name(model, "delegate", args.name), Path.cwd(), lead, say, claim=("", "poolhouse-agent"))
    if made.native is None:
        raise ValueError("the agent's session carries no native identity to run under")
    rec = store.Delegate(made.name, lead, board, args.task, workdir, model, args.name, source, edit, args.max_turns,
                         [made.native.model, made.native.harness, made.native.id], permissions=tier, sandboxed=not why)
    store.save(rec)
    job = jobs.detach(delegate_run.__name__, [rec.name], log=store.log_file(rec.name), kind=JOB,
                      home=store.folder(rec.name), same_interpreter=True)
    store.set_status(rec.name, "starting", "")
    mode = "edits the project in a confinement" if edit else "reads only"
    if why:
        say(delegate_unboxed.warning(tier, why))
        mode = f"{tier}, with no sandbox,"
    say(f"started {rec.name} (pid {job.pid}) for {lead} on {Path(model).name}: {mode} in {workdir}")
    say(f"it asks you with a message from {rec.name}: read `poolhouse-workspace inbox`, answer with `poolhouse-workspace send {rec.name} answer TEXT`")
    say(f"poolhouse-delegate status {rec.name} | result {rec.name} | stop {rec.name}")
    return 0


WARM_TASK = "Reply with the single word ready. Use no tools."
"""What `warm` hands a delegate: a task that costs the model nothing but the prompt every delegate of the lead shares."""


@COMMANDS.command("warm", help="load the model and its shared prompt now, so the next delegate starts at once", options=[
    option("model", help="the model (default: the one served on this machine now)"),
    flag("--edit", action="store_true", help="warm the prompt of a delegate that edits the project (its tools differ)")])
def warm(args: argparse.Namespace) -> int:
    """Run a delegate on a trivial task: the server is leased and stays up, and its prompt cache holds the part every delegate shares."""
    return start(argparse.Namespace(task=WARM_TASK, project="", name="warm", parent="", model=args.model, max_turns=1, edit=args.edit, permissions=""))


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
        row = store.status(name)
        progress = f"  {row['turns']} turn(s), last tool {nudge_fence.clean(str(row.get('tool') or 'none'))}" if row.get("turns") else ""
        say(f"{name}  {state}  {age}s  {Path(rec.model).name}  {rec.lead}  {rec.task[:60]!r}{progress}" + (f"  {nudge_fence.clean(detail)}" if detail else ""))
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
    for said in (row.get("error") if row["text"] else "", row.get("detail")):
        if said:
            say(nudge_fence.clean(str(said)))
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
    if rec.workdir != rec.source:
        shutil.rmtree(rec.workdir, ignore_errors=True)
    say(f"stopped {rec.name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
