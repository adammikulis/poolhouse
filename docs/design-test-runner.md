# A test runner and sharder that any project uses

Status: design. Nothing here is built. Claims carry a tag: **[code]** read in this tree (`0.3dev` at `ab63ced7b`), **[doc]** stated in
a document of this repository, **[report]** reported by workers and not re-measured here, **[plan]** a proposal. The owner ordered it: one general runner and sharder, a feature of the
Poolhouse library, that any repository that installs Poolhouse uses for its own tests; Poolhouse's own repository is the first project
that uses it, through the same public interface and nothing private. `docs/design-test-selection.md` is the design of the selection
and evidence rules for Poolhouse's tests; this document generalises its mechanisms (sections 4, 5 and the shard description of 6.1 to
6.5 become the analyzers, the soundness rules and the pool transport below) and replaces its build plan.

## 0. The dependency direction, and the whole design in a page

**The runner is a provider and every repository is a consumer.** A repository describes itself to the runner through one file in that
repository, `testfarm.toml`, and through nothing else: a command that **lists** test ids, a command template that **runs** a subset of
ids and says which passed, and, optionally, which files each id depends on. The runner knows nothing about any consumer: no name, no
path, no layout, no marker, no default for any repository, Poolhouse's included. Poolhouse's own `testfarm.toml` is an ordinary project
file, loaded exactly as another repository's is. Everything that varies between projects is data in that file; everything the runner
does is the same for every file.

- **The contract** (section 1) has three parts. LIST prints ids. RUN executes a subset of ids and signals pass or fail for each
  (exit code, optionally a result file). DEPS (optional) says which files an id depends on. A project that supplies only the first two
  gets parallel local runs, sharding over devices and result reuse for an identical tree; DEPS adds reuse across edits and affected-test
  selection. Labels a project attaches to ids carry per-id needs (a lease, a serial group, a platform, never-leaves-this-device).
  Environment and build steps are commands the project declares, run once per device and cached by an input key.
- **Presets** (section 7) are ready-made LIST and RUN commands for a *tool* (pytest, cargo, npm, a directory of executable files). A
  preset is a TOML table the core reads like the project's own, and a project can write its own in a few lines. Presets live outside the
  core and import only it.
- **The core** (section 5) plans, shards, schedules, merges, reports and sets one exit status; it holds the reuse store, the
  admission of compute slots, the jobs, the dependency sets for selection, and fail-first.
- **Transports** (section 6) take a shard to somewhere it runs: this machine's processes, a device of a Poolhouse pool over the pool's
  mutual-TLS routes, later ssh. The pool is one transport plugin. There is one sharding path and one wire protocol; what exists today as
  a node carrier, a pool carrier in dev mode, `--split` and a CI splitter is folded into it (section 2, section 14).
- **The surface** (section 3) is `ph.test` in Python, the `poolhouse-test` command (also `poolhouse test`), and the `testfarm.toml`
  schema. Everything else is internal. `pip install poolhouse` gives a repository all three.
- **Poolhouse's repository** keeps its tiers, markers, node build, gates, agent rules and confinement in its own `testfarm.toml` and
  its own scripts. `scripts/test` becomes a few lines that call the public command (section 8). What only matters for developing
  Poolhouse never reaches a repository that imports it (section 3.3).
- **Time is accounted, not mixed.** A unit's timeout counts from the moment it starts running; queue wait and build time are reported
  apart (`queued`, `built`, `ran`), and a unit that never started because the machine was overloaded is `not started`, not failed
  (section 5.10).
- **Security** (section 10): the runner runs code on other devices, so identity is the pool's, the receiver's policy decides what runs,
  every tree and result is checked against a hash the sender computed, text from a device is data, and every bound has a source. The
  attack list for the later break-it pass is section 10.8.

## 1. The contract

A project supplies three things and the runner needs no more.

1. **LIST**: a command that prints the test ids, one per line on stdout (`lines`), or one JSON object per line (`jsonl`) with the
   fields `id` and, optionally, `labels`, `deps`, `def` and `seconds`. An id is opaque text: 1 to `PATH_MAX` bytes, no control
   characters, not beginning with `-` (section 10.4). A line that does not match `list.match` (a regular expression, default every
   non-empty line) is ignored, so a tool's banner or summary is filtered by the project, not by the runner.
2. **RUN**: an argument vector with the placeholders `{ids}` (the ids as separate arguments), `{idfile}` (a file of ids, one per line,
   for more ids than the platform's `ARG_MAX`), `{result}` (a file the command may write) and `{extra}` (arguments a named set adds). It
   is an array, never a shell string; a project that wants a shell writes `["sh", "-c", "..."]` itself. The command signals per id by
   its **results** format: `exit` (the exit code of an invocation applies to every id in it; the default, and with it a batch is one
   id), `jsonl` (one `{"id", "status": "pass"|"fail"|"skip", "seconds", "message", "reads"}` per line in `{result}`) or `junit`
   (a junit file in `{result}`; ids are matched through `run.junit_id`, a template over the `classname` and `name` attributes). An id
   that a result file does not mention is **not reported** and counts as failed, loudly; nothing is assumed to have passed.
3. **DEPS** (optional): which files an id depends on, given as globs per id pattern (`deps.globs`), or by a command that prints
   `jsonl` rows `{"id", "deps": [...]}` (`deps.command`), or in LIST's rows. No DEPS means an id depends on the whole tree: it is
   reused only for an identical tree and is always affected, and the report says so once. DEPS is a statement about files; the runner
   never reads a language.

Two optional words complete the model. A **group** rule (`list.group`, a regular expression with one capture, or a separator) names the
scheduling and reuse granule: ids with the same group always run together, are keyed together and are listed as one **unit** (a
pytest test file, a cargo test target); with no rule every id is its own unit. A **label** is a word on a unit that the project's
`[label.NAME]` table gives a meaning (section 4.3): a lease, a serial group, a required platform, "never leaves this device", "never
reused".

A neutral project, `widgets`, with tests written as shell scripts (no preset):

```toml
schema = 1

[list]
run = ["sh", "tests/list.sh"]            # prints: tests/t_parse.sh  tests/t_render.sh  ...

[run]
argv = ["sh", "{ids}"]                   # results = "exit", one id per invocation
```

The same project with a tool preset and a dependency statement:

```toml
schema = 1
preset = "pytest"                        # section 7: a LIST and a RUN for pytest, written in this same schema

[deps]
globs = { "tests/**" = ["src/**", "tests/**", "pyproject.toml"] }
```

A project with a build step and a GPU-needing label, again neutral:

```toml
schema = 1
preset = "cargo"

[[step]]
name = "build"
run = ["cargo", "test", "--no-run", "--locked"]
inputs = ["Cargo.toml", "Cargo.lock", "src/**", "tests/**"]
tools = [["rustc", "--version", "--verbose"]]

[label.gpu]
lease = ["gpu"]
```

## 2. What exists today (the parallel paths this design folds)

[code] unless tagged. The same job, "run these tests somewhere, in parallel, and keep what passed", is written several times.

| Piece | Where | What it does | Becomes |
|---|---|---|---|
| Tier runner and jobs | `scripts/test` (735 lines), `testjobs.py`, `testqueue.py`, `testclass.py`, `testprelude.py`, `testphases.py`, `testorder.py`, `testhistory.py`, `testdurations.py`, `testtime.py` | tiers, background jobs with followers, estimates, classes, phase line, duration history | core: run, jobs, schedule, history, report |
| Admission | `testslots.py`, `testslots_policy.py`, `testslots_rpc.py`, `testslots_runner.py`, `testslots_pytest.py`, `testqueue.py` | machine-wide CPU permits, shortest estimate first, backfill, aging, per-test permits through a pytest plugin | core: admission; the per-test permit is a pytest preset helper |
| Reuse | `testreuse_{key,store,run,plugin,facts,imports,narrow}.py`, `testselectors.py` | per-file key and manifest, chain-hashed store, single flight, canary | core: reuse; the Python import closure and the recorder plugin are pytest preset helpers |
| Gate steps | `gaterun.py`, `gatescope.py` | a command step reused while the tree it reads is unchanged | core: steps and command units |
| Affected | `affected.py`, `affected_rules.py` | import graph plus string rules written for Poolhouse's tests, testmon | core: selection over dependency sets; `python-imports` analyzer in the pytest preset; the string rules become globs in Poolhouse's config; testmon deleted |
| Rust builds | `testnode.py`, `testunits.py`, `testartifacts.py`, `tests/native_cargo.py`, `testapp.py` | one cargo call builds the targets before the timer, shared target folder, pruning | core: steps; cargo preset helpers; the node binary stays Poolhouse's step |
| Node carrier | `app/poolhouse-node/src/shard/*.rs` (1,271 lines with `tests/shard.rs`), `testfarm/client.py` (`Shards`), `testfarm/consent.py`, grants `shard_call` and `shard_consent` | send a tree to a device of the node's pool, per-device allow list, leases | one wire and one receiver (section 6); open question 1 |
| Pool carrier in dev mode | `fleet/shard_{routes,host,store,spec,tree,run,exec,result,split}.py` (9 files, 974 lines), `testfarm/dev.py`, `Member.shard_*` | `/shards/v1/*` over the pool's pinned TLS, executor, limits | the pool transport and the executor (section 6) |
| Farm and `--on` | `testfarm/farm.py`, `scripts/test_on.py`, `testfarm/ledger.py`, `testfarm/report.py`, `poolhouse-test-devices`, `poolhouse.api.remote_tests` (`ph.test`) | merges the two carriers, reuse ledger apart from the store, per-device report | the planner, the store, the report, `poolhouse-test devices`, `ph.test` |
| `--split` | `fleet/shard_split.py`, `test_on.divide` | duration-balanced division, a person's option | the planner, always on when targets exist |
| CI shards | `scripts/ci_shards.py`, `tests/ci-durations.json`, `.github/workflows/ci.yml` | the same division for eight CI jobs | `poolhouse-test plan --shards N --index I` |
| Fail-first | `.claude/agents/branch-worker.md`, `branch-finisher.md`, `docs/agent-policy.md` | a line in a report; "no hook checks the line, so the reviewer does" | core: `failfirst` with evidence and a check |

Facts that shape the plan: the two carriers differ only in how five operations (capabilities, upload, start, state, cancel) reach a
device (the node's member operations, or the daemon's HTTPS routes), and they run the same Python executor; `shard_run` builds the one
command `scripts/test TIER --no-reuse FILES` and so the receiver can run only Poolhouse's runner; the remote-pass ledger
(`remote-results.json`) is separate from the reuse store; the executor runs `scripts/test` from the sent tree inside no confinement of
its own (`testwritedeny.py` is allow-default, lives in the tree and is switched off by `DEV_TEST_WRITE_DENY=0`, `docs/design-test-selection.md`
section 12 item 6). The fail-first rule is in the policy and the agents' definitions and is checked by no code.

## 3. Layers and the public surface

### 3.1 The diagram

Arrows point from importer to imported, and only downward. The layering test (section 13, check A) reads every import in the package.

```
 L5  a project's own files (any repository, Poolhouse's included)        NOT in the package
       testfarm.toml     the project's scripts      the project's CI workflow
       -- describes itself through LIST, RUN, DEPS, labels, steps; imports nothing of the runner except through L4 --
 ----------------------------------------------------------------------------------------------------------------
 L4  public surface            testfarm/api.py (ph.test)   testfarm/cli.py (poolhouse-test)   testfarm.toml schema 1
       composes L3 and L2; testfarm/names.py, the table that loads a preset, a transport or an event sink by name
 ----------------------------------------------------------------------------------------------------------------
 L3  plugins (each imports L2 and L1 only; none imports another)
       testfarm/presets/      tool presets: pytest, cargo, npm, files   (TOML tables, plus the helper programs a table runs)
       testfarm/transports/   local, pool (client and server), later ssh
       poolhouse.workspace    board event sink, task evidence, owner identity   (named in the project's file; reads the store)
 ----------------------------------------------------------------------------------------------------------------
 L2  testfarm core                                           imports L1 and the L0 list
       select (affected + reuse keys)  plan  shard  schedule  merge  report  exit status
       reuse store + canary   admission (slots, leases)   steps   jobs   history   failfirst   executor + confinement
 ----------------------------------------------------------------------------------------------------------------
 L1  testfarm model                                          standard library only
       config schema, parser and validator; Unit, Label, Plan, Shard, Result, Report; id grammar; exit codes
 ----------------------------------------------------------------------------------------------------------------
 L0  poolhouse core-layer modules the runner may import (an explicit list, pinned by the layering test):
       files, lock, platform, redact, sandbox, errors, log, home, credentials.child_environment, activity.reuse (state root)
```

Rules, each a test:

- The core (L2) imports no preset, no transport and no plugin; it reaches them by name through the L4 table of names. L1 imports nothing
  of Poolhouse.
- A preset imports only L2 and L1 (and the helper programs a preset runs are Python modules under `testfarm/presets/<tool>/` with the
  same rule). A transport imports L2 and L1, plus the pool client modules the pool transport talks through (`fleet.remote`,
  `fleet.discovery`, `fleet.membership`); nothing under `testfarm` imports `scripts/`, `tests/`, `poolhouse.board`, `poolhouse.serve`
  or `poolhouse.workspace`.
- `poolhouse.workspace` imports `testfarm.store` (to read evidence) and is loaded as a sink by name; `testfarm` never imports `workspace`.
- The daemon mounts the pool transport's server routes by name through `poolhouse.optional` (`tests/test_layers.py` prescribes that
  reach for upward edges); `fleet/shard_*.py` and their `KNOWN` entries disappear.
