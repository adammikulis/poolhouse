# Attack pass on docs/design-state-split.md

Non-author pass (claude-a4df03) against `origin/0.3dev` (2c19adcda), read from `fold.rs`, `lease/merge.rs`, `lease/rpc.rs`
(`mirror`, `foreign`), `lease/types.rs`, `board.rs` (`write`, `append_many`, `stage`), `logbounds.rs`, `landing.rs`,
`tasks/fold.rs:344`, `peer.rs` (`answer`), `net.rs`, and the diff of `feat/log-rotation` (bceddf929 base) against `0.3dev`.
Decisions taken as given: level defaults to `summary` everywhere until `expose_state` raises it; area-claim grain is decided
after S1. Verdicts: **HOLE** (design must change), **GAP** (design silent, needs a line), **OK** (holds), **ACCEPT** (named risk).
Not verified: whether `Node::view` drops rows of removed origins (a tool refusal stopped that read); H8 depends on it.

## Holes, highest first

**H1. Claim end rows are dropped when the log is full or the session is at its share; S2 deletes the reserve that would save them.**
`mirror` calls `append_many`, which stages every row as `Class::Author` and silently skips a `Quota` refusal (`board.rs:263-266`).
A pool-wide claim whose `release`, `expire` or `dead` row is refused stays held on every other device forever: `merge::view`
removes a holder only on a row from the same sender, and nothing else ends it. Today `Class::Node` (rows to `MAX - 500`) exists
but lease rows do not use it. S2 removes `RESERVED_*` and `Class::Node` and says node rows "use the last stretch", which is 500
rows shared with heads and person decisions; end rows are not named there.
Fix: keep a small node stretch; write acquire as `Author`, every end row (`release`, `expire`, `dead`, `abandon`) as node class,
never skipped; a skipped acquire must leave the table lease marked "unpublished" so it is retried, not forgotten.

**H2. Restating held claims in a base row reorders the race and can give one branch or area to two devices (S9 carry set).**
`view` is "earliest acquire in board order wins" and ignores a later acquire; there is no queue. A restated acquire is a new row
with a new hlc. Device B's acquire, dated between the original and the restatement, now sorts before the restated holder row, so
A (never released) and B both fold as holder after the next sync, and a device that had learned it lost now wins.
Fix: the restated row carries the original `hlc` and lease id and `view` orders restated claims by it; or the base row lists held
claims with their original order and `view` seeds from the base. Test: A holds, B tries and loses, rotate, B must still lose.
`rotate.rs:216` also rejects any restated kind other than Grant, Note, Room, Task, so S9's carry set needs that check widened,
named in S9.

**H3. Crash window between the local table and the log: a granted claim with no row, or a row with no lease.**
S4 rule 4 persists to the table first, then `mirror` appends; `append_many` is called with an empty `idem`, so a retry writes a
second acquire. After a crash: the table holds the claim, nobody else sees it (exclusion lost for that holder), or the table was
lost or set aside (`leases.json.old-format` at S4, a deleted `local/jobs.json`) and the log holds acquire rows with no owner,
blocking every other device's `claim branch` and `claim area` for those keys for ever.
Fix: at start and on each tick, reconcile both ways with one idempotent key per (lease id, action): for every held pool-wide lease
in the table without an own-origin acquire row, write it; for every own-origin acquire row (unreleased, from `view`) whose lease is
not in the table, write `dead`. Test: kill after the table write; delete the table; both end with the right rows after one tick, and
a second tick writes nothing. This also covers the S4 cutover.

