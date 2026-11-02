# Signed ordering

Design only; nothing here is built. The owner ordered it: races are ordered by signed counters that every member can check, not
by clocks the signing device chooses. Words follow [glossary.md](glossary.md). Read at `bceddf929` (`origin/0.3dev`) in
`app/poolhouse-node/src` (`row.rs`, `rules.rs`, `board.rs`, `log.rs`, `sync.rs`, `lease/merge.rs`, `lease/table.rs`,
`tasks/fold.rs`, `grants/book.rs`, `gov/{act,chain,roster,pool,clock,logs}.rs`) and the Python side (`board/session.py`,
`workspace/board_page.py`); neighbours: [design-job-log.md](design-job-log.md) (job chain, segments, slice 0),
[node.md](node.md) ("Board entries", "Who governs a pool"), [pools-and-serving.md](pools-and-serving.md) ("Time"). Not read in
depth: `presence/`, `phone/`, Windows paths.

## 1. What orders what today

An entry carries `hlc = (wall ms, counter, origin)` that the writing node computes (`board.rs:300`, `tick`). Nothing checks it
except that the wall never goes back from the writer's previous entry (`rules::check_chain`, line 82; the first entry of a log has
no previous one). `Node::view` sorts every entry of a board by it (`Row::order`, `rules::merge`) and every fold of state reads that
one order. Acts of governance have no log: the order of their effects is the awake time at which this device first saw them
(`gov/pool.rs:456,514`) or the `at_ms` their signer wrote.

| Where | Ordered today by | What a member can do today |
| --- | --- | --- |
| Every view and fold of a board (`Row::order`, `rules::merge`) | (wall, counter, origin, seq), all written by the author | sort first: write `(1, n, M)` (S1) |
| Pool-wide branch and area claims (`lease/merge.rs` `view`, `Table::yield_to`) | the earliest acquire in the line above | win every claim (S1) |
| A task claim (`tasks/fold.rs:173`) | the first claim in that order while the task is queued | win every task claim (S1) |
| A job's pool-wide hold (design-job-log 3.1, item 3) | the same order | the same |
| Grants (`grants/book.rs:65`) | the same sort; a grant's removal entry applies in any order; a use is a count, not an order; a repeated `gid` keeps the first valid issue | nothing: each is signed by a person |
| Repeats of one `idem` (`rules::merge`) | first in that order within one origin | nothing: one origin's wall never goes back |
| A removed device's entries (`Board::cut`, `gov/logs.rs`) | the (seq, hash) the remover held | on a board the remover never synced the cut is empty and the entries stand (known limit) |
| A device's `leave` against an `admit` of it (`roster.rs:321-337`) | the signer's `at_ms`, then id | order its own leave against a later admin admit; a delegate re-admits a device that left by dating the admit just after the leave (S4) |
| A signer's standing when it signs an admit (`member_at`, `delegate_at`, `roster.rs:197-222`) | the order this device first saw the signer's admits, leaves and delegate acts | choose what each device sees first (S3) |
| Delegate and open-member admit rate (`over_rate`, `roster.rs:226`) | first-seen awake time of the admits | the same (S3) |
| Start and length of a hold (`chain.rs:312`, `pool.rs:456`, `clock.rs`) | this device's awake time from its own first sight of the complete act | keep an act from one device (S5) |
| Cancel and veto against an act, "arrived after the step took effect" (`chain.rs:262-281,314-317,355-363`) | first-seen time on this device | the same (S5) |
| Acts a joiner takes as in effect (`pool.rs:514`, `chain.rs:313`, `roster.rs:221,245`) | `SETTLED`: the host's word | S6 |
| Python: `board_page.py` sorts by (at_ms, counter, id); `Entry.counter` | the node's `hlc` | display only; no Python code decides a claim |
| Forks between admin-set acts (`chain.rs` `rank`: strong signers, then fingerprints) | signed data only | nothing; unchanged |

The sequences:

- **S1, backdated claim.** A member M (a hand-built log or a changed node) writes as its first entry on a board a `lease` entry,
  acquire of `claim:branch:0.3dev`, with `hlc = (1, 0, M)`. `check_chain` accepts it. Every device sorts it before everything else;
  `lease::merge::view` gives M the branch and each holder's `Table::yield_to` ends its lease `Lost` at its next tick. M's later
  entries are `(1, n, M)` with `n` rising, so it wins every pool-wide claim and task claim until it is removed. Another member's
  claim at wall `W` is beaten by writing `W - 1` the moment it is seen; holding entries back and releasing them late is not needed.
