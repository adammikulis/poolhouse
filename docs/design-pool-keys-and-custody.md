# One key per pool, and verifiable custody

Design. Nothing here is built. Read at `94b34136a` (`0.3dev`): section 1 is code and document reading; no test was run and no screen was driven.

Two linked changes the owner ordered: (A) a distinct key for each pool, so a leak is one pool's and two pools cannot be linked by key (L2 of `docs/design-phone-free-pools.md`);
(B) a signed, checkable account of how a key is held, so a key's level is no longer only the issuing device's word (its H2). It is the "keys document" that
`docs/design-multi-pool-devices.md` (landed) assumes in its section 2.3; the interface that document lists is adopted in 2.1 to 2.6, and that document says it follows
this one where they differ. Part A changes no charter. Part B changes the charter and adds three acts.

## 0. In short

- **Two independent keys for each (computer, pool).** The **pool key** is the device's key in that pool: an Ed25519 key and a certificate, held by the node, signing the pool's
  handshake, board heads, beacons, heartbeat, the device's own `leave` and a delegate's `admit`. The **admin key** is the person's key in that pool, held by the person-facing signer and
  never by the node: it accepts the charter, signs the pool's acts and decides approvals for the pool's boards. (Phone-free 2.3 calls this one "the pool key"; here it is the admin key
  and "pool key" is the multi-pool design's word.) Neither is derived from anything: each is a random seed, sealed, made for one membership and destroyed when it ends.
- **Why not derive.** The pool id does not exist when either key is made (it hashes the charter that names them, and a joiner learns it inside the join), and a rejoin must give a new
  key (multi-pool D7), so any derivation needs a stored random value anyway; storing the seed costs the same and leaves no root whose leak is every pool. HKDF has one use: cutting
  each key's wrapping subkey from the secret that seals it. 2.2.
- **The device-wide secret signs nothing.** What the device key is today becomes a **custody secret**: 32 random bytes in the keystore (or under a passphrase, or in a file) that wraps
  the pool keys. `Node.key_binding` is deleted and the delegate rule reads the secret's custody. 2.5.
- **B. A custody statement.** A member device signs with its pool key a statement of how a key is held (a claim: method, system, prompt), bound to the key, the pool and a time, with
  evidence when evidence exists. Every member recomputes what it proves; no field says a level. 3.1 to 3.3.
- **The level a key counts at is computed**: the lower of the level its claim names and the cap of its basis. `attested` (a chain every member checks from bytes) caps at what the chain
  proves; `vouched` (one device's word) caps at `computer`; no basis caps at `weak`. A `phone` or `native` claim nobody can check counts as `computer`, with the holds that carries. 3.4.
- **What can be proven.** Only an Android Keystore key has evidence a member can check, and only once the owner sets the app publisher policy that is `None` today. macOS, Windows and
  Linux can prove nothing about a key to another member; they reach `vouched`. 3.5.
- **A lie.** Evidence false by its own bytes sets the voucher aside on every member, mechanically. A lie nobody can prove is made harmless by the cap and can be answered by an admin's
  `distrust`. Removal stays the admins' act. 3.6.
- **Words.** The person sees level and basis in one sentence on every card and in the pool header, computed when read; every fall of a level is a banner and a notification. 3.7.
- **Versions.** Charter 3 to 5 (4 is phone-free slice 1), acceptance `v3`, kinds `vouch`, `distrust`, `trust`, envelope unchanged. No migration. Eight slices (section 5). One open question (section 7).

## 1. What the code does today

Paths are under `app/poolhouse-node/src/` unless they start with `app/`, `src/`, `tests/` or `docs/`. The brief named `src/poolhouse/pool/*`; those files do not exist. The Python side
is `src/poolhouse/board/founding.py`, `board/person_key.py`, `fleet/onboard/signing.py`, `keystore.py` and `person.py`.

| Topic | What is there | Where |
|---|---|---|
| One admin key for every pool | `founding.Admin` takes one `grants.Signs`; the person key is one `SigningKeys` in `<state>/person` (`signing.json`, `signing.key.wrapped`) | `src/poolhouse/board/founding.py:41-48`, `board/person_key.py:29,52`; phone-free 2.3 |
| How a Python key is sealed | `ks.wrap("fleet-signing", account, raw)`; `account` is `onboard-signing-` and a hash of the key directory's path. `Keystore.wrap` takes an HKDF subkey of the one master per OS user with purpose and owner in `info`: another owner is another subkey, and no keystore item is added. Reads 600 an hour, makes and removals 5 | `signing.py:38,109-110,138-143`; `keystore.py:57-58,462-468,482-486` |
| What the node knows of a person key | `persons.json` rows `{public, label, at_ms, alg, phone}`, at most 8; the only level is `phone` (strong) or not (weak) | `grants/person.rs:18,34-46,197`; `gov/ops.rs:157-159` |
| The device key | One Ed25519 key signs the handshake, every board head and beacon, `leave` and a delegate's `admit`; its X.509 wrapper is the certificate every pool shows (M1). It is given to the node on standard input by the supervisor; the node never touches the keystore | `device.rs:1-7`; `cert.rs:1-5`; `docs/pools-and-serving.md:33,835` |
| Its custody | `device::Custody` is `Keystore`, `Passphrase` or `File`, held as `Node.custody`. A second enum `Node.key_binding` starts `File` (`node.rs:174`) and only a rig sets `Keystore` (`gov/ops.rs:190-192`); a delegate admit needs it (`ops.rs:260,313`). `Keystore` custody is a file sealed under the master, not a key the system keeps non-exportable | `device.rs:26-59`; `docs/pool-encryption.md` section 10, slice 3 |
| The charter | Version 3. An admin key lists `{key, alg, role, strength, person, since, acceptance, label}`; the acceptance (`pool-accept/v2`) covers them. Nothing says how a key is held. The pool id hashes the epoch-1 charter with acceptances blanked | `gov/charter.rs:26,119-139,186-189`; `gov/keys.rs:16,126-142,165-171` |
| One pool per node | `Pool::load` refuses a directory with two pools; `can_join_other` is false once in one | `membership.rs:157-161,185` |
| The multi-pool design | One certificate per pool (D3, replacing M1), up to 16 pools, a pending key until the pool id is known (2.3), a leave or a failed join destroys the pool key, a rejoin is a new key and certificate (D7). It lists the interface it needs from this document | `docs/design-multi-pool-devices.md` 0, 2.3 |
| The phone | `PoolKey` is a separate non-exportable Keystore P-256 key per pool anchor; its hardware level is "a local diagnostic, never remote attestation". `phone::attest::verify` checks a chain to two committed Google roots, the challenge, TEE or StrongBox, per-use authentication, a locked verified boot, the app id and a fresh revocation list, and has no caller. The publisher policy is `None`, so every verification is refused | `docs/notes/2026-10-14-phone-pool-key.md`; `phone/attest.rs:87-141`, `attest/policy.rs:103-166`, `attest/publisher.rs:8`, `attest/SOURCES.md` |
| What `docs/phone-approval.md` says | It treats the Keystore key (StrongBox "where the phone has it") as storage and `phone: true` as the mark a phone's own enrolment sets. It says nothing of attestation; `docs/pool-encryption.md:44` records that none is requested | "Who holds what" |
| What a member can verify | Nothing about custody. A key's level is `persons.json` as the issuing device wrote it; another member has the charter's word | phone-free 2.2, H2 |

## 2. A. One key per pool

### 2.1 Two keys, and what each is

| | Pool key (the device in the pool) | Admin key (the person in the pool) |
|---|---|---|
| Held by | the node, in memory while the pool is open | the person-facing signer, only for a ticket (phone-free 2.3); never the node (`gov/signer.rs:3-5`) |
| Made | at founding (the draft) and at the start of a join, `pending` until the pool id is bound (multi-pool 2.3) | at the card's prompt: founding, or making this computer an admin of a pool |
| Signs | handshake, board heads, beacons, heartbeat, `leave`, a delegate's `admit`, and statements (3.2) | the charter acceptance, governance acts, approvals and grants for sessions of this pool |
| Sealed by | AEAD under an HKDF subkey of the custody secret | `SigningKeys` under a subkey of the keystore master (the sealing that exists today) |
| Destroyed | a leave, a failed or abandoned join (one hour), a learned removal, an expired draft | a leave that removes it, a replace, or the person's tap after a removal (2.6) |
| Public value | its certificate, in the pool's roster | its public key, in the pool's charter |

Two keys of one computer in two pools, or the two keys of one pool, share no public value and no secret byte. A phone follows the same rule on its side (`PoolKey`).

### 2.2 The choice: independent seeds, not a derivation

| Option | A leak of one pool's key | A leak of the root | Leaving and rejoining | Hardware keys later | Verdict |
|---|---|---|---|---|---|
| HKDF from the device key and the pool id | the root is the always-running node's key and may be a plain file (`Custody::File`) | every pool | cannot be made for a join (the pool id arrives sealed inside the join, multi-pool 4); a rejoin repeats the old key unless a nonce is stored, which is a stored value again | impossible: a root must be readable | rejected |
| HKDF from the custody secret or the keystore master, with a stored random handle | one pool | every pool, unsealed at each signature | "destroy" means forgetting the handle, which a backup holds | impossible | rejected |
| **Independent random seeds, each sealed under its own subkey** | that pool | none: no root. The sealing secret reaches every file it seals, as any keystore secret does (below) | delete the file; a rejoin is a new seed | works: one Secure Enclave, TPM or Keystore key for each pool | **chosen** |

The one thing a derivation buys, a single secret that backs up every pool, is what the owner does not want: a recovery phrase recovers one pool.

| Event | One key per computer (phone-free 2.3) and one certificate (M1) | Two keys per pool |
|---|---|---|
| A signer bug, log line or crash dump shows a key's bytes | every pool | that pool |
| A signing ticket is abused | the computer's pools | the pool the ticket names |
| One sealed file is copied (a backup, a synced folder) and opened later | every pool | that pool |
| Two members of two pools compare notes | the same key and fingerprint link them | no value in common |
| The sealing secret is read (a program of the account, or the account's password) | every pool | every file it seals: unchanged, the stated weakness of `computer` (phone-free 2.1) |
| The computer leaves a pool | the key stays, other pools use it | both keys of that pool are destroyed |

### 2.3 Which key signs what

- The admin key is the person key of its pool. Its `persons.json` row gains `pool`; a grant a session of pool A holds counts only if an A key signed it, and one signed by another pool's key
  counts for nothing. `MOST_KEYS` (8) becomes 8 for each pool. A key made before its pool exists has `pool` empty and counts for one thing, the `pool_create` grant of the pool whose charter lists it.
- The typed-yes grant key of `board/person_key.py` stays: one, `weak`, for developers, in `<state>/person`. It counts for nothing in a pool.
- The recovery key has no claim, no voucher and no level; its power is its role (phone-free 2.4), and its scrypt salt holds the pool id, so a phrase belongs to one pool.
- Approvals cost what phone-free 2.4 says: the card's one prompt is the ticket for every payload it names, whichever key signs it.

### 2.4 Data and where it lives

```
<state>/custody/                       the custody secret (secret.wrapped | secret.enc | secret) and custody.json {custody, check}; today's device.key* and device.custody.json become these
<state>/pools/<id>/key.wrapped         the pool key's seed: ChaCha20-Poly1305 (`ring`, already a dependency) under HKDF("poolhouse/pool-key-wrap/v1", secret, slot); the slot id is in the header
<state>/pools/<id>/cert.der            its self-signed certificate (public)
<state>/pools/pending-<slot>/          the same files for a pending key; renamed to <id> when the pool id is bound
<state>/person/pools/<handle>/         one SigningKeys for each admin key, purpose "pool-key"; <handle> is 32 random hex digits made with the key
<state>/persons.json                   rows gain `pool`, and (phone-free slice 2) the claim and the device's signature
```

No file lists pools beside their keys: the signer finds the admin key of pool P as the handle whose public key P's charter lists. `<state>/person` is sandbox-denied (`docs/agent-sandbox.md:38`). An
admin key has no export: `SigningKeys.export` (`signing.py:259`) refuses a pool-key directory, because a file of a key is a new leak; the way back from a lost computer is the pool's 24 words.
`custody.json`'s `check` is an HKDF of the secret under its own domain, so a directory given another secret is refused as the device key is now (`device.rs:121-125`). The node wraps and
unwraps the pool keys itself with the secret it is handed; it still never touches the operating system's keystore.

### 2.5 The custody secret follows its custody

`Node.key_binding` and `KeyBinding` are deleted. The delegate rule reads the custody of the secret that wraps the pool key: `Custody::Keystore` may sign a delegate admit; `Passphrase` and `File` may
not (`Custody::weak`, `device.rs:51`). `custody` is already set in `Node::open_with` from `device::load`, so the rule is two lines where the second enum is read (`ops.rs:260,313`) and `rig_bind_device_key`
(`app/poolhouse-node/tests/gov_net.rs:412`) becomes a rig setter for `custody`: the "about two lines" of phone-free 2.3.

Said exactly: `Keystore` custody means the secret is sealed at rest under the keystore master and opens for this account on this computer. A program of the account can open it; the delegate's rates and
removal bound that (phone-free D3). The device records its custody in a self statement (3.2, `subject: device`) so the admin sees "Studio keeps its key in the keychain (Studio says so)" on the `delegate`
card, and a delegate admit from a device whose latest statement says `file` or `passphrase` counts for nothing on any member.

### 2.6 The life of the keys

| Event | What happens | Added to the person's steps |
|---|---|---|
| Found | The draft makes a pending pool key; its certificate is the charter's `first.cert`. The card's tap and one prompt make the admin key and the ticket signs the bundle (acceptance, `pool_create`, delegate). `pool_found` binds the pool key to the id and the device issues the first statement for each listed key (3.2) | none |
| Join | The join starts with a pending pool key, bound when the id arrives; no admin key is made. A failed or abandoned join destroys the pending key; one hour at most | none |
| Make this computer an admin of a pool | A card and a prompt make a new admin key; `admin_add` carries its claim and voucher (3.2), is accepted by the key, signed by the current admins and held as phone-free 2.6 says | as phone-free |
| Leave | The device signs `leave` and destroys the pool key at once (multi-pool 3). If the pool lists this computer's admin key, the card proposes `admin_remove` of it with the leave and the key is destroyed when that takes effect; if it is the only admin, the card says the pool can never change after this and offers **Leave anyway**, which destroys it at once | one line on the leave card |
| Removed by others | The signed removal destroys the pool key; the admin key stays listed. The app says "You were removed from 'home'. This computer's key for it is still listed. [Destroy it]"; destroying signs nothing, so it is a tap and no prompt | one tap, only here |
| Rejoin | A new pool key and certificate (D7). An admin key still listed with the old certificate as voucher counts `weak` (its voucher left) until it is replaced by rotation; a leave that removed it leaves nothing to replace | none |
| Recover | The 24 words make that pool's recovery key; `recover` names a new admin key, a new handle on the recovering computer (a computer new to the pool joins first, as any device does). Other pools' keys and phrases are untouched | as phone-free 2.4 |
| Replace an admin key (rotation) | A new handle and key, `admin_add` of it, then `admin_remove` of the old, then the old is destroyed. One pool only; two governance prompts | the card lists both |
| A lost computer | From a surviving key-holder, one card for each pool names the removal of the device and of its admin key. Governance prompts are not bundled across cards and pace at 3 in 10 minutes (phone-free 2.3), so a person in five pools is told first: "3 now, 2 in 8 minutes" | a prompt for each pool |
| An admin key made and never used | Swept after 31 days if no charter and no act of any held pool names it (the longest `admin_add` hold is 720 hours, `charter.rs:300`) | none |

"Destroyed" is stated plainly: the sealed file is deleted and nothing signs with it again. A copy in a backup is as secret as the keychain that opens it, which is why a leave also removes the admin key from the charter where it can.

**Backups and restores.** A sealed file opens only with the sealing secret; an admin key's file opens only under the same directory path as well (`signing.py:109-110`). A copy without the keychain is inert. A Time Machine restore to the
same account, or Migration Assistant to a new Mac with the keychain, opens every key on the clone: a second device with the same certificates in every pool. A restore from an older backup runs the
heartbeat's counter backwards, and a clone running beside the original shows two boot ids under one fingerprint; each pool flags it by its own heartbeat (`docs/pools-and-serving.md:292-296`, designed, not built) and an admin
may `distrust` the device, remove it, or replace that pool's admin key. A restore after which the original is gone is the owner's own move and no member can tell it apart. The secret is re-sealed by walking the pool directories.

### 2.7 What two pools reveal

| Item | In pool A and pool B | Linkable by an observer in both |
|---|---|---|
| Pool keys, certificates, fingerprints, heads, beacons | independent keys (multi-pool D3) | no |
| Admin keys | independent keys | no |
| Recovery keys | a phrase for each pool, the pool id in the salt | no |
| Approval signatures on boards | the pool's own admin key | no |
| Statement voucher | that pool's certificate | no |
| `first.name`, `person`, `label` | `first.name` is the computer's name; `person` and `label` are what the person gave (default `owner`) | yes by name if the defaults stay (multi-pool Q1); the card lets the person rename a pool's names |
| An Android chain in a statement | a factory-provisioned phone's chain may carry a per-phone certificate (`attest/SOURCES.md` names a serial-number attribute on the intermediate) | possibly: the open question, section 7 |
| Source address and port, the `hello` hardware line, timing | one listener | yes on one network (multi-pool 2.3) |

## 3. B. Custody statements

### 3.1 The claim

A claim says how a key is held, in a closed vocabulary. The level is a function of the claim, computed by every member from one table, so they cannot disagree and a caller cannot name a level
(`app/poolhouse-node/tests/gov_strength.rs:58` keeps refusing a draft that names one).

| `method` | `prompt` it needs | `level_of` | Meaning |
|---|---|---|---|
| `phone-keystore` | `biometric` | `phone` | a phone's Keystore key, authentication for each use |
| `helper` | `os-dialog` or `biometric` | `native` | a protected helper under another operating-system identity (phone-free slice 8) |
| `keystore-prompt` | `os-dialog` or `biometric` | `computer` | a file sealed under the keystore, made through the operating system's prompt for this key |
| `keystore-typed` | `typed-yes` | `weak` | sealed under the keystore, made by a typed yes |
| `file` | `typed-yes` or `none` | `weak` | a passphrase file or a plain file |

`os` is `android`, `macos`, `linux`, `windows` or `other`, for words. A claim whose `prompt` does not fit its `method`, or a word outside the vocabulary, is not a claim. The phone-free design stores a level for
each key; this design stores the claim and derives the level. The claim in a listing is the founder's word, as phone-free 2.2 says.

### 3.2 The statement, and where it lives

A key's charter row gains `claim` and `voucher` (the certificate fingerprint of the one device that stands behind the key; at founding the first device) and loses `strength` and the phone-free
`assurance`. The key's acceptance covers both, so a key is listed only with its claim and voucher, by its holder.

A **statement** is the act `vouch`, with one device signature (`SigKind::Device`) of the voucher's pool key. The act envelope binds the pool, epoch, `prev`, a nonce and `at_ms`, so a statement is bound to the pool and a time
(`gov/act.rs:16,80-84`); nothing signs the pool id twice.

```
vouch body   {"subject": "key" | "device", "key": "<public key hex, or the device's certificate fingerprint>",
              "claim": {"method": ..., "os": ..., "prompt": ...},
              "evidence": null | {"kind": "android-key-attestation", "chain": ["<hex DER>", ...], "nonce": "<32 hex>",
                                  "revocations": {"digest": "<64 hex>", "fetched_ms": n, "valid_until_ms": n}}}
distrust, trust body   {"device": "<fingerprint>"}
challenge of an attested key   SHA-256("poolhouse/pool-key-challenge/v1\n" + the key's handle + the voucher's fingerprint + nonce)
```

- **Who may sign.** Only the device the key's row names, for that key; a `subject: device` statement only by the device, about its own custody (`method` is `keystore`, `passphrase` or `file`). The voucher must be an active member.
  A statement by another device, or about a key the pool does not list, counts for nothing.
- **When it is made.** At founding, in `pool_found`; after an `admin_add` takes effect; every 30 days while the voucher runs, with no prompt and no tap, re-deriving the claim from live facts (the key is still in the keystore,
  the custody is unchanged) and, for an attested key, re-checking revocations. A statement can say less than the last.
- **How many stand.** The newest two for each key; older ones go by the pool's room rules (`gov/room.rs`). They are device acts, so the 256 admin-set cap is untouched.
- **Challenge.** It cannot hold the pool id or anchor, because the Android key (and so its attestation) is made before the charter that lists it. It holds the key's handle, the voucher and a nonce the voucher chose. The
  chain proves "this key was made in secure hardware for that voucher"; the act envelope and the key's acceptance bind it to this pool, and moving it to another pool needs that key's acceptance there.
- **Time.** A statement dated more than five minutes ahead of the receiver's clock is refused (the act rule, `docs/node.md` "Who governs a pool"). Aging below uses `at_ms`, which a signer can only set earlier, so it can only make a
  statement older. Clones are not detected here: the heartbeat of 2.6 does that.

### 3.3 What a member checks

On ingest, from bytes only: the act's shape and size (an evidence chain is at most 6 certificates and 6,500 bytes decoded, so a hex act stays under 16 KiB, `MAX_ACT_BYTES`; real chains are not measured here and the first hardware run
records them); the device signature against the voucher's certificate; that the voucher is the key's listed voucher and an active member; for `android-key-attestation`, `phone::attest::verify` with the member's own committed roots and
policy and the revocation summary the statement carries. No member reads a `basis` or `level` field; there is none. A build that does not know an evidence kind treats the statement as unevidenced.

### 3.4 Basis and the counted level

| Basis | When a member computes it | Cap |
|---|---|---|
| `attested` | the evidence verifies on this member and is at most 90 days old | the level the evidence proves: `phone` for a passing Android chain; nothing else is defined |
| `vouched` | no passing evidence, and the voucher is an active member in good standing | `computer` |
| none | no statement, the voucher has left, was removed, set aside or distrusted, or a recovery key | `weak` |

- C1. **Counted level = the lower of `level_of(claim)` and the cap of the basis.** Phone-free R1 to R6 use the counted level wherever they say level.
- C2. An attested basis does not need the voucher present: the chain proves itself. It lasts 90 days from `at_ms` and then reads `vouched`, so a revocation reaches it.
- C3. The newest statement for a key (by the order this member first saw it, as the roster orders admits) wins. One that lowers the counted level counts at once; one that raises it counts at once and is shown (3.7).
  Raising to `computer` by word is bounded by the holds of that level; raising to `phone` needs evidence.
- C4. An act counts a signer if its counted level meets the floor when this device first has the whole act and again when the hold ends. A key that falls below during a hold leaves the act short.
- C5. No fold removes a key from the charter. A key that counts nothing stays listed, so a statement can never empty a pool; the way back from a pool with no counting key is `recover`.
- C6. Before the owner sets the Android publisher policy no chain verifies (`publisher.rs:8`), so a phone's `phone` claim is `vouched` and counts as `computer`. That is the safe state, and the person is told in the same sentence (3.7).

### 3.5 What can and cannot be proven

"Vouched" means one sentence: the device that holds or enrolled the key says how it is held. Software on a computer cannot prove operating-system custody to another member, because the member would have to trust the
program that reports it. What exists on each platform, and what this design does with it:

| Platform | Where the key sits | What the vouching device itself learns | What another member can check | Basis it reaches | Highest counted level |
|---|---|---|---|---|---|
| macOS | a file sealed under the login-keychain master | the system's administrator dialog was answered for this key (`presence/mod.rs`); the signer's report of where it stores the key | nothing | vouched | `computer` |
| macOS Secure Enclave | not used: it needs a signed app with a keychain entitlement and a paid developer account (`docs/pool-encryption.md` 8.1) | n/a | Apple's remote attestation (App Attest) is tied to a registered app; I did not check its macOS availability | none possible now | n/a |
| Windows | the account's credential store through `keyring` | no prompt exists: the first key is refused (`presence/mod.rs:224`) | nothing. TPM key attestation with the maker's chain exists in principle (`docs/pool-encryption.md` G5) and is unbuilt | none | `weak` until a prompt exists, then `computer` |
| Linux | the Secret Service through `keyring`, else the passphrase file | the polkit dialog (`pkexec`) was answered | nothing. TPM 2.0 certify is the counterpart of the Windows case, unbuilt | vouched | `computer` with the keystore and the dialog; `weak` with the passphrase file |
| Android phone | Keystore, StrongBox where present; a new fingerprint destroys the key | the chain of the key, shown at the scan | the chain to Google's roots: hardware level (TEE or StrongBox), P-256, sign only, per-use authentication, locked verified boot, app id, signer and version floor, challenge, a fresh revocation list (`policy.rs:103-166`) | attested once the publisher policy is set; vouched before | `phone` with evidence; `computer` without |
| Native helper (phone-free slice 8) | another operating-system identity | the helper's own statement | a signature by an issuer key the pool admits: an evidence kind reserved here and defined in slice 8 | issued, reserved | `native` once defined |

A chain does not prove that the finger is the person's, that the phone's trusted environment has no flaw, or the vendor's honesty (the trust is Google's roots and the maker's intermediates). It is evidence about the key,
and the version floor makes it evidence about our app. It says nothing about the computer a node runs on.

### 3.6 A lie, and standing

Only the device the listing names can vouch, and the key accepted that listing, so a lie is one device describing a key it holds or enrolled. Two cases:

- **Provable from the statement's own bytes.** Evidence whose chain does not reach a committed root, whose leaf key is not the statement's key, whose challenge does not match, or that is malformed. An honest device
  verifies before it signs, so such a statement means a modified or malicious device. Every member derives the same result: the voucher's **standing** is `set_aside`. A shortfall of policy (an old version floor, a stale or revoked
  list) is not a lie: it makes the key unattested, not the voucher untrusted.
- **Not provable.** A device says `keystore-prompt` for a key in a file. No member can know. The cap makes it harmless where it is dangerous: the key counts as `computer`, with the 7-day holds phone-free 2.6 puts on `computer`
  keys, and cannot count as `native` or `phone`. It can do what a software key claimed as `computer` could do.

Standing is `good`, `set_aside` or `distrusted`; the last is an admin's `distrust {device}` act (routine class, any one counting key signs), undone by `trust {device}`. For a device not in good standing every statement it signed reads basis none
(its keys count `weak`, its self statement included) and its delegate admits count for nothing. It stays a member and its data acts are untouched. The pool header says "'Studio' is not trusted to vouch for keys. [Remove] [Trust again]";
every member gets a banner and a notification and the entry `custody.standing` is audited. `trust` also clears `set_aside`: the admin saw what the device did. Removal stays the admins' sticky `remove`; this is a step short of it.

The cost of a lie is: the keys it vouched stop counting, its delegate power stops, the pool is told, and its name sits on the signed statement. A lie nobody can prove costs the liar only what the cap already takes; this design
says so rather than promising a penalty it cannot apply. A key whose voucher is gone counts `weak` until it is listed again by a device that holds it (rotation); an attested phone key is unaffected.

### 3.7 What the person sees, and no silent downgrade

The words come from the fold at the moment of reading; no sentence is stored. The `LEVEL:` line of phone-free 2.7 gains the basis (appended after the cut and signed with the text, so a signer given a different line from the one it
recomputes refuses to sign).

| Counted | Basis | Claim | Words on the card and in the header |
|---|---|---|---|
| `phone` | attested | `phone` | "your phone, checked: its key sits in the phone's secure hardware and needs your fingerprint" |
| `computer` | vouched | `keystore-prompt` | "this computer ('Studio' says so; nothing else can check it)" |
| `computer` | vouched | `phone-keystore` | "your phone, as 'Pixel' says; this pool cannot check it, so it counts as a computer and big changes wait 7 days" |
| `computer` | vouched | `helper` | "this computer, protected, as 'Studio' says; this pool cannot check it" |
| `weak` | none | any | "a typed yes, or a key nobody stands behind ('Studio' is not trusted to vouch)" |

- Any change of the counted level or the basis of any key since the person last saw it (aged to vouched at 90 days, a statement lowered or raised, the voucher removed, set aside or distrusted, a chain no longer verifying) is a
  banner, an operating-system notification on every member device and an audit entry `key.level_changed` naming the key, the old and new words and the cause.
- A card for an act that lists a key which counts less than it did says so before the prompt: "After this, 1 of 2 keys stops counting" (`describe`, from the fold). Removing a voucher lists the keys that stop counting.
- The pool header reads "Approved from: this computer ('Studio' says so)" with **Add a phone** beside it, and the not-yet-checkable phone line above. No setting hides a basis.

## 4. Formats, versions, migration, costs

- **Charter** `v` 5: each admin key gains `claim` (null for a recovery key) and `voucher` (empty for a recovery key) and drops `strength`. Version 4 is phone-free slice 1 (`floors`, `assurance`, `counting(class)`); this design replaces its per-key
  `assurance` by `claim`. If that slice has not landed when slice 4 here starts they land as one version, the next integer; the field names do not change. The pool id hashes the charter with acceptances blanked, so the claim and voucher are in the id.
- **Acceptance** `poolhouse/pool-accept/v3\n` covers `claim` and `voucher` besides today's fields. **Act kinds** 13 to 16 (`vouch`, `distrust`, `trust`); `poolhouse/pool-act/v2` and act version 2 stay. `vouch` is a device act, taken only from
  the listed voucher; `distrust` and `trust` are routine admin acts. **`pool_status.governance.admins[*]`** gains `claim`, `counted`, `basis`, `voucher`, `vouched_ms`; `governance.standing` lists devices not in good standing.
- **Where it lives.** Keys: 2.4. Statements: the pool's `acts.json`, folded in a new `gov/custody.rs` beside `roster.rs`, built from the acts and the roster and never from a row a member sent. Nothing else is written.
- **Migration: none.** A charter of another version is set aside by the existing rule (`membership.rs:48-62`; "this pool was made by another version of Poolhouse and cannot be used: make a new pool"); a pool with no `key.wrapped` is set aside
  as multi-pool section 5 says; the old `device.key*` is not read as a custody secret or a pool key. No legacy mode, no shim.
- **Fixed costs.** A key is one Ed25519 generation and one AEAD seal; the admin key reuses the master the process already holds (no new keystore item, no extra Keychain dialog, the 5 makes and removals an hour untouched). A statement is one Ed25519
  signature and, when attested, about 4 P-256 verifications once at ingest, cached by act id; the fold reads the cache. Not measured here; slices 1, 2 and 5 carry tests that bound them. Disk: at most 2 statements for each key, near 600 bytes
  with no evidence and under 16 KiB with it. Wire: the existing `acts` op; no new message.
- **Onboarding.** No terminal, file, key or port. Founding, joining and approving are as phone-free 2.4 and multi-pool 4. Added: the basis words, one line on the leave card, the banners. No setting, flag or error on a normal journey.

## 5. Build plan

Each slice lands alone, with tests seen to fail first. Slices 1 to 3 need no charter and can land before phone-free slice 1; slice 4 needs it. The node-side slice 2 is what multi-pool slice 3 waits for. Sizes: S under a day, M one to three days, L a week.

| # | Slice | Exists | New | Tests that fail first | Size |
|---|---|---|---|---|---|
| 1 | **An admin key for each pool** | `SigningKeys`, `Keystore.wrap` with an owner, `person_key.keys` | `PoolKeys` makes a `SigningKeys` in `person/pools/<handle>/` (purpose `pool-key`); `Admin` is built for a handle; sweep after 31 days; destroy; export refused. `ComputerKey` (phone-free slice 3) signs with a handle through its ticket | `a_new_pool_gets_an_admin_key_no_other_pool_holds`; `two_admin_keys_share_no_public_key_and_no_sealed_bytes`; `a_sealed_file_of_one_pool_does_not_open_as_anothers`; `a_directory_copied_without_the_keystore_opens_nothing`; `the_signer_unwraps_only_the_key_of_the_pool_it_signs_for`; `making_an_admin_key_adds_no_keystore_item_and_reads_the_master_once`; `an_admin_key_cannot_be_exported`; `an_unused_key_is_swept_after_31_days_and_a_listed_one_never`; `destroying_a_key_deletes_the_directory` | M |
| 2 | **The custody secret and the pool key** | `device::load`, `Custody`, `cert.rs`, `fsutil::random_hex`, `ring` | `custody/` replaces `device.key*`; a pool key and certificate per pool (pending, bound, destroyed) sealed under an HKDF subkey of the secret; `KeyBinding` deleted, the delegate rule reads `Custody`; `rig_bind_device_key` sets `custody`. The rejoin test needs multi-pool slice 1 | `a_new_pool_key_shares_no_public_value_with_another_pools`; `a_pending_key_is_bound_to_the_id_when_the_join_completes`; `a_failed_join_or_an_hour_destroys_the_pending_key`; `a_leave_destroys_the_pool_key_at_once`; `a_rejoin_makes_a_new_key_and_certificate`; `a_key_file_does_not_open_under_another_pools_subkey`; `the_custody_secret_signs_nothing_a_pool_sees`; `a_directory_given_another_secret_is_refused`; `a_keystore_custody_signs_a_delegate_admit_and_a_passphrase_or_file_custody_does_not`; `opening_sixteen_pools_unseals_each_key_once`; `app/poolhouse-node/tests/gov_net.rs:412` rewritten | L |
| 3 | **Admin keys belong to a pool** | `Persons`, grants | `pool` on person rows and `MOST_KEYS` for each pool; a pending key counts for one `pool_create` | `a_grant_signed_by_another_pools_key_counts_for_nothing`; `a_pending_key_signs_only_the_pool_create_grant_of_the_pool_it_founds`; `eight_keys_for_each_pool_not_eight_in_all` | S |
| 4 | **Claim, voucher, statement, counted level** | `AdminKey`, `acceptance_message`, `roster`, `verdict`, `Charter::counting` (phone-free slice 1) | charter 5; acceptance `v3`; `level_of`; kinds `vouch`, `distrust`, `trust`; `gov/custody.rs`; C1 to C5; the founding statement in `pool_found`; renewal every 30 days; `describe` lines | `a_vouched_key_counts_at_most_computer`; `a_claim_of_phone_with_no_evidence_counts_as_computer`; `a_level_field_in_a_draft_is_refused`; `a_statement_by_another_device_than_the_listed_voucher_counts_for_nothing`; `a_statement_for_another_pool_is_refused`; `a_charter_with_another_claim_is_another_pool`; `a_lowered_statement_counts_at_once`; `a_raised_statement_counts_and_is_shown`; `a_key_that_falls_below_the_floor_during_a_hold_leaves_the_act_short`; `a_removed_voucher_leaves_its_keys_weak_and_the_card_names_them`; `a_statement_never_removes_a_key_from_the_charter`; `a_statement_dated_ahead_is_refused`; `only_the_newest_two_statements_stand` | L |
| 5 | **Attestation as evidence** | `phone::attest::verify`, `FreshRevocations`, `verify_with` | `android-key-attestation` on ingest; the challenge; 90-day aging; the size cap; the member's own roots and policy | `an_attested_statement_verifies_on_every_member_from_its_bytes`; `a_chain_for_another_key_is_provably_false`; `a_challenge_for_another_voucher_is_provably_false`; `a_policy_shortfall_is_unattested_not_a_lie`; `evidence_older_than_90_days_counts_as_vouched`; `a_chain_over_the_size_cap_is_refused`; `an_attested_key_counts_without_its_voucher_present`; `the_fold_with_sixteen_attested_keys_verifies_each_chain_once`. Parks until the open question and the publisher policy are settled | M |
| 6 | **Standing** | `roster` order, `delegate` rules | `set_aside`, `distrust`, `trust`; delegate admits need good standing and a statement that says `keystore` | `a_provably_false_statement_sets_the_voucher_aside_on_every_member`; `a_set_aside_voucher_keeps_its_membership_and_its_data_acts`; `distrust_turns_its_keys_weak_and_trust_turns_them_back`; `a_delegate_admit_from_a_device_whose_statement_says_file_counts_for_nothing` | M |
| 7 | **Words** | phone-free 2.7 `LEVEL:` | the basis words; header; `pool_status` fields; `key.level_changed`; banners; glossary and `docs/node.md`; pool-check steps `custody-vouched-cap`, `liar-voucher-capped`, `two-pools-share-no-key` | `the_level_line_carries_the_basis_and_survives_the_300_character_cut`; `a_signer_refuses_a_level_line_that_differs_from_its_own`; `every_fall_of_a_level_is_a_banner_and_an_audit_entry`; `the_words_are_computed_at_reading_not_stored`; `two_pools_on_one_computer_list_no_common_value` | M |
| 8 | **Leave, replace, lost computer** | `pool_leave`, `admin_remove`, phone-free cards | the leave card with the key; replace; the lost-computer list with the pacing line; destroy after removal | `leaving_proposes_removing_the_admin_key_and_destroys_it_when_that_takes_effect`; `leaving_with_the_only_admin_key_says_the_pool_can_never_change`; `replacing_a_key_adds_then_removes_and_destroys_the_old`; `a_lost_computer_lists_one_card_for_each_pool_and_says_the_pacing`; `a_removed_device_offers_to_destroy_its_key_and_asks_no_prompt` | M |

Order: 1, 2, 3; then 4; then 6 and 7 beside 5 once the open question is answered; 8 after phone-free slice 4 (the card screen).

## 6. Attack first

Written by the author; the break-it pass by a non-author is still to be done before slice 2 and slice 4 are built (AGENTS.md section 1). Each row holds under the owner's order that a pool is never blocked on a phone.

| Case | What stops it | What remains, named |
|---|---|---|
| **Stolen device** (locked, or unlocked with its password known) | A locked computer opens nothing without the keychain. A thief who knows the password opens every key on it, as for any keystore secret (`docs/pool-encryption.md` section 3) | The loss is per computer, so the cleanup is a card for each pool (2.6) at 3 prompts in 10 minutes, said first. Removing its device in a pool turns the keys that device vouched `weak`; removing its admin key ends their signatures |
| **Cloned state directory, or a Time Machine or Migration Assistant restore** | A directory without the keychain opens nothing, and an admin key's account name hashes its path. With the keychain the clone holds every key: a second device with one certificate in each pool. Each pool's heartbeat flags it (counter backwards, or two boot ids); an admin can `distrust` it, remove it, or replace that pool's admin key | The clone works until an admin acts; the delegate window and the 7-day holds bound it (phone-free section 4, M5). A restore after which the original is gone is the owner's own move |
| **A liar vouching a software key as hardware** | Only the key's listed voucher can vouch and the key accepted it, so a member cannot vouch for another's key. A `phone` or `native` claim with no checkable evidence counts as `computer`; a false chain sets the voucher aside on every member; an admin can `distrust` any voucher | A software key claimed as `keystore-prompt` counts as `computer` and nothing proves otherwise. It cannot count as `native` or `phone`. Said in 3.5 and the words of 3.7 (H2 stays true for `computer`) |
| **Cross-pool replay** | A statement is an act: pool, epoch, `prev`, nonce, `at_ms` and the voucher's signature are in its signed bytes; the voucher's pool key is that pool's only. The key's acceptance holds the anchor or `prev`. The challenge holds the voucher, so a chain made for one voucher fails for another. The recovery salt holds the pool id | A chain copied into another pool needs that key's acceptance there. A holder who lists one phone key in two pools links them by its own choice; a phone makes a key for each pool |
| **A downgrade of the stated level** | A lowered statement counts at once and is shown. A statement from another device counts for nothing. An old higher statement is the same act id and changes nothing; order is first-seen, not the signer's time. Aging attested to vouched is a banner. A floor is raised at once and lowered held (phone-free R4) | A compromised voucher can lower its own keys to `weak` and so stop them counting: an honest re-vouch restores it, and a pool with no counting key uses `recover`. The same voucher could always refuse to sign |
| **Approval fatigue** | Statements need no prompt, tap or card, so nothing asks the person. The new cards (leave, replace, distrust) take the per-kind cooldown, the ask limit and the prompt pacing of phone-free 2.3 | A lost computer in many pools is a queue of prompts, said before it starts. Not bundled, because a removal is sensitive |
| **A compromised or malicious member** | It cannot vouch for a key it is not the voucher of, cannot `distrust` (an admin act), cannot raise a counted level above its cap, and its flood of statements is cut at the newest two for each key. It holds no key of another pool | It can lie about its own keys (the liar row) and withhold relayed acts as in any pool |
| **A malicious agent, or one injected without knowing** | No call takes a claim, a level or a statement: the device writes them from its own facts. An agent cannot tap, cannot answer the prompt, cannot read `<state>/person` (sandbox `denyRead`) or `<state>/custody`. A board message that says "vouch as hardware" has no path to a statement | An agent with the account's rights outside the sandbox can write `persons.json`; a row is believed only with the device's signature (phone-free 2.2) |
| **Stolen phone or key** | A phone key signs only with the person's finger; a new fingerprint destroys it. An attested key's chain proves hardware, not the owner | A stolen unlocked phone and its owner's finger, as phone-free 2.1 |
| **Takeover by whoever is merely present** | Presence gives no key. A new admin key needs the prompt answered; a statement needs the listed voucher's pool key | Someone at an unlocked computer who knows its password: the stolen-device row |
| **Copied or cloned pool** | The pool id hashes the charter with claims and vouchers, so another claim is another pool; `founding_of` refuses a charter that names another computer first (`ops.rs:178-180`) | None new |
| **Forged or below-threshold signature** | Each member recomputes the basis from the bytes and the roster; a forged statement fails the voucher's certificate; no field states a level | None new |
| **Secure to open** | Unchanged: the policy is in the charter | The unauthenticated `hello` still returns the policy of a `secure` pool and nothing else (multi-pool 2.3) |

## 7. Decisions made, and the one open question

Made in this design, each with the option it rejects:

- **K1. Independent random seeds for both keys, not a derivation** (2.2). Rejected: HKDF from the device key (the node would hold a person key and one leak is every pool; no pool id at creation; a rejoin repeats the key) and from a separate root.
- **K2. The admin key is the person key of its pool**, not one approval key for the device. Rejected: a device-wide approval key beside admin keys, because a grant entry names its key on a board that more than one pool may hold, and a shared key links the pools.
- **K3. The claim is stored and the level is computed.** Rejected: storing the level beside the claim, which lets the two disagree and lets a caller name a level.
- **K4. Vouched caps at `computer`, including a founder's own `phone` claim.** Rejected: letting a founder's unchecked claim count at `phone` in a pool of one, because the next device to join cannot check it either.
- **K5. A clone is the heartbeat's notice and a possible `distrust`, not a hold on its powers.** Rejected: suspending a restored device until an admin taps, which puts a step on the normal move to a new Mac and stops nothing a password-knowing thief could not redo in a pool of one.
- **K6. A provable lie sets the voucher aside mechanically; an unprovable one is only capped.** Rejected: promising a penalty for a claim no one can check.
- **K7. No export of an admin key.** The way back is the 24 words for that pool.

**Open question (both options are legitimate).** Should a statement carry an Android attestation chain?

| Option | Gains | Costs |
|---|---|---|
| 1. The chain is in the statement | every member checks the phone's hardware itself; `phone` can count; the correct choice for security | a factory-provisioned phone's chain may carry a per-phone certificate, which links that phone's pools for members of both. I did not read a real chain; the first hardware run records what a member would see |
| 2. The chain stays with the voucher | nothing new is revealed; pools stay unlinkable | no member can check the phone; a `phone` claim counts as `computer` for ever |

Until it is answered slice 5 stays parked and option 2's state is in force (the safe default: nothing new revealed, `vouched` caps at `computer`). Slice 5 also needs a person-only step no agent can take: the Android publisher policy
(`phone/attest/publisher.rs`, `SOURCES.md`), a reviewed source commit tied to the owner-approved APK's signer digest and a version floor.

## 8. Documents that change

`docs/design-phone-free-pools.md` (2.1 and 2.2: the level is computed from a claim and a basis; 2.3: the "one pool key per computer" paragraph becomes the admin key of this design, and the L2 row in section 4 is closed; slice 1 stores `claim` in place of
`assurance`; slice 2's rows move to `persons.json` with `pool`; slice 5's `key_binding` line becomes slice 2 here); `docs/design-multi-pool-devices.md` (2.3 points here for the keys, and "If the keys document differs" is closed; its slice 3 needs slice 2 here);
`docs/pools-and-serving.md` (4.1 and M1 are replaced by multi-pool D3; 1.6 clone heartbeat is the clone detector used here); `docs/glossary.md` (add **claim**, **voucher**, **custody statement**, **basis**, **standing**, **pool key**, **custody secret**);
`docs/node.md` "Who governs a pool" (charter 5, the three kinds, acceptance v3, `gov/custody.rs`) and "Key custody"; `docs/phone-approval.md` ("Who holds what": a Keystore key per pool and the attestation chain as evidence, not a mark);
`docs/pool-encryption.md` section 10, slice 3 (`device.key*` becomes the custody secret); `docs/pool-check.md` (the three steps); `HANDOFF.md` (the pending items as each slice lands, and the publisher policy).
