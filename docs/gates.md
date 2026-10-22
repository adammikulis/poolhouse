# The gates

How the structural checks in `scripts/gates/` work. The rules that bind agents (a budget is a debt, nothing is
grandfathered, no agent raises a number) are in [AGENTS.md](../AGENTS.md), "The gates"; this file holds the
mechanics. The main agent runs `scripts/test gate`, `scripts/budgets` and the other shared checks once per
consolidated integration batch (AGENTS.md, "Scoped merge gates and background verification").

## Budgets

`budgets.json` holds the highest count each shape in `scripts/gates/` is allowed. `scripts/budgets` prints metric,
budget, actual and delta, a total under the table (what the budgets add up to, what the tree holds, the distance
between them) and every site that is over. `tests/test_budgets.py` fails when a number rises, and also when it
falls without being recorded, so a branch that lowers one runs `scripts/budgets --update` and commits the file;
`--update` refuses to raise a number. `scripts/budgets --show METRIC` lists the sites, and `SKIP_BUDGETS=1` skips
the pre-commit check. The checkers keep their results per file, and pyright's per import closure, in a
machine-local cache under the temporary directory, so an edit re-reads the files it can change and the counts
equal a full run; `POOLHOUSE_GATES_FULL=1` ignores the cache.

`scripts/hooks/budgets-only-fall` closes the other door: a staged `budgets.json` whose numbers rose is refused
whatever wrote it, and a metric dropped from the file counts as a rise, because the next `--update` puts it back
at whatever the tree holds. An agent is refused outright; `POOLHOUSE_BUDGET_RISE=yes` is for the owner's own
commit. The pre-commit chain is opt-in, so `ci.yml` runs the same check with `--against` the pull request's base.
When `budgets.json` cannot be read at the base (absent, not JSON, or the base commit not fetched) the hook prints
`ratchet not checked` with the reason; against a revision it exits 1, and a commit whose staged copy is unreadable
is refused, while a commit over an unreadable `HEAD` copy goes on after the notice.

## What the gates do not read

A gate result depends on the change under test, never on the machine around it: no checker reads other
worktrees or another agent's state. A worktree left behind is listed by `scripts/worktrees` and removed by
`scripts/worktrees sweep` when it is landed (docs/worktrees.md).

## Entries that are not ceilings

`floors-only-rise` holds `tests-collected`: it may not *fall*, because a module that stops being collected leaves
the suite still saying passed. `scripts/budgets` returns 1 when the count is under the floor or any module failed
to collect, and `--update` refuses to record a count taken while one did. The count is only comparable where every
extra is installed, so on a machine missing one it says so rather than reporting a low number. `module-skips`
counts the other way in: a skip *outside* a test function takes a whole file out of collection, which the floor
cannot see coming. `ratchet` is the date and total the debt is measured from, `RATCHET` at the top of
`scripts/budgets` is the share it should fall by each month, and the scoreboard prints what is due and whether the
tree is on track. It refuses nothing: a repo-wide debt must never block an unrelated branch, which is the pressure
that gets gates gamed.

## Layers and wiring

`tests/test_layers.py` sets out core, model, machine, graph, tools, and reads every import including the ones
inside functions. A package imports downwards, and sideways only when the other does not import it back. `KNOWN`
lists what still crosses; it only shrinks. `tests/test_wiring.py` requires every package to be imported by
something, back a console script, or be named in `STANDALONE` with a reason. Code nothing calls is a gap to close,
never a reason to delete.

## Size gates and duplicates

The size gates carry no number. `deep-files` (900 lines of Python) and `deep-components` (500 lines of the HTML,
JavaScript and CSS a page is assembled from) set `HARD = True`, take no line in `budgets.json`, and fail on a
single finding. A file over the limit is a file to split, never an allowance to record.
`handoff-size` is a ratchet: every line of `HANDOFF.md` past line 500 is one finding and the count in `budgets.json` only falls, to zero; `scripts/budgets` prints a note from line 300. `HANDOFF.md` lists what is pending and nothing else, so a finished item is deleted in the commit that lands its work.
`scripts/gates/duplicates.py` hashes normalised function bodies and reports each duplicate with its twin.
`tests/test_gates_duplicates.py` names duplicates that must still be found, so a normalisation that quietly
tightens is caught.

