# A device in several pools

Design, 2026-10-19. Nothing here is built. Read at `bceddf929` (`0.3dev`): the survey is code and document reading; no test was run and no
screen was driven. The owner's order: a device belongs to several pools, and leaving, rejoining and moving between pools are first-class
acts. It reads with `docs/design-pool-keys-and-custody.md`, written in parallel and not on the branch when this was written; the one
thing taken from it is **a key per (device, pool)** (section 2.3 lists the interface this design assumes). It replaces decision M1 and
sections 4.1 and 4.2 of `docs/pools-and-serving.md` (one certificate for every pool) and fills in slice 4 of that document ("Many pools on
one device"); sections 5 to 7 there (capacity, shared boards, serving) stand and are not redone here.

## 0. The decisions, one line each

| # | Decision | Why |
|---|---|---|
| D1 | A device holds up to 16 pools. Each is a **slot** with its own key, charter, acts, roster, members' addresses, join policy and boards. A pool of one is a pool. | The order; the limit keeps memory and beacons bounded. |
| D2 | The listener, the beacon port, the node, the lease table and the person's keys are the device's. Everything a pool can see is the pool's. | One port and one firewall step however many pools; no pool sees another. |
| D3 | **One certificate per pool** (from the pool's key), not one per device. The server picks it from an opaque, short-lived label the dialer puts in the ClientHello's server name; one connection serves exactly one pool. | Two members of two pools who compare notes must not be able to link the device by its fingerprint. Reverses M1. |
| D4 | A board belongs to one pool, fixed when it is made; a project belongs to the pool of its board. A session is in the pool of its board. Nothing a person does picks a pool except the first question for a new project. | No flag for a person with one pool; no way to put a project's entries in the wrong pool by default. |
| D5 | A device with more than one pool and a command that cannot tell which pool it means gets one plain question, never a guess. | A wrong guess discloses a board to strangers. |
| D6 | Leaving is the device's own act, takes effect at once, destroys the pool's key and ends its sessions, and freezes its boards read-only for 3 days before the node deletes them. Being removed does the same, once the device holds the signed removal. | HANDOFF "Release cut": a refused or shut-down pool is deleted after 3 days. |
| D7 | A rejoin is always a new key and a new certificate, so a replayed old `leave` cannot touch the new membership. | Closes the order-of-leave-and-admit hole listed in HANDOFF. |
| D8 | A move is a join of the new pool, then a leave of the old one; never the other way round. | A failed join never strands the device. |
| D9 | A device in a pool never joins another by itself. Only a device in no pool joins an `open` pool it hears. | A broadcast on the Wi-Fi cannot add pools to a device. |
| D10 | Leases stay one table per device; requests gain a `pool` label for accounting. The GPU stays one holder at a time across all pools. | AGENTS.md section 1. |
| D11 | Nothing migrates. A state directory from before this change is set aside with the existing one line and the device starts with no pool. | AGENTS.md: no legacy mode. |

## 1. What a node holds today

Read in `app/poolhouse-node/src/` unless a path says otherwise.

**One pool, hard-wired.** `Node` (`node.rs:61`) holds one `members: Pool` (`membership.rs:134`), one `cert` and `key`, one `facts` (the
members' addresses, kept in a file of the state directory), and `pool: String`, which stays empty because the board heads sign in a domain with no pool id. `Pool::load` reads the
single `pools/<id>/` directory and fails with "this build holds one pool, and this directory holds several" (`membership.rs:160`). With no
directory the device is the **unfounded pool of one**: no charter, no id, no admins, in memory, admits nobody. About 90 places read
`node.members` and about 40 read `node.cert`.

**Why a device that left cannot join another pool.** `Pool::can_join_other` is `gov.is_none()` (`membership.rs:185`). `pool_leave`
(`gov/ops.rs:359`) signs a `leave` act and keeps the `pools/<id>/` directory, so the pool stays governed with this device marked `left`; `pool_create` and
`start_pool` then answer "This device already runs a pool; leave it before making another" (`gov/ops.rs:274`), `adopt_governed` answers "a device that is in a pool does not
change pool" (`gov/ops.rs:414`), `join_pool` the same (`net.rs:372`), and the beacon handler answers the beacon of a pool only for a device with no governed pool (`net.rs:244`). A leave is therefore a one-way
act until the state directory is replaced (HANDOFF, "Known limits of slice 1").

**The network side.** `Net::start` builds one `server_config` from the one identity (`net.rs:125`) and the TLS client check asks one roster for standing (`tls.rs`); the join policy
is a charter attribute of the one pool; the beacon (`beacon.rs`) is one datagram every 5 seconds carrying the pool id and project in the clear, signed with the device key;
`Net.window` is one join window for the node; `attach` and `reverse` connections are keyed by the other member's fingerprint; the sync loop walks the one pool's members.

| Today, per node | Where | After this design |
|---|---|---|
| Device key and certificate; the fingerprint every pool knows | `device.rs`, `cert.rs`, `Node.key`, `Node.cert` | per pool (D3); a device-wide custody secret signs nothing a pool sees |
| Listener (`--listen`, `--network`), beacon port, sync loop thread, join window | `Net` (`net.rs:53`) | stay one each; the window records its pool |
| Charter, acts, roster, join policy, project | `Node.members` (`pools/<id>/`) | per pool, many |
| Members' addresses, reach, builds | `Node.facts` (one addresses file) | per pool (`pools/<id>/addresses.json`) |
| Boards, names, tokens, links | `Node.boards`, `tokens.json`, `links.json` | boards gain a pool; tokens and links stay device-wide, bound through the board |
| Project registry | `projects.json` (256 projects) | rows gain a pool |
| Pool history board | one board named `pool` (`poolops.rs:11`) | one per pool, `pool-<first 8 of the id>` |
| Leases, GPU, models, person keys, grants, approvals, phones | `leases.json`, `persons.json`, `approvals.json`, `phones.json` | stay device-wide; grants and cards name the pool (section 2.5) |
| Shard consent (who may run tests here) | `shard/consent` | per pool (an allow list is a list of that pool's members) |

**Python.** `ph.pool` (`api/pool.py`) and `node_pool.py` read the one `pool_status`; `board/founding.py` answers "already in the pool X" for any
founded device; `node_join.py` joins "a device in no pool"; `device_setup.py` (firewall rule, mirrored networking on WSL, the node, the join check) is
device-wide and needs no change. The legacy `poolhouse-pool` command (`fleet/join.py`, a passphrase-and-group daemon with its own `pool.json`) is the
older mechanism; HANDOFF already lists its replacement by `found NAME | status | members | add-device | join CODE | remove DEVICE | leave`, which is
section 6's command line, and this design does not extend it. `scripts/pool-check` and `tests/pool_check/world.py` give every device one pool.

## 2. The model

### 2.1 Slots

A device has one **local pool** and up to 16 **pool slots**.

* The **local pool** is today's unfounded pool of one. It always exists, has no key, no certificate and no beacon, is never served on the listener, and holds the boards made before
  any pool existed. It is not one of the 16. No pool takes a local board without a person's act: founding lists the boards in its own approval, checked, as
  `docs/pools-and-serving.md` 4.2 already decides; a join lists them unchecked; an open join binds none.
* A slot is `pending` (a founding draft or a join in progress; its key is pending), `active`, `frozen` (left or removed; read-only until a deadline) or
  `gone` (only a tombstone remains, 2.4). A slot is addressed by its id; the person says the **alias**, a name local to the device that starts as the charter's
  name and is made unique ("lab", "lab 2"); aliases are never sent to anyone.
* A device in no slot is exactly today's unfounded device. Leaving the last pool returns to it.
* **MAX_POOLS = 16**, in `docs/limits.md`. Past it: "This device is in as many pools as it may (16); leave one first." Frozen slots do not count.

### 2.2 What is the device's and what is the pool's

| The device's (one) | The pool's (per slot) |
|---|---|
| the node process, its supervisor, the local socket `node.sock`, the runtime | the pool key and certificate (2.3), the charter, acts, roster, refused acts, and the policy |
| the listener port and the beacon port, one firewall rule, one join window at a time | the members' addresses, reach and builds, the sync backoff, `attach` channels |
| the lease table, the GPU, resident models, the serve broker, capacity | the boards bound to it, its history board `pool-<id8>`, the heads signed with its key |
| person keys, grants (each naming its pool when a pool is the subject), the approval inbox and its fatigue ledger, phones | inbound consent and the shard allow list, rates for joins and knocks, the discovery setting |
| the project registry (a row names its pool), tokens and links (a session is in its board's pool) | open-join rate limits, the clone heartbeat |

The rule behind the table: **a value a member of one pool can read must be a value no member of another pool can read.** The device's list holds only
values nobody over a connection sees.

### 2.3 Identity, the listener and the certificate (decision D3)

**Interface taken from the keys document.** A `PoolKey` is an Ed25519 key and a self-signed certificate for one (device, pool). It is made when a pool is
founded or when a join starts (the pool id is not known then, so it is `pending`) and is bound to the pool id when the join completes. It signs that pool's
handshake, its board heads, its beacons, its clone heartbeat and the device's own acts in it (`leave`, a delegate's `admit`). A leave, a failed join, a
learned removal and an expired draft destroy it. Two keys of one device share no public value. The custody secret of the device wraps them and signs nothing a
pool sees. If the keys document differs, this document follows it; what must hold is the rule under the table in 2.2.

**Why not one certificate (M1).** M1 argued that a server cannot know the pool before it shows its certificate, and that separate certificates hide nothing
because addresses and ports are shared. The first is solved below. The second holds only for someone who sees the network. Two members of different pools,
on different networks or on different tailnets, who compare fingerprints link the device with certainty; with a certificate per pool they cannot, and the
cost M1 itself wrote down (a device removed from one pool takes a new identity in every pool) disappears.

**Choosing the certificate.** The listener reads the ClientHello first (rustls `Acceptor`). The dialer puts a **pool label** in the server-name field: a short,
opaque string made from the pool id, a fresh nonce and the minute (a MAC keyed from the pool id, accepted for a few minutes either side). The server checks
the label against each of its slots (at most 16 MACs, always all of them, so the time does not depend on which matches), takes that slot's prebuilt
`ServerConfig` (its certificate and its own client-certificate check against its roster) and finishes the handshake. The label is never the pool id, never the same twice,
and means nothing to anyone who does not hold the id. TLS 1.3 encrypts the server certificate, so an observer sees the label and nothing else.

| The dialer sends | The server shows |
|---|---|
| a label for a slot | that slot's certificate; every later request is for that pool only (a request cannot name another; there is no `ctx` field) |
| no label, while a join window is open | the certificate of the window's pool: a joiner with a code does not know the pool id yet |
| no label, no window, or a label no slot matches | a throwaway certificate made for the connection; the connection answers nothing and closes, in the same time and with the same text for both cases |

`hello` to a certificate that is not a member's answers a `secure` pool's policy and nothing else (no pool id, project or boards), and an `open` pool's what its clear beacon already
says, so a label heard on the wire and replayed teaches no pool id.

**What a member learns of another pool: nothing from the protocol.** `hello`, `boards`, `members`, `acts`, `vector`, `pull` and `push` answer for the
connection's pool and tell nothing of the rest. What stays linkable, named: the **source address and port** (a member of two pools on one network sees the same
address; no design that keeps one listener removes it), the computer's **name** (2.5, open question Q1), the hardware and build the `hello` reports, and timing.

### 2.4 Layout

```
<state>/node.sock, persons.json, approvals.json, tokens.json, leases.json, links.json, projects.json, write-limits.json       the device's
<state>/custody/                                               the device's custody secret (keys document)
<state>/pools/<id>/charter.json, acts.json, seen.json, refused.json    as today
<state>/pools/<id>/key.*, addresses.json, slot.json            the pool key and certificate (keys document), members' addresses, {alias, discovery, state, until}
<state>/boards/<board>/pool                                    the pool id (one line); no file = the local pool
<state>/boards/pool-<id8>/                                     the pool's history board
<state>/pools/retired/<id>/                                    a frozen pool: its charter and acts, its boards (read-only), tombstone.json
```

Frozen boards are hosted from there, read-only. A **tombstone** (at most 1 KiB: id, alias, charter name, the certificate it used, `left | removed | refused | shut-down`, when, the frozen deadline) outlives
the deletion of the rest, 256 at most, oldest first. It holds no key and no entry. A slot whose directory has no key file beside it is not read (section 5).

### 2.5 Choosing a pool

**A person with one pool sees no change.** No flag, no question, no pool name in a card or a sentence (a name appears only where it tells two pools apart), no
extra step in founding or joining. A device in no pool behaves as today, including joining an `open` pool of its project that it hears.

**A session is in the pool of its board** and needs nothing else. `project_resolve {path}` returns the project's board; the project row names its pool
(`projects.json` gains `pool`). So the hooks, `board.session.register` and every workspace command are unchanged. A project belongs to one pool for life: a
board never changes pool, and the same repository is not given a second board in a second pool (the board id is a device-wide name, and a repository's
`.poolhouse/project.toml` pins one). The refusal says so: "This folder's board is in 'lab'. To use it in 'team' as well, work from a second copy of the
repository."

**A new project on a device with more than one pool** picks its pool, in this order, stopping at the first that applies:

1. `--pool NAME` or `POOLHOUSE_POOL`, when given.
2. Exactly one of the device's pools names this repository's project in its charter (`Charter.project`): that pool. No question.
3. Otherwise a plain question, once per project, answered by a tap or a word and stored in `projects.json`: "This folder is new to Poolhouse. Which pool is its
   board for: lab or team?" A hook that cannot ask returns `needs: choice` and the agent asks the person with the question tool; the answer is never defaulted.

**A command that is about a pool** (`status`, `members`, `add-device`, `remove`, `leave`, `move`) takes `--pool NAME` (alias, or the first six hex digits of the
id when two aliases would read alike). With no flag: the one pool when there is one; else the pool of the project of the working directory when the command is
about the project's work (`status`, `members`); else "This device is in two pools, lab and team. Which one do you mean?" as a `needs: choice` refusal (a
`Refusal` in Python, `ph.Denied`) listing the aliases. `add-device`, `remove` and `leave` never take the project's pool silently.

**Grants name their pool.** A grant for a pool-level act (`pool_leave`, `member_remove`, `add_device`, `admin_manage`, `inbound_consent`, `shard_consent`)
carries the pool id as its resource; a grant with `*` for these is refused when it is made. Cards for these acts name the alias whenever the device has more
than one pool. The sentence a person signs under is written by the node from the act's bytes and names the alias, with the first six digits of the id when
two aliases are close, so a pool named "lab" by a stranger cannot read as the person's own.

**The name a pool sees.** A device's name in a pool is the computer's name by default, set per pool at the join and changeable by the owner in that pool.

### 2.6 Compute is shared; pools are not

`leases.json` stays one table. The GPU is one holder at a time across every pool; a test shard from one pool, a model call for another and the owner's own
work queue on the same `gpu` and `cpu_slots` resources, in the order of `docs/node.md` "Leases" and, once built, the fair order of
`docs/pools-and-serving.md` section 5. This design adds three things:

* A lease gets a `pool` label (the board's pool for a session, the connection's pool for a remote request), for accounting and fairness only, never for authority.
* A remote request can name no lease and no holder (it never could); a `busy` answer to a member says only that the device is busy, never whose work it is.
* A resident model is loaded once for the device. Which pools may call it is the offer of each pool (consent off by default, `docs/pools-and-serving.md` L2), and a
  server that answers two pools keeps `--parallel 1` and erases its slot when the next call is another pool's (that document, 7.4).

## 3. Leaving, being removed, rejoining, moving

**Leave** (grant `pool_leave` naming the pool; one tap on a card; the agent only asks). Order, each step idempotent:

1. The node writes a `pool_left` audit row to the pool's history board, then signs the `leave` act with the pool key. The act carries a **cut** of this device's
   own logs in that pool's boards (`{board, origin, seq, hash}`, as a removal does) so that nothing written later under those origins is taken from any relay.
2. It closes the pool's connections, stops its beacon, and refuses its requests; the sessions of its boards end (tokens ended, an `ended` identity row each) and
   the grants and shard consent scoped to the pool are removed.
3. **The pool key is destroyed**, the join code and every secret of the slot with it. The charter, acts and boards stay, read-only.
4. The act is sent once to each member whose address is held, without waiting for an answer; every other member learns it by relay. Nothing waits for a member.
5. The slot is `frozen` until 3 days of this device's awake time have passed (the monotonic clock of `gov/clock.rs`, so a changed wall clock deletes nothing early),
   then the node deletes the boards and the charter, writes the tombstone and the slot is `gone`. The frozen boards can be read the whole time and written
   never. A person who wants a board's entries earlier asks a member of the pool; the entries are on every member's copy.

A leave by the only member of a pool ends the pool: the card says "You are the only device in 'lab'. Leaving ends the pool, and its boards are deleted after
3 days." A device that is an admin's delegate loses the delegation with the key.

**Removed by others.** A device acts on a removal only when it holds the **signed removal** (from any member, by the `acts` op), never on a refused
handshake: a TLS alert says only that someone refused it (`net.rs:103`) and anyone on the path can send one. With the removal in hand the slot is retired exactly as in
steps 2 to 5 (the key is destroyed and nothing is sent, since the pool refuses it). A removal from one pool changes nothing of the device's other pools.

**Rejoin.** The same pool again is a new join: a new key, a new certificate, an admit as for any new device (section 4). Its old certificate stays `left` or
`removed` in the roster for ever; a replayed `leave` or `remove` names that fingerprint and can touch nothing of the new membership. The frozen boards of
the earlier membership are dropped once the rejoin has synced the pool's boards; nothing is lost, since the pool holds a verified copy of every entry the old
membership wrote that reached any member. A device never rejoins by itself a pool that removed it (the tombstone says so), including an `open` pool it hears.

**Move** (`poolhouse-pool move --from NAME CODE`, `ph.pool.move`) is a join of the new pool followed by a leave of the old one. The join runs first, with the
code typed on this device (section 4); only when it has completed does the node ask for the one tap, written from the leave act: "Move this computer from 'lab'
to 'team'? It stops being a member of 'lab'. Entries already copied to lab's other devices stay with them." If the join fails nothing changes. If the leave
then fails the device is in both and says so. Repeating the command after success finds the device in the new pool and out of the old one and reports done.
Projects of the old pool are not moved: a working directory whose board is frozen asks "That project's board was in 'lab', which you left. Use 'team'?" once,
and the answer makes or adopts the project's board in the new pool.

**A pool shut down for compromise** (the act is not built: HANDOFF "Release cut") is sealed instead of frozen: read-only for good, key and grants removed, never
restorable. The 3 days delete only its live state; the sealed record stays until a person deletes it. This design only gives the device side the same
retire routine with that variant.

## 4. Joining a second pool while in a first

1. A member of the other pool opens a join window on its own screen or phone and reads the code to the person (`docs/pools-and-serving.md` 1.6). The person
   tells their agent, or types the code into the app: "join Sam's pool, code 482 913" (`poolhouse-pool join CODE`, `ph.pool.join`).
2. The node makes a **pending key** and runs the join-code exchange (SPAKE2) with both certificate fingerprints as its identities. The fingerprint it shows is the
   pending certificate's, not that of any pool it is in. The pool id arrives sealed in the exchange; the node binds the key to it.
3. The approval is the one the first join already has, unchanged in cost: typing the code on the new device is the approval; a join through an agent adds the three
   check words and one tap (1.6 there). **Already being in a pool adds no ask.** Joining binds no existing board (its boards stay where they are),
   turns on no inbound consent and adds no one to any shard allow list, so the new pool can do nothing on the device that a stranger could not.
4. One plain line comes back: "Joined 'team' as 'Studio'. 'lab' and 'team' stay separate: neither can see the other." The pool's boards then sync.

A join that fails, is refused or is abandoned destroys the pending key. A pending key also expires an hour after it was made. Joining a pool the device is
already in answers `already` and changes nothing; joining a pool whose slot is frozen starts the rejoin of section 3. An open pool is joined only when asked
for by a person or an agent, except by a device in no pool (D9). At most one join window is open on a device; opening one ends another and says so.

## 5. Migration: none

A state directory with a pool in it that has no key file beside it (every pool made before this change) is set aside the way the node already sets aside a pool
of another charter version: moved, never deleted, to `pools-old-format/<id>`, with the existing line "this device was in an old-format pool: it now starts
unfounded; create a new pool". The device starts with no pool; every board it holds is served as before, in the local pool, and is bound by an explicit
person act when a pool is founded or joined. No old pool is read, converted or kept working. The device-wide certificate and key of the old format are not
reused as a pool key.

## 6. What changes, and the slices

Each slice lands alone, with the tests named first, each watched failing against the old code. `scripts/test all <files>`; Rust under `app/poolhouse-node/tests/`.

| # | Slice | Scope | Fail-first test | Needs |
|---|---|---|---|---|
| 1 | **Leave frees the device** (no second pool yet) | `pool_leave` retires `pools/<id>` to `pools/retired/<id>`, ends sessions, adds the cut to the act, writes the tombstone, runs the 3-day sweep on awake time; `can_join_other` becomes "the local pool"; `pool_create` and `adopt_governed` stop refusing a device that has left; the unfounded view returns. | `pools_leave.rs`: found, leave, then found another pool on the same state directory, and, separately, leave then join one (today: "already runs a pool"); the frozen board refuses a write and is gone after the deadline on a test clock; a replayed leave touches nothing. | none |
| 2 | **Pool slots, one at a time** | `pools.rs` (`PoolSet`, `Slot`); `Node.members`, `cert` and `facts` move into the slot; `Hosted.pool` and `boards/<b>/pool`; `pool_list`; `projects.json` `pool`; `MAX_POOLS` constant set to 1. | `pools_list.rs`: `pool_list` answers; a board made while founded names its pool and one made before is local. | 1 |
| 3 | **Pool key, pool certificate, label** | the slot's key and certificate replace the device's in the handshake, heads and beacons; `Acceptor` and the label (`label.rs`), carried in the client's server name (a constant today); the throwaway certificate; one connection, one pool; `hello` to a non-member of a `secure` pool answers the policy only. | `pools_label.rs`: a dial with the wrong label and with none get the same answer in the same shape; a label replayed after its window gets the throwaway; `hello` from an unknown certificate carries no pool id; fingerprints differ per slot. | 2, keys document |
| 4 | **N pools** | `MAX_POOLS` 16; the sync loop and `attach`/`reverse` keyed by (pool, fingerprint); per-pool addresses, rates, window, consent; board ops filtered by the connection's pool; links refuse boards of two pools; second founding and second join. | `pools_two.rs`: two pools on one device never answer for each other's boards, `boards`/`pull`/`push` for the other's board is the same `denied` as an unknown board; a removal's cut naming another pool's board is refused; second join with the code works and is idempotent. | 3 |
| 5 | **Beacons per pool** | one datagram per discoverable pool that has other members; sealed under the id for `secure`, clear for `open`; none for a secure pool of one or `discovery: off`. | `pools_beacon.rs`: sixteen secure pools of one send no datagram; a secure pool's beacon is not readable without its id. | 4 |
| 6 | **Choosing a pool** | `project_resolve` and the new-project question (2.5); `--pool`, `POOLHOUSE_POOL`; `needs: choice`; grants naming a pool and `*` refused; cards name the alias. | `pools_choose.rs`; `tests/test_pool_choose.py`: the question is asked only with two pools; a `*` `pool_leave` grant is refused; the hook returns `needs: choice` and does not block. | 4 |
| 7 | **Leave, removal and move on the network** | the signed-removal path retires a slot; no retire on a refused handshake; `move`; tombstone refusal of auto-rejoin. | `pools_retire.rs`: a forged TLS alert changes nothing; a signed removal retires only that pool; move with a failing join changes nothing. | 4 |
| 8 | **Accounting** | lease `pool` label, `capacity {pool}`, fatigue ledger counts all pools together, per-pool queue share. | `pools_fatigue.rs`: twelve asks spread over three pools stop at twelve. | 4 |
| 9 | **Python and the check** | `ph.pool.*` take `pool=`, `ph.pools()`; `node_pool.py`; `board/founding.py` ("already a pool named"); `board/place.py`; `node_join.py`; `poolhouse-pool` as the canonical command; `pool-check` steps `two-pools-on-one-device`, `leave-and-rejoin`, `move-pool`, `removed-from-one-stays-in-the-other`, `no-link-between-pools`; `tests/node_kit.py` and `pool_check/world.py` take two pools. | `tests/test_pool_two.py` and the steps, on real nodes. | 6, 7 |
| 10 | **Documents** | below. | `tests/test_glossary.py`, `tests/test_documented_env_vars.py`. | each |

Until slice 3 a rejoin of the same pool reuses the device's certificate, which D7 does not yet protect from a replayed `leave`; slice 1's commit message says so, and
slice 3 ends it.

**Modules, Rust:** new `pools.rs`, `label.rs`, `retire.rs`; changed `node.rs` (`Node` loses `members`, `cert`, `key` and `facts` for the pool, `host` takes the pool and the pool's key),
`membership.rs` (`Pool::load` reads many; `can_join_other` goes), `gov/ops.rs` (about 22 uses), `gov/wire.rs`, `gov/logs.rs` (cuts only for the pool's boards), `net.rs`
(`Net.me`, `server_config`, `heard`, `join_open`, `join_pool`, `adopt`, the sync loop), `tls.rs` (per-slot configs, the label), the member-protocol module (the connection's pool), `beacon.rs`, `facts.rs`,
`netsync.rs`, `poolapi.rs`, `poolops.rs` (`pool-<id8>` history), `rooms.rs`, `landing.rs`, `landtrust.rs`, `subs.rs`, `build.rs`, `shard/` (consent per pool), `approvals/request.rs`, `phone/` (the
four reads of `members`), `registry.rs`/`project.rs` (`pool`), `links.rs`, `grants/` (pool in the scope), `lease/` (label). **Python:** `node_pool.py`, `api/pool.py`, `board/founding.py`,
`board/place.py`, `node_join.py`, `fleet/onboard/cli.py`, `workspace` `pool` verbs, `poolhouse-pool`, `tests/pool_check/*`, `tests/node_kit.py`.

**Fixed costs; N pools must not multiply the idle cost.** One listener thread, one sync thread and one beacon thread however many pools: the sync thread walks
the slots and dials members, so its work grows with the members of all pools, as it does today with one. A pool of one dials nobody and, when `secure`, beacons
to nobody. Per slot: a `GovPool` (bounded by its caps), a prebuilt TLS config, and one history board (counted in the 256 of `MAX_BOARDS`, which stays device-wide). Per
handshake: at most 16 MACs. `Node::open` loads every slot eagerly. Bound, by `pools_cost.rs`: a node with 16 pools of one, idle, opens in at most twice the time
of a node with one pool plus a small per-pool allowance measured once and written into `docs/limits.md`, sends no beacon datagram, makes no dial, and a
handshake with a label costs at most the same as without. The new constants (`MAX_POOLS`, the tombstone cap, the 3 days, the label window) go in `docs/limits.md`.

## 7. Attack first

Written by the author; a non-author pass is owed before slice 3 is built (AGENTS.md section 1). Each case holds under the owner's rule that security adds no
visible step to a normal journey.

| Case | What stops it | What remains, named |
|---|---|---|
| A member of one pool probes whether the device is also in another | It needs the other pool's id to make a label; with a wrong or no label the answer is the same throwaway certificate and the same close, in the same shape and time (`pools_label.rs`). Board and member ops answer for the connection's pool only, and an unknown board and another pool's board give the same `denied`. | Whoever knows a pool's id can ask an address whether it answers for that pool; a charter gives every joiner the id, so every former member can (accepted risk, Q2). The source address, the computer's name and the hardware line of `hello` link a device for anyone who sees both pools (2.3, Q1). |
| Two members of two pools compare notes | Their fingerprints, origins, board heads and beacons differ per pool; nothing a pool receives is signed by a value another pool sees. | The same address on one network; the same name. |
| A device used as a bridge between pools | A board has one pool; a connection serves one pool; `boards`, `vector`, `pull`, `push` and `hello` read only that pool's boards; **a link between boards of different pools is refused** (`pools_two.rs`), stricter than the rule in `docs/pools-and-serving.md` 6.6, which only stops a link that carries entries to a pool that lacks the board; a lease answer names no holder; the cut of a removal applies only to boards of the act's pool. | An agent that holds sessions in two pools can carry text between them, as a person can; the node cannot see meaning. Each registration is in its pool's audit. |
| A copied or cloned device | A pool key is wrapped by the device's custody secret (keystore where one exists), so a copied directory starts no node and carries no key. A clone of a device with a file key holds every pool key at once; each pool detects the twin by its own heartbeat (a counter backwards, two boot ids under one fingerprint, `docs/pools-and-serving.md` 1.6) and an admin removes it per pool. A leave's cut stops a clone from extending the log of the device that left. | A weak (file) custody loses every pool of that device together; the device says its custody on `pool_status` as today. |
| A copied pool, or a look-alike named like one of mine | The pool id is the hash of the first charter, so a copy with other admins is another pool; the alias is local and made unique, cards add six digits of the id when two names read alike. | A person who joins a pool by a code a stranger gave them is trusting the stranger; the join approval is theirs. |
| Replay | A label is bound to a nonce and the minute and works for a few minutes; a replayed label shows an observer the pool's certificate and a `hello` that names no pool (2.3); every other op still needs a member's certificate. A replayed `leave` names an old fingerprint (D7). Acts name their pool. | A label replay within the window tells an active observer that the address answers for that pool. |
| A forged or below-threshold signature | A pool key signs only its own pool's device acts; an act for another pool is refused as today; no key of one pool is a key of another. | None new. |
| A compromised or malicious member | It can read and write only its pool's boards. A `remove` it relays carries a cut only for its pool's boards. It cannot fill another pool's slots; slots fill only by the device's own joins. Join and knock rates are per pool. | A member of a pool can spend that pool's open-join rate (5 an hour) and nothing else. |
| A stolen phone or person key | The phone makes a key per pool (`docs/pools-and-serving.md` 1.17); a grant names one pool. | A person key that is admin of two pools is stolen from both; per-pool admin keys are the keys document's. |
| A malicious or compromised agent (an injected instruction to leave, join or move) | The agent only asks; the person's tap is on a card the node writes from the act's bytes, naming the pool; `leave`, `remove` and `add-device` never take the project's pool silently; a `*` grant for them is refused; the fatigue ledger is device-wide. | A person who taps every card is protected by nothing here. |
| Downgrade | A policy is fixed per pool and the second join runs the first join's path. A device in a pool does not join an open pool by itself (D9). The label cannot be dropped: a dial without it gets nothing. | An `open` pool admits anyone on its segment; that is what open means. |
| Approval fatigue with many pools | The ledger counts asks per person across all pools (twelve in ten minutes, one prompt limit as in `docs/design-phone-free-pools.md` 2.3); the 64 waiting requests of a node are shared 16 to a pool, so a flooded pool cannot push out another's cards; a leave, a move and a join each cost one card or none. | A person with many pools sees more cards, one per act, each naming its pool. |
| Takeover by whoever is merely present | Presence gives no key; leaving, founding and joining need the person's grant and a card. | An unlocked, unattended computer whose password is known: as for every keystore secret. |
| A removed or departed device keeps another pool's state | Retiring a slot touches only that slot's directory, key, sessions, grants and consent; the others are untouched. A device that was never told it was removed keeps its frozen or live copy of that pool (no remote wipe exists), and the other pools stay as they were. | Entries already copied stay on every member's device; the leave card says so. |
| A forged refusal makes a device drop a pool | A slot is retired only on a signed removal, never on a TLS alert or a missing answer. | None. |
| Setting the clock forward to delete frozen boards early, or back to keep them | The 3 days count awake monotonic time; a wall clock changes nothing. | A process of the same user can delete files; out of scope. |
| A hostile pool fills the 16 slots | Slots fill only by joins the device's own person asked for (D9); a frozen slot does not count. | None. |
| A hostile pool's name or device name read as another's | Names are untrusted text, quoted and cut to 40 characters; the alias is the node's. | Check words remain the identity at a join. |

## 8. Open questions, and risks accepted with the owner

**Q1. The name a device shows each pool.** (a) The computer's own name in every pool (the choice in force): members recognise it when they approve or remove ("Remove
'guest-laptop'?"), and two members who compare notes can link the device by it. (b) A name made fresh per pool: no link by name, and the person cannot tell their own
devices apart in the pool's list without renaming. Both are correct; (a) costs the unlinkability goal a weak link, (b) costs recognition, which the approval cards rely on.

**Q2. What a pool label is keyed from.** (a) The pool id, which every former member knows (the choice in force; the same key the sealed beacons of
`docs/pools-and-serving.md` 4.2 use). (b) A per-pool discovery secret handed to members at admit and renewed at every removal: a former member then cannot ask whether the
device is still there. (b) is the stronger and is first by the ranking rule; (a) is in force because (b) adds an act kind and a renewal and a former member already knows
the address. This is the weaker option, named so, and the owner can order (b) as slice 3b.

## 9. Documents that change

`docs/pools-and-serving.md`: row M1 of section 0, section 4.1 (the certificate, the request context table), the layout and the beacon paragraph of 4.2, the "copied
device" paragraph of 1.6 (a heartbeat per pool), and slice 4 of section 11 (points here). `docs/node.md`: the pool section (certificate per pool, label, window), "Who
governs a pool" (the unfounded pool, "a device that left"), "What is left" (the lines on one pool and on the head domain, which stays empty by rule). `docs/limits.md`:
`MAX_POOLS`, the tombstone cap, the label window. `docs/glossary.md`: **alias** (a pool's name on one device); "a device may belong to several pools" is already
there; `left` and `removed` as the recorded states stay. `docs/api.md`, `docs/commands.md`, `docs/pool-check.md`, `docs/test-farm.md` (consent per pool), `docs/onboarding.md`
(no step added). `HANDOFF.md`: "many pools on a device remain pending" is deleted as slices land, and the known limit on `pool_leave` with slice 1.
