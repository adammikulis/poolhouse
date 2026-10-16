# Extension registry: signed, data-only packages that a pool publishes

Design, 2026-10-16. Nothing here is built. It specifies how a community, which is a pool, publishes packages of data (benchmarks,
closed-question decision tasks, extraction schemas, model recipes) that people in other pools install, with the verifier, the
approval, the state and the attack-first answers. It is the extension policy that `docs/assistant-security.md`, "Order of work", puts
before any extension mechanism exists. It reuses what is built or designed: the signed file list (`src/poolhouse/fleet/onboard/manifest.py`),
the pool's charter and acts (`docs/node.md`, "Who governs a pool"; `docs/pools-and-serving.md`, section 1), the node's approval and
fatigue defences (`docs/phone-approval.md`), the invariants for an agent steered by text (`docs/compromised-agents.md`, I1 to I9) and
the guarded download (`src/poolhouse/net/download.py`).

Code plugins are out of scope (section 1.3). The `benchmark` kind is specified by its constraints only: the plugin interface of
`feat/bench-extractbench` is one commit that is not on `0.3dev`, nothing here has been checked against it, and the verifier refuses the
kind ("This needs a newer Poolhouse") until the interface has landed and a handler is written against it (section 10, slice 5).

## 0. The decisions, one line each

| # | Decision |
| --- | --- |
| D1 | An extension is data for a handler that ships in the wheel. A package carries no code, no template with logic, no expression, no reference to another file or host, and nothing a loader evaluates. |
| D2 | A registry is a pool. Its id is the pool id; its publishers are the pool's strong admin keys under the pool's `sensitive` threshold; a person who publishes alone publishes from a pool of one. |
| D3 | Two signed layers: a **manifest** per package version and a **contents** list per registry (serial, expiry, yanks, the chain head). Both are threshold-signed canonical JSON under their own domain string. A manifest names no charter. |
| D4 | The device's node verifies, writes every word of the approval card and records the install. A fetcher, a mirror or an agent supplies bytes and nothing else. |
| D5 | An install is a person's signature on the exact manifest hash. `extension_pin` and `extension_install` are floors: no standing grant, agent, rule or text covers them. |
| D6 | An agent may suggest a package. A suggestion is a quiet card the person opens on their own screen. It is never an approval, a push or an install. |
| D7 | Adding a community is one approval that pins the registry and installs the first package. The community is shown by its id in groups of four, to compare with the person who shared it. |
| D8 | An update never switches itself. The installed version keeps working until the person approves the diff. Updates that only add items or change wording are listed and approved as one weekly batch whose card shows all of them; a package that changes or removes anything it had, or asks for more, is asked alone. |
| D9 | A lower serial than one already seen is refused from the network. Going back is the person's own signed act naming an older, unyanked manifest hash from the version list. |
| D10 | Phase 1 has no dependencies between packages. Every reference names `(registry id, name, manifest hash)`, never a bare name. |
| D11 | Authority is in signed entries on the `#extensions` channel. The node's `seen` files are anchors and counters. The graph store holds a catalogue and run provenance that are derived and rebuildable. |
| D12 | Limits are part of the format and are checked before parsing: sizes, counts, digits, depth. There are no archives, no compression, no YAML, no regular expressions, no schema references. |
| D13 | A community recipe ranks below the project's shipped measurements, the machine's own measurements and the person's settings. Nothing in a package outranks the broker's admission. |
| D14 | A local, unsigned file is an ordinary file a caller hands to code. Name resolution never falls through to it. |
| D15 | A signer removed from the pool loses the versions only it signed until a current admin endorses them, and the installed pointer falls back to the last version with a valid signer. |
| D16 | Signers and threshold come from the latest chain state the device verified. Contents sign that state as their `head`; a device offers nothing from a registry whose head it has not verified, or whose chain has an admin change in flight or a dispute open. |

## 1. What an extension is

### 1.1 Four kinds

A **kind handler** is code in the wheel: it owns a JSON Schema in `contracts/extensions/`, a bounds table, a differ that writes the
plain-words diff, and the typed object it hands to the library. A package supplies data for one handler. A kind this build has no
handler for is listed ("This needs a newer Poolhouse") and cannot be installed.

| Kind | The package carries | The library reads it through | Declared capabilities it may use |
| --- | --- | --- | --- |
| `decision-task` | Named closed questions (`noul`, `choice`, `score`) with their option texts, and optionally labelled cases | `poolhouse.decide.questions` (`noul`, `choice`, `score`), `poolhouse.decide.cases` | `decide.questions` |
| `extraction-schema` | A schema in the restricted subset of 2.3, the instructions string and the prompting fields of `Prompting` that 2.3 allows, optional example messages | `poolhouse.extraction.Prompting`, `contracts/extraction.schema.json` | `extract.schema` |
| `model-recipe` | Model files as pins (`decide.fetch.Pin`: repository, revision, filename, SHA-256, size) and profile records for named workloads, with the typed fields of 2.5 and the evidence the publisher claims | `poolhouse.serve.profile` as its lowest layer, below `poolhouse/data/profiles.json` and the machine's own `~/.poolhouse/profiles.json` | `serve.recipe`, `model.pin` |
| `benchmark` (refused until its interface has landed) | A loader choice from a closed list, its field map, the cases as a part, and a scorer chosen from the closed list with typed parameters | the benchmark interface in `src/poolhouse/bench` (not on `0.3dev`; see the head of this document) | `bench.run` |

A **capability** is a statement of what the handler may do with the data. The handler enforces it through typed fields, so the
declaration is checked against the kind, not trusted. A package declares exactly the capabilities its kind needs: an extra one, a
missing one or an unknown name is refused. The vocabulary is closed and small so that a widening is visible.

| Capability | What the person is told |
| --- | --- |
| `decide.questions` | "Adds questions that your decision models answer." |
| `extract.schema` | "Adds a shape that your models fill in when they read documents." |
| `serve.recipe` | "Suggests how to serve the models it names (memory and speed settings). Your own measurements and the ones that ship with Poolhouse win." |
| `model.pin` | "Names model files by exact checksum. Before one is fetched the usual model checks run and ask you when the publisher is not known." |
| `bench.run` | "Adds a test you can run against your models. It runs only when you start it." |

None of them reads files, memory or the network, writes outside the installed store, or runs a program. Installing a package runs
nothing: a benchmark runs when a person or an agent with the grant starts a run, and that run takes the lease like any other.

**Names are addresses.** An installed item is addressed `<registry id>/<name>`. Every resolver that reads the installed set (decision
tasks, extraction schemas, recipes, benchmarks) takes that full address and nothing shorter, so an installed name never takes the place
of a shipped item or of a bare name a caller wrote. A caller on a security path reads no installed item: `poolhouse.decide.guards`
takes its questions from `QUESTIONS` in `decide/guards/states.py`, and a test fails if a module under `decide/guards` can reach
`poolhouse.extensions` (T5).

### 1.2 What a package may not be

A package may not carry or imply: an archive, a compressed part, a symlink, a path, YAML, a pickle, HTML or Markdown that is rendered,
a template language, a regular expression, a JSON Schema `$ref`, `pattern` or `format`, a scorer written as an expression, a judge
model for a scorer, a free-form command line or environment, a URL the loader fetches, a model reference that is a path or an `hf:`
string, a raw `prompt`, a message with any role but `user` and `assistant`, or a reference to another package. Each is a refusal in the
verifier with a one-sentence reason (section 9, T13).

### 1.3 Code plugins: out of scope, and what would unlock them

