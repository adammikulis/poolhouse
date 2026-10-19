# Fail closed when unobservable, and a standing hostile harness

Design, 2026-10-19. Nothing in this document is built. Owner order: make "fails closed when unobservable" the default of the
sandbox and the sentinel, and replace per-branch hand review of them with a harness that runs in the suite. Code read at
`76817ce35` (development) and at `c0baaa90c` (branch `fix/denial-long-paths`, not landed; it is the case this design starts
from). Every statement about the code was read, not run. Statements about the macOS kernel's behaviour that no test of the
fix branch records are marked **hypothesis**: the harness settles them first (slice S0), and a hypothesis that is false
changes a row, not the design.

The design costs a person nothing in a normal journey. The one new thing they can see is a single standing sentence when a
device that has a refusal log cannot read it (R6, section 2). A device whose backend has no refusal log at all (bubblewrap)
shows no sentence: it runs with a tighter policy instead (D3).

A non-author review of this document said ACCEPT with edits; section 9 lists each finding, what the code showed and what
changed.

## 1. What happened, and what it shows

- Seatbelt reports a refusal in the system log. The kernel cuts a report at about 1010 bytes, at its end, and the profile
  puts the run's tag at the end. A refusal of a path near that length reached the stream with no tag, so no run owned it,
  `DenialLog._add` dropped it and the run looked clean to the sentinel. It was found in a hand review of the branch, one
  length at a time (935, 960, 1000, 1020 in `tests/test_sandbox_denial_cut.py` on the fix branch).
- First fix: every run watched while a tagless long report arrives is unreadable, and `Result.check()` raises. The sentinel
  then noted every in-flight session for the one run's problem. Second fix: only the session whose tool call started the run
  is scored (a `ContextVar`).
- What the fixes leave: both close one hole in one place. The same shape is elsewhere (section 3): the stream drops a report
  it cannot parse, filters by path suffix, caps at 1000 entries, ignores reports after a run ended, never reads a run that
  exits 0, and long-lived confined processes (MCP servers, model servers) are never read while they run. Each is found only
  if a reviewer happens to think of it. The cure is a rule and a harness that holds every case, not a longer review.

## 2. The rule

An observation has one of three states.

| State | Means | A consumer may treat it as |
|---|---|---|
| **seen** | the observer looked and holds what it saw, with a count of what it dropped | those facts; with `dropped > 0` a lower bound |
| **absent** | nobody was asked to look: a named argument or a person's grant said so, with a reason, or the backend has no refusal channel at all (bubblewrap emits no refusal events) | nothing; status shows who chose, or which backend, and why. Not refused and not scored: the policy is tightened instead (D3) |
| **unobservable** | the observer was asked and can ordinarily say, and cannot now: log lost, report cut, report not understood, cap hit, owner unknown | unknown: not clean and not guilty |

- **R1. Enforcement never depends on observation.** The profile refuses whether or not anyone hears about it. Everything below
  concerns what is learned, not what is allowed.
- **R2. Unobservable is a value, never a missing value.** No API returns `[]`, `None`, `0` or `False` for "could not tell".
  `Result.denials` becomes `Result.refusals()`, which raises `Unobservable` unless the state is seen, and `Result.observation`
  carries the state, the cause and the drop count. A caller that wants only the exit status never touches it. There is no
  user of the old shape; it is removed, not kept beside the new one.
- **R3. Every loss is counted.** A drop, cap, truncation, filter, swallowed exception or evicted entry adds to a named counter
  (`dropped`, `quiet`, `lost`) in the observation or in the observer's status. Silent loss is a defect, found by the harness
  and by an AST finder (5.6), not by a reader.
- **R4. Closed is per run; blame is per evidence.** Unobservable makes the run's result not clean (a consumer that decides
  trust from the run refuses) and degrades the observer. It adds to a session's score only when the evidence names that
  session (section 4). Presence is not evidence.
- **R5. The default is the closed one.** The open one is a named argument with a reason, logged and shown in status, as
  `AllowUnsandboxed(reason)` is today. No environment variable, flag or global switch makes a run less observed, with one
  named exception that exists: `POOLHOUSE_SENTINEL=off` together with a non-empty `POOLHOUSE_SENTINEL_BECAUSE`
  (`sentinel/core.py`, `_configured_mode`). It stops the sentinel from scoring and emits `sentinel.opt_out` with the reason; it
  does not touch `Result.observation`, so consumers that decide trust from a run still refuse on unobservable. The hostile
  environment-variable case (family F) asserts that this is the only variable that changes anything, and that it changes the
  sentinel's mode and nothing in the observation.
- **R6. Observer health is a state with one plain sentence**: "this device cannot see what the sandbox refuses right now".
  It is a standing notice with a count and a cooldown, never a question; it clears itself when the observer's probes pass. It
  is raised for unobservable only. A backend that is `absent` for lack of a channel raises no sentence (D3): status lists it
  once as a backend fact.

### 2.1 What the value carries

`Observation`: `state` (seen, absent, unobservable); `items` (the refusals, exactly as the kernel wrote them: no strip, no
byte replacement); `dropped` (past the cap), `quiet` (start-up noise, listed, not scored), `altered` (entries in which a byte
was not valid UTF-8 and was kept as an escape, never replaced), `notices` (lines the log process itself printed), `lower_bound`
(set when the kernel or the log is shown to coalesce); `cause` for unobservable (`stream_down`, `restarted`, `settle_timeout`,
`cut`, `unparsed`, `owner_unknown`) and for absent (`no_channel`, `named`); `attributed` (`owner`, `device`, `unknown`);
`escaped` (children that left the group); `output_complete`. A `Backend` declares what it can observe (`stream`, `none`, later
`tripwire`) and answers `denials` with this value; the `keeps_refusal_log` flag and the detail strings of the fix branch become
`cause`. `Result.check()` is `refusals()` plus a raise when there is one.

### 2.2 Consumers decide, and each one is read once

A consumer that decides trust from a run calls `refusals()` and so refuses on unobservable. On `absent` it does not refuse; the
device's policy for untrusted runs is already the tighter one (D3). One that relays output to a model passes the state on in
the result and the event. Enforcement held in both, so a relayed run is not withheld.

