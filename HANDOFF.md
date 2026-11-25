# Handoff

What is open and was checked against the tree on 2026-11-23 (`ac0639291`), plus the owner's decisions. Delete a row when its work lands; add nothing finished. The earlier long handoff, with entries this pass did not re-check, is at `git show ac0639291:HANDOFF.md`; reconcile such an entry against the code before acting on it. The older backlog is `docs/notes/release-backlog-2026-10-15.md`.

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
| `scripts/commit_types_chart.py`, `scripts/compare-harnesses`, `scripts/prepare_medmcqa.py` | One-shot scripts listed for deletion (`docs/design-shrink-surface.md`, slice 7); each has a test and a doc that still names it (`docs/harnesses.md`, `docs/medical-decision-model-plan.md`, `docs/development.md`, `docs/experiments/harness-comparison.md`) | `tests/test_commit_types_chart.py`, `test_compare_harnesses.py`, `test_prepare_medmcqa.py` |
| `scripts/sync-dev` | Still its own shell fast-forward, beside `scripts/primary_sync.py`. Merging them changes the messages `tests/test_sync_dev.py` asserts and adds a Python dependency to a script meant for a plain clone | `scripts/sync-dev` |
| Phone and computer approval | Not driven on a real phone or a real OS prompt; the phone-admin-add listing is refused until it is bound to the verified pool, epoch and charter; `NativeGovernance` is not wired into the production review path | `docs/phone-approval.md`, `docs/design-phone-free-pools.md` |
| Founding dialog on the owner's Mac | Never seen; the failure now prints the system's own words. Needs the owner's Mac and a person at the prompt | `docs/design-phone-free-pools.md` |
| Session credential sandbox | Not proven: the generated Codex configuration has no read-deny rules, a hard link made before confinement stays readable, and the installed wrappers were not tested. Do not claim S1 or S7 credentials are protected | `docs/agent-sandbox.md` |
| Registration of a retired session's native id | A different terminal can register it and spend its existing grants; do not reclaim sessions until revival creates authority that cannot inherit them | `docs/design-session-capabilities.md` |
| Model sharing between devices | No node design: add-device with a model, join-with-code sharing, accept, remove and `fetch` | `docs/pool.md` |
| Test shards between two real devices | Not driven: the Windows inbound-rule prompt, WSL in NAT against mirrored mode, a WSL device without `~/.venvs/poolhouse-dev` | `docs/test-farm.md` |
| Design reviews open | Second attack notes: session capabilities N1 to N3, pool keys G1, G2, G4 and G10, signed ordering N2 to N8 (revise before building) | `docs/notes/attack-*.md` |
| LiteRT on Android | No benchmark has run; the S26 Ultra was not given the app or a model | `docs/litert-lm-gemma4-pin-2026-10-16.md` |
| Isolated launcher | L1 (the engine), L2 (the library), L3 (the model bridge, the session bridge, the egress proxy and the client) and L4 (the workspace: the publication decision, the barrier, the session wrapper `isolation.workspace`) landed; slices L5 to L9 pending, nothing calls them yet (L4 leaves the per-path hold-back, the chat and task record, the in-place capture and the Linux runs open: the design's L4 row). The board's node has no `isolation.report` message type (the report's kind is the subject of a `status` message from the launcher's session): a decision for the owner | `docs/design-isolated-launcher.md` |
