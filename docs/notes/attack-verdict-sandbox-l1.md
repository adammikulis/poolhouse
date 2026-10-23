# Attack verdict: sandbox L1 (macOS and Linux) and harness denial L0

A non-author attack on branch `fix/sandbox-holes-and-harness-denial` at `f38cdce19` (base `c89058776`), run on 2026-10-23 against
the written list `docs/sandbox.md` "Attack first" (P1 to P9) and `docs/design-isolated-launcher-breakit.md` (N1 process listing, N2
hardware identifiers, N8 process count). The code was run, not read through: macOS on this Mac (macOS 26.6, Darwin 25.6.0, Apple
silicon, load average 30 to 45 from other agents), Linux in a throwaway Docker Desktop container (Ubuntu 24.04, bubblewrap 0.9.0,
user id 1000, `seccomp=unconfined`, `systempaths=unconfined`, the VM kernel has no Yama). Every probe ran under the branch's own
`poolhouse.sandbox.run` with the tip's `src/`, so the profile and the argv are the shipped ones. The machine's serial number,
hardware UUID, ECID and addresses were read outside the sandbox and searched for inside; no value is written here.

## Verdict

| Part | May it land | Why |
|---|---|---|
| **L1 as one branch** | **no** | On macOS, N1 (process listing) and N2 (hardware identifiers) are still open through channels the fix did not name (F1 to F4). `docs/sandbox.md` says a confined process "cannot read the arguments, environment, name, path or resource use of any other process", "cannot list processes" and cannot read the identity of the machine; each statement is false on this Mac. 61 tests pass because they probe only the channels the author found. |
| L1 Linux commits (`3c5988648`, `fbe36ea55`, `e7b264dc9`) | yes | Every network, namespace, capability and relay attack held (section 2). F7 to F11 are follow-ups; none opens a path out. `3c5988648` and `fbe36ea55` touch no macOS file and can land alone; `e7b264dc9` needs `Limits.processes` and `tests/procinfo_kit.py` from `f62476905`. |
| L0 (`e35215eb3`, `b4179e341`, `f55607a06`) | yes | The claim check is not touched and is still killed by tests after the change (section 3). |

L1 flips to yes when F1 to F4 are closed (a prototype of the diff, measured, is in section 4) and the tests of F13 exist and were seen
to fail on `f38cdce19`.

## 1 Findings, ranked

