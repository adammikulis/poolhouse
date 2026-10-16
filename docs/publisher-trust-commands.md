# Publisher trust commands: `poolhouse-models trust` and `distrust`

Status: design, 2026-10-16, revised after the first independent attack-first review (round 1 of at most 2). Nothing here is
built. It serves MOD-4 in `docs/release-readiness.md` section 3.6 and extends the publisher check of `docs/sentinel.md`
("Supply chain") and `docs/model-discovery.md` ("Publisher trust").

Tags: **[V path]** read in that code on `0.3dev` at `34e5e3aef`; **[proposal]** this design.

## 1. Summary

- A person adds or removes a model publisher without a source edit. The built-in list (`publishers.ORDER`) stays in
  source as the floor; a stored list adds names to it and `distrust` masks names out of it. [proposal]
- A stored name is a name only. It gets exactly the checks a built-in one gets: whole-owner match, the official Hub
  host, a commit that is a branch or tag head or in `main`'s first history pages, marker names ask. No stored field
  can lower one. [proposal]
- Three ways in. The non-technical path is a tap: a third choice on the `model_publisher` request the person already
  answers ("always trust this publisher"), and a `publisher_trust` request an agent proposes with
  `poolhouse-models stop-trusting OWNER` for the other direction. The power-user path is `poolhouse-models trust OWNER`
  and `distrust OWNER` at a terminal. A normal journey gains no prompt. [proposal]
- `distrust` removes trust first, then unpins what that publisher supplied, holds it in quarantine (released only by a
  person, never by a lead agent) and stops the servers using it. A pull that was running when `distrust` ran holds its
  own files when it finishes. [proposal]
- The list is sealed under the keystore and carries a generation. The floor that generation is checked against is one
  small item in the keystore, not sentinel's state, and nothing about the list reads sentinel's mode. A locked keystore
  refuses the pull with one sentence; a list that is missing, altered or older than the floor makes every publisher
  ask until a person starts it again. [proposal]
- The two questions the first draft left for the owner are decided in section 8 (Q1 and Q2): the list is a sealed file
  in `hub`, and an unreadable keystore refuses rather than asks. Nothing in this document waits for the owner.

## 2. What exists today

- `hub/publishers.py` holds `ORDER` (eight owners: `Qwen`, `unsloth`, `ggml-org`, `google`, `bartowski`,
  `lmstudio-community`, `mlx-community`, `litert-community`), `TRUSTED = frozenset(ORDER)`, `HOSTS = {huggingface.co, hf.co}`, `MARKERS`,
  and `valid_name`, `namespace`, `trusted`, `official`, `marker`. A name matches by whole owner, character for
  character; a name that is not an ASCII Hub name is not parsed. [V]
