# Mac four-patch producer build checkpoint

Observed 2026-10-24; macOS 26.6 arm64. **UNTRUSTED build candidates only.** Neither executable was run,
installed or substituted for the active host. No model, upstream test, TLS, IPC or OS confinement probe ran.
These exact artifacts contain four patches; later request-retention work is separate.

## Source and execution recipe

- Upstream: `6aaac694803004a63c6f393d153c96d22521a6b9` (Codex 0.164.0).
- Exact patched tree: `64168edc27ec59e7d8d9fb9a4654a0352e1aec1b`.
- Cargo.lock SHA-256: `c043b6b7e67bb2f0ae4b1b8dd485cd6a24bd1cb7ef3a42752d303bc5e5ca4be1`.
- Toolchain: `rustc 1.99.1 (58cf8f9c3 2026-10-13)`; Cargo 1.99.1.
- Four ordered patch SHA-256 values, verified before application and build:

  - `0001-harden-producer-entrypoints.patch`: `fa8b66bdc178b9649fc1f30dd9d65a6f626536f4f6d0bf1f3e4ee43fcd4ac022`.
  - `0002-retain-native-session-context.patch`: `8ab2fdc96d34ab5c8e5acf51b9b4addeb3a6c879b1a3dc76636669ab53200578`.
  - `0003-retain-original-native-occurrences.patch`: `d9509ba0eee0715d2bed658bf72f8e7314ff2845728ebbe6bbc2d6f3f16f49bf`.
  - `0004-retain-actual-native-actor-context.patch`: `4bb99f601acad2fc31baf6c7d4263ddf6770815709861a55f5051b9203da9fcb`.

A fresh private source tree preserved the earlier two-patch checkout and artifacts. One maintained CPU admission
wrapped both sequential package builds and internal `prepare_candidate.prepare` calls. The build used an APFS clone
of the quiescent earlier target cache; this is a warmed-cache build, not an independent reproducibility proof.
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
CLI package: started `2026-10-24T16:39:10.258078Z`, exit 0 after 344.264 seconds. Standalone package: started
`2026-10-24T16:44:54.521445Z`, exit 0 after 4.607 seconds. No redundant compile checks or upstream tests followed.

## Artifact hashes

SHA-256 values; original package and separately prepared copy are distinguished. Only the copied producer entrypoint
was signed ad hoc with hardened runtime; no personal signing identity or entitlements were used.

| Payload | Original | Prepared |
| --- | --- | --- |
| codex/bin/codex | `ce767691b262aa50750f940ea7071a1e686118f66725882174681c78ae8089b2` | `20391599efbb944ed783b87cbe6767e8d4ad47f2b4e311d52ce7495ef447a96b` |
| codex/bin/codex-code-mode-host | `bd20ad1d42804b4c3236b9c98ace3937ea2626f36085abf7ce1f7d11ed1188f5` | `bd20ad1d42804b4c3236b9c98ace3937ea2626f36085abf7ce1f7d11ed1188f5` |
| codex/codex-package.json | `c77eb3abe21da837655153670c53ccbb89c26995ba3ccad2b72204b4670e1ad8` | `c77eb3abe21da837655153670c53ccbb89c26995ba3ccad2b72204b4670e1ad8` |
| codex/codex-path/rg | `a326a1fb48074202e9ad41e4cd1e389eeea372c8c6f7d7e80da81176d5d9430e` | `a326a1fb48074202e9ad41e4cd1e389eeea372c8c6f7d7e80da81176d5d9430e` |
| codex-app-server/bin/codex-app-server | `f141277c9fcc5b5b3783d1ce38879b4b2cae3b275325cf9fa3de7f6b07cc05c0` | `fdf853f279a79cef51fe64101fdd54d7d0fef4c1c83f5d710b81b9c1360032ef` |
| codex-app-server/bin/codex-code-mode-host | `bd20ad1d42804b4c3236b9c98ace3937ea2626f36085abf7ce1f7d11ed1188f5` | `bd20ad1d42804b4c3236b9c98ace3937ea2626f36085abf7ce1f7d11ed1188f5` |
| codex-app-server/codex-package.json | `670975f440d9c6f972c538b7d4fff7af0191c8598d176398453c41b68561530f` | `670975f440d9c6f972c538b7d4fff7af0191c8598d176398453c41b68561530f` |
| codex-app-server/codex-path/rg | `a326a1fb48074202e9ad41e4cd1e389eeea372c8c6f7d7e80da81176d5d9430e` | `a326a1fb48074202e9ad41e4cd1e389eeea372c8c6f7d7e80da81176d5d9430e` |

Both strict preparation receipts report signature flags `0x10002`, absent original/candidate entitlement documents,
and unchanged source package plus nonproducer payloads. Their status is `UNTRUSTED build candidate`, with
`authentic_upstream=false`, `admission=false`, and `protected_custody=false`. No hardware protection or H/K/P acceptance
is inferred from signature readback. Earlier two-patch DYLD observations do not apply to these new executable bytes.

## Evidence binding

Private custody handle: `poolhouse-codex-four-9ukxjswa`; raw logs, source and packages remain outside Git.

- `build.py`: `3283205be91be1a00079d2b479753eca9a233d40a87df27014d8d92049b6fe87`.
- `source-pin.json`: `6e1b66c27a5376f26faf71ff3c3d4adb47cf110f130018244f8319c22ff71b9b`.
- `input-digests.json`: `08a4dd555dea3a59cbbe0c8ec21ef733490a0cabaa3353d02b5d71a7f7449e64`.
- `result.json`: `71df69ec75cb5a1d2430fbb5d48b40d2fa68a261e8e264803ccbc24c0d83435b`.
- Start input `source_json`: `2d679eb8c3c58617861bca8119c8c66bf41ca7c477ac4b4fa20dee900b6f6959`.
- Start input `prepare_helper`: `a10a09dc990edf61efe5ae3f83ddb0aeda925f246138e6cc81c32a27b1c68ba1`.
- Start input `builder`: `7a5b7ba90604944831dd25ad89ccba46b4c40f44ec6aeec67a6d058a0c837ad2`.
- Prepared codex receipt: `8d36177218153a2751984dfbbf2358ad4f8b68d17ed20b286e64cce91f4a4669`.
- Prepared codex-app-server receipt: `26f4f3c13f713d285016417f5e34e99a563d2c460f0115a58012b0f66ccf0b02`.

Written independent attack and exact-runner review preceded the single admitted build. This checkpoint supplies
platform build/package evidence only; private authenticated transport, complete original-request retention, native
caller admission, installed root/child lifecycle proof and protected service custody remain unfinished.
