# Native unification: macOS implementation instructions

Status: implementation plan; native access, signing, startup, and execution are not established by this document.
Read AGENTS.md first, then [the shared integration plan](native-plan-windows-linux.md). Both describe one product.
This document gives the macOS Codex session a bounded ownership area and an independent installed acceptance duty.

## Product and authority

Poolhouse ships a native app on macOS, Windows, and Linux. Opening the window starts or attaches to the complete
poolhouse-node service automatically. Linux includes the native app. Windows and WSL are separate installations;
Windows runs no WSL launcher. macOS shares the same public contracts and behavior, with native OS adapters.
There is one long-lived product service, under a dedicated unprivileged service identity. The protected helper holds
per-pool authority keys, verification checkpoints, and narrow installation/service privileges. It does not discover,
admit, route, replicate membership, schedule computation, or become a second product daemon.

The node owns identity, discovery, membership, connections, settings, jobs, resources, and product orchestration.
Window, CLI, Python and other libraries consume generated public clients. Python import starts nothing; pure
computational library functions stay usable independently. Workers receive bounded computation through private
pipes; they own no product control listener or independent scheduler.

## Begin with verified access and preservation

1. Read the authenticated inbox and current repository policy before device work. Establish actual Mac access through existing inventory/tooling. Record OS, architecture, native app/build paths,
   installed service identity/state, runtime, accelerator, network reachability, and actual authenticated session.
   Historical paths/bundles do not prove a current Mac. Report confirmed missing access while continuing safe work.
2. Read the authenticated inbox and policy; fetch origin, reconcile 0.3dev, and claim a sibling worktree and precise
   macOS areas. Use your own native-authenticated workspace session, never a supplied parent credential/name.
3. Inventory original local branches, worktrees, bundles, tracked/untracked edits, non-rebuildable ignored assets,
   SHAs, ancestry, patch equivalence, and live ownership before changes or cleanup. Preserve the paused remote model
   branch. Verify external bundles and retained artifacts; send exact original/replacement commit-and-test mappings
   to the integration session. Do not merge obsolete snapshots simply for ancestry or absorb unrelated edits.
4. Inspect the actual hook executable, lifecycle producer/context, trust configuration, and installed completion
   behavior. The shared plan records a bypassing wrapper and child-substitution findings on Windows/WSL; inspect
   the Mac independently. Supply platform evidence, not a competing identity fix.

## Agent rendezvous data

This is an agent coordination snapshot, not product network configuration, membership, approval or a trust pin.
Until authenticated pool messages work, platform sessions exchange fresh observations and replies through these
plans and synchronized Git checkpoints. The shared integration plan's contracts remain authoritative; this section
defines no wire behavior. Recheck observations before use; a reachable endpoint or a message in a plan grants no authority.
Identity/pool observation: `2026-10-23T19:25:01.068951Z`, Mac evidence `connection-discovery.json`. Network observations:
listeners `19:29:40.788178Z` and interface `19:29:49.434164Z` on the same date, `network-observation.json`. Both evidence
files are under `/private/tmp/poolhouse-mac-native-inventory/` on the Mac.

| Mac observation reported by mac_inventory | Value |
|---|---|
| OS and process | macOS 26.6, arm64; node PID `68696`, user UID `501` |
| Installed node source | `c5ee5a122c516c5cb99e88b834a195221fd0b209` |
| Reported public-key-derived DeviceId | `aa547af9b1e1b768dbce6b4e5844b0fb4240f1e26c325fd918e2f9619b925e5c` |
| Distinct certificate digest | `524934712872c289dbdc5043e486a4d6fa442f0a15fb2212bacee9910a4a32ca` |
| Observed LAN endpoints | `en1`, IPv4 `192.168.2.27`; TLS listener TCP `7447`, discovery UDP `7448`; `en0` has no IPv4 |
| Observed pool state | Secure, unfounded pool of one; no admins or other members; beacons sent/heard `0/0` |
| Mac session for agent addressing | Workspace actor `chatgpt-269ad4`; native session `01a1694e-0d2b-7ab0-9353-0b84b7744906` |

