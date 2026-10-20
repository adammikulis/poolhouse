# Working on Poolhouse

## Fresh clone setup

Follow [CONTRIBUTING.md](../CONTRIBUTING.md) to create an isolated virtual environment, install
`.[test,privacy]` as a regular wheel, install the spaCy English model and pinned gate tools, and
run `python scripts/preflight dev commit gate`. Tests read the checkout's `src/` tree; an editable
install is unnecessary. Hook installation checks commit dependencies before writing any hooks.

Contributors submit a pull request and affected test evidence. Maintainers handle authenticated
queue landing, publication and installed-runtime verification. A local test run needs no owner
board membership, pool credential or real keystore.

## `contracts/` is data, not code

`contracts/` holds JSON describing things a runtime and a non-Python host both need to
agree on: the RAM→model tier ladder, the sampler surface, GBNF grammars. It contains no
code, so a native or scripting host can read it directly.

There is exactly one copy on disk. The wheel pulls it in at build time,
so there is no synced duplicate in the source tree to drift.

Resolution order at runtime: `$POOLHOUSE_CONTRACTS` → the copy inside the installed wheel → a
`contracts/` found by walking up from the source file. The walk-up is last on purpose: if a
wheel is installed *and* a repo happens to be an ancestor, the wheel's own data should win,
because that is what its version was tested against.

## Hooks

`python scripts/install-hooks.py` installs the Git hooks using that Python interpreter,
including on Windows, and preserves hooks managed by someone else. Run it from your clone using the prepared virtual environment. Maintainers refresh it from the
primary checkout after landing installer changes. The POSIX `scripts/install-hooks.sh` remains
available for existing shell setups. Installed hooks run the checks in the invoking checkout:
`no-real-names` refuses a commit whose staged files carry a person's name, `commit-msg`
refuses one whose message does. A third hook there is for Claude Code rather than git:
`scripts/hooks/claude-bash-guard` is a PreToolUse hook on Bash that refuses the shells
which keep getting written instead of Poolhouse commands -- a hand-written `pgrep` waiter,
`nohup`, `llama-server` started directly, `find`-ing for GGUFs, `hf download`, curl probes
at the model, killing llama by name, `SKIP_NAME_CHECK=1` -- and names the command to run
instead. Wire it into a project's `.claude/settings.json` (the docstring shows the JSON);
`POOLHOUSE_GUARD=off` disables it for a session. Both hooks are tested:
`tests/test_no_real_names.py` and `tests/test_bash_guard.py`.

The `no-real-names` hook needs the privacy extra: `pip install '.[privacy]' && python -m spacy download
en_core_web_sm`; with it the hook refuses a person it has never seen, not merely a known list. Invented
names go in `tests/known-fixtures.txt`. Both name hooks (`no-real-names`, `commit-msg`) read `NAMES_GRAPH`,
`NAMES_SCRAPE`, `NAMES_FIXTURES` and `PYTHON` from the environment, so a machine holding a local database of
names can wrap them with an untracked `.git/hooks/` script that exports those and execs the tracked one; the
installer leaves such a wrapper alone.

Set `git config --local pull.ff only` for editor sync: a divergent pull refuses before
creating a merge. Reconcile divergence in an integration worktree, then land with a reviewed
fast-forward. The pre-push hook refuses dirty checkouts and unfinished Git operations for
people and agents.

Coding tasks own their branch and worktree through assignment, claim, review, integration
and cleanup ([task lifecycle](tasks.md)). A reviewed coding proposal is accepted; completion
requires the landed commit and verified removal of its worktrees and merged branches.

