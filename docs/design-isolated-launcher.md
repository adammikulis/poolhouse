# Isolated launcher: enforced isolation for coding agents

Design of the sandbox of a coding agent: isolation the operating system enforces on macOS, Linux and WSL, and Windows.

**When it applies.** A launch the person runs (`poolhouse-claude`, `poolhouse-codex`, `poolhouse-opencode`,
`poolhouse.coding.launch_coding_agent` and a turn of the coding chat) runs the harness on the project folder itself, with the
person's own settings, MCP servers and network (docs/harnesses.md, "In place"), on every platform and in any folder. This design
is what `--sandbox` (`sandbox=True`) asks for, what the workspace's own coding workers run in, and what a delegate runs in on a
computer that can confine. A delegate on a computer that cannot runs on the folder itself in the tier its lead chose, `read-only`
unless named, behind the blocks of docs/harnesses.md ("A delegate"), which are not a boundary. In the rest of this document a
launch means a sandboxed one: a platform that cannot enforce what is claimed refuses it, with the exact reason, and no sandboxed
run starts unisolated. The capability report and the launch itself say what a computer can confine. The
non-author attack pass of AGENTS.md section 1 is `docs/design-isolated-launcher-breakit.md`; section 14.3 names the section that
decides each of its findings, and the slices of section 15.1 carry them with their first red tests; the second pass, `docs/notes/attack-verdict-launcher-no-waiting.md`
(AF1 to AF11), is decided in 14.3 the same way. Every question the design
raised is decided in section 18; nothing remains open. Nothing in a launch or in a session waits for a person: the rule is "Zero
friction", after section 0.

Code read at `bf17c71cd`, an ancestor of development (the files this document names are identical on `2807a9005`). A statement about
code was read, not run. A statement about an operating system carries one of three labels: **VERIFIED** (run on a named device while
this document was written; the commands are in section 5.6), **PREDICTED** (from documentation or code, not run) and **PROBE** (a
check the slice that needs it runs first; until it has run, the design reads the worse case). Devices used: this Mac (macOS 26.6,
build 25G70, Apple M4 Max, `/usr/bin/sandbox-exec` present) and a Linux VM that is **not** the WSL device (Docker Desktop's kernel
7.0.14-linuxkit, Ubuntu 24.04 userland, bubblewrap 0.9.0, Landlock ABI 8). The WSL device and the Windows PC were not reachable
(`poolhouse test devices`: this pool has no other active device), so every WSL and Windows statement is PREDICTED or PROBE.

Read first: AGENTS.md sections 1 and 2 ("Attack first", "Never a fake model", "A number comes from a source", "Reporting a problem":
redesign, not patch), `docs/agent-sandbox.md`, `docs/sandbox.md`, `docs/harnesses.md`, `docs/compromised-agents.md` 2.1,
`docs/compromise-containment.md` tier 1, `docs/design-fail-closed-harness.md` (enforcement never depends on observation),
`docs/design-session-capabilities.md` (custody of a session's secret), `docs/windows-runtime.md`.

**Words.** A **confinement** is the operating-system boundary one coding session runs inside: its profile, its processes and the
services the parent runs for it. The **stage** is the directory the agent sees as its project: a parent-owned copy of the person's
project (`coding_workspace.Workspace`). The **master** is the parent's verified copy the stage starts from. **Publication** is the
parent applying verified stage changes to the person's checkout. A **bridge** is a service the parent runs and the confined session
reaches over the one channel its profile allows. A **grant** is one explicit, logged widening of a session's reach, decided at once by a rule of the launching session (8.3). The **report** is the one message of kind `isolation.report` the launcher posts under its own identity when a publication refused something (4.4). The **proof** is
the escape run of section 10. The **capability report** is the per-guarantee statement of section 9. A **guarantee** is something a
confined process cannot do (5.1). An **exposure** is a fact it can still learn on a platform whose mechanism does not hide it,
named in the report and pinned by a probe (5.7). The **launching session** is the process that starts a launch outside any
confinement: the **lead**, the one agent identity the authority registry names as the person's delegate, or a terminal. A change publication does not apply
(4.4: `.git`, a coding tool's settings, a credential name, a link out of the project) is **refused** at once, with a plain reason
written by the parent, kept as a patch under the state root that the session can read, and reported to the launching session. The findings of `docs/notes/attack-verdict-launcher-no-waiting.md` are cited as AF1
to AF11 (its F1 to F11), because F1 to F18 of 1.4 are defects found while reading.

**In short.** A launch builds one confinement, proves it, and only then starts the harness inside it. The confinement holds the
agent's copy of the project and a private home; it reaches the model only through a parent-run bridge, and the person's state
(hooks and board) only through a second bridge. Nothing else exists for it: not the home directory, not other loopback
services, not the network. After each turn and when the session ends the parent checks what the agent changed, applies the plain
source to the person's project, and refuses everything else at once, keeps it as a patch the session can read and reports it to
the launching session. A platform that cannot do this refuses the sandboxed launch, in one sentence.

## 0. The decisions, one line each

| # | Decision | Why |
|---|---|---|
| I1 | The sandboxed harness process and everything it starts run inside one confinement the launcher builds; no code path of a sandboxed run starts a harness outside it. | The harness vendors' own sandboxes cover less (Claude Code's covers shell commands only; its file tools, MCP servers and hooks run outside it, and it does not run on native Windows: `code.claude.com/docs/en/sandboxing`) and cannot nest (VERIFIED: `sandbox_apply: Operation not permitted` inside a Seatbelt run). |
| I2 | One choke point: `isolation.prepare(spec)` returns a `Confinement`; a launcher's runner starts the harness only through `Confinement.popen`. `harnessing.require_isolated_execution` and its 14 call sites are deleted. | A refusal at 14 places is a policy nobody can prove complete; one place that builds the only argv that exists is. |
| I3 | Default reach: the stage (read, write), a private agent home (read, write), the harness's own install tree and the system toolchain (read, execute), one directory of its own refused patches (read only, 4.2 item 2), and one model bridge. Nothing else: no home directory, keystore, other repository, other loopback service, LAN, internet, unix socket, GPU, other process or machine identifier. | The owner's decision. |
| I4 | The parent-owned workspace is the project the agent sees. The agent never writes the person's checkout; the parent publishes verified changes after each completed assistant turn: the whole change, except what would take effect unread or leave the project (4.4), whichever tool wrote it. A refusal is made at once with a reason the parent writes, kept as a patch the session can read and reported by an `isolation.report` message. Nothing waits and nothing is asked. | A confinement bounds what the agent can reach; it cannot bound what the agent leaves behind for the person's tools to run. Only the parent, comparing bytes, can, and a list of names that run later is always behind the tools that read them. |
| I5 | Writing the person's checkout in place is a named grant, a launch argument decided and logged at launch under the source rule of 8.3, never a fallback, never reachable from inside a confinement and never together with a network host (8.3 caps). Staging has no project size ceiling; the one reason a project cannot be staged is that the stage volume lacks the free bytes, and the launch then refuses with the figures. | No silent fallbacks. |
| I6 | The model is reached through a parent-owned bridge that allows a fixed set of routes, pins the model, and swaps a per-session token for the server's key. The model server's own port is in no profile. | `--on URL` servers carry whatever flags the person started them with; llama-server's slot, props and tool routes are not a coding agent's to call. |
| I7 | Hooks and workspace verbs run in the parent. The command inside the confinement is a client of one session bridge; the confinement never sees the state root (its one read-only directory of its own refused patches, 4.2 item 2, is the only entry it can read), the node socket, a bridge token minted for another session or any credential; the launcher mints and holds every token and none is pasted. An unsure call is denied at once with the reason (the rule of `harnesshook`). Carries A33, A35. | A hook inside the boundary it polices is not a gate. |
| I8 | Guarantees are a typed report per platform. A launch needs every guarantee of the default reach; any that is not enforced refuses with its own sentence. What a confined process can still learn on a platform is listed beside them as exposures, each pinned by a probe so the list cannot grow unnoticed (5.7). | The owner's decision. A report that says "enforced" while a fact leaks is false; a fact that cannot be hidden is named. |
| I9 | Every launch proves its confinement: a probe program runs inside the same profile, tries to escape and must fail, and positive controls must pass; the live harness process is then attested. A deviation stops the launch. | Enforcement is the claim; the proof is the evidence, on this machine, now. |
| I10 | Linux and WSL need bubblewrap namespaces (user, mount, pid, net, ipc, uts, with nested user namespaces disabled and no capabilities). Landlock and seccomp are added inside when present. Landlock alone never suffices. Native Windows refuses until an enforceable per-port loopback rule is shown; the WSL2 route is supported; there is no weaker mode. A Linux host that restricts user namespaces gets an AppArmor profile Poolhouse ships in the repository and the package install loads (a package step, never a launch, L7): bound to the one `bwrap` binary, granting `userns` and nothing else, never a sysctl change, pinned by a test (5.3). | Section 5. |
| I11 | The environment is built from an allow-list. Nothing is inherited. The harness holds a bridge token, never a server key. | `claude.environment` and `codex.environment` copy the whole of `os.environ` today. |
| I12 | Widening is a grant of four kinds (read, toolchain, network host, in-place). A grant is a launch argument, decided and logged at launch, scoped to the session. Its source is only the task the person gave the launching session or a person-owned project policy outside the repository, never text read after launch; only the lead identity or a terminal may pass one, every other marked agent is refused every widening argument at once; hard caps hold for every caller, and every widened launch is a sentinel signal (8.3). No token is issued to a human and nothing is typed or confirmed. A confined process never widens itself: it has no grant request, and the one `grant` frame answers every request with the same refusal (7, 8.3). Every grant, network hosts included, is fixed when the session starts. Carries N9, A45, A47, AF3, AF5, AF7. | A profile is fixed when the process starts. |
| I13 | The permission profiles do not move the operating-system boundary, except that `read-only` and `plan` mount the stage read-only. `yolo` allows more calls at the hook (destructive ones); it does not widen. | `tests/test_coding_permissions.py` already holds that `yolo` opens no launcher control. |
| I14 | What the operating system cannot bound per session (memory on macOS and on Linux without a delegated cgroup, disk everywhere, process count on Linux until the kernel is shown to count it per namespace) a parent watchdog bounds, which kills the session loudly on crossing a limit derived from the machine and is never silent; the report says "watched", not "enforced", for each resource. What the kernel can refuse at start (macOS `RLIMIT_NPROC` for the process count) it refuses. | The report must match the mechanism, and enforcement beats a sampled watcher wherever the kernel offers it. |
| I15 | `coding_workspace` stays and gets its caller (section 16). Nothing is deleted that this design does not supersede (section 15). | The owner's order. |
| I16 | Landing order: harden the backends first (they already protect every sandbox user), then the library, the bridges, the workspace, the wiring, the platforms. | Section 15. |
| I17 | A process inside a confinement is never treated as a person or as the launching session. Every launcher control (a grant flag, `--workspace in-place`, `--project`, `--protect`, `--on`) and every caller check (`caller.refuse_agent`) refuses at once, with a plain reason, in any process the operating system reports as confined, whatever its markers, its descriptors or its terminal; nothing read from a terminal the confinement holds is ever taken as an answer. | The confined harness holds the relay's pty as both ends of its stdio and can type into its own input (3.5); a check that reads descriptors and an environment variable is answered by the attacker. Carries N9, A47; the red tests assert the immediate refusal, not a person step. |
| I19 | Nothing in a launch or a session waits for a person: no prompt, no typed confirmation, no pasted token, no administrator prompt, no phone, no inbox. Every step is decided at once by a rule, or refused at once with the reason, and a refused publication is reported to the launching session. The one host fact that needs a privilege (the AppArmor profile, a missing bubblewrap) is a step of the package install and setup, never of a launch, and is recorded as an exception in "Zero friction". | The owner's order; the section "Zero friction". |

## Zero friction

**The rule.** Using a local coding harness costs a person nothing: no "that is a person command" refusal, no pasted token, no
typed yes, no administrator prompt, no phone, no wait on an inbox. A decision that would put a question to a person is made at
once, in one of two ways: a rule allows it and the decision is logged, or it is refused at once with a plain reason that names
what was refused and what kind of widening would allow it. A refused publication is reported to the launching session by an
`isolation.report` message the launcher posts under its own identity, which no session verb can send (4.4, 7); a terminal
launch gets the line at the end of the turn and the activity record. Where the launch has no lead and no terminal (the chat
coding dialog, a native task), the recipient is the launching session of the task (the session that proposed or started it) and
the report is also written to the conversation record and the task proposal, so a refusal never vanishes. The lead agent, acting
as the person's delegate, can re-launch with a wider rule only from the sources of 8.3 (the task the person gave it, or a
person-owned project policy outside the repository). Credentials (the bridge token, the session token) are minted and held by
the launcher and are never pasted, typed or printed.

**The `prod` preset.** `isolation.grant` is `person` in the `prod` preset (`POOLHOUSE_AUTHORITY_FLOOR=person`): the lead is
refused every widening argument at once, with the reason, and only a terminal outside any agent can widen. That is a decision
made at once, not a wait: nothing is held open and nobody is asked.

**The one exception.** A Linux host that restricts unprivileged user namespaces needs the AppArmor profile of 5.3 loaded by
root, and a host with no bubblewrap needs it installed. Neither is a step of a launch or a session, and no launch prompts for a
password, runs `sudo` or installs anything. Both are steps of the package install and of setup (L7), which already run with the
privilege the person gave to install the package; until the package route exists on a host, the launch refuses at once with the
sentence of section 9, which names the missing fact and no command, and nothing starts unisolated. This is the one place where
zero friction is met by the package and not by a decision of the launcher; it is recorded here for the owner's acceptance and
this document does not grant it.

**What does not change.** Every guarantee, probe and refusal of this design holds. The findings of the non-author pass keep an
owner in this document: each row that replaced a person step names the finding it carries, and the red test asserts the
property (a refused change never reaches the checkout, a confined process never widens itself, a credential never enters the
confinement), never a step by a person. A confined process is never treated as the launching session (I17), and its refusals are
immediate and plain. The lead is a reader of text the attacker writes, so every message from a session is data to its reader
and the report holds only words the parent wrote (AF1, AF2).

**What each slice must not do.**

| Slice | Must not |
|---|---|
| L1 the engine | wait on a person or on a dialog: no `sudo`, no keychain or system prompt, no operator pause in the barrier (a step with no progress ends in a survivor by its hang guard); install a missing backend; answer `sandbox.confined()` with anything but an immediate yes or no |
| L2 the library | build `confirm`, `typed` or a typed flag into a grant; take a token as an argument or an environment variable; let `capability()` run an installer or elevate; phrase a refusal as "ask a person" (it names the kind of widening that would allow it and never an argument string to run); let `grants.decide` return anything but granted or refused; let any non-lead marked agent pass a widening argument |
| L3 the bridges | print, store or accept a pasted token (the launcher mints it and the profile hands it over); hold a `grant` frame open (it is answered in the same round trip, with one fixed refusal for every kind and subject, and never reported to the lead); queue a `hook` frame for an answer (unsure is a denial at once, with the reason); let a session verb send message kind `isolation.report`; create an inbox item or any other waiting record |
| L4 the workspace | hold a change for an answer; open a question, a card or an item; retry a refused change; word the end-of-turn message as a question (it reports what was applied, what was refused and where the patch is); put a file name the master does not hold, patch text or a command to run into a report; write a name into the stage or the checkout for a patch |
| L5 turn it on | add a prompt, an `input()` or a confirmation flag to a launcher; refuse the lead identity a widening its task or the person's project policy names and the caps of 8.3 allow (8.3 refuses a confined caller and every marked agent that is not the lead); print a command a person must run; add a step beyond the arguments `poolhouse-claude` and `poolhouse-codex` take today |
| L6 interactive | let the parent write a question to the relay or read an answer from it: the relay carries the harness's screen and keys and nothing else, and nothing typed there is an answer |
| L7 Linux and WSL | let a launch run an installer or ask for a password: the AppArmor profile and bubblewrap belong to the package install and setup (5.3), a missing one refuses at once with a sentence of section 9 that names the fact and no command |
| L8 Windows | make setup of route B a step of a launch: it refuses at once until the setup exists |
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
| `harnesshook`, `harnesspolicy`, `guard.*` | classifier, denials with the reason, the auto-run name table (`guard.autoexec`, F14) | the hooks the launchers write |
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
| F10 | A hook runs as a child of the harness (`python -m poolhouse.harnesshook ...`) and needs the state root and the node socket; neither can be inside the confinement. | code, I7 |
| F11 | 30 tests failed on development at `bf17c71cd` in seven files (section 15.3). Seventeen are the refusal; three asserted its text and failed earlier in the rig (the rig left `ui.root` unset, so creating a conversation resolved `Path(None)`; fixed); ten (`test_harness_exec_chain`, `_more`, `test_harness_hook_outages`) failed only in a shell that exports `POOLHOUSE_BOARD`, as an agent's does: `harnesshook.pre` resolves its board from that variable, finds no token file for the rig's agent and denies, which is the hook's rule since `108e13a50`. The suite now clears the variables that name a session or a board (`board.session.IDENTITY_ENV`); the hook is unchanged. | measured, section 15.3 |
| F12 | A process inside a Seatbelt run lists every process of the machine, with its name, pid, parent pid and user id, through the per-pid `kern.proc.pid.` sysctl and libproc's `proc_listpids`, with D1 in force: the profile allows the `kern.proc.pid.` prefix (`PROCESS_LIST` in `sandbox/seatbelt.py`); libproc's per-process reads (`proc_pidinfo`: bsd info, working directory, open files) are refused (L1 re-ran it). D1 closes only the arguments and environment. | VERIFIED by the non-author pass (N1): 591 own-user processes enumerated in one scan |
| F13 | A process inside a Seatbelt run reads the machine's serial number, hardware UUID and provisioning UDID with `system_profiler SPHardwareDataType` or `ioreg -c IOPlatformExpertDevice`, with D1 and `(deny iokit*)`, `(deny mach-lookup)` and `(deny sysctl-read)` all appended. The channel is not a cached read: it is two operations the profile never named, which Seatbelt lets through whatever the default says, the property reads of the registry (`iokit-get-properties`: the serial number, `IOPlatformUUID`, the UDID) and the system call of the hardware UUID (`gethostuuid`, number 142). L1 closes both (5.2, `IDENTIFIER_CHANNELS` in `sandbox/seatbelt.py`). | VERIFIED by the non-author pass (N2); the two operations and the closure VERIFIED by L1 on 26.6 |
| F14 | `guard/autoexec.py` is a table of names (`RUNS`, `FOLDERS`, `FILES`, `SUFFIX`, `CONFIGS`) that stands as "the paths that run by themselves later". It returns no reason for 79 of 90 real auto-run file names: git-hook managers (`lefthook.yml`, `.lintstagedrc`), `.lfsconfig`, `action.yml`, `Tiltfile`, `cloudbuild.yaml`, wrapper scripts and jars (`gradlew`, `gradle-wrapper.jar`, `maven-wrapper.jar`), MSBuild and Bazel files (`Directory.Build.props`, `*.csproj`, `*.sln`, `WORKSPACE`, `BUILD.bazel`, `.bazelrc`), `requirements.txt`, `Pipfile`, `.prettierrc.js`, and the instruction files of GitHub Copilot, Windsurf, Aider, Continue and Cline. It does not name `CODEOWNERS`, which decides who must review a later change. | VERIFIED by the non-author pass (N3 to N5) |
| F15 | `sandbox/run.py` ends a run with `killpg`. A descendant that calls `setsid()` leaves the process group: the group kill reached 1 of 255 and 1 of 585 live descendants in the two `setsid` trials, while the follower reported every one. | VERIFIED by the non-author pass (A14, A21) |
| F16 | The caller check is "no agent marker in the environment" (`caller.marked`). A harness in an interactive confinement holds the relay's pty slave as stdio, the allow-list environment carries no marker, and the harness can type into its own input (`TIOCSTI` on its own slave), so a check that reads a marker or a terminal, and any answer read on that terminal, can be satisfied by the attacker. | DESIGN TRACE (N9); the relay is not built |
| F17 | `claude.launch` and `codex.launch` take `--project`, `--on` and every other flag from `argv` and check neither `caller.marked()` nor whether the caller is confined; `--on` is not checked for loopback. | code (A45) |
| F18 | A FIFO in the stage is left out of a capture and the change list names nothing: the published list is empty. | VERIFIED by the non-author pass (A8) |

