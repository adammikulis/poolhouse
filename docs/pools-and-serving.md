# Pools, governance, serving and shared boards

Design, 2026-10-11. Nothing in this document is built. It replaces the single-pool assumption of `docs/node.md` (the section on
the pool) and adds three things the product needs: a device that belongs to several pools, a device that serves a local model to
several pools, and a board that more than one pool holds. It starts with the question every other part depends on: who is allowed
to change a pool.

**Who decides what changes a pool? People do, through admin keys, never a device. Today nothing does:** any member device can add
any device, remove any other member (the founder included) and set the join policy, and the code accepts it (section 1.1 shows it
with a test). In this design a pool has admins (the founder's key from the first approval, plus any backup or trusted person's
key later), every change to the pool is a signed act that every member checks against the admin keys it already holds, and a member
device can do one thing alone: leave. **The first device is not special.** It only held the founder's key because that is where the
pool was made; if it goes away nothing changes as long as the key also lives on a phone or a backup, and the pool says plainly when
it holds only one key. **A pool that wants to go from open to secure after the original people and devices are gone** is changed by
any admin that is left (a backup, a trusted person, or the recovery key after a public delay), and it is changed by reissuing the
pool: the join policy never flips in place, the admin makes a new pool with the other policy, signs which members it keeps, and the
kept devices move by themselves with nobody pressing anything on them. **If no admin key can be reached at all**, the pool keeps
working exactly as it is and nobody can change it, however many members are present: being present never gives authority. A new
admin starts a successor pool and every member joins it by its own consent.

## 0. The decisions, one line each

| # | Decision | Why |
|---|---|---|
| G1 | A pool is governed by an admin set of person keys named in its signed charter; the pool id is the hash of the first charter. | Authority belongs to people; an id that commits to the admins cannot be copied onto other admins. |
| G2 | Every change to a pool is a signed act, checked by each member against the admin keys it holds. Rows a member relays carry no authority. | Closes the hole of section 1.1: today any member's word is the pool's word. |
| G3 | A member device can only leave. Routine acts (admit with a ticket) are signed by a delegate the admin approved once; sensitive acts need a tap. | Zero visible steps in the normal case; a tap only where a mistake is irreversible. |
| G4 | The join policy is immutable. Changing it reissues the pool under a signed handoff. | An in-place flip leaves every earlier joiner a member; a reissue keeps only reviewed ones. |
| G5 | A device follows a reissue only if the old pool's admin quorum signed it, with keys the device already holds, and it names this device. | A copy of a pool, or a stranger reusing its name, moves nobody. |
| G6 | No admin reachable: the pool keeps working and cannot be changed. Optional members-governed mode appoints admins after a long public delay. | No takeover by presence. |
| M1 | One device identity (one key, one certificate) is shown to every pool. Authority is decided per request and per pool. | One listener and one handshake; separate certificates would not hide a device on one network and would cost a handshake per pool. |
| M2 | A board belongs to one pool unless the pools' admins sign a charter that shares it; sharing is by named devices, whole board, never by "all members". | An open pool's members are strangers; a shared board's contents go only to devices a person named. |
| M3 | A link never carries entries to a pool that is not already a participant of the source board. | Links are node-local and would otherwise route around the sharing charter. |
| L1 | Leases stay per device. The GPU is one holder at a time across all pools; pools share it by least-recently-served first. | AGENTS.md hard floor; fairness without a second lock. |
| L2 | Inbound work (tests, model calls) is off for every pool until the device's owner turns it on for that pool, with safe limits. | Being in a pool is not being allowed. |
| T1 | Devices on different networks reach each other over the person's tailnet (Tailscale); we run no relay. The tailnet is a route, never an authority. | Owner decision 2026-10-11. Pinned device certificates stay the only authority, so tailnet membership, tags and ACLs authorise nothing. |
| S1 | A device offers named models to named pools; the default is none. A call goes over the existing pinned member connection, takes a `model_slot` and a `gpu` lease on the owning device, and is metered per pool and per member. | The serving device decides what leaves it; callers cannot see another pool's traffic. |
| S2 | Prompt caches are never shared across pools unless the serving device's owner declares it. The serving device sees prompts in the clear. | Isolation by default; the limit is stated, not hidden. |

## 1. Who governs a pool

### 1.1 What is true today (verified)

The record is `pool.json` (`membership.rs`): a pool id (8 random bytes), the join policy with a time and a name, the project key,
and one row per device (fingerprint, certificate, name, `active` or `revoked`, time, `by`). Nothing in it is signed. There is no
founder, owner, admin or pool key: `by` is a label. The first device to open a state directory made the pool and is a member like
any other.

Authority sits in two places that do not agree:

* **The local API** (`poolapi.rs`) needs a grant for `member_remove`, `set_join_policy`, `add_device` and `join_pool`, and a grant
  counts only when a person key enrolled on that device signed it. This guards one door.
* **The member protocol** (the module that serves member requests, op `members`) takes rows from any active member and merges them
  (`Pool::merge`): every device row whose certificate hashes to its fingerprint is added as it is, every removal is applied and is
  never undone, and a policy is taken when its `(time, by)` is newer than the held one. No grant, no signature. That the sender is
  an active member is the whole check, and `docs/node.md` says so: "only an active member's rows are merged".

Checked on 2026-10-11 with a throwaway test in a scratch copy of the tree (three real nodes joined by a join code, one plain member
with no grant; the test was not committed):

| What the plain member sent to another member | What happened |
|---|---|
| One `members` request with a row `{fingerprint: the founder, status: revoked}` | The receiver listed the founder as removed and refused the founder's next handshake (`HandshakeFailure`). The record spreads on to every member. |
| One `members` request with the certificate of a device no join ever approved, status `active` | The receiver listed it as an active member; that device then called `boards` on the receiver and got the answer. No code, no policy, no person. |
| One `members` request carrying `policy: open` with time `18446744073709551615` | A secure pool became open. No later change can be newer, so the policy is frozen at the attacker's value; in a debug build the pool's own `set_join_policy` then panicked at `poolops.rs:70` (`policy_at + 1` overflows); in a release build the addition wraps and the change is refused (read, not run). |

So today any one member (or any one stolen member device) can take the whole pool: remove the founder, enrol its own devices, fix the
policy. `docs/node.md` says it by design: "newest change wins", "revoked wins", "only an active member's rows are merged". The first
slice closes it; nothing else in this document is safe to build on until it does.

### 1.2 Principles

1. Authority belongs to people, and a person is a key. A device never governs: it holds data, runs work and carries a delegation.
2. Every change to who is in a pool, who governs it or what it allows is a signed act. A member verifies each act itself, from keys
   it already holds. A member that relays an act proves nothing about it.
3. A member device can leave itself, and nothing else alone.
4. No takeover by presence: more members, more time, a copy of the files or a cloned device key give no authority.
5. The signature of a person is one of the existing person keys (`docs/node.md`, "Standing grants": Ed25519, kept in the keystore,
   never readable by an agent). The way a person gives it, on a phone or a screen, is `docs/phone-approval.md`'s, not this document's.
6. Security is invisible in the normal case. Section 1.12 lists every protection and what the person feels.

### 1.3 The admin set

An **admin** is an Ed25519 public key named in the pool's charter, with a label (`"the owner's phone"`), the person it belongs to and a
role. There are two roles:

* `admin`: signs routine acts alone and takes part in sensitive acts.
* `recovery`: a key kept offline (a phrase the person writes down, or a second phone put away). It takes part in sensitive acts and
  can sign one time-locked act alone (section 1.8). It never signs routine acts, so it stays offline.

A **threshold** is how many distinct people must sign. Two classes:

| Class | Acts | Default | Choose at creation |
|---|---|---|---|
| `routine` | issue a ticket, remove a member, cancel a pending act, end a delegation | 1 | 1..n |
| `sensitive` | add or remove an admin, change a threshold, reissue the pool, sign a board charter side | 1 while the set holds one person; 2 once it holds two or more people | 1..n, counted in people or in keys |

Counting people, not keys, means one person with a phone and a laptop signs a sensitive act with either: the normal case needs one
tap, and a stolen phone is as dangerous as that person's own approval. The owner may choose "two keys of mine" at creation or later
(`sensitive: {m: 2, unit: "key"}`); the risk of the default is written in 1.11. Adding the first key of another person asks one
plain question in the same approval ("Should changes like this need both of you?", default yes), because a pool with two people
should not let one of them take it.

The founding approval enrols **every key the person approves with at that moment**: the phone's key and, when the screen path is
used, the keystore key of the device the app is on. Two keys of one person need no extra step and mean the loss of one phone does
not lock the pool.

