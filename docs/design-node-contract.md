# Design: the node's contract in one table

Status: a design, nothing built. It follows the owner's order to cut the seam between the Rust node and its clients down to
one definition: the limits, the refusal sentences, the message and record shapes and the error kinds are written once, in a
table, and everything that repeats them is generated from it. It builds on the work on branch `fix/node-limits` (the node's
rate caps removed, `limits::refuse`, the first `docs/limits.md`) and does not redo it. A non-author review accepted it with
edits; they are applied here and listed in "Review log". The attack-first answers below are the author's draft; a different
agent has to write the break-it pass before slices 2b and 5 are built.

## What is wrong today

The node's contract is spelled out by hand in the Rust crate, the Python client, the Android app, the tests and the docs, and
nothing ties the copies together. Counts are from the tree at `origin/0.3dev`.

File names: a bare `x.rs` is in `app/poolhouse-node/src`; `node-tests/x.rs` is in `app/poolhouse-node/tests`; the `.java` files
are under `app/android/src/main/java/app/poolhouse/phone` (`GovernanceActTest.java` under `src/test`); Python files are under
`src/poolhouse` (`names.py`, `client.py`, `founding.py`, `session.py`, `public.py` and `approval.py` in `board/`, `board_cli.py`
and `approval_cli.py` in `workspace/`).

- **Rust:** 394 `const` declarations in 99 files, 105 of them named `MOST_`, `MAX_`, `MIN_` or `PER_`. 55 `Error::Quota`
  constructions, of which about 17 name their limit; the other 38 or so refuse without saying which limit or what to do. 342 `Error::Invalid` and 202
  `Error::Denied` sites carry free text; 8 `Error::say` sites carry the one-sentence form (`error.rs:19`).
- **Python client:** `board/client.py` keeps the error code, the message and the grant of a refusal and drops `say`, `needs`
  and `then` (`client.py:43-46`); `board/founding.py:74` strips the code off the message text to get the sentence back and
  guesses `then` (`founding.py:76`). `node_health.py:22` is a second error class for the same reply. `board/session.py` has
  eight hand-written record classes, each built from a JSON object field by field.
- **Methods:** the method table exists three times: the dispatch in `api.rs`, 19 parameter tables (18 `fn params_for`, in `api.rs` and
  17 modules, and `poolapi::params`), and the 78 names in the table of `docs/node.md:177-232`. Python calls at least 78 distinct node methods; `task_watchers`
  and `phone_remove` are named nowhere in `docs/node.md`.
- **Tests:** 98 assertions in 25 Rust test files match refusal text; 93 `pytest.raises(..., match=...)` in 27 Python test
  files do the same; 131 Python test files use `tests/node_kit.py`, which runs a built node.
- **Docs:** `docs/node.md` states a limit in prose or in its Limits table (`:579-608`) on about 40 lines.

Ten examples, each a fact one person has to change in several places:

| # | what | defined | copies |
|---|---|---|---|
| 1 | frame limit, 1 MiB | `wire.rs:10` | `node_health.py:21`, `NodeApi.java:16`, `docs/node.md:152` and `:604`, `tests/test_remote_transport_attacks.py:56` and `:88` |
| 2 | the 7 reserved names | `row.rs:119` | `names.py:10`, `GovernanceAct.java:77`, `node-tests/phone_act_vectors.rs:47`, `GovernanceActTest.java:64`, `docs/earned-trust.md:95` |
| 3 | a name is at most 48 characters | `row.rs:113` (a literal) | `identity.rs:76` (another use of 48), `names.py:9` (`{0,47}`), `GovernanceAct.java:77` (`{0,47}`) |
| 4 | the six error codes | `error.rs:32-40` | `client.rs:56-58`, `peer.rs:333-336`, `client.py:45`, `board_cli.py:48` (exit codes), `founding.py:74`, `NodeApi.java:46`, `docs/node.md:173` |
| 5 | `say`, `needs`, `then` | `error.rs:19-20`, `api.rs:293` | `docs/node.md:174-175`; Python never reads them from the reply |
| 6 | 10,000 sessions a board | `identity.rs:16` | `docs/node.md:105` and `:589` |
| 7 | 8 tokens a name | `identity.rs:17` | `docs/node.md:104` and `:590`, `node-tests/lineage.rs:72` (the word "eight") |
| 8 | file chunk, 256 KiB | `files.rs:27` | `session.py:26`, `docs/node.md:192`, `files.rs:106` (inside a sentence) |
| 9 | 3 phone codes an hour | `phone/calls.rs:14` | `phone/calls.rs:90`, `phone/http.rs:104` (a literal 3) and `:106` ("3 codes"), `docs/node.md:601` |
| 10 | a note is 160 characters | `approvals/request.rs:22` | `public.py:253`, `approval.py:150`, `approval_cli.py:34`, `docs/node.md:174` and `:600`, `node-tests/approvals.rs:318`; `error.rs:48` truncates a different thing at the same number |

