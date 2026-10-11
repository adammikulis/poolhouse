# Project task workers

Coding workers consume structured task assignments on the board. Room messages discuss work;
they do not start jobs. A registered parent session runs
`poolhouse-workspace agent schedule WORKER_NAME --agent PARENT`, and the maintained coding
launcher runs `poolhouse.workspace.localcoding` for the saved worker. A worker is a registered session that holds a
`model_slot` lease of its model; its checkout, branch and baseline are the task's `scope`.

The scheduler runs as the parent's own session. It selects queued tasks whose dependencies are completed and whose
model and capability requirements match the model lease the worker holds and its configuration, and assigns the
oldest one to the worker with `task-assign`; the allocation it prints is what the worker claims. A task is eligible
when it names the worker, was created by the worker's parent, or names no one. Only the session that started the
worker, or the creator of a task that names it, manages it, and a worker never manages itself. Blocked tasks require
authorized resumption; the scheduler does not retry them automatically. The project is the saved worker's source; it
does not grant access to a path in task text.

Each task gets a branch and a sibling Git worktree beside the saved source checkout. Its immutable baseline is the
source HEAD. Uncommitted changes in other task worktrees remain separate. The task's `scope` on the board records the
owner, worker, source path, execution path, branch and baseline; the parent reserves the checkout when it assigns the
task, and the worker's claim creates it and holds it as `worktree` and `branch` claims. The task's `allocation` record
keeps the model lease, the worker's execution configuration, the broker's runtime and the scheduler's process.
Native tools receive the scope's execution path; task descriptions cannot select another path.

The worker reloads its own session's token file before each task transition, heartbeats its task lease,
and cancels its owned native process when stopped, its wall limit expires or heartbeat fails.
Checkpoints record successful native tool returns, the repository commit and runtime/interpreter
provenance. The native worker commits its changes before answering. Uncommitted changes block
submission and remain available for review. The runner commits a report and binary patch covering
the task baseline through the native commit, including deleted files. Its proposal records the
final clean commit and hashed artifacts for independent review. A final answer alone does not
award reputation or accept a task.

Coding workers run on a supported coding harness (`pi` or `claude`). The Claude profile caps native
turns with `--max-turns`; output tokens per response use `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, and
effort stays within the saved worker ceiling. These are not a total task token budget. A task accepts model, wall
time and retry limits; memory, context, total-token and custom step requirements remain unsupported and are
rejected by its schema. Memory admission and
server context remain the existing managed broker's responsibility. Linux tests remain on hold.

The native controls use the maintained [Claude CLI reference](https://code.claude.com/docs/en/cli-reference)
and [environment-variable reference](https://code.claude.com/docs/en/env-vars).

Stop ends only the saved worker process and its verified broker holder. It retains the
registered session, token, preferences and device account. Start reuses that session
when changing models; ending the session (`retire`) is a separate operation.

Repository issue intake creates project tasks with source-revision keys,
acceptance criteria and coding limits. Board chat replies cannot complete those tasks.
Blocked intake stays blocked until an authorized review changes its condition; issue
edits and elapsed time do not retry a blocked task. The scheduler selects eligible
queued tasks and assigns an isolated worktree before native execution.

Parent scheduler and repository intake poll independently accepted worker reviews through
the maintained development integration helper. An `attempt` record on the task binds the exact review
hash before publication starts. Published, blocked and interrupted attempts are not
repeated automatically; the owner inspects a blocked or interrupted candidate before
requesting further integration. Proposal submission alone never starts publication.

An obsolete repository issue can be excluded by the person or registered worker parent:
`poolhouse-workspace agent supersede-issue WORKER --issue NUMBER --reason "Current owner decision" --agent PARENT`.
The issue ledger in the workspace folder records the authenticated decision (who, when and why) against the issue's
key. Subsequent issue edits do not remove that decision; nothing is posted to GitHub.

The person or actual registered parent can explicitly recover a blocked legacy issue
projection with `agent resume-issue WORKER --issue NUMBER --reason "Changed condition" --agent PARENT`.
Recovery preserves the failed attempt and the reason in the issue ledger and permits
one new intake attempt. This does not resume project tasks or change their retry
budget; blocked tasks use the existing task queue recovery path.
