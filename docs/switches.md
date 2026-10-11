# Switches: every variable and flag that changes what is safe or what is fast

Written 2026-10-11 after a run of macOS Keychain prompts: the only guard against touching the real keystore was a variable
that every test, rig and worker command had to remember to set. The rule that comes out of it: **safe by default; the real
behaviour only when the installed software runs on the real home; a missing variable never means "unsafe"**. A variable may
narrow what a process does. Whether a process may touch the machine is decided by what the process is (its state root, whether
it is pytest, whether it has a terminal), and `tests/test_documented_env_vars.py` keeps this file honest: every `POOLHOUSE_*`
name the shipped source reads has a row, every row's class is one of A to D, and the counts below match the rows.

Classes
- **A, opt-in safety**: safe only if someone sets it (or sets it for every process). The goal is no row left in A that a missing
  variable can turn unsafe; the column "structural" names what replaced the variable, or what is left.
- **B, off switch or unlock**: a variable, flag or file that disables or loosens a protection (a guard, a gate, a budget, a lease,
  a floor). The columns say who can set it, whether an agent can in its own shell, and whether it is logged.
- **C, plain config**: a path, a name, a limit, an address; no safety consequence.
- **D, opt-in speed-up**: faster when set and off by default. The decision column says why it stays off or why it was turned on.

Counts: A 33, B 35, C 117, D 6 (a variable name counts once, a row naming no variable counts once; a name is in one class only).

## A: opt-in safety

| Names | Read in | Unset means | Who sets it | Structural now |
| --- | --- | --- | --- | --- |
| `POOLHOUSE_NODE_LAN` | `poolhouse.lan` (node start, daemon bind, beacons) | was: a node on the LAN, multicast and a macOS Local Network prompt | tests/conftest.py, rigs | **fixed**: `lan.allowed()` is true only for the installed software on the account's own state root and not under pytest; `off` still narrows. A moved `POOLHOUSE_HOME`, a redirected `HOME` or a test closes it with nothing set |
| `POOLHOUSE_NOTIFY` | `poolhouse.desktop.which_way` | was: a real dialog (`osascript`, `notify-send`, `zenity`) from a test or a rig | conftest, `runtime_deploy` smoke | **fixed**: `which_way()` is `none` unless `lan.on_the_persons_machine()`; `console` and `off` still narrow |
| `POOLHOUSE_RUNTIME_ENSURE` | `scripts/hooks/workspace_hook.py` | was: every hook call in any tree could start a runtime build | conftest, smoke | **fixed**: the refresh runs only on the persons machine; `off` still narrows |
| `POOLHOUSE_NO_REAL_KEYSTORE`, `POOLHOUSE_NONINTERACTIVE` | `poolhouse.keystore`, `person`, hooks | the real OS keystore is asked and may prompt a person | conftest, `runtime_deploy`, workers | **another worker is making the keystore structural** (keystore.py, node_key.py); not touched here |
| `POOLHOUSE_HOME`, `POOLHOUSE_CACHE` | `poolhouse.home` | the real `~/.poolhouse` and `~/.cache/poolhouse` | `_no_machine_state` and `_real_home` (autouse, session) | structural under pytest: the redirect is autouse and the run fails if a file under the real root changed. A rig outside pytest (`tests/pool_check`) sets both itself |
| `POOLHOUSE_BENCH_CEILING`, `POOLHOUSE_BENCH_HOME`, `POOLHOUSE_BENCH_TRACE`, `POOLHOUSE_FIT_FILE`, `POOLHOUSE_INGEST_HOME`, `POOLHOUSE_JOBS_HOME`, `POOLHOUSE_KOKORO_MODEL`, `POOLHOUSE_KOKORO_VOICES`, `POOLHOUSE_LIMITS_FILE`, `POOLHOUSE_LLAMA_BUILD`, `POOLHOUSE_PIPER_VOICE`, `POOLHOUSE_PROFILES_FILE`, `POOLHOUSE_SEARCH`, `POOLHOUSE_TRAIN_CEILING`, `POOLHOUSE_TRAIN_HOME`, `POOLHOUSE_WEB_PROFILE`, `POOLHOUSE_WHISPER_CPP_MODEL`, `POOLHOUSE_CHECKOUTS`, `POOLHOUSE_RATES` | `poolhouse.home.OVERRIDES` and one reader each | the machine's own file (measurements, limits, a voice, a search engine) | a shell | structural under pytest: `_no_machine_state` deletes them for every test |
| `POOLHOUSE_AUTHORITY_FLOOR` | `poolhouse.authority` | gates follow the `dev` preset: delegated | conftest at import | structural under pytest (set at import, inherited by children); a rig must set it |
| `POOLHOUSE_GUARD_JUDGE` | `poolhouse.guard.native` | the guard may start its judge model | conftest at import | structural under pytest (`setdefault` at import) |
| `POOLHOUSE_STORE_MEMORY` | `poolhouse.graph.cypher` | the store sizes its buffer pool as a share of the machine's memory | `_no_machine_state` (512 MiB) | structural under pytest |
| `POOLHOUSE_WINDOW_POSITION` | `poolhouse.scrape.browser.placed` | a headed browser opens on the primary display | `.claude/settings.json` | **left**: an agent that asks for a headed window without a position could take the primary screen. `scrape/browser.py` is being changed by the real-machine worker; the fix there is "an agent with no position is headless". Headless is the default of every driver |
| `POOLHOUSE_SANDBOX_SERVE` | `poolhouse.serve.confined` | model servers run unconfined | `fleet/wsl.py`, a person | **left, risk named below**: confining by default needs a GPU lease to prove Metal and CUDA still load under the profile |
| `POOLHOUSE_SHIM_LOG` | conftest, `testreuse_key` | desktop-tool shims record to nowhere | conftest | structural under pytest (shims on `PATH` fail the run) |
| conftest import installs git hooks | `tests/conftest.py::_install_git_hooks` | (no variable: on) | nobody | writes the repository's own `.git/hooks` only; never the real home |

