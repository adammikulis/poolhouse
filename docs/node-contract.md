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

The follow-up closes those two declaration gaps: `NativeCallerPlatform` now
borrows an opaque node-owned `SpawnPlan` when holding a process. The plan must
bind the invocation/generation and retain measured executable/assets, immutable
arguments, environment, directory and confinement, with explicit handle
ownership/inheritance. Release must match that same plan and generation. The
lifecycle v1 loader now refuses changed initial/terminal states, missing or extra
transitions, shortcut consumption and altered required checks. These semantics
require an explicit contract-version change to revise. Independent attack review
approved this scope; protected constructors and actual atomic enforcement remain
implementation and installed-proof obligations.


## Jobs operation proposal

`operations.toml` now declares `jobs.get` and `jobs.cancel`. The generator emits
Rust associated request/response/error types, a Python type catalogue and a
TypeScript operation map. These are declaration proposals, with no dispatch or
runtime authorization. Other retained jobs/agent operations remain uncontracted;
the full inventory and K acceptance remain open.

Both requests bind a distinct JobId and ResourceGeneration. Cancellation also
requires RequestId. The node must bind a durable retry to the authenticated
principal, pool/installation, operation, job generation and canonical payload
digest. Conflicting reuse refuses; identical retries recheck current permission
without repeating effects. UUIDs, labels and public request fields grant no
ownership, membership or execution authority.

JobView projects only job ID, generation, revision, project ID, display name,
operation, explicit state, return code, timestamps and cancellation state. No
working directory, environment, argv, PID or internal execution handle appears.
Job ownership and delegated control remain private checked state. Read and cancel
permissions are separate; project visibility alone cannot cancel work. Refusal
must authorize scope before revealing whether another principal's job exists.

Required optional keys encode absence as JSON null. Boolean fields accept only
JSON booleans. ExitCode is a canonical signed i32 decimal string, refusing `+1`,
`-0`, leading zeroes and overflow. UnixMicros is the nonnegative number of UTC
microseconds since the Unix epoch, encoded as a U64 decimal string. Display text
bounds count UTF-8 bytes and refuse Unicode control characters and malformed
Unicode; display names allow zero through 256 bytes. ResourceGeneration and
ProjectId are distinct UUID data types; node state owns their scope and lifetime.

`requested` records accepted cancellation intent. `settled` requires that intent,
verified process termination and verified capacity release. Completion can win
an atomic race without changing its final result to cancelled. Cancellation must
atomically prevent a pending launch; uncertain ownership enters recovery and
cannot release capacity optimistically. Disconnect/detach cancels observation,
never durable execution. The proposed interrupted/recovery_required states are
not terminal merely because they differ from queued/running. Actual state
transitions, owner/delegate storage, durable retry transactions and refusal tests
remain required before implementation/cutover; this declaration adds none.


## Job pagination and output proposal

`jobs.list` returns a bounded page of at most 100 JobViews for an authorized
project. JobPageLimit accepts decimal strings from 1 through 100. The first page
freezes a node-owned snapshot ordered by submitted time then JobId; subsequent
pages use that same snapshot, project and filters, avoiding concurrent insert
skips/duplicates. The response names ListSnapshotId and its UTC snapshot time.
Snapshot expiry or an invalidated authorization generation returns cursor_expired;
the client explicitly requests a new first page. Each page and each job are
reauthorized before delivery. Snapshot data is observation, never current control
authority; get/cancel check live state and exact resource generation.

`jobs.output.read` takes explicit beginning, tail or resume position and a
JobOutputByteLimit from 1 through 65536. It returns at most 65536 decoded bytes as
lowercase hexadecimal, a distinct JobOutputCursor, and OutputGeneration. Cursor
positions count stored redacted-stream bytes. Redaction happens before storage
and chunking, including secret matches spanning input boundaries. A changed
redaction projection changes OutputGeneration and expires old cursors. Clients
never compute cursor advancement. Log bytes and metric rows have separate cursor
domains; metrics remain a future operation rather than an invented shared index.

`exhausted` means that chunk reaches the current end; a running producer may append
later. `sealed` means that generation can no longer append. A sealed stream can
still have unread bytes. Tail starts at at most the requested bytes before the
current end; beginning starts at zero; resume uses the issued cursor. Byte chunks
can split UTF-8 sequences, so clients decode incrementally. Rendering must treat
output as untrusted text, never markup or terminal control instructions.

