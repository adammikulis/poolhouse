# The job log

Design only; nothing here is built. The owner ordered one append-only record of who is doing what, on which tree and
device, with claims, `scripts/status`, the test queue, worktree cleanup, the CI watcher's state and landing state read
from it. Words follow [glossary.md](glossary.md): the job log is the entries of kind `job` that each device keeps in its
log on the project's board. There is no second log and no second board. `job` joins the glossary when slice 1 lands.

## 1. What this changes

The node already has one lease table (`app/poolhouse-node/src/lease/`, `<state>/leases.json`) that admits the GPU, CPU
slots, memory, claims and model slots, and writes each grant and end to the board as a `lease` entry. Everything else on
the owner's list still keeps its own state in its own place (section 2). This design:

- makes the log the table: `leases.json` goes, the table is a fold of `job` entries;
- adds what a lease entry lacks: the kind of work, the tree, a queued state, an outcome, notes;
- moves each separate piece onto it as a view, and deletes the piece (section 5).

## 2. What exists and who reads it

Survey of the code at `origin/0.3dev` (read in ranges; not read in depth: `presence/`, the internals of
`testslots_rpc.py`, Windows paths; the owner's real board volume was not measured).

| State | Where | Written by | Read by |
| --- | --- | --- | --- |
| Lease table | `<state>/leases.json`, node memory | `lease/rpc.rs`, `claims.rs`, `tasks/calls.rs` | node sweep; `leasetable.py` (serve broker, request gate, `localslot.py`); `api/leases.py`; `board/session.py` |
| Lease entries | board kind `lease`, `#leases` | `mirror` in `lease/rpc.rs` (grants, ends, takeover audit; queued requests are not written) | `lease/merge.rs` (pool-wide holds of other devices); `ever_held` (landing review exclusion); board pages |
| Claims | `Resource::Claim`: worktree, branch, area, port, server, install, task; 15 min TTL, 8 h cap | `claim`, `reserve`, `release`, `heartbeat`; hooks (`harness_claims.py`) | `claims`, `who`; `stop_landed.py`; `land_steps.py` (dev branch claim, 7,200 s) |
| Announcements | `#announcements` messages: joined, milestone, done, blocked | `announce`; SubagentStart and SubagentStop hooks | `inbox` roll-up, `digest`; 6 per 10 minutes in `writes.rs` |
| Write quotas | in-memory `Rates` (`writes.rs`, cleared by a restart), `tally.rs`, `write-limits.json`, `limits.json` | node | node |
| Test permits | `~/.cache/dev-test-slots`: `*.slot`, `*.run`, `allocation.json`, `heavy-N.lane` | `testslots.py`, `testqueue.py`, `testslots_rpc.py` (admission socket for sandboxed pytest) | `runstatus.py`, `scripts/test status`, `workspace/slots.py`, `queue_doctor.py`, `queue_notice.py` |
| Test jobs | job folders: `spec.json`, `status.json`, `followers.json`, log; kept 3 days | `testjobs.py` | `scripts/test` `status`, `wait`, `result`, `cancel`; `queue_doctor.py`, `hooks/stop_jobs.py`, `runstatus.py` |
| Reuse claims | `testreuse_store.Store.claim` per lookup key | `testreuse_run.py` | other runs waiting on the same file |
| Worktree locks | `git worktree lock --reason <ident>` | `task_integration.py`; `integration_git.py` unlocks | `trees.py` (`locked`, `dead_owner` reads "pid N"), `scripts/worktrees` |
| Worktree ownership | `worktree-lifecycle.json` (+ lock), `local-agents/<id>.assigned` | `claims_cli.claim` (worktree), `task_worktrees.py` | `poolhouse-workspace worktrees`, Stop hooks, `task_worktree_recovery.py` |
| Landing locks | `<git-common>/land/runner.lock`, `branch-<hash>.lock`, `primary-sync.lock`, `primary-sync.again` | `land_steps.py`, `primary_sync.py` | a second lander, the runtime cutover, `pushed` hook |
| CI watcher | `<git-common>/ci-watch/<sha>.json`, `.lock`, `.log`, `.stuck-N` | `ci_watch.py` (started by `hooks/pushed`) | `ci_watch.py` (a second call prints the first result; red streak) |
| Status | none; reads the test permits, test jobs and `gh run list` | `runstatus.py` | `scripts/status`; the lead's attention hook (cached 5 minutes) |
| Session liveness | `seen_ms` in `names.json`; live lease test in `writes.rs::is_live` | node | idle retire, `agents` |
| Detached commands | one JSON per kind (`src/poolhouse/jobs.py`) | `poolhouse-jobs` | `status`, `wait`, `stop` |
| Landing reviews | board kind `landing` | `land_review` | `pushed` hook, `scripts/land` |
| Model servers | `servers.json` (`serve/leases.py`) | serve manager | `reclaim.py` |

The last two rows stay (section 5, "What stays"). Everything above them becomes a view or is deleted.

## 3. The record

### 3.1 Terms

A **job** is one thing a session is doing on one device: a unit of work with an id, a kind, what it asks to hold, where
it works, a state and an outcome. The **entries** of a job are rows of kind `job` (channel `#jobs`, replacing kind `lease`
and `#leases`) in the writing device's log. A **lease** stays what the glossary says: the permission the node grants a
job for a resource, shown by the job's `grant` row.

The node keeps one index, the fold of every `job` row it holds (its own and those of other members). One function,
`apply(row)`, is the only code that changes the index. It runs when this node appends, when the log is read at start and
when rows arrive from another device, so no view can disagree with another.

### 3.2 Rules that hold for every row

1. **The writer is the token's session.** `actor` is the session name the token resolves to; no body field names a
   writer. A request carrying a field the node already refuses (`sender`, `name`, `parent`, `from`, `author`, `actor`,
   `identity`, `as`, `label`) or one added to that list for jobs (`owner`, `holder`, `by`, `device`) is `denied`. Field
   names below avoid those words.
2. **The device is the origin of the row.** The tree path in `on` is something a writer says about its own device; other
   devices show its last component only.
3. **A device decides its own jobs.** A `grant`, `note`, `end`, `follow` or `unfollow` row counts only for a job opened in
   the same origin. Branch and area claims are about the shared repository and are decided by the board's total order as today
   (`lease/merge.rs`: the earliest acquire wins, the loser's job ends `lost`).
4. **Write-ahead.** The row is appended and flushed before its effect exists. A grant whose row cannot be written is not
   granted; the call fails with one plain line. A call that changes nothing (a `busy` answer) writes nothing. Two stated
   exceptions: the quiet request line (a GPU turn or a server line taken many times a second) is in memory, at most ten
   minutes between renewals, shown by `job_list` on its own device and never written; and the areas a held `work` job adds
   take effect at once and are written within a minute (section 4.1).
5. **Writes by the node.** `grant`, and the `end` of a dead, expired, abandoned, lost or retired job, are written by the
   node under its reserved name `node` (no session can hold it). A foreign `node` row counts only for jobs of its origin.
6. **Free text is data.** A title, step, blocked reason or outcome is cleaned (one line, no control characters), shown
   fenced and never read by a hook or a view to decide anything. Decisions read ids, states, numbers and git facts.
7. **Idempotent** (section 3.5).

### 3.3 Operations

| op | written by | body | notes |
| --- | --- | --- | --- |
| `open` | the session | `what`, `key`, `uses`, `on`, `class`, `estimate_s`, `ttl_s`, `pid`, `title`, `within`, `resumes`, `once` | state `queued`, or straight to `held` when free; with `wait: false` a job that is not free answers `busy` and writes nothing; the node reads the process start time itself |
| `grant` | node | `job`, `counts`, `after` | the lease; `after` names the ended job whose resources it took (replaces the `lease_takeover` audit entry) |
| `note` | the job's session | `job`, `step`, `blocked`, `areas`, `sha` | a step, a blocked reason, or up to 128 newly held areas; at most one `areas` note per job per minute |
| `end` | the session, its parent, the node | `job`, `why`, `outcome`, `sha`, `exit`, `cleanup` | once; the later `end` of an ended job returns the first |
| `follow`, `unfollow` | any session of the board | `job` | a second wanter of the same running job (test runs) |

Renewals (`job_beat`) are not rows: the node keeps the time of the last beat in memory. A restart gives each open local job
one fresh `ttl_s`; a job whose process is gone ends `dead` at the first sweep.

`uses` is the list of typed resources of today's `lease::types::Resource` (gpu, cpu_slots, memory_mb, claim, model_slot,
at most eight) and every conflict rule stays as written, including the path nesting of worktree, install and area claims.
Three additions: `cpu_slots` carries `min` and `count` (the node grants any number from `min` to `count` that is free at the
grant, which the elastic test pool needs); the heavy test lanes become a counted resource `lanes`; and `reuse` joins the claim
kinds (a test file's lookup key).
`on` is `tree` (an absolute path on the device), `branch`, `base`, `sha`, `treehash`, each at most 200 bytes.
`within` is the id of an enclosing job (a test phase inside a run); `resumes` names the ended job of a stopped session
whose tree this job continues.

### 3.4 Kinds of job

| `what` | opened by | holds | `key` (open at most one per actor and key) | ends with |
| --- | --- | --- | --- | --- |
| `work` | a session's first claim or mutation in a tree | claim worktree, claim branch when the session names it, areas added by `note` | `work:<tree>` | `outcome` landed, closed or stopped |
| `hold` | `claim port`, `server`, `install`, `area`; a test run's `reuse` key | that claim | `hold:<kind>:<name>` | released or expired |
| `test` | `scripts/test` | cpu_slots `min`..`count`, lanes or gpu for heavy tests | `test:<hash of argv and tree>` | done, failed, cancelled; `exit` |
| `serve` | `poolhouse-serve up`, the broker | model_slot, gpu, memory_mb, port | `serve:<model>` | released |
| `land` | `scripts/land` | a device-local claim on the lander's path | `land:<branch>:<sha>`, once | landed or stopped |
| `ci` | the `pushed` hook | nothing | `ci:<sha>`, once | green, red, skipped |
| `sync` | `primary_sync.py` | a device-local claim on its path | `sync:<dev>` | done |
| `tidy` | `worktrees close` and `sweep` | a device-local claim on the tree while it is removed | `tidy:<tree>:<head>`, once | landed, bundled, abandoned |
| `task` | `task_claim` (`tasks/`) | the `task` claim, beaten every 120 s | `task:<id>` | by the task's own state |
| `detach` | `poolhouse-jobs` | nothing | `detach:<kind>` | exit code |

The task board keeps its own `task` entries; only its claim lease becomes a `task` job. A person's terminal is a session
registered per terminal (`board.terminal.terminal_id`) like any other and writes under its own name; no person credential
is involved.

### 3.5 Idempotency

Three layers, each with one rule. They follow AGENTS.md ("Every command, hook, request and job is idempotent").

- **Request.** Each call carries a request id the caller makes once and reuses on a retry. It is the row's `idem`;
  `Board::append` already returns the first row for a repeated (actor, `idem`). A retried `open`, `note` or `end` creates nothing.
- **Key.** While a job with the same `what` and `key` of the same actor is open, `open` returns it. A different session
  opening a `test` key that is live gets the job and a `follow` row instead of a second run ("following job X, same tree and
  selectors"); the last `unfollow` ends a run whose followers are all gone. An exclusive resource held by another session queues the job, or answers `busy` with `wait: false`.
- **Once.** `once: true` returns the first job ever opened for the key, open or ended. This is "one landing per branch
  and SHA", "one CI watch per commit" and "a second call prints the first result". `--no-reuse` adds a nonce to the
  `test` key so it is a new run.

### 3.6 States

`queued`, `held`, `ended`; `stale` is a view, not a state (section 7). `why` is one of done, failed, cancelled, released,
expired, dead, abandoned, lost, retired, stopped.

## 4. Views

Each view is computed from the index; none is stored.

| Today | Becomes | Command |
| --- | --- | --- |
| `claims`, `who`, `heartbeat`, `release` | open jobs whose `uses` hold the claim; `job_beat` on the caller's open jobs; `job_end` | the verbs stay as the agent's words (`claim`, `release`, `claims`, `who`, `heartbeat`) and call `job_*`; the node methods `claim`, `reserve`, `release`, `claims`, `lease_*` are deleted |
| `scripts/testslots.py status`, `scripts/test status` | `test` jobs ordered by `lease/policy.rs` (shortest estimate first with aging, backfill, background share) | `scripts/test status` |
| `scripts/status` | open and recent jobs of every device, stuck rules (below) | `scripts/status` |
| `scripts/worktrees` | git's landed or work, plus the open local jobs on each tree and the last owner | `scripts/worktrees` |
| `ci_watch` state | the `ci` jobs of a branch: result, red streak, stuck notes | `scripts/ci_watch.py SHA` |
| Landing and push state | `land`, `sync` and `ci` jobs; `pushed` writes the pushed `sha` on the `land` or `work` job | `scripts/land` |
| `#announcements` | the `news` view (below) | `poolhouse-workspace inbox`, `digest` |
| Write quotas | counts of rows by session and window | none |
| `is_live` | a recent `seen_ms` or an open local job whose process is alive | `agents` |

### 4.1 Claims

A claim is an open job holding the resource. Taking a claim opens a `hold` job (or extends the session's `work` job by a
`note` of areas). The 15 minute TTL, the 8 hour cap, the path nesting, the pool-wide branch and area rule and "only the
holder or its parent releases" are unchanged. Native harness hooks that reserve files extend the `work` job in memory at
once (admission is immediate) and the node writes one `areas` note per minute with the new areas (up to 128 a note, 512 held by
one job; past that the job names a directory area). A crash loses at most the last minute of areas, which the next hook call
takes again; until then another session could take one, which is the window a restart already leaves.

### 4.2 Test queue

`scripts/test` opens a `test` job with the run's estimate and class, waits for the grant (`job_wait`) and ends the job when the
run ends. A phase (collection, tests) is a child job (`within`). The sandbox channel (`testslots_rpc.py`) stays as the way a
sandboxed pytest reaches the runner, which holds the job for it. Reuse claims become `hold` jobs of kind `reuse`. The
reaper's work (end a job whose runner is gone, tell the owner of one that is quiet, old or long) is the node's sweep plus
notices from `node`.

### 4.3 Status

`scripts/status` prints the verdict, then one section per device: its queue, its running jobs, its `work` jobs and its
`ci` jobs, each with the session, the age and the estimate. A job of another device shows as `name@d2` and the device's
last-heard age. Stuck rules are today's numbers (a run 5 minutes past its estimate, 20 minutes in total without one, a queued
run older than 5 minutes with nothing running, a CI run past 25 minutes), plus one: a device whose log is past 75 percent
of its row cap. GitHub runs not opened by a watcher are decision 2 (section 13).

```
ok
this device (d1): 2 test runs running, 1 queued; 3 work jobs
  running  test    claude-6e1a2f  est 90s age 41s   tests/test_trees.py
  queued   test    claude-91c0aa  est 240s age 12s  3 workers wanted, 2 free
  held     work    claude-6e1a2f  0.3dev  ../poolhouse-design-job-log  2h10
d2 (heard 8s ago): 1 work job
  held     work    claude-3b7d10@d2  docs/x  ../poolhouse-x  41 min
github: ci 76817ce in progress 6 min (ci job, claude-6e1a2f)
```

### 4.4 Worktrees

Git still says whether a tree is landed or work. The job log says who is in it: `scripts/worktrees` adds the owner of an
open local job on the tree and, for a work tree, the last owner and how it ended. `sweep` removes a tree that is landed, has
no open local job on it and is untouched for ten minutes (the process-cwd scan stays as a safety check, not as state).
The Stop hooks read the session's `work` jobs and git, not claims and `worktree-lifecycle.json`. Git worktree locks are no
longer written; an open job is the lock.

### 4.5 CI and landing

`hooks/pushed` opens `ci:<sha>` (once) for the pushing session and starts the watcher, which beats the job while it polls
GitHub, writes the stuck marks as `note` rows (idempotent per mark), sends the one red message and ends the job with
`green`, `red` or `skipped`. The red streak is the last ended `ci` jobs of the branch in the index (the last 1,024 ended jobs
of each kind per device are kept). `scripts/land` opens `land:<branch>:<sha>` (once), writes each step as a `note`, takes a device-local
claim on its own path instead of the landing lock files, and takes no pool-wide claim on the development branch: git's atomic
ref update decides who lands. A second lander of the same branch gets the first job and its step; the runtime cutover waits
on the open `land` job. The landing reviewer exclusion (`ever_held` in `landing.rs`) reads the `work` jobs that held the
branch or tree, as it reads claim entries now. `primary_sync.py` is a `sync` job; a second request while it runs is a `follow` row, which tells the
holder to go round again (replacing `primary-sync.again`).

### 4.6 News (the announcements)

The four announcement types become derived lines of `inbox` and `digest`: joined is the identity entry that exists; done is the
`end` of a subagent's `work` job; milestone is an `end` with `outcome: landed` and a `sha`, flagged when the commit touched
`AGENTS.md` or `CLAUDE.md` (the lander computes that from the diff, so "every agent re-reads both files" needs no free text);
blocked is a `note` with a blocked reason on an open job. One line per job, newest state only. A decision needed is a direct
`question` message, unchanged. The free-text `announce` write and the `#announcements` channel are deleted, with the 6 per
10 minutes limit.

### 4.7 Quotas

The in-memory `Rates` table goes. `tally.rs` already counts rows as they arrive and again from the log at start; it gains
counts of rows by session in one-minute buckets, so a restart no longer clears them. One rule replaces the send, child and
announcement limits: a session writes at most 30 rows a minute and its whole family (itself and its subagents) at most 150;
the numbers are the ones in `writes.rs` today, unchanged. The caps on unread messages and on notes per author stay: they bound
what readers must carry and are not job state. Job rows made by the node count for no session.

## 5. What is deleted

Rust (`app/poolhouse-node/src`): `claims.rs`; `lease/rpc.rs` and `lease/mod.rs` (the file, `Leases::save`, the table's
persistence) replaced by `jobs/` (`types`, `fold`, `sweep`, `rpc`, `view`, each under the 900 line limit); `lease/merge.rs`
becomes `jobs/view.rs`; the rate half of `writes.rs`; `Kind::Lease` and `#leases`, `ANNOUNCE`, `ANNOUNCE_TYPES` and the `claim` and
`release` message types in `fold.rs`; the `task` claim code in `tasks/calls.rs` (rewritten to a `task` job).
`lease/types.rs`, `lease/table.rs` and `lease/policy.rs` move under `jobs/` with their rules.

Python: `scripts/testslots.py`, `testslots_policy.py`, `testqueue.py`, `queue_doctor.py`, `queue_notice.py`, `workspace/slots.py`,
`hooks/tree_watch.py` (already an empty stub); `testslots_pytest.py`, `testslots_runner.py` and `testslots_rpc.py` lose their file locks and their calls into
`testslots.py` and keep the runner side; `testjobs.py` loses `spec.json`, `status.json`, `followers.json` and keeps spawning, log and junit files;
`ci_watch.py` loses `state_dir`, `load`, `save`, `records`, the markers and the lock; `land_steps.py` loses `exclusive`,
`one_run`, `dev_claim`; `primary_sync.py` loses its lock and again files; `trees.py` loses `locked`, `stale_lock`,
`dead_owner`; `task_integration.py` and `integration_git.py` lose `worktree lock` and `unlock`; `worktree_lifecycle.py`,
`task_worktree_recovery.py` and the record half of `worktree_archive.py` go; `claims_cli.py` shrinks to the verbs;
`leasetable.py`, `api/leases.py`, `board/session.py` claim methods and `harness_claims.py` call `job_*`;
`workspace/limits.py` loses `claim_ttl_s`; `runstatus.py` reads jobs; `jobs.py` (`poolhouse-jobs`) writes `detach` jobs.

Tests retired, each ported (the assertions kept, run against the new calls, watched failing before the code): Rust
`tests/claims.rs`, `claims_foreign.rs`, `lease.rs`, `lease_parties.rs`, `writes.rs`, `limits.rs`; Python `test_testslots.py`,
`test_testslots_classes.py`, `_fair`, `_holds`, `_queue`, `_storm`, `_wait`, `test_queue_watch.py`, `test_queue_doctor.py`,
`test_testjobs.py`, `test_testjobs_cancel.py`, `test_ci_watch.py`, `test_status.py`, `test_trees.py` (lock cases),
`test_worktree_lifecycle.py`, `test_worktree_archive.py`, `test_task_worktree_recovery.py`, `test_leasetable.py`,
`test_board_claims.py`, `test_workspace_claim_output.py`, `test_primary_sync.py`, `test_subagent_stop_jobs.py`,
`test_subagent_stop_landed.py`. A diff that weakens an assertion of a red-team, guard or authority test needs a
`Reviewed-by-second:` line; a port keeps the assertion.

Docs: `docs/worktree-lifecycle-recovery.md`; the lease section of `node.md`; the claims, mutation ownership and limits parts of
`workspace.md`; `test-execution.md`, `worktrees.md`, `landing.md` (CI run); the decisions table of `board-replication.md`.
AGENTS.md and CLAUDE.md lose the `announce` kinds and the claim wording; the owner orders that edit and it lands at once.

**What stays, and why.** `landing` entries (a verdict about a commit, not state of work); `servers.json` (a registry of
processes for the reaper); `lock.py` for exclusion around file writes; test duration, history and reuse stores (measurements);
job logs and junit files (data named by job id); `lease-config.json` (capacity, not state).

## 6. Dead workers

- **Process gone, node up.** Every `job_*` call, a ten second tick (new: today the sweep runs only when a lease call
  arrives) and the start sweep check each open local job's process (pid and start time, `lease/live.rs`). The node writes
  `end why=dead` under `node`, request id `end:<job>`, then grants the next job, with `after` naming the one it replaced.
  A queued request not polled for 60 s ends `abandoned`.
- **Node down.** At start the node folds the log, checks every open local job's process and ends the dead. A job with no
  process (a `remote` holder) lives by `ttl_s` from the restart.
- **Session retired or removed from idle.** The node ends its open jobs `why=retired`.
- **Another device's job.** No device ends another's job. A device unheard for 10 minutes (new, `partition_grace`) shows
  its jobs as `stale`; after `ttl_s` a stale pool-wide claim no longer holds a local request back. When the device returns and its earlier row wins the
  order, the local job ends `lost`, as now.
- **A tree left behind.** The dead session's `work` job is ended; the tree is `work` in git and shows its last owner. A new
  session opens a `work` job with `resumes` naming it. This is allowed only when the node ended the old job (verified death
  or expiry); a live job is never taken.
- **Two ends.** The node and a returning session may both write an `end`; the first stays and the second returns it.

## 7. The pool

Job rows replicate exactly as board rows do (`sync.rs`; per origin, through a verified head, at most 400 rows and 2 MiB per
request). `#jobs` is a fixed channel a board link may share, as `#leases` is. A member's `scripts/status` folds every origin it
holds: a job of device d2 shows with its session as `name@d2`, its state, its age and the age of the last row heard from d2.
Rows dated more than five minutes ahead are held back as today. Admission stays per device: this design adds no request that
opens a job in another device's table (leases on a remote device remain unbuilt, `node.md`, "What is left"). Clocks are
the hybrid clock of the row; nothing orders by the reader's wall time (service checklist, items 3 and 10: no tenant field
is added; ids do not assume one machine).

## 8. Size bounds

| Bound | Value |
| --- | --- |
| Row | at most 4 KiB, except an `areas` note at most 16 KiB; title 160 bytes; step 120; blocked 200; outcome 200; `on` fields 200 each |
| Open jobs (held and queued) | 64 per session, 256 per family, 1,024 per device; queued 8 per session (as today) |
| Notes | 200 per job |
| Areas held by one job | 512 |
| Row rate | 30 a minute per session, 150 per family (section 4.7) |
| Claim lifetime | 8 hours (as today); a test run `max_ttl_s` (24 hours) |
| Log | 200,000 rows and 128 KiB a row per origin (as today); 64 origins |
| Index | every open job, the last 1,024 ended jobs of each kind per device; startup folds the log once (`Tally` precedent) |
| Foreign view | at most 256 open jobs shown per device, then "and N more" |

Volume, an estimate and not a measurement: a test run is 3 rows; about 240 runs a day (724 stored job logs kept for 3 days,
`test-execution.md`) is 700 rows. A `work` job is 3 rows plus an `areas` note a minute while editing, about 100 rows; 20 a day
is 2,000. `ci` and `land` add about 400. That is about 3,000 rows a day, so a log holding only jobs would reach its cap in
about 10 weeks, and messages share the log. Slice 1 adds a rows-per-day counter to `scripts/status` and a STUCK line at
75 percent of the cap, so the growth is visible before slice 3 (retention) lands; the real number replaces this estimate.

The append checks `idem` by scanning the origin's rows; slice 1 measures it at 200,000 rows and adds an index if it is over
5 ms. The write-ahead flush costs one `fsync` per grant, as a lease entry does now.

## 9. Migration

None. A node of the new build that finds an old `leases.json`, a board log holding a `lease` row or an old `Kind` it does not
know says one plain sentence ("This pool was made by an older version of Poolhouse and cannot be used; make a new pool") and
the device makes a new pool and fresh state, as "Nothing is cemented until 1.0" says. Leftover files of the retired pieces
(`dev-test-slots`, `ci-watch`, `worktree-lifecycle.json`, `local-agents/*.assigned`) are not read and no code deletes them;
they hold records of runs that ended. The cost falls on the owner's devices only: each is re-added to the new pool by
`python -m poolhouse.device_setup --yes`, run by an agent. Keeping a `lease` kind parse-only, as `landing.rs` keeps retired
events, would keep the pool; the order is no migration, so it is not planned.

## 10. Onboarding impact

No human step is added. Job rows are written by commands and hooks that agents already run (SessionStart, the claim verbs,
`scripts/test`, `scripts/land`, the `pushed` hook); no new prompt, flag, code or file for a person. The token is the identity, so
nothing is registered by hand. A new person sees no change; only the owner's existing devices see the refusal of
section 9, once. `scripts/status` needs no network for the local part. The runner needs a node: a device that runs an
agent has one, and `scripts/test` on a bare checkout starts the device's node through the supervisor and never touches the
real state root from a test (slice 5 carries a fixed-cost test for that start).

## 11. Build plan

Each slice lands alone, with its tests written first and watched failing, the attack test of section 12 for what it touches, and
an affected-selector run on this device and on the WSL device for process and lock behaviour. No slice leaves two ways to do
one thing: a slice that moves a consumer deletes the old consumer in the same commit.

| # | Slice | Delivers | Tests |
| --- | --- | --- | --- |
| 1 | The log is the table (Rust; the same calls, a different store) | `Kind::Job` and `#jobs` in place of `lease` and `#leases`; `jobs/` with `apply` and `view.rs` (pool-wide holds of other devices, `ever_held`); `leases.json` deleted, the table is the fold; `open`, `grant`, `end` rows with write-ahead (queued requests are now written); the old-state refusal; the rows-per-day counter and the 75 percent verdict in `scripts/status` | port `lease.rs`, `lease_parties.rs`; new: cross-origin `end` ignored, write-ahead refusal, a replayed request is one row, a foreign `node` row ends nothing local, a restart folds to the same table, fixed-cost bound of the start fold, `idem` scan at 200,000 rows |
| 2 | The job calls | `job_open`, `job_wait`, `job_note`, `job_beat`, `job_end`, `job_follow`, `job_list` with `what`, `key`, `on`, `once`, notes and follow; the verbs, `leasetable.py`, `api/leases.py`, `board/session.py`, `harness_claims.py`, the serve broker and the request gate call them; `claims.rs` and the `claim`, `reserve`, `release`, `claims`, `lease_*` methods deleted; path claims confined to registered places; glossary row | port `claims.rs`, `claims_foreign.rs`, `test_leasetable.py`, `test_board_claims.py`, `test_workspace_claim_output.py`; new: a parent ends a child's job, a stranger cannot, `once` returns the first job, a claim of `/` is refused |
| 3 | Retention of the log (decision 1) | the log stops growing without bound | a log driven to the cap keeps every open job; a member offline past the cap rejoins |
| 4 | Quota from the log, announcements gone | `tally.rs` minute buckets; `Rates`, the send fields of `write-limits.json` and `announce` deleted; `news` in `inbox` and `digest`; subagent hooks stop posting; the AGENTS.md and CLAUDE.md edit | a restart keeps a session's count; a subagent flood counts against its family; `news` shows landed, blocked and done once per job |
| 5 | Test permits | `test` jobs with `cpu_slots min..count` and `lanes`; `testslots.py`, `testslots_policy.py`, `testqueue.py`, `slots.py` and the file locks of `testslots_pytest.py` and `testslots_runner.py` deleted; the runner holds the job | port the `testslots` tests onto a private node (`tests/node_kit.py`); a killed runner ends `dead` and frees its slots; fixed-cost test of reaching the node |
| 6 | Test jobs and the reaper | follow, cancel and status as `follow`, `end` and `job_list`; `testjobs.py` loses its records; `queue_doctor.py`, `queue_notice.py` replaced by the node's sweep and notices; `stop_jobs.py` reads jobs; `reuse` holds | port `test_testjobs*.py`, `test_queue_*.py`, `test_subagent_stop_jobs.py`; two runs of one tree and selectors follow one job; the last `unfollow` ends it |
| 7 | Worktrees | `work` and `tidy` jobs; `worktree-lifecycle.json`, `.assigned` and worktree locks deleted; `trees.py`, `scripts/worktrees` and the Stop hooks read jobs | port `test_trees.py`, `test_worktree_lifecycle.py`, `test_subagent_stop_landed.py`; a dead owner leaves a `work` tree that a `resumes` job takes; sweep ignores job text |
| 8 | CI watcher and status | `ci` jobs; `ci-watch` deleted; `runstatus.py` reads jobs and prints a section per device | port `test_ci_watch.py`, `test_status.py`; a second call prints the first result; a job of a second device shows as `name@d2` and `stale` |
| 9 | Landing and push | `land` and `sync` jobs; the landing lock files, the dev branch claim and the `primary-sync` files deleted | port `test_land*.py`, `test_primary_sync.py`; two landers of one branch and SHA are one landing |
| 10 | Cleanup | `jobs.py` writes `detach` jobs; the remaining files and docs of section 5; budgets lowered with `scripts/budgets --update`, said by how much | the budget and layer checks; `scripts/test gate` on the combined tree |

Slices 3 to 9 may land in any order after 2. Slice 3 moves up when the counter of slice 1 shows the log filling faster than the
estimate, and slice 5 (the first high-volume producer) lands after slice 3 unless that counter shows room.

## 12. Attack first

Written by the author; AGENTS.md requires a non-author to break this before it is built, so the pass below is the first draft of
that review brief, not the review. Each case says what the attack is and what stops it.

- **Copied or cloned pool or device.** A copied log gives history only: job rows grant nothing outside their own device, and the
  copy's node ends every local job whose process is not alive at its first sweep. A cloned device shares a device key and
  origin; two copies appending to one origin fork the chain, other members mark the origin damaged and drop the rows after the fork
  (existing rule), so the clone costs a device its status and nothing else. A pool copied to new devices is a new pool by
  `Pools are not preserved`. Residual: a clone makes its source disappear from status until it is removed.
- **Replay.** A captured row replays as the same row (id, hash, `idem`) and is kept once. A captured request replayed to the
  node returns the first result (section 3.5); with a stolen token it is a stolen token. A row copied into another board is
  refused by its board id in the hash and by the pinned key of its origin. Residual: heads sign an empty pool id today
  (`node.md`, "What is left"); moving the pool id into the head is a prerequisite of slice 1's cross-pool replay test.
- **Forged or below-threshold signature.** A row is stored only through a head the pinned device key signed; an unsigned tail
  is not stored and not relayed. No job row can change membership, a grant, an approval or a release, so no threshold applies to
  it. A forged `end` of another device's job fails the signature or the same-origin rule (3.2, rule 3).
- **Compromised or malicious member.** It can write false rows for its own device only: a job under any session name it
  invents except a name a local session holds (the fold refuses a foreign sender named like a local session). It can fill its own
  origin to the cap; the per-origin cap, the 256-job foreign view and removal bound that. It can win pool-wide branch and area
  claim races by holding back rows and releasing them late, which ends other devices' claims `lost`. Accepted: those claims serialize
  cooperating tools and are not a gate (git's push check and the `main` approval are), and removing the member cuts its log.
  The dev branch is no longer claimed pool-wide, so it cannot be held this way.
- **Stolen key.** A stolen device key signs rows as that device: jobs on that device only; the remedy is removal, which cuts the
  log. A stolen token acts as its session: opens and ends that session's jobs and nothing else, spends no grant from another
  terminal (lineage, existing). A stolen phone or person key has no use here: no job row takes a person signature and no
  view reads one.
- **Malicious agent.** It can open jobs to hog resources: bounded by the open-job caps, the 8 hour lifetime, 30 rows a minute and
  the family limit. It can hold a path claim to starve others: a path claim must lie inside a place the project registry
  (`registry.rs`) resolves to the job's board, so `/` and a home directory are refused (new; slice 2 confirms the registry gives
  this). It cannot end another session's job (owner, parent and node only; decision 3). A subagent cannot end its parent's.
  Sweeps and landing trust git and ids, never an `outcome` it wrote. The reviewer exclusion reads `work` rows as it reads
  claim rows today, so an agent that mutates without the hooks is as invisible as it is now (unchanged residual).
- **Injected instruction.** A title, step or blocked reason carrying an order is cleaned, fenced as data when shown, and read by
  no hook for a decision; hooks and views read ids, states, counts and git. An injected "end job X" can end only the caller's
  own jobs. `scripts/worktrees sweep` and `close` decide from git and from open local jobs, so a false row from any device
  cannot cause a deletion; a local false row can only keep a tree for at most its lifetime.
- **Downgrade.** An older node cannot parse `Kind::Job`, refuses the log and is not a member of the pool in practice
  (no mixed-build shim). A job kept out of the log needs `quiet`, which the node allows only for the GPU request line and the
  server line, ten minutes at most; any other `quiet` is `invalid`. A failed append refuses the grant (write-ahead), so a full
  disk or a capped log cannot become a silent grant; slice 3 makes the cap a roll, not a refusal. No flag or variable turns the
  log off.
- **Approval fatigue.** The job log adds no prompt and no approval reads a job row. Repeated `blocked` notes collapse to one
  line per job in `news`, are idempotent per reason and count against the row rate; the node's own notices (expired claim,
  quiet job) are one per job and event. The lead's attention line stays one cached line.
