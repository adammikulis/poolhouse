# Tasks and independent outcomes

## On the node

The agents' task commands run on the node (`docs/node.md`, "The task board"): `task-create`, `tasks`, `task`,
`task-subscribe`, `task-unsubscribe`, `task-assign`, `task-claim`, `task-heartbeat`, `task-checkpoint`, `task-submit`,
`task-review`, `task-credit` and `enforcement`, the `workspace_task*` MCP tools and `ph.board.tasks`/`add_task`/
`review_task`. A task belongs to the board it is made on, which is its project; a task of another device counts only
for a device on the landing list.

A parent gives a worker a task with `task-assign ID WORKER LEASE`, where LEASE is the worker's lease of its model
(`lease_list`); the answer's `allocation_id` is what the worker claims. The claim holds a two-minute `task` lease of
the node that `task-heartbeat` renews; a lease that is not renewed is recovered by an independent session (`recover`),
which spends one retry. A submission is stamped with the model of the lease and the worker's registration, and its
review names the exact proposal. In `open` mode any other session reviews; in `strict` mode a person (a session with no
parent that holds the grant `task_authority`), a designated reviewer, or the creator who is the worker's parent.
`enforcement` shows or changes the mode of the board. A task with a reserved scope stays `accepted` until its landing is
recorded with `task_complete`; the credit of an independent review is recorded in the encrypted work ledger under the
board's id.

The person's Tasks page is on the node too: the UI daemon reads and writes it as the person's own session on the board
(`docs/node.md`, "The person's session"), so a task made by `task-create` and a task made on the page are the same row.
The local project task workers (scheduler, worktrees, integration, source recovery) and `task-integrate` run on the
node as well: a worker is a registered session that holds a `model_slot` lease, its checkout, branch and baseline are
the task's `scope`, and the integration, attempt, event, allocation and evidence records are kept on the task
(`task_record`, `docs/project-task-workers.md`).

Open **Tasks** in the main UI to inspect structured work. Search or filter by status, select
an item, and inspect its account, physical device, registered worker, bound model,
resource lease, checkpoints, artifact hashes and independent reviews. The Board remains
available for discussion; a discussion message does not acquire a task or resource lease.

A person creates a task with a title and explicit acceptance criteria (`task-create`, or the Tasks page). The node
keeps it as `task` entries of the board, stamped with the session that wrote each one: the task, its assignments and
allocations, leases, checkpoints, proposals, artifacts and reviews. A source key deduplicates an identical task;
reusing it for a different specification is refused.

Workers claim with `task-claim ID ALLOCATION_ID` for the allocation the scheduler assigned. The node checks that the
worker was assigned the task and still holds the model lease of this device that the allocation names, then takes the
claim lease `task` for it; the scheduler offers a worker only queued tasks whose dependencies are completed and whose
capabilities its configuration provides. Execution uses the scheduler's prepared worktree and immutable baseline,
never a path from the task description. Models and harnesses are task provenance; balances belong to the
authenticated physical-device base account.

A heartbeat renews the task lease. Worker checkpoints, proposed checks and claimed usage
remain claims. Submission records artifact hashes and the actual allocated model/runtime;
it does not complete the task. An independent person or authorized reviewer records passed
checks covering every acceptance criterion before an outcome is accepted. Coding tasks remain
accepted until their reviewed commit lands and their worktrees and branches are removed; only
then do they become completed. Reviewers
are sessions of the task's board and never review their own work. Rejected work and
infrastructure blockage are separate outcomes.

Each board runs in one of two enforcement modes. In `open` mode, the default, any other session of the board
reviews a task, and a worker needs only an assignment that names it and the model lease it holds. In `strict` mode
a reviewer must be a person (a session with no parent that holds the grant `task_authority`), a designated reviewer,
or the creator of the task when it is the worker's parent. `poolhouse-workspace enforcement` shows the mode (`show`,
readable by any session) and a session with no parent that holds `task_authority` changes it (a lead the person gave
that grant with `grants-setup`): `check` lists the tasks that strict mode would strand, `promote` switches to strict
and `demote` to open in one step, and `set open|strict` does the same by name. A subagent cannot change it, and
`authority preset dev|prod` sets it together with the delegable gates. The mode is an `enforce` entry of the board's
tasks, so every change is kept with the session that made it, the time and the new mode. A change applies to new
assignments and reviews; leases, submitted work and accepted reviews continue under the rules they started with.

Expired working leases require **Recover expired lease**. Blocked work requires
**Authorize resume**, with a reason describing the changed condition. These actions preserve
checkpoint history and do not grant resources; the next worker claim still validates a live
allocation. Recovery consumes the configured retry budget. Supported limits currently cover
model choice, wall time and retries. Memory, context and token limits require maintained
broker/runtime enforcement before they can be represented as guarantees.

Independent review automatically records the verified outcome in the encrypted work ledger.
Accepted work earns completion credits once, with independently evidenced quality bonuses
when recorded by the reviewer. If the ledger is unavailable, the review remains saved and
the UI reports credit recording as pending. **Record verified outcome** retries the same
review idempotently; it cannot mint a second award or substitute worker checks. Quality is
shown as unrated until independently assessed. Reliability uses accepted/rejected outcomes;
infrastructure blockage is excluded. **Runs are free**: balances and reputation do not
restrict access or spend credits.

