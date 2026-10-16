# Extension registry: signed, data-only packages that a pool publishes

Design, 2026-10-16. Nothing here is built. It specifies how a community, which is a pool, publishes packages of data (benchmarks,
closed-question decision tasks, extraction schemas, model recipes) that people in other pools install, with the verifier, the
approval, the state and the attack-first answers. It is the extension policy that `docs/assistant-security.md`, "Order of work", puts
before any extension mechanism exists. It reuses what is built or designed: the signed file list (`src/poolhouse/fleet/onboard/manifest.py`),
the pool's charter and acts (`docs/node.md`, "Who governs a pool"; `docs/pools-and-serving.md`, section 1), the node's approval and
fatigue defences (`docs/phone-approval.md`), the invariants for an agent steered by text (`docs/compromised-agents.md`, I1 to I9) and
the guarded download (`src/poolhouse/net/download.py`).

Code plugins are out of scope (section 1.3). The benchmark kind is specified by its constraints only: the plugin interface of
`feat/bench-extractbench` had no commit beyond `0.3dev` when this was written, and this design does not depend on it landing.

## 0. The decisions, one line each

| # | Decision |
| --- | --- |
| D1 | An extension is data for a handler that ships in the wheel. A package carries no code, no template with logic, no expression, no reference to another file or host, and nothing a loader evaluates. |
| D2 | A registry is a pool. Its id is the pool id; its publishers are the pool's strong admin keys under the pool's `sensitive` threshold; a person who publishes alone publishes from a pool of one. |
| D3 | Two signed layers: a **manifest** per package version and a **contents** list per registry (serial, expiry, yanks). Both are threshold-signed canonical JSON under their own domain string. |
| D4 | The device's node verifies, writes every word of the approval card and records the install. A fetcher, a mirror or an agent supplies bytes and nothing else. |
| D5 | An install is a person's signature on the exact manifest hash. `extension_pin` and `extension_install` are floors: no standing grant, agent, rule or text covers them. |
| D6 | An agent may suggest a package. A suggestion is a quiet card the person opens on their own screen. It is never an approval, a push or an install. |
| D7 | Adding a community is one approval that pins the registry and installs the first package. The community's key is shown as check words. |
| D8 | An update never switches itself. The installed version keeps working until the person approves the diff. Updates are listed and approved as one weekly batch; a package that asks for more is asked alone. |
| D9 | A lower serial than one already seen is refused from the network. Going back is the person's own signed act naming an older, unyanked manifest hash. |
| D10 | Phase 1 has no dependencies between packages. Every reference names `(registry id, name, manifest hash)`, never a bare name. |
| D11 | Authority is in signed entries on the `#extensions` channel. The node's `seen` files are anchors. The graph store holds a catalogue and run provenance that are derived and rebuildable. |
| D12 | Limits are part of the format and are checked before parsing: sizes, counts, depth. There are no archives, no compression, no YAML, no regular expressions, no schema references. |
| D13 | A community recipe ranks below the machine's own measurements and below the person's settings. Nothing in a package outranks the broker's admission. |
| D14 | A local, unsigned file is an ordinary file a caller hands to code. Name resolution never falls through to it. |
| D15 | A signer removed from the pool loses the versions only it signed until a current admin endorses them, and the installed pointer falls back to the last version with a valid signer. |

## 1. What an extension is

### 1.1 Four kinds

A **kind handler** is code in the wheel: it owns a JSON Schema in `contracts/extensions/`, a bounds table, a differ that writes the
plain-words diff, and the typed object it hands to the library. A package supplies data for one handler. A kind this build has no
handler for is listed ("This needs a newer Poolhouse") and cannot be installed.

| Kind | The package carries | The library reads it through | Declared capabilities it may use |
| --- | --- | --- | --- |
| `decision-task` | Named closed questions (`noul`, `choice`, `score`) with their option texts, and optionally labelled cases | `poolhouse.decide.questions` (`noul`, `choice`, `score`), `poolhouse.decide.cases` | `decide.questions` |
| `extraction-schema` | A schema in the restricted subset of 2.3, the instructions string and the prompting fields of `Prompting`, optional example messages | `poolhouse.extraction.Prompting`, `contracts/extraction.schema.json` | `extract.schema` |
| `model-recipe` | The model file as a pin (`decide.fetch.Pin`: repository, revision, filename, SHA-256, size) and a profile record for named workloads: typed serving fields, request fields, the evidence the publisher claims | `poolhouse.serve.profile` as a layer between `poolhouse/data/profiles.json` and the machine's own `~/.poolhouse/profiles.json` | `serve.recipe`, `model.pin` |
| `benchmark` | A loader choice from a closed list, its field map, the cases as a part, and a scorer chosen from the closed list with typed parameters | the benchmark interface in `src/poolhouse/bench` (to be adopted from `feat/bench-extractbench`) | `bench.run` |

A **capability** is a statement of what the handler may do with the data. The handler enforces it through typed fields, so the
declaration is checked against the kind, not trusted. A package declares exactly the capabilities its kind needs: an extra one, a
missing one or an unknown name is refused. The vocabulary is closed and small so that a widening is visible.

