# Compute service bindings: independent attack-first review

Reviewer `chatgpt-874ed9` reviewed the Rust-authoritative HTTPS binding design before implementation at `36056b48e`. This records the accepted narrow design and required probes; passing source checks do not establish installed-device completion.

Rust alone authorizes pool membership and subordinate compute certificates. A binding is a canonical, domain-separated node signature over pool, canonical device, compute service certificate, endpoint, generation and certificate validity. An exact person-approved resource grant authorizes the static certificate and endpoint capability once on the named device. It is a service capability, not installed executable attestation, membership, admin or producer authority.

The actual local socket caller's PID/start and a fresh node epoch determine the generation. The current exact capability renews through a one-use P-256 service-key proof over the full live binding, static capability hash, actual caller, node epoch and fresh challenge. A changed pool, certificate or endpoint needs a new protected approval. The issuing process must still be alive. Receiver authorization checks current canonical membership and freshly asks the originating node for the expected certificate and generation. Cached binding bytes and signatures are never bearer credentials. Python proves service private-key possession through mutual TLS and retains independent owner, source, producer, admission and replay checks.

The latest node-only capability audit in the canonical signed own pool-board chain is the current approval. Its original sequence decides order; imported, session-authored or older markers do not. Rotation carries exactly that current marker as a mandatory durable operation. A standalone signed capability file is not an authority. Complete rollback of copied device keys and signed state remains the existing custody boundary. Pending proof memory is bounded by the existing local frame capacity and supported local identity count; challenge lifetime uses the existing signed-time skew.

| Attack | Required outcome |
|---|---|
| Copied pool/device | Existing key custody remains the boundary; a service binding creates no new membership and cannot name another canonical device. |
| Replay | A restarted daemon/node changes the live generation and request-key domain; renewal needs fresh key possession, and a replaced or reset binding fails the fresh source check. An older capability cannot replace the latest canonical marker. Existing nonce protection remains in RAM, not a durable exactly-once claim. |
| Forged or weak signature | Verify the current member's canonical node key and complete signed tuple; a service certificate never signs a pool act. |
| Malicious member | Its own compute credential conveys no owner, admin, source or producer authority. Unknown or altered binding fields are refused. |
| Stolen phone/key | Existing membership removal governs trust; already-open connections and continuations require current authorization. No stronger custody claim is made. |
| Malicious agent | Socket access or a generic wildcard grant cannot register a chosen certificate. The exact resource, explicit device and single use must be person-approved. |
| Injected agent | Request text, labels, PID assertions and headers never choose the signer or trusted member. The operating system supplies the actual local caller. |
| Downgrade | Missing Rust authorization refuses remote work; Python creates no replacement pool, shared authority key or roster. |
| Approval fatigue | The registration path returns the exact missing grant; it does not silently approve or retry declined prompts. |
| Merely present takeover | LAN presence and public binding bytes provide neither membership nor proof of the service private key. |

Implementation review also probes no-network authorization fallback, wrong service/pool/signer, altered endpoint, conflicting same-generation records, stale signatures, unsupported certificate algorithms, incorrect certificate validity, a removed member on an open TLS connection, and node loss. Python integration must exercise current-request and stream checks, pairwise key derivation, owner-route refusal, and actual installed two-device use.
