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
  the last two). A client resolves its board with `project_resolve {path}`: the project whose
  source contains the path; a linked git worktree resolves through the repository's common
  directory, so every worktree of a repo is the same project; a directory that matches nothing
  gets `denied: this directory is not part of a project`, never another project's board. A git
  source is stored as the repository's main working tree. A place is never the same as, inside or
  around a place of another project (`invalid`), so no project takes over where another's sessions
  land. A place is never the user's home, a directory around it or the root of the disk, and a folder that
  is not a git repository never holds a git repository of its own (a search three levels deep, with no
  links followed): such a folder is `invalid`. `project_add` needs the token of a session of the board the
  project names; a place that is not a git repository (`folder`, `gdrive`, `onedrive`) also needs the
  grant `source_add` on the project, as every further place does, which a person gives with
  `poolhouse-workspace grant source_add --to NAME --scope PROJECT`; the grant is spent once the project
  was added. A node holds 256 projects
  ("Limits"). The first session in a repository registers a session on the board the repository names
  (harness `poolhouse-place`) and adds the project with it (`board.place.resolve`). A person's approval
  of a git project is not asked: it would have to work before any session exists, and a prompt for the
  person to approve a new project, and for a place that is not a repository, is planned. A project is as trustworthy as whoever registered on its board first.
  `.poolhouse/project.toml` in a repo (`board = "id"`) is an optional hint only
  (`project::propose_id`, `project::init_project`); it is never required.
  `--board ID` still names a board directly, and is checked against the session's registration.
- **Links.** Nothing crosses between boards except through a link: a session of board `from`
  makes `link {to, channels, mode}` and the sessions of `to` may then use the named channels (none
  named = the whole board) of `from`, read-only (`ro`) or read-write (`rw`). Channels are
  `#general`, `#announcements` (messages by their `to`), `#notes` (notes and their verifications),
  `#leases` (claims), `#identity`, `#audit`, `#grants`. Messages to one session are on `#dm`, which a link never
  shares (see "Direct messages"). A linked session writes as `name@its-board`. `unlink {id}` revokes it at once. A link of any
  mode needs a grant on `FROM>TO`: `link_read` for `ro`, `link_write` for `rw`, and ending one needs `link_remove`. A board has at most
  64 links open, and making or ending a link writes one audit entry on both boards. Links are node-local.
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
`reputation_event` is in the schema so a later log still parses, and a foreign entry of that kind is not shown yet. `head` signs transport rows; `base` starts a segment and `carry` authenticates an original durable operation. These transport records are never shown.

- **Crash safety.** Append is one write plus `fsync`; it returns when the row is on disk. On open, a
  last line without its newline was never acknowledged and is cut off (a torn write from `kill -9`);
  damage anywhere else is an error, never a silent drop.
- **Heads.** A head row signs (Ed25519, the device key: "Where the device key is kept") the row directly before it.
  Only rows through the last verified head are given to peers or stored from peers. A pool of one
  never needs to sign.
- **Segments.** A log that holds 75 percent of the rows or of the bytes it may keep starts a new segment (`board/rotate.rs`,
  `docs/notes/2026-10-19-log-rotation.md`): a `base` row that continues the chain, this device's notes, rooms, tasks and grants
  carried with their original operation identity, order and clock, and a head, in one file that replaces the log by a rename; the rows before it go to `archive/<origin>.<first>-<last>.jsonl`.
  Seqs go on across segments. A copy of the log takes the base as any next row, applies the segment once every restated row is
  held (until then its view is the old rows), and a member that holds nothing of the origin, or is behind a base whose archive is
  gone, starts the chain at the base. A base may start a chain at a seq of at least 2; it is refused when it ends a segment under half
  the cap, restates more than half the rows or leaves out a grant revocation the copy holds. Nothing in the rows before a base is needed to
  verify the rows after it: the head that covers the base is signed by the key pinned for the origin, as every head is.
- **Merge.** All rows sort by (wall, counter, origin, seq): one total order for any delivery order,
  with a row delivered twice kept once and a repeated `idem` key of one actor on one origin kept at
  its first row. Foreign rows dated more than five minutes ahead are held back from the view.
- **Foreign rows** become entries only through explicit field allow-lists per kind, each field
  checked for type and size; the sender is the log's actor (a `from`/`author`/`name` that differs is
  refused), must be a usable name and must not be a local session's name, and is shown as
  `actor@dN`. Posts target `#general` or `#announcements` (with announcement types only). Notes per
  foreign sender are capped. Refusals are recorded (the latest 200) and shown in `status`.
- **Quotas.** 64 origins per board, 200,000 rows and 128 MiB per live segment of a log (one session writes a tenth of each and the sessions of one process tree
  a quarter between them; the last 2,000 rows and 8 MiB are the node's own: the identity entry of a session ending, and in
  the last 500 rows and 2 MiB only a head and the rows a person's decision makes; docs/limits.md, "At a bound"), 128 KiB per row, 64 KiB per
  body text.
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
SHA-256(`harness NUL session`), extended by two hex per collision (`Registry::assign` in `src/identity.rs`). The same session always gets the same name back, and a main
session's token only from the lineage it was registered from (below). A
`register` carrying the token of a session of the same board makes the new session its subagent
(`parent` recorded in its identity entry); the parent is never a request field.

The token is 32 random bytes as hex. The node stores only its SHA-256 in `tokens.json` (0600), with the lineage it was
registered from (below). A write's sender is the name the token resolves to; a request carrying `sender`, `name`, `parent`,
`label`, `from`, `author`, `actor`, `identity` or `as` anywhere in its envelope, params or `fields`
is refused `denied`. At most eight tokens per name: a ninth ends the oldest one except the first the name was given, and
a refused registration issues none and ends none. 10,000 sessions per board and 2,048 tokens per node ("Limits").

**A token is a bearer secret.** The client keeps it in `<state>/client/<board>/<name>.token`, mode 0600 in a 0700 directory. The
file mode keeps it from other operating-system users and from nothing else: any process of the same user can read the file, and
whoever reads it can present the token. The lineage below does not hide it; it limits what a token spends.

**Lineage.** The node reads the process id of the client of the socket (`SO_PEERCRED` on Linux, `LOCAL_PEERPID` on macOS) and the
processes above it, with their start times, up to and including the shell of its terminal (the leader of a session that has a
controlling terminal), or to the top when it has none, and records them with a main session when the session is first registered,
and with every token it issues. A harness that runs each command in a session of its own, with no terminal, is still its terminal's:
the walk goes on past such a session. Two lineages share a process when they end in the same one, the shell of the terminal
or the app at the top. Two things are answered only to a process of the lineage a session was registered from:

- **Registering again.** A later `register` of a main session is answered with a token only to a process that shares it. Any other
  process gets `denied: this session is registered from another process lineage`, no token, and the tokens the session holds go on
  working. A session that does not exist yet registers from anywhere and holds no grant. A subagent registers with its parent's token
  from any process, and its token carries its parent's lineage, not the registering process's.
- **Spending a grant.** A token presented from a process that shares none of the lineage it was registered from still reads, posts,
  claims and leases as the session, and spends no grant: the request is refused saying the grant is held but the token is used from
  another terminal or process tree than the one that registered it, and that a person moves the session with `rebind`. A subagent of the
  lead that runs in a terminal of its own therefore reads and sends and spends nothing it inherits; the grants a lead's subagents hold
  (`note_verify`) are spent from the lead's terminal. A token with no lineage recorded spends no grant either.

A person at the terminal where a session now runs moves it there with `poolhouse-workspace rebind HARNESS SESSION`: an enrolled person
key signs the board, the session's name, this node's device fingerprint, a nonce used once (remembered across restarts), the time (five
minutes) and the terminal it was signed in (`anchor`: the shell of that terminal, which the request must come from), with a typed
`yes`; the node makes the caller's lineage the session's and ends the tokens it held; the harness started in that terminal registers as
the session. Tokens of its subagents stay bound to where they were registered.

On a device in a pool a typed `yes` moves no session. `rebind` with the terminal's `anchor` and no `approval` makes a request that the admin
phones are shown (kind `rebind`, the session, the board and the terminal inside what the phone signs) and answers its id and a number; the
person picks that number among three on the phone, which ties the tap to the terminal in front of them. The same call with the request's id
as `approval` answers `{state: pending, moved: false}` until the tap, then makes the caller's lineage the session's once, from the terminal
the request names, within ten minutes of the tap; the request is then `used`. A session has one request waiting at a time and at most three
an hour, and the call spends no nonce. A device with no admin phone of the pool added refuses the request.

A main session with no lineage recorded (registered before lineages were kept, by a process the node could not read, from another
process id namespace, or under `hidepid`) is taken again only by a process that presents a token of that name, which binds it to where
that process runs; any other process is refused saying a person moves the session with `rebind`. No process adopts a name by being the
first to ask. Its old tokens read and send and spend nothing until it is bound. The client presents the token it keeps for a session
when it registers that session again (`board.session.register`).

What the lineage stops: a process in another terminal (or another app) than the one that registered a main session, whether it learned
the session id from the transcript files of the first or took the token from its file, for everything a grant allows. What it does
not: a process inside that lineage, which includes the subagents and tools of the lead's own harness (they run as children of its
process, so as far as the node can tell they are the lead, and they can read the token file); a harness that has no terminal at all,
where everything started by the same app shares the app at the top; a registrant whose parent has gone (a daemon) has no parents to
share, is its own lineage, and is bound again only after a `rebind`; a process of the same user that starts itself from the lead's
terminal; a process that reads the token file and reaches the node from a process the lead started (the lineage shows who sent a
request, not whose secret it is); and Windows, where the peer is not read yet, so nothing is recorded or refused there. Programs that
register a session of their own use one id per terminal (`board.terminal.terminal_id`), so each terminal has its own.