- Importing `poolhouse.testfarm` loads no `fleet`, `board`, `numpy` or `pytest`: a test pins it, as
  `test_a_client_does_not_load_the_fleet` does, and `tests/test_runner_overhead.py` bounds the CPU seconds of the command's start
  (AGENTS.md section 5: fixed overhead above 5 seconds or a quarter of the wall time is a defect).
- `testfarm` stays in the `tools` layer of `tests/test_layers.py` and is in the wheel (`testing` is the package that leaves it).

### 3.2 Public and internal

| | Public (documented in `docs/api.md` and `docs/commands.md`, pinned in `tests/api-signatures.json`) | Internal |
|---|---|---|
| Python | `ph.test`: `config(root)`, `units(root, select)`, `plan(root, on, select, cover)`, `run(select, *, root, on, reuse, timeout_s, cover)`, `affected(root, base)`, `devices()`, `failfirst(root, base)`; the frozen dataclasses `Config`, `Unit`, `Plan`, `Shard`, `Device`, `Result`, `Failure`; the protocol `Transport` and `Target` | every other name under `poolhouse.testfarm` |
| Command | `poolhouse-test init, check, list, plan, run, affected, devices, submit, status, wait, result, cancel, allow, deny, policy, reuse, failfirst, record` | the helper programs presets run; the receiver's server entry |
| File | `testfarm.toml`, `schema = 1`; the preset tables; the result `jsonl` and the LIST `jsonl` row shapes | the store layout, the wire routes, the state directory |
| Wire | none: the routes are versioned and checked by a handshake (section 6.4) but carry no promise to a third party | all |