## B: off switches and unlocks

| Names | Read in | Unset means | Who can set it | Agent in its own shell | Logged | Decision |
| --- | --- | --- | --- | --- | --- | --- |
| `POOLHOUSE_GUARD` (`off`) | `worktreerules.switched_off`, both Claude guards | soft rules on | a person at launch or in `.claude/settings.json` | no: a hook reads its own harness's environment, and the edit guard refuses edits to settings | no | kept; the hard rules (main, tags, force, identities, person record, hook skips) stay on when it is off |
| `POOLHOUSE_PUSH_MAIN` | nothing: inert | nothing | nobody | its use is refused | no | documented as inert; release-main approval replaced it |
| `POOLHOUSE_BUDGET_RISE` | `scripts/hooks/budgets-only-fall` | a rise is refused | a person at a terminal | refused under `CLAUDECODE` by the hook | no | kept |
| `SKIP_BUDGETS`, `SKIP_NAME_CHECK`, `--no-verify`, `commit -n` | the commit hooks | all hooks run | a person at a terminal | **fixed**: the Bash guard refuses all four spellings (hard rule, on even with `POOLHOUSE_GUARD=off`), and the budgets hook ignores `SKIP_BUDGETS` under `CLAUDECODE` or `POOLHOUSE_AGENT`; the hook's own message used to tell the committer to set it | no | closed |
| `POOLHOUSE_BUILD_UPDATE` (`off`), `POOLHOUSE_BUILD_PINNED` | `poolhouse.build_update` | the device follows its pool's build | a person, `runtime pin` | can set it for its own process only | the activity log records an update, not a pin | kept; pinning is a person's and shown in status |
| `POOLHOUSE_SENTINEL`, `POOLHOUSE_SENTINEL_SCAN`, `POOLHOUSE_SENTINEL_CANARY`, `POOLHOUSE_SENTINEL_DECOY`, `POOLHOUSE_SENTINEL_BECAUSE`, `POOLHOUSE_SENTINEL_SCAN_BECAUSE`, `POOLHOUSE_SENTINEL_CANARY_BECAUSE`, `POOLHOUSE_SENTINEL_DECOY_BECAUSE` | `poolhouse.sentinel`, `serve.decoy` | the sentinel runs | a person | an agent's environment only reaches its own children | yes: switching off needs a `_BECAUSE` reason, recorded | kept |
| `POOLHOUSE_NET_UNSCANNED`, `POOLHOUSE_NET_SCAN_MODELS`, `POOLHOUSE_NET_ALLOW_HOSTS`, `POOLHOUSE_FETCH_ALLOW_HOSTS` | `poolhouse.net`, `httpguard` | scanning on; only listed hosts | a person (operator) | its own process only; `_no_internet` deletes them for every test | no | kept |
| `POOLHOUSE_ALLOW_INSECURE_CREDENTIALS` | `credentials.reading` | credential files must be private | a person | its own process | no | kept |
| `POOLHOUSE_ROBOTS` (`off`) | `poolhouse.web` | robots.txt honoured | a person | its own process | no | kept |
| `POOLHOUSE_ADOPT_UNMANAGED` | `serve.machine_cli` | an unmanaged server is refused | a person | its own process | no | kept |
| `POOLHOUSE_PARALLEL_REQUESTS` | `poolhouse.gate` | one generation at a time per device | a person | its own process | yes: logs that it did | kept; this is the floor "one thing computing" |
| `POOLHOUSE_BROKER_LOCAL` | `serve.broker_wire` | the real broker | `_no_machine_state` | refused by the Bash guard | no | kept; tests only |
| `POOLHOUSE_REPUTATION_RECOVER` | `reputation.store` | a damaged store is refused | a person | its own process | no | kept |
| `POOLHOUSE_IQ` | `serve.lifecycle_cli` | `warn` | a person | its own process | no | kept |
| `POOLHOUSE_LIVE_API`, `POOLHOUSE_LIVE_NET`, `POOLHOUSE_LAN_TESTS` | `poolhouse.testing.live`, `poolhouse.lan` | the marked tests skip | a person at the machine | **`lan_network` tests skip under an agent marker**; the other two are documented "do not set" | no | kept; `POOLHOUSE_LAN_TESTS=1` also lets a person's own run listen on the LAN (`lan.allowed`), never under an agent marker |
| `POOLHOUSE_NET`, `POOLHOUSE_TEST_SSHD`, `POOLHOUSE_MANUAL_DIALOG`, `POOLHOUSE_TEST_GGUF`, `POOLHOUSE_POOL_CHECK` | tests and the `scripts/pool-check` rig only | the marked tests skip | a person | its own process | no | kept |
| `POOLHOUSE_MUTATION` | `scripts/gates/_mutation.py` | normal runs | the mutation runner | its own child | no | kept |
| `POOLHOUSE_FLEET_TLS` | nothing: removed | nothing | nobody | ignored (a test proves it) | no | removed |

