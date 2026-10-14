# Landing queue

Agents hand finished work to landing through the pool board, and a watcher lands every ready
entry as one batch with `scripts/land`. The batch mechanics (plan, merge, one combined
verification, ejection, finish) exist; this document is the design of the board side and the exact
cut between the two.

## Policy: a worker lands its own branch

The development branch is whatever the primary checkout is on (`POOLHOUSE_DEV_BRANCH` overrides it;
`src/poolhouse/devbranch.py`). The runner, the claim it holds, a request's default target and the push
all use that one resolved name.

1. Fetch; rebase your own linear commits onto `origin/<dev>` (merge `origin/<dev>` instead when the
   branch holds merges or is shared); run the affected tests.
2. `poolhouse-workspace land-request BRANCH SHA --test SELECTOR ...` with the full SHA of the tip, then, for security, encryption, access, trust or compute changes,
   another agent's `land-review ... accept` at that SHA. Ordinary changes proceed without review.
3. The runner batches, merges, gates, fast-forwards, pushes only the development branch and removes the
   landed worktree and branch. A worker never pushes, and reports landed only on the runner's `landed`
   message for its exact SHA (a gate result for that SHA exists before any push).
4. The lead steps in only for cross-branch conflicts, security review and release.

### One request never fails another

Every request is vetted and settled on its own, and a failure of one is ejected with its own outcome
and reason, sent to the requester by name, while the rest of the batch merges, gates and lands:

| what goes wrong | outcome |
| --- | --- |
| tip moved, SHA differs, branch missing | `refused` before the batch, with "request it again" |
| the diff changes a guard: `.claude/` settings and agents, the claude and git hooks that refuse, the person record's code, `AGENTS.md`, `CLAUDE.md`, `.github/`, the GitHub protection scripts, the landing code or the rules the guards read (`protectedwrites.GUARD_PATHS` and `REVIEWED`) | authenticated exact-SHA independent queue approval before merging; run and finish revalidate the live request, and finish checks guarded merge content against Git’s automatic merge |
| does not merge cleanly | `needs-human`, "merge conflict: files" |
| the merge step itself crashes | `needs-human`, "merge failed: ..."; the tree is reset to the last good merge |
| red test | `failed`, naming the failing test files and whether `<dev>` is red too |
| red combined gate | bisected by merge order on a clean base to the first merge that breaks it; that request is `failed`, the rest are re-merged and re-gated, and the loop repeats for each culprit |
| hang | no output for `--stall-minutes` (default 20), or past `--request-minutes`/`--batch-minutes`: the process group is stopped, the batch is re-run one request at a time, and only the request that hangs alone is `needs-human` |
| the batch fails with no culprit named, or the runner raises | the same one-at-a-time re-run; a request whose own step raises is `needs-human` with the error, logged to the board |
| worktree gone | the branch ref is what lands; a missing directory is not a failure |
| origin moved before the push | origin's tip is merged into the development branch, the checks the diff calls for are re-run on the merged tree, and the push is retried; a conflict or a red re-gate resets to the batch head and leaves the requests `landed-unpushed` with the reason, never dropped |

A runner that dies mid-batch resumes on restart: integration worktrees and branches (`land/*`) are swept,
a request still `running` whose SHA (or equivalent patches) is already on the development branch is
settled `landed` without merging again and pushed if origin lacks it, and any other is queued again.
Two runner processes cannot run at once (a lock file under the git directory), and the branch claim
carries the runner's pid and a lease, so a dead holder's claim is taken over at once.

### Keeping the runner alive

`scripts/land up` starts a detached supervisor that runs `scripts/land serve` and starts it again with a
growing delay (1 s up to 60 s, reset after two stable minutes) whenever it exits; `scripts/land down`
stops both cleanly (the runner releases its claim and lock) and `scripts/land status` prints the
supervisor's state. State is `land-runner.json`, output is `land-runner.log` (rotated at 5 MiB) and the
lock is `land-runner.lock`, all in the workspace directory. `digest --status` shows
`Runner supervisor: up (pid N), K restarts, ...; log PATH` or `NOT RUNNING; start it with scripts/land up`.
The lead's heartbeat (docs/heartbeat.md) reads that line each firing and runs `scripts/land up` when it
says `NOT RUNNING`; `up` is safe to repeat. The supervisor is not an OS unit: it dies with the machine
and the next heartbeat starts it.

