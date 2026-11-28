# Phone isolation prototype

One APK in which untrusted code runs under another Android user id than the app: an `isolatedProcess` service runs
a virtual machine (QEMU, TCG, no hardware virtualization), and the app hands it every disk and one channel as open
descriptors. The guest has no network device. This is a prototype of the boundary, not a phone worker: it was run
on an API 36 emulator only.

## What is a boundary here

| layer | a security boundary against code under test? |
|---|---|
| Android's `isolated_app` sandbox (own user id, SELinux domain, seccomp, no permissions) | **yes**: the only one relied on |
| QEMU | **no**: its [security policy](https://www.qemu.org/docs/master/system/security.html) says TCG users must not rely on it to isolate a guest. An escape from it lands in the isolated process |
| the guest kernel and users in the guest | no, towards the phone; they separate processes inside one machine |
| a translation layer (proot, a loader, a system-call shim) in the app's own user id | **no**: whatever it runs can leave it and is then the app |
| the app's channel parser | the attack surface: the isolated side is assumed hostile |

## The shape this argues for

| piece | where it runs | what it holds |
|---|---|---|
| the node, the interface, keys, the network, the model server | the app's user id | everything secret; it never runs pool-supplied code |
| one machine per job (`bindIsolatedService`, one instance each) | its own isolated user id | the job's tree, the read-only system image, a work disk wiped afterwards |
| the channel (a virtio serial port over a socket pair the app made) | between the two | the job in (`spec.json`, `tree.tgz`), `result.json` out, size capped; model requests relayed with a quota |

The guest never gets a credential, a network device or a path of the app. The app never mounts or reads a guest disk,
parses only the channel, and ends a job by letting go of the service. Only the boundary above is proven here; the
node, per-job instances, the relay and a pinned image download are not built.

Rejected: the app's own user id (nothing separates hostile code from the keys, and running built files there needs
`targetSdkVersion` 28); the Android Virtualization Framework (on the target chip only protected Microdroid machines,
whose permission an ordinary install cannot hold, with no network and no execution of what the payload writes);
WebAssembly (the tests start programs and compile with native toolchains); the SDK sandbox and Private Compute Core
(not offered to an app for its own code).

## Build and run

Every output goes to a folder outside the checkout.

```
python app/phone-isolation/engine.py build --cache CACHE --out OUT/engine
python app/phone-isolation/guest.py build --cache CACHE --out OUT/guest --extra SOURCE.tar:opt/poolhouse \
    --extra lua-5.4.7.tar.gz:opt/bench
python app/phone-isolation/build.py --out OUT --engine OUT/engine --guest OUT/guest/apk
adb -s emulator-NNNN install --no-incremental -r OUT/apk/isolation.apk
adb -s emulator-NNNN shell am start -n app.poolhouse.isolation/.ProbeActivity --es task probe
```

For the machine, put `OUT/guest/system.img` and a script (`guest/bench.sh` times the workloads) in the app's folder
as `files/vm/system.img` and `files/vm/task.sh` (`adb push`, then `run-as app.poolhouse.isolation cp`), and start the
activity with `--es task machine --ei cpus 4 --ei memory 2560`. Reports are `files/probe-isolated.txt`,
`files/probe-app.txt`, `files/console.txt` and `files/result.txt`; each line starts with the milliseconds since it was
opened. `engine.py lock` and `guest.py lock` rewrite the pins.

## Measured: what each user id can do

API 36 emulator (`google_apis` arm64, userdebug, SELinux enforcing, kernel 6.6), targetSdk 36, 2026-11-27. The same
program was run by the app's process and by the isolated service.

