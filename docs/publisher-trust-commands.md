# Publisher trust commands: `poolhouse-models trust` and `distrust`

Status: design, 2026-10-16, revised after the second independent attack-first review (round 2 of at most 2, the last).
Nothing here is built. It serves MOD-4 in `docs/release-readiness.md` section 3.6 and extends the publisher check of
`docs/sentinel.md` ("Supply chain") and `docs/model-discovery.md` ("Publisher trust").

Tags: **[V path]** read in that code on `0.3dev` at `34e5e3aef`; **[proposal]** this design.

## 1. Summary

- A person adds or removes a model publisher without a source edit. The built-in list (`publishers.ORDER`) stays in
  source as the floor; a stored list adds names to it and `distrust` masks names out of it. [proposal]
- A stored name is a name only. It gets exactly the checks a built-in one gets: whole-owner match, the official Hub
  host, a commit that is a branch or tag head or in `main`'s first history pages, marker names ask. No stored field
  can lower one. [proposal]
- Three ways in. At a terminal, `poolhouse-models trust OWNER` and `distrust OWNER`. As a tap, a third button, "always
  trust this publisher", in the desktop dialog that shows the `model_publisher` request the person already answers; it
  is offered and applied only there (D15), and where there is no desktop dialog, or the request is answered in the page
  or at a terminal, the person sees approve and deny only. The other direction is a `publisher_trust` request an agent
  proposes with `poolhouse-models stop-trusting OWNER`. A normal journey gains no prompt. [proposal]
- `distrust` records the removal when the keystore answers, holds what that publisher supplied before it unpins it
  (released only by a person, never by a lead agent, and neither purged nor settled as missing by one), and stops the
  servers using it. A pull that was running when `distrust` ran holds the files it wrote as each one lands. A keystore
  that refuses stops the record, never the holding. [proposal]
- The list is sealed under the keystore and carries a generation. The floor that generation must equal is one small
  item in the keystore, raised before the file is written. A device whose keystore holds no master item has never been
  provisioned, so no list and no distrust can exist on it: it is `fresh`, reads the built-in list and touches no
  secret, prompt or unlock (decided by an attributes-only lookup, section 4.5). On a device whose master item is
  present, the floor item is read; nothing about the list reads sentinel's mode. A provisioned keystore that cannot be
  read refuses the pull on the app and agent surfaces in a sentence for its cause; a list that is missing, altered,
  behind or ahead of the floor makes every publisher ask until a person starts it again. [proposal]
- Only the app and agent surfaces read the list. A library caller passes its own trust policy and never reads the list,
  touches the keystore or is asked (section 4.7, D14). [proposal]
- The questions the first draft left for the owner and the three the second review raised are decided in section 8 (Q1
  to Q5). Nothing in this document waits for the owner.

## 2. What exists today

- `hub/publishers.py` holds `ORDER` (eight owners: `Qwen`, `unsloth`, `ggml-org`, `google`, `bartowski`,
  `lmstudio-community`, `mlx-community`, `litert-community`), `TRUSTED = frozenset(ORDER)`, `HOSTS = {huggingface.co, hf.co}`, `MARKERS`,
  and `valid_name`, `namespace`, `trusted`, `official`, `marker`. A name matches by whole owner, character for
  character; a name that is not an ASCII Hub name is not parsed. [V]
- `hub/admission.py` `admit(ref, files)` reads `publishers.TRUSTED` at call time, once, in `transfer._bring` before the
  space check and the download; it is the only reader. `_bring` is reached by `hub.pull` from `serve/models_cli.py:203`,
  `fleet/models.py:284` and the library's `ph.models.pull` (`api/models.py:25`). Reasons to ask: host not official;
  owner not in `TRUSTED` ("who Poolhouse does not know"); (only for a known owner) commit not on a branch, tag or the
  first pages of `main` (`remote.on_branch`); a marker in the repository or file names; a digest that differs from an
  earlier pull. It raises a human-only `model_publisher` request with choices `approve` and `deny`, fields `publisher`,
  `repo`, `commit`, `host`, and approves only a request whose subject, words and fields are exactly the ones it builds
  (`approvals.approved`, which does not look at which choice was taken). At most ten wait (`MOST_WAITING`). [V]
- Answering a `model_publisher` request passes `require_person` whatever the authority registry delegates; the inbox
  is one file sealed under a keystore subkey. A resolved request is dropped after seven days and 400 rows are kept
  (`requests/store.py`, `RETAIN_S`, `MAX_ROWS`), and `approved()` finds an approved row for as long as it is kept. [V]
- `inbox.answer` (`requests/inbox.py:142-158`) calls `require_person` for a human-only kind, but passes
  `terminal = (True, True)` for every `via` except `terminal` (line 150), so an answer through the page (`ui`) or the
  dialog meets the agent-marker check alone. `Store.answer` accepts any `via` in `VIAS` and records it as `answered_by`
  (`requests/store.py:221-243`). `dialog.ask` answers with `via="dialog"` in the process that put the dialog up.
  `make_choices` (`requests/model.py:178-184`) checks that a choice id is in `CHOICES` and nothing about when a choice
  may be offered. [V]
- A pull pins each file with `publisher`, `commit` and `source_ref` in the sealed manifest, at the end of the
  download, after the file is promoted (`net/download._pin_pulled`, through `Want.pin_notes` from `hub/transfer._notes`;
  `net` is a core package and cannot import `hub`). Only `gguf` and `safetensors` files are pinned (`PINNED_KINDS`);
  nothing is pinned when sentinel's mode is `off`; a failure to pin is logged and the file is kept
  (`net/download.py:328-355`). `Manifest.unpin(path)` and `Store.quarantine((kind, key), reason, evidence,
  Holding(path=...), actor)` exist; a quarantined model file is moved aside and `adapters.stop_serving` stops the
  servers using it. `release` is `mint_gated("sentinel.quarantine", ...)`, which a lead agent passes while that gate is
  delegated (the dev preset). [V]
- A quarantined record leaves quarantine three ways: `Store.release` and `Store.purge`, each with a grant that the CLI
  mints with `mint_gated("sentinel.quarantine", ...)` (`sentinel/cli.py:141,148`), and `Store.settle_missing`, with no
  grant, called from `core._heal` (`sentinel/core.py:399-421`) for a record whose pinned file is gone and nothing was
  moved aside (`sentinel/store.py:392-415`). [V]
- `serve/guarded.verify` pins a model that has no pin at first use, with no publisher (`serve/guarded.py:203-215`). [V]
- `Store.quarantine` returns a record that is already `QUARANTINED` untouched; evidence is merged only when a record
  moves into quarantine (`sentinel/store.py:316-317`, `_move`). It returns `None` when the store is full. [V]