## What is implemented (phase 2)

The queue lives on the board and a runner lands from it. Phase 1 (`scripts/land plan|run|finish`)
still knows nothing of the board; `scripts/land_board.py` is the only bridge.

- The queue is the board's `landing` entries (docs/node.md, "The landing queue"), written by the node, which
  stamps each with the session behind the token and checks independence and the runner's claim.
  `src/poolhouse/workspace/landing.py` is the client side: the folded `Queue`, the model-tier bar and the
  runner's standing check. Requests, independent reviews, cancels, pause and resume, runner states and
  progress beats are all entries, and each but a beat also writes an audit entry.
- Commands (`src/poolhouse/workspace/landing_cli.py`): `land-request BRANCH SHA --test SELECTOR ...
  --replaces TEXT`, `land-review REQUEST SHA --verdict accept|reject`, `land-cancel`, `land-pause`,
  `land-resume`, `land-queue`. `digest --status` also prints the queue, the runner holder and the
  age and text of the last gate step.
- Runner: `scripts/land serve [--once] [--interval S] [--stall-minutes 20] [--remote origin]`, run by
  the coordinator (or any lead) on any device that has the checkout. It is a process, not a unit:
  it registers on the checkout's board as a session of its own (harness `land-runner`, its token kept in
  the client's private credentials), takes the `branch <dev>` claim (the claim task integration also takes, so the two
  never land at once), and a second runner is told who holds it. An always-on form may use the
  autostart prepare flow later; none is installed.
- `scripts/land run --entries FILE` (JSON list of `{branch, tip}`, `scripts/land_entries.py`) refuses
  a branch whose tip is not the requested SHA, that does not exist, or that shares no history with
  `origin/<target>`.

### One pass of the runner

1. Claim the runner role; stop when paused.
2. For each waiting request, re-check its stamped standing (below) and the tip
   against the requested SHA. A request that fails is settled (`refused`) or left waiting
   (`needs-review`) with the reason, and its requester gets a message.
3. Batch up to eight remaining requests, oldest first, and run `scripts/land run --entries`: one
   integration worktree cut from fetched `origin/<dev>`, merges with the recorded policy (a
   conflict ejects that request as `needs-human`; `HANDOFF.md` and log conflicts keep both sides),
   one combined gate through `scripts/test`. A failing check is re-run on a clean checkout of the
   base and bisected: a failure also red on plain `<dev>` is reported as such and does not block;
   a failure only on the batch ejects the branch that introduced it (`failed`, naming the test
   files and the baseline-red checks) and the rest are re-verified.
4. On a green batch, `scripts/land finish --apply` fast-forwards the local development branch and
   removes the worktrees and branches of landed requests when `git cherry` shows nothing unique
   and nothing is dirty. Then a normal push of `<dev>:refs/heads/<dev>`. It refuses any
   protected target, never forces, never pushes `main`, tags or deletions, and goes through the
   repository's `pre-push` hook unchanged. A rejected push leaves the requests `landed-unpushed`
   and an announced `blocked`.
5. Messages: each requester gets a direct message per state (`handoff` for failure and
   `needs-human`, `status` otherwise); an announcement marks a landing (`milestone`), a batch not
   landed or a stuck gate (`blocked`).

### Whose entries count

Landing authority is this device's own. A request, review, brake or runner state counts only when this device
wrote it, or when it came from a device on this device's landing list that is still an active member of the
pool. Every other `landing` entry is kept on the board and shown by `land-queue` and `digest --status` as
"foreign, ignored", with the device's name, so a device that merely joined the pool cannot make this device's
runner land anything. The list is empty by default; `land-trust DEVICE` (a session with no parent that holds the
grant `land_trust`, audited on the board) adds a device by fingerprint and `land-trust DEVICE --revoke` removes
it; putting the device out of the pool ends its standing at once. The runner still re-checks the stamped standing of the requester and the reviewers at landing time (below),
which names sessions of this device's own registry, so a listed device's request lands here only once multi-device
identity exists; today the list makes its entries count in the queue.
The runner's `branch <dev>` claim is held among this device's sessions only (a `local` claim): a claim on `<dev>`
that a session of another device holds does not keep this device's runner from starting. `land-queue` and
`digest --status` list such a claim as "claim on branch <dev> held by NAME on DEVICE: foreign, ignored", as for a
foreign landing entry, so a device that joined the pool cannot stop landing here by holding the branch.