The worktree rule is in `src/poolhouse/worktreerules.py` and reaches every harness. Claude Code
runs `claude-edit-guard` (Write, Edit, MultiEdit, NotebookEdit) and `claude-bash-guard` before
a tool call; both refuse a write inside the primary checkout (the first entry of `git worktree
list`), a git command that changes its tree (`add`, `commit`, `checkout`, `switch`, `reset`,
`restore`, `stash`, `rebase`, `cherry-pick`, `am`, `apply`, and any `merge` that is not
`--ff-only <branch>`) and a `pip install -e` of a tree that is not the primary checkout.
`claude-subagent-start` (SubagentStart) puts the rule and the development branch in each
subagent's context, and `.claude/settings.json` sets `worktree.baseRef` to `head` so worktrees
branch from the development branch. `poolhouse.harnesshook pre` applies the same refusal to Codex
and local-model sessions the launchers start (add a `[[hooks.PreToolUse]]` entry running it to a
Codex config the launchers did not write), and `scripts/hooks/primary-only` refuses a commit by an
agent in the primary checkout or on the development branch. Tests: `tests/test_worktree_rules.py`.
`claude-wrap-up` (PostToolUse) counts each subagent's tool calls in an append-only file under the hook state
directory, injects a wrap-up instruction as additional context at 120 calls and again at 140, and
`claude-subagent-stop` clears the count. Tests: `tests/test_wrap_up_hook.py`.

The paths an agent never writes, and the shell forms that write them, are in `src/poolhouse/protectedwrites.py`,
which `worktreerules` loads for both guards and for the local-model harness. The guards, `.claude/settings*.json`,
`.claude/agents`, `commands`, `skills` and `hooks`, git's hooks and config, the person record's code and the
authority registry are refused to the file tools and to a shell redirection, `tee`, `cp`, `mv`, `ln`, `rm`,
`sed -i`, `dd`, `find -delete`, `-fprint` or `-exec` of anything but a plain reader, `git checkout` and `restore` of
such a path, `bash -c`, `eval`, command substitution, a heredoc or a script file handed to an interpreter, with
variables, `~`, braces, globs, `cd` and links resolved (a new file under a link to a guard folder is a guard) and
letter case ignored. A `mv` or `git mv` is refused for what it takes away or writes over, not for the folder it moves
a file into, and a `chmod -R` that only adds permissions is not refused. The person record and the session tokens are not read or listed either. `git config core.hooksPath`
and the other settings that decide what git runs, `gh pr merge` without the person's release approval, and `gh` or
`curl` writing to rulesets, secrets, releases or settings are refused. `scripts/hooks/claude-tool-guard` applies
the same paths to `Read`, `Glob`, `Grep` and MCP tools, refuses the owner's own browser, and lets only the main
session stop a task. Keystore, system approval and release
authorization remain person-only. Tests: `tests/test_protected_writes.py`, `tests/test_protected_tools.py`.

What `no-real-names` takes for a name, and what stands a name-shaped pair down, is data:
`contracts/name-shapes.json` holds the place prefixes and suffixes (a gazetteer's
"North Carolina", "Colorado River"), the job-title endings (a role catalogue's
"Software Engineer"), the `no-real-names: shapes off` file marker and the data-file
suffixes it implies, the RFC 2606 reserved domains, the `noreply` mailboxes, the uuid
and name patterns, and the file suffixes never read -- each section with a `why` saying
what it is for and when it was learned. A refusal names the rule (`patterns: nameish;
nothing stood it down`), and `NAMES_WHY=1` (or `python -m poolhouse.redact.hook --why`)
prints, for every pair a rule cleared, which section and which word did it
(`'North Carolina' cleared by place_first: north`), so the next exception is a word added
to a known section rather than a code change. `NAMES_SHAPES=path.json` reads another
rules file instead of the shipped one.

### Hook failures and runtime cutover checks

The rules are in AGENTS.md, "Hook failures are active incidents" and "Updating to a fix"; this is
the procedure.

After changing or updating a runtime, exercise the installed hook commands for pre, post and
stop under the owning authenticated session. Check the actual launcher interpreter, the
distribution revision, the structured output and the exit status: source tests and a daemon
health response do not establish which hook runtime is active. Preserve any diagnostic ID and
the redacted local record. Fix a hook regression and repeat these checks before continuing work
that depends on it. Notification outages must warn without blocking completed local tools;
independent authorization and unfinished-work completion checks remain enforced. Inspect records
with `poolhouse-doctor hooks [ID]` or, without workspace imports,
`python -m poolhouse.hook_diagnostics [ID]`. These bounded local incident logs preserve failure
evidence while workspace services or graph startup are unavailable. The signature summary
retains first occurrence time, checkout branch/HEAD, installed runtime revision and repeat count
across detail rotation. Record runtime cutover checks in the handoff.

When a development hook fails repeatedly, or the owner asks to fix it:

- Pause unrelated work and assign one owner to diagnosis, installation and verification.
  Coordination must not delay a ready repair. Keep independent review bounded to the affected
  hook; do not wait for a UI batch, unrelated refactor, cleanup or broad suite.
- Reproduce the exact installed command immediately. Record elapsed time, exit status, stdout,
  stderr, configured timeout, interpreter path and installed version/commit. Compare the
  running artifact with the proposed source fix before further edits. Never substitute a
  checkout test for verification of the hook the owner is using.
- If measured duration approaches the host timeout, promptly apply an authorized timeout
  adjustment with a backup while correcting the slow path. Keep notification work bounded
  below that timeout, avoid redundant authentication and subprocesses, and terminate owned
  timed-out children. Notifications must warn and return successfully when the daemon is
  unavailable; independent authorization and completion checks retain their enforcement.
- Failures must emit a credential-redacted diagnostic reference before context truncation and
  preserve local records independently of the daemon: first occurrence, branch/HEAD, installed
  provenance, stage timings, reason and recurrence count. Never swallow a failure or proceed as
  though a broken hook succeeded.
- Complete the authorized build, isolated installation and exact-command smoke before
  requesting any required host trust review. Preserve active jobs, original configuration and
  rollback artifacts. Never fabricate a trust hash or bypass authorization to update to a change.
- Completion requires the actual configuration selecting the repaired artifact, required trust
  approval saved, and a successful installed invocation on the available hardware. Verify the
  normal host-triggered hook too; report any remaining reload or verification step. A source
  commit, review, passing test or prepared runtime is only a checkpoint.

## Python versions

Development and the required CI jobs use Python 3.13 (`.python-version`). The library supports
3.12 and later (`requires-python`); one CI job runs the suite on 3.14, may fail, and shows breakage
early. The app builds its own environment on 3.13 (`fleet.environment.PYTHON`, the installers). Code in `src/` and `tests/` uses nothing newer than 3.12:

- generics use PEP 695 (`class C[T]`, `def f[T]`, `type X[T] = ...`), not `Generic` and `TypeVar` (ruff UP046, UP047)
- `os.waitid` is missing on macOS before 3.13

`vermin -t=3.12- --no-tips src tests scripts` lists what a change added.

## WSL development environment

Run `scripts/setup-wsl-dev` from the primary checkout in WSL. It fills the persistent
`~/.venvs/poolhouse-dev` environment with the compatible `[wsl-dev]` dependency bundle using a
regular wheel install, then configures Bash to use that environment and the checkout's live `src/`
tree. It also adds existing Windows model stores and Hugging Face, Ollama, LM Studio, llama.cpp,
and Jan caches to `POOLHOUSE_MODEL_PATHS`, so WSL reuses downloaded weights.

`[wsl-dev]` includes the Linux-compatible runtime and test extras plus the pinned Ruff and Pyright
versions used by the structural gates. The setup script also installs MetaDrive from the source
revision pinned in [the Gym guide](studio-gym.md). `[viz]` conflicts with `[gym-warehouse]`, and
`[privacy-transformers]` conflicts with `[train-lora]`; those optional combinations stay in
separate environments. The Apple-only `[mlx]` and opt-in `[pdf-agpl]` are also excluded.

The setup is repeatable after dependency changes. Open a new shell or run `source ~/.bashrc`
after the first setup. `POOLHOUSE_WSL_VENV` selects another persistent venv, and
`POOLHOUSE_WINDOWS_USER` selects the matching Windows profile when its name differs from the WSL
login.

## Testing

`scripts/preflight` says what a machine still lacks for a job and the command that installs it
(`scripts/test` runs it first); [environment.md](environment.md) is the table.

```
python scripts/test all tests/<affected-file>.py -n 1
```

Agents test their own changes through `scripts/test`, which defaults to one worker and uses
shared broker admission. Explicit `-n 0` requests an automatic pool; explicit worker ceilings
remain effective. The main agent runs shared structural/security gates once per consolidated
integration batch before publication. The full suite runs on GitHub for pull requests, `main` and nightly (a development push runs only the `gates` job); an agent never runs it.