The person-facing API is `GET /ui/projects/ID/tasks`, `GET /ui/projects/ID/tasks?id=task-…` (the project's board; the
older `/ui/tasks` answers for the daemon's working directory), and same-origin `POST` to the same path. POST actions are create (`spec`), review (`id`, `decision`), recovery or
resume (`id`, `reason`), and credit retry (`id`). Fleet authenticates the browser; the
workspace person identity stays server-side. Browser requests cannot supply worker tokens,
claim resource allocations, or grant project permissions.

Agents use the maintained workspace CLI (`task-create JSON`, `tasks`, `task ID`, `task-assign ID WORKER LEASE`,
`task-claim ID ALLOCATION`, `task-heartbeat ID`, `task-checkpoint ID JSON`, `task-submit ID JSON`,
`task-review ID JSON` and `task-credit ID`). JSON payloads can be read from stdin with `-`. The corresponding
`workspace_task*` MCP tools run as the launcher's registered session; they cannot create tasks or grant
permissions. Claims require an allocation the worker was assigned on a model lease it still holds. Review uses the
same independent-review and idempotent outcome adapter as the person UI; a locked credit store leaves the saved
review intact and reports pending credit.

Reviewed native work can land through `task_integration.integrate(workspace, token, task_id)`.
The same authenticated operation is available as `poolhouse-workspace task-integrate ID`
and the `workspace_task_integrate` tool. It needs existing development-branch claim authority;
an accepted review does not grant publication rights. A foreign live development claim
blocks integration before any candidate or source-claim changes.
The helper requires the exact committed task branch, tracked artifact hashes and committed
full patch that the independent review accepted. It merges unchanged source into its own
candidate worktree, runs the maintained quick, structural and serving-security gates,
then fast-forwards the checked-out development branch. Local integration is the default;
publication requires an explicitly authorized `publish=True` request.
Before preparing a candidate and before landing, it fetches and refuses a stale or divergent
remote baseline. A repository lock serializes integration. Clean-tree checks reject unfinished
merges and uncommitted changes. It removes the landed task and candidate worktrees and their
merged branches, verifies removal, and records completion only after cleanup succeeds. Unknown
ignored files or other unique work preserve a cleanup-required outcome for inspection and retry.
It records integration, gate, commit and outcome records on the task. Publishing
does not award credits or grant permissions.

Conflicting changes, changed review evidence, failed gates and push-hook ownership blocks
preserve the candidate for inspection. Only the authenticated parent or person can return
the exact completed child's delegated worktree claim. The helper never removes or locks
another agent's worktree to clear a publication block; the recorded reason identifies the
condition that its owner must resolve. Completed and published integrations are idempotent.

The parent resource assignment reserves the task branch, path and committed development
baseline. Claiming the task creates that checkout; nested worktrees inside the primary
checkout are refused. Worker loss or cancellation preserves its task scope for recovery,
rather than deleting unfinished files or reporting completion.
The worker takes the checkout's `worktree` and `branch` claims when it claims the task and holds them until its task
lands or its parent recovers the checkout; the claims are not tied to the task lease, so a worker can still edit its
checkout after it submits. Integration refuses a changed commit, a dirty checkout or an artifact hash other than the
reviewed one, so work done after the review never lands.
Independent reviewers can open **Review quality — advanced** to link a validated result,
prevented regression or demonstrated impact to specific independently passed checks and
submitted artifact hashes. The server applies fixed, bounded bonus tiers; the interface has
no arbitrary credit amount. Optional quality and reliability assessments require a separate
reason. Task reliability still derives from actual accepted/rejected outcomes, with
infrastructure blockage excluded. Leaving advanced controls untouched keeps a plain accepted
review at the standard credit assessment and leaves quality unrated. Coding completion credit
waits for landed work and verified cleanup.
Native file and logical source-area reservations carry the authenticated task-worktree
assignment. After exact accepted review and release of its execution lease, returning the
worktree also atomically releases only that task's matching file and area reservations.
Claims for another assignment, unassigned work, ports or installation environments remain
owned by their original agent.

Task inspection includes the integration for its current independent review, earlier
integrations, publishing events and the scheduler's once-only attempt. Details expose
the exact reviewed proposal and source commit, gated development commit, gate results and
output hashes, plus a blocked reason and claim owner. Credentials, interpreter paths and
private workspace paths are excluded. Reading these records cannot retry publishing or
change an acceptance decision.

Blocked candidates remain owned and preserved. Conflicts or changed source require a new
proposal and independent review. A future explicit publication recovery can reuse a gated
candidate only after rechecking its immutable review, ownership, clean commit and unchanged
development/remote heads; that recovery action is not currently exposed. The scheduler
does not repeatedly attempt the same review.

A registered worker's authenticated parent can relocate its pending source bindings after
stopping the worker and dematerializing every affected task checkout:

```sh
poolhouse-workspace task-rebind-source WORKER CHECKOUT "Source checkout recovery" --agent PARENT
```

The replacement must belong to the same physical Git repository and contain every original
baseline. Task states, specifications, grants and historical resource records stay unchanged.
The operation records source checkpoints and an interruption log; rerunning the same
command resumes a prepared recovery. It runs only on the device that started the worker. Remove the old
source checkout only after the command reports verified recovery and normal Git preservation
checks show that the checkout has no unique work.
