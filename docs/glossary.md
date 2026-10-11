# Glossary

The words Poolhouse uses, and the words it does not. This file is the single source of truth: code, commands,
interface text, error messages, documents and the public API use these words, and `tests/test_glossary.py`
fails when a banned word appears where a person or an agent reads it. The only places a banned word may stay
are listed, each with its reason, in `contracts/glossary-allowlist.toml`; `scripts/nomenclature.py` rewrites a
tree to this vocabulary and `scripts/nomenclature.py --dry-run` lists what it would change by category.

Poolhouse pools devices. Devices join a pool, the pool's compute is pooled, and each project has one board.

## The words

| Word | Means |
|---|---|
| **pool** | The set of devices that work together. A device alone is a pool of one, and a device may belong to several pools. There is no other kind of pool: no home, dev or prod pool, and no "cluster" or "fleet" for it. |
| **device** | A machine running the node. |
| **member** | A device in the pool. Every member is a device; a device is a member of the pool it joined. |
| **node** | The daemon that runs on a device (the `poolhouse-node` program). The node is software; it is never the machine. Say device for the machine. |
| **join** | What a device does to enter a pool. The pool's join policy decides how: `open` joins automatically on the same network; `secure` needs approval with a short join code. |
| **join code** | The short code a person reads from a member to a new device to approve a join under `secure`. It is checked by a password-authenticated exchange (SPAKE2). |
| **add a device** | What a person does to bring a device into a `secure` pool: ask a member for a join code and give it to the new device. |
| **remove** | Take a member out of the pool. A removed device is refused at its next request. `revoke` stays only as a verb on keys, tokens and grants (a key is revoked), and `revoked` stays as the recorded state of a removed member; `left` is the recorded state of a member that left by its own act. |
| **board** | One per project: the replicated, signed entries every member of the pool holds a copy of. |
| **entry** | One signed record on a board. A device keeps its own entries in its log and a verbatim copy of every other member's. |
| **log** | A device's signed, append-only run of entries. |
| **session**, **agent** | A running coding agent. A session or an agent **registers** its identity on a board; register is used for that and nothing else. |
| **project** | A repository's work, with its own board. |
| **lease** | Permission to use a limited resource (the GPU, a test slot, a port) for a time. |
| **capacity** | What a device can offer the pool: memory, accelerator, free slots. |
| **sentinel**, **flags** | The open-join safety: what the sentinel watches and the flags it raises when a pool takes devices without a code. |
| **enrol** | Used only inside membership and audit records ("enrolled", the recorded way a member entered). A person never enrols a device; they add one. |
| **source** | A place a model file or release can be fetched from: a member, the Hub, a URL. |
| **peer** | Only in network-protocol text: the other end of a connection, which may not yet be a member. Everywhere else, say member or device. |
| **admin** | A person key that governs a pool: it is named in the pool's charter, with its own acceptance, and signs the acts that change the pool. A device is never an admin. |
| **charter** | The signed document that names a pool's admins, thresholds, holds and join policy, or a shared board's pools. The pool id is the hash of the first one. |
| **strong key**, **weak key** | A person key whose every signature needs a presence the signing process cannot supply (a phone's biometric, a hardware key's touch), or one that does not (a key a process of the account can use). Only strong keys count toward a removal or a change of admins. |
| **check words** | Three words made from a fingerprint or a key exchange, for a person to compare on two screens. |
| **hold** | The delay before a risky act takes effect, counted on each device from the first moment it had the whole act, in which any strong admin cancels it. |
| **dispute** | The pause of the acts that change who runs a pool when two admins' acts conflict. |
| **delegate** | A device an admin lets sign `admit` for a time, at a limited rate; it exists only where its key is held by the keystore. |
| **backup** | A second admin key, so that losing one phone does not lock the pool. |
| **reissue** | Make a new pool with another policy and carry the kept members to it; a pool's join policy never changes in place. |
| **handoff** | The signed act that reissues a pool. |
| **offer** | A device lets a pool's members use a named model. |
| **share** | A person-signed charter by which pools hold one board. |
| **roll** | The devices of one pool that hold a shared board for the other pools. |

## The banned words

These words do not appear in command names, flags, help, output, interface strings, error messages, the README,
the public API, documents, identifiers or file names. The allowlist names every exception and why.

| Banned | Say instead |
|---|---|
| pair, pairing, paired, unpair, re-pair, pairing code | join, joining, joined or member (the thing), join code (the code), add a device (the act of a person) |
| cluster | pool |
| journal (of a board) | log |
| mesh | board replication; the replica a device keeps of a board |
| host board, remote board | the board (a board is the same on every member that holds it) |
| project board, as a kind of board | board (every board belongs to a project) |
| home pool, Dev pool or Prod pool, Dev cluster or Prod cluster | the pool |
| seat | session |
| peer, where a member or device is meant | member, device |
| revoke, in text a person reads | remove (a member), or "revoke" only for a key, token or grant |
| node, where a device is meant | device |

Two pieces of design text may name the cryptographic handshake: "the join-code handshake (SPAKE2)". It is never
called pairing.

## Where the words are not enforced

Not scanned: `CHANGELOG.md` (it keeps the names a release had), this file, history and dated notes
(`HANDOFF.md`, `docs/notes/`, reports and measurements, which record what was true when they were written),
vendored and third-party files, and the names a cryptographic library gives its own types (the `spake2` crate).
Ordinary English is not banned: "a pair of values" in code that has nothing to do with membership is allowed,
and those files are named in the allowlist.
