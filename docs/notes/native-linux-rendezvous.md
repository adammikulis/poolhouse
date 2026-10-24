# Linux/WSL rendezvous observation

Observed 2026-10-24 00:04 UTC on Linux/WSL. This is public coordination data,
not endpoint trust, membership, installation approval or accepted H/K/P/I proof.

| Observation | Value |
|---|---|
| Local workspace `whoami` result | `chatgpt-ce2684`, board `poolhouse-9caf85`, harness `codex` |
| Running node's local API | Read-only `hello` and `pool_status` answered; client autostart and person callbacks disabled |
| Installed build reported by hello | `11c92eb6de3801d33425504d91bbb3771d8f319d`, sequence `6781`, version `0.3.0-dev.1`, wire `1` |
| Legacy raw Ed25519 public-key SHA-256 | `5874e2c678e883148e11ade07341990b26ba9e2d751a6b8a32aa81d1bbd6cce4` |
| Distinct certificate DER SHA-256 | `5a9131d7bc6e73c3fd674da7e59109559285a6c349d1c51cce8454f9d310779f` |
| Proposed domain-separated DeviceId | `64bff7041153e5b6771a3ab5506d435466c7e5a44f26cc1684fb427e148a44bb` |
| Kernel socket observation | PID `17181`, TCP `0.0.0.0:7447`, UDP `0.0.0.0:7448` |
| Observed process credentials | Real/effective/saved/filesystem UID and GID all `1000` |
| Local IPv4 interface observations | `eth0`: `192.168.2.59/24`; `eth4`: `192.168.2.25/24` |
| Pool state reported | Secure, unfounded, one active self member, no connected other member |

The public certificate was parsed as Ed25519 without reading or exporting a
private key. Its DER digest matches hello's `device`; its raw public-key hash
matches hello's `fingerprint`. The proposed DeviceId was calculated from the
reviewed domain plus NUL plus raw 32-byte public key. It is a proposal-derived
observation, not an identifier this installed build supplies or authenticates.
These local consistency checks do not establish protected service principal,
remote key possession, native emitting actor or endpoint trust. The old build
and owner-UID process are not accepted native installation evidence.

The interface addresses are candidate observations, not proof of reachability or
native Windows endpoints. No fresh Mac endpoint/key observation was available in
this checkpoint, so no new remote hello or network probe was performed. The old
Mac endpoint snapshot is not reused as current configuration. Authenticated
cross-device workspace messaging remains unproven; Git coordination works.
Native Windows actor, node identity and endpoint observations must come from
that actual installation/session; they were not inferred from WSL.

The reviewed native caller declaration checkpoint is `f27924b38`, with source
adapter blob `a6c8c6b1919cb5b0db04f28cc4689b799bcbf8dd` and generated Rust trait
blob `b80709dddf9dcdd9f2b6063ae76c0b0a4a288660`. Opaque runtime handles, immutable
spawn plans and generation/binding requirements are declarations; no platform
adapter, protected constructor or installed admission was accepted.

The agent/job declaration checkpoint is `64f53f237` (integration `c43c7e719`),
schema SHA-256 `85edabd2d8785da6dd55700a1f5c58be18b75d44082505198120758d63b3f1ed`.
Focused Python/Rust checks and strict TypeScript checking passed; CI for the
integration passed. K still needs the remaining retained operations, state,
governed approval, worker and platform feasibility contracts and complete
preservation evidence. Platform adapter implementation remains pending K.