**H4. A foreign acquire can hold a task alive or block claims indefinitely.**
(a) `tasks/fold.rs:345` builds `held` from every exclusive entry's lease id, any scope, any origin. A member that writes
`claim:task:x` or `model_slot:...` with the lease id of a dead worker keeps `lease.active && held.contains(id)` true
(`calls.rs:164`, `show.rs:13`), so recovery never triggers. Only the holder can release; the holder is the attacker.
Fix: count a lease as held only when the holder is the task's worker and the origin is the origin that wrote the assignment.
(b) Foreign `claim:branch` and `claim:area` rows have no expiry in the fold. A member squats names; local requests wait behind
`foreign_holds`. The design accepts "winning races" but not blocking: S1 shrinks the log to claims, so claims become the whole
attack surface. Fix: a foreign hold counts only while the writing origin has a head newer than the row by a bound (a held claim is
renewed by the origin's own rows), and a removed or damaged origin's holds are ignored (check this against `Node::view`).
(c) 200,000 hostile rows of fresh keys grow `exclusive` without bound and `foreign()` folds the board on every lease tick; confirm
`view` is cached or incremental, add a per-origin cap on held pool-wide claims (for example 512), reject beyond it.

**H5. `local/events.jsonl` is an unsigned, unreplicated, evictable ring that now carries security audits.**
S2 moves `land.trust`, `quarantine.release`, `authority.set_denied`, `file.delete`, `rebind` from the signed, replicated log to a
file of the newest 10,000 events. (a) Every ask is also recorded there; an active member that loops `ask` pushes every real audit
event out of the ring in minutes. (b) Anyone who can write the file (a compromised agent, a stolen device) erases or edits the
record, where before a copy sat on every member and could not be rewritten. (c) These events are rare; the volume that motivated
the move is retire (59 a day), `link`/`unlink`, `announce_over_quota`.
Fix: keep authority-bearing audits (`land.trust`, `land.untrust`, `quarantine.release`, `authority.set_denied`) as log rows, they
are a few a day; move only retire, link, unlink, rebind, file.delete. Asks become per-device counters, not events. Make the file a
hash chain with per-class retention so one class cannot evict another.

**H6. The `expose_state` level is per answering device, not per asker.**
Raising to `detail` shows session names, branches, tree names and the open-job list to every active member, including a device of
another person in a multi-person pool, and to a device that joins an open pool later. The grant says "spent on this device only"
but nothing names who may see. Compare `land_trust`, which is scoped `Scope::on(fingerprint)`.
Fix: scope the grant to the asker's fingerprint (or to a set of the same person's devices); `detail` for "all members" is a
separate, louder grant a person signs knowing it. Add the test: asker M of another person gets `summary` while asker A gets `detail`.

**H7. Creators ledger: eviction and fail-open.**
(a) At 100,000 entries "oldest first" a session that churns worktree or branch claims pushes out a creator's entry and that
creator can review its own branch again (`review` and `accepts` exclude only creators). Cap by age and by landed branch, never evict
an entry of an unlanded branch; refuse a new claim before evicting. (b) A missing, unreadable or empty ledger is "no creators",
which is fail-open. A damaged ledger must fail closed: `land_accepts` returns no reviews and says why. (c) Order: the ledger must be
fsynced before the grant reply, or a crash between grant and write loses the creator. (d) `review` checks only the branch key;
`accepts` also takes a caller-supplied `worktree` string, so a creator that never claimed the branch is excluded only if the lander
passes the right path. Unchanged by the design, name it.

**H8. Rotation (S9) and S2 are not conflict-free with `feat/log-rotation`.**
The textual diff of `logbounds.rs` is clean (the branch adds constants after line 22 and `segment_at`/`carry` before `admit_copy`;
S2 edits lines 11-14, the enum and `admit`). The semantic conflicts are four compile errors and one rewrite:
`tests/kit/rotation.rs:17`, `tests/log_rotation_node.rs:20` and `:54` build `Bounds { reserved_rows, reserved_bytes, .. }`, which
S2 deletes; `node.rs:451` of the branch calls `append_node`, which goes with `Class::Node`, and S3 edits `node.rs:440,449` in the
same lines. `ROTATE_PERCENT`, `CARRY_PERCENT` and `carry()` are computed on the full `rows`/`bytes`, so shrinking the last stretch
moves nothing, fine. Fix: the S2 commit updates the branch's tests and `node.rs` after the branch lands, or the branch lands first
(it is not landed); say which, and list these files in S2.

## Cases from AGENTS.md section 1