Both cursor types are opaque node-issued data, not credentials. They bind caller,
authorized scope, resource/snapshot and generation, projection, ordering, position
and expiry in checked node state. Reauthorize use and delivery; expiry refuses
without silent restart or unauthorized existence disclosure. Frame limits must
bound incoming allocations before decoding lists/hex. These declarations provide
wire refusal checks only; storage, redaction, pagination and authorization are
not implemented or proven by the generated codecs.


The shared declaration checkpoint `986cab4b3` (integration `c26f0f375`) includes
job inspection, cancellation, pagination and output. `vectors.json` now supplies
common accepted/refused job scalar cases to both Rust and Python, including
numeric bounds, signed zero, JSON boolean/number confusion, malformed hex and
opaque byte data. This is a representation checkpoint for platform review, not
K acceptance or an installed operation. The other retained job/agent operations,
state/storage, governed approval and worker contracts remain incomplete.


## Agent control proposal

`agents.get`, `agents.pause`, `agents.resume` and `agents.stop` use nominal AgentId,
ProjectId, current project/worker generations and closed records. Mutations carry
RequestId; durable retries bind the operation, principal, pool/installation,
project/worker generations and payload. A label or same-name worker is never a
replacement generation's authority. These contracts cover control observations,
not the complete models/preferences/backlog catalogue required by agents.describe.

Pause acknowledgment names the request and revision. Pause intent is distinct
from observed state: a working task may finish while pause_requested is true.
The node must atomically exclude new admission after pause acknowledgment. Resume
rechecks current authority; delayed older pause/resume requests cannot reorder a
newer control revision. Pause/resume/stop requests include expected_control_revision;
that counter changes only on accepted control mutations, independently of progress
snapshot revisions. New requests compare-and-swap it atomically, returning
control_revision_conflict without effects on mismatch. Already-recorded identical
retries reauthorize and return their recorded outcome before reapplying any CAS;
a new stale request cannot advance or overwrite the newer intent. Clients refresh
conflicting observations and never silently force an older intent through.
Running is true only for a verified live execution of the
same incarnation, false only for verified absence, and null when unknown. Steps,
tasks, ignored counts and memory bytes also use explicit null for unavailable
observations. No zero or readiness is invented. Snapshots contain no PID, lease,
credential or person assertion.

Stop records durable intent. Settled requires verified termination and release of
all owned capacity; unknown ownership or failed verification requires recovery.
Lease release and forced-stop observations can be null. Warnings are bounded safe
messages, never arbitrary exception strings. Disconnect cancels observation only,
not accepted stop intent. General lifecycle authority remains a protected native
caller decision, not a public person flag or authority borrowed from project access.

`agents.task.start` and `agents.task.stop` are separate from general lifecycle
control. TaskRef binds nominal BoardId and an opaque TaskId preserved exactly;
formatting alone proves no task exists. Task IDs are never silently normalized.
Both operations bind task, worker and project generations. Task stop additionally
names TaskExecutionId and its generation and may stop only that task-owned
execution; it cannot stop a replacement worker or grant general agent-stop rights.

The node rechecks the actual active task, creator/assignee relation, separate
caller/worker sessions, same-board owned checkout, worker orders relation and no
conflicting active task at admission and execution. Reassignment, closure, source
change or revoked orders invalidates pending execution. No parent exception is
inferred. Task start preserves the saved model and execution limits; none can be
reconfigured through this request. Resource admission and current membership are
still required. Pause prevents queued-task admission until resumed.

These are generated data declarations only. Board anchor/catalogue identity,
protected controller authority, transaction ordering, state transitions, native
process proofs and resource release remain implementation obligations. Existing
signed task/board records are preserved without importing legacy authority.

The [fresh Linux/WSL rendezvous observation](notes/native-linux-rendezvous.md)
records distinct public-key/certificate hashes, local API/socket readiness, the
actual workspace response and exact reviewed caller/agent contract checkpoints.
It supplies no endpoint trust or native Windows evidence and accepts no barrier.


## Native representation review follow-up

AuditToken now names `audit-token-le32` in the canonical source. Its wire data
remains 32 bytes as lowercase hex: eight unsigned words serialized little-endian,
including high bits. Generated Rust conversion round-trips words without making
PID, code, principal or freshness claims. The shared non-palindromic vector
contains high-bit words; only a native adapter can bind a received token to its
actual ingress message and establish cross-field consistency.