Four of the ten cross three languages (Rust, Python, Java). The read page size is the same kind of copy: `node.rs:36` and
`session.py:25`, named nowhere in `docs/node.md`. The copies already disagree: the local client's reply parser (`client.rs:56-58`)
has no `gap` and the sync client's (`peer.rs:333-336`) has it, so one maps a `gap` refusal to `invalid` and the other does not.

## What `fix/node-limits` does and where this takes over

That branch removes the rate caps and, at the time of writing, holds uncommitted work that adds `limits::refuse(what, value,
key, file)` (the one place a quota sentence is built) and moves the `Error::Quota` sites onto it; it plans `docs/limits.md` and
a scan that every `Error::Quota(` goes through the helper. All of that stays. It has not landed: `origin/0.3dev` has no
`docs/limits.md` and no `refuse` helper, and the branch exists only as `fix/node-limits`. Slice 1 is not started until it has
landed (check `git log origin/0.3dev` and `docs/limits.md` first). Two things change:

- `refuse` takes the name of a row and not the strings `"MAX_LOG_ROWS", "board.rs"` that each of the 55 call sites now types.
  Those strings are a new copy of a constant's name and file; they go stale the first time a constant moves. Slice 2b makes it
  `refuse(Limit)`, a variant of the generated limit enum; `refuse(Ref)` waits for `refusals.toml` in slice 4.
- `docs/limits.md` is generated from the table instead of kept by hand, so the hand-written version is the first content of the
  table (its "protects" and "who changes it" columns become fields). Slice 1 starts after that branch lands.

## The design

### One source: `contracts/node/`

Six TOML files in the existing `contracts/` directory, read by one Python generator with the standard library (`tomllib`, in
Python 3.12 and up, which the project requires). Every row has an `id` of the form `area.thing`; the ids are one namespace, and
they are the names used everywhere else (the Rust enum variants, the Python constants, the docs, the `ref` on the wire, the
keys of the settings file).

| file | rows | example |
|---|---|---|
| `contract.toml` | `version`, the error kinds (code, Python class, exit code, whether it may mark an origin damaged), the words of `needs` and `then` | `[[kind]] code = "quota"  exit = 4` |
| `limits.toml` | a number the node enforces that the person may change, from a short explicit list; also the `protocol` and `format` rows, which no configuration changes | `id = "links.open_per_board"  value = 64  unit = "count"  min = 1  max = 4096  protects = "..."  changed_by = "person"` |
| `floors.toml` | every other number or set the node enforces; no configuration changes it | `id = "phone.codes_per_hour"  value = 3  per_ms = 3600000  direction = "max"  unit = "count"  why = "..."` |
| `refusals.toml` | a sentence: kind, `say` template, `needs`, `then`, `args`, the limit it names | `id = "links.too_many"  kind = "quota"  limit = "links.open_per_board"` |
| `methods.toml` | a method of the local API (later the sync and phone surfaces): params, result record, the grant it needs | `id = "link"  params = ["to","channels","mode"]  grant = "link_read\|link_write"` |
| `records.toml` | the JSON objects a method returns: field, type, optional | `[[record]] id = "entry"  fields = [...]` |

Classification defaults to floor. A constant the scan finds in `src` with a limit name (`MOST_`, `MAX_`, `MIN_`, `PER_`, `SKEW_`)
is a floor unless its id is on the short tunable list at the head of `limits.toml`; a name in neither file fails the check, so a
constant added later is a floor until someone decides otherwise. Two named kinds sit outside the person's reach without being
floors: `protocol` (a bound both ends must agree on, such as the local `MAX_FRAME`) and `format` (a shape rule, such as a name's
48 characters); both change by a reviewed commit and `limits.json` cannot name them.

