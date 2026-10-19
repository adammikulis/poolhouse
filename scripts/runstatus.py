"""The report behind scripts/status: the local test broker, the test jobs and the GitHub runs of the development branch.

Stuck means: a GitHub run still going after 25 minutes; a GitHub run queued for more than 5 minutes while no run of the same
workflow has started after it (one queued behind a running run of its workflow is only flagged after 25 minutes); a local run past
its estimate by 5 minutes (a run without an estimate, or with one under 15 minutes, past 20 minutes in total); a queued local run
older than 5 minutes with nothing running. A missing gh or login is one line in the github section and never an error.

When a GitHub run is flagged, githubstatus.com is read (scripts/github_status.py). An open incident for Actions makes the finding
information, not a verdict: `ok (github degraded: <incident>; ci b3abc57 queued 18 min)`. Without one the verdict gains
`github reports: <status description>; <incident names>`.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import github_status
import srcpath  # noqa: F401
import testslots
from ci_watch import Gh, Skipped, age_s

ROOT = Path(__file__).resolve().parent.parent
GITHUB_S = 25 * 60
OVER_ESTIMATE_S = 5 * 60
LOCAL_TOTAL_S = 20 * 60
QUEUED_S = 5 * 60
GH_TIMEOUT_S = 20.0
JOBS_SHOWN = 3
PROBLEMS_SHOWN = 2
WAITING = frozenset({"queued", "waiting", "pending", "requested"})
ACTIVE = WAITING | {"in_progress"}
JOB_LINE = re.compile(r"^\S+\s+(running|queued)\s")


def minutes(seconds: float) -> str:
    """``seconds`` as whole minutes."""
    return f"{seconds / 60:.0f} min"


def local(now: float) -> tuple[list[str], list[str]]:
    """The broker's runs, one line each, and what is stuck among them."""
    st = testslots.status()
    runs = st["runs"]
    lines = [f"broker: budget {st['budget']} workers, {st['in_use']} in use, {sum(r['state'] == 'running' for r in runs)} run(s) running, "
             f"{sum(r['state'] == 'queued' for r in runs)} queued"]
    problems = []
    idle = not st["running"] and not any(r["state"] == "running" for r in runs)
    for run in runs:
        est, owner = run.get("estimate_s"), run.get("owner") or "person"
        running = run["state"] == "running"
        age = now - float(run.get("started" if running else "enqueued") or now)
        lines.append(f"  {run['state']:<8} {testslots.policy.run_class(run):<11} owner {owner} est {'?' if est is None else f'{est:.0f}s'} "
                     f"age {age:.0f}s; {run['label']}")
        what = f"{run['state']} run {run['label'][:40]} (owner {owner})"
        total = now - float(run.get("enqueued") or now)
        if running and est is not None and age > float(est) + OVER_ESTIMATE_S:
            problems.append(f"{what} is {minutes(age - float(est))} past its estimate")
        elif running and (est is None or float(est) + OVER_ESTIMATE_S <= LOCAL_TOTAL_S) and total > LOCAL_TOTAL_S:
            problems.append(f"{what} has taken {minutes(total)}")
        elif not running and idle and total > QUEUED_S:
            problems.append(f"{what} has waited {minutes(total)} with nothing running")
    return lines, problems


