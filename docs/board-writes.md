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
- Not measured: bytes per row, rows refused by the node's rate limits (they are not entries; 81 rejections are counted by `status`), and
  who read a row. Readers are found in the code.

## Ranked writers

Ranked by rows per day over the whole period.

| rank | writer (code) | rows | per day, all | per day, now | read by | acted on | now |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | test file claim and end: `testboard.BoardEvents.claimed` and `.finished`, one `test-run` and one `test-result` per test file | 17,669 | 2,400 | 2,477 | `scripts/test subscribe --key` | no | cut |
| 2 | landing queue: `beat`, `state`, `request`, `cancel` rows and their `audit land.*` copies (the old lander) | 14,939 | 2,030 | 0 | the lander runner | no | removed 2026-10-17; `landing.rs` keeps the names as retired |
| 3 | test job start and end: `job_started`, `job_done`, two rows per background job | 8,896 | 1,209 | 1,317 | followers of the thread, task watchers | no | cut; the end of a job is one row only when a task watcher follows it |
| 4 | install claims: `runtime_deploy.owning`, three paths per build, acquire and release | 8,033 | 1,091 | 85 | the claim check of the hooks | no | kept; node follow-up |
| 5 | branch and worktree claims: `claim`, `task_worktrees`, the harness hooks | 2,110 | 287 | 151 | the claim check, `scripts/worktrees` | yes, a refusal | kept |
| 6 | edit and area claims: `harness_claims.reserve` (one area claim per file the generic harness edits) and `claim area` | 1,792 | 243 | 0 | the claim check of the hooks | yes, a refusal | kept; node follow-up |
| 7 | identity rows: register, the model recorded at a subagent's stop, retire | 995 | 135 | 224 | every node (replicated state) | yes | kept; node follow-up |
| 8 | landing reviews and their audit copy | 642 | 87 | 32 | `pre-push`, `land_accepts` | yes, a standing reject stops a push | kept |
| 9 | announcements by hooks: `joined` and `done` of the session and subagent hooks | 603 | 82 | 98 | none | no | removed 2026-10-19 12:00 (`eb881b97c`) |
| 10 | audit rows: `retire`, `announce_over_quota` | 483 | 66 | 92 | the audit view | no | kept; node follow-up |
| 11 | other announcements: `blocked` and `milestone` (ci, runtime failures, handed-back work, agents' own lines, the old lander's) | 334 | 45 | 21 | all sessions | yes | kept |
| 12 | `joined` written by a subagent or an agent as free text, from `.claude/agents/branch-worker.md` and `branch-finisher.md` | 132 | 18 | 22 | none | no | cut |
| 13 | server and port claims: `coordination` | 136 | 18 | 0 | the claim check | yes | kept |
| 14 | `stopped, done refused` milestones: `claude-subagent-stop` when its done post fails | 103 | 14 | 25 | none | no | cut |
| 15 | `runtime ensure X (was Y)` milestones: `runtime_board.announce` on every switch | 101 | 14 | 11 | none | no | cut |
| 16 | done post of a stopped subagent: `claude-subagent-stop` | 43 | 6 | 25 | the session subscribed to the subagent | yes | kept |

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

The steady state after these cuts is about 630 rows a day (identity 224, claims 236, audit 92, reviews 32, the done post 25 and the
announcements someone acts on), against 4,581 a day before. The log holds 200,000 rows and holds 57,012: at 4,581 a day it is full
in 31 days, at 630 a day in 227.

## What remains, and why

- Identity and audit rows are node state: a registered subagent writes its identity when it starts, again when its model is recorded
  from its transcript, and once more when it retires, with one audit row for the retirement.
- Claim rows for paths that exist on one device only (`install`, `worktree`, `area`, `server`, `port`) are replicated to the pool
  although no other device can hold them. One session made 6,918 install rows in 26 hours (a build every 70 seconds, three paths
  each); its cause is not traced to a log, the runtime refresh was reworked on 2026-10-19 (`258dc2826`) to build a settled tip once,
  and the rate since 2026-10-18 is 85 a day. Keeping those claims in the node's own table and out of the board log is a change to
  `app/poolhouse-node/src/lease/` and `claims.rs` (HANDOFF).
- A done post of 25 rows a day is what a session subscribed to a subagent receives in its inbox. It is the only form of done that the
  board keeps.