### Eligibility, review and brakes

- Only a landing-level session may request or review: a live session of the board whose model is listed and
  above the lowest tier (a listed id counts as claimed until the node can verify models). A lowest-tier or
  unlisted model is refused. The tier source is `src/poolhouse/workspace/model_tiers.json`: the `anthropic`,
  `openai` and `qwen` families, where the `qwen` family lists every quantisation of Qwen3.8-27B and
  Qwen3.8-Flash-Next above its lowest tier (the Qwen3.5 0.8B, 2B and 4B ids), the owner's decision of
  2026-10-12 (docs/model-benchmarks.md, 1.3). `main` and `master` are never a branch or target. A subagent may request
  its own work under its authenticated branch claim. Creators submit their own branches; no proxy-author field is accepted.
- The runner derives review requirements from the pinned diff against the target merge base. Ordinary explanatory
  documentation and Python changes with an unchanged syntax tree proceed without review;
  policy/instruction files and unknown executable behavior require review. Symlinks and file mode changes need review.
  Request summaries and caller labels cannot waive review. Renames and deletions include their old paths.
- A required review is an exact-SHA acceptance by any different eligible authenticated agent, including a parent,
  child or sibling. The creator cannot review itself. Any standing rejection blocks even an ordinary change.
- The node stamps identity/model at request and review time. Normal retirement preserves standing; revocation or
  forgery ends it. Requests and reviews remain bound to the same full SHA, with device trust and runner claims intact.
- Pause and resume: the runner or a session with no parent (a subagent may not). Cancel: the requester or those. A cancel of a
  running request stops the gate and queues the others again. A new request for the same branch
  supersedes the older one (a new SHA means a new review).
- Stuck gate: no output from `scripts/land run` for `--stall-minutes` (default 20) announces
  `blocked`, SIGTERMs the gate's process group (SIGKILL only after a minute of ignoring it), sets
  the batch's requests to `needs-human` and moves on to the next pass. Nothing is pushed.

### Not built

See the HANDOFF entry "Landing queue phase 2 leftovers": the task-lifecycle entry path, the
SessionStart suggestion line, the async `scripts/test submit` path for the gate, requests from a
device that does not hold the branch, and the blocked-finish retry.

## Original design (the parts above supersede where they differ)

### Cut line

Built: `scripts/land plan|run|finish` and the `scripts/land_*.py` modules (docs/test-execution.md,
"Landing a batch"). They take branch names and a repository path, know nothing about the board, and
leave a JSON summary on the last line plus `<git-common-dir>/land/state.json`.

Not built, in this order, each its own reviewable branch:

1. `src/poolhouse/workspace/landing.py` (pure): the handoff record, entry readiness and queue order
   below, over a `Workspace` object. No git side effects beyond reading refs.
2. `scripts/land submit [BRANCH]`: builds the record from the current checkout and posts it.
3. Task hook: an accepted task with a reviewed tip enters the queue from `reviewed()` in
   `src/poolhouse/workspace/task_integration.py`; completion and rework go through its existing
   `finish` and `blocked` events.
4. `scripts/land watch [--once]` and the SessionStart suggestion line.

`scripts/land run` takes one extension for step 4: accept `--entries FILE` (JSON list of
`{branch, tip}`) and refuse an entry whose branch tip differs from `tip`, so a stale submission
cannot be merged.

## Handoff record

One record per `(branch, tip)`, written under the submitting agent's own identity (its token; never
a delegate and never a person). `submit` is idempotent: the record id is
`land:<sha256(repo-id, branch, tip)[:16]>`, and posting it again returns the existing record.

| field | source |
| --- | --- |
| `branch`, `tip`, `base` | `git rev-parse` in the worktree; `base` is the merge-base with the target |
| `target` | the development branch unless named |
| `worktree` | absolute path |
| `checks` | the selectors run and their results, with the `activity/gate.py` evidence ids and the tree hash (`evidence(tree, tier)`) |
| `budgets` | metric deltas from `scripts/budgets` |
| `claims` | the agent's live claims (`ws.claims.listing`) |
| `review` | reviewer id and state when a task review exists, else `none` |
| `task` | the task id when the branch belongs to a task |

