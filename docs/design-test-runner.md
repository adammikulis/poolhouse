# A test runner and sharder that any project uses

Status: design. Nothing here is built. Claims carry a tag: **[code]** read in this tree (`0.3dev` at `ab63ced7b`), **[doc]** stated in
a document of this repository, **[report]** reported by workers and not re-measured here, **[probe]** run in the second break-it pass
(`docs/design-test-runner-breakit-2.md`) on macOS 26.6 arm64 with Python 3.14.7, git 2.51.2 and expat 2.7.4 at a load average of 87 to 93, so
a time is an upper bound and a byte count or a pass or fail is not, **[plan]** a proposal. The owner ordered it: one general runner and
sharder, a feature of the
Poolhouse library, that any repository that installs Poolhouse uses for its own tests; Poolhouse's own repository is the first project
that uses it, through the same public interface and nothing private. `docs/design-test-selection.md` is the design of the selection
and evidence rules for Poolhouse's tests; this document generalises its mechanisms (sections 4, 5 and the shard description of 6.1 to
6.5 become the analyzers, the soundness rules and the pool transport below) and replaces its build plan.

Two written break-it passes by a non-author, `docs/design-test-runner-breakit.md` (findings F1 to F30) and `docs/design-test-runner-breakit-2.md`
(findings N1 to N18), are absorbed in the text below. Section 14 lists, for each build step, the findings it absorbs and the test that proves
each one. The findings that decide who is trusted (N1 to N6: the project's identity, git, the person's yes, the Owner zone, the scratch copy and
the hang guard) are absorbed in sections 3.5, 4.8, 4.9, 5.10 and 10.2, and step 3 builds the local run, `own` and the settings to them; step 8
needs a non-author's re-read of sections 4.8, 4.9, 5.10, 6.3, 6.4, 6.6, 10.2, 10.3 and 10.6 before it is built.

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
- **The core** (section 5) plans, shards, schedules, merges, reports and sets one exit status; it holds the reuse store, the admission of
  compute slots, the jobs, the dependency sets for selection, and fail-first. Dividing a suite into shards is a pure function of the listed
  ids, the config and the seed file; which machine runs a shard, what the store holds and what history says never change a division.
- **Transports** (section 6) take a shard to somewhere it runs: this machine's processes, a device of a Poolhouse pool over the pool's
  mutual-TLS routes, later ssh. The pool is one transport plugin. There is one sharding path and one wire protocol; what exists today as
  a node carrier, a pool carrier in dev mode, `--split` and a CI splitter is folded into it (section 2, section 14).
- **The surface** (section 3) is `ph.test` in Python, the `poolhouse-test` command (also `poolhouse test`), and the `testfarm.toml`
  schema. Everything else is internal. `pip install poolhouse` gives a repository all three.
- **Poolhouse's repository** keeps its tiers, markers, node build, gates, agent rules and confinement in its own `testfarm.toml` and
  its own scripts. `scripts/test` becomes a few lines that call the public command (section 8). What only matters for developing
  Poolhouse never reaches a repository that imports it (section 3.3).
- **Where tests run is the user's choice and never the project's.** The target comes from the user's own settings and the `--on` flag and is
  `local` unless the user sets another. A project file lists what it needs (tools, labels, files, requirements) and cannot name a device, a
  pool or a place to run. A tree is sent to a device only for a project the user has named as their own, and only to the devices the person's
  yes named (sections 4.8 and 6.1).
- **Handed code runs confined, in a copy.** Local runs, LIST, steps, hooks and units of a project the user has not named as their own run under
  the same sandbox a sent tree does, in a scratch copy of the transported set. A project is named by what the person pointed at, read from files
  and without git (section 4.9). Naming a project is one yes per project and is the only step this adds; the yes comes only from evidence the
  host supplies through one gate, never from a terminal (section 4.8); Poolhouse's own checkout is named by its device setup (section 10.2).
- **Trust is zones with one-way boundaries** (section 3.5). The executor never reads or writes by name a path a test can write; a
  configuration is parsed once, from the bytes that were hashed; the Owner zone (keys, store, settings) is denied to every child, named or not;
  the store signs what it holds under a key per project and records the trust domain of the run that wrote it; git runs only confined, from a
  closed list; nothing that grants authority travels in an environment variable.
- **Time is accounted, not mixed.** A unit's timeout counts from the moment it starts running; queue wait and build time are reported
  apart (`queued`, `built`, `ran`), and a unit that never started because the machine was overloaded is `not started`, not failed
  (section 5.10).
- **Security** (section 10): the runner runs code on other devices and runs handed code on this one, so identity is the pool's, the
  receiver's policy decides what runs, confinement is the receiver's own and nothing the executor itself does steps around it, every tree
  and result is checked against a hash the sender computed (which proves freshness, not that a device ran the tree: a device's pass is advice
  and is stored only when a second source agrees), text from a device or a unit is data inside frames the sender prints, and every bound has a
  source. The attack list is section 10.8.

## 1. The contract

Numbers in sample commands and sample output lines in this document (`queued 12 s`, `slice 5/8`, `--shards 8 --index 3`, and the like) show
the shape of a line or an argument; they are not values the runner uses. A real run prints its measured numbers, and a value that
configures the runner is either shown as generated by the tool or has its source beside it. The sample files below leave out the keys
`poolhouse-test init` writes from a project's measured first runs, `[schedule] silence_margin` and `total_margin` and `[reuse] canary`: a file
without the margins is refused (5.10) and a file without the third runs with reuse off (5.3).

A project supplies three things and the runner needs no more.

1. **LIST**: a command that prints the test ids, one per line on stdout (`lines`), or one JSON object per line (`jsonl`) with the
   fields `id` and, optionally, `labels`, `deps`, `group`, `def` and `seconds`. An id is opaque text under the grammar of section 10.4
   (UTF-8 in normal form C, no control, format, separator or private-use characters, not beginning with `-`). A line that does not begin with
   `list.starts` and end with `list.ends` (two literal strings, default none, so every non-empty line is an id) is ignored and counted, and
   the run prints `ignored N lines` whenever N is not zero: a tool's banner or summary is filtered by the project, not by the runner, and a
   filter that removes every line is seen. LIST is the project's code and runs in the project's trust state (4.8): confined for a project the
   user has not named. A LIST that exits nonzero, is killed or is ended by its hang guard is status 4 naming the exit code and the first
   sanitised line of its error output; a LIST that prints no unit is status 4 unless `[list] allow_empty = true`. `seconds` is a finite number
   from zero up to the wall time of the invocation that produced it (10.4).
2. **RUN**: an argument vector with the placeholders `{ids}` (the ids as separate arguments, after `run.ids_after` when the project names one,
   such as `--`), `{idfile}` (a file of ids separated by NUL bytes, for more ids than the invocation's argument space), `{result}` (a file
   the command may write: a path the executor reserved and holds open, 6.6) and `{extra}` (arguments a named set adds). It is an array,
   never a shell string; a project that wants a shell writes `["sh", "-c", "..."]` itself. The command signals per id by its **results**
   format: `exit` (the exit code of an invocation applies to every id in it; the default, and with it a batch is one id), `jsonl` (one
   `{"id", "status": "pass"|"fail"|"skip", "seconds", "message", "reads", "unstorable"}` per line in `{result}`) or `junit` (a junit file in
   `{result}`; ids are matched through `run.junit_id`, a template over the `classname` and `name` attributes). An id that a result file does
   not mention is **not reported** and counts as failed, loudly; nothing is assumed to have passed. A pass in `exit` format is shown and not
   stored, because an exit code cannot tell a pass from a skip (5.3).
3. **DEPS** (optional): which files an id depends on, given as globs per id pattern (`deps.globs`), or by a command that prints
   `jsonl` rows `{"id", "deps": [...]}` (`deps.command`), or in LIST's rows. No DEPS means an id depends on the whole tree: it is
   reused only for an identical tree and is always affected, and the report says so once. A preset that cannot know a unit's dependencies
   gives it the whole tree as well; only a project's declared globs narrow it. `check` fails a declared set that leaves out the unit's own
   file and a glob that matches no file, because an empty set is not an unknown. DEPS is a statement about files; the runner never reads a
   language.

