# Shared test execution

Run tests with `python scripts/test fast`, `full`, `slow`, `all`, or `quick`.
Explicit test paths are checked before broker admission using pytest’s argument parser.
A missing file or directory, or a node selector that collects no tests, returns
exit status 4, including in `quick`; an empty affected-test selection remains valid.
`-n N` limits the worker pool; scoped runs default to an automatic pool of what the broker grants (`-n 0`); `-n 1` runs sequentially. `-n 0` requests
an automatic pool up to broker capacity. Explicit worker ceilings remain effective.
An automatic pool is sized from the run's estimate (below): the square root of the estimated seconds, at least 1 and at most the
broker's capacity, because each worker costs about a second of start-up; a run with no estimate takes the whole capacity.
A pool of one runs pytest itself with no xdist worker (one process, one collection); an explicit `-n 1` still starts an xdist worker.
Before admission the runner builds the node binary only for a selection that can start one; that check reads the shared import facts
(`import-facts.json` in the reuse store), so it parses only sources whose bytes changed. The pytest arguments are parsed once per process.
The phase line `scripts/test` prints splits a run into collection, session setup, tests, teardown and the
seconds workers waited for CPU permits (`queued for CPU`); the queue wait is the machine's, so only the rest is the fixed overhead
that the warning measures.
Every checkout shares the same admission broker.

Agents run affected tests for their own changes: explicit file selectors, any number of them. The main agent
coordinates shared structural and security gates once per consolidated integration batch before publication.
The full suite is GitHub's job: the `ci` workflow runs it on every pushed commit, and an agent never runs one.
For an agent (`CLAUDECODE`, `POOLHOUSE_AGENT`, `POOLHOUSE_NONINTERACTIVE` or a Codex session set) `scripts/test`
refuses, before anything is queued, the `fast`, `full`, `slow` and `all` tiers that name no test file, and any run that names
every test file (directories count as the test files under them); the broker (`scripts/testslots_runner.py`) admits no agent
pytest run that names every test file; `quick` and `gate` stay allowed. The refusal reads `the whole suite runs on a schedule
and on pull requests; name the files you changed`. A person's terminal sets none of the
markers and is never refused. The main agent owns `quick`; workers use explicit affected selectors.

`scripts/test <tier>` runs as a background job that the command follows: `submit` prints the job id
and returns, `status`, `wait`, `result` and `cancel` act on it, and Ctrl-C on a followed run
cancels its job. Jobs use the admission below and are owned by the submitting workspace agent,
which `scripts/testslots.py status` shows beside each lease. A run that names test files reuses
earlier passes of unchanged files and says so per file ([test-reuse.md](test-reuse.md)).

## Reuse is the default

A run that names test files reuses every unchanged file's stored pass; nothing asks for it and a brief never says
"use --no-reuse". `--no-reuse` executes each named file again at the full cost of its run and prints one line saying so. It
is for watching a mutated test fail, and for nothing else: an affected-test report, a landing request and a rerun after an
unrelated edit all use the store. The runner states why a file's result was not stored (`result not stored: ...`); the
causes measured on 2026-10-16 across 724 stored job logs were a write outside the temporary root by a fixture (the node
fixtures make their short socket folder under the literal `/tmp`, which the plugin now counts as temporary like `TMPDIR`),
a `mkdir` of the slot directory by `tests/conftest.py` (made only when `DEV_TEST_SLOTS_DIR` is unset now), a skip, and a
closure that uses a call the runner cannot observe (`ladybug`, `sqlite3`, `utime`, `fstat`).

## Fixed overhead

A run's fixed overhead is its collection, session setup and teardown, the three phases `testphases` prints; the calls
between workers are not part of it, so an uneven share of slow tests does not read as overhead. The limit is 5 s (AGENTS.md).
`tests/test_runner_overhead.py` bounds the CPU seconds the runner takes to start and one pytest process takes to start and
collect, because CPU time holds on a busy machine where wall time does not. Measured 2026-10-16 on a 16-core machine at load
about 35: a one-file run took 17 s wall, 6.3 s of it inside pytest. Contributors found and removed: the `langsmith` pytest
plugin imported by every pytest process (`testcommand.UNUSED_PLUGINS`), the hook installer that `tests/conftest.py` ran in
every xdist worker (now `tests/git_hooks.py`, once, by the controller, and a machine-local wrapper counts as installed), and a
second selector validation in the job process.

