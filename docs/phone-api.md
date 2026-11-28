# The phone API

The contract between the Poolhouse computer and the phone app (`app/android`). A linked phone directs the computer: it sees the
pool's devices, follows and stops work, starts and stops models, talks to a live Claude Code session and to local delegates,
and, only when switched on, lends its own model to the pool. The computer side is `src/poolhouse/fleet/phone/` and
`src/poolhouse/phone_mailbox.py`. This page replaces the approval protocol of [phone-approval.md](phone-approval.md): that node
listener (port 7449) has no caller and the app does not use it.

Version: `api` 1. A field this page does not name is ignored; a client ignores fields it does not know.

## 1. Transport

- HTTPS on port **7450** of the computer, TLS 1.3, one self-signed P-256 certificate. The phone trusts exactly that
  certificate: it compares the SHA-256 of the certificate's DER bytes (the **pin**, 64 lowercase hex digits) and ignores
  names, dates and certificate authorities.
- The computer listens on its loopback, home-network (private and link-local) and Tailscale (`100.64.0.0/10`,
  `fd7a:115c:a1e0::/48`) addresses only, and drops any connection from a public address. It listens only while a link code is
  open or a phone is linked.
- The phone keeps every address it was given and tries them in order (home network first, then Tailscale).
- One request for each connection or HTTP/1.1 keep-alive. Bodies are JSON with `Content-Length`; chunked bodies are refused.
- Every answer is `{"ok": true, "result": ...}` or `{"ok": false, "error": {"code": "...", "message": "one plain sentence"}}`.

| Status | `code` | Meaning |
|---|---|---|
| 400 | `invalid` | the request is not understood |
| 401 | `unlinked` | this phone is not linked (or was removed): go to the linking screen |
| 401 | `signature` | the signature, time or nonce was refused; read `/hello` for the computer's time and sign again |
| 403 | `refused` | a rule refuses it; the message says why |
| 404 | `missing` | no such thing |
| 409 | `conflict` | the state does not allow it now |
| 413 | `too_large` | over a size limit |
| 429 | `slow_down` | over a rate limit |
| 501 | `unavailable` | this computer cannot do it |

## 2. Linking

On the computer the person opens **Phones** in the app (or runs `poolhouse phone link`) and gets a QR code and a typed code.
The code is single use and lasts 10 minutes.

**QR content** (an Android intent link, UTF-8, query values percent-encoded, `at` repeated):

```
poolhouse://link?v=1&pin=<64 hex>&code=<10 characters>&at=192.168.1.4%3A7450&at=100.101.2.3%3A7450&name=Studio&expires=1790000000
```

**Typed entry:** the person types an address (`192.168.1.4` or `host:port`; 7450 when no port) and the code, shown as
`ABCDE-FGHJK`. Normalise the code: uppercase, drop everything but letters and digits. The alphabet is
`23456789ABCDEFGHJKMNPQRSTVWXYZ`. For typed entry the phone has no pin yet: it connects, accepts the certificate it sees,
computes the pin from it, and keeps that pin only after checking the computer's `proof` below.

**The phone's key:** ECDSA P-256 in Android Keystore, not exportable, no user authentication needed per signature (the app may
lock itself behind a biometric). `key` is base64 (standard, padded) of the public key's X.509 SubjectPublicKeyInfo DER
(`PublicKey.getEncoded()`). Signatures are `SHA256withECDSA`, DER, base64.

**The code key:** `K = PBKDF2-HMAC-SHA256(password = normalised code, salt = "poolhouse/phone-link/v1|" + pin, iterations =
200000, 32 bytes)`.

`POST /phone/v1/link` (no signature headers):

```json
{"name": "Pixel", "key": "<base64 SPKI>", "proof": "<base64 HMAC-SHA256(K, M)>", "signature": "<base64 ECDSA over M>"}
```

`M` is UTF-8 of `"poolhouse/phone-link/v1\n" + pin + "\n" + key + "\n" + name`. `name` is 1 to 48 characters, no control
characters.

Result:

```json
{"phone": "ph-0123456789ab", "computer": "Studio", "words": "7f3a 91c2 d04e 5b18 a6c3 7e20",
 "proof": "<base64 HMAC-SHA256(K, 'poolhouse/phone-linked/v1\n' + pin + '\n' + key + '\n' + phone)>"}
```

The phone **must** check `proof` (it shows the answering computer knows the code, so nobody sits between them) before it keeps
the pin and the phone id. `words` are the first 24 hex digits of SHA-256 of the key's DER, in groups of four; the app and the
computer's Phones screen both show them.

A wrong code is `403 refused`. Ten failures from one address in ten minutes get `429`.

