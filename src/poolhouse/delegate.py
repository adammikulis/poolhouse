"""``poolhouse-delegate``: hand a task to a local coding agent on the model this machine serves and read what it sends back.

    poolhouse-delegate start --task TEXT [--edit] [--wait] ...     status [NAME]     wait NAME     result NAME     stop NAME
    poolhouse-delegate grade NAME VERDICT --critique TEXT [--check "RUN => SAW"]     record NAME     summary

The agent is OpenCode, a subagent of the session that starts it, `read-only` unless the lead chooses another tier, and
confined (`poolhouse.delegate_confined`; on the folder itself, with a warning, where there is no sandbox). Before
delivery a fresh reviewer on the same model tries to prove the work wrong (`poolhouse.delegate_review`); a change is
published once accepted, except `.git`, coding tools' settings, credentials and links that lead out. Every run keeps a
grading record (`poolhouse.delegate_grade`). Nothing waits on a person: a refusal says why (docs/harnesses.md).
"""

from __future__ import annotations

import argparse
import contextlib
import json
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
    delegate_grade,
    delegate_resume,
    delegate_review,
    delegate_run,
    delegate_store as store,
    delegate_tree,
    delegate_unboxed,
    harnessid,
    harnessing,
    isolation,
    jobs,
    lead_bridge,
    node_launch,
)
from poolhouse.board import place, session as sessions
from poolhouse.board.client import Client, NodeError
from poolhouse.command import Group, flag, option
from poolhouse.errors import Denied
from poolhouse.log import say
from poolhouse.ownership import recorded_servers
from poolhouse.serve import admission
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
    for entry in recorded_servers().values():
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
         help="what it may do (default: read-only; --edit is guarded). A platform with no sandbox runs a higher tier on the folder itself, with a warning"),
    flag("--kind", default="", help="the kind of task, one short word, for the grading record (default: edit or read)"),
    flag("--review-rounds", type=int, default=delegate_review.ROUNDS,
         help="the rounds of self-check before delivery: a fresh reviewer, then a fix, until one accepts (default: %(default)s)"),
    flag("--no-review", action="store_true", help="deliver the work with no self-check"),
    flag("--resumes", type=int, default=delegate_resume.RESUMES,
         help="the attempts to continue a run something outside its task broke off, such as its model's server going away (default: %(default)s)"),
    flag("--in-place", action="store_true",
         help="with --edit: change the project folder itself, not a worktree and branch of the delegate's own"),
    flag("--wait", action="store_true", help="return only when it ends or asks something (run this in the background to be woken then)")])
def start(args: argparse.Namespace) -> int:
    """Register the agent as a subagent of the calling session, then run it detached; prints its name."""
    isolation.require_attended("starting a delegate")   # a delegate, and whatever it starts, hands no task on
    authority.require("workspace.agents", "start a delegate")
    rounds = _rounds(args)
    tier = tier_of(args.edit, args.permissions)
    edit = tier != coding_policy.READ_ONLY
    kind = delegate_grade.kind_of(args.kind, edit)
    source = str(harnessing.check_project(args.project)) if args.project or edit else ""   # an editing delegate works on the project this runs in by default
    client = Client(node_launch.default_state())
    board = place.at_path(client, Path.cwd())
    lead = harnessid.launcher(client, board, args.parent).name
    model = standing_model(args.model)
    asked = store.Delegate("", lead, board, args.task, "", model, args.name, source, edit, permissions=tier, review_rounds=rounds)
    if same := _running(asked):
        say(f"{same} is already running that task; `poolhouse-delegate status {same}`")
        return 0
    why = delegate_confined.unsandboxable()
    workdir, branch = _admitted(asked, args.project, why), ""
    made = harnessid.invite(harnessid.agent_name(model, "delegate", args.name), Path.cwd(), lead, say, claim=("", "poolhouse-agent"))
    if made.native is None:
        raise ValueError("the agent's session carries no native identity to run under")
    if edit and not args.in_place and delegate_tree.possible(source):
        try:
            workdir, branch = _own_tree(asked, made.name, why)
        except (Denied, OSError, RuntimeError, ValueError):
            with contextlib.suppress(Denied, NodeError, ValueError, OSError):
                harnessid.launcher(client, board, lead).retire(made.name)
            raise
    rec = store.Delegate(made.name, lead, board, args.task, workdir, model, args.name, source, edit, args.max_turns,
                         [made.native.model, made.native.harness, made.native.id], permissions=tier, sandboxed=not why, kind=kind,
                         review_rounds=rounds, commit=delegate_grade.commit_of(source), resumes=args.resumes, branch=branch)
    store.save(rec)
    delegate_grade.opened(rec)
    job = jobs.detach(delegate_run.__name__, [rec.name], log=store.log_file(rec.name), kind=JOB,
                      home=store.folder(rec.name), same_interpreter=True)
    store.set_status(rec.name, "starting", "")
    _announce(rec, job.pid, why)
    return _waited(rec.name) if args.wait else 0


