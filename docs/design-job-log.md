# The job log

Design only; nothing here is built. The owner ordered one append-only record of who is doing what, on which tree and
device, with claims, `scripts/status`, the test queue, worktree cleanup, the CI watcher's state and landing state read
from it. Words follow [glossary.md](glossary.md): the job log is the entries of kind `job` that each device keeps in its
own job chain on the project's board. There is no second board and no second source of truth: the job chain is a second
chain of the same board, with the same rows, heads and replication (section 3.7). `job` joins the glossary when slice 2
lands. This text was revised after a non-author review; section 14 lists every finding and what it changed.

## 1. What this changes

The node already has one lease table (`app/poolhouse-node/src/lease/`, `<state>/leases.json`) that admits the GPU, CPU
slots, memory, claims and model slots, and writes each grant and end to the board as a `lease` entry. Everything else on
the owner's list still keeps its own state in its own place (section 2). This design:

- makes the log the table: `leases.json` goes, the table is a fold of `job` rows;
- keeps the job rows in a chain of their own per device that starts a new segment before it fills (section 3.7), so the
  busiest kind cannot fill the board's log;
- adds what a lease entry lacks: the kind of work, the tree, a queued state, an outcome, notes;
- moves each separate piece onto it as a view, and deletes the piece (section 5).

## 2. What exists and who reads it

Survey of the code at `origin/0.3dev` (63b9912e9), read in ranges; not read in depth: `presence/`, the internals of
`testslots_rpc.py`, Windows paths. A branch not yet landed, `fix/node-limits` (814dc686b, three commits over 76817ce35, in
the worktree `../poolhouse-node-limits`), removes the rate and lifetime limits; rows marked (L) say what it changes. The
volume of the owner's real board is the reviewer's count (section 8), not the author's.

| State | Where | Written by | Read by |
| --- | --- | --- | --- |
| Lease table | `<state>/leases.json`, node memory | `lease/rpc.rs`, `claims.rs`, `tasks/calls.rs` | node sweep; `leasetable.py` (serve broker, request gate, `localslot.py`); `api/leases.py`; `api/pool.py` (slots and leases held and queued, shown as the pool's capacity); `gate.py` (the GPU turn, taken quietly); `board/session.py` |
| Lease entries | board kind `lease`, `#leases` | `mirror` in `lease/rpc.rs` (grants, ends, takeover audit; queued requests are not written; a quiet lease writes nothing) | `lease/merge.rs` (pool-wide holds of other devices); board pages |
| Claims | `Resource::Claim`: worktree, branch, area, port, server, install, task; 15 min TTL; 8 h cap (`MAX_CLAIM_LIFETIME_S`, removed by (L)) | `claim`, `reserve`, `release`, `heartbeat`; hooks (`harness_claims.py`) | `claims`, `who`; `stop_landed.py` |
| Announcements | `#announcements` messages: joined, milestone, done, blocked | `announce` (`board_cli.py`, `board/public.py`); SubagentStart and SubagentStop hooks; `stop_jobs.py`, `stop_landed.py`; `status_cli.py` (autostart line), `testboard.py` (canary), `queue_notice.py`; the text of `onboard.py` and of the two `.claude/agents` files tells agents to post | `inbox` roll-up and `digest` (`board_cli.py`); `subs.rs` (digest and inbox delivery; the `agent` subscription a parent holds on its subagents, made by `child_watch.py`); `rooms.rs` (the channels everyone reads); `workspace/attention.py` and `claude-lead-attention` (the lead's line and its new-news count); `board_page.py`; `rooms_cli.py` |
| Write quotas | in-memory `Rates` (`limits.rs`, cleared by a restart). Callers that are not security floors: sends and announcements (`writes.rs`, 30 and 150 a minute, 6 per 10 minutes), new boards (16 an hour per lineage, `node.rs:271`), links and projects a minute (`api.rs:141`), `pool_submit` (120 a minute, `poolapi.rs:135`). Callers that are security floors: open joins (the join handler), phone codes (`phone/`), approval requests (`approvals/request.rs`), pool asks (`gov/ops.rs`). `tally.rs` counts unread messages and notes (not rates); `write-limits.json`; `limits.json` (`workspace/limits.py`) | node | node. (L) removes the callers that are not security floors and the send and announce keys; `Rates` stays for the floors |
| Test permits | `~/.cache/dev-test-slots`: `*.slot`, `*.run`, `allocation.json`, `heavy-N.lane`, and the admission socket | `testslots.py`, `testqueue.py`, `testslots_rpc.py` (admission socket for sandboxed pytest) | `runstatus.py`, `scripts/test status`, `workspace/slots.py`, `queue_doctor.py`, `queue_notice.py`; `agent_sandbox_profile.py` (the sandbox may write the folder) |
| Cores for pytest workers | the serve broker's in-memory `cores` (`broker_holds.take_cores`: never refuses, never waits), the op `cores` of `broker_wire.py` | `testing/cores.py` (`pytest_workers`, called from `tests/conftest.py`) | the broker's status; a second CPU allocator beside the test permits |
| Serve broker records | `broker.json` (port and token), `broker-leases.json` (holders of each served model, kept so a restart does not unload a server in use), `lease-history.ladybug` (ended model leases), in-memory `claims`; `broker-handoff.json` is named only in `scripts/test_kernel_role_home.py` and no source here writes it | `serve/broker.py`, `broker_holds.py`, `broker_wire.py` | the broker at restart; `test_kernel_role_home.py` |
| Test jobs | job folders: `spec.json`, `status.json`, `followers.json`, log; kept 3 days | `testjobs.py` | `scripts/test` `status`, `wait`, `result`, `cancel`; `queue_doctor.py`, `hooks/stop_jobs.py`, `runstatus.py` |
| Reuse claims | `inflight/<lookup>.json` in the reuse store, one per test file (owner pid and start time) | `testreuse_run.py` (`Store.claim`, line 320) | other runs waiting on the same file |
| Worktree locks | `git worktree lock --reason <ident>` | `task_integration.py`; `integration_git.py` unlocks (line 137 refuses a lock whose reason is not its own) | git itself (`worktree remove` and `prune` honour it), a person's terminal, `trees.py` (`locked`, `dead_owner` reads "pid N"), `scripts/worktrees` |
| Worktree ownership | `worktree-lifecycle.json` (+ lock), `local-agents/<id>.assigned` | `claims_cli.claim` (worktree), `task_worktrees.py`, `harness_claims.py` (`remember`), `notification_reader.py` | `harness_claims.py`, `harnesshook.py`, `harness_session.py`, `codex_hooks.py`, `task_runtime.py`, `task_cli.py`, `board_cli.py`, `poolhouse-workspace worktrees`, Stop hooks, `task_worktree_recovery.py` |
| Primary sync locks | `<git-common>/primary-sync.lock`, `primary-sync.again` | `primary_sync.py` | a second `primary_sync.py` |
| Cutover lock | `<git-common>/runtime-cutover.lock` (held while a runtime build or switch crosses the boundary) | `runtime_git.crossing` | the runtime cutover (`runtime_git.until_free`, `worktreerules.cutover_lock`), `node_supervise`, the node launch |
| CI watcher | `<git-common>/ci-watch/<sha>.json`, `.lock`, `.log`, `.stuck-N` | `ci_watch.py` (started by `hooks/pushed`) | `ci_watch.py` (a second call prints the first result; red streak) |
| Status | none; reads the test permits, test jobs and `gh run list` (20 s timeout, no cache; `github_status.py` 3 s, only when something is stuck) | `runstatus.py` | `scripts/status`; the lead's attention hook (cached 5 minutes) |
| Session liveness | `seen_ms` in `names.json`; live lease test in `writes.rs::is_live` | node | idle retire, `agents` |
| Detached commands | one JSON per kind (`src/poolhouse/jobs.py`) | `poolhouse-jobs` | `status`, `wait`, `stop` |
| Landing reviews | board kind `landing` | `land_review` | `pushed` hook, `runtime_unmerged.py` |
| Model servers | `servers.json` (`serve/leases.py`) | serve manager | `reclaim.py` |
| Test nodes left behind | processes whose state folder is under `/tmp/ml` | `testreap.py` | reads processes only, no lease or job state |

The last three rows stay (section 5, "What stays"). Everything else above becomes a view or is deleted.

## 3. The record

### 3.1 Terms

A **job** is one thing a session is doing on one device: a unit of work with an id, a kind, what it asks to hold, where
it works, a state and an outcome. The **rows** of a job are rows of kind `job` in the writing device's job chain (section
3.7), replacing kind `lease` and `#leases`. A **lease** stays what the glossary says: the permission the node grants a job
for a resource, shown by the job's `grant` row.

The node keeps one index, the fold of every `job` row it holds (its own and those of other members). It is changed by one
function, `Index::apply(row)`. Nothing of that name exists today: the nearest code is `Tally::catch_up` (`tally.rs`), which
counts rows by a per-origin position cursor, and the private `apply` of `tasks/fold.rs`. `Index::apply` is new and is the
only code that changes the index; it runs when this node appends, when the log is read at start and when rows arrive from
another device, so no view can disagree with another. What it does:

1. **Input.** One row of a job chain, once, in the order of its origin's chain. Rows of an origin whose writer is not an
   active member of the pool are not applied (the writer is the device whose key verified that origin's heads,
   `Board::writer_fingerprint`; section 7). A row dated more than five minutes ahead is held back (`rules::held_back`), and
   the origin's cursor stops before it and goes on when it is no longer ahead.
2. **Duplicates.** A row whose (origin, actor, `idem`) was applied already is skipped, as `rules::merge` skips it.
3. **Pool-wide winner.** For a branch or area claim the winner is the least (hlc, origin, seq) among the open jobs holding it
   (`Row::order`). A foreign row that sorts before the current holder, arriving late, changes the winner: the index
   re-evaluates that one key and the displaced local job loses that resource only (section 7).
4. **Foreign limits.** A foreign `open` past 1,024 open jobs of its origin, a `note` past 200 for one job, and an `areas` note
   past 128 areas are skipped and recorded with `Board::reject`, as the fold skips a foreign note past 200 per sender today
   (`fold.rs`, `NOTES_PER_SENDER`).
5. **Rebuild.** The cursor of an origin is its position and the hash of the last row applied there, not a row count. The
   index is rebuilt from zero when a log is cut (`Board::cut`), reset, forgiven or marked damaged, when a member is removed or
   comes back, and whenever the hash at the cursor differs from the row held there. (`Tally::catch_up` detects only a log
   that got shorter; a log refetched to the same length would go unseen.)
6. **Removed and silent origins.** The holds of a removed member's origin end at its cut. A device unheard for 10 minutes shows
   its jobs as `stale` (section 6).

### 3.2 Rules that hold for every row

1. **The writer is the token's session.** `actor` is the session name the token resolves to; no body field names a
   writer. A request carrying a field the node already refuses (`sender`, `name`, `parent`, `from`, `author`, `actor`,
   `identity`, `as`, `label`) or one added to that list for jobs (`owner`, `holder`, `by`, `device`) is `denied`. Field
   names below avoid those words.
2. **The device is the origin of the row; a path is written relative to its home.** A path in a row is written relative to the writer's home directory (`~/...`; a
   path outside the home is written whole) and expanded only by the device that wrote it. Every member receives the row, so a
   path is replicated; other devices show its last component. A row never carries the home directory's own name.