`GET /phone/v1/hello` (no signature): `{"service": "poolhouse-phone", "api": 1, "computer": "Studio", "now_ms": 1790000000000,
"linking": true}`.

## 3. Signing every request

Every other request carries:

| Header | Value |
|---|---|
| `Poolhouse-Phone` | the phone id |
| `Poolhouse-Time` | the computer's time in milliseconds (phone clock plus the offset learned from `/hello`) |
| `Poolhouse-Nonce` | 16 to 48 characters of `A-Za-z0-9_-`, never repeated |
| `Poolhouse-Signature` | base64 ECDSA over the string below |

```
poolhouse/phone-request/v1\n<pin>\n<phone id>\n<METHOD>\n<path with query, exactly as sent>\n<time>\n<nonce>\n<hex SHA-256 of the body>
```

Joined with single `\n`, no trailing newline; the body hash of an empty body is the SHA-256 of nothing. The computer refuses a
time more than 120 seconds off or from before its listener started, and a nonce it has seen. There is no token and no cookie:
without the key nothing is read or done. Removing the phone on the computer takes effect at the next request and closes its
streams.

## 4. Endpoints

All under `/phone/v1`. Times are Unix seconds (numbers) unless a name ends in `_ms`.

### Phone and pool

| Request | Result |
|---|---|
| `GET /me` | `{"phone", "name", "words", "computer", "linked_at", "pool_use": false}` |
| `DELETE /me` | unlinks this phone; `{"removed": true}` |
| `GET /devices` | `{"pool": {"name", "governed", "policy"} or null, "devices": [Device], "note": ""}` |
| `POST /pool/pause` `{"for": "2h" or "", "reason": ""}` | pauses work on every reachable computer: `{"reached", "total", "machines": [...]}` |
| `POST /pool/resume` `{}` | the same shape |

`Device`:

```json
{"id": "<64 hex or machine id, or 'phone:ph-...'>", "name": "Studio", "kind": "computer", "this_computer": true,
 "online": true, "busy": false, "paused": false, "paused_because": "", "slots": 1, "free": 1, "queued": 0,
 "room": "48 GB", "gpu": "Apple M4 Max", "serving": ["model-name:8080"], "models": ["name"], "version": "0.3.0",
 "address": "", "last_seen": 1790000000.0}
```

A linked phone appears with `"kind": "phone"`, `"pool_use": true|false` and `"models"` holding its offered model while it is
offering one. A pool member that does not answer is listed with `"online": false`.

### Models on this computer

| Request | Result |
|---|---|
| `GET /models` | `{"can_serve": true, "installed": [{"path", "name", "size_bytes"}], "running": [{"port", "models": [..], ...}]}` |
| `POST /models/start` `{"path": "<from installed>", "context": 32768}` | the running row (201). `context` is optional |
| `POST /models/stop` `{"port": 8080}` | `{"running": [...]}` |

### Jobs

