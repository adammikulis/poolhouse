# Isolated launcher: enforced isolation for coding agents

Design. Nothing in this document is built. Owner order: build the isolated launcher, so that `poolhouse-claude`, `poolhouse-codex`
and `poolhouse.coding.launch_coding_agent` start again, with isolation the operating system enforces on macOS, Linux and WSL, and
Windows; a platform that cannot enforce what is claimed refuses to start, with the exact reason, and never starts unisolated. The
unconditional refusal `harnessing.require_isolated_execution` is replaced by a capability report and by the launch itself. The
non-author attack pass of AGENTS.md section 1 is `docs/design-isolated-launcher-breakit.md`; section 14.3 names the section that
decides each of its findings, and the slices of section 15.1 carry them with their first red tests. Every question the design
raised is decided in section 18; nothing remains open.

Code read at `bf17c71cd`, an ancestor of development (the files this document names are identical on `2807a9005`). A statement about
code was read, not run. A statement about an operating system carries one of three labels: **VERIFIED** (run on a named device while
this document was written; the commands are in section 5.6), **PREDICTED** (from documentation or code, not run) and **PROBE** (a
check the slice that needs it runs first; until it has run, the design reads the worse case). Devices used: this Mac (macOS 26.6,
build 25G70, Apple M4 Max, `/usr/bin/sandbox-exec` present) and a Linux VM that is **not** the WSL device (Docker Desktop's kernel
7.0.14-linuxkit, Ubuntu 24.04 userland, bubblewrap 0.9.0, Landlock ABI 8). The WSL device and the Windows PC were not reachable
(`poolhouse-test-devices`: this pool has no other active device), so every WSL and Windows statement is PREDICTED or PROBE.

Read first: AGENTS.md sections 1 and 2 ("Attack first", "Never a fake model", "A number comes from a source", "Reporting a problem":
redesign, not patch), `docs/agent-sandbox.md`, `docs/sandbox.md`, `docs/harnesses.md`, `docs/compromised-agents.md` 2.1,
`docs/compromise-containment.md` tier 1, `docs/design-fail-closed-harness.md` (enforcement never depends on observation),
`docs/design-session-capabilities.md` (custody of a session's secret), `docs/windows-runtime.md`.

**Words.** A **confinement** is the operating-system boundary one coding session runs inside: its profile, its processes and the
services the parent runs for it. The **stage** is the directory the agent sees as its project: a parent-owned copy of the person's
project (`coding_workspace.Workspace`). The **master** is the parent's verified copy the stage starts from. **Publication** is the
parent applying verified stage changes to the person's checkout. A **bridge** is a service the parent runs and the confined session
reaches over the one channel its profile allows. A **grant** is one explicit, logged widening of a session's reach. The **proof** is
the escape run of section 10. The **capability report** is the per-guarantee statement of section 9. A **guarantee** is something a
confined process cannot do (5.1). An **exposure** is a fact it can still learn on a platform whose mechanism does not hide it,
named in the report and pinned by a probe (5.7). **Plain source** is the one class of change publication applies without a
question; every other change is **held** for the person's answer (4.4).

**In short.** A launch builds one confinement, proves it, and only then starts the harness inside it. The confinement holds the
agent's copy of the project and a private home; it reaches the model only through a parent-run bridge, and the person's state
(hooks, board, approvals) only through a second bridge. Nothing else exists for it: not the home directory, not other loopback
services, not the network. After each turn and when the session ends the parent checks what the agent changed, applies the plain
source to the person's project, and holds everything else for one question. A platform that cannot do this refuses to start, in
one sentence, and nothing is started unisolated.

## 0. The decisions, one line each

| # | Decision | Why |
|---|---|---|
| I1 | The harness process and everything it starts run inside one confinement the launcher builds; no code path starts a coding harness outside it. | The harness vendors' own sandboxes cover less (Claude Code's covers shell commands only; its file tools, MCP servers and hooks run outside it, and it does not run on native Windows: `code.claude.com/docs/en/sandboxing`) and cannot nest (VERIFIED: `sandbox_apply: Operation not permitted` inside a Seatbelt run). |
| I2 | One choke point: `isolation.prepare(spec)` returns a `Confinement`; a launcher's runner starts the harness only through `Confinement.popen`. `harnessing.require_isolated_execution` and its 14 call sites are deleted. | A refusal at 14 places is a policy nobody can prove complete; one place that builds the only argv that exists is. |
| I3 | Default reach: the stage (read, write), a private agent home (read, write), the harness's own install tree and the system toolchain (read, execute), and one model bridge. Nothing else: no home directory, keystore, other repository, other loopback service, LAN, internet, unix socket, GPU, other process or machine identifier. | The owner's decision. |
| I4 | The parent-owned workspace is the project the agent sees. The agent never writes the person's checkout; the parent publishes verified changes after each completed assistant turn. Publication is deny by default: only plain source is applied without a question, and every other change (build, CI, hook, package-manager, wrapper, lockfile, configuration, agent-instruction and review-governance files, programs, binaries, anything unrecognized) is held for the person's answer, whichever tool wrote it. | A confinement bounds what the agent can reach; it cannot bound what the agent leaves behind for the person's tools to run. Only the parent, comparing bytes, can, and a list of names that run later is always behind the tools that read them. |
| I5 | Writing the person's checkout in place is a named grant typed by a person, never a fallback. Staging has no project size ceiling; the one reason a project cannot be staged is that the stage volume lacks the free bytes, and the launch then refuses with the figures and names that grant. | No silent fallbacks. |
| I6 | The model is reached through a parent-owned bridge that allows a fixed set of routes, pins the model, and swaps a per-session token for the server's key. The model server's own port is in no profile. | `--on URL` servers carry whatever flags the person started them with; llama-server's slot, props and tool routes are not a coding agent's to call. |
| I7 | Hooks and workspace verbs run in the parent. The command inside the confinement is a client of one session bridge; the confinement never sees the state root, the node socket, the Requests store or a token. | A hook inside the boundary it polices is not a gate. |
| I8 | Guarantees are a typed report per platform. A launch needs every guarantee of the default reach; any that is not enforced refuses with its own sentence. What a confined process can still learn on a platform is listed beside them as exposures, each pinned by a probe so the list cannot grow unnoticed (5.7). | The owner's decision. A report that says "enforced" while a fact leaks is false; a fact that cannot be hidden is named. |
| I9 | Every launch proves its confinement: a probe program runs inside the same profile, tries to escape and must fail, and positive controls must pass; the live harness process is then attested. A deviation stops the launch. | Enforcement is the claim; the proof is the evidence, on this machine, now. |
| I10 | Linux and WSL need bubblewrap namespaces (user, mount, pid, net, ipc, uts, with nested user namespaces disabled and no capabilities). Landlock and seccomp are added inside when present. Landlock alone never suffices. Native Windows refuses until an enforceable per-port loopback rule is shown; the WSL2 route is supported; there is no weaker mode. A Linux host that restricts user namespaces gets an AppArmor profile Poolhouse ships in the repository and installs at setup: bound to the one `bwrap` binary, granting `userns` and nothing else, never a sysctl change, pinned by a test (5.3). | Section 5. |
| I11 | The environment is built from an allow-list. Nothing is inherited. The harness holds a bridge token, never a server key. | `claude.environment` and `codex.environment` copy the whole of `os.environ` today. |
| I12 | Widening is a grant of four kinds (read, toolchain, network host, in-place), issued only by a person, scoped to the session, logged. Network grants act at once through the egress proxy; file grants act at the next launch. | A profile is fixed when the process starts. |
| I13 | The permission profiles do not move the operating-system boundary, except that `read-only` and `plan` mount the stage read-only. `yolo` asks less; it does not widen. | `tests/test_coding_permissions.py` already holds that `yolo` opens no launcher control. |
| I14 | What the operating system cannot bound per session (memory on macOS and on Linux without a delegated cgroup, disk everywhere, process count on Linux until the kernel is shown to count it per namespace) a parent watchdog bounds, which kills the session loudly on crossing a limit derived from the machine and is never silent; the report says "watched", not "enforced", for each resource. What the kernel can refuse at start (macOS `RLIMIT_NPROC` for the process count) it refuses. | The report must match the mechanism, and enforcement beats a sampled watcher wherever the kernel offers it. |
| I15 | `coding_workspace` stays and gets its caller (section 16). Nothing is deleted that this design does not supersede (section 15). | The owner's order. |
| I16 | Landing order: harden the backends first (they already protect every sandbox user), then the library, the bridges, the workspace, the wiring, the platforms. | Section 15. |
| I17 | A process inside a confinement is never a person. `person.require_person`, `person.confirm` and `person.typed` refuse in any process the operating system reports as confined, whatever its markers, its descriptors or its terminal; no question for a person is ever read from a terminal the confinement holds. | The confined harness holds the relay's pty as both ends of its stdio and can type into its own input (3.5); a check that reads descriptors and an environment variable is answered by the attacker. |
| I18 | Ending a session ends every process it started, found by walking the backend's record of the session (macOS: the follower's tree; Linux: the pid namespace; Windows: the job), never by signalling a process group. | A descendant that calls `setsid()` leaves its group and still writes the stage (4.2 item 3). |

## 1. What exists

### 1.1 The refusal

`harnessing.require_isolated_execution()` (`harnessing.py:62`) raises `ValueError("This coding runtime cannot enforce isolated
session permissions")` and nothing else. It was added by `c1d01d19b`, which also removed Pi's per-tool worker wiring from `pi.py`
(`pi_tools` has had no caller since). It is called at 14 places in 12 files:

| Group | Sites |
|---|---|
| Launchers | `claude.py:110`, `codex.py:106`, `pi.py:130` |
| Turn manager (chat coding dialog, native tasks) | `workspace/coding_turns.py:87` (`Manager.start`), `:164` (`Manager._run`) |
| Catalogue (`can_start`, `enforcement_error`) | `workspace/coding_routes.py:20` |
| Local agent start (profile `coding`) | `workspace/localcli.py:144`, `localstart.py:155`, `localroute.py:99` |
| Local agent loop (any harness but the agent's own) | `workspace/localloop.py:137`, `:353` |
| Native tasks | `workspace/task_launch.py:19`, `task_worker.py:44`, `task_coding.py:140` |

Eight test files and `tests/test_wiring.py` name the refusal text: `test_coding_admission` (eight mentions), `test_task_coding` (3),
`test_coding_browser` (the `refused` helper), `test_chat_unified_browser`, `test_agent_dependency`, `test_coding_catalogue_readiness`,
and two that stub the function (`test_pi_harness`, `test_coding_current`).

### 1.2 Who starts a harness

Every production path ends in `runner(command, env)`:

- `poolhouse-claude` and `poolhouse-codex` (console scripts) and `python -m poolhouse.pi` call `claude.launch`, `codex.launch`, `pi.launch`. Each checks the
  project (`harnessing.check_project`), leases the model (`harnessing.serving`: the broker, never a hand-started server), registers
  a workspace session and writes the session's settings and brief into a read-only directory under the state root
  (`harnessing.opened`), then calls the runner; the default runner is `subprocess.call(cmd, env=env, cwd=project)` with the terminal
  inherited.
- `coding.launch_coding_agent(model, policy, project, harness, **options)` builds the argv of those same functions; `run_claude`,
  `run_codex` and `run_pi` in `options` replace the runner.
- `workspace/coding_turns.Manager._run` (the chat coding dialog) and `task_coding.TaskManager` (native tasks) call
  `launch_coding_agent` from a spawned worker with `run_<harness>=self._process`. `_process` starts the harness with
  `platform.start_process(command, env=environment, cwd=folder, stdin=PIPE, stdout=PIPE)` in print or exec mode, reads its JSONL
  with a 1 MiB line cap (`MAX_EVENT`), sets `CLAUDE_CONFIG_DIR` to a per-conversation home under the conversation store, and copies
  that home in and out of `CODEX_HOME` for Codex.

Both environments are built from a full copy of the caller's environment (`claude.environment`: `dict(os.environ)` plus the
`ANTHROPIC_*` variables; `codex.environment`: the same plus `CODEX_HOME` and the server key).

### 1.3 What the two landed layers hold, and which part has no caller

| Piece | What it gives | Caller today |
|---|---|---|
| `sandbox` policy engine (`policy.py`, `run.py`, `seatbelt.py`, `bubblewrap.py`) | `Policy` with read, write, exec, net (deny, loopback, ports), env, limits, gpu, `strict`, `deny_read`, `deny_write`, `read_files`, `read_dirs`, `unix_sockets`; `run()` and `wrapped()`; Seatbelt backend with refusal log; bubblewrap backend | `SandboxedBash`, MCP servers, the model server (opt-in), the Pi tool worker (tests) |
| `sandbox.io` | `IO` and `Pipes`: owned process channels, bounded output (darwin only) | the Pi tool worker |
| `sandbox.process_tree`, `hold`, `denial_log` | children followed from the first instruction, reported refusals | every Seatbelt run |
| `coding_policy` | four profiles, two modes, `Policy.writable` | launchers, hooks |
| `harnesshook`, `harnesspolicy`, `guard.*` | classifier, asks in the Requests inbox, the auto-run name table (`guard.autoexec`, F14) | the hooks the launchers write |
| `coding_workspace`, `coding_workspace_state`, `activity.source_snapshot` | the parent-owned stage (section 4) | **none** (`STANDALONE` in `tests/test_wiring.py`) |
| `coding_actions` and `Conversations.begin_execution`, `reserve_action`, `start_action`, `action_guard`, `coding_guard` | exactly-once tool actions with durable receipts and approvals bound to an action digest; the publication guard | **none** (tests only) |
| `pi_tools` | Pi's seven stock tools executed in one Seatbelt worker each | **none** since `c1d01d19b` |
| `serve.confined`, `serve.socket_relay` | a model server inside the sandbox with a Unix-socket relay to host loopback | the Windows launcher (WSL) |

Two execution models are in the tree. A **tool proxy** (Pi, `coding_actions`, `pi_tools`) runs each tool call in a fresh worker on a
stage; the harness is only the model loop. A **native** harness (Claude Code, Codex) runs its tools inside its own process tree.
This design builds the second, because it is the one the owner named; the first stays available (section 16).

### 1.4 Defects found while reading and measuring

F1, F2 (which holds two defects: the shared host network and the open nested user namespaces), F12, F13 and the process-count bound D5 of 5.2 are being fixed now in the sandbox engine on branch `fix/sandbox-holes-and-harness-denial` and are not part of the launcher build; L1 starts from its result, and what that result closes decides the state of G13 and of exposure X1 on macOS (5.2, 5.7). Every other finding is fixed by a slice of section 15. F12 to F18 were found by the non-author pass (`docs/design-isolated-launcher-breakit.md`).

| # | Finding | Evidence |
|---|---|---|
| F1 | A process inside a Seatbelt run reads the full argument vector and environment of its parent and of any other non-platform process of the same user (`KERN_PROCARGS2`). The launcher's environment, a ticket, a token in a board session's environment are readable. | VERIFIED 5.6 (M-proc). This answers probe P2 of `docs/design-session-capabilities.md` for macOS: yes. |
| F2 | On Linux `Net.loopback()` and `Net.only(...)` run with `--share-net`, which shares the whole host network (not loopback): VERIFIED that a port the policy did not name connects. `docs/sandbox.md` says so. The bubblewrap backend also leaves nested user namespaces enabled (VERIFIED: `unshare --user --map-root-user` yields uid 0 inside) and keeps capabilities. | VERIFIED 5.6 (B-ns, B-userns). |
| F3 | The bubblewrap backend refuses `deny_read` and `deny_write`; there is no way to freeze a control path inside a writable tree on Linux, and no Windows backend (`SandboxUnavailable`). `Pipes` exist on darwin only. | code |
| F4 | `Workspace` takes 16.0 s to construct, 9.2 s for `begin()` and 19.3 s for `finish()` on this repository (3,710 files, 39.6 MiB), against 1.9 s for a plain copy of the same files, 0.15 s to hash them and 0.5 s for an APFS clone. The time is spent in per-path ancestry validation (270,990 `lstat` calls), per-path secret-name matching (3.4 s) and git subprocess waits (4.3 s). It is a defect of the build, fixed there; it is not a reason to edit in place. | VERIFIED 5.6 (W-time), cProfile. |
| F5 | `actual_git()` on macOS accepts only Homebrew's git (`/opt/homebrew/opt/git`, `/usr/local/opt/git`) and refuses Apple's shim; a Mac without Homebrew git cannot build a workspace. | code |
| F6 | `SourceSnapshot` refuses a whole capture on one tracked symlink, one group-writable file (the default umask on Ubuntu is 002), one hard link or one submodule, with "capture requires owned protected regular files". It is POSIX-only (`getuid`, `O_NOFOLLOW`, `dir_fd`, `fwalk`). | code |
| F7 | Its bounds (16 MiB a file, 256 MiB in all, 30 s) have no recorded source (AGENTS.md section 2); they are removed (W4, 18.2). | code; VERIFIED that a 5 GiB sparse file ends a capture today with `source bytes exceed bounds` |
| F8 | The stage has no ignored files (`.venv`, `node_modules`, build output), so a project's tests cannot run in it as they are. | code |
| F9 | Nothing applies stage changes to the person's checkout; `result()` feeds the task proposal pipeline only. | code |
| F10 | A hook runs as a child of the harness (`python -m poolhouse.harnesshook ...`) and needs the state root, the node socket and the Requests store; none of these can be inside the confinement. | code, I7 |
| F11 | 30 tests fail on development at `bf17c71cd` in seven files (section 15.3). Seventeen are the refusal; three assert its text and fail earlier in the rig; ten (`test_harness_exec_chain`, `_more`, `test_harness_hook_outages`) fail for a different reason: `harnesshook.pre` denies because `harness_claims.conflict` raises `Denied: ... has no session of its own on the board` for an agent with no verified board session (`9cd3fde72`). | measured, section 15.3 |
| F12 | A process inside a Seatbelt run lists every process of the machine, with its name, pid, parent pid and user id, through the per-pid `kern.proc.pid.` sysctl and libproc, with D1 in force: the profile allows the `kern.proc.pid.` prefix (`SYSCTL_PREFIXES` in `sandbox/seatbelt.py`). D1 closes only the arguments and environment. | VERIFIED by the non-author pass (N1): 591 own-user processes enumerated in one scan |
| F13 | A process inside a Seatbelt run reads the machine's serial number, hardware UUID and provisioning UDID with `system_profiler SPHardwareDataType` or `ioreg -c IOPlatformExpertDevice`, with D1 and `(deny iokit*)`, `(deny mach-lookup)` and `(deny sysctl-read)` all appended. The channel is a cached read the profile's `file-read*` grants allow; the one operation is not yet isolated. No guarantee names it. | VERIFIED by the non-author pass (N2) |
| F14 | `guard/autoexec.py` is a table of names (`RUNS`, `FOLDERS`, `FILES`, `SUFFIX`, `CONFIGS`) that stands as "the paths that run by themselves later". It returns no reason for 79 of 90 real auto-run file names: git-hook managers (`lefthook.yml`, `.lintstagedrc`), `.lfsconfig`, `action.yml`, `Tiltfile`, `cloudbuild.yaml`, wrapper scripts and jars (`gradlew`, `gradle-wrapper.jar`, `maven-wrapper.jar`), MSBuild and Bazel files (`Directory.Build.props`, `*.csproj`, `*.sln`, `WORKSPACE`, `BUILD.bazel`, `.bazelrc`), `requirements.txt`, `Pipfile`, `.prettierrc.js`, and the instruction files of GitHub Copilot, Windsurf, Aider, Continue and Cline. It does not name `CODEOWNERS`, which decides who must review a later change. | VERIFIED by the non-author pass (N3 to N5) |
| F15 | `sandbox/run.py` ends a run with `killpg`. A descendant that calls `setsid()` leaves the process group: the group kill reached 1 of 255 and 1 of 585 live descendants in the two `setsid` trials, while the follower reported every one. | VERIFIED by the non-author pass (A14, A21) |
| F16 | `person.require_person` is "no agent marker, and a terminal on stdin and stdout". A harness in an interactive confinement holds the relay's pty slave as both, the allow-list environment carries no marker, and the harness can type into its own input (`TIOCSTI` on its own slave), so the check and a `confirm` read on that terminal can both be satisfied by the attacker. | DESIGN TRACE (N9); the relay is not built |
| F17 | `claude.launch` and `codex.launch` take `--project`, `--on` and every other flag from `argv` and call neither `person.marked()` nor `require_person`; `--on` is not checked for loopback. | code (A45) |
| F18 | A FIFO in the stage is left out of a capture and the change list names nothing: the published list is empty. | VERIFIED by the non-author pass (A8) |

## 2. Threat model

