# Attack verdict: isolated launcher, code of slices L2 and L3

Reviewer: claude-6ddf2a (non-author attack pass, advice only; it does not block a development push). Tree: `origin/0.3dev` at `cbf20fc42`.
Design read: `docs/design-isolated-launcher.md` (Zero friction, 3.5, 4.4, 6, 7, 8.2, 8.3, 14.3) and `docs/notes/attack-verdict-launcher-no-waiting.md`.
Method: read the code, then ran small harmless probes on this Mac (scripts were in the scratchpad, state under `/private/tmp`, the real Seatbelt via
`sandbox-exec`, plumbing-only sockets standing in for the upstream and for a registry host; no model server, no real home, no keystore, no board).
"Verified" below means a probe showed it; "read" means from the code only.

Nothing calls this code yet (L5 wires it), so every finding is a change to make before or with L5. Ranked by severity.

## HIGH

### H1. A project file (data) chooses a read-and-run grant of any folder that holds `bin/python*`; no gate, no credential scan (verified)

- `toolchain.py:107-118` `_venv_prefix` takes `home =` from the project's own `.venv/pyvenv.cfg`, accepts any absolute directory, and returns its
  parent when it ends in `bin`. The only test is `_holds_program(prefix, "python")` (an executable `bin/python*` file).
- `gate.py:181-182` adds one `declaration` request per declared directory on **every** launch, with no `require_widening` call
  (`decide_launch` only gates `arguments.widening`). `grants.py:151-155` accepts `declaration` as a source when `toolchain.declared` lists the
  subject, which is the same file that asked.
- `grants.py:116-122`: for a toolchain (`program=True`) the credential walk `secretpaths.tree_hit` is skipped; `_beyond` only refuses a
  credential name, the home folder, its ancestors and the state root.
- `profile.py:85-98`: the grant becomes `read` **and** `exec` of the whole subtree, and `<dir>/bin` joins `PATH`.
- Attack: the project (or a patch a confined agent landed earlier, or a cloned repo) holds `.venv/pyvenv.cfg` with `home = /Users/<p>/.local/bin`
  (any user with a `python3.x` in `~/.local/bin` from uv or pipx). Probe: with a fake home, `declared()` returned `<home>/.local` and
  `grants.decide` answered `granted`, with `<home>/.local/share/somekeyring/login.db` inside the grant. The same holds for `/opt/homebrew`
  style prefixes with `etc`/`var`, and any other tool folder a user keeps a `python` shim in. Result: the confined model reads (and runs) the
  tree, and the person was never asked and the sentinel is not told (`named` excludes `declaration`, gate.py:192).