def _rounds(args: argparse.Namespace) -> int:
    """The self-check rounds of a start, 0 for none, after what it was given is checked."""
    if not args.task.strip():
        raise ValueError("give the task as text: --task TEXT")
    if not 1 <= args.review_rounds <= delegate_review.MOST_ROUNDS:
        raise ValueError(f"--review-rounds is 1 to {delegate_review.MOST_ROUNDS}; --no-review turns the self-check off")
    if not 0 <= args.resumes <= delegate_resume.MOST_RESUMES:
        raise ValueError(f"--resumes is 0 to {delegate_resume.MOST_RESUMES}")
    return 0 if args.no_review else args.review_rounds


def _admitted(asked: store.Delegate, typed: str, why: str) -> str:
    """The folder of a delegate that has no worktree of its own. With no sandbox (``why``) the folder itself, or an empty one; in a sandbox the
    folder and the gate are decided now, so a refusal is at once and starts nothing."""
    if why:
        return asked.source or tempfile.mkdtemp(prefix="poolhouse-delegate-")
    workdir = _folder(asked.source, asked.edit)
    try:
        delegate_confined.admit(replace(asked, workdir=workdir), typed if workdir == asked.source else "")
    except (Denied, OSError, RuntimeError, ValueError):
        if workdir != asked.source:
            shutil.rmtree(workdir, ignore_errors=True)
        raise
    return workdir


def _own_tree(asked: store.Delegate, name: str, why: str) -> tuple[str, str]:
    """A worktree and branch of delegate ``name``, admitted like its project; removed again when it is refused."""
    if behind := delegate_tree.uncommitted(asked.source):
        say(f"it starts from the project's commit: {len(behind)} uncommitted file(s) of {asked.source} are not in its worktree")
    workdir, branch = delegate_tree.made(asked.source, name)
    try:
        if not why:
            delegate_confined.admit(replace(asked, workdir=workdir), "")
    except (Denied, OSError, RuntimeError, ValueError):
        delegate_tree.removed(asked.source, workdir, branch, keep_branch=False)
        raise
    return workdir, branch


def _announce(rec: store.Delegate, pid: int, why: str) -> None:
    """What the lead is told of a delegate that has started."""
    mode = "edits the project in a confinement" if rec.edit else "reads only"
    if why:
        say(delegate_unboxed.warning(rec.permissions, why))
        mode = f"{rec.permissions}, with no sandbox,"
    say(f"started {rec.name} (pid {pid}) for {rec.lead} on {Path(rec.model).name}: {mode} in {rec.workdir}")
    if rec.review_rounds:
        held = "" if why or not rec.edit else ", and its change is applied only once accepted"
        say(f"a fresh reviewer checks the work before delivery, in up to {rec.review_rounds} round(s){held}; --no-review turns it off")
    say(f"it asks you with a message from {rec.name}: read `poolhouse-workspace inbox`, answer with `poolhouse-workspace send {rec.name} answer TEXT`")
    if rec.branch:
        say(f"it works on branch {rec.branch} in a worktree of its own; what stands is committed there for you to review and land")
    say(f"poolhouse-delegate status {rec.name} | wait {rec.name} | result {rec.name} | stop {rec.name}")


ASKS = 3
"""The exit status of a wait that ended because the delegate asked something."""


