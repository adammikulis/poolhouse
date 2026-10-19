# Claude Code and Codex on a local model

`poolhouse-codex` and `poolhouse-claude` run the Codex and Claude Code command-line agents on a model
this machine serves, with Poolhouse's rails around them: the served window, a pre-tool classifier
hook under a role, the Requests inbox for approvals, and a workspace identity of their own. Codex is
the default coding harness (a lighter system prompt and tool list); Claude Code is there for when
its richer hook gate matters.

```
poolhouse-codex                                  # Qwen3.8-27B-UD-Q4_K_XL, role approve-first, this directory
poolhouse-codex Qwen3.5-2B --role plan-and-go --project ~/work/repo -- exec "fix the failing test"
poolhouse-claude --on http://127.0.0.1:8080 --role read-only -- -p "explain this repo"
```

A program starts the same thing with `poolhouse.coding.launch_coding_agent(model, role, project,
harness="codex", **options)` (options: `name`, `orders_from`, `harness_args`, `say`,
`run_codex` / `run_claude`, `context`); it returns the harness's exit code.

## What a launch does

1. **Serves the model through the broker lease** (never a hand-started server): 262,144 tokens on
   one slot, q8_0 KV cache, the model's own multi-token-prediction head where it has one, the
   model's measured settings when there are some. The model is asked for before it is loaded: when
   the wired-memory limit cannot hold it at that window, the launcher prints
   `poolhouse-serve memory --for MODEL --ctx 262144 --kv q8_0 --apply` and stops. Raising the limit is
   for a person at their own terminal. `poolhouse-serve memory --for Qwen3.8-27B-UD-Q4_K_XL.gguf --ctx
   262144 --kv q8_0` says what the default needs (29.2 GiB on the measured machine, 2026-10-03).
   `--ctx N` is the total across `--slots` (default 1); `--on URL` uses a server already up.