## Local API

Frames are a 4-byte big-endian length (at most 1 MiB) and JSON. The socket is
`<state>/node.sock`, mode 0600, in a 0700 directory; the peer's uid is read with `SO_PEERCRED`
(Linux) or `getpeereid` (macOS) and a different uid gets one `denied` frame and is closed; the peer's process id is read the same way
and `register` and `rebind` use it ("Identity"). One node
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
`invalid`. A refusal an agent can say to a person also carries `say` (one sentence, at most 160 characters), `needs`
(`approval`, `grant`, `device`, `time` or `none`) and `then` (`wait`, `retry`, `ask-owner` or `stop`).

| method | needs | params | result |
|---|---|---|---|
| `hello` | | | `{node, version, pid, fingerprint, device (the certificate fingerprint grants and approvals name), build, build_seq, wire}` |
| `lineage` | | | `{lineage}`: the processes the node reads for the one asking, from it up to the shell of its terminal, each `{pid, start}`; the last is the `anchor` a person signs for |
| `register` | board | `model, harness, session` (token = parent, or the session's own token) | `{name, token, created, parent, origin}`; a main session already registered is answered only to its lineage, and the token it gets is bound to the lineage it registers from |
| `register_person` | board | `session` (token = none, or the person's own session's token) | as `register`, for the person's own main session (harness `poolhouse-ui`, model `person`), which the node marks `person`; `register` and `whoami` refuse the harness `poolhouse-ui` |
| `rebind` | board | `harness, session, anchor`, and in no pool `at_ms, nonce, by, sig` (a person key's signature), in a pool `approval` (the id of the request) | `{name, moved, tokens_ended}`; in a pool the request with its `id` and `code`, then `{state, moved}` until the phone answers |
| `whoami` | board, token | `model, harness` (claim what you run on) | `{name, board, target, identity}` |
| `session_lookup` | board | `harness, session` | `{name}` (denied when none is registered) |
| `agents` | board, token | `retired` | `{agents: [{name, parent, family, model, model_state, harness, retired, retired_reason, person, seen_ms, idle}]}`; retires the sessions idle for a day first |
| `retire` | board, token | `target` (empty = yourself), `reason` (`done`, the default, `revoked` or `forged`) | `{name, retired, reason, by, tokens_revoked, leases_released, subagents_retired}` |
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
| `claim`, `reserve`, `release`, `claims` | board, token | `kind, key, ttl_s, pid, local` / `resources` (`[{kind, key}]`, at least one), `ttl_s, pid, local` / `kind, key, held_by` (a spawned subagent of the caller whose claim to give up) / `kind` | `{changed, claim}` / `{claims: [{changed, claim}]}` / `{kind, key, released}` / `{claims}` |
| `audit_event` | board, token | `event` (`agent.effort` or `authority.set_denied`), `subject`, `detail` (one clean line each, at most 300 characters) | `{recorded}`; writes an audit entry under the caller's name; any other event is `invalid` |
| `notes` | board, token | `ref, query, kind, all, limit` | `{notes}` |
| `note_verify` | board, token | `note, exit, out_sha` | `{notes: [the note]}` |
| `link`, `unlink`, `links` | board, token with the grant `link_read` or `link_write` (make), `link_remove` (end) | `to, channels, mode` / `id` | the link |
| `project_add` | token of a session of that board; for a place that is not a git repository, the grant `source_add` | `id, kind, path` | the project |
| `source_add` | token of that project, with the grant `source_add` | `id, kind, path` | the project |
| `project_list`, `project_resolve` | | `path` | projects / `{board}` |
| `status` | (board, token for detail) | | counts; with a token, the board's detail: `sessions`, `entries` by kind, `damaged`, `rejected` |
| `shutdown` | board, token | | `{stopping}` |
| `pool_status` | | | the pool id, join policy, this device, `listen`, `join_code_open`, `governance` ("Who governs a pool") and each member with `status` (`active`, `revoked` or `left`), `by`, `connected`, `addr`, `last_seen_ms`, `last_sync_ms`, `last_error`, `reach` (`direct`, `channel`, `none` or `self`) with `reach_hint` (the exact fix when it is not direct) and `network` (`wsl-nat`, `wsl-mirrored` or empty, as the member's hello said) |
| `member_remove` | token with the grant `member_remove` | `fingerprint` | `{fingerprint, status}`; in a governed pool an act of the admins ("Who governs a pool") |
| `pool_create` | token with the grant `pool_create` | `title`, `policy` (default `secure`), `project` | `{pool, name, policy, epoch}`: founds the governed pool of this device, which must be alone in its pool |
| `pool_leave` | token with the grant `pool_leave` | | the `leave` act this device signed with its own key |
| `pool_propose` | token with the grant `admin_manage` | `kind`, `body` | `{act, status, have, need, why}`: makes the act and signs it with the keys this device's signer holds that count |
| `pool_cosign` | token with the grant `admin_manage` | `act` | adds this device's signer's signatures to an act the pool holds |
| `pool_submit` | | `act` | takes an act a person's key signed elsewhere (the typed-yes path, a phone, another device); the signatures are the authority, so no grant |
| `pool_draft` | | `kind`, `body` | `{act, sign_bytes, summary}`: the act this pool would take next, unsigned, the bytes a key signs and what a person is shown, for a key that signs outside the node |
| `pool_draft_found` | | `title`, `policy`, `project`, `keys` (each `{key, person, label}`; `key` is an Ed25519 key of 64 hex digits or a phone's P-256 point of 130, and the charter says which as `alg`; a key that names its strength is refused) | `{charter, anchor, accept, summary}`: the charter with this device as its `first` device and blank acceptances, each key `strong` only when a phone enrolled it on this device and `weak` otherwise, and for each key its `alg` and the bytes it signs to accept it; the rig or person signs them (a P-256 key in strict DER with `s` in the lower half of the group) and hands the charter back to `pool_found` |
| `pool_found` | token with the grant `pool_create` | `act` (a founding act whose keys accepted it), or `charter` (the drafted one with the acceptances filled; the node makes the act) | `{pool, name, policy, epoch}`: founds the pool of an act signed elsewhere; the charter must name this device as its `first`, which is then the pool's first member with no admit |
| `add_device` | token with the grant `add_device` | `passphrase` (else a six-digit code is made), `ttl_s`, `presigned` (true: open the window with no signer on this node, for a device an admin already signed an admit for and submitted here; the window admits no other) | `{code, expires_in_s, port, fingerprint}` |
| `join_pool` | token with the grant `join_pool` | `host, port, passphrase`; without a passphrase `fingerprint` (join under `open`) | `{pool, peer}` |
| `sync_now` | token with the grant `sync_now` | | `{reached}` |
| `shard_call` | token with the grant `shard_call` | `device` (name or fingerprint), `op`, `args` | the device's answer to one test-shard op (docs/test-farm.md); the node adds `op` and the session as `by`, and refuses `op`/`by` in `args` |
| `shard_consent` | (token with the grant `shard_consent` to change) | `enabled`, `python`, `repo` | `{enabled, python, repo, by, at_ms}`; turning it on or off writes an audit entry to the pool board; shards are off until a person turns them on at that device |

| `lease_acquire`, `lease_renew`, `lease_release`, `lease_list`, `lease_wait` | board, token | see "Leases" | a lease |
| `grant_issue`, `grant_revoke` | board, any session (it only carries the request) | the signed grant or revocation, see "Standing grants" | `{gid, action, until_ms, entry}` / `{gid, revoked, entry}` |
| `grant_distrust` | none, any session (it only carries the request) | `key, since, pool, person, at_ms, reason, sig`: the signed distrust, see "Standing grants" | `{key, distrusted, entry}` |
| `grants`, `grant_log` | board, token | `limit` (log) | every grant with its state, the entries that name none that count, the registry / the newest grant entries |
| `person_enroll`, `person_unenroll`, `person_keys` | token (enrol, unenrol), none (keys) | `public, about, at_ms, proof` and `secret` (the first key) or `by, approval, approved_ms, nonce, anchor`; `fingerprint, at_ms, by, nonce, anchor, sig` | `{fingerprint, keys}` / the enrolled person keys, each with its `alg` (`ed25519` or `p256`) |
| `approval_request`, `approval_status`, `approval_cancel`, `approvals` | board, token | `asks` (one to eight of `{action, resource, lasts_ms, uses, review}`), `note`, `task`, `ttl_s`, `expect_policy` / `id` / `id` / `state` | the request with its plain-words `text` and `challenge` / its state and the grants it wrote / the request / the session's own requests; docs/phone-approval.md |
| `approval_pending`, `approval_decide` | token (any registered session; a session only carries a decision) | `key, sig, at_ms` / `id, decision (approve, approve_repeat, deny, block), key, sig, text_sha256, pick` | the session's own waiting requests, or every one with the signature of an enrolled key made in the last thirty seconds / the request decided, once, by the signature of an enrolled key |
| `phone_info`, `phone_offer`, `phones` | none, token, token | `secret` / / | where phones reach this node and its pinned certificate / a code for a further phone (once a person key exists); in a pool its link also carries `admit`, the terms a phone accepts being listed under / the phones held |

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
A message to a session that was retired is `denied` (`<name> is not a session of this board`), with one exception: an
`answer` whose `reply_id` names a message that session wrote is accepted and recorded on that thread like any
other, so the question it left is closed and the audit shows who answered it. Nothing is delivered to the retired
session, which has no token to read with; a question, or an answer that names no message of it, is still `denied`.

**Rooms and subscriptions.** A room is `#name` (lower-case letters, digits, `.`, `_`, `-`, at most 40, never the name of
a fixed channel such as `#general` or `#dm`). It is a fold of the board's `room` entries, so every device computes the
same members: the first `create` makes it, an open room is joined by whoever asks, a private one only by its maker's
`add`, and a member leaves. Members are the names in the writers' own logs. A message to a room is written and read
only by its members (`read` hides it from anyone else whatever the filters), a reply must stay in the room of the
message it answers, and a board holds at most 200 rooms (the sessions of one process tree made at most 16 of them) of at most 128 members.
A spawned subagent makes, joins and subscribes to nothing. Subscriptions are kept on this device beside the board
(`subs.json`, never synced): each delivers only what arrived after it was made, a session has at most 256 and three
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
changes the claim. A change of the model or the harness is an identity entry; a change of only the model state (`inherited` to `claimed`) is not. Registering a session that has a parent without
that parent's token, or under another parent, is `denied`, and so is registering a retired subagent.
`retire` revokes every token of the session, ends its leases (written to the board), marks the
identity retired and writes one identity entry that says who ended it (`retired_by`) and what that released (`detail`). A session retires itself, and a main session takes its subagents with it;
a parent (any ancestor) retires a descendant; a session retires no other main session. The end has a reason, recorded on the identity and in its
ending entry: `done` (an ordinary end, such as a worker whose work is finished) or, given only by a parent and never
by a session about itself, `revoked` or `forged` (an end for cause, after which the reviews the session wrote no longer count). The node itself ends a session as `idle` (see below). A main session that ended `done` or

`idle` registers again as the same session, from the lineage it was registered from and on the same terms as before it ended
(a retired subagent never does); its old tokens stay refused and it is given a new one.

**Idle sessions.** A session is *idle* when it is not retired and has neither made a request within 30 minutes
(`child_beat_s`) nor holds a lease whose process still runs; `agents` marks it `idle`. No count of live sessions is kept: any number register, from one terminal or many. The node retires a session that has been idle for 24 hours (`idle_retire_s`;
its tokens end, its leases are released and the board gets its ending identity entry) whenever it lists sessions or registers one.

**Claims** are leases of one `claim` resource taken without waiting. `reserve` takes a set of them in one call of the
node: the set is checked against what others hold before the first is taken, so it is taken whole or refused whole (the
refusal names the holder of the first resource in the way), a claim the caller already held is renewed and kept, and a
set refused by a claim another device holds gives back what it took. A branch claim is held under the
board's name, so two boards may claim the same branch name; ports, servers, paths and installs are the
device's. `claims` lists the claims of the caller's board with the owner. A holder's `pid` and `ttl_s`
behave as for any lease (dead process or expiry drops it). A claim taken with `local` waits only for this device's
sessions: a branch claim another device holds on the board does not keep it from being granted (the landing
runner's claim is one).

**Notes** carry `nkind` (decision, rule, fact, question), `title`, `body`, `source`, `tags`,
`supersedes` (references to older notes, a bare number for a note of this device or the full id; a
verified note cannot be superseded), `verify_cmd` and `ttl_days`. `notes` folds them: `trust` is
`test-verified` while the latest passing `verify` entry for the note's current command is fresh, else
`agent-claimed`; `stale` once `ttl_days` passed since the last verification (or the note). The node
never runs `verify_cmd`: a client does and records `exit` and the SHA-256 of the output with
`note_verify`, and the entry stores the command the note carries. Recording one is the grant
`note_verify`.

**Landing reviews** are the `landing` entries of the board (channel `#landing`): a `review` names a plain `branch` (never
`main` or `master`), a full 40-character `sha`, a `verdict` (`accept` or `reject`) and the `standing` the node stamps on it
(the sender's name, model and model state at that moment, never taken from the caller). `land_review` writes one for the
live session behind the token. `land_accepts` (`sha`, and optionally `branch` and `worktree`) answers the push hook and the unmerged runtime deployment: the last review of
each reviewer of this device for exactly that commit under any branch name, without the caller and, given a branch or worktree,
without any session that ever held its claim or was started by one (through any number of subagents); a session of that kind
cannot write a review of the branch either, and the creators of record come back with the answer. Entries of the
retired landing queue (`request`, `cancel`, `pause`, `resume`, `state`, `beat`, and reviews of a request) fold and count
for nothing.
`landing.rejected` and `landing.standing` drop a reviewer retired as `revoked` or `forged`. No model identifier decides who may land, review or
coordinate: the model on a review is the id the session claimed, kept as a label.

A review counts only when this device wrote it. The device list (`<state>/land_trust.json`, kept per pool, empty by default,
never replicated) decides whose `task` and `grant` entries count besides this device's own; it is changed by `land_trust`
(`device` the certificate fingerprint, `trusted` a boolean): the caller is a session with no parent holding the grant
`land_trust`, the device must be an active member and not this one, and the change is an audit entry (`land.trust`,
`land.untrust`).

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
spends a retry, and a blocked task is resumed the same way. `task_admit` is the worker's read-only check before it works: it returns the working task with its lease and scope only to the worker the task is assigned to, and renews nothing. A submission is stamped with the model of the lease and the
worker's own registration; a review names the exact proposal and hash and needs every acceptance criterion passed by an
independent check. The worker never reviews its own task. In `open` mode (the default) any other session of the board
reviews; in `strict` mode only a person (a session with no parent that holds `task_authority`), a designated reviewer, or the
task's maker when it is the worker's parent. `task_mode` shows the mode and a person sets it. A task with a `scope` (a
checkout reserved for it: an object of at most 16 fields, the latest write wins) stays `accepted` after its review until
`task_complete` records its landing. `task_scope` is written by the task's worker, by the worker's parent and by whoever has a hand in
landing it (its worker, its parent, its maker, its reviewer, or a session that holds `task_authority`), and `task_record` keeps
records on the task the same way, each under a `kind` and an id: `integration`, `attempt`, `event` (appended; never merged),
`allocation` (what the worker's parent recorded when it assigned the task: the model lease, the execution configuration, the checkout, the
scheduler's process) and `evidence` (the runner entries a checkpoint or a proposal cites, verified on the worker's device before they are written);
a later record of one id adds its fields to the first. Before the worker claims, only its parent writes `allocation` and the scope; they are
written by the parent or the worker of an assignment that stands (a queued task: one whose lease is still a live grant of the worker's
model; a task a worker holds: the assignment that worker claimed it with). A task's specification (title, description, acceptance, deps,
capabilities, limits, source key, assignees, reviewers) is the signed `create` entry and no later entry or call carries one, so a task has
no specification hash to check. A
body holds no fractions: times are whole milliseconds. Followers (`task_watch`) hear each state change as a direct
`status` message from whoever changed it. Only the entries of this device and of a device on the landing list count; the
others are shown under `foreign_ignored`. The `task` claim kind is the node's own: `claim` refuses it and `claims` does not list it.

**Stubs.** One check waits for the trust ledger and is a stub that allows nothing: model verification (no session is ever
`verified`, so a grant naming a verified model or a verified class holds for nobody yet).

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

**The registry.** Eighteen actions, fixed in `grants::spec::ACTIONS`: `member_remove`, `add_device`, `join_pool`,
`sync_now`, `shard_call`, `shard_consent`, `land_trust`, `task_authority`, `note_verify`, `quarantine_release`,
`file_delete`, `link_read`, `link_write`, `link_remove`, `source_add`, `pool_create`, `pool_leave` and `admin_manage`. Anything else is `invalid` to grant. **The floors** are refused by name, at issue, in the fold and at every
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

**Who may sign.** On a device in no pool, any key enrolled in `persons.json`. On a device in a pool (founded or joined; leaving keeps it so) the
action decides. A device-local action (`link_read`, `link_remove`, `source_add`, `quarantine_release`, `pool_leave`: everything it does stays in this
device's own state) is granted by any key enrolled here, as before. Every other action, including one added later, reaches beyond the device and counts
only from an approval a pool admin key signed: the key is listed in the charter in force with the admin role and strength strong, its own acceptance
verifies, and this device knows it as a phone's key (the mark a phone's own enrolment puts in `persons.json`). `grant_issue` for such an action is
refused ("In a pool your phone gives standing grants: ask for one and tap approve."), and an `issue` row that carries no approval, or that a key outside
that set signed, is listed `ignored` with the reason. The approval row carries the decision (`approve` or `approve_repeat`) the signature was made
for. A revocation of a grant of the pool counts from any admin-role key the pool listed in any epoch, strong or weak, checked against the charter and
never against `persons.json`; a revocation of a device-local grant also counts from any key enrolled here. `approval_decide` takes a decision only
from such an admin key. `grants` reports `governance` (`governed` or `unfounded`), `authority` (the admin keys that count now), `may_issue`,
`may_revoke` and `registry.device_local`, and `poolhouse-workspace grant` and `grants-setup` refuse in a pool before they ask for a typed `yes`.

**Who is added, told and shown requests in a pool.** A phone whose key the charter in force lists as a strong admin key joins with the one-time code
and the proof of its key and needs no approval: a device that holds only a typed-yes key gets the admin's phone with one scan. Any other phone waits for
an admin phone to approve it. Pushes, `list`, `get`, `wait` and `approval_pending` go only to an admin phone (an admin key this device knows as a
phone's, not distrusted); another phone is shown no request. `person_unenroll`, a phone removal and its request refuse a key the charter lists ("it
leaves through an admin removal") before a nonce is spent. `approval_request` is refused while this device holds no admin phone. Every request ticks
the pool once before it is served, so a removal whose hold has run counts at once. A `distrust` row (`grant_distrust`) is signed by an admin-role key
of the charter in force, names a key the charter lists and the epoch it was listed at, and the pool's id; it stops that key counting as an authority
key (grants, decisions, pushes, reads) on every board at once and ends only when the key leaves the charter or is listed again. It can only narrow:
a distrusted key's revocations and distrusts still count, and no row undoes one. A pool whose `acts.json` or `seen.json` is missing or malformed
stops the node. A pool set aside for another charter version leaves `grants_floor.json` at the state root: no grant row written at or before it
counts on any board.

**Who may sign on a device in no pool.** `person_enroll` takes `public`, `about`, `at_ms` and `proof` (the key's own
signature over the enrolment). The first key also takes `secret`: `poolhouse-workspace grants-setup`, run by a person at a terminal, asks the node (`presence_ask`)
to allow the session to add the first key, and the node asks the person itself: the operating system's own prompt (a password or a fingerprint,
on macOS and Linux, raised only by the installed software on the real home: the launcher passes `--presence os` only when
`poolhouse.real_machine.allowed()`, and the node itself asks only for the account's own node directory (`~/.poolhouse/node`, that directory and
no other under it, never as the administrator account). **On the real home the operating system's prompt is the only presence**: a computer
with no such prompt (Windows, a Linux without `pkexec`) refuses and says so plainly. The prompt shows the words of the key being added, the yes
is for that key only (`presence_ask` takes its `public`; another key presented with the secret is refused), and the prompt ends when two minutes
are up whether or not anything polls. For a node on any other state directory (a server, a container, the pool check's rig, a test) started
with `--presence terminal`, the node writes the question on the terminal device the asker names and reads the typed `yes` itself; it refuses that
mode on the real home, because the program that asks could open a terminal of its own and answer. A node started with no `--presence`
cannot ask and refuses the first key and the first phone. On a yes the node makes the random secret in its memory (never a file), hands it
once to the session that asked (`presence_status`), accepts it once and for ten minutes, and spends it on any guess, so a wrong secret ends the
attempt; a node that holds a key never asks. There is no cargo feature, argument or environment variable that answers for the person, in any
binary: the crate's own tests put a stand-in on a library field that no binary sets, and the rig's person types `yes` at a pseudo-terminal
(`tests/pool_check/wire.py`, `answer_presence`). Windows has no operating-system prompt the node can raise, so **the first key and the first phone
on the account's own node directory are refused there** until a presence step is built for it. Windows has the terminal presence for any other state
directory (a server, a test, the pool check's rig): the asker names the console by the id of a process that runs in it (`terminal` is a decimal
process id, which `poolhouse.board.presence.terminal_name` gives for the command window it runs in), the node attaches to that console (a node
started by its supervisor has none of its own), writes the question there, reads the typed `yes` itself with no echo or line editing but its own,
and leaves the console before the answer is known. Only a process of the node's own user may be named, and only one question is on a console at a
time. The real-home check there asks the system for the account's directory (never `USERPROFILE`) and refuses when it cannot. The pool check's
rig plays the person there at a ConPTY pseudo-console (`pywinpty`, `tests/pool_check/terminal.py`) that holds one idle process, and types `yes` and
Enter into it, against the same node binary. A session that registered first, or any session,
therefore enrols no key. Once a key is enrolled, `by`, `approval`, `approved_ms`, `nonce` and `anchor` add a second: `approval` is an enrolled key's signature over the
new key's public key, name and proof time, the approver's fingerprint, this node's device fingerprint, the approval time (five minutes),
the nonce and the terminal it was signed in (`anchor`, the shell of that terminal, which the request must come from). The node accepts a
nonce once, and remembers it for ten minutes across restarts (`persons_nonces.json`), so an approval enrols that key on that node once,
from that terminal, and nothing replays it. `person_unenroll` (`fingerprint`, `at_ms`, `by`, `nonce`, `anchor`, `sig`) removes a key when
an enrolled key signs the removal for this node, once and in the terminal it was made in, with the same node fingerprint, nonce and anchor
in the signed body: a removal signed for another node, used twice, or sent from another terminal removes nothing. What the removed key
signed stops counting at once, and a removed key is never enrolled again (the node keeps the removed fingerprints in
`persons_removed.json`): an entry it signed with a time ahead of its removal, on this device or arriving from another, is as void as one
signed before it, since the node has no record of when a foreign entry was written that a signer could not choose. A person who wants
that device back enrols a new key made there. The last key is never removed, and each enrolment and
removal is written to the pool board. The secret is in the node's memory, so a program that can write the state directory (a planted `enroll_secret.json`) enrols nothing,
and a session that asks gets a secret only when the person answers. What an agent can still do is ask repeatedly (the node asks one question at a
time, refuses for a few minutes after a no, and the prompt names the session that asked), spend the secret of the person's yes with a wrong guess
so that the person asks again. A node that asks on a terminal (never on the real home) can be answered by a program that opens a pseudo-terminal
of its own, which is why the real home allows the operating system's prompt only. On Linux a polkit rule that lets the account authenticate
without a password (or `pkexec` set up to allow the action to anyone) lets the prompt return yes with nobody answering: that is accepted, and
the person who runs such a rule has already given that power to every program of the account.

**Accepted residual: `persons.json` is a plain file.** The OS user can edit it with the node stopped (documented at `src/grants/person.rs`), so
a program that can stop the node and write its state directory can plant a key, including one marked `phone: true`, which a pool's charter
then lists as strong; the operating-system sandbox of agents (slice S0 of
`docs/compromised-agents.md`) is what closes it, not the node.

**A phone is a person key too.** An ECDSA P-256 key made in a phone's Keystore is enrolled in `persons.json` with `alg: p256` and `phone: true` (its fingerprint is the SHA-256 of its 65-byte
public point) and signs only approvals (docs/phone-approval.md): an agent asks (`approval_request`), the node writes the question in plain words, the phone's key
signs a challenge that binds the text, the node and the request, and an approved request writes ordinary `grant` entries whose body carries that approval instead
of a signature over the grant, so they count wherever a grant a person typed `yes` for counts. The phone endpoint (`--phone ADDR`, or the address in
`phone.json` in the state directory) is HTTPS on one P-256 certificate (`phone.cert`, pinned by the code a person scans); `phones.json` holds each phone, its token's hash
and its push address; `approvals.json` the requests; `fatigue.json` the declines, timeouts, holds and pushes that keep a request made again and again from wearing the person down (docs/phone-approval.md, "Approval fatigue"); `phone_offer.json` the hash of the one-time code a person's command (or the node, once a key exists) left for the next
phone. The first phone is enrolled by that code alone; every later phone waits for a key the node holds to approve it. On a device in a pool a phone the charter
lists as a strong admin key is enrolled by the code alone; any other phone sends, with its `add`, its `acceptance` of being listed as an admin key of the person
the code is for (the terms are in the link's `admit`: pool, charter hash, epoch and person, which the node reads from its own state again), and the request an
admin phone is shown carries the unsigned `admin_add` act. That phone approves with two signatures, `sig` on the request and `act_sig` on the act; the node hands
the act to the pool at that tap, so the hold begins there, and the new phone is an admin key when the hold has run. The person is the one whose admin phone the
code was made for (the one person whose admin phones the device holds, or the person of the phone that made it), and only a phone of that person approves. A
device with no admin phone refuses a phone the charter does not list before its code is spent.

**Recovery.** When the only key is lost or no longer trusted, the owner stops the node, deletes `persons.json` in the node's state directory, starts the node and runs `grants-setup` again. It is an offline step at a terminal: with
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
signing helper process under a different operating-system identity that holds the key and puts the question itself. A phone's key is the first of these
(docs/phone-approval.md): its signature needs the person's fingerprint on another device, which no process on the computer can answer. The
other two are planned and not built. The person's screen (the UI daemon) has no terminal, so a refusal there names the grant and the person runs the
command at a terminal; an approval dialog in the screen is planned.

What a signature is good for once it is made: a grant names the board, the session, the action, the device and its end, and is a
standing permission until then. Every other signature (the enrolment approval of a key, the removal of a key, the move of a session)
names this node by its device fingerprint, carries a nonce the node takes once and remembers for ten minutes across restarts, is fresh
for five minutes, and names the terminal it was made in (`anchor`), which the request must come from: a captured one is no good on
another node, a second time, later, or from a process of another terminal. The person key and the session token are two secrets
with two kinds of custody: the key is behind the typed `yes` and the keystore; the token is a file only the file mode keeps from
other users, bound to a lineage ("Identity").

**What counts.** `grants::check(node, holder, action, scope)` answers true, and writes the use, only for a live grant: this
board, this device (or `*`), a resource the pattern covers, a session or class the grant names (a class never covers a subagent
unless `main` is false), before `until_ms`, with uses left, not revoked. `holds` asks without spending. An entry written on
another device counts only when that device is on this device's landing trust list and still a member, the signature verifies
against a key enrolled here, and the action may travel: `task_authority`, `note_verify`, `sync_now` and `shard_call`. The rest (the
pool actions, `land_trust`, `shard_consent`, `quarantine_release`, `file_delete`) are granted on the device they apply to.
A revocation or a use from a device this one does not trust changes nothing.

**Where it is checked.** `member_remove` (resource: the fingerprint), `add_device`, `join_pool` (the
host), `sync_now`, `shard_call` (the device), `shard_consent`, `pool_create` (the title), `pool_leave` and `admin_manage` (the kind of act, or
`cosign`) before they run; a grant lets a session ask, and an admin's signature is what makes a governing act true for everyone; `land_trust` (the fingerprint), besides having no parent; `task_authority` (the task, or `mode`)
wherever a session with no parent acts for the person (assign, review in strict mode, block, recover, set the mode, record a
landing), spent only where it is what lets the call through; `note_verify` (the note), `quarantine_release` (the held id) and
`file_delete` (the handle); `link_read` and `link_write` (`FROM>TO`, for a link of mode `ro` and `rw`), `link_remove` (the link id) and `source_add` (the project id,
for a further place of a project and for a project whose first place is not a git repository).
`note_verify`, `quarantine_release` and `file_delete` are used only by a session registered on the board they act on, never through a
link. A link goes only to a board this node already hosts, so `link` creates none. The use is on the board as a `use` entry naming the grant, the session, the action and the target.
Every check applies the lineage of the token ("Identity"): a token used from a process that shares none of the lineage it was
registered from holds no grant at that moment. A link, the end of a link and a project or a place are first checked and made, and the
grant is spent once they were made, so a link that cannot be made (a channel that is not a `#name`, a link that is not there, a place
that does not exist) spends no use; the other sites spend before they act.

**Commands.** `poolhouse-workspace grant ACTION --to NAME [--with-subagents]|--class FAMILY|--account MODEL --scope RESOURCE --until 30d [--uses N]`,
`revoke-grant ID`, `grants` (who holds what, and its state; a grant that names a verified model says it matches nobody yet),
`grant-log` (issues, revocations and uses), `grants-setup` (below), `person-proof` and `person-enroll` (carry a key from another
device) and `person-unenroll --key FINGERPRINT`. The six that make or end a grant or a key need a person at a terminal with the key
and a typed `yes` for each thing they sign; `grants` and `grant-log` are for any session, and the public
`ph.board.Board.grants()` is planned (docs/api.md). A refusal for want of a grant names it; a command a person runs asks whether to
sign that grant for one use and two minutes and, on a typed `yes`, asks again as the person's own session of that terminal
(`board.person.acting`: the grant is signed for that session, since another terminal's token spends no grant here, and the use is
written under its name); a process an agent started, or one with no terminal, is not asked.

**The first-run bundle.** `grants-setup` goes to the lead's own sessions, named at setup: `--session NAME` (repeat for several), or
picked from the list of the board's main sessions it prints, where the model and harness are what each session says about itself.
It enrols the person's key if the node holds none and signs one grant per action and session on this device, 30 days each:
`task_authority`, `shard_call`, and `note_verify` for the session's subagents too. The
exact list is the one question, answered by a typed `yes` on the terminal. Nothing else: the pool actions, `shard_consent`, `sync_now`,
`land_trust`, `quarantine_release`, `file_delete`, `link_read`, `link_write`, `link_remove` and `source_add` stay with the person's own
commands (`poolhouse-workspace grant shard_consent --to NAME --until 30d`, each its own typed `yes`). Each is revoked
by its id. A class grant stays in the registry but is refused by `grants-setup --family` and `grant --class` unless the person passes
`--i-understand-registration-is-open`: it is held by every session that says it is of the family, which is any process.

**What the lead's lineage can do with the bundle.** For 30 days, the lead's main session and everything that runs from its terminal
and process tree (its subagents, its tools, any process the harness starts, and any process in them that reads the token file) can:
`task_authority` (act for the person on tasks: assign,
review in strict mode, set the enforcement mode, record a landing), `shard_call` (send a test run, its files and its start to another device of
the pool that allowed this one, and read and cancel it: `shard_put`, `shard_start`, `shard_status`, `shard_cancel`, `shard_caps`) and
`note_verify` (record that a note's command was run, which sets the note's trust). The subagents of the lead hold `note_verify` only.
The lead's lineage cannot do what the bundle leaves out: it cannot turn test shards on, or name which devices may run its tests
here (`shard_consent`, the most sensitive grant, since it lets code from another device run here), cannot ask for a sync (`sync_now`),
and cannot touch the pool, landing trust, quarantine, files, links or places without a grant of a person's own.

**What the grants protect against, and what they do not.** A grant names one session. A token is a bearer secret, kept in a file
every process of the same operating-system user can read, protected by the file mode against other users only. What the node adds is the
lineage: a main session registers again only from the lineage it registered from, and every token spends a grant only from the lineage
that registered it ("Identity"). So a process that merely claims a model family holds nothing a grant for one name gives, and a
process outside the terminal and process tree of a registered lead cannot spend the lead's grants, by knowing its harness and session id
or by taking its token from the file; it can read and send as the lead, and the lead's person moves the session to that terminal with
`rebind` when that is wanted. They do not protect against a process inside that lineage (the lead's own subagents and tools run as
children of its harness and are the lead as far as the node can tell, and a subagent holds a token of the lead's lineage), against a
process of the same operating-system user that starts itself from the lead's terminal or signs with the person's key ("Key custody"),
or on Windows, where lineages are not read yet. A class grant holds for whatever claims the family, which is why it asks for
`--i-understand-registration-is-open`. `--account` and `--verified` name a verified model, no session is verified yet, and the listing
says such a grant matches nobody. A project is added by a session of its own board, with the grant `source_add` for a place that is not
a git repository, 256 to a node, never in or around another project's place, never the user's home or a
directory around it, and a person's approval of a git project is not asked ("Boards, projects, links"; "Limits"). The trust ledger (levels from outcomes, suspension on forgery signals, credit to a
model-and-version account) is not built; a grant is the only thing that widens what a session may do.

## Limits

What a node holds is bounded, so no process fills the node's memory or disk. A rate is kept in memory (a restart clears it) in a
table of at most 1,024 keys; a new key past the bound drops the key used longest ago, so no key is refused for the others'. A use is
counted when it was made: a refused or failed attempt counts for nothing. The rates are the ones that guard a person's approval, a join, a
phone and a pool's asks. `docs/limits.md` lists every limit with its value, file, what it protects and who changes it.

| what | limit | refused as |
|---|---|---|
| sessions a board holds | 10,000 | `quota` |
| tokens a name holds | 8 (a ninth ends the oldest but the first) | the oldest ends |
| tokens a node holds | 2,048; when full, the tokens of sessions idle for 30 days end | `quota` |
| boards a node hosts; boards one process tree made | 256; 32 (a board that holds only the ends of its sessions is removed to make room, once it has been quiet for an hour) | `quota` |
| links a board has open | 64 | `quota` |
| projects a node holds | 256 | `quota` |
| person keys a node holds, phones included | 8 | `quota` |
| requests waiting on a node; waiting from one session; requests one session makes | 64; 8; 6 in ten minutes | `quota` |
| a request's asks, its text, its note | 8, 6000 bytes, 160 characters | `invalid` |
| codes for a phone the node makes | 3 an hour | `quota` |
| connections the phone endpoint holds; a request body | 48; 64 KiB | the connection closes |
| used signature nonces remembered | 256, each for ten minutes, across restarts | `denied` |
| a frame | 1 MiB | the connection closes |
| direct messages unread in a session's inbox | 500, and 100 from one sender | `quota` |
| notes an author keeps on a board | 500 | `quota` |
| idle time before the node retires a session | 24 hours (no lease held, not heard from) | none |

The constants are in `node.rs`, `identity.rs`, `links.rs`, `registry.rs`, `grants/person.rs` and `api.rs`; the limits on what a
session writes are in `writes.rs`, and `<state>/write-limits.json` (optional) sets any of `inbox_pending`, `unread_per_sender`,
`notes_per_author`, `child_beat_s` and `idle_retire_s`. A message
is unread until its recipient reads its inbox with a `since` cursor past it: the node keeps the furthest cursor each session presented
on an inbox read (`inbox_read` in `names.json`), and a recipient on another device is not counted. The unread messages of each recipient and the notes of each author are counted by `tally.rs` as rows arrive (counted again from
the log when the node starts), so a post is checked without folding the board.

The node writes a few direct messages of its own, as `node` (a name no session can hold): to the holder of a claim whose time ran
out, naming the `claim` command that takes it again, and to the parent of a subagent it retired for idleness. A request with a token
the node does not hold is told the session was retired or never registered.

## Leases

One table, `<state>/leases.json` (0600, written atomically on every change), admits everything that
used to be admitted six ways: the GPU, CPU slots, memory, named claims, served models. Code is
`src/lease/` (`types`, `policy`, `table`, `live`, `merge`, `rpc`). Config is `lease-config.json`
(optional; every field has a default): `cpu_slots` (cores), `memory_mb` (0 = no budget),
`max_wait_s` 600, `background_share_percent` 50, `unknown_background_s` 180, `queue_patience_s` 60,
`default_ttl_s` 300, `max_ttl_s` 86400, `max_leases_per_holder` 512, `max_leases_per_lineage` 1024, `max_leases` 4096, `max_worktree_records` 1024. The file is read again whenever its bytes change (at the next lease call), so a
budget a program writes there applies at once; a file that does not parse changes nothing (and neither does one that is not a
JSON object, whose `max_ttl_s` is under 1 or whose `background_share_percent` is over 100), and a deleted one sets the defaults.

**Resources** (JSON objects with a `type`; `device` defaults to `local`, the only device this node
decides; another device is `denied`):

| type | fields | rule |
|---|---|---|
| `gpu` | `device` | one holder at a time |
| `cpu_slots` | `device, count` | counted against `cpu_slots` |
| `memory_mb` | `device, mb` | counted against `memory_mb`; more than the budget is `quota`, less than what is left queues |
| `claim` | `kind, name` | `kind` is worktree, branch, area, port, server or install; one holder per name; worktree, install and area names are absolute paths, and two of them, whatever their kinds, conflict when one path contains the other |
| `model_slot` | `device, model, context, draft, parallel` | one holder per device and model; the shape is kept and shown |

A request names one to eight resources and gets all of them at once or waits. **Order** is
`scripts/testslots_policy.py`: shortest `estimate_s` first, less the seconds already waited; a request
that has waited past `max_wait_s` goes ahead of every ordering and cap, by arrival; arrival breaks
ties (`class` `interactive` estimates 0, `background` estimates `unknown_background_s`). While a
short run holds or waits for CPU slots of a device, a not-yet-aged long run may hold at most
`background_share_percent` of them. A request never overtakes an earlier one that wants the same
resource.

**Holder.** The holder is `board/name` from the token; a request that carries `holder`, `name` and the
like is refused. Only the holder renews or releases. A **tag** (up to 64 letters, digits, dots, dashes, colons
and underscores) names one request among the others of its holder: leases of one holder with different tags are
different parties and wait for each other, the same tag for the same resources is the same request, and no tag is a
party of its own. A program that serves many callers (the serve broker, one generation at a time per request) holds
them all under one session by giving each its own tag. A **quiet** lease (`quiet: true`, only for the GPU and a
`server` claim, which a request takes and gives back many times a second) is granted and ended without a board
entry, is kept in memory (a table that changed only by one is not written to `leases.json`, and
none outlives the node), and is not refused while the device is catching up: it only orders a request to a server already running here.
`lease_list` on the device shows it; no other device does, so a program that keeps renewing one keeps the device's GPU until it
gives it up or its process ends, as with any other holder, and only this device's listing shows who. A holder may pass `pid`: the node records the
process start time and drops the lease when the process is gone, a zombie, or started at another
moment (a reused pid). A `remote` holder has no pid and lives by expiry. Every lease expires
`ttl_s` after the last renewal; a queued request not polled for `queue_patience_s` is dropped. The
sweep runs on every lease call, and at the first one after a restart, so a stale holder is dropped
and a live one kept. A lease is kept for as long as its holder renews it, and the longest one renewal may keep it is the configuration's `max_ttl_s`.

**Takeover.** When a request is granted resources a dead or expired holder had, the node writes an
audit entry (`event lease_takeover`, `subject` the new lease, `detail` naming the old holder and why).

| method | params | result |
|---|---|---|
| `lease_acquire` | `resources, class, estimate_s, ttl_s, pid, remote, wait, tag, quiet` | the lease, or `{state: "busy", blockers}` when `wait` is false and it is not free |
| `lease_renew` | `id, ttl_s, pid` | the lease; `pid` binds a held lease to that running process, so it ends with it |
| `lease_release` | `id` | `{id, released}` (a queued request is cancelled) |
| `lease_list` | | `{leases, config}`; other boards' holders show as `other-board` |
| `lease_wait` | `id, timeout_ms` | the lease once `held`, or still `queued` at the timeout (at most 10 minutes; the node's lock is not held while it waits) |

A lease is `{id, state (queued|held), holder, resources, class, estimate_s, since_ms, granted_ms,
expires_ms, ttl_s, pid, remote, position, wait_estimate_s}`. Rust: `node.leases` (`Leases`, with
`tick`), `lease::table::Table`, `lease::rpc::call` and `serve`, `lease::live::is_alive`.

**On the board.** Every grant and end of a lease that holds something another device can name is a `lease` entry (`#leases`) written under the holder's
name: `{lease, action: acquire|release|expire|dead|abandon, resources: [lines]}`. `lease::merge::view`
reads them back in the board's total order: the earliest acquire of an exclusive resource holds it
until its own end entry; counted resources sum. Gpu and model slots are scoped to the origin that wrote them (a device's table decides its own, others only
read); a branch claim is pool-wide, and a local request for one a peer holds on the board
queues behind it (`merge::foreign_holds`). **Device-only claims write no entry.** A claim of a
worktree, an area, a port, a server or an install (`lease::types::DEVICE_CLAIMS`) names a thing that exists on one device
(a path of its disk, a port or server of its network, an area spelled under the path of its repository), so no other device can hold
or ask for it: the device's table keeps it (`leases.json`) and the board is not told. A lease that holds only such claims is
not mirrored (`Lease::on_board`); a lease that holds a branch or a task claim with them is. The sessions that held a worktree
claim are kept in `worktree-holders.json` (`lease/holders.rs`: at most `max_worktree_records` for one process tree and `MOST_HOLDERS` in all, retained without age-out, a new path past either refused, never one dropped to make room), which the landing reviews read to leave
a branch's creator out of its reviewers. The holder of an expired device-only claim is still told by a notice.

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
- **Where the device key is kept** (`device.rs`, `poolhouse.node_key`). Its custody is `keystore`, `passphrase` or `file`, and
  `pool_status` says it in `device_key` (`{custody, weak}`; `weak` here means a copy of the files, or of a passphrase, is a copy of the
  device, and is not the strong and weak person keys of docs/pools-and-serving.md, 1.3). In the `keystore` the key is wrapped
  under the computer's keystore (`poolhouse.keystore`, purpose `device-key`, owned by the state directory's path) as
  `device.key.wrapped`; the supervisor unwraps it and gives it to the node on its standard input at every start
  (`--key-stdin --key-custody keystore`), and the node writes no key anywhere, so a copy of the state directory, or a backup,
  carries no identity and starts no node. Under a `passphrase` (`POOLHOUSE_DEVICE_KEY_PASSPHRASE`, for a computer with no keystore)
  it is `device.key.enc`, the same way, and the supervisor does not pass that variable on to the node. A `file` is `device.key`
  (0600), which the node makes when nothing else holds a key. The state directory records the custody and the fingerprint the key
  must have in `device.custody.json`, and `device.key` then holds a note that is not a key, so a node from before this change stops on
  it ("device key unreadable") instead of making another identity. A node with a record and no key refuses to start; a key that is
  not the recorded one, or not the certificate's when there is no record, is refused; a record that is gone or damaged is refused
  and never read as "new device", and no key is made for a directory that holds a device's files. Only a person starts over, by
  moving the directory aside. A new device is born into the keystore when the keystore's master key exists, or when a person is at
  the screen to be asked its one Keychain question, so nothing else is asked of anyone; otherwise it starts with a weak file.
  `poolhouse node key` says where the key is kept, and `poolhouse node key --migrate` (a person at a terminal) moves a key out of
  its file, leaves the note, and restarts the node; a backup made before the move still holds the old key file. A keystore that will
  not answer (locked, not yet up) does not end the supervisor: it says why once a minute in `node.log` and in the error
  `ensure_node` raises, and starts the node when the keystore answers. One that was refused (the person declined the prompt, or
  the system said no) is asked again only after the keystore's own cooldown of ten minutes, and the supervisor leaves after the
  third refusal, so nobody is asked until they give in; a person starts the node again. The node leaves no core file when it holds a given key, and
  neither does the supervisor. What it does not protect: the key is in the node's memory while it runs, and a process of the same user
  that can ask the keystore as the supervisor does signs as the device. The supervisor gives the key to the binary the pointer
  (`node-binary.json`) or the selected runtime names, as it starts any node, and those are the user's files: a process of the
  same user that can write them can have the supervisor hand the key to a program of its own, so the per-binary Keychain access
  list is not a barrier to such a process. Ed25519 cannot be held non-exportable by the macOS Keychain or Windows CNG, and a Secure Enclave
  key needs a paid developer account (docs/pool-encryption.md, 8.1).
- **Transport** (`tls.rs`, rustls with the ring provider). TLS 1.3 only; a TLS 1.2 client is refused. Both
  sides show a certificate. A client pins the one fingerprint it expects (a changed digit is a refused
  handshake) or, when joining, takes any certificate and lets the exchange authenticate it. A server
  completes the handshake for an unknown certificate (it may join or join, nothing else) and refuses
  one the pool has revoked. No certificate, no connection.
- **Membership** (`membership.rs`). Per device: fingerprint, certificate, status `active`, `revoked` or `left`, how it came in. A row
  whose fingerprint is not the hash of its certificate is dropped; a removed (`revoked`) certificate cannot come back, and a device
  that `left` comes back only by an admit. Every request is checked against the record when it arrives, so a device removed after
  it connected is refused at its next request on the same connection. A device that records a removal also stops using the
  connections it holds to the removed device: a sync cycle does not dial a member removed since the cycle began, and a dial already
  open ends at its next request, so nothing is sent to, taken from or merged from the removed device once the record says so
  (`netsync::locked_for`); it closes the connections it keeps open to the removed device and the ones the removed device opened to
  it (`Net::drop_removed`, run after a removal is made here, after a `members` request is served and at the end of each sync cycle),
  so the device shows as not connected. A member that has not heard of the removal yet still syncs with the removed device and
  passes on what it takes; the entries the removed device wrote in that window are refused by every device that holds the removal,
  whoever relays them ("Who governs a pool", the log cut of a removal). Changes are written to the board `pool` as audit entries;
  the record is the authority. A device is **unfounded** (a pool of one that has no charter, no id and no admins, kept in memory) or in a
  **governed** pool (below): nothing a member sends in an ordinary exchange adds a device, removes one or moves the join policy.
- **Join policy**: one pool attribute, `open` or `secure` (default `secure`). It is part of the charter and never
  changes in place (there is no method that moves it; a reissue is the way).
- **Peer ops** (`peer.rs`, frames up to 4 MiB): `hello`, `join_open`, `join_exchange`, `join_confirm` for
  a non-member; `members` (the builds members report; no row and no policy in it is read), `acts` (the signed acts of a governed
  pool, "Who governs a pool"), `boards`, `vector`, `pull`, `push` for a member. `shard_caps`, `shard_put`, `shard_start`, `shard_status`, `shard_cancel` (members only)
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
  record then arrives sealed under the exchange (in a governed pool, the founding act and the acts that stand). `open`: the device
  is admitted on its first contact from the same segment and the admit is recorded with the certificate fingerprint (`by: open`).
  A device joins only if it is alone in a pool that no admin governs (`Pool::can_join_other`); a governed pool is left by its own
  `leave` act and never merged into another.

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
a firewall, WSL2 NAT). `python -m poolhouse.node_join join` starts it that way after showing what
it does and getting a yes (`--yes` for a script), and `scripts/pool-join-check` walks every step on one device and prints
the fix for the first that fails.

## Who governs a pool

`gov/` (design: `docs/pools-and-serving.md`, section 1). A pool is its **charter** and the **acts** that change it. People govern a
pool, through admin keys; a device never does, and the one thing a member device does alone is leave.

- **The charter** (`gov/charter.rs`): the pool's name, join policy (`open` or `secure`, fixed for the pool's life), first device,
  admin keys, thresholds, holds; version 3 (a charter of another version is not read). The first device is the founding computer's
  certificate (lower-case hex of an Ed25519 device certificate) and name (at most 40 characters, the computer's own word, shown quoted
  beside the certificate's fingerprint), part of the anchor, so the keys that accept the charter
  accept that computer as the pool's first member and no admit follows; the roster folds it as an admit with `how: found`, ahead of
  every act. A node founds only a charter that names its own certificate (`pool_found`: "That charter puts another computer in the
  pool first"), so a charter copied to another computer makes no pool and no member there. The pool id is the first 16 bytes of the anchor, 32 hex
  digits: the SHA-256 of the epoch-1 charter with its keys' signatures left out, so a copy of a pool with other admins is another
  pool and one set of terms is one pool however its keys sign it (an ECDSA signature differs each time it is made). An act's `prev`
  names the charter's hash, which also leaves out the signatures of the founding keys. `charter.json` holds the founding act, which
  is made of the charter alone (`Charter::found_act`); the epoch rises with each step of the chain below.
- **Admin keys** (`gov/keys.rs`): an Ed25519 public key (64 hex digits, signatures of 64 bytes) or a phone's ECDSA P-256 public
  point (130 hex digits, signatures in strict DER with no padding and `s` in the lower half of the group, the one form a pool
  takes), each listed with its `alg` and its own acceptance (its signature over the pool, the charter it follows, its algorithm,
  role, strength and person), so a key is listed only by its holder and an acceptance holds for the one charter it names. An
  algorithm this build does not know, a key whose length does not match its `alg` and a signature of the wrong form for its key are
  refused; the verifier picks the algorithm from the key. `strong` keys (a presence the signing process
  cannot supply) are the only ones that count toward a threshold, a removal or a cancel; a `weak` key may end a delegate and
  nothing else. The `recovery` role counts toward no immediate act and leaves the admin set only with its own signature.
  Thresholds count distinct people (`unit: person`) or keys: `routine` (remove, cancel, end a delegate) 1 by default, `sensitive`
  (admin changes, thresholds, holds, a delegate) 1 while one person is listed and 2 once two are.
- **Acts** (`gov/act.rs`, `gov/body.rs`): `{v, pool, epoch, kind, body, at_ms, not_after, nonce, prev, signatures}` (version 2) signed over
  `poolhouse/pool-act/v2`, a line break and the canonical JSON without the signatures; the id of an act is the hash of those bytes,
  so co-signers sign the same bytes and the signature sets of two copies merge. `prev` is the hash of the charter the signer knew.
  `not_after` is signed: a pool takes a new act, or a new signature on one, from a person's signer (`pool_submit`, `pool_cosign`, the
  signer path) only until `not_after` (five minutes of clock skew allowed), an hour after `at_ms` for an act one person signs and a
  week for one that needs several; an act whose window is empty or over a week is refused whatever it is signed with. What a
  member relays is taken as it comes, so a removal reaches a device that was away for longer than the window; an act held already is
  `known`, never refused for its age. A charter is founded (`pool_create`, `pool_found`) only within an hour of its `at_ms`, which
  every key accepted, and never when it is dated ahead.
  Kinds: `found`, `admit`, `remove`, `leave`, `admin_add`, `admin_remove`, `veto`, `cancel`, `set_threshold`, `set_holds`,
  `set_mode`, `delegate`, `delegate_end`. A body field nobody defined, an act over 16 KiB, more than eight signatures, one signer
  named twice, a kind this build does not know, an act for another pool, one dated more than five minutes ahead and one whose
  signature does not verify are refused, and each refusal is recorded with its reason (`pools/<id>/refused.json`, the last 256).
  An admin act for a charter the pool has left behind, and an act signed only by admins that are no longer listed (unless the cut
  of the removal that took them out kept it), are refused and held nowhere.
- **Time** (`gov/clock.rs`): every hold, window and expiry is measured in the awake time of this device (its monotonic clock, saved
  at checkpoints, not running while it sleeps) from the first moment it had the act with every signature the act needs. The wall
  clock and the signer's `at_ms` are never read for a delay.
- **The chain** (`gov/chain.rs`): `admin_add`, `admin_remove`, `set_threshold`, `set_holds` and `set_mode` that follow one charter
  form one step and take the pool to the next epoch after the longest hold among them (24 hours to add or remove an admin; a
  weakening, 24 hours; a strengthening, none) with no valid `cancel` seen first. Removals of one step all apply, unless together they
  would leave fewer strong admins than the sensitive threshold needs: then none applies and a dispute opens; one removal that alone
  would is void. An add or a threshold change counts only if its signers survive the step's removals. Two that cannot both hold are
  settled by the number of distinct strong signers, then by the lower signer fingerprints; a tie that remains opens a dispute and
  applies neither. A `veto` by the key a removal names ends the removal and opens a dispute. A dispute (seven days) pauses the chain.
  A key that is removed loses every signature of its that is not in the cut its removal carried. An act that is complete on this
  device only after the first act of its step has run its hold is too late to join or contest the step.
- **The roster** (`gov/roster.rs`): built from scratch from the acts and the chain, never from a row a member sent. `admit` by a
  strong admin; by a live delegate (a device an admin act lets sign `admit` for 30 days, at most 3 an hour and 10 a day by the order
  this device first saw the admits); in an open pool by any member's own device key with `via: segment`, at most 5 an hour. A device
  signature is taken only on the device's own `leave` (and alone) and on an `admit` from such a device; when an act is relayed with
  a signature its signer may not give, that signature is refused, and the roster credits an admit to the first of its signers that
  qualifies, so a relay voids nothing by adding its own. The order of a device's admits and leaves, and of `delegate` and
  `delegate_end`, is the order this device first saw them, not the time a signer wrote on them. A device that does not hold its key in a keystore (`Node::key_binding`; no keystore yet) signs no delegate admit, so a copy of its state
  directory signs none, and every delegate admit is written to the pool board as `delegate_admit`. A device that joins is handed the
  founding act, the admin-set acts in effect (their holds were over before it came), the live delegates' acts and the acts that
  stand (the first three must fit 256 KiB or the join is refused whole, before the device is admitted); it takes the admin-set acts and the rest as settled, so the rates of a delegate do not refuse admits that were made before
  it came. A device that has left, or was removed, signs no admit. `remove` is sticky; `leave` is the
  device's own signature and a later `admit` undoes it.
- **Keeping it** (`gov/pool.rs`, `gov/room.rs`): `pools/<id>/` holds `charter.json`, `acts.json`, `seen.json` and `refused.json`, each
  written only when what it holds changed (the awake total at least once a minute). Caps: 4,096 admits that stand, 256 admin-set
  acts (the oldest that never took effect make room), 8,192 acts. What a member can send in bulk is the device-signed admit that
  stands nowhere (past a rate, or by a device that may not admit): 32 of one device's and 1,024 in all are held, a device's own
  oldest go first, and they go before anything else when the pool is full; a device holds at most eight of its own leaves, and signs
  one only while it is a member. A push is folded when an act that follows needs what an earlier one changed (the admin set, a
  device that was just admitted), a tick when a hold, a delegate or a dispute ends. An act a joiner took as settled can still be
  cancelled until its hold has run from the join.
- **Removing a device and its logs** (`gov/logs.rs`, `Board::cut`): a `remove` carries, for each log of boards that the removed device
  wrote and the removing admin's device holds (`{board, origin, seq, hash}`, at most 256), how many entries of it stand and the
  hash of the last; the node writes the list itself, whatever the caller sent. A device that learns of the removal cuts those logs
  there (a copy that does not pass through the hash is dropped and fetched again up to it) and takes no later entry of them
  from any relay (an entry carries its own signature, and a relay proves nothing about it); a log of the removed device that no
  removal names stands as far as the device holds it; no entry under the key of a removed device is taken from a log no removal
  names. Several removals of one device stand at the least they name. What the removed device admitted goes with it, and an admin
  can admit it again.
- **The `acts` op** (`gov/wire.rs`): `{pool, have, page, push}`. `have` is up to 4,096 tokens (an act's id and a tag of who signed it,
  so a signature added later is passed on); a reply is a page of at most 256 acts and 2 MiB, the acts that change the admins first,
  and what was pushed is checked by the receiver from the keys it holds, whoever carried it. A request reads at most 64 pages.
- **Signing** (`gov/signer.rs`, `gov/ops.rs`): the node holds no person key. An act that needs a person's signature is put to a
  `Signer` (the phone approval, when it exists, and a rig's in-memory key for tests); the typed-yes person key path signs outside the
  node: `pool_draft` makes the act and the bytes to sign, `pool_found` and `pool_submit` take the signed act back. A no from the
  person is respected for ten minutes for that kind of act, and a person is asked at most twelve times in ten minutes. The sentence
  a person signs an act under (`gov/describe.rs`, at most 300 characters) names the key with its person, strength and fingerprint, the
  device with its certificate, and the numbers of a threshold or a hold, never only a label the proposer wrote. A charter is accepted
  under one sentence, "Make the pool 'home' and put the computer 'name' (certificate ...) in it.", so founding is one signature per
  key and no second prompt. `pool_found` is
  granted by the name of the pool the act founds and refuses a charter with no strong admin key, which nobody could admit a device
  under, and, once the grant is held (a session without it is told only that the grant is missing), a charter that calls a key strong that no phone enrolled on this device (the strength is this node's to derive, from the `phone`
  mark of the key in `persons.json`, and is never read from a caller).
  `POOLHOUSE_STEP_LEDGER` names a file that every step asked of a person is written to, one JSON line each.
- **The unfounded pool of one.** A fresh device (no `pools/<id>/`) is its own only member: `pool_status` says `governance.mode:
  unfounded` with an empty `pool`, nothing is written, it needs no signer to exist and it admits nobody (`add_device` answers "This
  device is not in a pool yet; make one, or join one."). It joins a pool with a code or an open pool it hears, or founds one
  (`pool_create`, or `pool_draft_found` and `pool_found` with a key outside the node). A device never founds a pool by itself.
- **A pool of another charter version is set aside the same way.** A `pools/<id>/` whose founding act or charter is not of the version
  this build reads is moved (never deleted) to `pools-old-format/<id>` and the device starts unfounded with the same one line; a
  charter of another version that is offered to found a pool is refused with "this pool was made by another version of Poolhouse and
  cannot be used: make a new pool".
- **An old-format pool is set aside, not refused.** A state directory with a `pool.json` makes the node rename it (never delete it) to
  `pool.json.old-format` (`.1`, `.2` ... when that name exists), print one line, "this device was in an old-format pool: it now starts
  unfounded; create a new pool", and start as the unfounded pool of one, whatever the file held (it is never read). Every board it
  holds is served as before; only the pool features (join, sync with members) wait until a new pool is founded. `pool_status` says
  `governance.old_format_file`. There is no legacy mode, no migration and no shim.

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
- **Which processes a stop signals.** `stop_node` reads the pids from `node-run.json` and signals one only when
  `node_supervise.owned` accepts it: an ordinary pid that is not the caller, one of its parents or init; a command line
  that is `<program> run --state DIR` (node) or `-m poolhouse.node_launch supervise --state DIR` (supervisor) for that
  state directory (links resolved); a start time that fits `started_at`. A process psutil cannot read is not accepted.
  The signal goes to that one process through its psutil handle, never to a group.
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
`session.find` are what the hooks call; `Session.reserve` takes several claims in a fixed order or none. `tasks.Tasks` is the
board's tasks as one session uses them (`create`, `assign`, `claim`, `checkpoint`, `submit`, `review`, `block`, `recover`, `resume`,
`scope`, `record`, `complete`). `board.names` says what a session name may look like. Every workspace command runs on the node
(`workspace/cli.py` lists them; `board_cli.py`, `claims_cli.py`, `notes_cli.py`, `mail_cli.py`, `rooms_cli.py`, `scratch_cli.py`,
`filecli.py`, `status_cli.py`, `landing_cli.py`, `task_cli.py`, `grants_cli.py`, `authority_cli.py`, `localcli.py`), and so do the local
agent loop (`localloop.py`, `localinbox.py`, `localtools.py`, `localstart.py`: a local worker is a registered session whose inbox, sends,
claims and tasks are the node's, started as a subagent of the session that starts it) and the task worker (`task_scheduler.py` assigns
a queued task to a worker that holds its model's `model_slot` lease, `task_worker.py` and `task_runtime.py` claim and run it, `task_worktrees.py`
reserves and takes its checkout as `worktree` and `branch` claims, `task_integration.py` lands it and `task_worktree_recovery.py` and
`task_source_recovery.py` recover a stopped worker's checkout and source). The checkout of a task, the branch and the baseline are the
task's `scope`; what git does stays in `integration_git.py`. Text from other
sessions is fenced as untrusted data on the way out and a credential in outgoing text is refused on the way in
(`screen`, still Python). Tests use `tests/node_kit.py`: a real node on a short `/tmp` root.

**Founding a pool from Python.** `board/founding.py` is the client of `pool_draft_found`, `pool_found`, `pool_draft` and `pool_submit`:
`found(session, admin, name, policy)` drafts the charter (which names this device as the pool's first member), has the person's key sign its acceptance and submits it,
after the grant `pool_create` came from the phone's card (`with_card` asks the card for any missing grant and sends the call again after the
tap) or from the first key. It answers "already in the pool X" for a device that is founded, and refuses a key the node does not derive as
strong before the person is asked (the node's draft of the charter says which keys it derives as strong). `ph.pool.found`, `ph.pool.status` (`governance`, `name`, `missing`) and `ph.pool.join` (a code, or an open pool's fingerprint)
wrap it, and `poolhouse-workspace pool found` is the agent's command; docs/pools-and-serving.md, 1.17, says what is built and what remains.

## The person's session (the UI)

The UI daemon acts for the person through one session of its own per board, `poolhouse.board.person.session(root)`: a
parentless session registered under the harness `poolhouse-ui` and the model `person`, with one native id for each terminal or
program that asks (`person.native_id()`), since a token spends a grant only from the lineage that registered it ("Identity"). It holds no grant of its own: when the
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
- The device-account reviewer rule of the old task store (a reviewer on the worker's account may not review it): the node has no
  device account for a session, and its independent-review rules stand as they are.
- Observations of the execution profile a harness reported about itself (`execution_profile`, `profilehook`): a session says
  its model and harness at registration and the node records what it was told.
- The `board_host` authority of a project and the invitation to a board hosted on another device: every device keeps the board.
- A screen of the text a call writes to the node: the node keeps the 200,000-row cap of a log and the quotas of "Limits", and the
  commands screen text for credentials and private terms before they write.

Tests that need a socket path short enough for macOS use `kit::short_dir()`; `kit::kill_hard` is `kill -9` or `taskkill /F`.

## What is left

- A note written on another device whose `supersedes` names a test-verified note of this device hides it from the default listing:
  `fold_notes` (`src/notes.rs`) does not compare trust, which the old store's replica refused.
- Windows has been compiled (`cargo check --target x86_64-pc-windows-gnu --all-targets`, with a stand-in C compiler for
  `ring`'s build script) but never run by the author; the first real run is the `windows` job in `ci.yml` and the
  Windows step of `release-build.yml` (see "Windows" above). A pipe server owned by another user is refused by the Rust
  client; the Python `hello` check opens the pipe at identification level and sends nothing secret.
- Starting the node on a default port and the multicast group (`NetConfig::standard`) and a default sync
  interval; today the supervisor starts it with `run --state` only (extra flags after `--`), so the network is off.
- The always-on form: a `fleet/autostart` role for `node_launch supervise` (a person-installed unit); a wheel
  that carries a prebuilt node instead of a Rust toolchain at `runtime ensure` time.
- Python lease clients for `testslots` permits, fleet `JobRunner` slots, `lock.only_one` and `claims.py` (the serve broker and the
  request gate take their leases from the table: `poolhouse.leasetable`, docs/serving.md), a local
  holder yielding when the board shows a peer's earlier acquire of a pool-wide claim, leases on a remote
  device, the rest of the entry kinds (`reputation_event`), content screening of foreign text (still in Python; the node only keeps the quarantine records, see "Replies, threads and held text"),
  a person's approval of a new project (a typed yes at project_add, for a git project and for a place that is not one), a per-use
  user-presence prompt for the person key, a lineage on each request that a token carries as proof of who holds it rather than only
  checked at the spend of a grant, the lineage of a registrant on Windows (`GetNamedPipeClientProcessId`), a screen that asks the person the
  signing question itself so the UI daemon can sign, rebuilding the graph index from entries.
- Pools do not merge: a device that is in a governed pool, or that has members, refuses to join another pool; a device in no pool may join one. A device in several pools, reissue (the way to change a policy), the recovery key's time lock and succession, and a signer
  that asks a person's phone are the later slices of `docs/pools-and-serving.md`.
- The head signature still uses an empty pool id as its domain (`Node.pool`); the pool id lives in
  `pool.json` and the beacon. Moving it into the head would need a re-sign when a device joins.
- Addresses of members come from the beacon, from joining (the caller's address and announced port) and
  `peers.json`; a member that moves between joins is found again by its beacon only.
