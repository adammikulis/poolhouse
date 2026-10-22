# Charter-bound grants: design and break-it pass

Design for the rule "in a governed pool a grant counts only when a key the pool's charter names signed it", with the break-it pass
written before anything is built (AGENTS.md section 1, "Attack first"). Read at `949b19a52` (`0.3dev`) against `app/poolhouse-node/src`
(`grants/`, `approvals/`, `gov/`, `sessions.rs`, `fold.rs`, `landtrust.rs`, `board/rotate.rs`), `docs/node.md`, `docs/design-phone-free-pools.md`
and the four red tests (`app/poolhouse-node/tests/grants_authority.rs`, `tests/test_governed_grants_native.py`, uncommitted on
`fix/governed-grant-authority`). Nothing was run: every finding is from reading code and documents. Words follow [glossary.md](../glossary.md).

Author: claude-7d9978 (subagent of claude-2f6d13). **This is the author's own pass and does not satisfy the non-author requirement**: an
agent other than the author must break it before it is built, and for grants that reviewer is a cloud model (AGENTS.md section 4,
"Reviewing the backlog"). The fix is not built here.

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

`gov/pool.rs:427` `admin_scope` is written for this rule (key listed in the charter in force and its acceptance verifies) and nothing
calls it. `docs/node.md` ("Standing grants", "Who may sign") documents the weaker rule; `docs/design-phone-free-pools.md:126` already says the
intended one: "The charter decides who approves; `persons.json` is a cache of it".

## 2. The rule

**Terms.**

- *Governed*: `node.members.gov()` is `Some`, that is the device founded or joined a pool. A node is in one pool and every board it hosts is
  that pool's (`docs/node.md`, "One pool of devices, many boards"), so governance is a property of the node, never of one board. *Ungoverned*
  is the unfounded pool of one.
- *Charter in force*: `gov.state().chain.latest().charter`, read after `Node::gov_tick()` (section 4, T5). Never `chain.pending`, never a
  charter an act proposes.
- *Authority key*: an `AdminKey` of the charter in force with `role == Admin`, `strength == Strong`, whose own acceptance verifies for the epoch
  it was listed at (the check `admin_scope` has), while `charter.mode == Admins`.

**R1, issue.** In a governed pool an `issue` row counts only when every condition that holds today still holds (counted origin, portable
action for a foreign row, signature verified by a key enrolled on this device, key not removed) **and** its `person` is an authority key,
with the key material `persons.json` holds under that fingerprint byte-equal to the charter's (same `alg`, same public key). The row's
`person` is a claim; the signature is verified with the key material and the charter is looked up by the fingerprint of the key that verified.
The `phone` mark of `persons.json` is never read.

**R2, revoke.** A `revoke` row counts when today's conditions hold and its `person` is listed with `role == Admin`, of any strength, in the
charter in force **or in any earlier epoch** of this pool's chain (`GovPool::listed` already walks every epoch). A revocation only narrows, so
a weak key may give it (the same reason a weak key may end a delegate), and a removed admin's earlier revocations keep counting: otherwise
removing an admin would bring back what that admin revoked. The recovery role never revokes.

**R3, decide.** `approval_decide` requires, for every decision (`approve`, `approve_repeat`, `deny`, `block`) and every kind (grants, hold,
phone add, phone remove), that the deciding key is an authority key. It is checked right after the request is found and `allowed()` has
passed, before the signature is verified and before any state moves. A refusal leaves the request pending, the fatigue ledger untouched and
writes no audit event of its own. The rows `approved()` writes are still folded under R1, so R3 is a courtesy and R1 is the authority.

**R4, the calls.** `grant_issue` and `grant_revoke` apply R1 and R2 before they write, with a plain sentence: "Only an admin key of this pool
signs a standing grant here; ask for it on your phone." A row can arrive any other way, so the fold decides; the calls only refuse early.

