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

See the table in the pull request or commit message; `guest/bench.sh` reproduces it. The native column is the same
system under `chroot` on the same emulator, so the ratio is the cost of the machine; absolute times on a phone are
unmeasured.

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