3. **A device decides its own jobs.** A `grant`, `note`, `end`, `follow` or `unfollow` row counts only for a job opened in
   the same origin. Branch and area claims are about the shared repository and are decided by the board's total order as today
   (`lease/merge.rs`: the earliest acquire wins), but the loser loses only the contested resource (section 7).
4. **Write-ahead for two ops.** `open` and `grant` are appended and flushed before their effect exists; a grant whose row
   cannot be written is not granted, and the call fails with one plain line. `end`, `dead`, `expired`, `abandoned`, `lost`,
   `retired` and `note` take effect first and are appended at once; one that cannot be appended stays in memory and is retried
   at every tick, and a restart derives it again (a process that is gone is gone). A call that changes nothing (a `busy`
   answer) writes nothing. The rows of a `test` job are written the same way as an `end` (effect first), so a full log or a
   stale build never stops a gate run (section 4.2). Two stated exceptions on when rows are written: a quiet request line is in
   memory until it has been held 10 minutes, then its `grant` row is written (section 6); and the areas a held `work` job adds
   take effect at once and are written within a minute (section 4.1).
5. **Writes by the node.** `grant`, and the `end` of a dead, expired, abandoned, lost or retired job, are written by the node
   under its reserved name `node`. `node` is added to `RESERVED` in `row.rs` (it is not there today), so no session can be
   named `node`; the job chain accepts the actor `node` only on rows the node itself appends. A foreign `node` row counts only
   for jobs of its origin.
6. **Free text is data.** A title, step, blocked reason or outcome is cleaned (one line, no control characters), shown
   fenced and never read by a hook or a view to decide anything. Decisions read ids, states, numbers and git facts. Text from
   another device is screened by the reading client exactly like a foreign message (`quarantine_hold` on the row's id, which
   has the same `board:origin:seq` form) and shows as a placeholder when flagged.
7. **Idempotent** (section 3.5).

### 3.3 Operations

| op | written by | body | notes |
| --- | --- | --- | --- |
| `open` | the session | `what`, `key`, `uses`, `on`, `class`, `estimate_s`, `ttl_s`, `pid`, `title`, `within`, `resumes`, `once`, `wait` | state `queued`, or straight to `held` when free; with `wait: false` a job that is not free answers `busy` and writes nothing; the node reads the process start time itself |
| `grant` | node | `job`, `counts`, `after`, `pid`, `pid_start` | the lease; `after` names the ended job whose resources it took (replaces the `lease_takeover` audit entry); `pid_start` is the process start time the node read (section 6) |
| `note` | the job's session; the node for `lost` and notices | `job`, `step`, `blocked`, `areas`, `lost`, `sha` | a step, a blocked reason, up to 128 newly held areas, or the areas the pool order took away; at most one `areas` note per job per minute |
| `end` | the session, an ancestor of the session, the node | `job`, `why`, `outcome`, `sha`, `exit`, `cleanup`, `asked` | once; the later `end` of an ended job returns the first; `asked` names the main session whose request made the node end a `test` job (section 3.8) |
| `follow`, `unfollow` | a session of the same device | `job` | a second wanter of the same running job (test runs) |
| `base`, `carry` | the node, at the start of a segment | `rows` (on `base`, which is its own row kind); `job` and the open fields (on `carry`, an op of this kind) | section 3.7 |

Renewals (`job_beat`) are not rows: the node keeps the time of the last beat in memory, on its monotonic clock (section 6). A
restart gives each open local job one fresh `ttl_s`; a job whose process is gone ends `dead` at the first sweep.

`uses` is the list of typed resources of today's `lease::types::Resource` (gpu, cpu_slots, memory_mb, claim, model_slot,
at most eight) and every conflict rule stays as written, including the path nesting of worktree, install and area claims.
Three additions: `cpu_slots` carries `min` and `count` (the node grants any number from `min` to `count` that is free at the
grant, which the elastic test pool needs); the heavy test lanes become a counted resource `lanes`; and `reuse` joins the claim
kinds, naming a run, not a file (section 4.2).
`on` is `tree` (the path, written as section 3.2 rule 2 says, and its last component), `branch`, `base`, `sha`, `treehash`,
each at most 200 bytes. `within` is the id of an enclosing job (a test phase inside a run); `resumes` names the ended job of a
stopped session whose tree this job continues.

### 3.4 Kinds of job

| `what` | opened by | holds | `key` (open at most one per actor and key) | ends with |
| --- | --- | --- | --- | --- |
| `work` | a session's first claim or mutation in a tree | claim worktree, claim branch when the session names it, areas added by `note` | `work:<tree>` | `outcome` landed, closed or stopped |
| `hold` | `claim port`, `server`, `install`, `area`; one `reuse` hold for a test run | that claim | `hold:<kind>:<name>` (`hold:reuse:<run>`) | released or expired |
| `test` | `scripts/test` | cpu_slots `min`..`count`, lanes or gpu for heavy tests | `test:<hash of argv and tree>` | done, failed, cancelled; `exit` |
| `serve` | `poolhouse-serve up`, the broker | model_slot, gpu, memory_mb, port | `serve:<model>` | released |
| `ci` | the `pushed` hook | nothing | `ci:<sha>`, once per actor | green, red, skipped |
| `sync` | `primary_sync.py` | a device-local claim on its path | `sync:<dev>` | done |
| `tidy` | `worktrees close` and `sweep` | a device-local claim on the tree while it is removed | `tidy:<tree>:<head>`, once per actor | landed, bundled, abandoned |
| `task` | `task_claim` (`tasks/`) | the `task` claim, beaten every 120 s | `task:<id>` | by the task's own state |
| `detach` | `poolhouse-jobs` | nothing | `detach:<kind>` | exit code |

The task board keeps its own `task` entries; only its claim lease becomes a `task` job. A person's terminal is a session
registered per terminal (`board.terminal.terminal_id`) like any other and writes under its own name; no person credential
is involved. `<tree>` in a key is the tree's path as section 3.2 rule 2 writes it.

