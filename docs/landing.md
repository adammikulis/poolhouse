# Landing

A worker puts its branch on the development branch itself, with git. The development branch is the one the primary
checkout is on (`POOLHOUSE_DEV_BRANCH` overrides it) and never `main`. A push takes no accept, no claim and no lock.

1. **Prepare.** In the branch's own worktree beside the primary checkout: `git fetch origin`, merge `origin/<dev>`, run the
   change's own tests with `scripts/test all <files>`, commit by named files. `python scripts/hooks/affected_push.py` runs the
   tests the push hook will run, so a red one is found before the push.
2. **Push.** `git fetch origin`, merge `origin/<dev>` again if it moved, then `git push origin HEAD:refs/heads/<dev>` (plain,
   never forced). A push refused because the remote moved is merged and pushed again.
3. **Watch GitHub.** The `ci` workflow runs on the pushed commit ("The CI run" below).
4. **Clean up.** `scripts/worktrees sweep` removes the landed worktree and branch (docs/worktrees.md).

## The push hook

`scripts/hooks/pre-push` lets an agent push the development branch, a new `promote/*` snapshot branch and, with the person's
approval, one fast-forward of `main`; it refuses everything else. A person's own terminal push is not checked. For a push of
the development branch `scripts/hooks/pushed` runs these checks on the commits the push adds (those on no remote-tracking ref):

- every Python file the push changes parses;
- `scripts/hooks/no-data-files --range` finds no data or weights file;
- every test the pushed paths can break passes (below);
- no added commit has a standing reject: `poolhouse-workspace land-review BRANCH SHA --verdict reject` by a live session of this
  board (not retired as revoked or forged) stops a push that carries that exact commit, and the same
  reviewer's later accept of it lifts the reject. A commit on top of a rejected commit leaves it in the push; replacing the
  commit (an amend or a rebase gives it a new id) clears it. The node is asked without being started and at most 10 seconds a
  call. A push with no session to ask as, a node that is not running or does not answer, and a reply that cannot be read are each a
  warning on stderr and the push goes on; an error on one commit leaves the rejects found on the others, and a node that stops
  answering ends the questions.

**The tests a push can break.** `scripts/hooks/affected_push.py` (called by `pushed` for the development branch, after the parse
and data-file checks and before any watcher starts) selects test files with `scripts/affected.py`, the selection
`scripts/test quick` makes, from every path the push changes (the net difference from the remote's old tip, or the commits on
no remote-tracking ref): the test files that import or name the path, directly or through one module; the glossary scan for any
file it reads; the switch-table scan (`tests/test_documented_env_vars.py`) for a path it scans; and the tree-wide guards once any
test runs. A deleted module selects the tests that import it. A path no rule can place (`pyproject.toml`, `budgets.json`,
`tests/conftest.py`) runs only the tree-wide guards and is named in a warning; `scripts/test gate` and CI cover it.
A test file that only a bare word in a string or a directory listing reaches (`Selection.loose`) is counted in a warning and left to
CI; the files that import, run or name the path by its path, and the files they reach through such references, run. The hook then
runs `scripts/test full FILES` in the pushing checkout (every test of those files not marked `slow` or `gate`; the slow
tests and the whole-tree scans of `scripts/test gate` are the gate's and CI's) with reuse on, so a file whose tree already passed
for the `full` tier is not run again and the broker admits the rest like any run. The tests run on the checked-out tree, so the pushed commit must be `HEAD`
(`git push origin HEAD:refs/heads/<dev>`).

A failing test is run again on a checkout of the commit the push is measured from (the remote's old tip, in a temporary worktree
that is removed afterwards): one that fails there too was red before the push, is named in a warning and is not held against it.
A test that passes there, and a test the push adds, refuses the push: stderr lists each `FAILED path::test` line and
`run it again with: scripts/test full FILES` for the failing files. Where the old tip is not known to the repository, or cannot be
checked out, every failure is held against the push. Nothing passes because the check could not run: a selection that raises, a push of a commit that is not
`HEAD`, a missing `scripts/test`, and a run that ends with a nonzero status and names no failing test each refuse the push with
the cause and the command for the same selection. The parses of the sources `affected.py` reads are kept between runs in
`affected-references.json` in the common git directory, named by each source's text, so a run parses only what moved: the
selection takes about a second once the file exists and about ten seconds the first time.

A merged worktree whose branch has nothing outside the pushed tip is named as a warning. After a good push the hook starts the CI
watcher and the primary checkout sync, both detached.

## Reviews

`land-review` and `land-trust` are the `poolhouse-workspace` commands for the board's `landing` entries (docs/node.md,
"Landing reviews"). A review is advice: an accept is needed by nothing that pushes the development branch. The
runtime deployment of a commit that is not yet on the development branch (`runtime_unmerged.py`) asks for a standing accept of
that exact commit from a reviewer other than the lead that deploys it.

Every git command the push check runs through `scripts/sealed_git.py` is in one of two contexts. `git` runs in the checkout with
the caller's configuration, for refs, worktrees and the remote; the variables that move the repository or add configuration are
dropped, and it refuses a command that reads commits or trees. `sealed` runs those (`diff-tree`, `rev-list`, `log`, `cat-file`
and the like) in an empty bare repository of its own over the object directory of the checkout, with no configuration, attribute,
driver, hook, replace ref, graft, shallow file or commit graph of the checkout, by full commit id.

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
development branch that changes more than documentation (`paths-ignore` skips `**.md` and `docs/**`) runs only the `gates` job. A push never waits for it. An agent never
runs the whole suite locally; a person's terminal may.

`scripts/hooks/pushed` starts `scripts/ci_watch.py SHA` detached after an agent's push of the development branch, on the
person's machine only (`lan.on_the_persons_machine`), when `gh` is installed. The watcher asks `gh run list --commit SHA
--workflow ci` for the push's run (up to three minutes), waits for it with `gh run watch` (at most 130 minutes, longer than the
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

## Not covered

The hook runs the tests the import graph and the name scans reach; a test that reaches the change in a way neither sees (a
subprocess that runs another script by a computed name, a data file only a runtime reads) is CI's, as are the paths above
that no rule places. A reject is seen only when the board answers. The interpreter that runs the
hook reads `PYTHONPATH` and the other `PYTHON*` variables of the pushing environment. The credential that pushes is whoever runs the
command (docs/github-protection.md).