| Capability | What the person is told |
| --- | --- |
| `decide.questions` | "Adds questions that your decision models answer." |
| `extract.schema` | "Adds a shape that your models fill in when they read documents." |
| `serve.recipe` | "Suggests how to serve the models it names (memory and speed settings). Your own measurements win." |
| `model.pin` | "Names model files by exact checksum. Fetching one goes through the usual model checks and asks as it always does." |
| `bench.run` | "Adds a test you can run against your models. It runs only when you start it." |

None of them reads files, memory or the network, writes outside the installed store, or runs a program. Installing a package runs
nothing: a benchmark runs when a person or an agent with the grant starts a run, and that run takes the lease like any other.

### 1.2 What a package may not be

A package may not carry or imply: an archive, a compressed part, a symlink, a path, YAML, a pickle, HTML or Markdown that is rendered,
a template language, a regular expression, a JSON Schema `$ref`, `pattern` or `format`, a scorer written as an expression, a judge
model for a scorer, a free-form command line or environment, a URL the loader fetches, or a reference to another package. Each is a
refusal in the verifier with a one-sentence reason (section 9, T13).

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
    "epoch": 3,
    "charter": "<hash of the charter the signers followed>",
    "capabilities": ["decide.questions"],
    "pins": [ { "repo": "Qwen/Qwen3.5-2B-Base", "revision": "<40 hex>", "filename": "model.safetensors",
                "sha256": "<64 hex>", "size": 4213224 } ],
    "parts": [ { "name": "questions.json", "media": "application/json", "size": 18211, "sha256": "<64 hex>" } ],
    "title": "Support ticket triage",
    "says": "Twelve questions for sorting incoming tickets.",
    "changes": "Adds two questions about refunds.",
    "at_ms": 1790000000000 },
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
- **`epoch` and `charter`** name the pool charter the signers followed (2.4). A manifest is refused when its signers are not
  strong admins of that charter.
- **Pins** have the fields of `poolhouse.decide.fetch.Pin` (repository, full revision, filename, SHA-256, size), the shape the
  released deciders are already pinned in (`poolhouse/decide/pins.py`). A pin names a file; it never carries one.
- **Algorithms** are Ed25519 and a phone's P-256 key, picked from the key as in `gov/keys.rs`, never from a field of the manifest.
  Any other is refused.

### 2.2 Contents

The **contents** of a registry list its current packages. It is the freshness, rollback and yank layer.

```
{ "contents": { "v": 1, "registry": "<pool id>", "serial": 41, "epoch": 3, "charter": "<hash>",
                "issued_ms": 1790000000000, "expires_ms": 1792592000000,
                "packages": [ { "name": "support-tickets", "kind": "decision-task", "serial": 7,
                                "manifest": "<sha256>", "capabilities": ["decide.questions"] } ],
                "yanked": [ { "manifest": "<sha256>", "why": "unsafe" } ] },
  "signatures": [ ... ] }
```

Signed under `poolhouse/extension-contents/v1`, so a signature over a manifest, an act or an approval is not a signature over a
contents list (T3). `serial` rises with every contents. `yanked.why` is one of `bug`, `wrong-data`, `unsafe`, `withdrawn`. The node
keeps the highest serial it has seen per registry and refuses a lower one. Contents last 30 days by default. A device that has
not seen fresh contents installs and updates nothing from that registry and keeps what it has working (T14). A short-lived
**heartbeat** signed by a delegated device key would renew freshness without a tap each week; it is slice 7 and is not needed
before.

### 2.3 Bounds

The format carries its limits. The numbers are starting values in `contracts/extensions/limits.json`, read by both the Rust node and
the Python loader, so there is one table. A byte count is checked before any parse; a count or depth is checked while parsing.

| Item | Limit |
| --- | --- |
| Manifest | 64 KiB, nesting depth 8, at most 64 parts, 16 capabilities, 8 pins |
| Contents | 1 MiB, at most 256 packages, 256 yanks |
| Chain bundle (founding act, admin-set acts) | 256 KiB, as handed to a joiner |
| A part | 16 MiB; at most 64 MiB for a package |
| Media types of a part | `application/json`, `application/x-ndjson`, `text/plain` and nothing else |
| JSON everywhere | duplicate keys refused at every depth (`poolhouse.json_objects.unique_object`), non-finite numbers refused, nesting at most 16, strings at most 64 KiB, arrays at most 100,000 |
| `decision-task` | at most 512 questions; text at most 2 KiB; at most 32 options of 80 characters |
| `benchmark` | at most 10,000 cases of 16 KiB; scorer and loader from the closed lists; at most 1,000 items in one answer |
| `extraction-schema` | schema at most 32 KiB, nesting at most 6, at most 64 properties an object, enums at most 256; keywords from an allow list (`type`, `properties`, `required`, `items`, `enum`, `minimum`, `maximum`, `minLength`, `maxLength`, `minItems`, `maxItems`, `description`) |
| `model-recipe` | every field typed with a range (context, slots, draft depth, cache type from the `DEFAULT_KV_CACHE` family, sampling); at most 8 models; no pass-through of server arguments |
| Fetch | `Content-Encoding` other than identity refused; a part is cut off at its declared size and refused |