### 3.5 Idempotency

Three layers, each with one rule. They follow AGENTS.md ("Every command, hook, request and job is idempotent").

- **Request.** Each call carries a request id the caller makes once and reuses on a retry. It is the row's `idem`;
  `Board::append` already returns the first row for a repeated (actor, `idem`). A retried `open`, `note` or `end` creates nothing.
- **Key.** While a job with the same `what` and `key` of the same actor is open, `open` returns it. A different session of the
  same device opening a `test` key that is live (open, and its process alive) gets the job and a `follow` row instead of a
  second run ("following job X, same tree and selectors"); the last `unfollow` ends a run whose followers are all gone. An
  exclusive resource held by another session queues the job, or answers `busy` with `wait: false`.
- **Once.** `once: true` returns the first job *this actor* opened for the key, open or ended. It is never answered with
  another actor's job, and no outcome of an ended job is read as a verdict by anyone else. "One landing per branch and SHA"
  is then git's fact (a second push of a commit finds it already on the development branch), "one CI watch per commit"
  is a live `ci:<sha>` job that a second caller follows, and after it ends a second caller reads the run from GitHub (cached,
  section 4.3). `--no-reuse` adds a nonce to the `test` key so it is a new run.

A follower of a `test` job takes the result from the runner's own junit and exit files under the job id, and uses them only
when the recorded tree hash equals its own `on.treehash`; the `outcome` text of the job is for display. A session that ends
a job it did not run changes nothing a follower decides on.

### 3.6 States

`queued`, `held`, `ended`; `stale` is a view, not a state (section 6). `why` is one of done, failed, cancelled, released,
expired, dead, abandoned, lost, retired, stopped.

### 3.7 The job chain, its segments and base rows

Each device keeps, for a board, two chains: its entries (`<origin>.jsonl`, as today) and its jobs (`<origin>.jobs.jsonl`).
Rows, hashes, heads and per-origin replication are the same code. A head names its chain in the bytes it signs, so a head of
one chain does not verify in the other. Job rows are not entries: no board view, room or link reads them, and a board link
shares no jobs (`#leases` leaves the fixed channels).

**Sizes.** A chain holds at most 200,000 rows (as an entry log does today, `MAX_LOG_ROWS`). At 150,000 rows (75 percent) the
node starts a new segment. At 180,000 (90 percent) it refuses `open` and `grant` with one plain line ("this device's job log
is full; it is starting a new segment") and nothing else: `end`, `dead`, `expired`, `abandoned`, `retired`, `base`, `carry`
and heads may use the last 10 percent. Today `Board::add` refuses every row at the cap, a head included, which is why a
full log would deny every grant and every `end`.

**Starting a segment.**

1. The node writes the new segment to a temporary file: a `base` row, then one `carry` row for each open job, then a head. The
   `base` row continues the chain: its `seq` is the last row's plus one and its `prev` is that row's hash, so a device
   holding the old rows verifies it as an ordinary next row. It says `rows: n`, the number of `carry` rows that follow (at
   most 1,024). A `carry` row restates one open job: its `open` fields, its grant (`counts`, `pid`, `pid_start`), its latest
   step and blocked text, up to 32 followers and up to 128 areas. A row follows the same size bounds as an `open` and an
   `areas` note.
2. The file is flushed and then takes the chain's name; the old segment is kept beside it under a numbered name. A crash
   before that leaves the old segment as the log (the node discards the temporary file at start); a crash after it leaves the
   new one.
3. A closed segment is deleted once every active member has acknowledged a row past the base (`Board::acknowledge` records
   it per device), and in any case after 7 days.
4. The index of the device is the fold of its current segment. Ended jobs older than the base are no longer in it; the red
   streak of CI counts the ended `ci` jobs of the current segment.

**What a device that holds another's chain does with a `base`.**

1. It holds nothing of that origin: it starts the chain at the base. The head that covers the base verifies under the key of
   the origin's writer, who must be an active member (slice 0).
2. It holds rows and the base is the next row (`seq` and `prev` fit): an ordinary append.
3. It holds rows and the base is ahead of them (a gap): it drops its copy and starts at the base, provided the base's `seq`
   is greater than its held tip's and the key is the pinned key.
4. The base is at or behind its tip: the rows are compared by hash as today; a difference is a fork and marks the origin
   damaged (`Board::ingest`).
5. It applies the new segment to its index only when the `n` `carry` rows and a head that covers them are held; until then
   it shows the origin's previous state. A `base` whose `rows` is over 1,024, or whose `carry` rows are not directly after it,
   or that names a job twice, makes the log damaged.

**What changes in today's code.** `check_chain` starts from `GENESIS` when it has no tip and `Log::open` calls it with none;
it learns that a `base` may start a chain. `Board::rows` is indexed by `seq - 1` (in `rules::accept`, `Board::trusted`,
`Board::cut`, `Board::acknowledge` and `sync::rows_since`); a chain with a base starts at a first `seq`, so those become
`seq - first`. The head message gains the chain's name. A new device that starts at a base takes at most 1,025 rows plus
heads where it would take 200,000, so the first sync of a device is also shorter.

### 3.8 Who may end a job

A job ends by its own session, by any ancestor of that session (its parent, its parent's parent, and so on, by the lineage
the node already keeps), or by the node (death, expiry, abandoned, lost, retired). A queued `test` job, or a held one whose runner is alive
but has stopped renewing, can also be ended by the node at the request of a main session of the same device; the row says `why: cancelled` and `asked` names the
main session. Nobody else ends it. A person ends any job by ending its process, and the node records it.

## 4. Views

Each view is computed from the index; none is stored.

| Today | Becomes | Command |
| --- | --- | --- |
| `claims`, `who`, `heartbeat`, `release` | open jobs whose `uses` hold the claim; `job_beat` on the caller's open jobs; `job_end` | the verbs stay as the agent's words (`claim`, `release`, `claims`, `who`, `heartbeat`) and call `job_*`; the node methods `claim`, `reserve`, `release`, `claims`, `lease_*` are deleted |
| `scripts/testslots.py status`, `scripts/test status` | `test` jobs ordered by `lease/policy.rs` (shortest estimate first with aging, backfill, background share) | `scripts/test status` |
| `scripts/status` | open and recent jobs of every device, stuck rules (below) | `scripts/status` |
| `scripts/worktrees` | git's landed or work, plus the open local jobs on each tree and the last owner | `scripts/worktrees` |
| `ci_watch` state | the `ci` jobs of a branch: result, red streak, stuck notes | `scripts/ci_watch.py SHA` |
| `#announcements` | the `news` view (below) | `poolhouse-workspace inbox`, `digest` |
| Write quotas | no view (section 4.7) | none |
| `is_live` | a recent `seen_ms` or an open local job whose process is alive | `agents` |

### 4.1 Claims

A claim is an open job holding the resource. Taking a claim opens a `hold` job (or extends the session's `work` job by a
`note` of areas). The 15 minute TTL, the path nesting, the pool-wide branch and area rule and "only the holder or its ancestor
releases" are unchanged; there is no lifetime cap (section 8). Native harness hooks that reserve files extend the `work` job
in memory at once (admission is immediate) and the node writes one `areas` note per minute with the new areas (up to 128 a
note and 128 held by one job; past that the job names a directory area, which is coarser). A crash loses at most the last
minute of areas, which the next hook call takes again; until then another session could take one, which is the window a
restart already leaves.

**Path claims are confined.** A worktree, install or area claim must lie below a path that `git worktree list` prints for a
git source of the job's project, or below a registered source of another kind. `registry::git_root` is not enough: it takes any
`.git` file it finds (a file `~/.git` holding `gitdir: <repo>/.git` makes the whole home resolve to the repository's project),
and `refuse_broad` runs only when a source is registered. So the check is on the claim: the node reads the repository's
worktree list (`<git-common>/worktrees/*/gitdir`) and refuses a path outside it with one plain line. A claim of `/` or of a
home directory is refused the same way.

### 4.2 Test queue

`scripts/test` opens a `test` job with the run's estimate and class, waits for the grant (`job_wait`) and ends the job when the
run ends. A phase (collection, tests) is a child job (`within`). The sandbox channel (`testslots_rpc.py`) stays as the way a
sandboxed pytest reaches the runner, which holds the job for it. The reaper's work (end a job whose runner is gone, tell the
owner of one that is quiet, old or long) is the node's sweep plus notices from `node`, and it ends nothing for time.

**Reuse is one hold per run.** A run opens one `hold` job `hold:reuse:<run>` when it starts. The per-file marks stay where they
are, in the reuse store's `inflight/<lookup>.json` (they already carry the owner's pid and start time), and now also name the
run's job id; a run waiting for a file waits on that job (`job_wait`), not on a pid. That is 3 rows for the `test` job and 3 for
the hold, not one claim per test file: the repository has 920 test files (counted), so a claim per file would write about 2,800 rows
a gate run.

