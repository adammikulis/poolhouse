# Native work preservation ledger

Status: **K pending; this is preservation evidence, not acceptance or cleanup authority.**
Observation: 2026-10-23, starting 22:04 UTC. Original means the Windows-mounted repository;
paths below are relative to its root. Development comparison is the fetched checkpoint
`83871eb292aadb71ff23e26067041cfe1047c17d`; later development changes do not invalidate
these point-in-time observations. This ledger changes no platform implementation.

## Protected evidence handles

These are private external archives under the native-unification state directory, not repository
paths or product trust inputs. The coordinating agent retains their physical locations.
Directories A0, A1 and A2 have mode 0700. No credential or real keystore contents were inspected,
copied or exported. Archive verification grants no runtime or publication authority.

| Handle | Directory basename | Contents and verification |
| --- | --- | --- |
| A0 | `20261023-chatgpt-ce2684` | Original bundles, 12-head manifest and two dirty-tree archives. |
| A1 | `ledger-0161a4` | Independent restored bare mirror, current ref/worktree/status/ignored metadata, ownership snapshot, verification results and prerequisite development bundle. Evidence files mode 0600. |
| A2 | `chatgpt-0161a4-paused-cargo` | Paused Cargo branch bundle, exact head, index and working patches; source worktree retained. |

| Bundle | SHA-256 | Actual result |
| --- | --- | --- |
| A0 `history.bundle` | `3c22c4c773c6c8011a4c398cb6b43835c6204661bad33beefe72205373ab6966` | Complete; verify passed; independently cloned and fsck passed. |
| A0 `native-hook-checkpoint.bundle` | `b5dfc5e93152ef8d71da78f9736ca82e05f828ffed0245f9005b4676b6cbe87e` | Complete; verify and restored fsck passed. Head `718d935d2223a8111943744287fa8982b73941ed`. |
| A0 `native-launch-preparation.bundle` | `fdf6f5f1c2aea3fa318a3e8621b40ebcec30b4e9f117346006432df9ff418d41` | Incremental; verify and restored fsck passed. Head `770161d3ab8ba959769d4cead8c3b25cf6adba0a`. |
| A1 `development-83871eb2.bundle` | `7198eadf514f43e1c3520513d8b9b9a1805b61ab981d29130531caf4c90b7a07` | Complete; verify and restored fsck passed; closes launch prerequisites. |
| A2 `history.bundle` | `2263cdd7f05dae940dd6570fcd327323b780a2c2c915687bdc1207b77f16624f` | Complete; verify and restored fsck passed. Head `ea2eebd3bd6dc9dde975477496d97112dc4151ea`. |

The launch bundle requires `977529e6b84db266904b8240aa3e52f4a7dd15a1` and
`87515c5acf340b74d5cd90931b7198af6036e7e6`; both objects were checked and are ancestors
of A1's development checkpoint. Launch head itself is not an ancestor of that checkpoint.
A0's `verification.json` was treated as an assertion and independently checked, not accepted as proof.
A1's `restore-verification.json` records fsck, all 12 original head object checks and patch apply checks.

A0 advertises **51 entries**, comprising 16 local refs, 13 remote refs, 10 other refs, HEAD
and 11 linked-worktree HEAD entries: **39 ordinary refs**, not 51 ordinary refs. All 39
remain present in the original repository's current 41-ref inventory. The two additional refs
are `fix/development-push-auth` and `review/native-hooks`. Current ref/head values are retained
in A1 `original-refs.json` and `original-worktrees.json`; no refs or worktrees were deleted.

## Original worktrees

All 12 directories exist. All current tracked/untracked source statuses are clean except W10.
Initial heads are in A0; every full initial SHA was verified in the restored mirror. “In development”
means ancestry at the comparison checkpoint, not proof that the native implementation retains a behavior.

