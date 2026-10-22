# Claude Code subagents: model, effort and cost

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
Haiku at `xhigh` (about 68%) beats Sonnet at low and medium for under half the cost: effort buys real accuracy at
Haiku's price. Haiku runs at `medium` or `high` by the effort rule below, and does no work that rule gives `xhigh`
(security, protocol or cross-module). So run Haiku for well-specified code (a clear brief, tests it writes for its own
code, additive integration edits), passing the effort to the Agent tool. Use Sonnet, at the effort the task needs, when
the work needs more than Haiku's ceiling or when judgment decides the result: design, conflict-heavy merges,
cross-module refactors, anything near security. The chart is a computer-use benchmark, not our code: when a Haiku run
fails review, move that kind of task to Sonnet and note it here.
Haiku never deletes or weakens a test or an assertion, edits authorization, grant, claim, guard,
hook or red-team code, resolves a semantic merge conflict, changes anything outside its worktree
(an install, an interpreter, a shared service), or decides that a branch is ready to land.
Use Sonnet 5.5 (`claude-sonnet-5-5`) for any other work that writes or reviews code. Use Opus 5.5
(`claude-opus-5-5`) only after Sonnet has failed on the task. Fable is used only when the owner
asks. The evidence is in docs/model-benchmarks.md. Select within that order using the capability
rules in AGENTS.md, "The main session and its agents".

## Subagent effort

Set the effort of every subagent from the task, with the Agent tool's `effort` argument (this section is the
instruction that allows it). The default is `high` or `medium`, chosen by the task: `medium` is the normal default for
routine edits, docs, mechanical migrations and read-only audits, and `high` is for logic-heavy work. `xhigh` is only for
security, protocol or cross-module work. Never `max`. Effort is a cost lever before the model is: on the owner's
Terminal-Bench chart (docs/model-benchmarks.md, 2.6) Sonnet 5.5 goes 20, 29, 43, 61, 70 percent from low to max at about
$0.7, $0.8, $1.9, $5.3 and $13 per attempt, so each step up is real accuracy and real cost; `max` is about 2.5 times the
cost of `xhigh` for about nine points. A failure at `xhigh` is resolved first by a sharper brief, then by a second look,
and only then by Opus at a mid effort. A large test or docs refactor is `medium` or `high`, never `xhigh`. Running agents
keep the effort they started with.

| Task | Model and effort |
| --- | --- |
| Read-only search, audits, summaries, lookups | Haiku `medium` or `high` |
| Narrow mechanical edits, docs, a budget ratchet with its named metric | Haiku `medium`, or Sonnet `low` or `medium` |
| A well-specified branch: code plus the tests that fail first, additive wiring | Sonnet `high` or `medium`, by how much logic it holds |
| Logic-heavy work: new behaviour, design, debugging, a refactor with many call sites | Sonnet `high` |
| Conflict-heavy merges, cross-module refactors, protocol or node changes | Sonnet `xhigh` |
| Security-adjacent work (authorization, grants, guards, red-team) | Sonnet `xhigh`, reviewed by a second agent |
| `xhigh` failed review twice, or a design the lead cannot settle | Opus at a mid effort: Opus peaks near 65 percent from about $4, so its top effort adds cost and no points |

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

- One slice per worker: a brief that names one deliverable and a budget, either a number of tool calls or a stopping
  condition ("run until X is delivered; report at the latest after N calls if blocked"), chosen from the task's size. No
  agent definition sets a turn ceiling (`maxTurns` is absent from `.claude/agents`), so the budget is the brief's. A worker
  that reaches its budget with work left says exactly what remains and pushes no half-done work unless the brief says to; the
  lead raises the budget by resuming that worker with SendMessage. A worker that is long past a sensible size is better
  replaced by a new one with a fresh context and a one-line summary of state (a continuation is cheaper than a longer life).
  The lead is told at 100 tool calls and every 50 after ("Tool-call milestones" below), to decide whether to extend, check in or stop.
- Split by deliverable, not by phase: design, code, review and fixes are separate workers; a reviewer reads, runs a few
  targeted checks and reports, with a small budget the brief states.
- A reviewer is started once per batch of related changes, not once per commit, and a failure to register or to record an accept
  is root-caused before any reviewer is started again.
- Briefs point at files and sections; they do not paste documents. Workers read ranges, not whole files, and run the
  affected tests, not the suite, until the one gate run.
- The lead keeps its own context short: results are summarised once, large outputs are read from a file range, and a session
  that has grown past roughly 300k tokens of context is continued after a summary.
