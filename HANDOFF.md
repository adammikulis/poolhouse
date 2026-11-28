# Handoff

The backlog, highest priority first. Take work from the top; a paused tier starts only when the owner asks. Delete a row in the change that lands it and add nothing finished. "Open, checked" and the sections beside it were checked against the tree at `ac0639291`; an entry of the long handoff at `git show ac0639291:HANDOFF.md` is reconciled against the code before anyone acts on it. An older backlog is `docs/notes/release-backlog-2026-10-15.md`.

A branch named below without a pull request is on GitHub and unmerged. Every item needs an accepting review of its exact commit before it lands; security items also need an attack pass.

## 1. Speed of the local model (active)

The local delegate takes no work until every row here is landed and installed, or measured and dropped.

| Order | Item | Next | Where |
|---|---|---|---|
| 1 | A long delegate run keeps the server's prompt cache | Review, checks, merge, install, restart the model server, rerun a long delegate task as the live proof | #113 |
| 2 | Current llama.cpp with the newest draft head serves Qwen3.8-Flash-Next by default | Start it through the broker from the branch, compare clean speed with the older fork, attack pass on the automatic head download, settle overlap with the Strata change | #120, branch `feat/bench-engines` |
| 3 | Strata engine | Run its tests, attack pass, merge, install; measure on the Apple silicon computer, then on a Windows computer with a 24 GB card and 64 GB of memory. Publishers ISTA-DASLab and incoai are not admitted for agent downloads: the owner decides | #116 |
| 4 | Faster serving settings the measurements show, then the 27B quant comparisons | Scheduled jobs first need three failing tests settled and an attack pass | #107, `docs/examples/qwen3.8-27b-quants.queue` |
| 5 | Delegate work resumes, one small testable task at a time | The two runtime selection tests that fail on development, the test-node build test that needs `cargo`, and `scripts/test quick --explain` running tests | `tests/test_runtime_selection.py`, `tests/test_testnode.py`, `scripts/test` |

## 1. Security of pool membership (active)

| Item | Next | Where |
|---|---|---|
| Joining a pool needs an explicit choice, and the serving device decides who gets a board | Attack pass, checks, `scripts/pool-check`, proof on two computers. It contains #110 | #118 |

## 2. Devices work together (paused)

The aim is one phone app, and Mac and Windows apps, ready to pair; the owner is told when all three are.

| Item | Next | Where |
|---|---|---|
| What another device or a local program can do without a signed-in window or beyond its own work | Finish before member compute lands: remaining unsigned routes on a device with no pool, member jobs running as the serving user, stopping other members' jobs, remote tests without a sandbox | branch `worktree-agent-ae3737d798aecbad8`, #110 |
| Pool founding, sync and joining by code | Attack pass again; land with or through #118 | #110 |
| Remote jobs, files and chat between members | Rest of the tests, pull request | branch `worktree-agent-ae3737d798aecbad8` |
| Windows runs natively, without WSL | Pull request, green Windows installer check, install over an older release, then confinement of the model server and agents | branch `worktree-agent-adc2b4ba43534d41d`, #112 |
| Phone linking to a computer, including native Windows | Attack pass of the Windows permission changes; take its Windows fixes into the native branch | #111 |
| Android app | Join or found a pool from the app, guest and device-uses screens, then a test on a phone | #114, branch `feat/android-node-mode` |
| Phone node runs no code from other devices | Wire the node module into the app | branch `backup/phone-node` |
| VM prototype for the phone, not wired in | Review and land as a prototype | #119 |

## 3. New features (paused)

| Item | Next | Where |
|---|---|---|
| Private compute-only guest access with a one-scan join | Coverage rows, pull request, attack pass, app screens; then the llama.cpp change that lets one shared server keep each person's context apart in memory | branch `worktree-agent-afb66e817d395794c` |
| Tailscale inside Poolhouse | Compile the helper, daemon control, sign-in and share screens | #115 (draft) |
| Screens saying what each device can do | Show only what works across devices | #117 |

## 4. Landing, checks and friction (paused)