| attempt | app (`untrusted_app`) | isolated (`isolated_app`, uid 99001) |
|---|---|---|
| run a program from the APK's native library folder | yes | yes |
| make code at run time (write then execute, memfd mapping) | yes | yes |
| `execve` a file it wrote, a memfd, or an app file by descriptor | no | no |
| map an app data file for execution | yes (from a descriptor) | no |
| trace its own child, install its own seccomp filter | yes | yes |
| make an internet, netlink or vsock socket | internet and netlink yes | no |
| reach the app's loopback listener or abstract socket | yes | no |
| open the app's files, preferences or Keystore | yes | no |
| content providers, broadcasts, starting services or activities | yes | refused |
| read, signal or trace the app's process | n/a | no |
| see other processes | its own only | every process id is listed; none of their `stat`, `status`, `cmdline`, `environ`, `maps` or `fd` can be read |
| use a descriptor handed over | yes | read, write, map; not reopen, not execute |

Not measured: an app target of 37, an API 37 image (none is installed and none was downloaded), any phone.

## Measured: speed

`guest/bench.sh` is run by the machine in the isolated service and by `guest/native.sh`, which runs the same files
under `chroot` on the same emulator. Seconds; the factor is against the native time of the same period.

**The computer was heavily loaded by other work throughout (load average 37 to 82 on 16 cores)**, and the emulator
is itself a virtual machine, where the machine's own thread wake-ups are costly. Native times alone moved by a factor
of two. Read these as a size, not a figure: repeat them on an idle computer and on a phone.

| workload | native | `-cpu max`, 4 | `-cpu max`, 1 | `cortex-a72`, `mitigations=off`, 1 | same, 4 |
|---|---|---|---|---|---|
| Python arithmetic (one thread) | 1.0 to 2.2 | 82 to 204 (49x to 93x) | | | 35 (36x) |
| start 300 programs | 0.2 to 0.8 | 7.8 to 17.9 (about 40x) | 9.6 | 5.2 (7x) | |
| write and read 3000 files | 0.3 to 0.4 | 23.0 (52x) | | | |
| `gcc -O2` of one large C file (`lvm.c`) | 1.2 to 2.3 | 127 (94x) | 80 | 41 (18x) | 48 (39x) |
| build Lua, `make -j3` | 3.0 to 6.8 | 510 (about 100x) | | over 290, stopped | 375 (123x) |
| optimized `rustc` build of one generated file | 16 to 40 | not measured | | | |

- The processor the guest sees decides much of it: `cortex-a72` was two to six times faster here than `max`. The
  runner uses it, with `mitigations=off` in the guest.
- A parallel build gained little from the machine's four processors here.
- Letting go of the service ends the job: Android killed the runner ("isolated not needed") and the engine with it
  within two seconds, in the middle of a build.
- gVisor's released `runsc`, a possible native-speed route, did not start on this kernel even as root: it reads
  `/proc/self/uid_map`, and the kernel has no user namespaces (nor process-id namespaces).
- First start installs the system onto the work disk once (about 80 to 100 s); later starts reach the task in about
  5 to 10 s.
- Not measured: any phone, battery, heat, a Rust build or the repository's tests inside the machine (the guest lacks
  `keyring`, so its tests did not run natively either).
- At these factors a test shard that takes minutes on a computer takes hours here. Short commands are usable.

## Limits and open work

- QEMU and its libraries here are prebuilt Termux packages (QEMU GPL-2.0, GLib LGPL-2.1, others per package): 70
  files, 69 MB unpacked, with devices the machine never uses. A product builds QEMU from source with `virt`,
  `virtio-blk`, `virtio-serial` and TCG only, without user networking, and ships its licences and source offer.
- The whole guest system is one tar the app is given; a product downloads it pinned by digest and keeps one read-only
  image for all jobs.
- One machine per isolated service instance. `bindIsolatedService` gives each instance its own user id, which keeps
  jobs apart; the prototype binds one.
- A compromised isolated process still has the kernel's system calls an app has, the few system services isolated apps
  may find, the process ids of the device, its own disks, and can spend processor, memory, battery and its disk file.
- Android 17 limits each app process's memory by the device's RAM; the guest's memory counts. The limit on the target
  phone is unread (`adb shell am memory-limiter status`).
- Everything is in memory and files of this app: uninstalling removes it.