**R5, ungoverned.** Unchanged: any key enrolled on this device, with every other condition as today.

**R6, fail closed.** Governed and (`charter.mode != Admins`, or no authority key listed, or the acceptance check fails): no grant counts.
`grants` lists the issue rows as `ignored` with the reason. A governed pool is never read as ungoverned because something failed to load:
`Pool::load` returns `Damaged` and the node does not start, which stays so (the one way to an unfounded device is the deliberate set-aside of a
charter of another version, R-1).

**Where.** One predicate in one place: a new `grants/authority.rs` builds an `Authority` snapshot once per `standing()` call (`Local` when
ungoverned, `Pool` otherwise, with the authority keys of the charter in force and the ever-listed admin fingerprints) and answers
`issuer`, `revoker` and `decider`. `admin_scope` becomes `GovPool::authority_keys` (it changes meaning, no wrapper keeps the old name). Callers:
`book::refusal`, the revoke arm of `book::standing`, `calls::issue`, `calls::revoke`, `decide::decide`, and (section 7) `sessions::rebind`
and `calls::unenroll`. The acceptance verifies are memoised per charter hash, so `standing()` adds a map lookup per row and no signature
check; a test bounds the fixed cost, because every guarded action runs `standing()`.

**What does not change.** The portable set (`task_authority`, `note_verify`, `sync_now`, `shard_call`); the landing trust list for foreign
origins; the floors; the five-minute freshness of an issue or revoke at the call; the uses count; the lineage rule at spend; the single
signature per grant (a grant is a permission to ask, and the governing act it may lead to still needs the threshold of admin signatures).

**What the rule costs, said plainly.**

- C1. A grant signed with a typed-yes key stops counting once the device is in a governed pool unless that key is an authority key. Today only a
  key a phone enrolled can be strong (`Node::key_strength`), so in a governed pool grants come through the phone's approval. The first-run bundle
  (`grants-setup`: `task_authority`, `shard_call`, `note_verify`) therefore has to be approved again after founding. The lead's lineage loses its
  30-day bundle at that moment. The computer-only admin key of `docs/design-phone-free-pools.md` (2.3, slice 3) is what removes the phone
  from this; until it lands the phone is the way, and the refusal says so in one sentence.
- C2. A non-admin member of a multi-person pool gets no standing grants for the agents on its device unless the admins list its key. That is
  "The admin key decides approvals and signs grants for its pool only" (`docs/design-phone-free-pools.md:141`), not a new limit.
- C3. Replacing or removing an admin key ends every grant it signed (section 4, T2). The remaining admins approve again.
- C4. Weak-listed keys sign no grant. The weaker option, letting a weak listed key sign the three bundle actions, is not taken: `shard_call` runs
  code on other devices and `task_authority` sets enforcement, and a weak key is one any process of the account with a terminal can use.

## 3. Every path that creates or counts a grant