- Effort follows the table above; `xhigh` is never used for work a clear brief makes mechanical.
- A design is built only when the owner asked for the feature; a design nobody asked for stops at its first attack review and is
  parked, not revised through further rounds. A mechanical doc edit (a count, a name, a pointer) goes to Haiku.

## Tool-call milestones

The lead decides whether to extend, check in on or stop a long subagent, so it is told when each one reaches 100 tool calls and
every 50 after (100, 150, 200, 250, ...), whatever budget its brief stated. The interval is `POOLHOUSE_TOOL_MILESTONE_EVERY` (50, set in `.claude/settings.json`
`env`). The notice informs; nothing in it limits or stops the subagent.

**What a `PostToolUse` event carries inside a subagent** (read from a real headless run with a logging hook and a subagent that
made three Bash calls): every field the main session's event has (`session_id`, `transcript_path`, `cwd`, `prompt_id`,
`permission_mode`, `effort`, `hook_event_name`, `tool_name`, `tool_input`, `tool_response`, `tool_use_id`, `duration_ms`) plus
`agent_id` and `agent_type` (for example `a72ba00f70ef2456e`, `general-purpose`). A call the main session makes has no `agent_id`,
which is what tells them apart; the `Agent` call that starts the subagent is the main session's. `SubagentStart` carries the same
`agent_id` and `agent_type` and no description; the description is in `<session>/subagents/agent-<agent_id>.meta.json`
(`description`, `agentType`, `spawnDepth`) beside the transcript named by `transcript_path`. The event holds no counter.

**The counter** is `scripts/hooks/claude-tool-milestones` (with `tool_count.py` and `milestones.py`): it appends a byte to the subagent's count file for each event,
and creates an exclusive marker per `tool_use_id`, so an event delivered twice counts once (an event without one counts as it
arrives). At the first call at or past a milestone, one exclusive `milestone-N` marker is claimed, so parallel calls and
repeated deliveries announce once. Only then does the hook import anything from poolhouse; an ordinary call stays one small
file append. The `SubagentStart` hook records `started` with the subagent's record, for the elapsed time.

**The notice** is a board post `milestone: NAME has made N tool calls (running 1h06m): DESCRIPTION` by the subagent itself,
`--type status --subject milestone`, to `#general`. The lead follows each subagent in inbox mode (`child_watch`), so it arrives
in the lead's inbox and in its `attention` text between turns. The harness gives a subagent's `additionalContext` to the subagent,
never to the lead, so the lead is not reached that way. A refused post warns on stderr, is tried again on the next two calls,
and after the third refusal the subagent is told (in its context) that the lead could not be told and to say so in its report.

## The roster

A worker that needs a sibling finds it itself; the lead never relays names. The Agent tool's agent id is not a board name, so the
board keeps, for each session, what it is for and where it works:

- **Recorded at start.** `claude-subagent-start` reads the description of the Agent call from the meta file beside the transcript
  (`SubagentStart` itself carries only `agent_id` and `agent_type`), the branch and top directory of the checkout it starts in, and
  has the subagent say them as itself in the call that builds its brief, which costs no extra process: `brief --registered --purpose TEXT
  --agent-type TYPE --branch NAME --worktree PATH` (`whoami` takes the same three for a session that says them later). The node keeps each as
  one short clean line (120, 100 and 240 characters) beside the session's identity. A purpose the board screens out (a credential or a
  private term) is recorded as the agent type and the brief says so; a record that fails is said in the brief too, and the rest of the
  brief is still given. A branch or worktree claim the subagent makes later replaces what the hook saw, because a claim is the
  authenticated statement of what it works on.
- **Shown.** `poolhouse-workspace roster` (also `status`, and `who` with no arguments) prints one line per live session: name, main or
  subagent of, working/idle/done (`done` only with `--all`; an ended session leaves the live list), purpose, branch, worktree and when it last
  acted; `--json` gives the rows. The text is one fenced block of untrusted data: a purpose is length-limited, a line, and has fence tags
  and chat markup neutralised, and it grants nothing.
- **In the brief.** The brief every worker receives lists the other live sessions with their purposes (twenty, then a count) and says
  how to reach one, so it never has to ask the lead.
- **Used by `send`.** `send TO TYPE TEXT` takes a session name or a room as before; otherwise `TO` is a branch, a worktree path or a word
  (three letters or more) of a purpose, tried in that order, and it must fit exactly one live session. `send --to-branch BRANCH TYPE TEXT`
  takes a branch only. Several fits, or none, is an exit status 3 error that lists the candidates (fenced, ten of them and a count), never a
  guess; the reply names the session it reached (`matched`). A session can word its own purpose to fit a message meant for another, so
  an ambiguous word is refused rather than delivered, and the reply shows where the message went.
