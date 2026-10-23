# Canonical node contract proposal

`contracts/node/` defines versioned data shapes. This is a K engineering proposal.
K acceptance still requires the retained-feature operation inventory, reviewed
opaque adapter interfaces, lifecycle/refusal vectors and preserved-work ledger.
This batch implements no domain operation, platform adapter or installed proof.
The existing installed API remains unchanged.

The definition generates Rust data types, public Python data classes, TypeScript
declarations and encoding vectors. `scripts/generate_node_contract.py --check`
checks drift. Generated data is untrusted even when its issuer annotation says
node, producer, installer or kernel adapter. Authorization, signatures, admitted
OS handles, fresh person presence and resource admission remain checked code.
No verified/approved/admitted capability wrapper is generated from wire data.

| Type | Proposed meaning and encoding |
|---|---|
| DeviceId | Lowercase SHA-256 hex of UTF-8 `poolhouse/device/ed25519/v1`, one NUL byte, then the raw 32-byte Ed25519 public key. Certificate verification and key possession are separate. |
| CertificateDigest | Lowercase SHA-256 hex of the exact DER certificate; certificate renewal does not change DeviceId. |
| InstallationId | Canonical lowercase UUID, distinct from device identity and pool membership. |
| PoolId | Full genesis-charter anchor digest as lowercase hex; canonical charter/signature vectors remain part of the governance contract work. |
| RequestId / JobId | Distinct UUID data types for request idempotency and node-owned jobs. |
| U32 / U64 | Canonical nonnegative decimal strings without leading zeroes, within the named unsigned width. JSON numbers are refused. |
| NativeThreadId | Private canonical UUID data from the native producer, scoped by its admitted installation and generation. |
| SessionLease / ReceiptId | Private node-issued opaque values; diagnostic formatting redacts them. Their shape conveys no authority. |

Device and certificate identifiers have distinct nominal types in all three
languages. The proposed DeviceId domain differs from historical raw-key hashes;
PoolId uses the full anchor rather than the installed truncated form. These are
explicit integration changes to review before K acceptance. Historical observations
are not rewritten as trust, and no existing authority is automatically imported.

Private process observations preserve platform semantics: Linux uses PID, boot
UUID, start ticks and explicit real/effective/saved/filesystem credentials; Windows
uses PID, creation FILETIME in 100 ns units and binary SID; macOS uses PID,
pid-version, audit token and birth time in Unix microseconds. Binary SID encoding
is lowercase hex of revision-1 SID bytes with the exact subauthority count/length.
Audit tokens encode their eight 32-bit words in little-endian order. These records
describe observations; only native adapters establish their provenance.

Missing/unknown record fields, unknown union tags, dangling references, duplicate
definitions, unsupported versions and private types reachable through public
records refuse. TypeScript declarations provide nominal compile-time checking;
runtime response decoding is still a client integration requirement. Neither a
well-formed NodeIdentity nor its decoded Python class authenticates a node.

Operations affected by integration are identity/status responses, discovery and
endpoint verification, governance targets, jobs, and native caller/session binding.
No generated operation table is accepted yet. The required inventory covers every
delegated legacy registry and caller; privileged legacy registration/rebind
operations stay visible as restricted surfaces until replacements and callers
are complete.

macOS review is requested for raw public-key extraction/domain separation,
audit-token word order and pid-version/birth semantics, and the planned opaque
native adapter boundary. Windows review includes binary SID and FILETIME handling.
Platform implementation waits for K. Linux launcher preparation history is
preserved separately while these contracts are settled.

The proposal checkpoint is `a7c7977a7`; its schema digest is
`d472c6141f2d81c22de630ab5184984ce8e444000ba033d95ca5d7510902fb79`.
Focused Python/Rust checks and strict TypeScript checking pass. Removing private
closure checks, Rust unknown-field refusal or TypeScript nominal separation each
fails its probe; the restored artifacts pass and match the generator.

Mac preservation evidence is also required for K: exact original full SHAs,
verified bundle manifests, paused remote-model history, tracked/untracked edits,
non-rebuildable ignored metadata and live ownership. The Windows launch-ticket
row's five modified/two untracked files remain unlocated in the local preservation
ledger. Replies and platform feasibility findings travel through Git checkpoints
until authenticated pool messaging works; no observation or Git label grants trust.

The [Mac representation review](native-plan-macos.md#mac-review-of-the-k-representation-proposal) records the remaining
encoding and verified-adapter requirements; it accepts raw representation only, not K. The published
[Mac preservation ledger](notes/native-macos-preserved-work.md) separates retained custody from current physical state.
Please return the reviewed canonical adapter/operation checkpoint and fresh Windows and Linux/WSL actor/endpoint replies
through the [rendezvous request](native-plan-macos.md#agent-rendezvous-data). These replies remain observations, not authority.

The next proposal adds `lifecycle.toml` and `adapters.toml` to the canonical
schema digest. Generated Rust transition metadata records terminal states,
atomic completion/cancellation, generation invalidation, and exact process
binding requirements. Metadata does not implement or authorize a transition.
The generated `NativeCallerPlatform` trait uses opaque associated runtime handles;
release consumes a held process and borrows the node-owned binding. These handles
are neither generated wire records nor proof accepted from a client. Platform
implementations, protected construction, drop cleanup, and installed hostile
caller evidence remain required. The earlier checkpoint digest above identifies
that historical type-only proposal, rather than the extended schema.

Independent review of `457235724` found no blocker to publishing these incomplete
declarations. Before K acceptance, `hold_spawn` needs an opaque node-owned spawn
plan binding the invocation, command and approved executable/assets; its current
producer/bootstrap arguments are insufficient for implementation. The lifecycle
loader checks declared terminal states and reachability, but does not enforce
every permitted edge against a fixed semantic transition set. Runtime refusal
tests and stronger semantic validation remain required; passing structural
checks proves neither admission nor single-consumer execution.
