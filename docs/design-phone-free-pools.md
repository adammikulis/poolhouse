# Pools without a phone

Design. Nothing here is built. Read at `29d9f78f7` (`0.3dev`): section 1 is code and document reading, no test was run and no screen was driven.
A non-author review (section 7) is applied: the verdict was accept with edits.

The owner's order: "we can't block a pool on a phone" and "some users may not want to connect their phone". A person with only a computer
must be able to found a pool, add a device, approve what an agent asks, remove a member and get the pool back if the computer is lost, with
taps and plain sentences, and the pool must say honestly how much weaker a computer's approval is than a phone's.

## 0. In short

- **Today a computer-only person cannot found a pool, add a device, remove a member or recover an admin key.** Only approving a grant works,
  by a typed `yes` at a terminal. The cause is one rule and two gaps: only a key a phone enrolled counts as strong (`Node::key_strength`), no
  production `Signer` exists for any key, and a delegate device cannot sign because `Node::key_binding` is `File` everywhere.
- **The idea.** A key counts according to its assurance level, which the node derives from evidence and the charter records. The charter names
  the lowest level that counts for routine acts and for sensitive acts. Levels, highest first: `phone`, `native` (a protected helper on the
  computer), `computer` (the computer's own prompt), `weak` (a typed yes). Raising the bar is immediate; lowering it is held and needs keys at
  the old level, so nothing downgrades silently. Acts fall in three classes: routine, sensitive and governance (2.6). A pool is never blocked
  on a phone: where a `computer` key's word is too weak for an act, the act is held with a cancel window and a notification, never refused.
- **The computer journey.** Found a pool: say it, tap **Make pool**, answer the computer's one prompt (2 counted steps).
  Add a device: tap to open the join window on the first computer, type the code it shows into the new device (1 tap and no prompt while
  the 30-day delegate lives). Approve: tap, prompt. Remove a member: open, approve, prompt, pick a number. Recover: 24 words, seven days.
- **Honest weakness.** At `computer` a program running as you outside the agent sandbox could sign without the prompt, and the level is only the
  issuing computer's word (2.2). What stands in the way is stated per operation in 2.5: holds and cancel windows, longer when only
  `computer` keys sign (2.6). `native` closes that and not root; `phone` closes both.
- **Build.** Eight slices (section 3). The protocol lands first (slice 1); the review findings H1 and H2 are settled in this design before slice 1 is
  built, and H3 before slice 6. After slice 4 a computer-only person can found a pool, approve,
  add a device by a card and remove one; slice 5 makes a join one tap and a code and ends with the live check on the owner's Mac. Windows and Linux
  without a prompt are slice 7: those computers join pools and found none until it lands, and the refusal says why in one sentence. A
  standard (non-administrator) macOS account is slice 3.
- **Three decisions** made by the owner, recorded in section 5. The review log is section 7.

## 1. What the code does today with no phone

Paths are under `app/poolhouse-node/src/` unless they start with `app/`, `src/`, `tests/`, `docs/` or `HANDOFF`.

### 1.1 The words

HANDOFF names **Native** and **AndroidStrong**. Neither word is in the node, the library or the phone app, and none carries an assurance field;
the only assurance wording is the inactive Swift card's `trusted-native` (`app/native-approval/Card.swift:65`). The only levels in use are
`Strength::{Strong, Weak}` (`gov/keys.rs:30-33`), and a key is strong only when its row in `persons.json` says `phone: true`
(`grants/person.rs:197-199`), which only a phone's own enrolment writes (`phone/enrol.rs:198`, `phone/mod.rs:212`). The Native foundations are
`app/native-approval` (a Swift approval window using `LAContext`) and `app/poolhouse-authority` (a root-owned request and compare-and-swap core
with a macOS authorization backend). Both state that they are inactive: no installer, no IPC server, no key registry
(`app/poolhouse-authority/SOURCES.md:3`, `app/native-approval/ADMISSION.md`). The phone's attestation checks (`phone/attest/`,
`gov/phone_verify/`) have no caller outside themselves.

### 1.2 Operation by operation

| Operation | With only a computer's key | Why, with the line |
|---|---|---|
| **Found a pool** | **Refused.** The library stops first: `default_admin` always raises (`src/poolhouse/board/founding.py:202-207`) and a draft with no strong key raises `NEEDS_PHONE` (`:191-192`, text `:29`). The node refuses as well. | `Node::key_strength` is strong only for a phone's key (`gov/ops.rs:157-159`); `founding_of` and `pool_create` both require one (`ops.rs:172-177`, `:283-285`). The charter rules would take an all-weak charter (`gov/charter.rs:217-219`, `:306`), so the block is in the entry points, and two tests pin it (`app/poolhouse-node/tests/gov_strength.rs:44`, `tests/test_pool_found.py:116`). |
| **Add a second device** (join code and approval) | **Refused at the admit.** The join-code exchange itself needs no key (`net.rs:350-402`). The admit after it (`net.rs:338` into `ops.rs:299-340`) is signed by a delegate device or by a strong admin key's signer; with neither the answer is "I need your OK for that, and nothing here can ask you yet." (`ops.rs:41-43`, `:322-326`). `add_device` refuses first for the same reason (`net.rs:357-359`, `ops.rs:258-268`). | The delegate never signs: `Node::key_binding` is `File` on every device (`node.rs:164`; the only setter is the rig's, `ops.rs:190-192`), though the node already knows its key's custody (`node.rs:61`, `:152`; `device.rs:28-34`). An `admit` signed elsewhere and submitted (`presigned`, `ops.rs:397-404`) is used only by the pool check. An `open` pool admits by a member's own device key with no person (`ops.rs:314-318`), but an open pool cannot be founded either. |
| **Approve a grant** | **Works, by a terminal.** Standing grants count any enrolled person key, phone or not (`docs/node.md:396-400`). The first key needs the operating system's prompt (`presence/mod.rs:210-228`: macOS, Linux with `pkexec`; Windows refused, `:224`). Each grant then needs a typed `yes` on `/dev/tty` (`src/poolhouse/board/person_key.py:1-11`). | A terminal step is forbidden by the onboarding rule, and the screen that would replace it is not built (`docs/node.md:1091-1094`; no approval view in `app/src-tauri` or the web UI). An agent's approval request is decided by any enrolled key's signature, with no phone test (`approvals/decide.rs:178-188`), but the Python side can only request, wait and cancel (`src/poolhouse/board/approval.py`): nothing on a computer decides. |
| **Remove a member** | **Refused.** `gov_remove` asks a signer for one strong key (`ops.rs:141`, `:343-356`). A weak key signs only `delegate_end` (`gov/pool.rs:361-367`), so the answer is "None of the keys I can ask is an admin of this pool." (`ops.rs:45-47`). | There is no signer to ask (`node.rs:164`: `signer: None`; `set_signer`, `ops.rs:185`, is called only from tests and rigs). A device **leaving** needs no person and works (`ops.rs:359-368`). |
| **Recover an admin key** | **Not built, for anyone.** There is no `recover`, `recover_cancel`, `recovery_replace` or `endorse` act (`gov/body.rs:173-222`; GOV-5, `docs/release-readiness.md:193`). | A recovery-role key can be listed (`gov/keys.rs:38-41`) but `pool_draft_found` makes only admin keys (`ops.rs:469`) and a recovery key signs nothing (`gov/pool.rs:365`). `docs/node.md:479-481` resets a node's grant keys, not a pool's admin key. `poolhouse-pool recovery export` (`docs/fleet.md:233`) recovers the old shared passphrase, not a governed pool. A pool that loses its only strong key keeps working and can never change (`docs/pools-and-serving.md:19-21`, `:336-342`). |

