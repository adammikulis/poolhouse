# The core: one local model, none of the pool

`poolhouse.core` serves a model on this machine for a program that only wants to talk to it (pcb-engine is the first such
program). It starts a llama-server the same way the broker does, minus everything that is the pool's: no session, token, board,
workspace, approval or app is imported or asked for, and no node, broker or sentinel has to be running.

```python
from poolhouse import core

path = core.resolve("Qwen3.8-27B-UD-Q4_K_XL.gguf")   # a name or a path to an installed GGUF; ModelNotFound otherwise
core.fit(path, 32768).said()                         # the preflight's report: would it load, and if not why
with core.serve(path, 32768, reasoning_budget=0) as server:   # admitted, started, healthy
    server.base_url, server.model_id, server.port, server.api_key
core.stop_all()                                      # every server this process started
```

`serve(model, context=0, *, port=None, policy=None, preflight=True, timeout=None, **spec)` takes any other `ServerSpec` field in
`spec`. A context of 0 asks for the largest the machine holds (`poolhouse.serve.fitted`, loaded only then, and heavier than the
rest of the core). The server stops when its handle is closed, when its `with` block ends and when this process ends. No warm-up
request is sent: it would queue on the pool's node.

## What it reuses, and what it leaves

The core and the pool's broker start a server the same way: both go through `LlamaServerBackend` (the binary found once per
start and judged by the policy whichever route found it, the key handed over in a 0600 file, the listener checked to be the
process just started before any key is sent) and both stop one through `serve.stopping.stop_server`. What the broker keeps for itself is
the queue, the leases and the node's table.

## One memory ledger

`servers.json` is the ledger (`poolhouse.serve.ledger`). A start looks at its live records and writes its own `pending`
record in one step under `servers.start.lock`: the core does that and nothing else; the broker does it before it asks the node,
so a pool start waiting in the node's queue is already a record the core counts. Both therefore see each other's servers
whichever asked first, and a core and a pool asking at the same moment cannot both take the memory
(`tests/test_serve_shared_ledger.py`).

What stays outside the ledger: memory the node leases to work that is not a server (training, a measurement) is counted by the
pool's admission and not by the core's, because the core talks to no node. The node's budget and the ledger's both come from
`admission.budget_mb()`, so the two never disagree about the total. A server that was not started by either (an adopted
`unmanaged` one) is in the ledger only when the broker adopted it. Records of the core and of a `ServerManager` in one process
are tagged by holder and no longer overwrite each other.

## Trust: `serve.policy` and `serve.binary_trust`

A launch asks a `Policy` two things: which digest is recorded for this binary (`watching`, `pinned`, `pin`) and what environment the
child gets (`scrub_env`). The pool's answer is `llamacpp_trust.PoolPolicy` (the sentinel's sealed pins), the default of every pool
entry point. The core's is `LocalPolicy`: a plain JSON pin file (`<state>/core/pins.json`) that pins a binary on first use. What is
done with a binary that no longer matches its pin is the same for both and is `binary_trust.vet`; nothing holds a binary and no
person is asked:

- A changed binary something vouches for is re-pinned and used, with a `RE-PINNED` warning naming both digests: the digest in the
  `BUILD.json` Poolhouse's own build wrote beside it, a macOS code signature that verifies and chains to Apple
  (`codesign --verify --strict -R="=anchor apple generic"`; an ad-hoc signature, which any compiler run makes, vouches for nothing), or a
  Homebrew Cellar file (real path under a Cellar, owned by this user, writable by no one else).
- A changed binary nothing vouches for, including one whose `BUILD.json` records another digest, is not run. The newest earlier
  mainline build kept under `llama.cpp/builds/` that still hashes to its own record replaces it (`FELL BACK`). With none, a good
  build is made the way `poolhouse-serve build` makes one (from source when a compiler is installed, else the newest release, and
  the other if that fails), switched to as `current` and pinned (`RECOVERED`). Only when that fails too does `BinaryTampered` say
  so, naming every attempt.
- A pin file that cannot be read is moved aside as `pins.json.unreadable-<UTC timestamp>` and the pins start again
  (`PIN FILE REBUILT`).
- A binary with no pin and no `BUILD.json` is pinned on first use (`TRUST ON FIRST USE`), as before.

The binary is vetted again the instant before the exec; a start in which it changed meanwhile fails with a message naming the
replacement, and the next start uses it.

## Records from a newer build

Every reader of a pin (the sentinel's `Manifest`, `LocalPolicy`'s pin file), a `BUILD.json`, a hold or `limits.json` goes through
`poolhouse.tolerant.known_only`: a field this build does not know is dropped and named once per process in a warning (the name
cut to 64 characters, at most 256 names kept). Unknown fields are never interpreted: they grant nothing, take no part in a pin
comparison or a trust decision (those use the digest and the other known fields), and are not written back, so a rewrite by this
build removes them. What is still refused: a required field that is missing, a known field of the wrong type (a `TypeError`), a
pin or build manifest that is not an object. Duplicate JSON keys are last-wins; an unknown key never shadows a known one because
only known names are copied.

