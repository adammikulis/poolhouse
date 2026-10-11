# Compromise containment: a pool that assumes its systems are breached

Design, 2026-10-11, revised the same day after a non-author attack review that returned BUILD-WITH-FIXES (section 17.1
lists each of its 11 findings, where it is fixed, the test that fails first and the mutation that turns it red). Nothing in
this document is built. Owner order of 2026-10-11: harden the pool against a compromised system. The other designs defend a pool whose devices, phone and agents behave; this one assumes each of them in turn does
not. For each tier of compromise it says what the attacker gets today (read in the code at `15a47043d`, or marked as read
only in a document), the gap, the defence, the slice that builds it and what it costs the person.

It does not rebuild what exists. Governance, holds, delegates, reissue, routing probation and serving privacy are
`docs/pools-and-serving.md`; agents that do not know they are compromised, taint, provenance and the S0 sandbox are
`docs/compromised-agents.md`; the phone is `docs/phone-approval.md` (on the phone branch, read at `d4373986e`, not in this
tree); encryption and key custody are `docs/pool-encryption.md`, `docs/node.md` ("Key custody", "Limits") and
`docs/keystore.md`; the harness sandbox is `docs/agent-sandbox.md`; the GitHub side is `docs/github-protection.md`. Where this
document builds on one it says so and adds only what that one does not hold.

**The rule: one compromise buys no more than its tier's blast radius, and each tier's radius is stated, bounded and visible to
the owner. A defence that the thing it defends against can switch off is not a defence**, so nothing here is enforced only by
the component it guards against: authority is a signature, never a file the user can write, and what runs is what a key the
attacker does not hold said should run. Security must not add friction (AGENTS.md, "Security without friction"): every
defence below is invisible in the normal journey, and section 13 lists the one that is not and why.

**What reading the code found** (each is checked in section 1 and used below):

1. Any push to the development branch, or to the GitHub account, runs code on every source-checkout device of every pool
   that follows it, with no signature check on the path that does it (`build_update.fast_forward` and `rebuild`); so does any
   commit in a primary checkout, built by the `post-merge` and session-start hooks.
2. One member's self-reported `build_seq` makes every other device refuse all work (`build.rs`, `work_gate`).
3. A compromised device whose fingerprint another device allows runs the sender's code there as that user, with no operating
   system sandbox, and the allow list is a plain file (`shards.json`).
4. Every board is plaintext on disk and in replication, and being an active member is the whole right to read it.
5. The governance verifier takes Ed25519 admin keys only, has no release role, and records a key's strength as declared; the
   phone's key is P-256.
6. No pool-level panic, freeze, wipe or incident record exists.

## 0. The decisions, one line each

| # | Decision | Why |
|---|---|---|
| K1 | What a pool runs is named by a signed `release` act: a commit, the hash of its tree and the hashes of what it builds. A member's claim of a build, and whatever the origin holds, name nothing. | The origin and the lander's GitHub credential are the weakest assets; a signature the attacker does not hold removes them from the trust path. |
| K2 | The release root is a strong admin key with role `release`, pinned in the founding charter (the phone's key by default, no extra step). A release delegate (a device that holds a keystore key and nothing else of the owner's) signs releases of ordinary changes only, limited, 30 days, and only with a landing record, a review from a different device and a build attestation from a builder key the charter pins: no single device, the delegate's included, can make a release. | The owner's agents sign through a routine, scoped, expiring grant, as the delegate of `docs/pools-and-serving.md` 1.3 already does for `admit`; a code landing is code execution on every follower, so three devices must agree. |
| K3 | A device hashes the commit's content from the git objects (never from a checkout and never from `git archive`) before any build step runs, builds only in a fresh staging directory with a scrubbed environment, and never merges into a user's working tree. An unsigned or mismatching tip is held and notified, never built. | A build runs code (build scripts, macros, the build backend, the smoke hooks, a `post-merge` hook); the verified commit must be the whole build input. |
| K4 | The delegate signs only when two independent builders (the lander and a builder key pinned in the charter) produce the same artifact hashes; the act pins the node binary, the wheel, the dependency locks and the toolchain. A follower that is not a developer's machine installs by fetching the artifact by hash instead of building. | A compromised builder alone cannot get a backdoored binary signed, and a follower that never builds cannot be attacked through a build. |
| K5 | A file the user can write is never an authority. The shard allow list, the landing trust list and the like are derived from signed acts or carry a strong signature. | A user-level attacker edits files; it cannot sign as the phone. |
| K6 | Work that another device sends runs on the host under the operating system sandbox: no read of state, keystore or home, no network, write only its scratch. | One compromised device must not be code execution on the others. |
| K7 | Boards are sealed per board and epoch; a member reads a board only by holding a wrap of its key. Envelopes are clear, bodies sealed. | Membership stops being the right to read everything. |
| K8 | A removal and a panic rekey; a removed member reads nothing after the cut. A device admitted by a delegate or an open join holds no board key until a person reviews it. | Closes the read right of a Sybil and of a removed device for the future. What it held stays exposed (section 9). |
| K9 | The design assumes a device's user account can copy the device key. What limits it: only strong keys sign anything irreversible, removal is sticky and one tap, clones are flagged, the key is the OS's best and never trusted beyond its tier. | A key that the node itself must use cannot be hidden from the account the node runs as (`docs/node.md`, "Key custody"). |
| K10 | The phone key's strength is attested when it is added (Android Key Attestation), not declared; release, panic and removal need `BIOMETRIC_STRONG` without the device-credential fallback. | A software key must not be able to call itself strong. |
| K11 | An irreversible root act (a change of the release role, an admin added or removed, a weakened charter, the removal of the owner's other devices in bulk) is held for hours in a pool with one strong key, and the recovery phrase can cancel a held act and remove the phone at once. Everything else stays one tap. A pool with one strong key shows a banner and the weekly nudge, and every strong act shows on the owner's other devices within a minute. | Per-use `BIOMETRIC_STRONG` already stops a thief with an unlocked phone; what remains is coercion, and a hold the phrase can cancel is the cushion for it. The notice alone fails when the thief removes the screens that would show it. |
| K12 | A result, a capacity figure or an answer from another device is untrusted data. Sensitive work is checked by redundancy and canaries; the evidence feeds the trust ledger, flags and quarantine. | A signed log proves who wrote a row, not that the row is true. |
| K13 | Credentials are per device and least privilege: one push credential, on the lander only; the owner's own login on no device that runs agents. | With K1 a stolen credential no longer reaches followers; this limits what it still does. |
| K14 | `panic` is one act signed by one tap: it freezes, suspends, ends the named sessions' tokens, removes named devices, rotates keys (level 2) and writes the incident record. It is bound to its own id, so it is not replayable and its end names it, and it only reduces power; each effect is undone by its own named act. | Containment is time-critical; recovery can take a few taps. |
| K15 | Every defence is exercised in the rig (`scripts/pool-check`): a compromised device, a stolen device, a malicious origin, a lying member, a panic. | A defence nobody attacks is a hope. |
| K16 | A release names its place in a chain: it must descend from the build the device runs (`merge-base --is-ancestor`) or be a root-signed `rollback`; two acts at one `seq` are an equivocation that freezes the follow and nothing else. A lapsed act never stops a device that is running. | A signed old build is still a downgrade, and a signer that is down must not become a denial of service. |
| K17 | Nothing derived from a board is left in the clear at rest, or the document says which store is: every derived store is sealed or dropped, and a joiner folds from a signed, sealed checkpoint, not from rows it cannot read. | Sealing the rows and leaving an index, an attachment or the graph store open seals nothing. |

## 1. What is held where (verified at `15a47043d`)

| Asset | Where and how it is kept | Who can use it today | Source |
|---|---|---|---|
| Device key (Ed25519) | Wrapped under a keystore subkey (`device.key.wrapped`); the supervisor unwraps it and hands it to the node on stdin at each start; in the node's memory while it runs. No keystore: a passphrase file, or a plain 0600 file the node calls weak. Not hardware-held (Ed25519 is not a type the macOS Keychain or Windows CNG keeps; a Secure Enclave key needs a paid developer account, blocked by the owner's 2026-10-08 constraint) | The node; any process of the same user, by the keystore's own words ("code running as you can call the keystore like any program of yours can"), or by writing the supervisor's binary pointer | `device.rs`; `docs/pool-encryption.md` slice 3; `docs/keystore.md` (read, not run) |
| Person keys typed with `yes` (weak) | The person's keystore beside the node (`<state>/person`) | Same user; a pseudo-terminal answers the `yes` | `docs/node.md`, "Key custody" |
| `persons.json`, `enroll_secret.json` | Plain 0600 files | Same user: plant a secret of its own and enrol a key | `grants/person.rs`; `docs/node.md` |
| Session tokens | `<state>/client/<board>/<name>.token`, 0600; the node keeps the hash and the lineage | Same user, from the lead's lineage | `docs/node.md`, "Identity" |
| Admin keys (the charter) | Ed25519, `role: admin` or `recovery`, `strength` declared by its holder. No release role. No P-256 anywhere in `app/poolhouse-node/src` | A threshold of strong keys; the rig key in tests | `gov/keys.rs`, `gov/act.rs` |
| The phone's key | ECDSA P-256 in Android Keystore, StrongBox where present, a fingerprint or the device credential for every signature, lost on a new fingerprint. No hardware attestation is requested when it is added | The phone's holder | `docs/phone-approval.md`; `docs/pool-encryption.md` 2.1 |
| Board logs | `<state>/boards/<board>/log/<origin>.jsonl`, JSON lines, no encryption. Any active member may `pull` any board the serving device holds; the only refusal is "this device does not hold that board" | Same user; any member | `log.rs`, `netsync.rs`, the member protocol's `pull` handler |
| Test shards | `shards.json` (consent and the `allowed` fingerprints) is a plain file; the host starts `python -E` on the sender's tree as the device's user, in a process group, no sandbox policy | Any allowed device | `shard/consent.rs`, `shard/host.rs`, `fleet/shard_run.py` |
| Landing trust | `land_trust.json`, per device, a file | Same user | `landtrust.rs` |
| The running build | `selected.json` and `~/.poolhouse/runtimes`; the node binary's sha256 is recorded at build and checked at start (tamper after build, not provenance); built by `cargo build --release --locked`; the wheel by `pip wheel --no-deps` of the source | The updater, the `post-merge` and session-start hooks, an agent under the delegated `runtime.deploy` gate, the person, any process of the user that writes the directory | `node_binary.py`, `fleet/runtime_wheel.py`, `runtime_deploy.py`, `scripts/hooks/workspace_hook.py` |
| The release key | `signing.RELEASE_KEY = ""`; `scripts/release-key` stores one in the OS keystore at the owner's terminal and loads it into ssh-agent to sign commits (a weak key by `docs/pools-and-serving.md` 1.3) | Nothing verifies against an empty key | `fleet/signing.py`; `docs/release.md` |
| The agent's GitHub identity | A GitHub App installation token (an hour) or a fine-grained token (up to a year), Contents and Workflows write; the development ruleset blocks deletion and non-fast-forward only; required signatures are optional and off. This is the document's target; whether the repository matches it was not checked here (`scripts/github-protection --check` needs the owner's `gh`) | Any process that holds the token: the landing runner, the harness environment | `docs/github-protection.md` |

**The findings the table supports**, in order of reach:

* **F1, the update path.** Three automatic triggers build and select code as the person, with no signature check on any of
  them. (a) `build_update.update_once` runs every 120 s and, when any active member advertises a newer `build_seq`, calls
  `fast_forward` (`fetch origin`, then `merge --ff-only FETCH_HEAD`: the branch tip at that moment, not a named commit) and
  `rebuild`. (b) The `post-merge` hook and the session-start hook run `runtime ensure --background` on the primary checkout's
  HEAD (`refresh_runtime`). (c) `runtime_trust.admit` accepts the checkout's tip or any ancestor. `ensure` runs `pip wheel` (the
  build backend runs), `cargo build` (build scripts and macros run), then `smoke`, which runs the built tree's hook scripts. The
  only verifier in the tree, `fleet/updates._unsigned` (`git verify-commit` against `signing.RELEASE_KEY`), is on a different path
  (`track_once`, the daemon's tracking) and is inert while the key is empty; a release install's path (`download_release`,
  `signing.verify_file`) also fails closed on the empty key. So nothing stands between a push to the development branch and a
  build on every device that follows it, and a legitimate landing is the trigger: every build a member advertises makes every
  follower fetch whatever the tip is. Nothing prompts.
* **F2, the work freeze.** A member's advertised `build_seq` is a plain number it reports (`Seen::read`); `newest_seq` takes the
  highest of any active member, and `work_gate` refuses leases, shards and landing while that is above this device's. Another
  member's claim about a third member is accepted until the third reports (`take_builds`, `relayed`). One lying member, or one
  runtime built from a local unpushed commit (the count is `rev-list --count` of the built commit), freezes work pool-wide and
  puts every follower's updater into its 5-minute-to-6-hour backoff.
* **F3, shard execution.** In section 11.
* **F4, files as authority.** `shards.json`, `land_trust.json`, `persons.json` and `selected.json` decide who may run code,
  land work, sign grants and what runs, and the user writes them.
* **F5, the read right is membership.** In section 9.
* **F6, governance gaps.** In sections 6 and 7.
* **F7, no pool-level incident response.** A search of the node source for freeze, suspend, wipe, erase, lockdown, incident or panic
  finds the sleep-time clock only. The narrower brakes are elsewhere: the sentinel freezes one agent session on a decoy
  (`sentinel/rails.py`) and `docs/sentinel-open-join.md` designs a pause of open intake. A removal ends one device's membership
  and cuts its log; it rotates nothing and wipes nothing.

## 2. Tier 1: a compromised agent session

`docs/compromised-agents.md` is the design: provenance, taint, standing-grant limits, held acts, the independent checker, the
protected set and slice S0 (the sandbox the agent installs with one system dialog). Nothing is repeated here. Three
cross-references this document depends on:

* **S0 is a precondition for any check that loosens, and it is on the friction-free path.** The release delegate (section 8)
  signs without a tap, which is a loosening; it enforces only on a device where S0 is installed, and **without S0 every
  release asks the phone, batched at most hourly: a tap an hour all day for a developer, which is a regression to onboarding,
  not a design**. So S0's one system dialog is part of the install that the owner already meets (shared with C15), and until it
  is installed the pool says so in one plain sentence and the hourly ask is the fallback, never the intended journey. C2 and C3
  need no S0 (they only verify), which is why they land first.
* **The protected set** (`docs/compromised-agents.md` 4.9) grows in this design: for the release path it is now every file
  not on a short inert list (section 8.1), and for the files that decide who holds a key (section 9) it stays named.
* **Cross-device reach of a session.** The first-run bundle's `shard_call` lets the lead's lineage send code to the owner's
  other devices; section 11 makes the host side safe whatever the sender is.

## 3. Tier 2: a compromised user account on a member device

Malware, or an attacker, running as the device's operating system user. No root.

**Today.**

* The device key: unwrapped through the keystore by any program of the user, or taken from the node's memory, or handed to a
  program of the attacker by writing the supervisor's binary pointer (`docs/pool-encryption.md`, slice 3: "a process of the same
  user that can write the supervisor's binary pointer can have the key handed to a program of its own"). The attacker is the
  device to every member, from this machine or another: it signs log rows, completes the handshake and serves.
* Every board the device holds, in plaintext on disk, and every session token (a process that starts itself from the lead's
  terminal shares its lineage and spends its grants).
* It can plant a person key (`enroll_secret.json`) and sign standing grants on this device. Those grants are local: a travelling
  grant (`task_authority`, `note_verify`, `sync_now`, `shard_call`) counts on another device only if its signature verifies
  against a key enrolled there, so a planted key signs nothing elsewhere.
* It cannot sign an admin act: strong keys are the only ones that count and the laptop's keys are weak (`docs/pools-and-serving.md`
  1.3). A delegate on this device signs at most 3 admits an hour and 10 a day for 30 days, each written to the pool board as
  `delegate_admit` (built) and shown on the phone with [Remove] (designed).
