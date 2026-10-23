# Limits

The limits in force, with their values, where each is set, what it protects and who changes it. A refusal at a node limit says which
limit it was, its value and the setting that holds it (`limits::refuse`); a refusal at a security floor says what was refused and what
to do and never the budget (`limits::floor`); `app/poolhouse-node/tests/quota_refusals.rs` fails for a refusal built any other way, for
a setting that does not exist and for one this table leaves out. A limit is kept for memory, disk or a hang; a rate that bounded only
abuse by one session is not kept (the owner never asked for one). The security floors are the owner's alone: an agent never loosens one.

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
| `JOIN_BYTES` | 256 * 1024 | `app/poolhouse-node/src/peer.rs:69` | bytes of a join request | the owner, in source |
| `MOST_RUNNING` | 2 | `app/poolhouse-node/src/shard/host.rs:31` | shards running at once on a device: CPU | the owner, in source |
| `MOST_OPEN` | 6 | `app/poolhouse-node/src/shard/host.rs:32` | shards a device holds open: disk | the owner, in source |
| `MOST_KEPT` | 40 | `app/poolhouse-node/src/shard/host.rs:33` | finished shards kept: disk | the owner, in source |
| `MOST_RESULT` | 900 << 10 | `app/poolhouse-node/src/shard/host.rs:35` | bytes of one shard result | the owner, in source |
| `STALE_UPLOAD` | Duration::from_secs(600) | `app/poolhouse-node/src/shard/host.rs:36` | an upload with no chunk for this long is dropped: disk | the owner, in source |
| `GRACE_S` | 120 | `app/poolhouse-node/src/shard/host.rs:38` | seconds a shard gets to stop before it is killed | the owner, in source |
| `KILL_AFTER` | Duration::from_secs(45) | `app/poolhouse-node/src/shard/host.rs:39` | seconds after a stop request that a shard is killed | the owner, in source |
| `MOST_KEYS (rates)` | 1024 | `app/poolhouse-node/src/limits.rs:10` | keys the in-memory rate table holds; a new key past it drops the least recently used | the owner, in source |
| `OPEN_JOINS_PER_SOURCE` | 5 | `app/poolhouse-node/src/peer.rs:82` | open-pool joins from one address in `OPEN_JOIN_WINDOW`: a flood of strangers asking to join | the owner, in source; a security floor no agent loosens |
| `OPEN_JOINS_PER_POOL` | 10 | `app/poolhouse-node/src/peer.rs:83` | open-pool joins in all in `OPEN_JOIN_WINDOW` | the owner, in source; a security floor no agent loosens |
| `OPEN_JOIN_WINDOW` | 600 s | `app/poolhouse-node/src/peer.rs:84` | the window of the two open join limits | the owner, in source; a security floor no agent loosens |
| `WRONG_CODES` | 10 | `app/poolhouse-node/src/phone/enrol.rs:34` | wrong phone codes from one address in `WRONG_WINDOW`: guessing a code | the owner, in source; a security floor no agent loosens |
| `WRONG_WINDOW` | 600 s | `app/poolhouse-node/src/phone/enrol.rs:35` | the window of the wrong code limit | the owner, in source; a security floor no agent loosens |
| `OFFER_TTL_MS` | 10 * 60 * 1000 | `app/poolhouse-node/src/phone/enrol.rs:31` | how long a phone code lives | the owner, in source; a security floor no agent loosens |
| `MOST_PHONES` | 8 | `app/poolhouse-node/src/phone/mod.rs:38` | phones a device holds | the owner, in source; a security floor no agent loosens |
| `MOST_OFFERS_PER_HOUR` | 3 | `app/poolhouse-node/src/phone/calls.rs:14` | phone codes one session asks for in an hour | the owner, in source; a security floor no agent loosens |
| `MOST_REMOVALS_PER_HOUR` | 6 | `app/poolhouse-node/src/phone/calls.rs:15` | requests one session makes to remove phones in an hour | the owner, in source; a security floor no agent loosens |
| `MOST_CODES_PER_PHONE` | 3 | `app/poolhouse-node/src/phone/http.rs:29` | phone codes one phone asks for in an hour | the owner, in source; a security floor no agent loosens |
| `MOST_TRIES_PER_ADDRESS` | 60 | `app/poolhouse-node/src/phone/http.rs:30` | phone enrolment and decision tries from one address in a minute | the owner, in source; a security floor no agent loosens |
| `MOST_PER_ADDRESS` | 6 | `app/poolhouse-node/src/phone/http.rs:28` | connections one address holds on the phone endpoint | the owner, in source; a security floor no agent loosens |
| `MOST_PENDING` | 64 | `app/poolhouse-node/src/approvals/mod.rs:52` | approval requests waiting on a node | the owner, in source; a security floor no agent loosens |
| `MOST_PENDING_PER_SESSION` | 8 | `app/poolhouse-node/src/approvals/mod.rs:53` | approval requests waiting from one session | the owner, in source; a security floor no agent loosens |
| `MOST_REQUESTS` | 6 | `app/poolhouse-node/src/approvals/mod.rs:54` | approval requests one session makes in `REQUEST_WINDOW_MS`: asking until the person gives in | the owner, in source; a security floor no agent loosens |
| `REQUEST_WINDOW_MS` | 10 * 60 * 1000 | `app/poolhouse-node/src/approvals/mod.rs:55` | the window of the request limit | the owner, in source; a security floor no agent loosens |
| `MAX_TTL_MS` | 24 * 3600 * 1000 | `app/poolhouse-node/src/approvals/mod.rs:57` | the longest a request lives | the owner, in source; a security floor no agent loosens |
| `backoff_ms` | 30 000 | `app/poolhouse-node/src/approvals/fatigue.rs:35` | how long after a decline the same kind of request is refused | the owner, in source; a security floor no agent loosens |
| `most_held` | 8 | `app/poolhouse-node/src/approvals/fatigue.rs:56` | requests held back for one requester | the owner, in source; a security floor no agent loosens |
| `reask_ms` | 600 000 | `app/poolhouse-node/src/approvals/fatigue.rs:54` | how long a cancelled or expired request counts as asked again | the owner, in source; a security floor no agent loosens |
| `QUIET_MS` | 10 * 60_000 | `app/poolhouse-node/src/presence/mod.rs:31` | the ask cooldown: no new question to the person this long after one was not answered yes | the owner, in source; a security floor no agent loosens |
| `ASK_FOR_MS` | 120_000 | `app/poolhouse-node/src/presence/mod.rs:28` | how long a question to the person stays open | the owner, in source; a security floor no agent loosens |
| `COOLDOWN` | std::time::Duration::from_secs(10 * 60) | `app/poolhouse-node/src/gov/ops.rs:30` | the ask cooldown: after a no the same kind of ask waits this long, and the window of `MOST_ASKS` | the owner, in source; a security floor no agent loosens |
| `MOST_ASKS` | 12 | `app/poolhouse-node/src/gov/ops.rs:31` | times a pool asks the person in `COOLDOWN`: asking until the person accepts from exhaustion | the owner, in source; a security floor no agent loosens |
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
| `ci_watch RUN_TIMEOUT_S` | 100 * 60 | `scripts/ci_watch.py:36` | seconds a run is watched before the watcher gives up | the owner |
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
| shard id | 128 random bits | `docs/design-test-runner.md` section 6 (`fleet/shard_spec.py` once built) | a shard id cannot be guessed or collide, so a replayed or cross-sent result is refused: a security floor, never loosened by an agent | a security floor of the owner |
| run id | 128 random bits | `docs/design-test-runner.md` section 5.3 (`testfarm/jobs.py` once built) | a run id and the canary seed come from the operating system's entropy and cannot be derived from the tree or the arguments, so which cached passes a run re-executes cannot be known beforehand: a security floor, never loosened by an agent | a security floor of the owner |
| install-instance id | 128 random bits | `docs/design-test-runner.md` section 6.2 (`testfarm/transports/pool_server.py` once built) | the id a runner makes when it is installed on a device and a sender pins with the member's fingerprint, so a clone that holds the key answers as a second instance and receives no shard: a security floor, never loosened by an agent | a security floor of the owner |
| store signing key | 256 random bits | `docs/design-test-runner.md` section 5.3 (`testfarm/store.py` once built) | the key under which the reuse store signs every entry, so a hostile repository cannot write or disable a pass, and a reader accepts only entries signed with its owner's key: a security floor, never loosened by an agent | a security floor of the owner |
| model server slots | 1 (`--parallel 1`) | `docs/agent-policy.md` section 1 and `docs/design-test-runner.md` section 5.5 (`poolhouse.serve` once changed) | slots of the llama-server a lease the test runner delegates to a unit starts: two pieces of work on a device's compute at once void both numbers; the runner never raises it | the owner |
