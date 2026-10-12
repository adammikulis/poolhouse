# Reviewing the backlog

`scripts/review-backlog` has a reviewer model read the unlanded work and write one report per head SHA, so a person or a
landing-level agent reads only what the report flags (AGENTS.md, "Reviewing the backlog").

    scripts/review-backlog --local [--model ID] [--limit N]
    scripts/review-backlog --cloud MODEL [--limit N]
    scripts/review-backlog --dry-run [--local | --cloud MODEL]
    scripts/review-backlog --list

`--cap-bytes N` (default 200000) and `--repo PATH` adjust the diff cap and the repository read.

## What is read

Read-only, from this repository and this device's board:

- every local branch with a patch the development branch lacks (`git cherry`);
- every landing request in `needs-review` (`poolhouse-workspace land-queue`), resolved to its full SHA;
- every open pull request (`gh pr list`, `gh pr diff`; `gh auth status` first). When `gh` is signed out the pull requests
  are skipped with one line and the rest runs.

Work is keyed by head SHA: a branch that is also a landing request and a pull request is one item. A SHA with a report in
the reports directory is skipped. `--limit` bounds the reviews per run (default 5), in the order landing requests,
branches, pull requests.

## What the reviewer gets

A detached copy of the diff in a scratch directory that is removed afterwards (`review-input.md`), also sent on standard
input. The branch name, the pull request title and body, the commit messages and the diff are each wrapped as untrusted
data (`poolhouse.guard.untrusted.fenced`), so text in them cannot close its fence or give an order. The reviewer runs with
no tools and an environment that holds only `PATH`, `HOME`, `USER`, `LANG`, `TMPDIR`, `TERM`, `POOLHOUSE_HOME`,
`POOLHOUSE_CACHE` and `POOLHOUSE_WORKSPACE_AGENT`: no token, key or login variable. The tool writes only the report and
the scratch copy; it never writes to a checkout.

A diff over the cap is not sent to a model: its report says `too large, flag for a person`.

## Backends

- `--local` runs `poolhouse-claude MODEL --role read-only` on the scratch directory. `poolhouse-claude` takes the model
  lease from the broker, which admits it against memory and queues behind other work, and the tool reviews one item at a
  time, so the device's compute holds one piece of work (AGENTS.md, section 1). Before each local call it prints the
  first line of `poolhouse-serve status`. Without `--model` the tool takes the broker's pick of the newest downloaded
  Qwen coder that fits (`localmodel.choose` with the coding selection) and says which in one line; with none
  downloaded it stops and prints the command that fetches one. No server is started by hand.
- `--cloud MODEL` runs the Claude Code harness (`claude --model MODEL -p`, the installed one or the Claude Agent SDK's
  bundled one) under the login that harness already holds. The tool adds no credential path and makes no network call of
  its own.
- `--local` is refused for a diff that touches a path naming authorization, authority, grant, guard, hook or red-team
  code. The refusal names `--cloud`, writes no report and the run exits 2 after the other items.

## The report

`reviews/<sha>.md` under the state root (`$POOLHOUSE_HOME/reviews/`, by default `~/.poolhouse/reviews/`):

    - verdict: flag | clear
    - source: branch, landing request and pull request that name the SHA
    - model, backend, date and diff size

then the reviewer's findings, each `file:line: the failure scenario`. The reviewer's first line is `VERDICT: flag` or
`VERDICT: clear`. A reply with no verdict line, or a `clear` that lists findings, is recorded as `flag`.

A report is advice. A local model is not landing-level, so a report is never the independent accept: the tool never runs
`land-review`, never posts to GitHub, never pushes. A flagged report goes to a person or a landing-level agent; a branch
gets one review pass and at most two fix-and-recheck rounds, and a finding that survives them goes to the owner as a
decision.

## Running it on a schedule

No daemon. The lead's heartbeat (docs/heartbeat.md) or a cron entry runs

    scripts/review-backlog --local --limit 3

and reads the flagged reports from `scripts/review-backlog --list`. A run with nothing new prints nothing and exits 0. A
run that finds the broker busy waits in the broker's queue; a heartbeat never starts a second run beside one still going.
