# The node

One `poolhouse-node` runs per device. It holds the boards that device takes part in, gives every
session one unique name per board, stamps every write from the session's token, and answers a
local API on a Unix socket (a named pipe on Windows). It is the Rust crate `app/poolhouse-node` (lib and bin) in the cargo
workspace `app/`; the Tauri app (`app/src-tauri`) is the other member and depends on the lib.

This is slices A and B1 of the node refactor: the crate (boards, identity, local socket, in-process
sync) and its network side (peer transport, membership, board sync over it, discovery, joining),
built and tested alone. The shipping section below puts it in every runtime and keeps it running; no Python client calls its board methods yet (see "What is left").

## Boards, projects, links

- **One pool of devices, many boards.** A board belongs to one project. Every entry carries its
  board id, and the id is part of the hash, so an entry cannot be moved to another board.
- **Hosting.** The node hosts any number of boards, each in its own directory
  `<state>/boards/<board>/` with its own logs, its own origin id and its own names. A session
  registers on its project's board; names, claims, reads and posts are all per board. A device
  only syncs boards it holds.
- **Projects.** The node keeps a project registry (`projects.json`): project id (= board id) and
  its sources, each `git`, `folder`, `gdrive` or `onedrive` with a local path (the sync folder for
  the last two). A client resolves its board with `project_resolve {path}`: the deepest source
  containing the path wins; a linked git worktree resolves through the repository's common
  directory, so every worktree of a repo is the same project; a directory that matches nothing
  gets `denied: this directory is not part of a project`, never another project's board. A git
  source is stored as the repository's main working tree. One place cannot belong to two projects.
  `.poolhouse/project.toml` in a repo (`board = "id"`) is an optional hint only
  (`project::propose_id`, `project::init_project`); it is never required.
  `--board ID` still names a board directly, and is checked against the session's registration.
- **Links.** Nothing crosses between boards except through a link: a session of board `from`
  makes `link {to, channels, mode}` and the sessions of `to` may then use the named channels (none
  named = the whole board) of `from`, read-only (`ro`) or read-write (`rw`). Channels are
  `#general`, `#announcements` (messages by their `to`), `#notes` (notes and their verifications),
  `#leases` (claims), `#identity`, `#audit`, `#grants`. Messages to one session are on `#dm`, which a link never
  shares (see "Direct messages"). A linked session writes as `name@its-board`. `unlink {id}` revokes it at once. Making
  and revoking a link writes an audit entry on both boards. Links are node-local for now, and any
  session of the sharing board may make one; the owner-only grant arrives with the trust ledger.
- Unknown board, a token of another board without a link, and a write over a read-only link are all
  `denied`.

## Board entries

Each device appends to one signed log per origin per board: `<state>/boards/<board>/log/<origin>.jsonl`,
one JSON row per line, mode 0600 in 0700 directories. Only this device's own origin file is ever
appended to; the others are verified copies of other devices' logs.

A row: `v` (schema version, 1), `board`, `origin` (32 lower-case hex), `seq`, `prev` (hash of the
row before), `hlc` (wall ms, counter, origin), `kind`, `actor`, `idem`, `body`, `hash`
(SHA-256 of `prev` plus the canonical row without its hash). Unknown row fields do not parse.
Bodies hold integers, text, booleans, arrays and objects; fractions are refused so the hash never
depends on a float format.

Kinds: `message`, `note`, `verify`, `identity`, `lease`, `audit`, `landing`, `room`, `task` and `grant` are folded into the view now;
`reputation_event` is in the schema so a later log still parses, and a foreign entry of that kind is not shown yet. `head` is a signature row, never shown.

- **Crash safety.** Append is one write plus `fsync`; it returns when the row is on disk. On open, a
  last line without its newline was never acknowledged and is cut off (a torn write from `kill -9`);
  damage anywhere else is an error, never a silent drop.
- **Heads.** A head row signs (Ed25519, device key, `device.key` 0600) the row directly before it.
  Only rows through the last verified head are given to peers or stored from peers. A pool of one
  never needs to sign.
- **Merge.** All rows sort by (wall, counter, origin, seq): one total order for any delivery order,
  with a row delivered twice kept once and a repeated `idem` key of one actor on one origin kept at
  its first row. Foreign rows dated more than five minutes ahead are held back from the view.
- **Foreign rows** become entries only through explicit field allow-lists per kind, each field
  checked for type and size; the sender is the log's actor (a `from`/`author`/`name` that differs is
  refused), must be a usable name and must not be a local session's name, and is shown as
  `actor@dN`. Posts target `#general` or `#announcements` (with announcement types only). Notes per
  foreign sender are capped. Refusals are recorded (the latest 200) and shown in `status`.
- **Quotas.** 64 origins per board, 200,000 rows per log, 128 KiB per row, 64 KiB per body text.
- **Damage.** A forged, forked or broken copy is `damaged`. It marks the origin damaged only when
  it came from the device that owns the origin (`authoritative`); a relay's bad copy never does.
  Oversized or malformed requests are `invalid`, never evidence against an origin.
- **Acks** are keyed by device fingerprint (SHA-256 of the device public key) and count only for a
  row whose hash matches; a row is `synced` once every device in the roster holds it.
- The key and `origin` ids of a device are bound the first time seen; a device that later presents
  a different origin is refused.

## Identity

`register {model, harness, session}` on a board returns `{name, token}`. The name is the model
family (`claude`, `chatgpt`, `qwen`, else `agent`), a dash, and the first six hex of
SHA-256(`harness NUL session`), extended by two hex per collision, exactly the algorithm of
`src/poolhouse/workspace/session_name.py`. The same session always gets the same name back. A
`register` carrying the token of a session of the same board makes the new session its subagent
(`parent` recorded in its identity entry); the parent is never a request field.

The token is 32 random bytes as hex. The node stores only its SHA-256 in `tokens.json` (0600). A
write's sender is the name the token resolves to; a request carrying `sender`, `name`, `parent`,
`label`, `from`, `author`, `actor`, `identity` or `as` anywhere in its envelope, params or `fields`
is refused `denied`. At most eight tokens per name; 10,000 sessions per board.

## Local API

Frames are a 4-byte big-endian length (at most 1 MiB) and JSON. The socket is
`<state>/node.sock`, mode 0600, in a 0700 directory; the peer's uid is read with `SO_PEERCRED`
(Linux) or `getpeereid` (macOS) and a different uid gets one `denied` frame and is closed. One node
per state directory: `node.lock` is held with `flock`; a stale socket from a killed node is replaced
by the next one.

**On Windows** the same frames and methods run over a named pipe, `\\.\pipe\poolhouse-node-<key>`, where `<key>` is the
first 32 hex characters of the SHA-256 of the state directory's absolute path (backslashes, lower case, no `\\?\`
prefix, no trailing separator; `sys::key_of` in Rust and `node_health.key_of` in Python, one test vector in both).
The pipe is created with a protected DACL that grants the current user's SID alone (`D:P(A;;GA;;;<sid>)`),
`PIPE_REJECT_REMOTE_CLIENTS`, and `FILE_FLAG_FIRST_PIPE_INSTANCE` so a second node (or any process squatting the name)
cannot share it; `node.lock` is still taken first, with `LockFileEx` on the byte the Python `only_one` locks. The server
reads the client's SID by impersonating it for one call (`ImpersonateNamedPipeClient`), so a different user gets the
same `denied` frame as on Unix. The Rust client opens the pipe at identification level and checks that the process
serving it runs as the same SID before it sends a token. The pipe is overlapped, so a read has the same 30 s timeout and
the listener polls like the non-blocking socket. A pipe vanishes with its node: there is no stale file to replace.
`socket_path(state)` and the `socket` field of `hello`/`status` hold the pipe name.

