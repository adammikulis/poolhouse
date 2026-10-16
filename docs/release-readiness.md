# Release readiness (0.3)

What the 0.3 release must do, and what must be tested before it ships. The owner and the agents drive toward the release from this
page. State as written: 2026-10-13, development branch `0.3dev` at `d365522ea`, plus the branches the landing queue held that day
(`poolhouse-workspace land-queue` is the live list). Contents: [1 How to use it](#1-how-to-use-it) |
[2 The two audiences](#2-the-two-audiences-and-the-bar-for-each) | [3 Feature checklist](#3-feature-checklist-by-area) |
[4 Security release gates](#4-security-release-gates) | [5 Owner-only steps](#5-owner-only-steps) |
[6 Not in 0.3, and known limits](#6-not-in-03-and-known-limits-shipped) | [7 The final drive](#7-the-final-drive) |
[8 Open questions for the owner](#8-open-questions-for-the-owner).

## 1. How to use it

Every row of sections 3 and 4 has one state word.

| State | Means | Who sets it | Evidence kept in the row |
| --- | --- | --- | --- |
| not built | No code on the development branch does it, or only a design document says it will | anyone, by reading | the design document, where there is one |
| built untested | The code is on the development branch and no run of its checks on a named tree is recorded here | the branch's author | the commit or branch |
| tested | Its automated checks (selectors, rig steps, Rust tests) pass on a named tree on a named device | the worker that landed it; the lead repeats the selectors on the batch tree | selector, tree, device, result |
| driven by a person | A person used it through the real interface on real hardware, following its journey | the person only (the owner, or a person the owner names); an agent writes down what the person reports | date, build, device and operating system, person, steps, what was seen |
| activated | The landed build runs on the owner's own devices and the original journey ran there | the lead that owns the activation, after the person drove it | installed version, commit and path |

- Nothing is ticked on a green suite alone (AGENTS.md, "Saying that something works"). A suite earns `tested` and nothing higher. A row
  that a person uses stays below `driven by a person` however green its tests.
- A row moves up only with its evidence and down on any red test, regression or change of what it depends on. A row is never above the
  lowest state of what it depends on, and a dependency that is an unlanded branch counts as `not built` until it lands.
- A branch that changes what a row says edits the row in the commit that lands the work, up to `tested`. The lead edits the rest.
- The state column was filled by reading the repository, `HANDOFF.md` and the landing queue. Its author ran only the selectors listed
  under "Selectors run for this document" below; every other `built untested` row has suites that were not re-run. The batch gate
  repeats them on the combined tree before a row moves to `tested`.
- Findings named F1 to F10 in this page are the phone-signed charter findings of `HANDOFF.md` (Priority 1). The red-team findings
  F1 to F9 of `docs/redteam/findings.md` are a different list and are not meant by that name.
- A row that cannot be driven because its feature does not exist says so in its state; it is not skipped.

A background full run on `6b7cca262` recorded 17,021 passes, 39 failures and 10 errors (290 skipped); its additional seatbelt set passed 152 tests with 91 skipped. These failures remain open release work; passing affected checks does not establish a repository-wide green result.

Scoped source checks for `tests/test_graph_vectors.py` and `tests/test_ingest_embed.py` passed 31 cases after correcting
the embedding caller's HTTP options. A loopback server's transient refusal was retried, vectors were written and reopened,
and programmer errors were no longer reported as refused batches. The caller retains its 300-second request timeout and
three-attempt retry policy. This does not replace the full-suite result above.

**Selectors run for this document** (2026-10-13, tree `d365522ea` plus this change, this Mac, through `scripts/test all`):

- Passed: `tests/test_glossary.py`, `tests/test_documents_name_current_files.py`, `tests/test_documented_env_vars.py`; and, in one run,
  `tests/test_phone_friction.py`, `tests/test_release_key.py`, `tests/test_fleet_signing.py`, `tests/test_fleet_updates.py`,
  `tests/test_github_protection.py`, `tests/test_github_protection_apply.py`, `tests/test_serve_no_bypass.py`, `tests/test_pool_found.py`,
  `tests/test_api_found.py`, `tests/test_board_approval.py`, `tests/test_api_approval.py`, `tests/test_presence.py`,
  `tests/test_requests_floor.py`, `tests/test_redteam_human_floor.py`, `tests/test_sentinel_wiring_agent.py`,
  `tests/test_sentinel_wiring_serve.py`, `tests/test_harness_rails.py`, `tests/test_workspace_quickstart.py`,
  `tests/test_credentials.py`, `tests/test_pool_check.py`, `tests/test_api.py` (505 passed, 3 skipped).
- Failed, not caused by this page: `tests/test_cli_help.py` (one test, `docs/commands.md:45`, which names `--policy` for
  `poolhouse-device-setup`; HANDOFF records it as a baseline failure); `tests/test_budgets.py` (`orphan-trees`, which reads the
  worktrees of this machine, and `handoff-size`, whose number in `budgets.json` is higher than the count `HANDOFF.md` now gives).
- Commands run: `scripts/github-protection --check --local` (`0 findings (local only)`), `scripts/pool-check --list` (30 steps).
- Not run: the Rust tests (`cargo test --locked -p poolhouse-node`), the `scripts/pool-check` steps, `scripts/test-on-linux`,
  `scripts/test gate`, `scripts/budgets`, and anything that needs a phone, Docker, a model or a second device.
- A row says "passes" for a selector in the Passed list. A row is `tested` only when every automated check in its "Verified by" column
  is in that list; a drive in that column is not automated and does not hold a row back from `tested`, but it holds it below `driven by
  a person`.

## 2. The two audiences and the bar for each

The release serves two audiences with one build. A change that adds a visible step for either is redesigned before it lands
(AGENTS.md, "Onboarding is the product" and "Security without friction"). **A new step is a regression**: a journey's step count only
falls, a step that is a tap, a plain sentence or a scan never becomes a typed command, and every security step is counted in the same
budget as every other.

Step kinds, from `docs/pools-and-serving.md` section 3: `install`, `os-prompt` (the operating system's own one-time prompt),
`link-phone`, `scan`, `say` (a plain sentence to the agent), `tap` (an approval), `tailscale`. A terminal step is a typed command, a
file edited, a key handled, or a port or firewall named; it counts zero in every person journey and a journey that produces one fails.

Where the counts live: the built phone counts are `contracts/phone-friction.json`, checked by `tests/test_phone_friction.py` and the
Android `PhoneApprovalTest`. The design counts are `docs/pools-and-serving.md` section 3. `tests/onboarding-steps.json`, the step
ledger check in `scripts/cold-start-check` and the `onboarding-*` pool-check steps that section 3.4 of that document specifies do not
exist; the node writes a step ledger to the file `POOLHOUSE_STEP_LEDGER` names (`app/poolhouse-node/src/gov/signer.rs`).

### 2.1 A person on the desktop or phone app

Bar: only taps and plain sentences. No terminal, configuration file, key, port or firewall is named to them. A refusal is one plain
sentence of at most 160 characters that an agent can relay. A person-facing string holds none of "run", "terminal", "config", "port",
"firewall", "key".

**P1. First run, one device, a pool, a served model.** Design count: 10 steps (install 2, os-prompt 3, link-phone 2, say 2, tap 1), 0
terminal.

| # | The person does | Kind | Built today |
| --- | --- | --- | --- |
| 1 | Installs Poolhouse on the computer; allows network discovery once | install, os-prompt | The installer is a one-line command typed in a terminal (README, "Install"); not driven by a person |
| 2 | Installs the Poolhouse app on the phone; allows camera and notifications once | install, os-prompt x2 | The app is a sideloaded APK, not on a store (`app/android/README.md`); never run on a phone |
| 3 | The computer's app shows "Link your phone" with a code; scans it and confirms the same three words on both screens | link-phone x2 | No such screen. The first phone is added by `poolhouse-workspace phone add` at a terminal, then the computer's own prompt, a scan and a fingerprint (4 steps, `first_run` in `contracts/phone-friction.json`). Check words are not built (slices 5 and 8) |
| 4 | Says "Make a pool called home" | say | `ph.pool.found` exists; it refuses a weak key, and no strong key can sign a charter yet |
| 5 | Taps once on the phone to create the pool | tap | Not built (phone-signed charters, F2 to F10) |
| 6 | Says "Serve a small model for my pool" | say | The agent runs `poolhouse-serve up` under the broker lease. `serve_offer` does not exist |

**P2. Add a second device.** 2a, same network, `open` pool: install, allow discovery; the device joins by itself and the first device's
phone says it joined (2 steps). 2b, same network, `secure` pool: install, allow discovery, say "Join my pool", compare three words and
tap on the first device's phone (4 steps). 2c, another network over the person's tailnet: say, install, allow discovery, scan (4 steps
with Tailscale on both; 10 from nothing). Built today: a join code made by `add_device` and given to the new device
(`scripts/pool-check --only join-secure`); an `open` join by fingerprint (`join-open`). Three check words and the tap are not built;
the tailnet route display is not built.

**P3. Approve from the phone.** Built and counted in `contracts/phone-friction.json`: routine request 2 steps (tap the notification,
fingerprint), 3 from a locked phone; sensitive or irreversible request 4 steps (tap, approve, fingerprint, pick the number the agent
showed), 5 from a locked phone.

**P4. Add another phone.** Scan (1) and one fingerprint on a phone already added (1), then the new phone does the scan and the
fingerprint of the first phone (`docs/phone-approval.md`, "The friction budget"). **P5. Upkeep.** One tap a month to keep the agents' permissions (design only, `docs/pools-and-serving.md` 3.5). **P6. Away from
home.** 6 steps from nothing and 0 where Tailscale is present (design only).

### 2.2 A developer using the library or an existing harness

Bar: no new flag, prompt or wait in the way they already work, and the same protection a person gets. `import poolhouse as ph` loads
nothing else. A harness (Claude Code, Codex, any other) is started exactly as before.

**D1. The library.** Design count: 7 human steps (install 2, os-prompt 2, link-phone 2, tap 1) and 3 lines of code
(`docs/pools-and-serving.md` 3.3). Built: `ph.pool.found(name, policy, say=...)`, `ph.pool.add_device()`, `ph.pool.join(host, code)`,
`ph.pool.members()`, `ph.board`, `ph.leases`, `ph.test`, `ph.models`, `ph.serve` (`up`, `down`, `status`), `ph.client`, `ph.hub`.
Not built: `ph.pool.create`, `ph.NeedsApproval`, `ph.serve.offer`, `ph.pool.join_link`.

**D2. An existing harness (Claude Code, Codex) started as before.**

| # | The developer does | Built today |
| --- | --- | --- |
| 1 | Installs Poolhouse (once) | as P1 step 1 |
| 2 | Links the phone (once per person) | as P1 step 3 |
| 3 | Starts the harness in the project directory with its usual command and flags | A harness hook registers the session and exports its name; hooks come from `poolhouse-workspace install-hooks` (`docs/workspace.md`, "Quickstart"). Whether first run installs them without a command is unverified |
| 4 | Works as before; a pool action the session was not granted raises a phone approval | P3 counts; the lead's standing grants are made with `poolhouse-workspace grants-setup` at a terminal |

No count for D2 is recorded in the repository. The ceiling proposed here: the harness's own steps, plus the human steps of P1 once per
person, plus the P3 counts per approval, and no flag, prompt or wait of Poolhouse's own. Question 10 of section 8.

## 3. Feature checklist by area

Columns: what the release must do; how it is verified (an exact command, selector or rig step, or a manual drive); the state; what it
depends on (HANDOFF entry, branch or land request). Land request numbers are the ones the queue showed on 2026-10-13.

### 3.1 Install and first run

| ID | The release must | Verified by | State | Depends on |
| --- | --- | --- | --- | --- |
| INS-1 | Install the app and daemon on macOS; the first-run window shows this device as a pool of one and the models that fit | `scripts/test all tests/test_packaging.py tests/test_packaging_install.py tests/test_packaging_install_runs.py tests/test_desktop_packaging.py`; drive: new macOS account, `packaging/install.sh`, read the first-run screen | built untested | HANDOFF "Person-session launch tickets", packaged-app acceptance (1) to (6) |
| INS-2 | Install on Windows (the app runs in Ubuntu on WSL and opens in the Windows browser) | `packaging/install.ps1`; `docs/windows-runtime.md`; drive on a real Windows machine | built untested; not driven | `docs/windows-wsl.md` |
| INS-3 | Install on Linux | `packaging/install.sh`; `scripts/test-on-linux` | built untested | HANDOFF "Benchmarks, local review and landing": nothing from 2026-10-12 has run on Linux |
| INS-4 | Upgrade in place by re-running the installer; the runtime stays current without a person | `poolhouse runtime ensure`; `docs/install.md` "Starting at login and keeping the runtime current" | built untested | HANDOFF "Autostart units": no unit has run under a real service manager |
| INS-5 | Installers verify a signature from the release key, not only the release digest | `packaging/install.sh` and `packaging/install.ps1` compare the digest GitHub reports; `src/poolhouse/fleet/signing.py` | not built: `RELEASE_KEY` is empty | HANDOFF Priority 1 first item; OW-3 |
| INS-6 | A new device is set up by one command that asks the person nothing but the operating system's own prompt | `python -m poolhouse.device_setup --dry-run` then `--yes`; `docs/windows-wsl.md` | built untested | CI `windows-and-wsl` job; no real Windows device driven |
| INS-7 | `scripts/cold-start-check` ends with no BROKEN step on a clean clone | `scripts/cold-start-check` | built untested | none |

### 3.2 Founding and joining a pool

| ID | The release must | Verified by | State | Depends on |
| --- | --- | --- | --- | --- |
| POOL-1 | A fresh device is the unfounded pool of one: no signer, nothing written, admits nobody, and says "make one, or join one" | `app/poolhouse-node/tests/gov_net.rs`; `ph.pool.status()` | built untested | none |
| POOL-2 | A person founds a pool: a strong key signs the charter, which names this device first; this device is the first member | `scripts/pool-check --only found-by-api` (the rig's key, declared strong); `tests/test_pool_found.py`; `tests/test_api_found.py`; P1 steps 4 and 5 | not built for a real person: no strong key can sign a charter | HANDOFF Priority 1, first pool item; F1 to F10 |
| POOL-3 | A device on the same network joins an `open` pool with no code; one reached only through NAT or loopback is refused with the reason | `scripts/pool-check --only join-open`; `app/poolhouse-node/tests/pool.rs` | built untested | Question 5; the open-join sentinel is a design (SEN-2) |
| POOL-4 | A device joins a `secure` pool with a join code; three wrong tries close the window | `scripts/pool-check --only join-secure --only join-wrong-code` | built untested | check words and trusted-display joins, slices 5 and 8, not started |
| POOL-5 | Removing a member makes every member refuse its next request; nothing it writes afterward reaches them | `scripts/pool-check --only member-removal --only cert-swap`; `app/poolhouse-node/tests/removal.rs`, `gov_removal.rs`, `removal_channels.rs` | built untested | none |
| POOL-6 | A pool's join policy never changes in place; a new policy is a new pool | `scripts/pool-check --only grants-gate` shows the refusal; reissue is slice 2 | partly built: the refusal; the reissue (new pool, kept members move) is not built | HANDOFF slice 2 not started |
| POOL-7 | An old-format `pool.json` is set aside (renamed `pool.json.old-format`), one plain line is shown, the device starts unfounded and keeps its boards | `app/poolhouse-node/tests/gov_net.rs` | built untested | none |
| POOL-8 | A pool founded before the key-strength fix is refused at a charter version bump; a person founds a new pool | none written | not built (owner, 2026-10-13) | branch `chore/handoff-release-cut`; GOV-2 |
| POOL-9 | A refused or shut-down pool is deleted after 3 days and a person can restore it inside the window; a pool shut down for compromise is frozen, sealed read-only with its keys and grants ended, never restorable, and a person founds a new one; the 3 days delete only its live state and secrets and the sealed record stays until a person deletes it | none written | not built (owner, 2026-10-13) | branch `chore/handoff-release-cut` |
| POOL-10 | Devices on other networks reach each other over the person's tailnet | `docs/pools-and-serving.md` 4.4 | not built | Question 6 |
| POOL-11 | Entries, direct messages, claims and leases converge across devices; a partition heals; a node killed mid-sync recovers | `scripts/pool-check --only entries-converge --only direct-message --only claim-pool-wide --only lease-queues --only partition-heal --only crash-mid-sync` | built untested | Docker for `partition-heal` and `crash-mid-sync` |

### 3.3 Phone approval and standing grants

The list of what only a real phone can check is "Checks on a real phone" in `docs/phone-approval.md` (items 1 to 11, with 10b to 10d). Its
item numbers are used below.

| ID | The release must | Verified by | State | Depends on |
| --- | --- | --- | --- | --- |
| PH-1 | Add the first phone: the computer asks the person once with its own prompt, one scan, one fingerprint; the app and the terminal name the same key words | `scripts/pool-check --only phone-approval`; `tests/test_board_approval.py`; `tests/test_api_approval.py`; `tests/test_presence.py`; `app/poolhouse-node/tests/presence_terminal.rs`; drive: items 1, 2, 11 | built untested; never run on a phone | HANDOFF "Phone approval": nothing of the app has run on a phone |
| PH-2 | A routine approval is 2 steps and a sensitive one is 4 (3 and 5 from a locked phone) | `tests/test_phone_friction.py`; `app/android` `gradle protocolChecks` (`PhoneApprovalTest`); drive: items 3 to 5 | built untested; `tests/test_phone_friction.py` passes | none |
| PH-3 | The request text is complete and signed as shown; a signature over other text is refused; a second decision is refused | `app/poolhouse-node/tests/approvals.rs`; `scripts/pool-check --only phone-approval`; drive: items 3, 5 | built untested | none |
| PH-4 | Lock screen shows only who is asking; an offline phone catches up; a request expires | drive: items 4, 6 | built untested; not driven | none |
| PH-5 | A new fingerprint ends the key and the app offers the scan; a second phone is added and the first removed from it | drive: items 7, 8 | built untested; not driven | none |
| PH-6 | The wake-up reaches a closed app through UnifiedPush and carries only ciphertext | drive: item 9 with a distributor (ntfy) | built untested; never tried against a distributor | HANDOFF "Phone approval": FCM is not planned |
| PH-7 | Approval fatigue brakes: one item per request, backoff, hold, quiet notice, deny and block, number to match, caps | `app/poolhouse-node/tests/approvals_fatigue.rs`; `scripts/pool-check --only phone-approval`; drive: items 10b to 10d | built untested; the app's pick dialog, "Approve anyway" and the quiet channel never ran on a phone | F8 |
| PH-8 | The first phone is added without a typed command: the computer's own screen shows the code | none | not built (today a command at a terminal) | HANDOFF "Phone approval", fourth item; bar of 2.1 |
| PH-9 | The first key and first phone work on Windows | none | not built: Windows has neither the system prompt nor terminal reading | HANDOFF "Phone approval", item 4; Question 11 |
| PH-10 | The person's yes to the node's own question is answered by a person | `tests/test_presence.py` (types it at a pseudo-terminal); drive: first key on the real home with `--presence os` | tested (`tests/test_presence.py` passes); never answered by a person | HANDOFF "Phone approval", item 4 |
| PH-11 | Standing grants: a person signs the lead's grants once per device; until then no agent session holds `land_control`, `task_authority`, `shard_call` or `note_verify` | `app/poolhouse-node/tests/grants.rs`, `grants_enrol.rs`, `grants_hostile.rs`, `grants_keys.rs`, `grants_foreign.rs`, `grants_sites.rs`; `tests/test_board_person_key.py`; `scripts/pool-check --only grants-gate --only grants-key-removed` | built untested; made at a terminal by `poolhouse-workspace grants-setup`, so a person with no terminal cannot yet | HANDOFF "Standing grants" |
| PH-12 | A lost phone is removed from another phone, or at the computer by a person who also holds a typed-yes key | drive: item 8 | built untested | one-time removal beside `persons.json` not built |

### 3.4 Governance

| ID | The release must | Verified by | State | Depends on |
| --- | --- | --- | --- | --- |
| GOV-1 | Charter, admin keys, thresholds, holds, the chain, the roster, signed acts and a refusal record | `app/poolhouse-node/tests/governance.rs`, `gov_acts.rs`, `gov_found.rs`, `gov_floods.rs`, `gov_describe.rs`, `gov_net.rs`; `cargo test --locked -p poolhouse-node` (the CI command, `.github/workflows/ci.yml`) | built untested | `docs/node.md` "Who governs a pool" |
| GOV-2 | The node derives a key's strength; a charter that declares a key strong that no person added through the phone path is refused (F1) | the failing-first test named in HANDOFF; `app/poolhouse-node/tests/gov_found.rs` | built on branch `fix/node-derives-key-strength`, in the landing queue (land-1726, land-3573); not on the development branch | independent accept for land-1726 |
| GOV-3 | A real strong key signs a charter acceptance and acts: the phone (P-256, or an Ed25519 key in the phone's keystore with a per-signature prompt) is the node's `Signer` | [protocol evidence](notes/2026-10-14-phone-founding-protocol.md): shared charter/acceptance vectors, Java checks and Android debug build | protocol foundation tested; production signer not built | F2 to F10; `docs/phone-approval.md` slice P1 |
| GOV-4 | Strong and weak keys: only strong keys count toward a removal, a cancel, a change of admins | `app/poolhouse-node/tests/governance.rs` | built untested; only the rig can sign strong | GOV-3 |
| GOV-5 | Backup and recovery key, the time lock, `endorse`, members-governed mode, dispute banners (slice 3) | none | not built | HANDOFF Priority 1 |
| GOV-6 | Reissue and the pool-copy defence (slice 2); a copy of a pool or a clone of a device joins nothing | `scripts/pool-check` steps `gov-pool-copy`, `gov-clone-flagged` (not written) | not built | Question 4 |
| GOV-7 | The delegate is signed by a keystore-bound device key; a copy of a state directory signs no admit | `Node::key_binding` | not built: every device reports `File`, so no device signs a delegate admit | `docs/pool-encryption.md` slice 3, prerequisite P3 |
| GOV-8 | Many pools on a device (slice 4) | none | not built | Question 4 |
| GOV-9 | Check words and trusted-display joins (slices 5 and 8) | none | not built | Question 4 |
| GOV-10 | Old pools refused; 3 day delete; sealed record on compromise | POOL-8, POOL-9 | not built | branch `chore/handoff-release-cut` |

### 3.5 Serving and the broker lease

| ID | The release must | Verified by | State | Depends on |
| --- | --- | --- | --- | --- |
| SRV-1 | A model server starts only through the broker's lease; nothing starts one by hand | `scripts/test all tests/test_serve_no_bypass.py tests/test_serve_broker.py tests/test_serve_admission.py`; `scripts/budgets` (`server-starts`) | built untested; `tests/test_serve_no_bypass.py` passes | none |
| SRV-2 | One piece of work on a device's compute at a time: a second request waits; a different model under one purpose gets its own server when memory admits | `poolhouse-serve status` before a run; drive: stage 4 of section 7 | partly built: the device field; `broker-purpose-not-exclusive`, `gate-compute-lease-per-device`, `workspace-allocation-device` and the CPU-beside-GPU rule are not started | HANDOFF "Serving: broker branches 2-5" |
| SRV-3 | Leases come from the node's table: the serve broker, the request gate, a training run | `src/poolhouse/leasetable.py` | partly built; test runners, `fleet.jobs.JobRunner` slots, `lock.only_one` and `workspace/claims.py` stay apart | HANDOFF "Leases from the node" |
| SRV-4 | A device offers a named model to a named pool; inbound calls are off until its owner turns them on | none | not built (`ph.serve.offer` does not exist) | Question 7; `docs/pools-and-serving.md` section 7 |
| SRV-5 | A device chats with a model another member serves | `docs/FEATURES.md`; `python docs/verify_release.py --offline`; drive: stage 4 | built untested; not driven across two real devices | HANDOFF, the entry on running it across two machines (the pool has not crossed a real network) |
| SRV-6 | The wired memory limit changes only through the administrator's dialog, by a person | `poolhouse-serve memory --for MODEL --ctx N --kv q8_0` prints the need | built untested | owner step |

### 3.6 Model pulls and publisher trust

| ID | The release must | Verified by | State | Depends on |
| --- | --- | --- | --- | --- |
| MOD-1 | A pull resolves to the 40-character commit and pins digests; an unknown publisher, a marked name, or a digest that differs from the one pinned under another commit asks a person through a human-only `model_publisher` request | `tests/test_publisher_trust.py`; `tests/test_publisher_trust_review.py` | built on branch `feat/publisher-trust`, in the landing queue (land-3133, land-2367); not on the development branch | independent accept for land-2367 |
| MOD-2 | A model file Poolhouse did not fetch is not pinned on first use; a signed list, an equal pinned digest or a person's approval pins it | same tests | same branch | same |
| MOD-3 | Pins at pull time and signed manifests at load; a held or changed model is refused | `tests/test_supply_chain_pins.py`; `tests/test_sentinel_wiring_serve.py` | built untested | none |
| MOD-4 | `poolhouse-models distrust OWNER` is person-only, only removes trust, and unpins and quarantines what that publisher supplied; `trust OWNER` and a tap on the request add one | none yet; tests named in `docs/publisher-trust-commands.md` section 9 | designed, not built | a different agent's attack-first pass on the revised design (round 1 findings are fixed in that document; Q1 and Q2 are decided there) |
| MOD-5 | Nothing scans model weights for a backdoor | `docs/sentinel.md`, "Considered and left out" | known limit | section 4.2 |

### 3.7 Harness policy and the exec chain

| ID | The release must | Verified by | State | Depends on |
| --- | --- | --- | --- | --- |
| HAR-1 | `poolhouse-claude` and `poolhouse-codex` serve the model through the lease, write settings outside the tree, install the hooks and register a workspace session | `scripts/test all tests/test_harness.py tests/test_harness_rails.py tests/test_harness_hook_outages.py tests/test_harness_ownership.py`; real model acceptance is a separate exclusive brokered run (`docs/release.md`) | built untested; `tests/test_harness_rails.py` passes | none |
| HAR-2 | The pre-tool hook labels each call and applies the role (`read-only`, `approve-first`, `plan-and-go`); an unreadable call asks | same tests | built untested | `docs/harnesses.md` |
| HAR-3 | The hook asks for downloads that are run later, fetch-and-run text, installs, credential reads, wildcard credential folders, login persistence and writes through links | `tests/test_harness_exec_chain.py`; `tests/test_harness_exec_chain_more.py` | built on branch `fix/harness-exec-chain` (land-3233); not on the development branch | none |
| HAR-4 | A session of a harness the launcher did not start (Claude Code or Codex on a cloud model) is registered by its hooks and keeps its own board name | `scripts/hooks/claude-session-start`, `scripts/hooks/claude-subagent-start`; drive: stage 5 | built untested; HANDOFF: hooks are not verified end to end on the restored runtime | HANDOFF "Keystore, hooks, sandbox and authority" |
| HAR-5 | Hook coverage includes `Agent`, MCP tools and `TaskStop` | none | not built | access-widening findings, section 4.3 |

### 3.8 The workspace

| ID | The release must | Verified by | State | Depends on |
| --- | --- | --- | --- | --- |
| WS-1 | The node names each session, records its parent and gives it a token; nobody mints, copies or pastes a token | `app/poolhouse-node/tests/sessions.rs`, `lineage.rs`, `lineage_tokens.rs`; `tests/test_workspace_quickstart.py` | built untested | none |
| WS-2 | No identity sprawl: a main session retires itself at the end, an idle session is retired, a device refuses a main session past its bound (24 live per harness, 8 per terminal) | `app/poolhouse-node/tests/idle.rs` | built on branch `fix/identity-sprawl`, in the landing queue (land-3971, needs a review); not on the development branch | none |
| WS-3 | Messages, rooms, claims, leases, notes and files work across devices and survive a restart | `scripts/pool-check` steps of POOL-11; `tests/test_workspace_files.py` | built untested | none |
| WS-4 | A worker lands its own branch through the queue (`land-request`, independent `land-review`, runner gate and push) | `scripts/test all tests/test_land.py tests/test_land_board.py tests/test_land_check_evidence.py tests/test_land_standing.py` | built untested | the session-start hook records no model after a clear, so the next `land-request` is refused until `poolhouse-workspace whoami --model` runs (`scripts/hooks/claude-session-start`) |
| WS-5 | Board delivery meets the existing latency budgets | HANDOFF names the observer file | not met: a single-recipient message and a Board message exceed their budgets | HANDOFF Priority 2 "Pools across devices" |

### Workspace delivery evidence (2026-10-15)

Published development through `ebacb7225` includes safe recovery of owned checkouts (`6e186cc67`), truthful
subagent presence (`c4fa3aee7`), generated-file snapshot checks (`dc32ce68e`), signed founding for test pools
(`9ba733403`), corrected runtime fixtures (`515369cc1`) and single-authentication notification reads
(`92fce11fd`). Their affected checks and structural gates passed. These results do not establish WS-3's
cross-device claims or WS-5's latency budgets.

The installed `ebacb7225` runtime was used for the latest checks on the available macOS device; the supervised
Rust node kept its unchanged binary. An installed notification read under the caller's own identity made one
identity request and one inbox read, versus two identity requests before the change, without advancing the cursor.
An actual digest listed nine registered subagents. Checkouts from the delivered code batches were removed;
three locked foreign checkouts were preserved.

The installed optional coordination CLI completed preparation and a short lifecycle. TLS certificate names,
restricted service-account permissions, named metadata writes, wrong-audience and anonymous refusals, and
600-second credentials were checked. The control plane had no worker nodes or pool authority. A separate
750-second source-run check observed refusal after actual token expiry; that long wait was not repeated with the
installed CLI. Docker volume inventories and inventories of owned containers and networks were unchanged after cleanup.

An agent drove the installed coding page in Chromium: start, Stop, cancelled, then a completed next turn with exit
zero and verified cleanup. Recorded Poolhouse imports, including spawned workers, came from the installed prefix.
The test used a scratch test-phone key, short grants, and a local fake native CLI and model catalogue, with no real
provider or owner credentials. Its workers, server, node, browser and scratch state were removed.

Earlier installed checks on `425deb61a` showed that broken output leaves messages unread and successful
`inbox --ack` renders them before advancing the cursor. Notification suppression and retry were checked after an
actual two-minute wait. Two authenticated agents exchanged a question and linked answer and acknowledged their own
inboxes. A supported native follow-up resumed a child; the direct app-server thread call refused it. During a
controlled daemon stop, manual UserPromptSubmit, PostToolUse and Stop notifications returned zero with a warning;
the daemon was restored with LAN and presence settings preserved. No refusal was bypassed.

These are agent-driven installed-command and browser checks. Seven Codex event definitions and a separate
completion check are installed; normal native callbacks and their trust review remain unverified. Automatic idle wake-up and durable sender-visible read receipts remain unbuilt. Windows/WSL,
another physical device and the real-phone founding journey remain unverified.

### Optional local coordination evidence (2026-10-14)

The bounded metadata API at source `e157d4c59` passed an agent-driven lifecycle on the available
macOS ARM64 Docker hardware. Fresh CA/SAN verification, source and installed-client anonymous
401 refusal, named metadata writes, restricted RBAC, wrong audience and actual token expiry
were checked. Normal cleanup removed the exact owned container and network and preserved all
existing volume IDs. [Evidence and limits](notes/2026-10-14-local-coordination.md) distinguish
the earlier installed HTTP-client check from the full CLI lifecycle checked after publication in `ebacb7225`.
Kubernetes supplies an optional metadata control plane only, with no task dispatch or pool authority. This does not establish multi-device pool coordination,
WSL onboarding, execution dispatch or any POOL/GOV completion claim.

### 3.9 Agent identity, release signing key and GitHub protection

| ID | The release must | Verified by | State | Depends on |
| --- | --- | --- | --- | --- |
| IDN-1 | Agents use a GitHub identity that cannot edit rulesets, environments or secrets; the App's key sits in the keystore and a token lasts an hour | `tests/test_agent_identity.py`; `scripts/github-protection --check --agent` | built on branch `feat/agent-identity-setup` (commits `94c0c8f6a`, `fa6ad732e`); no land request in the queue; not on the development branch | OW-1 |
| IDN-2 | The server's rulesets are applied: `scripts/github-protection --check` prints `0 findings` | `scripts/github-protection --check`; `tests/test_github_protection.py`; `tests/test_github_protection_apply.py` | not built on the server: HANDOFF records 12 findings and `PYPI_API_TOKEN` as a repository secret | OW-2 to OW-4 |
| IDN-3 | The local half is clean: CODEOWNERS and workflows | `scripts/github-protection --check --local` | tested: prints `0 findings (local only)` on `d365522ea`, 2026-10-13, this Mac | none |
| IDN-4 | A device that is not the lander holds no push credential; the lander's credential lasts at most an hour | `scripts/github-protection --check --agent`; `--agent --lander` | built untested | IDN-1 |
| IDN-5 | The release signing key exists and `RELEASE_KEY` in `src/poolhouse/fleet/signing.py` is set | `scripts/release-key show-public`; `tests/test_release_key.py`; `tests/test_fleet_signing.py` | not built: `RELEASE_KEY` is empty | OW-3 |
| IDN-6 | A signed update installs and an unsigned, wrongly signed or altered one is deleted; `poolhouse-pool join --track` accepts only a signed tip | `tests/test_fleet_updates.py`; `tests/test_fleet_signing.py` | tested (both selectors pass); fails closed while the key is empty | IDN-5 |
| IDN-7 | The runtime and the pinned Box build are signed by the same key (only if Box ships) | none | not built | Question 2 |
| IDN-8 | Release builds, SBOM and licence checks pass; the release environment needs the owner's approval | `python scripts/notices.py --check`; `python scripts/sbom.py --out sbom.cdx.json`; `release-dry-run.yml`; `audit.yml` | built untested; `audit.yml` has not run on GitHub | `docs/release.md` |
| IDN-9 | PyPI accepts `poolhouse` | PyPI similarity waiver, then a pending publisher, then `PYPI_ENABLED` | not built: upload is off until the waiver lands | HANDOFF "Getting it onto a machine that is not this one"; Question 13 |

### 3.10 The sentinel

| ID | The release must | Verified by | State | Depends on |
| --- | --- | --- | --- | --- |
| SEN-1 | The armed-by-default sentinel: start-time verification, the scan loop, canaries, decoys, session scores, output screening, child environment scrub | `scripts/test all tests/test_sentinel_wiring_agent.py tests/test_sentinel_wiring_serve.py tests/test_sentinel_integration.py tests/test_sentinel_cli.py` | built untested; the two wiring selectors pass | the "Not armed" list in `docs/sentinel.md` |
| SEN-2 | The sentinel makes `open` joins safe (flags on devices taken without a code) | none | not built: sections 3 to 6 of `docs/sentinel-open-join.md` are a design | Question 5 |
| SEN-3 | Releasing a quarantine, approving a host and changing guard or sentinel policy are a person's alone | `tests/test_redteam_human_floor.py`; `tests/test_requests_floor.py` | tested (both pass) | none |

### 3.11 Credentials never held by agents

| ID | The release must | Verified by | State | Depends on |
| --- | --- | --- | --- | --- |
| CRD-1 | Every credential is read through one resolver; a child process does not inherit tokens | `tests/test_credentials.py`; `docs/security.md` finding 18 | tested (`tests/test_credentials.py` passes) | none |
| CRD-2 | An agent holds a placeholder and never a usable secret; a separate process swaps it on an allowed host | none | not built (the connector broker; B2) | `docs/assistant-security.md` rule 2; section 4.2 |
| CRD-3 | An agent cannot read the credentials file, the keystore items, the node's key files or the person record | `docs/agent-sandbox.md` acceptance steps | not built: the sandbox is staged, not installed or measured | HANDOFF "The agent sandbox is staged"; Question 8 |
| CRD-4 | The person record and the typed yes cannot be forged by an agent | `tests/test_redteam_person_statement.py` | known weakness: the person record's key is a same-user file no hook covers | access-widening findings, section 4.3 |

### 3.12 Linux and Windows device checks

| ID | The release must | Verified by | State | Depends on |
| --- | --- | --- | --- | --- |
| DEV-1 | A pool of this machine and two Linux containers passes every step, with the failing step's output as evidence | `scripts/pool-check` (needs Docker); `scripts/pool-check --list` shows 30 steps | built untested on this tree; `docs/pool-check.md` records a green CI set on 2026-10-11 at an earlier tree | none |
| DEV-2 | A device behind NAT (WSL2 as a container) joins, syncs, is sent tests and is removed | `scripts/pool-check --wsl` | built untested | mirrored mode untested |
| DEV-3 | The native, WSL and Windows-and-WSL jobs pass | `.github/workflows/pool-check.yml` | built untested on this tree | none |
| DEV-4 | The suite passes on the Linux device on the final tip | `scripts/test-on-linux` | not run: HANDOFF says nothing from 2026-10-12 ran on Linux | HANDOFF "Benchmarks, local review and landing" |
| DEV-5 | A real Windows device, a real network, discovery by beacon, a firewall prompt, a VPN | drive: stage 3 on a Windows machine | not driven; `docs/pool-check.md` "What it cannot show" | none |

### 3.13 The library API and an existing harness driven through a pool journey

| ID | The release must | Verified by | State | Depends on |
| --- | --- | --- | --- | --- |
| API-1 | `import poolhouse as ph` exposes `pool`, `board`, `leases`, `test`, `models`, `serve`, `client`, `hub`, typed and documented | `scripts/test all tests/test_api.py tests/test_api_found.py tests/test_api_approval.py`; `tests/test_api_live.py` | built untested; `tests/test_api.py`, `tests/test_api_found.py`, `tests/test_api_approval.py` pass; HANDOFF: `test_serve_up_status_and_down_under_the_broker_lease` fails where no runtime is selected | none |
| API-2 | A developer's journey D1 runs with 3 lines of code and 7 human steps | drive: stage 5 | not built: `ph.pool.create`, `ph.NeedsApproval`, `ph.serve.offer`, `ph.pool.join_link` do not exist | GOV-3, SRV-4 |
| API-3 | Claude Code and Codex, each started as before, run a pool journey: hooks register, the board and a pool action work, the phone approves, nothing new is asked | drive: stage 5 of section 7 | not driven | HAR-4, PH-1 to PH-3 |
| API-4 | `ph.board` and the `poolhouse-workspace` shell commands agree with `docs/api.md` | `tests/test_workspace_quickstart.py` | tested (passes) | none |

### 3.14 The Android app

| ID | The release must | Verified by | State | Depends on |
| --- | --- | --- | --- | --- |
| AND-1 | The APK builds and its protocol checks pass | `python app/android/build.py GRADLE PLUGIN API debug` (an Android SDK and JDK; no CI job builds it) | built untested | SDK licence accepted by a person |
| AND-2 | A release APK is signed (alias `poolhouse-preview`) and sideloaded | `ANDROID_SIGNING_STORE`, `ANDROID_SIGNING_PASSWORD` | not built: a person's signing material | OW-9 |
| AND-3 | Every item of "Checks on a real phone" passes on a phone running Android 11 or later | `docs/phone-approval.md` items 1 to 11 | not driven | PH-1 to PH-7 |
| AND-4 | The chat page of the computer reads on the phone behind the fingerprint | drive: `app/android/README.md`, "What it does" | built untested; not driven | none |
| AND-5 | An iPhone app | none | not built; blocked until the product takes off (`docs/service.md` 9.1) | section 6 |

### 3.15 The docs and onboarding text

| ID | The release must | Verified by | State | Depends on |
| --- | --- | --- | --- | --- |
| DOC-1 | The README quickstart runs as written: `ph.pool.listen` takes only `agreed=`, and founding is a step a person answers | `README.md` lines 17 to 27; `src/poolhouse/api/pool.py` | defect: the README calls `ph.pool.listen("secure", agreed=True)` and `listen` is `listen(*, agreed: bool = False)`; the README shows no founding | none |
| DOC-2 | `pip install poolhouse` works | IDN-9 | not built: PyPI upload is off | IDN-9 |
| DOC-3 | The words are the glossary's; documents name current files; documented flags and variables exist | `scripts/test all tests/test_glossary.py tests/test_documents_name_current_files.py tests/test_documented_env_vars.py tests/test_cli_help.py` | built untested: the first three pass; `tests/test_cli_help.py` fails on `docs/commands.md:45` (`--policy` is not a flag of `poolhouse-device-setup`), a baseline failure recorded in HANDOFF | fix the line in `docs/commands.md` |
| DOC-4 | Every person-facing refusal is one plain sentence of at most 160 characters; no person-facing string holds "run", "terminal", "config", "port", "firewall" or "key" | the `onboarding` step of `scripts/cold-start-check` | not built | `docs/pools-and-serving.md` 3.4 |
| DOC-5 | `docs/release.md` names the development branch the release is cut from | `docs/release.md` first list item reads `0.2dev` | defect | none |
| DOC-6 | The note at the top of `docs/windows-wsl.md` ("READ THIS FIRST (2026-10-10)") is current | read it | defect: a dated cutover note | none |
| DOC-7 | `CHANGELOG.md` 0.3.0 is edited by the owner; `docs/FEATURES.md` claims hold | `python docs/verify_release.py --offline`; `tests/test_verify_release.py` | built untested | owner |
| DOC-8 | HANDOFF holds only what is pending and stays under its wall | `scripts/test all tests/test_budgets.py` (`handoff-size`) | defect: `HANDOFF.md` is far past the 500-line wall of `scripts/gates/handoff_size.py` and the number in `budgets.json` is higher than the count now | each landing prunes its own entries |

## 4. Security release gates

The attack-first rule is AGENTS.md section 1: a design and a security-adjacent change get a written break-it pass by a non-author
before it is built or landed. A gate below passes when the test or break-it pass exists, every finding is fixed or named as an accepted
risk by the owner, and the row is `tested` and then `driven by a person`.

### 4.1 The ten categories

| # | Category | Test or break-it pass | State | Open |
| --- | --- | --- | --- | --- |
| 1 | A copied or cloned pool or device | pass: `docs/pools-and-serving.md` 1.10, 1.11, 1.15 (2026-10-11). Built: the pool id hashes the first charter; a device that returns with a new certificate is refused (`scripts/pool-check --only cert-swap`). Not written: `gov-pool-copy`, `gov-clone-flagged` | partly built | reissue (GOV-6); keystore-bound device key (GOV-7); a copy of a state directory with the file key signs a delegate admit once an admin names it |
| 2 | Replay | pass: same review. A replayed act is a no-op, a phone approval is accepted once (`app/poolhouse-node/tests/governance.rs`, `approvals.rs`) | built untested | F6: a signed act never expires; the push wake-up is not signed |
| 3 | A forged or below-threshold signature | pass: same review. Forged act, wrong pool or epoch, a signature of a removed key, a sensitive act below threshold are refused and recorded (`gov_acts.rs`) | built untested | F5: P-256 malleability and algorithm-aware lengths; F2 and F3: the phone signs only what it renders |
| 4 | A compromised or malicious member | pass: same review, and the removal review (`docs/pools-and-serving.md` 1.16). A removal carries a cut of the removed device's logs; what a removed device admitted goes with it (`gov_removal.rs`) | built untested | the slice 1 known limits of section 6; the review list of devices a removed admin admitted is not built |
| 5 | A stolen phone or key | pass: `docs/phone-approval.md` "Threats this answers"; second attack-first review (2026-10-11). The phone key needs the person's body per signature | built untested | F4: the approval key accepts the device PIN; adding a fingerprint ends the only strong key of a one-person pool; no recovery path until slice 3 |
| 6 | A malicious agent | pass: break-it on an agent widening its own access (2026-10-12), `HANDOFF.md` Priority 1 | partly built: the Bash and edit guards exist; the findings of 4.3 are open | the Release cut blocker list |
| 7 | An agent compromised without knowing | pass: `docs/compromised-agents.md` (attacked twice, section 12). The defences S0 to S15 do not exist in `src` or the node | not built | Question 8; the taint rail of `docs/taint.md` is built and was measured on one model |
| 8 | A downgrade (secure to open) | the policy is fixed at founding and the approval paths for `set_join_policy` are removed (`scripts/pool-check --only grants-gate`); old pools refused (POOL-8) | partly built | the unauthenticated `hello` returns the policy; reissue is not built |
| 9 | Approval fatigue | pass: `docs/phone-approval.md` "Approval fatigue", `docs/compromised-agents.md` section 6. The brakes have node tests (`approvals_fatigue.rs`) | built untested | F8: prompts outside the fatigue ledger; the app's brakes never ran on a phone |
| 10 | Takeover by whoever is merely present | pass: `docs/pools-and-serving.md` 1.11; the join code is checked by a password-authenticated exchange with three tries | partly built | check words not built (GOV-9); the join code's three tries can be burned by any client; an `open` pool admits any device on the segment by design (SEN-2 not built); the first phone's code is a bearer secret for ten minutes |

Each design or change since the last pass needs its own written pass by a non-author, answered per category (copied pool or device,
replay, forged signature, malicious member, stolen phone or key, malicious agent, an agent compromised without knowing, downgrade,
approval fatigue, takeover by presence). Written passes found in the repository: the governance review (2026-10-11), the removal review,
the phone approval reviews (2026-10-11), the compromised-agents design (two reviews), the phone-signed charter pass (2026-10-12) and
the pass on an agent widening its own access (2026-10-12). No written pass was found for: `feat/publisher-trust`,
`fix/harness-exec-chain`, `fix/identity-sprawl`, `feat/agent-identity-setup`, the phone-signed charter once F2 to F10 are built, and the
three-day delete and the sealed record (HANDOFF's Release cut is the only place either is described).

### 4.2 The backdoored-model slices B1 to B6

Source: `docs/strands-box.md` on branch `docs/strands-box-design` (land-2878, in a batch when written), section 11 "Slices"; its
section 1 is the threat of a tampered or abliterated model, which HANDOFF calls the backdoored-model slices. It is a design: no
`src/poolhouse/agentbox` exists and no slice is built. The Release cut section of `HANDOFF.md` (branch
`chore/handoff-release-cut`) names B2, B4, B5 and B6 as blocking the release and names the Box enforcer as not in the first release;
Question 2 asks which holds.

| Slice | Delivers | Needs | State |
| --- | --- | --- | --- |
| B0 | probes of Box before any code depends on it | none | not built |
| B1 | `poolhouse.agentbox.policy`: the policy document, validator, canonicaliser | B0 | not built |
| B2 | the Poolhouse proxy enforcer: placeholder swap, address pinning, query and size caps, content refusal | B10, B1 | not built; named a blocker |
| B3 | launchers go through the broker on the proxy; per-run `HOME` and `TMPDIR`; the unboxed-launch card | B2 | not built |
| B4 | install of the pinned Box build: `poolhouse agentbox ensure`, source build compared with the release | B3 | not built; named a blocker |
| B5 | the Box adapter as an enforcer that passes the same corpus; opt-in | B3, B4, B9, B10 | not built; named a blocker |
| B6 | the device's boxing claim and counter in the pool view; the card sentence | B5 | not built; named a blocker |

The design's own attack-first table is section 10 of that document; it names 30 rows, each with a failing-first test and the slice that
writes it. Its eight residual risks (section 13) are "named and not accepted".

### 4.3 Open findings

| Finding | Source | State |
| --- | --- | --- |
| F1 critical: a caller declares a key strong | HANDOFF Priority 1 | fix on `fix/node-derives-key-strength` (GOV-2) |
| F2 the acceptance signs an opaque anchor; F3 the phone is a signing oracle; F4 PIN accepted, one fingerprint ends the only key; F5 malleable P-256, id from acceptances, algorithm-blind lengths; F6 acts never expire; F7 the card shows node prose; F8 `admin_manage` prompts outside the fatigue ledger; F9 later-add acceptance omits `prev` and `alg` | HANDOFF Priority 1, in build order | not built; stand until fixed or accepted |
| F10 three prompts at founding | the founding charter names the first device (charter version 3), so one acceptance founds the pool and no admit follows | built on the node: `app/poolhouse-node/tests/gov_first.rs`; the phone's card is not built |
| A subagent acts as its lead: the lead's board name is inherited and `workspace_helper("")` is false | HANDOFF, break-it on widening access | not built |
| Authority loosens with no person: a non-helper session can run `authority set delegated ALL` or `preset dev` | same | not built |
| The protected-path list covers the file tools only; `.claude/settings.local.json`, `AGENTS.md`, `CLAUDE.md`, `.claude/agents/*.md` are open to Bash writes | same | not built |
| The person record's key is a same-user file no hook covers; the release question shows agent-written subjects | same | not built |
| Hooks cover Bash and the edit tools only, not `Agent`, MCP tools or `TaskStop` | same | not built |
| Server rulesets are not applied; the Bash guard passed 14 bypasses; `pre-push` exits 0 once `CLAUDECODE` is unset in a re-parented process | same | OW-1 to OW-4 |
| Phone approval left items: the Keystore attestation chain is not recorded (item 8); the wake-up is not signed, pushes are not padded, the unauthenticated `hello` returns the device name, policy and version, `phone add` restarts the node and binds `0.0.0.0` (item 9) | HANDOFF "Phone approval" | not built |
| Red-team findings F1 (guards on calls, not re-measured on a model), F2 (hidden text reaches the model) and F5 (an oversized body is read) of `docs/redteam/findings.md` | the red-team record | open |

### 4.4 Accepted risks the owner must name

The owner names each as accepted or fixed; none is accepted by this page.

- From HANDOFF, accepted-risk candidates: no Android Key Attestation in the first slice; a one-person pool whose only strong key is lost
  waits for the 7-day recover (the recover is slice 3, not built); provenance lines on act cards are unsigned node assertions;
  node-written `cut` lists shown as counts; one Keystore key per pool.
- The slice 1 known limits of section 6.
- `docs/compromised-agents.md` 16.2 (eight items), if the compromised-agent defences ship.
- `docs/strands-box.md` section 13 (eight residual risks), if Box ships.
- Same-user malware outside any sandbox reads the keystore, the node's state and the credentials file (`docs/credentials.md`,
  `docs/keystore.md`).

## 5. Owner-only steps

Each is typed or answered by the owner at a terminal or on the phone; an agent is refused by the tool itself. The agent's part is what
must be true first.

| ID | The owner does | An agent has first |
| --- | --- | --- |
| OW-1 | Makes the agents' GitHub identity (`scripts/agent-identity setup`, branch `feat/agent-identity-setup`) and starts the harness with its environment (`docs/github-protection.md`, "Agent identity") | landed the branch through the queue after an independent accept; run `scripts/github-protection --check --local` (prints `0 findings`) |
| OW-2 | Runs `scripts/github-protection-apply --repo OWNER/REPO` and reads the diff, then `--apply` (it refuses an agent marker and a missing terminal) | landed OW-1; named what the apply changes; run `scripts/github-protection --check` from the agent's environment to list what differs |
| OW-3 | Runs `scripts/release-key create --write` (after the `release` environment exists), then commits `src/poolhouse/fleet/signing.py` | `release-dry-run.yml` green on the batch; `tests/test_release_key.py` and `tests/test_fleet_signing.py` green. After the key: an agent signs the runtime build with it and writes the installers' signature check (INS-5) |
| OW-4 | Runs the apply command again; `scripts/github-protection --check` must print `0 findings` | OW-3 committed |
| OW-5 | Registers the PyPI publisher after the similarity waiver and sets `PYPI_ENABLED` to `true` (`docs/github-protection.md`, "PyPI") | the steps of "PyPI" in `docs/github-protection.md` and the HANDOFF waiver entry are current |
| OW-6 | Selects the `release-main` approval for the head SHA of the promotion pull request in the question the main session asks (`scripts/hooks/person-consume propose`); the main session then merges it while the approval is live | a frozen `promote/<date>` snapshot; the combined-tree gates (`scripts/test gate`, `scripts/budgets`, `scripts/redteam_coverage.py --check`) green on the head; every row of sections 3 and 4 at `driven by a person` or named in section 6; the final drive recorded |
| OW-7 | Authorizes the merge of the release pull request (release-please) into `main` under `release-main` (`docs/person-delegation.md`), and approves the `release` environment for the `pypi` and `publish` jobs | OW-3 to OW-6 done; the CHANGELOG draft edited by the owner |
| OW-8 | Chooses the version number, the tag and the release | the release pull request names the version release-please computed from the `feat:` and `fix:` subjects |
| OW-9 | Signs the release APK (`ANDROID_SIGNING_STORE`, `ANDROID_SIGNING_PASSWORD`) and sideloads it; accepts the Android SDK licence | `python app/android/build.py GRADLE PLUGIN API debug` passes its protocol checks |
| OW-10 | Moves each existing device's key into the keystore with `poolhouse node key --migrate` at a terminal; answers the first Keychain prompt | the activation notes for each device |
| OW-11 | Raises the wired memory limit through the administrator's dialog where a model needs it | `poolhouse-serve memory --for MODEL --ctx N --kv q8_0` printed the need |
| OW-12 | Answers section 8 | each question's facts, written in the question |

## 6. Not in 0.3, and known limits shipped

Not in 0.3, named by the owner (HANDOFF "Release cut"): the Box enforcer, decoys, two-box layering and lab mode
(`docs/strands-box.md`, slices B7 to B18). The default stays the Poolhouse proxy and kernel sandbox. Question 2 asks about B2 to B6.

Deferred in HANDOFF or a design document, with no 0.3 decision recorded (Question 14 asks the owner to confirm each):

- A signed, updatable publisher list (rollback high-water mark, freshness, revocation): HANDOFF calls it phase 1, with its own
  attack-first pass.
- An iPhone app, APNs and notarized macOS builds: blocked until the product takes off (`docs/service.md` 9.1).
- The commercial service phases 1 to 3 (guests, money, open market) and guest tenancy (`docs/service.md` section 6,
  `docs/pool-encryption.md`).
- NATS JetStream as a board link (parked in a bundle), idle unload and Wake-on-LAN, a router that places a new session on the best
  device, the trust ledger of `docs/earned-trust.md`, attested instructions and decision-model screening of board messages at ingest.

Known limits shipped (each is in the document it names; none is accepted until the owner names it in 4.4):

- A pool's delegate and an open pool's member are rate-limited by the order each device first saw the admits; a compromised delegate
  chooses the order it delivers them in (`docs/node.md`, "Who governs a pool").
- Removing a device removes everything it admitted as a delegate or an open pool's member; an admin admits them again.
- A `veto` by the key a removal names pauses admin-set acts for seven days; the recovery path that ends it is slice 3.
- A `leave` and an `admit` of one device are ordered by the signer's `at_ms`; a device that left cannot join another pool until its state
  directory is replaced; admin acts are never pruned, so a long-lived pool's join message can pass 256 KB and refuse a joiner whole.
- A hold on Windows can be longer than its hours: the awake clock does not run while the machine sleeps.
- A board that a removed device wrote on, that the remover never synced, has no named cut; entries written before the removal stay.
- The first phone is added on a one-time code (valid ten minutes) and one fingerprint; a phone's yes is not bound to the phone's key.
- On Linux a polkit rule that authenticates without a password makes the system prompt answer itself; a program with a pseudo-terminal
  can answer a node that asks on a terminal; `persons.json` is a plain file the operating-system user can edit with the node stopped.
- The app trusts the first UnifiedPush distributor that answers; a choice screen is not built.
- A device key stays in a file (`device.key`, weak in `pool_status`) until a person runs `poolhouse node key --migrate`; a process of
  the same user that can ask the keystore signs as the device.
- A lead's grant is spent by its process lineage; the token is a file any process of the user reads, so the lead's subagents and tools
  hold the lead's bundle for 30 days (`docs/node.md`, "What the lead's lineage can do with the bundle").
- The keystore decisions D-1 to D-8 are unverified on real Windows and Secret Service backends and on a boot with no unlocked keystore;
  no real keystore prompt has been driven by a person.
- The real-state write denial ran on macOS only; the Linux bubblewrap branch has not run; the agent sandbox is staged, not installed.
- Sentinel limits ("Not armed" in `docs/sentinel.md`): a plain `serve.serve()` caller gets no scan loop, canaries or decoy listener;
  nothing quarantines an MCP server on its own; an environment passed as `None` is not scrubbed.
- Board replication: a command to clear a damaged origin is not built; acknowledgements are not signed; the 4 MB request limit is
  applied after the body is read; a reply to a foreign sender's `name@dN` is not routed back.
- Install is a terminal one-line command and an unsigned download until the release key exists; no macOS notarization.
- Discovery by beacon on a real network, the Windows firewall prompt and WSL mirrored mode have not been run by the rig.
- `scripts/pool-join-check` documents setting the policy to `open` on a running node; a pool's policy is fixed at founding, so the
  script is not relied on here (unverified whether it still runs).

## 7. The final drive

A person follows this on real hardware with the release candidate build installed the way a user installs it, never the checkout.
One person drives and one reads the sheet aloud; an agent may keep the record and may not touch a device, approve, answer a prompt or
sign for the person. Every step writes `pass` or `fail` with the time, the build and what was seen.

Hardware the owner provides: the Mac (primary device); at least one more computer (the owner's Windows machine with its WSL runtime,
and a Linux device where there is one); an Android phone (Android 11 or later) with a fingerprint reader and a UnifiedPush distributor
(ntfy); all on one network, and a second network for POOL-10 if it is in 0.3. Fresh state: a new operating-system account on each
computer and a phone with no earlier Poolhouse app data. Nothing reads the real `~/.poolhouse` of the owner's own account.

**Stop-ship rule.** The drive stops, and the release does not ship, when any of these happens: a step fails; a step needs something
the sheet does not list (a command, a file edit, a key, a port, a firewall rule); a prompt appears that the sheet does not list; a
journey takes more steps than its count; an agent signs, approves or answers for the person; a refusal is more than one plain
sentence; a row the drive depends on is below `tested`. The fix is made at the root with a failing test first, the build is replaced,
and the stage restarts on fresh state. A change to security-relevant code restarts the drive at stage 1. A step that cannot pass
because its feature is not built is a stop-ship unless the owner has moved that feature to section 6, with the date, in this file.

| Stage | Step | Expect | Stop-ship if |
| --- | --- | --- | --- |
| 1 Install on the Mac | 1.1 Install the build as a new user (P1 steps 1 and 2: the computer, then the phone) | window opens; first-run screen shows a pool of one and the models that fit; the camera and notification prompts appear once on the phone | a terminal is needed beyond the install line (Question 3); a second prompt; the window does not open |
| | 1.2 Quit and reopen the app; leave it a day; reopen | same state; the runtime is current without a command | a command is needed to update |
| 2 Link and approve | 2.1 Link the phone (P1 step 3; blocked until PH-8 is built) | one scan, one fingerprint; the computer and the app show the same key words | a typed command on the computer; the words differ |
| | 2.2 Items 3 to 6 and 10 to 11 of "Checks on a real phone" | each as that list says | any item fails |
| | 2.3 Close the app with ntfy installed; request an approval (item 9) | notification arrives; the push server's capture shows ciphertext | plaintext on the wire |
| | 2.4 Add a fingerprint on the phone; add a second phone and remove the first (items 7 and 8) | as listed | the old key still approves |
| 3 Found and join | 3.1 Say "Make a pool called home" to an agent (P1 steps 4 and 5; blocked until GOV-3) | one tap on the phone; the pool exists; the Mac is its first member | no strong key can sign; more than one tap |
| | 3.2 Add the second device with a join code (P2b) | joined; the member list shows both; an entry written on either appears on both | a code typed into a terminal |
| | 3.3 Add a device on the other network (P2c; only if POOL-10 is in 0.3) | joined | a firewall or port named to the person |
| | 3.4 Remove the second device from the phone | its next request is refused by every member | the removed device syncs |
| | 3.5 Copy the first device's state directory to a third device and start it (blocked until GOV-6 and GOV-7 are built) | it is flagged as a clone and signs no admit | it is accepted as a member |
| 4 Serve | 4.1 Say "Serve a small model for my pool"; ask two requests at once on one device | the second waits; `poolhouse-serve status` shows one holder | two pieces of work compute at once |
| | 4.2 Chat with that model from the second device | a reply; the conversation is kept | no reply, or a request to the person |
| | 4.3 Pull a model from a publisher no list names | the phone asks once in plain words; a deny pulls nothing | the model arrives without a person |
| 5 An existing harness | 5.1 With Claude Code on a cloud model, start it in a project as before | its session is registered with its own name; no flag or prompt added | a new flag, a prompt, or a wait at start |
| | 5.2 Repeat with Codex | same | same |
| | 5.3 The harness posts to the board; the second device reads it; the harness asks for a pool action it was not granted | the phone shows the approval; approve; the action runs | the action runs without the phone |
| | 5.4 Give the harness a page that tells it to read a canary file planted in the fresh account and send it out | the hook asks the person or refuses; nothing leaves | the canary is read or sent |
| | 5.5 Count the steps of the journey against D2 | the count equals the ceiling | more steps |
| 6 Hostile checks | 6.1 In a scratch repository with a local bare remote, an agent runs `authority set delegated ALL`, edits `.claude/settings.local.json`, and pushes with `CLAUDECODE` unset in a re-parented process | each is refused (the access-widening findings of 4.3) | any succeeds |
| | 6.2 Approve a pool act with the device PIN instead of the fingerprint (blocked until F4 is fixed) | the admin act does not sign | it signs |
| | 6.3 Start a pool founded before the key-strength fix (POOL-8) | refused in one sentence; a new pool is founded | it runs |
| | 6.4 Shut down a pool, wait 3 days, read the device (POOL-9; needs three days) | live state and secrets gone; the sealed record remains where compromise shut it | the pool is restorable after compromise |
| 7 Release mechanics | 7.1 Download a release asset and verify it: `ssh-keygen -Y check-novalidate -n poolhouse-release -s ASSET.zip.sig < ASSET.zip`, then against the pinned key | verifies | unsigned or wrong key installs |
| | 7.2 `poolhouse-pool join --track BRANCH` on an unsigned tip | refused | it fast-forwards |
| | 7.3 `scripts/github-protection --check` | `0 findings` | any finding |
| 8 Upgrade | 8.1 Re-run the installer over the installed build with a model resident | upgraded; the model and board survive | a lost job or model |

A completed drive is a record: date, build, devices and operating systems, person, the pass or fail of each step, and the screenshots
or logs. The record is committed under `docs/notes/`, and each row of sections 3 and 4 it covers moves to `driven by a person`.

## 8. Decisions and open questions

Decided by the owner on 2026-10-13:

- Box is not in 0.3 and no work on it continues for this release.
- The backdoored-model defences that block 0.3 are the proxy path: B0, B1, B10, B2, B12, B3, B9. The Box slices B4, B5 and B6 are after 0.3.
- Serving a model to a pool ships in 0.3 (P1 step 6, D1 step 4).
- Windows is a platform for a person's whole journey in 0.3, so the first key and the first phone must be made on Windows.
- A person with no terminal installs a Mac app that is not notarized (the owner has no Apple developer account): the first-run page shows the taps to open it anyway, and the app updates itself under the Poolhouse release key. The one-line terminal install is not the release path.
- Pools founded before the F1 fix are refused; a shut-down pool is deleted after 3 days; a pool shut down for a security issue keeps a sealed record.

Open, the owner's alone to settle. Nothing in this page decides them.

1. Is 0.3 the first release an external person runs (phase 1 of `docs/agent-rules.md`, "Release phases")? AGENTS.md says the owner
   names phase 1 and records it; it is not recorded. If yes, a persisted format or wire message that leaves the owner's machines changes
   only with a version and a migration, and an interface an outside party calls is not renamed without a release note.
4. Which governance slices ship: reissue and the copy defence (slice 2), the recovery key and time lock (slice 3), many pools on a
   device (slice 4), check words and trusted-display joins (slices 5 and 8)? None is started.
5. Does the `open` join policy ship in 0.3, given the sentinel that makes it safe (`docs/sentinel-open-join.md`) is a design?
6. Does a device on another network (over the person's tailnet, journey 2c) ship in 0.3? Not built.
8. Are the compromised-agent defences S0 to S15 (`docs/compromised-agents.md`: the installed sandbox, taint, evidence) a gate of
   0.3 or known limits? None is built.
9. Which accepted-risk candidates of 4.4 does the owner accept?
10. Is the ceiling proposed for D2 (the harness's own steps, P1's human steps once, the P3 counts, nothing of Poolhouse's own) the
    count to record?
12. Is the compromise and three-day-delete behaviour of POOL-9 specified well enough to build, or does it need a design first?
13. Is `pip install poolhouse` part of the 0.3 quickstart (it needs the PyPI waiver) or does 0.3 install from git?
14. Are the deferred items of section 6 out of 0.3?