CPU capacity defaults to the logical CPU count minus one, with a minimum of
one. `DEV_TEST_RESERVED_CORES` changes that reservation. This reserves capacity;
it does not pin execution to a particular efficiency core. `DEV_TEST_BUDGET`
sets an explicit owner override. The capacity does not change with the machine's load.
Existing active permits finish before new work enters.

The supervisor admits pytest startup, then each worker's configuration and collection, and each
test's complete setup, call and teardown. Idle workers hold no CPU permits. A
long final test therefore leaves the remaining capacity available to other
suites. A worker pool retains enough workers to use freed capacity after another
suite finishes. Multi-threaded model and benchmark test files also acquire a heavy
lane before their CPU permit. The `slow` marker alone does not require a lane.
Thread-library defaults are one thread per test process; explicitly configured
thread settings remain effective.

Lease records are published as complete JSON on an already locked inode under
the scheduler mutex. Published updates retain that inode and run under the same
mutex. Readers skip malformed or incomplete records and retry on the next
admission pass. A queued lease restores a missing or replaced record while
retaining its original FIFO position and a live file lock.

Normal command waits report a changed queue state at most once per second,
or repeat unchanged status after twenty seconds. Short waits stay quiet.
Per-test RPC waits do not print into pytest's progress output; use the shared
status command to inspect their queue.

`python scripts/testslots.py status` reports active permits and waiting work.
A queued minimum cannot exceed the configured capacity. Small requests can fill
a temporarily unusable gap twice before an older larger request reserves the
gap. Wait limits raise an error; they never disable admission. A command started
inside a live test permit fails promptly instead of queuing behind its parent.
Run an independent suite after the parent releases its permit.

`python scripts/testslots.py pytest --want auto --min 1 --label NAME -- COMMAND`
supervises a pytest command that loads `-p testslots_pytest` (`scripts/test` also loads `-p testagentenv_pytest`, which clears the agent markers for every test) and uses
`DEV_TEST_WORKERS` as its worker ceiling. `{workers}` in a command argument is
replaced by that ceiling. `DEV_TEST_SLOTS_DIR` identifies the shared host broker.
The supervisor supplies `DEV_TEST_PYTEST_ENDPOINT`, `DEV_TEST_PYTEST_TOKEN`, and
`DEV_TEST_REMOTE_BROKER`; propagate those into a supervised container. The token
is private to that run and its admission endpoint closes when the run ends.

`--container` exposes the authenticated host endpoint to Docker through
`host.docker.internal`. Docker Desktop file locks do not coordinate with macOS
host locks, so container workers request host permits through this endpoint.
Native supervisors bind to loopback. The Linux runner installs dependencies
under one setup permit, releases it, then starts supervised pytest. Serial
execution through `scripts/test` uses `-n 1`. A directly supervised pytest command can use `-n 0`
to run without xdist workers.

On macOS, `scripts/test --confine` (or `DEV_TEST_CONFINE=1`) runs pytest under the Seatbelt
confinement kernel; without it the run follows the ordinary path and the isolation guards in
`tests/conftest.py`. The kernel constructs private startup home, state, cache and temporary
roots before collection and launches pytest through the maintained Seatbelt backend, which
denies every write outside the private scratch root. Source reads name tracked files
individually; dependency prefixes are read-only. The child receives only its authenticated
private Unix admission and descriptor-transfer endpoints. Fixtures that need more require
explicit pre-launch endpoint reservations, and the kernel accepts only reviewed gate files and
the nodes listed in `scripts/test_kernel_selectors.py`; other selectors fail before collection
rather than receiving loopback access. The human-floor terminal fixture uses a supervisor-owned
PTY bank through bounded authenticated operations on that endpoint. The kernel does not walk the
real state root: a canary file outside the scratch root and the profile's write denial carry
the isolation proof.

