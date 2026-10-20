# A device in several pools

Design, 2026-10-19. Nothing here is built. Read at `bceddf929` (`0.3dev`): the survey is code and document reading; no test was run and no
screen was driven. The owner's order: a device belongs to several pools, and leaving, rejoining and moving between pools are first-class
acts. It reads with `docs/design-pool-keys-and-custody.md`, written in parallel and not on the branch when this was written; the one
thing taken from it is **a key per (device, pool)** (section 2.3 lists the interface this design assumes). It replaces decision M1 and
sections 4.1 and 4.2 of `docs/pools-and-serving.md` (one certificate for every pool) and fills in slice 4 of that document ("Many pools on
one device"); sections 5 to 7 there (capacity, shared boards, serving) stand and are not redone here. The break-it pass by a non-author is
`docs/notes/attack-multi-pool-devices.md`; section 7 states the design as it stands against it.

## 0. The decisions, one line each

| # | Decision | Why |
|---|---|---|
| D1 | A device holds up to 16 pools. Each is a **slot** with its own key, charter, acts, roster, members' addresses, join policy, discovery secret and boards. A pool of one is a pool. Frozen slots are capped at 16 as well. | The order; the limits keep memory, disk and beacons bounded. |
| D2 | The listener, the beacon port, the node, the lease table and the person's keys are the device's. Everything a pool can see is the pool's. | One port and one firewall step however many pools; no pool sees another. |
| D3 | **One certificate per pool** (from the pool's key), not one per device. The server picks it from a **pool label** the dialer puts in the ClientHello's server name, keyed from the pool's discovery secret and bound to the certificate the dialer expects; one connection serves exactly one pool. The pool certificate is shown only to a labelled dial. | Two members of two pools who compare notes must not be able to link the device by its fingerprint. Reverses M1. |
| D4 | A board belongs to one pool, fixed when it is made; its device id carries the pool. A project belongs to the pool of its board. A session is in the pool of its board. Nothing a person does picks a pool except the first question for a new project. | No flag for a person with one pool; no way to put a project's entries in the wrong pool by default; no pool can take another's board name. |
| D5 | A device with more than one pool and a command that cannot tell which pool it means gets one plain question, never a guess. | A wrong guess discloses a board to strangers. |
| D6 | Leaving is the device's own act, takes effect at once, destroys the pool's signing key and ends its sessions, and freezes its boards read-only for 3 days before the node deletes them. The signed `leave` is kept and re-sent until a member acknowledges it. Being removed does the same, once the device holds the signed removal, which the pool pushes to it. | HANDOFF "Release cut": a refused or shut-down pool is deleted after 3 days. |
| D7 | A rejoin is always a new key and a new certificate. A left or removed fingerprint is never admitted again, from the first slice. | Closes the order-of-leave-and-admit hole listed in HANDOFF; a copied old key gets nothing back. |
| D8 | A move is a join of the new pool, then a leave of the old one; never the other way round. | A failed join never strands the device. |
| D9 | A device in a pool never joins another by itself. Only a device that has never held a pool (no tombstone, no `had-a-pool` mark) joins an `open` pool it hears. | A broadcast on the Wi-Fi cannot add pools to a device, nor catch one that has just left. |
| D10 | Leases stay one table per device; requests gain a `pool` label for accounting. The GPU stays one holder at a time across all pools. | AGENTS.md section 1. |
| D11 | Nothing migrates. A state directory from before this change is set aside with the existing one line and the device starts with no pool. | AGENTS.md: no legacy mode. |
| D12 | A device's name in a pool is random and fresh per pool (two words), never derived from the computer's name, hostname or a hash of them. The person's own list shows "this computer" beside it. | No link by name between pools. |
| D13 | Each pool has a **discovery secret**, the key of its labels and sealed beacons. An admin renews it at every leave and removal. | The pool id is in every charter, so as a key it is no secret. |
| D14 | A pool-level request (`pool_leave`, `member_remove`, `add_device`, `admin_manage`, `inbound_consent`, `shard_consent`) is refused from a session whose board is in a different pool. Pool-level acts take no standing grant: each one is a card. | An injected instruction on one pool's board must not become a lever on the person's other pools. |

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
| Listener (`--listen`, `--network`), beacon port, sync loop thread, join window | `Net` (`net.rs:53`) | stay one each; the window has its own throwaway key and records its pool |
| Charter, acts, roster, join policy, project | `Node.members` (`pools/<id>/`) | per pool, many |
| Members' addresses, reach, builds | `Node.facts` (one addresses file) | per pool (`pools/<id>/addresses.json`) |
| Boards, names, tokens, links | `Node.boards`, `tokens.json`, `links.json` | boards live in a per-pool namespace (`<pool id>/<name>`); tokens and links stay device-wide, bound through the board |
| Project registry | `projects.json` (256 projects) | rows gain a pool |
| Pool history board | one board named `pool` (`poolops.rs:11`) | one per pool, named `pool` inside the pool's namespace |
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
* A slot is `pending` (a founding draft or a join in progress; its key is pending), `active`, `leaving` (the leave is under way, 3), `frozen` (left or removed; read-only until a deadline) or
  `gone` (only a tombstone remains, 2.4). A slot is addressed by its id; the person says the **alias**, a name local to the device that starts as the charter's
  name and is made unique ("lab", "lab 2"); aliases are never sent to anyone.
* A device in no slot is exactly today's unfounded device, with one difference: a device that has held a slot never joins an open pool by itself (D9). Leaving the last pool returns to it.
* **MAX_POOLS = 16**, in `docs/limits.md`. Past it: "This device is in as many pools as it may (16); leave one first." Frozen slots do not count toward it and are capped at **16** too; a leave that would make a seventeenth
  frozen slot brings the oldest frozen slot's deadline to now, and the leave card names it ("Leaving also deletes the frozen copy of 'old lab' now.").

### 2.2 What is the device's and what is the pool's

| The device's (one) | The pool's (per slot) |
|---|---|
| the node process, its supervisor, the local socket `node.sock`, the runtime | the pool key and certificate (2.3), the discovery secret (2.7), the device's name in the pool, the charter, acts, roster, refused acts, and the policy |
| the listener port and the beacon port, one firewall rule, one join window at a time (with its own throwaway key) | the members' addresses, reach and builds, the sync backoff, `attach` channels |
| the lease table, the GPU, resident models, the serve broker, capacity | the boards bound to it, its history board, the heads signed with its key |
| person keys, the approval inbox and its fatigue ledger, phones, the `had-a-pool` mark | inbound consent and the shard allow list, rates for joins and knocks, the discovery setting, grants scoped to the pool (kept in the pool's own history board or a device-local table, never on another pool's board) |
| the project registry (a row names its pool), tokens and links (a session is in its board's pool) | open-join rate limits, the clone heartbeat |

The rule behind the table: **a value a member of one pool can read must be a value no member of another pool can read.** The device's list holds only
values nobody over a connection sees.

### 2.3 Identity, the listener and the certificate (decision D3)

**Interface taken from the keys document.** A `PoolKey` is an Ed25519 key and a self-signed certificate for one (device, pool). It is made when a pool is
founded or when a join starts (the pool id is not known then, so it is `pending`) and is bound to the pool id when the join completes. It signs that pool's
handshake, its board heads, its beacons, its clone heartbeat and the device's own acts in it (`leave`, a delegate's `admit`). A leave destroys the key at once; a failed join, a
learned removal and an expired draft destroy it. Two keys of one device share no public value. The custody secret of the device wraps them and signs nothing a
pool sees. If the keys document differs, this document follows it; what must hold is the rule under the table in 2.2.

**Why not one certificate (M1).** M1 argued that a server cannot know the pool before it shows its certificate, and that separate certificates hide nothing
because addresses and ports are shared. The first is solved below. The second holds only for someone who sees the network. Two members of different pools,
on different networks or on different tailnets, who compare fingerprints link the device with certainty; with a certificate per pool they cannot, and the
cost M1 itself wrote down (a device removed from one pool takes a new identity in every pool) disappears.

**Choosing the certificate.** The node reads the ClientHello itself (rustls `Acceptor`), under a deadline of 5 seconds, and treats a missing, malformed or over-long server name as
no label: the answer is the same as for a label no slot matches. The dialer puts a **pool label** in the server-name field: lowercase base32 in DNS-sized pieces (at most 63
characters a piece, 253 in all), made of the **minute**, a fresh 16-byte **nonce** and a MAC keyed from the pool's discovery secret (2.7) over the nonce, the minute and
the **fingerprint of the certificate the dialer expects** (the one its roster pins for that member). A label therefore works only against the one certificate it names: sent to an address
that now belongs to someone else, or replayed against another address, it opens nothing. The server accepts a minute within 3 of its own and refuses a nonce it has seen
in that slot in that time (a cache of 4096 per slot, oldest evicted), so a label is used once per server. A dialer with no pinned fingerprint (a joiner with a code) sends no label.

The server computes **32 MACs for every label** whatever it holds: the current and the previous discovery epoch of each of 16 positions, with a dummy key for every
empty position, over the minute the label carries; the time does not depend on how many pools the device is in or on which matches. It takes the matching slot's prebuilt
`ServerConfig` (the slot's certificate and its own client-certificate check against its roster) and finishes the handshake. The label is never the pool id and never the same
twice. TLS 1.3 encrypts the server certificate, so an observer sees the label and nothing else.

| The dialer sends | The server shows |
|---|---|
| a label for a slot, with a member's certificate | that slot's certificate; every later request is for that pool only (a request cannot name another; there is no `ctx` field) |
| a label for a slot, with a throwaway certificate (a leaver delivering its `leave`, 3) | that slot's certificate; `hello` (the policy only) and `leave_notice` (self-verifying, a signed act) are answered, every other op is refused |
| a label for a slot, with the certificate of a removed (Revoked) member | that slot's certificate; the handshake completes and one answer comes back, the removal act naming that certificate; every other op is refused |
| no label, while a join window is open | the **window's own certificate**: a throwaway key made when the window opens and destroyed when it closes. A joiner with a code does not know the pool id; the join exchange binds both certificate fingerprints (section 4, step 2), so the pool's certificate is never needed before the code is accepted and is never shown to an unlabelled dial |
| no label and no window, a malformed name, or a label no slot matches | a throwaway certificate from a ring of 8 made at start and replaced one a minute (a connection costs no key generation); the connection answers nothing and closes, in the same time and with the same text for every case |

An unauthenticated handshake is capped: 8 in flight from one source and 64 in all, the rest closed at once, so a flood cannot take the thread-per-connection accept loop.
A window tells anyone on the segment that this device is open to a join for the minutes it is open, and tells nothing of its pools.

When a labelled dial reaches a throwaway the dialer says: "That device did not answer for this pool. Check that both computers' clocks are right." A clock more than 3 minutes off is a
named failure, not a silent refusal.

**What `hello` says to a member.** For a member of the connection's pool, exactly: the pool id; the slot certificate's `fingerprint`; `board_fingerprint`, which is the **pool key's**;
`network`; the build id; the node's seq; `pinned`, the pool's own pin. To a certificate that is not a member's, a `secure` pool answers its policy and nothing else (no pool id, project or
boards), and an `open` pool what its clear beacon already says.

**What a member learns of another pool: nothing from the protocol.** `hello`, `boards`, `members`, `acts`, `vector`, `pull` and `push` answer for the
connection's pool and tell nothing of the rest. What stays linkable, named: the **source address and port** (a member of two pools on one network sees the same
address; no design that keeps one listener removes it), the **hardware** and **build** `hello` reports, the **instant a build changes**, the **network** field, and the **pattern of
when the device is online**. The device's name does not link (D12).

### 2.4 Layout

```
<state>/node.sock, persons.json, approvals.json, tokens.json, leases.json, links.json, projects.json, write-limits.json       the device's
<state>/had-a-pool                                             present once the device has held any slot (D9); never deleted
<state>/custody/                                               the device's custody secret (keys document)
<state>/pools/<id>/charter.json, acts.json, seen.json, refused.json    as today
<state>/pools/<id>/key.*, discovery.*, addresses.json, slot.json       the pool key and certificate (keys document), the discovery secrets wrapped by the custody secret, members' addresses, {alias, name, epoch, state, until}
<state>/boards/<id>/<name>/                                    a pool's board; the history board is <id>/pool
<state>/boards/local/<name>/                                   boards of the local pool
<state>/pools/retired/<id>/                                    a frozen pool: its charter and acts, its boards (read-only), the signed leave and the discovery secret while frozen, tombstone.json
```

The board id on the device is the **full** pool id and the name (`<id>/<name>`); the name on the wire is the pool's own, looked up only inside the connection's pool (a prefix of the id is not
used, since a charter can be ground until eight digits agree). Frozen boards are hosted from `retired/`, read-only. A **tombstone** (at most 1 KiB: id, alias, charter name, the certificate it used, `left | removed | refused | shut-down`, when, the frozen deadline) outlives
the deletion of the rest, 256 at most, oldest first. It holds no key and no entry. `had-a-pool` outlives every tombstone. A slot whose directory has no key file beside it is not read (section 5).

### 2.5 Choosing a pool

**A person with one pool sees no change.** No flag, no question, no pool name in a card or a sentence (a name appears only where it tells two pools apart), no
extra step in founding or joining. A device that has never held a pool behaves as today, including joining an `open` pool of its project that it hears.

**A session is in the pool of its board** and needs nothing else. `project_resolve {path}` returns the project's board; the project row names its pool
(`projects.json` gains `pool`). So the hooks, `board.session.register` and every workspace command are unchanged. A project belongs to one pool for life: a
board never changes pool, and a project row names one board. The refusal for a second pool says so: "This folder's board is in 'lab'. To use it in 'team' as well, work from a second copy of the
repository." A pool that offers a board named like one the device already holds is no conflict: the two sit under different pool ids, and sync takes every name a pool lists with no reference to any other pool.

**A new project on a device with more than one pool** picks its pool, in this order, stopping at the first that applies:

1. `--pool NAME` or `POOLHOUSE_POOL`, when given.
2. Exactly one of the device's pools names this repository's project in its charter (`Charter.project`): that pool. No question.
3. Otherwise a plain question, once per project, answered by a tap or a word and stored in `projects.json`: "This folder is new to Poolhouse. Which pool is its
   board for: lab or team?" A hook that cannot ask returns `needs: choice` and the agent asks the person with the question tool; the answer is never defaulted.

**A command that is about a pool** (`status`, `members`, `add-device`, `remove`, `leave`, `move`) takes `--pool NAME` (alias, or the first six hex digits of the
id when two aliases would read alike). With no flag: the one pool when there is one; else the pool of the project of the working directory when the command is
about the project's work (`status`, `members`); else "This device is in two pools, lab and team. Which one do you mean?" as a `needs: choice` refusal (a
`Refusal` in Python, `ph.Denied`) listing the aliases. `add-device`, `remove` and `leave` never take the project's pool silently.