| Handle | Relative path | Original full HEAD | Current change / ancestry |
| --- | --- | --- | --- |
| W1 | `.` | `90c120c389cfdeaac3423bf11f7a7f23e85217a7` | now `2400838cf4f4bcd447aee479c6138ab29913765e`; in development |
| W2 | `../ml-stack-agent-connect` | `cc8f8eaf2bee315b70ac985b18e2bbb5c0e60ce2` | same; not an ancestor |
| W3 | `../ml-stack-fleet-bench-process-group` | `b9510a5c45cae1be804405d9cbeee14d187958ce` | same; not an ancestor |
| W4 | `../poolhouse-native-device-plan` | `90c120c389cfdeaac3423bf11f7a7f23e85217a7` | same; in development |
| W5 | `../poolhouse-native-hooks` | `90c120c389cfdeaac3423bf11f7a7f23e85217a7` | now `718d935d2223a8111943744287fa8982b73941ed`; in development |
| W6 | `../poolhouse-one-pool` | `3cb435349a01bc6298e8106f66afb517f39dabad` | same; in development |
| W7 | `.claude/worktrees/agent-a028c4a8ee250f4f9` | `daa6a4e9dcbedbe29cec652f3543ddbd7efb9543` | same; not an ancestor |
| W8 | `.claude/worktrees/agent-a4454410272ccde80` | `3f3841f575ca5305cfcff3ebeb803de593357277` | same; not an ancestor |
| W9 | `.claude/worktrees/agent-a53c4a670a3b6d32f` | `17aab24b3826b858f60927558604812e8a29b188` | same; in development |
| W10 | `.claude/worktrees/agent-a6d292fe11b4db92e` | `724881eeb4bf3aafcc4853f6828563f288ff6539` | same; in development; dirty index/working tree |
| W11 | `.claude/worktrees/agent-a73c3d35e287e3d57` | `7b89c640f05e64bf030815ff9477315c438c247b` | same; in development |
| W12 | `.claude/worktrees/agent-ab9ae88b769e5042b` | `aa439a102087748cac85558b2f2cd00900f135b0` | same; in development |

Ownership is a point-in-time coordination observation, never authority. W5 has a live worktree
claim by `chatgpt-ce2684`. No live claim was observed for W2, W3, W6 or W7–W12;
that does not establish abandonment or permission to remove them. Current ledger ownership is
`chatgpt-0161a4`; source disposition and Mac coordination remain with the coordinating agent.
A1 retains the full claims/agents metadata separately; historical Git attribution is not copied here.

## Required behavior disposition

“Retain” below is an outstanding implementation/review requirement unless an explicit ancestry
statement says otherwise. Preserving bytes does not establish a working native replacement.
`git cherry` found no patch-equivalent non-merge commits for W2, W3, W7 or W8 at the
comparison checkpoint; their unique commits remain pending review. Merge commits are retained
as history and are not silently counted as patch-equivalent behavior.