- This contradicts 8.3 "whatever the repository says about itself is never a source of a widening".
- Smallest fix: take the interpreter from a closed list of roots (pyenv versions, uv python, Homebrew `Cellar`, the framework install, the
  interpreter's own `sys.base_prefix`) and require the declared directory to be one of those or a direct child version directory of one; grant
  only `bin` and `lib/pythonX.Y` rather than the prefix; run `tree_hit` for toolchains as well; signal `declaration` grants to the sentinel.
  Test: the probe above as a fail-first.

### H2. The widening gate is decided by environment text and a name-keyed token file, so a same-user process or a subagent passes it (verified)

Three links of one chain; each is an input an agent controls.

1. `gate.py:131` `if caller.marked(env) and not _is_lead()`: an unmarked process skips the lead check, and `authority.require`
   (`authority.py:167-179`) then passes it as a terminal. `caller.marked` (`caller.py:44`) reads `CLAUDECODE`, `POOLHOUSE_AGENT` and four more
   variables. Probe: a child started with a clean environment returned "PASSED the widening gate"; the same child with `CLAUDECODE=1`
   was refused. Any agent that runs `env -i poolhouse ...` is a terminal. This also defeats the `person` state in the `prod` preset, which
   `gate.py:128-130` documents as "a terminal outside any agent".
2. `_is_lead` (`gate.py:103-110`) calls `authority.current_agent`, which is `session_identity._authenticated`
   (`session_identity.py:154-190`). It picks the name from `POOLHOUSE_WORKSPACE_AGENT` when no session selector is set, and reads
   `state/client/<board>/<name>.token` (`session_identity.py:96-104`), a same-user file named by the very variable. A subagent that unsets
   `CLAUDE_CODE_SESSION_ID`, `POOLHOUSE_SESSION_ID`, `POOLHOUSE_SUBAGENT` and sets `POOLHOUSE_WORKSPACE_AGENT=<lead>` authenticates as
   the lead (read; I did not run it against the real board). So "an agent that is not the lead is refused every widening argument" holds only
   for a subagent that does not try. It also accepts any live main agent of any project on the machine, not the one that launched.
3. `home.py:66-78`: `POOLHOUSE_HOME` moves the state root, and with it `authority.json`, the person's project policy
   (`policy.path_of`) and the audit log, so a process that sets it reads an empty policy and a default-delegated gate. `_beyond`
   (`grants.py:100`) protects `home.home()`, which is the moved root, not the account's real `~/.poolhouse`.

- Why it matters: I17 and "a person's decision is never an agent's". It is the floor under every widening.
- Smallest fix: decide "agent or not" from the process tree and the node, never from the environment. The node already authenticates a
  session at registration; have it record the harness process id and answer `whoami` only for a caller whose `LOCAL_PEERPID` ancestry
  contains it (so a subagent's process tree maps to the helper identity, and a descendant of any registered agent is an agent whatever its
  environment says). Take the state root for the policy, the gate and the audit from `home.account_roots()` in gate and policy code, not from
  the environment. Until then, state in the design that the gate is advice against a non-cooperating agent.

### H3. The person's project policy is skipped by a temporary or sibling-worktree project path (read)

- `gate.py:141-145`: an agent may name any folder under the temp directory as `project` without it being registered.
- `policy.py:44-47,57`: the policy is looked up by the **exact real path** of the project, so a copy or a sibling worktree
  (the repository's own rule is sibling worktrees) has no policy, and `grants.py:159-162` says "no policy: restricts nothing beyond the caps".
- Attack: the lead `git worktree add /private/tmp/x` (or copies the repository) and launches with `--project /private/tmp/x --read ...
  --allow-net ...`; the person's allow-list never applies. Fix: resolve the project through the node (`project_resolve`) to the registered
  root and key the policy on that; drop the temp exemption for an agent, or give a temp project the policy of the project it was cut from.

## MEDIUM

### M1. `confined()` no longer refuses a process under an allow-default profile (I17 weakening, verified)
- `confinement.py:54` answers yes only when `default` is also refused. Probe: under `(allow default)(deny file-write* ...)` it answers
  False and `require_widening` passed (probe 5 above); under `(deny default)` it answers True. A nested `sandbox-exec` or `sandbox_init`
  inside the engine's profile fails with "Operation not permitted" (verified), so a **confined** child cannot make itself look unconfined; the
  hole is for every tool a harness runs under its own deny-list profile (a "permissive-open" style seatbelt wrapper): before `d7232183e` those
  were refused as confined, now they are judged by markers alone (H2.1).
- Cause: the test runner's profile. Fix: put the exception where the suite is, not in the production answer: keep
  `sandbox_check(pid, None, 0) != 0` as the answer and make the suite's wrapper a test seam (the suite patches `confined`, or runs the
  caller checks it needs unwrapped), so no production caller learns "allow-default is fine". `attestation.py:42` uses `== 1` without the
  default test, so the two answers now differ for the same profile; make them one function.

### M2. The acting identity of a session bridge must be the confined session's own, and the fetch log is keyed by agent name (read, L5 requirement)
- `tools.as_session(acting)` runs every verb as one session and `Rail.agent` keys `fetchlog.note(agent, ...)` (`harnesshook.py:98`). If L5
  passes the launching lead's own session or name, a forged `pre` frame writes the lead's download log, and every board message, note and claim
  of the confined model carries the lead's name with only a text label to tell them apart. State in 4.4/7 and test it: the acting session is a
  fresh helper identity created for the launch, and its name is not derivable from the launcher's.
- `fetchlog._file` (`fetchlog.py:31`) folds `/` to `_`, so `a/b` and `a_b` share a file; `note` appends without a size bound.

### M3. Model bridge: metadata leak, a two-name deny-list, and a possible server-side fetch (verified pass-through, upstream behaviour to confirm)
- `model_bridge.py:36,155-217` forwards `GET /v1/models` as the server answered it. llama-server's answer names the model file path (home folder and
  user name), parameter count and size. Fix: answer `/v1/models` from the `Lease` (alias, window) in the bridge and never proxy it.