UnixMicros names its `unix-microseconds` unit in the canonical source. Rust,
Python and TypeScript provide checked seconds/microseconds conversion: require
0 <= microseconds < 1000000 and reject multiplication/addition overflow past
U64. Python requires exact integers and TypeScript actual bigint inputs. Vectors
cover carry, maximum, one-above overflow, negative and malformed components.
MacProcessObservation keeps the field name birth_unix_microseconds and its
existing decimal-string wire shape, now using nominal UnixMicros. Neither wall
clock birth nor conversion creates process identity or monotonic ordering.
The installed Mac observation/adapter proof remains required.

The pinned [operation mapping](../contracts/node/operation-mapping.json) connects
the ten initial root operations to hashed legacy handlers and retained callers. It
separates proposed behavior from current transport, records semantic differences
and lists remaining omissions. Metrics callers remain explicitly uncontracted;
the log OutputCursor does not cover metrics. A focused cross-artifact check keeps
request/result/error and lifecycle declarations aligned with that mapping.

## Canonical domain files

Domain declarations live in `contracts/node/domains/*.toml`, with closed fields
version, status, type and operation. Version 1 requires proposal status and explicit
arrays; at least one array is nonempty. Files are regular, lowercase ASCII-named
TOML; symlinks, nested directories and unknown entries refuse. Parsed snapshots
are read once, merged deterministically, then checked by the same global duplicate,
reference, privacy and cycle rules as the root declarations. Domain filenames and
versions enter the schema digest along with all combined definitions. An absent
or empty domains directory preserves the root-only output. These files introduce
no dispatch, native proof issuer or accepted barrier.

Lists may specify exact integer min_items and boolean unique constraints; missing
values mean zero and false. Rust and Python refuse out-of-range or duplicate
whole typed values without deduplicating or sorting. Domain-specific duplicate
identity keys still require checked node validation. Optional tagged unions use
null or their closed object encoding; nested optionals remain refused. Shared
ApprovalId and OperationId are node-issued UUID data references, never approval
or execution capabilities. Per-domain evidence pins retained handlers/callers
and explicitly separates missing runtime behavior from declarations.


## Integrated domain declaration checkpoint

The combined proposal contains 460 types and 73 operations across the root,
Settings/startup/setup, models/assets/serving/resources, and approval/governance
files. All use the same generated Rust, TypeScript and Python definitions and
schema digest `8d3b0f57c91c133db058fdd574c1f690e2f2c51ed984d82f819755a06785a7f4`.
Per-domain evidence pins retained source sites, caller mappings and remaining
omissions. The domain files were independently attack-reviewed before integration.

Wire checks cover nonempty unique weekday sets, empty approval snapshots, explicit
unknown startup observations, and exact fractional surplus/shortfall margins.
The integration check caught stale Settings authorization metadata after final
review constraints were added; its mappings now match the canonical declarations.
These are representation and contract checks, not implemented policy, native
assurance, rollback protection, atomic effects, downloads or model execution.
K remains unaccepted. Remaining domains, retained variants, genuine provenance
producers, cross-platform feasibility and installed H/P/I proofs stay required.

The Python generator now emits a package grouped by API area, with the same public
`poolhouse.api.node_contract` imports reexporting one exact class object per type.
The former flat generated file exceeded the repository's file-size gate; no budget
was raised. Shared codecs reject duplicate registration, verify public-reference
closure, and freeze their registry after all groups load. Imports define data
classes only and start no services. Type-hint resolution works across groups;
fully qualified module/pickling paths changed within this proposal and require
compatibility review before any installed SDK cutover. Generation refuses a stale
flat file or unknown/symlink package entries and never deletes unrelated files.


The native adapter v2 proposal makes the trait and its sealed node-owned Binding
and SpawnPlan parameters crate-private. Producer admission borrows the retained
VerifiedProducerLaunch; one reader receives an opaque producer message. Holding
and caller inspection explicitly bind the exact node Binding and retained running
process. These are runtime declarations, absent from all public SDKs.

Methods that transfer channel or process custody now return distinct owned failure
handles instead of a bare error classification. Partial spawn/release failure must
retain the actual held/running/unknown process and unresolved cleanup duty. A
borrowed receive failure retains the same shared channel owner and reader state;
it cannot move a channel out of a borrowed producer. Borrowed diagnostics retain
no authority and may return an ordinary classification without surrendering custody.