### 2.4 Content-addressed storage

Parts are stored once, by hash, under `~/.poolhouse/extensions/blobs/<sha256>` (directory 0700, files 0600), written to staging,
checked for size and hash, and promoted in one step (`poolhouse.net.download` already does this). A blob is read only through an
installed record that names it, and the loader hashes it again on every load.

## 3. Publishers, registries and trust

### 3.1 A registry is a pool

The identity of a registry is the **pool id**: the first 16 bytes of the SHA-256 of the epoch-1 charter with the keys' acceptances
left out (`docs/node.md`, "Who governs a pool"). A person pins a registry by that id. The admins who may sign for it are read from the
pool's charter chain, which travels with the registry as a **chain bundle** (the founding act and the admin-set acts in effect, at
most 256 KiB, the same bundle a joiner is handed) and is verified by the same code that verifies a joiner's. The registry therefore
follows the pool: an admin added, removed or rotated by the pool's own acts changes who may publish, with nothing to keep in step.

- **Who may publish.** A manifest and a contents list need signatures from distinct people (`unit: person`) holding **strong**
  admin keys, in number the pool's `sensitive` threshold (1 while the pool lists one person, 2 once it lists two). A weak key counts
  for nothing. A pool of friends where two people must tap to publish pays that for every publish; a separate `publish` threshold in the
  charter is a later change if it proves too heavy. The signer's tap is the phone's biometric or a hardware key's touch, which an
  agent's process cannot supply.
- **A person who publishes alone** makes a pool of one. There is no second kind of publisher, no standalone publisher key and no
  list of trusted publishers shipped in the wheel. The project's own packages, if any, are one more pool a person adds.
- **A pool that is reissued** (`handoff`) has a new pool id and so is a new registry. Installed packages keep working, updates from
  the old registry stop, and the new one is added by the same one-tap approval, which shows the new admins as check words. Nothing
  follows a move by itself.
- **Names inside a registry are the pool's to decide.** A name is unique within a registry. Two registries can both carry
  `support-tickets`; they are different packages (T6).

### 3.2 How a person pins a registry

The person is given a registry id and a place to fetch from: a link or QR that a member of the community shares (`Share this
community`), or a card the pool's own members see. Nothing else is needed. The pin is not a list the person edits. It is part of the
first approval (5.1): the person sees the community's name quoted as untrusted text, its admins as three check words each, how long it
has existed, and the first package. Their approval pins the registry id and the chain anchor together.

A **source** is any place that serves the registry as static files: a member over the node's file transfer, a URL, a Hub dataset
repository. Which source served the bytes is irrelevant to trust, because contents, manifests and parts are signed or addressed.

```
contents.json            the latest signed contents
chain.json               the chain bundle
manifests/<sha256>.json  each manifest, with its signatures
blobs/<sha256>           each part
```

The first approval also approves the source's host for this registry's fetches, scoped to those hosts and to the registry's
content-addressed objects. A different host for the same registry is a new line on the next approval. This is the person's
approval of a network host (`docs/assistant-security.md`, "Human-only floor"), given on the same screen.

### 3.3 Signer removed

A pool that removes an admin (`admin_remove`) changes the charter epoch. A device that learns of the new epoch
checks every installed version against it. A version whose signatures all depend on a removed key is **disabled until a current
admin endorses it** (a signature on the same manifest hash under the current epoch). The installed pointer falls back to the newest
installed version that has a valid signer; the device keeps the two latest installed versions of each package for this reason.
The community's admins endorse the old versions that were good in one tap on the phone ("3 packages were signed by a key you
removed. Sign them again?"). That cost falls on the publisher, once per admin change.

## 4. Verification: the node, not the agent