A package that runs code (a connector, a tool, a scorer in a language, a loader) is not designed here. It needs, built and tested
first: per-connector sandbox policies and egress lists (`docs/assistant-security.md`, "Each integration runs in its own sandboxed
process", marked *Not built: per-connector policies*), the connector credential broker ("No ambient authority", *Not built*), the grant
ledger that lists what each integration can do ("Off until granted", *Not built*), and a permission class `code.run` that is a floor
needing a strong key and an independent review of the exact code hash. It then gets its own attack-first pass. Nothing in the format
below reserves a field for code; a version of the format would add one.

## 2. The package

### 2.1 Layout and manifest

A package is a **manifest** and its **parts**. A part is one file, stored raw and addressed by its SHA-256. There are no archives:
a part's stored size is its size on disk.

```
{ "manifest": {
    "v": 1,
    "registry": "<pool id, 32 hex digits>",
    "name": "support-tickets",
    "kind": "decision-task",
    "serial": 7,
    "prev": "<sha256 of the manifest of serial 6, or empty>",
    "capabilities": ["decide.questions"],
    "pins": [ { "id": "weights", "repo": "Qwen/Qwen3.5-2B-Base", "revision": "<40 hex>", "filename": "model.safetensors",
                "sha256": "<64 hex>", "size": 4213224 } ],
    "parts": [ { "name": "questions.json", "media": "application/json", "size": 18211, "sha256": "<64 hex>" } ],
    "title": "Support ticket triage",
    "says": "Twelve questions for sorting incoming tickets.",
    "changes": "Adds two questions about refunds." },
  "signatures": [ { "key": "<fingerprint>", "alg": "ed25519", "sig": "<base64>" } ] }
```

- **Signed bytes.** `poolhouse/extension/v1`, a line break, then the canonical JSON of the manifest: sorted keys, no spaces, plain ASCII,
  integers only. A signed body has no floats; a fractional setting is an integer in a stated unit (`temperature_milli`). Parts are not
  canonicalised: their bytes are what the manifest's SHA-256 covers, and they may hold decimals.
- **The manifest hash** (the package version's id) is the SHA-256 of the signed bytes. Co-signers sign the same bytes, and the
  signature sets of two copies merge, as for an act (`docs/node.md`, "Who governs a pool", *Acts*).
- **Names.** `name` is `[a-z0-9][a-z0-9-]{0,62}`, ASCII only. `title`, `says` and `changes` are untrusted text: printable ASCII,
  at most 80, 280 and 560 characters.
- **`serial`** rises by one per version of a name within a registry. `prev` chains versions, so a skipped or forked history is
  visible in the diff.
- **No charter, no epoch.** A manifest names no charter. Who may sign it, and how many, is read from the latest state of the chain the
  device verified (3.4), so a signature cannot choose a lower threshold or an older admin set by naming an earlier epoch. A manifest
  that carries `epoch`, `charter` or any other field nobody defined is refused (4, check 2).
- **No date.** A manifest carries no time. A time a publisher writes is a claim: nothing reads it for an age or a delay, and the card
  shows none as fact (3.2, 5.2).
- **Pins** have the fields of `poolhouse.decide.fetch.Pin` (repository, full revision, filename, SHA-256, size) and a local `id`
  (`[a-z0-9][a-z0-9-]{0,31}`) that a recipe uses to name the pin (2.5). They are the shape the released deciders are already pinned in
  (`poolhouse/decide/pins.py`). A pin names a file; it never carries one. `Pin` itself checks only that the revision and the hash have
  the right length, so the verifier checks the rest (4, check 8).
- **Algorithms** are Ed25519 and a phone's P-256 key, picked from the key as in `gov/keys.rs`, never from a field of the manifest.
  Any other is refused.

### 2.2 Contents

The **contents** of a registry list its current packages. It is the freshness, rollback and yank layer.

```
{ "contents": { "v": 1, "registry": "<pool id>", "serial": 41,
                "head": { "epoch": 3, "charter": "<hash>" },
                "issued_ms": 1790000000000, "expires_ms": 1792592000000,
                "packages": [ { "name": "support-tickets", "kind": "decision-task", "serial": 7,
                                "manifest": "<sha256>", "capabilities": ["decide.questions"] } ],
                "yanked": [ { "manifest": "<sha256>", "why": "unsafe" } ] },
  "signatures": [ ... ] }
```

Signed under `poolhouse/extension-contents/v1`, so a signature over a manifest, an act or an approval is not a signature over a
contents list (T3). `head` is the epoch and charter hash of the chain state in effect when the signers signed; the node requires it
to equal the latest state of the chain it verified (3.4). `serial` rises with every contents. `yanked.why` is one of `bug`,
`wrong-data`, `unsafe`, `withdrawn`. For each registry the node keeps the highest serial it has seen and the hash of that contents; it
refuses a lower serial, and a second hash at a serial it has seen is equivocation (T16). `expires_ms` is at most 60 days after
`issued_ms` and 30 days by default; the device's wall clock is read for the expiry and for nothing else. A device that has not seen
fresh contents installs and updates nothing from that registry and keeps what it has working (T14). A short-lived **heartbeat** signed
by a delegated device key would renew freshness without a tap each week; it is slice 7 and is not needed before.

### 2.3 Bounds

The format carries its limits. The numbers are starting values in `contracts/extensions/limits.json`, read by both the Rust node and
the Python loader, so there is one table. A byte count is checked before any parse; a count, a digit count or a depth is checked
while parsing.

| Item | Limit |
| --- | --- |
| Manifest | 64 KiB, nesting depth 8, at most 64 parts, 16 capabilities, 8 pins; a pin is 1 byte to 64 GiB and all pins of a package at most 128 GiB; the fetch checks free space as the model pull does (`hub/transfer.py`, `_space`) |
| Contents | 1 MiB, at most 256 packages, 256 yanks |
| Chain bundle (founding act, admin-set acts, a step in flight and its `cancel` and `veto` acts) | 256 KiB |
| A part | 16 MiB; at most 64 MiB for a package |
| Media types of a part | `application/json`, `application/x-ndjson`, `text/plain` and nothing else |
| JSON everywhere | duplicate keys refused at every depth (`poolhouse.json_objects.unique_object`), non-finite numbers refused, nesting at most 16, strings at most 64 KiB, arrays at most 100,000, at most 500,000 values in a document (counted while parsing), numbers of at most 18 digits with no exponent and no leading zero, integers in a signed body within plus or minus 2^53 |
| `decision-task` | at most 512 questions; text at most 2 KiB; at most 32 options of 80 characters |
| `benchmark` | at most 10,000 cases of 16 KiB; scorer and loader from the closed lists; at most 1,000 items in one answer |
| `extraction-schema`, the schema | at most 32 KiB, nesting at most 6, at most 64 properties an object, enums at most 256; keywords from an allow list (`type`, `properties`, `required`, `items`, `enum`, `minimum`, `maximum`, `minLength`, `maxLength`, `minItems`, `maxItems`, `description`) with their values capped: `minItems` at most 32 and not above `maxItems`, `maxItems` at most 1,000, `minLength` at most 256 and not above `maxLength`, `maxLength` at most 4,096, `minimum` and `maximum` integers of at most 15 digits; the grammar the schema builds is at most 1 MiB of text and builds in at most 5 seconds |
| `extraction-schema`, the prompting | `instructions` at most 4 KiB; `messages` at most 8 entries, each `{role, content}` with `role` `user` or `assistant` and `content` printable text of at most 2 KiB; no `prompt`; `think` a boolean that spends only what `n_predict` allows; `n_predict` an integer from 16 to 8,192, and the caller's own lower value wins; `schema_name` `[a-z][a-z0-9_]{0,31}` |
| `model-recipe` | the fields and ranges of 2.5; at most 8 models; no pass-through of server arguments |
| Fetch | `Content-Encoding` other than identity refused; a part is cut off at its declared size and refused |

### 2.4 Content-addressed storage

Parts are stored once, by hash, under `~/.poolhouse/extensions/blobs/<sha256>` (directory 0700, files 0600), written to staging,
checked for size and hash, and promoted in one step (`poolhouse.net.download` already does this). A blob is read only through an
installed record that names it, and the loader hashes it again on every load.

### 2.5 Model-recipe fields

A recipe is profile records (`poolhouse.serve.profile.Profile`) for the pins of its package. The Python reader ignores a key it does
not know, so the node's check is the gate: a key not in this table is a refusal. The loader converts the `_milli` fields to the
floats `Profile` holds.

| Field | Allowed |
| --- | --- |
| `model` | the `id` of a pin of this package. A community record is matched to the machine's file by exact file name only; the family branches of `serve.profile._matched` are not used for it |
| `workload` | `ask`, `ingest` or `chat` |
| `serve.cache_type`, `serve.draft_cache_type` | empty or a cache type of `poolhouse.kvtype` |
| `serve.draft`, `serve.mmproj` | empty, `auto`, or the `id` of a pin of this package; never a path, an `hf:` reference or a bare file name (`serve.profile.resolved` hands a path or an `hf:` reference to the server as written) |
| `serve.spec_type` | empty or one of `draft-simple`, `draft-mtp`, `draft-eagle3`, `ngram-simple`, `ngram-map-k`, `ngram-map-k4v`, `ngram-mod`, `ngram-cache` (the methods `serve/backend.py` documents; the list is in `limits.json`) |
| `serve.build` | empty or a name on the closed list in `limits.json` (starting list: `unsloth`, the build `poolhouse-serve llama-cpp` makes under that name); never a path or a repository |
| `serve.reasoning_budget` | empty, or an integer from 0 to 16,384 |
| `serve.spec_p_min_milli` | an integer from 0 to 1,000 |
| `serve.slot_context`, `serve.parallel` | integers from 512 to 1,048,576 and from 1 to 16 |
| `serve.extra_args` | absent; a record that carries it is refused |
| `request.spec_draft_max` | empty, or an integer from 1 to 32 |
| `request.sampling` | `temperature_milli` 0 to 2,000, `top_p_milli` 0 to 1,000, `top_k` 0 to 1,000, `min_p_milli` 0 to 1,000, and no other key |
| `ask.*` | the booleans of `serve.profile.FLAGS`, and `reach` (1 to 64) and `rounds` (1 to 32), each of which may be empty |
| `measured.*` | the publisher's claims: strings of printable ASCII of at most 80 characters and integers. The card shows them as "the community says"; nothing ranks or chooses by them |

**Where a recipe sits.** The community layer is the lowest: `serve.profile.profiles()` reads the community records, then the shipped
`poolhouse/data/profiles.json` over them, then the machine's own file over both. A community record fills a `(model, workload)` key
that neither of the others holds, so it never takes the place of a measurement the project ships or the machine made. When two
communities hold one key the earlier install keeps it; the later one installs and its card says "Not used: <community> already has a
recipe for this model."

## 3. Publishers, registries and trust

### 3.1 A registry is a pool

The identity of a registry is the **pool id**: the first 16 bytes of the SHA-256 of the epoch-1 charter with the keys' acceptances
left out (`docs/node.md`, "Who governs a pool"). A person pins a registry by that id. The admins who may sign for it are read from the
pool's charter chain, which travels with the registry as a **chain bundle** verified by the same code that verifies a joiner's (3.4).
The registry therefore follows the pool: an admin added, removed or rotated by the pool's own acts changes who may publish, with
nothing to keep in step.

- **Who may publish.** A manifest and a contents list need signatures from distinct people (`unit: person`) holding **strong**
  admin keys of the latest verified chain state, in number that state's `sensitive` threshold (1 while the pool lists one person, 2
  once it lists two; the device counts against the new threshold as soon as it has verified the admin change). A weak key counts for
  nothing. A pool of friends where two people must tap to publish pays that for every publish; a separate `publish` threshold in the
  charter is a later change if it proves too heavy. The signer's tap is the phone's biometric or a hardware key's touch, which an
  agent's process cannot supply.
- **A person who publishes alone** makes a pool of one. There is no second kind of publisher, no standalone publisher key and no
  list of trusted publishers shipped in the wheel. The project's own packages, if any, are one more pool a person adds.
- **A pool that is reissued** (`handoff`) has a new pool id and so is a new registry. Installed packages keep working, updates from
  the old registry stop, and the new one is added by the same one-tap approval. Nothing follows a move by itself.
- **Names inside a registry are the pool's to decide.** A name is unique within a registry. Two registries can both carry
  `support-tickets`; they are different packages (T6).

### 3.2 How a person pins a registry

The person is given a registry id and a place to fetch from: a link or QR that a member of the community shares (`Share this
community`), or a card the pool's own members see. Nothing else is needed. The pin is not a list the person edits. It is part of the
first approval (5.1): the person sees the community's name quoted as untrusted text, its id in six groups of four hexadecimal digits
(the first 96 bits, the form `phone::words` in `app/poolhouse-node/src/phone/mod.rs` already uses for a key, because a short form
can be ground to look like another), its admins as three check words each as a cue, "You have not seen this community before", and
the first package. A scanned QR carries the whole id and needs no comparison; a link in a chat is compared with the friend's screen.
Their approval pins the registry id and the chain anchor together. The card shows no date the community wrote, and no age it
computed from one.

A **source** is any place that serves the registry as static files: a member over the node's file transfer, a URL, a Hub dataset
repository. Which source served the bytes is irrelevant to trust, because contents, manifests and parts are signed or addressed.

```
contents.json            the latest signed contents
chain.json               the chain bundle
manifests/<sha256>.json  each manifest, with its signatures
blobs/<sha256>           each part
```

The first approval also puts the source's host in the pin entry (section 7). It is not an approval in `net/approvals.jsonl`:
`poolhouse.net.policy.Approval` holds a host, a time, an approver and a note with no purpose, and `Policy.approved` matches on the host
alone, so a host recorded there is reachable by every fetch the device makes. The registry's fetches run through a `Pipeline` whose
`Policy` allows only the hosts in that registry's pin entries (`net.download` takes the pipeline as an argument); the default
policy, its approvals file and the Hub's allow-list are untouched, and the sentinel ledger still applies (`Policy._tighten`). A
different host for the same registry is a new line on the next approval and reaches only that registry.

### 3.3 Signer removed

A pool that removes an admin (`admin_remove`) changes the charter epoch. A device that learns of the new epoch, and has run its own hold
for the removal (3.4), checks every installed version against it. A version whose signatures all depend on a removed key is
**disabled until a current admin endorses it**: a signature by a current strong admin on the same manifest bytes, merged into the
signature set, which then meets the latest threshold again. The installed pointer falls back to the newest installed version that has a
valid signer; the device keeps the two latest installed versions of each package for this reason. The community's admins endorse the old
versions that were good in one tap on the phone ("3 packages were signed by a key you removed. Sign them again?"). That cost falls on
the publisher, once per admin change.

A **phone that is replaced** is an admin change like any other. When the old phone still works the pool adds the new key first, the
admins endorse the listed packages with it, and only then removes the old key (the add hold and the removal hold run one after the
other, 24 hours each by default), so installers see no gap. When the phone is lost there is no overlap, and every package whose
signatures depended on it is not loaded on installers' devices until an endorsement reaches them (A9).

### 3.4 The chain head, and an admin change in flight

A registry device does not take part in the pool, so nothing tells it of an admin change except the chain bundle it fetches. What
the pool's own members get from holds, `cancel`, `veto` and disputes (`docs/node.md`, "Who governs a pool"; `gov/chain.rs`) reaches a
registry device only as follows.

- **The bundle.** `chain.json` holds the founding act, the admin-set acts that lead to the head the contents sign (taken as settled,
  as a joiner takes them: their holds ran at the pool, and `gov/pool.rs` marks them `SETTLED`), and every admin-set act that
  follows that head: a step in flight and the `cancel` and `veto` acts for it. The device builds the chain with the code that
  builds a member's. A hold on this device is counted in its own awake time from the first fetch that delivered the act
  (`gov/clock.rs`); the signer's `at_ms` and the wall clock are never read for a delay.
- **The head.** Every contents list signs its `head`, and the node requires it to equal the latest state of the chain it built. A
  contents list at a lower head was signed by an admin set the pool has left (refused); one at a higher head than the bundle reaches
  waits and the bundle is fetched again. The signers of the contents and of every manifest, and the threshold, are those of that
  state, never of a field the signers wrote (T3).
- **An admin change in flight or a dispute open.** While the chain the device built has a pending step or a dispute (`pending` and
  `dispute` of `gov/chain.rs`), the node makes no pin card, no install card and no update card for that registry, and the list says "The
  community is changing who can publish. Offers come back in about N hours" (the hold left, or the dispute's days). Installed packages
  keep loading. A `yank` in a contents list is still honoured, because it only disables. Going back, removing, unpinning and holding
  are the person's and work throughout. A `cancel` or `veto` in the bundle ends the step on this device as it does for a member.
- **The publisher's side.** `ext_draft` and `ext_publish` refuse to draft or sign while the publisher's own chain shows a step in flight
  or a dispute, so admins do not sign under authority that is being changed.
- **What this does not do.** A key the pool has removed signs validly on a device that has not fetched the removal, and, once it has,
  until that device's own hold has run, except that the device offers nothing new during the hold. A first pin, a device offline for
  weeks and a source that serves an old bundle with old contents inside their expiry all act on the admin set they were given (A8).

## 4. Verification: the node, not the agent

The node verifies, in Rust (`app/poolhouse-node/src/ext/`), because the approval card is made of fields the node checked and
a Python process that an agent started cannot be that authority (`docs/compromised-agents.md`, U1 and I2). Whatever moves bytes
(the guarded download, a member's file transfer, a mirror, an agent) hands them to the node and is trusted with nothing.

The checks, in order. The first failure refuses the offer with one plain sentence the agent can relay.

1. **Size before parse.** Every document is within its byte limit; a part is within its declared size.
2. **Parse strictly.** Duplicate keys, floats in a signed body, nesting beyond the limit, more than the value count, a number beyond
   its digits, a field nobody defined and an unknown `v`, `kind`, `alg` or capability are refused.
3. **Chain.** The chain bundle verifies from the pinned anchor (the pool id) with the code that verifies a joiner's chain and, for the
   acts that follow the head, with this device's holds (3.4). The registry's epoch is not lower than the highest the device has seen
   for it. For an offer, the latest state has no step in flight and no dispute; a yank is read either way.
4. **Signatures.** The contents and the manifest each carry signatures from distinct strong admins of the latest state of the verified
   chain, in number that state's sensitive threshold, over their own domain strings. The contents' `head` equals that state's epoch
   and charter hash.
5. **Freshness and equivocation.** The contents are within `expires_ms` (skew allowed), and their serial is not below the highest
   seen. A second contents hash at a serial seen, or a second manifest hash for a `(name, serial)` seen, is equivocation: both
   documents are kept in `seen.json`, the registry is held with "The community published two different versions of the same
   release", and nothing new is offered from it until the person looks (T16).
6. **Binding.** The manifest's hash is listed in the contents with the same name, kind, serial and capabilities. The manifest's
   `registry` is the pinned registry. `serial` is above the installed one for an update, and `prev` names it. The manifest is not
   yanked.
7. **Parts.** Each part's size and hash match the manifest, its name passes `poolhouse.safenames.safe_filename`, its media type is on the
   list and its bytes parse under the kind's schema within the kind's bounds.
8. **Capabilities and pins.** The declared set equals the kind's required set. Each pin is checked by rules the Rust verifier ports
   from the Python, with golden vectors shared in `contracts/extensions/vectors.json`: the repository passes
   `hub.publishers.namespace` (owner and name each `valid_name`, nothing else in the string); every `/`-separated part of the filename
   passes `safe_filename`; the revision is 40 lower-case hexadecimal digits; the hash is 64; the size is within the bound. Whether the
   publisher is known is not judged here (below).
9. **Diff.** The node computes the diff against the installed version with the kind's differ (5.2).

The node then holds the offer, makes the card and waits for a person. After approval it writes the install, once, from the bytes it
already verified, and the blobs it promotes are hashed again as they are written. The approved manifest hash is the only thing an
install can use, so nothing can change between the tap and the write (T2).

**A pin's file.** `decide/fetch.py` (`locate`, `_fetch`) does not call `hub/admission.admit`; only `hub/transfer.py` (`_bring`) does,
so a `Pin` fetched through `decide.fetch` meets no publisher check. A recipe's model file is therefore fetched by a new entry,
`poolhouse.extensions.fetch_pin`, which builds a `Ref` (the repository, the revision as its `commit`) and a `RemoteFile` (path, size,
hash), calls `admission.admit` and only then `fetch.locate(pin, download=True)`. A publisher that is not known, a name marker or a
digest that differs from one pulled before asks the person as `model_publisher`, as it does for any model; that ask is separate from
the extension approval and says whose file it is. The pins of the shipped deciders (`decide/pins.py`) are not routed through it by this
design (T18).

The Python library has no verifier of its own. Loading (`src/poolhouse/extensions/`) reads the installed set (section 7), checks that
each entry is signed by the device's key and carries an approval that verifies under an enrolled person key, that its counter `n` is
the highest for its package and not below the high-water mark the node recorded (T20), hashes every part again, re-applies the bounds
and hands the kind handler a typed object. A tampered or missing part makes that package "not loaded", with the reason, and nothing
else.

## 5. Install, update and the diff

### 5.1 Two ways in, one approval

1. **The person starts it** on their own screen: "Add a community" in Settings, a shared link or QR, or a card in the list the pool's
   members see. The screen fetches the bytes through the registry's own pipeline (3.2) and passes them to the node (`ext_offer`). The
   card is the approval.
2. **An agent suggests it.** The agent calls `ext_suggest` with a registry id, a name and a source hint (at most 200 characters).
   The node writes a row in the person's quiet list: "Your assistant suggested the benchmark 'support-tickets' from a community.
   Nothing is installed." It is not a push, it is not an approval, and it fetches nothing. The person opens the row, which does what
   path 1 does. A suggestion carries no note from the agent beyond the agent's 160 characters shown as "the assistant says (not
   checked)". An agent that has been told by the person's own words, verified by the node (`docs/compromised-agents.md`, U3 and the
   `matches` verdict), to add that community gets its suggestion in the normal lane; while that evidence source does not exist every
   suggestion is quiet, and the agent says where to look.