**Cores.** The number of pytest workers is the `cpu_slots` grant of the run's `test` job (`min`..`count`). `testing/cores.py`,
the op `cores` of `broker_wire.py` and `take_cores` of `broker_holds.py` (a second CPU allocator that never waits and knows
nothing of the test permits) are deleted.

**Exempt from the gates that would stop a gate run.** `lease_acquire` calls `work_gate()` for every non-quiet request, and
`work_gate()` refuses work while the device runs a build older than the pool's. The runner needs the node, so a `test` job is
exempt from `work_gate()` and from the 90 percent refusal of section 3.7: a device catching up or with a full job log still
runs its own tests. Its rows are written after the effect (rule 4) and a failed write shows in `scripts/status`.

### 4.3 Status

`scripts/status` prints the verdict, then one section per device: its queue, its running jobs, its `work` jobs and its
`ci` jobs, each with the session, the age and the estimate. A job of another device shows as `name@d2` and the device's
last-heard age. Stuck rules are today's numbers (a run 5 minutes past its estimate, 20 minutes in total without one, a queued
run older than 5 minutes with nothing running, a CI run past 25 minutes), plus one: a device whose job chain is past 75 percent
of its row cap, and one that cannot start a segment.

The `github:` section makes one read-only `gh run list` for the development branch (plus the job lines of a run in progress),
with a 3 second timeout, and caches the answer for 60 seconds. When `gh` does not answer, the section is left out and nothing
says so. It shows scheduled runs, a person's pushes and re-runs that no `ci` job watches. `github_status.py` (githubstatus.com,
3 seconds, only when something is stuck) stays as it is. Today `runstatus.github()` waits up to 20 seconds and caches nothing;
slice 7 sets the new numbers.

```
ok
this device (d1): 2 test runs running, 1 queued; 3 work jobs
  running  test    claude-6e1a2f  est 90s age 41s   tests/test_trees.py
  queued   test    claude-91c0aa  est 240s age 12s  3 workers wanted, 2 free
  held     work    claude-6e1a2f  0.3dev  ../poolhouse-design-job-log  2h10
d2 (heard 8s ago): 1 work job
  held     work    claude-3b7d10@d2  docs/x  ../poolhouse-x  41 min
github: 0.3dev: ci 76817ce in progress 6 min; run 8123 (ci job, claude-6e1a2f)
```

### 4.4 Worktrees

Git still says whether a tree is landed or work. The job log says who is in it: `scripts/worktrees` adds the owner of an
open local job on the tree and, for a work tree, the last owner and how it ended. `sweep` removes a tree that is landed, has
no open local job on it and is untouched for ten minutes (the process-cwd scan stays as a safety check, not as state).
The Stop hooks read the session's `work` jobs and git, not claims and `worktree-lifecycle.json`.

**The git worktree lock stays, as a mirror of the job.** Git itself (`worktree remove`, `prune`), a person's terminal and
`integration_git.py` honour `git worktree lock`; none of them reads a job. So the Python code that opens a `work` or
`tidy` job runs `git worktree lock --reason job:<id>` on the tree, as `task_integration.py` does today, and the code that ends
it runs `unlock`; `scripts/worktrees sweep` repairs a difference (a tree with an open job and no lock is locked, a lock naming
an ended job is removed). The job is the source of truth; the lock is derived from it.
`trees.py` keeps `locked` and reads the reason as a job id; `dead_owner` asks the node whether that job is open instead of
reading "pid N"; `stale_lock` goes. `task_integration.py` and `integration_git.py` take the lock reason from the job (they
compare it at line 137 as now) and stop inventing their own.

### 4.5 CI and sync

`hooks/pushed` opens `ci:<sha>` (once, per actor) for the pushing session and starts the watcher, which beats the job while it
polls GitHub, writes the stuck marks as `note` rows (idempotent per mark), sends the one red message and ends the job with
`green`, `red` or `skipped`. The verdict is GitHub's, read by the watcher; no other session reads an `outcome` as one. The red
streak is the last ended `ci` jobs of the branch in the index (the last 1,024 ended jobs of each kind per device are kept, and
only those of the current segment).

A push opens no job and holds no lock: git's atomic ref update decides who lands. The runtime cutover waits on its own lock
(`runtime_git.boundary`, `worktreerules.cutover_lock`), which no push takes. `primary_sync.py` is a `sync` job; a second request while
it runs is a `follow` row, which tells the holder to go round again (replacing `primary-sync.again`).

### 4.6 News (the announcements)

This view lands after the `work` jobs (slice 6) exist, because it is built from them. The four
announcement types become derived lines of `inbox` and `digest`: joined is the identity entry that exists; done is the
`end` of a subagent's `work` job; milestone is an `end` with `outcome: landed` and a `sha`, flagged when the commit touched
`AGENTS.md` or `CLAUDE.md` (computed from the commit's diff, so "every agent re-reads both files" needs no free text);
blocked is a `note` with a blocked reason on an open job. One line per job, newest state only. A decision needed is a direct
`question` message, unchanged. What the slice deletes is only the sending of free text: the `announce` verb and its allowed
kinds, the `#announcements` post path, the posts of the subagent hooks and the other posters of section 2, and the
6 per 10 minutes key. The readers move to the new source: `subs.rs` (the digest and inbox delivery for the board target, and the
`agent` subscription a parent holds on a subagent, which `child_watch.py` makes and which now delivers the subagent's `work`
`end`), `rooms.rs`, `workspace/attention.py` and `claude-lead-attention`, `board_page.py`, `rooms_cli.py`, the roll-up of
`board_cli.py`. The `.claude/agents` files stop naming `announce` (HANDOFF.md says those two edits are the owner's).

### 4.7 Quotas

On `origin/0.3dev` the node counts sends and announcements in `Rates` (`writes.rs`: 30 and 150 a minute, 6 per 10 minutes), and
`Rates` also guards new boards (16 an hour per lineage, `node.rs:271`), links and projects a minute (`api.rs:141`) and
`pool_submit` (120 a minute, `poolapi.rs:135`). The last three have no equivalent in rows and no job state behind them.
`fix/node-limits`, not landed, removes those limits and the send and announce ones, with the rooms, subscriptions, reserve size,
8 hour claim lifetime and quiet lease limits, as the owner's order (HANDOFF.md, "Hidden limits"), and keeps `Rates` for the
security floors (open joins, phone codes, approval requests, pool asks). This design adds no rate limit and depends on none:
a job row is bounded by the segment of section 3.7, by the open-job bounds of section 8 and by the `fsync` each write-ahead row costs. Slice 9 deletes the
send and announce keys of `write-limits.json` and the code that reads them (`check_send`, `sent`, `Admitted`, the announcement
window) if they still exist; it touches no other `Rates` caller. The caps on unread messages and on notes per author stay:
they bound what readers must carry and are not job state. `tally.rs` gains nothing.

## 5. What is deleted

Rust (`app/poolhouse-node/src`): `claims.rs`; `lease/rpc.rs` and `lease/mod.rs` (the file, `Leases::save`, the table's
persistence) replaced by `jobs/` (`types`, `fold` with `Index::apply`, `sweep`, `rpc`, `view`, each under the 900 line limit);
`lease/merge.rs` becomes `jobs/view.rs`; the lease arm of `fold::entry` (a `lease` row, foreign or own, is rejected like a kind
with no arm), `ANNOUNCE`, `ANNOUNCE_TYPES` and the `claim` and `release` message types in `fold.rs`, and `#leases` from
`FIXED_CHANNELS`; `Kind::Lease` stays in the enum so a row of it still parses; the send and announce half of `writes.rs`; the
announcement paths of `subs.rs` and `rooms.rs`; the `task` claim code in `tasks/calls.rs` (rewritten to a `task` job).
`lease/types.rs`, `lease/table.rs` and `lease/policy.rs` move under `jobs/` with their rules; `QUIET_MAX_TTL_S` and
`MAX_CLAIM_LIFETIME_S` do not move with them.

Python: `scripts/testslots.py`, `testslots_policy.py`, `testqueue.py`, `queue_doctor.py`, `queue_notice.py`, `workspace/slots.py`,
`hooks/tree_watch.py` (already an empty stub), `testing/cores.py`; `testslots_pytest.py`, `testslots_runner.py` and `testslots_rpc.py`
lose their file locks and their calls into `testslots.py` and keep the runner side; `serve/broker_holds.py` loses `take_cores`
and `give_back_cores`, `broker_wire.py` the op `cores`, and the broker its `cores`, its `broker-leases.json` and its in-memory
claims (it reads the job index); `testjobs.py` loses `spec.json`, `status.json`, `followers.json` and keeps spawning, log and
junit files; `testreuse_run.py` waits on the run's job instead of a pid; `ci_watch.py` loses `state_dir`, `load`, `save`,
`records`, the markers and the lock; `primary_sync.py` loses its lock
and again files; `trees.py` loses `stale_lock` and reads the lock reason as a job id; `task_integration.py` and
`integration_git.py` take their lock reason from the job; `worktree_lifecycle.py`, `task_worktree_recovery.py` and the record
half of `worktree_archive.py` go, and so do the calls into `worktree_lifecycle` of `harness_claims.py`, `harnesshook.py`,
`harness_session.py`, `codex_hooks.py`, `task_runtime.py`, `notification_reader.py`, `task_cli.py` and `board_cli.py`;
`claims_cli.py` shrinks to the verbs; `leasetable.py`, `api/leases.py`, `api/pool.py`, `gate.py`, `board/session.py` claim
methods and `harness_claims.py` call `job_*`; `agent_sandbox_profile.py` keeps the folder (it holds the admission socket) and
no permit file is read; `workspace/limits.py` loses `claim_ttl_s`; `runstatus.py` reads jobs; `jobs.py` (`poolhouse-jobs`)
writes `detach` jobs. For the news view: the `announce` verb of `board_cli.py` and `board/public.py`, the posts of
`claude-subagent-start`, `claude-subagent-stop`, `stop_jobs.py`, `stop_landed.py`, `status_cli.py` and `testboard.py`, the
`announce` entry of `guard/workspacecmds.py` and the announcing text of `onboard.py`.

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
processes for the reaper); `broker.json` (the broker's port and token: connection information); `lease-history.ladybug` (a
graph of ended model leases for later measurement, written from the ended `serve` job); `lock.py` for exclusion around file writes; test duration, history and reuse stores (measurements)
and the reuse store's per-file marks; job logs and junit files (data named by job id); `lease-config.json` (capacity, not
state); `testreap.py` (reads processes only).