- **Takeover by whoever is present.** A process with no token writes nothing. Registering an existing main session from
  another terminal still needs the lineage or a person's `rebind`, so presence does not give its jobs. A dead worker's job is
  not taken: the node ends it on verified death or expiry and a successor opens its own job (`resumes`), which the node allows
  only after that end. An open pool admits any device on the network as a member; its rows then have exactly the reach of the
  malicious-member case, and the sentinel and flags for open joins still apply.

## 13. Open decisions

Each is one question; the first option is the more correct one, with its cost as a fact. Until answered, option 1 of each is
the plan and nothing waits on the answer.

1. **How does the log stay bounded (slice 3)?** (a) General retention first: a signed snapshot and a pruned prefix for the
   whole board log, as `board-replication.md` section 5 plans; one log, one retention story, a larger slice that is also what
   messages need. (b) Job entries get their own log per device (same row format and sync code, its own cap and roll): smaller,
   isolates the busiest kind, but doubles the chains a device verifies and the vector entries it exchanges.
2. **Does `scripts/status` still read GitHub for runs no watcher opened?** (a) Yes: one read-only `gh run list`, cached 60
   seconds, nothing written; shows scheduled runs, a person's pushes and re-runs, at the price of a network call in a view.
   (b) No: only `ci` jobs; fully offline and fast, but a run nobody watched is never shown or flagged.
3. **Who may end another session's job?** (a) Its owner, the owner's parent and the node; a person ends any job by ending its
   process, and the node records it. `scripts/test cancel` on someone else's run is refused with the owner's name. (b) Any
   session of the board for `test`, `ci` and `tidy` jobs, as `scripts/test cancel` allows today; convenient for a lead, but
   any agent can then stall another's gate run.
