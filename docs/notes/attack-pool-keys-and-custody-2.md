# Attack pass 2: one key per pool, and verifiable custody

A second break-it pass by a non-author on the revised `docs/design-pool-keys-and-custody.md` (Part B: statements, `seq`, standing, `distrust`, `trust`, `revouch`, the live transfer of 2.8; Part A changes), against `docs/notes/attack-pool-keys-and-custody.md` (F1 to F15) and `docs/design-phone-free-pools.md` (classes, holds). Read-only: the design and notes were read, no code was read or run. Verdicts: **hole** (fails as written), **gap** (silent or inconsistent), **ok** (holds, residue named).

## Verdict

F1 to F15 are closed in the text, with the residues in the table below. The new mechanisms reopen two of them (F1 by G1 and G2, F7 by G4) and the live transfer is not safe to build as written (G9 to G11). Slices 5 and 7 wait for G1 to G4; slice 10 waits for G9 to G11.

## F1 to F15 closure

| F | Closed in text? | Residue |
|---|---|---|
| F1 level diverges | yes: `seq`, position fold (3.2, 3.4) | G1 (stale `prev` rewrites history), G2 (ingest limits depend on arrival) |
| F2 statement raises | yes: 3.1 lowest-of rule | none |
| F3 voucher gone zeroes keys | yes: C3, 3.7 | G5 ("without a gap" is false), G8 |
| F4 revocation unverifiable | yes: no chain in a statement (K5, 3.9) | none |
| F5 root skew | yes: K9, 3.9 | none |
| F6 challenge, vocabularies | yes: no challenge; `custody` table | G13 (device statement has no `row`) |
| F7 distrust by one key | text yes: sensitive class, standing at position | G4 (still hold-free and uncancellable) |
| F8 custody secret | yes: AEAD data, one-operation secret, atomic re-seal | none |
| F9 path-bound seal | yes: handle | G10 (aside copy restorable) |
| F10 clone, restore | yes: slice 0, equivocation | G3 |
| F11 free statements | yes: hourly gap, 32 cap, coalescing | G2 |
| F12 destroy taps | yes: listed key asks the ticket | none |
| F13 31-day sweep | yes: awake clock, 7-day aside | none |
| F14 node decides approvers | yes: charter decides (2.3) | none |
| F15 `LEVEL:` recompute | yes: shared crate, signer runs the fold | a node can withhold a lowering statement or proof from the signer; the named residue (old consistent chain) should say so |

## New findings

| # | Case | Verdict | Severity |
|---|---|---|---|
| G1 | A stale `prev` makes a statement or an equivocation pair rewrite already-counted acts | hole | high |
| G2 | Hourly gap, 32 cap, newest-two and skew refusal depend on arrival order: members hold different acts | hole | high |
| G3 | Equivocation compared by bytes: a clone, a restore or an eclipse sets an honest voucher aside for ever | hole | medium |
| G4 | Removing a voucher, its leave or `distrust` neutralizes another person's admin keys with no hold and no cancel | hole | high |
| G5 | "The keys count without a gap" contradicts the 7-day hold of `revouch` | hole | medium |
| G6 | Acceptance freshness: a `revouch` replay and a revived statement set (A, B, A) | gap | medium |
| G7 | A node-proposed card can `revouch` a key to an attacker device | gap | low |
| G8 | C3 un-lowers a key its voucher honestly lowered | ok with residue | low |
| G9 | Transfer: no identity for the new computer, no confirmation, a LAN attacker burns the guesses | gap | medium |
| G10 | Transfer: a lost receipt leaves two holders; the aside copy is live and restorable by a tap | hole | high |
| G11 | Transfer: the voucher stays the old computer, so a move forces `revouch` in every pool | hole | medium |
| G12 | Nothing counts above `computer`: consequences | ok, named, regressions listed | medium |
| G13 | `seq` namespace of a device statement; `distrust` unreachable where the target vouches every key | gap | low |

### G1. Stale `prev` (3.4 table, 3.6, C2)

