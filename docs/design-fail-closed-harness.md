# Fail closed when unobservable, and a standing hostile harness

Design, 2026-10-19. Nothing in this document is built. Owner order: make "fails closed when unobservable" the default of the
sandbox and the sentinel, and replace per-branch hand review of them with a harness that runs in the suite. Code read at
`76817ce35` (development) and at `c0baaa90c` (branch `fix/denial-long-paths`, not landed; it is the case this design starts
from). Every statement about the code was read, not run. Statements about the macOS kernel's behaviour that no test of the
fix branch records are marked **hypothesis**: the harness settles them first (slice S0), and a hypothesis that is false
changes a row, not the design.

The design costs a person nothing in a normal journey. The one new thing they can see is a single standing sentence when a
device cannot see what its sandbox refuses (R6, section 2).

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
| **absent by design** | nobody was asked to look: a named argument or a person's grant said so, with a reason | nothing; status shows who chose and why |
| **unobservable** | the observer was asked and cannot say: log lost, report cut, report not understood, no log kept, cap hit, owner unknown | unknown: not clean and not guilty |

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
  `AllowUnsandboxed(reason)` is today. No environment variable, flag or global switch makes a run less observed.
- **R6. Observer health is a state with one plain sentence**: "this device cannot see what the sandbox refuses right now".
  It is a standing notice with a count and a cooldown, never a question; it clears itself when the observer's probes pass.

### 2.1 What the value carries

`Observation`: `state` (seen, absent, unobservable); `items` (the refusals, exact); `dropped` (past the cap), `quiet`
(start-up noise, listed, not scored), `lower_bound` (set when the kernel or the log is shown to coalesce); `cause` for
unobservable (`no_log`, `stream_down`, `restarted`, `settle_timeout`, `cut`, `unparsed`, `owner_unknown`); `attributed`
(`owner`, `device`, `unknown`); `escaped` (children that left the group); `output_complete`. A `Backend` declares what it can
observe (`stream`, `none`, later `tripwire`) and answers `denials` with this value; the `keeps_refusal_log` flag and the
detail strings of the fix branch become `cause`. `Result.check()` is `refusals()` plus a raise when there is one.

### 2.2 Consumers decide, and each one is read once

A consumer that decides trust from a run calls `refusals()` and so refuses on unobservable. One that relays output to a model
passes the state on in the result and the event. Enforcement held in both, so a relayed run is not withheld.

