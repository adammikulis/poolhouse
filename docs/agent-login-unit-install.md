# Lead agents installing login units

Status: design only; nothing in this document is built. Decided by the owner on 2026-10-16: a lead agent may run
`install` and `rollback` of `python -m poolhouse.fleet.autostart` for the `pool-daemon` and `runtime-ensure` roles. Read
against development `0f9bbbc58`; no autostart command was run to write it. The attack section is the author's own pass and does
not count: AGENTS.md section 1 still requires a different agent to try to break this design before it is built, and
section 4 requires that agent's exact-SHA `land-review` accept before the build lands.

## What changes

Today `prepare` is open, `install` and `rollback` refuse every agent marker and any process without a terminal
(`fleet/autostart_apply.py:198,250`, `person.require_person`), and the person types the manifest id
(`sentinel/human.mint`). After this change:

| Command | Person at a terminal | Lead agent, gate `autostart.install` delegated | Everyone else |
| --- | --- | --- | --- |
| `prepare` | open | open | open |
| `install --manifest PATH --digest SHA` | as today: types the manifest id | no prompt; user scope, the two roles, the fixed argv, nothing else | refused |
| `rollback` | as today | only what a lead agent installed, restored from validated bytes | refused |
| `status`, `verify` | read-only | read-only | read-only |
| `--system`, role `llama-cpp-ensure`, any other argv | as today | refused | refused |

A lead agent is what `authority.require` already means: `CLAUDECODE` or `POOLHOUSE_AGENT` set, a session name in
`POOLHOUSE_WORKSPACE_AGENT` that the node recorded as a main session (a token on a board and no parent), never a
`parent/name` identity, never an unnamed process. A spawned subagent is refused. The delegated path prompts for nothing,
asks for no password and calls no `sudo`, `osascript` or `pkexec`.

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

An agent-marked `install` or `rollback` does these in order and stops at the first refusal. Nothing before step 6 writes
anything except the audit row.

1. Drop the variables that move the state root or the home (`runtime_trust.ignore_redirects`: `POOLHOUSE_HOME`,
   `POOLHOUSE_CACHE` and the other overrides), as the three deploy commands do.
2. Refuse `--system` ("a system-wide install is a person's").
3. Require `real_machine.refusal() == ""` (the installed copy under the account's own `~/.poolhouse/runtimes`, not a
   checkout, a worktree or a copy) and `Path.home()` resolved equal to the account home from the password database
   (`HOME` is not redirected). Both are `Seams` fields so tests can drive them.
4. `authority.require("autostart.install", "install autostart units pool-daemon,runtime-ensure <digest12>")`. It returns
   `delegated` only for a lead agent while the gate is delegated, and appends the `authority.use` row naming the agent.
   An exception writing that row stops the install.
5. Read the manifest once (at most 256 KiB), parse it, and hold the parsed value and the staged unit bytes in memory for
   the rest of the call.
6. Run `problems()` as it is today, then the agent rules below. Then take `apply.lock` and apply.

### Agent rules (`autostart_agent.py`, new)

| Rule | Check |
| --- | --- |
| Roles | every role is in `AGENT_ROLES = ("pool-daemon", "runtime-ensure")`; scope is `user` |
| Digest | `sha256` of the manifest's canonical JSON (sorted keys, no spaces) equals `--digest`, the value `prepare` printed; required here, checked when a person passes it |
| Age | `created` is at most one hour old (`AGENT_MAX_AGE_S = 3600`); the person path keeps 24 hours |
| Order | `created` is later than the `at` of any install the ledger holds for the same role |
| Exact argv | `pool-daemon` is `[launcher, "--port=8770"]`, `runtime-ensure` is `[launcher, "runtime", "ensure"]`; no `--slots`, `--label` or `--report` |
| Exact launcher | the launcher file equals, byte for byte, the text `runtime.write_launcher` writes for that name and the selected verified runtime, with either `follow_selection` (Windows: the `.exe` the runtime ships) |
| Exact unit | the staged bytes equal what `render()` returns for the re-derived role (already so for every path) |
| Exact destination | `allowed_dir(platform, "user") / unit.name`, no symlink in the chain, no non-regular file at the target |
| Overwrite | the destination is absent, or its sha256 equals the one the ledger recorded for that role (a unit this tooling wrote and nobody edited); anything else is "a unit this tooling did not write is in the way; a person installs" |
| Rate | at most three delegated installs and three delegated rollbacks in the last hour, counted from the last 500 rows of `authority-audit.jsonl` |

`status` and `verify` are unchanged: they compare the ledger and the files, report `current`, `stale`, `missing`,
`drifted` or `not-prepared`, and never repair. A `drifted` unit is not installed over by an agent (rule Overwrite).

### What the delegated install does

