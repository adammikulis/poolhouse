# Offline native host candidate

`NativeHostCandidateCheck inspect ABSOLUTE_DIRECTORY ABSOLUTE_INVENTORY` captures inert helper/GUI payloads and prints an **untrusted** public hash inventory. It does not install, launch a service, request authentication, create/use keys, or produce authority. `libNativeHostCapture.dylib` contains the same capture and validation code without an executable entrypoint.

The inventory is canonical ASCII with LF endings. Its first line is `poolhouse-native-candidate-v1`. Each following line contains five tab-separated fields: role, relative path, canonical unsigned decimal byte count, mode, lowercase SHA256. Records are sorted uniquely by path. Empty lines, extra fields, unsupported names and noncanonical numbers refuse. Root-authorized limits are 16 KiB inventory, 64 files and 64 MiB total captured payload, including all resources. Limits refuse rather than truncate.

Supported roles and paths:

| Role | Required path | Mode |
|---|---|---|
| helper | `Helper/PoolhouseAuthority` | 0755 |
| gui | `GUI/PoolhouseApproval.app/Contents/MacOS/PoolhouseApproval` | 0755 |
| gui | `GUI/PoolhouseApproval.app/Contents/Info.plist` | 0644 |
| gui | `GUI/PoolhouseApproval.app/Contents/_CodeSignature/CodeResources` | 0644 |
| service | `Services/app.poolhouse.authority.plist` | 0644 |

Additional GUI resources are flat files under `GUI/PoolhouseApproval.app/Contents/Resources/`, mode 0644. Other shapes, nested frameworks and Python runtimes are unsupported. Every directory and file must be represented by the inventory or its derived parent directories; extras refuse. Paths use ASCII letters, digits, hyphens, periods and underscores, excluding empty/dot/dot-dot components.

Capture uses descriptor-relative no-follow opens, regular files with one hard link, exact modes, source-directory ownership, non-mutation ACLs and pre/post file identity checks. SHA256 covers the immutable captured bytes. Source ownership does not mean authority ownership; caller-chosen inventory hashes are not authentic provenance. Concurrent source changes can cause refusal or yield a captured matching byte snapshot, never installed authority.

`CapturedCandidate.checkProtectedCode` additionally requires every path parent, payload directory and file to be root-owned, nonwritable by group/others and free of ACL mutation grants. It recaptures and compares bytes before/after static code validation. This root-custody precondition excludes ordinary same-user replacement; root/OS compromise is outside the claim. Apple static code validation accepts paths, **not held descriptors**, and is not represented as an atomic snapshot API.

The protected-code check requires thin ARM64/ARM64_ALL Mach-O executables, full strict sealed-resource/nested-code signature validation and exact unique code hashes. Runtime hardening must be present. Entitlements must be absent or an empty dictionary; every entry refuses, including unknown or false-valued entries. This excludes DYLD environment, JIT, unsigned executable memory, disabled executable-page protection, disabled library validation, debugger and task-access exceptions. Fat binaries and other architectures refuse. Static identity checks do not authenticate a running process or confer authority.

The maintained build produces the checker and library through CPU admission. Tests compile literal non-TESTING source and a private TESTING protocol with temporary inert fixtures. No private keys, signing, GUI, live XPC or OS authentication are exercised. A complete sealed helper/GUI/service candidate, authentic inventory pin, physical OS entry/PAM freshness, protected installation/readback, running actual-message admission, shared authority-core wiring and end-to-end person approval remain separate prerequisites. The accepted GUI source is `8adb037bb5729dfbd29d36849291784816b489fb`; integration into short development ancestry is required before bundle assembly. Existing `NativeSender` admission is reused then; this component does not implement a second caller policy or governance fold.

Terminal bootstrap is an exceptional onboarding regression, not a normal product journey. No candidate report or self-selected hash supplies initial trust.

## Historical Mach code observation

`MachSender` retains a bounded received message, its kernel audit trailer and its
rights as one owned object. `MachCode` selects dynamic code using that object's
complete kernel audit token with public `kSecGuestAttributeAudit`.
The current observation primitive refuses complex messages, reply rights and
vouchers; it is not an application request/reply transport.
The supplied requirement is a comparison input; validity uses `kSecCSNoNetworkAccess`.
That documented mode omits online revocation/notarization work; a match is not proof of either.
There is no PID/path fallback or admission from a matching hash. Success transfers a
retained `SecCode` and historical sender data to the caller. Failures preserve output.
Security queries may contact system services; these are not keychain or authentication calls.

Destroying the owned message releases its rights. The object does not establish
receive incarnation or protected caller custody and does not prove current owner-session,
liveness, protected launch, provider admission or authorization. A private authenticated
receiver and independent current-owner issuer remain prerequisites for protected effects.
No caller-provided UID, console UID, or copied audit observation substitutes for them.

`NativeIdentity` observes one code object's designated-requirement bytes, embedded
entitlement bytes and, for certificate-signed code, the leaf certificate's public-key
external representation. Each digest uses a distinct NUL-terminated versioned domain,
a presence byte, an unsigned 64-bit big-endian byte count and the exact OS bytes.
Absent and empty entitlements differ. RSA and EC keys have different domains and
bounded key sizes; unsupported or inconsistent representations fail. These bytes
are not described as SPKI. Ad-hoc code has no publisher-key observation.

This observation does not validate a signature, certificate chain, publisher trust,
entitlement policy, runtime hardening or installed/running process identity. The
installer and receiver must independently perform those checks and bind results to
the protected installation and actual received message. A locally authenticated
ad-hoc publisher needs separate protected evidence; absence of a certificate is not
that evidence. A changed OS representation must be explicitly reconciled rather than
silently accepted.