- `mint_clicked` takes `answer` and `label` from its caller and checks the agent marker and that they are equal
  (`sentinel/human.py:91-103`; the dialog's Release button, `sentinel/heads_up.py:237`). `mint_pressed` runs
  `require_person` (marker, terminals) and then calls the caller's `pressed` (`sentinel/review.py:137`). `mint_gated`
  returns a grant to a lead agent without a typed confirmation while its gate is delegated (`human.py:67-75`). [V]
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
  There is no API for another item. A call that reaches the backend goes through `_flight`, which runs `_gate` (the
  denial latch from `denied.json`, and the rule that a process with no terminal or desktop session reads only once
  `provisioned.json` says a person unlocked), and then through `_call` (`_spend` counts it in `rate.json`, a bounded
  wait, an audit event); `_call` alone does not run `_gate` (`keystore.py:371-400`). A background call is made with
  prompts forbidden. Reads of an existing item are limited to 600 an hour, creates and deletes (and a read after a
  refusal) to 5 (`READ_CEILING`, `WRITE_CEILING`). `denied.json`, `rate.json` and `provisioned.json` are plain JSON in
  the state root: `_window` keeps a time later than now for ever and raises on an entry that is not a number, and
  `_denial` does the same for `until`. A locked, denied, busy or absent backend raises `KeystoreLocked`,
  `KeystoreDenied`, `KeystoreBusy` or `KeystoreUnavailable`; a vanished master raises `KeystoreMissing`.
  `Keystore.reset` deletes the master item and `provisioned.json`, `denied.json`, `noticed.json` and `legacy.json`; it
  leaves `rate.json`. [V]
- The keystore is open to Poolhouse code only when `real_machine.refusal()` is empty: not a test run, the state root is
  the account's own `~/.poolhouse`, and this module file lies under `~/.poolhouse/runtimes`. That is a check on where
  the code is, not on which interpreter or command started the process: any process that runs the installed copy
  passes it, an agent's included, and whether that process may then read the master is `_gate`'s rule above. It is a
  check in Poolhouse code, not an operating-system boundary against a process that calls the OS keyring itself. [V]

## 3. Decisions

| # | Decision | Why |
| --- | --- | --- |
| D1 | Trusted set = (built-in minus distrusted) plus added. `ORDER` and `TRUSTED` stay source constants; `fakehub` keeps patching `TRUSTED`. | The floor is code a reviewer reads; a stored list cannot delete it, only a person can mask it. |
| D2 | A stored entry has no host, flag, pattern, expiry or per-check switch. | Nothing in the file can lower a check, so nothing in a forged or old file can. |
| D3 | One record per owner, the newest of (record, answer) wins. A `stop-trusting` answer is applied only when it is newer than the owner's last record and than the list's `since`, no more than a day old, and its request id has not been applied. A `trust-publisher` answer is applied only by the process that showed the dialog, within two minutes of the click, after the offer rules run again (D15). | A removal must hold against an old approval still in the inbox, and a reset must not bring an old answer back. |
| D4 | Storage is one sealed file in the model layer, not the graph (section 4.4), and its generation floor is one keystore item, raised before the file is written. | Authorization state needs a floor a restore cannot lower; the graph is a rebuildable index. |
| D5 | Fail closed in two shapes. A keystore that cannot be read now is `unreadable`: the app or agent surface refuses the pull with the sentence of its cause (locked, denied, busy, not the installed copy). A list that is missing, foreign, behind or ahead of the floor, altered or malformed is `degraded`: every publisher asks. | A forgery or rollback must not return a distrusted publisher; a locked keystore is not evidence of tampering. |
| D6 | The writer takes a person's grant (`HumanGrant`); the commands mint it. The dialog writes only from a sealed approved request, read by id, in the process that showed it. | A caller that imports the module without a person gets a refusal, not a write. |
| D7 | `trust` needs the owner typed back exactly at the terminal device; `distrust` needs a typed `yes` or a tap on a request that has fixed words; a reset needs a typed `yes`. | Trust is sensitive and permanent; `distrust` stops running servers; a reset discards a person's work. |
| D8 | `distrust` records the removal first when the keystore answers, holds a file before it unpins it, goes on with the holding when the keystore refuses, and is safe to run again; a pull that finishes later holds the files it wrote, after each file. | A crash, a refusal or a race leaves new fetches asking, never trusting. |
| D9 | `trust` works only on the official Hub and only for an owner the Hub holds models for now. | No trust through a mirror; no trust of a name nobody holds yet. |
| D10 | The list is per device. No board entry, pool message, file or environment variable writes it. | A pool member cannot reach another device's trust. |
| D11 | The list, its floor and `known()` never read sentinel's mode or counters. `reconcile` and `settle()` read the manifest and sentinel's holds only to remove trust. | `POOLHOUSE_SENTINEL=off` and a damaged sentinel state must not move trust toward trusting. |
| D12 | A file `distrust` holds is `person_only`: no lead agent releases it, purges it or settles it as missing, its explanation says what happened, it is not in the periodic dialog, and its pin is restored when a person releases it. | A distrust must hold against the delegated `sentinel.quarantine` gate and read as what it is. |
| D13 | A record that is invalid, duplicated or of an unknown schema makes the list `degraded`. Nothing is dropped on read. | Dropping a `distrust` record would return the publisher it removed. |
| D14 | Only the app and agent surfaces read the list or the floor. A library caller passes its trust policy as an argument; the library default allows and records the pin, reads no list, touches no keystore and raises no request. | Library users get no new prompts or refusals (owner's standing rule). |
| D15 | A persistent "trust from now on" is written only by the process that showed the desktop dialog, from the button it got back; a phone-signed approval may carry it later. A row in the inbox never persists trust. Where there is no such path the offer is the one-time approval only. | The inbox is written by anything that runs the installed code, and `ui` and `dialog` answers meet the agent-marker check alone (section 2). |
| D16 | `fresh` is decided by the keystore's own state: the master item is absent, found by an existence lookup that reads no secret and needs no unlock (section 4.5). Absent: `fresh`, the built-in list applies, no keystore read, no prompt, no refusal. Present: the floor item is read. `provisioned.json` and `publishers/` decide nothing. | Those two are files a process of the same user deletes, so they cannot say whether a list ever existed. A write needs the master, so a distrust cannot exist while the master is absent, and a never-provisioned device must not need a person's unlock to pull a built-in publisher (onboarding, invisible security). |

## 4. The trust list

### 4.1 The effective set [proposal]

```
status(owner) = last record of owner, if any
trusted(owner) = status is "trust"                       (an added owner)
              or (owner in publishers.TRUSTED and status is not "distrust")
```

`hub/trust.py` exposes `known(owner)`; the app policy (section 4.7) calls it where `admission.admit` has `publisher not
in publishers.TRUSTED`. `known` returns false for every owner when the state is `degraded` and raises `Unreadable`
(with its cause) when it is `unreadable` (section 4.5). `publishers.trusted(repo)` stays the built-in predicate and
`PREFER` stays the built-in order: ranking in `poolhouse-models find` never approves anything.

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
             {"owner": "acme",  "do": "trust",    "at": 1760600200.0, "via": "dialog", "request": "rq_..."}],
 "denied":  [{"owner": "newco", "for": "trust", "at": 1760600300.0}],
 "applied": ["rq_..."]}
