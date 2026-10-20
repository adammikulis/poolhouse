# Deleting the old landing machinery, 2026-10-19

The owner ordered the lander (`scripts/land*`), claims as a landing precondition, the creator record, `land_review.required` as a
gate and their tests and docs deleted in one piece. After it a worker pushes the development branch itself with git; a standing
`land-review` reject of a commit still stops a push of that commit; `land-review` accept and reject stay as advice.
Branch `chore/delete-lander`. A non-author reviews it before it is pushed because it removes guards.

## What was deleted

| Kind | Items |
| --- | --- |
| Lander | `scripts/land`, `land_exec.py`, `land_steps.py`, `land_stale.py` (881 lines) |
| Review gate | `scripts/land_review.py` (121 lines): `required`, `sensitive`, the path classifier, `merged_parents`, `fork_point` |
| Sealed git helper | `scripts/land_git.py` renamed `scripts/sealed_git.py`; `lander_set`, `lander_modules`, `lander_problem`, `lander_moved`, `commit_reader`, `imported_names`, `merge_tree`, `tree_id`, `tree_of`, `dirty`, `conflicted`, `lines`, `empty_tree`, `refuse_protected` and `LANDER` deleted |
| Node (`app/poolhouse-node`) | `creators`, `lease::merge::ever_held`, the refusal of a reviewer that held the claim, the `branch` and `worktree` parameters of `land_accepts` (it takes the commit and answers under any branch name) |
| Python | `landing.standing` no longer needs a creator on record; `landing.accepts(session, sha)`; `landing.rejected` is new; the `scripts/land` autoexec entry; `trees.INTEGRATION` (the `land/` tree exemption of `sweep`); the `scripts/land*` protected-path patterns now name `scripts/sealed_git*` (`protectedwrites.py`, `scripts/github-protection`, `.github/CODEOWNERS`) |
| Tests (1,402 lines) | `test_land.py`, `test_land_concurrent.py`, `test_land_plan.py`, `test_land_review_policy.py`, `test_land_stale.py`, `test_land_trust.py`, `test_lander_ends_jobs.py`; `test_land_sealed.py` is cut to `test_sealed_git.py`; `land_support.py` is `git_world.py` without the fake test runner, the lander and the landing log; `runner_lock.py` is `cutover_lock.py` |
| Docs | `docs/landing.md` rewritten (push, push hook, reviews, primary checkout follows origin, CI run); the lander is gone from `docs/agent-rules.md`, `AGENTS.md`, `docs/test-execution.md` ("Landing a batch"), `docs/node.md`, `docs/pool.md`, `docs/worktrees.md`, `docs/heartbeat.md` and the others `grep -rn "scripts/land"` found; `docs/agents-protected.patch` carries the push instructions for the two agent files only a person edits |

There was no hook entry, workflow step, glossary or allowlist entry, red-team coverage row or budget line for the lander:
`.claude/settings.json` and `.github/workflows` never named it, `scripts/redteam_coverage.py --write` changes nothing and
`scripts/budgets --update` reports that the budgets already match the tree (no number fell, none rose).

## What stays, and what `runner.lock` is now

- `scripts/hooks/pushed` (parse check, `no-data-files`, now the standing-reject check, CI watcher, primary sync), `scripts/hooks/pre-push`,
  `scripts/primary_sync.py`, `scripts/ci_watch.py`, `scripts/hooks/stop_landed.py` (a stopping subagent with unpushed work; its
  message now says to merge and push), `land-review` and `land-trust` in `poolhouse-workspace`.
- `land-review` accept is read by one thing: the runtime deployment of a commit that is not on the development branch
  (`runtime_unmerged.py`) still asks for a standing accept from a reviewer other than the deploying lead.
- `land-trust` is not landing: it names the devices whose `task` and `grant` entries this device counts. It stays under its name
  and is a pending item in `HANDOFF.md`.
- `scripts/affected.py` and `affected_rules.py` are the test selector (`scripts/test`, `gatescope`, `test_on`, `testreuse_key`); they stay.
- `runner.lock` was the lander's push lock and the runtime cutover's wait. It is now only the cutover lock:
  `<git-common-dir>/runtime-cutover.lock`, taken by `runtime_git.crossing` while a runtime build or switch crosses the boundary and
  waited on by `runtime_git.until_free`, `node_supervise` and the node launch (`runtime_git.boundary`,
  `worktreerules.cutover_lock`, `tests/cutover_lock.py::CutoverLock`). Nothing about a push takes it.

## What now covers each protection the lander gave

