# Attack pass: one key per pool, and verifiable custody

A break-it pass by a non-author on `docs/design-pool-keys-and-custody.md`, read together with `docs/design-multi-pool-devices.md` and
`docs/design-phone-free-pools.md`, against the code at `7a1d2f6bc` (`0.3dev`). Read-only: no code was run, no test was run. Paths are under
`app/poolhouse-node/src/` unless they start with `src/`, `docs/` or `app/`. Verdicts: **hole** (the design as written fails), **gap** (the design is
silent or inconsistent and the build would have to guess), **ok** (holds, with the residue named).

## Verdict

Part A (a key per pool) holds with three fixes (F8, F9, F12). Part B (statements) must not be built as written: three of its own rules cannot be
evaluated by the code that exists (F4, F6) or give different members different answers (F1, F5), and two let one member change what other members' keys
count for (F2, F3, F7). Slice 2 may start after F8 and F9; slice 4 waits for F1 to F7.

## Findings

| # | Case | Verdict | Severity |
|---|---|---|---|
| F1 | Level diverges between members; replay-downgrade by a relayer | hole | high |
| F2 | The `vouch` act raises a floor-gated key by one device's word | hole | high |
| F3 | Voucher removal, leave or a thief's statement zeroes other people's keys; no way back | hole | high |
| F4 | The attestation revocation summary cannot be checked by a member | hole | high |
| F5 | A build or root difference sets an honest voucher aside on some members only | hole | high |
| F6 | The challenge needs a handle the statement does not carry; two vocabularies | gap | medium |
| F7 | `distrust` and `trust` by one key: a threshold bypass, an undo of a proven lie, a cycle | hole | high |
| F8 | The custody secret: the node stays off the keystore, but the leak claim is wrong | hole (claim), ok (mechanism) | medium |
| F9 | The admin key's sealing account is bound to a path: a normal move loses every admin key | hole | high |
| F10 | Clone, restore and equivocation lean on a heartbeat that does not exist | gap | high |
| F11 | Statements are free and unrated: banner flood and room exhaustion | hole | medium |
| F12 | Irreversible destroy taps carry no prompt | hole | medium |
| F13 | The 31-day sweep reads the local wall clock | gap | low |
| F14 | The node decides which keys approve grants, so the split protects governance only | gap | medium |
| F15 | The `LEVEL:` line recompute has no independent source | gap | medium |

### F1. Level computed by every member diverges (C3, C4, 3.3, 2.6)

- **Attack.** C3 picks the newest statement "by the order this member first saw it". That order is local (`gov/roster.rs:187-200`, `seen`) and is not in
  any signed byte: `prev` is the hash of the charter the signer knew (`gov/act.rs:5`), not the signer's previous statement. A member that relays acts to a
  joiner or to a member that resyncs chooses the order they arrive in, so it can deliver an old high statement after the new low one and that member counts
  the key higher than every other. C4 ("when this device first has the whole act and again when the hold ends") and the 90-day aging (a receiver clock)
  add two more local inputs. A late joiner replaying history cannot reproduce the verdicts the old members reached. `gov/pool.rs:532` and `:610` order
  folds by `at_ms`, which the signer picks, so a voucher can also backdate a statement to sort it before an older one.