The node verifies, in Rust (`app/poolhouse-node/src/ext/`), because the approval card is made of fields the node checked and
a Python process that an agent started cannot be that authority (`docs/compromised-agents.md`, U1 and I2). Whatever moves bytes
(the guarded download, a member's file transfer, a mirror, an agent) hands them to the node and is trusted with nothing.

The checks, in order. The first failure refuses the offer with one plain sentence the agent can relay.

1. **Size before parse.** Every document is within its byte limit; a part is within its declared size.
2. **Parse strictly.** Duplicate keys, floats in a signed body, nesting beyond the limit, a field nobody defined and an unknown
   `v`, `kind`, `alg` or capability are refused.
3. **Chain.** The chain bundle verifies from the pinned anchor (the pool id) with the code that verifies a joiner's chain. The registry's epoch is not lower than
   the highest the device has seen for it.
4. **Signatures.** The contents and the manifest each carry signatures from distinct strong admins of the charter they name, in number
   the sensitive threshold of that charter, over their own domain strings. Each signer is an admin at the device's latest known epoch
   (3.3).
5. **Freshness.** The contents are within `expires_ms` (skew allowed), and their serial is not below the highest seen.
6. **Binding.** The manifest's hash is listed in the contents with the same name, kind, serial and capabilities. The manifest's
   `registry` is the pinned registry. `serial` is above the installed one for an update, and `prev` names it. The manifest is not
   yanked.
7. **Parts.** Each part's size and hash match the manifest, its name passes `poolhouse.safenames.safe_filename`, its media type is on the
   list and its bytes parse under the kind's schema within the kind's bounds.
8. **Capabilities.** The declared set equals the kind's required set. A pin's repository name passes `hub.publishers.valid_name`;
   a pin from a publisher that is not known, or whose name carries a marker, is not blocked here and asks when the model is fetched,
   as today (`hub/publishers.py`).
9. **Diff.** The node computes the diff against the installed version with the kind's differ (5.2).

The node then holds the offer, makes the card and waits for a person. After approval it writes the install, once, from the bytes it
already verified, and the blobs it promotes are hashed again as they are written. The approved manifest hash is the only thing an
install can use, so nothing can change between the tap and the write (T2).

The Python library has no verifier of its own. Loading (`src/poolhouse/extensions/`) reads the installed set (section 7), checks that
each entry is signed by the device's key and carries an approval that verifies under an enrolled person key, hashes every part again,
re-applies the bounds and hands the kind handler a typed object. A tampered or missing part makes that package "not loaded", with the
reason, and nothing else.

## 5. Install, update and the diff

### 5.1 Two ways in, one approval

1. **The person starts it** on their own screen: "Add a community" in Settings, a shared link or QR, or a card in the list the pool's
   members see. The screen fetches the bytes with the guarded download and passes them to the node (`ext_offer`). The card is the
   approval.
2. **An agent suggests it.** The agent calls `ext_suggest` with a registry id, a name and a source hint (at most 200 characters).
   The node writes a row in the person's quiet list: "Your assistant suggested the benchmark 'support-tickets' from a community.
   Nothing is installed." It is not a push, it is not an approval, and it fetches nothing. The person opens the row, which does what
   path 1 does. A suggestion carries no note from the agent beyond the agent's 160 characters shown as "the assistant says (not
   checked)". An agent that has been told by the person's own words, verified by the node (`docs/compromised-agents.md`, U3 and the
   `matches` verdict), to add that community gets its suggestion in the normal lane; while that evidence source does not exist every
   suggestion is quiet, and the agent says where to look.

There is no agent call that creates an install, pins a registry or produces an approval prompt. `ext_install` takes an approval id
and nothing else, and the node refuses it unless the approval's signature verifies under an enrolled person key and its challenge
names this device, this manifest hash and this operation.

### 5.2 The card

The card is made by the node from checked fields, in plain ASCII, one fact a line. It follows `docs/phone-approval.md`, "The
plain-words text", and its challenge is the signed object of "The signed challenge" with a new action kind:

```
"action": {"kind": "extension", "op": "pin_install" | "install" | "update" | "update_batch" | "remove" | "unpin" | "rollback",
           "registry": "<pool id>", "items": [ {"name", "serial", "manifest", "from": "<manifest or empty>", "capabilities": []} ],
           "source_hosts": ["..."] }
```

```
FLAG:   <first: a key younger than 30 days; a name like one you already have; a widening; a signer endorsed after a removal>
RISK:   SENSITIVE (a community you have not used) | ROUTINE (an update that asks for nothing more)
WHO:    The community 'ticket-triage' (its own name, not checked). Admins: maple-river-ten, tide-oak-nine. Founded 2026-09-02.
ASK:    Add the decision questions 'support-tickets', version 7.
GIVES:  12 questions that your decision models answer. It runs nothing by itself.
NEEDS:  No other permission. It names no model files.
SIZE:   18 KB in 1 file.
CHANGED (update only): 2 questions added, 1 changed, 0 removed. Permissions unchanged. Model files unchanged.
SAYS:   The community says: "Adds two questions about refunds." (not checked; the manifest's `changes` on an update, its `says` on a first add)
CANNOT: Read your files or memory, reach the network, change your settings, or run anything unless you start it.
EXPIRES: This request ends at <time> UTC.
```

The node also writes the `SAID:`, `VERDICT:` and `TAINT:` lines of that template, which are always present. `FLAG:` and `RISK:` are the node's. The diff behind `CHANGED:` is made by the kind's differ from the two verified versions: questions added,
changed and removed by name, scorer and loader changes with old and new values, schema fields added, removed or retyped, recipe
fields with old and new numbers, pins added or changed, capabilities gained or lost. A change beyond twelve lines shows a count and
opens in full on the details screen, which the node also makes. A publisher's words appear only on the `SAYS:` line, quoted and
labelled, and never in the sentence that says what is asked.

An approval is a signature on the card bytes, so what the person saw is what was signed. `approve_repeat` applies when the person
declined the same manifest hash before. A declined manifest hash is not offered again; a newer serial is offered.

### 5.3 Updates

- The node refreshes the contents of each pinned registry when the device is idle and at most daily. An update is an offer waiting in
  the list. **Nothing switches version** until a person approves, and the installed version keeps working meanwhile.
- A **weekly list** gathers the updates that ask for nothing more (same capabilities, same kind, same pins, same signers): "4
  updates are ready. [Update all] [Look first]". `Update all` signs one `update_batch` challenge that lists each name, version and
  one-line diff by manifest hash, up to eight lines and a count; the full diff of each is one tap away. The list makes at most one quiet
  notification a week.