| Consumer | Today | Should |
|---|---|---|
| `SandboxedBash` (the agent's shell tool) | reads only a failed run; raises on refusals; a note when unreadable | reads every run; relays the state; event to the sentinel with the session as owner |
| `agent/confined.py` (MCP servers) | never reads | refusals delivered as they arrive to owner `mcp_server:<name>`; the store's existing block refuses a server that keeps probing |
| `serve/confined.py` (model servers) | reads on a failed start, as text, no event | delivered as they arrive to owner `server:port:N`; the failed-start message keeps its text |
| `serve/llamacpp_compile.py` (head build of upstream code) | exit code only | a refused or unreadable step means the new build is not switched to and the current one keeps serving; one event, owner `binary:<path of the new build>` (a store kind that exists); the person is told nothing unless asked |
| `llamacpp_update.py` `_version` (line 74) | reads the output text for a line holding "version" and never the exit code or the refusals: a run that was refused reads as an empty version | the exit code and `refusals()` are read; a failed or refused probe is not a version, and the new build is not switched to |
| `sandbox/cli.py` `test` | `diagnose="never"`, a named argument | unchanged: it tests enforcement; the `device` behaviours of 5.1 test observation |

## 3. Where an observation can be missing

"Today" is read at `76817ce35`; "(fix)" is the fix branch. "Should" is closed unless it says why not. Slices are in section 7.

| # | Place | How it is lost | Today | Should be | Slice |
|---|---|---|---|---|---|
| 1 | Kernel cuts a report (~1010 bytes) | the tag at its end is gone | open; closed for every in-flight run (fix) | closed, scoped by the pid in the report's head, and a run is cleared only on positive evidence: the pid is in another watched run's descendants, or the process table shows it has no poolhouse profile (read while it lives, `poolhouse.serve.process`). A pid that is gone, was reparented to pid 1 (`setsid`, double fork) or cannot be placed is not evidence: every run in flight is unreadable (cause: cut, unattributed). The run whose descendants hold the pid owns the report. A child that exited before the run's end-of-run list leaves the pid unplaced | S3 |
| 2 | Report carries our tag but does not parse (`_hear`: run found, `entry` is None) | dropped; includes a process name holding `(` (the pattern is `[^(]+\(\d+\) deny`) and any format change in a macOS update | open | closed: unreadable, cause `unparsed` | S2 |
| 3 | Readiness probe | on development `_add` answers a probe only when the line parses to a refusal; the fix branch's `_hear` is a regression of that: it counts a probe as answered when its token occurs in any kernel message and never checks that the report parses into process, operation and target | development closed; the fix branch open: a format change passes it | the fix branch lands with `_hear` answering a probe only from a message that parses to the process, operation and target the probe provokes (the behaviour that proves it is in S0); S2 records the result with the OS build | S0, S2 |
| 4 | Noise filter (`NOISE.search(target)`) | `\.CFUserTextEncoding$` and `/GlobalPreferences*.plist$` are unanchored at the front, so any path with that ending is dropped; `^/Users/[^/]+$` drops a refused listing of the home directory; no count | open | the exact operation and target entries the confined start-up makes, counted as `quiet` and shown; a probe of any noise target still shows in `quiet` | S2 |
| 5 | Per-run cap (`KEEP_PER_TAG = 1000`) | later entries dropped silently; the count under-reports; the "first" refusal may not be the worst | open | seen with `dropped = n`; `dropped > 0` is a flood signal to the owner | S2 |
| 6 | Run cap (`KEEP_TAGS = 256`, ended runs trimmed) | a trimmed run reads back as unreadable | closed | closed; stays in the catalogue | S0 |
| 7 | Report arrives after the run ended (a `setsid` or double-forked child) | appended to an ended run nobody reads | open | each report is delivered to the owner recorded at `watch`, as it arrives, coalesced per second (also fixes row 8) | S2 |
| 8 | Long-lived confined processes (`agent/confined.py` MCP servers; `serve/confined.py` model servers) | refusals pile into the 1000-entry buffer; the model server reads them only when its start fails and sends no event; an MCP server is never read | open | same delivery as row 7; the owner is `mcp_server:<name>` or `server:port:N`, kinds the store already holds | S2 |
| 9 | Run that exits 0 with empty stderr (`_diagnose`) | never read; `test_a_run_that_was_not_diagnosed_does_not_claim_its_refusals_were_unreadable` fixes this in as intended. The head build of upstream's code (`serve/llamacpp_compile.py`) checks only the exit code, so a build step that is refused a read of a secret and goes on leaves no trace | open: a program that catches its refusals leaves no trace | an untrusted policy is read always; the settle probes are coalesced (one `sandbox-exec` probe, answered once, settles every run that ended before it was sent), because a probe plus the 0.25 s poll per run is a cost on every run (S0 measures it); the head build reads refusals only after S0 has recorded what a real cmake build is ordinarily refused, so its ordinary refusals are `quiet` and not a failed build; `never` is a named argument with a reason | S1 |
| 10 | Stream not started, silent, restarted or ended (5 s start, 5 s settle, 60 s retry hold-off) | `denials` is None | closed; but 1.0 goes to the owner for a loss that is not theirs, and every run in the 60 s hold-off is unreadable | closed; scored to the device, not a session (section 4) | S4 |
| 11 | Backend with no log (bubblewrap, container) | `unreadable` is "no refusal log" | closed per failed run, 0.2 to the owner each: 50 ordinary failing commands in 10 minutes reach the hold (10.0); a run that exits 0 is never looked at | `absent` with cause `no_channel`, recorded once as a backend fact. Bubblewrap emits no refusal events, so no consumer is asked to look: nothing refuses on it and nothing is scored per run. Untrusted runs on it get the tighter policy instead (no network, narrower reads); no sentence is raised | S6 |
| 12 | Timeout, output cap, detached child holding the pipe (`_wait`, `_Capture`, `out.join(2)`) | `bytes(out.data)` is read while a thread may still write; `truncated` stays False | open for output | `output_complete` is false when a reader outlived the join; the pipe is closed by the runner | S3 |
| 13 | Child that left the process group (`setsid`; "known limits" in `docs/sandbox.md`) | the runner does not know it exists | open | descendants listed at the end of the run (`poolhouse.serve.process`, the owner of process-table reads), killed by pid, counted as `escaped`; a child gone before the list is counted as unplaced, which row 1 treats as no evidence. The catalogue crosses escaped child with cut length (family C, guard `cut-pid-attribution`) | S3 |
| 14 | Owner of a run (`ContextVar` of the fix) | a thread that did not copy the context (`threading.Thread`, `run_in_executor`) has no owner and counts for nobody | open | an unowned run is scored to the device subject `unowned:<policy>` with a notice; a wiring test names every `sandbox.run` and `wrapped` call site and its owner | S4 |
| 15 | No listener (`HOOKS.events` and `HOOKS.scrub` are None until `sentinel.default()` runs, `sandbox/run.py:247-263`) | a process that never asks for the sentinel leaves sandbox runs unwatched (`docs/sentinel.md`), and `Hooks.environment` returns the unscrubbed environment | open by accident | the callers outside both packages that start confined processes (`agent/confined.py`, `serve/confined.py`, the `llamacpp_*` steps) call `sentinel.default()` before their first run, and a wiring test names each of them; sandbox cannot import sentinel, so it does not arm anything. A run with no listener sets `Observation.attributed = "unwatched"`, logs `sandbox.unwatched` once per process with its reason and counts it in `HOOKS.unwatched`; an unscrubbed environment is the same state, shown in status | S4 |
| 16 | Listener raises (`_emitter`: `contextlib.suppress(Exception)`) | the event is lost, the run goes on | open | counted in `HOOKS.lost`, logged at error, in status, red chip | S4 |
| 17 | Score windows (`Windows`, 4096 keys, oldest popped) | a flood of distinct keys evicts a quiet attacker's own score, or a victim's | open | a per-origin cap on windows (the origin is the owner of the run or call that made the key); an origin over its cap evicts its own oldest first and never another origin's; every eviction is a counted device event | S4 |
| 18 | Store full (`Store._new` refuses at 8000 records, twice `max_records`, reached only with that many held). The quarantine path emits `store.overflow` (`store.py:321`); the watch path (`:277`) returns None and says nothing. `Sentinel.handle` still returns `act`, and `Score.add` has already set `held` so it never fires again | on the watch path nothing is recorded and nothing says so; on the quarantine path the event exists but `handle` and `Score` ignore it | open | `handle` returns `unrecorded`; `Score` sets `held` only after the store confirms; both paths emit `store.overflow`, shown in the chip. Capacity is per origin (row 23), so a full store is a signal about one origin and blocks no session | S4 |
| 19 | Decoy listener hit (`Honey.endpoint_hit`) | every session with a tool in flight is quarantined (high confidence): one request by anyone present freezes all | open, and victim-blaming | attribution by a per-owner token (section 4, S5); unattributed raises a device alarm that tightens the in-flight sessions' next runs and rotates the decoy, and freezes nobody | S5 |
| 20 | Identical refusals in a burst, and log volume | **hypothesis:** the kernel or `logd` coalesces or drops events under load; a neighbour's single refusal may be lost beside a flood while the settle probe still arrives | unknown | harness measures N identical refusals (1, 10, 1000) and a flood beside a neighbour; if events are lost the observation is `lower_bound`, and a per-run refusal rate limit becomes its own slice with the owner's number | S0 |

| 21 | `Bus.emit` log write and subscribers (`sentinel/events.py:311-319`) | an `OSError` writing the durable log is a `logger.warning`; a subscriber that raises is a `logger.exception`; the event reaches memory and the other subscribers only | open: a lost event is in a log file nobody reads | `Bus.lost` counts each (log, subscriber), shown in status; a log-write loss is a red chip, since the sealed record of the event does not exist | S4 |
| 22 | `Bus` memory (`recent[]`, `del self._recent[:-self._most]`, 2000, `events.py:308`) | the oldest event is dropped with no count | open | `Bus.lost` counts evictions; a reader of `recent()` is told how many it cannot see | S4 |
| 23 | `Store._prune` (`store.py:463-479`) | past `max_records` (4000) it deletes the oldest CLEAR, WATCH and RELEASED records by `updated`, and deletes CLEAR records after 7 days (`clear_after_s`) and unlinks a released record's payload after 30 days; an owner that mints keys evicts other owners' watch records, and no event says that a watched subject was forgotten | open | a per-origin record quota that evicts that origin's own oldest first; a hard cap on watched records per origin and in all; each eviction of a WATCH record is a counted event (`store.evicted`); a WATCH record is never removed by another origin's activity | S4 |
| 24 | `Hooks.environment` before `default()` (`sandbox/run.py:259`) | `HOOKS.scrub` is None, so the child gets the unscrubbed environment; row 15 covered events only | open | the same arming and the same `unwatched` state as row 15, and the scrub lives in the sandbox as a default scrub that the sentinel's `scrub_env` only extends | S4 |
| 25 | The stream alters what it reports (`denial_log.py`: `errors="replace"` at 194, `.strip()` on process and target at 78-79; a line that starts with `{` and does not decode as JSON returns None at 66-69) | an invalid byte becomes U+FFFD so two distinct targets read as one; a leading or trailing space in a refused path is removed so the target is not the path; a line our predicate selected that does not decode vanishes | open: "exact refusals" is false today | the text is read as bytes with `surrogateescape`, nothing is stripped beyond the pattern's own framing, `altered` counts escapes; a `{` line that does not decode, while a run is in flight, makes every run in flight unreadable (cause `unparsed`, unattributed) | S2 |
| 26 | The stream's own notices (`denial_log.py:211-212`) | non-JSON lines go to `deque(maxlen=3)`; a notice that matters can be pushed out by two later ones | open | every non-JSON line is counted in `notices`; the last few are kept for the start-up message; **hypothesis** (S0 measures it): a notice that says log data was dropped sets `lower_bound` on every run in flight | S0, S2 |
| 27 | `Score._levels` (`sentinel/score.py:60,78`) | one entry per subject, never pruned, while `Windows` evicts at 4096 keys: after a subject's window is evicted its `held` and `watching` flags remain, so it cannot fire again, and the dictionary grows with every subject seen | open | `Score` keeps no state outside the window and the store: `held` is read from the store, `watching` from the window; an entry goes when its window slot goes | S4 |

Already closed and kept: the tag is read from the last line of the message so a target with a newline cannot name another
run's tag; only events from the kernel's Sandbox extension count; a stream restarted after a run began makes it unreadable;
the running run's tag is never trimmed; a failed start ends the run it began watching.

## 4. Who is blamed (victim-blame)

**A signal is scored against a subject only when the evidence names that subject.** Evidence, strongest first: a token only
the subject holds (the run tag; a per-process decoy token); a context captured at the call site, not in the watched process
(the owner, copied into pool threads with `contextvars.copy_context`); a process-tree fact read while the process lives (pid
inside the run's descendants); never "was in flight at the time".

An owner is a kind and a key captured when a run is watched, not when its event arrives: `session:<id>`, `mcp_server:<name>`,
`server:port:N`, `caller:<key>`, `binary:<path>`, all kinds the store already holds. A signal with no evidence goes to a
**device subject**, a new store kind no hook acts on (`observer:<backend>`, `unowned:<policy>`, `decoy:endpoint`). A device
subject can be watched, shows in status and the chip, tightens policy for new runs (and, for the decoy alarm, for the next runs
of the sessions in flight), and makes consumers that decide trust from a run refuse. It never freezes or quarantines a session
and holds nothing a person must release.

**Decoy tokens (S5).** A token exists only where a hit is possible: the bash policy is `Net.deny()` (`sandbox/policies.py:51`),
so a token in a file a shell can read could never be used from the shell, and a per-session decoy file would widen every
profile's reads for nothing. Tokens are therefore minted for the loopback policies (`mcp_server`, `model_server`), one per
confined process, kept in that process's own scratch directory and never in a project directory (a project directory is
readable by the sibling sessions, which could then replay it to frame the owner). The owner of a token is the confined
process's owner (`mcp_server:<name>` or `server:port:N`); the session whose tool call started that process is recorded as
evidence beside it, not as the owner. A hit that carries a token is scored to the token's owner. It freezes that owner only when
the token **and** one more fact agree: the connecting process's pid is in the owner's process tree, or the owning session is in
flight; otherwise the owner is put on watch, never held.

| Signal | Today | Should |
|---|---|---|
| refusal with the run's tag | the owner (fix) | the owner; the tag proves it |
| flood (`dropped > 0`) | not seen | the owner, same proof |
| unreadable: cut, lost, stream down | the owner, 0.2 or 1.0 | the device; the owner only when the pid is inside its descendants |
| no refusal channel (`absent`) | the owner, 0.2 per failed run | nobody; a backend fact, and the tighter policy |
| run with no owner | nobody | `unowned:<policy>` |
| long-lived server's refusal | nobody | its `mcp_server` or `server` owner |
| decoy endpoint hit | every in-flight session | the owner of the token used (with the second fact above to freeze); else `decoy:endpoint`: a device alarm that taints the sessions in flight (their next runs get `policy.without_network()`, a scrubbed environment and a rotated decoy) and holds nobody; no person releases it, it clears when the decoy has been rotated and the sessions that were in flight have ended |
| decoy value seen in text screened for S | S | S (the value is in S's own context) |
| guard denial, refused lease, resource abuse | the session or caller named by the call | unchanged |

Properties the harness holds for every behaviour run beside an innocent bystander: the bystander's score does not change; none
of the hostile run's refusals appear in the bystander's events; the bystander's result is seen, or unobservable with a cause
and no weight when the observer itself failed. **What stays costly:** one local process that emits long reports can keep every
run it cannot be told apart from unreadable for as long as it does. That is availability, not blame, and pid attribution
(S3) narrows it to the runs it cannot clear.

## 5. The standing hostile harness

### 5.1 What it is

`tests/hostile/` is a package: the catalogue, the programs that run confined, the expectations, the guard registry and an
adapter over `Result`. `tests/test_redteam_hostile_*.py` run it (the name puts them under `weakened-assertions`). It sits in
`tests` because it is test support. The `device` behaviours (the readiness and format probes, a long-path round trip at three
lengths, a detached child; a few seconds) live in `src/poolhouse/sandbox/selftest.py`, which the catalogue imports and
`poolhouse security sandbox test` runs, so a person's device checks that it observes what it refuses, on request and when the
OS build differs from the last one verified. A cut report reaches every refusal stream on the machine, so the selftest waits
until no confined run is in flight on the device before it makes one, and says why it is waiting. The harness is called by the suite, by that command and by the nightly job.

One **behaviour** is a record: `id`, `family`, the program and files it needs, `needs` (backend, platform, whether it must be
alone on the machine's log), `expect`, `kills`.

- `expect.enforced`: the forbidden thing did not happen (a canary file's contents never reach the program's output, a
  forbidden write leaves no file). Checked for every behaviour on every backend, apart from the observation.
- `expect.observation`: per backend class, `seen([...exact refusals])`, `unobservable(cause)` or `absent`. A behaviour never
  expects an empty seen unless it is a control.
- `expect.consequences`: score change for the owner, for a bystander (zero), the device state, the events.
- `kills`: the guards whose removal must turn this behaviour red (5.5). `control = true` behaviours (a 900-byte path that the
  kernel keeps whole is still reported with its exact target) check that a guard does not over-fire.

### 5.2 The catalogue

| Family | Hostile behaviour | Grid |
|---|---|---|
| A names | probe forbidden paths | lengths 1, 64, 255, 500, 880, 900, 935, 960, 1000, 1013, 1020, 1024 and a path past `PATH_MAX`; encodings ASCII, 2, 3 and 4 byte characters, NFC and NFD, space, parentheses, tab, newline, quote, backslash, control bytes, trailing dot; names ending in a noise suffix; names holding another run's tag; names holding a fake `Sandbox: x(1) deny(1)` line; operations read, metadata, list, write, create, unlink, rename, exec, network connect, mach lookup |
| A' names of the process | **hypothesis:** the kernel's `proc(pid)` field comes from the name the program was started under, so a link in the writable directory named `x(1) y` to an allowed binary breaks the report's pattern | one case per punctuation class |
| B floods | refusals per second 10, 1000, 100000 in one run; 300 short runs ended quickly (past `KEEP_TAGS`); a flood in one run beside one refusal in a neighbour; 5000 distinct session, caller and address keys; notes from 64 threads | the neighbour case repeats N times to measure loss |
| C lifecycle | exit 0 with refusals swallowed; a child that calls `setsid` and refuses after its parent exited; double fork; a child holding stdout open; killed by timeout mid-refusal; a refusal in the last millisecond before exit; **escaped child x cut length**: a child that has left the group (`setsid`, double fork so its ppid is 1, and one that exits before the end-of-run list) emits a refusal at each cut length of family A, beside a bystander run in flight. Expected: every run in flight is unobservable (cause cut, unattributed), never seen, because none of the pids can be placed (row 1); the bystander's score is unchanged | the guard `cut-pid-attribution` and the guard `clear-on-evidence` are killed by it |
| D forgery | fake refusal text on stdout, stderr and through the allowed logging service in the kernel's format with another run's tag; a message carrying a live probe token | |
| E neighbours | a cut report beside an innocent run; a run under a foreign Seatbelt profile (not ours) that makes long reports; a hostile run making another run's tag collect entries | the bystander property of section 4 |
| F observer faults (injected, not by the program) | the log process stopped and killed; replaced by a program that prints garbage, another format, or nothing; the reader thread dies; the events log gets "no space"; a sealed file is damaged; the listener raises; the clock jumps; hostile environment variables (only `POOLHOUSE_SENTINEL=off` with its reason changes the sentinel's mode, and nothing changes `Result.observation`) | every case builds its own `DenialLog()`: the shared one holds a 60 s hold-off (`RETRY_SECONDS`) after a failed start, and a fault in it would make every later case wait or read unobservable |
| G sentinel | a decoy hit with two bystanders in flight (token, token from a process outside the tree, no token); a run with no context; the store at capacity and one origin minting 8000 digests or distinct watch keys beside a new session; the window at 4096 keys; no listener installed; the bus log full; a long-lived server that probes for an hour of simulated time | |
| H benign controls | ordinary work scores zero: fifty failing test runs, `grep` with no match, a long allowed path, a `find /` that is refused a thousand times (one run, one score) | follows `tests/test_sentinel_benign.py` |

### 5.3 Tiers, selection and cost

| Tier | Runs | Where and when | Cost bound (targets until S0 measures) |
|---|---|---|---|
| **fast** | recorded kernel messages fed to the parser and the stream's `_hear`, and the sentinel half with fake backends (`tests/sandbox_kit.py` already has them) | everywhere, including the Linux CI shards, on every change that selects it | about 600 cases in 1 s; no sandbox, no clock sleeps |
| **real** | real `sandbox-exec` and the real `log stream`, one shared stream per module | the Seatbelt pass (`tests/seatbelt-modules.txt`: a confined process cannot apply a second profile, so these files run in the pass without the write denial); the `macos` jobs of D4; the person's Mac | 60 to 80 confined runs; stream start once; every cut case in one exclusive block. The block is a broker resource lease taken with no compute lease held; `kernel_log(alone=True)` today is a machine-wide `flock` on files in the temp directory (`tests/sandbox_kit.py` on the fix branch), whose wait counts as test time and which holds a broker slot idle while it waits, so it is replaced by the lease |
| **linux** | bubblewrap contract: enforcement for every behaviour, `absent(no_channel)` expected and the tighter policy checked | `scripts/test-on-linux` on WSL | one run per behaviour |

- **Corpus.** `tests/data/kernel-reports/` holds raw log lines captured from the real tier, keyed per OS build (one file set per
  build, so two builds are compared, never merged). A raw line carries `/Users/<owner>` and `pytest-of-<user>`, so the capture
  runs with a fixed `--basetemp` under a synthetic user name of the same length as the real one (the cut depends on the length),
  rewrites the account name in every line the same way, and the corpus is run through `scripts/hooks/no-real-names` before it is
  committed; the nightly job does the same. It captures again, drops the pid and the time, and fails on a difference, so the
  fast tier cannot drift from the kernel and a macOS update that changes the report format turns the nightly red the next
  morning rather than a review missing it. Repeated lines are told apart by the log's `machTimestamp` and `traceID`, never by
  content: two identical refusals are two events, and content-based dedupe would hide the coalescing of row 20.
- **Selection needs one change to the selector.** It is not true that no new rule is needed. `scripts/affected.py` indexes only
  `tests/*.py` (`index()` globs `tests/*.py`; `module_of` accepts a tests path only when it has two parts), so a file under
  `tests/hostile/` is not a module: editing it is `unmapped`, which means run everything, which an agent may not do. And
  `DEPTH = 1`: a change to `sandbox/denial_log.py` selects only test files that import that exact module, not a file that
  imports the package. So S0 contains a change to `scripts/affected.py` that indexes `tests/**` and its own test, and the
  harness files import the modules they cover by name (a module map generated from the package, checked by the wiring test),
  so a change to any module under `sandbox/` or `sentinel/` selects the harness files that cover it. The wiring test also
  names the consumers outside the two packages (`agent/confined.py`, `serve/confined.py`, `serve/llamacpp_*`,
  `platform.start_grouped`, which `denial_log` uses to start the log process) as covered modules, and fails when a module of
  the two packages or one of those is imported by no harness file.
- **Fixed cost.** A targets-then-measure slice: S0 measures stream start, settle and per-run cost on the owner's Mac and
  `tests/test_redteam_hostile_cost.py` asserts the measured bounds. The shape is fixed now: fixed overhead under 5 s and under a
  quarter of the wall time (AGENTS.md, "A run's fixed cost is budgeted"), the lock wait of the exclusive block counted as
  fixed cost, the stream started once per module, and no behaviour that sleeps to wait for the log (it waits on `settle`, which
  returns when its own probe arrives). S0 measures what reading every run adds (a `sandbox-exec` probe and a 0.25 s poll per
  run today), the coalesced probe that replaces it, and a real cmake build's ordinary refusals.
- **Seeded exploration.** `scripts/hostile --seed N` samples the grid of family A and the faults of family F at random,
  seeded by the commit as `scripts/mutate` is, nightly. A red it finds is added to the catalogue as a fixed row in the same
  commit that fixes it; the sample itself is not part of the suite.

### 5.4 What a branch touching these two packages must show

A change under `src/poolhouse/sandbox/` or `src/poolhouse/sentinel/` lands when the selected harness files pass and every
changed lossy site (5.6) has its row. It is not sent to a reviewer for the classes the catalogue holds. A reviewer's output
is a catalogue diff: a hole found by reading becomes a behaviour that goes red first, and stays as a permanent row; a prose
finding with no behaviour is not a finding. A design that adds a new class (a new backend, a new owner kind, a new signal)
still gets the written attack-first pass of AGENTS.md section 1 before it is built. Once S0 has landed, the lead edits that
section and `docs/redteam.md` to say this, in a commit of its own that names the owner's order (AGENTS.md, "A rule-file edit
the owner orders lands at once").

### 5.5 Kept honest: every guard has a test that goes red without it

`tests/hostile/guards.py` is a registry. Each guard has a name, the place in the real source where it lives (`module:qualname`
and a selector for the statement or constant) and a **mutant** built from that source (`cut-makes-unreadable`,
`unparsed-makes-unreadable`, `noise-is-exact`, `drop-is-counted`, `late-is-delivered`, `diagnose-always`,
`owner-from-context`, `weight-by-cause`, `quota-evicts-own`, `store-full-is-reported`, `listener-error-is-counted`,
`escaped-are-killed`, `cut-pid-attribution`, `clear-on-evidence`, `decoy-token` and the guards that exist today:
`tag-from-last-line`, `sender-is-kernel`, `settle-before-read`, `generation-check`).

- **Mutants come from the source.** A hand-copied replacement function drifts from the function it replaces and goes on
  passing. A mutant is made by deleting the guard's statement (or replacing its constant) in the AST of the real module and
  loading the result in place of it; a registry entry whose selector no longer matches the source is a test failure. The
  mutant is in-process, so it costs milliseconds and copies no tree.
- **Kill rule.** For each guard, load the mutant, run the behaviours in its `kills`, and require at least one to fail **with the
  expected observation**: each `kills` entry names the observation that must differ (for example `seen -> unobservable(cut)`).
  A crash, an import error or any other failure is not a kill. A guard no behaviour notices is a test failure that names it.
- **Over-closed mutants.** The registry also holds `everything-unreadable` and `everything-scored`. Controls and family H must
  kill them, so "always closed" cannot pass as safe.
- **Orphans.** A behaviour with no `kills` and no `control` mark fails the wiring test: a behaviour that no guard's removal
  turns red proves nothing. A behaviour that is skipped (a platform or backend it needs is missing, or `needs` is not met)
  fails when its platform is the one running; only a behaviour whose `needs` names another platform may skip.
- **Constants and the kernel's own behaviour.** `CUT_BYTES`, `KEEP_PER_TAG`, `NOISE` and the `log stream` `PREDICATE` cannot be
  proved against a fake backend: what they must equal is what the kernel does. Their mutants run in the real tier, against
  the real stream.
- **Real mutants on every change.** Every change to `sandbox/` or `sentinel/` runs `scripts/mutate --guards` through the real
  mutation engine (a copy of the tree, the source changed) for the guards its diff touches, and the nightly job runs all of
  them, comparing each verdict with the in-process one.
- **Fail first.** Every slice commit carries the behaviours that were red against its parent; the existing fail-first check
  applies unchanged.
- **The registry and catalogue cannot be quietly weakened.** The harness test files are named `test_redteam_hostile_*`, so
  `scripts/hooks/weakened-assertions` refuses a diff that removes an assertion or a test function from them without a
  `Reviewed-by-second:` or `Owner-order:` line. Weakening in the catalogue data does not remove an assertion, so S0 extends
  that hook: a diff under `tests/hostile/**` that edits an `expect` or a `needs` of a behaviour (loosening an expected
  observation, narrowing the platforms it runs on, dropping a `kills` entry or a `control` mark) needs the same line. A
  behaviour deleted from the catalogue data is caught by the kill rule (a guard loses its last killer) and by the coverage
  map (its harness id stops resolving).

### 5.6 How `scripts/redteam_coverage.py` maps to it

Today the script finds surfaces by AST and checks that each has a row naming an existing test. Nothing checks that the test
notices the surface's guard going away, and nothing finds a place that loses information. Changes (S7):

1. A new surface kind `lossy`, found by AST inside `sandbox/` and `sentinel/` (both are already in `READ_DIRS`): a
   `contextlib.suppress` or an `except` that neither raises nor counts (7 `suppress` sites in these packages today, plus the
   `except` ones); a constant-bound slice of received data; a bounded container (`deque(maxlen=`, `popitem(last=False)`,
   `len(x) < CONST` before an append, `del self._runs[...]`); a match whose failure branch returns None or continues; a
   function documented to return None when something "cannot be known". The finder is heuristic; a false positive is an `n/a`
   row with a note saying why the loss is by design (for instance `_kill_group` suppressing `ProcessLookupError`: the process
   is already gone).
2. Each `lossy` row names `harness = ["A/cut-1000", ...]`, ids resolved against the catalogue as `ref_problems` resolves test
   names, and `guard = "cut-makes-unreadable"`, resolved against the registry. `covered` requires both to exist and the
   guard's kill test to pass.
3. `--check` fails on a lossy site with no row (printing the stanza to add, as it does for a surface), on a harness id or
   guard that does not exist, and on a ratchet that rose; the ratchet only falls, as now. `coverage.json` gains the kind, and
   `affected_rules.GENERATED` already reruns the script when it changes.

## 6. Attack-first

The ten cases of AGENTS.md section 1, answered for this design by the author. The non-author pass is still required before the
design is built.

| Case | Attack on this design | Answer |
|---|---|---|
| copied or cloned pool or device | a cloned state root carries "observer healthy" and a recorded OS build, so the format probe is skipped | observer health is recomputed from probes at process start and keyed by the OS build; it is never read from state or shared on a board; device subjects are local data no other device reads as trust |
| replay | replay a recorded refusal or a probe answer into the stream; replay a late report | only events the kernel's Sandbox extension sent count (existing); probe tokens and run tags are random and never reused; late delivery is idempotent per the event's `machTimestamp` and `traceID`, never by content; family D |
| forged or below-threshold signature | edit a sealed device record; stay under the watch level by spacing signals; evict your own score (row 17) | the seal fails and the chip is red (existing tamper handling covers the new records); an origin evicts only its own oldest records and scores, and a record on watch is removed by no other origin's activity (rows 17, 23); a patient prober under the window is accepted risk A2 |
| compromised or malicious member | a member's job floods the log or makes long reports to blind or blame local sessions | a member's runs are owned by its caller key, set by the device from the authenticated request, never from the body; its flood is scored to it; the bystander property holds (family B, E) |
| stolen phone or key | use a key to clear the degraded state or to release a device subject | no new approval path: a device subject holds nothing to release and clears itself when probes pass; releasing a held session stays a person's act |
| malicious agent | hide probes (cut, flood, detach, swallow), forge refusals, turn observation off, edit the registry | families A to F; no switch exists (a harness case runs with hostile environment variables and arguments and asserts the observation is unchanged); the harness tests are under `weakened-assertions` and the kill rule catches a deleted behaviour |
| agent compromised without knowing it | an injected instruction makes it run long-path or probing commands in good faith | a first unreadable run of its own costs it at most one weight when the pid is its own and nothing when unknown; an owner with three or more unreadable runs in ten minutes is watched and its next runs go alone (D1); thresholds stay 3 and 10; family H proves ordinary work scores zero |
| downgrade, secure to open | a lost stream, a failed probe or an OS update silently falls back to "no refusals"; `diagnose="never"` reached from a tool | there is no fallback path: a failed probe is unreadable, not clean; `never` and a named `absent` are arguments the shell tool does not expose and `agent_may` refuses to name; `absent` for a backend with no channel is a fact of the backend that carries the tighter policy, not a way down from `seen` |
| approval fatigue | degrade the observer repeatedly so a person is asked again and again | no question exists: one standing notice, repeats fold into its count, the cooldown of `heads_up` applies, and no button changes the state |
| takeover by whoever is merely present | another process on the device makes long reports, floods the log or hits the decoy listener to get sessions frozen or blinded | presence is not evidence, so no session is scored or frozen (section 4); a decoy hit without a token tightens the sessions in flight and rotates the decoy, and holds none of them; residual: it can keep runs unreadable while it does, accepted risk A1 |

**Accepted risks, named for the owner.** A1: a local process can make runs unreadable for as long as it emits long reports;
the cost is availability of clean results, never blame. A2: a prober that spaces refusals past the 600 s window is not scored;
every refusal stays in the event log and a per-subject daily count is shown as evidence for a person, never a trigger. A3: the
harness proves the observation, not the kernel's enforcement; enforcement bugs in a deprecated tool are out of reach. A4: a bubblewrap
device has no refusal channel; it runs untrusted code under the tighter policy and reports `absent`, and a tripwire channel is decided later (D3).

## 7. Slices

Each slice lands alone: its behaviours are red against its parent, the suite is green after it, it updates the documents it
touches and prunes `HANDOFF.md`. Order is by what is correct, not by ease. `fix/denial-long-paths` lands first, with the one
change row 3 names (`_hear` answers a probe only from a message that parses). S6 follows S0, and S1's consumer flip follows S6.

| Slice | Delivers | First red |
| --- | --- | --- |
| **S0 harness** | `tests/hostile/` (catalogue, programs, registry, adapter over today's `Result`), families A, B (cap, trim), C (exit 0, timeout, escaped child x cut length), D, E, F (stream faults, each on its own `DenialLog`), H and controls; a change to `scripts/affected.py` that indexes `tests/**`, with its test (section 5.3); the broker-lease exclusive block replacing `kernel_log(alone=True)`; guards built from the source by AST with the expected-observation kill rule, and the `weakened-assertions` extension to `expect` and `needs`; kill arms for the guards that exist; the four cut sizes of the fix branch move into the catalogue; the measurements of rows 20, 26, A' and of reading every run and a real cmake build; the cost test; the corpus (fixed basetemp, synthetic user, `no-real-names`) and its nightly diff; `sandbox/selftest.py` behind `poolhouse security sandbox test` | the exploratory grid run once on today's code: its reds become the briefs of S1 to S4; only green rows enter the suite |
| **S6 absent backends** | `Observation` state `absent` with cause `no_channel` for bubblewrap and the container backend; the tighter policy for untrusted runs on a device whose backend declares `stream: none`; no per-run score, no refusal, no sentence | row 11 |
| **S1 observation** | `Observation` (2.1), `Result.refusals()`, `diagnose` always for untrusted policies with coalesced probes, the consumers of 2.2 (the shell tool relays; the head build and the version probe refuse; the model server and MCP launch hand their reading to S2); the consumers refuse on unobservable only after S6 has landed | row 9; the old `denials` attribute does not exist |
| **S2 stream** | parse strictness and a parsing readiness probe with the OS build; bytes read with `surrogateescape`, nothing stripped, `altered` and `notices`; a `{` line that does not decode makes runs in flight unreadable; exact noise with `quiet`; `dropped`; delivery at arrival to the owner recorded at `watch`, coalesced per second (late reports, MCP and model servers) | rows 2, 3, 4, 5, 7, 8, 25, 26 |
| **S3 lifecycle** | descendants helper in `poolhouse.serve.process`; escaped children killed and counted; `output_complete`; cut attribution by pid, clearing only on positive evidence | rows 1, 12, 13 |
| **S4 sentinel** | owner `(kind, key)` at `watch`; `unowned:<policy>`; device subject and observer state; weights by cause (the table of section 4); chip sentence; the callers outside both packages call `sentinel.default()` and a wiring test names them; the `unwatched` state and a default scrub in the sandbox; `Bus.lost`; per-origin quotas on `Windows` and `Store` with a hard cap on watched records and `store.evicted`; `Score` and `Store` agree and `Score` keeps no state outside the window; full store reported on both paths; the D1 watch and run-alone rule | rows 10, 14, 15, 16, 17, 18, 21, 22, 23, 24, 27; the bystander property |
| **S5 decoy** | a token per confined loopback process, in that process's own scratch directory; a hit names the token's owner and freezes it only with the second fact of section 4; a hit without a token raises the device alarm (tighten the sessions in flight, rotate the decoy, hold nobody); attack-first pass by a non-author before it is built | row 19 |
| **S7 coverage** | the `lossy` kind in `scripts/redteam_coverage.py`, harness and guard references, `scripts/mutate --guards`, a test that every module of the two packages is reached by a harness file | a lossy site with no row fails `--check` |

A tripwire channel for bubblewrap (watched decoy paths, or the kernel's audit for Landlock) is not scheduled; the owner decides
later whether it becomes a slice (D3).

Activation for every slice: the next `poolhouse runtime ensure` build, then the real tier on the owner's Mac and the linux
tier on WSL, repeating the original failure (a refusal of a path of each length, a flood, a detached child) as a person
would run it through the shell tool. A green fast tier, a green real tier and the kill rule are the evidence a slice reports;
a Mac result is not a Linux result, and a macOS runner other than the owner's Mac is a different kernel build.

**Not covered.** The GPU lease and the broker (they are not observation). Windows: there is no backend, so
`SandboxUnavailable` is the closed state, and a harness case asserts that nothing runs there without `AllowUnsandboxed`.
Enforcement bugs in Seatbelt itself (A3). A process on the device outside every profile.

## 8. Decisions

Decided by the owner. Ranked by what is correct; effort is a fact beside each, not a rank.

**D1. What an unreadable run costs the session that ran it when the cause is not known to be its own. Decided: option 1 as
amended.**
1. Nothing per run: it goes to the device. Amended: a hider would otherwise get free, unlimited reconnaissance (the fix branch
   gives 0.2 per unreadable run: 15 runs to watch and 50 to hold in ten minutes, and until S2 a path ending in
   `x/.CFUserTextEncoding` in any directory is dropped as noise and probes unscored). So an owner with three or more unreadable
   runs in ten minutes is watched (never held), and its next runs go alone on the device with a tighter policy, so that a
   recurrence is attributable by elimination and costs it 0.2. Each such run still yields no clean result.
2. A small weight to the owner (today 0.2 or 1.0). It deters hiding and blames an innocent session whenever someone else
   caused the loss.

**D2. Decoy attribution. Decided: option 1, amended by the token scope of section 4.**
1. A token per confined loopback process, a device alarm when unattributed (S5). Effort: a decoy file per loopback policy and a
   listener that routes by token. Never the connecting process alone (a loopback pid lookup is racy), and a freeze needs the
   token and a second fact.
2. Attribution by the connecting process alone (socket owner to process tree). Effort lower; racy, so a short request can be
   unattributed or misattributed.
3. Keep quarantining every in-flight session (today). Any local request freezes all sessions; this is the weaker option.

**D3. Linux observation (row 11). Decided: option 2 now; a tripwire slice is decided later.**
1. A real channel: tripwire mounts of decoy paths watched from the device (inotify), or the kernel's audit for Landlock where
   the kernel has it. Effort: the most; a different mechanism per kernel.
2. `absent` plus a tighter policy on a device that cannot observe (no network, narrower reads); no refusal, no score, no
   sentence. Effort: small.
3. Refuse untrusted runs where nothing observes. Closed, and it adds a failure to onboarding on Linux and WSL.

**D4. Where the real tier runs in CI. Decided: option 1 with a `paths:` filter.**
1. A macOS job on a push of the development branch that touches `src/poolhouse/sandbox/**`, `src/poolhouse/sentinel/**`,
   `tests/hostile/**` or `src/poolhouse/platform*` (a `paths:` filter) running only the harness files, beside a nightly job
   (the nightly job runs the whole suite and takes about three quarters of an hour, `ci.yml`). The corpus is keyed per OS
   build. Cost: a macOS runner start per matching push; S0 measures the files.
2. The nightly `macos` job and the owner's Mac pass only. A break reaches the branch and is caught the next morning.

## 9. Review log

A non-author review of the first version said ACCEPT with edits. Each finding was checked against the code (development
`63b9912e9`, which holds `76817ce35`'s code; the fix branch `c0baaa90c` where a finding is about it) and applied. Line numbers
are development's unless marked.

| Finding | Checked | Change |
| --- | --- | --- |
| Row 18 "nothing says so" is wrong | `Store.quarantine` emits `store.overflow` at `store.py:321`; `Store.watch` (`:277`) returns None silently | row 18 splits the two paths; both emit, the chip shows it |
| Row 3 is the fix branch's regression | development `_add` answers a probe only for a line that parses; the fix branch's `_hear` matches the token anywhere in a message | row 3 says so; the fix branch lands with the one change; S0 holds the behaviour |
| Section 2.2 `_version` | `llamacpp_update.py:74` returns the first line holding "version" from the output; it never reads the exit code | its own row in 2.2 |
| Missed: `Bus.emit` | `events.py:311-319` warns on a log `OSError`, logs and swallows subscriber errors; `:308` caps `recent` at 2000 | rows 21 and 22 |
| Missed: `Store._prune` | `store.py:463-479` evicts the oldest CLEAR, WATCH and RELEASED records past 4000 and deletes CLEAR after 7 days | row 23, with the quota of M6 |
| Missed: scrub before `default()` | `sandbox/run.py:259-260` returns the unscrubbed environment while `HOOKS.scrub` is None | row 24 and row 15 |
| Missed: the stream alters targets | `denial_log.py:194` `errors="replace"`, `:78-79` `.strip()`, `:66-69` a failed decode returns None | row 25; "exact" is now true by construction and counted |
| Missed: `Score._levels` | `score.py:60,78` never pruned, while `Windows` evicts at 4096 | row 27 |
| Missed: stream notices | `denial_log.py:211-212` keeps three non-JSON lines | row 26, a hypothesis for S0 |
| H1 selection | `scripts/affected.py`: `index()` globs `tests/*.py` and `module_of` needs two path parts, so `tests/hostile/` is unmapped (run everything); `DEPTH = 1` | section 5.3: S0 changes the selector, harness files import modules by name, the wiring test names the outside consumers |
| H2 bubblewrap | it emits no refusal events, so nobody is asked to look | `absent`, tighter policy, no refusal, no sentence; S6 follows S0 and S1's flip follows S6 |
| H3 pid exoneration fails open | a `setsid` or double-forked child has ppid 1; a short-lived child is gone before the end-of-run list | row 1 clears a run only on positive evidence; family C crosses escaped child with cut length |
| H4 free reconnaissance | the fix branch gives 0.2 per unreadable run (15 to watch, 50 to hold per ten minutes); row 4's suffix is dropped as noise in any directory | D1 amended: watch at three in ten minutes, next runs alone |
| M5 unattributed decoy hit | dropping all containment would let one request defeat the decoy | device alarm taints sessions in flight; no person release |
| M6 quotas | `Store._prune` and `Windows` evict across owners; `_new` refuses at 8000 | per-origin quotas, a hard cap on watched, `store.evicted`, `store.overflow` in the chip |
| M7 token scope | `policies.py:51` bash is `Net.deny()`; project files are readable by sibling sessions | tokens for loopback policies only, in the process's own scratch; freeze on token and a second fact; owner defined |
| M8 mutants | hand-copied mutants drift | AST mutants from the source, expected-observation kills, constants in the real tier, real mutants on every change, `weakened-assertions` on `expect` and `needs`, skips fail |
| M9 lock | `tests/sandbox_kit.py` (fix branch) `kernel_log(alone)` is a temp-directory `flock`, its wait counts as test time | broker resource lease with no compute lease; selftest waits for zero runs; fault cases use their own `DenialLog` |
| M10 probe cost | `_probe` runs `sandbox-exec` and polls at 0.25 s per run | coalesced probes; S0 measures it and a real cmake build |
| M11 corpus | raw lines carry the account name and `pytest-of-<user>` | fixed basetemp, synthetic same-length user, `no-real-names` on the corpus |
| L12 | `core.py:103-110` has `POOLHOUSE_SENTINEL=off` with a reason; sandbox cannot import sentinel | R5 names the exception; dedupe by `machTimestamp` and `traceID`; arming by the callers outside both packages |

Not checked in code: the claims about the kernel marked **hypothesis** stay hypotheses until S0.