- `model_bridge.py:38,74-75` removes `id_slot` and `lora` only. Probe: `slot_id`, `cache_prompt`, and everything else reached the upstream
  unchanged. Replace the deny-list with an allow-list of the fields the three message routes take, checked against the pinned llama-server's
  request parser, and test against that version.
- Probe: an `image_url` content part with `http://169.254.169.254/...` reached the upstream byte for byte. If the pinned llama-server downloads a
  remote `image_url` (recent multimodal builds do), the parent-run server fetches any URL the model names, outside the profile: SSRF from
  the confined model through the one process that has the network. Check against the pinned build; if it fetches, refuse any
  `image_url`/`input_audio`/file part whose URL is not a `data:` URI, and set the server's remote-fetch flag off.
- Held (verified): duplicate or `Transfer-Encoding` headers, a second request pipelined behind the first, a query string (dropped), `%2e`
  escapes, a trailing slash, an unauthenticated route, Content-Length twice, extra request headers (dropped), and `/props`: all refused or
  stripped.

### M4. A session can make the parent do unbounded work, and no frame is rate-limited (read)
- `session_bridge.py:137-148`: any number of frames per connection, up to `connection_bound()` connections. A `hook post` frame
  calls `harnesshook.post`, which starts a notification subprocess (`harnesshook.py:119-143`) per frame; a `grant` frame writes an
  activity record and tells the sentinel (`:185-193`); a `pre` frame appends to the fetch log. A confined loop can fork parent-side
  processes, fill the activity log and the state disk (G11 says disk is only watched), and flood the sentinel.
- Fix: one in-flight `post` per session (coalesce, answer the cached result), a per-session frame budget per minute derived from the
  window (the bridge already knows it), log the first N `grant` frames then one counter, and cap the fetch log.
- Also `FAILURES` (`:43`) lacks `ArithmeticError`: `ttl_s: Infinity` (the frame parser accepts `NaN`/`Infinity`, unlike the model bridge's
  `parse_constant`) reaches `int(inf)` in `claims_cli.claim` and kills the connection thread with a traceback. Reject non-finite numbers in
  `session_verbs._typed` and cap `ttl_s` at the configured `claim_ttl_s`; I did not run a claim against the real board.

### M5. A parent exception text goes back to the confined session (read)
- `session_bridge.py:216-218` returns `f"{type(error).__name__}: {error}"` for any `OSError`, `ValueError`, ... from a workspace tool. A
  `FileNotFoundError` carries the parent's path (state root, token file, node socket). That is home-folder and user-name disclosure (G13's
  neighbour) and sometimes fragments of data. Fix: a fixed sentence per verb plus the exception class; the detail goes to the log.

### M6. Hidden friction against the zero-friction order (verified)
- Egress bound is the measured bytes of **one** small install (`contracts/egress-hosts.json`: pypi.org 4571, crates 1215) and cumulative per
  session (`egress_proxy.py:173-179`). Probe with the bound as shipped: three tunnels of 2000 bytes were cut at 4571 and the fourth CONNECT got 403,
  with no guidance to the model. A second `pip install`, a retry, or any project with more than a few dependencies fails mid-install. Each
  TLS connection with a post-quantum ClientHello alone is about 1.5 KB. Fix: bound = a per-session cap derived from what the install
  of the project's own lockfile needs (or a multiple of the measured value with the measurement of a 30-dependency project), reset per
  declared install, and a sentence the client can read. The exfil value of a few KB to a package index is the covert channel the design already
  accepts; the bound should be sized by that, not by one command.
