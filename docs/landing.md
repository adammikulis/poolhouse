# Landing

`scripts/land [BRANCH] [--dry-run] [--agent NAME]` puts one branch on the development branch. The development branch is the
one the primary checkout is on (`POOLHOUSE_DEV_BRANCH` overrides it) and never `main`. Whoever made the branch runs it, from
the branch's worktree, as their own board session; a lead may land a worker's branch the same way. The lander that runs is the
primary checkout's `scripts/land`: a copy in a branch's worktree hands over to it before importing anything from the branch,
and the run stops unless the primary checkout's lander files (`scripts/land*`, `scripts/affected*` and every `scripts/` module they
import) are byte for byte those of `origin/<dev>` (a branch's own lander never decides whether its own checks run).

## What it does, in order

0. **Preconditions.** The primary checkout is on the development branch; the branch has a clean worktree with no merge or
   rebase in progress; the repository has an `origin`; the board answers and the session is above the lowest model tier.
   A lock of the branch (`<git-common-dir>/land/branch-<hash>.lock`) is taken without waiting for the whole run, so a second
   lander of the same branch is refused naming the first. Steps 1 to 3 hold no repository lock and no claim: any number of
   branches sync and run their checks at once (the test broker queues the CPU).
1. **Sync.** `git fetch origin <dev>`, then `git ls-remote` asks origin which commit it holds; the remote-tracking ref in full
   (`refs/remotes/origin/<dev>`) must be that commit or the run stops (a difference caused by another lander's push or fetch
   between the two reads is read again, up to three times), and every later step uses that commit. A local development branch
   with a commit that origin lacks stops the run, naming it; it fast-forwards at step 4. The first sync fixes the commit the
   lander in memory came from. A commit already on `origin/<dev>` only gets the clean-up of step 4. A branch behind
   `origin/<dev>` is rebased (merged, when it holds merges) when its diff needs no review. When it does, the accept names an
   exact commit and a rebase would void it, so the lander checks that an accept stands for the tip and builds a merge commit
   M of `origin/<dev>` (first parent) and the tip (second parent) from `git merge-tree --write-tree`, moves the branch to M
   (a fast-forward) and runs the checks on M. The accept stays on the tip; M adds nothing git did not merge by itself. A
   conflict stops the run without touching the branch: rebase onto `origin/<dev>`, resolve, and ask for a new review. A
   run interrupted after the merge leaves the branch at M; the next run steps the branch back to the tip and merges again.
   A conflict of a rebase aborts it and names the files. A `HANDOFF.md` conflict is resolved by the worker (a union merge would
   bring back entries that were deleted).
2. **Review.** `scripts/land_review.py::required` lists the changed paths that are security-adjacent (security, encryption,
   access, identity, trust, sharing, serving, compute; guard, rule and landing code; the test selector and gates;
   `pyproject.toml`, `budgets.json`). A path in an area nobody classified needs review too. Then the node must report an
   independent accept for the exact commit under this branch name: `land_accepts` leaves out the caller, and every session
   that ever held `claim branch BRANCH` or `claim worktree PATH`, whatever branch name a reviewer wrote. A branch no session
   ever claimed gets the lander's session recorded as its creator at the first run (it claims the branch and its worktree and
   releases them), so that session's accept never counts. Any standing reject blocks. `scripts/land --dry-run` prints the
   command the reviewer runs: `poolhouse-workspace land-review BRANCH SHA --verdict accept`.
3. **Checks.** From the diff against `origin/<dev>`: documentation only runs `scripts/budgets` and `git diff-tree --check`; code
   runs `scripts/test gate`; both add `scripts/test all <affected files>` from `affected.select`. The lander never runs the whole
   suite: for a code diff it prints `the full suite runs on GitHub for this commit`, and the `ci` workflow runs it on pull requests,
   on `main` and nightly ("The CI run" below). Every check runs in a process group of its own with a fixed environment (`PATH`, `HOME`, the board and device
   variables, nothing else: no `PYTEST_ADDOPTS`, no caller `PYTHONPATH`, no `POOLHOUSE_LIVE_*`). The tree is hashed before and
   after each check and the run stops if it changed or if an ignored file that is not a build cache is present. `scripts/test`
   keeps its own `ran` or `reused from <id>` per file. The selector follows imports one level (`DEPTH = 1`). The checks run on
   the tree of the tip, identified by the tip and the commit of `origin/<dev>` they ran against (the base).
