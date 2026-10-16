# Publisher trust commands: `poolhouse-models trust` and `distrust`

Status: design, 2026-10-16. Nothing here is built. It serves MOD-4 in `docs/release-readiness.md` section 3.6 and
extends the publisher check of `docs/sentinel.md` ("Supply chain") and `docs/model-discovery.md` ("Publisher trust").

Tags: **[V path]** read in that code on `0.3dev` at `f803ba49f`; **[proposal]** this design.

## 1. Summary

- A person adds or removes a model publisher without a source edit. The built-in list (`publishers.ORDER`) stays in
  source as the floor; a stored list adds names to it and `distrust` masks names out of it. [proposal]
- A stored name is a name only. It gets exactly the checks a built-in one gets: whole-owner match, the official Hub
  host, a commit that is a branch or tag head or in `main`'s first history pages, marker names ask. No stored field
  can lower one. [proposal]
- Two ways in. The non-technical path is a third choice on the `model_publisher` request the person already
  answers: "always trust this publisher". The power-user path is `poolhouse-models trust OWNER` and `distrust OWNER`
  at a terminal. A normal journey gains no prompt. [proposal]
- `distrust` removes trust first, then unpins what that publisher supplied, holds it in quarantine (released only by a
  person at a terminal) and stops the servers using it. [proposal]
- The list is sealed under the keystore, carries a generation that never goes down, and fails closed: once a list has
  been written, one that cannot be read means every publisher asks. [proposal]
- Two things need the owner (section 8): the storage is not the graph, and a process that cannot read the keystore
  asks about every publisher once a list exists.

## 2. What exists today

- `hub/publishers.py` holds `ORDER` (seven owners: `Qwen`, `unsloth`, `ggml-org`, `google`, `bartowski`,
  `lmstudio-community`, `mlx-community`), `TRUSTED = frozenset(ORDER)`, `HOSTS = {huggingface.co, hf.co}`, `MARKERS`,
  and `valid_name`, `namespace`, `trusted`, `official`, `marker`. A name matches by whole owner, character for
  character; a name that is not an ASCII Hub name is not parsed. [V]
- `hub/admission.py` `admit(ref, files)` reads `publishers.TRUSTED` at call time. Reasons to ask: host not official;
  owner not in `TRUSTED`; (only for a known owner) commit not on a branch, tag or the first pages of `main`
  (`remote.on_branch`); a marker in the repository or file names; a digest that differs from an earlier pull. It
  raises a human-only `model_publisher` request with choices `approve` and `deny`, fields `publisher`, `repo`,
  `commit`, `host`, and approves only a request whose subject, words and fields are exactly the ones it builds
  (`approvals.approved`). At most ten wait (`MOST_WAITING`). [V]
- Answering a `model_publisher` request passes `require_person` whatever the authority registry delegates; a
  resolved request is dropped after seven days and 400 rows are kept (`requests/store.py`, `RETAIN_S`, `MAX_ROWS`). [V]
- A pull pins each file with `publisher`, `commit` and `source_ref` in the sealed manifest (`sentinel/integrity.py`,
  `hub/transfer.py _notes`). `Manifest.unpin(path)` and `Store.quarantine((kind, key), reason, evidence, Holding(path=...),
  actor)` exist; a quarantined model file is moved aside and `guarded.register` stops the servers using it. `release`
  is `mint_gated("sentinel.quarantine", ...)`, which a lead agent passes while that gate is delegated (the dev
  preset). [V]
- The shipped list "has no add path" (`docs/sentinel.md`); `docs/sentinel.md` and `docs/model-discovery.md` name six
  owners, the code seven. [V]
- The desktop dialog shows at most three buttons, refusing choices first; its first label is the default and the
  answer to Escape (`requests/dialog.py buttons`, `desktop.mac_script`). [V]
- `hub` is in the model layer, `graph` above it; a new `hub` to `graph` import would add an edge to `KNOWN` in
  `tests/test_layers.py`, which may only shrink. `docs/requests.md` keeps request relations as row fields for the same
  reason. [V]
