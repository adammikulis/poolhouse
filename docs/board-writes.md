# Board writes

Measured 2026-10-19 on the owner's Mac, read-only, on the board `poolhouse-659a2c` (this project's board), 2026-10-12 to 2026-10-19.

A row is written to the board only when another agent or the owner must act on it, or when it is state a node must replicate
(an identity, a claim, an audit entry, a review). Progress, heartbeats, status mirrors, per-file or per-test claims, joined and
done chatter, repeats of one fact and anything a view can derive from a process are not written.

## How it was measured

- Command: `PYTHONPATH=src:scripts python scripts/board_tally.py` (all rows) and `... board_tally.py --days 1.75` (since the landing queue
  ended on 2026-10-18). It connects as the session that runs it, reads each kind with `read_all(kind=...)` through the node, labels a
  row by its kind, its body prefix and its lease resource, and counts the sender families (`claude`, `agent`, `chatgpt`).
- Store: the node's log of the board. `status` reported 57,317 entries (message 28,222, landing 14,155, lease 12,065, audit 1,901,
  identity 973, note 1). The session read 56,597 at the time of the dump and 56,987 in the last run; the rows it cannot read are direct
  messages to other sessions (about 720). Their writers (`ci_watch` task messages, queue notices, the node's claim expiry notices,
  `send`) are counted by the node and not classified here.
- Windows: "all" is 7.4 days; "now" is the 1.7 days after the landing queue was removed (2026-10-17 23:01, `c57e2ace3` and `cbc875c54`), which is the rate the
  current code makes. Rows per day are rows divided by the span of the rows counted.
- Attribution: `board_tally.py` counts every sender. To attribute a run to one session, count that session's own rows by kind before and
  after (the rows of a subagent carry its name); other workers write meanwhile, so a total can move for them. Rows of the test
  runner (`test-run`, `test-result`, `test-job`) keep arriving from worktrees that have not merged the cut of 2026-10-19 17:05
  (`cb0fa19c9`); they stop as those worktrees merge. A subagent that ran `scripts/test`, read its inbox and its digest for an hour wrote
  only its registration.
- Not measured: bytes per row, rows refused by the node's rate limits (they are not entries; 81 rejections are counted by `status`), and
  who read a row. Readers are found in the code.

## Ranked writers

Ranked by rows per day over the whole period.

| rank | writer (code) | rows | per day, all | per day, now | read by | acted on | now |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | test file claim and end: `testboard.BoardEvents.claimed` and `.finished`, one `test-run` and one `test-result` per test file | 17,669 | 2,400 | 2,477 | `scripts/test subscribe --key` | no | cut |
| 2 | landing queue: `beat`, `state`, `request`, `cancel` rows and their `audit land.*` copies (the old lander) | 14,939 | 2,030 | 0 | the lander runner | no | removed 2026-10-17; `landing.rs` keeps the names as retired |
| 3 | test job start and end: `job_started`, `job_done`, two rows per background job | 8,896 | 1,209 | 1,317 | followers of the thread, task watchers | no | cut; the end of a job is one row only when a task watcher follows it |
| 4 | install claims: `runtime_deploy.owning`, acquire and release for the new tree, the launchers and the selection of every build | 8,033 | 1,091 | 85 | the claim check of the hooks | no | cut to the launchers and the selection of a switch that happens (4 rows); the node writes none (device-only) |
| 5 | branch and worktree claims: `claim`, `task_worktrees`, the harness hooks | 2,110 | 287 | 151 | the claim check, `scripts/worktrees` | yes, a refusal | branch claims kept; worktree claims cut (device-only) |
| 6 | edit and area claims: `harness_claims.reserve` (one area claim per file the generic harness edits) and `claim area` | 1,792 | 243 | 0 | the claim check of the hooks | yes, a refusal | cut (device-only) |
| 7 | identity rows: register, the model recorded at a subagent's stop, retire | 995 | 135 | 224 | every node (replicated state) | yes | one row at registration and one at the end; the end carries the audit |
| 8 | landing reviews and their audit copy | 642 | 87 | 32 | `pre-push`, `land_accepts` | yes, a standing reject stops a push | kept |
| 9 | announcements by hooks: `joined` and `done` of the session and subagent hooks | 603 | 82 | 98 | none | no | removed 2026-10-19 12:00 (`eb881b97c`) |
| 10 | audit rows: `retire`, `announce_over_quota` | 483 | 66 | 92 | the audit view | no | `retire` folded into the identity row of the end; `announce_over_quota` kept |
| 11 | other announcements: `blocked` and `milestone` (ci, runtime failures, handed-back work, agents' own lines, the old lander's) | 334 | 45 | 21 | all sessions | yes | kept |
| 12 | `joined` written by a subagent or an agent as free text, from `.claude/agents/branch-worker.md` and `branch-finisher.md` | 132 | 18 | 22 | none | no | cut |
| 13 | server and port claims: `coordination` | 136 | 18 | 0 | the claim check | yes | cut (device-only) |
| 14 | `stopped, done refused` milestones: `claude-subagent-stop` when its done post fails | 103 | 14 | 25 | none | no | cut |
| 15 | `runtime ensure X (was Y)` milestones: `runtime_board.announce` on every switch | 101 | 14 | 11 | none | no | cut |
| 16 | done post of a stopped subagent: `claude-subagent-stop` | 43 | 6 | 25 | the session subscribed to the subagent | yes | kept |
| 17 | model recorded at a subagent's stop: `claude-subagent-stop` runs `whoami --model`, an identity row that only turns the label `inherited` into `claimed` | not counted | not counted | about 35 | the label shown beside a message | no | cut when the transcript names the model registered at start |

