"""The lander: one branch onto the development branch, in the order preconditions, sync, review, checks, push, cleanup."""

from __future__ import annotations

import json
import os
import signal
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path

import land_exec as ex
import land_git as lg
import land_review
import queue_doctor

from poolhouse import lock
from poolhouse.board import session
from poolhouse.board.client import Conflict, NodeError
from poolhouse.devbranch import development_branch
from poolhouse.workspace import landing

CLAIM_TTL_S = 7200
RESTART_STEPS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")


@dataclass
class Landing:
    """One run: the branch and its worktree, the primary checkout, the caller's session and where the run is."""

    wt: Path
    top: Path
    common: Path
    dev: str
    branch: str
    s: session.Session
    me: str
    agent: str = ""
    sha: str = ""
    remote: str = ""
    step: str = "starting"
    began: float = field(default_factory=time.time)
    lock_fd: int = -1
    needs_review: str = ""
    reviewed: str = ""

    @property
    def upstream(self) -> str:
        """The remote-tracking ref of the development branch, as it is called in messages."""
        return f"origin/{self.dev}"

    @property
    def tracking(self) -> str:
        """The remote-tracking ref of the development branch in full, so no branch of the same short name stands for it."""
        return f"refs/remotes/origin/{self.dev}"


def say(text: str) -> None:
    """One line of the lander's own report."""
    print(f"land: {text}", flush=True)


def note(L: Landing, step: str) -> None:
    """Record the step in the lock file, where a second lander reads who holds the lock and what it is doing."""
    L.step = step
    if L.lock_fd >= 0:
        text = f"pid {os.getpid()} {L.me} is landing {L.branch} ({step}, since {time.strftime('%H:%M:%S', time.localtime(L.began))})"
        os.ftruncate(L.lock_fd, 0)
        os.lseek(L.lock_fd, 0, os.SEEK_SET)
        os.write(L.lock_fd, text.encode())


def open_landing(root: Path, branch: str, agent: str) -> Landing:
    """The run for ``branch`` from the checkout at ``root``, after the preconditions; ``ex.Stop`` when one fails."""
    dev = development_branch(root)
    top = lg.primary(root)
    branch = branch or lg.out(root, "branch", "--show-current")
    try:
        lg.refuse_protected(dev, branch)
    except ValueError as error:
        raise ex.Stop(str(error)) from None
    if not branch or branch == dev:
        raise ex.Stop(f"name a branch other than {dev}")
    if top.branch != dev:
        raise ex.Stop(f"the primary checkout is on {top.branch or 'a detached head'}, not {dev}")
    mine = lg.tree_of(root, branch)
    if mine is None:
        raise ex.Stop(f"no worktree has {branch} checked out (a branch in a rebase or detached counts as none)")
    if lg.git(mine.path, "remote", "get-url", "origin", check=False).returncode:
        raise ex.Stop("the repository has no remote named origin")
    for state in RESTART_STEPS:
        if Path(lg.out(mine.path, "rev-parse", "--path-format=absolute", "--git-path", state)).exists():
            raise ex.Stop(f"{branch} has a {state} operation in progress; finish or abort it")
    if lg.dirty(mine.path):
        raise ex.Stop(f"the worktree of {branch} has uncommitted or untracked files")
    try:
        s = session.connect(cwd=mine.path, agent=agent)
        me = s.whoami()
    except NodeError as error:
        raise ex.Stop(f"the board does not answer or does not know this session: {error}") from None
    why = landing.eligible(me)
    if why:
        raise ex.Stop(why)
    return Landing(mine.path, top.path, lg.common_dir(mine.path), dev, branch, s, me.name, agent)


