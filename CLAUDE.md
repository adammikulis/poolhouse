@AGENTS.md

# Claude Code instructions

[AGENTS.md](AGENTS.md) (imported above) is the complete repository policy; everything here applies in addition to it and only covers
Claude Code. [docs/glossary.md](docs/glossary.md) is the vocabulary and is binding: use its words, never the ones it bans.

## Session start and names

The `SessionStart` hook registers the lead, announces it and puts the inbox in its context; when the hook output is absent, the lead
runs `poolhouse-workspace inbox` before any other work (AGENTS.md, "The workspace", "Contact comes first"). A lead joining passes its
own model id (`--model <id>`; `claude-sonnet-5-5` unless it knows otherwise).

## Hooks and settings

`.claude/settings.json` wires `scripts/hooks/claude-bash-guard` and `scripts/hooks/claude-edit-guard` before tools run, and
`claude-session-start`, `claude-subagent-start` and `claude-subagent-stop` around sessions and subagents. `POOLHOUSE_GUARD=off` turns
both guards off.

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

The Agent tool's worktree isolation provides the worktree half of a subagent brief; the brief still names the branch. The SubagentStart
hook registers the subagent as its own identity under a board-assigned name and delivers it in the brief; SubagentStop announces done
as the subagent and retires it. A subagent prompt carries the workspace line from AGENTS.md and needs no invite or token. The
`.claude/agents/branch-worker.md` agent is the one-branch worker: it reads AGENTS.md and this file, announces, works in its worktree,
commits named files and reports. The harness confines it to that fresh tree, so `.claude/agents/branch-finisher.md` (no isolation) is
the one for a branch that already has a claimed sibling worktree.

A worker that has delivered its report is stopped at once with TaskStop, and the same goes for a worker that sends a report again. The
lead does not wait for it to exit and does not read a repeat report as new work. The branch and worktree stay; only the agent ends.

## Subagent model order

Use Haiku 5.5 (`claude-haiku-5-5`) for read-only search, summarising, narrow mechanical edits,
and lowering a budget ratchet when the brief names the metric and the test selector that checks it.
Haiku also writes a small standalone tool or script with its own tests and wires it in with additive
integration edits to existing files (an index or docs row, an import, a registry line, a regenerated
file): edits that only add references to its new code and change no existing behaviour. A trial
(a commit-history chart script) passed review: it worked, its tests failed when the code
was broken, it broke no repo rule and it took a correction cleanly. The lead reviews every such diff
to existing files before it lands.
Haiku has a thinking effort setting, and on the owner's OSWorld 2.1 cost chart (docs/model-benchmarks.md)
Haiku at `xhigh` (about 68%) beats Sonnet at low and medium for under half the cost, and at `max` (about 72%) it
matches Sonnet at high (about 73%) for about 45% of it. Only Sonnet at xhigh or max (about 81% and 84%) goes
higher. So run Haiku for well-specified code (a clear brief, tests it writes for its own code, additive
integration edits): `xhigh` by default, `max` when `xhigh` fell short or the task is harder, passing the
effort to the Agent tool. Use Sonnet, at the effort the task needs, when the work needs more than
Haiku's ceiling or when judgment decides the result: design, conflict-heavy merges, cross-module
refactors, anything near security. The chart is a computer-use benchmark, not our code: when a Haiku
run at `xhigh` or `max` fails review, move that kind of task to Sonnet and note it here.
Haiku never deletes or weakens a test or an assertion, edits authorization, grant, claim, guard,
hook or red-team code, resolves a semantic merge conflict, changes anything outside its worktree
(an install, an interpreter, a shared service), or decides that a branch is ready to land.
Use Sonnet 5.5 (`claude-sonnet-5-5`) for any other work that writes or reviews code. Use Opus 5.5
(`claude-opus-5-5`) only after Sonnet has failed on the task. Fable is used only when the owner
asks. The evidence is in docs/model-benchmarks.md. Select within that order using the capability
rules in AGENTS.md, "The main session and its agents".