Rows 2 and 3 are 82.8 percent of the rows in the last 1.7 days (3,795 of 4,581 a day). Row 1 was 26 percent of the whole period and
has written nothing since it was removed. Renewing a claim (`heartbeat`) writes no row: the lease table writes `acquire`, `release`,
`expire` and `dead` only. Test-slot leases are files of the test broker and never reach the board.

## What changed

Each cut has a test that failed before it.

| cut | before, per day (now window) | after |
| --- | --- | --- |
| The test runner writes a canary mismatch announcement and, for a job submitted with `--task`, one message naming that task's watchers; a file's claim, a file's end and a job's start and end are local (`testboard.py`, `scripts/test`, `testreuse_run.py`) | 3,795 | 0 for a run with no watcher; one row per watched job |
| `runtime_board.announce` posts `blocked` for a failed or recovered deploy only; a switch, a superseded build and a backed-off build post nothing | 14 (11 milestones, 3 blocked) | 3 (the blocked lines) |
| `claude-subagent-stop` warns on stderr when its done post is refused | 25 | 0 |
| The subagent definitions no longer tell a subagent to announce `joined` | 22 | 0 |
| A runtime build takes its install claims after the build, its smoke and a free landing boundary, for the launchers and the selection only (`runtime_deploy.build_and_switch`; `tests/test_runtime_quiet.py`) | 6 rows for every build, a failed or deferred one included (72 a day on 2026-10-19) | 4 rows for a switch, 0 for a failed or deferred build, 0 for a runtime that is current |
| `claude-subagent-stop` records the transcript model only when it differs from the model registered at start (`spawn` returns it, `subagent_records` keeps it; `tests/test_claude_workspace_hooks.py`) | about 35 | 0 for a subagent on its parent's model |

The Rust cuts (`fix/fewer-board-writes-3`; `app/poolhouse-node/tests/board_rows.rs`, `tests/test_board_row_counts.py`):

| cut | before | after |
| --- | --- | --- |
| A claim of a worktree, area, port, server or install leaves no `lease` row (`lease::types::DEVICE_CLAIMS`, `Lease::on_board`); a branch or task claim keeps its rows | 2 rows for each claim and release; on the board 2026-10-19 (1.3 days): install 177, worktree 119, area 76 and port 2 a day, 374 together | 0; a branch claim and its release 2 |
| A session's ending is one identity row carrying `retired_by` and `detail`; the `audit retire` row is not written | 3 rows for a subagent that starts and ends (the board: audit retire 137 a day) | 2 |
| A `whoami` that changes neither the model nor the harness (only `inherited` to `claimed`) writes no identity row | 4 rows for a subagent whose model is recorded at its end | 3 for a subagent on a model of its own, 2 for one on its parent's |

