# Lead agents installing login units

Status: design only; nothing in this document is built. Decided by the owner on 2026-10-16: a lead agent may run
`install` and `rollback` of `python -m poolhouse.fleet.autostart` for the `pool-daemon` and `runtime-ensure` roles. Read
against development `34e5e3aef` (the files it cites are unchanged since `f803ba49f`); no autostart command was run to write it. An independent attack-first review of the first
draft (2026-10-16) is answered in "Review round 1" and its second round, the last fix round, in "Review round 2"; a finding
that survives round 2 is the owner's decision. The attack table is the author's pass and does not count: AGENTS.md section 1
still requires a different agent to try to break this revision before it is built, and section 4 requires that agent's
exact-SHA `land-review` accept before the build is pushed.

## What changes

Today `prepare` is open, `install` and `rollback` refuse every agent marker and any process without a terminal
(`fleet/autostart_apply.py:198,250`, `person.require_person`), and the person types the manifest id
(`sentinel/human.mint`). After this change:

| Command | Person at a terminal | Lead agent, gate `autostart.install` delegated | Everyone else |
| --- | --- | --- | --- |
| `prepare` | open | open; the `poolhouse autostart prepare --agent NAME` verb stages the two fixed roles with the launchers in the state bin directory and takes no `--launchers`, `--slots`, `--label` or `--report`; it runs Admission steps 1 and 3 first | open |
| `install --manifest PATH --digest SHA` | as today: types the manifest id | `poolhouse autostart install --agent NAME`; no prompt; user scope, the two roles, the fixed argv and launchers, nothing else | refused |
| `rollback` | as today | `poolhouse autostart rollback --agent NAME`; only what a lead agent installed, restored from validated bytes | refused |
| `status`, `verify` | read-only | read-only | read-only |
| `--system`, role `llama-cpp-ensure`, any other argv | as today | refused | refused |
| `poolhouse-pool join --persist`, the page's "start at logon" (today `fleet.autostart.install`, F15) | the `pool-daemon` role through the same admission, no typed id | refused with the sentence naming the verbs above | refused |
| `poolhouse-traind --persist` | removed (F15) | removed | removed |

A lead agent is a session name that the node answers for as a main session: a token on the board, no parent, not retired;
never a `parent/name` identity and never an unnamed process. `authority.require` cannot tell it from its own subagent,
because it reads only the environment (`authority.py:201-218`) and a subagent's shell inherits its lead's variables;
`workspace_lead` checks that a token file exists with no `.parent` file beside it (`authority.py:195-199`) and never asks
the node. So the verbs take `--agent NAME`, ask the node (Admission step 4) and run under the Bash guard's subagent rule
(Wiring). The delegated path prompts for nothing, asks for no password and calls no `sudo`, `osascript` or `pkexec`.

## The gate

`autostart.install`, group `autostart`, added to `authority.GATES` with this line in `authority.MEANS`: "install or roll
back this device's pool daemon and runtime update at login, in your own login items". One gate covers `install` and
`rollback`: undoing an agent's own install is the same trust and the narrower direction.

| Preset | State | Why |
| --- | --- | --- |
| `dev` (default) | `delegated` | the default of every gate not set |
| `prod` | `person` | `PROD_DELEGATED` keeps only `workspace.setup`, `workspace.model`, `workspace.agents`; a new gate is a person's under `prod` with no code |

`person` to `delegated` is a person's alone, at a terminal, after typing `yes` to the `MEANS` sentence
(`authority.set_state`, `_ask_to_loosen`). `POOLHOUSE_AUTHORITY_FLOOR=person` reads it as `person`; the test suite sets
it, so every delegated test clears it first. A `person` gate sends a marked process to `require_person`, which refuses it
exactly as today.

## Admission order

An agent-marked `install` or `rollback` does these in order and stops at the first refusal. `prepare` runs steps 1 and 3 when
the process is agent-marked, records `--agent NAME` as the preparer and takes no gate. Nothing before the pending row (step 6)
writes anything except the lock file and a refusal row (Audit); a refusal at any step appends that row.

1. Drop the variables that move the state root or the home (`runtime_trust.ignore_redirects`: `POOLHOUSE_HOME`,
   `POOLHOUSE_CACHE` and the other overrides), as the three deploy commands do, and `HF_HOME`. The `HF_HOME` drop is this
   path's own list; `ignore_redirects` is unchanged. `prepare` drops them too: it builds the staged environment with
   `environment_for` (`autostart_prepare.py:87-94`), and an `HF_HOME` staged from the caller's environment is not what
   `install` re-derives after its own drop, so every manifest an agent prepared with `HF_HOME` set would fail the Environment
   rule. The delegated unit therefore carries `HF_HOME` as `~/.cache/huggingface` under the account home even where the
   owner's shell sets another; a person's install carries the owner's value. That is a known difference: a model cache the
   owner moved elsewhere is not read by a delegated unit, and a person who wants it installs as today.
2. Refuse `--system` ("a system-wide install is a person's").
3. Require `real_machine.refusal() == ""` (the installed copy under the account's own `~/.poolhouse/runtimes`, not a
   checkout, a worktree or a copy) and `Path.home()` resolved equal to the account home from the password database
   (`HOME` is not redirected). Both are `Seams` fields so tests can drive them.
4. Pass the gate. When the gate reads `person` (`prod`, `POOLHOUSE_AUTHORITY_FLOOR=person`, a registry set so), a marked
   process goes to `require_person` and is refused with today's sentence before anything else is asked. Otherwise:
   1. `NAME` is `--agent`, required while the process is agent-marked. The sandbox removes `POOLHOUSE_WORKSPACE_AGENT` from
      a command's environment (`SCRUBBED_ENV`, `scripts/agent_sandbox_profile.py:21-22`), and `poolhouse runtime` already takes
      `--agent` for the same reason (`runtime_cli.py:319`). When the environment still holds `POOLHOUSE_WORKSPACE_AGENT`
      it must equal `--agent`; a different value is refused ("you named one session and run as another"), which is how a
      subagent whose shell carries its lead's name is stopped when it names itself.
   2. `authority.admit("autostart.install", "install autostart units pool-daemon,runtime-ensure <digest12>", env=...)` with
      `AGENT_ENV` set to `NAME` in the mapping it is given. `admit` is `authority.require` without the `_log` call: it makes
      the same checks, returns `delegated` only for a lead agent while the gate is delegated, and writes no row;
      `require` calls it and appends its `authority.use` row as before, so every other gate is unchanged.
   3. `autostart_agent.live_lead(NAME)` asks the node, as `runtime_deploy.acting` does (`runtime_deploy.py:111-119`:
      `session.connect(agent=NAME)` then `whoami()`): the node answers for the token, the record has no parent, the session
      is not retired, and `NAME` holds no `/`. A token file left on disk by an ended or revoked session, a token the node
      does not know and an unreachable node are all refused (the check fails closed). The cost is one node call. The verb
      runs outside the sandbox, so it can read the token under `node/client`, which a sandboxed command cannot.
