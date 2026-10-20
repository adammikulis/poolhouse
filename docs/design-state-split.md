# The state split

Design only; nothing here is built. The owner ordered the state that every device must see split from the state that exists on one
device. Today both go through one signed log: the board of this project took 57,581 rows in 7.49 days (7,683 a day) and the
quotas, caps, row bounds and the rotation work followed from that. Words follow [glossary.md](glossary.md). This text replaces the
storage and replication half of [design-job-log.md](design-job-log.md) and keeps its data model (section 2).

Checked against `origin/0.3dev` (bceddf929). Volumes: `PYTHONPATH=src:scripts python scripts/board_tally.py` (all rows, and
`--days 1.75`), read through the node on the owner's Mac from the board `poolhouse-659a2c` on 2026-10-19, plus two read-only counts
of the same board (identity rows by main session or subagent). The tally cannot read direct messages between other sessions (about
720 in the period, `board-writes.md`); the log file holds 32.6 MB for these rows, 566 bytes a row.

## 0. The rule

A row enters the log only when another device must read it to decide something, show something or verify something about the
pool, and the fact cannot be had by asking. Everything else is one device's own. It lives in a table or file the node owns on that
device and no tool has a path that writes it into the log. There are three places, one question each:

| Place | Holds | The test |
| --- | --- | --- |
| The log (signed, copied to every member that holds the board) | governance, membership, shared messages, notes, tasks, grants, landing reviews, pool-wide claims | Does a different device read this row to decide, show or verify? |
| A local table or file (the node owns it; Python tools call the node) | work, leases, claims of one device, mail between sessions of one device, sessions, presence, diagnostics | Does only this device act on it? |
| An ask (section 3): a signed answer a device gives on request | what another device is doing now | Does a person or agent want to see it now, with nothing decided on it? |

Two designs were considered and rejected. A flag on a row that sync skips: each row's `prev` is the hash of the row before it, so
a member that skips a row cannot verify the log. State written to the log as periodic or per-change summaries: the same flood at a
lower resolution, and nothing reads a summary to decide.

The boundary is structural, because the cuts of 2026-10-17 to 2026-10-19 (`board-writes.md`) depend on every worktree having
merged them: in the last 1.3 days 6,396 of 8,108 rows were still test-runner rows from worktrees on older code. After this design
`fold::entry` accepts only the kinds and keys of table A, the node's `post` refuses a progress report to `#general`, and a test
lists every `append` call site in `app/poolhouse-node/src` and fails on one that table A does not name. Tool versions no longer
matter.

## 1. Every kind of state

Surveyed in `app/poolhouse-node/src` (`lease/`, `claims.rs`, `sessions.rs`, `board.rs`, `landing.rs`, `tasks/`, `presence/`,
`rooms.rs`, `subs.rs`, `notes.rs`, `files.rs`, `scratch.rs`, `notice.rs`, `events.rs`, `api.rs`, `poolops.rs`, `grants/`,
`approvals/`, `shard/`) and in the Python tools named in design-job-log section 2 (the files this design touches were opened;
`trees.py`, `harness_claims.py` and `testboard.py` are under `src/poolhouse/` and `src/poolhouse/workspace/`). Every place the
node appends a row was listed with `grep`: 23 call sites, each writing a row of table A or a copy that S2 removes. "Was" is rows
a day in the 7.49-day tally.

### A. Replicated: the log

| State | Writer, reader | Why it replicates | After the split |
| --- | --- | --- | --- |
| Charter and acts (`gov/`, `pools/<id>/acts.json`) | admins' signers; every member's roster | membership and who governs are verified by all | unchanged; its own log, not a board |
| Pool events on the board `pool` (`poolops.rs`: admit, remove, leave, `shard_consent`, `shard_allow`) | node; every member | a member verifies who is in the pool and who allowed shards | unchanged, rare |
| Identity of a main session (register, model, retire) | node; every member | a main session is addressed and shown across devices | main sessions only (was 12 a day of 142); a subagent's identity leaves (130 a day) |
| Messages to `#general`, `#announcements`, a room, a session of another device; file messages | sessions; members | shared by definition | `status` to `#general` refused; a message between two sessions of one device leaves (section 1.B, mail) |
| Rooms (`room`) | sessions; fold on every device | members must agree on who is in a room | one row, not a row and an audit copy (`rooms.rs:201-202`) |
| Notes and `verify` rows | sessions; members | shared knowledge, and who ran the check | unchanged |
| Tasks (`task`) | sessions; members that trust the writer (`landtrust`) | the shared task board; `tasks/fold.rs:344` also reads other devices' held claims | unchanged; no `enforcement.set` audit copy |
| Grants: issue, revoke, use (`grants/`, `approvals/decide.rs`) | a person's signature or approval; members | four actions travel between devices (`task_authority`, `note_verify`, `sync_now`, `shard_call`) | unchanged; no volume (zero rows in the tally) |
| Landing reviews (`landing.rs`) | reviewer session; pre-push and `land_accepts` | an attributable verdict about a commit that any device may push | the `review` row only (was 43 a day, plus a 43 a day audit copy); queue kinds stay retired |
| Pool-wide claims (`lease` rows) | the holder's device; every device that wants the same thing | `branch` and `area` name the shared repository (areas are `/.poolhouse-areas/<project>/<path>`, not device paths); `tasks/fold.rs:344` reads held `task` claims and `model_slot` leases to tell that a worker still holds its lease, counted only for the worker's own origin (section 3.1) | only these four keys (branch and area were 416 a day; the other keys 1,228 a day) |
| Authority audits: `land.trust`, `land.untrust`, `quarantine.release`, `authority.set_denied`, and a session ended for cause | node; every member | a record of a change of authority that no process of one device can rewrite | stay log rows (node class, a few a day) |
| Heads | node; verifying members | every stored row hangs from one | unchanged |

### B. Local: tables and files the node owns on one device