```

- `since` is when the list was made or last reset. `applied` holds the ids of the requests already applied (the newest
  128). `denied` holds the person's refusals of a trust offer or a stop-trusting proposal (the newest 64), so the
  24-hour rule of section 6.1 does not depend on the request log, which trims after seven days and 400 rows.
- One record per owner, newest replaces older. A `distrust` record stays as long as the owner is listed; it is what
  masks a built-in owner and what defeats an old stop-trusting or trust answer.
- At most 128 records. A 129th `trust` is refused with one sentence; nothing is dropped to make room.
- On read, a `schema_version` other than 1, an owner that fails `publishers.valid_name`, a `do` that is not `trust` or
  `distrust`, a repeated owner, or two owners with the same fold makes the list `degraded` (section 4.5). Nothing is
  skipped or dropped: a dropped `distrust` record would trust the publisher it removed.

### 4.4 Where it is kept, and why not the graph [proposal]

`~/.poolhouse/publishers/trust.enc` under the state root (`home.state`), AES-256-GCM under
`keystore.default().salted_subkey("publisher-trust", directory)`, additional data `poolhouse/publisher-trust/v1`,
replaced atomically (`files.writing`) under `lock.only_one(directory / "trust.lock")`.

The floor is one small item in the OS keystore beside the master item: two new methods, `Keystore.item(purpose)` and
`Keystore.set_item(purpose, text)` (account `publisher-trust/<user>`). Each goes through `_flight`, so `_gate` runs (the
denial latch and the background-process rule), and then through `_call`, so the ceilings, the bounded wait and the
audit event apply; `_call` alone would skip `_gate`. Its value is `{"generation": 7, "masked": ["Qwen"]}`: the
generation the file must carry, and the built-in owners distrusted (at most eight names). The item is created by the
first write, never on a normal journey. `docs/keystore.md`'s "one item" becomes "the master item, and one small item once
a list exists", and its Reset section says `keystore-reset` leaves that item: `Keystore.reset` deletes the master and the
state files only, so stopping trust in a publisher survives a keystore reset (a new master cannot open the old file, the
state reads `degraded`, and `publishers --reset` re-masks from the item). The floor is not in sentinel's state: its
`SealedFile` key sits beside it, so a process of the same user could lower it, and a `tampered` sentinel state reads as
counters of 0 (section 2). Nothing here reads sentinel.

A write raises the floor first and writes the file second (section 4.6). A floor raise is a keystore write and counts
against `WRITE_CEILING` (5 an hour): a sixth change in an hour is refused before the file is written, with the
keystore's own sentence, except for `distrust` (section 5.2, item 4). A read is one read of the item (600 an hour)
once the master item is present, whether or not a list was ever written (D16). While the master item is absent the
pull makes the existence lookup of section 4.5 and no read at all. A background call forbids OS prompts
(`keystore_guard.forbid_prompts`), so neither the lookup nor the read shows a dialog. `admit` reads the state once, for `settle()` and `known()` together; a pull then
reads once more when its last file lands, and once for each time the list file's stat moved while it ran (section 5.2,
item 7).

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

A read on an app or agent surface first asks whether the master item exists, then (only if it does) reads the floor
item (one keystore read; the master key is cached by the keystore, the item is not) and opens the file. Neither step
depends on `provisioned.json` or on the `publishers/` directory, which a process of the same user can delete (D16).

**The existence lookup (a build item).** `keystore.py` has none: `_master` reads the secret with `get_password`, which on
macOS can raise the unlock or access dialog and in a background process raises `KeystoreLocked`, so using it would put
the human step back. The build adds `Keystore.master_present() -> bool | None` (true: present, false: absent, none: the
backend cannot say), made without reading the secret, without unlocking and without a prompt:

- macOS: `SecItemCopyMatching` for the service and account with `kSecReturnAttributes` and no `kSecReturnData`, and
  `kSecUseAuthenticationUI` set to fail, so no dialog. `errSecItemNotFound` is absent; success, or
  `errSecInteractionNotAllowed` (the item exists and its keychain is locked), is present.
- Linux Secret Service: `SearchItems` with the service and account attributes. It lists locked items without
  unlocking the collection and never calls `GetSecret`; an empty answer is absent.
- Windows Credential Manager: `CredReadW` for the target, reading the record's attributes and never the credential
  blob; `ERROR_NOT_FOUND` is absent. The store opens at logon, so there is no separate unlock.
- A backend that cannot answer without the secret (the `keyring` library has no attributes-only call; the code uses
  the platform API directly) returns none, and none reads as present: the floor item is then read, which fails closed.
  No usable backend (none, or one the keystore refuses as unsafe) is `fresh` when no file exists, because nothing could
  have written a list.

The lookup goes through `_flight` and `_call` so it is audited and counted, and it does not spend the read ceiling.
Tests (the suite never touches the real keystore, so the platform calls themselves are driven by hand on each
platform and reported, not run by the suite): `test_master_present_reads_no_secret_and_raises_no_prompt` (a fake
backend whose `get_password` fails the test), `test_master_present_is_true_for_a_locked_keystore`,
`test_master_present_is_none_when_the_backend_cannot_answer`.

| State | When | Effective set |
| --- | --- | --- |
| `fresh` | the master item is absent (the lookup above), whatever files are on disk; or there is no usable keyring backend (none, or one the keystore refuses as unsafe) and no file exists | built-in list |
| `ok` | the master item is present, the floor item reads, the file opens, authenticates, passes the schema, and its generation equals the item's | built-in with the records applied |
| `unreadable` | the master item is present and the floor item cannot be read now, or a file exists that nothing can open: the causes below | none: the pull is refused, no request is raised |
| `degraded` | the master item is present and the floor item is absent; the floor item reads and the file is missing; the floor item is absent and a file exists; the file fails its tag or the master is gone; the schema, an owner or a record is invalid (section 4.3); the generation differs from the item's | empty: every publisher asks |

The file is the authority when the state is `ok`; the item's `masked` list is read only by a reset. The floor is raised
before the file is written, so the only generation that opens is the one the item names. An older copy, a file from
another device and a file one generation ahead (a forged file, or a distrust whose raise the keystore refused, section
5.2) all read `degraded`, and so does the file a crash leaves one behind between a raise and a write (R9). There is no
tolerance, and a read never writes.

`unreadable` has a sentence for each cause. They are fixed text, never built from the owner or the Hub, and none says
"who Poolhouse does not know":

| Cause | Raised by | Sentence |
| --- | --- | --- |
| locked | `KeystoreLocked` | "Poolhouse cannot check which publishers you trust from a background process until a person has run `poolhouse-security unlock` once. Run it in a terminal and ask again." |
| denied | `KeystoreDenied` | "Your computer's keystore declined Poolhouse's request a short while ago, so Poolhouse cannot check which publishers you trust. `poolhouse-security unlock` in a terminal lets it try again." |
| busy | `KeystoreBusy` | "Poolhouse has used the keystore as often as it allows this hour, so it cannot check which publishers you trust until that passes." |
| not the installed copy | `KeystoreUnavailable` from `real_machine.refusal` | "This copy of Poolhouse is not the installed one, so it does not use this computer's keystore and cannot check which publishers you trust. Use the installed Poolhouse." |
| no keystore | no usable backend while a file exists | "This computer has no keystore Poolhouse can use, and a list of the publishers you trust is on disk that it cannot open." |

Only a device that has been provisioned (the master item is present) and is now locked, denied or busy sees these
refusals. A device never provisioned is `fresh` and sees none. Distrust writes only after provisioning (every write
needs the master), so no distrust can exist on a fresh device and the built-in list there hides nothing a person
removed.

`Keystore._ring` raises `KeystoreUnavailable` with a `cause` (`absent`, `unsafe`, `not_installed`) so `trust_file` can
pick the sentence; any other keystore error is `unreadable` with the keystore's own message. The pull is refused with the
sentence and nothing is queued: the request inbox is under the same keystore, so a request could not be raised or
answered. Other pulls and other items go on; the refused pull is parked as not pulled, which is the safe default, and
the caller (an agent, the page) reads the sentence.

`degraded` has one sentence: "Poolhouse's list of the publishers you trust is damaged or out of date on this device, so
it asks about every model, including models from publishers it normally knows." It replaces "who Poolhouse does not
know" for every owner and appears on the request, in `poolhouse-models publishers` and as a sentinel event. A degraded
approval is the ordinary `approve` for that commit; the trust offer is withheld (section 6.1).

While `degraded`, `trust`, the dialog's trust button and `stop-trusting` refuse. `distrust` writes no file record (every
publisher already asks), adds the owner to the item's `masked` list when it is a built-in owner (an item write; if the
keystore refuses it, the holding below still happens), and still holds and unpins the files. A person starts again with
`poolhouse-models publishers --reset` (section 5.4), never as a side effect of `trust`: it would bring a distrusted
built-in owner back without the person seeing it.

**Journey audit.** A person who installs Poolhouse and never opens the keystore pulls a built-in publisher's model
through an agent with no unlock, prompt or refusal: the master is absent, the state is `fresh`, nothing is read. The
first thing that provisions the keystore (any sealed write, which a person's interactive session or `unlock` makes)
changes the state to `ok` or `degraded` from then on, and no visible step is added at that moment. A provisioned,
locked keystore refuses an agent's pull with one sentence naming the one step, as every other keystore use already
does. The change adds no step to the first-run journey, no flag and no wait.

`reset` is a terminal command because it throws away a person's additions (the one-line reason AGENTS.md section 2
asks for where friction is kept); everything else a normal journey does is a tap or nothing.

### 4.6 Writing [proposal]

`trust.add(owner, grant, via, request="")` and `trust.remove(owner, grant)` check `grant.check(action, owner)`, take
the lock, read the state (`ok` or `fresh` only; `remove` also works from `degraded` and `unreadable`, section 5.2),
apply, raise the item to `n+1` with the new `masked` list, then write the file at `n+1`, then release. The first write
creates the item at 1 and then writes the file at 1. `add`, `reset` and `apply_approval` stop when the raise is
refused, with the keystore's sentence and nothing written; `remove`, and so `apply_distrust`, goes on to write the file
when it can (section 5.2, item 4). `trust.reset(grant)` is the only writer that starts from `degraded`; `distrust` of a built-in owner in that state
is the one write that changes the item's `masked` list alone. The settle functions (`apply_approval`, `apply_distrust`,
`note_denial`; section 6) and `reconcile` are the only other writers; they read the request from the sealed inbox by
id. A static test lists the callers of all of them.

### 4.7 Who reads the list: the surfaces, not the library [proposal]

- `admission.admit` takes a trust policy. The library entry points (`ph.models.pull`, `ph.serve.up` and the other
  importers of `poolhouse` as a library) pass theirs: the argument is the `trust=` that branch
  `fix/library-trust-and-net-policy` adds, not landed when this was written, and this design adds no parameter of its
  own. The library default allows every publisher and records the pin (`publisher`, `commit`, `source_ref`). It imports
  nothing from `hub.trust`, reads no `publishers/` file, makes no keystore call, raises no `model_publisher` request and
  is never refused for a keystore cause.
- The app and agent surfaces are `poolhouse-models` (`serve/models_cli.py:203`) and the pool's model pull
  (`fleet/models.py:284`), which the app and the agents drive. Each builds `hub.trust.app_policy()` and passes it; the
  build lists every other caller of `hub.pull` and `hub.fetch` and gives each the app policy or the library default,
  and a static test names them. Only the app policy calls `known`, `settle`, `recheck` and `reconcile`, reads the floor
  and shows the sentences of section 4.5. `poolhouse-serve up` on those surfaces runs `reconcile` before
  `serve/guarded.verify`; a library start does not read the list, and `guarded.verify` refuses a held file for every
  caller (`blocked`).
- A library caller that wants the list imports `poolhouse.hub.trust` and passes `app_policy()` itself.
- Tests: `test_a_library_pull_with_the_default_policy_makes_no_keystore_call_reads_no_trust_file_and_raises_no_request`,
  `test_nothing_under_api_imports_hub_trust`, `test_the_app_policy_refuses_with_the_sentence_of_each_cause`,
  `test_no_refusal_says_who_poolhouse_does_not_know_for_a_keystore_cause`.

### 4.8 A forged keystore state file [proposal]

`denied.json`, `rate.json` and `provisioned.json` are not sealed; a process of the same user can write them.

- `denied.json` with a later `until`: every keystore call of every process raises `KeystoreDenied` until a person runs
  `poolhouse-security unlock`, which removes the file.
- `rate.json` with entries at the ceiling, dated in the future (`_window` never ages one out, and each `_spend` writes
  it back) or not numbers (`float` raises out of `_spend`, a `ValueError` and not a `KeystoreError`): reads or writes
  raise `KeystoreBusy`, or that `ValueError`. `unlock` does not remove `rate.json`, and its own read passes through
  `_spend`.
- `provisioned.json` deleted: no effect on this list; `fresh` is decided by the master item (D16). On a provisioned
  device the keystore rewrites the file when it next reads the master.

What the app and agent surfaces do then: the pull is refused with the sentence of its cause (denied, busy, locked), in
one line, with no request, no queue and no wait; the next pull asks the keystore again; other items proceed. A library
caller is unaffected, because it never reads the keystore. A forgery of these files stops every keystore user (memory,
signing, the request inbox), so it is first the keystore's finding, and the build closes it there: `_window` and
`_denial` ignore an entry that is not a finite number or lies in the future and cap `until` at `COOLDOWN_S` from now, so
a forgery ages out within the hour instead of standing and a malformed file raises no `ValueError`. A same-user process
can renew it (R8); it never trusts anyone, because every effect here is a refusal. A person's `distrust` is never
blocked by it (section 5.2, item 4).

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
7. `trust.add`. If the keystore refuses the floor raise, nothing is written and the keystore's sentence is printed.
   Print "Trusted OWNER. `poolhouse-models distrust OWNER` takes it back." If files distrust once held for OWNER are
   still held, say so and name `poolhouse-security review`; trusting again never releases them.

An owner already trusted prints that and exits 0. A built-in owner that was distrusted is trusted again the same way.

### 5.2 `poolhouse-models distrust OWNER` [proposal]

1. `require_person`; the name is validated. The state is not a reason to refuse: item 4 says what each state does.
2. Plan: the pins with `publisher == OWNER`, their bytes, and the servers running them. The manifest is read through
   its `SealedFile` status; when that is `tampered` the command says "Poolhouse cannot read its record of what it pulled,
   so it cannot tell which files came from OWNER" and still removes trust. With sentinel off, nothing new is pinned
   (R6) and the command says so. Nothing to remove (not trusted, no pins) prints that and exits 0.
3. Print what will happen: "Poolhouse will ask about every model from OWNER again. N files (SIZE) pulled from OWNER are
   unpinned and held, and M running servers are stopped. Type yes to continue." Read from the controlling terminal
   (`person.confirm`).
4. In this order:
   (a) The record. `trust.remove` takes the lock and reads the state. From `ok` or `fresh` it raises the floor to `n+1`
   (the item, with the new `masked` list) and then writes the file at `n+1` with the `distrust` record. From `degraded`
   it writes no file and raises only the item's `masked` list. If the keystore refuses the raise (denied, busy, locked)
   and the master is already in hand, it writes the file at `n+1` anyway: the state then reads `degraded`, so every
   publisher asks until a person resets, and the owner is among them. If the keystore refuses the read as well, there is
   nothing sealed to write and nothing is. A refusal never stops (b): the person's `distrust` is not blockable by a forged
   `denied.json` or `rate.json`. The command prints which of these happened, in the cause's sentence.
   (b) The holding. Read the pins; for each pin with `publisher == OWNER`: `Store.hold_for_person(("model", path),
   "publisher distrusted: OWNER", {"publisher": OWNER, "pin": <the pin>}, Holding(path=path))`, whose hook stops the
   servers, and only when that returned a record, `manifest.unpin(path)`. A hold the store refuses (`quarantine` returns
   `None` when it is full) leaves the pin where it is and is printed. The hold comes first because an unpinned model that
   is not held is pinned at first use with no publisher by `serve/guarded.verify` (section 2), and a crash between an
   unpin and a hold would lose the only record of where the file came from.
   (c) Read the pins once more and repeat (b) until a pass finds none (at most five passes).
   (d) An activity record and a sentinel event, and the printed outcome.
   Re-running finishes whatever is left: (a) is skipped when the record exists in an `ok` list and retried otherwise,
   (b) continues over the remaining pins. `settle()` also completes (a) for an owner a `person_only` distrust hold names
   when the list has no `trust` record for that owner at or after the hold, so a distrust whose record the keystore
   refused is recorded the next time it answers. The second pin read closes the window of a download that pinned a file
   after the plan was made; the pull's own recheck (item 7) and `reconcile` close the rest.
5. `Store.hold_for_person` is `quarantine` with one difference: a record that is already `QUARANTINED` (held by
   sentinel for another reason, which a delegated lead could release) has `person_only`, `publisher` and `pin` merged
   into its evidence under the store lock and a history line added, instead of being returned untouched. A record with
   `person_only` leaves quarantine only through a person:
   - `Store.release` and `Store.purge` refuse it unless the grant was minted by a person. `HumanGrant` gains a `person`
     field, true from `mint`, `mint_pressed` and `mint_clicked`, false from the delegated branch of `mint_gated`, so the
     CLI's `release` and `purge` (`mint_gated("sentinel.quarantine", ...)`, `sentinel/cli.py:141,148`) are refused for
     these records while the gate is delegated.
   - `Store.settle_missing`, which has no grant, returns false for it, so `core._heal` (`sentinel/core.py:416`) neither
     releases it nor unpins its key.
   What `person` is worth: it separates a person's grant from a lead agent's delegated one. It is not a defence against
   a process that clears the agent marker and runs the installed code: `mint_clicked` takes the `answer` and the `label`
   from its caller and checks the marker and their equality (`human.py:91-103`; `heads_up.py:237`), and `mint_pressed`
   takes a `pressed` callable after the marker and terminal checks (`review.py:137`). R1 and R2 say what that is worth.
6. A held file is released only by a person: a typed or pressed release at a terminal, or the Release button of
   `poolhouse-security review`. A lead agent passing `sentinel.quarantine` cannot give these files back or delete them.
   The reason `publisher distrusted: OWNER` is in neither `NEEDS_PERSON` nor the decoy reasons, so the periodic dialog
   never asks about it, and a test pins that. The review screen shows it with its own sentence (`explain.BY_REASON`
   gains "You stopped trusting the publisher of this file, so it was moved aside and nothing uses it.", with advice that
   a release puts it back in use) instead of the unknown-reason fallback. On release a `release` hook for `model` puts
   the pin back from the record's evidence when the restored file hashes to the same digest, with `publisher` cleared
   (so a later `distrust` does not hold it again) and `commit`, `source_ref`, `origin` and `digest_from` kept; a file
   whose bytes differ is quarantined again as a changed file.
7. The pull side. `admit` returns how it passed (`list` or `approval`); a pull that passed by `approval` is never
   rechecked. A pull that passed by the list calls `admission.recheck` in the loop of `transfer._bring` after each file is
   in place and linked, and again when the pull ends or fails. `recheck` compares the list file's stat with the one taken
   at admit and reads nothing from the keystore while it has not moved; when it has moved, and at the end of every pull,
   it reads the item and the file. If `known(owner)` is now false or raises, every target path of this pull that is a
   model file (`_Target.final`, the blob or the file in `dest`) is held with item 4(b)'s hold, whether or not it was
   pinned (pinning is absent with sentinel off, skips other kinds and fails soft, section 2), the files not yet fetched
   are not fetched, and the pull raises `NotApproved` with "Trust in OWNER was removed while this was downloading, so its
   files were held." The check comes after the file is in place: with the record written before the check, the pull
   holds itself; with it written after, the pin was already in place before `distrust` read the pins the second time (a
   file that was never pinned is R6). A keystore that cannot be read at that moment holds the files the same way, because a lock a same-user process can
   induce must not let a distrusted publisher's file through; a person releases them, and that is the cost of a lock
   that lands after a download.
8. `reconcile`. Holds, by item 4(b), every pin whose `publisher` has a `distrust` record, is in the item's `masked`
   list, or is named by a `person_only` distrust hold that no later `trust` record follows. It runs in `settle()`, at the
   start of a serve on an app or agent surface and when `known()` answers false for a distrusted owner (once per process
   per owner). It catches a pin that landed after the last pass, a pull that passed by the list in a process that
   crashed before its recheck, and a library pull that recorded the pin of a publisher the person had stopped trusting
   (R11).

`distrust` has no flag that keeps the files; it removes trust and what the trust vouched for.

### 5.3 `poolhouse-models stop-trusting OWNER` [proposal]

The non-technical road to `distrust`. Any process, an agent included, may run it; it raises one human-only
`publisher_trust` request and writes nothing else (section 6.4). Run again after a person has answered, it applies the
answer and prints what was held. It is refused for an owner that is neither trusted nor the publisher of a pin, when a
person said no to it in the last 24 hours, and when one is already waiting. An agent can therefore raise one proposal
per owner per day, each of them destructive if a person accepts (R10).

### 5.4 `poolhouse-models publishers [--json] [--reset]` [proposal]

Without `--reset`, read-only and open to any process. One line per owner: name, `built in` or `added`, `distrusted`,
the date, the way it was answered (`terminal`, `dialog` or `ui`), and the state of section 4.5 with its sentence.
Anything that came from the Hub is passed through `safetext.escape`. It is how a person finds what to distrust, and
where an answered request is settled (section 6.3).

`--reset` is a person's: `require_person` and a typed `yes` from the controlling terminal after this text: "Earlier
additions are lost. Built-in publishers you stopped trusting stay stopped: LIST. Add others again with `trust`." It
raises the item to `floor + 1` and then makes a new file at that generation with `since` now, holding a `distrust`
record for every built-in owner in the item's `masked` list, named by a `person_only` distrust hold in sentinel's store,
or found as a `distrust` record in any file that authenticates (an authenticated file of any generation can only add
distrust at a reset, never trust). A reset therefore never returns a distrusted built-in owner, even one that supplied
no pinned file, and a keystore reset that loses the master still leaves the item. A `tampered` or `recovered` sentinel
store makes the second source smaller, never the first.

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

- a desktop session exists on this device, so the dialog can be shown (D15); where it does not, or the request is
  answered in the page or at a terminal, there is no persistent path and the offer is the one-time approval only;
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
from now on. `poolhouse-models distrust` takes it back." It is in `APPROVING` and in a new `DIALOG_ONLY` set: the page
does not list it, the terminal does not list it, and `Store.answer` refuses it for any `via` but `dialog`. Choices stay
in the fingerprint, so what is shown is what is answered. The desktop dialog shows Deny (default and Escape), Approve
and Always trust.

The offer rules are checked again when the answer is applied (section 6.3), because nothing in the request model
enforces them: `make_choices` accepts any id in `CHOICES` for any request (`requests/model.py:178-184`), and a request
can be raised by any caller of `raise_request`.

### 6.2 What the person sees [proposal]

First model from a new publisher: the same one request, one more button in the desktop dialog. No new window, no
terminal. Choosing `approve` is today's behaviour. Choosing `trust-publisher` ends asking for that publisher. Answered
in the page or at a terminal, or on a device with no desktop session, the request shows `approve` and `deny` and trusts
nothing beyond that commit.

### 6.3 How it is persisted [proposal]

Never from a row in the inbox, never from the page or a terminal, and never from anything the agent says. The process
that put the dialog up (the scan loop `serve/guarded.py` wires; the machine layer, above `hub`; the layer check at build
decides the import) subscribes `trust.on_answered` with `requests.subscribe`. The listener runs in that process only,
for an answer that process recorded with `via == "dialog"` from the button the dialog returned, and calls
`trust.apply_approval(request_id)`, which reads the request from the sealed inbox by id and requires:

- `state == approved`, `answered_by == "dialog"`, `kind == "model_publisher"`, the choice `trust-publisher` among the
  request's choices, and `answered_at` within two minutes of now;
- the `host` field is the official host now, and the `publisher` field is a valid, unfolded owner;
- the state is `ok` or `fresh`, the request id is not in `applied`, and `answered_at` is later than the list's `since`
  and than the owner's last record of any kind;
- the offer rules of section 6.1 again, now: no `denied` entry of the owner in the last 24 hours, `remote.on_branch(repo,
  commit)` true (a Hub that cannot answer persists nothing), fewer than three distinct repositories of the owner in the
  last 24 hours, and the fold test;
- the `reason` equals the sentence rebuilt from the request's own fields;
- a grant minted with `mint_clicked` from the label the dialog returned (`dialog.ask` hands it to the listener), and a
  floor raise the keystore accepts.

Then it writes a `dialog` record and adds the id to `applied`. The pull the person was asked about passes by the
one-time approval either way (`approved()` does not look at the choice). If any condition fails, nothing is persisted and
the tap approved only that commit. `trust.settle()` runs at the start of every `admit`, of `publishers` and of
`stop-trusting`, and in the scan loop; it never calls `apply_approval`. It calls `note_denial` for a denied
`model_publisher` request (written to `denied` with its answer time), `apply_distrust` for a `stop-trusting` answer
(section 6.4), the completion of item 4(a) of section 5.2, and `reconcile`.

The `reason` check is circular: the sentence and the fields come from the same row, so a writer of the inbox makes them
agree. It catches a row built in other words by other code, not an attacker who can write the inbox, and that is why no
row persists trust: anything that runs the installed code once the keystore is unlocked can write an approved row, and
`answered_by` is the string its writer passed. The persistent write is made in the dialog process, at the click. That
closes every path through the inbox, the page and a terminal; it does not stop a process that runs the installed code
with the agent marker cleared and calls the writer itself with a grant it mints (R1, R2). The fix is a phone-signed
approval that can carry this choice (`docs/phone-approval.md`).

### 6.4 Distrust by tap [proposal]

`stop-trusting OWNER` raises a human-only `publisher_trust` request (a new `Kind`, `human_only=True`, `destructive=True`),
subject "stop trusting OWNER", with words Poolhouse builds from its own counts: "Poolhouse will ask about every model
from OWNER again. N files (SIZE) pulled from OWNER will be held and M running servers stopped. You release held files
yourself." Its choices are `stop-trusting` (label "stop trusting", effect "Poolhouse asks about every model from this
publisher again, holds the files it pulled from them and stops the servers using them; you release held files
yourself.", in `APPROVING`) and `deny`. The dialog default is Deny. At most one waits per owner; a `deny` is written to
`denied`, which blocks a new proposal for 24 hours; a stream of proposals is one decision, as in section 6.1. The agent
that proposed is told the answer is read the next time it repeats the command. `trust.apply_distrust(request_id)` has the
conditions of 6.3 that apply to a removal (state, answer `stop-trusting`, `applied`, order against `since` and the
owner's last record, no more than a day old) and runs the steps of 5.2 from (a), with `via` of the path that answered; it
needs no grant because it only removes trust, and the `person_only` hold is what a lead agent cannot undo. It may be
applied from any path that answers, the page and a terminal included: the answer's gate there is the agent marker (R2),
and the cost of a wrong yes is files held.

## 7. Attack-first pass

The first draft was the author's pass and did not count. An independent agent broke it (round 1) and broke the revision
again (round 2, the last); this section carries both rounds' fixes. AGENTS.md section 1 still requires a different agent
to break the revised design before it is built if it changes again. Each entry names the defence and a test that fails
without it (test files in section 9). "Defended in the design" means the design closes it; nothing is built, no test is
written.

**A1. A copied or cloned state directory.** Defence: the file opens only under the keystore master of this OS user on
this device; elsewhere it fails its tag. A file present whose tag fails is `degraded`, not `fresh`, and so is a file
with no item to match it; the floor is in this device's keystore, read once the master item is present, and travels with no
file. Tests: `test_a_list_copied_under_another_keystore_is_degraded_and_trusts_nothing`,
`test_a_missing_file_with_the_item_present_is_degraded_not_fresh`, `test_a_file_with_no_item_is_degraded`. Status:
defended in the design. A restored image of the same device and user is A2.

**A2. Replay of an old trust record.** Defence: the file carries `generation`; the floor is the keystore item, raised
before the file is written, and the two must be equal. An older file, a deleted file, a file one generation ahead and a
file whose item was deleted are `degraded`; deleting sentinel's state or putting its `.prev` back lowers nothing,
because nothing reads it. A stop-trusting answer older than the owner's last record is ignored. Tests:
`test_an_older_list_put_back_after_a_distrust_is_degraded`, `test_deleting_or_rolling_back_sentinel_state_does_not_lower_the_floor`,
`test_a_file_one_generation_ahead_of_the_floor_is_degraded`, `test_a_crash_between_the_raise_and_the_write_reads_degraded_and_trusts_nothing`,
`test_the_floor_is_raised_before_the_file_is_written`, `test_a_stop_trusting_answer_older_than_a_later_trust_is_not_applied`.
Status: defended in the design. R1 says what is left; R9 is the crash window and R12 the refused raise.

**A3. A forged record.** Defence: AEAD tag under a key only the keystore holder reaches; a file that fails it is
`degraded`; a record that is invalid, unknown or repeated makes the whole list `degraded` and none is dropped. Tests:
`test_a_list_written_without_the_key_is_degraded`, `test_an_invalid_or_unknown_schema_record_makes_the_list_degraded_and_drops_nothing`.
Status: defended in the design.

**A4. A malicious agent writes the list.** Defence: (a) the file is sealed, see A3; (b) `trust.add`, `trust.remove` and
`trust.reset` refuse a call with no person's grant; (c) the commands refuse a process with an agent marker and one
without terminals; (d) the bash guard and `agent_may` refuse the command line, the module name and the state directory;
(e) an agent may only propose (`stop-trusting`), and a proposal changes nothing until a person answers. Tests:
`test_the_writer_refuses_a_call_without_a_persons_grant`, `test_trust_and_distrust_refuse_a_process_an_agent_started`,
`test_an_agent_tool_call_naming_the_trust_commands_or_their_directory_is_refused`, `test_a_delegated_grant_is_not_a_persons_for_a_held_publisher_file`,
`test_the_guard_refuses_trust_distrust_and_reset_but_lets_stop_trusting_through`. Status: defended in the design. An agent
that clears the marker and owns a pseudo-terminal passes every terminal gate that has no typed confirmation
(`docs/person-commands.md`); `trust` and `distrust` read the confirmation from the controlling terminal device, which
that agent has only if it made it its own: accepted risk R1.

**A5. An injected instruction (board message, file, page, tool result) to trust a publisher.** Defence: it can only
reach the same doors as A4. No row persists trust (D15): the inbox is written by anything that runs the installed code
once the keystore is unlocked, and `answered_by` is the `via` string the answering caller passed (`Store.answer` checks
only that it is one of `VIAS`), so a `trust-publisher` answer in the inbox is the one-time approval and nothing else.
The persistent write is made only by the process that showed the desktop dialog, from the button it got back, after the
offer rules run again (section 6.3). `requests.answer` for a human-only kind meets `require_person` whatever the
registry delegates, but for `ui` and `dialog` that is the agent-marker check alone (`terminal = (True, True)`,
`requests/inbox.py:150`): a process that clears the marker passes it. The reason-equals-sentence check is circular and
is not the defence (section 6.3). Tests: `test_a_request_raised_in_other_words_with_the_trust_choice_trusts_nothing`,
`test_an_approve_answer_does_not_trust`, `test_a_denied_or_expired_trust_request_trusts_nothing`,
`test_an_approved_trust_publisher_row_written_into_the_inbox_persists_nothing`,
`test_trust_publisher_is_refused_from_the_page_and_the_terminal`, `test_settle_never_applies_a_trust_publisher_answer`,
`test_an_agent_cannot_answer_the_trust_choice`, `test_the_only_callers_of_the_trust_writers_are_the_commands_and_the_settle_step`.
Status: defended in the design for persistence; the one-time approve and the stop-trusting answer are a marker check
(R2).

**A6. A lookalike or homoglyph owner.** Defence: names are ASCII Hub names compared whole; `trust` refuses a name whose
fold equals any built-in, stored or distrusted owner's; the dialog offer is withheld for one; at fetch `Unsloth` is not
`unsloth`. Tests: `test_trust_refuses_an_owner_that_folds_to_another`, `test_trust_refuses_a_lookalike_of_a_distrusted_owner`,
`test_trust_refuses_a_name_that_is_not_a_hub_name`, `test_the_tap_is_not_offered_for_a_lookalike`,
`test_a_stored_owner_matches_character_for_character`. Status: defended in the design. An owner that is merely close
(one letter from a known name) is shown by `publishers`; there is no distance rule, because a legitimate organisation
can sit close to another: accepted risk R4.

**A7. A publisher compromised after it was trusted.** Defence: `distrust` records the removal first when the keystore
answers, then holds each pinned file and only then unpins it (reading the pins again after), with a record only a person
releases, purges or discards (merged onto a record that was already held), and stops the servers; every pull in the
window was pinned with `publisher`, `commit` and `source_ref`, so the set is exact. `Store.release`, `Store.purge` and
`Store.settle_missing` all honour `person_only`. Trusting again releases nothing. A release restores the pin; the
periodic dialog never lists the hold and the review screen explains it. Tests: `test_distrust_unpins_holds_and_stops_what_the_publisher_supplied`,
`test_distrust_commits_the_removal_before_it_touches_a_file`, `test_distrust_holds_a_file_before_it_unpins_it`,
`test_a_hold_the_store_refuses_leaves_the_pin_in_place`, `test_a_file_distrust_held_is_released_only_by_a_person`,
`test_a_lead_agent_cannot_purge_a_distrust_hold`, `test_settle_missing_leaves_a_person_only_hold`,
`test_distrust_marks_a_file_sentinel_already_held_as_person_only`, `test_a_distrust_hold_has_its_own_sentence_and_is_not_in_the_periodic_dialog`,
`test_releasing_a_distrust_hold_restores_the_pin_without_the_publisher`, `test_trusting_again_does_not_release_what_distrust_held`.
Status: defended in the design for pinned files. Accepted risks R5, R6: a file the publisher supplied that was never
pinned, a blob two publishers both supplied (one pin per path), and a first pull of a replaced file before anyone knew
(HANDOFF, "A compromised known namespace").

**A8. A downgrade, official Hub to another endpoint, or a check lowered by where the name came from.** Defence: the
record has no host; `publishers.official` runs for every owner; `trust` and the dialog offer refuse a mirror; the
dialog's approval is bound to the `host` field; the branch and marker checks run for stored owners. Tests:
`test_a_stored_publisher_on_a_mirror_asks_with_the_host`, `test_trust_refuses_while_the_endpoint_is_not_the_official_hub`,
`test_the_tap_is_not_offered_on_a_mirror`, `test_a_trust_answer_for_one_host_does_not_apply_on_another`,
`test_a_stored_and_a_built_in_publisher_are_checked_alike`, `test_the_trust_record_has_no_field_a_check_reads`.
Status: defended in the design.

**A9. Approval fatigue.** Defence: at most ten `model_publisher` requests wait; the trust choice is withheld for 24
hours after a denial of the owner (stored in the list) and once requests for three distinct repositories of it were
raised, and both rules run again when an answer is applied, so a stream is one decision; it is a separate button, never
the default (the dialog's default is Deny), and human-only kinds are excluded from "answer all". A `stop-trusting`
proposal is rate-limited the same way (R10). Answering a stream with `approve` approves single commits and trusts
nothing. Tests: `test_the_trust_choice_is_withheld_after_a_denial_or_three_repositories`, `test_a_denial_recorded_after_the_offer_stops_the_answer_persisting`,
`test_a_denial_survives_the_request_log_trimming`, `test_a_retried_pull_counts_once_toward_the_stream_rule`,
`test_the_dialog_default_is_deny_and_trust_is_never_first`, `test_answer_all_never_covers_a_trust_choice`,
`test_stop_trusting_is_not_raised_again_within_a_day_of_a_no`. Status: defended in the design.

**A10. Takeover by whoever is merely present.** Defence: at a terminal `trust` needs the name typed back exactly from
the controlling terminal; a bare Enter or `y` does nothing; `distrust` and `--reset` need a typed `yes`; every change
is an activity record, a sentinel event and a line in `publishers`, and one command undoes it. The dialog is a button
on the person's own screen, and a persistent trust is written only in the process that showed it (D15); the one-time
approve and the stop-trusting answer are request answers whose gate for `ui` and `dialog` is the agent marker alone
(R2). Tests: `test_trust_needs_the_owner_name_typed_back_exactly`, `test_trust_without_a_terminal_is_refused`,
`test_every_change_is_recorded_and_listed`. Status: terminal defended in the design; the rest is accepted risk R2 until
phone approval (`docs/phone-approval.md`) can carry the choice.

**A11. Concurrent edits.** Defence: one lock around read, apply, raise and write; atomic replace; a distrust racing a
trust leaves the later one; the settle step takes the same lock. Tests: `test_parallel_trusts_of_different_owners_all_persist`,
`test_a_trust_and_a_distrust_of_one_owner_leave_the_later_one`, `test_a_reader_never_sees_a_half_written_list`.
Status: defended in the design.

**A12. A compromised or malicious pool member.** Defence: the list is per device and no board entry, pool message or
file reaches a writer (A5's caller test); a copy served by a member is still checked against the Hub's digest after the
publisher check. Tests: the caller test above and `test_a_member_served_copy_of_a_distrusted_publisher_asks_first`.
Status: defended in the design. Pool-wide trust is not designed here.

**A13. A stolen phone or key; a forged or below-threshold signature.** Not applicable: no phone key or signature takes
part in the terminal path or the dialog. When phone approval lands, `trust-publisher` and `stop-trusting` are choices it
should carry, named in `docs/phone-approval.md` then.

**A14. A name that does not exist yet.** Defence: `trust` needs the official Hub to hold models for the owner now.
Test: `test_trust_refuses_an_owner_the_hub_holds_nothing_for`. Status: defended in the design for today. A trusted name
the Hub frees and someone else registers is accepted risk R3.

**A15. A download racing a `distrust`.** The pull was admitted by the list, the person distrusts the owner while a
multi-gigabyte, multi-file download runs, and files land after `distrust` read its pins, or were never pinned (sentinel
off, a pin that failed and was logged, a library pull). Defence: the record is written first when the keystore answers;
`distrust` reads the pins again after; the pull rechecks after each file and at its end, holds its own target paths
whether or not they were pinned, and does not fetch the files that remain; `reconcile` holds any pin of a distrusted
publisher that appears later. Tests: `test_a_pull_that_finishes_after_a_distrust_holds_its_own_files`,
`test_a_pull_rechecks_after_each_file_and_stops_fetching`, `test_a_pull_holds_its_target_paths_when_nothing_was_pinned`,
`test_distrust_holds_a_pin_that_lands_between_its_plan_and_its_removal`, `test_reconcile_holds_a_pin_of_a_distrusted_publisher`,
`test_a_pull_approved_once_is_not_held_by_the_recheck`, `test_the_recheck_reads_the_keystore_only_when_the_list_moved`.
Status: defended in the design.

**A16. A reset followed by a replay.** A stop-trusting answer is kept seven days in the inbox. Without a rule, `--reset`
(which empties the records, `distrust` ones included) lets the next `settle()` re-apply an old answer. (A trust answer
never re-applies: no row persists trust.) Defence: the list's `since`, the applied ids, the owner's last record, and the
reset's re-masking (section 5.4). Tests: `test_an_answer_from_before_a_reset_is_not_applied`,
`test_a_stop_trusting_request_is_applied_once`, `test_a_reset_keeps_built_in_owners_distrusted_before_it`. Status:
defended in the design.

**A17. Sentinel switched off, tampered or recovered.** `POOLHOUSE_SENTINEL=off` (with a reason) turns pinning and the
digest check off; a tampered or recovered sentinel state reads as zeroed counters. Defence: nothing about the list, the
floor or `known()` reads sentinel's mode, counters or state; only the pin plan, the hold, `reconcile` and the
completion of a distrust use the manifest and the store, only to remove trust, and say so when sentinel is off or the
manifest is unreadable. Tests: `test_the_list_and_its_floor_do_not_depend_on_sentinels_mode`,
`test_distrust_with_sentinel_off_removes_trust_and_says_nothing_was_tracked`. Status: defended in the design; files
pulled while sentinel was off are R6.

**A18. Induced degradation or refusal.** A process of the same user that can write the state directory deletes or
corrupts the list file, or forges the keystore's state files (section 4.8), and the person is then asked about every
model, is refused, or is pushed to `--reset`. Defence: deleting the file or `publishers/` with the item in place reads
`degraded` (D16), not `fresh`; deleting `provisioned.json` changes nothing, because `fresh` is decided by the master item;
a forged `denied.json` or `rate.json` is `unreadable` with its own sentence, once per pull, with no request and no
queue, and the keystore build ages it out (section 4.8); `degraded` carries the one plain sentence, makes no trust
offer, and asks with ordinary per-commit approvals under the ten-wait cap; the reset keeps built-in owners distrusted
and loses only what a person added. A locked keystore is `unreadable`, never `degraded`, so a lock never looks like
tampering. The attacker gains friction (R8, R13). Tests:
`test_a_degraded_list_asks_with_its_own_sentence_for_a_built_in_publisher`, `test_each_keystore_cause_refuses_with_its_own_sentence_and_raises_no_request`,
`test_degraded_is_not_reported_for_a_locked_keystore`, `test_reset_is_a_persons_and_the_only_way_out_of_a_degraded_list`.
Status: defended in the design; the nuisance remains.

**A19. A delayed answer.** A stop-trusting answer given and not settled for days is applied when something later runs
`settle()`. Defence: it is applied only within a day of `answered_at`, only if newer than the owner's last record and
`since`, and the settle runs on every fetch, `publishers`, `stop-trusting` and the scan loop, so an answer waits only
until the next of those runs. A `trust-publisher` answer is never applied by `settle()`: the dialog process applies it
within two minutes of the click or not at all. Tests: `test_a_stop_trusting_answered_a_day_ago_is_not_applied`,
`test_a_trust_answered_in_the_dialog_is_not_applied_two_minutes_later`, `test_an_answer_older_than_a_later_terminal_trust_or_distrust_is_not_applied`.
Status: defended in the design.

**A20. An old build.** A build from before this feature ignores the list and trusts the built-in owners. Not defended
here: AGENTS.md section 4 has every agent run the latest build, automatically, and a stale build is reported as a defect.
Accepted risk R7.

**A21. A record collision.** Two records for one owner, two owners that fold alike, and a request id used twice.
Defence: the writer refuses each; a file that holds one is `degraded`; an id
applied once. Test: `test_a_list_with_two_records_for_one_owner_or_two_alike_owners_is_degraded`. Status: defended in the
design.

**A22. Rolling back by files alone.** A process that can write the state root deletes `publishers/` and
`provisioned.json` to make the device look as if it never had a list. Defence: `fresh` is decided by the master item
alone (D16), which these files do not touch. With the master present a missing file is `degraded`, whether or not
`provisioned.json` is there. Tests:
`test_deleting_the_list_and_provisioned_json_does_not_return_a_distrusted_publisher`, `test_a_device_with_no_master_item_reads_fresh_and_touches_no_secret`.
Status: defended in the design for files. A same-user process that deletes the master item itself returns the device to
`fresh` and the built-in list (R1); it also destroys every secret sealed under that master, which is why it is a
residue and not a defence. R13 is now a narrow cost.

**A23. A forged keystore state file.** `denied.json`, `rate.json` and `provisioned.json` are unsealed (section 4.8).
Defence: every effect is a refusal on the app and agent surfaces, with the cause's sentence, no request and no queue; a
library pull is unaffected; a person's `distrust` still holds the files, stops the servers and says what the keystore
refused; the keystore ignores a non-numeric or future-dated entry and caps `until`. Tests:
`test_a_forged_denied_json_refuses_the_pull_with_the_denied_sentence_and_raises_no_request`,
`test_a_forged_rate_json_refuses_with_the_busy_sentence`, `test_a_future_dated_or_non_numeric_rate_entry_is_ignored`,
`test_distrust_holds_the_files_and_reports_when_the_keystore_refuses`, `test_a_library_pull_is_unaffected_by_a_forged_keystore_state`.
Status: defended in the design; the nuisance remains (R8).

**A24. A library caller (D14).** A process that calls the library entry points pulls under the library default, which
allows every publisher and records the pin, so a distrusted publisher's model is not refused at that pull. Defence: the
pin carries the publisher, `reconcile` holds it at the next `settle()`, scan or app-surface serve start, and a library
caller that wants the list passes `hub.trust.app_policy()`. Tests: `test_a_library_pull_records_the_pin_with_its_publisher`,
`test_reconcile_holds_the_pin_a_library_pull_recorded_for_a_distrusted_publisher`. Status: accepted risk R11 (owner's
standing rule: library users get no new prompts or refusals).

## 8. Findings and decisions

Defended in the design: A1 to A12, A14 to A19 and A21 to A23 above, with the residues named under each; the dialog
default; the old answer outliving a `distrust` or a reset; a delegated lead releasing, purging or settling files
`distrust` held (including a file sentinel already held); the case-folding `person.typed` defeating an exact typed
name; a floor that a same-user process could lower; a list that stopped working when sentinel was off; a rollback by
files alone. These are designed, not built.

What the first independent review claimed and what the code showed (round 1). Each was checked against the code and
holds, except where noted:

- The floor was forgeable (sentinel's `SealedFile` key sits beside it, and a `tampered` load reads as counters of 0), the
  list depended on sentinel's mode, a retained tap answer survived a reset, `quarantine` left an already-held record
  without `person_only`, a download could land after `distrust`, forced degradation was a lever, and distrust holds
  had no explanation of their own and a wrong release wording. All are fixed above.
- The review asked for the `known()` check just before pinning. The pin happens in `net/download._pin_pulled`, which
  cannot import `hub`, and a check before the pin leaves a window; the check is after the file is in place in
  `transfer._bring` (section 5.2, item 7).
- The review asked that holds be excluded from the periodic dialog. They already are, because `needs_person` is false
  for the new reason; the fix is to say so and pin it with a test. They do appear in `review()`, with the
  unknown-reason sentence and a Release button that is a person's click, so "released only by a person at a terminal"
  was wrong and is now "released only by a person".
- The review asked that a `tampered` or `recovered` sentinel state be `degraded` and not floor 0. That is superseded:
  the floor is no longer in sentinel's state, so nothing about it depends on that status.

What the second independent review claimed and what the code showed (round 2, the last). Each was checked against the
code:

- The first revision said the floor item goes "through `_call`, so the keystore's gate, counts and audit apply". `_call`
  counts and audits but does not run `_gate` (`keystore.py:371-400`); the item goes through `_flight` and `_call`.
  Holds; fixed in section 4.4.
- A file one generation ahead was accepted and raised by the reader, which left a window in which the previous
  generation also read `ok` (R9). Holds. The floor is raised first and the file written second, there is no tolerance,
  and a read never writes.
- `fresh` depended on `provisioned.json` and the `publishers/` directory, both deletable by a process of the same user.
  Holds (`Keystore.status` reads `provisioned.json` without a backend call, `keystore.py:243-253`, and `_gate` reads it
  too, 331-342). The lead then replaced the file-independent probe with the master-item rule (D16): `fresh` is the
  master item absent, found without a secret read.
- A forged `rate.json` or `denied.json` could block a person's `distrust` and stop pulls. Holds, and is worse than
  stated: a future-dated entry is never aged out and a non-numeric one raises `ValueError` out of `_spend`, which
  `unlock` passes through (section 4.8). The keystore build ignores such entries; `distrust` holds files and reports
  when the keystore refuses. One part of the request does not hold as worded: with the reads refused there is no
  sealed record to write, so `distrust` records the removal only when the keystore answers; the holds carry it
  meanwhile and `settle()` completes the record from them.
- `apply_approval` checked only ids. Holds: `make_choices` (`requests/model.py:178-184`) validates the id and nothing
  about when it may be offered. The offer rules run again when an answer is applied (section 6.3).
- The recheck ran once after the last pin and held by pin. Holds, and a pin is not always there: sentinel off, a pin that
  failed and was logged, kinds other than `gguf` and `safetensors` (`net/download.py:328-355`). The recheck runs after
  each file and holds the pull's target paths; `reconcile` covers the rest. Holding before unpinning holds too:
  `serve/guarded.verify` pins an unpinned model at first use with no publisher (`serve/guarded.py:203-215`).
- A distrust hold could be purged and settled by a lead agent. Holds: `Store.purge` takes any grant and the CLI mints it
  with `mint_gated` (`sentinel/cli.py:148`); `Store.settle_missing` takes none (`sentinel/store.py:400`, called from
  `core.py:416`). All three exits honour `person_only`.
- R1 said the keystore is closed to anything but the installed software. `real_machine` checks where the module file is,
  so any process running the installed copy passes it and `_gate` lets it read once a person has unlocked. R1 is
  rewritten. R2 and A5 overstated the person gate: for `ui` and `dialog` it is the agent marker alone
  (`requests/inbox.py:150`). `mint_clicked` and `mint_pressed` take their evidence from the caller
  (`sentinel/human.py:91-103`, `heads_up.py:237`, `review.py:137`), so `person` separates a person's grant from a lead
  agent's delegated one and nothing more. Both are rewritten. The floor item survives `Keystore.reset` and
  `docs/keystore.md` says so (section 10).
- D-C's "dialog only" is stronger than a row in the inbox and not stronger than a process that runs the installed code
  with the marker cleared; it is specified as the in-process write of section 6.3, and R1 and R2 say what is left.

Accepted risks, named for the owner:

| # | Risk | What bounds it |
| --- | --- | --- |
| R1 | The list is sealed under a subkey of the keystore master and its floor is a keystore item. `real_machine` checks where the code is (the module file under `~/.poolhouse/runtimes` on the real home, not a test run), not which interpreter or command started the process: any process that runs the installed copy passes it, an agent's included, and `_gate` lets a process with no terminal read once a person has unlocked. Such a process can derive the master, forge the file, write the item and lower the floor by calling the keystore directly, and a process that talks to the OS keystore itself needs neither. A pseudo-terminal under a cleared marker passes `require_person` and can type the confirmation. | The commands, the grants and the guard refuse everything short of that. The list is no stronger than the master key under everything else Poolhouse seals (memory, signing, the request inbox) and no weaker; the OS keystore's access rule decides how far that reaches. A deleted file, `provisioned.json` or `publishers/` returns nothing (A22). A same-user installed interpreter can delete the master item, which returns the device to `fresh` and the built-in list; it also destroys everything else sealed under that master. |
| R2 | Any process of the account that clears the agent marker can answer a request through `ui` or `dialog`, where the marker is the only check (`requests/inbox.py:150`), so it can give the one-time approve and answer a stop-trusting proposal. None of those answers persists a trust (D15): the only way to one is to call the writer directly with a grant it mints itself (`mint_clicked` takes its evidence from its caller), which is R1. | Answers are audited and listed; one command undoes a trust; a stop-trusting costs files held, which a person releases. A phone-signed approval that carries these choices (`docs/phone-approval.md`) is the fix. |
| R3 | A trusted name the Hub frees and another account registers. | The built-in list has the same exposure. To look into: pinning the Hub's account id if its API exposes one (not verified). |
| R4 | A near-miss owner name trusted on purpose. | The typed name, the Hub's account type, member and model counts, and `publishers`. |
| R5 | A blob two publishers supplied belongs to the one that pinned it last. | `distrust` of the other leaves it; rare, and the sentinel pin check still applies. |
| R6 | Files never pinned (pulled with sentinel off, or from a path nothing pinned), a manifest that cannot be read, and a replaced file pulled before anyone knew. | `distrust` says when it could not tell; the pull's own recheck holds its target paths; existing "Not armed" items in `docs/sentinel.md`; an independent digest source is HANDOFF work. |
| R7 | A build older than this feature ignores the list. | Every agent runs the latest build automatically (AGENTS.md section 4); a stale build is a defect. |
| R8 | A same-user process can force `degraded` by deleting or corrupting the file, and `unreadable` by forging `denied.json` or `rate.json`. | It buys the attacker only friction; a person resets with one command and loses only what was added; the keystore build ages a forgery out (section 4.8). |
| R9 | A crash between the floor raise and the file write leaves the file one generation behind the item, so the list reads `degraded` and every publisher asks until a person resets. | It returns no trust; the window is one crash wide and the cost is R8's. |
| R10 | An agent can raise one `stop-trusting` proposal per owner per day (at most one waits per owner, a no blocks it for 24 hours, at most ten requests of a kind wait). Each is destructive if a person accepts it: the owner's files are held and its servers stopped. | A proposal changes nothing until a person answers; a no is remembered; the cost of a wrong yes is files held, which only a person releases, and a trust restored at once with `trust`. Accepted by the lead. |
| R11 | A library caller pulls under the library default (allow, record the pin), so a distrusted publisher's model is not refused at that pull, and a library `ph.serve.up` serves it under the pin check until `reconcile` holds it. | The pin carries the publisher; `reconcile` holds it at the next `settle()`, scan or app-surface serve start; a library caller can pass `app_policy()`. Owner's standing rule: library users get no new prompts or refusals. |
| R12 | When the keystore refuses the floor raise of a `distrust`, the file is at `n+1` and the item at `n`: the list reads `degraded`, but a copy of the previous file put back reads `ok` and trusts the owner until a raise succeeds. | The attacker needs the old file and to have caused the refusal. Where the owner supplied a pinned file, a `person_only` hold names it, and the `settle()` that runs before `known()` answers completes the record and raises the floor as soon as the keystore answers; where it supplied none there is no hold to complete from and the window lasts until a raise succeeds. `distrust` prints that this happened. |
| R13 | Only a device that has been provisioned (the master item is present) and is locked, denied or busy refuses an agent's pull on an app or agent surface, with the sentence of its cause; a checkout or worktree run of an app surface on the real machine is refused with the not-installed sentence. A never-provisioned device is `fresh` and refuses nothing. No distrust can exist on a fresh device, because every write needs the master. | The refusal reaches only a keystore a person already set up and that is now closed; each refusal is one sentence naming what to do and parks that pull. Nothing blocks a first run on a person. |

Decided here (the first draft left Q1 and Q2 to the owner; Q3 to Q5 are the lead's, on the owner's standing rules):

- **Q1. A sealed file in `hub`, with its floor in one keystore item.** Not the graph and not a core module that owns a
  graph. The reason that decides it is that this is authorization state needing a monotonic floor under the keystore:
  the graph is a rebuildable index with neither. The layer rule and the second copy of the publisher-to-files relation
  (section 4.4) agree.
- **Q2. A process that cannot reach a provisioned keystore refuses; it does not ask, and it does not read the built-in
  list.** Once the master item is present, a locked, denied, busy or not-installed keystore is `unreadable`: the pull stops with one plain
  sentence for its cause, because the request store is the same keystore and could not hold an ask. Letting such a
  process read the built-in list and ignore masks would let a `distrust` be undone by making the keystore unreadable.
  Only a missing, foreign, behind, ahead or altered list is `degraded` (every publisher asks).
- **Q3. `fresh` is the master item absent, found without a secret read (D16).** Not `provisioned.json` or `publishers/`,
  which a same-user process deletes, and not a probe that reads the secret, which would put an unlock in front of a
  first pull. The residue is a process that deletes the master item (R1).
- **Q4. The surfaces refuse with a cause-specific sentence and read the list; the library passes its policy and reads
  nothing (D14).** A library caller is never asked, refused for a keystore cause or made to touch the keystore.
- **Q5. A persistent trust is written only by a path stronger than the agent-marker check (D15).** Today that is the
  process that shows the desktop dialog; a phone-signed approval is next. Where neither exists the offer is the one-time
  approval only.

## 9. First slices and tests

### Slice 1: the list, the commands and `distrust` (MOD-4)

Files:

- `src/poolhouse/hub/trust.py` (new): records, rules, `known`, `add`, `remove`, `reset`, `plan`, `recheck`, `reconcile`,
  `app_policy`; `src/poolhouse/hub/trust_file.py` (new): the sealed file, the item, the four states, the causes and
  their sentences. Each stays well under the file limit.
- `src/poolhouse/hub/trust_cli.py` (new, about 180 lines): `trust`, `distrust`, `publishers`; `hub/cli.py` composes it.
- `src/poolhouse/keystore.py`: `Keystore.master_present` (section 4.5), `Keystore.item` and `Keystore.set_item` (through `_flight` and `_call`),
  `KeystoreUnavailable.cause`, and `_window` and `_denial` ignoring an entry that is not a finite number or lies in the
  future and capping `until`.
- `src/poolhouse/hub/admission.py`: the policy argument; `known` replaces the `TRUSTED` test on the app policy; the
  degraded and unreadable sentences; `admit` returns its basis. `src/poolhouse/hub/transfer.py`: the call to `recheck`
  after each file and on the error path, holding the pull's target paths. `src/poolhouse/hub/remote.py`:
  `owner_models`, `owner_account`.
- `src/poolhouse/person.py`: `typed_exact`. `src/poolhouse/sentinel/human.py`: `HumanGrant.person`, `_FORBIDDEN`.
  `src/poolhouse/sentinel/store.py`: `hold_for_person`, the `person_only` check in `release`, `purge` and
  `settle_missing`. `src/poolhouse/sentinel/explain.py`: the `BY_REASON` entry and advice.
  `src/poolhouse/sentinel/adapters.py`: the `release` hook that restores the pin.
- `src/poolhouse/serve/guarded.py`: `reconcile` before `verify` on the app and agent surfaces.
- `scripts/hooks/claude-bash-guard`, the chat tool list.
- `docs/redteam/coverage-map.toml` rows for the two new grant mints, the Hub calls and the keystore call;
  `scripts/redteam_coverage.py --check`.
- Documents: section 10.

Tests, in `tests/test_publisher_trust_store.py` and `tests/test_publisher_trust_commands.py`, real fake Hub on loopback
(`src/poolhouse/testing/fakehub.py`, which gains the account fields), real sealed sentinel store and request inbox
under `tmp_path`, the file test keyring: the A1 to A4, A6 to A8 (terminal parts), A10 to A12, A14, A15, A17, A18 (the
degraded and unreadable parts), A21, A22, A23 and A24 tests named in section 7, and
`test_a_publisher_trust_added_by_a_person_is_fetched_without_asking`, `test_distrust_of_a_built_in_publisher_asks_again`,
`test_a_degraded_list_makes_every_publisher_ask_with_one_plain_sentence` (the degraded sentence, not "who Poolhouse
does not know"), `test_reset_is_a_persons_and_the_only_way_out_of_a_degraded_list`, `test_trust_refuses_while_the_list_is_degraded`,
`test_a_keystore_reset_leaves_the_item_and_the_list_reads_degraded`,
`test_a_device_that_never_wrote_a_list_makes_one_item_read_per_pull_and_writes_no_item`,
`test_trust_shows_the_account_type_and_member_count_escaped`,
`test_a_hub_name_with_control_or_bidi_characters_is_escaped_before_it_is_printed`.

Mutation checks, per AGENTS.md section 5: drop the floor check (A2 fails), raise the floor after the write instead of
before (A2's order test fails), decide `fresh` by `provisioned.json` or the files instead of the master item (A22 fails), read the secret in the lookup (the `master_present` test fails), drop the person check
in the writer (A4 fails), let `known` ignore the `distrust` record (A7 fails), skip the fold test (A6 fails), recheck
once at the end instead of after each file (A15's per-file test fails), hold by pins instead of target paths (A15's
nothing-pinned test fails), unpin before the hold (A7's order test fails), drop the `person_only` check in `purge` or
`settle_missing` (A7's tests fail), drop the `person_only` merge on an already-held record (A7's marker test fails),
drop the `BY_REASON` entry (the explanation test fails), read sentinel's mode in `known` (A17 fails), let the library
policy read the list (A24's and D14's tests fail).

### Slice 2: the tap (the non-technical path)

- `requests/model.py`: the `trust-publisher` and `stop-trusting` choices and their words, the `DIALOG_ONLY` set, the
  `publisher_trust` kind; `requests/store.py`: `answer` refuses a `DIALOG_ONLY` choice for any `via` but `dialog`;
  `requests/approvals.py ask` takes the choices.
- `hub/admission.py`: the offer rule; `hub/trust.py`: `settle`, `on_answered`, `apply_approval`, `apply_distrust`,
  `note_denial`; `hub/trust_cli.py`: `stop-trusting`; `serve/guarded.py`: the listener and the settle call in the scan
  loop; the page and terminal listings omit the dialog-only choice.
- Tests in `tests/test_publisher_trust_tap.py`: A5, A6 (tap), A8 (tap), A9, A16, A19, the A2 answer test, and
  `test_a_person_taps_always_trust_in_the_dialog_and_the_next_pull_from_that_publisher_does_not_ask`,
  `test_the_same_answer_given_in_the_page_trusts_nothing_beyond_that_commit`,
  `test_a_person_taps_stop_trusting_and_the_files_are_held_and_the_servers_stopped`. Driven the way a person does it
  before it is called working: the request in the desktop dialog and in the page, then the second pull.

Slice 1 is MOD-4. The feature is not done for a person bad with tech until slice 2 lands and has been driven.

## 10. Documents to change when it is built

`docs/sentinel.md` (the Supply chain paragraph: the list can be added to and masked, eight owners; the periodic dialog
and the review screen for a distrust hold; a `person_only` record is neither purged nor settled as missing),
`docs/model-discovery.md` ("Publisher trust"), `docs/requests.md` (the choices, the dialog-only choice and the
`publisher_trust` kind), `docs/keystore.md` (the second item and the new purpose; the Reset section saying
`keystore-reset` leaves the small publisher item; the Limits section saying `rate.json` and `denied.json` entries that
are not numbers or lie in the future are ignored and `until` is capped; a pull on an app or agent surface reads the
small item), `docs/person-commands.md` (Hard: `poolhouse-models trust`, `distrust`, `publishers --reset`),
`docs/commands.md` (the `poolhouse-models` row), `docs/release-readiness.md` (MOD-4 verified-by and state),
`docs/redteam/coverage.json`.

Not designed here: repository-level trust, a pool-wide list, expiry, ranking stored publishers in `find`, a
per-mirror list, pinning the Hub's account id, the library's own trust policy (branch
`fix/library-trust-and-net-policy`).
