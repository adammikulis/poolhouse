# Landing

`scripts/land [BRANCH] [--dry-run] [--agent NAME]` puts one branch on the development branch. The development branch is the
one the primary checkout is on (`POOLHOUSE_DEV_BRANCH` overrides it) and never `main`. Whoever made the branch runs it, from
the branch's worktree, as their own board session; a lead may land a worker's branch the same way. The lander that runs is the
primary checkout's `scripts/land`: a copy in a branch's worktree hands over to it before importing anything from the branch,
and the run stops unless the primary checkout's `scripts/land*` and `scripts/affected*` files are byte for byte those of
`origin/<dev>` (a branch's own lander never decides whether its own checks run).

## What it does, in order

0. **Preconditions.** The primary checkout is on the development branch; the branch has a clean worktree with no merge or
   rebase in progress; the repository has an `origin`; the board answers and the session is above the lowest model tier.
   The landing lock (`<git-common-dir>/land/runner.lock`, an `flock`, the same file the runtime cutover waits on) is taken
   without waiting, and so is the claim on the development branch. A second lander is refused with the name, branch, step and
   start time of the first.
1. **Sync.** `git fetch origin <dev>`, then `git ls-remote` asks origin which commit it holds; the remote-tracking ref in full
   (`refs/remotes/origin/<dev>`) must be that commit or the run stops, and every later step uses that commit. The local
   development branch fast-forwards to it (a local commit that origin lacks stops the run, naming it). A commit already on `origin/<dev>` only gets the clean-up of step 4. A branch behind
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
   suite: for a code diff it prints `the full suite runs on GitHub for this commit`, and the `ci` workflow runs it on the
   pushed commit ("The CI run" below). Every check runs in a process group of its own with a fixed environment (`PATH`, `HOME`, the board and device
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

## The CI run

The whole suite runs on GitHub, in the `ci` workflow, for each push of the development branch that changes more than
documentation (`paths-ignore` skips `**.md` and `docs/**`). `scripts/land` and a hand push never wait for it. `scripts/test full`
and `scripts/test all` stay available by hand.

`scripts/hooks/pushed` starts `scripts/ci_watch.py SHA` detached after an agent's push of the development branch, on the
person's machine only (`lan.on_the_persons_machine`), when `gh` is installed. The watcher asks `gh run list --commit SHA
--workflow ci` for the push's run (up to three minutes), waits for it with `gh run watch` (at most 100 minutes, longer than the
longest job timeout in `ci.yml`) and reads the result:

- Green posts nothing.
- Red posts one `task` message to the session that pushed (`$POOLHOUSE_WORKSPACE_AGENT` of the pushing process) naming the
  commit, the failing job and the first three failing test names from the end of `gh run view --log-failed`. The failure is work
  for that session or the lead in the same turn. A red run that follows two other red pushes of the branch is also announced as
  `blocked`, once; a green run ends the streak and a cancelled or skipped run counts for nothing.
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