## Subagent effort

Set the effort of every subagent from the task, with the Agent tool's `effort` argument (this section is the
instruction that allows it). Effort is a cost lever before the model is: on the owner's Terminal-Bench chart
(docs/model-benchmarks.md, 2.6) Sonnet 5.5 goes 20, 29, 43, 61, 70 percent from low to max at about $0.7, $0.8,
$1.9, $5.3 and $13 per attempt, so each step up is real accuracy and real cost. **Avoid max**: it is about 2.5 times the
cost of xhigh for about nine points, and a failure at xhigh is resolved first by a sharper brief, then by a second look,
and only then by max.

| Task | Model and effort |
| --- | --- |
| Read-only search, summaries, lookups | Haiku, `xhigh` (see the model order above) |
| Narrow mechanical edits, a budget ratchet with its named metric | Haiku `xhigh`, or Sonnet `low` or `medium` |
| A well-specified branch: code plus the tests that fail first, additive wiring | Sonnet `high` (the default) |
| Conflict-heavy merges, cross-module refactors, protocol or node changes, anything that touches many files | Sonnet `xhigh` |
| Security-adjacent work (authorization, grants, guards, red-team), design decisions, the hardest debugging | Sonnet `xhigh`, reviewed by a second agent |
| xhigh failed review twice, or a design the lead cannot settle | Sonnet `max`, or Opus at a mid effort: Opus peaks near 65 percent from about $4, so its top effort adds cost and no points |

A reviewer subagent runs one effort step below the worker it checks, never above it, because reading is cheaper
than writing and its job is to disagree on evidence. Record a repeated failure of a kind of task at an effort
in docs/model-benchmarks.md, not only here.

## Subagent length and cost

Price per token does not rise past 200k on the models we use: Claude 4.6 and later models except Haiku 5.5 bill the whole
1M window at the standard rate (Sonnet 5.5 $2 in / $10 out per MTok; Opus 5.5 $4 / $20; cache reads $0.10 and $0.20; platform
pricing page). **Haiku 5.5 is the exception: above 100k tokens of prompt it costs five times as much**
($0.50 in / $2.50 out against $0.10 / $0.50), so a Haiku task stays under 100k tokens of context or moves to Sonnet.
The cost that grows with length is the re-reading: every tool call of an agent re-reads its whole context (a cache read at
5 percent of the input price when the cache is warm, a full write at 1.25 times when it is not, and the cache lasts 5 minutes
unless a one-hour write was made), so a worker's bill grows with tool calls times context, roughly the square of its length.
Measured in this project: workers that ran 600 to 900 tool calls consumed 0.5 to 0.9 million tokens each. So:

- One slice per worker: a brief that names one deliverable with a stop condition. A worker that has made about 150 tool calls
  without reporting is stopped by the lead, its commits kept, and the rest is a new worker with a fresh context and a one-line
  summary of state (a continuation is cheaper than a longer life).
- Split by deliverable, not by phase: design, code, review and fixes are separate workers; a reviewer reads, runs a few
  targeted checks and reports, with its tool calls capped near 40.
- A reviewer is started once per batch of related changes, not once per commit, and a failure to register or to record an accept
  is root-caused before any reviewer is started again.
- Briefs point at files and sections; they do not paste documents. Workers read ranges, not whole files, and run the
  affected tests, not the suite, until the one gate run.
- The lead keeps its own context short: results are summarised once, large outputs are read from a file range, and a session
  that has grown past roughly 300k tokens of context is continued after a summary.
- Effort follows the table above; avoid xhigh for work a clear brief makes mechanical.
- A design is built only when the owner asked for the feature; a design nobody asked for stops at its first attack review and is
  parked, not revised through further rounds. A mechanical doc edit (a count, a name, a pointer) goes to Haiku.

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