| Plan row | Original source | Retained behavior / superseded route / remaining work |
| --- | --- | --- |
| One pool | W6 `refactor/one-pool` | Head is already in development; current tree clean, no ignored entries. Branch name is not evidence of one native pool. Preserve shared pool/ownership behavior while replacing duplicate Python execution paths. |
| Windows launch ticket | `05859e65c54891a57c32c450c7d76d9d1bdcb871` | Committed files cover fleet launch, WSL command/window and runtime CLI plus deployment/backend/window tests. Replace the WSL ticket/window route with authenticated native node IPC; retain hostile-endpoint regressions. No registered original/current tree for this branch was found. The plan's five modified and two untracked files are **not located or byte-preserved by this evidence**; owner inventory remains required. |
| Saved workers | W2 | Retain saved-worker identity and rejection of unknown execution limits in public jobs/agents. Historical paths `src/ml_stack/workspace/localagent.py`, `localcli.py`, `localresume.py` and `tests/test_agent_resume.py` are evidence sources, not a replacement architecture. Native behavior mapping/tests remain pending. |
| Benchmark process groups | W3 | Retain fixture child process isolation, cleanup and installed identity/native fixture validation. Compare `tests/test_fleet_bench.py` and historical `src/ml_stack/bench/{ops,run}.py` before replacing Python benchmark orchestration. |
| Test farm | W7 | Retain public selector ownership, roles, weighted split and planning; replace Python discovery dependencies. Source includes `scripts/test_on.py`, `testfarm/{devices,plan,report}.py`, fleet roles/split and `api/remote_tests.py`; native route mapping remains pending. |
| Bootstrap-independent deployment | W8 | Retain reviewed installer/runtime/bootstrap independence. Supersede daemon switching and session-dependent deployment. Do not wholesale merge removal of claims, trust, backoff or admission. Archived attack notes and unattended/cutover tests require review against the native design. |
| Edit ledger | W10 | Separately preserved staged `docs/notes/no-terminal-lockout-break-it.md` and staged/working `src/poolhouse/editledger.py`. Neither is accepted as finished or related to native consolidation merely by proximity. |
| Other local trees | W1, W4, W5, W9, W11, W12 | Ancestry, dirty state and ignored metadata are recorded here; ownership review remains required before any future cleanup. |
| Mac originals and paused remote model | Missing physical source inventory | Git contains version-comparison evidence, not the original Mac refs, dirty files, bundles or non-rebuildable assets. Mac owner must publish exact original SHAs, verified bundle manifests, dirty/ignored metadata and ownership through Git, including paused remote-model work. Request is with the coordinating agent; full K remains pending. |

Unique full original commits for the non-ancestor rows:

- W2: `8fe025a4208a502eb8e713ef649529d9840436d2`, `cc8f8eaf2bee315b70ac985b18e2bbb5c0e60ce2`.
- W3: `717727389849b5b7cada6d230577e0896081337b`, `b9510a5c45cae1be804405d9cbeee14d187958ce`.
- W7: `3d26812da54abb64b10d6b09d1aadb9fa0e7ba87`, `7584767ca295245e5a7bb95493ae6323f04c3f47`, `daa6a4e9dcbedbe29cec652f3543ddbd7efb9543`.
- W8: `11c92eb6de3801d33425504d91bbb3771d8f319d`, `ba60b26efd20ba9a3b60f7be86e790cb40743956`, `ca0ddd40666c2a4b7b35029326fa871007186a52`, `3e233d877dcebaeedc8e2be23ebc4947b01d36f1`, `93e3f77d048ab028b516601834cc8df6ad1a6e6c`, `3f3841f575ca5305cfcff3ebeb803de593357277`.

## Dirty bytes and paused work

A0 W5 archives preserve ten modified tracked files and three untracked source files against
`90c120c389cfdeaac3423bf11f7a7f23e85217a7`. Tracked and working patches are identical,
SHA-256 `552dfe5d99df19f63cb7acfc6bbf9649b7330a422ff447259723e1261b5f98c2`,
and apply-check independently passed. The tar has exactly three regular entries, no links:
`docs/notes/native-hooks-h-barrier.md` (5415 bytes), `src/poolhouse/workspace/cli_commands.py`
(7072), `src/poolhouse/workspace/codex_hook_cli.py` (3008). Headers were inspected without executing
archive code. W5 is now clean at the independently bundled hook checkpoint; historical bytes remain.

A0 W10 index patch (9136 bytes), working patch (627 bytes) and combined patch (9043 bytes)
were checked against `724881eeb4bf3aafcc4853f6828563f288ff6539`. The index patch applies;
the working patch applies after that index state; the combined patch also apply-checks independently.
Current index and working diffs match A0 byte-for-byte. SHA-256 values:

- Index: `8307c08022f2fd3e427c553b8d5c9d3505e30477ed520f51d133ca2fca3b7b28`.
- Working: `b073d009f0a4fb163b5b2a9b170938c590b29f42a8a41efd8740663dd515b498`.
- Combined: `7c9dfc4e7c3935fde57599a57752e2e376e6d5f7eef1247ae512e2395e1831ff`.