Not a variable but a switch: `CLAUDECODE`, `POOLHOUSE_AGENT` and `POOLHOUSE_NONINTERACTIVE` mark an agent (`person.AGENT_MARKERS`); a command could run
`env -u CLAUDECODE`, so every person-only action also needs a controlling terminal (`person.controlling_terminal`), which an agent's shell does not have.
`poolhouse node join --yes` and `--force-build` are an ordered agent's on the owner's instruction, and are audited by the authority registry.

## C: plain config

| Names | What they set |
| --- | --- |
| `POOLHOUSE_ACTIVITY`, `POOLHOUSE_ACTIVITY_RETENTION_DAYS`, `POOLHOUSE_ACTOR`, `POOLHOUSE_SESSION`, `POOLHOUSE_SESSION_HARNESS`, `POOLHOUSE_SESSION_ID`, `POOLHOUSE_WORKSPACE_AGENT`, `POOLHOUSE_WORKSPACE_HOME`, `POOLHOUSE_WORKSPACE_TOKEN`, `POOLHOUSE_WORKSPACE_DENYLIST`, `POOLHOUSE_BOARD`, `POOLHOUSE_AGENT`, `POOLHOUSE_HOOK_STATE`, `POOLHOUSE_ATTENTION_EVERY_S`, `POOLHOUSE_LABELS` | who is speaking and where it is recorded; set by the launchers, never typed |
| `POOLHOUSE_NO_MEMBERS`, `POOLHOUSE_ADMISSION_WAIT_S`, `POOLHOUSE_REQUEST_WAIT_S`, `POOLHOUSE_LEASE_FOR`, `POOLHOUSE_SLOTS`, `POOLHOUSE_LOG_DAYS`, `POOLHOUSE_LOG_FILES`, `POOLHOUSE_LOG_MB`, `POOLHOUSE_MIN_INTERVAL` | waits, reasons and sizes; `POOLHOUSE_NO_MEMBERS=1` stops a download asking paired devices first |
| `POOLHOUSE_ADOPT_CACHE`, `POOLHOUSE_BUILD`, `POOLHOUSE_DEST`, `POOLHOUSE_MODE`, `POOLHOUSE_MODELS`, `POOLHOUSE_NAME`, `POOLHOUSE_OFFLINE_MODELS`, `POOLHOUSE_OFFLINE_WHEELS`, `POOLHOUSE_OFFLINE_ZIP`, `POOLHOUSE_PREFIX`, `POOLHOUSE_REF`, `POOLHOUSE_REPO`, `POOLHOUSE_SRC`, `POOLHOUSE_TRACK`, `POOLHOUSE_VERSION`, `POOLHOUSE_WIRE`, `POOLHOUSE_BUILD_ID`, `POOLHOUSE_BUILD_SEQ` | the installer and the build the node reports |
| `POOLHOUSE_API_KEY`, `POOLHOUSE_CANARY_API_KEY`, `POOLHOUSE_CREDENTIALS_FILE`, `POOLHOUSE_DECIDE_KEY`, `POOLHOUSE_DECIDE_URL`, `POOLHOUSE_DEVICE_KEY_PASSPHRASE`, `POOLHOUSE_JOIN_CODE`, `POOLHOUSE_MEMORY_KEYS`, `POOLHOUSE_MEMORY_PASSPHRASE`, `POOLHOUSE_MEMORY_PROFILE`, `POOLHOUSE_PASSPHRASE`, `POOLHOUSE_POOL`, `POOLHOUSE_POOL_KEY`, `POOLHOUSE_SIGNING_PASSPHRASE`, `POOLHOUSE_RELEASE_SIGNING_KEY`, `POOLHOUSE_VOLUME_KEYCHAIN`, `POOLHOUSE_MEMBER_NAME` | a secret or a name handed to one command, or the keychain item name; never a switch |
| `POOLHOUSE_DESTRUCTIVE_FLOOR`, `POOLHOUSE_DESTRUCTIVE_MODEL`, `POOLHOUSE_THINK` | stricter or tuned behaviour a person turns on |
| `POOLHOUSE_BACKEND`, `POOLHOUSE_DEVICE`, `POOLHOUSE_MODEL_PATHS`, `POOLHOUSE_SCAN_VOLUMES`, `POOLHOUSE_LLAMA_PATCHES`, `POOLHOUSE_MTP`, `POOLHOUSE_PDF_ENGINE`, `POOLHOUSE_UI_VOCAB`, `POOLHOUSE_PYTHON`, `POOLHOUSE_CONTRACTS`, `POOLHOUSE_GATES_FULL`, `POOLHOUSE_GUARD_CACHE`, `POOLHOUSE_PHASES_FILE`, `POOLHOUSE_SMOKE_GGUF`, `POOLHOUSE_FAKE_LLAMA` | engines, paths and the model locations a person points at |
| `POOLHOUSE_DISCOVERY_GROUP`, `POOLHOUSE_DISCOVERY_PORT`, `POOLHOUSE_WSL_DISTRO`, `POOLHOUSE_WSL_NETWORK`, `POOLHOUSE_WSL_VENV`, `POOLHOUSE_WINDOWS_PYTHON`, `POOLHOUSE_WINDOWS_USER`, `POOLHOUSE_DEV_BRANCH`, `POOLHOUSE_DEV_ROOT`, `POOLHOUSE_DEV_VENV`, `POOLHOUSE_LINUX_IMAGE`, `POOLHOUSE_LINUX_PLATFORM`, `POOLHOUSE_LINUX_REBUILD`, `POOLHOUSE_LINUX_SINGLE`, `POOLHOUSE_LINUX_VOLUME`, `POOLHOUSE_GYM_FILES_ROOT`, `POOLHOUSE_GYM_PYTHON`, `POOLHOUSE_GYM_PYTHONS`, `POOLHOUSE_FILES_ROOT`, `POOLHOUSE_JOB_DIR`, `POOLHOUSE_JOB_ID`, `POOLHOUSE_OUT` | ports, groups, WSL and Linux test-container shapes, worlds and job files |
| `POOLHOUSE_TREES_CAP_PER_HOUR`, `POOLHOUSE_TREES_CLAIM_H`, `POOLHOUSE_TREES_GRACE_H`, `POOLHOUSE_TREES_LEAD`, `POOLHOUSE_TREES_MAX_AGE_H`, `POOLHOUSE_TREES_MAX_AHEAD`, `POOLHOUSE_TREES_MAX_BEHIND`, `POOLHOUSE_TREES_REPEAT_MIN`, `POOLHOUSE_TREES_TTL_H` | the worktree report's limits |
| `POOLHOUSE_TEST_HOLDER_MANIFEST`, `POOLHOUSE_TEST_HOLDER_ROLE_BANK`, `POOLHOUSE_TEST_ROLE_CONTROL_REPORT`, `POOLHOUSE_TEST_ROLE_IMPORT_REPORT`, `POOLHOUSE_FROZEN_BINARY`, `POOLHOUSE_FROZEN_CODING_BINARY`, `POOLHOUSE_ACCEPTANCE_DECIDE_CACHE`, `POOLHOUSE_ACCEPTANCE_DECISION_DEVICE`, `POOLHOUSE_ACCEPTANCE_VISION` | inputs of the test kernel and the acceptance runs |

