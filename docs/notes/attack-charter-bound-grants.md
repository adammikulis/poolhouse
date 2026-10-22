# Charter-bound grants: design and break-it pass

Design for the rule "in a governed pool a grant that reaches beyond one device counts only when an admin key the pool's charter names
signed it", with the break-it pass written before anything is built (AGENTS.md section 1, "Attack first"). Read at `7133690fc`
(`0.3dev`) against `app/poolhouse-node/src` (`grants/`, `approvals/`, `gov/`, `phone/`, `sessions.rs`, `poolapi.rs`, `fold.rs`,
`membership.rs`, `board/rotate.rs`), the phone app's signing sites (`app/android`), `src/poolhouse/board/grants.py`,
`src/poolhouse/workspace/grants_cli.py`, `docs/node.md`, `docs/design-phone-free-pools.md` and the four red tests
(`app/poolhouse-node/tests/grants_authority.rs`, `tests/test_governed_grants_native.py`, uncommitted on `fix/governed-grant-authority`).
Nothing was run: every finding is from reading code and documents, and every `file:line` below was read at that commit. Words follow
[glossary.md](../glossary.md).

Round 3 (builder, claude-e40b84): an approval always writes the asking node's own fingerprint into the grant's device (`approvals/request.rs`,
`grant_of`), and `grants::find` counts a row whose scope device is `*` or this device. In a pool no approval-written grant is therefore portable to
another device: a session that works on two devices is approved on each. The `PORTABLE` set only says which actions a foreign row with device `*`
may cover, and no product path writes `*` into an approval-written row (an ask names no device); a pool-wide ask that names `*` is a product
decision this note does not take. A9 and A29 over two nodes are tested with the foreign row built by hand, which is what a hostile writer
on a trusted device would append; the rows of the table below read that way.

Round 1: claude-7d9978 (subagent of claude-2f6d13). Round 2: claude-a67834 (subagent of claude-2f6d13), answering an independent review
of round 1; this is the last fix round and the builder works from this text. An author's own pass does not satisfy the non-author
requirement: the reviewer of round 1 reads round 2, and for grants that reviewer is a cloud model (AGENTS.md section 4, "Reviewing the
backlog"). The fix is not built here.

## Review items and where they are answered

| # | Review item | Read at | Answer | Section |
| --- | --- | --- | --- | --- |
| 1 | A revoke stops verifying when its key leaves `persons.json` | `book.rs:161`, `person.rs:130-131`, `:152-164`, `phone/mod.rs:226` | rows verify under the key material the charter holds for the fingerprint, in any epoch, never under `persons.json` | R2, A14 |
| 2 | No phone can be added to a device holding only a typed-yes key | `enrol.rs:97`, `:187`, `decide.rs:178` | a phone whose key the charter lists is added with the code and its key proof, no approval | R8 |
| 3 | Rebind, unenroll, enrol approval and issue bodies are signed by the Python typed-yes key | `grants.py:31`, `:106-116`, `:197-204`, `sessions.rs:86` | grants in a governed pool come only from approvals; a session moves on a phone-signed approval kind; enrol and unenroll stay device custody; controls use `FakePhone` | R1, R9, P-controls |
| 4 | `pool_leave` is a member's own act | `poolapi.rs:112-115`, `docs/node.md:823-824` | exempt: device-local | R0 |
| 5 | `approve_repeat` grants never verify | `Challenge.java:21`, `challenge.rs:58`, `decide.rs:184`, `:256` | the row carries the decision; the fold rebuilds the bytes with it | R1, A41 |
| 6 | `PHONE_REMOVE` and `person_unenroll` of a listed key | `phone/mod.rs:226`, `calls.rs:245-252` | refused in a governed pool; a listed key leaves through `admin_remove` | R9, A33 |
| 7 | A compromised admin cannot be cut off at once | `charter.rs:77-79` (24-hour holds), `chain.rs:149-156` | a narrowing `distrust` row any admin writes, effective at once | R7 |
| 8 | Strength must be derived on this device too | `ops.rs:156-159` | AND of the charter's strength and `Node::key_strength` | Terms |
| 9 | Prompts to authority phones only; a replacement phone | `phone/mod.rs:181`, `http.rs:66-131` | pushes and reads gated; the phone-add approval carries `admin_add` | R8 |
| 10 | Damaged `acts.json` or `seen.json` read as "no acts" | `pool.rs:209-213`, `chain.rs:92-99` | `Damaged`; the node does not start | R6 |
| 11 | Version-skew set-aside revives older rows | `membership.rs:48-62`, `:157-159` | a remembered floor: rows older than the set-aside stay ignored | R6 |
| 12 | Tick once per request | `api.rs:309-322`, `net.rs:613-620`, `http.rs:152-190` | tick at each entry point, never in `find` | T5 |
| 14 | Authority check before `persons.once` | `calls.rs:245-250`, `sessions.rs:86-92` | every refusal that costs nothing runs first | R9, A44 |
| 15 | Pre-check before any typed-yes prompt | `grants_cli.py:144-147`, `:189-205` | the grants listing carries the governance facts; the command refuses first | R10, A51 |
| C2 | Non-admin members' own agents lose grants | classification table | pool-wide actions need an admin; device-local ones stay the member's | R0, C2 |
| - | F-4 and P-d | - | removed as moot | 7, 10 |

## 1. The defect

A grant counts when a key enrolled in `persons.json` signed it. `persons.json` is a per-device cache that any enrolled key may extend
(`person_enroll` takes `by`: any enrolled key, `grants/calls.rs:213`), so on a device in a governed pool the people who may grant are the
people someone once enrolled, not the pool's admins. The signature is checked in five places and none reads the charter:

| Site | Line | Checks |
| --- | --- | --- |
| `grant_issue` | `grants/calls.rs:94` | `persons.signed(person, bytes, sig)` |
| `grant_revoke` | `grants/calls.rs:112` | the same |
| fold, issue rows | `grants/book.rs:111` | the same, per row, own or foreign |
| fold, revoke rows | `grants/book.rs:161` | the same, per row |
| `approval_decide` | `approvals/decide.rs:178` and `:185` | `persons.get(key)` and `persons.signed`; then `approved()` writes the rows (`:256-265`) |

`gov/pool.rs:427` `admin_scope` is written for this rule (key listed in the charter in force and its acceptance verifies) and no code but
two tests calls it (`tests/gov_acts.rs:643-648`, `tests/gov_charter.rs:71`). `docs/node.md` ("Standing grants", "Who may sign")
documents the weaker rule; `docs/design-phone-free-pools.md:126` already says the intended one: "The charter decides who approves;
`persons.json` is a cache of it".

Facts about the same code that shape the design and that round 1 had wrong or missed:

- **The fold reads `persons.json` for history.** `Persons::signed` looks only at current keys (`person.rs:130-131`), `Persons::remove`
  deletes the entry and the key is never enrolled again (`:152-164`, `:183`), and two paths call it: `PHONE_REMOVE` (`phone/mod.rs:226`)
  and `person_unenroll` (`calls.rs:252`). Removing a key from the file therefore silences every row it signed, a revoke included.
- **A phone holds two keys.** The approval key signs the phone-add proof and approvals (`MainActivity.java:123`, `Decider.java:81`); a
  separate pool key signs a pool's founding acceptance and pool acts (`PoolKey.java:294`, `GovernanceAct.java:45-46`), and no production
  caller uses it yet (`docs/notes/2026-10-14-phone-pool-key.md`). Today `pool_create` and `pool_found` list the key `persons.json`
  marks as a phone's, which is the approval key (`ops.rs:156-159`, `:171`), so one key is both listed and approving. The predicate
  below takes that as one function, `stands_for`, so that the pool-key enrolment that is still to come changes one place.
- **A phone key signs no grant body.** `grants.py:106-116` signs `grant_issue` with the typed-yes Ed25519 key; the phone's P-256 key
  signs only the four things above. The same holds for the enrol approval (`grants.py:162-172`), the removal of a key (`:187-194`) and the
  move of a session (`:197-204`).
- **Approval signatures need not be low-`s`.** `P256Signature.normalize` is applied to the pool key (`PoolKey.java:349`) and nowhere on
  the approval path; ring verifies both forms, `gov::keys::Alg::verifies` refuses the high one.
- **`approve_repeat` grants never verify.** `decide` verifies and stores a signature over `approve_repeat` bytes (`decide.rs:184`,
  `:256`); the fold rebuilds the bytes with the word `approve` (`challenge.rs:58`) and the row has no field that says otherwise
  (`challenge.rs:23`). `approvals_fatigue.rs:159` asserts `state == approved` and nothing about the grant.
- **A damaged pool reads as a pool with no acts.** `acts.json` and `seen.json` are parsed with `.ok()` and `unwrap_or_default()`
  (`gov/pool.rs:209-213`); a missing `seen`/`ready` entry reads as "seen now" (`chain.rs:92-99`). A file of the right JSON and the wrong
  shape lists a removed admin again and restarts every hold that had run. Round 1's R6 said the node would not start; it starts.
- **A version change makes a governed device unfounded** (`membership.rs:48-62`, `:157-159`), and the unfounded rule counts every row an
  enrolled key signed, including the rows that were ignored while the pool stood.
- **A refused request still spends a nonce.** `unenroll` and `rebind` call `persons.once` before anything the pool could refuse
  (`calls.rs:249`, `sessions.rs:92`), and the table holds 256 (`person.rs:20`): any enrolled key can fill it with requests that will
  be refused and stop the lead's own for ten minutes.
- **`pool_leave` is granted like a pool action** (`poolapi.rs:112-115`, `docs/node.md:224`) although `docs/node.md:823-824` says leaving
  is the one thing a member device does alone.

## 2. The rule

**Terms.**

- *Governed*: `node.members.gov()` is `Some`, that is the device founded or joined a pool. A node is in one pool and every board it hosts
  is that pool's (`docs/node.md`, "One pool of devices, many boards"), so governance is a property of the node, never of one board.
  *Ungoverned* is the unfounded pool of one. A device that left the pool stays governed (`pool_leave` is an act, the pool directory
  remains).
- *Charter in force*: `gov.state().chain.latest().charter`, read after the request's tick (T5). Never `chain.pending`, never a charter
  an act proposes.
- *Listed*: a key is named in a charter of some epoch. Every listing carries a valid acceptance, because the chain refuses an add
  without one (`chain.rs:201`) and founding checks each (`Charter::check_founding`).
- *Key material of a fingerprint*: the `alg` and `key` the charter holds for it. A fingerprint is the SHA-256 of the key, so the
  material is the same in every epoch that lists it.
- *Authority key* (it issues and it decides): a key `K` with all of
  - (a) listed in the charter in force with `role == Admin` and `strength == Strong`, and `charter.mode == Admins`;
  - (b) its own acceptance verifies for the epoch it was listed at (the check `admin_scope` has, `pool.rs:429-433`);
  - (c) this device derives it strong: `Node::key_strength(K) == Strong`, that is `persons.json` holds an entry under the fingerprint with
    `phone: true` and the same `alg` and public key as the charter's (a key a phone's own enrolment put here);
  - (d) not distrusted (R7).

  A signature by `K` is verified with `person::verifies_with(alg, key, ..)` on the charter's material, not with `Alg::verifies`, which
  also demands the low-`s` form an approval need not have. `stands_for(signer) -> Option<&AdminKey>` is the one function that maps the key
  that signed to the listed key it stands for: today the identity; when per-pool enrolment of the phone's pool key lands, the binding
  between the approval key and the pool key is read here and nowhere else.
