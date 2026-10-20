# Test selection by reach, and shards on pooled devices

Status: design, 2026-10-19, revised after a non-author review (section 12). Nothing here is built. Claims carry a tag:
**[measured]** (commands in section 3), **[code]** read in this tree (`0.3dev` at `76817ce35`; the review's checks at
`264b54056`), **[plan]** a proposal. The owner ordered this redesign: tests are selected by what a change reaches, from an
import and data dependency graph built once and cached by tree hash, and run in shards on the pool's devices when a pool
exists. Decisions the owner has made are in section 11.

## 1. Summary

- Selection today is a depth-1 import lookup plus string-matching rules. On nine recent commits it picked 7 to 391 test
  files unrelated to what the change reaches: 343 where the real reach is 2, 191 for a regenerated JSON file, and 7 to 26
  (or "unmapped") for the three Rust commits where 170 files can start the node. A call costs 4 to 20 s and caches
  nothing. Reuse does not help across code changes: all 920 test files carry the whole-tree digest in their manifest.
- A static graph cannot be both sound and selective here. Followed transitively it reaches 868 of 920 test files from a
  typical `src/` change over all edges at depth 50, and 845 at name level, because of a few hubs (the conftest closure alone holds
  715 of 2,246 modules, plus module-level registry tables and package `__init__` files).
- So four layers: a typed static floor (exact edges, one parse per file content, cached by tree hash; a directory lister is
  selected by any change under its directory); observed edges recorded by the lead's own instrumented run and keyed by
  content hash (transitive precision, a record trusted only when its children were observed); a hub rule (a change to code
  every session runs selects everything and goes to GitHub); evidence (a selected test is skipped only when a pass exists
  for the same dependency digest).
- Soundness comes from three rules (an unknown edge selects, a skip needs a positive proof, the selector's own files
  select everything) and one check on the full runs that exist (pull request, `promote/<date>`, nightly): a ledger per
  commit range compares each run's failures with the selection for that range, each miss fixed in the turn it is found.
  A development push starts no full run (`de1df9fc7`), so there is no per-push check.
- A shard is a tree hash, a file list, a flags digest and a nonce. The member fetches that tree, verifies its git tree
  hash, runs it under its own broker lease and returns a signed result. A device's result is advice: a pass serves the
  inner loop, is stored as skip evidence only when corroborated, and never counts for landing, task verification or
  release; a failure is always accepted, because one re-run verifies it. The member runs every shard inside a deny-default
  sandbox that its installed executor applies, never one the sent tree carries (6.3).
- Nine build slices, each landable alone with a bound on its fixed cost; the confined executor (slice 6) is a hard
  prerequisite of every slice that sends a tree to another device. One human step is added, by the owner's decision: a
  separate yes per device before it runs another device's tests, never inside adding a device and off under `open`
  (decision 3).

## 2. What exists today

### 2.1 The mechanisms

| Mechanism | Where | What it does |
| --- | --- | --- |
| An agent names files | AGENTS.md section 5; `scripts/testscope.py` | An agent may run any test files it names, never the whole suite |
| `scripts/test quick` | `scripts/test`, `scripts/affected.py` | `affected.select` files, then testmon's per-checkout map for the rest; an unmapped path runs the full tier |
| Reuse | `scripts/testreuse_*.py`, docs/test-reuse.md | A file's stored pass is reused when its key and manifest still hold |
| GitHub `ci` | `.github/workflows/ci.yml`, `scripts/ci_shards.py` | Every test file, in 8 shards balanced by `tests/ci-durations.json`, on a pull request, a push to `main`, the nightly schedule and a manual dispatch; a push to a development branch runs no shard (`de1df9fc7`, the `shards` job `if:`); junit uploaded. HANDOFF records that no sharded run has completed yet |
| Test farm | `scripts/test_on.py`, docs/test-farm.md | `--on DEVICE` runs a tier on a member of the pool |
| Broker | `scripts/testslots.py`, docs/test-execution.md | One admission queue for CPU leases on a machine; `scripts/test-on-linux` runs a container under it |

### 2.2 How `affected.select` decides [code]

- It parses every module under `src/poolhouse`, `scripts` and `tests` (2,246) for imports at any depth in a file
  (function-level ones count) and for dotted names in strings (`affected.graph`). `reach(..., DEPTH = 1)`: a changed
  module selects the test files that import it directly; a test file ends a path, a helper module under `tests/` is not a
  step, and `tests/conftest.py` has no edges.
- Scripts are placed by `affected_rules`: a test file that imports it, names `scripts/<name>` or the bare name in a
  string, or reaches it through another script, plus every test that enumerates `scripts/`.
- `pyproject.toml`, `budgets.json` (unless it only fell), `tests/conftest.py` and `tests/known-fixtures.txt` select
  everything. A path nothing places is `unmapped`. Six guard tests are added whenever anything is selected.

### 2.3 What the farm does [code]

It sends the whole working tree as one gzip tar (24 MiB packed at most) to a member its person allowed; the member
unpacks it into a scratch checkout, runs `scripts/test TIER --no-reuse FILES` under the node's lease table (half the
cores, class background) and returns per-file counts, which the sender keeps in `remote-results.json` per device.
`quick --on all` sends the same list to every device for platform coverage; `--split` divides a list by recorded duration
and is a person's option. Counts are taken as the device states them: no signature beyond the TLS channel, a ledger apart
from the reuse store, and task evidence reads only the local store. Speedup has not been measured (docs/test-farm.md).

## 3. Measurements

Machine: macOS, 16 CPUs, load 7 to 11 (other agents running), 2026-10-19; `76817ce35` and the nine commits below, each in a
scratch worktree, run with the primary checkout's Python 3.13 and scripts kept outside the repository. Commands:
`affected.select(root, changed, gone, base=sha^)` and its phases timed one by one; `testreuse_key.build_manifest` for each
test file; `Closures.closure([test])` and module-level imports from conftest and the `-p` plugins for the hubs; `git log
-40 --no-merges origin/0.3dev` for the commit mix. Not measured: an execution map (none exists), the reuse store's
contents, any run on more than one device, a Windows device, the recorder's overhead.

### 3.1 Fixed cost of selecting [measured]