| State | Where, writer, reader | Why it is local | Target |
| --- | --- | --- | --- |
| Lease table: gpu, cpu_slots, memory_mb, model slots, `worktree`, `port`, `server`, `install` claims, queue, quiet leases | `leases.json`, memory; `lease/rpc.rs`, `claims.rs`; `leasetable.py`, `gate.py`, `api/pool.py`, serve broker | one device admits its own resources; `lease/merge.rs` already scopes these to the writing origin, so no other device uses the row | local job table (S4); no row |
| Creators ledger (new) | node writes at a `branch` or `worktree` grant; `landing.rs` reads | `landing.rs::creators` reads only the device's own `lease` rows (`ever_held`, `!e.foreign`) | `local/creators.json`, S1; durable and fail-closed (section 3.2) |
| Sessions: tokens, lineage, names, subagent identities, `seen_ms`, idle sweep | `tokens.json`, `names.json`; `identity.rs`, `idle.rs`, `writes.rs::is_live` | secrets and this device's registry; liveness is read here only | unchanged files |
| Subscriptions, inbox cursors, unread counters | `subs.json`, `names.json`, `tally.rs` | per reader on this device | unchanged |
| Mail between sessions of one device and the node's notices (`notice.rs`: claim expired, subagent retired) | log today (96 a day of direct messages; notices) | nobody else reads it, and today every member's copy of the log holds it | `local/mail/<board>.jsonl`, S3 |
| Diagnostics and device-only audit: `link`, `unlink` (`api.rs:168`, on both boards), `rebind`, `file.delete`, `agent.effort`, `announce_over_quota`, subagent retire | audit rows (59 a day for retire alone) | the effect is on this device and changes no authority; a record is wanted, a copy on every member is not | `local/events.jsonl`, S2 (a hash chain with a retention per class, section 3.2) |
| Quarantine records, scratch folders, file bytes and staged uploads | `quarantine`, `scratch/`, `files`; node | bytes and flags for this device; the `file` message is the replicated part | unchanged |
| Projects, links, land trust, member addresses and facts (`facts.rs`: "never replicated"), board bookkeeping (`state.json`: acks, damage marks; `rejected`) | `projects.json`, `links.json`, `land_trust.json`, the addresses file, memory | configuration and verification state of this device | unchanged |
| Person keys, phones, approvals, fatigue, presence prompts | `persons.json`, `phones.json`, `approvals.json`, `fatigue.json`, `presence/` | secrets and prompts for the person at this device; the decision replicates as a grant row (A) | unchanged |
| Shards (`shard/`, `shards.json`) | node-to-node ops already, run here for another device | work done for a member, asked and answered, never a row | unchanged: the model for section 3 |
| Limits and configuration | `write-limits.json`, `lease-config.json`, in-memory `Rates` | settings of one node | unchanged |

### C. Local: Python tooling (device files today)

| State | Where, writer, reader | Target |
| --- | --- | --- |
| Test permits, cores for pytest workers | `~/.cache/dev-test-slots`; `testslots*.py`, `testqueue.py`, `testing/cores.py`, broker `cores`; read by `runstatus.py`, `scripts/test status`, `queue_doctor.py` | `test` jobs, a `cpu_slots` grant (S5) |
| Test jobs, reuse marks | job folders (`testjobs.py`, 3 days), `inflight/*.json` (`testreuse_run.py`) | `test` jobs and one `hold` per run; logs and junit files stay (S5) |
| Worktree locks and ownership | `git worktree lock`, `worktree-lifecycle.json`, `local-agents/*.assigned` (`trees.py`, `task_worktrees.py`, `harness_claims.py`) | `work` and `tidy` jobs; the git lock mirrors the job (S6) |
| Landing locks and history, primary sync | `<git-common>/land/*.lock`, `landings.jsonl`, `primary-sync.*` (`land_steps.py`, `primary_sync.py`) | `land` and `sync` jobs; `landings.jsonl` stays (S7) |
| CI watcher | `<git-common>/ci-watch/<sha>.json` (`ci_watch.py`) | `ci` jobs (S7) |
| Install claims of a runtime switch | claims (`runtime_deploy.owning`; was 1,083 a day, now 4 rows a switch) | an `install` hold in the local table (S1, S4) |
| Serve broker | `broker.json`, `broker-leases.json`, `lease-history.ladybug`, `servers.json` | stay, except `cores` and the broker's claims (S5) |
| Detached commands | `jobs.py` | `detach` jobs (S10) |
| Hook posts: subagent `done` (`claude-subagent-stop`), `stop_jobs`, `stop_landed`, `queue_notice`, `ci_watch` task messages | `#announcements` or direct messages | local mail to the parent or owner on this device (S3) |

### What the survey corrected

- `leases.json` is already a local table. What is wrong is `mirror` (`lease/rpc.rs`), which writes every non-quiet grant and end
  of it to the log. The table stays; the mirror narrows.
- Landing reviews count only on the device that wrote them (`landing.rs`, `e.origin == own`) and each also writes an audit copy.
- The one reader of `worktree` lease rows is `landing.rs::creators`; it needs a local ledger before those rows stop (S1).
- `tasks/fold.rs:344` reads other devices' held leases through `merge::view`, so `task` claims and `model_slot` stay rows.
- Direct messages replicate to every member today (`node.md`, "Direct messages"). A malicious member reads every conversation
  between two local sessions, and every absolute worktree path. This design closes both (section 7).
- `HANDOFF.md` says to keep `branch` and `task` claims as rows. Confirmed; `area` (pool-wide on purpose) and `model_slot` stay too.
- The `milestone: tree` lines (17 a day, "unlanded commits N h old") are posted by a session's own command: no code in this tree
  writes them. They are counted as staying in the size estimate (section 5) until their writer is found and moved to local mail.

## 2. Reconciliation with the job log

The job log put every job in a second signed chain on the board so that a job of device B shows in `scripts/status` on device A.
That reason is the flood: jobs are the busiest, most local state there is. **A job is a row in a table its device owns and never
a row of the log.** Its data model (kinds, ops, idempotency, dead-worker rules, views) is what the owner asked for and it stands;
its storage and its replication go. No job replicates as a row, a summary row or a heartbeat. The pool-relevant effect of a job
already has a replicated form: the pool-wide claim it holds (a row), a landing review (a row), a task (a row), a push (git), and a
"landed" announcement. What device A shows about a job of device B comes from an ask (section 3), not from the log.

