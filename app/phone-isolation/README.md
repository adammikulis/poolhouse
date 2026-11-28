# Phone isolation prototype

**Not wired into the app.** Nothing in the Poolhouse app, the node, the app's build or a release calls or ships any
of this folder: it builds a separate APK of its own (`app.poolhouse.isolation`) by hand. The virtual machine work
stops here; see [Later](#later) for the faster route.

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
  runner now asks for it, with `mitigations=off` in the guest. The timed runs passed both by hand; the APK has not
  been built since they became the defaults.
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

## Later

**A native-speed layer in the isolated service.** The same isolated user id, with the guest's programs running on the
phone's processor and a user-space Linux layer answering their system calls, in place of an emulated processor. The
boundary does not change, so the layer is only compatibility: a fault in it, or code that leaves it, gains the
isolated process and nothing more. Not started.

| proven in the isolated service (`native/probe.c`, API 36 emulator) | what was not tried |
|---|---|
| writable and executable memory; code written then executed; a memfd mapped to execute | loading a whole ELF program, static or with its dynamic loader, from memory |
| tracing a child it forked (it stopped under tracing) | stopping the child at each system call, reading and changing its registers or memory |
| a seccomp filter of its own that traps a system call to its signal handler | serving that call, the trap's cost, signals and threads under it |
| making a user-notify listener | receiving or answering a notification, passing a descriptor through it |
| reading, writing and read-mapping a descriptor the app passed | a filesystem on such a descriptor, with folders, links and shared libraries |
| running a program shipped in the APK's native library folder; 48 children alive after 45 s | `execve` between guest programs; Android's limit on extra child processes over a longer run |

What shapes it: the isolated process may open none of the app's files, may not map a handed-over one for execution,
and may not `execve` one or a memfd. So the filesystem (folder operations included) and `execve` are emulated inside
the process, and program text is copied into memory it made itself, which separate processes may not share. It can
make no internet socket.

Two ways to build it:

- **gVisor** (Apache-2.0): most is reused (system calls, filesystems, network stack, a trap-based platform). The
  released `runsc` did not start here even as root, for want of `/proc/self/uid_map`: the emulator's kernel has no
  user or process-id namespaces, and its address space is 39 bits. Its engine would be started by a launcher of our
  own without that outer setup. Whether the engine runs with such an address space, or in the isolated service at
  all, is unknown. A [forum report](https://groups.google.com/g/gvisor-users/c/DyIXSbxNOKI) of building `runsc` on
  Android with a one-line address fix is unverified.
- **Our own layer**: a loader, a trap or trace handler, and a filesystem over a disk descriptor. More code of ours,
  and only what the node's tests need.

**First experiment**, which chooses between them: on an emulator, start gVisor's engine inside the isolated service,
with its root image passed as a descriptor, and run one program such as `uname`. Record the first thing that fails
and whether a patch of ours removes it. If it cannot be made to start, load one static program from a passed
descriptor under a trap filter, serve its file calls from memory and time a system call against a native one.

**Size**, a judgement and not a measurement: the first experiment is a few days of one agent's work. A route that
runs this repository's tests is weeks with gVisor, and considerably more with a layer of our own.

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