Before-consume cleanup consumes its distinct obligation. Cancelling a hold or
terminating a process consumes prior node authorization revocation plus a terminal
cleanup obligation, returning the responsible holder's scoped cleanup evidence or
an owned unresolved failure. Registry revocation does not assert that OS resources
closed. Holder cleanup cannot assert domain effect completion.

This independently reviewed increment is deliberately incomplete. The sealed
read-only projection graph, unique obligation identity/resource scope, separate
NodeEffectOwner completion interface remain required before K. No platform implementation, trusted issuer, actual resource
closure, proof bridge or installed H/P evidence follows from these declarations.


Lifecycle v2 retains the same states and edges while requiring authenticated birth
or same-producer resume, native invocation, current producer boot/admission and
producer-stream global event deduplication. The original occurrence identity must
survive retransmission: minting another message ID cannot authorize that same
native occurrence again. The producer guarantee and protected provenance remain
unimplemented; metadata cannot establish either.

Cancellation and retirement atomically revoke authorization before returning
physical-cleanup obligations. They do not assert channel closure or process
reaping. Consume issues one effect permit; permit loss or crash preserves unknown
or Pending disposition without a replacement permit or repeated effect. Scoped
cleanup and genuine effect recording still need their separate responsible owners.


`FiniteF64` represents an exact finite IEEE 754 binary64 value as 16 lowercase hex
digits of its unsigned bit pattern, most significant digit first (`1.0` is
`3ff0000000000000`). Exponent `0x7ff` always refuses, including both infinities
and every NaN payload/sign. Signed zero, subnormals and maximum finite values
round-trip unchanged. Equality and hashing describe bit identity; hex ordering
is not numeric ordering. This is intrinsic machine-value validity, not a
probability/range check or model metadata estimate.

The retained decision/calibration implementation converts its scores, temperature
and calibration knots to Python floats; latency is already rounded by its source.
Conversion helpers in Rust, Python and TypeScript preserve those resulting binary64 bits
without another rounding, clamping or integer/boolean/string coercion. Tensor
float32/bfloat16, PCM integers and other numeric formats remain distinct. Public
compatibility adapters must still apply the operation's existing domain validation;
this shared encoding does not implement inference, workers or their authorization.


`Utf8Chunk` is bounded, untrusted content data. It preserves well-formed Unicode,
including NUL, CRLF, tabs, combining sequences and non-BMP characters, without
normalization. Existing control-free text labels keep their validation. The chunk
limit remains 65536 decoded UTF8 bytes; it counts neither UTF16 code units nor
serialized JSON bytes. Lone surrogates refuse. The TypeScript factory checks the
runtime string type and counts code points without an eager whole-input copy.

Larger retained prompts need ordered chunks plus their original logical/aggregate
limits after reassembly. Encoded frame size, chunk/event count, leases and output
limits remain separate checks; empty chunks and heavily escaped controls cannot
bypass them. The decision source's existing JSON aggregate measurement must stay
source-equivalent, including its escaping and separators, rather than becoming a
raw UTF8 byte limit. No truncation or budget increase follows from this encoding.
Content validity does not sanitize HTML, commands, paths or tool arguments, grant
authority, or replace rendering escapes and logging redaction. Domain operations
and private worker framing still need their own complete declarations.


The reviewed connectivity proposal defines 28 operations for local readiness,
devices/pools, discovery candidates and manual address hints, join attempts,
logical connection interests and sync attempts. Readiness, key possession,
membership, authenticated connection, replication and execution stay distinct.
Address hints remain untrusted and require Node policy plus actual endpoint/key
verification; certificates and candidate signatures do not admit a member.
Membership and cleanup references bind the exact pool and device incarnation.

Endpoint and membership observations have bounded pages with explicit snapshot
identity, completeness and continuation reads. Their page bounds do not cap the
number of interfaces or pool memberships. Nested cursors bind the frozen parent,
principal, generation and projection; watches preserve that snapshot across pages.
Sync scope distinguishes all authorized mutual boards from a selected bounded
subset, preserving governing/build synchronization in both cases. The Node freezes
and reauthorizes that scope rather than asking clients to partition a full sync.

Source inventory and exact caller mapping are in the connectivity domain evidence.
K still requires full retained multi-pool and protected join-code contracts plus
platform feasibility review. Runtime dispatch, native custody and installed
connection evidence remain later implementation and proof obligations.
