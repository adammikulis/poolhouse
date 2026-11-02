# A local model as a workspace agent

One command starts a downloaded model, registers it on the board as a session of its own and runs its loop detached:

    poolhouse-workspace agent start [--model auto|ID] [--name NAME] [--role ROLE] [--project PATH]
                                   [--effort off|low|medium|high|auto] [--max-effort LEVEL]
                                   [--max-output-tokens TOKENS] [--orders-from NAMES] [--no-wait]
    poolhouse-workspace agent list
    poolhouse-workspace agent stop NAME

`start` and `stop` are run by a person at a terminal, or by a registered lead session while the gate
`workspace.agents` is delegated (`poolhouse-workspace authority show`); a process an agent started that has no session
of its own is refused. The Agents panel does the same from the browser: `poolhouse-workspace board-serve` prints
a link, `/agents?session=...`, that sets the browser session cookie the start and stop routes require.

## What `start` does

1. **Model.** `auto` is the highest-ranked already-downloaded Qwen model that fits the requested
   context on this machine; Flash-Next is never chosen. With none downloaded it prints the one command
   that fetches one (`poolhouse-models fetch REF`, or the `find` that locates it) and downloads nothing.
   A model rated red for this machine's memory is refused in one line with the smaller downloaded
   choice to pass as `--model`.
2. **Identity.** The agent's record is named `local-` and the model's short name (or `--name`). The node registers
   it as a subagent of the session that starts it (the person's own session of the terminal, or the lead that
   runs the command), records that session as its parent, names it (the model family plus six characters; the name
   is printed) and gives it a token, which the client keeps in a file only this user reads and never prints. Its
   board is the project's (`--project`, else the working directory). It holds no grant a person did not sign for it
   or for its parent with `--with-subagents`.
3. **Loop.** `python -m poolhouse.workspace.localloop NAME` runs detached (`poolhouse.jobs.detach`), its
   pid and start time are recorded, and its log is `local-agents/NAME.log`. The same name again
   reports the running agent and changes nothing.
4. **Model lease.** The loop leases the model from the broker (memory admission and the queue are the
   broker's) on one slot using the maintained harness profile: measured settings, the requested
   context, q8 KV cache, an installed matching MTP head, and the maintained patched chat template.
   Installed Hub references select the cached snapshot path without downloading. A compatible
   managed server used by Chat or Coding is reused across ports; different head, template, cache,
   or context requirements remain distinct.
   A refusal ends the agent with `failed` and the broker's one-line reason.

`stop` stops the owned process and releases its lease. Its saved identity, token, settings and
device account remain available for the next start.

## Taking and giving orders

The loop polls the node for the agent's inbox. A message is acted on only when it is a clear `task` or `question`
(not held in quarantine, not to `*`; in a room it must `@name` the agent) from a live session that is the person's
own session, the session that started the agent, a main session that holds the grant `task_authority` for itself
(a lead the person gave task authority), a session named with `--orders-from` (default none; name only sessions you
control), or a subagent of one of those. Everything else is counted as information and never reaches the model. The task text goes to the model fenced as data. The result is sent back on the
thread: `answer` when the model called `done`, `status` when it stopped.

Under its role the agent has Poolhouse's own tools (`poolhouse-chat`'s) plus `workspace_roster`,
`workspace_thread`, `workspace_reputation`, `workspace_send` (kind `task`, `question` or `status`, up to five a task, no `task`
after reading an agent it does not obey; not offered in the role that only reads) and `set_effort`.
Anything the role says needs a person goes to `localtools.ask_a_person`, which answers no: nobody can
answer in a detached process. That function is the one seam the Requests inbox replaces. The agent
cannot change roles, rules, quarantine, approvals, its ceiling or its caps; it holds no credential.

A task has no cap unless the person sets one: `--max-rounds`, `--max-tool-calls`, `--max-model-calls` and
`--max-task-seconds` default to none, and the same four fields in the Agents panel's Advanced options and in `task_caps` on
`/agents/start` are the only way a cap exists. The kill switch is always there: the stop file, checked before every model call.
An agent record saved by an older version with the former profile caps (12 rounds, 30 calls, 24 model calls and 600 s for chat;
60, 150, 120 and an hour for coding) is read as no caps, because those numbers were a default and never a choice.