| Item | Next | Where |
|---|---|---|
| Merge-time checks and a bounded hung test | Second reader for the merged files | #99 |
| A delegate commits inside its sandbox; generated delegate briefs follow it | Attack pass, review; then rebase the briefs | #101, branch `backup/delegate-briefs` |
| Tests and uninstalled copies are refused the real state folders | Finish the test run, attack pass; remove the development-linked install just before merging | #104 |
| No hook rewrites a command | Second reader; afterwards a running session needs a restart | #106 |
| Hook failures reach the agent | Attack pass again | #108 |
| Risk-tiered reviews in the merge check, then reviewed branches editing guard code | Pull request and attack pass for the first; the second lands after it | branch `worktree-agent-a46c203791603610b`, branch `backup/guard-rule` |
| Guards let an agent edit a person's global settings, which the rules forbid | The owner decides | `tests/test_protected_writes.py` |
| Comparison of agent-to-agent protocols (the tried protocol did not pass; its code stays unmerged) | Land only the comparison document | branch `backup/acp-trial` |
| Failing on development | Red-team coverage floor, `cargo-audit`, `npm-audit`, the macOS affected-tests job's Swift toolchain error | CI |
| The real-name list the commit hooks read is not readable, so those names are unchecked; the guard refuses `llama-server --help` | Fix both | `scripts/hooks` |
| Example network addresses in documents | Review and merge | #121 |

## 5. Later

| Item | Where |
|---|---|
| Native-speed isolated code execution on the phone; starts only when the owner asks | `app/phone-isolation/README.md` in #119 |
| macOS signing certificate opt-in; images and other input across the product; macOS read-ahead patch for llama.cpp; Splash engine trial; delegate runs visible in the app; alerts on unanswered agent questions; remaining browser failures; cleanup of merged worktrees; the same required checks on `main` | |

## A person has to do these (guard paths an agent cannot edit)

| Item | State | Where |
|---|---|---|
| Rename `caller` to `agent_marker` and the push-credential role `lander` to `publisher` | Built on branch `w3-held` (worktree `/Users/adammikulis/Documents/repos/poolhouse.worktrees/w3-renames`, commit `c67c750c9`, 119 files). It cannot land alone: apply `guarded-scripts.patch` (54 changed lines) to `scripts/release-key`, `scripts/github-protection-apply` and `scripts/github-protection`, then run `scripts/redteam_coverage.py --write` and push. The `--lander` flag in `scripts/github-protection` becomes `--publisher`; `tests/test_github_protection.py` and `tests/test_github_protection_device.py` wait for it | `git show c67c750c9` |
| Release signing key | Does not exist (owner: yes, 2026-10-13); `scripts/release-key create --write` needs the owner's selected approval. Every pin and trusted build roots in it | `docs/person-delegation.md`, `docs/release-readiness.md` |
| GitHub protection | `scripts/github-protection-apply --apply` not run (the release environment first, then the rest once the agent identity and signing key exist) | `docs/github-protection.md` |
| Release question patch | `docs/access-widening-person-files.patch` is not applied (a person at a terminal; the edit guard stops an agent). The command is in the patch's own header; delete the file after | `src/poolhouse/workspace/person_release.py` |

## Owner decisions

| Decision | Where |
|---|---|
| Accepted risks of the session-capability design (section 7) | `docs/design-session-capabilities.md` |
| Weaker act classes of the phone-free design (a 7 day hold for every pool, `member_remove` by a computer key against a phone owner, the floors now inert) | `docs/design-phone-free-pools.md` |
| Multi-pool leaver offline longer than the one-day grace (accepted risk) | `docs/design-multi-pool-devices.md` |
| Lead agents installing login units: decided 2026-10-16, not built; findings F1 to F16 are open in the current code, F15 and F1 first | `docs/agent-login-unit-install.md` |
| A refused or shut-down pool deleted after 3 days (owner, 2026-10-13); a compromised pool is sealed read-only and never restorable. Not built | `docs/design-phone-free-pools.md` |
| Not in the first release: the Box enforcer, decoys, two-box layering, lab mode | none in the repository |

## Open, checked