`affected.select` took 3.9 to 5.7 s for diffs that touch only Python modules and 11.2 to 20.0 s when a script, a
generated file or `budgets.json` is in the diff (nine calls). Phases on the tip tree: graph (parse every module) 4.1 s,
`affected_rules.corpus` (parse scripts, tests and source again) 5.1 s, `dynamic_tests` 1.8 s, index 0.04 s. The reuse
key's closure over all 920 test files takes 18.8 s; `tree_digest` 0.12 s. Nothing is cached between calls: every `quick`,
`land` plan and `gatescope` check parses the tree again.

### 3.2 Reuse manifests carry the whole tree [measured]

`testreuse_key.build_manifest` was run for all 920 test files: all 920 manifests hold the whole-tree digest (code,
documents, packaging, Rust sources and the HEAD commit), and the median manifest lists 717 files (715 to 876). A manifest
gets the digest when any module in the closure spawns a process or imports by machinery the closure cannot follow, and
the conftest closure does both. By this key a stored pass survives only an unchanged tree and commit (read from the
code; the store's contents were not read).

### 3.3 The hubs [measured]

- `tests/conftest.py` imports 358 modules at module level (339 under `src/`, mostly through `poolhouse.testing.fakes`)
  and 715 counting function-level imports. Without conftest, a test file's import closure has a median of 631 modules
  (p90 719, maximum 856) of 2,246, and 623 `src/` modules are reached by at least half of the test files.
- Of the last 40 non-merge commits, 10 touch one of those 358 hub files (`scripts/testslots*.py`,
  `scripts/testreuse_plugin.py`, `src/poolhouse/sandbox/*`, `node_launch.py`), 3 touch `app/`, 18 `scripts/`, 8 only
  `tests/`, 5 only documents.
- A hub is not only conftest. At name level (top-level defs, classes and the module body as nodes), `node_launch.swap` is
  referenced from a lambda in a module-level command table, so every importer of `node_launch` depends on `swap`.
  Transitive name-level reach from the 48 changed symbols of `258dc2826` was 845 test files (904 with conftest edges).

### 3.4 Nine commits [measured]

"Floor" is `affected.reach` at depth 1 with the helper hop and none of the string rules; "own" is the test files the
commit itself changed; "module transitive" follows hard import edges plus one lazy hop. "Name level" is a prototype
(nodes are top-level defs, classes and module bodies, changed nodes found by AST hash against `sha^`, edges are names
resolved through aliases and package re-exports, cumulative test files by depth, conftest edges left out). CI seconds are
from `tests/ci-durations.json` (whole suite 7,933 s; median for a file with no record).

| Commit | What it changed | Picked today (files, CI s) | Floor + own | Name level, depth 1 / 2 / 3 | Module transitive |
| --- | --- | --- | --- | --- | --- |
| `76817ce35` | 2 scripts, 1 test, 1 doc | 343 (4,437) | 1 + 1 | 1 / 1 / 1 | 1 |
| `3d48da3f4` | broker script, 5 tests, doc | 13 (46) | 2 + 5 | 4 / 73 / 195 | 3 |
| `258dc2826` | 10 `src/`, 1 script, 2 tests | 391 (5,244) | 180 + 3 | 20 / 56 / 133 | 868 (all edges, depth 50) |
| `a0ab8321c` | 6 Rust, 2 tests | 10 (31), 5 Rust paths unmapped | 0 + 2 | none | 0 |
| `2d412f828` | 10 `src/` (sandbox), 2 tests | 32 (128) | 13 + 2 | 11 / 25 / 108 | 778 |
| `e2081f983` | 2 Rust, 1 Rust test | 7 (24), 2 paths unmapped | 0 + 0 | none | 0 |
| `a34a612d3` | regenerated JSON | 191 (3,408) | 0 + 0 | none | 0 |
| `d8e73a996` | 2 Rust, `board_cli`, doc, test | 26 (155), 2 Rust paths unmapped | 10 + 1 | 2 / 12 / 45 | 70 |
| `76ce112d2` | a tests helper and its test | 168 (2,469) | 161 + 1 | 1 / 47 / 120 | 50 |

What the table shows, from the diffs and the reasons `select` records:

- **Over-selection by rule, not by reach.** `76817ce35`: 144 of the 343 files come from "a source module that names
  `ci_watch`", 27 more through a tests helper (`node_kit`) reached by another script, 14 from the bare word `status` in a
  string; the real reach is the script's test and the hook's test. `a34a612d3`: 4 test files name `coverage.json`, the
  generated-file rule picked 191 through the generator script. `258dc2826`: 391 picked against a floor of 183.
- **Rust is invisible.** All three Rust commits left their paths unmapped (`quick` would run the whole tier, the lander
  ignores them), yet what a Rust change reaches is known: 170 test files can start the node (`testnode.needed`, counted per
  test file over all 920), a fifth of the files.
  `a0ab8321c` leaves 5 Rust paths unmapped, not 3.
- **Depth 1 hides indirection.** `3d48da3f4` changes `scripts/testslots.py`; `tests/test_test_kernel_isolation.py`
  imports `test_kernel_attestation` and `test_kernel_lifecycle`, which import `testslots`, and was not picked.
- **Static transitive reach is unusable.** The module-level column is 778 whenever a hub module changes (`2d412f828`);
  for `258dc2826` the earlier 779 is not reproducible and the review counted 868 files over all edges at depth 50 (that
  column's other cells were not re-run); no static rule separates the tests that execute the changed code from those that import it.
- **Name level does not fix it.** Depth 1 is small (20 against a floor of 180 for `258dc2826`) but unsound for a caller
  in the same module. Closing the changed symbols over their module's callers and taking one hop gave 1, 174, 320, 11,
  10 and 136 files for six commits, above the floor for two, because an edit to a module body (an import line) selects
  every importer. Symbol hashes stay for what they do well: telling a comment or reformat from a change, and keying
  observed edges (4.5).

### 3.5 What the suite costs [measured from `tests/ci-durations.json`]

817 recorded files, 7,827 s; the top 10 hold 2,655 s (34 percent) and the 144 files marked `slow` hold 4,447 s (57
percent). With 8 shards the ideal shard is 978 s and no file is longer; with 16 it is 489 s and two files are longer
(`test_graph_page.py` 814 s, `test_gates_incremental.py` 611 s); with 32 it is 245 s and three are (plus
`test_testreuse.py` 277 s), so more than 8 shards need a split by test. What the heavy files need decides where a shard
can go: 170 start the node binary, 45 drive playwright, 17 run `cargo`, 82 carry the `redteam` marker, 96 name a lease or
a model server (these stay on the sender).

## 4. The design: the reach graph

### 4.1 Nodes and typed edges

A node is a tracked file; a Python file also carries its top-level symbols (def, class, module body), each with the hash of
its AST (docstrings included: help text is generated from them). An edge says "this node can change that node's
behaviour".

| Edge | Extracted from | Stance |
| --- | --- | --- |
| import | `import` and `from` outside function bodies, with the imported names; package `__init__` chain | Certain |
| lazy import | the same inside function bodies | May execute; kept |
| name | dotted module names in string constants (`monkeypatch.setattr("a.b.c")`, `-m a.b`) | Exact match only |
| spawn | a path, `./name`, `python -m` module or `[project.scripts]` name that a file runs, including a path folded from a `/` chain (below) | An exact reference or a folded path; the bare word `status` is not a reference. What cannot be folded is opaque |
| data | a path string naming a tracked file; a directory listing (`glob`, `rglob`, `iterdir`, `listdir`, `os.walk`, `ls-files`) | A lister reads the content of every file under its directory, not only the membership: any change under that directory (an edit, an addition, a removal, a rename) selects the lister, narrowed only by a valid observed read set (4.5) |
| fixture | a test's parameter or `usefixtures` name to the fixture of that name; `autouse` fixtures and `pytest_*` hooks to every test | Name match over the conftest chain and imported helpers |
| rust | any file in the Cargo workspace members (`testnode.MEMBERS`) | Selects every test file `testnode.needed` returns (170 now) and the files that run `cargo` |
| config | `pyproject.toml`, `tests/conftest.py`, rises in `budgets.json`, the selector's own files | Selects everything |

**Listers.** The first design selected only direct readers for an edit under a listed directory, which is the baseline
miss: `select(docs/node.md)` returns 7 files and not `test_documented_env_vars`, which globs `docs/*.md` and rglobs `src`
for the variables it names; `test_glossary` scans the whole tree; 66 test files combine a repo-root constant with a lister.
A lister therefore depends on every file under the directory it lists, and the walk's root is resolved the way a path is
(below). Only a valid observed read set (4.5) may narrow it, and a test with none keeps the whole directory.

**Paths assembled from parts.** 259 script and test combinations name a script only as a bare string, about 96 of them real
references (`test_redact_audit` builds `ROOT / "scripts" / "audit-pii"`, `test_budgets_skip` builds `... / "scripts" /
"hooks" / "budgets"`), and the bare-name rule of `affected_rules` catches them today. The extractor folds `/` chains,
`Path(...).joinpath`, `os.path.join` and f-strings through module-level constants (`ROOT`, `HOOK`) into one path and treats
that as an exact reference. A path it cannot fold to constants is opaque, and so is anything that gives a name to a script
or directory only by assembly (a loop over names, a parameter, a format string with a free variable).

What an extractor cannot resolve (a computed `import_module`, `getattr(module, variable)`, a path built at run time, **a
path assembled from parts that do not fold to constants**, an unparseable file) makes that file **opaque**: it gets an edge
to every file of the category it could reach (all of `src/`, all of `scripts/`, or the directory), never to nothing. The
count of opaque files is reported and only falls.

### 4.2 Facts, and the cache by tree hash

One parse per file content. `reach-facts.json` in the project's reuse store holds, for each blob sha (with the module
context and the extractor's digest, as `import-facts.json` does now), the symbols with hashes, the edges of every kind
and the opaque flags. It replaces four parses today (`affected.graph`, `affected_rules.corpus`, `affected.dynamic_tests`,
`testreuse_key.Closures`). A tree's graph is assembled from facts by dictionary work and kept as `reach/<tree-hash>.json`
beside them, the tree hash being `poolhouse.activity.gate.tree_hash` (0.3 s) of the working tree. Every worktree of the
project shares both; entries unused for two weeks are dropped. A new tree costs the parse of the blobs that changed.

### 4.3 Selection

For a diff from a base tree to the working tree:

1. **Changed nodes.** Files by blob hash; Python symbols by AST hash, so a comment, a blank line or a reformat selects
   nothing, and a renamed or moved function is a removed node and an added node.
2. **Floor.** The tests that reach a changed file by exact edges at depth 1: importers (lazy ones too), name, spawn,
   data, fixture and rust edges, a tests helper module counting as part of the test that imports it. This is the reach
   `affected.select` has now, minus the string-matching rules and plus the rust and data edges, computed from the tree
   alone. A lister is selected by any change under its directory (4.1). It is module-level on purpose: a name-level
   floor is not reliably smaller (3.4).
3. **Observed reach (4.5).** Tests whose recorded execution touched any changed symbol, through any depth.
4. **No record.** A test with no valid observation (new, never passed on an instrumented run, its observed edges name
   nodes that no longer exist, or its record carries `child unobserved`, 4.5) takes the floor plus the transitive reach
   over import edges only.
5. **Hub.** A changed node reachable from the session hub (4.4) selects the tests that use that part of the hub.
6. **Evidence (4.6).** A selected test with a valid pass is not run.

`scripts/test select --explain` prints the reason per test, so a reader sees why any file was chosen or left out.

### 4.4 The session hub

The hub is what every test session executes, defined from the tree and not from a list kept by hand. It reuses
`affected_rules.infrastructure()` (conftest, `tests/__init__.py` and every `-p` plugin that `scripts/test` names) and widens
it:

- **Plugins.** The six `-p` plugins of `scripts/test` (`testslots_pytest`, `testdurations`, `testagentenv_pytest`,
  `testphases`, `testinterrupt`, and `testreuse_plugin` when `DEV_TEST_REUSE_RECORD` is set), read by the same regular
  expression `infrastructure()` uses, so a plugin added to `scripts/test` joins the hub with no edit here. The first draft
  named three.
- **Conftest.** The module body of `tests/conftest.py` and of the helpers it imports at module level (`environ_guard`,
  `network_guard`, `signal_guard`, `system_guard`); every `autouse` fixture and `pytest_*` hook, with the symbols they
  call and their function-level imports (`poolhouse.keystore`, `net`, `net.untrusted`, `net.policy`, `httpguard`,
  `serve.binary`, `sentinel.observers`, `activity.writer`).
- **Files the hub reads.** Any file read by a hub symbol, such as `tests/heavy-modules.txt` and
  `tests/seatbelt-modules.txt` (which decide the markers every test gets), is a hub node through the `data` edge.
- **Closure.** What those import at module level, as above.

A change inside the hub selects every test file, and an agent does not run that locally
(AGENTS.md keeps the whole suite as GitHub's job): it gets one line (`this change reaches every test; GitHub's full run
covers it at the pull request, the promote snapshot or the nightly run; these N files ran now`) and runs the floor.

The hub is a debt to shrink: `poolhouse.testing.fakes` pulls 339 `src/` modules into the session. Of the 10 hub commits in
the last 40, 5 reached it only through such library modules (`node_launch`, `sandbox/*`) and 5 through infrastructure
(`testslots*`, `testreuse_plugin`). Fixtures that import their library lazily move the first group out; a gate counts the
hub's modules and lets the number only fall (slice 3).

### 4.5 Observed edges

A static graph cannot say which of the 778 to 868 transitive importers execute the changed function; a recorder can. During a
run the pytest plugin records, per test file, which top-level symbols executed (`sys.monitoring` function-start events,
re-armed at each file; child Python processes record through an environment variable and a `usercustomize` in the run's
scratch site directory) and which files it read (the audit hook of `testreuse_plugin.py` already does). The record is keyed
by symbol hash, so it holds for any tree in which those symbols hash the same, and is kept per platform class.

**Source of the map.** The lead's own instrumented run, on the lead's device, writes the map; GitHub does not, because the
runs it makes (pull request, promote, nightly) are too rare and too late to keep a map current, and the pool does not,
because 6.5 forbids a member's data for the graph. A run that records is a run of the lead's own tests, under its own
broker lease; it is never sent to a member. It replaces testmon: no per-checkout `.testmondata`, no background `record`
run.

**A record is complete only when its children were observed.** The recorder sees the pytest process and the Python
children that inherit its environment. It does not see a child launched with a scrubbed environment or with `-I` or `-S`
(194 test files contain `env=`; the review counted 157 that pass it to a subprocess call), and the audit hook sees no read
made by a Rust node process. The record then carries `child unobserved` (set when the test file spawns a child the
recorder could not instrument, found from the spawn edges and from the audit hook's `subprocess.Popen` events), and a test
with that flag takes the static-transitive floor (4.3 step 4), never the observed set alone.

**Platform branches.** A path taken only on a platform the recording run was not (a Seatbelt or Windows branch) is in no
map. The Linux map may only add tests for code with a platform branch (a file that reads `sys.platform` or `os.name`, or
that imports `sandbox.seatbelt`); it never removes a test the static floor selected, and it is never used to leave a Mac
test out (decision 4). Mac-only paths are caught by no ledger either, which is said in section 5.

**Prototype first.** Before selection relies on the map (slice 4), the recorder runs on a subset of test files and the
selectivity is measured: for the nine commits of 3.4, how many files the observed record selects against the floor, with
`child unobserved` counted. The map is relied on only if the measured selectivity justifies it (decision 1).

### 4.6 One mechanism with reuse

Selection and reuse answer one question: "has this test passed with exactly these dependencies?". A test's dependency set
is the floor edges closed under the observed record, not the import closure plus the whole-tree digest; the manifest
keeps its recorded reads and probes, and the `tree` field stays only for a test with an opaque spawn. A test is selected
when no pass exists for its current dependency digest. The selector and the reuse key become one definition that cannot
drift, and a `src/` change reuses the pass of every test whose dependencies did not change. The store keeps its rules
for what is never reused (docs/test-reuse.md: markers, skips, writes outside the temporary roots, a tree that changed
during the run).

## 5. Why the selection is sound, and how that is checked

A static analysis of Python cannot prove a test unaffected. The claim is smaller and checkable.

1. **An unknown selects.** An opaque file, an unresolved spawn, a path no rule places, a missing or unreadable fact, a
   cache entry whose extractor digest differs: each widens the selection and prints why. A failure to build the graph
   runs the floor plus the hub rule and says so; it never selects less.
2. **A skip needs a positive proof.** A test is left out only because its dependency set is unchanged since a recorded
   pass (4.6), never because no rule found a reason to include it.
3. **The selector selects itself.** A change to the extractors, the graph code, the planner or the evidence code selects
   everything (the `config` edge), as `EVERYTHING` does for `pyproject.toml`.
4. **The graph comes from the tree.** Never from a device, a board entry or a cache whose key is not this tree hash and
   this extractor digest; a mismatching entry is recomputed from its blobs.
5. **The full runs that exist are the cross-check, per range.** A push to a development branch starts no full run
   (`de1df9fc7`: the `shards` job runs for `main`, a pull request, the nightly schedule and a manual dispatch), and
   HANDOFF records that no sharded run has completed. The full runs are therefore a pull request, a `promote/<date>`
   snapshot and the nightly run, and each covers a range of commits. For a failing test T at head H, let G be the last
   earlier run at which T passed (the previous run P when T passed there). A **miss** is T not in `selection(G..H)`, the
   selector run on the diff of that range; a test that is red at P and still red at H is compared with the last green,
   so a repeated red does not hide. A test that the selection kept but evidence skipped (4.6) is an **evidence miss**,
   counted separately. Each miss is fixed in the turn it is found: the missing edge or opaque flag is added and a
   fixture replaying the range is the regression test. The counts are budget keys (`selection-misses`,
   `evidence-misses`) that only fall.
6. **The window is commits covered, by change class.** The ledger reports, per class (hub, script, data, Rust, docs, and
   `src/` outside the hub), the number of commits the compared runs covered and the misses in them, with the upper bound
   the count allows: zero misses in n commits bounds the rate only below about 3 in n (200 commits bound it near 1.5
   percent, not at zero). An extractor digest change resets the counts, since the old numbers describe another selector.
   No run waits on the window (decision 2): full runs stay full, and the ledger says how far to trust the selection the
   agent runs now. A selection that reaches every test is handed to GitHub (pull request, promote, nightly) and the floor
   runs now (decision 5).

What stays unsound, and is named: behaviour that depends on a clock, the network or an environment value the manifest does
not name; a path taken only on a platform the observing run was not (the ledger's runs are Linux, so a Mac-only path is
caught by no ledger; the macOS job runs on the nightly schedule and on dispatch only); a dispatch through a registry the
recorder did not exercise; a child the recorder did not see (4.5). Observed edges can go stale; item 5 is the check on
that, beside the existing canary (`CANARY_RATE` 0.05).

## 6. Shards on pooled devices

### 6.1 The description

A shard is a request to run named files at an exact tree. Fields: `tree` and `base`; `files` (up to 200 names
`tests/NAME.py`, as `shard_spec.py` allows now, or `file::test` ids for a split file); `tier` and `flags` (the
`testreuse_key.argument_digest` of the pytest arguments); `selection` (the digest of the selection the files came from);
`nonce` (128 random bits per shard); `timeout_s`; `needs` (platform class, Python 3.13, node binary built or not). No
command line, environment or working directory travels; the node builds the one command, as now.

- **`tree` is a hash over exactly the transported file set.** `shard_tree.tracked_paths` ships only the top-level places
  its `SHIPPED` pattern allows (`src`, `tests`, `scripts`, `contracts`, `patches`, `packaging`, `docs`, `app` and the
  top-level files), tracked or untracked-and-not-ignored, with no symlink. The tree hash is computed over that list, mode
  and content per path, by one function that both sides call, so it is not the working tree's git tree hash (a subset has
  another one). The member recomputes it from what it unpacked.
- **`base` is a commit named by full SHA** (`origin/<development>` resolved by the sender), so the upload is a delta. The
  member fetches it by that SHA only, with no credential helper and none of the member's git credentials, from the
  repository's public remote; a base that cannot be fetched that way is sent whole by the sender instead. The member never
  authenticates to git for a shard.
- **`timeout_s` is the planner's estimate for the shard times two**, from the sender's durations (6.2). A shard that
  passes it is killed with its process group and counts as not finished (6.2, last rule).

### 6.2 The planner

`scripts/test plan` takes the selection, the durations (`tests/ci-durations.json` and the per-file history the farm and
the broker keep) and the devices that answered `shard_caps` (platform class, free slots, node ready), and returns shards;
the CI job matrix calls the same function instead of `ci_shards.py list`. It assigns longest first to the least loaded
device, as `shard_split.split` does, with these rules:

- A device takes a shard only when its estimated seconds are at least four times the device's fixed cost (`SETUP_S` is 3
  today; section 8 replaces the constant with the measured one).
- A file longer than the ideal shard is split by test id (the 814 s and 611 s files); a split file stores no per-file pass.
- Tests that need the node go only to devices whose node is ready; tests that name a model or GPU lease, `live_*`
  markers or Seatbelt stay on the sender (`LOCAL_ONLY` and the "never reused" rules). `scripts/test-on-linux` is a
  container on the sender's own CPUs, so it is not a device to the planner.
- **Platform.** A pass is per platform (the reuse key holds it). A member of another platform adds coverage and does not
  stand in for the sender's, so speed comes only from members of the sender's platform class (decision 4).
- A member never holds the device against its own owner: a shard takes the half-cores lease only while the device has no
  GPU, benchmark or foreground lease held or queued (6.3).
- A shard not finished by its deadline is run by the sender's own broker; the sender never waits on a device.

### 6.3 How a member runs it

1. `shard_caps` as now (consent, platform, Python, free slots), plus `node_ready` and the trees the member already holds.
   It answers with no free slots while any GPU, benchmark or foreground lease is held or queued on the device.
2. The sender sends the delta of its working tree against `base` (changed files only, the limits of `shard_tree.py`), or
   nothing when the member can fetch `tree` from `base` as 6.1 says.
3. The member builds the tree in a scratch checkout and computes the tree hash of 6.1; a mismatch with `tree` refuses the
   shard. The hash, not a checksum of an opaque tar, ties a result to a tree.
4. **The member's installed executor wraps the run; the tree does not.** The executor is `poolhouse.fleet.shard_run` from
   the member's own installed build, and it launches `scripts/test` from the sent tree inside a deny-default profile from
   `src/poolhouse/sandbox/` (`seatbelt.py` on macOS, `bubblewrap.py` on Linux), chosen and written by the executor:
   - reads: the scratch tree, the member's interpreter and system libraries, nothing else; the keychain, `~/.ssh`,
     `~/.poolhouse` and the home directory are denied;
   - writes: the scratch directory only;
   - network: loopback only;
   - the sent tree's own `scripts/test`, `tests/conftest.py` and `-p` plugins run inside it as ordinary code. The existing
     `scripts/testwritedeny.py` profile is allow-default, lives in the tree and is switched off by
     `DEV_TEST_WRITE_DENY=0`; it stays as the inner check of a local run and is never what protects a member.
   - a test marked `seatbelt` (`tests/seatbelt-modules.txt`) cannot nest a second Seatbelt profile, so it never goes to a
     member and stays on the sender; so do the tests that cannot run under the profile (those that need a browser, the
     `cargo` tests that download crates), until a slice 6 test shows a case holds.
   - without the confined executor on the member, `shard_caps` answers that the device takes no shards.
5. The executor runs `scripts/test all FILES --no-reuse --junitxml=OWN` through the member's own broker admission, with CPU
   slots from the node's lease table (half the cores, class background), so a shard queues behind the device's own work. A
   shard never takes a GPU or model lease. When a GPU, benchmark or foreground lease is requested while a shard runs, the
   node kills the shard's process group and the sender's deadline rule (6.2) takes the files; a benchmark row is never
   measured beside a shard (AGENTS.md section 1).
6. It returns the signed result (6.4) and removes the scratch checkout.

### 6.4 Results as evidence

A result is one record per file, `{file, tree, junit_sha256, counts, platform, python, runner_digest, started, ended,
shard_id, nonce}`, plus failing test ids with messages, all signed by the member node's key. It carries no `lookup_key`
and no `manifest_digest`: a member-supplied key would let a lying member shrink a test's dependency set and have a pass
stored against a digest the test does not have. The sender computes the key and the dependency digest from its own tree
and its own graph and ignores any value in the result. The node also writes `shard_done` to the pool board's audit log with the result digest (today only the exit code),
so the attestation is a signed, replicated entry too.

The sender's runner checks the signature against the pool's certificate for that device, that the device is still an
active member, the nonce, the tree, the platform and the counts (at least one test collected and passed, none skipped,
the junit's count), and the runner's and the hub's digests against the sender's own (a result from a runner or hub that
differs from the sender's is refused). Only then does it write an entry of kind `remote` into the evidence store, with an
expiry (a `remote` entry is a loan: it lapses after a fixed number of days and when the extractor digest changes, and
the test is selected again). **The remote device never
writes the store; the sender's runner does, after verifying.** `Store.hit` takes a `remote` entry only under the rule in
6.5, and `testruns.verified`, which the task board reads, accepts only the local `pass` kind, so a remote result never
satisfies a task checkpoint or submission.

### 6.5 What a device is not trusted for

A member is trusted to run what it is given and to say how it went, and to nothing else. A compromised or malicious member
can return a pass without running, run altered tests, report fewer tests than ran, answer for a tree it did not build,
replay an old result, claim a platform it is not, supply a key or digest that shrinks a dependency set, or withhold a
failure. The member is also a target: it runs the sent tree's own `scripts/test`, conftest and plugins as the person, so
without confinement a prompt-injected agent on device A reaches code execution as the person on device B. The confinement
of 6.3 step 4 is therefore part of what a member is trusted with, and not an option. So:

- **Never trusted for:** the graph or the execution map (built or fetched by the sender, never from a member); the choice
  of files (the member cannot drop one); anything that gates landing, a task checkpoint, a release or `main`; durations
  for planning (clipped to three times the sender's own value, as `testhistory` clips samples); the store (only the
  sender's runner writes it).
- **A failure is always accepted.** The runner re-runs the failing file on the sender before reporting it, so a false
  failure costs one file's run.
- **A pass is accepted by use.**
  1. *Inner loop:* a verified pass from an allowed member is shown (`passed on Studio Mac, signed`) and lets the agent
     push. It is not stored. A wrong pass shows as a red test in the next full run that covers the range (pull request,
     promote or nightly), where the ledger of section 5 records it.
  2. *Stored as skip evidence (`remote`):* only when a second source confirms it: another member (a different
     fingerprint), or GitHub's result for the same tree and file when the tree is a committed one (a pull request,
     `promote/<date>` or nightly tree). An uncommitted working tree has no GitHub result, so its second source is another
     member. Confirmation by a second member runs the shard twice, so the speedup of a stored pass is at most half the
     speedup of an inner-loop pass; the inner loop uses one member, and storing is the choice that costs the second run.
     Until confirmed the pass is `unconfirmed` and prints so. A sample of stored `remote` entries (rate 0.05,
     `CANARY_RATE`) is re-run on the sender.
  3. *Authority (landing, task evidence, release):* never from a device; only the sender's own execution and GitHub's run.
- **A sentinel in every shard.** The sender adds two generated files to the delta: a test that must fail with a message
  derived from the nonce and the tree, and one that must pass and print a digest of the shard's own files. A result
  without that failure and that value is refused and counted against the device. The member receives the sentinel and the
  nonce, so it knows what the output must be: the sentinel catches only an honest executor that ran nothing by mistake (a
  runner that silently collects zero tests). A member that reads the sentinel and fabricates its output, or runs it
  honestly and lies about the rest, is what confirmation is for.
- **Quarantine by signer, with an expiry.** A canary or GitHub mismatch for a `remote` entry writes an `incident` row (the
  existing mechanism), marks the device `suspect`, refuses its passes for skip evidence and demotes every `remote` entry it
  signed until each is confirmed again. `suspect` and every demotion carry an expiry: after it the device is probed with
  confirmed shards, and a quarantine nobody clears is a defect, not a state.

## 7. Attack first

Preliminary, by the author, then broken by a non-author reviewer (section 12), whose findings are in the entries below.
AGENTS.md section 1 still requires the break-it pass on the revised design before it is built; that pass is the first task
of slice 6 and blocks slices 6 to 9. One entry per case in that list.

- **A copied or cloned device or pool.** A clone with the member's key answers under its fingerprint: a signature proves
  the key, not the machine, so a clone's pass is confirmed by a second source before it is stored. A copy of the pool
  record elsewhere is a different pool; its results name another pool id and are dropped.
- **Replay.** The nonce is fresh per shard and inside the signed record; a result for another shard, tree or nonce fails.
- **A forged or below-threshold signature.** A forged signature fails the certificate check. One device's signature is
  below the threshold for skip evidence (two sources) and serves only the inner loop.
- **A compromised or malicious member.** The cases of 6.5. Worst case: a false pass that one agent sees until the next full
  run that covers the range, a false failure (one re-run), a wasted shard (run by the sender at the deadline), a skewed duration
  (clipped), a key or digest it made up (never read, 6.4). It reads the tree it is sent, the repository's own non-ignored
  files; a delta carrying a file a staged-file hook refuses (a name, a secret) is not sent. A sentinel is known to the
  member and catches only an executor that ran nothing by mistake (6.5).
- **A stolen phone or key.** A stolen device key is a compromised member: removing the member refuses it at its next
  request (the node does this) and the signer quarantine demotes what it signed. A stolen person key can add a member
  only by the charter's thresholds, unchanged here; the new member starts unconfirmed.
- **A malicious agent.** It holds `shard_call` for the fixed command and cannot call `shard_consent` or write evidence
  (only the runner process does). A hostile tree sent to a member meets that member's allowed list and consent, and then
  the member's confined executor: the tree's `scripts/test`, conftest and plugins run as ordinary code inside a
  deny-default profile that the member's installed build applies (6.3 step 4), so they read the scratch tree only,
  write the scratch directory only, touch no keychain, `~/.ssh` or `~/.poolhouse`, and reach loopback only.
  `DEV_TEST_WRITE_DENY=0` and the tree's own allow-default profile are not the protection. A member without the confined
  executor takes no shards. This is a hard prerequisite of slice 7.
- **An agent compromised without knowing it.** "Tests passed on the other device, skip them" in a board message or tool
  result changes nothing: selection reads signed records and the store, never board text, and no gate depends on a remote
  pass. An injected agent on device A that sends a hostile tree to device B is the case the confined executor is for.
- **A downgrade (secure to open).** A device that entered under `open` (its recorded enrolment) is never a trusted runner
  for skip evidence; a pool's join policy never changes in place (glossary, "reissue").
- **Approval fatigue.** The one yes per device (decision 3) is asked once per device and never repeated. Repeated shard
  requests meet the node's caps (two running, six open, two uploading per sender) and cannot become a repeated question.
- **Takeover by whoever is merely present.** Consent is a person's separate yes on a device, outside adding a device and
  off under `open`, and nothing here lowers it. Running `--on` at the sender needs a registered session and the
  `shard_call` grant.
- **Starving the member.** A shard that holds half the cores up to its timeout would keep a benchmark or a GPU lease
  waiting, and a row measured under load is void (AGENTS.md section 1). The member answers `shard_caps` with no free slots
  while a GPU, benchmark or foreground lease is held or queued, and kills the shard's process group when one arrives
  (6.3); the shard timeout is twice the planned estimate (6.1).
- **Attacks on selection.** A change that evades the extractors makes the file opaque and selects wider (rule 1); a change
  to the selector selects everything (rule 3); a forged cache entry is recomputed from its blobs and otherwise caught by
  the per-range ledger (section 5); a write by the same user is the limit docs/test-reuse.md already states for the store.

## 8. Fixed-cost bounds

Measured as CPU seconds, as `tests/test_runner_overhead.py` does, so they hold on a busy machine. Each bound is a test
that fails when exceeded and each slice names its own. AGENTS.md section 5 calls a fixed cost above 5 seconds, or above a
quarter of the wall time, a defect, so no bound here sits at or above 5 seconds.

| Cost | Today [measured] | Bound |
| --- | --- | --- |
| `scripts/test select`, warm facts and warm observed map, tip tree (tree hash 0.3 s included) | 3.9 to 20.0 s per call | 1.0 s, with the observed map loaded inside it |
| the same with every fact missing (a new checkout) | the same 4 to 20 s, every call | under 5 s once, then warm: the facts are primed from the project's shared store (4.2), so a new worktree parses only the blobs the store lacks |
| shard planning, 920 files, 8 devices | n/a | 0.2 s |
| member fixed cost per shard (fetch, unpack, tree hash, profile setup, collection) | unmeasured; `SETUP_S` assumes 3 s | under 5 s above the tests, printed as a phase; a shard is sent only if it is at least 4 times that, which keeps it under a quarter of the shard's wall |
| touch recording on an instrumented run | n/a | 3 percent of the run's wall; zero when off |
| facts file | `import-facts.json` is 0.97 MB for about 3,000 entries | 12 MB, pruned at two weeks |
| observed map file | n/a; the review counted 10.4k symbols across 920 tests | 8 MB, stored inverted (symbol hash to the tests that executed it) and deduplicated against the hub, so what every test executes is stored once; the load is part of the 1.0 s above |
| the delta a sender uploads | the whole tree up to 24 MiB packed | 24 MiB packed, now for the delta against `base` only; a delta over it is refused and the files run on the sender |

Wall time is the sender's `select`, plus the longest shard, plus its fixed cost. For the nine commits the floor and own
files are 0 to 2,618 CI seconds (median 25) against 7,933 for the suite. The figure of about 1,950 CI seconds for a Rust
commit counted 135 files with the node; with the 170 that `testnode.needed` returns it is higher and is to be recomputed.
On 4 devices of one platform class the longest shard is about a quarter of those when the files balance. This is
arithmetic from `tests/ci-durations.json`, not a measured speedup: nothing has run on more than one device.

## 9. Onboarding impact

One human step is added, by the owner's decision (3b): a separate yes, per device, before it runs another device's tests.
It is asked once through the question tool, never inside adding a device, never under `open`, and it is not part of the
base journey: a person who never answers still gets every test run locally, and the agent says so in a sentence. An agent
runs `scripts/test changed` [plan]: the selection, minus valid passes, sharded over the sender and the allowed members when
any answer, run locally otherwise; `--on` stays for naming a platform. Setup on a member is what `python -m
poolhouse.device_setup --yes` already does, with the confined executor installed with it (6.3). The Python 3.13, node-binary
and confinement checks go into `shard_caps`, so a device that cannot take shards says why in a sentence an agent relays
("Studio Mac did not take the tests: it has not been allowed to"); a person reads "tests passed on 3 devices, 1 unconfirmed".

## 10. Build plan

Each slice lands alone, in order, with tests watched failing first, its bound from section 8, and the deletion of what it
replaces. Slices 1 and 2 change nothing about what runs; slice 2 is the soundness instrument and lands before anything
narrows.

1. **One parse, cached by tree hash.** `scripts/reach_facts.py` (facts per blob, assembled per tree) behind the existing
   `affected.select`, which keeps its output; a differential test on synthetic trees and the nine commits. Removes the
   duplicate parses. Bound: warm select 1 s, from 4 to 20 s on every `quick`, `land` plan and gate-scope check; a cold
   tree under 5 s, primed from the shared project store.
2. **The miss ledger, per range, in shadow.** `scripts/ci_watch.py` reads the junit of each full run that exists (pull
   request, `promote/<date>`, nightly) and the selection computed for the range from the last green of each failing test
   to the run's head (today's selector first), and records misses, evidence misses and the commits covered per change
   class: the baseline miss rate of the selector in use now. It resets when the extractor digest changes.
3. **Exact edges and the hub.** Typed edges (4.1) replace the bare-name rule (a folded path is a reference, an unfolded
   one is opaque) and the lister rule (a lister is selected by any change under its directory); the Rust edge; the
   generated-file rule limited to tests that run the generator; a reason and a superset for every `unmapped`; the hub
   defined from `infrastructure()` (4.4) with its one-line answer; a hub-size budget. Expected on the nine commits: 343 to
   2, 191 to 4, the Rust commits from 7 to 26 files up to the files that start the node (170) plus the floor, the correct
   reach. `quick` and `land` switch after slice 2 shows the effect.
4. **Observed edges.** First a prototype of the recorder on a subset of test files, with the selectivity measured on the
   nine commits (decision 1); only if it justifies it, the recorder plugin (off by default, on in the lead's own
   instrumented run), the inverted symbol-hash store, `child unobserved` and the platform-branch rule, the selector
   reading it; `testmon` and `scripts/test record` deleted. Bound: 3 percent. Gate: the ledger of slice 2 reports, per
   change class, no miss the combined selection made against the full runs.
5. **Selection is reuse.** The dependency digest replaces the whole-tree digest in the manifest of tests with no opaque
   spawn; `scripts/test changed` becomes the agent's one command. Measure: reuse hit rate across a `src/` change (0 of 920
   today).
6. **The confined executor.** The break-it pass by another agent on this design comes first. The member's installed
   executor (`poolhouse.fleet.shard_run`) wraps every shard in a deny-default Seatbelt or bubblewrap profile from
   `src/poolhouse/sandbox/`: reads the scratch tree, interpreter and system libraries; writes the scratch directory; no
   keychain, `~/.ssh` or `~/.poolhouse`; loopback only; `shard_caps` reports the confinement and a device without it takes
   no shards. Tests: a sent tree whose `scripts/test`, conftest and plugin each try to read `~/.ssh`, write outside the
   scratch directory, open a non-loopback socket and set `DEV_TEST_WRITE_DENY=0`, all refused. This slice is a hard
   prerequisite of slices 7 to 9.
7. **Shard description, planner, tree transport.** `scripts/test plan` (CI calls it, so `ci_shards.py list` goes); test-id
   split for the two long files; a delta against `base` fetched by SHA only, the tree hash over the transported file set
   replacing the tar checksum, the 24 MiB cap kept for the delta; the shard timeout of twice the estimate and the
   yield-to-lease rule.
8. **Signed evidence and the acceptance rule.** The signed record without a member key, the `remote` kind with its expiry,
   the sentinel, corroboration, the canary, quarantine by signer with its expiry, `shard_done` carrying the digest;
   `testruns.verified` refuses `remote`.
9. **The pool scheduler.** `scripts/test changed` shards over allowed members, runs a late shard itself and reports
   `unconfirmed` passes. First measured speedup, on two or more devices of one platform class, recorded in
   `docs/test-farm.md` with its command.

## 11. Decisions made

The owner's answers, recorded. Options were ranked by what is correct.

1. **Observed edges first, with a prototype.** (a): the recorder and observed edges are built before selection narrows
   authoritatively, but the recorder is first prototyped on a subset of test files and its selectivity measured before
   anything relies on it (slice 4). The recording run is the lead's own instrumented run, not the pool (4.5, 6.5).
2. **CI scope: narrow at once.** A development push already starts no full run (`de1df9fc7`); the pull request,
   `promote/<date>` and nightly runs stay full. Nothing waits on a window; the ledger is per commit range (section 5, items
   5 and 6) and reports how far to trust the selection.
3. **Consent to run another device's tests: (b).** A separate yes per device, never inside adding a device, off under
   `open` (section 9).
4. **A pass on another platform: (a).** It replaces nothing; the Linux map is never used to deselect a Mac test, and speed
   comes from members of the sender's platform class (4.5, 6.2).
5. **A selection that reaches every test: (a).** It is handed to GitHub (pull request, promote, nightly) and the floor runs
   now; the main session does not shard the whole suite over the pool.

## 12. Review log

A non-author review of `ec3940fa3` returned REJECT as written and ACCEPT once the edits below landed. Each was checked
against the tree (`264b54056`) before it was applied; counts the review made and this revision did not re-run are marked.

1. **The per-push run does not exist (critical).** Verified: `de1df9fc7` (2026-10-19, owner order) adds
   `if: github.event_name != 'push' || github.ref == 'refs/heads/main'` to the `shards` job, so a development push runs no
   shard; HANDOFF says CI has never completed a sharded run. The ledger, the second source, the observed-map source and the
   200-push window assumed it. Replaced by a per-range ledger over the pull request, promote and nightly runs (section 5,
   items 5 and 6; slice 2), the lead's own instrumented run as the map source (4.5), the window as commits covered per
   change class, and the stale AGENTS.md text listed under follow-ups.
2. **A lister is not selected by its readers only (high).** Verified: `affected.select(docs/node.md)` returns 7 files
   (`test_land_review_policy` and six tree-wide guards) and not `test_documented_env_vars`, which globs `docs/*.md` and
   rglobs the source tree. Any change under a lister's directory selects it (4.1). The 66 test files combining a repo-root
   constant with a lister are the review's count.
3. **Exact reference alone drops references (high).** Verified in code: `test_redact_audit` builds
   `ROOT / "scripts" / "audit-pii"` and `test_budgets_skip` builds `... / "scripts" / "hooks" / "budgets"`. The extractor
   folds such chains and treats an unfoldable path as opaque (4.1). The 259 and about 96 counts are the review's.
4. **The hub is mechanical (high).** `scripts/test` names six `-p` plugins, and `affected_rules.infrastructure()` reads them
   by regular expression; conftest has the autouse fixtures and function-level imports listed in 4.4 and reads
   `tests/heavy-modules.txt` and `tests/seatbelt-modules.txt` (4.4).
5. **A stored record is not trusted as complete (high).** A plain `env=` search finds 194 test files; the review's 157 is
   the subset passing it to a subprocess call. The record carries `child unobserved`, a platform-branch rule keeps the
   Linux map from removing tests (4.5).
6. **Confinement (critical).** Verified: `shard_run.py` only strips environment names (`DROPPED`) and runs `scripts/test`
   from the sent tree; `scripts/testwritedeny.py` is allow-default, sits in the tree and is switched off by
   `DEV_TEST_WRITE_DENY=0`. The installed executor applies a deny-default profile (6.3 step 4); slice 6 is a hard
   prerequisite of slices 7 to 9.
7. **Member-supplied keys (high).** The result carries no key or digest, the sender computes them, the runner and hub
   digests are checked, `remote` entries expire (6.4).
8. **Sentinel, confirmation, uncommitted trees, quarantine (high).** Stated as the review put them (6.5): the sentinel
   catches only an honest executor that ran nothing; confirmation by a second member halves the speedup of a stored pass; an
   uncommitted tree has no GitHub result; quarantine and demotion expire.
9. **Starving the member, tree hash, `base` (high).** Yield to GPU, benchmark and foreground leases and kill the shard
   group; timeout twice the estimate; the tree hash over the transported file set (`shard_tree.SHIPPED` ships a subset);
   `base` fetched by SHA only with none of the member's git credentials (6.1, 6.3).
10. **Fixed-cost limit (high).** The cold bound and the member bound are under 5 s; the observed map and the delta have
    bounds (section 8).
11. **Sample size (high).** Zero misses in 200 events bounds the rate only below about 1.5 percent; a miss is judged
    against the last green so a repeated red does not hide; evidence skips are counted; an extractor digest change resets
    the counts (section 5, items 5 and 6).
12. **Measurement corrections.** `a0ab8321c` leaves 5 Rust paths unmapped (the changed Rust paths in that commit are six; 5
    unmapped is the review's count); the number of test files that start the node is 170 (re-counted here over all 920
    files with the `testnode.needed` rule: closure plus conftests, `node_kit`, `runner_binary` and the node environment
    variable); the 779 transitive figure is withdrawn and the review's 868 over all edges at depth
    50 stands in for it (not re-run here).

Follow-ups outside this document (nothing here edits a rule file):

- AGENTS.md section 4, "Landing" step 3 and Read-this-first item 20 said GitHub runs the full suite on every pushed commit.
  Both were stale after `de1df9fc7`; `bcb74b2a4` (merged into this branch) has rewritten them to the runs that exist, and
  this document matches the rewrite.
- `docs/development.md` line 189 ("The full suite runs on GitHub for every pushed commit") is still stale and is to be
  rewritten to a pull request, `main` and the nightly schedule.