Poolhouse's own repository uses the public column only. The dogfooding rule is a test (section 13, check E): nothing in `scripts/`,
`tests/` (other than the runner's own tests and the named integrations), `.github/` or `docs/verify_release.py` imports a name
under `poolhouse.testfarm` that `ph.test` does not export, and no script reads the store's files.

`ph.test` exists today (`poolhouse.api.remote_tests`: `devices()`, `run(tier, *, on, root, files, timeout_s)`, `Device`, `Result`).
The new `run` takes a selection (a set name such as `@fast`, ids, or globs) in place of `tier`, `on` takes `auto`, `local`, `pool`, a
device name or a fingerprint, and `Result` gains `reused`, `not_run` and `devices`. Before 1.0 a minor release may change the surface
(`docs/api.md`, "Versions"), and the change is a deliberate edit of `tests/api-signatures.json`.

### 3.3 Development rules stay in development

Nothing that matters only for developing Poolhouse is in the library. Each item below is today in `scripts/` and is in Poolhouse's own
`testfarm.toml` or scripts afterwards; the library has no code for it and no default.

| Poolhouse development rule | Where it lives afterwards |
|---|---|
| An agent may not run the whole suite (`testscope.py`, the `CLAUDECODE` and `CODEX_*` markers) | a `before_plan` hook script in Poolhouse's `scripts/` named in its `[hooks]`; the runner reads no agent marker |
| The real state root is write-denied, the Seatbelt confinement kernel, the container run | a `launcher` Poolhouse's config names for its units (section 4.7); the runner only wraps the argument vector |
| The node binary and the native app tests are built before the run, with Poolhouse's member list and fixtures | `[[step]]` tables whose commands are `scripts/testnode.py build` and its siblings |
| `heavy-modules.txt`, `seatbelt-modules.txt`, the redteam, `live_api`, `live_net`, `real_model` markers | `[label.NAME]` tables and the pytest preset's marker-to-label rule |
| The budget, red-team coverage, command reference and layer checks (the gate) | command units (section 5.7) in a set named `gate` |
| The ratchet on the full tier's time, `scripts/test heavy` | scripts that read `ph.test` results and the public history, not part of the library |
| The queue doctor, reaping the node processes Poolhouse's tests start, the board events of a canary mismatch | `[hooks]` and the `workspace` sink named in `[events]` |
| Worktrees, claims, the landing steps, hooks that refuse commits | not the runner's business at all |

A check (section 13, check F) runs the same generated project with and without every Poolhouse development marker in the environment
(`CLAUDECODE`, `AI_AGENT`, `CODEX_THREAD_ID`, `POOLHOUSE_AGENT`, `POOLHOUSE_NONINTERACTIVE`) and requires the identical plan and result.

### 3.4 Words

The runner uses the glossary's words (`docs/glossary.md`): a **device** runs a shard, the **pool** is the set of devices, a **member** is a
device in it, a **lease** is permission to use a limited resource, **capacity** is what a device offers, and **node** appears only as the
software `poolhouse-node`. It uses no word that `docs/glossary.md` bans. The words this design adds are **unit** (the
scheduling and reuse granule), **id**, **shard** (the part of a run sent to one target), **target** (a place a shard can run),
**transport**, **preset**, **step**, **set** (a named selection, where the old tiers were), **label**, **DEPS** and **analyzer**. The
package keeps its name `testfarm`; `farm` is not a glossary word and is not banned. `tests/test_glossary.py` covers this document and
the code the way it covers the rest.

## 4. The config schema

One file, `testfarm.toml`, at the project root, parsed with `tomllib`, strictly: an unknown key is an error that names the key and the
nearest known one, a wrong type is an error, and nothing is defaulted from the project's identity. `schema = 1` is required. Paths in
the file are relative to it and cannot leave its directory. `poolhouse-test check` validates it, runs LIST, and prints the units, the
labels, how many units have DEPS, and which steps would run.

### 4.1 Reference

| Table and key | Meaning | Default and its source |
|---|---|---|
| `schema` | the schema version | required |
| `preset` | a tool preset to start from (section 7); any key below overrides it | none |
| `[project] name` | a label in reports only | the directory name; never an identity |
| `[tree] exclude` | globs left out of the tree sent to a device | none; the tree is the tracked and untracked-not-ignored files (`git ls-files -co --exclude-standard`), no `.git`, no hidden top-level entries, no links: `shard_tree.tracked_paths` today |
| `[list] run` | the LIST argument vector | none: without it the run is split by slice or not at all (5.2, "Three split modes") |
| `[list] format`, `match`, `group` | `lines` or `jsonl`; id filter; the group rule | `lines`; every non-empty line; each id is a unit |
| `[run] argv` | the RUN template | required unless the preset gives it; with LIST it receives ids, without LIST it runs the whole selection once |
| `[run] slice` | the RUN-slice template for a tool that cannot list its tests but can run a share of them (`--shard {slice_index}/{slice_count}`); an array of strings with exactly one `{slice_index}` and one `{slice_count}` | none; read only when there is no `[list]` (5.2) |
| `[run] slice_base`, `slice_count`, `slices_per_target`, `slice_results` | whether the tool counts slices from 0 or 1; the number of slices, or `auto`; for `auto`, slices per target; what a slice reports (`exit`, `jsonl` or `junit`, as for `results`) | `1` (a tool's own first index is the project's to say, 1 is the common reading of "slice 1 of N"); `auto`; `1` (one slice per target is the least that uses every target); `exit` |
| `[run] results`, `junit_id`, `batch` | `exit`, `jsonl` or `junit`; how junit rows map to ids; ids per invocation | `exit`; `1` for `exit`, all of a unit's ids otherwise |
| `[run] timeout` | seconds one invocation may run, from the moment it starts | none locally; on a device, the shard's deadline (5.10, 10.6) |
| `[run] env` | variables set for every invocation (values travel in the file) | none; the child environment is an allowlist (10.5) |
| `[run] launcher` | an argument vector put in front of RUN (a confinement, a container) | none; local only (10.2) |
| `[deps] globs` | a table `id-glob = [file-globs]` | none: the whole tree |
| `[deps] command`, `self` | an analyzer command printing DEPS rows; the files that are the analyzer itself | none; `self` always joins `everything` |
| `[deps] everything` | a change to any of these globs selects every unit and changes every key | `testfarm.toml` itself, always |
| `[[step]] name, run, inputs, tools, env, slots, timeout, keep` | a build or environment command (4.4) | `slots` and `timeout` below |
| `[label.NAME] match, lease, serial, platform, local, reuse, slots, estimate, timeout` | what a label means (4.3); `match` globs ids that carry it | see 4.3 |
| `[set.NAME] labels, not_labels, ids, extra, reuse` | a named selection: `poolhouse-test run @NAME` (4.5) | none |
| `[reuse] key_env, key_tools, canary` | extra key inputs; the sampling rate | none; none; none (off; the project's file sets it) |
| `[remote] default, accept` | `auto`, `local` or `pool`; which labels may leave this machine | `auto` (section 6.1); every label except `local` |
| `[failfirst] tests, fail_line, exempt_line, mode` | section 5.9 | `report`; the lines of `docs/agent-policy.md` |
| `[schedule] seed, unrecorded_max, silence_margin, total_margin, queue_wait` | a committed durations file used until history exists (CI); seconds assumed for a unit with no record; the margins of the hang guards (5.10); the queue limit | none; the median of the recorded ones; none; none (required unless every unit and step has a `timeout`: the run is refused with status 4, 5.10); none; none |
| `[requires]` | things a device must have, checked by probes the device owns (6.5) | none |
| `[[unit]]` | a command unit with no LIST (5.7) | none |
| `[preset.NAME]` | a preset defined in the project (7) | none |
| `[list] glob` | the `files` preset's glob | none |
| `[hooks] before_plan, before_run, after_run` | commands run on this machine only, never on a device | none |
| `[events] sinks` | where canary mismatches and run endings go | `stderr`; a sink such as `board` is named here |

Numbers the runner supplies where the file is silent, with their sources, are in section 15.

### 4.2 Units, ids and groups

LIST yields ids; the group rule folds them into units. A unit has: its ids, its labels, its DEPS, its recorded durations, and its key
(5.3). Scheduling, reuse and the report work on units; RUN receives ids. A selector on the command line is an exact id, a glob over
ids, or the name of a unit; `scripts/test all tests/test_x.py` is the last, under the pytest preset's group rule.

### 4.3 Labels: per-unit needs, mapped generically

A label is a word on a unit. Its table maps it to the runner's generic machinery:

| Key | Effect |
|---|---|
| `lease = ["gpu", "memory:8192", ...]` | the unit holds these resources for its invocation, through the admission interface (5.5); the kinds are the lease kinds of `ph.leases` (`cpu_slots`, `memory_mb`, `gpu`, `model_slot`, `claim`) |
| `serial = "NAME"` | at most one unit with this serial name runs at a time on a device (a long serial group, a benchmark) |
| `platform = ["macos"]` | the unit goes only to a target whose platform tags include these |
| `local = true` | the unit never leaves the sender |
| `reuse = false` | a pass is not stored (a unit that reaches a live service) |
| `slots = N` | the unit holds N CPU slots (default 1) |
| `timeout = S` | seconds the unit may run, counted from the moment it starts (5.10); the largest of a unit's labels applies |
| `estimate = S` | seconds used for planning until a duration is recorded |

Labels come from LIST rows (`labels`), from `match` globs, or from the preset's rules (pytest markers become labels). The project
decides what `gpu` or `model` means; the runner knows the *kinds of resource* a lease can name and nothing about what a project calls
them. Open question 4 is how a model-needing unit uses the compute lease through the runner.

### 4.4 Steps: environment and build commands

A step is a command the project declares to prepare a device before any unit runs: a virtual environment, an installation, a compile.

- **Once per device, cached by a key.** The key is a hash over the step's `inputs` (file contents), the output of each `tools` command
  (versions), the step's `run` and `env`, and the platform class. The step runs when its key has no completed entry on that device and is
  skipped otherwise. A unit lists the steps it needs (`needs`, default all).
- **Outside every timer.** A step runs before the run's clock starts, on its own deadline, and is not counted in any unit's duration
  or timeout. Its time is a line of the report (`steps: build 41 s`) and is part of the fixed cost the planner measures.
- **Its own directories.** The step receives `{cache}`, a directory keyed by the step's key and removed when idle, and `{shared}`, a
  directory that persists across keys for the same project and sender (a compiler's target folder). Both are outside the checkout and
  outside the tree a device receives; later commands read them as `{step:NAME}` (the cache) and `{shared:NAME}`.
- **Admitted.** A step takes the `slots` its table names, else the slots the admission interface has free when the step starts (a
  measurement, not a fraction of the capacity), before it starts and reports `CARGO_BUILD_JOBS`-style parallelism through `{jobs}`.
- **Pruned.** `keep` (the project's setting, no runner default) removes keyed directories not used for that long; with no `keep`, nothing
  is removed by age and the least recently used keyed directories are removed when the volume's free disk is needed (10.6). A step may
  name `prune`, a command run when a run starts and the free disk is short, for what the runner cannot see inside `{shared}`; there is
  no interval.
- **Sender-scoped on a device.** On a device that receives a shard, `{cache}` and `{shared}` are keyed by the sender's fingerprint
  as well, so one sender cannot leave a product that another sender's step key then finds (10.8, A12).

The cargo handling that has landed is this mechanism with the cargo preset's helpers, shown in section 9.

### 4.5 Sets: the former tiers

`[set.NAME]` selects units by label and adds arguments: Poolhouse's `fast` is "every unit without the labels `slow` and `heavy`",
`all` adds `extra = ["--slow"]`. `extra` fills `{extra}` and joins the reuse key. `poolhouse-test run @fast tests/test_x.py` runs the
selected units that match the selector. A project with no sets runs `@all`, every unit.

### 4.6 Hooks

`before_plan`, `before_run` and `after_run` are argument vectors run on the sender only; a nonzero `before_plan` ends the run with
status 4 and its output (an agent policy, a queue check). They are never sent to a device and are not part of any key. The hook table is
the only place a project runs code the runner did not derive from LIST, RUN or a step.

### 4.7 Launchers

A launcher is an argument vector placed before RUN on this machine (Poolhouse's Seatbelt kernel, a container wrapper). It is applied by
the local transport only. A device that receives a shard ignores any launcher in the file and wraps the run in its own confinement
(10.2), so a project cannot ask a device to run it unwrapped.

## 5. The core

### 5.1 The run, start to end

```
config → LIST → units → select (set, selector, affected) → reuse lookup → plan → shard → schedule → execute → merge → report → status
```

1. Load and validate the config; run `before_plan`. 2. Run LIST on this machine; build units, labels, DEPS. 3. Select: a set, ids or
globs, optionally the affected units. 4. Look every selected unit up in the store (5.3); a hit is not run. 5. Plan the rest (5.2).
6. Run the steps each target needs, outside the clock. 7. Execute each shard on its transport; output arrives as it is written,
prefixed by the device. 8. Merge the per-id results in unit order, store passes (5.3), re-run a sample as canaries. 9. Print the report
and return one status (11).

### 5.2 Plan, shard, schedule

The planner is a pure function of: the units to run, a durations table, and a list of `Target`s (`name`, `capacity`, `platform class`,
`tags`, `fixed_cost_s`). It is deterministic: units sorted by (estimated seconds descending, id), each given to the target with the
least finish time, ties broken by target name. The same inputs give the same plan on every machine, and the plan prints a digest of its
inputs.

- **Local with no pool.** The one target is this machine, with capacity from the admission broker (5.5). The shard is run by
  `workers = clamp(sqrt(estimated seconds / start-up seconds), 1, capacity)` processes pulling batches from the shard's queue. The
  rule is the minimum of `start-up * workers + work / workers`, and the start-up seconds are the project's own, measured: the median
  of the recorded `setup` seconds of earlier workers in the history (5.8); with none recorded the run takes the whole capacity.
- **A device takes a shard only when it finishes sooner.** A target's `fixed_cost_s` is measured from earlier shards on it (upload,
  unpack, tree hash, steps, start); with none recorded it is the measured local cost of the same phases. A unit goes to the target that
  finishes it first including that cost, so a short run stays local and a long run spreads, with no constant of the runner's own.
- **Platform classes.** A target's class is its `(system, machine)` and WSL-ness. By default the plan splits units among the targets of
  the sender's class (speed). `--cover` also runs the selection once on each other class, split among that class's targets (coverage),
  so `--on all` of today and `--split` of today are one planner with one switch. A pass is per class (5.3); a pass on another class
  replaces nothing (decision 4 of `docs/design-test-selection.md`).
- **Units that do not move.** `local` labels, labels whose `platform` no target satisfies, and units that hold a lease no target
  offers stay on the sender; the plan says why for each.
- **A unit longer than the ideal shard** is split by id automatically, from the recorded durations, in every project; there is no
  opt-in and no test file is rewritten into smaller ones. Each shard pays the unit's module and session setup once, and the planner
  counts that cost (measured from the history of runs of one id against the whole unit, never guessed) when it decides whether a
  split pays off. A split unit stores no per-unit pass (5.3). Order-dependence between the tests of a split unit is detected, not
  worked around: the planner runs the shards in an order different from the unit's own, and a test that passes in the unit's
  order and fails in another is reported as a defect in the test. A unit whose LIST gives no ids cannot be split, and the plan
  says so.
- **Three split modes, chosen by the config and stated by the plan.** (1) *By name*: the project gives LIST, the ids are known, and the
  planner splits them into shards from the recorded durations (the rules above). (2) *By slice*: the project gives no LIST but gives
  `[run] slice`, a RUN-slice template with an index and a count placeholder (`pytest --shard {slice_index}/{slice_count}`, `cargo
  nextest --partition count:{slice_index}/{slice_count}`); the runner makes one unit per slice, `slice 1/8` to `slice 8/8`, and spreads
  the slices over the targets as it spreads units, one slice a unit that takes one slot or the slots its labels say. (3) *Whole*: the
  project gives neither, the selection is one unit that runs once on one target, and the plan says `not split: the project gives no
  LIST and no slice template`. The plan names the mode on its first line (`split: by name`, `split: by slice, 8 slices`, `split:
  none`), so no run is split in a way the person did not see. A project with LIST never uses its slice template (names are
  strictly better information); the template is not a fallback for a failed LIST, which is an error.
  - **The count comes from the plan and has no fixed maximum.** `slice_count` is `auto` by default: the number of targets the plan may
    use times `[run] slices_per_target`, the project's setting. A project that sets an integer gets that integer. The count is a
    positive decimal integer (at least 1) and nothing else is checked by the runner: the tool alone knows which counts it accepts, so
    a count the tool refuses shows as its slices failing with the tool's own message (status 1, section 11), never a count the runner
    guessed was too large. The count is fixed before anything runs and printed; it is one number for the whole run, because the
    tool's own partition is only a partition for one count. `--slices N` (a positive integer, otherwise status 4) overrides it for one
    run and joins the unit key.
  - **The balance is rough, and the plan says so.** The tool splits by count of tests (or by its own rule), not by duration, and the
    runner cannot see inside a slice: a slice of a few slow tests finishes long after the others. Each slice is scheduled with the
    mean of its own recorded durations (history is kept per `slice i/n` for one count), or `unrecorded_max` with none, and the plan
    prints `balance: rough, slices are even by count, not by duration` with the longest and shortest recorded slice once any exist.
    A slice is never rebalanced, retried elsewhere or split further by the runner.
  - **Results merge per slice.** A slice reports what the project's `slice_results` says. With `exit` (the usual case: the tool
    reports only pass or fail for its share) the slice is one result, `slice 3/8: passed` or `failed`, with the tool's output tail;
    the run lists slices, not tests, and says `tests in a failed slice are not named`. With `jsonl` or `junit` the rows are real ids,
    merged as in 6.4, and the runner checks that no id appears in two slices (duplicates are an error naming both slices). The run
    passes when every slice passed, the report is ordered by slice number, and a run's selection is the same as the tool's whole
    selection only because the slice template carries the project's own filters in `[run] argv`'s shared arguments; the runner does not
    check that the slices cover the suite, and the plan states `coverage is the tool's partition, not checked`.
  - **Reuse is per slice and keyed on the slice count.** A slice unit's key is the key of 5.3 over the slice template with its
    placeholders filled (so the index and the count are in it), over the tree or the DEPS the project gives (a project without LIST
    usually has none, so the whole tree). A pass is stored per slice; changing the count changes every key and invalidates every
    stored slice, which is correct because slice 3 of 8 and slice 3 of 6 hold different tests. A changed file reruns the slices its
    DEPS reach, and with no DEPS all of them. Canary re-runs (5.3) apply per slice. The `remote` kind rules are unchanged.
  - **A failing or lost slice.** A failing slice is `failed` with its slice number, the exact invocation (argument vector with the
    two integers filled) and its tail, so it can be re-run by hand; status 1. A slice on a device that stopped answering is `not
    run` and named `slice 5/8`, status 70, exactly as in section 11; a slice that never got a permit is `not started`, status 75; a
    slice ended by a deadline is 124. Nothing re-runs a slice elsewhere by itself, and running the command again reuses the
    passed slices so only the failed and not-run ones execute. A tool that exits zero having run no tests in a slice is the tool's
    business (the runner reads exit codes and rows, never output text); a project that wants that caught gives `junit` or `jsonl`.
  - **Exit codes are unchanged.** The table in section 11 holds for all three modes; no code is added for slices.
- **Batch size and order** inside a shard: longest first (`testorder`'s rule, the recorded duration), `batch` ids per invocation.

CI uses the same planner without a transport: `poolhouse-test plan --shards 8 --index 3` prints the ids or units of shard 3 using the
durations in `[schedule] seed` (Poolhouse's `tests/ci-durations.json`, written by `poolhouse-test record --results FILE...` from the
result files of a CI run), and `poolhouse-test run --shard 3/8` runs exactly those. A CI job is a target the CI system placed; no
second algorithm exists.

### 5.3 Reuse: result reuse with canary re-runs

A unit's **key** is a hash over: its ids and labels; the RUN template with ids removed, the set's `extra`, `run.env` and `results`; the
file contents named by its DEPS (the whole tree's hash when it has none); `deps.everything` contents; the keys of the steps it needs;
the platform class; the values of the variables named in `reuse.key_env`; the output of the `reuse.key_tools` commands; and the digest
of the runner's own code. This is the key `docs/test-reuse.md` describes with its Python-specific parts moved out: the "bytes of the test
file, conftests and `pyproject.toml`", the pytest and plugin versions, and the import closure are the pytest preset's DEPS and
`key_tools`, stated in the preset's table.

A result may carry `reads`, files the invocation opened that DEPS did not list (the pytest preset's recorder plugin is a helper that
writes them). The store keeps them in the entry's **manifest** and a later hit requires them unchanged; a result may also carry
`unstorable` with a reason (a write outside the temporary roots, a skip), and then nothing is stored. The runner never infers either.

The store is the one `docs/test-reuse.md` defines: entries written through a rename, a chain of row hashes, `latest/<key>`, single flight
with `inflight/<key>.json` claims (a waiter waits while the claim's holder is alive, a liveness the OS reports, and for no fixed time), a store per project (origin url hash, else the
common git directory: `testruns.scope`), only the runner process writes it, and the state directory is outside the checkout and outside
any root a unit may write. What it adds:

- **Kinds.** An entry is `local` (this machine ran it) or `remote` (a device did, keyed by its fingerprint and platform class). A
  `remote` pass satisfies only a later run on that device and never a local run; a `local` pass satisfies this machine. Task evidence
  (`workspace.testruns.verified`) reads only `local`. The ledger `remote-results.json` is deleted; its job is the `remote` kind.
- **Canary.** Each hit is re-executed with probability `reuse.canary`, the project's setting with no runner default (with none, no hit is re-executed and the report says `canary: off`; Poolhouse's file sets it). A cached pass that fails fresh prints
  `canary mismatch`, fails the run, writes an `incident` row and a disabling record for the key, and sends one event to the sinks. The
  draw is seeded from the run id, printed in the report, so a run is repeatable with `--seed`.
- **Never stored:** a failure, a unit with `reuse = false`, a result with `unstorable`, a run whose tree changed while it ran (status
  4), a unit whose key recomputed at the end differs from the start, a split unit (a slice unit is not a split unit: it is stored per slice, 5.2), an interrupted or crashed invocation.
- **Report lines:** `ran` or `reused from <run id>` per unit with the key prefix, `--no-reuse` forces execution and says so once.

The unit key and the affected set are one definition (design-test-selection 4.6): the DEPS of a unit are what the key hashes and what
selection intersects with the changed paths.

### 5.4 Affected selection and analyzers

`poolhouse-test affected --base REF` (and `run --affected`) selects the units whose DEPS contain a path changed between REF and the
working tree (tracked changes, untracked files, deletions), plus every unit when a path in `deps.everything` changed, plus every unit
without DEPS. `--explain` prints the reason per unit.

DEPS come from three places and the core only intersects them:

| Analyzer | Where it runs | What it knows |
|---|---|---|
| declared globs | the core | `[deps] globs`; any language |
| a command | the project's `deps.command`, any program | prints `{"id", "deps": [...]}` rows for LIST's ids; the project writes what it knows (imports, crates, a build graph) |
| shipped helpers | `testfarm/presets/<tool>/` programs, run as commands | `python-imports` (the import closure, facts cached per blob hash in the store, `import-facts.json` today), `cargo-crates` (workspace members and their dependents from `cargo metadata`), `files` (a unit is a file and depends on itself and what its glob says) |

The soundness rules of `docs/design-test-selection.md` section 5 hold for any analyzer: an unknown selects (an id with no DEPS, a
helper that errors, an unreadable file); a skip needs a positive proof (a stored pass for the same key); the selector's own files
select everything (`deps.self`, the config, the helper's code via the runner's digest); and the graph comes from the tree, never from a
device or a board entry. Where an analyzer reports `opaque` for a unit, DEPS widen to the whole tree for that unit.

### 5.5 Admission: compute slots and leases

Admission keeps tests from overloading a machine. The interface the core defines:

```
Admission.acquire(request: Request) -> Grant          # Request: slots, resources, estimate_s, class, label, serial
Grant.release()
```

The default implementation is the machine-wide, file-lock-backed broker of `testslots*.py` moved in whole: capacity is the logical CPU
count less the cores the machine is measurably busy with outside the broker (load sampled when a permit is asked for) and less the
cores the user reserves (`reserved_cores` in the user's settings, no default), shortest estimated work first, a request ages once it has waited as
long as the estimated work ahead of it (from the history, 5.8), so it is never passed over by later shorter work for longer than the
queue it joined; a later request backfills when it fits in the free slots and its estimate ends before the head's estimated start, so
the head is never delayed; a class of `interactive` or `background` (background when the project's `[schedule] background_after`
says so, no default: with none every run is interactive unless the person says `--background`), one slots directory shared by every checkout and project on the machine, a dead holder's record removed at
once. Environment variable names become `TESTFARM_*` (Poolhouse's `DEV_TEST_*`, read by `tests/conftest.py`, move with them).

A unit holds the slots its label names for the duration of its invocation. Finer admission (a permit per test inside one pytest
process, `testslots_pytest.py`) is a pytest preset helper that talks to the same broker; the core knows only per-invocation permits.
A resource the label names that the broker does not own (a GPU, a model slot) is requested from the pool's lease table (`ph.leases`,
the node's table) when a node answers, else from the broker's own `gpu` lane (`HEAVY_MODULES`' heavy lane today). One piece of work on
a device's GPU at a time is a hard floor (AGENTS.md section 1) and holds for every project: the lease kind `gpu` is exclusive.

A command started inside a live permit fails promptly instead of queuing behind its parent (`docs/test-execution.md`); the runner
refuses nested runs the same way.

### 5.6 Jobs

A run is a job: `submit` prints an id, `status`, `wait`, `result`, `cancel` act on it, and the foreground command follows a job
(`testjobs.py`, `docs/test-reuse.md`, "Async jobs"). The same argument vector and tree hash follow the live job instead of starting a
second; Ctrl-C of the last follower cancels, a cancel signals the job's process group and then kills it; one whole-selection job may be
live at a time per project and a second is refused with status 6. A job's owner is the OS user and the process; a named provider
(Poolhouse's workspace) supplies an agent identity and watchers, through the event sink, not through core code.

### 5.7 Command units: gate steps are units

A `[[unit]]` table declares a command as a unit with no LIST: `name`, `run`, `inputs` (globs), `tools`, `labels`. Its key is the
inputs' contents, the command and the tool outputs; a pass is reused while nothing it reads changed, a failure is never stored
(`gaterun.py`'s rule), and independent command units run side by side. Poolhouse's gate (budgets, red-team coverage, command reference,
structure tests) is a set of command units; `gaterun.py` and `gatescope.py` are deleted.

### 5.8 History and estimates

Every run records per-unit CPU and wall seconds (from the invocation's rusage and the result file's `seconds`), merged under a file lock
into the project's history in the state directory, shared by every worktree (the common git directory today), the median of the recorded samples per
unit with a sample count (a median is not moved by one slow outlier, so no clip is needed). The estimate
of a run is the sum of its units' seconds divided by the workers granted, with a unit that has no record counted at the mean of the
recorded ones (the median, so no clip and no cap are needed), and with none recorded at all the units are placed by count and the
estimate says `unknown`; `[schedule] unrecorded_max`, if the project sets it, caps the median; a unit the store would serve costs nothing. The estimate and its class print before admission.

### 5.9 Fail-first

A new test is run on the code without the change and seen to fail. The library makes that a command and a check:

- `poolhouse-test failfirst run [--base REF]`. It lists the units at `REF` (a scratch tree under the state directory, never the
  working tree) and at the working tree. A **new** id is one absent at `REF`; a **changed** id is one whose `def` digest (a LIST
  `jsonl` field; the pytest preset emits the AST hash of the test function) differs; a project whose LIST gives no `def` has only new
  ids. It builds a scratch tree of `REF`'s files with the working tree's files under `[failfirst] tests` laid over them, runs those ids
  there through the ordinary run path (so they shard and respect labels), and requires every one to fail. It then requires them to pass
  on the working tree. For each id it writes an evidence entry `{id, def, base tree, result at base, first error line}` to the store.
  Printed lines: `fail-first: <id> failed on <sha> with <error line>`.
- `poolhouse-test failfirst check --range A..B` is the mechanical check: for every new or changed id in the range's tip it finds
  either an evidence entry for the same `def`, or an `exempt_line` in a commit message of the range (`Fail-first-exempt: <id>:
  <reason>`), and fails listing the ids with neither and the command that makes the evidence. It reads commit messages only for the
  exempt lines.
- `mode` is `off`, `report` (the default: prints, never fails) or `enforce` (the check exits 4). Poolhouse's config sets `enforce` and
  its pre-push hook and `gates` job call the check; another repository wires its own hook or leaves it at `report`.

A pure refactor has no id that is new or changed with a failing base, so it is covered by the exempt line, as the policy says.

### 5.10 Timeouts and where time goes

A loaded machine must never turn queueing into a failing test. Under load 60 to 127 workers saw tests fail their 120 s timeout while
they waited for a CPU permit, which also hides a real hang behind the same message. [code] the per-test permit is taken in a
`pytest_runtest_protocol` wrapper (`testslots_pytest.py`) that has no ordering against `pytest-timeout`'s wrapper, whose timer covers
setup, call and teardown; [report] a wait for the permit therefore counts against `timeout = 120` (`pyproject.toml`, the owner's value).
The runner's rule, for every unit and every project:

- **The clock starts when the unit starts.** A unit's timeout counts only from the moment its invocation is running: the permit is
  granted, the steps it needs are done, the process is started. Waiting in the queue, waiting for a lease, building and unpacking are
  outside it. For a preset whose tool keeps its own timer (pytest-timeout), the preset's helper takes the permit outside that timer
  (outermost hook) and, where a tool's ordering cannot be relied on, restarts the tool's timer after the grant; the preset's tests
  prove it by holding every permit while a unit with a small timeout waits.
- **Three numbers per unit, always reported.** `queued_s` (waiting for admission), `built_s` (steps and setup the unit waited for) and
  `ran_s` (the running time the timeout judges). The report line is `ran tests/x.py  queued 12 s  built 0 s  ran 3.1 s`, the run's phase
  line totals them (`queued for CPU` is the machine's, the rest is the fixed overhead the 5-second rule measures), and a result file may
  carry `queued_s` for per-test permits. The history records `ran_s` as the unit's duration, so the estimates and the planner do not
  learn queue time.
- **Outcomes.** A unit is `passed`, `failed`, `timed out` (it ran past its timeout: a failure that says `timed out after N s running`
  and shows the three numbers), `skipped`, `reused`, `not reported`, or **`not started`**. `not started` means the machine could not
  admit it: it waited for a permit or lease past the project's queue limit (`[schedule] queue_wait`, no runner default; with none, the queue wait is guarded by no progress: the broker's queue position and the holders' heartbeats stop advancing for the derived stretch, status 75 naming the holder waited for), or the run was
  cancelled before its permit. It is neither a failure nor a pass, is listed apart in the report (`NOT STARTED, machine overloaded:
  waited <N> s for a permit`, N measured), is not stored, and gives status 75. Running the command again continues with exactly those units.
- **Where a timeout's value comes from.** The runner has no timeout of its own. In order: `[label.NAME] timeout` for the unit's labels
  (the largest of its labels), `[run] timeout`, then none locally. Poolhouse's file takes its value from the owner's setting: 120 s a
  unit (`pyproject.toml`, `docs/limits.md` row `pytest timeout`), and a longer unit carries a label with a longer value (the
  `@pytest.mark.timeout(N)` it has today). On a device a unit with no timeout is bounded by its shard's deadline, which is the largest unit
  timeout the sender sent for the shard and, when no unit has one, the hang guard below.
- **Hang guards: no progress first, a total second.** Nothing waits forever. Each unit, shard, build step and queue wait has (1) a
  *no-progress* guard: it ends when no output, heartbeat or result row has arrived for a stretch derived from a measurement, namely the
  longest silence its own recorded runs showed times the project's `[schedule] silence_margin`, or for a device the measured round
  trip of the stream times that margin; and (2) a *total* guard, the unit's recorded typical duration (the median, 5.8) times the
  project's `[schedule] total_margin`, used only when the label, step or `[run]` table names no `timeout` (which always wins). The
  margins are the project's numbers in `testfarm.toml` with no runner default; a unit with no silence recorded of its own takes the
  guard of its siblings: the median of the per-unit longest silences recorded across the project's other units times `silence_margin`.
  With no history at all in the project (its first run ever), the run is guarded against the live gaps it produces itself: a silence
  longer than the longest gap seen so far in this run times `silence_margin`, applied after at least one unit has finished, and the
  report says `guard: bootstrapping`. **A run with no hang guard is not allowed.** When
  neither `[schedule] silence_margin` nor an explicit `timeout` for every unit and step is set, the runner refuses to start, loudly,
  with status 4, naming the missing key and the reason (`testfarm.toml: [schedule] silence_margin is not set, and unit tests/x.py
  has no timeout: a run with no hang guard is not allowed`). There is no default number. `poolhouse-test init` writes the key for a
  new project from its measured first run (the longest gap between outputs seen, divided by the median gap, rounded up) and prints it
  for the person to read in the new file. A guard ends the work loudly, with status 124 for a unit, shard or step and 75 for a queue wait, and the
  line names what was waited for and the derivation: `unit tests/x.py: no output for 41 s (longest silence recorded 8 s x margin 5)`.
  A build step has its own `timeout`, else the hang guards above on the step's recorded runs, and never counts in a unit's.
- **Overload is named, not guessed.** The report says `the machine was overloaded` only when units are `not started`; a run in which every
  unit started but some queued for a long time says `queued N s (the machine's)` and judges the units by `ran_s` alone.

## 6. Transports

### 6.1 The interface

```
class Transport(Protocol):
    name: str
    def targets(self) -> list[Target]                  # devices reachable now: capacity, platform, tags, accepts / why not
    def start(self, shard: Shard, tree: Tree) -> Handle
    def poll(self, handle, since: int) -> Progress      # state, output from byte `since`, result once ended
    def cancel(self, handle) -> None
```

A `Shard` is `{id (128 random bits), config_sha256, tree_sha256, run (the set), units, extra, steps, deadline_s}`: no argument
vector, environment or working directory. The receiver builds the command from the config it holds at `config_sha256` (10.3).
`Tree` is the sender's packed files and their hash over the transported set.

`local` is the transport of this machine: `start` forks the shard in the checkout itself (no copy, the warm caches, the project's
launcher) under the same executor code a device runs. `pool` is the pool of this device (6.2). `ssh` later implements the same four
calls over an ssh channel and stays a plugin. `[remote] default` and the flags choose targets: `auto` (the default) uses the pool's
accepting devices of a pool in dev mode in addition to this machine and nothing else; `local` uses this machine; `pool` or a device
name or fingerprint uses those, and a pool that is not in dev mode is used only when named. `auto` is a decision made here: in a
pool in dev mode every member already runs tests for every other member (`docs/fleet.md`, "Two devices on a LAN"), and
`poolhouse features disable remote-tests` is the switch.

### 6.2 The pool transport

The wire is the shard routes of a pool in dev mode, generalised from a fixed command to a unit list. [code] today:

| Operation | Route | Notes |
|---|---|---|
| capabilities | `GET /shards/v1/caps` | `accepts` or the sentence for why not, platform, free slots; no authority needed beyond membership |
| upload | `PUT /shards/v1/ID/tree` | in order, `Content-Range`, the packed tree |
| start | `POST /shards/v1/start` | a closed set of fields |
| state | `GET /shards/v1/ID?since=N` | state, output from byte N, result once ended |
| cancel | `POST /shards/v1/ID/cancel` | the shard and its process tree |

Changes: the start fields become `id, config_sha256, tree_sha256, set, units, extra, steps, deadline_s`; caps adds `protocol`, the
build, `requires` satisfied (6.5) and the policy under which the sender is admitted. Transport and authentication are unchanged:
requests are signed with the pool's key over TLS 1.3 pinned to the certificate the pool recorded for the member, the sender is that
certificate and never a field, a shard belongs to the member that first uploaded to its id and every other member is told "no such
shard", and a removed member is refused at the connection. A protocol or build mismatch is refused with the update hint
(`poolhouse runtime ensure`), never run.

**Admission by the receiver.** One function decides: `admit(sender fingerprint, pool mode, policy) → allow | the sentence for why not`.
In a pool in dev mode it allows every member while `remote-tests` is not disabled. In any other pool it allows only the senders on the
device's allow list, empty until a person adds one (`poolhouse-test allow DEVICE`, a typed yes at a terminal; `deny` and removing a
member take it off and cancel its runs), as `shard_consent` does today. Open question 1 is which pool stack carries that list.

**One server, one executor.** The server half (`testfarm/transports/pool_server.py`) and the executor (`testfarm/executor.py`, L2) are
the only receiver code; `fleet/shard_*.py` and the Rust shard host are deleted in favour of them. The executor: checks the header and the
config against the schema; checks the tree (6.3); makes a scratch tree; runs the steps, then the units; streams output with a size
bound; enforces the deadline (an interrupt, then a kill of the whole process group, `taskkill /T /F` on Windows); returns a result of
bounded size; removes the scratch tree. It takes slots from the device's admission and yields to the device's own work: a shard queues
behind the device's interactive work and is killed when a GPU, benchmark or foreground lease is requested (10.7).

### 6.3 The tree

The sender packs the transported set (the `[tree]` rule) as a gzip tar of plain files, mode normalised, and computes **one hash over
exactly the transported files** (path, mode, content, sorted), by a function both ends call. The upload is checked by its digest, the
unpacked files are re-hashed and compared with `tree_sha256`, and `config_sha256` must be the hash of `testfarm.toml` in that tree.
Limits are the receiver's (10.6). A later step sends a delta against a base commit fetched by SHA only, with none of the device's git
credentials (`design-test-selection` 6.1); the first version sends the whole tree.

### 6.4 Results

A result is per unit `{id, status, seconds, message, reads, unstorable}`, a platform stamp, wall and CPU seconds, the `shard_id`,
`tree_sha256` and `config_sha256`, and a bounded output tail. The sender checks: the shard id, the tree and config hashes, that every
assigned id has exactly one row and no other id appears, that the platform is the device's class, and that sizes are in bounds. A
`reads` list from a device is kept in a `remote` entry and is never used to store a `local` pass. The sender computes every key itself
and ignores any key or digest in a result. A pass from a device is advice (the inner loop, and reuse on that device); it never counts
for landing, task evidence or release (`design-test-selection` 6.5).

### 6.5 Capabilities and requirements

A project cannot ask a device to run a probe command. `[requires]` names things the device checks itself with probes it owns:
`python>=3.13`, `module:pytest`, `exe:cargo`, `exe:node>=20`. Caps lists which are satisfied; a device missing one takes no shard and says
which, in one sentence an agent can relay. Provisioning (installing what is missing) is a person's or the device setup's act, not the
runner's: `python -m poolhouse.device_setup --yes` on the device, or the project's own steps within the confinement.

## 7. Presets: tools, never repositories

A preset is a TOML table with the same keys as a project file (`list`, `run`, optionally `deps`, `label` rules, `[requires]`). It is
named after a tool and holds no knowledge of any repository. It lives in `testfarm/presets/NAME.toml`; the helper programs it runs
(`testfarm/presets/NAME/*.py`) are ordinary Python modules that import only the core. A project may define `[preset.NAME]` in its own
file or in the user's `~/.config/poolhouse/testfarm-presets.toml`, which wins over a shipped one.

| Preset | LIST | RUN | Results | Group and labels | DEPS |
|---|---|---|---|---|---|
| `pytest` | the helper `pytest/list.py` runs `pytest --collect-only` with a plugin and prints `jsonl` rows | `python -m pytest -q {extra} -p testfarm_results {ids}` | `jsonl` from the plugin (true node ids, seconds, `reads`) | group = the part before `::`; markers become labels; `def` = the test function's AST hash | the `python-imports` helper |
| `cargo` | the helper lists test targets from `cargo test --no-run --message-format=json` | the helper runs the built executables the step produced | `exit` per target (libtest has no stable machine-readable output); `junit` with nextest | group = the target | the `cargo-crates` helper |
| `npm` | `["npm", "run", "--silent", "test:list"]` when the project defines it, else `files` | `["npm", "test", "--", "{ids}"]` | `exit`, or `junit` when configured | per file | `deps.globs` |
| `files` | every file matching `list.glob` | execute the file | `exit` | a unit is a file | itself |

`poolhouse-test init` looks for the tool's own marker files (a pytest configuration, a `Cargo.toml`, a `package.json`) and writes the
four-line file for the tool it finds; it never overwrites a file and never invents DEPS. The shipped presets are tested against
generated neutral projects only (section 13, check C). A preset for a new tool is a file; nothing in the core changes.

## 8. Poolhouse's own project file, and `scripts/test`

Poolhouse's repository has a `testfarm.toml` like any other. A sketch of what it says about itself (illustrative; the file starts in migration step 3,
grows with each step that moves the code a table describes, and is complete in step 11):

```toml
schema = 1
preset = "pytest"

[tree]
exclude = ["dist/**", "target/**"]

[set]
fast = { not_labels = ["slow", "heavy", "gate"] }
full = { not_labels = ["slow", "gate"] }
slow = { labels = ["slow"], extra = ["--slow"] }
all  = { extra = ["--slow"] }
gate = { labels = ["gate"], extra = ["--gate"] }

[label]
heavy      = { lease = ["heavy"] }
seatbelt   = { local = true }
real_model = { lease = ["gpu"], reuse = false }
live_api   = { reuse = false, local = true }

[run]
timeout = 120        # the owner's per-test value in pyproject.toml; a unit with a longer one gets a label with its own `timeout`

[[step]]
name = "node"
run = ["python", "scripts/testnode.py", "build"]
inputs = ["app/**/*.rs", "app/**/Cargo.toml", "app/Cargo.lock", "tests/fixtures/phone-approval-vectors.json"]
tools = [["rustc", "--version", "--verbose"]]

[deps]
everything = ["pyproject.toml", "tests/conftest.py", "tests/known-fixtures.txt", "budgets.json"]

[schedule]
seed = "tests/ci-durations.json"
silence_margin = 5   # written by `poolhouse-test init` from the measured first run; without it (or a timeout on every unit and step) the run is refused, status 4

[failfirst]
mode = "enforce"

[hooks]
before_plan = [["python", "scripts/test_policy.py"]]       # agents may not run the whole suite; the preflight
```

`scripts/test` becomes:

```python
#!/usr/bin/env python3
"""Run this repository's tests: the public `poolhouse-test` command, configured by testfarm.toml."""
from poolhouse.testfarm.cli import main
raise SystemExit(main())
```

with `scripts/test all tests/test_x.py` kept working as the spelling of `poolhouse-test run @all tests/test_x.py`: the first word
that names a set becomes `@set`. The pytest arguments agents pass after the tier go through `{extra}`. `--confine` and
`--artifact-output` stay Poolhouse's: the launcher and the hooks.

What stays in `scripts/` as Poolhouse-specific: `test_kernel_*.py`, `test_container_*.py`, `test-on-linux`, `test_browser_admission.py`,
`test_terminal_bank.py`, `test_fixture_plan.py`, `testwritedeny.py`, `testreap.py`, `testagentenv_pytest.py`, `testextras.py`,
`testscope.py`, `testnode.py`, `testapp.py`, `queue_doctor.py`, `ci_watch.py`, the ratchet and `heavy` helpers. What moves into
`src/poolhouse/testfarm/` (git moves, renamed from `test*` to the layers above): `testslots*`, `testjobs`, `testqueue`, `testclass`,
`testprelude`, `testphases`, `testhistory`, `testdurations`, `testorder`, `testtime`, `testreuse_*`, `testselectors`, `gaterun`,
`testunits`, `testartifacts`, `affected*`. What is deleted outright is in section 14.

## 9. The cargo build handling, generalised

The handling that has landed [code, `testunits.py`, `tests/native_cargo.py`, `testnode.py`, `testartifacts.py`] is: build every target a
module names in one `cargo test --no-run` call at a quarter of the cores, before the module's timer; run the executables cargo
reported, never asking cargo again; a staged copy of the workspace at a fixed path keyed by the sources and compiler, a target folder
keyed by `Cargo.lock` and the compiler and shared by every run; products of old snapshots pruned when the disk is needed, least recently used first; cargo's own lock
serialising two builds.

In the design this is the `cargo` preset plus a step, with the core knowing none of it:

- the **step** `build` has `inputs` (the workspace files), `tools` (`rustc --version --verbose`), `slots` (the project's, else the free slots), its own
  `timeout` (the project's), `{shared}` for the target folder (so registry dependencies are built once across keys), `{cache}`
  for the staged workspace and the products manifest, and `prune` for the workspace-crate products (`testartifacts.prune_when_due`);
- the preset's **list helper** names the targets the step built; its **run helper** executes the built files with the package folder as
  the working directory;
- timers: the step's time is outside every unit's timeout, which is what the `func_only` timeout achieves today; the report shows
  `steps: build 41 s`;
- on a device: the same step runs in the device's confinement with `{shared}` keyed by the sender (4.4); a device without a toolchain
  fails `requires` with one sentence rather than trying to build;
- what stays Poolhouse's: the node binary's member list, the fixtures staged beside the workspace and the `phnc-` scratch folder are in
  `scripts/testnode.py` (declared as Poolhouse's step) and `tests/native_cargo.py` (its fixtures).

## 10. Security

The runner runs code on other devices, as the user, from a tree and a configuration a sender chose. The design assumes every input,
caller, device and agent is hostile (AGENTS.md section 1) and derives trust in the receiver.

### 10.1 Threat model

Assets: the user's files, keys and sessions on every device in the pool; the compute and disk of a device; the integrity of the sender's
results and store; the sender's source tree (disclosed to every device it is sent to).

Attackers: a pool member that is compromised or malicious (as sender or as receiver); a device that is cloned or whose key is stolen;
a malicious or prompt-injected agent on the sender; a hostile repository whose `testfarm.toml` and tests the user runs; another user on
the same machine; a network attacker on the LAN.

Not defended: a sender that sends its own code to its own pool on purpose to harm the pool's devices is limited by confinement and
consent, not by anything about the code; a receiver that lies about results it ran (it can only affect passes it reports for itself,
10.8 A9); a same-user attacker with filesystem access to the state directory (the limit `docs/test-reuse.md` already states).

### 10.2 Authentication and who may run what

- **Identity is the pool's.** A shard operation is a signed request over mutual TLS pinned to the certificate the pool recorded for the
  member; the sender is the certificate, never a field of the request; `X-Poolhouse-Agent` and `by` are labels for the audit trail
  (`docs/test-farm.md`, "Mechanism"). The runner creates no identity, token or credential of its own.
- **The receiver's policy decides.** `admit()` (6.2) in a pool in dev mode: every member, until `remote-tests` is disabled on the
  device. In any other pool: the allow list, empty until a person adds a sender, never widened by joining, by `inbound_consent` or by being
  a member (`docs/pools-and-serving.md`). The list is per pool (`docs/design-multi-pool-devices.md`).
- **What the policy can restrict, on the receiver, by its own settings:** which runner kinds (`presets` only, or any vector), the
  network a step may use (default loopback only), the slots and disk a sender may hold, the senders. A key in the sent
  `testfarm.toml` can only ask for less than the policy allows; it cannot widen it.
- **Confinement is the receiver's.** The executor wraps every step and invocation in a deny-default profile it builds itself from
  `src/poolhouse/sandbox/` (`seatbelt.py` on macOS, `bubblewrap.py` on Linux): reads the scratch tree, the interpreter and system
  libraries, the project's `{cache}` and `{shared}`; writes the scratch tree and those two directories; no keychain, `~/.ssh`,
  `~/.poolhouse` or home; loopback only unless the policy allows more. A device with no confinement available takes no shard. A tree's
  own scripts and `launcher` never choose the profile. This is `design-test-selection` slice 6, and it is a hard prerequisite of
  sending a tree to any other device through the generalised path (migration step 8).
- **Local runs** use the project's launcher and the user's own account: the user is running their own repository.

### 10.3 What a sent configuration and test list can do

The sent tree contains code, so the receiver runs sender code; what it refuses to do is *build the command from sender text*.
The receiver reads `testfarm.toml` from the tree it verified, validates it against the schema, and builds the argument vector from the
`run.argv` of the named set: only `{ids}` (and `{idfile}`, `{result}`, `{extra}`, `{jobs}`, `{step:NAME}`, `{shared:NAME}`) are substituted,
each as a separate argument, never through a shell. `extra` comes from the set in the verified file, not from the start request.
Hooks and launchers in the file are not run on a device. A shard asks for units by id; the receiver checks each id against the id
grammar and, when the sender asked for it (`[remote] verify_list`), against its own run of LIST inside the confinement.

### 10.4 The id grammar and untrusted lists

An id is 1 to `PATH_MAX` bytes (the smaller of sender and receiver), UTF-8, no control characters (U+0000 to U+001F, U+007F), no
bidirectional override characters, not beginning with `-`, with no repeat inside a shard, with the units of one shard bounded only by the
receiver's request-size bound (10.6) and not by a count of the runner's own (more ids in them); a unit group is checked as a whole. An id is only ever an argument or a line of `{idfile}`. The sender's LIST output is
data: lines over the bound, binary output, a LIST that never ends (bounded by output size and a deadline) and duplicate ids are
errors that name the first offender.

A slice index and count (5.2) are never read from a result, a device or the environment: the runner computes them as positive integers (the count at
least 1, the index from `slice_base` to `slice_count + slice_base - 1`, with no fixed maximum), formats them in decimal with no sign or padding, and substitutes
them as whole arguments of the argument vector (or inside one argument, never into a shell string). A template with a placeholder
other than the two, a repeated one, or one in the program name is refused when the file is loaded; a device receives the filled
vector with ids from this grammar and refuses anything else.

### 10.5 Output and environment

- **Output from a device is data.** Every byte a device sends (output, messages, names) is decoded as UTF-8 with replacement,
  stripped of ANSI/OSC/CSI escapes, other control characters (except newline and tab), carriage-return line overwrites and
  bidirectional overrides, truncated per line and in total, and printed after a `[device]` prefix the *sender* adds, so a device cannot
  forge another device's line, a summary line or an exit status. A device's name is sanitised the same way before display
  (`report.plain`). Result messages and failure texts are bounded by the receiver's result bound (10.6), shared among the fields.
- **The child environment is an allowlist**, not today's denylist (`shard_run.DROPPED`): `PATH`, `LANG`, `LC_*`, `TZ`, `TMPDIR` (a scratch
  path), `HOME` (a scratch path), and the names the verified file's `run.env` sets; nothing else, so agent markers, tokens and the
  sender's variables do not arrive, and `credentials.child_environment` strips what looks like a credential.
- **XML is parsed without entities or external references** (`defusedxml` is already a test dependency; the runner uses the standard
  parser with DTDs refused), and a junit file larger than the receiver's result bound (10.6) is refused.

### 10.6 Resource bounds and where they come from

Every bound has a source; where the source is "the owner, in source" it is a row of `docs/limits.md`, and the rows of the deleted Rust
host move to the Python module that now holds them. Section 15 lists them. The rule for every bound, new or carried over: derived from the device (free disk
of the scratch volume, free inodes, logical CPUs, memory) or from the operating system (`PATH_MAX`, `ARG_MAX`), or set by the project's
own file, never typed in by the runner. The request, result, log, junit, member, unit and output bounds are all shares of the free
scratch disk the receiver measures when it takes a shard. Examples: the packed-tree limit a receiver accepts is at most its free
scratch disk divided by the shards it holds open; slots granted to one sender are at most the device's free slots at admission; the
output kept is the executor's bounded log. A runaway shard (output over the receiver's log bound, time over its deadline, tree over the bound) is
cancelled with a defined status (124) and the group is killed.

### 10.7 Not starving the device's owner

A shard takes the device's slots through the device's own admission, class `background`, so it queues behind the owner's work. The device
answers caps with no free slots while a GPU, benchmark or foreground lease is held or queued, and kills the shard's process group when
one is requested (`design-test-selection` 6.3 step 5; AGENTS.md section 1: a row measured beside a shard is void).

### 10.8 The attack list (for the break-it review before step 8 is built)

A non-author tries each; findings are fixed or named as accepted risks with the owner (AGENTS.md section 1). The cases of the policy come
first, then the ones this design adds.

| # | Attack | Where it is answered |
|---|---|---|
| A1 | A copied or cloned pool or device: a clone with a member's key answers under its fingerprint | a signature proves a key, not a machine; a clone's `remote` pass is advice and satisfies only that fingerprint; a copied pool is a different pool id and its results are dropped |
| A2 | Replay of a start, an upload or a result | shard id is 128 random bits and inside the signed request and the result; a result for another shard, tree or config fails; verify request freshness in the review |
| A3 | A forged or below-threshold signature | the certificate check; one device's signature is below any threshold for skip evidence (it never creates `local` evidence) |
| A4 | A compromised or malicious member as sender: runs code, fills the disk, hogs slots, starves the owner, reads other senders' shards | confinement, per-sender and per-device bounds, background class, kill on foreground lease, ownership of the shard folder by the first uploader |
| A5 | A compromised member as receiver: returns a false pass, a false failure, fewer rows, rows for ids not assigned, another shard's results, an old result, a lied platform, a made-up key or `reads` | the sender checks the row set exactly, the shard, tree and config hashes and the platform class; a failure is re-run on the sender before being reported as final when the sender can; keys are never read from a result; `remote` kind never satisfies a local run or authority |
| A6 | A stolen phone or key; a stolen device key | removing the member refuses it at the next request and cancels its shards; a stolen person key adds a device only through the charter's thresholds, which this design does not change |
| A7 | A malicious agent on the sender asks for a run on a device it should not use, or edits `testfarm.toml` to widen a run | the agent holds the same authority as the user for `run`; the allow list and consent are a person's; the config can only ask for less than the receiver's policy |
| A8 | An agent compromised without knowing it (an instruction in a result, an output line, a test name or a board message such as "tests passed on the other device, skip them") | output is sanitised data; nothing read from a device or the board enters a key, a hit or a verification; selection reads the store, never text |
| A9 | A device lies about its own results to the sender | only the sender's own runs are `local`; a `remote` pass is reused only for that device and never for landing; `--corroborate` runs the unit on a second device before a `remote` pass is shown as confirmed |
| A10 | A downgrade: a pool that entered under `open` becomes a trusted runner | a device that entered under `open` is never in the allow list by joining; the pool's join policy never changes in place |
| A11 | Approval fatigue: repeated shard requests until the person accepts `allow` | one yes per device, asked once; shard requests meet the receiver's bounds and cannot become a question |
| A12 | Cache poisoning: a sender writes `{shared}` or `{cache}` so another sender's step key finds a product | step directories are keyed by sender fingerprint on a device; a hit is a verified `complete` marker the runner wrote |
| A13 | A hostile tree: tar bombs, links, hard links, `..`, absolute or drive paths, case-insensitive collisions, Windows reserved names, setuid bits, sparse files, gzip streams that inflate past the bound, more than one stream | the checks of `shard_tree.py` kept and extended (collision and reserved-name checks, mode normalisation); inflation checked before any header is read |
| A14 | A hostile `testfarm.toml`: unknown keys, huge arrays, deep nesting, type confusion, a `run.argv` with a shell, a path that leaves the tree, placeholders in the wrong place | the strict schema; only listed placeholders; an array of strings; paths normalised and refused if they leave the tree; size bound on the file |
| A15 | Argument injection through an id (`--option`, a path that escapes, a newline) | the id grammar (10.4); ids are separate arguments or lines of a file; ids may not begin with `-` |
| A16 | A LIST that prints a huge or infinite output, binary data, or ids that do not exist | bounded output and deadline; errors name the first offender; ids sent to a device are checked against the sender's LIST |
| A17 | Result files: junit entity expansion, a result for ids not assigned, duplicates, a path in `reads` that escapes the tree, a giant `message` | entities refused; exact row set; `reads` normalised to tree paths or dropped; bounded sizes |
| A18 | Terminal injection through output: escape sequences, carriage returns, a fake `[other-device]` prefix, a fake summary or status line | 10.5; the prefix and the summary are the sender's |
| A19 | The source tree is disclosed to a device (secrets in untracked files) | the packer refuses files whose names look like secrets and says which; only devices the pool trusts; a person-visible list of what a shard carries (`plan --show-tree`) |
| A20 | Time-of-check to time-of-use between the hash and the run, symlink races in the scratch tree | hash after unpack, files made read-only for the invocation's user, no links accepted, the scratch tree owned by the executor |
| A21 | Fork bomb, memory hog, disk fill by a shard | process-group limits and kill, memory and disk bounds derived from the device, bounded log |
| A22 | The step or hook of a repository the user was handed runs on the sender | hooks and steps are code of the project the user chose to run; the first run in a project with hooks prints the hook commands once; nothing sends a hook to a device |
| A23 | A project's `testfarm.toml` or the reuse store reads outside the project (a symlink to a home file in `inputs`) | the runner reads only the project root and its state directory; checked by check B |
| A24 | Takeover by whoever is merely present on the LAN | dev mode trusts every device that joined it, which `docs/fleet.md` states; a pool that is not in dev mode requires the allow list; the kill switch is `remote-tests` |
| A25 | An old build on one device (a downgrade of the protocol) | caps carries `protocol` and the build; a mismatch is refused, never run |
| A26 | A sender queues work or sets tiny timeouts to make a device's own work look failed or late | the device's own work is admitted first (background class); `not started` is reported to the sender only |
| A27 | Slice injection: a hostile index or count (`--slices '8; rm -rf'`, `-1`, `1e9`, a count that makes the tool run one slice out of range, a slice template that smuggles a flag), or a device that reports slices the sender did not assign | the index and count are integers the runner makes, positive, decimal only, with no invented maximum, put in as whole arguments (10.4); `--slices` is parsed as an integer or refused with status 4; the template allows exactly the two placeholders; the sender checks the slice set it received (exactly the slices it assigned, once each) as it checks ids (A5) |
| A28 | A slice count change used to keep a stale pass (slice 3 of 8 satisfying slice 3 of 6), or a hidden gap where the tool's partition skips tests | the count and index are in every slice key (5.2); the plan states that coverage is the tool's partition and not checked, so nothing claims a coverage the runner cannot see |

## 11. Failure behaviour, exit codes and determinism

**Exit status** is one number, the numerically highest of the causes present (the order below is the order of severity), and the report always lists every cause. `not run` (a device was lost: status 70) and `not started` (the machine was overloaded: status 75) are different outcomes and are never merged:

| Status | Meaning | Source |
|---|---|---|
| 0 | every selected unit passed or was reused (an empty selection says so and is 0) | the runner's existing rule that "no tests selected" is success (`scripts/test` `clean`) |
| 1 | at least one unit failed | pytest's failure status, the existing report |
| 4 | refused before anything ran: invalid config, id, selector or tree; no hang guard set (`[schedule] silence_margin`, or a `timeout` on every unit and step); a hook refused; the tree changed during the run; a selector matched nothing | `scripts/test` argument errors and the tree-changed rule |
| 6 | busy: a whole-selection job is already live | `BUSY_EXIT` |
| 70 | a device did not complete its part: not in the pool, refused, unreachable, vanished mid-run, missing a requirement | `test_on.NOT_RUN`, `docs/test-farm.md` |
| 75 | at least one unit never started: it waited for a permit past the queue limit while the machine was overloaded; nothing about it failed | `EX_TEMPFAIL` of `sysexits.h`; the queue limit is the project's `[schedule] queue_wait` |
| 124 | a deadline or an output bound ended a shard | `shard_run.execute` |
| 130 | cancelled | `scripts/test` job cancel |

**A device vanishes mid-run.** The sender holds one pinned-TLS stream per shard and the receiver reports on it as things happen,
so there is no poll interval; a stream that fails (a reset, or the TCP keepalive timeout of the operating system), or that carries
no result or heartbeat for the no-progress stretch (the measured round trip of the stream times the project's `silence_margin`), ends
the device's part: the report says, loudly and on stderr, `<device> stopped answering <N>s ago (measured) while shard <id> ran there; its units did not finish
and were not run anywhere else`, lists the units as `not run` (neither failed nor reused), and the status is 70. The other targets
finish and are reported. Nothing re-runs on another device or locally by itself. Running the same command again continues: reuse
serves every unit that passed, so exactly the not-run remainder runs, on whatever is reachable then. The shard on the vanished device is
cancelled when the device returns (its shard is dropped by its own idle rule otherwise).

**A local invocation dies** (a signal, an out-of-memory kill): its units fail with the signal named; units of a batch that never reported
are `not reported` failures; the process group is killed.

**Cancel** (Ctrl-C, `cancel JOB`) signals every target's shard, waits for the process groups to end, and returns 130; units that
finished stay stored.

**Determinism.** For one tree, one config, one durations file and one target list the plan is identical on every machine and prints its
digest; the report is ordered by unit, not by arrival; live output is ordered per device by byte offset; the canary draw is seeded and
the seed printed; no unit depends on another having run. Wall time and which device ran a unit may vary only through what is measured
(durations, free slots).

## 12. Adoption, in minutes, for any repository

```
pip install poolhouse                       # nothing else: the command, the Python API and the presets come with it
poolhouse-test init                         # finds the tool's marker file, writes a testfarm.toml (the tool's four lines and, after its measured first run, `[schedule] silence_margin`), prints it
poolhouse-test run                          # parallel on this machine under the broker, with reuse; second run reuses what passed
poolhouse-test run --affected               # once DEPS are given (init offers the globs it can see)
poolhouse-test devices                      # the pool's other devices, whether each takes tests, platform, free slots
poolhouse-test run                          # with a pool in dev mode: the same command, sharded over it (auto)
```

Steps that need a person: none for a pool of one device or a pool in dev mode; a pool that is not in dev mode needs the receiving
device's person to add the sender once (`poolhouse-test allow`), asked once through the question tool with the sentence "This lets
'<device>' run its own code on this computer, as you." Security adds no visible step to this journey. A repository in any language
adopts by writing its two commands: for tests that are executable files, `preset = "files"`; for anything else, `[list]` and `[run]` in
the sentence of section 1. DEPS can be added any time and only make the runner faster.

**The dogfooding rule.** Poolhouse's tests run through `poolhouse-test`, configured by `testfarm.toml`, by the same code path as every
repository, and the repository's scripts and workflows use only the public surface (check E). A capability the repository needs that
the public surface lacks is added to the surface, never reached for privately. The `scripts/test` spelling is a convenience that
translates to the public command.

## 13. Checks that keep the direction and the contract

Each is a test in the migration steps named, each with generated neutral projects and no other repository.

- **A. Layering.** `tests/test_testfarm_layers.py` reads every import under `src/poolhouse/testfarm/`: the
  core imports no preset, transport or plugin; a preset imports only L2 and L1; L1 imports only the standard library; the L0 list is
  exact; nothing under `testfarm` imports `scripts`, `tests`, `board`, `serve`, `workspace` (except the named integration, which
  imports the store); `import poolhouse.testfarm` loads no `fleet`, `board`, `numpy` or `pytest`.
- **B. The runner reads only its project and its state.** A test generates a neutral project in a temporary directory, makes every
  other directory under the temporary root absent or unreadable, and runs `poolhouse-test check`, `list`, `run` and `affected`
  under the deny-default read profile of `src/poolhouse/sandbox/` that allows exactly: the project root, the runner's state
  directory, the interpreter and system libraries. It also runs once under `sys.addaudithook` recording every `open`, `stat` and
  `listdir` and fails on a path outside the allowed set. Nothing outside is read: no home, no parent directory, no sibling repository.
- **C. Neutral projects only.** Every test of the runner builds its project with the generator `tests/testfarm_project.py` (a
  temporary directory, invented names such as `widgets`); a static test fails when a runner test names the repository root, `scripts/`
  of Poolhouse, or a path outside its temporary project, except `tests/test_testfarm_dogfood.py`, which is the only module that
  loads Poolhouse's own file and says so.
- **D. No hidden defaults.** A test copies Poolhouse's tree to a temporary directory without `testfarm.toml`, runs `poolhouse-test
  check` and requires status 4 with the `init` hint and no units; another test greps `src/poolhouse/testfarm/` for the repository's own
  script names, test directory names, `DEV_TEST_`, `budgets`, `heavy-modules`, `seatbelt-modules` and the agent markers, and requires
  none. Presets are tested to contain no path or name of any repository.
- **E. Dogfooding.** `tests/test_testfarm_dogfood.py` loads Poolhouse's `testfarm.toml` through `ph.test.config`, runs `check`, and
  fails when a script, a workflow or a non-runner test imports a `poolhouse.testfarm` name that `ph.test` does not export.
- **F. Development rules stay in development.** The same generated project run with and without the Poolhouse development markers in
  the environment gives the identical plan and result; the wheel's file list for `testfarm` contains nothing from `scripts/`.
- **G. Contract fuzz.** LIST and RUN fakes written as real programs in the generated project (a shell script, a Python script, a
  program that prints escapes, huge lines, duplicate ids, ids starting with `-`) are run and each hostile output is refused or sanitised
  as section 10 says.

The second-repo test (section 14, step 10): a throwaway repository generated in a temporary directory, depending on the installed
wheel of Poolhouse in a fresh virtual environment, with a small pytest suite and a non-Python test (a shell script), runs
`poolhouse-test run` across **two local devices** (two pool daemons on loopback ports with separate state directories and a
dev-mode pool between them, the harness of `tests/test_dev_shards.py` and `tests/dev_pool_kit.py`, no beacon, no multicast), using only the
`poolhouse-test` command and `ph.test`. It asserts: both devices ran a share; one report and one status; a second run reuses; a
failing unit gives status 1 with its message; stopping one daemon mid-run gives 70 with the loud sentence and no re-run; a tampered
tree is refused; and, under the read profile of check B, nothing outside the project and the state directories was read. It is the
proof that a repository that is not Poolhouse can use the feature; it is a `slow` test that needs the wheel (`python
packaging/build.py`).

## 14. Migration plan

Principles: **library first, Poolhouse's scripts as config second, deletions third.** Each step lands alone, keeps the repository green,
moves code with its tests (`git mv`, so history follows), is tested with explicit `scripts/test` selectors, and is watched failing first
(`Fail-first:` lines). There is never a second implementation at rest: a step that introduces the general path removes in the same
commit the code it replaces, and a move leaves nothing behind. Poolhouse's own `testfarm.toml` exists from step 3 and gains a table in
each step that moves the code it describes, so Poolhouse is a consumer of the library from the first runnable step and `scripts/test`
keeps its commands and output until step 11 makes it thin. Where a step says the tests move, the test file is renamed to the new
module's name and its imports updated; its assertions are not weakened (`scripts/hooks/weakened-assertions` refuses that). The selection
design's slices are absorbed: its slice 1 is step 5, slices 3 and 4 are the analyzers inside step 5, slice 6 is step 8, and slices 7 to 9
are steps 8 and 9.

**Phase A: the library** (steps 0 to 10)

0. **Fix the timeout accounting of the current harness** (section 5.10), before anything moves. Files: `scripts/testslots_pytest.py`
   (the permit is taken in a `pytest_runtest_protocol` wrapper that has no ordering against `pytest-timeout`'s wrapper, so the 120 s
   timer of `pyproject.toml` can start before the permit is granted), `scripts/testslots_rpc.py`, `scripts/testphases.py` (queue wait
   is already a phase), and the report lines. The permit is taken outside the timer, queue wait is reported per test and in the run
   total, and a test that never got its permit within `WAIT_S` is reported as `not started` with status 75, not as a failure. Tests:
   `tests/test_testslots_timeout.py` (new): with every permit held by another process for longer than a test's `timeout` marker, the test
   runs after the grant and passes with its queue wait recorded, and a wait past the limit is `not started`, status 75; it is first run
   against the parent commit to confirm the nesting order and see it fail with a timeout. Proves: queueing on a loaded machine is not
   a test failure and cannot hide one.
1. **Model and config.** New `src/poolhouse/testfarm/model/` (schema, parser, strict validator, ids, exit codes, dataclasses),
   `poolhouse-test init|check|list`, and `ph.test.config/units`. Files: `pyproject.toml` (script), `docs/commands.md`, `docs/api.md`,
   `tests/api-signatures.json`, `tests/test_layers.py` (L1 is standard library only). Tests: `tests/test_testfarm_config.py` (every key,
   every hostile file of A14), `tests/test_testfarm_layers.py` (check A, partial), `tests/testfarm_project.py` (the generator, check C).
   Proves: any project describes itself through LIST, RUN and DEPS, and a bad file is refused with a sentence.
2. **Admission.** `git mv` `scripts/testslots{,_policy,_rpc,_runner}.py` and `testqueue.py` to `testfarm/admission/` (and
   `testslots_pytest.py` to the pytest preset's helpers; `AdmissionUnreachable`, imported today from the confinement file `test_kernel_endpoint.py`, moves with the admission interface), the `Admission` interface, the `DEV_TEST_*` names renamed `TESTFARM_*`
   (`tests/conftest.py` and `scripts/test` updated in the same commit). Tests move: `tests/test_testslots*.py`; new
   `tests/test_testfarm_admission.py` (a neutral command under the broker). Proves: a general command is admitted like a pytest run,
   the nested-run refusal holds, and the fixed cost of `scripts/test` is unchanged (`tests/test_runner_overhead.py`).
3. **Local run core and Poolhouse's file.** Plan, shard, schedule, merge, report with the time accounting of 5.10, exit status, the
   `local` transport and the executor without confinement (local only), `poolhouse-test run|plan` for any LIST and RUN,
   `ph.test.run/plan`; the `pytest` and `files` presets and the pytest result plugin; the first `testfarm.toml` of Poolhouse
   (`preset = "pytest"`, the sets of the tiers). Files: `testfarm/{core,transports/local,presets}/`, `testfarm.toml`,
   `tests/test_testfarm_run.py`, `test_testfarm_plan.py`, `test_testfarm_presets.py`, `test_testfarm_timeouts.py` (a neutral unit
   queued behind held permits with a small timeout passes and reports `queued`; a unit that runs past its timeout fails with
   `timed out`). Proves: a shell-script project and a pytest project run in parallel locally and give one report and one status; the
   plan is deterministic; the exit-code table; contract fuzz (check G); in this repository `scripts/test` on the moved pieces and
   `poolhouse-test run` give the same result.
4. **Jobs, history, classes.** Move `testjobs`, `testclass`, `testprelude`, `testphases`, `testhistory`, `testdurations`, `testorder`,
   `testtime` into the core. Tests move: `tests/test_testjobs*.py`, `test_testclass.py`, `test_testphases.py`, `test_testorder.py`,
   `test_test_time.py`. Proves: a second run of the same argument vector and tree follows the first.
5. **Reuse and selection.** Move `testreuse_*` and `testselectors` into the core with the generalised key (declared DEPS, step keys,
   tool probes, `reads` and `unstorable` from results); move the Python import closure and the recorder plugin into the pytest preset's
   helpers; replace `affected.py` and `affected_rules.py` by selection over dependency sets, the Poolhouse-specific string rules by
   `deps.globs` and `deps.everything` in Poolhouse's file, and `scripts/test quick` by `run --affected` (testmon, `record` and
   `.testmondata` are deleted in this step because `quick` no longer uses them). Store entries written under the old key are not
   read (nothing is preserved, no migration). Tests move: the 21 `tests/test_testreuse*.py` files, `tests/test_affected_*.py`; new
   `test_testfarm_reuse.py` and `test_testfarm_select.py` on neutral projects (a non-Python project reuses across an edit of an
   unrelated file; the canary catches a poisoned entry). Proves: reuse and affected selection with no Python knowledge in the core.
6. **Steps and the cargo preset.** Core `[[step]]` (key, cache, shared, admission, outside the timer, prune); the `cargo` preset helpers
   by moving `testunits.py` and `testartifacts.py`; `tests/native_cargo.py` calls the preset; Poolhouse's node and app builds stay in
   `scripts/testnode.py` and `testapp.py`, declared as Poolhouse's steps. Tests: `tests/test_testunits.py`, `test_testnode*.py`,
   `test_testfarm_steps.py` (a neutral step cached by an input key, skipped when unchanged, outside a unit's timeout). Proves: the build
   handling that has landed is generic and its timeout and lock behaviour are unchanged.
7. **Command units and the gate.** `[[unit]]` tables; move `gaterun.py`; `gatescope.py` becomes dependency sets. Tests:
   `tests/test_gaterun*.py`, `tests/test_gates_incremental.py`. Proves: the gate's steps are reused by input hash.
8. **Pool transport, executor with confinement, one wire.** First the written break-it pass on section 10 by a non-author, which blocks
   the rest of this step. Then: the generalised `/shards/v1` routes and caps, `testfarm/transports/pool{,_server}.py`, the executor
   with the deny-default profile, the allowlist environment, the sanitiser, the id grammar, the tree hash over the transported set, the
   sender-keyed step directories, the `remote` kind in the store, `admit()`. The daemon mounts the server by name. Everything the
   generalised path replaces is deleted in this same step, because two ways of sending a shard cannot rest side by side:
   `fleet/shard_{routes,host,store,spec,tree,run,exec,result,split}.py`, the node carrier's client and the carrier of a pool in dev mode
   (`testfarm/client.py`, `dev.py`, `farm.py`, `ledger.py`; the report moves to the core), `scripts/test_on.py`,
   `poolhouse-test-devices` (the `pyproject.toml` script and the `docs/commands.md` row; `poolhouse-test devices` replaces it),
   `poolhouse.api.remote_tests` (`ph.test` is the facade), and the `--split` flag (the planner always splits); `scripts/test --on` now
   calls `ph.test.run`. The old tests are deleted with their code or moved: `tests/test_dev_shards.py`, `test_dev_shards_hostile.py`,
   `test_shard_limits.py`, `test_redteam_test_shards.py`, `test_testfarm.py`, `test_testfarm_nodes.py`, `testfarm_kit.py`,
   `testfarm_tree.py`, `dev_pool_kit.py`. New: `tests/test_testfarm_pool.py` (two loopback daemons, a neutral project),
   `test_testfarm_confinement.py` (a sent tree whose scripts try to read `~/.ssh`, write outside the scratch tree, open a non-loopback
   socket, set `DEV_TEST_WRITE_DENY=0`, all refused), `test_testfarm_attacks.py` (every row of 10.8 that a test can reach). The Rust
   node carrier stays in the tree until open question 1 is answered; it is unreachable from `ph.test` and `scripts/test` and is deleted
   in Phase C. Proves: one wire, any command, hostile inputs refused, a vanished device gives 70 with no re-run.
9. **Fail-first.** `failfirst run|check`, the evidence entries, `Fail-first-exempt:` parsing, the pytest preset's `def` digests. Tests:
   `tests/test_testfarm_failfirst.py` (a generated git repository: a new test that fails on the base passes the check; one that passes on
   the base is reported; an exempt line satisfies; a refactor with none passes). Proves: the policy line is checked by code.
10. **The second-repo test** (section 13, last paragraph) and the read-confinement check B. Needs steps 1 to 9. Proves that a repository
    that is not Poolhouse shards and reuses with a few lines of config.

**Phase B: Poolhouse's scripts as config**

11. **`scripts/test` thin.** Poolhouse's `testfarm.toml` is completed (section 8: labels, steps, hooks, `everything`, the seed file), the
    `scripts/test` front is reduced to the call above with the tier spellings kept, and `docs/test-execution.md`,
    `docs/test-reuse.md`, `docs/test-farm.md`, `docs/development.md` and `docs/commands.md` are rewritten to the new locations. Tests:
    `tests/test_testfarm_dogfood.py` (checks D and E); the existing `tests/test_test_*.py` that exercise `scripts/test` pass unchanged.
    Proves: Poolhouse runs its own tests through the public interface, and the report, jobs, tiers and refusal lines are as before.
12. **CI and hooks.** `.github/workflows/ci.yml` shards call `poolhouse-test plan --shards 8 --index N` and `run --shard N/8`;
    `poolhouse-test record --results` rewrites the seed file; the pre-push hook and the `gates` job call `failfirst check`; then
    `scripts/ci_shards.py` and its tests are deleted (`tests/ci-durations.json` stays as the seed). Proves: CI uses the one planner and
    fail-first is mechanical.

**Phase C: deletions that wait for a replacement already in use**, each in its own commit, each when nothing calls the removed file
(`tests/test_wiring.py`):

1. The `pytest-testmon` dependency of the `test` extra and its mentions in `docs/development.md`, once step 11 shows no caller.
2. If open question 1 is answered "the Rust node carrier goes": `app/poolhouse-node/src/shard/`, `app/poolhouse-node/tests/shard.rs`,
   the `shard_*` member operations, the grants `shard_call` and `shard_consent`, their rows in `docs/node.md`, `docs/limits.md`,
   `contracts/` and the allowlists, and `testfarm/consent.py`; the allow list lives in the pool transport's `allow`. Otherwise the node
   binds to the same server module and keeps no executor of its own.
3. `docs/design-test-selection.md` is edited to say what it still states (the analyzer soundness rules and the measurements) and to drop
   the sections this design replaced. `HANDOFF.md` is pruned in the same commit as each step.

Every step's commit message carries the `Fail-first:` lines or `Fail-first-exempt:` lines; a move is exempt with the reason "pure move".

## 15. Numbers and their sources

No number is the runner's own. Defaults carried over from the current code keep their rows in `docs/limits.md` (the owner, in source)
under the module that holds them after the move; a number with no source is derived from the device or removed.

| Name | Value | Source |
|---|---|---|
| capacity | logical CPUs less the measured outside load and the user's `reserved_cores` | the OS (CPU count), a load sample, the user's setting with no default |
| automatic workers | `sqrt(estimated seconds / measured start-up seconds)`, between 1 and the capacity | the minimum of start-up times workers plus work divided by workers; start-up from the recorded `setup` seconds |
| background class | the project's `[schedule] background_after` | the project's file, no runner default |
| aging, backfill | aged by the estimated work ahead; backfilled when it ends before the head's estimated start | the history's estimates (5.8), no constant |
| canary rate | the project's `[reuse] canary` | the project's file, no runner default; Poolhouse's file sets it |
| single-flight wait | while the claim holder is alive | the OS's process liveness, no time |
| hang guards | no-progress stretch: longest recorded silence or measured round trip times `silence_margin`; total: recorded median times `total_margin` | the history and the measured round trip; both margins are the project's, no runner default |
| step build deadline, slots, idle, prune | the step's `timeout`, else the hang guards; its `slots` or the free slots; `keep` or least recently used on disk need; no interval | the project's file, or the measured free slots and free disk |
| history sample | the median of the recorded samples | no clip |
| fixed overhead limit | 5 s or a quarter of the wall time | AGENTS.md section 5 |
| units per shard, slices per run | no bound of the runner's own | units: the receiver's request-size bound (10.6); slices: the plan (targets times `slices_per_target`), or the project's `slice_count`; the tool refuses what it cannot take |
| shard deadline on a device | the largest of the units' timeouts from the project's file, else the no-progress guard and the recorded-typical-times-margin total (5.10) | replaces `shard_spec.MOST_SECONDS` (3600 s), which has no source |
| packed tree, unpacked tree, members | shares of the receiver's free scratch disk and free inodes, divided by the shards it holds | replaces `shard_tree.MOST_PACKED`, `MOST_UNPACKED`, `MOST_MEMBERS` (24 MiB, 256 MiB, 20,000), which have no source |
| runner output, tee, result, tail, failures, message, tests, junit | shares of the same free scratch disk, divided by the shards held | replaces `shard_run.MOST_LOG`, `MOST_TEE`, `shard_routes.MOST_RESULT`, `shard_result.MOST_*`, which have no source |
| shards held, per sender | the device's free slots at admission, and the free disk (10.6) | replaces `shard_store.MOST_KEPT`, `MOST_OWNED` (8, 4), which have no source |
| idle upload, kept shard, grace before kill | the receiver's admission clock: an upload idle for the sender's own poll interval times missed polls; a kept shard until its result is fetched or the disk is needed; grace is the process group's own exit | replaces `shard_store.IDLE_S`, `KEPT_S`, `shard_run.GRACE_S` (600 s, 24 h, 30 s), which have no source |
| poll, lost device | none: results are reported on the stream; a failed or silent stream is the loss | the transport's own failure (reset, the OS's keepalive timeout); replaces `dev.POLL_S`, `dev.LOST_S` (2 s, 45 s) |
| shard id | 128 random bits | a security floor: guessing or colliding is out of reach (a row of `docs/limits.md`) |
| id length | `PATH_MAX` of the sender and the receiver | the operating system headers |
| arguments before `{idfile}` | `ARG_MAX` of the platform | `sysconf(SC_ARG_MAX)` |
| exit statuses | 0, 1, 4, 6, 70, 75, 124, 130 | section 11 |
| queue limit | the project's `[schedule] queue_wait`, else none | the project's file, else the no-progress guard; replaces `testslots.WAIT_S` (3600 s) |
| unit timeout in Poolhouse's file | 120 s | `pyproject.toml` `timeout`, the owner (`docs/limits.md`, `pytest timeout`) |

`unrecorded_max` (the cap on a unit with no record) is the project's setting with no runner default, and the sample clip and `JOBS_DIVISOR`
are gone. No row of this table has "the owner's value in code" as its source: a number is measured, set by the project's file with no
runner default, or has a row in `docs/limits.md` with its protocol, operating system or security reason (the shard id).

The Rust host's constants (`MOST_RUNNING` 2, `MOST_OPEN` 6, `MOST_KEPT` 40, `GRACE_S` 120, `KILL_AFTER` 45) have no source: the single
receiver derives them (running shards from the device's free slots, open shards from its free disk, the kill grace from the process
group's own exit) and `docs/limits.md` is updated in step 8.

## 16. Open questions

Product choices this document cannot settle from the code and documents. Each names the option it would take.

1. **Which pool stack carries the allow list of a pool in prod mode, and does the Rust carrier stay?** Pools in dev mode are served by the Python
   daemon; pools with charters, grants and phone approvals are served by `poolhouse-node`, whose `shard_consent` grant is the only
   place a person's signed yes for "this sender may run code here" exists. The design makes one server and one wire. Option A: delete the
   Rust shard host and its two grants and keep the allow list as a device setting changed by `poolhouse-test allow` (a typed yes at a
   terminal), valid for every pool mode (`docs/agent-policy.md` says there are no production users and pools are not preserved). Option
   B: keep the grant as the way the allow list is written and have the node bind its member operations to the same Python server. The
   design takes A unless the owner wants a phone-signed grant for it.
2. **Default of `auto`.** In a pool in dev mode, `poolhouse-test run` sends the tree to accepting members by default (section 6.1),
   because the owner said the runner uses a pool when there is one. Does the owner want the first send to a device to print a visible
   one-line notice, or none (a notice adds no step but is noise)? The design prints none and lists the devices used in the report.
3. **Dependencies on a device.** A device runs a project only with what it has installed (a Python with the project's test
   dependencies, a toolchain). The design lets the project declare steps (an environment command cached by a key) and `[requires]`
   probes, and never installs anything on a device on the runner's own initiative. Should a step be allowed network on a device (to
   fetch dependencies) when the receiver's policy permits, given the default is loopback only? The design keeps it off by default and
   makes the policy a device setting; whether the pool's dev mode should turn it on is the owner's.
4. **How a unit that needs a model or the GPU uses the lease through the runner.** The runner can hold the lease for the unit's
   invocation (`lease = ["gpu"]`) and export its id, but `poolhouse-serve up` is itself a lease from the broker, so a unit that calls it
   while the runner holds one would nest and deadlock, which the broker refuses for CPU leases today. Option A: the runner holds the
   `gpu` lease and the serve path recognises a held lease of the same holder through an exported lease id (a change in `poolhouse.serve`).
   Option B: the label only serialises (`serial = "gpu"`) and the unit takes its own lease. A is safer for the floor (one piece of
   work on a device's compute at a time, enforced for every project); B is smaller. The design takes A and needs the serve change in
   step 3.
5. **Remote passes and landing.** `design-test-selection` 6.5 makes a device's pass advice and stores it as skip evidence only when
   corroborated. This design keeps that and adds `--corroborate`. Whether a project may declare a device (the owner's own) trusted
   enough for its pass to be stored as skip evidence for local runs is a trust decision this document does not make; the default is
   never.
6. **Names.** `poolhouse-test` and `ph.test` follow the existing `ph.test` and `poolhouse-test-devices`; `testfarm` stays as the
   package and the config's file name. Does the owner want the file called `testfarm.toml` or `poolhouse-test.toml`? The glossary has no
   word against either; the design uses `testfarm.toml`.
7. **Splitting a long unit.** Decided: the runner splits any unit longer than the ideal shard by id, automatically, from recorded
   durations (the two files of 814 s and 611 s in `docs/design-test-selection.md` section 3.5 are split, not rewritten). The cost
   of module and session setup per shard is measured and counted by the planner; order-dependence in a split file is found by
   running its shards in a different order than the file's own and is reported as a defect in the test; a unit with no ids from
   LIST is not split and the report says so. Nothing remains open.
8. **ssh.** The transport interface leaves room for it. Whether it is built, and whether it needs the same confinement on a device
   that is not a pool member, is deferred; no step of the plan depends on it.
