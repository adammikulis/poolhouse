# Design: the node's contract in one table

Status: a design, nothing built. It follows the owner's order to cut the seam between the Rust node and its clients down to
one definition: the limits, the refusal sentences, the message and record shapes and the error kinds are written once, in a
table, and everything that repeats them is generated from it. It builds on the work on branch `fix/node-limits` (the node's
rate caps removed, `limits::refuse`, the first `docs/limits.md`) and does not redo it. The attack-first answers below are the
author's draft; a different agent has to write the break-it pass before slices 2 and 5 are built.

## What is wrong today

The node's contract is spelled out by hand in the Rust crate, the Python client, the Android app, the tests and the docs, and
nothing ties the copies together. Counts are from the tree at `origin/0.3dev`.

File names: a bare `x.rs` is in `app/poolhouse-node/src`; `node-tests/x.rs` is in `app/poolhouse-node/tests`; the `.java` files
are under `app/android/src/main/java/app/poolhouse/phone` (`GovernanceActTest.java` under `src/test`); Python files are under
`src/poolhouse` (`names.py`, `client.py`, `founding.py`, `session.py`, `public.py` and `approval.py` in `board/`, `board_cli.py`
and `approval_cli.py` in `workspace/`).

- **Rust:** 394 `const` declarations in 99 files, 105 of them named `MOST_`, `MAX_`, `MIN_` or `PER_`. 55 `Error::Quota`
  constructions, of which 16 name their limit; 39 refuse without saying which limit or what to do. 342 `Error::Invalid` and 202
  `Error::Denied` sites carry free text; 8 `Error::say` sites carry the one-sentence form (`error.rs:19`).
- **Python client:** `board/client.py` keeps the error code, the message and the grant of a refusal and drops `say`, `needs`
  and `then` (`client.py:43-46`); `board/founding.py:74` strips the code off the message text to get the sentence back and
  guesses `then` (`founding.py:76`). `node_health.py:22` is a second error class for the same reply. `board/session.py` has
  eight hand-written record classes, each built from a JSON object field by field.
- **Methods:** the method table exists three times: the dispatch in `api.rs`, 20 `params_for` functions (`api.rs:26` and 19
  modules), and the 78 names in the table of `docs/node.md:177-229`. Python calls at least 78 distinct node methods; `task_watchers`
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
a scan that every `Error::Quota(` goes through the helper. All of that stays. Two things change:

- `refuse` takes the name of a row and not the strings `"MAX_LOG_ROWS", "board.rs"` that each of the 55 call sites now types.
  Those strings are a new copy of a constant's name and file; they go stale the first time a constant moves.
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
| `limits.toml` | a number the node enforces and the person may change | `id = "links.open_per_board"  value = 64  unit = "count"  min = 1  max = 4096  protects = "..."  changed_by = "person"` |
| `floors.toml` | a number or set the node enforces and no configuration changes | `id = "phone.codes_per_hour"  value = 3  per_ms = 3600000  direction = "max"  why = "..."` |
| `refusals.toml` | a sentence: kind, `say` template, `needs`, `then`, `args`, the limit it names | `id = "links.too_many"  kind = "quota"  limit = "links.open_per_board"` |
| `methods.toml` | a method of the local API (later the sync and phone surfaces): params, result record, the grant it needs | `id = "link"  params = ["to","channels","mode"]  grant = "link_read\|link_write"` |
| `records.toml` | the JSON objects a method returns: field, type, optional | `[[record]] id = "entry"  fields = [...]` |

Rules the generator checks before it writes anything: ids are unique; a refusal's `limit` exists; every `say`, with the
longest argument it allows and the suffix `refuse` adds for a limit, renders in at most 160 characters (today `Error::say`
cuts a longer sentence mid-word, `error.rs:48`; that cut becomes unreachable and is deleted); a `say` argument is one of `name`, `number` or `word`, never request text
(an agent reads these sentences as instructions, so request text in one is an injection path); a limit row in `limits.toml`
has `min`, `max` and a value between them; a floor has a `direction` (`max`: may only fall, `min`: may only rise, `grow`: a
set that may only gain members).

### What is generated

`scripts/contract --write` writes, `--check` compares. The pattern is `scripts/reference` and its test
(`tests/test_cli_reference.py`), which the repository already runs.