| # | Path | Where | Today | After |
| --- | --- | --- | --- | --- |
| P1 | `grant_issue` call | `grants/calls.rs:86` | enrolled key | R4 early refusal; the fold decides |
| P2 | `grant_revoke` call | `grants/calls.rs:104` | enrolled key | R4 |
| P3 | `approval_decide` approve, then `approved()` appends rows as actor `node` | `approvals/decide.rs:171`, `:251-277` | `persons.get` and `persons.signed` | R3, then R1 in the fold |
| P4 | `approval_decide` deny, block, hold, phone add, phone remove | `approvals/decide.rs:171`, `:228-248` | any enrolled key | R3 |
| P5 | `use` rows from `grants::check` | `grants/mod.rs:123` | written after `find` | unchanged; not authority |
| P6 | rows replicated from a trusted device | `book.rs:65-73`, `landtrust.rs:69` | origin trusted, then signature | R1 and R2 per row; the origin rule is unchanged |
| P7 | a row appended to the board log by any other writer (the injected row of red test 2, a bug, a process with log access) | `fold.rs:285` checks shape only | enrolled key | R1 and R2 per row |
| P8 | rotation carrying grant rows | `board/rotate.rs:127-153` | keeps every grant and revoke row | unchanged: carries rows, adds no authority; see F-5 |
| P9 | consumers of `grants::find`: `holds`, `check`, `listing`, `flags::for_grants`, the duplicate-gid test in `calls::issue`, the found test in `calls::revoke` | `grants/mod.rs:88`, `approvals/flags.rs:35`, `grants/calls.rs:97`, `:115` | read `standing()` | read the same `standing()`, now charter-bound; nothing re-implements the fold |
| P10 | actions spent through the fold: `file_delete`, `shard_consent`, `shard_call`, `note_verify`, `task_authority`, `land_trust`, `quarantine_release`, `link_*`, `source_add`, `member_remove`, `add_device`, `join_pool`, `pool_create`, `pool_leave`, `admin_manage`, `sync_now` | `files.rs:176`, `shard/mod.rs:50`, `poolapi.rs:47-130`, `net.rs:619`, `landing.rs:193`, `quarantine.rs:134`, `tasks/calls.rs:46`, `notes.rs:165`, `api.rs:152-183` | all through `grants::holds` and `check` | no site changes: one choke point |
| P11 | a client that folds grants itself | `src/poolhouse/board/grants.py` reads the node's listing | the node's verdict | stays so; a second fold would be a second authority, the builder greps for one |

## 4. Time, order and the charter

- **T1. No signer time is compared with a removal time.** A grant's `at_ms` is chosen by its signer, a row's clock value by its writer
  (`held_back` bounds only the future side), and the chain measures removal in this device's awake time (`gov/clock.rs`), which has no
  relation to wall time. The charter in force at the moment of evaluation decides; a grant by a key not listed now is ignored however it is
  dated. Back-dating gains nothing.
- **T2. A removed admin's earlier grants all stop counting.** Acts keep a removed key's signatures that sit in the removal's cut
  (`chain.cuts`); a grant has no cut. Their `use` rows stay in the log as the record. The admins who remain approve again.
- **T3. A removed admin's revokes keep counting** (R2), so nothing a removed admin ended comes back.
- **T4. Order inside the fold is unchanged**: rows in the board's total order, the first valid row of a gid wins, a revoke beats the grant it
  names whatever the order. A row whose signer is not an authority key never claims a gid (the `why.is_none()` test), so a non-admin cannot
  squat a gid. A listed admin can: it can suppress another admin's grant by taking its gid, which it could do by revoking it. No new reach.
- **T5. The fold must be current.** `find` and every other reader of `standing()` call `Node::gov_tick()` first. It must be `gov_tick`, not
  `GovPool::tick`: the latter consumes the "due" flag and the node then skips `members.refresh()` and `apply_log_cuts()` for that
  transition. The cost is a deadline compare and a one-second throttle (`GovPool::tick`).
- **T6. Holds.** A pending `admin_add` is not in force, so its key signs nothing that counts; it counts from the epoch the add takes effect, which is
  after `admin_add_h`. A pending `admin_remove` does not suspend the key: acts of a key also count until its epoch ends, a hold is the pool's
  chosen delay, and a pending removal that froze grants would let one admin freeze another's by filing a removal. An open dispute changes
  nothing for the same reason. The remaining admins can revoke a suspect admin's grants at once (revoke has no hold) while the removal waits.