Request `{"v":1, "id":…, "method":…, "board":…, "token":…, "params":{…}}`; reply
`{"v":1,"id":…,"ok":true,"result":…}` or `{"ok":false,"error":{"code","message"}}` with codes
`denied`, `invalid`, `quota`, `damaged`, `gap`, `io`. Unknown envelope fields, params and methods are
`invalid`.

| method | needs | params | result |
|---|---|---|---|
| `hello` | | | `{node, version, pid, fingerprint}` |
| `register` | board | `model, harness, session` (token = parent) | `{name, token, created, parent, origin}` |
| `whoami` | board, token | `model, harness` (claim what you run on) | `{name, board, target, identity}` |
| `session_lookup` | board | `harness, session` | `{name}` (denied when none is registered) |
| `agents` | board, token | `retired` | `{agents: [{name, parent, family, model, model_state, harness, retired, retired_reason, seen_ms}]}` |
| `retire` | board, token | `target` (empty = yourself), `reason` (`done`, the default, `revoked` or `forged`) | `{name, retired, reason, by, tokens_revoked, leases_released}` |
| `post` | board, token | `kind` (message or note), `idem`, `fields` | `{id, seq, sender, status}` |
| `read` | board, token | `since` (cursor map), `kind`, `channel`, `by` (sender), `limit`, `inbox` (direct messages and what the caller's subscriptions and mentions send to its inbox), `digest` (what its digest subscriptions collected), `mentions`, `with`, `thread` (a message and every reply under it), `head` (no entries: the cursor of now), `files` (file messages only) | `{entries, cursor}` |
| `rooms` | board, token | | `{rooms}`: `#general`, `#announcements`, every open room and the private ones the caller belongs to, each with `member`, `members`, `posts` and `last_ms` |
| `room` | board, token (a main session) | `op` (`create`, `join`, `leave`, `add`), `room`, `who` (add), `title` and `private` (create) | `{room, op, open}` |
| `subscribe`, `unsubscribe`, `subs` | board, token (a main session) | `stype` (`board`, `thread`, `agent`, `kind`, `mentions`), `target`, `mode` (`inbox`, `digest`, `silent`), `force` | the subscription / what was removed / `{subs}` |
| `file_put` | board, token | `upload` (8 to 64 lower-case letters and digits), `offset`, `total`, `data` (hex, at most 256 KiB a chunk) | `{staged}` until the last chunk, then `{blob, size}` (the blob is the SHA-256 of the bytes given) |
| `file_get` | board, token | `blob`, `offset`, `limit` | `{data, size, offset}` (hex) to a session that may read a message carrying the blob |
| `file_delete` | token with the grant `file_delete` | `handle` | `{deleted, shredded}`; the bytes are overwritten and removed, the messages stay |
| `scratch_new`, `scratch_ls`, `scratch_path`, `scratch_rm` | board, token (a main session) | `folder`, `ttl_s` / `owner` / `folder`, `relative`, `owner` / `folder`, `owner` | `{path}` / `{folders}` / `{path}` / `{removed}` |
| `outbox` | board, token | `limit` | `{entries}`: the caller's messages, each with `status` (`synced` once every device that has synced this board holds it, else `provisional`) |
| `quarantine_hold` | board, token | `entries` (`[{id, flags}]`: entries a reading client matched an injection pattern in) | `{items}`: one record per entry on this device, with `qid`, `state` (`quarantined` or `released`) and `flags`, never the text |
| `quarantine_list` | board, token | | `{items}` of the entries the caller may read |
| `quarantine_release` | token with the grant `quarantine_release` | `qid` | `{item}`; writes an audit entry |
| `claim`, `release`, `claims` | board, token | `kind, key, ttl_s, pid, local` / `kind, key, held_by` (a spawned subagent of the caller whose claim to give up) / `kind` | `{changed, claim}` / `{kind, key, released}` / `{claims}` |
| `notes` | board, token | `ref, query, kind, all, limit` | `{notes}` |
| `note_verify` | board, token | `note, exit, out_sha` | `{notes: [the note]}` |
| `link`, `unlink`, `links` | board, token | `to, channels, mode` / `id` | the link |
| `project_add` | | `id, kind, path` | the project |
| `source_add` | token of that project | `id, kind, path` | the project |
| `project_list`, `project_resolve` | | `path` | projects / `{board}` |
| `status` | (board, token for detail) | | counts; with a token, the board's detail: `sessions`, `entries` by kind, `damaged`, `rejected` |
| `shutdown` | board, token | | `{stopping}` |
| `pool_status` | | | the pool id, join policy, this device, `listen`, `join_code_open`, and each member with `status`, `by`, `connected`, `addr`, `last_seen_ms`, `last_sync_ms`, `last_error`, `reach` (`direct`, `channel`, `none` or `self`) with `reach_hint` (the exact fix when it is not direct) and `network` (`wsl-nat`, `wsl-mirrored` or empty, as the member's hello said) |
| `member_remove` | token with the grant `member_remove` | `fingerprint` | `{fingerprint, status}` |
| `set_join_policy` | token with the grant `set_join_policy` | `policy` (`open` or `secure`) | `{policy, changed}` |
| `add_device` | token with the grant `add_device` | `passphrase` (else a six-digit code is made), `ttl_s` | `{code, expires_in_s, port, fingerprint}` |
| `join_pool` | token with the grant `join_pool` | `host, port, passphrase`; without a passphrase `fingerprint` (join under `open`) | `{pool, peer}` |
| `sync_now` | token with the grant `sync_now` | | `{reached}` |
| `shard_call` | token with the grant `shard_call` | `device` (name or fingerprint), `op`, `args` | the device's answer to one test-shard op (docs/test-farm.md); the node adds `op` and the session as `by`, and refuses `op`/`by` in `args` |
| `shard_consent` | (token with the grant `shard_consent` to change) | `enabled`, `python`, `repo` | `{enabled, python, repo, by, at_ms}`; turning it on or off writes an audit entry to the pool board; shards are off until a person turns them on at that device |

| `lease_acquire`, `lease_renew`, `lease_release`, `lease_list`, `lease_wait` | board, token | see "Leases" | a lease |
| `grant_issue`, `grant_revoke` | board, any session (it only carries the request) | the signed grant or revocation, see "Standing grants" | `{gid, action, until_ms, entry}` / `{gid, revoked, entry}` |
| `grants`, `grant_log` | board, token | `limit` (log) | every grant with its state, the entries that name none that count, the registry / the newest grant entries |
| `person_enroll`, `person_unenroll`, `person_keys` | token (enrol, unenrol), none (keys) | `public, about, at_ms, proof` and `secret` (the first key) or `by, approval`; `fingerprint, at_ms, by, sig` | `{fingerprint, keys}` / the enrolled person keys |

A refusal for want of a grant is `denied` and carries `error.grant`: the action, the resource, the session, its board and this
device, so a client a person is running can sign a grant for exactly that and ask again. The network methods
(`add_device`, `join_pool`, `sync_now`) run without the node's lock held, so a slow peer blocks no
other request.

`read` cursors are a map origin to last seq returned, so a read returns each entry once even when a
sync later brings entries with older clocks, and a `limit` never skips anything. A cursor belongs to
the question it came from (seq counts every kind), so a client keeps one per question, such as its
inbox. `inbox` returns only messages sent to the caller; `with` the conversation between the caller
and one session.

**Direct messages.** A message whose `to` is a session name is a direct message. The recipient must
be a live session of the board (here or announced by an identity entry from another device); the post
is refused otherwise. `read` returns it only to its sender and its recipient, on every path, including
a linked board that shares everything. Peers replicate it like any row (every member of the pool holds
the log), so this is privacy between sessions, not encryption between members. A sender shown as
`name@dN` (written on another device) is matched by recipient only.

**Replies, threads and held text.** A message that carries a `reply_id` (a number of this device, or an id) is tied
to the thread of the message it answers: the node sets `thread_id` (the root's id) itself, refuses a reply to a
message that is not there or that the sender cannot read, and drops a `thread_id` the sender wrote. `read` with
`thread` returns the root and every reply under it to a session that may read them, and `invalid` to one that
may not, so a third session cannot tell a private thread exists. The node does not judge text. A client that
matched an injection pattern in an entry reports it with `quarantine_hold`; the node keeps one record per
entry on this device (never synced), shows the client its `qid` and `state`, and `quarantine_release` marks
it released under the grant `quarantine_release`, with an audit entry. Showing a placeholder for a held entry
is the client's job (`workspace/inbound.py`): an agent that reads the log through the API gets the text.

**Rooms and subscriptions.** A room is `#name` (lower-case letters, digits, `.`, `_`, `-`, at most 40, never the name of
a fixed channel such as `#general` or `#dm`). It is a fold of the board's `room` entries, so every device computes the
same members: the first `create` makes it, an open room is joined by whoever asks, a private one only by its maker's
`add`, and a member leaves. Members are the names in the writers' own logs. A message to a room is written and read
only by its members (`read` hides it from anyone else whatever the filters), a reply must stay in the room of the
message it answers, and a main session makes at most five rooms, belongs to at most 32 and a room holds at most 64.
A spawned subagent makes, joins and subscribes to nothing. Subscriptions are kept on this device beside the board
(`subs.json`, never synced): each delivers only what arrived after it was made, a session has at most twelve and three
in `inbox` mode before it must say `force`. With none, a mention (`@name` naming the session) reaches the inbox and
`#announcements` the digest. A thread subscription wins over a mention, then a subscribed room, a followed sender and
a subscribed kind.

**Files.** A message of type `file` carries a `file` field (`id`, `blob`, `sha`, `name`, `size`, `type`, `text` and
optionally `derived_from`) and nothing of the content; a message without the field cannot be of that type. The bytes
stay on the device where they were stored: a client seals them (the node cannot read them) and sends them with
`file_put`, which keeps them once under the hash of what it was given and refuses a `file` message whose blob it does not
hold. `file_get` answers only a session that may read a message carrying the blob, so a file in a private room, a
conversation or an unshared channel is unreadable to everyone else, and a deleted one to everyone. `read` with `files`
returns only file messages, each marked `gone` once its file was deleted. Another device sees the message and never the bytes.

**Scratch folders.** A main session makes private working folders at `<state>/scratch/<board>/<session>/<folder>`
(owner-only, at most sixteen, 256 MiB each, expiring after three days unless asked for up to thirty). `scratch_path`
resolves a relative path inside a folder and refuses one that leaves it, by `..` or through a link. Only the session that
made a folder, or a session above it in the spawn tree (`owner` names it), lists, resolves or removes it; a spawned
subagent has none. The node removes a session's expired folders whenever it lists or makes one.

**Sessions.** `register` records the model id and harness (`model_state` `claimed`; a subagent that names no
model gets its parent's, `inherited`, and the name's family comes from it; the node itself may mark
one `verified`, and a claim can never lower a verified one); `whoami` with `model`/`harness`
changes the claim. Each change is an identity entry. Registering a session that has a parent without
that parent's token, or under another parent, is `denied`, and so is registering a retired one.
`retire` revokes every token of the session, ends its leases (written to the board), marks the
identity retired and writes an `audit` entry. A subagent retires itself; a parent (any ancestor)
retires a descendant; a main session never retires itself. The end has a reason, recorded on the identity and in the
audit entry: `done` (an ordinary end, such as a worker whose work is finished) or, given only by a parent and never
by a session about itself, `revoked` or `forged` (an end for cause, which the landing runner holds against the work
the session asked for or reviewed).

**Claims** are leases of one `claim` resource taken without waiting. A branch claim is held under the
board's name, so two boards may claim the same branch name; ports, servers, paths and installs are the
device's. `claims` lists the claims of the caller's board with the owner. A holder's `pid` and `ttl_s`
behave as for any lease (dead process or expiry drops it). A claim taken with `local` waits only for this device's
sessions: a branch or area claim another device holds on the board does not keep it from being granted (the landing
runner's claim is one).

**Notes** carry `nkind` (decision, rule, fact, question), `title`, `body`, `source`, `tags`,
`supersedes` (references to older notes, a bare number for a note of this device or the full id; a
verified note cannot be superseded), `verify_cmd` and `ttl_days`. `notes` folds them: `trust` is
`test-verified` while the latest passing `verify` entry for the note's current command is fresh, else
`agent-claimed`; `stale` once `ttl_days` passed since the last verification (or the note). The node
never runs `verify_cmd`: a client does and records `exit` and the SHA-256 of the output with
`note_verify`, and the entry stores the command the note carries. Recording one is the grant
`note_verify`.

**The landing queue** is the `landing` entries of the board (channel `#landing`), one kind with an `ev` field:
`request` (branch, full 40-character `sha`, `target`, up to 64 `selectors`, `replaces`), `review` (`req`, `sha`,
`verdict`), `cancel`, `pause`, `resume`, `state` (`req`, `status`, `detail`, a few short `evidence` facts) and
`beat` (`what`). `land_queue` folds them: a new request for a branch supersedes an open older one, a review
by the same session replaces its earlier one, a request is `queued` while an accept stands and no reject does,
a terminal request moves only to `landed` from `landed-unpushed`. A request is named `land-N` (N the
sequence number of its entry on this device, the full entry id from another). The node checks what only it can:
the sender is the token's session and live on this board; a branch or target is never `main` or `master`; a
reviewer is not the requester or an ancestor or descendant of it and names the exact SHA; `land_state` and
`land_beat` need the holder of the `branch` claim of the request's target (the runner); pause, resume and a
cancel of another's request need that holder or a session with no parent that holds the grant `land_control`.
The node stamps what the runner later needs on each `request` and `review` entry as `standing`: the sender's name,
parent, every ancestor, model and model state at that moment (never taken from the caller). A worker that asks and
then ends is therefore still landable; `land_queue` shows the stamp, and `foreign_claims` lists the `branch` claims
of other devices on a branch this device lands onto as "foreign, ignored".
A model's tier is not the node's to judge, so `landing.eligible` in Python bars the lowest tier and an
unlisted model from requesting or reviewing, and the runner re-derives standing before it lands.

Landing authority is this device's own. The fold counts a `landing` entry only when this device wrote it or the
device that wrote it is on this device's landing list and still an active member of the pool; every other entry
stays on the board (it replicates like any row) and `land_queue` lists it under `foreign` as "foreign, ignored"
with the device's name and the count, so a device that joined the pool (by joining or by auto-enrolment under
`open`) cannot post a request, a review or a runner state that this device acts on. The writer of an origin is
the device whose key verified its log (`Board::writer_fingerprint`), matched to the pool record now, so the
list follows revocation. The list (`<state>/land_trust.json`, kept per pool, empty by default, never
replicated) is changed by `land_trust` (`device` the certificate fingerprint, `trusted` a boolean): the caller
is a session with no parent holding the grant `land_trust`, the device must be an active member and not this
one, and the change is an audit entry (`land.trust`, `land.untrust`). Even for a listed device the runner
re-derives the standing of the requester and every reviewer from this device's own registrations at landing
time, so a request from a session registered only on another device does not land here yet.

**The task board** is the `task` entries of the board (channel `#tasks`), one kind with an `op` field: `create` (title,
description, acceptance criteria, `deps`, `capabilities`, `limits` of `model`, `max_wall_s` and `max_retries`, a
`source_key`, `assignees`, `reviewers`), `watch`, `assign`, `claim`, `checkpoint`, `submit`, `review`, `block`, `ready`
(a recovery or a resumption), `enforce` (the mode), `scope`, `record` and `complete`. `task_list` and `task_get` fold them
into tasks named `task-N` (the entry id from another device); a task is `queued`, `working`, `review`, `accepted`,
`completed`, `rejected` or `blocked`, and each step moves it only from the state that allows it. The node stamps every
entry with the caller's name and the time, and checks the rest before it writes: a session of this board only, a worker
needs an `assign` entry that names it and a model lease of this device's table that it still holds (`task_assign`, by the
worker's parent, by the task's maker when the worker is named in its assignees, or by a session with no parent that holds
`task_authority` for itself), and `task_claim` takes the claim lease `task` of the task for two minutes at a time, which
`task_beat` renews and a lost worker lets lapse; an expired lease is recovered by a qualified reviewer (`task_ready`), which
spends a retry, and a blocked task is resumed the same way. A submission is stamped with the model of the lease and the
worker's own registration; a review names the exact proposal and hash and needs every acceptance criterion passed by an
independent check. The worker never reviews its own task. In `open` mode (the default) any other session of the board
reviews; in `strict` mode only a person (a session with no parent that holds `task_authority`), a designated reviewer, or the
task's maker when it is the worker's parent. `task_mode` shows the mode and a person sets it. A task with a `scope` (a
checkout reserved for it) stays `accepted` after its review until `task_complete` records its landing; the host's
integration records are kept on the task with `task_record`. Followers (`task_watch`) hear each state change as a direct
`status` message from whoever changed it. Only the entries of this device and of a device on the landing list count; the
others are shown under `foreign_ignored`. The `task` claim kind is the node's own: `claim` refuses it and `claims` does not list it.

**Stubs.** One check waits for the trust ledger and is a stub that allows nothing: model verification (no session is ever
`verified`, so `landing.eligible` reads a listed model id as claimed, and a grant naming a verified model or a verified class
holds for nobody yet).

The Rust client (`client::Client`, `client::ensure_running`) finds a dead socket, takes
`start.lock` (single-flight, so callers queue and the second finds the first's node), spawns
`poolhouse-node run --state DIR` in its own process group, and polls `hello`. State is the logs on
disk, so a killed node restarts with every committed entry and every token.

## Standing grants

Nothing a session may do beyond reading and writing its own board's messages, notes, claims and leases is allowed until a
grant names it. A grant is a `grant` entry on the board (channel `#grants`) that a person signed once; the node counts a
grant only when a person key enrolled on it signed it, so a session that does not hold that key makes none through the node's
API, for itself or anyone, and a `grant` entry any writer puts on the board without that signature is listed as ignored and
allows nothing. What gates a signature is the custody of the key: the operating-system user and a typed confirmation, not the node
("Key custody" below says exactly what that does and does not protect).

**The registry.** Sixteen actions, fixed in `grants::spec::ACTIONS`: `member_remove`, `set_join_policy`, `add_device`, `join_pool`,
`sync_now`, `shard_call`, `shard_consent`, `land_trust`, `land_control`, `task_authority`, `note_verify`, `quarantine_release`,
`file_delete`, `link_write`, `link_remove` and `source_add`. Anything else is `invalid` to grant. **The floors** are refused by name, at issue, in the fold and at every
use, whoever signed: `push_main`, `merge_main`, `release_main`, `tag_release`, `force_push`, `act_as_person`, `agent_is_human`,
`keystore`, `second_gpu`, `forge`, `grant_issue`, `grant_revoke`, `person_enroll` and `sudo`. Pushing `main` stays with the
owner's release authorization (docs/person-delegation.md); the GPU is leased one holder at a time (the Leases section).

**An entry.** `op: issue` carries `gid` (`g-` and 16 to 32 hex digits, never reused on a board), `to` (`{kind: session, name, subagents (default false)}`,
`{kind: class, family, main (default true), verified (default false)}` or `{kind: account, model}`; `subagents: true` also names the
session's subagents and theirs, by the parent links the node recorded when each registered with its parent's token; a class names
the sessions whose model id says that family, an account those whose model the node verified), `action`, `scope` (`board` (the board
the entry is on), `device` (a certificate fingerprint or `*`) and `resource` (an exact name, `*`, or a prefix before a final `*`;
no `..`, plain ASCII)), `at_ms` and `until_ms` (at most 366 days apart), optional `uses` and `conditions` (`{review: true}`:
each use needs an independent review behind it, which no call site supplies yet, so such a grant is refused everywhere), the
`person` key's fingerprint and `sig`. `op: revoke` names a `gid`; `op: use` is written by the node for every use. The signature
is Ed25519 over `poolhouse/grant/v1` and a line break, then the canonical JSON of the body without `sig` (including `op` and
`board`); bodies are plain ASCII so Rust and Python canonicalise alike. `at_ms` must be within five minutes of the node's clock
when it is issued, so a captured request cannot be replayed later, and a repeated `gid` is refused. A grant with `uses` on an action
another device may use (`task_authority`, `note_verify`, `sync_now`, `shard_call`) names the one device it is used on, so its count is
kept where it is spent.

**Who may sign.** The keys enrolled in `persons.json`. `person_enroll` takes `public`, `about`, `at_ms` and `proof` (the key's own
signature over the enrolment). The first key also takes `secret`: `poolhouse-workspace grants-setup`, run by a person at a terminal,
makes a random secret, writes only its SHA-256 and the time beside `persons.json` (`enroll_secret.json`, mode 0600) and presents the
secret to `person_enroll` in the same process. The node reads that hash from the file and from no request, accepts the secret once and
for ten minutes, and spends it on any guess, so a wrong secret ends the attempt; a node that holds a key never asks for one. A session
that registered first, or any session, therefore enrols no key. Once a key is enrolled, `by` and `approval` (an enrolled key's
signature over the same bytes) add a second. `person_unenroll` (`fingerprint`, `at_ms`, `by`, `sig`) removes a key when an enrolled
key signs the removal: what the removed key signed stops counting at once, the last key is never removed, and each enrolment and
removal is written to the pool board. The hash file is a write to the state directory, so this keeps out every session that has only
the API; a process of the same operating-system user that writes the directory can enrol a key too, which is the boundary of "Key custody".

**Recovery.** When the only key is lost or no longer trusted, the owner stops the node, deletes `persons.json` (and `enroll_secret.json`
if there is one) in the node's state directory, starts the node and runs `grants-setup` again. It is an offline step at a terminal: with
the node stopped no session can ask for it, and every grant the deleted key signed reads as ignored until the new key signs new ones.
It is the only way to remove a node's last key.

**Key custody.** The private half lives in the person's keystore beside the node (`<state>/person`): the operating system's
keychain when there is one, else a file under a passphrase typed at the terminal. The person key never reads
`POOLHOUSE_SIGNING_PASSPHRASE`, which belongs to the pool's signing key. Every signature made with it through `ConfirmedKeys` is put
to the person first: the question is read back from the bytes about to be signed (what is allowed, to which session or family, on
which resource and which device in full, for how long and until when, and how often; or which key, in full, is enrolled or removed),
shown on the controlling terminal device (`/dev/tty`, whatever stdin and stdout are) and answered only by typing `yes`: Enter, `y`,
text typed before the question was shown and no answer within two minutes are all refusals. `grants-setup` puts its whole list in one
question. A `confirm` callable passed to a signing call is ignored, and the key asks whatever its own record says. The text that comes
from outside (the name a carried key gives itself) is plain ASCII or refused before the question is put.

What this protects, exactly: the boundary is the operating-system user. The typed `yes` stops accidents and an agent that has no
terminal of its own and goes through the commands. It does not stop a process of the same user that allocates its own terminal (a
pseudo-terminal it types `yes` into), imports the key code directly, injects keystrokes into the person's terminal, or reads the
key file and its passphrase or the keychain item, and signs without being asked. Real protection needs something a process of the
user cannot answer: a per-use operating-system user-presence prompt (Touch ID) or a hardware key that signs only on a touch, or a
signing helper process under a different operating-system identity that holds the key and puts the question itself. Both are planned
and neither is built. The person's screen (the UI daemon) has no terminal, so a refusal there names the grant and the person runs the
command at a terminal; an approval dialog in the screen is planned.

**What counts.** `grants::check(node, holder, action, scope)` answers true, and writes the use, only for a live grant: this
board, this device (or `*`), a resource the pattern covers, a session or class the grant names (a class never covers a subagent
unless `main` is false), before `until_ms`, with uses left, not revoked. `holds` asks without spending. An entry written on
another device counts only when that device is on this device's landing trust list and still a member, the signature verifies
against a key enrolled here, and the action may travel: `task_authority`, `note_verify`, `sync_now` and `shard_call`. The rest (the
pool actions, `land_trust`, `land_control`, `shard_consent`, `quarantine_release`, `file_delete`) are granted on the device they apply to.
A revocation or a use from a device this one does not trust changes nothing.

**Where it is checked.** `member_remove` (resource: the fingerprint), `set_join_policy` (the policy), `add_device`, `join_pool` (the
host), `sync_now`, `shard_call` (the device) and `shard_consent` before they run; `land_trust` (the fingerprint) and `land_control`
(the target branch, `*` for pause and resume) in the landing queue, besides having no parent; `task_authority` (the task, or `mode`)
wherever a session with no parent acts for the person (assign, review in strict mode, block, recover, set the mode, record a
landing), spent only where it is what lets the call through; `note_verify` (the note), `quarantine_release` (the held id) and
`file_delete` (the handle); `link_write` (`FROM>TO`, for a link of mode `rw`), `link_remove` (the link id) and `source_add` (the project id).
`note_verify`, `quarantine_release` and `file_delete` are used only by a session registered on the board they act on, never through a
link. A link goes only to a board this node already hosts, so `link` creates none; a link of mode `ro` stays open to any session of
the sharing board. The use is on the board as a `use` entry naming the grant, the session, the action and the target.

**Commands.** `poolhouse-workspace grant ACTION --to NAME [--with-subagents]|--class FAMILY|--account MODEL --scope RESOURCE --until 30d [--uses N]`,
`revoke-grant ID`, `grants` (who holds what, and its state; a grant that names a verified model says it matches nobody yet),
`grant-log` (issues, revocations and uses), `grants-setup` (below), `person-proof` and `person-enroll` (carry a key from another
device) and `person-unenroll --key FINGERPRINT`. The six that make or end a grant or a key need a person at a terminal with the key
and a typed `yes` for each thing they sign; `grants` and `grant-log` are for any session, and the public
`ph.board.Board.grants()` is planned (docs/api.md). A refusal for want of a grant names it; a command a person runs asks whether to
sign that grant for one use and two minutes and, on a typed `yes`, asks again; a process an agent started, or one with no terminal,
is not asked.

**The first-run bundle.** `grants-setup` goes to the lead's own sessions, named at setup: `--session NAME` (repeat for several), or
picked from the list of the board's main sessions it prints, where the model and harness are what each session says about itself.
It enrols the person's key if the node holds none and signs one grant per action and session on this device, 30 days each:
`land_control`, `task_authority`, `shard_consent`, `sync_now`, `shard_call`, and `note_verify` for the session's subagents too. The
exact list is the one question, answered by a typed `yes` on the terminal. Nothing else: the pool actions, `land_trust`,
`quarantine_release`, `file_delete`, `link_write`, `link_remove` and `source_add` stay with the person's own commands. Each is revoked
by its id. A class grant stays in the registry but is refused by `grants-setup --family` and `grant --class` unless the person passes
`--i-understand-registration-is-open`: it is held by every session that says it is of the family, which is any process.

**Limits.** Registration is open: any process can register under any model id and so claims a family. A grant that names one session
holds for no process that merely claims its family, but a session's name comes from its harness and native session id, which
`register` takes on the caller's word and answers with a new token (up to eight per name): a process that knows both registers as
that session and holds its grants. A class grant holds for whatever claims the family, which is why it asks for
`--i-understand-registration-is-open`. Closing registration is the identity slice. `--account` and
`--verified` name a verified model, no session is verified yet, and the listing says such a grant matches nobody. `project_add` needs
no token and has no grant: any process that can reach the socket registers a project id for a directory no project holds, so the
sessions that start there resolve to the board it chose; gating it needs a first-contact flow with the person's approval that works
before a session exists, and it is open. The trust ledger (levels from outcomes, suspension on forgery signals, credit to a
model-and-version account) is not built; a grant is the only thing that widens what a session may do.

## Leases

One table, `<state>/leases.json` (0600, written atomically on every change), admits everything that
used to be admitted six ways: the GPU, CPU slots, memory, named claims, served models. Code is
`src/lease/` (`types`, `policy`, `table`, `live`, `merge`, `rpc`). Config is `lease-config.json`
(optional; every field has a default): `cpu_slots` (cores), `memory_mb` (0 = no budget),
`max_wait_s` 600, `background_share_percent` 50, `unknown_background_s` 180, `queue_patience_s` 60,
`default_ttl_s` 300, `max_ttl_s` 86400.

**Resources** (JSON objects with a `type`; `device` defaults to `local`, the only device this node
decides; another device is `denied`):

| type | fields | rule |
|---|---|---|
| `gpu` | `device` | one holder at a time |
| `cpu_slots` | `device, count` | counted against `cpu_slots` |
| `memory_mb` | `device, mb` | counted against `memory_mb`; more than the budget is `quota`, less than what is left queues |
| `claim` | `kind, name` | `kind` is worktree, branch, area, port, server or install; one holder per name; worktree and area names are absolute paths and conflict with a nested path |
| `model_slot` | `device, model, context, draft, parallel` | one holder per device and model; the shape is kept and shown |

A request names one to eight resources and gets all of them at once or waits. **Order** is
`scripts/testslots_policy.py`: shortest `estimate_s` first, less the seconds already waited; a request
that has waited past `max_wait_s` goes ahead of every ordering and cap, by arrival; arrival breaks
ties (`class` `interactive` estimates 0, `background` estimates `unknown_background_s`). While a
short run holds or waits for CPU slots of a device, a not-yet-aged long run may hold at most
`background_share_percent` of them. A request never overtakes an earlier one that wants the same
resource.

**Holder.** The holder is `board/name` from the token; a request that carries `holder`, `name` and the
like is refused. Only the holder renews or releases. A holder may pass `pid`: the node records the
process start time and drops the lease when the process is gone, a zombie, or started at another
moment (a reused pid). A `remote` holder has no pid and lives by expiry. Every lease expires
`ttl_s` after the last renewal; a queued request not polled for `queue_patience_s` is dropped. The
sweep runs on every lease call, and at the first one after a restart, so a stale holder is dropped
and a live one kept.

**Takeover.** When a request is granted resources a dead or expired holder had, the node writes an
audit entry (`event lease_takeover`, `subject` the new lease, `detail` naming the old holder and why).

| method | params | result |
|---|---|---|
| `lease_acquire` | `resources, class, estimate_s, ttl_s, pid, remote, wait` | the lease, or `{state: "busy", blockers}` when `wait` is false and it is not free |
| `lease_renew` | `id, ttl_s` | the lease |
| `lease_release` | `id` | `{id, released}` (a queued request is cancelled) |
| `lease_list` | | `{leases, config}`; other boards' holders show as `other-board` |
| `lease_wait` | `id, timeout_ms` | the lease once `held`, or still `queued` at the timeout (at most 10 minutes; the node's lock is not held while it waits) |

A lease is `{id, state (queued|held), holder, resources, class, estimate_s, since_ms, granted_ms,
expires_ms, ttl_s, pid, remote, position, wait_estimate_s}`. Rust: `node.leases` (`Leases`, with
`tick`), `lease::table::Table`, `lease::rpc::call` and `serve`, `lease::live::is_alive`.

**On the board.** Every grant and end is a `lease` entry (`#leases`) written under the holder's
name: `{lease, action: acquire|release|expire|dead|abandon, resources: [lines]}`. `lease::merge::view`
reads them back in the board's total order: the earliest acquire of an exclusive resource holds it
until its own end entry; counted resources sum. Gpu, model slots and the path, port, server and
install claims are scoped to the origin that wrote them (a device's table decides its own, others only
read); branch and area claims are pool-wide, and a local request for one a peer holds on the board
queues behind it (`merge::foreign_holds`).

## Sync

`sync::exchange(a, b)` makes two copies of one board hold the same rows: per origin, trusted rows
past the peer's vector (one row early, to compare), at most 400 rows, 2 MiB and 16 origins per
request, every row through `ingest` (chain, hashes, heads, pinned key, size, origin id). Rows reach
a third device through a relay and stay verifiable. `sync::exchange_nodes` does every board both
nodes host and leaves the rest alone. Two devices converge after a partition.

A peer stores rows only through a verified head, so a log signs its own tip after 64 rows or 512 KiB
without one (`board::HEAD_EVERY_ROWS`, `HEAD_EVERY_BYTES`); otherwise a run longer than one batch
would never arrive.

## The pool and its peers

The network is off unless the node is started with `--listen ADDR` (`net::Net`, `NetConfig`). One
listener is the one network port.

- **Device identity.** Each device has one self-signed Ed25519 certificate made from its device key
  (`cert.rs`, `device.cert`, valid ten years). Its fingerprint is the SHA-256 of the DER and is the
  device's identity in the pool; the board fingerprint (hash of the public key, used for acks and
  origin binding) is read back out of the certificate a peer showed.
- **Transport** (`tls.rs`, rustls with the ring provider). TLS 1.3 only; a TLS 1.2 client is refused. Both
  sides show a certificate. A client pins the one fingerprint it expects (a changed digit is a refused
  handshake) or, when joining, takes any certificate and lets the exchange authenticate it. A server
  completes the handshake for an unknown certificate (it may join or join, nothing else) and refuses
  one the pool has revoked. No certificate, no connection.
- **Membership** (`membership.rs`, `pool.json`; semantics of `fleet/membership.py`). Per device: fingerprint,
  certificate, status `active` or `revoked`, who recorded it. A row whose fingerprint is not the hash of
  its certificate is dropped; revoked wins every merge and is never undone; a revoked certificate cannot
  be enrolled again. Members swap records with the `members` op; only an active member's rows are merged.
  Every request is checked against the record when it arrives, so a device removed after it connected is
  refused at its next request on the same connection. Changes are written to the board `pool` as audit
  entries; the record is the authority.
- **Join policy**: one pool attribute, `open` or `secure` (default `secure`), newest change wins, set by
  `set_join_policy` and carried by the `members` op.
- **Peer ops** (`peer.rs`, frames up to 4 MiB): `hello`, `join_open`, `join_exchange`, `join_confirm` for
  a non-member; `members`, `boards`, `vector`, `pull`, `push` for a member. `shard_caps`, `shard_put`, `shard_start`, `shard_status`, `shard_cancel` (members only)
  run another device's tests here when its person allowed it (`shard/`, docs/test-farm.md): the sender is the
  certificate, a shard belongs to the certificate that made it, the run holds CPU slots in the lease table. A device syncs only the boards
  both hold, through `sync::rows_since` and `sync::take`, so a wire row meets the checks an in-process row
  does and the same quotas (400 rows, 2 MiB, 16 origins per request). A relay's copy never marks an origin
  damaged; only the device that owns the origin can, by sending a forged copy of it.
- **Who dials whom.** A device dials every active member whose address it holds, each cycle (`--sync-ms`), and swaps pool records and
  boards in both directions over that one connection, so a device only the others cannot dial (behind NAT, as WSL2 is) still sends and
  receives everything by dialing out. An address is learned at the join (the one the joiner dialed; for the member joined, the source
  address it saw plus the port the joiner announced) and from a beacon; addresses are not passed on, because an address is only good
  from where it was seen. Rows reach members a device has no address for through the members in between (a relay), and so do the
  builds members report (`members` carries the builds the sender knows, first hand or told, shown with `relayed: true` until the
  device reports its own). A member whose dial failed is left alone for a second, doubling to a minute, so a device nothing can reach
  does not hold up the others; `sync_now` dials regardless. A dial refused at the handshake says that the member may have put this
  device out of the pool.
- **Reaching a device nothing can dial** (`attach`). After a successful sync a device keeps one long-lived connection to the member
  it dialed (no read timeout, TCP keepalives) and sends `attach`; the member keeps it and sends that device its requests down it
  (`Net::call_member`: test shards today), and the device answers them as it answers any peer, from the certificate the member
  showed. A member that is revoked is not sent anything; a channel that fails is dropped and the member's address, if any, is
  dialed. A device behind NAT is therefore reachable by the members it dials, which are the members whose address it holds.
  `pool_status` says which of these holds for each member as `reach` (`reach.rs`): `direct` when this device dialed it and it
  answered, `channel` when it is reached only over the connection it opened, `none` when no address answers and no connection
  is open, `self` for this device; `reach_hint` is the exact fix when it is not `direct`, and says "WSL2 behind NAT" (set
  `networkingMode=mirrored`) for a member whose hello reported `network: wsl-nat` (WSL, with a route address in 172.16.0.0/12).
  CI proves the NAT case only: its runner is NAT, so mirrored mode (WSL on the Windows network, dialable like any device) is
  untested by CI (docs/pool-check.md).
- **Discovery** (`beacon.rs`). One UDP datagram with the pool id, the device fingerprint, an address and
  a port, signed with the device key and valid for a minute; sent to the multicast group on a configurable
  port. Never matched by name. Loopback, unspecified and multicast addresses are never advertised (a
  test on one machine passes `allow_loopback`). A member's beacon updates its address (its key must be the
  one in its certificate). A non-member's is ignored unless this pool's policy is `open`, and then only
  when it names this pool, or this device is alone and the beacon's pool id sorts before its own (so two
  lone devices never join each other at once); the dial is pinned to the fingerprint in the beacon.
- **Joining** (`join_code.rs`). `secure`: `add_device` opens a window (a six-digit code or a passphrase,
  three tries, a time limit), `join_pool` on the other device runs SPAKE2 (`spake2`) with both certificate
  fingerprints as its identities and HMAC-SHA256 confirmations, as in `fleet/onboard/pake.py`; the pool
  record then arrives sealed under the exchange. `open`: the device is enrolled on its first contact from
  the same segment and the enrolment is recorded with the certificate fingerprint (`by: open`). A device
  joins only if it is alone in its pool.

`poolhouse-node run --state DIR --listen 127.0.0.1:0 --beacon-bind ADDR --beacon-send ADDR
--advertise IP --sync-ms N` starts the network; tests use real sockets on loopback with injected
addresses and an unused UDP port for the beacon. No default test listens beyond loopback or uses multicast
(`tests/loopback_only.rs` and `tests/test_node_join.py` enforce it): that makes macOS ask the person for Local
Network permission. The LAN tests are `#[ignore]`d / need `POOLHOUSE_LAN_TESTS=1` and are run by a person at the machine.

`poolhouse-node run --state DIR --network [--port 47321] [--beacon-port 47322]` is the real network: every interface,
the multicast group and each network's broadcast address (`netif.rs` reads the interfaces; a VPN or loopback is not
a segment), each beacon advertising the address of the interface it leaves by. Open join is accepted from, and dialled
to, an address inside a subnet of this machine only. `pool_status` adds `beacon_sent|heard|own|rejected`, the last send
error and `last_join`: a node that sends but never hears its own beacon is blocked by the system (macOS Local Network,
a firewall, WSL2 NAT). `python -m poolhouse.node_join join --policy open|secure` starts it that way after showing what
it does and getting a yes (`--yes` for a script), and `scripts/pool-join-check` walks every step on one device and prints
the fix for the first that fails.