- A package that **asks for more** (a new capability, a new or changed pin, a new signer or a key younger than 30 days, a changed
  kind) is a card of its own, never in a batch.
- The fatigue defences of `docs/phone-approval.md`, "Approval fatigue", apply to suggestions and cards unchanged: one item per
  request, backoff, holds, a push budget, no consent by silence. An update that nobody answers waits and expires from the list
  without effect.

### 5.4 Going back, removing and holding

- **Back** (`rollback`) names an older manifest hash that is still listed, unyanked and signed validly; the card says OLDER. It is the
  only way the installed serial falls (T2).
- **Remove** takes a package out and keeps its blobs for the two-version fallback until the person clears them. **Stop using this
  community** (`unpin`) disables every package of the registry and stops refreshing it.
- **Keep this version** (`hold`) stops offering updates to a package until the person lifts it.

## 6. Taking things back

| What | Who does it | What a device does |
| --- | --- | --- |
| Yank a version, `why: bug` or `wrong-data` | the community's signers, in a new contents list | Lists "the community withdrew version 7"; keeps it loaded until the person moves; the update card offers the fixed version when there is one. |
| Yank a version, `why: unsafe` | the same | Does not load the version at the next refresh, falls back to the previous installed version if it has a valid signer, tells the person once with [Remove]. |
| Revoke a signing key | the pool, by its own admin acts (`docs/node.md`, "Who governs a pool") | The next epoch makes the key's versions wait for an endorsement (3.3). |
| Stop using a community | the person | `unpin`. |
| A known-bad hash or registry | the project's release pool, in a signed deny list (slice 7) | Disables only. A deny list can never install, enable or widen anything, so its compromise can disable packages and do nothing else. |

A yank and a key removal reach a device only when it fetches fresh contents. A device that cannot reach a source keeps what it has
and says so ("Last checked 12 days ago"); once the contents it holds expire it takes no new install or update from that registry.

## 7. Where state lives

| State | Where | Why there |
| --- | --- | --- |
| Pins, installs, updates, removals, holds, rollbacks | Entries on the `#extensions` channel of the board `pool`, each signed by the device's key and carrying the person's approval object, as a grant carries one (`docs/node.md`, "Standing grants"). Whether other members copy the channel follows the board's sharing and is settled in slice 3; nothing depends on it, because a device trusts only entries that name its own certificate | Signed, append-only, verifiable by any device that holds the person key; the installed set is the **fold** of these entries, never a directory listing. |
| High-water marks: the highest contents serial and epoch seen per registry, the freshest contents expiry, declined hashes | `extensions/<registry id>/seen.json` in the node's state, written only by the node | A tiny security cache the node reads before anything else, like `pools/<id>/seen.json`. It records what was fetched but never installed, which no entry would. |
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
| Add a community | Opens a link or card a friend shared, reads three check words and a plain card, approves | One approval for pinning, the source host and the first package |
| An update with the same permissions | Presses "Update all" once a week, or ignores it | Nothing breaks if ignored |
| An update that asks for more | Approves on its own card | The card says what is new |
| Something withdrawn as unsafe | Reads one line | It stops loading by itself |
| Publishing (a community admin) | Signs on the phone, as for any admin act; the card shows the node's diff against the last version | One tap per admin per publish |
| Leaving a community | Presses "Stop using" | Its packages stop loading |

Sentences that work as written: "Add the benchmarks from the ticket-triage pool." (the agent asks for the link, makes the suggestion and says
"Look for the ticket-triage card in your list"), "What have I installed from other pools?", "Publish my question set to my pool.",
"Go back to the last version of the support questions.", "Stop using the ticket-triage pool's packages." The agent's refusals use `say`,
`needs` and `then` of `docs/pools-and-serving.md`, section 2, for example "That version is older than the one you have; tap to go
back on purpose."

Friction audit (`AGENTS.md`, "Security without friction"): signature, chain, binding, bounds, freshness and the diff are invisible.
The visible costs are one approval to add (the thing prevented is a signature the person did not see), one weekly list, and a
card when a package asks for more. Updates that ask for nothing more cost one tap a week, never one per package.

## 9. Attack first

Written for this design before it is built. For each case: the defence, the test that fails without it, and the status. "Fixed" means
the design closes it; "Accepted" is a risk named for the owner in 9.2. Test names are proposals in the files of section 10; each test
must be seen to fail with the named line removed (`AGENTS.md`, "Commit before you mutate").