## 6. Dead workers

- **Process gone, node up.** Every `job_*` call, a ten second tick (new: today the sweep runs only when a lease call
  arrives) and the start sweep check each open local job's process (pid and start time, `lease/live.rs`). The node writes
  `end why=dead` under `node`, request id `end:<job>`, then grants the next job, with `after` naming the one it replaced.
  A queued request not polled for 60 s ends `abandoned`.
- **The start time is in the `grant` row.** The node reads the process start time at the grant and writes it as `pid_start`.
  At start, a job whose grant row names a pid and no start time is dead (a reused pid cannot be told from the holder); one
  whose start differs from the process now running under that pid is dead.
- **Unknown is not dead.** `live::inspect` returns `None` for a process that does not exist and also for any other failure,
  and drops the error (on macOS a short answer of `proc_pidinfo`, on Linux an unreadable `/proc` entry); `is_alive` reads
  `None` as dead, so a process the node was not allowed to read (`EPERM`) reads as gone. `inspect` becomes three answers: seen,
  gone (`ESRCH`, no entry) and unknown (`EPERM` or unreadable). Unknown keeps the job, shows it as `unverified` in
  `scripts/status`, and leaves it to its time-to-live.
- **Time is monotonic.** Beats and time-to-live are measured on the node's monotonic clock (`Instant`, which does not run
  while the machine sleeps). Today `expires_ms` is `Leases::now()` plus the time-to-live in wall milliseconds, so a sleep or a
  clock step makes every job's expiry pass at once and the sweep ends them all. A gap of more than 30 seconds between two
  ticks (a sleep or a stopped node) is not counted: the node gives every open job its beat time back by the gap. Wall time is
  written to rows and is never compared with a beat.
- **A quiet hold.** The GPU line of a request and a server line stay in memory, as today (the 600 second `QUIET_MAX_TTL_S`
  bounds one renewal only; a holder that keeps renewing keeps the hold and no row is written). Once a quiet hold has renewed for
  10 minutes the node writes its `grant` row, so it is a job in the log with the holder's session, and once it has been held 8
  hours the node writes one `note` ("held 8 hours") and tells the owner. It ends nothing for time: the owner removed lifetime
  caps (`fix/node-limits` removes `MAX_CLAIM_LIFETIME_S` and `QUIET_MAX_TTL_S`), and the holder's process ending ends the hold.
- **Node down.** At start the node folds the log, checks every open local job's process and ends the dead. A job with no
  process (a `remote` holder) lives by `ttl_s` from the restart.
- **Session retired or removed from idle.** The node ends its open jobs `why=retired`.
- **Another device's job.** No device ends another's job. A device unheard for 10 minutes (new, `partition_grace`) shows
  its jobs as `stale`; after `ttl_s` a stale pool-wide claim no longer holds a local request back. When the device returns and
  its earlier row wins the order, the local job loses that resource (section 7).
- **A tree left behind.** The dead session's `work` job is ended; the tree is `work` in git and shows its last owner. A new
  session opens a `work` job with `resumes` naming it. This is allowed only when the node ended the old job (verified death
  or expiry); a live job is never taken.
- **Two ends.** The node and a returning session may both write an `end`; the first stays and the second returns it.

## 7. The pool

Job rows replicate exactly as board rows do (`sync.rs`; per origin, through a verified head, at most 400 rows and 2 MiB per
request); the job chain is one more chain per origin in the same exchange. A member's `scripts/status` folds every origin it
holds: a job of device d2 shows with its session as `name@d2`, its state, its age and the age of the last row heard from d2.
Rows dated more than five minutes ahead are held back as today. Admission stays per device: this design adds no request that
opens a job in another device's table (leases on a remote device remain unbuilt, `node.md`, "What is left"). Clocks are
the hybrid clock of the row; nothing orders by the reader's wall time (service checklist, items 3 and 10: no tenant field
is added; ids do not assume one machine).

**Whose rows count.** Three facts about the log today decide this, and slice 0 changes the first two.

- `Node.pool` is `String::new()` (`node.rs:157`) and is never assigned, so every head signs the empty pool id
  (`node.md`, "What is left"). A log of another pool for a board with the same id (a board id is the project's id, a short
  word) is not told apart by its heads.
- `Board::store` pins the first key it sees for an origin, and `check_heads` takes that key from the head's own body when
  nothing is pinned. A relayed log is accepted under any key; a member can hand another member a log of an origin signed by a key
  of its own making, up to the 64 origins a device holds.
- `check_chain` requires only that a device's clock never goes back from its previous row, and the first row of a log has no
  previous row. A member can therefore write rows dated long ago.

So: slice 0 puts the pool id in the signed head and makes a device that founds or joins a pool start a new origin for each
board (its earlier heads sign the empty pool id and would fail; the earlier logs stay on its own disk and are not exchanged). `Index::apply` counts a row only when the origin's writer is an
active member of the pool, and an origin whose key is no member's is stored and counted for nothing.

**Pool-wide claims.** The earliest acquire in the board's order holds a branch or an area (section 3.1, item 3). The loser
loses that resource only: a `hold` job of it ends `lost`; a `work` job drops that one area or branch from what it holds, the
node writes a `note` with `lost`, and the job goes on with the rest. A member that writes rows dated long ago wins every such
race until it is removed; section 12 names this as accepted.

## 8. Size bounds

Every bound here protects memory, disk or a row's size. This design adds no rate limit and no lifetime limit (section 4.7,
section 6): the owner is removing them (`fix/node-limits`, whose `docs/limits.md` lists the bounds that are kept).

| Bound | Value |
| --- | --- |
| Row | at most 4 KiB, except an `areas` note and a `carry` row of a `work` job at most 48 KiB (128 areas of at most 300 bytes is 37.5 KiB; the existing body bound `BODY_BYTES` is 64 KiB); title 160 bytes; step 120; blocked 200; outcome 200; `on` fields 200 each |
| Open jobs (held and queued) | 64 per session, 1,024 per device (the lease table has no bound today); 1,024 per foreign origin in the index |
| Notes | 200 per job |
| Areas held by one job | 128 (past that, a directory area) |
| Job chain | 200,000 rows per origin: a new segment at 150,000, `open` and `grant` refused at 180,000 (section 3.7); at most 1,024 `carry` rows after a `base` |
| Entry log | 200,000 rows and 128 KiB a row per origin (as today, `MAX_LOG_ROWS`, `MAX_ROW_BYTES`); 64 origins |
| Index | every open job, the last 1,024 ended jobs of each kind per device of the current segment; startup folds the segment once (`Tally` precedent) |
| Foreign view | at most 256 open jobs shown per device, then "and N more" |

