# Claude Code instructions

[AGENTS.md](../AGENTS.md) routes to the repository policy; everything here applies in addition to it and only covers
Claude Code. [glossary.md](glossary.md) is the vocabulary and is binding: use its words, never the ones it bans.

## Session start and names

The `SessionStart` hook registers the lead, announces it and puts the inbox in its context; when the hook output is absent, the lead
runs `poolhouse-workspace inbox` before any other work.

## Hooks and settings

`.claude/settings.json` wires `scripts/hooks/claude-bash-guard` before the Bash and Monitor tools (both run a shell command),
`scripts/hooks/claude-edit-guard` before the edit tools and `scripts/hooks/claude-tool-guard` before every tool, which leaves
the first two their tools and reads the rest by the shape of their input (a path is checked as the Read tool's is, a `command` as
Bash's is, and a subagent's command in a tool other than Bash and Monitor is refused); and `claude-session-start`,
`claude-subagent-start` and `claude-subagent-stop` around sessions and subagents. `POOLHOUSE_GUARD=off` turns the soft rules of
the Bash and edit guards off; the hard rules and the tool guard stay. The `PostToolUse` hook `claude-tool-milestones` posts a notice to the lead for each subagent's tool-call count; it informs
and never limits (docs/claude-subagents.md, "Tool-call milestones").

- The Bash guard refuses the command-line forms of the rules in AGENTS.md: `git add -A`, `.` and `-u`, `git commit -a`, `llama-server`
  by hand, `poolhouse-serve up` flags that skip the lease, a push to `main` without the person's approval (docs/agent-policy.md,
  section 1, "The owner's `main`"; `POOLHOUSE_PUSH_MAIN=yes` opens nothing), and a force push, a remote ref deletion, `--all` or `--tags`
  pushes, past any `NAME=value` written in front of the command.
- The edit guard refuses, at the moment it is written, the writes docs/agent-policy.md, section 5, "The gates" lists. It reads the two
  limits from the checkers.
- A subagent's Bash and Monitor commands are stamped in the hook: its own board name and native id are set, and the names by
  which the harness names the session that started it (`CLAUDE_CODE_SESSION_ID`, `POOLHOUSE_SESSION_ID`,
  `POOLHOUSE_SESSION_HARNESS`, `CODEX_THREAD_ID`) are removed, so a program that drops the subagent's own marker has no
  identity instead of the lead's. A command of a subagent that sets one of those names to a value, another session's name or a
  token is refused (an empty value, and the name inside an argument such as a `grep` pattern, are not an assignment).
- The guards read the text of a command, so they stop the forms their tests list and no more: expansions, `eval`, base64, heredocs
  and a program an agent writes and runs are past them, and the lead's session id can be read from the process environment and the
  harness's files. The closure is the isolated launcher's sandbox (`docs/design-isolated-launcher.md`, L1 to L5; the agent
  sandbox of `docs/agent-sandbox.md` for a harness launched without it). A command or an input too long or too nested to read in
  bounded work is refused.
- Claude Code sets `CLAUDECODE` for every command it runs. `--allow-increase` on the budgets and the `pre-push` hook's agent
  restrictions key on it; a terminal sets nothing.

## Commit attribution

End git commit messages with the attribution trailer the session's system reminder gives, and end pull request descriptions with the
line it gives.

## Subagents and the Agent tool

The Agent tool's worktree isolation provides the worktree half of a subagent brief; the brief still names the branch. The SubagentStart
hook registers the subagent as its own identity under a board-assigned name and delivers it in the brief; SubagentStop records its done on the board
for the sessions subscribed to it (its parent is, by default) and retires it. A subagent prompt carries the workspace line from AGENTS.md and needs no invite or token. The
`.claude/agents/branch-worker.md` agent is the one-branch worker: it reads AGENTS.md and this file, announces, works in its worktree,
commits named files and reports. The harness confines it to that fresh tree, so `.claude/agents/branch-finisher.md` (no isolation) is
the one for a branch that already has a claimed sibling worktree.

A worker that has delivered its report is stopped at once with TaskStop, and the same goes for a worker that sends a report again. The
lead does not wait for it to exit and does not read a repeat report as new work. The branch and worktree stay; only the agent ends.

## Browsers

Never drive the owner's own browser through the `claude-in-chrome` tools to test this project's pages.