| # | Attack | Defence | Test that fails without it | Status |
| --- | --- | --- | --- | --- |
| T1 | **A copied or cloned registry or pool.** Someone copies the registry's files to their own source, clones a member's state directory, or founds a pool with the same name and other admins. | The registry id is the pool id, the hash of the founding charter, so a pool with other admins has another id and no pin. A copy of the files at a mirror is the same bytes with the same signatures and gains nothing. A cloned member holds no admin key (strong keys live on phones), so it signs nothing. Installs name the device certificate, so a cloned install store is "not installed" on the copy. | `ext_verify.rs::registry_with_other_admins_has_another_id_and_no_pin`, `::mirror_copy_verifies_identically`, `::install_entry_for_another_certificate_is_not_loaded` | Fixed |
| T2 | **Replay or rollback.** An old, validly signed version or contents list is served again to bring back a bug, hide a yank or undo an update; or bytes change between the tap and the write. | Highest contents serial and epoch per registry in `seen.json`; a manifest's serial must exceed the installed one and `prev` must name it; a yank is final for that hash; the approval binds the manifest hash and the node installs the bytes it already verified; the loader re-hashes. The installed serial falls only by a signed `rollback` that says OLDER. | `ext_verify.rs::lower_contents_serial_is_refused`, `::old_manifest_does_not_replace_a_newer_install`, `::yanked_hash_is_not_offered`, `::bytes_changed_after_approval_are_not_installed`; `tests/test_extensions_load.py::test_changed_part_makes_package_not_loaded` | Fixed |
| T3 | **A forged or below-threshold signature.** One valid signature at threshold two; a forged signature; a weak key; an admin signature taken from another document (a pool act, an approval, a contents list used as a manifest). | Distinct strong admins at the charter's `sensitive` threshold, each verified over the domain string of that document type (`poolhouse/extension/v1`, `poolhouse/extension-contents/v1`); weak keys and a repeated signer count once or not at all; the algorithm is read from the key. | `ext_verify.rs::one_of_two_signatures_is_refused`, `::weak_key_does_not_count`, `::repeated_signer_counts_once`, `::pool_act_signature_is_not_a_manifest_signature`, `::manifest_signature_is_not_a_contents_signature` | Fixed |
| T4 | **A malicious publisher.** A pinned community publishes a package whose data is harmful as data: a benchmark that rewards wrong answers, questions that bias a decider, a recipe that makes serving slow. | The package is data for a closed handler, so it cannot act. The person sees what it does in plain words and approves a diff on every update. Recipes rank below local measurements (D13); the broker admits against memory whatever a recipe says. A community benchmark's results are labelled with its registry in reports. | `tests/test_extensions_recipe.py::test_local_profile_beats_community_recipe`, `::test_recipe_cannot_exceed_broker_admission`; `ext_verify.rs::update_that_adds_a_capability_is_its_own_card` | Fixed for harm beyond data; Accepted (A4) for misleading data |
| T5 | **A typosquat or look-alike.** `support-ticket` beside `support-tickets`; a community named like a known one; confusable letters in a name. | Names are ASCII `[a-z0-9-]`. The node folds names and community names with the look-alike and leet folding of `hub/publishers.py` (`_fold`) and raises a `FLAG:` on the card when a name folds to one the device already has or to a name in another pinned registry. Community names are untrusted text, quoted, with admins as check words. | `ext_verify.rs::name_with_non_ascii_is_refused`, `::lookalike_name_in_another_registry_raises_a_flag`; `tests/test_extensions_card.py::test_lookalike_flag_is_first_line` | Fixed; the community's own name remains untrusted text (A2) |
| T6 | **Dependency confusion between registries.** A package from a second registry carries the name of one from the first, hoping a reference resolves to it. | Phase 1 has no dependencies. Every reference to a package names `(registry id, name, manifest hash)`; a bare name resolves only inside the registry that published the referring package; local aliases are per device and never appear in a signed document. | `ext_verify.rs::manifest_reference_to_another_registry_is_refused`, `::bare_name_never_resolves_across_registries`; `tests/test_extensions_load.py::test_two_registries_same_name_are_two_packages` | Fixed |
| T7 | **A compromised publisher key.** One admin's phone is stolen and signs a malicious update. | The sensitive threshold (two people once two are listed); a strong key needs the phone's biometric; every installer still approves the diff; a key younger than 30 days raises a `FLAG:`; the pool removes the key and a device disables versions only that key signed until a current admin endorses them. | `ext_verify.rs::version_signed_only_by_a_removed_key_is_disabled`, `::pointer_falls_back_to_previous_valid_version`, `::endorsement_by_current_admin_restores_it`, `::young_key_raises_a_flag` | Fixed with two or more people; Accepted (A1) for a pool of one |
| T8 | **A malicious or compromised agent installs without the person.** It calls the node, writes the installed set by hand, forges a `#extensions` entry, or asks for a grant that covers installing. | No call an agent token makes creates an install: `ext_install` takes an approval id and verifies it under an enrolled person key for this device and this hash. `extension_pin` and `extension_install` are floors, refused by name for any grant. A forged entry is listed as ignored, as a forged `grant` entry is. A hand-written install fails the loader's approval check. `ext_suggest` only writes a quiet row. | `app/poolhouse-node/tests/ext_floor.rs::floor_refuses_extension_install_for_any_grant`, `::install_without_person_signature_is_refused`, `::forged_extensions_entry_is_ignored`; `tests/test_extensions_load.py::test_hand_written_install_is_not_loaded`; `::test_suggest_creates_no_approval_and_no_push` | Fixed |
| T9 | **An agent compromised by injected text in a package description, name or schema.** A `says` or a question text tells the agent to approve something, or a schema description steers the extraction. | Publisher text is capped, printable ASCII and shown only quoted on `SAYS:`; an agent reading a listing gets it inside an untrusted fence, never in the node's sentences. The agent cannot install (T8), so a steered agent gets a quiet row at most. Question texts and schema descriptions reach models whose outputs are typed (probabilities over closed options, or a grammar-constrained shape), and their results are data under the taint rules of `docs/taint.md`. Scorers are deterministic, so case text cannot steer a score. | `tests/test_extensions_card.py::test_publisher_text_appears_only_on_says_line`, `::test_non_ascii_publisher_text_is_refused`; `tests/test_extensions_agent_view.py::test_listing_fences_publisher_text`; `tests/test_redteam_extensions.py::test_injection_in_says_creates_no_grant_or_install` | Fixed for actions; Accepted (A4) for wrong data |
| T10 | **Downgrade from signed to unsigned.** A package with no signature, an old format, a weaker algorithm, or a local file offered in place of a name. | There is no unsigned package type and no setting: a document without valid signatures is a format error, not a lesser class. Only `v: 1` and the two algorithms parse. A local file is an argument of another type; name resolution reads the installed set only, never a path (D14), and run records mark `local`. | `ext_verify.rs::manifest_without_signatures_is_refused`, `::unknown_version_and_algorithm_are_refused`; `tests/test_extensions_load.py::test_name_never_resolves_to_a_path`, `::test_local_run_is_marked_local` | Fixed |
| T11 | **Approval fatigue from many updates.** A community publishes weekly, or an agent suggests constantly, until the person taps without reading. | A batch of updates that ask for nothing more is one weekly tap that lists each name; any update that asks for more is its own card and is never batched; nothing breaks if ignored, so nothing is urgent; a declined hash is not re-offered; suggestions are one item per request, quiet, and subject to the holds and push budget of `docs/phone-approval.md`. | `ext_floor.rs::widening_update_is_never_batched`, `::declined_hash_is_not_offered_again`; `app/poolhouse-node/tests/ext_fatigue.rs::two_hundred_suggestions_are_one_row`, `::updates_make_one_quiet_notice_a_week` | Fixed; Accepted (A5) for a person who taps "Update all" without opening one |
| T12 | **Takeover by whoever is merely present.** Someone at the unlocked laptop, a member of the pool, or the pool's growth in size adds a community or approves an update. | The approval is a strong person key's signature (the phone's biometric); the laptop's weak key cannot sign it where a strong key is enrolled. A member of the pool cannot cause an install on another member's device; a pool recommending a community puts a card in each member's list, never a pin. More members or more time grant nothing. | `ext_floor.rs::weak_key_cannot_approve_pin_when_a_strong_key_exists`, `::member_recommendation_is_a_card_not_a_pin` | Fixed |
| T13 | **A data package that is a vehicle for code execution or resource exhaustion**: a zip bomb, a regular expression that backtracks, a schema that explodes the grammar, unbounded fields, a path in a part name, a template that reaches attributes. | No archive or compression: stored size is real size, and fetches refuse any content encoding but identity. Byte limits are checked before parsing, count and depth while parsing, duplicate keys refused. No YAML, pickle, regular expression, `$ref`, `pattern`, expression language or `str.format`-style template; templates are fixed placeholders replaced by name. Scorers and loaders are a closed list in the wheel. Schemas are bounded, and the grammar the worst allowed schema builds is bounded in time and memory. Part names pass `safe_filename`. Recipe fields are typed and ranged with no argument pass-through. | `ext_bounds.rs::oversize_manifest_refused_before_parse`, `::deep_nesting_refused`, `::duplicate_keys_refused`, `::part_name_traversal_refused`, `::part_larger_than_declared_is_cut_off`, `::archive_and_compressed_media_refused`, `::gzip_content_encoding_refused`; `tests/test_extensions_kinds.py::test_schema_ref_pattern_and_format_refused`, `::test_worst_allowed_schema_builds_grammar_within_budget`, `::test_recipe_server_arguments_refused`, `::test_template_attribute_access_is_not_evaluated`, `::test_scorer_not_on_list_refused` | Fixed |
| T14 | **A freeze or withholding attack.** A mirror serves the last good contents forever to hide a yank, a key removal or a new epoch. | Contents expire (30 days); an expired list allows no install or update from that registry; the device says when it last checked; a second source can be tried; the heartbeat (slice 7) shortens the window to days. | `ext_verify.rs::expired_contents_allow_no_install_but_installed_version_keeps_working`, `::stale_check_is_reported` | Fixed up to the expiry; Accepted (A3) inside it |
| T15 | **A bad first pin.** A link in a chat names an attacker's pool as a friend's. | The card shows the community's admins as check words, to compare with the friend on another channel; the pin and the source host are in one approval, so nothing is pinned silently. An agent's link is a suggestion, so a steered agent cannot make it. | `tests/test_extensions_card.py::test_pin_card_shows_admins_as_check_words` | Accepted (A2) |

### 9.1 What the tests have to be

Each row's tests are written first and watched fail against the parent commit. Tests use real Ed25519 keys (`Signer.generate()` in
Python, `ed25519-dalek` in Rust), real files in `tmp_path` or `tempfile`, and the real node in the integration tests of slices 3 and 4;
they never use the real keystore, home or network (`tests/README.md`). A shared file of golden vectors in
`contracts/extensions/vectors.json` is read by the Rust tests and `tests/test_extension_vectors.py`, so the two languages agree on the
signed bytes.