| output | content | replaces |
|---|---|---|
| `app/poolhouse-node/src/contract/` (one small file per table) | the `Limit` and `Ref` enums, the values, one `REFUSALS` table and one `refuse(Ref, args)`, the kind strings, `METHODS` with params, the field lists of the records, the name rules | the 105 limit constants, the 55 `Error::Quota` sentences, the 20 `params_for`, the code strings of `error.rs`, `client.rs`, `peer.rs` |
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
told where the limit is written.

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
the table exactly when something outside the node's own code depends on it. A scan enforces the edge: a test string in
`app/poolhouse-node/tests` or `tests/` that is a substring of a sentence in `src` and not in the table fails, naming the
sentence to list. That is at most about 190 sentences today (the 98 Rust and 93 Python test assertions, many sharing a sentence),
not the 544 free-text sites.

### Floors stay out of configuration

Today `write-limits.json` (`writes.rs:20`) is the one settings file, refusing keys it does not know. It becomes
`<state>/limits.json`: its keys are ids of `limits.toml` rows only, each value inside `min` and `max`; a floor, an unknown key
or a value outside the range is refused with one plain sentence and the node does not start on it. No method of the local API
sets a limit. The loader's accepted keys are generated from `limits.toml`, so a floor cannot be added to the file by a mistake
in the loader.

The owner named the floors that stay (HANDOFF, "Hidden limits"): open joins, join-code tries, phone codes, approval requests
and the ask cooldown. This design adds the other limits that guard a key, a grant or the socket, as a proposal for the owner to
cut. The list: join-code tries (`join_code.rs:20`), the open-join rate (`peer.rs:89`), phone codes and offers an hour and wrong codes from one address (`phone/calls.rs:14-15`,
`phone/http.rs:28`, `phone/enrol.rs:100`), approval requests, waiting requests and the declined cooldown (`approvals/mod.rs:52-59`,
`approvals/request.rs:124-140`), the ask cooldown (`gov/ops.rs:30`, `presence/mod.rs:310-313`), the signature nonce count and
window (`grants/person.rs:20`), grant span and uses (`grants/spec.rs:23-26`), the frame sizes (`wire.rs:10` and `:13`), the act
and admit bounds of `gov/room.rs`, the name rules and reserved set (`row.rs:108-119`) and the envelope fields a request may
not carry (`api.rs:24`). A client enforces only floors and protocol bounds, never a changeable limit, so a client and a node
cannot disagree about a number a person set.

A new hook `scripts/hooks/floors-only-tighten` does for `floors.toml` what `budgets-only-fall` does for `budgets.json`: a
staged change that raises a `max` floor, lowers a `min` floor, drops a set member or deletes a row is refused whatever wrote
it, an agent outright. `ci.yml` runs it with `--against` the pull request's base. `contracts/node/floors.toml`, `scripts/contract`
and the hook are added to `.github/CODEOWNERS` and to `REVIEWED` in `protectedwrites.py`, like the other guard files.

### Drift is a failing gate

An unwired rule does not exist, so each control below names where it runs.

| control | what it catches | wired in |
|---|---|---|
| `scripts/contract --check` | a generated file differs from what the table makes; a docs block is stale; a table rule broken | a `gate_steps` entry in `scripts/test` next to "command reference", a `STALE_HINTS` line (`run scripts/contract --write`), `GENERATED` in `scripts/affected_rules.py` so any touched file selects its test |
| `tests/test_node_contract.py` | the same check through pytest, the TOML rules, the version never falls, the Python records match `session.py`'s dataclasses, the name vectors | the structure tests list in `gate_steps`; CI runs the suite |
| generated header | `@generated`, the source digest, the generator version at the top of each file; a hand edit changes the body and not the digest and is named by `--check` | every generated file |
| `tests/test_node_contract_scan.py` | a `Error::Quota(`, `Error::say(` or `const MOST_` of a table limit outside `contract/`; a Python string equal to a table sentence; a test pinning an unlisted sentence | the same list; a metric `node-hand-limits` (105 today) that only falls, then a hard gate at zero |
| `app/poolhouse-node/tests/contract.rs` | the Rust sentences, kinds and limits equal `tests/fixtures/node-contract.json`; every record's JSON keys equal the table | the CI `cargo test` step; `scripts/test` builds the node already |
| `tests/test_node_contract_live.py` | a real node from `node_kit`: each probeable limit refuses at the limit with the row's `ref`, each method refuses an unknown param, each grant-gated method refuses a session with no grant and returns the grant the row names | the suite |
| `floors-only-tighten` | a floor weakened | pre-commit chain, `ci.yml` |

