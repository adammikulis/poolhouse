# Test selection by reach, and shards on pooled devices

Status: design, 2026-10-19. Nothing here is built. Claims carry a tag: **[measured]** (commands in section 3),
**[code]** read in this tree (`0.3dev` at `76817ce35`), **[plan]** a proposal. The owner ordered this redesign: tests are
selected by what a change reaches, from an import and data dependency graph built once and cached by tree hash, and run
in shards on the pool's devices when a pool exists.

## 1. Summary

- Selection today is a depth-1 import lookup plus string-matching rules. On nine recent commits it picked 7 to 391 test
  files unrelated to what the change reaches: 343 where the real reach is 2, 191 for a regenerated JSON file, and 7 to 26
  (or "unmapped") for the three Rust commits where 133 to 135 files can start the node. A call costs 4 to 20 s and caches
  nothing. Reuse does not help across code changes: all 920 test files carry the whole-tree digest in their manifest.
- A static graph cannot be both sound and selective here. Followed transitively it reaches 779 of 920 test files from a
  typical `src/` change at module level and 845 at name level, because of a few hubs (the conftest closure alone holds
  715 of 2,246 modules, plus module-level registry tables and package `__init__` files).
- So four layers: a typed static floor (exact edges, one parse per file content, cached by tree hash); observed edges
  recorded by GitHub's instrumented run and keyed by content hash (transitive precision); a hub rule (a change to code
  every session runs selects everything and goes to GitHub); evidence (a selected test is skipped only when a pass exists
  for the same dependency digest).
- Soundness comes from three rules (an unknown edge selects, a skip needs a positive proof, the selector's own files
  select everything) and one check on every push: GitHub's full run against the selection, each miss fixed in the same turn.
- A shard is a tree hash, a file list, a flags digest and a nonce. The member fetches that tree, verifies its git tree
  hash, runs it under its own broker lease and returns a signed result. A device's result is advice: a pass serves the
  inner loop, is stored as skip evidence only when corroborated, and never counts for landing, task verification or
  release; a failure is always accepted, because one re-run verifies it.
- Nine build slices, each landable alone with a bound on its fixed cost. No new human step: the one existing consent per
  device is folded into the approval a person already gives when adding a device (decision 3).

## 2. What exists today

### 2.1 The mechanisms

| Mechanism | Where | What it does |
| --- | --- | --- |
| An agent names files | AGENTS.md section 5; `scripts/testscope.py` | An agent may run any test files it names, never the whole suite |
| `scripts/test quick` | `scripts/test`, `scripts/affected.py` | `affected.select` files, then testmon's per-checkout map for the rest; an unmapped path runs the full tier |
| `scripts/land` | `scripts/land_exec.py` | `affected.select` files plus `scripts/test gate`; an unmapped path is ignored (GitHub runs everything) |
| Reuse | `scripts/testreuse_*.py`, docs/test-reuse.md | A file's stored pass is reused when its key and manifest still hold |
| GitHub `ci` | `.github/workflows/ci.yml`, `scripts/ci_shards.py` | Every test file on every push, in 8 shards balanced by `tests/ci-durations.json`; junit uploaded |
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
| `258dc2826` | 10 `src/`, 1 script, 2 tests | 391 (5,244) | 180 + 3 | 20 / 56 / 133 | 779 |
| `a0ab8321c` | 6 Rust, 2 tests | 10 (31), 3 Rust paths unmapped | 0 + 2 | none | 0 |
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
  ignores them), yet what a Rust change reaches is known: 135 test files can start the node (`testnode.needed`), 1,934 CI s,
  a quarter of the suite.
- **Depth 1 hides indirection.** `3d48da3f4` changes `scripts/testslots.py`; `tests/test_test_kernel_isolation.py`
  imports `test_kernel_attestation` and `test_kernel_lifecycle`, which import `testslots`, and was not picked.