**A phone does not do them either.** The phone's signer and approval transport are protocol classes only
(`docs/notes/2026-10-14-phone-founding-protocol.md`, `HANDOFF.md:46-47`), so every row above is also "not built" with a phone. What the order
asks is that the computer need not wait for it.

### 1.3 What the pool check proves

Nothing about a computer-only pool.

- `found-by-api` says "the admin key the node holds as a phone's" signs (`tests/pool_check/steps_pool.py:64`); `docs/pool-check.md:97` says "the
  key the rig's person typed yes for" signs. The code agrees with the first. `World.fresh` gives every device the person's key
  (`tests/pool_check/world.py:107`); `wire.start` writes it into `persons.json` marked `phone: true` before the node starts
  (`tests/pool_check/wire.py:129-135`, called at `:141-142`); `RigPerson.enrol` then finds it held and returns (`person.py:75-77`), so the typed-yes branch of the rig does not
  run in `fresh` either. Every governed step (found, admit, remove: `person.py:146-165`) is signed by that in-memory, phone-marked key.
  `tests/node_kit.py:97-105` does the same for `tests/test_pool_found.py`.
- No step shows a weak key refused or accepted. The nearest (`steps_pool.py:69-71`) shows that a charter no key accepted founds nothing.
- `phone-approval` uses a P-256 key made in the run over real TLS and decides grants only; it covers no governance act.

So the check proves counting, journeys and refusals for a planted marked key, and nothing about custody, a phone or a computer
(`HANDOFF.md:48` says the same).

### 1.4 What exists to build on

- The governance core already allows an all-weak charter, a recovery role, a delegate with rates, holds, cancel and veto (`gov/`).
- The operating system's prompt as the root of the first key: an agent can ask for it and cannot answer it; it is refused for root and for any
  directory but the account's own (`presence/mod.rs:152-228`), waits two minutes (`:28`) and is quiet for ten minutes after a refusal (`:31`).
- Approval requests with node-written plain text, the fatigue ledger and the number to match (`approvals/`, `docs/phone-approval.md`).
- The Python seam `grants.Signs` (`src/poolhouse/board/grants.py:40-46`), which `founding.found` already signs charters through, and a person key
  sealed under the keystore in `<state>/person`, which the agent sandbox may neither read nor write (`docs/agent-sandbox.md:38`, `:88-89`).
- Device-key custody in the keystore (`device.rs`), the roster's delegate rules (`gov/roster.rs`), `Role::Recovery` protection (`gov/chain.rs:199`, `:228`).
- The seam the phone also needs: the node `Signer` over approvals (`HANDOFF.md:47`). The computer is the shorter road to it.

## 2. The design

### 2.1 Levels