Rules the generator checks before it writes anything: ids are unique; a refusal's `limit` exists; a limit's `max` is at most
any floor that bounds it (a tunable limit cannot be set above a floor that bounds the same thing);
the nonce window is at least twice `SKEW_MS`, so a signature inside the clock skew cannot be replayed after its nonce ages out; every `say`, with the
longest argument it allows and the suffix `refuse` adds for a limit, renders in at most 160 characters (today `Error::say`
cuts a longer sentence mid-word, `error.rs:48`; the cut is deleted in slice 4, once the 8 `Error::say` sites move to the table); a `say` argument is one of `name`, `number` or `word`, never request text
(an agent reads these sentences as instructions, so request text in one is an injection path), and a `name` is quoted or
delimited in the template (`the name "{name}"`), so a name that reads like an instruction is shown as a value; a limit row in `limits.toml`
has `min`, `max` and a value between them; a floor has a `direction` (`max`: may only fall, `min`: may only rise, `grow`: a
set that may only gain members) and a `unit`; a rate is a value and a `per_ms` and is compared as value over `per_ms`.

### What is generated

`scripts/contract --write` writes, `--check` compares. The pattern is `scripts/reference` and its test
(`tests/test_cli_reference.py`), which the repository already runs.

| output | content | replaces |
|---|---|---|
| `app/poolhouse-node/src/contract/` (one small file per table) | the `Limit` enum and the values (slice 2a), `refuse(Limit)`, the `Ref` enum with one `REFUSALS` table and `refuse(Ref, args)` (slice 4), the kind strings, `METHODS` with params, the field lists of the records, the name rules | the 105 limit constants, the 55 `Error::Quota` sentences, the 19 parameter tables, the code strings of `error.rs`, `client.rs`, `peer.rs` |
| `src/poolhouse/nodecontract/` | `Final` constants for the limits a client enforces, the kind classes and exit codes, a `Literal` of method names, a `TypedDict` per record, the name regex and reserved set | `MAX_FRAME`, `API_VERSION`, `PAGE`, `CHUNK`, `names.py`, the dict at `client.py:45`, `board_cli.py:48`, the text parsing of `founding.py` |
| `docs/limits.md` | the whole page: id, value in words, tier, where it is enforced (file, not line), what it protects, who changes it | the hand-written page |
| `docs/node.md` between markers | the Limits table, the method table, the error kinds, and each number in prose between `<!--c id-->64<!--/c-->` | about 40 hand-typed statements |
| `tests/fixtures/node-contract.json` | every refusal rendered with sample arguments, every limit, the name vectors | the sentence strings copied into tests |
| later: `NodeApi.java` constants | the phone endpoint's kinds and limits | `NodeApi.java:16`, `:46` |

One function and one table in Rust, not a function per refusal: 55 near-identical function bodies would trip the duplicate
body gate and add nothing. A generated test renders every row with its declared arguments, so a wrong argument count fails.

The file line of an enforcement site is left out of `docs/limits.md`: a line number changes with any edit of that file and
would turn the drift check red for unrelated work. The file name and the row id are stable; `grep` finds the line.

### Wire and client changes

One refusal, from the table to the wire. `links.rs:58` today builds `Error::Quota(format!("a board has at most
{MOST_ACTIVE} links open: end one first"))`. After slice 4 the table holds

```toml
[[refusal]]
id = "links.too_many"  kind = "quota"  limit = "links.open_per_board"  needs = "none"  then = "retry"
say = "A board has as many links open as it may: end one first."
```

the call site is `return Err(refuse(Ref::LinksTooMany, &[]))`, and the reply is

```json
{"ok": false, "error": {"code": "quota", "ref": "links.too_many", "needs": "none", "then": "retry",
  "limit": {"id": "links.open_per_board", "value": 64},
  "say": "A board has as many links open as it may: end one first (links.open_per_board is 64; see docs/limits.md).",
  "message": "quota: A board has as many links open as it may: end one first (links.open_per_board is 64; see docs/limits.md)."}}
```

`refuse` appends the parenthesis from the limit row and the value in force, so no template repeats a number and the person is
told where the limit is written. The `limit.value` field and the parenthesis are left out of a reply on an unauthenticated
surface (the phone endpoint, a peer): a stranger is told that a limit was reached and which row, never the number to work to.
Any refusal on such a surface is in the table whether or not a test pins it, so its words are reviewed once.

- A refusal in the table is always an `Error::Say`: the reply carries `code`, `message`, `ref` (the row id), `say`, `needs`
  and `then`; a limit refusal also carries `limit` (id and value). `message` stays `code: say`. The Python `NodeError` keeps
  all of it, so `founding.py` stops parsing text and a test asserts on `err.ref == "links.too_many"` and not on words.
- The Python client's own refusals (the frame check at `client.py:90`, the no-token refusals in `session.py`) are rows with
  `origin = "client"` and are generated as constructors, so the client says things in the same shape.
