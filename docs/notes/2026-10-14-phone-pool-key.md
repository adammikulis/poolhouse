# Android pool key foundation

This bounded leaf adds an Android pool key lifecycle and a typed founding request. It does not complete GOV-3 or POOL-2, and no production caller uses it yet.

`PoolKey` provisions a separate nonexportable Android Keystore P-256 key. It never uses or deletes the approval or enrollment key. A private random handle is mapped to the linked phone endpoint; retries load the existing key. Provisioning permits four mapped keys and only one unbound pending key. Missing aliases, orphaned keys, unreadable metadata and persistence failures refuse instead of replacing a key.

The key requires strong biometric authentication for every operation and is invalidated by new biometric enrollment. StrongBox is attempted where advertised; only its specific unavailable exception permits an attempt in the trusted environment. Loaded key metadata must report secure hardware enforcing the strong biometric policy, per-use authentication and enrollment invalidation. Hardware level is a local diagnostic, never remote attestation.

`PoolKeyBinding` reconstructs founding acceptance bytes through `FoundingCharter`, binds the actual local public key to a strong admin entry, checks the separately retained node device certificate fingerprint, and refuses another anchor after binding. The phone endpoint TLS pin and device certificate fingerprint remain distinct. Returned bytes are copied and the reviewed card belongs to the immutable request.

Signing takes that typed request, rechecks its device, key and anchor, and uses a fresh biometric CryptoObject. Concurrent operations on an alias are refused. Cancellation and duplicate callbacks cannot complete twice; the callback must carry the prepared Signature and biometric authentication type. The anchor is committed synchronously before signing, and DER signatures are normalized to low s. A rejected operation never automatically prompts again.

The non-author attack-first review covered cloned devices and pools, replay, forged or insufficient signatures, malicious members, stolen keys and phones, malicious or injected agents, downgrade, fatigue and presence takeover. Implementation review found selected weak/recovery keys and the risk of truncating a full charter inside the system prompt; both were corrected. The complete card must be shown by a separately reviewed trusted screen before authentication. This leaf supplies the immutable card/request boundary and does not claim that screen exists.

Verification on the feature branch:

- Gradle `protocolChecks`: 29 pool key binding/provider assertions, 116 founding assertions and 66 governance act assertions, plus the existing approval and chat checks, pass.
- `scripts/test all tests/test_phone_friction.py`: 3 passed.
- A preliminary SDK 36 debug APK compile passed before the final typed-request review refinement. The coordinator owns the final published APK build.

The signature provider test uses a software P-256 key in memory. It does not exercise Android Keystore, StrongBox, biometric enrollment invalidation, cancellation callbacks or hardware authentication. Those remain unverified on available hardware. Production per-pool public-key enrollment, node-derived strength, authenticated request transport, the prompt/refusal ledger, a full-card screen and the production signer are separate prerequisites. No owner risk acceptance is inferred.
