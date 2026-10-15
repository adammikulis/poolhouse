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