- **T7. Clock.** The wall clock still decides `until_ms` and `not-yet`. A hostile clock can revive an expired grant of a listed admin (not a
  removed admin's: R1 reads no clock) or end live ones early; that is unchanged and needs the machine.
- **T8. Concurrency.** One API call holds the node lock for its `holds` and `check`; both run `find`, so a removal ingested between two calls is
  seen by the second. The peer path (`authorize_from`) takes the lock for the pair. A removal that lands after a use leaves that use standing.
- **T9. Other devices.** Each device evaluates against the charter it has folded. A device that has not yet learned of a removal keeps
  counting that admin's grants until it syncs (bounded by the sync interval plus the hold, as for acts).

## 5. The attacks

Severity: **H** breaks the claim "only a pool admin grants", **M** a gap or a decision, **L** a note. "Red test" is the red test that
already holds the case (T1 `an_enrolled_non_admin_raw_grant_cannot_delete_the_shared_file`, T2 `a_valid_non_admin_signed_row_cannot_bypass_the_grant_fold`,
T3 `a_non_admin_approval_cannot_write_a_grant_that_deletes_a_file`, T4 `an_enrolled_non_admin_cannot_persist_a_consent_change`).

| # | Sev | Attack | Expected refusal | Red test |
| --- | --- | --- | --- | --- |
| A1 | H | Enrolled non-admin key signs `grant_issue` for `file_delete` on a shared file | `denied` at issue with the plain sentence, no row, the later delete `denied`, the bytes intact | T1 |
| A2 | H | The same key signs a `shard_consent` grant, then the session turns consent off | issue `denied`; `shard_consent` `denied`; `shards.json` not written | T4 |
| A3 | H | A validly signed non-admin row is appended to the log, or arrives from a trusted device | row listed `ignored: signed by a key that is not an admin of this pool`; the action `denied`; the foreign copy the same | T2 for the own log only |
| A4 | H | A non-admin key approves the agent's `file_delete` request | `decide` refuses before any state moves: request still pending, no row, ledger byte-equal, file intact | T3 asserts the file only |
| A5 | H | A non-admin builds the `approval` object by hand and signs the rebuilt challenge (`challenge::grant_bytes`), then appends the row | ignored: `person` is not an authority key | none |
| A6 | H | Relabel: the outsider signs, with `person` set to an admin's fingerprint | signature fails against the admin's key; ignored as not signed | none |
| A7 | M | A key listed with the recovery role signs an issue or approves | refused: "the recovery key signs no grant" | none |
| A8 | H | A key listed weak signs an issue, approves, or `grants-setup` signs the bundle with the typed-yes key | refused with one plain sentence that names the phone; no prompt is raised first | none |
| A9 | H | The charter lists a second admin whose key is not enrolled on this device, and that admin signs | still ignored ("not enrolled on this device"): the charter never supplies key material on its own | none |
| A10 | M | `persons.json` holds a different public key or `alg` under an admin's fingerprint (planted file) | refused by the byte compare of R1 | none |
| A11 | H | A key in `persons.json` marked `phone: true` that no charter lists | counts for nothing: R1 never reads `phone` | none |
| A12 | H | Non-admin key calls `grant_revoke` on an admin's live grant (a denial of service on the lead) | `denied`; the grant stays live | none |
| A13 | H | A revoke row signed by a non-admin is appended | ignored and listed; the grant stays live | none |
| A14 | H | An admin revokes a grant, then that admin is removed | the grant stays revoked (R2: ever-listed) | none |
| A15 | M | A weak-listed key revokes; a recovery-listed key revokes | weak counts (narrows only); recovery does not | none |
| A16 | H | A removed admin revokes everything on the way out | counts; the pool re-issues under new gids. Availability cost, accepted (R-5) | none |
| A17 | H | Non-admin key sends `deny` or `block` on the lead's request (counted as a person's decline until the lead's agent is blocked, `decide.rs:228-243`) | refused at the top of `decide`; request pending; fatigue ledger and block list byte-equal | none |
| A18 | M | Non-admin approves a `hold`, letting held requests through | refused; the request stays held | none |
| A19 | H | Non-admin approves a `phone_remove` of the admin's phone (`persons.remove`, never enrolled again, `person.rs:152`) | refused; the phone and its key stay | none |
| A20 | H | Non-admin approves a `phone_add` to mint a `phone: true` key | refused; and such a key still is not listed in the charter (A11) | none |
| A21 | H | An admin is removed (hold over); an earlier, unexpired grant of that admin is held here and on a trusted device | after the fold both are `ignored: signed by a key that is not an admin of this pool now`; actions `denied` | none |
| A22 | H | A removed admin signs a grant with `at_ms` before the removal and it arrives later | ignored: no signer time is read (T1) | none |
| A23 | H | The hold ran out on the awake clock and nobody has called `gov_tick` | `find` ticks first; the removed admin's grants do not count | none |
| A24 | M | A key in a pending `admin_add` signs | ignored: only the charter in force counts (T6) | none |
| A25 | M | An admin's removal is pending, or a dispute is open | that admin's grants still count (T6); `grants` carries a one-line note while a removal is pending | none |
| A26 | M | Charter `mode: members`, or a charter with no strong admin key | no grant counts; `grants` says why (R6) | none |
| A27 | H | Charter rotation replaces an admin key | the old key's grants end, the new key's are signed fresh, `grants` names the reason | none |
| A28 | M | Time-bomb: an admin signs a grant valid 366 days from a future `at_ms`, then is removed | ignored from the removal on | none |
| A29 | H | A trusted device writes a non-admin-signed row, or an admin-signed grant for a non-portable action | the first is ignored (R1); the second is ignored because the action is granted on the device it applies to (unchanged) | none |
| A30 | M | A trusted device replays an admin-signed grant that was revoked | revoke beats it whatever the order (`book.rs` header says so; no test in `grants*.rs` pins the order, so one is added) | none |
| A31 | M | A trusted device truncates its own log to drop a revoke | board layer: per-origin hash chain and `revokes_kept` at rotation; stated as a dependency | none |
| A32 | H | Non-admin key `rebind`s the lead's session to its own terminal and spends the lead's admin-signed grants (`sessions.rs:86`) | refused: R3's predicate applies to a move (F-1) | none |
| A33 | H | Non-admin key `person_unenroll`s an admin's key (permanent: removed keys are never enrolled again) | refused for any key the charter lists (F-2) | none |
| A34 | M | An agent taken in by an injected instruction runs `poolhouse-workspace grant` with the typed-yes key | one plain refusal, no extra prompt, nothing written | none |
| A35 | M | `approval_request` while no authority-key phone is enrolled on this device | refused at the request, one plain sentence, so the agent hears it and no non-admin phone is pushed to; the person is never asked to tap and then told no | none |
| A36 | M | A pool whose only admin key is weak | no grant counts and the listing says so (R6); the pool is otherwise usable | none |
| A37 | M | Ungoverned device: an enrolled key grants | counts, as today (R5); a test pins it so the rule is not applied to a pool of one | none |

Positive controls the four red tests do not contain, without which "refuse every grant in a governed pool" would turn them all green:
**P-a** an authority key issues `file_delete` and the delete works; **P-b** the authority key approves the request and the delete works;
**P-c** the authority key revokes and the action stops; **P-d** a foreign portable grant by an authority key counts on a trusted device;
**P-e** the ungoverned rule of A37.

## 6. The cases AGENTS.md names

| Case | Result |
| --- | --- |
| Copied or cloned pool or device | A copied charter makes no pool on another device (`pool_found`: the first device is part of the anchor), so a copy governs nothing. A copied whole state directory is a copy of the device, its keys and its trust: device compromise, outside this rule (R-1). |
| Replay | An issue or revoke at the call is fresh for five minutes and a gid is used once. A replicated row has no freshness by design: replay of an admin-signed row is a valid grant until it expires or is revoked. Across pools see R-2. |
| Forged or below-threshold signature | Forgery: the signature is verified with the key material and the charter is looked up by the verified key (A6). Below threshold: a grant is one signature by design (R-4); the pool's thresholds still guard the governing acts a grant lets a session ask for. |
| Compromised or malicious member | A non-admin member signs nothing that counts (A1-A5, A12, A17-A20, A32, A33). A malicious admin keeps admin power until removed (T6, R-6); the other admins can revoke at once. |
| Stolen phone or key | A stolen strong admin key signs grants until its removal takes effect, the same window as for acts, and every grant it signed dies with the removal (A21). A stolen non-admin key signs nothing. |
| Malicious agent | Holds a session token and no key; cannot sign, cannot decide (A4, A32). A signed body it read from the board is already on the board, so its gid is used and a resubmission is refused. |
| Agent compromised without knowing it | A34 and A35: the refusal costs no prompt and the person is not asked first. |
| Downgrade (secure to open) | Governed to ungoverned needs a write to the state directory (R-1), the same class as planting a key. The change adds no downgrade of its own: a charter that cannot be read stops the node (R6); `mode`, a weak-only charter and a failed acceptance fail closed. |
| Approval fatigue | R3 refuses a non-admin decision before it moves the ledger (A17), so a non-admin key cannot bomb the agent, and refuses at request time (A35) so no tap is wasted. |
| Takeover by whoever is merely present | Presence at a terminal with a typed-yes key grants nothing in a governed pool (A8, A34); `rebind` and `unenroll` are closed (A32, A33). |

## 7. Adjacent holes the same predicate closes

These are not in the four red tests. F-1 is part of the fix because without it a non-admin key spends admin-signed grants.

- **F-1. `rebind` takes any enrolled key** (`sessions.rs:86`). A non-admin key signs the move of the lead's session to its own terminal
  and then spends the grants an admin signed for that session. Fix: in a governed pool the move's signer must be an authority key
  (`decider`); ungoverned stays.
- **F-2. `person_unenroll` and `person_enroll`'s `by` take any enrolled key** (`calls.rs:213`, `:245`). A non-admin key can remove an admin's
  key from `persons.json` for good ("a key that is removed is not enrolled again", `person.rs:183`), which silences that admin on this device
  and, by R1's byte compare, ends its grants. Fix: in a governed pool `person_unenroll` refuses any fingerprint the charter lists (a listed key
  leaves through `admin_remove`), and its signer, like `by` of an enrolment, must be an authority key. Own commit, availability not authority.