- Python call sites take `Method` (a `Literal`), so pyright checks the about 125 `client.call("name")` sites against the table.

### What stays hand-written

The table says what is refused and in which words. It never decides who is allowed. Authorization (grants, lineage, the
person's key), the signed and hashed layouts (the log `Row`, charters, acts, signing labels), the merge and sync logic, and
the Python wrappers (`Session.post`, the dataclasses) stay code. The `grant` column of a method is documentation and a live
test (below); dispatch never reads it. The log `Row` is listed in `records.toml` for the docs only, with a test that its serde
field names equal the table's, so a field added in Rust and not in the table fails.

Refusal text that no client, test or document depends on stays an ordinary `Error::Invalid(format!(...))`. A sentence is in
the table exactly when something outside the node's own code depends on it. A scan enforces the edge: it reads the `match=` argument of `pytest.raises` and the message assertions of the Rust tests that
check a node error (not every substring of a test file), and one that equals a sentence in `src` and is not in the table fails,
naming the sentence to list. That is at most about 190 sentences today (the 98 Rust and 93 Python test assertions, many sharing a sentence),
not the 544 free-text sites.

### Floors stay out of configuration

Today `write-limits.json` (`writes.rs:20`) is the one settings file, refusing keys it does not know. It becomes
`<state>/limits.json`: its keys are ids of the tunable rows of `limits.toml` only, each value inside `min` and `max`; a floor,
a `protocol` or `format` row, an unknown key or a value outside the range is refused with one plain sentence and the node does
not start on it. The node reads the file once, at start; nothing reloads it. No method of the local API sets a limit. The
loader's accepted keys are generated from `limits.toml`, so a floor cannot be added to the file by a mistake in the loader.
`limits.json` (and `write-limits.json` until slice 2b renames it) is added to `STATE_DENY_WRITE` in
`scripts/agent_sandbox_profile.py:39`, with a test in `tests/test_agent_sandbox.py` that the profile denies a write to it, so an
agent in the sandbox cannot edit the person's settings.

**What is a floor.** Every limit-named number is a floor unless it is on the short tunable list (see "One source"), and the
envelope of the tunable rows is ratcheted too: a `max` only falls and a `min` only rises. The owner named five floors (HANDOFF,
"Hidden limits"): open joins (`peer.rs:89`), join-code tries (`join_code.rs:20`), phone codes (`phone/calls.rs:14`), approval
requests and the ask cooldown (`gov/ops.rs:30`, `presence/mod.rs:310-313`). This design adds, each because it guards a key, a
grant or the socket:

- wrong codes from one address (`phone/http.rs:28`, `phone/enrol.rs:100`) and phone offers an hour (`phone/calls.rs:15`);
- waiting approval requests and the declined cooldown (`approvals/mod.rs:52-59`, `approvals/request.rs:124-140`);
- the signature nonce count and window (`MOST_NONCES`, `grants/person.rs:20`) with the clock skew they depend on (`SKEW_MS`,
  `grants/spec.rs:25`), and the number of keys a person may enrol (`MOST_KEYS`, `grants/person.rs:18`);
- grant span and uses (`MAX_SPAN_MS`, `MOST_USES`, `grants/spec.rs:23-26`);
- the peer frame (`MAX_PEER_FRAME`, `wire.rs:13`);
- the reserved name set (`row.rs:119`) and the envelope fields a request may not carry (`api.rs:24`).

Not floors: the local `MAX_FRAME` (`wire.rs:10`) is a `protocol` row; the name length of 48 (`row.rs:113`) is a `format` rule that
sits with the name rules; the act and admit bounds of `gov/room.rs` stay hand-written constants, outside the table, until each
row names the member abuse it stops, because a floor with no named abuse is a number nobody can judge a change to. A client
enforces only floors and protocol bounds, never a changeable limit, so a client and a node cannot disagree about a number a
person set.

**What stops a floor being weakened.** A new hook `scripts/hooks/floors-only-tighten` does for `floors.toml` and the envelope of
`limits.toml` what `budgets-only-fall` does for `budgets.json`, with the gaps of that hook closed (it passes a deleted or
renamed staged file, because it lists only `--diff-filter=ACMR`):

- it refuses a deleted, renamed or unparseable `floors.toml`, and a floor id that moves to `limits.toml`, which it treats as a
  deletion;
- it reads the TOML itself with `tomllib` and never through the generator, so a table edit followed by
  `scripts/contract --write` cannot turn it green;
- it compares a rate as value over `per_ms`, refuses a change to `direction` or `unit`, and refuses a raised `max`, a lowered
  `min`, a dropped set member or a deleted row, whatever wrote it;
- `POOLHOUSE_FLOOR_WEAKEN=yes` is the owner's terminal bypass and is refused when any agent marker is set (`CLAUDECODE`,
  `POOLHOUSE_AGENT`, any `CODEX_*`) or there is no terminal; the bash guard blocks the `-n` form for Claude Code only, so the
  hook checks for itself.

