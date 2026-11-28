# Limits

The limits in force, with their values, where each is set, what it protects and who changes it. A refusal at a node limit says which
limit it was, its value and the setting that holds it (`limits::refuse`); a refusal at a security floor says what was refused and what
to do and never the budget (`limits::floor`); `app/poolhouse-node/tests/quota_refusals.rs` fails for a refusal built any other way, for
a setting that does not exist and for one this table leaves out. A limit is kept for memory, disk or a hang; a rate that bounded only
abuse by one session is not kept (the owner never asked for one). The security floors are the owner's alone: an agent never loosens one.

**Nothing that needs the person's answer is limited by a timer, a count per hour or a quiet.** A request for the person (an approval
of grants, a phone's addition or removal, a move of a session, a prompt of the system) is shown and pushed when it is made and is open
until they answer or it runs out. What stands in place of a rate is visibility: a session has one request open at a time (the same
request again returns that one; another is refused with what waits and that the person is to answer it), `pool_status.waiting` lists
every open request, a decline or a withdrawal or a request nobody answered holds nothing back (the next one is made and pushed at once,
and a declined scope says so, with how often, on the next card), and the one refusal left is a block the person chose on a card, which
names its end to the agent. The owner's orders: "I should never silently have things like this hidden from me due to a 'quiet period'";
"anything that requires the user's response should not be suppressed, or we have an unfulfillable request".

## At a bound

- **A log** (`MAX_LOG_ROWS`, `MAX_LOG_BYTES`, `AUTHOR_SHARE_PERCENT`, `LINEAGE_SHARE_PERCENT`, `RESERVED_ROWS`, `RESERVED_BYTES`, `LAST_ROWS`,
  `LAST_BYTES`). A session writes at most a tenth of the rows and of the bytes of a device's log, and the sessions of one process tree
  (a lead, its subagents and the main sessions started from one terminal) a quarter between them; past either that session or tree is
  refused with the share named and every session of another tree still writes. A session's registration, and the return of a main
  session that ended, write an identity entry to its own share; a registration the log refuses leaves the registry as it was. Three
  stretches of the log are kept for what an agent cannot make at will: author rows stop `RESERVED_ROWS` rows (and `RESERVED_BYTES`
  bytes) before the end, which leaves room for the node's rows about a session's end (its identity entry, which carries who ended it and
  what that released, skipped when there is no room: the registry is the record of an ending); the last `LAST_ROWS` rows (and
  `LAST_BYTES` bytes) are for a head, so the log is still signed and synced, and for the rows a person's decision makes (a grant they
  signed or approved on the phone, a session they moved). A lease entry or a notice a full log refuses is skipped: the lease table is the
  record of a lease. A copy of another device's log that would pass the rows or the bytes is refused whole. Every count above is of
  the live segment: a log starts a new segment when it holds `ROTATE_PERCENT` of the rows or of the bytes. The segment is a base row, a
  carry envelope retaining the original operation identity, order and time of the device's own notes (not superseded, not expired), rooms, tasks and grants (grants always; the others up to
  `CARRY_PERCENT` of the rows and of the bytes, newest first, and the base says how many were left) and a head; the rows before the base
  go to `archive/` beside the log, and the shares, the reserve and the last stretch start again on the new segment. A copy takes a base
  only after a segment of at least `MIN_SEGMENT_PERCENT` of the rows or of the bytes (when it holds that segment), applies the segment
  once every restated row has arrived (a copy behind the base restarts at it only when the device that wrote the log sends it), and
  refuses a base that leaves out a grant revocation it holds. An archive is kept `ARCHIVE_MIN_MS` at
  least and `ARCHIVE_MAX_MS` at most, until every device that synced the board has acknowledged past it, and `MOST_ARCHIVES` of an origin and
  `MAX_LOG_BYTES` of them at most; a device behind the base is served from the archive while it exists and otherwise starts at the base. Messages, audit,
  landing, lease, identity and verify rows are not restated: they leave the view with the segment (`docs/notes/2026-10-19-log-rotation.md`).