- **Attack.** 3.4 counts the statement with the highest `seq` "whose `prev` position is at or before p". The only constraint on `prev` is that a higher `seq` is not older than a lower one's. A voucher (a thief with the laptop's pool key is enough) signs `seq` 2 lowering its keys with `prev` equal to `seq` 1's, which can be the start of the pool, and publishes it a month later. At every position from that old `prev` on, `seq` 2 stands: acts its keys signed and that already took effect (an `admin_add`, a `member_remove` of another device) no longer count at their position and the chain re-folds without them. 3.6 does the same for an equivocation pair: `set_aside` "from the position of the later `prev` of the two", and the pair can both carry an old `prev` (the `prev` rule binds only different `seq`). C2 promises "an act keeps the verdict of its position"; 3.4 and 3.6 break it. A joiner and an old member still agree (the fold is of the act set), so tests of F1 stay green, but history is rewritten by whoever holds one voucher key, for every act signed by keys it vouches except cure acts (C3).
- **Fix.** Anchor the effect to signed data the voucher does not choose: the signer's signed `LEVEL:` text (3.8) lists, for each signing key, the `seq` it used and the proofs it saw; the fold counts that signature at the cited `seq`, and a later statement or proof lowers only acts signed after their signers cited it. A lowering statement then protects the future, never the past; a withholding node only delays the effect. Equivalent for the proof: standing changes for acts that cite it.

### G2. Ingest depends on arrival (3.2 "How many stand", "Time", 3.3)

- **Attack.** "One statement for a (voucher, key) is accepted for each hour of `at_ms` after the last; the rest are refused and not stored". "The last" is the last this member accepted. Member A sees `seq` 2 at t then `seq` 3 at t+10 minutes and keeps 2; member B sees 3 first, then refuses 2 (not after the last): A holds {2}, B holds {3}, and they compute different levels for ever, though the fold is "a function of the act set". The newest-two, the 32 cap and the five-minute skew refusal (a receiver clock) have the same shape. A voucher can choose which members hold which statement by timing, and a relayer by delivery order.
- **Fix.** Make the limits functions of signed content: store every statement whose `seq` is above the highest `seq` already counted, evaluate the hourly rule in `seq` order against the previous `seq`'s `at_ms` (a statement that fails it never counts, on every member), keep the newest two by `seq`, and drop the skew refusal from the fold (a skewed statement only counts from the position that cites it, G1). Storage bounds stay, but the held set of one voucher is then the same on every member that has them all.

### G3. Equivocation by bytes (3.2 `seq`, 3.6, 2.6 restores)

- **Attack.** Two statements with one `seq` and different bytes set the voucher aside, and `trust` never lifts it. `at_ms`, the nonce and `prev` differ whenever two devices sign. A Time Machine or Migration Assistant restore while the original lives, two computers holding one certificate, or a restored voucher that syncs only through a peer that hides its newer statements (an eclipse; "syncs the pool" is not "has everything") signs the same `seq` twice. The honest owner is set aside for ever and must remove and re-admit the device under a new certificate, then `revouch` every key it vouched, with 7-day holds. The same state restored over the original is the clone case slice 0 is meant to flag, so detection is wanted, but the penalty is permanent and the trigger is a byte difference with the same meaning.
- **Fix.** Compare the semantic body (`subject`, `key`, `row`, `seq`, `claim` or `custody`), not the bytes: two statements that say the same thing are not a lie. Different bodies under one `seq` keep the set-aside. Replace "remove and re-admit" by one card, **Replace this device**, that removes it, re-admits the computer and carries the `revouch` of its keys in one prompt.

### G4. A neutralizing act with no hold (3.6, 3.7, C5, phone-free 2.6)

- **Attack.** `member_remove` and `distrust` are sensitive: no hold, no cancel window (phone-free 2.6: only the irreversible acts wait 24 hours). Admin keys stay listed (C5), but a key whose voucher is removed, set aside or distrusted reads basis none and counts `weak`. In a 1-of-2 pool (or any pool where B's key meets the sensitive threshold) B signs `member_remove` of A's device: A's admin key stops counting for every ordinary act at once, and B acts alone. A's only way back is `revouch`, a governance act held 7 days, and a card that would keep A's key counting needs A's own acceptance (3.7), which B cannot give. `admin_remove` of A's key, the act meant for this, is governance: 7 days and the recovery key's cancel. The design thus gives a hold-free, uncancellable way to do what a held, cancellable act was made for. `distrust` does the same without removing the device. The thief-of-a-laptop defence needs the same speed, so the fix must tell them apart.
- **Fix.** Neutralizing another person's keys (a removal or `distrust` of a voucher whose keys name a `person` that none of the act's signers hold) takes effect after the governance hold, cancellable by the recovery key and by a cure-act cancel of the neutralized keys (C3 lets them count for it). A removal or `distrust` whose signers include a key of the same `person` as the neutralized keys is immediate (the lost-computer card). Mechanical `set_aside` from a proof is immediate, as it needs no admin.