## Nothing listens beyond loopback in development

macOS asks the person to allow every new binary that listens on every interface, and again after every rebuild or new path
(a cargo test binary, a copy of the node in a test's temporary directory, a Python path of a new version). So development and
test code binds `127.0.0.1` and nothing else. `wildcard-binds` (`HARD`, no number) reads `src/poolhouse`, `scripts`, `tests`,
`examples` and `app/**/*.rs`, and fails on a listener, a `--host`/`--listen`/`--bind` word, a `(host, port)` address, a
`host=` argument or a Docker `-p` with no address that names the wildcard (`0.0.0.0`, `::`, `''` as a host). The files in
`scripts/gates/wildcard_binds.py` `LAN` are the network on purpose (the pool daemon's LAN mode, the node's `--lan` listener
and beacon, `add-device`, `share`, the rig's containers), each with its reason, and they listen beyond loopback only for the
installed software on the account's real state root (`poolhouse.lan.allowed`). `tests/network_guard.py` refuses the same
at run time inside a test process, and `app/poolhouse-node/tests/loopback_only.rs` guards the node's default tests.

## Mutation

`scripts/mutate` samples functions out of `src/poolhouse`, changes one thing each (a flipped comparison, a swapped
`and`, a negated or dropped branch, a constant return, an emptied body) and runs the test files that name the
module, in a copy of the tree made from `git ls-files`. It is seeded by the commit, so a given commit samples the
same functions every time. A mutation the tests keep green is a survivor: those tests do not read what the function
answers. `scripts/gates/survivors.txt` holds the survivors already found, `mutation-survivors` counts the rows whose
function is still in the tree, and `scripts/mutate --verify` re-runs every row and says which the tests now catch.
A mutation that cannot change what anyone observes is recorded as `equivalent` with the reason.

`mutation-survivors: 0` means no recorded survivor is left, not that the tests catch everything: the run is a
sample, so `scripts/mutate` writes a `campaign` row saying what it sampled and how many functions it did not
mutate, and `scripts/budgets` prints the same caveat under the table. A deeper `--mutations` finds more mutations
of a function already sampled, so a function with a row is not one that is done. A module no test file names is
measured by nothing: `scripts/mutate --unmeasured` lists them, and each is a test file to write. `from
poolhouse.sources import rows` does not name `poolhouse.sources.rows`, so a well-tested module can sit in that list.

## Ruff and pyright

`pyproject.toml` selects ruff's rules and pyright's checks, and `scripts/gates/` budgets both: `ruff-blind-except`,
`ruff-bugbear`, `ruff-security`, `ruff-other`, `pyright-errors`. Neither tool is a dependency, so a checker that
cannot find its tool prints why and its metric is left out rather than counted as zero.

A whole pyright run over `src/poolhouse` takes 80 to 100 seconds (it checks 1252 files; `include` already leaves out
worktrees, examples, vendored assets and build output), so `pyright-errors` is incremental: findings are kept per file
and only files whose import closure changed are re-checked. The `executionEnvironments` in `pyproject.toml` that only
add `extraPaths` are followed as extra import roots; any other setting there runs the whole project. The cache is
keyed by content and shared by every worktree of one repository, so a new worktree starts warm. `POOLHOUSE_GATES_FULL=1`
(CI) forces the whole run.

## Writes the edit guard and the hooks refuse

Writes are refused when they add a function whose body already exists elsewhere, a raw HTTP call, a docstring over
twelve lines, a signature over eight parameters, or a write that takes a file over its line limit or makes an
already-over file longer. `scripts/install-hooks.sh` installs the pre-commit chain.
