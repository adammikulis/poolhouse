# Scheduled jobs

A scheduled job is one or more of Poolhouse's own commands run later on this computer, once it is free. It has a start time
and a latest start, it outlives the terminal and the agent that made it, and what it came to is kept and told to whoever
scheduled it.

```
poolhouse schedule add FILE --at "2026-11-28 23:07" --window 2h [--name N] [--code DIR] [--python PATH] [--max-hours 24] [--dry-run]
poolhouse schedule list
poolhouse schedule show ID [--json]
poolhouse schedule cancel ID
```

`--at` takes `2026-11-28 23:07`, `23:07` (the next one), `+30m` or `now`, in local time. `--window` (default `1h`) is how long
after `--at` the job may still start. `--dry-run` checks every line, prints what would run and schedules nothing. The app shows
the same jobs under Work, Scheduled. Its button that cancels one works in a window signed in with a credential (the app, or
`poolhouse open`), in a pool nobody founded too.

`add` returns only once the job's own process holds it, and prints that process. When no process takes the job within 90
seconds, `add` fails with the process's last words and the job ends `failed`, so nothing starts it later. `add` is refused
while the selected runtime has no scheduled jobs (`poolhouse runtime ensure` deploys one that has). `add` and `--dry-run`
name what holds the device at that moment, such as a held server, as `blocking now:` lines; the job is still scheduled, and
runs only if that is gone by its latest start. `show ID --json` says `"held": true` and the `holder` (pid and interpreter)
while a process holds the job.

## The job file

One command a line, read like a bench queue file (`poolhouse.joblines`): `#` comments, `set NAME=VALUE` and `${NAME}`
(expanded when the job is scheduled), and `smoke:` whose failure skips the `then:` under it.

| line | runs |
|---|---|
| `[bench] SUBCOMMAND ...` | `python -m poolhouse.bench`: `run`, `sweep`, `concurrent`, `drafts`, `retrieval`, `speed`, `quants`, `show`, `report`, `compare`. A bench queue file works unchanged |
| `delegate --model M --model N (--task TEXT \| --task-file F) [--project DIR] [--edit] [--kind K] [--hours 4]` | the same task to a delegate on each model, one after the other, so the two never compute at once. Each is a `poolhouse-delegate` run: `poolhouse-delegate record NAME` shows it, and the step's result names both with their state, time, turns and tokens |
| `remove-model hf:owner/repo/file.gguf` | `poolhouse-models remove`: every shard of that build, only after usable results (below). A build a running process names stays |

There is no shell line. A bench line may not name a program or module to run (`--binary`, `--tool`, `--tool-arg`, `--client`,
`--ask`) or leave the job (`--detach`).

A model is removed only when every step before it passed and the steps before it came to something. A measuring step (`run`,
`sweep`, `concurrent`, `drafts`, `retrieval`, `speed`, and `quants speed`, `fidelity` or `tasks`) must have kept at least one
run, and each thing it kept must hold a row that was measured with the machine to itself and without an error; an exit of 0 is
not enough. At least one step before the removal must be such a measurement or a delegate that finished: a report alone is
not. A step that kept nothing usable says so, and the removal is `skipped` with why. A job whose only lines are removals runs
them.

[The 27B comparison](examples/qwen3.8-27b-quants.queue) is a file in use.

## What is fixed when a job is scheduled

A job runs later, so what it runs and reads as settings is copied when it is scheduled:

- The lines are expanded and checked, and kept in the job's record. The file may be edited or deleted afterwards.
- Bench steps run a copy of the source checkout's `src/poolhouse`, `docs`, `contracts` and `patches`, taken then (`--code DIR`,
  default the checkout the command runs from), with the interpreter that scheduled them (`--python`). The record names the
  commit and whether there were uncommitted changes, and every result the copy keeps is stamped with that commit. A release
  build ships no benchmark, so from one `--code` is required.
- An input file a bench line names is copied into the job. One over 64 MiB (a model) is recorded by size and time, and the
  step fails if it has changed.
- A delegate's task is read then.
- The environment: the search path, the model cache variables and where Poolhouse keeps its state, so a process started again
  by the daemon runs with them and not with the daemon's.