The supervisor pins stdout and stderr descriptors before the confined launch. Regular output
files and JUnit destinations must be owned, have one hard link, and stay outside protected
state by physical path and directory identity. Child output forwarding and JUnit promotion each
permit at most 16 MiB. Sockets, named FIFOs and unidentified device destinations are refused;
output writes have a five-second deadline per forwarded block. A failed preparation removes the
control and holder directories it created.

`scripts/test all --confine --artifact-output` creates fresh private console, JUnit and
evidence files. Confined output and supervisor diagnostics stay in that namespace; the caller
receives its location before collection. Private shadow Git metadata disables executable
configuration and filters. Tree hashing there uses raw source bytes, tracked deletions,
executable modes, symlinks and recorded gitlinks; repository `.git/info/exclude` and global
ignore configuration are not imported, and unsupported split or sparse indexes fail closed.

# The real state root is write-denied

Without `--confine`, `scripts/test` (and `scripts/testslots run`) still keep every test process from
writing under the real `~/.poolhouse` (`scripts/testwritedeny.py`), so a test that reaches for it gets
`EPERM` at the line that did it instead of failing the run afterwards ("real state root changed
during the run", which also names other agents' live writers the suite did not cause). On macOS the
pytest tree runs under `sandbox-exec` with an allow-default profile that denies `file-write*` under the
account's state and cache roots and every root the launching environment names (`POOLHOUSE_HOME`, `POOLHOUSE_CACHE` and each set `home.OVERRIDES` variable); a root that holds the checkout, the temporary directory or HOME is left out with a printed notice; on Linux under `bwrap` with the root
bound read-only, but only when a probe on a scratch directory shows both that it denies and that a
nested `bwrap` still starts. Windows and a host without either tool run unwrapped, and the
after-the-fact check alone applies, with a notice. `DEV_TEST_WRITE_DENY=0` turns it off for one run. The supervisor sets `DEV_TEST_WRITE_DENY=1` in a child only after it wrapped that child, and a value set by hand is never proof: a run already under a denial is recognised by effect (opening an existing file under the root for writing is refused), and `tests/test_real_state_write_denied.py` checks the same way, without creating or changing anything.

What it does not cover: the denial is a path rule on file writes. The Keychain (securityd, a Mach service), `launchctl`, `open` and `fleet.autostart` act through other processes and are not stopped; a hard link made outside the root to a file inside it lets a write through (measured by `test_a_hard_link_to_a_file_in_the_root_is_written_through_or_blocked_and_this_is_which`); and the listed modules below run undenied. The list is matched by path relative to `tests/`, not by file name.

A process that is already sandboxed cannot apply another `sandbox-exec` profile (measured: the call
fails with `sandbox_apply: Operation not permitted` unless the two profiles are identical). The test
modules that start `sandbox-exec` themselves are listed in `tests/seatbelt-modules.txt`, marked
`seatbelt` by `tests/conftest.py`, and a run that selects them is split in two: everything else under
the denial, then those modules beside it without, with the JUnit files merged. A module that fails with
that message under the denial belongs on the list (`tests/test_test_write_deny.py` fails when one that
names the Seatbelt backend is missing). A full `fast` run measured this way (macOS, 2026-10-09) showed
no other test writing the real root.

A test that tries the forbidden write leaves a "touched" note in the reuse store, so
`tests/test_real_state_write_denied.py` is never stored as reusable; it costs two seconds a run.

# Holder channel resources on macOS

Reviewed holder protocol nodes use three offline-prepared immutable fixture prefixes. The
supervisor verifies the reviewed manifest digest, complete file/link inventory and source
stamp, then copies the manifest into its private control directory before collection. One
fresh, private, short invocation namespace supplies TMPDIR and permits Unix bind, inbound and
outbound operations only inside that namespace. TCP stays denied.

## Scheduling: measured duration, shortest work first

Every run writes a duration history. The `testdurations` pytest plugin records the CPU and wall
seconds of each passing test, merges them under a file lock into `test-history/durations.json` in the
repository's common Git directory (shared by every worktree; `scripts/test` passes the path in
`DEV_TEST_HISTORY`), keeps an exponentially weighted value per test with a sample count (a sample is
clipped to three times the current value once three exist, so one loaded run barely moves it) and drops
tests whose file or definition is gone. A test's estimate is its CPU seconds, and at least a quarter of
its wall seconds. `scripts/test heavy` rewrites `tests/heavy-modules.txt` from this history.
Only measured durations decide a run's length: the `slow` and `heavy` labels (the marker and
`tests/heavy-modules.txt`) never enter the estimate or the class and must not bar a test from any
tier, cache or queue.