| Item | State | Where |
|---|---|---|
| `node_key.migrate` passphrase branch | Reported unreachable by the rename review (the `elif passphrase` arm in `migrate`); not re-proved here. Delete it with its test once confirmed | `src/poolhouse/node_key.py` |
| `models_adopt` old-name handling | Not removed; the brief that named it gave no further detail, so read the module before cutting | `src/poolhouse/serve/models_adopt.py` |
| `bench` builds a `telemetry.Call` itself | `Call.from_reply(reply, 0.0)` in the backend instead of the client's own record | `src/poolhouse/bench/backends.py` |
| Canned node replies in tests | Seven files marked PLUMBING ONLY: `test_fleet_plan.py`, `test_setup.py`, `test_fleet_tls.py`, `test_node_health_deadline.py`, `test_node_service_pool.py`, `test_fleet_pausing.py`, `test_fleet_roster.py`. Replacing them with `two_nodes` needs node-side service unbind | `tests/` |
| `contracts/node/retained-features.json` (908 KB) | About 480 stale paths for deleted code. No script, source file or test reads it, but 11 files of `contracts/node/` (`operation-mapping.json`, ten `domain-evidence/*.json`) and `contracts/glossary-allowlist.toml` cite it with a sha256, so deleting it means rewriting those pins | `contracts/node/` |
| `scripts/sync-dev` | Still its own shell fast-forward, beside `scripts/primary_sync.py`. Merging them changes the messages `tests/test_sync_dev.py` asserts and adds a Python dependency to a script meant for a plain clone | `scripts/sync-dev` |
| Phone and computer approval | Not driven on a real phone or a real OS prompt; the phone-admin-add listing is refused until it is bound to the verified pool, epoch and charter; `NativeGovernance` is not wired into the production review path | `docs/phone-approval.md`, `docs/design-phone-free-pools.md` |
| Founding dialog on the owner's Mac | Never seen; the failure now prints the system's own words. Needs the owner's Mac and a person at the prompt | `docs/design-phone-free-pools.md` |
| Session credential sandbox | Not proven: the generated Codex configuration has no read-deny rules, a hard link made before confinement stays readable, and the installed wrappers were not tested. Do not claim S1 or S7 credentials are protected | `docs/agent-sandbox.md` |
| Registration of a retired session's native id | A different terminal can register it and spend its existing grants; do not reclaim sessions until revival creates authority that cannot inherit them | `docs/design-session-capabilities.md` |
| Model sharing between devices | No node design: add-device with a model, join-with-code sharing, accept, remove and `fetch` | `docs/pool.md` |
| Test shards between two real devices | Not driven: the Windows inbound-rule prompt, WSL in NAT against mirrored mode, a WSL device without `~/.poolhouse/wsl-dev-venv` | `docs/test-farm.md` |
| Design reviews open | Second attack notes: session capabilities N1 to N3, pool keys G1, G2, G4 and G10, signed ordering N2 to N8 (revise before building) | `docs/notes/attack-*.md` |
| LiteRT on Android | No benchmark has run; the S26 Ultra was not given the app or a model | `docs/litert-lm-gemma4-pin-2026-10-16.md` |
| Isolated launcher | A person's coding launch runs in place; `--sandbox` and delegates use the macOS confinement (print mode). `poolhouse-delegate` (OpenCode 2.4.2, Qwen3.8-Flash-Next) was proven live on 2026-11-27: read-only with note and question, and six of six one-line fixes with the test run inside the box. A refused write now ends a delegate `partial` or `unapplied` (seen with the pinned OpenCode against the marked canned server; no model run yet, and not on the installed runtime). `--on` sends a key only to a server shown to be this computer's own (a worktree run on 2026-11-27 against the running Qwen3.8-Flash-Next server passed `localhost` and refused `[::1]`; that was before the key needed its process's start time; a server the installed build keyed is proved from its lease record and listener at first use (tested on real processes with records in the old form, not against the live server); no harness was run in it, and not on the installed runtime). Open: Linux, WSL and Windows confinement; harder delegate tasks unmeasured; a refused write a program makes without its shell line naming it is not seen; the egress proxy's wiring (no `--allow-net`); the unwired `workspace.task_caps` hook module; nothing calls `service_draft`/`service_bind`; older test failures (`test_workspace_local_agent`, testfarm approval's session quota, setup-preparation context, saved-models picker) | `docs/design-isolated-launcher.md`, `docs/harnesses.md` |