**A pool-level request comes from the person, not from another pool's session.** The node refuses `pool_leave`, `member_remove`, `add_device`, `admin_manage`,
`inbound_consent` and `shard_consent` from a session whose board is in a different pool than the one named. Such an act comes from the person's own app or phone, or from a session of that pool's own boards, and in
every case is a card; the card reads "asked by an agent working in 'lab'" when a session asked.

**Pool-level acts take no standing grant.** Each is one card, and the approval on it is single use, bound to the act's bytes. A card for one names the alias, and **always the first six
digits of the pool id** (a stranger's "lab" must not read as the person's own), written by the node from the act's bytes. Grants for other pool-scoped things name an exact 32-hex pool id (no wildcard of any kind, since a prefix before a final `*` also covers); they are kept in the pool's own
history board or a device-local table, never on another pool's board (a board entry replicates to every member of that pool and would disclose another pool's id); and a grant that names a frozen or gone pool is void by derivation.

**The name a pool sees (Q1).** A device's name in a pool is made at the join: random, two words, never taken from the computer's name or hostname or a hash of either. A rename in one pool is not offered in another.
The person's own list marks "this computer" beside it, so recognition does not rest on a shared name. A card names another device by the name the pool knows.

### 2.6 Compute is shared; pools are not

`leases.json` stays one table. The GPU is one holder at a time across every pool; a test shard from one pool, a model call for another and the owner's own
work queue on the same `gpu` and `cpu_slots` resources, in the order of `docs/node.md` "Leases" and, once built, the fair order of
`docs/pools-and-serving.md` section 5. This design adds three things:

* A lease gets a `pool` label (the board's pool for a session, the connection's pool for a remote request), for accounting and fairness only, never for authority.
* A remote request can name no lease and no holder (it never could); a `busy` answer to a member says only that the device is busy, never whose work it is, and is **immediate**: a remote
  request is never queued behind another pool's lease.
* A resident model is loaded once for the device. Which pools may call it is the offer of each pool (consent off by default, `docs/pools-and-serving.md` L2), and a
  server that answers two pools keeps `--parallel 1` and erases its slot when the next call is another pool's (that document, 7.4).

### 2.7 The discovery secret (decision D13)

Each pool has a 32-byte **discovery secret** made at founding and handed to each device at its admit, sealed to that device's pool key. It keys the pool label (2.3) and the sealed
beacon. A secret has an **epoch** (a counter in the act that carries it); a device holds the current one and, until a day has passed, the previous.

* **Renewal at every leave and every removal.** An admin who sees a `leave` or a `remove` writes a `discovery_renew` act (routine class). The new secret rides **sealed to each remaining
  member's pool key inside the act**, so a malicious member cannot split the pool by handing the secret to some. A renewal is authored by an admin only. The act's size cap is checked in slice 3b against a charter of
  16 admins and the largest pool. A pool of one renews nothing.
* **A day of grace.** A server accepts the previous epoch's label for one day of its awake time after it learns the renewal. A removed member then learns only that the address answers during that day,
  against for ever. A member that was offline through a renewal learns the new secret from the acts it fetches in the day; one away longer is re-reached by the other members, who dial it with the epoch it last acknowledged (a member keeps a secret until
  every active member has acknowledged a later one, 8 epochs at most).
* **Beacons use the same secret.** The sealed beacon of `docs/pools-and-serving.md` 4.2 is sealed under the discovery secret of the pool, not under the id; a former member decodes no beacon sent after the renewal.
* **Kept through the frozen period.** A leaver keeps the secret and its signed `leave` in the frozen slot so it can still deliver the act (3); the secret is destroyed at the end of the frozen period.
* **Storage.** The secrets sit in the slot, wrapped by the custody secret and destroyed with the slot; a stolen directory with a file key carries them, as it carries the pool key.

## 3. Leaving, being removed, rejoining, moving