### 9.2 Accepted risks, for the owner

| # | Risk | Why it is accepted, and what would change it |
| --- | --- | --- |
| A1 | In a pool of one, a stolen phone with its biometric bypassed can publish a malicious data package. | Installers still approve the diff and the package is data. A second strong key or a hardware key (the backup nudge of `docs/pools-and-serving.md`, 1.8) removes it. |
| A2 | The first pin rests on a link the person received, plus check words they may not compare. | Same as joining by a code. A community name is untrusted text. Comparing check words out of band is the defence; nothing else exists without a central authority. |
| A3 | Inside the contents' 30 days, a mirror can hide a yank. | Shortened by the heartbeat. A person can ask for a refresh from another source. |
| A4 | A signed, approved package can still mislead: a benchmark that favours a model, a question pack that biases a decider, a recipe that serves badly. | A signature says who, not that it is true. Run records name the package and community; local measurements win over a recipe. |
| A5 | "Update all" can be tapped without opening any diff. | The batch carries only updates that ask for nothing more. Anything that asks for more is alone. |
| A6 | Malware running as the person can delete the installed set or the `seen` files (denial) or change the clock. | It cannot forge an install: the loader needs a device signature and a person approval. The same-user limit is stated in `docs/pool-encryption.md`, section 3, and `docs/assistant-security.md`, "Limits we state plainly". |
| A7 | If every admin key of a pool is lost or taken and the pool cannot act, installers keep an old registry's packages and receive nothing new. | The pool's recovery path (`docs/pools-and-serving.md`, 1.8) and, for a known-bad hash, the project's deny list (slice 7) are the answer. |