### G5. "Without a gap" (3.7)

- **Attack.** "The keys count at their level without a gap" is said of the removal card that carries `revouch`. `member_remove` takes effect at once; `revouch` is a governance act held 7 days when only `computer` keys sign, and with G12 that is every pool. From the removal until the hold ends the keys have basis none: a 7-day gap, the one 3.7 admits only for the case of a device that leaves.
- **Fix.** Say the gap, or close it: while a `revouch` named in the same card is pending, the removed voucher's standing for those keys is frozen at its removal position. The thief's keys on the removed laptop are removed by the same card (`admin_remove`), so the freeze gives the thief nothing the card does not already end.

### G6. Acceptance freshness and row digests (3.1, 3.2, 3.7)

- **Attack.** `row` is "the digest of that key's acceptance", and a `revouch` carries "the key's fresh acceptance". If the acceptance covers only key, claim, voucher and pool (the text says `claim` and `voucher` besides today's fields), a listing A, then `revouch` to B, then back to A has the same digest as the first: every statement made for the first A (the highest `seq`, maybe a lowering made then and cured by the re-listing) revives, and the old acceptance bytes, public in the chain, are reusable by an admin quorum to put a key under a voucher without the holder's fresh consent.
- **Fix.** The acceptance covers the listing act's nonce (or the epoch and the previous row's digest), so each listing has a unique digest; say "fresh" means that.

### G7. The successor on the card (3.7, 2.6)

- **Attack.** A compromised node proposes a removal card with a `revouch` of the person's key to a device of its choosing; the one prompt is the ticket for all of it. The successor can then lower the key (a denial until a cure act and its hold) and equivocate.
- **Fix.** The prompt text and the `describe` line name every successor in words ("'Pixel' will stand behind this key"), computed by the signer from the bytes; a card with a successor the person did not choose is refused by the signer.

### G8. C3 and an honest lowering (3.4 C3)

- **Attack.** C3 counts a D-vouched key at `min(row claim, computer)` for cure acts "ignoring D's statements". A key D lowered because it is compromised still counts toward `admin_remove` of D's other keys, `revouch` and `member_remove D`.
- **Verdict.** ok: it is the price of letting a thief-voucher not block its own removal, the acts are few and held, and a `weak` row stays `weak`. Name it in 3.4 as accepted.

### G9. The transfer channel (2.8 steps 1 to 3)

- **Attack.** (a) Step 2 uses "both certificates' fingerprints as identities", but the new computer has no certificate (pool keys are per pool and made at a join, which is step 5); the identity is undefined. (b) The code authenticates whoever holds it, not the intended computer: a person who reads the code to a "helper" gives away every admin key of every pool; the old card never shows the receiver's name. (c) "Three wrong codes end it": a listener on the network sends three wrong codes and ends every transfer, each retry costs a prompt, so the attacker buys approval fatigue.
- **Fix.** Use an ephemeral per-transfer key on each side as the SPAKE2 identity; after the exchange the old computer shows the new computer's name and a short fingerprint to confirm in the same prompt; three wrong codes retire the code, not the ticket: the old mints a new code for 10 minutes with no new prompt.

### G10. Half-finished transfer, two holders, the aside copy (2.8 steps 3 and 4, 2.6 "Moved aside")