A **delegate** is a device named by an admin-signed act that may sign routine acts of listed kinds for a time. The founding approval
creates one for the founding device: kind `ticket` (admit one device), 30 days. The delegate signs with the device key, and its signature is a device signature shown as one ("by the delegate of
the owner's phone"): nothing is signed in a person's name, so the floor that an agent never acts as a person holds. It cannot sign a
sensitive act, a removal or anything not listed, and any admin ends it at once. This is how "add my laptop" needs no new tap after the first approval. The
delegate and the standing grants of the first-run bundle renew together: any approval the person gives in their last 14 days
carries the renewal in the same tap, and otherwise one nudge asks once, never in the middle of a task; a person who uses Poolhouse
at least monthly never sees it.

### 1.4 The charter and the pool id

`pools/<id>/charter.json` holds the current charter, signed by the admins of the previous epoch (the founding charter by the
founder's key):

```
{ v: 1, epoch, name, policy: "open"|"secure", mode: "admins"|"members", project: "" | <project key>,
  admins: [{key, label, person, role}],
  thresholds: {routine, sensitive, unit: "person"|"key"},
  recover_delay_days (1..30, default 7), succession: {quorum, delay_days} | null,
  from: null | {pool, handoff_hash}, at_ms }
```

The **pool id is the first 16 bytes of the SHA-256 of the epoch-1 charter body**, 32 hex digits. A copy of a pool with different
admins therefore has a different id, and a stranger cannot make a pool with this id and other admins. The policy is part of the
charter body and does not change inside a pool. The epoch rises when the admin set or a threshold changes; a reissue makes a new
pool, not a new epoch.

A pool has no key of its own and no secret shared by its members. Nothing in a member's files can be copied to govern the pool.

### 1.5 Acts

Every act is `{v, pool, epoch, kind, body, at_ms, nonce, prev, signatures: [{kind: admin|device, key, sig}]}`, signed over
`poolhouse/pool-act/v1` and a line break and the canonical JSON of everything but the signatures (the same canonical form as
grants). `prev` is the hash of the latest admin-set act the signer knew; it orders admin-set changes. Acts replicate in the `acts`
member op (`{pool, have: [act hashes]}` answered with the acts the asker lacks, at most 4,096 acts per pool, 64 KiB each) and are
kept in `pools/<id>/acts.jsonl`. The member record (`roster.json`) is a view folded from the acts; it is not an input and a `members`
row is never believed again.

| Kind | Signed by | Effect | Replay |
|---|---|---|---|
| `found` | the founding admin keys | the epoch-1 charter | the id is its hash; a second `found` for an id is refused |
| `ticket` | one admin, or the delegate | lets the named device admit one device within 5 minutes (secure pools) | single use; a second admit on one ticket is refused; expired by its own times |
| `admit` | secure: the device named in the ticket, or an admin; open: any active member's device key | adds the certificate as active | idempotent; refused for a removed certificate |
| `remove` | one admin | the certificate is `revoked`, sticky | idempotent; wins every merge |
| `leave` | the device itself | status `left`, which a later `admit` of that certificate may undo | idempotent |
| `admin_add`, `admin_remove`, `set_threshold` | sensitive | epoch + 1 with the new admin set | an old epoch's act is no-op |
| `delegate`, `delegate_end` | routine | names or ends a delegate | idempotent |
| `recover` | the recovery key alone | time-locked admin replacement (1.8) | one pending at a time |
| `succession` | members' owner keys | time-locked admin replacement (1.8), only `mode: members` | one pending at a time |
| `cancel` | one live admin | ends a pending `recover` or `succession` | idempotent |
| `handoff` | sensitive | reissues the pool (1.9) | nonce used once; the old pool is retired after it |
| `board_side` | sensitive | one pool's side of a shared board's charter (section 6) | versioned by the board charter |

How a member accepts an act: the pool and epoch are its own; every signature verifies; the distinct people (or keys, under
`unit: key`) among the signers that hold an admin key of that epoch reach the class's threshold; a `device` signature is accepted
only where the table says so (a `ticket` only from a device a live `delegate` act names for that kind); `at_ms` is not more than
five minutes ahead of its clock. Anything else is refused and recorded with the reason, never applied. Heartbeats (1.6) travel in
the same exchange and are not stored.

**Removing a key carries a cut.** An `admin_remove` lists the hashes of every act by that key the remover has seen; an act of that
key that is not in the cut is void on every device, whenever it arrives. A stolen key therefore cannot keep a signed act in a drawer
and release it after the key is removed. **A fork** (two admin-set acts with the same `prev`) is settled by the lower hash, and
the member record is refolded.

The last admin cannot be removed, nor the set left below the sensitive threshold; ending a pool's governance is a reissue.

### 1.6 Joining, leaving and removing

* **Secure pool.** The person asks the agent to add a device. The node issues a `ticket` signed by the delegate, opens the join
  window (a six-digit code or a link, five minutes, three tries; SPAKE2 as today), and the device that held the window signs
  `admit` for the one certificate the code proved. The person did nothing but say it and read or hand over a code.
* **Open pool.** A device on the same network segment is admitted by whichever member heard it, with that member's device key, as
  today. Nobody is asked. This is what "open" means, and `docs/sentinel-open-join.md` still applies. An open pool is joined only from a device on the same network segment, never over a tailnet (4.4). An open pool is the lowest
  trust level: it cannot be members-governed (1.8), its devices are never carried over or shared with another pool without a
  review (1.9, 6.2), and an offer to it needs a plain warning (7.2).
* **Removing a member** is an `admin` act: one tap, plain words naming the device ("Remove 'guest-laptop' from 'home'? It stops
  working with every device in the pool at once."). It is sticky: a removed certificate never returns; a device comes back with a
  new certificate, which is a new identity in every pool it belongs to. That is why it is a tap.
* **Leaving** is a `leave` signed by the device itself and needs no admin and no tap beyond the owner's wish to leave. A left
  device is gone from the roster and can be admitted again by a ticket.
* **A copied device.** Every device signs a heartbeat `{fingerprint, boot id, counter}` every minute with a counter it persists
  before sending. A member that sees two boot ids alternate under one fingerprint, or a counter that goes backwards, has seen a
  clone: it flags it to the admins ("Two devices answer as 'guest-laptop'; it may have been copied. [Remove it] [It's fine]") and the
  fingerprint is excluded from new offers and shards until the person answers. A person can tell which copy is real; no code can.
  Device keys move into the operating system's keystore where one exists (`docs/pool-encryption.md`, slice 3), so a file copy alone
  carries no identity; a copy of a pool's files without a device key is a stranger.

### 1.7 What a member still decides alone

A device's owner decides what that device does: whether it takes tests or model calls from a pool (section 5), what it offers
(section 7), what its sessions may do (grants, unchanged). These are authorised by a grant that a person key enrolled on
that device signed (`persons.json`), not by the pool's admins. An admin cannot offer someone else's device; the pool's removal of a
device ends all of it at once. Two different decisions, two different keys, usually one person. The owner may also set a pool to
`follow: ask`, so that every move of that pool (1.9) waits for the owner's tap instead of being followed silently.

### 1.8 Backup, recovery, succession and total loss

**Backup is a nudge, not a setup blocker.** After the first successful use, the agent says once a week, in one sentence, "Your pool
has one key; add a backup so losing this phone does not lock you out", with three plain choices: another phone of yours, a trusted
person's phone, a recovery phrase to write down. Risk written down for the owner: until a second key exists, losing every key the
person approved with freezes the pool (it keeps working and nobody can change it; see total loss below). The founding approval already
adds the keystore key of the device the app is on when there is one, so the common case has two keys at once with no step.

**The recovery key** is an admin key of role `recovery`. Its holder alone can sign `recover {new admin set}`, which takes effect
after `recover_delay_days` (default 7). Every device and phone shows a banner with the countdown ("Pool 'home' will get new admins
on Oct 18 unless a current admin cancels it") and any live admin cancels with one tap (`cancel`). Enough admins to meet the sensitive threshold replace the set at once as a
sensitive act, with no delay; the recovery key alone is the only time-locked path. A stolen recovery phrase lets a thief start a
countdown that live admins cancel; if no admin is alive it is the total-loss case below, and a phrase kept like a password is
protected like one.

**Members-governed mode** (chosen at creation, `secure` pools only, off by default). If every admin key is gone, a `succession`
signed by at least two thirds of the active members' owner keys (and at least three of them) appoints a new admin set after
`delay_days` (default 30). It replicates at once; every device and phone shows a banner and a countdown; any live admin cancels with
one tap; members may withdraw their signature, and falling below the quorum cancels it. The owner keys are the person keys that
members presented when they were admitted; a device with no owner key counts for nothing. The mode exists so a pool of friends
outlives its founder; the default stays admins plus a recovery key because a majority of members is the group an attacker joins.

**Total loss** (no admin key, no recovery key, not members-governed): the pool keeps working unchanged. Members keep syncing,
serving and testing; in a `secure` pool nobody can admit a device; in an `open` pool devices keep joining by themselves, since open
admission never needed an admin. Nobody can remove a member, change an admin or reissue. The plain message to a member's agent is
"This pool has no admin left, so it can't be changed. You can start a new pool and join it; your boards can come with you." A
successor is a new pool made by a new admin; each member device joins it by its owner's own approval; each device owner may move its
local copy of a board to the new pool (`board_rebind`, a device-owner act, 6.8). Members can never take the old pool over by being
there.

### 1.9 Changing the join policy is a reissue

`set_join_policy` is removed. The node answers it with `invalid: a pool's join policy never changes in place; reissue the pool to
change it`, and no grant can name it. To go from open to secure (or the reverse) an admin reissues the pool.

**The agent flow** (the owner says "make this pool secure"):

1. The agent calls `pool_reissue {pool, policy: "secure", keep: "reviewed"}` (`ph.pool.reissue(...)`). The node builds a draft: the
   new charter (same admins unless the agent names others, same thresholds, `policy` as asked, `from` the old pool), and a **review
   list**: every active device with its name, last seen, how it joined (`open`, `join-code`, `ticket`, `self`, `handoff`) and
   whether this node can reach it.
2. The draft becomes one approval on the person's phone or screen: "Make 'home' secure? Keep: Desktop, Laptop, Phone (joined by
   code). Decide: 'laptop-77' joined 3 days ago without a code. [Keep] [Drop]". A device that joined while the pool was open must be
   decided by the person; the rest are pre-selected from the list. "Keep all" is one button. The person's edits are made before the
   signature, and the signed bytes are the final list.
3. The admin quorum signs the handoff (sensitive threshold). Secure to open is the same with `downgrade: true` in the signed body,
   the plain warning "Anyone on the same network will be able to join", and the same review.
4. The node writes the handoff as an act of the old pool and founds the new pool from it (its `found` is the handoff's embedded
   charter; the id is that charter's hash). The old pool is marked `moving`: it admits nobody, issues no tickets and takes no
   new offers, and it goes on syncing and serving for members that have not followed yet until each has.
5. The act replicates through the old pool's `acts` exchange to every member. Each kept device follows without any person.
   A dropped device never follows and is told, in the old pool, "this pool moved and you were not kept".

**The handoff** (`kind: handoff`, signed over `poolhouse/pool-handoff/v1`):

```
{ old: {pool, epoch, charter_hash},
  new: {charter: <the full new charter body>, pool: <its hash>},
  keep: [{fingerprint, name}...],          // every kept device, this device included
  carry: {boards: [board ids] | "all", offers: [{device, model}...]},
  downgrade: bool, nonce: 16 random bytes, at_ms, expires_ms (default 14 days after at_ms),
  signatures: [admin signatures of the old epoch] }
```

**A device follows if and only if** (each is a test, section 1.13):

* R1. The handoff reached it inside the old pool's authenticated `acts` exchange, from a member (who relays; who relays proves
  nothing), and the device verifies everything itself. A beacon, a name, an id, a connection from a non-member, or another pool
  using the old pool's name moves nobody.
* R2. The signatures verify under the admin keys of the old pool's current epoch **as this device already stores them**, and
  reach the sensitive threshold.
* R3. `old.pool`, `old.epoch` and `old.charter_hash` equal what this device holds. A handoff from an older epoch, or for a copy
  with another charter, is refused.
* R4. `keep` names this device's fingerprint.
* R5. `new.pool` is the hash of `new.charter`; the old pool is not already retired; the nonce is unused; `at_ms` is not ahead of
  the device's clock by more than five minutes; `expires_ms` has not passed.
* R6. `new.charter.admins` equals the old epoch's admin set: the device follows silently. If the admin set differs, the old quorum
  did sign it and the handoff is valid, but a rotation changes who governs: the device holds the move until its own person approves
  ("Pool 'home' moved to a new pool managed by 'the friend's phone' instead of 'the owner's phone'. Move this device?"), a sensitive approval with
  that warning.
* R7. `downgrade` matches the charters: a handoff to a weaker policy without the flag is refused, and a device whose own setting is
  `ask` for downgrades holds it like R6.

A device that follows (all done by the node, no person):

* creates `pools/<new>/` with the charter and a roster of the `keep` list (`by: handoff`);
* rebinds every board the handoff carries from the old pool to the new one (board history is untouched; a board shared with other
  pools asks those pools, 6.8);
* ends every lease and shard of the old pool with the plain error "pool moved", withdraws every offer it made to the old pool, and
  re-makes exactly the offers the handoff lists whose model it still offers (never an offer the handoff does not list, never a
  new one);
* moves `pools/<old>` to `pools/retired/<old>` (kept for the record, never read for authority). For 90 days it still answers the
  old pool's `acts` requests with the signed handoff and nothing else, so a device that missed it can fetch it (the dropped ones
  learn they were not kept this way), and answers every other op of the old pool with "this pool moved";
* dials the other kept devices at the addresses it already knows and exchanges the new pool's acts.

**The device that missed it.** Within `expires_ms` every kept device follows as soon as it reconnects. After it, or when the person
has set the device to `ask`, the device shows the plain step "This pool moved. Approve the move?" on the owner's phone or screen.
That approval is made by a person key enrolled on **this device**, never by an agent, shows the new pool's admins, and is a
sensitive approval with a warning if they differ from the old pool's. The signed handoff is fetched from any kept device and checked
by R2..R7 exactly as above; the approval adds consent, not authority.

**What carries over, in one line each:** members (the keep list); boards and their history (rebound, so a board id and its entries
stay; shared boards continue by 6.8); standing grants (scoped by pool id, so a grant for the old id stops counting and the first-run
bundle's `admin:<key>` scope covers the new pool at once); tickets and delegates (reissued by the new charter's founding act under
the same tap); leases (ended, then queued again by their holders); served models and their offers (only those the handoff lists);
quotas and consents (per pool, so they must be made again: a pool the owner has not allowed takes no work, which is the safe
direction); the pool's name (same). **Pool id continuity:** the id does not continue (a new charter is a new hash), and the new charter's
`from` names the old pool and the handoff, so `pool_status` shows the lineage and a board's history stays whole across it. The old
pool is retired for good: a retired pool cannot be re-activated, a copy of it moves
nobody, and an act for it is refused by every device that followed.

**Person feels:** "make this pool secure", one approval with a review list whose defaults are already right. Reason it is visible:
the list is the whole point, since an unreviewed open joiner is exactly what must not be carried.

### 1.10 Pool copy and takeover

**Do devices ever migrate into a copy of a pool? No.** A copy of a pool's files, a cloned member, a pool that reuses the name or the
id, or a beacon announcing a move has no admin signature, so it moves nobody. The seven rules:

1. A device follows a move only by R1..R7: the handoff is signed by the old pool's admin quorum with keys the device already holds,
   and it contains the new pool's id and charter (so its admin set and policy), the exact kept list including this device, a nonce,
   an expiry and the old pool's epoch.
2. The new pool's admin set must be the old one or a rotation signed in the same handoff by the same old quorum; a handoff that names
   other admins than the quorum signed is a different byte string and its signatures do not verify. A rotation is held for the
   device's own person (R6).
3. A device never follows a move announced by a non-admin, by a beacon (beacons are signed only by device keys and say a pool exists,
   not that it moved), by a name or an id, or over any connection but the old pool's own authenticated member channel.
4. The "approve the move" step is the person's, on this device, shows who the new admins are, and is a sensitive approval when they
   differ.
5. A member that copies the pool's files or its own device key to another machine gets a clone, which is flagged to the admins within
   a minute (1.6). A copy of pool metadata without an admin key can never govern: it cannot sign an act, a ticket or a handoff.
6. **Compute never goes to an attacker's pool.** A device offers models and takes tests only for pools its own owner approved, per
   pool and per device. A new pool's offers and consents are empty until that owner's grants make them again (for the owner's own
   pools the first-run bundle already covers a pool the owner's key administers, so the agent re-offers without a tap; for any
   other pool it is one tap), unless the signed handoff lists an offer the device already had. An offer is never created by a copy,
   a handoff that does not list it, or the other pool's admins.
7. Each case above is a test that leaves every device in the original pool (1.13).

### 1.11 Takeover analysis

| Attacker | What it can do | What stops the rest |
|---|---|---|
| A malicious or compromised member device | Read what the pool's boards hold, call models offered to the pool, leave. It can no longer add devices, remove members, set a policy, forge a handoff or enrol a stranger. | Section 1.1's three holes become tests that must fail: each is an act it cannot sign. |
| A member that relays forged or stale acts | Nothing: acts verify by signature and epoch, not by sender. | R1..R7. |
| A stolen phone (unlocked by its biometric) | Everything its admin key allows: with the default (sensitive threshold 1 for one person) it can reissue the pool and keep only the attacker's devices. | Two keys of the person for sensitive acts, if the owner chooses "two keys of mine"; any live admin removing the stolen key at once, with a cut that voids its unseen acts; a rotation handoff is held for each device's person (R6), so a stolen key cannot quietly rotate admins on devices whose owners are watching. The default's risk: a stolen unlocked phone is a stolen pool for as long as the owner does not notice; written for the owner in section 13. |
| A stolen laptop | Its keystore key signs only after the person's screen approval, which an agent cannot give. A thief at a logged-in screen is the same as a stolen unlocked phone. | As above, plus OS disk encryption (`docs/pool-encryption.md`, section 3, out of scope). |
| A stolen or lost recovery phrase | Starts a time-locked `recover`. | Any live admin cancels; the banner is on every device and phone. If no admin is alive, it is the total-loss case and the phrase is the pool's last key: keep it like a password. |
| A rogue co-admin | Removes members, adds admins up to the sensitive threshold. | Another admin removes it (with a cut); a two-person pool needs both people for sensitive acts. |
| Someone on the network under `open` | Joins an `open` pool. | That is what open means; it cannot be members-governed, cannot be shared or offered to without a review and a warning, and a reissue to secure drops it unless a person keeps it. |
| A majority of members in members-governed mode | Appoints new admins after 30 days. | Notice on every device and phone from day one, any live admin cancels, only `secure` pools allow the mode. |
| A cloned device key | Is a member as the original is. | Heartbeat clone detection, one-tap removal; a copy of a pool's files alone is a stranger. |
| A member that reissues nothing but stops answering | Nothing: its absence changes no signature threshold. | Admin acts do not need members to be online. |

### 1.12 What the person feels (security without friction)

| Protection | Normal case | A visible step, and why it cannot be invisible |
|---|---|---|
| Admin acts are signed, members verify them | Nothing | none |
| Routine acts (admit a device) by the delegate | Nothing: "add my laptop" needs no tap after the first approval | none |
| Sensitive acts (admin change, reissue, board side) | One tap with plain words | Irreversible or a disclosure; a signature a person did not see is the thing being prevented |
| Removing a member | One tap | Sticky and takes a device's identity from the pool |
| Handoff verification, following a reissue | Nothing: kept devices move by themselves | Only when the admins changed (R6) or the device missed the window: a sensitive approval, because it decides who governs |
| Review of members | One tap with defaults already right | Open joiners must be chosen: they are the risk |
| Clone detection | Nothing | A flagged clone asks one tap; only a person can tell which copy is real |
| Backup admin | A weekly one-line nudge, never a blocker | Optional; the risk is written in 1.8 |
| Offers, calls, shards (per pool) | Nothing for the person's own pools (the first-run bundle names them); one tap for a pool someone else administers | Prompts leave the device, or foreign work comes in |
| Per-pool isolation, slot erase, limits | Nothing; first token is slower when pools alternate | none |
| Clock, nonces, expiry | Nothing | none |
| Time locks | Nothing until a recovery is pending, then a banner | A pending recovery is news the live admins must see |

### 1.13 Tests for governance (written first, each seen to fail)

Rust, `app/poolhouse-node/tests/governance.rs` and `reissue.rs`:

* the three holes of 1.1 as tests that fail today: a plain member's `members` request cannot remove the founder, cannot add a
  certificate and cannot move the policy;
* a member without an admin signature cannot remove, admit (secure), change admins or reissue;
* a forged act (wrong key, wrong pool, wrong epoch, bad signature, signature of a removed key) is refused and recorded; a replayed
  act is a no-op; a stale epoch is a no-op; a signature counted twice counts once;
* threshold enforcement: a sensitive act with one of two required people is refused; one person with two keys counts once under
  `unit: person` and twice under `unit: key`;
* the cut: an act of a removed key that was not in the cut is void on every device, an act in the cut stays;
* tickets: single use, five minutes, only the named device may sign the admit; an open pool admits by a member's device key and a
  secure pool refuses that;
* the recovery key alone is refused before its delay, applies after, and a live admin's `cancel` ends it; the same for
  `succession`, which also needs `mode: members` and a secure pool and falls when signatures are withdrawn;
* the last admin cannot be removed;
* reissue: a kept device follows with no person; a dropped device is refused at its next request in the new pool and told in the
  old; a forged handoff, a below-threshold handoff, a handoff naming other admins than the signers, a replayed handoff, a handoff
  for a copy of the pool with another charter, a handoff delivered by a beacon or from a non-member, a handoff naming no kept
  entry for the device, and a handoff past its expiry each leave every device in the original pool; the fallback approval with
  different admins is held for the person and warns; the old pool cannot be re-activated; a cloned device key is flagged; offers
  and leases of the old pool end on follow and only listed offers return.

Pool-check steps (`tests/pool_check/steps_governance.py`): `gov-plain-member-cannot` (the three holes against real devices),
`gov-open-to-secure-after-founder-removed` (a second admin reissues an open pool to secure after the first device was removed; the
kept devices continue and the dropped one is refused), `gov-handoff-forged`, `gov-pool-copy` (a copy of one device's files joins
nothing), `gov-clone-flagged`, `gov-recovery-timelock`.

### 1.14 Migration of existing pools

An existing pool has the legacy `pool.json`, a random id, no charter, and person keys enrolled per device. Migration is **the first
reissue**, started only by an explicit call, never by the node on start:

* A node that finds `pool.json` and no `pools/` directory starts in `needs_migration`: `hello` and `pool_migrate` answer; every
  other pool method answers "This device's pool needs a one-time upgrade; ask the owner to approve it." Boards, grants and sessions
  are untouched and keep working locally; nothing is deleted.
* `pool_migrate` (grant `pool_migrate`) builds a charter naming the person key that approves as the first admin (a person approval,
  never automatic), a handoff keeping every currently active member (the person can drop any in the review list), and signs it with
  that key. For this one handoff the old pool's authority is **the person keys enrolled on each device** (`persons.json`), which is
  what governed it. Each other device does the same call; it verifies the handoff against its own enrolled key, checks its
  fingerprint is kept, and follows with no further approval beyond the grant it already holds (one batch approval on the phone
  covers all the owner's devices).
* The old file moves to `pools/retired/<old id>/pool.json`. Boards are bound to the new pool. Grants that name no pool keep working
  for board-level actions (`task_authority`, `note_verify`, `land_*`, `link_*`, `source_add`, `file_delete`, `quarantine_release`);
  grants for pool-level actions stop counting and the first-run bundle replaces them in the same approval.
* Never touched by tests, by a worktree or by a build: every test uses a temporary state directory; the real home is migrated once,
  at a safe boundary, by the owner's approval (section 13).

## 2. Operated by agents, approved by a tap

Requirements. The mechanism (the enrolment of the person key by QR, the signed approval, the push, the phone and screen paths) is
`docs/phone-approval.md`, owned by another branch; this document does not design it and depends on it (slice P1).

1. **Agent-operated.** Every operation here (create a pool, add a device, offer a model, share a board, reissue, remove) is a node
   call a registered agent makes with its own token under a grant. The command line is for agents. The person never types a command,
   edits a file or opens a terminal.
2. **A tap approves.** The person's part is an approval given where a non-technical person already is (the phone, or the app's
   screen), showing what will happen in plain words, signed by the person key in the keystore, one approval per action, with an
   expiry. Nothing an agent can click. The floors stay: an agent never acts as a person, `grant_issue` and `person_enroll` stay
   person-only, `act_as_person` stays refused.
3. **No terminal.** The typed `yes` on `/dev/tty` remains a second signer for people who prefer a terminal. The first-run step of
   `grants-setup` that needs a terminal is removed: the first-run approval replaces it.
4. **First run offers a default bundle** of standing grants for routine pool, serve and board operations, scoped to pools the
   approving key administers (`pool: "admin:<key fingerprint>"`) and to this device, 30 days: `pool_create`, `add_device`,
   `join_pool`, `sync_now`, `pool_leave`, `pool_reissue`, `admin_manage`, `serve_offer` and `serve_call` for those pools, and
   `inbound_consent` for those pools. Never in the bundle: joining or offering to or sharing with a pool someone else administers,
   `board_share`, `member_remove`, `inbound_consent` for foreign pools. After setup "create a pool called home and add my laptop"
   needs at most one approval and routine repeats need none.
5. **Every refusal and every pending approval explains itself in one plain sentence** the agent can relay. The node's error carries
   `say` (at most 160 characters, no jargon: it names what is needed and where to tap), `needs` (`approval`, `grant`, `device`,
   `time` or `none`, with an id, `where` and `expires_in_s`) and `then` (`wait`, `retry`, `ask-owner`, `stop`). A waiting approval is
   not a failure: `ph.NeedsApproval` carries the same fields and a library call with `wait=True` blocks until the tap or the expiry.
6. **No step in an owner's document says "run".** Section 3 and the "What to tell your agent" block are written for the owner.

**What to tell your agent** (every sentence works as written):

* "Make a pool called home." / "Make a pool called home that my devices on this network join by themselves."
* "Add my laptop to home." / "Add a device that is on another network."
* "Offer the small model to home." / "Stop offering models to the friend's pool."
* "Share the board for this project with the friend's pool, read only."
* "Make home secure." / "Show me who can use this device's model."
* "Add my second phone as a backup for home."
* "Join the pool my friend sent me."

Refusal examples the node must be able to say, each one sentence:

| Situation | `say` |
|---|---|
| a grant is missing | "I need your OK to add devices to 'home'; check your phone." |
| phone offline | "Your phone didn't answer. The question is on your screen and your phone will show it when it's back." |
| approval expired | "That approval ran out. Tell me again and I'll ask once more." |
| device unreachable | "I can't reach 'guest-laptop' from here. Is it on, and on the same Wi-Fi?" |
| wrong join code | "That code didn't match. Ask the first device for a new one; they last five minutes." |
| open pool and an offer | "'home' lets anyone on this Wi-Fi join, so I'll only offer the model if you OK it on your phone." |
| two pools | "This device is in two pools, home and work. Which one do you mean?" |
| clone | "Two devices answer as 'guest-laptop'. It may have been copied. Check your phone." |
| model busy | "The model is busy with a friend's request. You're number 2 in line, about 40 seconds." |
| private message on a shared board | "This board is shared with a friend's pool, so private messages would be stored on their devices. Use a board of your own pool." |
| pool moved | "This pool moved to a new one. Tap to approve the move on your phone." |
| other network, Tailscale missing | "To reach your other network I need Tailscale on this device. It isn't installed; get it from tailscale.com, sign in, and I'll carry on when it's up." |
| Tailscale signed out | "Tailscale is installed here but signed out. Open it and sign in; I'll carry on when it's up." |
| Tailscale on, no route | "Tailscale is on for both devices but isn't letting them talk. Check the access rules in your Tailscale account." |
| a different device answered | "Another device answered at that address, so I ignored it. If you use a Tailscale exit node or subnet router, tell me which device you meant." |

## 3. The first ten minutes

Onboarding is a first-order property: it is measured, budgeted and gated. A **human step** is one of: `install`, `os-prompt` (the
operating system's own one-time prompt), `link-phone` (scan once so the person key exists), `say` (a plain sentence to the agent),
`tap` (an approval), `tailscale` (install Tailscale, sign in to it: two steps, each counted; the system's one-time VPN prompt is an
`os-prompt`), needed only on a device that must reach another network and never for a same-network join. A **terminal step** (a typed command, a file edited, a key handled, a port or firewall named) is zero in every
journey, and a journey that produces one fails its check. Every `link-phone` and `tap` is a security step and is counted in the
same budget: in the normal case there is no other, so security adds no step the budget does not already show.

### 3.1 The owner on one device (journey 1)

| # | The person sees and does | The agent does | What can go wrong, in the words shown |
|---|---|---|---|
| 1 | Installs Poolhouse. The system asks once whether it may find devices on the network (`os-prompt`). | Nothing yet | "Poolhouse needs to find your other devices. Please allow it." |
| 2 | Opens the app: "Link your phone" with a code. Scans it with the phone app (`link-phone`). | Enrols the person key; waits | "The phone can't be reached from this screen's network. Open the phone app and try again." |
| 3 | Says "Make a pool called home" (`say`). | `pool_create`; an approval reaches the phone | "I need your OK to make the pool; check your phone." |
| 4 | Taps once on the phone: "Create pool 'home' (secure) on this computer. Your agents can add devices and use its models for 30 days. [Approve]" (`tap`; the same approval carries the default bundle of standing grants, listed in plain words on the screen) | Signs the charter, the delegate for tickets and the grants; the pool exists | "That approval ran out; tell me again." |
| 5 | Says "Serve a small model for my pool" (`say`). | The broker takes the lease (`poolhouse-serve up`), then `serve_offer` to `home` under the bundle | "There isn't room for that model; I'll pick a smaller one." |

Budget: install 1, os-prompt 1, link-phone 1, say 2, tap 1 = 6 human steps, 0 terminal steps. A later "add a backup" nudge is one
sentence and one tap, optional.

### 3.2 The same owner adds a second device (journeys 2a, 2b, 2c)

**2a: same network, the pool is open** (the owner said "that my devices on this network join by themselves", and the approval said
"anyone on this Wi-Fi can join").

| # | The person sees and does | The agent does | What can go wrong, in the words shown |
|---|---|---|---|
| 1 | Installs Poolhouse on the second device; allows network discovery once (`install`, `os-prompt`) | Starts the node; it hears the pool's signed beacon | "I can't see 'home' from here. Are both devices on the same Wi-Fi?" |
| 2 | Nothing. The first device's phone says "'guest-laptop' joined 'home'. Review?" | The node admits the device by an open `admit`; the first device's agent offers the review | "'guest-laptop' joined without a code. Keep it?" |

Budget: 2 steps, 0 taps, 0 terminal.

**2b: same network, the pool is secure** (the default).

| # | The person sees and does | The agent does | What can go wrong, in the words shown |
|---|---|---|---|
| 1 | Installs; allows discovery once (`install`, `os-prompt`) | Starts the node | as 2a |
| 2 | Says "Join my pool called home" on the new device (`say`) | Knocks on the pool it heard; shows three words in the chat | "I can't see 'home' from here. Are both devices on the same Wi-Fi?" |
| 3 | The first device's phone shows "'guest-laptop' wants to join 'home'. Does it show maple-river-ten?"; compares with the new device's chat and taps (`tap`) | The first device issues a ticket under the delegate; the new device is admitted when the tap arrives | "The words don't match, so I stopped. Someone else may be knocking." |

Budget: 4 steps (install, os-prompt, say, tap), 0 terminal. Reason for the visible tap: a stranger on the Wi-Fi must not join, and
only the person can see two screens agree.

**2c: another network** (over the person's tailnet; 4.4). Tailscale is a human step: it is installed and signed in by the person,
once per device, and the agent never does it for them.

| # | The person sees and does | The agent does | What can go wrong, in the words shown |
|---|---|---|---|
| 1 | Says "Add a device that is on another network" on the first device (`say`) | Reads whether Tailscale is up here. If it is: issues a ticket under the delegate (no tap), opens the join window, shows a link and a QR carrying the tailnet address. If not: says the sentence of the next row | "To reach your other network I need Tailscale on this device. It isn't installed; get it from tailscale.com, sign in, and I'll carry on when it's up." |
| 1a | Only where Tailscale is missing here: installs it, signs in, allows its VPN prompt once (`tailscale` twice, `os-prompt`) | Reads the state every 30 seconds and goes on from row 1 by itself when it is up | "Tailscale is installed here but signed out. Open it and sign in; I'll carry on when it's up." |
| 2 | On the second device installs Poolhouse and allows discovery once (`install`, `os-prompt`) | Starts the node | as 2a |
| 2a | Only where Tailscale is missing there: installs it, signs in to the same account (or accepts the shared device), allows its VPN prompt once (`tailscale` twice, `os-prompt`) | Reads the state; says the same sentences | "Tailscale is on here, but 'home-desktop' isn't answering on it. Is Tailscale on and signed in there?" |
| 3 | Says "Join this pool" and pastes the link, or scans the QR (`say`) | `join_pool` with the tailnet address, the first device's certificate fingerprint as the pin and the code from the link; nothing to type | "That code didn't match. Ask the first device for a new link; they last five minutes." |

Budget: 4 steps when Tailscale is already on both devices (say, install, os-prompt, say), 0 taps, 0 terminal; 10 from nothing (those
four, and install, sign-in and os-prompt for Tailscale on each device). Both numbers are tracked (3.4). Honest limit: Tailscale is
not ours, and the person's account, access rules and sharing are theirs; when it cannot reach, the agent says what blocks and where
to tap and never changes Tailscale's settings.

### 3.3 A developer embedding the library (journey 3)

| # | The developer sees and does | The library does | What can go wrong, in the words shown |
|---|---|---|---|
| 1 | `pip install poolhouse`; once per person, scans the phone link (`install`, `link-phone`) | | "Poolhouse needs your phone once, to know it's you. Scan this." |
| 2 | `import poolhouse as ph` then `ph.pool.create("lab")` | `pool_create`; raises `ph.NeedsApproval` with `say`, or waits | "I need your OK to make the pool; check your phone." |
| 3 | Taps once (`tap`) | The founding charter, delegate and bundle are signed; the call returns the `Pool` | "That approval ran out; call again." |
| 4 | `ph.serve.offer("Qwen3-0.6B", pool="lab")` | Covered by the bundle; no tap | "There isn't room for that model; try a smaller one." |
| 5 | `ph.client.Client(ph.serve.connect("Qwen3-0.6B").base_url).chat([...])` | The relay opens on loopback and the call goes to the offering device | "The model is busy; you're number 2 in line." |

On a second device: `ph.pool.join_link(link)` (one line). Budget: install, link-phone and tap, 3 human steps and 3 lines of
code. A call that needs an approval and cannot get one raises `ph.NeedsApproval` with `say`; `wait=False` returns at once. Nothing
in the library reads a terminal.

### 3.4 Acceptance, gated

* **Step ledger.** Where the product asks the person for anything, it writes `{journey, kind}` to a ledger file when the
  check names one in the environment (the variable is added, and documented, with slice 6), and does nothing otherwise. A step that would read `/dev/tty`, print a
  command for the person to type or name a port, a file or a key writes `terminal` first, and the journey fails on it.
* **`scripts/cold-start-check`** gets a step `onboarding` that, in a fresh venv with a temporary home, drives journeys 1 and 3 as the
  agent through the library with the rig's in-memory person key as the approver and no terminal, and fails when a journey's counted
  steps exceed `tests/onboarding-steps.json`, when a `terminal` step appears, when a refusal in the journey has no `say` or has a
  `say` over 160 characters, or when a person-facing string contains "run", "terminal", "config", "port", "firewall" or "key".
* **`scripts/pool-check`** gets steps `onboarding-owner-one-device`, `onboarding-open-join`, `onboarding-secure-join`,
  `onboarding-other-network` (a second Docker network with no route to the first, joined only by a third network in the tailnet
  range, with a stand-in `tailscale` command that prints the status of that network; section 10), `onboarding-away-from-home` (the stand-in in the states
  missing, signed out and up: the agent's sentences and the 0 or 6 steps of 3.5) and `onboarding-developer`, each counting its
  steps against the same file.
* **The metric** `onboarding-steps` is the sum of the budgets of journeys 1, 2a, 2b, 2c, 3 and 4 (3.5), counted twice: with
  Tailscale missing on the devices that need it (6, 2, 4, 10, 3 and 6, for 31) and with it present (6, 2, 4, 4, 3 and 0, for 19).
  Both are ceilings in `scripts/gates` that only fall. Every slice below that adds a person-visible step must justify it in the line
  "a visible step because ..." (1.12) or it is refused.

### 3.5 Away from home (journey 4)

A person approves from the phone while away from home, or reaches a device on another network, over the tailnet: the phone path is
already HTTPS over the tailnet (`docs/pool.md`, 2.5; the mechanism is `docs/phone-approval.md`). At home on the same network the phone
needs nothing (journey 1, row 2). Away from home the phone and the device must both be on the tailnet: Tailscale on the phone
(`tailscale` twice, `os-prompt`) and on the device (the same three where it is missing), 6 steps from nothing and 0 where it is
present, once per person and device. The agent says it when the person first asks for something that needs it ("I can reach your
phone at home but not away from home. Install Tailscale on your phone and sign in; I'll carry on when it's up."). Until then the
approval waits on the screen until the phone is reachable (`docs/phone-approval.md`).

## 4. One device, many pools

### 4.1 Identity: one device identity, authority per pool

**Decision: a device has one key, one certificate and one fingerprint, shown to every pool it belongs to.** Each pool's record
lists that certificate; every request names the pool it is for, and the node checks that pool and that certificate together, never
"is this certificate known anywhere". A dial pins the fingerprint the target's pool roster holds, as today, and a certificate that
differs from the roster entry of the pool the dial is for is refused.

What is per pool and what is per device:

| Per pool | Per device |
|---|---|
| charter (id, name, admins, policy), acts, roster, discovery setting, inbound consent and limits, the boards bound to it, offers made to it | the key and certificate, person keys and grants (scoped by pool), the lease table, the serve broker and its models, `projects.json`, sessions and tokens |

Why, security first:

* **A handshake cannot know the pool.** In TLS 1.3 the server shows its certificate before the client shows its own, so a server
  with a certificate per pool could choose one only from the name in the ClientHello, which is sent in the clear and would publish
  the pool id to a passive observer. One certificate needs no such name.
* **Separate certificates would hide nothing that matters.** A device's addresses, its listener port and its beacons are the same for
  every pool, so members of two pools on one network can already tell it is one machine. A fingerprint per pool would add a key per
  pool to protect and a handshake per pool, and protect nothing against that.
* **A compromised device key compromises every pool of that device either way**: the key lives on the device.
* **Removal stays per pool and complete**: an admin's `remove` is in that pool's roster, so a removed certificate is refused there
  and nowhere else. The cost, written down: a device removed from one pool cannot return to it without a new certificate, which is a
  new identity in every pool it belongs to; removal is meant to be permanent, so it is a tap (1.6).

Request context. Every member-protocol request carries `ctx: {pool: id}` or `ctx: {board: id}`:

| Context | Allowed to | Not allowed |
|---|---|---|
| `hello` (none) | any certificate not removed in every pool that knows it; answers only the pool ids and boards the caller is entitled to | revealing other pools |
| `{pool}` | `acts`, `serve`, test shards, `attach`: a certificate active in that pool | anything else |
| `{board}` | `vector`, `pull`, `push` for that board: a certificate in the board's roster (6.3) | other boards, pool acts |

The TLS verifier refuses a certificate only when every pool that knows it has removed it; the per-request check is the authority,
as today.

### 4.2 Layout and the node

```
<state>/device.key, device.cert               one identity
<state>/persons.json ...                      device-owner person keys, as today
<state>/pools/<id>/charter.json, acts.jsonl, roster.json, limits.json, consent.json
<state>/pools/retired/<id>/                   moved, left or retired pools, never read for authority
<state>/boards/<board>/pool                   the pool id the board belongs to (one line)
<state>/boards/<board>/charter.json           only for a board shared by more than one pool (section 6)
<state>/boards/pool-<id[:8]>/                 each pool's own history board; belongs to that pool and is never shared
<state>/serve/offers.json, usage.json
<state>/leases.json                           one table for the whole device
```

The node holds up to 16 pools. A request that names no pool is resolved when the device is in one pool; with several it is refused
with the plain question "This device is in two pools, home and work. Which one do you mean?". A session registers on a board
through a pool: the token records `(board, name, pool)` and the identity entry names the pool. A board created on a device in
several pools names its pool, taken from the project's registered pool (`projects.json` gains `pool`).

**The unfounded pool of one.** A fresh device is in a pool of one that has no charter, no id and no admins: the device is its own
only member, and it admits nobody. `pool_create` founds the first pool (an approval); the boards the device already holds are bound
to it at that moment, so a person who used Poolhouse alone for a week and then makes a pool keeps everything. Joining another pool
while unfounded just joins that pool and binds the boards to it. A device in a founded pool that joins another pool belongs to
both; pools never merge. The automatic founding of an open pool per project (`settle`, `claim_project`) is removed, since founding
is an approval; a device still joins an open pool it hears (a pool that names a project is joined only by a device of that
project), so devices of one repository on one network find each other as before once one pool is founded.

**Beacons** carry the pool id (32 hex) and are sent once per pool per interval, signed by the device key, with `lone`, `project`
and `tail` (this device's tailnet addresses, at most two, 4.4) as today's fields. A pool may set `discovery: off`; it is then found only by a ticket or a link. Heads of board logs stay signed in
a domain with no pool id (the pool field of the head message is the empty string permanently): a board is not a pool's, so no pool
id may enter its signatures, and the old note "the head signature uses an empty pool id" is a rule.

**Grants** gain `scope.pool`: an id, `*`, or `admin:<key fingerprint>` (the pools whose admin set holds that key). A grant that names
a pool-level action is spent only for the pool in the call's context. The registry changes: removed `set_join_policy` and
`shard_consent`; added `pool_create`, `pool_leave`, `pool_reissue`, `pool_migrate`, `admin_manage`, `board_share`, `board_leave`,
`board_rebind`, `serve_offer`, `serve_call`, `inbound_consent`. A grant lets a session **ask**; an admin signature is what makes a
governing act true for everyone; both are needed, and the first-run bundle (section 2) is how a person gives the first without a
tap per act. Pool-level audit entries (member added, uses of pool-level grants, offers) are written to the pool's own history
board, never to a shared board.

### 4.3 Local API and command line

| Method | Needs | Result |
|---|---|---|
| `pool_list` | none | every pool: id, name, policy, member count, this device's role, boards, offers |
| `pool_status {pool}` | none | members with reach, governance state (admins, thresholds, pending recover or succession), per-pool limits |
| `pool_create {name, policy, discovery}` | grant `pool_create` | the pool; approval for the founding charter |
| `pool_leave {pool}` | grant `pool_leave` | this device leaves (`leave` act) |
| `add_device {pool, ttl_s}`, `join_pool {pool?, host, port, passphrase, fingerprint}` | grants `add_device`, `join_pool` | as today, per pool; a device in other pools joins as a new pool record |
| `member_remove {pool, fingerprint}` | grant `member_remove` | asks for the admin act; one tap |
| `pool_reissue {pool, policy, keep}` | grant `pool_reissue` | draft and approval (1.9) |
| `admin_list`, `admin_add`, `admin_remove` | `admin_manage` | admins and their state |
| `pool_migrate` | grant `pool_migrate` | 1.14 |
| `sync_now {pool}`, `shard_call {pool, device, ...}`, `capacity {pool}` | as today | per pool; ambiguity is a plain question |

Command line for agents (`--json` everywhere): `poolhouse pool list | status | create | join | add-device | remove | leave |
reissue | admins | migrate`, `poolhouse board share | unshare | sharing`, `poolhouse-serve offer | withdraw | offers | offered |
usage | stop`.

### 4.4 Devices on different networks: the tailnet

Owner decision, 2026-10-11: **devices on different networks reach each other over the person's tailnet (Tailscale). Poolhouse runs
no relay and no rendezvous server of its own.** Devices on one network never need Tailscale: they find each other by beacon on the
segment and dial each other there, and an `open` pool is joined from the segment only. The phone path is already HTTPS over the tailnet
(`docs/pool.md`, 2.5; `docs/phone-approval.md`).

**The tailnet is a route, never an authority.**

* Pool membership, admission, grants, offers and shared boards never depend on tailnet membership, tags or access rules. **A device
  on the tailnet is not a member**, and a member needs no tailnet. Mutual TLS 1.3 with the pinned device certificate decides every
  request, as on a LAN. The node never asks Tailscale who a caller is (no tag, no tailnet identity: `docs/onboarding.md` already
  says it is not checked).
* Whatever the tailnet lets reach the node's port can start a handshake, as anything on a LAN can. An unknown certificate gets
  `hello` and nothing else, and a join only inside an open join window (a code, three tries). `open` admission is never given to
  a tailnet address: `on_segment` is the subnet of an up, non-point-to-point interface, so a tailnet address is outside it.
* Tailscale's access rules and tags are the person's. We never read them for authority, never edit them and never ask the person to
  loosen one beyond the plain sentence that names what blocks.

**Detection, read-only, and what the agent says.** The node reads its own interfaces; the supervisor writes `tailnet.json` from
`tailscale status --json` (the CLI on the path, the macOS app's copy, or `tailscale.exe`; `fleet/tailnet.py` today: bounded in size
and time, scrubbed environment, only whitelisted fields, no authentication URL, key or token ever read) every 30 seconds and at each
join. The state is `missing`, `signed-out` (installed, backend state `NeedsLogin` or `Stopped`) or `up`, with the device's own
tailnet addresses. The agent reads it only when something needs the tailnet (journey 2c, a member that only the tailnet could
reach, an approval away from home), says one plain sentence (section 2 lists them), and **never installs, signs in or changes
Tailscale**: installing and signing in are the person's, as `docs/pool.md` 2.5(c) says of every system setting.

**Addresses.** A member's addresses are kept per fingerprint and per route (`lan` or `tailnet`).

* `lan` addresses are learned only from a beacon heard on a local segment and from the direct dial of a join. A statement that
  arrives over the tailnet never teaches a private LAN address (an address is only good from where it was seen), and a device never
  advertises a LAN address over the tailnet.
* `tailnet` addresses (100.64.0.0/10) are learned from the join (the address dialed, or the source address the issuing device saw),
  from the member's own signed beacon (`tail`), from the member's own `hello` over an authenticated connection (`addrs`), and as
  **candidates** relayed by another member. A candidate is stored only after a handshake at it shows the pinned certificate (the
  rule `fleet/tailnet.py` already keeps). **The tailnet's own device list** (`tailscale status` members, MagicDNS names) is only a
  source of candidates to try, never trust, and a name is never an identifier.
* A device advertises a tailnet address only when its own interface holds it **and** Tailscale's status lists it as this device's.
  An address in 100.64.0.0/10 on a normal interface (some carriers hand them out) is a LAN address unless Tailscale says it is the
  device's own. IPv4 only in the first release.
* Dial order: `lan`, then `tailnet`; both failing is the existing backoff (a second, doubling to a minute).
* Beacons are multicast and broadcast, which a tailnet does not carry, so devices on different networks do not discover each other
  over it: they join by ticket and link (3.2, 2c) and learn addresses as above. The signed beacon heard at home carries `tail`, so
  devices that have met at home reach each other later away from it.

**Exit nodes and subnet routers.** An exit node moves the machine's default route; the address `route_address()` finds is then not
a LAN address. The node advertises only the addresses of up, non-point-to-point, non-tailnet interfaces and the tailnet addresses
Tailscale confirms, never the route address and never an address its own interface does not hold. A subnet router publishes
private ranges into the tailnet, so a dial to `192.168.1.5` can reach a different machine: the pinned certificate refuses it
("answered with another certificate"), the address is not stored, and the agent says the sentence of the "a different device
answered" row of section 2.

**Between people.** A friend's pool across two tailnets is a pool across two networks. A device shared into the other tailnet is
reachable at the address and name that tailnet gives it; we keep addresses per route and never names, the device's own `hello`
announces its address in its own tailnet, and every one of these is a candidate that the pinned certificate settles. Whether the
sharing tailnet's access rules allow the node's port is the people's, and the agent says so ("Tailscale is on for both devices but
isn't letting them talk. Check the access rules in your Tailscale account."). Two different tailnet accounts without a share is
the sentence "These devices are on different Tailscale accounts. Ask the other person to share the device with you in Tailscale."
Nothing here creates a share, an invitation or a rule.

**Address changes and offline.** A device rereads its own tailnet addresses every 30 seconds and on an interface change; new ones go
into the next beacon and `hello`, and members try them when the old one fails. A device that signs out of Tailscale stays a member:
it is reachable on its LAN and unreachable from other networks until it signs in. A device that comes back dials the members whose
addresses it holds, and its dial teaches them its source address, so a changed address heals from either end; work queued meanwhile
syncs after (boards are append-only logs).

**`reach` and `reach_hint`.** `reach` keeps `direct`, `channel`, `none` and `self`; a new `route` (`lan`, `tailnet` or empty)
says which address answered. The hints are plain sentences the agent can say as they are:

| Case | `reach` and `route` | `reach_hint` |
|---|---|---|
| answered on the tailnet | `direct`, `tailnet` | none |
| no address, Tailscale missing or signed out here | `none` | "It is on another network, and Tailscale isn't on and signed in on this device." |
| a tailnet candidate that did not answer | `none` | "It did not answer on the tailnet. Is Tailscale on and signed in there?" |
| answered with another certificate | `none` | "Another device answered at that address, so I ignored it." |
| on the tailnet, access rules deny | `none` | "Tailscale isn't letting these two devices talk. Check the access rules in your Tailscale account." |
| a device only the other side can dial | `channel` | as today, and "give it a Tailscale address if it should be dialed" |

**What we log.** Locally only, in `node.log` and `pool_status`: the route used per member (`lan` or `tailnet`), the Tailscale state
word, this device's own tailnet addresses and members' tailnet addresses (so the person can see what the node dialed). Never in a
board entry or an audit entry (those replicate to other pools, which hold the addresses they need already): no address, no tailnet
name, no account name. Never stored or logged anywhere: the tailnet's name, the login name, authentication URLs, keys, tokens, and
the MagicDNS names of other tailnet devices (a name is spoken in a sentence, not saved). Diagnostics redact tailnet addresses and
names as they redact credentials. Counts of dials per route are kept as numbers.

**Where it is a human step.** Tailscale is installed and signed in by the person on every device that must cross networks
(`tailscale` steps, 3.2 and 3.5). Same-network joins never need it and never mention it.

## 5. One device, many pools: capacity, limits and fairness

**Leases are per device, not per pool.** One table (`leases.json`) decides the device's GPU, CPU slots, memory and model slots for all
its pools. The GPU stays one holder at a time: a model call from pool A, a test shard from pool B and the owner's own work all
queue on the one `gpu` or `cpu_slots` resource. Requests gain a `pool` label (for fairness and accounting; never for authority) and
a `quiet` flag (no board entry per grant, used by model calls, whose per-request entries would flood the pool board; counts go to
usage records instead). On a shared board only `claim` leases are mirrored: gpu, memory and model-slot facts of a device do not
leave its pools.

**Capacity accounting per pool.** `capacity {pool}` returns the device's capacity (as `ph.pool.capacity()` today) and, for that pool,
what it used in the last 24 hours (GPU seconds, slot seconds, memory MB-seconds, requests, refusals, queue wait) and its limits.
`pool_status` shows it.

**Who may use this device's compute: per pool, per kind of work, off by default** (decision L2). `shard_consent` becomes
`inbound_consent {pool, work: shards|serve, on, ...}`: a device takes tests or model calls from a pool only after its owner turned
that on for that pool (the owner's own pools are on by the first-run bundle). Defaults when turned on, safe by choice:

| Knob | Default |
|---|---|
| `priority` | `background`: the device's own interactive work always goes first |
| `weight` | 1 (equal fair share between pools) |
| `max_slots` (tests) | half the cores |
| `max_queue` | 4 waiting requests per pool |
| `max_wait_s` | 120, then the caller is told it is busy |
| `rate` (model calls) | 30 requests a minute per member |
| `max_context`, `max_new_tokens` | 8192, 2048 |
| `cache` | `pool`: slots are shared inside a pool, erased when the next request is another pool's (7.4) |

**Fairness.** Within a class, requests are served pool by pool, the pool that used the least GPU time in the last ten minutes
first, then member by member inside a pool, then arrival order. The existing shortest-estimate-first order stays inside one
member's requests. A request that waited past `max_wait_s` still goes ahead by arrival (`docs/node.md`, "Leases").

**Person feels:** nothing for the owner's own pools; one tap the first time a foreign pool is allowed to use the device, with the
limits shown in plain words.

## 6. Boards shared by pools

### 6.1 The model

A board is held by a set of devices. It belongs to one pool unless a **charter** shares it. A board with one pool has no charter and
behaves as today. A shared board's charter lists its pools, each with a right (`rw` or `ro`) and a **roll**: the devices of that pool
that hold the board for the other pools. Sharing is by named devices and the whole board, never by "all members" (an open pool's
members are strangers) and never by channel (a log is a hash chain, so a channel cannot be cut out of it; a channel's worth of
content is a different board plus a link).

The device lists are the point: pool B's other devices are invisible to pool A. **A member of pool A sees of pool B only the
board's participants** (their names, fingerprints and sessions on the board), not pool B's membership or its admins' other work.

### 6.2 Creating a shared board, and who may add a pool

Adding a pool is an explicit, signed, audited act of people, never an agent default:

1. The agent on a device of pool A asks `board_share_invite {board, right}`. The node opens a window like `add_device`: a code and a
   link, five minutes. Nothing is disclosed yet.
2. The agent on a device of pool B redeems it (`board_share_join {link}`). A SPAKE2 exchange over the pinned connection, authenticated
   by the code, carries B's proposal: B's pool id, name and admin keys, B's proposed roll, B's wanted right.
3. A's node turns B's proposal into one approval for A's admins: "Pool 'y' (admins: the friend's phone) asks to share board 'x' with your
   pool, read and write, from the device 'guest-laptop'. Allow?" with the roll's review list (name, last seen, how it joined; an
   open-joined device defaults to drop). The tap signs A's side and, in the same approval, creates the one-use `board_share` grant
   for the asking session naming the board and both pools. The signature is the consent; the grant lets the node do the exchange and
   writes the audit entry.
4. B's node does the same for B's admins. Both sides are then held by both pools. The charter takes effect when every pool's side
   is signed. When one person holds the admin key of both pools, the phone shows both requests together and one tap signs both.

A charter is valid only when **every pool listed has signed it** (kind `board_side`, sensitive threshold of that pool's admins). A
pool's side names its own roll; changing the set of pools or a right needs every pool's signature; changing only its own roll needs
only its own admins and, where the new devices were admitted by ticket, may be delegated (1.3: kind `roll_add`, never for open-joined
devices). An open pool's devices appear on a roll only when a person chose them in the review.

### 6.3 Replication across pools

* A device syncs a shared board with a member of its own pool as today. Across pools only roll devices talk: `x` and `y` sync board X
  only when they are active members of one pool of the board, or `x` is on the roll of pool P and `y` on the roll of pool Q.
  Entries reach a device of A that is not on the roll through a roll device of A, and stay verifiable because every device holds the
  charter and so every origin's key.
* The context of every sync request is `{board: id}`. A roll device answers `vector`, `pull` and `push` for that board only; it
  answers no pool act, no board listing, no model call and no test shard to a device that is not also its pool's member. A shared
  board is not pool membership.
* **Rights are enforced by receivers.** A device accepts rows of an origin only when that origin's device is in the board's roster
  (own pool, or a roll) and its pool's right is `rw`. A pool whose right is `ro` receives and reads; its sessions are refused writes
  (`denied: your pool may only read this board`), and any rows its devices write anyway are refused by every other device. The
  effective right of a pool is the lowest of its own declaration and what every other side signed. A device in two pools of one
  board has the more permissive of its two rights (each is a person's explicit act).
* 64 origins per board still bounds the number of writing devices (`board::MAX_ORIGINS`).

### 6.4 What a spanning board does to names, sessions, claims, tasks, landing and grants

| Part | Behavior on a board spanning pools |
|---|---|
| Names | Unique per board as today (hash of harness and session, extended on collision). A name written on another device is shown `name@dN`; the identity entry names the writer's pool. |
| Sessions | A session registers on its device's node through a pool it is a member of; the token records it. It reads and writes the whole board subject to the pool's right. |
| Claims | `branch` and `area` claims are board-wide, so pool B's claim of a branch blocks pool A's request for it, naming the holder: coordination across pools, wanted. Device resources are not mirrored. |
| Tasks | Entries count only from this device and from devices on this device's landing trust list, as today; a device of another pool is "foreign, ignored" until a person trusts it for that board. |
| Landing queue | Same: `land_trust` is per board and per device; a roll device of another pool is never trusted by default. |
| Grants | A grant is scoped to a board and a pool. A grant entry counts only when signed by a key enrolled on the checking device, so another pool's grants count for nothing here. Board-level grant entries are visible to every participant; pool-level uses go to the pool's own history board. |
| Direct messages and private rooms | **Refused on a shared board.** They replicate like any row, so the other pool's devices would store them in the clear. Open rooms and `#general` work. Lifted when sealed messages land (`docs/pool-encryption.md`, slice 4). |
| Files | Message metadata replicates; the bytes stay on the device that stored them (unchanged). |
| Quarantine, subscriptions, scratch | Node-local, unchanged. |

### 6.5 Leaving a shared board, and what stays

A pool leaves by signing a `board_side` without itself (`board_leave`, its own admins' signature alone, since it only reduces what
others disclose to it). Effects: its roll devices are removed from the roster; every device freezes the origins of the departing
pool's devices at the length it holds when it adopts the new charter (no further rows are taken from them, whoever relays them);
the departing pool keeps its copy and continues the board with the pools it still has (itself); the other pools keep every entry the
leaver wrote up to the freeze. **Entries already replicated stay where they are**: nothing unsends data, and the approval text says
so ("Entries already copied stay on the other pool's devices"). Another pool is removed from a charter only by every remaining
pool's signature, and any pool may stop syncing with another for itself (`board_block`, its own admins alone, local).
A pool that comes back needs a new unanimous charter; the gap's entries merge as a union of append-only logs.

### 6.6 Relation to links

Cross-pool sharing and links stay **separate**: a link is node-local, session-level access between two boards hosted on one device,
needing no replication; sharing is replication of one board among devices and pools. Neither replaces the other. One rule joins
them, so a link cannot route around a charter: **a link may not carry entries to a pool that does not already hold the source
board.** A read-only link from board F into T is allowed only when every pool of T is a pool of F; a read-write link only when the
pool sets are equal (the session can read and write both ways). Making a link checks it; adding a pool to a board ends any link
that now breaks it, with an audit entry. For a board with a single pool the rule always holds, so existing links are unchanged.

### 6.7 Failure and conflict

A board id on two pools that were never shared is two boards; when the pools share it the logs union (origins differ, rows carry the
board id in their hash). A pool removed mid-sync: the roster is checked per request, so the next request is refused. A charter
update with a missing signature is held and shown as "waiting for pool 'y's approval". A device behind NAT reaches roll devices of
the other pool by dialing out, as today.

### 6.8 Pools that move, and rebinding

When a pool is reissued (1.9) the pools it shares a board with hold its old admin keys in the charter side, so they can verify the
handoff. They follow it for the board, silently, only for roll devices that are in the keep list and only if the admin set is
unchanged; any other change is a sensitive approval for their admins. After a total loss (1.8) each device owner may move its local
copy to a successor pool with `board_rebind` (a device-owner act; the other devices adopt the same copy by the same act; origins
union).

### 6.9 Tests

Rust `app/poolhouse-node/tests/board_share.rs`: a board shared by two pools converges for the members of both and for a device of
each that is not on a roll; a device of neither pool, and a roll device of the other pool asking about another board, are refused
(`denied`); a `ro` pool's rows are refused by receivers and its sessions cannot write; a roll device answers no pool op; a
non-roll device of pool A refuses to sync with pool B; leaving freezes the origins and keeps the entries; the link rule in both
directions; direct messages and private rooms refused; claims block across pools; landing is not trusted; a grant for pool A is
refused for pool B; a charter with one missing side takes no effect; an open-joined device is not on a roll unless chosen.
Pool-check `board-shared-two-pools` (a pool-A device, a pool-B device and a device in both: entries written on each reach all;
a fourth device in neither is refused) and `board-shared-non-member`.

## 7. Serving a local model to pools

### 7.1 What exists, and the gap

The broker (`poolhouse-serve`, `docs/serving.md`) is per device: `up` is a lease, one server per port, the lease is held by a
detached process. It listens on loopback only, a request to another device's model goes through the old daemon's `/infer` proxy,
and nothing says which pool may call which model. The node has the `model_slot` and `gpu` resources and per-device lease
authority (`docs/node.md`, "Leases"), and the Python broker does not take its leases from the node yet (`docs/node.md`, "What is
left"; prerequisite P2).

### 7.2 Offers

An **offer** is a record on the serving device, `serve/offers.json`: `{id, model, pool, context, parallel, priority, limits,
since}`. **The default is none.** The owner (or the owner's agent under `serve_offer`) creates one per model per pool. An offer is
the device owner's act, authorised by a `serve_offer` grant that a person key enrolled on that device signed (1.7); the first-run
bundle covers offers to the owner's own pools. Offers of one model share one shape: the first offer fixes the context and the
parallel slots, and a later offer naming another is refused with "This model is already offered at a different size".
An offer to a pool the person does not administer, or to an `open` pool, needs a tap with the plain text ("'home' lets anyone on
this Wi-Fi join, so anyone on it could use this model and see what it is asked") and, for an open pool, a standing warning in
`pool_status`.

The offered set is published to the pool in the same member exchange as acts, beside them and not as acts (live state, refreshed
each exchange, dropped after 90 seconds without refresh; not history), and each offer and withdrawal also writes a small audit entry to the pool's history board (model
name, device, who). Nothing names a file path.

Node methods: `serve_offer`, `serve_withdraw`, `serve_stop`, `serve_offers`, `serve_usage` (this device), `serve_offered` (what
the pools' members offer), `serve_call` (streaming).

### 7.3 A call, end to end

1. The caller's session asks its node `serve_call {pool, model, device?, request}` (grant `serve_call`, scope the pool, resource the
   device and model; the bundle covers the owner's own pools, a foreign pool needs a tap because prompts leave the device).
   The caller's relay screens the prompt for credentials with the Python `screen` module, as it does for outgoing board text, and
   refuses one (the node does not judge text).
2. It opens a fresh connection to the serving device (a call never shares a connection with sync) pinned to its certificate, and
   sends `{op: "serve", ctx: {pool}, model, request, by: <label from the token>}`. The label is unverified; authority is the
   certificate and the pool. A serving device that cannot be dialed (behind NAT) keeps up to four attached connections open to the
   members it dials, one per concurrent call, because a stream occupies its connection.
3. The serving device checks, under its node lock: the certificate is an active member of the pool; an offer for `(pool, model)`
   exists and is not withdrawn; the pool is allowed to send work (`inbound_consent`); per-pool limits and the member's rate.
   Any refusal comes back as a `say` sentence.
4. It takes a `model_slot` lease on itself (the broker starts or adopts the server only under a lease it holds; holder is the node's
   serve service for that offer, so two pools calling one model share one server) and queues a `gpu` lease for the generation, class
   by the pool's priority, labelled with the pool and the member.
5. It forwards the OpenAI-shaped request to the loopback server and streams the answer back as frames
   (`{state: queued, position, wait_estimate_s}`, `{chunk}` ..., `{done, usage}`); each frame re-checks that the caller is still an
   active member and the offer still stands.
6. It releases the `gpu` lease when the generation ends or the caller's connection drops, writes the usage record, and keeps the
   server for the offer's idle time.

The serving device runs the model and nothing else: no tools, no files, no network for the model. A reply is data a caller's agent
treats as untrusted text. No part of the path carries a person's credential, and `act_as_person` stays refused.

### 7.4 Isolation

* A request is never visible to another pool: no endpoint lists calls, usage is per pool, and the frames of one call go only to the
  connection that opened it.
* The prompt cache and KV slots are **not shared across pools unless the serving device's owner declares it** (`cache: pool` is
  the default: shared inside one pool, erased with the server's slot-erase action whenever the next request is another pool's;
  `cache: member` erases between members; `cache: shared-with [pool ids]` is the owner's explicit declaration for pools that are
  all theirs). Cost, stated: alternating pools reprocesses prompts. The cache is also a timing channel, which is why the default
  closes it.
* A server serving pools never runs with prompt logging; a test plants a marker in a prompt and greps every log and file the
  device writes.

### 7.5 Metering, and stopping an offer at once

Per pool and per requesting member (certificate): requests, prompt and completion tokens, GPU seconds, queue wait, refusals, kept in
`serve/usage.json` by day for 90 days, readable as `usage {pool}` and written as counts only (never text) to the pool's history
board hourly. `serve_withdraw {offer}` removes the offer under the node lock: new calls are refused that instant and in-flight
calls drain for at most 30 seconds. `serve_stop {offer}` is the stop button: withdraw and abort every in-flight call now (the
slot is cancelled and the stream ends with "the owner stopped this model"). Both are covered by `serve_offer`, and the person's
screen has the stop button without a grant. Removing a member ends its calls at the next frame. A reissue withdraws every offer
(1.9).

### 7.6 Privacy and attestation limits

* **The serving device sees every prompt and every answer in the clear**, in the server's memory, its KV cache and possibly swap
  and core files. Its owner, as root, can read them. Transport is TLS 1.3 pinned to the member certificate; there is no attestation,
  and `docs/service.md` (guest privacy) and `docs/pool-encryption.md` (section 7, Level 1) say what protects against accidents and
  other tenants and what does not protect against the owner. For secrets, use devices you own.
* The caller trusts that the model named is the model run; nothing proves it. A serving device can return wrong or hostile text, so
  results are untrusted data.
* The serving device's owner learns, per call, the caller's certificate, the model and sizes. The caller learns nothing of other
  pools' calls.
* Hard floors hold: one thing on the GPU at a time (the `gpu` lease), agents are never people, no `act_as_person`, no second GPU
  grant, and a model call cannot reach a person's credential.

## 8. Finding a served model: the public API

All additions follow `docs/api.md`: each name is documented there, listed in `api.SURFACE`, exercised by `tests/test_api*.py`, and
recorded in `tests/api-signatures.json` (regenerate it with the snapshot-writing mode of `tests/test_api.py`, which the test's
docstring names, and say why in the commit). Existing
functions gain a keyword-only `pool: str = ""` (empty means the only pool; with several it raises `ph.Error` with the plain
question).

| Name | Contract |
|---|---|
| `ph.NeedsApproval` | An approval is waiting: `say`, `approval` id, `where`, `expires_ms`. Not a refusal. |
| `ph.pool.pools()` | The `PoolSummary`s of this device. |
| `ph.pool.create(name, *, policy="secure", discovery="lan", wait=True)` | Make a pool (a person key's approval); returns the `Pool`. |
| `ph.pool.leave(pool)` | Leave a pool. |
| `ph.pool.join_link(link, *, wait=True)` | Join the pool a link names (address, certificate pin and code in one string); returns the `Pool`. |
| `ph.pool.reissue(pool, *, policy, keep="reviewed", wait=True)` | Reissue under the other policy (1.9). |
| `ph.pool.admins(pool="")`, `add_admin(label, *, pool="", person="")`, `remove_admin(fingerprint, *, pool="")` | Admins; adding and removing wait for approval. |
| `ph.pool.approvals()`, `ph.pool.wait(approval, timeout_s=60)` | Pending approvals and a wait on one. |
| `ph.pool.allow(pool, work, *, on=True, priority="background", max_queue=4, ...)`, `ph.pool.limits(pool="")` | Let a pool send this device tests or model calls (`work` is `tests` or `serve`) with limits, and read them with this device's use by that pool. |
| `ph.pool.PoolSummary`, `ph.pool.Admin`, `ph.pool.Approval` | Frozen dataclasses. |
| `ph.board.Board.pools()`, `.share(pool, *, right="rw", wait=True)`, `.unshare(pool)`, `.sharing()` | Pools of the board and its sharing; sharing waits for approvals. `connect` and `register` take `pool=`. |
| `ph.serve.offer(model, pool, *, context=0, parallel=1, priority="background")`, `withdraw(model="", pool="", *, stop=False)`, `offers()` | This device's offers. |
| `ph.serve.offered(pool="")` | The `Offered` models members offer this device: pool, device, model, context, parallel, state (`resident`, `loadable`, `busy`), queue, since. Expires 90 s after the offer's last refresh. |
| `ph.serve.connect(model, *, pool="", device="")` | A `Remote`: `base_url`, `pool`, `device`, `model`, `expires_ms`; without `device` it picks the offering device with the shortest queue and keeps it for the conversation (cache). |
| `ph.serve.usage(pool="")` | `Usage` per pool and member for this device (as a server) and calls it made (as a caller). |
| `ph.serve.Offer`, `Offered`, `Remote`, `Usage` | Frozen dataclasses. |

**The endpoint a member sees.** `Remote.base_url` is `http://127.0.0.1:<port>/v1` on the caller's own device, served by a small
loopback relay (`poolhouse.serve.relay`) that translates OpenAI-compatible HTTP to `serve_call`: `POST /v1/chat/completions`
(streaming as server-sent events), `GET /v1/models` (the `offered()` list, each `owned_by: <pool>/<device>`), a model name as
`<model>` or `<model>@<device>`. A bearer token per `Remote` authenticates the caller's process; errors are OpenAI error objects
whose message is the `say` sentence. `ph.client.Client(remote.base_url)` and any harness that takes an OpenAI base URL work
unchanged.

## 9. Failure modes

| Case | What happens | What the agent says |
|---|---|---|
| The serving device is offline | Its offers expire from `offered()` after 90 s; a call fails before the first token with `unreachable` and `reach_hint`; `connect` without a device picks another offering device | "'guest-laptop' isn't answering. I'll try another device if one offers it." |
| Lease contention | The call queues (first frame carries position and estimate); past `max_wait_s` it is refused as busy; the owner's own work goes first | "The model is busy; you're number 2 in line, about 40 seconds." |
| Pool removed or caller removed mid-request | Each frame re-checks membership; the stream ends and the `gpu` lease is released | "You were removed from 'home', so the request stopped." |
| Offer withdrawn or stopped mid-request | Drain 30 s (withdraw) or abort (stop) | "The owner stopped this model." |
| The serving device restarts mid-stream | The stream ends with an error frame; a retry is the caller's choice | "The device restarted. Say retry and I'll ask again." |
| Caller disappears | The write fails or 30 s pass without a read; the server's slot is cancelled and the `gpu` lease released | none |
| A pool is reissued during a call | Calls of the old pool end ("pool moved"); offers return only if listed | "'home' moved to a new pool; I'll ask again once the device has moved." |
| Two pools offered one model, one removes the other's right | Each offer is independent; the model server stays while any offer needs it | none |
| Shared board and a pool leaves mid-sync | Next request refused; origins frozen | "'y' stopped sharing the board; what it already sent stays." |
| A member ignores the old pool after a reissue | It is not in the new roster and is refused | "This pool moved and this device wasn't kept." |
| Devices on different networks and Tailscale missing or signed out on one | Reach is `none` with the hint; nothing is lost, boards sync when they meet at home or when it is signed in | "To reach your other network I need Tailscale on this device..." (section 2) |
| A member's tailnet address changed | Its next beacon or dial teaches the new one; the old one fails by backoff | none |
| Tailscale access rules deny the node's port | `none` with the hint; no setting of ours changes | "Tailscale isn't letting these two devices talk. Check the access rules in your Tailscale account." |
| An exit node or subnet router sends the dial to another machine | The pinned certificate refuses it; the address is not stored | "Another device answered at that address, so I ignored it." |
| The person is away from home and the phone is off the tailnet | The approval waits on the screen and shows on the phone when it is reachable; the agent is told it is waiting, not failed | "I asked you on your phone; it can't reach this device away from home. Install Tailscale on it, or tap on the screen." |
| Governance: no admin reachable | Pool keeps working unchanged; governing calls answer 1.8's sentence | "This pool has no admin left, so it can't be changed. You can start a new pool." |

## 10. The consolidated test plan

* **Rust node tests** (each seen to fail first, `app/poolhouse-node/tests/`): `governance.rs`, `reissue.rs` (1.13), `pools.rs`
  (two pools on one device: the pool-and-certificate check; a member of A cannot reach B's ops; one certificate in both; beacons per
  pool; leaving A leaves B; a join does not merge; lone-pool absorption; `needs_migration` and `pool_migrate`; grants scoped by pool;
  one GPU lease across pools; fairness; limits), `board_share.rs` (6.9), `serve.rs` (default no offers; a member of an offered pool
  calls; a non-member and a member of another pool are refused; metering per pool and member; withdraw, stop, removal mid-call; slot
  erase on a pool switch; a stub model server stands in for the real one and records requests, so no GPU is needed).
* **Python tests**: `tests/test_pools_api.py` (every new name, signatures, `NeedsApproval`, refusal fields), `tests/test_serve_offered.py`,
  `tests/test_serve_relay.py` (a real loopback socket, real streaming, `/v1/models`), `tests/test_onboarding_steps.py` (the ledger and
  the budget file), `tests/test_api.py` snapshot.
* **Pool-check steps** (the Docker N-device rig plus the native runners on macOS, Linux and Windows and WSL): `pool-two-on-one-device`
  (device 0 in pools A and B with devices {0,1} and {0,2}); `serve-two-pools` (one model offered to both: a member of each calls it,
  alternating, and neither sees the other's traffic; the GPU lease is held by one at a time); `serve-not-offered` (**negative**: a
  non-member and the other pool's member are refused a model not offered to them, and a member of A is refused a model offered only
  to B); `serve-stop`; `board-shared-two-pools` and `board-shared-non-member` (a board shared by two pools converges for members of
  both, and a member of neither cannot read it); the governance steps of 1.13; the onboarding steps of 3.4. A live model step
  (`serve-live`, a small Qwen3.8 through the broker, one GPU lease) is opt-in and needs the owner's lease; CI uses the stub.
* **The tailnet in the rig** (`tests/pool_check/steps_tailnet.py`). The Docker rig makes a second "different network": two bridge
  networks with no route between them, each device on one of them, plus a third bridge whose subnet is in 100.64.0.0/10 that every
  device joins as a second interface; a stand-in `tailscale` command (on the container's path, like the rig's stand-ins for
  `powershell.exe` and `wsl.exe`) prints `status --json` for that third network, with `up`, `signed-out` and `missing` selectable.
  Production code is unchanged and reads the same command. Steps: `tailnet-join-by-link` (a device on the other bridge joins by link
  over the tailnet bridge and syncs a board), `tailnet-not-authority` (a device on the tailnet bridge that is in no pool gets `hello`
  and nothing else, and cannot join without a window and code; a member removed from a pool is refused although it is on the
  tailnet), `tailnet-open-refused` (an `open` pool is not joined from a tailnet address), `tailnet-candidate-needs-pin` (a candidate
  address answered by another certificate is refused and not stored), `tailnet-wrong-address` (an exit-node style route address and
  a CGNAT address Tailscale does not list are not advertised), `tailnet-address-change` (a member's tailnet address changes and the
  pool heals), `tailnet-detect` (missing, signed out and up each give the agent's sentence), `tailnet-no-lan-leak` (no LAN address
  is learned from a tailnet-delivered statement and none is advertised over it), `tailnet-log` (a planted tailnet name, account name,
  authentication URL and MagicDNS name appear in no board entry, no audit entry and no stored file). The Rust parts are tests of
  `netif.rs`, `beacon.rs` (the `tail` field signed) and `reach.rs` (the hints). Windows and WSL run the same steps natively in CI;
  none of this needs a Tailscale account.
* **Real tailnet checks, later, on the owner's devices and phone, listed exactly** (each needs a real Tailscale and is not run by
  CI): (1) a Mac and a Windows or Linux device on two different networks (one on a phone hotspot) join by link over the tailnet and
  sync a board; (2) `tailscale status --json` parses on the macOS app's copy, on Windows and on Linux and WSL, in the states
  signed out and stopped; (3) a sign-out and sign-in changes nothing about membership and the pool heals; (4) an exit node enabled
  on one device does not make it advertise a wrong address; (5) a subnet router in front of a LAN does not make a dial reach the wrong
  machine without being refused; (6) an access rule that blocks the port produces the sentence and is fixed only by the person;
  (7) the phone approves a reissue and a share away from home over HTTPS on the tailnet (the mechanism is `docs/phone-approval.md`); (8)
  a device shared into a second tailnet (this needs a second Tailscale account, the owner's) joins and syncs; (9) the Windows
  firewall allows the node's port on the Tailscale adapter through the one prompt `device_setup` already plans.

## 11. Slices, in dependency order

The first slice closes the hole; each later slice names what must exist before it. `P` rows are prerequisites owned elsewhere.

| # | Slice | Scope | Depends on |
|---|---|---|---|
| P1 | Phone approval | `docs/phone-approval.md`: enrol the person key by QR, the per-action signed approval, push, the screen path; removes the terminal-only step of `grants-setup`. Owned by another branch. | none |
| P2 | Serve broker on node leases | The Python broker takes `model_slot`, `gpu` and claims from the node (`docs/node.md`, "What is left"). | none |
| P3 | Device keys in the keystore | `device.key` into the OS keystore where one exists (`docs/pool-encryption.md`, slice 3). | none |
| 1 | Governance core (first; everything else depends on it) | Charter, pool id hash, admin set, thresholds, acts, `acts` op replacing `members` as authority, tickets, delegates, open and secure admission, `remove`, `leave`, cut rule, `set_join_policy` refused, plain `say` errors on new methods, step ledger skeleton; with it the vocabulary of section 12 (`docs/glossary.md`) and the one-pool wording of `docs/node.md`, `docs/api.md`, `docs/serving.md`, `docs/pool-check.md`, `docs/test-farm.md`. Tests of 1.13 (core part) and the pool-check step `gov-plain-member-cannot`. | none (P1 for real approvals; the rig key until then) |
| 2 | Reissue, migration, copy defence | Handoff, follow rules R1..R7, review list, grace window, fallback approval, clone heartbeat and flag, `pool_migrate` (the first reissue). Tests of 1.13 (reissue part); steps `gov-open-to-secure-after-founder-removed`, `gov-handoff-forged`, `gov-pool-copy`, `gov-clone-flagged`. | 1, P1 |
| 3 | Backup, recovery, succession | Backup nudge, recovery key and time lock, `cancel`, members-governed mode, banners. Step `gov-recovery-timelock`. | 1, P1 |
| 4 | Many pools on one device | `pools/<id>/` layout, pool context on every member request, one-identity standing check, per-pool beacons and discovery, `pool_create/list/status/leave`, join of a second pool, lone-pool absorption, `scope.pool` and the new grant registry, board-pool binding and session pool, `ph.pool` and CLI additions. Step `pool-two-on-one-device`. | 1 (2 for migration) |
| 5 | First-run bundle and plain refusals | The first-run bundle of standing grants, `NeedsApproval`, `say`/`needs`/`then` on every error, the `admin:<key>` pool scope. | 4, P1 |
| 6 | Onboarding steps (tracked metric) | Step ledger, `tests/onboarding-steps.json`, the `onboarding` step of `scripts/cold-start-check`, the five pool-check journey steps, the `onboarding-steps` gate. Tracked from here on: no later slice may raise it. | 5 |
| 7 | Reach across networks (Tailscale) | `tailnet.json` from the read-only status, tailnet detection states and the agent's sentences, the `tail` field of the signed beacon, `addrs` in `hello`, per-route addresses and dial order, the advertise rules (own interface and Tailscale's list), `route` and the new `reach_hint`s, the logging rules, `tailscale` steps in the step ledger. Steps `tailnet-*`. | 4, 5 |
| 8 | Join by tap and by link | Knock with three-word confirmation for secure same-network joins, join link and QR with address (a tailnet one when the other device is on another network) and pin, reachability messages. Journeys 2b, 2c. | 4, 5, 7 |
| 9 | Device capacity per pool | `pool` label and `quiet` flag on leases, per-pool limits and consent (`inbound_consent`, replacing `shard_consent`), pool-fair queue, usage accounting, `capacity {pool}`. | 4 |
| 10 | Shared boards | Charter and sides, rolls, replication filter and rights, freeze on leave, link rule, refusal of direct messages and private rooms, `board_rebind`, `ph.board` additions. Steps `board-shared-*`. | 1, 4, 5 |
| 11 | Serving offers and calls | Offers, the `serve` member op with streaming, `model_slot` and `gpu` leases, metering, isolation (slot erase), withdraw and stop. Steps `serve-two-pools`, `serve-not-offered`, `serve-stop`. | 9, P2 |
| 12 | Discovery and the endpoint | Offers beside acts in the member exchange, `ph.serve` additions, the loopback relay and `/v1/models`, `docs/api.md` and `tests/api-signatures.json`. | 11 |
| 13 | Live proof | `serve-live` with a real small model and real runners on the owner's devices; the journeys timed on a clean machine, and the real tailnet checks of section 10 on the owner's devices and phone. | 6, 7, 8, 12 |
| 14 | Sealed messages across pools | Lift the refusal of private rooms and direct messages on shared boards (`docs/pool-encryption.md`, slice 4). | 10 |

Order of arrival: P1 to P3 and slice 1 can start together; slices 2 and 3 follow slice 1; slice 4 follows slice 1; 5, 6, 7, 8 and 9
follow 4 (6 after 5, 7 after 5, 8 after 7); 10 and 11 follow their rows; 12, 13 and 14 last. The `onboarding steps` metric (slice 6)
is a ceiling every later slice must stay under, with and without Tailscale present.

## 12. Documents and words that change

* `docs/glossary.md`: **admin** (a person key that governs a pool), **reissue** (make a new pool with another policy and carry the
  kept members), **handoff** (the signed act that does it), **offer** (a device lets a pool's members use a named model), **share**
  (a person-signed charter by which pools hold one board), **roll** (the devices of one pool that hold a shared board for the
  other pools), **charter** (the signed document that names a pool's admins and policy, or a shared board's pools),
  **ticket** (an admin's one-use permission for a device to admit one new device), **delegate** (a device an admin lets sign
  routine acts for a time), **backup** (a second admin key). "A device alone is
  a pool of one" stays; add "a device may belong to several pools". `left` joins `revoked` as a recorded state.
* `docs/node.md`: the pool section becomes this document's governance; "Pools of two or more members do not merge" and "A device
  joins only if it is alone" are replaced by the lone-pool rule; the pool record's "newest change wins" is deleted.
* `docs/onboarding.md`: "Devices on other networks (Tailscale)" becomes this document's 4.4 (per-route addresses, the beacon `tail`
  field, the advertise rules); `docs/node.md`: discovery and reach (`route`); `docs/security.md`: the tailnet is a route and nothing
  more.
* `docs/api.md`: section 8's names; `docs/serving.md`: a section "Offering a model to pools"; `docs/test-farm.md`: consent per pool;
  `docs/pool-check.md`: the new steps; `docs/commands.md`: the new verbs.

## 13. Owner-only questions, and decisions taken

**Two things only the owner can do** (each spends, goes outward or cannot be undone):

1. **Migrating the real pool.** The owner's real pool is migrated once, at a safe boundary, by the owner's approval (1.14). It
   renames every device's pool id and rebinds its boards; it cannot be undone except by reissuing again. Needs the owner's tap, on
   a day the devices are all on. Nothing touches the real home before then.
2. **Tailscale accounts.** The owner decided on Tailscale and no relay of ours (2026-10-11). Crossing networks needs the owner's
   own Tailscale account, installed and signed in on each device (and the phone for approvals away from home); the real checks of
   section 10 need it, and the node-sharing check needs a second account. Registering an account is outward-facing and is the
   owner's; nothing here creates, pays for or changes one.

**Decisions taken here, with the risk the owner should know** (not questions; the better option is taken):

* New pools are `secure`; `open` is chosen only when the owner asks for devices to join by themselves, and the approval says
  "anyone on this Wi-Fi can join".
* One key signs sensitive acts for a one-person pool (no extra tap). Risk: a stolen, unlocked phone can reissue the pool until the
  owner removes its key. "Two keys of mine" removes the risk at the cost of a second device at hand for every sensitive act; it is
  one setting.
* A backup admin is a weekly nudge, not a setup blocker. Risk: if every key the person approved with is lost before a backup
  exists, the pool keeps working and nobody can change it (1.8).
* The recovery key alone is time-locked for 7 days, and the first-run bundle lasts 30 days with its renewal riding on any approval.
* External accounts for push belong to `docs/phone-approval.md`, not here.