**Who can call what.** The node cannot tell the person's page from an agent by the harness and model a session gives
(`docs/node.md`, "The person's session"), so no extension call takes its caller for the person. `ext_offer` is an approval request
attributed to the session that made it: the defences of `docs/phone-approval.md`, "Approval fatigue", count it as they count any
request (one item for the same offer, backoff, holds, the push budget, caps on the person, a stranger device listed and never
pushed), an offer from a held session is kept and shown to nobody, and one the evidence source says nobody told is listed and never
pushed. The node stages at most 2 offers for a session and 8 in all, for 24 hours. `ext_status` is read-only and gives every caller
the agent's view (section 8): names, kinds, versions, states and publisher text inside the untrusted fence, with no approval id,
challenge, host, path or blob name. `ext_versions` gives the same view of one package's older versions (5.4). `ext_suggest` stays the
one call that makes only a quiet row.

No agent call creates an install or pins a registry. `ext_install` takes an approval id and nothing else, and the node refuses it
unless the approval's signature verifies under an enrolled person key and its challenge names this device, this manifest hash, this
operation and the next counter of this package (5.2).

### 5.2 The card

The card is made by the node from checked fields, in plain ASCII, one fact a line. It follows `docs/phone-approval.md`, "The
plain-words text", and its challenge is the signed object of "The signed challenge" with a new action kind:

```
"action": {"kind": "extension", "op": "pin_install" | "install" | "update" | "update_batch" | "remove" | "unpin" | "rollback",
           "registry": "<pool id>", "items": [ {"name", "serial", "manifest", "from": "<manifest or empty>", "seq", "capabilities": []} ],
           "source_hosts": ["..."] }
```

`from` is the manifest the device has installed and the approval replaces; `seq` is one more than the number of writes the node has
made for that package, so an approval fits one moment of one package's history (T20). The challenge's `session` is the session that
made the offer: the page's for an install the person started, an agent's for one it made; the template's `SAID:`, `VERDICT:`,
`TAINT:` and `WHO:` lines are about that session, and the community has its own line, `FROM:`.

```
FLAG:   <one line for each that applies, in this order, none left out: two versions of one release | a signer new to this device | a signer endorsed after a removal | a name like one you have or one that ships | a widening | declined before>
RISK:   SENSITIVE (a community you have not used) | ROUTINE (an update that asks for nothing more)
FROM:   The community 'ticket-triage' (its own name, not checked). Id 3fa1 9c20 b7d4 e118 0a5c 62fe. Admins: maple-river-ten, tide-oak-nine (words are a cue; compare the id). You have not seen this community before.
ASK:    Add the decision questions 'support-tickets', version 7.
GIVES:  12 questions that your decision models answer. It runs nothing by itself.
NEEDS:  No other permission. It names no model files.
SIZE:   18 KB in 1 file.
CHANGED (update only): 2 questions added, 1 changed, 0 removed. Permissions unchanged. Model files unchanged.
SAYS:   The community says: "Adds two questions about refunds." (not checked; the manifest's `changes` on an update, its `says` on a first add)
CANNOT: Read your files or memory, reach the network, change your settings, or run anything unless you start it.
EXPIRES: This request ends at <time> UTC.
```

The node also writes the `SAID:`, `VERDICT:`, `TAINT:` and `WHO:` lines of that template, which are always present. `FLAG:` and `RISK:`
are the node's, and every flag that applies is its own line on the card, the notification and the list, never folded into the details
screen. A **signer new to this device** is a key whose first signature this device has not seen for this registry before; the device
records that itself (section 7) and shows it as "you first saw this key on <date of this device>", never as a date the community
wrote. On a first pin every signer is new, and `FROM:` says so once instead of a flag. The diff behind `CHANGED:` is made by the kind's differ from the two verified versions: questions added,
changed and removed by name, scorer and loader changes with old and new values, schema fields added, removed or retyped, recipe
fields with old and new numbers, pins added or changed, capabilities gained or lost. A change beyond twelve lines shows a count and
opens in full on the details screen, which the node also makes. A publisher's words appear only on the `SAYS:` line, quoted and
labelled, and never in the sentence that says what is asked.