| Level | Shown to the person as | Where the key is | Each signature needs | Stops | Does not stop | State |
|---|---|---|---|---|---|---|
| `phone` (HANDOFF: AndroidStrong) | "your phone" | the phone's hardware keystore, attested | a fingerprint on the phone | every program on the computers; a stolen computer | a stolen unlocked phone and its owner's finger | protocol classes only |
| `native` (HANDOFF: Native) | "this computer, protected" | a protected helper under another operating-system identity | the password or fingerprint in the helper's own window | agents and programs running as you | root on that computer; a stolen unlocked computer whose password is known | foundations, inactive |
| `computer` | "this computer" | an Ed25519 key sealed under the account's keystore in `<state>/person` | the computer's own prompt, raised by the node for one card (today an administrator's password on macOS; Touch ID unverified, 2.9) | agents that go through Poolhouse or sit in the agent sandbox; a state directory copied without the login keychain | a program running as you, outside the sandbox, that unseals the key itself; a stolen unlocked computer whose password is known | new (slices 1 to 4) |
| `weak` | "a typed yes" | a key beside the node | a typed `yes` | accidents | any program with a terminal of its own; counts toward nothing but ending a delegate | exists |

Protection against programs on the computer runs `phone` over `native` over `computer` over `weak`; the evidence for each differs, and the order
only serves the floors of 2.6. `computer` is a **weak key in the glossary's sense** (a process of the account can use it). It differs from a typed yes
in two ways: its first use needed the computer's own prompt, and it signs only through the card, so no terminal path exists for it. Its level
is the issuing node's word and no other member can verify it (2.2), so `computer` alone never decides governance or an irreversible data act without a
hold (2.6). HANDOFF's
Native covers the whole computer side; it is split here because the protected helper is not built and a pool must not wait for it.

### 2.2 How the node knows a level

The node derives it; a caller never says it (`app/poolhouse-node/tests/gov_strength.rs:58` already refuses a draft that names a strength, and keeps doing so).

- `phone`: today's mark, moving to verified attestation (`phone/attest/`) in the phone branch. Unchanged here.
- `native`: the admitted issuer of the protected helper (slice 8).
- `computer`: the key was enrolled through the operating system's prompt on the account's real home, for a new prompt purpose `pool_key`. The
  prompt shows the key's words and the yes is for that key only, as for the first key today (`presence/mod.rs:393-396`). It is allowed while other
  keys exist, unlike the first-key prompt (`presence/mod.rs:417`). The node writes the level into the key's row, signed with its device key, so
  a `persons.json` written by a program that lacks the device key is not believed. The file otherwise stays a plain file (`docs/node.md:466-469`).
- `weak`: every other enrolled key: one a terminal's typed yes enrolled, any key on a directory that is not the real home, and a pool key whose
  store is the passphrase file of a computer with no keystore (`src/poolhouse/fleet/onboard/signing.py:145-158`).
- Other members read the level from the charter. For a key founded by someone else they have the founder's word, and the joiner's screen says so
  ("this pool says it is approved from a computer"). A key added later by another computer counts as `computer` at most, vouched by the card the
  pool's keys signed; `native` and `phone` need their own evidence.
- **No member can verify `computer`.** The level is the issuing node's word: a key added later is vouched by a card, and a node that enrols a key
  through the prompt shows no proof of it to anyone else. So the rules of 2.6 never let `computer` alone decide governance or an irreversible data
  act without a hold, and `native` and `phone` are the levels a member can check (attestation, the admitted issuer).

### 2.3 Where the secret lives and what signs

- **One pool key per computer**: an Ed25519 key made on first use, sealed by `SigningKeys`, the wrapper today's person key uses (`src/poolhouse/fleet/onboard/signing.py:96`), under the keystore, in
  `<state>/person/` (denied to sandboxed agents). It is also enrolled as a person key, so it decides approvals and signs grants. A developer who
  prefers a terminal keeps today's typed-yes key beside it; that key stays `weak`. A person who never opens a terminal never makes one.
  The same key serves every pool on the computer: a leak of it reaches all of them, and the one public key in each charter links the pools to
  each other for every member that sits in two of them. This is named, not fixed (a key per pool costs a prompt per pool); `native` ends it.
- **The node holds no person key**, as now (`gov/signer.rs:3-5`). A signer in the person-facing process, `ComputerKey` (a `Signs`), signs only
  with a **ticket**:
  1. The act or grant is drafted by the node (`pool_draft_found`, `pool_draft`), which writes the card text from the bytes (`gov/describe.rs`).
  2. The person opens the card in the Poolhouse window and taps **Approve**: a call only the person's own UI session can make, never an
     agent's session (`docs/node.md` "The person's session").
  3. Only then the node raises the computer's own prompt, once for the card, with one sentence the node writes from the act's bytes.
  4. On a yes the node gives the signer a ticket for that card id, the SHA-256 of each payload it names and a two-minute life, usable once.
  5. The signer rebuilds the bytes from the act it was given, refuses any that differ, unseals the key, signs the payloads, wipes the key.
  6. The signed act goes back through `pool_found` or `pool_submit`.
- A card names a **bundle** (the charter acceptance, the `pool_create` grant, the 30-day delegate) and costs one prompt, as the first-run bundle
  of `docs/pools-and-serving.md` 2.4 does. The signer signs each payload only if its kind and pool are ones the card names.
- **Bundled tickets.** One ticket may name up to 10 routine cards that the person opened together ("Approve 4 requests from your agents"),
  and costs one prompt. A sensitive or governance card is never bundled: one prompt each.
- **One ask at founding.** The first pool key is made and enrolled by the same prompt that answers the founding card: the `pool_key` enrol prompt
  of 2.2 carries the card's sentence, and its yes is both the key's enrolment and the ticket. A second pool on the computer finds the key and
  raises only the ticket prompt. The system's keychain may add a dialog of its own for the new item; that is unverified and 2.9 records it, and
  until it is recorded the friction contract counts 2 prompts for the first pool.
- **What the prompt is, and is not.**
  - On macOS it is `do shell script ... with administrator privileges` through `osascript` (`presence/mod.rs:133-141`): the system's
    dialog for an **administrator's name and password**, titled by `osascript`, not by Poolhouse. Any program running as you can raise the same dialog
    with any text, so the prompt is not proof that the node raised it. That is the weakness of `computer` again (2.1), and `native` is the fix.
  - Its text is cut to 160 characters of letters, digits and ` .,-/()` with no quotes (`plain`, `presence/mod.rs:133-134`). The node never cuts a
    sentence: it builds the sentence from fixed words, the act's kind, the pool's name and a short fingerprint, and **refuses to raise the prompt when the
    sentence is over 160 characters**, with one plain sentence on the card ("This name is too long to confirm here; shorten it").
  - The sentence is written from the act's bytes and never from the agent's words: the card's `WHY: The agent says: ...` text
    (`approvals/content.rs:228`) and any task text never enter the prompt string.
  - A standard (non-administrator) macOS account cannot pass an administrator's password dialog, so a family Mac fails onboarding. Slice 3 gives
    such an account a prompt it can answer with its own password (the candidate is the `LAContext` policy that `app/native-approval/Card.swift`
    already uses; not run on a standard account yet, so the live check of 2.9 proves it).
  - **Prompt limits.** At most 3 operating-system prompts in 10 minutes and 20 a day on a computer, counted by the node. The ask ceiling of
    `ops.rs:28-31` (12 asks in 10 minutes) counts asks and would allow about 72 password prompts an hour, so it is not the limit of prompts. A
    request over the limit waits as a card; nothing is refused and no phone is asked.
- Not chosen now. A key bound to Touch ID in the Secure Enclave needs a signed app with a keychain entitlement and so a paid Apple account
  (`docs/pool-encryption.md` 8.1). A browser's platform authenticator keeps the key non-extractable without one, but its prompt cannot show the
  act, any page on `localhost` shares the credential, and a passkey may sync off the device with no attestation, so the node could not tell a
  device-bound key from a copied one. Revisit as a custody for `computer` after slice 5.

### 2.4 The person's journeys

A person bad with tech: Poolhouse installed, then taps and plain sentences. No terminal, file, key or port.

**Found a pool** (journey 1, one computer)

| # | The person | What happens | Counted |
|---|---|---|---|
| 1 | Says "Make a pool called home" | The agent calls `ph.pool.found`; the card appears in the Poolhouse window and as a notification | say |
| 2 | Taps **Make pool** on the card: "Make the pool 'home' (secure) and put the computer 'Studio' in it. This computer will approve changes to it. This computer may admit devices without asking for 30 days." | The node raises the prompt | tap |
| 3 | Answers the computer's prompt (the account's password) | The one prompt enrols the pool key if none exists and is the ticket; the signer signs the bundle; the pool exists and the computer is its first member | prompt |
| 4 | (Optional, once, never a blocker) the app offers "Write down your recovery words" | See recovery below | optional |

Counted: 1 tap, 1 prompt (2 until the live check of 2.9 shows the keychain adds no dialog). The pool exists with no phone and no terminal. The
header reads "no recovery key yet" until step 4 is done and its 7-day hold has run. The same flow runs for the `open` policy, with the card saying "anyone on
this Wi-Fi can join".

**Add a second device** (journey 2)

