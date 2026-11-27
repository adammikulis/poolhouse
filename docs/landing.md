# Landing

Work lands on the development branch by GitHub pull request; there is no landing script. The development branch is the
one the primary checkout is on (`POOLHOUSE_DEV_BRANCH` overrides it) and never `main`.

1. **Prepare.** In the branch's own worktree beside the primary checkout: `git fetch origin`, merge `origin/<dev>`, run the
   change's focused tests with explicit `scripts/test` selectors, and commit by named files. Avoid repeated unaffected checks.
2. **Open a pull request.** `git push origin HEAD` (plain, never forced), then `gh pr create --base <dev>`. The
   description holds one `Author-agent: <name>` line for each agent that wrote the change. The author never merges it.
3. **Review.** "Review of a pull request" below. A new commit needs a new review.
4. **Merge.** The reviewer waits for the required checks and runs the printed command.
5. **Clean up.** `git fetch origin`, then `scripts/worktrees sweep`, which removes the landed worktree and branch and
   fast-forwards the primary checkout (docs/worktrees.md).

## Review of a pull request

The agent that spawned the author reviews the change; a main agent's own pull request is reviewed by an agent that did
not write it. The owner is never required. One command records the verdict on the exact commit that was read:

```sh
scripts/lead-review <PR> <SHA> --verdict accept|reject --note "what was read and why"
```

It refuses a pull request that is not open into the development branch, or whose head is no longer `<SHA>`. It accepts
nothing for an author the description names, for an agent that a subagent author started, or from a subagent that is not
above a subagent author (a main session may review any subagent's work); a pull request that names no author is accepted
only when a bot opened it. It then records the `land-review` verdict on the board, posts the note as a comment on the pull
request (the repository is public) and sets the `lead-review` commit status on that commit. After an accept it prints:

```sh
gh pr merge <PR> --merge --delete-branch --match-head-commit <SHA>
```

The guard (`poolhouse.workspace.release_check`) allows an agent that exact command, alone on its line, when the pull
request targets the development branch, its head is `<SHA>` and the `lead-review` status there passed. `--admin` may be
added to use the administrator's pull-request bypass when a required check is broken by the base branch itself. Any other
`gh pr merge`, a pull request into another branch and one `gh` cannot read keep the rule for `main`: a standing
independent `land-review` accept of the checkout's commit and a clean checkout. If `gh` reports that it could not delete
the local branch because a worktree holds it, the merge is done: run `scripts/worktrees sweep`, then the same command again.

**What the status is not.** Every agent uses the owner's GitHub sign-in, so GitHub cannot take a formal approval and any
agent could set a status by hand; the authors are the names the description gives. `lead-review` records the process. It is
not a security boundary. The attack pass (docs/agent-policy.md, "Attack first") stays advice, and an author still runs its
own checks.

## The push hook

`scripts/hooks/pre-push` is a fast ref guard. An agent can push a branch forward. A push to `main` passes only with a
standing review (docs/agent-policy.md, "The owner's `main`"); tags, other refs, deletions, unknown commit objects and
non-fast-forward updates refuse. Every input record is checked, including a forbidden ref accompanying an otherwise valid
push. That the development branch takes work only by pull request is for a rule GitHub holds on that branch to enforce,
not for the hook.

The hook does not run tests, builds, Python/data scans, board review queries, worktree scans or CI watchers. Run focused
tests for the change separately; CI runs the quick checks on pull requests into development and broader suites on pull
requests into main, main and nightly schedules.

`python scripts/hooks/affected_push.py` remains an explicit affected-test check when useful. It is not invoked by
Git's push hook; the CI job `affected` runs it on a pull request into development. Existing pre-commit checks and the
plan's native proof requirements remain separate.

## Reviews

`land-review` is the `poolhouse-workspace` command for the board's `landing` entries (docs/node.md, "Landing reviews");
`device-trust` names the devices whose `task` and `grant` entries this device counts. A merge into `main` asks for a
standing accept on the board; a merge into development asks for the pull request's `lead-review` status. The
runtime deployment of a commit that is not yet on the development branch (`runtime_unmerged.py`) asks for a standing accept of
that exact commit from a reviewer other than the lead that deploys it.

## The primary checkout follows origin

After publishing, agents fetch and fast-forward clean development checkouts to stay in sync. The optional explicit
validation helper `scripts/hooks/pushed` starts
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

The whole suite runs on GitHub, in the `ci` workflow, for pull requests into `main`, pushes of `main`, the nightly
schedule and manual runs. It is not a condition of development:

| Event | Jobs |
| --- | --- |
| Pull request into development | `gates`, `privacy`, `licenses` (required by the branch's rule, with `lead-review`), `windows` and `affected` (informational); `shards` and `test` report as skipped |
| Push of development (a merged pull request) | `gates`, `privacy`, `licenses`; a change of only `**.md` and `docs/**` starts none |
| Pull request into `main`, push of `main` | the whole suite |

The `redteam`, `pool-check` and `release-dry-run` workflows run on pull requests into `main` and their schedules.
`affected` runs the tests the pull request's changes reach on macOS (`scripts/hooks/affected_push.py`). No rule requires
it. An agent never runs the whole suite locally; a person's terminal may.

When explicitly run, `scripts/hooks/pushed` starts `scripts/ci_watch.py SHA` detached for the proposed development commit, on the
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

## Validation scope

The ref guard does not certify commit contents or test results. Authors validate the behavior they change with
focused checks, inspect CI, and complete the actual native proofs required by the implementation plan. An optional
affected-test selection may miss computed subprocess paths or runtime-only inputs; broader CI supplements it.
Development publication does not wait for a designated App holder or another device.