A2 retains branch `fix/native-cargo-targets` in sibling handle `native-cargo-targets`, including
checkpoint `883453804b18e436111d2b80a66fc8a884fb9e12`, merge
`a248e5d225e7ffc76364e0c99292c343aa568907` and head
`ea2eebd3bd6dc9dde975477496d97112dc4151ea`. Four dirty files remain:
`scripts/testapp.py`, `scripts/testnode.py`, `tests/native_cargo.py`, `tests/test_testunits.py`.
The 11114-byte full-index working patch exactly matches the live tree; its SHA-256 is
`2cc395c59a0adcc48435a8a0ab9e803ea38397af0ebe1741dead52780a4ef186`.
Index patch is empty. Independent apply-check passed. A1 additionally retains the same diff
with abbreviated object hashes; that representation difference is not a source change.
The unfinished shared-target executable alias fix is **paused and untested**; earlier passing
checkpoint tests do not validate the dirty split-directory work. No tooling fix was resumed.

## Ignored assets and exclusions

A1 `ignored-metadata.json` records lstat mode, size, modification time, symlink status and
artifact child metadata without reading restricted contents. Ignored top-entry counts W1–W12
are respectively **98, 42, 41, 38, 48, 0, 54, 61, 36, 18, 0, 46**. These are Git ignored
entry counts, not recursive asset counts. Cache directories (`__pycache__`, `.pytest_cache`,
`.ruff_cache`, `.venv`, `app/target`) are recorded as rebuildable; that does not authorize deletion.

| Tree/path | Non-cache metadata / disposition |
| --- | --- |
| W1 `.claude/settings.local.json` | 3505 bytes; potentially secret local configuration. Contents neither read nor copied; original retained. |
| W1 `.claude/scheduled_tasks.lock` | 124 bytes; runtime coordination state, metadata only, never replay as authority. |
| W1 `.claude/worktrees/` | Six directories, all accounted for as W7–W12; no additional child directory observed. |
| W1 `.full-tier-last.json` | 73-byte nonsecret measurement copied into A1; SHA-256 `66073ecf4557f56883dc75754a1e906d1fc992e20589c43b870d3c19f781c10e`. |
| W1 `dist/` | `ml_stack-0.2.2-py3-none-any.whl`, 3803964 bytes; metadata only, original retained. |
| W5 `dist/` | `compute-artifact.json` 25819 bytes; `poolhouse-0.3.0.dev1-py3-none-any.whl` 3740452; `poolhouse_studio_compute-0.3.0.dev1-py3-none-any.whl` 715852. Metadata only, originals retained. |
| W8 `dist/` | `.built-from` 65 bytes; `compute-artifact.json` 25819; `poolhouse-0.3.0.dev1-py3-none-any.whl` 3691304; `poolhouse_studio_compute-0.3.0.dev1-py3-none-any.whl` 716007. Metadata only, originals retained. |

No other non-cache ignored top entries were observed in these original trees. This does not
inventory external model caches, datasets, other machines or secret contents. Build artifacts
were not assumed reproducible or disposable: their metadata and originals remain, while byte
archival/reproducibility and historical owner confirmation remain pending where required.
No original worktree was removed, cleaned, reset or rewritten.

## Remaining evidence for K

Locate and preserve the launch-ticket dirty files; obtain the Mac original inventory and paused
remote-model bundle evidence; assign owners and map each retained behavior to the native
implementation and its real tests. Resolve any non-rebuildable artifact bytes before cleanup.
The isolated Mac derivative version record at the comparison checkpoint explicitly lacks live
process/protection/server identity, installation and native admission evidence; it cannot fill these gaps.
This ledger verifies storage consistency only, with no broad test suite or native acceptance claim.
