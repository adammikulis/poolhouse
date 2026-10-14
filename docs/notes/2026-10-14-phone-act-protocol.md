# Phone admission and removal protocol preview

GOV-3/F7 bounded protocol leaf: Android reconstructs `poolhouse/pool-act/v2` admission/removal bytes from strict parsed fields, compares supplied bytes, binds pool/previous charter/selected strong admin to independently pinned founding terms, and derives the card from those same fields. Removal cards show each retained log prefix and the canonical cut hash; admission cards show certificate check words. Rust supplies shared current-version reference bytes.

This previews new unsigned acts at epoch 1. It does not authenticate founding acceptances, prove that genesis remains current, verify a governance chain or count a quorum. Later epochs, other kinds and existing signatures are refused. The card labels this boundary. Production signing/transport/current-state authentication, biometric key use, fatigue-ledger integration, and hardware journeys remain pending; no owner risk acceptance is inferred. HANDOFF entries remain because their complete behavior is still pending.

Attack-first: phone_attack_review covered all ten repository threat cases before source changes. Root owns integration and activation; this protocol has no production signing caller to activate.

Verification: Rust-generated shared bytes, JVM hostile-input checks, existing founding protocol regression checks and explicit affected Python selectors. Tests use temporary state and no real phone keys or public services. Exact final counts and mutation evidence are recorded with the landing handoff.

Verified in the claimed `poolhouse-phone-act-protocol` tree on macOS, parent commit `703888636`:

- `scripts/test all tests/test_phone_friction.py tests/test_wiring.py tests/test_glossary.py`: 195 passed, all three files ran. Evidence `tmp460skavx.result.json` in the broker's temporary evidence directory. Fixed runner overhead was 17.3 seconds versus 1.1 seconds of tests; root received this as separate contributor-friction work.
- Brokered `cargo test --manifest-path app/Cargo.toml -p poolhouse-node --test phone_act_vectors --test phone_founding_vectors --test gov_acts`: 46 passed, two fixture generators ignored. The new Rust generator was explicitly run to produce the shared JSON.
- Brokered Gradle 9.2.0, Android plugin 8.9.1/API 36 `protocolChecks`: 59 new act assertions and 116 founding assertions passed, existing approval/chat checks passed.
- After commit `370501583`, removing the supplied-byte comparison made `actChecks` fail with `Accepted hostile act`. The committed source returned all 59 assertions to green and left the diff clean.

Android hardware and installed approval journeys were not exercised. This leaf supplies a protocol preview with no production caller; GOV-3/F7 remains unfinished.

Independent implementation review found that removal board names also need the node's reserved-name refusals. The correction adds every reserved name and fresh recomputed invalid-body checks in Java and Rust. Incremental `protocolChecks` passed 66 act assertions, 116 founding assertions and existing approval/chat checks; `phone_act_vectors` passed its shared-byte/body test with all seven reserved-name cases. The earlier 195 affected Python tests and 44 governance-act tests cover unchanged surfaces. Coordinator SDK compilation remains part of integration verification.
