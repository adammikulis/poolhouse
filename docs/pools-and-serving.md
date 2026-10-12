# Pools, governance, serving and shared boards

Design, 2026-10-11. Slice 1 (governance core, section 11) is built in `app/poolhouse-node/src/gov/` and described as built in
`docs/node.md`, "Who governs a pool"; the rest of this document is not built. Where the code differs from the design as first
written, the text below says what the code does. It replaces the single-pool assumption of `docs/node.md` (the section on
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
| G3 | A member device can only leave. Only strong keys (per-signature presence) sign sensitive acts and removals; the recovery key signs no immediate act; every lever that takes a pool is held, and any strong admin cancels it. | A stolen file or an agent on the laptop signs nothing that cannot be undone; the hold costs the normal case nothing. |
| G4 | The join policy is immutable. Changing it reissues the pool under a signed handoff. | An in-place flip leaves every earlier joiner a member; a reissue keeps only reviewed ones. |
| G5 | A device follows a reissue only if the old pool's admin quorum signed it, with keys the device already holds, and it names this device. | A copy of a pool, or a stranger reusing its name, moves nobody. |
| G6 | No admin reachable: the pool keeps working and cannot be changed. Optional members-governed mode appoints admins after a long public delay. | No takeover by presence. |
| M1 | One device identity (one key, one certificate) is shown to every pool. Authority is decided per request and per pool. | One listener and one handshake; separate certificates would not hide a device on one network and would cost a handshake per pool. |
| M2 | A board belongs to one pool unless the pools' admins sign a charter that shares it; sharing is by named devices, whole board, never by "all members". | An open pool's members are strangers; a shared board's contents go only to devices a person named. |
| M3 | A link never carries entries to a pool that is not already a participant of the source board. | Links are node-local and would otherwise route around the sharing charter. |
| L1 | Leases stay per device. The GPU is one holder at a time across all pools; pools share it by least-recently-served first. | AGENTS.md hard floor; fairness without a second lock. |
| L2 | Inbound model calls are off for every pool until the device's owner turns them on for that pool, with safe limits. Running another device's code (tests) keeps its per-device allow list, empty except for the owner's own devices. | Being in a pool is not being allowed; code execution is not a quota. |
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
   never readable by an agent), and for an act that cannot be undone a strong one (1.3). The way a person gives it, on a phone or a screen, is `docs/phone-approval.md`'s, not this document's.
6. Security is invisible in the normal case. Section 1.12 lists every protection and what the person feels.

### 1.3 Keys: roles, strength, thresholds and the delegate

An **admin** is a public key named in the pool's charter, with the **algorithm** it signs with: `ed25519` (64 lower-case hex digits) or
`p256-ecdsa` (an uncompressed P-256 point, 130; ECDSA with SHA-256, signatures in DER, as a phone's hardware key makes them). The algorithm is
the one the key's encoding says, is named by the charter entry and by every signature, and is inside the bytes the key signs to accept
being listed; a signature that names the other algorithm is refused whichever way round, so one is never checked as the other, and a device
signs with Ed25519 only (`gov/alg.rs`). Every entry carries the key's **own acceptance**: a signature by
that key over the pool, the epoch, its algorithm, its role and its strength. A key is therefore listed only by its holder: adding a key needs the
added key's signature as well as the admins' (1.5), so nobody can list someone else's public key to borrow its name or its
standing grants. A person decides on a key by its **fingerprint as three check words** ("maple-river-ten"); the label an adder wrote
("the owner's phone") is untrusted text, quoted and styled apart wherever it appears, and is never what an approval rests on.

**Strength.** A key is `strong` when the platform enforces a per-signature presence that the signing process cannot supply for
itself: a phone's biometric or device credential, a hardware key's touch, a hardware-backed key with a presence prompt. It is `weak`
when any process of the user's account can use it: a keystore item readable after login, a key file, a `yes` typed at a terminal a
process can open for itself (the limit `docs/node.md` states under "Key custody"). The strength is declared when the key is
enrolled and written in the charter; keys enrolled through the phone path are strong (`docs/phone-approval.md`). **Only strong keys
count toward sensitive acts, toward removing a member and toward cancelling.** A weak key may sign only the end of a delegate (a
reduction of power); it counts toward nothing else, because it proves less than that a person was there. `pool_status` says "weak keys only" for a pool with no strong admin. **What is strong today:** the key of a phone added to this node that showed an Android Key Attestation chain (`phone/attest.rs`): every
certificate signed by the next, the last at a root the node trusts, the first about the key being added and made for this addition, and
the key made in hardware, P-256, with the person's fingerprint needed for every signature (no use without authentication, no time window).
A phone that shows less is added, and approves what it is asked, but its key is weak and founds nothing. **Until the owner fills in Google's
published attestation roots (`GOOGLE_ROOTS` in `phone/attest.rs`, empty and marked) no real phone is strong.** Not checked: Google's
revocation list and the validity dates of the chain. A keystore key of the account, or a key kept in a file or put
in by a typed yes, is `weak`: any process of the account can use it after one prompt.

**Roles.** `admin` signs and counts as above. `recovery` is a key kept offline (a phrase the person writes down, or a second phone
put away). **The recovery key counts toward no immediate threshold, routine or sensitive**: the phrase alone can never sign an
admin change or a reissue, so its time lock cannot be skipped. It signs exactly three acts: `recover` (time-locked, 1.8),
`recover_cancel` for its own pending recover and `recovery_replace` (time-locked like a recover). It is removed or replaced only by
its own signature, or by a reissue, which every device holds for its own person when the admin set changes (R6). A thief holding an
admin key therefore cannot quietly strip the recovery path.

**Thresholds** count distinct people, each by a strong key. Two classes:

| Class | Acts | Default | Choose at creation |
|---|---|---|---|
| `routine` | remove a member, cancel a pending act, end a delegate | 1 | 1..n |
| `sensitive` | add or remove an admin, change a threshold, a delay or the mode, reissue the pool, sign a board charter side, make a delegate | 1 while the set holds one person; 2 once it holds two people | 1..n, counted in people or in keys |

Counting people means one person with a phone and a security key signs a sensitive act with either: the normal case is one tap.
The owner may choose "two keys of mine" at creation or later (`sensitive: {m: 2, unit: "key"}`). Adding the first key of another
person asks one plain question in the same approval ("Should changes like this need both of you?", default yes).

**What a one-person pool does.** Its strong key is the phone. The laptop's keystore key is weak: it signs nothing that matters, so
an agent on the laptop cannot sign as the person. While the phone is the only strong key, sensitive acts and removals are the phone's
alone; if the phone is lost the pool keeps working, and changes wait for a second strong key added earlier (a second phone, a
hardware key) or for the recovery key's time-locked `recover` (7 days). The backup nudge (1.8) asks for exactly that second strong
key or the phrase. The risk is written for the owner in section 13.

**The delegate.** A **delegate** is a device that an admin-signed `delegate` act lets sign `admit` (and nothing else) for 30 days,
at most 3 admits an hour and 10 a day (every member counts the admits it first saw and refuses the rest). Rules:

* It exists only where the device key is non-exportable in the operating system's keystore (`docs/pool-encryption.md`, slice 3,
  prerequisite P3). Where the key is a file there is no delegate and every admit is a tap, so a copy of a founder's state directory
  carries no delegate power.
* Its signature is a device signature and is shown as one ("by the delegate of maple-river-ten"): nothing is signed in a person's
  name, so the floor that an agent never acts as a person holds.
* **Every admit it signs notifies the phone** ("'guest-laptop' (tide-oak-nine) joined 'home'. [Remove]"), invisible unless acted
  on, and any strong admin ends the delegate at once.
* It renews by its own stated approval (below), never inside another tap.

**The first-run bundle and its renewal.** The standing grants of the first-run bundle and the delegate last 30 days. Renewal is
**its own approval**, worded as a renewal ("Keep your agents' permissions for another 30 days?"), nudged a week before expiry and
never folded into an unrelated tap, so a person never signs a permission they were not shown. It costs one tap a month (counted
in section 3.5).

### 1.4 The charter and the pool id

`pools/<id>/charter.json` holds the current charter, signed by the admins of the previous epoch (the founding charter by the
founders, each with its acceptance):

```
{ v: 1, epoch, name, policy: "open"|"secure", mode: "admins"|"members", project: "" | <project key>,
  admins: [{key, role, strength, acceptance, label}],
  thresholds: {routine, sensitive, unit: "person"|"key"},
  holds: {admin_add_h: 24, admin_remove_h: 24, handoff_min: 15, dispute_d: 7},
  recover_delay_days (1..30, default 7), succession: {quorum, delay_days, min_key_age_days: 30} | null,
  from: null | {pool, handoff_hash}, at_ms }
```

The **pool id is the first 16 bytes of the SHA-256 of the epoch-1 charter body with every acceptance blank**, 32 hex digits, so no signature changes it. A copy of a pool with different
admins has a different id, and a stranger cannot make a pool with this id and other admins. The policy is part of the charter body
and does not change inside a pool. The epoch rises when the admin set, a threshold, a hold or the mode changes; a reissue makes a
new pool, not a new epoch. A **weakening** is any change to a smaller threshold, a shorter hold or delay, `mode: members`, a
relaxed strength rule, or `policy: open`; everything else strengthens or is neutral.

A pool has no key of its own and no secret shared by its members. Nothing in a member's files can be copied to govern the pool.

### 1.5 Acts

Every act is `{v, pool, epoch, kind, body, at_ms, nonce, prev, signatures: [{kind: admin|device, key, sig}]}`, signed over
`poolhouse/pool-act/v1` and a line break and the canonical JSON of everything but the signatures. `prev` is the hash of the charter in force that the
signer knew (an epoch's charter is derived from the acts, so its hash is the same on every device that applied the same acts, whatever order a
hold ended in). The member record (`roster.json`) is a view folded from the acts; it is not an input and a `members`
row is never believed again. A **hold** is a delay before an act takes effect, in which any strong admin may `cancel` it.