- **S2, a clock that is only wrong.** An honest node ticks past the largest entry it has stored, so a device a day behind ranks
  ahead of every claim it has not received yet, and a device a day ahead ranks behind. The order cannot tell this from S1.
- **S3, delivery order of admits.** A delegate D signs admits a1 to a4 inside one hour and delivers them to device P in the order
  a1, a2, a3, a4 and to device Q as a4, a1, a2, a3. P stands {a1, a2, a3} and Q stands {a4, a1, a2}: the rate is counted by the
  order each saw them. The two rosters differ and D chose how. The same delivery choice flips `member_at` when D signs a `leave`
  and an `admit`.
- **S4, leave against admit.** X signs a `leave` with `at_ms` five minutes ahead (the most allowed); an admin's admit a minute
  later has the smaller `at_ms`, so the leave stands as the later act. A delegate does the reverse: it dates its admit of X one
  millisecond after X's leave and X is back in, with nothing in the record to show the order was chosen.
- **S5, a hold kept from one device.** A relay between the pool and device P hands P an `admin_remove` and withholds the
  `cancel`. P's awake clock runs the hold out; the `cancel` arrives later with a later first-seen time and is ignored
  (`stoppable_until`). P's roster differs from every other device for good.
- **S6, the joiner's word.** A host hands a joiner admits and acts marked `SETTLED`. The joiner counts them as past their hold
  and past the rate (`roster.rs:245`); the acts can still be cancelled until a hold has run from the join, which is all that
  bounds it.

## 2. The options, and the decision

What a design has to defeat: (A1) backdating, a smaller clock or counter than the member has earned; (A2) lying by omission, or
withholding an entry and releasing it late, so that a claim made after seeing another looks concurrent; (A3) a minority that
claims while partitioned; (A4) delivery order chosen by a member; and it has to keep `partition-heal` (both sides claim, one
holder after the merge) and `claim-pool-wide` (a second device is refused naming the holder) as they are.

| Option | A1 | A2 | A3 | Cost and offline | Verdict |
| --- | --- | --- | --- | --- | --- |
| (a) a signed `seq` per origin plus cites: each entry names the newest entries of other origins its writer held (a happens-before graph), then a tie-break | fixed: a claim cannot rank ahead of an entry it cites | not fixed: a writer that omits a cite looks concurrent and wins the tie-break one time in two | a minority claim ties and wins half the time | small; works offline | needed, not enough |
| (b) a signed counter owned by the pool: one sequencer signs every grant of a pool-wide claim | fixed | fixed | fixed: only the sequencer's side can claim | one member decides every race; offline it ranks nothing; electing and replacing it is a new protocol that removal, partition and a stolen key all attack | rejected |
| (c) witness co-signing: a head counts as seen at a time only when k members signed it | fixed | fixed | fixed | a second kind of signed message, collected with timeouts, carrying the information a cite already carries | rejected as a mechanism, kept as a reading of (a) |
| (d) logical time only: a counter and the origin id as tie-break | not fixed: the counter is the writer's (`seq` is the least it can write) and the origin id is a random value the member picks at its first use | not fixed | not fixed | smallest | rejected |

**Decision: (a), with (c) read from the cites.** An entry that cites X is its writer's signature that it held X (the head that
follows signs the cite). So no new message exists, and every device ranks from the same signed entries whatever order they
arrived in, which is all A4 needs. A race is ordered by three rules, in this order:

1. **Causal.** An entry that demonstrably saw a competitor (it cites it, or cites a later entry of that origin) never ranks
   ahead of it. A member cannot claim to have acted before something it cited.
2. **Quorum time.** Among competitors that did not see each other, the one that a majority of members first had, by the times
   those members themselves wrote, ranks first. Nothing a claimant writes sets it: not its clock, not its counter, not its cites.
3. **Identity hash.** Equal or unreached: the least SHA-256 of pool id, key and device fingerprint. It is fixed when a device is
   admitted and differs per key, so no member picks it at claim time.

