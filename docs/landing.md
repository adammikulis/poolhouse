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
not write it. The owner is never required. One command records every verdict on the exact commit that was read, and it
is the only way an agent posts the `lead-review` status:

```sh
scripts/lead-review <PR> <SHA> --show
scripts/lead-review <PR> <SHA> --verdict accept|reject --note "what was read and why" [--finding "SEVERITY: what"]...
```

It reviews a pull request into the development branch or `main`. A `critical`, `high` or `medium` finding blocks: a
verdict with one is a reject, and a reject names one. A `low` finding is a follow-up, recorded with an accept. It
accepts nothing for an author the description names, for an agent that a subagent author started, or from a subagent
that is not above a subagent author (a main session may review any subagent's work); a pull request that names no author
is accepted only when a bot opened it. It records the `land-review` verdict on the board, posts the note, the findings
and what the change needs as a comment on the pull request (the repository is public) and sets the `lead-review` commit
status. After an accept it prints the one merge command, `gh pr merge <PR> --merge ... --match-head-commit <SHA>`.

### Which review a change needs

`poolhouse.workspace.review_depth` holds the one map from changed files to the review they need, and `--show` prints
the answer with its reasons and what the change weakens in guard tests. A change that needs an attack review is
accepted, and merged or pushed to `main`, only with an accepting attack verdict on its exact head:

```sh
scripts/lead-review <PR> <SHA> --attack --verdict accept|reject --note "what was tried" [--finding "SEVERITY: what"]...
```

The attack reviewer is no author of the change, no agent an author started and not the one that merges. Its accept sets
no status: the ordinary accept above follows it, from another agent. An attack review never blocks a push, a fix or a
test (docs/agent-policy.md, "Attack first").

### Standing rejects

An accept is refused, and `lead-review` set to failed, while another reviewer's reject of any commit the pull request
would merge stands; a board or a commit that cannot be read refuses it too. The reviewer who rejected clears it by
accepting that commit (`poolhouse-workspace land-review BRANCH COMMIT --verdict accept`), or, when it is the one
accepting the new head, by that accept. Once its session has ended, another reviewer answers the reject of an earlier
commit by naming it with `--resolves COMMIT` (an attack reject: with `--attack`). A rejected head is answered by a new
commit.

### The merge

The guard (`poolhouse.workspace.release_check`) allows an agent that exact command, alone on its line, when the pull
request targets the development branch, its head is `<SHA>`, the `lead-review` status there passed, no reject of that
head stands and any attack verdict it needs is recorded; into `main`, `<SHA>` also needs a standing `land-review` accept
in a clean checkout. Every other form of `gh pr merge` is refused here, as is a pull request `gh` cannot read. No agent
skips a required check: `gh pr merge --admin` and a write to the merge API are refused. If `gh` reports that it could
not delete the local branch because a worktree holds it, the merge is done: run `scripts/worktrees sweep`, then the same
command again. These rules hold in a repository whose base branch carries `scripts/hooks/primary-only`; elsewhere a
merge needs a passed `lead-review` status into development, and a standing accept of the checkout's commit otherwise.

**What the status is not.** Every agent uses the owner's GitHub sign-in, so GitHub cannot take a formal approval and
nothing there stops an agent from setting a status by hand, which the process forbids; the authors are the names the
description gives. `lead-review` records the process. It is not a security boundary, and an author still runs its own
checks.

## After a merge

A commit that fixes a defect traced to a merge carries the trailer `Fixes-merge: #N` (or a commit of that merge); a
revert keeps git's `This reverts commit SHA` line, or carries `Reverts-merge: #N`. `scripts/review-report` reads the
review comments of merged pull requests and those trailers and reports rejects per merged change, defects found after
merge, reverts and review time.

## The push hook

`scripts/hooks/pre-push` is a fast ref guard. An agent can push a branch forward. A push to `main` passes only with a
standing review (docs/agent-policy.md, "The owner's `main`"); tags, other refs, deletions, unknown commit objects and
non-fast-forward updates refuse. Every input record is checked, including a forbidden ref accompanying an otherwise valid
push. That the development branch takes work only by pull request is for a rule GitHub holds on that branch to enforce,
not for the hook.

The hook does not run tests, builds, Python/data scans, board review queries, worktree scans or CI watchers. Run focused
tests for the change separately; CI runs the quick checks on pull requests into development and broader suites on pull
requests into main, main and nightly schedules. The required `gates` job of a pull request fails on a changed Python
file that does not parse (`scripts/sealed_git_parse.py`) and on a data or weight file (`scripts/hooks/no-data-files`);
`scripts/lead-review` holds a standing reject ("Standing rejects").

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
