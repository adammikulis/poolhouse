# Mac own-process IPC ABI observations

This is **UNCONFINED_ABI_BASELINE_ONLY**, not sender authentication or confinement evidence.
The explicitly separate baseline applied no sandbox. It preserves the earlier failed strict
observer and inert startup-control attempts; it is neither a fallback implementation nor a
replacement pass for them. No `VerifiedSender`, protected custody, hostile caller admission or
H/K/P acceptance follows.

The Mac ran one reviewed inert C fixture through maintained one-CPU admission: one direct
public-SDK compiler invocation and one own-process run with at most one measured self-exec.
The receiver bounded the actual Mach message and FORMAT_0 audit trailer, ignored forged
body identity fields, decoded all eight words through public libbsm, and corroborated its own
contemporaneous task token. Own `proc_pidinfo` size, PID and birth sec/usec arithmetic were checked.
These are two actual own-process observations, not a foreign-sender or role/lineage test.

The coordinator independently bound the raw records to source, runner and executable digests,
compared the actual two observations without trusting prior argv values, checked field/word
equality and the separate synthetic vector, and verified the strict-failure records unchanged.
Raw PID/UID/GID/ASID values, actual audit words/tokens, birth values, paths and argv are omitted.
The synthetic vector below is public test data and is not one of those kernel tokens.

## Redacted structured evidence

```json
{
  "scope": "UNCONFINED_ABI_BASELINE_ONLY",
  "observed_utc": "2026-10-23T23:18:31.088741+00:00",
  "sandbox_applied": false,
  "complete_inherited_mach_port_isolation": false,
  "acceptance": {
    "VerifiedSender": false,
    "H": false,
    "K": false,
    "P": false,
    "protected_custody": false,
    "foreign_sender_admission": false
  },
  "provenance": {
    "source_sha256": "f05cd674f6b8afd2ab9aabafd08a05ebf4f84a7161c05ea1034ece51773a4cff",
    "runner_sha256": "b2f0127dec8a039009e438b086fe5a5c77c4df7687d794d412992fe904e0a581",
    "artifact_sha256": "9e5010378964e9fdd6972554cd9a7d8b6d01ec5a3187d64ad0a9c8b8c497483f",
    "result_sha256": "e7593d631f7e52cf67f84b3bbd46df1efa75fa77f16f06d5e88e4afae901bc12",
    "raw_stdout_sha256": "37ed5e0f0a7cd32ac15f3aaea0dba66c436691edb67e5925a90a858df8b0c627",
    "independent_receipt_sha256": "c1cd30f99de325e1810df13b082e176e8246609008ed21f8d890ca7581149d59",
    "independent_receipt_observed_utc": "2026-10-23T23:19:49.896454+00:00",
    "artifact_unchanged": true,
    "compile_exit": 0,
    "run_exit": 0,
    "timed_out": false,
    "raw_stdout_bytes": 1399,
    "raw_stderr_bytes": 0
  },
  "kernel_observations": [
    {
      "phase": "initial",
      "message_size_bytes": 84,
      "audit_trailer_size_bytes": 52,
      "format_0_and_buffer_bounds_checked": true,
      "audit_word_count": 8,
      "public_libbsm_decoding": true,
      "kernel_words_match_decoded_fields": true,
      "forged_body_pid_and_token_ignored": true,
      "contemporaneous_self_task_token_match": true,
      "own_proc_pidinfo_exact_size_and_pid_checked": true,
      "birth_microsecond_range_and_overflow_checked": true,
      "identity_values_redacted": true
    },
    {
      "phase": "after-self-exec",
      "message_size_bytes": 84,
      "audit_trailer_size_bytes": 52,
      "format_0_and_buffer_bounds_checked": true,
      "audit_word_count": 8,
      "public_libbsm_decoding": true,
      "kernel_words_match_decoded_fields": true,
      "forged_body_pid_and_token_ignored": true,
      "contemporaneous_self_task_token_match": true,
      "own_proc_pidinfo_exact_size_and_pid_checked": true,
      "birth_microsecond_range_and_overflow_checked": true,
      "identity_values_redacted": true
    }
  ],
  "single_self_exec_comparison": {
    "same_measured_artifact_both_phases": true,
    "same_pid_observed": true,
    "same_checked_birth_observed": true,
    "pidversion_changed_in_this_observation": true,
    "previous_argv_treated_as_untrusted": true,
    "comparison_independently_recomputed_from_two_records": true
  },
  "synthetic_vector": {
    "scope": "SYNTHETIC_SERIALIZATION_VECTOR_ONLY; not a kernel token",
    "words_hex32": [
      "01020304",
      "11223344",
      "55667788",
      "10293847",
      "a1b2c3d4",
      "12345678",
      "76543210",
      "89abcdef"
    ],
    "little_endian_hex": "04030201443322118877665547382910d4c3b2a17856341210325476efcdab89",
    "high_bit_pidversion_hex": "89abcdef",
    "unsigned_bit_pattern_preserved": true
  },
  "strict_attempts_retained": [
    {
      "evidence_handle": "mach-own-process-direct",
      "result_sha256": "a52dcea9e2fd3425a40a05835d9c42248ec6d987dc21f7ea7f4c50c86a677f8d",
      "source_sha256": "f05cd674f6b8afd2ab9aabafd08a05ebf4f84a7161c05ea1034ece51773a4cff",
      "runner_sha256": "118aa361dd246382d501ec3e8f0bdd9e62662e078c7980f5923ca19555eb9c65",
      "profile_sha256": "76899cfbcdb3588c151be54ce4b0e43bea756e091d568fe9bc81a3495812ec45",
      "artifact_sha256": "9e5010378964e9fdd6972554cd9a7d8b6d01ec5a3187d64ad0a9c8b8c497483f",
      "compile_exit": 0,
      "run_exit": -6,
      "timed_out": false,
      "stdout_bytes": 0,
      "stderr_bytes": 0,
      "status": "FAILED; abort site unlocalized; not superseded by baseline"
    },
    {
      "evidence_handle": "mach-startup-control",
      "result_sha256": "b2cfaa9a9ba0cc3c913e8c87f2133b63ceb38717ff78092d0b739abe611a4046",
      "source_sha256": "67ce513d89c3979640b09e6ff4b7294a172d26255f5a20b978876b89703fd281",
      "runner_sha256": "8a46e40956dfd22fa3d1549f2e37e4dc62386956c40f9a4d77145d7b11ea951a",
      "profile_sha256": "dbccd3c5e223693f4cb5625fa827f98ca9df30aeeb68fbf3ddc94c67c82a7ffa",
      "artifact_sha256": "284ed160b25564cd98b15c4317481f0d4dc5caaf63a523d6ccb1ff2549291808",
      "compile_exit": 0,
      "run_exit": -6,
      "timed_out": false,
      "stdout_bytes": 0,
      "stderr_bytes": 0,
      "status": "FAILED; abort site unlocalized; not superseded by baseline"
    }
  ]
}
```

