# Handoff

The backlog, highest priority first. Take work from the numbered tiers top down; a paused tier starts only when the owner asks. Delete a row in the change that lands it and add nothing finished. A row under the later headings is reconciled against the code before anyone acts on it.

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

## 2. Security of pool membership (active)

| Item | Next | Where |
|---|---|---|
| Joining a pool needs an explicit choice, and the serving device decides who gets a board | Attack pass, checks, `scripts/pool-check`, proof on two computers. It contains #110 | #118 |

## 3. Devices work together (paused)

The aim is one phone app, and Mac and Windows apps, ready to pair; the owner is told when all three are.

| Item | Next | Where |
|---|---|---|
| What another device or a local program can do without a signed-in window or beyond its own work | Finish before member compute lands: remaining unsigned routes on a device with no pool, member jobs running as the serving user, stopping other members' jobs, remote tests without a sandbox | branch `worktree-agent-ae3737d798aecbad8`, #110 |
| Pool founding, sync and joining by code | Attack pass again; land with or through #118 | #110 |
| Remote jobs, files and chat between members | Rest of the tests, pull request | branch `worktree-agent-ae3737d798aecbad8` |
| Windows runs natively, without WSL | Pull request, green Windows installer check, install over an older release, then confinement of the model server and agents | branch `worktree-agent-adc2b4ba43534d41d`, #112 |
| Phone linking to a computer, including native Windows | Attack pass of the Windows permission changes; take its Windows fixes into the native branch | #111 |
| Android app | Join or found a pool from the app, guest and device-uses screens, then a test on a phone | #114, branch `feat/android-node-mode` |
| Approvals in the app, with a person deciding, for the actions that person chooses; protections stay automatic | Keep the node's approval protocol. #114 removes the old approval app and its native library, so the new app needs the approval screens. Settle which actions can ask and what happens with no answer, and state it in the rule of `AGENTS.md` that nothing waits for a person | `docs/phone-approval.md`, `app/poolhouse-node/src/gov/`, #114 |
| Phone node runs no code from other devices | Wire the node module into the app | branch `backup/phone-node` |
| VM prototype for the phone, not wired in | Review and land as a prototype | #119 |

## 4. New features (paused)

| Item | Next | Where |
|---|---|---|
| Private compute-only guest access with a one-scan join | Coverage rows, pull request, attack pass, app screens; then the llama.cpp change that lets one shared server keep each person's context apart in memory | branch `worktree-agent-afb66e817d395794c` |
| Tailscale inside Poolhouse | Compile the helper, daemon control, sign-in and share screens | #115 (draft) |
| Screens saying what each device can do | Show only what works across devices | #117 |

## 5. Landing, checks and friction (paused)

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
| `docs/access-widening-person-files.patch` targets files that do not exist | Delete it | `docs/` |

## 6. Later

| Item | Where |
|---|---|
| Native-speed isolated code execution on the phone; starts only when the owner asks | `app/phone-isolation/README.md` in #119 |
| macOS signing certificate opt-in; images and other input across the product; macOS read-ahead patch for llama.cpp; Splash engine trial; delegate runs visible in the app; alerts on unanswered agent questions; remaining browser failures; cleanup of merged worktrees | |

## Release setup and guard paths

| Item | State | Where |
|---|---|---|
| Release signing key | Does not exist; the owner wants it, and `scripts/release-key create --write` needs the owner's approval. Every pin and trusted build roots in it | `docs/person-delegation.md`, `docs/release-readiness.md` |
| GitHub protection | `main` does not require the pull request and checks the development branch does; `scripts/github-protection-apply --apply` has not been run (the release environment first, then the rest once the agent identity and signing key exist) | `docs/github-protection.md` |
| Rename `caller` to `agent_marker` and the push-credential role `lander` to `publisher` | An earlier build is the local commit `c67c750c9` (local branch `hold/w3-renames`). Its changes to `scripts/release-key`, `scripts/github-protection-apply` and `scripts/github-protection` need the guard-code rule in section 5 | `git show c67c750c9` |

## Open from earlier work

| Item | State | Where |
|---|---|---|
| Lead agents installing login units | Decided, not built; findings F1 to F16 are open, F15 and F1 first | `docs/agent-login-unit-install.md` |
| A refused or shut-down pool is deleted after 3 days; a compromised pool is sealed read-only and never restorable | Not built | `docs/design-phone-free-pools.md` |
| Session credential sandbox | Not proven: the generated Codex configuration has no read-deny rules, a hard link made before confinement stays readable, and the installed wrappers are untested. Do not claim S1 or S7 credentials are protected | `docs/agent-sandbox.md` |
| Registration of a retired session's native id | A different terminal can register it and spend its grants; do not reclaim sessions until revival creates authority that cannot inherit them | `docs/design-session-capabilities.md` |
| Design reviews | Second attack notes open: session capabilities N1 to N3, pool keys G1, G2, G4 and G10, signed ordering N2 to N8 (revise before building) | `docs/notes/attack-*.md` |
| Model sharing between devices | No node design: add-device with a model, join-with-code sharing, accept, remove and `fetch` | `docs/pool.md` |
| Test shards between two real devices | Not driven on real hardware, including the Windows inbound-rule prompt | `docs/test-farm.md` |
| Isolated launcher and delegates | Linux and Windows confinement; harder delegate tasks unmeasured; a refused write a program makes without its shell line naming it is not seen; no `--allow-net` for the egress proxy; the `workspace.task_caps` hook module is not wired; a refused write ending a run `partial` or `unapplied`, and the `--on` key proof, lack a live run on the installed runtime; older failures to re-run (`test_workspace_local_agent`, the testfarm approval's session quota, setup-preparation context, saved-models picker) | `docs/design-isolated-launcher.md`, `docs/harnesses.md` |
| Canned node replies in tests | `test_fleet_plan.py`, `test_setup.py`, `test_fleet_tls.py`, `test_node_health_deadline.py`, `test_node_service_pool.py`, `test_fleet_pausing.py` and `test_fleet_roster.py` answer for the node; replacing them with `two_nodes` needs node-side service unbind | `tests/` |
| `node_key.migrate` passphrase branch | Reported unreachable (the `elif passphrase` arm), not proved. Delete it with its test once confirmed | `src/poolhouse/node_key.py` |
| `bench` builds a `telemetry.Call` itself | `Call.from_reply(reply, 0.0)` in the backend instead of the client's own record | `src/poolhouse/bench/backends.py` |
| `contracts/node/retained-features.json` | Mostly paths of deleted code; no script, source or test reads it, but other files in `contracts/` cite it with a sha256, so deleting it means rewriting those pins | `contracts/node/` |
| `scripts/sync-dev` | Its own shell fast-forward beside `scripts/primary_sync.py`; merging them changes messages `tests/test_sync_dev.py` asserts and adds a Python dependency to a script for a plain clone | `scripts/sync-dev` |

## Not in the first release

The Box enforcer, decoys, two-box layering and lab mode.