| Kind | Signed by | Effect | Hold, replay, limits |
|---|---|---|---|
| `found` | the founding keys, each with its acceptance | the epoch-1 charter | the id is its hash; once |
| `admit` | secure: a strong admin after the join rules of 1.6, or the delegate for a trusted-path join; open: any active member's device key, `via: segment` | adds the certificate, with `how` and `by_key` | idempotent; refused for a removed certificate; delegate 3 an hour, 10 a day; an open member's device 5 an hour |
| `remove` | one strong admin | the certificate is `revoked`, sticky; carries a log cut (below) | idempotent; immediate |
| `leave` | the device itself | status `left`; a later `admit` may undo it | idempotent |
| `admin_add` | sensitive threshold of strong admins **and** the added key's acceptance | the key joins the admin set | effect after `holds.admin_add_h` (24 h) |
| `admin_remove` | sensitive threshold of strong admins other than the removed key; carries a cut | the key leaves the admin set | effect after `holds.admin_remove_h` (24 h); the removed key may `veto` |
| `veto` | the key a pending `admin_remove` names, by its own strong signature | cancels that removal and opens a dispute | once per removal |
| `cancel` | one strong admin | ends a pending `admin_add`, `admin_remove`, `handoff`, `recover` or `succession` | idempotent |
| `set_threshold`, `set_holds`, `set_mode` | sensitive | the new epoch | a weakening takes effect after the longest hold and shows a banner |
| `delegate`, `delegate_end` | sensitive to make; routine, and a weak key may sign it, to end | names or ends a delegate | idempotent |
| `recover`, `recover_cancel`, `recovery_replace` | the recovery key alone | admin replacement or key replacement (1.8) | after `recover_delay_days`; one pending at a time |
| `endorse` | sensitive | names a member's owner key as eligible for succession | counts after `min_key_age_days` |
| `succession` | endorsed owner keys (1.8) | admin replacement, only `mode: members` | after its delay; one pending at a time |
| `handoff` | sensitive | reissues the pool (1.9) | followed after `holds.handoff_min` (15 min); nonce once |
| `board_side` | sensitive | one pool's side of a shared board's charter (section 6) | versioned by the board charter |

**How a member accepts an act.** The pool and epoch are its own; every signature verifies; the distinct people (or keys, under
`unit: key`) among the signers that hold a **strong** admin key of that epoch reach the class's threshold (the recovery key counts
for none); a `device` signature is accepted only where the table says so, and each one by a device that may give it (its own `leave`, alone;
an `admit` from a live delegate, or in an open pool from a member that says `segment`), so a member that relays an act with its own
signature added changes nothing; an act names each signer once; `at_ms` is not more than five minutes ahead of its clock. An admin
act that follows a charter the pool has left behind is refused, and so is an act signed only by admins that are no longer listed
unless the cut of their removal names it.
Anything else is refused and recorded with its reason, never applied. Heartbeats (1.6) travel in the same exchange and are not
stored.

**Time.** Every hold, delay, window and expiry is measured on **each device by its own
monotonic clock from the first moment that device had the act with every signature it needs** (an act signed by one of two required people
and completed three days later is held from the completion, so a thief with all the signatures but one gets no head start), saved at
checkpoints and across restarts. An act that is complete on a device only after the first act of its step has run its hold arrives too
late to join or contest that step there, so a late arrival cannot reopen a step that is already in effect. The wall clock and the
signer's `at_ms` are never read for a delay: an act backdated by its signer, or a device whose clock a network source moved
forward, shortens nothing. A device counts only the time it is awake, so a hold never ends early for a device that was off: it ends
after the full hold has run on that device with no `cancel` seen. The acts a device takes from the member that let it in as already
in effect (their holds ran before it came) can still be cancelled until their hold has run from the moment it joined, so a host that
withheld a `cancel` does not make a joiner's view final. The order of a device's admits and leaves, and of `delegate` and
`delegate_end`, is the order this device first saw them.

**Order, forks and last admin.**

* Removals commute: the set of removals is a union, whatever the order, and a removal that has held and was not vetoed applies on
  every device.
* An add counts only if its signers survive: after the settled removals are applied, an `admin_add` whose signers no longer meet the
  threshold among the surviving keys is void, and the check repeats until nothing changes.
* The last-admin rule is checked **after** the fold: when the settled removals together would leave fewer strong admins than the
  sensitive threshold needs, none of the conflicting removals applies, a dispute opens, and the pool shows it.
* Two acts that conflict and cannot both hold (two handoffs, two admin sets with one `prev`) are settled by no tie rule that a
  signer could grind: first by the larger number of distinct strong signers, then by the lower fingerprint among the signers (a
  fixed identity, not a value anyone can search for); if they are still tied, neither applies, a dispute opens and the signers
  sign one of them again.
* A **dispute** suspends admin-set acts, handoffs, board sides and `set_*` (not removals of members, leaves or admits) for
  `holds.dispute_d` (7 days) or until the contested act is cancelled; after the period the contested act is dropped. Every device
  and phone shows "Two admins disagree about 'maple-river-ten'. Changes to who runs this pool are paused." Only `recover` (1.8)
  can change the admin set during a dispute.

**Removing a key carries a cut.** An `admin_remove` lists the hashes of every act by that key the remover has seen; an act of that
key that is not in the cut is void on every device, whenever it arrives. A stolen key cannot keep a signed act in a drawer and
release it after the key is removed.