- `hub/admission.py` `admit(ref, files)` reads `publishers.TRUSTED` at call time, once, in `transfer._bring` before the
  space check and the download; it is the only reader. Reasons to ask: host not official; owner not in `TRUSTED` ("who
  Poolhouse does not know"); (only for a known owner) commit not on a branch, tag or the first pages of `main`
  (`remote.on_branch`); a marker in the repository or file names; a digest that differs from an earlier pull. It
  raises a human-only `model_publisher` request with choices `approve` and `deny`, fields `publisher`, `repo`,
  `commit`, `host`, and approves only a request whose subject, words and fields are exactly the ones it builds
  (`approvals.approved`). At most ten wait (`MOST_WAITING`). [V]
- Answering a `model_publisher` request passes `require_person` whatever the authority registry delegates; the inbox
  is one file sealed under a keystore subkey. A resolved request is dropped after seven days and 400 rows are kept
  (`requests/store.py`, `RETAIN_S`, `MAX_ROWS`), and `approved()` finds an approved row for as long as it is kept. An
  answer through the page or the dialog is checked only for the agent marker (`inbox.answer`). [V]
- A pull pins each file with `publisher`, `commit` and `source_ref` in the sealed manifest, at the end of the
  download, after the file is promoted (`net/download._pin_pulled`, through `Want.pin_notes` from `hub/transfer._notes`;
  `net` is a core package and cannot import `hub`). Nothing is pinned when sentinel's mode is `off`. `Manifest.unpin(path)`
  and `Store.quarantine((kind, key), reason, evidence, Holding(path=...), actor)` exist; a quarantined model file is
  moved aside and `adapters.stop_serving` stops the servers using it. `release` is `mint_gated("sentinel.quarantine",
  ...)`, which a lead agent passes while that gate is delegated (the dev preset). [V]
- `Store.quarantine` returns a record that is already `QUARANTINED` untouched; evidence is merged only when a record
  moves into quarantine (`sentinel/store.py:316-317`, `_move`). [V]
- Sentinel's `SealedFile` keeps its HMAC key in a file beside it, so a process of the same user can write a valid one
  (`sentinel/sealed.py:42-55`; HANDOFF, "A server started by a process of its own"). Its `load()` is `fresh` when
  neither the file nor its `.prev` exists, `recovered` with the previous copy's payload when the main file fails its
  seal, and `tampered` with `{}` when neither holds; `Store._load` then starts every counter at 0
  (`sentinel/store.py:166-177`). [V]
- `POOLHOUSE_SENTINEL=off` with `POOLHOUSE_SENTINEL_BECAUSE` sets mode `off` (`sentinel/core.py:103-108`); in that
  mode `admission._changed` returns nothing and `_pin_pulled` pins nothing. [V]
- The periodic sentinel dialog asks only about records whose reason is in `heads_up.NEEDS_PERSON` or carries a decoy
  (`needs_person`, `heads_up.py:77-79, 258-260`). `review()` and `poolhouse-security review` list every held record
  with `explain.why_of`, which falls back to "Sentinel recorded no plain-language reason" for a reason it does not
  know, and the Release button there is `human.mint_clicked`, a person's click. [V]
- The shipped list "has no add path" (`docs/sentinel.md`); `docs/sentinel.md` and `docs/model-discovery.md` name six
  owners, the code eight. [V]
- The desktop dialog shows at most three buttons, refusing choices first; its first label is the default and the
  answer to Escape (`requests/dialog.py buttons`, `desktop.mac_script`). [V]
- `hub` is in the model layer, `graph` above it; a new `hub` to `graph` import would add an edge to `KNOWN` in
  `tests/test_layers.py`, which may only shrink. `serve` (machine layer) is above `hub`. The graph is a derived index
  of a board's entries, rebuilt by `replace` and not restored from a copy (`docs/graph.md`); it is not sealed under the
  keystore and has no generation floor. [V]
- The keystore holds one master item per OS user (`docs/keystore.md`); a subkey is cut from it by `salted_subkey`.
  There is no API for another item. Reads of an existing item are limited to 600 an hour, creates and deletes to 5
  (`READ_CEILING`, `WRITE_CEILING`). A locked, denied, busy or absent backend raises `KeystoreLocked`, `KeystoreDenied`,
  `KeystoreBusy` or `KeystoreUnavailable`; a vanished master raises `KeystoreMissing`. [V]
- The keystore is open to Poolhouse code only when it is the installed software on the real home
  (`real_machine.py`). That is a check in Poolhouse code, not an operating-system boundary against a process that calls
  the OS keyring itself. [V]

## 3. Decisions

| # | Decision | Why |
| --- | --- | --- |
| D1 | Trusted set = (built-in minus distrusted) plus added. `ORDER` and `TRUSTED` stay source constants; `fakehub` keeps patching `TRUSTED`. | The floor is code a reviewer reads; a stored list cannot delete it, only a person can mask it. |
| D2 | A stored entry has no host, flag, pattern, expiry or per-check switch. | Nothing in the file can lower a check, so nothing in a forged or old file can. |
| D3 | One record per owner, the newest of (record, answer) wins. A tap answer is applied only when it is newer than the owner's last record and than the list's `since`, no more than a day old, and its request id has not been applied. | A removal must hold against an old approval still in the inbox, and a reset must not bring an old answer back. |
| D4 | Storage is one sealed file in the model layer, not the graph (section 4.4), and its generation floor is one keystore item. | Authorization state needs a floor a restore cannot lower; the graph is a rebuildable index. |
| D5 | Fail closed in two shapes. A keystore that cannot be reached now is `unreadable`: the pull is refused with one sentence. A list that is missing, foreign, older, altered or malformed is `degraded`: every publisher asks. | A forgery or rollback must not return a distrusted publisher; a locked keystore is not evidence of tampering. |
| D6 | The writer takes a person's grant (`HumanGrant`); the commands mint it. The tap writes only from a sealed approved request, read by id. | A caller that imports the module without a person gets a refusal, not a write. |
| D7 | `trust` needs the owner typed back exactly at the terminal device; `distrust` needs a typed `yes` or a tap on a request that has fixed words; a reset needs a typed `yes`. | Trust is sensitive and permanent; `distrust` stops running servers; a reset discards a person's work. |
| D8 | `distrust` commits the removal before it touches a file, re-reads the pins after, and is safe to run again; a pull that finishes later holds itself. | A crash or a race leaves new fetches asking, never trusting. |
| D9 | `trust` works only on the official Hub and only for an owner the Hub holds models for now. | No trust through a mirror; no trust of a name nobody holds yet. |
| D10 | The list is per device. No board entry, pool message, file or environment variable writes it. | A pool member cannot reach another device's trust. |
| D11 | The list, its floor and `known()` never read sentinel's mode or counters. | `POOLHOUSE_SENTINEL=off` and a damaged sentinel state must not move trust. |
| D12 | A file `distrust` holds is `person_only`: no lead agent releases it, its explanation says what happened, it is not in the periodic dialog, and its pin is restored when a person releases it. | A distrust must hold against the delegated `sentinel.quarantine` gate and read as what it is. |
| D13 | A record that is invalid, duplicated or of an unknown schema makes the list `degraded`. Nothing is dropped on read. | Dropping a `distrust` record would return the publisher it removed. |

## 4. The trust list

### 4.1 The effective set [proposal]

```
status(owner) = last record of owner, if any
trusted(owner) = status is "trust"                       (an added owner)
              or (owner in publishers.TRUSTED and status is not "distrust")
```

`hub/trust.py` exposes `known(owner)`; `admission.admit` calls it instead of `publisher not in publishers.TRUSTED`.
`known` returns false for every owner when the state is `degraded` and raises `Unreadable` when it is `unreadable`
(section 4.5). `publishers.trusted(repo)` stays the built-in predicate and `PREFER` stays the built-in order: ranking
in `poolhouse-models find` never approves anything.

### 4.2 What a stored name never changes [proposal]

`admit` runs the same lines for every trusted owner, whichever list it came from: host must satisfy
`publishers.official` (`HOSTS` stays a source constant), the commit must satisfy `remote.on_branch`, a marker in a
repository or file name asks, a changed digest asks. The record has no field these read. A test over the record's
schema and a test that runs one scenario against a built-in and a stored owner hold this (section 9).

### 4.3 Records and file [proposal]

```
{"schema_version": 1, "generation": 7, "since": 1760500000.0,
 "records": [{"owner": "maker", "do": "trust",    "at": 1760600000.0, "via": "terminal"},
             {"owner": "Qwen",  "do": "distrust", "at": 1760600100.0, "via": "terminal"},
             {"owner": "acme",  "do": "trust",    "at": 1760600200.0, "via": "tap", "request": "rq_..."}],
 "denied":  [{"owner": "newco", "for": "trust", "at": 1760600300.0}],
 "applied": ["rq_..."]}
```

- `since` is when the list was made or last reset. `applied` holds the ids of the tap requests already applied (the
  newest 128). `denied` holds the person's refusals of a trust offer or a stop-trusting proposal (the newest 64), so
  the 24-hour rule of section 6.1 does not depend on the request log, which trims after seven days and 400 rows.
- One record per owner, newest replaces older. A `distrust` record stays as long as the owner is listed; it is what
  masks a built-in owner and what defeats an old tap answer.
- At most 128 records. A 129th `trust` is refused with one sentence; nothing is dropped to make room.
- On read, a `schema_version` other than 1, an owner that fails `publishers.valid_name`, a `do` that is not `trust` or
  `distrust`, a repeated owner, or two owners with the same fold makes the list `degraded` (section 4.5). Nothing is
  skipped or dropped: a dropped `distrust` record would trust the publisher it removed.

### 4.4 Where it is kept, and why not the graph [proposal]

`~/.poolhouse/publishers/trust.enc` under the state root (`home.state`), AES-256-GCM under
`keystore.default().salted_subkey("publisher-trust", directory)`, additional data `poolhouse/publisher-trust/v1`,
replaced atomically (`files.writing`) under `lock.only_one(directory / "trust.lock")`.

The floor is one small item in the OS keystore beside the master item: two new methods, `Keystore.item(purpose)` and
`Keystore.set_item(purpose, text)` (account `publisher-trust/<user>`, both through `_call`, so the keystore's gate,
counts and audit apply). Its value is `{"generation": 7, "masked": ["Qwen"]}`: the highest generation written, and the
built-in owners distrusted (at most eight names). The item is created by the first write, never on a normal journey;
`docs/keystore.md`'s "one item" becomes "the master item, and one small item once a list exists". The floor is not in
sentinel's state: its `SealedFile` key sits beside it, so a process of the same user could lower it, and a `tampered`
sentinel state reads as counters of 0 (section 2). Nothing here reads sentinel.

A floor raise is a keystore write and counts against `WRITE_CEILING` (5 an hour): a sixth change in an hour is refused
before the file is written, with the keystore's own sentence. A `known()` is one read of the item (600 an hour), also on
a device that never wrote a list; a pull makes two, at the start and at the end (section 5.2, item 7). A device whose
keystore was never provisioned and that has no `publishers/` directory skips the read.

AGENTS.md section 6 defaults persistent relational state to the graph. This list departs from it (decided, section 8,
Q1):

1. It is authorization state, and it needs a monotonic floor under the keystore. The graph is a derived index that a
   rebuild or `replace` rewrites from the board's entries and that no keystore seals (`docs/graph.md`); a restore or a
   rebuild would roll trust back with nothing to notice, and a sealed copy of the floor would be a second store beside
   it.
2. The reader is `hub.admission`, in the model layer. `graph` is above it; importing `GraphStore` there adds an edge
   to `KNOWN` in `tests/test_layers.py`, which may only shrink. A provider that a higher layer registers would leave a
   process that did not register it silently on the built-in list.
3. The list has no relations of its own. The one relation that matters, publisher to the files it supplied, is
   `Pin.publisher` in the sentinel manifest; a graph copy would be a second source of truth for `distrust` to disagree with.
4. It is a short set of names, which is the "tiny single-purpose" case the rule allows.

Ranked options: (a) this sealed file with the keystore floor; (b) graph nodes, which first needs the graph engine moved
below `hub` in the layer order (a repository-wide change), still has no floor and leaves two copies of the
publisher-to-files relation; (c) graph nodes with `hub` importing `graph`, which `tests/test_layers.py` refuses.

### 4.5 Reading: four states [proposal]

A read takes the item (one keystore read; the master key is cached by the keystore, the item is not) and then the file.

| State | When | Effective set |
| --- | --- | --- |
| `fresh` | the keystore was never provisioned (`Keystore.status()`, no backend call) and there is no `publishers/` directory; or the item reads as absent and no file exists; or there is no keyring backend and no file | built-in list |
| `ok` | the file opens, authenticates, passes the schema, and `floor <= generation <= floor + 1`; with floor 0 (item absent) and a file present, only `generation == 1` | built-in with the records applied |
| `unreadable` | not `fresh` by the first or third case, and the item cannot be read now: the keystore is locked, denied or busy, or has no backend while a file exists | none: the pull is refused, no request is raised |
| `degraded` | floor above 0 and the file is missing; the file fails its tag or the master is gone; the schema, an owner or a record is invalid (section 4.3); `generation < floor` or `generation > floor + 1` (floor 0 with a file of generation above 1 is the case where the item was lost) | empty: every publisher asks |

The file is the authority when the state is `ok`; the item's `masked` list is read only by a reset. A file one
generation above the floor is a crash between the write and the raise and is accepted; the reader raises the floor
before it answers (a keystore write; if the keystore refuses, the read still answers `ok`). Until then a copy of the
previous generation also reads `ok`: a window the size of one crash, closed by the first read that can raise.

The sentences, fixed and never built from the owner or the Hub:

- `unreadable`: "Poolhouse cannot reach this computer's keystore right now, so it cannot check which publishers you
  trust. Unlock it with `poolhouse-security unlock` and try again." The pull is refused with this and nothing is
  queued (the request inbox is under the same keystore, so a request could not be raised or answered).
- `degraded`: "Poolhouse's list of the publishers you trust is damaged or out of date on this device, so it asks about
  every model, including models from publishers it normally knows." It replaces "who Poolhouse does not know" for every
  owner and appears on the request, in `poolhouse-models publishers` and as a sentinel event. A degraded approval is
  the ordinary `approve` for that commit; the trust offer is withheld (section 6.1).

While `degraded`, `trust`, the tap and `stop-trusting` refuse. `distrust` writes no file record (every publisher already
asks), adds the owner to the item's `masked` list when it is a built-in owner, and still unpins and holds the files. A
person starts again with `poolhouse-models publishers --reset` (section 5.4), never as a side effect of `trust`: it
would bring a distrusted built-in owner back without the person seeing it.

`reset` is a terminal command because it throws away a person's additions (the one-line reason AGENTS.md section 2
asks for where friction is kept); everything else a normal journey does is a tap or nothing.

