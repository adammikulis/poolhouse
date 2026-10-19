#!/usr/bin/env python3
"""Watch the GitHub Actions `ci` run of a pushed commit and tell the session that pushed it when the run fails.

    scripts/ci_watch.py SHA [--dev BRANCH] [--agent NAME]

A green run posts nothing. A red run posts one task message to the pushing session (`--agent`, else
$POOLHOUSE_WORKSPACE_AGENT) naming the failing job and the first failing tests, and a run that is still red two pushes
later is also announced as blocked. Without gh, without a login or with Actions off it prints one line and exits 0.
A second call for the same commit prints the first result. `scripts/hooks/pushed` starts it detached after an agent's push.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import land_git as lg
import srcpath  # noqa: F401

from poolhouse import lan, lock

WORKFLOW = "ci"
WAIT_S = 180
POLL_S = 10
RUN_TIMEOUT_S = 100 * 60
LOG_TAIL = 400
TESTS_NAMED = 3
BLOCKED_AFTER = 3
KEPT = 50
RED = frozenset({"failure", "timed_out", "startup_failure"})
FAILED_TEST = re.compile(r"(?:^|\s)(?:FAILED|ERROR) (\S+\.py\S*)")
SHA = re.compile(r"[0-9a-f]{40}")


class Skipped(Exception):
    """The run cannot be watched; the message is the one plain line that says why."""


class Gh:
    """The gh command run in one checkout."""

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd

    def __call__(self, *args: str, timeout: float = 120.0) -> str:
        """Standard output of ``gh args``; ``Skipped`` when gh is missing, fails or does not answer in time."""
        try:
            done = subprocess.run(["gh", *args], cwd=self.cwd, capture_output=True, text=True, errors="replace", check=False,
                                  stdin=subprocess.DEVNULL, timeout=timeout)
        except FileNotFoundError:
            raise Skipped("gh is not installed") from None
        except subprocess.TimeoutExpired:
            raise Skipped(f"gh {' '.join(args[:2])} did not answer in {timeout:.0f} s") from None
        if done.returncode:
            said = next((ln.strip() for ln in done.stderr.splitlines() if ln.strip()), f"exit {done.returncode}")
            raise Skipped(f"gh {' '.join(args[:2])} failed: {said[:200]}")
        return done.stdout

    def json(self, *args: str, timeout: float = 120.0) -> object:
        """The JSON ``gh args`` prints; ``Skipped`` when it is not JSON."""
        try:
            return json.loads(self(*args, timeout=timeout) or "null")
        except ValueError:
            raise Skipped(f"gh {' '.join(args[:2])} did not answer with JSON") from None


def state_dir(repo: Path) -> Path:
    """The folder under the git directory of the repository that holds one record per watched commit."""
    return lg.common_dir(repo) / "ci-watch"


def load(state: Path, sha: str) -> dict:
    """The record of ``sha``, or an empty dict."""
    try:
        return dict(json.loads((state / f"{sha}.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def records(state: Path) -> list[dict]:
    """Every record in ``state``."""
    return [r for r in (load(state, p.stem) for p in state.glob("*.json")) if r]


def save(state: Path, record: dict) -> None:
    """Write ``record`` and drop the oldest records past ``KEPT``."""
    path = state / f"{record['sha']}.json"
    scratch = path.with_suffix(".tmp")
    scratch.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    scratch.replace(path)
    old = sorted(state.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[KEPT:]
    for stale in old:
        for name in (stale, stale.with_suffix(".lock"), stale.with_suffix(".log")):
            name.unlink(missing_ok=True)


def find_run(gh: Gh, sha: str, wait_s: float, poll_s: float) -> dict | None:
    """The newest `ci` run of the push of ``sha``, waiting up to ``wait_s`` for it to appear; None when none does."""
    end = time.monotonic() + wait_s
    while True:
        rows = gh.json("run", "list", "--commit", sha, "--workflow", WORKFLOW, "--limit", "20",
                       "--json", "databaseId,status,conclusion,createdAt,headSha,event,url")
        mine = [r for r in rows or [] if isinstance(r, dict) and r.get("headSha") == sha and r.get("event") == "push"]
        if mine:
            return max(mine, key=lambda r: str(r.get("createdAt", "")))
        if time.monotonic() >= end:
            return None
        time.sleep(poll_s)


def ended(gh: Gh, run: dict, poll_s: float) -> dict:
    """The run once it has completed, with its jobs; ``Skipped`` when it is still going after ``RUN_TIMEOUT_S``."""
    number = str(run["databaseId"])
    if run.get("status") != "completed":
        try:
            gh("run", "watch", number, "--interval", str(max(1, int(poll_s))), timeout=RUN_TIMEOUT_S)
        except Skipped as why:
            raise Skipped(f"run {number} is not finished ({why}); no longer watching it") from None
    view = gh.json("run", "view", number, "--json", "status,conclusion,jobs,url,createdAt")
    if not isinstance(view, dict) or view.get("status") != "completed":
        raise Skipped(f"run {number} is not finished; no longer watching it")
    return {**run, **view}


def outcome_of(conclusion: str) -> str:
    """``green`` for a success, ``red`` for a failure, otherwise ``other`` (cancelled, skipped, neutral)."""
    return "green" if conclusion == "success" else "red" if conclusion in RED else "other"


def failing(gh: Gh, run: dict) -> tuple[str, list[str]]:
    """The first failing job of the run and the first failing test names in the tail of its failed log."""
    jobs = [j.get("name", "") for j in run.get("jobs", []) if j.get("conclusion") in RED]
    try:
        log = gh("run", "view", str(run["databaseId"]), "--log-failed", timeout=300).splitlines()[-LOG_TAIL:]
    except Skipped:
        log = []
    tests = list(dict.fromkeys(FAILED_TEST.findall("\n".join(log))))[:TESTS_NAMED]
    return (jobs[0] if jobs else ""), tests


def observe(gh: Gh, sha: str, dev: str, wait_s: float, poll_s: float) -> dict:
    """Wait for the run of ``sha`` to end and describe it; ``Skipped`` when there is nothing to report."""
    run = find_run(gh, sha, wait_s, poll_s)
    if run is None:
        raise Skipped(f"no `{WORKFLOW}` run for {sha[:12]} appeared (a push that changes only documentation starts none)")
    run = ended(gh, run, poll_s)
    outcome = outcome_of(str(run.get("conclusion", "")))
    job, tests = failing(gh, run) if outcome == "red" else ("", [])
    line = f"ci {outcome} for {sha[:12]} on {dev}: run {run['databaseId']} {run.get('conclusion') or 'ended'}"
    return {"sha": sha, "dev": dev, "run": run["databaseId"], "url": run.get("url", ""), "created": str(run.get("createdAt", "")),
            "outcome": outcome, "job": job, "tests": tests, "line": line}


def red_streak(state: Path, record: dict) -> int:
    """How many pushes of this branch up to ``record`` ended red in a row; a cancelled or skipped run counts for nothing."""
    rows = sorted((r for r in records(state) if r.get("dev") == record["dev"] and r.get("outcome") in ("red", "green")
                   and r["created"] <= record["created"]), key=lambda r: r["created"], reverse=True)
    streak = 0
    for row in rows:
        if row["outcome"] != "red":
            break
        streak += 1
    return streak


def message(record: dict) -> str:
    """The plain sentences sent to the session that pushed the failing commit."""
    named = ", ".join(record["tests"]) if record["tests"] else "none found in the end of the log"
    return (f"The GitHub ci run for {record['sha'][:12]} on {record['dev']} failed in job {record['job'] or 'unknown'}. "
            f"First failing tests: {named}. Fix it in a new commit in this turn. Run: {record['url']}")


def report(record: dict, state: Path, repo: Path, agent: str) -> None:
    """Post the failure to the pushing session, and the blocked announcement when this is the third red push in a row."""
    from poolhouse.board import session
    from poolhouse.workspace import board_cli

    s = session.connect(cwd=repo, agent=agent)
    board_cli.delivered(s, s.name, "task", message(record), subject=f"ci red {record['sha'][:12]}")
    if red_streak(state, record) == BLOCKED_AFTER:
        first = record["tests"][0] if record["tests"] else record["job"] or "unknown"
        board_cli.announced(s, "blocked", f"ci on {record['dev']} is red for {BLOCKED_AFTER} pushes in a row, now {record['sha'][:12]}: {first}"[:190])


def tell(record: dict, state: Path, repo: Path, agent: str) -> tuple[int, str]:
    """Post a red record to the pushing session and mark it told; the exit status and the line."""
    try:
        report(record, state, repo, agent)
    except Exception as why:  # noqa: BLE001 -- any board failure is one plain line
        return 1, f"ci-watch: {record['line']}; the board did not take the message: {why}"
    save(state, {**record, "told": True})
    return 0, record["line"]


def watch(sha: str, dev: str, agent: str, repo: Path, timing: tuple[float, float] = (WAIT_S, POLL_S)) -> tuple[int, str]:
    """Watch ``sha``: the exit status and the one line saying what the run did; a commit watched before gives the first result."""
    state = state_dir(repo)
    state.mkdir(parents=True, exist_ok=True)
    try:
        with lock.only_one(state / f"{sha}.lock", timeout=RUN_TIMEOUT_S + timing[0] + 60, announce=lambda _: None):
            record = load(state, sha)
            if not record:
                try:
                    record = observe(Gh(repo), sha, dev, *timing)
                except Skipped as why:
                    return 0, f"ci-watch: {why}"
                record["told"] = record["outcome"] != "red"
                save(state, record)
            if record["told"]:
                return 0, record["line"]
            return tell(record, state, repo, agent)
    except lock.Busy as busy:
        return 0, f"ci-watch: {busy}"


def start(sha: str, dev: str, repo: Path) -> bool:
    """Start a detached watcher of ``sha`` on the person's machine when gh is installed; True when it started."""
    if not lan.on_the_persons_machine():
        return False
    if shutil.which("gh") is None:
        print("pushed: the ci run is not watched: gh is not installed", file=sys.stderr)
        return False
    state = state_dir(repo)
    state.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    with (state / f"{sha}.log").open("ab") as log:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), sha, "--dev", dev], cwd=repo, env=env,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return True


def main(argv: list[str]) -> int:
    """Parse the command line, watch the commit and print the line."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sha", help="the full id of the pushed commit")
    parser.add_argument("--dev", default="", help="the development branch it was pushed to (default: the branch of this checkout)")
    parser.add_argument("--agent", default="", help="the session to tell (default: $POOLHOUSE_WORKSPACE_AGENT)")
    parser.add_argument("--wait-s", type=float, default=WAIT_S, help="seconds to wait for the run to appear")
    parser.add_argument("--poll-s", type=float, default=POLL_S, help="seconds between asking GitHub")
    args = parser.parse_args(argv)
    if not SHA.fullmatch(args.sha):
        print("ci-watch: give the full 40-character id of the commit")
        return 2
    repo = Path.cwd()
    code, line = watch(args.sha, args.dev or lg.out(repo, "branch", "--show-current"), args.agent, repo, (args.wait_s, args.poll_s))
    print(line, flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