| # | The person | What happens | Counted |
|---|---|---|---|
| 1 | Says "Add my other laptop" on the first computer | The agent asks for the window; a card appears in the app: "Let a new device join 'home' with a code shown here. Open for 5 minutes." | say |
| 2 | Taps **Open window** on the card (a tap in the app, which only the person's own session can make) | **The first computer's screen shows the code**, and the agent is told only that a window is open | tap |
| 3 | Types the code into the new device's Poolhouse (it finds the pool by its beacon) | The code is checked by SPAKE2, three tries per window; the first computer's delegate device key signs the admit | type |
| 4 | Nothing. The first computer shows "'laptop-2' (tide-oak-nine) joined 'home'. [Remove]" and every member shows the same notice | | none |

The code is at least 40 bits: four words from the 2048-word list of the recovery phrase (44 bits), where today's is six digits
(`join_code.rs:124`). A pool allows at most 2 windows an hour. A pool whose counting keys are all `computer` (2.6) caps the delegate at 3
admits a day and 5 outstanding (admitted since the delegate was last renewed; the sixth is a card with a prompt); the phone-pool caps (3 an hour,
10 a day) stand otherwise.

Counted: 1 tap, 1 typed code, 0 prompts while the delegate lives. With no delegate (never granted, or the 30 days ran out) the admit is a card:
tap and prompt, 2 more. Renewal is its own approval, nudged a week ahead, never inside another tap (`docs/pools-and-serving.md` 1.3).

**Approve what an agent asks** (journey 3)

| Request | Steps | Counted |
|---|---|---|
| Routine (`sync_now`, `link_remove`, `note_verify`) | tap **Approve**; prompt | 2 |
| Sensitive or irreversible (`add_device` with no live delegate, `shard_consent`, `member_remove`, `file_delete`) | open the card; **Approve**; prompt; pick the agent's number out of three | 4 |
| Governance (`admin_add`, `admin_remove`, `set_floors`, `recover`, `delegate`) | the same four steps, then the hold of 2.6 | 4 |

Opening a join window while a delegate lives is the tap of journey 2, not this table: the agent's word opens nothing, and the admit itself needs no
approval (D3), so the tap and the typed code are its two human steps.

An irreversible data act that only `computer` keys approved (`file_delete`, `shard_consent`) is then held with a cancel window (2.6): the card
says "Waiting: this deletes 'notes' tomorrow at 10:03 unless you cancel", the agent is told "waiting", and every member device gets a notification.

The card is the same text as the phone's, plus one new line, `LEVEL:` (2.7). The agent shows its number in its own chat, as for a phone.
Nothing here asks twice. Opening the app to look asks nothing; a locked screen means the card waits.

**Remove a member** is a sensitive act (2.6, table): "Remove 'laptop-2' (tide-oak-nine) from 'home'? It stops working with every device in the pool at once."

**Recover when the only computer is lost** (journey 4)

- *Once, at founding or later, optional:* the app offers "Write down your recovery words so you can take this pool back if this computer is lost". One tap,
  one prompt, then **24 generated words shown once** to write down. No file is made. The words are the recovery secret, and the node never keeps them. If the person
  skips it, a weekly one-line nudge asks again (`docs/pools-and-serving.md` 1.8). The recovery key joins the pool by an `admin_add` of role
  `recovery`, with its own acceptance. Because a pool with no recovery key lets whoever holds an admin key list one that nobody else can remove
  (`gov/chain.rs:199` voids only a second one), an `admin_add` of a recovery key when none is listed is a governance act held **7 days**, not 24
  hours; the app says "ready in 7 days" and every member is notified. Until it has run the pool header reads "no recovery key yet".
- *The words and their strength.* The 24 words come from the **BIP-39 English list** (2048 words): 256 bits of entropy from the computer's
  random source plus an 8-bit checksum held in the last word, so a mistyped word is caught before anything is tried. At that entropy no offline guess is practical
  whatever the KDF. The key is derived with scrypt (N = 2^17, r = 8, p = 1, salt `poolhouse/recovery/v1` and the pool id), about 128 MiB and
  a fraction of a second on a laptop (not measured), so a phrase for another pool derives another key. `SCRYPT_N` in `signing.py:41` is 2^15 and belongs to the passphrase file,
  which this does not use. The app offers no phrase a person chose: only generated ones.
- *Replacing or retiring the recovery key.* `recovery_replace` is an **admin act**, signed like any governance act and held **7 days**, and the
  current recovery key can cancel it. A thief of the computer's key then cannot swap in a recovery key of its own unseen; the owner who lost the
  words replaces them the same way, with the admins' signature. `gov/chain.rs:199` and `:228` (replaced or removed only by its own signature) are
  rewritten to that.
- *Recovering:* on any surviving member's Poolhouse, "Take back this pool": type the 24 words, answer that computer's prompt.
  The app makes a new pool key on that computer and signs `recover {new admin set}` with the recovery key outside the node, which takes the
  signed act back through `pool_submit`. It takes effect after
  `recover_delay_days` (7 by default, 1 to 30) counted on each member's awake clock from the first moment it saw the act. Every member shows
  "Pool 'home' will get new admins in 6 days unless a current admin cancels it", in the app **and by an operating-system notification on every
  member device each day of the 7**. A key at the pool's current level cancels it, and the recovery key cancels its own. A wrong word opens nothing; a mistyped one is caught.
- *A contest.* Admins can cancel a `recover` and the recovery key can cancel admin acts, so the owner and a thief look alike to a member: an
  attacker with the phrase, or malware with the admin key. Two rules keep either from looping:
  - A cancel of a `recover` **retires that recovery key**; it can propose nothing more. The pool header reads "Recovery words were used and
    cancelled. Write down new ones", and a new recovery key is an `admin_add` held 7 days like the first.
  - A cancel of an admin act by the recovery key puts the pool **in dispute**: every governance act is refused (data acts, sync, serving, joins
    by a live delegate and removals of a device by its owner go on) and the header reads "In dispute. Make a new pool." The pool is never
    blocked on a phone; it is frozen only after two parties with keys disagree, and the way out is a new pool, as for total loss
    (`docs/pools-and-serving.md` 1.8).
- *No surviving member:* nothing holds the pool, so there is nothing to recover; the person makes a new pool. A pool of one device says so at
  founding in the same sentence as the nudge.

### 2.5 What the weaker level means, operation by operation

"Weaker" always means one thing: **a program running as you, outside the agent sandbox, could produce the signature without the prompt.** What
differs is what that program could do with it and what is left to undo it.

| Operation | What a program with the key could do | What stands in the way | What undoes it |
|---|---|---|---|
| Found | Nothing before a pool exists: a pool it founds is its own | The card names the computer; the charter is bound to this device's certificate (`ops.rs:178-180`) and dated (`ops.rs:439-441`) | Nothing needed |
| Admit a device | Add a device it controls, as a member who reads boards and takes work. A thief of the delegate device key alone can admit 10 a day, about 300 in the 30 days, in a pool whose caps are the phone pool's | The join window is a tap in the app (an agent cannot open it) and its code is at least 40 bits; at most 2 windows an hour; in a `computer`-only pool the delegate admits 3 a day and 5 until renewed; a notice with **Remove** on every member's screen, and a notification | Remove the device (sticky; its own admits go with it, `docs/node.md` "Who governs a pool") |
| Approve a grant | Grant a session of its own an action, as today's typed yes already allows | The fatigue ledger; the number to match; per-device scope; sensitive grants take four steps | Revoke the grant |
| Remove a member | Remove devices you own, and in a mixed pool the devices of a person whose key is stronger | A sensitive act (2.6): the floor is the highest level present, so a `computer` key does not count in a pool with a phone key. The card, the prompt, the pick; every member shows who was removed | Re-admit with a new certificate |
| Change who governs (admins, thresholds, holds, floors, delegate) | Add its own key and drop yours | Governance (2.6): held 7 days when only `computer` keys sign, with a banner and a notification on every other member device. In a pool of one device the only screen is the thief's own, so the hold protects only through the recovery key's cancel and a person who comes back within 7 days | A key at the floor, or the recovery key's cancel. With one device and no recovery key yet, nothing undoes it: the worst case, which the nudge, the "no recovery key yet" header and the level line exist for |
| Add the first recovery key | List a recovery key of its own that the owner cannot remove, one that survives a rotation of the admin keys (`gov/chain.rs:199` voids only a second one) | Governance, held 7 days (2.4); the header says "no recovery key yet" until one is listed | A key at the floor cancels it within the 7 days |
| Replace the recovery key | The same, once one is listed | `recovery_replace` is an admin act held 7 days that the current recovery key can cancel (2.4) | The current recovery key's cancel |
| Recover | Cannot: the recovery key is a separate secret. A thief of the phrase proposes it | 7 days, a notification a day on every member device, cancel by any key at the floor, and the cancel retires that recovery key (2.4) | The current keys cancel it; a thief of both is the case in 4 |

At `native` the same table holds for programs running as you and fails only for root. At `phone` it holds for both, and fails for a stolen
unlocked phone with its owner's finger.

### 2.6 What a pool may do at each level (the rules)

**The classes.** Every act belongs to one of three (`class_of`, `gov/ops.rs:54-60`, gains `Governance`; `remove` moves from routine to sensitive and
`delegate` from sensitive to governance):

| Class | Acts | Hold when a key above `computer` signs | Hold when every counting key is `computer` |
|---|---|---|---|
| routine | `sync_now`, `link_remove`, `note_verify`, `cancel`, `delegate_end` | none | none |
| sensitive | `add_device` with no live delegate, `member_remove`, `shard_consent`, `file_delete`, a grant of a sensitive action | none | the **irreversible** ones (`file_delete`, `shard_consent`) wait a cancel window of 24 hours; the rest none |
| governance | `admin_add`, `admin_remove`, `set_threshold`, `set_holds`, `set_mode`, `set_floors`, `delegate`, `recover`, `recover_cancel`, `recovery_replace` | today's holds (24 hours for admin changes and weakenings, none otherwise) | **7 days** |

Two governance acts are held 7 days whoever signs them: an `admin_add` of a recovery key when none is listed, and `recovery_replace` (2.4). The
current recovery key can cancel `recovery_replace`.

A hold is never a refusal. During it the card says "Waiting" in plain words, the agent is told "waiting", every member device shows a banner and
gets an operating-system notification, and any key at the floor cancels. Nothing here asks for a phone: a pool of computers waits, and a pool with
a phone does not.

R1. A key counts for a class of act only if its level is at or above the charter's **floor** for that class (three floors: routine, sensitive and
governance, the last defaulting to the sensitive floor). Default floors: the lowest level among the founding admin keys for routine, the highest for
sensitive when more than one level is present. A pool founded by a phone keeps its floor at `phone`, which is today's behaviour exactly. Because
`member_remove` is sensitive, a `computer` key in a mixed pool cannot remove a phone owner's devices (`docs/pools-and-serving.md:114` lists
remove-a-member as routine and changes).

