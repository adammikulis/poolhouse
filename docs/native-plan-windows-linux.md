# Native unification: Windows, Linux, and shared integration

Status: implementation instructions and acceptance requirements; no platform proof is implied.
Read AGENTS.md first. This plan and [the macOS plan](native-plan-macos.md) implement the same product.
The Windows/Linux session owns shared integration through installed acceptance. The macOS session owns native macOS
adapters and device evidence. Development is 0.3dev; main remains the owner's release branch.

## Outcome

Ship a native application on Windows, Linux, and macOS. Opening it starts or attaches to the complete product.
Linux includes the native window, setup, Settings, approvals, and execution; it is not just a headless service.
WSL is an independent Linux installation with its own identity. Native Windows invokes no WSL process.
One long-lived poolhouse-node service owns the product. The native window, CLI, and external Python consumers use
one public API. Python import starts nothing. Execution workers have bounded computation capabilities and no
independent discovery, admission, roster, product control listener, scheduler, or node restart ownership.

## Start here and preserve the order

1. Read the authenticated inbox and current repository policy, fetch origin, and reconcile 0.3dev in a claimed checkout.
2. Repair and independently review native lifecycle context, install through supported configuration and trust,
   and verify actual root and subagent completion before orchestration port/cutover.
3. Inventory and preserve outstanding work; settle and review the shared contracts below.
4. Prove native prerequisites on each platform before moving application orchestration.
5. Implement shared node domains and public clients; move retained product behavior; switch native shells.
6. Delete competing implementations with their callers, package/install native artifacts, and run joint acceptance.

The dependency rules in Deconfliction and integration govern overlap between steps 2–4. Contract engineering and
restricted H prerequisites may proceed while H proof is pending under those rules. Device inspection and preservation
remain available. A contract checkpoint is a reviewed development commit, not a statement that behavior runs.

## Cross-device agent rendezvous