**Volume, from the real board.** The reviewer counted the owner's board `poolhouse-659a2c` (the author did not read the real
state root): 57,604 rows in 8 days, an average of 7,200 a day and 16,400 on the busiest day; messages 46 percent, landing
entries 25 percent, `lease` entries 21 percent. If those are one device's log, its 200,000-row cap is 19.8 days away at the
average and 8.7 days at the busiest day, before a single job row (the reviewer's "12 days at the peak" counts from an empty
log). The cap is per origin. A family at today's limit of 150 rows a minute writes 216,000 rows a day, more than the whole
cap, so a rate limit does not bound the log; a segment does.

What the design adds, by estimate: a test run is 3 rows and its `reuse` hold 3 more; about 240 runs a day (724 stored job logs
kept for 3 days, `test-execution.md`) is about 1,400 rows. A `work` job is 3 rows plus an `areas` note a minute while editing,
about 100 rows; 20 a day is 2,000. `ci` and `sync` add about 400. That is about 3,800 job rows a day in a chain of their own, so
the chain starts a segment about every 40 days and an idle log never refuses. Slice 2 adds a rows-per-day counter and the
percent of the segment to `scripts/status`; the real number replaces this estimate.

**Not part of this design, and named so it is not lost.** The entry log (messages, landing entries, identity, audit) has no
segment start. Without its 21 percent of lease entries it still gains about 5,700 rows a day on the reviewer's count, so a log
that holds 45,507 such rows today reaches its cap in about four weeks, and `Board::add` then refuses every row, a head
included. The mechanism of section 3.7 (a `base` row, carried state, an atomic new segment) is what that log needs; it is a
separate piece of work and a pending item for the lead, not a slice here.

The append checks `idem` by scanning the origin's rows; slice 1 measures it at 200,000 rows and adds an index if it is over
5 ms. The write-ahead flush costs one `fsync` per grant, as a lease entry does now.

## 9. Migration

None. The device refuses only its own old state: an old `leases.json` in its state folder, or `lease` rows in its own entry
log. It says one plain sentence ("This pool was made by an older version of Poolhouse and cannot be used; make a new pool"), and the
device makes a new pool and fresh state, as "Nothing is cemented until 1.0" says. A row of a retired kind that arrives from
another device is never a reason to refuse anything: `fold::entry` rejects a kind with no arm ("this kind of entry is not
accepted yet") and `Board::reject` records the row, so one member's foreign `lease` row cannot make every device abandon its
pool. `Kind::Lease` stays in the enum so the row parses. Leftover files of the retired pieces (`dev-test-slots` permit files,
`ci-watch`, `worktree-lifecycle.json`, `local-agents/*.assigned`, `broker-leases.json`) are not read and no code deletes them;
they hold records of runs that ended. The cost falls on the owner's devices only: each is re-added to the new pool by
`python -m poolhouse.device_setup --yes`, run by an agent.

## 10. Onboarding impact

No human step is added. Job rows are written by commands and hooks that agents already run (SessionStart, the claim verbs,
`scripts/test`, the `pushed` hook); no new prompt, flag, code or file for a person. The token is the identity, so
nothing is registered by hand. A new person sees no change; only the owner's existing devices see the refusal of
section 9, once. `scripts/status` needs no network for the local part, and its `github:` section is left out when GitHub does
not answer in 3 seconds. The runner needs a node: a device that runs an agent has one, and `scripts/test` on a bare checkout
starts the device's node through the supervisor and never touches the real state root from a test (slice 4 carries a
fixed-cost test for that start).

## 11. Build plan

Each slice lands alone, with its tests written first and watched failing, the attack test of section 12 for what it touches, and
an affected-selector run on this device and on the WSL device for process and lock behaviour. No slice leaves two ways to do
one thing: a slice that moves a consumer deletes the old consumer in the same commit, and a slice that deletes a sender lands
after the views that replace it.

| # | Slice | Delivers | Tests |
| --- | --- | --- | --- |
| 0 | The footing | the pool id in the signed head (`Node.pool` assigned, the head message verifies under the receiver's pool); a device that founds or joins a pool starts a new origin for each board; `ingest` of a relayed origin needs the writer's key to be an active member's; `node` in `RESERVED` | a head signed for pool A is refused by a member of pool B; a relayed log signed by a key no member holds is not stored as a member's; a joined device's earlier log is not exchanged; no session can be named `node` |
| 1 | The job chain | the second chain per origin, `Kind::Job` and `Kind::Base`; 75 percent segment start with `base` and `carry` rows, written atomically; 90 percent refusal of `open` and `grant` only; the first-seq addressing in `check_chain`, `accept`, `trusted`, `cut`, `acknowledge`, `rows_since`; the stream name in the head | a chain driven to 150,000 rows starts a segment that holds every open job; at 180,000 an `end` is still written and an `open` is refused; a member holding old rows continues across a base; a member offline past the segment retention starts from the base; a hostile base is refused (a lower `seq`, a `rows` over 1,024, a duplicated job, a writer who is no member); a crash during the start leaves the old segment; a head of one chain does not verify in the other; the first sync of a new device is at most 3 rounds; `idem` scan at 200,000 rows |
| 2 | The log is the table (Rust; the same calls, a different store) | `jobs/` with `Index::apply` and `view.rs` (pool-wide holds of other devices); `leases.json` deleted, the table is the fold; `open`, `grant`, `end` rows with write-ahead for `open` and `grant` (queued requests are now written); the refusal of the device's own old state; the lease arm of `fold::entry` deleted; the rows-per-day counter and the 75 percent verdict in `scripts/status`; glossary row | port `lease.rs`, `lease_parties.rs`; new: cross-origin `end` ignored, an `end` never refused for a full log, a replayed request is one row, a foreign `node` row ends nothing local, a foreign `lease` row is rejected and refuses nothing, a restart folds to the same table, an earlier foreign row arriving late changes the winner and costs only the contested resource, an origin of a non-member counts for nothing, a rebuild when a log is refetched to the same length, fixed-cost bound of the start fold |
| 3 | The job calls | `job_open`, `job_wait`, `job_note`, `job_beat`, `job_end`, `job_follow`, `job_list` with `what`, `key`, `on`, `once`, notes and follow; the verbs, `leasetable.py`, `api/leases.py`, `api/pool.py`, `gate.py`, `board/session.py`, `harness_claims.py`, the serve broker (its holders read from jobs) and the request gate call them; `claims.rs` and the `claim`, `reserve`, `release`, `claims`, `lease_*` methods deleted; path claims confined to `git worktree list` paths; ancestors end a child's job; foreign job text screened | port `claims.rs`, `claims_foreign.rs`, `test_leasetable.py`, `test_board_claims.py`, `test_workspace_claim_output.py`; new: a parent and a grandparent end a child's job, a stranger cannot, `once` of one actor never returns another's job, a claim of `/` and of a planted `~/.git` home are refused, a quiet hold is written after 10 minutes |
| 4 | Test permits | `test` jobs with `cpu_slots min..count` and `lanes`; exempt from `work_gate` and the 90 percent refusal; the worker count from the grant; `testslots.py`, `testslots_policy.py`, `testqueue.py`, `slots.py`, `testing/cores.py`, the broker's `cores` and the file locks of `testslots_pytest.py` and `testslots_runner.py` deleted; the runner holds the job | port the `testslots` tests onto a private node (`tests/node_kit.py`); a killed runner ends `dead` and frees its slots; a stale-build node and a full job log still grant a `test` job; a sleep gap ends no job; fixed-cost test of reaching the node |
| 5 | Test jobs and the reaper | follow, cancel and status as `follow`, `end` and `job_list`; `testjobs.py` loses its records; `queue_doctor.py`, `queue_notice.py` replaced by the node's sweep and notices; `stop_jobs.py` reads jobs; one `reuse` hold per run, the reuse store's marks name the job; a main session of the device ends a queued `test` job or one whose runner stopped renewing | port `test_testjobs*.py`, `test_queue_*.py`, `test_subagent_stop_jobs.py`; two runs of one tree and selectors follow one job; the last `unfollow` ends it; a gate run of 920 files writes under 10 rows; a follower with another tree hash does not take the result |
| 6 | Worktrees | `work` and `tidy` jobs; `worktree-lifecycle.json`, `.assigned` deleted; the git lock mirrors the job (`job:<id>`); `trees.py`, `scripts/worktrees` and the Stop hooks and the other readers of section 5 read jobs | port `test_trees.py`, `test_worktree_lifecycle.py`, `test_subagent_stop_landed.py`; a dead owner leaves a `work` tree that a `resumes` job takes; a terminal's `git worktree remove` of a tree with an open job is refused by git; sweep ignores job text |
| 7 | CI watcher and status | `ci` jobs; `ci-watch` deleted; `runstatus.py` reads jobs and prints a section per device; the `github:` section with a 3 second timeout and a 60 second cache | port `test_ci_watch.py`, `test_status.py`; a second call while the job is open follows it and after it ends reads GitHub; a job of a second device shows as `name@d2` and `stale`; GitHub not answering leaves the section out |
| 8 | Sync | `sync` jobs; the `primary-sync` files deleted | port `test_primary_sync.py`; a second request while a sync runs is a `follow` row and makes the holder go round again |
| 9 | News and quotas | the `news` view in `inbox` and `digest`; the `announce` verb, the subagent hooks' posts, the other posters and the send and announce keys deleted; the readers of section 4.6 moved; the AGENTS.md and CLAUDE.md edit; no `Rates` caller outside these keys is touched | `news` shows landed, blocked and done once per job; a subagent's done reaches its parent through the `agent` subscription; no reader of `#announcements` is left |
| 10 | Cleanup | `jobs.py` writes `detach` jobs; the remaining files and docs of section 5; budgets lowered with `scripts/budgets --update`, said by how much | the budget and layer checks; `scripts/test gate` on the combined tree |

Slice 0 lands first, then slice 1, then slice 2, then slice 3. Slices 4 to 8 may land in any order after 3. Slice 9 lands after
slices 6 and 8, because it deletes `announce` and the hooks' posts and the news view is built from `work` jobs.
Slice 10 is last. Slice 4 is the first high-volume producer and follows slice 1 by construction.

## 12. Attack first

Written by the author and revised after one non-author review (section 14); AGENTS.md requires a non-author to break this
before it is built, so the pass below is the brief for the next review. Each case says what the attack is and what stops it.

- **Copied or cloned pool or device.** A copied log gives history only: job rows grant nothing outside their own device, and the
  copy's node ends every local job whose process is not alive at its first sweep. A cloned device shares a device key and
  origin; two copies appending to one origin fork the chain, other members mark the origin damaged and drop the rows after the fork
  (existing rule), so the clone costs a device its status and nothing else. A pool copied to new devices is a new pool by
  `Pools are not preserved`, and a device that founds or joins a pool starts a new origin (slice 0). Residual: a clone makes its source
  disappear from status until it is removed.
- **Replay.** A captured row replays as the same row (id, hash, `idem`) and is kept once. A captured request replayed to the
  node returns the first result (section 3.5); with a stolen token it is a stolen token. A row copied into another board is
  refused by its board id in the hash. A log relayed from another pool is refused by the pool id in the head's signed bytes
  (slice 0) and, if the writer's key is no member's, counts for nothing (`Index::apply`). Today none of this holds: heads sign
  an empty pool id, and a relay can hand a member any origin under a key of its own making (section 7).
- **Forged or below-threshold signature.** A row is stored only through a head the pinned device key signed; an unsigned tail
  is not stored and not relayed. No job row can change membership, a grant, an approval or a release, so no threshold applies to
  it. A forged `end` of another device's job fails the signature or the same-origin rule (3.2, rule 3).
- **Hostile writer of a segment start.** A member writes a `base` and `carry` rows that lie. It can list jobs it never opened
  or leave out jobs it did: both only change its own jobs (rule 3), which the foreign view shows under the 256 cap, and the 1,024
  open-job bound refuses more. A `base` with a lower `seq` than the rows another device holds, or a
  different hash at a `seq` it holds, is a fork and marks the origin damaged; one whose `rows` is wrong, whose `carry` rows
  are not directly after it, or that names a job twice, makes the log damaged. A device that relays a `base` it did not write
  cannot forge one: the head covering it must verify under the writer's pinned key, which the relay does not hold. It can serve an older segment (ignored: the
  rows are compared by hash and a lower `seq` adds nothing) or withhold the newest, which shows as the origin going `stale`
  after 10 minutes. A `base` of another pool is refused by the pool id of the head. A segment is applied to the index only
  when complete, so a half-served start changes nothing.
