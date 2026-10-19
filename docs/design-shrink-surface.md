# Shrinking the surface

A design for taking everything that is not on the first release's path out of the shipped build, in slices that each land
alone. Nothing here is built or deleted yet. The release is the one `HANDOFF.md` ("Release cut") and
`docs/release-readiness.md` describe: a person on the desktop or phone app, a developer using the library (`ph.pool`,
`board`, `leases`, `test`, `models`, `serve`, `client`, `hub`, `decide`), and existing harnesses driven through hooks and
settings.

Contents: [1 What the inventory found](#1-what-the-inventory-found) | [2 Inventory](#2-inventory) |
[3 Classification and mechanism](#3-classification-and-mechanism) | [4 What each slice removes](#4-what-each-slice-removes) |
[5 Slices](#5-slices-safest-first) | [6 Security and red-team](#6-security-and-red-team) |
[7 Rules that change](#7-rules-that-change) | [8 Decisions](#8-decisions) | [9 Review](#9-review)

## 1 What the inventory found

1. **The Box enforcer, decoys, two-box layering and lab mode have no code in the tree.** `HANDOFF.md` and
   `docs/release-readiness.md` cite `docs/strands-box.md` "on branch `docs/strands-box-design`"; no such file or branch is in
   the repository (no ref and no commit in any history carries it), and no source, script, test or app file implements them. The
   only "decoy" is the sentinel's decoy endpoint (`src/poolhouse/serve/decoy.py`), a different thing that is a security
   control. There is nothing to isolate for that line of the release cut; the dangling pointers are the only fix (slice 1).
2. **The import graph cannot tell what is release code.** Walking imports from every console script, the app, the facade
   and the frozen launcher reaches 1017 of 1120 modules (every console script is a root). Walking from the release commands, the facade and the app alone reaches 992. The release
   daemon, `doctor`, `mcp`, `workspace` and `decide_cli` import the non-release packages, so reachability says nothing.
   The useful output is the list of **cut edges**: the few imports from release code into a candidate package (section 5).
   After cutting every edge in this document, 823 modules stay reachable from the release roots.
3. **About 240 modules (21 percent) and 47,600 lines (25 percent of 193,837) can leave the shipped build** in six
   slices, and nothing on the release path is lost: bench, world, gym, train, gguf, ingest, sources, scrape, web, datasheet,
   draft, speech, walk, surface, redteam and testing.
4. **Budget debt is concentrated there.** Of the 1,198 sites in `budgets.json`, 442 (37 percent) sit in bench (146),
   ingest (99), train (70), world (60), speech (25) and their small neighbours. gym, walk and surface carry none.
5. **The 486 unmeasured modules are mostly release code**: workspace 79, fleet 74, serve 50, hub 18. Only 77 (16 percent)
   are in the candidates. Shrinking does not fix `scripts/mutate --unmeasured`; writing tests for the release path does.
6. **Tests are a small share of the saving.** The six slices hold about 1,130 of 12,573 test functions (9 percent, a
   count of `def test_` lines) and 14,100 of 212,936 test lines (7 percent). `tests/ci-durations.json` puts them at
   about 190 of 7,798 seconds. The large CI costs are elsewhere: `graph` tests are 1,107 seconds, `workspace` 1,268,
   `activity` 584, and each shard installs torch (twice: the `test` extra and a CPU wheel).
7. **The gates scan `src/poolhouse` only.** 19 gate scripts name that path, and 65 tests walk it. Code moved to a new
   top-level directory leaves every gate (subprocess spawns, urlopen, http servers, home paths) without a word. Isolation
   therefore has to keep the code where the gates look (section 3).
8. **Neither `tests/test_layers.py` nor `tests/test_wiring.py` requires any package to exist.** They require that a package
   be placed in a layer and be wired (imported, a console script, or named in `STANDALONE`). Removing a package leaves a
   stale name in `LAYERS` (silent) and fails `test_standalone_names_only_real_pieces` for a stale `STANDALONE` entry, and
   it fails `test_known_holds_nothing_already_fixed` once a `KNOWN` edge is gone (bench removes four of the thirteen).
   Isolating needs one new layer and one wiring exemption (section 7).

How it was measured: an `ast` import graph of `src/poolhouse` at tree `76817ce35` (absolute and relative imports, function-local
imports, and dotted `poolhouse.x.y` string constants for lazy loads), roots from `pyproject.toml` console scripts and
entry points, the Rust and packaging files that name Python modules, and the frozen launcher; test files counted by the
same naming rule `scripts/mutate --unmeasured` uses; budgets from `scripts/budgets --json`; red-team rows from
`docs/redteam/coverage.json`. The script is not committed. Limits: modules loaded by file path (hooks) look unreferenced;
"tests that go" are by naming, and each test file is read by the worker before it is removed.

## 2 Inventory

### 2.1 `src/poolhouse` (1,119 modules, 193,837 lines; 48 directories and 121 single-file modules)

`Unm` is modules no test file names; `Tests` is test files naming the package; `Debt` is its sites in `budgets.json`.
`Release` is how release code reaches it. Class: keep, isolate (leaves the shipped build, section 3), decide (section 8).

| Package | Mods | Lines | Unm | Tests | Debt | Release | Class |
|---|---:|---:|---:|---:|---:|---|---|
| fleet | 166 | 30,421 | 74 | 198 | 124 | direct (daemon, join, UI routes) | keep; carries the gym, knowledge and speech routes |
| serve | 99 | 20,809 | 50 | 141 | 121 | direct (`ph.serve`) | keep |
| bench | 45 | 14,600 | 13 | 48 | 146 | `doctor` 2 top-level, `fleet/ui` 10, `mcp` 8, `do` 5 | isolate |
| workspace | 111 | 13,716 | 79 | 122 | 0 | direct | keep |
| graph | 48 | 11,661 | 6 | 66 | 122 | `fleet` 17, `memory`, `reputation` | keep; internal split is decision D1 |
| train | 38 | 6,599 | 16 | 28 | 70 | `decide_cli` 3 top-level | isolate |
| ingest | 19 | 5,279 | 5 | 12 | 99 | strings only | isolate |
| world | 16 | 4,665 | 5 | 8 | 60 | `mcp` 1 local | isolate |
| gym | 37 | 3,440 | 7 | 20 | 0 | `fleet` 8 top-level (3 files, all gym routes) | isolate |
| testing | 19 | 2,881 | 7 | 111 | 3 | `train` 2, `redteam` 6; the suite's shared fakes | isolate (leaves the wheel only) |
| spec | 19 | 2,814 | 4 | 8 | 0 | `serve.mlx_tree*` 4 top-level | decide D4 |
| redteam | 24 | 2,655 | 10 | 18 | 0 | none (`python -m poolhouse.redteam`) | isolate (leaves the wheel only) |
| sources, scrape, web, datasheet | 21 | 3,952 | 6 | 27 | 25 | `ingest`, strings | isolate |
| speech | 8 | 1,323 | 4 | 6 | 25 | `fleet/api` 3, `fleet/daemon`, `fleet/remote` 2, `mcp` 3, `setup` | decide D2 |
| draft, walk, surface | 8 | 1,655 | 2 | 3 | 0 | none (console scripts only) | isolate |
| gguf, backend | 12 | 1,624 | 4 | 8 | 28 | `train` 12; `graph` 3 (backend) | gguf isolate, backend D1 |
| vision, entities, media | 15 | 1,668 | 6 | 12 | 16 | `graph`, `ingest` | keep (follows graph) |
| agent, chat, do, pi, coding, memory, reputation | 38 | 6,926 | 15 | 41 | 16 | `workspace` imports `chat`, `memory`, `reputation` | keep; D3 |
| hub, sentinel, guard, decide, net, client, board | 172 | 26,665 | 75 | 294 | 36 | direct (facade and security) | keep |
| sandbox, activity, taint, requests, inbox, coordination, redact, credentials, keystore, testfarm | 69 | 8,989 | 33 | 68 | 22 | direct | keep; `testfarm` D6 |
| everything else (`api`, `cli`, `contracts`, `data`, `jsonl`, `ui` and 114 single-file modules: `mcp`, `setup`, `doctor`, runtime, node, harness) | 135 | 21,495 | 65 | n/a | 74 | direct | keep |

The rows add to the tree totals (1,119 modules, 193,837 lines).

### 2.2 The other directories

| Directory | Files | Lines | On the release path | Class |
|---|---:|---:|---|---|
| `app/poolhouse-node` (Rust node: governance, phone, approvals, grants, leases, tasks, presence, `shard`) | 215 | 41,271 | yes; `shard` (812 lines) serves the experimental `remote-tests` feature | keep; `shard` follows D6 |
| `app/android`, `app/src-tauri`, `app/poolhouse-authority`, `app/native-*`, `phone-governance-jni` (lockfiles excluded) | 141 | 12,543 | yes (phone, desktop, approval) | keep |
| `scripts/` (dev tooling, never shipped; includes `hooks` and `gates`) | 196 | 27,984 | no | keep, except landing machinery and four one-shot scripts (slice 7) |
| `tests/` | 1,029 | 212,936 | no | edited with each slice |
| `docs/` (123 documents plus images and `redteam`) | 197 | 45,090 (about 8,000 are image and JSON) | no | edited with each slice; about 2,700 lines belong to candidate packages |
| `contracts/`, `patches/`, `packaging/`, `.github/` | 42 | 6,000 | yes (build, policy data) | keep; `contracts/recipes` and `extraction.schema.json` are checked per slice |

## 3 Classification and mechanism

**Delete: only what has no caller and no test.** `src/poolhouse/workspace/project_history.py` (36 lines) and
`src/poolhouse/bench/tree.py` (397 lines, leaves with bench). Everything else is called by a command or a route today, so it
is off the release path, not dead. `AGENTS.md` ("The gates") says code nothing calls is a gap to close and never a reason
to delete; an order to delete the off-path packages would need that sentence amended, so isolation is the route that
needs no rule change (decision D7 asks the owner for the later step).

**Isolate: a layer, not a directory.** Each isolated package stays at `src/poolhouse/<name>` so every gate, the 65
tests that walk the tree, the import paths in docs and tests and the red-team map keep working. What changes:

1. `tests/test_layers.py` gets an `experimental` layer above `tools` and `dev`. Nothing in a lower layer may import it
   (this is the rule that keeps the cuts cut), and packages inside it may import each other (bench, world, ingest and draft
   import each other today).
2. `pyproject.toml` excludes the experimental packages from the wheel and the frozen bundle
   (`packaging/frozen.py` collects the installed files, so the wheel exclusion covers it); a test compares the exclude
   list with the layer so they cannot differ.
3. The package's console script leaves `[project.scripts]`, `cli/reference.py` and `docs/commands.md`; it runs as
   `python -m poolhouse.<name>` from a checkout.
4. `tests/test_wiring.py` counts an experimental package as wired by `python -m` (section 7).
5. Its tests stay and keep running in the CI shards on pull requests, `main` and nightly; the list of an experimental
   package's test files is derived from the layer by `tests/test_experimental_tests_run_in_ci.py`, which fails when a
   file is in no shard (docs/test-execution.md, "CI shards").

The alternative, a top-level `experimental/` directory with its own distribution, isolates harder and costs more: the
gates and tests must first be taught a second root (19 gate files, the mutation sampler, 65 tests), which is a prerequisite
slice of its own. It is decision D8.

## 4 What each slice removes from the shipped build

Counts are for the packages and their fleet-side files. If the owner chooses deletion, the debt and red-team rows fall by
the figures shown; under isolation the same code stays under the gates and the counts do not move. Tests are by naming
(own plus at least half of what the file names); `edit` is test files that also name release code and change but stay.

| Slice | Modules | Lines | Unm | Test files / fns / lines (edit) | Debt | Red-team rows (uncov / partial / cov / n/a) | Console scripts |
|---|---:|---:|---:|---|---:|---|---|
| 1 dead code, dangling pointer | 1 | 36 | 0 | 0 | 0 | 0 | 0 |
| 2 redteam, walk, surface out of the wheel | 31 | 3,792 | 12 | 0 (stay) | 0 | 0 / 1 / 0 / 0 | 2 |
| 3 gym | 37 | 3,440 + 267 fleet + 1,148 UI | 7 | 16 / 84 / 1,914 (4) | 0 | 0 / 0 / 7 / 1 | 1 |
| 4 bench, world, draft | 62 | 19,783 | 18 | 27 / 429 / 5,978 (28) | 206 | 0 / 5 / 1 / 6 | 3 |
| 5 train, gguf, testing | 62 | 10,056 | 25 | 14 / 283 / 3,355 (11) | 87 | 0 / 1 / 0 / 3 | 4 |
| 6 ingest, sources, scrape, web, datasheet | 40 | 9,231 | 11 | 19 / 271 / 2,267 (22) | 124 | 6 / 2 / 6 / 2 | 1 |
| 7 landing machinery and one-shot scripts | 2 | 119 + 3,100 scripts | 0 | 10 test files, 2,653 lines | 0 | 0 | 0 |
| 8 speech (after D2) | 8 | 1,323 | 4 | 2 / 64 / 611 (4) | 25 | 3 / 4 / 0 / 0 | 1 |
| Total, slices 2 to 6 and 8 | 240 | 47,625 | 77 | 78 / 1,131 / 14,125 | 442 | 9 / 13 / 14 / 12 | 12 |

Against the tree: modules 21 percent, lines 25 percent, test lines 7 percent, debt sites 37 percent (442 of 1,198),
unmeasured 16 percent, uncovered red-team rows 9 of 52. Local CI seconds from `tests/ci-durations.json` (which
`HANDOFF.md` says is 3 to 10 times off for some files): 87, 68, 35 and 1 for slices 4, 5, 6 and 8, under 2.5 percent. The
`gates` job on a push scans the whole tree, so it shrinks with the lines; it was 163 seconds on `76817ce35`. The shards do
not run on development pushes (`ci.yml`: only `main` and pull requests), so a measured shard saving cannot be stated.
Not measured at all: wheel size, frozen bundle size, install time.

**What decides the large CI costs, outside these slices:** torch leaves the test install only when `backend` and the
graph tensor modules leave with `train` (D1), and `graph` tests (1,107 seconds) shrink only with the graph split.

## 5 Slices, safest first

Each slice is one branch, one commit series, landable alone with `scripts/test all <its files>` and the one gate batch
(`scripts/budgets`, `scripts/redteam_coverage.py --check`, `tests/test_layers.py`, `tests/test_wiring.py`). Under isolation
"leaves the build" means: added to the layer, the wheel exclude list and the experimental test list. `git rm` is the
deletion form, used only after D7.

**Slice 1: dead code and the dangling pointer.**
`git rm src/poolhouse/workspace/project_history.py`. Edit `HANDOFF.md` ("Not in the first release" drops the path
`docs/strands-box.md` and says the code is absent) and `docs/release-readiness.md` (the three places that cite the
file: 4.2, 4.4 and section 6) the same way; `docs/notes/release-backlog-2026-10-15.md` is a dated note and keeps its words. Changes: no
test, no budget. Check: `scripts/test all tests/test_wiring.py tests/test_layers.py tests/test_documents_name_current_files.py`.

**Slice 2: tools that never ship (redteam, walk, surface).** No cut edges; nothing imports them.
Leaves the build: `src/poolhouse/redteam/` (24), `src/poolhouse/walk/` (3), `src/poolhouse/surface/` (4).
Edit: `pyproject.toml` (remove `poolhouse-walk` and `poolhouse-surface`, add the wheel exclude), `cli/reference.py`,
`docs/commands.md`, `tests/test_layers.py` (new layer; `redteam` moves from `dev` into it), `tests/test_wiring.py`
(`STANDALONE` entries `redteam` and the new rule). Budgets: none. Red-team: the
red-team harness tests keep running (section 6). This slice also adds the mechanism every later slice uses.

**Slice 3: gym.** Cut: `fleet/workspace_routes.py` (`poolhouse.gym.catalog`, `poolhouse.gym.transport`, the
`gym_interpreters` import, the `poolhouse-gym` run branch at line 141) and the Gym view in the UI.
Leaves: `src/poolhouse/gym/` (37), `src/poolhouse/fleet/gym_interpreters.py`, `gym_recording_routes.py`, `gym_routes.py`,
`src/poolhouse/fleet/web/components/gym-*.html` (8), `docs/studio-gym.md`, `docs/drone-search.md`, `docs/live-worlds.md`.
Tests that go: `tests/test_gym_*.py` (19 files), `test_redteam_gym_worker.py`, `test_redteam_gym_http.py`,
`test_fleet_gym_boundaries.py` (22 files; the naming count in section 4 is 16 because some of these name no gym module).
Edited: `test_fleet_workspace_routes.py`, `test_serve_broker.py`. Also: `docs/redteam/coverage-map.toml` groups at lines 504 to 568 and 767 to 770 (7 covered rows
and their `count` pins), the `gym-*` extras in `pyproject.toml`, `docs/commands.md`, `docs/UI-WORKFLOWS.md`.
The attack tests are security-relevant: section 6.

**Slice 4: bench, world, draft.** Cut: `doctor.py` lines 18 and 19 (top-level; `doctor` stops importing the bench
measurement and keeps its checks), `do.py` (5 local imports), `fleet/ui.py` (10 local imports and the benchmark routes),
`mcp.py` (8 local imports: the bench and world tools leave the MCP tool list). `KNOWN` in `tests/test_layers.py` loses
`("doctor","bench")`, `("fleet","bench")`, `("ingest","bench")` and `("ingest","serve")`.
Leaves: `src/poolhouse/bench/` (45), `src/poolhouse/world/` (16), `src/poolhouse/draft.py`,
`src/poolhouse/fleet/web/components/benchmarks-view.html` and `fleet-benchmark.html`,
`docs/bench.md`, `docs/world.md`, `docs/model-ranking.md`. Tests that go: `tests/test_bench_*.py` (13 files),
`tests/test_graph_bench*.py` (12), `tests/test_world_*.py` (5), `tests/test_draft.py`; 28 files are edited (`test_mcp.py`, `test_do.py`, `test_fleet_ui.py`, `test_serve_profile.py`, `test_cli_help.py` and others).
Debt falls by 206 sites. The worker lists the exact test set with the naming script before the first `git rm`.

**Slice 5: train, gguf, testing.** Cut: `decide_cli.py` lines 37 to 39 (the train imports; `poolhouse-decide` keeps
ask, score, calibrate and compare; the fine-tune subcommand and `poolhouse-train-decider` leave), the fleet training view
(`training-view.html`, 379 lines) and its route. Leaves: `src/poolhouse/train/` (38), `src/poolhouse/gguf/` (5),
`src/poolhouse/testing/` (leaves the wheel only; it stays importable for tests), `poolhouse-train-run`, `-train-tools`,
`-train-decider`, `-suite`, the `train*` extras, `docs/training.md`, `docs/decision-model-integration-plan.md`,
`docs/medical-decision-model-plan.md`. Tests: `tests/test_train*.py` (12), `test_gguf.py`, `test_backend_parity.py`,
`test_studio_training_ui.py`, `test_accelerator_memory.py`. `backend` stays until D1 says the graph tensor modules go.

**Slice 6: ingest, sources, scrape, web, datasheet.** No release importer; only strings in `graph/store_cli.py`
messages that name `poolhouse-ingest`. Leaves: `src/poolhouse/ingest/` (19), `sources/` (9), `scrape/` (8), `web.py`,
`datasheet/` (3), `poolhouse-ingest`, `docs/ingest.md`, `docs/sources.md`, `docs/web-research.md`. Tests: `tests/test_ingest*.py`
(13), `test_scrape*.py` (3), `test_sources*.py` (5), `test_web*.py` (3), `test_datasheet_find.py`,
`test_studio_data_evaluate_browser.py`; 22 edited. Follow-up inside this slice: `net/pdfread.py`, `pdfrender.py`,
`pdftext.py`, `pdfchild.py`, `pdfrenderchild.py` (about 980 lines, the hostile-PDF child) lose their last caller;
they stay in `net` (security) until D5 decides.

**Slice 7: the old landing machinery (already ordered in `HANDOFF.md`, "Delete the old landing machinery in one piece").**
Security-adjacent, so it follows its attack-first pass. `git rm scripts/land scripts/land_exec.py scripts/land_git.py
scripts/land_review.py scripts/land_stale.py scripts/land_steps.py src/poolhouse/workspace/landing.py
src/poolhouse/workspace/landing_cli.py docs/landing.md`; tests `tests/land_support.py test_land.py test_land_concurrent.py
test_land_plan.py test_land_review_policy.py test_land_sealed.py test_land_stale.py test_land_trust.py
test_lander_ends_jobs.py test_subagent_stop_landed.py`. Edit callers: `workspace/cli.py` (the `landing_cli.TABLE`
entry), `workspace/github_release.py` (`eligible`), `runtime_unmerged.py`, `protectedwrites.py`, `scripts/hooks/stop_landed.py`,
`scripts/github-protection`, `test_model_tiers.py`, `test_runtime_unmerged.py`. Then the four one-shot scripts that only
`land_review.py` names: `scripts/rename_to_poolhouse.py`, `commit_types_chart.py`, `compare-harnesses`,
`prepare_medmcqa.py` with their tests (`test_rename_to_poolhouse.py`, `test_commit_types_chart.py`, `test_compare_harnesses.py`, `test_prepare_medmcqa.py`). The commit carries `Owner-order:`.

**Slice 8: speech (dropped; D2 keeps it).** Cut: `fleet/api.py` (3), `fleet/daemon.py`, `fleet/remote.py` (2), `mcp.py` (3), `setup.py`
(1). Leaves: `src/poolhouse/speech/` (8), `poolhouse-speech`, speech routes, `tests/test_speech.py`,
`test_speech_ffmpeg_flags.py`; edit `test_mcp.py`, `test_redteam_mcp.py`, `test_setup.py`, `test_fleet_daemon.py`.
Not built: speech stays in the release.

**Slice 9: the graph split (D1, after slices 1 to 6).** `graph` keeps the store, search and the UI the daemon and `memory`
use. `answers`, `community`, `judging`, `tidy`, `looking`, `tree`, `absorbing` and the tensor modules leave the shipped build
with `backend`, `vision` and their console scripts, by the layer mechanism of section 3. First step in its own commit: a
measurement of the cut edges (the 17 `fleet` imports of `graph`, `memory`, `reputation`, the three `backend` imports) with
the same `ast` walk as section 1, written into this section before any file moves; then the cut in `fleet` (the daemon's
knowledge view), the layer entries, the wheel excludes, the `KNOWN` and `STANDALONE` edits.
The `graph` tests that name the leaving modules stay in the CI shards; torch leaves the
release test install in this slice. Sizes are not yet measured (about 3,500 lines and most of the 122 debt sites by the
estimate in D1); `scripts/budgets` counts stay as they are under isolation.

## 6 Security and red-team

**No deletion of a guard or a red-team test is proposed.** The slices touch these, each listed so a reviewer sees them:

| Item | Slice | What is proposed |
|---|---|---|
| `tests/test_redteam_gym_worker.py`, `test_redteam_gym_http.py`, `test_fleet_gym_boundaries.py` and the 7 covered rows in `coverage-map.toml` | 3 | Isolation moves them with gym and they keep running. Deletion removes the attack surface they test (the spawn, route and recording paths of gym); no equivalent is needed because nothing is left to attack, and the commit carries `Owner-order:` because `scripts/hooks/weakened-assertions` reads it as a weakening. |
| `src/poolhouse/redteam/` and its test files (`tests/test_redteam_*.py`, 49 files) | 2 | Leaves the wheel only. The harness, scenarios and tests stay, still run, and still run in `redteam.yml`. |
| Red-team rows for ingest, sources, scrape, web, bench, speech | 4, 6, 8 | Same: moved with their package. `web` has 4 covered rows (private-address refusal, robots, redirect); they and `tests/test_web.py` move with it. |
| `net/pdf*` hostile-PDF children and `tests/test_pdf_engine.py`, `test_pdf_render.py` | 6 follow-up | Not removed. They stay in `net` until D5. |
| `scripts/land_review.py` policy, `test_land_review_policy.py`, `test_land_sealed.py`, `test_land_trust.py`, `stop_landed.py`, `protectedwrites.py` entries | 7 | Owner-ordered deletion of a gate. It needs the attack-first pass: what a standing reject currently stops, and that `pre-push` still refuses a standing reject of the pushed commit after the lander is gone. |
| Gates (`scripts/gates/*`) | all | Not touched. Isolation keeps code under every gate and under `redteam_coverage.py --check`, which is why section 3 does not move code out of `src/poolhouse`. |

Review order: slices 3 to 6 are not security-adjacent in what they add, but each removes or moves tests under
`tests/test_redteam_*`, so each gets one non-author reader for "did an attack test lose its target without the target
leaving". Slice 7 gets the full attack-first pass (replay, a downgrade of `pre-push`, a standing reject that no longer
stops a push).

## 7 Rules that change

- `tests/test_layers.py`: add the `experimental` layer; allow edges inside it; keep every edge into it from below a
  failure. Remove from `KNOWN` each edge a slice removes, in the same commit (the test fails on a stale entry).
- `tests/test_wiring.py`: `STANDALONE` gains a derived entry for every experimental package ("run as
  `python -m poolhouse.<name>` from a checkout"); `test_standalone_names_only_real_pieces` fails on a stale one, so each
  slice edits it.
- `AGENTS.md` "The gates" sentence "Code nothing calls is a gap to close, never a reason to delete": amended only if the
  owner orders deletion (D7).
- `docs/commands.md`, `cli/reference.py`, `docs/packages.md`, `README.md` ("What you can use it for" names graphs, training
  and benchmarks as the library's headline uses): rewritten to the release scope in the slice that removes the command.
- `budgets.json`: lowered with `scripts/budgets --update` in each slice that deletes; under isolation unchanged.

## 8 Decisions

Answered by the owner:

- **D1. Split the graph.** The store, search and UI stay; `answers`, `community`, `judging`, `tidy`, `looking`, `tree`,
  `absorbing` and the tensor modules (with `backend` and `vision`) leave the shipped build. Added as slice 9, after the
  packages in section 5.
- **D2. Keep speech.** Slice 8 is not built; the voice controls and MCP tools stay.
- **D3. Keep the local-model chat agent** (`chat`, `agent`, `do`, `memory`, `pi`, `coding`) for the release.
- **D6. Keep `testfarm` and `ph.test`.** `fleet/shard_exec`, `app/poolhouse-node/src/shard` and `scripts/test_on.py` stay;
  `testing` still leaves the wheel only (slice 5), and `testfarm` does not move.

Decided by the lead:

- **D4. Keep `spec`.** The MLX tree decoding stays, with `serve/mlx_tree*.py`.
- **D5. Keep the hostile-PDF engine in `net`**, with `tests/test_pdf_engine.py` and `test_pdf_render.py`.
- **D7. Isolate only.** Nothing is deleted after isolation; deleting would need the "Code nothing calls is a gap to close"
  sentence in `AGENTS.md` "The gates" amended first. Debt sites and CI-shard cost stay as in section 4 until then; the
  only `git rm` is slice 1.
- **D8. Isolation is a layer inside `src/poolhouse`** (section 3, option 1), not a top-level directory: all 19 gate scripts
  and 65 tests scan only `src/poolhouse`, so a second root would first need a slice that teaches them both.

## 9 Review

Security part (section 6): the review found no guard and no red-team test deleted or weakened by any slice proposed here.
Isolation keeps every red-team test, harness and gate running; the red-team files leave the wheel only (slice 2). The only
deletion left is slice 1 (`project_history.py`, no caller, no test), and D7 rules out the deletions section 6 listed for
slices 3 to 8. Slice 7 is the one change that removes a gate (the old landing machinery) and keeps its attack-first pass.