- **Leases** (`max_leases_per_holder`, `max_leases_per_lineage`, `max_leases`, `max_worktree_records`). A request past any of the first three is refused with the
  limit; a reserve naming more resources than a session may hold is refused whole before any is looked at, and a reserve writes its
  lease entries to the board in one write, so it holds the node for a fraction of a second whatever its size. The leases of one
  process tree are counted together over every board. A quiet lease with no process behind it is kept `default_ttl_s` between renewals
  however long it asks. A worktree claim past `max_worktree_records` for its process tree is refused (its record is what the landing reviews read
  to find a branch's creator, so none is dropped to make room).
- **`write-limits.json`** names only the keys of the rows above. A file that still holds a removed key (`sends`, `child_sends`,
  `subagent_sends`, `window_s`, `announces`, `announce_window_s`) stops the node starting and names the key: delete the key.
- **Live main sessions** (`live_main_sessions`, 16). A board holds at most sixteen main sessions that have not ended: a tree's share of a
  log is a quarter (`LINEAGE_SHARE_PERCENT`), so four trees can write all of it, and sixteen is four trees of four terminals each. A
  registration that would make seventeen, or a main session that ended coming back into a full board, is refused with the limit named; an
  ending (or the node retiring an idle session) frees a place at once. The person's own session counts like any other while its terminal
  runs and is retired once every process it was registered from has ended. Subagents are not counted: their tree's share holds them. This is a
  count of what exists, not a rate: a process that forks twice is its own process tree with a share of its own, and the count keeps more
  than sixteen of them from writing to a board at once. A board with more terminals than that raises the key in `write-limits.json`.
- **Boards, rooms, subscriptions and waiting** (`MOST_BOARDS_PER_LINEAGE`, `board_grace_s`, `MOST_ROOMS_PER_LINEAGE`, `MEMBERS_PER_ROOM`,
  `MOST_SUBS`, `MOST_WAITERS_PER_LINEAGE`). They count what is held, not how fast it was made: a board is held by the process tree that
  made it by registering a session on it, for as long as it exists; a room while it exists; a subscription until it is removed or its
  session ends. A new board past `MAX_BOARDS`, or past the share of its tree, first removes the boards that hold nothing but the starts and
  ends of their sessions, left alone for `board_grace_s` (an hour). A board is removed only when no other device ever synced it (a board
  another device holds a copy of would be refused by that device when it is made again), it holds no message, note, room, grant, task or any
  other entry, no file and no flagged entry, no session of it is live or ended for cause, and no link or lease names it; a removed board is
  made again, empty, by the next registration. A device counts as having synced a board when rows moved between the two (a pull or a push
  that carried at least one row); a call that moved none, such as asking for a vector, ties nothing. The board is renamed to a trash name
  before its files are deleted, so a deletion that stops halfway leaves no live board; what is left under a trash name is removed when the
  node starts and before the next removal. A board with entries, or one another device synced, is never removed: `HANDOFF.md` lists the
  rest. A new subscription at the bound first drops those to subagents that ended. A `lease_wait` holds a server thread for as long as it
  waits: a device answers `MOST_WAITERS` at once and the processes of one process tree `MOST_WAITERS_PER_LINEAGE` of them.
- **Accepted: a process tree is the unit of the per-tree bounds.** The bounds above that say "process tree" (boards, rooms, leases,
  waiters, the share of a log) read the tree from the operating system by the process id of the socket. A process that forks twice and
  is left to the system's first process is its own tree each time, so each one starts with a fresh share; and where the operating system
  does not report the process (Windows) a main session and its subagents are the unit instead. The bounds that hold whatever the tree
  (`MAX_BOARDS`, `MAX_LOG_ROWS`, `MAX_LOG_BYTES`, `max_leases`, `MOST_WAITERS`, `MAX_IDENTITIES`) are what stop a node, not a
  tree, from being filled that way: `live_main_sessions` holds how many such trees a board has live at once, but a process that ends its
  session and registers a new one in a fresh tree, one after the other, still writes a share each time until the log is full. The owner
  accepts this or asks for a stricter unit.
- **Accepted: a process more than `MOST_ANCHORS` deep shifts its tree.** The tree is read as the processes above the requester up to
  `MOST_ANCHORS` of them, so a process started more than that many levels below the terminal that began the work is read with a different
  key than its siblings and counts as a tree of its own. It is the same fresh share as a double fork, bounded by `live_main_sessions` live
  main sessions per board and by the bounds that hold whatever the tree.

| Name | Value | File and line | What it protects | Who changes it |
|---|---|---|---|---|
| `MAX_BOARDS` | 256 | `app/poolhouse-node/src/node.rs:38` | boards a node hosts: memory and open files | the owner, in source |
| `MOST_BOARDS_PER_LINEAGE` | 32 | `app/poolhouse-node/src/node.rs:40` | boards one process tree made by registering a session on them, that still exist: memory and open files | the owner, in source |
| `live_main_sessions` | 16 | `app/poolhouse-node/src/writes.rs:34` | main sessions a board holds that have not ended: four process trees write a log, each with four terminals | the owner, `<state>/write-limits.json` |
| `board_grace_s` | 3600 | `app/poolhouse-node/src/writes.rs:32` | seconds a board that holds nothing but the starts and ends of its sessions is left alone before a new board may remove it | the owner, `<state>/write-limits.json` |
| `MAX_IDENTITIES` | 10_000 | `app/poolhouse-node/src/identity.rs:16` | sessions a board holds: memory | the owner, in source |
| `CHALLENGE_TTL` | 30 s | `app/poolhouse-node/src/root_auth.rs` | lifetime of one connection's root registration challenge; a response is used once | the owner, in source |
| `SHORT` | 6 | `app/poolhouse-node/src/identity.rs:15` | characters of the shortest name a session is given: when every length is taken no name is left | the owner, in source |
| `MAX_TOKENS_PER_NAME` | 8 | `app/poolhouse-node/src/identity.rs:17` | tokens one session holds: the ninth ends the oldest | the owner, in source |
| `MOST_TOKENS` | 2048 | `app/poolhouse-node/src/identity.rs:19` | tokens a node holds: memory; when full the tokens of sessions idle for `IDLE_MS` end | the owner, in source |
| `IDLE_MS` | 30 * 24 * 3600 * 1000 | `app/poolhouse-node/src/identity.rs:20` | how long a session may be silent before its token can be dropped to make room | the owner, in source |
| `MAX_LOG_ROWS` | 200_000 | `app/poolhouse-node/src/logbounds.rs:8` | rows one device's log keeps: memory and disk of a board | the owner, in source |
| `MAX_LOG_BYTES` | 128 * 1024 * 1024 | `app/poolhouse-node/src/logbounds.rs:10` | bytes of rows one device's log keeps: memory and disk of a board | the owner, in source |
| `RESERVED_ROWS` | 2_000 | `app/poolhouse-node/src/logbounds.rs:12` | rows at the end of the log only the node's own rows use: the identity entry and the audit of a session ending, and the last `LAST_ROWS` of them for a head and a person's decisions | the owner, in source |
| `RESERVED_BYTES` | 8 * 1024 * 1024 | `app/poolhouse-node/src/logbounds.rs:14` | bytes at the end of the log only the node's own rows use | the owner, in source |
| `LAST_ROWS` | 500 | `app/poolhouse-node/src/logbounds.rs:16` | rows at the very end of the log only a head and a person's decisions use | the owner, in source |
| `LAST_BYTES` | 2 * 1024 * 1024 | `app/poolhouse-node/src/logbounds.rs:18` | bytes at the very end of the log only a head and a person's decisions use | the owner, in source |
| `AUTHOR_SHARE_PERCENT` | 10 | `app/poolhouse-node/src/logbounds.rs:20` | the share of the rows and of the bytes one session writes: one session cannot fill the log for the others | the owner, in source |
| `LINEAGE_SHARE_PERCENT` | 25 | `app/poolhouse-node/src/logbounds.rs:22` | the share of the rows and of the bytes the sessions of one process tree write together: a lead with its subagents cannot fill the log for other trees | the owner, in source |
| `ROTATE_PERCENT` | 75 | `app/poolhouse-node/src/logbounds.rs:24` | the share of the rows or of the bytes of a log at which the device starts a new segment: a full log takes nothing and would never recover | the owner, in source |
| `MIN_SEGMENT_PERCENT` | 50 | `app/poolhouse-node/src/logbounds.rs:26` | the least a copy holds of a segment before it takes a base that ends it: a member cannot make every copy rewrite its files on each row | the owner, in source |
| `CARRY_PERCENT` | 25 | `app/poolhouse-node/src/logbounds.rs:28` | the share of the rows and of the bytes of a log the restated notes, rooms and tasks of a new segment may use, and the base a copy takes may restate half the rows at most | the owner, in source |
| `MOST_ARCHIVES` | 8 | `app/poolhouse-node/src/logbounds.rs:30` | archived segments one origin keeps: disk | the owner, in source |
| `ARCHIVE_MIN_MS` | 7 * 24 * 3600 * 1000 | `app/poolhouse-node/src/logbounds.rs:32` | how long an archived segment is kept at least, so a device behind the base can be served from it | the owner, in source |
| `ARCHIVE_MAX_MS` | 30 * 24 * 3600 * 1000 | `app/poolhouse-node/src/logbounds.rs:34` | how long an archived segment is kept at most, whoever has not acknowledged past it: disk | the owner, in source |
| `MAX_ORIGINS` | 64 | `app/poolhouse-node/src/board.rs:19` | logs a device holds for one board: memory | the owner, in source |
| `MAX_REJECTED` | 200 | `app/poolhouse-node/src/board.rs:20` | refused foreign rows kept for `status`: memory | the owner, in source |
| `MAX_ROW_BYTES` | 128 * 1024 | `app/poolhouse-node/src/row.rs:14` | the size of one signed row | the owner, in source |
| `BODY_BYTES` | 64 * 1024 | `app/poolhouse-node/src/fold.rs:32` | the size of a row's body | the owner, in source |
| `NOTES_PER_SENDER` | 200 | `app/poolhouse-node/src/fold.rs:33` | notes folded for one sender: the fold's memory | the owner, in source |
| `READ_MAX` | 1000 | `app/poolhouse-node/src/node.rs:36` | entries one `read` returns | the owner, in source |
| `inbox_pending` | 500 | `app/poolhouse-node/src/writes.rs:23` | direct messages unread in a session's inbox: memory of the log and the reader's context | the owner, `<state>/write-limits.json` |
| `unread_per_sender` | 100 | `app/poolhouse-node/src/writes.rs:24` | direct messages unread from one sender: one sender cannot fill an inbox | the owner, `<state>/write-limits.json` |
| `notes_per_author` | 500 | `app/poolhouse-node/src/writes.rs:26` | notes one author keeps on a board: memory | the owner, `<state>/write-limits.json` |
| `child_beat_s` | 1800 | `app/poolhouse-node/src/writes.rs:28` | seconds a session may go unheard before it is idle, unless it holds a live lease | the owner, `<state>/write-limits.json` |
| `idle_retire_s` | 86 400 | `app/poolhouse-node/src/writes.rs:30` | seconds a session may be idle before the node retires it and its token ends | the owner, `<state>/write-limits.json` |
| `MOST_ACTIVE` | 64 | `app/poolhouse-node/src/links.rs:15` | links one board has open | the owner, in source |
| `MAX_PROJECTS` | 256 | `app/poolhouse-node/src/registry.rs:23` | projects a node holds | the owner, in source |
| `MAX_SOURCES` | 32 | `app/poolhouse-node/src/registry.rs:21` | places one project keeps | the owner, in source |
| `SCAN_DEPTH` | 3 | `app/poolhouse-node/src/registry.rs:69` | how deep a place is searched for a git repository: a walk that cannot run away | the owner, in source |
| `SCAN_ENTRIES` | 4000 | `app/poolhouse-node/src/registry.rs:70` | entries read by that search | the owner, in source |
| `ROOMS_TOTAL` | 200 | `app/poolhouse-node/src/rooms.rs:19` | rooms one board holds: every read folds them | the owner, in source |
| `MOST_ROOMS_PER_LINEAGE` | 16 | `app/poolhouse-node/src/rooms.rs:21` | rooms the sessions of one process tree made | the owner, in source |
| `MEMBERS_PER_ROOM` | 128 | `app/poolhouse-node/src/rooms.rs:23` | members of one room: every read folds them | the owner, in source |
| `MOST_SUBS` | 256 | `app/poolhouse-node/src/subs.rs:27` | subscriptions one session keeps: `subs.json` is written whole on each change | the owner, in source |
| `LOUD_FREE` | 3 | `app/poolhouse-node/src/subs.rs:25` | inbox subscriptions a session takes before it must say `force`: its context, not memory | the owner, in source |
| `MAX_BYTES` | 256 * 1024 * 1024 | `app/poolhouse-node/src/scratch.rs:18` | bytes one session keeps in scratch folders: disk | the owner, in source |
| `MOST_FOLDERS` | 16 | `app/poolhouse-node/src/scratch.rs:20` | scratch folders one session keeps: disk | the owner, in source |
| `TTL_S` | 3 * 86_400 | `app/poolhouse-node/src/scratch.rs:19` | seconds a scratch folder lives: disk | the owner, in source |
| `MAX_BLOB` | 9 * 1024 * 1024 | `app/poolhouse-node/src/files.rs:25` | bytes of one file on a board: disk and memory while it is sealed | the owner, in source |
| `CHUNK` | 256 * 1024 | `app/poolhouse-node/src/files.rs:27` | bytes of one chunk of an upload | the owner, in source |
| `MAX_STAGED` | 8 | `app/poolhouse-node/src/files.rs:28` | uploads waiting to be finished: disk | the owner, in source |
| `MAX_ITEMS` | 5000 | `app/poolhouse-node/src/quarantine.rs:25` | flagged entries a device holds: memory | the owner, in source |
| `MAX_PER_CALL` | 100 | `app/poolhouse-node/src/quarantine.rs:26` | entries one quarantine call takes | the owner, in source |
| `MAX_TASKS` | 5000 | `app/poolhouse-node/src/tasks/mod.rs:25` | tasks on a board: memory of the task fold | the owner, in source |
| `HEARTBEAT_S` | 120 | `app/poolhouse-node/src/tasks/mod.rs:24` | seconds a task claim lives without a heartbeat: a dead worker's task is freed | the owner, in source |
| `MAX_RESOURCES` | 8 | `app/poolhouse-node/src/lease/types.rs:14` | resources one lease request names | the owner, in source |
| `max_leases_per_holder` | 512 | `app/poolhouse-node/src/lease/types.rs:253` | leases, held or queued, one session keeps: the table is swept whole on every lease call | the owner, `<state>/lease-config.json` |
| `max_leases_per_lineage` | 1024 | `app/poolhouse-node/src/lease/types.rs:257` | leases, held or queued, the sessions of one process tree keep together, over every board: one tree cannot fill the table | the owner, `<state>/lease-config.json` |
| `max_leases` | 4096 | `app/poolhouse-node/src/lease/types.rs:255` | leases, held or queued, the table keeps | the owner, `<state>/lease-config.json` |
| `cpu_slots` | the device's cores | `app/poolhouse-node/src/lease/types.rs:239` | CPU slots the table grants and the most one request asks for | the owner, `<state>/lease-config.json` |
| `memory_mb` | 0 (no budget) | `app/poolhouse-node/src/lease/types.rs:241` | megabytes the table grants and the most one request asks for | the owner, `<state>/lease-config.json` |
| `MAX_TAG` | 64 | `app/poolhouse-node/src/lease/types.rs:16` | length of a lease tag | the owner, in source |
| `max_ttl_s` | 86 400 | `app/poolhouse-node/src/lease/types.rs:251` | seconds one renewal keeps a lease; a quiet lease with no pid is kept `default_ttl_s` instead: a dead holder without a pid frees its resource | the owner, `<state>/lease-config.json` |
| `MOST_HOLDERS` | 8192 | `app/poolhouse-node/src/lease/holders.rs:16` | records of worktree claims (`worktree-holders.json`, which they write whole when one is new) a device holds; a new path past it is refused, and records are retained without age-out | the owner, in source |
| `max_worktree_records` | 1024 | `app/poolhouse-node/src/lease/types.rs:273` | worktree claims, by path and holder, the sessions of one process tree have on record: a session cannot push its record out by claiming more paths; a new path past it is refused | the owner, `<state>/lease-config.json` |
| `MOST_WAITERS` | 64 | `app/poolhouse-node/src/lease/rpc.rs:25` | `lease_wait` requests a node holds a thread for at once | the owner, in source |
| `MOST_WAITERS_PER_LINEAGE` | 16 | `app/poolhouse-node/src/lease/rpc.rs:27` | `lease_wait` requests the processes of one process tree hold a thread for at once: one tree cannot take every place | the owner, in source |
| `MAX_WAIT_MS` | 600_000 | `app/poolhouse-node/src/lease/rpc.rs:23` | milliseconds a `lease_wait` holds a server thread and keeps its queued request alive: a client that left would otherwise hold both for ever | the owner, in source |
| `MOST_ANCHORS` | 32 | `app/poolhouse-node/src/lineage.rs:23` | processes read above a requester when its lineage is found | the owner, in source |
| `MAX_FRAME` | 1024 * 1024 | `app/poolhouse-node/src/wire.rs:10` | bytes of one frame on the local socket | the owner, in source |
| `MAX_PEER_FRAME` | 4 * 1024 * 1024 | `app/poolhouse-node/src/wire.rs:13` | bytes of one frame between devices | the owner, in source |
| `MAX_BATCH_BYTES` | 2 * 1024 * 1024 | `app/poolhouse-node/src/sync.rs:15` | bytes one sync pull carries | the owner, in source |
| `MAX_ACT_BYTES` | 16 * 1024 | `app/poolhouse-node/src/gov/mod.rs:42` | bytes of one governance act | the owner, in source |
| `PAGE_BYTES` | 2 * 1024 * 1024 | `app/poolhouse-node/src/gov/mod.rs:45` | bytes of one page of acts | the owner, in source |
| `ADMIN_ACTS_PER_KEYS` | 16 | `app/poolhouse-node/src/gov/mod.rs:53` | admin acts held per set of keys: memory of the pool log | the owner, in source |
| `MOST_JUNK_PER_DEVICE` | 32 | `app/poolhouse-node/src/gov/room.rs:16` | acts of no kind held for one device: a member cannot fill the pool log | the owner, in source |
| `MOST_LEAVES_PER_DEVICE` | 8 | `app/poolhouse-node/src/gov/room.rs:17` | leave acts held for one device | the owner, in source |
| `MOST_LOGGED_PER_ORIGIN` | 500 | `app/poolhouse-node/src/grants/calls.rs:18` | grant uses logged per origin: memory | the owner, in source |
| `MOST_KEYS` | 8 | `app/poolhouse-node/src/grants/person.rs:18` | person keys a node holds, phones included | the owner, in source; a security floor no agent loosens |
| `MOST_NONCES` | 256 | `app/poolhouse-node/src/grants/person.rs:20` | signature nonces remembered, so a signed request cannot be replayed | the owner, in source; a security floor no agent loosens |
| `MAX_SPAN_MS` | 366 * 24 * 3600 * 1000 | `app/poolhouse-node/src/grants/spec.rs:23` | how long one grant may last | the owner, in source; a security floor no agent loosens |
| `SKEW_MS` | 5 * 60 * 1000 | `app/poolhouse-node/src/grants/spec.rs:25` | clock difference tolerated when a grant is checked | the owner, in source; a security floor no agent loosens |
| `SKEW_MAX_MS` | 300_000 | `app/poolhouse-node/src/rules.rs:12` | clock difference tolerated for a foreign row | the owner, in source |
| `MAX_AGE_MS` | 60_000 | `app/poolhouse-node/src/beacon.rs:29` | age of a beacon that is still believed | the owner, in source |
| `JOIN_BYTES` | 256 * 1024 | `app/poolhouse-node/src/peer.rs:74` | bytes of a join request | the owner, in source |
| `HANDSHAKE_FOR` | 10 s | `app/poolhouse-node/src/tls.rs:26` | a peer's whole handshake: a host that stalls or trickles it does not keep a connection; the node's own wait for its lock is not counted | the owner, in source |
| `STRANGER_FOR` | 30 s | `app/poolhouse-node/src/peer.rs:41` | the whole connection of a device the pool does not list, after its handshake | the owner, in source |
| `MOST_PEER_CONNECTIONS` | 128 | `app/poolhouse-node/src/net.rs:38` | connections the peer listener holds together: descriptors | the owner, in source |
| `MOST_PEER_PER_ADDRESS` | 48 | `app/poolhouse-node/src/net.rs:40` | connections one address holds on the peer listener; at a member's address a new one then takes the place of the oldest that has not kept its own. A connection keeps its place once the node has checked a member's certificate on it after the handshake, or just before it admits a device on it by a join; until then, a member's own may be hung up too | the owner, in source |
| `PEER_RESERVED` | 32 | `app/poolhouse-node/src/net.rs:42` | the last peer connections, for members' addresses: one a member connected from, or was reached or recorded at, in the last hour (a recorded one counts from the node's start), forgotten when the last member there is removed | the owner, in source |
| `MOST_LEAVING` | 16 | `app/poolhouse-node/src/gate.rs:20` | connections hung up to make room that have yet to end: the most a listener passes its total by | the owner, in source |
| `ALERT_LINGER` | 5 s | `app/poolhouse-node/src/tls.rs:29` | how long a refused connection is drained so the other end reads why | the owner, in source |
| `MOST_DRAINED` | 64 * 1024 | `app/poolhouse-node/src/tls.rs:31` | bytes drained from one refused connection | the owner, in source |
| `MOST_LINGERING` | 32 | `app/poolhouse-node/src/net.rs:44` | refused connections drained at once; one past it is closed straight away | the owner, in source |
| `MOST_RUNNING` | 2 | `app/poolhouse-node/src/shard/host.rs:31` | shards running at once on a device: CPU | the owner, in source |
| `MOST_OPEN` | 6 | `app/poolhouse-node/src/shard/host.rs:32` | shards a device holds open: disk | the owner, in source |
| `MOST_KEPT` | 40 | `app/poolhouse-node/src/shard/host.rs:33` | finished shards kept: disk | the owner, in source |
| `MOST_RESULT` | 900 << 10 | `app/poolhouse-node/src/shard/host.rs:35` | bytes of one shard result | the owner, in source |
| `STALE_UPLOAD` | Duration::from_secs(600) | `app/poolhouse-node/src/shard/host.rs:36` | an upload with no chunk for this long is dropped: disk | the owner, in source |
| `GRACE_S` | 120 | `app/poolhouse-node/src/shard/host.rs:38` | seconds a shard gets to stop before it is killed | the owner, in source |
| `KILL_AFTER` | Duration::from_secs(45) | `app/poolhouse-node/src/shard/host.rs:39` | seconds after a stop request that a shard is killed | the owner, in source |
| `MOST_KEYS (rates)` | 1024 | `app/poolhouse-node/src/limits.rs:10` | keys the in-memory rate table holds; a new key past it drops the least recently used | the owner, in source |
| `OPEN_JOINS_PER_SOURCE` | 5 | `app/poolhouse-node/src/peer.rs:87` | open-pool joins from one address in `OPEN_JOIN_WINDOW`: a flood of strangers asking to join | the owner, in source; a security floor no agent loosens |
| `OPEN_JOINS_PER_POOL` | 10 | `app/poolhouse-node/src/peer.rs:88` | open-pool joins in all in `OPEN_JOIN_WINDOW` | the owner, in source; a security floor no agent loosens |
| `OPEN_JOIN_WINDOW` | 600 s | `app/poolhouse-node/src/peer.rs:89` | the window of the two open join limits | the owner, in source; a security floor no agent loosens |
| `WRONG_CODES` | 10 | `app/poolhouse-node/src/phone/enrol.rs:34` | wrong phone codes from one address in `WRONG_WINDOW`: guessing a code | the owner, in source; a security floor no agent loosens |
| `WRONG_WINDOW` | 600 s | `app/poolhouse-node/src/phone/enrol.rs:35` | the window of the wrong code limit | the owner, in source; a security floor no agent loosens |
| `OFFER_TTL_MS` | 10 * 60 * 1000 | `app/poolhouse-node/src/phone/enrol.rs:31` | how long a phone code lives | the owner, in source; a security floor no agent loosens |
| `MOST_PHONES` | 8 | `app/poolhouse-node/src/phone/mod.rs:38` | phones a device holds | the owner, in source; a security floor no agent loosens |
| `MOST_TRIES_PER_ADDRESS` | 60 | `app/poolhouse-node/src/phone/http.rs:30` | phone enrolment and decision tries from one address in a minute | the owner, in source; a security floor no agent loosens |
| `MOST_PER_ADDRESS` | 6 | `app/poolhouse-node/src/phone/http.rs:29` | connections one address holds on the phone endpoint | the owner, in source; a security floor no agent loosens |
| `MOST_PENDING` | 64 | `app/poolhouse-node/src/approvals/mod.rs:54` | approval requests waiting on a node: memory. Every one is on the person's phone and in `pool_status.waiting`; a request past it is told to have the person answer some | the owner, in source; a security floor no agent loosens |
| `MAX_TTL_MS` | 24 * 3600 * 1000 | `app/poolhouse-node/src/approvals/mod.rs:56` | the longest a request lives | the owner, in source; a security floor no agent loosens |
| `OPEN_ASKS_PER_ASKER` | 1 | `app/poolhouse-node/src/presence/firsts.rs:26` | prompts one asker has open for the person at once: it is told what is open; another asker is never blocked | the owner, in source |
| `ASK_FOR_MS` | 120_000 | `app/poolhouse-node/src/presence/mode.rs:8` | how long a question to the person stays open; running out counts against nothing and the question is raised again by asking again | the owner, in source; a security floor no agent loosens |
| `OPEN_PER_HOUR` | 5 | `app/poolhouse-node/src/gov/mod.rs:62` | open joins one member signs in an hour | the owner, in source; a security floor no agent loosens |
| `OPEN_POOL_PER_HOUR` | 10 | `app/poolhouse-node/src/gov/mod.rs:65` | open joins a pool takes in an hour | the owner, in source; a security floor no agent loosens |
| `DELEGATE_PER_HOUR` | 3 | `app/poolhouse-node/src/gov/mod.rs:60` | devices a delegate admits in an hour | the owner, in source; a security floor no agent loosens |
| `DELEGATE_PER_DAY` | 10 | `app/poolhouse-node/src/gov/mod.rs:61` | devices a delegate admits in a day | the owner, in source; a security floor no agent loosens |
| `pytest timeout` | 120 s a test | `pyproject.toml:268` | one test that hangs; a longer one carries `@pytest.mark.timeout(N)` | the owner |
| `CI test timeout` | 120 s a test | `.github/workflows/ci.yml:108` | the same limit on the pushed commit | the owner |
| `CI shard timeout` | 30 minutes | `.github/workflows/ci.yml:46` | one of the eight test shards that hangs | the owner |
| `REFUSALS` | 2 | `scripts/hooks/stop_landed.py:16` | stops of one subagent refused for work development lacks before it may stop; the stop hook's other refusal is a test job it could not cancel (`scripts/hooks/stop_jobs.py`) | the owner |
| `ci_watch WAIT_S` | 180 | `scripts/ci_watch.py:34` | seconds the watcher waits for a run to appear | the owner |
| `ci_watch POLL_S` | 60 | `scripts/ci_watch.py:35` | seconds between asks of GitHub while a run is watched | the owner |
| `ci_watch RUN_TIMEOUT_S` | 130 * 60 | `scripts/ci_watch.py:36` | seconds a run is watched before the watcher gives up | the owner |
| `ci_watch WARN_S` | 25 * 60 | `scripts/ci_watch.py:37` | seconds into a run when the watcher says it is slow | the owner |
| `ci_watch BLOCK_S` | 35 * 60 | `scripts/ci_watch.py:38` | seconds into a run when the watcher says it is stuck | the owner |
| `ci_watch MIN_WATCH_S` | 60.0 | `scripts/ci_watch.py:39` | the shortest a run is watched | the owner |
| `primary_sync WAIT_S` | 60.0 s | `scripts/primary_sync.py:29` | seconds the primary checkout waits for a lock before it skips a sync | the owner |
| `NOTE_COMMITS` | 10 | `scripts/worktrees:35` | a note printed under a worktree that is far ahead of or behind development; nothing is refused | the owner |
| `NOTE_AHEAD` | 30 | `scripts/worktrees:36` | a note printed under a worktree that is far ahead of or behind development; nothing is refused | the owner |
| `NOTE_BEHIND` | 20 | `scripts/worktrees:37` | a note printed under a worktree that is far ahead of or behind development; nothing is refused | the owner |
| `NOTE_AGE_BEHIND_DAYS` | 1.0 | `scripts/worktrees:38` | a note printed under a worktree that is far ahead of or behind development; nothing is refused | the owner |
| `queue_doctor QUIET_ALERT_S` | 900.0 s | `scripts/queue_doctor.py:17` | when the queue doctor tells the owner of a quiet, long or old test job; only a dead or ownerless job is ended | the owner |
| `queue_doctor ESTIMATE_ALERT_S` | 1200.0 s | `scripts/queue_doctor.py:18` | when the queue doctor tells the owner of a quiet, long or old test job; only a dead or ownerless job is ended | the owner |
| `queue_doctor OVERRUN_ALERT_S` | 1200.0 s | `scripts/queue_doctor.py:19` | when the queue doctor tells the owner of a quiet, long or old test job; only a dead or ownerless job is ended | the owner |
| `queue_doctor AGE_ALERT_S` | 5400.0 s | `scripts/queue_doctor.py:22` | when the queue doctor tells the owner of a quiet, long or old test job; only a dead or ownerless job is ended | the owner |
| `queue_doctor ADMIT_NOTICE_S` | 600.0 s | `scripts/queue_doctor.py:20` | when the queue doctor tells the owner of a quiet, long or old test job; only a dead or ownerless job is ended | the owner |
| `queue_doctor NODE_MAX_AGE_S` | 3600.0 s | `scripts/queue_doctor.py:24` | when the queue doctor tells the owner of a quiet, long or old test job; only a dead or ownerless job is ended | the owner |
| `queue_doctor GRACE_S` | 5.0 s | `scripts/queue_doctor.py:23` | when the queue doctor tells the owner of a quiet, long or old test job; only a dead or ownerless job is ended | the owner |
| `DEV_TEST_RESERVED_CORES` | 1 | `scripts/testslots.py:68` | logical cores the test budget leaves to the machine | the owner, in the environment |
| `DEV_TEST_BUDGET` | logical cores minus the reserved ones, at least 1 | `docs/test-execution.md:86` | CPU permits the test broker grants at once | the owner, in the environment |
| `hook timeout` | 15 s | `.claude/settings.json:61` | a harness hook that hangs | the owner |
| `parallel` (serving default) | 1 slot | `src/poolhouse/serve/backend.py` (`ServerSpec.parallel`) | the only fixed serving default: llama.cpp's own (`common_params.n_parallel`); slots beyond it are the caller's demand, `escalate` grows a running server | the person, `--parallel` |
| `context` (serving default) | none: 0 asks for the fit | `src/poolhouse/serve/fitted.py`, `serve/suggest.py::largest_fit` | derived, never defaulted: the model's header `context_length` (an MLX model's `max_position_embeddings`) as the ceiling, the memory the machine can give a model (rated yellow, below 95%) as the floor; no header or no `context_length` is refused naming the field | the person, `--context` |
| `PAD` | 256 | `src/poolhouse/serve/largest.py:17` | the step a context is searched in: llama.cpp pads each sequence to a multiple of it (`GGML_PAD(n_ctx, 256)`) | the owner, in source |
| `STEPS` | 2^9 to 2^20 | `src/poolhouse/serve/suggest.py:35` | the menu of the "bigger and tighter" alternative `models suggest` prints; not a limit | the owner, in source |
| `BATCHES` | 2048, 1024, 512 | `src/poolhouse/serve/suggest.py:37` | `-ub` values tried, largest first, kept while the memory rating holds: 2048 measured 4.7x on Flash-Next, 512 is llama.cpp's own | the owner, in source |
| `OFFLOADS` | 1.0 to 0.0 in quarters | `src/poolhouse/serve/suggest.py:40` | shares of a model's layers tried on a card that cannot hold it whole | the owner, in source |
| `MARGIN` | 0.10 | `src/poolhouse/bench/fitcontext.py:26` | the tenth of a measured fit record's longest context left unused, so a longer conversation than the one measured does not swap | the owner, in source |
| `CHECKPOINTS` | 32 | `src/poolhouse/serve/wired.py:37` | context checkpoints per slot llama-server allocates (its own `n_ctx_checkpoints`), counted in the wiring plan | llama.cpp |
| `CACHE_RAM_MB` | 8192 | `src/poolhouse/serve/wired.py:41` | the prompt cache llama-server holds in RAM (its own `cache_ram_mib`), counted in the wiring plan | llama.cpp |
| `CONTEXTS` | 8K to 1M | `src/poolhouse/serve/wired.py:27` | rows of the wiring table, a display to read across; never a choice | the owner, in source |
| shard id | 128 random bits | the full design (`git show ac0639291:docs/design-test-runner.md`) section 6 (`fleet/shard_spec.py` once built) | a shard id cannot be guessed or collide, so a replayed or cross-sent result is refused: a security floor, never loosened by an agent | a security floor of the owner |
| run id | 128 random bits | the full design (`git show ac0639291:docs/design-test-runner.md`) section 5.3 (`testfarm/jobs.py` once built) | a run id and the canary seed come from the operating system's entropy and cannot be derived from the tree or the arguments, so which cached passes a run re-executes cannot be known beforehand: a security floor, never loosened by an agent | a security floor of the owner |
| install-instance id | 128 random bits | the full design (`git show ac0639291:docs/design-test-runner.md`) section 6.2 (`testfarm/transports/pool_server.py` once built) | the id a runner makes when it is installed on a device and a sender pins with the member's fingerprint, so a copy of the key made without it answers as a second instance and receives no shard: a security floor, never loosened by an agent | a security floor of the owner |
| store master key | 256 random bits | the full design (`git show ac0639291:docs/design-test-runner.md`) section 5.3 (`testfarm/store.py` once built) | the key from which the runner derives each project's signing key, so a hostile repository cannot write or disable a pass, one project's key signs nothing of another's and no child of any run reads it in the turn it runs (a hostile named child can reach it one command later, the full design (`git show ac0639291:docs/design-test-runner.md`) section 10.9 item 7); a reader accepts only entries signed under its owner's project key: a security floor, never loosened by an agent | a security floor of the owner |
| control nonce | 128 random bits | the full design (`git show ac0639291:docs/design-test-runner.md`) sections 6.1 and 6.4 (`testfarm/executor.py` once built) | the value the sender makes for each shard and its control units echo, so a stale or mis-routed answer is refused: a security floor, never loosened by an agent | a security floor of the owner |
| frame nonce | 128 random bits | the full design (`git show ac0639291:docs/design-test-runner.md`) section 10.5 (`testfarm/report.py` once built) | the value in the markers of an output frame, so an end marker forged in a unit's text does not match: a security floor, never loosened by an agent | a security floor of the owner |
| model server slots | 1 (`--parallel 1`) | `docs/agent-policy.md` section 1 (`poolhouse.serve`) | slots of the llama-server a lease starts, including one the test runner delegates to a unit: two pieces of work on a device's compute at once void both numbers; the test runner starts no server and passes no slot count | the owner |
| `ASK_WAIT_S` | 180 s (`POOLHOUSE_LEAD_ASK_WAIT_S` sets another) | `src/poolhouse/lead_bridge.py` | how long an agent's question waits for its lead before it is told there is no answer yet, so a question never holds a run for ever; it is `serving.IDLE_SECONDS`, the project's one request timeout | the launching session, by the variable |
| tool timeout of the lead's tools | twice `ASK_WAIT_S` | `src/poolhouse/lead_bridge.py` (`tool_timeout`) | what a harness is told to wait for `ask` and `note`, above the bound of a question so the bridge's answer arrives while the harness listens; the factor leaves one whole bound for posting the question and the last look at the inbox | follows `ASK_WAIT_S` |
| `NO_PROGRESS_S` | 180 s | `src/poolhouse/delegate_confined.py` | a run of an editing delegate that prints nothing while neither the model server nor the lead has the turn and no shell command is within its timeout is ended (a hang); it is `serving.IDLE_SECONDS`, the project's one request timeout, and no measurement of a harness's longest silence stands behind it | the owner, in source |
| `QUIET_S` | 600 s, then twice as long each time | `src/poolhouse/delegate_notices.py` | a delegate's lead is told when its run has had no event for this long; a notice only, nothing is ended or held; chosen, with no measurement | the owner, in source |
| `MOST` notices of a delegate | 30 | `src/poolhouse/delegate_notices.py` | the messages one run sends its lead about itself, so a run that fails again and again does not fill the lead's inbox and the board's log; the last says no more follow, and the record holds the rest; chosen | the owner, in source |
| a delegate's record | outputs and messages left out past 16 MB, only how the run went past 24 MB, nothing past 32 MB | `src/poolhouse/delegate_record.py` (`FULL`, `SPARSE`, `LAST`) | the disk one run's `events.jsonl` takes, whatever a box prints; chosen, with no measurement | the owner, in source |
| a field of a delegate's record | a command 2000 characters, an output its last 60 lines and 4000 characters, a message 4000, other text 400 to 1500 | `src/poolhouse/delegate_record.py` (`LONG`, `SHORT`, `LINES`, `WINDOW`, `RUN`) | one event stays readable and a page of the record stays small; credentials are replaced in the first or last 32000 characters before the cut, and a run of more than 256 characters without a space is left out whole, so that no text makes the patterns slow | the owner, in source |
| `REPORT_CHARS` | 32,000 characters | `src/poolhouse/isolation/prompt_watch.py` | a request that rewrites history is said once it makes the model server read this much again and give up as much of a conversation it held (about 8,000 tokens), so a title or a first request is not; nothing is refused or changed; chosen, with no measurement | the owner, in source |
| `REREAD` | 32,000 tokens | `src/poolhouse/delegate_recorder.py` | a step that read at least this many tokens of its prompt without the server's cache while reusing less than half of the conversation the step before left is recorded and told to the lead; chosen, with no measurement | the owner, in source |
| `PROGRESS_EVERY_S` | 10 s | `src/poolhouse/isolation/model_bridge.py` | how often the bridge reads the model server's `/slots` while a request is with it: the server makes the reply between two batches of its work; chosen, with no measurement | the owner, in source |
| prompt cache of a coding session's server | four times the estimated KV cache of its window, at most a quarter of the machine's memory; 8192 MiB where that is less | `src/poolhouse/harnessing.py` (`prompt_cache_mb`) | room for two conversations beside the slot (45 KiB a token of saved state was measured where the KV cache was estimated at 25), so another request does not push a session's own out of it | the machine and the model, read when the server is started |
| `SHELL_DEFAULT_S` | 180 s | `src/poolhouse/isolation/environment.py` (`BASH_DEFAULT_TIMEOUT_MS`) | a shell command of a confined session that names no timeout is ended after it (a hang); it is `serving.IDLE_SECONDS`, the project's one request timeout, and no measurement of a test run stands behind it | the owner, in source |
| `SHELL_MAX_S` | 600 s | `src/poolhouse/isolation/environment.py` (`BASH_MAX_TIMEOUT_MS`) | the longest timeout a shell command may ask for, and so the longest it can hold a run with no progress: Claude Code's own default maximum (its settings documentation), set here so `delegate_confined` knows it | Claude Code's default; the owner, in source |
| output of one shell command | the served window's token count, in characters | `src/poolhouse/isolation/environment.py` (`BASH_MAX_OUTPUT_LENGTH`) | what the model is given of one command's output, so one command cannot fill its window: at the four bytes a token the session bridge allows a frame (`session_bridge`), about a quarter of the window; chosen, with no measurement | follows the served window |
| CPU time of a confined process | `IDLE_SECONDS` (180 s) times the cores, divided by live confinements (this one counted) plus one: 1440 s for one launch on a 16-core Mac | `src/poolhouse/isolation/bounds.py` (`derive`) | macOS: `RLIMIT_CPU` of every process of a confined launch; the parent ends the session when a process has used it, also one that ignores the signal | the machine, read at launch |
| open descriptors of a confinement | the smaller of `kern.maxfilesperproc` and the free file table (`kern.maxfiles` less `kern.num_files`) divided by the same sharers | `src/poolhouse/isolation/bounds.py` (`derive`) | macOS: `RLIMIT_NOFILE` of each process; the parent ends the session when its processes together hold that many | the machine, read at launch |
| memory of a confinement | the memory no lease holds of the node's budget (else what the machine reports available) divided by the same sharers | `src/poolhouse/isolation/bounds.py` (`derive`) | macOS: watched, since the kernel bounds no memory; past it the session ends | the machine and the node, read at launch |
| disk and file size of a confinement | the stage volume's free bytes divided by (`COPIES`, 5, times the same sharers) | `src/poolhouse/isolation/bounds.py` (`derive`, `COPIES`) | macOS: the stage and the agent home together are watched, and no one file may be larger (`RLIMIT_FSIZE`); past it the session ends | the volume, read at launch |
| watch interval of a confinement | 0.5 s (`serve._watchdog.POLL_S`), never less than the last look took | `src/poolhouse/isolation/bounds.py` (`Watch`) | how often the parent looks at a confinement's processes and writable trees | the owner, in source |