An accepted task needs no `submit`: `reviewed()` already proves acceptance, the exact proposal and
review hashes, and a released execution lease, and the tip is the worktree's `source_commit`. The
queue builds the same record from the task so the two entry paths look alike. The task lifecycle
is not forked: landing marks the task `completed` through the existing `finish` path
(`state='completed'`, `landed_commit`, claim release, worktree cleanup), and an ejection records a
`blocked` event on the task with the ejection evidence, which returns it to its worker.

## Queue

A deterministic view, computed from records and git only:

- ready: the tip still equals the submitted `tip`; `checks` are recorded and passing; no live claim
  of another owner covers the branch, its worktree or a touched file; the worktree is clean.
- order: oldest `ts` first, except an entry that other entries stack on (their unique patches
  contain it, from `land_plan.minimal_cover`) comes first.
- not ready entries stay listed with the reason (`stale-tip`, `dirty`, `claim-held`,
  `checks-missing`) so the submitter sees it.

## Watcher

`scripts/land watch` loops; `--once` does one iteration for a hook, cron or monitor. Each
iteration:

1. Return when no entry is ready, or when `other_full_run` or a live batch claim shows a gate in
   flight.
2. Take the batch id `sha256(sorted(entry ids))`, claim it (`ws.claim(token, 'area',
   'land-batch:<id>')`, live-owner check as for any claim) and stop if the claim is held.
3. Post one board line: `landing N entries: <branches>`.
4. Run `scripts/land run --entries ...`, then `scripts/land finish --apply`. The fast-forward is a
   compare-and-set on the target ref (`git update-ref refs/heads/<target> <new> <old>`), so two
   watchers cannot both land: the loser sees the moved ref, releases its claim and replans.
5. Post one board line: `landed N, ejected M: <branch>: <check>: <evidence>`; send each
   submitter, and the task's subscribers (`task-subscribe`), a message under the watcher's own
   identity. An ejection also writes the `blocked` task event.

The watcher is a role of the derived coordinator (docs/board-replication.md, section 6); today that is the
main session that started it, as a background task running `scripts/land watch`. It does not
push. It fast-forwards the local target and says "push left to the lead" unless the session is
allowed to push the development branch (AGENTS.md, "Landing"; `scripts/hooks/pre-push`). It
installs no launchd or systemd unit: that is a machine setting and stays with a person.

The SessionStart hook prints one line when the queue is non-empty and no watcher holds a live
claim: `landing queue: N ready; run scripts/land watch`.

## Tests

Real temporary git repositories and a real temporary workspace, no board mock: submit
idempotency; two entries batched once; a stale tip rejected; a conflicting pair where one is
ejected and its submitter notified; two watchers racing on one batch; task accepted, queued,
completed; ejected task returned as blocked with evidence.

## The runner's session, evidence and a landing by hand

- **The runner's board session.** `scripts/land serve` registers a session with harness `land-runner` and a native id of the checkout, the target and its launching supervisor (`ROOT:TARGET:<supervisor pid>:<its start time>`, or its own pid when init launched it). A restart under the same supervisor keeps the session; a new `scripts/land up` registers a new one. A session is bound to the process lineage that registered it and cannot move to another without a person's `rebind`, so one id per supervisor is what lets the runner be stopped and started without a person.
- **A request whose requester has ended.** The runner cannot record a failure against a requester session that has ended ("not a session of this board"); a request that fails is settled as failed and the requester files it again. A reviewer's accept belongs to a request, so a re-filed request needs its accept again.
- **Gate evidence.** `scripts/land_check.py` looks up an earlier pass of a check on the same tree and records each pass in the activity log. That log is keyed to the keystore, which only the installed software may touch, so from a checkout the lookup and the record are skipped and every check runs. Nothing is reused from an earlier pass there.
- **Landing several branches by hand.** `scripts/land plan BRANCH...` lists merge order and conflicts; `scripts/land run BRANCH...` merges them in an integration worktree, gates once and ejects the branch that fails with the evidence; `scripts/land finish --apply` fast-forwards the development branch, and the lead pushes it. Stop the runner first (`scripts/land down`): its idle pass removes every integration worktree, including one a hand landing is using. `scripts/land run` requires authenticated independent queue approval for a branch that changes a guard path. Batch state records request IDs and full source SHAs; `finish` revalidates approval, cancellation, revocation and guarded merge content immediately before applying. Keystore, privileged OS settings and release gates retain their separate authorization checks.
- **Branches that conflict with each other** (the plan names the files) are rebased in a stack and landed in that order, each branch keeping both sides' behaviour.