If a newer writer adds a field that changes what a pin *means* (a revocation, a stricter digest rule), this build cannot know it
and ignores it. That is why such a field must never be the only carrier of a restriction. What this build still enforces without
it: a binary that no longer hashes to its pin is judged by `binary_trust.vet`. Nothing is held and no flag keeps a binary from running
(a `held` written by another build is an unknown field), so a newer restriction is safe against an older reader only as a digest
that no longer matches. A schema bump is not used to fail closed: `LocalPolicy` reads a newer `schema_version` on purpose
(`tests/test_serve_policy.py`), because refusing it would stop `poolhouse-serve up` on every older snapshot, which is the failure
this section exists to prevent.

## Threat note: what `LocalPolicy` weakens, and what still protects

For the attack pass by another agent (AGENTS.md: a security design gets one).

Weakened against the pool:

- No identity. Nothing says which agent started a server, and nothing queues or revokes by caller. Any process of the account can
  import `poolhouse.core` and start a server.
- No quarantine by a person, and no hold at all: a binary that fails its pin is replaced or re-pinned automatically
  (`binary_trust.vet`), and the pin file is the account's own, so a process that can write it can write a pin. A pin file that cannot
  be read is moved aside under a timestamped name and the pins start again, so a corrupt file never stops a start; what it held is
  kept in the moved file and named in the log.
- A change that something vouches for is used without asking: whoever can write a binary's directory can write its `BUILD.json`, and a
  Developer ID or Apple signature or a Homebrew Cellar location is as strong as the account's own files. A change nothing vouches
  for is never run (an earlier build or a fetched one stands in), but the stand-in is chosen by what the managed directory holds.
- The pin file is plain JSON, not sealed. An attacker who can write the state directory can write a pin, or replace the binary
  and its `BUILD.json` together, and every check agrees with the swap. That case is now loud and not silent: a binary that changed
  and is vouched for by its `BUILD.json` is re-pinned with a WARNING naming both digests, and the entry keeps the digest it replaced
  (`previous`); a binary with no `BUILD.json` is pinned on first use with a WARNING. Neither is prevented.
- No sentinel screening of the child's environment. `scrub_env` passes it through; the environment is `child_env`'s, which removes
  names that look like tokens, keys or passwords by pattern, not by an allowlist. A secret in a variable with an innocent name
  reaches the server.
- No provenance in `servers.json`.
- A `Policy` check and an exec cannot be one step. The binary is found once per start, and hashed again in `launch` the
  instant before `Popen` (`launch(binary=...)`, which fails the start and leaves the replacement to the next one); a replacement
  between that hash and the exec of the same path is still possible for an attacker who can write the binary's directory.
- The listener check (`process.listens_on`, in `launch`) confirms the server is a live process of ours and holds the port before the key is sent
  to it; a process that wins the port in the instant after the check is outside what it can see.

Still protecting:

- Every route to the binary (an explicit path, `$LLAMA_CPP_SERVER`, `$LLAMA_CPP_DIR`, a named build, a vendor directory, the cache,
  PATH) is vetted by the policy in the core (`find_binary(strict=True)`), and the route is logged. A binary whose `BUILD.json`
  records another digest is not run on any of them (an earlier build or a fetched one replaces it); one with none is pinned on first
  use. The pool's default still vets `current` alone.
- A server starts only inside `grant.broker_grant()` with a `Lease` made there; a program that goes round `core` gets `NoGrant`.
- Memory admission under a cross-process file lock, a recorded port never reused while live, and a refusal to take a port a process
  that is not ours holds.
- The API key is generated per server, handed over in a file of mode 0600 (`--api-key-file`; the command line shows no key), stored
  with `serverkeys` under a lock, and removed when the handle closes. The server listens on loopback and ends with this process.
- `resolve` accepts an existing path or a file name found in the model places, only for `.gguf` files; `hf:` references are refused.
- The sandbox the backend wraps a launch in (`serve.confined`) is untouched.

## Attack pass on the broker-on-core change: fixed, accepted, open

Fixed: a client of the machine's broker can no longer name a binary, a vendor directory or a disabled sandbox (`named_backend`);
a key is sent only once it is bound to the server's process, so no health probe of a start carries it before the listener is
checked; a key file is written by unlinking first and opening with `O_EXCL | O_NOFOLLOW`; `forget` keeps the key of a live server;
`ServerSpec.api_key` is out of its repr.

Accepted (same-user attacker, no boundary crossed): the broker's token and `servers.json` are the account's; a record there with a
hostile `est_mb` or pid can make the ledger refuse or over-admit. `listens_on` proves our process listens on the port, not that
nobody shares it (`SO_REUSEPORT` by a same-user stranger). Both routes vet the binary again at the launch. `BUILD.json` beside a
binary vouches for it, and whoever can write the binary's directory can write both.

Open, for follow-up (not done here): sandbox read set includes the whole `server-keys.d` directory (one directory per port);
the wire still takes `slot_save_path`, `chat_template_file`, `lookup_*` and `Starting` flags (`anyway`, `preflight`) from a client;
a sandbox choice the client made by environment is not sent to the broker; the broker daemon inherits `LLAMA_CPP_*`/`PATH` from the
client that spawned it; the pool picks its port outside the ledger lock, so a core and a pool can pick one port (the loser fails at
`claim_port`, nothing is double-admitted); `Admitting._save` copies `_mine` without the manager's lock; `entry_mb` and the GGUF
estimate do not clamp hostile numbers; `issue` runs before `claim_port` and so replaces the key entry of a live server on a busy port.