4. **Land.** Only this step holds the landing lock (`<git-common-dir>/land/runner.lock`, an `flock`, the same file the runtime
   cutover waits on) and the claim on the development branch. A lander that finds the lock held says who holds it and waits up
   to 120 seconds for it, then stops naming the holder; a claim held by another session stops it at once. Under the lock it
   fetches and asks `git ls-remote` again, then:
   - **origin is still the base**: the lander in memory must still be the one `origin/<dev>` holds (a change to a
     lander file since the run began stops it), the branch must still be at the tested commit, the
     tree unchanged and the accept read again; then `git push origin <sha>:refs/heads/<dev>` (plain, never forced; a rejected
     push stops with git's line). The primary checkout fast-forwards, then the worktree and branch are removed. A kill between
     the push and the fast-forward is repaired by running the command again. If the local development branch holds a commit
     that origin lacks, the commit is on origin and the lander says so (exit 6) and leaves that branch alone.
   - **origin moved**: nothing is pushed. The lock and the claim are released and the run goes back to step 1 for the new
     base (a rebase, or the merge of a reviewed tip), then the review check of step 2. The earlier green result stands for the
     new commit, with no run, when every one of these holds; otherwise step 3 runs again, outside the lock:
     origin's new commits and the branch change no path in common; the branch's files are those that were tested, unchanged;
     the branch's diff against the new base calls for the same checks and the same test files; the paths the new commits
     changed are not shared test infrastructure, the lander, `pyproject.toml` or `budgets.json`, are mapped by a selector, and
     reach no test file that ran (the selector of step 3, applied to the new commits' paths);
     origin still descends from the base. A branch with a new commit, a different tip or a test run that is no longer valid is
     never pushed on the earlier result. After three rounds the run stops with `origin/<dev> moved 3 times ...; run land again`.

The whole-tree results (`scripts/test gate`, `scripts/budgets`) and the tree-wide guard tests (`affected.GUARDS`) are not run
again for new upstream paths that pass the list above; the combined-tree gates the lead runs per batch and CI cover a
combination of two changes that no path-level check can see.

One line per run is appended to `<git-common-dir>/land/landings.jsonl`: time, session, branch, commit, outcome, cause, seconds
and the end of the checks' output.

## The push hook

`scripts/hooks/pushed` runs for an agent's push (a person's own terminal push is not checked). For a push of the development
branch it diffs the pushed commit against the commit git read from the remote and passed on stdin, tree to tree, never a local
ref; a remote commit this checkout lacks, or none, makes every file of the pushed commit part of the diff. When `required` marks
the diff, it asks the node for an accept of exactly the pushed commit under a local branch that points at it, and refuses
without one, when the node does not answer or when it cannot load. A pushed merge commit passes on the accept of its second
parent when its first parent is the commit git read from the remote and its tree is the tree `git merge-tree --write-tree`
computes for the two parents; any other tree is unreviewed content and is refused. The lander's own push passes the same hook, run as the
landing agent (`--agent NAME` is passed to git as `POOLHOUSE_WORKSPACE_AGENT`).

The lander, `scripts/land_review.py` and `scripts/hooks/pushed` run every git command through `scripts/land_git.py`, in one of two contexts. `git` runs in the
checkout with the caller's configuration, for refs, worktrees, the remote and the commands that change the checkout (fetch,
merge, rebase, commit-tree, push); the variables that move the repository (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, the
object and namespace variables) or add configuration (`GIT_CONFIG_COUNT` and its keys and values) are dropped, and replace refs
and the grafts file are off. It refuses a command that reads commits or trees (`merge-tree`, `diff-tree`, `diff`, `merge-base`,
`rev-list`, `log`, `show`, `cat-file`, `cherry`, `ls-tree`, `hash-object`). `sealed` runs those: in an empty bare repository
of its own, over the object directory of the checkout, with no configuration of the repository, the user or the system, no
attributes (`info/attributes`, a `.gitattributes` in the worktree, `core.attributesFile`), no merge, diff or text-conversion
driver, no hook, no ref, replace ref, graft, shallow file or commit graph. Its answer depends only on the commits it is given,
which it takes by full id; a name is not looked up. It reads the objects without storing any, except the tree of a merge. The
trees of the review check (`required`, `merged_parents`), the merge of a reviewed tip, the file lists that pick
the checks, `git diff-tree --check`, and the file lists and files that the push check parses and that `no-data-files --range`
reads all come from it; file names are NUL separated, and a path that is not UTF-8 stops the check.

## The primary checkout follows origin

Agents push the development branch directly, so nothing else moves the primary checkout. `scripts/hooks/pushed` starts
`scripts/primary_sync.py --dev BRANCH --sha SHA` detached after an agent's push, on the person's machine only, and
`scripts/worktrees sweep` runs it last. The script waits up to 60 seconds for `git ls-remote origin BRANCH` to show the pushed
commit, fetches, and runs `git merge --ff-only origin/BRANCH` in the primary checkout, only when:

