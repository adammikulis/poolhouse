# Mac certificate preflight: connection remains unverified

Candidate addresses came from the published Linux/WSL rendezvous observation at `2026-10-24T00:04Z`.
A Python observer attempted one bounded TCP connection to each candidate; neither established TCP.

| Destination | Observation UTC | Elapsed | Result |
| --- | --- | --- | --- |
| `192.168.2.59:7447` | `2026-10-24T00:45:42.570547Z` | 5.002 s | Timeout before TCP connection |
| `192.168.2.25:7447` | `2026-10-24T00:45:47.572774Z` | 5.002 s | Timeout before TCP connection |

Private evidence `E-MAC/peer-certificate-preflight-result.json` records zero retries, no DNS/proxy, no TLS exchange,
no certificate observed, zero product bytes, no client certificate loaded, no persistent key/device identity created,
no product request and no pool membership change. The supplied certificate digest remained an unchecked candidate.
This observer is not the resident node-owned mTLS path and establishes no remote authentication or join.

At `2026-10-24T00:47:47.324929Z`, local route and neighbor-cache queries selected `en1` and reported resolved entries
for both candidates. `E-MAC/peer-preflight-local-network-diagnosis.json` records these queries without new network
requests. A cached resolved neighbor and a local route prove neither host/node reachability nor a firewall cause.
The difference between macOS per-app local-network permission and remote WSL ingress/listener behavior remains
undetermined; the observer context must not be treated as proof of the native node's access.

Please reply from the actual counterpart source with fresh node build/process, bound socket, interface/address and
actual actor observations, separately for Windows and independent Linux/WSL. Supply a bounded reciprocal
certificate-only result, or the reviewed node-owned hello result if that path is available, recording the actual source,
destination, time, stage and certificate/key binding. No shutdown, firewall change, key handling or weakening of trust
is authorized by this diagnostic note. The connection remains unfinished and no fix is claimed.

Existing `app/poolhouse-node/src/reach.rs` hints ask for manual WSL configuration, shutdown, terminal setup commands
or firewall/address chores. The native product must own diagnosis and authorized repair or an authorized reverse
channel, while preserving independent admission. This is a product requirement, not an implemented GUI action;
these instructions must not become required user onboarding steps.
