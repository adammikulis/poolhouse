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

It reuses `ServerSpec`, `LlamaServerBackend` (as `StrictBackend`), `startup`, the health check, the port checks, the preflight and
the memory estimate. Memory is admitted against the machine's server file (`servers.json`) under the lock `ServerManager` takes
for the same file. A core caller therefore sees every server any process recorded there, a pool broker's included, and a pool
broker's own servers are counted. The reverse is not true: the pool admits against the node's lease table, which does not hold the
core's servers, so a pool start can still be admitted beside a core server it does not see, and memory leased in the node for work
that is not a server is invisible to the core. Two core callers cannot overcommit together; a core and a pool can.
The grant (`serve.grant`) is satisfied, not bypassed: `core._start` is the one place besides the broker that opens it.

## Trust: `serve.policy`

A launch asks a `Policy` two things: may this binary run (`held`, `problem`, `pin`, `unpin`) and what environment does the child
get (`scrub_env`). The pool's answer is `llamacpp_trust.PoolPolicy` (the sentinel's sealed pins, quarantine and release), the
default of every pool entry point and unchanged. The core's is `LocalPolicy`: the digest in the build's own `BUILD.json`, and a
plain JSON pin file (`<state>/core/pins.json`) that pins a binary on first use and refuses one that no longer matches.

## Threat note: what `LocalPolicy` weakens, and what still protects

For the attack pass by another agent (AGENTS.md: a security design gets one).

Weakened against the pool:

- No identity. Nothing says which agent started a server, and nothing queues or revokes by caller. Any process of the account can
  import `poolhouse.core` and start a server.
- No quarantine by a person. `LocalPolicy` holds a binary that fails its pin until `unpin`, and the pin file is the account's own, so
  a process that can write it can release the hold. A pin file that cannot be read is never overwritten (a hold in it would be lost):
  a start is refused until it is moved aside.
- The pin file is plain JSON, not sealed. An attacker who can write the state directory can write a pin, or replace the binary
  and its `BUILD.json` together, and every check agrees with the swap. That case is now loud and not silent: a binary that changed
  and is vouched for by its `BUILD.json` is re-pinned with a WARNING naming both digests, and the entry keeps the digest it replaced
  (`previous`); a binary with no `BUILD.json` is pinned on first use with a WARNING. Neither is prevented.
- No sentinel screening of the child's environment. `scrub_env` passes it through; the environment is `child_env`'s, which removes
  names that look like tokens, keys or passwords by pattern, not by an allowlist. A secret in a variable with an innocent name
  reaches the server.
- No provenance in `servers.json`.
- A `Policy` check and an exec cannot be one step. The binary is found once per start, and hashed again in `launch` the
  instant before `Popen` (`Policy.before_launch`); a replacement between that hash and the exec of the same path is still possible
  for an attacker who can write the binary's directory.
- The listener check (`_owns_listener`) confirms the server is a live process of ours and holds the port before the key is sent
  to it; a process that wins the port in the instant after the check is outside what it can see.

Still protecting:

- Every route to the binary (an explicit path, `$LLAMA_CPP_SERVER`, `$LLAMA_CPP_DIR`, a named build, a vendor directory, the cache,
  PATH) is judged by the policy in the core (`find_binary(strict=True)`), and the route is logged. A binary whose `BUILD.json`
  records another digest is refused on all of them; one with none is pinned on first use. The pool's default still judges `current` alone.
- A server starts only inside `grant.broker_grant()` with a `Lease` made there; a program that goes round `core` gets `NoGrant`.
- Memory admission under a cross-process file lock, a recorded port never reused while live, and a refusal to take a port a process
  that is not ours holds.
- The API key is generated per server, handed over in a file of mode 0600 (`--api-key-file`; the command line shows no key), stored
  with `serverkeys` under a lock, and removed when the handle closes. The server listens on loopback and ends with this process.
- `resolve` accepts an existing path or a file name found in the model places, only for `.gguf` files; `hf:` references are refused.
- The sandbox the backend wraps a launch in (`serve.confined`) is untouched.