| # | Severity | Platform | Finding | Proposed change |
|---|---|---|---|---|
| F1 | **high** | macOS, all profiles | **Other processes are still listed by name, with their open files, listening ports and resource use.** `(allow process-info* (target self))` does not bind `process-info-codesignature`, `-pidfdinfo`, `-rusage`, `-dirtycontrol`, `-pidfileportinfo`, `-ledger`: the filter on the wildcard has no effect on them, and the fix denied only `-pidinfo` and `-listpids`. With `kill(pid, 0)` for existence (documented), `csops(pid, CS_OPS_IDENTITY)` names 826 of 908 processes (482 distinct signing identifiers; 217 processes carry one that is not Apple's: a debugger daemon, a cargo test binary, the poolhouse app); `proc_pidfdinfo(pid, fd, VNODEPATHINFO)` gives the paths other processes hold open (336 paths, 77 processes); `proc_pidfdinfo(.., SOCKETINFO)` gives their sockets: 40 local ports with 24 owner pids, the services `lsof -iTCP -sTCP:LISTEN` shows outside; `proc_pid_rusage` gives resource use and the Mach-O UUID of 713 processes. `CS_OPS_ENTITLEMENTS_BLOB` reads too. This is N1 rebuilt, plus the host's loopback services (the stated goal of the attacker). | Deny the six operations and allow each for `(target self)` (section 4). Measured: names 826 to 0, paths 77 to 0 processes, ports 40 to 0, rusage 713 to 0; a process still reads its own file descriptors and resource use (`psutil` `open_files`, `num_fds` work; `memory_full_info` is `AccessDenied` before and after). |
| F2 | **high** | macOS, all profiles | **The NVRAM is readable.** `nvram-get` is allowed under `(deny default)` (measured with a minimal `(version 1)(deny default)` profile, and with the shipped one). `/usr/sbin/nvram -p` prints all 23 variables: the Bluetooth controller address and the addresses and names of the devices it remembers (`BluetoothInfo`, `BluetoothUHEDevices`, `bluetoothInternalControllerInfo`), the boot volume entries, the installer data, the display configuration. The same data is the `options` entry of the IOKit registry (`IODTNVRAM`, `IODTNVRAMVariables`), which `(deny iokit-get-properties)` does not cover: in the plain profile it is the only class whose properties are readable. N2 names the serial number, the UUID and the provisioning identifier; a Bluetooth address is a hardware address of the same kind as the MAC address the brief lists. | `(deny nvram-get)`, `(deny nvram-set)`, `(deny nvram-delete)`. Measured: `nvram -p` 23 lines to 0 in every profile kind. |
| F3 | **high** | macOS, all profiles | **A hardware address of the machine is readable: `uuid.getnode()` returns the address of one network interface** (it equals an `ether` line of `ifconfig`). The channel is the `socket-ioctl` operation: libc's `uuid_generate_time` lists the interface addresses with `SIOCGIFCONF` on a datagram socket. `docs/sandbox.md` records it as "visible and not chased". It is open with `Net.deny()`. | `(deny socket-ioctl)`. Measured: the UUID's node field becomes a random value with the multicast bit set; a Unix `socketpair` with `settimeout`, and a TCP loopback connection in the loopback profile, still work. |
| F4 | **high** | macOS, GPU profile (the model server's) | **The GPU profile reads the whole registry except five property names.** Searching all 3244 entries for the machine's values (taken outside) finds, under the shipped GPU profile: the ECID (`ECID`, `die-id`, `unique-device-id`; the ECID is half of the provisioning UDID that N2 names), the interface addresses (`local-mac-address`, `device-mac-address`, `IOMACAddress`, `mac-address-ethernet0`), Bluetooth addresses (`BD_ADDR`, `BTAddress`, `mac-address-bluetooth0`), the NVMe serial number (the property `serial number` of the controller and of the block device, and `controller-unique-id`) and the NVRAM. The serial number and the hardware UUID are held (the two names denied are the right ones for those two). A deny list of names cannot be complete: the next macOS adds a property. `docs/sandbox.md` says the GPU profile "needs the rest of the registry"; it does not. | Invert it: `(deny iokit-get-properties)` and allow only `(iokit-registry-entry-class-prefix "IOGPU")` and `"AGX"`. Measured: a Metal device and a 1 MiB buffer are created (each prefix alone is enough); 0 identity hits in 3235 entries. Not run: a model server offloading every layer (`tests/test_sandbox_gpu.py` must run on the change). |
| F5 | **high** (cause of F1 to F4) | macOS | **`(deny default)` leaves 40 operation names open, and the fix closes them one named channel at a time.** `sandbox_check` run inside the plain profile for 158 operation names (133 are operations) answers "allowed" for 40 and "denied" for 93; the 40 are: `darwin-notification-post`, `dynamic-code-generation`, `file-clone`, `file-link`, `file-map-executable`, `file-test-existence`, `iokit-open-service`, `mach-bootstrap`, `mach-cross-domain-lookup`, `mach-derive-port`, `mach-task-special-port-get/set`, `managed-preference-read`, `necp-client-open`, `nvram-get/set/delete`, `process-codesigning`, `process-fork`, `process-info-codesignature/-dirtycontrol/-ledger/-pidfdinfo/-pidfileportinfo/-rusage/-setcontrol`, `socket-ioctl`, `socket-option-get/set`, `syscall-mach/-mig/-unix`, `system-fcntl`, `system-mac-syscall`, `system-necp-client-action`, `system-privilege`, `user-preference-read`. Some are meant (`process-fork`, `syscall-unix`, `file-map-executable`); F1 to F3 are in the rest. This is the second symptom fix in one area (process information, then the registry, then the identity of the machine), so it is a structure problem, not four missing lines. | Make the open set a reviewed table and enforce it by a test: section 5. |
| F6 | medium | macOS and Linux | **`Limits.processes` is on for no shipped policy and bounds neither threads nor `setsid` children on macOS.** `bash`, `mcp_server` and `model_server` set no allowance, so a fork bomb in the shell tool is stopped only by the wall clock. Where it is set it holds for `fork`, `posix_spawn`, `subprocess` and `vfork` + `exec`, and `setrlimit` cannot raise it (section 2). On macOS the rlimit does not count threads: 1500 threads started with an allowance of 40. A child that calls `setsid` (`posix_spawn` with `setsid=True`) leaves the group the wall clock kills; `docs/sandbox.md` names the second, not the first. | Say in the "Known limits" row that threads are unbounded on macOS. Set a default allowance in `bash` and `mcp_server` (the number is a product decision: a test run or a build spawns many processes). |
| F7 | medium | Linux | **The allowance counts processes; the kernel counts tasks.** `_ceiling` adds the allowance to the user's process count (`procscan`), and Linux `RLIMIT_NPROC` counts every thread. With a 300-thread program of the same user running (311 tasks, 10 processes), `Limits(processes=40)` makes the run fail before it starts: `bwrap: Creating new namespace failed: Resource temporarily unavailable` (the limit is set before `bwrap` makes its namespaces). A desktop user runs thousands of threads, so the option cannot be used on Linux. It fails closed. | On Linux count tasks (the `Threads` field of each `/proc/<pid>/status`) and add the few tasks `bwrap` makes itself. Test: a helper with 300 threads, then the allowance 40 forks 30 or more. |
| F8 | low | Linux | **A hard kill of the parent leaves the relay directory.** `/tmp/poolhouse-net-*` (mode 0700, the socket file inside) stays; the sandbox itself dies with the parent (`--die-with-parent`; only zombie `bwrap` entries remain, reaped by a real init). P8 says the directory is gone "after `finish`"; that holds only for an orderly end. | Sweep `poolhouse-net-*` directories of dead owners when a `Relay` is made (put the owner's pid in the name). |
| F9 | low | Linux | **`wrap` resolves `bwrap` a second time.** `available()` probes the binary it found; `wrap()` calls `shutil.which("bwrap")` again, so a different `bwrap` earlier on `PATH` at that moment runs unprobed (no `--disable-userns` check). It needs a writable `PATH` directory, which a confined process has only if the policy gives it one. | Keep the probed real path and use it in `wrap`. |
| F10 | low | Linux | **`Net.only` binds all of `sys.base_prefix` read-only** for the forwarder, beyond the policy's `read`. With `/usr` that adds nothing; with a pyenv, uv or conda interpreter it is the whole install (every installed package) readable by the command. Read from the code, not run on such an interpreter. | Bind the standard library directories the forwarder imports (`sysconfig` `stdlib`, `platstdlib`, `lib-dynload`), or ship the forwarder as a small static helper. |
| F11 | info | Linux | `/etc/hosts` and `/etc/resolv.conf` of the host are bound into every policy, `Net.deny` included, where nothing resolves; `/proc/cmdline`, `/proc/version`, `boot_id` and the kernel host name are readable. Not claimed closed. | Do not bind the two files when the policy has no network. |
| F12 | info | macOS | Still visible and not new: `kill(pid, 0)` (every pid, documented), `getpgid`, `getsid`, the host name, `id` (group names), `getpwuid` (the login name and the full name), `getattrlist(ATTR_VOL_UUID)` of `/`, `getpwall` (134 accounts), the class and entry names of the registry (`iokit-open-service` is open: `ioreg -l` prints the tree with empty properties), and, in a profile that is not `strict`, the distributed-notification and notification-center services. | Add the first four to the "Still visible" sentence; decide the rest in F5's table. |
| F13 | medium | tests | **The new tests probe the author's channels, so they cannot see F1 to F4.** `test_every_profile_closes_the_channels_to_other_processes_and_the_machine` pins the rule text. The behaviour tests probe `KERN_PROCARGS2`, `proc_pidinfo` flavors 3 and 4, `KERN_PROC_PID`, `proc_listpids(ALL)`, two registry properties, `gethostuuid`, `ioreg` and `system_profiler`. | Add the four probes of section 5 (the closure test, the named-process scan, the identity search, the UUID node against `ifconfig`) and watch each fail on `f38cdce19`. |

## 2 Held, each attempt

The same driver ran every command: `sandbox.run(argv, Policy(read=runtime_reads(), exec=(python, *SYSTEM_EXEC), net=..., gpu=...))`
on the tip's `src/`. The held rows ran in the plain kind (no network, no GPU); the nvram and UUID-node probes of F2 and F3 ran in all five kinds (plain, loopback, `Net.only(8080)`, strict, GPU), the registry search in plain and GPU, and the generated rule text of all five was read.

### macOS

| Attempt | Command or call | Result |
|---|---|---|
| arguments and environment of a sibling process that holds a marker, of the launcher, of pid 1 | `sysctl(CTL_KERN, KERN_PROCARGS2 = 49 / KERN_PROCARGS = 38, pid)`; `sysctlbyname("kern.procargs2.<pid>")` | **held**: refused for all three (unconfined: the sibling "holds the secret", the launcher returns 5724 bytes) |
| the same through `proc_pidinfo` | 22 flavors from `PROC_PIDTASKALLINFO` to `PROC_PIDREGIONPATH`, `proc_name`, `proc_pidpath` | **held**: all refused for the same three pids |
| list by `sysctl` | `kern.proc` `ALL`, `PID`, `PGRP`, `SESSION`, `TTY`, `UID`, `RUID`, `LCID` by number and by name; `KERN_PROC_PID` over pids 1 to 99960 | **held**: refused; 0 found (unconfined 968) |
| list by libproc | `proc_listpids` types 1 to 7, `proc_listallpids`, `proc_listpgrppids`, `proc_listchildpids(1)` | **held**: 0 (unconfined 3968 and 991) |
| task ports | `task_for_pid`, `task_name_for_pid` on the sibling and on pid 1 | **held**: `KERN_FAILURE` (unconfined: `task_name_for_pid` of the sibling returns a port) |
| tools | `ps -ax -o pid,ppid,user,command`, `top -l 1`, `pgrep -l`, `lsof`, `w`, `who`, `last`, `launchctl list`, `launchctl print system`, `lsmp`, `vmmap`, `sample`, `leaks`, `heap`, `spindump`, `netstat -an`, `ifconfig -a`, `arp -a`, `scutil --dns`, `networksetup`, `diskutil list`, `sysctl -a`, `dmesg`, `log show` | **held**: empty output or an `Operation not permitted` line |
| serial number and UUID | `gethostuuid`; `ioreg -c IOPlatformExpertDevice -d 2`; `system_profiler SPHardwareDataType`; `IORegistryEntryCreateCFProperty("IOPlatformUUID" / "IOPlatformSerialNumber")`; a search of every property of the registry in both profile kinds | **held**: `EPERM`, no value, 0 bytes from `system_profiler`; neither value occurs anywhere in the registry the profile can read (`ioreg -lw0` in the GPU profile mentions the name `IOPlatformUUID` once, inside a list of matching names) |
| raising the allowance | `setrlimit(RLIMIT_NPROC, hard + 100)` and `RLIM_INFINITY` from inside | **held**: `not allowed to raise maximum limit` |
| forks past the allowance (40) | `os.fork` loop 300; `os.posix_spawn` 300; `subprocess.Popen` 300; a C `vfork` + `execl` loop 400 | **held**: 42, 42, 36 and 21 started, then `EAGAIN` (the ceiling is the user's 595 processes plus 40, other processes of the user use some) |
| Metal under the shipped GPU profile | `MTLCreateSystemDefaultDevice()` and `newBufferWithLength:` in the GPU kind | **held** (works, as intended): a device and a buffer |
| a later rule overriding a deny | read of the generated text for the five kinds; `_gpu()` adds only `iokit-open` and `mach-lookup` | **held**: no later rule allows a denied operation |
| `uuid.getnode()` | see F3 | broken |

### Linux (Docker VM)

| Attempt | Command or call | Result |
|---|---|---|
| a host port the policy did not name | connect `127.0.0.1:<other>`, `127.0.0.1:<wildcard listener 0.0.0.0>`, `[::1]:<other>`, the LAN address | **held**: `ECONNREFUSED`, `ECONNREFUSED`, `ECONNREFUSED`, `ENETUNREACH`; the host saw 0 connections on those |
| the named port | connect `127.0.0.1:<named>` | works (`pong`), through the forwarder; through the socket file too |
| UDP, Unix sockets | `sendto` the host's UDP port; connect the host's abstract socket and its path socket | **held**: the host received no datagram; `ECONNREFUSED`, `ENOENT` |
| the relay directory | `unlink`, create, `mkdir`, `symlink`, `rename` in `/run/poolhouse/net`; connect a socket of an unnamed port | **held**: `EROFS` for all five; `ENOENT` |
| flood | 200 connections to the named port, and 35 more to the socket file | **held**: 32 stay open on each path, the host accepted 32 |
| nested namespaces | `unshare(CLONE_NEWUSER)`, `unshare --user --map-root-user id`, `unshare -r -m mount -t tmpfs`, `unshare(NEWNS/NEWNET/NEWPID)`, `setns` to its own and to pid 1's namespaces | **held**: `ENOSPC` for the user namespace, `EPERM` for mount, net and pid; `setns` gives `EINVAL` for its own user namespace and `EPERM` for the others of pid 1 |
| capabilities | `/proc/self/status` | **held**: `CapEff`, `CapPrm`, `CapBnd`, `CapAmb` all 0, `NoNewPrivs: 1`; the uid is 1000 |
| pid 1 (the `bwrap` init) | `ptrace(PTRACE_ATTACH)` on pid 1 and on the parent, `process_vm_readv`, `kill(1, SIGKILL)`, reading `/proc/1/{fd,root,environ,cmdline}` | attached (this VM has no Yama) and signalled, but **held**: pid 1 has `CapEff` 0 and `NoNewPrivs` 1, so there is nothing to gain; the run ended normally; `/proc/1/environ` is the policy's own environment |
| leaked descriptors | `/proc/self/fd`, `/proc/1/fd` | **held**: only the probe's own descriptors, two pipes and an `eventfd` |
| writes | `/usr`, `/proc/sys/kernel/hostname`, `/proc/sys/user/max_user_namespaces`; files in `/etc`, `/run/poolhouse`, `/tmp` | **held**: `EROFS` for `/usr`, `EACCES` for the two `/proc/sys` files; `/etc`, `/run/poolhouse` and `/tmp` accept files, but they are the sandbox's own tmpfs and end with it |
| the policy refusal | `Net.loopback()` with `Bubblewrap` | **held**: `PolicyError` before a process starts |
| the author's tests | `tests/test_sandbox_linux.py` in the same image | 14 passed |
| not attacked | a `bwrap` older than 0.8.0 (the author's test covers it), a kernel with AppArmor's user-namespace restriction, WSL | not run |

## 3 L0: does the harness-denial fix weaken the claim check

`git diff c89058776..f38cdce19` touches `board/session.py` (one added constant, `IDENTITY_ENV`), `tests/conftest.py` (the autouse
guard deletes those variables), two rig lines and two test files. `harnesshook.py`, `harness_claims.py`, `harness_session.py` and
`place.py` are unchanged: the claim check is the same code. The ten hook tests fail only in a shell that exports `POOLHOUSE_BOARD`
because `place.resolve` then returns that board and `credentials.load` finds no token for the rig's agent; without it `resolve`
asks the daemon, which a test state does not have, so `_authenticated` raises `BoardUnavailable` and `harnesshook.pre` warns and goes on (read from the code, not run). So the ten tests now run the
outage path, which is what their rig can reach, and the refusal path is covered by `tests/test_harness_ownership.py`, whose
`workspace_node` fixture sets `POOLHOUSE_BOARD` itself (`tests/node_kit.py`) after the autouse guard.

Mutations, run in this agent's shell (which exports `POOLHOUSE_BOARD`, the workspace agent and the session ids):

| Mutation | Result |
|---|---|
| none, tip | `tests/test_harness_ownership.py` (needs `--redteam`) 31 passed; the ten hook tests, the exec-chain files, the outage file and `test_test_identity_env.py`: 1526 passed |
| `harness_session._authenticated` raises `BoardUnavailable` instead of `Denied` for a missing token (the claim check weakened) | 2 failed: `test_a_session_without_a_token_of_its_own_reserves_nothing`, `test_the_hook_denies_a_session_with_no_token_file_and_does_not_wave_it_through_with_a_note` |
| the autouse deletion of `IDENTITY_ENV` removed | 11 failed, 1515 passed (the ten and `test_test_identity_env.py`) |

Verdict: **L0 may land.** Two low notes: `test_the_board_and_session_of_the_agent_that_runs_the_suite_do_not_reach_a_test` passes
trivially in a shell that exports nothing (CI), so only an agent's shell exercises it; and `IDENTITY_ENV` is kept by hand against a
second hand-kept list in the test, while other names that steer a hook or a command are read elsewhere (`POOLHOUSE_AGENT`,
`POOLHOUSE_WORKSPACE_DENYLIST`, `CLAUDE_CODE_SESSION_ATTENDED`). A scan test that lists every name in `src/` matching
`BOARD|SESSION|AGENT|TOKEN` and requires each to be cleared or named as kept would keep the list complete.

## 4 The prototype that closes F1 to F4

Applied to a scratch copy of `f38cdce19` (`src/poolhouse/sandbox/seatbelt.py` only), not committed. Measured with the same probes: names
826 to 0, ports 40 to 0, open-file paths 77 processes to 0, rusage 713 to 0, `nvram -p` 23 lines to 0, `uuid.getnode()` random,
0 identity hits in the GPU registry, Metal device and buffer created, own `psutil` data readable, loopback TCP works. The existing
sandbox tests: 65 passed, 7 failed: the five `test_every_profile_closes_...` cases, `test_the_gpu_rules_are_added_only_when_asked` (they pin the
old rule text) and one `test_sandbox_seatbelt.py` case that passed twice when run alone (the refusal log lagged under load).

```diff
+PROCESS_INFO_OTHERS = tuple(f"({verb} process-info-{op}{target})"
+                            for op in ("pidfdinfo", "rusage", "codesignature", "dirtycontrol", "pidfileportinfo", "ledger")
+                            for verb, target in (("deny", ""), ("allow", " (target self)")))
+GPU_REGISTRY_CLASSES = ("IOGPU", "AGX")
@@ def profile(...)
         "(deny process-info-listpids)",
+        *PROCESS_INFO_OTHERS,
         "(deny syscall-unix (syscall-number SYS_gethostuuid))",  # the hardware UUID
+        "(deny nvram-get)", "(deny nvram-set)", "(deny nvram-delete)",
+        "(deny socket-ioctl)",
@@ def _registry(policy)
-    return "(deny iokit-get-properties (iokit-property " + " ".join(quote(name) for name in IDENTITY_PROPERTIES) + "))"
+    return "(deny iokit-get-properties)\n(allow iokit-get-properties " + " ".join(
+        f"(iokit-registry-entry-class-prefix {quote(c)})" for c in GPU_REGISTRY_CLASSES) + ")"
```

`IDENTITY_PROPERTIES` is then unused. `process-info-setcontrol` stays open (it sets a thread's own name); `process-info-ledger` and
`-pidfileportinfo` are denied because `sandbox_check` reports them open, not because a leak through them was shown.

## 5 The structure: a closed set of operations, and the probes that prove it

The profile is `(deny default)` plus allows plus a growing list of named denies. A named deny is added after a leak is found, so the
next leak is whatever the list does not name. The target structure:

1. **One table** in `seatbelt.py`, `OPEN_OPERATIONS`, of the operations the profile leaves allowed, each with the reason a command
   needs it; the writer emits `(deny <op>)` for every operation in `ALL_OPERATIONS` (the names the compiler accepts, listed from the
   system's own profiles under `/System/Library/Sandbox/Profiles` plus the ones the compiler adds) that is not in the table, before
   the allows that carry a filter.
2. **A closure test** (macOS): a confined Python calls `sandbox_check(getpid(), op, 0)` from `libsandbox` for every name in
   `ALL_OPERATIONS` and asserts the allowed set equals `OPEN_OPERATIONS`. A new macOS that adds an operation, or a change to the
   writer that reopens one, fails on the owner's Mac. Measured to work: the call answers 0 (allowed), 1 (denied) or -1 (not an
   operation) inside the profile for 158 names, and its answer matched every behaviour above (`nvram-get` 0 and `nvram -p` works; `process-info-pidinfo`
   1 and `proc_pidinfo` refused).
3. **Three behaviour probes** in `tests/procinfo_kit.py`, each with a known target so the test needs no luck: (a) *named scan*: the
   confined Python scans pids with `kill(pid, 0)` and asks `csops(pid, CS_OPS_IDENTITY)`, `proc_pidfdinfo` (vnode path and socket) and
   `proc_pid_rusage`, against a sibling that holds a marker file open and listens on a known port; it must learn nothing about it
   (appendix A); (b) *identity search*: read the serial number, the hardware UUID, the ECID, the interface and Bluetooth addresses
   and the NVMe serial outside, search every property of the registry and `nvram -p` inside, for the plain and the GPU kind, expect
   no hit (appendix B); (c) *UUID node against `ifconfig`*: `uuid.getnode()` inside must differ from every `ether` line outside.
4. **Linux**: the thread-aware allowance (F7) with a 300-thread helper, and the stale relay directory (F8).

## 6 Not run and limits of this attack

- No model server was run under the changed GPU profile. `tests/test_sandbox_gpu.py` (a real `llama-server` offloading every layer) must
  run on the F4 change; only device and buffer creation was measured.
- Linux ran in a Docker Desktop VM: a kernel without Yama, no AppArmor user-namespace restriction, no WSL. The ptrace and `setns` results
  are therefore a floor, not a measurement of an Ubuntu host.
- `process-info-ledger`, `-pidfileportinfo` and `necp` were not shown to leak; `sandbox_check` reports them open.
- Pre-existing and out of the diff, not attacked: the notification services a non-strict profile allows (`com.apple.system.notification_center`,
  `com.apple.distributed_notifications@Uv3`) let a confined process receive and post notifications of the user's session.
- A thread bomb was run to 1500 threads only; the per-task limit is 16384 on this Mac.

## Appendix A: named scan and listening ports (macOS)

Run inside the profile as `python -c "$(cat file)" <sibling pid> <unused> <launcher pid> <top pid>`; the sibling is a process that
holds a marker and a socket.

```python
"""Full process inventory with names by csops after kill(pid,0): the N1 listing rebuilt from channels the fix left open. argv: target secret parent top"""
import ctypes, ctypes.util, json, os, sys, collections
top = int(sys.argv[4])
libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
libc.csops.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_void_p, ctypes.c_size_t]
me = os.getpid()
named = {}
alive = 0
for p in range(1, top):
    if p == me: continue
    try:
        os.kill(p, 0); alive += 1
    except PermissionError:
        alive += 1
    except ProcessLookupError:
        continue
    buf = ctypes.create_string_buffer(512)
    if libc.csops(p, 11, buf, len(buf)) == 0:
        named[p] = buf.raw[8:].split(b"\0")[0].decode(errors="replace")
print(json.dumps({"pids alive by kill(pid,0)": alive, "pids with signing identity readable": len(named),
                  "distinct identities": len(set(named.values())),
                  "top identities": collections.Counter(v for v in named.values() if v.startswith("com.apple.")).most_common(6),
                  "non-apple identity count": sum(1 for v in named.values() if not v.startswith("com.apple.")),
                  "sample non-apple (first 12 chars)": sorted({v[:12] for v in named.values() if not v.startswith("com.apple.")})[:8]}, indent=1))
```

```python
"""What libproc's per-fd and rusage calls tell about OTHER processes, in detail. argv: target secret parent top"""
import ctypes
import ctypes.util
import json
import os
import socket
import struct
import sys

target, ppid, top = int(sys.argv[1]), int(sys.argv[3]), int(sys.argv[4])
libproc = ctypes.CDLL(ctypes.util.find_library("proc"), use_errno=True)
me = os.getpid()
exist = []
for p in range(1, top):
    if p == me:
        continue
    try:
        os.kill(p, 0)
        exist.append(p)
    except PermissionError:
        exist.append(p)
    except ProcessLookupError:
        pass

out = {"pids existing (kill 0)": len(exist)}
interesting_paths = {}
listeners = {}
connections = 0
rusage_seen = 0
for p in exist:
    rb = ctypes.create_string_buffer(1024)
    if libproc.proc_pid_rusage(p, 4, rb) == 0:
        rusage_seen += 1
    for fd in range(0, 64):
        b = ctypes.create_string_buffer(4096)
        got = libproc.proc_pidfdinfo(p, fd, 2, b, len(b))
        if got > 0:
            raw = b.raw
            pos = raw.find(b"/", 24 + 152 - 8)
            end = raw.find(b"\0", pos)
            if pos > 0:
                path = raw[pos:end].decode(errors="replace")
                if path not in ("/dev/null", "/dev/ttys000") and not path.startswith("/dev/"):
                    interesting_paths.setdefault(p, []).append((fd, path))
        s = ctypes.create_string_buffer(2048)
        got = libproc.proc_pidfdinfo(p, fd, 3, s, len(s))
        if got > 0:
            raw = s.raw
            soi_type, soi_protocol, soi_family = struct.unpack_from("iii", raw, 176)
            kind = struct.unpack_from("i", raw, 256)[0]
            if soi_family in (socket.AF_INET, socket.AF_INET6) and kind in (1, 2):
                fport, lport = struct.unpack_from("ii", raw, 264)
                fport = socket.ntohs(fport & 0xFFFF)
                lport = socket.ntohs(lport & 0xFFFF)
                if lport and not fport:
                    listeners.setdefault(lport, set()).add(p)
                elif lport:
                    connections += 1
out["pids whose rusage is readable"] = rusage_seen
out["pids with non-/dev file paths readable"] = len(interesting_paths)
out["TCP/UDP local ports found with no remote end (listeners), with owning pids"] = {str(k): sorted(v) for k, v in sorted(listeners.items())}
out["connections seen"] = connections
flat = [(p, fd, path) for p, v in interesting_paths.items() for fd, path in v]
out["total open-file paths of other processes"] = len(flat)
out["sample paths"] = [f"{p}:{fd}:{path}" for p, fd, path in flat[:25]]
print(json.dumps(out, indent=1))
```

## Appendix B: identity search (macOS)

`mkneedles.py` runs outside and writes `needles.json`; `p_idsearch.py` runs inside as `python -c "$(cat p_idsearch.py)" "$(cat needles.json)"`
and prints only labels, property names and entry classes.

```python
"""Build the machine-identity needles from OUTSIDE the sandbox. Values are never printed; only labels and lengths."""
import json, re, subprocess, sys
def sh(*a):
    return subprocess.run(a, capture_output=True, text=True, timeout=60).stdout
needles = {}
hw = sh("/usr/sbin/system_profiler", "SPHardwareDataType")
m = re.search(r"Serial Number \(system\): (\S+)", hw); needles["serial"] = [m.group(1)] if m else []
m = re.search(r"Hardware UUID: (\S+)", hw); needles["hw-uuid"] = [m.group(1)] if m else []
m = re.search(r"Provisioning UDID: (\S+)", hw)
if m:
    chip, ecid = m.group(1).split("-")
    needles["udid-ecid"] = [ecid]
nv = sh("/usr/sbin/system_profiler", "SPNVMeDataType")
needles["nvme-serial"] = list(dict.fromkeys(re.findall(r"Serial Number: (\S+)", nv)))
bt = sh("/usr/sbin/system_profiler", "SPBluetoothDataType")
needles["bluetooth-addr"] = list(dict.fromkeys(re.findall(r"Address: ([0-9A-Fa-f:]{17})", bt)))
net = sh("/sbin/ifconfig", "-a")
macs = re.findall(r"ether ([0-9a-f:]{17})", net)
needles["interface-mac"] = list(dict.fromkeys(macs))
for sec in ("SPAirPortDataType", "SPEthernetDataType"):
    needles["interface-mac"] += [x for x in re.findall(r"MAC Address: ([0-9a-fA-F:]{17})", sh("/usr/sbin/system_profiler", sec)) if x not in needles["interface-mac"]]
json.dump(needles, open(sys.argv[1], "w"))
print({k: [len(x) for x in v] for k, v in needles.items()})
```

```python
import ctypes, ctypes.util, json, plistlib, sys
needles = json.loads(sys.argv[1])
iokit = ctypes.CDLL(ctypes.util.find_library("IOKit")); cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
V = ctypes.c_void_p
iokit.IORegistryCreateIterator.argtypes = [ctypes.c_uint, ctypes.c_char_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]
iokit.IOIteratorNext.argtypes = [ctypes.c_uint]; iokit.IOIteratorNext.restype = ctypes.c_uint
iokit.IORegistryEntryCreateCFProperties.argtypes = [ctypes.c_uint, ctypes.POINTER(V), V, ctypes.c_uint]
iokit.IOObjectGetClass.argtypes = [ctypes.c_uint, ctypes.c_char_p]
iokit.IORegistryEntryGetName.argtypes = [ctypes.c_uint, ctypes.c_char_p]
iokit.IOObjectRelease.argtypes = [ctypes.c_uint]
cf.CFPropertyListCreateData.argtypes = [V, V, ctypes.c_long, ctypes.c_ulong, V]; cf.CFPropertyListCreateData.restype = V
cf.CFDataGetLength.argtypes = [V]; cf.CFDataGetLength.restype = ctypes.c_long
cf.CFDataGetBytePtr.argtypes = [V]; cf.CFDataGetBytePtr.restype = ctypes.c_void_p
forms = []  # (label, needle bytes, ascii?)
for label, vals in needles.items():
    for v in vals:
        forms.append((label, v.lower().encode(), True))
        if label in ("interface-mac", "bluetooth-addr"):
            forms.append((label, bytes.fromhex(v.replace(":", "")), False))
        if label == "hw-uuid":
            forms.append((label, bytes.fromhex(v.replace("-", "")), False))
        if label == "udid-ecid":
            raw = bytes.fromhex(v)
            forms += [(label, raw, False), (label, raw[::-1], False)]
def scan(obj, hits, key):
    if isinstance(obj, dict):
        for k, v in obj.items():
            scan(v, hits, str(k) if not key else key + "." + str(k))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            scan(v, hits, key)
    elif isinstance(obj, (bytes, bytearray)):
        raw = bytes(obj); low = raw.lower()
        for label, f, ascii_ in forms:
            if f in (low if ascii_ else raw):
                hits.add((label, key, "data"))
    elif isinstance(obj, str):
        low = obj.lower().encode()
        for label, f, ascii_ in forms:
            if ascii_ and f in low:
                hits.add((label, key, "string"))
hits = set(); where = {}
entries = 0
for plane in (b"IOService", b"IODeviceTree"):
    it = ctypes.c_uint()
    iokit.IORegistryCreateIterator(0, plane, 1, ctypes.byref(it))
    while True:
        e = iokit.IOIteratorNext(it)
        if not e: break
        entries += 1
        d = V()
        if iokit.IORegistryEntryCreateCFProperties(e, ctypes.byref(d), None, 0) == 0 and d.value:
            data = cf.CFPropertyListCreateData(None, d, 200, 0, None)
            if data:
                raw = ctypes.string_at(cf.CFDataGetBytePtr(data), cf.CFDataGetLength(data))
                try:
                    props = plistlib.loads(raw)
                except Exception:
                    continue
                local = set(); scan(props, local, "")
                if local:
                    cls = ctypes.create_string_buffer(128); nm = ctypes.create_string_buffer(128)
                    iokit.IOObjectGetClass(e, cls); iokit.IORegistryEntryGetName(e, nm)
                    for h in local:
                        where.setdefault(h, set()).add(f"{plane.decode()}:{cls.value.decode()}:{nm.value.decode()}")
        iokit.IOObjectRelease(e)
print(json.dumps({"entries": entries, "hits": [[l, k, t, sorted(w)[:3], len(w)] for (l, k, t), w in sorted(where.items())]}, indent=0))
```