- **Static transitive reach is unusable.** The module-level column is 778 to 779 whenever a hub module changes
  (`258dc2826`, `2d412f828`); no static rule separates the tests that execute the changed code from those that import it.
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
can go: 135 start the node binary, 45 drive playwright, 17 run `cargo`, 82 carry the `redteam` marker, 96 name a lease or
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
| spawn | a path, `./name`, `python -m` module or `[project.scripts]` name that a file runs | Exact reference only; the bare word `status` is not a reference |
| data | a path string naming a tracked file; a directory listing (`glob`, `iterdir`, `listdir`, `os.walk`, `ls-files`) | A listing depends on the directory's membership: a file added, removed or renamed under it selects the lister; an edit selects only direct readers |
| fixture | a test's parameter or `usefixtures` name to the fixture of that name; `autouse` fixtures and `pytest_*` hooks to every test | Name match over the conftest chain and imported helpers |
| rust | any file in the Cargo workspace members (`testnode.MEMBERS`) | Selects every test file `testnode.needed` returns (135 now) and the files that run `cargo` |
| config | `pyproject.toml`, `tests/conftest.py`, rises in `budgets.json`, the selector's own files | Selects everything |

What an extractor cannot resolve (a computed `import_module`, `getattr(module, variable)`, a path built at run time, an
unparseable file) makes that file **opaque**: it gets an edge to every file of the category it could reach (all of
`src/`, or the directory), never to nothing. The count of opaque files is reported and only falls.

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
   alone. It is module-level on purpose: a name-level floor is not reliably smaller (3.4).
3. **Observed reach (4.5).** Tests whose recorded execution touched any changed symbol, through any depth.
4. **No record.** A test with no valid observation (new, never passed on an instrumented run, or its observed edges name
   nodes that no longer exist) takes the floor plus the transitive reach over import edges only.
5. **Hub.** A changed node reachable from the session hub (4.4) selects the tests that use that part of the hub.
6. **Evidence (4.6).** A selected test with a valid pass is not run.

`scripts/test select --explain` prints the reason per test, so a reader sees why any file was chosen or left out.

### 4.4 The session hub

The hub is what every test session executes: the module body of `tests/conftest.py` and its helpers, the `autouse`
fixtures, the `-p` plugins (`scripts/testslots_pytest.py`, `testreuse_plugin.py`, `testagentenv_pytest.py`) and what they
import at module level. A change inside the hub selects every test file, and an agent does not run that locally
(AGENTS.md keeps the whole suite as GitHub's job): it gets one line (`this change reaches every test; the push starts
GitHub's full run; these N files ran now`) and runs the floor.

The hub is a debt to shrink: `poolhouse.testing.fakes` pulls 339 `src/` modules into the session. Of the 10 hub commits in
the last 40, 5 reached it only through such library modules (`node_launch`, `sandbox/*`) and 5 through infrastructure
(`testslots*`, `testreuse_plugin`). Fixtures that import their library lazily move the first group out; a gate counts the
hub's modules and lets the number only fall (slice 3).

### 4.5 Observed edges

A static graph cannot say which of the 778 to 779 transitive importers execute the changed function; a recorder can. During a
run the pytest plugin records, per test file, which top-level symbols executed (`sys.monitoring` function-start events,
re-armed at each file; child Python processes record through an environment variable and a `usercustomize` in the run's
scratch site directory) and which files it read (the audit hook of `testreuse_plugin.py` already does). The record is keyed
by symbol hash, so it holds for any tree in which those symbols hash the same, and is kept per platform class (a Linux map
says nothing about Seatbelt code). Only the runner that executed the tests writes it. Maps are accepted from GitHub's full
run of a commit on the development branch (the run that exists, instrumented; fetched the way `ci_watch.py` reads runs)
and from this device's own runs, never from a member's shard (6.5). It replaces testmon: no per-checkout `.testmondata`,
no background `record` run.

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
5. **GitHub's full run is the cross-check.** It already runs every test file on every push (8 shards, the required
   check). Slice 2 compares each push's results with the selection computed for that diff. A **miss** is a test that
   failed on commit C, passed on its parent and was not selected for C. Each miss is fixed in the turn it is found: the
   missing edge or opaque flag is added and a fixture replaying the diff is the regression test. The count is a budget
   key (`selection-misses`) that only falls.
6. **Narrowing CI waits for evidence.** GitHub keeps running everything on every push until a window of pushes (decision
   2) shows no misses; a miss resets the window. Then a push runs its selection, with the full run on a schedule and on the
   `promote/<date>` snapshot before `main`.

What stays unsound, and is named: behaviour that depends on a clock, the network or an environment value the manifest does
not name; a path taken only on a platform the observing run was not; a dispatch through a registry the recorder did not
exercise. Observed edges can go stale; item 5 is the check on that, beside the existing canary (`CANARY_RATE` 0.05).

## 6. Shards on pooled devices

### 6.1 The description

