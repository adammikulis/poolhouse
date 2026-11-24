# A test runner and sharder that any project uses

Status: partly built. The rationale below is the cut of a 2,300-line design; the full text, with its sections 3 to 15 (schema
reference, trust zones, the core, transports, presets, security attack list, migration steps and numbers), is in git history at
`ac0639291` (`git show ac0639291:docs/design-test-runner.md`). The three break-it passes that shaped it are in
`docs/notes/design-test-runner-breakit.md`, `-2.md` and `-3.md` (findings F1 to F30, N1 to N18, S1 to S16).

## What exists

| Built | Where |
|---|---|
| Poolhouse's own tier runner, jobs, history and phase line | `scripts/test`, `testjobs.py`, `testqueue.py`, `testclass.py`, `testprelude.py`, `testphases.py`, `testorder.py`, `testhistory.py`, `testdurations.py`, `testtime.py` |
| Machine-wide admission: CPU permits, shortest estimate first, backfill, aging, per-test permits | `scripts/testslots.py`, `testslots_policy.py`, `testslots_rpc.py`, `testslots_runner.py`, `testslots_pytest.py`, `testqueue.py` |
| Result reuse: per-file key, chain-hashed store, single flight, canary | `scripts/testreuse_{key,store,run,plugin,facts,imports,narrow}.py`, `docs/test-reuse.md` |
| Gate steps reused while their inputs are unchanged | `scripts/gaterun.py`, `scripts/gatescope.py` |
| Affected selection (import graph and string rules) | `scripts/affected.py`, `scripts/affected_rules.py` |
| Shards to a pool device, with a per-device allow list and a reuse ledger | `app/poolhouse-node/src/shard/`, `src/poolhouse/testfarm/` (`client.py`, `consent.py`, `devices.py`, `ledger.py`, `report.py`), `scripts/test_on.py`, `poolhouse test devices` |
| The project description: `testfarm.toml`, `poolhouse test init`, `check`, `list` | `src/poolhouse/testfarm/cli.py`, `core/project.py`, `core/select.py`, `model/` |
| CI division into shards | `scripts/ci_shards.py`, `tests/ci-durations.json` |

Not built: the generic planner and divider with presets (pytest, cargo, npm, a directory of executables), `poolhouse test run`,
`plan`, `affected`, `own`, `allow`, `target` and `settings`, the one wire for shards (the node carrier and the older pool carrier
`fleet/shard_*.py` both still exist), the executor's confinement of handed code, fail-first as code (it is a rule in
`docs/agent-policy.md` and the agents' definitions, checked by no hook), and `scripts/test` as a few lines over the public command.
`docs/design-test-selection.md` holds the selection and evidence rules for Poolhouse's own tests; `docs/test-execution.md`,
`docs/test-farm.md` and `docs/test-reuse.md` describe the built parts.

## Why

The job "run these tests somewhere, in parallel, and keep what passed" was written several times: the tier runner, the admission
code, the reuse store, two shard carriers (the node's and the pool daemon's), `--on`, `--split` and the CI splitter. The owner
ordered one general runner and sharder, a feature of the library, that any repository that installs Poolhouse uses for its own
tests, with Poolhouse's repository as the first project, through the same public interface and nothing private.

## The design in a page

- **The runner is a provider and every repository a consumer.** A repository describes itself in one file, `testfarm.toml`, and
  nothing else. The runner has no name, path, layout or default for any repository, Poolhouse's included; everything that varies
  between projects is data in that file.
- **The contract has three parts.** LIST prints test ids. RUN executes a subset of ids (an argument vector with `{ids}`,
  `{idfile}`, `{result}`, `{extra}`; never a shell string) and signals pass or fail per id by exit code, JSON lines or junit; an id a
  result file does not mention counts as failed, never as passed. DEPS (optional) says which files an id depends on; without it an id
  depends on the whole tree, so it is reused only for an identical tree and is always affected. A project that gives LIST and RUN gets
  parallel local runs, sharding and reuse for an identical tree; DEPS adds reuse across edits and affected selection.
