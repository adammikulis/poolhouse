# Sandboxing and egress control for tool execution

Status: accepted 2026-10-02 (issue #27). One package, `poolhouse.sandbox`, runs a command under an
operating-system sandbox with a deny-by-default policy. Every place Poolhouse starts an
agent-chosen command or an untrusted tool goes through it.

## Decision

A `Policy` names everything a process may touch. Anything not named is refused.

| Part | What it grants |
|---|---|
| `read` | trees it may read (plus the system trees every process reads: linker, frameworks, zoneinfo) |
| `write` | trees it may read and write |
| `exec` | the programs it may start; the command itself is added |
| `net` | `Net.deny()`, `Net.loopback()` (macOS only: Linux has no way to give the host's loopback without the host's network), or `Net.only(*ports)` (those ports of the host's loopback) |
| `env` | the whole environment; nothing is inherited |
| `limits` | wall clock (kills the process group), CPU seconds, file size, open files, output bytes, processes the run may add |
| `gpu` | what Metal needs (below) and nothing else |

Every path is validated before it reaches a profile: absolute, existing, no control character,
no `..`, `.`, empty or trailing-slash segment, and no symlink on the way (`name the resolved
path`). The profile writer quotes each path as a string literal (quote and backslash escaped,
control characters refused), so a directory called `x") (allow file-read* (regex #".*"))`
is a directory, not a rule.

Backends sit behind one `Backend` protocol (`available`, `wrap`, `denials`):

| Backend | Where | State |
|---|---|---|
| `seatbelt` | macOS, `sandbox-exec -p <profile> -- argv` | implemented, tested against the real tool |
| `bubblewrap` | Linux, `bwrap --unshare-all --unshare-user --disable-userns --cap-drop ALL --die-with-parent --new-session --clearenv ...` | read grants, network denial, the relay for named ports, nested namespaces, capabilities and the process allowance tested on Ubuntu 24.04 (bubblewrap 0.9.0); model relay and GPU discovery tested on WSL |
| `container` | any | stub that raises `NotImplementedError`; plan below |
| none | | `run` raises `SandboxUnavailable` and does not start the command |

Untrusted execution fails closed. Running without a sandbox takes an explicit
`AllowUnsandboxed(reason)` argument: the reason is logged at warning level and sent as a
`sandbox.unsandboxed` event with every run. There is no environment variable or global switch.

## The exception: `sandbox-exec`

The repository bans deprecated tools. `sandbox-exec` (Seatbelt) is deprecated by Apple and is
used here anyway.

- **Date and decision:** 2026-10-02, the owner, recorded on issue #27: on macOS it is the best
  available command-line sandbox.
- **Scope:** every use of the tool is in `src/poolhouse/sandbox/seatbelt.py`. Nothing else names
  it. Claude Code's own Bash sandbox (enabled by `poolhouse.harness`) is Anthropic's code and uses
  the same tool underneath.
- **Mitigations:** it logs one warning per process saying the tool is deprecated;
  `poolhouse security sandbox status` and `poolhouse security status` print it; the binary's
  existence is checked before each use; untrusted execution is refused when it is missing.
- **Replacement path:** a container backend (below) for CPU-only work, behind the same
  `Backend` protocol, so the change is one file.
- **GPU workloads have no non-deprecated replacement.** Containers and virtual machines on macOS
  cannot use Metal. A model server on the GPU can only be confined with Seatbelt today. This stays
  true until Apple ships GPU access for containers; nothing in this repository can change that.

## Evaluated alternatives

Measured on this Mac (macOS 26.6, Apple silicon) on 2026-10-02.

| Option | Version, licence, last release | Enforces | Platforms | Cost | Result here |
|---|---|---|---|---|---|
| Anthropic `sandbox-runtime` (`srt`) | 0.0.78, Apache-2.0, 2026-09-30; repo pushed 2026-10-02 | Seatbelt (macOS) or bubblewrap (Linux) from a JSON settings file; a domain-filtering HTTP/SOCKS proxy for network | macOS, Linux, Windows | Node 26 and 4 npm dependencies (16 MB installed); pre-1.0 | ran: `echo` in 0.25 s; a denied read and a write outside the allow-list were refused; **reads are allowed everywhere unless denied** (a home directory listing worked); **environment inherited**; proxy variables injected; no exec allow-list; no GPU rule |
| Apple `container` | 1.5.0, Apache-2.0, 2026-09-29 | one lightweight Linux VM per container | macOS 26, Apple silicon | 431 MB Homebrew bottle, a launchd service, a kernel and image downloads | installed and `--version` answered; `container system start` was refused by the permission system in this session, so startup latency, mounts and tool behaviour were **not measured**. No Metal in the guest |
| Lima 2.2.0 (Apache-2.0), Colima 0.10.3 (MIT), Podman 6.1.3 (Apache-2.0 and GPL-3.0+) | Homebrew formulae | a full Linux VM and a container runtime | macOS, Linux | a VM image, a daemon | not installed or run; same Metal limit; heavier than Apple `container` for the same result |
| bubblewrap 0.13.0 | LGPL-2.0+ | user, mount, pid, net namespaces | Linux | one setuid-free binary | not runnable on this Mac; the argv is tested only |
| nsjail | Apache-2.0 | namespaces, seccomp, rlimits | Linux | | no Homebrew formula; not run |
| Docker Desktop | installed on this Mac | container VM | | | daemon was not running; not used |

Why not `srt` as the macOS backend: it still calls `sandbox-exec`, so the deprecated tool does
not go away. Its read model (allow everywhere, deny some paths) is the opposite of the contract
asked for here, it passes the environment through, and it cannot express an exec list or the
GPU. It also adds a Node runtime to a Python library. Its useful part is the host-name egress
proxy; a host allow-list is not built here (web tools run inside the net pipeline,
`poolhouse.httpguard`), and an optional `srt` backend for that is the follow-up.

## What is enforced where

macOS (Seatbelt), checked by `tests/test_sandbox_seatbelt.py` against the real `sandbox-exec`:

- reads outside the allow-list fail; writes outside it fail; a symlink inside an allowed
  directory does not lead out;
- a child process inherits the sandbox;
- a program outside the exec list is not started;
- the network is refused unless the policy allows loopback or the named ports;
- the environment is exactly the policy's;
- the wall-clock limit kills the command's process group and no other process; CPU, file-size
  and output limits stop the command;
- a refusal is read from the system log (each run's profile carries a unique tag), becomes an
  `Result.denials` entry, a `sandbox.denied` event and a `SandboxViolation` naming the operation
  and path.

Known limits:

- Metadata (existence, size) is readable only for the allow-listed trees and their ancestor
  directories; contents outside the allow-list cannot be read.
- A process that calls `setsid` leaves the group the runner kills. It stays inside the sandbox.
- macOS does not enforce `RLIMIT_AS`, so there is no memory ceiling. `Limits.processes` is a process-count ceiling the kernel enforces:
  `RLIMIT_NPROC` counts every process of the user, so the ceiling is what the user has when the run starts plus the allowance, and the
  kernel refuses the fork that crosses it (`EAGAIN`; `tests/test_sandbox_linux.py`).
  The allowance covers the run's own helpers as well as the command's processes, and other processes of the user that start meanwhile use
  some of it: it bounds the run, it does not count it. The three shipped policies (`bash`, `mcp_server`, `model_server`) set it to
  `run.fair_share()`: the room the system leaves the user (its per-user process limit, less what the user has) divided by the runs of this
  process alive plus one (docs/design-isolated-launcher.md, "Numbers and their sources"). On macOS the kernel counts processes and not
  threads, so threads are not bounded (1500 ran with an allowance of 40); on Linux it counts both, and the ceiling adds the user's threads.
  A child that calls `setsid` leaves the group the wall clock kills; the allowance still bounds it.
- Refusals are read from one `log stream` (`denial_log.py`) that the first confined command of a
  process starts and that runs until the process ends; the kernel does not keep its reports for
  `log show`. Reading them waits for a refusal of the reader's own to come back through the stream
  (a few milliseconds) and only happens after a failed run or with `diagnose="always"`. A process
  that is itself confined cannot run `log`.
- When the stream cannot say what a run was refused (it did not start, did not answer, ended or
  restarted after the run began, a report of the run was cut off or could not be read, or the run
  failed with "Operation not permitted" and the stream carried no refusal for it),
  `Result.denials_unavailable` is set, `Result.check()` raises `SandboxViolation`, and a
  `sandbox.denials-unavailable` event (warning) goes to the sentinel. Only events sent by the
  kernel's Sandbox extension count as refusals, and only for a run the process watched. Bubblewrap and
  the container backend keep no refusal log at all: a failed run on them reports
  `sandbox.denials-unavailable` with the detail "this backend keeps no refusal log" and
  `Result.check()` raises.
- The kernel cuts a refusal report over about 1010 bytes at its end, which removes the profile's tag
  (a refused path of about 940 bytes or more). The stream therefore also carries every kernel Sandbox
  report of 200 or more characters. One that no watched run or probe owns and that is 900 bytes or
  longer, or ends in the kernel's cut mark, still names the process that was refused
  (`Sandbox: cat(1234) deny(1) ...`). The kernel reports a process by at most 32 bytes of its name
  (measured with a binary named by 100 characters), so only a `(pid) deny(` that starts within the first 32
  bytes after `Sandbox: ` can be the process that was refused; the same text in the target, or in the
  message of a profile (any local process can have the kernel log a report whose message is
  `(<pid>) deny(` and 900 more bytes), names no process. A process name that holds the text still counts
  every pid it holds, besides the real one. `process_tree.py` follows the
  processes of each run from the moment it starts: the process waits, before it runs its program, until
  the backend has been told its pid (`hold.py`); `kqueue` fork notifications and libproc's child lists
  add each child, a pid stays the run's for ten seconds after its process ended, and a pid that a new
  process takes is followed again. The report makes unreadable the run whose process it names (detail
  "a refusal report of a process of this run was cut off by the kernel"). A child that ends before its
  parent's notification is read is not listed, so a tree counts the forks it was told of and a report
  of a process that no run follows is the run's own only when the run started nothing but its first
  process: it makes unreadable every run that forked or whose processes were not followed from the
  start (the process had already ended when the run was first looked at, a process had more than 4096
  children, or the run started more than 65536 processes), with the detail "... could not be traced
  to a process of another run". A run whose first process never forked, and a report of a process
  that a run still going follows, are not charged to it. A report that names no process makes every
  run being watched unreadable. A run that ends hands the pids of its processes to the runs beside it, so
  that a report of one of them is not charged to them. A tree notes when each of its processes began
  (libproc `proc_pidinfo`), and a pid explains a cut report only for a process that began no later than the
  report was heard, so a process that takes the pid of one that ended after its report cannot explain it.
  The start-up probes include a long report, so a
  stream that cannot carry one is not used, and a probe is answered only by the refusal report it
  provokes (the probe program's refusal, ending in the probe's own token).
- A report cut inside a multi-byte character is not delivered by the system log at all. A run that
  fails with "Operation not permitted" while the stream carried no refusal for it (and one whose
  only refusals were the start-up ones listed below) is unreadable, with the detail "the command
  failed with "Operation not permitted" and no refusal report of it reached the log"; a locked file
  gives the same message without any refusal. A report of the run's own tag that is neither a refusal nor
  the kernel's note of repeats of a refusal the run was reported (or of a start-up one) makes the run
  unreadable ("a refusal report of this run could not be read").
- A run that exits non-zero while the stream carried no report of it, and that did not say "Operation not
  permitted", is not unreadable (`Result.denials_unavailable` stays false and `Result.check()` does not
  raise): it carries `Result.failure_unreported`, and a `sandbox.failure-unreported` event (notice) with
  the detail "the command failed and the refusal log carried no report of it" goes to the sentinel. A
  run that exited 0, was not diagnosed, or timed out adds nothing here.
- Sentinel weights of `sandbox.denials-unavailable`: 1.0 for a log that was lost; 0.2 for a backend
  without a log, for a cut report that could not be traced and for "Operation not permitted" with no
  refusal report (the failure may not be the run's); the weight of a refusal (2.0) for a report of the
  run's own that was cut off or could not be read; 0.05 (`UNREPORTED_FAILURE_WEIGHT`) for a failed run
  with no report in the log at all. The last weight charges ordinary failing commands a little, since
  most commands that fail were not refused anything: 60 of them inside the ten-minute window put the
  session on watch (3.0) and 200 hold it (10.0).
- Accepted cost: a cut report of a process that no run follows (a local process outside every run, a
  forger's own process, a process of a run that ended before the report was heard) makes every run that
  forked unreadable, with the weight of 0.2. A run of one process is not charged. Reading the
  process from a field of the kernel's event, which the stream does not carry, would remove the cost.
- A sandbox event counts toward the score of the session whose tool call started the run
  (`Sentinel.running_session`, the innermost tool call of that sentinel in the running context) and of
  no other. The context follows `asyncio.to_thread` and `contextvars.copy_context().run`; a plain
  `threading.Thread` or an executor does not inherit it, so a run started there counts for nobody and
  the sentinel logs a warning (`counts for nobody`) when a tool call was running meanwhile. A run
  started outside a tool call counts for nobody.
- Refusals that every confined process provokes at start-up (preference files, a few Mach
  services, `net.routetable`) are filtered out of `Result.denials`.

## Attack first: the network, new namespaces and the process count

The attacker is code that runs inside a confined process with the person's user id: a command a model chose, an MCP server, a model
server, and every program any of them writes and starts. It wants the services on the host's loopback (the node, the broker, the
approval page, the model server's own port), more processes than the run was given, and a way out of the profile. Each row has the
probe that failed on the code before the change and passes on the code after it (`tests/test_sandbox_linux.py`, in a container with
bubblewrap).

| # | Attack | Defence | Probe |
|---|---|---|---|
| P1 | Linux: with `Net.only` or `Net.loopback` reach any host port, the LAN or the internet, a pathname or abstract Unix socket of the host | no `--share-net`; every mode runs in its own network namespace; a named port is reached only through a relay | a confined process connects to a port the policy did not name, to a wildcard listener (through the host's own address too) and to the host's abstract socket (refused), and to the named port (works) |
| P2 | Linux: enter a nested user namespace to regain capabilities, then mount, `ptrace` or open raw sockets | `--unshare-user --disable-userns --cap-drop ALL` | `unshare(CLONE_NEWUSER)` fails inside; the capability sets in `/proc/self/status` are empty |
| P3 | Linux: use the relay as a deputy: another port, another host, a second socket the parent did not make | the host end connects to `127.0.0.1` and the one port its socket was made for; nothing in the byte stream names a destination; the socket directory is the parent's, mounted read-only | the sockets present inside are exactly the named ports; a connection through a socket reaches that port and no other |
| P4 | Linux: flood the relay, hold connections open, outlive the run | at most 32 connections per port; relays and sockets end with the run (`finish`); a directory a killed parent left is swept when the next relay is made | connections past the bound are closed; after `finish` the socket directory is gone; a directory of a dead process is removed |
| P5 | downgrade: a bubblewrap that lacks `--disable-userns`, or a policy that asks for the host's whole loopback on Linux; a different `bwrap` found on `PATH` after the probe | the backend reports itself unavailable with the reason; `Net.loopback()` on Linux raises `PolicyError` naming `Net.only`; `wrap` runs the binary `available` probed | the unavailable reason names the missing option; the refusal is raised before a process starts; the probed path is the one run |
| P6 | fork without end (a fork bomb), or hold many threads so that a count of processes undercounts the kernel's | `RLIMIT_NPROC` set to what the user has (processes, and on Linux threads) plus the policy's allowance: the kernel refuses the fork that crosses it | a confined loop forks until `EAGAIN` and stops within the allowance, with 300 threads of the same user running (Linux) |

The other cases of AGENTS.md section 1 do not change: nothing here handles a key, a signature, a membership or an approval, so a copied
or cloned pool or device, a replay, a forged signature, a stolen phone, approval fatigue and a takeover have no new surface, and a
malicious or compromised agent is the attacker described above. Not defended: a kernel or `sandbox-exec` or bubblewrap bug.

## GPU

`Policy(gpu=True)` adds two rules to the profile:

```
(allow iokit-open (iokit-user-client-class "IOGPUDeviceUserClient"))
(allow mach-lookup (global-name "com.apple.MTLCompilerService"))
```

Found by running `llama-server -ngl 99` (granite-4.0-h-350m, Q4_K_M) under the profile, starting
from a guessed list of 12 IOKit classes, 4 Mach services, 3 read paths and `iokit-get-properties`,
and removing one at a time while the server still loaded all 33 layers onto the GPU and answered.
Only the two above were needed. The server is up in 3.6 to 4.7 s with them. llama.cpp embeds its
Metal library, so it needs no shader-cache directory; `Policy.cache` names one directory that is
readable and writable for a stack that does (MLX, PyTorch). A model server started from a Homebrew prefix also needs that prefix readable (its libraries are
linked by absolute path) and a working directory it can read. The base profile also allows
`hw.pagesize_compat` (without it the allocator asks for 2^44 MiB and the server crashes).

## Where it is applied

| Call site | Policy | Layer behind it |
|---|---|---|
| `poolhouse.sandbox.tools.SandboxedBash`, the shell tool for an `Agent` | `bash`: read the project, write a scratch directory, no network, 120 s | the guard's tool-policy and taint rails (`bash` is an `exec` sink: text from outside cannot reach it) |
| `McpTools.stdio(...)` | `mcp_server`: read the interpreter, project and `reads`, write a scratch directory, loopback only, `env` as the whole environment | the guard; the server is not started when no sandbox is available |
| `poolhouse.harness` (Claude Agent SDK) | Claude Code's sandbox for Bash: on, no per-command opt-out, `failIfUnavailable`, no network domains | the guard's `PreToolUse` hooks; not driven end to end in this change (see below) |
| `LlamaServerBackend(sandboxed=True)` or `POOLHOUSE_SANDBOX_SERVE=1` | `model_server`: read the binary's directory and the files in the command line, loopback, GPU | opt-in; the Broker leases the server as before |

Not routed: `llama-server` and the Broker daemon by default (opt-in only), `checks.py`'s
`shell=True` fix line (typed by the person and confirmed), `subprocess` calls in `serve`, `bench`,
`fleet`, `gguf`, `speech` and `doctor` that run Poolhouse's own commands with arguments the code
builds. Each is a candidate for review as it takes input from an agent.

Sandbox events go to the sentinel bus through `sentinel.adapters.sandbox_listener`:
`sandbox.denied` (warning), `sandbox.denials-unavailable` (warning), `sandbox.failure-unreported` (notice), `sandbox.unavailable` (warning), `sandbox.unsandboxed` (warning),
`sandbox.timeout` (notice).

## Replacement path: container backend

Written when Apple's `container` (or a VM runner) is installed and measured:
`Container.wrap` turns the policy into `container run --rm --network none --read-only` with the
read trees mounted read-only, the write trees read-write, an explicit `--env` list and the limits
as `--cpus`/`--memory`, and a Linux build of the tool inside the image. It serves CPU-only work
(shells, MCP servers, converters). It cannot serve GPU work. Until then `Container.wrap` raises
`NotImplementedError` and `available()` is false.

## Linux

`bubblewrap.arguments` builds the argv: `--unshare-all` (user, IPC, PID, network, UTS and cgroup namespaces), `--unshare-user
--disable-userns --cap-drop ALL` (the process cannot make a user namespace of its own, which is how an unprivileged process regains
capabilities, and holds none), read-only binds for `read`/`exec`, binds for `write`, `--clearenv` with `--setenv` for each variable.
No policy shares the host's network. The process sees its own loopback and nothing else; `Net.only(*ports)` reaches those ports of
the host's loopback through a relay, and `Net.loopback()` (every port) raises `PolicyError` because it cannot be given without the
host's whole network. A policy with no network is not given the host's `/etc/hosts` and `/etc/resolv.conf`.

The relay (`netrelay.py` in the parent, `netforward.py` in the namespace): the parent makes a directory it alone can enter
(`poolhouse-net-<pid>-<start>-...` under the temporary directory), listens on one Unix socket in it per named port and connects each
accepted connection to `127.0.0.1` and that port. The directory is bound read-only into the namespace at `/run/poolhouse/net`, and the
process that runs first inside (`python -I -S netforward.py`) listens on the same ports of the namespace's loopback, carries each
connection to the socket for its port from a process of its own, then becomes the command. The interpreter that runs it is bound
read-only with its standard library (and its shared library, when it has one), and the directories of installed packages inside those
trees are hidden. Neither end copies a destination from the bytes it carries, each port carries at most 32 connections at once (one
over is closed), and the sockets and the directory end with the run (`Bubblewrap.finish`); a directory a killed parent left is removed when the
next relay is made. A command can connect to the named ports and, through the sockets it finds, to the same ports; to no other port, to
no address beyond the machine, to no Unix socket of the host and to no abstract socket of the host.

A bubblewrap older than 0.8.0 has no `--disable-userns`: the backend reports itself unavailable and says so, and no command runs more
loosely. `wrap` runs the binary `available` probed. The Linux boundary is tested by `tests/test_sandbox_linux.py` on a host that can make
the namespaces (Ubuntu 24.04, bubblewrap 0.9.0, a container started with `seccomp=unconfined` and `systempaths=unconfined`, since Docker's
defaults refuse user namespaces); elsewhere the real-process tests skip with the availability check's reason. Network-denied reads and GPU
visibility are exercised on WSL by `tests/test_wsl_backend.py`. Executable visibility follows the mounted filesystem; the executable
allow-list needs additional enforcement. Still readable on Linux: `/proc/cmdline`, `/proc/version`, the boot id and the kernel host name.

## Commands

`poolhouse security sandbox status` prints the backend, whether it can run and the deprecation
note. `poolhouse security sandbox test` runs six guarantees against real confined processes and
exits 1 when one fails.

## Sandboxed model server

`LlamaServerBackend(sandboxed=True)` or `POOLHOUSE_SANDBOX_SERVE=1` starts the model under
the `model_server` policy: the GPU, the binary's install tree, the directories of the files
on its command line, and the slot-save directory. The Windows WSL launcher enables this
automatically. On Linux, the model has no network and listens on a private Unix socket in
a writable temporary directory. Its trusted supervisor relays the socket through host
loopback and exits with the model. On macOS, the policy grants loopback directly.
Downloads happen through the broker before the confined process starts. A failed start
appends the sandbox's refusals to the `ServerFailed` message. GPU inference, MTP,
multimodal projectors, and sharded models require live model validation.

## Tests and measurements

- `tests/test_sandbox_policy.py` (pure), `tests/test_sandbox_seatbelt.py` (real `sandbox-exec`), `tests/test_sandbox_linux.py` (real `bwrap`),
  `tests/test_sandbox_integration.py`, `tests/test_sandbox_redteam.py` and
  `tests/test_sandbox_gpu.py` (slow: a real `llama-server` offloading every layer inside the
  profile, plus a decoy read and a connection out refused). The Seatbelt tests skip cleanly
  where `sandbox-exec` is absent.
- Mutation check of the profile writer, validators, runner and bubblewrap argv: 45 hand-made
  mutations. The first pass left 7 alive (message-masked validation errors, signals, the
  operation parser); tests were tightened and all 45 are now killed.

Native managed-build success tests first run a confined system `true` command. They skip at the
test function when the backend is absent or the host denies namespace creation. Other probe
failures and malformed policies fail the tests. The Linux container runner keeps Docker's
default privileges; it does not enable privileged mode or relax seccomp to make a native
sandbox run. Fail-closed policy and managed-build refusal tests still run when native success
tests cannot.

Bubblewrap availability uses a bounded native namespace probe, cached per executable identity,
user and user namespace. An installed executable whose namespace probe is denied is reported
unavailable. The probe keeps the policy's namespace and network restrictions, mounts no
entire root directory, and runs the system `true` executable. Timeout cleanup terminates its
process group. No agent command is executed by the availability check.