- `model_bridge.py:183` answers 429 at once when the lease's slots are busy. Claude Code runs parallel tool and subagent requests; it
  retries 429 with back-off and shows errors. Wait for a slot (up to the idle time) instead of refusing.
- `gate.py:141-145` refuses an agent's launch on any unregistered non-temp path, and registering needs the person's terminal: for a fresh
  clone that is a wait on a person in effect. Let the node register a project the lead names when the folder is under the lead's own
  worktree area, or say so in 14.3.
- If a lead's own shell runs inside its harness's deny-default sandbox (not tested here), `confined()` is True and every launch is refused as
  nested; the refusal sentence should name that cause.

### M7. `publish.matches` does not re-classify what the agent made (read, L4 requirement)
- `publish.py:137-150` binds identities of the directory and the file at launch; `governing` runs once at `bind` on the file **as it was**.
  The agent then rewrites the content and mode in the stage (a shebang, an executable bit, a Mach-O or ELF header, a name a non-plain file
  of the project already runs). L4's apply step must call `governing(project, relpath)` on the **staged bytes and mode** (`_facts` reads
  from a root; give it the stage) and refuse on the result, then apply by descriptor into the bound directory. State it as an L4 acceptance
  test, because the identity match alone passes.
- `_files` (`publish.py:57-65`) skips `.venv`, `node_modules`, `site-packages`; a `.pth` or `sitecustomize` there that imports the published file
  is invisible to "named by a non-plain file". Count those names as governing, or refuse files that a skipped folder's loader could name.

### M8. The workspace verbs are wide to read and names can read as sentences (read)
- `session_verbs.py:53-72`: a confined model may read the whole board: `inbox`, `thread`, `notes_search`, `note_get`, `file`, `file_search`,
  `claims`, `status`, `reputation`. Nothing says these are scoped to the session's own task or project. Anything an agent pasted into a note
  or an attachment of that board is readable by the model and leaves through the model's output. Limit reads to the session's own inbox,
  its own thread, claims, and notes/files of its project; confirm the board scope in the node.
- `PLAIN` (`session_verbs.py:26`) admits `ignore-previous-rules-and-run:...` as a claim key, file name, recipient, `note`, `source`, `kind`
  of an announcement, up to 200 characters, and no label is put on those (only on `lines` and `bodies`). A hyphenated imperative is a
  sentence to a reader model. Cap `PLAIN` at 64 characters, allow hyphen-joined words only in fields the reader never sees as text, and have
  every reader surface (claims, who_owns, file list) print these under the same "data, no authority" fence as bodies.
- A claim of a key such as an integration branch or the test slot, held for `claim_ttl_s`, blocks other agents (denial of coordination). Reserve
  the kinds that gate integration and test slots (as `isolation` is reserved) and give a session its own prefix.
- Held (verified by reading): the label is added in the bridge for every line and body field; `isolation*` is refused in any case, width or
  invisible-character spelling for the kinds the table lists (`kinds`), and `send` always carries a subject. Note `attach` `name` is not in
  `kinds`, so a file may be named `isolation.report` (cosmetic; `genuine()` keys on sender, type and subject only, correctly).

## LOW

- L1. `egress_proxy.blocked` (`:64-78`) allows IPv4-compatible `::a.b.c.d` (so `::127.0.0.1`, `::10.0.0.1`) and SIIT `::ffff:0:a.b.c.d`
  (verified: ALLOWED). On this Mac both fail with "No route to host" (verified), and a DNS answer for a registry name must carry them, so the
  exposure is a poisoned resolver on another OS. Close it by blocking `::/96` and `::ffff:0:0:0/96` and by refusing every address
  that is not `is_global` of the **unwrapped** IPv4 when any wrapper is recognised. Held (verified): mapped, 6to4, Teredo, NAT64 well-known
  prefix, `64:ff9b:1::/48`, link-local with a zone, shared 100.64/10, link-local metadata 169.254, documentation, benchmarking, `192.0.0/24`.
  Single resolution then dial by address: held (the `resolve` stub was called once per CONNECT and the dialled address was the checked one).