Two optional words complete the model. A **group** rule (`list.group`, a separator such as `::` that ends the group part of an id, or a
`group` field in LIST's rows) names the scheduling and reuse granule: ids with the same group always run together, are keyed together and
are listed as one **unit** (a pytest test file, a cargo test target); with no rule every id is its own unit. A **label** is a word on a unit
that the project's `[label.NAME]` table gives a meaning (section 4.3): a lease, a serial group, a required platform, "never leaves this
device", "never reused". Globs in this schema (`deps.globs`, `tree.include`, `label.match`) use `*`, `**` and `?` only, matched by one
non-backtracking matcher the core implements; no key takes a regular expression.

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
       composes L3 and L2; testfarm/names.py, the table that loads a preset, a transport, a resource provider or an event sink by name
 ----------------------------------------------------------------------------------------------------------------
 L3  plugins (each imports L2 and L1 and, where noted, the named Poolhouse modules; none imports another plugin)
       testfarm/presets/      tool presets: pytest, cargo, npm, files   (TOML tables, plus the helper programs a table runs)
       testfarm/transports/   local, pool (client and server; also the pool modules named below), later ssh
       testfarm/resources/    resource providers: the lease table of a device's pool (also the lease modules named below)
       poolhouse.workspace    board event sink, task evidence, owner identity, person gate   (named in the user's settings; reads the store)
 ----------------------------------------------------------------------------------------------------------------
 L2  testfarm core                                           imports L1 and the L0 list
       select  divide  assign  schedule  merge  report  exit status
       reuse store (signed, with a trust domain)   admission (slots, resources)   steps   jobs   history   failfirst
       executor and its zones (parser worker, child sandbox)   user settings   the runner's state location
 ----------------------------------------------------------------------------------------------------------------
 L1  testfarm model                                          standard library only
       config schema, parser and validator; Unit, Label, Plan, Shard, Result, Report; id grammar; exit codes
 ----------------------------------------------------------------------------------------------------------------
 L0  poolhouse core-layer modules the runner may import (an explicit list, pinned by the layering test):
       files, lock, platform, redact, sandbox, errors, log
```

Rules, each a test:

- The core (L2) imports no preset, no transport, no resource provider and no plugin; it reaches them by name through the L4 table of
  names. L1 imports nothing of Poolhouse. The L0 list names no pool home, no state root and no credential module: the runner's state
  location (below), the child environment (10.5) and the lease vocabulary (4.3) are the runner's own or a provider's.
- A preset imports only L2 and L1 (and the helper programs a preset runs are Python modules under `testfarm/presets/<tool>/` with the
  same rule). A transport imports L2 and L1; the pool transport also imports the pool client modules it talks through (`fleet.remote`,
  `fleet.discovery`, `fleet.membership`) and its server half the membership and feature modules it reads (`memberships`, `features`); the
  layering test lists them by name. A resource provider imports L2, L1 and the lease modules it adapts (`poolhouse.leases`,
  `poolhouse.serve.grant`). Nothing else under `testfarm` imports `scripts/`, `tests/`, `poolhouse.board`, `poolhouse.serve` or
  `poolhouse.workspace`.
- `poolhouse.workspace` imports `testfarm.store` (to read evidence) and is loaded as a sink and as the person gate by name from the user's
  settings; `testfarm`
  never imports `workspace`.
- The daemon mounts the pool transport's server routes by name through `poolhouse.optional` (`tests/test_layers.py` prescribes that
  reach for upward edges); `fleet/shard_*.py` and their `KNOWN` entries disappear.
- Importing `poolhouse.testfarm` loads no `fleet`, `board`, `numpy` or `pytest`: a test pins it, as
  `test_a_client_does_not_load_the_fleet` does, and `tests/test_runner_overhead.py` bounds the CPU seconds of the command's start
  (AGENTS.md section 5: fixed overhead above 5 seconds or a quarter of the wall time is a defect).
- `testfarm` stays in the `tools` layer of `tests/test_layers.py` and is in the wheel (`testing` is the package that leaves it).
- **The runner's state lives outside the pool's secret root.** The store, `{cache}`, `{shared}`, scratch trees, job records, the history
  and the store's keys are under the platform's state location for the runner (a `testfarm` directory under it), never under
  `~/.poolhouse`. The state location holds two sibling directories: `owner/` (the store, the master and project keys, the history, job
  records and the slots directory) and `work/` (scratch trees, `{cache}` and `{shared}`, everything a child is given). `owner/` is in the Owner
  zone and `work/` is not, so no allow rule lies inside a deny. The allow list that gates remote execution is kept with the pool's settings
  and is neither readable nor writable by any child. A test pins that the state path is not under the pool's home and that every sandbox
  profile names `owner/` by exact path.
- **The Owner zone is a list of paths.** The state location's `owner/` directory, the settings file, the user presets, the pool's home and allow
  list and `~/.ssh` are returned by one function, `ph.test.owner_paths()`. The sandbox profile of every child names them by exact path (3.5,
  rule 4), and the host adds the same list to its edit and shell guards (4.8).
- **One way to start a process, explicit limits on every call.** Every process the runner starts (LIST, `deps.command`, hooks, steps, units,
  the control units of 6.4, the Parser worker, the `[requires]` probes, `prune`, `git`, the launcher) is started by one spawn function in L2. It
  takes the hang guard (5.10), the sandbox profile, the exact list of descriptors the child keeps and explicit `sandbox.Limits` as required
  arguments: no call inherits the defaults `poolhouse.sandbox.policy.Limits` carries, a wall time and an output size with no source here. A test
  fails when any code under `testfarm` starts a process another way or calls the executor without limits (check A).
- **Two modes of the L0 `sandbox` module are the runner's to ask for:** an open profile that denies exact paths (the Own child's narrow deny, 10.2)
  and `Net.loopback_except(ports)`, loopback with the named ports denied (10.2). Each is added to `src/poolhouse/sandbox/` with its own tests in
  the step that first uses it (step 3(b)).

### 3.2 Public and internal

| | Public (documented in `docs/api.md` and `docs/commands.md`, pinned in `tests/api-signatures.json`) | Internal |
|---|---|---|
| Python | `ph.test`: `config(root)`, `units(root, select)`, `plan(root, on, select, cover)`, `divide(root, shards, index)`, `run(select, *, root, on, reuse, test_timeout_s, unit_timeout_s, cover)`, `affected(root, base)`, `devices()`, `failfirst(root, base)`, `owner_paths()`; the frozen dataclasses `Config`, `Unit`, `Plan`, `Shard`, `Device`, `Result`, `Failure`; the protocols `Transport`, `Target` and `PersonGate` | every other name under `poolhouse.testfarm` |
| Command | `poolhouse-test init, check, list, plan, run, affected, devices, submit, status, wait, result, cancel, allow, deny, policy, own, target, reuse, failfirst, record` | the helper programs presets run; the receiver's server entry |
| File | `testfarm.toml`, `schema = 1`; the preset tables; the user's settings file; the result `jsonl` and the LIST `jsonl` row shapes | the store layout, the wire routes, the state directory |
| Wire | none: the routes are versioned and checked by a handshake (section 6.2) but carry no promise to a third party | all |

Poolhouse's own repository uses the public column only. The dogfooding rule is a test (section 13, check E): nothing in `scripts/`,
`tests/` (other than the runner's own tests and the named integrations), `.github/` or `docs/verify_release.py` imports a name
under `poolhouse.testfarm` that `ph.test` does not export, and no script reads the store's files.

`ph.test` exists today (`poolhouse.api.remote_tests`: `devices()`, `run(tier, *, on, root, files, timeout_s)`, `Device`, `Result`).
The new `run` takes a selection (a set name such as `@fast`, ids, or globs) in place of `tier`, `on` takes `local`, `auto`, `pool`, a
device name or a fingerprint (its default is the user's setting, `local` when there is none, 4.8), and `Result` gains `reused`, `not_run`
and `devices`. `own`, `allow` and a `target` or `on` that widens (4.8) ask a person through the host's `PersonGate`; no function of `ph.test`
takes an answer, and a Python call cannot answer for a person. Before 1.0 a minor release may change the surface (`docs/api.md`,
"Versions"), and the change is a deliberate edit of
`tests/api-signatures.json`.

### 3.3 Development rules stay in development

Nothing that matters only for developing Poolhouse is in the library. Each item below is today in `scripts/` and is in Poolhouse's own
`testfarm.toml`, in its scripts or in the user's settings afterwards; the library has no code for it and no default.

| Poolhouse development rule | Where it lives afterwards |
|---|---|
| An agent may not run the whole suite (`testscope.py`, the `CLAUDECODE` and `CODEX_*` markers) | a `before_plan` hook script in Poolhouse's `scripts/` named in its `[hooks]`; the runner reads no agent marker |
| The real state root is write-denied, the Seatbelt confinement kernel, the container run | a `launcher` Poolhouse's config names for its units (section 4.7); the runner only wraps the argument vector |
| The node binary and the native app tests are built before the run, with Poolhouse's member list and fixtures | `[[step]]` tables whose commands are `scripts/testnode.py build` and its siblings |
| `heavy-modules.txt`, `seatbelt-modules.txt`, the redteam, `live_api`, `live_net`, `real_model` markers | `[label.NAME]` tables and the pytest preset's marker-to-label rule |
| The lease vocabulary (`cpu_slots`, `memory_mb`, `gpu`, `model_slot`, `claim`, the node's lease table, `HEAVY_MODULES`' heavy lane) | the resource provider Poolhouse's setup names (4.3, 5.5); the core knows resource names and amounts and nothing about what they are |
| The `Fail-first:` report line and the `Fail-first-exempt:` commit line of `docs/agent-policy.md` | `[failfirst]` keys in Poolhouse's file; the library has no default line (5.9) |
| The split between an agent's store and the owner's (`DEV_TEST_REUSE_DIR`) | the trust domain, the key per project and the denial of the Owner zone to every child (5.3); the library has no environment name for it |
| Task evidence, landing and release (`workspace.testruns.verified`) | readers of the store in `poolhouse.workspace`, which ask for domain `named`, kind `local`, the exact tree and a run by a session other than the task's author (5.3); the store has attributes (`domain`, `kind`, `by`) and no workflow |
| A person's yes for `own`, `allow` and a widening target (the structured approval question, `person-consume`) | the host's `PersonGate` in `poolhouse.workspace` (4.8); the library asks it and reads no terminal, agent marker or environment name |
| The edit and shell guards of the agent harness | the host's guards read `ph.test.owner_paths()` and refuse an agent's write or command that names those paths (4.8) |
| The budget, red-team coverage, command reference and layer checks (the gate) | command units (section 5.7) in a set named `gate` |
| The ratchet on the full tier's time, `scripts/test heavy` | scripts that read `ph.test` results and the public history, not part of the library |
| The queue doctor, reaping the node processes Poolhouse's tests start, the board events of a canary mismatch | `[hooks]` and the `workspace` sink named in the user's settings |
| Worktrees, claims, the landing steps, hooks that refuse commits | not the runner's business at all |

A check (section 13, check F) runs the same generated project with and without every Poolhouse development marker in the environment
(`CLAUDECODE`, `AI_AGENT`, `CODEX_THREAD_ID`, `POOLHOUSE_AGENT`, `POOLHOUSE_NONINTERACTIVE`) and requires the identical plan and result.

### 3.4 Words

The runner uses the glossary's words (`docs/glossary.md`): a **device** runs a shard, the **pool** is the set of devices, a **member** is a
device in it, a **lease** is permission to use a limited resource, **capacity** is what a device offers, and **node** appears only as the
software `poolhouse-node`. It uses no word that `docs/glossary.md` bans. The words this design adds are **unit** (the
scheduling and reuse granule), **id**, **shard** (the part of a run sent to one target), **target** (a place a shard can run),
**transport**, **preset**, **step**, **set** (a named selection, where the old tiers were), **label**, **DEPS**, **analyzer**, **named**
(a project the user has said is their own, 4.8), **domain** (the trust a run had when it wrote a store entry, 5.3), **zone** (3.5), **gate**
(the host's `PersonGate`, through which a person's yes arrives, 4.8) and **control unit** (a generated unit the executor runs to test itself
and its profile, 6.4; the glossary's **sentinel** is the open-join safety and is not used for it). The
package keeps its name `testfarm`; `farm` is not a glossary word and is not banned. `tests/test_glossary.py` covers this document and
the code the way it covers the rest.

### 3.5 Trust zones and one-way boundaries

The structure every security rule below follows. Five zones; data crosses a boundary in one direction and only as bytes, a descriptor or a
row of a closed schema.

| Zone | What is in it | May read | May write |
|---|---|---|---|
| Owner | the files in the user's account that grant or record authority, listed by `ph.test.owner_paths()`: the runner's settings file, user presets, list of named projects, master key, derived keys, store, history and job records (the `owner/` directory of the state location), and the pool's allow list and home and `~/.ssh` | the Runner, for the files it needs | the settings by the CLI after the host's gate said yes (4.8); the Runner's own files; nothing else. No Child, named or not, reads or writes it |
| Runner | the core, the executor, the store writer, admission | the Owner zone; verified bytes held in memory; Child-written data only through a descriptor it opened itself | the store (signed), the executor-owned directory, result descriptors |
| Parser | a long-lived confined worker with the executor's limits: TOML, JSON and XML parsing and the tar unpack | one descriptor of bytes | one descriptor of tag-length-value records (6.6); `tree/` for the unpack and nothing else |
| Child | steps, units, LIST, `deps.command`, hooks, the control units of 6.4 and the `[requires]` probes of an unnamed project and of every sent tree; on a device, always; in a scratch copy | the scratch copy, the toolchain paths the preset names, the executor-owned directory read-only | the scratch tree, the step directory it owns |
| Own child | the same commands of a named project, on this machine only, in the checkout | the user's account except the Owner zone | the user's account except the Owner zone |

Rules, each a test (checks A, H, J and L of section 13):

1. The Runner never writes through, or reads by name, a path a Child can change. Everything it writes for a child (`{idfile}`, argument
   lists, the config copy) is in a directory the Child cannot write; the only thing it creates in a place a Child can write is a result
   file, with exclusive no-follow create through a held directory descriptor, and it keeps the descriptor; everything it reads that a Child
   wrote it reads through a descriptor it opened itself, after `fstat` says regular file, with the size capped on the descriptor. Every
   component of such a path is a descriptor too (`O_NOFOLLOW` covers only the last component), and a link, a FIFO or a device found in a
   walk is recorded by its type and never opened. A local checkout is read as data and never followed out of its root, so DEPS hashing
   cannot be aimed at a private file.
2. A configuration is parsed once, in the Parser zone, from the bytes that were hashed; the validated result (bounded by the receiver's budget
   on a device, 10.6) stays in Runner memory and the copy in the scratch tree is never consulted again.
3. Nothing that grants authority travels in an environment variable or a file a Child can read: permits arrive on an inherited descriptor, and
   a lease is delegated by the broker to a process it identifies by a handle the operating system keeps stable for the process's life, never
   by a process id alone (5.5). The child keeps no descriptor beyond the ones it is given.
4. A Child cannot read or write the Owner zone, named or not. The profile of an unnamed or sent child is deny-default and names its reads;
   the profile of a named child is open with a narrow deny of the Owner paths by exact path, with `(deny file-link)` (10.2).
5. The store holds signed entries with a domain, under a key per project; a reader verifies the signature and filters on the domain and on
   the run's `by` (5.3).
6. Where a tree goes is read only from the Owner zone: the settings and the command line (4.8). A setting or flag that widens it needs the
   host's gate.
7. A project is classified from files, with no git, before anything of it runs, and every `git` call is confined and from a closed list (4.9).
8. Every number the project's file supplies is finite and in range, a hang guard is two margins and a total guard, and no bound is the runner's
   own (4, 5.10, 15).

## 4. The config schema

One file, `testfarm.toml`, at the project root, parsed with `tomllib` in the Parser zone (3.5), strictly: an unknown key is an error that
names the key and the nearest known one, a wrong type is an error, nesting deeper than the schema itself is an error, an integer past the
parser's digit limit is a validation error, and nothing is defaulted from the project's identity. `schema = 1` is required. Paths in the file
are relative to it and cannot leave its directory. Names (sets, labels, steps, units, and the `NAME` of `{step:NAME}` and `{shared:NAME}`)
match `[a-z0-9_-]+`; environment variable names match `[A-Za-z_][A-Za-z0-9_]*` and their values hold no NUL byte; anything else is refused at
validation, on the sender and again on a device. **The project root** is found from the working directory (or the path a command names) by
walking up to the first directory that holds a `testfarm.toml` or a `.git` entry and stopping there: the walk never climbs into a parent
repository, and a stop at a `.git` with no `testfarm.toml` is status 4 with the `init` hint (4.9). **Numbers are finite and in range:**
`silence_margin`, `total_margin`, every `timeout`, `test_timeout`, `queue_wait`, `estimate`, `unrecorded_max` and `keep` are finite and greater
than zero, a margin is at least 1, `slots`, `batch`, `slice_count` and `slices_per_target` are positive integers and `canary` is in (0, 1]. TOML
parses `inf`, `nan` and `1e999` as floats [probe], so the validator refuses each of them, and zero and a negative number, with status 4 naming
the key.
`poolhouse-test check` validates it, runs LIST, and prints the units, the labels, how many units have DEPS, and which steps would run.

### 4.1 Reference

| Table and key | Meaning | Default and its source |
|---|---|---|
| `schema` | the schema version | required |
| `preset` | a tool preset to start from (section 7); any key below overrides it | none |
| `[project] name` | a label in reports only | the directory name; never an identity |
| `[tree] include`, `exclude` | globs of untracked files to send in addition to the tracked ones; globs left out of the tree sent to a device | none; none. The sent set is the tracked files (`git ls-files`, a confined call, 4.9) plus the untracked files a unit's DEPS or `include` names through a glob that is not the whole tree; never `.git`, never a link (6.3) |
| `[list] run` | the LIST argument vector | none: without it the run is split by slice or not at all (5.2, "Three split modes") |
| `[list] format`, `starts`, `ends`, `group` | `lines` or `jsonl`; the literal strings an id line begins and ends with; the group separator | `lines`; none (every non-empty line is an id); each id is a unit |
| `[list] allow_empty`, `timeout` | whether a LIST with no unit is a run; seconds LIST may run | `false`; none: the hang guard (5.10) |
| `[run] argv` | the RUN template | required unless the preset gives it; with LIST it receives ids, without LIST it runs the whole selection once |
| `[run] ids_after`, `refuse_prefix` | an argument placed before `{ids}` (such as `--`); first characters of an id the runner refuses besides `-` (`@` and `+` for tools that read them as options) | none; none |
| `[run] slice` | the RUN-slice template for a tool that cannot list its tests but can run a share of them (`--shard {slice_index}/{slice_count}`); an array of strings with exactly one `{slice_index}` and one `{slice_count}` | none; read only when there is no `[list]` (5.2) |
| `[run] slice_base`, `slice_count`, `slices_per_target`, `slice_results` | whether the tool counts slices from 0 or 1; the number of slices, or `auto`; for `auto`, slices per target; what a slice reports (`exit`, `jsonl` or `junit`, as for `results`) | none, required whenever `slice` is set (a tool's own first index is the project's to say); `auto`; `1` (one slice per target is the least that uses every target); `exit` |
| `[run] results`, `junit_id`, `batch` | `exit`, `jsonl` or `junit`; how junit rows map to ids; ids per invocation | `exit`; `1` for `exit`, all of a unit's ids otherwise |
| `[run] reuse` | `"exit"` lets a project whose results are `exit` store passes | not set: only `jsonl` and `junit` results are stored (5.3) |
| `[run] test_timeout` | seconds one id may run, handed to the tool's own per-test timer by the preset | none |
| `[run] timeout` | seconds one invocation (a unit) may run, from the moment it starts | none: the hang guard (5.10) |
| `[run] env` | variables set for every invocation (values travel in the file) | none; the child environment is built as 10.5 says |
| `[run] launcher` | an argument vector put in front of RUN that can only narrow what the sandbox allows | none; local only (4.7, 10.2) |
| `[run] needs` | the steps every unit waits for; a label or a `[[unit]]` overrides it | all |
| `[deps] globs` | a table `id-glob = [file-globs]`; each set contains the unit's own file, and every glob matches a file | none: the whole tree |
| `[deps] command`, `self`, `timeout` | an analyzer command printing DEPS rows; the files that are the analyzer itself; seconds it may run | none; `self` always joins `everything`; the hang guard |
| `[deps] everything` | a change to any of these globs selects every unit and changes every key | `testfarm.toml` itself, always |
| `[[step]] name, run, inputs, tools, env, slots, timeout, keep, prune` | a build or environment command (4.4) | `slots` and `timeout` below |
| `[label.NAME] match, lease, serial, platform, local, net, reuse, slots, estimate, timeout, needs` | what a label means (4.3); `match` globs ids that carry it | see 4.3 |
| `[set.NAME] labels, not_labels, ids, extra, reuse` | a named selection: `poolhouse-test run @NAME` (4.5) | none |
| `[reuse] key_env, key_tools, canary` | extra key inputs; the sampling rate of canary re-runs, in (0, 1] | none; none; none, and with no `canary` reuse is off and the report says so (5.3) |
| `[failfirst] tests, line, exempt, mode` | globs of test files, a template over `{id}`, `{sha}` and `{error}` for the printed line, the literal prefix of an exempting commit-message line, the mode (5.9) | none; the runner's own line; none (no exempt route); `report` |
| `[schedule] seed, unrecorded_max, silence_margin, total_margin, queue_wait, background_after` | a committed durations file used by `divide` and until history exists; the most seconds assumed for a unit with no record; the margins of the hang guards (5.10); the queue limit; the estimated work after which a run takes the `background` class (5.5) | none; none (the median of the recorded ones, uncapped); none, and both margins required unless every command the runner starts has a `timeout` (the run is refused with status 4, 5.10); none; none; none (every run is interactive unless the person says `--background`) |
| `[requires]` | things a device must have, checked by probes the device owns (6.5) | none |
| `[[unit]]` | a command unit with no LIST (5.7) | none |
| `[presets.NAME]` | a preset defined in the project (7); the table is `presets` because `preset` is the key that names one, and a TOML key cannot be a text and a table | none |
| `[list] glob` | the `files` preset's glob | none |
| `[hooks] before_plan, before_run, after_run, timeout` | commands run on this machine only, never on a device; seconds each may run | none; the hang guard |

The file has no key that chooses a target, a device or a pool, and none that names an event sink: where a run goes and where its endings
are told belong to the user (4.8). Numbers the runner supplies where the file is silent, with their sources, are in section 15.

### 4.2 Units, ids and groups

LIST yields ids; the group rule folds them into units. A unit has: its ids, its labels, its DEPS, its recorded durations, and its key
(5.3). Scheduling, reuse and the report work on units; RUN receives ids. A selector on the command line is an exact id, a glob over
ids, or the name of a unit; `scripts/test all tests/test_x.py` is the last, under the pytest preset's group rule.

### 4.3 Labels: per-unit needs, mapped generically

A label is a word on a unit. Its table maps it to the runner's generic machinery:

| Key | Effect |
|---|---|
| `lease = ["gpu", "memory:8192", ...]` | the unit holds these resources for its invocation, through the admission interface (5.5). A resource is a name and an amount that a **resource provider** named in the user's settings supplies; the core knows names, amounts and whether the provider marks a resource exclusive, and nothing about what a resource is; `check` refuses a name no provider supplies |
| `serial = "NAME"` | at most one unit with this serial name runs at a time on a device (a long serial group, a benchmark) |
| `platform = ["macos"]` | the unit goes only to a target whose platform class includes these |
| `local = true` | the unit never leaves the sender; a restriction, and the only thing a project says about where a unit goes |
| `net = "deny"` or `"loopback"` | the network the unit's sandbox allows: nothing (the default) or this machine's loopback; anything wider is a receiver policy that names destinations, and on a macOS device a sent `loopback` needs the receiver's listed ports (10.2) |
| `reuse = false` | a pass is not stored (a unit that reaches a live service) |
| `slots = N` | the unit holds N CPU slots (default 1) |
| `timeout = S` | seconds the unit may run, counted from the moment it starts (5.10); the largest of a unit's labels applies |
| `estimate = S` | seconds used for planning until a duration is recorded |
| `needs = [...]` | the steps the unit waits for (default: the `[run] needs`) |

Labels come from LIST rows (`labels`), from `match` globs, or from the preset's rules (pytest markers become labels). The project decides
what `gpu` or `model` means; the provider says which resource names exist. A label that asks for a capability (`lease`, or `net =
"loopback"`) is honoured for a named project on this machine and, on a device, only as far as the receiver's policy names it; a project
the user has not named gets no lease and no loopback whatever its file says. A unit that needs a model uses a lease the runner delegates to
the unit's invocation (5.5); the runner starts no server and passes no slot count.

### 4.4 Steps: environment and build commands

A step is a command the project declares to prepare a device before any unit runs: a virtual environment, an installation, a compile.

- **Once per device, cached by a key.** The key is a hash over the step's `inputs` (file contents), the output of each `tools` command
  (versions), the step's `run` and `env`, and the platform class. The step runs when its key has no verified entry on that device and is
  skipped otherwise. A unit lists the steps it needs (`needs`, default all).
- **Before every unit, outside every timer.** Every step a target needs finishes before the first unit is admitted there, so no unit holds a
  slot while it waits for a step. A step runs before the run's clock starts, on its own deadline, and is not counted in any unit's duration
  or timeout. Its time is a line of the report (`steps: build 41 s`) and is part of the fixed cost the planner measures.
- **Its own directories, written by the step alone.** The step receives `{cache}`, a directory keyed by the step's key and removed when
  idle, and `{shared}`, a directory that persists across keys for the same project and sender (a compiler's target folder). Both are
  outside the checkout and outside the tree a device receives. Units and later steps read them as `{step:NAME}` (the cache) and
`{shared:NAME}` and receive them **read-only** in their sandbox; only the step that owns a key writes that exact directory. **The manifest covers
`{cache}`.** When a step ends the runner walks the directory through descriptors: it holds a descriptor for the directory and for every component
below it, opens each entry with `openat` and `O_NOFOLLOW|O_NONBLOCK`, takes `fstat` before any read, and records a link, a FIFO or a device by
its type and never opens it; the manifest holds path, type, mode, size and content hash, with the entry count and byte total taken from the
descriptors. Before every reuse it walks the same way, re-hashes any entry whose metadata differs, and a mismatch removes the directory and runs
the step again. **`{shared}` is covered by what a unit read from it.** It is a compiler's target folder, and a walk of it before every reuse is a
fixed cost against the 5-second rule (an `lstat` walk of this repository's cargo target directory, 10,942 entries, took 2.3 to 3.6 s at a load
of 90 [probe]), so the manifest of a `{shared}` entry is the digest of the files a unit read there, its `reads` (5.3), checked at reuse; an
entry no unit read is not checked, and units receive the directory read-only. A `complete` marker proves nothing about contents and is not what
a hit checks.
- **Admitted.** A step is a request like any other (5.5): it takes the `slots` its table names, else the slots the admission interface has
  free when the step starts (a measurement, not a fraction of the capacity), and reports `CARGO_BUILD_JOBS`-style parallelism through
  `{jobs}`.
- **Pruned.** `keep` (the project's setting, no runner default) removes keyed directories not used for that long; with no `keep`, nothing
  is removed by age and the least recently used keyed directories are removed when the volume's free disk is needed (10.6). A step may
  name `prune`, a command run when a run starts and the free disk is short, for what the runner cannot see inside `{shared}`; there is
  no interval.
- **Scoped to a project and, on a device, to a sender.** `{cache}` and `{shared}` are keyed by the project, and on a device by the sender's
  fingerprint first: the project id is an opaque hash the sending runner computes from the named project (6.1) and never reads from a file,
  so one sender's two projects never share a directory and a sender cannot leave a product that another sender's step key finds. An
  unnamed project is never sent, so it has no key there.

The cargo handling that has landed is this mechanism with the cargo preset's helpers, shown in section 9.

### 4.5 Sets: the former tiers

`[set.NAME]` selects units by label and adds arguments: Poolhouse's `fast` is "every unit without the labels `slow` and `heavy`",
`all` adds `extra = ["--slow"]`. `extra` fills `{extra}` and joins the reuse key; so do the arguments a person types after the selector
(`poolhouse-test run @fast tests/test_x.py -k name`), which also fill `{extra}`. `poolhouse-test run @fast tests/test_x.py` runs the
selected units that match the selector. A project with no sets runs `@all`, every unit.

### 4.6 Hooks

`before_plan`, `before_run` and `after_run` are argument vectors run on the sender only, in the project's trust state (confined, in the
scratch copy, for a
project the user has not named, 4.8); a nonzero `before_plan` ends the run with status 4 and its output (an agent policy, a queue check).
They are never sent to a device and are not part of any key, and each has a hang guard (5.10). The hook table is the only place a project
runs code the runner did not derive from LIST, RUN, a step or a command unit.

### 4.7 Launchers

A launcher is an argument vector placed before RUN on this machine (Poolhouse's Seatbelt kernel, a container wrapper). It is applied by
the local transport only and can only narrow: the runner puts it inside the profile it builds, never outside it, so a launcher cannot
widen what the sandbox allows. A device that receives a shard ignores any launcher in the file and wraps the run in its own confinement
(10.2), so a project cannot ask a device to run it unwrapped.

### 4.8 The user's settings

Where a run goes, which projects are the user's own and what the user lets confined children read are the user's, kept in one file in the
user's config directory (`~/.config/poolhouse/testfarm-settings.toml`), outside every project and outside the pool's secret root. A
project's `testfarm.toml` is never consulted for any of it. The settings file and the user presets file are in the Owner zone (3.5): no child
of any run reads or writes them, `ph.test.owner_paths()` lists them with the rest of the zone, and the host adds that list to its edit and
shell guards, so an agent's file-write tool or shell command that names one is refused (for Poolhouse, `claude-edit-guard` and
`claude-bash-guard`). The commands below write the file only after the gate answers yes. `check` reports a settings file that the user does not
own, that is a link, or that group or others can write, and prints every key that widens what a run may do (`named`, `agree`, `target`,
`confine.read`, `resources`, `events.sinks`, `person_gate`) so a person reads them.

| Key | Meaning | Default and its source |
|---|---|---|
| `target` | where tests run: `local`, `auto` (every target the enabled transports list), `pool`, a device name or a fingerprint | `local` |
| `[project."ID"] target` | the same for one named project; `ID` is the digest of the project's identity record (4.9) | the global `target` |
| `[[named]]` | the projects the user has named as their own, each an identity record (4.9): the root's realpath and `(st_dev, st_ino)`, the `.git` directory and, for a linked worktree, its administrative directory | none; written only by `poolhouse-test own` |
| `[[agree]]` | the devices a named project's tests may be sent to, as the person agreed them (below) | none: nothing is sent |
| `reserved_cores` | cores the runner leaves to the machine | none |
| `confine.read` | extra read-only paths a confined child may read, as a shipped preset's `[confine]` names for its own tool | none |
| `events.sinks` | where canary mismatches and run endings go | `stderr`; device setup adds `board` |
| `resources` | the resource providers (4.3) | none: a label naming a resource is refused |
| `person_gate` | the host's gate (below) | none: every ask is `unavailable` |
| `presets` | the file of user presets (section 7) | `~/.config/poolhouse/testfarm-presets.toml` |

The command line overrides `target` for one run: `--on local`, `--on auto`, `--on pool`, `--on DEVICE`. `poolhouse-test target
local|auto|pool|DEVICE [--project]` writes the setting. `local`, and a device set the person already agreed for the project, ask nobody; a
`target` or `--on` that names a device the project has no agreement for is a widening and asks the gate (below), once for that set.

**The person's yes.** `own`, `allow` and a widening `target` ask one function, `person_gate(question)`, that the host supplies and the settings
name; it returns `yes`, `no` or `unavailable`. A `question` carries the act, the sentence exactly as the person will read it and a digest of its
subject (the identity record, the sender's fingerprint, the device set); a `yes` is host evidence that a person answered that exact question,
and the library accepts nothing else. For Poolhouse the host is `poolhouse.workspace`: it puts the sentence through the structured approval
question and consumes the harness's evidence with `person-consume`, as it does for `release-main` (`docs/person-delegation.md`, sections 7 and
12); the closed registry of that document gains the kinds `test-own`, `test-allow` and `test-widen`, each with a derived target, one use and an
expiry. The library has no terminal prompt for any of the three. A controlling terminal, an answer read from standard input, an environment
variable, a flag and a file are never accepted as a person's yes: [probe] an agent that starts a child under `script(1)` gives it a controlling
terminal and types the answer, and the child read `yes`. With no gate named, or one that answers `unavailable`, the command refuses, changes
nothing and prints what a person does at their own terminal and editor: the exact block (an array-of-tables entry, valid wherever the file
ends) and the one-line shell command that appends it to the settings file, with the file's path. A person's own edit of a file the host guards
is the only act with no gate in it. Nothing waits: `unavailable` keeps the safe default (confined, `local`) and the run goes on.

**Naming a project.** `poolhouse-test own [PATH]` puts one sentence to the gate: "The tests and build commands in '<path>' will run as you, with
access to your files except the runner's own keys and settings, and may be sent to: <devices>. Without a yes they run confined, in a copy."
`<devices>` lists the devices the current target would use for the project, by name and fingerprint (`none` for `local`). The path and the
names go through `report.plain` (a directory name may hold a newline, a quote or a right-to-left override; macOS allows all three [reasoned]).
A yes records the project's identity record (4.9) and the agreed device set. A later send to a device outside that set asks the same gate
again, once, with the new list. Poolhouse's own checkout is named by its device setup, never by its project file. A project that is not
named runs confined (10.2) and is never sent: `--on pool` for it is refused with status 4 and the sentence "this project is not named as
yours: run `poolhouse-test own` to let it leave this machine".

### 4.9 A project's identity, its root and the git calls

A project is what the person pointed at, read without git. Whether a project is named is decided before anything of it runs, from files only.

- **The identity record.** The realpath of the root, the `(st_dev, st_ino)` of the root directory and, for a repository, the directory
  `<root>/.git` itself when it is a directory that holds `HEAD`, `objects` and no `commondir` file. A linked worktree (`<root>/.git` is a file)
  is accepted only when `<gitdir>/gitdir` names that `.git` file back (git writes the back-pointer at `worktree add`) and `<gitdir>` lies
  under `<common>/worktrees/`; the record then holds the worktree's administrative directory and the common directory, the common directory
  checked by the rule above. A check that fails makes the project unnamed and is not an error, and a renamed, moved or restored directory has another realpath or
  inode and is another project until it is named again. [probe] Asking git instead is defeated by a
  directory whose `.git` file reads `gitdir: <a named repository>/.git`, by a `.git` directory whose `commondir` file names one, by a directory
  below a parent repository and by `GIT_DIR` in the caller's environment: each printed the named repository's common directory.
- **The root is a work-tree top-level** (`<root>/.git` exists), found by the walk of section 4. A root below a repository is a project of its
  own, named only by naming it, so a person who names `~/Downloads/app` while the home directory is a repository names that directory and
  not the repository above it.
- **Named means the identity record is in the settings** (4.8) and matches. The store's project scope is the digest of the record's repository
  part (the common directory, or the root for a project outside git), so every worktree of a repository shares one scope and one key, and a
  clone elsewhere is another project. Naming binds a repository, not a branch (10.9 item 6).
- **Classification runs no git.** The record, the top-level test, named or not, the scope and the root are read from files.
- **Every `git` call the runner makes** (the tracked list, the untracked list, a REF's tree, the merge-base, the objects `affected` compares)
  runs inside the profile of a Child (read-only, no network, 10.2), named projects included, because a named repository's configuration is as
  writable by its own tests as by anyone. The repository's own configuration is code: `core.fsmonitor` is "the pathname of the fsmonitor hook
  command" and, set to `true`, starts a long-lived daemon for the work tree [doc: `git-config(1)`], and `core.hooksPath`, filter drivers and
  `diff.external` are the same class. So each call carries `-c core.fsmonitor=false -c core.hooksPath=<an empty directory the runner owns>
  -c core.untrackedCache=false -c protocol.allow=never`, `--no-pager`, and `--no-ext-diff --no-textconv` where the subcommand takes them; its
  environment is `PATH`, `LANG=C`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CEILING_DIRECTORIES=<the root's parent>` and
  `GIT_OPTIONAL_LOCKS=0`, and every other `GIT_*` variable is removed. The subcommands are a closed list: `ls-files --cached`, `ls-files
  --others --exclude-per-directory=.gitignore`, `ls-tree`, `rev-parse --verify`, `merge-base`, `diff-tree` between two commits, and
  `cat-file`. None applies a content filter, a textconv or an external diff, because none reads the working tree through git; the working-tree
  side of a comparison is the runner's own hash of the file read through a descriptor (5.4), so a clean filter can only make a file look changed.

## 5. The core

### 5.1 The run, start to end

```
classify → config → LIST → units → select (set, selector, affected) → divide → assign → reuse lookup → steps → schedule → execute → merge → report → status
```

0. Classify the project (4.9: the identity record, named or not, from files and with no git) and, for a project that is not named, make the
   scratch copy of the transported set (10.2). 1. Load and validate the config, once (3.5, rule 2); run `before_plan`. 2. Run LIST on this
   machine under its hang guard (a LIST that
fails or prints no unit ends the run with status 4, section 1); build units, labels, DEPS. 3. Select: a set, ids or globs, optionally the
affected units; a selection that matches nothing is status 4. 4. Divide: with `--shards N --index I` the units are divided by the pure
function of 5.2 and this run keeps shard I; with no division asked there is one shard. 5. Assign the shard's units to targets (5.2).
6. Look every unit of the shard up in the store (5.3); a hit leaves the shard's list now, after the division and never before it.
7. Run the steps each target needs, outside the clock; they finish before any unit is admitted on that target. 8. Execute each shard on
its transport; output arrives as it is written, framed and prefixed by the sender, and per-unit rows arrive as they land. 9. Merge the
per-id results in unit order, store passes (5.3), re-run a sample as canaries. 10. Print the report and return one status (11): a run in
which no unit passed or was reused and at least one skipped or ran no tests prints `nothing ran` and is status 4.

### 5.2 Divide, assign, schedule

One balancing rule serves both functions below: units sorted by (estimated seconds descending, id bytes ascending), each given to the bin
with the least finish time, ties broken by bin index. No second algorithm exists.

**Divide** is `divide(list, config, seed, N)` and is a pure function of the sorted LIST output, the config and the `[schedule] seed` file.
Its bins are N anonymous shards. Its seconds come from the seed file only (a unit absent from the seed takes the median of the seed's,
capped by `unrecorded_max` when the project sets it; with no seed file every unit weighs the same). It never reads the store, the local
history or a target list, so two machines with different stores and histories produce byte-identical divisions. `poolhouse-test plan
--shards N --index I` prints the units of shard I and, on every shard, the digest of the full unit list and of the full division;
`poolhouse-test plan --verify-cover FILE...` reads the N printed lists and fails unless the digests are equal and every unit appears in
exactly one list. A reuse hit removes a unit from shard I's list after the division. A unit list that differs between machines (a
collection that branches on the platform or on installed plugins, ids that carry a memory address) shows as unequal digests, which is the
failure to see.

**Assign** is the planner for the shard this machine runs, a pure function of the shard's units, a durations table and a list of `Target`s
(`name`, `capacity`, `platform class`, `tags`, `fixed_cost_s`). It is deterministic: the balancing rule above with ties broken by target
fingerprint, then name. The same inputs give the same plan on every machine, and the plan prints a digest of its inputs. A target's free
slots at the moment never decide which units a shard holds; they decide when it starts.

- **Local with no pool.** The one target is this machine, with capacity from the admission broker (5.5). The shard is run by
  `workers = clamp(sqrt(estimated seconds / start-up seconds), 1, capacity)` processes pulling batches from the shard's queue. The
  rule is the minimum of `start-up * workers + work / workers`, and the start-up seconds are the project's own, measured: the median
  of the recorded `setup` seconds of earlier workers in the history (5.8); with none recorded the run takes the whole capacity.
- **A device takes a shard only when it finishes sooner.** A target's `fixed_cost_s` is measured on the sender's clock from earlier
  shards on it (upload, unpack, tree hash, steps, start) and never taken from the device's own report; with none recorded it is the
  measured local cost of the same phases. A unit goes to the target that finishes it first including that cost, so a short run stays
  local and a long run spreads, with no constant of the runner's own. A device's planning capacity is the most units the sender has seen it
  run at once; with no observation the capacity it claims is used for its first shard only, whose sender-side bound (5.10) limits a false
  claim. A unit with a valid `remote` entry (5.3) for a reachable device is planned on that device first, so affinity does not move with
  free slots.
- **Platform classes.** A target's class is its `(system, machine)` and WSL-ness, taken from the member record the sender pinned when the
  device was added (6.2) and never from what the device says in a request. By default the plan splits units among the targets of the
  sender's class (speed). `--cover` also runs the selection once on each other class, split among that class's targets (coverage), so
  `--on all` of today and `--split` of today are one planner with one switch. A pass is per class (5.3); a pass on another class
  replaces nothing (decision 4 of `docs/design-test-selection.md`).
- **Units that do not move.** `local` labels, labels whose `platform` no target satisfies, units that hold a resource no target offers,
  units whose ids do not fit the argument space of a target (10.4), units with neither a `timeout` nor a recorded duration (a device
  could not bound them, 5.10), and every unit of a project the user has not named stay on the sender; the plan says why for each.
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
    guessed was too large. The one bound is the sender's own memory: it builds one unit per slice before anything runs, so the count is at
    most its free memory divided by the measured size of one planned unit (10.6), and a larger count is status 4 naming that bound and the
    numbers it was derived from. The count is fixed before anything runs and printed; it is one number for the whole run, because the
    tool's own partition is only a partition for one count. `--slices N` (a positive integer, otherwise status 4) overrides it for one
    run and joins the unit key.
  - **The balance is rough, and the plan says so.** The tool splits by count of tests (or by its own rule), not by duration, and the
    runner cannot see inside a slice: a slice of a few slow tests finishes long after the others. Each slice is scheduled with the
    median of its own recorded durations (history is kept per `slice i/n` for one count), or `unrecorded_max` with none, and the plan
    prints `balance: rough, slices are even by count, not by duration` with the longest and shortest recorded slice once any exist.
    A slice is never rebalanced, retried elsewhere or split further by the runner.
  - **Results merge per slice.** A slice reports what the project's `slice_results` says. With `exit` (the usual case: the tool
    reports only pass or fail for its share) the slice is one result, `slice 3/8: passed` or `failed`, with the tool's output tail;
    the run lists slices, not tests, and says `tests in a failed slice are not named`. With `jsonl` or `junit` the rows are real ids,
    merged as in 6.4, and the runner checks that no id appears in two slices (duplicates are an error naming both slices). The run
    passes when every slice passed, the report is ordered by slice number, and a run's selection is the same as the tool's whole
    selection only because the slice template carries the project's own filters in `[run] argv`'s shared arguments; the runner does not
    check that the slices cover the suite, and the plan states `coverage is the tool's partition, not checked`. A `jsonl` or `junit` slice
    that reports no row is `ran no tests` and is not stored, and a run in which every slice ran no tests is `nothing ran` (status 4). The
    slices of one run go only to targets of one platform class whose `tools` digests are equal (the keys of the steps), because the tool
    partitions what it collects on each device and two devices that collect differently partition differently with nothing to compare;
    a plan that would put slices on targets that differ is refused for slice mode with that sentence.
  - **Reuse is per slice and keyed on the slice count.** A slice unit's key is the key of 5.3 over the slice template with its
    placeholders filled (so the index and the count are in it), over the tree or the DEPS the project gives (a project without LIST
    usually has none, so the whole tree). A pass is stored per slice when `slice_results` is `jsonl` or `junit` (an `exit` slice is shown and not stored, 5.3); changing the count changes every key and invalidates every
    stored slice, which is correct because slice 3 of 8 and slice 3 of 6 hold different tests. A changed file reruns the slices its
    DEPS reach, and with no DEPS all of them. Canary re-runs (5.3) apply per slice. The `remote` kind rules are unchanged.
  - **A failing or lost slice.** A failing slice is `failed` with its slice number, the exact invocation (argument vector with the
    two integers filled) and its tail, so it can be re-run by hand; status 1. A slice on a device that stopped answering is `not
    run (device lost)` and named `slice 5/8`, status 70 (1 when any slice failed), exactly as in section 11; a slice that never got a
    permit is `not started`, status 75; a slice ended by its guard is `timed out`, status 1, and one ended by the sender's bound is 124. Nothing re-runs a slice elsewhere by itself, and running the command again reuses the
    passed slices so only the failed and not-run ones execute. A tool that exits zero having run no tests in an `exit` slice is not
    seen (the runner reads exit codes and rows, never output text): the slice is shown as passed, exit status only, and is not stored; a
    project that wants it caught gives `junit` or `jsonl`, where an empty slice is `ran no tests`.
  - **Exit codes are unchanged.** The table in section 11 holds for all three modes; no code is added for slices.