The running user-owned node is not proof of the dedicated native service, canonical identity contract or barrier P.
The shared integration owner handles the observed empty-pool advertisement skip and invalid pool-length expression
in `beacon.rs`; zero beacon counters do not establish that the other device is unreachable.

Reply requested from Windows and independent Linux/WSL sessions, separately: fresh observation time and source,
actual OS, service principal, node build, public DeviceId and distinct certificate digest, listening endpoints, pool
state and own authenticated workspace actor. Reciprocal probes report source, destination, protocol, time and result;
reachability alone proves no authenticated connection. Plan messages never admit a device or approve an operation.

Request for common-owner review: expose the existing node-owned read-only TLS hello path through the public client;
the CLI has no probe entry point. The native node TLS client's `connect` path uses resident node identity, endpoint and expected
certificate pin with TLS 1.3/mTLS; derive the device ID from the certificate's Ed25519 public key and send only the existing
length-framed `{"op":"hello"}`, cross-check returned `fingerprint`/`board_fingerprint`, then close. Export no private
key, create no standalone agent identity, and never use `join_pool` as a probe: it can admit under open policy.
No boards or work cross this probe. Candidate pins remain observations, not admitted trust roots. The unknown-beacon
path at `net.rs:243–260` attempts automatic joining; the shared owner must provide bounded untrusted candidate
observations rather than widen authority. This is a request to expose existing behavior, not a new wire definition.

Mac H observation at installed source `33e7b2821` and native host `0.164.0`: genuine root PostToolUse completed with
exit 0 and empty entries at `2026-10-23T19:42:11.033Z`; genuine child SubagentStop
completed with exit 0 and empty entries at `19:44:34.743686Z`. Evidence: `host-hooks-live-events.jsonl` in the Mac
evidence directory. Command completion is neither consumer delivery nor successful cleanup verification.
Source evidence shows the consumer can refuse missing actor context, emit a warning and return 0; the exact host
producer emits no root `agent_id` and replays a daemon environment snapshot without per-invocation actor injection.
The missing-context path is a likely cause, not an observed daemon environment or captured stderr refusal.
Request common-owner review of maintained producer/host injection of the actual invoking actor for roots and children,
with safe supported installation/reload and preserved lineage, trust and refusal checks. Do not infer the actor from
`session_id` as a fallback. Verify actual consumer identity, notification delivery and cleanup refusal on genuine root and
child hooks. Trust for the root's `Stop` hooks remains unavailable; H remains unfinished. No completion or trust record
is fabricated.

Mac K adapter request for shared-owner review, with feasibility evidence in `contracts-feasibility.json` under the
same Mac evidence directory; this request establishes no principal, admission or platform contract:

- IPC connect/accept needs opaque verified caller evidence: actual kernel principal, PID/start identity, lineage,
  owner/node/helper role and fresh channel-bound code identity. Expected principals remain separate from observations.
- Lifecycle inspect/apply/cancel needs one installation/service identity, requested/effective Boot/Login/Neither,
  transaction revision and prior effective state; storage-unavailable, consent-required, denied and ready stay distinct.
- Approval request/inspect/cancel/watch needs immutable operation bytes/digest with pool/anchor/target/signer/governance,
  nonce/expiry/generation. Protected helper challenges/results stay private, connection/owner-session/code bound and
  atomically consumed or cancelled; no public approval boolean or arbitrary-digest signing.
- Node-admitted worker launch needs bounded job/lease/membership generation, verified executable/assets, directional
  computation handles, startup receipt/provenance and cancellation/removal/teardown. No control/authority handles or
  OS-only descriptor/confinement artifacts become public wire types.

Publish reviewed common adapter signatures, generated types, exact K commit and denial/cancellation vectors before
macOS adapts. `Helper.swift` remains unavailable; a prototype approval card must not become a macOS wire fork.