- **Compromised or malicious member.** It can write false rows for its own device only: a job under any session name it
  invents except a name a local session holds (the fold refuses a foreign sender named like a local session). It can fill its own
  chain; its segment starts, the per-origin bounds of `Index::apply` and removal bound that. It can win pool-wide branch and area
  claim races by holding back rows and releasing them late, and also, more simply, by writing rows dated long ago: `check_chain`
  only requires a device's clock never to go back. Each such win costs the losers one contested area or branch, not a
  job. Accepted: those claims serialize cooperating tools and are not a gate (git's push check and the `main` approval are), and
  removing the member cuts its log. The dev branch is no longer claimed pool-wide, so it cannot be held this way.
- **Stolen key.** A stolen device key signs rows as that device: jobs on that device only; the remedy is removal, which cuts the
  log. A stolen token acts as its session: opens and ends that session's jobs and nothing else, spends no grant from another
  terminal (lineage, existing). A stolen phone or person key has no use here: no job row takes a person signature and no
  view reads one.
- **Malicious agent.** It can open jobs to hog resources: bounded by the open-job bounds; a live process that keeps renewing keeps
  its jobs, which the 8 hour notice shows to the owner and which ending the process ends. It can hold a path claim to starve
  others: a path claim must lie below a path `git worktree list` prints for a git source of the job's project, or below a
  registered source, so `/`, a home directory and a home that a planted `~/.git` file makes resolve to the project are all refused
  (slice 3). It cannot end another session's job (its own session, its ancestors and the node only; section 3.8); a subagent
  cannot end its parent's. It cannot make a follower take a false verdict: `once` never answers with another actor's job, and a
  follower reads junit and exit files bound to its tree hash. Sweeps and landing trust git and ids, never an `outcome` it wrote.
  The reviewer exclusion reads `work` rows as it reads claim rows today, so an agent that mutates without the hooks is as
  invisible as it is now (unchanged residual).
- **Injected instruction.** A title, step or blocked reason carrying an order is cleaned, screened and held as a foreign message
  is (rule 6), fenced as data when shown, and read by no hook for a decision; hooks and views read ids, states, counts and git. An
  injected "end job X" can end only the caller's own jobs. `scripts/worktrees sweep` and `close` decide from git and from open
  local jobs, so a false row from any device cannot cause a deletion; a local false row can only keep a tree for at most its
  lifetime.
- **Downgrade.** An older node cannot parse `Kind::Job`, refuses the log and is not a member of the pool in practice
  (no mixed-build shim). A job kept out of the log needs `quiet`, which the node allows only for the GPU request line and the
  server line, and a quiet hold older than 10 minutes is written; any other `quiet` is `invalid`. A failed append refuses an
  `open` or a `grant` (write-ahead) and never an `end`, so a full disk or a full chain cannot become a silent grant and cannot
  keep a freed resource held; the segment start of slice 1 keeps the chain from filling. No flag or variable turns the log off.
- **Approval fatigue.** The job log adds no prompt and no approval reads a job row. Repeated `blocked` notes collapse to one
  line per job in `news`, are idempotent per reason and count against nothing else; the node's own notices (expired claim, quiet
  job, held 8 hours) are one per job and event. The lead's attention line stays one cached line.
- **Takeover by whoever is present.** A process with no token writes nothing. Registering an existing main session from
  another terminal still needs the lineage or a person's `rebind`, so presence does not give its jobs. A dead worker's job is
  not taken: the node ends it on verified death or expiry and a successor opens its own job (`resumes`), which the node allows
  only after that end. An open pool admits any device on the network as a member; its rows then have exactly the reach of the
  malicious-member case, and the sentinel and flags for open joins still apply.

## 13. Decisions

All three open decisions of the earlier draft are decided; nothing waits on the owner.

1. **How does the log stay bounded?** Decided by the reviewer: (b), a per-device job chain that starts a new segment with an
   origin-signed `base` row listing its open jobs (section 3.7). It lands before the log is the table (slices 1 and 2). The cost
   is a second chain per device to verify and a second vector entry per origin in each exchange; what it avoids is a
   board-wide retention scheme before any job row is written.
2. **Does `scripts/status` still read GitHub for runs no watcher opened?** Decided: (a), with one read-only `gh run list`, a
   3 second timeout, a 60 second cache and the section left out when GitHub does not answer; `github_status.py` stays for the
   stuck explanation (section 4.3). The cost is a network call in a view.
