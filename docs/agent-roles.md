# Agent roles and saved rules

`poolhouse-chat` is the one agent command. `poolhouse-chat` is a conversation; `poolhouse-chat
"run benchmarks with quince-2b"` (or `--task`) is a task: the agent asks what the task leaves
open, states its plan once, runs the calls the plan names and ends on `done`, printing
a cost line. What differs between the two is a role, a row in `poolhouse.roles.ROLES`.

Every call is decided at once: it runs, or it is refused with the reason. Nothing waits for a yes,
nothing is put in the Requests inbox, and no one has to be at a terminal. The person's own messages (the task, an
answer to `ask_user`, `/role`, `/rules`) are how they steer.

| role | tools | what lets a call that acts run | default for | limits (calls, GPU time) |
| --- | --- | --- | --- | --- |
| `read-only` | the reads (status, find, history, views); nothing that starts, stops, downloads or writes | nothing acts | | 60, 0 s |
| `approve-first` | reads and the acting tools | a saved always rule | conversation | 200, none |
| `plan-and-go` | reads and the acting tools | a saved always rule, or a step of the plan the agent stated | task | 200, 6 h |

`read-only` may look and never change anything; `approve-first` runs a call that acts only when an always rule the owner saved covers it, and refuses every other with the reason; `plan-and-go` also runs the calls the plan it states names, still bound by the destructive-action classifier, the taint rail and the protected actions below.

`--role NAME` picks one at the start; `/role NAME` changes it from the prompt. Only the
typed line does either: the model has no tool for it, its text and tool output are
never read as commands, and asking it to change the role is refused with the command to type. A role
can never hold a protected action (release or purge quarantine, approve a host, grants, the
sentinel and guard policy, baselines, the role and the rules): those tools do not exist, and a call
that names one is refused to an agent with the command the owner runs outside the agent.
`tests/test_roles.py` walks every role against every tool and asserts they are absent.

A plan step names a tool first and every value the call will carry
(`bench_run ["run", "quince-2b.gguf", "--sample", "10"]`). A plan goes at once and covers a call when
the tool matches and each of its values appears in a step; the step then covers that one call.
A call outside the plan is refused. A run that has read text from outside (a model card, a log)
refuses every acting call whatever the role, and a plan made after that text was read covers
nothing: only an always rule set to apply after outside text (`/rules tainted N`) lets a call run
then, and the taint rail still judges each value. A path outside Poolhouse's state is always refused.
A call the destructive-action classifier labels destructive or unsure is refused in every role, and no
always rule can be saved for it ([destructive actions](destructive-actions.md)).

## Rules

```
you> /rules always serve_up model=quince-2b.gguf
always rule saved
1. Always allow serve_up for model quince-2b.gguf  [0x, since 2026-11-22]
```

A rule is a tool, one pattern per argument of the call (exact, or with `*` and `?` in a never rule; no regular
expressions), a verdict, an optional role and a created date, and a count of how often it
fired. A call matches only when it carries exactly the arguments the rule names. A never rule
beats an always rule, which beats the role's own refusal, and a never rule stops a call even inside a plan-and-go
plan. No always rule can be saved, and an existing always rule does not apply, for a downloaded
model (its size is not known first), a path outside Poolhouse's state, a wildcard in a value, a
tool a feature added that acts (only the plan-and-go role runs it), or a run that has read outside text (unless the
rule was given `/rules tainted N`; the taint rail still judges its own question).

Rules live in `~/.poolhouse/agent-rules.json` (`schema_version` 2), mode 0600, written atomically, only by
the owner's typed `/rules` or `poolhouse-chat rules`:

| | |
| --- | --- |
| `/rules` or `poolhouse-chat rules` | the numbered rules in words, with count and date |
| `/rules always serve_up model=quince-2b.gguf` | let exactly that call run, in any role that has the tool |
| `/rules never serve_up model=quince-2b.gguf` | refuse exactly that call in every role |
| `/rules remove 3` | delete rule 3; the call is refused again by the role |
| `/rules flip 3` | always <-> never |
| `/rules tainted 3` | let always rule 3 apply after outside text was read |
| `/rules clear` | delete every rule |

Each change is logged (`agent-rules-events.jsonl` beside the file), and each call a rule decides is in the
activity log (`approval.rule_fired`). A file that cannot be read
whole, has an unknown shape or tool, or is readable by others, matches nothing: every acting call is refused
and `/rules` says why. No tool can write it: a tool argument naming it is refused.

## Adding a feature

`chat.extensions(person, mem)` returns a `roles.Extension`: `tools` (schema and callable pairs),
`context` (text added to the system message when a session starts), `start` (text put in front of
the first message of a session and of each `/new`), `commands` (slash commands for the person,
such as `/memory`), `reads` (tool names every role may call), `acts` (names that change
something; only the plan-and-go role runs them, and no always rule can cover them) and `checks_itself` (names
that change something and check their own limits inside the tool: only roles that act are offered them,
the rail adds no rule of its own). `remember` is in `checks_itself`
and `recall` in `reads`, so the `read-only` role recalls but cannot remember.