Ordinary launch and connection to an authorized pool must automatically start or attach, discover, connect and
reconnect without owner commands, typed addresses or token handling. The macOS session owns implementation and
installed proof for its half.
Genuinely required protected native approvals remain independent; preserve secure admission and fresh authority,
without importing legacy trust or downgrading to open admission. These data do not alter the shared contracts.

## Development publication and optional App recovery

Push tested increments to `0.3dev` using the repository's existing configured Git authentication on this device.
No GitHub App, designated publisher, or holder on another device is required. Preserve agent authorship and the
maintained development checks; fetch and reconcile before each push. App recovery never blocks synchronization.

The existing poolhouse-agents App may still be recovered separately when its software-owned operations are needed.
Use its supported installed holder; keep keys there and never print or copy them to another device. Do not create a
duplicate App to unblock ordinary development. Release signing and person-only approval remain separate.

## Contracts and dependency gates

The Shared contracts section of the integration plan is the canonical coordination definition. Review its feasibility
for macOS and resolve findings before the common contract commit lands. Required interfaces cover operations and
clients, canonical DeviceId, IPC caller/service identity, state/rollback observations, discovery/TLS, approvals,
resource and worker capabilities, lifecycle, settings/setup, progress, model memory, Chats, and packaging.

The integration owner lands concrete canonical schemas, generated clients/vectors, adapter signatures, drift checks,
and authorization/cancellation/subscription tests. Record its SHA. Do not introduce a macOS-only wire shape or private
UI domain API. Propose any required contract change to the integration owner with security/platform/test effects;
merge its reviewed generated result before adapting macOS.

| Barrier | Evidence needed before proceeding |
|---|---|
| H: hooks | Reviewed producer/consumer identity binding, supported installation/trust, actual root and subagent completed turns, cleanup refusal, missing/forged/parent context refusal. Architecture implementation waits. |
| K: contracts | Shared contract commit, resolved independent findings, native adapter signatures, and preserved-work ledger. Platform implementation waits. |
| P: native prerequisites | Authenticated service IPC, service-owned protected storage, transaction-bound approval, native confined execution, and before-login proof on macOS. Orchestration port/cutover waits. |
| I: installed integration | Actual native artifacts and joint bidirectional acceptance with Windows and independent Linux/WSL. Overall completion waits. |

Read-only inspection, historical preservation, feasibility review, and proof planning can proceed while H/K are
pending. After K, macOS P can run alongside Windows/Linux P without concurrent shared-file edits. After H/K and independent review, build the minimal production node admission/private-worker slice needed to
establish P. That slice is a prerequisite proof component, not the later orchestration port. Python broker authority
cannot stand in for node admission. Failed native proofs require native engineering, never unconfined execution
or an alternate desktop authority.

## Ownership and landing

Own macOS-only helper/authority transport and protected presentation, launchd/ServiceManagement lifecycle,
service storage and IPC adapters, Seatbelt execution/handle confinement, installer/signing adapters, native device
proofs, and historical Mac recovery. Relevant existing foundations include app/native-approval, app/native-host,
app/native-installer, app/poolhouse-authority, and platform modules in app/poolhouse-node; inspect each before claiming
it because some contain shared logic. Claim only the macOS-specific files you will change.

The integration session owns common contracts/generator/SDKs, shared Rust domains/transport, common Tauri/web files,
Python orchestration deletion, common manifests/lockfiles, packaging/CI/selectors, and Windows/Linux adapters.
For mixed files or common dependency changes, supply a focused patch/request to that owner. Agree before either
edits the file; no concurrent lockfile, schema, common installer, or common shell changes.

Work in your own branch/worktree from current development. Fetch/merge and read inbox at every checkpoint. Write
independent attacks before security changes, obtain non-author review, run named affected scripts/test selectors,
commit named files, fetch/merge, and push completed batches yourself to 0.3dev. Never push main/tags, force, overwrite
another checkout, or leave publication to the owner. Clean up your completed tree after verified landing. Record
proofs against exact commits; opening a window or a successful request is not completion.