### 4.6 Writing [proposal]

`trust.add(owner, grant, via, request="")` and `trust.remove(owner, grant)` check `grant.check(action, owner)`, take
the lock, read the state (`ok` or `fresh` only), apply, write generation `n+1`, raise the item to `n+1` (with the new
`masked` list), release. `trust.reset(grant)` is the only writer that starts from `degraded`; `distrust` of a built-in
owner in that state is the one write that changes the item's `masked` list alone. The settle functions
(`apply_approval`, `apply_distrust`, `note_denial`; section 6) are the only other writers and read the request from
the sealed inbox by id. A static test lists the callers of all of them.

## 5. The commands

All are subcommands of `poolhouse-models`, added through the existing `extend` and `more` arguments of
`hub.cli.main` from a new `hub/trust_cli.py`. `docs/commands.md` and `docs/person-commands.md` get rows.

### 5.1 `poolhouse-models trust OWNER` [proposal]

1. `person.require_person(...)`: refused when an agent marker is set or stdin and stdout are not terminals.
2. Refused with its sentence when the state is `degraded` or `unreadable`.
3. `OWNER` must be a bare Hub name (`publishers.valid_name`; no `/`, no `*`, no case or separator games). Refused if
   its fold (`publishers._fold`, which maps lookalikes and leet and drops separators) equals the fold of any other owner
   the device knows: a built-in owner, a stored owner, or a distrusted owner (a lookalike of an owner the person
   removed is the likeliest attack). "OWNER looks like unsloth".
4. Refused unless `publishers.official(remote.endpoint_host())`: "Trust is set on the official Hub only; HF_ENDPOINT
   points at HOST."
5. Ask the official Hub about OWNER (new `remote.owner_models(owner)` and `remote.owner_account(owner)` beside
   `remote.models`, through `poolhouse.net`). Refused when it holds no models or cannot answer. Show the account type
   (organisation or a single user), the member count of an organisation, the model count and up to three repository
   names, so a one-person account is not read as a company. Every string that came from the Hub goes through
   `safetext.escape` (and `requests.model.shown` where it enters a request) before it is printed. The response shape of
   the account call is not read anywhere in this repository today; the build step checks it against the real Hub, the
   fake Hub carries the same fields, and a field the Hub does not give is printed as "not given", never guessed.