**Where it runs, and what each place is worth.** The hook runs in three places and they are not equal:

| place | what it does | limit |
|---|---|---|
| `scripts/hooks/pre-push` calls it over the pushed range | refuses an agent's push to the development branch before it leaves | client side; it holds only where the hook runs |
| a `ci.yml` step on the `push` event, `--against ${{ github.event.before }}` | detects a weakened floor on the development branch that agents push to directly (`docs/github-protection.md`, "Development branch ruleset"); the CI watcher posts the red run to the pusher, who reverts it | after the push has landed: a detector |
| the `ci.yml` pull request step, `--against` the base, and on the `promote/<date>` pull request `--against main` | the owner's real gate: a floor cannot reach `main` without that check green and the owner's approval of the promotion | needs the pull request, which a promotion always is |

`budgets-only-fall` today runs in CI only `if: github.event_name == 'pull_request'` (`ci.yml`, "budgets.json only falls") and is
not in `pre-push`, so a push to the development branch is not checked by it at all. Slice 1 adds the `push`-event step and the
`pre-push` call for `floors-only-tighten` and adds the same two for `budgets-only-fall`. The pre-commit chain is opt-in and is
not counted. CODEOWNERS bites only where the repository applies `--owner-review` (`docs/github-protection.md`, "Optional:
GitHub enforces the owner's approval"), and `REVIEWED` in `protectedwrites.py` feeds only the release authorization check
(`workspace/person_release.py:44`) and the legacy `scripts/land_review.py`; `contracts/node/floors.toml`, `scripts/contract` and
the hook are added to both, and neither is relied on as the barrier. CI runs the hook from the trusted base overlay (`ci.yml`,
"the base commit's gate over this tree", which copies `scripts/hooks`, `scripts/gates` and `scripts/budgets` from the base
checkout before anything runs); `scripts/contract` is added to that overlay, so a pull request cannot edit the check that
judges it.

### Drift is a failing gate

An unwired rule does not exist, so each control below names where it runs.

| control | what it catches | wired in |
|---|---|---|
| `scripts/contract --check` | a generated file differs from what the table makes; a docs block is stale; a table rule broken | a `gate_steps` entry in `scripts/test` next to "command reference", a `STALE_HINTS` line (`run scripts/contract --write`), `GENERATED` in `scripts/affected_rules.py` so any touched file selects its test |
| `tests/test_node_contract.py` | the same check through pytest, the TOML rules, the version never falls, the Python records match `session.py`'s dataclasses, the name vectors | the structure tests list in `gate_steps`; CI runs the suite |
| generated header | `@generated`, the source digest, the generator version at the top of each file; a hand edit changes the body and not the digest and is named by `--check` | every generated file |
| `tests/test_node_contract_scan.py` | a `Error::Quota(`, `Error::say(` or `const MOST_` of a table limit outside `contract/`; a limit-named constant in neither table file; a Python string equal to a table sentence; a test pinning an unlisted sentence; any file under `src/` or `scripts/` that opens `contracts/node` through `contracts.loader` | the same list; a metric `node-hand-limits` (105 today) that only falls, then a hard gate at zero |
| `app/poolhouse-node/tests/contract.rs` | the Rust sentences, kinds and limits equal `tests/fixtures/node-contract.json`; every record's JSON keys equal the table | `cargo test` in the Windows CI job only (see below) |
| `tests/test_node_contract_live.py` | a real node from `node_kit`: each probeable limit refuses at the limit with the row's `ref`, each method refuses an unknown param, each grant-gated method refuses a session with no grant and returns the grant the row names | the suite; it fails, and does not skip, when no node binary is built |
| `floors-only-tighten` | a floor weakened (see "Floors stay out of configuration") | `pre-push`, the `push` and `pull_request` steps of `ci.yml`, the `--against main` run on the `promote/<date>` pull request |

**What verifies the Rust.** `cargo test --locked` runs only in the Windows CI job (`ci.yml`, "crate tests of poolhouse-node"); the
other jobs do not run it. The Python drift check compares the generated Rust as text, so it proves the committed file is what
the table makes and nothing about whether it compiles or behaves. What verifies the Rust is: a local `scripts/test` that builds
the node, the live conformance test against that binary (a missing binary is a failure), and the Windows crate tests including
`contract.rs`. A change that touches `app/poolhouse-node/src/contract/` reports which of the three it ran.

### Versioning with no migration

`contract.toml` carries `version`, an integer that rises with any change to a method, a record, a kind or a refusal's shape (not
with a limit's value). The node's `hello` returns `{version, digest}`; the digest is a SHA-256 of the canonical wire-relevant
fields only (method names and params, record fields, kinds, the refusal ids and shapes, the limit ids), and leaves out `protects`,
`why` and other prose, so a reworded explanation does not make a client and a node disagree. The generated Python module holds
its own. The client checks them when `ensure_node` already asks for health. A mismatch does not reach a normal journey: after a
runtime update the client runs the safe `runtime ensure` itself (the command `build.rs:18` already names; the node follows the
selected runtime, so a mismatch means one side is stale), restarts its connection and carries on; it refuses, with one plain
sentence naming `poolhouse runtime ensure`, only when that ensure fails. There is no compatibility path: no old kind is mapped, no
missing field is defaulted. A field removed from a record is removed. This follows "nothing is cemented before 1.0". When a
release phase starts that puts a wire format outside the owner's machines, `version` is the existing hook for "a version and a
migration note"; the table adds nothing to design for then.

The digest is a build check, not authentication: nothing trusts a device because its digest matches.

## Attack-first answers (author's draft)

| case | attack | answer |
|---|---|---|
| a generated file edited by hand | an agent lowers a floor in `contract/floors.rs` so a test passes | `--check` and the header digest fail the gate and CI; the same edit made to the table too needs a `floors.toml` change, which the hook refuses an agent on `push` and `pull_request` and the owner's review of the promotion catches before `main` |
| a forged table | `$POOLHOUSE_CONTRACTS` points a reader at another table; a pull request poisons a `say` | the node contract is compiled into Rust and generated into Python and never read at run time. The wheel does ship `contracts/node/` (`pyproject.toml:257` force-includes the whole `contracts` directory) and `contracts.loader.contracts_dir()` honours `$POOLHOUSE_CONTRACTS`, so `scripts/contract` reads the repository path only and never the loader, and a scan test fails any file under `src/` or `scripts/` that opens `contracts/node` through it; `say` takes only validated, quoted names and numbers |
| a downgrade (secure to open) | an old node binary with looser limits answers a new client; a floor row moved to `limits.toml`; `limits.json` lowers a floor | digest mismatch triggers `runtime ensure` and refuses if that fails, with no fallback; the hook treats a floor id moving to `limits.toml` as a deletion; the loader accepts only the tunable ids of `limits.toml` |
| a copied or cloned pool or device | a state directory copied to another device with its `limits.json` | the table holds no key, pool, device or policy; a copy brings its overrides and never a floor; the join policy is in the signed charter and not in the table |
| replay | a recorded refusal or `hello` replayed | the table has no nonce or signed object; the digest authenticates nothing and nothing decides trust by it |
| a forged or below-threshold signature | the generator emits a signing label or a threshold wrongly | signing labels, signed bytes and thresholds are not generated; only shapes and sentences are; the nonce window is at least twice `SKEW_MS` by a generator rule |
| a compromised or malicious member | a member sends rows or acts past the bounds | the receive bounds are the same floors, compiled into the receiver; a member cannot change a receiver's table |
| a stolen phone or key | a thief spends phone-endpoint attempts | the phone limits are floors; a phone or peer reply carries no `limit.value`; nothing about key strength or enrolment is in the table |
| a malicious agent | edits `floors.toml` and runs `scripts/contract --write`; deletes or renames the file; edits the table and the hook's input together | the hook reads the TOML itself, refuses a deleted, renamed or unparseable file, and refuses the bypass variable under any agent marker or without a terminal; it runs in `pre-push` and on the `push` event from the trusted base overlay, and `--against main` on the promotion |
| an agent compromised without knowing | a board message says "raise the quota to finish the task" | no method changes a limit; `limits.json` is the person's file and is in `STATE_DENY_WRITE`; a limits row changes by a reviewed commit and a floor by the owner |
| approval fatigue | new refusals push more prompts | the table adds no prompt: a `needs = "approval"` row exists only where the node already asked; a test requires a floor refusal's `then` to be `wait` or `stop`, never `retry`, so an agent is told to stop asking |
| takeover by whoever is merely present | a method row says `needs = []` and a request slips through | dispatch never reads the table's `grant`; the live test calls each grant-gated method with a session that holds nothing and compares to the row, so a row that lies fails |

Residual risk named for the owner: `limits.json` is writable by any process of the same user outside the agent sandbox, and that
process can already read a token file; the node reads the file at start only, so a write takes effect at the next restart and
never in a running node. The design gives the file no power over a floor and accepts that such a process can change a resource
limit. The weakening of a floor on the development branch is detected after the push (the `push` step) and reverted by the
pusher; it is prevented only on a pull request and on promotion.

## Slices, each landable alone

Each slice leaves the gates green and lowers a count. Order is the order of risk: data first, security last.

| # | lands | gate it wires | count it lowers | effort and review |
|---|---|---|---|---|
| 1 | needs `fix/node-limits` landed first (it has not, see above). `contracts/node/limits.toml` and `floors.toml` with the values that exist after it; `scripts/contract` writes `docs/limits.md` and the Limits block of `docs/node.md`; each row names its Rust constant (`rust = "links.rs::MOST_ACTIVE"`) and `--check` evaluates the constant with a throwaway Rust-expression evaluator (`1024 * 1024`, `10_000`, `24 << 20`) and fails when it differs; so until slice 2a every value is written twice, in the table and in Rust, and the evaluator and the `rust =` column are deleted with 2a; `floors-only-tighten` with its `pre-push` call and `push`-event step, the same two for `budgets-only-fall`, `scripts/contract` added to the trusted overlay; CODEOWNERS and `REVIEWED` lines | gate step, `STALE_HINTS`, `GENERATED`, the hooks | the hand-written limits page and the node.md table (about 25 rows) | Sonnet high; no Rust change; the edits to `scripts/test`, `ci.yml` and the hooks are reviewed paths, and the hook is guard code that a second agent reads |
| 2a | generated `contract/limits.rs` and `floors.rs` and `refuse(Limit)` replacing `refuse(what, value, key, file)`; the `rust =` column and its evaluator deleted; `src/poolhouse/nodecontract/` constants replace `MAX_FRAME`, `PAGE`, `CHUNK`, `names.py`. Mechanical | scan test, metric `node-hand-limits` | 105 hand constants fall as each moves; 4 Python copies | Sonnet high; no new behaviour |
| 2b | the `limits.json` loader (tunable ids only, floors refused, read at start), `STATE_DENY_WRITE` entry and its test, the envelope ratchet | loader test, sandbox test | `write-limits.json`'s own key list | Sonnet xhigh; attack-first pass of its own first (floors, settings file, same-user writer) |
| 3 | `contract.toml` kinds and vocabularies; `ref`, `say`, `needs`, `then` kept by `NodeError`; `founding.py` stops parsing text; `client.rs` and `peer.rs` read kinds from the table | contract test | 8 hand-written kind sites; 1 text parser | Sonnet high |
| 4 | `refusals.toml` and `refuse(Ref)`: the 55 `Error::Quota` and 8 `Error::say` sites, the sentences tests pin, and every refusal on a phone or peer surface; `Error::say`'s 160-character cut deleted; the golden fixture; tests assert on `ref` | scan test for `Error::Quota(` at zero | the 38 or so refusals that name no limit; 93 plus 98 text assertions as each moves | Sonnet high, in batches by file area |
| 5 | `methods.toml` and `records.toml`; generated `params_for` (19 tables), the method and record blocks of `docs/node.md`; `Method` literal; the live conformance test. The version and digest check in `hello` is a separate commit, reviewed on its own, because a version check is a fail-closed allowlist and a generated `params_for` is a mechanical table | live test | 19 parameter tables, 2 undocumented methods, 8 record classes checked against a table | Sonnet xhigh; attack-first pass first (digest, downgrade, dispatch) |
| 6 | the sync and phone surfaces as `surface` rows; Java constants for the phone endpoint | contract test; Android unit test reads the fixture | `NodeApi.java:16`, `:46`, `GovernanceAct.java:77` | Sonnet high, reviewed for the sync rows |
| 7 | `contracts/observed.toml`: the other limits (150-turn worker limit, hang guards, `NOTE_*`, `DEV_TEST_BUDGET`), check-only | contract test | the hand-kept rows of `docs/limits.md` | Sonnet high |

## Decisions made

1. **Where the Rust constants come from:** (a), the committed output of the Python generator with a digest header, verified in
   the gate. One mechanism for Rust, Python and docs, and the crate builds offline from its own directory.
2. **Who approves weakening a floor:** (a), hardened as under "What stops a floor being weakened" (the bypass refused under
   any agent marker or without a terminal), plus the `--against main` check and the owner's review on the `promote/<date>`
   pull request. No new approval surface and nothing in a normal journey.
3. **How far `docs/limits.md` reaches beyond the node:** (a), in a separate file outside `contracts/node/`
   (`contracts/observed.toml`), check-only (the generator reads each constant from its file and fails when it differs), out of
   the node digest, and its own later slice (7).
4. **How many refusal sentences go in the table:** (a), a sentence is in the table when something outside the node's code
   depends on it, and also every refusal on an unauthenticated surface (phone, peer) whether or not a test pins it. The scan
   reads `match=` and message assertions for node errors, not substrings.

## Review log

A non-author review answered ACCEPT with edits. Each finding was checked against the tree at `origin/0.3dev` before it was
applied.

| finding | checked | applied |
|---|---|---|
| H1 the hook's backstops are weaker than stated | `budgets-only-fall` runs in `ci.yml` only `if: github.event_name == 'pull_request'`; `scripts/hooks/pre-push` does not call it; agents push the development branch directly; CODEOWNERS bites only with `--owner-review`; the `REVIEWED` patterns are read through `touches_guards` by `workspace/person_release.py:44` and `scripts/land_review.py:62` only; pre-commit is opt-in; the trusted overlay copies `scripts/hooks`, `scripts/gates`, `scripts/budgets` | "Where it runs, and what each place is worth": a `push`-event step (`--against ${{ github.event.before }}`), a `pre-push` call, the real owner gate named as `--against main` on the promotion pull request, `scripts/contract` added to the overlay; slice 1 carries both and the same two for `budgets-only-fall` |
| H2 deleted or renamed files pass; bypass too open | `staged_names` lists `--diff-filter=ACMR`, so a deleted file is not seen; the bash guard blocks the `-n` form for Claude Code only | "What stops a floor being weakened": deleted, renamed or unparseable refused, TOML read with `tomllib`, rate as value over `per_ms`, `direction` and `unit` frozen, a floor moving to `limits.toml` is a deletion, the bypass refused under any agent marker or with no terminal |
| H3 classification defaults to limit | `SKEW_MS` (`grants/spec.rs:25`), `MOST_NONCES` (`grants/person.rs:20`) and `MOST_KEYS` (`grants/person.rs:18`) exist and were not listed | default is floor with a short tunable list; the three added; envelope ratcheted in the hook; generator rules for `max` against a bounding floor and the nonce window against `2 * SKEW_MS` |
| M1 `limits.json` writable by an agent | `STATE_DENY_WRITE` (`scripts/agent_sandbox_profile.py:39`) does not hold it or `write-limits.json` | entry plus a sandbox test in slice 2b; same-user residual risk named; the loader reads once at start |
| M2 the digest covers prose; a mismatch must not hit a journey | the version check is new | wire-relevant fields only; the client runs `runtime ensure` and restarts itself, refusing only if that fails |
| M3 the wheel ships `contracts/node/` | `pyproject.toml:257` force-includes `contracts`; `loader.py` `_candidates()` honours `POOLHOUSE_CONTRACTS` | `scripts/contract` reads the repository path only; a scan test that nothing under `src/` or `scripts/` opens `contracts/node` through the loader; the forged-table row rewritten |
| L1 names in sentences; `limit.value` on unauthenticated replies | | names quoted or delimited in templates; `limit.value` and the parenthesis left off phone and peer replies |
| floors list | | five named plus the added ones listed; the local `MAX_FRAME`, the name length and the `gov/room.rs` bounds are not floors |
| slices | `fix/node-limits` has not landed (no `docs/limits.md`, no `refuse` helper on `origin/0.3dev`) | slice 1 waits on it and states the throwaway evaluator and the second copy; slice 2 split into 2a and 2b; `refuse(Limit)` in 2a, `refuse(Ref)` in slice 4; the `Error::say` cut deleted in slice 4; version check separate from the generated `params_for` in slice 5 |
| Rust verification | `cargo test --locked` appears once in `ci.yml`, in the Windows job | stated plainly under "What verifies the Rust"; the live test fails and does not skip without a binary |
| survey counts | 18 `fn params_for` plus `poolapi::params` is 19; the method table is `docs/node.md:177-232`; the `Quota` sites naming a limit are about 17 and the 55 holds | corrected |
| decisions 1 to 4 | | recorded under "Decisions made" |