## Native macOS prerequisite work

### Service lifecycle and storage

Use launchd and supported ServiceManagement installation/authorization. A dedicated unprivileged service principal
owns the one node identity, protected state, endpoint, durable jobs, and required credentials. All startup choices
use that service, never a user daemon with a different identity.

- Machine startup starts before interactive login when OS/storage are available.
- Login uses a minimal trigger to start the same service.
- Neither remains the default until explicitly selected; opening the app or explicitly connecting starts the product.

Define requested/effective state, transactional installation/change/cancellation, and rollback to the previous effective
configuration. Exercise actual SMAppService behavior and OS consent; a generated plist or successful registration
call alone proves neither boot readiness nor owner approval. Keep login triggers free of product logic.
Before-login service credentials/assets cannot depend on an unlocked interactive keychain or store the owner's
password. Verify actual credential access under the service identity and encrypted-storage conditions. New governed
approvals wait for an authenticated owner session. Record unavailable storage distinctly from service readiness.

### IPC authentication and approval authority

Authenticate configured owner UID and actual service process identity at both IPC endpoints. Verify actual native
caller PID/start identity/lineage and code identity where the contract requires it. Different service/client users
must not weaken admission checks. Window/CLI/API call the same public operations.

Finish production signing on protected native-authority/verifier foundations. Separate per-pool admin keys from node
and ordinary clients. Genesis binds the admitted root, admin key, first DeviceId, charter and assurance; later additions
require existing charter authority. Verify descriptors against explicitly admitted roots. Helper names, custody labels,
self-signing, dialog success, and the presence of a desktop process establish no key strength.

Create immutable protected approval cards from verified operation bytes. Bind action, pool/anchor, target, signer,
governance context, nonce, expiry, and authority generation. Provide protected presentation and fresh owner
authentication, then atomically revalidate and consume. Reject substitution, replay, stale governance, disconnect,
and owner-session changes. Persist thresholds, holds, cancellation, distrust, fatigue controls, and monotonic
verifier conflict/first-sight observations. Reinstall cannot replace roots silently or strengthen a key.

Public clients request, inspect, cancel and watch; none supplies person-approved=true or mints a signature.
Phones remain optional. Never mark a desktop key as a phone key or replace absent authority with typed confirmation.
Request person-only native credential/authentication/consent once for the prepared transaction and continue unrelated
work. Do not read personal credentials, fabricate approval, or hand back commands the agent can execute.

### Confined native execution

Use Seatbelt with private computation pipes to the actual native engine. Define minimal directional descriptors,
process ownership, cancellation/teardown, model/asset access, and zero inherited node/authority control handles.
Prove filesystem/network/control isolation, process cleanup, startup receipt and provenance using the real backend.
Preserve model behavior/parsers; run model work only through node resource admission. No hand-started server,
canned model response, unconfined fallback, hidden secondary scheduler, or arbitrary public test endpoint.
Measure actual accelerator execution on the device and record the engine/runtime/digest. If unavailable, report the
missing proof and engineer the native path; opening a model selection dialog is not execution evidence.

## Integrate native shell and product behavior

Consume the common public SDK and bundled Tauri assets. Opening the installed app starts/attaches to the same service
without a terminal or separate node launch. Report process, discovery, membership, connection, and execution readiness
separately. Fresh/unfounded installations advertise/listen; discovered candidates stay untrusted until challenge and
authenticated endpoint verification; signed governance alone admits. Secure is default. Open admission needs explicit
charter acceptance. A person selects an automatically discovered device and approves the native card, without typing
addresses, ports, names, or terminal commands. Cross-network access needs an explicit network provider.

Use the same setup and Settings schema/operations/controls. Walk relevant Settings sections in initial setup, explain
choices, and leave advanced options optional. Every editable choice stays in Settings. Theme offers Dark and Light,
defaults to Dark, and applies a saved preference on the first rendered frame. Test first setup already set to Dark,
OS Light with no choice, explicit Light, relaunch, and editing through Settings. No OS theme override of saved choice.