**Leave** (a card naming the pool; one tap; the agent only asks). The card says, in plain words, "Entries that never left this computer are deleted after 3 days." Order, each step idempotent and written ahead:

0. The node writes `slot.json` `state: leaving` with the act bytes **before anything else**, and from then refuses every request for the pool. A crash at any later step is resumed at boot from this state.
1. The node writes a `pool_left` audit row to the pool's history board, then signs the `leave` act with the pool key. The act carries the leaver's own log tips in that pool's boards (`{board, origin, seq, hash}`) as **advisory**: each member closes the leaver's logs at the tip it holds when it first sees the act, never lower than the highest tip it has held and never above what it holds, so a `leave` naming seq 0 truncates nothing a member held. `close_removed` names `Left` as well as Revoked.
2. It closes the pool's connections, stops its beacon, and refuses its requests; the sessions of its boards end (tokens ended, an `ended` identity row each) and
   the grants and shard consent scoped to the pool are void.
3. **The pool's signing key is destroyed** at once, and the join code. The signed `leave`, the discovery secret, the charter, acts and boards stay in the frozen slot; the boards are read-only.
4. **Delivery is retried until acknowledged.** The leaver dials each member whose address is held, with a label from the kept discovery secret and a throwaway client certificate, and calls `leave_notice` (the act authenticates itself). It retries every 5 minutes
   for an hour, then hourly, until one member acknowledges; any member that holds the act relays it. Nothing else waits for a member.
5. The slot is `frozen` until 3 days of this device's awake time have passed (the monotonic clock of `gov/clock.rs`, so a changed wall clock deletes nothing early),
   then the node deletes the boards, the charter and the discovery secret, writes the tombstone and the slot is `gone`. The frozen boards can be read the whole time and written
   never. A person who wants a board's entries earlier asks a member of the pool; the entries are on every member's copy, except any that never left this computer.

A leave by the only member of a pool ends the pool: the card says "You are the only device in 'lab'. Leaving ends the pool, and its boards are deleted after
3 days." A device that is an admin's delegate loses the delegation with the key. A `leave` is a union in the roster, like a `remove`: a left fingerprint is never admitted again, whatever
the order the acts arrive in or the time the signer wrote.

**Removed by others.** A device acts on a removal only when it holds the **signed removal**, never on a refused handshake: a TLS alert says only that someone refused it and anyone on the path can send one, so `refusal_hint` no longer reads one. The pool delivers it two ways:
the admin who removes the device pushes the signed removal once to its last address, and every member pushes it at its next dial of that address, retrying for 30 days until the device acknowledges, each push labelled with the epoch the device held at the removal; and a removed
device that dials a member is not cut off by an alert: its handshake completes and it gets one answer, the removal act naming it, which verifies against the charter it holds (2.3). With the removal in hand the slot is retired exactly as in
steps 2 to 5 (the key is destroyed and nothing is sent, since the pool refuses it). A removal from one pool changes nothing of the device's other pools.

**Rejoin.** The same pool again is a new join: a new key, a new certificate, a fresh random name, an admit as for any new device (section 4). Its old certificate stays `left` or
`removed` in the roster for ever and is never admitted again, from slice 1; until slice 3 gives a rejoin a new key, slice 1 refuses a rejoin of the same certificate with one plain line, and the HANDOFF limit stays one slice longer. A replayed `leave` or `remove` names the old fingerprint and can touch nothing of the new membership.
The frozen boards of the earlier membership are dropped once the rejoin has synced the pool's boards, **except** entries no member holds (offline writes, a pool of one): the rejoin line says "This computer holds 12 entries of your earlier membership that no
member has. Delete them?" and the frozen copy is kept until the person's yes. A device never rejoins by itself a pool that removed it (the tombstone says so), including an `open` pool it hears, and a device that has held any pool never joins an open pool by itself (D9).

**Move** (`poolhouse-pool move --from NAME CODE`, `ph.pool.move`) is a join of the new pool followed by a leave of the old one. The join runs first, with the
code typed on this device (section 4); only when it has completed does the node ask for the one tap, written from the leave act: "Move this computer from 'lab' (a1b2c3)
to 'team' (d4e5f6)? It stops being a member of 'lab'. Entries already copied to lab's other devices stay with them." If the join fails nothing changes. If the leave
then fails the device is in both and says so. Repeating the command after success finds the device in the new pool and out of the old one and reports done.
Projects of the old pool are not moved: a working directory whose board is frozen asks "That project's board was in 'lab', which you left. Make a new, empty board for it in 'team'?" once,
and the answer makes a new, empty board in the new pool. Copying is a separate named card, "Copy 214 entries from 'lab' into 'team'? 'team's members will be able to read them.", and is never part of the first.

**A pool shut down for compromise** (the act is not built: HANDOFF "Release cut") is sealed instead of frozen: read-only for good, key and grants removed, never
restorable. The 3 days delete only its live state; the sealed record stays until a person deletes it. This design only gives the device side the same
retire routine with that variant.

## 4. Joining a second pool while in a first

1. A member of the other pool opens a join window on its own screen or phone and reads the code to the person (`docs/pools-and-serving.md` 1.6). The person
   tells their agent, or types the code into the app: "join Sam's pool, code 482 913" (`poolhouse-pool join CODE`, `ph.pool.join`).
2. The node makes a **pending key** and runs the join-code exchange (SPAKE2) with both certificate fingerprints as its identities, over the window's own certificate (2.3). The fingerprint it shows is the
   pending certificate's, not that of any pool it is in. The pool id and the discovery secret arrive sealed in the exchange; the node binds the key to the id.
3. The approval is the one the first join already has, unchanged in cost: typing the code on the new device is the approval; a join through an agent adds the three
   check words and one tap (1.6 there). **Already being in a pool adds no ask.** Joining binds no existing board (its boards stay where they are),
   turns on no inbound consent and adds no one to any shard allow list, so the new pool can do nothing on the device that a stranger could not.
4. One plain line comes back: "Joined 'team' as 'amber-otter'. 'lab' and 'team' stay separate: neither can see the other." The pool's boards then sync.

A join that fails, is refused or is abandoned destroys the pending key. A pending key also expires an hour after it was made. Joining a pool the device is
already in answers `already` and changes nothing; joining a pool whose slot is frozen starts the rejoin of section 3. An open pool is joined only when asked
for by a person or an agent, except by a device that has never held a pool (D9). At most one join window is open on a device; opening one ends another and says so.