## Interpretation limits and custody

- The observed PIDversion changed across this one exec while the measured PID and checked
  birth stayed equal. This proves no general increment, monotonicity, uniqueness or wrap rule,
  and no universal birth-time lifecycle guarantee.
- Own task corroboration is contemporaneous. It does not establish an immutable foreign
  caller, dynamic code admission, verified lineage, message replay prevention or completion receipts.
- Complete inherited Mach-port isolation was not established. The fixture destroyed its own
  unregistered message port/rights and closed unrelated file descriptors before exec.
- Both strict sandbox attempts compiled but aborted with SIGABRT and no marker/output. The
  exact abort site remains unknown; no profile was weakened to obtain the separate baseline.
  Earlier launcher-specific SIGXFSZ evidence also remains retained; its exact transient write
  was not identified. No OS logs or personal crash reports were read.
- No installation, native service, model computation, pool trust or active host replacement
  occurred. Code/source and raw evidence remain outside Git; only this redacted record is published.

Private Mac custody handles are `mach-unconfined-abi-baseline/{probe.c,run.py,result.json,stdout.jsonl}`
and `mac-abi-independent-verification.json` under the retained inventory handle. Each strict
attempt has its own named directory above. Raw output/receipts are mode0600. The raw generic
own-process rows must remain attached to their enclosing explicit unconfined result; they must
never be distributed as successful sandbox or sender-admission evidence. These local custody
handles and Git publication grant no authority and assert no remote transfer or durable backup.