Measured on a fresh node with `scripts/board_tally.py`'s labels (`scripts/test all tests/test_board_row_counts.py -s`): ten subagents that start
and end wrote 30 rows (identity 10 registered, 10 retired, audit retire 10) before and 20 after; an install claim with its release 2 rows before and 0
after; a worktree claim 2 before and 0 after; a branch claim 2 before and 2 after. The in-process harness (`board_rows.rs`) counts the same and also
counts the label-only model record (40 rows for ten before, 20 after) and the model of its own (40 before, 30 after).

Audit kinds, and which stay: `retire` is folded into the identity row of the end (its subject, who ended it and the counts are fields of that row);
`rebind` (a person moved a session), `land.review`, `land.trust` and `land.untrust`, `lease_takeover` (for the leases that are written),
`board.create` and its siblings, `file.delete`, `quarantine.release`, `enforcement.set`, `agent.effort`, `authority.set_denied`,
`announce_over_quota` and the pool's governance entries all stay: each records an act a person or another device may need to see, and none of
them is written at the rate of a session. A takeover of a device-only claim is not an entry because the claim is not.

The steady state after the Python cuts was about 600 rows a day (identity 190, claims 190, audit 92, reviews 32, the done post 25 and the
announcements someone acts on), against 4,581 a day before. The log holds 200,000 rows and holds 57,300: at 4,581 a day it is full
in 31 days, at 600 a day in 238. The Rust cuts take the device-only claims and the retire audit out of that.

Decided to keep, with who acts on it: `landing review` and `audit land.review` (a standing reject stops a push), `blocked` lines of
a failed deploy or a failed run, the `milestone` of a handed-back worker with unlanded work, and the done post of a stopped subagent
(the one signal a session subscribed to a subagent gets; the retire is a node row that wakes nobody). Decided to cut earlier and now
at zero from current code: `joined` (`eb881b97c`), `milestone: stopped, done refused`, `milestone: runtime`, the test runner's
per-file and per-job rows.

## What remains, and why

- Identity rows are node state: a registered subagent writes its identity when it starts and once more when it ends, the second
  naming who ended it and what that released (and one more identity row when its model differs from the one it was registered with).
- Device-only claims (`install`, `worktree`, `area`, `server`, `port`) are kept in the node's own table and not written to the board.
  The sessions that held a worktree claim are kept in `worktree-holders.json` for the landing reviews; the lease rows that older nodes
  wrote stay in the log until it rotates.
- The install-claim loop of 2026-10-16 19:30 to 2026-10-17 21:30 (6,918 rows from one session, a build every 70 seconds): the board
  shows one commit (`3f7cd18`) built again and again, each time into a new tree, with no other row from that session. A build took
  its claims for the new tree, the launchers and the selection first and then built for 38 seconds; the cutover needs the primary
  checkout's runtime cutover lock (then `.git/land/runner.lock`, now `.git/runtime-cutover.lock`), which the landing runner held, so it ended `busy` with the tree kept and
  the claims released, and the caller built again 32 seconds later. `tests/test_runtime_quiet.py` reproduces the rows (18 for three
  calls with the boundary held). Claims are now taken after the boundary is known to be free, ensure waits for the boundary before it
  builds (`c26590c50`), and the landing runner left on 2026-10-17 23:01. The caller that retried every 32 seconds was part of the
  landing runner; its log is gone and it is not identified further.
- On 2026-10-19 detached `runtime ensure` processes started by the refresh hooks (`post-merge`, `primary-sync`, session start) of a session that is not a lead are refused
  ("delegated to a lead agent the node has a session for") about 15 times an hour (`ensure.log`). They write no board row (the
  refusal is local), and they start a Python process each (HANDOFF).
- A done post of 25 rows a day is what a session subscribed to a subagent receives in its inbox. It is the only form of done that the
  board keeps.