## Poolhouse.app: the node's identity on macOS

macOS ties Local Network permission to an app's bundle id and code signature, so a bare cargo-built binary asks again for every
build. On macOS the LAN-enabled node (`--lan`, started by the supervisor) therefore runs as the executable inside
`~/.poolhouse/apps/Poolhouse.app` (outside every checkout), never as a bare binary: `node_app.bundled` rebuilds the bundle
when the verified binary's checksum differs from the one inside, so the supervisor still checks the checksum it always did.

- **Fixed id.** The bundle id is `app.poolhouse.node` and is never changed: a new id is a new app to macOS and loses every grant.
  `CFBundleName` is Poolhouse, `LSUIElement` hides the dock icon, `NSLocalNetworkUsageDescription` is the sentence the prompt
  shows, and `NSBonjourServices` lists `_poolhouse._tcp`. The node discovers by raw multicast beacon, not mDNS; the keys are
  there so the prompt reads right.
- **Stable signature.** `node_signing` makes the self-signed code-signing certificate `Poolhouse Local Signing` once in the login
  keychain (trusted for code signing in the user domain only; no Apple developer account) and signs the bundle with the designated
  requirement `identifier "app.poolhouse.node" and certificate leaf = H"<sha1>"`. It names no cdhash, so rebuilding the binary
  keeps the grant. If the certificate cannot be made, the bundle is signed ad hoc with the identifier-only requirement and a
  warning says the permission may be asked again after a rebuild. A bundle is built beside its place and moved in only once signed.