5. Take `apply.lock` without waiting (a second call is refused as today), so the id, rate and overwrite checks below and the
   apply cannot interleave with another install or rollback. Read the manifest once (at most 256 KiB), parse it, and hold
   the parsed value and the staged unit bytes in memory for the rest of the call.
6. Run `problems()` as it is today, then the agent rules below. A refusal appends a refusal row. When every rule has passed,
   append the pending row (Audit) and apply.

### Agent rules (`autostart_agent.py`, new)

| Rule | Check |
| --- | --- |
| Roles | every role is in `AGENT_ROLES = ("pool-daemon", "runtime-ensure")`; scope is `user` |
| Location | the manifest path, resolved, equals `staging_root() / <manifest id> / manifest.json` with no symbolic link in the chain; the id is the directory name |
| Digest | `sha256` of the manifest's canonical JSON (sorted keys, no spaces) equals `--digest`, the value `prepare` printed; required here, checked when a person passes it. The digest has no key, so whoever can write the manifest can compute it: it detects a manifest changed after `prepare` printed it, and the re-render (Exact unit) is what refuses a forged one |
| Id and times | `id` is not in the ledger's `used` list (the last 200 ids) and `backups/<id>` does not exist. `created` is at most one hour old (`AGENT_MAX_AGE_S = 3600`; the person path keeps 24 hours) and later than the `at` of any install the ledger holds for the same role. `id`, `created` and `expires` are written by whoever wrote the manifest, so these checks bound an honestly stale manifest, not a forged one |
| Exact argv | `pool-daemon` is `[launcher, "--port=8770"]`, `runtime-ensure` is `[launcher, "runtime", "ensure"]`; no `--slots`, `--label` or `--report` |
| Launcher path | `argv[0]` equals `runtime_launchers.directory() / <name>` (`<name>.exe` on Windows), compared with that expected path and not with a path derived from `argv[0]`'s own directory, with no symbolic link in its chain (`runtime.plain`) |
| Exact launcher | POSIX: the file equals, byte for byte, the text `runtime.write_launcher` writes for that name, its entry point from `runtime_launchers.entrypoints(chosen)` and `runtime.selected()`, with `follow_selection=True` (what `runtime_deploy.switch` passes for the state bin directory); a launcher that differs is refused with "run poolhouse runtime ensure". Windows: `windows_private.launcher_problem` returns an empty string and the file's sha256 equals that of `chosen.python.parent / <name>.exe`, the file `runtime_launchers.install` copies |
| Environment | `role.environment` equals `environment_for(platform)` computed after step 1, so `HF_HOME` is `~/.cache/huggingface` under the account home whatever the caller's environment held; no other key. `prepare` stages it the same way |
| Exact unit | the staged bytes equal what `render()` returns for the re-derived role (already so for every path) |
| Exact destination | `allowed_dir(platform, "user") / unit.name`, no symlink in the chain, no non-regular file at the target |
| Overwrite | the destination is absent, or the ledger holds an entry for the role with `via` equal to `delegated` and the destination's sha256 equals that entry's unit sha256. Anything else is "a unit this tooling did not write, or a person installed, is in the way; a person installs". An entry with no `via` is a person's. `via` is provenance by record, not authentication: `ledger.json` is plain JSON, and what stops an edit of it is the write denial on `autostart` and A3 |
| Loaded | when the service manager has the role's label loaded, it was loaded from the fixed destination. `Backend.loaded_from(role)` returns it: macOS the `path` line of `launchctl print gui/<uid>/<label>`, Linux `systemctl --user show --property=FragmentPath --value <unit>`, Windows the command and arguments of the registered task (`schtasks /Query /TN <label> /XML`), which must equal the recorded role's `argv`. A different path, or none the manager reports for a label it has loaded, means a person's job holds the label: "a service of this name that this tooling did not load is running; a person installs". Without this rule the `unload` that every install and rollback runs first (`autostart_apply.py:226`, `Backend.unload`) boots out a person's job by label alone |
| Backup | `backups/<id>` is created with `mkdir(exist_ok=False)` and mode 0700; each file in it is created with `O_EXCL` and `O_NOFOLLOW` |
| Rate | at most three delegated installs and three delegated rollbacks whose pending row `at` is within the last 3600 s (`AGENT_WINDOW_S`) |

The rate count reads `authority-audit.jsonl` from its end, in blocks, and counts rows with `event` `autostart.install` or
`autostart.rollback`, `by` `delegated` and `result` `pending` (one per attempt that passed the rules; the completion row is
not counted again). Rows of other gates (`authority.use`) are read past, not counted, and cannot push a counted row out of
range. The scan stops at the second consecutive row older than the window, not the first, and counts a row dated later than
now, so a row written under a slow or stepped-back clock neither hides the rows before it nor lets the count fall under the
limit. A clock set far ahead can hold the agent path closed until the date passes and never opens it; `status --json`
shows the count and the `at` of the oldest counted row. The cost is the rows of the last hour and one block.

`status` and `verify` are unchanged: they compare the ledger and the files, report `current`, `stale`, `missing`,
`drifted` or `not-prepared`, and never repair. A `drifted` unit is not installed over by an agent (rule Overwrite). Their
launcher check is `launcher_problem` (the head of the file and its runtime root), not the byte comparison above.

### What the delegated install does

It writes the role's unit files into `~/Library/LaunchAgents` (macOS), `~/.config/systemd/user` (Linux) or registers the
user task named by the role label (Windows), and runs only `Backend.load`, `unload`, `probe` and `loaded_from` for user scope
(`launchctl bootstrap|bootout|print gui/<uid>`, `systemctl --user ...`, `loginctl enable-linger`, `schtasks /Create|/Delete|/Query`).
On Linux it records the account's linger state in the ledger entry as `linger_before`. The value is the earliest one: when
the ledger already holds an entry with `via` equal to `delegated` for either role, the install copies that entry's
`linger_before` and reads nothing, because the first delegated install turned linger on and a later read would say `yes`;
otherwise it reads `loginctl show-user <uid> --property=Linger` (a failed read counts as `yes`). It creates the working
directory, log directory and backup folder under the state root. The ledger entry of each role gains `via` (`delegated`),
`agent` and `linger_before`. A `failed` install, which puts the previous units back, also runs `loginctl disable-linger`
under the same rule as a rollback (below), because `_undo` never undoes the `enable-linger` that `_apply` ran
(`autostart_apply.py:231`). The admission steps read the selected runtime with `runtime.selected()` and
`runtime_launchers.entrypoints`, which run that runtime's interpreter under `-I`. It does nothing else.

### Rollback by an agent