@contextmanager
def exclusive(L: Landing) -> Iterator[None]:
    """Hold the landing lock of this repository (the runtime cutover waits on it too) for the block; a second lander is refused naming the holder."""
    path = L.common / "land" / "runner.lock"
    path.parent.mkdir(exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if not lock.take(fd):
            raise ex.Stop(lock.held_by(path) or "another lander holds the lock")
        L.lock_fd = fd
        note(L, "starting")
        yield
    finally:
        if L.lock_fd == fd:
            with suppress(OSError):
                os.ftruncate(fd, 0)
        L.lock_fd = -1
        os.close(fd)


@contextmanager
def dev_claim(L: Landing) -> Iterator[None]:
    """Hold the claim on the development branch for the block, as an integration does."""
    try:
        taken = L.s.claim("branch", L.dev, ttl_s=CLAIM_TTL_S, pid=os.getpid())
    except Conflict as error:
        raise ex.Stop(str(error)) from None
    except NodeError as error:
        raise ex.Stop(f"the claim on {L.dev} was refused: {error}") from None
    try:
        yield
    finally:
        if taken.changed:
            with suppress(NodeError):
                L.s.release("branch", L.dev)


def tip(L: Landing) -> str:
    """The commit the branch is at now."""
    return lg.out(L.wt, "rev-parse", f"refs/heads/{L.branch}^{{commit}}")


def lift_primary(L: Landing) -> str:
    """Fast-forward the local development branch to origin's; the reason it cannot be, or an empty string."""
    local = lg.out(L.top, "rev-parse", f"refs/heads/{L.dev}")
    if local == L.remote:
        return ""
    if lg.is_ancestor(L.top, local, L.remote):
        done = lg.git(L.top, "merge", "--ff-only", L.remote, check=False)
        return (done.stderr.strip().splitlines() or ["fast-forward refused"])[-1] if done.returncode else ""
    ahead = lg.sealed(L.top, "log", "--oneline", f"{L.remote}..{local}").stdout.splitlines()
    return f"local {L.dev} holds commits origin lacks ({'; '.join(ahead[:3])}) and is left alone"


def accept_problem(L: Landing, sha: str) -> str:
    """Why no independent accept of exactly ``sha`` stands for the branch, or an empty string."""
    return landing.standing(L.s, landing.accepts(L.s, L.branch, sha, str(L.wt)))


def conflict_text(L: Landing, files: list[str]) -> str:
    """The line for a reviewed tip that does not merge with origin cleanly."""
    return (f"merging {L.upstream} into {L.branch} conflicts in {', '.join(files[:8]) or 'the branch'}; rebase onto "
            f"{L.upstream}, resolve, and ask for a new review")


def merge_reviewed(L: Landing, first: str) -> None:
    """Merge origin into the reviewed tip as a commit whose first parent is origin and whose second is the tip, so the accept stays on the tip."""
    why = accept_problem(L, L.sha)
    if why:
        raise ex.Stop(f"{L.upstream} moved past {L.sha[:12]}, which needs an independent review ({first}), and {why}; another agent runs: "
                      f"poolhouse-workspace land-review {L.branch} {L.sha} --verdict accept")
    tree, files = lg.merge_tree(L.wt, L.remote, L.sha)
    if not tree:
        raise ex.Stop(conflict_text(L, files))
    merged = lg.out(L.wt, "commit-tree", tree, "-p", L.remote, "-p", L.sha, "-m", f"chore: merge {L.branch} into {L.dev}")
    lg.git(L.wt, "merge", "--ff-only", merged)
    say(f"merged {L.upstream} into {L.branch}: {merged[:12]} keeps the accept of {L.sha[:12]}")
    L.reviewed, L.sha = L.sha, merged


def unmerged(L: Landing, sha: str) -> str:
    """The tip a previous run reviewed, when ``sha`` is that run's merge commit of it and origin; else ``sha``."""
    parent, second = land_review.merged_parents(L.wt, sha)
    left = second and lg.is_ancestor(L.wt, parent, L.remote) and not lg.is_ancestor(L.wt, second, L.remote)
    return second if left else sha


def bring_up_to_date(L: Landing) -> None:
    """Merge origin into a reviewed tip; rebase (or merge, when the branch holds merges) onto origin when no review is needed."""
    needed, _, first = land_review.required(L.wt, land_review.fork_point(L.wt, L.remote, L.sha), L.sha)
    if needed:
        merge_reviewed(L, first)
        return
    merges = lg.sealed(L.wt, "rev-list", "--merges", "--count", f"{L.remote}..{L.sha}").stdout.strip() != "0"
    verb = "merge" if merges else "rebase"
    done = lg.git(L.wt, verb, *(["--no-edit"] if merges else []), L.remote, check=False)
    if done.returncode:
        files = lg.conflicted(L.wt)
        lg.git(L.wt, verb, "--abort", check=False)
        raise ex.Stop(f"{verb} onto {L.upstream} conflicts in {', '.join(files[:8]) or 'the branch'}; resolve it and run land again")
    L.sha = tip(L)


def fetch(L: Landing) -> None:
    """Fetch the development branch and record the commit origin says it holds; stop unless the remote-tracking ref is that commit."""
    fetched = lg.git(L.wt, "fetch", "origin", L.dev, check=False)
    if fetched.returncode:
        raise ex.Stop(f"fetch failed: {(fetched.stderr.strip().splitlines() or ['no output'])[-1]}")
    asked = lg.git(L.wt, "ls-remote", "origin", f"refs/heads/{L.dev}", check=False)
    held = asked.stdout.split()[:1]
    if asked.returncode or not held:
        raise ex.Stop(f"origin does not say which commit it holds for {L.dev}")
    L.remote = held[0]
    local = lg.git(L.wt, "rev-parse", "--verify", "--quiet", f"{L.tracking}^{{commit}}", check=False).stdout.strip()
    if local != L.remote:
        raise ex.Stop(f"{L.upstream} is {local[:12] or 'missing'} here and {L.remote[:12]} on origin; fetch {L.dev} again")


def check_lander(L: Landing) -> None:
    """Stop unless the lander files of the primary checkout are the ones origin's development branch holds."""
    name = lg.lander_problem(L.top, L.remote)
    if name:
        raise ex.Stop(f"{name} in the primary checkout {L.top} differs from {L.upstream}, whose lander is the one that runs; "
                      f"bring {L.dev} there level with {L.upstream} first")


def record_creator(L: Landing) -> None:
    """A branch no session ever claimed gets the caller as its creator of record, so that the caller never counts as its reviewer."""
    if landing.accepts(L.s, L.branch, tip(L), str(L.wt))["creators"]:
        return
    try:
        for kind, key in (("branch", L.branch), ("worktree", str(L.wt))):
            L.s.claim(kind, key, ttl_s=CLAIM_TTL_S)
            L.s.release(kind, key)
    except Conflict as error:
        raise ex.Stop(str(error)) from None
    say(f"no session had claimed {L.branch}: {L.me} is recorded as its creator, so its accept of this branch never counts")


def sync(L: Landing) -> bool:
    """Fetch, level the local development branch and the branch with origin; True when the branch is already on origin."""
    fetch(L)
    check_lander(L)
    L.sha = tip(L)
    already = lg.is_ancestor(L.wt, L.sha, L.remote)
    stepped = L.sha if already else unmerged(L, L.sha)
    if stepped != L.sha:
        L.sha = stepped
        lg.git(L.wt, "reset", "--hard", stepped)
    problem = lift_primary(L)
    if problem and not already:
        raise ex.Stop(problem)
    if not already and not lg.is_ancestor(L.wt, L.remote, L.sha):
        bring_up_to_date(L)
    return already


def review(L: Landing) -> None:
    """Stop unless the diff needs no review or an independent accept stands for exactly this commit."""
    needed, _, first = land_review.required(L.wt, L.remote, L.sha)
    L.needs_review = first if needed else ""
    if not needed:
        return
    accepted = L.reviewed or L.sha
    why = accept_problem(L, accepted)
    if why:
        raise ex.Stop(f"{why} for {accepted[:12]} (first sensitive path {first}); another agent runs: "
                      f"poolhouse-workspace land-review {L.branch} {accepted} --verdict accept")


def recheck(L: Landing, tree: str) -> None:
    """Immediately before the push: the branch, the tree and the accept are what they were when the checks began."""
    if tip(L) != L.sha:
        raise ex.Stop(f"{L.branch} moved while its checks ran")
    ex.verify_tree(L.wt, tree)
    if L.needs_review:
        accepted = L.reviewed or L.sha
        why = accept_problem(L, accepted)
        if why:
            raise ex.Stop(f"the accept for {accepted[:12]} no longer stands: {why}")


def push(L: Landing) -> None:
    """Push exactly the commit that was reviewed and tested, plainly, to the development branch."""
    lg.refuse_protected(L.dev)
    env = lg.clean_env({session.AGENT_ENV: L.agent} if L.agent else None)
    done = lg.git(L.wt, "push", "origin", f"{L.sha}:refs/heads/{L.dev}", check=False, env=env)
    if done.returncode:
        lines = [x for x in done.stderr.splitlines() if x.strip()]
        raise ex.Stop("push refused: " + " | ".join(ex.printable(x.strip()) for x in lines[-3:]))


def cleanup(L: Landing) -> str:
    """Remove the worktree and the branch of a landed commit; the reason one stays, or an empty string."""
    os.chdir(L.top)
    done = lg.git(L.top, "worktree", "remove", str(L.wt), check=False)
    if done.returncode:
        return (done.stderr.strip().splitlines() or ["worktree remove failed"])[-1]
    lg.git(L.top, "worktree", "prune")
    gone = lg.git(L.top, "branch", "-d", L.branch, check=False)
    return "" if gone.returncode == 0 else f"branch -d {L.branch} refused: {(gone.stderr.strip().splitlines() or [''])[-1]}"


def finish(L: Landing, outcome: str) -> tuple[str, str]:
    """After the commit is on origin: level the local development branch, clean up, and say what is left."""
    note(L, "cleaning up")
    try:
        fetch(L)
        warning = lift_primary(L)
    except ex.Stop as stop:
        warning = str(stop)
    left = cleanup(L)
    if left:
        say(f"kept {L.branch}: {left}")
    if warning:
        say(f"WARNING: {outcome} on origin, and {warning}")
    return outcome, warning


def execute(L: Landing) -> tuple[str, str, list[str]]:
    """Land the branch: the outcome, a warning (empty when none) and the end of the checks' output."""
    note(L, "syncing")
    record_creator(L)
    if sync(L):
        say(f"{L.branch} ({L.sha[:12]}) is already on {L.upstream}")
        return (*finish(L, "already landed"), [])
    note(L, "checking the review")
    review(L)
    checks = ex.plan(L.wt, L.remote, L.sha)
    if checks.kind == "none":
        raise ex.Stop(f"nothing to land: {L.branch} changes nothing against {L.upstream}")
    tree = lg.tree_id(L.wt, L.sha)
    ex.verify_tree(L.wt, tree)
    note(L, "running checks")
    queue_doctor.mend(L.wt, say=say)
    tail = ex.run_plan(L.wt, checks.checks, tree)
    note(L, "pushing")
    recheck(L, tree)
    push(L)
    say(f"{L.sha[:12]} is on {L.upstream}")
    return (*finish(L, "landed"), tail)


def show(L: Landing) -> None:
    """A dry run: what landing would check and whether it would be reviewed; nothing changes but the remote-tracking ref."""
    fetch(L)
    check_lander(L)
    L.sha = tip(L)
    if lg.is_ancestor(L.wt, L.sha, L.remote):
        say(f"{L.branch} ({L.sha[:12]}) is already on {L.upstream}")
        return
    L.sha = unmerged(L, L.sha)
    behind = not lg.is_ancestor(L.wt, L.remote, L.sha)
    needed, base, first = land_review.required(L.wt, land_review.fork_point(L.wt, L.remote, L.sha), L.sha)
    how = f"land merges {L.upstream} into it and the accept of this commit stands" if needed else "land rebases it"
    say(f"{L.branch} at {L.sha}" + (f" is behind {L.upstream}; {how}" if behind else ""))
    if behind and needed:
        tree, files = lg.merge_tree(L.wt, L.remote, L.sha)
        if not tree:
            say(conflict_text(L, files))
    if needed:
        found = landing.accepts(L.s, L.branch, L.sha, str(L.wt))
        why = landing.standing(L.s, found)
        say(f"review needed (first sensitive path {first}): " + (why or "an independent accept stands"))
        if why:
            say(f"another agent runs: poolhouse-workspace land-review {L.branch} {L.sha} --verdict accept")
    else:
        say("no independent review needed")
    checks = ex.plan(L.wt, base, L.sha)
    say(f"{checks.kind} diff of {len(checks.changed)} files; checks: {', '.join(c.name for c in checks.checks) or 'none'}"
        + (f"; whole suite because {checks.full}" if checks.full else ""))


def record(L: Landing, outcome: str, cause: str, tail: list[str]) -> None:
    """Append one line about this run to the landings log of the repository."""
    row = {"ts": round(time.time()), "who": L.me, "branch": L.branch, "sha": L.sha, "outcome": outcome, "cause": cause,
           "seconds": round(time.time() - L.began, 1), "tail": tail}
    with (L.common / "land" / "landings.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(row, sort_keys=True) + "\n")


STOPS = (ex.Stop, NodeError, RuntimeError, OSError)


def terminate(*_: object) -> None:
    """Turn SIGTERM or SIGHUP into an exit that runs the cleanups of the run."""
    raise SystemExit(143)


def stop_on_signals() -> None:
    """Make SIGTERM and SIGHUP (the terminal lost) end the run through its cleanups."""
    for number in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, terminate)


def land(root: Path, branch: str, agent: str, dry_run: bool) -> int:
    """Run the lander for ``branch`` from ``root``; the exit status (0 landed or already, 5 stopped, 6 landed with a warning)."""
    stop_on_signals()
    try:
        L = open_landing(root, branch, agent)
        if dry_run:
            show(L)
            return 0
        with exclusive(L), dev_claim(L):
            return run(L)
    except STOPS as stop:
        print(f"land: stopped: {stop}")
        return 5
    except KeyboardInterrupt:
        print("land: stopped: interrupted; run it again to repair or finish")
        return 5


def run(L: Landing) -> int:
    """Execute under the lock and the claim, record the run and return the exit status."""
    outcome, cause, tail, code = "stopped", "", [], 5
    try:
        outcome, cause, tail = execute(L)
        code = 6 if cause else 0
    except STOPS as stop:
        cause, tail = str(stop), getattr(stop, "tail", [])
        print(f"land: stopped: {cause}")
    with suppress(OSError):
        record(L, outcome, cause, tail)
    return code