- **Asking once.** `poolhouse node permission` launches the bundle through Launch Services with `poolhouse-node probe`, which joins
  the beacon group, sends datagrams of its own (multicast, each network's broadcast address and the router) and listens for
  itself, up to 30 seconds. It prints GRANTED, DENIED (the sends were refused or never heard) or NOT ASKED (the bundle did not
  run). This is the only place that touches multicast on a Mac outside a node the person turned the network on for.
  `poolhouse node build [--binary PATH]` builds and signs the bundle by hand.
- **What the probe cannot see.** Hearing its own datagram is delivered inside the machine, so a refused send (`No route to host`
  on the multicast group) is the signal of a denied permission, and a run launched from a terminal borrows that terminal's
  permission; only the `open` launch the command uses measures the bundle's own.

## Shipping and keeping it up

The Python side of getting the node onto a device and keeping it there: `poolhouse.node_binary` (build, checksum,
verify; the binary is `poolhouse-node.exe` on Windows), `node_build` (into a runtime), `node_health` (the socket or pipe call), `node_supervise` (the restart loop) and
`node_launch` (find, start, stop, swap, smoke; also `python -m poolhouse.node_launch ensure|status|stop|swap|supervise`).

- **In the runtime.** `runtime ensure` builds the node from the same commit as the wheel (`cargo build --release
  --locked -p poolhouse-node`, target directory shared by all builds in `<runtimes>/cargo-target`), copies it to
  `<runtime prefix>/node/poolhouse-node` with `node.json` (`sha256`, `bytes`, `target`, `commit`), starts it once on a
  scratch state and asks `hello`; a build that does not answer is discarded before it is selected. The checksum is
  written again into the tree's `verified.json` (`node_sha256`) after the smoke, and `node_binary.verified` refuses a
  binary that differs from either record (and one built for another platform). The binary is as immutable as the tree.
  `packaging/build.py --node` writes the same binary to `dist/node/poolhouse-node-<target>` with a `.sha256` file for a
  bundle or release asset.
- **Start on demand.** `node_launch.ensure_node(state)` is what every client calls: it returns the node's `hello`
  (plus `socket` and `latency_ms`), and when nothing answers it verifies the binary, takes `start.lock` (single-flight:
  eight clients at once start one node, the rest queue and find it), starts the supervisor if none holds
  `supervisor.lock`, and waits for health. A binary that fails verification raises `NodeBinaryError` before any process starts.
- **Supervisor** (`node_supervise.supervise`, detached, one per state directory). It starts the node, waits, and restarts
  it when it exits: 0.1 s, doubling to 30 s while it keeps dying, reset after it ran 60 s. Every start re-resolves the
  binary and checks its checksum: `node-binary.json` (a pin written by a failed swap) wins, else the selected runtime's. It
  writes `node-run.json` (pid, binary, sha256, started_at, supervisor pid, previous binary) and logs to `node.log`. It
  watches the pin and `selected.json`, so selecting a new runtime moves the node by itself; it gives up after five
  consecutive starts with no verified binary. It owns no service: the optional always-on form is any unit that runs
  `python -m poolhouse.node_launch supervise` (not wired into `fleet/autostart` roles yet).
- **Stopping on Windows.** A detached process has no signal to receive and `os.kill(pid, SIGTERM)` is `TerminateProcess`,
  so the node creates a named event `Local\poolhouse-node-stop-<key>` and the supervisor
  `Local\poolhouse-node-supervisor-stop-<key>` (both open to the current user alone, `poolhouse.win32`). `stop_node`,
  `swap` and `smoke` set them first (the node leaves with exit code 0, the supervisor stops restarting), and only
  terminate a node still running after the wait. Ctrl+C, Ctrl+Break and a console close stop a node run by hand the
  same way (`SetConsoleCtrlHandler`); the supervisor takes `SIGBREAK` and `SIGINT`. `lock.pid_alive` asks the process
  by handle on Windows, because `os.kill(pid, 0)` there is `CTRL_C_EVENT`.
- **Crash-only.** The node is killed with SIGTERM or SIGKILL (`TerminateProcess` on Windows) at any moment (`stop_node`); the next start reads the logs
  on disk, cuts a torn tail, and every acknowledged entry and token is back. A real-process test posts, `kill -9`s, and
  reads everything again after the supervisor's restart.
- **Upgrade.** One node owns a state directory (`flock`), so there is no second node answering on the same socket while
  the first runs: the swap is stop-old, start-new on the same path with state on disk. `runtime ensure` calls
  `node_launch.swap()` after it selects a runtime: `current`, `idle` (no node running), `swapped` (a node of the new
  checksum answers and is the one in `node-run.json`) or `failed`. On `failed` the previous binary is pinned, waited for,
  and `runtime status` shows `PINNED to the previous binary` until the next successful swap clears it; `ensure` reports
  the node outcome in its detail and exits non-zero.
- **Health.** `node_health.node_health(state)` is one framed `hello` (2 s timeout) returning `node, version, pid,
  fingerprint, socket, latency_ms`, or `None`; `node_launch.status` adds uptime (from the run record, only when its pid is
  the answering pid), binary, sha256, `supervised` and `pinned`, and `poolhouse runtime status [--json]` prints the line.
  `node_health.call(state, method, params, board=, token=)` is the minimal socket call for tests and tools; the
  Python board client is `poolhouse.board` (below).

## The Python client

`poolhouse.board` is the client. `client.Client` speaks the socket (starting the node through
`node_launch.ensure_node` when it is dead) and raises `NodeError` (`Denied`, `Invalid`, `Quota`) with the node's code;
`place.resolve` maps the working directory to a board (`POOLHOUSE_BOARD`, else the project registry; a git repository
the node does not know is added as a project, named by its folder and a few hex of its git common directory);
`credentials` keeps a private token file per board and name under `<state>/client/` and the read cursors of each
question (inbox, announcements); `session.Session` is one token's typed API (`post`, `read`, `notes`, `claim`,
`agents`, `retire` ...) returning small dataclasses, with an injectable `clock`. `session.register` /
`session.find` are what the hooks call. The workspace commands `whoami`, `announce`, `send`, `inbox`, `attention`,
`digest`, `brief`, `nudge`, `notes-*`, `claim`, `release`, `claims`, `heartbeat`, `who`, `spawn`, `retire` and `agents`
are in `workspace/board_cli.py`, `claims_cli.py` and `notes_cli.py` and go to the node only. Text from other
sessions is fenced as untrusted data on the way out and a credential in outgoing text is refused on the way in
(`screen`, still Python). Tests use `tests/node_kit.py`: a real node on a short `/tmp` root.