- **Fix.** The body carries a per-(voucher, key) counter `seq` the voucher signs and persists before sending; the newest statement is the highest `seq`, never
  an arrival order or `at_ms`. Two statements with one `seq` and different bytes are an equivocation proof (F10). State the fold as a function of the act set
  and the clock only: counted level of a key at an act is computed at the act's position in the chain order, with aging read from `at_ms` of the act
  being counted (not the receiver's clock) plus a stated skew, so a joiner and an old member agree.

### F2. A statement raises a level with no admin act (C3, 3.2, 3.4)

- **Attack.** The row's `claim` is covered by the key's acceptance and the admins' signatures (3.2); the statement's `claim` is signed by the voucher's pool key
  alone, which the always-running, networked node holds. C3 says a statement that raises the counted level "counts at once". The document never says which
  of row claim and statement claim governs. If the statement does: a weak key already listed (R3 lists keys that count for nothing; a developer's typed-yes
  key is the normal case) is raised to `computer` by one automatic, prompt-less signature, and the program that holds that weak key now counts toward
  floor-gated acts. If the row does, the statement can only lower and C3's raise rule is dead text. This undoes the point of the admin-key and pool-key split:
  the person-facing signer holds the admin key so the node cannot decide who counts, yet the node's pool key decides at what level each admin key counts.
- **Fix.** Counted level = lower of `level_of(row.claim)`, `level_of(statement.claim)` and the basis cap. A statement can only lower or restate. Raising is a
  governance act that re-lists the row with a new `claim` under the key's fresh acceptance (so a card, a prompt and the governance holds), exactly as
  `admin_add` does today. Say this in 3.1 and C3 in one sentence.

### F3. Voucher gone means keys zeroed, with no repair (3.4, 3.6, 2.6, 6 "Stolen device")

- **Attack.** Basis is none when "the voucher has left, was removed, set aside or distrusted", and a key with no basis counts `weak`. Consequences:
  - Lost laptop. The phone key's voucher is the laptop that enrolled it. Removing the laptop (a sensitive act, no hold) turns the surviving phone key weak at
    once. Replacing it needs a counting key's `admin_add`; none counts. The pool's governance is dead until `recover`, which needs a recovery key the
    design lists as optional ("no recovery key yet"). "Listed again by a device that holds it (rotation)" needs the same counting keys.
  - A malicious or compromised voucher leaves (`leave` needs no person, `docs/design-multi-pool-devices.md` 3) and zeroes every key it vouched. Or the thief
    of that laptop posts lowered statements (C3 counts a lowering at once) in the minutes before the owner's removal act reaches the hold test (C4 "a key
    that falls below during a hold leaves the act short"): the thief cancels the owner's cleanup.
  - Attested keys: the chain "lasts 90 days from `at_ms` and then reads vouched", and with the voucher gone that is none. A phone whose computer is off for
    three months (a drawer, a trip) falls to `weak` with nobody awake to see the banner.
- **Fix.** (a) A removal or leave of a device names a successor voucher for each counting key it vouched; the successor is a device of a signer of that act,
  and the act's own signers are counted at their level before it takes effect. (b) `revouch {key, voucher}` as a governance act carrying the key's own
  acceptance. (c) A pending act's signers are counted at their level when first had; a statement or standing change by the same voucher never cancels an act
  another voucher's keys signed (only an admin `distrust` or removal, with its own threshold, can). (d) Aging an attested statement needs a renewal by any
  active device, not only the original voucher, and the first banner comes at 60 days.

### F4. The revocation summary cannot be verified by a member (3.2 `revocations`, 3.3, 3.4 C2)

- **Attack.** `phone::attest::verify` takes `&FreshRevocations` (`phone/attest.rs:91,109`), an in-memory object built from the whole fetched response body
  (up to 2 MiB, `phone/attest/revocations.rs:86-128`) with an `Instant` and a 24 hour maximum age. The statement carries only `digest`, `fetched_ms` and
  `valid_until_ms`. A member cannot rebuild the object from those three values. It either fetches the list itself at ingest (a network call in a fold, a
  result that changes with the minute, and F1/F5 divergence) or believes the voucher's three numbers, in which case a revoked key is restated as fresh
  forever. The "90 days old" test uses `at_ms`, which the signer sets to now for a three-year-old chain: it measures the statement, not the attestation.
- **Fix.** Make chain acceptance purely structural and deterministic (roots, leaf key equals the statement's key, challenge, per-use authentication, boot
  state, app id), and take revocation out of the statement. Each member applies its own revocation list as a local overlay that can only lower a key's
  counted level, never set a voucher aside (F5), is shown as a banner like any fall, and is cached by act id. Use the key description's creation time when
  the extension carries it for the age, else drop the 90-day claim and say aging is of the statement only.

### F5. Root or policy skew sets an honest voucher aside on some members (3.6, 3.3)

- **Attack.** 3.6 calls "a chain that does not reach a committed root" provably false, and every member then derives `set_aside`. Committed roots and the
  publisher policy are per build (`attest/SOURCES.md`, `attest/publisher.rs`), and the design depends on members running different builds (no
  migration, a build that does not know a kind treats it as unevidenced). A phone chain that reaches a root the newer build commits is a lie on the older
  member only, and the reverse: the older build sets the honest voucher aside, the newer does not, and their delegate-admit and key folds fork. Vendors
  rotate attestation roots, so this is the normal case, not an edge.
- **Fix.** Only contradictions inside the bytes that no policy changes set a voucher aside: a signature in the chain that fails, a leaf key that is not the
  statement's key, a challenge that does not match, malformed DER. An unknown root, an old version floor and a revocation are unattested (cap `computer`).

### F6. The challenge cannot be recomputed; two claim vocabularies (3.1, 3.2)

- **Attack.** The challenge is `SHA-256(... key's handle + voucher fingerprint + nonce)`, and the `vouch` body has `key` (the public key), `claim`, `evidence`
  with `nonce`, and no handle. The handle is local (2.4, 32 random hex digits). A member cannot compute the challenge, so "a challenge for another voucher
  is provably false" (slice 5 test) is not implementable, and "the chain proves this key was made for that voucher" is unchecked. The public key cannot
  stand in: the Android key is made before it exists as a listed key, which is why the handle was chosen. Separately 3.1's closed vocabulary lists
  `phone-keystore`, `helper`, `keystore-prompt`, `keystore-typed`, `file`, and 3.2 says a `subject: device` statement's method is `keystore`,
  `passphrase` or `file`: by 3.1's own rule a word outside the vocabulary is not a claim, so the self statement of 2.5 is invalid.
- **Fix.** The handle goes in the charter row (covered by the acceptance) and in the body; the member recomputes the challenge from them. Give `subject:
  device` its own table (`custody` with `keystore`, `passphrase`, `file`), and read an absent self statement as not `keystore` for a delegate admit.

### F7. `distrust` and `trust` by one key (3.6, 4)

- **Attack.** Both are routine acts "any one counting key signs". A distrusted voucher's keys read `weak` and its delegate admits count for nothing, so one
  `distrust {device}` does to every admin key that device vouches what `admin_remove` does, without the threshold and without the hold. In a 2-of-3 pool one
  compromised key zeroes the other two keys' voucher device and then acts alone. `trust` is the same single signature and "clears `set_aside`": one
  compromised key silently undoes the mechanical verdict on a provably lying device, and a distrust by an honest admin is undone by the next `trust`.
  Alternating the two flips every dependent level on every member (F11). A cycle also exists: a `distrust D` signed by a key that D vouches stops counting
  once it takes effect, so it does not count, so D is in good standing, so it counts. The design gives no evaluation order.
- **Fix.** `distrust` is sensitive class with the threshold, and keys vouched by the target do not count toward it. `trust` is governance class (threshold
  and hold), and `set_aside` clears only by remove-and-admit under a new certificate (a new voucher identity), never by `trust`. Standing is evaluated in
  chain order: an act is counted with the standing in force at its own position.

### F8. The custody secret (2.2, 2.4, 2.5, 2.7 row 4)

- **Does the node stay off the keystore?** Yes: `device.rs:1-7` and `:93-134` give the key to the node on standard input and the node writes only a note and
  a record; the same path carries the secret. That part is ok.
- **Hole in the claim.** The node unseals each open pool's key, up to 16 ("opening sixteen pools unseals each key once"), and holds the secret that opens
  every `key.wrapped`. A crash dump, a debugger attach, a memory-disclosure bug in the node's network parsers or a log line of the secret therefore reaches
  every pool, not "that pool" (2.2 table row 4). Separation between pools is a property of files at rest only. Say so, hold only the open pools' subkeys
  in memory (derive per slot, wipe the secret after the opens), and `zeroize` on drop.
- **Custody is the launcher's word.** `Supplied.custody` is written to the record from what the starter says (`device.rs:128-131`); any process of the
  account can start the node over a file-held secret and name `Keystore`, which is what the delegate rule of 2.5 reads. That is the stated "says so"
  weakness; the delegate admit rate caps it. Keep it in the card words and do not describe the rule as a check.
- **Sealing is bound to the slot, not to the pool.** The slot id is in the file's header and derives the subkey; nothing in 2.4 binds the pool id or the
  certificate into the AEAD associated data or checks that `key.wrapped` matches `cert.der`. A program of the account that swaps two pools' key files
  between directories (with each file's own header) makes pool A sign heads with pool B's key, which links the pools. Fix: associated data is pool id,
  slot and certificate fingerprint; refuse a key whose public value is not `cert.der`'s.
- **Re-seal.** "The secret is re-sealed by walking the pool directories" must write each file atomically and record progress, or a crash mid-walk leaves
  files under two secrets.

### F9. The admin key's account is bound to the directory path (2.4, 2.6, 7 K7)

- **Attack (no malice needed).** `signing.py:109-110` makes the keystore account `onboard-signing-` + a hash of the resolved directory path, and 2.6 relies
  on it ("an admin key's file opens only under the same directory path as well"). A Migration Assistant move to a Mac with another account name, a renamed
  home, a state directory moved to another volume, or a reinstall under a different path leaves every admin key permanently sealed. K7 forbids export, so
  the only way back is the 24 words for each pool, a 7-day hold each, and only for pools that wrote the words down (the recovery key is optional and takes 7
  days to list). The ordinary move of a non-technical person to a new computer ends every pool's governance. The path binding buys no protection: a
  copy with the keychain opens either way, a copy without it opens neither.
- **Fix.** For `person/pools/<handle>/` use the handle as the account owner (`pool-key-` + handle), not the path. Also make the recovery key part of
  founding (offered, one tap, still never a blocker) so the way back exists by default.

### F10. Clone, restore and equivocation (2.6 "Backups and restores", 6 "Cloned state directory")

- **Attack.** Every clone row ends with "each pool flags it by its own heartbeat" and cites `docs/pools-and-serving.md:292-296` as "designed, not built". The
  node source has no `boot id` or heartbeat counter (a search of `app/poolhouse-node/src` finds none), so today nothing detects a restored or copied
  device, and K5 removes the hold that would bound it. A restore from an older backup also re-signs: its persisted counters are behind, so it can sign a
  second statement with a `seq` (F1) or a `prev` it already used, and the members that saw the first and the second disagree on which stands. "Destroyed"
  keys come back from a backup whose keychain opens them; the design states that and it is harmless for listed-out keys, but a pool key of a pool the
  device left reappears with a valid certificate until a rejoin replaces it.
- **Fix.** Land the heartbeat as slice 0 of this plan (the clone rows are not evidence until it exists), or mark them "not covered until the heartbeat
  lands". Two statements by one voucher with the same `seq` and different bytes are a signed equivocation proof every member can check from bytes: that is
  the one provable lie available for a software key, and it sets the voucher aside. Add it to 3.6.

### F11. Free, unrated statements (3.2, 3.7, room rules)

- **Attack.** "Statements need no prompt, tap or card" and every fall or rise of any key's level is a banner, an operating-system notification on every
  member device and an audit entry (3.7). A voucher whose node is compromised (or a thief with the laptop) can flip a key's claim every few seconds: the
  newest-two rule bounds storage, not the rate, so the flood is the notifications themselves, which teaches the person to dismiss the one real
  banner. `gov/room.rs` indexes device-signed acts by admits and leaves only (`MOST_JUNK`, `MOST_JUNK_PER_DEVICE`); a `vouch` for a key the pool does not
  list "counts for nothing" but still takes a slot of the shared `MAX_ACTS` (8192) and up to 16 KiB each, so a device can fill the table and push out
  honest device acts.
- **Fix.** One accepted statement per (voucher, key) per hour (the rest refused at ingest, not stored), at most 32 held `vouch` per device, a statement for
  an unlisted key refused at ingest, and notifications for level changes coalesced per voucher per day with the audit entries kept.

### F12. Irreversible taps with no prompt (2.6 "Leave", "Removed by others")

- **Attack.** "Leave anyway" destroys the sole admin key at once and "[Destroy it]" destroys a still-listed admin key; neither costs a prompt ("a tap and
  no prompt"). The app's session is whoever is at an unlocked computer, which is the takeover-by-presence case: a passer-by or a guest ends every
  future change of the pool by two taps. AGENTS.md allows friction where an action is irreversible.
- **Fix.** The same ticket prompt as any governance act for a key that is still listed; or a soft destroy (the file moved aside, restorable for 7 days with a
  banner on this computer) with no prompt.

### F13. The 31-day sweep (2.6 last row)

- **Attack.** The sweep is a local wall-clock rule on a file nothing lists. Setting the clock forward, or a laptop offline longer than 31 days with an
  `admin_add` it never received, deletes a key about to be listed. It cannot be undone (K7).
- **Fix.** Sweep only after the key's age on the awake clock exceeds the longest hold plus the act's `not_after`, and move it aside for 7 days first.

### F14. The node decides who approves (2.3, 2.1 "Signs")

- **Gap.** The admin key signs "approvals and grants for sessions of this pool", but whether a signature counts for a grant is decided from
  `persons.json` rows (`grants/person.rs`), a file the node writes, with the new `pool` field and, in phone-free slice 2, the device's signature, which is the
  pool key the node holds. A compromised node (the lowest-trust process of the two) enrols a key of its own as pool A's approver. The split therefore
  protects charter acts, not approvals.
- **Fix.** A sensitive or irreversible grant counts only if its key is a charter-listed admin key at the counted level (the fold from F1 to F3);
  `persons.json` is a cache of the charter for that. State this in 2.3.

### F15. The `LEVEL:` line recompute (3.7)

- **Gap.** "A signer given a different line from the one it recomputes refuses to sign". The signer is the person-facing process; the level and basis come
  from the node's fold and from chain verification the signer does not run. If the signer asks the node for its recompute, the check proves the node agrees with
  itself. Name the source: the signer verifies the charter and the statements it is shown from bytes (it needs the same `gov/custody.rs` fold, built
  once as a shared crate or a small verifier binary), or the line is described as the node's word.

## The AGENTS.md cases

| Case | Attack tried | Verdict | Residue / fix |
|---|---|---|---|
| Cloned or copied pool, device or directory | Copy `<state>` without the keychain; with it; restore an old backup | gap | Without the keychain inert (ok). With it a second device with the same certificates: detection is an unbuilt heartbeat (F10); equivocation proof added |
| Replay | A statement or chain replayed into another pool, or an old high statement redelivered | hole for ordering, ok across pools | Across pools the voucher's per-pool certificate, the act's `pool` and the key's acceptance hold (ok). Within one pool a relayer controls arrival order (F1) |
| Forged or below-threshold signature | Forge a `vouch`; sign a `distrust` below threshold | ok for `vouch`, hole for `distrust` | A `vouch` needs the listed voucher's pool key (ok). `distrust` has no threshold (F7) |
| Compromised or malicious member | Leave to zero keys; flood; set aside an honest device; lower keys | hole | F3, F5, F11. A member cannot raise above its cap, but a voucher can raise to the cap (F2) |
| Stolen phone or key | Thief with a phone and the finger; thief of a computer with the password | ok for the phone, hole for the computer | The computer thief controls its keys' voucher and cancels the owner's cleanup (F3); the stolen-device cleanup card turns keys weak without a successor (F3) |
| Malicious agent | Ask the signer for a level, write a statement, edit `persons.json` | ok for statements and levels | No call takes a claim (ok). A node-level injected agent enrols approvers through `persons.json` (F14) |
| Agent compromised without knowing | An injected "vouch as hardware" order | ok | No path from text to a statement (ok); statements are written from live facts, so a compromised node writes them |
| Downgrade (secure to open) | Change the policy; lower floors; lower a key | ok for policy, hole for levels | Policy is in the charter (ok). A lowering counts at once and cancels pending acts (F3); a raise needs no admin (F2) |
| Approval fatigue | Many cards; many banners | hole | Cards are paced (ok); banners and notifications are not (F11); the lost-computer queue of 3 prompts per 10 minutes is honest, the prompts of F12 are missing |
| Takeover by whoever is present | At an unlocked computer | hole | Two unprompted destroy taps end governance (F12); a present person raising a prompt needs the password (ok) |
| Lying voucher | Say `keystore-prompt` for a file key; a false chain; replay a good chain | ok for the cap, hole for the rest | Capped at `computer` (ok, named). A lie that raises a weak key is not capped by the row (F2); a false chain on a different root is judged differently by members (F5); the revocation is unchecked (F4) |
| Restored backup | An old backup restores a clone, resurrected keys, old counters | gap | F10; and the same restore under another path loses admin keys (F9) |
| Custody-secret leak | A dump or debugger on the node; a file swap | hole in the claim | F8 |
| Admin key and pool key split | Node compromise; node-written level and approver rows | hole | F2, F14 |
| `distrust` / `trust` abuse | One key | hole | F7 |
| Level diverging between members | Arrival order, clock, build, root | hole | F1, F4, F5 |
| `vouch` used to raise a floor-gated act | Raise a weak key, or lower the others to defeat a hold | hole | F2, F3; R4's "a floor is refused when no listed key holds the new level" reads the counted levels, so F1 and F3 also let a lowering statement make a legitimate floor change refuse |

## What holds

- Independent random seeds per key (2.2), the sealed-at-rest model, and no export of an admin key: sound once F9 is fixed.
- Cross-pool unlinkability of pool keys, admin keys, certificates and recovery salts (2.7), apart from the named open question; the open question's safe
  default (option 2, nothing new revealed) is right.
- Computed level from a claim and a basis with no level field (K3) and the `computer` cap on a word nobody can check (K4).
- A statement by a device other than the listed voucher counts for nothing; a statement never removes a key from the charter (C5).
- Migration none, and the version bump set aside by the existing rule.

## Order of fixes

1. F1, F2, F7 change the statement and act formats: settle them before slice 4's charter 5 and acceptance `v3`.
2. F4, F5, F6 before slice 5 (it stays parked until they and the open question are settled).
3. F8, F9 before slice 2 and slice 1 (they change what is sealed and under which account).
4. F3, F10, F11, F12, F13, F14, F15 before slices 6, 7 and 8, and F10's heartbeat before any "clone" row is cited as a defence.