**The attacker** is the model's output. It is prompt-injectable by anything the session reads (a file, a page, a tool result, a
commit message, another agent's message) and may be hostile outright. It chooses every tool call: any shell command, any file edit,
any program it writes and runs, with the session's user id, inside the confinement. It sees the harness's environment and its
command line. It does not choose the harness binary, the launcher, the hook, the bridges or the profile. The harness is trusted
code but not trusted to be bug-free: the confinement holds with the harness itself as the attacker.

**Assets**, in the order the owner listed them: the person's files (home, keystore, credentials, other repositories), the person's
processes and terminal, the state root (tokens, Requests store, node socket, keys), the model server and the GPU, the network, the
person's checkout (what the agent leaves in it), and the person's attention (approvals).

**Not defended**, and said so: a kernel or sandbox-engine bug (Seatbelt, bubblewrap, Landlock, AppContainer); side channels; a person
who approves a well-disguised request after reading an honest one (`docs/compromised-agents.md` 15); a change to plain source that
the person runs without reading the diff (4.4 applies plain source without a question); bytes smuggled in the path or sub-domain of
a granted host; the exposures of 5.7, which are named and pinned and which no mechanism the platform offers hides.

| # | Surface | What the attacker tries | Where it is answered |
|---|---|---|---|
| T1 | Files | read the home directory, keystore, `~/.ssh`, other repositories; write outside the stage | 5, G1 |
| T2 | Links and names | symlink out, hard link in or out, `..`, `/proc/self/root`, firmlink aliases (`/System/Volumes/Data/...`), case and Unicode aliases of protected names, Windows short names and alternate data streams | 5, 4.4 |
| T3 | Workspace | race the parent's capture; swap the stage root; FIFOs, devices, sparse and enormous files, millions of files; set-uid and mode bits | 4 |
| T4 | Planted code | write a git hook or a hook manager's file, git's own configuration, `.gitattributes`, `.lfsconfig`, `.claude/`, `.mcp.json`, the instruction file of any coding tool, a `Makefile`, `conftest.py`, `package.json`, a wrapper script or jar, a lockfile, a CI workflow, a shell startup file, or `CODEOWNERS` (so that a later change is not reviewed) that the parent or the person runs or relies on later | 4.4 |
| T5 | Processes | read another process's arguments and environment, `ptrace`, `task_for_pid`, signal the parent, list processes | 5, G6, exposure X1 |
| T6 | Terminal and desktop | `TIOCSTI`, `/dev/tty`, Apple events to the terminal, `osascript`, `open`, clipboard, UI automation; typing into the harness's own pty to answer a question put to a person | 3.5, 5, G7, 8.3 |
| T7 | Persistence | `launchd`, `cron`, `at`, `systemd --user`, scheduled tasks, login items, a process that outlives the session | 5, G10 |
| T8 | Network | any loopback port but the bridge (the node, the broker, the approval UI), LAN, internet, IPv6, UDP, DNS, raw sockets, binding a listener | 6 |
| T9 | Unix sockets | `docker.sock`, `ssh-agent`, D-Bus, X11, `tmux`, abstract sockets, the node's socket | 5, G5 |
| T10 | Model server | routes other than chat (`/slots`, `/props`, `/tools`, `/metrics`, model management), a different model, field smuggling, keys, a second slot | 6.1 |
| T11 | Credentials | tokens in the launcher's environment, the harness's own login (`~/.claude`, `~/.codex/auth.json`), the keychain, browser profiles | 3.4, 5 |
| T12 | Hooks and verbs | forge or replay a hook event, flood asks, run a verb the profile does not allow, act as another session, read another session's token | 7 |
| T13 | Resources | fork bomb, memory, CPU, descriptors, disk fill (including the person's disk), output flood | 5, G11 |
| T14 | GPU and devices | take the GPU from the model server; open Metal, `/dev/dri`, `/dev/nvidia*`, camera, microphone | 5, G9 |
| T15 | Children | double fork, `setsid` (which leaves the process group), daemons that outlive the session or mutate the stage after publication | 4.2 item 3, 5, G10 |
| T16 | Nested confinement | enter a new user namespace or a nested sandbox to regain capabilities | 5 |
| T17 | The launcher as deputy | pass `--project`, `--protect`, grant flags or a hostile `--on URL` to a launcher run by a sandboxed agent | 8.3 |
| T18 | The proof | make the proof pass and the run differ; downgrade between check and start; a flag or variable that skips it | 10 |
| T19 | Machine identity | read the serial number, hardware UUID, provisioning UDID, machine id or network hardware addresses | 5, G13 |
| T20 | The person's own edits | time publication, or hold a descriptor, so that the parent overwrites or deletes a version the person saved after the parent read the file | 4.4.3 |
| T21 | The host's setting | make the installer turn off a host-wide restriction (the AppArmor user-namespace sysctl) instead of allowing one binary | 5.3 |

## 3. The launcher

### 3.1 Layers

```
callers      workspace/coding_turns, task_coding, local*, coding_routes      (UI, tasks, CLI)      tools layer
launchers    coding.launch_coding_agent -> claude / codex / pi .launch        (lease, identity)     tools layer
isolation    capability, profile, grants, proof, confinement, bridges         (new package)         tools layer
workspace    coding_workspace (+ _state), activity.source_snapshot            (the stage)           core layer
sandbox      Policy, Seatbelt, Bubblewrap, AppContainer, IO                   (the engine)          core layer
```

Imports go down only: callers to launchers to `isolation` to `workspace` and `sandbox`. `isolation` imports neither a launcher nor
`workspace/` nor `fleet/`. It reaches the hook classifier, the node and the Requests store only through callbacks the launcher
passes in (`isolation.bridge.Handlers`), so no upward import exists. Enforced by `tests/test_layers.py` (the package is added to
the tools layer), an import test that `claude.py`, `codex.py` and `pi.py` do not import `subprocess` and call
`platform.start_process` nowhere, and `tests/test_wiring.py` (section 16). The `subprocess-spawns` budget falls by the launchers'
spawn sites.

### 3.2 The objects

| Object | Holds | Built by |
|---|---|---|
| `Spec` | harness, registered project (real path), `coding_policy.Policy`, model endpoint (base URL, alias, window, slots), session identity, requested grants, the conversation home | the launcher |
| `Capability` | for each guarantee of 5.1: `enforced`, `watched` or `unenforceable`, the mechanism, the reason, the remedy sentence | `isolation.capability()`; cheap, cached per boot, per OS build and per backend binary identity |
| `Profile` | the sandbox policy, the stage and home paths, the toolchain reads, the bridge endpoints, the grants in force, and a digest of all of it | `isolation.profile.build(spec, grants)` |
| `Confinement` | the wrapped argv, the exact environment, the working directory, the started services, the workspace session, the proof, `popen()`, `close()` | `isolation.prepare(spec)` |
| `Proof` | each probe line with the expected and the observed result, the profile digest, the operating-system build, the time | `isolation.proof.run(profile)` |
| `Grant` | kind, subject, scope, issuer, the sentence the person saw, the request fingerprint | `isolation.grants` |

### 3.3 Order of a launch

1. The caller calls `launch_coding_agent` (signature unchanged) or a harness `launch`.
2. `harnessing.check_project` (unchanged), then `isolation.capability()` for this spec. A guarantee that is not enforced raises
   `IsolationRefused` with its sentence (section 9) **before** the model is leased: loading a 27B model to refuse afterwards wastes
   a minute and a lease.
3. The model is leased through the broker (`harnessing.serving`, unchanged). `harnessing.opened` registers the session and writes
   its settings and brief (unchanged except the hook command, section 7).
4. The stage: `Workspace` is constructed or reopened and `begin()` runs (section 4). A project that cannot be staged raises
   `IsolationRefused` (only when the stage volume lacks the free bytes) naming the in-place grant.
5. The services start: the model bridge, the session bridge, the egress proxy. Each binds before the next step; a service that
   cannot start refuses the launch.
6. `Profile` is built; its digest is recorded.
7. The proof runs (section 10). A deviation raises `IsolationFailed` naming the first attempt that got through. Nothing else starts.
8. The runner is called with the `Confinement`. The runner starts the harness with `Confinement.popen(...)`, which starts the
   wrapped argv and, before returning, attests the live process (section 10.3).
9. During the session the bridges serve, the watchdog counts (5.2 and 5.3, G11), the harness's own hooks call the session
   bridge, and after each completed assistant turn (the harness's `Stop` event in an interactive session, the end of a headless
   run) the parent publishes what verified (4.2 item 1).
10. At exit (or cancel): the harness's tree is ended and the barrier of 4.2 item 3 is passed, `Workspace.finish` publishes the rest under the
    policy guard, the services stop, the stage's physical directory is removed. The activity log records the launch (profile
    digest, grants, proof digest) and the end (exit status, what was published, what was refused).

The proof runs after the lease and immediately before the start, so the interval it covers is seconds, not the minutes a model
load takes.

### 3.4 The environment and the harness settings

The environment is an allow-list built by the launcher; `os.environ` is never copied.

| For | Variables |
|---|---|
| Always | `PATH` (agent-home `bin` shims, then the toolchain directories the profile grants), `HOME` and `TMPDIR` (the agent home), `LANG`, `POOLHOUSE_AGENT` (a marker for the tools the harness runs; the person check never relies on it, 3.5) |
| Interactive only | `TERM`, `COLORTERM`, `NO_COLOR` |
| Claude Code | `CLAUDE_CONFIG_DIR` and `CLAUDE_CODE_TMPDIR` inside the agent home, `ANTHROPIC_BASE_URL` (the bridge), `ANTHROPIC_AUTH_TOKEN` (the bridge token), the model variables, the offline variables and the window variables of `claude.py`, `CLAUDE_CODE_EXTRA_BODY` and `CLAUDE_CODE_EFFORT_LEVEL` where the caller sets them |
| Codex | `CODEX_HOME` (an existing directory), the provider key variable set to the bridge token |
| Pi | `PI_CODING_AGENT_DIR`, `PI_OFFLINE`, `POOLHOUSE_NONINTERACTIVE` |

The table is closed. A variable it does not name does not exist for any harness, whatever the caller's environment, a project file
or a grant holds: a toolchain grant contributes `PATH` entries and no variable, so a toolchain directory whose files name
`SSH_AUTH_SOCK`, `GH_TOKEN` or any other variable changes nothing in the environment. The launcher's own variables
(`POOLHOUSE_*` other than the marker, the node's, the state root's) never enter it.

Facts found by running the harnesses under the existing profile on this Mac (5.6, H-claude and H-codex):

- Claude Code 2.1.308 starts, reads its settings from `--settings`, and tries `/tmp/claude-<uid>`; with no access to it the run
  stops with `EEXIST: file already exists, mkdir '/tmp/claude-501'`. `CLAUDE_CODE_TMPDIR` inside the agent home removes the need.
- It needs `signal` to its own children (it sends `SIGTERM` to a child it started), granted as `(allow signal (target children))`;
  VERIFIED that this still refuses a signal to the launcher and to an unrelated process of the same user.
- It reads `/Library/Application Support/ClaudeCode/managed-settings.json` (refused inside the confinement), so the owner's managed
  sandbox settings (`docs/agent-sandbox.md`) never stack on this one.
- Codex 0.163.0 refuses to start when `CODEX_HOME` does not exist. Its binary lives under the person's home
  (`~/.codex/packages/standalone/releases/<version>-<triple>/`), so the harness's own install tree is a derived, read-only grant:
  the real path of the resolved binary and its release directory, nothing above it.
- Codex's own macOS sandbox is `sandbox-exec`; nested `sandbox_apply` is refused (VERIFIED), so inside this confinement Codex
  runs with its own sandbox off (`--sandbox danger-full-access --ask-for-approval never`, PREDICTED from the verified nested
  refusal; probe P-codex) and the permission profile is enforced by the mount (I13, 8.1). The hook is the gate for what the
  boundary cannot express.
- Claude Code's built-in sandbox is off in the launcher's settings (it is today); `claude` is never given `sandbox.enabled`.
- A shell inside the confinement needs a real `git`: Apple's `/usr/bin/git` is a shim that asks `xcode-select` through a Mach
  service the profile refuses. `PATH` puts the resolved git first; with only Apple's command-line developer tools installed, the profile reads and executes
  `/Library/Developer/CommandLineTools/usr` (a system tree).

### 3.5 Headless and interactive

The chat dialog and native tasks run the harness headless (`claude --print`, `codex exec`) with piped stdio. `Confinement.popen`
returns a `Popen` with those pipes. The terminal commands run it interactive. A full-screen harness sets raw mode on its terminal,
and the existing profile refuses that (VERIFIED: on a pty slave given as standard input `tcgetattr` works and `tcsetattr` fails
with `Operation not permitted`, with `strict` on or off; `TIOCSTI` is refused in all four combinations of `strict` and controlling
terminal). The launcher therefore owns a pty (a master and a slave end) and relays it to the person's terminal; the harness gets the slave as its
standard input, output and error, and the profile allows terminal ioctls on that one device node and no other. Whatever the harness
injects with `TIOCSTI` reaches only its own input, never the person's terminal or the shell that runs after it. That input
belongs to the attacker. A process in the confinement can type into its own slave where `TIOCSTI` is allowed, and it can open a
pty of its own (`/dev/ptmx`), hold the master end and give the slave to a child as a controlling terminal, so it can answer
any question read from a terminal; a descriptor duplicated from its stdio, or a pty it made, passes `isatty()` for stdin and
stdout. Two rules follow, and the second is the one that holds.

1. **No question for a person is read from a terminal the confinement holds.** A hook's ask is a Requests item answered on the
   person's own surface (section 7, `grant`); the relay carries the harness's screen and keys and nothing the parent asks. Where
   the profile language can name an ioctl command, the relay's slave is allowed the terminal-attribute and window-size commands
   and refused `TIOCSTI`; where it cannot, rule 2 alone carries the guarantee and the report says so.
2. **A process the operating system reports as confined is never a person (I17).** `person.require_person` adds a third condition
   to "no agent marker, and a terminal on stdin and stdout": `sandbox.confined()` is false. `person.confirm` and `person.typed`
   return the empty answer at once when it is true, before any terminal is opened. `sandbox.confined()` reads a fact of the
   process that its descendants inherit and none can change: on macOS `sandbox_check(getpid(), NULL, 0)` is 1 (the call 5.2
   already uses to detect a nested sandbox, VERIFIED); on Linux and WSL a marker file the launcher binds read-only into the
   namespace at a fixed path is present, and a process inside cannot remove it; on Windows the process token is an AppContainer
   token or the process is in the launcher's job. A dropped marker variable, a duplicated pty descriptor, `setsid`, a new pty and
   a terminal opened by name all leave it true. The check sits in `require_person` itself, so every caller (the authority
   registry, the Requests inbox, the approval, grant, keystore and wired-memory verbs) is covered, not only the launchers.

PROBE P-tty checks that a profile naming only the relay's slave lets a real full-screen harness draw and read keys, that the
person's own terminal device is refused, that window-size changes pass through the relay, and that text injected into the slave
with `TIOCSTI` answers no `confirm`. Until slice L6 an interactive launch refuses with a sentence that names print mode.

## 4. The parent-owned workspace

### 4.1 What `coding_workspace` provides, read from the code and its tests

| Property | Where | Test |
|---|---|---|
| A private namespace (`poolhouse-coding-*`, mode 0700) under storage whose ancestry is owned and not group- or world-writable, that overlaps neither the project nor the protected roots (`POOLHOUSE_HOME`, `POOLHOUSE_CACHE`, the account roots) | `Workspace.__init__`, `source_snapshot.validate_storage`, `protected_roots` | `test_replaced_source_or_namespace_identity_cannot_be_adopted` |
| A capture of the tracked and untracked-not-ignored files through a private git index: repository config, hooks and filters are never read (`-c core.hooksPath=/dev/null`, a scrubbed environment, `--git-dir` private), and `.git` is never source content | `SourceSnapshot` | `test_parent_index_never_executes_repository_defined_filters`, `test_verified_manifest_index_uses_literal_names_and_leaves_source_git_unchanged` |
| Credentials and control files stay out: `secretpaths` names and `CONTROL_PREFIXES` (`.claude`, `.codex`, `.pi`, `.mcp.json`, `.poolhouse-project.json`) are excluded from every capture, and a tool that creates one is rejected | `_excluded`, `finish` | `test_parent_capture_excludes_credentials_and_preserves_omitted_paths`, `test_whole_control_prefix_is_denied_in_actual_worker`, `test_parent_rejects_even_ignored_new_control_prefix_before_capture` |
| Capture is race-checked: `O_NOFOLLOW`, identity (device, inode, size, times, owner, mode, link count) before and after each read, the inventory and index re-read after, a second walk of the output | `blob`, `_verify_capture`, `_verify_output` | `test_changed_file_bytes_are_rejected_even_with_original_tree_hash`, `test_full_modes_content_digest_and_size_are_verified` |
| Epochs: `begin()` copies the master to a new `tool-<uuid>` directory and rotates the symlink `workspace` to it, so a detached writer of an earlier epoch cannot touch the next stage or the master | `begin` | `test_old_detached_writer_cannot_change_capture_master_or_next_stage` |
| Publication under a policy guard with rollback: the revision is checked inside the guard, a failure on exit restores the previous master and receipt | `finish`, `Conversations.coding_guard` | `test_guard_failure_on_exit_rolls_back_all_parent_publication`, `test_revocation_after_child_returns_rejects_its_captured_edits` |
| A read-only run cannot change its input (`writable=False` compares manifests); a failed tool publishes nothing | `finish`, `run` | `test_actual_read_only_policy_and_parent_gate_both_prevent_publication` |
| Deletions, renames and mode changes are explicit manifest deltas; the person's own concurrent edits to the checkout are untouched | `_changes` | `test_deletions_and_renames_are_explicit_manifest_deltas`, `test_owner_edits_are_preserved_while_private_result_is_captured` |
| Receipts survive a process restart, are verified field by field on reopen, and are single-writer under a process lock | `receipt`, `reopened`, `workspace_state.checked` | `test_coding_workspace_resume` (266 lines) |

### 4.2 What the launcher adds

1. **Session granularity.** The unit today is one tool call (`run(execute, guard, writable)`). A native harness is one process
   tree, so the unit becomes one launch with a publication after each completed assistant turn, in interactive sessions too: `begin()` at step 4 of 3.3, a verified `publish_turn()` at each `Stop` event that reaches the session bridge, and `finish()` at step 10. A turn-end capture does not end the tree, so it is race-checked as `finish` is: a path whose identity changed between the two reads of the capture is held back and reconsidered at the next turn or at exit, never applied unverified, so a background process that still writes delays a path and never corrupts a publication. The base advances to the published state after each turn, so the three-way check of 4.4 compares against the last publication. The `workspace` alias path is the
   harness's working directory, so it is stable across the turns of one conversation (a resumed Claude Code session keys its
   transcript by working directory) when the workspace is reopened and not rebuilt.
2. **The profile gets the stage.** The stage and the agent home are the only write grants. The five control names are
   `deny_write` inside the stage even though they are absent, so a harness that tries to create `.claude/settings.local.json` gets
   an error at the write (VERIFIED on Seatbelt: creating `.claude`, `.Claude` and `CTL/hooks/x` all fail, and so do renaming an
   ancestor of a protected path and hard-linking a protected file into the project), not a rejected session at the end.
3. **A quiescence barrier before `finish()`.** The final capture, at exit, is taken only after the confined tree is gone. The
   barrier ends the session by walking the backend's record of its processes, never by signalling a process group (I18): a
   descendant that calls `setsid()` leaves the group and still writes the stage (F15, VERIFIED: the group kill reached 1 of 255
   and 1 of 585 live descendants, and the follower reported every one). On Linux and WSL the record is the pid namespace: ending
   its init ends every member, and the barrier waits for the init to be gone. On Windows it is the job, ended by terminate. On
   macOS it is the follower's tree (`sandbox.process_tree.Tree`), which follows every fork through `kqueue` whatever its group or
   session, and the barrier is: (1) stop every member (`SIGSTOP`) and list again until a pass adds none, so no member forks while
   the others are ended; (2) `SIGKILL` every member; (3) wait until the follower reports no live member. A member is named by pid
   and start time, so a reused pid is never signalled. Every step is a wait with a hang guard (`docs/design-test-runner.md`,
   "Hang guards: no progress first, a total second"): the no-progress stretch is measured from the step's own progress (the
   number of members still live, the pass that adds none), there is no number in this design, and a step that stops making
   progress ends the barrier with a survivor. When the tree is not complete (`Tree.complete` is false, or the run started more
   processes than `KEPT`), a process the follower never saw could still write the stage, so the barrier also sweeps every process
   of the user for one that is sandboxed (`sandbox_check(pid, NULL, 0)` is 1) and whose profile allows writing the stage's real
   path (`sandbox_check` with the path filter): only this confinement's profile allows that path. PROBE P-sweep checks both
   calls on another pid; until it has run, an incomplete tree is a survivor. A process that cannot be ended makes the launch
   report it and skip publication; capture of a directory something still writes cannot be trusted and `finish` fails closed on
   the identity check anyway. `sandbox/run.py` keeps its group kill for the engine's own runs; the barrier does not call it.