`rollback` runs Admission steps 1 to 4 with the action "roll back autostart units", then takes the lock of step 5. It judges
every role first and changes nothing until all of them are judged; then it appends the pending row and undoes the roles
that passed. For each role:

1. `ledger["last"]` names an install whose ledger entries have `via` equal to `delegated`, and the role is in its
   `remaining` list (below).
2. The label, unit names and destinations of the role are re-derived from the fixed role table; any difference from the
   ledger refuses.
3. For each unit, the destination is absent, or a regular file whose sha256 equals the recorded unit sha256, and the
   Loaded rule holds. A destination that holds anything else was edited after the install ("a unit was changed after it was
   installed; a person rolls back"), and a label loaded from another path is a person's job (the Loaded sentence): that role
   is refused and nothing of it is booted out, removed or overwritten.
4. When the role has a `previous` record, the agent restores it only if that record has `via` equal to `delegated`, equals
   `render()` of the fixed role, passes the agent rules of the running build, and the backup bytes equal that render. The
   write goes through a descriptor on the fixed destination. A `previous` record that fails any of these refuses the role
   the same way: the agent restores nothing, and a person's `rollback` restores it from the backup. With no `previous`, the
   role is unloaded and its fixed unit files are removed.
5. On Linux user scope, when the removed entry's `linger_before` is `no` (every delegated entry carries the earliest value,
   so the oldest record decides) and no role remains in the ledger after the rollback, whoever installed it, it runs
   `loginctl disable-linger`. A role that stays installed (a refused one, or a restored `previous`) keeps linger on.

**One of two roles refused.** A refused role stays installed and loaded exactly as it was, with its ledger entry untouched.
`ledger["last"]` gains `remaining`, the names of the roles not undone; it is cleared only when `remaining` is empty, and a
later rollback (an agent's, or a person's) acts on `remaining` alone. The command exits 2 and prints each role as `undone`
or `refused: <reason>`; the completion row carries `roles_undone` and `roles_refused`, with result `undone` when any role
was undone and `refused` when none was.

**A `previous` that fails.** The checks of step 4 run before anything is unloaded, so a failing `previous` leaves the role
installed and loaded under the install being rolled back. When the checks pass and the restore itself fails (the write, or
`Backend.load` of the previous unit), the agent writes the current install's unit again from the fixed render and loads
it, and reports "the previous units could not be restored; the current ones are back". If that also fails, the role is
reported `broken: <reason>` and `status` shows it as `drifted`. In every case the ledger keeps the current entry.

Backup `destination` and `name` fields are read as claims and compared, never used as paths. The person's `rollback` is
unchanged and leaves linger on.

### Audit

Rows go to `authority-audit.jsonl` through `authority.record(event, by="delegated", ...)`; a person's install keeps its
`by: person` row. A delegated attempt writes one of these:

- a **refusal row** (`result` `refused`, `reason`, and the rule that refused) at the first refused step, or
- once every rule has passed, a **pending row** (`result` `pending`, with an `attempt` of twelve hex digits) before
  anything is changed (before the backup, the unit write, any service manager call that changes state and the ledger
  write; for a rollback, after the roles are judged and before the first unload), and when the call returns a
  **completion row** with the same `attempt` and `result` `installed`, `failed` or `undone`.

```
{"at":…, "event":"autostart.install", "by":"delegated", "agent":"claude-6e1a2f", "gate":"autostart.install",
 "roles":["pool-daemon","runtime-ensure"], "manifest":"<id>", "digest":"<64 hex>", "scope":"user",
 "platform":"darwin", "attempt":"<12 hex>",
 "from":[{"role":…, "manifest":"<id> or empty", "units":["<sha256> or empty"]}],
 "to":[{"role":…, "manifest":"<id>", "units":["<sha256>"]}], "result":"pending|installed|failed|refused|undone", "reason":"…"}
```

There is no separate `authority.use` row for this gate: the pending row names the agent, the gate, the manifest and the digest
and is the record of the use, so an attempt refused by a rule writes one row, not two, and the log grows by at most the
rows the Rate rule and the refusal rule allow. An exception writing the pending row stops the install. `_apply` writes the
ledger and then the audit row (`autostart_apply.py:239-240`), so a call that dies between them leaves the install in the
ledger; the pending row written before it is then the record, and `status --json` lists every `attempt` that has a pending
row and no completion row as `unfinished`.

`failed` is an install that reached apply, did not answer its probe and put the previous units back. Refusals are capped
by reason, not by count: the first refusal of each distinct reason in an hour (a reason is the name of the rule or step that
refused, never the free text) always appends its row, whatever else was refused; a repeat of a reason already logged that
hour appends no row and increments that reason's entry in `autostart/refusals.json` (taken under `refusals.lock`). The rows
are bounded by the number of rules, so a flood of cheap refusals cannot fill the cap and hide a different one. The first
attempt after that hour appends one `autostart.refusals-suppressed` row with `since`, `until` and the count for each reason,
then clears the counter. A counter that is locked, unreadable or does not parse is not trusted to suppress anything: the
refusal appends its row. `status --json` shows the live counts, so every refusal is in the log or in the counter, and the
`via` and `agent` of each ledger entry.

## Wiring so an agent can use it

The Bash sandbox profile (`scripts/agent_sandbox_profile.py:29-58`) allows a sandboxed command to write
`workspace`, `logs`, `activity`, `requests`, `holds` and `hook-diagnostics` under the state root, and denies
`~/Library/LaunchAgents` and `~/.config/systemd`. `~/.poolhouse/autostart` (staging, ledger, backups, logs) and
`~/.poolhouse/bin` (the launchers) are in neither list, so a sandboxed `prepare` cannot write its staging directory today.

- `prepare`, `install` and `rollback` run outside the sandbox the way `DEPLOY_COMMANDS` do: a stable verb `poolhouse
  autostart prepare|install|rollback` calls `fleet.autostart.main`, and the profile's `excludedCommands` gains the three
  forms `poolhouse autostart prepare`, `poolhouse autostart install --manifest` and `poolhouse autostart rollback`.
  `prepare` prints the `install` form with `--manifest PATH --digest SHA --agent NAME`; the person's pasted line is that
  same line without `--agent`. `status` and `verify` stay inside the sandbox. Each verb takes `--agent NAME`, which reads
  `node/client`; the sandbox refuses that read (docs/agent-sandbox.md:199), one more reason the three run outside it.
- **Precondition, not a build step.** No `excludedCommands` entry for any of the three forms, `prepare` included, is added
  until slice 1 has driven the harness and recorded each form as VERIFIED. How the harness matches an excluded string
  against a command with further arguments or a compound tail is unverified (docs/agent-sandbox.md:195). For each form the
  run uses extra arguments and `; touch MARKER`, and records the matching it found. `prepare` is in the list because it is
  the verb that writes the staging directory, and it takes the same trailing-command risk as the other two. If a trailing
  command runs outside the sandbox, the three entries are an owner's decision (A6) and none of them is added.
