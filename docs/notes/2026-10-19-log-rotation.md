# Log rotation for a board's entry log

Status: design with its attack-first pass, written by the author and then broken once by a non-author reviewer (section 7). AGENTS.md
section 1 ("Attack first") needs the revised pass in section 5 broken again before the code lands; the code is on branch
`feat/log-rotation` and is not pushed.

## 1. The problem and the measured rate

A device's log of a board holds at most `MAX_LOG_ROWS` (200,000) rows and `MAX_LOG_BYTES` (128 MiB). A log that reaches the end of its
reserve takes no author row, and one that reaches the end of the last stretch takes nothing, ever (`docs/limits.md`, "At a bound").

Measured with `PYTHONPATH=src:scripts python scripts/board_tally.py --days 1` on the board of this project, 2026-10-19: 5,767 rows in
1.0 days, **5,823 rows a day**, over every device's log together. 94 percent of them are `message` rows about test runs and session
starts and stops (`test-run`, `test-result`, `test-job start`, `test-job end`), identity and audit rows and claim (`lease`) rows. No
`note`, `room`, `task` or `grant` row was written in that day. At that rate a log reaches 75 percent (150,000 rows) in 25.8 days and
the cap in 34 days; the busiest earlier day (16,400 rows) reaches the cap in 12 days.

## 2. What a member checks today, from GENESIS

Read from `rules.rs`, `board.rs` and `sync.rs`:

1. **Shape** (`rules::shape`): version, board id, origin id, clock origin, body allow-list; the board id and origin are inside the row
   hash, so a row cannot be moved to another board or log.
2. **Chain** (`rules::check_chain`): the first row is seq 1 with `prev` = GENESIS (64 zeros); each next row is seq+1 with `prev` =
   the hash before; the hash is `sha256(prev || canonical(row))`; the wall clock never goes back.
3. **Heads** (`rules::check_heads`): a head row at seq h signs (Ed25519, `verify_strict`) the message
   `poolhouse-board-head-v1|pool|board|origin|h-1|hash(h-1)`. One key signs every head of an origin: the key already pinned for it, or,
   with none pinned, the key in the first head (trust on first sight). Rows are stored only through the last verified head.
4. **Pin** (`Board::store`): the first key seen for an origin is pinned; an owner that presents its log directly (`authoritative`)
   replaces a pin that came by a relay (`bound: false`).
5. Nothing checks who the key belongs to (membership is a separate check at the transport), and nothing binds a pool: `Node.pool` is
   `String::new()` and never assigned, so every head signs an empty pool id (`docs/design-job-log.md`, section 7, slice 0).

What the chain from GENESIS proves is that every row up to the last head is one hash chain under one key. It proves nothing about who
holds the key beyond the pin, and nothing about the pool. A segment that starts at a `base` row proves the same for the rows from the
base on; the base's `seq` and `prev` are claims about rows the receiver does not hold.

## 3. Design

Terms: a **segment** is the run of one origin's chain from a `base` row (or from seq 1) to its end. The **live segment** is the one in
`<origin>.jsonl`; **archived segments** are in `archive/<origin>.<first>-<last>.jsonl` beside it (0600, in 0700 directories).

**Base row.** `Kind::Base`, actor empty, idem empty, body `{"carried": n, "left": d}`. It is an ordinary next row of the chain (seq =
last+1, `prev` = hash of the last row), so a member that holds the old rows verifies it as any next row. `n` is the number of rows
that follow it in the segment before any other row (the restated rows); `d` is the number of durable rows that did not fit.
There is no separate signature on a base: a head covers it exactly as it covers every row, and a base is stored only through a head.

**When.** A device starts a segment on its own log when the live segment holds 75 percent of the rows or of the bytes of the cap
(`ROTATE_PERCENT`), checked after every write and when the board opens. The share and reserve logic of `logbounds.rs` applies to
the live segment unchanged: a new segment is empty, so the author shares, the reserve and the last stretch start again. Rotation is
off for `Board::open_bounded` (the bounds-only constructor the bounds tests use) and on for boards a `Node` hosts
(`Node.log_rotate_percent`, default 75; the test kit sets 0).