| Request | Result |
|---|---|
| `GET /jobs` | `{"jobs": [{"id", "name", "state", "submitted_at", "started_at", "finished_at", ...}]}` (the daemon's own job rows) |
| `GET /jobs/{id}` | one row |
| `GET /jobs/{id}/log?tail=200` | `{"log": "text"}` (at most 64 KiB, the newest lines) |
| `POST /jobs/{id}/stop` `{}` | the row |

### Scheduled jobs

| Request | Result |
|---|---|
| `GET /scheduled` | `{"available": false, "jobs": []}` until scheduled jobs are on the computer; then `{"available": true, "jobs": [{"id", "name", "when", "next_at", "last_state", "enabled"}]}` |
| `POST /scheduled/{id}/cancel` `{}` | `{"cancelled": true}`; `501` while unavailable |

### Delegates (local agents)

| Request | Result |
|---|---|
| `GET /delegates` | `{"runs": [Run]}`, newest first: the computer's own delegate rows (`name`, `state`, `doing`, `task`, `model`, `lead`, `started`, `seconds`, `edit`, `answer`) |
| `GET /delegates/{name}?after=0` | `{"run": Run, "events": [...], "next": 12, "more": false}`; call again with `after=next` to follow |
| `POST /delegates/{name}/stop` `{}` | `{"stopped": true, "said": "..."}` |

### Sessions: every agent is a conversation (the app's first screen)

Every agent the computer can see is a session, listed and talked to like any conversation: Claude Code sessions, their
subagents (running and finished), local delegates, and the other agents on the workspace board, on this computer or another.
Discovery is automatic; nobody registers one. `parent` gives the tree. The model on the phone itself is the app's own entry:
the computer does not list it.

| Request | Result |
|---|---|
| `GET /sessions` | `{"sessions": [Session], "note": ""}`, the most recently active first; ended sessions older than 7 days are left out |
| `GET /sessions/{id}` | `{"session": Session}` |
| `GET /sessions/{id}/events?after=<cursor>` | `{"events": [Event], "next": "<cursor>", "more": false}`. Without `after`: the newest 200 events. With the last `next`: only what is new |
| `POST /sessions/{id}/messages` `{"text": "..."}` | `{"message": "m-...", "state": "queued", "mode": "wake", "note": "..."}`; `409` when `mode` is `none` |
| `POST /sessions/{id}/questions/{question}/answer` `{"answers": {"<question text>": "<option label or your own words>"}}` | `{"answered": true}` |
| `POST /sessions/{id}/stop` `{}` | stops a delegate: `{"stopped": true, "said": "..."}`; `409` for a session that has no stop (a Claude session is stopped by asking it) |

`Session`:

```json
{"id": "claude-<uuid>", "kind": "claude", "title": "get a great android app going", "agent": "claude-db43eb",
 "model": "claude-opus-5-5", "project": "poolhouse", "device": "Studio", "this_computer": true, "parent": null,
 "state": "working", "mode": "wake", "note": "", "active_at": 1790000000.0, "last_text": "…the newest reply…",
 "waiting": 0}
```

| `kind` | What | `id` |
|---|---|---|
| `claude` | a Claude Code session | `claude-<session uuid>` |
| `subagent` | a subagent a Claude session spawned; `parent` is that session | `subagent-<session uuid>-<agent id>` |
| `delegate` | a local-model delegate | `delegate-<name>` |
| `agent` | any other agent the board knows (Codex, OpenCode, Pi, a session on another computer) | `agent-<board name>` |

`state` is `working`, `idle`, `asking`, or `ended`. `mode` says what sending does, and `note` says it in one sentence to
show above the send box:

| `mode` | Meaning |
|---|---|
| `wake` | delivered now; an idle session starts working |
| `next_step` | delivered at the agent's next tool call or the end of its turn |
| `via_parent` | the agent has finished or cannot be reached directly: its lead session is given the message to pass on (or resume it) |
| `next_turn` | waits until someone or something gives the session its next turn |
| `none` | cannot receive a message here; `note` says why (hide the send box) |

A message waits at most 24 hours, then is dropped with a `dropped` event. `text` is 1 to 8000 characters.

`Event`: `{"id": "t:81233:0", "at": 1790000000.0, "type": "...", ...}`. `id` is unique within the session (use it to
de-duplicate); events arrive oldest first.

| `type` | Extra fields | Meaning |
|---|---|---|
| `prompt` | `text`, `from` (`computer`, `phone`, `lead`) | what the agent was told |
| `message` | `message`, `text` | what a phone sent that is not delivered yet |
| `delivered` | `message` | the session received it |
| `dropped` | `message`, `text` (why) | it will not be delivered |
| `reply` | `text` | what the agent said |
| `progress` | `text` | one line on a tool the agent ran (and `ok: false` when it failed) |
| `question` | `question`, `questions: [{"question", "header", "multiSelect", "options": [{"label","description"}]}]`, `expires_at` | the agent asks; answer before `expires_at` or it is asked at the computer |
| `answered` | `question`, `by` (`phone` or `computer`) | the question is closed |
| `notice` | `text`, `kind` (`permission`, `ended`, `delegate`) | something to know, such as a permission prompt waiting at the computer |

Permission prompts of Claude Code are **not** answered from the phone: a `notice` with `kind: "permission"` says one waits at
the computer.

### Inbox

| Request | Result |
|---|---|
| `GET /inbox` | `{"questions": [{"session", "question", "questions", "expires_at", "asked_at"}], "notices": [Event with "session"]}`: what waits for the person, and the 50 newest notices |

### Stream

`GET /stream` (signed like any request) answers `text/event-stream`, from now on:

```
event: session
data: {"session":"claude-…","event":{"id":"t:81233:0","at":1790000000.0,"type":"reply","text":"Done."}}

event: changed
data: {"what":["sessions"]}

: ping
```

`session` carries one new event of any session; `changed` says which lists to read again (`devices`, `models`, `jobs`,
`delegates`, `sessions`, `inbox`). There is no replay: after (re)connecting, read each open session's events from its last
`next`. A `: ping` comes at least every 15 seconds; reconnect after 40 silent seconds. The computer closes a stream after 10
minutes (reconnect and sign again) and at once when the phone is removed. At most 3 streams for each phone.

## 5. Using the phone in the pool (off by default)

One switch for each phone, **off** when linked, stored on the computer, changeable from the phone and from the computer's
Phones screen. While it is off the computer sends the phone no request, shows no model for it and refuses its model calls.
Directing, sessions and chat do not depend on it.

| Request | Result |
|---|---|
| `GET /pool-use` | `{"enabled": false, "offering": false, "model": null, "queued": 0}` |
| `POST /pool-use` `{"enabled": true}` | the same shape. Turning it off cancels every request in hand at once |

While it is on and the app can answer, the app keeps one long poll open. Polling is what makes the model available; a phone
that has not polled for 45 seconds is offline. The runtime (LiteRT-LM or llama.cpp) is the app's business: the computer sends
chat messages and takes text back.

`POST /model/next`:

```json
{"model": {"id": "gemma-3n-e4b", "name": "Gemma 3n", "runtime": "litert-lm", "context": 4096, "max_output": 1024, "tools": false},
 "wait_s": 25}
```

`runtime` is `litert-lm` or `llama.cpp`; `context` 256 to 1048576; `max_output` 1 to 8192; `wait_s` 0 to 25; `id` and `name`
1 to 80 printable characters. Result `{"request": null, "enabled": true}` when there is nothing, `enabled: false` (answered at
once) when pool use is off: stop polling. Otherwise:

```json
{"request": {"id": "r-0123456789abcdef", "model": "gemma-3n-e4b",
             "messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
             "max_tokens": 512, "temperature": 0.7, "tools": [], "deadline": 1790000600.0},
 "enabled": true}
```

`content` is a string (text only in api 1). `tools` (OpenAI function tools) is non-empty only for a model that declared
`tools: true`.

`POST /model/requests/{id}` sends the answer in order, many times:

```json
{"seq": 0, "delta": "Hello", "done": false}
{"seq": 1, "delta": "", "done": true, "finish_reason": "stop", "usage": {"prompt_tokens": 12, "completion_tokens": 3},
 "tool_calls": []}
{"seq": 2, "error": "out of memory"}
```

`seq` starts at 0 and rises by one. `finish_reason` is `stop`, `length` or `tool_calls`. `tool_calls` (in the last post only)
are OpenAI-shaped: `{"id", "type": "function", "function": {"name", "arguments": "<json text>"}}`. The result is
`{"continue": true}`; `continue: false` means stop generating (the asker left, or pool use was turned off). Send a delta at
least every 60 seconds (batch tokens: no more than about 10 posts a second).

Limits: 2 requests waiting or running for each phone, 30 requests a minute, 256 KiB and 128 messages a request, 16 KiB a
delta, 1 MiB an answer, 90 seconds to the first post, 90 seconds between posts, 10 minutes in all.

**The pool's side** (not for the app): `POST /infer/v1/chat/completions?poolhouse_model=phone:<phone id>` on the daemon, with
the daemon's own member authentication, takes an OpenAI chat request and answers it (streamed as `data:` chunks when
`"stream": true`).