Before admission `scripts/test` estimates the run: the recorded seconds of the selected files and nodes,
summed per file without collecting, plus for each file with no record its test functions (counted in the source) at the
mean seconds of a recorded test with the slowest one percent left out, at most 30 s a file (30 s when nothing is recorded),
divided by the workers the broker would grant. Named files whose pass the test-reuse store would serve (a read-only lookup, no
claim) cost nothing; a reused file never runs, so the plugin records no duration for it, and a
selection of directories or nodes is estimated in full. It prints the estimate (`test: estimated 38 s`, or `estimated 42 min from 3,120
recorded tests`). An estimate of `DEV_TEST_BACKGROUND_S` (default 180) or more classes the run
`background`, below it `interactive`, whatever the tier name. With fewer than 50 recorded tests the
command shape decides: tiers `full`, `slow`, `record`, `all` or `fast` with no file or node selector,
and `--redteam` are background. `--background` forces the class; `quick` and `gate` are interactive. A background run prints one line saying why, its pytest
processes start at `nice` +10 (no change where `os.nice` is absent), and `testslots.py status` shows each
lease's class.

The estimate travels with the lease (`DEV_TEST_ESTIMATE_S`). Queued requests are granted shortest
estimated work first: the order key is the estimate minus the seconds waited, so a longer run arriving
at about the same time as a shorter one goes behind it and a long wait outweighs a large estimate. A
request that has waited `DEV_TEST_BACKGROUND_WAIT_S` (default 600) is granted ahead of the ordering.
A run takes every worker that is free (or its `--want`, when given): no class and no queued run holds
another to a share of the budget, and the queue order prevents starvation. Nothing is held or delayed to
a time of day. While a lease written by older code (`version` below 2) is live, every lease keeps
arrival order.

Each `scripts/test` run keeps a run record in the slots directory (`*.run`: class, estimate, workers
wanted and granted, enqueue and start time). `testqueue.forecast(runs, budget, now)` (module
`scripts/testqueue.py`) returns for each run its position among queued runs under the real ordering,
the count and estimated work ahead, and an estimated start time from the remaining estimate of the
running runs plus the work ahead; `testslots.py status` prints these per queued run.

## CI shards

The CI `shards` job runs the test files as eight jobs. `scripts/ci_shards.py list --shards 8 --index N`
prints the files of job N: every file under `tests/` goes to the job with the least recorded time so far,
longest first, from the seconds per file in `tests/ci-durations.json` (a file with no record takes the
median). Each job runs its files with `--slow --timeout=120 --junitxml=junit.xml` and uploads the junit file
whether the tests passed or not; a test that needs longer carries `@pytest.mark.timeout(N)`. The check the
repository requires, `test (ubuntu-latest, 3.13, --slow)`, is the job that waits for every shard. The next
Python runs the same shards on the nightly schedule only.

`scripts/ci_shards.py record --junit junit-*.xml` rewrites the durations file from the junit files of a
CI run (the mean over the files that ran it), and `record --history` from the per-test history above.
`scripts/ci_shards.py summary --shards 8` prints each job's total and the longest file.

## Landing a batch

`scripts/land` (modules `scripts/land_*.py`) lands several ready branches with one verification.
Every test and gate runs through `scripts/test`, so the broker admits them like any other run.

- `plan [--cover] [BRANCH...]` lists each branch's unique patches (patch-id, `git cherry`
  semantics), drops branches another one contains, orders the rest so each merges cleanly onto the
  plan so far (`git merge-tree --write-tree`, no checkout), and reports files, commit and behind
  counts, predicted conflicts and dirty worktrees. It warns past 50 commits or 20 behind.