Not copied: the interpreter and its installed libraries, the model files, and the llama.cpp builds. A runtime deploy keeps
every installed runtime that a job which has not ended names; an interpreter outside the installed runtimes (a checkout's
virtual environment) is yours to keep.

## When it runs

Each job has a process of its own. At the start time it asks the model broker to reserve the device (`gpu-job`, shown by
`poolhouse-serve status`). The broker grants it only while no server is held or loading, nothing else claims the GPU and the
node's GPU lease is free; load alone decides nothing. A job with bench steps measures the device alone: the broker is asked
to stop each server nobody holds (it refuses one in use, and a server put up with `poolhouse-serve up` holds itself), and the
job waits while any model stays resident. While the reservation is held, only the job's own steps may start or lease a model
or claim the GPU; others wait and are told who holds it. Nothing in use is ever stopped. A delegate step and a model removal
hold no reservation: a delegate leases its model like any other.

`show` and the app say why a job is waiting. A job that has not started by its latest start ends `missed`, with why: the
device was not free, or nothing checked it (the computer was asleep or off, or no process of the job was running).

States: `waiting`, `running`, then `done`, `failed`, `missed` or `cancelled`. Every step records `ok`, `failed` or `skipped`
with why. Output is in the job's `log`.

## What it survives

- Closing the terminal, the agent or the app, and restarting the daemon: the job's process belongs to none of them.
- An update: a process that waits starts its successor on the newly selected runtime, when that has scheduled jobs and the
  start is more than 5 minutes away, and keeps the job if the successor does not take it; a running job is left running.
- A full disk: a change to the record that cannot be written is held and written with the next. A job is not started until its
  start is recorded, a running step is not stopped for it, and an end that cannot be written is told at once and written when
  there is room.
- A restart of the computer: the daemon starts a process again for every job that has not ended, as does any
  `poolhouse schedule` command. A job that was running when its process ended is `failed`; nothing is run twice. With
  autostart enabled the daemon starts at login. **With autostart not enabled, a job waiting across a restart or a log out is
  picked up only once Poolhouse is open again or a `poolhouse schedule` command runs, and one whose latest start has passed
  by then is reported missed.** `add` says which of the two applies.
- Sleep: while a step runs the computer is kept from idle sleep; while a job due within 12 hours waits, it is kept awake on
  mains power. A computer that sleeps anyway (a closed lid on battery, or idle sleep more than 12 hours before the start)
  misses the time, and the job says so. Nothing wakes a sleeping computer, and no system setting is changed.

## Notices

When a job ends, the session that scheduled it (or `--notify AGENT`) gets a workspace message; a job a person scheduled, or one
whose session is gone, is announced on the project's board. A notice that cannot be sent is tried again for seven days.

## Security

A stored job is a command that runs later with the account's reach.

- `add` and `cancel` are decided at once (`poolhouse.schedule.who`): refused to a process under any sandbox, to a delegate or
  a board's local agent and whatever they start, to a process marked as an agent's that the node places in no session, and when
  the node cannot say who asks. An agent session in no sandbox, lead or helper, and a person's terminal pass.
- No page and no network request creates or changes a job. The app's routes list, show and cancel, on this computer alone,
  and cancel only for a window signed in with a credential.
- Only the kinds above run, as argument lists, never through a shell.
- Records are under `<state>/schedule`, for this account alone. One another account can write, a link, or another account's
  file is not read.
- An agent's file tools and shell commands may not write anything under `<state>/schedule` (`poolhouse.protectedwrites`, as
  for the authority registry); reading a job's record and log stays allowed.
- A job's process keeps only the variables naming the account, the search path, the model cache and Poolhouse's state, so it
  carries no session's token and nothing that changes what code is loaded.
- Every creation (with a digest of the steps and the commit), start, end, cancel and refusal is in the authority audit log
  with who asked (`authority-audit.jsonl`).

Not covered: **the stored records and code copies are not sealed.** The runner trusts what it reads there and does not check it
against the digest in the audit log. A process of this account that is in no sandbox can edit a record or a copy and change
what a job runs, as it can already run anything; the rule above stops an agent's tools and the shell commands it can read, not
every program such a command may start.
