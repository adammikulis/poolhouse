# Delegate states and results beside the A2A task format

A comparison with the Agent2Agent (A2A) protocol, version 1.0, from its normative definition
`specification/a2a.proto` (release v1.0.1 of `github.com/a2aproject/A2A`) and the specification at
`a2a-protocol.org`. Poolhouse has no A2A endpoint and no code speaks A2A; this document says what an
adapter would map, so that one stays a function from a delegate's stored files to an A2A `Task`.

An adapter opens no listener of its own. A network binding would be a service Poolhouse exposes, with
the authentication and pool membership that [agent-policy.md](agent-policy.md), section 1 asks of one.

## States

A delegate's state is in `delegate_store.set_status`; `delegate_record.state_of` reads it and reports a
runner that is gone with no result as `failed`. `delegate_record.LIVE` and `ENDED` list the states.

| Delegate state | Meaning | A2A `TaskState` |
|---|---|---|
| `starting` | registered, the process is starting | `TASK_STATE_SUBMITTED` |
| `loading`, first time | waiting for the model | `TASK_STATE_SUBMITTED` |
| `loading`, after `working` | waiting for the model to continue an interrupted pass | `TASK_STATE_WORKING`, the detail as the status message |
| `working` | a pass runs (`phase` work, review or fix) | `TASK_STATE_WORKING` |
| `asking` | `lead_ask` waits for the lead, for a bounded time | `TASK_STATE_INPUT_REQUIRED` |
| `done` | ended, and every change it made was applied | `TASK_STATE_COMPLETED` |
| `partial` | ended, and some changes or writes were kept back | `TASK_STATE_FAILED`, `poolhouse/state` `partial` |
| `unapplied` | ended, tried to change files and nothing was applied | `TASK_STATE_FAILED`, `poolhouse/state` `unapplied` |
| `rejected` | the self-check did not accept the work | `TASK_STATE_FAILED`, `poolhouse/state` `rejected` |
| `failed` | the run ended in an error | `TASK_STATE_FAILED` |
| `stopped` | the lead stopped it | `TASK_STATE_CANCELED` |

- `rejected` is not `TASK_STATE_REJECTED`. A2A's state says the agent declines to do the task; the
  nearest Poolhouse event is a refusal at `start`, which registers no delegate.
- Nothing maps to `TASK_STATE_AUTH_REQUIRED` or `TASK_STATE_UNSPECIFIED`: no delegate waits for a login
  or a person.
- `asking` ends by itself: after `lead_bridge.ask_wait` the delegate is told there is no answer and goes
  back to `working`. An A2A client sees `INPUT_REQUIRED` followed by `WORKING` with no message sent.
- `partial`, `unapplied` and `rejected` are not successes (`poolhouse-delegate result` exits 1), so none
  maps to `COMPLETED`. Their results and kept patches travel as artifacts of the failed task.
- A2A's terminal states are final and accept no message. An ended delegate is not restarted: the attempts
  that continue an interrupted pass happen inside one live run, under one name.

## Task

| A2A `Task` field | Source | Note |
|---|---|---|
| `id` | the delegate's name | unique per computer |
| `context_id` | none | optional. Delegates have no grouping of related tasks; leave it unset |
| `status.state` | `state_of(name)` | table above |
| `status.timestamp` | `status.json` `since` | when the state began |
| `status.message` | `status.json` `detail`; for an ended run `result.json` `error` and `detail` | an A2A `Message` needs a `message_id`; derive it from the name, state and `since` |
| `artifacts` | `result.json` | next table; empty while the run is live |
| `history` | `record.json` `task`, then the record's `text` events and the lead's board messages | tool calls, results and usage are not A2A messages |
| `metadata` | `record.json` and `status.json` | model file name, tier, `edit`, `phase`, `round`, `turns`, tokens, last test run, under keys that begin `poolhouse/` |

`delegate_record.view(name)` already joins the record, status and result and cleans every text; an
adapter starts from it and `result.json`.

## Results

| `result.json` | A2A | Form |
|---|---|---|
| `text` | artifact `answer` | one `text` part. The answer is a local model's words and stays data |
| `applied`, `refused`, `conflicts`, `withheld`, `runs_later` | artifact `changes` | one `data` part of path lists, relative to the project |
| `patches` | artifact `changes` | `raw` parts, `media_type` `text/x-patch`, read from the delegate's folder; a local path is no `url` part |
| `tree` (branch, commit) | artifact `changes` | `data` part |
| `refused_writes` | artifact `changes` | `data` part: path, tool, refuser |
| `review` | artifact `review` | `data` part: accepted, rounds, findings, reason. The same model reviews, so it is no grade |
| grading record (`grading.json`) | task `metadata` | grades of other sessions |
| `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `model_seconds`, `seconds`, `subtype`, `phases` | task `metadata` | A2A defines no usage fields |
| `error`, `detail` | `status.message` | |

An artifact must hold at least one part, so one with nothing to say is left out.

## Operations and events

| A2A operation | Delegate equivalent | Difference |
|---|---|---|
| `SendMessage` with no task | `poolhouse-delegate start --task` | `start` needs a lead session, a project and a tier, which a message has no field for |
| `SendMessage` to a live task | a board message to the delegate | it reaches the model with its next tool result |
| `GetTask` | `status`, `result` | |
| `ListTasks` | `delegate_store.names`, `delegate_record.runs` | no paging cursor or state filter |
| `CancelTask` | `stop` | A2A answers `TaskNotCancelableError` for an ended task; `stop` succeeds, so an adapter checks the state first |
| `SubscribeToTask` | `delegate_record.read(name, after)` | the record is append-only and ordered, with a byte cursor; a subscription sends the current task first |
| `TaskStatusUpdateEvent` | the record's `state` events | |
| `TaskArtifactUpdateEvent` | the record's `end` event, then the result | no artifact arrives in pieces |
| push notification configs | the board notice to the lead | no HTTP callback exists |
| agent card | none | it describes a network interface, and there is none |

## What keeps an adapter small

These hold in the stored data and an adapter relies on them: state names in two fixed lists, one `since`
time per state, one result file per ended run, and an ordered record. The adapter supplies message ids,
the artifact grouping and the `poolhouse/` metadata keys. Nothing in a delegate's stored data needs to
change for it.