**What is carried.** The node's own state outside the log (leases, subscriptions, tokens, the registry) is not in the log, so the chain
head and the counters are not what the node needs. What lives only in log rows and is read by a fold after the base is the durable
kinds: `note` (the view's notes), `room`, `task` and `grant` (standing grants, their revocations and the uses that count toward a
limit). `message`, `audit`, `landing`, `lease`, `verify` and `identity` rows are history: they stay in the archive and leave the live
view (the registry in `names.json` holds the identity state; `#leases` rows are the retired claim record). The new segment is the base,
then a verbatim restatement of this origin's durable rows of the old segment (same kind, actor, idem and body, new seq, hash and
clock), with a head every 64 rows or 512 KiB, then a head. A note named by a `supersedes` of any held note, or past its `ttl_days`, is
not restated. Grant rows are restated (not the `use` rows of a grant with no limit or a revoked one, which no status reads); the other durable rows are restated newest first until `CARRY_PERCENT` (25) of the
cap is used, and the base says how many were left (`left`). Messages unread at the base, and a note's id, are lost to the view: a
restated row has a new id.

**Starting the segment on the writer.** The tail (base, restated rows, heads) is built in memory, the old rows are written to the
archive file, then the live file is replaced by one atomic rename of a complete new file. A crash before the rename leaves the old
log; after it, the new one. There is no state in between.

**A member that holds the origin.** It stores the base and the restated rows as ordinary rows (chain, heads, key). It applies the new
segment to its view once the base, its `n` restated rows and a head are held: until then the view of the origin is the old rows
only. Applying means writing the old rows to its archive and replacing its live file with the segment, as the writer did.
Before applying, a member that holds old rows checks that every grant revocation row of the old rows it holds is among the restated
rows as often as it was held (same actor, idem and body); a base that leaves one out marks the origin damaged, so a writer cannot
retract a revocation by starting a segment. Other grant rows are not checked: an issue left out closes a grant, and a use row is the
writer's own count of its own uses. The rows right after a base, as many as it says it carries, must be durable rows; a base followed
by anything else is refused.

**A member that holds nothing of the origin.** Its first rows may start at a base: seq at least 2 and at most 2^48, `carried` at most
half the rows of the cap, a head covering it under the key that is then pinned. It starts the chain there.

**A member behind the base.** It holds rows up to seq t, the base is at seq b and b > t+1. A device that holds the old rows (the
writer, or a copy that kept them in its archive) serves them from the archive in batches of 400 rows, so nothing between is missed.
When no archive has the next row, the base is served, and the member restarts at it only if the device that wrote the log sent it
(`authoritative`): a relay cannot move a copy past rows. A restart is held apart (`Board.staged`, in memory, reported in the member's
vector so the sender goes on) and the old rows stay the member's view until the base, every restated row and a head are held; then the
revocation check runs and the old rows go to its archive.

**What a base may not do.** A member refuses a base (the origin is damaged when the writer sent it directly, as for any bad row) when:
`carried` is over half the rows of the cap; the segment before it, as the copy holds it, has under `MIN_SEGMENT_PERCENT` (50) of the rows and of the bytes of
the cap (a copy that starts at the base, or jumps to it, holds no such segment and does not check this); its `seq` is not above the held tip; it names a seq a held row has with another hash (a fork); or it leaves out a held revocation.
A base needs no new kind of trust: nothing but the pinned key's head makes any row storable.

**Serving.** `rows_since` serves a member that holds nothing of an origin from the start of the live segment (the base, for a rotated
log), a member at or past the segment start from its tip, and a member behind the base from the archive as above.