It lands in two levels (section 6). Level 1 is rule 1 and rule 3 (slices 1, 2 and 5); it ends backdating and the delivery-order
dependence of a roster, and leaves A2 and A3 at the tie-break. Level 2 is rule 2 and what reads it (slices 3, 4, 6, 7 and 8); it
ends A2 and A3 for every pool of three or more. Each level is a strict improvement and lands alone.

## 3. The board: entries, cites, quorum time

**3.1 The entry.** `hlc` becomes `at`: the wall ms the writer reports, still non-decreasing per origin. `at` is for display, for
quorum time and for pool time (4.5); it never orders a claim and never starts a hold. A new field `after`, on entries of an
ordered kind (`lease` with a pool-wide resource line, `task`, and the new `receipt`; one list, `Kind::ordered`, widened when a
kind starts to race, as `job` will): `[[origin, seq, hash32], ...]`, the highest `seq` of every other origin the writer holds
above what it last cited of that origin, at most 16 (a base entry, 5.2, up to 64). Plain entries (message, note, identity,
audit) carry none, so chat costs nothing. `after` is inside the entry's hash, so the head that follows signs it.

`check_chain` refuses: a cite of the entry's own origin, one origin cited twice in an entry, a cite below what the origin cited
before (cites only rise), a malformed hash, more than the cap. A cite to an entry the reader does not hold yet waits; one whose
hash differs from the entry held at that `seq` counts for nothing. The node writes cites itself; no body field names one, so
nothing a session sends can set them.

**3.2 The cite index.** Derived, never stored: for each member M and origin A, the list of (M's `seq`, M's `at`, the highest `seq`
of A that M's entry at that `seq` cited), built as entries arrive and rebuilt with the log (cut, reset, forgive, damaged,
removal). Two lookups serve every rule: `reached_by(M, X)`, the `at` of M's first entry whose cite of X's origin is at or above
X's `seq`; and `saw(Y, X)`, whether Y's writer had cited X's origin at or above X's `seq` by Y's own `seq`. Both are one binary
search per member, and the index holds one line per cite, so it is bounded by the log's entry cap times the cite cap.

**3.3 Receipts.** A device writes a `receipt` entry (empty body, `after` = the new tips) once per sync round and board in which it
stored an ordered entry of another origin. It is idempotent (`idem` is the digest of its cites), it is the node's own class of
entry in the log shares, a receipt owes no receipt, and `fold::entry` skips the kind as it skips `head`. A member that never
writes receipts gives no cover and breaks nothing.

**3.4 Quorum time.** n is the number of origins on the board whose writer is an active member of the pool (a removed device counts
up to its cut); k = floor(n / 2) + 1. q(X) is the k-th smallest of `reached_by(M, X)` over those members (for X's author the value is
X's own `at`), or infinity when fewer than k members have it. What it gives:

- A coalition of fewer than k members cannot place an entry before the moment the first honest member had it: it has fewer than k
  small values to give. An author's own value is one of the n, and k is at least 2 once n is 2 or more, so no one value decides.
- Withholding raises q: the honest members' values are when they received the entry.
- Every device holding the same signed entries computes the same q, whatever order they arrived in.

**3.5 Rank.** For a contested key (a branch or area, a task, a job), the candidates are its open acquires or claims. Among the
candidates that no other candidate demonstrably precedes (`saw`), the winner has the least q, then the least identity hash. The
loser's device ends its lease `Lost` at its next tick, as `Table::yield_to` does today. Callers: `lease::merge::view` (best of the open
acquires of a key in place of "first `or_insert` wins"), the claim of `tasks/fold.rs` (the best claim of a task, the rest refused),
and the job chain's `Index::apply` (design-job-log 3.1, item 3, which names `Row::order` and takes `Board::rank` instead).
Releases and ends stay in their writer's chain, in `seq` order. A claim is *settled* when every active member has it; until then
the entry says `provisional`, which the status word already exists for. Nothing waits for a quorum to be used: the local table
grants at once, as today, and a lone claim is the holder.

**3.6 What stays on `at`.** The view of a board (messages, notes, rooms) sorts by (`at`, origin, `seq`); the five-minute hold-back
of an entry dated ahead stays so a future date cannot pin a line to the bottom. This order decides no race; a member can place
its own messages early on the page, which `status` shows as a clock difference (7, clock-skewed device).