## 2. Threat model

**The attacker** is the model's output, and anyone who writes text the lead agent reads (a board message, a patch, a file name).
It is prompt-injectable by anything the session reads (a file, a page, a tool result, a
commit message, another agent's message) and may be hostile outright. The lead agent is a model that reads what the session
wrote, so it is a reader to defend as well as a deputy (T22). It chooses every tool call: any shell command, any file edit,
any program it writes and runs, with the session's user id, inside the confinement. It sees the harness's environment and its
command line. It does not choose the harness binary, the launcher, the hook, the bridges or the profile. The harness is trusted
code but not trusted to be bug-free: the confinement holds with the harness itself as the attacker.

**Assets**, in the order the owner listed them: the person's files (home, keystore, credentials, other repositories), the person's
processes and terminal, the state root (tokens, node socket, keys), the model server and the GPU, the network, the
person's checkout (what the agent leaves in it), and the person's attention (what is reported to the launching session).

**Not defended**, and said so: a kernel or sandbox-engine bug (Seatbelt, bubblewrap, Landlock, AppContainer); side channels; a
lead that is itself prompt-injected into inventing a task that names a widening inside the caps (R9: the source rule, the
caps and the sentinel signal of 8.3 bound it; no rule reaches a credential location, the state root or a
confined or non-lead caller); a change to the project that
the person runs without reading the diff (4.4 applies the whole change); bytes smuggled in the path or sub-domain of
a granted host; the exposures of 5.7, which are named and pinned and which no mechanism the platform offers hides.

| # | Surface | What the attacker tries | Where it is answered |
|---|---|---|---|
| T1 | Files | read the home directory, keystore, `~/.ssh`, other repositories; write outside the stage | 5, G1 |
| T2 | Links and names | symlink out, hard link in or out, `..`, `/proc/self/root`, firmlink aliases (`/System/Volumes/Data/...`), case and Unicode aliases of protected names, Windows short names and alternate data streams | 5, 4.4 |
| T3 | Workspace | race the parent's capture; swap the stage root; FIFOs, devices, sparse and enormous files, millions of files; set-uid and mode bits | 4 |
| T4 | Planted code | write a git hook or a hook manager's file, git's own configuration, `.gitattributes`, `.lfsconfig`, `.claude/`, `.mcp.json`, the instruction file of any coding tool, a `Makefile`, `conftest.py`, `package.json`, a wrapper script or jar, a lockfile, a CI workflow, a shell startup file, or `CODEOWNERS` (so that a later change is not reviewed) that the parent or the person runs or relies on later | 4.4 |
| T5 | Processes | read another process's arguments and environment, `ptrace`, `task_for_pid`, signal the parent, list processes | 5, G6, exposure X1 |
| T6 | Terminal and desktop | `TIOCSTI`, `/dev/tty`, Apple events to the terminal, `osascript`, `open`, clipboard, UI automation; typing into the harness's own pty so that its text is taken as an answer or an order of the launching session | 3.5, 5, G7, 8.3 |
| T7 | Persistence | `launchd`, `cron`, `at`, `systemd --user`, scheduled tasks, login items, a process that outlives the session | 5, G10 |
| T8 | Network | any loopback port but the bridge (the node, the broker, the approval UI), LAN, internet, IPv6, UDP, DNS, raw sockets, binding a listener | 6 |
| T9 | Unix sockets | `docker.sock`, `ssh-agent`, D-Bus, X11, `tmux`, abstract sockets, the node's socket | 5, G5 |
| T10 | Model server | routes other than chat (`/slots`, `/props`, `/tools`, `/metrics`, model management), a different model, field smuggling, keys, a second slot | 6.1 |
| T11 | Credentials | tokens in the launcher's environment, the harness's own login (`~/.claude`, `~/.codex/auth.json`), the keychain, browser profiles | 3.4, 5 |
| T12 | Hooks and verbs | forge or replay a hook event, flood refusal reports, run a verb the profile does not allow, act as another session, read another session's token | 7 |
| T13 | Resources | fork bomb, memory, CPU, descriptors, disk fill (including the person's disk), output flood | 5, G11 |
| T14 | GPU and devices | take the GPU from the model server; open Metal, `/dev/dri`, `/dev/nvidia*`, camera, microphone | 5, G9 |
| T15 | Children | double fork, `setsid` (which leaves the process group), daemons that outlive the session or mutate the stage after publication | 4.2 item 3, 5, G10 |
| T16 | Nested confinement | enter a new user namespace or a nested sandbox to regain capabilities | 5 |
| T17 | The launcher as deputy | pass `--project`, `--protect`, grant flags or a hostile `--on URL` to a launcher run by a sandboxed agent | 8.3 |
| T18 | The proof | make the proof pass and the run differ; downgrade between check and start; a flag or variable that skips it | 10 |
| T19 | Machine identity | read the serial number, hardware UUID, provisioning UDID, machine id or network hardware addresses | 5, G13 |
| T20 | The person's own edits | time publication, or hold a descriptor, so that the parent overwrites or deletes a version the person saved after the parent read the file | 4.4.3 |
| T21 | The host's setting | make the installer turn off a host-wide restriction (the AppArmor user-namespace sysctl) instead of allowing one binary | 5.3 |
| T22 | The lead as reader | post a message that looks like the launcher's report; put instructions in a file name, a patch, a grant request or a refusal sentence; talk the lead into a wider launch; reach the launcher from an agent outside the lead identity or from an outer sandbox | 4.4, 7, 8.3 |

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
`workspace/` nor `fleet/`. It reaches the hook classifier and the node only through callbacks the launcher
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
| `Grant` | kind, subject, scope, issuer (the launching session and the argument or rule that named it), the sentence of the decision, the request fingerprint | `isolation.grants` |

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
    policy guard, the services stop, the stage's physical directory is removed (when a change was refused, the stage is kept for a
re-launch of the same conversation, 4.4 step 6). The activity log records the launch (profile
    digest, grants, proof digest) and the end (exit status, what was published, what was refused).

The proof runs after the lease and immediately before the start, so the interval it covers is seconds, not the minutes a model
load takes.

### 3.4 The environment and the harness settings

The environment is an allow-list built by the launcher; `os.environ` is never copied.

| For | Variables |
|---|---|
| Always | `PATH` (agent-home `bin` shims, the programs of the project's environments, git, the `bin` of Homebrew and `/usr/local`, Apple's command-line tools, the system, then the granted toolchains), `HOME` and `TMPDIR` (the agent home), `SHELL` (the system's zsh), `LANG`, `PYTHONDONTWRITEBYTECODE`, `GIT_CONFIG_NOSYSTEM` (Homebrew's system configuration is not readable, and git ends with an error on it), `POOLHOUSE_AGENT` (a marker for the tools the harness runs; the person check never relies on it, 3.5) |
| Interactive only | `TERM`, `COLORTERM`, `NO_COLOR` |
| Claude Code | `CLAUDE_CONFIG_DIR` and `CLAUDE_CODE_TMPDIR` inside the agent home, `ANTHROPIC_BASE_URL` (the bridge), `ANTHROPIC_AUTH_TOKEN` (the bridge token), the model variables, the offline variables and the window variables of `claude.py`, the shell bounds (`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS`, `BASH_DEFAULT_TIMEOUT_MS`, `BASH_MAX_TIMEOUT_MS`, and `BASH_MAX_OUTPUT_LENGTH` from the served window; docs/limits.md), `CLAUDE_CODE_EXTRA_BODY` and `CLAUDE_CODE_EFFORT_LEVEL` where the caller sets them |
| Codex | `CODEX_HOME` (an existing directory), the provider key variable set to the bridge token |
| Pi | `PI_CODING_AGENT_DIR`, `PI_OFFLINE`, `POOLHOUSE_NONINTERACTIVE` |
| OpenCode | `OPENCODE_CONFIG` and the four `XDG_*` homes inside the agent home, and the `OPENCODE_DISABLE_*` switches of `environment.OPENCODE_OFFLINE` (update, model list, Claude Code import, file watcher, the project's own OpenCode configuration), and `OPENCODE_PASSWORD` (the bridge token, the password of the server OpenCode runs on the one loopback port `Rendering.listen` names) |

The table is closed. A variable it does not name does not exist for any harness, whatever the caller's environment, a project file
or a grant holds: a toolchain grant contributes `PATH` entries and no variable, so a toolchain directory whose files name
`SSH_AUTH_SOCK`, `GH_TOKEN` or any other variable changes nothing in the environment. The launcher's own variables
(`POOLHOUSE_*` other than the marker, the node's, the state root's) never enter it.

Facts found by running the harnesses under the existing profile on this Mac (5.6, H-claude and H-codex):

- Claude Code 2.1.308 starts, reads its settings from `--settings`, and tries `/tmp/claude-<uid>`; with no access to it the run
  stops with `EEXIST: file already exists, mkdir '/tmp/claude-501'`. `CLAUDE_CODE_TMPDIR` inside the agent home removes the need.
- It needs `signal` to the processes it started (it sends `SIGTERM` to a child, and a shell command's timeout reaches grandchildren),
  granted as `(allow signal (target children))` and `(allow signal (target same-sandbox))`; VERIFIED on this Mac
  (`tests/test_sandbox_seatbelt.py`) that a grandchild is then signalled and that another run of the same policy and an unrelated
  process of the same user still are not. With the first rule alone a grandchild is refused.
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
  `/Library/Developer/CommandLineTools/usr` (a system tree). `/usr/bin/python3` is a shim of the same kind and does not run; the
  command-line tools' `usr/bin` precedes `/usr/bin` on `PATH`.
- **The stage's `.git` is read-only** (`profile.GIT_DIRECTORY`, a frozen name like the control names, with every ancestor): the
  launcher reads its index after the run, so the box cannot write, remove, rename, link or redirect it. `git status` and
  `git diff` work; a commit fails.
- **The project's environments** (`.venv`, `venv`, `node_modules`) are linked into the stage and granted read and exec by their
  real paths (`Spec.environments`), never write, and their programs are on `PATH`. The link is not a change of the session; a link
  the session replaced is reported as any other file. Each folder passes the credential walk of a read grant
  (`secretpaths.tree_hit`) or is neither linked nor read. The walk does not enter `site-packages` or a nested `node_modules`: a
  credential file there is readable by the box, which can put what it reads only into its answer, its messages to its lead and the
  source it publishes.
- **A delegate** (`poolhouse-delegate`) is OpenCode in this profile, whether it edits or only reads, so it starts only where the
  launcher can confine. Its OpenCode permissions deny every action but its own tools, and Poolhouse's plugin offers the model only
  those; a delegate that only reads is permitted no shell and no editing tool, and its stage is read-only. A run whose plugin never
  reached the parent fails and publishes nothing. That word comes from inside the box, so it shows a plugin that did not load and
  is no boundary: the profile and publication are. A process the box detaches can send such frames and so hold off the no-progress
  limit; `poolhouse-delegate stop` ends the run.
- **A delegate's shell** runs in this profile with no further grant: no network but the model bridge's port (which the shell, like
  the harness, holds the token of), the session bridge's socket and the one loopback port OpenCode's own server listens on, and no
  nested sandbox. The plugin runs each command under a program that ends it at its timeout. Its hooks are judged by their text
  alone (`harnesshook.confined_pre`), so nothing a hook or a tool result holds is run outside the box.

### 3.5 Headless and interactive

The chat dialog and native tasks run the harness headless (`claude --print`, `codex exec`) with piped stdio. `Confinement.popen`
returns a `Popen` with those pipes. The terminal commands run it interactive. A full-screen harness sets raw mode on its terminal,
and the existing profile refuses that (VERIFIED: on a pty slave given as standard input `tcgetattr` works and `tcsetattr` fails
with `Operation not permitted`, with `strict` on or off; `TIOCSTI` is refused in all four combinations of `strict` and controlling
terminal). The launcher therefore owns a pty (a master and a slave end) and relays it to the person's terminal; the harness gets the slave as its
standard input, output and error, and the profile allows terminal ioctls on that one device node and no other. Whatever the harness
injects with `TIOCSTI` reaches only its own input, never the person's terminal or the shell that runs after it. That input
belongs to the attacker. A process in the confinement can type into its own slave where `TIOCSTI` is allowed, and it can open a
pty of its own (`/dev/ptmx`), hold the master end and give the slave to a child as a controlling terminal, so it can write
text that looks like an answer; a descriptor duplicated from its stdio, or a pty it made, passes `isatty()` for stdin and
stdout. Two rules follow, and the second is the one that holds.

1. **Nothing read from a terminal the confinement holds is an answer, and the parent asks nothing on it.** A hook's unsure call
   is a denial at once with the reason (section 7); a refused publication is a line the harness's own screen shows and an
   `isolation.report` message to the launching session; a refused grant frame is a fixed sentence on the harness's screen,
   logged and counted for the sentinel, and told to no one. The relay carries the harness's screen and keys and nothing the parent asks. Where
   the profile language can name an ioctl command, the relay's slave is allowed the terminal-attribute and window-size commands
   and refused `TIOCSTI`; where it cannot, rule 2 alone carries the guarantee and the report says so.
2. **A process the operating system reports as confined is never treated as a person or as the launching session (I17).** The
   launcher controls and `caller.refuse_agent` add a condition to the marker check: `sandbox.confined()` is false. A confined
   caller of a launcher control is refused at once with a plain reason, before any terminal is opened and whatever its markers.
   `sandbox.confined()` reads a fact of the
   process that its descendants inherit and none can change: on macOS `sandbox_check(getpid(), NULL, 0)` is 1 (the call 5.2
   already uses to detect a nested sandbox, VERIFIED) and `sandbox_check(getpid(), "default", 0)` is 1, which holds for a profile
   that starts `(deny default)`, as every profile the engine builds does, and not for one that allows by default and names what
   to refuse, such as the write denial the test runner puts around the whole suite (VERIFIED on 26.6); on Linux and WSL a marker file the launcher binds read-only into the
   namespace at a fixed path is present, and a process inside cannot remove it; on Windows the process token is an AppContainer
   token or the process is in the launcher's job. A dropped marker variable, a duplicated pty descriptor, `setsid`, a new pty and
   a terminal opened by name all leave it true. The check sits in `caller.refuse_agent` itself, so every caller (the authority
   registry, the grant, keystore and wired-memory verbs) is covered, not only the launchers.

PROBE P-tty checks that a profile naming only the relay's slave lets a real full-screen harness draw and read keys, that the
person's own terminal device is refused, that window-size changes pass through the relay, and that text injected into the slave
with `TIOCSTI` changes no decision (a grant, a publication, a launcher control). Until slice L6 an interactive launch refuses
with a sentence that names print mode.

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
2. **The profile gets the stage.** The stage and the agent home are the only write grants. The patches of the changes the parent
   refused (4.4) are kept in one directory under the state root, `refused/<session>/`, which the parent creates before the
   launch, owns and writes with `openat` relative to a descriptor it holds, under names it chooses (`p<k>.patch`, `k` a counter).
   The session gets a read-only grant to that one directory and to no other entry of the state root: a read-only bind at a
   fixed path inside the namespace on Linux and WSL (a bind of one directory, so it does not depend on the stage and survives
   the epoch rotation of `begin()`), the real path of that directory in the profile on macOS, an ACE for the container on
   Windows. The session has no write grant there, so it cannot replace, rename or fill it. No name is added to the project: the
   stage and the checkout hold no patch, the control names stay exactly five, and an in-place session (4.5) reads its patches
   the same way. What the session reads there is what it wrote itself. The five control names are
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
   and start time, so a reused pid is never signalled. Every step is a wait with a hang guard (`docs/design-test-runner.md`, full text `git show ac0639291:docs/design-test-runner.md`,
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
| W12 | `guard.autoexec` is a name table (F14). | 4.4.1: `guard.runs_later` answers from records; the tables are deleted. |
| W13 | `apply` reads the live file, decides, and writes later (N7): the person's save between the two is overwritten. | 4.4.3: the swap is verified on the displaced version and undone when it changed; no version is deleted unverified. |

### 4.4 Publication: what the parent does with a stage

Publication takes the changes of the stage (path, before and after digests, before and after modes) and applies **the agent's whole
change** to the person's checkout, so the person reads it in their diff (owner order, 2026-11-26). It refuses only what would take
effect on this computer without being read there, or leave the project. Every refusal is made at once; nothing waits and nothing is
asked. No grant, rule or argument lifts a refusal.

| Change | Action |
|---|---|
| Any file or link added, changed (the executable bit alone included) or deleted that no row below names: source, build, package, CI and test files, `Makefile`, dotfiles, `.gitignore`, data and configuration, scripts, programs, binaries, instruction files, `CODEOWNERS` | applied (4.4.3) |
| Anything in `.git`: a path with a `.git` component at any depth, compared with case and Unicode form folded, with the HFS+ ignored code points removed, with trailing dots and spaces removed and as `git~1` | never applied; reported |
| A coding tool's own settings, which give it hooks, permissions, plugins or servers: the control names (`CONTROL_PREFIXES`: `.claude`, `.codex`, `.pi`, `.mcp.json`, `.poolhouse-project.json`, each as a whole folder or file) and the settings names of the harness records (`guard.harnesses.settings`: those and `.opencode`, `opencode.json`, `opencode.jsonc`), at any depth | never applied; reported. A control path is not read into the patch |
| What a coding tool or git reads through a link or a gitdir file of the checkout: every link at or under a settings name or `.git`, at any depth, and every `.git` file and the `commondir` of what it names, followed through chains, absolute targets, the links inside what is reached and the links this publication would make. A change at or under a place reached, and a change of a link on the way there | never applied; reported |
| Credential name (`secretpaths`) | never applied; reported; never read into the patch |
| Special file (a FIFO, a socket, a device), submodule, hard-linked file, a link the capture cannot carry (absolute, or leaving the tree) | never applied; reported by name |
| A change that adds, changes or removes a symlink, after which a new link, or a link the checkout holds that it reroutes, would lead out of the project or to a path of the rows above. Each link is followed as the filesystem follows it, over every outcome of the publication (a changed path holds what it holds now or what the change makes it, since a change can be refused or end in a conflict), and again after each refusal until none follows | never applied; reported |
| A change that adds, changes or removes a symlink whose path, or whose new target, is not plain ASCII: no folding table decides whether another spelling reaches it | never applied; reported |
| A path of the checkout that goes through a link, or that holds a link where the session started from none (4.4.3) | never written; reported |
| Name collision: two paths equal under case or Unicode folding (APFS and NTFS fold) | never applied; reported |
| A link to an environment folder of the project that the session removed or replaced (4.2 item 4) | nothing of it applied; reported |
| A file the person changed since the session started from it | the person's version stays; the session's is kept for it to read (4.4.3) |
| A file git does not hold, overwritten or deleted (an untracked file of the person's, a file an earlier launch or turn created) | applied by 4.4.3 after the launcher keeps what it displaces (a restore copy, next): the live file must still be byte for byte the version the stage started from, and a copy that cannot be written stops the change with the reason that its previous version could not be kept |

Build junk (bytecode, `.DS_Store`, cache and dependency folders) is neither applied nor reported.

**What a refusal is.** Each publication makes one decision for the whole change set and records it at once:

1. The changes are applied. The refused ones are not applied and the rest is.
2. The reason holds only words the parent wrote. It gives the count of refused files, the count by cause (one fixed sentence per
   cause), the digest of the patch and the patch's path. A file appears by name only when its whole path matches
   `[A-Za-z0-9._/-]{1,80}` and the master already holds that exact path (a name of the person's own project, not one the session
   chose); every other name appears as `<file 4, 41 bytes, digest ab12..>`. A file name, a patch line, a comment, a commit message
   or any other byte the session chose is never copied into a report, and a report never contains an argument string to run: the
   lead derives launch arguments from its task and the policy of 8.3.
3. The refused changes are kept as one patch per publication under the state root (4.2 item 2), which the session can read, so
   the agent sees what was refused and why and continues its work; the file stays as the agent made it in the stage. Reports are
   folded by the set of refused files and their causes: a publication whose refused set and causes equal a set already reported in
   this launch, or a part of one (each file with its cause is compared by a digest of the two, never by file bytes), is not reported
   again, and neither is it by a re-launch of the same conversation (step 6). The number of reports per launch is capped at the
   session's own share of the board log (17); past it the launcher sends one sentinel signal and no further report, and the
   activity record keeps counting.
4. The parent reports the refusal to the launching session by a message of kind `isolation.report` posted under the launcher's
   own identity, which no verb of the session bridge can send (7): the counts, the sentences, the patch digest and its path.
   The reader treats the patch as data, to diff and never to obey (AF2). When a terminal launched the session, the report is
   the end-of-turn line and the activity record (`isolation.publication`: applied, refused, with digests); where no lead or
   terminal launched it (the chat coding dialog, a native task), the report goes to the launching session of the task and into
   the conversation record and the task proposal.
5. The lead agent, acting as the person's delegate, reads the report and the patch as data. When its own task calls for a refused
   change, it makes it as an edit of its own under its own hook and review rules. The session never does for itself.
6. A re-launch of the same conversation reuses the kept stage instead of rebuilding it (4.2 item 1), so nothing the agent made is
   redone; a file refused before stays refused.

A restore copy is no refusal and posts no message: the end-of-turn line says, in the same words, how many files git does not hold the
publication replaced or deleted, the directory that keeps their previous versions and the first sixteen digits of each digest (a name only
as in step 2), and the activity record keeps the digests. A report that is posted for a refusal carries the same sentence.

A report is one message per publication that refused something and was not already reported, never one per file or per tool
call, so a session cannot flood its launching session (A33). The words rule of step 2 holds for every message the launcher posts about
a session: a conflict, a survivor, a guard refusal and the end-of-session record. The hook does not decide per call what a session may
write: the publication decides once for the whole change set (8.4).

#### 4.4.1 Files of the kind that runs later

The result of a publication names the applied files that run later, **for information**: nothing of the answer holds a change back.
The end-of-turn line gives each with its reason (a name as in step 2, at most `notices.LISTED` of them), the turn and the tally of a
session hold every path (`runs_later`), and a delegate's `result.json` holds them. `guard.runs_later.why` answers from the path and,
for a file in hand, its shape; no list of auto-run names is kept by hand:

| Input | What it supplies | How it stays current |
|---|---|---|
| Shape | an executable bit, a shebang, a program format (ELF, Mach-O, PE, Java class, WebAssembly, Python bytecode) | computed from the file |
| Position | a dot file or dot folder, a file without an extension in a folder of programs | computed from the path |
| The language registry snapshot | the names the registry assigns to a language (`contracts/language-kinds.json`) | generated from GitHub's public language registry (`languages.yml`) by `scripts/refresh_language_kinds.py`, which records the registry revision and a digest; the nightly CI job runs it and fails when the committed snapshot differs |
| The harness records | the instruction and settings names of every harness Poolhouse launches | each harness module's record holds them beside the argv it builds, and a contract test fails a harness record with none |
| The ecosystem records | the names a toolchain loads from an ordinary source position (`conftest.py`, `sitecustomize.py`, `setup.py`, `build.rs`, `*.config.js`) and the build, CI, lockfile and wrapper names | the oracle below runs the real tool and fails a name it runs that no record names |

The oracle is `tests/test_publication_oracle.py`. For each ecosystem whose tool is on the machine it builds a scratch project with a
marker-writing payload at candidate names, runs the tool's offline entry points and records which names left a marker; each must get a
reason. The coverage tests are `tests/test_publication_rule.py`. `guard.autoexec` calls the same `why` for the hooks, with path facts
alone, and keeps its scan of what a written text downloads or runs. A file reached only because another file names it is not listed.

#### 4.4.3 Applying one path

Writing into the checkout is the part with the sharpest edge, because the parent acts with the person's authority on paths the
agent chose, and the person's editors, formatters and build tools write the same files while it does. The agent chooses when a
turn ends and so when publication starts; it never touches the checkout, so the one concurrent writer is the person. The rule is
that **the parent never deletes or overwrites a version of a file it has not verified equal to the base or to its own result**.

- Every write opens the parent directory by file descriptor and each component with `O_NOFOLLOW`, never by path string; a symlink
  anywhere in the live path (`a/` is a symlink to `~/.ssh`) refuses that path and reports it.
- A link is applied by the same steps: what it holds is its target, read without following it; it is exchanged, claimed and verified
  like a file, and a link found where the session started from none refuses the path.
- A file is applied in five steps. They pin the read of the live file to the write, because the person can save the same file
  between any two operations.
  1. **Read and hold.** Open the live file with `O_NOFOLLOW` and keep the descriptor to the end. Record its identity (device,
     inode, size, modification time, change time, mode) and its digest. The three-way check compares base (the manifest the stage
     started from), live (now) and result (the stage). Live equals result: nothing to do. Live equals base: continue. Otherwise it
     is a conflict: the live file stays, the agent's version is kept under the state root for the session and the path is named in
     the end-of-turn message.
  2. **Prepare.** Write the result to a temporary name in the same directory (`O_EXCL`, `O_NOFOLLOW`), with the permissions of the file it replaces (the right to
     run it added or removed as the stage says; a new file takes the stage's mode with the group and world write bits cleared; a person
     who changed since the start whether the file runs makes it a conflict at step 1), flush it and check its digest. Keep a descriptor to it.
  3. **Swap.** Exchange the temporary name and the live name in one operation: `renameat2` with `RENAME_EXCHANGE` on Linux,
     `renamex_np` with `RENAME_SWAP` on macOS, `ReplaceFileW` with a backup name on Windows. The live name now holds the result;
     the temporary name holds the version that was live.
  4. **Verify the displaced version**, through the descriptor held since step 1 and the temporary name: the device and inode, the
     size, the modification time, the mode and the digest read again through the held descriptor equal what step 1 recorded. The
     change time is not compared: the exchange itself renames the held file, which moves its change time with nothing written (measured
     in `tests/test_coding_apply.py`), so comparing it would refuse every application. A save in place after step 1 changes the
     digest (and, unless the save restores the modification time and the size, the identity too), and a save that replaced the file
     by rename shows as a different inode.
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
- **A file git does not hold is kept first.** `git checkout` gives back a tracked file and nothing gives back any other, so the launcher
  guarantees it itself: after step 1 has matched the live file to the base and before step 3 (before the claim of a deletion), the parent
  copies the bytes of the held file, read once more through the held descriptor and compared with step 1's digest and identity (a save
  between the two reads is a conflict and keeps nothing), into the state root at `restored/<session>/<sha256 of the bytes>`. The copy
  is written with `openat` relative to a descriptor of that directory under a temporary name, read-only (mode 0400), flushed, renamed to
  its digest without replacing and followed by a flush of the directory; identical bytes share one copy. It holds only bytes the master
  (or the parent's own last publication) already held, because the live digest equals the base: an ignored file, a file the person made
  after the launch and a file the person changed are conflicts at step 1, so nothing of the checkout's own is read into any copy (F1).
  No grant of the session reaches `restored/` (it is a sibling of `refused/<session>/`, never inside it). The displaced version still
  stays under its temporary name until step 4 has verified it equals the base, and the undo of step 5 exchanges the person's newest
  version back as before; the copy is the second, durable record of the base and is never put in place of a version that changed. A copy
  that cannot be written (`unkept`) stops the change: the live file is not touched and the refusal says so. A tracked file needs no copy.
- A record of the intended writes (an intent log) is written before the first one, with each path's base and result digests and
  the step reached, so a crash leaves a recoverable half-applied state that the next launch of the same project completes or rolls
  back by the same verification, and reports.
- The flush and the verification read are steps with a hang guard on their progress (bytes read, paths finished), as in 4.2 item 3.

### 4.5 In place

`--workspace in-place` (a launch argument decided and logged at launch under the source rule and the caps of 8.3; refused at once for a caller inside a confinement and for any marked agent that is not the lead, and refused for every caller together with a network host) gives the session the person's
checkout itself, with the same control names `deny_write` (Seatbelt: VERIFIED; Linux: a read-only bind over each existing control
path and no way to protect a name that does not exist without leaving a placeholder file in the checkout, VERIFIED, so on Linux the
five names are created empty and read-only by the launcher and removed at the end if they were absent; Windows: a deny ACE for the
container). Credentials in the project (`.env`) are readable, and the grant's sentence says so. There is no publication step; the
parent captures the checkout before and after and reports to the launching session, by an `isolation.report` message with the
words, folding and cap of 4.4 step 2 to 4, every change that 4.4.1 would
refuse afterwards (the same `guard.runs_later.classify`, 4.4.2), which is detection and not prevention, and the decision line says
so; the patch of such a change is kept under the state root and read by the session as in 4.2 item 2, so in-place needs no name
in the checkout. Because `.env` is readable there, a session with a network host is refused (8.3): readable credentials and a
way out never go together. Nothing waits for an answer. The grant is logged (8.2). It covers one project, and a session never has a second writable
project (8.2). It is never chosen by the launcher and never a fallback for size.

## 5. Enforcement per platform

### 5.1 The thirteen guarantees

The capability report (section 9) names these. A launch needs all thirteen. Two of them (G10, G11) may be **watched** (the parent
enforces by watching, and the report says so, resource by resource for G11); no other may. A watcher counts only if it kills the session loudly on crossing a limit derived from the machine (17) and names the limit; a watcher that only logs or warns is `unenforceable`. What a guarantee does not cover is an **exposure** (5.7): a guarantee is something the confined process cannot do, so a fact the platform cannot hide is never worded as a guarantee that holds.

| Id | Guarantee |
|---|---|
| G1 | Nothing is readable outside the stage, the agent home, the harness's install tree, the runtime interpreter that runs the bridge client, the system toolchain, the derived toolchain directories, the granted reads and the session's own directory of refused patches (read only, 4.2 item 2). |
| G2 | Nothing is writable outside the stage and the agent home. |
| G3 | A control name (`.claude`, `.codex`, `.pi`, `.mcp.json`, `.poolhouse-project.json`) cannot be created or changed in the stage; and independently of any platform, nothing in `.git`, no coding tool's settings and no link that leads out of the project reaches the checkout (4.4). |
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
| D2 | `signal_children`: `(allow signal (target children))`, a field of `Policy` (L1) | a harness ends its own children; VERIFIED that signals to the launcher and to an unrelated process of the same user stay refused (`test_a_confined_shell_signals_its_own_children_and_no_process_beside_the_session`) |
| D3 | `Net.only(bridge_port)` plus the proxy port, and `unix_sockets=(session socket,)` | G4, G5 |
| D4 | `strict=True`, `deny_write` of the five control names and their ancestors' unlink, `file-link` and `file-clone` denied | G3; already in the engine, used by `pi_tools` |
| D5 | `RLIMIT_NPROC` set in the child before it executes, as `sandbox/run.py:_limits` sets `RLIMIT_CPU`, `RLIMIT_FSIZE` and `RLIMIT_NOFILE`, to the user's live process count at launch plus the session's share (17) | G11 process count, enforced by the kernel, which refuses the fork past the limit with `EAGAIN`; being fixed now on `fix/sandbox-holes-and-harness-denial`. VERIFIED by the non-author pass (N8): with the limit at the current count plus 40, a 300-fork loop stopped with `EAGAIN` in every run, and with no limit completed all 300. The count is the user's, so other work by the same user uses part of the allowance and the session fails earlier, never later; the person's own processes carry no limit |

F12 has no closing operation (X1 stays, below). F13 is closed by two operations, appended to every profile whose policy has no GPU
(`IDENTIFIER_CHANNELS` in `sandbox/seatbelt.py`): `(deny iokit-get-properties)` and `(deny syscall-unix (syscall-number SYS_gethostuuid))`.
VERIFIED on 26.6: with the profile of M-proc, `ioreg -c IOPlatformExpertDevice`, `ioreg -l`, `system_profiler SPHardwareDataType` and the
`gethostuuid` call returned the serial number, the platform UUID, the hardware UUID and the provisioning UDID; with the two denials none
of them, and none of the sixteen network hardware addresses of `ifconfig`, appears in the output of any of fourteen commands tried
(`test_no_hardware_identifier_is_readable` reads every identifier in the parent and searches the output). `(deny iokit*)` does not
match `iokit-get-properties`, which is why the earlier appended denials changed nothing. A policy with a GPU keeps both operations open,
because Metal's property reads were not shown to work without them, so G13 is not claimed for it (the launcher's profile has no GPU, G9).

**Guarantees:** G1 to G5, G7 to G9, G12 enforced (evidence 5.6); G6 enforced for signals, tracing and the reads of arguments,
environment, memory and open files, with the process list an exposure (X1, 5.7); G13 enforced for a policy with no GPU (L1); G10 watched; G11 enforced for the process count (D5) and for CPU, file size and descriptors (rlimits),
and watched for memory and disk (below). **Does not guarantee:** a Seatbelt or kernel bug; a memory ceiling (neither `RLIMIT_AS`
nor the Mach per-task footprint limit stops a 500 MB allocation, VERIFIED by the non-author pass), so memory stays watched; that a
process reparented to launchd (a double fork that the follower missed) is ended: it stays confined and has lost the bridges and
its stage, the barrier reports it (4.2 item 3) and publication is skipped; that the refusal log is complete (it is advisory,
`docs/design-fail-closed-harness.md`).

**Watched (G10, G11).** The follower (`sandbox/process_tree.py`) names the session's processes; the parent (`isolation/bounds.py`,
`Watch`) ends the session by a walk of that tree, never a process-group kill, and reports when the stage and the agent home pass the
disk bound, when the processes' memory footprint passes the memory bound, when their open descriptors reach the descriptor bound,
when a process has used the CPU bound (also one that ignores `SIGXCPU`), when a file reaches the size bound, or when the follower's
exit report says `SIGXCPU` or `SIGXFSZ` ended a process (`NOTE_EXITSTATUS`; the numbers are in 17). A crossing ends the session,
publishes nothing more, keeps the unpublished work as a patch, and puts the limit that was passed in the result and in an
`isolation.report` to the launching session; a look that cannot be taken ends the session too. What a sampled watcher does not see
is a bound passed and given back between two looks (memory freed, files deleted); the kernel's three rlimits hold meanwhile. A
descriptor refused with `EMFILE` by a process that then closes or exits is refused by the kernel and ends nothing. The process
count is not watched: the kernel refuses the fork (D5).

**Already inside a sandbox.** A launcher that is itself sandboxed (an agent session under `docs/agent-sandbox.md`) cannot apply a
profile (VERIFIED, nested `sandbox_apply` refused). `sandbox_check(pid, NULL, 0)` from `libsystem_sandbox` answers 0 for an
unsandboxed process and 1 for one under `sandbox-exec` (VERIFIED), so the launcher detects this and refuses with a sentence. The
same call is `sandbox.confined()` on macOS, which `caller.refuse_agent` and the launcher controls read so that no process in a
confinement is treated as a person or as the launching session (3.5).
`scripts/agent_sandbox_profile.py` adds `poolhouse-claude` and `poolhouse-codex` to the managed `sandbox.excludedCommands`, so an
agent in the owner's sandbox can run them. They run unsandboxed as the person only until they confine themselves at their first
step, so `sandbox.confined()` is false for them and says nothing about who called. Their arguments come from the agent, so the
caller is resolved by the authority registry (8.3), not by the confinement test: every agent that is not the lead gets the
default reach and no widening argument (`--read`, `--toolchain`, `--allow-net`, `--workspace in-place`,
`--project`, `--protect`). The registry resolves the caller by walking its process ancestry to a registered session (the pid
walk of `docs/design-session-capabilities.md`), so dropping a marker variable changes nothing, and a caller it cannot resolve
as the lead or as a terminal outside every agent is refused as a non-lead (T17, AF3). The exclusion is therefore a way to start
a default-reach session and never a way to a wider one.

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
| bubblewrap with `--disable-userns` and `--cap-drop` | `bwrap --version` and a probe run; 0.9.0 has both (VERIFIED); 0.9.0 has no `--overlay` | Ubuntu 24.04 ships 0.9.0; the package install and setup bring it in (L7), a launch never installs it |
| unprivileged user namespaces allowed | the namespace probe the backend already runs (3 s, cached by binary identity and user namespace) | WSL: the launcher already checks it (`docs/windows-runtime.md`) |
| Ubuntu desktop (not WSL): `kernel.apparmor_restrict_unprivileged_userns` | read it; when 1, `bwrap` fails with `setting up uid map: Permission denied` | Poolhouse ships an AppArmor profile for the one `bwrap` binary that allows it `userns`, and the package install loads it (the profile and its scope are below); a launch never installs it, never prompts and never runs `sudo`: without it the launch refuses at once with the sentence of section 9, which names the fact and no command. PREDICTED; PROBE P-ubuntu |
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
- **Installed by the package and setup, never by a launch.** The install step (`poolhouse security sandbox install`, run by the
  package's own install script or by setup, which already hold the privilege the person gave to install the package) writes the
  profile to `/etc/apparmor.d/poolhouse-bwrap` and loads it with `apparmor_parser`. It is a gate of the authority registry
  decided at once (`docs/agent-policy.md`, "System settings and the authority registry"), as `autostart.install` is: a confined
  process is refused at once with the reason, and the one privileged step the host needs is the operating system's own. No
  launch, harness start or capability check calls it, prompts for a password or waits, and no message to a person names it as a
  command to run. A host where the package route does not exist yet is the exception recorded in "Zero friction" (AF9).
  `poolhouse security sandbox uninstall` unloads and removes it.
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
| G13 identifiers | enforced, VERIFIED (L1: `iokit-get-properties` and `gethostuuid` denied, 5.2); a policy with a GPU is not covered | enforced by G1 (namespace has no `dmi`, machine-id or host interfaces), PREDICTED; PROBE P-ident | as Linux | PREDICTED |
| Exposures | X1 process list, X2 machine class and host name (5.7) | X3 `/etc` account and host files (5.7) | as Linux | PROBE |

### 5.6 Evidence

**Later conflicting probe (2026-11-06):** A native reader on this macOS 26.6 arm64 device read a synthetic marker
from another same-user process via `KERN_PROCARGS2` under both an allow-default Seatbelt profile and a minimal
deny-default profile with `(deny process-info-pidinfo)`. The allow-default profile reported the operation denied
via `sandbox_check`, and a separate physical-path file denial control worked. An independent agent reproduced the
allow-default case and verified that the read fails after the synthetic target exits. Denying `process-info*` or
`sysctl-read` alone still allowed that read, but denying both refused it in that agent's synthetic profile. Other channels
and the harness wrappers were not tested. A subsequent test of the generated `poolhouse.sandbox` profile found the
marker readable at `76a1f1f` and refused the same read when D1 was appended, consistent with M-proc below. The result
depends on the baseline profile; test the exact installed wrapper before using D1 as a credential secrecy guarantee.

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
| X1 | macOS | the name, pid, parent pid and user id of every process the user can see: 591 of the user's own in one scan (F12) | the arguments, environment, working directory, open files and memory of any of them; signals and tracing (G6, D1, D2) | the profile allows the `kern.proc.pid.` sysctl prefix (`PROCESS_LIST`), which a process uses to ask about itself (`psutil`, the harness's tools); the profile is written before the process exists, so its pid cannot be named in it and the prefix serves every pid. When the engine shows a profile that closes it, P13 asserts an empty list, X1 is removed and G6 covers the list on macOS |
| X2 | macOS | the machine class and the host name: the sysctls of `MACHINE_CLASS` in `sandbox/seatbelt.py` (`hw.machine`, `hw.memsize`, `machdep.cpu.*`, `kern.hostname`, `kern.osversion`, `kern.bootargs`); the host name can hold the person's name | the serial number, hardware UUID and provisioning UDID (G13) | the tools size themselves from them |
| X3 | Linux, WSL | the account and host files the profile binds read-only: `/etc/passwd`, `/etc/group`, `/etc/hosts`, `/etc/resolv.conf`, `/etc/localtime`, `/etc/ssl` | the machine id, DMI identifiers, host interfaces (G13), other processes (G6) | the toolchain reads them |
| X4 | Windows route B | decided by the probes of 5.4 | | |

P23 pins the set. The parent lists every sysctl name the machine has and every candidate file of the platform; the probe tries each
and reports which it could read. The readable set must lie inside the declared exposures and the controls of the proof (P20); a
name outside it is a deviation naming the first such name, and the declared lists live in `sandbox/seatbelt.py` (`PROCESS_LIST`
is X1, `MACHINE_CLASS` is X2, `EXPOSURES` holds both) and `sandbox/bubblewrap.py` (`ACCOUNT_AND_HOST_FILES` is X3) as the same constants
that grant the reads: the profile's sysctl rule and the namespace's `/etc` binds are generated from them, so the declaration cannot drift
from the grant (L1; `test_the_system_names_a_process_can_read_are_exactly_the_declared_exposures` is the macOS half of P23, the Linux half is L7's).

## 6. The network

### 6.1 The model bridge

The sandbox never gets the model server's port. It gets the **bridge**, a parent-owned HTTP/1.1 server (one per session):

| Rule | Detail |
|---|---|
| Routes | exact method and path from a fixed table: `POST /v1/messages`, `POST /v1/messages/count_tokens`, `POST /v1/chat/completions`, `POST /v1/responses`, `GET /v1/models`; `GET /health` is answered by the bridge itself and never reaches the server. Every other path is 404 locally: `/slots`, `/props`, `/tools`, `/metrics`, `/lora-adapters`, `/embeddings`, model management, `/infill`, the web UI. |
| Request form | origin-form only. A request-target with `//`, `..`, `%2e`, `%2f`, a backslash, a fragment, or an absolute-form or `CONNECT` target, `Upgrade`, `Expect`, any `Transfer-Encoding` (a body states its length by `Content-Length` alone, which also covers the body with both), a duplicate `Content-Length` or any other field that may occur once, a missing or repeated `Host`, a bare line feed, a folded field or a control character is refused 400 before parsing further. One request is read per connection and the connection is closed after its answer, so a request written behind another is never read; the bridge writes the request upstream itself and frames the server's answer (a malformed or doubly framed answer is a 502, and the cookie and location fields are dropped), which `isolation.wire` and `isolation.model_bridge` do. A query is checked and then not forwarded. |
| Headers | forwarded: `Content-Type`, `Accept`, `Anthropic-Version`, `User-Agent`; added: the server's key. All others are dropped. The list is completed per harness by running it against the bridge (a header the harness needs shows as a failed real-model test); nothing is added on a guess. |
| Authentication | `Authorization` or `x-api-key` must equal the session's bridge token (random, 32 bytes); the harness holds only that. The server's key (`serverkeys.for_url`) never leaves the parent. |
| Body | JSON only, at most the served window times 4 bytes (a code point is at most 4 bytes and a token is at least one: 17). `model` must equal the leased alias (rewritten if empty, refused if different). `id_slot` and `lora` are removed. Streaming responses pass through unbuffered. |
| Concurrency | requests in flight to the server at most the lease's slot count; beyond it the bridge answers 429 and never queues, since the server queues per slot. `GET /health` is answered by the bridge and is not counted. |
| Records | method, route, status, bytes, duration. No body is kept. A refused route is counted and reported to the sentinel as a probe. |
| Waiting | `serving.IDLE_SECONDS` (180 s) bounds the client's silence while it sends a head or a body (a 408 past it); it never bounds the server's answer. Once the request is read the bridge waits on the server without a limit of its own (a cold prompt is read by the model for minutes before its first byte, which is the model working and not the client idle), and ends only when the server or the client closes. A request the bridge cannot read through a fault of its own is answered 500 with a fixed sentence and its traceback goes to the launcher's stderr, never a bare reset: the harness reports a reset as `ECONNRESET` and retries ten times, which is how an unhashable `type` in a tool's schema (`_media` read it as a content part's type) looked on 2026-11-25 as a 191 s failed delegate with no request ever reaching the server. Tests: `a_tool_whose_schema_has_a_property_named_type_is_served_and_the_connection_is_never_dropped`, `an_upstream_that_is_silent_for_longer_than_the_idle_limit_is_waited_for_and_the_client_is_served`, `a_client_that_sends_nothing_is_cut_at_the_idle_limit_and_never_reaches_the_server`, `a_fault_in_reading_a_request_is_answered_500_and_never_ends_in_a_reset`. |
| Upstream | `--on URL` must name loopback (a numeric `127.0.0.1` or `::1`, never a name); anything else is refused with the sentence of section 9. A model on another member is reached through that member's pool offer on this device's loopback, never by a remote URL in the profile. |

**Hardened after the attack pass (M3, M6).** `GET /v1/models` is answered by the bridge from the lease (the alias and the window; never the server's own answer, which
names its model file, the account it runs under and its size) and is never forwarded. A request's fields are an allow-list by route (`model_bridge.FIELDS`): a slot
(`id_slot`, `slot_id`), an adapter (`lora`), the server's cache switch (`cache_prompt`) and every field no route of the harnesses takes are dropped, and an unlisted field is never
forwarded because it is unknown. A picture, sound, video or file named by anything but its own bytes (a URL that is not `data:`, a file id) is refused with a fixed sentence, so
the one process that has the network never fetches an address the model named. **Not verified here:** the pinned llama-server's request parser was not read, so the lists come from
the three message routes of the harnesses and not from the parser; whether the pinned build fetches a remote `image_url` is unconfirmed, and the refusal holds either way.
A busy model waits at most `SLOT_WAIT_SECONDS` for a slot and then answers 429 with `Retry-After`, which the harnesses honour; it waits for a slot, never for a person.

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

A `net` grant puts an exact host on the proxy's allow list when the session starts; the list is fixed for the session and no
request changes it. A host is allowed only when it is on the registry list, a data file (`contracts/egress-hosts.json`) whose
every entry carries its source (the ecosystem record of 4.4.2: the index host the tool's own documentation names as its default)
and the bytes bound below; a launch rule that names any other host is refused for every caller (8.3 caps). The proxy is a
CONNECT-only HTTP proxy beside the bridge. It tunnels TLS end to end on port 443 only (never plain HTTP, so there is no redirect
to follow), resolves the name itself, refuses a result in a loopback, link-local, private, multicast, shared-address or
documentation range, and connects to the address it resolved (no second resolution). It refuses wildcards and an IP literal.
A tunnel carries bytes in both directions, so an allowed host can be sent to as well as received from (a `twine upload` or an
`npm publish` with a token the prompt supplied is such a send). The proxy answers only a client that presents the credential the launcher minted for the session (`Proxy-Authorization`, carried in
`HTTPS_PROXY` as `http://poolhouse:TOKEN@127.0.0.1:PORT`, built by the launcher, never printed or pasted): any other caller gets one fixed 407 and the sentinel hears
nothing, so a local process can neither spend the session's bound nor raise its events. It counts bytes per host and per direction. Two bounds are recorded for
each host in the registry list, each measured with its command and recorded beside the entry (17): what **one tunnel** may send (the most one tunnel sent in a small
install or in a larger one, so a pip install that carries a whole install in one tunnel is not cut) and what **the session** may send over all its tunnels (the larger
install and one more of the small one, so a second normal install or a retry is not cut: the covert channel the design accepts is a few kilobytes to a package index, and
the bound is sized by that and not by one command). A tunnel is cut when the bytes it sent pass the first, and the host takes no new tunnel once the session has sent
the second, with a sentence the client can read. A download costs nothing against either bound; an upload the size of a stage is cut. Every IPv6 form that carries an IPv4
address (`::a.b.c.d`, `::ffff:0:a.b.c.d`, mapped, 6to4, Teredo, the NAT64 prefix) is blocked whole. Waits use `selectors` (a descriptor past 1024 works), the head of a
request has a total deadline, and the accept loop retries what a sound listener fails with (no descriptor, a reset before the accept) and tells the sentinel when the
listener is gone. The env var `HTTPS_PROXY` names the proxy; with no host in the launch rule the
list is empty. A tool that ignores proxy variables has no route and fails, which is the safe failure. The bytes counted per
host and direction are part of the end-of-session record, with the host names.

### 6.4 What stays open

A granted host carries bytes in the body a tunnel sends (up to the bound of 6.3), its path, its sub-domain, its request headers
and its timing; the proxy sees the hostname and sizes only, so it cannot see a TLS name that differs from the CONNECT name on a
shared CDN host (`docs/harnesses.md` lists the same residual for a plain GET). Pool offers reached through the bridge's upstream carry
whatever the offered model sees. The model's own output reaches the person unreviewed.

## 7. The session bridge: hooks and workspace verbs in the parent

The harness runs a hook as a child, and the classifier, the board client and the ownership claims need the
state root and the node socket. None can be inside the confinement (F10). The **session bridge** is one parent-owned service per
session on one channel (a Unix socket in a 0700 directory with a random name; a named pipe on Windows). The channel is the only
endpoint in the profile besides the model bridge.

| Part | Rule |
|---|---|
| Authentication | the connecting process is checked on accept (`LOCAL_PEERCRED` on macOS, `SO_PEERCRED` on Linux): the user id is the parent's. The directory's mode and random name keep other sandboxed sessions out; the client verifies the server's pid is the launcher's on connect (T12). |
| Rail | the permissions, the execution mode, the agent name, the roots and the protected paths are fields of the bridge object the launcher built. No frame carries or changes them (`harnesshook`'s rule, kept: "never read from the environment or from the call"). |
| Frames | one line of JSON (`{"verb": "hook" \| "workspace" \| "grant" \| "tools", ...}`, a line feed ends it, which is what the proof's P21 sends and reads), at most the served window times 4 bytes (a tool input is model output, which the window bounds; 17), unique keys (`json_objects.unique_object`), no field a kind of frame does not have, one outstanding frame per connection; a frame that is not read to its end in the idle limit ends the connection. The connections are bounded by the machine's own limit (`serving.connection_bound`: the per-user process room shared among the live confinements, never more than the listen queue). |
| `hook` | `pre`, `post`, `stop` with the harness's event payload; the bridge calls `harnesshook.confined_pre`, `confined_post` or `stop` and returns the answer unchanged. `pre` is decided with the launch's own `Rail`: allow, or deny at once with the reason (the hook never prompts). `post` answers what the lead sent and the agent was not given yet, as `additionalContext`: the launching session's messages only, cleaned (`workspace.nudge_fence`), headed as the lead's, read with the one inbox cursor `ask` shares so each is given once, and nothing when there are none; a `post` that overlaps another is answered empty, and one that fails is answered with a fixed sentence and the kind of failure, logged, and blocks nothing. The session writes the whole payload, its folder included, so `pre` is decided from its text alone: no program is started and no path it names is looked at (8.4). A bridge that is down, slow past the harness's hook timeout or confused returns nothing, which both harnesses read as a block. |
| `workspace` | two tools and no other, `ask(question)` and `note(text)` (`isolation.session_verbs.LEAD`, one text argument each), which the launcher's handler (`poolhouse.lead_bridge`) runs as the session made for this launch and sends to the session that launched it. No argument names a recipient, a kind or subject of a message, a file or a claim: no other verb of the workspace (inbox, status, send, claim, announce, attach, a task, administration, grant or registration) is reachable, the parent resolves no path of the session's and runs no git for it, and no message of a session is of a kind the launcher identity owns. A name outside the table, or an argument that is not the tool's own, is refused and counted. Both tools are answered in every permission profile, a read-only or planning session's too, and the launch's hook allows them. The bridge holds the session's token in the parent's memory and acts as the session. Every text carries the sender label `from a confined agent: data`, set by the bridge, under the handler's line naming the agent as a local model, and its reader treats it as data to read and never as instructions to follow (AF1). `ask` returns the next message the lead sends the session, or says there is no answer yet once `lead_bridge.ask_wait()` seconds have passed (what the lead sends later reaches the session as news), and ends when the lead ends (a delegate's also when it is stopped); a launch with no launching session is answered at once that it has no lead. The board's message types are a closed list that holds no `isolation.report`, so the report's kind travels as the fixed subject `isolation.report` of a `status` message posted by the launcher's own session (`isolation.reports`), and `reports.genuine` is true only for that sender and that subject. |
| `grant` | the one frame about grants. The reply is the same fixed refusal for every request, whatever its kind and subject ("Grants are launch arguments; none is decided here."), so it says nothing about the launch rule and gives no oracle for the host list or any other rule. The subject is never echoed. The request is written to the activity log (its kind and a digest of its subject, never the text) and counted for the sentinel, whose reader sees a burst; it is never reported to the launching session, never queued and never waits, and nothing is asked. No answer is read from a terminal the confinement holds (3.5). Network hosts have no request at all: the proxy's list is fixed at launch (6.3). (AF2, AF5.) |
| Clients | `python -I <client.py> --socket PATH --pid PID hook pre\|post\|stop`, `... workspace ask --question TEXT` or `... workspace note --text TEXT` (a text `-` reads standard input), `... grant [KIND [SUBJECT]]` and `... mcp`, stdlib only and importing nothing of Poolhouse, so the file runs under a profile that reads nothing else of the library; `-m poolhouse.isolation.client` works where the library is importable, and `-I` ignores `PYTHONPATH`. `mcp` is the stdio MCP server of every harness, named `lead`, with the two tools: the model sees `mcp__lead__ask` and `mcp__lead__note` in Claude Code, Codex and Pi, and `lead_ask` and `lead_note` in OpenCode, whose plugin hands the hook the former names (the file each harness reads it from: docs/harnesses.md, "The workspace"). The launcher writes the socket path and its own process id into the hook command and that server's command; no token exists. On connecting, the client asks the operating system whose process listens on the socket (`LOCAL_PEERPID`, `SO_PEERCRED`) and sends nothing to any process but the launcher. A call that is not answered exits 2 (blocked) and writes nothing to standard output; a notification that is not answered does not block. The interpreter is the runtime's own, read-only. |

The bridge makes the hook a client of a gate that is outside the thing it gates. It is not the boundary: a harness tool that never
calls the hook (a subagent, an MCP tool, a background command) is still inside the OS boundary, and the proof does not depend on
the hook.

**Hardened after the attack pass (M4, M5, L4, M8).** A frame is budgeted: a session sends at most one frame a minute for each 512 tokens of its window (never fewer than 60;
a ceiling no harness comes near, not a measurement), and the rest are answered with one fixed sentence; one `hook post` runs at a time and the others are answered as the last was;
a number that is not finite is no number and a time to live is held at the configured `claim_ttl_s`; every failure of a verb tells the session a fixed sentence and the kind of
the exception (the detail, with any path of the parent, goes to the log); a bridge is built with an admit rule or the reason it has none, which is logged when it starts; a plain
name is at most 64 characters. The labels on what a reader sees (M8) and the reads a confined session may make of the board belong to the workspace slice (L4).

## 8. Profiles, modes and grants

### 8.1 What each profile grants

| `--permissions` (and mode) | Stage | Network | Hook decides | Codex flags |
|---|---|---|---|---|
| `read-only`; any profile with `--execution-mode plan` | read-only mount; `finish(writable=False)` asserts the manifest is unchanged; the agent home is writable | bridge only | editing and sending tools denied at once; a shell command runs against the read-only mount | `--sandbox danger-full-access --ask-for-approval never` |
| `guarded` (default) | read-write | bridge only | every call allowed that does not name the launcher's own files | same |
| `yolo` | read-write | bridge only | the same as `guarded` | same |

Codex's own sandbox flags are replaced because its macOS sandbox cannot start inside this confinement (VERIFIED nested refusal)
and the mount enforces what `--sandbox read-only` meant. The OS boundary is the same for the last two rows by construction,
and inside it so is the hook.

### 8.2 Grants

| Kind | What it adds | Takes effect | The decision line (logged and printed) |
|---|---|---|---|
| `read PATH` | a read-only mount or read grant of one directory (not a file under `~/.ssh` or any `secretpaths` name, not an ancestor of the state root) | next launch | "This coding session can read PATH (it cannot change it)." |
| `toolchain PATH` | read and execute of one toolchain version directory. A directory the project itself declares (`.python-version`, `.venv/pyvenv.cfg`, `rust-toolchain`, `.nvmrc`, `.tool-versions`) is granted at launch by the declaration; one the project does not declare is granted only when a launch argument names it, and otherwise refused at once | next launch | "This coding session can run the Python at PATH." |
| `net HOST` | an exact host that the registry list of 6.3 holds, on the egress proxy's list for the session | at launch (the list is fixed for the session) | "This coding session can reach HOST, and can send to it as well as receive from it; bytes sent are cut at a bound." |
| `workspace in-place` | 4.5; never together with a `net` host | next launch | "This coding session changes your files directly in PROJECT. Files like .env in that folder are readable to it, and nothing checks the changes before they land." |

A refusal has one form: "Refused: a network host is not allowed for this coding session." The sentence names the kind of
widening and never a person, and it never contains an argument string to run, a subject the session chose or a command: the
launching session derives its launch arguments from its task and the policy of 8.3. The launch arguments are `--read PATH`,
`--toolchain PATH`, `--allow-net HOST` and `--workspace in-place`; they are the launching session's
vocabulary, a confined process has none of them (8.3), and an agent that is not the lead has none of them either.

The **system toolchain** is the default and needs no grant: macOS `/usr`, `/bin`, `/sbin`, `/System/Library` and the other system
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
subject, scope, issuer (the launching session's identity and the argument or rule that named the grant, with its request
fingerprint), the profile digest it widened, and the decision line. A refusal is a record of the same kind with the reason. The
launch banner and the coding dialog list the grants in force, and the end-of-session record lists the grants used. Every launch
with a widening also sends the sentinel one `isolation.widened` signal (the kinds, the digests of the subjects, the source of 8.3
and the issuer), because a log has no reader and the sentinel is the one reader that exists; in-place is marked unusual (AF7).

### 8.3 Who may widen a session, and the launcher as a deputy

**The rule.** A grant is decided at once, by `isolation.grants.decide(request, rule)`, which returns granted or refused and
nothing else. The rule is the launch arguments of 8.2 and the declarations of the project (8.2, toolchain), and a widening
argument has two sources only: **the task the person gave the launching session** (the person's own words for this launch) or
**a project policy the person owns, kept outside the repository** (the registered-project record under the state root, never a
file or control name inside the project, written by the person at registration and never as a prompt; default empty, and when
present it names the widenings the project may ever be launched with, so an agent that holds it cannot pass a widening it does
not name). It is never a board message, a patch, a project file, a refusal sentence, a tool result or anything else read after
launch. The launcher cannot see where an argument came from, so the property is made checkable in three ways: the issuer and the
claimed source are recorded with each grant (8.2); the caps below hold whoever asks and whatever the source; and the sentinel
sees every widened launch (8.2). The launching session is a process outside every confinement, the lead (8.3, gate) or a
terminal.

**Who is the lead, an agent or a terminal is read from the process tree, by the node.** The environment is never the evidence. The node reads the processes behind a
request by the process id the operating system reports for the other end of its socket (`lineage`), and answers with the registered sessions (a person's session and the
project-placing session are no agents) that still run a process the caller has above it (`isolation/ancestry.py`). A caller none of them runs in is a terminal; one of
them runs in is an agent; the lead is a main session among them; a node that does not answer, is older than this code or cannot read the process refuses the launch at once
with the reason. The environment only narrows: a subagent's marker or the name of a helper of the tree makes the caller a helper and refuses it; a name or a marker never
makes a caller the lead, so `env -i` does not hide an agent (the `person` gate refuses it) and a process outside the lead's tree that names the lead is refused. The
authority registry, its audit log, the project policies and the node's state are the account's own (`home.guarded`, `home.real_roots`): the environment moves them only in a
test run. The person's policy belongs to the project the node has registered around the launch's folder (a worktree and every other place of it give the same project),
the temporary-folder exemption is gone, and a git repository the launch works in (the one named by `--project` or the folder the launcher runs in) that the node did not yet hold
is registered by the launch itself, through the node's place verb, for the lead and for a terminal outside any agent: naming a project is the base reach of a launch (the
confined copy and the publication of the stage) and widens nothing, so there is no terminal step and nobody is asked. The registration is logged (`isolation.project_registered`)
and sent to the sentinel as one signal of that name, which carries the project's id, a digest of its place and the issuer. A folder that is the person's home or one around it,
Poolhouse's state root or one around it or inside it, a folder of the operating system, the working copy of another coding session, or a credential location (`secretpaths`) is
refused at once with its own plain sentence (`isolation/places.py`), a folder that is no git repository is refused with the sentence of section 9, and a copy of a registered
repository is a project of its own with no policy of the person (R9). Any other agent, a subagent or an agent the node cannot place, registers nothing: it works only in a project the node
holds, and an unregistered folder is refused to it with the reason. `poolhouse-workspace project remove PROJECT` (its id, or a folder of it, one that no longer exists too; the lead
or a terminal only) forgets a project at once, so an orphan record does not hold the place around it. **What the tree cannot
attest:** inside one harness the lead and its subagents are one process tree, and a process that leaves its tree (a double fork, a pseudo-terminal of its own) is read as
a terminal; against a subagent that rewrites its own command line the gate is the harness hook's marker and no more. What would attest it is a wrapper process the hook puts
above each subagent command and registers with the node as that helper's anchor, which a subagent cannot take out of its own ancestry: that is a change of the harness hooks, not
of the launcher, and is the open step of H2. The decision is made and logged at launch, scoped to the one session it names, and issues no token to anyone: nothing
is typed, confirmed or pasted. A request outside the rule is refused at once with the reason and the kind of widening, and it is
reported to no one: only a refused publication is reported to the launching session (4.4). A subject that no rule can name is
refused for every caller: a credential location, an ancestor of the state root or of the person's home, a control name, a
special file.

**The caps.** Each widening has a cap that holds for every caller, the lead and a terminal included, and for every source:
never `workspace in-place` together with any `net` host (a
readable `.env` and a way out are the exfiltration this design stops, AF5, AF7); `net` hosts only from the registry list of 6.3;
and one sentinel signal on every widened launch (8.2). The launcher enforces them at the choke point before any argument is
decided.

**A confined process never widens itself (I17).** A process the operating system reports as confined (`sandbox.confined()`,
3.5) is refused at once, with a plain reason, for every grant argument, `--workspace in-place`, `--project`, `--protect`, `--on`
and `--as`, whatever markers, descriptors or terminal it holds; it is audited (`caller.record`) and nothing it types or writes
counts as an answer. A `grant` frame from the confined session over the session bridge (7) is answered in the same round trip
with one fixed refusal for every kind and subject, logged and counted, and never reported to the launching session: it widens
nothing, because every grant is fixed when the session starts, and it tells the session nothing about the rule.

**The gate.** `isolation.grant` is a gate of the authority registry, decided at once (`docs/agent-policy.md`, "System settings
and the authority registry"): `delegated` only to the lead identity, which the registry resolves by walking the caller's process
ancestry to a registered session (the pid walk of `docs/design-session-capabilities.md`), never by a marker the caller can
drop; its arguments pass the caps and are audited with who ran them. Every other marked agent (a subagent, a tool, an agent in
the owner's sandbox that runs the launcher outside it, 5.2) and every caller the registry cannot resolve as the lead or as a
terminal outside every agent is refused at once, for every widening argument together: `--read`, `--toolchain`, `--allow-net`,
`--workspace in-place`, `--project` and `--protect`. It gets the default reach for the registered project of its
working directory and nothing else. `POOLHOUSE_AUTHORITY_FLOOR=person` (the `prod` preset) reads the gate as `person`, which
refuses the lead too and passes only a terminal outside any agent. Nothing prompts and nothing waits in either reading.

**The deputy checks.** A launcher run by an agent takes `--project` only from the lead (the gate above), works only in a project the node
holds or the launch registers for the lead (8.3, the registration), and refuses `--protect` and `--on` values that name anything outside loopback. These checks sit at
the choke point, in `launch_coding_agent` and `isolation.prepare`, before an argument reaches a launcher's parser, and not in
each launcher (`claude.launch` and `codex.launch` read them from `argv` unchecked, F17). `--on` is checked for a numeric loopback
address for every caller. A widening is therefore never reachable by the model, by an injected instruction inside a
confinement, by a process the confinement holds or by an agent that is not the lead; it is reachable by the lead from the two
sources above and inside the caps, and R9 names what that costs.

### 8.4 What the hook keeps doing

The OS refuses what the classifier used to ask about (a download has no route; a write outside the stage has no path). The
hook keeps two denials, both decided from the text of the call: one that names the launcher's own files or the state root, and,
in a read-only or planning session, an editing or sending tool. It reads no command: the classifier of a harness on the host
runs git and Python in the folder an event names and looks at the files it names, and a confined session writes that folder and
those names, so running it for a boxed event executed the box's own code outside the box
(`tests/test_redteam_isolation_session.py`). A call that cannot work offline fails in the box. The hook does not decide about
what a session writes, which publication decides once for the whole change set.

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

The sentences are what an agent relays to a person (AGENTS.md section 2, "Onboarding is the product"). No sentence names a command
for a person to run, an argument that widens a session, or a step that waits: it states the fact, and where the remedy is a step
of the package install or setup it says Poolhouse's installation does it (AF9, AF11). The missing-bubblewrap and AppArmor
sentences are the host facts of the exception recorded in "Zero friction":

| Condition | Sentence |
|---|---|
| macOS, `sandbox-exec` missing | "This Mac cannot confine coding agents: its built-in sandbox tool is missing." |
| The launcher is itself sandboxed | "This command is already inside a sandbox, and a sandbox cannot start inside another; start it from a terminal or from a session that is not sandboxed." |
| Linux, no bubblewrap | "This computer cannot confine coding agents until Poolhouse's installation has added bubblewrap." |
| Linux, bubblewrap without `--disable-userns` | "The installed bubblewrap cannot stop a coding agent from starting its own sandboxes (it lacks the option that turns that off)." |
| User namespaces off | "This computer's security settings stop coding agents from being confined (unprivileged user namespaces are off)." |
| AppArmor restriction | "This computer's security rules block the sandbox tool until Poolhouse's installation has added its permission, so the coding agent was not started." |
| AppArmor permission changed | "The permission installed for the sandbox tool is not the one Poolhouse ships; Poolhouse's installation restores it, so the coding agent was not started." |
| macOS, the machine's serial number or hardware identifiers are readable inside the box | "This Mac's serial number and hardware identifiers are readable from inside the safety box, so the coding agent was not started." |
| WSL 1 | "Coding agents need WSL 2; this is WSL 1." |
| Project on a Windows drive under WSL | "This folder is on a Windows drive; coding agents can only be confined for folders inside WSL." |
| Native Windows network | "This Windows computer cannot keep a coding agent's network to the model alone; run it in WSL 2." |
| The stage volume is too small | "This computer has `M` MB free for the working copy and this project needs `N` MB; free some space and the agent will start." |
| No trusted git | "Coding agents need git, and none was found that Poolhouse can trust." |
| `--on` names a non-loopback server | "A coding agent can only use a model server on this computer; `URL` is not." |
| The project is not a git repository | "A coding agent works on a copy of a git project, and this folder is not one." |
| The project is the person's home or a folder around it | "Refused: a coding session cannot work in your home folder or a folder around it." |
| The project is Poolhouse's state root, or around or inside it | "Refused: a coding session cannot work in the folder where Poolhouse keeps its own state or around it." |
| The project is a folder of the operating system | "Refused: a coding session cannot work in a folder of the operating system." |
| The project is inside another session's working copy | "Refused: a coding session cannot work in the working copy of another coding session." |
| The project is a credential location | "Refused: that folder is where credentials are kept, so no coding session can work in it." |
| An agent that is not the lead names an unregistered folder | "Refused: this folder is not a registered project of this computer, and only the session that launches coding agents for the person registers one." |
| The node does not take the folder as a project, or the project to remove is unknown | "Refused: this computer could not make this folder a project (`reason`)." and "Refused: this computer could not remove that project (`reason`)." |
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
| P24 | call `caller.refuse_agent` and the launcher's grant control with the marker environment empty and a terminal on both ends forced true, then feed the terminal the text `yes` | `AgentRefused` at once with the reason; the grant is refused and the text changes no decision | I17 (3.5) |

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
| A change publication does not apply (4.4) | at once: that change is not applied and the rest is; it is refused with a reason the parent wrote (counts, cause sentences, digests), kept as a patch under the state root, and reported; nothing waits and nothing is asked | the launching session, by an `isolation.report` message from the launcher identity, folded by file set and cause and capped per launch | the stage (kept for a re-launch of the conversation) and the patch under the state root |
| The displaced version changed between the read and the swap (4.4.3) | the swap is undone, the person's version stays under the name, the agent's is kept under the state root, the path is named | the launching session | per 4.4.3 |
| The harness exits non-zero or is cancelled | the tree is ended, the barrier passed; **publication still runs** on what verified: the stage is the person's work in progress, and the checks decide, not the exit status | the launching session reads what was applied and what was refused | per 4.4 |
| A process survives the barrier (macOS orphan) | publication is skipped, the survivor's pid is reported, the stage is kept until the survivor is gone or the conversation ends | the launching session | the stage |
| Publication refused by the policy guard (permissions changed, project withdrawn, revision moved) | nothing applied; the previous receipt stands | the launching session | `test_revocation_after_child_returns_rejects_its_captured_edits` |
| A confined process asks for a grant or a launcher control | refused in the same round trip: a `grant` frame by one fixed sentence for every request, a launcher control with the reason (8.3); nothing is queued | the activity log and the sentinel counter; the launching session is not told | nothing widened |
| Crash of the launcher | the harness is a child in a pid namespace or a job, both of which end with the launcher; on macOS its processes stay confined and have lost the bridges, and the next launch of the project ends them with the sweep of 4.2 item 3 before it recovers; the intent log of 4.4.3 lets that launch finish or undo a half-applied publication, and it removes the leftover namespace directory | the next launch says what it recovered | recovered |
| Backend disappears between check and start | the start fails closed (the wrapped argv does not exist); the capability cache is dropped | the caller | nothing |
| The operating system is updated | the cache key changes; the next launch recomputes and proves again | nobody unless it now fails | |

## 12. What a person feels

The normal journey adds no step, prompt, flag or wait a person notices (AGENTS.md section 2, "Security without friction"; the
section "Zero friction").

- A Mac user starts a coding conversation: the capability check is a cache read; the stage is built in under the 5 s budget (W1);
  the proof adds a fraction of a second; the first line says "Working on a copy of PROJECT; changes arrive when the turn ends."
  Nothing is typed.
- The turn ends: "Applied N changes. Refused 1 file, named by a file that runs it: `Makefile` (no rule of this session
  names it; the patch is kept with this conversation)." A name the master does not hold appears as `<file 4, 41 bytes, digest
  ab12..>`. The same text goes to the launching session as an `isolation.report` message. If the person edited the same file
  meanwhile: "Kept your version of `src/x.py`; the agent's is saved with this conversation."
- A Linux user whose system restricts user namespaces has the shipped AppArmor profile installed by the package install (5.3);
  a launch never prompts for it. Everyone else meets nothing.
- A Windows user with WSL 2 runs route A with no new step. Without it, the sentence of section 9 and nothing else.
- The agent needs the network to install a package: a registry host named by `--allow-net` at launch is reachable from the
  start of the session; any other has no route and fails at once, counted for the sentinel, and the launching session is not
  told per request (it knows the task).
- The agent's change is applied with nothing asked; refused files are reported once per publication, counted by
  class, not once per tool call and not again for the same set.
- Refusals are one sentence an agent relays. No refusal asks anyone to open a terminal, type a confirmation or edit a file; it
  names the kind of widening that would change the decision and no command.

Added visible cost, with its reason: a project gets a working copy, so edits reach the checkout after each completed turn and not
as they happen (the person's editor does not show them mid-turn). That is the price of I4; the working copy is made by clone or
reflink where the filesystem has it, so it costs neither time nor, usually, space. A second cost of I4: a change to a data or
configuration file (a JSON fixture, a YAML file, a lockfile) is refused like a build file, because only the tool that reads such a
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
| `a_pty_given_as_stdio_is_not_a_person` (the relay slave and a duplicated descriptor as stdio; a pty the process made, its master held and answering `yes`; a dropped marker; `setsid`: the launcher's grant control and `caller.refuse_agent` refuse at once, also after `TIOCSTI` text or `yes` into the slave) | mac, linux, win | N9, A45, A47 |
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
| `a_file_git_does_not_hold_is_overwritten_only_after_the_launcher_keeps_its_exact_bytes_under_the_state_root`, `the_deletion_of_a_file_git_does_not_hold_is_restorable_from_the_report_alone`, `a_follow_up_edit_of_a_file_an_earlier_launch_created_and_left_untracked_is_applied_with_a_restore_copy`, `an_untracked_file_changed_in_the_checkout_since_launch_is_a_conflict_that_is_never_overwritten_or_deleted`, `an_untracked_file_the_session_never_saw_is_never_overwritten_and_the_bytes_of_an_ignored_file_are_copied_nowhere` and `the_restore_directory_is_in_no_grant_of_the_launchers_profile` (real git, real files, real publication; the mutants that drop the digest check, drop the copy or accept a file the session never saw turn them red) | mac, linux | N7, A37, F1 |
| `every_change_that_is_not_plain_source_is_refused_with_a_patch_whichever_tool_wrote_it` (shell redirect, `python -c`, an editor tool, a build script) | all | A11, N3, N4, N5 |
| `the_whole_change_is_applied_and_the_files_of_the_kind_that_runs_later_are_named_for_information`, `nothing_inside_git_is_ever_applied_under_any_spelling`, `a_coding_tools_settings_and_control_paths_and_credentials_are_refused_at_any_depth_and_the_rest_is_applied` | all | A11, N3, N4, N5 |
| `every_name_an_ecosystem_tool_runs_is_non_plain` (the oracle of 4.4.2, on the tools present) | mac, linux | A11, N3 |
| `a_file_named_by_a_refused_change_is_refused_and_named` (a script a Makefile or workflow names, planted a turn earlier) | all | A11 |
| `the_shipped_apparmor_profile_is_scoped_to_bwrap_and_changes_no_sysctl` (parse, plan, and the Ubuntu job with the restriction on) | linux | N6 |
| `a_proof_with_a_weakened_profile_stops_the_launch` (one mutant per rule) | all | A41, A43 |
| `project_content_cannot_change_the_probe` (`json.py`, `sitecustomize.py`, `.pth` and a canary-named file in the stage) | all | A42 |
| `no_flag_variable_or_argument_skips_the_proof` | all | A43, A44 |
| `a_launcher_run_by_any_agent_but_the_lead_or_inside_a_confinement_refuses_every_widening_argument_and_unregistered_projects_and_a_non_loopback_on` (a marked non-lead agent, a marked agent that runs the launcher outside the owner's sandbox through `excludedCommands`, and a confined caller each pass `--read`, `--toolchain`, `--allow-net`, in-place, `--project` and `--protect` together and get none; a dropped marker changes nothing) | all | A45, F17, AF3 |
| `in_the_prod_preset_the_grant_gate_reads_person_and_the_lead_is_refused_at_once_while_a_terminal_outside_any_agent_passes` | all | AF3, Zero friction |
| `a_widening_argument_outside_a_present_project_policy_is_refused_and_a_policy_in_the_repository_is_not_read` (a policy file, a board message, a patch and a refusal sentence each name a widening and none is taken as its source) | all | AF7 |
| `the_caps_hold_for_every_caller_and_every_source` (in-place with any net host; a host not on the registry list: each refused for the lead and for a terminal) and `every_widened_launch_sends_the_sentinel_one_signal_and_in_place_is_marked_unusual` | all | AF7, AF5 |
| `the_refusal_report_is_posted_under_the_launcher_identity_as_kind_isolation_report_and_no_session_verb_can_send_that_kind` and `every_message_a_confined_session_sends_is_labelled_as_from_a_confined_agent_data_by_the_bridge` (a session that announces text shaped like a report arrives labelled, not as a report) | mac, linux, win | AF1 |
| `a_report_holds_only_parent_written_words_name_tokens_the_master_holds_and_digests_whatever_the_file_names_and_patch_text_say` (a name of 4096 bytes, a name that addresses the reader, patch comments that instruct, a name that holds an argument string: none is copied and no argument string appears) | all | AF2 |
| `a_grant_frame_is_answered_with_one_fixed_refusal_for_every_kind_and_subject_logged_and_counted_and_never_reported_to_the_launching_session` (a request for a host on the list and one off it get identical bytes) | all | AF2, AF5 |
| `a_refusal_in_a_chat_or_task_launch_reaches_the_launching_session_of_the_task_the_conversation_record_and_the_proposal` | all | AF2 (recipient) |
| `refusal_reports_fold_by_file_set_and_class_and_stop_at_the_cap_with_one_sentinel_signal` (one changed byte per turn makes no second report) | all | AF10 |
| `a_relaunch_of_the_same_conversation_reuses_the_kept_stage_and_a_file_refused_before_is_not_reported_again` | mac, linux | AF11 |
| `a_symlink_is_applied_only_when_its_target_in_the_result_tree_is_plain_source_and_neither_path_has_a_dot_component` (a link to `.git/hooks/pre-commit`, to a workflow file, to a Makefile, to `.env`) | mac, linux | AF8 |
| `the_reserved_control_names_are_exactly_the_five_and_a_refusal_adds_no_name_to_the_stage_or_the_checkout` (the A6 case and Unicode cases over the five names) | mac, linux, win | AF6 |
| `the_refused_patch_directory_is_read_only_to_the_session_in_the_stage_and_in_place_modes_and_across_epoch_rotation` (the session cannot write, rename or replace it or an ancestor; on Linux the bind survives `begin()`; no other entry of the state root is readable) | mac, linux | AF6 |
| `a_connect_tunnel_is_counted_per_direction_and_cut_when_bytes_out_pass_the_registry_bound_while_a_download_is_not` (a stage-sized upload to an allowed host is cut) | all | AF5 |
| `net_hosts_come_only_from_the_registry_list_and_every_entry_names_its_source_and_its_measured_bound` | all | AF5 |
| `a_launch_on_a_host_without_the_apparmor_profile_or_bubblewrap_refuses_at_once_with_a_sentence_that_names_the_fact_and_no_command_to_run_and_never_prompts_or_installs` | linux | AF9 |
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
| A10 | set-uid, mode, xattr, ACL, quarantine bits that change what the person's tools do | modes copied from a whitelist (`0644`, `0755`), xattrs not carried, set-id files left out and listed | `test_isolation_workspace` |
| A11 | plant a hook, config, wrapper, lockfile, instruction file or review-governance file that runs or decides later | what git or a coding tool runs unread (`.git`, a tool's settings) is refused (4.4); every other file is applied, is in the person's diff and is named as one that runs later (4.4.1). The person reading the diff is the check (R1) | `nothing_inside_git_is_ever_applied_under_any_spelling`, `the_whole_change_is_applied_and_the_files_of_the_kind_that_runs_later_are_named_for_information` |
| A12 | spread an exploit over ordinary source files the person later runs | not detectable by name or structure; the person sees the diff when the file is wired; accepted risks R1 and R8 | none |
| A13 | persist injected instructions in the agent home across turns | the home is per conversation and dies with it; accepted risk R2 | none |
| A14 | a surviving process changes the checkout or the stage after publication, including one that left the process group with `setsid()` | the checkout is not reachable from the confinement; the barrier walks the tree, never the group (4.2 item 3); the stage is removed; a survivor loses the bridges and publication is skipped | `a_setsid_descendant_is_ended_before_the_final_capture`, `a_process_that_outlives_the_session_loses_the_bridges_and_the_stage` |
| A15 | read the launcher's or another process's arguments and environment; list processes | D1 on macOS (VERIFIED open before, closed after) for arguments and environment; the process list is exposure X1 on macOS and absent on Linux (pid namespace) | `the_launchers_arguments_and_environment_are_unreadable`, `signal_ptrace_and_the_reads_of_other_processes_reach_nothing_outside_and_the_list_is_exactly_the_exposure` |
| A16 | `ptrace`, `task_for_pid`, `process_vm_readv`, a debugger | refused (VERIFIED macOS; Landlock domain; pid namespace) | the same |
| A17 | signal the parent or `kill -9 -1` | `signal` limited to children (VERIFIED); pid namespace | the same |
| A18 | inject keystrokes (`TIOCSTI`), open `/dev/tty`, send Apple events, `open`, clipboard, UI automation | refused (VERIFIED); the interactive harness gets a private pty, and nothing read from it is an answer (3.5) | `tiocsti_dev_tty_and_apple_events_are_refused`, `interactive_relay_isolates_the_terminal` |
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
| A33 | forge, replay or flood hook events; flood the launching session with refusal reports; inject text into a refusal message | the bridge fixes the rail; nothing is asked and nothing is queued; one `isolation.report` per publication that refused a set not already reported, folded by file set and class and capped per launch (4.4); the report holds only words the parent wrote, name tokens the master holds and digests, so it carries no instruction of the attacker's; a `grant` frame is answered by one fixed refusal and counted, never reported; a burst reaches the sentinel | `a_forged_replayed_or_flooded_hook_event_changes_nothing` |
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
| A45 | use the launcher as a deputy from a sandboxed agent: `--project`, `--protect`, grant flags, `--on` | 8.3: the checks sit at the choke point; they refuse a confined process, and every marked agent that is not the lead, outside a confinement too (an agent in the owner's sandbox that runs the launcher through `excludedCommands`), resolved by the registry's pid walk and not by a marker | `a_launcher_run_by_any_agent_but_the_lead_or_inside_a_confinement_refuses_every_widening_argument_and_unregistered_projects_and_a_non_loopback_on` |
| A46 | read the machine's serial number, hardware UUID, provisioning UDID or machine id (N2) | G13; the proof reads the identifiers in the parent and searches for them (P22) | `no_hardware_identifier_is_readable` |
| A47 | pass for the launching session by typing into the harness's own pty, or satisfy the caller check with a duplicated pty descriptor or a dropped marker (N9) | a confined process is never the launching session or a person (3.5, I17): every launcher control refuses it at once; nothing read from a terminal the confinement holds is an answer | `a_pty_given_as_stdio_is_not_a_person` |
| A48 | have the installer turn off the host's user-namespace restriction, or attach the AppArmor permission to more than one binary (N6) | one literal path, `userns` only, no sysctl, an installer that setup alone runs and that refuses a confined caller (5.3) | `the_shipped_apparmor_profile_is_scoped_to_bwrap_and_changes_no_sysctl` |
| A49 | swap a toolchain declaration's target between the check and the grant (8.2) | resolved once; the checked descriptor is the granted object; identity re-read after the proof | `a_toolchain_declaration_is_resolved_once_and_granted_by_the_same_object` |
| A50 | learn what the platform cannot hide: the process list, the machine class and host name, the `/etc` account files (5.7) | named exposures X1 to X3, pinned by P23, accepted risk R7 | `the_exposure_set_is_exactly_the_declared_set` |
| A51 | forge the refusal report: post a message in the shape of the launcher's report, with a file name or a command the lead would run (AF1, AF2) | the report is a message kind the session's verbs cannot send, posted under the launcher's identity; every session message is labelled data by the bridge; the report holds parent-written words, name tokens the master holds and digests, and no argument string (4.4, 7) | `the_refusal_report_is_posted_under_the_launcher_identity_as_kind_isolation_report_and_no_session_verb_can_send_that_kind`, `a_report_holds_only_parent_written_words_name_tokens_the_master_holds_and_digests_whatever_the_file_names_and_patch_text_say` |
| A52 | a widening argument or a project file makes publication apply a refused path | no grant, rule or argument lifts a refusal (4.4) | `a_decision_is_granted_or_refused_and_nothing_else` |
| A53 | send bytes out through an allowed registry host (a CONNECT tunnel sends as well as receives); read `.env` with in-place and send it; learn the host list by probing grants (AF5) | hosts only from the sourced registry list; bytes counted per direction and the tunnel cut at the measured bound; in-place never with a host; the `grant` frame answers every request alike | `a_connect_tunnel_is_counted_per_direction_and_cut_when_bytes_out_pass_the_registry_bound_while_a_download_is_not`, `the_caps_hold_for_every_caller_and_every_source`, `a_grant_frame_is_answered_with_one_fixed_refusal_for_every_kind_and_subject_logged_and_counted_and_never_reported_to_the_launching_session` |
| A54 | a symlink to `.git`, a tool's settings, a credential or a place outside the project, directly or through other links, which the person's tools later open (AF8) | a link is followed as the filesystem follows it and refused when it leads there, with every link it goes through (4.4) | `a_link_that_stays_in_the_project_is_applied_and_one_that_leads_out_or_to_a_refused_path_is_not` |
| A55 | talk the lead into a wider launch by a board message, a patch, a refusal sentence or a project file; use the launcher from an agent that is not the lead (AF3, AF7) | the source rule, the caps and the sentinel signal of 8.3; the gate is `delegated` to the lead only; the project policy lives outside the repository | `a_widening_argument_outside_a_present_project_policy_is_refused_and_a_policy_in_the_repository_is_not_read`, `the_caps_hold_for_every_caller_and_every_source`, `every_widened_launch_sends_the_sentinel_one_signal_and_in_place_is_marked_unusual` |

Accepted risks, named for the owner. **R1** A change that the person, or a tool of the person's, then runs is not judged: a build file, a script, a CI workflow or an instruction file is applied and named (4.4.1). **R2** A
conversation's agent home persists instructions between its own turns. **R3** On macOS a process that escapes the follower can
outlive the session, confined and without reach; the barrier reports it and publication is skipped. **R4** Bytes can leave through a granted host: a CONNECT tunnel sends as well as receives, so the body of a request (up to the
bound of 6.3), its path, sub-domain, headers and timing carry bytes, and a TLS name that differs from the CONNECT name on a
shared CDN host is invisible to the proxy. **R5** The profile language of Seatbelt is not a public interface and the tool is deprecated; the
proof is the only notice of a change. **R6** Windows route B has no verified network rule and refuses. **R7** The exposures of 5.7
stay visible: on macOS the name, pid, parent pid and user id of other processes and the machine class and host name, on Linux the
`/etc` account and host files. **R8** A file that runs only because another file names it is not listed as one that runs later (4.4.1).
**R9** (a rule with a residual) A widening argument comes only from the task the person gave the launching session or from a
person-owned project policy outside the repository, and never from a board message, a patch, a project file, a refusal sentence
or a tool result (8.3). It is reachable by a request nowhere; it is reachable by a message only through the lead's own
judgment, and the launcher cannot see where an argument came from. What holds without a person: the gate is `delegated` to
the lead identity alone and every other agent is refused every widening argument; the caps hold for every caller (never in-place with a network host, hosts only from the registry list); the project
policy, when present, bounds what the lead may pass; each grant records its issuer and source; every widened launch is a
sentinel signal, the one reader that exists; no rule names a credential location, the state root, a control name, a governing
file or a special file, and nothing a rule adds carries a credential or an environment variable. What remains: an injected lead
that invents a task inside the caps, which the signal makes visible and the caps make bounded; and a copy or a clone of a project
that has a policy, which the lead's launch registers as a project of its own with no policy (8.3), so the person's policy of the
original does not bound a widening argument for the copy, only the caps do, and the `isolation.project_registered` signal makes it visible.

### 14.2 The cases of AGENTS.md section 1

| Case | Attack on this design | Answer |
|---|---|---|
| copied or cloned pool or device | a cloned state root or a copied capability cache claims "enforced" | the cache is keyed by the boot, the operating-system build and the backend binary identity and recomputed; no verdict is read from state or a board; the proof runs on this device at this launch |
| replay | replay a hook frame, a bridge token, a spent grant | the bridge token is per session and dies with it; a hook frame carries nothing the rail does not already fix; a grant is a record with a fingerprint, is decided once at launch and is single-session, so a replayed request grants nothing a second time |
| forged or below-threshold signature | none is used; a grant carries no signature and no token | a grant is a launch argument of the lead or of a terminal, audited with who passed it; the gate `isolation.grant` is decided at once and refuses a confined caller and every agent that is not the lead (8.3) |
| compromised or malicious member | a pool member offers a model whose server abuses the bridge | the bridge treats the upstream as untrusted data; routes and fields are filtered in the other direction too (responses are passed as bytes, never parsed for instructions) |
| stolen phone or key | approve a grant from a stolen phone | there is no approval path to steal: no grant is approved by a phone, a key or a token; a grant is a launch argument, and it widens one session only |
| malicious agent | every row of 14.1 | 14.1 |
| agent compromised without knowing it | an injected instruction to read a key, call the network, plant a hook | the OS refuses the first two; the third is refused at publication at once and reported, by structure and not by a name; a burst of refusals reaches the sentinel |
| downgrade, secure to open | a profile that falls back to a weaker one when a mechanism is missing | there is no weaker profile: every missing guarantee refuses; `watched` is named in the report, and an exposure is named and pinned, never silent; in-place is a launch grant, refused to a confined caller, to every agent that is not the lead and to any launch with a network host |
| approval fatigue | many grants or refused changes | nothing is asked, so there is nothing to fatigue; refused changes batch into one report per publication, folded by file set and class, capped per launch, and past the cap one sentinel signal replaces them (4.4); refused `grant` frames are never reported |
| takeover by whoever is merely present | another local process connects to a bridge or squats a socket | random 0700 directory, the connecting process's credentials, bridge token, the client checks the server; a same-user process is the launching session only when the registry's pid walk resolves it as the lead or as a terminal outside every agent, and a process inside a confinement, or a marked agent that is not the lead and runs unconfined from an outer sandbox, never is (I17, 8.3) |

### 14.3 The non-author pass: where each finding is decided

Every finding of `docs/design-isolated-launcher-breakit.md` is decided in the section named and carried by the slice named, with
the test that is run on the parent commit and seen to fail first (the full list of 15.1 repeats the slice side). A finding
that was decided by a step of a person is decided by an automatic rule or an immediate refusal with its property unchanged
(Zero friction): N3, N4, N5 and A11 by the refusal of 4.4, N9, A45 and A47 by 8.3 and I17, A33 by section 7; the red tests assert
the property and never a step by a person. The engine
fixes of F1, F2, F12, F13 and D5 are in `fix/sandbox-holes-and-harness-denial`, and L1 re-runs their tests against the result.

| Finding | Decided in | Slice | Fail-first test |
|---|---|---|---|
| N1 process list readable (G6) | 5.1 G6 narrowed to acts; exposure X1 (5.7); F12; P13, P23 | L1, L2 | `signal_ptrace_and_the_reads_of_other_processes_reach_nothing_outside_and_the_list_is_exactly_the_exposure` |
| N2 hardware identifiers readable | G13 (5.1, 5.5); F13; P22; the sentence of 9; the engine note after the deltas of 5.2 | L1, L2 | `no_hardware_identifier_is_readable` |
| N3 name table misses real auto-run files | 4.4.1 and 4.4.2, deny by default; F14; the tables deleted (W12) | L4 | `the_corpus_of_auto_run_names_is_refused_and_ordinary_source_is_applied` |
| N4 other tools' instruction files | 4.4.1 test 2 (dot names), harness records (4.4.2) | L4 | the same, and `every_change_that_is_not_plain_source_is_refused_with_a_patch_whichever_tool_wrote_it` |
| N5 `CODEOWNERS` | 4.4.1 tests 2 and 3; 4.4.2 | L4 | the same |
| N6 AppArmor scoping | 5.3 "The AppArmor profile", I10, T21, the sentence of 9 | L7 | `the_shipped_apparmor_profile_is_scoped_to_bwrap_and_changes_no_sysctl` |
| N7 live read not pinned to the write | 4.4.3, W13, T20 | L4 | `publication_never_overwrites_a_concurrent_edit` |
| N8 process count watched, could be enforced | D5, G11 (5.1, 5.2, 5.5), I14 | L1 | `a_fork_flood_is_refused_by_the_kernel_and_a_disk_fill_and_memory_growth_end_the_session` |
| N9 caller check satisfied by a pty | 3.5 rules 1 and 2, 8.3, I17, F16, P24 | L1, L2, L6 | `a_pty_given_as_stdio_is_not_a_person` |
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
| A45 launcher as deputy | 8.3, F17 | L2, L5 | `a_launcher_run_by_any_agent_but_the_lead_or_inside_a_confinement_refuses_every_widening_argument_and_unregistered_projects_and_a_non_loopback_on` |
| Toolchain grant widened by a hostile declaration | 8.2 (resolved once, granted as checked) | L2 | `a_toolchain_declaration_is_resolved_once_and_granted_by_the_same_object` |
| Memory ceiling | 5.2 (neither `RLIMIT_AS` nor the Mach footprint limit holds; watched) | L1 | `a_fork_flood_is_refused_by_the_kernel_and_a_disk_fill_and_memory_growth_end_the_session` |

The second pass, `docs/notes/attack-verdict-launcher-no-waiting.md`, read the design with no person step in it. Its findings are
cited as AF1 to AF11 (its F1 to F11). The lead agent replaced the person in the design, and the lead is a model that reads text
the confined agent wrote, so each finding is a way to put text in front of the lead or to reach a widening without the lead.
Each is decided in the section named, with the red test that asserts the property, never a step by a person.

| Finding | Decided in | Slice | Fail-first test |
|---|---|---|---|
| AF1 the report is indistinguishable from the agent's own message | 4.4 step 4, 7 (`workspace`), A51 | L3, L4 | `the_refusal_report_is_posted_under_the_launcher_identity_as_kind_isolation_report_and_no_session_verb_can_send_that_kind`, `every_message_a_confined_session_sends_is_labelled_as_from_a_confined_agent_data_by_the_bridge` |
| AF2 file names, grant subjects and a ready command reach the lead as instructions | 4.4 steps 2 and 4, 7 (`grant`), 8.2 refusal form, A33, A51 | L3, L4 | `a_report_holds_only_parent_written_words_name_tokens_the_master_holds_and_digests_whatever_the_file_names_and_patch_text_say`, `a_grant_frame_is_answered_with_one_fixed_refusal_for_every_kind_and_subject_logged_and_counted_and_never_reported_to_the_launching_session`, `a_refusal_in_a_chat_or_task_launch_reaches_the_launching_session_of_the_task_the_conversation_record_and_the_proposal` |
| AF3 a marked agent outside any confinement passes every grant argument | 8.3 gate, 5.2 `excludedCommands`, A45, Zero friction (`prod`) | L2, L5 | `a_launcher_run_by_any_agent_but_the_lead_or_inside_a_confinement_refuses_every_widening_argument_and_unregistered_projects_and_a_non_loopback_on`, `in_the_prod_preset_the_grant_gate_reads_person_and_the_lead_is_refused_at_once_while_a_terminal_outside_any_agent_passes` |
| AF4 `publish` takes a directory with no bound | the `publish` rule is removed (4.4) | L4 | `a_decision_is_granted_or_refused_and_nothing_else` |
| AF5 "download only" is untrue for a CONNECT tunnel; the `grant` frame for hosts is a no-op and an oracle | 6.3, 6.4, 7, 8.2 `net`, R4, A53 | L3 | `a_connect_tunnel_is_counted_per_direction_and_cut_when_bytes_out_pass_the_registry_bound_while_a_download_is_not`, `net_hosts_come_only_from_the_registry_list_and_every_entry_names_its_source_and_its_measured_bound`, `the_caps_hold_for_every_caller_and_every_source` |
| AF6 `.poolhouse-refused/` as a sixth reserved name | 4.2 item 2, 4.5, G1, 13.2 | L1, L4 | `the_reserved_control_names_are_exactly_the_five_and_a_refusal_adds_no_name_to_the_stage_or_the_checkout`, `the_refused_patch_directory_is_read_only_to_the_session_in_the_stage_and_in_place_modes_and_across_epoch_rotation` |
| AF7 R9 does not keep the lead from being talked into a wider launch | 8.3 (the rule, the caps), 8.2 logging, R9, A55 | L2, L5 | `a_widening_argument_outside_a_present_project_policy_is_refused_and_a_policy_in_the_repository_is_not_read`, `the_caps_hold_for_every_caller_and_every_source`, `every_widened_launch_sends_the_sentinel_one_signal_and_in_place_is_marked_unusual` |
| AF8 a symlink to a refused target inside the tree is applied | 4.4 table (symlink), A54 | L4 | `a_link_that_stays_in_the_project_is_applied_and_one_that_leads_out_or_to_a_refused_path_is_not` |
| AF9 the AppArmor install and a missing bubblewrap still need root, and the refusal printed a command | Zero friction (the exception), 5.3, 9, L7, I19 | L7 | `a_launch_on_a_host_without_the_apparmor_profile_or_bubblewrap_refuses_at_once_with_a_sentence_that_names_the_fact_and_no_command_to_run_and_never_prompts_or_installs`, `the_shipped_apparmor_profile_is_scoped_to_bwrap_and_changes_no_sysctl` |
| AF10 one report per turn is attacker-paced | 4.4 step 3, 14.2 "approval fatigue", 17 | L4 | `refusal_reports_fold_by_file_set_and_class_and_stop_at_the_cap_with_one_sentinel_signal` |
| AF11 a refusal gives only "re-launch wider", so friction steers to in-place | 4.4 step 6, 3.3 step 10, 9 (stage sentence) | L4 | `a_relaunch_of_the_same_conversation_reuses_the_kept_stage_and_a_file_refused_before_is_not_reported_again` |

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
| **L0** the hook rig (independent of isolation) | done: the suite clears the variables that name a session or a board (`board.session.IDENTITY_ENV`), so the 10 hook tests pass in an agent's shell; the hook still denies a session with no token file; the browser rig sets the daemon root | the 10 tests, in a shell that exports `POOLHOUSE_BOARD` | none |
| **L1** the engine | **Landed on development.** Done: macOS D2 (`Policy.signal_children`); F13 closed by two operations (5.2), so G13 is enforced on macOS and no refusal of it is needed; the exposure constants X1 to X3; `sandbox.confined()` on macOS (VERIFIED), Linux (the marker, PREDICTED) and Windows (PREDICTED); `process_tree.end`; bubblewrap `deny_read` and `deny_write` by read-only binds for a path directly under a writable tree (a deeper path is refused, since the directories above it could be renamed: PREDICTED, no Linux device ran it), and the confinement marker bound read-only; `Windows`, a backend that reports unavailable with the reason of 5.4. Already in the tree before L1: D1, D5 and the net namespace with its relay (the engine branch), `--unshare-user --disable-userns --cap-drop ALL`, `Net.loopback` refused on Linux (`local_net` gives a Linux policy no network), `Pipes` refused outside macOS. Left: the memory and disk watcher (L2, I14: neither `RLIMIT_AS` nor the Mach limit holds, so memory stays watched). Planned text: starting from the result of `fix/sandbox-holes-and-harness-denial` (macOS D1 and D5, the net namespace instead of `--share-net`, `--unshare-user --disable-userns --cap-drop ALL`, which are not part of this build): macOS D2; the closure of the identifier channel F13 (a bisect of the profile's `file-read*` grants until `system_profiler` and `ioreg` print no identifier, if the branch has not closed it) or, when no operation closes it, the macOS refusal of G13; the declared exposure constants of 5.7 beside the grants they describe; `sandbox.confined()` (macOS `sandbox_check` of the process itself, Linux the read-only marker file bound into the namespace, Windows the token and job); `sandbox.process_tree` gains the tree end (stop every member, list again, kill, wait; pid and start time identity) the barrier calls; bubblewrap `deny_read` and `deny_write` by read-only binds, `Net.only` with a relay and forwarder for the bridge, `Net.loopback` refused on Linux (the `mcp_server` policy names its ports); `Pipes` outside darwin refuse clearly; a Windows backend that reports unavailable with the reason of 5.4. Closes the Linux half of F3. | the read-only-bind and `Net.only` relay tests; `no_hardware_identifier_is_readable` (macOS); `a_fork_flood_is_refused_by_the_kernel_and_a_disk_fill_and_memory_growth_end_the_session` (the process-count half); a `setsid` descendant is ended by the tree end and not by a group kill; the marker, host-port and nested-namespace tests of F1 and F2 are the other branch's and are only re-run here | N1 (the exposure declaration), N2, N8, N9 (the predicate), A14, A15, A21, A22 and the memory ceiling |
| **L2** the library | **Changed by the owner order of 2026-11-26: the classifier of plain source, the wiring test and the `publish` rule named in this row are removed; 4.4 is the rule.** **Landed on development, hardened after the attack pass** (`docs/notes/attack-verdict-isolation-l2-l3.md`: H1 a venv's `home =` names a toolchain only from the closed list of interpreter folders, a toolchain is granted as its `bin` and `lib/pythonX.Y` with the credential walk and a sentinel signal; H2 and H3 the gate reads the process tree from the node and the policy belongs to the registered project; M1 `confined()` and the attestation give one answer, `seatbelt_state`: only a deny-default profile is confined, so a deny-list profile on a harness is attested as not confined; L1, L5, M6 as in 6.3 and 10; `tests/test_redteam_isolation_hardening.py`, `tests/test_redteam_isolation_gate_hardening.py`, `tests/test_redteam_isolation_bridges_hardening.py`) (`src/poolhouse/isolation/`; the proof runs the 24 attempts in the real Seatbelt in 2.8 s, and 19 weakened-profile mutants each stop the launch at the attempt named in `tests/test_redteam_isolation_proof.py`). What differs from the plan below: P24 in the launch checks the predicate the callers read (`sandbox.confined()` under a pty, a dropped marker and a `yes` typed at it), because the probe cannot import the library, and `a_pty_given_as_stdio_is_not_a_person` asserts the `AgentRefused` of `caller.refuse_agent` against a real confined child holding the parent's pty; a confined process cannot open a pty of its own on this profile (`/dev/ptmx` is refused), so the test hands it one; the lead is read from the process tree by the node (8.3; the first version authenticated the session by its name and token file, which an environment chose); the toolchain declaration is held by its `O_NOFOLLOW` descriptor and reopened by its path after the proof (`F_GETPATH` is not used: the file-locks gate counts `fcntl`); `proof.run(lan=False)` leaves out only the listener on the machine's LAN address, which the test network guard forbids, and P08 then tries a documentation-range address; the interactive sentence carries no example command; `contracts/egress-hosts.json` holds five hosts measured with real pip, npm and cargo installs through a counting proxy (`scripts/measure_egress_hosts.py`), each with a bound for one tunnel and a budget for a session (6.3), measured on 2026-11-25 with a small and a larger real install; the limits other than the process count (CPU, file size, descriptors) are set by the launcher at launch (L5) and `build` takes them; `(deny file-link)` and `(deny file-clone)` of the engine survive every mutant (no attempt can show they matter under deny-default). Planned text: `isolation` package: `capability` (guarantees and exposures), `profile`, `grants` (the model, not the bridge; the single resolution of 8.2), `proof` and the probe (P01 to P24, isolated interpreter, empty working directory), the closed environment table, the attestation, the refusal sentences, the launcher's choke-point checks of 8.3 and `grants.decide`; `caller.refuse_agent` reads `sandbox.confined()`; the package in the tools layer. No call site changes. The slice builds no wait, prompt, confirmation or pasted token (Zero friction). | the sentence tests with an unavailable backend; the weakened-profile mutants; `a_pty_given_as_stdio_is_not_a_person` (the caller check, with a real pty: a confined caller is refused at once); `project_content_cannot_change_the_probe`; `the_environment_is_exactly_the_allow_list`; `a_toolchain_declaration_is_resolved_once_and_granted_by_the_same_object`; `a_launcher_run_by_any_agent_but_the_lead_or_inside_a_confinement_refuses_every_widening_argument_and_unregistered_projects_and_a_non_loopback_on`; `in_the_prod_preset_the_grant_gate_reads_person_and_the_lead_is_refused_at_once_while_a_terminal_outside_any_agent_passes`; `a_widening_argument_outside_a_present_project_policy_is_refused_and_a_policy_in_the_repository_is_not_read`; `the_caps_hold_for_every_caller_and_every_source`; `every_widened_launch_sends_the_sentinel_one_signal_and_in_place_is_marked_unusual`; `publish_takes_exact_files_and_refuses_the_root_a_directory_a_glob_a_dot_path_and_every_governing_file_for_every_caller`; `a_publish_rule_is_bound_to_the_files_device_and_inode_and_not_to_a_string` | N1 (P13, P23), N2 (P22), N9 (P24 and the person check), A30, A41 to A43, A45, the toolchain grant, AF3, AF4 (the rule), AF7 |
| **L3** the bridges | **Landed on development** (`isolation.model_bridge`, `egress_proxy`, `session_bridge`, `session_verbs`, `reports`, `client`, with `wire` the one strict HTTP reader and `serving` the one listener; `workspace.tools.as_session` runs a tool as the one session a bridge holds; tests `test_redteam_isolation_bridge.py` and `test_redteam_isolation_session.py` against real sockets, a real node and the real Seatbelt). What differs from the plan below: a frame is one line of JSON, because the proof's P21 and the L2 kit send and read lines; the board's message types are a closed list in the node, so the report's kind is the subject of a `status` message from the launcher's own session until the node learns the type (a node change, not made here); the bound of a host counts across all of a session's tunnels to it; the model bridge refuses any `Transfer-Encoding`; the constructors take `Lease`, `Terms` and `Wiring` objects. Planned text: model bridge, session bridge (the `workspace` verbs that cannot send `isolation.report`, the sender label, the one fixed `grant` answer, counted and logged), egress proxy with the registry host list `contracts/egress-hosts.json` (each entry with its source and its measured bytes-out bound) and per-direction byte counts that cut a tunnel, and the client module, each standalone | the route, smuggling, SSRF and forged-frame tests; `the_refusal_report_is_posted_under_the_launcher_identity_as_kind_isolation_report_and_no_session_verb_can_send_that_kind`; `every_message_a_confined_session_sends_is_labelled_as_from_a_confined_agent_data_by_the_bridge`; `a_grant_frame_is_answered_with_one_fixed_refusal_for_every_kind_and_subject_logged_and_counted_and_never_reported_to_the_launching_session`; `a_connect_tunnel_is_counted_per_direction_and_cut_when_bytes_out_pass_the_registry_bound_while_a_download_is_not`; `net_hosts_come_only_from_the_registry_list_and_every_entry_names_its_source_and_its_measured_bound` | A27, A28, A29, A33, A34, AF1, AF2 (the frame), AF5 |
| **L4** the workspace | **Changed by the owner order of 2026-11-26: the classifier of plain source, the wiring test and the `publish` rule named in this row are removed; 4.4 is the rule.** **Landed on development, except what the sentence before "Planned text" names.** Done: `guard.runs_later` (`guard/runs_later.py`, the registry snapshot `contracts/language-kinds.json` from `scripts/refresh_language_kinds.py` with its nightly `--check` job in `ci.yml`, the harness records `guard/harnesses.py`, the ecosystem records `guard/ecosystems.py`), the corpus (`tests/test_publication_rule.py`) and the oracle (`tests/test_publication_oracle.py`), the name tables of `guard.autoexec` deleted and the publish rule of L2 reading the classifier; W1 (the launch budget, `tests/test_coding_workspace_launch.py`, measured in CPU seconds in a fresh interpreter: 3.1 s to build, 3.7 s to stage and 2.3 s to finish on a 47 MiB project of 4231 files, from 4.2, 5.1 and 6.9 s), W2 (Apple's git), W3 (`SourceSnapshot(strict=False)`: a link inside the tree carried, a group-writable file copied closed, a submodule, a link out, a special file and a hard-linked file left out and named), W4 (progress and the volume's free bytes, a sparse file stays sparse), W10, W13 and 4.4.3 (`coding_apply`: the swap, the claim, the intent log and `recover`, `tests/test_coding_apply.py`). Done in the second half (`isolation/publication.py`, `notices.py`, `workspace.py`, `Workspace.observe` and `Workspace.adopt`, `Policy.binds`, `tests/test_isolation_publication.py`, `tests/test_isolation_workspace.py`): the publication decision of 4.4 (a change set classified against the result tree by `runs_later.tree`, from the stage's own bytes and mode, the files a deleted path had and the files inside the environment folders and `.git/hooks` that run by their path alone, so a `.pth` there names a published file in view; plain source applied, everything else refused at once; the symlink rule; `isolation.publish.matches` for a rule, which never lifts a governing class, a control name, a credential, a special file, a link or a collision; the overwrite or deletion of a file git does not hold applied after the launcher keeps the version it displaces, 4.4.3), the patch under `refused/<session>/` written with `openat`, the report in the parent's words (a name only when it matches `[A-Za-z0-9._/-]{1,80}` and the master held it, the first file of each cause only, a label that says every name is data), folded by file and cause (a file and cause reported once is not reported again, even in a later turn or a re-launch of the same conversation) and capped per launch with one sentinel signal, the barrier of 4.2 item 3 (`Session.finish` calls `process_tree.end` and skips publication on a survivor or an incomplete tree), the session wrapper (`begin`, `publish_turn`, `finish`, the base that advances per turn, the first line from `Workspace.left_out()`), W6 in the new per-launch API (`Workspace.observe`: a control path or a credential is left out, reported and told to the sentinel, and the rest is published; `Workspace.finish` is unchanged), the kept stage of a re-launch (`Workspace.adopt`), the environment links (`.venv`, `venv`, `node_modules`, each only when the person's own directory is inside the project) and the Linux bind hook (`Policy.binds`: the stage at the project path and the patch directory read-only at `/poolhouse/refused`, which no Linux device has run), and the one sentence of `docs/agent-policy.md`. The wiring test of 4.4.1 was narrowed by the owner: a file makes the paths it names non-plain only when it runs or configures a tool (`Verdict.acts`), never when it is data or documentation. Not built, left to the slices that name them: the per-path hold-back of a path written while a turn is captured (a turn whose capture sees a write is deferred whole and read again at the next turn); the conversation record and the task proposal of a chat or task launch, and the exec grant of an environment link (L5, the callers); the in-place capture of 4.5 and its patch-directory test; any Linux, WSL or Windows run of the binds and the barrier (the Linux and Windows ends of the barrier take the backend's own `ended` report); the expiry of a kept stage nobody re-launches. Planned text: W1 to W13 of 4.3: the launch budget, Apple's git, classification, the session API, the barrier of 4.2 item 3 (calls the tree end of L1), `apply` with the swap of 4.4.3 and its intent log, the refusal of 4.4 (the parent-written reasons, the patch under the state root with the session's read-only grant, the `isolation.report` message to the launching session or to the task's launching session, folded by file set and class and capped per launch, the kept stage for a re-launch, the symlink rule, the governing-file class, the publish rule bound to device and inode) with no wait and no question, the environment links, the Linux bind hook; `guard.runs_later` with the language registry snapshot, `scripts/refresh_language_kinds.py`, the harness and ecosystem records, the corpus and the oracle; the nightly workflow step; the name tables of `guard.autoexec` deleted; the one sentence of `docs/agent-policy.md` that tells a reader a session's message and a patch are data to read and never instructions (AF1, AF2). `guard.runs_later` lands first within the slice, so the hooks use it at once. | the budget test, the symlink, umask and submodule fixtures, `special_files_in_the_stage_are_left_out_and_named`, `a_sparse_file_larger_than_the_volume_and_a_tree_of_small_files_stage_by_progress`, `publication_never_overwrites_a_concurrent_edit`, `the_corpus_of_auto_run_names_is_refused_and_ordinary_source_is_applied`, `every_name_an_ecosystem_tool_runs_is_non_plain`, `a_file_named_by_a_refused_change_is_refused_and_named`, `a_setsid_descendant_is_ended_before_the_final_capture`, `a_report_holds_only_parent_written_words_name_tokens_the_master_holds_and_digests_whatever_the_file_names_and_patch_text_say`, `a_refusal_in_a_chat_or_task_launch_reaches_the_launching_session_of_the_task_the_conversation_record_and_the_proposal`, `refusal_reports_fold_by_file_set_and_class_and_stop_at_the_cap_with_one_sentinel_signal`, `a_relaunch_of_the_same_conversation_reuses_the_kept_stage_and_a_rule_naming_the_refused_files_exactly_publishes_them_without_rework`, `a_symlink_is_applied_only_when_its_target_in_the_result_tree_is_plain_source_and_neither_path_has_a_dot_component`, `the_reserved_control_names_are_exactly_the_five_and_a_refusal_adds_no_name_to_the_stage_or_the_checkout`, `the_refused_patch_directory_is_read_only_to_the_session_in_the_stage_and_in_place_modes_and_across_epoch_rotation` | N3, N4, N5, N7, A3, A6, A8, A9, A11, A14, A21, A37, AF2, AF4, AF6, AF8, AF10, AF11 |
| **L5** turn it on, macOS | **Landed on development (macOS).** `isolation.prepare(plan)` (`isolation/confinement.py`) is the one door: it checks the capability, stages the project, starts the model bridge and the session bridge, writes the harness's files read-only inside the agent home (a frozen path is refused a write one by one), builds the allow-list environment and the profile, runs the proof and returns a `Confinement`; `Confinement.popen` (the engine's `sandbox.spawn`, followed from the first instruction through the hold, then attested) is the only way a harness starts, and `close()` ends the tree, publishes the last turn and removes what the launch made. `claude`, `codex`, `pi`, `Manager._process` and `TaskManager` use it; `harnessing.require_isolated_execution` and its call sites are deleted (an early `isolation.require_capable` stands where a refusal before a lease helps: `Manager.start`, the local start routes, `lease_model`, the catalogue); the 17 launcher and route tests, the 3 browser tests and the refusal-asserting tests are rewritten to the two behaviours (an unavailable backend injected at `sandbox.run.backend`, and an admitted launch); `the_launcher_is_the_only_path_that_starts_a_harness` is an AST test with its mutation check; `tests/test_wiring.py` has no `isolation` entry in `STANDALONE`; `refusal-stubs` is 0. Found by running the real harnesses under the profile: Codex reads its own preference domain through the system's preference service, so `Policy.preferences` (the domain, the service's shared memory, two lookups and `/etc/passwd`) is set for `codex` only; Codex's provider and Claude Code's base URL are the bridge's, never the server's (a connection to the server's port is refused: `network-outbound` in the system log); the harness is run by its real path; a folder that is not a git project is refused with its own sentence (`not_a_repository`). A launch is print mode (an interactive one refuses until L6). The task manager's tool-call limit is counted in the parent from the harness's event stream (the `task_caps` hook cannot run in the box; its module stays, unwired). The memory and disk watcher and the CPU, file-size and descriptor limits are built for macOS (`isolation/bounds.py`, `tests/test_isolation_bounds.py`; numbers in 17), none of them on Linux or Windows. Not built here: the egress proxy's wiring into a launch (`--allow-net` is not a launcher flag; no proxy variable is in the closed environment table), `--workspace in-place`, and the interactive relay (L6). **Requirement from the attack pass (M2):** the session bridge's acting session and `Rail.agent` (which keys `fetchlog`) are a fresh helper identity created for the launch and never the lead's name or session, and its name is not derivable from the launcher's, so a forged `pre` frame cannot write the lead's download log and nothing of the confined model carries the lead's name (`fetchlog._file` keeps `a/b` and `a_b` apart and bounds the file now); the bridge is built with an admit rule (the process group of the confinement) or a logged reason, and the launcher passes the proxy's credential in `HTTPS_PROXY` and the configured `claim_ttl_s` in `Terms`. `claude.py`, `codex.py`, `pi.py`, `coding.py`, `Manager._process`, `TaskManager`, `harnessing.opened` use `isolation`; the environment allow-list; Codex flags; the catalogue; `require_isolated_execution` and its 14 sites are deleted; `coding_workspace` leaves `STANDALONE`; the refusal-asserting tests are rewritten; the real-model run on this Mac (Claude Code and Codex, one turn each through the lease on the one slot, writing a file in the stage, publication applying it, and a second file that is not plain source refused at once with a patch and an `isolation.report` message); no launcher asks for confirmation or a token (Zero friction); `subprocess-spawns` falls. Needs L1 to L4. | the 17 launcher and route tests; the 3 browser tests; `the_launcher_is_the_only_path_that_starts_a_harness` | A30, A44, A45 |
| **L6** interactive | the pty relay; P-tty | an interactive launch draws, reads keys, and cannot reach the person's terminal; `a_pty_given_as_stdio_is_not_a_person` with the real relay slave and `TIOCSTI` text | N9, A18 |
| **L7** Linux and WSL | the tier proven on the WSL device; the CI step and the second CI job that keeps the AppArmor restriction on; the shipped AppArmor profile (`src/poolhouse/isolation/apparmor/poolhouse-bwrap`), `poolhouse security sandbox install` and `uninstall` as steps of the package install and setup (a package maintainer script or setup that already holds the privilege the person gave to install the package) and bubblewrap brought in by the same route, with the sentences of section 9 that name the fact and no command; cgroup limits, `RLIMIT_NPROC` and seccomp where the probes pass (P-nproc, P-cgroup, P-seccomp) | the whole Linux suite, red until the step exists; `the_shipped_apparmor_profile_is_scoped_to_bwrap_and_changes_no_sysctl`; `a_launch_on_a_host_without_the_apparmor_profile_or_bubblewrap_refuses_at_once_with_a_sentence_that_names_the_fact_and_no_command_to_run_and_never_prompts_or_installs` | N6, N8 (Linux half), G13 on Linux (P-ident), AF9 |
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
| the name tables of `guard/autoexec.py` (`RUNS`, `FOLDERS`, `FILES`, `SUFFIX`, `CONFIGS`) | `guard.runs_later.why` (4.4.1) |

Not deleted: `coding_workspace` and its tests (16); `coding_actions`, the ledger methods of `Conversations` and `pi_tools` (the
tool-proxy model, kept until the native path is proven; 18.10); `SandboxedBash` and the agent loop's policies (other harness).

### 15.3 The failing tests

Measured on development at `bf17c71cd` with `scripts/test all FILES --no-reuse -n 1` on seven files: 1,589 passed, 30 failed.

| File | Failed | Cause | Slice |
|---|---|---|---|
| `test_claude_launcher.py` | 8 | `launch` returns 2: the refusal sits before the lease and the runner | L5 |
| `test_harness_rails.py` | 4 (`TestLaunch` 2, `TestHarnessSession` 1, `TestCodingAgent` 1) | same | L5 |
| `test_coding_routes.py` | 5 | `Manager.start` raises, the route answers 400 for 202 | L5 |
| `test_coding_browser.py` | 3 | they assert the refusal text, and failed earlier: `POST /ui/conversations` answered 400 `argument should be a str or an os.PathLike object ... not 'NoneType'` because the rig's `Serving` leaves `ui.root` unset and `setup_context.bind` resolves `Path(ui.root)`; the rig sets the root (L0, landed), and all three are rewritten in L5, since their expectation is the unconditional refusal | L0 fixed the rig, L5 rewrites |
| `test_harness_exec_chain.py` | 4 | passed with `POOLHOUSE_BOARD` unset and failed with it set: the suite did not clear it (L0, landed) | L0 |
| `test_harness_exec_chain_more.py` | 5 | passed with `POOLHOUSE_BOARD` unset and failed with it set: the suite did not clear it (L0, landed) | L0 |
| `test_harness_hook_outages.py` | 1 | same: with the board named, the node outage the test makes is reached only after the missing token (L0, landed) | L0 |

So 17 failures are the refusal, 3 assert it (their rig is fixed), 10 were the shell's board variable (the suite clears it now). `test_layers::test_no_import_reaches_above_its_layer`
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
| Resident memory of a confinement (the physical footprint of its processes together) | the memory the broker reports unadmitted, divided by (live confinements plus one); where the node keeps no memory budget or does not answer, the memory the machine reports available takes its place | the node's lease table, or `psutil`, read at launch (`isolation/bounds.py`). Live confinements counts this one, in the memory, disk, CPU and descriptor rows; the count is of the launcher's own process |
| Stage volume (the stage and the agent home together) | the free bytes of the stage volume divided by (5 full copies times (live confinements plus one)) | the volume, read at launch; 5 is the list master, before, stage, result and current capture, and no test counts it yet (a session also keeps one result capture per turn). Replaces the 16 MiB, 256 MiB and 30 s of `source_snapshot` (F7), which are removed (18.2); this is the machine's free space, not a project ceiling or a per-file cap |
| File size (`RLIMIT_FSIZE`) | the stage volume bound of this table | no one file may be larger than the whole session may hold; never above the launcher's own hard limit |
| CPU time of one process (`RLIMIT_CPU`) | the model request timeout (`isolation.serving.IDLE_SECONDS`, 180 s) times the machine's cores, divided by (live confinements plus one) | an existing constant of the code and `os.cpu_count()`; never above the launcher's own hard limit |
| Open descriptors (`RLIMIT_NOFILE` of each process; the watcher counts the session's together) | the smaller of `kern.maxfilesperproc` and (`kern.maxfiles` minus `kern.num_files`) divided by (live confinements plus one) | the system's file table, read at launch |
| Interval between two looks of the watcher | `serve._watchdog.POLL_S` (0.5 s), and never less than the last look took | an existing constant of the project's server watchdog. The time the disk bound takes to fill, measured while staging, is not used: a clone moves no bytes, so it measures no throughput, and it gave minutes where a bound ends the session at once |
| Model request body | the served window times 4 bytes | UTF-8 encodes a code point in at most 4 bytes (RFC 3629) and a token is at least one code point, so a larger text cannot fit the window |
| Session bridge frame | the served window times 4 bytes | a tool input is model output, which the window bounds; replaces `coding_actions.MAX_FRAME` for this channel |
| Requests in flight to the model server | the lease's slot count | one conversation per slot (`docs/harnesses.md`) |
| Bridge token | 32 random bytes (256 bits) | the session token of `docs/design-session-capabilities.md` 1.1 |
| Stage build | progress-based: stalled when the files visited and the bytes advanced do not change | no clock; replaces the 30 s deadline |
| Every wait this design adds (the barrier of 4.2 item 3, the flush and verification read of 4.4.3, the workspace, service and installer steps) | a no-progress guard, then a total guard: the no-progress stretch is the longest silence the step's own recorded runs showed times the project's margin, the total is the recorded typical duration times the project's margin | the hang guards of `docs/design-test-runner.md`, full text `git show ac0639291:docs/design-test-runner.md` ("Hang guards: no progress first, a total second"); both margins are the project's and there is no default number here; a step with no history is guarded by the gaps it produces itself and the report says so |
| Reports per launch (4.4 step 3) | the session's share of the board log, the same share that bounds its other messages | the root's log share of `docs/design-session-capabilities.md` (the "Log starvation" row); no number here; past it one sentinel signal replaces the reports |
| Bytes out through an allowed host (6.3) | per host, the largest bytes-out of a real install from that host, recorded in `contracts/egress-hosts.json` with the command and the date that measured it | a measurement the L3 slice makes and a test pins; a download counts nothing against it |
| Namespace probe | `bubblewrap._probe` timeout | an existing constant, unchanged (no recorded reason) |
| Harness event line | `coding_turns.MAX_EVENT` | an existing constant, unchanged (no recorded reason) |

Every number the slices name beyond these is read from the machine at launch (free bytes, process limit, window, slots) or
measured by a test of the slice.

## 18. Decisions taken

Every question this design raised is decided. Each statement below is built in the section it names.

1. **Default workspace.** A staged copy is the default (I4, 4). It is made by an APFS clone or a reflink where the filesystem
   supports one, and a plain copy otherwise. The 16 s construct of `coding_workspace.Workspace` (F4) is a defect fixed in the build
   (W1), not a reason to edit in place; in-place stays a launch grant of the launching session (4.5, 8.3).
2. **No project size ceiling and no per-file cap.** No invented limits. The 16 MiB, 256 MiB and 30 s constants of
   `source_snapshot` are removed (W4); the only size bound is the stage volume's free bytes, a fact of the machine (17).
3. **When edits reach the checkout.** After each completed assistant turn, verified, in interactive sessions too (3.3, 4.2 item 1);
   a path still changing during the capture is held to the next turn or to exit, never applied unverified.
4. **Conflicts.** The person's version wins; the agent's copy is kept and named, and the parent never deletes or overwrites a
   version it has not verified equal to the base or to its own result (4.4.3).
5. **Native Windows.** It refuses loudly until an enforceable per-port loopback rule is shown; the WSL2 route is supported; there
   is no silent weaker mode (5.4).
6. **Linux hosts that restrict user namespaces.** Poolhouse ships, in the repository, an AppArmor profile for the one `bwrap`
   binary that grants `userns` and nothing else, installed by the package install and setup, never by a launch and never by
   changing a sysctl; a test pins its scope; without it the launch refuses at once with the sentence of section 9, which names
   the fact and no command (5.3, 9). A missing bubblewrap is the same (Zero friction, the exception).
7. **Toolchains.** The project's declared toolchain version directories are readable and executable by the declaration alone; the list
   of roots is derived from the project's own files (8.2).
8. **`watched` for G10 and G11.** Acceptable for G10, and for memory and disk, only when the watcher kills the session loudly on
   crossing a limit derived from the machine; never silent. The process count is enforced by the kernel where it can be (D5), not
   watched (5.1, 5.2, 17).
9. **Task commits.** The parent commits the published change set; the task prompt stops saying "commit" (4.2 item 6).
10. **The tool-proxy stack.** `coding_actions`, the ledger methods of `Conversations` and `pi_tools` are kept until the native path
    is proven; no caller is not dead; `HANDOFF.md` carries the entry (15.4, 16).
11. **Excluded commands.** `poolhouse-claude` and `poolhouse-codex` are added to the managed `sandbox.excludedCommands` (5.2); a
    caller the registry does not resolve as the lead or as a terminal gets the default reach and no widening argument (8.3).
12. **A second writable project.** None: a session has one writable project (8.2).
13. **Publication applies the whole change** (owner order, 2026-11-26). Refused at once, with a reason the parent writes, is only what
    would take effect unread or leave the project: `.git`, a coding tool's settings and control paths, credential names, special
    files, links that lead out and names that collide (4.4). Files of the kind that runs later are applied and named (4.4.1).
14. **What the platform cannot hide is named.** A fact a confined process can still learn is an exposure, listed in the report and
    pinned by the proof (5.7); the machine's identifiers are a guarantee (G13), and a macOS build that leaves them readable
    refuses to start.
15. **A confined process is never a person or the launching session.** The caller check reads the operating system's own report
    of the process, in addition to its marker and terminal; nothing read from a terminal the confinement holds is an answer
    (3.5, 8.3).
16. **Ending a session walks the backend's record of its processes**, never a process group (4.2 item 3).
17. **Nothing waits for a person.** Every grant, publication and hook decision is made at once by a rule or refused at once with
    the reason, and a refused publication is reported to the launching session; the credentials are minted and held by the
    launcher (Zero friction).
18. **Who may widen, and from what.** A widening argument comes only from the task the person gave the launching session or from
    a person-owned project policy outside the repository; only the lead identity or a terminal may pass one; hard caps hold for
    every caller; every widened launch is a sentinel signal (8.3, R9).
19. **The network is a sourced list.** Hosts come only from the registry list, a tunnel is cut at a measured bound on bytes
    out, and a session has no request for a host (6.3, 7).

Nothing remains open in the design. The exception recorded in "Zero friction" (the host facts of Linux) is the owner's to accept.