It writes the role's unit files into `~/Library/LaunchAgents` (macOS), `~/.config/systemd/user` (Linux) or registers the
user task named by the role label (Windows), and runs only `Backend.load`, `unload` and `probe` for user scope
(`launchctl bootstrap|bootout|print gui/<uid>`, `systemctl --user ...`, `loginctl enable-linger`, `schtasks /Create|/Delete|/Query`).
It creates the working directory, log directory and backup folder under the state root. It does nothing else.

### Rollback by an agent

`rollback` reads `ledger["last"]` and refuses unless that install was delegated (the ledger gains `via` and `agent` per
install). It then re-derives, from the fixed role table, the label, unit names and destinations of the roles it names and
refuses if the ledger holds anything different. It restores a backup only when the backup bytes equal `render()` of the
previous role record and that record passes the same agent rules; with no previous unit it unloads and removes the role's
fixed unit files. Backup `destination` and `name` fields are read as claims and compared, never used as paths.

### Audit

Every delegated attempt appends a row to `authority-audit.jsonl`, refusals included (twenty refusal rows an hour at
most):

```
{"at":…, "event":"autostart.install", "by":"delegated", "agent":"claude-6e1a2f", "gate":"autostart.install",
 "roles":["pool-daemon","runtime-ensure"], "manifest":"<id>", "digest":"<64 hex>", "scope":"user",
 "platform":"darwin", "from":[{"role":…, "manifest":"<id> or empty", "units":["<sha256> or empty"]}],
 "to":[{"role":…, "manifest":"<id>", "units":["<sha256>"]}], "result":"installed|refused|undone", "reason":"…"}
```

The rows are written with `authority.record(event, by="delegated", ...)`; a person's install keeps its `by: person` row.
`status --json` adds the ledger's `via` and `agent`.

## Wiring so an agent can use it

The Bash sandbox profile (`scripts/agent_sandbox_profile.py:35-58`) allows a sandboxed command to write
`workspace`, `logs`, `activity`, `requests`, `holds` and `hook-diagnostics` under the state root, and denies
`~/Library/LaunchAgents` and `~/.config/systemd`. `~/.poolhouse/autostart` (staging, ledger, backups, logs) is in neither
list, so a sandboxed `prepare` cannot write its staging directory today.

- `prepare`, `install` and `rollback` run outside the sandbox the way `DEPLOY_COMMANDS` do: a stable verb `poolhouse
  autostart prepare|install|rollback` calls `fleet.autostart.main`, and the profile's `excludedCommands` gains those three
  strings. `prepare` prints its command in that form with `--digest`; the person's pasted line is that same line.
  `status` and `verify` stay inside the sandbox.
- `STATE_DENY_WRITE` gains `autostart`, `authority.json` and `authority-audit.jsonl`, so `edit_rules` also refuses the
  Edit and Write tools there. The profile as written does not refuse the file tools at those paths.

## Findings in the current code

Each is fixed by this design; none is fixed by this document.

| Id | Finding | Fix |
| --- | --- | --- |
| F1 | `--report=MODULE:CALLABLE` makes the daemon import any module and call the named function with no arguments (`fleet/device.py:120`, `fleet/daemon.py:782`); `_POOL_ARG` accepts any text up to 512 characters for it (`autostart_verify.py:32`) | the agent path accepts no `--report`; the person path restricts it to `poolhouse.` modules in the same slice |
| F2 | `install` records its audit row through `authority.record` without `by`, which writes `by: person` | the agent path passes `by="delegated"` and the agent name; `record` already lets a caller set `by` |
| F3 | `_undo` takes `destination`, `name` and `sha256` from `backups/<id>/index.json`, and the label and destinations from `ledger["last"]`; a tampered file makes rollback write or delete any path the account owns and boot out any label | agent rollback re-derives all of them from the fixed table and compares (Rollback by an agent) |
| F4 | `launcher_problem` checks the head of the launcher and an assigned `root`; the rest of the file may be any code | whole-file equality with the text the runtime tooling writes |
| F5 | `~/.poolhouse/autostart` and `authority.json` are not in `STATE_DENY_WRITE`, so Edit and Write are not refused there | add them (Wiring) |
| F6 | `schtasks /Create /XML` reads the staged XML after the hash was checked | install from a private copy of the verified bytes, made with `O_EXCL` and mode 0600 |
| F7 | `allowed_dir` and `environment_for` read `$HOME` and `POOLHOUSE_HOME`; an agent that sets them installs under a home it controls | Admission steps 1 and 3 |
| F8 | the file write follows a directory check made earlier in the call | repeat the realpath check, open the unit directory with `O_DIRECTORY\|O_NOFOLLOW`, write the temporary file and rename through that descriptor |

## Attack first

