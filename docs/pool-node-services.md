# Pool compute service bindings

The Rust node owns pool identity, membership, join policy, device identity and authorization. Python provides HTTPS compute services using a subordinate P-256 service certificate. A service certificate supplies no membership by itself.

## Binding authority

The node issues a domain-separated signed binding of its actual pool id, member fingerprint, service certificate fingerprint and DER, fixed compute-service name, endpoint, process generation and validity. A person approves the exact certificate, endpoint and node-observed process generation as a subordinate compute capability. The local node derives the actual socket caller PID and start identity and refuses generic or wildcard service grants. This approval supplies no software-attestation claim or pool administration right.

A remote node verifies a binding against the current trusted member key and current pool, never a signer supplied by the binding. Bindings propagate through existing authenticated node member exchange. A replacement daemon, node restart, removal or leave invalidates the previous generation immediately, regardless of its nominal expiry.

Python asks its local node for current service authorization on each request and each streaming continuation. Node absence, an unknown method, a stale binding, an untrusted certificate or a removed member refuses the request. Python stores no membership roster or enrollment authority and never infers membership from a certificate, network presence or cached binding.

## HTTPS and request integrity

The existing compute routes, body limits, file chunks, resume/digest checks, model lease admission, sandbox, source permits and producer execution authority remain enforced. Browser/owner authentication is independent of service membership. A service binding never authorizes setup, runtime administration, key export, membership changes or local owner UI actions.

Two distinct current P-256 service identities derive a pairwise ECDH request secret. Derivation is domain-separated and binds the actual Rust pool id and both service certificate fingerprints and binding generations in a canonical order. Both endpoints validate their own and the other endpoint's current node-issued binding before deriving or using the secret. No shared pool key or node private key reaches Python. Invalid or non-P-256 service certificates are refused.

Existing request MACs, the long-lived authenticator's RAM nonce cache and sealed request/reply bodies use this pairwise secret. Restarting the daemon changes the node-observed process generation and therefore the pairwise key. An authenticated member cannot use its secret to impersonate another member. Rebinding changes the secret. Existing operation idempotency, ownership and resource checks remain independent and continue to apply. No retained compatibility mode accepts old shared-pool keys.

## Caller coverage

`fleet.remote.Member` discovers endpoints and pins certificates from node-authorized binding facts. Its health, availability, model, job, speech, upload and download methods retain their existing compute transport. `pull()` and remote chat revalidate current bindings between streamed chunks. `fleet.sweeps` and `fleet.applying` retain their existing bounded operation schemas.

`fleet.files`, `fleet.models`, `fleet.producer_devices`, `studio_transport` and `serve.decision_authority` use the current Rust pool id and bound service identity. Their source, offer, permit and execution guards remain independent of membership. A node binding cannot create a software producer offer or decision permit.

Test work uses existing Rust `shard_call` and `testfarm.client.Shards`. Python automatic pool convergence, PAKE joining, invitation membership, roster merge routes and shared pool keys are removed after callers route pool actions to the Rust node. No state migration or dual authority is retained.

## Verification

Independent attack review covers cloned devices/pools, replay, forged or insufficient signatures, malicious members, stolen keys, malicious or injected agents, secure-to-open downgrade, approval fatigue and presence-only takeover. Focused tests cover forged/wrong-pool/stale bindings, removal between requests and chunks, node/daemon loss, pairwise-secret separation, nonce replay, upload/download integrity and preserved producer permits. Installed verification joins two physical devices, performs shared test work, exercises compute service transfer and confirms removal refuses subsequent requests.