## 5. Migration: none

A state directory with a pool in it that has no key file beside it (every pool made before this change) is set aside the way the node already sets aside a pool
of another charter version: moved, never deleted, to `pools-old-format/<id>`, with the existing line "this device was in an old-format pool: it now starts
unfounded; create a new pool". The device starts with no pool, and `had-a-pool` is written so it joins nothing by itself; every board it holds is served as before, in the local pool, and is bound by an explicit
person act when a pool is founded or joined. No old pool is read, converted or kept working. The device-wide certificate and key of the old format are not
reused as a pool key.

## 6. What changes, and the slices

Each slice lands alone, with the tests named first, each watched failing against the old code. `scripts/test all <files>`; Rust under `app/poolhouse-node/tests/`. Every fix of the attack pass
has its slice and test here.

| # | Slice | Scope | Fail-first tests | Needs |
|---|---|---|---|---|
| 1 | **Leave frees the device** (no second pool yet) | `pool_leave` writes `state: leaving` first, then retires `pools/<id>` to `pools/retired/<id>`, ends sessions, writes the advisory tips into the act, writes the tombstone and `had-a-pool`, runs the 3-day sweep on awake time; `leave` is a union in `gov/roster.rs` (never admitted again) and `close_removed` names `Left`; `leave_notice` and its retry; frozen slots capped at 16; `can_join_other` becomes "the local pool"; `pool_create` and `adopt_governed` stop refusing a device that has left; a rejoin of the same certificate is refused; the unfounded view returns; the beacon handler never auto-joins a device with `had-a-pool`. | `pools_leave.rs`: `found_leave_found_again`; `leave_then_join_other`; `frozen_board_refuses_write_and_goes_after_deadline` (test clock); `replayed_leave_touches_nothing`; `left_fingerprint_never_readmitted` (leave, admit the same fingerprint, still `Left` on a device that sees the acts in either order, and with a later `at_ms`); `leave_with_seq_zero_truncates_nothing_held` (F6); `crash_after_signing_resumes_leave` (F4/F14a: kill after the signature, boot finishes and destroys the key); `leave_notice_retried_until_acknowledged` (first sends fail, a later one is acknowledged); `leaver_never_auto_joins_open_pool` (F7: beacon of an open pool, device with a tombstone and with only `had-a-pool` stays out); `seventeenth_frozen_slot_deletes_oldest` (F18) | none |
| 2 | **Pool slots, one at a time** | `pools.rs` (`PoolSet`, `Slot`); `Node.members`, `cert` and `facts` move into the slot; the board id becomes `<pool id>/<name>` (`Hosted.pool`, `boards/local`); `pool_list`; `projects.json` `pool`; `MAX_POOLS` constant set to 1. | `pools_list.rs`: `pool_list_answers`; `board_made_founded_names_its_pool_and_older_is_local`; `board_id_uses_full_pool_id` (two ground charters sharing eight digits keep separate boards) | 1 |
| 3 | **Pool key, pool certificate, label** | the slot's key and certificate replace the device's in the handshake, heads and beacons; `Acceptor` and the label (`label.rs`), carried in the client's server name (a constant today), keyed from the discovery secret (epoch 0, handed at admit) and bound to the expected fingerprint; the nonce cache; 32 MACs; the window key; the throwaway ring; the unauthenticated handshake caps and the ClientHello deadline; uniform answer for malformed names; the clock message; `hello` to a non-member of a `secure` pool answers the policy only, and the member `hello` set of 2.3. | `pools_label.rs`: `wrong_label_and_no_label_same_answer_same_shape`; `label_replayed_after_window_gets_throwaway`; `label_bound_to_expected_fingerprint` (F2: a label made for one certificate opens nothing on another address); `label_used_once_per_server` (F2: nonce cache); `window_serves_its_own_key_and_pool_cert_is_never_shown_unlabelled` (F1); `window_key_destroyed_on_close`; `always_32_macs` (F13: timing does not depend on slot count); `malformed_sni_same_answer_as_unmatched` (F13d); `unauthenticated_flood_is_capped` (F13c); `label_is_dns_shaped_lowercase` (F13e); `labelled_dial_to_throwaway_names_the_clock` (F13f); `hello_from_unknown_certificate_carries_no_pool_id`; `hello_member_set_is_per_pool_where_it_can_be` (F17: `board_fingerprint` differs per slot); `fingerprints_differ_per_slot` | 2, keys document |
| 3b | **Discovery renewal** | `discovery_renew` act (admin only, routine class), sealed to each remaining member's pool key; renewal at every leave and removal; a day of grace for the previous epoch; re-reach of a member away longer; the secret kept through the frozen period; the size cap checked. | `pools_discovery.rs`: `leave_triggers_renewal`; `removal_triggers_renewal`; `renewal_by_non_admin_refused`; `renewal_sealed_to_each_member_and_not_splittable`; `removed_member_label_fails_after_grace_and_works_inside_it`; `offline_member_rejoins_reach_after_grace`; `leaver_keeps_secret_until_frozen_deadline` (F4); `renewal_act_fits_16_admin_charter_and_largest_pool` | 3 |
| 4 | **N pools** | `MAX_POOLS` 16; the sync loop and `attach`/`reverse` keyed by (pool, fingerprint); per-pool addresses, rates, window, consent; board ops filtered by the connection's pool; links refuse boards of two pools; second founding and second join. | `pools_two.rs`: `two_pools_never_answer_for_each_other_boards`; `other_pool_board_is_same_denied_as_unknown_board`; `removal_cut_naming_other_pool_board_refused`; `second_join_with_code_works_and_is_idempotent`; `pool_b_lists_board_named_like_a_boards_pulled_into_own_namespace` (F10: A's board untouched, no name is skipped); `link_between_two_pools_refused` | 3 |
| 5 | **Beacons per pool** | one datagram per discoverable pool that has other members; sealed under the discovery secret for `secure`, clear for `open`; none for a secure pool of one or `discovery: off`. | `pools_beacon.rs`: `sixteen_secure_pools_of_one_send_no_datagram`; `secure_beacon_unreadable_without_secret`; `former_member_decodes_no_beacon_after_renewal` | 3b, 4 |
| 6 | **Choosing a pool** | `project_resolve` and the new-project question (2.5); `--pool`, `POOLHOUSE_POOL`; `needs: choice`; pool-level acts take no standing grant and refuse a request from a session of another pool; pool-scoped grants stored in the pool's own history board or a device-local table, exact id only; cards name the alias and six digits. | `pools_choose.rs`; `tests/test_pool_choose.py`: `question_asked_only_with_two_pools`; `hook_returns_needs_choice_and_does_not_block`; `pool_leave_from_session_of_other_pool_refused` (F8); `card_names_the_asking_agents_pool` (F8); `pool_level_act_always_raises_a_card` (F9c); `grant_with_prefix_wildcard_refused` (F9a: `0*`, `ab*`, `*`); `pool_grant_not_written_to_another_pools_board` (F9b); `grant_naming_frozen_pool_is_void` (F9d); `card_shows_six_digits_with_one_pool_alias` (F14b) | 4 |
| 7 | **Leave, removal and move on the network** | the signed-removal path retires a slot; a removed (Revoked) member's handshake completes with one answer, the removal act; the push of the signed removal by the remover and by every member for 30 days; `refusal_hint` no longer reads the alert; no retire on a refused handshake; `move`; the frozen copy kept until a yes when it holds rows no member has; the wording of the frozen-board question and the copy card; tombstone refusal of auto-rejoin. | `pools_retire.rs`: `forged_tls_alert_changes_nothing`; `signed_removal_retires_only_that_pool`; `move_with_failing_join_changes_nothing`; `removed_member_gets_the_removal_and_nothing_else` (F3b); `removal_pushed_to_last_address_and_retried_by_every_member` (F3a); `removed_device_that_never_dialled_retires_on_push` (F3); `frozen_copy_with_unsynced_rows_kept_until_yes` (F15); `frozen_board_question_makes_empty_board_and_copy_is_separate` (F11); `tombstone_refuses_auto_rejoin` | 4 |
| 8 | **Accounting** | lease `pool` label, `capacity {pool}`, fatigue ledger counts all pools together, per-pool queue share and waiting-request cap max(2, 64 / pools held) plus the device-wide 64; a remote `busy` answers at once. | `pools_fatigue.rs`: `twelve_asks_spread_over_three_pools_stop_at_twelve`; `four_pools_cannot_fill_all_64_waiting_requests` (F12: each pool keeps at least 2); `remote_busy_is_immediate_and_never_queued_behind_other_pool` (F16) | 4 |
| 9 | **Python and the check** | `ph.pool.*` take `pool=`, `ph.pools()`; `node_pool.py`; `board/founding.py` ("already a pool named"); `board/place.py`; `node_join.py`; `poolhouse-pool` as the canonical command; `pool-check` steps `two-pools-on-one-device`, `leave-and-rejoin`, `move-pool`, `removed-from-one-stays-in-the-other`, `no-link-between-pools`, `leave-delivered-after-offline-send`; `tests/node_kit.py` and `pool_check/world.py` take two pools. | `tests/test_pool_two.py` and the steps, on real nodes. | 6, 7 |
| 10 | **Documents** | below. | `tests/test_glossary.py`, `tests/test_documented_env_vars.py`. | each |

