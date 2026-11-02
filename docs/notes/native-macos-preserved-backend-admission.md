# M31 preserved backend admission and load behavior

This supplement maps only the eight declared M31 Python paths in [W-MAC custody](native-macos-preserved-work.md#scoped-current-byte-preservation). It authorizes no recovery, deletion, source merge, model execution or native acceptance. M17 has a separate mapping owner/file.

## Exact comparison boundaries

- Recorded M31 HEAD: `a215dd7f0f8061c9f4a1be76a76e39008783a9cc`.
- Current comparison: local `0.3dev` at `fd7a4db803e5a5c695f5529252942fa0d0afab3b`; later changes require a new comparison.
- W-MAC manifest SHA-256: `8e5af876292cff77c94ea28b70bb70dfe79829d65af1605700171334692cc697`.
- All eight declared captured objects were read from external archive bytes and checked against their manifest SHA-256. All seven scoped index blobs equal their paths at recorded HEAD; `loadguard.py` was untracked/absent there. The captured working bytes differ from those index bytes. The ledger has no original dirty-byte hashes: capture integrity does not establish original inventory-time identity.
- The table's working-copy Git blob IDs were recomputed from archived bytes without writing objects. They identify content, not a claim that those objects are committed. Current IDs come from `git show`/`rev-parse` at the pinned commit, not imports of source.

| Declared path | Archived scoped index / recorded HEAD blob | Archived captured working-byte blob | Current pinned blob |
| --- | --- | --- | --- |
| `src/poolhouse/bench/serve.py` | `56153ddf0c9c709b026bfdfc0d6296b0fb17ddcf` | `ec044371e818988e4f28e9b36482f2e996581aa5` | `37dce6a318e852a6984b2258809996c7b5f773b1` |
| `src/poolhouse/client/health.py` | `ec47e7c498b7f8d3ae96404a0ff540946c4af60b` | `14c0d18ffe22d37d9cf67bd535ba09a197c0dd57` | `1dbb91da460960274bbe0c0ae4ccbda66debbad2` |
| `src/poolhouse/serve/admitting.py` | `471812511b502974b515395607c5668584399acd` | `e03346d36ed27c16329d1ea16a0e928f442fe996` | `419a1718eb7d32300d75bdb186f211155b0bba11` |
| `src/poolhouse/serve/backend.py` | `672bafe79a27840e42391e97526fdd7f0db6b018` | `46cca35b104170eab8816d65eef65b6b5b81d9dc` | `672bafe79a27840e42391e97526fdd7f0db6b018` |
| `src/poolhouse/serve/manager.py` | `8a324b260a85c3799039567b5bc0338d9fe86c53` | `ddbc263872212096d90e0a681edb5c3b871fba9c` | `8a324b260a85c3799039567b5bc0338d9fe86c53` |
| `src/poolhouse/serve/mlx_tree.py` | `d7e0a26aaf4a5cd006eb80b56e35367701634529` | `6408eea5f5055ca51e9af92ab7eada7936309671` | `a932ccbfc51549e86d9d100f3526a2175d8c0dd0` |
| `src/poolhouse/serve/python_engines.py` | `5b5085065ba5c8c54b1d6542236c6b06f91cdc77` | `e417f86cf3a4864a982eb05330db3a0500c663d9` | `66d50762d3cc63b40c4d296d1890da4b2d7de79f` |
| `src/poolhouse/serve/loadguard.py` | absent | `38f58a59498fc40f8e86f0e29858be7564d91eb5` | absent |

## Behavior disposition

| Preserved behavior / exact archived functions | Current implementation and static test evidence | Disposition |
| --- | --- | --- |
| Admission before backend spawn: `Admitting._admitted`, `_bound`, `_given_back`; `Lease.__post_init__`, `launch` and `LlamaServerBackend.start` retain broker-grant checks. Queue requests are released in `_admitted`'s `finally` unless handed off. | Same Python functions remain; `serve/admission.ask` and the lease table use the existing node lease RPCs. `tests/test_serve_node_leases.py::test_a_start_that_fails_gives_its_memory_back_at_once` asserts a lease is held during start and absent after failure. | Legacy admission/cleanup structure retained in source. This is not a replacement by a sealed native spawn adapter. |
| No default total load limit: `Loading.serve_timeout`, `ServerManager.lease`, `escalate`, `_launch`, `_beside`, `Admitting._admitted(load_s=None)`, MLX and Python-engine `start` propagate an optional deadline. | Current manager still resolves `None` through `scaled_timeout(weight_of(...))`; `_admitted` expects a numeric `load_s`. MLX/Python-engine callsites forward their supplied timeout but retain finite defaults; forwarding alone does not reproduce the archived end-to-end policy. | Unique unreviewed WIP, not retained end-to-end. No adoption or timeout-budget change proposed here. |
| Bounded health probes plus progress-sensitive waits: archived `client.health.wait_for_health(timeout=None, probe_s, stall, progress)` beats/checks `Watch`, caps each probe to `probe_s` and the remaining total deadline, and tests child liveness. | Current `wait_for_health` has a numeric total deadline and calls `is_healthy` with its default probe timeout; it has no progress/stall parameters. | Missing current equivalent. No read test establishes advancing-load continuation, silent-load termination or per-probe remaining-deadline behavior. |
| Learned stall limit: `loadguard.slowest`, `record`, `watch`, `progress_of` use the largest recorded duration, log size, CPU time and RSS; no prior duration means no stall guard. | No `serve/loadguard.py` exists at the pinned current commit. The archived eight-path group references `poolhouse.stall.Watch/run` but does not include that dependency. | Unreviewed WIP; dependency completeness and first-load safety are unproven. No universal load deadline or native policy can be inferred from this snapshot. |
| Loading-memory retention: `Lease.on_progress`, `backend.launch.advancing`, `ServerManager._pending` and `_kept_alive` renew the exact node memory lease after observed advancement, throttled by `KEEP_S / 3`. | Current `Lease` has no `on_progress`, and manager has no `_kept_alive`. Existing `_bound` binds a completed server PID. `test_keeping_the_leases_renews_the_servers_and_the_claims` covers broker/training-claim renewal, not this loading-progress callback. | Missing current equivalent; existing renewal tests do not prove the archived callback or a native replacement. |
| Load failure/interruption and release: archived `backend.launch` ends an unhealthy child and releases its exit guard in `finally`; manager `release`, `_stop_server`, `stop_all` return leases and end owned children. | Current `launch` retains a health-wait `finally` and `end_child`; manager retains release/stop paths. `test_a_server_that_is_released_gives_its_lease_back` checks lease removal; `tests/test_serve_exit_guard.py` names host-exit, PID-reuse and fork cases. | Legacy teardown structure retained, not native lifecycle acceptance. Archived guard/progress initialization occurs after spawning but before the health-wait `try`; this group alone does not prove every partial-initialization failure is reaped. |
| Binary information commands: archived `_binary_info`, `devices_of`, `help_of`, `flags_of`, `values_of` replace fixed subprocess deadlines with measured output-stall watching (`INFO_STALL_S`). | Current backend still uses `subprocess.run(... timeout=...)`; `tests/test_serve_backend.py` contains information-command flag/cache/timeout cases. | Unique unreviewed WIP; no output-stall replacement or fresh measurement is established. |

## Test and native-contract limits

The named tests were read, not executed. Exact current test blobs at `fd7a4db803e5a5c695f5529252942fa0d0afab3b`:

- `tests/test_serve_node_leases.py`: `636a029ceb4f435ad68e037d2772656c75d4e50a`.
- `tests/test_serve_up_is_a_lease.py`: `ed52a3218c2232bc8adf68eaaeee8767dee89e68`; its `test_down_leaves_a_server_another_lease_still_holds` is a retained last-holder-release assertion.
- `tests/test_serve_exit_guard.py`: `76137b048e876a9dc433ff9314a32217a31ca123`; host-exit, retained PID identity and fork assertions.
- `tests/test_serve_backend.py`: `88c80a5b46b70aa451f1731725a5ca807bfaa2d6`; information-command flag/cache/deadline assertions.
- `tests/test_serve_python_engines.py`: `253add1b4123147c1efad860c1a0b16e3694d6d7`; `test_an_engine_server_is_leased_recorded_and_released` covers process/log/lease plumbing with a stand-in health-only engine.

Some existing cases use `FakeBackend` or health-only plumbing processes. Their names and assertions are source evidence only; they do not demonstrate a real model, MLX/vLLM/SGLang load, the new archived stall policy, accelerator behavior or hardware teardown. No such process was started for this mapping.

The retained-feature inventory at `contracts/node/retained-features.json` identifies source baseline `4a46649f67435c6e87e357fe70b31afa8a013b06` and labels every proposed operation as proposal-only. Relevant declarations include `facade.serve.up`, `cli.poolhouse.serve.{up,down,hold,escalate,leases,queue,broker}` and `node.lease.{acquire,renew,release}`. Its `file0275` (`src/poolhouse/leasetable.py`) callers connect existing Python admission to the node; listing a lease RPC is not a model-load lifecycle contract. This inventory does not specify the archived progress callback/stall semantics or accept a replacement.

At the pinned current commit, `app/poolhouse-node/src/contract_lifecycle.rs` blob `b80709dddf9dcdd9f2b6063ae76c0b0a4a288660` declares `NativeCallerPlatform` hold/release/cancel/terminate interfaces. Those declarations supply no M31-specific load implementation or real-model tests. H/K/P acceptance, sealed native process ownership, partial-release teardown and retained-feature operation definitions remain pending in the [native plan](../native-plan-macos.md) and [node contract](../node-contract.md).

Disposition: retain all eight archived working-byte objects and seven index blobs. An implementation owner must separately review the unique deadline/progress/lease-renewal behavior, dependency completeness and failure cleanup before any port or source adoption. This read-only mapping ran no product imports, tests, backend/model startup or network calls and changed no original file or index; the paused model remains paused.