| Protection | Now |
| --- | --- |
| Pushes serialised by the `runner.lock` flock, one lander at a time, `branch-<hash>.lock` | git: origin refuses a push from a stale base; the pusher merges and pushes again (AGENTS.md 4). Two pushes of one branch are two plain pushes. Owner order: a push takes no lock. |
| Combined-tree gate and affected selectors before the push (`scripts/test gate`, budgets, `affected.select`) | The author runs the affected selectors (AGENTS.md 5); the lead runs the combined-tree gate per batch; the GitHub `gates` job runs on every pushed commit and the full suite on pull requests, `main` and nightly; the CI watcher posts a red run to the pusher. Accepted by the owner: the push check no longer asks for a result. |
| Independent accept for security-adjacent paths (`required`, in the lander and in `pushed` until `b9fcd4dcb`) | The attack-first pass (AGENTS.md 1) by a non-author before a security-adjacent design is built; `land-review` as advice; a standing reject at push; the owner's `main` approval; the GitHub `gates` job. Accepted by the owner: "the only permission an agent asks for is merging or pushing main". |
| Creator record (the lander claimed an unclaimed branch for its caller; `standing` refused a branch with no creator; the node dropped reviewers that held the claim) | Removed. A reviewer is excluded only when it is the caller of `land_accepts`. See finding R1. |
| Claim on the development branch and on the branch and worktree as a landing step | Removed. Claims stay for worktrees, ports, source areas and servers (AGENTS.md 3) and are not read by a push. |
| `.git/land/*` locks, `landings.jsonl` (one line per landing: time, session, branch, commit, outcome) | No lock to take. The record of a landing is the commit on `origin/<dev>`, the GitHub run of it and the author, which a push no longer adds to. Accepted: no other audit of who pushed. |
| Activation after landing: fast-forward of the primary checkout, removal of the worktree and branch | `scripts/hooks/pushed` starts `scripts/primary_sync.py` (fast-forward, then the runtime refresh) and warns about a merged worktree; `scripts/worktrees sweep` removes it. |
| The lander of the primary checkout decides (a branch's own lander never runs its own checks) | Moot: no script in a branch decides whether a check runs. `scripts/hooks/pre-push` and `pushed` run from the checkout that holds the installed hooks and are protected paths. |
| Standing reject stops a push | Kept, and now actually checked: `pushed` had no reject check since `b9fcd4dcb` removed the accept check with it, so AGENTS.md "Review scope" described a check that did not exist. `pushed` now asks the node for the reviews of every commit the push adds. |

## Security note

Changed behaviour, attack-first (what a non-author reviewer is asked to break):

- **R1, accepted risk for the owner or a second reviewer.** The runtime deployment of an unmerged commit asked for an accept
  from a reviewer who was not the author, where author meant a session that held `claim branch`, and refused a branch nobody had
  claimed. Now the reviewer only differs from the deploying lead. A subagent that wrote the source can accept its own commit and
  a lead can then deploy it. The gate is no weaker than the merged path: the development branch needs no accept and the runtime
  follows it. If the owner wants author independence there, the missing piece is an author the node can name (the session that
  registered the worktree), not the claim record.
- **Reject check fails open.** A push with no registered session, or a board that does not answer, goes on (a warning on stderr
  when the board does not answer). An agent can also skip the hook with `git push --no-verify`; the Bash guard and the GitHub
  `gates` job are what stop it. A reject is advice.
- **A hostile reviewer can stall a commit.** Any live session of the board above the lowest model tier can post a reject for any
  commit. The pusher makes another commit, or the reviewer replaces its reject with an accept. Before, the reject was also scoped by
  a branch name; it is now by commit, so a reject posted under another name still stops the commit. A session retired as
  `revoked` or `forged` no longer counts.
- **A rejected commit cannot be hidden in a merge.** The check covers every commit the push adds (those on no remote-tracking
  ref), so merging `origin/<dev>` into a rejected branch does not clear the reject. Amending the commit does, as it is another commit.
- **Copied pool or device, replay, forged signature, compromised member:** unchanged. The reviews are entries the node stamps with
  the sender's name and model; only this device's own entries count (`landtrust.rs`, untouched); a replayed review is bound to the
  full commit id; a forged stamp is refused by the node's own checks (`fold::check_local`).
- **Downgrade and takeover by presence:** the lander is gone, so there is no check left to turn off. Anyone who can run `git push`
  with the push credential pushes the development branch; `main` stays behind the person's approval and the origin's ruleset.
- **Approval fatigue:** no prompt is added or removed.
- **Compromised without knowing:** an injected instruction cannot ask a lander to do more than a push, because there is no lander.

## Tests removed because they test deleted behaviour

- `test_land.py`, `test_land_concurrent.py`, `test_land_plan.py`, `test_land_stale.py`, `test_lander_ends_jobs.py`,
  `test_land_trust.py`: the lander's sync, review, checks, push, locks, plan, reuse of a green result and ending of check jobs.
- `test_land_review_policy.py`: the classifier of security-adjacent paths and its coverage of every top-level path.
- In `test_land_sealed.py`: the merge-driver and union-attribute tests of `merge_tree`, the two lander merge tests, the diff-setting
  test of `required`, the forged commit graph test and the non-UTF8 path test of `merged_parents` and `required`, and the `required`
  assertions of the commit-id test. Kept in `test_sealed_git.py`: the unparseable-name and data-file pushes, the sealed and checkout
  git contexts, the shallow file and the audit that only the helper starts git.
- Rust `landing.rs`: the test that a session which held the claim is never counted, and the creators assertions of the other tests.
- `test_harness_exec_chain_more.py` and `test_protected_writes.py`/`test_github_protection.py`: the `scripts/land` and
  `scripts/land_steps.py` path entries (`scripts/sealed_git.py` replaces them where a protected path is listed).
- `test_trees.py`: the `land/batch` integration tree that `sweep` kept.

Added: five tests of the reject check in `test_sealed_git.py` (a reject refuses and the same reviewer's accept lifts it; a reject of
an earlier added commit; an accept, a review of another commit and no review never stop a push; a board that cannot be asked is a
warning), a Rust test that a review stands under any branch name, and a runtime test that the deploying lead cannot accept its own commit.

## Not done here

- `.claude/agents/branch-worker.md` and `branch-finisher.md` still name `land-request` and `scripts/land`: person-only, the
  replacement text is `docs/agents-protected.patch`.
- `scripts/github-protection --lander` and the "lander" device role in `docs/github-protection.md` and
  `docs/compromise-containment.md` (the device that holds the push credential) are not the script; renaming the role is a pending item.
- The four one-shot scripts that only `land_review.py` named are the remainder of `docs/design-shrink-surface.md` slice 7.
