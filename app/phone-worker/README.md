# The phone worker

An Android app that makes a phone a pool device which builds and tests: in a pool it takes a test shard
(`scripts/test ... --on phone [--commit SHA]`, [test farm](../../docs/test-farm.md)), runs it on its own working
copy and returns the result. It is off until switched on, and it is a separate app from the one a person approves
from: its own Android user, so no approval key and no data of that app.

**State on 2026-11-27: built and checked on a computer only. Nothing here has run on a phone**: the debug APK was
installed once and never started, and the memory, time, heat and battery a build or a test run takes there are
unmeasured. Treat every statement below about behaviour on a phone as the design.

## With no computer

The APK carries the source of the commit it was built from. Switched on, the phone downloads its Linux files and
tools, makes its own working copy from that source and builds its node there; with no `/work/node.json` the node is
in no pool and listens on no network. It needs the internet for the first start and no computer.

| works with no pool | only once the phone is in a pool |
|---|---|
| the node and its own boards, its own working copy and builds | taking test runs from other devices, verifying another agent's commit |
| new source only by installing a newer app | the shared board and other devices' compute |

Not built: founding or joining a pool from the app (the node's `pool_found` and `join_pool` need acts and grants
signed by an admin key the app would hold), and a way for the app to hand the phone its own work. Today only the
debug build's `.JOB` runs something with no pool.

## The app's side

| what | how |
|---|---|
| switch | `startForegroundService` of `WorkerService` with `app.poolhouse.worker.ENABLE` or `.DISABLE` |
| state | query `content://<package>.state/status`: `enabled` (0 or 1), `state` (words), `device` (fingerprint), `pool` (empty: none); the URI is notified on change |
| permission for both | `<package>.CONTROL`, signature: both apps are signed with one key; the caller also lists the package under `<queries>` |

It is not a library module of the Poolhouse app: code under test runs as the app's Android user, so inside that app
it could read the app's stored tokens and use its keys that ask for no fingerprint, and the older target version
this build needs would apply to the whole app.

## What runs

| layer | what it is |
|---|---|
| the app | `app.poolhouse.worker` (debug: `.nodetest`), a foreground service with a wake lock while it is on |
| the guest | Debian trixie arm64 (Python 3.13), pinned by layer digest, under `proot` in the app's private folder |
| tools | git, gcc, Rust 1.94.0 and the test dependencies, installed once by `guest/setup.sh` |
| the node | `poolhouse-node` for Linux aarch64, built on the phone from the phone's working copy (`/work/repo`, made by `guest/prepare.sh`) |

A pool run takes the ordinary shard path: the node checks consent and the tree's digest, takes CPU slots from its
lease service and runs `scripts/test` in a scratch checkout. At most 3 build jobs and test workers
(`guest/env.sh`).

## Why not a virtual machine

Read from a Galaxy S26 Ultra (Snapdragon 8 Elite Gen 5, SM8850, Android 17) on 2026-11-27:

- `/apex/com.android.virt/bin/vm info` says `Only protected VMs are supported.` (hypervisor gunyah, OS list
  `microdroid`), and `ro.boot.hypervisor.vm.supported` is empty.
- The built-in Linux Terminal needs a non-protected VM, so it cannot run on this chip
  ([Android Authority](https://www.androidauthority.com/galaxy-s26-linux-terminal-3645822/), 2026-03-02); it has
  no launchable activity on this phone. Exynos S26 models differ.
- An app of our own could ask for a protected Microdroid VM
  ([framework README](https://android.googlesource.com/platform/packages/modules/Virtualization/+/refs/heads/main/libs/framework-virtualization/README.md)),
  with `MANAGE_VIRTUAL_MACHINE`: `signature|development|preinstalled` on this phone, so for a sideloaded app only a
  development grant over adb. Microdroid is headless bionic with the payload loaded from the APK, no network (the
  payload may only listen on vsock) and no execute on its writable storage
  ([payload policy](https://android.googlesource.com/platform/system/sepolicy/+/refs/heads/main/microdroid/system/private/microdroid_payload.te)),
  so it cannot build a program and then run it.

## Build, install, drive

```
python app/phone-worker/build.py --out /somewhere/outside/the/checkout [--keystore FILE --alias NAME] [--commit SHA]
adb -s SERIAL install --user 0 /somewhere/.../debug/poolhouse-worker.apk
adb -s SERIAL shell am start-foreground-service -n app.poolhouse.worker.nodetest/app.poolhouse.worker.DebugWorkerService \
    -a app.poolhouse.worker.ENABLE
```

`.DISABLE` turns it off, as does the notification's button. The first start downloads about 30 MB of Debian and
then Python, the compilers and Rust. The node listens only where `/work/node.json` says
(`{"listen": "ADDRESS:PORT"}`). A debug build also takes `.JOB --es name NAME`, which runs `/work/jobs/NAME.sh`;
only the adb shell may send it.

## Security

- **The boundary is the Android app sandbox**, not proot: code under test can leave proot and is then this app. As
  this app it has the network (including the phone's own loopback), its own folder, and nothing else of the
  owner's: no storage permission, no other app's data, no Keystore key of another app.
- **The node's key and the code it tests share one user**, as on computers: a hostile tree can read the node's
  device key and act as this phone in the pool until the phone is removed from it. A pool takes tests only from the
  devices the phone's node allows (`shard_consent`). Splitting the node and the runner into two apps is the way to
  close this and is not built.
- `targetSdkVersion 28` is what lets the phone execute what it built; it also means older platform rules apply to
  this app (for example the local network is reachable without a prompt).
- **What is copied**: the shard's tree (tracked files under the source folders, no hidden files) into a scratch
  folder that is removed when the run ends; the phone's own working copy and build caches stay. All of it is in the
  app's private folder, excluded from backups. Uninstalling the app removes everything, keys included; also remove
  the device from the pool.
- Downloads are pinned: the Debian layer and the Termux `proot`, `libtalloc` and `libandroid-shmem` packages by
  SHA-256, `rustup-init` by SHA-256; apt and pip use their own signed or HTTPS indexes.

## Limits

- No GPU or NPU for tests: nothing in the guest reaches them. Models come from the Poolhouse app over the phone's
  loopback; this app does not yet read that app's endpoint.
- Memory: the phone has about 10.8 GiB and the app's model holds several of them while loaded. Whether a build or a
  test run fits beside it is unmeasured, and nothing yet makes the two take turns.
- Android ends an app's child processes beyond 32 on the whole device ("phantom processes"), which a wide test run
  can pass; switching that off is a developer setting only the phone's owner may change.
- A tree's tests that need macOS, Windows, a browser, a container, `sandbox-exec` or a real model do not run here.
- Linux aarch64 support is needed again for this device, inside the guest.