## 10. Slices

Each slice is a checkpoint. The feature is usable by a person only at slice 4: nothing is "done" before a person can add a community
from their own screen and see a package load.

| Slice | What | Tests |
| --- | --- | --- |
| 1. Verifier | `app/poolhouse-node/src/ext/` (`mod`, `bounds`, `manifest`, `contents`, `verify`) and the first kind handler, `decision-task` (the closed kind, with no memory or grammar coupling), as pure functions on bytes plus a pinned anchor and a seen file. `contracts/extensions/` (schemas, `limits.json`, `vectors.json`). No network, no approval, no UI. | `app/poolhouse-node/tests/ext_verify.rs` (T1 to T3, T6, T7, T10, T14) and `ext_bounds.rs` (T13 byte, depth, key, name, media); `tests/test_extension_vectors.py` |
| 2. Loader | `src/poolhouse/extensions/`: read the fold of `#extensions` entries, check device signature and approval, re-hash parts, hand `decide.questions` a typed pack; mark runs with the manifest hash. | `tests/test_extensions_load.py` (T2 hash change, T6, T8 hand-written install, T10 path), `tests/test_extensions_kinds.py` (questions bounds) |
| 3. Approval and install | Node ops `ext_offer`, `ext_install`, `ext_status`; the `extension` action kind in the signed challenge; floors `extension_pin` and `extension_install`; the diff and the card text; `seen.json`; install, update, rollback, remove, hold. | `ext_floor.rs` (T8, T11, T12), `ext_fatigue.rs`, `tests/test_extensions_card.py` (T5, T9, T15); `tests/test_redteam_extensions.py` and a row in `docs/redteam/coverage-map.toml` |
| 4. The person's path | "Add a community" screen and card in the desktop UI and the phone app, link and QR, the suggestion row and `ext_suggest`, the weekly list, the agent's view of listings. Driven by hand on a real device before it is called done. | `tests/test_extensions_agent_view.py`; the app tests for the card; a recorded walk-through of adding and updating, with the exact tree and device reported |
| 5. The other kinds | `extraction-schema`, `model-recipe` (the profile layer), `benchmark` (against the interface that branch lands). | `tests/test_extensions_kinds.py` (grammar budget, recipe ranges, scorer list), `tests/test_extensions_recipe.py` (T4) |
| 6. Publishing | `ext_draft` and `ext_publish`: the node drafts the manifest, contents and the bytes to sign with the diff against the previous version; the admins sign on their phones; the node verifies and writes the static source tree. An agent runs the command, no key is in a file. | `ext_publish.rs` (threshold, endorsement after a removal), and a round trip: publish, fetch, add, update |
| 7. Freshness and deny list | The heartbeat key delegated for freshness, the deny list, a second source for a registry. | `ext_verify.rs` (heartbeat cannot add, alter or widen), `ext_deny.rs` (disable-only) |

Costs, as facts: slice 1 is a few Rust files and fixtures (M); slice 3 touches the node's grants and approvals and needs a second
agent's review before it lands (security-adjacent); slice 4 touches two interfaces (L). Slice 1 adds no human step.