def _waited(name: str) -> int:
    """Return when ``name`` ends (its result printed, and its exit status) or asks something (`ASKS`)."""
    while True:
        state, detail = _view(name)
        if state == "asking":
            say(f"{name} asks you something ({nudge_fence.clean(detail)}): read `poolhouse-workspace inbox`, answer with "
                f"`poolhouse-workspace send {name} answer TEXT`, then `poolhouse-delegate wait {name}`")
            return ASKS
        if state not in LIVE:
            if store.result(name):
                return result(argparse.Namespace(name=name))
            say(f"{name} {state}: {nudge_fence.clean(detail)}")
            return 1
        time.sleep(lead_bridge.POLL_S)


@COMMANDS.command("wait", help="return when the agent ends or asks something (run this in the background to be woken then)", options=[flag("name")])
def wait(args: argparse.Namespace) -> int:
    """The result once it ends, else status `ASKS` when it asks."""
    if store.load(args.name) is None:
        raise ValueError(f"no delegate {args.name}; `poolhouse-delegate status` lists them")
    return _waited(args.name)


WARM_TASK = "Reply with the single word ready. Use no tools."
"""What `warm` hands a delegate: a task that costs the model nothing but the prompt every delegate of the lead shares."""


@COMMANDS.command("warm", help="load the model and its shared prompt now, so the next delegate starts at once", options=[
    option("model", help="the model (default: the one served on this machine now)"),
    flag("--edit", action="store_true", help="warm the prompt of a delegate that edits the project (its tools differ)")])
def warm(args: argparse.Namespace) -> int:
    """Run a delegate on a trivial task: the server is leased and stays up, and its prompt cache holds the part every delegate shares."""
    return start(argparse.Namespace(task=WARM_TASK, project="", name="warm", parent="", model=args.model, max_turns=1, edit=args.edit, permissions="",
                                    kind="warm", review_rounds=delegate_review.ROUNDS, no_review=True, resumes=0, in_place=True, wait=False))


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
        phase = _phase(rec, state, row)
        say(f"{name}  {state}  {age}s  {Path(rec.model).name}  {rec.lead}  {rec.task[:60]!r}{phase}{progress}"
            + (f"  {nudge_fence.clean(detail)}" if detail else ""))
    if not shown:
        say("no delegate; `poolhouse-delegate start --task TEXT` starts one")
    return 0


def _phase(rec: store.Delegate, state: str, row: dict) -> str:
    """The phase a running delegate is in, or what the self-check of an ended one came to; empty for one that has neither."""
    if state in LIVE:
        phase, round_ = str(row.get("phase") or ""), row.get("round")
        return f"  [{phase} {round_}/{rec.review_rounds}]" if round_ else f"  [{phase}]" if phase and rec.review_rounds else ""
    review = store.result(rec.name).get("review")
    if not review:
        return ""
    came = "accepted" if review["accepted"] else f"not accepted, {len(review['findings'])} open finding(s)"
    return f"  [self-check {came}, round {review['rounds']}/{review['allowed']}]"


def _passes(name: str, row: dict) -> list[str]:
    """Each pass of a self-checked run with its time and tokens, and each review's verdict."""
    rounds = {each.get("round"): each for each in store.graded(name)["grades"] if each.get("kind") == delegate_review.SELF_CHECK}
    said = []
    for each in row.get("phases") or []:
        title = f"{each['phase']} {each['round']}" if each["round"] else each["phase"]
        title += f", attempt {each['attempt']} to continue" if each.get("attempt") else ""
        broke = str(each.get("interrupted") or "")
        verdict = rounds.get(each["round"], {}) if each["phase"] == "review" and not broke else {}
        found = f": {verdict.get('verdict')}, {len(verdict.get('findings', []))} finding(s)" if verdict else ": interrupted" if broke else ""
        why = str(broke or verdict.get("critique") or each.get("error") or "")
        said.append(f"  {title}{found}; {each['turns']} turn(s), {each['input_tokens']} read, {each['output_tokens']} written, {each['seconds']}s"
                    + (f"; {nudge_fence.clean(why)[:300]}" if why else ""))
    return said


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
    review = row.get("review")
    if review:
        came = "accepted" if review["accepted"] else "not accepted"
        say(f"self-check (the same model reviewing, not a grade): {came} after {review['rounds']} of {review['allowed']} round(s)")
    if len(row.get("phases") or []) > 1:
        for line in _passes(args.name, row):
            say(line)
    if review and review["findings"] and not review["accepted"]:
        found = "\n".join(f"- {each['what']} (evidence: {each.get('evidence') or 'none given'})" for each in review["findings"])
        say(nudge_fence.fenced([f"open findings of the self-check of {args.name}, a local model's words\n{nudge_fence.clean(found)}"]))
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
    if not store.graded(rec.name)["run"].get("state"):   # a runner that ended wrote its own
        store.keep(rec.name, run={"state": store.status(rec.name).get("state", "stopped"), "ended": time.time()})
    with contextlib.suppress(Denied, NodeError, ValueError, OSError):
        harnessid.launcher(Client(node_launch.default_state()), rec.board, rec.lead).retire(rec.name)
    if rec.branch:   # a runner that ended removed its own; one that is gone left it
        _state, said, _kept = delegate_tree.settled(rec, "stopped", ())
        if said:
            say(said)
    elif rec.workdir != rec.source:
        shutil.rmtree(rec.workdir, ignore_errors=True)
    say(f"stopped {rec.name}")
    return 0