**3.7 Cost.** Every entry is about 40 bytes smaller (`hlc`'s copy of the origin and the counter go). An ordered entry grows by
about 85 bytes a cite, one to three in practice. A receipt is a few hundred bytes. A device writes at most one a round and at most
as many as the ordered entries that reached it; on the real board `lease` entries were 21 percent of 57,604 entries in 8 days (the
job-log review), so level 2 adds a fraction of that share to a device's log, which slice 3 measures on a scripted day and
`scripts/status` then shows as entries a day. The rank of a key is one search per member, 64 at most;
the index rebuild is one pass over the log (bound by a test, slice 2, against the 200,000-entry cap). Latency: nothing a person
waits on; "settled" shows one sync round after a quorum has the entry.

**3.8 Partition and healing.** Each side ranks what it holds: a lone claim is the holder and says `provisional`. On healing the
entries cross, q counts every member's first cite, and a claim that a majority had before the cut ranks ahead of one made on the
minority side whatever either side's clock says; an even split ranks by which entry reaches k members first after the merge, then by identity hash.
`yield_to` ends the loser. `partition-heal` ends with one holder; `claim-pool-wide` is unchanged, because the second device sees
one candidate and refuses it naming the holder. A device alone (every other member away) holds what it claims and nothing
contradicts it; members away for good make n larger, so a contested claim falls to the identity hash and an uncontested one is
untouched; removing the device is a person's choice and never a wait.

## 4. Acts of governance

Acts have no log, so they get the same two things a log has: a signed counter and cites, and a witness record.

**4.1 Counter and cites in the act.** Act version 3 adds `dn`, on device-signed acts only (`leave`, a delegate's or an open pool
member's `admit`): the signing device's own counter, one more per act it signs, written to disk before the signature leaves the
node. Two different acts with one (device, `dn`) are a fork: both are void and that device signs no delegate admit. It adds
`after` on every act: the ids of the maximal acts the drafting node holds (at most 8). `pool_draft` fills it into the bytes a
person signs; the sentence of `describe.rs` does not change, so a person sees nothing new. `at_ms` stays for display and for the
`not_after` window at submission; no fold reads it for order.

**4.2 Witness record.** A device that holds an act complete (every signature its kind needs, by the verdict of the chain) writes a
`receipt` entry on the `pool` board (`POOL_BOARD`) naming it (`acts`, at most 64). q(act) is the same function as 3.4 over those
entries, with n the active members. Fewer than k receipts is infinity. An admin's admit needs no receipt to stand (a person's
decision and the onboarding path wait for nobody); the rate, a delegate's window, a hold and a cancel read q.

**4.3 Leave against admit.** For one device, its `leave` and the admits of it are ordered by the three rules of section 2; the last
decides, so an admit that cites the leave is later by rule 1 and an admit that does not is later when q says so. Concurrent and
both unreached fall to the identity hash. Nothing is ordered by `at_ms`: X cannot date its leave ahead of an admin's admit, and a
delegate cannot date an admit behind it.

**4.4 Standing and rate.** An admit by a delegate stands when (a) its q is finite, (b) the delegate act that names D is effective
and D's `delegate_end` does not rank before it (rule 1 by `after`, else q), and (c) it passes the rate. D's admits under one
grant are walked in the order of D's `dn`, each taking as its time the later of its own q and the time of D's previous act, so D
cannot place a later act before an earlier one; at most 3 in any hour and 10 in any day of those times (the owner's numbers, kept;
5 an hour for an open pool's member, 10 for all of them). Every device walks the same list and drops the same admits. `member_at`
becomes "D's `leave` ranks before the admit". The cost is one sync round before a delegate's admit stands; an admin's does not
wait.

**4.5 Holds, the awake clock, and Windows.** What is measured, and by whom:

- *Validity* (a pool of three or more) is read from the record, the same on every device that holds it. The hold of an act
  starts at q(act), the k-th receipt: an event any member can check, not a device's own first sight. A `cancel` or `veto` is
  valid against an act when its q is below q(act) plus the hold. A device that applied an act and then learns of a valid cancel
  folds from scratch and reverts, as the roster already folds from the acts alone, so S5 heals when contact returns.
- *Effect* (three or more): pool time, the k-th largest of the `at` of each active member's newest entry on the `pool` board. A
  hold is over when pool time reaches q(act) plus the hold. A device writes a `receipt` with no acts, a beat, at most once an
  hour while it holds an act in its hold, so pool time moves with no other traffic. A device eclipsed from a majority has no
  fresh entries to show and its holds stall: they fail closed. No device's own clock is read.
- *A pool of one or two:* nobody else can witness time, so validity and effect stay today's rule: first sight on this device and
  `gov/clock.rs`, the device's awake time, never early. Moving to three devices moves the pool to the record.

The Windows sleep limit (HANDOFF, from the second attack review): `Instant` on Windows does not run during sleep, so a hold on a
device there is longer than its hours. Under this design that changes nothing the pool decides: validity and, in a pool of
three or more, the end of a hold come from signed entries, which count real time asleep or not. In a pool of one or two the hold
is longer on that device, which is the safe direction and is the rule the clock's comment already states ("a hold never ends
early for a device that was off"); `status` shows the seconds a device slept (the gap between its wall and monotonic clocks at
a tick), so it is said and not silent. The host's word goes (S6): a joiner counts an act's hold from the record in a pool of three
or more and from its own first sight in a pool of one or two, so `SETTLED` stays only as the mark of the founding admit, and
`joined` and the cancel-until-a-hold-from-the-join rule are deleted. Until the `pool` board is synced a joiner treats acts as
unreached. The first-seen maps (`seen`, `ready`) stay for the pools of one or two.

**4.6 Removal against use.** The removal's list of cuts stays as the fast path. In addition, an entry of a removed device that is
whose q is not below q(removal) is void (both are wall times that members wrote, so they compare). A board the remover never
synced no longer leaves the device's later entries standing: a member that withholds entries on a board and releases them after
the removal reaches a quorum finds them void. An entry the removed device wrote honestly but that no quorum had before the
removal reached one is void too; the cost is named in section 8.

## 5. Interfaces

**5.1 Pool id in signatures** (design-job-log slice 0, a prerequisite and unchanged by this design). A head signs the pool id and a
device that joins starts a new origin per board. Cites name origins, and an origin of an earlier pool is unknown in the new one,
so a cite to it counts for nothing; an entry of another pool relayed in fails at its head. Acts name their pool already
(`act.pool`), so the ids in `after` are pool-bound. Receipts and `dn` add no new domain: a receipt is an entry, and `dn` is inside
signed act bytes.

**5.2 Segments and the base entry** (design-job-log 3.7, built by the log-rotation work in `log.rs` and `board.rs`). Counters
continue across a base entry:

1. `seq` and `prev` continue as designed; `at` stays non-decreasing across it.
2. The base entry restates the writer's cite state: `after` with its highest cite of every origin (up to 64), so `saw` and
   `reached_by` stay defined for entries after the base although the entries that carried the old cites are gone.
3. A base is written only when the open pool-wide claims it carries are settled (every active member has them). Each `carry` entry
   of a claim names the original (`of`: seq and hash in the old segment) and the claims it outranked (`beats`, at most 8, as
   origin, seq, hash), because a loser that never ends its own entry would otherwise outrank the carry once the original is gone.
   A member that still holds the old segment checks both when the base arrives; a lie marks the origin damaged, as design-job-log
   already does for a false base.
4. The old segment stays until every active member has acknowledged an entry past the base, as designed; that is the window in
   which the check in item 3 is possible. A device that joins after takes a carry on the writer's word until a member that held
   the old segment says otherwise: named in section 8.
5. q of a dropped entry is not recomputed; the settled decision lives in the `carry`. The first-seq addressing (`seq - first`) in
   `accept`, `trusted`, `cut`, `acknowledge` and `rows_since` is the rotation work's; the cite index keys on `seq`, not position.

## 6. Migration and slices

None. `VERSION` rises to 2 for entries and act version to 3. A device refuses its own old logs and acts with one plain sentence
("This pool was made by an older version of Poolhouse and cannot be used; make a new pool") and starts fresh; there is no mixed
build and no reading of `hlc`. Each slice lands alone, tests written first and watched failing, its fixed cost bound by a test
measured in the slice (targets for the slices to confirm by measurement: under 5 ms to append an entry with 16 cites, under 1 s to rebuild the index of a 200,000-entry log).

| # | Slice | Delivers | Tests (watched failing first) |
| --- | --- | --- | --- |
| 0 | the footing | design-job-log slice 0, as written | as written there |
| 1 | entry v2 | `at` for `hlc`; `after` on ordered kinds; `check_chain` rules of 3.1; the node writes cites; `observe`, `seen` and the hybrid `tick` go; the API's `hlc` becomes `at`; Python `Entry.counter` and `board_page.py` lose the counter | an old log is refused with the one line; own-origin, repeated, falling, over-cap and malformed cites are refused; a second ordered entry cites only what rose; `at` never falls; a chat entry carries no cite |
| 2 | cites and rank (level 1) | the cite index; `Board::rank` (rules 1 and 3); `lease::merge::view`, the task claim and the job index use it | S1 as a test with a hand-built log wins nothing; the same ranking on three devices in every delivery order (shuffled); a claim that cites the other never ranks ahead; rebuild equals incremental; `partition-heal` and `claim-pool-wide` pass; fixed cost of rank and rebuild |
| 3 | receipts and quorum time (level 2) | `Kind::Receipt`, the rule of 3.3, q, rule 2 in `rank`; the entries-a-day figure in `scripts/status` | withhold-and-release loses; the minority side loses after healing; k - 1 liars cannot place an entry before the first honest member; unreached claims fall to the hash; receipts idempotent, one a round, none for a receipt; entries a day on a scripted day |
| 4 | fork proof | two heads of one origin at one `seq` travel as a proof every member verifies; the origin is cut at the fork on every device and its cites stop counting after it | a clone's fork converges on three devices; a relay cannot forge a proof; cites after the fork count for nothing |
| 5 | acts: counter, cites, standing | act v3 (`dn`, `after`); equal `dn` is a fork; `member_at`, `delegate_at`, leave against admit by rules 1 and 3 of section 2 | S4, and the `member_at` half of S3, as tests: the same roster on two devices in every delivery order; no `at_ms` decides; equal `dn` voids both acts |
| 6 | act receipts and the rate | act receipts on the `pool` board; q(act); rule 2 for acts; the rate of 4.4 | the rate half of S3: the same admits stand on every device in every delivery order; 11 admits in a day leave the same 10; an admit not yet reached does not stand; an admin's admit stands with no receipt |
| 7 | holds by the record | hold start at q(act); cancel validity by q; pool time and the beat; the host's `SETTLED` marks and `joined` removed, the first-seen maps kept only for pools of one or two; `status` shows sleep | S5 heals; a joiner derives what is settled from the record (S6); a hold in a pool of three ends the same with one device asleep for a day; pools of one and two keep the awake clock |
| 8 | removal by rank | 4.6 | an entry withheld on a board the remover never synced is void after the removal |

Slice 1 touches the entry type, so it lands before slices 2, 3 and 4 and after the rotation work or merged with it (they edit the
same files); slice 5 stands alone; slices 6 to 8 need 3. Documents that change when a slice lands: node.md ("Board entries", "Who governs a pool":
Time, The roster), pools-and-serving.md ("Time"), board-replication.md (ordering), design-job-log.md (3.1 item 3, 7 and the
accepted backdating in 12), pool-check.md, the known-limit lines of `HANDOFF.md` and `release-readiness.md`, which this removes
rather than annotates.

## 7. Attack first

This is the written pass AGENTS.md requires; a non-author reviews it before slice 1 is built.

| Case | What the attacker does | What stops it, or what is left |
| --- | --- | --- |
| Copied or cloned pool or device | a clone shares the key and origin and both append; copied state is run as a second pool | the fork is two heads of one `seq`: slice 4 cuts the origin at the fork everywhere and its cites stop counting; two acts with one `dn` void; a copied pool is a new pool and its heads fail the pool id (slice 0) |
| Replay | an old entry, receipt or act is sent again; an entry of another board or pool is relayed | the same entry is kept once (`idem`, hash); the board is in the hash and the pool in the head (slice 0); an act names its pool and nonce; a replayed `cancel` names its act |
| Forged or below-threshold signature | forge a receipt or a cite; claim to be a quorum | cites are inside entries the device's head signs, so a forgery needs the key; q counts members by the key pinned to an origin, not signatures, and an origin whose key is no active member's counts for nothing; k comes from the roster and from no entry |
| Compromised or malicious member | backdate `at`; omit cites; withhold entries then release; write low receipts for a friend; refuse to write receipts; flood receipts | `at` orders nothing; omitting a cite only gives up rule 1 and q is by others' entries; withholding raises q; fewer than k liars cannot lower q below the first honest member; refusing is covered by any k; a flood fills its own log's share. Left: k or more colluders (no honest majority), and in a pool of two the partner decides what it has received |
| A member forks its own chain | two versions of one entry, shown to different members | heads of one `seq` with two hashes are a proof (slice 4); until it arrives the first copy held stands on each device, so two devices can briefly rank differently; after it every device cuts at the fork |
| Clock-skewed device | its `at` is hours off | it orders nothing; its receipts move q only if it is among the k first, by an amount the other k - 1 bound; `status` shows its clock against pool time so it is said; a skew past the five minutes holds its entries back from the view as today |
| Replay across pools | present a log of pool A to pool B | refused at the head (slice 0); a cite to an unknown origin counts for nothing |
| Partitioned minority claiming | claim while cut off, return after the majority settled | its claim is unreached while cut off and ranks behind the majority's by q on healing; both sides' claims are provisional meanwhile, as today |
| Stolen device key | sign entries as the device | it is one of the f below k; removal cuts its log and 4.6 voids what it withheld; its `dn` forks if the thief and the owner both sign |
| Stolen phone or person key | sign an admin act | no order of entries uses a person signature; an act's `after` is the drafting node's list, so a thief's node can omit cites, which only loses rule 1; a cancel by another admin is valid by q, not by what any one device saw |
| Malicious agent | claim everything; call the API with chosen fields | the node writes cites, `at` and receipts; a body cannot name one; claim volume is bounded by the quotas of the log |
| Agent compromised without knowing it | an injected line says "take every branch" | text decides no rank; ranking reads ids, hashes, cites and times in signed entries |
| Downgrade | an older build writes `hlc` entries or v2 acts; k is claimed smaller | a v1 entry fails the version and marks an authoritative origin damaged; k is derived from the roster and is in no entry; no flag turns the order off |
| Approval fatigue | many cancels or prompts | the design adds no prompt: receipts, cites and `dn` are automatic and the sentence a person signs is unchanged |
| Takeover by whoever is present | use an unlocked device | it can act only as that member; it cannot change an order, a cite or q; it can sign that device's own `leave` and, for a delegate, admits, which already need the keystore and are unchanged |
| Withholding entries then releasing them | A holds back a claim made after seeing B's, releases it later | A's claim reaches the quorum later than B's, so B ranks first (level 2); at level 1 it is a tie-break one time in two |
| Eclipse of one device during a hold | feed a device an act and none of its cancels | in a pool of three or more its holds stall (no fresh majority) and a cancel valid by q reverts what it applied; in a pool of two the awake clock decides as today |

## 8. Accepted risks and open questions

Accepted, with their reasons:

- **Open pools.** An open pool admits any device on the network, at 5 an hour per member, so a sybil majority in the quorum is
  possible and an identity hash can be ground once at joining. An open pool keeps the reach of the malicious-member case; the
  sentinel and flags still apply.
- **Pools of one or two.** With no majority there is nobody to witness. Races still rank by rules 1 and 3 and by arrival in a pool
  of two (k = 2); a hostile partner can win a contest it creates by not writing receipts. Holds there stay on the awake clock. The
  person removes a hostile device; a third device removes the limit.
- **A claim made inside one witness's receipt.** If one member's single entry is the k-th cover of two competitors, they tie in q
  and the hash decides.
- **A joiner after a rotation** takes a `carry` on its writer's word (5.2); a false carry is caught by every member that held the
  old segment and costs the writer its origin.
- **Equal-time ties on the identity hash.** Grinding a key to win unreached ties for a known branch needs a person to add that key.
- **A removed device's honest entries** that no quorum had before its removal reached one are void (4.6); the writer's work on a board
  it shared with nobody for that long is lost, which is the price of closing the unsynced-board hole.

Open, where two options are both correct:

1. **Two-device pools.** Quorum 2 of 2 (a claim ranks by which device received the other's claim first; the partner can win by
   withholding receipts) or the identity hash alone (no receipts at all in a pool of two, a fixed winner per key, which is
   arbitrary for honest contests). Until decided, the rule in force is quorum 2 of 2, as written in 3.4.
2. **Pool time in a pool of three or more with a majority away.** Fail closed (holds stall until a majority returns, which is the
   rule written in 4.5) or fall back to the awake clock after a number of days the charter names (liveness for a pool whose
   devices travel, at the cost of the eclipse protection for those days). Until decided, the rule in force is fail closed.