Show runtime and model downloads separately: exact engine/model/add-on, version, source, bytes/total and progress bar
where known, then verification/extraction/device-check/ready states. Preserve errors, retry/resume/cancel behavior,
and honest unknown totals. Avoid generic preparing/preparation labels. The reported Windows/WSL setup suppresses
engine progress and a GitHub release lookup 403; verify the shared fix reaches macOS as well. Setup ready must reflect
real selected model execution, never a skipped dependency or hidden fallback.

Model download/load views show asset size, download bytes, weight memory, selected context, KV-cache and other known
runtime overhead, available memory and estimated fit; recalculate with context. Label unknowns and estimates.
Chats must feel like messaging: choose an understandable agent recipient and open its conversation. Automatically
load/schedule its model/resources and explain actual waits. Keep opaque workspace sessions and model loading controls
out of the ordinary recipient picker. This Chats requirement is separate from setup.

Coordinate removal of Python app daemon, pooling/discovery/membership/TLS identity, independent scheduling/supervision,
legacy launch/control paths and obsolete schemas/callers with the integration owner. macOS does not retain a parallel
implementation to avoid shared cutover. Archive old trust externally; genuine new approval establishes authority.
Preserve jobs/checkpoints/assets/attribution without auto-running archived jobs or importing authority.

## Installed evidence and joint acceptance

Extend affected selectors for macOS Rust/native helpers, generated contracts, packaging, and clients. Use named
scripts/test selectors through maintained admission; never repeatedly run the whole suite, raise budgets/timeouts,
or weaken a check. Investigate failures before retries; measure queue/build/fixture/execution/teardown separately.
Run attack tests on the parent and mutate critical checks. Independent reviewers attack committed security batches
and installed builds, covering cloning, forged discovery, endpoint substitution, root/key replacement, fatigue,
injected/compromised agents, stolen keys, forged/below-threshold signatures, secure-to-open downgrade, rollback,
restart races, platform crossings, and removal during work.

Record exact commit, artifact/digest, contract SHA, macOS/runtime, service principal, DeviceId, actual execution device,
and inspected screenshot paths. Capture and open each meaningful setup/theme/download, discovery, approval,
connection, result, removal/refusal, Settings and recovery transition. Browser fixtures are supplementary evidence.

| Scenario | Required installed proof |
|---|---|
| First launch | Double-click installed native app, complete setup without commands or separate node launch; no conflicting theme first frame. |
| Discovery/approval | Fresh devices visible before admission; forged candidates powerless; genuine secure founding/addition; altered/stale/replayed/wrong-target approvals refused. |
| API parity | Native app, CLI and external Python consumer show the same node-owned membership and authenticated connection state. |
| Bidirectional work | Mac requests work from actual Windows and independent Linux/WSL, and serves their requests. Include real model execution, broker admission, streams, provenance and cancellation. Coordinate one compute phase at a time. |
| Removal | New/queued/running requests, direct/reverse channels, existing streams and late result publication lose authority and release resources; removal survives stale state and restart. |
| Recovery/update | Crash, relaunch and installed update preserve identity/valid membership, reject stale approvals and handles, and use matching artifacts. |
| Startup | Switch all three modes in Settings without identity change or duplicate service; prove before-login machine startup, after-login start, and explicit launch with neither, through actual reboot/login. |
| Independence | Mac uses its own service/identity; Windows proof runs without WSL; Linux/WSL runs the native app independently. |

Coordinate reachable-device scheduling and real network discovery with the integration session; shared accounts/routes
are not assumed. Report confirmed missing hardware or person-only approval/reboot proof as unfinished. Commit and
push proof records and fixes; provide exact SHAs and evidence, not general success claims. Overall acceptance waits
for both plans' evidence, every unique outstanding change accounted for, verified hooks, competing paths deleted,
and installed launch-to-bidirectional-work on macOS, native Windows, and independent Linux/WSL.