- *Narrowing key*: a key listed with `role == Admin`, any strength (R2, R7). The recovery role never narrows.
- *Pool-wide* and *device-local* actions: R0.

**R0, the two classes.** An action is *pool-wide* when another device acts on its result, or it changes membership, trust, or bytes and
rows the other members hold; it is *device-local* when everything it does stays in this device's own state. The classes matter only in a
governed pool; an ungoverned device counts both as R5 says. Fail closed: the registry names the device-local actions, and every other
action, including one added later, is pool-wide.

| Class | Actions | Why |
| --- | --- | --- |
| pool-wide | `member_remove`, `add_device`, `join_pool`, `pool_create`, `admin_manage` | membership and governance (the act itself still needs the admins' signatures) |
| pool-wide | `land_trust`, `shard_consent`, `shard_call` | which devices' commits and rows this device counts, which devices may run code here, code run on another device |
| pool-wide | `task_authority`, `note_verify`, `sync_now` | with `shard_call`, the `PORTABLE` set: a foreign row naming device `*` may cover them (an approval names its own device, so none does today), and tasks and notes replicate |
| pool-wide | `file_delete` | removes bytes the other members read (red tests T1, T3) |
| pool-wide | `link_write` | writes into another project's board, which replicates; not sure, so closed |
| device-local | `link_read`, `link_remove`, `source_add` | `links.json` and the project registry of this device |
| device-local | `quarantine_release` | the record is "never synced" (`quarantine.rs`, header) |
| device-local | `pool_leave` | the one thing a member device does alone; it signs the `leave` itself (`ops.rs:359-368`) |

`spec.rs` gains `DEVICE_LOCAL: [&str; 5]`; `pool_wide(action)` is `!DEVICE_LOCAL.contains(action)`. A test lists `ACTIONS`, asserts the
five by name and that every other action is pool-wide, so a new action is closed until a reviewed commit opens it.

**R1, issue.** In a governed pool an `issue` row for a pool-wide action counts only when every condition that holds today still holds
(counted origin, portable action for a foreign row) **and**

- it carries an `approval` object: grants in a governed pool come only from approvals. `grant_issue` for a pool-wide action is refused,
  because no key that can sign its body is strong (the typed-yes key is weak; the phone's key signs no grant body), and a raw row
  appended by another writer is ignored as "not an approval";
- its `person` is an authority key;
- the approval signature verifies under that key's charter material. The rebuilt bytes use the decision the row names: `approval` gains a
  `decision` field (`challenge.rs:23` goes from nine fields to ten) that is `approve` or `approve_repeat`, and `check_entry` requires it
  to be `approve_repeat` exactly when `context.repeat > 0` (`allowed()`, `decide.rs:143-156`, refuses the other pairings at decide).

The row's `person` is a claim: the signature is verified under the material found by that fingerprint, and (c) reads `persons.json`'s
`phone` mark for that fingerprint, never the row. Neither the planted file nor the planted row suffices alone: (a) is the charter's,
(c) is the device's.

**R2, revoke.** A revoke row counts when today's conditions hold and its `person` is a narrowing key **listed in the charter in force or
in any earlier epoch** (`GovPool::listed` walks every epoch), its signature verified under the charter's material. **`persons.json` is
not read**: removing a key from the file (`person_unenroll`, `PHONE_REMOVE`, an edit) changes no revoke. A revocation only narrows, so a
weak key may give it and a removed admin's earlier revocations keep counting, otherwise removing an admin would bring back what that
admin revoked. A revoke of a pool-wide grant needs a narrowing key; a revoke of a device-local grant also counts from any key enrolled
on this device, verified against `persons.json` as today. The standing fold therefore keeps each revoke with its signer's standing and
applies it per grant by the grant's action class.

**R3, decide.** `approval_decide`, and the phone endpoint's `decide`, require for every decision (`approve`, `approve_repeat`, `deny`,
`block`) and every kind (grants, hold, phone add, phone remove, session move) that the deciding key is an authority key. It is checked
right after the request is found and `allowed()` has passed, before the text hash, before the signature is verified and before any state
moves. A refusal leaves the request pending, the fatigue ledger untouched and writes no audit event. The rows `approved()` writes are
folded under R1, so R3 is a courtesy and R1 is the authority. `refuse_key` says "That key is not an admin key of this pool, so nothing
was decided."

**R4, the calls.** `grant_issue` refuses a pool-wide action in a governed pool with "In a pool your phone gives standing grants: ask
for one and tap approve."; `grant_revoke` applies R2 before it writes. A row can arrive another way, so the fold decides; the calls
only refuse early. `approval_request` refuses a pool-wide ask in a governed pool while no authority phone is active on this device, in
one plain sentence, so the agent hears it and no tap is wasted.

**R5, ungoverned and device-local.** Unchanged: any key enrolled on this device, every other condition as today, own-origin rows only
for a device-local action (none of the five is portable).

**R6, fail closed.** Governed and (`charter.mode != Admins`, or no authority key is listed, or no listed key is derived strong here): no
pool-wide grant counts; `grants` lists the issue rows as `ignored` with the reason. A governed pool is never read as ungoverned or
as an older pool because something failed to load:

- `GovPool::load` returns `Damaged` when `acts.json` or `seen.json` is missing, is not JSON, has not the shape (`acts` an array of acts,
  `seen` and `ready` maps of integers, `awake_ms` an integer), or holds an act that fails `check_shape` or names another pool; the node
  does not start and says so in one sentence. The read replaces the `.ok()` and `unwrap_or_default()` at `pool.rs:209-213` with `?`.
- `GovPool::create` writes the pool into a scratch directory and renames it into `pools/<id>/`, so a directory that holds a pool is
  whole. `Pool::load` treats a `pools/` directory that holds any of the four files without `charter.json` as `Damaged`, not as no pool
  (`membership.rs:149-151` filters on `charter.json` today).
- **A set-aside remembers.** `set_aside_old_pools` (`membership.rs:48-62`) writes `grants_floor.json` at the state root, `{"before_ms":
  <wall ms>}`, before it moves the directory; it never moves back. Every grant row (issue, revoke, distrust, use) whose clock value
  `hlc.0` is not after the floor is ignored by the fold of every board, whatever rule applies after. A restated row keeps its original
  clock (`Row::operation`), so rotation does not carry an old row past the floor. An unfounded device whose state holds
  `pools-old-format/` and no floor writes the floor at its current time when it loads.

**R7, distrust.** A `distrust` row cuts a key off from widening, at once, on every board.

- *Body*: `{op: "distrust", board: "pool", key: <fingerprint>, since: <the epoch the key was listed at>, pool: <pool id>, person: <signer>,
  at_ms, reason?, sig}`, signed like a revoke (`poolhouse/grant/v1` over the canonical body). A new call `grant_distrust`, boardless like
  `person_unenroll` and carried by any registered session, checks it (fresh within five minutes) and the node writes it to the pool board
  as actor `node`, as `approved()` writes its rows. The row is allowed in `spec::check_entry` and `GRANT_KEYS` (`fold.rs:283`) and kept
  like a revoke across rotation (`rotate.rs:32` counts `distrust` beside `revoke`, so a segment cannot drop one).
- *Counts when* the signer is a narrowing key listed in the charter **in force** (a removed key's cut does not keep it), the signature
  verifies under the charter's material, `pool` is this pool's id and the subject is listed in the charter in force with that `since`.
  Origin trust is not needed: it only narrows. At most the newest 200 rows of a device this one does not trust are read, as for revokes.
- *Effect*: the subject is not an authority key (d) for issue rows and decisions, on every board, at once. `standing()` of any board
  reads the pool board's distrust set. Its pushes and reads stop (R8). A distrusted key's revoke and distrust rows keep counting:
  dropping them would revive what it revoked.
- *Order*: rows commute. Mutual distrust leaves no authority key and every pool-wide grant is ignored, which fails closed; admin acts do
  not depend on grants, so `admin_remove` recovers.
- *End*: never by a row. The distrust ends when the subject leaves the charter in force, or is listed again under a new `since`.
- *It cannot widen.* The only reader of the set is the conjunct (d), which subtracts. No row removes a key from the set. A row signed by
  a key that is not a narrowing key of the charter in force, naming an unlisted subject, a stale `since` or another pool, counts for
  nothing. Revoke rows are untouched. A row cannot name a grant, so it cannot end a grant it does not own.

**R8, phones.** A phone is only ever added to a governed device as an authority key or as one on its way to being one.

- *A listed phone is added without an approval* (`enrol.rs:97`, `:187` pick the path by `persons.is_empty()`). When the pool is governed
  and the key in the request is an authority key by (a), (b) and (d), the one-time code and the key's proof of itself are enough: the
  phone is `active` at once and `persons.json` gets the phone mark that makes (c) true. A device that holds only a typed-yes key, such as
  a member that joined, can therefore get the admin's phone with one scan. Any other key still needs an approval, and a distrusted
  listed key is "any other". The `persons.json` limit of eight keys stands.
- *The phone-add approval carries `admin_add`.* For a key the charter does not list, the request an authority phone sees names the key
  as an admin of the same person as the approving key (the node writes `person`, never the new phone), and carries the unsigned
  `admin_add` act with the new key's acceptance, which the new phone signs in its `add` request. The approving phone signs the approval
  and the act in one sitting; the node submits the act, so the hold starts at the tap and not at a later step. Until the hold ends the
  new phone is `active` on the device and an authority key by nothing: it is pushed nothing, reads nothing and its decisions are
  refused. A key for a new person is a deliberate `pool_propose`, never a phone-add.
- *Pushes and reads go to authority phones only.* `phone::announce` (`phone/mod.rs:181`) targets the active phones whose key is an
  authority key; `list`, `get` and `wait` (`http.rs:66-131`) show a phone nothing unless its key is one. A distrusted or removed-from-the-
  charter phone stops hearing about requests at once.
- *`PHONE_REMOVE` of a listed key is refused* (R9).

**R9, keys and sessions.**

- `person_enroll` and `person_unenroll` stay device custody: a typed-yes key is a weak key and counts for nothing but the device-local
  actions, so an enrolled non-admin key adds no pool authority. Two refusals are added: `person_unenroll` and `PHONE_REMOVE` of a
  fingerprint the charter in force lists (a listed key leaves through `admin_remove`; "That key is an admin key of this pool: it leaves
  through an admin removal."). Every refusal that needs no state runs before `persons.once`, so a refused request spends no nonce.
- *Moving a session* in a governed pool needs a phone-signed approval of a new kind `rebind` (the typed-yes signature of
  `sessions.rs:86` is refused there): `poolhouse-workspace rebind` asks the node for the request, naming the session, the board and the
  terminal (`anchor`); an authority phone approves it; the same command then completes the move once. The request has a number, as an
  agent's request has (`pick_code`): the command prints it in the terminal being bound and the person picks it among three on the phone,
  so the tap is tied to the terminal in front of them, and a request they did not make shows a number no terminal of theirs shows. It is
  rate-limited like a code for a phone and one waits at a time. The completion checks that the request is approved by an authority
  key, that name, board and anchor equal the request's, that the process asking is in that terminal, and that the request has not been
  used. Ungoverned pools keep the typed yes.

**R10, clients.** The `grants` listing gains `governance` (`governed` or `unfounded`), `authority` (the fingerprints that are authority
keys now), `may_issue` and `may_revoke` for each key the node holds. `grants_cli.py` reads it once and refuses before any typed-yes
prompt (`grant` at `:144`, `grants-setup` at `:189`, whose prompt is `:202`); in a governed pool `grants-setup` asks the node for the three
grants as one approval request and waits for the tap. `revoke-grant` and a new `distrust --key FINGERPRINT` run with a listed key;
`rebind` follows R9.

**Where.** One predicate in one place: a new `grants/authority.rs` builds an `Authority` snapshot at the top of every `standing()` call
(`Local` when ungoverned, `Pool` otherwise) from the chain, `persons.json` and the pool board's distrust rows, and answers `issuer`,
`revoker`, `decider`, `narrower` and `class_of(action)`. The snapshot is stored nowhere: not on `Node`, not in `Book`. Only pure
verifications (key material, message hash, signature to bool) are memoised; whether a key is listed, strong, derived strong or
distrusted is read from the current state at each call, so a removal or a distrust folded between two calls is seen by the second.
`admin_scope` becomes `GovPool::authority_keys` (it changes meaning; the two tests that call it change in the same commit, no wrapper
keeps the old name). Callers: `book::refusal`, the revoke arm of `book::standing`, `calls::issue`, `calls::revoke`, `calls::unenroll`,
`decide::decide`, `phone::enrol`, `phone::announce`, `phone::remove`, the phone endpoint's reads and `sessions::rebind`. A test bounds
the fixed cost of `standing()` with sixteen admin keys and many rows, because every guarded action runs it.

**What does not change.** The portable set (`task_authority`, `note_verify`, `sync_now`, `shard_call`: a foreign row naming device `*` may cover these and no other action; a grant an approval writes names its own device); the landing trust list for
foreign origins; the floors; the five-minute freshness of an issue or revoke at the call; the uses count; the lineage rule at spend; the
single signature per grant (a grant is a permission to ask, and the governing act it may lead to still needs the threshold of admin
signatures); the typed-yes enrolment of keys.

**What the rule costs, said plainly.**

- C1. A grant for a pool-wide action signed with a typed-yes key stops counting once the device is in a governed pool. The first-run
  bundle (`grants-setup`: `task_authority`, `shard_call`, `note_verify`) is approved once more after founding, as one phone request with
  three asks. The lead's lineage loses its 30-day bundle at that moment. The computer-only admin key of
  `docs/design-phone-free-pools.md` (2.3, slice 3) is what removes the phone from this.
- C2. A non-admin member of a multi-person pool gets `task_authority`, `note_verify`, `shard_call` and `sync_now` for the agents on its
  device only when an admin approves them, from an admin's phone that has been added to that device (R8: one scan). Its device-local
  actions (`link_read`, `link_remove`, `source_add`, `quarantine_release`, `pool_leave`) stay its own, signed by its own key as now. That
  is "The admin key decides approvals and signs grants for its pool only" (`docs/design-phone-free-pools.md:141`), not a new limit.
- C3. Replacing or removing an admin key ends every grant it signed (T2). The remaining admins approve again.
- C4. Weak-listed keys sign no grant. They revoke and distrust. The weaker option, letting a weak listed key sign the three bundle
  actions, is not taken: `shard_call` runs code on other devices and `task_authority` sets enforcement, and a weak key is one any
  process of the account with a terminal can use.
- C5. Moving a session in a governed pool is a phone tap, not a typed yes.
- C6. Adding a phone to a governed device lists it as an admin key of the same person, and the card says so.

## 3. Every path that creates or counts a grant

| # | Path | Where | Today | After |
| --- | --- | --- | --- | --- |
| P1 | `grant_issue` call | `grants/calls.rs:86` | enrolled key | R4: refused for a pool-wide action; device-local unchanged |
| P2 | `grant_revoke` call | `grants/calls.rs:104` | enrolled key | R2 and R4 |
| P3 | `approval_decide` approve and `approve_repeat`, then `approved()` appends rows as actor `node` | `approvals/decide.rs:171`, `:251-277` | `persons.get` and `persons.signed` | R3, then R1 in the fold; the row carries its decision |
| P4 | `approval_decide` deny, block, hold, phone add, phone remove, session move | `approvals/decide.rs:171`, `:228-248` | any enrolled key | R3 |
| P5 | `use` rows from `grants::check` | `grants/mod.rs:123` | written after `find` | unchanged; not authority |
| P6 | rows replicated from a trusted device | `book.rs:65-73`, `landtrust.rs:69` | origin trusted, then signature | R1 and R2 per row; the origin rule is unchanged |
| P7 | a row appended to the board log by any other writer (the injected row of red test 2, a bug, a process with log access) | `fold.rs:283-287` checks shape only | enrolled key | R1 and R2 per row |
| P8 | rotation carrying grant rows | `board/rotate.rs:127-153` | keeps every grant and revoke row | unchanged for issue; `distrust` is counted with `revoke` at `:32`; see F-4 |
| P9 | consumers of `grants::find`: `holds`, `check`, `listing`, `flags::for_grants`, the duplicate-gid test in `calls::issue`, the found test in `calls::revoke` | `grants/mod.rs:88`, `approvals/flags.rs:35`, `grants/calls.rs:97`, `:115` | read `standing()` | read the same `standing()`, now charter-bound; nothing re-implements the fold |
| P10 | actions spent through the fold: `file_delete`, `shard_consent`, `shard_call`, `note_verify`, `task_authority`, `land_trust`, `quarantine_release`, `link_*`, `source_add`, `member_remove`, `add_device`, `join_pool`, `pool_create`, `pool_leave`, `admin_manage`, `sync_now` | `files.rs:176`, `shard/mod.rs:50`, `poolapi.rs:47-130`, `net.rs:619`, `landing.rs:193`, `quarantine.rs:134`, `tasks/calls.rs:46`, `notes.rs:165`, `api.rs:152-183` | all through `grants::holds` and `check` | no site changes: one choke point, which applies R0 |
| P11 | a client that folds grants itself | `src/poolhouse/board/grants.py` reads the node's listing | the node's verdict | stays so; a second fold would be a second authority, the builder greps for one |
| P12 | the session-move approval | new, `approvals/`, `sessions.rs` | none | R9 |
| P13 | the phone-add approval with `admin_add` | `phone/enrol.rs:211-237`, `phone/mod.rs:205-216` | any enrolled key approves a phone | R3 and R8 |
| P14 | `distrust` rows | new, pool board | none | R7 |

Every reader of `persons.json`, and what it does after the change: `grants/calls.rs:94` (R4), `:112` (R2), `:138` and `:258` (listing
of keys, unchanged), `:213` and `:218` (`person_enroll`, device custody, unchanged), `:245-252` (R9); `grants/book.rs:111` (R1) and `:161`
(R2); `approvals/decide.rs:178` and `:185` (R3); `approvals/calls.rs:176` (the signed read of every waiting request: authority key only,
F-3); `sessions.rs:86` and `:92` (R9); `phone/mod.rs:226` (R9); `phone/enrol.rs:177` and `:183` (R8); `gov/ops.rs:158` (condition (c)).

## 4. Time, order and the charter

- **T1. No signer time is compared with a removal time.** A grant's `at_ms` is chosen by its signer, a row's clock value by its writer
  (`held_back` bounds only the future side), and the chain measures removal in this device's awake time (`gov/clock.rs`), which has no
  relation to wall time. The charter in force at the moment of evaluation decides; a grant by a key not listed now is ignored however it
  is dated. Back-dating gains nothing. The one clock the fold reads is the floor of R6, which is this device's own wall time at the
  set-aside and only ever hides rows.
- **T2. A removed admin's earlier grants all stop counting.** Acts keep a removed key's signatures that sit in the removal's cut
  (`chain.cuts`); a grant has no cut. Their `use` rows stay in the log as the record. The admins who remain approve again.
- **T3. A removed admin's revokes keep counting** (R2), so nothing a removed admin ended comes back. Its distrust rows do not (R7).
- **T4. Order inside the fold is unchanged**: rows in the board's total order, the first valid row of a gid wins, a revoke beats the
  grant it names whatever the order. A row whose signer is not an authority key never claims a gid (the `why.is_none()` test), so a
  non-admin cannot squat a gid. A listed admin can: it can suppress another admin's grant by taking its gid, which it could do by revoking
  it. No new reach.
- **T5. The pool is current at the entry, once.** Every place that takes the node lock to serve a request calls `Node::gov_tick()` once,
  right after it: `api::run` before `dispatch` (`api.rs:309-312`), every `lock(node)` of `phone/http.rs` that serves a request (`handle`
  at `:158`, `phone_op` at `:123`, each poll of `wait` at `:90`), `net::Net::network` before `authorize_from` (`net.rs:618`); the
  member-facing `peer::answer` already does (`peer.rs:182`).
  `standing()`, `find` and the readers of R3 never tick, so one request sees one pool and costs one deadline compare and a one-second
  throttle (`GovPool::tick`). It must be `gov_tick`, not `GovPool::tick`: the latter consumes the "due" flag and the node then skips
  `members.refresh()` and `apply_log_cuts()` for that transition.
- **T6. Holds.** A pending `admin_add` is not in force, so its key signs nothing that counts; it counts from the epoch the add takes
  effect, which is after `admin_add_h`. A pending `admin_remove` does not suspend the key: acts of a key also count until its epoch ends,
  a hold is the pool's chosen delay, and a pending removal that froze grants would let one admin freeze another's by filing a removal. An
  open dispute changes nothing for the same reason. What cuts a suspect admin off at once is the distrust row (R7), which needs no hold
  because it only narrows; revoking its grants one by one cannot do that, since the key goes on signing new ones.
- **T7. Clock.** The wall clock still decides `until_ms` and `not-yet`. A hostile clock can revive an expired grant of a listed admin
  (not a removed admin's: R1 reads no clock) or end live ones early; that is unchanged and needs the machine.
- **T8. Concurrency.** One API call holds the node lock for its `holds` and `check`, tick included; both run `find`, so a removal
  ingested between two calls is seen by the second. The peer path (`authorize_from`) takes the lock for the pair. A removal that lands
  after a use leaves that use standing.
- **T9. Other devices.** Each device evaluates against the charter and the pool board it has folded. A device that has not yet learned of
  a removal or a distrust keeps counting that admin's grants until it syncs (bounded by the sync interval plus the hold for a removal, by
  the sync interval alone for a distrust).
- **T10. Distrust commutes.** A distrust row and the grant it cuts may arrive in either order, and two distrust rows in either order,
  with the same result on every device.

## 5. The attacks

Severity: **H** breaks the claim "only a pool admin grants" or leaves a compromised admin in place, **M** a gap or a decision, **L** a
note. "Red test" is the red test that already holds the case (T1 `an_enrolled_non_admin_raw_grant_cannot_delete_the_shared_file`, T2
`a_valid_non_admin_signed_row_cannot_bypass_the_grant_fold`, T3 `a_non_admin_approval_cannot_write_a_grant_that_deletes_a_file`, T4
`an_enrolled_non_admin_cannot_persist_a_consent_change`).

| # | Sev | Attack | Expected refusal | Red test |
| --- | --- | --- | --- | --- |
| A1 | H | Enrolled non-admin key signs `grant_issue` for `file_delete` on a shared file | `denied` at issue with the plain sentence, no row, the later delete `denied`, the bytes intact | T1 |
| A2 | H | The same key signs a `shard_consent` grant, then the session turns consent off | issue `denied`; `shard_consent` `denied`; `shards.json` not written | T4 |
| A3 | H | A validly signed non-admin row is appended to the log, or arrives from a trusted device | row listed `ignored: signed by a key that is not an admin of this pool now`; the action `denied`; the foreign copy the same | T2 for the own log only |
| A4 | H | A non-admin key approves the agent's `file_delete` request | `decide` refuses before any state moves: request still pending, no row, ledger byte-equal, file intact | T3 asserts the file only |
| A5 | H | A non-admin builds the `approval` object by hand and signs the rebuilt challenge (`challenge::grant_bytes`), then appends the row | ignored: `person` is not an authority key | none |
| A6 | H | Relabel: the outsider signs, with `person` set to an admin's fingerprint | signature fails under the admin's charter material; ignored | none |
| A7 | M | A key listed with the recovery role signs an issue or approves | refused: "the recovery key signs no grant" | none |
| A8 | H | A key listed weak signs an issue or approves, or `grants-setup` signs the bundle with the typed-yes key | refused with one plain sentence that names the phone; no prompt is raised first (R10) | none |
| A9 | H | The charter lists a second admin whose phone is not enrolled as a phone key on this device, and its pool-wide row, built by hand with device `*`, arrives from a trusted device | ignored: (c) is not true here; the person adds the phone (R8) | none |
| A10 | M | `persons.json` holds a different public key or `alg` under an admin's fingerprint (planted file) | refused by the byte compare in (c); revokes and distrusts are unaffected, they read the charter | none |
| A11 | H | A key in `persons.json` marked `phone: true` that no charter lists; and the mirror, the mark planted on a key the charter lists weak | counts for nothing: (a) is the charter's and (c) is the device's, and both must hold | none |
| A12 | H | Non-admin key calls `grant_revoke` on an admin's live pool-wide grant (a denial of service on the lead) | `denied`; the grant stays live | none |
| A13 | H | A revoke row signed by a non-admin is appended | ignored and listed; the grant stays live | none |
| A14 | H | An admin revokes a grant, then that admin is removed from the pool; and, separately, that admin's key is removed from `persons.json` (`person_unenroll`, an edit and a reopen of `Persons`) | the grant stays revoked in both: R2 reads the charter's material and every epoch, never the file | none |
| A15 | M | A weak-listed key revokes; a recovery-listed key revokes | weak counts (narrows only); recovery does not | none |
| A16 | H | A removed admin revokes everything on the way out | counts; the pool re-issues under new gids. Availability cost, accepted (R-5) | none |
| A17 | H | Non-admin key sends `deny` or `block` on the lead's request (counted as a person's decline until the lead's agent is blocked, `decide.rs:228-243`) | refused at the top of `decide`; request pending; fatigue ledger and block list byte-equal | none |
| A18 | M | Non-admin approves a `hold`, letting held requests through | refused; the request stays held | none |
| A19 | H | Non-admin approves a `phone_remove` of the admin's phone | refused; the phone and its key stay | none |
| A20 | H | Non-admin approves a `phone_add` to mint a `phone: true` key | refused; and such a key still is not listed in the charter (A11) | none |
| A21 | H | An admin is removed (hold over); an earlier, unexpired grant of that admin is held here and on a trusted device | after the fold both are `ignored: signed by a key that is not an admin of this pool now`; actions `denied` | none |
| A22 | H | A removed admin signs a grant with `at_ms` before the removal and it arrives later | ignored: no signer time is read (T1) | none |
| A23 | H | The hold ran out on the awake clock and nobody has called `gov_tick` | the entry ticks first; the removed admin's grants do not count; checked through the local API, the phone endpoint and the network path (T5) | none |
| A24 | M | A key in a pending `admin_add` signs | ignored: only the charter in force counts (T6) | none |
| A25 | M | An admin's removal is pending, or a dispute is open | that admin's grants still count (T6) unless it is distrusted; `grants` carries a one-line note while a removal is pending | none |
| A26 | M | Charter `mode: members`, or a charter with no strong admin key | no pool-wide grant counts; `grants` says why (R6) | none |
| A27 | H | Charter rotation replaces an admin key | the old key's grants end, the new key's are approved fresh, `grants` names the reason | none |
| A28 | M | Time-bomb: an admin signs a grant valid 366 days from a future `at_ms`, then is removed | ignored from the removal on | none |
| A29 | H | A trusted device writes a non-admin-signed row, an admin-signed grant for a non-portable action, or a device-local row | the first is ignored (R1); the second because the action is granted on the device it applies to; the third because a device-local row counts from its own origin only | none |
| A30 | M | A trusted device replays an admin-signed grant that was revoked | revoke beats it whatever the order (`book.rs` header says so; no test in `grants*.rs` pins the order, so one is added) | none |
| A31 | M | A trusted device truncates its own log to drop a revoke or a distrust | board layer: per-origin hash chain and `revokes_kept` at rotation, which counts `distrust` too; stated as a dependency | none |
| A32 | H | Non-admin key `rebind`s the lead's session to its own terminal and spends the lead's approved grants (`sessions.rs:86`) | in a governed pool no typed-yes signature moves a session; only an approved `rebind` request, once, from the terminal it names (R9) | none |
| A33 | H | Any key, a lone admin included, `person_unenroll`s or `PHONE_REMOVE`s a key the charter lists (permanent: removed keys are never enrolled again, `person.rs:183`) | refused; the key leaves through `admin_remove` (R9) | none |
| A34 | M | An agent taken in by an injected instruction runs `poolhouse-workspace grant` with the typed-yes key | one plain refusal, no extra prompt, nothing written | none |
| A35 | M | `approval_request` for a pool-wide action while no authority phone is active on this device | refused at the request, one plain sentence, so the agent hears it and no tap is wasted | none |
| A36 | M | A pool whose only admin key is weak | no pool-wide grant counts and the listing says so (R6); the pool is otherwise usable | none |
| A37 | M | Ungoverned device: an enrolled key grants | counts, as today (R5); a test pins it so the rule is not applied to a pool of one | none |
| A38 | H | A compromised admin goes on signing new grants for the whole removal hold | another admin's distrust row cuts it at once, on every board; grants already live stop; the compromised key's own revokes keep counting; in either order of arrival (R7, T10) | none |
| A39 | H | A distrust row that should not count: a non-admin signer, a removed admin signer, an unlisted subject, a stale `since`, or another pool's id; and a mutual pair of distrusts | the first five are ignored; the pair removes both keys from the authority set and nothing widens; no row undoes a distrust | none |
| A40 | H | A listed admin key is removed from `persons.json` by a file edit, the node stopped | the edit is R-1; its revokes and distrusts still verify (R2, R7); its approvals stop counting here until the phone is added again, because (c) is false | none |
| A41 | H | A grant approved with `approve_repeat`; and a row that claims `approve` over an `approve_repeat` signature, or `approve_repeat` with `context.repeat == 0` | the first counts (positive control); the others are ignored | none |
| A42 | H | `acts.json` or `seen.json` is damaged: valid JSON of the wrong shape, a missing file, an act of another pool, a missing `awake_ms` | `Pool::load` returns `Damaged` for each; the node does not start; before the change the removed admin is listed again and the holds restart | none |
| A43 | H | A non-admin row planted while the pool stood waits; the build changes the charter version and the device becomes unfounded | the floor of R6 keeps every older row ignored; a row written after the floor counts under the ungoverned rule | none |
| A44 | M | A non-authority key sends signed requests the pool will refuse, to fill the nonce table | refused before `persons.once`; the table is unchanged; the lead's own request still works (R9) | none |
| A45 | H | A device that holds only a typed-yes key: a listed phone scans the code; an unlisted phone scans it; a distrusted listed phone scans it | the listed phone is `active` with no approval; the unlisted one waits for an authority phone's approval; the distrusted one waits too (R8) | none |
| A46 | M | A non-authority or distrusted active phone | it is sent no push and `list`, `get`, `wait` show it nothing (R8) | none |
| A47 | H | A session asks for a code and a phone-add approval for an unlisted key | the card names an admin of the approving key's person and the session that made the code; the three-number match ties the tap to the phone in hand; the new phone is no authority until the hold ends, and the hold is the undo (a strong admin cancels); a new person cannot arrive this way | none |
| A48 | M | One request sees two pools: a reader that ticks in the middle, or an entry that does not tick | one tick at each entry point and none in `find` (T5) | none |
| A49 | M | An authority verdict cached across `standing()` calls | a removal or a distrust folded between two calls is seen by the second | none |
| A50 | M | A device-local action in a governed pool | `link_read` by an enrolled non-admin key counts and `file_delete` does not; `pool_leave` works with a typed-yes grant; the table is pinned to `ACTIONS` (R0) | none |
| A51 | M | `grant` or `grants-setup` in a governed pool | refused before any key prompt is shown (a prompt callable that must not be called); `revoke-grant` and `distrust` work with a listed key; ungoverned unchanged (R10) | none |
| A52 | H | A session move by the approval path: replay, a different terminal, a different session, a non-authority decision | each refused; an approved move works once and ends the tokens the session held (R9) | none |
| A53 | M | An agent asks the node for approvals again and again for pool-wide grants on a member device with no authority phone | refused at the request (A35); no push goes anywhere | none |
| A54 | M | A phone's approval whose `s` is in the upper half of the group | counts (positive control): the verifier is `verifies_with` | none |

Positive controls the four red tests do not contain, without which "refuse every grant in a governed pool" would turn them all green. All
use `kit::phone::FakePhone`, a P-256 key that signs through the same endpoint a real phone does, never a planted Ed25519 key with
`phone: true`; the founding is `Rig::add` for the first phone, `pool_draft_found` listing that phone's key, the phone's acceptance
signed in the low-`s` form (`FakePhone` gains the method; `kit::p256::PhoneKey::sign` has the code), then `pool_found`
(`tests/gov_charter.rs:369-389` does this for a bare `PhoneKey`). The charter also lists a typed-yes key as a weak admin, so there is a key
that can revoke and distrust.

- **P-a** the phone approves an agent's `file_delete` request through `phone/v1/decide` and the delete works, with its `use` row;
- **P-b** the same after one decline, with `approve_repeat` (A41);
- **P-c** the weak listed key revokes and the action stops; the same revoke survives removing that key from `persons.json` (A14);
- **P-d** the ungoverned rule of A37;
- **P-e** an approval with a high-`s` signature counts (A54);
- **P-f** a device-local grant by an enrolled key counts in the governed pool (A50);
- **P-g** a device that holds only a typed-yes key gets the listed phone with one scan and then an approval from it (A45);
- **P-h** a replacement phone is approved by the first, the `admin_add` hold runs, and after it the new phone's approval counts (A47);
- **P-i** an approved session move works once (A52).

## 6. The cases AGENTS.md names

| Case | Result |
| --- | --- |
| Copied or cloned pool or device | A copied charter makes no pool on another device (`pool_found`: the first device is part of the anchor), so a copy governs nothing. A copied whole state directory is a copy of the device, its keys and its trust: device compromise, outside this rule (R-1). |
| Replay | An issue or revoke at the call is fresh for five minutes and a gid is used once; a session move is one approval used once. A replicated row has no freshness by design: replay of an admin-signed row is a valid grant until it expires or is revoked. Distrust rows carry the pool id. Across pools for the other rows see R-2. |
| Forged or below-threshold signature | Forgery: the signature is verified under the charter's material and the key is looked up by the verified fingerprint (A6). Below threshold: a grant is one signature by design (R-4); the pool's thresholds still guard the governing acts a grant lets a session ask for. |
| Compromised or malicious member | A non-admin member signs nothing pool-wide that counts (A1-A5, A12, A17-A20, A32, A33). A malicious admin keeps admin power until removed (T6, R-6), and the other admins cut its grants and approvals at once with a distrust row (A38). |
| Stolen phone or key | A stolen strong admin key signs grants until the other admins distrust it (at once, from the computer's key if the phone is the only strong key) and until its removal takes effect for the acts; every grant it signed dies with the removal (A21). A stolen non-admin key signs nothing pool-wide. |
| Malicious agent | Holds a session token and no key; cannot sign, cannot decide (A4, A32). A signed body it read from the board is already on the board, so its gid is used and a resubmission is refused. |
| Agent compromised without knowing it | A34, A35 and A53: the refusal costs no prompt and the person is not asked first. |
| Downgrade (secure to open) | Governed to ungoverned needs a write to the state directory (R-1), or a version change, which now leaves a floor (A43). The change adds no downgrade of its own: a pool that cannot be read stops the node (A42); `mode`, a weak-only charter and a failed acceptance fail closed. |
| Approval fatigue | R3 refuses a non-admin decision before it moves the ledger (A17), refuses at request time (A35), pushes go to authority phones only (A46), and a refused request spends no nonce (A44). The phone-add tap lists an admin, so its card says so and carries the number match (A47). |
| Takeover by whoever is merely present | Presence at a terminal with a typed-yes key grants nothing pool-wide in a governed pool (A8, A34); a session move needs the phone (A32, A52); `unenroll` of a listed key is closed (A33). |

## 7. Adjacent holes the same predicate closes

These are not in the four red tests. F-1 is part of the fix because without it a non-admin key spends admin-approved grants.

- **F-1. `rebind` takes any enrolled key** (`sessions.rs:86`). A non-admin key signs the move of the lead's session to its own terminal
  and then spends the grants an admin approved for that session. Fix: R9, an approved `rebind` request in a governed pool; ungoverned
  stays.
- **F-2. `person_unenroll` of a listed key** (`calls.rs:245-252`). Any enrolled key can remove an admin's key from `persons.json` for good
  ("a key that is removed is not enrolled again", `person.rs:183`), and `PHONE_REMOVE` does the same. Fix: both refuse a fingerprint the
  charter in force lists (R9). With R2 reading the charter, a removed `persons.json` entry no longer changes any row, so this is an
  availability fix, not an authority one. Own commit.
- **F-3. Approval list read** (`approvals/calls.rs:176`, and the phone endpoint's `list`, `get`, `wait`): any enrolled key reads every
  pending request's text. Fix: authority key only (R8).
- **F-4. Ignored rows are never dropped** (`rotate.rs:127-153` keeps every grant row; `kept.len() > rows/2` refuses the rotation). A
  flood of non-admin-signed rows from a trusted device fills the log until its rotation is refused. Dropping an ignored row is unsafe (a
  re-listed key would lose it), so the bound belongs on the trusted origin's grant-row quota. Own item.

## 8. Accepted residuals

- **R-1. The state directory is a set of plain files.** `persons.json` and `pools/<id>/` can be edited by a process that can write them
  (documented in `docs/node.md`, "Accepted residual"); setting the pool aside makes the device ungoverned, and the floor of R6 then hides
  every row written before. The agent sandbox is what closes it. The `grants` listing carries `governance` so the change is visible to
  the person.
- **R-2. No pool id in the signed body of an issue or revoke row.** The same admin key in two pools signs a body valid in both if the
  board, session name and device match, and a writer on the second pool's trusted origin can replay it. Closing it changes the phone's
  challenge (the phone signs bytes the node builds), so it waits for that change; preconditions are narrow (a shared admin key, a shared
  board name, a trusted malicious device, a portable action). Distrust rows are signed by the computer's key, so they carry the pool id.
- **R-3. A removed key that is listed again revives its older unexpired, unrevoked grants.** The body carries no epoch. Advice in the
  removal card: revoke first. A distrust row does not revive: it names `since`.
- **R-4. One strong admin signature authorises a local, irreversible action** (`file_delete`, `shard_consent`). The 24-hour cancel window
  and several-step approval of `docs/design-phone-free-pools.md` (2.6) are that design's slices.
- **R-5. A removed admin can revoke** and so cancel every grant, until the grants are issued again under new gids.
- **R-6. A strong admin key that is compromised signs grants until the other admins distrust it or its removal takes effect**
  (`admin_remove_h`, 24 hours by default).
- **R-7. A device that has not folded a removal or a distrust keeps counting it** until it syncs (T9).
- **R-8. A compromised weak admin key can distrust the strong keys** and so stop every pool-wide grant until the weak key is removed
  (`admin_remove_h`). It needs the account's terminal, which is what makes a key weak, and it widens nothing.
- **R-9. One key is both listed and approving until the phone's pool key is enrolled** (Terms): a pool's strong admin key is the
  phone's approval key. The enrolment slice changes `stands_for` and nothing else.

## 9. What changes besides the fold

- **Slices**, each landing alone, in this order: (1) the fold, R0-R5, `decide`, `decision` in the row, the listing fields and the
  pre-check of R10, the positive controls and the fixture helper; (2) strict pool loading and the floor (R6); (3) phones: the
  listed-phone path, pushes and reads, the refusals of R9 (R8); (4) distrust (R7) and its command; (5) the phone-add approval carrying
  `admin_add` (R8) and the session-move approval (R9), which need the phone app; (6) docs. Slices 1 and 3 are one release: slice 1
  alone leaves a joined device with no way to get an admin's phone.
- **Docs.** `docs/node.md` "Standing grants" (the paragraph "Who may sign", "What counts", the first-run bundle and its "stays with the
  person's own commands" text) states the two rules: ungoverned, an enrolled key; governed, an authority key for a pool-wide action and an
  enrolled key for a device-local one, with the table of R0. Add a line to "Who governs a pool", the `grant_distrust` call to the method
  tables, and the listing fields. `docs/design-phone-free-pools.md:58` ("Standing grants count any enrolled person key", "decided by any
  enrolled key's signature, with no phone test") is rewritten to the new fact. `src/poolhouse/board/grants.py` and
  `workspace/grants_cli.py` say in their heading and refusal text which key signs in a governed pool.
- **Phone app** (slice 5): the `add` request carries the new key's acceptance of being listed (`AdminKey::acceptance_message`); the
  approval card for a phone-add names the admin, the person and the hold; the decision for it carries the `admin_add` signature; the
  `rebind` card shows the session, the board and the terminal. Each signature uses a fresh biometric object (`PoolKey.java`), so this is
  likely two touches in one sitting; the builder reports what the app does.
- **Messages.** Plain, one sentence each: issue "In a pool your phone gives standing grants: ask for one and tap approve."; revoke
  "Only an admin key of this pool ends a grant of the pool."; decide "That key is not an admin key of this pool, so nothing was decided.";
  unenroll and phone remove "That key is an admin key of this pool: it leaves through an admin removal."; phone add "An admin's phone adds
  this phone; none is connected here."; listing `why` "signed by a key that is not an admin of this pool now" (or "not strong", "the
  recovery key signs no grant", "this pool lists no strong admin key", "this device does not know that key as a phone's", "that admin key
  is distrusted", "a pool's grants come from an approval on a phone").
- **Fixtures.** Tests that grant a pool-wide action in a governed pool with a key the charter does not list must go through the approval
  path: one helper, `kit::govnode::approved(dev, phone, token, actions)`, makes the `approval_request` and the `FakePhone` decision, in
  process and over the socket of the process rigs (which use `approval_decide`, as red test T3 does); the files are not patched one by one.
  The builder checks every file that founds a node (`govnode::found`, `found_for`, `found_remote`, `pool_create`) and also signs with
  `kit::allow`, `person()`, `enrolled`: at least `crash.rs`, `gov_found.rs`, `gov_net.rs`, `gov_strength.rs`, `processes.rs`; the
  process rigs plant the test person as the pool's phone key (`kit::govnode::plant_phone_person`), a planted Ed25519 key that R1
  no longer lets issue raw rows. `grants_sites.rs:63` needs no change: `pool_leave` is device-local. The Python rigs
  (`tests/pool_check`) are grepped the same way. `Governed::new` in `grants_authority.rs` stays as the negative fixture.
- **Board layer.** `rotate.rs:32` counts `distrust`; `fold.rs:283` `GRANT_KEYS` and `spec::check_entry` accept it.

## 10. Tests

Fail first on the parent commit, each by name; the four red tests already do and stay in `grants_authority.rs`. New ones (same file, or
`grants_authority_pool.rs` if it nears the size limit), each with its fail-first line in the commit message:

- the controls P-a to P-i;
- A4 and A17 assert the request is still pending and the fatigue ledger and block list are byte-equal;
- A12, A13, A14 (an admin revoke survives that admin's removal and the removal of its key from `persons.json`), A30 (a revoke beats its
  grant in either order of arrival), and A38 to A39 (distrust, in either order, and what it must not do);
- A21, A22, A23 (a removal with a hold, folded through `gov_tick`, not an edit of the roster; through each of the three entry points);
- A5, A6, A7, A8, A10, A11 against the fold with rows appended directly, so the call-site checks cannot hide a fold gap;
- A9 and A29 over two real nodes (`kit::netkit`), a foreign row built by hand with device `*` from a trusted device (an approval names its own node, so only a hand-built row can be portable);
- A32, A33, A40, A44, A52 (rebind, unenroll, nonce, session move);
- A41 and A54 (the decision in the row, the high-`s` signature), A42 (each kind of damage, in `Pool::load`), A43 (found, plant a row,
  change the version, reload, plant another, check both);
- A45 to A47 (phone paths), A50 (the table pinned to `ACTIONS`), A51 in Python with a prompt callable that fails the test if called;
- a bound on the fixed cost of `standing()` with 16 admin keys and many rows, and one on a request's tick.

Mutations after the commit (AGENTS.md section 5, "Commit before you mutate"): break the code, see the named test go red, restore with
`git restore --source=HEAD -- <file>`.

| # | Mutation | Test that must go red |
| --- | --- | --- |
| M1 | remove the authority conjunct from the issue refusal in `book::refusal` | T2, A5, A6 |
| M2 | remove the approval-only conjunct (accept a raw row for a pool-wide action) | A3, A5 |
| M3 | verify revoke rows with `persons.signed` again | A14 (the `persons.json` removal) |
| M4 | remove the narrowing-key conjunct from the revoke arm | A13 |
| M5 | apply every revoke by the pool-wide rule, or every one by the device-local rule | A12, A50 |
| M6 | remove R3 from `decide`, once per decision and per kind | T3, A17, A18, A19, A20 |
| M7 | drop condition (c), `key_strength` | A9, A10, A11 |
| M8 | drop condition (a) strength, or `mode == Admins` | A7, A8, A26, A36 |
| M9 | drop condition (d), the distrust read | A38 |
| M10 | distrust: drop the listed-signer check, the `since` match, the pool id match, or the cut exclusion | A39 |
| M11 | verify with `Alg::verifies` instead of `verifies_with` | P-e (A54) |
| M12 | rebuild the bytes with `approve` again, or drop the `repeat` pairing | A41, P-b |
| M13 | remove the tick in `api::run`, in `http::handle`, in `net::network` (three runs) | A23, once per entry point |
| M14 | tick inside `find` and drop it at the entry | A48 |
| M15 | cache the `Authority` snapshot on `Node` | A49 |
| M16 | remove the listed-key refusal in `unenroll`, or in `phone::remove` | A33 |
| M17 | move the refusal after `persons.once` | A44 |
| M18 | class `pool_leave`, or `link_read`, as pool-wide, or `file_delete` as device-local | A50, P-f, T1 |
| M19 | require an approval for a listed phone again | A45, P-g |
| M20 | push to every active phone; drop the gate on `list`, `get`, `wait` | A46 |
| M21 | drop `admin_add` from the phone-add approval | A47, P-h |
| M22 | `.ok()` and `unwrap_or_default()` back on `acts.json` or `seen.json`; read `charter.json` last in `create` | A42 |
| M23 | remove the floor | A43 |
| M24 | accept the typed-yes `rebind` signature in a governed pool | A32, A52 |
| M25 | remove the pre-check from `grants_cli.py` | A51 |
| M26 | treat a governed device as ungoverned in `class_of` | A37, T1 to T4 |
| M27 | drop `distrust` from `rotate.rs:32` | A31 |

Affected selectors for the build: `scripts/test all app/poolhouse-node/tests/grants_authority.rs` through
`tests/test_governed_grants_native.py`, plus the native files named in section 9 and `grants*.rs`, `approvals*.rs`, `sessions.rs`,
`gov_removal.rs`, `gov_acts.rs`, `gov_charter.rs`, `person.rs`, `phone_endpoint.rs`, `log_rotation*.rs`, and the Python files that drive
grants in a pool (`tests/test_board_approval.py`, the grants CLI tests); the phone app's checks for slice 5.

## 11. Owner decisions

Each is a visible step for a person or a trade between two safe behaviours; the recommended default is in force until the owner says
otherwise, and nothing waits for an answer.

1. **The first-run bundle after founding is one phone tap.** `grants-setup` in a governed pool asks for the three grants as one request
   and prints "Approve on your phone". *Default: one tap, three asks.* The weaker option is a typed yes by classing `task_authority`,
   `note_verify` and `shard_call` as device-local, which leaves a compromised agent with a terminal able to sign them and reach other
   devices.
2. **Moving a session (`rebind`) in a pool is a phone tap.** *Default: a tap on the phone, once per move.* The weaker option keeps the
   typed yes and lets a weak key take over a lead that holds approved grants.
3. **A non-admin member's agents get pool-wide grants from an admin's phone.** The admin scans one code at the member's device (the phone
   must reach that device once). *Default: the admin's phone.* The alternative is to list the member as an admin, which gives that person
   a say in the pool's governance; a separate member-approver role is a charter change and a version, not part of this fix.
4. **Adding a phone to a governed device lists it as an admin key of the same person.** One tap whose card says "admin" and names the
   hold. *Default: as written.* The alternative is a phone with no authority, which has no use today.
5. **A weak admin key may distrust at once.** The computer's typed-yes key can cut off a stolen phone without the phone. *Default:
   allowed.* The cost is R-8: a compromised computer key can lock out the phone's grants for up to the removal hold.