- **Batch size and order** inside a shard: longest first (`testorder`'s rule, the recorded duration), `batch` ids per invocation.

### 5.3 Reuse: result reuse with canary re-runs

A unit's **key** is a hash over: the fully resolved unit table (its ids and labels and the RUN template after preset and user-preset
resolution, minus the fields that only change how long it may take: `slots`, `estimate`, `timeout`); the set's `extra` and the arguments
typed after the selector; `run.env`, `run.results`, `run.junit_id`, `run.batch`, `tree.exclude` and whether a launcher was used; the file
contents named by its DEPS (the whole tree's hash when it has none) with their mode bits; `deps.everything` contents; the keys of the steps
it needs; the platform class; the values of the variables named in `reuse.key_env`; the output of the `reuse.key_tools` commands; and the
digest of the runner's own code. This is the key `docs/test-reuse.md` describes with its Python-specific parts moved out: the "bytes of
the test file, conftests and `pyproject.toml`", the pytest and plugin versions, and the import closure are the pytest preset's DEPS and
`key_tools`, stated in the preset's table. For a `remote` entry the key also holds the device's resolved-table digest and tool outputs;
only the device can run them, so they are device-claimed fields and the entry stays advice (6.4).

A result may carry `reads`, files the invocation opened that DEPS did not list (the pytest preset's recorder plugin is a helper that
writes them). The store keeps them in the entry's **manifest** and a later hit requires them unchanged; a result may also carry
`unstorable` with a reason (a write outside the temporary roots, a skip), and then nothing is stored. The runner never infers either, so a
pass is stored only for results formats that can say it: `jsonl` and `junit`. A pass in `exit` format is shown (`passed, exit status only:
skips and side effects are not seen`) and is stored only when the project sets `[run] reuse = "exit"`, and then the report says `reused
(exit status only: skips and side effects are not seen)`. The runner also stamps the files a unit's DEPS name (path, mode, size, modification
time, read through descriptors) before and after each invocation and stores nothing when one changed. A unit with no DEPS depends on the whole
tree, whose stamp is one walk at the start and one at the end of the run and not one per invocation: a change between them stores nothing and,
with no failure, is status 4. A project that is not named runs in a scratch copy its units cannot write back, so its stamp is of the copy.

The store is the one `docs/test-reuse.md` defines: entries written through a rename, a chain of row hashes, `latest/<key>`, single flight
with `inflight/<key>.json` claims, only the runner process writes it, and the state directory is outside the checkout and outside any root
a unit may write. What it adds:

- **A directory per project and per domain.** The project scope is the digest of the identity record's repository part (4.9: the common git
  directory, or the root for a project outside git); the domain is the trust the run had (below). Both name the directory, so a repository that
  copies another project's remote url writes a directory of its own, and every worktree of one repository shares one.
- **Signed entries with a domain, under a key per project.** The runner generates a master key of 256 random bits on first use, in a file in the
  Owner zone. The profile of every child, named or not, denies it by exact path (3.5, rule 4): [probe] a profile that is open except
  `(deny file-read* file-write* (subpath <owner zone>))` refused a child the key on read and on append, left it unchanged and refused a hard link
  and an APFS clone of it, so no test, build script or `conftest.py` of any project reads it. The runner is the only reader. Every entry and every
  chain row carries an HMAC under a key the runner derives from the master key and the project's identity record (4.9), so one project's key signs
  nothing of another's; a `domain`: `named` when the project was named (4.8), `confined` when it was not, set from the settings and never from
  anything a child can reach; and `by`, an opaque label the host's event sink supplies for the session that started the run (Poolhouse's sink
  derives it from the workspace's session identity, never from a flag or an environment variable), signed with the entry. A reader verifies the
  signature and asks for a domain; evidence readers accept `named` entries signed under the project's key and nothing else, and a reader's state
  root comes from the user's settings, never from an environment variable. **What a `named` entry proves** is that the runner, holding a key no
  child can read, ran this exact tree and recorded this result. It does not prove the code is right. A task is credited from a run of the exact
  tree by a session that did not author the task: the reader compares the entry's tree hash with the landing tree and its `by` with the task's
  author. A runner process that cannot read the master key (it runs inside a sandbox of its own) writes under a key and a directory of its own,
  and its entries are never in the owner's domain.
- **Single flight.** A waiter waits while the claim's holder is alive, a liveness the OS reports, and for no fixed time; it holds no slot
  or lease while it waits, and when the claim clears without a result it runs the unit itself.
- **Kinds.** An entry is `local` (this machine ran it) or `remote` (a device did, keyed by its fingerprint and platform class). A device
  pass is shown and stored as a `remote` entry only when a second source agrees: another member with a different install-instance id and
  the same platform class, or the sender when it is of that class; the report says `agreed by 2 members` and never `confirmed`. A
  `remote` pass satisfies only a later run on the device that produced it and never a local run. A reader asks the store for a domain and a
  kind and decides what they mean (Poolhouse's workspace sink asks for `named` and `local` when it reports task evidence, 3.3); the store has
  attributes and no workflow. The ledger `remote-results.json` is deleted; its job is the `remote` kind.
- **Canary.** `reuse.canary` is the project's sampling rate, in (0, 1], with no runner default; whatever the rate, a run that serves any hit
  re-executes at least one, so a tiny rate cannot turn detection off. With none set, reuse is off (nothing is served, nothing
  is stored) and the report says `reuse: off, [reuse] canary is not set`; `poolhouse-test init` writes it with the reuse, from the project's
  own first run (one re-executed hit per run on average: `1 / units`), and prints the derivation. The draw takes its seed from the
  operating system's entropy, after the lookup, and prints it so a run can be repeated with `--seed`; two runs of one tree sample different
  sets. The first reuse of every `remote` entry is always a canary, run on the sender when the sender is of that class and otherwise on a
  member of that class other than the entry's device; with none available the entry is not served. A canary is an ordinary run: the same
  argument vector, order and environment, with no marker. A cached pass that fails fresh gets one more fresh run; if that passes the unit is
  reported `flaky` and its entry is removed; if it fails again the run prints `canary mismatch`, fails, writes an `incident` row and a
  disabling record for the key, and sends one event to the sinks.
- **Never stored:** a failure, a unit with `reuse = false`, a result with `unstorable`, a unit that ran no tests, an `exit` result unless
  the project opted in, a run whose tree changed while it ran (status 4), a unit whose key recomputed at the end differs from the start,
  a split unit (a slice unit is not a split unit: it is stored per slice, 5.2), a crashed invocation. The rows an interrupted invocation
  had already written in `jsonl` or `junit` are reported and each is stored as a pass for its id under the unit's key; a unit is a hit
  only when every one of its ids has a pass.
- **Retention.** Entries not reachable from a `latest/<key>` of a unit seen in a recent run, history rows for ids absent from recent
  LISTs and evidence outside the live range are pruned on the same idle rule as steps (`keep`, else least recently used when the volume's
  free disk is needed, 10.6). `check` prints the share of ids that differ from the previous LIST, because an id that carries a memory
  address or a timestamp adds an entry per run.
- **Report lines:** `ran` or `reused from <run id>` per unit with the key prefix, `--no-reuse` forces execution and says so once. The run
  id is 128 random bits from the operating system and is unrelated to the argument vector or the tree.

The unit key and the affected set are one definition (design-test-selection 4.6): the DEPS of a unit are what the key hashes and what
selection intersects with the changed paths.

### 5.4 Affected selection and analyzers

`poolhouse-test affected --base REF` (and `run --affected`) selects the units whose DEPS contain a path changed between REF and the
working tree (tracked changes, untracked files, deletions), plus every unit when a path in `deps.everything` changed, plus every unit
without DEPS. `--explain` prints the reason per unit. A REF the clone does not have (a shallow clone) selects every unit and prints
`REF is unknown here (shallow clone): every unit selected`; a REF that exists with nothing changed since it selects nothing, prints
`nothing changed since REF` and is status 0. A path is changed when git's record of it at REF (`ls-tree`, an object read) differs from the
runner's own hash of the file in the working tree, read through a descriptor (git's blob hash, so a clean filter or `autocrlf` can only make a
file look changed and select more units), when it is untracked (`ls-files --others`, 4.9) or when a tracked path is absent. `affected` assumes
git and a history that holds REF; the store scope reads no git (4.9), and a project outside git uses its root as the scope and has no
`affected`.

DEPS come from three places and the core only intersects them:

| Analyzer | Where it runs | What it knows |
|---|---|---|
| declared globs | the core | `[deps] globs`; any language |
| a command | the project's `deps.command`, any program | prints `{"id", "deps": [...]}` rows for LIST's ids; the project writes what it knows (imports, crates, a build graph) |
| shipped helpers | `testfarm/presets/<tool>/` programs, run as commands | `python-imports` (the import closure, facts cached per blob hash in the store, `import-facts.json` today), `cargo-crates` (workspace members and their dependents from `cargo metadata`), `files` (a unit is a file; it depends on the whole tree unless the project declares globs) |

The soundness rules of `docs/design-test-selection.md` section 5 hold for any analyzer: an unknown selects (an id with no DEPS, a
helper that errors, an unreadable file, a declared glob that matches nothing); a skip needs a positive proof (a stored pass for the same
key); the selector's own files select everything (`deps.self`, the config, the helper's code via the runner's digest); and the graph comes
from the tree, never from a device or a board entry. Where an analyzer reports `opaque` for a unit, DEPS widen to the whole tree for that
unit. A preset that cannot know a unit's dependencies gives it the whole tree, and `check` fails a declared set that leaves out the
unit's own file.

### 5.5 Admission: compute slots and resources

Admission keeps tests from overloading a machine. The interface the core defines:

```
Admission.acquire(request: Request) -> Grant     # Request: slots, resources, serial, estimate_s, class, label; all or nothing
Grant.sub(n) -> Grant                            # a per-test permit: a share of this grant, never a new request
Grant.delegate(group) -> None                    # the broker lets the processes of a group present this grant
Grant.release()
```

