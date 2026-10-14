# Phone founding protocol evidence

Date: 2026-10-14. Code: `1f522a6f0`, branch `feat/phone-founding-protocol`, based on
`00f54c49e3c1be16073e76b147684978a8d1e2a5`. Device: this computer, macOS 26.6, arm64.
Implementation and activation owner: `chatgpt-9973c8`, native runtime `gpt-6.1-sol`, low reasoning.

This batch supplies protocol classes, a derived founding card and DER normalization. No production
signer or approval transport calls these classes yet. GOV-3 and POOL-2 remain unfinished.

## Independent attack-first review

Non-author reviewer: `/root/phone_attack_review`, inherited runtime. Written before implementation;
reviewed the node charter/key protocol and Android canonicalization. Findings became the following
invariants and tests. No owner risk acceptance was requested or inferred.

| Threat | Required protection or boundary |
| --- | --- |
| Cloned pool or device | Hash the whole charter with every acceptance blank; check anchor and pool ID. A byte-identical copy retains identity; physical possession is outside this batch. |
| Replay | Preserve acceptance v2 domain, anchor, key and entry fields; require founding epoch 1 and empty previous charter. Identical-charter reuse is intentional; transport freshness remains pending. |
| Forged or below-threshold signature | Require an exact listed key, valid key shape, sorted distinct entries and reachable thresholds. One acceptance is not a governance quorum. Verify normalized signatures at the node. |
| Malicious member | Derive every displayed policy, key, strength, threshold, hold and recovery delay from the charter. Supplied summaries grant no authority. |
| Stolen phone or key | Declared strength is displayed; protocol possession establishes no biometric protection. Keystore enforcement and recovery remain pending. |
| Malicious agent | Reconstruct bytes locally and reject supplied bytes that differ before signing. |
| Unknowingly injected agent | Names and labels are quoted data. Snapshot input and clone returned bytes so displayed terms and signing bytes stay bound. |
| Secure-to-open downgrade | Require explicit supported policy/version and exact schema/types. Show the signed policy without defaults. |
| Approval fatigue | No prompt or retry loop in this batch. Fatigue ledger and transport integration remain pending. |
| Presence takeover | Connectivity or a first certificate grants no admin rights. Require a listed signing key; derive certificate check words from its bytes. |

The implementation review found Android checked JSON exceptions, incorrect automatic-threshold
wording and negative tests that could fail only on an anchor mismatch. All were corrected; the
reviewer reported no remaining blockers for this foundation-only batch. This written review does
not replace the landing queue's eligible independent session review.

## Checks run

- Rust reference generator: `cargo test --manifest-path app/Cargo.toml -p poolhouse-node --test
  phone_founding_vectors generate_reference -- --ignored`, through `scripts/testslots.py run`.
  Fixture: `tests/fixtures/phone-founding-vectors.json`. The normal Rust test checks canonical
  charter bytes, anchor, pool ID and every acceptance against the node implementation.
- `cargo test --manifest-path app/Cargo.toml -p poolhouse-node --test phone_founding_vectors
  --test gov_charter`, through the maintained CPU broker: 21 tests passed; generator ignored.
  The node accepts the shared low-s signature and rejects its high-s twin and malformed signatures.
- `python app/android/build.py GRADLE 8.9.1 36 debug`: protocol checks and debug APK build passed.
  Gradle 9.2.0; Android SDK 36; Android Studio JBR. Founding checks: 116 assertions, including
  matching invalid requests, certificate substitution, caller mutation, shared Ed25519 decoding,
  automatic thresholds under both counting units and DER scalar boundaries.
- `scripts/test all tests/test_phone_friction.py tests/test_pool_found.py tests/test_api_found.py`:
  18 passed, all ran. Isolated Python 3.13.5 environment with declared test dependencies.
- Two committed-code mutations separately disabled supplied-byte comparison and low-s conversion.
  Each made `foundingChecks` fail. Both were restored from HEAD; all 116 checks passed again and
  the source diff was empty.
- `scripts/test gate`: 256 passed, 3 failed. Red-team coverage and generated command reference passed.
  The identical failures were reproduced on clean target `00f54c49e` with explicit selectors:
  `tests/test_budgets.py` (orphan checkout and stale HANDOFF budget) and
  `tests/test_affected_scripts.py` (missing `tests/test_installed.py` mapping for `scripts/agent-identity`).
  The stale HANDOFF budget is lowered from 716 to 714 in this evidence commit; no limit or guard is weakened.
  Incremental `scripts/test all tests/test_budgets.py`: 70 passed, only the baseline orphan check failed.
  HANDOFF remains 1,214 lines against a 500-line wall; its remaining pending work is preserved.

## Pending delivery and hardware proof

Landing requires an eligible independent session's acceptance at the final SHA and the queue's
combined integration gate. The unrelated checkout flagged orphan has recent commits and dirty work;
its owner must be identified before cleanup. It has been preserved.

This batch has no new app screen to activate. The Android certificate provider has not been driven
on hardware. biometric keystore integration, approval transport, production `Signer`, fatigue ledger,
recovery and the installed phone/node founding journey remain pending, with this owner responsible
until an activation owner acknowledges a handoff. No Android phone or Linux verification is claimed.