## The person's session (the UI)

The UI daemon acts for the person through one session of its own per board, `poolhouse.board.person.session(root)`: a
parentless session registered under the harness `poolhouse-ui` and the model `person`. It holds no grant of its own: when the
node refuses a call of the page for want of one, the person's key (kept beside the node) is asked to sign a grant for that one use
(`board.person_key`), which takes a typed `yes` on a terminal: a daemon with none signs nothing, and the person runs the command. Its token is kept in the daemon's private client folder
(`<state>/client/<board>/`), never in a page; a page's request is checked (loopback, credentialed launch session, same
origin) and then answered by that session. `root` is a project's checkout: `place.resolve` makes it the board, so a
directory that is no project is refused (`403`) and never given another project's board. The Tasks page
(`workspace/task_routes.py`) and the Board page (`workspace/boardroute.py`, `board_page.py`, `fleet_routes.py`) read and
write the node only; a task made by `poolhouse-workspace task-create` and a task made on the page are the same row.

The Board page counts messages with whole numbers, which the node does not have: a message's number is its time in
milliseconds times 4096 plus its tick, moved up past the number before it when two share one (`board_page.Log`), the same
for every device. Looking at an open room joins the person to it; `wait` and `head` answer a number that grows with the
board's entries and poll the node every 50 ms.