**Modules, Rust:** new `pools.rs`, `label.rs`, `retire.rs`; changed `node.rs` (`Node` loses `members`, `cert`, `key` and `facts` for the pool, `host` takes the pool and the pool's key),
`membership.rs` (`Pool::load` reads many; `can_join_other` goes), `gov/ops.rs` (about 22 uses), `gov/wire.rs`, `gov/roster.rs` (`leave` a union), `gov/logs.rs` (tips advisory for a leave, cuts only for the pool's boards), `net.rs`
(`Net.me`, `server_config`, `heard`, `join_open`, `join_pool`, `adopt`, the sync loop, the window key), `tls.rs` (per-slot configs, the label, the ring, the Revoked answer), the member-protocol module (the connection's pool, `leave_notice`), `beacon.rs`, `facts.rs`,
`netsync.rs`, `poolapi.rs`, `poolops.rs` (the per-pool history board), `rooms.rs`, `landing.rs`, `landtrust.rs`, `subs.rs`, `build.rs`, `shard/` (consent per pool), `approvals/request.rs`, `phone/` (the
four reads of `members`), `registry.rs`/`project.rs` (`pool`), `links.rs`, `grants/` (pool in the scope, no wildcard for pool-level acts, storage), `lease/` (label). **Python:** `node_pool.py`, `api/pool.py`, `board/founding.py`,
`board/place.py`, `node_join.py`, `fleet/onboard/cli.py`, `workspace` `pool` verbs, `poolhouse-pool`, `tests/pool_check/*`, `tests/node_kit.py`.

**Fixed costs; N pools must not multiply the idle cost.** One listener thread, one sync thread and one beacon thread however many pools: the sync thread walks
the slots and dials members, so its work grows with the members of all pools, as it does today with one. A pool of one dials nobody and, when `secure`, beacons
to nobody. Per slot: a `GovPool` (bounded by its caps), a prebuilt TLS config, and one history board (counted in the 256 of `MAX_BOARDS`, which stays device-wide). Per
handshake: a fixed 32 MACs and no key generation. `Node::open` loads every slot eagerly. Bound, by `pools_cost.rs`: a node with 16 pools of one, idle, opens in at most twice the time
of a node with one pool plus a small per-pool allowance measured once and written into `docs/limits.md`, sends no beacon datagram, makes no dial, and a
handshake with a label costs at most the same as without. The new constants (`MAX_POOLS`, the frozen-slot cap, the tombstone cap, the 3 days, the label window of 3 minutes, the nonce cache of 4096,
the throwaway ring of 8, the unauthenticated caps of 8 and 64, the ClientHello deadline, the day of grace, 8 epochs, the 30-day removal push, the waiting-request share) go in `docs/limits.md`.

## 7. Attack first

A non-author pass is `docs/notes/attack-multi-pool-devices.md`; each row states what stops the case now, and the last column names where it is built and tested. Each case holds under the owner's rule that security adds no
visible step to a normal journey.

| Case | What stops it | What remains, named | Slice, test |
|---|---|---|---|
| A member of one pool probes whether the device is also in another | It needs the other pool's discovery secret to make a label bound to the certificate it expects; with a wrong, malformed or no label the answer is the same throwaway certificate and the same close, in the same shape and time. An unlabelled dial gets the window's own key or a ring certificate, never a pool certificate. Board and member ops answer for the connection's pool only, and an unknown board and another pool's board give the same `denied`. Pool ids are no key: knowing one asks nothing. | Whoever holds a pool's discovery secret can ask an address whether it answers for that pool: its members, and a removed or departed member for one day after the renewal (2.7). The source address and port, the hardware and build line and the instant a build changes, the `network` field and the online pattern link a device for anyone who sees both pools (2.3). An open window shows that the device is open for a join. | 3: `wrong_label_and_no_label_same_answer_same_shape`, `window_serves_its_own_key_and_pool_cert_is_never_shown_unlabelled`; 4: `other_pool_board_is_same_denied_as_unknown_board` |
| Side channels in label handling | 32 MACs for every label; the minute rides in the label; throwaway certificates from a ring; a malformed or over-long name is treated as unmatched; per-source and global caps and a ClientHello deadline; the label is lowercase and DNS-sized; a clock off by more than the window gets a named message. | Timing at the granularity of a whole handshake. | 3: `always_32_macs`, `malformed_sni_same_answer_as_unmatched`, `unauthenticated_flood_is_capped`, `label_is_dns_shaped_lowercase`, `labelled_dial_to_throwaway_names_the_clock` |
| Two members of two pools compare notes | Their fingerprints, board fingerprints, origins, board heads, beacons and device names differ per pool (the name is random); nothing a pool receives is signed by a value another pool sees. | The same address and port on one network; the hardware and build; the instant a build changes; the online pattern. | 3: `fingerprints_differ_per_slot`, `hello_member_set_is_per_pool_where_it_can_be` |
| A device used as a bridge between pools | A board has one pool; its device id carries the full pool id; a connection serves one pool; `boards`, `vector`, `pull`, `push` and `hello` read only that pool's boards; **a link between boards of different pools is refused**, stricter than the rule in `docs/pools-and-serving.md` 6.6, which only stops a link that carries entries to a pool that lacks the board; a lease answer names no holder; the cut of a removal applies only to boards of the act's pool. | An agent that holds sessions in two pools can carry text between them, as a person can; the node cannot see meaning. Each registration is in its pool's audit. | 4: `link_between_two_pools_refused`, `removal_cut_naming_other_pool_board_refused` |
| A pool squats or mirrors a board name | The device id is `<pool id>/<name>` and the wire name is looked up only inside the connection's pool; sync takes every name the pool lists with no reference to another pool, so no name is skipped or merged and none is an oracle. | None. | 2: `board_id_uses_full_pool_id`; 4: `pool_b_lists_board_named_like_a_boards_pulled_into_own_namespace` |
| A copied or cloned device | A pool key is wrapped by the device's custody secret (keystore where one exists), so a copied directory starts no node and carries no key. A clone of a device with a file key holds every pool key at once; each pool detects the twin by its own heartbeat (a counter backwards, two boot ids under one fingerprint, `docs/pools-and-serving.md` 1.6) and an admin removes it per pool. A leave is final in the roster, so a clone that signs a `leave` in every pool can evict the original from all of them, but the original needs only a new key and an admit in each, and the clone cannot undo the leave either. A leave's tips are advisory and truncate nothing a member held. | A weak (file) custody loses every pool of that device together; the device says its custody on `pool_status` as today. | 1: `left_fingerprint_never_readmitted`, `leave_with_seq_zero_truncates_nothing_held` |
| A copied pool, or a look-alike named like one of mine | The pool id is the hash of the first charter, so a copy with other admins is another pool; the alias is local and made unique, and every card for a pool-level act shows the first six digits of the id. | A person who joins a pool by a code a stranger gave them is trusting the stranger; the join approval is theirs. | 6: `card_shows_six_digits_with_one_pool_alias` |
| Replay | A label is bound to a nonce, the minute and the one certificate it names, and a server takes a nonce once; a label heard on the wire is useless against another address and against the same server. A replayed `leave` names an old fingerprint and `leave` is final (D7). Acts name their pool. | None for the label. | 3: `label_bound_to_expected_fingerprint`, `label_used_once_per_server`; 1: `replayed_leave_touches_nothing` |
| A forged or below-threshold signature | A pool key signs only its own pool's device acts; an act for another pool is refused as today (a `leave` copied into another pool is refused: the act names its pool); no key of one pool is a key of another; a `discovery_renew` from a non-admin is refused. | None new. | 3b: `renewal_by_non_admin_refused`; 1: `replayed_leave_touches_nothing` |
| A compromised or malicious member | It can read and write only its pool's boards. A `remove` it relays carries a cut only for its pool's boards. It cannot fill another pool's slots; slots fill only by the device's own joins. It cannot split the pool by handing the discovery secret to some (each remaining member receives it sealed inside the act). Join and knock rates are per pool. | A member of a pool can spend that pool's open-join rate (5 an hour) and nothing else. | 3b: `renewal_sealed_to_each_member_and_not_splittable` |
| A stolen phone or person key | The phone makes a key per pool (`docs/pools-and-serving.md` 1.17); pool-level acts take no standing grant, so a stolen key leaves nothing behind that outlives it. | A person key that is admin of two pools is stolen from both; per-pool admin keys are the keys document's. | 6: `pool_level_act_always_raises_a_card` |
| A malicious or compromised agent (an injected instruction to leave, remove, add or move, on a shared board) | The agent only asks; the node refuses a pool-level request from a session whose board is in a different pool; the card is written from the act's bytes, naming the pool, six digits of its id and "asked by an agent working in 'lab'"; every such act is one card, never a standing grant; the fatigue ledger is device-wide. | A person who taps every card is protected by nothing here. | 6: `pool_leave_from_session_of_other_pool_refused`, `card_names_the_asking_agents_pool`, `grant_with_prefix_wildcard_refused`, `pool_grant_not_written_to_another_pools_board`, `grant_naming_frozen_pool_is_void` |
| Downgrade | A policy is fixed per pool and the second join runs the first join's path. A device that has held a pool never joins an open pool by itself, so a beacon on the segment cannot catch a device that has just left or been removed; only a fresh install does. The label cannot be dropped: a dial without it gets nothing. | An `open` pool admits anyone on its segment; that is what open means. A fresh install with no tombstone joins an open pool of its project it hears. | 1: `leaver_never_auto_joins_open_pool` |
| Approval fatigue with many pools | The ledger counts asks per person across all pools (twelve in ten minutes, one prompt limit as in `docs/design-phone-free-pools.md` 2.3); the 64 waiting requests of a node are shared to a pool at max(2, 64 / pools held) each, so a flooded pool cannot push out another's cards; a leave, a move and a join each cost one card or none. | A person with many pools sees more cards, one per act, each naming its pool. | 8: `twelve_asks_spread_over_three_pools_stop_at_twelve`, `four_pools_cannot_fill_all_64_waiting_requests` |
| Takeover by whoever is merely present | Presence gives no key; leaving, founding and joining need the person's card. | An unlocked, unattended computer whose password is known: as for every keystore secret. | 6: `pool_level_act_always_raises_a_card` |
| A removed or departed device keeps another pool's state | Retiring a slot touches only that slot's directory, key, sessions, grants and consent; the others are untouched. A removed device is told by a push of the signed removal and by the answer to its own dial, so its 3-day clock starts. A device that stays offline and unreachable keeps its frozen or live copy of that pool (no remote wipe exists), and the other pools stay as they were. | Entries already copied stay on every member's device; the leave card says so. | 7: `removal_pushed_to_last_address_and_retried_by_every_member`, `removed_member_gets_the_removal_and_nothing_else`, `signed_removal_retires_only_that_pool` |
| A leave that never reaches a member | The signed `leave` and the discovery secret stay in the frozen slot; delivery is retried until one member acknowledges, by a throwaway certificate on a label, through `leave_notice`; a crash mid-leave resumes from `state: leaving`. | A leaver offline for more than the day of grace after a renewal by another act cannot label a dial; the pool lists it until an admin removes it. | 1: `leave_notice_retried_until_acknowledged`, `crash_after_signing_resumes_leave`; 3b: `leaver_keeps_secret_until_frozen_deadline` |
| A forged refusal makes a device drop a pool | A slot is retired only on a signed removal, never on a TLS alert or a missing answer. | None. | 7: `forged_tls_alert_changes_nothing` |
| Setting the clock forward to delete frozen boards early, or back to keep them | The 3 days count awake monotonic time; a wall clock changes nothing. A device whose clock is off by more than 3 minutes is told so when a labelled dial fails, not refused silently. | A process of the same user can delete files; out of scope. | 1: `frozen_board_refuses_write_and_goes_after_deadline`; 3: `labelled_dial_to_throwaway_names_the_clock` |
| A hostile pool fills the 16 slots, or churns frozen slots | Slots fill only by joins the device's own person asked for (D9); a frozen slot does not count toward 16 and is capped at 16 itself, the oldest deleted first and named on the leave card. | None. | 1: `seventeenth_frozen_slot_deletes_oldest` |
| A hostile pool's name or device name read as another's | Names are untrusted text, quoted and cut to 40 characters; the alias is the node's. | Check words remain the identity at a join. | 6: `card_shows_six_digits_with_one_pool_alias` |
| A frozen board's content is handed to a new pool by one tap | The frozen-board question makes a new, empty board; copying is a separate card naming the entry count and the pool, and the frozen copy of a pool a person left is never adopted. | A person who taps the copy card chooses to disclose. | 7: `frozen_board_question_makes_empty_board_and_copy_is_separate` |
| A rejoin loses entries nobody else has | The frozen copy is dropped only after the pool has synced, and is kept until the person's yes when it holds rows no member has; the leave card says unsent entries are deleted after 3 days. | Entries that never left the computer are deleted after 3 days if the person leaves and never rejoins. | 7: `frozen_copy_with_unsynced_rows_kept_until_yes` |
| Whether a device is busy for another pool | A remote request's `busy` is immediate and never queued behind another pool's lease; it names no holder. | A member of one pool sees when the device is busy and so learns it works for someone; the busy periods link by timing. Accepted. | 8: `remote_busy_is_immediate_and_never_queued_behind_other_pool` |

## 8. Decisions with the owner, and risks accepted

**Q1, the name a device shows each pool: a random name, fresh per pool** (D12). Never derived from the computer's name, hostname or a hash of them. The person's list marks "this computer" beside it; a rename in one pool is not offered in another;
a card names another device by the name that pool knows. The name does not link, and the source address, port, hardware, build instant and online pattern still do (2.3): a stable fingerprint was the headline reason for D3, and a stable name would be a weaker form of the same link.

**Q2, what a pool label is keyed from: a per-pool discovery secret** (D13, 2.7), in five parts: (1) renewed at every leave and every removal; (2) one day of grace for the previous epoch; (3) sealed beacons on the same secret; (4) kept through the frozen period; (5) the renewal authored by an admin only.

**Risks accepted.** A removed or departed member can ask whether an address answers for the pool for one day after a renewal. A member away through a renewal and longer than the grace is out of reach by label until
re-reached by the others. A leaver offline past the grace cannot deliver its `leave` until an admin removes it. A remote request's `busy` shows a member of one pool when the device is busy. A clone of a file-custody directory loses every pool of the device together.
A person who taps every card is protected by nothing here. An open window shows that the device is open to a join for the minutes it is open.

## 9. Documents that change

`docs/pools-and-serving.md`: row M1 of section 0, section 4.1 (the certificate, the request context table), the layout and the beacon paragraph of 4.2 (the sealed beacon is keyed from the discovery secret), the "copied
device" paragraph of 1.6 (a heartbeat per pool), and slice 4 of section 11 (points here). `docs/node.md`: the pool section (certificate per pool, label, window key, discovery secret), "Who
governs a pool" (the unfounded pool, "a device that left", `had-a-pool`), "What is left" (the lines on one pool and on the head domain, which stays empty by rule). `docs/limits.md`:
`MAX_POOLS`, the frozen-slot cap, the tombstone cap, the label window, the nonce cache, the ring, the unauthenticated caps, the grace, the removal push, the waiting-request share. `docs/glossary.md`: **alias** (a pool's name on one device) and
**discovery secret**; "a device may belong to several pools" is already
there; `left` and `removed` as the recorded states stay. `docs/api.md`, `docs/commands.md`, `docs/pool-check.md`, `docs/test-farm.md` (consent per pool), `docs/onboarding.md`
(no step added). `HANDOFF.md`: "many pools on a device remain pending" is deleted as slices land, and the known limit on `pool_leave` with slice 1 (a rejoin of the same certificate stays refused until slice 3).