| design-job-log | Fate |
| --- | --- |
| 1 What this changes | the table is local and node-owned (`local/jobs.json`), not a fold of rows; the lease table's persistence moves, its callers move |
| 2 Survey | stands as the start; section 1 above re-verifies it |
| 3.1 `Index::apply`, foreign limits, rebuild, removed and silent origins | superseded: no foreign job rows. The pool-wide claim fold (`lease::merge::view`, earliest acquire wins) is unchanged |
| 3.2 rules 1, 3 (a device decides its own jobs), 4 (persist before effect), 5 (`node` reserved), 6 (free text is data), 7 | stand; 4 is a write to the local table, not a log append |
| 3.2 rule 2 (paths relative to home) | superseded: local paths stay absolute; an ask shows the last component only |
| 3.3 ops, 3.4 kinds, 3.5 idempotency, 3.6 states, 3.8 who may end | stand |
| 3.7 second chain, segments, base rows | the second chain is superseded. The base-row mechanism stands, applied to the one entry chain (S9, built on `feat/log-rotation`) |
| 4.1 claims (confined paths), 4.2 test queue, 4.4 worktrees, 4.5 CI and landing | stand, read from the local table |
| 4.3 status | changes: this device from its table, other devices by ask |
| 4.6 news (announcements derived from jobs) | superseded: announcements stay messages; `done` goes to the parent's local mail; "landed" stays an announcement; nobody asked for more |
| 4.7 quotas | stands (adds none; `fix/node-limits` is landed) |
| 5 What is deleted | stands for S4 to S7 and S10, with the changes in section 5 |
| 6 Dead workers | stands whole (all of it is local) |
| 7 The pool | superseded except "Whose rows count" (slice 0) |
| 8 Size bounds | open-job and note bounds stand; chain, segment and volume figures are superseded by section 5 |
| 9 Migration | replaced by section 4 |
| 10 Onboarding | stands: no human step |
| 11 Build plan, 12 Attack, 13 Decisions | replaced by sections 6, 7 and 8 |

Slices: 0 (pool id in the head, a new origin per board on found or join, a relayed origin needs an active member's key, `node`
reserved) **stands unchanged** and is S0 below. 1 (the job chain) is superseded; only its base mechanism stands, as S9. 2 and 3
(the log is the table; the job calls) become S4 without the chain. 4 and 5 become S5, 6 becomes S6, 7 and 8 become S7, 9 (news and
quotas) is superseded, 10 becomes S10. Slice S8, the ask, is new.

## 3. Local tables and the pool

**Reading local state.** The node owns every local table. Python tools never open its files; they call the node (as
`leasetable.py` does now), and each call answers from this device's table with no fold of other devices' rows:

- `scripts/status` prints its this-device section from one `job_list` call, with the queue, running and `work` jobs and the age
  of each;
- the test queue (`scripts/test`, `status`, `wait`, `cancel`) is the `test` jobs of that table in the order `lease/policy.rs`
  gives;
- `scripts/worktrees` adds to git's landed or work the open job on each tree and its last owner; `sweep` removes a landed tree
  with no open job;
- the CI watcher keeps its red streak and stuck notes as the ended and open `ci` jobs of the branch on this device, and reads
  GitHub for the verdict.

A device that runs an agent has a node, and a bare checkout starts it through the supervisor (design-job-log section 10). The local
files are written with mode 0600 by the node; a process of the same OS user can rewrite them, as it can `tokens.json` today, and none
is a security boundary. A lost `local/jobs.json` ends every job and double-books `cpu_slots` until the next tick reconciles; lost
`local/mail` loses the mail between local sessions and the notices of expiry, and nothing else.

### 3.1 Pool-wide claims

Exclusion across devices rests on four keys, `claim:branch`, `claim:area`, `claim:task` and `model_slot`, of which only branch and
area exclude (the other two give a liveness read). The fold is `lease::merge::view`: the earliest acquire in board order holds a
resource until a row of the same sender ends it. Checked in `board.rs`, `fold.rs`, `lease/merge.rs` and `rules.rs`: `Node::view`
does **not** drop the rows of a removed origin (`close_removed` cuts a removed device's log at a point and the rows up to it stay
in `merged()`), nothing in the fold reads `damaged`, and `view` folds the whole board on every call (`foreign()` calls it on every
lease tick). So each of the rules below is new.

1. **End rows are never dropped.** `mirror` writes an acquire as an author row and every end row (`release`, `expire`, `dead`,
   `abandon`) as a node-class row (`append_node`) that is never skipped. The node stretch (`RESERVED_ROWS`, `RESERVED_BYTES`,
   `Class::Node`) stays; its rows after the split are these end rows, the identity of a main session and the authority audits, all
   bounded by rule 4. An acquire the log refuses leaves the table lease marked `unpublished`, and the tick retries it; an end row
   that cannot be written keeps the lease in the table as `ending` until it is written. `status` lists both.
2. **The table and the log are reconciled both ways.** Every row `mirror` writes carries `idem = lease:<id>:<action>`, so a retry
   is the same row. At start and on each tick, for every held pool-wide lease in the table with no own-origin acquire row, the
   acquire is written; for every unreleased own-origin acquire row whose lease is not in the table (a crash, a table deleted or set
   aside at S4) a `dead` row is written. A second tick writes nothing. The tick reads an incremental index of pool-wide claims,
   built by one fold at board open and updated on each accepted row, not a fold per tick.
3. **A foreign hold has a life.** The acquire row carries `ttl_s` (at most `max_ttl_s`, 86,400) and a holder keeps a claim by a
   `renew` row at half its ttl. A foreign hold counts only while the reader's clock is within `ttl_s` of the `at` of the lease's
   newest acquire or renew row; a backdated `at` only shortens the writer's own hold, and a future one is already held back at five
   minutes. Two devices may disagree for a few seconds at the edge: claims serialize cooperating tools and are not a gate. The index
   leaves out every origin whose writer is removed or whose origin is in `damaged`, and is rebuilt when either changes.
4. **A foreign origin holds at most 512 pool-wide claims** (`max_leases_per_holder`); an acquire beyond it is stored, not folded,
   and recorded in `rejected`. `rejected` in `state.json` keeps the newest 1,000 ids and a count per origin.
5. **A task counts a lease as held** only when its holder is the task's worker and its origin is the origin that wrote the
   assignment (`tasks/fold.rs:344`), so a foreign row naming a dead worker's lease id keeps nothing alive.
6. **Rows are matched exactly.** A `lease` row is accepted only if every one of its (up to eight) resource lines has the kind
   `claim:branch:`, `claim:area:`, `claim:task:` or `model_slot:` exactly (not a prefix such as `claim:branchx`); one line outside
   rejects the whole row, so a row cannot mix a pool-wide and a device-only line.