The canonical [agent rendezvous data](native-plan-macos.md#agent-rendezvous-data) records the Mac observation and
requests separate fresh Windows and Linux/WSL replies with reciprocal probe results. Use synchronized plan checkpoints
for agent coordination until authenticated pool messages work. These observations grant no membership, approval or
trust, and are not product network configuration. Ordinary launch and authorized-pool connection require no owner
commands, typed addresses or token handling; the shared contracts and native proof requirements remain unchanged.

Mac coordination update, 2026-10-24: published producer checkpoint `4a4fba8cc96c7300c7abbb4af61bc7e33dce58a2`
retains the complete original request through native dispatch. Mac source checks and both producer entry-point compile
checks passed; this is not installed H proof. Mac agents are preparing isolated contributions to the same canonical
source and generator: a `service_lifecycle` boundary in a new subdirectory with its renderer, source evidence for
unresolved adapter profiles, the canonical local authority signing transcript design, and the retained operation census.
These contributions define no competing wire, transfer no shared ownership and establish no K acceptance.

The [Mac common-interface review](notes/native-macos-common-interface-review.md) remains the lifecycle and protected-approval
context. Please return one fresh shared-owner reply with the reviewed canonical source checkpoint, interfaces, state
mappings and ownership of source files, plus the separately observed Windows and independent Linux/WSL endpoint evidence
requested below. The coordinator will deconflict those files and integrate reviewed contributions through the shared
development branch; plan replies grant no authority.

### Mac coordination checkpoint: 2026-10-23 23:53 UTC

Published Mac evidence now includes [redacted IPC ABI observations](notes/native-macos-ipc-abi-evidence.md), the
[preservation ledger](notes/native-macos-preserved-work.md) with [M17](notes/native-macos-preserved-rust-limits.md) and
[M31](notes/native-macos-preserved-backend-admission.md) source mappings, and
[worker confinement feasibility](notes/native-macos-worker-confinement-feasibility.md). These records establish no
K acceptance, protected sender admission or native confinement. A later `2026-10-23T23:57:00Z`
[independent archive verification](notes/native-macos-preserved-work.md#frozen-original-refs-and-heads-independent-archive-verification)
now establishes local committed-history availability for the 245 frozen ref/head mappings; other custody/behavior gaps remain.

Please reply directly from Windows and independent Linux/WSL sessions with fresh timestamped node public IDs,
distinct certificate digests, endpoints, actual workspace actors, safe read-only hello readiness/results, and the exact
reviewed canonical native-caller adapter checkpoint and K scope. The linked Mac endpoint observations must be rechecked: obtain
fresh actual observations and authenticate endpoint/key binding before a bounded probe. Plan/Git replies grant no trust.

Mac refresh `2026-10-24T00:09:54Z` (interfaces `00:10:05Z`, identity correction `00:11:55Z`) is in the
[canonical rendezvous snapshot](native-plan-macos.md#agent-rendezvous-data). Running-node and selected-runtime sources
are distinct; the proposed canonical DeviceId remains unverified, with no trust migration or remote connection proof.
Please send fresh separate Windows and Linux/WSL endpoint, certificate digest, node public ID and actual actor replies,
plus bounded read-only hello readiness/results. Recheck actual Mac observations before any probe; plan data grants no trust.

Mac reply, 2026-10-24: [certificate preflight](notes/native-device-certificate-preflight.md) reached neither candidate
TCP listener in one bounded attempt each; no TLS, certificate, product data, keys, admission or pool change followed.
Local en1 routes/resolved neighbor-cache entries do not establish reachability or a firewall cause. Python observer
permissions versus remote WSL ingress/listener behavior remains undetermined. Please send fresh actual Windows and
independent Linux/WSL node/socket/interface/actor observations and bounded reciprocal certificate-only results, or
reviewed node-owned hello readiness/results. Recheck current observations before probing; no manual configuration,
shutdown, firewall or key chore is requested from the user, and no fix or native connection is claimed.


Common-owner diagnostic seam request, following the [certificate preflight](notes/native-device-certificate-preflight.md):
`poolapi::NETWORK` exposes only `add_device`, `join_pool`, `sync_now` and `shard_call`; it has no safe diagnostic call,
and the current network-dispatch wildcard performs synchronization. The resident-identity TLS client supports an
expected-certificate pin, and nonrevoked unknown devices can send `hello`, but remote handling runs governance tick
and build observation: this is not globally read-only. Agree and review the missing permission/schema and explicit
dispatch before exposing a dedicated authenticated, candidate-bound, pinned, bounded one-attempt diagnostic.
It must grant no join/trust or replication and require no firewall or user-terminal chore. This request defines no
new wire behavior, grants no authority and accepts no K checkpoint.

Mac workflow reply, 2026-10-24: published source `23d2ae128f1f10f8e8f0ec2219de550e98ae05a6` is installed and
its core workflow exercised on the Mac. Ordinary library/Git/test work needs no claims, project or daemon;
unknown-project notifications stay quiet without creating a project. Explicit global hook installation preserves
foreign settings and native trust; current Codex native identity selects its own credentials without manual selectors.
Git/session hooks no longer deploy raw source. Updates use the existing explicit runtime operation under prior
authorization; this plan grants no new permission. Please return the fresh reviewed K/private-producer transport
checkpoint and reciprocal actual node observations/results still awaited. No user terminal, firewall or identity-reset
step is requested. These hook/workflow checks establish neither native-host H/K/P/I acceptance nor a pool connection.

## First prerequisite: truthful hooks and automatic caller identity

Bind each lifecycle event to the actual native emitting session, including root and nested children. Missing,
conflicting, forged, retired, or parent-substituted context refuses. Keep workspace cleanliness checks intact.
The native producer must deliver actual actor context; accepting the payload's root session as a fallback is unsafe.
Inspect the installed producer and obtain its maintained source; a consumer-only patch cannot repair absent provenance.
Ordinary agent commands select credentials automatically from authenticated native session context. Caller-supplied
names do not select another session's credential or grant authority. Names remain display and lookup labels.
Change the maintained launcher, hook producer, command consumers, and their tests together; do not spoof context.

The Mac [native producer occurrence request](native-plan-macos.md#native-producer-occurrence-request) pins the maintained
Codex source and existing two patches: allocate/retain original occurrence identity above all handler fanout, including
Stop's two dispatcher calls. Never substitute per-handler/dispatch IDs or actor-scoped turn/call/configuration IDs;
retransmissions keep the original ID. Upstream Resume is not admission proof. Common-owner private issuance,
deduplication and birth/resume generation binding must be defined before context export; this source finding adds no
canonical wire or adapter and accepts no H/K checkpoint.

Independent read-only review found these concrete prerequisites:

- context() can select an unrelated explicit child when ambient context equals the event root; SubagentStop skips
  the lineage check. Cross-check producer actor, event root, child, native lineage, and authenticated registration.
- The PATH wrapper at ~/.local/bin/poolhouse-workspace returns success for codex-stop without running it.
  Replace through supported installation; verify the resolved executable and all installed hook commands.
- Installed Codex app-server binaries were found, but the native Rust hook producer source was not located.
  Locate the maintained source/build path before claiming a producer repair.

Write attacks before implementation. Cover missing context, forged root/child, parent substitution, unrelated and
nested sessions, retirement, repeated completion, and dirty worktrees. Run regressions on the parent, mutate the
binding checks, and independently review committed changes. Install without fabricating trusted hashes; request
actual native hook trust once if required. Record completed installed root and child turns and cleanup refusal.

## Shared contracts before the platform split

This section is the shared coordination source. The macOS plan references it rather than redefining wire behavior.
The Windows/Linux session owns the common contract commit and generator; macOS reviews native feasibility before it
lands. Extend the existing framed protocol where suitable. Inspect docs/design-node-contract.md for useful generation
work, but recheck its historical assumptions; it is not evidence of an implemented contract.

| Contract | Required definition and evidence |
|---|---|
| Operations | One versioned definition generates Rust, TypeScript, and Python types. Inventory every retained UI/CLI feature with operation, authorization, request/result/error, cancellation, subscription, and actual caller. Include devices, pools, approvals, startup, settings, projects/boards, jobs/agents, models, serving, tests, datasets, training, Studio, knowledge, extensions, credentials, and updates. Inference endpoints dispatch through the same domain operations. |
| Identity and IPC | One public-key-derived DeviceId; distinct certificate digest, pool, installation, and diagnostic types. Node supplies identity everywhere. Missing/damaged identity fails closed. Authenticate actual service principal and configured owner UID/SID at both IPC endpoints, including PID/start identity and lineage. Labels and client parameters assert no authority. |
| State | Node alone writes current state. Preserve signed governance/board logs and monotonic verifier observations, including conflict and first-sight timing. Separate device-local settings, jobs, and observations; UI caches are disposable. Define atomicity, crash recovery, storage ownership, and rollback refusal. |
| Discovery and transport | Advertise/listen before membership. Candidates are bounded, expiring, and untrusted. Challenge and authenticated endpoint verification bind keys. Signed governance alone admits. One node-owned TLS 1.3 transport carries replication, capacity, jobs, files, calls, and reverse channels; advertise actual endpoints. Separate readiness, discovery, membership, authenticated connection, and execution state. |
| Approval | Public request/inspect/cancel/watch operations cannot assert person approval. Protected helper holds per-pool admin keys outside node/clients and narrow installation privileges only. Genesis binds admitted authority root, admin key, first DeviceId, charter, assurance. Subsequent additions require charter authority. Verify exact immutable operation bytes; bind action, pool/anchor, target, signer, governance context, nonce, expiry, and authority generation. Revalidate and consume atomically. |
| Authority continuity | Verify descriptors against explicitly admitted roots. No strength from labels, self-signing, or dialog success. Preserve thresholds, holds, cancellation, distrust, durable fatigue controls, session/disconnect checks, rollback observations, and reinstall root continuity. Protected presentation and fresh owner authentication exist on every desktop OS. Phones are optional; no typed-confirmation bypass or desktop-as-phone key. |
| Jobs and resources | One node admission mechanism owns CPU, memory, accelerators, models, and worker leases. Durable request IDs, retry/attach semantics, cancellation, resumable streams, receipts, provenance, and result publication are explicit. Membership generations cover queued/running work, direct/reverse channels, streams, capabilities, and publication. Removal closes channels, cancels work, releases leases, and survives stale state/restart. |
| Workers | Same bounded request/result and private-pipe contract on each OS, including minimal directional handles, cancellation, native engine, parsers, provenance, and startup receipts. No node/authority control handles, product listeners, network authority, or independent scheduler. |
| Lifecycle | One dedicated unprivileged service identity, protected state, and endpoint for every startup mode. Boot starts before login when storage is available; login triggers the same service; neither starts only on app/API connection. Startup defaults to neither. Report requested/effective OS configuration; cancellation preserves previous effective state. No owner password storage or dependency on an unlocked interactive keychain; approvals wait for authenticated owner presence. |
| Settings and setup | One schema, validators, and operations power both. Walk relevant Settings sections during initial setup with plain explanations and optional advanced choices. Every editable choice stays reachable in Settings; governance edits invoke governed operations. Theme offers Dark and Light, defaults to Dark, respects saved selection on the first frame, and persists identically on all OSes. |
| Runtime and model progress | Setup identifies each engine, model, and optional asset; publishes lookup, transfer, verification, extraction, device check, and ready states. Show asset/version, source, transferred/total bytes, determinate bar where known, and honest unknown-total status. Preserve actionable failure, retry, cancellation, and resume semantics. Ready requires the selected real workload to run through node admission. |
| Model selection | Before download/load show file/download size, weight memory, context length, KV-cache estimate, other known runtime overhead, available memory, and estimated fit. Recalculate on context change. Label estimates and unknowns; use effective model/runtime metadata, not invented figures. |
| Chats | Messaging an agent opens its conversation and automatically schedules required model/resources. Keep model loading controls in Models. Separate workspace sessions from model assets. The chat recipient chooser uses understandable purpose/capability and availability, with a sensible default; do not require choosing opaque session/model identifiers. Preserve advanced inspection without making it a normal messaging step. |
| Packaging | Each native artifact contains matching node, public clients, workers, bundled UI, and verified runtime manifest. Define supported versions/digests, source policy, repair, installation transaction, cancellation, and rollback. No competing default implementation or legacy authority migration. |

For each contract, land canonical source location, concrete types/operations, vectors, authorization/refusal tests,
platform adapter signatures, generated-output drift check, and affected selectors. Proposed operation names in a plan
are not independently implemented APIs. Record the exact contract commit in both platform evidence records.

## Deconfliction and integration

| Owner | Exclusive implementation area |
|---|---|
| Windows/Linux integration session | Shared contracts/generator/SDKs; common Rust domains and transport; common Tauri/web shell; Python orchestration removal; Windows and Linux adapters; common packaging/CI/test selectors; these two plans' shared contract section. |
| macOS session | macOS-only authority/presentation/helper transport; launchd/ServiceManagement/service identity and storage; Seatbelt/handle confinement; macOS installer/signing/build integration; actual Mac inventory and historical recovery; macOS proof records. |

Claim precise areas and independent sibling worktrees before edits. Mixed platform files, common manifests, Cargo
lockfiles, shared SDK/schema files, and common Tauri files belong to the integration owner. macOS supplies a focused
patch or review request for those; never have both sessions edit them concurrently. macOS can create isolated
platform modules under the capability-specific dormant implementation rule below.

At every checkpoint both sessions fetch/merge origin/0.3dev and read inbox. Each author runs affected checks and pushes
its own completed batch to 0.3dev; no shared mutable branch, force push, or owner publication chore. A contract change
is first proposed with affected operations, security implications, both platform effects, and test changes. Reconcile
it in the common contract, regenerate once, then refresh adapters. No local wire fork or relaxed platform check.

Use four commit barriers: H = installed hook proof; K = reviewed common contracts and preserved-work ledger;
P = reviewed native prerequisite proofs for the relevant platform; I = installed cross-device acceptance.
Shared contract/schema/generator definition, building and independent review may proceed while H proof is pending;
K can be accepted from its required evidence before H.

A dormant capability-specific platform module may be implemented and tested once its exact canonical interface and
complete transitive dependencies/profiles have independent attack/design and platform-feasibility review, and the
coordinator has integrated and pushed that source to shared `0.3dev`. Record the exact source SHA, capability and
dependency scope, unavailable paths and activation preconditions; communicate and deconflict the scope through these
plans. Active shared-file ownership conflicts still require reconciliation, not a new remote or person approval step.
Unrelated unresolved profiles do not block this scoped work. Missing required dependencies refuse; no placeholder
proof, fake issuer, stubbed verified handle, alternate wire or runtime fallback may stand in. This permission adds
no public/production entrypoint or installed activation and accepts no part of H, K or P. Activation requires its
relevant protection evidence and separate review; full orchestration/cutover still requires global K.

Engineering needed to prove H is limited to independently attack-reviewed protected producer/measured launch,
immutable actor/root/direct-parent context, native caller/private node admission and single-consumer completion
components. Coordinate this scope through the reviewed canonical dependency checkpoint and dormant implementation
rule above. This allowance establishes no accepted barrier and admits no broader
orchestration or computation slice before its prerequisites.

Full orchestration port/cutover requires H, global K and the relevant P; scoped dormant engineering does not satisfy them.
Platform proofs may proceed independently after their relevant dependency and protection prerequisites;
H caller admission is distinct from the bounded computation/private-worker slice proved for P. No shipped cutover is
complete before all required P and I evidence exists. Record each barrier SHA and actual installed proof artifact,
never infer acceptance from branch names, a contract build or a prerequisite component.

## Preserve outstanding work before reconciliation

Record original full SHAs, ancestry, patch equivalence, tracked/untracked edits, ownership, and non-rebuildable ignored
state before mutation. Verify external bundles and artifact copies; never delete a tree based on ancestry alone.

| Work | Disposition |
|---|---|
| refactor/one-pool | Head belongs to development; inspect dirty state/artifacts. Name proves no architectural unification. |
| fix/windows-launch-ticket | The original five-modified/two-untracked inventory was subsequently committed in `05859e65c` with `wsl_command.py`; all eight final blobs match the preserved mirror ([reconciliation](notes/native-preserved-work.md#windows-launch-inventory-reconciliation)). Replace WSL ticket/window design with authenticated node IPC; port useful hostile-endpoint regressions. Historical intermediate/ignored state is not asserted preserved. |
| fix/agent-resume | Preserve two unique original commits' saved-worker identity and execution-limit behavior in public jobs/agents. |
| fix/fleet-bench-process-group | Compare two original commits to current code; preserve remaining process ownership, cleanup, and native-path tests. |
| Test-farm tree a028… | Preserve three unique commits' public selector ownership/planning; replace Python discovery dependencies. |
| fix/deploy-without-bootstrap-deadlock | Preserve reviewed installer/runtime/bootstrap independence; supersede daemon switching/session-dependent deployment. Do not wholesale merge authority removals. |
| Tree a6d… | Preserve edit-ledger edits and break-it note separately from unrelated unfinished work. |
| Other local trees | Confirm committed ancestry, live ownership, dirty state, ignored assets, and retained artifacts before cleanup. |
| Mac branches/bundles | macOS owner inspects originals on the actual device; preserve the paused remote model branch. Historical paths prove no current access. |

Merge useful original histories where appropriate. For superseded designs, verify external history preservation and
map every retained behavior to replacement commit/test. Never merge obsolete snapshots merely to satisfy ancestry.
No legacy trust migration: archive old state externally, establish fresh authority through genuine approval, and
preserve jobs/checkpoints/assets/attribution without granting old trust or auto-executing archived work.

## Development publication

Each device uses the repository's existing configured Git authentication to push its tested increments to `0.3dev`.
A GitHub App or a holder on another device is not required. Keep agent authorship, fetch and reconcile before each
push, and retain the maintained development checks. Never extract or copy credentials, create a duplicate App to
unblock development, or call a local commit published. App recovery is separate from development synchronization.

## Windows and Linux implementation batches

After H/K and independent review, build the smallest production bounded computation admission/private-worker slice
needed for P. This is distinct from the restricted H caller-admission components above and from the later
orchestration port; Python broker authority cannot stand in for node admission. Prove authenticated service IPC,
protected storage, transaction-bound approval, confined execution and before-login operation first. Windows uses SCM, actual SID/PID/lineage checks, AppContainer plus Job Objects, minimal directional
handles, no breakaway, and kill-on-job-close. Workers inherit no node/authority handles or LAN/localhost exemption.
Prove native CUDA on the available RTX 3090 Ti while WSL is stopped and invocation denied. AppContainer documentation
alone is not GPU compatibility proof. A failed proof requires native backend/confinement engineering.
Linux uses systemd, authenticated UID/service identity, protected owner approval/presentation, and Bubblewrap.
Drive the native Linux app as well as the independent WSL installation. Shared physical GPU phases run serially.

Then implement candidate discovery, canonical identity, production signing integration, authoritative membership,
resumable subscriptions, and removal propagation. Correct beacon reporting and the invalid pool-length expression
identified in the original review. Secure admission is default; open admission requires explicit charter acceptance.
Delete lexicographic automatic pool adoption. Setup uses discovered devices and native approvals, not typed network
addresses, names, ports, or terminal commands. Cross-network access requires an explicit network provider.

Move settings, durable jobs, capacity, residency, downloads, conversations, projects, and all retained feature domains
into the node. Keep pure algorithms as libraries/bounded workers. Bundle Tauri assets and consume public SDKs from
window/CLI. Preserve supported model behavior and all security invariants. Complete replacement and callers before
retiring old paths; incomplete replacements remain outside the installed default.

Delete Python pooling/discovery/shared-secret membership/roster/TLS identity; LAN work authorization and duplicate
remote tests; app HTTP daemon/headless sidecar/control/launch tickets; Windows-to-WSL runtime/path/network/UI/startup
bridges; independent Python node supervision; obsolete schemas, aliases, compatibility readers, and repair advice.
Update packaging, callers, selectors, and equivalent public-mechanism tests in the same batches.

## Observed setup defects to resolve in the unified journey

The inspected installed Linux UI runs commit 6ffbb592ba1cb9b09119263c9e3f78bff60fb9d5 inside a Windows Edge window;
that is not native Windows acceptance. Relevant setup source matches development at inspection.
Setup invokes llama.ensure_server before model transfer, discards engine progress callbacks, and replaces setup
exceptions with generic interruption/cancellation. A reported GitHub release lookup HTTP 403 therefore occurs before
model download and is hidden by vague status. The observed job is cancelled in runtime stage with 0/0 byte counters.
Preserve actual failure and stage; use verified runtime manifests and explicit source policy rather than fragile
release enumeration. Diagnose the 403 cause; do not assume rate limiting or use personal credentials to bypass it.

The HTML shell starts with data-theme="light"; first-run code can select the OS theme before saved preferences load.
Test the reported saved-Dark first-setup journey and eliminate conflicting theme initialization. Replace visible
preparing/preparation language with the actual action, download, verification, wait reason, or error. Avoid global
word replacement of internal protocol states. Capture inspected screenshots at every transition on installed builds.

## Verification and completion

Use named scripts/test selectors chosen by affected.py, maintained broker admission, independent security review,
parent regressions and mutation probes. Extend selection to Rust, generated contracts, clients, installers and native
packaging. Never raise budgets/timeouts, weaken checks, rerun unexplained failures, or substitute model behavior.
Measure queue/build/fixture/execution/teardown separately. Reuse only when the runner validates it; execute gates and
installed-device proofs. Structural checks enforce public API use, worker boundaries, no Windows WSL invocation,
Settings/setup parity, and clients' inability to mint identity, membership, grants or resource authority.

For each installed scenario record commit, artifact/digest, OS/runtime, actual service principal, DeviceId, execution
device, contract SHA, and opened screenshot paths. Exercise first launch without commands; fresh discovery before
admission; secure founding/addition; altered/stale/replayed/wrong-device approval refusal; identical app/CLI/external
Python state; real broker-admitted bidirectional work, streaming, provenance and cancellation; removal through new,
queued/running, direct/reverse, stream and result paths; crash/relaunch/update recovery; all three startup settings
and actual reboot/login behavior without identity change or a second service. Test forged candidates and root/key
replacement, cloning, forged/below-threshold signatures, secure-to-open downgrade, fatigue, injected agents, stolen
keys, rollback, restart races, and platform crossings.

Run Windows without WSL and independent Linux/WSL; coordinate with the macOS owner for actual physical cross-device
work in both directions. Open screenshots for setup/theme/downloads, discovery, approval, connection, result,
removal/refusal, Settings, and recovery. Browser fixtures supplement installed native evidence only.
Person-only OS authorization/authentication/reboot actions are requested once through the concrete native journey;
continue other work. Missing Mac access, unperformed approvals/reboots, or failed native GPU proof remain unfinished.
Completion requires preserved outstanding histories, reviewed/pushed changes, installed hook proof, one implementation
on the installed path, all three native apps installed, and launch-to-bidirectional-work proved on actual devices.
