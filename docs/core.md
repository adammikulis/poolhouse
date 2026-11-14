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

It reuses `ServerSpec`, `LlamaServerBackend`, `startup`, the health check, the port checks, the preflight and the memory
estimate. Memory is admitted against the machine's server file (`servers.json`) under the lock `ServerManager` takes for the
same file, so a pool broker and any number of core callers see each other's records and cannot overcommit together. The grant
(`serve.grant`) is satisfied, not bypassed: `core._start` is the one place besides the broker that opens it, and the structural
tests and the `server-starts` gate name it.

## Trust: `serve.policy`

A launch asks a `Policy` two things: may this binary run (`held`, `problem`, `pin`, `unpin`) and what environment does the child
get (`scrub_env`). The pool's answer is `llamacpp_trust.PoolPolicy` (the sentinel's sealed pins, quarantine and release), the
default of every pool entry point and unchanged. The core's is `LocalPolicy`: the digest in the build's own `BUILD.json`, and a
plain JSON pin file (`<state>/core/pins.json`) that pins a binary on first use and refuses one that no longer matches.

## Threat note: what `LocalPolicy` weakens, and what still protects

For the attack pass by another agent (AGENTS.md: a security design gets one).

Weakened against the pool:

- No identity. Nothing says which agent started a server, and nothing queues, rations or revokes by caller. Any process of the
  account can import `poolhouse.core` and start a server; the pool's broker, by contrast, answers to a session's token.
- No quarantine by a person. The sentinel's hold needs a person's release. `LocalPolicy` holds a binary that fails its pin until
  `unpin`, and that file is the account's own, so a process that can write it can release the hold.
- The pin file is plain JSON, not sealed. A local attacker who can write the state directory can rewrite a pin, or the
  `BUILD.json` beside the binary and the binary together, and the check agrees with the swap. The pool's sealed store makes that
  an edit the node notices.
- A rebuilt `current` whose `BUILD.json` vouches for its own new bytes is re-pinned without a person. That is what lets
  `poolhouse-serve llama-cpp ensure` update a build; it is also a way for the same writer to replace a binary.
- No sentinel screening of the child's environment: `scrub_env` passes it through. The environment is still built by
  `child_env` (no tokens or keys of this process; `LLAMA_ARG_` download settings dropped), so what is not removed is only what the
  sentinel had quarantined.
- No provenance: a record in `servers.json` carries no claim of who asked.
- `LlamaServerBackend` consults `$LLAMA_CPP_SERVER`, `$LLAMA_CPP_DIR`, an explicit path and PATH before the managed build, as it
  always has, and those paths are not judged by any policy. The core does not take an `explicit` path; the environment variables
  still apply.

Still protecting:

- The binary must hash to the digest its own `BUILD.json` records, on every start, pinned or not.
- A server starts only inside `grant.broker_grant()` with a `Lease` made there; a program that goes round `core` gets `NoGrant`.
- Memory admission under a cross-process file lock, a recorded port that is never reused while live, and a refusal to take a port
  a process that is not ours holds.
- The server keeps its generated API key (`serverkeys`, mode 0600), listens on loopback and ends with the process that started it.
- The model file is read, not downloaded: `hf:` references are not accepted by `resolve`.
- The sandbox the backend wraps a launch in (`serve.confined`) is untouched.

An attack worth trying: replace the binary and its `BUILD.json` together between two starts; write `core/pins.json` with the new
digest; start two processes at once at the memory limit; symlink the model path; leave a pending record from a dead process.