3. **Who may end another session's job?** Decided: (a), amended. A job's own session, any ancestor of it (not only the parent)
   and the node; and the node ends a queued `test` job, or one whose runner stopped renewing, at the request of a main session of the same
   device (section 3.8). The cost is that a main session of a device can end another session's queued or stalled test run.

## 14. Review log

The non-author review said REJECT. Each finding was checked against the code first (`origin/0.3dev` 63b9912e9 and the
branch `fix/node-limits` 814dc686b, which is not landed). Slice numbers in the findings are the old ones; the new ones are in
section 11 (old 1 is now 2, old 2 is 3, old 3 is 1, old 4 is 9, old 5 to 9 are 4 to 8, old 10 is 10; slice 0 is new).

| Finding | Checked | What changed |
| --- | --- | --- |
| HIGH 1: the 200,000-row cap arrives before any job row; write-ahead at the cap denies every grant and `end`; `reuse` writes about 2,800 rows a gate run | The volume figures are the reviewer's count of the real board; the author does not read the real state root. Arithmetic checked (7,200 a day; 19.8 days at the average and 8.7 at the peak from the current rows; the reviewer's 12 counts from an empty log). Code confirmed: `Board::add` refuses every row at the cap, heads included; 920 test files and one `Store.claim` per lookup key (`testreuse_run.py:320`); `check_chain` starts at `GENESIS` and `Log::open` passes no tip, so a base row needs new code; `accept`, `trusted`, `cut`, `acknowledge` and `rows_since` index by `seq - 1` | Retention is slice 1, before the log is the table; write-ahead only for `open` and `grant`; opens and grants refused at 90 percent, a segment started at 75; `end` and the like never refused; `reuse` is one hold per run; volume restated from the real board (section 8); decision 1 is (b) with the base row and its attack answers (sections 3.7 and 12). Also found: the entry log itself fills in about four weeks (section 8, named as a separate piece of work). Also corrected: "queued 8 per session (as today)" was false, no such bound exists; the per-session 64 and the per-device 1,024 are new bounds, the family 256 and the row rate are dropped |
| HIGH 2: a `lease` row in the log makes the device abandon its pool | Confirmed: `fold::entry` rejects a kind with no arm, and `Kind::Lease` is in the enum so the row parses | Section 9: only the device's own old state (its `leases.json`, `lease` rows in its own entry log) is refused; foreign rows of a retired kind are rejected like unknown ones; `Kind::Lease` stays parse-only |
| HIGH 3: "cross-pool replay refused by board id and pinned key" is false | Confirmed: `Node.pool` is `String::new()` (`node.rs:157`) and never assigned, heads sign an empty pool id (`node.md` says so too); `Board::store` pins the first key and `check_heads` takes an unpinned key from the head's body, so a relay can hand over any origin; `check_chain` needs only a non-decreasing clock per origin and the first row has no previous one | Slice 0 (pool id in the head, fresh origin on join, relayed origin needs an active member's key, `node` reserved); `Index::apply` counts only active members' origins; a lost pool-wide claim drops only the contested resource; backdating named as an accepted residual with its cost (sections 7 and 12) |
| HIGH 4: slice 4 cannot land alone; `Rates` also guards new boards and `pool_submit`; announcement readers missing | Confirmed: on `origin/0.3dev` `node.rs:271` (16 an hour per lineage), `api.rs:141` (links and projects a minute) and `poolapi.rs:135` (120 a minute) use `Rates`; `fix/node-limits` removes those callers and keeps `Rates` for the security floors, but is not landed; readers `subs.rs`, `rooms.rs`, `attention.py`, `claude-lead-attention`, `board_page.py`, `child_watch.py` found, and `rooms_cli.py`, `status_cli.py`, `testboard.py`, `queue_notice.py`, `onboard.py` and the two `.claude/agents` files as posters | The news and quota slice is 9, after worktrees (6) and landing (8); it deletes only the send and announce keys and touches no other `Rates` caller; section 2 and section 4.6 list every reader and poster, `subs.rs` included |
| MEDIUM 5: `apply(row)` does not exist | Confirmed; the nearest code is `Tally::catch_up` (a position cursor, reset only when a log gets shorter) and the private `apply` of `tasks/fold.rs` | Section 3.1 specifies `Index::apply`: winner is the least (hlc, origin, seq), re-evaluated when an earlier foreign row arrives; dedupe by (origin, actor, `idem`) as `rules::merge` does; held-back rows skipped; rebuild on cut, reset, forgive, damaged and member removal with a cursor tied to a hash; per-origin limits on foreign rows; holds of a removed or silent origin end or go stale |
| 6: path claims; `git_root` trusts any `.git` file | Confirmed: `registry::git_root` follows any `gitdir:` file, `Projects::resolve` accepts its result, `refuse_broad` runs only in `source()` | Section 4.1: a path claim must lie below a path `git worktree list` prints for the project's git source or below a registered source; slice 3 tests it with a planted `~/.git` |
| 7: dead workers: start time, wall-clock expiry, `EPERM`, a quiet hold with no row | Confirmed: `live::inspect` returns `None` for any failure and `is_alive` reads it as dead; `expires_ms` is wall time (`Leases::now`); a quiet lease writes no row. The 8 hour cap is not adopted as an end: the owner's order removes lifetime caps (`fix/node-limits` removes `MAX_CLAIM_LIFETIME_S` and `QUIET_MAX_TTL_S`) and AGENTS.md has the queue doctor end nothing for time | Section 6: `pid_start` in the `grant` row, a missing start means dead; monotonic time and a 30 second tick-gap grace; `inspect` answers seen, gone or unknown, and unknown keeps the job; a quiet hold writes its `grant` row after 10 minutes and a notice at 8 hours |
| 8: `once` and `follow` let any session end `ci:<sha>` or `land:<branch>:<sha>` green for everyone | Confirmed as a design flaw: `once` returned the first job of any actor and its outcome was trusted | Section 3.5: `once` is per actor; a follower joins only a live job; the verdict is GitHub's, git's or junit bound to the tree hash, never an `outcome` |
| 9: dropped guards: worktree lock, `runner.lock`, `branch-<hash>.lock`, readers of `worktree-lifecycle.json` | Confirmed: git, a terminal and `integration_git.py:137` honour the lock and read no job; `exclusive` wraps only `publish` and the already-landed finish, `one_run` is per branch; ten files read `worktree_lifecycle` (the reviewer named three) | Section 4.4: the lock stays as a mirror written from the job; section 4.5: a push takes no lock; section 5 lists every reader |
| 10: section 2 rows missing; the quota row is wrong | Confirmed each: `broker.json`, `broker-leases.json`, `lease-history.ladybug`, `testing/cores.py` with `take_cores`, `landings.jsonl`, `worktreerules.landing_lock`, `runtime_git.boundary`, `agent_sandbox_profile.py`, `api/pool.py`, `gate.py`, `testreap.py`. `broker-handoff.json` is named only in `scripts/test_kernel_role_home.py`; no source here writes it. `testreap.py` reads processes only | Section 2 rows added and the quota row rewritten; section 5 says what each becomes; `testreap.py` and `lease-history.ladybug` stay with their reasons |
| 11: slice 5, `work_gate()` refuses a stale build | Confirmed: `lease_acquire` calls `work_gate()` for every non-quiet request | `test` jobs are exempt from `work_gate()` and from the 90 percent refusal (section 4.2, slice 4) |
| LOW: `node` in `RESERVED` | Confirmed: it is not in the list (`row.rs`) | Rule 5 and slice 0 |
| LOW: foreign job text | Design gap | Rule 6: screened and held like a foreign message |
| LOW: `on.tree` replicates the absolute path | Confirmed: it is in the row body and every member receives it; the lease entries' `resources` lines already carry such paths | Rule 2: written relative to the home directory, said to replicate, last component shown elsewhere |
| LOW: 128 areas at 300 bytes exceed 16 KiB | Arithmetic: 38,400 bytes | `areas` notes and `carry` rows of a `work` job may be 48 KiB (under the 64 KiB body bound); a job holds at most 128 areas |
| Decision 2 | Adopted (a). The description given of `github_status.py` does not match the code: it reads githubstatus.com with a 3 second timeout; `runstatus.github()` runs `gh run list` with a 20 second timeout and no cache | Section 4.3 and slice 7 set the 3 second timeout and the 60 second cache, and leave the section out on failure |
| Decision 3 | Adopted (a), amended | Section 3.8: any ancestor; the node ends a queued or beat-less `test` job for a same-device main session |