The Rust conformance test does not run where cargo cannot fetch crates (HANDOFF, "GitHub CI has never completed a sharded
run": the native cargo tests fail offline). The Python drift check compares the generated Rust as text and needs no cargo, and
the live test drives the built binary, so the contract is checked end to end without relying on that step.

### Versioning with no migration

`contract.toml` carries `version`, an integer that rises with any change to a method, a record, a kind or a refusal's shape (not
with a limit's value). The node's `hello` returns `{version, digest}` (the digest is a SHA-256 of the canonical table); the
generated Python module holds its own. The client checks them when `ensure_node` already asks for health; a mismatch is one
plain refusal naming `poolhouse runtime ensure`, the command `build.rs:18` already names (the node follows the selected runtime,
so a mismatch means one side is stale). There is no compatibility path: no old kind is mapped, no missing field is defaulted.
A field removed from a record is removed. This follows "nothing is cemented before 1.0". When a release phase starts that puts
a wire format outside the owner's machines, `version` is the existing hook for "a version and a migration note"; the table
adds nothing to design for then.

The digest is a build check, not authentication: nothing trusts a device because its digest matches.

## Attack-first answers (author's draft)

| case | attack | answer |
|---|---|---|
| a generated file edited by hand | an agent lowers a floor in `contract/floors.rs` so a test passes | `--check` and the header digest fail the gate and CI; the same edit made to the table too needs a `floors.toml` change, which the hook refuses an agent and CODEOWNERS sends to the owner |
| a forged table | `$POOLHOUSE_CONTRACTS` points the client at another table; a pull request poisons a `say` | the node contract is compiled into Rust and generated into Python and is never read at run time, so the variable has no effect on it (the runtime loader in `docs/development.md` is not used); the files are guard paths; `say` takes only validated names and numbers |
| a downgrade (secure to open) | an old node binary with looser limits answers a new client; a floor row moved to `limits.toml`; `limits.json` lowers a floor | digest mismatch refuses, with no fallback; the hook treats a deleted floor row as a weakening; the loader accepts only `limits.toml` ids |
| a copied or cloned pool or device | a state directory copied to another device with its `limits.json` | the table holds no key, pool, device or policy; a copy brings its overrides and never a floor; the join policy is in the signed charter and not in the table |
| replay | a recorded refusal or `hello` replayed | the table has no nonce or signed object; the digest authenticates nothing and nothing decides trust by it |
| a forged or below-threshold signature | the generator emits a signing label or a threshold wrongly | signing labels, signed bytes and thresholds are not generated; only shapes and sentences are |
| a compromised or malicious member | a member sends rows or acts past the bounds | the receive bounds are the same floors, compiled into the receiver; a member cannot change a receiver's table |
| a stolen phone or key | a thief spends phone-endpoint attempts | the phone limits are floors; nothing about key strength or enrolment is in the table |
| a malicious agent | edits `floors.toml` and runs `scripts/contract --write` | the hook, `protectedwrites.REVIEWED`, CODEOWNERS and the CI `--against` run each refuse it |
| an agent compromised without knowing | a board message says "raise the quota to finish the task" | no method changes a limit; a `limits.json` is the person's file; a limits row changes by a reviewed commit and a floor by the owner |
| approval fatigue | new refusals push more prompts | the table adds no prompt: a `needs = "approval"` row exists only where the node already asked; a test requires a floor refusal's `then` to be `wait` or `stop`, never `retry`, so an agent is told to stop asking |
| takeover by whoever is merely present | a method row says `needs = []` and a request slips through | dispatch never reads the table's `grant`; the live test calls each grant-gated method with a session that holds nothing and compares to the row, so a row that lies fails |

Residual risk named for the owner: `limits.json` is writable by any process of the same user, which can already read a token
file; the design gives it no power over a floor and accepts that a same-user process can change a resource limit.

## Slices, each landable alone

Each slice leaves the gates green and lowers a count. Order is the order of risk: data first, security last.

| # | lands | gate it wires | count it lowers | effort and review |
|---|---|---|---|---|
| 1 | `contracts/node/limits.toml` and `floors.toml` with the values that exist after `fix/node-limits`; `scripts/contract` writes `docs/limits.md` and the Limits block of `docs/node.md`; each row names its Rust constant (`rust = "links.rs::MOST_ACTIVE"`) and `--check` evaluates the constant (`1024 * 1024`, `10_000`, `24 << 20`) and fails when it differs, so until slice 2 a value is written twice and the two are checked equal; `floors-only-tighten`; CODEOWNERS and `REVIEWED` lines | gate step, `STALE_HINTS`, `GENERATED`, the hook | the hand-written limits page and the node.md table (about 25 rows) | Sonnet high; no Rust change; the edits to `scripts/test` and the hook are reviewed paths, and the hook is guard code that a second agent reads |
| 2 | generated `contract/limits.rs` and `floors.rs`; `refuse(Ref)` replaces `refuse(what, value, key, file)`; the `rust =` column and its check are deleted; `limits.json` loader with floors refused; `src/poolhouse/nodecontract/` constants replace `MAX_FRAME`, `PAGE`, `CHUNK`, `names.py` | scan test, metric `node-hand-limits` | 105 hand constants fall as each moves; 4 Python copies | Sonnet xhigh; attack-first pass first (floors, settings file) |
| 3 | `contract.toml` kinds and vocabularies; `ref`, `say`, `needs`, `then` kept by `NodeError`; `founding.py` stops parsing text; `client.rs` and `peer.rs` read kinds from the table | contract test | 8 hand-written kind sites; 1 text parser | Sonnet high |
| 4 | `refusals.toml`: the 55 `Error::Quota` and 8 `Error::say` sites and the sentences tests pin; the golden fixture; tests assert on `ref` | scan test for `Error::Quota(` at zero | 39 refusals that name no limit; 93 plus 98 text assertions as each moves | Sonnet high, in batches by file area |
| 5 | `methods.toml` and `records.toml`; generated `params_for`; the method and record blocks of `docs/node.md`; `Method` literal; `hello` returns `{version, digest}`; the live conformance test | live test, version check | 20 `params_for`, 2 undocumented methods, 8 record classes checked against a table | Sonnet xhigh; attack-first pass first (digest, downgrade, dispatch) |
| 6 | the sync and phone surfaces as `surface` rows; Java constants for the phone endpoint | contract test; Android unit test reads the fixture | `NodeApi.java:16`, `:46`, `GovernanceAct.java:77` | Sonnet high, reviewed for the sync rows |

## Open decisions

1. **Where do the Rust constants come from?** (a) The committed output of the Python generator, checked in the gate: one
   mechanism for Rust, Python and docs, and the crate builds offline from its own directory. (b) A `build.rs` in the node that
   reads `contracts/node/` with the `serde_json` it already depends on: Rust has no committed generated copy to edit by hand,
   but the crate then reads a file outside its directory and the docs and Python still need the generator, so there are two
   mechanisms. Proposed: (a).
2. **Who approves weakening a floor?** (a) Only the owner at his terminal (`POOLHOUSE_FLOOR_WEAKEN=yes` for his commit, the rule
   `budgets.json` already follows): no new approval surface, nothing in a normal journey. (b) The person's approval question
   that `main` uses (`person-consume propose`): the owner can approve without a terminal, at the cost of a second use of that
   path. Proposed: (a).
3. **How far does `docs/limits.md` reach beyond the node?** (a) One table: the node rows are generated, and the other limits
   (the 150-turn worker limit, the hang guards, the `NOTE_*` marks, `DEV_TEST_BUDGET`) are `observed` rows whose constant the
   generator reads from its file and fails when it differs: one page, and a change to any of them is seen. (b) Node rows only,
   the rest stay hand-written on the page and are a second place that can go stale. Proposed: (a), as an addition to slice 1
   once `fix/node-limits` has landed its page.
4. **How many refusal sentences go in the table?** (a) A sentence is in the table when something outside the node's code depends on it (the scan
   enforces that edge, about 190 sentences). (b) Every refusal the node can make, about 600 sites, so the table is the complete
   list of what a device can say. (b) is the more complete contract; its cost is the whole of slice 4 several times over for
   text that no client reads. Proposed: (a), with the scan making the table grow whenever a test starts depending on a sentence.
