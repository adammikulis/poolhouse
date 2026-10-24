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
set -e
git checkout --detach 6aaac694803004a63c6f393d153c96d22521a6b9
for patch in 0001-harden-producer-entrypoints.patch \
             0002-retain-native-session-context.patch \
             0003-retain-original-native-occurrences.patch; do
  git apply --check "/path/to/poolhouse/patches/codex/$patch"
  git apply "/path/to/poolhouse/patches/codex/$patch"
done
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


The third patch retains an original occurrence across preview, handler fanout,
retries, command/MCP dispatch and legacy notification. Stop's two dispatcher calls
share that same occurrence. A process-local launch UUID and checked global serial
cover all actors and native lifecycle/invocation kinds. Capture is consumed once
when binding its immutable request. Ownership, kind, fork, exhaustion and entropy
failures refuse dispatch; an unsupported failure surface terminates the producer
rather than skipping an enforced handler. The legacy payload's root-shared session
identifier is separate from the actual emitting actor in retained native context.

Birth versus resume comes from actual native identity selection. Forked history
can carry a legacy resume label while allocating a new actor, so the label does
not determine native lifecycle origin. Persisted context still grants no admission.

Current three-patch source validation is eleven focused occurrence tests and a
Core library compilation check. Both serial-reuse and retained-owner mutations
failed, followed by exact restoration and eleven passing tests. A fresh isolated
index independently reproduced source tree
`70dbe07a9dae5c328fea58dc917bb9cbdcb5bd6f`. Earlier CLI/app-server checks in the
manifest retain their original patch scope; they are not current three-patch
binary or installation proof. Native transport, measured launch, Node proof
issuance, platform confinement and installed H evidence remain pending.