- **Groups and labels.** A group rule makes ids that must run together one unit (a pytest file, a cargo target). A label gives a
  unit a need the project defines: a lease, a serial group, a platform, never leaves this device, never reused. Environment and
  build steps are commands the project declares, run once per device and cached by an input key.
- **Presets are tools, never repositories.** A preset is a TOML table with a LIST and a RUN for a tool; it lives outside the core
  and imports only it.
- **Division is a pure function** of the listed ids, the config and a seed file. Which machine runs a shard, what the store holds and
  what history says never change a division, so two machines produce byte-identical divisions and CI can verify the cover.
- **Transports** take a shard somewhere: this machine, a pool device over the pool's mutual-TLS routes, later ssh. One sharding
  path and one wire protocol replace the node carrier, the pool carrier, `--split` and the CI splitter.
- **Where tests run is the user's choice, never the project's.** The target comes from the user's settings and `--on`, default
  `local`. A project file lists needs and cannot name a device or pool. A tree is sent only for a project the user has named as their
  own, and only to the devices the person's yes named.
- **Handed code runs confined, in a copy.** Local runs, LIST, steps, hooks and units of a project the user has not named run under
  the sandbox a sent tree gets, in a scratch copy. A project is named by one yes per project, from evidence the host supplies
  through one gate and never from a terminal.
- **Trust is zones with one-way boundaries.** The executor never reads or writes by name a path a test can write; the configuration
  is parsed once from the bytes that were hashed; the Owner zone (keys, store, settings) is denied to every child in the turn it
  runs; a process takes one profile, built by the runner; git runs only confined from a closed list; nothing that grants authority
  travels in an environment variable. What the store proves is that the runner ran the exact tree, not that the code is honest.
- **Time is accounted, not mixed.** A unit's timeout counts from the moment it starts; queue wait and build time are reported
  apart (`queued`, `built`, `ran`), and a unit that never started because the machine was overloaded is `not started`, not failed.
- **Security.** Identity is the pool's; the receiver's policy decides what runs; every tree and result is checked against a hash the
  sender computed (which proves freshness, not that a device ran the tree: a device's pass is advice, stored only when a second
  source agrees); text from a device or a unit is data inside frames the sender prints; every bound has a source.

## The config schema

One file, `testfarm.toml`, at the project root, parsed with `tomllib` strictly: an unknown key is an error that names the nearest
known one, a wrong type is an error, and `schema = 1` is required. Paths are relative to the file and cannot leave its directory.
Numbers are finite and in range (TOML parses `inf`, `nan` and `1e999` as floats, so the validator refuses them, and zero and negatives).
`tests/test_testfarm_config.py` reads the key table below to check that the schema in `src/poolhouse/testfarm/model/schema.py` holds
every key. Section numbers in the table, such as 5.3 or 4.7, name sections of the full text at `ac0639291`.

### 4.1 Reference