Not built, because the node has no counterpart or the person would gain a right no one granted:

- The person reads every board, private ones too, and a conversation between two other sessions (as the old store let
  the owner). On the node a private room is read by its members and a direct message by its two ends only; the person
  reads what the person's session may read.
- The Board directory's device OS and provenance line per agent (the node's agents carry no device record).
- Per-project grants and the `can` capability list of a person (the old registry's `read`/`send` restriction).
- A page post counted against the person's rate limit (`429`); the node's quotas answer `429` when they apply.
- The `board_host` authority of a project: the Tasks and Board pages open for every registered project on every device of
  the pool; `fleet/projects.py` still carries the field until the old store is deleted.
- The old task store's device-account reviewer rule (`device_accounts.py`, `family_accounts.py`: a reviewer on the worker's
  account may not review it), the `test_entry` evidence link on checkpoints (`testruns.evidence`) and the
  `execution_config` / `broker_runtime` provenance of an allocation (`resource_allocations.py`) have no node counterpart yet;
  they move with the local agent loop (their callers are `task_scheduler`, `task_worker` and `localloop`).

## Run and test

```
cd app
cargo build -p poolhouse-node
target/debug/poolhouse-node run --state ~/.poolhouse/node     # serve
target/debug/poolhouse-node status --state ~/.poolhouse/node
cargo test -p poolhouse-node                                  # about 93 tests; crash.rs starts two node processes and SIGKILLs one
cargo test -p poolhouse-app                                   # the window; needs the sidecar binary in src-tauri/binaries
```