Author's pass, 2026-10-16. Each row is a defence and a test that goes red when the defence is removed. Tests are in
`tests/test_autostart_delegated.py` unless named. "Fixed" means fixed in this design; "Accepted" means a named risk for the
owner.

| Attack | Defence | Test that fails without it | Status |
| --- | --- | --- | --- |
| Copied or cloned manifest (another account, another device, a restored disk image) | `device`, `platform`, the recorded `HOME` and state root against `environment_for`, the staging directory owned by the account and mode 0700, the digest, Admission step 3 | `test_a_manifest_copied_from_another_account_is_refused`, `test_a_manifest_for_another_device_is_refused_to_an_agent` | Fixed. A cloned disk image has the same device id: the manifest then installs the same two fixed units the clone's own `prepare` would, with the clone's launchers |
| Replay of an old manifest | the ledger `used` list, the one-hour age, the Order rule, the digest from this `prepare` | `test_an_installed_manifest_is_refused_again`, `test_a_manifest_older_than_an_hour_is_refused_to_an_agent`, `test_a_manifest_older_than_the_last_install_of_its_role_is_refused` | Fixed. A reset ledger leaves the age rule and the fixed-role checks; an unused manifest under an hour old and newer than the last install still installs a valid configuration |
| Forged or edited manifest or unit between `prepare` and `install` | the digest over the canonical manifest, each unit's sha256, re-rendering every unit from the fixed role, one read held in memory, the 0700 staging directory, the profile's write denial | `test_a_manifest_and_units_edited_together_fail_the_digest`, `test_a_staged_unit_swapped_after_the_check_is_not_installed` | Fixed |
| A malicious agent crafts a unit for persistence (extra argv, `--report=os:system`, another role, a launcher with appended code, `--system`, a destination outside the unit directory, a foreign label, a changed environment) | Agent rules: fixed roles, exact argv, exact launcher bytes, exact destination, `environment_for`; the service manager is called only with the fixed label | parametrized `test_a_crafted_manifest_is_refused_before_anything_runs` (asserts the recorder saw no command and the unit directory is unchanged), `test_a_launcher_with_appended_code_is_refused` | Fixed. A runtime deployed under `runtime.deploy` is trusted code and the units run it: Accepted (A1) |
| An agent compromised by injected text (a board message, a page, a file) | the effect is capped at the two fixed units on user scope; a widening (a role, a flag, `--system`, another path) is refused as a crafted manifest; no prompt exists to talk the person through; the audit row and `status` show it; one-hour age and three installs an hour; `rollback` undoes it | `test_an_injected_widening_changes_nothing`, `test_every_delegated_pass_and_refusal_is_audited_with_agent_and_digest` | Fixed for widening. Accepted (A2): injected text can cause the permitted effect, installing the two units |
| Downgrade, `prod` to `dev` | loosening is a person at a terminal typing `yes`; a lead agent may only tighten; `preset dev` over a stricter registry takes the same prompt; `POOLHOUSE_AUTHORITY_FLOOR` only tightens; `authority.json` is write-denied to the sandbox and the file tools | `test_prod_keeps_the_autostart_gate_a_persons` (extends `tests/test_authority.py::test_new_gate_honors_an_existing_prod_preset`), `test_an_agent_cannot_loosen_the_autostart_gate`, `test_edit_rules_refuse_authority_and_autostart_state` (`tests/test_agent_sandbox.py`) | Fixed. Unsandboxed code running as the account can edit `authority.json`: Accepted (A3) |
| A subagent or helper tries to pass the gate | `authority.require` accepts only a name the node recorded as a main session: `parent/name`, an unknown name and an unnamed process raise `HumanRequired`; the refusal is a row | `test_a_helper_identity_is_refused_install_and_rollback`, `test_an_unnamed_marked_process_is_refused` | Fixed |
| Approval fatigue | the delegated path asks nothing; the person path's typed id is unchanged; an agent cannot raise the loosening prompt (an agent-marked process is refused before it asks); repeated agent installs stop at three an hour | `test_a_fourth_delegated_install_in_an_hour_is_refused`, `test_an_agent_marked_process_cannot_raise_the_loosening_prompt` | Fixed in code. An agent asking the person in chat to delegate the gate again and again is a social act no code stops: Accepted (A4) |
| Takeover by whoever is merely present (a process that sets the markers, an unattended terminal) | the path needs the installed copy on the real home, a name the node holds a main-session token for, and the gate delegated; a terminal with no marker still needs the typed manifest id; `prod` leaves it a person's | `test_a_process_that_only_sets_the_markers_is_refused`, `test_the_delegated_path_needs_the_installed_copy_on_the_real_home`, `test_the_person_path_still_needs_the_typed_id` | Accepted (A3, A5): the gate is an environment test (docs/person-delegation.md, section 1), and code running as the account that can read the session records can write a login unit without it |
| Symlinked `LaunchAgents` (or `~/.config/systemd/user`, or a parent of either), or a symlink swapped in after the check | `destination_problem`'s realpath chain, repeated after the lock is taken, then a write through a directory descriptor opened with `O_NOFOLLOW`; a symlink or non-regular file at the target is refused | `test_a_symlinked_unit_directory_is_refused_to_an_agent`, `test_a_directory_swapped_for_a_symlink_after_the_check_is_not_followed` (a seam runs between the check and the write) | Fixed |
| Rollback abuse to remove or overwrite the person's other units | agent rollback only for a `delegated` ledger entry; label, unit names and destinations re-derived from the fixed table and compared with the ledger; backups restored only when they equal `render()` of the previous role; `index.json` fields never used as paths | `test_a_tampered_ledger_naming_another_label_boots_out_nothing`, `test_a_tampered_backup_index_destination_is_never_written`, `test_a_backup_that_is_not_a_rendered_unit_is_not_restored`, `test_an_agent_cannot_roll_back_a_person_s_install`, `test_rollback_leaves_every_other_unit_in_the_directory_alone` | Fixed |
| `--report` or another daemon option used to run code at login (F1) | no option but `--port=8770` on the agent path | covered by the crafted-manifest parametrization | Fixed |
| Audit forged as a person's (F2), or lost | `by: delegated` and the agent name; an exception writing the pass row stops the install | `test_a_delegated_install_is_not_recorded_as_a_person` | Fixed |