A shard is a request to run named files at an exact tree. Fields: `tree` (the git tree hash of the sender's working tree)
and `base` (a commit the member can fetch, `origin/<development>`, so the upload is a delta); `files` (up to 200 names
`tests/NAME.py`, as `shard_spec.py` allows now, or `file::test` ids for a split file); `tier` and `flags` (the
`testreuse_key.argument_digest` of the pytest arguments); `selection` (the digest of the selection the files came from);
`nonce` (128 random bits per shard); `timeout_s`; `needs` (platform class, Python 3.13, node binary built or not). No
command line, environment or working directory travels; the node builds the one command, as now.

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
- A shard not finished by its deadline is run by the sender's own broker; the sender never waits on a device.

### 6.3 How a member runs it

1. `shard_caps` as now (consent, platform, Python, free slots), plus `node_ready` and the trees the member already holds.
2. The sender sends the delta of its working tree against `base` (changed files only, the limits of `shard_tree.py`), or
   nothing when the member can fetch `tree` from origin.
3. The member builds the tree in a scratch checkout and computes its git tree hash; a mismatch with `tree` refuses the
   shard. The hash, not a checksum of an opaque tar, ties a result to a tree.
4. The executor takes CPU slots from the node's lease table (half the cores, class background) and runs
   `scripts/test all FILES --no-reuse --junitxml=OWN` through the member's own broker admission, so a shard queues behind
   and yields to the device's own work. A shard never takes a GPU or model lease.
5. It returns the signed result (6.4) and removes the scratch checkout.

### 6.4 Results as evidence

A result is one record per file, `{file, lookup_key, manifest_digest, tree, junit_sha256, counts, platform, python,
runner_digest, started, ended, shard_id, nonce}`, plus failing test ids with messages, all signed by the member node's
key. The node also writes `shard_done` to the pool board's audit log with the result digest (today only the exit code),
so the attestation is a signed, replicated entry too.

The sender's runner checks the signature against the pool's certificate for that device, that the device is still an
active member, the nonce, the tree, the platform and the counts (at least one test collected and passed, none skipped,
the junit's count). Only then does it write an entry of kind `remote` into the evidence store. **The remote device never
writes the store; the sender's runner does, after verifying.** `Store.hit` takes a `remote` entry only under the rule in
6.5, and `testruns.verified`, which the task board reads, accepts only the local `pass` kind, so a remote result never
satisfies a task checkpoint or submission.

### 6.5 What a device is not trusted for

A member is trusted to run what it is given and to say how it went, and to nothing else. A compromised or malicious member
can return a pass without running, run altered tests, report fewer tests than ran, answer for a tree it did not build,
replay an old result, claim a platform it is not, or withhold a failure. So:

- **Never trusted for:** the graph or the execution map (built or fetched by the sender, never from a member); the choice
  of files (the member cannot drop one); anything that gates landing, a task checkpoint, a release or `main`; durations
  for planning (clipped to three times the sender's own value, as `testhistory` clips samples); the store (only the
  sender's runner writes it).
- **A failure is always accepted.** The runner re-runs the failing file on the sender before reporting it, so a false
  failure costs one file's run.
- **A pass is accepted by use.**
  1. *Inner loop:* a verified pass from an allowed member is shown (`passed on Studio Mac, signed`) and lets the agent
     push. It is not stored. A wrong pass costs a red GitHub run, which every push already gets.
  2. *Stored as skip evidence (`remote`):* only when a second source confirms it: another member (a different
     fingerprint) or GitHub's result for the same tree and file. Until then the pass is `unconfirmed` and prints so. A
     sample of stored `remote` entries (rate 0.05, `CANARY_RATE`) is re-run on the sender.
  3. *Authority (landing, task evidence, release):* never from a device; only the sender's own execution and GitHub's run.
- **A sentinel in every shard.** The sender adds two generated files to the delta: a test that must fail with a message
  derived from the nonce and the tree, and one that must pass and print a digest of the shard's own files. A result
  without that failure and that value is refused and counted against the device. It catches a runner that ran nothing,
  by mistake (an executor that silently runs zero tests) or by lazy design; a member that reads the sentinel and
  fabricates its output, or runs it honestly and lies about the rest, is what confirmation is for.
- **Quarantine by signer.** A canary or GitHub mismatch for a `remote` entry writes an `incident` row (the existing
  mechanism), marks the device `suspect`, refuses its passes for skip evidence and demotes every `remote` entry it signed
  until each is confirmed again.

## 7. Attack first

Preliminary, by the author. AGENTS.md section 1 requires an agent other than the author to break this before it is built;
that pass is the first task of slice 6 and blocks slices 6 to 8. One entry per case in that list.

- **A copied or cloned device or pool.** A clone with the member's key answers under its fingerprint: a signature proves
  the key, not the machine, so a clone's pass is confirmed by a second source before it is stored. A copy of the pool
  record elsewhere is a different pool; its results name another pool id and are dropped.
- **Replay.** The nonce is fresh per shard and inside the signed record; a result for another shard, tree or nonce fails.
- **A forged or below-threshold signature.** A forged signature fails the certificate check. One device's signature is
  below the threshold for skip evidence (two sources) and serves only the inner loop.
- **A compromised or malicious member.** The cases of 6.5. Worst case: a false pass that one agent sees until the push's
  GitHub run, a false failure (one re-run), a wasted shard (run by the sender at the deadline), a skewed duration
  (clipped). It reads the tree it is sent, the repository's own non-ignored files; a delta carrying a file a staged-file
  hook refuses (a name, a secret) is not sent.
- **A stolen phone or key.** A stolen device key is a compromised member: removing the member refuses it at its next
  request (the node does this) and the signer quarantine demotes what it signed. A stolen person key can add a member
  only by the charter's thresholds, unchanged here; the new member starts unconfirmed.
- **A malicious agent.** It holds `shard_call` for the fixed command and cannot call `shard_consent` or write evidence
  (only the runner process does). A hostile tree sent to a member meets that member's allowed list and consent, as now.
- **An agent compromised without knowing it.** "Tests passed on the other device, skip them" in a board message or tool
  result changes nothing: selection reads signed records and the store, never board text, and no gate depends on a remote
  pass.
- **A downgrade (secure to open).** A device that entered under `open` (its recorded enrolment) is never a trusted runner
  for skip evidence; a pool's join policy never changes in place (glossary, "reissue").
- **Approval fatigue.** No prompt is added. Repeated shard requests meet the node's caps (two running, six open, two
  uploading per sender) and cannot become a repeated question.
- **Takeover by whoever is merely present.** Consent stays a person's act on a device, wherever decision 3 places it, and
  nothing here lowers it. Running `--on` at the sender needs a registered session and the `shard_call` grant.
- **Attacks on selection.** A change that evades the extractors makes the file opaque and selects wider (rule 1); a change
  to the selector selects everything (rule 3); a forged cache entry is recomputed from its blobs and otherwise caught by
  the push-time cross-check; a write by the same user is the limit docs/test-reuse.md already states for the store.

## 8. Fixed-cost bounds

Measured as CPU seconds, as `tests/test_runner_overhead.py` does, so they hold on a busy machine. Each bound is a test
that fails when exceeded and each slice names its own.

| Cost | Today [measured] | Bound |
| --- | --- | --- |
| `scripts/test select`, warm facts, tip tree (tree hash 0.3 s included) | 3.9 to 20.0 s per call | 1.0 s |
| the same with every fact missing (a new checkout) | the same 4 to 20 s, every call | 6 s once, then warm |
| shard planning, 920 files, 8 devices | n/a | 0.2 s |
| member fixed cost per shard (fetch, unpack, tree hash, collection) | unmeasured; `SETUP_S` assumes 3 s | 5 s above the tests, printed as a phase; a shard is sent only if it is at least 4 times that |
| touch recording on an instrumented run | n/a | 3 percent of the run's wall; zero when off |
| facts file | `import-facts.json` is 0.97 MB for about 3,000 entries | 12 MB, pruned at two weeks |

Wall time is the sender's `select`, plus the longest shard, plus its fixed cost. For the nine commits the floor and own
files are 0 to 2,618 CI seconds (median 25) against 7,933 for the suite, about 1,950 for a Rust commit with the node
tests added; on 4 devices of one platform class the longest shard is about a quarter of those when the files balance.
This is arithmetic from `tests/ci-durations.json`, not a measured speedup: nothing has run on more than one device.

## 9. Onboarding impact

No new human step. An agent runs `scripts/test changed` [plan]: the selection, minus valid passes, sharded over the
sender and the allowed members when any answer, run locally otherwise; `--on` stays for naming a platform. Setup on a
member is what `python -m poolhouse.device_setup --yes` already does, plus the one consent (decision 3). The Python 3.13
and node-binary checks go into `shard_caps`, so a device that cannot take shards says why in a sentence an agent relays
("Studio Mac did not take the tests: it has not been allowed to"); a person reads "tests passed on 3 devices, 1 unconfirmed".

## 10. Build plan

Each slice lands alone, in order, with tests watched failing first, its bound from section 8, and the deletion of what it
replaces. Slices 1 and 2 change nothing about what runs; slice 2 is the soundness instrument and lands before anything
narrows.

1. **One parse, cached by tree hash.** `scripts/reach_facts.py` (facts per blob, assembled per tree) behind the existing
   `affected.select`, which keeps its output; a differential test on synthetic trees and the nine commits. Removes the
   duplicate parses. Bound: warm select 1 s, from 4 to 20 s on every `quick`, `land` plan and gate-scope check.
2. **The miss ledger, in shadow.** `scripts/ci_watch.py` reads each pushed commit's junit and the selection computed for
   its diff (today's selector first) and records misses and the `selection-misses` count: the baseline miss rate of the
   selector in use now.
3. **Exact edges and the hub.** Typed edges (4.1) replace the bare-name and lister rules; the Rust edge; the generated-file
   rule limited to tests that run the generator; a reason and a superset for every `unmapped`; the hub rule with its
   one-line answer; a hub-size budget. Expected on the nine commits: 343 to 2, 191 to 4, the Rust commits from 7 to 26
   files up to about 135 to 145, the correct reach. `quick` and `land` switch after slice 2 shows the effect.
4. **Observed edges.** The recorder plugin (off by default, on in GitHub's run), the symbol-hash store, the selector
   reading it; `testmon` and `scripts/test record` deleted. Bound: 3 percent. Gate: over the window of decision 2 the
   combined selection misses nothing the full run found.
5. **Selection is reuse.** The dependency digest replaces the whole-tree digest in the manifest of tests with no opaque
   spawn; `scripts/test changed` becomes the agent's one command. Measure: reuse hit rate across a `src/` change (0 of 920
   today).
6. **Shard description, planner, tree transport.** The attack-first pass by another agent comes first. `scripts/test plan`
   (CI calls it, so `ci_shards.py list` goes); test-id split for the two long files; a delta against `base`, the tree-hash
   check replacing the tar checksum and the 24 MiB cap.
7. **Signed evidence and the acceptance rule.** The signed record, the `remote` kind, the sentinel, corroboration, the
   canary, quarantine by signer, `shard_done` carrying the digest; `testruns.verified` refuses `remote`.
8. **The pool scheduler.** `scripts/test changed` shards over allowed members, runs a late shard itself and reports
   `unconfirmed` passes. First measured speedup, on two or more devices of one platform class, recorded in
   `docs/test-farm.md` with its command.
9. **Narrow CI.** After the window of decision 2 a push runs its selection; the full run moves to a schedule and to the
   `promote/<date>` snapshot; the ledger stays on.

## 11. Open decisions

Options are ranked by what is correct; effort and cost are stated beside each.

1. **Observed edges before narrowing, or static first?** (a) Build the recorder and observed edges (slice 4) before
   anything narrows authoritatively: correct, since static transitive reach is unusable; costs one instrumented run and
   about 3 percent of its wall. (b) Ship the exact floor (slices 1 to 3) and decide from the ledger: less work, and a miss
   rate the present depth-1 selector already has.
2. **Does CI keep running every test on every push?** (a) Keep it until a window of pushes shows no misses (proposed: 200
   consecutive), then narrow, with the full run on a schedule and on the snapshot before `main`: safest, costs GitHub
   minutes until then. (b) Narrow at once and rely on the ledger: a miss surfaces at the next scheduled full run.
3. **Where consent to run tests on another device is given.** (a) Inside the approval a person already gives when adding a
   device to a pool with one admin person, in plain words ("this device will also run tests for your other devices"): no
   extra step, still an explicit tap; a pool that joins under `open` has no such approval, so consent stays off there.
   (b) The separate typed yes per device (`python -m poolhouse.testfarm.consent on`): one more step per device.
4. **What a pass on another platform replaces.** (a) Nothing: extra coverage; speed comes from members of the sender's
   platform class. (b) A listed set of platform-neutral tests (the required GitHub check is Linux), after measuring the
   agreement between platforms.
5. **A selection that reaches every test.** (a) Handed to GitHub, with the floor run now: the rule is unchanged. (b) The
   main session shards the whole suite over the pool, if the owner says that is not the local run the rule forbids.