- L2. Egress targets held (verified): trailing dot, userinfo, `host@evil`, `0443`, a space, IDN/punycode, IPv6 and IPv4 literals, port 80 and a
  registry host not granted all refused; case folds to the granted name. Bound accounting is exact under 30 parallel tunnels
  (delivered 4571 = bound, counted 4571).
- L3. The proxy and the model bridge listen on `127.0.0.1` with no credential for the proxy: any local process can spend a session's egress bound
  (cut its installs), tunnel to a granted host, and fire `isolation.probe` events (`egress_proxy.py:135`, `model_bridge.py:169` tell the sentinel
  before authentication). Use the standard `Proxy-Authorization` (already in `wire.ONCE`) with a token the launcher puts in `HTTPS_PROXY`
  (`http://u:token@127.0.0.1:port`; clients send it with no step for a person) and answer unauthenticated requests with one fixed 407 that tells
  the sentinel nothing; rate-limit events.
- L4. Session bridge: `admit=None` is the default (`session_bridge.py:84,129`), which admits every same-user process that knows the path; make it
  required. `admit(pid)` is checked once, at accept, against a pid that can be recycled before the check; use the pid plus start time, and
  the process group of the confinement rather than the parent chain (a double-forked daemon falls out of a parent chain and is refused, which
  is the safe side). The 0700 directory is made then checked by `lstat` (`:101-104`) and the socket is `chmod`ed after bind (`:107`); the
  directory protects the window, and only the same user could race it. Client: `connect` compares the listener's pid to `--pid` (held; an impostor
  needs the same user and write access in a 0700 directory).
- L5. Proof: `lan=False`, or a machine with no LAN address, makes P08 vacuous (`proof.py:184,273-274`: a documentation address nobody listens on
  proves "heard 0"). Only tests pass `lan=False` today; keep the default and have the proof record "P08 not proven" instead of passing.
  A weakened profile that allows `network-outbound` with no `lan` canary would pass P08 on such a machine.
- L6. `Resolved.unchanged` (`toolchain.py:47-57`) re-opens by path, not by descriptor. Seatbelt `subpath` is path based, so a descriptor bind
  would not change what the profile grants; a replaced directory at the same path keeps the grant. A same-user process is outside the model; record
  it as accepted, and re-run `unchanged` as the last step before exec, not after the proof only.
- L7. `serving.Server._accept` returns on any `OSError` (`:96-98`): an `EMFILE` or other persistent accept error ends the service silently
  (a reset storm did not, verified: 2000 resets, still serving). Retry on `EMFILE`/`ECONNABORTED`, tell the sentinel on a stop.
  `select.select` on a descriptor over 1024 raises `ValueError` that no handler catches (`model_bridge.py:241`, `egress_proxy.py:190`);
  use `selectors`. Idle time is per `recv`, not per request, so a drip of one byte every 179 s holds a connection; a total head deadline closes it.
- L8. A response is passed as bytes (`_pass`); a hostile upstream cannot split a response here (checked: status and field filter, `Content-Length` cut,
  chunked only when the whole value is `chunked`). Held.

## Not broken (probes that came back clean)

- Request smuggling, path tricks, header games, body bound, route table and the 401/404 ordering of the model bridge (list in M3).
- The `grant` frame: one fixed answer for every kind and subject; nothing queued, nothing waits.
- The client never reads a terminal or a token, and blocks a `pre` that the bridge did not answer.
- `confined()` in a nested attempt: `sandbox-exec` and `sandbox_init` refuse from inside the engine's profile (verified), so a confined child cannot
  apply an allow-default profile on top of its own.
- Seatbelt string quoting for grant subjects (`seatbelt.quote` escapes quote and backslash, refuses controls), so a hostile folder name cannot add a rule.

## Order I would take

1. H1 and H2 before L5 wires anything (H2 as a design statement plus the node-side ancestry check; H1 is a small code change).
2. H3, M1, M2, M3, M5 with L5.
3. M4, M6, M7 with L5/L4; M8 and the low items as their slices land.