- **The Bash guard names a subagent for these verbs.** `claude-bash-guard` blocks a subagent's command that omits its own
  `--agent` only when the command matches `WORKSPACE`, a `poolhouse-workspace` command
  (`scripts/hooks/claude-bash-guard:170-182`); `subagent_naming_another` (185-194) runs on every command and refuses
  `--agent`, a token file or the agent variable naming another session. A subagent's shell inherits its lead's
  `POOLHOUSE_WORKSPACE_AGENT`, so `poolhouse autostart install` without `--agent` is not intercepted today and acts as the
  lead. Slice 1 adds an `AUTOSTART_VERBS` pattern beside `WORKSPACE` (`poolhouse autostart prepare|install|rollback` and the
  `python -m poolhouse.fleet.autostart` form, with the same path prefixes), tested by `subagent_without_its_name`, so the
  subagent has to pass its own name; Admission step 4 then refuses it because the node records a parent for it. Only a
  harness without this hook (Codex, a local model) reaches step 4 alone (A7).
- `STATE_DENY_WRITE` (`scripts/agent_sandbox_profile.py:39-42`) gains `autostart` and `bin`, which nothing refuses today, and
  `authority.json` and `authority-audit.jsonl` as the sandbox and permission layer beneath the hook that already refuses
  the Edit and Write tools at those two files (`protectedwrites.py:24,115-120`), so `edit_rules` also refuses the file
  tools at all four. The profile as written does not refuse the file tools at `autostart` and `bin`.
- `src/poolhouse/fleet/autostart_agent.py` and `src/poolhouse/fleet/autostart_agent_rules.py` (the rule table:
  `AGENT_ROLES`, the exact argv, the limits) join `protectedwrites.REVIEWED` (`protectedwrites.py:25-30`), so a change to
  either needs the exact-SHA independent approval that `authority.py` needs; `tests/test_protected_writes.py` gains the two
  paths.

## Findings in the current code

Each is fixed by this design; none is fixed by this document.

