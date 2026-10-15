# Native authority boundaries

The request/CAS core and descriptor store are inactive foundations. They have no production authorization-proof constructor, installer, IPC server, key registry or native password backend. Approval-phone enrollment does not grant pool administrator status or Strong key assurance.

The store addresses tampering by an agent running as the target account. Its fixed state directory and every parent must remain root-owned and non-writable by that account. Root compromise and privileged disk rollback are outside this boundary.

The installer must create the pristine envelope once, with authenticated account and authority identity. The runtime only opens existing state; missing or damaged state is refused. Installer creation, pending-request cancellation/expiry/fatigue, exact registered public-key algorithms and signature verification remain required before activation.

macOS descriptor ACL checks use the SDK contracts in `sys/acl.h`, `sys/fcntl.h` and `sys/stat.h`. `fstatx_np` and `filesec_query_property` distinguish absent ACLs from retrieval failure. Valid ACL iteration ends with `EINVAL`; any other error refuses access. Private entries permit no extra allow rights; public parent entries permit only non-mutating rights. Deny entries remain intact.

`filesec_query_property` reports presence as a nonzero validity mask, not the integer one. Its successful nonzero result still requires retrieving and validating the complete ACL.

Primary references:

- [Apple filesystem access controls](https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/FileSystemProgrammingGuide/FileSystemDetails/FileSystemDetails.html)
- [Apple filesec property implementation](https://github.com/apple-oss-distributions/Libc/blob/main/gen/filesec.c)
- [Apple ACL entry implementation](https://github.com/apple-oss-distributions/Libc/blob/main/posix1e/acl_entry.c)
- [Apple descriptor ACL retrieval](https://github.com/apple-oss-distributions/Libc/blob/main/posix1e/acl_file.c)

## Native authorization policy

The private macOS backend uses a new GUI-created AuthorizationRef with flags zero for each immutable approval card. Only a trusted native renderer may create it. The helper validates its exact custom right, requests it itself with InteractionAllowed and ExtendRights, then destroys the imported reference's rights. External forms are transient bearer capabilities, never persisted or logged. CopyInfo context is not identity proof.

The exact user rule requires session-owner authentication, no root shortcut, zero timeout, no sharing, three password-correction attempts inside one transaction, and the ordered Apple chain: `builtin:authenticate`, `builtin:reset-password,privileged`, `builtin:authenticate,privileged`. The chain permits the operating system's biometric/password choice. No PreAuthorize or LeastPrivileged flag is used.

Rule identifier and requirement must match the original installer identity recorded in protected state. Installer and right-modification authority must prevent same-account mutation of the right between policy verification and authorization. This is local provenance, separate from running GUI code validation and remote key assurance. Missing installer identity, peer verification, native card rendering, protected pending lifecycle/fatigue, or OS installation leaves the path inactive. Root administrators can control the helper and its protected state; this boundary does not protect against root.

Source evidence uses Apple Security commit `db15acbe6a7f257a859ad9a3bb86097bfe0679d9`:

- [GUI token and session](https://github.com/apple-oss-distributions/Security/blob/db15acbe6a7f257a859ad9a3bb86097bfe0679d9/OSX/authd/authtoken.c#L209-L218)
- [Credential freshness and root handling](https://github.com/apple-oss-distributions/Security/blob/db15acbe6a7f257a859ad9a3bb86097bfe0679d9/OSX/authd/engine.m#L340-L429)
- [Mechanism selection and final owner check](https://github.com/apple-oss-distributions/Security/blob/db15acbe6a7f257a859ad9a3bb86097bfe0679d9/OSX/authd/engine.m#L800-L850)
- [User-rule serialization and provenance](https://github.com/apple-oss-distributions/Security/blob/db15acbe6a7f257a859ad9a3bb86097bfe0679d9/OSX/authd/rule.c#L944-L1078)
- [Apple authentication mechanism chain](https://github.com/apple-oss-distributions/Security/blob/db15acbe6a7f257a859ad9a3bb86097bfe0679d9/OSX/authd/authorization.plist#L1968-L1980)

Source and scratch tests do not prove actual installed-OS password fallback, cancellation, no-cache or cross-session behavior. Native administrative trust enrollment and remote key assurance are unimplemented; this local backend grants neither.
