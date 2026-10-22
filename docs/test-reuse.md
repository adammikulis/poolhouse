# Test result reuse and the async runner

> **Replaced by the test runner design.** The fixed numbers this note describes as current (the canary rate of 0.05, the 900 s single-flight wait and the three-day pruning of finished jobs) are replaced by measured or
> project-set values in the migration of `docs/design-test-runner.md` (section 15 and migration steps 1 to 11); until that step lands
> they are what the code does, and none is to be copied into new code.

`scripts/test` counts a passing test file from an earlier run when that run executed the identical
file against identical code under identical settings, and runs suites as background jobs. This note
defines what counts as identical, what is never reused, how entries are protected, and where the
protection stops.

## When reuse applies

Reuse applies to a run in tier `fast`, `full`, `slow` or `all` that names test files
(`scripts/test all tests/test_x.py ...`). It does not apply to a node selector (`file::test`), a
directory, `quick`, `record`, `gate`, or `--no-reuse`. A tier run with no file selector and the
`gate` pytest step always execute and write an entry for every file that passes, so the
coordinator's combined-tree gates and background suites refresh the store and are never satisfied
by it. A named-file run reuses an entry only when its pytest flags equal the flags of the run that
wrote it (`all` writes `--slow`, `fast` writes its marker expression).

A reused file satisfies a worker's affected-test requirement only when the report says
`reused from <run id>` for that file, with the tree hash and age the runner printed.

## The key

Each file has a lookup key and a manifest. The lookup key is a sha256 over:

1. the bytes of the test file, every applicable `conftest.py`, `pyproject.toml` (and `pytest.ini`,
   `tox.ini`, `setup.cfg`) and `tests/heavy-modules.txt`;
2. the Python version and implementation, platform and machine;
3. `name==version` of every installed distribution that registers a pytest plugin, the pytest and Python
   versions of the interpreter the command runs, and the bytes of the runner's own code
   (`scripts/testreuse_*.py`, `testslots_pytest.py`, `testslots_rpc.py`);
4. the pytest arguments that can change a result (markers, `-k`, `-m`, `--slow`, options), with file
   selectors, `-n`, `-q`, `--junitxml` and the runner's own plugins removed (other `-p` names and the
   values of `-c`, `--rootdir`, `--confcutdir` and `--ignore` stay);
5. the values of the environment variables named in the test file or its conftests, plus a fixed list
   (`PATH` without temporary entries and with the checkout root masked, `CI`, `TZ`, `LANG`, `LC_ALL`,
   `CLAUDECODE`, `POOLHOUSE_NOTIFY`, `POOLHOUSE_LIVE_API`, `POOLHOUSE_LIVE_NET`, `DEV_TEST_SLOTS`);
6. the installed `poolhouse` distribution and how it was installed.

