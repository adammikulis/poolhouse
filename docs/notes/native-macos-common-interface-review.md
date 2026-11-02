# Mac review request: common lifecycle and protected approval interfaces

Review proposal, 2026-10-24, against source checkpoint `94dcc9ac40ca0010b38cb6abf76db12855f4c541`.
This records Mac requirements for the shared owner's closure A decisions. It defines no alternate wire protocol,
native issuer or adapter, and establishes no K acceptance, protected custody, H admission or installed behavior.

## Existing shared definitions

[Startup settings](../../contracts/node/domains/settings.toml) already retain requested mode, revision, operation,
previous effective observation and current known/unknown observation. `StartupChangeRequest` carries generation,
request ID and expected revision. `SettingsOptionalApproval` contains a product `ApprovalId`: OS consent needs its
own truthful projection rather than a fabricated product approval.

[Local authority](../../contracts/node/domains/local_authority.toml) separates `pool_approval` from
`installation_credential` in `LocalAuthorityScopedOperation`. Its bound action states include outcome unknown and
recovery required. [Approval operations](../../contracts/node/domains/approvals.toml) retain immutable operation,
requester, installation/device, resource generation, nonce, expiry, governance and presentation bindings.
The [v3 native adapter graph](../../contracts/node/adapters.toml) already describes opaque message/caller custody;
these records alone create no authenticated constructor or active protected transport.

## SDK facts and their limits

The installed Xcode macOS SDK headers are the primary source for these observations. Paths below are relative to
`MacOSX.sdk/System/Library/Frameworks`; they describe APIs, not executed OS proof.

- `ServiceManagement.framework/Versions/A/Headers/SMAppService.h:15–34`: `NotRegistered` means absent registration;
  `Enabled` means registered and eligible to run; `RequiresApproval` also covers withdrawn consent;
  `NotFound` is an error. None proves a running authenticated node, usable storage or committed product state.
  Lines 147–168 distinguish immediate user-context login/agent startup from daemon startup after administrator
  consent, and report denied/signature failures. Lines 188–232 describe unregister killing without synchronous
  reaping and asynchronous completion; this is not a domain rollback transaction.
- The same header, lines 120–128, requires a daemon's containing app to be accessible before login.
  Lines 273–283 expose the Login Items settings destination; opening it grants nothing.
- `Security.framework/Versions/A/Headers/SecCode.h:193–227`: `SecCodeCreateWithXPCMessage` derives code identity from
  the actual received XPC dictionary's associated audit token; dynamic validity checks include host and requirement.
  Connection UID/session snapshots alone do not establish every message's sender or lifetime.
- `LocalAuthentication.framework/Versions/A/Headers/LAContext.h:171–187,317–326`: evaluation returns asynchronously;
  invalidation cancels that context. A zero biometric reuse interval prevents reuse of a previous biometric device
  unlock, not proof of global password-cache freshness or cryptographic operation binding.

## Decisions requested from the shared owner

1. Specify one owned service identity, endpoint and storage installation across Boot/Login/Neither, and the reviewed
   mapping from each mode to bundled triggers. Login/agent triggers must reach the same service, not a second user
   node. Default Neither still permits ordinary app/API activation automatically. Public SM APIs do not create the
   dedicated service identity or prove protected storage. Resolve that custody boundary explicitly.
2. Bind inspect/apply/cancel to the existing exact startup operation, generation and revision. Inspect separates OS
   status from verified effective mode and service/storage readiness; uncertainty remains unknown, not Neither.
   Apply retains owned pending state through OS consent, disconnect or partial failure. Cancel races atomically with
   commit: report committed if it won; report cancelled only after the prior effective state is restored and verified.
   Otherwise retain custody and recovery required. Unregister success alone cannot settle this state.
3. Define how OS-consent pending, denial, cancellation and unavailable storage project into the existing startup
   view without treating OS consent as `ApprovalId`. Keep one pending operation, with passive status inspection
   resuming that operation. Confirm ownership of unresolved native resources and terminal cleanup evidence.
4. Complete the canonical versioned, domain-separated signing transcript and protected prepare/present/consume
   semantics for both scoped branches. Preserve the complete immutable nested target/signer/governance context,
   installation/device, intent, state transaction, expected authority head, nonce, generation and expiry; distinguish
   intent digest, transcript digest and authority head. Bind actual message caller/helper code, current owner session,
   immutable visible snapshot and fresh authentication to an opaque one-shot result. Revalidate and atomically consume
   or cancel against current state. A dialog callback boolean, OS service consent or arbitrary digest is insufficient.
   Existing native-authority v1 cards/payloads are unsupported as this authentication path, not a fallback.

The current [approval helper](../../app/native-approval/Helper.swift) deliberately reports unavailable.
Its [admission analysis](../../app/native-approval/ADMISSION.md) distinguishes message checks, local presence and
unfinished root-side admission. The [installer preview](../../app/native-installer/plan.py) and
[transaction log](../../app/native-installer/transaction_log.py) are untrusted/declarative data, not active privileged
installation or transactional proof. The [host specification](../../app/native-host/SPEC.md) likewise separates static
capture from actual running identity. Reconcile these sources rather than activating a prototype card.

## Native journey and review handoff

Start at Neither. Ordinary authorized launch attaches or starts automatically. If a mode change genuinely requires
OS consent, present one contextual native request with its settings destination and retain the pending transaction.
A separate protected product approval appears only for its actual scoped operation. Cancellation, denial and unknown
outcome remain visible and recoverable; no terminal commands, repeated prompts or user-managed launch configuration.

Please return the exact reviewed canonical closure A commit, signatures, state mappings and denial/cancel/unknown
vectors addressing the four decisions above. Mac can then implement behind those shared definitions. Review of K
can finish independently of later installed H/P proof; this note supplies requirements, not that proof.