An approval is a signature on the card bytes, so what the person saw is what was signed. `approve_repeat` applies when the person
declined the same manifest hash before. A declined manifest hash is not offered again; a newer serial is offered.

### 5.3 Updates

- The node refreshes the contents of each pinned registry when the device is idle and at most daily. An update is an offer waiting in
  the list. **Nothing switches version** until a person approves, and the installed version keeps working meanwhile.
- A **plain update** has the same kind, capabilities and pins, only signers this device has seen, and a diff that only adds items
  (questions, cases) or changes `title`, `says` or `changes`. The **weekly list** gathers plain updates: "4 updates are ready. [Update
  all] [Look first]". Its card shows every update with its whole diff: name, version, and each added item by name with its first 80
  characters, up to twelve lines in all; the update that does not fit waits for the next list. `Update all` signs one `update_batch`
  challenge that lists each name, version and manifest hash on that card. The list makes at most one quiet notification a week.
- Anything else is a card of its own, never in a batch: a changed or removed question, case, option, scorer or loader; any change to a
  schema or a recipe; a new capability; a new or changed pin; a signer new to this device; a changed kind.
- The fatigue defences of `docs/phone-approval.md`, "Approval fatigue", apply to suggestions and cards unchanged: one item per
  request, backoff, holds, a push budget, no consent by silence. An update that nobody answers waits and expires from the list
  without effect.

### 5.4 Going back, removing and holding

- **Back** (`rollback`) names an older manifest hash from the version list that `ext_versions` and the details screen show. The
  versions are the installed manifest's `prev` chain; every source serves each manifest by hash under `manifests/` (the contents list
  current packages only), and the device keeps the two latest it installed. Each line has the serial, the community's `changes` text
  (untrusted), whether it is yanked and whether this device holds it. A source that stops serving an older manifest ends going back
  to it unless the device holds it. A yanked version is not offered; the signer check is an update's, at the latest verified state;
  the card says OLDER. It is the only way the installed serial falls (T2).
- **Remove** takes a package out and keeps its blobs for the two-version fallback until the person clears them. **Stop using this
  community** (`unpin`) disables every package of the registry and stops refreshing it.
- **Keep this version** (`hold`) stops offering updates to a package until the person lifts it.

## 6. Taking things back

| What | Who does it | What a device does |
| --- | --- | --- |
| Yank a version, `why: bug` or `wrong-data` | the community's signers, in a new contents list | Lists "the community withdrew version 7"; keeps it loaded until the person moves; the update card offers the fixed version when there is one. |
| Yank a version, `why: unsafe` | the same | Does not load the version at the next refresh, falls back to the previous installed version if it has a valid signer, tells the person once with [Remove]. |
| Revoke a signing key | the pool, by its own admin acts (`docs/node.md`, "Who governs a pool") | The device that has the removal offers nothing new from the registry during its own hold, then the key's versions wait for an endorsement (3.3, 3.4). |
| Stop using a community | the person | `unpin`. |
| A known-bad hash or registry | the project's release pool, in a signed deny list (slice 7) | Disables only. A deny list can never install, enable or widen anything, so its compromise can disable packages and do nothing else. |

A yank and a key removal reach a device only when it fetches fresh contents and chain. A device that cannot reach a source keeps what
it has and says so ("Last checked 12 days ago"); once the contents it holds expire it takes no new install or update from that
registry.

## 7. Where state lives