## 6. What reaches which agent

The computer connects the projects it knows to phones by itself when a phone is linked (and the Phones screen turns each one
off or adds another; `poolhouse phone connect --project DIR`). Connecting writes hooks into that project's own
`.claude/settings.local.json`, never the user's global settings. [phone-sessions.md](phone-sessions.md) says how delivery
works and what cannot be done.

| Agent | While working | While idle or finished |
|---|---|---|
| Claude Code session in a connected project | `next_step` | `wake`; a session open since before the project was connected is `next_turn` until its next turn; a closed one is `none` |
| Claude Code session elsewhere | `none` (history is shown) | `none` |
| Subagent in a connected project | `next_step` | `via_parent` (through its live lead); `none` when the lead is closed |
| Sandboxed local delegate | `next_step` | `none` once ended |
| Delegate without a sandbox, Codex, OpenCode, Pi | `none`: they have no supported way to take the person's input yet | `none` |
| Any agent on another computer | `none`: link the phone to that computer | `none` |

## 7. Limits

| What | Limit |
|---|---|
| Connections | 48 at once, 12 from one address |
| Request headers | 16 KiB, 15 seconds |
| Body | 64 KiB (`/model/requests`: 128 KiB) |
| Signed requests | 900 a minute for each phone |
| Messages and answers | 30 a minute for each phone |
| Unsigned requests (`hello`, `link`) | 60 a minute from one address |
| Linked phones | 8 |

## 8. Audit

Every link, removal, message, answer, start, stop, pause and pool-use change is one line in the computer's
`<state>/person/phones/audit.jsonl`: the time, the phone, the action and its target. Message texts are not in it.
