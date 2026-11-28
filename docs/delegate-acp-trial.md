# Trial: a delegate driven over the Agent Client Protocol

`poolhouse.delegate_acp` drives a boxed delegate over the Agent Client Protocol (ACP) on the pinned
OpenCode (`opencode acp`, release 2.4.2, ACP protocol version 1) in place of `opencode run --format
json`. Nothing in the product imports it. The tests are `tests/test_delegate_acp.py`; they run the
delegate runner on the pinned release in the real confinement against the canned server, which is not a
model.

**Result: it does not pass.** Progress detail and the turn limit cannot be matched over what the
protocol carries. Delegates keep `run --format json`.

Sources: the protocol pages at `agentclientprotocol.com/protocol/v1`, OpenCode's ACP documentation at
`opencode.ai/docs/acp`, and `packages/cli/src/acp` at tag `v2.4.2` of `github.com/anomalyco/opencode`.

## Criteria

| Criterion | Result | What the tests show |
|---|---|---|
| Progress detail | **fail** | The protocol sends no step start or end and no per-step usage. A run has no `step` or `usage` events, so no per-step tokens, reuse percentage, speed or finish reason. Turns are a lower bound: only replies that carry text or reasoning have an id, so a reply that only calls a tool is not counted. Tokens arrive once, with the answer to the prompt. Tool calls, commands, exit status, the final text and the token totals match |
| Limit of turns (`--max-turns`) | **fail** | The run goes past the limit: nothing says when a step ends. OpenCode never answers `max_turn_requests` |
| Tokens of a failed attempt | **fail** | A failed prompt is answered with an error that carries no usage |
| Permission checks | pass | The plugin judges every call as before: a denied call does not run, a shell command is cut at its timeout, refused writes are attributed to the sandbox or the rules, a run whose plugin did not load publishes nothing |
| Stop | pass | During a command, `session/cancel` is answered `cancelled` with the turn's usage; the command ends, the change is kept as a patch and nothing is published |
| Resume after an error | pass, with a command outside the protocol | `session/resume` refuses a folder other than the conversation's own, and each box has a new folder. The driver moves the conversation first with `opencode --standalone api`, which starts and ends a server of its own |
| Message from the lead during the task | pass | By the plugin, with the next tool result, as before. ACP version 1 has no message during a turn; OpenCode has an extension, `_session/steering` (`agentCapabilities` says `steering`), which the driver does not use |
| The same sandbox | pass, with changes | `opencode acp` starts its own server on any free port, so the socket preload differs. The protocol takes no title, so the plugin gives one; otherwise a title request goes to the model on the task's cache slot |
| A computer with no sandbox | not covered | `delegate_unboxed` runs `run --format json` |
| `poolhouse-opencode --sandbox` | not covered | it prints what `run` prints |

The pinned delegate tests of `tests/test_opencode_pinned.py` that need no platform without a sandbox run
unchanged on the driver. One raises the log level by replacing today's start script; the trial's test
module raises the driver's in the same way.

## History and the list of tools

ACP gives the client no control over either, so it does not help a delegate that reads its history again.

- `session/new`, `session/load` and `session/resume` take a folder, MCP servers, further folders and, in
  the specification, an optional `systemPrompt`. OpenCode 2.4.2 does not advertise `systemPrompt` and
  does not read it.
- No method sends or edits history. `session/load` replays it to the client; `session/resume` does not.
- No method names the tools. A client adds some through MCP servers and refuses calls it is asked about.
- Compaction can be seen (`compaction_update`, when the client advertises `session.compaction`) and asked
  for (`/compact`), not shaped or stopped.
- Version 2 of the protocol is a preview. It allows a prompt during a turn and moves usage to the end of
  the turn; it describes no control of history or tools.

The control is in OpenCode's plugin hooks, which both drivers use: `context` (system text, messages and
tools of each request), `compaction` and `title`. The first requests of two delegates, one over each
driver, differ only in the delegate's name in its task and in the temporary folder named below.

Two parts of the system prompt change between boxes under both drivers, so the model server reads the
prompt again from the first of them:

- A resumed delegate's `Working directory` and `Workspace root folder` name the first box's folder where
  its first request has `.`. The plugin replaces only the current box's folder.
- The line `Prefer <folder>/opencode over generic system temporary directories` names the box's own
  temporary folder, which the plugin does not replace, so two delegates of one lead do not share the
  whole system prompt.

## What a passing driver needs

Per-step data over a second channel: the plugin subscribes to OpenCode's own events
(`session.step.started`, `session.step.ended` with tokens and finish reason; the plugin API offers
them) and reports them over the session bridge. The turn limit then reads those events. With that the
protocol carries the prompt, the text, the tool results and the cancel, and everything Poolhouse
depends on is still OpenCode's own: the plugin, the events, the move command and the preload. The
driver is about 400 lines; the reading it replaces is `Stream.line` and `Stream._spent`.

## Running a delegate over the driver with a model

From a lead session in a project, in a checkout that holds this branch, once the GPU is free:

```sh
PYTHONPATH="$PWD/src" python -m poolhouse.delegate_acp start --model MODEL --edit --task "TASK" --project DIR --wait
```

`MODEL` is the Qwen model to serve; without `--model` it is the one served now. The same task for
comparison, over today's driver, is the same line with `poolhouse.delegate`. `poolhouse-delegate status`,
`follow`, `result` and `stop` work on both. Compare in `follow` and `result`: the tool calls (both), and
the steps, per-step reuse and turns (today's driver only). The server's own log shows how much of each
prompt it reused.