The manifest is what the run depended on: the first-party files in the static import closure of the
test file, its conftests, their `pytest_plugins` and the `-p` modules (imports at any depth, including
inside functions and packages' `__init__`), every path the tests probed with `os.stat`, `lstat` or
`access` (a file by content, a directory by listing, a missing path by staying missing), symlink targets on
the way to a recorded path, reads made while a session, package or module fixture was set up (attributed to
every file in the run), every file the
test's collection or tests opened under the checkout, every module they imported, the names in each
directory they listed, the `name==version` of the third-party packages those files import, the
values of the environment variables those files name, and a digest of the code, documents and packaging
(`src/`, `scripts/`, non-test `tests/` files, `docs/`, `packaging/`, `budgets.json`, `AGENTS.md`,
`CLAUDE.md`, `pyproject.toml`, `HANDOFF.md`) and of the HEAD commit when any file in the closure spawns a
process (`subprocess`, `Popen`, `os.system`, `os.exec*`, `os.fork`, `multiprocessing`, `pty`,
`create_subprocess_*`, `ProcessPoolExecutor`, `runpytest_subprocess`, `playwright`) or imports by machinery
the closure cannot follow (`import_module`, `__import__`, `importlib.util`, `runpy`, `pkgutil`,
`entry_points`, `importorskip`), because a child's reads and a computed import are not seen. The pytest plugin in `scripts/testreuse_plugin.py` records the opened files and
listings with an audit hook and attributes them to the test file whose collection or test was
running; modules imported at collection are attributed from `sys.modules`.

The import statements of each module are parsed once per exact source bytes: the facts are kept in
`import-facts.json` in the project's store, shared by every checkout, keyed by a digest of the bytes, the module
context and the analysis code (never by file metadata), and entries unused for two weeks are dropped.

A hit needs a stored entry for the lookup key whose manifest still matches the checkout byte for
byte. Nothing in either is a path, so two worktrees with identical content hit the same entry. The store is
`~/.cache/test-reuse/<project>`, and every worktree of one repository is one project (by origin url,
else by the repository's common git directory).

Limits of what the plugin sees: `DirEntry.stat`, `fstat`, `utime`, `chown`, `setxattr`, `mmap`, `sqlite3` and C extensions (such as `ladybug`) touch files without a Python-level `open`, `os.stat`, `lstat`, `access` or listing event, and a function bound before the plugin loaded (`from os import stat`) bypasses the `os.stat` wrapper. A file whose own code (the test file, its `tests/` helpers and conftests) mentions any of `DirEntry`, `scandir`, `fstat`, `utime`, `chown`, `setxattr`, `mmap`, `sqlite3` or `ladybug` is therefore never stored. Library code under `src/` that uses them is not scanned: a file that reaches such code is covered by the recorded reads, the tree digest for spawning code and the canary only.
A removal or rename made relative to a directory file descriptor is not attributed.

File hashes are memoised on path, modification time, change time, size and inode. An edit that restores the modification time still moves the change time, which user space cannot set; the residual is an edit inside one clock tick of the earlier read, and manifest members are checked once more just before an entry is written. A checkout whose walk takes more than ten seconds is not watched: its runs execute and store nothing. The plugin's `os.stat`, `lstat` and `access` wrappers take the real keyword arguments and resolve `dir_fd` paths where the platform can name the descriptor's directory.

Known gaps: a module another test file had already imported and that is reached only through a
run-time `importlib` call is not attributed; a file read by a child process of a test that spawns
nothing the regex recognises is not seen; a network or clock dependency has no file to record.
The canary below is the check on these. pytest-testmon's database is not read: it is one SQLite file per checkout keyed by function
checksums, so it cannot be compared across worktrees.

## Never reused

A file is executed and no passing entry is written for it when any of the following holds:

- the test file carries marker `live_api`, `live_net`, `redteam`, `gpu` or `model`, or names a model or
  GPU lease (`Lease`, `llama-server`, `poolhouse-serve`), or the marker appears on any of its items at run
  time. A scheduling label never bars a file: `heavy`, listing in `tests/heavy-modules.txt` and `slow`
  decide only when and where a file runs, never whether its result is kept;
- the run was red. A failure is written to the chain as a `fail` entry for attribution; it is never
  a hit, because a hit needs a `pass` entry;
- its tests wrote under the checkout other than to cache directories (Python's `__pycache__` bytecode is
  regenerable: it is ignored wherever it is written, site-packages included, and is not in the key; `dist/` is build output and
  may be written), wrote outside the temporary roots (`$TMPDIR`, `/tmp`, `/var/tmp`), read or listed something under the shared `/tmp`
  that the run did not make (what a run creates there with `mkdir` or a write, and anything inside it, is its own), or opened anything
  under the real `~/.poolhouse`, `~/.ssh`, `~/.gnupg`, `~/.config`,
  `~/.cache/huggingface` or `Library/Keychains` (the suite's own isolation guard in
  `tests/conftest.py` reads real state to check it is untouched; those reads are ignored);
- the run emitted a warning that mentions an isolation violation;
- any test in it was skipped or xfailed, because a skip depends on conditions the key lacks;
- the source tree changed during the run: the tree hash and a stamp (modification time, change time and size) of every
  file in the checkout, ignored files included but not caches, virtual environments, other agents'
  worktrees under `.claude/worktrees`, `dist/` or the runner's own `.full-tier-last.json`, are read before and after, so an edit that is put back is
  caught; the run fails with status 4. A file a test reads from `dist/` is stamped when it is read and compared when the result
  is stored, so a wheel built or rebuilt during the run does not stop other files from being stored but a wheel rewritten after a
  test read it does. An unknown tree hash (git failed) also stores nothing;
- the lookup key of a file, recomputed at the start and the end of the launch, differs from the key used
  for its hit check, so a result is stored only under the key of the bytes that ran;
- pytest exited with a status other than 0 or 1 (interrupted, internal error), the junit file holds
  fewer tests than were collected for the file, an xdist worker died or did not write its record, or the
  junit holds an error that names no test file while pytest exited 1;

A file whose tests all skip at module level, or that collects no tests, counts as passed and is not stored;
pytest's status 5 (no tests ran) stays 5, unless every named file collected tests and skipped them.

## Integrity

Only the runner process writes entries, after it executed pytest itself; no command records an
entry. An entry holds the schema, the lookup key, the manifest and its digest, the exact command, the
junit file's sha256 and counts, the tree hash, the runner's pid and start time, the executing
workspace agent (id, label, parent, taken from the authenticated session and not from arguments), a
timestamp and the previous chain hash. `entries/<id>.json` is written through a rename; the id is
the first twenty hex digits of the entry's own sha256. `chain.jsonl` appends
`{seq, kind, lookup, id, entry_sha256, prev, row_sha256}` under a file lock, like the activity log;
`latest/<lookup>` names the newest pass.

A read parses the entry against its schema, recomputes its hash, finds its chain row and checks the
chain from the first row, then recomputes the manifest from the current tree. Any failure is a miss.

What an agent with filesystem access can still do: the store is ordinary files owned by the same
operating system user as the agent. That user can rewrite an entry and the whole chain
consistently, delete the chain, edit the runner or the plugin, or set `DEV_TEST_REUSE_DIR` to a store
it made. The chain detects corruption, a partial write and an edited entry; it does not stop a
deliberate forger. A forged hit is caught only by the canary on its sample and by the tier and gate
runs, which execute. A sandboxed agent (docs/agent-sandbox.md) cannot write `~/.cache/test-reuse`: its `scripts/test` stores in
`~/.cache/test-reuse-sandbox`, named by `DEV_TEST_REUSE_DIR` in the sandbox's environment, so the task evidence
(`poolhouse.workspace.testruns`) reads only the owner's store and a pass forged in the sandbox store is read only by other
sandboxed runs. Entries also hold the commit they ran on and whether the checkout was clean; the tree hash, commit and cleanliness are read together just before each launch. The store sits under a per-project directory (a hash of the project key), and
board verification reads only the asking agent's project directory.

## Single flight

Before executing, the runner claims each missing key with an exclusive create of
`inflight/<key>.json` holding pid, process start time and agent. A second request for a key whose
owner is alive (pid and start time match, zombies excluded) runs its other files first, then waits
for the claim to clear, polling, up to fifteen minutes, and uses the entry if one passes. If the
owner died or finished red, the waiter executes the file. A claim from a dead owner is removed by
whoever finds it.

## Reporting and the canary

Each file prints `ran` or `reused from <run id> (tree <hash>, <age>, by <agent>)` with its key, and
the summary counts both. The exit code is pytest's status for what ran; a run of reused files only
exits 0. `--no-reuse` forces execution. Each hit is re-executed with probability `CANARY_RATE`
(0.05; `DEV_TEST_REUSE_CANARY` overrides it). A cached pass that fails fresh prints
`canary mismatch`, fails the run, writes an `incident` chain row and a disabling record for the key
(reuse stays off while the manifest equals the one the cached pass recorded), and posts one
`#announcements` line.

## Async jobs

`scripts/test submit <tier> [paths]` records a job under the project's store, starts a detached
runner that goes through the same broker admission as a foreground run (no extra permit class and
no second queue), and prints the job id. `status [JOB]`, `wait JOB [--timeout S]`, `result JOB`
(status, summary, failing node ids, kept junit paths) and `cancel JOB`. A job is owned by its
submitting agent; only that agent cancels it. A job survives the shell, keeps its output in `log`,
and is pruned three days after it finishes. `scripts/test <tier>` is submit plus follow; Ctrl-C
cancels. One whole-tier job (a tier with no test path) may be live at a time; a second submit is
refused with exit status 6 and names the owner. A foreground tier run is not exclusive: it queues in
the broker. A job without a workspace agent can be cancelled only by the process that submitted it.

## Workspace integration

- A job, lease and entry carry the authenticated workspace agent (id, label, parent). Leases carry
  it as `agent`; the board's testslots view shows `agent/label (lease label)` and `registered`,
  which says whether the workspace has that identity.
- Entries, in-flight claims and jobs sit under a per-project directory. `task-checkpoint`/`task-submit`
  verification is project-scoped by the board's own membership.
- Notices go out under the submitting agent's own authenticated identity (the identity the harness assigned
  this process through its environment; the runner takes no `--agent` or `--label`). Running tests creates no identity, token file or credential; without a workspace
  agent the runner posts nothing. A run writes two kinds of board row and no others: a canary mismatch
  (one `blocked` announcement) and, when a job was submitted with `--task`, one message naming each watcher
  of that task other than the submitter. A file's claim, its result, a job's start and a job's end
  with no watcher stay in the runner's own directory: the submitter learns through
  `scripts/test wait|result|status`, which read the job's status file, and a waiter polls the claim
  of the run it waits on.
- `scripts/test subscribe --task ID` is the task board's `task-subscribe`; the watchers of a task hear
  how a job attached to it ends.
- `task-checkpoint` takes `test_entry` and `task-submit` takes `test_entries`: runner entry ids. The
  board verifies each in the store of the project of the task's assigned checkout (not the caller's
  working directory, and never a store chosen by the service's environment), requires `outcome == pass`
  and, when the checkpoint or provenance names a commit, the same commit, and records the entry's file,
  key, tree, commit, junit hash, counts and executing agent with the checkpoint or proposal. The
  checkpoint or provenance must name a commit; the entry must have run on that commit with no uncommitted
  changes (`clean`), and its tree must equal the commit's tree in the task's checkout. The record says
  what the entry covers: its one test file.
- Board text is data: nothing read from the board enters a key, a hit or a verification.
  Notice failures are printed and never fail a run.

## Failure modes

- An unrecorded input (see the known gaps) serves a stale pass until the canary, a tier run or a
  gate run catches it.
- Store missing or unreadable: every key misses and the runner executes.
- Two runners finish one key: each writes an entry; `latest` names the later pass.
- Runner killed mid-run: its claim is removed by the next finder; no entry is written.
- Job runner killed: `status` reports the job failed.
- Chain compaction and entry pruning are not implemented; the store grows by one entry per passing
  file run.