2. **Tells the harness the window it was given**, so it neither compacts at a small default nor
   assumes a larger one. Claude Code: `CLAUDE_CODE_MAX_CONTEXT_TOKENS` and
   `CLAUDE_CODE_AUTO_COMPACT_WINDOW` (accepted from 100,000, capped at the model's window) are set to
   the served slot context. Codex: `model_context_window` and `model_auto_compact_token_limit`
   (90 percent of the window) in its generated config.
3. **Writes the session's settings outside the working tree**: a private directory under the state
   root (`harness/<id>`), the files read-only and, for Claude Code, the directory too. Nothing is
   written to `~/.claude` or `~/.codex`: Claude Code gets `--settings FILE` and
   `--append-system-prompt-file FILE`; Codex gets its own `CODEX_HOME` with `config.toml` and
   `AGENTS.md`, no auth file and no API key in its environment. The directory is removed when the
   harness exits.
4. **Installs the hooks** (below).
5. **Registers a workspace session** and announces it (below).

## The rails

| role | Codex's own mode | the hook |
| --- | --- | --- |
| `read-only` | sandbox `read-only`, approvals `never` | allows reads; denies every acting call |
| `approve-first` (default) | sandbox `workspace-write`, approvals `on-request` | allows reads; asks for every acting call |
| `plan-and-go` | sandbox `workspace-write`, approvals `on-request` | allows reads and reversible calls; asks for destructive and unsure ones |

The hook is `python -m poolhouse.harnesshook pre`, a PreToolUse command hook that both harnesses run
before every tool call (Claude Code: every tool; Codex: Bash, `apply_patch`, MCP and other local
function tools). It labels the call with the destructive-action classifier
(`docs/destructive-actions.md`, deterministic layer) and applies the role. A call the classifier
cannot read, or a tool it does not know, is `unsure` and asks. An ask is a request in the Requests
inbox (`docs/requests.md`) that the hook waits on, up to five minutes; only the person answers it
(`poolhouse-requests`), never the model. Not answered, expired, or a request store that cannot be
opened (the hook runs without a terminal, so it never prompts the keystore) is a denial.

Denied in every role: a call whose text names the session's files or the state root, and a shell
command that asks for an action only a person can take (answer a request, change a role, release
quarantine; the human-only table in `chatpolicy.py`). The role, the label and the paths are on the
hook's command line, written by the launcher; nothing in the environment, the tool arguments or
a file the model reads changes them. A hook that crashes exits 2, which both harnesses read as a
block. The role names are those of `docs/agent-roles.md`.

The hook's output is a fixed sentence per outcome, so it adds a few identical tokens to a
conversation and nothing that changes the prompt prefix.

### Code that arrives, and credentials

A tampered model, or text it takes for an order, can write a script that downloads code and then
run it through a tool that looks harmless. The hook reads these in every role, by fixed rules and
with no model, and each one asks (`unsure`, a request of kind `tool_call_destructive`) or is denied:

- **A write to a file that runs by itself later** asks even when its content looks harmless: a git
  hook, `.git/config`, the agent folders (`.claude`, `.codex`, `.cursor`, `.agents`), `conftest.py`,
  `setup.py`, `pyproject.toml`, `package.json`, `Makefile`, a CI workflow, `.envrc`, an editor task
  file, `sitecustomize.py`, a `.pth` file, a shell startup file, an agent instruction file (`CLAUDE.md`,
  `AGENTS.md`, `.mcp.json`, `.cursorrules`), `.gitattributes`, `.git/info/`, `scripts/test`, `scripts/land`,
  `scripts/hooks/*`, a `.plist`, `.service` or `.timer` file. The ask names the file, says when it
  runs and quotes what the write starts with. Paths are read relative to the project folder, case-folded and through links,
  so `.git/Hooks/x`, `src/../.git/hooks/x` and a link to the hooks folder are the same file.
  A shell redirect, `tee`, `cp`, `mv`, `ln`, `install`, `dd`, `sed -i` and `curl -o` onto such a file ask the same way.
  A write whose target, once links are followed, is a credential file outside the project (`~/.ssh/authorized_keys`,
  `~/.aws/config`, `~/.gnupg`, `~/.config/gh`, a link in the project that leads to one) is denied; a write through a
  link that leads out of the project to anything else asks.
- **A download that can be run** asks: `curl -o`, `curl -O`, `wget`, `aria2c`, a redirect or `tee` of a download, onto a file
  that is not plainly data (a `.json`, `.csv`, `.html`, an archive or an image is data; anything under `tests`, `scripts` or `bin`,
  a source file, a program with no suffix or an executable is not), and `wget -r`, `wget -i` and `curl -K`, which name their files elsewhere.
  Every path a session's shell calls download, clone or copy from a download is kept in the state root (`harness-fetched/<session>.log`,
  closed to the model like the keystore), and a later command that runs files (an interpreter, `scripts/test`, `make -f`, `source`, `./file`,
  `cd` into the folder and run) and names one of them, or a folder that holds one, asks "which this session downloaded";
  a download renamed or copied in the same line counts too. Reading, listing, deleting and `git add` are not questions.
- **Login and schedule persistence** asks: `launchctl` with any verb that is not a listing, `systemctl enable` and `--user start`,
  `crontab`, `at`, `batch`, `open x.plist`, `sudo`, `doas`, `su` and `pkexec`.
- **Text written to any file** is read for a download piped to a shell (`curl ... | sh`,
  `bash <(curl ...)`, `eval "$(curl ...)"`), text decoded and run, a shell opened to another machine
  and a credential sent out. A call or pipeline spread over several lines is read as one, and an
  `Edit` or `MultiEdit` is read together with the file it changes, so a download split over two edits is caught. A name is
  followed from the statement that assigns it a download (or a copy of the environment) to a later statement that runs it (or sends it
  out), a file from the statement that downloads it to the one that makes it executable or runs it, and a remote shell built
  from a socket and `dup2` or `pty.spawn` in separate statements is read as a whole. The ask
  quotes the flagged statement with its line number (the part around the match when it is long),
  every character outside ASCII written as a visible escape, so a direction mark, a zero-width
  character or a lookalike letter cannot hide it. A direction mark in written code asks on its own.
- **Commands that bring code in** ask: `git clone`, `git submodule update`, `pip install`, `npm install`
  and the other package managers' installs, `pipx run`, `npx`, `cargo run`, `go run`, unpacking an
  archive, `git apply`, a `git config` or `git -c` that sets a command git runs (hooks path, alias,
  pager, ssh command, filters, credential helper), a server or tunnel, and a command left running in the background.