- **F-3. Approval list read** (`approvals/calls.rs:176`): any enrolled key reads every pending request's text. Fix: authority key only.
- **F-4. `approval.node` is not compared** (`challenge.rs:73`): a grant row carries the device the approval was made for and the fold never
  compares it. A portable admin-signed row for device N1 with scope device `*` therefore counts on N2 by design (the admin approved text that
  named `*`); a non-portable one cannot arrive (own origin only). Noted, no change.
- **F-5. Ignored rows are never dropped** (`rotate.rs:127-153` keeps every grant row; `kept.len() > rows/2` refuses the rotation). A flood of
  non-admin-signed rows from a trusted device fills the log until its rotation is refused. Dropping an ignored row is unsafe (a re-listed key
  would lose it), so the bound belongs on the trusted origin's grant-row quota. Own item.

## 8. Accepted residuals

- **R-1. The state directory is a set of plain files.** `persons.json` and `pools/<id>/` can be edited by a process that can write them
  (documented in `docs/node.md`, "Accepted residual"); setting the pool aside makes the device ungoverned. The agent sandbox is what closes
  it. The `grants` listing gains a `governance` field (governed or unfounded) so the change is visible to the person.
- **R-2. No pool id in the signed body.** The same admin key in two pools signs a body valid in both if the board, session name and device
  match, and a writer on the second pool's trusted origin can replay it. Closing it changes the phone's challenge (the phone signs bytes the
  node builds), so it waits for that change; preconditions are narrow (a shared admin key, a shared board name, a trusted malicious device,
  a portable action).
