#!/usr/bin/env python3
"""Fast-forward the primary checkout to `origin/<dev>` when that is all it takes.

    scripts/primary_sync.py [--dev BRANCH] [--sha SHA] [--wait-s N] [--poll-s N]

The primary moves only when it is on the development branch, its working tree and index are clean (no tracked or untracked
change, no merge, rebase, cherry-pick, revert or bisect under way) and `origin/<dev>` is ahead of it in a straight line. The
one thing it runs is `git merge --ff-only` of that remote branch; it never stashes, checks out, resets or touches a changed
tree, and otherwise prints one plain line and exits 0. With `--sha` it first waits up to `--wait-s` seconds for that commit
to reach origin. A second run while one is going leaves a note and exits; the first run goes round again for it.
`scripts/hooks/pushed` starts it detached after an agent's push of the development branch; `scripts/worktrees sweep` runs it last.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import land_git as lg
import srcpath  # noqa: F401

from poolhouse import lan, lock

REMOTE = "origin"
WAIT_S = 60.0
POLL_S = 1.0
NET_S = 30.0
MERGE_S = 60.0
ROUNDS = 5
LOG_MAX = 100_000
UNDER_WAY = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "REBASE_HEAD", "BISECT_LOG", "rebase-merge", "rebase-apply", "sequencer")


def run(root: Path, *args: str, timeout: float) -> subprocess.CompletedProcess[str]:
    """``git -C root args`` without a prompt, ended after ``timeout`` seconds (a timeout is a failed process)."""
    env = {**lg.clean_env(), "GIT_TERMINAL_PROMPT": "0"}
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, errors="replace", check=False,
                              env=env, stdin=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", f"did not finish in {timeout:.0f} s")


def said(done: subprocess.CompletedProcess[str]) -> str:
    """The first line git printed to stderr, or its exit status."""
    return next((ln.strip() for ln in (done.stderr or done.stdout).splitlines() if ln.strip()), f"exit {done.returncode}")[:200]


def remote_head(root: Path, dev: str) -> str:
    """The id `origin` holds for the development branch, or an empty string when it cannot be asked."""
    done = run(root, "ls-remote", REMOTE, f"refs/heads/{dev}", timeout=NET_S)
    return done.stdout.split()[0] if done.returncode == 0 and done.stdout.split() else ""


def arrived(root: Path, dev: str, sha: str, wait_s: float, poll_s: float) -> bool:
    """Whether `origin/<dev>` holds ``sha`` or a commit after it, waiting up to ``wait_s`` seconds for the push to finish."""
    end = time.monotonic() + wait_s
    while True:
        head = remote_head(root, dev)
        if head == sha:
            return True
        if head and run(root, "fetch", "--quiet", REMOTE, f"+refs/heads/{dev}:refs/remotes/{REMOTE}/{dev}", timeout=NET_S).returncode == 0 \
                and lg.is_ancestor(root, sha, head):
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(poll_s)


def in_the_way(path: Path, dev: str) -> str:
    """The plain reason the checkout at ``path`` is not to be moved, or an empty string when it may be."""
    branch = run(path, "symbolic-ref", "--short", "-q", "HEAD", timeout=NET_S).stdout.strip()
    if dev in lg.PROTECTED:
        return f"{dev} is the owner's"
    if not branch or branch != dev:
        return f"the primary checkout is on {branch or 'no branch'}, not {dev}"
    git_dir = Path(lg.out(path, "rev-parse", "--path-format=absolute", "--git-dir"))
    busy = next((name for name in UNDER_WAY if (git_dir / name).exists()), "")
    if busy:
        return f"the primary checkout has a merge, rebase or similar under way ({busy})"
    status = run(path, "status", "--porcelain", "--untracked-files=all", timeout=NET_S)
    if status.returncode:
        return f"the primary checkout's state could not be read ({said(status)})"
    if status.stdout.strip():
        return "the primary checkout has changes"
    return ""


def once(root: Path, dev: str) -> str:
    """Fetch the development branch and fast-forward the primary checkout when allowed; the one line that says what happened."""
    path = lg.primary(root).path
    why = in_the_way(path, dev)
    if why:
        return f"primary-sync: left alone: {why}"
    fetched = run(path, "fetch", "--quiet", REMOTE, f"+refs/heads/{dev}:refs/remotes/{REMOTE}/{dev}", timeout=NET_S)
    if fetched.returncode:
        return f"primary-sync: left alone: {REMOTE} could not be fetched ({said(fetched)})"
    ref = f"refs/remotes/{REMOTE}/{dev}"
    head, target = lg.out(path, "rev-parse", "HEAD"), lg.out(path, "rev-parse", ref)
    if head == target or lg.is_ancestor(path, target, head):
        return f"primary-sync: the primary checkout is at {head[:12]}, level with {REMOTE}/{dev} or ahead of it"
    if not lg.is_ancestor(path, head, target):
        return f"primary-sync: left alone: the primary checkout and {REMOTE}/{dev} have each moved on ({head[:12]} and {target[:12]})"
    merged = run(path, "merge", "--ff-only", "--quiet", ref, timeout=MERGE_S)
    if merged.returncode:
        return f"primary-sync: left alone: the fast-forward was refused ({said(merged)})"
    return f"primary-sync: the primary checkout moved from {head[:12]} to {target[:12]}"


def development(root: Path) -> str:
    """$POOLHOUSE_DEV_BRANCH, else the branch the primary checkout is on (empty when it is on none)."""
    return os.environ.get("POOLHOUSE_DEV_BRANCH") or lg.primary(root).branch


def follow_origin(root: Path) -> str:
    """:func:`sync` of the development branch for a caller that must not fail on it; the line to print."""
    try:
        dev = development(root)
        return sync(root, dev) if dev else "primary-sync: left alone: the primary checkout is on no branch"
    except (RuntimeError, OSError) as why:
        return f"primary-sync: left alone: {why}"


def sync(root: Path, dev: str) -> str:
    """Run :func:`once` under the lock, one line per round; a run that finds the lock held leaves a note for the holder and returns at once."""
    common = lg.common_dir(root)
    again, lines = common / "primary-sync.again", []
    again.touch()
    for _ in range(ROUNDS):
        try:
            with lock.only_one(common / "primary-sync.lock", wait=False, announce=lambda _: None):
                again.unlink(missing_ok=True)
                lines.append(once(root, dev))
        except lock.Busy:
            lines.append("primary-sync: another sync is running and will take this one in")
            break
        if not again.exists():
            break
    return "\n".join(lines)


def start(sha: str, dev: str, repo: Path) -> bool:
    """Start a detached sync that waits for ``sha`` to reach origin, on the person's machine; True when it started."""
    if not lan.on_the_persons_machine():
        return False
    primary = lg.primary(repo).path
    log_path = lg.common_dir(repo) / "primary-sync.log"
    if log_path.exists() and log_path.stat().st_size > LOG_MAX:
        log_path.write_bytes(b"")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    with log_path.open("ab") as log:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--dev", dev, "--sha", sha], cwd=primary, env=env,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return True


def main(argv: list[str]) -> int:
    """Parse the command line, wait for the commit if one is named, sync and print the line."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dev", default="", help="the development branch (default: the branch the primary checkout is on)")
    parser.add_argument("--sha", default="", help="the full id of a commit to wait for on origin before syncing")
    parser.add_argument("--wait-s", type=float, default=WAIT_S, help="seconds to wait for that commit")
    parser.add_argument("--poll-s", type=float, default=POLL_S, help="seconds between asking origin")
    args = parser.parse_args(argv)
    root = Path.cwd()
    try:
        dev = args.dev or development(root)
        if not dev:
            print("primary-sync: left alone: the primary checkout is on no branch")
            return 0
        if args.sha and not arrived(root, dev, args.sha, args.wait_s, args.poll_s):
            print(f"primary-sync: left alone: {args.sha[:12]} did not reach {REMOTE}/{dev} in {args.wait_s:.0f} s")
            return 0
        print(sync(root, dev), flush=True)
    except (RuntimeError, OSError) as why:
        print(f"primary-sync: left alone: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