def test_jobs() -> list[str]:
    """The running and queued jobs `scripts/test status` lists."""
    try:
        done = subprocess.run([sys.executable, str(ROOT / "scripts" / "test"), "status"], cwd=ROOT, capture_output=True, text=True,
                              errors="replace", check=False, stdin=subprocess.DEVNULL, timeout=GH_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as why:
        return [f"test jobs: scripts/test status gave no answer ({type(why).__name__})"]
    live = [f"  {ln}" for ln in done.stdout.splitlines() if JOB_LINE.match(ln)]
    return ["test jobs:", *(live or ["  none running"])]


def development_branch() -> str:
    """$POOLHOUSE_DEV_BRANCH, else the branch of the primary checkout."""
    if os.environ.get("POOLHOUSE_DEV_BRANCH"):
        return os.environ["POOLHOUSE_DEV_BRANCH"]
    try:
        out = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=False,
                             timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return next((ln.split("refs/heads/", 1)[1] for ln in out.splitlines() if ln.startswith("branch refs/heads/")), "")


def job_lines(gh: Gh, run: dict) -> list[str]:
    """The jobs of ``run`` not yet finished, with state and age, and how many have finished."""
    view = gh.json("run", "view", str(run["databaseId"]), "--json", "jobs", timeout=GH_TIMEOUT_S)
    jobs = [j for j in (view.get("jobs") if isinstance(view, dict) else None) or [] if isinstance(j, dict)]
    rows = [f"    {j.get('name', '?')}: {j.get('status', '?')} {minutes(age_s(j.get('startedAt')))}" for j in jobs if j.get("status") != "completed"]
    done = len(jobs) - len(rows)
    return [*rows, f"    {done} job{'s' if done != 1 else ''} finished"] if jobs else ["    no jobs yet"]


def queue_note(run: dict, rows: list[dict]) -> tuple[str, bool]:
    """For a run that has not started: ' waiting behind run N' when a run of its workflow is going, and whether it is stuck."""
    age = age_s(run.get("createdAt"))
    same = [r for r in rows if r is not run and r.get("workflowName") == run.get("workflowName")]
    ahead = next((r for r in same if r.get("status") == "in_progress" and str(r.get("createdAt")) < str(run.get("createdAt"))), None)
    if ahead is not None:
        return f"; waiting behind run {ahead['databaseId']}", age > GITHUB_S
    later = any(r.get("status") not in WAITING and str(r.get("createdAt")) > str(run.get("createdAt")) for r in same)
    return "", age > QUEUED_S and not later


def github() -> tuple[list[str], list[str], github_status.Summary | None]:
    """The development branch's recent runs with per-job states for those in progress, what is stuck among them and, when something
    is, what githubstatus.com reports."""
    dev = development_branch()
    gh = Gh(ROOT)
    try:
        rows = gh.json("run", "list", "--branch", dev, "--limit", "20", "--json",
                       "databaseId,status,conclusion,createdAt,workflowName,headSha,url", timeout=GH_TIMEOUT_S) if dev else []
        rows = [r for r in rows or [] if isinstance(r, dict)]
        lines, problems = [f"github: {dev or 'no development branch'}"], []
        active = [r for r in rows if r.get("status") in ACTIVE]
        for n, run in enumerate(active):
            age = age_s(run.get("createdAt"))
            name, sha = run.get("workflowName", "?"), str(run.get("headSha", ""))[:7]
            note, stuck = queue_note(run, rows) if run["status"] in WAITING else ("", age > GITHUB_S)
            if n < JOBS_SHOWN + 2:
                lines.append(f"  {name} {sha} {run['status']} {minutes(age)}; run {run['databaseId']}{note}")
            if stuck and run["status"] in WAITING:
                problems.append(f"github: {name} {sha} queued {minutes(age)}{note.replace(';', ',')}")
            elif stuck:
                problems.append(f"github run {run['databaseId']} ({name}) is {run['status']} after {minutes(age)}")
            if run["status"] == "in_progress" and n < JOBS_SHOWN + 2:
                lines.extend(job_lines(gh, run))
        if len(lines) == 1:
            last = rows[0] if rows else {}
            lines.append(f"  nothing in progress; latest {last.get('workflowName', '?')} {last.get('status', '?')} {last.get('conclusion', '')}".rstrip()
                         if rows else "  no runs")
        return lines, problems, github_status.fetch() if problems else None
    except Skipped as why:
        return [f"github: {why}"], [], None


def report(now: float, jobs: bool = True) -> list[str]:
    """The verdict line followed by the three sections (the test jobs only when ``jobs``)."""
    lines, problems = local(now)
    lines += test_jobs() if jobs else []
    shown, found, said = github()
    lines += shown
    if said is not None and said.actions:
        info = "; ".join(["github degraded: " + "; ".join(said.actions), *(p.removeprefix("github: ") for p in found)])
        lines.append(f"  {info}")
    else:
        info = ""
        problems += found
    head = "ok" if not problems else "STUCK: " + "; ".join(problems[:PROBLEMS_SHOWN]) + (f" (+{len(problems) - PROBLEMS_SHOWN} more)" if len(problems) > PROBLEMS_SHOWN else "")
    if info and not problems:
        head += f" ({info})"
    elif said is not None and not info:
        head += f". {said.line()}"
    return [head, *lines]


def main(argv: list[str]) -> int:
    """Print the report, or only its first line with ``--verdict``."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verdict", action="store_true", help="print only the first line")
    args = parser.parse_args(argv)
    lines = report(time.time(), jobs=not args.verdict)
    print("\n".join(lines[:1] if args.verdict else lines))
    return 0