7. **A race is re-checked.** Two devices that claim one branch before they sync both start; the fold resolves by order, and a job
   that holds a branch or area claim re-checks `foreign_holds` each tick and ends with one plain sentence when it lost. Across
   devices `view` compares exact keys; if the grain is ever coarsened to a directory, `view` compares by prefix, with a test.

### 3.2 The creators ledger and the events file

- **Creators ledger** (`local/creators.json`). An entry (board, key, session) is fsynced before the grant reply. An entry is removed
  only when its branch has landed (the `land` job of S7 tells the node) or it is more than a year old; the 100,000-entry cap
  refuses a new claim with one plain sentence before it evicts anything. A ledger that is unreadable, truncated, missing while
  `local/creators.marker` (written with the first entry) exists, or missing an entry for an own `claim:branch` acquire row still in
  the log is damaged: `land_accepts` returns no reviews and says why, the file is set aside as `creators.json.damaged` and a new
  one starts. The cost is that branches in flight on that device take their review from a session of another device or are
  created again. `review` checks the branch key; `accepts` also takes a worktree string from the lander, so a creator that never
  claimed the branch is excluded only when the lander passes the right path: accepted, as before.
- **Events file** (`local/events.jsonl`). Each line carries its class and the hash of the line before it; each class keeps its newest
  2,000 lines, so one class cannot evict another. Asks are not events: the node keeps a counter per asking device. Only retire of a
  subagent, `link`, `unlink`, `rebind`, `file.delete`, `agent.effort` and `announce_over_quota` are written here. The chain shows a
  partial edit or a truncation; a process of the same OS user can rewrite the whole file, which is why authority audits stay log rows.

