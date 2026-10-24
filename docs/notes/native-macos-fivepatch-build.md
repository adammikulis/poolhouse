# Mac five-patch producer build checkpoint

Observed 2026-10-24; macOS 26.6 arm64. **UNTRUSTED build candidates only.** Neither executable was run,
installed or substituted for the active host. No model, upstream test, TLS, IPC or OS confinement probe ran.
These exact artifacts include the fifth patch retaining complete original native invocations. This is internal data retention, not authenticated transport.

## Source and execution recipe

- Upstream: `6aaac694803004a63c6f393d153c96d22521a6b9` (Codex 0.164.0).
- Exact patched tree: `c8b1a0f97f2cf06e94216a1c9c18edb7142974c3`.
- Cargo.lock SHA-256: `c043b6b7e67bb2f0ae4b1b8dd485cd6a24bd1cb7ef3a42752d303bc5e5ca4be1`.
- Toolchain: `rustc 1.99.1 (58cf8f9c3 2026-10-13)`; Cargo 1.99.1.
- Five ordered patch SHA-256 values, verified before application and build:

  - `0001-harden-producer-entrypoints.patch`: `fa8b66bdc178b9649fc1f30dd9d65a6f626536f4f6d0bf1f3e4ee43fcd4ac022`.
  - `0002-retain-native-session-context.patch`: `8ab2fdc96d34ab5c8e5acf51b9b4addeb3a6c879b1a3dc76636669ab53200578`.
  - `0003-retain-original-native-occurrences.patch`: `d9509ba0eee0715d2bed658bf72f8e7314ff2845728ebbe6bbc2d6f3f16f49bf`.
  - `0004-retain-actual-native-actor-context.patch`: `4bb99f601acad2fc31baf6c7d4263ddf6770815709861a55f5051b9203da9fcb`.
  - `0005-retain-complete-native-invocations.patch`: `e454706fa291c141bf28069de98938422220208b7c09e680e2fac4133d5914cc`.

A fresh private source tree preserved the earlier two- and four-patch checkouts and artifacts. One maintained CPU admission
wrapped both sequential package builds and internal `prepare_candidate.prepare` calls. The build used an APFS clone
of the quiescent four-patch target cache; this is a warmed-cache build, not an independent reproducibility proof.
The new target was separate and Cargo-managed. The sanitized environment retained ordinary Cargo/Rustup build paths;
no inherited signing, loader-injection or compiler overrides were passed. Tracked source HEAD, index tree, status and
lock digest matched before and after. The maintained preparation helper digest was checked again before signing.

For each variant (`codex`, then `codex-app-server`), the maintained entry point received:

```text
python scripts/build_codex_package.py --target aarch64-apple-darwin --variant <variant>
  --cargo <locked-wrapper> --cargo-profile dev-small --package-dir <fresh-private-output>
```

The fixed wrapper invoked Cargo `+1.99.1` with `--locked`; `CARGO_BUILD_JOBS=1`. The package builder also built its
required code-mode host and used its maintained dependency verification. It did not execute candidate artifacts.
CLI package: started `2026-10-24T17:20:10.529039Z`, exit 0 after 366.522 seconds. Standalone package: started
`2026-10-24T17:26:17.050531Z`, exit 0 after 4.782 seconds. No redundant compile checks or upstream tests followed.

## Artifact hashes

SHA-256 values; original package and separately prepared copy are distinguished. Only the copied producer entrypoint
was signed ad hoc with hardened runtime; no personal signing identity or entitlements were used.