| Table and key | Meaning | Default and its source |
|---|---|---|
| `schema` | the schema version | required |
| `preset` | a tool preset to start from (section 7); any key below overrides it | none |
| `[project] name` | a label in reports only | the directory name; never an identity |
| `[tree] include`, `exclude` | globs of untracked files to send in addition to the tracked ones; globs left out of the tree sent to a device | none; none. The sent set is the tracked files (`git ls-files`, a confined call, 4.9) plus the untracked files a unit's DEPS or `include` names through a glob that is not the whole tree; never `.git`, never a link; a project outside git sends only what `include` and the DEPS globs name (6.3) |
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
| `[run] launcher` | an argument vector put in front of RUN that can only narrow what the sandbox allows | none; local only; on macOS only a wrapper that is not Seatbelt (4.7, 10.2) |
| `[run.sandbox] deny_read`, `deny_write` | paths the project asks to deny to its own children, merged into the runner's one profile; it can only narrow (4.7) | none |
| `[run] needs` | the steps every unit waits for; a label or a `[[unit]]` overrides it | all |
| `[deps] globs` | a table `id-glob = [file-globs]`; each set contains the unit's own file, and every glob matches a file | none: the whole tree |
| `[deps] command`, `self`, `timeout` | an analyzer command printing DEPS rows; the files that are the analyzer itself; seconds it may run | none; `self` always joins `everything`; the hang guard |
| `[deps] everything` | a change to any of these globs selects every unit and changes every key | `testfarm.toml` itself, always |
| `[[step]] name, run, inputs, tools, env, slots, timeout, keep, prune` | a build or environment command (4.4) | `slots` and `timeout` below |
| `[label.NAME] match, lease, serial, platform, local, net, reuse, slots, estimate, timeout, needs` | what a label means (4.3); `match` globs ids that carry it | see 4.3 |
| `[set.NAME] labels, not_labels, ids, extra, reuse` | a named selection: `poolhouse test run @NAME` (4.5) | none |
| `[reuse] key_env, key_tools, canary` | extra key inputs; the sampling rate of canary re-runs, in (0, 1] | none; none; none, and with no `canary` reuse is off and the report says so (5.3) |
| `[failfirst] tests, line, exempt, mode` | globs of test files, a template over `{id}`, `{sha}` and `{error}` for the printed line, the literal prefix of an exempting commit-message line, the mode (5.9) | none; the runner's own line; none (no exempt route); `report` |
| `[schedule] seed, unrecorded_max, silence_margin, total_margin, queue_wait, background_after` | a committed durations file used by `divide` and until history exists; the most seconds assumed for a unit with no record; the margins of the hang guards (5.10); the queue limit; the estimated work after which a run takes the `background` class (5.5) | none; none (the median of the recorded ones, uncapped); none, and both margins required unless every command the runner starts has a `timeout` (the run is refused with status 4, 5.10); none; none; none (every run is interactive unless the person says `--background`) |
| `[requires]` | things a device must have, checked by probes the device owns (6.5) | none |
| `[[unit]]` | a command unit with no LIST (5.7) | none |
| `[presets.NAME]` | a preset defined in the project (7); the table is `presets` because `preset` is the key that names one, and a TOML key cannot be a text and a table | none |
| `[list] glob` | the `files` preset's glob | none |
| `[hooks] before_plan, before_run, after_run, timeout` | commands run on this machine only, never on a device; seconds each may run | none; the hang guard |

The file has no key that chooses a target, a device or a pool, and none that names an event sink: where a run goes and where its endings
are told belong to the user.

## Decisions

1. A device's pass is stored as skip evidence only when a second source agrees, serves only that device and never counts as
   evidence of a run the sender made. Trusting an owner's own device for local skip evidence is not decided; the default is never.
2. A person's yes for `own`, `allow` and a widening `target` comes only from host evidence through one gate (for Poolhouse, the
   `person-consume` evidence); with none the command refuses and says what a person does at their own editor; a terminal answer
   is never accepted.
3. A unit that needs a model or the GPU holds the resource for its invocation, and the broker delegates it to the invocation's process
   group; no lease id travels in the environment.
4. Any unit longer than the ideal shard is split by id from recorded durations; order dependence in a split file is a defect in the
   test and is reported.
5. A device runs a project only with what it has installed; the runner never installs anything on its own initiative.

## Open

- Whether the Rust shard host and its two grants (`shard_call`, `shard_consent`) are deleted in favour of an allow list kept as a
  device setting (option A, the design's default), or kept so a phone signs the same act (option B).
- Whether a device's policy should name network destinations by default, for projects whose build fetches dependencies.
- Whether ssh is built, and whether it needs the same confinement on a device that is not a pool member (no step depends on it).
- The config file name: `testfarm.toml` (used) or `poolhouse-test.toml`.