R2. `weak` is below every floor, and **a floor is never `weak`**: the charter is refused if it names it. `weak` counts toward nothing but
ending a delegate (`gov/pool.rs:361`).

R3. Adding a key below the floor lists it and counts it for nothing.

R4. **Raising** a floor is a governance act with the holds of the table (none when a key above `computer` signs, 7 days when only `computer` keys
sign) and is **refused when no listed key holds the new level**, as `apply_set` already refuses thresholds that need more people than the strong keys
listed (`gov/chain.rs:172-188`, `gov/charter.rs:250`): without that a thief with a `computer` key raises a floor to `phone` with no hold and no phone
listed, and nothing counts again. **Lowering** one is a weakening: held 24 hours (`gov/chain.rs:153-159`), 7 days when only `computer` keys sign,
signed by keys at the *current* floor, cancelled by any of them. A thief with a `computer` key cannot lower a `phone` floor.

R5. When a key above the floor is added (a phone to a computer pool), the same approval asks one plain question: "Should big changes need your
phone from now on?" The answer yes raises the sensitive floor. The default is yes, as for a second person today (`docs/pools-and-serving.md` 1.3).

R6. A `recover` act replaces the admin set and its levels. Its card says so ("After this, this pool is approved from a computer instead of a
phone"), and the current floor's keys can cancel it.

### 2.7 No silent downgrade: what the person sees

- Every card carries a `LEVEL:` line the node writes from the charter and the signing key, signed with the text. `describe` cuts its sentence at
  300 characters (`gov/describe.rs:75-77`), so the line is appended after that cut and is never part of the text that can be cut off. Example:
  `LEVEL: This change is approved from this computer. A program running as you could approve it without asking; a phone would stop that.`
- The pool header in the app reads "Approved from: this computer" with **Add a phone** beside it, never a blocker; it also reads "no recovery key yet"
  until one is listed, and "In dispute. Make a new pool." in a contest (2.4). `pool_status.governance`
  carries `levels` and `floors` in place of `weak_keys_only`.
- A level or a floor change is a card of its own, a banner and an operating-system notification on every member device for the whole hold, and an
  audit entry.
- The card sentence for a weak key ("weak, which ends a delegate and nothing else", `gov/describe.rs:37`) and the founding line
  (`gov/describe.rs:69-72`) gain the level.

### 2.8 Security without friction

| Journey | Phone (`contracts/phone-friction.json`, `docs/pools-and-serving.md` 3.1) | Computer | Why any step stays |
|---|---|---|---|
| First pool | 10 steps: install 2, the system's one-time prompts 3, link-phone 2, say 2, tap 1 | 5 steps: install 1, the system's one-time prompt 1, say 1, tap 1, prompt 1 (6 until the live check shows the keychain adds no dialog; the `pool_key` enrol prompt and the ticket prompt are one ask, 2.3) | The prompt is the one step an agent cannot do. It replaces today's terminal `yes` |
| Routine approval | tap, fingerprint (3 locked) | tap, prompt (waits while locked) | Same |
| Sensitive approval | 4 (5 locked) | 4 | The pick ties it to the agent's own screen |
| Add a device | 0 with a delegate | 1 tap (open the window) and a typed code with a delegate, 2 more without | The tap keeps an agent from opening windows |
| Prompts per day | not measured | at most 20; at most 3 in 10 minutes; one prompt for up to 10 routine cards opened together | A password prompt every few minutes teaches the person to type the password into anything |
| Terminal steps | 0 | 0 | The typed-yes path stays for developers only |

The added visible items are the `LEVEL:` line, the level in the pool header and the recovery nudge. A new `contracts/computer-friction.json` and
`tests/test_computer_friction.py` mirror the phone's ceilings (first run 2, routine 2, sensitive 4) and add prompts per day (20) and per 10 minutes (3).

## 3. Build plan

Each slice lands alone, with tests seen to fail first. Any test of 1.2 that is changed carries an `Owner-order:` line in its commit message
(`scripts/hooks/weakened-assertions`). Sizes: S under a day, M one to three days, L a week.

| # | Slice | Exists | New | Tests that fail first | Size |
|---|---|---|---|---|---|
| 1 | **Levels and floors in the charter** | `Strength`, `strong()`, `weak_only`, `signers_fit`, `keys_for` | Charter version 4: `assurance` for each key, `floors` for three classes, `counting(class)`; `Class::Governance`; `remove` sensitive and `delegate` governance in `class_of`; holds of 7 days when only `computer` keys count and the 24-hour cancel window of irreversible data acts; `recovery_replace` and the first recovery key as 7-day acts (`chain.rs:199`, `:228` rewritten); act `set_floors`; `LEVEL:` appended after `describe`; glossary and `docs/node.md` edits | `a_pool_of_one_computer_key_founds_admits_and_removes`; `a_floor_of_phone_makes_a_computer_key_count_for_nothing`; `lowering_a_floor_is_held_and_needs_keys_at_the_old_floor`; `raising_a_floor_takes_effect_at_once`; `a_charter_with_other_floors_is_another_pool`; `a_key_added_below_the_floor_counts_nothing`; `a_floor_is_never_weak`; `raising_a_floor_to_a_level_nobody_holds_is_refused`; `computer_only_governance_is_held_seven_days`; `computer_only_file_delete_waits_a_cancel_window`; `member_remove_needs_the_sensitive_floor`; `the_first_recovery_key_is_held_seven_days`; `recovery_replace_is_held_and_the_recovery_key_cancels_it`; `the_level_line_survives_the_300_character_cut`; `app/poolhouse-node/tests/gov_strength.rs:44` inverted. Pool check: `plant_phone_key` becomes `plant_key(level)`, the doc and docstring of 1.3 are corrected, steps `found-computer-only`, `remove-computer-only`, `floor-held`; the report line says "planted, not custody" | M |
| 2 | **A key's level is derived** | `presence/`, `Persons`, the device key | Prompt purpose `pool_key`; `Firsts::take` returns the presence kind; rows carry `assurance` and the node's signature; a terminal or non-real directory derives `weak` | `a_pool_key_enrolled_through_the_os_prompt_is_computer_level` (stand-in `Presence`, as the crate's tests do); `a_key_a_terminal_enrolled_is_weak`; `a_persons_file_a_program_wrote_is_not_believed`; `a_copied_state_directory_derives_nothing` | M |
| 3 | **The computer signer** | `grants.Signs`, `person_key`, `founding.found` | `ComputerKey` with tickets; a Python rebuild of an act's signing bytes (`poolhouse/pool-act/v2`) to compare with the node's; `presence` raises the prompt per card, only for a card the person opened, with a sentence the node writes from the act and never cuts (refused over 160 characters); the `pool_key` enrol prompt is the founding ticket; bundled tickets; 3 prompts per 10 minutes and 20 a day; a prompt a standard macOS account can answer (moved from slice 7); `founding.default_admin` returns it; refusals rewritten | `signing_without_a_ticket_raises`; `a_ticket_for_other_bytes_is_refused`; `the_signer_refuses_bytes_that_do_not_match_the_act`; `a_ticket_is_used_once_and_ends_in_two_minutes`; `an_agent_session_cannot_get_a_prompt_raised`; `a_refused_prompt_founds_nothing_and_is_quiet_for_ten_minutes`; `a_prompt_sentence_over_160_characters_is_refused_not_cut`; `the_agents_words_never_enter_the_prompt`; `the_fourth_prompt_in_ten_minutes_waits`; `ten_routine_cards_cost_one_prompt`; `founding_is_one_prompt`; `test_pool_found.py:116` rewritten | M |
| 4 | **The card on the computer** | `approvals/` text, fatigue, number to match; `app/src-tauri`; the UI daemon | An approval list and card view, notification, **Approve** then prompt, the pick; `approval_decide` from the app; `contracts/computer-friction.json` | Playwright on its own Chromium (never the owner's browser): the card text equals the node's; no terminal step in the journey (`POOLHOUSE_STEP_LEDGER`); `tests/test_computer_friction.py`; a held requester is shown nothing | L |
| 5 | **Join and remove without a phone** | SPAKE2 join, delegate rules, `gov_remove` | The window opens on a tap in the app; the code (at least 40 bits, 2 windows an hour) is shown on the first computer and absent from the agent's reply; caps for `computer`-only pools (3 a day, 5 until renewed); `key_binding` follows custody (two lines at `node.rs:164`); the `delegate` act in the founding bundle; notices with **Remove**; a join screen for the code | `the_agent_is_not_told_the_code`; `an_agent_cannot_open_a_join_window`; `a_code_has_at_least_40_bits`; `the_third_window_in_an_hour_is_refused`; `a_computer_only_delegate_admits_three_a_day_and_five_in_all`; `a_restored_copy_is_seen_as_a_second_device_with_one_certificate`; `a_keystore_device_signs_a_delegate_admit_and_a_copied_directory_does_not`; `a_join_with_a_live_delegate_asks_nobody`; `an_expired_delegate_asks_a_card`; pool-check `join-computer-only`. **Live check on the owner's Mac (2.9) closes the slice** | M |
| 6 | **Recovery** | `Role::Recovery`, `recover_delay_days`, the awake clock | Acts `recover`, `recover_cancel`, `recovery_replace`; the 24-word recovery phrase (BIP-39 English list, scrypt N = 2^17 from the pool id) and its entry on a surviving member; a daily notification on every member device for the 7 days; a cancel retires the recovery key; the dispute freeze and "In dispute. Make a new pool."; banners | `a_recover_takes_effect_only_after_seven_awake_days_on_each_device`; `a_current_key_cancels_it`; `a_wrong_word_opens_nothing`; `a_phrase_for_another_pool_is_refused`; `a_cancelled_recover_retires_the_recovery_key`; `a_recovery_key_cancel_of_an_admin_act_opens_a_dispute`; `a_dispute_refuses_governance_and_keeps_data_acts`; `a_mistyped_word_is_caught_by_the_checksum`; pool-check `gov-recovery-timelock` | L |
| 7 | **Prompts on Windows and Linux without `pkexec`** | `presence/` refuses with a plain sentence (`presence/mod.rs:224`) | Windows credential prompt; a desktop polkit path | Per platform, on the owner's WSL and Windows devices; until then those computers join pools and found none | M each |
| 8 | **`native`** | `app/poolhouse-authority`, `app/native-approval` | Installer (one administrator approval, offered later in Settings, never at first run), IPC, key registry, issuer policy, level `native`, the question of 2.6 R5 | The ten attacks of `docs/notes/native-finalizer-design.md`; blocked for people other than the owner until a paid Apple account (`docs/pool-encryption.md` 8.1) | L |

Order: 1, 2, 3, 4, 5, 6; 7 beside them; 8 after. A computer-only pool exists end to end after slice 4; adding a device with one tap and a code after slice 5.

**Before building:** the findings H1 (the first recovery key and `recovery_replace`) and H2 (the classes, the 7-day holds, the floors) are settled in
this document and must be built into slice 1, not added after it; H3 (the contest rules, the phrase and its notifications) must be settled before slice 6
and is in 2.4. The slice 1 text carries H1 and H2; slice 6 carries H3.

### 2.9 The live check on the owner's Mac (end of slice 5)

No automated check raises the operating system's prompt (it is refused off the real home, `presence/mod.rs:221-223`). The owner's Mac does, in this
order, and the slice is not done until it passes: (1) a fresh account with Poolhouse installed and no phone, and a second fresh **standard**
(non-administrator) account that goes through (2) as well (the family-Mac case, slice 3); (2) "Make a pool called home": one tap, one
prompt, the pool exists, `pool_status` says `computer`; the run records how many dialogs the system showed in all (the keychain may add one) and
whether the prompt offers **Touch ID** or only a password: **unverified**, the text of 2.1 and the journeys says "password" until it is recorded;
(3) tap **Open window** on the first computer, type the code shown on its screen into a second device, and see the join with no further tap and the notice on
the first screen; (4) an agent asks for `add_device`: the card, the tap, the prompt, the pick; (5) cancel the prompt: nothing
is signed and the next prompt waits ten minutes; (6) remove the second device and see it refused at its next request; (7) write down the recovery words;
(8) delete the first account's state directory, take the pool back from the second device, and see the seven-day banner on both.

## 4. Attack first

Written by the author, then broken by a non-author (section 7: accept with edits). A finding is fixed here or named as accepted with the owner.
Every row below holds under the owner's constraint that a pool is never blocked on a phone: the answer to a weak key is a hold, a notification and a
cancel window, never a phone-required refusal.

| Case | What stops it on the computer path | What remains, named |
|---|---|---|
| **Stolen unlocked laptop** | A signature needs the prompt; a thief without the password or the owner's finger cannot give it. Admin changes are held 24 hours with a banner on every other member | A thief who knows the password has everything the key has until recovery completes. Out of scope as for every keystore secret (`docs/pool-encryption.md` section 3) |
| **Malware as the same user, using the prompt** | It must raise a prompt: the node raises one only for a card the person opened, and the node writes the prompt's sentence from the same bytes the ticket names, so a prompt it raised describes what the honest signer will sign. Fatigue limits and the ten-minute quiet apply, and at most 3 prompts in 10 minutes and 20 a day. Governance and irreversible data acts it signs are held (2.6) with a notification on every other member device | Unsandboxed malware can skip the node, read the sealed key and sign. **This is the stated weakness of `computer`.** It can also raise the same `osascript` administrator dialog itself with any text (2.3), asking for an administrator's password; a prompt is not proof that the node raised it. It can race a true prompt into a moment the person expects another. In a pool of one device the hold is shown only on the computer the malware runs on. `native` closes the first two, a protected window closes the third |
| **A prompt-injected agent triggers the prompt** | The agent can only ask. It cannot tap **Approve** or **Open window** (the taps are the person's, in the app), cannot answer the prompt, cannot read the key (sandbox `denyRead`, `docs/agent-sandbox.md:38`, `:89`) and cannot read the number to match of another session. Its words (`WHY: The agent says: ...`) never enter the prompt string. A request that looks wrong carries `FLAG:`, `SAID:`, `VERDICT:`, `TAINT:` lines | An agent with Accessibility permission can tap **Approve**; the prompt still stops it. `SAID`, `VERDICT` and `TAINT` still read "not tracked" until a harness attests instructions (`docs/phone-approval.md`) |
| **Approval fatigue** | One item per request, backoff, holds, caps, quiet notice, deny and block (`docs/phone-approval.md`); at most twelve asks in ten minutes and ten minutes of quiet after a no (`ops.rs:28-31`), which would allow about 72 password prompts an hour, so prompts have their own limit: 3 in 10 minutes and 20 a day, with up to 10 routine cards on one prompt; one prompt at a time, two minutes to answer | A person who accepts every card is protected by nothing here; the pick and the `RISK:` line are the help |
| **Cloned state directory** | The pool key and the device key are sealed under a subkey of one master per OS user (`src/poolhouse/keystore.py:3-5`), named by a hash of the directory's path (`signing.py:109-110`). Nothing binds either to a machine. A directory copied to another account or computer without that account's login keychain opens nothing. `founding_of` refuses a charter that names another computer first (`ops.rs:178-180`). A device whose own key is a file signs no delegate admit (`ops.rs:260`, `:313`) | **A copy does not always open nothing.** A Time Machine or Migration Assistant restore of the state directory and the login keychain onto another Mac opens the pool key and the delegate device key there. That restored copy is a **second device with the same certificate**, with the delegate power in a pool that has one: `docs/pools-and-serving.md:131` (a copy carries no delegate power) holds only where the device key is a file. Remove the device by its certificate removes both. Slice 5 adds a test that a member seeing one certificate answer from two places says so. A device with no keystore holds nothing that counts. A cloned *pool* with other admins is another pool (the pool id hashes the charter, floors included) |
| **Replay** | A card is single use under the node's lock; a ticket names one card and its payload hashes and lasts two minutes; an act carries pool, epoch, `prev`, nonce and `not_after`; a prompt result is a ticket, never a signature, so it cannot be moved | A signed act replayed inside its hour is the same act (ids are content hashes) and changes nothing |
| **Forged or below-threshold signature** | Every member checks every act against the keys and levels the charter lists; a key below the floor counts for nothing; strict DER and low `s` for P-256 stay | None new |
| **Contest over the recovery key** (an attacker with the phrase, or malware with the admin key) | A cancel of a `recover` retires that recovery key, so it cannot propose again; a cancel of an admin act by the recovery key puts the pool in dispute and freezes governance; a `recover` is notified on every member device each day of its 7 (2.4). The phrase is 256 bits from a named list (BIP-39 English), scrypt from the pool id | Malware holding the admin key can cancel the owner's `recover` and retire the owner's words; every member is told at once, and the owner makes a new pool. An attacker with the phrase can freeze governance by one cancel of an admin act; data acts go on. A thief holding admin key and phrase wins: out of scope as for every keystore secret |
| **A recovery key slipped in** (no recovery key listed yet) | The first recovery key and `recovery_replace` are 7-day governance acts the current keys cancel; the header says "no recovery key yet" | A pool of one device with a thief on it for 7 days and no second screen: named in 2.5 |
| **One pool key in every pool** | It is one key for the computer | A leak reaches every pool, and members who sit in two pools can link them by the public key (2.3). Named, not fixed |
| **Downgrade, phone-required to computer-allowed** | The floor is in the signed charter. Listing a `computer` key under a `phone` floor counts nothing. Lowering needs keys at the `phone` floor, is held 24 hours (7 days when only `computer` keys sign) and shows a banner and a notification. `recover` names the new levels on its card and the `phone` keys can cancel it | If the only phone key is lost the floor cannot be lowered except by `recover` after seven days: that is the intended path, and its card says so |
| **Downgrade, secure to open** | Unchanged: the policy is in the charter and never changes in place | The unauthenticated `hello` still returns the policy (`docs/release-readiness.md:378`) |
| **Compromised or malicious member** | A member holds no person key (`gov/signer.rs:3-5`). It cannot change floors, admins or members; its relayed acts carry no authority; a delegate is capped | A founder can overstate its own level to a joiner: the joiner's screen says "this pool says". Withholding relayed acts delays a removal on the devices a member feeds, as in any pool |
| **A malicious agent minting its own key** | `pool_draft_found` refuses a named level; a `computer` row exists only after the prompt for that key; the node signs rows with its device key | A program that can write the state directory and holds the device key can write a row. The sandbox closes it, as for `phone: true` today |
| **Takeover by whoever is merely present** | Presence gives no key. A device joining holds no admin power. The prompt asks for the account's secret, not for presence | Someone at an unlocked, unattended computer who knows its password: see the stolen unlocked laptop |

## 5. Decisions made

The owner made these three decisions. The rest of this document follows them.

**D1. A pool with only a computer's key is never blocked.** The owner's order: a pool is not blocked on a phone, because some people will not connect one.
Founding a pool, adding devices, approving and removing members work with a computer key and no phone. The option of requiring a stronger key for new
pools is dropped. Computer-only pools are allowed, and they are reissued at the `native` level once that level exists. The level is shown to the person in
plain words, as the "Shown to the person as" column of 2.1 gives it. The cost, accepted with the order: a program running as the founder can take such a
pool, and a person who joins it trusts that. The joiner's screen and the pool header say so.

**D2. Recovery is 24 words, with no file.** The person writes down 24 generated words, shown once; the node never keeps them. A recovery takes effect
only after a 7-day lock. The file sealed under six words is dropped.

**D3. Founding gives the founding computer the 30-day delegate.** Adding your own devices then costs no tap, the same trade as phone pools
(`docs/pools-and-serving.md` 3.1). A program running as you can use that device key as well as the pool key; the admit rate (3 an hour, 10 a day) and
removal bound it. For a pool whose counting keys are all `computer` the review tightened the bound to 3 admits a day and 5 until the delegate is
renewed, and a window to open only on the person's tap (2.4); the card says "this computer may admit devices without asking".

## 6. Documents that change

`docs/glossary.md` (strong and weak key become assurance levels and floors; add **assurance level**, **floor**, **recovery phrase**, **governance act**);
`docs/node.md` "Who governs a pool" and "Standing grants"; `docs/pools-and-serving.md` 1.3 ("What a one-person pool does" says the laptop's key is
weak and signs nothing that matters; line 114 lists remove-a-member as routine and line 131 says a copy carries no delegate power, both change), 1.5
(the hold table), 1.8 (the first recovery key, `recovery_replace`, the retire and dispute rules), 1.12, 3.1; `src/poolhouse/board/founding.py`;
`app/poolhouse-node/src/join_code.rs` (six digits become 40 bits); `docs/phone-approval.md` (the `LEVEL:` line, the computer decider); `docs/pool-check.md:97`;
`docs/release-readiness.md` POOL-2, GOV-2 to GOV-5, GOV-7; `HANDOFF.md:46-49` and "Phone optional" as each slice lands.

## 7. Review log

A non-author review of the first version, verdict **accept with edits**. Each finding was checked against the code before it was applied. The
owner's constraint that a pool is never blocked on a phone overrides the review where they differ: no irreversible data act sits at a phone or `native`
floor and no phone-required refusal was added; holds, notifications and cancel windows stand in.

| Finding | Checked against | Change |
|---|---|---|
| H1 the first recovery key is a backdoor | `gov/chain.rs:199` voids only a second recovery key; `:228` removes one only by its own signature; with none listed, an admin signature lists one, held 24 hours (`hold_ms`) | `recovery_replace` is an admin act held 7 days, cancellable by the current recovery key; an `admin_add` of a first recovery key is held 7 days; the header says "no recovery key yet" (2.4, 2.5, 2.6) |
| H2 `computer` is the issuing node's word and decides everything | `ops.rs:157-159` and 2.2: a later key is vouched by a card, no member verifies it; `class_of` has two classes (`ops.rs:54-60`); R4 raised a floor with no hold | Act-to-class table with a third class, governance; 7-day holds when only `computer` keys count; irreversible data acts wait a 24-hour cancel window with a notification; a floor is never `weak`; raising a floor to a level no listed key holds is refused; one-device pools: named residual (2.5, 2.6) |
| H3 a cancel is symmetric; the attacker re-proposes | `docs/pools-and-serving.md` 1.8 lets admins cancel a `recover` and the recovery key cancel; only an in-app banner | A cancel retires the recovery key; a recovery-key cancel of an admin act opens a dispute that freezes governance ("In dispute. Make a new pool."); BIP-39 English list named; 256 bits of entropy, scrypt N = 2^17 stated (`signing.py:41` is 2^15 and belongs to the passphrase file); a notification a day on every member device for the 7 days (2.4, 4) |
| M1 the prompt | `presence/mod.rs:133-141`: `do shell script ... with administrator privileges` through `osascript`, 160 characters of `[A-Za-z0-9 .,-/()]` taken with `take(160)` | Never cut: the prompt is refused over 160 characters; standard-account prompt moved from slice 7 to slice 3; any same-user program can raise the same dialog (named); Touch ID recorded as unverified in 2.9 |
| M2 founding is two prompts | 2.2 `pool_key` enrol prompt and the 2.3 ticket prompt are separate | One ask: the enrol prompt is the ticket; the friction contract counts 2 until the live check shows no keychain dialog (2.3, 2.8) |
| M3 prompts per day | `ops.rs:28-31`: 12 asks per 10 minutes, about 72 prompts an hour | 3 prompts per 10 minutes and 20 a day; one prompt for up to 10 routine cards; prompts per day in the friction contract (2.3, 2.8) |
| M4 join windows | Journey 2 opened a window on the agent's word; `join_code.rs:124` is six digits, 3 tries; no window limit; the delegate admits 10 a day, about 300 in 30 days | Opening a window is the person's tap; the code is at least 40 bits; at most 2 windows an hour; `computer`-only pools admit 3 a day and 5 until renewed; the card says "this computer may admit devices without asking" (2.4, D3) |
| M5 cloned state directory | `keystore.py:3-5` one master per OS user; `signing.py:109-110` hash of the directory path; no machine binding | The row of 4 no longer says a copy opens nothing: a restore with the login keychain opens the keys and is a second device with one certificate; `pools-and-serving.md:131` holds only for File custody |
| M6 remove-member | `class_of` makes `remove` routine and `pools-and-serving.md:114` agrees, while the card journey called it sensitive | `member_remove` is sensitive in the table (2.6); `docs/pools-and-serving.md` 114 changes |
| L1 | `describe` takes 300 characters (`gov/describe.rs:75-77`) | `LEVEL:` is appended after the cut (2.7) |
| L2 | One pool key per computer, 2.3 | Named in 2.3 and 4; not fixed |
| Prompt text | `approvals/content.rs:228` puts the agent's words on `WHY:` | They never enter the prompt string (2.3) |
| Slips | `ops.rs:439-441` is the date check; `OTHER_FIRST` is `ops.rs:178-180`; the planted key is `wire.py:129-135`, called at `:141-142` | Corrected in 1.3 and 2.5 |

Not changed: recovery as 24 words with no file and a 7-day lock; the 30-day delegate at founding; computer-only pools now and reissued at `native` later.
Findings are design changes only; nothing was built or run, so none of them is shown to hold by a test yet. The tests are named in section 3.