| Id | Finding | Fix |
| --- | --- | --- |
| F1 | `--report=MODULE:CALLABLE` makes the daemon import any module and call the named function with no arguments (`fleet/device.py:120`, `fleet/daemon.py:782`); `_POOL_ARG` accepts any text up to 512 characters for it (`autostart_verify.py:32`) | the agent path accepts no `--report`; the person path restricts it to `poolhouse.` modules in the same slice |
| F2 | `install` records its audit row through `authority.record` without `by`, which writes `by: person` | the agent path passes `by="delegated"` and the agent name; `record` already lets a caller set `by` |
| F3 | `_undo` takes `destination`, `name` and `sha256` from `backups/<id>/index.json`, and the label and destinations from `ledger["last"]`; a tampered file makes rollback write or delete any path the account owns and boot out any label | agent rollback re-derives all of them from the fixed table and compares (Rollback by an agent) |
| F4 | `launcher_problem` checks the head of the launcher and an assigned `root`; the rest of the file may be any code | whole-file equality with the text the runtime tooling writes (Exact launcher) |
| F5 | the edit guard already refuses Edit and Write at `authority.json` and `authority-audit.jsonl` (`protectedwrites.py:24,115-120`); `STATE_DENY_WRITE`, the sandbox and permission layer, lists neither, and lists neither `~/.poolhouse/autostart` nor `~/.poolhouse/bin`, which nothing refuses | add the four to `STATE_DENY_WRITE`; add `autostart_agent.py` and its rule table to `protectedwrites.REVIEWED` (Wiring) |
| F6 | `schtasks /Create /XML` reads the staged XML after the hash was checked | install from a private copy of the verified bytes, made with `O_EXCL` and mode 0600 |
| F7 | `allowed_dir` and `environment_for` read `$HOME` and `POOLHOUSE_HOME`; an agent that sets them installs under a home it controls | Admission steps 1 and 3 |
| F8 | the file write follows a directory check made earlier in the call | repeat the realpath check, open the unit directory with `O_DIRECTORY\|O_NOFOLLOW`, write the temporary file and rename through that descriptor |
| F9 | `_argv_problem` compares the launcher's file name with a path built from `argv[0]`'s own directory (`autostart_verify.py:59-68`); `prepare --launchers` takes any absolute directory (`autostart_prepare.py:181-183`); `launcher_problem` returns an empty string for any owned regular `.exe` outside a checkout (`autostart_manifest.py:216-217`) | Launcher path and Exact launcher rules; the verb takes no `--launchers` |
| F10 | `environment_for` copies `HF_HOME` from the caller's environment into the unit (`autostart_prepare.py:89-91`); `problems()` compares the manifest with the same call, so the caller's value passes | Admission step 1, which `prepare` also runs, and the Environment rule |
| F11 | `_undo` removes or overwrites `destination` without comparing it with the recorded sha256 (`autostart_apply.py:147-150`), and nothing undoes `loginctl enable-linger` (`autostart_apply.py:231`) | Rollback by an agent, steps 3 and 5, and the `failed` install |
| F12 | `_backup` creates `backups/<id>` with `exist_ok=True` and writes unit-named files into it through any link already there (`autostart_apply.py:120-121,131`) | Backup rule |
| F13 | `install --manifest` takes any path and `problems()` treats `path.parent` as the staging directory, so any private directory the account owns passes (`autostart_apply.py:206`, `autostart_verify.py:35`) | Location rule |
| F14 | `_record` keeps the replaced entry as `previous` without saying who installed it (`autostart_apply.py:175-183`) | the ledger gains `via`; Overwrite and Rollback read it |
| F15 | a second writer of login units has no authority check: `fleet.autostart.install("login")` (`fleet/autostart.py:310-341`) writes the plist, the systemd unit or the task itself (`_mac_install`, `_systemd_install`, `_windows_install`, with the caller's `PATH` and `enable-linger`) and is reached by `poolhouse-traind --persist` (`fleet/daemon.py:658-661,811-825`, which also carries `--slots`, `--label` and `--report` into the unit), by `poolhouse-pool join --persist` (`fleet/join.py:539-546,647`; `packaging/install.sh:387` and `install.ps1:341` call it) and by the pool page's "start at logon" (`fleet/routes.py:783`, `fleet/ui.py:407`). It writes the same label `com.poolhouse.traind` and the same destination as the `pool-daemon` role, so an agent that runs it overwrites a delegated unit with any argv (the gate never ran), and its `uninstall()` (`join.py:572`, `fleet/uninstall.py:109`) unloads and removes one. docs/notes/release-backlog-2026-10-15.md:382 records the gap | slice 3, no legacy mode: delete `poolhouse-traind --persist` and `daemon.persist`; delete `autostart.install`, `autostart.uninstall` and the three writers. `join._persist`, the page route, `leave_machine` (`join.py:572`) and `fleet/uninstall.py:109` call `autostart_agent.persist_login` and `unpersist_login`, which stage the `pool-daemon` role (what `join --persist` installs today) and run Admission steps 1 to 3, 5 and 6, or undo the ledger's roles by the person path. A process with no agent marker passes as a person, as `runtime_trust.authorize` treats it (`runtime_trust.py:115`), with no typed id: the one process that stages the manifest also installs it, so there is no gap between `prepare` and a typed id for an agent to fill, the `--persist` flag or the tick is the consent, and the install scripts gain no step. A marked process is refused with "an agent starts Poolhouse at login with `poolhouse autostart prepare` and `install --agent NAME`", and `join` and `leave` carry on without persisting (they already handle `persisted` false) |
| F16 | the identity test of the gate is the environment alone: `authority.require` (`authority.py:201-218`) reads `CLAUDECODE` and `POOLHOUSE_WORKSPACE_AGENT`, a subagent's shell inherits both from its lead, `workspace_lead` only finds a token file with no `.parent` beside it (`authority.py:195-199`) and a token file of an ended or revoked session stays on disk; the Bash guard names a subagent's missing `--agent` only for `poolhouse-workspace` commands (`scripts/hooks/claude-bash-guard:170-182`); the sandbox removes `POOLHOUSE_WORKSPACE_AGENT` from a command (`scripts/agent_sandbox_profile.py:21-22`) | `--agent NAME` on the verbs, equal to a present environment value; `authority.admit` and the live `whoami` of Admission step 4; the guard pattern for the verbs (Wiring) |

## Attack first

Author's pass, 2026-10-16, revised after review rounds 1 and 2. Each row is a defence and a test that goes red when the defence is
removed. Tests are in `tests/test_autostart_delegated.py` unless named. "Fixed" means fixed in this design; "Accepted" means
a named risk for the owner.

| Attack | Defence | Test that fails without it | Status |
| --- | --- | --- | --- |
| Copied or cloned manifest (another account, another device, a restored disk image) | `device`, `platform`, the recorded `HOME` and state root against `environment_for`, the Location rule, the staging directory owned by the account and mode 0700, Admission step 3 | `test_a_manifest_copied_from_another_account_is_refused`, `test_a_manifest_for_another_device_is_refused_to_an_agent`, `test_a_manifest_outside_the_staging_directory_is_refused_to_an_agent`, `test_a_manifest_whose_id_is_not_its_directory_name_is_refused_to_an_agent` | Fixed for the manifest. A cloned disk image has the same device id: the manifest then installs the same two fixed units the clone's own `prepare` would, with the clone's launchers. A cloned state directory also copies `node/client/<name>.token` and its `.parent` file, so on the clone a copied lead passes `workspace_lead`, and the live check wherever the clone's node answers for that token: Accepted (A7) |
| Replay of an old manifest | the ledger `used` list, `backups/<id>` absent, the one-hour age and the Order rule | `test_an_installed_manifest_is_refused_again`, `test_a_manifest_older_than_an_hour_is_refused_to_an_agent`, `test_a_manifest_older_than_the_last_install_of_its_role_is_refused`, `test_an_existing_backup_folder_for_the_id_is_refused` | Bounded. The age and order rules read fields the manifest's writer sets; the `used` list (200 ids) is the only guard against reusing an id. What a replay installs is the two fixed units a fresh `prepare` stages |
| Forged or edited manifest or unit between `prepare` and `install` | re-rendering every unit from the fixed role (the defence against a forgery), each unit's sha256, the digest over the canonical manifest (detects a change after `prepare` printed it), one read held in memory, the 0700 staging directory, the profile's write denial | `test_a_manifest_and_units_edited_together_with_a_matching_digest_fail_the_re_render`, `test_a_manifest_edited_after_prepare_fails_the_digest`, `test_a_staged_unit_swapped_after_the_check_is_not_installed` | Fixed |
| A malicious agent crafts a unit for persistence (extra argv, `--report=os:system`, another role, a launcher with appended code, a copy of a real launcher in a directory it can write, `--system`, a destination outside the unit directory, a foreign label, a changed environment, a model cache directory it controls through `HF_HOME`) | Agent rules: fixed roles, exact argv, Launcher path, Exact launcher, Environment, exact destination; the service manager is called only with the fixed label | parametrized `test_a_crafted_manifest_is_refused_before_anything_runs` (asserts the recorder saw no command and the unit directory is unchanged), `test_a_launcher_with_appended_code_is_refused`, `test_a_byte_identical_launcher_in_a_writable_directory_is_refused_to_an_agent`, `test_a_windows_launcher_that_is_not_the_runtime_copy_is_refused_to_an_agent`, `test_an_hf_home_in_the_callers_environment_is_not_carried_into_the_unit`, `test_the_agent_prepare_verb_takes_no_launchers_argument` | Fixed. A runtime deployed under `runtime.deploy` is trusted code and the units run it: Accepted (A1) |
| A launcher replaced after the install | `~/.poolhouse/bin` is outside the sandbox's write list and in `STATE_DENY_WRITE`; `status` runs `launcher_problem` | `test_edit_rules_refuse_the_launcher_directory` (`tests/test_agent_sandbox.py`) | Accepted (A1): the launcher is compared once, at install; `runtime ensure` rewrites it; `status` does not repeat the byte comparison |
| An agent compromised by injected text (a board message, a page, a file) | the effect is the two fixed units on user scope, running the pinned launcher with the fixed environment (Launcher path, Exact launcher and Environment hold this); a widening (a role, a flag, `--system`, another path) is refused as a crafted manifest; no prompt exists to talk the person through; the audit row and `status` show it; one-hour age and three installs an hour; `rollback` undoes it | `test_an_injected_widening_changes_nothing`, `test_every_delegated_pass_and_refusal_is_audited_with_agent_and_digest` | Fixed for widening. Accepted (A2): injected text can cause the permitted effect, installing the two units |
| Downgrade, `prod` to `dev` | loosening is a person at a terminal typing `yes`; a lead agent may only tighten; `preset dev` over a stricter registry takes the same prompt; `POOLHOUSE_AUTHORITY_FLOOR` only tightens; `authority.json` is write-denied to the sandbox and the file tools | `test_prod_keeps_the_autostart_gate_a_persons` (extends `tests/test_authority.py::test_new_gate_honors_an_existing_prod_preset`), `test_an_agent_cannot_loosen_the_autostart_gate`, `test_edit_rules_refuse_authority_and_autostart_state` (`tests/test_agent_sandbox.py`) | Fixed. Unsandboxed code running as the account can edit `authority.json`: Accepted (A3) |
| A subagent or helper tries to pass the gate (it inherits its lead's environment; a stale or revoked lead's token file stays on disk) | the environment cannot tell a subagent from its lead, so a marked process must pass `--agent NAME`, equal to a present `POOLHOUSE_WORKSPACE_AGENT`; the Bash guard blocks a subagent's autostart verb that omits its own `--agent` and any command naming another session; Admission step 4 asks the node live and refuses a record with a parent, a name holding `/`, a retired session, an unknown token and an unreachable node; every refusal is a row | `test_a_subagent_running_an_autostart_verb_must_name_itself` (`tests/test_access_hooks.py`), `test_a_helper_identity_is_refused_install_and_rollback`, `test_a_subagent_whose_environment_carries_its_leads_name_is_refused`, `test_a_retired_session_with_a_token_file_on_disk_is_refused`, `test_an_unreachable_node_refuses_the_agent_path`, `test_a_marked_process_without_agent_is_refused`, `test_an_unnamed_marked_process_is_refused` | Fixed for a subagent under the Bash guard that passes its own name, and for a stale token file. Accepted (A7): a process outside the guard (Codex, a local model, a shell with the hook off) that names its lead and can read the lead's token file passes as the lead |
| Approval fatigue | the delegated path asks nothing; the person path's typed id is unchanged; an agent cannot raise the loosening prompt (an agent-marked process is refused before it asks); repeated agent installs stop at three an hour, counted from pending rows over a time window that tolerates a stepped clock; a refused attempt writes one row; refusals leave one row per distinct reason an hour and a per-reason counter that fails closed to appending | `test_a_fourth_delegated_install_in_an_hour_is_refused`, `test_other_gates_audit_rows_do_not_reset_the_rate_count`, `test_a_row_dated_in_the_future_does_not_hide_counted_rows`, `test_a_row_written_under_a_slow_clock_does_not_hide_the_rows_before_it`, `test_a_refused_attempt_writes_one_row_not_two`, `test_a_flood_of_one_refusal_does_not_hide_a_different_one`, `test_a_locked_or_corrupt_refusal_counter_appends_the_row`, `test_refusals_past_the_cap_leave_one_counted_summary_row`, `test_an_agent_marked_process_cannot_raise_the_loosening_prompt` | Fixed in code. An agent asking the person in chat to delegate the gate again and again is a social act no code stops: Accepted (A4) |
| Takeover by whoever is merely present (a process that sets the markers, an unattended terminal) | the path needs the installed copy on the real home, `--agent` naming a session the node answers for as a main session, and the gate delegated; a terminal with no marker still needs the typed manifest id; `prod` leaves it a person's | `test_a_process_that_only_sets_the_markers_is_refused`, `test_the_delegated_path_needs_the_installed_copy_on_the_real_home`, `test_the_person_path_still_needs_the_typed_id` | Accepted (A3, A7): the gate is an environment test with a node check (docs/person-delegation.md, section 1), and code running as the account that can read the session records can write a login unit without it |
| Symlinked `LaunchAgents` (or `~/.config/systemd/user`, or a parent of either), or a symlink swapped in after the check | `destination_problem`'s realpath chain, repeated after the lock is taken, then a write through a directory descriptor opened with `O_NOFOLLOW`; a symlink or non-regular file at the target is refused | `test_a_symlinked_unit_directory_is_refused_to_an_agent`, `test_a_directory_swapped_for_a_symlink_after_the_check_is_not_followed` (a seam runs between the check and the write) | Fixed |
| An agent install over a person's customised install, or over a person's job loaded under the same label from another path, which the agent then cannot undo | the Overwrite rule: an agent replaces only a unit whose ledger entry has `via` `delegated` and an unedited destination; the Loaded rule: a label loaded from any other path is a person's and is not booted out; `via` is a record, not authentication | `test_an_agent_install_over_a_persons_install_is_refused`, `test_an_agent_install_over_an_unrecorded_unit_is_refused`, `test_an_agent_install_over_a_job_loaded_from_another_path_is_refused`, `test_an_agent_rollback_does_not_boot_out_a_job_loaded_from_another_path` | Fixed. Accepted (A3): `ledger.json` is plain JSON, so whoever can write it can mark a person's entry `delegated`; the write denial on `autostart` (`test_edit_rules_refuse_authority_and_autostart_state`) is the defence |
| Rollback abuse to remove or overwrite the person's other units or the person's edits | agent rollback only for a `delegated` ledger entry; label, unit names and destinations re-derived from the fixed table and compared with the ledger; destination sha256 equal to the recorded sha256 before any removal or restore; backups restored only for a `delegated` `previous` that equals `render()` of the fixed role; `index.json` fields never used as paths | `test_a_tampered_ledger_naming_another_label_boots_out_nothing`, `test_a_tampered_backup_index_destination_is_never_written`, `test_a_backup_that_is_not_a_rendered_unit_is_not_restored`, `test_an_agent_cannot_roll_back_a_person_s_install`, `test_rollback_refuses_a_unit_edited_after_install`, `test_an_agent_does_not_restore_a_previous_record_a_person_made`, `test_rollback_leaves_every_other_unit_in_the_directory_alone`, `test_rollback_with_one_role_refused_undoes_the_other_and_keeps_remaining`, `test_a_failing_previous_leaves_the_role_installed_and_loaded`, `test_a_restore_that_fails_puts_the_current_unit_back` | Fixed |
| Rollback leaves the account's login lingering on, or a second install hides that the account had it off | the install records the earliest `linger_before` (a later delegated install copies it from the delegated entry it replaces, because the first install turned linger on); a delegated rollback and a `failed` install run `loginctl disable-linger` when it was `no` and no role remains in the ledger | `test_a_second_install_keeps_the_first_installs_linger_before`, `test_rollback_disables_the_linger_the_install_enabled`, `test_rollback_keeps_linger_while_a_role_remains`, `test_rollback_leaves_a_linger_that_was_already_on`, `test_a_failed_install_disables_the_linger_it_enabled` | Fixed for a delegated rollback; the person's rollback leaves it on: Accepted (A5) |
| A sandboxed command matched by an excluded string runs more than the verb | none until the matching is verified: slice 1's harness run is a hard precondition for all three entries, `prepare` included (Wiring); each verb checks its own arguments and the Admission order whatever it is given | `test_prepare_prints_a_command_the_profile_excludes` (`tests/test_agent_sandbox.py`); the harness run of slice 1 | Accepted (A6) until slice 1 records the result; no entry is added before it does |
| `--report` or another daemon option used to run code at login (F1) | no option but `--port=8770` on the agent path | covered by the crafted-manifest parametrization | Fixed |
| A second writer of login units: `poolhouse-traind --persist`, `poolhouse-pool join --persist`, the page's "start at logon" (F15) | slice 3 deletes the writer and `--persist`; the replacement runs Admission steps 1 to 3, 5 and 6; a marked process is refused with the verbs named, so an agent has one way in | `test_poolhouse_traind_has_no_persist_flag`, `test_a_marked_process_cannot_persist_through_join`, `test_a_marked_process_cannot_unpersist_through_leave`, `test_join_persist_installs_the_same_fixed_units_for_a_person`, `test_a_persisted_unit_is_current_for_status` | Fixed. Accepted (A3): a process with no marker is a person here, as it is at `runtime_trust.authorize` |
| Audit forged as a person's (F2), or lost, or an install in the ledger with no row | `by: delegated` and the agent name; a pending row is written before anything changes and completed after, so a crash between the ledger and the completion row leaves the pending row, and `status --json` lists it as unfinished; an exception writing the pending row stops the install | `test_a_delegated_install_is_not_recorded_as_a_person`, `test_a_crash_after_the_ledger_write_leaves_a_pending_row_listed_as_unfinished`, `test_an_exception_writing_the_pending_row_stops_the_install` | Fixed |

### Accepted risks for the owner

- **A1.** Units run the launchers of a runtime that `poolhouse runtime ensure` deployed under `runtime.deploy` (and
  `runtime.deploy-unmerged`, which has its own independent review). The launcher is compared byte for byte once, at install.
  `runtime_deploy.switch` rewrites the launchers in the state bin directory on each deploy, the unit runs whatever the file
  holds at each login, and `status` checks only the head of the file and its runtime root. After install, trust in the units
  is trust in `runtime.deploy` and in write access to `~/.poolhouse/bin`.
- **A2.** An agent that follows injected text can install the two units. They are Poolhouse's own programs with fixed
  arguments, a fixed launcher and a fixed environment; that bound holds only through the Launcher path, Exact launcher and
  Environment rules. `status` reports them and `rollback` removes them.
- **A3.** The `excludedCommands` verb is the capability the owner is deciding: a sandboxed agent cannot write
  `~/Library/LaunchAgents` or `~/.config/systemd` today, and with the three verbs it causes a login unit to be written
  outside the sandbox. The gate and the registry are environment and file tests. Code that runs as the account, outside the
  sandbox, can already edit `authority.json`, edit `autostart/ledger.json` (so `via` is provenance by record, not
  authentication), read the session records or copy a state directory with its token files, or write a login unit directly;
  for it the gate adds nothing.
- **A4.** Nothing stops an agent pressing the person in chat to delegate the gate.
- **A5.** `loginctl enable-linger` on Linux user installs makes the units start at boot, before login. A delegated rollback
  disables it when the install turned it on, and so does a `failed` install. The person's rollback leaves it on, as today.
- **A6.** The harness's matching of an excluded string against a command with further arguments or a compound tail is
  unverified (docs/agent-sandbox.md:195). If it matches by prefix, a sandboxed command that starts with one of the three
  strings runs outside the sandbox in full. The three deploy commands have the same property today; this design adds three
  strings, `prepare` among them, and adds none until slice 1 has recorded the matching (Wiring).
- **A7.** The lead identity is the node's record of a session plus the caller's assertion of its name.
  `authority.workspace_lead` finds a token file with no parent file and does not ask the node (`authority.py:195-199`), and
  `authority.require` reads only the environment. The verbs add `--agent`, the live `whoami` and the Bash guard's subagent
  rule, which stop a subagent under the guard and a stale or revoked token file. They do not stop a process outside the
  guard (Codex, a local model, a shell with the hook off) that passes the lead's name and can read the lead's token file
  under `node/client`: such a process is the lead for this gate. A copied state directory carries the token files with it.

## Review round 1

An independent review of the first draft reported six blocking findings and further notes. Each was checked against the
code; all hold. None is dropped.

| Id | Finding | Checked against | Where answered |
| --- | --- | --- | --- |
| B1 | launcher directory unpinned | `autostart_verify.py:59-68`, `autostart_prepare.py:181-183`, `autostart_manifest.py:216-217` (F9); a copy of a real launcher in a directory the sandbox can write passes whole-file equality and can be swapped after install | Launcher path, Exact launcher, F9, F5 |
| B2 | `HF_HOME` copied from the caller's environment | `autostart_prepare.py:90` (F10) | Admission step 1, Environment |
| B3 | an agent overwrites a person's customised install and cannot roll it back | `autostart_apply.py:175-183`; the person's own `rollback` still restores `backups/<id>`, the agent's cannot restore a person's record under the agent rules | Overwrite, Rollback by an agent step 4, F14 |
| B4 | the rate limit counts the last 500 audit rows | `authority.py:216` writes an `authority.use` row for every delegated gate use and `audit_rows` returns every row, so other gates' rows displace counted rows | Rate, Audit |
| B5 | rollback leaves `loginctl enable-linger` on | `autostart_apply.py:231`; `_undo` has no disable | What the delegated install does, Rollback by an agent step 5, A5, F11 |
| B6 | rollback of an edited unit | `autostart_apply.py:147-150` removes or overwrites without comparing | Rollback by an agent step 3, F11 |
| N1 | the digest is integrity, not authenticity | the digest has no key | Digest rule, the AGENTS.md replacement |
| N2 | the manifest may be anywhere | `autostart_apply.py:206` | Location, F13 |
| N3 | `created`, `expires` and `id` are self-asserted | `autostart_manifest.py:134-158` checks their shape and a 24 hour span only | Id and times, the replay row |
| N4 | `backups/<id>` reuse | `autostart_apply.py:120-121,131` | Backup, F12 |
| N5 | `excludedCommands` grows by three verbs with free arguments | `scripts/agent_sandbox_profile.py:58`, docs/agent-sandbox.md:195 | Wiring, A6 |
| N6 | A1, A2 and A3 overstate | the Accepted risks as first written | A1, A2, A3 |
| N7 | the lead identity is an environment assertion | `authority.py:195-198,213-215` | A7 |

## Review round 2

An independent review of the revision reported two blocking findings and eight further points. Each was checked against the
code; all ten hold, and R2, R4 and R7 hold more widely than reported (the notes in the table). None is dropped.

| Id | Finding | Checked against | Where answered |
| --- | --- | --- | --- |
| R1 | a subagent passes the gate as its lead | `authority.py:195-218` reads the environment and a token file; the Bash guard names a missing `--agent` only for `poolhouse-workspace` commands (`scripts/hooks/claude-bash-guard:170-182`); `subagent_naming_another` (185-194) already refuses naming another session, so what is open is a subagent that names nobody and inherits the lead's variables | Admission step 4, Wiring (guard pattern), F16, the subagent row, A7 |
| R2 | `poolhouse-traind --persist` installs a login unit with no check | `fleet/daemon.py:658-661,811-825` calls `autostart.install("login")` (`fleet/autostart.py:310-341`). Wider than reported: `poolhouse-pool join --persist` (`fleet/join.py:539-546`, called by both install scripts), the page's "start at logon" (`fleet/routes.py:783`) and `uninstall()` (`join.py:572`, `fleet/uninstall.py:109`) reach the same writer, which uses the label and destination of the `pool-daemon` role | F15, slice 3, the F15 attack row |
| R3 | a stale or revoked lead passes | `workspace_lead` checks that a token file exists with no `.parent` (`authority.py:195-199`); an ended or revoked session leaves its token file | Admission step 4 (live `whoami`, fails closed), A7 |
| R4 | the sandbox removes the agent variable | `agent_sandbox_profile.py:21-22`. `runtime_cli.py:319` takes `--agent` for claims and posting, but the runtime gate (`runtime_trust.authorize`, `runtime_trust.py:108-117`) still reads the environment; these verbs take the identity from `--agent` | Admission step 4, Wiring |
| R5 | `prepare` stages the caller's `HF_HOME` | `autostart_prepare.py:87-94`; `install` re-derives after its own drop, so a manifest prepared with `HF_HOME` set fails the Environment rule | Admission step 1, Environment rule |
| R6 | a second install records `linger_before` as `yes` | `_record` keeps the replaced entry as `previous` (`autostart_apply.py:175-183`); `_apply` enables linger on every Linux user install (`autostart_apply.py:230-231`) | What the delegated install does, Rollback step 5, the linger row |
| R7 | audit gaps | `authority.require` writes a row per pass (`authority.py:216`), so a pass refused later by a rule has two rows; `_apply` writes the ledger before the audit row (`autostart_apply.py:239-240`); a scan that stops at the first old row undercounts after a clock step back; a refusal cap of twenty lets one reason fill it. All four hold | Audit, Rate, the approval-fatigue and audit rows |
| R8 | `via` is not authenticated; a person's job under the same label | `ledger.json` is plain JSON (`autostart_ledger.py:15-32`); `_apply` unloads by label alone (`autostart_apply.py:226`, `Backend.unload`) | Overwrite, Loaded rule, A3 |
| R9 | rollback with one of two roles refused; a failing `previous` | `rollback` clears `last` after one undo (`autostart_apply.py:268`) and has no partial outcome | Rollback by an agent |
| R10 | F5 misstates the guard; the new module is not reviewed; cloned state; slice 1 precondition | `protectedwrites.py:24,115-120` already refuses Edit and Write at the two authority files and `STATE_DENY_WRITE` is the sandbox layer; `REVIEWED` (`protectedwrites.py:25-30`) lacks the new module; token files sit in the state directory | F5, Wiring, the cloned row, A6 |

Added while answering: `apply.lock` is taken before the id, rate and overwrite checks (Admission step 5), so two concurrent
installs cannot both pass them; and the pending row replaces the `authority.use` row for this gate, so the rate rule counts
one row per attempt.

## Tests

New `tests/test_autostart_delegated.py` (the attack table above plus the happy path on each platform with the service
manager recorded: a lead agent installs both roles, `status` is `current`, `rollback` restores, each step audited).
`tests/test_authority.py` gains the gate and the `prod` default. `tests/test_agent_sandbox.py` gains the
excluded commands, the new deny paths (`autostart`, `bin`, `authority.json`, `authority-audit.jsonl`) and
`test_prepare_prints_a_command_the_profile_excludes`. `tests/test_access_hooks.py` gains
`test_a_subagent_running_an_autostart_verb_must_name_itself` beside `test_a_subagent_acts_only_as_itself`, and
`tests/test_protected_writes.py` the two new module paths. `tests/test_authority.py` also covers `authority.admit` writing no
row. The existing `test_autostart_apply.py` agent-refusal tests keep passing under the person floor, and the two autostart
entries of `tests/test_redteam_human_floor.py` stay as they are: they run a marked process with no `--agent`, which the
delegated path refuses before it changes anything. The delegated tests clear `POOLHOUSE_AUTHORITY_FLOOR`. Slice 3 rewrites
the tests that drive `autostart.install` and `poolhouse-traind --persist` (`tests/test_windows.py`,
`tests/test_fleet_autostart.py`, `tests/test_wsl_autostart.py`) against the replacement, each case moved and none dropped.
No test calls the real service manager, the real keystore or an administrator prompt.

## Build slices

1. Gate, `authority.admit`, sandbox profile (deny paths, then the harness run for the excluded forms, a hard precondition
   for every entry, A6), the Bash guard pattern for the verbs, `protectedwrites.REVIEWED` and `docs/agent-sandbox.md`
   (small; the guard and `REVIEWED` edits are guard sources and need the exact-SHA independent approval).
2. `autostart_agent.py` and `autostart_agent_rules.py`, the digest in `prepare`, agent rules, the live session check,
   descriptor writes, rollback re-derivation, the rate window, pending and completion rows and the refusal counters, the
   linger record, `Backend.loaded_from`, the `poolhouse autostart` verb (the largest; stays under the 900-line limit by
   living in its own modules).
3. The older writer (F15): `poolhouse-traind --persist`, `autostart.install` and `uninstall`, and their callers moved to the
   replacement, with their tests.
4. Documents: the AGENTS.md section 1 bullet, `docs/install.md` "Starting at login", `docs/commands.md` and
   `src/poolhouse/cli/reference.py` (the `--persist` row of `poolhouse-traind`), `docs/person-commands.md`, `docs/pool.md`
   2.5(c), the `autostart_cli.py` help text ("a person at a terminal only"), HANDOFF.md.

Replacement for the AGENTS.md section 1 sentence that begins "`python -m poolhouse.fleet.autostart prepare`":
"`python -m poolhouse.fleet.autostart prepare` is open to an agent. `install` and `rollback` for the roles `pool-daemon`
and `runtime-ensure`, at user scope, with the fixed arguments and state-directory launchers `prepare` writes, pass a lead
agent that names itself with `--agent` and that the node answers for, while the gate `autostart.install` is delegated
(default under `dev`, a person's under `prod`); the manifest must be
the one `prepare` staged for this device, is checked against the digest `prepare` printed (an integrity check, not a
signature) and is re-rendered byte for byte from the fixed roles; every use is audited, and an agent overwrites or rolls
back only what an agent installed, and an agent installs a login unit by no other command. `--system`, `llama-cpp-ensure`,
any other argument and `install`'s typed confirmation by a person stay as they were." That file is a guarded rule source, so the replacement goes in with the build under exact-SHA
independent approval.

## The person's journey

A person who is bad with tech tells an agent to start Poolhouse at login; the lead runs `prepare` and `install` and says
what it did. The install scripts' `poolhouse-pool join --persist` and the page's "start at logon" install the same
`pool-daemon` unit with no new step (F15). The terminal step that `docs/person-commands.md` lists as blocking the journey goes
away, and the person gains no prompt, flag or wait. The person path (`install` at a terminal with the typed id) is
unchanged, so a person who prefers it loses nothing. What the person can see afterwards: the device report's
`autostart: current` word, `status --json` naming who installed it, and the audit log.