- `run [--dry-run] [--conflicts=eject|stop] [-n N] BRANCH...` merges with `--no-ff` and rerere in a
  sibling `<repo>-land-<stamp>` worktree on `land/<stamp>`, keeping both sides of `HANDOFF.md`.
  A documentation-only diff runs `scripts/budgets` and `git diff --check`. A code diff runs
  `scripts/test gate` and `scripts/test all` on the selectors `scripts/affected.py` computes from the
  combined diff. The whole suite runs on GitHub for the pushed commit.
- A pass is recorded with `record_run` under the tree hash; the same tree and check later print
  `reused from <id>`. A failing check is first run on a clean worktree of the base: failing there
  too is reported as `baseline`. Otherwise the merge commits are bisected with only the failing
  selectors, the branch is ejected, the integration branch is rebuilt without it, and the failed
  checks run again. An ejection prints branch, check, evidence, owner (the tip's `Agent-Label`
  trailer, else its worktree name) and commit count.
- `finish [--apply]` is a dry run unless `--apply`. It fast-forwards the target only when the
  primary checkout is clean and on the target, otherwise prints the command for the lead. It never
  pushes. Then it removes each landed source branch's worktree and branch (never forced), keeping
  dirty trees, trees holding the current directory and branches with unique patches, and the
  integration tree while its landing run is alive.

The last line of every command is a JSON summary.

## Rust builds in tests

The tests that run `cargo test` (`tests/native_cargo.py`, `test_native_authority_*.py`, `test_phone_native_governance.py`) get a
staged copy of `app/` and a target folder from `testnode.shared_build`: the copy sits at a fixed path under
`~/.cache/test-cargo/workspaces/<key of the sources and compiler>/app`, rewritten only where a file differs, and the target
folder `~/.cache/test-cargo/targets/<key of Cargo.lock and compiler>` is shared by every run with the same lock file and compiler,
so a run compiles the crates whose files changed and not the registry dependencies again (`DEV_TEST_CARGO_DIR` names another
folder). The folder is outside every checkout and outside `~/.poolhouse` and `~/.cache/poolhouse`, which a test may not write;
the reuse plugin counts it as a place a test may write because it holds only what the sources say. Folders idle for three days
are removed. Cargo's own lock on the target folder serialises two runs that build at once.

## Fixed cost of a scoped run

Measured 2026-10-19 on a 16-core macOS machine at load 32 with
`scripts/test all tests/test_test_time.py tests/test_testclass.py tests/test_testorder.py --no-reuse`: a `cProfile` of the detached
job process with a timestamp around each stage of `main`, run against an empty private broker directory (`DEV_TEST_SLOTS_DIR`,
`DEV_TEST_BUDGET=15`, so the queue wait left is the protocol's) and against the shared broker, whose permit table was sampled every
50 ms while the run waited. The reuse store held 2,988 import facts.

| stage | seconds |
| --- | --- |
| follower process: imports, argument parse, job record | 0.8 |
| job process: imports | 0.35 |
| job process: argument parse (validation and classification) | 0.9 before, 0.6 after |
| job process: node-needed check (`testnode.needed`) | 2.4 to 3.3 before, 0.3 after |
| four tree hashes | 0.3 |
| pytest process start, plugin load, conftest import, collection | 1.1 to 1.4 |
| first-test setup (session fixtures, two walks of the real state root, about 4,000 files) | 0.6 to 0.8 |
| per-test autouse fixtures | about 0.025 each |
| reuse recording after pytest | 0.65 |
| queue wait, empty broker | 0.1 for the whole run |
| queue wait, shared broker at budget 7 of 15 | 2 to 12 for the whole run |
| `python packaging/build.py` (hatchling cached) | 4.5 per input digest per checkout |

On the shared broker no permit was free during 85 percent of the sampled waiting (the load rule had halved the budget to 7), and
the grant latency of the head of the queue after a release was 17 ms with no other request waiting and 80 ms with 60 waiting, so the
wait is the machine's. What a run controls is how many leases it takes: a worker holds one for its bootstrap and collection, and every
test takes one. A built wheel is stamped with the commit and the checkout it was built from, so it is not shared between checkouts.