* The build: it can commit to the checkout, or just wait for the session-start hook, and `runtime ensure` builds the local tip
  (`admit`) and selects it, so its code runs at every start of the node, and the device advertises a high `build_seq` (F2).
  Writing `selected.json` and the `runtimes` directory directly does the same without `ensure`, and the checks beside them (the
  recorded sha256) live in the same directory.
* Where the device's fingerprint is on another device's shard list (`shards.json`; the owner's own devices are, once shard consent is
  on), it sends a tree that runs there as that device's user (F3). **This is the lateral path that turns one device into all.**
* The desktop app's loopback session as the person (a cookie on loopback, `docs/pool-encryption.md` 2.1), and the phone's queue:
  it can create approval requests as a session and so spam the phone (the fatigue defences of `docs/phone-approval.md` apply).
  What the screen path signs is a weak key's signature, which counts for nothing pool-wide.
* The owner's `gh` login and the agent token, if they are on the device (`docs/github-protection.md`: a process of the same user
  can read `~/.config/gh` and the keychain item).

**Gap.** A key the node must use cannot be hidden from the user the node runs as (K9). What limits the account today is that
it cannot sign admin acts, and that is all: the shard and build paths turn it into code execution elsewhere.

**Defence.**

* **D2.1 No authority in a writable file (K5), slice C1.** The shard host derives its allow list from the signed roster. A
  device is the person's **own** only when a strong key signed it in: the `admit` that put it in the pool is signed by a strong
  admin key of the same person as this device's holder (the charter records each key's person), or the device came in by a
  trusted-path scan (the join code and check words approved on the person's own phone with its strong key), or a person
  approved it from the review list with a strong key and marked it "mine". **A device admitted by a delegate or by an open join
  is never own**, whoever the delegate's person is: it has no standing in the shard list, no wrap, and no work until a person
  reviews it (section 7). An earlier draft counted a delegate's admit "for the delegate's person"; that made a delegate's
  rate-limited admit of an attacker's device an own device and contradicted the probation rule, and it is withdrawn. An own
  device is allowed to send tests here and nothing else is. Another person's device needs the existing one-tap approval,
  which now carries a strong signature the host verifies (not a line written to `shards.json`, which is then only a cache the host
  rebuilds). The rule reads the charter, not `persons.json`, so a planted key changes nothing. `land_trust.json` the same. A
  planted `persons.json` key stays local and is marked "added by a process, not by a person" in `pool_status` (weak, nothing
  pool-wide).
* **D2.1b What cannot be closed here.** `selected.json`, the `runtimes` directory and the supervisor's pointer are files the user
  writes together with the checks beside them, so against an attacker running as the user they stay open until C15. C3 closes the
  *remote and automatic* paths (a pushed commit, a lying member, a hook building an unsigned tip); persistence by the account
  itself is on the not-protected list (section 16).
* **D2.2 The sandbox on the host (K6), slice C1.** Section 11.
* **D2.3 Fewer boards (slice C9).** A device holds a board only for a project it works on, and with sealing only the boards it
  holds a wrap for. The attacker gets what this device held, not the pool.
* **D2.4 Clone detection and one-tap removal.** The heartbeat of `docs/pools-and-serving.md` 1.6 flags two boot ids under one
  fingerprint within a minute; a thief who uses the key from another machine is seen. An attacker who stays on the device is not
  a clone, and only removal ends it.
* **D2.5 The node under its own operating system identity (slice C15, a spike).** The only change that takes the device key,
  the logs, the tokens and the state files out of the account's reach is a separate identity for the node and a signing
  helper (`docs/node.md`, "Key custody": planned, not built). The cost is one system dialog at install, and S0's already asks one
  (`docs/compromised-agents.md` 2.1): the same dialog installs both, so the step count does not move. The spike measures what
  breaks (the Python client reads token files, the supervisor, the app) before the owner is asked anything; it may come back
  no-go, and sections 3 to 12 stand without it.
* **Rejected.** Rotating the device key by its own signature: the thief holds it too and races. Rotating by a tap per device per
  month: a visible step for a thief who needs one day.

**Slice and friction.** C1, C2, C3, C9; C15 later. Visible cost: none. One new line in `pool_status` for a planted key.

## 4. Tier 3: a compromised root or operating system on a member device

**What can never be protected.** Everything the device holds or can use while the attacker has root: the boards it holds
(in memory and on disk), every prompt it serves and the KV cache beside it, the device key while the node runs and every key
unwrapped into memory (board keys included), the session tokens, the answers it gives (results, capacity, offers), the
runtime and the sandbox on it, and the screen. A hardware-held key stops copying the key off the machine; it does not stop root
using it while the machine is up. The device can lie in every way a device can, inside its own signed log.

**What limits the blast** (each is a measurable bound the owner can read in `pool_status`, slice C8):

| Limit | What it bounds | Source |
|---|---|---|
| Boards held | The attacker reads only the projects this device holds; with C9 only the boards it holds a wrap for, and none after the cut | C9, C10 |
| Grants it can spend | Standing grants are scoped to a device and a session, expire in 30 days and carry signed spend limits; only four actions travel, and only to a device that enrolled the signing key | `docs/node.md`; `docs/compromised-agents.md` 5.1 (limits designed, not built) |
| Keys it never holds | Admin keys, the release root and the recovery key are on the phone; a device holds none, so it signs no admin act, no release of a protected change, no handoff | `docs/pools-and-serving.md` 1.3; section 8 |
| Hardware-held keys | The phone's key (Android Keystore, attested, C7). A device key on a TPM (Windows, Linux) or the Secure Enclave (blocked by the paid account) lets the owner keep the device's identity after a reinstall because it could not have been copied; it does not stop use during the compromise (C16) | `docs/pool-encryption.md` slice 8 |
| Routing probation | A device admitted by a delegate or an open join receives no prompts, files, shards, jobs or board keys until a person approves it | `docs/pools-and-serving.md` 7.7 (designed); C10 |
| What it can send | Rows under its own key only (a relayed row proves nothing, and a removal cuts its log), `acts` it cannot sign, a build claim the pool ignores (C3), work to hosts that sandbox it (C1), answers that redundancy checks (C12) | `gov/logs.rs`; sections 8, 10, 11 |
| Time to evict | Removal is sticky, immediate and one tap; a panic rekeys every board so the evicted device reads no later row | Sections 5, 12 |

**After the eviction.** The device is reinstalled and joins as a new device (one tap with three check words); its old identity
stays removed. Whatever it held before the cut stays exposed (section 9). **Friction:** none until the owner meets it, and then
the cost is a reinstall.

## 5. Tier 4: a stolen or lost device

**Today.** A strong admin removes the device: one tap, sticky, immediate, with a cut of its logs; every member that has the act
refuses its next handshake and takes no later row of its logs (`gov/logs.rs`, `removal.rs`); what it admitted goes with it.
The device key is in the OS keystore (not copyable from the disk), and the disk's own encryption is the operating system's.
The removed device keeps every board it held, in plaintext, forever, and nothing tells it to forget; its session tokens work only
on that device. A device that is only off the network is unaffected by the removal until it dials a member.

**Gap.** (a) Removal is a request the owner must find and word; `docs/phone-approval.md` describes no device list and no "lost
this device" button. (b) Nothing checks that the device's disk is encrypted, so a stolen laptop's boards may be one `cat` away.
(c) A removal rotates no key, so nothing the pool writes afterwards is protected from the thief. (d) Nothing makes the data on
the device unreadable.

**Defence.**

* **A one-tap "Lost or stolen" on the phone** (slice C8): the phone lists every device by name and check words with a
  last-seen time; the button removes the device, ends its delegate, suspends the grants scoped to it, ends its session tokens
  and writes the incident record (C8: the effects of level 1 for that device, plus the removal), and **from C11 it is a panic at
  level 2 for that one device (section 12), which also rekeys every board it held**. Before C11 the sentence on the phone does
  not claim a rekey ("it is cut off and keeps what it had; boards are not sealed yet, so nothing is rotated"). Biometric once.
* **Disk encryption is read, not asked.** The node reads FileVault, BitLocker or LUKS state (read-only) and the phone's device
  list says "this disk is not encrypted" in plain words. Nothing blocks; the agent says it once.
* **Wipe by deleting keys, not files (C10).** Once boards are sealed at rest, a device wipes a board by deleting its wrapped
  key: a few hundred bytes, cryptographic erase, nothing to overwrite. An honest node does it when it is told it was removed
  by a signed removal act, and when it has heard from no member for 30 days of its own **monotonic running time** (a "lease" on
  the wraps that members renew at every sync). Two rules keep the lease from destroying what it must not. (1) **It never
  deletes the last copy:** before it deletes a board's key the node checks its last signed roster for another holder of that
  board; a **sole holder** keeps the key, says so on the device list ("this is the only copy of 'notes'") and the lease for that
  board does not run. (2) **It is not a wall-clock timer:** it counts only time the device was running and awake, from a
  monotonic clock that a change of the system time cannot move, so setting the clock forward wipes nothing, and a laptop in a
  drawer (off, asleep) does not age, so it comes back with its boards. An attacker with a patched node cannot be made to wipe
  by any of this, which is why it is a courtesy. **It is a courtesy, not a defence**: a thief runs a patched node or never runs it. The defence is the key that is not on the disk
  (the keystore, the disk's encryption) and the rekey that makes later rows unreadable. The document says so wherever the
  button is described.
* **What the removed device already synced** is retroactively exposed, all of it, and cannot be recalled (K8). The phone says
  so once, in the removal sentence: "It keeps what it already had; it will read nothing new." Secrets are never in a board: the
  credential screen of the relay stays (`docs/pools-and-serving.md` 7.3), and sealing does not replace it.

**Slice and friction.** C8, C10. Visible: one tap, only on a loss. The device list is a screen the owner opens, never a step.

## 6. Tier 5: a compromised or stolen phone

The phone holds the admin key, soon the release root, and the panic button. **Today:** the approval key is hardware-backed
where the phone has it, signs only after the fingerprint or the device credential, is lost on a new fingerprint, and the
branch's later commits ask for a strong fingerprint or face with no PIN for non-routine acts. Another phone removes it
(`docs/phone-approval.md`, "Several phones"). Governance holds every lever (24 h to add or remove an admin, 15 minutes to
follow a reissue), and any other strong key cancels. **The residual**, stated in `docs/pools-and-serving.md` 1.11: when the
phone is the only strong key, nobody can cancel what it signs. **The residual, stated honestly (revised after review):** the
earlier text said a stolen unlocked phone used in one burst takes the pool. With per-use `BIOMETRIC_STRONG` and no device
credential (K10) the thief of an unlocked phone cannot sign: every signature needs the owner's fingerprint or face, and a newly
enrolled fingerprint destroys the key. What is left is **coercion** (someone forces the owner's finger or face), a person whose
biometric is enrolled on the phone, and a modified phone operating system or app that is attested away. Those are real, they
sign in one burst, and what bounds them is that irreversible root acts are held for hours and the recovery phrase cancels a
held act and removes the phone (G5.5).

**Gaps (new).**

* **G5.1 Strength is declared.** A charter lists a key's `strength` as its holder wrote it. A modified app or an emulator with a
  software key can say `strong`. Android Key Attestation (a certificate chain to a public Google root, checkable against the
  published roots with no account of ours; the revocation list is a public download) proves a key is hardware-backed, requires user authentication per use and cannot be exported. Nothing requests it
  (`docs/pool-encryption.md` 2.1: "no hardware-backed attestation requested").
* **G5.2 P-256.** The phone's key is P-256; `gov/keys.rs` verifies Ed25519 only. A phone-held admin, release or panic key does not
  verify today; the rig signs with an Ed25519 key in memory.
* **G5.3 The device credential.** The first phone design accepts a PIN; a shoulder-surfed PIN with the stolen phone is a full
  signature. The branch's later commits remove the PIN for non-routine approvals; panic, release and removal must be in that class.
* **G5.4 Nobody sees it.** A removal or an admin act by a stolen phone shows only on that phone.
* **G5.5 Nothing separates the reversible from the irreversible.** Today a removal of one device and a change of who runs the
  pool are both one signature of the same key; in a one-key pool only the existing 24 h holds stand between a coerced tap and a
  takeover, and a thief who first removes the owner's other devices also removes the screens the tripwire would show.
* **G5.6 Key loss.** A pool whose phone is lost has the recovery key and nothing that rotates the `release` role; the release
  root would stay a key nobody holds.

**Defence (slice C7).**

* Attestation checked by the node when a phone is added: `strong` is recorded only for an attested, hardware-backed,
  per-use-authenticated key, and the founding text says "your phone's key is protected by the phone's hardware" or "this phone
  cannot protect the key in hardware; it counts as weak". A phone with no attestation signs routine acts only.
* **P-256 admin keys, split out as slice C7a** because the update path (C2 to C4) cannot use a phone-held key without it: an
  `alg` field in the key, its acceptance and the signature (the verifier branches on it; both algorithms are in the existing
  libraries, no new primitive). It needs no attestation, so it lands before C2.
* `BIOMETRIC_STRONG` only, no device credential, for `panic`, `release`, `remove`, `admin_add`, `admin_remove` and `handoff`.
* **The tripwire (K11).** Every device of the owner shows, within a minute of first sight, "your phone signed: removed
  'mac-mini'" for every irreversible strong act, and the phone's quiet daily summary lists them. It costs the owner nothing and
  shortens the time a thief works unseen to the time it takes to look at a screen. It is not a cancel, **and it fails if the
  thief removes the owner's other devices first**, so the removals that would blind it are themselves irreversible root acts
  (next bullet), held while the screens are still there to show them.
* **A gate on irreversible root acts only, not on the pool (replaces "no gate on a second strong key").** In a pool with one
  strong key, each of these is held for 6 hours, shown on every device of the owner and in the phone's summary, and cancelled by
  the recovery phrase **at once, with no 7-day wait**: any change to the `release` role (add, remove, rotate), an admin added or
  removed, a charter weakening (a lower threshold, a shorter hold, a shorter soak, an open join policy), a `handoff`, and **the
  removal of every device of the owner that is not the signer, or of two or more devices of one person within an hour** (one
  device removed alone, "Lost or stolen", is still one tap, immediate: the common case is unchanged). The governance holds that
  are already longer (24 h) stay. Everything that only reduces power or is routine is never held: a panic, a single removal, an
  ordinary release by the delegate, an admit. A pool with a second strong key has no such hold (the second key cancels), so the
  hold is the price of having one key and the nudge to add another is real. **The phrase has two powers and no more:**
  (a) **cancel** any held act, and remove the phone's key from the charter, immediately, since both only reduce power;
  (b) **add or rotate** a key (a new phone's admin and `release` key), which is held 7 days and cancelled by any strong key.
* **Second strong key.** The pool holding a release root, or serving a device of another person, reads "one key: coercion or a
  stolen phone with its owner's face can sign; what it signs that cannot be undone waits 6 hours" in `pool_status`, with the
  weekly one-line nudge of `docs/pools-and-serving.md` 1.8. The better choices, in order: a second phone (an old one in a
  drawer is enough), a hardware key through the phone app (later), the recovery phrase.
* **Key loss (G5.6).** `release_role_rotate {old, new}` is signed by the recovery key (the phrase) and names a new phone's key
  as admin and `release`; held 7 days, cancelled by any strong key, shown everywhere. Until it takes effect devices keep running
  the last release they verified and follow nothing new (a pool with no live `release` key follows nothing, 8.1). A pool that
  lost both the phone and the phrase is in the not-protected list (section 16, item 12), and each of its devices keeps working on the last build.
* **Removing the phone's key from another admin** is the existing "remove the phone" request on a second phone, or the typed-`yes`
  command at a computer that holds a person key (`docs/phone-approval.md`), or the phrase's immediate remove above; with a phone
  as the only strong key and no phrase, the offline recovery of `docs/node.md` is the last resort and deletes `persons.json`,
  which does not touch the charter. The phrase is therefore the one thing the owner writes down once (section 15).

**Friction.** Attestation is checked inside the existing add-a-phone scan: no extra step. Tripwire notices are quiet (a notice
is free, a tap costs friction: every new warning here is a notice). The 6-hour hold costs the owner nothing in a normal day,
because no irreversible root act is a normal act; it is felt only by an owner who changes the charter, who waits 6 hours, with
the reason stated in the card. The second key is the nudge that already exists. A phone that cannot attest counts as weak, and `pool_found` refuses a charter with no
strong key, so the owner's own phone is checked first (section 15, step 6) and a second strong key is the answer if it fails.

## 7. Tier 6: a compromised or malicious pool admin or delegate

**Today.** A second person's admin key removes members (a routine act, immediate), proposes admin changes (held 24 h, vetoed
by the removed key, a seven-day dispute freezes admin-set acts), reissues (15 min hold, cancelled by any other strong key) and
cannot weaken a threshold without the longest hold. A delegate signs only `admit`, at most 3 an hour and 10 a day, for 30 days,
each admit written to the pool board (built) and shown on the phone with [Remove] (designed). Nothing gives an admin a release
role, because none exists.

**Gaps.**

* **G6.1 An admitted device reads everything at once.** An admitted device, by an admin, a delegate or an open join, may `pull`
  every board any holder has (section 1). The delegate's rate limit is on admits, not on what each admit receives; 10 a day for a
  month is 300 devices, each a full reader.
* **G6.2 Roles blur.** A friend's admin key is a governance key. If the release root were just "an admin", a rogue co-admin would
  decide what code every device runs.
* **G6.3 A rogue co-admin's cheap harm** is bounded by holds and a seven-day dispute, which freezes the pool's governance for a
  week. That is the design's accepted cost (`docs/pools-and-serving.md` 1.11) and is listed in section 16.

**Defence.**

* **Separate roles.** `release` is a role in the charter, added only by a sensitive act that needs the threshold of people and the
  key's own acceptance, never inherited by being an admin. Admins govern who is in; the release root decides what code runs;
  they are usually one phone, and a second person's admin key does not sign releases unless the owner adds the role (C2).
* **Probation for a board key (K8, C10).** A device admitted by a delegate or an open join gets no board key and no work until a
  strong admin approves it from the review list (default Drop for open joiners, as in `docs/pools-and-serving.md` 1.9). The
  person's own devices (admitted by a **strong** key of that person, or by a trusted-path scan, D2.1; **never** by a delegate,
  and never by a key enrolled on a device, which is a weak key) are approved by that admission, so the normal journey adds
  nothing. This is one definition of "own" used by the shard list, the landing trust list, board wraps and probation alike.
* **A delegate's reach is the sum of its admits**: with C10 that is zero boards until review, so the 10 a day rate bounds queue
  length for the reviewer, not exposure. Before C10 exists, C10a (below) already makes a delegate-admitted or open-joined device
  refused on `pull`, with no cryptography: the honest holder checks the signed roster and the review list and serves a device on
  probation no board but the pool's history board.
* **Every admin act is shown to every other admin** as the tripwire of section 6; an act of a rogue admin reaches the owner's
  screen within a minute.

**Slice and friction.** C2, C7, C10a, C10. Friction: one line in the review list, only for devices that were not the person's own.

## 8. Tier 7: supply chain and the update path

**What the attacker gets today.** F1, above. In full: whoever can push to the development branch (the lander's GitHub App or
fine-grained token, a harness environment that holds it, the owner's `gh` login on any device, or the GitHub account itself)
gets code execution as the person on every source-checkout device of every pool that follows that branch, at the next moment any
member runs a newer build (every landing is one), with the device key, the boards and the phone-approval caller in reach. A
commit in a primary checkout is built at the next session start or merge by the hooks (`admit` accepts the local tip) and
advertised as a newer build, which freezes the others (F2). Release installs and `track_once` verify a signature, but against
an empty key, so they refuse everything and protect nothing the pool actually uses. **Read, not run:** nobody has pushed an
unsigned commit to a bare origin and watched a second device build it; the claim rests on `build_update.py`, `runtime_trust.py`
and the hooks, and C3's first test is that run.

**Gap.** The trust path has no signature the attacker lacks. The one key the design pins, `RELEASE_KEY`, is a single global
key kept where a process of the user can use it (the OS keystore, and ssh-agent for commit signing) and compiled into the
source; it is the project's key for outside releases, not the pool's.

### 8.1 The design

**A release is a pool act.** A new act kind `release` in `gov/` (`docs/pools-and-serving.md` 1.5), replicated by the existing
`acts` op, verified by every member, held, capped and paged like the others:

```
release { kind: build|rollback, commit: 40 hex, tree_sha256, seq, prev: hash of the previous release act (a cancelled one
          included) or "", prev_commit: the commit of the newest uncancelled release or "", floor,
          artifacts: [{target, kind: node|wheel, sha256, bytes}],
          locks_sha256: {cargo: app/Cargo.lock, build_requirements, wheel_requirements},
          toolchain: {version, archives: [{target, sha256}]}, inputs_sha256,
          landing, review, attest,
          builders: [{id, artifacts_sha256}], not_after_ms }
```

* **The three signatures a follower verifies (K2).** `landing` is the lander's signed record (`{commit, tree_sha256, gate
  result at that SHA, lander_fp}`, `docs/compromised-agents.md` 4.9); `review` is the independent review's accept, signed by the
  device key of the device the reviewing session runs on; `attest` is the build attestation `{commit, tree_sha256, artifacts,
  builder_fp}` signed by a **builder key pinned in the charter** (role `builder`, added at founding or by a root act, never a
  key of the delegate or of the lander). Every follower checks that all three verify against the signed roster (a removed,
  flagged or probation device is no one), name the same commit and tree, and that **`reviewer_fp` differs from `lander_fp`,
  the builder key is neither, and the signer is none of the three**. One compromised device, the delegate's included, therefore
  cannot make a release: it would need three. The builder is by default a second device of the owner's that runs the
  build in the nested sandbox and holds nothing else; a hosted CI key is allowed and is the weaker choice (its secret is on
  GitHub, so a GitHub takeover can forge an attestation, and still needs the lander and the reviewer). A pool without three
  such devices has no delegate for code releases: the root signs them, batched (8.2), which is the honest cost of a pool of one
  or two devices.
* **Tiers, by the diff from the previous release's tree, recomputed by every verifier from the two trees (a signer's flag is
  never read).** A short **inert list** is the only thing that is not protected: `docs/**` and `tests/**` and the test-support
  files, only as Markdown, text or test sources, and `assets/**` that are images by extension, **and** only when neither
  builder's record of the files it read (`inputs_sha256`: cargo's dep-info and the wheel's file list) names the path and its
  mode is a plain file (a new executable bit, a symlink or a rename into a non-inert path makes it protected). **Every other
  path is protected**, including `device.rs`, `net*.rs`, `shard/host.rs`, `log.rs`, `build.rs` and every other source file: an
  ordinary landing is code execution on every follower, so it is not treated as ordinary. The earlier version of this document
  listed protected paths and so left all of those out; a deny-list that must name every dangerous file is wrong by default.
  * **Inert diff:** no new artifact; the delegate's signature with the `landing` record alone advances the tree, and the
    artifact hashes must equal the previous release's (checked by every verifier). A documents-only landing builds nothing.
  * **Protected diff:** the delegate signs only with all three signatures present and verified, the soak below, and the notices
    below. No tap.
  * **Root core:** inside the protected set, the paths that decide the verification itself need the root as well, by
    signing the act or by a **pre-authorisation**: `app/Cargo.toml`, `app/Cargo.lock`, the toolchain pin, every `build.rs`,
    `pyproject.toml`, `packaging/**`, `.github/**`, `src/poolhouse/{build_update,runtime*,node_binary,node_build,node_supervise}.py`,
    `src/poolhouse/fleet/{signing,updates,runtime_wheel}.py`, `app/poolhouse-node/src/gov/**`, `keystore*.py`, `person*.py`, the
    sandbox profile and helper, the hooks and guards, and the protected set of `docs/compromised-agents.md` 4.9. The list is data
    in code and contains itself. The pre-authorisation is the root's signature over `poolhouse/release-preauth/v1`, the previous
    release's `tree_sha256` and the new one, made on the landing card before the push (the runner knows the combined tree at
    gate time); a delegate's signature then counts for exactly that tree and no other.
* **Notices (free) in the first 24 hours of a new delegate:** every release a delegate signs in its first 24 hours is a notice
  on the owner's phone and other devices, with the changed paths, the three signers' names and "Remove" next to it, whatever
  its tier. After that it is the daily line, and an urgent notice only for the first release after a gap, one past a rate, or
  a root-core one. A notice is free and a tap is not, so the early hours cost the owner a look and no step.
* **Order, downgrade and rollback (K16).** `seq` is strictly above every `seq` the device has seen for this pool, a cancelled
  act **burns its `seq`** (the next act is `seq` + 1 and names the cancelled one as `prev`, so a withheld or cancelled act
  cannot be reused and a gap is a signal, not a fork). A `kind: build` act must have `prev_commit` as an ancestor of `commit`,
  checked by every follower with `git merge-base --is-ancestor prev_commit commit` on the objects it fetched (a missing object
  fails closed); a device that runs a commit the new one does not descend from takes the act only if it is `kind: rollback`,
  which needs the root's signature (never the delegate's) and names the `seq` it rolls back from. `floor` is the runtime epoch of
  `packaging/runtime-floor`; a lower one is refused. **Equivocation:** two valid acts at one `seq` with different commits
  (a delegate or the root signing twice, or two delegates racing) are both held, nobody follows either, each device writes a
  `release_equivocation` flag row naming both, the signer's delegate ends, and the next root-signed or three-signature act at
  `seq` + 1 that descends from neither's `prev_commit` resolves it; devices keep the build they run. **`not_after_ms` (14 days
  from signing) limits only when an act may be newly adopted, never whether a running build may keep running**: a signer that
  is down for a month makes the pool "behind", never "stopped", and the quiet line says so (8.5). A device ahead of the pool's
  newest release (it holds a later act than the pool's others, or a rollback lowered the head) adopts the pool's act only by the
  rules above; until then it runs what it has, advertises the `seq` of the act it runs, and takes no work that needs the same
  build as another device.
* **A soak hold** (`holds.release_soak_min`, default 10 minutes) counted on each device from first sight of the complete act, in
  which any strong admin may `cancel` it; a weakening (a shorter soak) is held like any weakening. The soak is the only time a
  device waits, and nothing waits for it: a build takes longer. **The soak is invisible and so is an attack on it,** so the
  daily summary says it: "14 releases soaked, the shortest wait was 10 minutes, none cancelled", and a cancel, a cancel storm
  (an attacker stalling every release), a weakened soak or a device whose soak ran short is an urgent notice.
* **Delegate limits** (starting values): at most 6 an hour and 40 a day, `delegate` and `delegate_end` as for `admit`, a weak key
  may end one, and every release it signs is a line in the quiet daily summary (urgent as above). A pool keeps the last 64
  release acts; each names its predecessor by hash.

**The release root is pinned at founding.** The founding charter lists the phone's key with `role: release` as well as admin, by
default and with one plain line in the founding text: "this key also decides which version of Poolhouse your devices run".
A pool with no release key follows nothing (a device keeps its build and `runtime status` says why in one sentence); there is no
legacy pool and no migration. A lost root is rotated by the recovery phrase (section 6, key loss).

**The follow rule.** `build_update` no longer asks which commit is newest; the pool's build is the verified, soaked, uncancelled
`release` act with the highest `seq`. **The verified commit is the whole build input, so the user's checkout is never part of it
(K3).** The earlier design verified a commit and then ran `ensure` in the user's checkout, where the working tree, untracked
files, `.git/hooks/post-merge`, a `.cargo/config`, `RUSTFLAGS`, `PIP_*` and an unhashed build requirement were all inputs the
signature did not cover. For a verified act a device:

1. fetches the commit by name into a bare object store the node owns (`<state>/release-objects`), from the origin or from a
   member: the source of objects is irrelevant. **It does not merge into any working tree.** `fast_forward` (which merges
   `FETCH_HEAD` and fires `post-merge`) is no longer on the follow path; a person or an agent updates their own checkout as they
   do today, and that never builds a runtime for the pool;
2. computes `tree_sha256` as the sha256 of the sorted records of `git ls-tree -r -z --full-tree <commit>`, each with its mode,
   path and the sha256 of the blob's content read by `git cat-file`. It is **not** `git archive`, which honours `export-ignore`,
   `export-subst` and `eol` attributes and so does not cover the full tree. It **rejects** any submodule (mode 160000), any
   symlink that leaves the tree, a `.git` component, a `..` component and a case or Unicode-normalisation collision. A mismatch
   is `held: the files do not match what was signed` and no build step has run;
3. checks ancestry or the `rollback` kind, the three signatures, the tier, and `not_after` for a new adoption (above);
4. **installs by fetching, by default**: a device that is not a developer's machine (no source checkout of Poolhouse, no
   toolchain: the owner's normal devices) fetches each artifact for its target by `sha256` from any source (a member, the
   release store, the origin's release assets: all untrusted transport) and refuses one whose `sha256` or `bytes` differ from the
   act. Nothing is built, so there is no build to attack, and macOS reproducibility is not needed (8.3);
5. otherwise (a source follower: a developer's device that chose to build, or a target with no artifact) **exports the verified
   blobs into a fresh, empty staging directory** the node creates (`<state>/staging/<act id>`: no `.git`, no untracked file, no
   hook), installs the toolchain the act names by the archive hashes it lists, and builds there, in the nested sandbox of S0,
   with a **scrubbed environment**: an allow-list of `PATH` (a fixed minimum), and `HOME`, `CARGO_HOME`, `TMPDIR` inside the
   staging directory; every `RUSTFLAGS`, `RUSTC_WRAPPER`, `CARGO_*`, `PIP_*`, `PYTHON*`, `LD_*` and `DYLD_*` variable is removed
   and `PIP_CONFIG_FILE` points at nothing; `cargo build --release --locked --frozen` against `app/Cargo.lock`; the wheel built
   with `--no-build-isolation` and the build requirements (hatchling) installed first from `packaging/build-requirements.txt`
   with `--require-hashes` (today `pyproject.toml` says `requires = ["hatchling"]`, unpinned and unhashed, so the build
   backend that runs at wheel time is whatever the package host serves; C6 closes it);
6. compares each built artifact's sha256 with the act's for this target. A mismatch selects nothing and keeps the previous
   runtime: either the build is not reproducible here or something is wrong; the notice says which it measured. A target the act
   does not list is built and its hash is *reported* in a `build_report` log row, not pinned (said, not hidden);
7. runs the existing smoke and health check, selects, and keeps the last three verified runtimes. Rollback is the existing
   `runtime rollback --to`, plus automatic rollback when a later `release` act with `revokes: [seq]` or a higher `floor` names
   the running build.

`runtime_trust.admit` for an automatic caller or a delegated agent accepts only a commit a verified `release` act names; a
person at a terminal may still build a local commit with `--local`, marked `local` in `selected.json`, and such a runtime
**advertises `local`, takes no work for other devices and counts for nobody's staleness**. Installed runtimes are not how agents
test their own work (`PYTHONPATH=src` in their worktree is), so the person-only gate costs the normal day nothing.

**Members' build claims are gone (F2).** A device advertises the `seq` and act id of the release act it runs. `newest_seq` is the
highest `seq` among the verified acts this device holds, not among what members say; a member's claim of a build is shown and
counts for nothing. A device behind the pool's verified `seq` is "catching up" exactly as today; a lying member changes nothing.

**An unsigned tip is held and notified.** When origin has commits past the last release act, nothing happens. After 3 hours with
no new act, the quiet summary says "the pool's newest build was signed 3 hours ago; 4 commits since are not signed yet" (the
lander is down or a delegate lapsed); an unsigned tip is never shown as a prompt. The owner's agents already see it in `runtime
status`.

### 8.2 How the owner's agents sign (the heartbeat)

A signed tip arrives with each landing, invisibly. The release delegate is a device with **no agent sessions and no branch code
on it**: it watches the pool board for a landing the runner settled `landed`, fetches the commit, computes the tree and the
diff's tier, gathers the three signatures (the lander's record, a review from another device, the builder's attestation) and
signs only if they verify and the builders' artifact hashes agree. The lander's gate executes untrusted branch code, which is
why the delegate's key must not live on it. **A commit with no landing record is never signed** (so a push straight to origin
gets nothing signed), and **a review counts only from a session on a different device than the lander's**.

* **Ordinary landings** (almost all): signed, no tap, a notice for the first 24 hours of a delegate and then one line in the
  daily summary.
* **A landing touching the root core**: the landing already asks the person (`docs/compromised-agents.md` 4.9); the same card
  carries the pre-authorisation of 8.1, so it is **one** tap per such landing, never two.
* **Lockfile bumps do not become a daily tap.** Dependabot opens lockfile bumps daily, and `app/Cargo.lock` is in the root core.
  The card therefore shows a **semantic diff** and not "Cargo.lock changed": the crates added, removed and moved (name, old and
  new version), any new `build.rs`, proc-macro or `links`, any new registry or git source, and the registry's publication
  time of each new version. A **standing grant for lockfile-only bumps** (signed by the root once, monthly with the renewal,
  scoped, expiring in 30 days) lets the delegate sign a landing whose only root-core change is `app/Cargo.lock`, with **no new
  crate, no changed source, no `build.rs` or manifest change, every moved crate already in the lock, its version published at
  least 72 hours before and its checksum the registry's**; anything else asks. What this does not stop is a malicious new
  version of a crate already trusted, which 8.4 names; the 72 hours and the delegate's three signatures are its only cushions.
* **Renewal.** The delegate lasts 30 days and renews inside the monthly approval of `docs/pools-and-serving.md` 1.3 (one line:
  "let 'mac-mini' sign builds of ordinary changes"). No new tap.
* **Without S0 every release asks the phone, batched at most hourly**, which is a tap an hour for a developer and not an
  acceptable steady state: S0's one dialog is on the friction-free path of the install (section 2), and the sentence on the
  phone says "install the safety box to stop being asked".
* **A pool with fewer than three devices** (a lander, a reviewer on another device, a builder) has no delegate for code
  releases: the phone signs them, batched, and the pool says so once. A pool of one device can run all three roles on it with
  the sandbox as the only separation, and says that plainly (section 16).

### 8.3 Reproducible builds and the checksum pin

The 2026-10-08 decision (`docs/pool-encryption.md` 8.1) rejected reproducible builds as more machinery than the owner wanted for
a guest-attestation path that was not being built. This order supersedes it for the update path only, and the cost is carried by
the builder and the signer, not the owner. Two independent builders: the charter-pinned builder (a read-only build from the
pushed tree) and the signer's sandboxed build. **Before anything enforces, slice C5 measures** whether they agree: two builds of
the node binary and the wheel on two of the owner's devices with a pinned toolchain, `--locked`, `CARGO_INCREMENTAL=0`,
`--remap-path-prefix`, `SOURCE_DATE_EPOCH`, recorded in a dated measurement that names its command.

**The toolchain is not pinned today and the earlier text named a file that does not exist.** There is no `rust-toolchain.toml`;
`rust-version = "1.82"` in `app/Cargo.toml` is a minimum that any later compiler satisfies, and the lock is `app/Cargo.lock` (not
a root `Cargo.lock`, which does not exist). C5 adds `app/rust-toolchain.toml` naming one exact version and the act's
`toolchain` field names the archive hash for each target, checked by the follower before it runs anything the archive holds;
the toolchain pin is in the root core. If macOS linking or the ad-hoc signature is not deterministic, the pin applies to the
targets that are (Linux, Windows) and the macOS artifact is the builder's, verified by hash and **fetched, never compiled, by
a follower** (8.1 step 4, which is the default for every follower that is not a developer's machine, so macOS
reproducibility is needed only for the signer's cross-check). The result is written down either way, and no pin is claimed
before the first measurement.

### 8.4 What is still open

* A compromised **dependency** (a crate or wheel both builders fetch) is built into both artifacts and signed. The lock hash,
  `--locked` and the 72-hour age of a bumped version stop a *later* swap and a rushed one, not a first malicious release;
  review of new crates (a root-core change) is the control. The toolchain is pinned by hash; the registry's index is not.
* A **reviewed malicious source change** in an ordinary file now needs a lander, a reviewer on another device and a builder to
  agree, so one steered agent or one compromised device cannot ship it; it remains possible when the owner's own steered
  agents on three devices all accept it (`docs/compromised-agents.md` 4.9). Partly fixable, and no longer unprotected: the cost
  of the attack is three devices, not one push.
* The **first install of a new device** is a download the owner trusts, and `RELEASE_KEY` is empty, so the phase-1 answer of
  `docs/release.md` does not exist today. The honest design is in section 15.1.
* **GitHub account takeover** no longer runs code on followers; it still destroys the repository, withholds updates (devices
  stay on the last verified build and say so after 3 hours) and can publish to outside users if the release environment's
  approval and the `RELEASE_SIGNING_KEY` are also taken (`docs/github-protection.md`).

### 8.5 What it costs the owner, day to day

| When | What the owner sees or does | Target |
|---|---|---|
| Every landing, all day | Nothing: the delegate signs, the soak runs, followers fetch the artifact; one line in the quiet daily summary (a notice for each release in a delegate's first 24 hours) | invisible |
| A landing that touches the root core | One tap, on the card the landing already shows, with the changed paths and a semantic diff; the same tap pre-authorises that exact tree | a few a week (already counted, `docs/compromised-agents.md` 4.9) |
| A Dependabot lockfile bump with no new crate | Nothing, under the monthly standing grant; a new crate or a `build.rs` asks, with the crates named | invisible; a tap only for a new crate |
| Monthly | The renewal approval lists the release delegate and the lockfile grant as lines | no extra tap |
| The signer or lander is down 3 hours | One quiet line: "the newest build was signed 3 hours ago; 4 commits since are not signed yet"; devices keep the last verified build and work, also past an act's 14 days | informational |
| An unsigned or mismatching tip | Nothing; the device holds and keeps working; after 3 hours the same line | informational |
| The owner builds a local commit to test Poolhouse itself | `--local` at a terminal (a person's act); that runtime takes no work for other devices | rare, a developer's act, not an onboarding step |
| A build that does not reproduce on a device | One quiet line, the previous runtime stays; the agent reads the measurement and fixes the cause | informational |
| No S0 yet | A tap an hour for releases; the phone offers the one-dialog install | a regression until S0 lands, named |
| After a panic | `panic_end` and the renewal tap, and a root-signed release if one was held: three taps | only after an incident |

**Slices and friction.** C2, C3, C4, C5, C6, C13. Visible: none in the normal journey (a 10-minute soak nobody waits for); one
tap per root-core landing, already counted.

## 9. Tier 8: confidentiality inside a pool

**Today.** Boards replicate over TLS 1.3 pinned to the member certificate and are plaintext at rest on every device that holds
them (`log.rs`: JSON lines, no encryption); any active member may pull any board a device holds; a removed device keeps all it
had; a Sybil admitted through a delegate or an open join receives everything. Core dumps are off for the node and the supervisor
(`RLIMIT_CORE 0`, `node_supervise.py`, `main.rs`). Prompts served to other pools (designed, `docs/pools-and-serving.md` 7.6)
are in the serving device's memory, its KV cache and perhaps swap, in the clear, by design.

**Design: per-board sealing (K7, slice C9).**

* **Keys.** One board key per board per epoch, 32 random bytes. A row's body is sealed with an AEAD from `ring`, which the node
  already links for TLS (no new crate), with the pool id, board, origin, sequence, epoch and previous hash as associated data, so a
  sealed body is bound to its place in its log. The **envelope is clear**: origin, sequence, previous hash, epoch, a size bucket
  (bodies padded to 1, 4, 16 and 64 KiB), the hybrid-clock time and the row's signature over all of it. Members that hold
  the board verify, merge and fold as now; a device that does not hold the key can carry and verify nothing more than the chain.
  The pool's history board (acts, delegate admits, incident records) stays unsealed: it is the record every member must read.
* **Wraps.** Each device publishes a signed device card: its X25519 agreement key, signed by its device key (HPKE, RFC 9180,
  from a maintained crate chosen in C9's first step, as `docs/pool-encryption.md` slice 4 decided HPKE; no hand-written primitive).
  A board key is wrapped to each holding device's agreement key and published as a `board_key` row of that board.
  Holding a board is therefore cryptographic: an active member with no wrap reads ciphertext. Honest holders also refuse to serve
  a board's rows to a member with no wrap, so the check does not rest on the key alone.
* **Who gets a wrap.** The person's own devices (the own-device rule of D2.1: no tap) automatically for every
  board they work on, with the current epoch and all earlier ones; any other device after review, with the current epoch and the
  epochs the reviewing admin picks (default none). History on join is therefore a decision that costs the owner's own devices
  nothing and costs a stranger's device a tap.
* **At rest.** The board keys are kept wrapped under the keystore subkey of the device (slice 3 of `docs/pool-encryption.md`, done
  for the device key); the logs on disk are the sealed rows themselves. A stolen disk reads envelopes. This is `docs/pool-encryption.md`
  slice 6, built as a by-product.
* **Every store derived from a board, enumerated (K17).** Sealing the rows and leaving what is built from them open seals
  nothing. The known derived stores, each of which is sealed, wrapped under the keystore subkey, or dropped, and each of
  which the marker test of C9 reads: **attachments** (files held by file ID, sealed as blobs under the board key and epoch,
  the ID a hash of the ciphertext); the **search indexes** over entries (an index leaks terms: encrypted at rest under a key
  wrapped by the keystore subkey, or not built for a sealed board); the **graph store** (ladybug, which stays: its files are
  encrypted at rest under a keystore-wrapped store key, or its pages for a sealed board are not written); **folded state**
  (the derived views of a board, same); **caches** (summaries, embeddings, thumbnails, push payloads and the slot files of
  C14: sealed or deleted at an epoch cut and on a member or pool switch). C9's first step is the C14 grep moved forward: plant
  a marker in a sealed board, exercise every route, then read every file the device wrote. **If any store cannot be sealed in
  the slice, `pool_status` and this document say "at rest: partial" and name it; the at-rest claim is made for no store the
  marker test did not clear.**
* **Joining a sealed board: the signed sealed checkpoint.** A joiner whose wrap covers only the current epoch cannot read, so
  cannot fold, the earlier rows, and the state it folds would diverge from every other member's. At approval a holder publishes
  `checkpoint {board, epoch, heads: [{origin, seq, hash}], state_sha256, sealed_state}`: the folded state at those heads, sealed
  under the current epoch's key, signed by the holder's device key. The joiner verifies the head vector against the clear
  envelopes' chain (which it can read without the key), unseals and folds forward from there. A single holder could poison a
  joiner's state, so a checkpoint is accepted only when **two holders on different devices sign the same `state_sha256`** (or,
  for the person's own device, one of the person's own holders); a joiner that finds two that disagree refuses both, keeps
  the boards it already folds, and says so on the review card. Epochs the reviewing admin chose to wrap are read directly and the
  checkpoint covers the rest. The cost is one extra signed row per approved joiner.
* **Rekey, hardened after review.** A new epoch is made on a removal, on a panic, on an admin's request and weekly. The ad hoc
  rekey has four holes, each closed:
  * **Wrap set equals holder set is not enough**: it proves who got a wrap, not that every wrap holds the **same key**. A
    malicious proposer could wrap different keys to different holders and split the pool silently. `board_rekey {epoch,
    key_commit, wraps}` carries `key_commit = SHA-256("poolhouse/boardkey-commit/v1" ‖ board ‖ epoch ‖ K)`; each holder unwraps,
    recomputes the commitment and accepts only if it equals the act's, else it writes a `bad_wrap` flag naming the proposer and
    keeps the old epoch. **Sealed rows use a key-committing construction too**: an AEAD from `ring` is not key-committing, so each
    row's envelope carries `kc = key_commit[..16]`, bound into the associated data and checked before decryption, so one
    ciphertext cannot be opened under two keys to two readings.
  * **The proposer** is not the lowest fingerprint, which a device can grind in advance. It is the holder with the lowest
    `H(trigger ‖ fingerprint)` among **reviewed, unflagged, unnamed** holders, where `trigger` is the hash of the act that caused
    the rekey (the removal, the panic, the weekly tick's last pool act), unknowable when the devices were admitted and so not
    grindable; a flagged device, one a `panic` names, a device on probation and a removed one cannot propose; the next holder by
    the same order after a timeout; two proposals for an epoch: the lowest ticket wins and the second proposer is flagged. A
    malicious proposer can delay a rekey; with the commitment it cannot split or choose who reads.
  * **A holder accepts the wrap set only when it equals** the holder set it computes from the signed roster (acts) and the review
    list: a wrap to a removed device or **to any non-holder is refused** (and flagged: it is a leak), and so is a set that omits
    a member (the omitted member asks again and the next proposer redoes it).
  * **Writers refuse the old epoch** once they have seen the removal or panic: a device that has the act holds the bodies of new
    rows locally ("pending rekey", for at most 15 minutes) and writes them in the new epoch; an honest holder refuses a row
    written in an old epoch by a device after the act it had seen, and a removed device's own rows are cut anyway.
  * **Why not MLS (RFC 9420).** Decided: keep this rekey, with the four fixes. MLS gives post-compromise security by tree
    commits, but it needs a delivery service that gives every member one total order of commits, which our multi-writer replicated
    logs do not have (two concurrent commits fork the group); it brings a large dependency (`openmls`) into a node that links
    nothing like it; and the thing MLS authenticates, the roster, is already a signed act log here. The fixes above give the
    properties this design needs: one key per epoch, who may hold it, no split. Revisit it if the rig (K15) ever measures a
    rekey fork or the per-epoch cost of weekly rekeys becomes a limit.
* **`board_key` rows (a late wrap for one device).** Any holder may publish one, so it is specified: `board_key {board, epoch,
  to, wrap, key_commit}` signed by the publisher's device key; accepted only if the publisher held that epoch, `to` is a
  holder **at that moment** by the roster and the review list (an approval names the epochs it covers, and a wrap for an
  epoch it does not name is refused), and `key_commit` equals the epoch's commitment in its `board_rekey`. A `board_key`
  to a non-holder is refused and flags the publisher `suspend` (it is a leak by a holder, which a probation or a removal
  does not let a device undo).
* **Retroactive exposure, exactly.** Everything a member held up to the cut, in every epoch it had a wrap for, is exposed to it
  forever, including what is on a stolen device. Rekey protects rows written after the cut. A removed device that kept a copy of the
  *current* key before it was removed reads only rows in epochs it held. Weekly epochs bound how much a *future* quiet compromise
  reads before the next rotation, at the cost of one `board_rekey` row a week per board.
* **Metadata, per pool, still visible to every member:** the pool id, the roster, the act log, the board ids, which devices hold
  which board (wrap sets), origins, sequence numbers, times, bucketed sizes and who dials whom. Outside the pool: the network
  operator sees TLS to pinned certificates; the tailnet's coordination server sees which devices connect, by address and time
  (`docs/pools-and-serving.md` 4.4); the push service sees ciphertext and an opaque id (`docs/phone-approval.md`). Whether a
  board id reveals a project's name is not verified here. **Not hidden:** that a member exists and talks.
* **Logs, caches and swap on a device.** Core dumps off (verified). Swap is the operating system's (FileVault encrypts it on macOS;
  BitLocker and a Linux crypt swap are the owner's; `mlock` for keys in the node is part of C9). KV caches and slot files of the
  model server: `serve/backend.py` has a slot save path; whether a served prompt's state lands in a slot file is **not verified
  here**, and slice C14 greps every file the device writes for a planted marker, as `docs/pools-and-serving.md` 7.4 specifies, and
  erases slots on a pool or member switch. Test shards write job directories under the state root (`shard_run.py`); the sandbox
  of C1 gives each its own scratch, deleted at the end.
* **Secrets never in a board.** The relay's credential screen (`screen`), the redaction module and the rule that no token, key or
  password is written to an entry stay; sealing is not a reason to relax them, because a member that holds the key reads the
  row.

**Slice and friction.** C9, C10, C14. Friction: none for the owner's devices; a review tap for a stranger's.

## 10. Tier 9: integrity: a compromised member that lies

**Today.** Per-origin signed, hash-chained logs: a member cannot forge another's row, rewrite its own history without a fork the
others see, or write past the cut of a removal (`gov/logs.rs`). What remains true is whatever a member says inside its own
rows and replies: a test result, a capacity figure, an offer, a model identity ("nothing proves the model named is the model run",
`docs/pools-and-serving.md` 7.6), a build claim (closed in C3), a clock. `probe.rs` and the broker's calibration measure some
capacity.

**Defence (slice C12).**

* **Redundancy for sensitive work.** A result that gates a landing, signs a release or feeds a decision is run on two devices
  and compared (the junit result and the tree hash it ran on). Other results stay single, as now. The cost is one extra run
  for gate work only, and is paid from idle capacity. **Who the second runner is, by pool:**
  * *Two or more people's devices:* a device of a different person from the first runner.
  * *One owner, two or more devices:* a different device of the owner's (never the lander twice); a compromised device is one
    of the two, so the two runs disagree and neither result is taken.
  * *One owner, one device:* the earlier text fell back to "the lander itself", which is the compromised party. A one-device
    pool has no second party, so its gate result is labelled **single-party** on the card and never counts toward a release of
    the root core, which the phone signs (8.2). Two clean-room runs in separate sandboxes on that device check determinism
    (a flaky test, a corrupted cache) and nothing about honesty, and the card says which it did.
* **Canaries, derived from the real tests, not planted by the sender.** A synthetic always-fails test is recognisable by the
  host (its name, its fixtures, its location, the absence of history). A canary here is a **mutation of a real test of the same
  tree** (an assertion flipped, a fixture value changed, a code path the real test exercises broken in the test's own copy),
  chosen by a seeded draw the host cannot see, carrying the real test's name, file and fixtures with the mutation applied
  after the draw, and a **mutation of a passing test that stays passing** as the control. The host returns the result of the
  real run, and a device that returns green for a mutated test that must fail, or red for the control, has failed a canary.
  The draw and the mutation are made by the party that sends the shards, never by the host being checked, so a compromised
  host cannot tell a canary from a real test and a compromised sender only fools itself.
* **N strikes, with a human clear for flakiness.** One failed canary is a strike, not a flag: the shard is redone elsewhere and a
  quiet line records it. A flag `suspend` (`docs/flags.md`) is raised at **3 strikes in 7 days**, or at once when a mutated test
  that failed on a second, unsuspected device is green on the suspect, and its results are void and
  redone elsewhere (`docs/pools-and-serving.md` 7.7). A strike from a test that is itself nondeterministic (it disagrees
  between two known-good devices) is listed as `flaky` and is not counted. A flag a detector raised stays raised by the
  detector's rule, and **a person may clear it** from the review list with the evidence shown (the two results and the
  mutation), one tap: a detector can never clear a flag, a person can (`docs/flags.md`). Canary rows are indistinguishable from
  the rest.
* **Capacity checks.** A device's offered capacity is checked by a short measured probe when it is admitted and at random times
  afterwards, against a tolerance; a device that overstates is shown by what it measured. Offers are already device-signed and
  expire in two minutes.
* **Reputation feeds the trust ledger.** Every canary or redundancy failure is a flag and a `reputation_event`; a device's
  standing narrows what it is sent (`docs/earned-trust.md`), and a flag a detector can raise it can never clear
  (`docs/flags.md`). Induced signals never reach the ledger, as `docs/compromised-agents.md` 9.1 requires.
* **Quarantine.** A flagged device's results, offers and leases are refused at the next frame and its pending work is redone;
  removal by a person is the only exit.

**Friction:** none; a strike needing a clear is one line in the review list. **Not solved:** a device that lies only where
nothing is checked, and **collusion between two different people's devices that return the same wrong result** (two devices
that agree on a wrong answer pass redundancy), both named in section 16.

## 11. Tier 10: lateral movement through the pool itself

| Path | What a compromised device can do today | Defence |
|---|---|---|
| `shard_call` and files | Run its tree on any device that allows it (F3), as that user, unsandboxed; supplies no argv, environment or working directory, but the tree is code | C1: host-side sandbox (`poolhouse.sandbox`: read the tree and the interpreter, write scratch, `Net.deny()`, minimal env, limits), allow list derived from the signed roster, a test that a shard cannot read `node/`, the keystore or the home and cannot reach the network |
| Leases | Another device's resource is `denied`; its own claims are board entries | none needed; a flood is the rate limits |
| Landing | Entries from a device count only if it is on this device's `land_trust.json` (a file) and an independent review and a gate follow | C1 makes the list derived from the roster; the lander's gate runs the branch's code in a sandbox with no credentials (C6) |
| Serving (designed) | A served call gives the serving device every prompt, and a caller a reply it must treat as data | `docs/pools-and-serving.md` 7.3 to 7.7; C14 for residue, C12 for the answers |
| The build path | F1, F2 | C2 to C5 |
| Joining | A delegate's admits, an open join's admits | C10 (no key before review), the rates of `docs/pools-and-serving.md` 1.5 |
| GitHub credentials | Push to the development branch, run workflows; no rulesets, no secrets | C13, and K1 removes the push's reach to followers |
| The agent's identity on other devices | A session's token works on its own device only | `docs/node.md`, "Identity" |

**Least privilege for credentials (K13, slice C13).**

* **One push credential, on the lander only.** The App installation token (an hour) is minted per landing batch by a helper that
  holds the App's key in the keystore; other devices that run agents hold no push credential and fetch with none or a read-only
  one. The fine-grained token (up to a year) is the weaker option and is used only until the App exists; both are named in
  `scripts/github-protection --check --agent`, which gains a per-device mode and fails on a device that holds a push credential
  and is not the lander.
* **The owner's own `gh` login and ssh keys are on no device that runs agents.** The check reports it; moving it is the owner's
  (section 15).
* **The lander's gate runs branch code in the nested sandbox** with no keystore, no credentials and no network but the package
  hosts, so the code under test cannot reach the token or the signer's key (C6). Without S0 the lander is one device with the
  weaker separation.
* **Per-device scope.** Standing grants already name a device; the `shard_call` and `land_control` of the bundle are limited by the
  signed spend limits of `docs/compromised-agents.md` 5.1 once they exist, and a device flagged in section 10 loses them at once.

**Friction:** none visible; the sandbox may fail a test that needed the network or the home, which is a defect found by running
the suite under it before it is enforced (C1's first step).

## 12. Tier 11: the panic button

**The button.** One tap on the phone, "Something is wrong", with a second screen that is optional: the device list with the
suspect devices pre-ticked (flagged, not the person's own) and "this phone" and "this agent" as choices. One biometric signature
(`BIOMETRIC_STRONG`, K10) signs a `panic` act. Everything below is applied by each device itself on first sight of the complete
act; none of it is a hold, because every effect only reduces power.

```
panic { id: random 16 bytes, level: 1|2, devices: [fingerprint ...], sessions: [lineage ...], reason: short quoted text,
        freeze_until_ms }
panic_end { panic: the id of the panic it ends }
```

| Level | Effects (in this order, each persisted before the next) |
|---|---|
| 1, freeze | (1) **Freeze non-admin acts:** no `admit` (a delegate's or an open join's), no new `delegate`, no new offer, share or standing-grant use for an outward action, no `shard_call` or `serve_call` out, none taken in, no `release` act except the root's; local work on a device's own boards, its own leases and its own sessions continues. (2) **Suspend every delegate and standing grant:** each grant entry reads `suspended` until a person re-signs it; every release delegate ends. (3) **End the compromised sessions' own tokens:** every session token of each lineage in `sessions` (by default the lineage of every session on a named device and of "this agent" when ticked) is invalidated on every device that holds its hash, the loopback cookie of a named device is invalidated, and the session's standing grants are among those suspended in (2); the owner's own app signs in again silently with its device key. A freeze that left the compromised session's token alive would let the session keep spending what the freeze did not name. (4) **Hold the build:** devices stay on the release they run until a `release` act signed by the root after the panic. (5) **Write the incident record** (below). |
| 2, cut off (from C11) | Level 1, plus (6) **remove each named device** (the existing `remove`, with its log cut, filled in by the node that submits it), and (7) **rotate every board key** to the holders left, with all unreviewed devices excluded (section 9, `board_rekey`). **Level 2 always rekeys**: a level 2 that removed devices and left the keys would promise a containment it does not give, so it does not exist before sealing (C9, C10) does. Until C11 the button on a lost device is the removal and the effects of level 1 for that device (section 5), and its sentence says no key was rotated. |

The existing reissue (`docs/pools-and-serving.md` 1.9) is level 3 ("start over with these devices"): not rebuilt here.

* **Replay and rate.** A `panic` carries its own random `id` and the roster epoch it was signed against; a device applies a given
  `id` once, so a replayed old `panic` is a no-op, and `panic_end` **names the `panic` it ends** and ends nothing else (an old
  `panic_end` replayed against a later panic does nothing). A level 1 freeze begun within 10 minutes of a `panic_end` is honoured
  only if it names a device or a session the first did not (a freeze loop that only wears the owner out is a no-op with a
  line); a level 2 act that names a device not yet removed always applies. A device honours at most 6 `panic` acts a day from one
  key, after which it still applies the named removals and shows the rest as "the phone is panicking: 6 today". Panics only reduce
  power, so the rate is about the owner's attention, and nothing is lost by a limit that still applies removals.
* **What the owner feels.** One tap and one sentence on the phone ("Your pool is frozen. 7 of 8 devices have confirmed."). The
  agents keep working on their own boards; outward work waits. Recovery is `panic_end` (one tap, "All clear") and the
  monthly-renewal approval re-signing the grants and the delegates (one tap, the same screen as the monthly renewal), plus a
  root-signed release if one was held. **Total: three taps to be back, none to be safe.**
* **The incident record.** A `flag` entry (`docs/flags.md`) `code: incident` on the pool history board with references only: who
  tapped (a key fingerprint), the act's id, the reason (quoted, untrusted), and the signed `panic_ack` rows that follow. Each
  device writes `panic_ack {panic, state, grants_suspended: n, tokens_ended: n, removed: [fp], rekeyed: [board], release: seq}`
  under its own key, from its own clock; the phone shows a checklist, "garage-pc has not answered; it applies this the moment it
  comes back". A compromised device can lie in its ack and cannot hide the others'.
* **Offline devices** process the `panic` before any other exchange when they return, since acts come first in the member
  exchange. **A device that ignores it** is a compromised device: level 2 removes it, and the members it dials refuse it.
* **Freeze expiry.** The freeze ends with `panic_end`, or by itself after 7 days with a banner each day; suspended grants, ended
  delegates, ended tokens and the held build never return on their own, so a forgotten freeze ends safe.
* **Panic is not a lever.** A thief with the phone and the owner's biometric can panic: that is an inconvenience to the owner
  and the same thief could do more. An agent or a user-level process holds only a weak key, which signs no panic (a later option,
  level 1 only, once per 24 hours, lifted by a strong tap, is listed in section 16 and is not in the first slices). No act of
  `panic` loosens anything.
* **Rig test (K15).** In `scripts/pool-check` (the rig is the person of its pool, in-memory key, `docs/pool-check.md`): a pool of
  three devices where one is made hostile by a script that, in turn, requests an admit through a delegate, sends a shard, writes
  a release past its limit, claims a high `build_seq` and reads a board. Steps: `panic-contains` (after the act every online
  device refuses each, and the time from the act reaching a device to its `panic_ack` is recorded as a measurement that names its
  command), `panic-offline-device` (a device off during the act applies it first on return), `panic-removed-reads-nothing` (after
  level 2 the removed device decrypts no row written after the cut and the surviving devices do), `panic-grants-stay-off` (a
  suspended grant is refused until re-signed), `panic-tokens-ended` (the compromised session's token is refused at once),
  `panic-replay-is-a-noop` (a replayed `panic` and a `panic_end` naming another id do nothing), `panic-end-restores` (one
  `panic_end` and one renewal bring the owner's agents back), `panic-weak-key-refused`.

## 13. The threat table

Friction is what the person sees in the normal journey. `0` means nothing visible.

| Tier | Asset | Today (verified) | Gap | Defence | Slice | Friction |
|---|---|---|---|---|---|---|
| 1 agent | grants, approvals | node floors, taint, S0 | `docs/compromised-agents.md` | that design | S0..S15 there | see there |
| 2 user account | device key, boards, tokens | readable by the user (keystore, files) | no authority separate from the account | roster-derived allow lists (a delegate's admit is never own), host sandbox, fewer boards, clone flag, node identity (spike) | C1, C9, C15 | 0 (one dialog shared with S0) |
| 2 user account | the build | local commit builds and is advertised (F1, F2) | `admit` accepts the local tip; claims count | verified `release` only; `local` builds take no work; no merge into a working tree | C3 | 0 |
| 3 root | everything the device holds | all of it | cannot be protected | bound the radius: boards held, grants, keys not held, probation, rekey on eviction | C8, C9, C10 | 0 until it happens |
| 4 stolen device | boards on disk, device key | plaintext logs; key in keystore; removal immediate | no one-tap loss, no disk check, no rekey | "Lost or stolen" tap, sealed boards, key-delete wipe (never the last copy), rekey, disk check read | C8, C9, C10 | one tap on a loss |
| 5 phone | admin, release, panic keys | per-use biometric; sole strong key is the residual | strength declared, P-256 unverified, PIN fallback, nobody sees acts, no hold on irreversible acts | P-256, attestation, `BIOMETRIC_STRONG`, tripwire, 6-hour hold on irreversible root acts in a one-key pool cancelled by the phrase | C7a, C7, C2 | 0 (inside the add-a-phone scan); a wait only on a charter change |
| 6 admin or delegate | pool membership and releases | holds, vetoes, 10 admits a day for 30 days; no release role | an admitted device reads all; roles blur | separate `release` role, no key before review, `pull` refused on probation | C2, C10a, C10 | review line for strangers' devices |
| 7 supply chain | every follower's code execution | push to origin = build on every device; no verifier on the path | no signature the attacker lacks; the verified commit is not the build input | signed `release` act, three signatures from three devices, content hash from the objects, staging build, artifact fetch by hash, rollback and ancestry rules, soak | C2..C6 | 0; one tap per root-core landing (existing) |
| 7 supply chain | the work gate | one member's `build_seq` freezes all work | claims count | verified `seq` only | C3 | 0 |
| 8 confidentiality | boards | plaintext, read right = membership | no per-board keys | sealing, wraps, key commitment, rekey, probation, checkpoint on join, every derived store | C9, C10 | 0 |
| 8 confidentiality | prompts, caches | in the serving device's memory, maybe slot files | unverified residue | slot erase, marker grep | C14 | 0 |
| 9 integrity | results, capacity, offers | signed per origin; contents unchecked | a member's own claims | redundancy by pool shape, mutation canaries, strikes, probes, ledger, quarantine | C12 | 0 |
| 10 lateral | shard hosts | sender's code, user, no sandbox | one device is all | sandbox, roster allow list | C1 | 0 |
| 10 lateral | GitHub credentials | App token or PAT on agent devices | broad, long-lived | one lander, minted per batch, none elsewhere; origin rulesets | C13 | 0 (see 14, C13, for signed commits and 2FA) |
| 11 panic | the whole pool | nothing | no freeze, suspend, token end, rotation, record | `panic` act (id-bound), levels 1 and 2, acks, `panic_end` | C8, C11 | one tap in; three to return |

## 14. Slices, in dependency order

Each slice is small enough to land alone, written test-first (the test is seen to fail on the tree before it) and names the
mutation that must turn it red. A slice that adds a node method, a hook or a route adds its stanza to
`docs/redteam/coverage-map.toml` in the same commit, and a slice that adds an act kind adds its test to the governance suite
(`app/poolhouse-node/tests/gov_*.rs`) and a step to `scripts/pool-check`. `S0` is the sandbox of `docs/compromised-agents.md`;
**GOV** is governance slice 1 (built); **P1** is the phone approval; **SEAL** is C9; **REL** is C2.

| # | Slice | Scope | Test written first (fails today) | Mutation check | Depends on |
|---|---|---|---|---|---|
| C13 | Credentials and origin rulesets | the per-device mode of `scripts/github-protection --check --agent`, the App token minted per batch, no push credential outside the lander; the origin rulesets on the development branch (below) | `a_device_that_is_not_the_lander_holds_no_push_credential_per_the_check`, `the_token_is_minted_per_batch_and_expires`, `the_development_branch_ruleset_blocks_deletion_and_force_push_per_the_check` | Check the lander only; mint a long token; drop the ruleset check: each fails | none |
| C1 | Host sandbox and roster-derived allow lists | `shard/host.rs` starts the executor under `poolhouse.sandbox` (no read of state, keystore or home, `Net.deny()`, scratch only, limits); the shard allow list and `land_trust` derive from signed `admit` acts by the own-device rule (D2.1: a strong key or a trusted-path scan, **never a delegate's admit**) and not from files; another person's device needs a strong-signed approval; the suite is run under the sandbox first and fixed | `a_shard_cannot_read_the_state_dir_the_keystore_or_the_home`, `a_shard_cannot_open_a_socket`, `an_edit_of_shards_json_adds_no_device`, `a_device_admitted_by_the_owners_strong_key_is_allowed_and_another_persons_is_not_until_signed`, `a_delegate_admitted_device_is_never_own`, `a_shard_scratch_is_gone_after_the_run` | Drop the read denial; allow the network; read `allowed` from the file; accept an unsigned approval; count a delegate's admit as own: each fails | none |
| C7a | P-256 and `alg` | `gov/keys.rs` and the act signature branch on `alg` (Ed25519, P-256); an admin key and a `release`-role key may be P-256; the rig signs both | `a_p256_admin_signature_verifies_and_a_wrong_algorithm_does_not`, `a_p256_key_in_an_ed25519_slot_is_refused`, `an_unknown_alg_is_refused` | Ignore `alg`; verify P-256 as Ed25519: each fails | GOV |
| C2 | The `release` role and act | `gov/`: `role: release` and `role: builder` in the charter (own acceptance, sensitive to add), the act body and its three signatures, verification (the inert list, the protected set and the root core recomputed by the verifier from the two trees; the root's pre-authorisation of that exact tree for the root core; `seq` (a cancelled act burns its seq), `prev`, `prev_commit` ancestry or a root-signed `rollback`, equivocation, `floor`, `not_after` for new adoption only), soak hold and `cancel`, caps, the 6-hour hold on irreversible root acts in a one-key pool and the phrase's immediate cancel and phone removal, `release_role_rotate` by the phrase (7 days), `describe` sentence, `pool_status.release`; verify only, no follower change | `a_forged_release_is_refused`, `a_below_threshold_release_is_refused`, `a_delegate_signed_release_with_one_signer_is_refused`, `a_review_from_the_landers_device_is_refused`, `an_attestation_from_an_unpinned_builder_key_is_refused`, `a_landing_touching_device_rs_is_protected_and_needs_three_signatures`, `a_root_core_release_without_a_preauthorisation_is_refused`, `a_preauthorisation_for_another_tree_is_refused`, `a_release_that_does_not_descend_from_the_running_commit_is_refused`, `a_root_signed_rollback_is_accepted_and_a_delegate_signed_one_is_not`, `two_acts_at_one_seq_are_an_equivocation_that_follows_neither`, `a_cancelled_act_burns_its_seq`, `a_lower_seq_release_is_refused`, `a_lapsed_act_is_not_newly_adopted_and_stops_no_running_build`, `a_release_waits_its_soak_and_a_cancel_ends_it`, `an_admin_without_the_role_signs_no_release`, `a_signer_flag_is_never_read`, `an_irreversible_root_act_is_held_6_hours_in_a_one_key_pool`, `the_phrase_cancels_a_held_act_at_once_and_removes_the_phone`, `removing_every_other_device_is_held_and_removing_one_is_not`, `the_phrase_rotates_the_release_role_only_after_7_days_and_a_strong_key_cancels_it` | Protect only named files (the deny-list); accept the lander's own review; accept any builder key; skip the ancestry check; let a delegate sign a rollback; reuse a cancelled seq; stop a running build at `not_after`; hold one removal; make the phrase's cancel wait 7 days; let the phrase rotate at once: each fails | GOV, C7a (real signing needs P1; the rig key until then) |
| C3 | The follow rule | `build_update` follows the verified release act; the content hash of the commit from `ls-tree` and the blobs (not `git archive`) before any build step, submodules and path tricks refused; no merge into a working tree; `held` state and notice; `admit` accepts only named commits for automatic and delegated callers, `--local` for a person; `local` builds advertise `local`; member build claims count for nothing; last three runtimes and revoke; **enforcement is off until the pool has a verified release act** (a pool with none is `unenforced`, behaves as today, and `runtime status` says so, so no pool freezes its build the day C3 lands) | `an_unsigned_commit_on_origin_is_not_built`, `a_signed_commit_with_another_tree_is_not_built_and_no_build_step_ran`, `the_tree_hash_covers_a_file_marked_export_ignore`, `a_submodule_is_refused`, `a_follow_never_merges_into_a_working_tree_and_runs_no_post_merge`, `a_lying_members_build_seq_freezes_no_work`, `a_local_unpushed_commit_makes_no_other_device_stale`, `a_revoked_release_rolls_back`, `a_release_past_not_after_is_not_newly_followed`, `a_pool_with_no_release_key_follows_nothing_and_says_why`, `a_pool_with_no_release_act_yet_keeps_following_as_today` | Check the tree after the build; hash `git archive`; trust `FETCH_HEAD`; count a member's claim; let `--local` advertise; enforce with no act: each fails | C2 |
| C4 | The delegate and the heartbeat (lands with C3) | `delegate` for `release`, the signer on a device with no agent sessions, the landing record as input (a review counts from a device other than the lander's), the builder attestation and the two-builder agreement, the semantic card and the standing grant for lockfile-only bumps, the root-core card merged into the landing card as a pre-authorisation, notices for a new delegate's first 24 hours, the daily line including the soak, the S0 precondition and the hourly ask without it | `an_ordinary_landing_is_signed_and_asks_nothing`, `a_root_core_landing_asks_once_and_the_delegate_signs_that_tree`, `the_signer_refuses_when_the_builders_disagree`, `the_signer_refuses_a_commit_with_no_landing_record`, `the_signer_refuses_a_review_from_the_landers_own_device`, `the_signer_refuses_without_a_gate_result_at_that_sha`, `a_release_past_the_delegates_rate_is_refused_by_every_device`, `a_new_delegates_first_day_of_releases_each_notify`, `a_lockfile_bump_with_no_new_crate_is_signed_under_the_grant`, `a_new_crate_or_a_build_rs_asks_with_a_semantic_diff`, `a_bump_published_under_72_hours_ago_asks`, `a_cancelled_soak_is_an_urgent_line_in_the_summary`, `without_s0_every_release_asks_hourly_and_the_install_offers_s0` | Sign on one builder; sign without a landing record; count a same-device review; skip the rate; sign without S0; let the grant cover a new crate; drop the age check; omit the soak line: each fails | C2, C3, C7a, P1 (S0 for no-tap) |
| C10a | Probation on `pull` (no cryptography) | the honest holder checks the signed roster and the review list and serves a delegate-admitted or open-joined device on probation only the pool history board | `a_probation_device_pulls_nothing_but_the_history_board`, `a_reviewed_device_pulls_its_boards` | Serve a probation device any board: fails | GOV |
| C7 | The phone's strength | Android Key Attestation chain verified at add, strength recorded as attested, `BIOMETRIC_STRONG` class for panic, release, removal and admin acts, tripwire notices on the owner's other devices, the one-key banner, **the founding card shows the installer's own hash and the build words** (15.1) | `a_software_key_cannot_be_recorded_strong`, `a_removal_shows_on_every_other_device_within_a_minute`, `a_device_credential_signs_no_panic`, `the_founding_card_shows_the_installers_hash_and_the_build_words` | Record the declared strength; drop the attestation check; send no notice; omit the hash: each fails | P1, C7a, GOV |
| C8 | Panic, level 1, and "Lost or stolen" | `panic` and `panic_end` acts (id-bound), freeze, suspended grants and delegates, **the named sessions' tokens ended**, named removals for "Lost or stolen" (no rotation claimed), `panic_ack`, the incident flag, the device list with the disk-encryption line, rig steps `panic-contains`, `panic-offline-device`, `panic-grants-stay-off`, `panic-tokens-ended`, `panic-replay-is-a-noop`, `panic-end-restores`, `panic-weak-key-refused` | the rig steps; `a_panic_act_below_threshold_is_refused`, `a_frozen_device_refuses_each_outward_act`, `a_suspended_grant_is_refused_until_resigned`, `a_level_1_ends_the_named_sessions_tokens`, `a_replayed_panic_is_a_noop`, `a_panic_end_for_another_id_does_nothing`, `a_panic_loop_is_rate_limited_but_still_removes`, `a_restart_keeps_the_freeze`, `a_panic_loosens_nothing` | Apply removals before the freeze is persisted; let a weak key sign; let a grant resume on its own; leave the token alive; drop the id binding: each fails | GOV, P1, C7 |
| C5 | Reproducibility, then the pin | the builder, the measurement script and its dated document, `app/rust-toolchain.toml` and the `toolchain` archive hashes, `artifacts` pinning where the measurement says it holds, `build_report` rows elsewhere, the artifact store a follower fetches from by hash | `two_builds_of_the_node_on_two_devices_have_one_hash` (a measurement, recorded, red until it holds or the target is dropped), `a_built_hash_that_differs_selects_nothing`, `a_target_the_act_does_not_list_is_reported_not_pinned`, `a_follower_with_no_toolchain_fetches_the_artifact_by_hash_and_refuses_a_wrong_hash`, `the_toolchain_archive_hash_is_checked` | Skip the compare; pin an unmeasured target; skip the artifact hash; skip the toolchain hash: each fails | C3 |
| S0, C6 | Build and gate isolation | S0 (its own document); the staging export, the scrubbed environment, the nested sandbox for builds and the lander's gate (no keystore, no credentials, network only package hosts), `--no-build-isolation` with hash-pinned build requirements, `--require-hashes`, the lock hashes checked | `a_planted_build_script_cannot_write_outside_the_sandbox`, `a_planted_rustflags_or_pip_variable_is_not_seen_by_the_build`, `a_build_reads_no_untracked_file_of_the_checkout`, `an_unhashed_build_requirement_is_refused`, `the_gate_cannot_read_the_push_token`, `a_changed_lock_without_the_root_is_refused` | Build outside the sandbox; pass the environment through; build in the checkout; skip the lock check: each fails | S0, C3 |
| C9 | Per-board sealing | device cards (X25519, signed), board keys per epoch, sealed bodies with bound associated data and the key-committing `kc`, wraps, holder checks on `pull`, keys wrapped under the keystore subkey, `mlock`, the history board stays clear, padding buckets; **every derived store sealed or dropped**; the **signed sealed checkpoint** for a joiner | `a_member_with_no_wrap_reads_ciphertext_only`, `a_marker_written_to_a_board_is_in_no_derived_store_in_the_clear` (attachments, search index, graph store, folded state, caches, each), `a_sealed_body_moved_to_another_row_fails_to_open`, `a_row_opens_under_one_key_only`, `an_honest_holder_serves_no_rows_to_a_member_with_no_wrap`, `a_joiner_folds_the_same_state_from_a_checkpoint`, `a_checkpoint_two_holders_disagree_on_is_refused`, `the_pool_history_board_is_readable_by_every_member` | Seal with no associated data; drop `kc`; serve rows to any member; log the key; leave the index open; accept one holder's checkpoint: each fails | GOV, P3 (done); independent of C2 to C8 |
| C10 | Rekey, probation and wipe | `board_rekey` with `key_commit`, the exact-holder-set rule, the ticket-based proposer, refusal of the old epoch after a removal, `board_key` row rules, rekey on removal and panic and weekly, no key for a delegate-admitted or open-joined device before review, history-on-join choice, key-delete wipe on a signed removal and after 30 days of monotonic running time unheard (never the last copy) | `a_removed_device_decrypts_no_row_after_the_cut`, `a_wrap_holding_another_key_is_refused_by_its_commitment`, `a_rekey_naming_a_removed_device_is_refused`, `a_wrap_to_a_non_holder_is_refused_and_flags_the_publisher`, `a_rekey_that_omits_a_member_is_redone_by_the_next_proposer`, `a_grinding_fingerprint_does_not_win_the_proposal`, `a_flagged_or_panic_named_device_cannot_propose`, `a_writer_refuses_the_old_epoch_after_it_saw_the_removal`, `a_board_key_row_for_an_unapproved_epoch_is_refused`, `a_delegate_admitted_device_holds_no_key_until_reviewed`, `a_wipe_deletes_the_keys_and_the_logs_are_unreadable`, `the_lease_wipe_never_deletes_the_last_copy`, `a_clock_change_wipes_nothing_and_a_drawer_laptop_does_not_age` | Include the removed device in a wrap; compare only the wrap set; propose by lowest fingerprint; give a wrap before review; wipe a sole holder; run the lease on the wall clock: each fails | C9 |
| C11 | Panic, level 2 and rotation | level 2 removes the named devices and **always** rotates every board key; "Lost or stolen" becomes a level 2 for one device; the rig step `panic-removed-reads-nothing`; a pre-panic release is not followed after it until re-signed | `a_level_2_rotates_the_keys`, `panic_removed_reads_nothing`, `a_release_before_the_panic_is_not_followed_after_it_until_re_signed` | Skip the rotation; follow a pre-panic release: each fails | C8, C10, C2 |
| C12 | Verified results | redundancy by pool shape (a single-device pool's result is `single-party`), canaries as mutations of real tests with a control, 3 strikes in 7 days, a person's one-tap clear, capacity probes, flags, ledger events, quarantine and redo | `a_one_device_pools_gate_result_is_single_party_and_gates_no_root_core_release`, `a_canary_is_a_mutation_of_a_real_test_and_carries_its_name`, `one_strike_redoes_and_three_flag`, `a_person_clears_a_flag_with_one_tap_and_a_detector_cannot`, `a_flagged_devices_results_are_void_and_redone`, `an_induced_event_never_reaches_the_ledger` | Plant a synthetic test; flag at one strike; let the detector clear; skip the redo: each fails | `docs/flags.md` F1 and F3, C1 |
| C14 | Serving residue | slot erase on pool and member switch, the marker grep of every file the device writes, swap and core checks reported by `pool_status` | `a_planted_prompt_is_in_no_file_after_a_call`, `a_slot_is_erased_on_a_switch` | Skip the erase: fails | `docs/pools-and-serving.md` slice 11 |
| C15 | The node under its own identity (spike) | Measure what breaks, then decide; shares S0's dialog | the measurement; if go: `the_users_processes_cannot_read_the_device_key_or_the_logs` | n/a for the spike | S0 |
| C16 | Hardware-held device keys | TPM (Windows, Linux) behind the keystore interface; the Secure Enclave stays blocked on the paid account | `a_tpm_held_key_does_not_leave_the_device` | Export the key: fails | none |

**C13 and the origin rulesets, and what each costs.** The lead has added a ruleset on the development branch: **no deletion and
no force push**, which costs nothing (the landing runner fast-forwards and never rewrites history). Two more rules are the
owner's alone and each has a price. **Required signed commits:** every commit that reaches the branch must carry a signature
GitHub can verify, so the lander needs a signing key (a commit the App writes through the API is signed by GitHub; one an agent
rebases locally is not and must be re-signed by whoever rebases), and a worker's rebase onto `origin/<dev>` becomes a step that
needs the key. It is defence in depth only: with K1, followers trust a `release` act and not origin, so unsigned commits cannot
become code on a follower; the rule protects provenance and review on GitHub. **Two-factor authentication on the account** (a
passkey or a hardware key): a one-time step by the owner, then a tap on each new sign-in; it stops an account takeover, which
no longer runs code on followers but still destroys or withholds the repository (8.4). Neither is a gate on this design.

**Order of arrival, revised after the attack review.** **Now, with no build needed:** C13 and the origin rulesets (the
credentials check and the ruleset above). **Then:** C1; C7a (P-256 and `alg`, split out of C7); C2; C3; C4. **C4 lands with
C3**: C3's enforcement stays off (a pool with no verified release act is `unenforced` and behaves as today) until C4 and C7a
can sign the first act, otherwise every pool would freeze its build the day C3 lands. **Then** a small C10a (probation refusal
on `pull`, no cryptography). **Then** C7 (attestation), C8, C5, S0 and C6, C9, C10, C11, C12, C14; and C15 and C16 when
measured. S0 is **not** needed first: C2 and C3 only verify. The reasons: C13 changes no code path; C1 is small, needs nothing
and removes the worst lateral path; C7a is the prerequisite for any phone-held key; C2 and C3 close F1 and F2 and C3 is the
first slice that changes what a device runs; C4 makes C3 free for the owner's day; C10a stops a Sybil's read before sealing
exists; C8 gives the owner the button before sealing exists, because freezing and removing need no keys; C9 and C10 are the
largest and independent of the update path; C6 needs S0; C11 joins the panic to the keys; the rest are valuable and not
urgent. **Depends on S0:** C4 (loosening), C6, C15. **Depends on sealing (C9):** C10, C11. **Depends on release signing (C2):**
C3 to C5, C11. Where one slice needs another's output the slice that lands first lowers a recorded number: the red-team
coverage map gains the new tiers in C1 and no slice raises a ceiling.

## 15. What the owner does, and what the agents do

**The owner**, and nobody else can:

1. **Hold a phone with the app, and add a second strong key** (a second phone is enough): one scan and one biometric, nudged
   weekly until done. This is the only thing that makes a coerced or stolen phone survivable without a wait.
2. **Write the recovery phrase down once,** offline. It cancels any held act and removes the phone at once, and it rotates the
   release role (or adds a key) after 7 days.
3. **Tap**: the founding (it lists the release role in one line), a removal, a root-core landing, a panic and the all-clear,
   and the monthly renewal that now includes the release delegate and the lockfile grant. Beyond the existing review of a
   stranger's device, nothing else in this document asks for a tap; a charter change in a one-key pool also waits 6 hours.
4. **The one system dialog** of S0 (and C15 if it goes ahead). It is the operating system's own and the agent installs the rest.
5. **Account actions that are outward-facing and the owner's alone:** a passkey or hardware key on the GitHub account, required
   signed commits on the development branch if wanted (14, C13), creating the GitHub App (`docs/github-protection.md`), moving
   the owner's own `gh` login and ssh keys off devices that run agents, switching on disk encryption where an agent reports it
   is off, and setting `RELEASE_KEY` for outside releases (`scripts/release-key`, a terminal).
6. **Real-phone checks** (attestation on the actual phone, the tripwire notice, the panic on the real device) in the order the
   rig leaves them.

**The agents** do everything else: write and land C1 to C16, run the measurements, run the delegate, sign ordinary releases,
build in the sandbox, compare hashes, keep the three runtimes, run the canaries, write the rig steps, rotate the keys, write the
incident record, and say each state in one plain sentence. No agent signs as the owner, reads a person's key, answers an
approval or loosens a guard to turn a gate green (AGENTS.md, section 1).

### 15.1 First install and the first key

`RELEASE_KEY` is empty (`fleet/signing.py`), so the phase-1 answer that the installer verifies a project key does not exist,
and the owner's constraint of 2026-10-08 (no paid developer account) rules out Apple's notarization. The trust of the **first
install of the first device** therefore cannot be created by software: it rests on where the owner downloaded the program and
on the operating system's own checks. What this design adds is small and honest:

* **The pool pins its first build at founding, by the founder's key.** The founding charter lists the founder's phone key as the
  release root, and the first `release` act (seq 1) is signed by it for the build the first device runs. Every later build is a
  descendant of it or a root-signed rollback (8.1), so the chain begins at the founder's key and not at the project's.
* **The founding card shows the installer's hash.** The installer prints its own sha256 (as three words and a short hex tail)
  on the founding card, and an agent recomputes the downloaded file's hash with the system's `shasum` or `Get-FileHash`
  (a different program than the installer) and shows it beside the release page's published hash. Matching words are a check
  against a **swapped download**. **An installer that was malicious at the source prints the right hash**, so this is not
  evidence about the source and the card does not claim it is.
* **Every later device checks the build in the join it already does.** The three check words of a join are made from the key
  exchange and **the sha256 of the build the joining device will run, which the member computes from the pool's current release
  act**; the person compares the same three words on two screens, as now, so one comparison covers the join and the build and
  adds no step. A device that fetched the wrong program shows different words.
* **What stays outside:** the downloaded first installer, the operating system's own signature check, and the owner scanning the
  right code on the right screen. `RELEASE_KEY` for outside users stays a terminal step of the owner's (`scripts/release-key`)
  and is not on this path.

## 16. Decisions taken here, and what is not protected

**The five risks that matter most,** in order of reach:

1. The update path (F1): a push to origin or a commit in a primary checkout is code on every follower until C2 and C3 land, and
   after them an ordinary landing is code execution on every follower, so it needs three devices to agree (8.1).
2. Lateral execution (F3): one compromised allowed device is code on the others until C1 lands.
3. A phone used under coercion, or with another person's biometric enrolled: per-use `BIOMETRIC_STRONG` already stops a thief
   of an unlocked phone; coercion signs reversible acts in one burst, and irreversible ones wait 6 hours for the recovery
   phrase to cancel them (K11). Strength is declared, not attested, until C7.
4. Plaintext boards with membership as the read right: a removed, stolen or delegate-admitted device keeps or gets everything
   until C9 and C10 (and C10a for the second), and what it already read stays read.
5. No incident response: nothing freezes, suspends or rotates, and one lying member's `build_seq` can stop all work (F2) until C8
   and C3.

**Decisions taken** (the better option, with the risk the owner should know; none is a question):

* **The update path is signed by a pool key, not the project's `RELEASE_KEY`.** The pool pins its own release root at founding.
  The project key stays for outside releases (phase 1).
* **Everything not on a short inert list is protected, and an ordinary landing needs three devices.** A deny-list of dangerous
  files was wrong by omission. Risk: a pool with fewer than three devices has no delegate for code releases and its owner is
  asked by the phone, batched.
* **Followers fetch artifacts by hash and do not build, unless they are a developer's machine.** Reproducible builds are measured
  before they are enforced and reverse the 2026-10-08 rejection for the update path only. Risk: macOS may not reproduce; the
  answer is then the builder's artifact, written down.
* **An irreversible root act is held for 6 hours in a one-key pool; nothing else is gated.** It replaces "no gate on a second
  strong key". Risk: an owner who changes the charter waits 6 hours; the phrase cancels at once.
* **The ad hoc rekey is kept, with a commitment and a ticketed proposer, and MLS is not adopted** (reasons in section 9).
* **The wipe is a courtesy that never deletes the last copy and does not run on the wall clock.**
* **A weak key signs no panic in the first slices.** A later option is a laptop's weak key signing level 1 only, once per 24 hours,
  lifted by a strong tap, for the day the phone is lost and the laptop is not; its cost is a nuisance freeze by any process of
  the user, so it waits for the measurements of C8.
* **The signer, the lander, the reviewer and the builder are different devices.** With fewer than three the root signs code
  releases. One device: the sandbox is the only separation and an owner with one device accepts a weaker release trust than
  one with three.
* **History on join defaults to the current epoch only for another person's device,** and to all epochs for the person's own;
  a joiner folds from a signed, sealed checkpoint (section 9).

**Not protected** (stated so nobody reads silence as safety; **fixable** marks what a named slice can still close):

1. **Root or the operating system of a device:** everything it holds, serves, signs and says, for as long as it is compromised
   (section 4), and every board and prompt in memory.
2. **What a removed or compromised member already read,** in every epoch it held a wrap for, including a stolen device's disk
   (section 9). Rekey protects the future only. Wipe is a courtesy.
3. **A phone under coercion, or with another person's biometric enrolled, or a modified phone system that is attested away**
   (mislabelled before as "a stolen unlocked phone", which per-use `BIOMETRIC_STRONG` already stops). It signs reversible acts
   (panics, single removals, ordinary releases) in one burst before the owner acts; irreversible root acts are held 6 hours and
   cancelled by the phrase in a one-key pool, and by the second key otherwise. **Partly fixable:** a second strong key.
4. **A person who approves what they did not read,** and an attacker who lines a request up with the person's real words
   (`docs/compromised-agents.md` 15).
5. **The serving device sees every prompt it serves,** and no attestation exists on consumer devices (`docs/pools-and-serving.md`
   7.6); Level 2 of `docs/pool-encryption.md` is not attempted.
6. **A malicious dependency or toolchain** both builders fetch, and **a reviewed malicious ordinary change.** **Partly
   fixable and no longer unprotected:** the toolchain is pinned by archive hash and a bumped crate waits 72 hours; an
   ordinary change needs a lander, a reviewer on another device and a builder to agree (8.4). What remains is a crate that was
   malicious on its first release and three steered agents on three devices accepting one change.
7. **Metadata:** who is in the pool, who dials whom, when, bucketed sizes and which devices hold which board, to every member;
   addresses and times to the tailnet's coordination server (section 9).
8. **A lying member where nothing checks:** single, unchecked results, an answer in a field no canary covers, a model that is not
   the one named; and **collusion between two different people's devices** that agree on a wrong answer, which redundancy
   cannot see.
9. **A user-level attacker on a device until C15:** the device key, the boards it holds and its tokens, for as long as it runs
   as that user (section 3). C1 and C3 stop it becoming code execution elsewhere. **Fixable:** C15 (a spike that may come back
   no-go) and C16 narrow it; it is not a permanent limit.
10. **Denial of service by a member inside its rate caps,** and by a rogue co-admin (a seven-day dispute freezes governance).
11. **Coercion of the owner for reversible acts** (a forced panic or removal); the 6-hour hold covers only the irreversible ones.
12. **A pool that loses its phone and its recovery phrase** has no live `release` key and follows nothing new, and its charter
    cannot change: every device keeps working on its last build, and nothing is taken over. **Partly fixable:** a second
    strong key or a stored phrase (the phrase rotates the release role in 7 days, section 6); it is permanent only when both
    are lost.
13. **The first install of the first device** rests on the download the owner trusts and on the operating system's own checks,
    and an installer that is malicious at the source prints the right hash (15.1). **Partly fixable:** the pool pins its first
    build by the founder's key, the card shows the installer's hash, and every later device checks the build in the join's
    check words; the first download itself is not closable without a paid platform signature.
14. **Side channels,** timing beyond the bucketing of `docs/pools-and-serving.md` 7.4, and hardware attacks.
15. **A disk the owner did not encrypt,** and swap the operating system leaves in the clear.
16. **GitHub's own compromise** beyond the account: a rewritten history is detected only by the tree hash a signed release names.

## 17. Attack-first, documents and words

**This document had one review by someone other than its author, on 2026-10-11, and it returned BUILD-WITH-FIXES.** AGENTS.md
section 1 makes a non-author's written break-it pass a condition of building anything here. The reviewer was told to break it for
each of: a copied or cloned pool (a pool with the same name and another root; a clone of the signer), replay (a stale `release`
act after a withheld pool, a replayed `panic`), a forged or below-threshold signature (a delegate-signed protected-set release; a
weak key on a panic; an admin with no `release` role), a compromised or malicious member (a lying `build_seq`, a rekey that omits
or adds a wrap, a canary-aware shard host), a stolen phone (release root, panic, removals, `panic_end`), a malicious agent and
one that is compromised without knowing it (a lander session that asks the signer to sign, a lure that tells the lead to call
`panic`), a downgrade (an unsigned `--local` build presented as the pool's build; a pool founded with no release key; a held tip
released by an escape hatch), approval fatigue (protected-set cards, a flood of release notices, panics in a row), and takeover
by whoever is merely present (a device that appears with a lower-`seq` signed act, a person with the owner's unlocked laptop).
**The revised text above has not been re-reviewed, and the fixes added new mechanisms** (the three-signature rule, the
checkpoint, the ticketed proposer, the strike rule). A second non-author pass on this text is a condition of building C2, C3, C4,
C9 and C10, and the first thing it is told to attack is each fix below.

### 17.1 The review's findings, where each is fixed, the test that fails first, the mutation that turns it red

| # | Severity and finding | Fixed in | Test written first (fails today) | Mutation check |
|---|---|---|---|---|
| 1 | HIGH. The protected set was a deny-list: an ordinary landing (`device.rs`, `net*.rs`, `shard/host.rs`, `log.rs`, `build.rs`) was still code execution on every follower | 8.1 tiers and three signatures, 8.2, K2, C2, C4 | `a_landing_touching_device_rs_is_protected_and_needs_three_signatures`, `a_delegate_signed_release_with_one_signer_is_refused`, `a_review_from_the_landers_device_is_refused`, `an_attestation_from_an_unpinned_builder_key_is_refused`, `a_new_delegates_first_day_of_releases_each_notify` | Protect only named files; accept the lander's review; accept any builder key; drop the notice |
| 2 | HIGH. Downgrade and rollback, equivocation, a down signer as a denial of service | 8.1 order, downgrade and rollback, K16, C2, C3 | `a_release_that_does_not_descend_from_the_running_commit_is_refused`, `a_root_signed_rollback_is_accepted_and_a_delegate_signed_one_is_not`, `two_acts_at_one_seq_are_an_equivocation_that_follows_neither`, `a_cancelled_act_burns_its_seq`, `a_lapsed_act_is_not_newly_adopted_and_stops_no_running_build` | Skip `merge-base --is-ancestor`; let a delegate sign a rollback; reuse a cancelled seq; stop a running build at `not_after` |
| 3 | HIGH. A delegate's admit could yield "own" for `shards.json` and sealing, contradicting probation | 3 (D2.1), 7, C1, C10a | `a_delegate_admitted_device_is_never_own`, `an_edit_of_shards_json_adds_no_device` | Count a delegate's admit as own |
| 4 | HIGH. The verified commit was not the build input (a build in the user's checkout; `git archive` attributes; submodules; the environment; an unhashed build backend; a toolchain that does not exist as a file and is not pinned; the lock is `app/Cargo.lock`) | 8.1 follow rule, 8.3, K3, K4, C3, C5, C6 | `the_tree_hash_covers_a_file_marked_export_ignore`, `a_submodule_is_refused`, `a_follow_never_merges_into_a_working_tree_and_runs_no_post_merge`, `a_planted_rustflags_or_pip_variable_is_not_seen_by_the_build`, `a_build_reads_no_untracked_file_of_the_checkout`, `an_unhashed_build_requirement_is_refused`, `a_follower_with_no_toolchain_fetches_the_artifact_by_hash_and_refuses_a_wrong_hash`, `the_toolchain_archive_hash_is_checked` | Hash `git archive`; build in the checkout; pass the environment; skip the artifact hash; skip the toolchain hash |
| 5 | HIGH. `board_rekey`: the wrap set does not prove one key; the proposer by lowest fingerprint is grindable; writers could keep the old epoch; `board_key` rows were unspecified | 9 (rekey, `board_key`, MLS decision), K17, C9, C10 | `a_wrap_holding_another_key_is_refused_by_its_commitment`, `a_row_opens_under_one_key_only`, `a_grinding_fingerprint_does_not_win_the_proposal`, `a_flagged_or_panic_named_device_cannot_propose`, `a_writer_refuses_the_old_epoch_after_it_saw_the_removal`, `a_wrap_to_a_non_holder_is_refused_and_flags_the_publisher`, `a_board_key_row_for_an_unapproved_epoch_is_refused` | Compare only the wrap set; drop `kc`; propose by lowest fingerprint; accept a wrap for any device |
| 6 | MED-HIGH. Sealing left derived stores open; a current-epoch joiner could not fold | 9 (derived stores, checkpoint), K17, C9 | `a_marker_written_to_a_board_is_in_no_derived_store_in_the_clear`, `a_joiner_folds_the_same_state_from_a_checkpoint`, `a_checkpoint_two_holders_disagree_on_is_refused` | Leave the index open; accept one holder's checkpoint |
| 7 | MED. Panic: no rate limit or replay binding; level 1 left the session's tokens; the wipe destroyed sole holders and could be set off by the clock; level 2 did not rekey | 12, 5 (wipe), K14, C8, C10, C11 | `a_replayed_panic_is_a_noop`, `a_panic_end_for_another_id_does_nothing`, `a_panic_loop_is_rate_limited_but_still_removes`, `a_level_1_ends_the_named_sessions_tokens`, `the_lease_wipe_never_deletes_the_last_copy`, `a_clock_change_wipes_nothing_and_a_drawer_laptop_does_not_age`, `a_level_2_rotates_the_keys` | Drop the id binding; leave the token alive; wipe a sole holder; run the lease on the wall clock; skip the rotation |
| 8 | MED. Redundancy fell back to the lander in a one-owner pool; planted canaries are recognisable; one failure flagged a flaky test; collusion unnamed | 10, 16 (item 8), C12 | `a_one_device_pools_gate_result_is_single_party_and_gates_no_root_core_release`, `a_canary_is_a_mutation_of_a_real_test_and_carries_its_name`, `one_strike_redoes_and_three_flag`, `a_person_clears_a_flag_with_one_tap_and_a_detector_cannot` | Plant a synthetic test; flag at one strike; let the detector clear |
| 9 | MED. No key-loss path for the release role; `RELEASE_KEY` empty, so no first-install answer | 6 (key loss), 15.1, C2, C7 | `the_phrase_rotates_the_release_role_only_after_7_days_and_a_strong_key_cancels_it`, `the_founding_card_shows_the_installers_hash_and_the_build_words`, `a_joining_device_checks_the_build_words_in_the_join_check_words` | Let the phrase rotate at once; omit the installer hash |
| 10 | MED. "No gate on a second strong key" must be a gate on irreversible root acts only; the tripwire fails if the thief removes the owner's other devices first; the stolen-phone exposure was overstated | 6, K11, 16 (3, 11), C2 | `an_irreversible_root_act_is_held_6_hours_in_a_one_key_pool`, `the_phrase_cancels_a_held_act_at_once_and_removes_the_phone`, `removing_every_other_device_is_held_and_removing_one_is_not`, `a_pool_with_two_strong_keys_has_no_6_hour_hold` | Hold a single removal; make the phrase's cancel wait 7 days; skip the bulk-removal hold |
| 11 | MED. Friction: hourly asks without S0; Dependabot lockfile bumps as daily taps; an invisible soak and an invisible attack on it | 2, 8.1 (soak), 8.2, 8.5, C4 | `without_s0_every_release_asks_hourly_and_the_install_offers_s0`, `a_lockfile_bump_with_no_new_crate_is_signed_under_the_grant`, `a_new_crate_or_a_build_rs_asks_with_a_semantic_diff`, `a_bump_published_under_72_hours_ago_asks`, `a_cancelled_soak_is_an_urgent_line_in_the_summary` | Let the grant cover a new crate; drop the age check; omit the soak line |

The review's corrected slice order is in section 14 and its not-protected corrections are in section 16 (items 3, 6, 9, 12 and 13).

**Documents and words that change** (each lands with the first slice that shows it):

* `docs/glossary.md`: **release key** (the key whose signature makes a commit the pool's build), **release act**, **soak** (the
  hold of a release act), **board key** and **epoch**, **wrap** (a board key sealed to one device), **panic**, **freeze**,
  **incident record**, **probation**, **builder key** (the charter-pinned key that attests a build), **checkpoint** (the signed,
  sealed folded state a joiner starts from); **delegate** widens to "a device an admin lets sign one named kind of act for a time".
* `docs/pools-and-serving.md`: 1.3 (roles gain `release` and `builder`), 1.5 (acts `release`, `panic`, `panic_end`), 1.12 (what the
  person feels), 7.7 (probation covers board keys and `pull`). `docs/node.md`: "Who governs a pool", "Sync" (sealing), "Key
  custody" (the node identity, if C15 goes ahead). `docs/pool-encryption.md`: slices 3, 4, 6 and 7 are built as C9 and C10; its 8.1
  rejection of reproducible builds is superseded for the update path. `docs/compromised-agents.md`: the protected set gains
  the build files (4.9); S0's `ensure` fix (2.1) is C6's. `docs/release.md` and `docs/github-protection.md`: the pool's
  release root, the per-device credential check and the origin rulesets. `docs/phone-approval.md`: attestation, P-256 admin
  keys, the device list, panic, the tripwire and the 6-hour hold (13.1 of `docs/compromised-agents.md` lists the other
  additions). `docs/pool-check.md`: the new steps.
* `AGENTS.md`: nothing; `HANDOFF.md`: one pending entry per slice that has not landed, as each is written.