| tier | what it runs | about |
|---|---|---|
| `scripts/test quick` | the tests a change reaches (import graph, then testmon's map) | under a minute for one module |
| `scripts/test gate` | the structural checks a merge is gated on: budgets, red-team coverage, command reference, layers, the human-floor tests | a minute |
| `scripts/test fast` | everything not marked `slow` or `heavy` | |
| `scripts/test full` | everything not marked `slow`; writes its wall and CPU time for the ratchet | 3 minutes on an idle 16-core machine |
| `scripts/test all` | slow included: what CI runs | |
| `scripts/test ratchet` | compares the last `full` run with `tests/full-tier-time.json` (10 % tolerance) | |

The main agent owns `quick`; its cold-map recording and fallback can start full runs.
Workers use explicit affected selectors. `quick` needs a per-checkout testmon map (`.testmondata`, not committed). The first `quick` in a
checkout runs the test files the import graph reaches and starts `scripts/test record` detached
(three workers, a lock file, a log in `.testmondata.log`); the map is written to a scratch file and
moved into place when the run ends, so the second `quick` is fast and never sees half a map. A
change to something no module maps (`pyproject.toml`, `tests/conftest.py`, ...) still runs the full
tier.

`tests/full-tier-time.json` is the full tier's wall time (at a worker count) and the sum of every
test's own time. A later run more than 10 % over either fails `scripts/test ratchet`; wall time is
compared only at the same or a higher worker count. `scripts/test ratchet --update` records a
lower time and refuses a higher one (`--allow-increase` is the owner's, refused for an agent). Where
the time went, and the causes already fixed: `docs/experiments/test-durations.md`.

Rules that keep the suite fast and not flaky:

* A test never nests a slot lease. A test that runs `scripts/test-on-linux` or
  `testslots.py run` inside a full run waits for slots the run holds (it deadlocked a whole run
  for seven minutes); give the child `DEV_TEST_WORKERS` so it skips the queue.
* A test that checks a failure path does not wait out the production timeout: pass a small one.
* Servers a test starts shut down in milliseconds (conftest polls every 20 ms); do not add sleeps
  to wait for them.
* The real-state-root guard (`tests/conftest.py`, `LIVE_PATHS`) fails a run when a file under
  `~/.poolhouse` changed. Other agents and runs append to a few logs while yours is going, so only
  `workspace/`, `harness/`, the activity log and the sentinel's event and anchor logs are tolerated.
  The keystore, every `.key` file, manifests, canaries, honey, requests and credentials stay guarded
  (`tests/test_live_paths.py`). The guard cannot tell which process wrote a tolerated log; a test
  that escapes into one of those paths is not caught.
* A test never depends on a neighbour having run first (a module fixture it forgot to request, a
  process-wide observer): run it alone before you push.

Nothing here mocks the transport. Every client test runs a real `http.server` on a real
socket, because the failures these modules exist to prevent are transport-shaped: a server
that answers `/health` while still loading, one that ignores a `Range` header, one that
returns 500 on a concurrent request. A mocked `urlopen` reproduces none of them.

The fleet tests go further: they boot real `poolhouse-traind` subprocesses and speak real
UDP on a real interface, on randomised ports so a run never answers -- or gets answered
by -- a daemon you actually have running on your LAN. A forged beacon, a replayed reply,
a multicast group a router quietly drops: a fake socket reproduces none of those either.

What *is* faked -- the model's answers, a `serve()` that would load 87G, a preflight that
would read it -- is faked once, in `poolhouse.testing.fakes`, with the real signature.
`FakeClient` is built exactly as `Client` is built, `fake_serve` / `FakeServe` take what
`serve()` takes, `FakePreflight` returns a real `Report`, and `ScriptedModel` replays tool
calls through `Client.chat`'s signature. None takes a `**kwargs` the real one lacks: a fake
that accepts every keyword lets a test pass on a keyword the real thing refuses, which is
how a `--also tight` flag once reached `Client.__init__` in a benchmark and took the load
down with it. `tests/test_testing_fakes.py` diffs every fake's signature against the real
one (`mirrors`, `drift`), so a change to the real one fails the suite until the fake follows.

## Commit history by type

`python3 scripts/commit_types_chart.py [--branch 0.2dev] [--bucket day|week|month] [--out commit-types.png]`
draws the share of each commit type (feat, fix, chore, and the rest) over time as a 100% stacked
area chart from `git log`, with no network. It writes the PNG to the current directory by default.
Needs the `plot` extra (matplotlib).