**Removing a device carries a log cut.** A `remove` lists, for each log of boards that the removed device wrote and the removing
admin's device holds, the board, the origin and how many entries of it stand (at most 256 logs; the node fills it in and ignores
what a caller sent). Every device that learns of the removal cuts those logs there and takes no later entry of them, from the removed
device or from any member that relays it; an entry carries its own signature, and a relay proves nothing about it. A log of the
removed device that no removal names stands as far as the device holds it, and no log is started under its key. Several removals of
one device stand at the least they name. What the removed device admitted (as a delegate or an open pool's member) goes with it, and
an admin may admit those devices again. A member that has not heard of the removal can still hold and pass on later entries until it
does; the others refuse them.

**Limits and paging.** Acts replicate in the `acts` member op in pages of at most 256 acts (`{pool, have: <tokens of the last 4,096 acts held>,
page, push}`, a token being an act's id and a tag of who has signed it, so a signature added later to an act already held is passed on),
each act at most 16 KiB, a page at most 2 MiB so it fits the 4 MiB frame. Caps per pool: 4,096 `admit` entries that stand, 256
admin-set acts, 8,192 acts, 1 pending `handoff`, 1 pending `recover`, 1 pending `succession`. What a member can send in bulk, the
device-signed `admit` that stands nowhere, is held 32 of one device's and 1,024 in all, a device's own oldest first, and goes before
anything else when the pool is full; a device holds at most eight of its own `leave` and signs one only while it is a member. A device
that takes a push is folded once for each time an act that follows needs what an earlier one changed (the admin set, a device just
admitted), and a request that changes nothing writes nothing. There are no stored tickets: a join window is
ephemeral and lives in the memory of the device that opened it, and the delegate's `admit` is the only trace. Rates: knocks 5 a
minute per source address and 30 a minute in all, at most 3 pending; one pending sensitive approval per pool; after a decline the
same kind of request waits 10 minutes (slice 1 keeps this per device and kind, since it has no session of the phone to name).
A device that catches up after being off counts the admits it first sees at once, so it refuses those past the rate of a delegate
or an open member that others took when they came; that is the cost of counting by first sight.

The last admin cannot be removed, nor the set left below the sensitive threshold; ending a pool's governance is a reissue.

### 1.6 Joining, leaving and removing

**The secret is shown by a trusted display, not by an agent.** A join window's code, QR or link is shown only by the node's own
screen or the phone app and is never returned to the agent that asked ("I opened a window; your phone shows the code"). Two
paths follow, and they differ because of who held the secret:

* **Trusted-path join.** The person scans the QR or types the six digits **into the new device's own app**. The scan or the typing
  is the approval, the secret never passed through an agent, the delegate (or a strong admin) signs `admit`, and the phone shows
  the notice with [Remove]. No tap. This is the normal case for a person with the app on both devices.
* **Agent-in-path join.** The person told an agent the link or the code on the new device, or the new device **knocked**. Both
  nodes compute **three check words** from the key-exchange transcript and show them on their own screens or on the phone, never
  relayed by an agent; the person compares them and taps once. A strong admin signs `admit` after the tap. The one-line reason this
  tap cannot be invisible: **an agent holds the link**, so only a person looking at two screens can tell the device that
  completed the exchange is the one in front of them.

This resolves the apparent contradiction between "add a device by saying so" and "a tap with words": the words and the tap are the
price of letting an agent carry the secret, and a person who scans or types it themselves pays nothing.

* **Knocks** are rate-limited (1.5) and the device name is untrusted text (40 characters, quoted); the check words are the identity.
* **Open pool.** A device on the same network segment is admitted by whichever member heard it, with that member's device key,
  after the member's own check that the address is on its segment (`via: segment`; others cannot check it, so the rate cap bounds a
  compromised member). Nobody is asked. `docs/sentinel-open-join.md` still applies. An open pool is joined only from the segment,
  never over a tailnet (4.4), cannot be members-governed (1.8), and its devices are never carried over or shared with another
  pool without a review (1.9, 6.2); an offer to it needs a plain warning (7.2).
* **Removing a member** is a `remove` by a strong admin: one tap naming the device ("Remove 'guest-laptop' (tide-oak-nine) from
  'home'? It stops working with every device in the pool at once."). It is sticky: a removed certificate never returns; a device comes
  back with a new certificate, which is a new identity in every pool it belongs to. That is why it is a tap.
* **Leaving** is a `leave` signed by the device itself and needs no admin.
* **A copied device.** Every device signs a heartbeat `{fingerprint, boot id, counter}` every minute with a counter it persists
  before sending. A member that sees two boot ids alternate under one fingerprint, or a counter going backwards, has seen a clone:
  it flags it to the admins ("Two devices answer as 'guest-laptop'; it may have been copied. [Remove it] [It's fine]") and the
  fingerprint takes no new offers or tests until the person answers. Device keys move into the operating system's keystore where one
  exists (prerequisite P3), so a file copy carries no identity; a copy of a pool's files without a device key is a stranger.

### 1.7 What a member still decides alone

A device's owner decides what that device does: whether it takes model calls from a pool (section 5), what it offers (section 7),
what its sessions may do (grants, unchanged). These are authorised by a grant that a person key enrolled on that device signed
(`persons.json`), not by the pool's admins. An admin cannot offer someone else's device; the pool's removal of a device ends all of
it at once. The owner may also set a pool to `follow: ask`, so that every move of that pool (1.9) waits for the owner's tap.

**Running another device's code is not a quota.** Test shards (`docs/test-farm.md`) run a sender's code on this device as this user,
so they keep their **per-device allow list** (`shard_consent`), which is **empty except for the owner's own devices** (the devices
admitted by a key that is enrolled on this device) and is **never widened by `inbound_consent`, by the first-run bundle or by being
a member**. Adding a device to it is its own approval that says so in plain words ("This lets 'guest-laptop' (tide-oak-nine)
run its own code on this computer, as you."), one tap, for that device only.

### 1.8 Backup, recovery, succession and total loss

**Backup is a nudge, not a setup blocker.** After the first successful use, the agent says once a week, in one sentence, "Your
pool has one strong key; add a backup so losing this phone does not lock you out", with the choices: a second phone, a hardware key,
a trusted person's phone, a recovery phrase to write down. Risk written down for the owner: until a second strong key or a phrase
exists, losing the phone freezes sensitive changes and removals (the pool keeps working; the laptop's weak key cannot stand in).

**The recovery key** is the admin key of role `recovery` (1.3). Its holder alone can sign `recover {new admin set}`, which takes
effect after `recover_delay_days` (default 7) counted on each device from its own first sight of the act (1.5). Every device and
phone shows a banner with the countdown ("Pool 'home' will get new admins in 6 days unless a current admin cancels it"). A pending
`recover` is cancelled by any strong admin or by `recover_cancel` from the recovery key itself; a cancel by an admin is itself
shown on every device. The pool with enough strong admins for the sensitive threshold replaces the admin set at once by an
ordinary `admin_add` and `admin_remove` (with their holds); the recovery key alone is the only path that works with no strong
admin left. A stolen recovery phrase lets a thief start a countdown that any live strong admin cancels and the phrase's holder
cannot shorten; with no admin alive it is the total-loss case below and the phrase is the pool's last key.

**Members-governed mode** (chosen at creation, `secure` pools only, off by default). A device cannot know that every admin is gone
(they may be asleep, abroad or in a drawer), so the mode never infers it: it offers one route, a `succession` that is a time-locked
proposal like `recover`, cancellable by any strong admin at any time during the delay, shown on every device and phone from the
first moment, and valid only if signed by enough **endorsed** keys. An owner key is eligible only if a sensitive `endorse` act named
it and 30 days have passed since each device first saw the endorsement (`min_key_age_days`), so a device that joins today cannot
vote today. The quorum is two thirds of the endorsed keys and at least three of them, the delay is 30 days by default, members may
withdraw their signature, and falling below the quorum cancels it. The mode exists so a pool of friends outlives its founder; the
default stays admins plus a recovery key because a majority of members is the group an attacker joins.

**Total loss** (no strong admin key, no recovery key, not members-governed): the pool keeps working unchanged. Members keep
syncing, serving and testing; in a `secure` pool nobody can admit a device; in an `open` pool devices keep joining by themselves,
since open admission never needed an admin. Nobody can remove a member, change an admin or reissue. The plain message to a member's
agent is "This pool has no admin left, so it can't be changed. You can start a new pool and join it; your boards can come with
you." A successor is a new pool made by a new admin; each member device joins it by its owner's own approval; each device owner
may move its local copy of a board to the new pool (`board_rebind`, a device-owner act, 6.8). Members can never take the old pool
over by being there.

### 1.9 Changing the join policy is a reissue

`set_join_policy` is removed. The node answers it, in a governed pool, with `invalid: A pool's join policy never changes in place;
reissue the pool to change it.`, whatever grant is held; a legacy pool (1.14) keeps its local, grant-guarded `set_join_policy` until
it is migrated, because removals and the policy are the local API's alone there. To go from open to secure (or the reverse) an admin reissues the pool.

**The agent flow** (the owner says "make this pool secure"):

1. The agent calls `pool_reissue {pool, policy: "secure", keep: "reviewed"}` (`ph.pool.reissue(...)`). The node builds a draft: the
   new charter (same admins unless the agent names others, same thresholds and holds, `policy` as asked, `from` the old pool) and a
   **review list**: every active device with its name (untrusted, quoted), its three check words, last seen, how it joined (`open`,
   `join-code`, `tap`, `self`, `handoff`) and whether this node has itself connected to it.
2. The draft becomes one approval on the person's phone or screen: "Make 'home' secure? Keep: three devices that joined by tap or
   code. **Dropped unless you keep them:** 'laptop-77' (tide-oak-nine) joined 3 days ago without a code." **A device that joined
   while the pool was open, or that this node never connected to itself, defaults to Drop**; "Keep all" is one button. The text is
   the **canonical charter diff** (admins added or removed by check words, thresholds, holds, mode, policy, the kept and dropped
   lists) that the phone renders from the bytes to be signed, so no word of it is the asker's. The person's edits are made before the
   signature and the signed bytes are the final list.
3. The strong admin quorum signs the handoff (sensitive threshold). Secure to open is the same with `downgrade: true`, the plain
   warning "Anyone on the same network will be able to join", and the same review.
4. The node writes the handoff as an act of the old pool. The old pool is marked `moving`: it admits nobody, issues no delegate and
   takes no new offers, and it goes on syncing and serving for members that have not followed yet until each has.
5. The act replicates through the old pool's `acts` exchange to every member. After the hold (15 minutes, counted on each device, 1.5)
   each kept device follows without any person; any strong admin may `cancel` during the hold ("A reissue of 'home' was signed
   at 3:02. [Cancel]" is on the phone from the first minute). A dropped device never follows and learns it was not kept.

**The handoff** (`kind: handoff`, signed over `poolhouse/pool-handoff/v1`):

```
{ old: {pool, epoch, charter_hash},
  new: {charter: <the full new charter body>, pool: <its hash>},
  keep: [{fingerprint, name}...],          // every kept device, this device included
  carry: {boards: [board ids] | "all", offers: [{device, model}...]},
  downgrade: bool, nonce: 16 random bytes, at_ms, window_days (default 14),
  signatures: [strong admin signatures of the old epoch] }
```

**A device follows if and only if** (each is a test, section 1.13):

* R1. The handoff reached it inside the old pool's authenticated `acts` exchange, from a member (who relays; who relays proves
  nothing), and the device verifies everything itself. A beacon, a name, an id, a connection from a non-member, or another pool
  using the old pool's name moves nobody.
* R2. The signatures verify under the strong admin keys of the old pool's current epoch **as this device already stores them**,
  and reach the sensitive threshold (the recovery key counts for none).
* R3. `old.pool`, `old.epoch` and `old.charter_hash` equal what this device holds.
* R4. `keep` names this device's fingerprint.
* R5. `new.pool` is the hash of `new.charter`; the old pool is not already retired; the nonce is unused; `at_ms` is not ahead of
  the device's clock by more than five minutes; the **hold has elapsed on this device's own clock from first sight with no valid
  `cancel`**; and the **window** (`window_days`, counted from first sight on this device) has not passed.
* R6. The move is held for this device's own person ("Move this device?", with the canonical diff) whenever the new charter has a
  **different admin set** or any **weakening** (1.4): a smaller threshold, a shorter hold or delay, `mode: members`, a relaxed
  strength rule. A strengthening or neutral handoff follows silently.
* R7. `downgrade` matches the charters: a handoff to a weaker policy without the flag is refused, and a downgrade is held like R6.
* R8. If two handoffs of the same old pool are pending, the rule of 1.5 settles them (more strong signers, then lower signer
  fingerprint, else neither applies and a dispute opens).

**On verifying, before any hold: every device outside `keep` is suspended on this device.** The moment this device has verified R1 to
R4 (signatures, hash, keep list) it refuses requests from each device the handoff does not keep, in the old pool and every other
pool context, so a dropped or unreviewed device stops getting anything in minutes rather than after the window. A valid `cancel`
lifts the suspension; nothing is removed by it.

A device that follows (all done by the node, no person):

* creates `pools/<new>/` with the charter and a roster of the `keep` list (`by: handoff`);
* rebinds every board the handoff carries from the old pool to the new one (history untouched; a board shared with other pools
  asks those pools, 6.8);
* ends every lease and shard of the old pool with the plain error "pool moved", withdraws every offer it made to the old pool, and
  re-makes exactly the offers the handoff lists that it already had (never an offer the handoff does not list, never a new one);
* moves `pools/<old>` to `pools/retired/<old>` (kept for the record, never read for authority). For 90 days, counted on that device's
  own clock, a retired pool **serves its full act log** (so a device that missed the handoff can verify the whole chain from the
  keys it holds) and answers every other op with "this pool moved";
* dials the other kept devices at the addresses it already knows and exchanges the new pool's acts.

**The device that missed it.** Within the window every kept device follows as soon as it reconnects and the hold has run. After the
window, or when the person set the device to `ask`, the device shows the plain step "This pool moved. Approve the move?" with the
canonical diff, on the owner's phone or screen. That approval is made by a person key enrolled on **this device**, never by an agent,
and **re-verifies the handoff's signatures, the old epoch and the keep list (R2 to R4, R7), not the window**; it has its own bound,
180 days from the first sight of the handoff on this device, after which the old pool is treated as gone and the device joins the
new pool afresh. It is a sensitive approval with a warning if the admins differ (R6). The signed handoff is fetched from any kept
device that still serves the act log; a device off for longer than that serving period re-joins.

**What carries over, in one line each:** members (the keep list); boards and their history (rebound, so a board id and its entries
stay; shared boards continue by 6.8); standing grants (scoped by pool id, so a grant for the old id stops counting; the first-run
bundle's `admin:<key>` scope covers the new pool only if that key accepted it, 1.3); delegates (a new `delegate` act in the new
charter, same approval); leases (ended, then queued again by their holders); served models and their offers (only those the handoff
lists); quotas and consents (per pool, so they must be made again: a pool the owner has not allowed takes no work, which is the
safe direction); the pool's name (same). **Pool id continuity:** the id does not continue (a new charter is a new hash), and the new
charter's `from` names the old pool and the handoff, so `pool_status` shows the lineage and a board's history stays whole across it.
The old pool is retired for good: it cannot be re-activated, a copy of it moves nobody, and an act for it is refused by every device
that followed.

**Person feels:** "make this pool secure", one approval with a review list whose defaults are already right (open joiners dropped).
Reason it is visible: the list is the whole point, since an unreviewed open joiner is exactly what must not be carried.

### 1.10 Pool copy and takeover

**Do devices ever migrate into a copy of a pool? No.** A copy of a pool's files, a cloned member, a pool that reuses the name or the
id, or a beacon announcing a move has no admin signature, so it moves nobody. The seven rules:

1. A device follows a move only by R1 to R8: the handoff is signed by the old pool's strong admin quorum with keys the device
   already holds, and it contains the new pool's id and charter (so its admin set, policy, thresholds and holds), the exact kept
   list including this device, a nonce, a window and the old pool's epoch.
2. The new pool's admin set must be the old one or a rotation signed in the same handoff by the same old quorum; a handoff that names
   other admins than the quorum signed is a different byte string and its signatures do not verify. A rotation, and any weakening,
   is held for the device's own person (R6).
3. A device never follows a move announced by a non-admin, by a beacon (beacons say a pool exists, not that it moved), by a name or
   an id, or over any connection but the old pool's own authenticated member channel.
4. The "approve the move" step is the person's, on this device, shows the canonical diff and who the new admins are, and is a
   sensitive approval when they differ.
5. A member that copies the pool's files or its own device key to another machine gets a clone, flagged to the admins within a
   minute (1.6). A copy of pool metadata without an admin key can never govern: it cannot sign an act or a handoff.
6. **Compute never goes to an attacker's pool.** A device offers models and takes work only for pools its own owner approved, per
   pool and per device. A new pool's offers and consents are empty until that owner's grants make them again (for the owner's own
   pools the first-run bundle covers a pool the owner's key administers *and accepted*, so the agent re-offers without a tap; for any
   other pool it is one tap), unless the signed handoff lists an offer the device already had. An offer is never created by a copy,
   a handoff that does not list it, or the other pool's admins. Code execution has its own allow list that no handoff touches (1.7).
7. Each case above is a test that leaves every device in the original pool (1.13).

### 1.11 Takeover analysis

| Attacker | What it can do | What stops the rest |
|---|---|---|
| A malicious or compromised member device | Read what the pool's boards hold, call models offered to the pool, leave. It can no longer add devices, remove members, set a policy, forge a handoff or enrol a stranger. | Section 1.1's three holes become tests that must fail: each is an act it cannot sign. |
| A member that relays forged or stale acts | Nothing: acts verify by signature, epoch and device-side time, not by sender. | R1 to R8. |
| A stolen phone, unlocked, used in one burst | Signs what its strong key may. | Every lever that takes a pool is held: a new admin counts after 24 hours, a removal of another admin after 24 hours (the removed key can veto and freeze the pool), a reissue is followed after 15 minutes and any other strong key cancels it, and the phone of the owner shows a banner for each from the first minute. **Residual:** when that phone is the only strong key nobody can cancel, so a burst of three signatures can still take the pool; the second strong key or the phrase is the mitigation, and the nudge exists for it (section 13). |
| An agent or any process on the laptop | Cannot sign: the laptop's key is weak. It can ask the node; the node needs the phone. | Section 1.3 (strength); the floor that an agent never acts as a person. |
| A stolen laptop with a hardware-backed key | Only after the presence prompt, which a thief at a logged-in screen may satisfy. | As the stolen phone, and OS disk encryption (`docs/pool-encryption.md`, out of scope). |
| A thief holding only the recovery phrase | Starts a `recover` that every device shows for 7 days and any strong admin cancels; cannot sign anything immediate. | Section 1.3 (counts toward no threshold), 1.8. |
| A key listed to spoof an `admin:<key>` scope | Nothing: a key is listed only with its own acceptance. | Section 1.3, 1.5. |
| A signer who backdates an act, or a source that moves a device's clock | Shortens no hold: delays count the device's own monotonic time from first sight. | Section 1.5 (time). |
| A rogue co-admin | Removes members, proposes admin changes. | The hold and the veto; another admin cancels; a two-person pool needs both people for sensitive acts. |
| Someone on the network under `open` | Joins an `open` pool. | That is what open means; it cannot be members-governed, shared or offered to without a review and a warning, and a reissue to secure drops it by default. |
| An agent that holds the link | Cannot join a device of its own choosing: the check words differ on the two screens. | Section 1.6. |
| A knock flood or an open-admit flood | Fills a queue. | Rates and caps of 1.5. |
| A cloned founder state directory | Has no delegate power (the delegate needs the keystore-bound device key); a clone is flagged by its heartbeat. | Section 1.3, 1.6. |
| A majority of members in members-governed mode | Proposes new admins after 30 days. | Only endorsed keys at least 30 days old count; notice from day one; any strong admin cancels. |
| A cloned device key | Is a member as the original is. | Heartbeat detection, one-tap removal; keystore binding where one exists. |
| Hash or nonce grinding | Cannot win a tie. | Ties are settled by signers' counts and fixed fingerprints, else by a dispute (1.5). |
| A legacy `by` field written by an attacker | Nothing: only devices this node itself handshook with are kept by default. | Section 1.14. |

### 1.12 What the person feels (security without friction)

| Protection | Normal case | A visible step, and why it cannot be invisible |
|---|---|---|
| Admin acts are signed, members verify them | Nothing | none |
| Strong-key rule | Nothing: the phone is the strong key | none |
| A join by scanning or typing on the new device | Nothing beyond the scan or typing, and a phone notice with [Remove] | none |
| A join through an agent or a knock | One tap with three check words | An agent holds the link, so only a person looking at two screens can tell |
| Holds and cancels | Nothing: a banner on the phone, never a step | none |
| Sensitive acts (admin change, reissue, board side) | One tap with a rendered diff | Irreversible or a disclosure; a signature a person did not see is the thing prevented |
| Removing a member | One tap | Sticky and takes a device's identity from the pool |
| Following a reissue | Nothing: kept devices move by themselves | Only when the admins changed or a protection weakened (R6), or the device missed the window |
| Review of members | One tap; open joiners default to Drop | Open joiners are the risk |
| Clone detection | Nothing | A flagged clone asks one tap; only a person can tell which copy is real |
| Backup admin | A weekly one-line nudge, never a blocker | Optional; the risk is written in 1.3 and 13 |
| Renewal of the bundle and the delegate | One tap a month, worded as a renewal | A permission that lasts longer than shown is the thing prevented |
| Offers, calls (per pool) | Nothing for the person's own devices; one tap the first time per other person's device | Prompts leave the device, or foreign work comes in |
| Code execution by another device | One tap per device, stating it | It runs code as you |
| Isolation, slot erase, limits, rates, pages | Nothing; first token slower when pools alternate | none |

### 1.13 Tests for governance (written first, each seen to fail)

Rust, `app/poolhouse-node/tests/governance.rs` and `reissue.rs`. The numbers in brackets are the review findings of 1.15.

* the three holes of 1.1 as tests that fail today: a plain member's `members` request cannot remove the founder, cannot add a
  certificate and cannot move the policy;
* a member without an admin signature cannot remove, admit (secure), change admins or reissue;
* a forged act (wrong key, wrong pool, wrong epoch, bad signature, signature of a removed key) is refused and recorded; a replayed
  act is a no-op; a stale epoch is a no-op; a signature counted twice counts once;
* threshold enforcement: a sensitive act with one of two required people is refused; one person with two keys counts once under
  `unit: person` and twice under `unit: key`;
* [1] the recovery key alone signs no `admin_add`, `handoff`, `remove` or `admin_remove` at threshold 1; its `recover` takes effect
  only after the delay measured on the device's clock from first sight, whatever `at_ms` says (a backdated `recover`, and a device
  whose wall clock is moved a month forward, do not shorten it); a strong admin's `cancel` and the key's own `recover_cancel` end it;
  removing the recovery key needs its own signature or a held reissue; an `admin_remove` takes effect only after its hold and the
  removed key's `veto` cancels it and opens a dispute that pauses admin-set acts;
* [2] an `admin_add` without the added key's acceptance is refused; a pool cannot list another person's public key; the scope
  `admin:<fp>` matches a pool only where the key accepted;
* [3] a weak key counts toward no sensitive act, no removal and no cancel; a pool with only weak keys is flagged and changes
  nothing sensitive; the one-person pool with a lost phone keeps working, rejects sensitive acts from the laptop key and accepts a
  `recover` after its delay;
* [5] the delegate does not exist without a keystore-bound device key; a copied state directory signs no `admit`; each delegate
  `admit` produces a phone notice; the fourth admit in an hour is refused by every member; a join through an agent needs the check
  words and a tap and fails when the words differ; a knock flood is capped; a device name with control characters is shown
  quoted and truncated;
* [7] removals commute; an add whose signer is removed is void; the last-admin rule holds after the fold; two competing handoffs
  with equal signers neither apply and open a dispute; the tie rule does not change when a nonce changes;
* [12] an open `admit` without `via: segment` is refused; a flood of acts is paged within the frame and capped per kind;
* tickets do not exist as acts; the cut: an act of a removed key not in the cut is void on every device, an act in the cut stays;
* the recovery key alone is refused before its delay, applies after, and a strong admin's `cancel` ends it; the same for
  `succession`, which also needs `mode: members`, a secure pool, endorsed keys older than 30 days, and falls when signatures are
  withdrawn [11];
* the last admin cannot be removed;
* [6] reissue: a kept device follows with no person after the hold; a dropped device is refused at its next request as soon as a
  verifying device has seen the handoff, and a `cancel` lifts it; a forged handoff, a below-threshold handoff, a handoff naming
  other admins than the signers, a replayed handoff, a handoff for a copy of the pool with another charter, a handoff delivered by a
  beacon or from a non-member, a handoff naming no kept entry for the device, and a handoff past its window each leave every
  device in the original pool; a handoff that shortens a hold, lowers a threshold, turns on `mode: members` or opens the policy
  is held for the device's person; the fallback approval with different admins is held and warns, re-verifies the signatures and
  not the window, and refuses after its 180 days; a retired pool serves its full act log for 90 days and nothing else; the old pool
  cannot be re-activated; a cloned device key is flagged; offers and leases of the old pool end on follow and only listed offers
  return; the approval text is the canonical diff of the signed bytes (the rendering is a function of the bytes and no asker text
  appears outside quotes).

Pool-check steps (`tests/pool_check/steps_governance.py`): `gov-plain-member-cannot` (the three holes against real devices),
`gov-open-to-secure-after-founder-removed` (a second admin reissues an open pool to secure after the first device was removed; the
kept devices continue and the dropped one is refused), `gov-handoff-forged`, `gov-pool-copy` (a copy of one device's files joins
nothing), `gov-clone-flagged`, `gov-recovery-timelock`, `gov-hold-cancel` (a reissue signed and cancelled in its hold moves
nobody), `gov-weak-key` (a key-file admin cannot reissue).

### 1.14 Migration of existing pools

An existing pool has the legacy `pool.json`, a random id, no charter, and person keys enrolled per device. Migration is **the first
reissue**, started only by an explicit call, never by the node on start, and **it is not an outage**:

* **Legacy mode keeps syncing.** A node that finds `pool.json` and no `pools/` directory starts in `needs_migration`, and **boards,
  grants, sessions and sync with its current members keep working exactly as before**. What changes at once, so landing slice 1 closes
  the hole: in legacy mode the node **ignores removals and policy changes that arrive from members, and ignores device rows it
  does not already hold** (a member can no longer remove the founder, enrol a stranger or freeze the policy); removals and the
  policy are the local API's (grants) alone. Every other pool method answers "This device's pool needs a one-time upgrade; ask the
  owner to approve it."
* **Exactly one device originates.** `pool_migrate` (grant `pool_migrate`) is called on one device, which builds a charter naming
  the strong key that approves as the first admin (a person approval, never automatic) and a handoff. **The review list defaults to
  keeping only the devices this node has itself handshaken with** (a legacy `by` or name is written by whoever sent the row and is
  never evidence); every other device defaults to Drop, with the same check words and last-seen as 1.9.
* **Each other device follows; it does not originate.** Before it can follow, the device needs the person's strong key enrolled
  on it (the same key, enrolled through the phone path, one approval per device or one batch); the old pool's authority for this one
  handoff is **the person keys enrolled on each device** (`persons.json`), which is what governed it. The device verifies the
  signature against its own enrolled key, checks its fingerprint is kept, and follows after the hold.
* The old file moves to `pools/retired/<old id>/pool.json`. Boards are bound to the new pool by the same approval (the person sees
  the list of boards and the tap binds them, 4.2). Grants that name no pool keep working for board-level actions
  (`task_authority`, `note_verify`, `land_*`, `link_*`, `source_add`, `file_delete`, `quarantine_release`); grants for pool-level
  actions stop counting and the first-run bundle replaces them in the same approval.
* Never touched by tests, by a worktree or by a build: every test uses a temporary state directory; the real home is migrated once,
  at a safe boundary, by the owner's approval (section 13).

### 1.15 The attack-first review (2026-10-11): where each finding is fixed

| # | Finding | Fix in one line | Where | Test |
|---|---|---|---|---|
| 1 | Recovery key defeats thresholds; delays trust signer time | Recovery key counts for no immediate threshold, is removed only by its own signature, key removals are held and vetoable, every delay is monotonic from first sight | 1.3, 1.5, 1.8 | 1.13 [1] |
| 2 | Admin scope spoofing | A key is listed only with its own acceptance; `admin:<fp>` matches only accepted keys; check words, never labels | 1.3, 1.5, 2 | 1.13 [2] |
| 3 | Agent-reachable admin key | Only strong (per-signature presence) keys count for sensitive acts, removals and cancels; the laptop's key is weak | 1.3 | 1.13 [3] |
| 4 | Code-execution regression | Per-device shard allow list kept, default the owner's own devices, never granted by `inbound_consent` or the bundle | 1.7, 5 | `serve.rs`, `shard.rs` |
| 5 | Tap-less admission | Trusted display for secrets; scan or typing on the new device is the approval; through an agent: check words and a tap; delegate bound to a keystore key, rate-limited, notifies the phone | 1.3, 1.6 | 1.13 [5] |
| 6 | Reissue gaps | Suspension on verify, 90-day full act log, window versus fallback reconciled, hold, non-grindable tie rule, weakening gets R6, diff rendered by the phone | 1.9 | 1.13 [6] |
| 7 | Fork rules | Removals commute, adds need surviving signers, last-admin rule after the fold, no hash ties | 1.5 | 1.13 [7] |
| 8 | Misleading approvals | Check words per device, open joiners default Drop, untrusted text quoted, one pending sensitive approval per pool, cooldown, renewal its own approval | 1.3, 1.5, 1.9, 2 | 1.13 |
| 9 | Unfounded devices | A join binds no existing board; boards bind only by an explicit person act | 4.2 | `pools.rs` |
| 10 | Serving | Offers are device-signed; `serve_call` bundle only to own devices, other persons' devices one tap; bucketed estimates | 7.2, 7.3, 2 | `serve.rs` |
| 11 | Members-governed mode | Endorsed keys of a minimum age; a device cannot know every admin is gone, so the mode never infers it | 1.8 | 1.13 [11] |
| 12 | DoS and open | `via: segment`, ephemeral windows, caps, paging within the frame, per-member queues, rate limits | 1.5, 5 | 1.13 [12] |
| 13 | Migration | Legacy mode keeps syncing but ignores member removals, policy and unknown rows; one originator; handshaken devices only; keys enrolled first | 1.14 | `reissue.rs` |
| 14 | Cross-pool | Beacons rotate and seal; tokens bound to one pool; the least permissive right wins; origin cap counts live origins; bucketed timing | 4.2, 6.3, 7.4 | `pools.rs`, `board_share.rs` |

### 1.16 What slice 1 carries from the removal review (2026-10-11), as built

A device that records a removal stops exchanging with the removed device (`docs/node.md`, "Membership"), but a member that has not
heard of the removal still syncs with it, and it used to take the entries the removed device wrote and relay them: `sync::take` and
`Board::ingest` checked a row's signature and chain, never whether its origin belongs to a removed device. Two requirements, both built:

* **F1: a removal carries a cut of the removed device's logs.** The `remove` act lists, for each log of boards that the removed
  device wrote and the remover's device holds, the board, the origin and the tip it holds (sequence and hash), as `admin_remove` does
  for a key's acts (1.5, "Removing a key carries a cut"; the node writes the list and ignores what a caller sent). Every receiver
  refuses the rows of such an origin past the cut, **whoever relays them**, and drops a copy it holds that does not pass through the
  tip named; a relayed row carries no authority of its own, so a member that already removed the device and a member that has not
  heard yet end with the same rows. A log that no removal names stands as far as the device holds it, and no entry under the key of
  a removed device is taken from a log started later. `app/poolhouse-node/tests/removal.rs`
  (`rows_of_a_removed_device_relayed_by_an_unaware_member_are_refused_by_a_device_that_holds_the_removal`) pins the refusal, and
  `tests/gov_removal.rs` shows the rows up to the cut still arrive through a relay that has not heard.
* **F2: a removal retracts what the removed key admitted.** In a governed pool a device is a member only by an `admit` act: an admin's,
  a live delegate's, or an open pool's member's. `members` rows enrol nobody, and an `admit` signed by a device that is removed (or by
  an admin key whose removal does not name it in its cut) is void on every device, whenever it arrives, and an admin admits those
  devices again if the person wants them (`gov_removal.rs`, `removing_a_device_removes_the_devices_it_admitted...`). **Not built:** the
  review list that puts the devices a removed admin key admitted inside its cut to the person to keep or drop with the check words of
  each (slices 5 and 8); until then they stay members.

### 1.17 The agent's founding path, as built (2026-10-11), and what remains

`ph.pool.found(name, policy)` and `poolhouse-workspace pool found [NAME] [--policy open|secure]` (`src/poolhouse/board/founding.py`) found the
pool of this device. **With a phone linked** (`found_by_phone`) the node writes two cards from the structured act and the phone's key signs
each one: "Found the pool home, secure: you will be its first admin" (the key's acceptance of the charter, which makes the pool), then
"Let the device ... join the pool" (the first `admit`, of this device). The phone signs the act's own bytes behind the person's
fingerprint, so the signature is the one every member checks; `pool_ask_found` and `pool_ask_sign` make the requests and the node
finishes the work when the signature comes (`approvals/sign.rs`; the blocking `Signer` of `gov/signer.rs` cannot stand behind a phone,
because the node's lock is held while it signs and the phone's answer needs it). The person taps twice and types nothing; no grant card
comes first, since the card is the person's consent. **With no phone** the first key the node holds is `weak`, and the call refuses it before
anyone is asked: "A pool needs a key that asks for you each time it signs, which is your phone; link your phone in the Poolhouse app and tell
me again." A caller that holds a key it declares strong passes it as `admin` and the node drafts, the key signs outside it and the node takes
the act back (`pool_draft_found`, `pool_found`, `pool_draft`, `pool_submit`, `alg` named on each key); that path asks for the `pool_create`
grant on the phone's card as before. A device already in a founded pool answers "This device is already in the pool X" and changes
nothing. Every refusal is a `Refusal` (an `ph.Denied`): one sentence of at most 160 characters, what is needed, where the person taps and
what the agent does next. The node writes the `pool_created` event to the pool board's audit.

**What remains, exactly.**

* **Founding is two taps** because the first admit follows the charter in time (its `prev` is the hash of the charter with the acceptance
  in it). One tap needs the charter to name the device it starts with, so the key's acceptance covers it and the fold admits it.
* **No number to match on a signing card.** A grant card for a sensitive action asks the person for the number the agent showed; a card to
  sign an admit or a removal carries none, so an agent that asks for a signature the person did not mean relies on the person reading the
  card. Accepted for now; the hardening is the same pick as for grants.
* **The phone shows the node's text** for a signing card and recomputes the bytes from the act or charter entry it carries
  (`Challenge.actBytes`, not compiled in this checkout), but does not yet render the plain words from the act itself; the text a card shows
  is the node's, and a node that wrote text that did not match its act is caught only by the person reading the one-line summary.
* **Several phones**: the oldest active phone is the admin; adding another phone as an admin is an `admin_add` it signs, which `pool_ask_sign`
  asks for but no command makes yet.
* **Attestation roots are empty** (1.3): the node needs Google's published roots in `phone/attest.rs` before any real phone is strong, so
  no pool is founded on a real machine until they are filled in. The pool check proves the refusal (`found-by-phone`); the success path is
  proved in the node's own tests with a root the test makes. Nothing a shipped node honours adds a root.
* **The only admin phone is not removed** (`phone::last_admin`): a lost phone is a lost pool until a second admin or a recovery key (slice 3)
  exists.
* **Retrying founding** after a denied admit asks the phone for the admit alone: the pool stands and this device is not yet its member.
* A call that lists the open pools a device has heard (`ph.pool.join` needs the host and the fingerprint of an open pool's member, or a
  code), and the audit entry naming the agent that asked (`audit_event` takes two events only).

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
4. **First run offers a default bundle** of standing grants for routine pool, serve and board operations, scoped to this device
   and to pools whose charter lists the approving key **with that key's own acceptance** (`pool: "admin:<key fingerprint>"`, 1.3),
   30 days: `pool_create`, `add_device`, `join_pool`, `sync_now`, `pool_leave`, `pool_reissue`, `admin_manage`, `serve_offer` for
   those pools, `inbound_consent` (model calls) for those pools, and `serve_call` **only to the owner's own devices**. Never in
   the bundle: joining, offering to, calling into or sharing with a pool or a device someone else administers, `board_share`,
   `member_remove`, `shard_consent` (code execution: 1.7) and `board_bind` of an existing board. After setup "create a pool called
   home and add my laptop" needs the founding tap, and adding the laptop needs nothing when the person scans or types the code on
   it (1.6); routine repeats need none. The bundle and the delegate renew by **their own stated approval**, nudged a week ahead and
   never inside another tap (1.3).
5. **Every refusal and every pending approval explains itself in one plain sentence** the agent can relay. The node's error carries
   `say` (at most 160 characters, no jargon: it names what is needed and where to tap), `needs` (`approval`, `grant`, `device`,
   `time` or `none`, with an id, `where` and `expires_in_s`) and `then` (`wait`, `retry`, `ask-owner`, `stop`). A waiting approval is
   not a failure: `ph.NeedsApproval` carries the same fields and a library call with `wait=True` blocks until the tap or the expiry.
6. **No step in an owner's document says "run".** Section 3 and the "What to tell your agent" block are written for the owner.
7. **What this document needs from the approval path** (`docs/phone-approval.md` owns the mechanism): per-signature presence that
   the signing process cannot supply (a strong key, 1.3); text **rendered by the phone from the bytes being signed** (for a
   charter change, the canonical diff of 1.9), so no word of an approval is the asker's; each device and key shown by its three
   check words, with names and labels quoted and styled apart as untrusted text; **check words and a confirmation at the first-key
   link** (whoever scans first would otherwise be the founder); one pending sensitive approval per pool at a time and a 10-minute
   cooldown after a decline of the same kind from the same session; the notice with [Remove] for every delegate admit; a renewal
   approval that is separate and worded as a renewal.

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
operating system's own one-time prompt), `link-phone` (scan once so the person key exists, then confirm the same three check words on both screens: two steps, each counted),
`scan` (scan or type a code into an app), `say` (a plain sentence to the agent), `tap` (an approval), `tailscale` (install Tailscale, sign in to it: two steps, each counted; the system's one-time VPN prompt is an
`os-prompt`), needed only on a device that must reach another network and never for a same-network join. A **terminal step** (a typed command, a file edited, a key handled, a port or firewall named) is zero in every
journey, and a journey that produces one fails its check. Every `link-phone` and `tap` is a security step and is counted in the
same budget: in the normal case there is no other, so security adds no step the budget does not already show.

### 3.1 The owner on one device (journey 1)

| # | The person sees and does | The agent does | What can go wrong, in the words shown |
|---|---|---|---|
| 1 | Installs Poolhouse on the computer; allows network discovery once (`install`, `os-prompt`) | Nothing yet | "Poolhouse needs to find your other devices. Please allow it." |
| 2 | Installs the Poolhouse app on the phone; allows the camera and notifications once (`install`, `os-prompt` twice) | Nothing yet | "Poolhouse needs your camera once, to read the code." / "Allow notifications so I can ask you in seconds." |
| 3 | The computer's app shows "Link your phone" with a code; scans it with the phone and confirms both screens show the same three words (`link-phone` twice) | Enrols the person's strong key | "The words don't match, so I stopped. Start again." |
| 4 | Says "Make a pool called home" (`say`) | `pool_create`; an approval reaches the phone | "I need your OK to make the pool; check your phone." |
| 5 | Taps once on the phone: "Create pool 'home' (secure) on this computer. Bring in your boards: proj, notes. Your agents can add devices and use its models for 30 days. [Approve]" (`tap`; the same approval carries the default bundle, listed in plain words, and the list of boards to bind) | Signs the charter, the delegate and the grants; binds the boards the person left checked; the pool exists | "That approval ran out; tell me again." |
| 6 | Says "Serve a small model for my pool" (`say`) | The broker takes the lease (`poolhouse-serve up`), then `serve_offer` to `home` under the bundle | "There isn't room for that model; I'll pick a smaller one." |

Budget: install 2, os-prompt 3, link-phone 2, say 2, tap 1 = 10 human steps, 0 terminal steps. Why each security step is there: the
phone app (the strong key lives on the phone), the camera prompt (to read the code; typing six digits instead costs a step too),
the notification prompt (an approval that does not reach the phone in seconds is one nobody sees), the second `link-phone` step
(whoever scans first would be the founder). A later "add a strong backup" nudge is one sentence and one tap, optional.

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
| 3 | The first device's phone shows "'guest-laptop' wants to join 'home'. Does the new device show maple-river-ten?"; the words are on the new device's own app screen; the person compares and taps (`tap`) | Nothing: the two nodes compute and show the words, an agent never relays them; a strong admin signs the admit after the tap | "The words don't match, so I stopped. Someone else may be knocking." |

Budget: 4 steps (install, os-prompt, say, tap), 0 terminal. Reason for the visible tap: a stranger on the Wi-Fi must not join, and
only the person can see two screens agree (a knock is an agent-in-path join, 1.6).

**2c: another network** (over the person's tailnet; 4.4). Tailscale is a human step: it is installed and signed in by the person,
once per device, and the agent never does it for them.

| # | The person sees and does | The agent does | What can go wrong, in the words shown |
|---|---|---|---|
| 1 | Says "Add a device that is on another network" on the first device (`say`) | Reads whether Tailscale is up here. If it is: opens the join window; **the phone, not the agent, shows** a QR and six digits that carry the tailnet address, the certificate pin and the code. If not: says the sentence of the next row | "To reach your other network I need Tailscale on this device. It isn't installed; get it from tailscale.com, sign in, and I'll carry on when it's up." |
| 1a | Only where Tailscale is missing here: installs it, signs in, allows its VPN prompt once (`tailscale` twice, `os-prompt`) | Reads the state every 30 seconds and goes on from row 1 by itself when it is up | "Tailscale is installed here but signed out. Open it and sign in; I'll carry on when it's up." |
| 2 | On the second device installs Poolhouse and allows discovery once (`install`, `os-prompt`) | Starts the node | as 2a |
| 2a | Only where Tailscale is missing there: installs it, signs in to the same account (or accepts the shared device), allows its VPN prompt once (`tailscale` twice, `os-prompt`) | Reads the state; says the same sentences | "Tailscale is on here, but 'home-desktop' isn't answering on it. Is Tailscale on and signed in there?" |
| 3 | Opens the Poolhouse app on the second device and scans the QR on the phone, or types the six digits (`scan`) | The node joins over the tailnet with the address, the pin and the code; the delegate signs `admit`; the phone shows "'guest-laptop' (tide-oak-nine) joined 'home'. [Remove]" | "That code didn't match. Ask the first device for a new one; they last five minutes." |

Budget: 4 steps when Tailscale is already on both devices (say, install, os-prompt, scan), 0 taps, 0 terminal; 10 from nothing (those
four, and install, sign-in and os-prompt for Tailscale on each device). **Variant through an agent**: if the person hands the link
to the new device's agent instead of scanning it, both nodes show three check words and the person taps once; the reason is that an
agent holds the link (1.6); it is 5 steps and 11 from nothing. All of these numbers are tracked (3.4). Honest limit: Tailscale is
not ours, and the person's account, access rules and sharing are theirs; when it cannot reach, the agent says what blocks and where
to tap and never changes Tailscale's settings.

### 3.3 A developer embedding the library (journey 3)

| # | The developer sees and does | The library does | What can go wrong, in the words shown |
|---|---|---|---|
| 1 | `pip install poolhouse`; installs the phone app; allows camera and notifications once; links the phone and confirms the three words (`install` twice, `os-prompt` twice, `link-phone` twice; once per person) | | "Poolhouse needs your phone once, to know it's you. Scan this." |
| 2 | `import poolhouse as ph` then `ph.pool.create("lab")` | `pool_create`; raises `ph.NeedsApproval` with `say`, or waits | "I need your OK to make the pool; check your phone." |
| 3 | Taps once (`tap`) | The founding charter, delegate and bundle are signed; the call returns the `Pool` | "That approval ran out; call again." |
| 4 | `ph.serve.offer("Qwen3-0.6B", pool="lab")` | Covered by the bundle; no tap | "There isn't room for that model; try a smaller one." |
| 5 | `ph.client.Client(ph.serve.connect("Qwen3-0.6B").base_url).chat([...])` | The relay opens on loopback and the call goes to the offering device | "The model is busy; you're number 2 in line." |

On a second device: `ph.pool.join_link(link)` (one line; a join through the library is a join through an agent, so it shows check
words and takes one tap, 1.6). Budget: install 2, os-prompt 2, link-phone 2, tap 1 = 7 human steps and 3 lines of code. A call that
needs an approval and cannot get one raises `ph.NeedsApproval` with `say`; `wait=False` returns at once. Nothing in the library reads
a terminal.

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
* **The metric** `onboarding-steps` is the sum of the budgets of journeys 1, 2a, 2b, 2c, 3, 4 and 5 (3.5), counted twice: with
  Tailscale missing on the devices that need it (10, 2, 4, 10, 7, 6 and 1, for 40) and with it present (10, 2, 4, 4, 7, 0 and 1, for
  28); the agent-in-path variant of 2c (5, or 11) is tracked on its own line. The honest recount adds 9 steps to the 19 and 31 of the
  first draft: the phone app install, the camera prompt, the notification prompt and the first-key confirmation, each in journeys 1
  and 3 (8 steps; the reason of each is in 3.1), and the monthly renewal tap (1). All are ceilings in `scripts/gates` that only fall. Every slice below that adds a person-visible step must justify it in the line
  "a visible step because ..." (1.12) or it is refused.

### 3.5 Away from home (journey 4) and upkeep (journey 5)

A person approves from the phone while away from home, or reaches a device on another network, over the tailnet: the phone path is
already HTTPS over the tailnet (`docs/pool.md`, 2.5; the mechanism is `docs/phone-approval.md`). At home on the same network the phone
needs nothing (journey 1, row 2). Away from home the phone and the device must both be on the tailnet: Tailscale on the phone
(`tailscale` twice, `os-prompt`) and on the device (the same three where it is missing), 6 steps from nothing and 0 where it is
present, once per person and device. The agent says it when the person first asks for something that needs it ("I can reach your
phone at home but not away from home. Install Tailscale on your phone and sign in; I'll carry on when it's up."). Until then the
approval waits on the screen until the phone is reachable (`docs/phone-approval.md`).

**Journey 5, upkeep.** Once a month, a week before the bundle and the delegate end, the phone shows "Keep your agents' permissions for
another 30 days? [Keep] [Let them end]" (`tap`, 1 step a month). It is its own approval and is never folded into another tap, so a
person never signs a permission they were not shown; the cost is counted here and not hidden.

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
through a pool: **the token is bound to that one pool** (`(board, name, pool)`; a session that needs two pools registers twice, and
the relay's bearer token of section 8 is bound to one pool as well) and the identity entry names the pool. A board created on a device in
several pools names its pool, taken from the project's registered pool (`projects.json` gains `pool`).

**The unfounded pool of one.** A fresh device is in a pool of one that has no charter, no id and no admins: the device is its own
only member, and it admits nobody. `pool_create` founds the first pool (an approval). **A join binds no existing board, and a board
that exists before its pool is bound only by an explicit person act** (`board_bind`): the founding approval of 3.1 lists the
device's boards and the tap binds the ones left checked, so a person who used Poolhouse alone for a week keeps everything with
no extra step; a join of someone else's pool lists them unchecked in its own approval, and an open join binds none. A board
created while the device is in exactly one founded pool belongs to it from its first entry (no existing data is exposed by that). A device in a founded pool that joins another pool belongs to
both; pools never merge. The automatic founding of an open pool per project (`settle`, `claim_project`) is removed, since founding
is an approval; a device still joins an open pool it hears (a pool that names a project is joined only by a device of that
project), so devices of one repository on one network find each other as before once one pool is founded.

**Beacons** are sent once per pool per interval. For a `secure` pool they carry **a per-pool, per-day rotating id** (a hash of
the pool id and the day) and seal the sender's identity, its addresses and `tail` (this device's tailnet addresses, at most two,
4.4) under a key derived from the pool id and the day, so an observer who does not know the pool id learns neither the pool nor
the device and cannot link one device across two pools; members, who know the id, read them. An `open` pool's beacon is in the
clear, since being found is the point. A pool may set `discovery: off`; it is then found only by a QR or code. Heads of board logs stay signed in
a domain with no pool id (the pool field of the head message is the empty string permanently): a board is not a pool's, so no pool
id may enter its signatures, and the old note "the head signature uses an empty pool id" is a rule.

**Grants** gain `scope.pool`: an id, `*`, or `admin:<key fingerprint>` (the pools whose admin set holds that key). A grant that names
a pool-level action is spent only for the pool in the call's context. The registry changes: removed `set_join_policy`; kept `shard_consent` (its per-device allow
list, 1.7); added `pool_create`, `pool_leave` and `admin_manage` (slice 1), `pool_reissue`, `pool_migrate`, `board_share`, `board_leave`,
`board_bind`, `board_rebind`, `serve_offer`, `serve_call`, `inbound_consent` (model calls). A grant lets a session **ask**; an admin signature is what makes a
governing act true for everyone; both are needed, and the first-run bundle (section 2) is how a person gives the first without a
tap per act. Pool-level audit entries (member added, uses of pool-level grants, offers) are written to the pool's own history
board, never to a shared board.

### 4.3 Local API and command line

| Method | Needs | Result |
|---|---|---|
| `pool_list` | none | every pool: id, name, policy, member count, this device's role, boards, offers |
| `pool_status {pool}` | none | members with reach, governance state (admins, thresholds, pending recover or succession), per-pool limits |
| `pool_create {title, policy, project}` | grant `pool_create` | the pool; approval for the founding charter (`title`, since `name` is a field the API refuses on every request to keep a caller from naming a sender; `discovery` arrives with slice 4; a charter of weak keys alone is refused) |
| `pool_leave {pool}` | grant `pool_leave` | this device leaves (`leave` act) |
| `add_device {pool, ttl_s}`, `join_pool {pool?, host, port, passphrase, fingerprint}` | grants `add_device`, `join_pool` | as today, per pool; a device in other pools joins as a new pool record |
| `member_remove {pool, fingerprint}` | grant `member_remove` | asks for the admin act; one tap |
| `pool_reissue {pool, policy, keep}` | grant `pool_reissue` | draft and approval (1.9) |
| `admin_list`, `admin_add`, `admin_remove` | `admin_manage` | admins and their state; slice 1 has `pool_propose {kind, body}` (make an act and sign it with the keys this device's signer holds), `pool_cosign {act}` (add this signer's signatures to an act the pool holds) and `pool_submit {act}` (take an act signed elsewhere; the signatures are the authority) |
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
  over it: they join by QR, code and link (3.2, 2c) and learn addresses as above. The signed beacon heard at home carries `tail`, so
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

**Who may use this device's compute: per pool, off by default** (decision L2). `inbound_consent {pool, work: serve, on, ...}`:
a device takes model calls from a pool only after its owner turned that on for that pool (the owner's own pools are on by the
first-run bundle). **Test shards are not here**: they run a sender's code as this user and keep the per-device allow list of 1.7,
empty except for the owner's own devices and never widened by this consent or by the bundle. Defaults when turned on, safe by
choice:

| Knob | Default |
|---|---|
| `priority` | `background`: the device's own interactive work always goes first |
| `weight` | 1 (equal fair share between pools) |
| `max_slots` (tests, for an allowed device) | half the cores |
| `max_queue` | 4 waiting requests per pool and 1 per member |
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
only its own admins (a strong key, one tap; a roll is a disclosure, so it is never delegated). An open pool's devices appear on
a roll only when a person chose them in the review, and the review defaults them to Drop.

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
  board has the **least** permissive of its two rights (`ro` wins): a person's explicit read-only is never undone by the same
  device's other pool.
* 64 origins per board still bounds the number of **live** writing origins (`board::MAX_ORIGINS`). The origins of removed or
  dropped devices, and of devices that came back with a new certificate, are frozen (read-only, kept up to 1,024 per board) and
  stop counting, so a pool that reissues or removes often keeps taking new writers; a test reissues a pool 20 times on one board.

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

**An offer is a statement signed by the serving device's key** (model, shape, pool, an expiry of 2 minutes, refreshed), so a member
verifies that it really comes from that device and nobody can forge a menu for it. The offered set is published to the pool in the
same member exchange as acts, beside them and not as acts (live state, refreshed
each exchange, dropped after 90 seconds without refresh; not history), and each offer and withdrawal also writes a small audit entry to the pool's history board (model
name, device, who). Nothing names a file path.

Node methods: `serve_offer`, `serve_withdraw`, `serve_stop`, `serve_offers`, `serve_usage` (this device), `serve_offered` (what
the pools' members offer), `serve_call` (streaming).

### 7.3 A call, end to end

1. The caller's session asks its node `serve_call {pool, model, device?, request}` (grant `serve_call`, scope the pool, resource the
   device and model). **The bundle covers it only to the owner's own devices** (devices admitted by a key enrolled on the caller's
   device); another person's device in the same pool needs **one tap the first time** for that device ("Send prompts to
   'guest-laptop' (tide-oak-nine)? Its owner will be able to read them."), which creates a `serve_call` grant for that device,
   30 days.
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
  connection that opened it. **Timing is bucketed and per pool**: a caller is told its own pool's queue position only, wait
  estimates are rounded to 30 seconds, and a slot erased on a pool switch makes the first token take the same time whatever another
  pool sent before, so latency says nothing about another pool.
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

### 7.7 Routing goes only to devices that were really admitted

A fake device (a Sybil enrolled through a member's rows, a cloned identity, an unreviewed open joiner, a removed device still
believed in by an unaware member) captures whatever is routed to it: prompts, files, test shards, results. So routing is part of
the membership rules, not a separate feature (owner, 2026-10-11: preventing fake devices is crucial because any compute or traffic
routed to them can be captured):

* **Route only to active members whose admit is a valid act** (admin- or delegate-signed, verified by the sender, not a row a
  member relayed). A device whose admit is unverified, retracted or cut is never a target of a job, shard, prompt, file or lease
  grant, whatever its certificate says.
* **Probation.** A newly admitted device receives no prompts, files, shards or jobs until the device owner's own policy says so:
  by default only the person's own enrolled devices receive anything; a device joined by `open` or by a delegate is listed but
  receives nothing until a person approves it for serving (one line in the review list, no extra step for the person's own
  devices).
* **Check at send time and again at the reply.** Standing is re-read when the request is sent and when the reply is taken
  (section 1.5 cut rule); a device removed between the two has its reply refused and the work redone elsewhere.
* **Identity continuity.** Two live addresses presenting one fingerprint, a fingerprint whose key is not the keystore-held key
  enrolled for it, or a name that changes owner mid-session stop routing to that device at once and flag the admins.
* **Sealed where possible.** What can be sealed to a specific device key (files, results, prompts for a device the person owns)
  is sealed to that key, so a device that merely holds a copied certificate cannot read it; the rest is stated in 7.6.
* **Test.** A Sybil device enrolled while members were unaware receives nothing, and nothing is routed to it after its admitter
  is removed; a cloned device identity is not routed to; mutation: the active/probation check deleted must turn the tests red.

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
| `ph.pool.allow(pool, work, *, on=True, priority="background", max_queue=4, ...)`, `ph.pool.limits(pool="")` | Let a pool send this device model calls (`work` is `serve`; tests have their own per-device allow list, `ph.test`) with limits, and read them with this device's use by that pool. |
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
  one GPU lease across pools; fairness; limits; a token registered through pool A is refused in pool B; a join binds no existing board
  and a board binds only by `board_bind`; a beacon of a `secure` pool shows neither the pool id nor the fingerprint to a listener
  that does not know the id and does not repeat across two days or two pools; in legacy mode a member's removal, policy and unknown
  device rows are ignored while sync goes on), `board_share.rs` (6.9; a device in two pools of one board takes the `ro` of the two;
  a board whose pool reissued 20 times still takes a new writer), `serve.rs` (default no offers; a member of an offered pool
  calls; a non-member and a member of another pool are refused; metering per pool and member; withdraw, stop, removal mid-call; slot
  erase on a pool switch; an offer not signed by its device is not shown; a shard from a member that is not on the allow list is
  refused whatever `inbound_consent` and the bundle say; `serve_call` to another person's device is refused until its one tap;
  queue positions and wait estimates show only the caller's pool and are rounded; a queue per member holds one request; a stub
  model server stands in for the real one and records requests, so no GPU is needed).
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
| 1 | Governance core (first; everything else depends on it) | Charter, pool id hash, keys with their own acceptance and their strength, thresholds, acts with holds, cancel and veto, monotonic first-sight time, fork rules, caps, paging and rates, the `acts` op replacing `members` as authority, admission by tap or (with P3) by delegate, `remove`, `leave`, cut rule, `set_join_policy` refused, **legacy mode that keeps syncing but ignores member removals, policy and unknown device rows** (so landing it is not an outage), plain `say` errors on new methods, step ledger skeleton; with it the vocabulary of section 12 and the one-pool wording of `docs/node.md`, `docs/api.md`, `docs/serving.md`, `docs/pool-check.md`, `docs/test-farm.md`. Review fixes 1, 2, 3, 7, 8, 12 and the closing part of 13. Tests of 1.13 (core part) and the pool-check steps `gov-plain-member-cannot`, `gov-hold-cancel`, `gov-weak-key`. | none (P1 for real approvals; the rig key until then; P3 for the delegate) |
| 2 | Reissue, migration, copy defence | Handoff, follow rules R1..R8, suspension on verify, hold, window and fallback, 90-day act log, review list (open joiners drop), clone heartbeat and flag, `pool_migrate` (one originator, handshaken devices only, keys enrolled first). Review fixes 6, 13. Tests of 1.13 (reissue part); steps `gov-open-to-secure-after-founder-removed`, `gov-handoff-forged`, `gov-pool-copy`, `gov-clone-flagged`. | 1, P1 |
| 3 | Backup, recovery, succession | Backup nudge, recovery key and time lock, `recover_cancel`, `endorse`, members-governed mode, disputes, banners. Review fixes 1 (the rest), 11. Step `gov-recovery-timelock`. | 1, P1 |
| 4 | Many pools on one device | `pools/<id>/` layout, pool context on every member request, one-identity standing check, per-pool rotating sealed beacons and discovery, tokens bound to one pool, `pool_create/list/status/leave`, join of a second pool, the unfounded pool and explicit `board_bind`, `scope.pool` and the new grant registry, session pool, `ph.pool` and CLI additions. Review fixes 9, 14 (beacons, tokens). Step `pool-two-on-one-device`. | 1 (2 for migration) |
| 5 | First-run bundle and plain refusals | The first-run bundle of standing grants (scope by accepted key, `serve_call` to own devices only, `shard_consent` never), separate monthly renewal approval, the allow list of 1.7, `NeedsApproval`, `say`/`needs`/`then` on every error, check words and trusted-display joins, delegate notices with [Remove]. Review fixes 2 (scope), 4, 5, 8, 10 (bundle). | 4, P1 |
| 6 | Onboarding steps (tracked metric) | Step ledger, `tests/onboarding-steps.json`, the `onboarding` step of `scripts/cold-start-check`, the five pool-check journey steps, the `onboarding-steps` gate. Tracked from here on: no later slice may raise it. | 5 |
| 7 | Reach across networks (Tailscale) | `tailnet.json` from the read-only status, tailnet detection states and the agent's sentences, the `tail` field of the signed beacon, `addrs` in `hello`, per-route addresses and dial order, the advertise rules (own interface and Tailscale's list), `route` and the new `reach_hint`s, the logging rules, `tailscale` steps in the step ledger. Steps `tailnet-*`. | 4, 5 |
| 8 | Join by tap and by link | Knock with three-word confirmation for secure same-network joins and for every join through an agent, QR and six digits shown by the phone with address (a tailnet one when the other device is on another network) and pin, scan or typing in the new device's app as the approval, reachability messages. Journeys 2b, 2c. | 4, 5, 7 |
| 9 | Device capacity per pool | `pool` label and `quiet` flag on leases, per-pool limits and `inbound_consent` for model calls (`shard_consent` and its allow list stay, per device), pool-fair queue, a queue per member, usage accounting, `capacity {pool}`. | 4 |
| 10 | Shared boards | Charter and sides, rolls, replication filter and rights (the least permissive wins), live and frozen origins, freeze on leave, link rule, refusal of direct messages and private rooms, `board_rebind`, `ph.board` additions. Review fix 14 (rights, origin cap). Steps `board-shared-*`. | 1, 4, 5 |
| 11 | Serving offers and calls | Device-signed offers, the `serve` member op with streaming, `model_slot` and `gpu` leases, metering, isolation (slot erase), bucketed per-pool timing, per-member queues, `serve_call` to own devices by the bundle and to others by one tap, withdraw and stop. Review fixes 10, 14 (timing). Steps `serve-two-pools`, `serve-not-offered`, `serve-stop`. | 9, P2 |
| 12 | Discovery and the endpoint | Offers beside acts in the member exchange, `ph.serve` additions, the loopback relay and `/v1/models`, `docs/api.md` and `tests/api-signatures.json`. | 11 |
| 13 | Live proof | `serve-live` with a real small model and real runners on the owner's devices; the journeys timed on a clean machine, and the real tailnet checks of section 10 on the owner's devices and phone. | 6, 7, 8, 12 |
| 14 | Sealed messages across pools | Lift the refusal of private rooms and direct messages on shared boards (`docs/pool-encryption.md`, slice 4). | 10 |

