Attestation policy sources:

- [Android verification guidance](https://developer.android.com/privacy-and-security/security-key-attestation): nearest-root extension, official revocations, factory-key date exemption and RKP date enforcement.
- [Android attestation schema](https://source.android.com/docs/security/features/keystore/attestation): authorization tags, boot state and application identity.
- [Official roots](https://android.googleapis.com/attestation/root), retrieved 2026-10-15. DER SHA-256: `cedb1cb6dc896ae5ec797348bce9286753c2b38ee71ce0fbe34a9a1248800dfc`, `6d9db4ce6c5c0b293166d08986e05774a8776ceb525d9e4329520de12ba4bcc0`.

The factory date exemption is restricted to the first official root's exact SPKI. Its path is checked at the common certificate validity-window intersection; a path without an intersection is unsupported. Other paths use current time. The phone app signer and minimum package version are immutable build policy; either absent value refuses verification. A signing publisher must assign monotonically increasing package versions to security-relevant APK changes.

Revocations come only from an authenticated in-memory official HTTPS response. Transport must enforce origin, certificate verification, response bounds and Cache-Control revalidation/no-store semantics before constructing the opaque value. The verifier enforces a maximum 24-hour freshness window reduced by Age, monotonic expiry, wall-clock rollback refusal, strict schema and rejection of every listed status.