- The keystore is open only to the installed software on the real home (`real_machine.py`); the requests store and
  the memory store are sealed under keystore subkeys (`salted_subkey`). The sentinel's `SealedFile` keeps its HMAC
  key beside the file, so a process of the same user can write a valid one (HANDOFF, "A server started by a
  process of its own"). [V]

## 3. Decisions

| # | Decision | Why |
| --- | --- | --- |
| D1 | Trusted set = (built-in minus distrusted) plus added. `ORDER` and `TRUSTED` stay source constants; `fakehub` keeps patching `TRUSTED`. | The floor is code a reviewer reads; a stored list cannot delete it, only a person can mask it. |
| D2 | A stored entry has no host, flag, pattern, expiry or per-check switch. | Nothing in the file can lower a check, so nothing in a forged or old file can. |
| D3 | One record per owner, the last one wins. A `distrust` newer than a trust answer beats it. | A removal must hold against an old approval still in the inbox. |
| D4 | Storage is one sealed file in the model layer, not the graph (section 4.4). | Layer rule, no relations of its own, one source of truth for what a publisher supplied (the pins). |
| D5 | Fail closed: after a list has been written, an unreadable, foreign, older or missing one makes every publisher ask. | A forgery or rollback must not return a distrusted publisher. |
| D6 | The writer takes a person's grant (`HumanGrant`); the commands mint it. The tap writes only from a sealed approved request, read by id. | A caller that imports the module without a person gets a refusal, not a write. |
| D7 | `trust` needs the owner typed back exactly at the terminal device; `distrust` needs a typed `yes`. | Trust is sensitive and permanent; `distrust` stops running servers. |
| D8 | `distrust` commits the removal before it touches a file, and is safe to run again. | A crash leaves new fetches asking, never trusting. |
| D9 | `trust` works only on the official Hub and only for an owner the Hub holds models for now. | No trust through a mirror; no trust of a name nobody holds yet. |
| D10 | The list is per device. No board entry, pool message, file or environment variable writes it. | A pool member cannot reach another device's trust. |

## 4. The trust list

### 4.1 The effective set [proposal]

```
status(owner) = last record of owner, if any
trusted(owner) = status is "trust"                       (an added owner)
              or (owner in publishers.TRUSTED and status is not "distrust")
```

`hub/trust.py` exposes `known(owner)`; `admission.admit` calls it instead of `publisher not in publishers.TRUSTED`.
`publishers.trusted(repo)` stays the built-in predicate and `PREFER` stays the built-in order: ranking in
`poolhouse-models find` never approves anything.

### 4.2 What a stored name never changes [proposal]

`admit` runs the same lines for every trusted owner, whichever list it came from: host must satisfy
`publishers.official` (`HOSTS` stays a source constant), the commit must satisfy `remote.on_branch`, a marker in a
repository or file name asks, a changed digest asks. The record has no field these read. A test over the record's
schema and a test that runs one scenario against a built-in and a stored owner hold this (section 9).

### 4.3 Records and file [proposal]

```
{"schema_version": 1, "generation": 7,
 "records": [{"owner": "maker", "do": "trust",    "at": 1760600000.0, "via": "terminal"},
             {"owner": "Qwen",  "do": "distrust", "at": 1760600100.0, "via": "terminal"},
             {"owner": "acme",  "do": "trust",    "at": 1760600200.0, "via": "tap", "request": "rq_..."}]}
```

- One record per owner, newest replaces older. A `distrust` record stays as long as the owner is listed; it is what
  masks a built-in owner and what defeats an old tap answer.
- At most 128 records. A 129th `trust` is refused with one sentence; nothing is dropped to make room.
- On read every owner must pass `publishers.valid_name`; a record that does not is dropped and an event is written.

### 4.4 Where it is kept, and why not the graph [proposal]

`~/.poolhouse/publishers/trust.enc` under the state root (`home.state`), AES-256-GCM under
`keystore.default().salted_subkey("publisher-trust", directory)`, additional data `poolhouse/publisher-trust/v1`,
replaced atomically (`files.writing`) under `lock.only_one(directory / "trust.lock")`. Until a list has been written a
read is one `stat` and the sentinel counter, and the keystore is not touched, so the normal journey costs nothing.

AGENTS.md section 6 defaults persistent relational state to the graph. This list departs from it, with these
reasons (owner question Q1):

1. The reader is `hub.admission`, in the model layer. `graph` is above it; importing `GraphStore` there adds an edge
   to `KNOWN` in `tests/test_layers.py`, which may only shrink. Reading through a provider that a higher layer
   registers would leave a process that did not register it silently on the built-in list.
2. The list has no relations of its own. The one relation that matters, publisher to the files it supplied, is
   `Pin.publisher` in the sentinel manifest; a graph copy would be a second source of truth for `distrust` to disagree with.
3. It is a short set of names read on every fetch, which is the "tiny single-purpose" case the rule allows.

Ranked options for Q1: (a) this sealed file; (b) graph nodes, which first needs the graph engine moved below `hub` in the
layer order (a repository-wide change) and still leaves two copies of the publisher-to-files relation; (c) graph nodes
with `hub` importing `graph`, which `tests/test_layers.py` refuses.

### 4.5 Reading: three states [proposal]

The sentinel's sealed state keeps a counter, the floor, raised to the file's generation on every write. A read goes:

| State | When | Effective set |
| --- | --- | --- |
| `fresh` | floor is 0 and there is no file | built-in list |
| `ok` | the file opens, authenticates, and `generation >= floor` | built-in with the records applied |
| `degraded` | floor above 0 and the file is missing, cannot be opened (keystore locked or another keystore), fails its tag, or has `generation < floor` | empty: every publisher asks |

A degraded read says one sentence on the request and in `poolhouse-models publishers` ("Poolhouse cannot read its
list of trusted publishers on this device, so it asks about every model."). While degraded `distrust` writes no record
(every publisher already asks) and still unpins and holds the files; `trust` and the tap refuse. A person starts again
with `poolhouse-models publishers --reset`: a typed `yes` that says earlier additions and distrusts are lost and lists
the built-in owners to distrust again, then a new list at `floor + 1` holding no records. A repair never happens as a side effect of `trust`, because it would bring a
distrusted built-in owner back without the person seeing it. A valid file one generation above the floor (a crash
between the write and the raise) is accepted and the floor is raised. When sentinel is off the floor is not consulted.

### 4.6 Writing [proposal]

`trust.add(owner, grant, via, request="")` and `trust.remove(owner, grant)` check `grant.check(action, owner)`, take
the lock, read, apply, write generation `n+1`, raise the floor, release. `trust.apply_approval(request_id)` (section 6.3)
is the only other writer. A static test lists the callers of all three.

## 5. The commands

All three are subcommands of `poolhouse-models`, added through the existing `extend` and `more` arguments of
`hub.cli.main` from a new `hub/trust_cli.py`. `docs/commands.md` and `docs/person-commands.md` get rows.

### 5.1 `poolhouse-models trust OWNER` [proposal]

1. `person.require_person(...)`: refused when an agent marker is set or stdin and stdout are not terminals.
2. `OWNER` must be a bare Hub name (`publishers.valid_name`; no `/`, no `*`, no case or separator games). Refused if
   its fold (`publishers._fold`, which maps lookalikes and leet and drops separators) equals the fold of a built-in
   or stored owner it is not equal to: "OWNER looks like unsloth".
3. Refused unless `publishers.official(remote.endpoint_host())`: "Trust is set on the official Hub only; HF_ENDPOINT
   points at HOST."
4. Ask the official Hub for OWNER's models (a new `remote.owner_models(owner)` beside `remote.models`, through
   `poolhouse.net`). Refused when it holds none or cannot answer. Show the count and up to three repository names.
5. Print: "Trust every model from OWNER on huggingface.co from now on? It will still check the commit and the file
   names, and ask about anything altered. Type OWNER to confirm." Read the line from the controlling terminal and
   compare exactly. `person.typed` lower-cases; a `person.typed_exact` that keeps case is added for this.
6. `trust.add`. Print "Trusted OWNER. `poolhouse-models distrust OWNER` takes it back." If files distrust once held
   for OWNER are still held, say so and name `poolhouse-security review`; trusting again never releases them.

An owner already trusted prints that and exits 0. A built-in owner that was distrusted is trusted again the same way.

### 5.2 `poolhouse-models distrust OWNER` [proposal]

1. `require_person`; the name is validated.
2. Plan: the pins with `publisher == OWNER`, their bytes, and the servers running them. Nothing to remove (not trusted,
   no pins) prints that and exits 0.
3. Print what will happen: "Poolhouse will ask about every model from OWNER again. N files (SIZE) pulled from OWNER are
   unpinned and held, and M running servers are stopped. Type yes to continue." Read from the controlling terminal
   (`person.confirm`).
4. In this order: (a) `trust.remove` writes the distrust record; (b) for each pin, `manifest.unpin(path)` then
   `store.quarantine(("model", path), "publisher distrusted: OWNER", {"publisher": OWNER, "person_only": True},
   Holding(path=path), actor="human")`, whose hook stops the servers; (c) an activity record and a sentinel event.
   Re-running finishes whatever is left: (a) is skipped when the record exists, (b) continues over the remaining pins.
5. `Store.release` refuses a record with `person_only` unless the grant was minted by a person: `HumanGrant` gains a
   `person` field, true from `mint`, `mint_pressed` and `mint_clicked`, false from the delegated branch of
   `mint_gated`. A lead agent passing `sentinel.quarantine` cannot give these files back.

`distrust` has no flag that keeps the files; it removes trust and what the trust vouched for.

### 5.3 `poolhouse-models publishers [--json]` [proposal]

Without `--reset`, read-only and open to any process. One line per owner: name, `built in` or `added`, `distrusted`,
the date, the way (`terminal` or `tap`), and the state of section 4.5. It is how a person finds what to distrust.
`--reset` is a person's (section 4.5): `require_person` and a typed `yes` from the controlling terminal.

### 5.4 Who may run them [proposal]

- Not registry gates: a registry gate can be `delegated` to a lead agent, and these must not be. The gate is in the
  writer (a person's grant), as `Store.release` is. `docs/person-commands.md` lists them under Hard.
- `scripts/hooks/claude-bash-guard` refuses a command line naming `poolhouse-models trust` or `distrust`;
  `sentinel/human.py _FORBIDDEN` and `agent_may` refuse a tool call naming them or `publishers/trust`; `chatpolicy`'s
  person-only list gains the two command names. These are guard changes and need the exact-SHA independent approval.

## 6. The tap: "always trust this publisher"

### 6.1 When it is offered [proposal]

`admission.admit` adds the choice `trust-publisher` to the request when all hold; otherwise the request is as it is
today (`approve`, `deny`):

- the host is official and the only reason is that the publisher is unknown (no marker, no changed digest);
- `remote.on_branch(repo, commit)` is true, so the model asked about is from the publisher's own branch;
- the owner passes `valid_name` and the fold test of section 5.1 step 2 (a lookalike of a known publisher never gets
  the offer; the one-time `approve` is still there);
- no request of that owner was denied in the last 24 hours, and requests for fewer than three distinct repositories of
  that owner were raised in that time (a stream of requests is one decision, never a trust offer; an agent retrying one
  pull counts once).

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
On the next fetch `admit` calls `trust.apply_approval(request_id)` for approved `model_publisher` requests of this
publisher and host, found through `requests.list_requests`. The writer reads the request from the sealed inbox by id and
requires: `state == approved`, `answered_by in VIAS`, `answer == "trust-publisher"`, that choice among the request's
choices, `reason` equal to the sentence rebuilt from the request's own fields, a field `host` that is the official
host now, a valid unfolded owner, and `answered_at` after the owner's last `distrust` record. Then it writes a `tap`
record. The same call makes the retried pull pass. If the person taps and nothing fetches from that publisher, nothing is
written, and the tap approved only that commit.

## 7. Attack-first pass

This is the author's pass and does not count: AGENTS.md section 1 requires **a different agent, told to break this
design, to answer in writing before it is built.** Each entry names the defence and a test that fails without it (test
files in section 9).

**A1. A copied or cloned state directory.** Defence: the file opens only under the keystore master of this OS user on
this device; elsewhere it fails its tag and the read is `degraded` (everything asks). The floor travels with the sentinel
state, so a missing or foreign file is degraded, not fresh. Tests: `test_a_list_copied_under_another_keystore_is_degraded_and_trusts_nothing`,
`test_a_missing_file_after_a_write_is_degraded_not_fresh`. Status: fixed. The same device, same user, restored image is A2.

**A2. Replay of an old trust record.** Defence: the file carries `generation`; the sentinel counter is the floor; an
older file is degraded. A tap answer older than the owner's last `distrust` is ignored. Tests: `test_an_older_list_put_back_after_a_distrust_is_degraded`,
`test_a_trust_answer_older_than_the_distrust_is_not_applied`. Status: fixed against a file copy. A process that writes
both the sentinel state and the keystore-sealed file can still roll both back: accepted risk R1.

**A3. A forged record.** Defence: AEAD tag under a key only the installed software reaches (`real_machine`); a
file that fails it is `degraded`; a record whose owner is not a Hub name is dropped. Tests: `test_a_list_written_without_the_key_is_degraded`,
`test_a_stored_owner_that_is_not_a_hub_name_is_dropped`. Status: fixed.

**A4. A malicious agent writes the list.** Defence: (a) the file is sealed, see A3; (b) `trust.add` and `trust.remove`
refuse a call with no person's grant; (c) the commands refuse a process with an agent marker and one without
terminals; (d) the bash guard and `agent_may` refuse the command line, the module name and the state directory. Tests:
`test_the_writer_refuses_a_call_without_a_persons_grant`, `test_trust_and_distrust_refuse_a_process_an_agent_started`,
`test_an_agent_tool_call_naming_the_trust_commands_or_their_directory_is_refused`, `test_a_delegated_grant_is_not_a_persons_for_a_held_publisher_file`.
Status: fixed. An agent that clears the marker and owns a pseudo-terminal passes every terminal gate that has no typed
confirmation (`docs/person-commands.md`); `trust` and `distrust` read the confirmation from the controlling terminal
device, which that agent has only if it made it its own: accepted risk R1.

**A5. An injected instruction (board message, file, page, tool result) to trust a publisher.** Defence: it can only
reach the same doors as A4. The tap persists only from a sealed request answered by a person, with the exact rebuilt
words, so a request an agent raised in other words, one answered `approve`, one denied, and one never answered all
write nothing. `requests.answer` for a human-only kind refuses an agent whatever the registry delegates. Tests:
`test_a_request_raised_in_other_words_with_the_trust_choice_trusts_nothing`, `test_an_approve_answer_does_not_trust`,
`test_a_denied_or_expired_trust_request_trusts_nothing`, `test_an_agent_cannot_answer_the_trust_choice`,
`test_the_only_callers_of_the_trust_writers_are_the_commands_and_the_fetch_check`. Status: fixed.

**A6. A lookalike or homoglyph owner.** Defence: names are ASCII Hub names compared whole; `trust` refuses a name whose
fold equals another owner's; the tap is not offered for one; at fetch `Unsloth` is not `unsloth`. Tests:
`test_trust_refuses_an_owner_that_folds_to_another`, `test_trust_refuses_a_name_that_is_not_a_hub_name`,
`test_the_tap_is_not_offered_for_a_lookalike`, `test_a_stored_owner_matches_character_for_character`. Status: fixed. An
owner that is merely close (one letter from a known name) is shown by `publishers`; there is no distance rule, because a
legitimate organisation can sit close to another: accepted risk R4.

**A7. A publisher compromised after it was trusted.** Defence: `distrust` commits the removal first, then unpins by
`Pin.publisher`, holds the files with a record only a person releases, and stops the servers; every pull in the window
was pinned with `publisher`, `commit` and `source_ref`, so the set is exact. Trusting again releases nothing. Tests:
`test_distrust_unpins_holds_and_stops_what_the_publisher_supplied`, `test_distrust_commits_the_removal_before_it_touches_a_file`,
`test_a_file_distrust_held_is_released_only_by_a_person`, `test_trusting_again_does_not_release_what_distrust_held`.
Status: fixed for pinned files. Accepted risks R5, R6: a file the publisher supplied that was never pinned, a blob two
publishers both supplied (one pin per path), and a first pull of a replaced file before anyone knew (HANDOFF, "A
compromised known namespace").

**A8. A downgrade, official Hub to another endpoint, or a check lowered by where the name came from.** Defence: the
record has no host; `publishers.official` runs for every owner; `trust` and the tap refuse a mirror; the tap's approval
is bound to the `host` field; the branch and marker checks run for stored owners. Tests:
`test_a_stored_publisher_on_a_mirror_asks_with_the_host`, `test_trust_refuses_while_the_endpoint_is_not_the_official_hub`,
`test_the_tap_is_not_offered_on_a_mirror`, `test_a_trust_answer_for_one_host_does_not_apply_on_another`,
`test_a_stored_and_a_built_in_publisher_are_checked_alike`, `test_the_trust_record_has_no_field_a_check_reads`. Status: fixed.

**A9. Approval fatigue.** Defence: at most ten `model_publisher` requests wait; the trust choice is withheld for 24
hours after a denial of the owner and once requests for three distinct repositories of it were raised, so a stream is
one decision; it is a separate button, never the default (the dialog's default is Deny), and human-only kinds are
excluded from "answer all". Answering a stream with `approve` approves single commits and trusts nothing. Tests:
`test_the_trust_choice_is_withheld_after_a_denial_or_three_repositories`, `test_a_retried_pull_counts_once_toward_the_stream_rule`,
`test_the_dialog_default_is_deny_and_trust_is_never_first`, `test_answer_all_never_covers_a_trust_choice`. Status: fixed.

**A10. Takeover by whoever is merely present.** Defence: at a terminal `trust` needs the name typed back exactly from
the controlling terminal; a bare Enter or `y` does nothing; `distrust` needs a typed `yes`; every change is an activity
record, a sentinel event and a line in `publishers`, and one command undoes it. The tap is an ordinary request answer:
whoever is signed in at the unlocked computer is the person for every request today. Tests: `test_trust_needs_the_owner_name_typed_back_exactly`,
`test_trust_without_a_terminal_is_refused`, `test_every_change_is_recorded_and_listed`. Status: terminal fixed; the
tap is accepted risk R2 until phone approval (`docs/phone-approval.md`) can carry this choice.

**A11. Concurrent edits.** Defence: one lock around read, apply, write and floor raise; atomic replace; a distrust
racing a trust leaves the later one; the tap writer takes the same lock. Tests: `test_parallel_trusts_of_different_owners_all_persist`,
`test_a_trust_and_a_distrust_of_one_owner_leave_the_later_one`, `test_a_reader_never_sees_a_half_written_list`. Status: fixed.

**A12. A compromised or malicious pool member.** Defence: the list is per device and no board entry, pool message or
file reaches a writer (A5's caller test); a copy served by a member is still checked against the Hub's digest after the
publisher check. Test: the caller test above and `test_a_member_served_copy_of_a_distrusted_publisher_asks_first`.
Status: fixed. Pool-wide trust is not designed here.

**A13. A stolen phone or key; a forged or below-threshold signature.** Not applicable: no phone key or signature takes
part in the terminal path or the request tap. When phone approval lands, `trust-publisher` is one of the choices it
should carry, named in `docs/phone-approval.md` then.

**A14. A name that does not exist yet.** Defence: `trust` needs the official Hub to hold models for the owner now.
Test: `test_trust_refuses_an_owner_the_hub_holds_nothing_for`. Status: fixed for today. A trusted name the Hub frees and
someone else registers is accepted risk R3.

## 8. Findings and decisions for the owner

Fixed in the design: A1 to A12 and A14 above; the dialog default; the old tap answer outliving a `distrust`; a
delegated lead releasing files `distrust` held; the case-folding `person.typed` defeating an exact typed name.

Accepted risks, named for the owner:

| # | Risk | What bounds it |
| --- | --- | --- |
| R1 | A process of the same user that writes the sentinel state and the keystore-sealed file, or presents a pseudo-terminal with the marker cleared, can replay or write. | The marker plus a terminal is the only boundary of every person gate (`docs/person-commands.md`); the keystore is closed to anything but the installed software. |
| R2 | An unlocked, signed-in session can tap "always trust". | It is audited, listed and one command undoes it; phone approval is the fix. |
| R3 | A trusted name the Hub frees and another account registers. | The built-in list has the same exposure. To look into: pinning the Hub's account id if its API exposes one (not verified). |
| R4 | A near-miss owner name trusted on purpose. | The typed name, the Hub's model count, and `publishers`. |
| R5 | A blob two publishers supplied belongs to the one that pinned it last. | `distrust` of the other leaves it; rare, and the sentinel pin check still applies. |
| R6 | Files never pinned, and a replaced file pulled before anyone knew. | Existing "Not armed" items in `docs/sentinel.md`; an independent digest source is HANDOFF work. |

Owner questions, each asked through the question tool when this is built:

- **Q1.** Store the list as a sealed file in `hub` (section 4.4), or move it to the graph with a core-layer reader?
  Recommended: the sealed file. The other option costs a new core module that owns a graph and a layer move, and gives a
  second copy of the publisher-to-files relation.
- **Q2.** Once a list exists, a process that cannot read the keystore (a background one) asks about every publisher, and
  its request store is the same unreadable one, so the pull is refused with one plain sentence. Accept this, or let that
  process read the built-in list and ignore masks? Recommended: accept; the second option lets a `distrust` be undone by
  making the keystore unreadable. A device that never uses `trust` or `distrust` sees no change.

## 9. First slices and tests

### Slice 1: the list, the commands and `distrust` (MOD-4)

Files:

- `src/poolhouse/hub/trust.py` (new, about 190 lines): records, file, reading states, `known`, `add`, `remove`, `plan`.
- `src/poolhouse/hub/trust_cli.py` (new, about 150 lines): `trust`, `distrust`, `publishers`; `hub/cli.py` composes it.
- `src/poolhouse/hub/admission.py`: `known` replaces the `TRUSTED` test.
- `src/poolhouse/person.py`: `typed_exact`. `src/poolhouse/sentinel/human.py`: `HumanGrant.person`, `_FORBIDDEN`.
  `src/poolhouse/sentinel/store.py`: the floor counter, the `person_only` check in `release`.
- `scripts/hooks/claude-bash-guard`, the chat tool list.
- `docs/redteam/coverage-map.toml` rows for the two new grant mints, the Hub call and the keystore call;
  `scripts/redteam_coverage.py --check`.
- Documents: section 10.

Tests, in `tests/test_publisher_trust_store.py` and `tests/test_publisher_trust_commands.py`, real fake Hub on loopback
(`src/poolhouse/testing/fakehub.py`), real sealed sentinel store and request inbox under `tmp_path`, the file test keyring:
the A1 to A4, A6 to A8 (terminal parts), A10 to A12 and A14 tests named above, and
`test_a_publisher_trust_added_by_a_person_is_fetched_without_asking`, `test_distrust_of_a_built_in_publisher_asks_again`,
`test_an_unreadable_list_makes_every_publisher_ask_with_one_plain_sentence`,
`test_reset_is_a_persons_and_the_only_way_out_of_a_degraded_list`, `test_trust_refuses_while_the_list_is_degraded`,
`test_no_file_is_read_and_no_keystore_is_touched_until_a_list_exists`.
Mutation checks, per AGENTS.md section 5: drop the floor check (A2 fails), drop the person check in the writer (A4
fails), let `known` ignore the `distrust` record (A7 fails), skip the fold test (A6 fails).

### Slice 2: the tap (the non-technical path)

- `requests/model.py`: the `trust-publisher` choice and its words; `requests/approvals.py ask` takes the choices.
- `hub/admission.py`: the offer rule and the call to `trust.apply_approval`; `hub/trust.py`: `apply_approval`.
- Tests in `tests/test_publisher_trust_tap.py`: A5, A6 (tap), A8 (tap), A9, the A2 tap test, and
  `test_a_person_taps_always_trust_and_the_next_pull_from_that_publisher_does_not_ask`. Driven the way a person does
  it before it is called working: the request in the page and in the desktop dialog, then the second pull.

Slice 1 is MOD-4. The feature is not done for a person bad with tech until slice 2 lands and has been driven.

## 10. Documents to change when it is built

`docs/sentinel.md` (the Supply chain paragraph: the list can be added to and masked, seven owners), `docs/model-discovery.md`
("Publisher trust"), `docs/requests.md` (the choice), `docs/person-commands.md` (Hard: `poolhouse-models trust`, `distrust`),
`docs/commands.md` (the `poolhouse-models` row), `docs/release-readiness.md` (MOD-4 verified-by and state), `docs/redteam/coverage.json`.

Not designed here: repository-level trust, a pool-wide list, expiry, ranking stored publishers in `find`, a
per-mirror list, pinning the Hub's account id.