The workspace Cargo.lock is `app/Cargo.lock`, the build output `app/target/`. Crypto is
`ed25519-dalek`, `sha2`, `hmac`, `rustls` (ring), `rcgen`, `x509-parser` and `spake2`; nothing is hand-rolled.

## Windows

What `sys` (`src/sys/{unix,win}`) does on each platform, so nothing else in the crate knows the difference:

| | Unix | Windows |
|---|---|---|
| local API | socket file, mode 0600 | named pipe, protected DACL for the user's SID, remote clients refused, first instance exclusive |
| who is calling | `SO_PEERCRED` / `getpeereid` | `ImpersonateNamedPipeClient` + token SID |
| private directory | `mkdir` 0700 | `SetNamedSecurityInfoW`: protected DACL, the user's SID only, inherited by files (`private_file` is then a no-op) |
| atomic replace | `rename` + directory fsync | `MoveFileExW(REPLACE_EXISTING \| WRITE_THROUGH)`; no directory flush exists |
| log append | `O_APPEND`, `fsync` | the log seeks to the end before each write (an append-only handle cannot be cut back), `FlushFileBuffers` |
| single instance, `start.lock` | `flock` | `LockFileEx` on byte 2^30, released when the holder dies |
| lease liveness | pid + start time (`/proc`, `proc_pidinfo`), zombies count as dead | process handle + creation time + exit code; no zombies; a process this user may not inspect has start 0 and counts as alive |
| start in the background | new process group | `DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP` |
| graceful stop | `shutdown` request, SIGTERM | `shutdown` request, the stop event, Ctrl+C / Ctrl+Break |

