# Inactive native peer admission

The public manifest is fixed at `/Library/Application Support/Poolhouse/Approval/manifest.json`.
Its descriptor ancestry is root-owned and cannot grant write access through modes or ACLs. The
Poolhouse and Approval directories are 0755; the regular manifest is 0644 with one hard link.
The reader takes a bounded snapshot and checks file identity and metadata before and after reading.
The installer owns publication and replacement of this public file. No immutable filesystem flag
is assumed. SHA256 entries describe installer-verified artifact bytes, not a runtime path lookup.

The manifest schema is version 1, owner_uid, authority_id, helper {service, cdhash, sha256}, and
gui {cdhash, sha256}. The service must equal `app.poolhouse.authority`. Pins are lowercase hex;
cdhash is 20 bytes and SHA256/authority identity are 32 bytes. The parser rejects duplicate or
unknown fields, coercions, escapes, and oversized input. The installer emits this ASCII schema.

`SecCodeCreateWithXPCMessage` receives the actual incoming dictionary. Dynamic
`SecCodeCheckValidity` and signing-information unique cdhash checks establish code identity;
connection EUID must be root. Public XPC EUID/ASID getters describe connection-time attributes,
not a documented atomic raw audit-token snapshot for every message. No PID lookup or code path
is used. The GUI's own kernel AUID/EUID/ASID must belong to the target owner and a local graphical
session. The root daemon's ASID is not compared to the GUI's. Root-side sender AUID admission
still needs a separate reviewed implementation; session-owner lookup is not claimed equivalent.

The transport configures only the privileged system endpoint and a peer code requirement. It does
not activate the connection. `AuthenticatedHelper.connect` remains unavailable. No admitted
challenge reaches authorization, and no ExternalForm is sent. Peer requirements validate received
messages; they are not an outgoing-secret gate. A future bearer path must use a reply to the same
admitted challenge with exact request, nonce, digest and snapshot, and fail on disconnect or login
change. It requires real message/reply-routing and spoof/exec/disconnect proof before enablement.

Installer/server work must also establish hardened runtime and entitlement policy against runtime
injection, immutable helper-side card semantics, and final serialized authorization/state checks.
An exact code hash alone does not establish those properties. This checkpoint does not replace
those checks or prove human intent, authorization freshness, or a usable approval workflow.

SDK evidence: macosx27.0 `Security/SecCode.h` (SecCodeCreateWithXPCMessage,
SecCodeCheckValidity, kSecCodeInfoUnique), `xpc/connection.h` (privileged service flag,
peer code requirements, EUID/ASID), `bsm/audit.h` (local session APIs), and `sys/acl.h`.
See [Apple's API](https://developer.apple.com/documentation/security/seccodecreatewithxpcmessage(_:_:_:))
and [Apple's XPC identity guidance](https://developer.apple.com/forums/thread/681053).