- **Attack.** The receipt names public keys only, so a lost receipt leaves the new computer holding a sealed seed and the old computer not knowing; the old keeps signing, and "only the new one is told it may sign" names no sender or order. A false receipt from the receiver makes the old destroy a key nobody holds (the aside copy is what saves it). After `moved` the old key sits in the aside directory 7 days and 2.6 says a banner restores it with a tap: the reason for a move is often a computer the person no longer trusts, and a program on it restores the live key (listed, so it is not harmless like an unlisted key).
- **Fix.** A transfer has an id and three states. The receiver seals the seed but stays `pending` and signs nothing. The old computer first marks `moved` durably, then sends `release`; the receiver activates on `release` and answers with a signature by the key itself over the id (proof it opens after reopening from its own keystore). Lost release: the receiver retries on reconnect; no response in the window: the old computer's aside copy restores it by the ticket prompt, never by a banner tap. On the proof the old deletes the aside copy at once, so no live seed stays on a computer being left. At most one active holder at every step.

### G11. The voucher after a move (2.8 step 5, 2.6, 3.7)

- **Attack.** Step 5 says "a charter changes nothing". The charter row names the old computer as voucher; after the move it vouches a key it no longer holds, and the 30-day re-derivation ("the key is still in the keystore") has no claim for "absent" (3.1 has no such method), so it signs nothing or a stale restate. When the person removes or leaves with the old computer, the keys read basis none until a `revouch` per pool, each a governance act (7 days, one prompt each, 3 in 10 minutes). "One prompt, one code" in 2.6 understates the move by a join approval and a `revouch` for every pool.
- **Fix.** The move card bundles, per pool, the new computer's join and the `revouch` of every moved key to it, and says the pacing first (as the lost-computer card does). Add `absent` to the vouch vocabulary as a claim that counts `weak`, signed by the old voucher on `moved`.

### G12. Nothing counts above `computer` (C6, K4, 3.8)

- **Attack and verdict.** ok and named in the text; what follows is not all said. (a) A phone key counts as a laptop key, so in a mixed pool malware on the laptop signs `member_remove` of every device with no hold, which today's phone floor forbids (phone-free R1); the 7-day hold belongs to governance acts only. (b) Every governance act in every pool is now held 7 days, because no key above `computer` exists to waive it (G5, G11, `admin_add` of a second person). (c) A pool made on a phone cannot keep a `phone` floor; existing floors cannot be set. (d) The words "Add a phone" in the header (3.8) suggest more security than the counted level gives.
- **Fix.** Say (a) and (b) in 3.8 and in the phone-free doc's R1 text, so no card or help line implies a phone is stronger; rename the header button to "Add another device". Reopen the cap only when an evidence kind (3.9) exists.

### G13. Device statements (3.2)

- `seq` is "for each (voucher, subject, row)", and a `subject: device` body has no `row`; the namespace and the equivocation rule for it are not defined. Use (voucher, subject, key) with the device's own fingerprint as the key.
- `distrust` excludes the keys the target vouches from its threshold, so where one device vouches every key, nobody can `distrust` it; the cure is `member_remove` of the device (a cure act, counted by C3). ok; say it.

## The brief's cases

| Case | Verdict | Where |
|---|---|---|
| `seq` reuse or rollback after a restore | hole: same-seq re-signing by a clone or an eclipsed restore sets the voucher aside for ever; the persisted counter lives in the restored state | G3 |
| Equivocation proof used to frame an honest voucher | a third party cannot (needs the voucher's signature); the voucher's own clone, restore or eclipse can; a stale `prev` makes the effect retroactive | G1, G3 |
| `distrust` as a denial lever | hole: sensitive means no hold and no cancel, the same effect as `admin_remove` | G4 |
| Successor-voucher takeover | ok for signing (the key's own acceptance is needed); a successor gets the power to lower the key; the card must name it | G6, G7 |
| Live transfer: interception | ok for the code (SPAKE2, 88 bits, online guesses limited); gap for identity, receiver confirmation and a guess-burning attacker | G9 |
| Live transfer: half-finished, two holders | hole | G10 |
| `revouch` replay | gap: depends on the acceptance carrying a nonce | G6 |
| Nothing above `computer` | ok, named; consequences listed | G12 |

## Order of fixes

1. G1, G2, G3, G6 change the statement and acceptance formats: before slice 4's acceptance `v3` and slice 5.
2. G4, G5 change the classes and the card: before slices 6 and 7.
3. G9, G10, G11 before slice 10 (and G11's `absent` claim before slice 5's vocabulary).
4. G7, G12, G13 are text and card changes with their slices.