## D: opt-in speed-ups

Gains come from `docs/test-time.md` (2026-10-09, 16 logical CPUs, load about 25), `docs/serving.md` and a measurement in this change; a speed-up
with no measurement says so and stays off.

| Switch | Where | Now | Measured gain | Risk if on by default | Decision |
| --- | --- | --- | --- | --- | --- |
| `scripts/test quick` (testmon map) | `scripts/test` | opt-in tier; `full` is the default | none recorded for `quick`; the `full` idle baseline is 183 s wall (`tests/full-tier-time.json`) | `quick` runs the full tier when it cannot tell what a change reaches | stays opt-in: no number to justify it |
| `scripts/test TIER --on DEVICE\|all --split` | `docs/test-farm.md` | opt-in | `docs/test-farm.md` says no speed-up has been measured on real machines | a second device in the loop | stays opt-in: unmeasured |
| CI `cache: pip` and a cargo target cache | `.github/workflows/ci.yml` | none (only `redteam.yml` caches pip) | not measurable off CI | a cache written by one run is read by another; the workflows pin every action by SHA and none of the cache actions' SHAs can be checked offline | not changed: needs a CI run to measure and a pinned SHA to verify |
| `--cache-reuse 256` and `--no-warmup` on `poolhouse-serve up` | `serve/backend.py::_cache_argv` | off for `up`; on in `bench`, `harness`, `ingest`, `localloop` | no number recorded; `ServerSpec` says a benchmark otherwise reprocesses its preamble "twenty or thirty times a run" | a changed server shape for every lease | stays: re-measure under a GPU lease first (not available here) |
| `up --plan auto` and the 131072 context | `docs/serving.md`, "plan" | opt-in; `up` serves 4096 | the table in `docs/serving.md` is estimated; a real `--n-cpu-moe` load is listed as unverified | a plan that overcommits a machine's memory | stays: needs the GPU lease |
| per-commit hooks: the person-name check reads each staged file whole | `scripts/hooks/no-real-names` | on, whole file | 20.7 s of a 27.2 s pre-commit (`docs/test-time.md`, load about 25); measured again 2026-10-11 with one docs file and one test file staged: 11 s wall for the whole commit (timed with `date` before and after, machine not quiet) | a missed name | not changed: privacy hook, its own change with a break-it pass |

Already on by default and so not rows: q8_0 KV cache, flash attention, the MTP head when trusted, `-n` workers from the broker, reuse of an unchanged file's pass
(`--no-reuse` turns it off), incremental gates (`POOLHOUSE_GATES_FULL` turns it off), `concurrency: cancel-in-progress` in CI.