6. Print: "Trust every model from OWNER on huggingface.co from now on? It will still check the commit and the file
   names, and ask about anything altered. Type OWNER to confirm." Read the line from the controlling terminal and
   compare exactly. `person.typed` lower-cases; a `person.typed_exact` that keeps case is added for this.
7. `trust.add`. Print "Trusted OWNER. `poolhouse-models distrust OWNER` takes it back." If files distrust once held
   for OWNER are still held, say so and name `poolhouse-security review`; trusting again never releases them.

An owner already trusted prints that and exits 0. A built-in owner that was distrusted is trusted again the same way.

### 5.2 `poolhouse-models distrust OWNER` [proposal]

1. `require_person`; the name is validated; refused when the state is `unreadable`.
2. Plan: the pins with `publisher == OWNER`, their bytes, and the servers running them. The manifest is read through
   its `SealedFile` status; when that is `tampered` the command says "Poolhouse cannot read its record of what it pulled,
   so it cannot tell which files came from OWNER" and still removes trust. With sentinel off, nothing new is pinned
   (R6) and the command says so. Nothing to remove (not trusted, no pins) prints that and exits 0.
3. Print what will happen: "Poolhouse will ask about every model from OWNER again. N files (SIZE) pulled from OWNER are
   unpinned and held, and M running servers are stopped. Type yes to continue." Read from the controlling terminal
   (`person.confirm`).
4. In this order: (a) `trust.remove` writes the distrust record; (b) read the pins again and, for each pin with
   `publisher == OWNER`, `manifest.unpin(path)` then `Store.hold_for_person(("model", path), "publisher distrusted:
   OWNER", {"publisher": OWNER, "pin": <the pin>}, Holding(path=path))`, whose hook stops the servers; (c) read the pins
   once more and repeat (b) until a pass finds none (at most five passes); (d) an activity record and a sentinel event.
   Re-running finishes whatever is left: (a) is skipped when the record exists, (b) continues over the remaining pins.
   The second read closes the window of a download that pinned a file after the plan was made; the pull's own re-check
   (item 7) closes the rest.
5. `Store.hold_for_person` is `quarantine` with one difference: a record that is already `QUARANTINED` (held by
   sentinel for another reason, which a delegated lead could release) has `person_only`, `publisher` and `pin` merged
   into its evidence under the store lock and a history line added, instead of being returned untouched. `Store.release`
   then refuses a record with `person_only` unless the grant was minted by a person: `HumanGrant` gains a `person`
   field, true from `mint`, `mint_pressed` and `mint_clicked`, false from the delegated branch of `mint_gated`.
6. A held file is released only by a person: a typed or pressed release at a terminal, or the Release button of
   `poolhouse-security review`. A lead agent passing `sentinel.quarantine` cannot give these files back. The reason
   `publisher distrusted: OWNER` is in neither `NEEDS_PERSON` nor the decoy reasons, so the periodic dialog never asks
   about it, and a test pins that. The review screen shows it with its own sentence (`explain.BY_REASON` gains
   "You stopped trusting the publisher of this file, so it was moved aside and nothing uses it.", with advice that a
   release puts it back in use) instead of the unknown-reason fallback. On release a `release` hook for `model` puts the
   pin back from the record's evidence when the restored file hashes to the same digest, with `publisher` cleared (so a
   later `distrust` does not hold it again) and `commit`, `source_ref`, `origin` and `digest_from` kept; a file whose
   bytes differ is quarantined again as a changed file.
7. The pull side: `admit` returns how it passed (`list` or `approval`). After the last file of a pull is pinned, and on
   the error path too, `transfer._bring` calls `admission.recheck`: when the pull passed by the list and `known(owner)`
   is now false or raises, every file of this pull pinned with that owner gets step 4(b)'s unpin and hold, and the pull
   raises `NotApproved` with "Trust in OWNER was removed while this was downloading, so its files were held." The check
   comes after the pin, not before it: with the record written before the check, the pull holds itself; with it written
   after, the pin was already in place before `distrust` read the pins the second time. A check before the pin would
   leave the window open. A keystore that cannot be read at that moment holds the files the same way, because a lock
   a same-user process can induce must not let a distrusted publisher's file through; a person releases them, and
   that is the cost of a lock that lands after a download.

`distrust` has no flag that keeps the files; it removes trust and what the trust vouched for.

### 5.3 `poolhouse-models stop-trusting OWNER` [proposal]

The non-technical road to `distrust`. Any process, an agent included, may run it; it raises one human-only
`publisher_trust` request and writes nothing else (section 6.4). Run again after a person has answered, it applies the
answer and prints what was held. It is refused for an owner that is neither trusted nor the publisher of a pin, when a
person said no to it in the last 24 hours, and when one is already waiting.

### 5.4 `poolhouse-models publishers [--json] [--reset]` [proposal]

Without `--reset`, read-only and open to any process. One line per owner: name, `built in` or `added`, `distrusted`,
the date, the way (`terminal` or `tap`), and the state of section 4.5 with its sentence. Anything that came from the
Hub is passed through `safetext.escape`. It is how a person finds what to distrust, and where an answered tap request
is settled (section 6.3).

`--reset` is a person's: `require_person` and a typed `yes` from the controlling terminal after this text: "Earlier
additions are lost. Built-in publishers you stopped trusting stay stopped: LIST. Add others again with `trust`." It
makes a new file at generation `floor + 1` with `since` now, holding a `distrust` record for every built-in owner in the
item's `masked` list or named by a `person_only` distrust hold in sentinel's store, and raises the item. A reset
therefore never returns a distrusted built-in owner, even one that supplied no pinned file, and a keystore reset that
loses the master still leaves the item. A `tampered` or `recovered` sentinel store makes the second source smaller,
never the first.

### 5.5 Who may run them [proposal]