### Accepted risks for the owner

- **A1.** Units run the launchers of a runtime that `poolhouse runtime ensure` deployed under `runtime.deploy` (and
  `runtime.deploy-unmerged`, which has its own independent review). Trust in the units is trust in that runtime.
- **A2.** An agent that follows injected text can install the two units. They are Poolhouse's own programs with fixed
  arguments, `status` reports them, and `rollback` removes them.
- **A3.** The gate and the registry are environment and file tests. Code that runs as the account, outside the sandbox,
  can edit `authority.json`, read the session records or write a login unit directly. The gate adds no capability to it.
- **A4.** Nothing stops an agent pressing the person in chat to delegate the gate.
- **A5.** `loginctl enable-linger` on Linux user installs makes the units start at boot, before login. It is part of the
  install a person gets today.

## Tests

New `tests/test_autostart_delegated.py` (the attack table above plus the happy path on each platform with the service
manager recorded: a lead agent installs both roles, `status` is `current`, `rollback` restores, each step audited).
`tests/test_authority.py` gains the gate and the `prod` default. `tests/test_agent_sandbox.py` gains the
excluded commands, the new deny paths and `test_prepare_prints_a_command_the_profile_excludes`. The existing
`test_autostart_apply.py` agent-refusal tests keep passing under the person floor; the delegated tests clear
`POOLHOUSE_AUTHORITY_FLOOR`. No test calls the real service manager, the real keystore or an administrator prompt.

## Build slices

1. Gate, sandbox profile and `docs/agent-sandbox.md` (small).
2. `autostart_agent.py`, the digest in `prepare`, agent rules, descriptor writes, rollback re-derivation, audit rows,
   the `poolhouse autostart` verb (the largest; stays under the 900-line limit by living in its own module).
3. Documents: the AGENTS.md section 1 bullet, `docs/install.md` "Starting at login", `docs/person-commands.md`,
   `docs/pool.md` 2.5(c), the `autostart_cli.py` help text ("a person at a terminal only"), HANDOFF.md.

Replacement for the AGENTS.md section 1 sentence that begins "`python -m poolhouse.fleet.autostart prepare`":
"`python -m poolhouse.fleet.autostart prepare` is open to an agent. `install` and `rollback` for the roles `pool-daemon`
and `runtime-ensure`, at user scope, with the fixed arguments `prepare` writes, pass a lead agent while the gate
`autostart.install` is delegated (default under `dev`, a person's under `prod`); the manifest is bound by digest, every
use is audited, and an agent rolls back only what an agent installed. `--system`, `llama-cpp-ensure`, any other argument
and `install`'s typed confirmation by a person stay as they were." That file is a guarded rule source, so the
replacement goes in with the build under exact-SHA independent approval.

## The person's journey

A person who is bad with tech tells an agent to start Poolhouse at login; the lead runs `prepare` and `install` and says
what it did. The terminal step that `docs/person-commands.md` lists as blocking the journey goes
away, and the person gains no prompt, flag or wait. The person path (`install` at a terminal with the typed id) is
unchanged, so a person who prefers it loses nothing. What the person can see afterwards: the device report's
`autostart: current` word, `status --json` naming who installed it, and the audit log.