| Case | Attack | Verdict | Note |
| --- | --- | --- | --- |
| Clone of a device | copied key answers asks and writes rows | OK | forks the log; answers differ, display only. A cloned `local/jobs.json` ends dead jobs at the sweep. Add: the clone's claim rows are the holder's and persist (H3). |
| Clone of a pool | copy to new devices | OK | new origin, slice 0. |
| Replay | resend an ask; replay an answer | GAP | The signature covers `device, asker, board, topic, nonce, body`. `asker` must be the asker's certificate fingerprint, not the session name `by`, which a hostile node chooses. Add a freshness field (answering device's monotonic counter) so a stored answer cannot be shown later as current. |
| Forged signature | bad answer signature | OK | dropped; no decision reads it. |
| Malicious member, state scrape | ask for jobs and trees | GAP | In one pool every member holds the project board, so check 2 (rows moved between devices) stops only cross-board asks, not the common attack. Level and per-asker scope carry the whole defence (H6). |
| Malicious member, flood of allowed rows | claim rows of fresh keys | HOLE | H4(b)(c). Rejected rows of a wrong kind are stored (the chain needs them) and `rejected` in `state.json` has no stated bound; bound it. |
| Lying device in an ask | false counts, false job list | ACCEPT | Display only, but `scripts/status` is read by agents and the lead's attention hook reads counts. A false "idle, gpu free" attracts work; a false "busy" hides a device. Say so; no dispatch decides on it. |
| Injection through an answer | a one-line instruction in session name, branch or step | GAP | Cleaning to one line leaves a readable instruction, and a hostile answerer chooses the text for every device that runs `status` (auto-asked, all members). The asker must validate each string against a closed grammar (`valid_name`, `plain_branch`, step from a fixed list), not clean free text; drop the whole answer otherwise. At `summary` there is no free text but `build`; give it a regex too. |
| Ask: amplification | 200-byte request, 64 KiB answer | GAP | `dispatch` runs under the one node mutex (`peer.rs:168-213`, `gov_tick` first). A member with 4 asks in flight, back to back, holds the lock for 4 x 20 ms jobs builds repeatedly; "a count, not a rate" is no bound on lock time. Fix: build the body at most once a second per topic and level into a cache, sign outside the lock, and add a per-device rate (token bucket) beside the in-flight count. Also `accept_loop` spawns a thread per connection with no cap (not new). |
| Ask: answer as decision input | an agent or hook acts on counts | ACCEPT | See "lying device". The `sweep` claim holds (decides from git and local jobs). |
| Ask: local DoS | a local agent opens 64 pending nonces to a silent device | ACCEPT | `status` shows "no answer" for 3 s; low. Count pending per session. |
| Compromised member (races) | write claims dated long ago | ACCEPT | Already named; H4(b) is the part that is not a race. |
| Stolen key | thief signs rows and answers | OK | Removal applies at next request. Check that removed origins' held claims stop counting (not verified, H4(b)). |
| Stolen phone | approves `expose_state` | GAP | Under H6 a stolen phone approves "all members see detail". Per-asker scope limits it. |
| Malicious agent | no call writes local state to the log | OK | The test that lists every `append` call site is the right mechanism; extend it to `append_node`, `append_person`, `append_many`, `board.seal` callers. |
| Unknowingly compromised agent | injected line ends a job or deletes | OK | Ends caller's own jobs; sweep reads git. Subject to the injection grammar above. |
| Downgrade | older node writes device rows; older node asked | OK | `v` 2 refuses at sync; `ask` unknown op. Add: a v2 row of a kind outside table A is rejected (stored, not folded). |
| Approval fatigue | repeated `expose_state` | OK | One grant, rare. With per-asker scope there is one card per asker; batch cards of one person's devices. |
| Takeover by whoever is present | token from another terminal asks | OK | Read only, summary. |

## Local tables and the pool

**Lost or forged local table after a crash.** Covered in H3 (claims) and H7 (creators). Other tables: `jobs.json` forged by a
process of the same OS user is the same trust as `leases.json` and `tokens.json` today; write mode 0600 and say it is not a
boundary. A lost `jobs.json` ends all jobs and double-books `cpu_slots` for a moment: acceptable, but state that the next tick
reconciles rows (H3). `local/mail` lost: mail between local sessions is gone; a notice of claim expiry is lost too; fine, say so.

**Does moving leases local break cross-device exclusion?** For the kinds that were device-scoped, no: `view` already scopes
everything except `claim:branch:` and `claim:area:` to the writing origin (`pool_wide`), so no other device used those rows to
exclude. `claim:task` and `model_slot` are origin-scoped too: they give other devices only the liveness read, which H4(a) shows is
forgeable. The exclusion that matters is branch and area, and it survives only if H1 (end rows always written), H2 (rotation keeps
order) and H3 (table and rows reconciled) hold. Two more points:

- Two devices racing on one branch: both append before they sync; the fold resolves by hlc then origin, the loser learns at the next
  sync and its job is already running. Same as today; the design should say a job holding a branch claim re-checks `foreign_holds`
  on each tick and ends with a plain message when it lost.
- Path nesting: locally `worktree`, `install` and `area` claims conflict by containment (`Resource::conflicts`); across devices
  `view` compares exact keys. Open question 1's coarser option (a directory claim) would not conflict with another device's file
  claim under it. If the grain is ever coarsened, `view` must compare by prefix, with a test.

## What holds

The table A allow-list at `fold::entry` plus the call-site scan test is the right boundary; `lease` narrowed to four keys must match
the **kind** of each resource line, exactly (`claim:branch:`, not a prefix such as `claim:branchx`), over all eight lines of a row,
and reject the whole row if any line is outside, so a row cannot mix a pool-wide and a device-only line.