4. **The environment links.** Ignored directories a project needs (`.venv`, `venv`, `node_modules`, and the toolchain a project
   declares) are not in a stage. The parent creates them in the stage as symlinks to the person's own directories, read-only in the
   profile (a read grant of that exact directory). They are ignored by git, so no capture ever sees them (a symlink is not
   source content), and a declaration that does not resolve to a toolchain directory under the rules of 8.2 gets nothing
   from it. On Linux the stage is bound at the person's project path inside the mount namespace, so absolute paths in a virtual
   environment's scripts resolve with no link at all.
5. **Publication to the checkout** (`Workspace.apply`, new): section 4.4.
6. **Commits belong to the parent.** The stage's `.git` is a throwaway repository the parent initialises so `git status` and
   `git diff` work for the agent. Its commits are not carried. A native task that must leave a commit on its branch gets the
   parent's commit of the published change set (`task_coding._proposal` reads `HEAD`); the task prompt stops saying "commit".
7. **Display paths.** The agent sees `.../workspace/src/x.py`; `coding_events` rewrites the stage prefix to the project path in
   what the person reads.

### 4.3 What is wrong or missing in `coding_workspace` for this threat model

| # | Finding | Change |
|---|---|---|
| W1 | Too slow to launch (F4), a defect of the build that is fixed in the build: 16.0 s, 9.2 s, 19.3 s on a 40 MiB project; the launch budget is 5 s (AGENTS.md section 5, "A run's fixed cost is budgeted"). | Validate each distinct parent directory once per capture (cache by device and inode); match secret names on names, once; one `git ls-files` set; clone the master into the stage (`clonefile` on APFS, reflink where the filesystem has it, a copy otherwise). A test bounds `begin()` and `finish()` on a fixture of the size of this repository to 5 s, and to the time of `cp -R` plus hashing the same files, measured in the test. |
| W2 | POSIX-only primitives and a macOS git that must be Homebrew's (F5, F6). | Accept the git of Apple's command-line developer tools (`/Library/Developer/CommandLineTools/usr/bin/git`, root-owned Mach-O, ancestry checked), never the `/usr/bin/git` shim. A Windows capture uses `CreateFile` with `FILE_FLAG_OPEN_REPARSE_POINT`, file ids and link counts; until it exists the Windows tier stages inside WSL2 (5.4, route A). |
| W3 | One tracked symlink, one group-writable file, one hard link or one submodule fails the whole capture (F6). | Classify, do not abort. A tracked symlink whose target is relative and stays inside the tree is carried as a symlink and listed; any other is left out and listed. A group-writable file is copied with the group and world write bits cleared and listed. A hard-linked or foreign-owned file is left out and listed. A submodule is left out and listed. A FIFO, socket or device node is left out and listed by name (F18). The list is shown in the first line of the session ("N files left out of the working copy: ..."); a path is never left out without a name in it. |
| W4 | Bounds with no source (F7). | Remove them: there is no project size ceiling and no per-file cap, and the only size bound is the stage volume's free bytes (17), a fact of the machine. The 30 s deadline goes too: the build is judged by progress (files visited and bytes advanced), not by a clock, so a slow disk is not a failure and a stalled one is (the hang guard of 17). Sparse files are cloned as they are and the volume check counts allocated blocks, so a sparse file whose apparent size exceeds the volume's free bytes still stages; hashing it advances the progress counters. Tests: a sparse file whose apparent size exceeds the volume's free bytes stages; a tree of small files whose count is read from the volume's free inodes advances its progress counters until it finishes. |
| W5 | The stage has no ignored files (F8). | 4.2 item 4. |
| W6 | `finish` is all or nothing: one control path discards a whole session. A harness writes its own `.claude` in the project when nothing stops it. | Control names are denied at write time (4.2 item 2); at publication a control path, credential name or special file in the stage is **left out and reported**, and the rest is published. The session is not discarded for what the profile already refuses. A control path that appears despite the profile is an escape of the profile and is reported to the sentinel. |
| W7 | No apply (F9). | 4.4. |
| W8 | A CLI session has no conversation, so no receipt store for `reopened`. | The CLI session uses an ephemeral workspace (no receipts; the guard checks the registered project and the permission snapshot only). |
| W9 | `finish` captures a directory that may still be written (T3, T15). | 4.2 item 3. |
| W10 | The stage's `.git/index` is agent-controlled input to the parent's git. | Already validated (size, signature, owner, mode, link count, every path through `path()`); the design adds a test that a hostile index naming `..`, `.git/x`, an absolute path or a 4096-byte name changes nothing. |
| W11 | The in-place workspace has no equivalent of the capture. | 4.5. |
| W12 | `guard.autoexec` is a name table (F14); publication needs a rule that holds what it does not know. | 4.4.1 and 4.4.2: `guard.runs_later` classifies by structure, deny by default; the tables are deleted. |
| W13 | `apply` reads the live file, decides, and writes later (N7): the person's save between the two is overwritten. | 4.4.3: the swap is verified on the displaced version and undone when it changed; no version is deleted unverified. |

### 4.4 Publication: what the parent does with a stage

`Workspace.finish` yields `changes` (path, before and after digests, before and after modes). Publication (`Workspace.apply`)
classifies each from bytes, modes and names only, never from what produced them, and it is **deny by default**: a change is
applied without a question only when it is plain source (4.4.1); every other change is held.

| Class | Rule | Action |
|---|---|---|
| Plain source, added, changed or deleted | passes all four tests of 4.4.1 | applied (4.4.3) |
| Held | fails any test of 4.4.1: build, CI, hook, package-manager, wrapper, lockfile, configuration, data, agent-instruction and review-governance files, programs, binaries, anything unrecognized, anything whose facts cannot be read | one batched question to the person per publication with the diff of each, as a Requests item of kind `tool_call_destructive`; answered no, the file is not applied and the rest is |
| A file git does not hold, overwritten or deleted | the checkout is a git repository and the path is untracked | asked with the rest (it cannot be restored) |
| Control path, credential name | `CONTROL_PREFIXES`, `secretpaths` | never applied; reported |
| Special file | FIFO, socket, device, set-uid or set-gid mode | never applied; reported by name |
| New symlink | relative, target inside the tree | applied and listed; any other: never applied, reported |
| Name collision | two paths equal under case or Unicode folding (APFS and NTFS fold) | never applied; reported |