- Not registry gates: a registry gate can be `delegated` to a lead agent, and these must not be. The gate is in the
  writer (a person's grant), as `Store.release` is. `docs/person-commands.md` lists them under Hard.
- `scripts/hooks/claude-bash-guard` refuses a command line whose `poolhouse-models` subcommand token is `trust`,
  `distrust` or `publishers --reset`, and lets `stop-trusting` and plain `publishers` through (the token is matched
  whole, which is why the proposing command is not named `distrust-...`). `sentinel/human.py _FORBIDDEN` and
  `agent_may` refuse a tool call naming the three or `publishers/trust`; `chatpolicy`'s person-only list gains their
  names. These are guard changes and need the exact-SHA independent approval.

## 6. The tap

### 6.1 When "always trust this publisher" is offered [proposal]

`admission.admit` adds the choice `trust-publisher` to the request when all hold; otherwise the request is as it is
today (`approve`, `deny`):

- the state is `ok` or `fresh` (a degraded list cannot be written);
- the host is official and the only reason is that the publisher is unknown (no marker, no changed digest);
- `remote.on_branch(repo, commit)` is true, so the model asked about is from the publisher's own branch;
- the owner passes `valid_name` and the fold test of section 5.1 step 3 (a lookalike of a known or distrusted publisher
  never gets the offer; the one-time `approve` is still there);
- the list holds no `denied` entry of that owner from the last 24 hours, and requests for fewer than three distinct
  repositories of that owner were raised in that time (a stream of requests is one decision, never a trust offer; an
  agent retrying one pull counts once). A trimmed request log can only make the offer appear, never make an answer count
  more; the denial is read from the list, which does not trim.

The sentence a person reads is unchanged. The choice's fixed words (`CHOICES`) are: label "always trust this
publisher", effect "The model is used, and every model from this publisher on the official Hub is used without asking
from now on. `poolhouse-models distrust` takes it back." It is in `APPROVING`. Choices stay in the fingerprint, so what
is shown is what is answered. The desktop dialog shows Deny (default and Escape), Approve and Always trust; the terminal
and the page list all three.

### 6.2 What the person sees [proposal]

First model from a new publisher: the same one request, one more button. No new window, no terminal. Choosing
`approve` is today's behaviour. Choosing `trust-publisher` ends asking for that publisher.

### 6.3 How it is persisted [proposal]

Not by the answering process (`requests` is core and cannot import `hub`) and never from anything the agent says.
`trust.settle()` runs at the start of every `admit`, of `publishers` and of `stop-trusting`, and in the scan loop that
`serve/guarded.py` wires (the machine layer, above `hub`; the layer check at build decides the import). For each
approved `model_publisher` request whose answer is `trust-publisher`, `trust.apply_approval(request_id)` reads it from
the sealed inbox by id and requires:

- `state == approved`, `answered_by in VIAS`, `kind == "model_publisher"`, the choice among the request's choices;
- the `host` field is the official host now, and the `publisher` field is a valid, unfolded owner;
- the state is `ok` or `fresh`, the request id is not in `applied`, and `answered_at` is later than the list's `since`,
  later than the owner's last record of any kind, and no more than a day old;
- the `reason` equals the sentence rebuilt from the request's own fields.

Then it writes a `tap` record and adds the id to `applied`. The call that settled it lets the retried pull pass. If the
person taps and nothing settles within a day, nothing is written and the tap approved only that commit. A denied
`model_publisher` request for an owner is written to `denied` by the same settle, with its answer time.

The `reason` check is circular: the sentence and the fields come from the same row, so a writer of the inbox makes
them agree. It catches a row built in other words by other code, not an attacker who can write the inbox. The security
is that the inbox is sealed under a keystore subkey (writing a row needs the keystore), that answering a human-only kind
passes `require_person`, that the fixed `CHOICES` words are in the fingerprint the person answered, and that the list
write is gated by the age, order and replay rules above. The tap's person gate is the one every request has today
(accepted risk R2).

### 6.4 Distrust by tap [proposal]

`stop-trusting OWNER` raises a human-only `publisher_trust` request (a new `Kind`, `human_only=True`), subject
"stop trusting OWNER", with words Poolhouse builds from its own counts: "Poolhouse will ask about every model from OWNER
again. N files (SIZE) pulled from OWNER will be held and M running servers stopped. You release held files yourself."
Its choices are `stop-trusting` (label "stop trusting", effect "Poolhouse asks about every model from this publisher
again, holds the files it pulled from them and stops the servers using them; you release held files yourself.", in
`APPROVING`) and `deny`. The dialog default is Deny. At most one waits per owner; a `deny` is written to `denied`,
which blocks a new proposal for 24 hours; a stream of proposals is one decision, as in section 6.1. The agent that
proposed is told the answer is read the next time it repeats the command. `trust.apply_distrust(request_id)` has the conditions of 6.3 with
`answer == "stop-trusting"` and runs the steps of 5.2 from (a), with `via: "tap"`; it needs no grant because it only
removes trust, and the `person_only` hold is what a lead agent cannot undo.

## 7. Attack-first pass

The first draft was the author's pass and did not count. An independent agent broke it (round 1); this section carries
the fixes. AGENTS.md section 1 still requires a different agent to break the revised design before it is built. Each
entry names the defence and a test that fails without it (test files in section 9). "Defended in the design" means
the design closes it; nothing is built, no test is written.

**A1. A copied or cloned state directory.** Defence: the file opens only under the keystore master of this OS user on
this device; elsewhere it fails its tag. A file present whose tag fails is `degraded`, not `fresh`, and so is a file with no item
to match it unless it is generation 1; the floor is in this device's keystore and travels with no file. Tests: `test_a_list_copied_under_another_keystore_is_degraded_and_trusts_nothing`,
`test_a_missing_file_after_a_write_is_degraded_not_fresh`. Status: defended in the design. A restored image of the same
device and user is A2.

**A2. Replay of an old trust record.** Defence: the file carries `generation`; the floor is the keystore item. An older
file, a deleted file, and a file above generation 1 whose item was deleted are `degraded`; deleting sentinel's state or putting its `.prev`
back lowers nothing, because nothing reads it. A tap answer older than the owner's last record is ignored. Tests:
`test_an_older_list_put_back_after_a_distrust_is_degraded`, `test_deleting_or_rolling_back_sentinel_state_does_not_lower_the_floor`,
`test_floor_zero_with_a_file_is_ok_only_for_generation_one`, `test_a_file_one_generation_ahead_of_the_floor_is_accepted_and_raises_it`,
`test_a_trust_answer_older_than_the_distrust_is_not_applied`. Status: defended in the design. R1 says what is left.

**A3. A forged record.** Defence: AEAD tag under a key only the keystore holder reaches; a file that fails it is
`degraded`; a record that is invalid, unknown or repeated makes the whole list `degraded` and none is dropped. Tests:
`test_a_list_written_without_the_key_is_degraded`, `test_an_invalid_or_unknown_schema_record_makes_the_list_degraded_and_drops_nothing`.
Status: defended in the design.

**A4. A malicious agent writes the list.** Defence: (a) the file is sealed, see A3; (b) `trust.add`, `trust.remove` and
`trust.reset` refuse a call with no person's grant; (c) the commands refuse a process with an agent marker and one
without terminals; (d) the bash guard and `agent_may` refuse the command line, the module name and the state directory;
(e) an agent may only propose (`stop-trusting`), and a proposal changes nothing until a person taps. Tests:
`test_the_writer_refuses_a_call_without_a_persons_grant`, `test_trust_and_distrust_refuse_a_process_an_agent_started`,
`test_an_agent_tool_call_naming_the_trust_commands_or_their_directory_is_refused`, `test_a_delegated_grant_is_not_a_persons_for_a_held_publisher_file`,
`test_the_guard_refuses_trust_distrust_and_reset_but_lets_stop_trusting_through`. Status: defended in the design. An agent
that clears the marker and owns a pseudo-terminal passes every terminal gate that has no typed confirmation
(`docs/person-commands.md`); `trust` and `distrust` read the confirmation from the controlling terminal device, which
that agent has only if it made it its own: accepted risk R1.

**A5. An injected instruction (board message, file, page, tool result) to trust a publisher.** Defence: it can only
reach the same doors as A4. The tap persists only from a sealed request answered by a person, with the fixed words, so
a request raised in other words, one answered `approve`, one denied, and one never answered all write nothing.
`requests.answer` for a human-only kind refuses an agent whatever the registry delegates. The reason-equals-sentence
check is circular and is not the defence (section 6.3); the sealed inbox and the person gate are. Tests:
`test_a_request_raised_in_other_words_with_the_trust_choice_trusts_nothing`, `test_an_approve_answer_does_not_trust`,
`test_a_denied_or_expired_trust_request_trusts_nothing`, `test_an_agent_cannot_answer_the_trust_choice`,
`test_the_only_callers_of_the_trust_writers_are_the_commands_and_the_settle_step`. Status: defended in the design.

**A6. A lookalike or homoglyph owner.** Defence: names are ASCII Hub names compared whole; `trust` refuses a name whose
fold equals any built-in, stored or distrusted owner's; the tap is not offered for one; at fetch `Unsloth` is not
`unsloth`. Tests: `test_trust_refuses_an_owner_that_folds_to_another`, `test_trust_refuses_a_lookalike_of_a_distrusted_owner`,
`test_trust_refuses_a_name_that_is_not_a_hub_name`, `test_the_tap_is_not_offered_for_a_lookalike`,
`test_a_stored_owner_matches_character_for_character`. Status: defended in the design. An owner that is merely close
(one letter from a known name) is shown by `publishers`; there is no distance rule, because a legitimate organisation
can sit close to another: accepted risk R4.

**A7. A publisher compromised after it was trusted.** Defence: `distrust` commits the removal first, then unpins by
`Pin.publisher` (reading the pins again after), holds the files with a record only a person releases (merged onto a
record that was already held), and stops the servers; every pull in the window was pinned with `publisher`, `commit`
and `source_ref`, so the set is exact. Trusting again releases nothing. A release restores the pin; the periodic dialog
never lists the hold and the review screen explains it. Tests: `test_distrust_unpins_holds_and_stops_what_the_publisher_supplied`,
`test_distrust_commits_the_removal_before_it_touches_a_file`, `test_a_file_distrust_held_is_released_only_by_a_person`,
`test_distrust_marks_a_file_sentinel_already_held_as_person_only`, `test_a_distrust_hold_has_its_own_sentence_and_is_not_in_the_periodic_dialog`,
`test_releasing_a_distrust_hold_restores_the_pin_without_the_publisher`, `test_trusting_again_does_not_release_what_distrust_held`.
Status: defended in the design for pinned files. Accepted risks R5, R6: a file the publisher supplied that was never
pinned, a blob two publishers both supplied (one pin per path), and a first pull of a replaced file before anyone knew
(HANDOFF, "A compromised known namespace").

**A8. A downgrade, official Hub to another endpoint, or a check lowered by where the name came from.** Defence: the
record has no host; `publishers.official` runs for every owner; `trust` and the tap refuse a mirror; the tap's approval
is bound to the `host` field; the branch and marker checks run for stored owners. Tests:
`test_a_stored_publisher_on_a_mirror_asks_with_the_host`, `test_trust_refuses_while_the_endpoint_is_not_the_official_hub`,
`test_the_tap_is_not_offered_on_a_mirror`, `test_a_trust_answer_for_one_host_does_not_apply_on_another`,
`test_a_stored_and_a_built_in_publisher_are_checked_alike`, `test_the_trust_record_has_no_field_a_check_reads`.
Status: defended in the design.

**A9. Approval fatigue.** Defence: at most ten `model_publisher` requests wait; the trust choice is withheld for 24
hours after a denial of the owner (stored in the list) and once requests for three distinct repositories of it were
raised, so a stream is one decision; it is a separate button, never the default (the dialog's default is Deny), and
human-only kinds are excluded from "answer all". A `stop-trusting` proposal is rate-limited the same way. Answering a
stream with `approve` approves single commits and trusts nothing. Tests:
`test_the_trust_choice_is_withheld_after_a_denial_or_three_repositories`, `test_a_denial_survives_the_request_log_trimming`,
`test_a_retried_pull_counts_once_toward_the_stream_rule`, `test_the_dialog_default_is_deny_and_trust_is_never_first`,
`test_answer_all_never_covers_a_trust_choice`, `test_stop_trusting_is_not_raised_again_within_a_day_of_a_no`.
Status: defended in the design.

**A10. Takeover by whoever is merely present.** Defence: at a terminal `trust` needs the name typed back exactly from
the controlling terminal; a bare Enter or `y` does nothing; `distrust` and `--reset` need a typed `yes`; every change
is an activity record, a sentinel event and a line in `publishers`, and one command undoes it. The tap is an ordinary
request answer: whoever is signed in at the unlocked computer is the person for every request today. Tests:
`test_trust_needs_the_owner_name_typed_back_exactly`, `test_trust_without_a_terminal_is_refused`,
`test_every_change_is_recorded_and_listed`. Status: terminal defended in the design; the tap is accepted risk R2 until
phone approval (`docs/phone-approval.md`) can carry this choice.

**A11. Concurrent edits.** Defence: one lock around read, apply, write and floor raise; atomic replace; a distrust
racing a trust leaves the later one; the settle step takes the same lock. Tests: `test_parallel_trusts_of_different_owners_all_persist`,
`test_a_trust_and_a_distrust_of_one_owner_leave_the_later_one`, `test_a_reader_never_sees_a_half_written_list`.
Status: defended in the design.

**A12. A compromised or malicious pool member.** Defence: the list is per device and no board entry, pool message or
file reaches a writer (A5's caller test); a copy served by a member is still checked against the Hub's digest after the
publisher check. Tests: the caller test above and `test_a_member_served_copy_of_a_distrusted_publisher_asks_first`.
Status: defended in the design. Pool-wide trust is not designed here.

**A13. A stolen phone or key; a forged or below-threshold signature.** Not applicable: no phone key or signature takes
part in the terminal path or the request tap. When phone approval lands, `trust-publisher` and `stop-trusting` are
choices it should carry, named in `docs/phone-approval.md` then.

**A14. A name that does not exist yet.** Defence: `trust` needs the official Hub to hold models for the owner now.
Test: `test_trust_refuses_an_owner_the_hub_holds_nothing_for`. Status: defended in the design for today. A trusted name
the Hub frees and someone else registers is accepted risk R3.

**A15. A download racing a `distrust`.** The pull was admitted by the list, the person distrusts the owner while a
multi-gigabyte file downloads, and the file then lands and is pinned with `publisher == OWNER` after `distrust` read
its pins. Defence: the three steps of section 5.2, items 4 and 7: the record is written first, the pins are read
again after, and the pull checks `known()` after its last pin and holds itself. Tests:
`test_a_pull_that_finishes_after_a_distrust_holds_its_own_files`, `test_distrust_holds_a_pin_that_lands_between_its_plan_and_its_removal`,
`test_a_pull_approved_once_is_not_held_by_the_recheck`. Status: defended in the design.

**A16. A reset followed by a replay.** A tap answer is kept seven days in the inbox. Without a rule, `--reset` (which
empties the records, `distrust` ones included) lets the next fetch re-apply it. Defence: the list's `since`, the
applied ids, the owner's last record, and the reset's re-masking (section 5.4). Tests: `test_a_tap_answer_from_before_a_reset_is_not_applied`,
`test_a_tap_request_is_applied_once`, `test_a_reset_keeps_built_in_owners_distrusted_before_it`. Status: defended in
the design.

**A17. Sentinel switched off, tampered or recovered.** `POOLHOUSE_SENTINEL=off` (with a reason) turns pinning and the
digest check off; a tampered or recovered sentinel state reads as zeroed counters. Defence: nothing about the list, the
floor or `known()` reads sentinel's mode, counters or state; only the pin plan and the hold use the manifest and the
store, and say so when sentinel is off or the manifest is unreadable. Tests: `test_the_list_and_its_floor_do_not_depend_on_sentinels_mode`,
`test_distrust_with_sentinel_off_removes_trust_and_says_nothing_was_tracked`. Status: defended in the design; files
pulled while sentinel was off are R6.

**A18. Induced degradation.** A process of the same user that can write the state directory deletes or corrupts the
file and the person is then asked about every model, or is pushed to `--reset`. Defence: the state is `degraded`, not
`fresh`; every request carries the one plain sentence; no trust offer is made; the asks are ordinary per-commit
approvals with the ten-wait cap; the reset keeps built-in owners distrusted and loses only what a person added; a
locked keystore is `unreadable`, a refusal with its own sentence, so a lock never looks like tampering. The attacker
gains nothing but friction (R8). Tests: `test_a_degraded_list_asks_with_its_own_sentence_for_a_built_in_publisher`,
`test_a_locked_keystore_refuses_with_one_sentence_and_raises_no_request`, `test_degraded_is_not_reported_for_a_locked_keystore`,
`test_reset_is_a_persons_and_the_only_way_out_of_a_degraded_list`. Status: defended in the design; the nuisance remains.

**A19. A delayed tap.** An answer given and not settled for days is applied when something later fetches from the
publisher. Defence: an answer is applied only within a day of `answered_at`, only if newer than the owner's last record
and `since`, and the settle runs on every fetch, `publishers`, `stop-trusting` and the scan loop, so a tap waits only
until the next of those runs. Tests: `test_a_tap_answered_a_day_ago_is_not_applied`, `test_a_tap_older_than_a_later_terminal_trust_or_distrust_is_not_applied`.
Status: defended in the design.

**A20. An old build.** A build from before this feature ignores the list and trusts the built-in owners. Not defended
here: AGENTS.md section 4 has every agent run the latest build, automatically, and a stale build is reported as a defect.
Accepted risk R7.

**A21. A record collision.** Two records for one owner, two owners that fold alike, and a request id used twice.
Defence: the writer refuses each; a file that holds one is `degraded`; an id
applied once. Test: `test_a_list_with_two_records_for_one_owner_or_two_alike_owners_is_degraded`. Status: defended in the
design.

## 8. Findings and decisions

Defended in the design: A1 to A12 and A14 to A19 and A21 above, with the residues named under each; the dialog default;
the old tap answer outliving a `distrust` or a reset; a delegated lead releasing files `distrust` held (including a
file sentinel already held); the case-folding `person.typed` defeating an exact typed name; a floor that a same-user
process could lower; a list that stopped working when sentinel was off. These are designed, not built.

What the independent review claimed and what the code showed (round 1). Each was checked against the code and holds,
except where noted:

- The floor was forgeable (sentinel's `SealedFile` key sits beside it, and a `tampered` load reads as counters of 0), the
  list depended on sentinel's mode, a retained tap answer survived a reset, `quarantine` left an already-held record
  without `person_only`, a download could land after `distrust`, forced degradation was a lever, and distrust holds
  had no explanation of their own and a wrong release wording. All are fixed above.
- The review asked for the `known()` check just before pinning. The pin happens in `net/download._pin_pulled`, which
  cannot import `hub`, and a check before the pin leaves a window; the check is after the pin in `transfer._bring`
  (section 5.2, item 7).
- The review asked that holds be excluded from the periodic dialog. They already are, because `needs_person` is false
  for the new reason; the fix is to say so and pin it with a test. They do appear in `review()`, with the
  unknown-reason sentence and a Release button that is a person's click, so "released only by a person at a terminal"
  was wrong and is now "released only by a person".
- The review asked that a `tampered` or `recovered` sentinel state be `degraded` and not floor 0. That is superseded:
  the floor is no longer in sentinel's state, so nothing about it depends on that status.

Accepted risks, named for the owner:

| # | Risk | What bounds it |
| --- | --- | --- |
| R1 | Forging the list needs the keystore master; lowering the floor needs write access to the keystore item. Both are the keystore's own protection, which `real_machine` guards only in Poolhouse code: a process that talks to the OS keystore itself can do either, and then the list is no stronger than the master key under everything else; the OS keystore's access rule decides how far that reaches. A pseudo-terminal under a cleared marker passes `require_person` and can type the confirmation. A process that deletes the `publishers/` directory, the file and the keystore's `provisioned.json` makes the device read `fresh`. | The marker plus a terminal is the only boundary of every person gate (`docs/person-commands.md`). The keystore is closed to anything but the installed software by Poolhouse's own check. A reader that finds the floor behind the file raises it. |
| R2 | An unlocked, signed-in session can tap "always trust" or "stop trusting". | It is audited, listed and one command undoes it; phone approval is the fix. |
| R3 | A trusted name the Hub frees and another account registers. | The built-in list has the same exposure. To look into: pinning the Hub's account id if its API exposes one (not verified). |
| R4 | A near-miss owner name trusted on purpose. | The typed name, the Hub's account type, member and model counts, and `publishers`. |
| R5 | A blob two publishers supplied belongs to the one that pinned it last. | `distrust` of the other leaves it; rare, and the sentinel pin check still applies. |
| R6 | Files never pinned (pulled with sentinel off, or from a path nothing pinned), a manifest that cannot be read, and a replaced file pulled before anyone knew. | `distrust` says when it could not tell; existing "Not armed" items in `docs/sentinel.md`; an independent digest source is HANDOFF work. |
| R7 | A build older than this feature ignores the list. | Every agent runs the latest build automatically (AGENTS.md section 4); a stale build is a defect. |
| R8 | A same-user process can force `degraded` by deleting or corrupting the file. | It buys the attacker only friction; a person resets with one command and loses only what was added. |
| R9 | After a crash between the file write and the floor raise, a copy of the previous generation also reads `ok` until the first read that can raise the floor. | The window is one crash wide; the attacker needs the previous file and write access. |

Decided here (the first draft left both to the owner):

- **Q1. A sealed file in `hub`, with its floor in one keystore item.** Not the graph and not a core module that owns a
  graph. The reason that decides it is that this is authorization state needing a monotonic floor under the keystore:
  the graph is a rebuildable index with neither. The layer rule and the second copy of the publisher-to-files relation
  (section 4.4) agree.
- **Q2. A process that cannot reach the keystore refuses; it does not ask, and it does not read the built-in list.** Once
  a list exists, a locked, denied, busy or absent keystore is `unreadable`: the pull stops with one plain sentence
  naming the unlock command, because the request store is the same keystore and could not hold an ask. Letting such a
  process read the built-in list and ignore masks would let a `distrust` be undone by making the keystore unreadable.
  Only a missing, foreign, older or altered list is `degraded` (every publisher asks). A device that never wrote a list
  sees no change and no keystore read beyond the item probe of section 4.5.

## 9. First slices and tests

### Slice 1: the list, the commands and `distrust` (MOD-4)

Files:

- `src/poolhouse/hub/trust.py` (new): records, rules, `known`, `add`, `remove`, `reset`, `plan`, `recheck`;
  `src/poolhouse/hub/trust_file.py` (new): the sealed file, the item, the four states. Each stays well under the file limit.
- `src/poolhouse/hub/trust_cli.py` (new, about 180 lines): `trust`, `distrust`, `publishers`; `hub/cli.py` composes it.
- `src/poolhouse/keystore.py`: `Keystore.item` and `Keystore.set_item`.
- `src/poolhouse/hub/admission.py`: `known` replaces the `TRUSTED` test; the degraded and unreadable sentences; `admit`
  returns its basis. `src/poolhouse/hub/transfer.py`: the call to `recheck` after the last pin and on the error path.
  `src/poolhouse/hub/remote.py`: `owner_models`, `owner_account`.
- `src/poolhouse/person.py`: `typed_exact`. `src/poolhouse/sentinel/human.py`: `HumanGrant.person`, `_FORBIDDEN`.
  `src/poolhouse/sentinel/store.py`: `hold_for_person`, the `person_only` check in `release`.
  `src/poolhouse/sentinel/explain.py`: the `BY_REASON` entry and advice. `src/poolhouse/sentinel/adapters.py`: the `release`
  hook that restores the pin.
- `scripts/hooks/claude-bash-guard`, the chat tool list.
- `docs/redteam/coverage-map.toml` rows for the two new grant mints, the Hub calls and the keystore call;
  `scripts/redteam_coverage.py --check`.
- Documents: section 10.

Tests, in `tests/test_publisher_trust_store.py` and `tests/test_publisher_trust_commands.py`, real fake Hub on loopback
(`src/poolhouse/testing/fakehub.py`, which gains the account fields), real sealed sentinel store and request inbox
under `tmp_path`, the file test keyring: the A1 to A4, A6 to A8 (terminal parts), A10 to A12, A14, A15, A17, A18 (the
degraded and unreadable parts) and A21 tests named in section 7, and
`test_a_publisher_trust_added_by_a_person_is_fetched_without_asking`, `test_distrust_of_a_built_in_publisher_asks_again`,
`test_a_degraded_list_makes_every_publisher_ask_with_one_plain_sentence` (the degraded sentence, not "who Poolhouse
does not know"), `test_reset_is_a_persons_and_the_only_way_out_of_a_degraded_list`, `test_trust_refuses_while_the_list_is_degraded`,
`test_no_file_is_read_and_no_keystore_item_is_written_until_a_list_exists`, `test_trust_shows_the_account_type_and_member_count_escaped`,
`test_a_hub_name_with_control_or_bidi_characters_is_escaped_before_it_is_printed`.

Mutation checks, per AGENTS.md section 5: drop the floor check (A2 fails), drop the person check in the writer (A4
fails), let `known` ignore the `distrust` record (A7 fails), skip the fold test (A6 fails), drop the post-pin recheck
(A15 fails), drop the `person_only` merge on an already-held record (A7's marker test fails), drop the `BY_REASON`
entry (the explanation test fails), read sentinel's mode in `known` (A17 fails).

### Slice 2: the tap (the non-technical path)

- `requests/model.py`: the `trust-publisher` and `stop-trusting` choices and their words, the `publisher_trust` kind;
  `requests/approvals.py ask` takes the choices.
- `hub/admission.py`: the offer rule; `hub/trust.py`: `settle`, `apply_approval`, `apply_distrust`, `note_denial`;
  `hub/trust_cli.py`: `stop-trusting`; `serve/guarded.py`: the settle call in the scan loop.
- Tests in `tests/test_publisher_trust_tap.py`: A5, A6 (tap), A8 (tap), A9, A16, A19, the A2 tap test, and
  `test_a_person_taps_always_trust_and_the_next_pull_from_that_publisher_does_not_ask`,
  `test_a_person_taps_stop_trusting_and_the_files_are_held_and_the_servers_stopped`. Driven the way a person does
  it before it is called working: the request in the page and in the desktop dialog, then the second pull.

Slice 1 is MOD-4. The feature is not done for a person bad with tech until slice 2 lands and has been driven.

## 10. Documents to change when it is built

`docs/sentinel.md` (the Supply chain paragraph: the list can be added to and masked, eight owners; the periodic dialog
and the review screen for a distrust hold), `docs/model-discovery.md` ("Publisher trust"), `docs/requests.md` (the
choices and the `publisher_trust` kind), `docs/keystore.md` (the second item and the new purpose), `docs/person-commands.md`
(Hard: `poolhouse-models trust`, `distrust`, `publishers --reset`), `docs/commands.md` (the `poolhouse-models` row),
`docs/release-readiness.md` (MOD-4 verified-by and state), `docs/redteam/coverage.json`.

Not designed here: repository-level trust, a pool-wide list, expiry, ranking stored publishers in `find`, a
per-mirror list, pinning the Hub's account id.