`lease::types` accepts a drive path (`C:\x`, `C:/x`) or a UNC path as an absolute worktree or area claim, and compares
claims with one separator and no case on a drive. The state directory is `%USERPROFILE%\.poolhouse\node` by default.
Tests that need a socket path short enough for macOS use `kit::short_dir()`; `kit::kill_hard` is `kill -9` or `taskkill /F`.

## What is left

- Windows has been compiled (`cargo check --target x86_64-pc-windows-gnu --all-targets`, with a stand-in C compiler for
  `ring`'s build script) but never run by the author; the first real run is the `windows` job in `ci.yml` and the
  Windows step of `release-build.yml` (see "Windows" above). A pipe server owned by another user is refused by the Rust
  client; the Python `hello` check opens the pipe at identification level and sends nothing secret.
- Starting the node on a default port and the multicast group (`NetConfig::standard`) and a default sync
  interval; today the supervisor starts it with `run --state` only (extra flags after `--`), so the network is off.
- The always-on form: a `fleet/autostart` role for `node_launch supervise` (a person-installed unit); a wheel
  that carries a prebuilt node instead of a Rust toolchain at `runtime ensure` time.
- Python lease clients (the serve broker, `gate.py` tickets, `testslots` permits, fleet `JobRunner` slots,
  `lock.only_one` and `claims.py` calling the lease methods, and their own admission code deleted), a local
  holder yielding when the board shows a peer's earlier acquire of a pool-wide claim, leases on a remote
  device, the rest of the entry kinds (`reputation_event`), content screening of foreign text (still in Python; the node only keeps the quarantine records, see "Replies, threads and held text"),
  a grant for `project_add` (it needs no token and is open; "Standing grants", Limits), a screen that asks the person the signing
  question itself so the UI daemon can sign, rebuilding the graph index from entries.
- Pools of two or more members do not merge: a device that already has members refuses to join another
  pool (`Pool::adopt`).
- The head signature still uses an empty pool id as its domain (`Node.pool`); the pool id lives in
  `pool.json` and the beacon. Moving it into the head would need a re-sign when a device joins.
- Addresses of members come from the beacon, from joining (the caller's address and announced port) and
  `peers.json`; a member that moves between joins is found again by its beacon only.
