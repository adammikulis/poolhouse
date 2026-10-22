@AGENTS.md

# Claude Code instructions

[AGENTS.md](AGENTS.md) (imported above) is the complete repository policy; everything here applies in addition to it and only covers
Claude Code. [docs/glossary.md](docs/glossary.md) is the vocabulary and is binding: use its words, never the ones it bans.

## A blocker is fixed, never reported or worked around (critical, standing)

When a design, rule, guard, lock, identity or claim scheme, review requirement, queue or tool refusal blocks development, finding
it and fixing it are one task: change the thing that blocks, in the same turn. Never state it as a fact of the system ("X cannot
because Y"), never list problems and stop, and never route around it (an extra reviewer, a manual claim, a retry, waiting). The
moment "X can't because Y" is about to be written, the next action is the change that removes Y. A report to the owner says what
was changed; when the agent is truly blocked (a person-only act, a harness permission only the owner can grant) it says what was
tried and asks the one decision with the fix as the first option. If the concept is the flaw, the whole concept is removed, not one
hook at a time. The same holds in code: a defect that shows a wrong structure (a boundary in the wrong place, hidden state
reaching a consumer, behaviour coupled that should not be, the same area fixed twice) is fixed by redesign, never by a patch
around it: write the target structure (layers, one-way boundaries, who may read what) first, rebuild to it, enforce it with a
test (import, layering or type boundary) and absorb the symptom fixes into it. A second symptom fix in one area is the signal
to stop and redesign, and a worker's brief names the target structure, not the symptom. AGENTS.md, "Reporting a problem", "A problem comes with its fix", is the same rule for every agent.

## Memory is not for project guidance

Claude Code's auto-memory holds only notes about one device or one person's own preferences. A lesson or rule for anyone
working on the project is written into the repo in the same turn (docs/agent-policy.md, section 7, "Rule files").

## Session start and names

The `SessionStart` hook registers the lead, announces it and puts the inbox in its context; when the hook output is absent, the lead
runs `poolhouse-workspace inbox` before any other work (AGENTS.md, "The workspace", "Contact comes first"). A lead joining passes its
own model id (`--model <id>`; `claude-sonnet-5-5` unless it knows otherwise).

## Hooks and settings

`.claude/settings.json` wires `scripts/hooks/claude-bash-guard` and `scripts/hooks/claude-edit-guard` before tools run, and
`claude-session-start`, `claude-subagent-start` and `claude-subagent-stop` around sessions and subagents. `POOLHOUSE_GUARD=off` turns
both guards off. The `PostToolUse` hook `claude-tool-milestones` counts each subagent's tool calls and posts a notice to the lead at 100 and every 50
after (`POOLHOUSE_TOOL_MILESTONE_EVERY`); it informs and never limits (docs/claude-subagents.md, "Tool-call milestones").

- The Bash guard refuses the command-line forms of the rules in AGENTS.md: `git add -A`, `.` and `-u`, `git commit -a`, `llama-server`
  by hand, `poolhouse-serve up` flags that skip the lease, a push to `main` until the person has selected the approval for that
  one commit (AGENTS.md, "The owner's `main`"; the main session runs `scripts/hooks/person-consume propose`, asks its question with
  AskUserQuestion word for word, and the guard and `pre-push` then let that one fast-forward through, once;
  `POOLHOUSE_PUSH_MAIN=yes` opens nothing; where the origin's ruleset on `main` requires a pull request, the same approval covers a
  pull request from a `promote/<date>` snapshot that the main session merges under the live `release-main` authorization), and a
  force push, a remote ref deletion, `--all` or `--tags` pushes, past any `NAME=value` written in front of the command.
- The edit guard refuses, at the moment it is written, the writes AGENTS.md, "The gates" lists (a duplicate function body, a raw HTTP
  call, a docstring over twelve lines, a signature over eight parameters, a file over its line limit or made longer). It reads the two
  limits from the checkers.
- Claude Code sets `CLAUDECODE` for every command it runs. `--allow-increase` on the budgets and the `pre-push` hook's agent
  restrictions key on it; a terminal sets nothing.
- `POOLHOUSE_WINDOW_POSITION` is set in `.claude/settings.json`.

## Commit attribution

End git commit messages with the attribution trailer the session's system reminder gives, and end pull request descriptions with the
line it gives. The commit message rules in AGENTS.md apply to the subject and body above the trailer.

## Subagents and the Agent tool

At the start of a request the lead asks the owner (AskUserQuestion) what it cannot settle itself, before any brief or dispatch (AGENTS.md,
"Questions to the owner"), and a brief for a demo, simulation or example carries the end state and the real timing (AGENTS.md, "Briefing a demo,
simulation or example"). The Agent tool's worktree isolation provides the worktree half of a subagent brief; the brief still names the branch. The SubagentStart
hook registers the subagent as its own identity under a board-assigned name and delivers it in the brief; SubagentStop records its done on the board
for the sessions subscribed to it (its parent is, by default) and retires it. A subagent prompt carries the workspace line from AGENTS.md and needs no invite or token. The
`.claude/agents/branch-worker.md` agent is the one-branch worker: it reads AGENTS.md and this file, announces, works in its worktree,
commits named files and reports. The harness confines it to that fresh tree, so `.claude/agents/branch-finisher.md` (no isolation) is
the one for a branch that already has a claimed sibling worktree.

A worker that has delivered its report is stopped at once with TaskStop, and the same goes for a worker that sends a report again. The
lead does not wait for it to exit and does not read a repeat report as new work. The branch and worktree stay; only the agent ends.

## Subagent model, effort and cost

Haiku 5.5 for read-only search and narrow mechanical edits, Sonnet 5.5 for anything that writes or reviews code, Opus only
after Sonnet has failed, Fable only when the owner asks. Set `effort` on every Agent call from the task: `high` or `medium`, chosen by
the task (`medium` for routine edits, docs, mechanical migrations and read-only audits; `high` for logic-heavy work), `xhigh` only for
security, protocol or cross-module work, never `max`. One deliverable per worker, with a budget the lead states in the brief.
The tables, prices and reasons are in [docs/claude-subagents.md](docs/claude-subagents.md); read it before choosing a model or effort.

## The heartbeat

A lead keeps a heartbeat prompt firing every 55 minutes for the whole life of its session (the prompt cache goes
cold at 60), as a chain of one-shot cron jobs where each firing schedules the next, and recreates it at each
session start. It is a standard part of driving this project and is never deleted when the queue empties: each
firing does real work or ends in one line. The prompt and the rules are in docs/heartbeat.md.

## Browsers

Never drive the owner's own browser through the `claude-in-chrome` tools to test this project's pages. AGENTS.md, "Driving a
browser", gives the reason and the alternatives: your own Chromium through playwright, or headless with screenshots read.

## poolhouse-claude

`poolhouse-claude` runs Claude Code against a local model under a broker lease. Drive it in a scratch directory, never in a checkout
being edited (AGENTS.md, "Driving a model on this machine").
