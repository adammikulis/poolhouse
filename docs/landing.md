# Landing

`scripts/land [BRANCH] [--dry-run] [--agent NAME]` puts one branch on the development branch. The development branch is the
one the primary checkout is on (`POOLHOUSE_DEV_BRANCH` overrides it) and never `main`. Whoever made the branch runs it, from
the branch's worktree, as their own board session; a lead may land a worker's branch the same way.

## What it does, in order

0. **Preconditions.** The primary checkout is on the development branch; the branch has a clean worktree with no merge or
   rebase in progress; the repository has an `origin`; the board answers and the session is above the lowest model tier.
   The landing lock (`<git-common-dir>/land/runner.lock`, an `flock`, the same file the runtime cutover waits on) is taken
   without waiting, and so is the claim on the development branch. A second lander is refused with the name, branch, step and
   start time of the first.
1. **Sync.** `git fetch origin <dev>`; the local development branch fast-forwards to `origin/<dev>` (a local commit that origin
   lacks stops the run, naming it). A commit already on `origin/<dev>` only gets the clean-up of step 4. A branch behind
   `origin/<dev>` is rebased (merged, when it holds merges) when its diff needs no review; when it does, the run stops: the
   review names an exact commit, so rebase and ask for a new review. A conflict aborts the rebase and names the files.
   A `HANDOFF.md` conflict is resolved by the worker (a union merge would bring back entries that were deleted).
2. **Review.** `scripts/land_review.py::required` lists the changed paths that are security-adjacent (security, encryption,
   access, identity, trust, sharing, serving, compute; guard, rule and landing code; the test selector and gates;
   `pyproject.toml`, `budgets.json`). A path in an area nobody classified needs review too. Then the node must report an
   independent accept for the exact commit under this branch name: `land_accepts` leaves out the caller, and every session
   that ever held `claim branch BRANCH` or `claim worktree PATH`, whatever branch name a reviewer wrote. A branch no session
   ever claimed has no creator of record and stops. Any standing reject blocks. `scripts/land --dry-run` prints the
   command the reviewer runs: `poolhouse-workspace land-review BRANCH SHA --verdict accept`.
3. **Checks.** From the diff against `origin/<dev>`: documentation only runs `scripts/budgets` and `git diff --check`; code
   runs `scripts/test gate`; both add `scripts/test all <affected files>` from `affected.select`, and `scripts/test full` when
   the diff touches shared test infrastructure, a path no selector maps, a deleted module or more than 120 files or 4000
   lines. Every check runs in a process group of its own with a fixed environment (`PATH`, `HOME`, the board and device
   variables, nothing else: no `PYTEST_ADDOPTS`, no caller `PYTHONPATH`, no `POOLHOUSE_LIVE_*`). The tree is hashed before and
   after each check and the run stops if it changed or if an ignored file that is not a build cache is present. `scripts/test`
   keeps its own `ran` or `reused from <id>` per file. The selector follows imports one level (`DEPTH = 1`).
4. **Land.** The branch must still be at the tested commit and the accept is read again; then
   `git push origin <sha>:refs/heads/<dev>` (plain, never forced; a rejected push stops with git's line). The primary
   checkout fast-forwards, then the worktree and branch are removed. A kill between the push and the fast-forward is repaired
   by running the command again. If the local development branch holds a commit that origin lacks, the commit is on origin
   and the lander says so (exit 6) and leaves that branch alone.

One line per run is appended to `<git-common-dir>/land/landings.jsonl`: time, session, branch, commit, outcome, cause, seconds
and the end of the checks' output.

## The push hook

`scripts/hooks/pushed` runs for an agent's push (a person's own terminal push is not checked). For a push of the development
branch whose diff `required` marks, it asks the node for an accept of exactly the pushed commit under a local branch that
points at it, and refuses without one or when the node does not answer. The lander's own push passes the same hook.

## Exit status

0 landed or already landed; 5 stopped, the last line says why; 6 landed and the local development branch could not follow.

## Not covered

The hook cannot know that tests ran; CI is the backstop. The credential that pushes is whoever runs the command
(docs/github-protection.md). A session that registers a second identity of its own to review is not told apart from a
reviewer; a child of the creator may review, as section 1 of AGENTS.md allows.