**Acks and removal of archives.** `acknowledge` checks the hash of the row a member claims in the live segment, at the row before the
base (the base's `prev`) or in an archive. An archive is removed when it is older than 7 days (`ARCHIVE_MIN_MS`) and every device
that synced this board has acknowledged past its last row, or when it is older than 30 days (`ARCHIVE_MAX_MS`), or when more than 8
archives (`MOST_ARCHIVES`) of one origin exist. A removed archive is gone; a device that needed it starts at the base.

**Archive bytes.** The archives of one origin are held to the bytes a log keeps, newest first, whatever their age.

**Cuts.** A removal names a cut as (seq, hash). Seqs continue across segments, so a cut is a seq; a copy is truncated by seq. A cut
that falls before the live segment resets the copy (the rows it names are in the archive, not in the view), and rows past a cut are
refused as today.

## 4. What stays unchanged, and what is a known gap

- The chain, head and pin rules are unchanged; a segment start is a next row. `Log::open` and `check_chain` accept a log whose first
  row is a base.
- `fold::merge` leaves a base out of the view as it leaves out a head.
- Tally counts and the grant book rebuild when the live segment's first seq changes.
- A failed segment start (a full disk) is said on stderr and leaves the log as it was; it is tried again at the next write. The check
  for "is a segment due" reads the rows and bytes the log already counts and costs nothing per write.
- Gap: a request retried after the row it wrote was moved to the archive writes again (the idem key is in the archive only). It needs
  a reply lost at the moment a segment starts.
- Gap: a note that another device's note supersedes is not restated, but a note restated on this device and superseded later by
  its old id is not hidden (the id changed).
- Gap: an identity entry or an audit entry is history after a segment start; a member shows the sessions of a device from the
  registry, not from old entries.

## 5. Attack first (AGENTS.md section 1), written by the author; a non-author breaks it before it lands

For each case: the attack, what stops it, what is left.

- **A hostile producer of the carried state.** There is no snapshot producer other than the writer: the restated rows are rows of
  the writer's own log, each in the hash chain under the writer's key, restating only its own rows (never another origin's). A relay
  copies signed rows and changes nothing (hash chain plus head). The writer can restate a row it never wrote before, or leave one out;
  it can already write any row of its own log. Left out: a `grant` row it published (stopped by the held-grants check for members that
  hold the old rows; a member that joins after the segment start cannot check, which is accepted: it trusts the writer's word about
  the writer's own grants, as it trusts the writer's other rows). Left out: any other durable row (a note, a room membership, a
  task): the writer's word.
- **A forged base row.** Only the writer's key signs the head that covers it; a relay without the key cannot make a base that is
  stored. A base is never stored without a head. A base with a seq over 2^48 (so `seq + 1` could overflow), a `carried` over the bound,
  restated rows that do not follow it, or a count that does not match is refused. A forged base at a lower seq than a held tip is a
  fork (the hash at that seq differs) or ignored (the same hash).
- **A member withholding rows.** Withholding the restated rows leaves the view of the origin on its old rows until the segment is
  complete (nothing half-applied), also when the copy restarts at the base. Withholding the newest rows is staleness, as today. A relay
  cannot skip rows of the chain (the prev links and the heads force every row between) and cannot move a copy past rows that the writer
  or an archive still serves (`authoritative`). The writer withholding the archive makes a member behind the base start at the
  base; that member then misses the rows between its tip and the base. Accepted: it is the writer's own log.
- **Replay of an old segment.** Old rows replayed to a member that has applied a segment are before its segment start and are dropped
  without effect; an old base at a lower seq cannot rewind (its hash differs from the held row's, a fork, or it is the same row and
  is ignored). A whole old log replayed to a device that holds nothing of the origin is a valid old prefix (stale, not forged); the
  next base from the owner moves the device forward by the jump rule. Same as an old prefix today.
- **Cross-pool replay.** Heads sign an empty pool id today, so a base binds no pool by its head. What it binds: the board id and the
  origin id inside the row hash (the origin is 128 random bits made per device per board), the chain it continues (`prev`, `seq`) and
  the key of the head. A log of another pool can reach a member only as a different origin under a key of its own making, which is
  today's relay gap (slice 0 of the job-log design) and which a base does not widen: a three-row chain from GENESIS is as cheap as a
  base. When slice 0 assigns the pool id the head of a base signs it too. A base carries no pool field of its own.
- **A member behind the base.** The jump needs the base's covering head to verify under the pinned key and the base's seq to be above
  the member's tip; a relay can serve only the owner's real bases. A member that is behind is served from the archive first; if the
  archive is gone it starts at the base. Left: rows between its tip and the base that were never delivered (messages) are not
  delivered; the durable rows are carried.
- **Compromised or malicious member.** It can start a segment at any time only if its previous segment held 50 percent of the cap
  (`MIN_SEGMENT_PERCENT`) as far as a member that holds that segment can see, so it cannot force those members to rewrite their
  files on every row; a member that is behind and jumps cannot see it, and the cost to that member is one archive write of the rows
  it held; to retract recent history it must first
  write that much. A base at every 50 percent of the cap costs each member two file writes of the segment, once per 100,000 rows. It
  can omit durable rows (above). It can choose `carried` and `left` only within the bounds. It can fill its log (existing share
  bounds do not stop a device from filling its own log).
- **A stolen device key.** It signs rows and bases as the device. The remedy is removal; the cut is a seq, which continues across
  segments, so a cut still names the point the admins held. A removed device's base past the cut is refused with its other rows; a cut
  that falls before a copy's segment start resets the copy.
- **A malicious agent, or one compromised without knowing it** (an injected instruction in a message, note, page or file). Rotation is
  started by the node on a size, not by a request, and no request names what is carried; the carried rows are rows already accepted,
  restated byte for byte in their body. Text in a restated row is data exactly as before. A session can raise the log toward 75 percent
  only within its share (a tenth of the rows, a quarter for its tree; 16 live main sessions), so a flood by one tree cannot start a
  segment by itself; four trees can, and cause the history of messages to leave the view earlier. Durable rows cannot be flushed out:
  they are carried. Left: a flood of durable rows (notes, rooms, tasks, grants) each within its own bound raises the carry toward
  its 25 percent, which makes the next segment start after 50 percent of the cap instead of 75.
- **A stolen phone or person key.** A grant row carries a person's signature over its spec in the body; a restated row carries the
  same body, so the signature still verifies and counts; no restatement can sign for a person, and the node signs nothing for a
  person. A stolen person key issues grants exactly as before.
- **Downgrade.** An older node cannot parse `kind: "base"` and refuses a log that holds one, so no member runs the old rules on a
  rotated log. A newer node never relaxes a check: every rule above only adds to the old ones. A segment start that cannot be
  written (disk full) leaves the old log and the old behaviour (full at the cap); it is retried at the next write.
- **Approval fatigue.** There is no prompt, request or approval in rotation; a person sees nothing.
- **Takeover by whoever is merely present.** No call starts or shapes a segment; a process on the device cannot name the carried
  rows or the moment. A process that can write files in the state directory can already replace a log.
- **A copied or cloned device or pool.** Two clones of one state write one origin from the same point and fork it; a segment start by
  one makes the fork visible as at any seq (the hash at a seq differs). The archive holds the same rows as the log and is as secret
  as the log (0600).
- **Equivocation.** A writer that tells two members two histories is caught when they compare the hash at a seq they both hold. A
  segment start keeps that for the live segment and the base; for rows only in an archive that was removed it is lost. Accepted.
- **Write and disk cost.** Each start rewrites one segment file (at most the cap) and writes one archive (at most the cap); at most
  8 archives of one origin are kept.

## 6. Tests (written first, each watched failing)

`app/poolhouse-node/tests/log_rotation.rs`: the segment starts at 75 percent and keeps the chain; a restart with a log past 75
percent; three members across a segment start (one synced the old rows, one joins after, one is behind and served from the archive,
one behind after the archive is gone); a crash before the rename; a base with a seq over the bound, with more `carried` than the bound,
below `MIN_SEGMENT_PERCENT`, and leaving out a held grant; replay of an old segment; a head from another key; a cut across a segment
start; durable rows carried and history left; archive removal rules. `tests/log_bounds.rs` and `tests/node_attacks.rs` run unchanged
(the test kit sets `log_rotate_percent` to 0).

## 7. The non-author review and what changed

A reviewer (Sonnet 5.5, not the author) wrote seven attack tests; all seven broke the first version. Each is fixed and has a test
in `tests/log_rotation_review.rs` that goes red when the fix is removed:

1. A restart at a base ran no revocation check, so the writer or a relay retracted a held revocation: a restart is staged and checked
   when the segment is whole.
2. A relay that served part of a segment removed the grants a copy held: the old rows stay the view until the segment is whole.
3. A relay moved a copy past rows the writer would serve from its archive: a restart over held rows needs the writer (`authoritative`).
4. A copy at the end of a relay's archive never advanced: when the archive has no row past what the member holds, the base is served.
5. Grant rows past half the cap stopped rotation for good: `use` rows of a grant with no limit or a revoked one are not restated.
6. Three identical revocations collapsed to one in a set: revocations are compared as a multiset.
7. A base could lie about `carried` and freeze a copy's view: the rows after a base must be the durable rows it counts.

Also changed from the review: a log that cannot be opened again after a new segment takes no more rows, and archives are held to the
bytes a log keeps. Left as the reviewer named them: an archive file is read whole for each batch served from it; a restart is not
compared with the chain a copy held (the jump is the writer's word about rows between); a hostile writer costs a copy at most
8 archives and a live segment on disk per origin (the bytes bound holds the archives to one log's worth).