| Consumer | Today | Should |
|---|---|---|
| `SandboxedBash` (the agent's shell tool) | reads only a failed run; raises on refusals; a note when unreadable | reads every run; relays the state; event to the sentinel with the session as owner |
| `agent/confined.py` (MCP servers) | never reads | refusals delivered as they arrive to owner `mcp_server:<name>`; the store's existing block refuses a server that keeps probing |
| `serve/confined.py` (model servers) | reads on a failed start, as text, no event | delivered as they arrive to owner `server:port:N`; the failed-start message keeps its text |
| `serve/llamacpp_compile.py` (head build of upstream code) and `llamacpp_update.py` `_version` | exit code only | a refused or unreadable step means the new build is not switched to and the current one keeps serving; one event, owner `binary:<path of the new build>` (a store kind that exists); the person is told nothing unless asked |
| `sandbox/cli.py` `test` | `diagnose="never"`, a named argument | unchanged: it tests enforcement; the `device` behaviours of 5.1 test observation |

## 3. Where an observation can be missing

"Today" is read at `76817ce35`; "(fix)" is the fix branch. "Should" is closed unless it says why not. Slices are in section 7.

| # | Place | How it is lost | Today | Should be | Slice |
|---|---|---|---|---|---|
| 1 | Kernel cuts a report (~1010 bytes) | the tag at its end is gone | open; closed for every in-flight run (fix) | closed, scoped by the pid in the report's head: a pid confirmed outside a run leaves that run seen; an unknown pid makes every run in flight unreadable (cause: cut, unattributed) | S3 |
| 2 | Report carries our tag but does not parse (`_hear`: run found, `entry` is None) | dropped; includes a process name holding `(` (the pattern is `[^(]+\(\d+\) deny`) and any format change in a macOS update | open | closed: unreadable, cause `unparsed` | S2 |
| 3 | Readiness probe (`_hear`) | (fix) counts as answered when its token occurs in any kernel message; never checks that the report parses into process, operation, target | open: a format change passes it | the probe's refusal must parse to the process, operation and target it provokes; the result is recorded with the OS build | S2 |
| 4 | Noise filter (`NOISE.search(target)`) | `\.CFUserTextEncoding$` and `/GlobalPreferences*.plist$` are unanchored at the front, so any path with that ending is dropped; `^/Users/[^/]+$` drops a refused listing of the home directory; no count | open | the exact operation and target entries the confined start-up makes, counted as `quiet` and shown; a probe of any noise target still shows in `quiet` | S2 |
| 5 | Per-run cap (`KEEP_PER_TAG = 1000`) | later entries dropped silently; the count under-reports; the "first" refusal may not be the worst | open | seen with `dropped = n`; `dropped > 0` is a flood signal to the owner | S2 |
| 6 | Run cap (`KEEP_TAGS = 256`, ended runs trimmed) | a trimmed run reads back as unreadable | closed | closed; stays in the catalogue | S0 |
| 7 | Report arrives after the run ended (a `setsid` or double-forked child) | appended to an ended run nobody reads | open | each report is delivered to the owner recorded at `watch`, as it arrives, coalesced per second (also fixes row 8) | S2 |
| 8 | Long-lived confined processes (`agent/confined.py` MCP servers; `serve/confined.py` model servers) | refusals pile into the 1000-entry buffer; the model server reads them only when its start fails and sends no event; an MCP server is never read | open | same delivery as row 7; the owner is `mcp_server:<name>` or `server:port:N`, kinds the store already holds | S2 |
| 9 | Run that exits 0 with empty stderr (`_diagnose`) | never read; `test_a_run_that_was_not_diagnosed_does_not_claim_its_refusals_were_unreadable` fixes this in as intended. The head build of upstream's code (`serve/llamacpp_compile.py`) checks only the exit code, so a build step that is refused a read of a secret and goes on leaves no trace | open: a program that catches its refusals leaves no trace | an untrusted policy is read always (one settle per run); `never` is a named argument with a reason | S1 |
| 10 | Stream not started, silent, restarted or ended (5 s start, 5 s settle, 60 s retry hold-off) | `denials` is None | closed; but 1.0 goes to the owner for a loss that is not theirs, and every run in the 60 s hold-off is unreadable | closed; scored to the device, not a session (section 4) | S4 |
| 11 | Backend with no log (bubblewrap, container) | `unreadable` is "no refusal log" | closed per failed run, 0.2 to the owner each: 50 ordinary failing commands in 10 minutes reach the hold (10.0); a run that exits 0 is never looked at | recorded once as a device blind spot, not scored per run; consumers that decide trust refuse, or the policy is tightened | S4, S6 |
| 12 | Timeout, output cap, detached child holding the pipe (`_wait`, `_Capture`, `out.join(2)`) | `bytes(out.data)` is read while a thread may still write; `truncated` stays False | open for output | `output_complete` is false when a reader outlived the join; the pipe is closed by the runner | S3 |
| 13 | Child that left the process group (`setsid`; "known limits" in `docs/sandbox.md`) | the runner does not know it exists | open | descendants listed at the end of the run (`poolhouse.serve.process`, the owner of process-table reads), killed by pid, counted as `escaped` | S3 |
| 14 | Owner of a run (`ContextVar` of the fix) | a thread that did not copy the context (`threading.Thread`, `run_in_executor`) has no owner and counts for nobody | open | an unowned run is scored to the device subject `unowned:<policy>` with a notice; a wiring test names every `sandbox.run` and `wrapped` call site and its owner | S4 |
| 15 | No listener (`HOOKS.events` is None until `sentinel.default()` runs) | a process that never asks for the sentinel leaves sandbox runs unwatched (`docs/sentinel.md`) | open by accident | the first confined run arms the listener (the sentinel package registers its arming function with `HOOKS`, since sandbox imports nothing from it), or logs `sandbox.unwatched` once with its reason; no silent state | S4 |
| 16 | Listener raises (`_emitter`: `contextlib.suppress(Exception)`) | the event is lost, the run goes on | open | counted in `HOOKS.lost`, logged at error, in status, red chip | S4 |
| 17 | Score windows (`Windows`, 4096 keys, oldest popped) | a flood of distinct keys evicts a quiet attacker's own score, or a victim's | open | evict only subjects below the watch level; overflow is a counted device event | S4 |
| 18 | Store full (`Store._new` refuses at 8000 records, reached only with that many held; `Sentinel.handle` still returns `act`; `Score.add` has already set `held` so it never fires again) | nothing is held and nothing says so | open | `handle` returns `unrecorded`; `Score` sets `held` only after the store confirms; a full store blocks new untrusted subjects until room is made | S4 |
| 19 | Decoy listener hit (`Honey.endpoint_hit`) | every session with a tool in flight is quarantined (high confidence): one request by anyone present freezes all | open, and victim-blaming | attribution by a per-session token; unattributed names the device (section 4) | S5 |
| 20 | Identical refusals in a burst, and log volume | **hypothesis:** the kernel or `logd` coalesces or drops events under load; a neighbour's single refusal may be lost beside a flood while the settle probe still arrives | unknown | harness measures N identical refusals (1, 10, 1000) and a flood beside a neighbour; if events are lost the observation is `lower_bound`, and a per-run refusal rate limit becomes its own slice with the owner's number | S0 |

Already closed and kept: the tag is read from the last line of the message so a target with a newline cannot name another
run's tag; only events from the kernel's Sandbox extension count; a stream restarted after a run began makes it unreadable;
the running run's tag is never trimmed; a failed start ends the run it began watching.

## 4. Who is blamed (victim-blame)

**A signal is scored against a subject only when the evidence names that subject.** Evidence, strongest first: a token only
the subject holds (the run tag; a per-session decoy token); a context captured at the call site, not in the watched process
(the owner, copied into pool threads with `contextvars.copy_context`); a process-tree fact read while the process lives (pid
inside the run's descendants); never "was in flight at the time".

An owner is a kind and a key captured when a run is watched, not when its event arrives: `session:<id>`, `mcp_server:<name>`,
`server:port:N`, `caller:<key>`, `binary:<path>`, all kinds the store already holds. A signal with no evidence goes to a
**device subject**, a new store kind no hook acts on (`observer:<backend>`, `unowned:<policy>`, `decoy:endpoint`). A device subject can be watched, shows in status and the chip, tightens policy for new
runs, and makes consumers that decide trust from a run refuse. It never freezes or quarantines a session and holds nothing a
person must release.

| Signal | Today | Should |
|---|---|---|
| refusal with the run's tag | the owner (fix) | the owner; the tag proves it |
| flood (`dropped > 0`) | not seen | the owner, same proof |
| unreadable: no log, cut, lost | the owner, 0.2 or 1.0 | the device; the owner only when the pid is inside its descendants |
| run with no owner | nobody | `unowned:<policy>` |
| long-lived server's refusal | nobody | its `mcp_server` or `server` owner |
| decoy endpoint hit | every in-flight session | the session whose token was used; else `decoy:endpoint` and the device |
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
OS build differs from the last one verified. The harness is called by the suite, by that command and by the nightly job.

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
| C lifecycle | exit 0 with refusals swallowed; a child that calls `setsid` and refuses after its parent exited; double fork; a child holding stdout open; killed by timeout mid-refusal; a refusal in the last millisecond before exit | |
| D forgery | fake refusal text on stdout, stderr and through the allowed logging service in the kernel's format with another run's tag; a message carrying a live probe token | |
| E neighbours | a cut report beside an innocent run; a run under a foreign Seatbelt profile (not ours) that makes long reports; a hostile run making another run's tag collect entries | the bystander property of section 4 |
| F observer faults (injected, not by the program) | the log process stopped and killed; replaced by a program that prints garbage, another format, or nothing; the reader thread dies; the events log gets "no space"; a sealed file is damaged; the listener raises; the clock jumps | |
| G sentinel | a decoy hit with two bystanders in flight; a run with no context; the store at capacity; the window at 4096 keys; no listener installed; a long-lived server that probes for an hour of simulated time | |
| H benign controls | ordinary work scores zero: fifty failing test runs, `grep` with no match, a long allowed path, a `find /` that is refused a thousand times (one run, one score) | follows `tests/test_sentinel_benign.py` |

### 5.3 Tiers, selection and cost

| Tier | Runs | Where and when | Cost bound (targets until S0 measures) |
|---|---|---|---|
| **fast** | recorded kernel messages fed to the parser and the stream's `_hear`, and the sentinel half with fake backends (`tests/sandbox_kit.py` already has them) | everywhere, including the Linux CI shards, on every change that selects it | about 600 cases in 1 s; no sandbox, no clock sleeps |
| **real** | real `sandbox-exec` and the real `log stream`, one shared stream per module | the Seatbelt pass (`tests/seatbelt-modules.txt`: a confined process cannot apply a second profile, so these files run in the pass without the write denial); the nightly `macos` job; the person's Mac | 60 to 80 confined runs; stream start once; every cut case in one exclusive block (one `kernel_log(alone=True)` for the block, not one per test) |
| **linux** | bubblewrap contract: enforcement for every behaviour, `unobservable(no_log)` expected until S6 | `scripts/test-on-linux` on WSL | one run per behaviour |

- **Corpus.** `tests/data/kernel-reports/` holds raw log lines captured from the real tier, with the OS build. The nightly job
  captures again, drops the pid and the time, and fails on a difference, so the fast tier cannot drift from the kernel and a macOS update that changes the
  report format turns the nightly red the next morning rather than a review missing it.
- **Selection.** No new rule. `scripts/affected.py` already selects the test files that import what changed; the harness files
  import `poolhouse.sandbox` and `poolhouse.sentinel`, so a change to either selects them and a change elsewhere does not.
  A wiring test (`tests/test_wiring.py` style) fails when a module under those two packages is imported by no harness file.
- **Fixed cost.** A targets-then-measure slice: S0 measures stream start, settle and per-run cost on the owner's Mac and
  `tests/test_redteam_hostile_cost.py` asserts the measured bounds. The shape is fixed now: fixed overhead under 5 s and under a
  quarter of the wall time (AGENTS.md, "A run's fixed cost is budgeted"), the stream started once per module, and no behaviour
  that sleeps to wait for the log (it waits on `settle`, which returns when its own probe arrives).
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

`tests/hostile/guards.py` is a registry. Each guard has a name, the function to patch (`module:qualname`) and a **mutant**: a
replacement with the guard removed (`cut-makes-unreadable`, `unparsed-makes-unreadable`, `noise-is-exact`,
`drop-is-counted`, `late-is-delivered`, `diagnose-always`, `owner-from-context`, `weight-by-cause`, `eviction-keeps-watched`,
`store-full-is-reported`, `listener-error-is-counted`, `escaped-are-killed`, `cut-pid-attribution`, `decoy-token` and the
guards that exist today: `tag-from-last-line`, `sender-is-kernel`, `settle-before-read`, `generation-check`).

- **Kill rule.** For each guard, patch the mutant in (in the process, so it costs milliseconds and copies no tree), run the
  behaviours in its `kills`, and require at least one to fail. A guard no behaviour notices is a test failure that names it.
- **Over-closed mutants.** The registry also holds `everything-unreadable` and `everything-scored`. Controls and family H must
  kill them, so "always closed" cannot pass as safe.
- **Orphans.** A behaviour with no `kills` and no `control` mark fails the wiring test: a behaviour that no guard's removal
  turns red proves nothing.
- **Real mutants.** Nightly, `scripts/mutate --guards` runs each registry entry through the real mutation engine (a copy of
  the tree, the source changed) and compares the verdict with the in-process one, so an in-process mutant cannot drift from
  the source.
- **Fail first.** Every slice commit carries the behaviours that were red against its parent; the existing fail-first check
  applies unchanged.
- **The registry and catalogue cannot be quietly weakened.** The harness test files are named `test_redteam_hostile_*`, so
  `scripts/hooks/weakened-assertions` refuses a diff that removes an assertion or a test function from them without a
  `Reviewed-by-second:` or `Owner-order:` line. A behaviour deleted from the catalogue data is caught by the kill rule (a
  guard loses its last killer) and by the coverage map (its harness id stops resolving).

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
| replay | replay a recorded refusal or a probe answer into the stream; replay a late report | only events the kernel's Sandbox extension sent count (existing); probe tokens and run tags are random and never reused; late delivery is idempotent per (tag, count); family D |
| forged or below-threshold signature | edit a sealed device record; stay under the watch level by spacing signals; evict your own score (row 17) | the seal fails and the chip is red (existing tamper handling covers the new records); eviction never removes a watched subject; a patient prober under the window is accepted risk A2 |
| compromised or malicious member | a member's job floods the log or makes long reports to blind or blame local sessions | a member's runs are owned by its caller key, set by the device from the authenticated request, never from the body; its flood is scored to it; the bystander property holds (family B, E) |
| stolen phone or key | use a key to clear the degraded state or to release a device subject | no new approval path: a device subject holds nothing to release and clears itself when probes pass; releasing a held session stays a person's act |
| malicious agent | hide probes (cut, flood, detach, swallow), forge refusals, turn observation off, edit the registry | families A to F; no switch exists (a harness case runs with hostile environment variables and arguments and asserts the observation is unchanged); the harness tests are under `weakened-assertions` and the kill rule catches a deleted behaviour |
| agent compromised without knowing it | an injected instruction makes it run long-path or probing commands in good faith | a first unreadable run of its own costs it at most one weight when the pid is its own and nothing when unknown; thresholds stay 3 and 10; family H proves ordinary work scores zero |
| downgrade, secure to open | a lost stream, a failed probe or an OS update silently falls back to "no refusals"; `diagnose="never"` reached from a tool | there is no fallback path: a failed probe is unreadable, not clean; `never` and `absent` are named arguments the shell tool does not expose and `agent_may` refuses to name |
| approval fatigue | degrade the observer repeatedly so a person is asked again and again | no question exists: one standing notice, repeats fold into its count, the cooldown of `heads_up` applies, and no button changes the state |
| takeover by whoever is merely present | another process on the device makes long reports, floods the log or hits the decoy listener to get sessions frozen or blinded | presence is not evidence, so no session is scored or frozen (section 4); residual: it can keep runs unreadable while it does, accepted risk A1 |

**Accepted risks, named for the owner.** A1: a local process can make runs unreadable for as long as it emits long reports;
the cost is availability of clean results, never blame. A2: a prober that spaces refusals past the 600 s window is not scored;
every refusal stays in the event log and a per-subject daily count is shown as evidence for a person, never a trigger. A3: the
harness proves the observation, not the kernel's enforcement; enforcement bugs in a deprecated tool are out of reach. A4: until
S6, a bubblewrap device runs with a stated blind spot.

## 7. Slices

Each slice lands alone: its behaviours are red against its parent, the suite is green after it, it updates the documents it
touches and prunes `HANDOFF.md`. Order is by what is correct, not by ease; S6 waits for D3. `fix/denial-long-paths` lands first as it is.

| Slice | Delivers | First red |
|---|---|---|
| **S0 harness** | `tests/hostile/` (catalogue, programs, registry, adapter over today's `Result`), families A, B (cap, trim), C (exit 0, timeout), D, E, F (stream faults), H and controls; kill arms for the guards that exist; the four cut sizes of the fix branch move into the catalogue; the measurements of rows 20 and A'; the cost test; the corpus and its nightly diff; `sandbox/selftest.py` behind `poolhouse security sandbox test` | the exploratory grid run once on today's code: its reds become the briefs of S1 to S4; only green rows enter the suite |
| **S1 observation** | `Observation` (2.1), `Result.refusals()`, `diagnose` always for untrusted policies, the consumers of 2.2 (the shell tool relays; the head build and the version probe refuse; the model server and MCP launch hand their reading to S2) | row 9; the old `denials` attribute does not exist |
| **S2 stream** | parse strictness and a parsing readiness probe with the OS build; exact noise with `quiet`; `dropped`; delivery at arrival to the owner recorded at `watch`, coalesced per second (late reports, MCP and model servers) | rows 2, 3, 4, 5, 7, 8 |
| **S3 lifecycle** | descendants helper in `poolhouse.serve.process`; escaped children killed and counted; `output_complete`; cut attribution by pid | rows 1, 12, 13 |
| **S4 sentinel** | owner `(kind, key)` at `watch`; `unowned:<policy>`; device subject and observer state; weights by cause (the table of section 4); chip sentence; first confined run arms the listener; `HOOKS.lost`; `Windows` eviction; `Score` and `Store` agree; full store blocks new untrusted subjects | rows 10, 11, 14, 15, 16, 17, 18; the bystander property |
| **S5 decoy** | a decoy address per session, the token in the decoy file that session's policy can read; a hit names its owner; unattributed names the device and replaces the decoy; attack-first pass by a non-author before it is built | row 19 |
| **S7 coverage** | the `lossy` kind in `scripts/redteam_coverage.py`, harness and guard references, `scripts/mutate --guards`, a test that every module of the two packages is reached by a harness file | a lossy site with no row fails `--check` |
| **S6 Linux** | by decision D3 | row 11 on bubblewrap |

Activation for every slice: the next `poolhouse runtime ensure` build, then the real tier on the owner's Mac and the linux
tier on WSL, repeating the original failure (a refusal of a path of each length, a flood, a detached child) as a person
would run it through the shell tool. A green fast tier, a green real tier and the kill rule are the evidence a slice reports;
a Mac result is not a Linux result, and a macOS runner other than the owner's Mac is a different kernel build.

**Not covered.** The GPU lease and the broker (they are not observation). Windows: there is no backend, so
`SandboxUnavailable` is the closed state, and a harness case asserts that nothing runs there without `AllowUnsandboxed`.
Enforcement bugs in Seatbelt itself (A3). A process on the device outside every profile.

## 8. Decisions for the owner

Ranked by what is correct; effort is a fact beside each, not a rank.

**D1. What an unreadable run costs the session that ran it when the cause is not known to be its own.**
1. Nothing: it goes to the device (the design). A hostile run can hide probes in its own unreadable runs, at the price that
   each such run yields no clean result.
2. A small weight to the owner (today 0.2 or 1.0). It deters hiding and blames an innocent session whenever someone else
   caused the loss.

**D2. Decoy attribution.**
1. A decoy address per session, a device alarm when unattributed (S5). Effort: a decoy file per session policy and a
   listener that routes by token.
2. Attribution by the connecting process alone (socket owner to process tree). Effort lower; loopback pid lookup is racy, so
   a short request can be unattributed.
3. Keep quarantining every in-flight session (today). Any local request freezes all sessions; this is the weaker option.

**D3. Linux observation (row 11).**
1. A real channel: tripwire mounts of decoy paths watched from the device (inotify), or the kernel's audit for Landlock where
   the kernel has it. Effort: the most; a different mechanism per kernel.
2. A recorded blind spot with a tighter policy on a device that cannot observe (no network, narrower reads). Effort: small.
3. Refuse untrusted runs where nothing observes. Closed, and it adds a failure to onboarding on Linux and WSL.

**D4. Where the real tier runs in CI.**
1. A macOS job on every push of the development branch that runs only the harness files, beside the nightly job (the
   nightly job runs the whole suite and takes about three quarters of an hour, `ci.yml`). Cost: a macOS runner start per
   push; S0 measures the files.
2. The nightly `macos` job and the owner's Mac pass only. A break reaches the branch and is caught the next morning.