@COMMANDS.command("grade", help="grade a delegate's work, as the lead or another agent; the run's own model never can", options=[
    flag("name"), flag("verdict", choices=delegate_grade.VERDICTS), flag("--critique", required=True, help="what is right and wrong with it"),
    flag("--check", action="append", default=[], metavar='"RUN => SAW"', help="a check you ran and what it showed; without one the grade is unverified"),
    flag("--survived", type=int, default=None, metavar="PERCENT", help="how much of the change survived, 0 to 100"),
    flag("--as", dest="parent", default="", metavar="AGENT", help="the session that grades (default: the session this command runs in)")])
def grade(args: argparse.Namespace) -> int:
    """Add one grade to the run's record, from a session that is neither the run's nor on its model."""
    held = delegate_grade.record(args.name)
    if store.load(args.name) and _view(args.name)[0] in LIVE:
        raise ValueError(f"{args.name} is still running; grade it when it has ended")
    client = Client(node_launch.default_state())
    board = str(held["run"].get("board") or place.at_path(client, Path.cwd()))
    grader = harnessid.launcher(client, board, args.parent)
    graders = [grader.name]
    if args.parent:   # the session the command runs in is judged too, whatever name it grades under
        with contextlib.suppress(Denied, NodeError, ValueError, OSError):
            graders.append(sessions.connect(board=board, client=client).name)
    agents = grader.agents(True)
    if why := delegate_grade.self_grade(held["run"], graders, agents):
        raise Denied(why)
    model = next((each.model for each in agents if each.name == grader.name), "")
    made = delegate_grade.entry((grader.name, model), args.verdict, args.critique, args.check, args.survived)
    store.keep(args.name, grade=made)
    unverified = "" if made["verified"] else "; unverified, since no check was given (--check)"
    say(f"graded {args.name} {args.verdict} as {grader.name}{unverified}")
    return 0


@COMMANDS.command("record", help="print a delegate's grading record: the run, its passes, the self-check and the grades", options=[flag("name")])
def record(args: argparse.Namespace) -> int:
    """The whole record as JSON, fenced: it holds a local model's words."""
    held = json.dumps(delegate_grade.record(args.name), indent=1, sort_keys=True)
    say(nudge_fence.fenced([f"grading record of {args.name}\n{nudge_fence.clean(held)}"]))
    return 0


@COMMANDS.command("summary", help="runs by model and task kind: how they ended, the self-check, the grades, the success rate and the time", options=[
    flag("--model", default="", help="only the models whose file name holds this"), flag("--kind", default="", help="only this task kind"),
    flag("--json", action="store_true", help="print the rows as JSON")])
def summary(args: argparse.Namespace) -> int:
    """The success rate counts only checked grades of other sessions; the self-check is shown beside it and never counted."""
    records = []
    for name in store.names():
        held = delegate_grade.record(name)
        held["run"].setdefault("state", _view(name)[0])
        records.append(held)
    rows = [row for row in delegate_grade.summary(records) if args.model in row["model"] and args.kind in ("", row["kind"])]
    if args.json:
        say(json.dumps(rows, indent=1))
        return 0
    for line in delegate_grade.lines(rows) or ["no run to summarize"]:
        say(line)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