**The review fixes of 1.15 numbered 1 to 6 are in slices 1, 2, 3 and 5** (1 and 2 in slice 1, 3 in slice 1, 4 and 5 in slice 5 with their
tests in slices 1 and 5, 6 in slice 2): slice 1 may start now and lands the closing of the three holes; slices 2, 3 and 5 do not
start until those fixes are in the text they implement.

Order of arrival: P1 to P3 and slice 1 can start together; slices 2 and 3 follow slice 1; slice 4 follows slice 1; 5, 6, 7, 8 and 9
follow 4 (6 after 5, 7 after 5, 8 after 7); 10 and 11 follow their rows; 12, 13 and 14 last. The `onboarding steps` metric (slice 6)
is a ceiling every later slice must stay under, with and without Tailscale present.

## 12. Documents and words that change

* `docs/glossary.md`: **admin** (a person key that governs a pool), **reissue** (make a new pool with another policy and carry the
  kept members), **handoff** (the signed act that does it), **offer** (a device lets a pool's members use a named model), **share**
  (a person-signed charter by which pools hold one board), **roll** (the devices of one pool that hold a shared board for the
  other pools), **charter** (the signed document that names a pool's admins and policy, or a shared board's pools),
  **strong key** and **weak key** (a person key whose every signature needs a presence the signing process cannot supply, or does not),
  **check words** (three words from a fingerprint or a key exchange, for a person to compare), **hold** (the delay before a risky
  act takes effect, in which any strong admin cancels it), **dispute** (the pause when two admins' acts conflict), **delegate** (a
  device an admin lets sign `admit` for a time), **backup** (a second admin key). "A device alone is
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
* One strong key signs sensitive acts for a one-person pool (no extra tap). Residual risk: a stolen phone, unlocked, used in one
  burst of three signatures, can take the pool when it is the only strong key, because nobody else can cancel the holds. "Two keys
  of mine" (a second phone or a hardware key) removes it at the cost of a second device at hand for every sensitive act; it is one
  setting, and the weekly nudge asks for it.
* The laptop's keystore key is weak and signs nothing that cannot be undone, so an agent on the laptop cannot sign as the person.
* A strong backup is a weekly nudge, not a setup blocker. Risk: if the phone is lost before a backup or a phrase exists, the pool
  keeps working and nobody can change it (1.8).
* The recovery key alone is time-locked for 7 days; the first-run bundle lasts 30 days and renews by its own monthly approval.
* Every takeover lever is held (24 hours to add or remove an admin, 15 minutes to follow a reissue) and counted on each device's own
  clock; the cost is that a legitimate removal of a lost key takes a day to settle.
* External accounts for push belong to `docs/phone-approval.md`, not here.