| State | Where | Why there |
| --- | --- | --- |
| Pins (with the source hosts), installs, updates, removals, holds, rollbacks | Entries on the `#extensions` channel of the board `pool`, each signed by the device's key, numbered by a per-package counter `n` and carrying the person's approval object, as a grant carries one (`docs/node.md`, "Standing grants"). Whether other members copy the channel follows the board's sharing and is settled in slice 3; nothing depends on it, because a device trusts only entries that name its own certificate | Signed, append-only, verifiable by any device that holds the person key; the installed set is the **fold** of these entries, never a directory listing. |
| High-water marks: per registry the highest contents serial with its hash, the head, the freshest expiry, the highest manifest serial and hash per name, declined hashes, the first sight of each signer (fingerprint and this device's own date); per package the highest `n` | `extensions/<registry id>/seen.json` in the node's state, written only by the node | A tiny security cache the node reads before anything else, like `pools/<id>/seen.json`. It records what was fetched but never installed, which no entry would, and what the loader checks entries against. |
| Blobs | `~/.poolhouse/extensions/blobs/<sha256>` | A cache; a missing or changed blob makes its package not loaded. |
| Catalogue and provenance: registry, package, version and part nodes; `publishes`, `supersedes`, `installed-from` edges; edges from each benchmark run record to the manifest hash it ran | The graph store (`poolhouse.graph.GraphStore`), rebuilt from the entries and blobs | Relational and searchable; nothing in it is authority, so deleting it loses nothing. |

Every benchmark, decision and extraction run record that used a package names its manifest hash and registry id, so a kept result
says which version of which community's data it measured. A result from a local file is marked `local` and does not compare with
one from a signed package (D14).

A device installs for itself. An entry names the device's certificate, so another member that copies the entries or a state
directory finds nothing installed for its own certificate and must be approved separately (T1).

## 8. The person's journey

A person taps to add a community and, now and then, to update. There is no terminal, file, key, port or command.

| Step | What the person does | What they feel |
| --- | --- | --- |
| Add a community | Opens a link or card a friend shared, compares the id with the friend's screen (a scanned QR needs no comparison), reads a plain card, approves | One approval for pinning, the source host and the first package |
| An update that only adds | Presses "Update all" once a week, or ignores it | The card shows all of it; nothing breaks if ignored |
| An update that changes or asks for more | Approves on its own card | The card says what is new |
| Something withdrawn as unsafe | Reads one line | It stops loading by itself |
| Publishing (a community admin) | Signs on the phone, as for any admin act; the card shows the node's diff against the last version | One tap per admin per publish |
| Leaving a community | Presses "Stop using" | Its packages stop loading |

Sentences that work as written: "Add the benchmarks from the ticket-triage pool." (the agent asks for the link, makes the suggestion and says
"Look for the ticket-triage card in your list"), "What have I installed from other pools?", "Publish my question set to my pool.",
"Go back to the last version of the support questions.", "Stop using the ticket-triage pool's packages." The agent's refusals use `say`,
`needs` and `then` of `docs/pools-and-serving.md`, section 2, for example "That version is older than the one you have; tap to go
back on purpose."

**The agent's view.** An agent that lists packages gets names, kinds, versions, states and the older versions of one package, with
every word a community wrote inside an untrusted fence and nothing else of theirs. A community benchmark's run prints case ids and
scores by default; its case text, expected answers and the model's replies (`poolhouse.bench.detail`, `poolhouse.bench.show`) print
only inside the same fence, with control characters removed and at most 200 characters a field (T9).

Friction audit (`AGENTS.md`, "Security without friction"): signature, chain, binding, bounds, freshness, equivocation and the diff are
invisible. The visible costs are one approval to add (the thing prevented is a signature the person did not see), for a link in a
chat a comparison of the id that a scanned QR makes unnecessary, one weekly list, and a card when a package changes what it had or
asks for more. Updates that only add cost one tap a week, never one per package.

## 9. Attack first

Written for this design before it is built. For each case: the defence, the test that fails without it, and the status. "Fixed" means
the design closes it; "Accepted" is a risk named for the owner in 9.2. Test names are proposals in the files of section 10; each test
must be seen to fail with the named line removed (`AGENTS.md`, "Commit before you mutate").

| # | Attack | Defence | Test that fails without it | Status |
| --- | --- | --- | --- | --- |
| T1 | **A copied or cloned registry or pool.** Someone copies the registry's files to their own source, clones a member's state directory, or founds a pool with the same name and other admins. | The registry id is the pool id, the hash of the founding charter, so a pool with other admins has another id and no pin. A copy of the files at a mirror is the same bytes with the same signatures and gains nothing. A cloned member holds no admin key (strong keys live on phones), so it signs nothing. Installs name the device certificate, so a cloned install store is "not installed" on the copy. | `ext_verify.rs::registry_with_other_admins_has_another_id_and_no_pin`, `::mirror_copy_verifies_identically`, `::install_entry_for_another_certificate_is_not_loaded` | Fixed |
| T2 | **Replay or rollback.** An old, validly signed version or contents list is served again to bring back a bug, hide a yank or undo an update; or bytes change between the tap and the write. | Highest contents serial, hash and head per registry in `seen.json`; a manifest's serial must exceed the installed one and `prev` must name it; a yank is final for that hash; the approval binds the manifest hash and the node installs the bytes it already verified; the loader re-hashes. The installed serial falls only by a signed `rollback` that says OLDER. | `ext_verify.rs::lower_contents_serial_is_refused`, `::old_manifest_does_not_replace_a_newer_install`, `::yanked_hash_is_not_offered`, `::bytes_changed_after_approval_are_not_installed`; `tests/test_extensions_load.py::test_changed_part_makes_package_not_loaded` | Fixed |
| T3 | **A forged or below-threshold signature, or a lowered threshold.** One valid signature at threshold two; a forged signature; a weak key; an admin signature taken from another document (a pool act, an approval, a contents list used as a manifest); a manifest that names an older epoch so that an older charter's threshold applies (a pool that grew from one person to two needs two signatures at its latest state and needed one at epoch 1). | Signers and threshold come from the latest state of the verified chain, never from a field of the document. A manifest names no charter and an `epoch` or `charter` field is a format error; contents sign their `head`, which must equal that state. Distinct strong admins, each verified over the domain string of that document type (`poolhouse/extension/v1`, `poolhouse/extension-contents/v1`); weak keys and a repeated signer count once or not at all; the algorithm is read from the key. | `ext_verify.rs::one_of_two_signatures_is_refused`, `::weak_key_does_not_count`, `::repeated_signer_counts_once`, `::pool_act_signature_is_not_a_manifest_signature`, `::manifest_signature_is_not_a_contents_signature`, `::threshold_is_the_latest_states_after_the_pool_grows`, `::manifest_with_epoch_or_charter_field_is_refused`, `::contents_with_an_older_head_is_refused` | Fixed |
| T4 | **A malicious publisher.** A pinned community publishes a package whose data is harmful as data: a benchmark that rewards wrong answers, questions that bias a decider, a recipe that makes serving slow. | The package is data for a closed handler, so it cannot act. The person sees what it does in plain words and approves the diff of every update, alone or as a batch whose card shows all of it (5.3). Recipes rank below shipped and local measurements (D13); the broker admits against memory whatever a recipe says. A community benchmark's results are labelled with its registry in reports. | `tests/test_extensions_recipe.py::test_local_profile_beats_community_recipe`, `::test_shipped_profile_beats_community_recipe`, `::test_recipe_cannot_exceed_broker_admission`; `ext_verify.rs::update_that_adds_a_capability_is_its_own_card` | Fixed for harm beyond data; Accepted (A4) for misleading data |
| T5 | **A typosquat or look-alike.** `support-ticket` beside `support-tickets`; a name close to a shipped item; a community named like a known one; confusable letters in a name. | Names are ASCII `[a-z0-9-]`, so a homoglyph cannot be written. The node folds names with `hub/publishers.py` (`_fold`: case, leet digits, separators) and compares each new item and community name with the installed names, the names that ship for its kind and the other pinned registries' names, by equal fold and by edit distance on the fold (Damerau-Levenshtein, at most 2, at most 1 when the folded name has under eight characters); a match is a `FLAG:` line. The fold alone would not catch the example (`supportticket` and `supporttickets` differ by an `s`), so the distance does. The flag is a warning; the defence is that no resolver takes a name: installed items are addressed `<registry id>/<name>` (1.1) and a security-path caller reads none, so a look-alike cannot take the place of what it imitates. | `ext_verify.rs::name_with_non_ascii_is_refused`, `::lookalike_name_in_another_registry_raises_a_flag`, `::plural_of_an_installed_name_raises_a_flag`, `::name_one_edit_from_a_shipped_name_raises_a_flag`; `tests/test_extensions_load.py::test_bare_name_never_resolves_to_an_installed_item`; `tests/test_extensions_kinds.py::test_decide_guards_cannot_reach_the_installed_set`; `tests/test_extensions_card.py::test_lookalike_flag_is_first_line` | Fixed for substitution; the community's own name remains untrusted text (A2) |
| T6 | **Dependency confusion between registries.** A package from a second registry carries the name of one from the first, hoping a reference resolves to it. | Phase 1 has no dependencies. Every reference to a package names `(registry id, name, manifest hash)`; a bare name resolves nowhere in the installed set (1.1); local aliases are per device and never appear in a signed document. | `ext_verify.rs::manifest_reference_to_another_registry_is_refused`, `::bare_name_never_resolves_across_registries`; `tests/test_extensions_load.py::test_two_registries_same_name_are_two_packages` | Fixed |
| T7 | **A compromised publisher key.** One admin's phone is stolen and signs a malicious update. | The sensitive threshold (two people once two are listed); a strong key needs the phone's biometric; every installer still approves the diff; a signer this device has not seen sign for the registry raises a `FLAG:` (the device's own record, never a date the key's owner wrote); the pool removes the key, and a device that has the removal offers nothing new during its own hold (3.4), then disables versions only that key signed until a current admin endorses them. | `ext_verify.rs::version_signed_only_by_a_removed_key_is_disabled`, `::pointer_falls_back_to_previous_valid_version`, `::endorsement_by_current_admin_restores_it`, `::new_signer_raises_a_flag`, `::removal_in_flight_freezes_new_offers` | Fixed with two or more people once the device has the removal; Accepted (A1) for a pool of one and (A8) before the device has it |
| T8 | **A malicious or compromised agent installs without the person.** It calls the node, writes the installed set by hand, forges a `#extensions` entry, asks for a grant that covers installing, or floods the person with `ext_offer`. | No call an agent token makes creates an install: `ext_install` takes an approval id and verifies it under an enrolled person key for this device and this hash. `extension_pin` and `extension_install` are floors, refused by name for any grant. A forged entry is listed as ignored, as a forged `grant` entry is. A hand-written install fails the loader's approval check. `ext_suggest` only writes a quiet row. `ext_offer` from any session is an ordinary request of that session under the fatigue defences, with a bounded stage; `ext_status` carries nothing an agent could use to forge an approval (5.1). | `app/poolhouse-node/tests/ext_floor.rs::floor_refuses_extension_install_for_any_grant`, `::install_without_person_signature_is_refused`, `::forged_extensions_entry_is_ignored`, `::offer_counts_in_the_requesters_push_budget`, `::held_sessions_offer_is_shown_to_nobody`, `::staged_offers_are_bounded`, `::status_carries_no_approval_id_host_or_path`; `tests/test_extensions_load.py::test_hand_written_install_is_not_loaded`; `::test_suggest_creates_no_approval_and_no_push` | Fixed |
| T9 | **An agent compromised by injected text in a package description, name, schema or benchmark output.** A `says` or a question text tells the agent to approve something, a schema description steers the extraction, or a community benchmark's case text or a model's reply to it, printed by `poolhouse-bench show` or `detail` to the lead, carries an instruction. | Publisher text is capped, printable ASCII and shown only quoted on `SAYS:`; an agent reading a listing gets it inside an untrusted fence, never in the node's sentences. The same fence, with control characters removed and 200 characters a field, wraps every case text, expected answer and reply a community benchmark's run prints; ids and scores are printed bare. The agent cannot install (T8), so a steered agent gets a quiet row at most. Question texts and schema descriptions reach models whose outputs are typed (probabilities over closed options, or a grammar-constrained shape), and their results are data under the taint rules of `docs/taint.md`. Scorers are deterministic, so case text cannot steer a score. | `tests/test_extensions_card.py::test_publisher_text_appears_only_on_says_line`, `::test_non_ascii_publisher_text_is_refused`; `tests/test_extensions_agent_view.py::test_listing_fences_publisher_text`, `::test_bench_detail_fences_community_case_text`, `::test_bench_show_prints_ids_and_scores_bare`; `tests/test_redteam_extensions.py::test_injection_in_says_creates_no_grant_or_install` | Fixed for actions; Accepted (A4) for wrong data |
| T10 | **Downgrade from signed to unsigned.** A package with no signature, an old format, a weaker algorithm, or a local file offered in place of a name. | There is no unsigned package type and no setting: a document without valid signatures is a format error, not a lesser class. Only `v: 1` and the two algorithms parse. A local file is an argument of another type; name resolution reads the installed set only, never a path (D14), and run records mark `local`. | `ext_verify.rs::manifest_without_signatures_is_refused`, `::unknown_version_and_algorithm_are_refused`; `tests/test_extensions_load.py::test_name_never_resolves_to_a_path`, `::test_local_run_is_marked_local` | Fixed |
| T11 | **Approval fatigue from many updates.** A community publishes weekly, or an agent suggests constantly, until the person taps without reading. | Only plain updates are batched, once a week, on a card that shows all of them; any update that changes or removes something it had, or asks for more, is its own card and is never batched; nothing breaks if ignored, so nothing is urgent; a declined hash is not re-offered; suggestions are one item per request, quiet, and subject to the holds and push budget of `docs/phone-approval.md`. | `ext_floor.rs::widening_update_is_never_batched`, `::changed_question_is_never_batched`, `::batch_card_lists_every_update_with_its_whole_diff`, `::declined_hash_is_not_offered_again`; `app/poolhouse-node/tests/ext_fatigue.rs::two_hundred_suggestions_are_one_row`, `::updates_make_one_quiet_notice_a_week` | Fixed; Accepted (A5) for a person who taps "Update all" without reading the card |
| T12 | **Takeover by whoever is merely present.** Someone at the unlocked laptop, a member of the pool, or the pool's growth in size adds a community or approves an update. | The approval is a strong person key's signature (the phone's biometric); the laptop's weak key cannot sign it where a strong key is enrolled. A member of the pool cannot cause an install on another member's device; a pool recommending a community puts a card in each member's list, never a pin. More members or more time grant nothing. | `ext_floor.rs::weak_key_cannot_approve_pin_when_a_strong_key_exists`, `::member_recommendation_is_a_card_not_a_pin` | Fixed where a phone is enrolled; Accepted (A10) where only a weak key is |
| T13 | **A data package that is a vehicle for code execution, server arguments or resource exhaustion**: a zip bomb, a regular expression that backtracks, a schema that explodes the grammar, unbounded fields or counts, a path in a part name, a template that reaches attributes, a recipe that hands the server a path, a reference or an argument. | No archive or compression: stored size is real size, and fetches refuse any content encoding but identity. Byte limits are checked before parsing, count, digit and depth while parsing, duplicate keys refused. No YAML, pickle, regular expression, `$ref`, `pattern`, expression language or `str.format`-style template; templates are fixed placeholders replaced by name. Scorers and loaders are a closed list in the wheel. Schema keyword values are capped (2.3) and the grammar the worst allowed schema builds is bounded in time and memory. Example messages are `user` and `assistant` only and a raw `prompt` is refused. Part names pass `safe_filename`. Recipe fields are the closed table of 2.5: typed, ranged, `draft` and `mmproj` only pin ids or `auto`, `spec_type` and `build` from closed lists, no `extra_args`, unknown keys refused. | `ext_bounds.rs::oversize_manifest_refused_before_parse`, `::deep_nesting_refused`, `::duplicate_keys_refused`, `::part_name_traversal_refused`, `::part_larger_than_declared_is_cut_off`, `::archive_and_compressed_media_refused`, `::gzip_content_encoding_refused`, `::value_count_and_digit_caps_refused`; `tests/test_extensions_kinds.py::test_schema_ref_pattern_and_format_refused`, `::test_schema_numeric_caps_refused`, `::test_worst_allowed_schema_builds_grammar_within_budget`, `::test_message_role_other_than_user_or_assistant_refused`, `::test_n_predict_out_of_range_refused`, `::test_recipe_server_arguments_refused`, `::test_recipe_draft_path_and_hf_reference_refused`, `::test_recipe_unknown_key_refused`, `::test_recipe_spec_type_and_build_closed`, `::test_template_attribute_access_is_not_evaluated`, `::test_scorer_not_on_list_refused` | Fixed |
| T14 | **A freeze or withholding attack.** A mirror serves the last good contents forever to hide a yank, a key removal or a new epoch. | Contents expire (30 days, at most 60); an expired list allows no install or update from that registry; the contents' signed head must equal the head of the chain served with them, so fresh contents beside an old chain, or an old list beside a new chain, is refused; the device says when it last checked; a second source can be tried; the heartbeat (slice 7) shortens the window to days. | `ext_verify.rs::expired_contents_allow_no_install_but_installed_version_keeps_working`, `::stale_check_is_reported`, `::contents_head_ahead_of_the_bundle_waits_and_refetches` | Fixed up to the expiry and for a chain the device has been told of; Accepted (A3, A8) inside it |
| T15 | **A bad first pin.** A link in a chat names an attacker's pool as a friend's, with admin keys ground until their three check words match the friend's. | The card shows the registry id in six groups of four (96 bits, the form `phone::words` uses for a key), to compare with the friend's screen; a QR scanned from the friend's screen carries the whole id and needs no comparison; the three check words are labelled a cue. The pin, the source host and the first package are one approval, so nothing is pinned silently. An agent's link is a suggestion, so a steered agent cannot make it. | `tests/test_extensions_card.py::test_pin_card_shows_registry_id_in_groups_of_four`, `::test_check_words_are_labelled_a_cue` | Accepted (A2) |
| T16 | **Equivocation.** The signers, or a mirror holding their signatures, serve two different contents lists at one serial, or two manifests for one name and serial, to different devices or at different times. | The node records the hash of the highest contents and of each `(name, serial)` manifest it has seen; a second hash is equivocation: both signed documents are kept, the registry is held with a plain sentence and offers nothing new until the person looks. Two devices that never meet cannot detect it. | `ext_verify.rs::second_contents_hash_at_a_serial_is_equivocation`, `::second_manifest_hash_for_a_name_and_serial_is_equivocation`, `::held_registry_offers_nothing_new` | Fixed on one device; Accepted (A11) across devices |
| T17 | **A stale chain, or an admin change in flight.** A removed key is still served as an admin by an old bundle; a removal, a threshold change or a dispute is pending while a key being removed publishes. | The contents' head must equal the verified chain's latest state. A step in flight or a dispute the device has been told of stops pin, install and update cards from the registry for the hold or the dispute (3.4); the publisher's own node does not sign during one; installed packages keep working and yanks are still honoured. A device that has not been told of a change cannot know it. | `ext_verify.rs::pending_step_stops_new_offers`, `::open_dispute_stops_new_offers`, `::cancel_in_the_bundle_ends_the_step`, `::yank_is_honoured_during_a_step`; `ext_publish.rs::publishing_is_refused_during_a_step` | Fixed once the device has the bundle; Accepted (A8) before |
| T18 | **A recipe that pins a hostile model file or repository.** A pin names a repository string that reaches another path or host, a filename that climbs, an unknown publisher or a name that says the model was altered, or a file of an absurd size. | The verifier validates repository (`hub.publishers.namespace`), filename (`safe_filename` per part), revision (40 hexadecimal digits), hash and size bounds. The fetch goes through `hub/admission.admit` before `decide.fetch` (4): an unknown publisher, a name marker or a changed digest asks the person as `model_publisher`. The file is size and hash checked by the guarded download. Pin count, size and free space are bounded. | `ext_verify.rs::pin_repo_filename_and_revision_are_validated`, `::pin_size_and_count_are_bounded`; `tests/test_extensions_recipe.py::test_pin_fetch_calls_admission_first`, `::test_unknown_publisher_asks_before_download`, `::test_name_marker_asks_before_download` | Fixed |
| T19 | **A source host approval that reaches every fetch.** The first approval of a community's host is recorded where any fetch of any purpose can use it. | The host is stored in the registry's pin entry and used only to build the `Policy` of that registry's pipeline; nothing is written to `net/approvals.jsonl`, and the default policy is untouched. | `tests/test_extensions_load.py::test_source_host_is_not_in_net_approvals`, `::test_other_fetch_cannot_reach_a_registry_source_host` | Fixed |
| T20 | **A re-wrapped old approval.** An approval the person gave for an older install or update is put into a new entry to bring an older version back, or to replace what was installed since. | An approval binds `from` (the manifest it replaces) and `seq` (the next counter of that package); the entry carries `n` and the node records the highest `n` per package in `seen.json`; the loader loads an entry only if its `n` is the highest for its package and not below that record, and refuses a package whose entries end below it. A rollback is an approval that says OLDER. | `ext_floor.rs::approval_for_update_from_another_manifest_is_refused`, `::approval_with_an_old_seq_is_refused`; `tests/test_extensions_load.py::test_rewrapped_old_approval_is_not_loaded`, `::test_entries_missing_below_the_recorded_n_make_package_not_loaded` | Fixed; same-user rollback of the record itself is A6 |

### 9.1 What the tests have to be

Each row's tests are written first and watched fail against the parent commit. Tests use real Ed25519 keys (`Signer.generate()` in
Python, `ed25519-dalek` in Rust), real files in `tmp_path` or `tempfile`, and the real node in the integration tests of slices 3 and 4;
they never use the real keystore, home or network (`tests/README.md`). A shared file of golden vectors in
`contracts/extensions/vectors.json` is read by the Rust tests and `tests/test_extension_vectors.py`, so the two languages agree on the
signed bytes and on the pin rules (`valid_name`, `safe_filename`, the revision form).

### 9.2 Accepted risks, for the owner

| # | Risk | Why it is accepted, and what would change it |
| --- | --- | --- |
| A1 | In a pool of one, a stolen phone with its biometric bypassed can publish a malicious data package. | Installers still approve the diff and the package is data. A second strong key or a hardware key (the backup nudge of `docs/pools-and-serving.md`, 1.8) removes it. |
| A2 | The first pin rests on a link the person received, and on a comparison they may not make. A person who does not compare, or whose friend's message was forged, pins an attacker's community and, in the same approval, installs its first package. The three check words of a key can be ground offline until they match a chosen target (`phone::words` shows 96 bits for that reason); the join code is different, checked by a password-authenticated exchange with three tries, and the words of an agent-in-path join come from a fresh key exchange nobody can prepare. | Comparing the id with the friend's screen, or scanning the friend's QR, is the defence; nothing else exists without a central authority. A community name is untrusted text, and what a hostile community can then do is bounded to data (T4, T9, T18). |
| A3 | Inside the contents' expiry (30 days, at most 60), a mirror can hide a yank. | Shortened by the heartbeat. A person can ask for a refresh from another source. |
| A4 | A signed, approved package can still mislead: a benchmark that favours a model, a question pack that biases a decider, a recipe that serves badly. | A signature says who, not that it is true. Run records name the package and community; local and shipped measurements win over a recipe. |
| A5 | "Update all" can be tapped without reading the card, or without opening the details of an added question. | The batch carries only updates that add items or change wording, and its card shows each one's whole diff and the first 80 characters of every added item. Anything that changes or removes what the community published before, or asks for more, is alone. |
| A6 | Malware running as the person can delete or roll back what the node wrote (`seen.json`, the entries and the counters), change the clock and use any weak key. Deleting the marks reopens replay of old contents and rollback until the device sees a fresh list; deleting entries makes a package not loaded, or, with the marks deleted too, restores an older version the person once approved. | It cannot make an approval where a strong person key is enrolled, so it cannot install anything the person did not approve (A10 covers the person with no phone). The same-user limit is stated in `docs/pool-encryption.md`, section 3, and `docs/assistant-security.md`, "Limits we state plainly". |
| A7 | If every admin key of a pool is lost or taken and the pool cannot act, installers keep an old registry's packages and receive nothing new. | The pool's recovery path (`docs/pools-and-serving.md`, 1.8) and, for a known-bad hash, the project's deny list (slice 7) are the answer. |
| A8 | A device that has not been told of a newer chain acts on the admin set it holds: at the first pin, after weeks offline, or when a source serves an old chain bundle with old contents inside their expiry. A key the pool has removed signs validly there, and a removal or threshold change reaches the device only at its next fetch plus the hold it counts in its own awake time. | The device cannot know what it has not been told. Chain and contents are signed and both name the head, so a mirror can replay only what a past admin set signed, for at most the expiry; "Last checked" shows the age, a second source and the heartbeat (slice 7) shorten it. Nothing short of an always-online authority removes it. |
| A9 | A phone that is replaced or lost disables the packages whose signatures depended on its key, on every installer's device, until a current admin endorses them. | It fails closed and costs availability, not safety. The overlap order of 3.3 avoids it when the old phone still works; a lost phone has none, and endorsement is one tap for all listed packages. A second admin key on a pool of one is the backup nudge of `docs/pools-and-serving.md`, 1.8. |
| A10 | A person with no phone approves with a weak key (a typed yes at a terminal). A process of that account, a compromised agent that can open a terminal included, can approve for itself, so for that person the floors stop an agent steered by text but not a program already running as them. | Same sentence as `docs/assistant-security.md`, "Limits we state plainly". Refusing extension installs without a strong key would remove it and add a phone step for a person with none: the owner's decision. |
| A11 | Two devices can be shown different valid documents (two contents lists at one serial) and neither learns of it unless a device sees both. | Each device records what it saw and refuses a second; devices comparing their records is not built. A signature set at threshold is needed to equivocate, which is a publisher fault, not an outsider's. |

## 10. Slices

Each slice is a checkpoint. The feature is usable by a person only at slice 4: nothing is "done" before a person can add a community
from their own screen and see a package load.

| Slice | What | Tests |
| --- | --- | --- |
| 1. Verifier | `app/poolhouse-node/src/ext/` (`mod`, `bounds`, `manifest`, `contents`, `verify`, `pins`) and the first kind handler, `decision-task` (the closed kind, with no memory or grammar coupling), as pure functions on bytes plus a pinned anchor and a seen file: the chain head and step-in-flight rules (3.4), equivocation, the pin validators. `contracts/extensions/` (schemas, `limits.json`, `vectors.json`). No network, no approval, no UI. | `app/poolhouse-node/tests/ext_verify.rs` (T1 to T3, T6, T7, T10, T14, T16, T17, T18 rules) and `ext_bounds.rs` (T13 byte, depth, key, digit, name, media); `tests/test_extension_vectors.py` |
| 2. Loader | `src/poolhouse/extensions/`: read the fold of `#extensions` entries, check device signature, approval and counter against the recorded marks, re-hash parts, hand `decide.questions` a typed pack, resolve by `<registry id>/<name>` only; mark runs with the manifest hash. | `tests/test_extensions_load.py` (T2 hash change, T5 bare name, T6, T8 hand-written install, T10 path, T19, T20), `tests/test_extensions_kinds.py` (questions bounds, guards cannot reach the set) |
| 3. Approval and install | Node ops `ext_offer`, `ext_install`, `ext_status`, `ext_versions`; the `extension` action kind in the signed challenge; floors `extension_pin` and `extension_install`; the diff and the card text; `seen.json`; install, update, rollback, remove, hold; the registry pipeline and its `Policy`; `extensions.fetch_pin` through `hub.admission.admit`. | `ext_floor.rs` (T8, T11, T12, T20), `ext_fatigue.rs`, `tests/test_extensions_card.py` (T5, T9, T15); `tests/test_extensions_recipe.py` (T18); `tests/test_redteam_extensions.py` and a row in `docs/redteam/coverage-map.toml` |
| 4. The person's path | "Add a community" screen and card in the desktop UI and the phone app, link and QR, the suggestion row and `ext_suggest`, the weekly list, the agent's view of listings and the bench output fence. Driven by hand on a real device before it is called done. | `tests/test_extensions_agent_view.py`; the app tests for the card; a recorded walk-through of adding and updating, with the exact tree and device reported |
| 5. The other kinds | `extraction-schema`, `model-recipe` (the profile layer, lowest, of 2.5). `benchmark` is blocked: the verifier refuses the kind until the plugin interface of `feat/bench-extractbench` is on the development branch and a handler is written and reviewed against it; this document's `benchmark` rows are constraints until then. | `tests/test_extensions_kinds.py` (grammar budget, numeric caps, roles, recipe fields and ranges, scorer list), `tests/test_extensions_recipe.py` (T4) |
| 6. Publishing | `ext_draft` and `ext_publish`: the node drafts the manifest, contents and the bytes to sign with the diff against the previous version; refuses while a step is in flight; the admins sign on their phones; the node verifies and writes the static source tree. An agent runs the command, no key is in a file. | `ext_publish.rs` (threshold, endorsement after a removal, refusal during a step), and a round trip: publish, fetch, add, update |
| 7. Freshness and deny list | The heartbeat key delegated for freshness, the deny list, a second source for a registry. | `ext_verify.rs` (heartbeat cannot add, alter or widen), `ext_deny.rs` (disable-only) |

Costs, as facts: slice 1 is a few Rust files and fixtures (M); slice 3 touches the node's grants and approvals and needs a second
agent's review before it lands (security-adjacent); slice 4 touches two interfaces (L). Slice 1 adds no human step.
