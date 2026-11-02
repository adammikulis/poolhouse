# Inactive native caller admission

The public manifest is fixed at `/Library/Application Support/Poolhouse/Approval/manifest.json`.
`ManifestFields.decode` returns untrusted parsed data. Only `PublicManifest.loadFixed` creates
the opaque manifest after descriptor validation. Its descriptor ancestry is root-owned and cannot grant write access through modes or ACLs. The
Poolhouse and Approval directories are 0755; the regular manifest is 0644 with one hard link.
The reader takes a bounded snapshot and checks file identity and metadata before and after reading.
The installer owns publication and replacement of this public file. No immutable filesystem flag
is assumed. SHA256 entries describe installer-verified artifact bytes, not a runtime path lookup.

The manifest schema is version 2. It binds owner, authority and installation identifiers,
generation, receipt verification key, policy digest, purpose and rotation policy, and exact
helper, GUI and node code pins. Each pin includes code-directory and artifact hashes plus
publisher kind, publisher-key, requirement and entitlement digests. The helper service must
equal `app.poolhouse.authority`. The shared Rust parser rejects duplicate or unknown fields,
coercions, escapes, noncanonical hex and oversized input; the native UI uses its C interface.
The policy digest describes the code and purpose/rotation fields; it does not authenticate
the separately encoded installation, owner, generation or key. Protected bootstrap and the
authority store must bind the complete manifest before it can be used as authority.

`SecCodeCreateWithXPCMessage` receives the actual incoming dictionary. Dynamic
`SecCodeCheckValidity` and signing-information unique cdhash checks establish code identity;
connection EUID must be root. Public XPC EUID/ASID getters describe connection-time attributes,
not a documented atomic raw audit-token snapshot for every message. No PID lookup or code path
is used. The GUI's own kernel AUID/EUID/ASID must belong to the target owner and a local graphical
session. The root daemon's ASID is not compared to the GUI's. Root-side sender AUID admission
still needs a separate reviewed implementation; session-owner lookup is not claimed equivalent.

The transport plan specifies only the privileged system endpoint and a caller code requirement.
It allocates no connection: the SDK requires activation before an XPC connection can be released.
`AuthenticatedHelper.connect`, challenge validation and commit remain unavailable. No result is
serialized or sent. Received-message code requirements do not guard outgoing secrets or assertions.
The authenticated helper is the sole card producer; strict duplicate-field card decoding remains
required before active transport replaces JSONSerialization.

The GUI uses a fresh `LAContext` per attempt with `deviceOwnerAuthentication` and biometric unlock
reuse duration zero. No context, credential or boolean result is imported from a caller. Contexts
are invalidated before completion and on cancellation, disconnect, expiry or session loss. A transient
focus loss during OS authentication is not itself success or cancellation; the first callback on the
main actor must find the same visible window, immutable snapshot, active GUI and validated caller.
It never waits for focus to return or retries. The local opaque result is consumed once against the
same challenge, snapshot, authority, pinned GUI and owner session. It is not cryptographic OS proof.
The future helper trusts that pinned, hardened GUI under the approved administrative trust model;
presence does not replace operation policy, governance signatures or quorum.

Card `head` and `generation` name the protected native authority state, not a pool's governance chain.
They are immutable internal snapshot/CAS bindings. Head is lowercase 32-byte hex; zero is permitted
only for `found_pool`, which can also have a nonzero existing authority head. Generation is a strict
CF integer from zero through Int64.max; booleans, floats, negative and overflowing values refuse.
Every future helper commit must revalidate the exact actual authority head/generation and request
nonce/digest. Full verified human text and pool/anchor/signer/target context remain visible.

Active result transport requires a one-shot reply on the same admitted helper challenge and connection,
with exact request, authority, nonce, digest, head/generation and current owner session. No arbitrary
client boolean is an approval. Actual message/reply routing, spoof/exec/disconnect behavior and current
login binding must be demonstrated before transport is enabled. Root-owned helper pending-request
limits must prevent fatigue; the GUI never automatically retries.

Actual consecutive password and biometric authentication, no-biometric fallback, focus, cancellation
and session tests remain activation prerequisites. Reuse duration zero controls reuse of a recent
biometric device unlock; it does not prove global password-cache behavior. See
[Apple's reuse-duration API](https://developer.apple.com/documentation/localauthentication/lacontext/touchidauthenticationallowablereuseduration).

Installer/server work must also establish hardened runtime and entitlement policy against runtime
injection, immutable helper-side card semantics, and final serialized authorization/state checks.
An exact code hash alone does not establish those properties. This checkpoint does not replace
those checks or prove human intent, authorization freshness, or a usable approval workflow.

SDK evidence: macosx27.0 `Security/SecCode.h` (SecCodeCreateWithXPCMessage,
SecCodeCheckValidity, kSecCodeInfoUnique), `xpc/connection.h` (privileged service flag,
caller code requirements, EUID/ASID), `bsm/audit.h` (local session APIs), and `sys/acl.h`.
See [Apple's API](https://developer.apple.com/documentation/security/seccodecreatewithxpcmessage(_:_:_:))
and [Apple's XPC identity guidance](https://developer.apple.com/forums/thread/681053).