- the primary checkout is on the development branch (`$POOLHOUSE_DEV_BRANCH`, else its own branch; never `main` or `master`);
- `git status --porcelain --untracked-files=all` is empty and no merge, rebase, cherry-pick, revert or bisect is under way;
- the primary is behind `origin/BRANCH` in a straight line.

Otherwise it prints one line saying why and changes nothing: it never stashes, checks out, resets or touches a changed tree.
It is idempotent. It holds `<git-common-dir>/primary-sync.lock` without waiting; a run that finds the lock held leaves
`primary-sync.again` and the holder goes round again (at most five rounds). Output of the detached run is
`<git-common-dir>/primary-sync.log`. It is the only code that moves the primary checkout for an agent, and the one thing
it runs besides that fast-forward is the runtime refresh (`scripts/hooks/runtime-refresh`, docs/install.md, "Triggers"), started
after each sync that waited for a pushed commit (`--sha`) to reach `origin`.

## The CI run

The whole suite runs on GitHub, in the `ci` workflow, for pull requests, `main` and the nightly schedule. A push of the
development branch that changes more than documentation (`paths-ignore` skips `**.md` and `docs/**`) runs only the `gates` job. `scripts/land` and a hand push never wait for it. An agent never
runs the whole suite locally; a person's terminal may.

`scripts/hooks/pushed` starts `scripts/ci_watch.py SHA` detached after an agent's push of the development branch, on the
person's machine only (`lan.on_the_persons_machine`), when `gh` is installed. The watcher asks `gh run list --commit SHA
--workflow ci` for the push's run (up to three minutes), waits for it with `gh run watch` (at most 100 minutes, longer than the
longest job timeout in `ci.yml`) and reads the result:

- Green posts nothing.
- Red posts one `task` message to the session that pushed (`$POOLHOUSE_WORKSPACE_AGENT` of the pushing process) naming the
  commit, the failing job and the first three failing test names from the end of `gh run view --log-failed`. The failure is work
  for that session or the lead in the same turn. A red run that follows two other red pushes of the branch is also announced as
  `blocked`, once; a green run ends the streak and a cancelled or skipped run counts for nothing.
- A run still going 25 minutes after it was created posts one `task` message to the pusher naming each job not finished with its
  state and age; at 35 minutes it posts another and announces `blocked`. Each is posted once per commit and mark. A shard of
  `ci.yml` ends itself after 30 minutes, so a hung shard cannot hold the branch's run queue longer.
- `scripts/status` prints `ok` or `STUCK: ...` first, then the local test broker, the running test jobs and the development
  branch's GitHub runs with per-job states. It flags a GitHub run past 25 minutes, a local run past its estimate by 5 minutes (a
  run with no estimate or one under 15 minutes: past 20 minutes in total) and a queued run older than 5 minutes with nothing
  running. GitHub CI is a signal, never a gate: while githubstatus.com lists an open Actions incident, `scripts/status` reports
  `ok (github degraded: ...)` and `ci_watch` posts no `blocked` announcement. The lead's attention hook adds the line when it is not `ok` (cached 5 minutes, ended after 3 seconds).
- A missing `gh`, no login, Actions off, a push that starts no run (documentation only) or a run still going after the limit
  is one plain line in `<git-common-dir>/ci-watch/SHA.log` and exit 0; the push is never refused. A missing `gh` is also said
  once at the push. If the board does not take a red message the watcher exits 1 and the next call for that commit posts it.
- A second call for the same commit waits for the first, then prints its recorded result (`<git-common-dir>/ci-watch/SHA.json`)
  and posts nothing.

## Exit status

0 landed or already landed; 5 stopped, the last line says why; 6 landed and the local development branch could not follow.

## Not covered

The hook cannot know that tests ran; CI is the backstop. The hook and the review check run from the checkout that holds the
installed hooks, and an uncommitted edit of them there is not detected; `scripts/land` runs the review check of the worktree it
lands, so the hook is where it is enforced. The node and board the hook asks are the ones the environment names
(`POOLHOUSE_HOME`, `POOLHOUSE_BOARD`), and the interpreter that runs it reads `PYTHONPATH` and the other `PYTHON*` variables of
the pushing environment. The credential that pushes is whoever runs the command
(docs/github-protection.md). A session that registers a second identity of its own to review is not told apart from a
reviewer; a child of the creator may review, as section 1 of AGENTS.md allows.