| Payload | Original | Prepared |
| --- | --- | --- |
| codex/bin/codex | `68b995d4db0a2f4c9d7ed6faac214a8128313e99f3e90b0e672290f13348d7cf` | `686d7d40a0cd3aac9cdf7809cd4317dcb148258d392bb9d8d664ac84f279997f` |
| codex/bin/codex-code-mode-host | `8e06fcf688d2a3d2d8ff8d6c798a9d367e315517c85692bead9af5ab1f583c03` | `8e06fcf688d2a3d2d8ff8d6c798a9d367e315517c85692bead9af5ab1f583c03` |
| codex/codex-package.json | `c77eb3abe21da837655153670c53ccbb89c26995ba3ccad2b72204b4670e1ad8` | `c77eb3abe21da837655153670c53ccbb89c26995ba3ccad2b72204b4670e1ad8` |
| codex/codex-path/rg | `a326a1fb48074202e9ad41e4cd1e389eeea372c8c6f7d7e80da81176d5d9430e` | `a326a1fb48074202e9ad41e4cd1e389eeea372c8c6f7d7e80da81176d5d9430e` |
| codex-app-server/bin/codex-app-server | `9650451091980f1fb3977888f280c39440b3177daf70708bbaa68ae15740065e` | `0e2bde459d64ba1cef0cca55a91e770102d91a0f4b4319244da7677514c14da9` |
| codex-app-server/bin/codex-code-mode-host | `8e06fcf688d2a3d2d8ff8d6c798a9d367e315517c85692bead9af5ab1f583c03` | `8e06fcf688d2a3d2d8ff8d6c798a9d367e315517c85692bead9af5ab1f583c03` |
| codex-app-server/codex-package.json | `670975f440d9c6f972c538b7d4fff7af0191c8598d176398453c41b68561530f` | `670975f440d9c6f972c538b7d4fff7af0191c8598d176398453c41b68561530f` |
| codex-app-server/codex-path/rg | `a326a1fb48074202e9ad41e4cd1e389eeea372c8c6f7d7e80da81176d5d9430e` | `a326a1fb48074202e9ad41e4cd1e389eeea372c8c6f7d7e80da81176d5d9430e` |

Both strict preparation receipts report signature flags `0x10002`, absent original/candidate entitlement documents,
and unchanged source package plus nonproducer payloads. Their status is `UNTRUSTED build candidate`, with
`authentic_upstream=false`, `admission=false`, and `protected_custody=false`. No hardware protection or H/K/P acceptance
is inferred from signature readback. Earlier two-patch DYLD observations do not apply to these new executable bytes.

## Evidence binding

Private custody handle: `poolhouse-codex-five-vjmav773`; raw logs, source and packages remain outside Git.

- `build.py`: `b2f0273ccbaf57c470d1205fab44815627d1928312975fe86959355cee839c43`.
- `source-pin.json`: `9bb862c288062f78dd3eaa43caddacecb4f64a1e6efecfe2cee4b8952a9a216a`.
- `input-digests.json`: `676880e5ff50442b60c216c3cecaa41ce6342637d1207c6365b22b93837ff577`.
- `result.json`: `f0a7cbc0ce23743b42c93f068c65efa005e3a8a29a8213868e00c4206271b812`.
- Start input `source_json`: `21b0b121129b16526d0e5c9064facfbbd63148acb9fc2bdd20cd07088c8cc56a`.
- Start input `prepare_helper`: `a10a09dc990edf61efe5ae3f83ddb0aeda925f246138e6cc81c32a27b1c68ba1`.
- Start input `builder`: `7a5b7ba90604944831dd25ad89ccba46b4c40f44ec6aeec67a6d058a0c837ad2`.
- Prepared codex receipt: `3e44a8a5cfb1bbdb8d1608bbeadec4d6312bfe3b645e7ac8fd3f33bdeac7863e`.
- Prepared codex-app-server receipt: `22af0d84db646c2d05a2411378545f8e12d207c3ca93b0597a4a3bcc8cb1929b`.

Written independent attack and exact-runner review preceded the single admitted build. This checkpoint supplies
platform build/package evidence only; private authenticated transport, actual retained-request delivery, native
caller admission, installed root/child lifecycle proof and protected service custody remain unfinished.

Independent static verification (one CPU admission, 1.834 seconds) rechecked all 18 payload/receipt hashes,
the five patches, source tree, lockfile, manifests and helper continuity. All four original/prepared producer
strict signature checks passed: originals `0x20002`, prepared copies `0x10002`, absent entitlement documents.
Receipt custody handle: `fivepatch-independent-verification.json`; SHA-256
`265b7f2719904fd9e840f794d91e12de34e6e5acf79df7d53d0f457604c5ca7a`.
This verification executed no candidate or probe and adds no installation/admission proof.
