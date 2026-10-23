# Native hook producer source

`source.json` pins the maintained Codex source and patch bytes. The patch invokes
Codex’s existing process hardening constructor in both the CLI entry point
(`codex`, including its app-server command) and standalone `codex-app-server`.
A second patch retains immutable actor, root and direct-parent identifiers from
native Session construction through hook cloning and reconfiguration. It exports
no context through JSON or environment variables. Resumed root/parent metadata is
context data; it grants no authority without independent node validation.

The lock records the release source’s 0.164.0 workspace version and the two added
workspace dependencies; third-party resolutions and checksums are unchanged.

Apply to a fresh checkout of the pinned revision:

```sh
git checkout --detach 6aaac694803004a63c6f393d153c96d22521a6b9
git apply --check /path/to/poolhouse/patches/codex/0001-harden-producer-entrypoints.patch \
  /path/to/poolhouse/patches/codex/0002-retain-native-session-context.patch
git apply /path/to/poolhouse/patches/codex/0001-harden-producer-entrypoints.patch \
  /path/to/poolhouse/patches/codex/0002-retain-native-session-context.patch
cargo +1.99.1 check --locked --manifest-path codex-rs/Cargo.toml \
  -p codex-cli --bin codex -p codex-app-server --bin codex-app-server
```

Build packages with the maintained `just assemble-codex-package` entry point;
its instructions are in `scripts/codex_package/README.md` in the pinned source.
Build both variants and retain the exact source revision, patch digest, compiler,
build command, package manifest and resulting artifact digests. Static musl is
required for the Linux loader boundary unless a separately reviewed protected
launcher supplies that boundary. A GNU development build is compile evidence only.
Do not identify a patched binary by its version string alone.

On Linux the existing helper disables dumping, sets both core limits to zero,
and exits on either syscall failure. On macOS it applies the existing attach and
core restrictions. Its Windows implementation performs no hardening. Constructor
ordering relative to other constructors is unspecified; clearing loader variables
in a constructor occurs after the dynamic loader runs.

This is a producer prerequisite, not authenticated hook context or installed H
proof. Protected measured launch, private node admission, immutable native actor,
root and direct-parent context, one-consumer completion receipts, supported hook
installation, and actual root and nested child turns remain required. A package
must pass the installed memory/core and syscall-refusal probes before admission;
no source manifest or version string grants trust.