- **R-3. A removed key that is listed again revives its older unexpired, unrevoked grants.** The body carries no epoch. Advice in the removal
  card: revoke first.
- **R-4. One strong admin signature authorises a local, irreversible action** (`file_delete`, `shard_consent`). The 24-hour cancel window and
  several-step approval of `docs/design-phone-free-pools.md` (2.6) are that design's slices.
- **R-5. A removed admin can revoke** and so cancel every grant, until the grants are issued again under new gids.
- **R-6. A strong admin key that is compromised signs grants until its removal takes effect** (`admin_remove_h`, 24 hours by default), the same window as for acts.
- **R-7. A device that has not folded a removal keeps counting it** until it syncs (T9).

## 9. What changes besides the fold

- **Docs.** `docs/node.md` "Standing grants" (the paragraph "Who may sign", "What counts", the first-run bundle and its "stays with the
  person's own commands" text) states the two rules: ungoverned, an enrolled key; governed, a strong admin key of the charter in force. Add a
  line to "Who governs a pool". `docs/design-phone-free-pools.md:58` ("Standing grants count any enrolled person key", "decided by any enrolled
  key's signature, with no phone test") is rewritten to the new fact. `src/poolhouse/board/grants.py` and `workspace/grants_cli.py` say in their
  heading and refusal text which key signs in a governed pool.
- **Messages.** Plain, one sentence each: issue/revoke "Only an admin key of this pool signs a standing grant here; ask for it on your
  phone."; decide "That key is not an admin key of this pool, so nothing was decided."; listing `why` "signed by a key that is not an admin
  of this pool now" (or "not strong", "the recovery key signs no grant", "this pool lists no strong admin key").
- **Existing tests that sign in a governed pool with a key the charter does not list** must sign with the charter's key (a fixture change in
  the same commit): `app/poolhouse-node/tests/grants_sites.rs:63` (`a_granted_pool_action_can_be_used_only_as_often_as_it_was_granted` founds
  with the rig's signer key, then issues with the separate test person key of seed 0x70). The builder checks every file that founds a node (`govnode::found`, `found_for`,
  `found_remote`, `pool_create`) and also signs with `kit::allow`, `person()`, `enrolled`: at least `crash.rs`, `gov_found.rs`, `gov_net.rs`,
  `gov_strength.rs`, `grants_sites.rs`, `processes.rs`; the process rigs plant the test person as the pool's phone key
  (`kit::govnode::plant_phone_person`), which is the charter key there. The Python rigs (`tests/pool_check`) are grepped the same way.

## 10. Tests

Fail first on the parent commit, each by name; the four red tests already do and stay in `grants_authority.rs`. New ones (same file, or
`grants_authority_pool.rs` if it nears the size limit), each with its fail-first line in the commit message:

- the controls P-a to P-e;
- A4 and A17 assert the request is still pending and the fatigue ledger and block list are byte-equal;
- A12, A13, A14 (an admin revoke survives that admin's removal), and A30 (a revoke beats its grant in either order of arrival);
- A21, A22, A23 (a removal with a hold, folded through `gov_tick`; not an edit of the roster);
- A5, A6, A7, A8, A10, A11 against the fold with rows appended directly, so the call-site checks cannot hide a fold gap;
- A9 and A29 over two real nodes (`kit::netkit`), a foreign row from a trusted device;
- A32 and A33 (F-1, F-2);
- a bound on the fixed cost of `standing()` with 16 admin keys and many rows.

Mutations after the commit (AGENTS.md section 5, "Commit before you mutate"): remove the charter conjunct from `refusal`, then from the
revoke arm, then from `decide`, then the `gov_tick` call; each must turn a named test red.

Affected selectors for the build: `scripts/test all app/poolhouse-node/tests/grants_authority.rs` through
`tests/test_governed_grants_native.py`, plus the native files named in section 9 and `grants*.rs`, `approvals*.rs`, `sessions.rs`,
`gov_removal.rs`, `person.rs`, and the Python files that drive grants in a pool.