The default implementation is the machine-wide, file-lock-backed broker of `testslots*.py` moved in whole: capacity is the logical CPU
count less the cores the machine is measurably busy with outside the broker (load sampled when a permit is asked for) and less the
cores the user reserves (`reserved_cores` in the user's settings, no default); shortest estimated work first; a request ages once it has
waited as long as the estimated work ahead of it (from the history, 5.8), so it is never passed over by later shorter work for longer
than the queue it joined; a later request backfills when it fits in the free slots and its estimate ends before the head's estimated
start, so the head is never delayed; a class of `interactive` or `background` (background when the project's `[schedule]
background_after` says so, no default: with none every run is interactive unless the person says `--background`); a dead holder's record
removed at once. The broker reads `TESTFARM_*` names only (Poolhouse's `DEV_TEST_*`, read by `tests/conftest.py`, are renamed in step 2
and the library knows no other name).

- **One request, all or nothing.** A request names its CPU slots, its resources and its serial name together. The broker tries each kind
  in one fixed order (serial name, then resources by provider and name, then slots) without waiting; on the first refusal it releases what
  it took and the request waits holding nothing, for a release of the refused kind, and the oldest request is tried first at every
  release, so aging still stops starvation. Nothing waits while holding, so two requests cannot each hold what the other waits for. The
  arbiters behind a request (this file broker, the provider's lease table) are asked inside the same transaction, never one after the other
  with a wait between.
- **Per-test permits are sub-grants.** A finer permit per test inside one pytest process (`testslots_pytest.py`) is a pytest preset
  helper, and it takes `Grant.sub(n)` out of the slots its invocation already holds; it never makes a request of its own. Permits reach the
  helper on an inherited descriptor the executor holds (`TESTFARM_PERMIT_FD`), so a confined child needs no broker endpoint and no path.
  The spawn function (3.1) closes every other descriptor in the child: it keeps exactly 0, 1, 2, the permit and the result descriptor, because
  a descriptor the runner leaves open is a capability ([probe]: with `pass_fds` including one opened on a file outside the profile, the confined
  child read it). A `poolhouse-test` started inside a live permit refuses to run, and a command started inside one fails promptly instead of
  queueing behind its parent (`docs/test-execution.md`); the test is the broker's ancestor test (below), the same code `serve up` uses, and not
  the variable, which the child can unset. A nested run whose parent has already exited is not found by the test and queues until a guard ends it.
- **Resources.** A resource a label names is requested, in the same request, from the provider the user's settings name; the provider says
  which names exist, how much of each there is and which are exclusive. Poolhouse's provider takes them from the pool's lease table
  (`ph.leases`) when a device answers, else from the broker's own heavy lane (`HEAVY_MODULES` today), and marks `gpu` exclusive: one piece
  of work on a device's GPU at a time is a hard floor (AGENTS.md section 1). The core knows only names, amounts and exclusivity.
- **Delegation, not inheritance.** When a unit's own command needs the resource the runner holds for it (`poolhouse-serve up` under a
  `gpu` label), the runner asks the broker to delegate the lease to the process group of the invocation. **The broker identifies a process by a
  handle, never by a process id alone.** A handle is a `pidfd` on Linux and, on macOS, the two values (process id, start time) read from the operating
  system with a `kqueue` exit registration (`NOTE_EXIT`) made after the start time is checked; a process id names a dead process once its
  process ends and a different one once the id is recycled. [probe] `LOCAL_PEERPID` on macOS is the process that made the connection: after it
  forked a child that kept the connection and exited, the server read its id and the process was dead. The broker refuses a request whose
  connecting process cannot be turned into a live handle (it fails closed; the client connects again from the process that speaks) and drops a
  delegation on the exit notification of the group leader's handle. `serve up` finds an ancestor of its caller among the delegated groups, each
  ancestor identified by handle and checked against the delegation's own, and uses that lease instead of queueing for a second one; no lease id
  travels in the environment, and a process outside the group that presents the same text gets nothing. The model server a delegated lease
  starts is a member of the group and ends with it; one that detaches keeps the lease held until it exits, so the next unit's lease is never a
  second piece of work on the compute (AGENTS.md section 1). The server keeps the slot default of `poolhouse.serve` (one, `--parallel 1`,
  `docs/agent-policy.md` section 1 and `docs/limits.md`); the runner starts no server and passes no slot count. This is a change to
  `poolhouse.serve` in step 2, and it keeps AGENTS.md section 6: a lease is never made outside the broker's grant.
- **Steps and single flight take no slot while they wait.** Every step a target needs finishes before the first unit is admitted there
  (4.4), and a single-flight waiter (5.3) takes nothing until the claim clears.
- **One slots directory per user,** mode 0700, in the `owner/` directory of the state location. A user's broker sees another user's load as outside load in its
  sample, so two users on one machine yield to each other without sharing records. Two users who ask at the same moment can still
  overload the machine; the owner accepts that (10.9).

### 5.6 Jobs

A run is a job: `submit` prints an id, `status`, `wait`, `result`, `cancel` act on it, and the foreground command follows a job
(`testjobs.py`, `docs/test-reuse.md`, "Async jobs"). The same argument vector and tree hash follow the live job instead of starting a
second (the job key; the run id is a separate 128 random bits); Ctrl-C of the last follower cancels, a cancel signals the job's process
group and then kills it; one whole-selection job may be live at a time per project and a second is refused with status 6. The job's owner
is the OS user: any process of that user may cancel it, because the user is the trust boundary on a machine the user owns. A job holds a
file lock for the life of its runner and the operating system releases it at the runner's death; the next run finds the record with no
holder, marks it `lost` in its report and goes on, so status 6 is never held by a dead job. A named provider (Poolhouse's workspace)
supplies an agent identity and watchers, through the event sink, not through core code.

### 5.7 Command units: gate steps are units

A `[[unit]]` table declares a command as a unit with no LIST: `name`, `run`, `inputs` (globs), `tools`, `labels`. Its key is the
inputs' contents, the command and the tool outputs; a pass is reused while nothing it reads changed, a failure is never stored
(`gaterun.py`'s rule), and independent command units run side by side. An `inputs` glob that matches no file is a `check` error: an empty
set is not an unknown. Poolhouse's gate (budgets, red-team coverage, command reference, structure tests) is a set of command units;
`gaterun.py` and `gatescope.py` are deleted.

### 5.8 History and estimates

Every run records per unit `ran_s` (the wall seconds from the unit's start to its end) and the slots it held, merged under a file lock
into the project's history in the state directory, shared by every worktree (the project scope of 5.3). For a unit that ran here the
number is the runner's own; for a unit that ran on a device it is the sender's own: the interval between the unit's start event and its end
event as they arrive on the sender's clock. A number a device reports about itself must be finite and between zero and that interval, and
is never recorded in its place. The planner and the estimate use wall seconds times slots; CPU seconds from an invocation's rusage are kept
for local runs as information and steer nothing. The history keeps a sample count per unit and the median of the samples (a median is not
moved by one slow outlier, so no clip is needed). The estimate of a run is the sum of its units' seconds divided by the workers granted,
with a unit that has no record counted at the median of the recorded ones, and with none recorded at all the units are placed by count and
the estimate says `unknown`; `[schedule] unrecorded_max`, if the project sets it, caps that median; a unit the store would serve costs
nothing. The estimate and its class print before admission. History rows are pruned with the store (5.3, "Retention").

### 5.9 Fail-first

A new test is run on the code without the change and seen to fail. The library makes that a command and a check:

- `poolhouse-test failfirst run [--base REF]`. It lists the units at `REF` (a scratch tree under the state directory, never the
  working tree) and at the working tree. A **new** id is one absent at `REF`; a **changed** id is one whose `def` digest (a LIST
  `jsonl` field; the pytest preset emits the AST hash of the test function) differs; a project whose LIST gives no `def` has only new
  ids. It builds a scratch tree of `REF`'s files with the working tree's files under `[failfirst] tests` laid over them, runs those ids
  there through the ordinary run path (so they shard and respect labels), and requires every one to fail. It then requires them to pass
  on the working tree. For each id it writes an evidence entry `{id, def, base tree, result at base, first error line}` to the store. It
  prints `[failfirst] line` filled in (default: `failfirst: <id> failed at <sha>: <error line>`); a project whose reports use another
  sentence sets the template, and the library has no sentence of any project's policy.
- Evidence is produced only by the runner and carries the store's signature under the project's key (5.3); an entry written by anything else
  does not verify, and a new test cannot write one, because its import-time code is a child of the run and no child reads the key.
- `poolhouse-test failfirst check --range A..B` is the mechanical check: for every new or changed id in the range's tip it finds either
  an evidence entry for the same `def` whose `base tree` equals the tree of A, the range's start as the caller gave it (a range in which A
  is not an ancestor of B is status 4, `the range is not linear: merge A first`, so there is no second base to choose), or an exempting line
  in a commit message of the range (a line that begins with the literal prefix `[failfirst] exempt` names, then an id of the range and a
  reason), and fails listing
  the ids with neither and the command that makes the evidence. It reads commit messages only for the exempt lines. A project with no
  `exempt` key has no exempt route. A change to a file under `[failfirst] tests` that changes no function digest (a fixture, a
  `conftest.py`, parametrisation data) makes the units that file feeds `unverified`, which need evidence or an exempt line. A project whose
  tests live in the implementation files names the test-bearing hunks in `[failfirst] tests`, or stays on `mode = "report"`.
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
- **Three numbers per unit, always reported.** `queued_s` (waiting for admission), `built_s` (steps and setup before the unit's first
  admission on its target) and `ran_s` (the running time the timeout judges). The report line is `ran tests/x.py  queued 12 s  built 0 s
  ran 3.1 s`, the run's phase line totals them (`queued for CPU` is the machine's, the rest is the fixed overhead the 5-second rule
  measures), and a result file may carry `queued_s` for per-test permits. The history records `ran_s` as the unit's duration (5.8), so the
  estimates and the planner do not learn queue time.
- **Outcomes.** A unit is `passed`, `failed`, `timed out` (it ran past its timeout or hang guard: a failure that says `timed out after N
  s running` and shows the three numbers), `skipped`, `reused`, `not reported`, `ran no tests`, **`not started`** or **`not run`**.
  `not started` means this machine could not admit it: it waited for a permit or a resource past the project's queue limit (`[schedule]
  queue_wait`, no runner default; with none, the queue wait is guarded by no progress: the broker's queue position and the holders'
  heartbeats stop advancing for the derived stretch, status 75 naming the holder waited for), or the run was cancelled before its permit.
  It is neither a failure nor a pass, is listed apart in the report (`NOT STARTED, machine overloaded: waited <N> s for a permit`, N
  measured), is not stored, and gives status 75; the sender's own admission is the only source of it, and a device that says it could not
  admit a unit has its units reported `not run (device refused)`, reassigned (nothing ran, so reassigning is safe) and counted against the
  device (6.4). `not run` has a reason in parentheses: `device refused`, `device lost`, `deadline`, `step NAME failed`. Running the command
  again continues with exactly the units that did not finish.
- **Two timeouts, never one name.** `test_timeout` limits one id: the preset hands it to the tool's own per-test timer and restarts that
  timer after the permit is granted. The unit's `timeout` limits one invocation, which holds many ids. The runner has no timeout of its
  own; in order, a unit's `timeout` is `[label.NAME] timeout` for its labels (the largest), then `[run] timeout`, then the hang guards
  below, which derive it from the unit's recorded durations. Poolhouse's file takes the owner's per-test value as `test_timeout = 120`
  (`pyproject.toml`, `docs/limits.md` row `pytest timeout`) and sets no unit `timeout`: a file of fifty five-second tests is bounded by its
  recorded duration times `total_margin`, not by 120 s, and a longer test carries a label with a longer `test_timeout` as
  `@pytest.mark.timeout(N)` does today. The report names the limit that fired: `test timed out after 120 s running (test_timeout)` or
  `unit timed out after N s running (limit: recorded typical duration x total_margin)`. Rows an invocation had written when it was killed
  keep their outcomes and are stored as 5.3 says.
- **Hang guards: no progress first, a total second.** Nothing waits forever. Every command the runner starts (every call of the one spawn
  function, 3.1: LIST, `deps.command`, a hook, `git`, the
  Parser worker, a `[requires]` probe, `prune`, a control unit, a build step, a unit) and every queue wait has (1) a *no-progress* guard: it
  ends when no child output, result row or unit start or end
  has arrived for a stretch derived from a measurement, namely the longest silence its own recorded runs showed times the project's
  `[schedule] silence_margin`, or for a device the measured round trip of the stream times that margin; and (2) a *total* guard, the
  unit's recorded typical duration (the median, 5.8) times the project's `[schedule] total_margin`, used only when the label, step or
  `[run]` table names no `timeout` (which always wins). A heartbeat from an executor proves its stream is alive and is not progress, so a
  device that sends heartbeats and nothing else is ended by the total guard of the unit it runs. The margins are the project's numbers in
  `testfarm.toml` with no runner default; a unit with no silence recorded of its own takes the guard of its siblings: the median of the
  per-unit longest silences recorded across the project's other units times `silence_margin`. With no history at all in the project (its
  first run ever), the run is guarded against the live gaps it produces itself: a silence longer than the longest gap seen so far in this
  run times `silence_margin`, applied after at least one unit has finished (the first unit is bounded as the next bullet says), and the
  report says `guard: bootstrapping` with its source. **A run with no hang guard is not allowed.** Unless the project sets both
  `[schedule] silence_margin` and `total_margin`, or an explicit `timeout` on every command the runner starts, the runner refuses to start,
  loudly, with status 4, naming the missing key and the reason (`testfarm.toml: [schedule] silence_margin
  is not set, and unit tests/x.py has no timeout: a run with no hang guard is not allowed`). There is no default number. `poolhouse-test
  init` writes both margins for a new project from its measured first runs (`silence_margin`: the longest gap between outputs seen, divided
  by the median gap, rounded up; `total_margin`: the largest ratio between a unit's slower and faster time over two runs of the selection,
  rounded up) and prints both for the person to read in the new file. A guard ends the work loudly and the line names what was waited for
  and the derivation: `unit tests/x.py: no output for <N> s (longest silence recorded <S> s x margin <M>), all three measured`. A unit
  ended by its guard is `timed out`, status 1; a build step ended by its guard, or a shard ended by the sender's bound below, is status
  124; a queue wait is 75. A build step has its own `timeout`, else the hang guards above on the step's recorded runs, and never counts in
  a unit's. A probe a device runs for `[requires]` lives as long as the request that asked for it: the sender's guard ends the request
  and closing the connection kills the probe, which runs in the profile of a unit (6.5).
- **A number is not a guard, the first unit has none, and a byte of output is not progress for ever.** Every number of the project's file is
  finite and in range (section 4), so `inf`, `nan` and `1e999` never satisfy the refusal, and both margins are required because one is half
  a guard. A byte of unit output
  counts as progress only until
  the total guard fires, and the total guard exists once any duration is recorded, so a unit that prints a byte every few seconds is ended by it.
  Before the first unit of a project has finished there is no recorded duration, so the run is allowed only attached to a terminal (a person can
  end it) or with `--timeout S` on the command line, a number whose source is the caller; the report says `guard: bootstrapping, terminal` or
  `guard: bootstrapping, --timeout`, and an unattended first run with neither is status 4. `poolhouse-test init`'s measured first run is that
  run. On a device the receiver is the caller (10.6, "Time").
- **A shard has no clock of its own; the sender bounds the sum.** A shard ends when its last unit ends. Each unit on a device has its own
  bound (its `timeout`, else its recorded typical duration times `total_margin`) and a unit with neither is not sent to a device. The
  sender enforces the sum of its shard's unit bounds on its own monotonic clock, counted from the device's report of the first unit's
  start; the wait for that first start is bounded by the queue limit or the no-progress guard, and the steps before it have their own
  bounds. At expiry the sender cancels the shard and records the units that had not ended `not run (deadline)`. The executor enforces each
  unit's bound from the verified config and its own measurements, and units it did not start are reported `not run (device refused)`,
  never `124`. The sender never trusts a device to end its own shard, and a device never trusts a sender to end it: the receiver's
  own ceiling (10.6, "Time") holds whatever the sender does.
- **Overload is named, not guessed.** The report says `the machine was overloaded` only when units are `not started`; a run in which every
  unit started but some queued for a long time says `queued N s (the machine's)` and judges the units by `ran_s` alone.

## 6. Transports

### 6.1 The interface

```
class Transport(Protocol):
    name: str
    def targets(self) -> list[Target]                  # devices reachable now: capacity, platform, tags, accepts / why not
    def start(self, shard: Shard, tree: Tree) -> Handle
    def poll(self, handle, since: int) -> Progress      # state, output from byte `since`, unit rows as they land
    def cancel(self, handle) -> None
```

A `Shard` is `{id (128 random bits), project (the sender's opaque project id), config_sha256, tree_sha256, set, nonce (128 random bits the
sender makes per shard, for the control units of 6.4), units: [{id, labels}]}`:
no argument vector, environment, working directory, `extra`, step list or deadline. The receiver builds every command from the config it
holds at `config_sha256` (10.3): arguments, steps, timeouts and slots come from that verified file, and a label name in `units` selects a
table in it (a name the file lacks is refused). `project` is an opaque hash the sending runner computes from the named project (4.8) and is
the only identity of a project a device ever sees; the file's `[project] name` is never one. `Tree` is the sender's packed files and
their hash over the transported set (6.3).

`local` is the transport of this machine, under the same executor code a device runs. For a named project `start` forks the shard in the
checkout itself (no copy, the warm caches, the project's launcher). For a project that is not named it runs the shard in a scratch copy of the
transported set inside the same sandbox, exactly as a device does (10.2).
pool of this device (6.2). `ssh` later implements the same four calls over an ssh channel and stays a plugin.

**The target is the user's.** It is read from the user's settings and the `--on` flag (4.8), never from the project's file, and it is
`local` unless the user chose another. `local` uses this machine; `auto` uses this machine and every accepting target the enabled
transports list for this user; `pool`, a device name or a fingerprint uses those. A tree is sent only for a project the user has named, to
devices the person agreed (4.8), so
a repository handed to the user cannot send anything anywhere. What a transport lists is that transport's own definition: the pool
transport lists the accepting members of a pool in dev mode, where every member already runs tests for every other member (`docs/fleet.md`,
"Two devices on a LAN"), and the members of another pool whose allow list holds the sender; `poolhouse features disable remote-tests` is
the receiver's switch. The first send of a project to a device prints the file count, the top-level names of the transported set and every
untracked file in it;
`plan --show-tree` prints them without sending.

### 6.2 The pool transport

The wire is the shard routes of a pool in dev mode, generalised from a fixed command to a unit list. [code] today:

| Operation | Route | Notes |
|---|---|---|
| capabilities | `GET /shards/v1/caps` | `accepts` or the sentence for why not, protocol, build, platform stamp, free slots, install-instance id; answered in full only to a sender the receiver admits |
| upload | `PUT /shards/v1/ID/tree` | in order, `Content-Range`, the packed tree |
| start | `POST /shards/v1/start` | a closed set of fields |
| state | `GET /shards/v1/ID?since=N` | state, output from byte N, unit rows as they land, result once ended |
| cancel | `POST /shards/v1/ID/cancel` | the shard and its process tree |

Changes: the start fields become `id, project, config_sha256, tree_sha256, set, nonce, units` (a closed set; an unknown field is refused); caps
adds `protocol`, the build, `requires` satisfied (6.5), the install-instance id (128 random bits made when the runner is installed on
the device, which the sender pins with the fingerprint on first contact: a second instance answering the same fingerprint, which is a copy of
the key made without the id, gets no shard and is reported; a copy of the whole disk, a snapshot restore or a backup restore carries the id and
is the same instance, so the id separates a stolen key from the original only while the original also contacts the sender) and the policy under
which the sender is admitted. A member the receiver does not admit receives
the sentence for why not and no capacity, platform or toolchain list. Transport and authentication are unchanged: requests are signed
with the pool's key over TLS 1.3 pinned to the certificate the pool recorded for the member, the sender is that certificate and never a
field, a shard belongs to the member that first uploaded to its id and every other member is told "no such shard", and a removed member is
refused at the connection. A protocol or build mismatch is refused with the update hint (`poolhouse runtime ensure`), never run.

**Admission by the receiver.** One function, supplied by the pool transport, decides: `admit(sender fingerprint, policy) → allow | the
sentence for why not`. In a pool in dev mode it allows every member while `remote-tests` is not disabled. In any other pool it allows only
the senders on the device's allow list, empty until a person adds one. Adding is one act with one sentence, "This lets '<device>' run its
own code on this computer, as you.": `poolhouse-test allow DEVICE` puts it to the receiving device's gate (4.8), and with none available refuses
and prints the block for the person's own editor. With the sentence the person gives the sender's budget (10.6, "Time"): the seconds one unit
may run, the seconds one shard may run and the slot-seconds it may hold. In a pool in dev mode, where no allow act exists, the same three
numbers are the device's policy for the pool's members (`poolhouse-test policy`, asked through the gate at device setup), and a device with
none refuses a sender's first shard with the sentence naming that command (`not run (device refused)`, reassigned). `deny` and removing a
member take the sender off and cancel its runs. A phone-signed grant is the same act through another surface (open question 1).

**One server, one executor.** The server half (`testfarm/transports/pool_server.py`) and the executor (`testfarm/executor.py`, L2) are
the only receiver code; `fleet/shard_*.py` and the Rust shard host are deleted in favour of them. The executor, in this order: checks the
header; hashes the config bytes and parses those same bytes once, in the Parser zone (6.6); makes a scratch tree in a
directory it owns; has the Parser worker unpack the tree into `tree/` and checks it by hashing the unpacked files through descriptors it
opens afterwards (6.3, 6.6); runs the steps, then the units, each in the sandbox the receiver builds; streams per-unit rows and bounded
output as
they land; ends a unit at its bound and a shard's process tree at a cancel; returns a result of bounded size; removes the scratch tree. It
takes slots from the device's admission, class `background`, and yields to the device's own work: a shard queues behind the device's
interactive work and is killed when the device's own admission says its owner's work has been requested (10.7). A native Windows machine is
a sender only: `src/poolhouse/sandbox/` has no Windows backend and a device with no confinement takes no shard, so a Windows machine
receives through its WSL device, which is a Linux receiver, and nothing here specifies `taskkill` or Windows reserved names.

### 6.3 The tree

The sender packs the transported set into a gzip tar of plain files, mode normalised to the two modes git records (regular, and executable
when the owner-execute bit is set). The set is the tracked files plus the untracked files a unit's DEPS or `[tree] include` names, minus
`[tree] exclude`, never `.git` and never a link (4.1). The tracked list is `git ls-files --cached` and the untracked candidates are `git
ls-files --others --exclude-per-directory=.gitignore`, both confined calls of 4.9: the exclusion rules are the tree's own `.gitignore` files
[doc: `git-ls-files(1)`], and no user, system or repository-local setting (`core.excludesFile`, `.git/info/exclude`) enters the set, so two
users at one commit send one set. An untracked file joins only through a glob that is not the whole tree (`**`, `*` or the empty set; a unit
with no DEPS depends on the whole tree and pulls in no untracked file), and the first send lists each one (6.1). No directory name,
extension or file name is special to the runner.

**One hash** covers exactly the transported files: for each file, in order of its NFC UTF-8 path bytes, the record `len(path) | path |
mode | len(content) | content`, lengths as fixed-width integers, by a function both ends call. A test hashes colliding concatenations
(`a` with `bc` against `ab` with `c`) and a non-ASCII name. The upload is checked by its digest, the unpacked files are re-hashed and
compared with `tree_sha256`, and `config_sha256` must be the hash of `testfarm.toml` in that tree. Limits are the receiver's (10.6), and the
unpack runs in the Parser worker, not in the daemon (6.6). The
first version sends the whole tree. A later step may send a delta against a base commit fetched by SHA only; it must have the receiver
pin the remote itself, disable the `ext::` and `fd::` transports and `--upload-pack`, and use none of the device's git credentials,
because a sender-supplied remote is code execution (`design-test-selection` 6.1).

### 6.4 Results

A result is streamed. As each unit ends the executor sends its row `{id, status, seconds, message, reads, unstorable}`; at the end it
sends a platform stamp, the `shard_id`, `tree_sha256` and `config_sha256`, and a bounded output tail. Rows that arrived survive a vanished
device, a deadline and a preemption; the sender keeps them, reports them, and stores them only as 5.3 says. The sender checks: the shard id,
the tree and config hashes, that every assigned id has exactly one row and no other id appears, that every number is valid (5.8, 10.4) and
that sizes are in bounds. A `not started` row from a device is no row: the device refused, the units are `not run (device refused)`, they
are reassigned to another target (nothing ran, so reassigning is safe), and a device that refuses more than once is `suspect`.

The echo of the shard id and the hashes defends against staleness and mis-routing, not against a device that did not run the tree: a lying
device echoes. The platform class is the pinned member record's, never the stamp's. The sender computes every key itself and ignores any
key or digest in a result, and a `reads` list from a device is kept in a `remote` entry and is never used to store a `local` pass.

- **A failure on a device is final.** It is reported `failed on <device>`. The sender may run the unit again only on a target of the same
  platform class, and only to add the line `reproduced` or `did not reproduce on <class>`; the status never changes, so a failure that is
  real on one platform class cannot be erased by a pass on another. A failure that did not reproduce is run once more on the device itself
  (the two-run rule of a canary, 5.3): when the device fails again and the same-class target passed, the device gave a mismatch and is
  `suspect`; when it passes, the unit is `flaky` and nothing is counted. A hostile member's false failures therefore cost it its place in
  every plan.
- **A pass from a device is advice.** It is shown (`passed on <device>, not stored`), stored as a `remote` entry only when a second source
  agrees (5.3), and a `remote` entry is never evidence of a run the sender made: a reader that decides landing, release or any other claim
  from the store reads `local` entries only (`design-test-selection` 6.5).
- **Control units and suspicion.** Every shard carries three generated command units the executor runs itself, called *control units* (the
  glossary's *sentinel* is the open-join safety and is not used for these). One must fail with a message holding the shard's nonce, a start
  field the sender made (6.1); one must pass echoing it; one tests the confinement: the executor creates a canary file outside the scratch tree
  and outside the profile, and the unit, run inside the unit profile, tries to read it and must be refused. A device whose sandbox failed open
  reads it, so "the device says it can confine" (A25) becomes a measured result. A shard that comes back without the failure, the value and the
  refusal is refused and counted against the device. Control units test the executor and its profile, not the project's tool: they catch an
  honest executor that ran nothing or confined nothing, and not a device that recognises them, so the canary and the second source carry the
  rest (10.9 item 5). A device that gives a **mismatch** is `suspect (mismatch)` and stays out of every plan until a person adds it again with `allow`
  (4.8); the pool's other devices run on, and nothing waits. A mismatch is a failed control unit, a canary mismatch, a second-source comparison
  that disagrees, or a failure that did not reproduce (below). Its `remote` entries are demoted until a second source confirms each, and the
  report says so. A device that only *refuses* units (more than once, or an executor error) is `suspect (bad executor)`: it is planned only
  control units until one control-only shard comes back right; a control unit never ends a mismatch.
- **A unit that kills its device.** A unit that was running when a device vanished, and again when a second device vanished, is reported
  `suspected: ended its device` with status 1 and is not planned again until the person selects it. One lost device is infrastructure; the
  same unit twice is the unit.

### 6.5 Capabilities and requirements

A project cannot ask a device to run a probe command. `[requires]` names things the device checks itself with probes it owns:
`python>=3.13`, `module:pytest`, `exe:cargo`, `exe:node>=20`. A name matches `[A-Za-z0-9_.-]+`, does not begin with `-` or `.`, and a
`module:` name holds no dot. The
list travels with the caps request (in its query, bounded by the request-size bound) and is evaluated before any tree exists, on the
device's own `PATH` with `shutil.which` and with `importlib.machinery.PathFinder.find_spec` of the name under `python -I`, from a working
directory outside every
tree; a version string comes from a table of the device's own probe commands keyed by program, never from the name, so the sender picks
which program on the device is asked and never which command runs. A `module:` probe imports nothing: `find_spec` of a dotted name imports
every parent package, so a dotted name would let the sender pick which installed package's import-time code runs on the device ([probe]:
`find_spec('this.x')` ran the module and `find_spec('xml.dom.minidom')` loaded three), and `PathFinder.find_spec` of a top-level name finds
the spec and loads nothing. Every probe, the version commands included, runs inside the profile of a unit, started by the spawn function
under the request's guard. Caps lists which are satisfied; a device missing one takes no shard
and says which, in one sentence an agent can relay. Provisioning (installing what is missing) is a person's or the device setup's act,
not the runner's: `python -m poolhouse.device_setup --yes` on the device, or the project's own steps within the confinement.

### 6.6 The executor's boundary

The structure that 3.5 states, as the receiver builds it. A shard's scratch root is created by the executor, mode 0700, and holds three
directories:

```
<scratch root>/
    exec/    owned by the executor; children may read it and never write it
               the verified config bytes, {idfile} files (ids separated by NUL), argument lists
    tree/    the unpacked tree; the children's write set, with the step directories each step owns
    out/     a drop directory the executor opened; {result} files are created here by the executor
```

1. Everything the executor writes for a child is under `exec/`. The sandbox profile lets a child read `exec/` and denies every write to
   it, so no child can aim an executor-written path at a file of the device user's.
2. A result channel is a file the executor created in `out/` with `openat` on a held directory descriptor, exclusive and no-follow
   (`O_NOFOLLOW` covers only the last component, so a path through a directory a child could replace with a link is never used), and holds
   open. The child writes the path
   it was given; the executor reads through its held descriptor and never reopens a path a child can write. A child that swaps the name for
   a link changes where its own writes go, leaves the executor's file empty, and its units are `not reported`. The log is read the same
   way, through the descriptor the executor created for the child's output.
3. Every read of a thing a child wrote does `fstat` for a regular file, caps the size on the descriptor, and reports a malformed row by row
   number and rule, never by echoing its content.
4. The configuration is hashed and parsed once, from the same bytes. The Parser zone is **one long-lived confined worker per shard** (or per
   local run), started with the executor's limits (memory budget and `RLIMIT_CPU` from 10.6, one input descriptor, no network, write access to
   `tree/` for the unpack and to nothing else) and restarted after any error. It parses the TOML config, the JSON rows and the junit XML, and
   unpacks the tar of a sent tree. **Its output is a flat stream of tag-length-value records**: a fixed set of tags, a fixed bound per tag,
   decoded by one function with no recursion and no `loads`; the Runner imports no deserialiser (`json`, `pickle`, `marshal`) to read it, so
   nothing a hostile input nests can reach the Runner as a structure. **Any end of the worker other than a complete record stream followed by a
   clean exit is status 4 naming the rule**: an exception of any class (`RecursionError`, `MemoryError`, the `ValueError` a 5000-digit TOML
   integer raises [probe], an expat error), a signal, a timeout, a short read. The input size is checked first against a cap, the worker's
   memory budget divided by the amplification a test measures once per release by parsing a generated table and reading resident memory
   ([probe]: `tomllib` used about twenty bytes of memory per input byte on tables, and 16 s of CPU for 10 MB at a load of 90); a larger input is
   status 4 naming the size and the cap. The start of a confined interpreter costs real time ([probe]: a median 62 ms against 36 ms unconfined
   for one that imports `tomllib`, `json` and `xml.etree`), so a process per result file (600 invocations, 37 s) is not allowed;
   `tests/test_runner_overhead.py` measures the worker's start and a round trip. The validated config, bounded by the receiver's budget on a
   device, stays in the executor's memory and the copy in the scratch tree is never read again.
5. A junit document is parsed in the worker by `xml.parsers.expat` directly, with the encoding forced to UTF-8 (the parser overrides the
   document's own declaration [doc: `xml.parsers.expat.ParserCreate`], so a UTF-16 document is a parse error) and with `StartDoctypeDeclHandler` and
   `EntityDeclHandler` set to refuse, and a document that holds a NUL byte is refused (valid XML decoded from any encoding has none). The
   refusal is made inside the parser for every encoding, because a check of the bytes outside it misses
   what the parser decodes: [probe] a UTF-16LE document without a byte-order mark passed a check for the strings `<!DOCTYPE` and `<!ENTITY`, and
   the standard parser auto-detected it and expanded nested entities. The refusal does not rely on the parser's own limits on entity expansion (a
   device's system expat may be older), and no dependency is added.
6. Patterns the sender wrote (`list.starts`, `list.ends`, the group separator, globs) are literal strings and the core's linear glob
   matcher, so no sender text becomes a backtracking expression in the daemon.
7. Process ends: on Linux the sandbox's PID namespace and `--die-with-parent` end every descendant with the shard; on macOS the executor
   ends the process group and then sweeps for processes carrying the shard's marker (an identifier in the child's environment and never an
   authority), and a descendant that left both is an accepted risk (10.9).
8. The unpack runs in the worker with write access to `tree/` only; the Runner hashes the unpacked files afterwards through descriptors it
   opens itself, after `fstat` says regular file, so the tar and gzip handling of hostile bytes (the checks of A13) is in a confined process and
   not in the daemon that holds the pool's keys.
9. The budgets of 10.6 (time, disk, memory) are enforced here: the unit scratch volume or the polled disk floor, the polled memory share, and the
   worker's `RLIMIT_CPU` and memory share.

## 7. Presets: tools, never repositories

A preset is a TOML table with the same keys as a project file (`list`, `run`, optionally `deps`, `label` rules, `[requires]`) and an
optional `[confine]` table. It is named after a tool and holds no knowledge of any repository. It lives in `testfarm/presets/NAME.toml`; the
helper programs it runs (`testfarm/presets/NAME/*.py`) are ordinary Python modules that import only the core. A project may define
`[presets.NAME]` in its own file or in the user's `~/.config/poolhouse/testfarm-presets.toml`, which wins over a shipped one; the resolved
table, whichever file it came from, is what the reuse key hashes (5.3). `[confine]` names `read` (subpaths the tool needs to read, such as a
package registry or the toolchains: a shipped preset names `registry` and `toolchains` below a tool's home and never the home itself, because a
home also holds credentials), `deny` (exact paths of credential files inside an allowed subpath, such as a registry publish token, which the
profile denies by exact path; the list is the preset's knowledge and never the core's) and `env` (variable names the tool needs, such as
`VIRTUAL_ENV`, `CARGO_HOME`, `RUSTUP_HOME`, `JAVA_HOME`, proxy and certificate variables; a variable whose value is a URL is forwarded with its
user and password removed, host and port only). It is honoured from a shipped preset and from the user's preset file and is ignored in a
project's own `[presets.NAME]`, because it widens a sandbox and a project must not widen its own.

| Preset | LIST | RUN | Results | Group and labels | DEPS |
|---|---|---|---|---|---|
| `pytest` | the helper `pytest/list.py` runs `pytest --collect-only` with a plugin and prints `jsonl` rows | `python -m pytest -q {extra} -p testfarm_results {ids}` | `jsonl` from the plugin (true node ids, seconds, `reads`) | group = the part before `::`; markers become labels; `def` = the test function's AST hash | the `python-imports` helper |
| `cargo` | the helper lists test targets from `cargo test --no-run --message-format=json` | the helper runs the built executables the step produced | `exit` per target (libtest has no stable machine-readable output); `junit` with nextest | group = the target | the `cargo-crates` helper |
| `npm` | `["npm", "run", "--silent", "test:list"]` when the project defines it, else `files` | `["npm", "test", "--", "{ids}"]` | `exit`, or `junit` when configured | per file | the whole tree, unless the project declares `deps.globs` |
| `files` | every file matching `list.glob` | execute the file | `exit` | a unit is a file | the whole tree, unless the project declares `deps.globs` |

A preset's `exit` results are shown and not stored unless the project sets `[run] reuse = "exit"` (5.3). `poolhouse-test init` looks for the
tool's own marker files (a pytest configuration, a `Cargo.toml`, a `package.json`) and writes the four-line file for the tool it finds
and, after the project's measured first run, `[schedule] silence_margin` and `[reuse] canary`; it never overwrites a file and never invents
DEPS. The shipped presets are tested against generated neutral projects only (section 13, check C). A preset for a new tool is a file;
nothing in the core changes.

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
test_timeout = 120   # the owner's per-test value in pyproject.toml; a test that needs longer carries a label with its own test_timeout

[[step]]
name = "node"
run = ["python", "scripts/testnode.py", "build"]
inputs = ["app/**/*.rs", "app/**/Cargo.toml", "app/Cargo.lock", "tests/fixtures/phone-approval-vectors.json"]
tools = [["rustc", "--version", "--verbose"]]

[deps]
everything = ["pyproject.toml", "tests/conftest.py", "tests/known-fixtures.txt", "budgets.json"]

[schedule]
seed = "tests/ci-durations.json"
# silence_margin and total_margin are not shown: `poolhouse-test init` generates them from Poolhouse's own measured first runs and they are committed after those runs

[reuse]
# canary is not shown for the same reason: init writes it with the reuse, from the same first run

[failfirst]
mode = "enforce"
tests = ["tests/**"]
exempt = "Fail-first-exempt:"                              # Poolhouse's commit-message convention (docs/agent-policy.md); the library has none
line = "fail-first: {id} failed on {sha} with {error}"     # Poolhouse's report line

[hooks]
before_plan = [["python", "scripts/test_policy.py"]]       # agents may not run the whole suite; the preflight
```

Where Poolhouse's runs go belongs to the owner's settings (4.8) and its checkout is named by device setup; this file names neither.

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
- on a device: the same step runs in the device's confinement with `{shared}` keyed by the sender and the project (4.4), mounted read-only to
  units; a device without a toolchain fails `requires` with one sentence rather than trying to build;
- what stays Poolhouse's: the node binary's member list, the fixtures staged beside the workspace and the `phnc-` scratch folder are in
  `scripts/testnode.py` (declared as Poolhouse's step) and `tests/native_cargo.py` (its fixtures).

## 10. Security

The runner runs code on other devices, as the user, from a tree and a configuration a sender chose, and it runs handed code on this
device. The design assumes every input, caller, device and agent is hostile (AGENTS.md section 1) and derives trust in the receiver. The
structure is the zones of 3.5; each rule below is a test.

### 10.1 Threat model

Assets: the user's files, keys and sessions on every device in the pool; the compute and disk of a device; the integrity of the sender's
results and store; the sender's source tree (disclosed to every device it is sent to).

Attackers: a pool member that is compromised or malicious (as sender or as receiver); a device that is cloned or whose key is stolen;
a malicious or prompt-injected agent on the sender; a hostile repository whose `testfarm.toml` and tests the user runs; another user on
the same machine; a network attacker on the LAN.

Not defended: a sender that sends its own code to its own pool on purpose to harm the pool's devices is limited by confinement and
consent, not by anything about the code; a receiver that lies about results it ran (it can only affect passes it reports for itself, and
those are advice, 6.4); the code of a project the user has named as their own, which runs as the user and cannot reach the Owner zone (3.5); a
same-user attacker with filesystem access to the Owner zone, which is exactly what an unconfined hostile repository would be, and is why a
project the user has not named is never run unconfined (10.2) and why the host guards the Owner paths (4.8); the residues named in 10.9.

### 10.2 Authentication and who may run what

- **Identity is the pool's.** A shard operation is a signed request over mutual TLS pinned to the certificate the pool recorded for the
  member; the sender is the certificate, never a field of the request; `X-Poolhouse-Agent` and `by` are labels for the audit trail
  (`docs/test-farm.md`, "Mechanism"). The runner creates no identity, token or credential of its own.
- **The receiver's policy decides.** `admit()` (6.2) in a pool in dev mode: every member, until `remote-tests` is disabled on the
  device. In any other pool: the allow list, empty until a person adds a sender, never widened by joining, by `inbound_consent` or by being
  a member (`docs/pools-and-serving.md`). The list is per pool (`docs/design-multi-pool-devices.md`) and is kept with the pool's settings,
  where no child, confined or not, can read or write (the Owner zone, 3.5).
- **What the policy can restrict, on the receiver, by its own settings:** controls that limit a capability: the network a step may use
  (none by default), the slots, time, disk and memory a sender may hold (10.6), the resources a sender may lease, and the senders. A key in the sent
  `testfarm.toml` can only ask for less than the policy allows; it cannot widen it. There is no policy knob for which kinds of runner may
  be used (presets only, or any vector): a preset runs the tree's own `conftest.py`, plugins and tests, so such a knob would limit the
  shape of a command and not what runs, and a person who set it could believe it contained the risk.
- **Confinement is the receiver's.** The executor wraps every step and invocation in a deny-default profile it builds itself from
  `src/poolhouse/sandbox/` (`seatbelt.py` on macOS, `bubblewrap.py` on Linux). It reads the scratch tree, `exec/` (read-only, 6.6), the
  interpreter and system libraries and the toolchain paths the preset's `[confine]` names; it writes the scratch tree and the step
  directory a step owns; the runner's `owner/` directory, the settings, the pool's home, `~/.ssh`, the keychain and the home directory are
  denied by exact path, and none of them holds a path the profile allows, so no allow rule lies inside a deny. **The network is denied by default
  on both platforms.** On Linux the unit gets a new network namespace whose loopback is its own, so a test that binds a local server
  works. Seatbelt has no private loopback, so on a macOS device a sent unit whose label says `net = "loopback"` is refused (`not run
  (device refused)`, with the sentence, and reassigned to a Linux or WSL device) unless the receiver's policy lists the loopback ports to
  allow, which the profile grants by number (`sandbox.Net.only`): an allow list, because a deny list is a snapshot ([probe]: with the
  device's service ports denied by number the daemon stand-in was refused on IPv4 and IPv6, and a listener started after the profile was
  built was reachable). A local run of a named project on macOS gets loopback with the device's own service ports (the daemon's, the lease
  broker's, the workspace host's and any model server's, each known to the device) denied by number and unix sockets denied, a list the
  runner builds again when each unit starts and not once per run, in a new `sandbox.Net` mode, `loopback_except(ports)`; the loopback
  services of third parties are the residue named in 10.9. A sent test therefore cannot reach the owner's model server, which would be a
  second piece of work on the device's compute. A device with no confinement available takes no shard. A tree's own scripts and
  `launcher` never choose the profile. This is `design-test-selection` slice 6, and it is a hard prerequisite of sending a tree to any
  other device through the generalised path (migration step 8).
- **Local runs: classify, then confine.** The runner reads the project's identity from files and decides named or not (4.9) before it starts
  anything of the project. A project the user has named runs on this machine in the checkout, as the user, under an **open profile with a
  narrow deny**: the user's account for everything except the Owner zone (3.5), which is denied to read and write by exact path, with
  `(deny file-link)`. [probe] A profile that is open except `(deny file-read* file-write* (subpath <owner zone>))` refused a child the key on
  read and on append and refused a hard link and an APFS clone of it. Its LIST, `deps.command`, hooks, steps and units use the project's
  `launcher`. The narrow deny is a speed bump and not a wall (10.9 item 7). **A project the user has not named runs confined, in a scratch
  copy.** Its LIST, `deps.command`, steps, hooks and units, and the commands that run them (`list`, `check`, `init`, `plan` and `affected`
  among them), execute under the same sandbox backend and on the same executor a device's do, in a copy of the transported set that the runner
  makes (APFS `clonefile` where the volume has it, a copy otherwise) from regular files it opens without following links. The checkout is
  read-only for `git` at classification and absent from every child's profile; the scratch tree and the step directories a step owns are the
  only writable paths; results and a step's declared products come back through the runner's descriptors. [probe] A confined child with the
  checkout writable wrote an executable hook into the repository's metadata and appended to `testfarm.toml`, and the next unconfined action
  would have run what it left; neither is reachable from a copy. There is no network, no `owner/` directory, pool home, `~/.ssh`, settings file or
  other project's store, and no `.git` or home directory: a test that needs one fails until the person names the project with
  `poolhouse-test own` (4.8), one yes per project, the only step this adds. The copy's time is a line of the report (`built`). Its `launcher`
  only narrows, its children get the allowlist environment of 10.5, and it gets no lease and no loopback whatever its file says. The runner
  learns that a project is named from the user's settings and the identity check of 4.9 and from nowhere else. The store entries of a confined
  run are in the `confined` domain, which no evidence reader accepts (5.3).

### 10.3 What a sent configuration and test list can do

The sent tree contains code, so the receiver runs sender code; what it refuses to do is *build the command from sender text*. The receiver
hashes the bytes of `testfarm.toml` in the tree it verified, parses those same bytes once (6.6), validates them against the schema,
bounds `slots`, `timeout` and `env` by its budget for the sender (10.6) and its policy, and builds the argument vector from the verified
`run.argv` and the named set: only
`{ids}`, `{idfile}`, `{result}`, `{extra}`, `{jobs}`, `{step:NAME}` and `{shared:NAME}` are substituted, each as a separate argument,
never through a shell. `extra` is the verified set's own: the start request carries no `extra`, no step list and no deadline, and a step
that rewrites the config in the scratch tree changes nothing, because the executor never reads that copy again. A `NAME` in a placeholder
or a table, and every environment variable name, matches the grammars of section 4 or the shard is refused; `{step:../x}` is refused at
validation. Hooks and launchers in the file are not run on a device. A shard asks for units by id and label name; the receiver checks each
id against the grammar (10.4), each label name against the verified file, and the shard against its own set and bounds. It does not run
the sender's LIST to compare, because LIST is the sender's code and what it prints proves nothing about the ids.

### 10.4 The id grammar and untrusted lists

An id is UTF-8 text in normal form C, at least one byte, with no character of Unicode general category Cc, Cf, Zl, Zp, Co, Cs or Cn (so
no control character, no line or paragraph separator such as U+2028, U+2029 and U+0085, no zero-width, direction-control or tag character),
not beginning with `-` or with a prefix in `run.refuse_prefix`, and with no repeat inside a shard. An id is not a path and has no length
bound of its own: an id carried as an argument must fit the argument space of the invocation on its target (the platform's argument
maximum less the measured size of the environment and of the other arguments, and on Linux the longest single argument), and a unit whose
ids do not fit a target stays off that target and the plan says why; ids carried in `{idfile}` are bounded by the request-size share
(10.6). Ids in `{idfile}` are separated by NUL bytes and the preset helpers read them with `split("\0")`, never `splitlines()`; a project
whose tool reads an id as an option sets `run.ids_after = "--"`. The units of one shard are bounded only by the receiver's request-size
bound (10.6) and not by a count of the runner's own; a unit group is checked as a whole. An id is only ever an argument or an entry of
`{idfile}`. The sender's LIST output is data: lines over the bound, binary output, a LIST that never ends (bounded by output size and its
hang guard), duplicate ids, a nonzero exit and an empty result are errors that name the first offender (section 1).

Every number read from LIST, a result row or a device is checked against its range, or the row is an error that names its number:
`seconds` is finite and between zero and the wall time of the invocation that produced it (for a device unit, the interval the sender
measured), counts and capacities are non-negative integers, and `deps` entries are normalised tree-relative paths. NaN, infinity and a
negative number are refused; none reaches a sort, a deadline or the history. The numbers of the project's own file are checked the same way
(section 4).

A slice index and count (5.2) are never read from a result, a device or the environment: the runner computes them as positive integers (the count at
least 1, the index from `slice_base` to `slice_count + slice_base - 1`, with no fixed maximum), formats them in decimal with no sign or padding, and substitutes
them as whole arguments of the argument vector (or inside one argument, never into a shell string). A template with a placeholder
other than the two, a repeated one, or one in the program name is refused when the file is loaded; a device receives the filled
vector with ids from this grammar and refuses anything else.

### 10.5 Output and environment

- **Output is data.** Every byte a unit, a LIST, a hook or a device sends (output, messages, names), local and remote alike, is decoded as
  UTF-8 with replacement, stripped of ANSI/OSC/CSI escapes, carriage-return line overwrites, other control characters (except newline and
  tab) and every character of the categories Cf, Zl, Zp, Co, Cs and Cn (direction overrides and isolates, zero-width characters, tag
  characters), truncated per line and in total, and printed inside a frame the sender prints (`--- <name>: untrusted output <nonce> ---` and
  `--- end <nonce> ---`, the nonce 128 random bits made for the run and printed in the first line of the report, so an end marker forged in
  a unit's text does not match) with a `[device]` prefix the *sender* adds on every line, so a device cannot forge another device's line, a
  summary line
  or an exit status, and an agent that reads the report can tell the runner's text from a test's. The summary and the status are printed
  after the frames and never inside one. A name is sanitised the same way (`report.plain`). Result messages and failure texts are bounded
  by the receiver's result bound (10.6), shared among the fields. `--json` puts that text in fields named `untrusted_*`.
- **The child environment** depends on where the command runs. The children of a named project on this machine inherit the user's
  environment minus the runner's own names and receive the permit descriptor variable. A confined child of an unnamed project, and every
  child on a device, gets an allowlist, not today's denylist (`shard_run.DROPPED`): `PATH`, `LANG`, `LC_*`, `TZ`, `TMPDIR` (a scratch
  path), `HOME` (a scratch path), the names the preset's `[confine] env` lists (a URL-valued one without user and password), the names the
  verified file's `run.env` sets, and the
  permit descriptor variable; nothing else, so agent markers, tokens and the sender's variables do not arrive. `run.env` may set `PATH` or
  `LD_PRELOAD` for its own children, which is inside the confinement and acceptable.
- **Sender bytes are parsed in the Parser zone** (6.6): XML with a DOCTYPE or an ENTITY is refused by the parser itself in every encoding, any
  end of the worker other than a complete record stream and a clean exit (an exception of any class, a signal, a timeout, a short read) is an
  error that names the rule, and a junit or JSON result larger than the receiver's result bound (10.6) is refused.

### 10.6 Resource bounds and where they come from

Every bound has a source; where the source is "the owner, in source" it is a row of `docs/limits.md`, and the rows of the deleted Rust
host move to the Python module that now holds them. Section 15 lists them, and `poolhouse-test limits` prints the library's own table (each
bound with the value measured now and the rule that gives it), because a project that imports the library cannot edit Poolhouse's
`docs/limits.md`. The rule for every bound, new or carried over: derived from the device (free disk of the scratch volume, free inodes,
logical CPUs, memory), from the operating system (the argument maximum), or set by the receiver's person or by the project's own file, never
typed in by the runner; **a clamp with no source is not written.** The receiver owns three budgets for a sender, below, and a key in the sent
file can only ask for less.

**Disk.** The receiver measures its free scratch disk and free inodes when it takes a shard; the shard's share is that free amount divided by
the shards it holds, this one included. The share is one budget spent in a fixed order, so the parts cannot add up to more than it: the packed
tree is accepted up to the share and its measured size is subtracted; the unpacked tree is accepted up to what is left, measured as it
unpacks, and subtracted; of what is left the output files (log, tee, result, junit) take equal parts, their count being the number of those
files, and the unit scratch takes the rest. The unit's scratch is a volume of that size where the platform makes one (Linux `tmpfs` with
`size=`, macOS a sized sparse image); where it cannot be, the executor ends the process group when `statfs` shows the volume's free space
below the amount measured at the start less the shard's share, polled at the hang guard's cadence. Seatbelt cannot limit the bytes a child
writes in total and `RLIMIT_FSIZE` is per file [probe], so this rule, with the sized volume where there is one, is what stops a unit that
writes until stopped, on both backends.

**Time.** A device bounds a unit by the smaller of the sent bound (the unit's `timeout`, else its recorded typical duration times
`total_margin`, 5.10) and its own ceiling for the sender. The ceiling's sources, in order: the seconds the receiver's person gave for this
sender, one for a unit and one for a shard, with the slot-seconds it may hold (given at the `allow` act, or as the device's policy for the
members of a pool in dev mode, both through the gate, 4.8); else the longest wall time the device itself measured, on its own clock, for any
unit of that project from that sender on an earlier shard. A sender with neither gets no first shard: it is refused with the sentence naming
`poolhouse-test policy`, which is `not run (device refused)` and reassigned, so nothing waits. The sender's bound on a shard (5.10) is a
second bound and never the receiver's.

**Memory.** Where the platform lets a child's memory be limited (a Linux cgroup memory limit through the sandbox backend, when the device
delegates one), the shard's memory share, the device's free memory when it takes the shard divided by the shards it holds, is that limit.
macOS has none: `setrlimit(RLIMIT_AS)` and `RLIMIT_DATA` fail with `EINVAL` and a 6 GiB anonymous map was not stopped [probe]; there the
executor polls the group's resident memory at the hang guard's cadence and ends the group over its share, and 10.9 states that the platform
does not bound a confined child's memory. The Parser worker's memory budget is a share of the same kind and its time is `RLIMIT_CPU`, which
works on macOS [probe]; its input cap follows from the budget (6.6).

The other bounds are parts or consequences of these: the packed-tree limit a receiver accepts is at most its share as above; slots granted to
one sender are at most the device's free slots at admission; the output kept is the executor's bounded log; the slices a sender builds are at
most its free memory divided by the measured size of one planned unit; the store, the history and the evidence stay inside the volume's free
space through the idle rule of 5.3. A runaway shard (output over the receiver's log bound, time over a bound, tree over the bound) is
cancelled with a defined status (124) and the group is killed.

### 10.7 Not starving the device's owner

A shard takes the device's slots through the device's own admission, class `background`, so it queues behind the owner's work. The
device answers caps with no free slots while its own admission holds or queues work of the device's owner (the resources its policy marks
as the owner's: on a Poolhouse device a GPU, a benchmark or a foreground lease), and the executor ends the shard's process group when the
admission asks for the slots back (`design-test-selection` 6.3 step 5; AGENTS.md section 1: a row measured beside a shard is void). The
executor knows that signal and nothing about what a GPU or a benchmark is. The network denial of 10.2 keeps a sent test from prompting the
owner's model server on loopback.

### 10.8 The attack list (for the break-it review before step 8 is built)

A non-author tries each; findings are fixed or named as accepted risks with the owner (AGENTS.md section 1). The cases of the policy come
first, then the ones this design adds. Each row names the test that proves its answer, written first and watched failing (section 14); a test named
with no file is in `tests/test_testfarm_attacks.py`.

| # | Attack | Where it is answered | Test |
|---|---|---|---|
| A1 | A copied or cloned pool or device: a clone with a member's key answers under its fingerprint | a signature proves a key, not a machine: the install-instance id the sender pins with the fingerprint (6.2) makes a copy of the key made without the id a second instance that gets no shard and is reported (a copy of the whole disk carries the id and is the same instance, 10.9 item 3); such a copy's `remote` pass is advice and stored only when a second source with another instance id agrees; a copied pool is a different pool id and its results are dropped | `test_testfarm_attacks.py::test_a1_a_second_instance_of_a_fingerprint_gets_no_shard` |
| A2 | Replay of a start, an upload or a result | shard id is 128 random bits and inside the signed request and the result; requests are bound to a time, a nonce, the method, the target and the host (`sealing.request_data`, `macauth`'s nonce table); a result for another shard, tree or config fails; the echo proves freshness only (6.4) | `test_a2_a_replayed_start_and_a_result_for_another_shard_are_refused` |
| A3 | A forged or below-threshold signature | the certificate check; results are not signed and one device's word is below any threshold for skip evidence: a device pass is stored only when a second source agrees (5.3, 6.4) and never creates `local` evidence | `test_a3_one_device_pass_is_shown_and_not_stored` |
| A4 | A compromised or malicious member as sender: runs code, fills the disk, hogs slots, starves the owner, reads other senders' shards, aims the executor at a file | confinement with the network denied (10.2); the executor's boundary (6.6); the receiver's time, disk and memory budget for a sender (10.6), per-sender and per-device bounds, background class, kill when the admission asks for the slots back, ownership of the shard folder by the first uploader | `test_testfarm_confinement.py::test_a_step_that_links_every_executor_path_to_a_canary_file_reaches_nothing` and `::test_a_unit_cannot_connect_to_the_daemon_a_model_server_or_the_lan_gateway`, `test_testfarm_policy.py::test_a_sender_with_no_budget_gets_no_first_shard` |
| A5 | A compromised member as receiver: returns a false pass, a false failure, fewer rows, rows for ids not assigned, another shard's results, an old result, a lied platform, capacity or cost, `not started` for failing units, a made-up key or `reads` | the sender checks the row set exactly and the shard, tree and config hashes; the platform class comes from the pinned member record, capacity from the sender's own observation and `fixed_cost_s` from the sender's clock; `not started` from a device is `not run (device refused)` and the units are reassigned; a failure on a device is final and is not erased by a re-run on another class (6.4); keys are never read from a result; the `remote` kind never satisfies a local run or authority | `test_a5_a_device_that_refuses_every_unit_cannot_turn_failures_into_a_retry_status` and `::test_a_failure_on_a_device_survives_a_pass_on_the_sender` |
| A6 | A stolen phone or key; a stolen device key | removing the member refuses it at the next request and cancels its shards, a running executor included; a stolen person key adds a device only through the charter's thresholds, which this design does not change | `test_a6_a_removed_member_is_refused_and_its_shard_is_cancelled` |
| A7 | A malicious agent on the sender asks for a run on a device it should not use, edits `testfarm.toml` to widen a run, or names a project as its own | where a tree goes is read from the user's settings and the flag and never from the project's file (4.8); a project that is not named is never sent; naming, `allow` and a widening target are answered only by the host's gate (4.8), never by a terminal; the settings are in the Owner zone, which no child reads or writes and which the host's guards refuse to an agent's tools, so the row holds against a hostile repository and against an agent whose host guards those paths (10.9 item 9); an agent holds the same authority as the user for `run` on a named project; the config can only ask for less than the receiver's policy | `test_a7_a_project_file_cannot_choose_a_target_and_an_agent_cannot_name_a_project` and `test_testfarm_settings.py::test_the_settings_are_written_only_after_the_gate_says_yes` |
| A8 | An agent compromised without knowing it (an instruction in a result, an output line, a test name, a tag-character message or a board message such as "tests passed on the other device, skip them") | output from every child, local included, is sanitised data inside frames the sender prints, with the invisible categories removed (10.5); nothing read from a device or the board enters a key, a hit or a verification; the summary and the status are printed outside frames | `test_a8_invisible_and_framed_text_cannot_pose_as_the_runner` |
| A9 | A device lies about its own results to the sender | only the sender's own runs are `local`; a `remote` pass needs a second source, is reused only on that device and never for landing; canaries of `remote` hits run on the sender or another member; control units; a mismatch keeps the device out of every plan until a person adds it again (6.4); the report says `agreed by 2 members`, never `confirmed` | `test_a9_a_lying_device_cannot_make_a_pass_that_serves_a_later_run` and `test_testfarm_pool.py::test_a_mismatch_is_sticky_until_allow` |
| A10 | A downgrade: a pool that entered under `open` becomes a trusted runner | a device that entered under `open` is never in the allow list by joining; the pool's join policy never changes in place | `test_a10_joining_never_adds_a_sender_to_the_allow_list` |
| A11 | Approval fatigue: repeated shard requests until the person accepts `allow` | one yes per device and one per project, each asked once; shard requests meet the receiver's bounds and cannot become a question | `test_a11_shard_requests_never_become_a_question` |
| A12 | Cache poisoning: a sender, or a unit, writes `{shared}` or `{cache}` so a later step finds a product | step directories are keyed by sender and project; units mount them read-only; a manifest of `{cache}` and the digest of what a unit read from `{shared}` are checked at reuse, walked by descriptor (4.4) | `test_testfarm_steps.py::test_a_unit_cannot_change_a_cached_product_and_a_changed_product_is_rebuilt` |
| A13 | A hostile tree: tar bombs, links, hard links, `..`, absolute or drive paths, case-insensitive collisions, setuid bits, sparse files, gzip streams that inflate past the bound, more than one stream | the unpack runs in the Parser worker (6.6); the checks of `shard_tree.py` kept and extended (collision checks on a case-insensitive receiver, mode normalisation); inflation checked before any header is read; the canonical hash (6.3); there is no Windows receiver (6.2) | `test_a13_every_hostile_tree_is_refused` |
| A14 | A hostile `testfarm.toml`: unknown keys, huge arrays, deep nesting, type confusion, a `run.argv` with a shell, a path that leaves the tree, placeholders in the wrong place, a regular expression, a name with `..` | the strict schema; parsed once in the Parser zone with any end of the worker other than a complete record stream and a clean exit refused as status 4, and sizes bounded first; only listed placeholders; name and environment grammars; no key takes a regular expression; the config is never read again | `test_a14_a_config_rewritten_after_the_hash_changes_nothing` and `test_testfarm_config.py::test_every_hostile_file` |
| A15 | Argument injection through an id (`--option`, a path that escapes, a newline, a line separator) | the id grammar by Unicode category in normal form C; NUL-separated `{idfile}`; `ids_after`; `refuse_prefix` (10.4) | `test_a15_ids_with_line_separators_and_prefixes_stay_one_argument` |
| A16 | A LIST that prints a huge or infinite output, binary data, nothing, a nonzero exit, or ids that do not exist | bounded output and hang guard; a failed or empty LIST is status 4; `ignored N lines` is printed; the receiver checks the grammar, the set and the bounds, which are its own, and does not trust a LIST comparison made by the sender's own code | `test_a16_a_failed_empty_or_filtered_list_is_not_a_green_run` |
| A17 | Result files: junit entity expansion, a result for ids not assigned, duplicates, a path in `reads` that escapes the tree, a giant `message`, a link in place of the file | read through descriptors the executor opened, regular files only, size capped; XML with a DOCTYPE or ENTITY refused by the parser itself in every encoding; exact row set; validated numbers; `reads` normalised to tree paths or dropped | `test_testfarm_confinement.py::test_a_result_path_replaced_by_a_link_reads_nothing` |
| A18 | Terminal injection through output: escape sequences, carriage returns, a fake `[other-device]` prefix, a fake summary or status line, direction and tag characters | 10.5; the prefix, the frames (their markers carry the run's nonce) and the summary are the sender's, for local output too | `test_a18_a_fake_prefix_or_summary_inside_output_stays_in_its_frame` |
| A19 | The source tree is disclosed to a device | only a project the user named is sent, to the targets the user chose; the set is the tracked files plus untracked files a unit names through a glob that is not the whole tree (no `.env.local`), listed at the first send, and only to the devices the person agreed (4.8); the first send prints the file count and top-level names; `plan --show-tree` | `test_a19_untracked_files_are_not_sent_unless_named` |
| A20 | Time-of-check to time-of-use between the hash and the run, symlink races in the scratch tree | the executor-owned `exec/` that children cannot write; descriptors held open; the config in memory; hash after unpack; no links accepted | `test_testfarm_confinement.py::test_a_step_that_links_every_executor_path_to_a_canary_file_reaches_nothing` |
| A21 | Fork bomb, memory hog, disk fill by a shard, a `setsid` child that outlives the shard | process-group kill; PID namespace and `--die-with-parent` on Linux; a marker sweep on macOS with the residue in 10.9; the disk, memory and time budgets of 10.6 (a sized scratch volume or a polled floor, and a polled memory share on macOS with the residue in 10.9); bounded log | `test_a21_a_detached_child_does_not_survive_a_shard_on_linux` and `test_testfarm_confinement.py::test_a_unit_that_writes_until_stopped_and_one_that_allocates_until_stopped_are_ended` |
| A22 | The step or hook of a repository the user was handed runs on the sender | a project the user has not named runs LIST, `deps.command`, steps, hooks and units confined, including for `list`, `check`, `init`, `plan` and `affected` (10.2), in a scratch copy, after the project is classified from files with no git and with every git call confined (4.9); nothing sends a hook to a device | `test_a22_check_on_a_fresh_clone_runs_its_list_confined` |
| A23 | A project's `testfarm.toml` or the reuse store reads outside the project (a symlink to a home file in `inputs`, a DEPS row with an absolute or `..` path) | such rows are refused; the runner reads only the project root and its state directory, and check B proves it under the sandbox's read profile, children included | `test_testfarm_readonly.py::test_nothing_outside_the_project_and_the_state_is_read` |
| A24 | Takeover by whoever is merely present on the LAN | dev mode trusts every device that joined, which `docs/fleet.md` states; with the default target `local` a project's tree goes nowhere until the user names the project and chooses another target, so that trust does not reach every consumer by default; a `target` or `--on` that names a device set the person has not agreed is a widening that asks the host's gate (4.8); a pool that is not in dev mode requires the allow list; the kill switch is `remote-tests` | `test_a24_nothing_is_sent_until_the_project_is_named_and_a_target_chosen` |
| A25 | An old build on one device (a downgrade of the protocol) | caps carries `protocol` and the build; a mismatch is refused, never run; the device's statement that it can confine is its own build's and is measured by the confinement control unit of 6.4; a device that lies is a compromised receiver (A5) | `test_a25_a_protocol_or_build_mismatch_is_refused` |
| A26 | A sender queues work or sets tiny timeouts to make a device's own work look failed or late | the device's own work is admitted first (background class); the network denial keeps a sent test from the owner's model server, and on a macOS device a sent `loopback` unit is refused unless the receiver lists ports (10.2); a device's refusal is reported to the sender only | `test_a26_a_sent_test_cannot_prompt_the_owners_model_server` |
| A27 | Slice injection: a hostile index or count (`--slices '8; rm -rf'`, `-1`, `1e9`, a count that makes the tool run one slice out of range, a slice template that smuggles a flag), or a device that reports slices the sender did not assign | the index and count are integers the runner makes, positive, decimal only, with the count bounded by the sender's memory (10.6), put in as whole arguments (10.4); `--slices` is parsed as an integer or refused with status 4; the template allows exactly the two placeholders; the sender checks the slice set it received (exactly the slices it assigned, once each) as it checks ids (A5) | `test_a27_a_hostile_slice_count_or_index_is_refused` |
| A28 | A slice count change used to keep a stale pass (slice 3 of 8 satisfying slice 3 of 6), a hidden gap where the tool's partition skips tests, or slices partitioned differently on two devices | the count and index are in every slice key (5.2); the plan states that coverage is the tool's partition and not checked; slices of one run go only to targets of one class with equal tool digests | `test_a28_a_changed_slice_count_invalidates_every_slice` |
| A29 | A hostile repository, run by the user, forges a pass in the store of the user's real project (the directory is named by a public url and the key is an unkeyed hash) | the project's identity is read from files, so a `.git` that points at a named repository is not named (4.9); no child of any run, named or not, reaches the state location or the key (3.5); entries are signed with a key per project and carry a domain, in a directory per project and domain (5.3); no evidence reader accepts `confined` | `test_testfarm_store.py::test_a_confined_run_cannot_write_or_disable_an_entry_of_another_project` and `::test_a_named_unit_cannot_read_the_key_or_write_the_store_or_the_settings` |
| A30 | A device or a unit keeps a shard alive with heartbeats and no progress | a heartbeat is not progress; the sender bounds a shard by the sum of its units' bounds on its own clock; a unit with no bound is not sent (5.10) | `test_testfarm_pool.py::test_a_device_that_only_heartbeats_is_ended_by_the_sender` |
| A31 | A unit that skips and exits 0 is stored as a pass | `exit` results are not stored unless the project opts in; `jsonl` and `junit` carry `skip` and `unstorable` (5.3) | `test_testfarm_reuse.py::test_an_exit_result_that_skipped_is_not_stored` |
| A32 | Fail-first evidence written by hand, or for another base | evidence is produced by the runner and signed under a key no child of any run can read, so a new test cannot forge its own (5.3); the check compares trees against the one base, the range's start (5.9); a fixture-only change makes units `unverified` (5.9) | `test_testfarm_failfirst.py::test_forged_and_wrong_base_evidence_is_refused` |
| A33 | A CI shard job drops a unit: shards divided from each machine's store or history | `divide` reads only the sorted LIST, the config and the seed; `--verify-cover` fails unless every unit is in exactly one list (5.2) | `test_testfarm_plan.py::test_two_machines_with_different_stores_divide_alike` |
| A34 | A forged entry survives because the canary draw is predictable | the draw comes from the operating system's entropy; a `remote` entry's first reuse is always a canary on the sender or another member (5.3) | `test_testfarm_reuse.py::test_two_runs_of_one_tree_sample_different_hits` |
| A35 | A directory made "named" without the person naming it: a `.git` file or directory that points at a named repository, a stray `.git` above the directory, `GIT_DIR` in the caller's environment | the identity record is read from files with no git, requires the work-tree top-level, the back-pointer of a linked worktree and no `commondir` in a `.git` directory; the root walk never climbs into a parent repository (4.9) | `test_testfarm_settings.py::test_a_directory_that_points_at_a_named_repository_is_not_named` |
| A36 | A repository's own git configuration is code: `core.fsmonitor`, `core.hooksPath`, a filter driver, `diff.external` | classification runs no git; every git call runs in the read-only profile with fixed overrides and environment, from a closed list of subcommands that apply no filter (4.9) | `test_testfarm_confinement.py::test_a_repository_whose_git_configuration_runs_a_program_runs_nothing` |
| A37 | A confined run writes what the next unconfined action runs: repository hooks, `testfarm.toml`, shell-integration files | an unnamed project's units run in a scratch copy and the checkout is not in their profile (10.2) | `test_testfarm_confinement.py::test_a_confined_unit_cannot_write_the_checkout_testfarm_toml_or_repository_metadata` |
| A38 | A named unit reads the key, writes an entry of any project or edits the settings | the Owner zone is denied to every child by exact path and the key is per project (3.5, 5.3) | `test_testfarm_store.py::test_a_named_unit_cannot_read_the_key_or_write_the_store_or_the_settings` |
| A39 | The hang guard is satisfied by `inf`, by one margin or before the first unit finishes; a unit prints a byte at a time; a spawn bypasses the guard | numbers are finite and in range; both margins are required; a byte is progress only until the total guard; the first unit needs a terminal or `--timeout`; one spawn function (4, 5.10, 3.1) | `test_testfarm_config.py::test_non_finite_and_one_sided_guards_are_refused`, `test_testfarm_timeouts.py::test_a_unit_that_prints_a_byte_at_a_time_is_ended_by_the_total_guard` and `::test_every_process_the_runner_starts_goes_through_the_one_spawn_function` |
| A40 | A sender sends a unit with `timeout = 1e9`, or a loop that fills the disk or the memory of a device | the receiver's budget (10.6): a ceiling with a source, a share of the disk spent in order, a polled memory share, no first shard without a time budget | `test_testfarm_policy.py::test_a_sender_with_no_budget_gets_no_first_shard` and `test_testfarm_confinement.py::test_a_unit_that_writes_until_stopped_and_one_that_allocates_until_stopped_are_ended` |
| A41 | XML or TOML that ends the parser in a way the code did not list: UTF-16 without a byte-order mark, a 5000-digit integer | the refusal is made inside expat for every encoding and any end of the worker other than a clean record stream is status 4 (6.6) | `test_testfarm_confinement.py::test_xml_in_every_encoding_with_a_doctype_is_refused` and `::test_any_end_of_the_parser_worker_is_status_4` |
| A42 | `[requires] module:NAME.sub` runs an installed package's import-time code on the device | a `module:` name holds no dot, `PathFinder.find_spec` imports nothing and every probe runs in a unit's profile (6.5) | `test_testfarm_pool.py::test_requires_never_imports_a_package` |
| A43 | A delegated lease credited to a dead or recycled process id, or a model server that outlives its group | the broker identifies a process by handle, drops a delegation on the leader's exit and keeps the lease held while a detached server runs (5.5) | `test_serve_delegation.py::test_a_dead_connecting_process_gets_no_delegation` and `::test_a_server_that_outlives_the_group_keeps_the_lease_held` |
| A44 | A descriptor the runner leaves open is read by a confined child | the spawn function keeps exactly 0, 1, 2, the permit and the result descriptor (5.5) | `test_testfarm_confinement.py::test_a_confined_child_holds_only_its_own_descriptors` |

### 10.9 Accepted risks

Named with the owner, because each is a residue of the platform and the owner accepts it or asks for the stricter unit named after it.

1. **A macOS descendant that leaves the process group and the marker sweep.** Seatbelt gives no PID namespace; the executor ends the
   process group and then sweeps for processes that carry the shard's marker, and a process that double-forks and clears its environment
   survives the shard. It stays inside the same sandbox profile. The stricter unit is an endpoint-level process-tree watch.
2. **Several users on one machine.** The slots directory is per user and each user's broker counts the other's load as outside load, but two
   users who ask at the same moment can overload the machine. The tool is a developer tool on a machine its user owns.
3. **Two members counted as two sources.** The install-instance id separates a copy of the key made without it from the original, and only
   while the original also contacts the sender; a copy of the whole disk, a snapshot restore or a backup restore carries the id and is the
   same instance, which is the device. One person who runs two instances under two fingerprints on one machine (dev mode lets a joiner join
   twice) can make two members agree. The effect is a `remote` entry that serves that device only and counts for no authority.
4. **Third-party loopback services on macOS.** Seatbelt has no private loopback and its port rules are a snapshot. A sent unit's
   `net = "loopback"` is refused on a macOS device unless the receiver lists the ports to allow, and such labels go to a Linux or WSL device; a
   local run of a named project gets the device's own service ports denied by number as of the unit's start, and a service started later, or
   one the device does not know of, is reachable.
5. **A control unit catches an executor that ran nothing or confined nothing, not a device that recognises it.** The canary and the second
   source are the defence, and a mismatch from either keeps the device out of every plan until a person adds it again.
6. **Naming binds a repository, not a branch.** A hostile branch checked out in a worktree of a named repository runs as the user. The Owner
   zone is denied to it, so it reaches no key, store or settings (3.5).
7. **The narrow deny is a speed bump, not a wall.** A named child that asks LaunchServices to start an unconfined program (`open x.command`)
   leaves the deny [not run: it would open a Terminal window on the owner's screen]. The denial of the Owner zone stops a direct read or write
   and not a child that starts a program outside the profile's reach.
8. **Memory of a confined child on macOS is not bounded by the platform.** The executor polls the group's resident memory and ends it over its
   share, so a burst between two polls is not stopped.
9. **No host guard, no protection for the Owner paths.** The settings are an ordinary file in the user's account. An agent with a file-write
   tool, on a host that does not refuse writes to `ph.test.owner_paths()`, can name its project or widen a target. A7 holds against a hostile
   repository and against an agent whose host guards those paths; the library cannot know that a person answered without a host that says so
   (4.8).
10. **A terminal bounds the first unit only as far as a person watches it.** A process that allocates a pseudo-terminal for itself satisfies
    "attached to a terminal" (5.10) with no person behind it; its first unit then has no bound until one duration is recorded, and the caller's
    own tool timeout is the only end. The run is that process's own, and nothing outside it is affected.

## 11. Failure behaviour, exit codes and determinism

**Exit status** is derived from the causes present, and a failure outranks every other cause: the status is 1 whenever any unit failed
(a failed, `timed out`, `not reported` or `suspected: ended its device` unit, or a `canary mismatch`); otherwise it is the numerically
highest of the other causes
present. The report and `--json` always carry the full cause set and the per-unit outcomes, so a wrapper that checks for 1 never misses a
failure and a wrapper that retries on 70 or 75 never retries a run that had one. `not run` (a device did not finish: status 70) and
`not started` (this machine was overloaded: status 75) are different outcomes and are never merged. A `flaky` unit (a cached pass that
failed fresh and passed on one more fresh run) is a pass and is reported `flaky`. A shard refused because a control unit failed is `not run
(device refused)` and gives 70 like any refusal. The table:

| Status | Meaning | Source |
|---|---|---|
| 0 | every selected unit passed or was reused; an `--affected` selection that is empty because nothing changed since REF is 0 and says `nothing changed since REF` | the runner's existing rule that "no tests selected" because nothing changed is success (`scripts/test` `clean`) |
| 1 | at least one unit failed, whatever else happened | pytest's failure status, the existing report |
| 4 | refused before anything ran, or nothing ran: invalid config, id, selector or tree; LIST failed or printed no unit (unless `[list] allow_empty`); a selector or set that matched nothing; no hang guard set (`[schedule] silence_margin`, or a `timeout` on every command the runner starts); a hook refused; a number in the project's file outside its range (section 4); the first unit of a project with no bound (5.10); a send asked for a project the user has not named; no unit passed or was reused and at least one skipped or ran no tests (`nothing ran`); the tree changed during the run and nothing failed | `scripts/test` argument errors and the tree-changed rule |
| 6 | busy: a whole-selection job is already live | `BUSY_EXIT` |
| 70 | a device did not complete its part: not in the pool, refused, unreachable, vanished mid-run, missing a requirement | `test_on.NOT_RUN`, `docs/test-farm.md` |
| 75 | at least one unit never started on this machine: it waited for a permit or a resource past the queue limit while the machine was overloaded; nothing about it failed | `EX_TEMPFAIL` of `sysexits.h`; the queue limit is the project's `[schedule] queue_wait` |
| 124 | a build step ended by its guard, a shard ended by the sender's bound, or an output bound | `shard_run.execute` |
| 130 | cancelled | `scripts/test` job cancel |

**A device vanishes mid-run.** The sender holds one pinned-TLS stream per shard and the receiver reports on it as things happen: unit
start and end events, rows, output and heartbeats. A device is lost when its stream fails (a reset, or the TCP keepalive timeout of the
operating system) or carries no event of any kind, a heartbeat included, for the liveness stretch: the measured round trip of the stream
times the project's `silence_margin`. Silence is counted only across the sender's own completed waits; any gap in which the sender made no
wait (a starved process, a paused terminal) restarts the count, so a stall of the sender is never read as a stall of the device. The lost
device's part ends and the report says, loudly and on stderr, `<device> stopped answering <N>s ago (measured) while shard <id> ran there;
<k> units had finished and are kept, <m> did not and were not run anywhere else`, lists the unfinished units as `not run (device lost)`
(neither failed nor reused), and the status is 70, or 1 when any unit failed. The rows that arrived are kept, reported and stored as 5.3
says. The other targets finish and are reported. Nothing re-runs on another device or locally by itself. Running the same command again
continues: reuse serves every unit that passed, so exactly the not-run remainder runs, on whatever is reachable then. A unit that was
running when two devices vanished is reported `suspected: ended its device` (6.4). The shard on the vanished device is cancelled when the
device returns (its shard is dropped by its own idle rule otherwise).

**A device refuses.** A device that refuses at start, or says it could not admit a unit, ran nothing: its units are reassigned to another
target once, and a device that refuses more than once is `suspect` (6.4). The status is 70 only for a unit that ends up not run.

**A local invocation dies** (a signal, an out-of-memory kill): its units fail with the signal named; units of a batch that never reported
are `not reported` failures; the process group is killed.

**Cancel** (Ctrl-C, `cancel JOB`) signals every target's shard, waits for the process groups to end, and returns 130; units that
finished stay stored.

**Determinism.** For one tree, one config and one durations file, the **division** (`divide`, 5.2) is identical on every machine whatever
its store, its history or its target list, and prints its digest. The **plan** for a run on this machine is identical for one tree, one
config, one durations table and one target list, and prints a digest of those inputs; the store, a target's free slots at the moment and
the time of day are not inputs. The report is ordered by unit, not by arrival; live output is ordered per device by byte offset; the canary
draw takes its seed from the operating system and prints it so a run can be repeated; no unit depends on another having run. Wall time and
which device ran a unit may vary only through what is measured (durations, observed capacity).

## 12. Adoption, in minutes, for any repository

```
pip install poolhouse                       # nothing else: the command, the Python API and the presets come with it
poolhouse-test init                         # finds the tool's marker file, writes a testfarm.toml (the tool's four lines and, after its measured first runs, `[schedule] silence_margin`, `total_margin` and `[reuse] canary`), prints it, and offers once to ask whether the project is yours (a question put through the host's gate)
poolhouse-test run                          # parallel on this machine under the broker (confined, in a copy, while the project is not named as yours), with reuse; the second run reuses what passed
poolhouse-test run --affected               # once DEPS are given (init offers the globs it can see)
poolhouse-test devices                      # the pool's other devices, whether each takes tests, platform, free slots
poolhouse-test target auto                  # once, to use the pool's devices for your named projects, after the gate agrees the device list (or --on auto for one run)
poolhouse-test run                          # the same command, sharded over them
```

Steps that need a person: naming a project as one's own, one yes per project asked once through the host's gate ("The tests and build commands in
'<path>' will run as you, with access to your files except the runner's own keys and settings, and may be sent to: <devices>. Without a yes they
run confined, in a copy."), which `init` offers as its last line and which Poolhouse's own checkout gets from its device setup; and, for a pool
that is not in dev mode, the receiving device's person adding the sender once (`poolhouse-test allow`), asked once through the same gate with the
sentence "This lets '<device>' run its own code on this computer, as you." and the seconds, disk and slot-seconds the sender may use. A pool in
dev mode asks the receiving person the same budget once, at device setup. With no gate available each of these refuses, changes nothing and
prints the block a person adds to the settings file at their own editor; the run goes on, confined and local. A repository the user was handed
and has not named still runs, confined, in a copy: it reads its own files and the toolchain, writes the scratch tree, and uses no network, and
nothing of it leaves the machine.
Security adds no other visible step to the sender's journey. A repository in any language adopts by writing its two commands: for tests that are
executable files, `preset = "files"`; for anything else, `[list]` and `[run]` in the sentence of section 1. DEPS can be added any time
and only make the runner faster.

**The dogfooding rule.** Poolhouse's tests run through `poolhouse-test`, configured by `testfarm.toml`, by the same code path as every
repository, and the repository's scripts and workflows use only the public surface (check E). A capability the repository needs that
the public surface lacks is added to the surface, never reached for privately. The `scripts/test` spelling is a convenience that
translates to the public command.

## 13. Checks that keep the direction and the contract

Each is a test in the migration steps named, each with generated neutral projects and no other repository.

- **A. Layering.** `tests/test_testfarm_layers.py` reads every import under `src/poolhouse/testfarm/`: the
  core imports no preset, transport, resource provider or plugin; a preset imports only L2 and L1; L1 imports only the standard library; the
  L0 list is exact (`files`, `lock`, `platform`, `redact`, `sandbox`, `errors`, `log`) and the pool transport and the lease provider import
  only the Poolhouse modules the test names; nothing under `testfarm` imports `scripts`, `tests`, `board`, `workspace` or the pool's home and
  state-root modules; the runner's state path is not under the pool's home; every executor call passes explicit `Limits`, no code under
  `testfarm` starts a process except through the one spawn function (3.1), and `import poolhouse.testfarm` loads no `fleet`, `board`,
  `numpy`
  or `pytest`.
- **B. The runner reads only its project and its state.** A test generates a neutral project in a temporary directory, makes every
  other directory under the temporary root absent or unreadable, and runs `poolhouse-test check`, `list`, `run` and `affected` under the
  deny-default read profile of `src/poolhouse/sandbox/` that allows exactly the project root, the runner's state directory, the
  interpreter and system libraries. The profile covers every process the runner starts, children included (`git`, LIST, RUN), so a read
  of the home directory, a parent directory, a sibling repository or a git configuration file fails the test; the runner runs `git` only as
  4.9 says, with
  the fixed overrides and environment. A `sys.addaudithook` recording of the runner process's own `open`, `stat` and `listdir`
  stays as a second witness for that process only.
- **C. Neutral projects only.** Every test of the runner builds its project with the generator `tests/testfarm_project.py` (a
  temporary directory, invented names such as `widgets`); a static test fails when a runner test names the repository root, `scripts/`
  of Poolhouse, or a path outside its temporary project, except `tests/test_testfarm_dogfood.py`, which is the only module that
  loads Poolhouse's own file and says so.
- **D. No hidden defaults.** A test copies Poolhouse's tree to a temporary directory without `testfarm.toml`, runs `poolhouse-test
  check` and requires status 4 with the `init` hint and no units; another test greps `src/poolhouse/testfarm/` for the repository's own
  script names, test directory names, `DEV_TEST_`, `budgets`, `heavy-modules`, `seatbelt-modules`, the agent markers and the sentences of
  `docs/agent-policy.md`, and requires none. Presets are tested to contain no path or name of any repository. A generated Rust workspace
  under `crates/` with a `.cargo/config.toml` and a `.python-version` reaches a device in the transported set, so no directory name or
  extension is special to the runner.
- **E. Dogfooding.** `tests/test_testfarm_dogfood.py` loads Poolhouse's `testfarm.toml` through `ph.test.config`, runs `check`, and
  fails when a script, a workflow or a non-runner test imports a `poolhouse.testfarm` name that `ph.test` does not export.
- **F. Development rules stay in development.** The same generated project run with and without the Poolhouse development markers in
  the environment gives the identical plan and result; the wheel's file list for `testfarm` contains nothing from `scripts/`.
- **G. Contract fuzz.** LIST and RUN fakes written as real programs in the generated project (a shell script, a Python script, a
  program that prints escapes, huge lines, duplicate ids, ids starting with `-`, `@` or `+`, an id with U+2028, U+2029 or U+0085, a tag
  character, a 4 KiB id, a LIST that exits nonzero after printing nothing, a banner that `list.starts` filters away) are run and each
  hostile output is refused or sanitised as section 10 says, against each preset helper.
- **H. The executor's boundary.** `tests/test_testfarm_confinement.py`: a step links every path the executor made (`exec/`, the `out/`
  names, the result file, the log) to canary files outside the scratch tree, and the canaries are neither read nor written and no byte of
  them reaches the sender; a step rewrites `testfarm.toml` and the units' argument vectors are those of the first parse; `{step:../x}` is
  refused at validation; a unit cannot write `{step:NAME}` or `{shared:NAME}`; a unit connects to the pool daemon's port, a model server's
  port and the LAN gateway on both platforms and none answers; on Linux a `setsid` child does not outlive the shard. A confined unit cannot
  write the checkout, `testfarm.toml` or the repository's metadata, because its profile holds a copy; a confined child's open descriptors
  are exactly `0`, `1`, `2`, the permit and the result descriptor; a unit that writes until stopped and one that allocates until stopped are
  ended on both backends; the Parser worker ends in status 4 on a UTF-16 DOCTYPE document, a 5000-digit integer and a nested document.
- **I. Divide and cover.** `tests/test_testfarm_plan.py`: two machines with different stores and histories produce byte-identical
  divisions, and the toy case (units of 4, 3, 2 and 1 seconds in two shards) covers every unit once; mutants that drop the id sort, read the
  history or read the store fail it; `plan --verify-cover` fails when a digest differs or a unit is in no list or in two.
- **J. Trust domains.** `tests/test_testfarm_store.py`: a run of a project that is not named cannot read the `owner/` directory of the state location, the settings or
  another project's store, cannot write or delete an entry, and leaves entries only in the `confined` domain; an entry with a bad
  signature, a wrong domain or another key is not served and is not evidence; `check` run on a fresh hostile clone executes its LIST and
  `deps.command` confined (a canary file in the home directory is not read). The identity fixtures of 4.9 are each unnamed: a `.git` file
  whose `gitdir:` names a named repository, a `.git` directory with a `commondir` file naming one, a directory below a parent repository and
  a `GIT_DIR` in the environment. A repository whose `.git/config` sets `core.fsmonitor` and `core.hooksPath` to programs that write a
  canary leaves it unwritten through `list`, `check`, `plan` and `affected`. A named unit that reads the key, appends to the store and edits
  the settings fails on each, and a worktree's unit cannot write an entry for another project.
- **K. Status and outcomes.** `tests/test_testfarm_run.py`: the status is a function of the causes present (a failed unit and a lost device
  give 1, a lost device alone gives 70); a LIST that fails, prints nothing or is filtered away is 4; a run of only skips is `nothing ran`;
  an `exit` result that skipped is not stored.
- **L. One spawn function.** `tests/test_testfarm_spawn.py`: every process the runner starts (LIST, `deps.command`, hooks, steps, units, control
  units, the Parser worker, `[requires]` probes, `prune`, `git`, the launcher) goes through the one spawn function of 3.1, which refuses a call
  without a hang guard, a profile, a descriptor list and explicit limits; a static test fails when code under `testfarm` starts a process
  another way; a unit that prints a byte at a time and one that never prints are both ended; `git` runs with the overrides and environment of
  4.9, and a repository whose configuration names a program runs none.

The second-repo test (section 14, step 10): a throwaway repository generated in a temporary directory, depending on the installed
wheel of Poolhouse in a fresh virtual environment, with a small pytest suite and a non-Python test (a shell script), named as the user's own
in the test's settings file, runs `poolhouse-test run --on pool` across two devices, using only the `poolhouse-test` command and `ph.test`.
It asserts: both devices ran a share; one report and one status; a second run reuses; a failing unit gives status 1 with its message;
stopping one daemon mid-run gives 70 (1 when a unit also failed) with the loud sentence and no re-run; a tampered tree is refused; and,
under the read profile of check B, nothing outside the project and the state directories was read. It runs in two forms. The wire form,
on two local devices (two pool daemons on loopback ports with separate state directories and a dev-mode pool between them, the harness of
`tests/test_dev_shards.py` and `tests/dev_pool_kit.py`, no beacon, no multicast), proves the protocol. The proof of steps 8 and 10 is
`scripts/pool-check` (`docs/pool-check.md`) on this Mac and the WSL device, real devices of different operating systems joined through the
real join path, because `docs/agent-policy.md` section 3 says two nodes on one machine over loopback are not tested: platform classes,
`--cover`, certificate pinning across hosts, a lost device from a real partition, confinement on both backends and the two network
semantics appear only there. The report names the platform the check did not cover. It is the proof that a repository that is not
Poolhouse can use the feature; it is a `slow` test that needs the wheel (`python packaging/build.py`).

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

**Findings per step.** The written break-it passes (`docs/design-test-runner-breakit.md`, findings F1 to F30, and
`docs/design-test-runner-breakit-2.md`, findings N1 to N18, which found F5, F7, F10 to F15, F18, F21 and F25 absorbed in part) are absorbed
in the text of
sections 1 to 13. Each step below lists the findings it must absorb and, for each, the test that proves it. The test is written before
the code it proves and is run first against the parent commit or against the naive implementation the row names (a mutant that
reproduces the finding), and the step's commit carries the `Fail-first:` line with the error it showed. A finding is absorbed when its
test is green on the step's commit and red on that mutant. Findings that change prose only (F30) are listed where they land: items 1, 2,
9 and 13 are the text of sections 11, 6.1 and 10.3, 11 and 16, and item 14 is the green state of `tests/test_glossary.py`.

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

   | Finding | Proof |
   |---|---|
   | F12 a clock that counts the wait is the error | `test_testslots_timeout.py::test_a_permit_wait_is_not_counted_against_the_test_timeout` (red on the parent: the test times out while it waits) |
   | F29 one name for two limits | `::test_the_report_names_the_limit_that_fired` (red on the parent: the permit wait and the test share one `timeout` message) |

1. **Model and config.** New `src/poolhouse/testfarm/model/` (schema, parser, strict validator, ids, exit codes, dataclasses),
   `poolhouse-test init|check|list`, and `ph.test.config/units`. Files: `pyproject.toml` (script), `docs/commands.md`, `docs/api.md`,
   `tests/api-signatures.json`, `tests/test_layers.py` (L1 is standard library only). Tests: `tests/test_testfarm_config.py` (every key,
   every hostile file of A14), `tests/test_testfarm_layers.py` (check A, partial), `tests/testfarm_project.py` (the generator, check C).
   Proves: any project describes itself through LIST, RUN and DEPS, and a bad file is refused with a sentence.

   | Finding | Proof |
   |---|---|
   | F3 LIST keys and statuses | `test_testfarm_config.py::test_a_run_with_no_hang_guard_is_refused`, `::test_list_has_allow_empty_and_a_timeout_key`; `test_testfarm_list.py::test_a_list_that_fails_or_prints_nothing_is_status_4` (red on a build that reads LIST's output only) |
   | F4 empty and self-less DEPS | `::test_check_fails_a_deps_glob_that_matches_no_file_and_a_set_without_the_units_own_file` |
   | F8 grammar of every name | `::test_names_and_environment_variables_outside_their_grammar_are_refused` (`{step:../x}`, a name with `=`, a value with NUL) |
   | F14 no key chooses a target | `::test_a_remote_or_target_key_in_the_project_file_is_an_unknown_key` |
   | F15 parser limits, no regular expressions | `::test_toml_nesting_that_raises_recursionerror_is_a_validation_error`, `::test_no_key_takes_a_regular_expression` |
   | F16 id grammar | `test_testfarm_ids.py::test_ids_with_line_separators_tag_characters_prefixes_and_non_nfc_text_are_refused` (`a\u2028--evil`, `a\u0085--evil`, a tag-character id) |
   | F18 numbers | `::test_nan_infinity_negative_and_oversized_seconds_are_refused` |
   | F23 state location | `test_testfarm_layers.py::test_the_state_path_is_not_under_the_pool_home` |
   | F30 items 3, 5, 6 (schema keys the text uses, one median, the plugin import rule) | `test_testfarm_config.py::test_the_schema_has_every_key_of_the_design_table` (red when a key of 4.1 is missing from the schema); `test_testfarm_layers.py::test_a_plugin_imports_only_the_modules_the_test_names` |
   | N6 numbers of the project's file (F18 in part) | `test_testfarm_config.py::test_non_finite_zero_negative_and_out_of_range_numbers_are_refused` (`silence_margin = inf`, `nan`, `1e999`, `0`, a margin below 1, `canary = 0` and `canary = 2`; red on a check that tests only that the value is a float) |
   | N6 half a guard (F12 in part) | `::test_a_run_with_one_margin_is_refused` (red on a refusal that needs only `silence_margin`) |
   | N17 `slice_base` has no default | `::test_slice_base_is_required_with_slice` |
   | N8 a TOML integer past the digit limit | `::test_a_toml_integer_of_5000_digits_is_a_validation_error` (red on a handler that catches only `TOMLDecodeError`, `RecursionError` and `MemoryError`) |

2. **Admission.** `git mv` `scripts/testslots{,_policy,_rpc,_runner}.py` and `testqueue.py` to `testfarm/admission/` (and
   `testslots_pytest.py` to the pytest preset's helpers; `AdmissionUnreachable`, imported today from the confinement file `test_kernel_endpoint.py`, moves with the admission interface), the `Admission` interface (`acquire` all or nothing, `Grant.sub`, `Grant.delegate`), the
   `DEV_TEST_*` names renamed `TESTFARM_*` (`tests/conftest.py` and `scripts/test` updated in the same commit), the permit descriptor, and
   the change to `poolhouse.serve` that lets `serve up` use a lease delegated to an ancestor process group of its caller, each process
   identified by a handle (5.5). Tests move:
   `tests/test_testslots*.py`; new `tests/test_testfarm_admission.py` (a neutral command under the broker) and
   `tests/test_serve_delegation.py`. Proves: a general command is admitted like a pytest run, the nested-run refusal holds, a model-needing
   unit does not deadlock behind the lease its runner holds, and the fixed cost of `scripts/test` is unchanged
   (`tests/test_runner_overhead.py`).

   | Finding | Proof |
   |---|---|
   | F13 two permit levels | `test_testfarm_admission.py::test_invocations_waiting_for_per_test_permits_do_not_deadlock` (red on a broker that makes a second request per test) |
   | F13 two arbiters, steps against units, single flight | `::test_a_request_for_slots_and_a_resource_takes_both_or_neither`, `::test_a_single_flight_waiter_holds_no_slot` |
   | F13 the runner's lease against the unit's own | `test_serve_delegation.py::test_a_unit_under_a_runner_held_gpu_lease_serves_without_queueing`, `::test_a_process_outside_the_group_gets_no_delegated_lease`, `::test_no_lease_id_is_in_the_environment` (red on the parent: `serve up` queues behind the runner) |
   | F24 permits without an endpoint | `::test_a_child_receives_the_permit_descriptor_and_a_nested_run_refuses` |
   | F28 several users | `::test_the_slots_directory_is_per_user_and_mode_0700` |
   | N12 delegation keyed on a handle (F13 in part) | `test_serve_delegation.py::test_a_dead_connecting_process_gets_no_delegation`, `::test_a_recycled_process_id_with_another_start_time_matches_nothing`, `::test_a_delegation_ends_on_the_leaders_exit_notification` |
   | N12 a model server that outlives its group | `::test_a_server_that_outlives_the_group_keeps_the_lease_held` |
   | N12 the nested-run refusal | `test_testfarm_admission.py::test_a_nested_run_is_refused_by_the_brokers_ancestor_test_with_the_variable_unset` (red on a refusal keyed on `TESTFARM_PERMIT_FD`) |

3. **Local run core, the executor and its zones, confined local runs, and Poolhouse's file.** The largest step; it lands as one branch of
   ordered commits (a) to (d), each green; N1 to N6 are in the text before it starts. (a) Divide, assign, schedule, merge, report with the
   time accounting of 5.10 and the outcomes and
   status of section 11, `poolhouse-test run|plan` for any LIST and RUN, `ph.test.run/plan/divide`; the `pytest` and `files` presets and the
   pytest result plugin. (b) The `local` transport and the executor with its boundary (6.6: the executor-owned directories, descriptors
   held open, the Parser worker, one parse of the config), the one spawn function, the git calls of 4.9, the scratch copy and the sandbox
   for a project that is not named, the Owner-zone deny for a named one and the two `sandbox` modes of 3.1. (c) The user's settings, the
   identity record, `owner_paths()`, the `PersonGate` seam with Poolhouse's gate plugin and the guards that read the paths, `own`,
   `target`, `--on`, the default target `local`, and the state location outside the pool's home. (d) The first `testfarm.toml` of
   Poolhouse (`preset = "pytest"`, the sets of the tiers). Files: `testfarm/{core,transports/local,presets}/`, `testfarm.toml`,
   `tests/test_testfarm_run.py`, `test_testfarm_plan.py`, `test_testfarm_presets.py`, `test_testfarm_timeouts.py` (a neutral unit
   queued behind held permits with a small timeout passes and reports `queued`; a unit that runs past its timeout fails with
   `timed out`), `test_testfarm_confinement.py`, `test_testfarm_settings.py`. Proves: a shell-script project and a pytest project run in
   parallel locally and give one report and one status; the plan is deterministic; the exit-code table; contract fuzz (check G); a project
   that is not named runs confined; in this repository `scripts/test` on the moved pieces and `poolhouse-test run` give the same result.

   | Finding | Proof |
   |---|---|
   | F1 the executor follows links a child makes | `test_testfarm_confinement.py::test_a_step_that_links_every_executor_path_to_a_canary_file_reaches_nothing` (red on the carried-over `shard_run`, which reopens the log by name) |
   | F2 a pure division | `test_testfarm_plan.py::test_two_machines_with_different_stores_divide_alike` and `::test_the_toy_case_covers_every_unit_once` (red on a planner that reads the store or the history, or drops the id sort) |
   | F3 failed, empty and filtered LIST; skips | `test_testfarm_run.py::test_a_filtered_list_prints_ignored_n_lines`, `::test_a_run_of_only_skips_is_nothing_ran`, `::test_an_empty_affected_selection_is_zero_with_its_line` |
   | F4 presets know no dependencies | `test_testfarm_presets.py::test_files_and_npm_give_a_unit_the_whole_tree` (red on a preset whose DEPS are the unit itself) |
   | F5 handed code runs confined (decision 1) | `test_testfarm_confinement.py::test_a_project_that_is_not_named_runs_list_steps_hooks_and_units_confined`, `::test_check_on_a_hostile_clone_reads_no_home_file`; `test_testfarm_settings.py::test_naming_a_project_asks_only_the_hosts_gate_and_an_agent_cannot_answer_it`; F5 was absorbed in part, and the N rows below finish it |
   | F6 the status ranks a failure first | `test_testfarm_run.py::test_a_failure_outranks_every_other_cause` (red on "numerically highest") |
   | F8 one parse of the config | `test_testfarm_confinement.py::test_a_step_that_rewrites_the_config_changes_no_argument_vector` |
   | F10 default network (local half) | `::test_a_confined_local_unit_has_no_network` |
   | F14 the target is the user's (decision 2) | `test_testfarm_settings.py::test_the_default_target_is_local`, `::test_the_target_is_never_read_from_the_project_file`, `::test_a_project_that_is_not_named_is_never_sent`, `::test_a_widening_target_asks_the_gate` (F14 was absorbed in part: N1, N3, N13) |
   | F15 parse in a confined process | `test_testfarm_confinement.py::test_xml_in_every_encoding_with_a_doctype_is_refused` (UTF-8, UTF-16LE without a byte-order mark, UTF-16 with one), `::test_a_junit_file_nested_to_recursionerror_is_an_error_not_a_crash`, `::test_any_end_of_the_parser_worker_is_status_4` (N8) |
   | F17 text is data in frames | `test_testfarm_attacks.py::test_a8_invisible_and_framed_text_cannot_pose_as_the_runner` (local output) |
   | F23 state outside the pool's home | `test_testfarm_confinement.py::test_every_profile_names_the_state_location_by_exact_path` |
   | F24 environment | `test_testfarm_settings.py::test_a_named_child_inherits_the_environment_and_a_confined_child_gets_the_allowlist` |
   | F29 two timeouts | `test_testfarm_timeouts.py::test_a_file_of_fifty_five_second_tests_is_not_killed_by_the_per_test_value`, `::test_the_report_names_the_limit_that_fired`, `::test_rows_of_a_killed_invocation_are_reported` |
   | F30 item 4 (wall seconds are the unit), item 8 (a native Windows machine is a sender only) | `test_testfarm_plan.py::test_the_planner_uses_wall_seconds_times_slots` |
   | N1 the project is named by what the person pointed at | `test_testfarm_settings.py::test_a_directory_that_points_at_a_named_repository_is_not_named` (four fixtures: a `.git` file reading `gitdir: <named>/.git`, a `.git` directory with a `commondir` file naming it, a directory below a parent repository, `GIT_DIR` in the environment; red on an implementation that asks `git rev-parse --git-common-dir`), `::test_a_linked_worktree_is_named_only_with_its_back_pointer`, `::test_the_root_walk_stops_at_the_first_testfarm_toml_or_git_entry` |
   | N2 git is code (F25 in part) | `test_testfarm_confinement.py::test_a_repository_whose_git_configuration_runs_a_program_runs_nothing` (`.git/config` sets `core.fsmonitor` and `core.hooksPath` to programs that write a canary; `list`, `check`, `plan` and `affected` leave it unwritten; red on a runner that runs `git` in the checkout unconfined), `::test_git_runs_only_from_the_closed_list_with_the_fixed_overrides_and_environment` |
   | N3 the person's yes | `test_testfarm_settings.py::test_own_allow_and_a_widening_target_ask_only_the_gate`, `::test_no_gate_refuses_and_prints_the_block_for_a_persons_editor`, `::test_a_terminal_answer_is_never_accepted` (a child under `script(1)` types `yes`), `::test_the_settings_are_written_only_after_the_gate_says_yes`, `::test_the_sentence_lists_the_devices_and_a_new_device_set_asks_again`, `::test_the_path_in_the_sentence_is_plain`, `::test_owner_paths_lists_every_file_the_cli_writes`, `::test_check_reports_a_settings_file_the_user_does_not_own_or_others_can_write`; `tests/test_person_guards.py::test_the_guards_refuse_the_test_runners_owner_paths` and `tests/test_person_release.py::test_the_test_kinds_are_in_the_closed_registry` |
   | N4 the Owner zone is denied to every child | `test_testfarm_confinement.py::test_a_named_child_is_refused_the_owner_zone_by_exact_path` (read, append, hard link and clone of the key; red on an open profile with no deny) |
   | N5 a confined run cannot write what the next action runs | `test_testfarm_confinement.py::test_a_confined_unit_cannot_write_the_checkout_testfarm_toml_or_repository_metadata` (red on a profile with the project directory writable) |
   | N6 the guard cannot be bypassed | `test_testfarm_timeouts.py::test_a_unit_that_prints_a_byte_at_a_time_is_ended_by_the_total_guard`, `::test_the_first_unit_needs_a_terminal_or_a_timeout_flag`, `::test_every_process_the_runner_starts_goes_through_the_one_spawn_function` (red when a `subprocess` call bypasses it) |
   | N9 the Parser worker | `test_testfarm_confinement.py::test_the_parser_worker_starts_once_per_shard_and_its_output_is_tag_length_value`, `test_runner_overhead.py::test_the_parser_worker_start_is_not_paid_per_result_file`, `test_testfarm_layers.py::test_the_runner_imports_no_deserialiser_for_the_worker_stream` |
   | N12 descriptors, N14 the result channel | `test_testfarm_confinement.py::test_a_confined_child_holds_only_its_own_descriptors`, `::test_a_result_file_is_created_through_a_held_directory_descriptor` (a directory component swapped for a link is not followed) |
   | N17 explicit limits | `test_testfarm_layers.py::test_every_executor_call_passes_explicit_limits` (red on a call that inherits the `Limits` defaults) |
   | N15 the local loopback mode (F10 in part) | `tests/test_sandbox_net.py::test_loopback_except_denies_the_named_ports_when_each_unit_starts` |

4. **Jobs, history, classes.** Move `testjobs`, `testclass`, `testprelude`, `testphases`, `testhistory`, `testdurations`, `testorder`,
   `testtime` into the core. Tests move: `tests/test_testjobs*.py`, `test_testclass.py`, `test_testphases.py`, `test_testorder.py`,
   `test_test_time.py`. Proves: a second run of the same argument vector and tree follows the first.

   | Finding | Proof |
   |---|---|
   | F18 numbers steer the history | `test_testfarm_history.py::test_a_device_unit_is_timed_by_the_senders_clock`, `::test_a_reported_seconds_beyond_the_measured_interval_is_not_recorded` |
   | F20 the run id | `test_testjobs.py::test_the_run_id_is_random_and_is_not_the_job_key` |
   | F27 history retention | `test_testfarm_history.py::test_rows_for_ids_absent_from_recent_lists_are_pruned` |
   | F30 item 12 (who cancels, a dead job) | `test_testjobs.py::test_a_job_whose_runner_died_is_lost_and_does_not_hold_status_6` |

5. **Reuse and selection.** Move `testreuse_*` and `testselectors` into the core with the generalised key (declared DEPS, step keys,
   tool probes, `reads` and `unstorable` from results); move the Python import closure and the recorder plugin into the pytest preset's
   helpers; replace `affected.py` and `affected_rules.py` by selection over dependency sets, the Poolhouse-specific string rules by
   `deps.globs` and `deps.everything` in Poolhouse's file, and `scripts/test quick` by `run --affected` (testmon, `record` and
   `.testmondata` are deleted in this step because `quick` no longer uses them). Store entries written under the old key are not
   read (nothing is preserved, no migration). Tests move: the 21 `tests/test_testreuse*.py` files, `tests/test_affected_*.py`; new
   `test_testfarm_reuse.py`, `test_testfarm_store.py` and `test_testfarm_select.py` on neutral projects (a non-Python project reuses across
   an edit of an unrelated file; the canary catches a poisoned entry). Proves: reuse and affected selection with no Python knowledge in the core.

   | Finding | Proof |
   |---|---|
   | F4 `exit` hides skips; a tree that changes; empty DEPS | `test_testfarm_reuse.py::test_an_exit_result_that_skipped_is_not_stored`, `::test_a_tree_that_changed_during_an_invocation_stores_nothing`; `test_testfarm_select.py::test_an_edit_of_the_code_under_a_files_unit_selects_it` |
   | F5 signed entries, domains, a directory per project | `test_testfarm_store.py::test_a_confined_run_cannot_write_or_disable_an_entry_of_another_project`, `::test_an_entry_with_a_wrong_signature_or_domain_is_not_served`, `::test_a_readers_root_is_not_overridable_by_the_environment` |
   | F11 the `remote` kind (store half) | `::test_a_remote_entry_is_stored_only_with_a_second_source_and_serves_only_its_device` |
   | F19 the key holds every input | `test_testfarm_reuse.py::test_each_input_that_changes_a_result_changes_the_key` (`junit_id`, `batch`, `tree.exclude`, typed arguments, mode bits, launcher, resolved preset) |
   | F20 canary | `::test_two_runs_of_one_tree_sample_different_hits`, `::test_a_flaky_canary_is_not_poison`, `::test_a_canary_run_carries_no_marker`, `::test_reuse_is_off_without_a_canary_rate` |
   | F27 store retention | `test_testfarm_store.py::test_unreachable_entries_are_pruned_on_the_idle_rule`; `test_testfarm_config.py::test_check_prints_the_share_of_unstable_ids` |
   | Section 5 table, last row (git assumed) | `test_testfarm_select.py::test_an_unknown_ref_selects_every_unit_and_says_why` |
   | N2 the working-tree side of `affected` | `test_testfarm_select.py::test_a_clean_filter_can_only_make_a_file_look_changed` |
   | N4 a key per project, the reader's rule (F21 in part) | `test_testfarm_store.py::test_the_key_of_one_project_signs_nothing_of_another`, `::test_a_task_is_credited_only_from_a_run_of_the_exact_tree_by_another_session` (in `tests/test_workspace_testruns.py`) |
   | N14 the stamp | `test_testfarm_reuse.py::test_a_whole_tree_unit_is_stamped_once_per_run_and_not_once_per_invocation` |
   | N17 the canary range (F20 in part) | `::test_a_run_that_serves_a_hit_re_executes_at_least_one` |
   | N18 statuses and outcomes | `test_testfarm_run.py::test_a_canary_mismatch_is_status_1_a_flaky_unit_is_a_pass_and_a_refused_shard_is_70` |

6. **Steps and the cargo preset.** Core `[[step]]` (key, cache, shared, admission, outside the timer, prune); the `cargo` preset helpers
   by moving `testunits.py` and `testartifacts.py`; `tests/native_cargo.py` calls the preset; Poolhouse's node and app builds stay in
   `scripts/testnode.py` and `testapp.py`, declared as Poolhouse's steps. Tests: `tests/test_testunits.py`, `test_testnode*.py`,
   `test_testfarm_steps.py` (a neutral step cached by an input key, skipped when unchanged, outside a unit's timeout). Proves: the build
   handling that has landed is generic and its timeout and lock behaviour are unchanged.

   | Finding | Proof |
   |---|---|
   | F9 products a unit can rewrite; directories shared across projects | `test_testfarm_steps.py::test_a_unit_cannot_change_a_cached_product_and_a_changed_product_is_rebuilt`, `::test_two_projects_of_one_sender_do_not_share_a_cache_directory` |
   | F13 steps against units | `::test_every_step_finishes_before_the_first_unit_is_admitted` |
   | N14 the manifest walks by descriptor | `test_testfarm_steps.py::test_a_manifest_records_a_link_a_fifo_and_a_device_without_opening_them`, `::test_a_directory_component_swapped_for_a_link_is_not_followed`, `::test_shared_is_covered_by_the_digest_of_what_a_unit_read` |

7. **Command units and the gate.** `[[unit]]` tables; move `gaterun.py`; `gatescope.py` becomes dependency sets. Tests:
   `tests/test_gaterun*.py`, `tests/test_gates_incremental.py`. Proves: the gate's steps are reused by input hash.

   | Finding | Proof |
   |---|---|
   | F4 an empty `inputs` match | `test_gaterun.py::test_an_inputs_glob_that_matches_nothing_is_a_check_error` |

8. **Pool transport, the receiver, one wire.** The second written break-it pass (`docs/design-test-runner-breakit-2.md`) is absorbed in this
   text; a non-author re-reads the edited 4.8, 4.9, 5.10, 6.3, 6.4, 6.6, 10.2, 10.3 and 10.6, which
   blocks the rest of this step. The step lands as one branch of ordered commits (a) to (e), because two ways of sending a shard cannot rest
   side by side and nothing is reachable from `ph.test` or `scripts/test --on` until (e). (a) The receiver on the executor of step 3:
   the closed start fields, per-unit streaming, the deny-default profile with the network denied on both platforms, the allowlist
   environment, the receiver's time, disk and memory budgets (10.6), the listed-ports rule for macOS loopback, the PID namespace on Linux
   and the sweep on macOS. (b) The wire: the generalised
   `/shards/v1` routes and caps (answered only to an admitted sender), the tree hash over the transported set, the project id, the shard nonce, the
   install-instance id, the sender-keyed step directories, `admit()`, the `allow` act. (c) Trust: control units, sticky suspicion,
   second-source storage of the
   `remote` kind, `suspect`, the sender's bound on a shard, the loss rule, reassignment of refused units. (d) Sending: the user's target,
   the named-project gate, the first-send file listing, `--cover`. (e) Everything the generalised path replaces is deleted in this same
   commit: `fleet/shard_{routes,host,store,spec,tree,run,exec,result,split}.py`, the node carrier's client and the carrier of a pool in dev
   mode (`testfarm/client.py`, `dev.py`, `farm.py`, `ledger.py`; the report moves to the core), `scripts/test_on.py`,
   `poolhouse-test-devices` (the `pyproject.toml` script and the `docs/commands.md` row; `poolhouse-test devices` replaces it),
   `poolhouse.api.remote_tests` (`ph.test` is the facade), and the `--split` flag (the planner always splits); `scripts/test --on` now
   calls `ph.test.run`. The old tests are deleted with their code or moved: `tests/test_dev_shards.py`, `test_dev_shards_hostile.py`,
   `test_shard_limits.py`, `test_redteam_test_shards.py`, `test_testfarm.py`, `test_testfarm_nodes.py`, `testfarm_kit.py`,
   `testfarm_tree.py`, `dev_pool_kit.py`. New: `tests/test_testfarm_pool.py` (two loopback daemons, a neutral project, the wire form),
   `test_testfarm_confinement.py` (a sent tree whose scripts try to read `~/.ssh`, write outside the scratch tree, open a non-loopback
   socket, set `DEV_TEST_WRITE_DENY=0`, link executor paths to canaries, all refused), `test_testfarm_attacks.py` (every row of 10.8 that a
   test can reach), `test_testfarm_policy.py`. The Rust node carrier stays in the tree until open question 1 is answered; it is unreachable
   from `ph.test` and `scripts/test` and is deleted in Phase C. `docs/limits.md` is updated for the deleted host's rows. Proves: one wire,
   any command, hostile inputs refused, a vanished device gives 70 with no re-run, and, on this Mac and the WSL device through
   `scripts/pool-check`, the platform classes, `--cover`, certificate pinning across hosts and both network semantics.

   | Finding | Proof |
   |---|---|
   | F1 the executor on a device | `test_testfarm_confinement.py::test_a_step_that_links_every_executor_path_to_a_canary_file_reaches_nothing` and `::test_a_result_path_replaced_by_a_link_reads_nothing`, run on a sent tree |
   | F6 completed rows survive; refused is not a failure; a unit that kills devices | `test_testfarm_pool.py::test_completed_rows_survive_a_vanished_device`, `::test_a_device_that_returns_not_started_for_failing_units_is_reassigned_and_suspect`, `::test_a_unit_running_when_two_devices_vanish_is_suspected` |
   | F7 a device failure is final | `::test_a_failure_on_a_device_survives_a_pass_on_the_sender` (red on a sender that re-runs and reports the pass), `::test_a_failure_that_does_not_reproduce_twice_makes_the_device_suspect` (N18; a hostile member's false failures cost it its place) |
   | F8 the start request | `test_testfarm_confinement.py::test_the_start_request_has_no_extra_steps_or_deadline_and_a_rewritten_config_changes_nothing` |
   | F9 `{shared}` on a device | `test_testfarm_steps.py::test_a_sender_cannot_leave_a_product_another_senders_step_finds` |
   | F10 network and loopback | `test_testfarm_confinement.py::test_a_unit_cannot_connect_to_the_daemon_a_model_server_or_the_lan_gateway` on both platforms (the Linux half runs on the WSL device), `::test_a_sent_unit_with_loopback_on_macos_is_refused_unless_the_receiver_lists_ports`, `::test_a_listener_started_after_the_profile_was_built_is_not_reachable` (N15) |
   | F11 device claims steer nothing; passes are advice | `test_testfarm_pool.py::test_a_device_pass_is_shown_and_not_stored_without_a_second_source`, `::test_a_second_instance_of_a_fingerprint_gets_no_shard`, `::test_platform_and_capacity_come_from_the_senders_record`, `::test_a_control_unit_refuses_a_shard_that_ran_nothing`, `::test_a_confinement_control_unit_catches_a_sandbox_that_fails_open`, `::test_a_mismatch_is_sticky_until_allow`, `::test_a_control_unit_never_ends_a_mismatch` (N11) |
   | F12 the sender's bound; queue time; a stalled poller | `::test_a_device_that_only_heartbeats_is_ended_by_the_sender`, `::test_a_shard_waiting_in_the_devices_queue_is_not_a_deadline`, `::test_silence_is_not_counted_across_a_sender_stall`, `::test_a_sender_that_never_cancels_does_not_extend_the_receivers_ceiling` (N7) |
   | F14 sending (decision 2) | `::test_untracked_files_are_not_sent_unless_named`, `::test_the_first_send_prints_the_file_count_and_top_level_names`, `::test_nothing_is_sent_until_the_project_is_named_and_a_target_chosen`, `::test_a_glob_for_the_whole_tree_pulls_in_no_untracked_file`, `::test_a_send_to_a_device_set_not_agreed_asks_again` (N13, N3) |
   | F15 requirements, regular expressions, descendants | `::test_requires_names_are_checked_by_the_devices_own_probes`, `::test_requires_never_imports_a_package` (`module:this.x` and `module:xml.dom.minidom`; N10), `test_testfarm_confinement.py::test_a_pattern_from_the_file_cannot_backtrack`, `::test_a_detached_child_does_not_survive_a_shard_on_linux` |
   | F17 device text | `test_testfarm_attacks.py::test_a8_invisible_and_framed_text_cannot_pose_as_the_runner` (device output) |
   | F22 the policy lists capability limits | `test_testfarm_policy.py::test_the_policy_lists_only_capability_limits` (red when a "presets only" knob exists) and `::test_a_sent_tree_cannot_ask_the_receiver_to_run_list` |
   | F23 profiles | `test_testfarm_confinement.py::test_every_profile_names_the_state_location_by_exact_path` on a device |
   | F25 tree set and hash | `test_testfarm_pool.py::test_the_tree_hash_has_no_colliding_concatenation`, `::test_two_users_with_different_git_settings_send_one_set`, `::test_no_directory_name_or_extension_is_special`, `::test_info_exclude_and_a_global_excludes_file_change_nothing` (N2: the exclusion rules are the tree's own `.gitignore` files) |
   | F26 the real-device proof | `scripts/pool-check` on this Mac and the WSL device, its output in the step's report with the platform not covered named; the loopback harness is the wire test |
   | F30 items 7, 8, 10, 11 (one `allow` act, Windows is a sender, caps answers only an admitted sender, the delta rules) | `test_testfarm_pool.py::test_caps_gives_a_member_it_does_not_admit_only_the_sentence` |
   | N7 the receiver's budget | `test_testfarm_policy.py::test_a_sender_with_no_budget_gets_no_first_shard`, `::test_a_unit_is_bounded_by_the_smaller_of_the_sent_bound_and_the_receivers_ceiling`; `test_testfarm_confinement.py::test_a_unit_that_writes_until_stopped_and_one_that_allocates_until_stopped_are_ended` (both backends), `::test_the_disk_parts_of_a_shard_add_up_to_its_share` |
   | N8 parser death | `test_testfarm_confinement.py::test_xml_in_every_encoding_with_a_doctype_is_refused`, `::test_a_toml_integer_of_5000_digits_ends_the_worker_in_status_4` |
   | N9 the unpack in the worker | `test_testfarm_pool.py::test_the_tar_is_unpacked_in_the_parser_worker_and_hashed_through_descriptors` |
   | N16 credentials in a preset's reach | `test_testfarm_presets.py::test_a_shipped_preset_names_subpaths_and_denies_known_credential_files`, `::test_a_proxy_value_is_forwarded_without_user_and_password` |

9. **Fail-first.** `failfirst run|check`, the evidence entries, the `exempt` prefix parsing, the pytest preset's `def` digests. Tests:
   `tests/test_testfarm_failfirst.py` (a generated git repository: a new test that fails on the base passes the check; one that passes on
   the base is reported; an exempt line satisfies; a refactor with none passes). Proves: the policy line is checked by code.

   | Finding | Proof |
   |---|---|
   | F21 forgeable evidence, no base binding, fixtures, inline tests | `test_testfarm_failfirst.py::test_forged_and_wrong_base_evidence_is_refused`, `::test_a_fixture_only_change_makes_units_unverified`, `::test_an_exempt_line_must_name_an_id_of_the_range` |
   | N4 a new test cannot forge evidence at import time (F21 in part) | `::test_a_new_test_that_reads_the_key_at_import_time_writes_no_evidence` |
   | N18 one base | `::test_the_range_start_is_the_only_base_and_a_non_linear_range_is_status_4` |

10. **The second-repo test** (section 13, last paragraph) and the read-confinement check B. Needs steps 1 to 9. Proves that a repository
    that is not Poolhouse shards and reuses with a few lines of config.

    | Finding | Proof |
    |---|---|
    | F26 the proof is real devices | `scripts/pool-check` with the second repository on this Mac and the WSL device; `tests/test_testfarm_second_repo.py` is the wire form on loopback and names the platform not covered |
    | N1 identity fixtures in the second repository | `tests/test_testfarm_second_repo.py::test_a_copy_whose_git_file_points_at_the_named_repository_is_not_named` (the throwaway repository is named; a copy of it with that `.git` file is not) |

**Phase B: Poolhouse's scripts as config**

11. **`scripts/test` thin.** Poolhouse's `testfarm.toml` is completed (section 8: labels, steps, hooks, `everything`, the seed file), the
    `scripts/test` front is reduced to the call above with the tier spellings kept, and `docs/test-execution.md`,
    `docs/test-reuse.md`, `docs/test-farm.md`, `docs/development.md` and `docs/commands.md` are rewritten to the new locations. Tests:
    `tests/test_testfarm_dogfood.py` (checks D and E); the existing `tests/test_test_*.py` that exercise `scripts/test` pass unchanged.
    Proves: Poolhouse runs its own tests through the public interface, and the report, jobs, tiers and refusal lines are as before.

    | Finding | Proof |
    |---|---|
    | F5 Poolhouse's checkout is named by its setup | `tests/test_device_setup.py::test_setup_names_the_poolhouse_checkout_and_no_project_file_can` |
    | Section 5 table (the library knows no repository: report lines, tree set, timings, lease vocabulary, `DEV_TEST_`) | `test_testfarm_dogfood.py` (check D) |

12. **CI and hooks.** `.github/workflows/ci.yml` shards call `poolhouse-test plan --shards 8 --index N` and `run --shard N/8`, and the
    job that waits for them runs `poolhouse-test plan --verify-cover` over the printed lists; `poolhouse-test record --results` rewrites
    the seed file; the pre-push hook and the `gates` job call `failfirst check`; then `scripts/ci_shards.py` and its tests are deleted
    (`tests/ci-durations.json` stays as the seed). Proves: CI uses the one planner and fail-first is mechanical.

    | Finding | Proof |
    |---|---|
    | F2 the join step | `tests/test_ci_workflow.py::test_the_job_that_waits_for_the_shards_runs_verify_cover` (red when the workflow has no cover check); `test_testfarm_plan.py::test_verify_cover_fails_on_a_missing_or_doubled_unit` |

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
under the module that holds them after the move; a number with no source is derived from the device or removed. The library ships its own
table: `poolhouse-test limits` prints every row below with the value measured now and the rule that gives it, because a project that
imports the library cannot edit Poolhouse's `docs/limits.md`.

| Name | Value | Source |
|---|---|---|
| capacity | logical CPUs less the measured outside load and the user's `reserved_cores` | the OS (CPU count), a load sample, the user's setting with no default |
| planning capacity of a device | the most units the sender has seen it run at once; its claim for its first shard only | the sender's own observation |
| automatic workers | `sqrt(estimated seconds / measured start-up seconds)`, between 1 and the capacity | the minimum of start-up times workers plus work divided by workers; start-up from the recorded `setup` seconds |
| background class | the project's `[schedule] background_after` | the project's file, no runner default |
| aging, backfill | aged by the estimated work ahead; backfilled when it ends before the head's estimated start | the history's estimates (5.8), no constant |
| canary rate | the project's `[reuse] canary`, in (0, 1]; `init` writes `1 / units` and prints the derivation; with none, reuse is off; a run that serves any hit re-executes at least one | the project's file, no runner default; Poolhouse's file sets it |
| canary seed, run id, job key | operating-system entropy (seed, run id); the argument vector and tree hash (job key) | no constant |
| first reuse of a `remote` entry | always a canary | a rule, no number |
| single-flight wait | while the claim holder is alive | the OS's process liveness, no time |
| hang guards | no-progress stretch: longest recorded silence or measured round trip times `silence_margin`; total: recorded median times `total_margin` | the history and the measured round trip; both margins are the project's, finite and at least 1, and both are required, no runner default |
| a device's liveness | the measured round trip of the stream times `silence_margin`, counted across the sender's completed waits | the measured round trip; the project's margin |
| the first unit's bound | `--timeout S` on the command line, or a terminal a person can end the run from | the caller, no default; an unattended first run with neither is refused (5.10) |
| the receiver's time ceiling for a sender | the seconds the receiver's person gave (unit, shard, slot-seconds), else the longest wall time the device measured for any unit of the project | the receiver's person at `allow` or `policy`, or the device's own clock; no constant (10.6) |
| the sender's bound on a shard | the sum of its units' bounds, counted from the device's report of the first unit start | each unit's `timeout`, else its recorded typical duration times `total_margin` |
| unit and test timeouts | the unit's `timeout` and `test_timeout` are the project's; with no unit `timeout` the hang guards derive it | the project's file, or the history |
| step build deadline, slots, idle, prune | the step's `timeout`, else the hang guards; its `slots` or the free slots; `keep` or least recently used on disk need; no interval | the project's file, or the measured free slots and free disk |
| history sample | the median of the recorded samples; for a device unit the sender's measured interval | no clip |
| retention of store, history, evidence | `keep`, else least recently used when the volume's free disk is needed | the project's file, or the measured free disk |
| fixed overhead limit | 5 s or a quarter of the wall time | AGENTS.md section 5 |
| units per shard | no bound of the runner's own | the receiver's request-size bound (10.6) |
| slices per run | the plan (targets times `slices_per_target`) or the project's `slice_count`, at most the sender's free memory divided by the measured size of one planned unit | the project's file, the plan, measured memory |
| id size | an id carried as an argument fits the argument space of the invocation on its target; ids in `{idfile}` fit the request-size share | `sysconf(SC_ARG_MAX)` less the measured environment and other arguments, and the platform's longest single argument; the receiver's free scratch disk |
| shard deadline on a device | none of its own: the sender's bound above | replaces `shard_spec.MOST_SECONDS` (3600 s), which has no source |
| packed tree, unpacked tree, members | parts of one budget: the receiver's free scratch disk and free inodes divided by the shards it holds, spent in a fixed order (10.6) | replaces `shard_tree.MOST_PACKED`, `MOST_UNPACKED`, `MOST_MEMBERS` (24 MiB, 256 MiB, 20,000), which have no source |
| runner output, tee, result, tail, failures, message, tests, junit | equal parts of what the packed and unpacked trees leave of the same share (10.6) | replaces `shard_run.MOST_LOG`, `MOST_TEE`, `shard_routes.MOST_RESULT`, `shard_result.MOST_*`, which have no source |
| the parser's limits | memory: a share of the device's free memory divided by the shards held; time: `RLIMIT_CPU` from the hang guard of the request that asked; input cap: the memory share divided by the amplification a test measures once per release | measured memory; the guard; a measurement |
| shards held, per sender | the device's free slots at admission, and the free disk (10.6) | replaces `shard_store.MOST_KEPT`, `MOST_OWNED` (8, 4), which have no source |
| idle upload, kept shard, grace before kill | the receiver's admission clock: an upload idle for the sender's own round trip times the missed round trips; a kept shard until its result is fetched or the disk is needed; grace is the process group's own exit | replaces `shard_store.IDLE_S`, `KEPT_S`, `shard_run.GRACE_S` (600 s, 24 h, 30 s), which have no source |
| poll, lost device | none: unit rows are reported on the stream; a failed or silent stream is the loss | the transport's own failure (reset, the OS's keepalive timeout) and the liveness row above; replaces `dev.POLL_S`, `dev.LOST_S` (2 s, 45 s) |
| shard id, run id, install-instance id | 128 random bits each | a security floor: guessing or colliding is out of reach (rows of `docs/limits.md`) |
| control nonce, frame nonce | 128 random bits each | a security floor (rows of `docs/limits.md`) |
| store master key, key per project | 256 random bits; each project's key is derived from the master key and the project's identity record | a security floor: the key each project's HMAC key comes from (a row of `docs/limits.md`); no constant |
| model server slots | not a runner number | `poolhouse.serve` keeps its default of one (`docs/limits.md`, `docs/agent-policy.md` section 1); the runner starts no server and passes no slot count |
| exit statuses | 0, 1, 4, 6, 70, 75, 124, 130 | section 11 |
| queue limit | the project's `[schedule] queue_wait`, else none | the project's file, else the no-progress guard; replaces `testslots.WAIT_S` (3600 s) |
| `test_timeout` in Poolhouse's file | 120 s | `pyproject.toml` `timeout`, the owner (`docs/limits.md`, `pytest timeout`) |

`unrecorded_max` (the cap on a unit with no record) is the project's setting with no runner default, and the sample clip and `JOBS_DIVISOR`
are gone. No row of this table has "the owner's value in code" as its source: a number is measured, set by the project's file with no
runner default, given by the receiver's person, or has a row in `docs/limits.md` with its protocol, operating system or security reason (the
shard id, the run id, the install-instance id, the control and frame nonces, the store master key). The small counts the text uses (a refused
unit is reassigned once, a device is `suspect` after a second refusal, a failed canary gets one more fresh run, a run that serves a hit
re-executes at least one) are rules and not measurements, and have no number to source. `poolhouse.sandbox.policy.Limits` carries defaults (a
wall time and an output size) with no source in this document, so the runner passes explicit limits on every call (3.1).

The Rust host's constants (`MOST_RUNNING` 2, `MOST_OPEN` 6, `MOST_KEPT` 40, `GRACE_S` 120, `KILL_AFTER` 45) have no source: the single
receiver derives them (running shards from the device's free slots, open shards from its free disk, the kill grace from the process
group's own exit) and `docs/limits.md` is updated in step 8.

## 16. Open questions

Product choices this document cannot settle from the code and documents. Each names the option it would take.

1. **Which pool stack carries the allow list of a pool in prod mode, and does the Rust carrier stay?** Pools in dev mode are served by the Python
   daemon; pools with charters, grants and phone approvals are served by `poolhouse-node`, whose `shard_consent` grant is the only
   place a person's signed yes for "this sender may run code here" exists. The design makes one server and one wire. Option A: delete the
   Rust shard host and its two grants and keep the allow list as a device setting changed by `poolhouse-test allow` (one act with one
   sentence, put to the host's gate, 4.8), valid for every pool mode
   (`docs/agent-policy.md` says there are no production users and pools are not preserved). Option B: keep the grant as the way the allow
   list is written and have the node bind its member operations to the same Python server, so a phone signs the same act. The design
   takes A unless the owner wants a phone-signed grant for it.
2. **Where tests run.** Decided: the target is the user's setting and the `--on` flag, default `local`; a tree is sent only for a project the
   user has named; the first send prints the file count and top-level names (4.8, 6.1). Nothing remains open.
3. **Dependencies on a device.** A device runs a project only with what it has installed (a Python with the project's test
   dependencies, a toolchain). The design lets the project declare steps (an environment command cached by a key) and `[requires]`
   probes, and never installs anything on a device on the runner's own initiative. Steps have no network under the default policy, so a
   project whose build fetches dependencies (no vendored dependencies) is not served by devices under it: it runs on `local`, or on a
   device whose policy names the destinations. Whether the pool's dev mode should name any destinations by default is the owner's.
4. **How a unit that needs a model or the GPU uses the lease through the runner.** Decided: the runner holds the resource for the unit's
   invocation and asks the broker to delegate it to the invocation's process group, and `poolhouse-serve up` finds an ancestor of its
   caller in a delegated group (5.5). No lease id travels in the environment, and the model server runs with one slot. The change to
   `poolhouse.serve` is in step 2. Nothing remains open.
5. **Remote passes and landing.** `design-test-selection` 6.5 makes a device's pass advice and stores it as skip evidence only when
   corroborated. This design keeps that: a device pass is stored only when a second source agrees, serves only that device, and never
   counts as evidence of a run the sender made (5.3, 6.4). Whether a project may declare a device (the owner's own) trusted enough for
   its pass to be stored as skip evidence for local runs is a trust decision this document does not make; the default is never.
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
9. **How a person's yes reaches `own`, `allow` and a widening `target`.** Decided: only host evidence through the one `PersonGate` the host
   supplies (for Poolhouse, the `person-consume` evidence); with none available the command refuses and prints what a person does at their own
   editor; a controlling-terminal answer is never accepted; the host guards the settings paths; the sentence lists the devices (4.8). Nothing
   remains open.
10. **What `named` means for evidence.** Decided: the Owner zone is denied to every child by exact path, the signing key is per project, and a
    task is credited from a run of the exact tree by a session that did not author it (5.3). Nothing remains open.
