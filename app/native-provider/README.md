# Existing native key observation

This unlinked leaf is for a future dedicated, measured user-session signing holder. It is not part of the ordinary app,
root daemon or existing approval-card transport. It creates no keys, looks up no keychain items, signs nothing and opens
no authentication UI. The caller must already own the live native key reference.

An observation retains that exact reference, checks documented native attributes and algorithm support, and copies only
its public component. Its key policy is always unknown. Attributes, token IDs, public-key equality, application labels,
`CFEqual` and successful UI authentication cannot issue owner/root admission or per-use policy evidence.

Public `SecAccessControl` APIs do not expose the creation flags. Apple DTS documents that Secure Enclave access control
is add-only, which can support future continuity from a genuine protected creation path. A receipt alone remains
insufficient: the future holder must verify protected, rollback-aware exact root/key/policy origin and current native
identity. Missing origin, replacement, restart without verified continuity, or owner/session/code changes must remain
unknown and retain custody. No policy-evidence constructor exists here.

The Data Protection keychain requires a user login context and provisioned host entitlements. A root launch daemon
cannot replace that holder by using a weaker file-based keychain. Fresh operation authentication belongs to the holder's
one-shot exact-transcript flow; a caller's `LAContext` or an old approval card is never imported. Actual consecutive
biometric/password, cancellation, session-loss and authentication-cache behavior still need authorized hardware proof.

Sources: [Apple keychain implementations](https://developer.apple.com/documentation/technotes/tn3137-on-mac-keychains),
[Secure Enclave key restrictions](https://developer.apple.com/documentation/security/protecting-keys-with-the-secure-enclave),
[add-only access control](https://developer.apple.com/forums/thread/724013), and the macOS 27 public `SecKey.h`,
`SecAccessControl.h`, `SecItem.h` and `LAContext.h` headers.