## Effort and the prompt cache

Effort selects reasoning: `off`, `low`, `medium`, or `high`. Native coding sends the
selected model family's thinking flag in `CLAUDE_CODE_EXTRA_BODY`; `off` explicitly disables
Qwen template thinking. It does not impose an output limit from the effort level. Explicit
caller output budgets are retained. `--max-output-tokens` sets an independent response cap for Chat
and Pi coding; with none given there is no cap and a reply may run to the context (Pi is told the context as its
`maxTokens`), and Codex and Claude use their native output settings. The Agents panel exposes the same
cap under Advanced options and preserves it with saved settings; a record saved with the former default of 8192 and no
explicit choice is read as no cap.
An explicit caller `MAX_THINKING_TOKENS=0` also disables template thinking. The runtime
does not generate a thinking-token limit that can carry over when effort changes.
The default is `off`. The model may raise or lower its own effort with `set_effort(level, reason)` up
to `--max-effort` (default `high`, the top level, so by default nothing is refused); above a lower ceiling a
person set it is refused with the ceiling in the message.
The change applies from the next task, so a task's prompt is never rewritten; the agent's status file records it.
`auto` picks per task from a rule table (status or lookup: off; plan, diagnose, review, design: medium; otherwise
low), never above the ceiling.

Within a task the prompt only grows: the system message and tool list are fixed bytes, new turns are
appended, and thinking, sampling and token ceiling stay constant. Requests go to slot 0 with
`cache_prompt`, and the server runs with `cache_idle_slots`, so the prefix survives between tasks.
No context trimming is done; the task caps keep a task within the context selected for this device.
`tests/test_workspace_local_agent.py::test_each_turn_extends_the_last_prompt_byte_for_byte_and_tasks_share_their_prefix`
checks this against a fake server.

## Profiles

`--profile chat` (default) and `--profile coding` differ in the model and the harness, not in limits: the context of both is the
largest safe fit for the selected model on this device's memory (`serve.suggest.largest_fit`, one slot: the model's trained window,
as much of it as the memory holds, the same answer `poolhouse-serve up` serves). `--ctx 256k` (k and K are accepted) overrides that
choice. Coding prefers Qwen3.8-27B (Q4_K_XL first; `poolhouse-serve memory` rates it 27.2 GiB at 256K,
q8_0 cache, MTP head shared) and has no task caps. A coding
agent runs on Pi by default through `poolhouse.coding.launch_coding_agent(model, role, project,
harness='pi')`; Codex and Claude Code are also supported.
Flash-Next is used only when named with `--model`. Before starting, the memory estimator checks the
context; when it does not fit, `start` says the longest context that does and prints the person-only
`poolhouse-serve memory --for ... --apply`. Past 85% of the context the chat loop drops whole oldest turns
down to 50%, leaving one fixed marker; nothing kept is edited, so the cached prefix survives.


Coding agents retain their registered workspace identity and process authorized inbox tasks
serially through the maintained native harness. The parent worker reads task data and posts
threaded results; the model does not need workspace shell access to finish or report a job.
Each task has a graph conversation, native session, bounded runtime and cancellable process.
The task role and native sandbox remain in force. An idle worker waits without running inference.

The local-agent routes accept POST `/agents/pause` and `/agents/resume` with `{"name":"NAME"}`
under the same person-session guard as start/stop. Pause takes effect before the next task; an
active task finishes first. The listing exposes `identity`, `paused`, `state`, `tasks` and the
latest message. Stopping a worker stops its process and releases its model lease; its registered session, token and
parent remain for the next start. `agent.task` activity links the workspace message, graph conversation, project and native session;
task completion is separate from independent verification and reputation credit.

Coding start defaults to Pi and accepts `harness` (`pi`, `codex` or `claude`), also available as CLI `--harness`.
The installed Claude Agent SDK's bundled executable is reused when `claude` is absent from PATH.
The person-authorized start registers one worker session before launching its inbox loop;
native tasks reuse that session rather than registering a new agent for each job.