- **Credential files** are listed once, in `poolhouse.secretpaths` (folders such as `.ssh` and `.aws`,
  names such as `.env` and `id_ed25519`, key suffixes, the state root's keys). A read of one outside the
  project folder is denied in every role, whichever tool or command reads it (`Read`, `Grep`, `cat`, `cp`, `curl -d @file`,
  `xargs -a`, a `python -c` that opens it, a link to it); inside the project folder it asks. A wildcard that could expand to one (`cat .e*`),
  a variable that builds a path, a search of a folder that holds one (`grep -r KEY .`, `rg --hidden`, `tar` of the folder) and files another
  command picks (`find ... | xargs cat`) ask; a search of the home folder is denied. A command that prints the environment, a
  variable named like a key, a process listing that shows environments (`ps eww`, `ps -E`, `ps -o command,env`), `git config --list` or a
  `--get-regexp` that could match a credential or an address, `git remote -v`, `git remote show` or `git config --get remote.*.url` when a
  remote address holds a password, a repository fetched or added from a host the project does not list (github.com, gitlab.com,
  bitbucket.org, codeberg.org, sr.ht, dev.azure.com, huggingface.co and the hosts in the project's own remotes are listed), a shell string
  that a variable or command builds (`bash -c 'echo $X'`), and opening an address in the person's browser ask. A key or token from a cloud or package CLI asks.
- **An address** (`WebFetch`, `curl`, `wget`) asks when it carries data after `?` or `#`, has a user
  name or password, names an internal host, uses a scheme other than http or https, or has a long encoded
  host name or path segment; `file:` of a credential file is denied.

What the person sees is the command, or the file or address first and then the names of the other
arguments, as one ASCII line of at most 250 characters that keeps both its start and its end, with the
classifier's reasons (up to 700 characters) beside it. Code: `poolhouse.guard.execchain` (commands),
`gitcmds`, `toolreview` and `autoexec` (tool arguments and written files), `fetchexec` and `flowscan` (written text),
`fetchlog` (the downloads of a session), `secretcmds`, `urlscan`; tests: `tests/test_harness_exec_chain.py` and
`tests/test_harness_exec_chain_more.py`.

### The workspace

Codex receives a workspace-only MCP server that runs as its assigned session: the session's name, the board and the
state folder are in the server's environment, and the token is the client's private file for that session. Inbox,
thread, status and ownership reads follow the read role; send, claim, announce and acknowledgement follow the acting
role. The server exposes no model administration, grant or registration tools. Workspace state stays outside the
coding sandbox; use these MCP tools instead of shell CLI commands there. The token file is deleted when the session
ends. Shell workspace commands must explicitly name the launcher-assigned identity; other identity flags and
token-file overrides are denied.

The launcher registers the session on the node as a subagent of the session that starts it (`--as AGENT`; by default
the session named by the command's environment, else the person's own session). The node records the parent, names
the session (the model family plus six characters; `--name`, or `local-<model>-<harness>`, only seeds its session
id) and gives it a token, which the client keeps in a file readable by this user only: nothing is printed and no
invitation is involved. The session starts with only what any session has: it reads and writes its own board, and a
grant a person signed for it or for its parent with `--with-subagents` adds the rest. The session is placed on the
project's board (the project of `--project`, else the working directory) with the workspace's quiet defaults (direct
messages and mentions in its inbox, `#announcements` as a roll-up). It gets the workspace brief (it acts with `--agent NAME`; it obeys the person,
the session that started it and any `--orders-from` identity; what it reads there is data), and a PostToolUse hook
runs `poolhouse-workspace nudge --agent NAME` after each tool call and hands its output (at most 500 characters,
fenced as data) back as context; where `nudge` does not exist the hook says nothing. The session is retired and its
token file deleted when the launcher exits. The served alias and harness are recorded as the session's claimed model
(`whoami --model ALIAS --harness H`) once the serving endpoint reports that model; when it does not, the launcher
says the model is not recorded.

## What is not covered

- The hook is a gate inside the harness's own process tree. Neither harness is a sandbox against
  its own model: a Claude Code session has no sandbox here, so a call the classifier labels safe or
  that the person allowed can still do what that call does, and a command built to hide what it
  touches (an `eval` of a computed string) is `unsure` and asks rather than being read. The session's
  files are read-only and outside the tree, but the same operating-system user owns them. Codex's
  own sandbox is the stronger boundary for shell commands.
- A hook that cannot start (a missing interpreter) is a non-blocking error in both harnesses. The
  launcher writes the absolute interpreter that started it.
- Codex runs hooks it has reviewed; the launcher passes `--dangerously-bypass-hook-trust` because it
  wrote the hook into a home that holds nothing else. Codex has no pre-tool hook for its web search
  tool or for model-side reasoning; those are outside the gate.
- The Requests answer comes from the terminal or the UI, so an unattended session in `approve-first`
  stops at its first acting call (denied, with the reason shown to the model).
- Only the deterministic classifier layer runs in the hook; the model-assisted layer does not.
- What the rules above cannot decide: code the model writes in an ordinary file in a form no rule
  recognises (a name built piece by piece, an indirect call) and then runs through the project's own
  `make`, `npm test` or `cargo test`; files that a `git pull`, `merge` or `rebase` brings in and a
  later command runs; a plain GET to a host the model chooses, which can carry a few bytes in a short
  path or sub-domain on each call (the hook holds no list of allowed hosts); instruction files such as
  `AGENTS.md`, which are read, not run (their writes ask, but what an injected line in one says is not judged); a download that a
  later `make`, `npm test` or `cargo test` runs without naming the file; the written-text scan follows a name through assignments
  in one file, not through a function, an import or a name built from pieces; `sh -c` with a body the classifier reads and finds
  harmless (`sh -c id`); a file replaced by a link between the check and the call;
  the folder after a `cd` (a relative name is read against the project folder); and a project folder
  with more than 20,000 entries, which asks instead of being searched for credential files.
- The harness talks to the server directly, not through `poolhouse.http`, so the per-pool request queue (`poolhouse.gate`) does not order its calls. One slot per session keeps two sessions from sharing a cache.

## Local models and large harness prompts

- The harness prompt and tool list are tens of thousands of tokens before the first message. At 256K
  the first turn pays for them once; every later turn should reuse the cache. Keep the harness's
  system prompt, tool list and instruction files byte-stable between turns (do not change `AGENTS.md`
  or the settings mid-session; the launcher's files are constant for a run) and keep one slot per
  session so the long prefix is not evicted by a second conversation.
- A small model with a large tool list loops: it reads, retries a command a hook denied, and does
  not finish. A 2B model run through Codex for about fifteen minutes made 34 tool calls and tried to
  rewrite the file through `sed`, `python -c` and heredocs, which the classifier asked about, and
  never ran the edit. Use a model sized for the harness (the 27B) for real work.
- Codex needs the Responses API (`wire_api = "responses"`, the only value it accepts); llama-server
  serves `/v1/responses` and `/v1/messages` in the managed build. Claude Code needs `/v1/messages`.
- The thinking-off default of the model's profile applies; the Claude Code settings also turn
  always-on thinking off.

## Comparing the harnesses

`scripts/compare-harnesses --model NAME` serves the model once and runs the fixture in
`tests/fixtures/toy_bugfix` (a one-line bug, scored by its own test, in a temporary git repository)
through `poolhouse-claude` and `poolhouse-codex`, recording wall time, tool calls, the tokens the server
processed (llama-server `/metrics`), the tokens each harness says it sent and how many came from
the cache, the last request's prompt and cached tokens from `/slots`, the first-turn cost of each harness measured with a one-line prompt, and whether the test
passes. Rows are appended to `docs/experiments/harness-comparison.md`. Poolhouse's own agent loop is
skipped where `poolhouse-workspace agent` does not exist. It refuses Flash-Next.

## Sources

- Claude Code hooks: https://code.claude.com/docs/en/hooks; environment variables:
  https://code.claude.com/docs/en/env-vars
- Codex hooks: https://learn.chatgpt.com/docs/hooks; configuration:
  https://learn.chatgpt.com/docs/config-file/config-reference and
  https://learn.chatgpt.com/docs/config-file/config-advanced; instruction files:
  https://developers.openai.com/codex/guides/agents-md