The question is one Requests item per publication. It shows every held file with its diff (a new file in full), the sentence of
the test that held it ("a hidden configuration file", "a program", "data or configuration: the tool that reads it decides what it
means", "an instruction file for a coding tool", "named by `Makefile`, which runs it"), and the current contents of every file a
held change newly names. The answer is bound to each path and its result digest, so the same bytes are never asked twice in a
session and a changed byte is asked again; the fatigue ledger applies (14.2). The hook still asks for what the boundary cannot
express (8.4); it does not ask per call about writes to held files, which the publication question covers once with the whole
diff in view.

#### 4.4.1 Plain source

A change is plain source when **all four** tests hold. A fact the parent cannot read is a failed test, so a missing fact holds
the file.

1. **Shape.** A regular file with one link, valid UTF-8 text with no NUL byte, no executable bit and no set-id bit, no shebang,
   and no leading bytes of an executable, archive or object format (ELF, Mach-O, PE, Java class, zip and its jar, war and apk
   forms, gzip, tar, WebAssembly, Python bytecode). A file that is not text is a binary and is held; so is anything executable.
2. **Position.** No component of the path starts with a dot. A tool that reads project configuration without being asked reads a
   dot name or a dot directory (`.github/`, `.husky/`, `.claude/`, `.vscode/`, `.lfsconfig`, `.bazelrc`, `.prettierrc.js`,
   `.windsurfrules`, `.aider.conf.yml`, `.continue/`, `.clinerules`, `.github/CODEOWNERS`), so the structure holds them all,
   named or not.
3. **Kind.** The extension (the last suffix, case folded) is one the language registry snapshot (4.4.2) types as a programming
   language, markup or prose, and the basename is not one the registry assigns to a language by name. A file with no extension,
   a data type (JSON, YAML, TOML, XML, INI, CSV, lock files, `.props`, `.csproj`, `.sln`) or an extension the registry does not
   know is held: what a data or unrecognized file means is decided by the tool that reads it, which structure cannot see. The
   registry assigns by name `Makefile`, `Dockerfile`, `Jenkinsfile`, `Tiltfile`, `WORKSPACE`, `Rakefile`, `Gemfile`, `Pipfile`,
   `requirements.txt` and the like, so a file with such a name is held whatever its content. The basename is also not an
   instruction name of a harness record or an implicit-load name of an ecosystem record (4.4.2): `AGENTS.md`, `CLAUDE.md`,
   `GEMINI.md`, `conftest.py`, `sitecustomize.py`, `setup.py` and `build.rs` are held at any depth.
4. **Wiring.** No held file of the result tree names it. The result tree is the checkout as it will be after publication. The
   parent reads every held text file of it, takes each path the file contains (a maximal run of path characters that resolves,
   relative to the project root or to that file's directory, to a file the result tree holds), and follows the paths found in
   each file reached until no new file is named. Every file reached is held: `scripts/build.py` named in `package.json` or a
   Makefile, a `.mk`, `.cmake` or `.bzl` file a build file includes, a script a workflow runs.

#### 4.4.2 Where the held set comes from

No list of auto-run names is kept by hand. The held set is the complement of plain source, computed from five inputs, each of
which stays current by its own means:

| Input | What it supplies | How it stays current |
|---|---|---|
| Structure of the path and the file | tests 1 and 2: dot components, binary and archive magic, executable and set-id bits, shebang, non-text | computed from the file; there is nothing to keep |
| The project's own wiring | test 4: the files its held files name | computed from the result tree at each publication; a build tool a project adopts names its files in its own held files |
| The language registry snapshot | test 3: the extension types and the names the registry assigns to a language (`contracts/language-kinds.json`) | generated from GitHub's public language registry (`languages.yml`) by `scripts/refresh_language_kinds.py`, which records the registry revision and a digest; the nightly CI job runs it and fails when the committed snapshot differs, so a registry change is a red job and a reviewed diff and never a silent drift |
| The harness records | the instruction and settings names of every harness Poolhouse launches (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `.mcp.json`, `.claude/`, `.codex/`, `.pi/`) | each harness module's record holds them beside the argv it builds, so adding a harness adds its names, and a contract test fails a harness record with none. The instruction files of a tool Poolhouse does not launch (Copilot, Windsurf, Aider, Continue, Cline, Cursor) sit in dot names and are held by test 2 |
| The ecosystem records | the names a toolchain loads from an ordinary source position (`conftest.py`, `sitecustomize.py`, `usercustomize.py`, `setup.py`, `noxfile.py`, `build.rs`, `*.config.js`), in the record that already holds the toolchain's declarations and ignored directories (4.2 item 4, 8.2) | the oracle below runs the real tool, removes a name it does not run and fails a name it runs that no record holds |

The oracle is `tests/test_publication_oracle.py`. For each ecosystem whose tool is on the machine (Python with pytest, a
JavaScript runtime with npm, cargo, make, git) it builds a scratch project with a marker-writing payload at every candidate name
of the registry snapshot, runs the tool's offline entry points inside the platform's confinement, and records which names left a
marker. Each such name must be held by `guard.runs_later`, by structure, the registry, a harness record or an ecosystem record; one
that is not fails the test and is added to the record of its ecosystem. The oracle measures what tools do; the rule never reads
its output at run time.

The coverage tests are `tests/test_publication_rule.py`. Every name of the corpus is held: the names the non-author pass tried
(N3 to N5 of `docs/design-isolated-launcher-breakit.md`), the registry snapshot's special names and the oracle's findings. Every
`.py` file of this repository's `src/` and `tests/` that no held file names is plain source, so a rule that holds everything
fails as surely as one that holds too little. What the rule does not see is R8: a file a held file reaches only through a path it
computes.

`guard.autoexec` keeps `write_findings` and the scan of what a written text downloads or runs. Its name tables (`RUNS`,
`FOLDERS`, `FILES`, `SUFFIX`, `CONFIGS`) are deleted: `path_reason` and `write_findings` call `guard.runs_later.classify` with the
facts they have, and a fact they lack (no bytes, no mode) holds the file. The classifier is one function in one place, shared by
publication, by the in-place capture (4.5) and by the hook.

Review governance has no name of its own in the rule. `CODEOWNERS` has no extension (test 3) or sits in `.github/` (test 2), so
a session cannot remove itself or a reviewer from the file that decides who reviews a later change in the batch that lands the
change it wants unreviewed: the question lists it with the rest, and a no leaves it as it was.

#### 4.4.3 Applying one path

Writing into the checkout is the part with the sharpest edge, because the parent acts with the person's authority on paths the
agent chose, and the person's editors, formatters and build tools write the same files while it does. The agent chooses when a
turn ends and so when publication starts; it never touches the checkout, so the one concurrent writer is the person. The rule is
that **the parent never deletes or overwrites a version of a file it has not verified equal to the base or to its own result**.

- Every write opens the parent directory by file descriptor and each component with `O_NOFOLLOW`, never by path string; a symlink
  anywhere in the live path (`a/` is a symlink to `~/.ssh`) refuses that path and reports it.
- A file is applied in five steps. They pin the read of the live file to the write, because the person can save the same file
  between any two operations.
  1. **Read and hold.** Open the live file with `O_NOFOLLOW` and keep the descriptor to the end. Record its identity (device,
     inode, size, modification time, change time, mode) and its digest. The three-way check compares base (the manifest the stage
     started from), live (now) and result (the stage). Live equals result: nothing to do. Live equals base: continue. Otherwise it
     is a conflict: the live file stays, the agent's version is kept under the state root for the session and the path is named in
     the end-of-turn message.
  2. **Prepare.** Write the result to a temporary name in the same directory (`O_EXCL`, `O_NOFOLLOW`), with the stage's mode and
     the group and world write bits cleared, flush it and check its digest. Keep a descriptor to it.
  3. **Swap.** Exchange the temporary name and the live name in one operation: `renameat2` with `RENAME_EXCHANGE` on Linux,
     `renamex_np` with `RENAME_SWAP` on macOS, `ReplaceFileW` with a backup name on Windows. The live name now holds the result;
     the temporary name holds the version that was live.
  4. **Verify the displaced version**, through the descriptor held since step 1 and the temporary name: the device and inode, the
     size, the modification and change times and the digest equal what step 1 recorded. The kernel sets the change time on every
     write, rename and mode change, so a save in place after step 1 shows in it, and a save that replaced the file by rename shows
     as a different inode.
  5. **Finish or undo.** Verified: unlink the temporary name; the displaced bytes equal the base, which the master holds. Not
     verified: the person saved after step 1. When the live name still holds the file step 2 made, exchange back, so the person's
     newest version is under the live name again; keep the agent's version under the state root and name the path. When it does
     not, the person saved again during the undo: change nothing more, rename the displaced version to a conflict name beside it,
     and name both.
- A path the filesystem cannot exchange (`ENOTSUP`, `EINVAL`) is applied by claim: the live file is renamed to a private name in
  the same directory, verified there as in step 4, and the result is installed with a rename or link that never replaces
  (`EEXIST` when the person re-created the path in between: the person's new file stays and the result is kept under the state
  root). The path is absent between the two operations; the end-of-turn message says which method each path used.
- A deletion is the same claim with no result: the live file is renamed to the private name, verified, then unlinked; not
  verified, it is renamed back with a rename that never replaces. A new file is installed with a rename or link that never
  replaces: `EEXIST` is a conflict.
- A record of the intended writes (an intent log) is written before the first one, with each path's base and result digests and
  the step reached, so a crash leaves a recoverable half-applied state that the next launch of the same project completes or rolls
  back by the same verification, and reports.
- The flush and the verification read are steps with a hang guard on their progress (bytes read, paths finished), as in 4.2 item 3.

### 4.5 In place

`--workspace in-place` (a person's flag; refused when the launcher runs under an agent marker or inside a confinement, 8.3) gives the session the person's
checkout itself, with the same control names `deny_write` (Seatbelt: VERIFIED; Linux: a read-only bind over each existing control
path and no way to protect a name that does not exist without leaving a placeholder file in the checkout, VERIFIED, so on Linux the
five names are created empty and read-only by the launcher and removed at the end if they were absent; Windows: a deny ACE for the
container). Credentials in the project (`.env`) are readable, and the grant's sentence says so. There is no publication step; the
parent captures the checkout before and after and asks about every change that 4.4.1 would hold afterwards (the same
`guard.runs_later.classify`, 4.4.2), which is detection and not prevention, and says so in the same sentence. The grant is logged (8.2). It covers one project, and a session never has a second writable project (8.2). It is never
chosen by the launcher and never a fallback for size.

## 5. Enforcement per platform

### 5.1 The thirteen guarantees

The capability report (section 9) names these. A launch needs all thirteen. Two of them (G10, G11) may be **watched** (the parent
enforces by watching, and the report says so, resource by resource for G11); no other may. A watcher counts only if it kills the session loudly on crossing a limit derived from the machine (17) and names the limit; a watcher that only logs or warns is `unenforceable`. What a guarantee does not cover is an **exposure** (5.7): a guarantee is something the confined process cannot do, so a fact the platform cannot hide is never worded as a guarantee that holds.

| Id | Guarantee |
|---|---|
| G1 | Nothing is readable outside the stage, the agent home, the harness's install tree, the runtime interpreter that runs the bridge client, the system toolchain, the derived toolchain directories and the granted reads. |
| G2 | Nothing is writable outside the stage and the agent home. |
| G3 | A control name (`.claude`, `.codex`, `.pi`, `.mcp.json`, `.poolhouse-project.json`) cannot be created or changed in the stage; and independently of any platform, nothing that is not plain source reaches the checkout without the person's answer (4.4). |
| G4 | The only network reachable is the model bridge, and the egress proxy for granted hosts. |
| G5 | No unix, abstract or other socket is reachable except the session bridge. |
| G6 | No process outside the session can be signalled or traced, or have its arguments, environment, memory, working directory or open files read. That a process exists (its name, pid, parent pid and user id) is an exposure on macOS (X1, 5.7) and is hidden by the pid namespace on Linux and Windows route A. |
| G7 | The person's terminal and desktop cannot be driven (`TIOCSTI`, `/dev/tty`, Apple events, `open`, clipboard, UI automation). |
| G8 | No credential store is reachable (keychain, credential manager, keyring, browser profiles, the harness's own login). |
| G9 | No GPU, camera, microphone or input device. |
| G10 | The session's processes end with the session, and nothing is scheduled to run after it. |
| G11 | Process count, memory, descriptors, CPU and disk are bounded. |
| G12 | No new user namespace or nested sandbox can be entered to regain capabilities. |
| G13 | No hardware or machine identifier is readable: serial number, hardware UUID, provisioning UDID, machine id, network hardware addresses. The proof reads the identifiers itself in the parent and searches everything the probe can obtain for them (P22). |

### 5.2 macOS

**Mechanism.** Seatbelt through `/usr/bin/sandbox-exec -p <profile>`, generated by `sandbox/seatbelt.py` from a `Policy`. On this
macOS (26.6, 25G70) `sandbox-exec(1)` says "DEPRECATED", the header `sandbox.h` says "deprecated and may be removed in a future
release", and Apple's own `.sb` profiles are still in `/System/Library/Sandbox/Profiles`. There is no command-line replacement:
Apple's app-sandbox feature needs a signed bundle with entitlements, Apple's `container` runs Linux virtual machines without Metal, and Endpoint
Security needs an entitlement Apple grants. The owner's exception is recorded in `docs/sandbox.md`. The profile language is not a
public interface, so the proof (section 10) is what notices a change.

**The harness profile** is the existing `Policy` plus five deltas. Everything else is the engine as it is:

| # | Delta | Reason |
|---|---|---|
| D1 | `(deny process-info-pidinfo)` in every profile, not only this one | closes F1 (being fixed now on `fix/sandbox-holes-and-harness-denial`, not part of this build); VERIFIED that it makes `KERN_PROCARGS2` fail for the launcher and for another process of the same user, that denying the sysctl names, `process-info*` for other targets or all `sysctl-read` does not, that Claude Code still starts, and that `psutil` on the process itself (`memory_info`, `cpu_times`, `cmdline`, `environ`) works the same with and without it; the engine's own suites run against it in L1 |
| D2 | `signal_children`: `(allow signal (target children))` | a harness ends its own children; VERIFIED that signals to the launcher and to an unrelated process of the same user stay refused |
| D3 | `Net.only(bridge_port)` plus the proxy port, and `unix_sockets=(session socket,)` | G4, G5 |
| D4 | `strict=True`, `deny_write` of the five control names and their ancestors' unlink, `file-link` and `file-clone` denied | G3; already in the engine, used by `pi_tools` |
| D5 | `RLIMIT_NPROC` set in the child before it executes, as `sandbox/run.py:_limits` sets `RLIMIT_CPU`, `RLIMIT_FSIZE` and `RLIMIT_NOFILE`, to the user's live process count at launch plus the session's share (17) | G11 process count, enforced by the kernel, which refuses the fork past the limit with `EAGAIN`; being fixed now on `fix/sandbox-holes-and-harness-denial`. VERIFIED by the non-author pass (N8): with the limit at the current count plus 40, a 300-fork loop stopped with `EAGAIN` in every run, and with no limit completed all 300. The count is the user's, so other work by the same user uses part of the allowance and the session fails earlier, never later; the person's own processes carry no limit |

The operations that close F12 and F13 are whatever the engine branch lands and L1 re-runs; this design states the guarantee, the
exposure and the probe (G13, X1, P22, P23), not the operation. Until the branch shows an operation that makes P22 find nothing,
macOS reports G13 `unenforceable` and a macOS launch refuses with its sentence (9); it never starts with the identifiers readable.

**Guarantees:** G1 to G5, G7 to G9, G12 enforced (evidence 5.6); G6 enforced for signals, tracing and the reads of arguments,
environment, memory and open files, with the process list an exposure (X1, 5.7); G13 enforced when P22 passes and
`unenforceable` otherwise; G10 watched; G11 enforced for the process count (D5) and for CPU, file size and descriptors (rlimits),
and watched for memory and disk (below). **Does not guarantee:** a Seatbelt or kernel bug; a memory ceiling (neither `RLIMIT_AS`
nor the Mach per-task footprint limit stops a 500 MB allocation, VERIFIED by the non-author pass), so memory stays watched; that a
process reparented to launchd (a double fork that the follower missed) is ended: it stays confined and has lost the bridges and
its stage, the barrier reports it (4.2 item 3) and publication is skipped; that the refusal log is complete (it is advisory,
`docs/design-fail-closed-harness.md`).

**Watched (G10, G11).** The follower (`sandbox/process_tree.py`) names the session's processes; the parent ends the session by the
barrier of 4.2 item 3 (a walk of the tree, never a process-group kill) and reports when the stage or the agent home passes the
disk bound or when the confinement's resident memory passes the memory bound (both derived in 17, from the machine and the number
of live confinements). Disk and memory are sampled at the interval 17 derives. A crossing ends the session and names the limit
that was passed; it is never silent. The process count is not watched: the kernel refuses the fork (D5), and the follower's count
names the processes in the failure message.

**Already inside a sandbox.** A launcher that is itself sandboxed (an agent session under `docs/agent-sandbox.md`) cannot apply a
profile (VERIFIED, nested `sandbox_apply` refused). `sandbox_check(pid, NULL, 0)` from `libsystem_sandbox` answers 0 for an
unsandboxed process and 1 for one under `sandbox-exec` (VERIFIED), so the launcher detects this and refuses with a sentence. The
same call is `sandbox.confined()` on macOS, which `person.require_person` reads so that no process in a confinement is a person
(3.5).
`scripts/agent_sandbox_profile.py` adds `poolhouse-claude` and `poolhouse-codex` to the managed `sandbox.excludedCommands`, so an
agent in the owner's sandbox can run them. They run unsandboxed as the person only until they confine themselves at their first
step; their arguments come from the agent, so 8.3 applies to every flag (T17).

### 5.3 Linux and WSL

**Mechanism.** Bubblewrap with `--unshare-user --unshare-ipc --unshare-pid --unshare-net --unshare-uts --unshare-cgroup-try
--disable-userns --cap-drop ALL --die-with-parent --new-session --clearenv`, a fresh `/proc`, a minimal `/dev`, a `tmpfs` on
`/tmp`, read-only binds for the toolchain, a read-write bind of the stage (at the person's project path) and of the agent home,
a bind of the bridge sockets' directory, and a read-only bind of the launcher's marker file at a fixed path, which is what
`sandbox.confined()` reads (3.5). Inside, a small helper (`isolation.enter`) sets `no_new_privs`, applies Landlock
and a seccomp filter where the kernel has them, starts the in-sandbox forwarder of 6.2, and runs the harness as its child.
Landlock and seccomp are extra layers: the namespaces are the boundary.

**Why bubblewrap and not Landlock alone.** Measured on a Linux 7.0.14 kernel with Landlock ABI 8, as an unprivileged user
(VERIFIED, 5.6 L-landlock):

| Landlock does | Landlock does not |
|---|---|
| refuse read and write outside the granted trees, rename out, links across directories, symlink-out reads | let a read-write tree have a read-only subpath (the rules only add): `ctl/config` under a granted project stayed writable |
| refuse TCP connect to any port not named, and TCP bind (ABI 4) | restrict a pathname Unix socket (a connect to a socket outside the granted trees succeeded; that needs ABI 9) or UDP (a send to loopback succeeded; that needs ABI 10) |
| refuse abstract-socket connect and signals to the parent (the ABI 6 scopes), and `ptrace` of the parent; refuse `/proc/<pid>/environ` and `mem` of processes outside its domain | hide the process list: with `/proc` granted, every process and its `cmdline` is readable |

No kernel measured has ABI 9. Landlock cannot be the boundary for G3, G5 or G6; the namespaces are.

**Guarantees:** G1 to G9, G12 enforced; G6 includes the process list, which the pid namespace hides (B-ns); G13 enforced by G1,
because `/sys/class/dmi/id`, `/etc/machine-id`, `/var/lib/dbus/machine-id` and the host's network interfaces are absent from the
namespace, which has `lo` only (PREDICTED; PROBE P-ident reads each on the Ubuntu and the WSL devices, where the virtual machine
shows its own identifiers); G10 enforced by the pid namespace (the harness's init is bubblewrap's child, and `--die-with-parent`
ends it with its supervisor); G11 for the process count by `RLIMIT_NPROC` set in `isolation.enter` to the user's count at launch
plus the share of 17 where the kernel counts it per user namespace (PROBE P-nproc), or by `pids.max` under a delegated cgroup v2
(PROBE P-cgroup), and watched until one of them is shown; memory and disk watched unless the cgroup gives `memory.max`.
**Does not guarantee:** a kernel or bubblewrap bug; anything on a project inside `/mnt/c` (requirements below); that seccomp is on
(it is added when the PROBE P-seccomp shows the harness and its tools run under the filter). **Exposure X3** (5.7): the read-only
`/etc` files the profile binds (`passwd`, `group`, `hosts`, `resolv.conf`, `localtime`, `ssl`, `ld.so.cache`).

**Requirements** (the capability check reads each; a failure is the refusal sentence):

| Need | Check | Status |
|---|---|---|
| bubblewrap with `--disable-userns` and `--cap-drop` | `bwrap --version` and a probe run; 0.9.0 has both (VERIFIED); 0.9.0 has no `--overlay` | Ubuntu 24.04 ships 0.9.0 |
| unprivileged user namespaces allowed | the namespace probe the backend already runs (3 s, cached by binary identity and user namespace) | WSL: the launcher already checks it (`docs/windows-runtime.md`) |
| Ubuntu desktop (not WSL): `kernel.apparmor_restrict_unprivileged_userns` | read it; when 1, `bwrap` fails with `setting up uid map: Permission denied` | Poolhouse ships an AppArmor profile for the one `bwrap` binary that allows it `userns`, and installs it at setup with the single administrator prompt (the profile and its scope are below); without it the launch refuses and names the one-line fix (section 9). PREDICTED; PROBE P-ubuntu |
| WSL2 virtual-machine architecture, project inside the WSL filesystem | the kernel string; the project's mount type; a project under `/mnt/c` is refused: DrvFs has no owner or mode, so G2 and the capture cannot hold | PREDICTED; PROBE P-drvfs |
| Landlock ABI, seccomp | `landlock_create_ruleset(NULL, 0, VERSION)` (VERIFIED returns 8 here); WSL's kernel enabled Landlock in release 5.15.57.1 (Microsoft's kernel release notes) | PROBE P-wsl reads the WSL device's ABI |

**The AppArmor profile.** `kernel.apparmor_restrict_unprivileged_userns=1`, the Ubuntu 24.04 default, lets a process create a
user namespace only under a profile that grants `userns`. Poolhouse satisfies it with a profile for the one `bwrap` binary and
never by changing the sysctl. The profile is shipped in the repository as package data
(`src/poolhouse/isolation/apparmor/poolhouse-bwrap`), in the form the distribution documents for allowing user namespaces to one
program (PREDICTED; PROBE P-ubuntu):

```
abi <abi/4.0>,
include <tunables/global>

profile poolhouse-bwrap @BWRAP@ flags=(unconfined) {
  userns,
}
```

- **Scope.** `@BWRAP@` is replaced at install time by the literal, absolute, real path of the `bwrap` binary the capability check
  resolved (a root-owned file whose ancestors are not group- or world-writable). The attachment is that one path: no glob, no
  `/usr/bin/*`, no profile for a shell, an interpreter or a harness binary. `flags=(unconfined)` adds no restriction and removes
  none; the one permission is `userns`. It is granted to the `bwrap` process; inside the confinement the refusal of a new user
  namespace is `--disable-userns` and the empty capability set (G12), not the profile, and PROBE P-ubuntu confirms that
  `unshare --user` fails there with the profile installed.
- **Never a sysctl.** The installer, the uninstaller, the launcher and the capability check never write
  `kernel.apparmor_restrict_unprivileged_userns` or any other sysctl, nor a file under `/etc/sysctl.d`. Turning the restriction
  off hands unprivileged user-namespace creation back to every unconfined program of the host, which is what the restriction is
  there to stop.
- **Installed by a person.** `poolhouse security sandbox install` writes the profile to `/etc/apparmor.d/poolhouse-bwrap` and
  loads it with `apparmor_parser`, behind the operating system's one administrator prompt (the dialog pattern of
  `docs/compromised-agents.md` 2.1). It refuses every agent marker, every confined process and any process without a terminal,
  as `fleet.autostart install` does (`docs/agent-policy.md`, "System settings and the authority registry"). `poolhouse security
  sandbox uninstall` unloads and removes it.
- **Checked at each capability check.** The profile is loaded, its attachment is the resolved `bwrap`, and the installed file
  equals the shipped file with the path substituted. A file that differs, or a profile that attaches to anything else, is the
  refusal sentence of section 9 for a changed permission; the launch never starts on it.
- **Tested** (`tests/test_apparmor_profile.py`; first red on the parent, where the file does not exist): the shipped file parses to
  exactly one profile whose attachment is a single absolute literal path ending in `bwrap` with none of `*`, `?`, `[` or `{`, whose
  flags are `unconfined` alone and whose body is `userns` and nothing else (no `capability`, `mount`, `ptrace`, `network`, `file`
  or `signal` rule); `apparmor_parser` accepts it where it is installed; the installer is a plan function (`install_plan`) whose
  steps are exactly "write this file, load it", and the test asserts the plan names no sysctl, no other path and no other command;
  and on the Ubuntu CI job that keeps the restriction on (13.3), `bwrap` makes its namespaces after the install while `unshare
  --user true` from a shell still fails. A global toggle fails that last assertion and a broad attachment fails the parse.

The Docker container used by `scripts/test-on-linux` is not a place these tests can run: its default seccomp and masked `/proc`
refuse user namespaces (the measurements above needed `seccomp=unconfined` and `systempaths=unconfined`). They run on the WSL
device and on a CI job that turns the AppArmor restriction off, and the profile's scope test runs on a second job that keeps it
on (section 13).

### 5.4 Windows

Two routes, tried in this order.

**Route A, the harness inside WSL2.** The Windows `poolhouse` already runs its daemon and model backend inside Ubuntu on WSL
(`docs/windows-runtime.md`). A coding agent whose project lives in the WSL filesystem is a Linux session (5.3) in that
distribution; Windows adds nothing but the check that the distribution is WSL2 and the bubblewrap probe passes.

**Route B, a native Windows harness** (`claude.exe`, `codex.exe` started from Windows). Claude Code itself runs commands
unsandboxed on native Windows (its documentation); Codex ships an `elevated` mode (dedicated low-privilege sandbox users and an
outbound firewall rule, administrator-approved setup) and an `unelevated` mode (a restricted token, ACLs, "weaker network
isolation" that relies on environment-level offline controls; its documentation). The second is not enforcement of G4 and is not
accepted. Native Windows here is:

| Part | Mechanism | Needs administrator | Status |
|---|---|---|---|
| Token | an AppContainer with no capabilities (`CreateAppContainerProfile`, one fixed profile name per user; `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES`), so the user profile, registry and other processes are default-denied | no | PREDICTED; PROBE P-win-token |
| Files | an ACE for the container SID on the stage, the agent home and each granted read; system directories already allow `ALL APPLICATION PACKAGES`; ACEs are removed at the end, and a stale ACE for a random container SID is inert | no (the person owns the directories) | PREDICTED; PROBE P-win-acl |
| Processes | a job object: kill on close, active-process limit, job memory limit, CPU rate, UI limits (no clipboard, no desktop switching, no handles to outside windows), no breakaway; the process is created suspended, assigned, then resumed | no | PREDICTED; PROBE P-win-job |
| Desktop | a private desktop (`CreateDesktop`) | no | PREDICTED |
| Network, G4 | an AppContainer without the network capabilities has no network, loopback included (Microsoft: loopback is restricted by network isolation). Loopback is opened per container with `CheckNetIsolation LoopbackExempt -a -n=<container>`, which opens every loopback port, not one. A per-port rule is a filter for the container SID in the Windows Filtering Platform (user mode, no driver) or a firewall rule scoped to the container. | **PROBE P-win-loopback: whether either needs elevation and whether the filter can name one port** | UNVERIFIED |
| Git, shell | Claude Code on Windows needs Git for Windows; its tree is a derived read-and-execute grant | no | PREDICTED |

Route B refuses, loudly, until an enforceable per-port loopback rule is shown (P-win-loopback): "this Windows computer cannot confine
the network of a coding agent to the model" and, when WSL2 is present, it names route A. The WSL2 route (A) is supported. There is
no silent weaker mode: no Codex `unelevated` mode, no loopback exemption of every port, no start with the network unconfined.
Route B is built only after the probe has shown such a rule; the capability report then says "not set up" until its one-time
setup ran, and the proof still must show a non-bridge loopback port refused.
The mechanisms above are written for a Python parent with `ctypes` and the `pywin32` that `windows_private.py` already imports.

Considered and not used: a low integrity level alone (it blocks writes to higher-integrity objects and leaves reads open, so G1
fails); a restricted token (it can deny reads, but only by editing the ACLs of everything the harness must read, and it says
nothing about the network); Codex's `unelevated` mode (above). All PREDICTED from Microsoft's documentation; PROBE P-win-token.

Names the Windows stage must handle (W2 in 4.3): reparse points and junctions as links, 8.3 short names (`CLAUDE~1`) of a
protected name, alternate data streams (`file:stream`, already refused by `checked_path`), reserved device names, trailing dots
and spaces, and case-insensitive collisions at publication.

### 5.5 The report: guarantee by platform

| | macOS (Seatbelt) | Linux and WSL (bubblewrap) | Windows route A | Windows route B |
|---|---|---|---|---|
| G1 read | enforced, VERIFIED | enforced, VERIFIED | as Linux | enforced, PREDICTED |
| G2 write | enforced, VERIFIED | enforced, VERIFIED | as Linux | enforced, PREDICTED |
| G3 control names | enforced, VERIFIED (absent names, case aliases, ancestors, links) | enforced for existing paths, VERIFIED; absent names leave a placeholder (VERIFIED), so the stage plus 4.4 carries it | as Linux | PREDICTED |
| G4 network | enforced, VERIFIED (TCP, UDP, IPv6, bind) | enforced, VERIFIED (net namespace, bridge by bound socket) | as Linux | unenforceable until P-win-loopback |
| G5 sockets | enforced, VERIFIED | enforced, VERIFIED (host paths absent); abstract sockets are per net namespace, PREDICTED | as Linux | PREDICTED |
| G6 processes | enforced for signals, tracing and the reads of arguments, environment, memory and files after D1, VERIFIED; the process list is **exposure X1** | enforced, VERIFIED (pid namespace), the list included | as Linux | PREDICTED |
| G7 terminal, desktop | enforced, VERIFIED; interactive relay PROBE P-tty | enforced, VERIFIED (`--new-session`) | as Linux | PREDICTED (private desktop, job UI limits) |
| G8 credentials | enforced, VERIFIED (no home, Security server refused) | enforced by G1; keyring bus socket absent, PREDICTED | as Linux | PREDICTED |
| G9 devices | enforced, VERIFIED (no Metal device) | enforced; GPU nodes bound only by grant | as Linux | PREDICTED |
| G10 lifetime | **watched** (follower tree walk; launchd and cron refused, VERIFIED) | enforced (pid namespace) | as Linux | enforced, PREDICTED (kill on close) |
| G11 resources | process count **enforced** (D5, VERIFIED); CPU, file size, descriptors enforced (rlimits); memory and disk **watched** | process count by `RLIMIT_NPROC` where P-nproc shows it per namespace, else **watched**; memory and disk **watched**, enforced with a delegated cgroup, PROBE | as Linux | job limits enforced for processes, memory, CPU; disk watched |
| G12 nesting | enforced, VERIFIED | enforced after `--disable-userns` and `--cap-drop ALL`, VERIFIED | as Linux | not applicable |
| G13 identifiers | **unenforceable until P22 passes** (F13: serial number, hardware UUID and provisioning UDID are readable today, VERIFIED); enforced on the engine branch's result when P22 finds nothing | enforced by G1 (namespace has no `dmi`, machine-id or host interfaces), PREDICTED; PROBE P-ident | as Linux | PREDICTED |
| Exposures | X1 process list, X2 machine class and host name (5.7) | X3 `/etc` account and host files (5.7) | as Linux | PROBE |

### 5.6 Evidence

All run on 2026-10-22 through `poolhouse.sandbox` as it is at `bf17c71cd` (macOS) or through the tools named (Linux), from probe
scripts kept in the session's scratch directory; slice L1 turns each row into a test. The Bash guard refused a script that merely
contains the text `.git/hooks`, so the protected-path probes use the stand-in names `ctl/hooks` and `ctl/config`; the mechanism
under test is the profile's path rule, which does not read the name.

| Id | Setup | Result |
|---|---|---|
| M-fs | `Policy(read=(project, scratch), write=(project, scratch), exec=SYSTEM_EXEC)`, `HOME` in scratch | read of an outside file, write outside, `ls /Users`, reading through a symlink created in the project to an outside file, a hard link of an outside file into the project and of a project file out: all `Operation not permitted` (the symlink itself can be created); project read and write work |
| M-protected | `deny_write=(project/ctl, project/.claude, project/a/b/prot)` | append, truncate, `touch`, `chmod`, `xattr`, rename-over, `mkdir .claude`, `mkdir .Claude`, write into `CTL/hooks` and `Ctl/config` (the volume is case-insensitive APFS), write through a symlink to `ctl`, rename of `a` and of `a/b`, `rmdir ctl/hooks`, a hard link of `ctl/config` into the project: all refused |
| M-net | `Net.only(model_port)`; listeners in the parent: the model port, another loopback port, a wildcard listener, two UDP sockets | connect to the model port works; another loopback port, the wildcard listener through `127.0.0.1` and through the Mac's LAN address, `1.1.1.1:443`, `::1`, a Unix socket outside, `nc -l` bind: refused; UDP send works to the model port number only, UDP bind refused; DNS lookup returns nothing |
| M-proc | a parent launcher and an unrelated Python process of the same user, each with a marker variable set at `exec` | `kill -0` to the launcher and to pid 1 refused; `ps` refused; `sysctl kern.proc.all` refused (the per-pid form `kern.proc.pid.N` is not: X-list); `ptrace(PT_ATTACH)` of the launcher returns -1; **`KERN_PROCARGS2` of the launcher returned 6,468 bytes containing the marker and of the other process 916 bytes containing its marker; of launchd it failed.** A SIP-protected binary (`/bin/sleep`) returns no environment. With `(deny process-info-pidinfo)` appended both fail. Appending `(deny sysctl-read (sysctl-name-prefix "kern.procargs"))`, `(deny process-info* (target others))` or `(deny sysctl-read)` changes nothing; removing the two `process-info` allows changes nothing |
| M-misc | the same profile | `osascript` "can't open default scripting component"; `open -a` finds no application; `launchctl submit` fails; `launchctl list` empty; `crontab` not executable; `pbpaste` empty; `security find-generic-password` cannot reach the keychain; nested `sandbox-exec` fails `sandbox_apply: Operation not permitted`; `MTLCreateSystemDefaultDevice` returns none; a Unix socket bind and POSIX shared memory in the project refused; `/dev/tty` "Device not configured" |
| M-tty | a pty slave as standard input, `setsid`, with and without it as controlling terminal, `strict` on and off | `TIOCSTI` refused in all four; `tcgetattr` works; `tcsetattr` (raw mode) refused |
| M-signal | profile plus `(allow signal (target children))` | a shell signals and ends its own background child; signals to an outside process of the same user and to the launcher refused |
| M-limits | the same | a loop of 50 background forks (the probe's size) all succeed; `ulimit -u` and `kern.maxprocperuid` are 10,666 |
| M-attest | `sandbox_check(pid, NULL, 0)` through ctypes | 0 for the unsandboxed caller, 1 for a `sandbox-exec` child |
| X-list | the profile of M-proc with D1; raw `sysctl(KERN_PROC_PID)` per pid and libproc, by the non-author pass | 591 processes of the user's own enumerated by name, pid and parent pid in one scan (304 or more system-wide); arguments and environment stay refused |
| X-ident | the profile of M-proc with D1, and with `(deny iokit*)`, `(deny mach-lookup)` and `(deny sysctl-read)` all appended; `system_profiler SPHardwareDataType`, `ioreg -c IOPlatformExpertDevice`, by the non-author pass | the serial number, the hardware UUID and the provisioning UDID print; the channel is a cached read the profile's `file-read*` grants allow, and the single operation or path is not isolated: a bisect of the read grants is the next step |
| M-nproc | `RLIMIT_NPROC` set to the current count plus 40 in the child's preexec; a loop of 300 forks, by the non-author pass | stops with `EAGAIN` at about 250 to 276 forks in every run; with no limit all 300 complete |
| M-follow | `sandbox.process_tree` against fork trees to depth 7 and breadth 8, by the non-author pass | no missed descendant in any trial inside one process group; with `setsid()` the group kill reaches 1 of 255 and 1 of 585 live descendants and the follower reports all |
| M-mem | `task_set_phys_footprint_limit` on the process itself, then a 500 MB allocation, by the non-author pass | the allocation succeeds: the Mach footprint limit does not hold, as `RLIMIT_AS` does not |
| H-claude | Claude Code 2.1.308, `HOME` and `CLAUDE_CONFIG_DIR` in scratch, `Net.only(9)`, `/opt/homebrew` readable | `--version` runs; `-p` stops with `EEXIST ... mkdir '/tmp/claude-501'` until `CLAUDE_CODE_TMPDIR` is set, then reaches the model request and retries the closed port; refusals logged: `/Library/Application Support` read, Security preference read, `com.apple.FSEvents`, `com.apple.SecurityServer`, `securityd.xpc`, `/private/var/run/syslog`; it signals a `git` child (needs D2); D1 does not stop it |
| H-codex | Codex 0.163.0 | stops "CODEX_HOME points to ... but that path does not exist" until it exists; its binary resolves under `~/.codex/packages/standalone/releases/` |
| H-git | stage-shaped project, `strict`, control names denied, `file-link` denied, Homebrew git | `git init`, `add`, `commit`, `status`, `diff`, `log` work |
| W-time | `Workspace(this repository)`, `begin()`, one added file, `finish()`; plain copy and hash of the same 3,713 files; `cp -cR` | 16.0 s, 9.2 s, 19.3 s; 1.9 s copy, 0.15 s hash, 0.5 s APFS clone |
| L-landlock | Linux 7.0.14 VM, ABI 8, uid 1000, ruleset: read-write project, read-only system trees, TCP connect to one port | see 5.3; also raw and packet sockets refused (no capability); a second ruleset can be stacked; `landlock_create_ruleset` ABI query returns 8 |
| B-userns | bubblewrap 0.9.0 in the same VM (`seccomp=unconfined`, `systempaths=unconfined`) | without `--disable-userns`, `unshare --user --map-root-user id` inside prints `uid=0`; with it, the call fails (`No space left on device`, the user-namespace limit is 0); with `--cap-drop ALL` the capability sets are empty and `NoNewPrivs` is 1 |
| B-ns | the same | the outside path is absent; the project is writable; a read-only bind over `ctl` refuses write, create, rename and remove of its contents; the pid namespace shows 4 processes, the launcher's `/proc` entry is absent and `kill` returns "No such process"; the host loopback model port is unreachable in the net namespace and `lo` is up inside; `--share-net` reaches **another** host loopback port (F2); a bind-mounted Unix socket connects and the host path of the same socket is absent; the new session has no controlling terminal; nested bubblewrap works without `--disable-userns` |
| B-placeholder | `--ro-bind /dev/null project/.claude` for a name that does not exist | bubblewrap created an empty read-only file `.claude` in the host project |

**Probes still to run**, by the slice that needs each: P-wsl (kernel, Landlock ABI, `unprivileged_userns`, bubblewrap version, DrvFs
behaviour on the WSL device), P-drvfs, P-cgroup (delegated `pids.max` and `memory.max` under a user systemd scope, on WSL and on a
plain Ubuntu), P-nproc (whether `RLIMIT_NPROC` set inside the user namespace bounds only the session's processes), P-ident (the
identifier paths of G13 on a stock Ubuntu 24.04 host and on the WSL device), P-seccomp (the filter of 5.3 against Claude Code,
Codex, Node, Python, git, cargo), P-tty (3.5), P-sweep (4.2 item 3: `sandbox_check` with an operation and a path filter on
another pid), P-bind (whether each backend binds a granted directory by descriptor, 8.2), P-codex (Codex with its sandbox off,
nested under the profile), P-ubuntu (the AppArmor restriction on a stock Ubuntu 24.04 host, the profile's scope and `unshare
--user` inside the confinement), P-win-token, P-win-acl, P-win-job, P-win-loopback.

### 5.7 Exposures: what stays visible

A guarantee is something the confined process cannot do. An **exposure** is a fact it can still learn because the platform's
mechanism cannot hide it without breaking the tools the harness runs. The report lists each exposure of the platform beside the
guarantees (9). An exposure never refuses a launch, because none lets the process act on anything outside its confinement, and
none is silent: each is named here, each is pinned by P23 (the proof reads what is readable, and any fact outside the declared
set is a deviation, so a new leak stops the launch), and each is an accepted risk (R7).

| Id | Platform | What stays visible | What does not | Why it stays |
|---|---|---|---|---|
| X1 | macOS | the name, pid, parent pid and user id of every process the user can see: 591 of the user's own in one scan (F12) | the arguments, environment, working directory, open files and memory of any of them; signals and tracing (G6, D1, D2) | the profile allows the `kern.proc.pid.` sysctl prefix (`SYSCTL_PREFIXES`), which a process uses to ask about itself (`psutil`, the harness's tools); the profile is written before the process exists, so its pid cannot be named in it and the prefix serves every pid. When the engine shows a profile that closes it, P13 asserts an empty list, X1 is removed and G6 covers the list on macOS |
| X2 | macOS | the machine class and the host name: the sysctls of `SYSCTLS` and `SYSCTL_PREFIXES` in `sandbox/seatbelt.py` (`hw.machine`, `hw.memsize`, `machdep.cpu.*`, `kern.hostname`, `kern.osversion`, `kern.bootargs`); the host name can hold the person's name | the serial number, hardware UUID and provisioning UDID (G13) | the tools size themselves from them |
| X3 | Linux, WSL | the account and host files the profile binds read-only: `/etc/passwd`, `/etc/group`, `/etc/hosts`, `/etc/resolv.conf`, `/etc/localtime`, `/etc/ssl` | the machine id, DMI identifiers, host interfaces (G13), other processes (G6) | the toolchain reads them |
| X4 | Windows route B | decided by the probes of 5.4 | | |

P23 pins the set. The parent lists every sysctl name the machine has and every candidate file of the platform; the probe tries each
and reports which it could read. The readable set must lie inside the declared exposures and the controls of the proof (P20); a
name outside it is a deviation naming the first such name, and the declared lists live in `sandbox/seatbelt.py` and
`sandbox/bubblewrap.py` as the same constants that grant the reads, so the declaration cannot drift from the grant.

## 6. The network

### 6.1 The model bridge

The sandbox never gets the model server's port. It gets the **bridge**, a parent-owned HTTP/1.1 server (one per session):

| Rule | Detail |
|---|---|
| Routes | exact method and path from a fixed table: `POST /v1/messages`, `POST /v1/messages/count_tokens`, `POST /v1/chat/completions`, `POST /v1/responses`, `GET /v1/models`; `GET /health` is answered by the bridge itself and never reaches the server. Every other path is 404 locally: `/slots`, `/props`, `/tools`, `/metrics`, `/lora-adapters`, `/embeddings`, model management, `/infill`, the web UI. |
| Request form | origin-form only. A request-target with `//`, `..`, `%2e`, `%2f`, a backslash, a fragment, or an absolute-form or `CONNECT` target, `Upgrade`, `Expect`, a body with both `Content-Length` and `Transfer-Encoding`, or a duplicate `Content-Length` is refused 400 before parsing further. |
| Headers | forwarded: `Content-Type`, `Accept`, `Anthropic-Version`, `User-Agent`; added: the server's key. All others are dropped. The list is completed per harness by running it against the bridge (a header the harness needs shows as a failed real-model test); nothing is added on a guess. |
| Authentication | `Authorization` or `x-api-key` must equal the session's bridge token (random, 32 bytes); the harness holds only that. The server's key (`serverkeys.for_url`) never leaves the parent. |
| Body | JSON only, at most the served window times 4 bytes (a code point is at most 4 bytes and a token is at least one: 17). `model` must equal the leased alias (rewritten if empty, refused if different). `id_slot` and `lora` are removed. Streaming responses pass through unbuffered. |
| Concurrency | requests in flight to the server at most the lease's slot count; beyond it the bridge answers 429 and never queues, since the server queues per slot. `GET /health` is answered by the bridge and is not counted. |
| Records | method, route, status, bytes, duration. No body is kept. A refused route is counted and reported to the sentinel as a probe. |
| Upstream | `--on URL` must name loopback (a numeric `127.0.0.1` or `::1`, never a name); anything else is refused with the sentence of section 9. A model on another member is reached through that member's pool offer on this device's loopback, never by a remote URL in the profile. |

### 6.2 How the sandbox reaches it, per platform

| Platform | Bridge listens on | Profile | Inside the confinement |
|---|---|---|---|
| macOS | `127.0.0.1:<ephemeral>` (the only port in the profile) | `Net.only(port)` | the harness connects to `http://127.0.0.1:<port>` |
| Linux, WSL | a Unix socket in a 0700 directory; no host TCP port at all | net namespace; the socket's directory bound in | `isolation.enter` runs a forwarder on `127.0.0.1:<port>` inside the namespace (loopback is up there, VERIFIED) copying to the socket; the harness connects to the same URL as on macOS |
| Windows route B | a named pipe or loopback port with a DACL for the container | P-win-loopback | decided by the probe |

The port number is chosen by the parent at bind time and is the same inside and outside on macOS; on Linux it is the forwarder's.
`Net.only` on Linux is the engine's own feature from L1: a net namespace, one relay per named port, and a forwarder inside. It
replaces `--share-net`, which shares the host network (F2). `Net.loopback()` (every port) cannot be expressed on Linux without
sharing the network and is refused there; the callers that use it (the `mcp_server` policy) name their ports in L1.

### 6.3 The egress proxy (granted hosts only)

A `net` grant adds an exact host to the proxy's allow list at once. The proxy is a CONNECT-only HTTP proxy beside the bridge.
It tunnels TLS end to end on port 443 only (never plain HTTP, so there is no redirect to follow), resolves the name itself, refuses
a result in a loopback, link-local, private, multicast, shared-address or documentation range, and connects to the address it
resolved (no second resolution). It refuses wildcards and an IP literal. It counts bytes per host. The env var `HTTPS_PROXY`
names it from the first launch, with an empty allow list, so that a grant needs no restart. A tool that ignores proxy variables
has no route and fails, which is the safe failure.

### 6.4 What stays open

A granted host carries bytes in its path, its sub-domain, its request headers and its timing; the proxy sees the hostname and
sizes only (`docs/harnesses.md` lists the same residual for a plain GET). Pool offers reached through the bridge's upstream carry
whatever the offered model sees. The model's own output reaches the person unreviewed.

## 7. The session bridge: hooks and workspace verbs in the parent

The harness runs a hook as a child, and the classifier, the Requests store, the board client and the ownership claims need the
state root and the node socket. None can be inside the confinement (F10). The **session bridge** is one parent-owned service per
session on one channel (a Unix socket in a 0700 directory with a random name; a named pipe on Windows). The channel is the only
endpoint in the profile besides the model bridge.

| Part | Rule |
|---|---|
| Authentication | the connecting process is checked on accept (`LOCAL_PEERCRED` on macOS, `SO_PEERCRED` on Linux): the user id is the parent's. The directory's mode and random name keep other sandboxed sessions out; the client verifies the server's pid is the launcher's on connect (T12). |
| Rail | the permissions, the execution mode, the agent name, the roots and the protected paths are fields of the bridge object the launcher built. No frame carries or changes them (`harnesshook`'s rule, kept: "never read from the environment or from the call"). |
| Frames | length-prefixed JSON, at most the served window times 4 bytes (a tool input is model output, which the window bounds; 17), unique keys (`json_objects.unique_object`), one outstanding frame per connection; each connection is a process of the confinement, so the process bound is the bound on connections. |
| `hook` | `pre`, `post`, `stop` with the harness's event payload; the bridge calls `harnesshook.pre/post/stop` with its own `Rail` and returns the answer unchanged. A bridge that is down, slow past the harness's hook timeout or confused returns nothing, which both harnesses read as a block. |
| `workspace` | the verbs of `poolhouse.mcp --workspace-only` (inbox, thread, status, ownership reads under read permission; send, claim, announce under the acting permission); no administration, grant or registration. The bridge holds the session's token in the parent's memory and acts as the session. A verb outside the table is refused and counted. |
| `grant` | a request for a grant (8.2) becomes a Requests item answered only by the person; the reply is the person's, not the agent's. At most one is outstanding; repeats fold into it (`docs/compromised-agents.md` 6.3). No question is put to or answered from a terminal the confinement holds (3.5). |
| Clients | `python -I -m poolhouse.isolation.client hook|workspace|grant ...` (stdlib only), written into the hook command by the launcher and into the agent home's `bin/` as a `poolhouse-workspace` shim; for Codex the same module runs as the stdio MCP server. The interpreter is the runtime's own, read-only. |

The bridge makes the hook a client of a gate that is outside the thing it gates. It is not the boundary: a harness tool that never
calls the hook (a subagent, an MCP tool, a background command) is still inside the OS boundary, and the proof does not depend on
the hook.

## 8. Profiles, modes and grants

### 8.1 What each profile grants

| `--permissions` (and mode) | Stage | Network | Hook asks | Codex flags |
|---|---|---|---|---|
| `read-only`; any profile with `--execution-mode plan` | read-only mount; `finish(writable=False)` asserts the manifest is unchanged; the agent home is writable | bridge only | reads allowed; acting calls denied | `--sandbox danger-full-access --ask-for-approval never` |
| `ask-changes` | read-write | bridge only | every acting call asks | same |
| `ask-dangerous` (default) | read-write | bridge only | destructive and unsure calls ask | same |
| `yolo` | read-write | bridge only | only unsure calls ask | same |

Codex's own sandbox flags are replaced because its macOS sandbox cannot start inside this confinement (VERIFIED nested refusal)
and the mount enforces what `--sandbox read-only` meant. The OS boundary is the same for the last three rows by construction;
`yolo` asks less and widens nothing.

### 8.2 Grants

| Kind | What it adds | Takes effect | The sentence the person reads |
|---|---|---|---|
| `read PATH` | a read-only mount or read grant of one directory (not a file under `~/.ssh` or any `secretpaths` name, not an ancestor of the state root) | next launch | "Let this coding session read PATH (it cannot change it)?" |
| `toolchain PATH` | read and execute of one toolchain version directory. A directory the project itself declares (`.python-version`, `.venv/pyvenv.cfg`, `rust-toolchain`, `.nvmrc`, `.tool-versions`) is granted at launch without a question; one the project does not declare is asked | next launch | "Let this coding session run the Python at PATH?" |
| `net HOST` | an exact host on the egress proxy's list | at once | "Let this coding session reach HOST (download only)?" |
| `workspace in-place` | 4.5 | next launch | "Let this coding session change your files directly in PROJECT? Files like .env in that folder become readable to it, and nothing checks the changes before they land." |

The **system toolchain** is the default and asks nothing: macOS `/usr`, `/bin`, `/sbin`, `/System/Library` and the other system
trees `sandbox/seatbelt.py` already lists, `/Library/Developer/CommandLineTools`, and the `bin`, `lib`, `opt`, `Cellar`, `share`,
`include` and `Frameworks` of `/opt/homebrew` and `/usr/local` (not their `etc` or `var`); Linux `/usr`, `/bin`, `/sbin`, `/lib`,
`/lib64` and the handful of `/etc` files `sandbox/bubblewrap.py` lists; Windows `%SystemRoot%` and `%ProgramFiles%`, read and
execute. The **harness's own install tree** is added from the resolved binary.

No grant carries a credential, an environment variable, a GPU, a device or a pool authority; a second writable project is not
offered: a session has one writable project. The list of toolchain roots is derived from the project's own files, not fixed: the
launcher resolves each declaration to the real path of the version directory the person's own version manager installed (the
prefix of the interpreter or compiler the declaration selects) and grants that one directory, never its parent. A directory is
refused, and the report lists it, when it is a credential location (`secretpaths`), an ancestor of the state root or of the
person's home, or holds no executable of the declared kind; a `pyvenv.cfg` that names `~/.ssh` gets nothing. A declaration is
resolved once and the same object is checked and granted: each component is opened with `O_NOFOLLOW` (a symlink farm in a
declaration resolves to nothing), the final directory is held as a descriptor, its device and inode are recorded, the refusals
above run on that descriptor, and the profile rule or bind is built from the descriptor's own path (`F_GETPATH` on macOS,
`/proc/self/fd` on Linux), never from a second resolution of the declaration. The identity is read again after the proof, and a
different one is a deviation. A backend that can bind by descriptor does (PROBE P-bind). The environment gains nothing from a
toolchain directory (3.4).

**Logging.** Every grant is an activity record of kind `isolation.grant` (`docs/activity-log.md`): session, conversation, kind,
subject, scope, issuer (the person's request fingerprint, or the typed flag), the profile digest it widened, and the sentence the
person saw. The launch banner and the coding dialog list the grants in force, and the end-of-session record lists the grants used.

### 8.3 Who may issue a grant, and the launcher as a deputy

A grant comes from a person: a flag typed at a terminal by a process that is not under an agent marker (`CLAUDECODE`,
`POOLHOUSE_AGENT`, `POOLHOUSE_NONINTERACTIVE`) and is not inside a confinement, or an answer to a Requests item. The test is
`person.require_person`, which holds three conditions: no marker, a terminal on stdin and stdout, and `sandbox.confined()` false
(3.5, I17). The third is a fact of the process the operating system keeps for it and its descendants, so a pty slave handed to a
shell the harness runs, a dropped marker variable or a new session leaves the check refusing; in interactive mode the relay's
slave is never what answers it. A new gate, `isolation.grant`, is `person` in the authority registry in every preset and
`POOLHOUSE_AUTHORITY_FLOOR=person` reads it so too. A launcher run by an agent, or inside a confinement, refuses every grant flag
and `--workspace in-place`, takes `--project` only for a directory the project registry holds or one under the system temporary
root, and refuses `--protect` and `--on` values that name anything outside loopback. These checks sit at the choke point, in
`launch_coding_agent` and `isolation.prepare`, before an argument reaches a launcher's parser, and not in each launcher
(`claude.launch` and `codex.launch` read them from `argv` unchecked, F17). `--on` is checked for a numeric loopback address for
every caller, a person included. A widening is therefore never reachable by the model, by an injected instruction, or by a lead
agent on its own.

### 8.4 What the hook keeps doing

The OS now refuses what the classifier used to ask about (a download has no route; a write outside the stage has no path). The
hook keeps: the asks that become grants (`pip install` becomes the `net` question once), `git`/`gh` calls that cannot work offline
(they fail instead of asking), and unsure calls. It stops asking per call about writes to held files (4.4.1), which publication
asks once with the whole diff in view.

## 9. The capability report and the refusals

`isolation.capability(spec=None)` replaces the unconditional refusal. It returns a report with one entry per guarantee of 5.1:
`state` (`enforced`, `watched`, `unenforceable`; for G11 one state per resource), `how` (the mechanism, in a phrase), `reason` and
`remedy` (one plain sentence each, empty when enforced), one entry per exposure of 5.7 (`visible`, with what stays visible), and
the platform, backend, backend version and operating-system build it was computed for. An exposure never changes `can_start`. It reads
the machine: the backend binary and its version, the namespace probe, the Landlock ABI, the kernel string, the project's mount
type and, for a spec, the project's size and the toolchain it needs. It is cached for a boot, an operating-system build and a
backend binary identity, and recomputed when any changes. It does not run the proof; the proof is the launch.

Surfaces: `poolhouse security sandbox status` and `poolhouse-doctor` print one line per guarantee; `/ui/coding/catalogue` returns
`can_start`, `enforcement_error` (the first unenforceable guarantee's sentence) and the whole report for a details view; every
site that refused before calls `capability()` for an early plain refusal, and the launch is what enforces.

The sentences are what an agent relays to a person (AGENTS.md section 2, "Onboarding is the product"):

| Condition | Sentence |
|---|---|
| macOS, `sandbox-exec` missing | "This Mac cannot confine coding agents: its built-in sandbox tool is missing." |
| The launcher is itself sandboxed | "This command is already inside a sandbox, and a sandbox cannot start inside another; start it from a terminal or from a session that is not sandboxed." |
| Linux, no bubblewrap | "This computer cannot confine coding agents until bubblewrap is installed." |
| Linux, bubblewrap without `--disable-userns` | "The installed bubblewrap cannot stop a coding agent from starting its own sandboxes (it lacks the option that turns that off)." |
| User namespaces off | "This computer's security settings stop coding agents from being confined (unprivileged user namespaces are off)." |
| AppArmor restriction | "This computer's security rules block the sandbox tool; run `poolhouse security sandbox install` once as an administrator to allow it." |
| AppArmor permission changed | "The permission installed for the sandbox tool is not the one Poolhouse ships; run `poolhouse security sandbox install` once as an administrator to restore it." |
| macOS, the machine's serial number or hardware identifiers are readable inside the box | "This Mac's serial number and hardware identifiers are readable from inside the safety box, so the coding agent was not started." |
| WSL 1 | "Coding agents need WSL 2; this is WSL 1." |
| Project on a Windows drive under WSL | "This folder is on a Windows drive; coding agents can only be confined for folders inside WSL." |
| Native Windows network | "This Windows computer cannot keep a coding agent's network to the model alone; run it in WSL 2." |
| The stage volume is too small | "This computer has `M` MB free for the working copy and this project needs `N` MB; free some space, or open it with `--workspace in-place`, which lets the agent change your files directly." |
| No trusted git | "Coding agents need git, and none was found that Poolhouse can trust." |
| `--on` names a non-loopback server | "A coding agent can only use a model server on this computer; `URL` is not." |
| Interactive mode before L6 | "Interactive mode is not available yet; run it with its print option, for example `poolhouse-claude -- -p 'your request'`." |
| A service did not start | "The coding agent's helper (`name`) could not start, so the agent was not started." |
| The proof failed | "Poolhouse could not confirm that the safety box holds on this computer (it allowed `attempt`), so the coding agent was not started." |

## 10. The proof: the launcher shows the confinement holds, every launch

### 10.1 What runs

After the services are up and before the harness starts, `isolation.proof.run(profile)` starts one **probe** inside the profile the
harness will get (the same `Policy` object and the same wrapped-argv builder; only the program differs, and the probe echoes the
profile digest the parent computed so a different profile is a deviation). The probe is a standard-library Python program that
the runtime's own interpreter runs read-only in isolated mode (`-I`), with a working directory that is an empty directory the
parent made outside the stage, so a `json.py`, `sitecustomize.py`, `usercustomize.py` or `.pth` file in the project is never
imported; it needs nothing the harness profile does not already grant. It runs the attempts below in order, writes one JSON line
per attempt, and exits. The parent holds canaries the probe cannot know: a file in a fresh
directory outside every grant, listeners on a loopback port and (when the machine has one) a non-loopback address, a Unix socket,
and a marker variable in its own environment set before the probe starts.

| Id | Attempt | Must | Guarantee |
|---|---|---|---|
| P01 | read the canary file | fail | G1 |
| P02 | list the person's home directory | fail | G1 |
| P03 | write a file next to the canary and in the home directory | fail | G2 |
| P04 | create each control name in the stage root, in its case and Unicode variants | fail | G3 |
| P05 | read the canary through a symlink made in the stage | fail | G1 |
| P06 | hard-link the canary into the stage | fail | G1, G2 |
| P07 | connect to the parent's loopback listener that is not the bridge | fail | G4 |
| P08 | connect to the non-loopback listener; with none, to a documentation-range address | fail | G4 |
| P09 | send UDP to the P07 port; bind a UDP and a TCP port | fail | G4 |
| P10 | connect to the canary Unix socket | fail | G5 |
| P11 | signal and trace the launcher | fail | G6 |
| P12 | read the launcher's arguments and environment and look for the marker | marker absent | G6 |
| P13 | list processes; read the arguments, environment, working directory and open files of each one listed outside the session | Linux and Windows route A: none listed; macOS: only the fields of X1 are listed and every read of the rest fails | G6, X1 |
| P14 | `TIOCSTI` on a terminal descriptor; open the controlling terminal | fail | G7 |
| P15 | submit a scheduled job | fail | G10 |
| P16 | open the GPU device | none | G9 |
| P17 | read a credential store | fail | G8 |
| P18 | start a nested sandbox or user namespace | fail | G12 |
| P19 | read its own environment | exactly the allow-list; no marker | G8 |
| P20 | write and read back a file in the stage and in the agent home; run `--version` of each granted toolchain; `GET /health` through the model bridge; `GET /props` through it; a request with another model | succeed, succeed, succeed, 200, 404, refused | controls |
| P21 | connect to the session bridge and send an unknown frame | well-formed refusal | controls |
| P22 | the parent reads the serial number, hardware UUID, provisioning UDID, machine id and network hardware addresses itself; the probe runs `system_profiler SPHardwareDataType` and `ioreg -rd1 -c IOPlatformExpertDevice`, reads `/sys/class/dmi/id/*`, `/etc/machine-id`, `/var/lib/dbus/machine-id` and `/sys/class/net/*/address` (on Windows the machine GUID and the SMBIOS table) and returns what it obtained; the parent searches that for the values | none found; the record says "absent" and never holds a value | G13 |
| P23 | try every sysctl name the machine has (macOS) and every candidate file of the platform's exposure list; report what could be read | the readable set lies inside the declared exposures (5.7) and the controls of P20 | exposures |
| P24 | call `person.require_person` with the terminal and marker conditions forced true (`terminal=(True, True)`, `env={}`), then `person.confirm` | `HumanRequired`; the empty answer | I17 (3.5) |

A control that fails is a deviation too, so a profile that refuses everything cannot pass. An attempt the platform cannot make
(no scheduler to submit to) is not skipped: the probe reports "cannot attempt", and a guarantee whose attempts cannot be made is
`unenforceable` in the report, so the launch refuses. The probe finishes inside the launch budget (17), which its 24 attempts share; an attempt that has not
answered when the budget is spent is a deviation, not a retry.

### 10.2 What a deviation does

`IsolationFailed` names the first attempt that got through, the guarantee and the profile digest. The launch stops before the
harness exists. The sentinel gets an `isolation.proof_failed` event (warning); the activity log gets a record; the capability
cache is dropped so the next call recomputes; the person's surface shows the sentence of section 9. Nothing retries, and no
environment variable, flag or argument skips the proof or the launch (`AllowUnsandboxed` is not accepted by `isolation`; the
engine's opt-out is for `SandboxedBash` and tests of other things). The same rule as `docs/design-fail-closed-harness.md` R1:
enforcement does not depend on observation; the proof reads results, not the refusal log.

### 10.3 Attestation of the live process

After `Confinement.popen` starts the harness, and before it returns, the launcher checks the live pid: macOS
`sandbox_check(pid, NULL, 0)` equals 1 (VERIFIED for a `sandbox-exec` child); Linux `/proc/<pid>/ns/{mnt,pid,net,user}` differ
from the launcher's, `NoNewPrivs` is 1, `CapEff` is 0; Windows the process token is an AppContainer token and
`IsProcessInJob` is true. A harness that is not in the confinement it was started in is ended at once. This is the check that the
proof's probe and the real harness are the same boundary, not two.

### 10.4 What the proof is not

It is a smoke alarm, not the suite: it samples twenty-four attempts on this machine at this moment. It does not see a kernel bug,
a later change of the machine's state, or an escape the list does not name; the suite of section 13 and the review of section 14
carry those. It cannot prove the absence of an escape. It proves that the profile the harness runs in refuses the things the
owner listed, here, now, and that the services it needs are up.

## 11. Failure behaviour

| Event | Behaviour | Who is told | State left |
|---|---|---|---|
| Capability unenforceable | `IsolationRefused` before the model is leased | the caller; the UI note; the agent relays the sentence | nothing started |
| A service cannot start | the launch stops; services already started are stopped | same | nothing |
| Proof deviates | `IsolationFailed`; no harness | same, plus the sentinel and the activity log | the stage is removed |
| The attestation fails | the process is killed at once | same | the stage is removed, nothing published |
| Model bridge dies mid-session | the harness's next request fails and the harness stops | the turn ends `failed` with the sentence | stage published as for any exit (below) |
| Session bridge dies | every hook fails, which blocks the tool call; the harness cannot act | same | as above |
| A bridge sees a forbidden route, field or verb | refused locally and counted; a burst reports to the sentinel as probing | the sentinel | session continues |
| Watchdog bound passed (G10, G11) | the session is ended by the barrier's tree walk, loudly | the turn ends `failed` naming the bound | nothing of the turn in progress published |
| The kernel refuses a fork past the process limit (D5) | the fork fails with `EAGAIN` in the harness; a harness that does not recover ends, and the follower names the processes in the failure | the turn ends `failed` naming the process limit | as for any exit |
| A held change is answered no, or the question is not answered | the held files are not applied and the rest is; an unanswered question applies nothing held and is asked again with the next publication | the person | the stage and the held list |
| The displaced version changed between the read and the swap (4.4.3) | the swap is undone, the person's version stays under the name, the agent's is kept under the state root, the path is named | the person | per 4.4.3 |
| The harness exits non-zero or is cancelled | the tree is ended, the barrier passed; **publication still runs** on what verified: the stage is the person's work in progress, and the checks decide, not the exit status | the person sees what was applied and what was held | per 4.4 |
| A process survives the barrier (macOS orphan) | publication is skipped, the survivor's pid is reported, the stage is kept until the survivor is gone or the conversation ends | the person | the stage |
| Publication refused by the policy guard (permissions changed, project withdrawn, revision moved) | nothing applied; the previous receipt stands | the person | `test_revocation_after_child_returns_rejects_its_captured_edits` |
| Crash of the launcher | the harness is a child in a pid namespace or a job, both of which end with the launcher; on macOS its processes stay confined and have lost the bridges, and the next launch of the project ends them with the sweep of 4.2 item 3 before it recovers; the intent log of 4.4.3 lets that launch finish or undo a half-applied publication, and it removes the leftover namespace directory | the next launch says what it recovered | recovered |
| Backend disappears between check and start | the start fails closed (the wrapped argv does not exist); the capability cache is dropped | the person | nothing |
| The operating system is updated | the cache key changes; the next launch recomputes and proves again | nobody unless it now fails | |

## 12. What a person feels

The normal journey adds no step, prompt, flag or wait a person notices (AGENTS.md section 2, "Security without friction").

- A Mac user starts a coding conversation: the capability check is a cache read; the stage is built in under the 5 s budget (W1);
  the proof adds a fraction of a second; the first line says "Working on a copy of PROJECT; changes arrive when the turn ends."
  Nothing is typed.
- The turn ends: "Applied N changes. Held one for your answer: `Makefile`." One Requests item with the diff, answered like any
  other. If the person edited the same file meanwhile: "Kept your version of `src/x.py`; the agent's is saved with this
  conversation."
- A Linux user whose system restricts user namespaces meets one administrator prompt once, at setup, which installs the shipped
  AppArmor profile (the dialog pattern of `docs/compromised-agents.md` 2.1); everyone else meets none.
- A Windows user with WSL 2 runs route A with no new step. Without it, the sentence of section 9 and nothing else.
- The agent asks for the network to install a package: one question, "Let this coding session reach pypi.org (download only)?",
  once for the session; before this design the same install asked per command.
- Source files, tests and documents are applied with no question; held files are asked once per publication with the whole diff in
  view, not once per tool call.
- Refusals are one sentence an agent relays. No refusal asks a person to open a terminal or edit a file.

Added visible cost, with its reason: a project gets a working copy, so edits reach the checkout after each completed turn and not
as they happen (the person's editor does not show them mid-turn). That is the price of I4; the working copy is made by clone or
reflink where the filesystem has it, so it costs neither time nor, usually, space. A second cost of I4: a change to a data or
configuration file (a JSON fixture, a YAML file, a lockfile) is asked like a build file, because only the tool that reads such a
file knows what it does and structure cannot tell an inert fixture from a manifest. That is the price of denying by default
instead of listing names.

## 13. Tests

### 13.1 Rules

- **Real escape attempts, no mock of the operating system.** Each test starts a real confined process on the platform's real
  backend and tries the thing. The only object injected in place of the operating system is a `Backend` that reports itself
  unavailable (the engine's own protocol), to test the refusal sentences. A backend or a test double never claims enforcement.
- **Fail first, and the kill rule.** Every test is run on the parent commit and seen to fail. Every rule of every profile has a
  mutant (delete the rule from the generated profile or argv) that turns at least one probe or test red with the expected
  deviation, as `docs/design-fail-closed-harness.md` 5.5 requires of its guards. A rule no test notices is a failing test that
  names it.
- **A test of a platform's own boundary does not skip on that platform.** It skips only on another platform. On its own platform
  an absent or unusable backend is a failure that names the cause, so a CI runner without user namespaces is red until it is fixed
  (13.3), not green by skipping.
- **No fake model.** A plumbing server may stand in only where no model behaviour is under test (a bridge route table, framing,
  smuggling); it marks every reply `[CANNED RESPONSE, NOT A MODEL]` and reports the model id `CANNED-NOT-A-MODEL`, and the tests
  assert the marker. The end-to-end tests run a real small Qwen3.8 model through the lease (`--real-model`), on the server's one
  slot (`--parallel 1`, AGENTS.md section 1, "One thing computing on the GPU at a time"); two sessions never share a slot, and no
  test waits without a hang guard (17).
- **Files.** `tests/` is flat (the selector indexes `tests/*.py`). The boundary suites are `test_redteam_isolation_macos.py`,
  `_linux.py`, `_windows.py`, `_proof.py`, `_bridge.py` and `_session.py`: the `test_redteam_` name puts them under the
  `weakened-assertions` hook and the `redteam` marker, as `test_redteam_bubblewrap.py` is. The others are
  `test_isolation_capability.py`, `_workspace.py`, `_launch.py` and `_env.py`, with `test_publication_rule.py`,
  `test_publication_oracle.py` and `test_apparmor_profile.py`. `tests/isolation_kit.py` holds the hostile
  programs (the probe's attempts are importable, so the suite and the launch run the same code). The files that start
  `sandbox-exec` are added to `tests/seatbelt-modules.txt`.

### 13.2 Fail-first catalogue

Names are the tests the slices write; the right column is the attack of section 14 (or the finding of the non-author pass, 14.3)
each answers. Each is run on the parent commit and seen to fail before the change that makes it pass.

| Test | Platforms | Attacks and findings |
|---|---|---|
| `a_confined_session_cannot_read_or_write_outside_its_grants` | all | A1, A2 |
| `symlink_hardlink_dotdot_and_alias_paths_cannot_leave_the_stage` (including `/System/Volumes/Data`, `/proc/self/root`, `/dev/fd`) | mac, linux | A3, A4, A5 |
| `a_control_name_cannot_be_created_in_any_case_or_normalization` (case, NFC and NFD, full-width forms; on Windows 8.3 short names and `:stream`) | mac, linux, win | A6 |
| `an_ancestor_of_a_protected_path_cannot_be_renamed_and_the_stage_root_cannot_be_swapped` | mac, linux | A7 |
| `special_files_in_the_stage_are_left_out_and_named` (a FIFO, a socket made in a short path, a device node) | mac, linux | A8, F18 |
| `a_sparse_file_larger_than_the_volume_and_a_tree_of_small_files_stage_by_progress` | mac, linux | A9, F7 |
| `the_launchers_arguments_and_environment_are_unreadable` (a marker variable) | mac, linux | A15 |
| `signal_ptrace_and_the_reads_of_other_processes_reach_nothing_outside_and_the_list_is_exactly_the_exposure` | all | A16, A17, N1 |
| `no_hardware_identifier_is_readable` (`system_profiler`, `ioreg`, the cached channel; `dmi`, machine id, interfaces on Linux) | all | N2, G13 |
| `the_exposure_set_is_exactly_the_declared_set` (every sysctl name and candidate file) | mac, linux | N1, N2 |
| `tiocsti_dev_tty_and_apple_events_are_refused`; `interactive_relay_isolates_the_terminal` | mac, linux, win | A18 |
| `a_pty_given_as_stdio_is_not_a_person` (the relay slave and a duplicated descriptor as stdio; a pty the process made, its master held and answering `yes`; a dropped marker; `setsid`: `require_person` raises and `confirm` is empty, also after `TIOCSTI` text into the slave) | mac, linux, win | N9, A45 |
| `no_scheduler_accepts_a_job` (`launchctl`, `crontab`, `systemd-run --user`, `schtasks`) | all | A19 |
| `nested_sandboxes_and_user_namespaces_are_refused_and_a_sandboxed_launcher_is_detected` | mac, linux | A20 |
| `a_process_that_outlives_the_session_loses_the_bridges_and_the_stage` | all | A21 |
| `a_setsid_descendant_is_ended_before_the_final_capture` (the barrier walks the tree; a group kill reaches 1 of 255) | mac | A14, A21 |
| `a_fork_flood_is_refused_by_the_kernel_and_a_disk_fill_and_memory_growth_end_the_session` | all | A22, N8 |
| `no_loopback_port_but_the_bridges_is_reachable` (TCP, UDP, `::1`, wildcard listener, LAN address, the node's and the approval UI's real ports) | all | A23, A24, A26 |
| `no_unix_or_abstract_socket_but_the_session_bridge_connects` (`docker.sock` stand-in, `ssh-agent` stand-in) | mac, linux | A25 |
| `the_model_bridge_serves_only_its_routes_and_pins_the_model` (a canned plumbing server) | all | A27, A29 |
| `the_egress_proxy_refuses_private_ranges_rebinding_ports_wildcards_and_literals` | all | A28 |
| `the_environment_is_exactly_the_allow_list` (a marker in the launcher, `GH_TOKEN`, `SSH_AUTH_SOCK`, a toolchain directory whose files name a variable) | all | A30, A32 |
| `no_credential_store_is_reachable` | all | A31 |
| `a_forged_replayed_or_flooded_hook_event_changes_nothing`; `a_verb_outside_the_table_is_refused`; `a_session_cannot_act_as_another` | mac, linux, win | A33, A34, A35 |
| `hostile_project_content_runs_nothing_in_the_parent` (git config, filters, attributes, a worktree pointer outside, a hostile index) | mac, linux | A36 |
| `publication_never_overwrites_a_concurrent_edit` (a real writer process edits in place, by rename and by delete and re-create, at every step of 4.4.3; every version the writer made is under the name or kept) | mac, linux | A37, N7 |
| `publication_refuses_symlinked_live_paths_collisions_and_recovers_from_a_crash` | mac, linux | A38, A39 |
| `every_change_that_is_not_plain_source_is_held_with_its_diff_whichever_tool_wrote_it` (shell redirect, `python -c`, an editor tool, a build script) | all | A11, N3, N4, N5 |
| `the_corpus_of_auto_run_names_is_held_and_ordinary_source_is_applied` (the names of N3 to N5 and the registry's special names are held; `src/` and `tests/` files no held file names are plain) | all | A11, N3, N4, N5 |
| `every_name_an_ecosystem_tool_runs_is_held` (the oracle of 4.4.2, on the tools present) | mac, linux | A11, N3 |
| `a_file_named_by_a_held_change_is_held_and_shown` (a script a Makefile or workflow names, planted a turn earlier) | all | A11 |
| `the_shipped_apparmor_profile_is_scoped_to_bwrap_and_changes_no_sysctl` (parse, plan, and the Ubuntu job with the restriction on) | linux | N6 |
| `a_proof_with_a_weakened_profile_stops_the_launch` (one mutant per rule) | all | A41, A43 |
| `project_content_cannot_change_the_probe` (`json.py`, `sitecustomize.py`, `.pth` and a canary-named file in the stage) | all | A42 |
| `no_flag_variable_or_argument_skips_the_proof` | all | A43, A44 |
| `a_launcher_under_an_agent_marker_or_inside_a_confinement_refuses_grants_and_unregistered_projects_and_a_non_loopback_on` | all | A45, F17 |
| `a_toolchain_declaration_is_resolved_once_and_granted_by_the_same_object` (a symlink farm swapped between the refusal checks and the grant) | mac, linux | 8.2 |
| `stage_and_begin_finish_stay_inside_the_launch_budget` (size of this repository) | mac, linux | W1 |
| `the_launcher_is_the_only_path_that_starts_a_harness` (AST) | all | A44 |

### 13.3 Where each runs

| Where | What | Status |
|---|---|---|
| This Mac (macOS 26.6) | the whole macOS suite through `scripts/test` (the Seatbelt pass); the capability, proof, bridge, session and workspace suites; the real-model end-to-end for Claude Code and Codex with a Qwen3.8 model through the lease, in a scratch project | available now |
| The WSL device (`scripts/test all FILES --on DEVICE`) | the whole Linux suite, the bubblewrap probes, P-wsl, P-drvfs, P-cgroup, P-seccomp, the Linux real-model run | needs the WSL device in the pool (no other device is active at the time of writing); the owner turns on `remote-tests` for it (`docs/test-farm.md`) |
| GitHub Linux | the Linux suite, with a step that clears `kernel.apparmor_restrict_unprivileged_userns` on the runner (`ci.yml`); a red run, never a skip, when bubblewrap cannot make a namespace | needs the workflow edit of L7 |
| GitHub Linux, second job | the AppArmor scope test of 5.3 with the restriction left on and the shipped profile installed: `bwrap` makes its namespaces and `unshare --user true` from a shell does not | needs the workflow edit of L7 |
| GitHub, nightly | `scripts/refresh_language_kinds.py` fails when the committed `contracts/language-kinds.json` differs from the registry; `tests/test_publication_oracle.py` on the tools the runner has | needs the workflow edit of L4 |
| The Windows PC | P-win-token, P-win-acl, P-win-job, P-win-loopback, then `test_redteam_isolation_windows.py`, and route A on its WSL | **needs the owner's device**: a person turns on remote tests there and an agent on the device runs the probe script (`docs/windows-wsl.md`, "Letting the Mac run tests on this device") |
| `scripts/test-on-linux` (Docker) | the pure-Python suites (bridges, session bridge, workspace classification, refusal sentences) only; its container refuses user namespaces by default, so it cannot run the Linux boundary tests and is not counted for them | available now |

### 13.4 Mutation and coverage

`scripts/mutate` runs on the profile builders (`isolation/profile.py`, `sandbox/seatbelt.py`, `sandbox/bubblewrap.py`) with the
guard registry of 13.1. `scripts/redteam_coverage.py` gets a row per surface of section 2 naming a test of 13.2. A diff that
loosens an expected deviation in the proof table needs the `Reviewed-by-second:` line, as any red-team test does.

## 14. Attack first

Status: the non-author pass of AGENTS.md section 1 ran and is `docs/design-isolated-launcher-breakit.md`. The reviewer was told the
attacker's goal (read or change what the person owns, reach the approval path, outlive the session, or leave something that runs
later) and the capabilities of section 2, and answered in writing for each row, running probes on this Mac. Linux and Windows
claims in it are code reads, marked NOT RUN. Findings are fixed or named as accepted risks with the owner; 14.3 decides each.

### 14.1 The list

| # | Attack | Defence | Test |
|---|---|---|---|
| A1 | read the home directory, keystore, `~/.ssh`, other repositories by absolute path | G1: nothing granted; the stage excludes credential names | `a_confined_session_cannot_read_or_write_outside_its_grants` |
| A2 | write outside the stage (home, `/tmp`, `LaunchAgents`, shell startup files, cron) | G2 | the same |
| A3 | symlink out of the stage, then read or write through it; make the parent's capture or apply follow it | G1, G2 by the OS (VERIFIED); capture refuses a tracked link that leaves the tree and lists it (W3); apply opens every component with `O_NOFOLLOW` (4.4.3) | `symlink_hardlink_dotdot_and_alias_paths_cannot_leave_the_stage`, `publication_refuses_symlinked_live_paths_collisions_and_recovers_from_a_crash` |
| A4 | hard link an outside file in, or a stage file out | refused (VERIFIED on macOS and Landlock) | `symlink_hardlink_dotdot_and_alias_paths_cannot_leave_the_stage` |
| A5 | `..`, `/proc/self/root`, `/dev/fd`, `/System/Volumes/Data/...`, `/.vol` | real-path rules; the probe reads `$HOME` by each alias | the same |
| A6 | case, Unicode, 8.3 short name or alternate stream of a protected name | VERIFIED case aliases on APFS; normalization forms are in the test; Windows names PROBE; 4.4 refuses folded collisions | `a_control_name_cannot_be_created_in_any_case_or_normalization` |
| A7 | rename an ancestor of a protected path away and back; swap the stage root while the parent captures | ancestor unlink denied (VERIFIED); capture after the barrier; identity checks | `an_ancestor_of_a_protected_path_cannot_be_renamed_and_the_stage_root_cannot_be_swapped` |
| A8 | FIFOs, sockets, device nodes in the stage so the parent blocks on them | a FIFO can be created inside the confinement (VERIFIED); the capture opens with `O_NONBLOCK` and checks `S_ISREG`, and the node is left out and listed by name (W3, F18) | `special_files_in_the_stage_are_left_out_and_named` |
| A9 | enormous, sparse or millions of files; deep trees; fill the disk | the disk bound derived in 17, the allocated-blocks check, stage builds judged by progress (W4), the watchdog | `a_sparse_file_larger_than_the_volume_and_a_tree_of_small_files_stage_by_progress`, `a_fork_flood_is_refused_by_the_kernel_and_a_disk_fill_and_memory_growth_end_the_session` |
| A10 | set-uid, mode, xattr, ACL, quarantine bits that change what the person's tools do | modes copied from a whitelist (`0644`, `0755`), xattrs not carried, set-id files held out and listed | `test_isolation_workspace` |
| A11 | plant a hook, config, wrapper, lockfile, instruction file or review-governance file that runs or decides later (hook managers, `.lfsconfig`, `action.yml`, `Tiltfile`, wrapper scripts and jars, MSBuild and Bazel files, `requirements.txt`, the instruction files of every coding tool, `CODEOWNERS`, and the rest of N3 to N5) | not writable at the OS level where the name is known (G3); at publication everything that is not plain source is held with its diff, by structure, the language registry, the harness and ecosystem records and the project's own wiring, whichever tool wrote it (4.4) | `every_change_that_is_not_plain_source_is_held_with_its_diff_whichever_tool_wrote_it`, `the_corpus_of_auto_run_names_is_held_and_ordinary_source_is_applied`, `every_name_an_ecosystem_tool_runs_is_held`, `a_file_named_by_a_held_change_is_held_and_shown` |
| A12 | spread an exploit over ordinary source files the person later runs | not detectable by name or structure; the person sees the diff when the file is wired; accepted risks R1 and R8 | none |
| A13 | persist injected instructions in the agent home across turns | the home is per conversation and dies with it; accepted risk R2 | none |
| A14 | a surviving process changes the checkout or the stage after publication, including one that left the process group with `setsid()` | the checkout is not reachable from the confinement; the barrier walks the tree, never the group (4.2 item 3); the stage is removed; a survivor loses the bridges and publication is skipped | `a_setsid_descendant_is_ended_before_the_final_capture`, `a_process_that_outlives_the_session_loses_the_bridges_and_the_stage` |
| A15 | read the launcher's or another process's arguments and environment; list processes | D1 on macOS (VERIFIED open before, closed after) for arguments and environment; the process list is exposure X1 on macOS and absent on Linux (pid namespace) | `the_launchers_arguments_and_environment_are_unreadable`, `signal_ptrace_and_the_reads_of_other_processes_reach_nothing_outside_and_the_list_is_exactly_the_exposure` |
| A16 | `ptrace`, `task_for_pid`, `process_vm_readv`, a debugger | refused (VERIFIED macOS; Landlock domain; pid namespace) | the same |
| A17 | signal the parent or `kill -9 -1` | `signal` limited to children (VERIFIED); pid namespace | the same |
| A18 | inject keystrokes (`TIOCSTI`), open `/dev/tty`, send Apple events, `open`, clipboard, UI automation | refused (VERIFIED); the interactive harness gets a private pty, and no question for a person is read from it (3.5) | `tiocsti_dev_tty_and_apple_events_are_refused`, `interactive_relay_isolates_the_terminal` |
| A19 | persist through `launchd`, `cron`, `at`, `systemd --user`, scheduled tasks, login items | refused (VERIFIED `launchctl submit`, `crontab`); Linux has no scheduler in the namespace | `no_scheduler_accepts_a_job` |
| A20 | enter a nested sandbox or user namespace to regain capabilities | `--disable-userns`, empty capabilities (VERIFIED); nested `sandbox_apply` refused (VERIFIED) | `nested_sandboxes_and_user_namespaces_are_refused_and_a_sandboxed_launcher_is_detected` |
| A21 | double fork, `setsid`, a daemon that outlives the session | pid namespace and job on Linux and Windows; the follower's tree walk on macOS; survivor reported | `a_setsid_descendant_is_ended_before_the_final_capture` |
| A22 | fork bomb, memory, CPU, descriptors, disk, output flood | the kernel refuses forks past the limit (D5) and enforces the rlimits; memory and disk watched with the bounds derived in 17; output capped by `MAX_EVENT` per line (exists) and by the output queue | `a_fork_flood_is_refused_by_the_kernel_and_a_disk_fill_and_memory_growth_end_the_session` |
| A23 | reach any loopback service other than the bridge: the node socket, the broker, the approval UI | only the bridge port is in the profile (VERIFIED); the model server's port is in none | `no_loopback_port_but_the_bridges_is_reachable` |
| A24 | LAN, internet, IPv6, UDP, DNS, raw sockets | refused (VERIFIED) | the same |
| A25 | `docker.sock`, `ssh-agent`, D-Bus, X11, `tmux`, abstract sockets | refused (VERIFIED on macOS; host paths absent on Linux) | `no_unix_or_abstract_socket_but_the_session_bridge_connects` |
| A26 | bind a listener to expose the stage or take a reverse shell | bind refused (VERIFIED) | `no_loopback_port_but_the_bridges_is_reachable` |
| A27 | bridge abuse: unknown routes, request smuggling, absolute-form targets, pipelining, slow bodies, oversize bodies, a different model, header injection | 6.1 | `the_model_bridge_serves_only_its_routes_and_pins_the_model` |
| A28 | egress proxy abuse after a grant: SSRF to private ranges, DNS rebinding, CONNECT to other ports, wildcards, IP literals, redirects | 6.3 | `the_egress_proxy_refuses_private_ranges_rebinding_ports_wildcards_and_literals` |
| A29 | use the model server as a deputy: tool routes, slot save, adapters, the server's key | the server's port is unreachable; the bridge allows five routes and strips two fields; the key stays in the parent | `the_model_bridge_serves_only_its_routes_and_pins_the_model` |
| A30 | leaked tokens and variables: `GH_TOKEN`, `AWS_*`, `SSH_AUTH_SOCK`, `POOLHOUSE_*`, the launcher's marker | the environment is a closed table (3.4); a toolchain grant adds `PATH` entries and no variable; D1 | `the_environment_is_exactly_the_allow_list` |
| A31 | keychain, credential manager, keyring, browser profiles | refused (VERIFIED macOS); absent by G1 elsewhere | `no_credential_store_is_reachable` |
| A32 | the person's own harness login (`~/.claude`, `~/.codex/auth.json`) | G1; `CLAUDE_CONFIG_DIR` and `CODEX_HOME` are private and hold no login | `the_environment_is_exactly_the_allow_list` |
| A33 | forge, replay or flood hook events; make asks that fatigue the person; inject text into an ask | the bridge fixes the rail; one ask outstanding; the ask text is escaped and cut as the inbox does; the fatigue ledger | `a_forged_replayed_or_flooded_hook_event_changes_nothing` |
| A34 | squat or leave the session socket path; connect to another session's bridge | random directory name, mode 0700, the connecting process's credentials, the client checks the server pid | `a_session_cannot_act_as_another` |
| A35 | a tool call that never reaches the hook (subagent, MCP tool, background command) | the hook is not the boundary; the OS holds | `a_verb_outside_the_table_is_refused` |
| A36 | hostile project content run by the parent's git (config, filters, attributes, `.git` as a pointer elsewhere, a hostile index) | private git directory, scrubbed environment, `core.hooksPath=/dev/null`, bounded index (exists) | `hostile_project_content_runs_nothing_in_the_parent` |
| A37 | race publication with the person's edits; crash mid-apply | the held descriptor, the exchange and the verification of the displaced version (4.4.3), the intent log | `publication_never_overwrites_a_concurrent_edit` |
| A38 | apply through a symlink in the live path | refused per component | `publication_refuses_symlinked_live_paths_collisions_and_recovers_from_a_crash` |
| A39 | two stage names that collide on a folding filesystem | refused and reported | the same |
| A40 | lie in a manifest or a size | re-derived, never read from the stage (exists) | `test_coding_workspace_resume` |
| A41 | make the proof pass and the run differ | one `Profile` object; the digest is echoed; the live process is attested; the interval is seconds | `a_proof_with_a_weakened_profile_stops_the_launch` |
| A42 | interfere with the probe through project content or canary collisions | the probe runs isolated (`-I`) in an empty directory outside the stage, with fresh parent-made canaries | `project_content_cannot_change_the_probe` |
| A43 | downgrade between check and start; remove the backend; a flag or variable that skips the proof | the start fails closed; no skip exists; `AllowUnsandboxed` is not accepted | `a_proof_with_a_weakened_profile_stops_the_launch`, `no_flag_variable_or_argument_skips_the_proof` |
| A44 | start a harness by any path that does not go through `isolation` (a custom runner, an injected backend, a new harness module) | the AST test; `Confinement.popen` is the only constructor of the argv | `the_launcher_is_the_only_path_that_starts_a_harness` |
| A45 | use the launcher as a deputy from a sandboxed agent: `--project`, `--protect`, grant flags, `--on` | 8.3: the checks sit at the choke point and key on the process being confined, not only on a marker | `a_launcher_under_an_agent_marker_or_inside_a_confinement_refuses_grants_and_unregistered_projects_and_a_non_loopback_on` |
| A46 | read the machine's serial number, hardware UUID, provisioning UDID or machine id (N2) | G13; the proof reads the identifiers in the parent and searches for them (P22) | `no_hardware_identifier_is_readable` |
| A47 | answer a question put to a person by typing into the harness's own pty, or satisfy the person check with a duplicated pty descriptor (N9) | a confined process is never a person (3.5, I17); no question is read from a terminal the confinement holds | `a_pty_given_as_stdio_is_not_a_person` |
| A48 | have the installer turn off the host's user-namespace restriction, or attach the AppArmor permission to more than one binary (N6) | one literal path, `userns` only, no sysctl, a person-only installer (5.3) | `the_shipped_apparmor_profile_is_scoped_to_bwrap_and_changes_no_sysctl` |
| A49 | swap a toolchain declaration's target between the check and the grant (8.2) | resolved once; the checked descriptor is the granted object; identity re-read after the proof | `a_toolchain_declaration_is_resolved_once_and_granted_by_the_same_object` |
| A50 | learn what the platform cannot hide: the process list, the machine class and host name, the `/etc` account files (5.7) | named exposures X1 to X3, pinned by P23, accepted risk R7 | `the_exposure_set_is_exactly_the_declared_set` |

Accepted risks, named for the owner. **R1** A change to plain source that the person then runs is not judged. **R2** A
conversation's agent home persists instructions between its own turns. **R3** On macOS a process that escapes the follower can
outlive the session, confined and without reach; the barrier reports it and publication is skipped. **R4** Bytes can leave in the
name or timing of a granted host. **R5** The profile language of Seatbelt is not a public interface and the tool is deprecated; the
proof is the only notice of a change. **R6** Windows route B has no verified network rule and refuses. **R7** The exposures of 5.7
stay visible: on macOS the name, pid, parent pid and user id of other processes and the machine class and host name, on Linux the
`/etc` account and host files. **R8** A file that a held file reaches only through a path it computes (string concatenation, an
environment lookup) is named by no held file and is plain source (4.4.1 test 4).

### 14.2 The cases of AGENTS.md section 1

| Case | Attack on this design | Answer |
|---|---|---|
| copied or cloned pool or device | a cloned state root or a copied capability cache claims "enforced" | the cache is keyed by the boot, the operating-system build and the backend binary identity and recomputed; no verdict is read from state or a board; the proof runs on this device at this launch |
| replay | replay a hook frame, a bridge token, a spent grant | the bridge token is per session and dies with it; a hook frame carries nothing the rail does not already fix; a grant is a Requests item with a fingerprint and is single-session |
| forged or below-threshold signature | none is used; a grant is a Requests answer | grants inherit the inbox's person-only answer; the gate `isolation.grant` is `person` in every preset |
| compromised or malicious member | a pool member offers a model whose server abuses the bridge | the bridge treats the upstream as untrusted data; routes and fields are filtered in the other direction too (responses are passed as bytes, never parsed for instructions) |
| stolen phone or key | approve a grant from a stolen phone | no new approval path: a grant is the existing request class; it widens one session only |
| malicious agent | every row of 14.1 | 14.1 |
| agent compromised without knowing it | an injected instruction to read a key, call the network, plant a hook | the OS refuses the first two; the third is held at publication and asked, by structure and not by a name; a burst of refusals reaches the sentinel |
| downgrade, secure to open | a profile that falls back to a weaker one when a mechanism is missing | there is no weaker profile: every missing guarantee refuses; `watched` is named in the report, and an exposure is named and pinned, never silent; in-place is a typed grant |
| approval fatigue | many grants or held asks | one question per kind per session; held changes batch into one Requests item per publication, answered once for the bytes shown; the fatigue ledger applies |
| takeover by whoever is merely present | another local process connects to a bridge or squats a socket | random 0700 directory, the connecting process's credentials, bridge token, the client checks the server; a same-user process outside the confinement is the person, and a process inside one never is (I17) |

### 14.3 The non-author pass: where each finding is decided

Every finding of `docs/design-isolated-launcher-breakit.md` is decided in the section named and carried by the slice named, with
the test that is run on the parent commit and seen to fail first (the full list of 15.1 repeats the slice side). The engine
fixes of F1, F2, F12, F13 and D5 are in `fix/sandbox-holes-and-harness-denial`, and L1 re-runs their tests against the result.

| Finding | Decided in | Slice | Fail-first test |
|---|---|---|---|
| N1 process list readable (G6) | 5.1 G6 narrowed to acts; exposure X1 (5.7); F12; P13, P23 | L1, L2 | `signal_ptrace_and_the_reads_of_other_processes_reach_nothing_outside_and_the_list_is_exactly_the_exposure` |
| N2 hardware identifiers readable | G13 (5.1, 5.5); F13; P22; the sentence of 9; the engine note after the deltas of 5.2 | L1, L2 | `no_hardware_identifier_is_readable` |
| N3 name table misses real auto-run files | 4.4.1 and 4.4.2, deny by default; F14; the tables deleted (W12) | L4 | `the_corpus_of_auto_run_names_is_held_and_ordinary_source_is_applied` |
| N4 other tools' instruction files | 4.4.1 test 2 (dot names), harness records (4.4.2) | L4 | the same, and `every_change_that_is_not_plain_source_is_held_with_its_diff_whichever_tool_wrote_it` |
| N5 `CODEOWNERS` | 4.4.1 tests 2 and 3; 4.4.2 | L4 | the same |
| N6 AppArmor scoping | 5.3 "The AppArmor profile", I10, T21, the sentence of 9 | L7 | `the_shipped_apparmor_profile_is_scoped_to_bwrap_and_changes_no_sysctl` |
| N7 live read not pinned to the write | 4.4.3, W13, T20 | L4 | `publication_never_overwrites_a_concurrent_edit` |
| N8 process count watched, could be enforced | D5, G11 (5.1, 5.2, 5.5), I14 | L1 | `a_fork_flood_is_refused_by_the_kernel_and_a_disk_fill_and_memory_growth_end_the_session` |
| N9 person check satisfied by a pty | 3.5 rules 1 and 2, 8.3, I17, F16, P24 | L1, L2, L6 | `a_pty_given_as_stdio_is_not_a_person` |
| A3 capture weak until W3 | W3 | L4 | `symlink_hardlink_dotdot_and_alias_paths_cannot_leave_the_stage` |
| A6 Unicode forms, Windows names | 13.2 control-name row; Windows PROBEs | L4, L8 | `a_control_name_cannot_be_created_in_any_case_or_normalization` |
| A8 FIFO left out with no name | W3, F18 | L4 | `special_files_in_the_stage_are_left_out_and_named` |
| A9 sparse and many files; replacement bound | W4, 17 | L4 | `a_sparse_file_larger_than_the_volume_and_a_tree_of_small_files_stage_by_progress` |
| A11 table broken | N3 to N5 | L4 | as N3 |
| A14, A21 group kill misses `setsid()` | 4.2 item 3, I18, F15 | L1 (the walk), L4 (the barrier) | `a_setsid_descendant_is_ended_before_the_final_capture` |
| A15 listing | N1 | L1, L2 | as N1 |
| A18 interactive unbuilt | 3.5 | L6 | `interactive_relay_isolates_the_terminal`, `a_pty_given_as_stdio_is_not_a_person` |
| A22 fork count, memory | N8; memory stays watched (M-mem, 5.2) | L1 | as N8 |
| A27, A28, A29 bridge and proxy unbuilt | 6.1, 6.3 | L3 | `the_model_bridge_serves_only_its_routes_and_pins_the_model`, `the_egress_proxy_refuses_private_ranges_rebinding_ports_wildcards_and_literals` |
| A30 environment | 3.4 closed table | L2, L5 | `the_environment_is_exactly_the_allow_list` |
| A33, A34 session bridge unbuilt | 7 | L3 | `a_forged_replayed_or_flooded_hook_event_changes_nothing`, `a_session_cannot_act_as_another` |
| A37 race with the person's edits | N7 | L4 | as N7 |
| A41, A42, A43 proof unbuilt | 10.1, 10.2 | L2 | `a_proof_with_a_weakened_profile_stops_the_launch`, `project_content_cannot_change_the_probe` |
| A44 AST test unbuilt | 15.2 | L5 | `the_launcher_is_the_only_path_that_starts_a_harness` |
| A45 launcher as deputy | 8.3, F17 | L2, L5 | `a_launcher_under_an_agent_marker_or_inside_a_confinement_refuses_grants_and_unregistered_projects_and_a_non_loopback_on` |
| Toolchain grant widened by a hostile declaration | 8.2 (resolved once, granted as checked) | L2 | `a_toolchain_declaration_is_resolved_once_and_granted_by_the_same_object` |
| Memory ceiling | 5.2 (neither `RLIMIT_AS` nor the Mach footprint limit holds; watched) | L1 | `a_fork_flood_is_refused_by_the_kernel_and_a_disk_fill_and_memory_growth_end_the_session` |

## 15. Migration: small landable steps

### 15.1 Slices

All three platforms are in the design from the start: the report, the profile, the proof and the bridges are platform-neutral,
and every slice names what it needs on each platform. The order of turning them on follows what can be verified (this Mac now; the
WSL device and the Windows PC when they are in the pool). A platform whose slice has not landed refuses with its sentence; it is
not skipped and not approximated.

Each slice lands alone, keeps the suite green, updates the documents it touches and prunes `HANDOFF.md`. Fail-first applies to every
test. The order is dependency first; nothing is turned on before the thing that makes it true exists. The last column names the
findings of the non-author pass (14.3) the slice carries; a slice is not done while one of its findings has no red test that has
gone green.

| Slice | Delivers | First red (against its parent) | Findings carried |
|---|---|---|---|
| **L0** the hook rig (independent of isolation) | `test_harness_exec_chain`, `_more` and `test_harness_hook_outages` get a verified board session, or the hook's behaviour for "no session" is decided (see 15.3); the 10 tests pass | the 10 tests | none |
| **L1** the engine | starting from the result of `fix/sandbox-holes-and-harness-denial` (macOS D1 and D5, the net namespace instead of `--share-net`, `--unshare-user --disable-userns --cap-drop ALL`, which are not part of this build): macOS D2; the closure of the identifier channel F13 (a bisect of the profile's `file-read*` grants until `system_profiler` and `ioreg` print no identifier, if the branch has not closed it) or, when no operation closes it, the macOS refusal of G13; the declared exposure constants of 5.7 beside the grants they describe; `sandbox.confined()` (macOS `sandbox_check` of the process itself, Linux the read-only marker file bound into the namespace, Windows the token and job); `sandbox.process_tree` gains the tree end (stop every member, list again, kill, wait; pid and start time identity) the barrier calls; bubblewrap `deny_read` and `deny_write` by read-only binds, `Net.only` with a relay and forwarder for the bridge, `Net.loopback` refused on Linux (the `mcp_server` policy names its ports); `Pipes` outside darwin refuse clearly; a Windows backend that reports unavailable with the reason of 5.4. Closes the Linux half of F3. | the read-only-bind and `Net.only` relay tests; `no_hardware_identifier_is_readable` (macOS); `a_fork_flood_is_refused_by_the_kernel_and_a_disk_fill_and_memory_growth_end_the_session` (the process-count half); a `setsid` descendant is ended by the tree end and not by a group kill; the marker, host-port and nested-namespace tests of F1 and F2 are the other branch's and are only re-run here | N1 (the exposure declaration), N2, N8, N9 (the predicate), A14, A15, A21, A22 and the memory ceiling |
| **L2** the library | `isolation` package: `capability` (guarantees and exposures), `profile`, `grants` (the model, not the bridge; the single resolution of 8.2), `proof` and the probe (P01 to P24, isolated interpreter, empty working directory), the closed environment table, the attestation, the refusal sentences, the launcher's choke-point checks of 8.3; `person.require_person`, `confirm` and `typed` read `sandbox.confined()`; the package in the tools layer. No call site changes. | the sentence tests with an unavailable backend; the weakened-profile mutants; `a_pty_given_as_stdio_is_not_a_person` (the person check, with a real pty); `project_content_cannot_change_the_probe`; `the_environment_is_exactly_the_allow_list`; `a_toolchain_declaration_is_resolved_once_and_granted_by_the_same_object`; `a_launcher_under_an_agent_marker_or_inside_a_confinement_refuses_grants_and_unregistered_projects_and_a_non_loopback_on` | N1 (P13, P23), N2 (P22), N9 (P24 and the person check), A30, A41 to A43, A45, the toolchain grant |
| **L3** the bridges | model bridge, session bridge, egress proxy and the client module, each standalone | the route, smuggling, SSRF and forged-frame tests | A27, A28, A29, A33, A34 |
| **L4** the workspace | W1 to W13 of 4.3: the launch budget, Apple's git, classification, the session API, the barrier of 4.2 item 3 (calls the tree end of L1), `apply` with the swap of 4.4.3 and its intent log, the environment links, the Linux bind hook; `guard.runs_later` with the language registry snapshot, `scripts/refresh_language_kinds.py`, the harness and ecosystem records, the corpus and the oracle; the nightly workflow step; the name tables of `guard.autoexec` deleted. `guard.runs_later` lands first within the slice, so the hooks use it at once. | the budget test, the symlink, umask and submodule fixtures, `special_files_in_the_stage_are_left_out_and_named`, `a_sparse_file_larger_than_the_volume_and_a_tree_of_small_files_stage_by_progress`, `publication_never_overwrites_a_concurrent_edit`, `the_corpus_of_auto_run_names_is_held_and_ordinary_source_is_applied`, `every_name_an_ecosystem_tool_runs_is_held`, `a_file_named_by_a_held_change_is_held_and_shown`, `a_setsid_descendant_is_ended_before_the_final_capture` | N3, N4, N5, N7, A3, A6, A8, A9, A11, A14, A21, A37 |
| **L5** turn it on, macOS | `claude.py`, `codex.py`, `pi.py`, `coding.py`, `Manager._process`, `TaskManager`, `harnessing.opened` use `isolation`; the environment allow-list; Codex flags; the catalogue; `require_isolated_execution` and its 14 sites are deleted; `coding_workspace` leaves `STANDALONE`; the refusal-asserting tests are rewritten; the real-model run on this Mac (Claude Code and Codex, one turn each through the lease on the one slot, writing a file in the stage, publication applying it); `subprocess-spawns` falls. Needs L1 to L4. | the 17 launcher and route tests; the 3 browser tests; `the_launcher_is_the_only_path_that_starts_a_harness` | A30, A44, A45 |
| **L6** interactive | the pty relay; P-tty | an interactive launch draws, reads keys, and cannot reach the person's terminal; `a_pty_given_as_stdio_is_not_a_person` with the real relay slave and `TIOCSTI` text | N9, A18 |
| **L7** Linux and WSL | the tier proven on the WSL device; the CI step and the second CI job that keeps the AppArmor restriction on; the shipped AppArmor profile (`src/poolhouse/isolation/apparmor/poolhouse-bwrap`), `poolhouse security sandbox install` and `uninstall`; cgroup limits, `RLIMIT_NPROC` and seccomp where the probes pass (P-nproc, P-cgroup, P-seccomp) | the whole Linux suite, red until the step exists; `the_shipped_apparmor_profile_is_scoped_to_bwrap_and_changes_no_sysctl` | N6, N8 (Linux half), G13 on Linux (P-ident) |
| **L8** Windows | route A check; route B by the probes P-win-* on the owner's PC, or the standing refusal of 5.4; the Windows names of A6 | `test_redteam_isolation_windows` | A6 |
| **L9** close | `docs/harnesses.md` ("What is not covered") and `docs/agent-sandbox.md` ("Local-model harness") rewritten, `docs/limits.md` rows, `scripts/agent_sandbox_profile.py` exclusions, the red-team rows (T1 to T21), budgets lowered, `HANDOFF.md` pruned | | |

### 15.2 Deleted, and why

| What | Superseded by |
|---|---|
| `harnessing.require_isolated_execution` and its 14 call sites; the 8 test files' assertions of its text | `isolation.capability` for the early refusal and `isolation.prepare` for the launch |
| the unconfined default runners (`subprocess.call(...)` in `claude.py`, `codex.py`, `pi.py`) and the unconfined `platform.start_process` of `Manager._process` for harnesses | `Confinement.popen` |
| `claude.environment` and `codex.environment` copying `os.environ` | the allow-list environment |
| `codex.policy_flags` (`--sandbox workspace-write`) | 8.1 |
| the "Local-model harness: sandbox-runtime" section and the Codex `config.toml` fragment of `docs/agent-sandbox.md` for Poolhouse-launched sessions | this design (the rest of that document stays: it is for the owner's own Claude Code sessions) |
| the first bullets of `docs/harnesses.md` "What is not covered" ("Neither harness is a sandbox against its own model...") | the confinement; the document says what the hook still does |
| `Net.loopback()` on Linux as `--share-net` | the net namespace and relay (L1) |
| the name tables of `guard/autoexec.py` (`RUNS`, `FOLDERS`, `FILES`, `SUFFIX`, `CONFIGS`) | `guard.runs_later.classify` (4.4.1, 4.4.2) |

Not deleted: `coding_workspace` and its tests (16); `coding_actions`, the ledger methods of `Conversations` and `pi_tools` (the
tool-proxy model, kept until the native path is proven; 18.10); `SandboxedBash` and the agent loop's policies (other harness).

### 15.3 The failing tests

Measured on development at `bf17c71cd` with `scripts/test all FILES --no-reuse -n 1` on seven files: 1,589 passed, 30 failed.

| File | Failed | Cause | Slice |
|---|---|---|---|
| `test_claude_launcher.py` | 8 | `launch` returns 2: the refusal sits before the lease and the runner | L5 |
| `test_harness_rails.py` | 4 (`TestLaunch` 2, `TestHarnessSession` 1, `TestCodingAgent` 1) | same | L5 |
| `test_coding_routes.py` | 5 | `Manager.start` raises, the route answers 400 for 202 | L5 |
| `test_coding_browser.py` | 3 | they assert the refusal text, and fail earlier: the chat note shows `argument should be a str or an os.PathLike object ... not 'NoneType'` (a `Path(None)` on the settings or start path of the rig; not chased, because it needs the server-side traceback of a browser run); all three are rewritten in L5 either way, since their expectation is the unconditional refusal | L0 diagnoses, L5 rewrites |
| `test_harness_exec_chain.py` | 4 | `harnesshook.pre` returns deny: `harness_claims.conflict` raises `Denied: local-test has no session of its own on the board` before the classifier decides; `9cd3fde72` made the hook verify the session behind a token and the rig registers none | L0 |
| `test_harness_exec_chain_more.py` | 5 | same | L0 |
| `test_harness_hook_outages.py` | 1 | same: the test expects a node outage to degrade to a warning, and the missing token is reported first | L0 |

So 17 failures are the refusal, 3 assert it, 10 are the session requirement. `test_layers::test_no_import_reaches_above_its_layer`
(`client -> fleet`, from `HANDOFF.md`) was not run here and is not an isolation matter. The tests that stay green and name the
refusal text (`test_coding_admission`, `test_task_coding`, `test_chat_unified_browser`, `test_agent_dependency`,
`test_coding_catalogue_readiness`, and the stubs in `test_pi_harness` and `test_coding_current`) are rewritten in L5 to the two
behaviours that replace it: a refused launch with the capability sentence (an unavailable `Backend` injected), and an admitted
one.

Rewritten launcher tests keep their lease and argument assertions. Their runner stub becomes `runner(confinement)`; the
confinement they receive is real on a capable machine. A machine that cannot confine fails them with the sentence of section 9,
which is the point.

### 15.4 `HANDOFF.md`

The two entries about the isolated launcher and `coding_workspace` are rewritten in the commit that lands this document: the
launcher is designed and the slices above are pending; the tool-proxy stack (`coding_actions`, the ledger methods of `Conversations`, `pi_tools`) is carried as one pending entry: kept, with no caller today and not dead, until the native path is proven; the 30 failures are split into the three causes; the alternative "or the
module and about 800 lines of tests are deleted" is removed.

## 16. `coding_workspace`: keep, with its caller

**Keep.** It has no caller because the launcher that would call it was not built, and the launcher is now to be built. Its
absence from the call graph is the missing launcher, not a reason to delete a module that holds the stage, the race-checked
capture, the epoch rotation, the policy-guarded publication and its receipts, all with its tests (`test_coding_workspace` 350 lines,
`test_coding_workspace_resume` 266, and the fixtures reused by `test_coding_actions`, `test_coding_actions_process` and
`test_coding_observations`).

**What it already provides** is 4.1. **What the launcher adds around it** is 4.2. **What is wrong or missing for the threat
model** is 4.3 (speed, Apple's git, abort-on-one-odd-file, bounds without a source, no ignored files, no apply, no barrier, no
Windows primitives).

**How it gets its caller and leaves `STANDALONE`.** `isolation/workspace.py` is a thin session wrapper: it constructs or reopens
a `Workspace`, runs `begin()` and `finish()` around a launch, and calls `Workspace.apply`. `claude`, `codex` and `pi` reach it only
through `isolation.prepare`. At L5 `tests/test_wiring.py` loses the `coding_workspace` entry of `STANDALONE`, and gains a test that
it has an importer outside `tests/`. `("fleet", "coding_actions")` in `tests/test_layers.py` `KNOWN` is a different debt and is not
touched.

**Truly superseded: nothing in it.** `Workspace.run` and the per-call epoch API stay: they are the tool-proxy model's entry
(18.10). The per-launch API is added beside them.

## 17. Numbers and their sources

No number in this design is chosen by the author. Each is the machine's, the model's, a written standard, an existing constant of
the code (named, and marked when it has no recorded reason), or a measurement the slice makes and a test pins. "Live
confinements" is the count the parent holds of confinements running now; the person counts as one more claimant.

| Bound | Value | Source |
|---|---|---|
| Added launch latency (capability, stage, services, proof) | at most 5 s on a project the size of this repository | AGENTS.md section 5, "A run's fixed cost is budgeted" (fixed overhead above 5 s is a defect); measured floor on this Mac: 1.9 s copy, 0.5 s clone (5.6 W-time) |
| Proof duration | inside the same 5 s, shared by its 24 attempts | the same rule |
| Processes in a confinement | (the system's per-user limit, `kern.maxprocperuid` on macOS, `pid_max` on Linux, minus the user's processes now) divided by (live confinements plus one); on Windows the job's active-process limit takes the same value. On macOS the `RLIMIT_NPROC` of D5 is the user's live process count at launch plus this value | the machine's limit and the live count, read at launch |
| Resident memory of a confinement | the memory the broker reports unadmitted, divided by (live confinements plus one) | the broker's admission figure (`poolhouse-serve status`), read at launch |
| Stage volume (project copy and everything the agent writes) | the free bytes of the stage volume divided by (the peak number of full copies `Workspace` holds at once, times (live confinements plus one)) | the volume; the peak copy count (master, before, stage, result and current capture at `finish`) is counted by the L4 test, not assumed. Replaces the 16 MiB, 256 MiB and 30 s of `source_snapshot` (F7), which are removed (18.2); this is the machine's free space, not a project ceiling or a per-file cap |
| Disk and memory sampling interval | the stage volume budget divided by the write throughput measured while the stage was copied (the time the budget would take to fill) | a measurement made at each launch; a crossing kills the session loudly (5.1) |
| Model request body | the served window times 4 bytes | UTF-8 encodes a code point in at most 4 bytes (RFC 3629) and a token is at least one code point, so a larger text cannot fit the window |
| Session bridge frame | the served window times 4 bytes | a tool input is model output, which the window bounds; replaces `coding_actions.MAX_FRAME` for this channel |
| Requests in flight to the model server | the lease's slot count | one conversation per slot (`docs/harnesses.md`) |
| Bridge token | 32 random bytes (256 bits) | the session token of `docs/design-session-capabilities.md` 1.1 |
| Stage build | progress-based: stalled when the files visited and the bytes advanced do not change | no clock; replaces the 30 s deadline |
| Every wait this design adds (the barrier of 4.2 item 3, the flush and verification read of 4.4.3, the workspace, service and installer steps) | a no-progress guard, then a total guard: the no-progress stretch is the longest silence the step's own recorded runs showed times the project's margin, the total is the recorded typical duration times the project's margin | the hang guards of `docs/design-test-runner.md` ("Hang guards: no progress first, a total second"); both margins are the project's and there is no default number here; a step with no history is guarded by the gaps it produces itself and the report says so |
| Approval wait | `harnesshook.WAIT_S` | an existing constant, unchanged (no recorded reason) |
| Namespace probe | `bubblewrap._probe` timeout | an existing constant, unchanged (no recorded reason) |
| Harness event line | `coding_turns.MAX_EVENT` | an existing constant, unchanged (no recorded reason) |

Every number the slices name beyond these is read from the machine at launch (free bytes, process limit, window, slots) or
measured by a test of the slice.

## 18. Decisions taken

Every question this design raised is decided. Each statement below is built in the section it names.

1. **Default workspace.** A staged copy is the default (I4, 4). It is made by an APFS clone or a reflink where the filesystem
   supports one, and a plain copy otherwise. The 16 s construct of `coding_workspace.Workspace` (F4) is a defect fixed in the build
   (W1), not a reason to edit in place; in-place stays a typed grant (4.5).
2. **No project size ceiling and no per-file cap.** No invented limits. The 16 MiB, 256 MiB and 30 s constants of
   `source_snapshot` are removed (W4); the only size bound is the stage volume's free bytes, a fact of the machine (17).
3. **When edits reach the checkout.** After each completed assistant turn, verified, in interactive sessions too (3.3, 4.2 item 1);
   a path still changing during the capture is held to the next turn or to exit, never applied unverified.
4. **Conflicts.** The person's version wins; the agent's copy is kept and named, and the parent never deletes or overwrites a
   version it has not verified equal to the base or to its own result (4.4.3).
5. **Native Windows.** It refuses loudly until an enforceable per-port loopback rule is shown; the WSL2 route is supported; there
   is no silent weaker mode (5.4).
6. **Linux hosts that restrict user namespaces.** Poolhouse ships, in the repository, an AppArmor profile for the one `bwrap`
   binary that grants `userns` and nothing else, installed by a person at setup with the single administrator prompt and never by
   changing a sysctl; a test pins its scope; without it the launch refuses and names the one-line fix (5.3, 9).
7. **Toolchains.** The project's declared toolchain version directories are readable and executable without a question; the list
   of roots is derived from the project's own files (8.2).
8. **`watched` for G10 and G11.** Acceptable for G10, and for memory and disk, only when the watcher kills the session loudly on
   crossing a limit derived from the machine; never silent. The process count is enforced by the kernel where it can be (D5), not
   watched (5.1, 5.2, 17).
9. **Task commits.** The parent commits the published change set; the task prompt stops saying "commit" (4.2 item 6).
10. **The tool-proxy stack.** `coding_actions`, the ledger methods of `Conversations` and `pi_tools` are kept until the native path
    is proven; no caller is not dead; `HANDOFF.md` carries the entry (15.4, 16).
11. **Excluded commands.** `poolhouse-claude` and `poolhouse-codex` are added to the managed `sandbox.excludedCommands` (5.2).
12. **A second writable project.** None: a session has one writable project (8.2).
13. **Publication is deny by default.** Only plain source is applied without a question; everything else is held and asked once
    per publication, and the held set is derived from structure, the language registry snapshot, the harness and ecosystem
    records and the project's own wiring, with no hand-kept list of names (4.4).
14. **What the platform cannot hide is named.** A fact a confined process can still learn is an exposure, listed in the report and
    pinned by the proof (5.7); the machine's identifiers are a guarantee (G13), and a macOS build that leaves them readable
    refuses to start.
15. **A confined process is never a person.** The person check reads the operating system's own report of the process, in
    addition to its marker and terminal; no question for a person is read from a terminal the confinement holds (3.5, 8.3).
16. **Ending a session walks the backend's record of its processes**, never a process group (4.2 item 3).

Nothing remains open.
