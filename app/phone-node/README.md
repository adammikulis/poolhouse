# Node mode for the Android app

The phone as a pool device inside the one Android app: the same `poolhouse-node` every device runs, cross-built for
Android, started by `NodeService` while node mode is on. It is not yet part of the app (`app/android`), and it runs
no code another device sends.

## What the app takes from here

| piece | how |
|---|---|
| `AndroidManifest.xml` | merged into the app: its permissions, `extractNativeLibs`, one service that nothing outside the app can reach |
| `src/app/poolhouse/node/` | `NodeService.start(context)`, `.stop(context)`, `.enabled(context)`; the notification turns it off too |
| `libpoolhouse_node.so` | `python app/phone-node/build.py --out DIR` writes `DIR/jniLibs/arm64-v8a/`, which the app packs |

The app keeps its own target version: a current one (checked with 37) runs the node, because the program is a
native library of the app. State is `files/node`. The node listens only when `files/node-listen` names an address.

## Where isolated execution plugs in

A device takes a test run as a job folder: `spec.json` (id, tree digest, tier, test files, time limit) and
`tree.tgz`; it answers with `result.json` and an exit status, and a cancel ends the process
(`app/poolhouse-node/src/shard/host.rs`, `launch`). Today the node starts this device's Python 3.13 on that folder.
A phone has none, so it answers `shard_caps` with why it takes no tests. An isolated runner replaces what `launch`
starts, and nothing else of the node.

## What has been shown

On an Android 16 emulator (API 36, SELinux enforcing, arm64, the app at target 37) on 2026-11-27, with a throwaway
pool whose other node ran on the computer: the node starts from the app, is admitted and joins by code, both see
each other as members, board messages pass both ways within a second, `shard_caps` answers (platform `android`, no
tests taken), the process comes back by itself after being killed with its membership, and switching off leaves no
process and no listener. It used 9 MB of memory at rest. A task made on one member was not listed on the other,
which is the same between two computer nodes. **Nothing has run on a physical phone**: Doze, battery, heat, a
vendor's process killer and joining over Wi-Fi are unchecked.

## What Android changed in the node

- An app may make no hard link, so a file that must be published once and never replaced is renamed without
  replacing (`sys::publish_new`).
- Android is not `linux` to the compiler: process start times were never read there, so no caller had a lineage and
  every grant was refused as spent from an ended process (`lease::live::inspect`). The node now also keeps its
  memory from other processes of its user there, as on Linux.

## Not built

Founding or joining a pool from the app (the app calls the node's local socket; `join_pool` needs a grant an admin
key signed), a state view, the network address to listen on, serving the app's model to the pool, and running tests.

## Checking it on an emulator

`build.py --test-apk` writes `app.poolhouse.node.nodetest`, a debuggable app that only the `adb` shell can start:

```
adb -s emulator-5554 install DIR/test-apk/poolhouse-node-test.apk
adb -s emulator-5554 shell am start-foreground-service -n app.poolhouse.node.nodetest/app.poolhouse.node.NodeService -a app.poolhouse.node.START
```
