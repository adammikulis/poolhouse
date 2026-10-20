# One key per pool, and verifiable custody

Design. Nothing here is built. Section 1 is code and document reading at `94b34136a` (`0.3dev`); no test was run and no screen was driven.

Two linked changes the owner ordered: (A) a distinct key for each pool, so a leak is one pool's and two pools cannot be linked by key (L2 of `docs/design-phone-free-pools.md`);
(B) a signed, checkable account of how a key is held, so a key's level is no longer only the issuing device's word (its H2). It is the "keys document" that
`docs/design-multi-pool-devices.md` (landed) assumes in its section 2.3; the interface that document lists is 2.1, 2.3 and 2.6 here, and that document follows this one where they differ.
Part A changes no charter. Part B changes the charter and adds four acts.

## 0. In short

- **Two independent keys for each (computer, pool).** The **pool key** is the device's key in that pool: an Ed25519 key and a certificate, held by the node, signing the pool's
  handshake, board heads, beacons, clone heartbeat, the device's own `leave` and a delegate's `admit`. The **admin key** is the person's key in that pool, held by the person-facing signer and
  never by the node: it accepts the charter, signs the pool's acts and decides approvals for the pool's boards. (Phone-free 2.3 calls this one "the pool key"; here it is the admin key
  and "pool key" is the multi-pool design's word.) Neither is derived from anything: each is a random seed, sealed, made for one membership and destroyed when it ends.
- **Why not derive.** The pool id does not exist when either key is made (it hashes the charter that names them, and a joiner learns it inside the join), and a rejoin must give a new
  key (multi-pool D7), so any derivation needs a stored random value anyway; storing the seed costs the same and leaves no root whose leak is every pool. HKDF has one use: cutting
  each key's wrapping subkey from the secret that seals it. 2.2.
- **The device-wide secret signs nothing.** What the device key is today becomes a **custody secret**: 32 random bytes in the keystore (or under a passphrase, or in a file) that wraps
  the pool keys. The node holds it for one operation at a time. `Node.key_binding` is deleted and the delegate rule reads the secret's custody, which is the starter's word. 2.5.
- **An admin key is sealed under its handle, never a path, and never leaves as a file.** A rename, a volume move or a restore opens it; a move to a new computer is a live transfer under a code. 2.4, 2.8.
- **B. A custody statement.** A member device signs with its pool key a numbered statement of how a key is held (a claim: method, system, prompt), bound to the key, its listing, the pool and
  a time. A statement can only lower the level the listing gave or restate it; raising is an admin act. Every member recomputes what it proves; no field says a level. 3.1 to 3.4.
- **The level a key counts at is computed**, at the position of the act it signs, as the lowest of the level its listing names, the level its newest statement names and the cap of its basis.
  `vouched` (one device's word) caps at `computer`; no basis caps at `weak`. A `phone` or `native` claim nobody can check counts as `computer`, with the holds that carries. The Android chain stays
  with the voucher and is in no statement. 3.4, 3.5.
- **A voucher that goes does not zero the keys.** Its keys count for the acts that cure the loss (removing it, naming a successor voucher) and a removal names the successors in the same card. 3.7.
- **A lie.** Two statements one device signed with one number, or numbers in an order its own `prev` contradicts, are a proof every member checks from bytes; the voucher is set aside. A lie nobody
  can prove is made harmless by the cap. `distrust` and `trust` are admin acts under thresholds and holds. 3.6.
- **Words.** The person sees level and basis in one sentence on every card and in the pool header, recomputed by the signer from bytes; every fall of a level is a banner. 3.8.
- **Versions.** Charter 3 to 5 (4 is phone-free slice 1), acceptance `v3`, kinds `vouch`, `distrust`, `trust`, `revouch`, envelope unchanged. No migration. Eleven slices (section 5).

## 1. What the code does today

Paths are under `app/poolhouse-node/src/` unless they start with `app/`, `src/`, `tests/` or `docs/`. The brief named `src/poolhouse/pool/*`; those files do not exist. The Python side
is `src/poolhouse/board/founding.py`, `board/person_key.py`, `fleet/onboard/signing.py`, `keystore.py` and `person.py`.

| Topic | What is there | Where |
|---|---|---|
| One admin key for every pool | `founding.Admin` takes one `grants.Signs`; the person key is one `SigningKeys` in `<state>/person` (`signing.json`, `signing.key.wrapped`) | `src/poolhouse/board/founding.py:41-48`, `board/person_key.py:29,52`; phone-free 2.3 |
| How a Python key is sealed | `ks.wrap("fleet-signing", account, raw)`; `account` is `onboard-signing-` and a hash of the key directory's path. `Keystore.wrap` takes an HKDF subkey of the one master per OS user with purpose and owner in `info`: another owner is another subkey, and no keystore item is added. Reads 600 an hour, makes and removals 5 | `signing.py:38,109-110,138-143`; `keystore.py:57-58,462-468,482-486` |
| What the node knows of a person key | `persons.json` rows `{public, label, at_ms, alg, phone}`, at most 8; the only level is `phone` (strong) or not (weak) | `grants/person.rs:18,34-46,197`; `gov/ops.rs:157-159` |
| The device key | One Ed25519 key signs the handshake, every board head and beacon, `leave` and a delegate's `admit`; its X.509 wrapper is the certificate every pool shows (M1). It is given to the node on standard input by the supervisor; the node never touches the keystore | `device.rs:1-7`; `cert.rs:1-5`; `docs/pools-and-serving.md:33,835` |
| Its custody | `device::Custody` is `Keystore`, `Passphrase` or `File`, held as `Node.custody`; the record is what the starter says (`Supplied.custody`). A second enum `Node.key_binding` starts `File` (`node.rs:174`) and only a rig sets `Keystore` (`gov/ops.rs:190-192`); a delegate admit needs it (`ops.rs:260,313`). `Keystore` custody is a file sealed under the master, not a key the system keeps non-exportable | `device.rs:26-59,128-131`; `docs/pool-encryption.md` section 10, slice 3 |
| The charter | Version 3. An admin key lists `{key, alg, role, strength, person, since, acceptance, label}`; the acceptance (`pool-accept/v2`) covers them. Nothing says how a key is held. The pool id hashes the epoch-1 charter with acceptances blanked | `gov/charter.rs:26,119-139,186-189`; `gov/keys.rs:16,126-142,165-171` |
| Acts | `{v, pool, epoch, kind, body, at_ms, not_after, nonce, prev, signatures}`; `prev` is the hash of the charter the signer knew; a signature is `Admin` or `Device`. Folds order by `at_ms`, which the signer picks, and `seen` order, which is local | `gov/act.rs:3-6,38-46`; `gov/roster.rs:187-200`; `gov/pool.rs:532,610` |
| One pool per node | `Pool::load` refuses a directory with two pools; `can_join_other` is false once in one | `membership.rs:157-161,185` |
| The multi-pool design | One certificate per pool (D3, replacing M1), up to 16 pools, a pending key until the pool id is known (2.3), a leave or a failed join destroys the pool key, a rejoin is a new key and certificate (D7), grants for pool-level acts name their pool (2.5), a clone heartbeat per slot. It lists the interface it needs from this document | `docs/design-multi-pool-devices.md` 0, 2.2, 2.3, 2.5 |
| The heartbeat | Designed: `{fingerprint, boot id, counter}` each minute with a counter persisted before sending. No source in the node has a boot id or a counter | `docs/pools-and-serving.md:292-296`; a search of `app/poolhouse-node/src` |
| The phone | `PoolKey` is a separate non-exportable Keystore P-256 key per pool anchor; its hardware level is "a local diagnostic, never remote attestation". `phone::attest::verify` checks a chain to two committed Google roots, the challenge, TEE or StrongBox, per-use authentication, a locked verified boot, the app id and a fresh revocation list, and has no caller. The publisher policy is `None`, so every verification is refused | `docs/notes/2026-10-14-phone-pool-key.md`; `phone/attest.rs:87-141`, `attest/policy.rs:103-166`, `attest/publisher.rs:8`, `attest/SOURCES.md` |
| What `docs/phone-approval.md` says | It treats the Keystore key (StrongBox "where the phone has it") as storage and `phone: true` as the mark a phone's own enrolment sets. It says nothing of attestation; `docs/pool-encryption.md:44` records that none is requested | "Who holds what" |
| What a member can verify | Nothing about custody. A key's level is `persons.json` as the issuing device wrote it; another member has the charter's word | phone-free 2.2, H2 |

## 2. A. One key per pool

### 2.1 Two keys, and what each is

| | Pool key (the device in the pool) | Admin key (the person in the pool) |
|---|---|---|
| Held by | the node, in memory while the pool is open | the person-facing signer, only for a ticket (phone-free 2.3); never the node (`gov/signer.rs:3-5`) |
| Made | at founding (the draft) and at the start of a join, `pending` until the pool id is bound (multi-pool 2.3) | at the card's prompt: founding, or making this computer an admin of a pool |
| Signs | handshake, board heads, beacons, clone heartbeat, `leave`, a delegate's `admit`, and statements (3.2) | the charter acceptance, governance acts, approvals and grants for sessions of this pool |
| Sealed by | AEAD under an HKDF subkey of the custody secret, bound to the pool, the slot and the certificate | `SigningKeys` under a subkey of the keystore master, account `pool-key-<handle>` |
| Destroyed | a leave, a failed or abandoned join (one hour), a learned removal, an expired draft | a leave that removes it, a replace, or the person's act after a removal (2.6), always moved aside for 7 days first |
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
| A log line or crash dump shows the custody secret | every pool | every pool: the node holds the secret for one operation and wipes it (2.5), so a dump taken at rest holds none |
| A memory read of the running node | every pool | the pool keys of the open pools, up to 16: separation between pools is a property of files at rest, and of a dump taken outside an operation, not of live memory |
| A signing ticket is abused | the computer's pools | the pool the ticket names |
| One sealed file is copied (a backup, a synced folder) and opened later | every pool | that pool |
| Two members of two pools compare notes | the same key and fingerprint link them | no value in common |
| The sealing secret is read (a program of the account, or the account's password) | every pool | every file it seals: unchanged, the stated weakness of `computer` (phone-free 2.1) |
| The computer leaves a pool | the key stays, other pools use it | both keys of that pool are destroyed |

### 2.3 Which key signs what

The interface the multi-pool design lists (a pool key for each (device, pool), made pending and bound at the join, signing handshake, heads, beacons, heartbeat, `leave` and a delegate `admit`, destroyed by a leave, a
failed join, a learned removal or an expired draft, sharing no public value with another pool's, wrapped by a custody secret that signs nothing a pool sees) is 2.1, 2.4 and 2.6. What this section adds to it is the admin key.

- The admin key is the person key of its pool. Its `persons.json` row gains `pool`. A grant for a pool-level act carries the pool id as its resource and a grant with `*` for one is refused (multi-pool 2.5); a grant a session
  of pool A holds counts only if an A admin key signed it and its resource names A, and one signed by another pool's key, or naming another pool, counts for nothing. `MOST_KEYS` (8) becomes 8 for each pool. A key made before
  its pool exists has `pool` empty and counts for one thing, the `pool_create` grant of the pool whose charter lists it.
- **The charter decides who approves; `persons.json` does not.** A sensitive or irreversible grant counts only if its key is an admin key the pool's charter lists, at the counted level of its class (3.4), computed from
  the charter and the acts. `persons.json` is a cache of the charter for lookups, written by the node, and a row in it lists no key for any pool: a node that enrols a key of its own there adds no approver.
- The typed-yes grant key of `board/person_key.py` stays: one, `weak`, for developers, in `<state>/person`. It counts for nothing in a pool.
- The recovery key has no claim, no voucher and no level; its power is its role (phone-free 2.4), and its scrypt salt holds the pool id, so a phrase belongs to one pool.
- Approvals cost what phone-free 2.4 says: the card's one prompt is the ticket for every payload it names, whichever key signs it.

### 2.4 Data and where it lives

```
<state>/custody/                       the custody secret (secret.wrapped | secret.enc | secret), custody.json {custody, check}, reseal.json while a re-seal runs
<state>/pools/<id>/key.wrapped         the pool key's seed: ChaCha20-Poly1305 (`ring`) under HKDF("poolhouse/pool-key-wrap/v1", secret, slot); the slot id is in the header
<state>/pools/<id>/cert.der            its self-signed certificate (public)
<state>/pools/pending-<slot>/          the same files for a pending key; rewritten and renamed to <id> when the pool id is bound
<state>/person/pools/<handle>/         one SigningKeys for each admin key, purpose "pool-key"; <handle> is 32 random hex digits made with the key
<state>/person/aside/<handle>-<ms>/    a destroyed admin key for 7 days (2.6)
<state>/persons.json                   rows gain `pool`
```

- **A pool key's file is bound to its pool, slot and certificate.** The associated data of the AEAD is the pool id (empty while pending, rewritten when bound), the slot and the certificate's fingerprint, and the node
  refuses a key whose public value is not `cert.der`'s. A program of the account that swaps two pools' key files makes them unreadable, and links nothing. A re-seal under a new secret writes each file to a temporary name, renames it
  atomically and records progress in `reseal.json`; a crash leaves each file under exactly one secret and the walk resumes at the next start.
- **An admin key is sealed under its handle.** Its keystore account is `pool-key-` and the handle; no path enters it (today's `onboard-signing-` account hashes the directory path, `signing.py:109-110`). A rename of
  the home, a state directory moved to another volume, a reinstall under another path and a Migration Assistant clone to another account open every admin key that the keystore master opens. A copy without the
  keystore master opens none, as before.
- **No file of an admin key exists.** `SigningKeys.export` (`signing.py:259`) refuses a pool-key directory. A key reaches another computer by the live transfer of 2.8, by rotation (2.6), or by the pool's 24 words.
- No file lists pools beside their keys: the signer finds the admin key of pool P as the handle whose public key P's charter lists. `<state>/person` is sandbox-denied (`docs/agent-sandbox.md:38`). `custody.json`'s `check`
  is an HKDF of the secret under its own domain, so a directory given another secret is refused as the device key is now (`device.rs:121-125`). The node never touches the operating system's keystore.

### 2.5 The custody secret follows its custody

`Node.key_binding` and `KeyBinding` are deleted. The delegate rule reads the custody of the secret that wraps the pool key: `Custody::Keystore` may sign a delegate admit; `Passphrase` and `File` may
not (`Custody::weak`, `device.rs:51`). `custody` is already set in `Node::open_with` from `device::load`, so the rule is two lines where the second enum is read (`ops.rs:260,313`) and `rig_bind_device_key`
(`app/poolhouse-node/tests/gov_net.rs:412`) becomes a rig setter for `custody`: the "about two lines" of phone-free 2.3.

Said exactly: `Keystore` custody means the secret is sealed at rest under the keystore master and opens for this account on this computer, and it is the starter's word: the record is what the process that started the node
says (`Supplied.custody`), so any process of the account can start the node over a file-held secret and name `Keystore`. The rule is therefore not a check; the card says "Studio says it keeps its key in the keychain",
and the delegate's rates and removal bound it (phone-free D3). The device records its custody in a self statement (3.2, `subject: device`), and a delegate admit from a device whose newest self statement says `file` or
`passphrase`, or that has none, counts for nothing on any member.

**The node keeps the secret only for one operation.** The supervisor hands the secret over standard input at start. The node derives each open pool's subkey, opens the pool keys and zeroizes the secret and the subkeys (`zeroize` on drop); for a
later operation that needs it (a pending key, a bind, a re-seal) the node writes one request line to the supervisor on the same channel, receives the secret, uses it and zeroizes it. While the node runs, memory holds the open pools'
pool keys, which must sign, and nothing that opens a file: a dump or log line of the running node reaches those keys (up to 16), not the secret and not a closed pool.

### 2.6 The life of the keys

| Event | What happens | Added to the person's steps |
|---|---|---|
| Found | The draft makes a pending pool key; its certificate is the charter's `first.cert`. The card's tap and one prompt make the admin key and the ticket signs the bundle (acceptance, `pool_create`, delegate). The act that lists the key carries the first device's signature as its voucher (3.2). `pool_found` binds the pool key to the id. The card offers the 24 words of the recovery key (one tap, skippable, never a blocker; the first recovery key is held 7 days, phone-free 2.4) | none |
| Join | The join starts with a pending pool key, bound when the id arrives; no admin key is made. A failed or abandoned join destroys the pending key; one hour at most | none |
| Make this computer an admin of a pool | A card and a prompt make a new admin key; `admin_add` carries its claim and voucher, is accepted by the key, carries the voucher's device signature, is signed by the current admins and held as phone-free 2.6 says | as phone-free |
| Leave | The device signs `leave` and destroys the pool key at once (multi-pool 3). If the pool lists this computer's admin key, the card proposes `admin_remove` of it with the leave and the key is moved aside when that takes effect. If it is the only admin, the card says the pool can never change after this and offers **Leave anyway**, which asks the same prompt as a governance act and then moves the key aside | one line on the leave card; the prompt only for **Leave anyway** |
| Removed by others | The signed removal destroys the pool key; the admin key stays listed. The app says "You were removed from 'home'. This computer's key for it is still listed. [Destroy it]". A key still listed is destroyed with the ticket prompt, because it ends that key's signatures; a key no longer listed is moved aside for 7 days with no prompt and a banner on this computer that restores it, then deleted | a prompt only for a listed key |
| Rejoin | A new pool key and certificate (D7); the old certificate is `left` or `removed` in the roster for ever. An admin key still listed with the old certificate as voucher has no basis (its voucher left) and counts `weak` for ordinary acts until `revouch` names a device that holds it (3.7); a leave that removed it leaves nothing to re-vouch | none |
| Recover | The 24 words make that pool's recovery key; `recover` names a new admin key, a new handle on the recovering computer (a computer new to the pool joins first, as any device does). Other pools' keys and phrases are untouched | as phone-free 2.4 |
| Replace an admin key (rotation) | A new handle and key, `admin_add` of it, then `admin_remove` of the old, then the old is moved aside for 7 days. One pool only; two governance prompts | the card lists both |
| Move to a new computer | The live transfer of 2.8: every admin key of this computer, under its handle, to the new one; no file | one prompt, one code |
| A lost computer | From a surviving key-holder, one card for each pool names the removal of the device, the `revouch` of every key it vouched to a successor the card chooses, and the removal of its admin key. Governance prompts are not bundled across cards and pace at 3 in 10 minutes (phone-free 2.3), so a person in five pools is told first: "3 now, 2 in 8 minutes" | a prompt for each pool |
| An admin key made and never used | Moved aside, then deleted, only when its age on the awake clock (the monotonic clock of `gov/clock.rs`) exceeds the longest `admin_add` hold (720 hours, `charter.rs:300`) plus that act's `not_after` window, and no charter and no act of any held pool names it; the move aside lasts 7 days. A wall clock set forward deletes nothing | none |

"Moved aside" is stated plainly: the sealed directory goes to `<state>/person/aside/` and nothing signs with it; it is deleted after 7 days, and a banner on this computer restores it until then. A copy in a backup is as secret as
the keychain that opens it, which is why a leave also removes the admin key from the charter where it can.

**Backups and restores.** A sealed file opens only with the sealing secret. A copy without the keychain is inert. A Time Machine restore to the same account, or Migration Assistant to a new Mac with the keychain, opens every key
on the clone, whatever path it lands under: a second device with the same certificates in every pool. Slice 0 is what detects it: each pool's heartbeat shows a counter going backwards or two boot ids under one fingerprint to the
admins, who may `distrust` the device, remove it, or replace that pool's admin key; until slice 0 has landed no clone is detected. A restored pool key of a pool the device left carries a certificate that is `left` in the roster for
ever: it signs nothing any member counts, and a rejoin makes a new one. A voucher restored from an older backup has numbers behind its pool's statements, so it signs no statement until it has synced and read the highest number
the pool holds for that key (3.2). A restore after which the original is gone is the owner's own move and no member can tell it apart. The secret is re-sealed as 2.4 says.

### 2.7 What two pools reveal

| Item | In pool A and pool B | Linkable by an observer in both |
|---|---|---|
| Pool keys, certificates, fingerprints, heads, beacons | independent keys (multi-pool D3) | no |
| Admin keys | independent keys | no |
| Recovery keys | a phrase for each pool, the pool id in the salt | no |
| Approval signatures on boards | the pool's own admin key | no |
| Statement voucher and numbers | that pool's certificate; numbers for each (voucher, key, listing) | no |
| `first.name`, `person`, `label` | `first.name` is the computer's name; `person` and `label` are what the person gave (default `owner`) | yes by name if the defaults stay (multi-pool Q1); the card lets the person rename a pool's names |
| An Android chain | stays with the voucher; no statement and no act carries it | no |
| Source address and port, the `hello` hardware line, timing | one listener | yes on one network (multi-pool 2.3) |

### 2.8 Moving admin keys to a new computer

A transfer, not an export: no file of a key is made, so none can be copied, mailed or restored later.

1. On the old computer a card says "Move this computer's keys for 'home' and 'lab' to a new computer" and takes one prompt, the ticket for every key it names.
2. The new computer opens "Receive keys" and the old shows an eight-word code (88 bits). The two run the join-code exchange (SPAKE2, `join_code.rs`) over the local network with both certificates' fingerprints as identities, so a
   listener on the network learns nothing it can guess offline; three wrong codes or ten minutes end it.
3. The old computer sends each admin key's seed inside the channel. The new computer seals it under its own keystore as `pool-key-<handle>`, with the handle unchanged, and answers with a receipt that names the public keys.
4. On the receipt the old computer marks each key `moved`: it signs nothing again and its sealed directory goes aside for 7 days (2.6). Between the receipt and that mark both computers hold the key and only the new one is told it may sign.
5. Pool keys do not move. The new computer joins each pool as a new device (multi-pool 4), and the person removes or leaves with the old one when they choose. A charter changes nothing, because the admin keys keep their public values.

When the old computer no longer exists the way back is rotation from any surviving key, then the pool's 24 words.

## 3. B. Custody statements

### 3.1 The claim, the custody, and who raises a level

A claim says how a key is held, in a closed vocabulary. The level is a function of the claim, computed by every member from one table, so they cannot disagree and a caller cannot name a level
(`app/poolhouse-node/tests/gov_strength.rs:58` keeps refusing a draft that names one).

| `method` | `prompt` it needs | `level_of` | Meaning |
|---|---|---|---|
| `phone-keystore` | `biometric` | `phone` | a phone's Keystore key, authentication for each use |
| `helper` | `os-dialog` or `biometric` | `native` | a protected helper under another operating-system identity (phone-free slice 8) |
| `keystore-prompt` | `os-dialog` or `biometric` | `computer` | a file sealed under the keystore, made through the operating system's prompt for this key |
| `keystore-typed` | `typed-yes` | `weak` | sealed under the keystore, made by a typed yes |
| `file` | `typed-yes` or `none` | `weak` | a passphrase file or a plain file |

`os` is `android`, `macos`, `linux`, `windows` or `other`, for words. A claim whose `prompt` does not fit its `method`, or a word outside the vocabulary, is not a claim. A device's own custody has its own table:

| `custody` | Meaning | Delegate admit |
|---|---|---|
| `keystore` | the custody secret is sealed under the keystore master | may sign |
| `passphrase` | under a passphrase | counts for nothing |
| `file` | a plain file | counts for nothing |

**Two claims, one rule.** A key's charter row holds a claim the admins signed and the key accepted; the voucher's statement holds a claim the voucher signed alone. The counted level is the lowest of `level_of(row.claim)`,
`level_of(statement.claim)` and the cap of the basis (3.4). **A statement can only lower the level or restate it up to the row's; it never raises it.** Raising a key is an admin act: `admin_add` of the listed key with a new claim under
the key's fresh acceptance, which takes a card, a prompt and the governance holds as a first listing does. A listing that changes a row's acceptance ends every statement made for the old one (3.2).

### 3.2 The statement, and where it lives

A key's charter row gains `claim` and `voucher` (the certificate fingerprint of the one device that stands behind the key; at founding the first device) and loses `strength` and the phone-free `assurance`. The key's acceptance covers
both, and the act that lists the key (`pool_found`, `admin_add`, `revouch`) carries the voucher's device signature beside the admins', so a key is listed only with its claim and voucher, by its holder, with the voucher's agreement.

A **statement** is the act `vouch`, with one device signature (`SigKind::Device`) of the voucher's pool key. The act envelope binds the pool, epoch, `prev`, a nonce and `at_ms`, so a statement is bound to the pool and a time
(`gov/act.rs:16,80-84`); nothing signs the pool id twice.

```
vouch body   {"subject": "key", "key": "<public key hex>", "row": "<hex digest of that key's acceptance>", "seq": n, "claim": {"method": ..., "os": ..., "prompt": ...}}
             {"subject": "device", "key": "<the device's certificate fingerprint>", "seq": n, "custody": "keystore" | "passphrase" | "file"}
distrust, trust body   {"device": "<fingerprint>"}
revouch body           {"key": "<public key hex>", "voucher": "<fingerprint of the successor>"}   with the key's fresh acceptance and the successor's device signature
```

- **Who may sign.** Only the device the key's row names, for that key, and only while it is an active member; a `subject: device` statement only by the device, about itself. A statement by another device, about a key the
  pool does not list, or naming a `row` that is not the key's current acceptance counts for nothing and is refused at ingest.
- **`seq`.** One counter for each (voucher, subject, row), starting at 1, that the voucher signs and persists before it sends. The statement that stands is the one with the highest `seq`, never the one that arrived last
  and never the one with the later `at_ms`. A higher `seq` must carry a `prev` no older than the lower one's. Before it signs, a voucher syncs the pool and takes a number above the highest it holds; offline, or restored
  from a backup behind its pool, it signs nothing.
- **When it is made.** Every 30 days, and whenever a live fact changes, the device re-derives each claim from live facts (the key is still in the keystore, the custody is unchanged) and signs a statement only if the result differs
  from the newest: lower, or back up to the row's level when the fact recovered. No prompt, no tap.
- **How many stand.** The newest two for each (key, row), by `seq`; older ones go by the pool's room rules (`gov/room.rs`). A device holds at most 32 `vouch` acts. One statement for a (voucher, key) is accepted for each hour of
  `at_ms` after the last; the rest are refused at ingest and not stored. A proof of equivocation (3.6) is kept outside these caps, one for each voucher. They are device acts, so the 256 admin-set cap is untouched.
- **What a statement carries.** No evidence and no chain: it is at most 1 KiB. The Android chain stays with the voucher, which checks it at the scan against its own roots and policy and states the result as the claim (3.5).
- **Time.** A statement dated more than five minutes ahead of the receiver's clock is refused at ingest (the act rule, `docs/node.md` "Who governs a pool"). That is the only place a receiver's clock is read; the fold (3.4) reads none.

### 3.3 What a member checks

On ingest, from bytes only: the act's shape and size; the device signature against the voucher's certificate; that the voucher is the key's listed voucher for that `row` and an active member; the `seq` rule (a number
already held with other bytes goes to 3.6; a higher number with an older `prev` goes to 3.6); the hourly gap; the caps; the skew. Nothing is fetched and no list of roots, policy or revocations is read. No member reads a `basis` or `level`
field; there is none.

### 3.4 The fold, basis and the counted level

Every question about a level is asked at a **position**: the charter state named by an act's `prev`. An act's signers are counted at the act's own position, from the acts the member holds, in chain order. The fold is a function of the
act set; it reads no arrival order, no `at_ms` of a statement and no clock. Two members holding the same acts compute the same levels, a joiner replaying history reproduces the verdicts of the old members, and a member that is
missing acts agrees when it has them.

| Input at position p | Where it comes from |
|---|---|
| the key's row | the charter in force at p |
| the statement | the highest `seq` for (the row's voucher, the key, the row's acceptance) whose `prev` position is at or before p; none if there is no such statement |
| the voucher's standing | `good`, `set_aside` or `distrusted` from the acts at or before p (3.6) |

| Basis | When a member computes it | Cap |
|---|---|---|
| `vouched` | the row's voucher is an active member in good standing at p | `computer` |
| none | the voucher has left, was removed, set aside or distrusted at p, or the key is a recovery key | `weak` |

- C1. **Counted level = the lowest of `level_of(row.claim)`, `level_of(statement.claim)` (when a statement exists) and the cap of the basis.** Phone-free R1 to R6 use the counted level wherever they say level.
- C2. **An act keeps the verdict of its position.** When a hold ends, the act's signers are counted again at the act's position, not at the end of the hold. A statement, a departure or a change of standing that comes later
  never cancels an act that counted at its position; an admin `cancel`, as today, or a removal with its own threshold, does. A statement changes only the keys its voucher vouches, so it can take away only that voucher's own keys'
  signatures, never another voucher's.
- C3. **Cure acts count the keys of a voucher whatever the voucher says.** A key vouched by D counts at the lower of `level_of(row.claim)` and `computer` for exactly these acts, ignoring D's statements, standing and presence:
  `member_remove D`, `revouch` or `admin_remove` of a key D vouches, and a re-listing of such a key. It counts for no other act. The holds of the class apply (7 days when only `computer` keys sign).
- C4. An act counts a signer if its counted level meets the floor at the act's position.
- C5. No fold removes a key from the charter. A key that counts nothing stays listed, so a statement can never empty a pool; the way back from a pool with no counting key is `recover`.
- C6. A claim of `phone` or `native` with no checkable evidence counts as `computer`, and no evidence exists in a statement, so no listed key holds `phone` or `native`. A floor above `computer` is refused by phone-free R4
  ("refused when no listed key holds the new level"), and the person is told in the same sentence (3.8).

### 3.5 What can and cannot be proven

"Vouched" means one sentence: the device that holds or enrolled the key says how it is held. Software on a computer cannot prove operating-system custody to another member, because the member would have to trust the
program that reports it. What exists on each platform, and what this design does with it:

| Platform | Where the key sits | What the vouching device itself learns | What another member can check | Basis | Highest counted level |
|---|---|---|---|---|---|
| macOS | a file sealed under the login-keychain master | the system's administrator dialog was answered for this key (`presence/mod.rs`); the signer's report of where it stores the key | nothing | vouched | `computer` |
| macOS Secure Enclave | not used: it needs a signed app with a keychain entitlement and a paid developer account (`docs/pool-encryption.md` 8.1) | n/a | n/a | none possible now | n/a |
| Windows | the account's credential store through `keyring` | no prompt exists: the first key is refused (`presence/mod.rs:224`) | nothing. TPM key attestation with the maker's chain exists in principle (`docs/pool-encryption.md` G5) and is unbuilt | none | `weak` until a prompt exists, then `computer` |
| Linux | the Secret Service through `keyring`, else the passphrase file | the polkit dialog (`pkexec`) was answered | nothing. TPM 2.0 certify is the counterpart of the Windows case, unbuilt | vouched | `computer` with the keystore and the dialog; `weak` with the passphrase file |
| Android phone | Keystore, StrongBox where present; a new fingerprint destroys the key | the chain of the key, shown at the scan, which the voucher checks with `phone::attest::verify` under its own roots, policy and revocation list: hardware level, P-256, sign only, per-use authentication, locked verified boot, app id, version floor, challenge | nothing: the chain is in no statement | vouched | `computer` |
| Native helper (phone-free slice 8) | another operating-system identity | the helper's own statement | an evidence kind reserved for slice 8 of phone-free, with the rules of 3.9 | vouched until defined | `computer` until defined |

A voucher states `phone-keystore` only for a chain its own `phone::attest::verify` accepted (today every verification is refused, the publisher policy being `None`), and otherwise states `keystore-prompt`; both count `computer`,
so the choice changes the words and no level. Because the voucher checks the chain, the voucher's own revocation list is what lowers a phone's claim when its attestation key is revoked, at the next 30-day re-derivation. A chain
does not prove that the finger is the person's, that the phone's trusted environment has no flaw, or the vendor's honesty; and it says nothing about the computer a node runs on.

### 3.6 A lie, and standing

Only the device the listing names can vouch, and the key accepted that listing, so a lie is one device describing a key it holds or enrolled. Two cases:

- **Provable from bytes.** Two statements the same voucher signed for the same subject and row with the same `seq` and different bytes (an equivocation), or a higher `seq` with a `prev` older than a lower one's. Each is a valid
  act; together they are a proof every member checks from the two acts and nothing else. The voucher's **standing** is `set_aside` from the position of the later `prev` of the two, on every member that holds them. A relayer
  can delay a proof but not suppress it: both statements are acts like any, synced to every member.
- **Not provable.** A device says `keystore-prompt` for a key in a file. No member can know. The cap makes it harmless where it is dangerous: the key counts as `computer`, with the 7-day holds phone-free 2.6 puts on `computer`
  keys, and cannot count as `native` or `phone`. It can do what a software key claimed as `computer` could do.

Standing is `good`, `set_aside` or `distrusted`. For a device not in good standing every statement it signed reads basis none (its keys count `weak` for ordinary acts, its self statement included), its delegate admits count for nothing,
and the cure acts of C3 still count its keys. It stays a member and its data acts are untouched.

- **`distrust {device}`** is a sensitive-class admin act: it needs the sensitive threshold, and keys the target vouches do not count toward it.
- **`trust {device}`** is a governance-class admin act with the threshold and the hold. It lifts a `distrust`. It never lifts `set_aside`: a set-aside voucher is cleared only by removing the device and admitting it again under a new
  certificate, a new voucher identity.
- Standing is evaluated in chain order and an act counts with the standing in force at its own position, so a `distrust` signed by a key that the target vouches is excluded rather than circular, and alternating `distrust` and
  `trust` is limited by the thresholds and holds of both.

The pool header says "'Studio' is not trusted to vouch for keys. [Remove] [Trust again]"; every member gets a banner and a notification and the entry `custody.standing` is audited. Removal stays the admins' sticky `remove`; this is a step short of it.

The cost of a lie is: the keys it vouched stop counting for ordinary acts, its delegate power stops, the pool is told, and its name sits on the signed statements. A lie nobody can prove costs the liar only what the cap already takes; this design
says so rather than promising a penalty it cannot apply.

### 3.7 A voucher that goes: successors and `revouch`

A key whose voucher has left, been removed, set aside or distrusted has basis none and counts `weak` for ordinary acts. That is repaired by naming a new voucher, never by listing the key afresh:

- **`revouch {key, voucher}`** is a governance act. It carries the key's own fresh acceptance (the row with the new voucher), the new voucher's device signature, and the admins' signatures counted by C3 and the class's threshold. The new
  voucher is an active member device; it makes the first statement for the new listing, numbered from 1.
- **A removal names successors.** The card for removing a device lists every listed key that device vouches and a successor for each (a device of one of the card's signers); its one prompt is the ticket for the `member_remove`, every
  `revouch` and the keys' acceptances. The keys count at their level without a gap.
- **A device that leaves** cannot name successors. Its keys read none until a `revouch`, and meanwhile C3 lets those keys sign that `revouch` and the removal of the departed device. A voucher that leaves therefore zeroes no one's
  ability to act; it costs the pool the hold of a governance act (7 days when only `computer` keys count). The lost-computer card of 2.6 is this act for a device that cannot be asked.
- A statement or a change of standing by one voucher never cancels an act that keys of another voucher signed (C2).

### 3.8 What the person sees, and no silent downgrade

The words come from the fold at the moment of reading; no sentence is stored. The `LEVEL:` line of phone-free 2.7 gains the basis (appended after the cut and signed with the text). **The signer recomputes the line from bytes**: the node
gives it the charter chain and the statements, and it runs the fold itself (`gov/custody.rs` is built as a small crate, `app/poolhouse-custody`, that the node and the signer both link) after verifying every signature in them; it
never asks the node for the node's own answer. A signer that cannot verify what it was shown, or whose line differs from the one it was given, refuses to sign.

| Counted | Basis | Claim | Words on the card and in the header |
|---|---|---|---|
| `computer` | vouched | `keystore-prompt` | "this computer ('Studio' says so; nothing else can check it)" |
| `computer` | vouched | `phone-keystore` | "your phone, as 'Pixel' says; this pool cannot check it, so it counts as a computer and big changes wait 7 days" |
| `computer` | vouched | `helper` | "this computer, protected, as 'Studio' says; this pool cannot check it" |
| `weak` | vouched | `keystore-typed`, `file` | "a typed yes or a file; it counts for nothing" |
| `weak` | none | any | "a key nobody stands behind ('Studio' is not trusted to vouch, or has left)" |

- Any change of the counted level or the basis of any key since the person last saw it (a statement lowered or restated, the voucher removed, set aside or distrusted, a key re-listed) is a banner and an audit entry
  `key.level_changed` naming the key, the old and new words and the cause. Operating-system notifications on member devices are coalesced for each voucher for each day; the audit entries are not.
- A card for an act that lists a key which counts less than it did says so before the prompt: "After this, 1 of 2 keys stops counting" (`describe`, from the fold). Removing a voucher lists the keys that stop counting.
- The pool header reads "Approved from: this computer ('Studio' says so)" with **Add a phone** beside it and, for a phone key, the line above. No setting hides a basis.

### 3.9 If an evidence kind is added later

A statement has no evidence field. A later kind (the native helper's, a TPM certificate, a chain carried in a statement) needs a design of its own and must keep these rules:

- Acceptance is structural and deterministic from the bytes in the act: signatures in the evidence, the evidence's key equals the statement's key, the challenge matches the key, the voucher and a value the voucher chose. No clock of the receiver
  and no fetch enter it, and the key's age is read from the evidence, never from `at_ms`.
- Only a contradiction inside those bytes that no policy changes (a signature that fails, a key that is not the statement's, a challenge that does not match, malformed bytes) sets a voucher aside. An unknown root, an old version
  floor and a revocation make the key unattested, which caps it at `computer`; they never judge the voucher.
- A revocation list is a local overlay each member applies, cached by act id, that can only lower a counted level and is a banner like any fall; it is never carried in a statement.

## 4. Formats, versions, migration, costs

- **Charter** `v` 5: each admin key gains `claim` (null for a recovery key) and `voucher` (empty for a recovery key) and drops `strength`. Version 4 is phone-free slice 1 (`floors`, `assurance`, `counting(class)`); this design replaces its per-key
  `assurance` by `claim`. If that slice has not landed when slice 4 here starts they land as one version, the next integer; the field names do not change. The pool id hashes the charter with acceptances blanked, so the claim and voucher are in the id.
- **Acceptance** `poolhouse/pool-accept/v3\n` covers `claim` and `voucher` besides today's fields. **Act kinds** 13 to 16 (`vouch`, `distrust`, `trust`, `revouch`); `poolhouse/pool-act/v2` and act version 2 stay. `vouch` is a device act, taken only from
  the listed voucher. `distrust` is sensitive and `trust` and `revouch` governance. `admin_add` of an already listed key re-lists it and replaces its row. **`pool_status.governance.admins[*]`** gains `claim`, `counted`, `basis`, `voucher`, `seq`;
  `governance.standing` lists devices not in good standing.
- **Where it lives.** Keys: 2.4. Statements: the pool's `acts.json`, folded in `app/poolhouse-custody` (called from `gov/custody.rs` beside `roster.rs`), built from the acts and the roster and never from a row a member sent. Nothing else is written.
- **Migration: none.** A charter of another version is set aside by the existing rule (`membership.rs:48-62`; "this pool was made by another version of Poolhouse and cannot be used: make a new pool"); a pool with no `key.wrapped` is set aside
  as multi-pool section 5 says; the old `device.key*` is not read as a custody secret or a pool key; an admin key sealed under a path-hashed account is not read. No legacy mode, no shim.
- **Fixed costs.** A key is one Ed25519 generation and one AEAD seal; the admin key reuses the master the process already holds (no new keystore item, no extra Keychain dialog, the 5 makes and removals an hour untouched). A statement is one Ed25519
  signature and at most 1 KiB. The fold for an act reads at most 8 keys and their newest statements and is cached by act id. Not measured here; slices 4, 5 and 7 carry tests that bound them. Disk: at most 2 statements for each key.
  Wire: the existing `acts` op; no new message.
- **Onboarding.** No terminal, file, key or port. Founding, joining and approving are as phone-free 2.4 and multi-pool 4. Added: the basis words, one line on the leave card, the banners, the code of the move. No setting, flag or error on a normal journey.

## 5. Build plan

Each slice lands alone, with tests seen to fail first. Slices 0 to 3 need no charter and can land before phone-free slice 1; slice 4 needs it. The node-side slice 2 is what multi-pool slice 3 waits for. Sizes: S under a day, M one to three days, L a week.

| # | Slice | Exists | New | Tests that fail first | Size |
|---|---|---|---|---|---|
| 0 | **The clone heartbeat** | the device key, `gov/clock.rs`, the sync loop, `docs/pools-and-serving.md` 1.6 | a signed `{fingerprint, boot id, counter}` each minute, the counter persisted before it is sent, flagged to the admins when it goes backwards or two boot ids alternate; the fingerprint takes no offers until answered. Slice 2 moves its signer to the pool key | `a_counter_going_backwards_is_flagged_to_the_admins`; `two_boot_ids_alternating_under_one_fingerprint_are_flagged`; `a_flagged_fingerprint_takes_no_new_offers_until_the_person_answers`; `the_counter_is_persisted_before_it_is_sent` | M |
| 1 | **An admin key for each pool** | `SigningKeys`, `Keystore.wrap` with an owner, `person_key.keys` | `PoolKeys` makes a `SigningKeys` in `person/pools/<handle>/` (purpose `pool-key`, account `pool-key-<handle>`); `Admin` is built for a handle; move aside and delete after 7 days; sweep on the awake clock; export refused. `ComputerKey` (phone-free slice 3) signs with a handle through its ticket | `a_new_pool_gets_an_admin_key_no_other_pool_holds`; `two_admin_keys_share_no_public_key_and_no_sealed_bytes`; `a_sealed_file_of_one_pool_does_not_open_as_anothers`; `a_directory_copied_without_the_keystore_opens_nothing`; `an_admin_key_opens_after_the_state_directory_moves`; `an_admin_key_opens_after_the_home_directory_is_renamed`; `the_signer_unwraps_only_the_key_of_the_pool_it_signs_for`; `making_an_admin_key_adds_no_keystore_item_and_reads_the_master_once`; `no_file_of_an_admin_key_can_be_made`; `an_unused_key_is_not_swept_by_a_forward_wall_clock`; `an_unused_key_is_moved_aside_for_seven_days_before_it_is_deleted`; `a_listed_key_is_never_swept`; `a_destroyed_key_is_restorable_for_seven_days` | M |
| 2 | **The custody secret and the pool key** | `device::load`, `Custody`, `cert.rs`, `fsutil::random_hex`, `ring` | `custody/` replaces `device.key*`; a pool key and certificate per pool (pending, bound, destroyed) sealed under an HKDF subkey of the secret with the pool id, slot and certificate fingerprint as associated data; the secret handed for one operation and zeroized; atomic re-seal with progress; `KeyBinding` deleted, the delegate rule reads `Custody`; `rig_bind_device_key` sets `custody`. The heartbeat and the rejoin test need slice 0 and multi-pool slice 1 | `a_new_pool_key_shares_no_public_value_with_another_pools`; `a_pending_key_is_bound_to_the_id_when_the_join_completes`; `a_failed_join_or_an_hour_destroys_the_pending_key`; `a_leave_destroys_the_pool_key_at_once`; `a_rejoin_makes_a_new_key_and_certificate`; `a_key_file_does_not_open_under_another_pools_subkey`; `a_key_file_swapped_between_pool_directories_is_refused`; `a_key_whose_public_value_is_not_the_certificates_is_refused`; `an_interrupted_reseal_leaves_every_file_under_one_secret_and_resumes`; `the_node_holds_no_custody_secret_after_it_opens_the_pools`; `the_custody_secret_signs_nothing_a_pool_sees`; `a_directory_given_another_secret_is_refused`; `a_keystore_custody_signs_a_delegate_admit_and_a_passphrase_or_file_custody_does_not`; `opening_sixteen_pools_unseals_each_key_once`; `app/poolhouse-node/tests/gov_net.rs:412` rewritten | L |
| 3 | **Admin keys belong to a pool** | `Persons`, grants | `pool` on person rows and `MOST_KEYS` for each pool; a pending key counts for one `pool_create` | `a_grant_signed_by_another_pools_key_counts_for_nothing`; `a_pending_key_signs_only_the_pool_create_grant_of_the_pool_it_founds`; `eight_keys_for_each_pool_not_eight_in_all` | S |
| 4 | **Claim, voucher, charter 5** | `AdminKey`, `acceptance_message`, `roster`, `verdict`, `Charter::counting` (phone-free slice 1) | charter 5; acceptance `v3`; `level_of` and the `custody` table; the voucher's signature on a listing; `admin_add` of a listed key re-lists it; approvers are charter-listed keys; `describe` lines | `a_level_field_in_a_draft_is_refused`; `a_charter_with_another_claim_is_another_pool`; `a_listing_without_the_vouchers_signature_is_refused`; `raising_a_key_is_an_admin_relisting_with_the_governance_holds`; `a_relisting_ends_the_statements_made_for_the_old_row`; `a_sensitive_grant_signed_by_a_key_the_charter_does_not_list_counts_for_nothing`; `a_node_written_persons_row_adds_no_approver`; `a_device_statement_uses_its_own_vocabulary`; `an_absent_custody_statement_is_not_keystore_for_a_delegate_admit` | M |
| 5 | **Statements and the fold** | `roster`, `gov/room.rs`, `app/poolhouse-custody` (new) | `vouch`; `seq`; the ingest rules; the fold of 3.4 at a position as a crate the node and the signer share; C1, C2, C4 to C6 | `a_vouched_key_counts_at_most_computer`; `a_claim_of_phone_counts_as_computer`; `a_statement_by_another_device_than_the_listed_voucher_counts_for_nothing`; `a_statement_for_another_pool_is_refused`; `a_statement_naming_another_row_counts_for_nothing_and_is_refused`; `the_highest_seq_stands_whatever_order_the_statements_arrive_in`; `a_relayer_redelivering_an_old_statement_changes_no_level`; `a_late_joiner_and_an_old_member_compute_the_same_levels_from_the_same_acts`; `a_backdated_at_ms_does_not_reorder_statements`; `the_fold_never_reads_the_receivers_clock`; `a_statement_cannot_raise_a_key_above_its_listed_claim`; `a_lowered_statement_counts_for_acts_after_it_and_not_before`; `an_act_valid_at_its_position_is_not_cancelled_by_a_later_statement`; `a_statement_changes_only_the_keys_its_voucher_vouches`; `a_statement_never_removes_a_key_from_the_charter`; `a_statement_dated_ahead_is_refused`; `only_the_newest_two_statements_stand`; `a_statement_for_an_unlisted_key_is_refused_at_ingest`; `one_statement_for_a_voucher_and_key_is_accepted_each_hour`; `at_most_32_vouch_acts_are_held_for_a_device`; `a_voucher_restored_behind_its_pool_signs_no_statement_until_it_has_synced` | L |
| 6 | **A voucher that goes** | `member_remove`, `admin_remove`, the card | `revouch`; C3; the removal card with successors | `a_removed_vouchers_keys_still_count_for_its_removal`; `a_voucher_that_lowers_every_key_it_vouches_cannot_stop_their_removal_of_it`; `a_leaving_voucher_zeroes_no_ability_to_act`; `revouch_needs_the_keys_acceptance_and_the_new_vouchers_signature`; `a_removal_with_revouch_in_one_card_keeps_the_keys_counting`; `keys_of_a_gone_voucher_count_for_no_act_but_its_cure`; `a_cure_act_signed_only_by_computer_keys_is_held_seven_days` | M |
| 7 | **Standing** | `roster` order, `delegate` rules | `set_aside` from a proof, `distrust`, `trust`; delegate admits need good standing and a statement that says `keystore` | `two_statements_with_one_seq_set_the_voucher_aside_on_every_member`; `a_higher_seq_with_an_older_prev_sets_the_voucher_aside`; `a_set_aside_voucher_keeps_its_membership_and_its_data_acts`; `trust_does_not_lift_a_set_aside`; `distrust_needs_the_sensitive_threshold`; `keys_the_target_vouches_do_not_count_toward_its_distrust`; `trust_is_a_governance_act_with_its_hold`; `distrust_turns_its_keys_weak_for_ordinary_acts_and_trust_turns_them_back`; `a_delegate_admit_from_a_device_whose_statement_says_file_counts_for_nothing`; `standing_is_taken_at_the_position_of_the_act` | M |
| 8 | **Words** | phone-free 2.7 `LEVEL:` | the basis words; header; `pool_status` fields; `key.level_changed`; banners; glossary and `docs/node.md`; pool-check steps `custody-vouched-cap`, `liar-voucher-capped`, `two-pools-share-no-key` | `the_level_line_carries_the_basis_and_survives_the_300_character_cut`; `a_signer_recomputes_the_level_line_from_the_acts_and_not_from_the_node`; `a_signer_refuses_a_line_that_differs_from_its_own`; `a_signer_refuses_statements_it_cannot_verify`; `every_fall_of_a_level_is_a_banner_and_an_audit_entry`; `notifications_are_coalesced_for_each_voucher_for_each_day`; `the_words_are_computed_at_reading_not_stored`; `two_pools_on_one_computer_list_no_common_value` | M |
| 9 | **Leave, replace, lost computer** | `pool_leave`, `admin_remove`, phone-free cards | the leave card with the key; replace; the lost-computer list with the pacing line; move aside after removal, with the prompt for a listed key | `leaving_proposes_removing_the_admin_key_and_moves_it_aside_when_that_takes_effect`; `leaving_with_the_only_admin_key_says_the_pool_can_never_change`; `leave_anyway_asks_the_governance_prompt`; `replacing_a_key_adds_then_removes_and_moves_aside_the_old`; `a_lost_computer_lists_one_card_for_each_pool_and_says_the_pacing`; `destroying_a_listed_key_asks_the_ticket_prompt`; `destroying_an_unlisted_key_moves_it_aside_and_asks_no_prompt` | M |
| 10 | **Move to a new computer** | the join-code exchange (`join_code.rs`), `SigningKeys` | the transfer of 2.8: code, channel, receipt, `moved` state, no file | `a_move_transfers_every_admin_key_under_its_handle`; `a_moved_key_signs_nothing_on_the_old_computer`; `a_wrong_code_transfers_nothing_and_three_end_it`; `a_listener_on_the_network_learns_no_seed`; `the_move_asks_one_prompt_for_every_key`; `a_moved_key_opens_on_the_new_computer_under_another_home_path`; `a_move_leaves_no_file_of_a_key_on_either_computer` | M |

Order: 0, 1, 2, 3; then 4, 5; then 6 and 7; then 8; 9 and 10 after phone-free slice 4 (the card screen).

## 6. Attack first

Each row names the section that closes it and the test that fails first. A non-author's break-it pass on this text is owed before slices 2 and 4 are built (AGENTS.md section 1). Each row holds under the owner's order that a pool is never blocked on a phone.

| Case | What stops it | What remains, named | Fail-first test |
|---|---|---|---|
| **Stolen device** (locked, or unlocked with its password known) | A locked computer opens nothing without the keychain. A thief who knows the password opens every key on it, as for any keystore secret (`docs/pool-encryption.md` section 3) | The loss is per computer, so the cleanup is a card for each pool (2.6) at 3 prompts in 10 minutes, said first. The card removes the device and re-vouches its keys to a successor in one prompt (3.7); the thief's lowered statements and leave cannot stop that card (C3) | `a_lost_computer_lists_one_card_for_each_pool_and_says_the_pacing`; `a_voucher_that_lowers_every_key_it_vouches_cannot_stop_their_removal_of_it` |
| **Cloned state directory, or a Time Machine or Migration Assistant restore** | A directory without the keychain opens nothing. With the keychain the clone holds every key, whatever path it lands under: a second device with one certificate in each pool. The heartbeat of slice 0 flags it (counter backwards, or two boot ids) and an admin can `distrust` it, remove it, or replace that pool's admin key. A restored voucher signs no statement before it syncs; a restored key of a left pool is `left` in the roster | The clone works until an admin acts, and is not detected at all before slice 0 lands; the delegate window and the 7-day holds bound it (phone-free section 4, M5). A restore after which the original is gone is the owner's own move | `a_counter_going_backwards_is_flagged_to_the_admins`; `a_voucher_restored_behind_its_pool_signs_no_statement_until_it_has_synced`; `an_admin_key_opens_after_the_state_directory_moves` |
| **A liar vouching a software key as hardware** | Only the key's listed voucher can vouch and the key accepted it, so a member cannot vouch for another's key. A `phone` or `native` claim counts as `computer`; two statements with one `seq` set the voucher aside on every member; an admin can `distrust` any voucher | A software key claimed as `keystore-prompt` counts as `computer` and nothing proves otherwise. It cannot count as `native` or `phone`. Said in 3.5 and the words of 3.8 (H2 stays true for `computer`) | `a_vouched_key_counts_at_most_computer`; `two_statements_with_one_seq_set_the_voucher_aside_on_every_member` |
| **Cross-pool replay** | A statement is an act: pool, epoch, `prev`, nonce, `at_ms` and the voucher's signature are in its signed bytes; the voucher's pool key is that pool's only; it names the key's acceptance as `row`. The recovery salt holds the pool id | A holder who lists one phone key in two pools links them by its own choice; a phone makes a key for each pool | `a_statement_for_another_pool_is_refused`; `a_statement_naming_another_row_counts_for_nothing_and_is_refused` |
| **Replay or reorder by a relayer** | The statement that stands is the highest signed `seq`; the fold reads no arrival order, no statement `at_ms` and no clock, and an act is counted at its own position | A member missing acts differs until it has them, as for every fold | `the_highest_seq_stands_whatever_order_the_statements_arrive_in`; `a_late_joiner_and_an_old_member_compute_the_same_levels_from_the_same_acts` |
| **A downgrade or a raise of the stated level** | A statement lowers or restates up to the listing and never raises; raising is a re-listing with a card, a prompt and the governance holds. A lowered statement counts for later acts only; a later statement never cancels an act that counted at its position | A compromised voucher can lower its own keys to `weak` and so stop them counting for ordinary acts: the cure acts still count them (C3), and a pool with no counting key uses `recover`. The same voucher could always refuse to sign | `a_statement_cannot_raise_a_key_above_its_listed_claim`; `raising_a_key_is_an_admin_relisting_with_the_governance_holds`; `an_act_valid_at_its_position_is_not_cancelled_by_a_later_statement` |
| **A voucher that leaves or is removed** | Its keys count for the cure acts and a removal names successors in the same card; `revouch` carries the key's acceptance and the successor's signature | The hold of a governance act (7 days when only `computer` keys count) before ordinary acts count the keys again, when no successor was named | `a_leaving_voucher_zeroes_no_ability_to_act`; `a_removal_with_revouch_in_one_card_keeps_the_keys_counting` |
| **`distrust` and `trust` abuse** | `distrust` needs the sensitive threshold and the target's keys do not count toward it; `trust` is governance class with the hold and never lifts a set-aside; standing is taken at the act's position | A pool of one key has one signer for each | `distrust_needs_the_sensitive_threshold`; `keys_the_target_vouches_do_not_count_toward_its_distrust`; `trust_does_not_lift_a_set_aside` |
| **Approval fatigue** | Statements need no prompt, tap or card, so nothing asks the person. One statement a voucher and key an hour is accepted, at most 32 are held for a device, a statement for an unlisted key is refused at ingest, and notifications are coalesced for each voucher for each day. The new cards (leave, replace, distrust, move) take the per-kind cooldown, the ask limit and the prompt pacing of phone-free 2.3 | A lost computer in many pools is a queue of prompts, said before it starts. Not bundled, because a removal is sensitive | `one_statement_for_a_voucher_and_key_is_accepted_each_hour`; `at_most_32_vouch_acts_are_held_for_a_device`; `notifications_are_coalesced_for_each_voucher_for_each_day` |
| **A compromised or malicious member** | It cannot vouch for a key it is not the voucher of, cannot `distrust` (an admin act), cannot raise a counted level above its listing, cannot fill the act table with statements, and takes away only its own voucher's keys' signatures. It holds no key of another pool | It can lie about its own keys (the liar row) and withhold relayed acts as in any pool | `a_statement_changes_only_the_keys_its_voucher_vouches`; `at_most_32_vouch_acts_are_held_for_a_device` |
| **A malicious agent, or one injected without knowing** | No call takes a claim, a level or a statement: the device writes them from its own facts. An agent cannot tap, cannot answer the prompt, cannot read `<state>/person` (sandbox `denyRead`) or `<state>/custody`. A board message that says "vouch as hardware" has no path to a statement. A node-level agent that writes `persons.json` adds no approver: the charter decides | An agent with the account's rights outside the sandbox can write `persons.json`, which is a cache | `a_node_written_persons_row_adds_no_approver`; `a_level_field_in_a_draft_is_refused` |
| **Stolen phone or key** | A phone key signs only with the person's finger; a new fingerprint destroys it. The voucher's word is all a member has about it | A stolen unlocked phone and its owner's finger, as phone-free 2.1 | `a_claim_of_phone_counts_as_computer` |
| **Takeover by whoever is merely present** | Presence gives no key. A new admin key needs the prompt answered; a statement needs the listed voucher's pool key; destroying a listed key and **Leave anyway** need the ticket prompt; any other destroy is a 7-day move aside | Someone at an unlocked computer who knows its password: the stolen-device row | `leave_anyway_asks_the_governance_prompt`; `destroying_a_listed_key_asks_the_ticket_prompt` |
| **Copied or cloned pool** | The pool id hashes the charter with claims and vouchers, so another claim is another pool; `founding_of` refuses a charter that names another computer first (`ops.rs:178-180`) | None new | `a_charter_with_another_claim_is_another_pool` |
| **Forged or below-threshold signature** | Each member recomputes the basis from the bytes and the roster; a forged statement fails the voucher's certificate; no field states a level; a listing needs the voucher's signature | None new | `a_listing_without_the_vouchers_signature_is_refused`; `a_statement_by_another_device_than_the_listed_voucher_counts_for_nothing` |
| **A leak of the custody secret, or a swapped key file** | The node holds the secret for one operation and wipes it, so a dump at rest holds none; a key file's associated data is the pool id, slot and certificate fingerprint, so a swap is refused | A memory read of the running node reaches the open pools' keys (2.2); custody is the starter's word (2.5) | `the_node_holds_no_custody_secret_after_it_opens_the_pools`; `a_key_file_swapped_between_pool_directories_is_refused` |
| **A move to a new computer or a renamed home loses the keys** | An admin key is sealed under its handle, never a path; the live transfer moves every key under its handle with one prompt; no file exists | A computer lost before any move: rotation from a surviving key, else the 24 words | `an_admin_key_opens_after_the_home_directory_is_renamed`; `a_moved_key_opens_on_the_new_computer_under_another_home_path` |
| **A wall clock set forward or a long offline laptop** | The sweep reads the awake clock, waits the longest hold and the act's `not_after`, and moves a key aside for 7 days first | A key abandoned and swept is gone: it was listed nowhere | `an_unused_key_is_not_swept_by_a_forward_wall_clock` |
| **The level line from the node alone** | The signer verifies the charter chain and the statements and runs the fold itself, in the same crate as the node | A signer shown an old consistent chain shows an old consistent line; the pool's freshness is the sync's | `a_signer_recomputes_the_level_line_from_the_acts_and_not_from_the_node` |
| **Secure to open** | Unchanged: the policy is in the charter | The unauthenticated `hello` still returns the policy of a `secure` pool and nothing else (multi-pool 2.3) | none new |

## 7. Decisions made

Each with the option it rejects:

- **K1. Independent random seeds for both keys, not a derivation** (2.2). Rejected: HKDF from the device key (the node would hold a person key and one leak is every pool; no pool id at creation; a rejoin repeats the key) and from a separate root.
- **K2. The admin key is the person key of its pool**, not one approval key for the device. Rejected: a device-wide approval key beside admin keys, because a grant entry names its key on a board that more than one pool may hold, and a shared key links the pools.
- **K3. The claim is stored and the level is computed.** Rejected: storing the level beside the claim, which lets the two disagree and lets a caller name a level.
- **K4. Vouched caps at `computer`, including a founder's own `phone` claim.** Rejected: letting a founder's unchecked claim count at `phone` in a pool of one, because the next device to join cannot check it either.
- **K5. The Android chain stays with the voucher; no statement carries it.** Rejected: a chain in each statement, which lets every member check the phone's hardware and lets `phone` count, and costs unlinkability, because a factory-provisioned phone's
  chain may carry a per-phone certificate (`attest/SOURCES.md` names a serial-number attribute on the intermediate); and it needs members to agree on roots, policy and revocations, which differ by build. The cost kept: `phone` never counts above `computer`.
- **K6. A statement lowers or restates; raising is an admin re-listing.** Rejected: letting the voucher's pool key, which the networked node holds, decide at what level an admin key counts.
- **K7. A level is counted at the position of the act, from the act set.** Rejected: counting by arrival order, `at_ms` or the receiver's clock, which gives two members two answers.
- **K8. A voucher that goes leaves its keys able to cure it.** Rejected: weak keys with no way back but the 24 words.
- **K9. A provable lie is a contradiction between two signed acts and sets the voucher aside mechanically; an unprovable one is only capped.** Rejected: promising a penalty for a claim no one can check, and setting a voucher aside on a root or policy a build differs on.
- **K10. A clone is the heartbeat's notice and a possible `distrust`, not a hold on its powers.** Rejected: suspending a restored device until an admin taps, which puts a step on the normal move to a new Mac and stops nothing a password-knowing thief could not redo in a pool of one.
- **K11. No file of an admin key; a move is a live transfer under a code.** Rejected: an export file (a new leak that restores and syncs), and a path-bound seal (which loses every key on an ordinary move).

## 8. Documents that change

`docs/design-phone-free-pools.md` (2.1 and 2.2: the level is computed from a claim and a basis; 2.3: the "one pool key per computer" paragraph becomes the admin key of this design, and the L2 row in section 4 is closed; slice 1 stores `claim` in place of
`assurance`; slice 2's rows move to `persons.json` with `pool`; slice 5's `key_binding` line becomes slice 2 here; R1, R4 and R5: no listed key holds `phone` or `native` while vouched caps at `computer`, so a floor above `computer` is refused until an evidence kind exists);
`docs/design-multi-pool-devices.md` (2.3 points here for the keys, and "If the keys document differs" is closed; its slice 3 needs slice 2 here);
`docs/pools-and-serving.md` (4.1 and M1 are replaced by multi-pool D3; 1.6 the clone heartbeat is slice 0 here); `docs/glossary.md` (add **claim**, **voucher**, **custody statement**, **basis**, **standing**, **pool key**, **custody secret**);
`docs/node.md` "Who governs a pool" (charter 5, the four kinds, acceptance v3, `app/poolhouse-custody`) and "Key custody"; `docs/phone-approval.md` ("Who holds what": a Keystore key per pool whose chain the enrolling device checks, not a mark);
`docs/pool-encryption.md` section 10, slice 3 (`device.key*` becomes the custody secret); `docs/pool-check.md` (the three steps); `HANDOFF.md` (the pending items as each slice lands).