**Reading another device's local state: the ask.** A new member op `ask` (beside `members`, `acts` and `pull` among the node's
network ops, served in `answer`) and a local method `device_ask {device, topic}`. It rides the connection the pool already has
(TLS 1.3 pinned to the member's certificate, or the `attach` channel of a device nothing can dial), the way `shard_call` does.
Nothing is stored as a row, and the answer is not an input to any decision.

| Topic | Returns | Answering device requires, for this asker |
| --- | --- | --- |
| `summary` | counts: running and queued jobs by kind, gpu busy or free, free cpu slots, build, age of the oldest running job | level `summary` (the default for every asker) |
| `jobs` | open jobs of the board and the last 20 ended: kind, state, session name, age, estimate, branch, last component of the tree, one step code (at most 256 rows, then "and N more") | level `detail` |
| `trees` | worktrees of the board's project: last component, branch, owner session, state | level `detail` |

**Authorization is the answering device's.** The asking side needs only the token of a session of the board and spends no grant:
the answering device decides what it gives, which the asker cannot argue with. The answering side checks, on every request:

1. the asker is an active member (`Standing::Active`, rechecked each request, so a removed device is refused at its next ask);
2. the board is one both devices hold. In one pool every member holds the project board, so this stops only a member that shares
   another board; the level and its scope in 3 carry the defence against a member that scrapes;
3. the level for this asker and topic: `off`, `summary` or `detail`. The default is `summary` for every asker. `detail` is the grant
   `expose_state`, scoped like `land_trust` (`Scope::on`) to the answering device and the asker's certificate fingerprint, signed
   by a person once like `shard_consent`; the level is read from the grant fold, so a revoke applies at the next ask. A pool
   whose charter names one person offers the grant for a device it admits on the admit card itself (one tap, no further card);
   other devices of one person are batched into one card. `detail` for every member, including devices added later, is a separate
   grant, `expose_state_all`, whose card says so in one sentence. The reason for the friction is one line: it lets another device
   see which sessions work on what;
4. the topic is on the list above, and the request carries only `topic`, `board`, a random `nonce` and the asking node's `by`
   (the session name the asking node stamped from its token; a caller-supplied `by` is refused, as in `shard_call`);
5. in flight: at most 4 asks from one device and 32 in all, and a token bucket of one ask a second per device with a burst of 4;
   beyond either the answer is "busy", which the asker shows as no answer. The body of a topic and level is built at most once a
   second into a cache, outside the node's lock; the signature is made outside the lock. The accept loop holds at most 256 live
   connections and refuses the rest.

**The answer** is `{device, asker, board, topic, nonce, counter, body}` plus an Ed25519 signature of the device key over
`poolhouse/ask-answer/v2`, a line break and the canonical JSON of those fields. `asker` is the fingerprint of the certificate the
asker presented on the connection, never the session name `by`. `counter` is the answering device's own counter, persisted and
rising on every answer; the asker keeps the highest it saw per device and drops an answer at or below it. The asker accepts an
answer only over the connection it opened to that device, with a nonce it is waiting for (at most 8 pending per session and 64 in
all), a valid signature under the certificate it pins, and a body of at most 64 KiB. Bodies carry ages in seconds computed by
the answering device, never wall times.

**Every string in a body is checked against a closed grammar** by the asker, and one violation drops the whole answer as "answer
refused": a session name by `valid_name`, a branch by `plain_branch`, a tree by `[A-Za-z0-9._-]{1,64}`, a kind and a state from the
fixed lists of job kinds and states, a step from a fixed list of step codes (the text is the asker's own, never the answerer's),
and `build` by a commit-hash or version pattern. Free text is not cleaned and shown: there is none. Never in an answer: tokens, full
paths, titles, outcomes, environment, anything outside the table above.

**What a device does with one.** `scripts/status` asks every other active member in parallel with a 3 second limit. A device that
does not answer shows "device B: no answer (last heard N min ago)"; an older build shows "older build, nothing to show"; there is
no fallback to rows because there are none. An answer is shown as "device B says" and is checked against what the log proves: if
the log holds pool-wide claims for B's sessions and B reports none, the line says "differs from its signed claims". Nothing
decides on an answer, because a false "idle, gpu free" would attract work and a false "busy" would hide a device: no dispatch,
claim, lease or hook acts on one, and the lead's attention hook reads only counts that it shows as "device B says". The answering
device counts asks per asking device (a counter, not an event) and its `status` shows the count.

An ask never writes to another device, never fetches a file and never starts work (leases on a remote device stay unbuilt, `node.md`,
"What is left"). No human step is added to any journey: the default is `summary`, and `detail` is one tap on the card of
an admit or a batch (AGENTS.md, "Security without friction").

## 4. Migration

None. Before 1.0 the old state is refused with one plain message and the device starts fresh (AGENTS.md, "Nothing is cemented
until 1.0"):

- Row `v` becomes 2 in S0. A node that finds a board whose log holds version 1 rows moves the board directory to
  `boards-old-format/<board>`, prints "This board was made by an older version of Poolhouse and cannot be used; it starts empty",
  and creates the board empty at the next registration. The precedent is `pool.json.old-format`. A second start finds nothing to
  move. Sessions register again on their next hook; the owner's board resets once, in S0, and nowhere else in this design.
- S4 sets `leases.json` aside the same way (`leases.json.old-format`, one line). Held leases at that moment end with their
  processes; nothing carries over.
- A version 1 device is refused at sync (a row of another version is refused today). No mixed-build shim.
- Leftover Python files (`~/.cache/dev-test-slots`, `ci-watch/`, `worktree-lifecycle.json`, `broker-leases.json`) are not read and
  not deleted by code; they hold records of runs that ended.
- One rule loses history in the reset: the creators ledger of S1 starts empty (with no marker, so it is not damaged), so a session
  that created a branch before S0 is no longer excluded from reviewing it. Branches in flight are landed or abandoned before S0 and the cutover is announced.
- The only cost is to the owner's devices, each of which is re-added by `python -m poolhouse.device_setup --yes`, run by an agent.

## 5. What is deleted, and the size of the log

**Rust (`app/poolhouse-node/src`).**

- S1: `lease::merge::ever_held` (replaced by the creators ledger); the `lease_takeover` audit in `mirror`; every `lease` line but
  `claim:branch`, `claim:area`, `claim:task` and `model_slot` from `fold::lease` (a foreign row with another key is rejected,
  not folded); the fold per tick in `lease/rpc.rs::foreign` (replaced by the incremental index of section 3.1).
- S2: the `land.review` audit copy (`landing.rs`, `audit()` in `review`), the `RETIRED` queue names in `landing.rs` (S0 set the old
  boards aside, so no row of them remains), the `board.*` audit (`rooms.rs:202`), `enforcement.set` (`tasks/calls.rs:483`), the `retire` audit
  except for cause (`sessions.rs:178`), `rebind` (`sessions.rs:99`), `link`/`unlink` (`api.rs:168`), `file.delete`; `events.rs`
  writes `local/events.jsonl`. `land.trust`, `land.untrust`, `quarantine.release` and `authority.set_denied` stay log rows. All
  of `logbounds.rs` stays, `RESERVED_ROWS`, `RESERVED_BYTES` and `Class::Node` included: the node stretch holds the end rows of
  pool-wide claims (section 3.1).
- S3: the log write in `notice.rs`; the identity row in `node.rs:440,449` for a session with a parent.
- S4: `claims.rs`; the file persistence of `lease/mod.rs` (`leases.json`, `Leases::save`); `lease_*` and `claim`, `reserve`,
  `release`, `claims` replaced by `job_*` (design-job-log section 5, Rust paragraph, with `Kind::Lease` and the `lease` arm of
  `fold::entry` kept and narrowed, and `#leases` kept in `FIXED_CHANNELS`).
- Kinds and types: no `Kind` is removed. `lease` is narrowed to four keys, `audit` to pool events and sessions ended for cause,
  `identity` to sessions with no parent, `landing` to `review`; a `status` message to `#general` is refused; the announcement
  types `joined` and `done` are written by no tool (they stay in the type list for rows other devices still hold, until S10).

**Python and tests.** Reduced or deleted, from design-job-log section 5 and re-checked: `scripts/testslots.py`,
`testslots_policy.py`, `testqueue.py`, `queue_doctor.py`, `queue_notice.py`, `src/poolhouse/workspace/slots.py`,
`src/poolhouse/testing/cores.py`, the file locks of `testslots_pytest.py` and `testslots_runner.py`, the record files of
`testjobs.py`, `take_cores` and `give_back_cores` of `serve/broker_holds.py` and the op `cores` of `broker_wire.py` (S5);
`workspace/worktree_lifecycle.py`, `task_worktree_recovery.py`, the record half of `worktree_archive.py`, the `stale_lock` field of
`trees.py` (S6); the state of
`ci_watch.py`, the locks of `land_steps.py` and `primary_sync.py` (S7). Kept: `announce` and its posters (those named in table C
change recipient), `landings.jsonl`, `servers.json`, `broker.json`, `lease-history.ladybug`, `testreap.py`. The `news` view is not
built. Rust tests ported or rewritten: `tests/claims.rs`, `claims_foreign.rs`, `lease.rs`, `lease_parties.rs`, `landing.rs`,
`rooms.rs`, `sessions.rs`, `mail.rs`, `events.rs`, `log_bounds.rs`, `tasks.rs`, `limits.rs`, `writes.rs`, `idle.rs`, `reap.rs`,
`node_attacks.rs`; Python tests as design-job-log section 5 lists them. A port keeps its assertions (`weakened-assertions`).

**Docs.** `node.md` ("On the board" of Leases, "Direct messages", kinds, Limits, `audit_event`, the grants registry for
`expose_state`), `limits.md` (the node stretch now holds the end rows of claims), `board-writes.md` (the "What remains" section), `workspace.md`,
`test-execution.md`, `worktrees.md`, `landing.md`, `board-replication.md`; design-job-log gets a header naming this document (S0 edits it); `glossary.md` gains `job`: a unit of work
a session does on one device, held in that device's table and never a row of the log (S4); `HANDOFF.md` loses the item "Board
rows only the Rust node can cut" when S1 to S3 land and rewrites "A full log does not recover" as S9's trigger.

**Size of the log after the split** (rows a day from the 7.49-day tally of all rows, `board_tally.py`; the last 1.3 days only in the
total):

| Writer | Was, per day | After |
| --- | --- | --- |
| test runner (`test-run`, `test-result`, `test-job`) | 3,572 | 0 (cut in code; now refused by the node) |
| landing queue and its audit copies | 1,994 | 0 (retired) |
| device-only claims: install 1,083, worktree 128, server 12, port 7 | 1,228 | 0 |
| branch and area claims | 416 (area 251) | 416 |
| identity: main 12, subagents 130 | 142 | 12 |
| audit: retire 59, `announce_over_quota` 8 | 67 | a few (authority audits and sessions ended for cause) |
| landing reviews and their audit copy | 86 | 43 |
| announcements: landed, free text, blocked, tree notices 17 (the rest: joined 68, runtime and stopped 28, retired lander 12 are cut; done 38 moves to local mail) | 179 | 33 |
| **Total** | **7,683** | **about 500** (about 270 in the last 1.3 days) |

The log would hold 3,780 of the sample's 57,581 rows, a 93.4 percent reduction, and 82 percent of what remains is claims. Sixty
percent of the claim rows are area claims (1,880 of 3,119); 783 of the 941 area acquires are by sessions named `chatgpt`, whose
harness reserves one claim per file (`board-writes.md`, row 6; section 8). At 500 a day the 200,000-row cap is 400 days away (740
days at 270). Today the log holds 57.6k rows and has 18 days left at the measured rate; at the 600 a day that `board-writes.md`
expects after its cuts it has 238. Against that estimate the gain is smaller in rows and larger in kind: no worktree on older
code can write device state into the log any more. The tally cannot see direct messages; about 96 a day more leave the log in S3.

## 6. Build plan

Each slice lands alone, written fail-first, with the attack test of section 7 for what it touches, an affected-selector run on
this device and on the WSL device for process behaviour, and a fixed-cost test. A slice that moves a consumer deletes the old
consumer in the same commit. Every new call is idempotent and its test calls it twice (the set-aside, a creators insert, a mail
post with one `idem`, an ask).

- **S0 Footing.** design-job-log slice 0 as written, plus row `v` 2 and the set-aside of section 4, plus the header on
  design-job-log naming this document. Tests: slice 0's; a version 1 board is moved with one line and a second start does nothing;
  a version 1 row from a member is refused. Cost: the version check reads one row per board at start, under 1 ms a board.
- **S1 Claims split.** `mirror` writes a row only for the four keys of table A, listing only those resources of the lease, with
  `idem = lease:<id>:<action>`, acquire as an author row and every end row as a node-class row that is never skipped; the rest of
  section 3.1 (reconcile both ways, foreign hold life and `renew`, per-origin cap of 512, bounded `rejected`, task held rule,
  exact-kind match, removed and damaged origins left out, incremental index); the creators ledger of section 3.2 replaces
  `ever_held`; `lease_takeover` audit gone; install claims of a switch become local; `status` shows rows a day and days to the cap.
  Tests (each written fail-first): `end_rows_are_written_when_the_log_is_full` (H1), `a_refused_acquire_is_retried_as_unpublished`
  (H1), `reconcile_writes_the_missing_row_and_ends_the_orphan_once` (kill after the table write; delete the table; a second tick
  writes nothing; H3), `a_removed_origins_claims_stop_holding` and `a_damaged_origins_claims_stop_holding` (H4b),
  `a_foreign_hold_lapses_without_renewal` (H4b), `a_foreign_origin_holds_at_most_512_claims` (H4c),
  `rejected_keeps_the_newest_thousand`, `a_foreign_row_cannot_keep_a_task_alive` (H4a), `a_mixed_row_is_rejected_whole` and
  `a_prefix_kind_is_rejected`, `the_tick_does_not_fold_the_board` (cost: under 5 ms on a 200,000-row log),
  `a_creator_stays_excluded_after_a_crash_between_grant_and_write` (fsync first; H7c), `a_busy_creator_never_evicts_an_unlanded_entry`
  and `a_full_ledger_refuses_the_new_claim` (H7a), `a_damaged_ledger_yields_no_reviews` (H7b), `a_job_that_lost_a_branch_race_ends`;
  100 worktree, port, server and install claims write 0 rows and sign no head; a branch claim writes one acquire row and one end row;
  `lease_parties.rs` and `landing.rs` pass. Cost: a creators lookup is O(1) and the file loads in under 50 ms at the maximum.
- **S2 One row per fact.** The audit copies of section 5 go; `events.rs` and the device-only events write `local/events.jsonl`
  (a hash chain, newest 2,000 lines per class, written with the append rule of the log: one write and `fsync`); authority audits
  stay log rows; identity rows only for sessions with no parent (register, model, retire); a `status` message to `#general` is
  refused with a one-sentence `say`; `RESERVED_*` and `Class::Node` stay. Tests: each listed call writes the one row of its fact
  or none; registering and retiring a subagent writes no row; a `status` post to `#general` is refused; the source scan fails on an
  unlisted `append`, `append_node`, `append_person`, `append_many` or `board.seal` caller; `land_trust_audit_stays_a_log_row`,
  `an_ask_loop_cannot_evict_an_audit_event`, `an_edited_events_line_breaks_the_chain` (H5); a v2 row of a kind outside table A is
  stored and not folded. Cost: an event write is one append; `log_bounds.rs` still proves the last stretch signs a head.
- **S3 Local mail.** A message whose sender and recipient are sessions of this device (the node included) is written to
  `local/mail/<board>.jsonl` and never to the log; a message to a session known only by another device's identity row, and every
  message to `#general`, `#announcements` or a room, goes to the log as now. `read`, `inbox`, `thread`, `with` and `outbox` merge both
  sources by clock; ids are `board:local:N`; `subs` cursors gain a `local` position; the node's notices and the subagent `done`
  post go here, and the `agent` subscription of `subs.rs` (a parent's view of its subagent) reads the `done` from local mail; the
  unread bounds (`inbox_pending`, `unread_per_sender`) cover local mail. Tests: a message between two local sessions writes 0 log rows and is readable by those two only (not by a third session, not through a link); one to a session of
  another device is one row; a reply thread across both sources reads in order; a retry with the same `idem` is one message.
  Cost: an inbox read merges at most 500 pending messages in under 5 ms; the file keeps the newest 20,000 messages a board.
- **S4 The local job table.** design-job-log slices 2 and 3 without the chain: `local/jobs.json` (open jobs) and a bounded ended
  history, written atomically on change; ops, kinds, idempotency, dead-worker rules, confined path claims and who may end as in
  that document; callers move (`leasetable.py`, `api/leases.py`, `api/pool.py`, `gate.py`, `board/session.py`,
  `harness_claims.py`, `runtime_deploy.owning`, the serve broker); a job's branch or area claim writes its S1 row. Tests: port
  `lease.rs`, `lease_parties.rs`, `claims.rs`, `claims_foreign.rs`, `test_leasetable.py`, `test_board_claims.py`,
  `test_workspace_claim_output.py`; new: a stranger cannot end a job, an ancestor can, `once` never returns another actor's job,
  a claim of `/` or of a planted `~/.git` home is refused, a sleep gap ends no job, a restart folds to the same table. Cost: start
  fold of 1,024 open and 1,024 ended jobs per kind under 50 ms; a job call under 5 ms with 4,096 leases.
- **S5 Test permits and test jobs.** design-job-log slices 4 and 5, retargeted. Tests as there; plus a gate run of 920 files
  writes 0 log rows. Cost: reaching the node from `scripts/test` as in design-job-log slice 4.
- **S6 Worktrees.** design-job-log slice 6.
- **S7 CI watcher and landing.** design-job-log slices 7 (without its status part) and 8.
- **S8 The ask.** Section 3: the `ask` op, `device_ask`, the grants `expose_state` (per asker) and `expose_state_all`, the other-devices
  section of `scripts/status`, the body cache and rate, the connection cap. Tests: see section 7; a 1,024-job table answers
  `jobs` in under 20 ms and the frame is at most 64 KiB; a device that does not answer costs `scripts/status` at most 3 seconds
  in all, asked in parallel.
- **S9 Entry-log segments.** design-job-log slice 1's `base` row on the one chain, already built on the branch
  `feat/log-rotation` (worktree `poolhouse-log-rotation`, not landed): a segment starts at a base row at 75 percent
  of the cap, restates notes, rooms, tasks and grants, archives the rest and serves a member behind the base from the archive. This
  design changes three things about it. Its trigger and priority: the log it rotates fills in 400 days, not 34, so it lands when its
  own review is done and `scripts/status` shows the cap under 90 days away at the last 7 days' rate (the line is added in S1); it
  blocks nothing. Its carry set: the branch treats `lease`, `verify`, `identity` and `landing` rows as history, which is right for
  device state and wrong for what remains replicated, so it must also restate this origin's held pool-wide claims, the
  verifications of notes it restates (a note loses `test-verified` without them), the latest identity of each main session and the
  reviews of commits not yet landed; `rotate.rs:216` rejects a restated kind other than Grant, Note, Room, Task and is widened to
  these. A restated claim keeps its place in the race: its body names the original (`of`: origin, seq, hlc, hash) and the claims
  it outranked (`beats`, at most 8), the `carry` of design-signed-ordering 5.2 item 3, and `view` ranks a carried claim by `of`
  and never by the restating row; until the ranking of that design lands the order is the original hlc. A base is written only
  when the carried claims are settled on every active member. Test `a_carried_claim_still_beats_its_loser` (A holds, B tries and
  loses, rotate, B still loses; a loser that never ends still loses; H2). If one fold cannot restate itself, that kind is the
  reason to replace the board (HANDOFF, "A full log does not recover") instead.
- **S10 Cleanup.** `jobs.py` writes `detach` jobs; the remaining files and docs of section 5; budgets lowered with
  `scripts/budgets --update`, said by how much.

Order: S0, then S1, S2, S3 (they relieve the log and need no new storage), then S4; S5, S6, S7 in any order after S4; S8 after
S4; S10 last; S9 on its trigger. S2 lands after the worktrees that still write `status` posts have merged `cb0fa19c9`, checked
with `git branch --contains`; a refused post in a tool on older code only warns (`testboard.guarded`), so no gate run fails.

**What S2 and S3 do to `feat/log-rotation`** (checked against the branch's tests): because `RESERVED_*`, `Class::Node` and
`append_node` stay, no `Bounds { reserved_rows, .. }` literal (`tests/kit/rotation.rs:17`, `tests/log_rotation_node.rs:20,54`,
`tests/log_bounds.rs`) and no `append_node` call fails to compile, and the tests use only the kinds Note, Grant, Room, Message,
Head and Base, all in table A. What does conflict is text: S3 edits `node.rs:440,449`, the branch edits `node.rs` at its
`Board::open_rotating` call and `append_node` identity line, and S2 edits the retire audit at `sessions.rs:178` where the branch
holds `append_node`. The order that avoids a broken tree: S2 and S3 land first, then the branch merges `0.3dev`, resolves those
hunks and runs `log_rotation.rs`, `log_rotation_review.rs`, `log_rotation_node.rs` and `log_bounds.rs` unchanged, then S9 widens
the carry set. The branch is not built on S2 or S3, and neither waits for it.

## 7. Attack first

Each case says the attack and what stops it; the table after the cases names the test of each fix.

- **Copied or cloned device or pool.** A cloned state directory of a device in the `keystore` custody carries no key. A clone
  with a copied key is a second device with one identity: two copies append to one origin and fork the log, members mark it damaged
  (existing rule), and its claim rows stay the holder's and persist until reconciled (section 3.1, rule 2); two copies answer asks with different answers, shown as one device whose lines disagree, which is display only.
  Copied local tables end every job whose process is not alive at the first sweep (design-job-log section 6). A pool copied to new
  devices is a new pool (slice 0 starts a new origin).
- **Replay.** A captured ask is a read inside TLS 1.3; a member that resends one costs an in-flight slot and a token. An answer
  replayed by a third device is refused: it arrives on a connection to another certificate, or its signature names the original
  asker's certificate fingerprint and a nonce the asker is no longer waiting for; a stored answer shown later is dropped by its
  counter. A replayed log row is the same row (id, hash, `idem`). A row copied to another board is
  refused by its board id in the hash; one from another pool by the pool id in the head (slice 0).
- **Forged or below-threshold signature.** An answer with a bad signature, a key other than the pinned certificate's, or a wrong
  asker, nonce or board is dropped and shown as "answer refused". No answer is evidence of anything and no decision reads one.
  Rows are stored only through a head under the pinned device key. `expose_state` needs the same person signature or phone
  approval as any grant.
- **A lying device in an ask.** It can say anything about itself and nothing about another device. The lines it shows are marked
  "device B says" and checked against its signed claims in the log; the signature makes the lie attributable, not true. Accepted
  risk: `scripts/status` is read by agents and a false "idle, gpu free" attracts work while a false "busy" hides a device; nothing
  decides on an answer: not a claim, a lease, a landing, a push, an admission, a dispatch or a test run. Size, rows, field set and
  the grammar of every string are fixed; an answer outside them is dropped whole.
- **A malicious member reading local state it should not.** The answering device decides: member, shared board, topic list, and
  the level for that asker's certificate fingerprint. Members of one pool all hold the project board, so the board check stops
  only a cross-board ask; the per-asker level is the defence. Every asker gets `summary` (counts) until a person signs
  `expose_state` for it. A member that does not hold the board gets
  nothing about it. An answer never carries a token, a full path, a title, an outcome or a string outside the closed grammar.
  Before this design the same member received every direct message between two local sessions and every absolute worktree path, as
  rows; after S1 to S3 it does not.
- **Compromised or malicious member.** It can write false rows for its own device only and fill its own log up to the per-origin
  bounds (rows, bytes, 64 origins), which removal cuts. It can win pool-wide claim races by writing rows dated long ago; unchanged
  and accepted: claims serialize cooperating tools and are not a gate (git's push check and the `main` approval are). It cannot
  squat: a foreign hold lapses without renewal, a removed or damaged origin's holds are ignored, at most 512 are held, and a row
  of fresh keys beyond that is stored and not folded, with `rejected` bounded (section 3.1). A row of a
  kind or key outside table A is rejected by every other device, recorded and shown, never folded.
- **Stolen device or key.** The thief signs rows as that device and answers or asks as it until the device is removed, which applies
  at its next request and ends the holds of its claims. What it holds is less than before: the log no longer carries local mail, device paths or job history, and an
  ask returns counts unless `detail` is on. A stolen phone can approve an `expose_state` grant like any other grant, under the same fatigue limits,
  and the grant names one asker; `expose_state_all` is the louder grant it could also sign, a known cost of a stolen phone.
- **Malicious agent.** It has no call that writes local state to the log, and `status` posts to `#general` are refused. It cannot
  read another device's detail unless that device allows it for its device, and where `detail` is on it sees what the owner's other agents there
  are doing. It holds a server thread at most 3 seconds per ask under the waiter counts, a rate and a body cache; a local agent that opens
  pending nonces to a silent device is held to 8 a session and sees "no answer" after 3 seconds (accepted). It can fill only its own mail
  and its own device's tables, under the open-job bounds.
- **Compromised agent that does not know.** A string in an answer outside the closed grammar drops the whole answer, so no
  instruction reaches a reader; a mail body is screened, fenced and read by no hook; the lead's attention hook reads only counts. An injected "end job X" can end the caller's
  own jobs only. `scripts/worktrees sweep` decides from git and open local jobs, so a false answer cannot cause a deletion.
- **Downgrade.** An older node refuses `ask` ("unknown op") and shows as "older build"; it never falls back to reading rows. An older
  node that writes device state into the log is refused at sync by `v` 2, and a v2 row of a kind or key outside table A is stored
  (the chain needs it) and not folded. `detail` cannot
  be forced on by a member, and a pool's join policy never changes in place. No flag or variable turns the boundary off.
- **Approval fatigue.** One new grant per asker, on the admit card or one batched card for one person's devices, through the
  existing limits; the default needs no approval. No ask prompts anyone.
- **Takeover by whoever is present.** A process with no token asks nothing. A token from another terminal may ask (read only) and
  spends no grant, at `summary`. A device on the network in an open pool becomes a member and sees `summary` counts: it learns that a device is
  busy, which `capacity` already tells it.

### Fixes and their tests

Each is written fail-first (S1, S2, S8 above name the rest).

| Hole | Fix | Test |
| --- | --- | --- |
| End rows dropped on a full log (H1) | node-class end rows, never skipped; `unpublished` and `ending` | `end_rows_are_written_when_the_log_is_full` |
| A restated claim reorders the race (H2) | `carry` with `of` and `beats`; `view` ranks by `of` | `a_carried_claim_still_beats_its_loser` |
| Table and log disagree after a crash (H3) | `idem` per (lease, action), reconcile both ways each tick | `reconcile_writes_the_missing_row_and_ends_the_orphan_once` |
| A foreign hold blocks or keeps a task alive (H4) | life by `ttl_s` and `renew`; removed and damaged origins left out; cap 512; task held rule | `a_foreign_hold_lapses_without_renewal`, `a_removed_origins_claims_stop_holding`, `a_foreign_origin_holds_at_most_512_claims`, `a_foreign_row_cannot_keep_a_task_alive` |
| Security audits in an evictable file (H5) | authority audits stay log rows; chain; per-class retention; asks are counters | `land_trust_audit_stays_a_log_row`, `an_ask_loop_cannot_evict_an_audit_event` |
| `detail` shown to every member (H6) | `expose_state` per asker fingerprint; `expose_state_all` separate | `asker_of_another_person_gets_summary_while_the_owner_gets_detail` |
| Creators ledger evicts and fails open (H7) | no unlanded eviction, refuse before evict, fsync first, damaged fails closed | `a_busy_creator_never_evicts_an_unlanded_entry`, `a_damaged_ledger_yields_no_reviews` |
| Rotation tests and S2, S3 (H8) | node stretch kept; merge order of section 6 | the branch's four test files pass unchanged after the merge |
| Replay of an answer | `asker` is the certificate fingerprint; `counter` | `a_stored_answer_is_dropped_by_its_counter`, `an_answer_naming_another_asker_is_refused` |
| Injection through an answer | closed grammar per string, whole answer dropped | `an_answer_with_a_free_text_step_is_dropped` |
| Ask amplification | body cache once a second, signed outside the lock, token bucket, connection cap | `back_to_back_asks_build_one_body`, `an_ask_past_the_rate_is_busy` |

## 8. Open questions

Only where two options are both correct; each carries the default this design uses.

1. **Area claims at file granularity.** Keep them (decided here): two devices editing one file see each other's claim, and the
   cost is about 250 rows a day while a generic harness runs. The other option is a coarser claim, a directory or a reserve of
   several files as one lease with one acquire and one end row, which cuts the row count and loses file-level warning. Decide after
   S1 lands and the area rate is read again; this design does not change the grain.
2. **The default level.** Decided here: `summary` for every asker until a person signs `expose_state` for it, taken on the admit
   card where the charter names one person. The other option, `detail` by default in such a pool, spends no tap and shows
   every future member of it.
