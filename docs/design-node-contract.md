# Design: the node's limits, refusals and methods in one table

Status: not built. The full text, with its ten worked examples, attack table, slice table and review log, is in git history at
`ac0639291` (`git show ac0639291:docs/design-node-contract.md`). What exists instead is the generated contract of data shapes
described in `docs/node-contract.md`: `contracts/node/` (`contract.toml`, `types.toml`, `operations.toml`, `adapters.toml`,
`lifecycle.toml`, `domains/`), the generator `scripts/generate_node_contract.py` (`--check` for drift) with `scripts/node_contract/`,
and `tests/test_node_contract*.py`. It generates Rust, Python and TypeScript types and vectors. It does not generate limits,
refusal sentences or the method and record tables; the hand-kept `docs/limits.md` and the tables of `docs/node.md` stand.

## The problem

The node's limits, refusal sentences, message and record shapes and error kinds are spelled out by hand in the Rust crate, the
Python client, the Android app, the tests and the docs, and nothing ties the copies together. At the time of writing there were 394
Rust `const` declarations (105 named `MOST_`, `MAX_`, `MIN_` or `PER_`), 55 `Error::Quota` sites of which only about 17 name their
limit, 19 hand-written parameter tables, eight hand-written Python record classes and about 40 lines of `docs/node.md` that state a
limit in prose. The copies already disagreed: the local client's reply parser lacked the `gap` kind the sync client's had.

## The design

- **One source, `contracts/node/`**, read by one standard-library generator. Rows have ids of the form `area.thing`, one namespace
  used everywhere: Rust enum variants, Python constants, docs, the `ref` on the wire, keys of the settings file. Files: `contract.toml`
  (version, error kinds, the words of `needs` and `then`), `limits.toml` (numbers the person may change, from a short explicit list,
  plus `protocol` and `format` rows nothing changes), `floors.toml` (every other number the node enforces), `refusals.toml`
  (sentence templates), `methods.toml` and `records.toml`.
- **Classification defaults to floor.** A limit-named constant is a floor unless its id is on the short tunable list; a name in
  neither file fails the check, so a constant added later is a floor until someone decides otherwise. A floor has a `direction`
  (`max`: may only fall, `min`: may only rise, `grow`: a set that only gains members) and a `unit`; a rate is a value and a `per_ms`.
- **Generated outputs, checked for drift:** the Rust `contract/` module (the `Limit` and `Ref` enums, `refuse(Limit)`, `refuse(Ref,
  args)`, `METHODS`), a Python `nodecontract` package (constants, kind classes, a `Literal` of method names, a `TypedDict` per
  record), `docs/limits.md`, marked blocks of `docs/node.md`, and a golden fixture of every refusal rendered. One function and one
  table in Rust, not a function per refusal, so the duplicate-body gate stays quiet.
- **A refusal reaches the wire whole:** `code`, `ref` (the row id), `say`, `needs`, `then`, and for a limit `limit` (id and value).
  `message` stays `code: say`. The Python `NodeError` keeps all of it, so callers assert on `err.ref` and not on words. On an
  unauthenticated surface (the phone endpoint, a peer) the reply names the row and never the number to work to.
- **A sentence is in the table when something outside the node's own code depends on it**, and every refusal on an unauthenticated
  surface is in it. A `say` argument is a name, number or word, never request text (an agent reads these sentences as instructions),
  and a name is quoted in the template. Every `say` renders in at most 160 characters.
- **The table says what is refused and in which words, never who is allowed.** Authorization, signed layouts, merge and sync
  logic and the Python wrappers stay code. A method row's `grant` is documentation plus a live test that a session with no grant is
  refused; dispatch never reads it.
- **Floors stay out of configuration.** `<state>/limits.json` accepts only the ids of tunable rows, each inside its `min` and `max`;
  a floor, an unknown key or an out-of-range value refuses start-up with one sentence. The node reads it once at start; no method
  sets a limit. The file is denied to an agent in the sandbox profile. The envelope of the tunable rows is ratcheted too.
- **Weakening a floor is a failing gate.** A hook reads the TOML itself and refuses a lowered floor, a deleted, renamed or
  unparseable file, and a floor moved to `limits.toml`; the bypass is refused under any agent marker or without a terminal. It
  runs in the pre-push hook, on the push event of CI from the trusted base overlay, and as `--against main` on the promotion pull
  request, where the owner's review is the real barrier.
- **Versioning with no migration.** `contract.toml` carries an integer `version` that rises with any change to a method, record,
  kind or refusal shape. `hello` returns the version and a digest of the wire-relevant fields only (not prose). A mismatch makes
  the client run `poolhouse runtime ensure` and reconnect; it refuses with one sentence only when that fails. No old kind is mapped
  and no missing field defaulted. The digest is a build check and authenticates nothing.
- **What verifies the Rust:** `cargo test --locked` runs only in the Windows CI job, and the Python drift check compares generated
  Rust as text. So the verification is a local `scripts/test` that builds the node, a live conformance test against that binary
  (a missing binary is a failure, not a skip) and the Windows crate tests.

## Slices (each landable alone, data first, security last)

1. `limits.toml` and `floors.toml` with the existing values, the generator writing `docs/limits.md` and the Limits block of
   `docs/node.md`, and the floors-only-tighten hook with its pre-push and CI wiring.
2. Generated Rust and Python constants and `refuse(Limit)` (mechanical), then the `limits.json` loader with its sandbox deny and
   envelope ratchet (an attack-first pass of its own first).
3. Error kinds and vocabularies from `contract.toml`; `founding.py` stops parsing text.
4. `refusals.toml` and `refuse(Ref)`: the 55 `Error::Quota` and 8 `Error::say` sites and every phone and peer refusal; tests assert
   on `ref`.
5. `methods.toml` and `records.toml`: generated parameter tables, the `Method` literal, the live conformance test; the `hello`
   version check in a separate commit (attack-first pass first).
6. The sync and phone surfaces and the Java constants.
7. `contracts/observed.toml`: the other limits (turn limits, hang guards, note sizes), check-only.

## Decisions made

1. Rust constants are the committed output of the Python generator with a digest header, verified in the gate: one mechanism for
   Rust, Python and docs, and the crate builds offline from its own directory.
2. Weakening a floor is approved by the owner's review of the promotion pull request; there is no new approval surface and nothing
   in a normal journey.
3. `docs/limits.md` reaches beyond the node through a separate check-only file, `contracts/observed.toml`, outside the node digest.
4. A refusal sentence is in the table when something outside the node's code depends on it, and always on an unauthenticated surface.

Residual risk, accepted: `limits.json` is writable by any process of the same user outside the agent sandbox, which can already read
a token file; a write takes effect at the next restart, never in a running node, and gives no power over a floor.
