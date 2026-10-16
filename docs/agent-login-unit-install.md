# Lead agents installing login units

Status: design only; nothing in this document is built. Decided by the owner on 2026-10-16: a lead agent may run
`install` and `rollback` of `python -m poolhouse.fleet.autostart` for the `pool-daemon` and `runtime-ensure` roles. Read
against development `f803ba49f`; no autostart command was run to write it. An independent attack-first review of the first
draft (2026-10-16, round 1 of at most two fix rounds) is answered in "Review round 1". The attack table is the author's pass
and does not count: AGENTS.md section 1 still requires a different agent to try to break this revision before it is built,
and section 4 requires that agent's exact-SHA `land-review` accept before the build lands.

## What changes

Today `prepare` is open, `install` and `rollback` refuse every agent marker and any process without a terminal
(`fleet/autostart_apply.py:198,250`, `person.require_person`), and the person types the manifest id
(`sentinel/human.mint`). After this change:

| Command | Person at a terminal | Lead agent, gate `autostart.install` delegated | Everyone else |
| --- | --- | --- | --- |
| `prepare` | open | open; the `poolhouse autostart prepare` verb stages the two fixed roles with the launchers in the state bin directory and takes no `--launchers`, `--slots`, `--label` or `--report` | open |
| `install --manifest PATH --digest SHA` | as today: types the manifest id | no prompt; user scope, the two roles, the fixed argv and launchers, nothing else | refused |
| `rollback` | as today | only what a lead agent installed, restored from validated bytes | refused |
| `status`, `verify` | read-only | read-only | read-only |
| `--system`, role `llama-cpp-ensure`, any other argv | as today | refused | refused |

A lead agent is what `authority.require` already means: `CLAUDECODE` or `POOLHOUSE_AGENT` set, a session name in
`POOLHOUSE_WORKSPACE_AGENT` that the node recorded as a main session (a token on a board and no parent), never a
`parent/name` identity, never an unnamed process. The test reads the environment (A7). The delegated path prompts for
nothing, asks for no password and calls no `sudo`, `osascript` or `pkexec`.

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
   `POOLHOUSE_CACHE` and the other overrides), as the three deploy commands do, and `HF_HOME`. The `HF_HOME` drop is this
   path's own list; `ignore_redirects` is unchanged.
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
| Location | the manifest path, resolved, equals `staging_root() / <manifest id> / manifest.json` with no symbolic link in the chain; the id is the directory name |
| Digest | `sha256` of the manifest's canonical JSON (sorted keys, no spaces) equals `--digest`, the value `prepare` printed; required here, checked when a person passes it. The digest has no key, so whoever can write the manifest can compute it: it detects a manifest changed after `prepare` printed it, and the re-render (Exact unit) is what refuses a forged one |
| Id and times | `id` is not in the ledger's `used` list (the last 200 ids) and `backups/<id>` does not exist. `created` is at most one hour old (`AGENT_MAX_AGE_S = 3600`; the person path keeps 24 hours) and later than the `at` of any install the ledger holds for the same role. `id`, `created` and `expires` are written by whoever wrote the manifest, so these checks bound an honestly stale manifest, not a forged one |
| Exact argv | `pool-daemon` is `[launcher, "--port=8770"]`, `runtime-ensure` is `[launcher, "runtime", "ensure"]`; no `--slots`, `--label` or `--report` |
| Launcher path | `argv[0]` equals `runtime_launchers.directory() / <name>` (`<name>.exe` on Windows), compared with that expected path and not with a path derived from `argv[0]`'s own directory, with no symbolic link in its chain (`runtime.plain`) |
| Exact launcher | POSIX: the file equals, byte for byte, the text `runtime.write_launcher` writes for that name, its entry point from `runtime_launchers.entrypoints(chosen)` and `runtime.selected()`, with `follow_selection=True` (what `runtime_deploy.switch` passes for the state bin directory); a launcher that differs is refused with "run poolhouse runtime ensure". Windows: `windows_private.launcher_problem` returns an empty string and the file's sha256 equals that of `chosen.python.parent / <name>.exe`, the file `runtime_launchers.install` copies |
| Environment | `role.environment` equals `environment_for(platform)` computed after step 1, so `HF_HOME` is `~/.cache/huggingface` under the account home whatever the caller's environment held; no other key |
| Exact unit | the staged bytes equal what `render()` returns for the re-derived role (already so for every path) |
| Exact destination | `allowed_dir(platform, "user") / unit.name`, no symlink in the chain, no non-regular file at the target |
| Overwrite | the destination is absent, or the ledger holds an entry for the role with `via` equal to `delegated` and the destination's sha256 equals that entry's unit sha256. Anything else is "a unit this tooling did not write, or a person installed, is in the way; a person installs". An entry with no `via` is a person's |
| Backup | `backups/<id>` is created with `mkdir(exist_ok=False)` and mode 0700; each file in it is created with `O_EXCL` and `O_NOFOLLOW` |
| Rate | at most three delegated installs and three delegated rollbacks whose audit row `at` is within the last 3600 s (`AGENT_WINDOW_S`) |

The rate count reads `authority-audit.jsonl` from its end, in blocks, until the first row older than the window, and
counts rows with `event` `autostart.install` or `autostart.rollback`, `by` `delegated` and `result` `installed`, `failed`
or `undone`. Rows of other gates (`authority.use`) are read past, not counted, and cannot push a counted row out of range;
the cost is the rows of the last hour.

`status` and `verify` are unchanged: they compare the ledger and the files, report `current`, `stale`, `missing`,
`drifted` or `not-prepared`, and never repair. A `drifted` unit is not installed over by an agent (rule Overwrite). Their
launcher check is `launcher_problem` (the head of the file and its runtime root), not the byte comparison above.

### What the delegated install does

It writes the role's unit files into `~/Library/LaunchAgents` (macOS), `~/.config/systemd/user` (Linux) or registers the
user task named by the role label (Windows), and runs only `Backend.load`, `unload` and `probe` for user scope
(`launchctl bootstrap|bootout|print gui/<uid>`, `systemctl --user ...`, `loginctl enable-linger`, `schtasks /Create|/Delete|/Query`).
On Linux it first reads the account's linger state (`loginctl show-user <uid> --property=Linger`; a failed read counts as
`yes`) and records it in the ledger entry as `linger_before`. It creates the working directory, log directory and backup
folder under the state root. The ledger entry of each role gains `via` (`delegated`), `agent` and `linger_before`. The
admission steps read the selected runtime with `runtime.selected()` and `runtime_launchers.entrypoints`, which run that
runtime's interpreter under `-I`. It does nothing else.

### Rollback by an agent

`rollback` runs Admission steps 1 to 4 with the action "roll back autostart units", then:

1. `ledger["last"]` names an install whose ledger entries have `via` equal to `delegated`.
2. The label, unit names and destinations of the roles it names are re-derived from the fixed role table; any difference
   from the ledger refuses.
3. For each unit, the destination is absent, or a regular file whose sha256 equals the recorded unit sha256. A destination
   that holds anything else was edited after the install: that role is refused ("a unit was changed after it was
   installed; a person rolls back") and nothing of it is removed or overwritten.
4. When the role has a `previous` record, the agent restores it only if that record has `via` equal to `delegated`, equals
   `render()` of the fixed role, passes the agent rules of the running build, and the backup bytes equal that render. The
   write goes through a descriptor on the fixed destination. A `previous` record that fails any of these is refused the
   same way: the agent restores nothing, and a person's `rollback` restores it from the backup. With no `previous`, the
   role is unloaded and its fixed unit files are removed.
5. On Linux user scope, when `linger_before` is `no` and no role remains in the ledger after the rollback, it runs
   `loginctl disable-linger`.

Backup `destination` and `name` fields are read as claims and compared, never used as paths. The person's `rollback` is
unchanged and leaves linger on.

### Audit

Every delegated attempt appends a row to `authority-audit.jsonl`:

```
{"at":…, "event":"autostart.install", "by":"delegated", "agent":"claude-6e1a2f", "gate":"autostart.install",
 "roles":["pool-daemon","runtime-ensure"], "manifest":"<id>", "digest":"<64 hex>", "scope":"user",
 "platform":"darwin", "from":[{"role":…, "manifest":"<id> or empty", "units":["<sha256> or empty"]}],
 "to":[{"role":…, "manifest":"<id>", "units":["<sha256>"]}], "result":"installed|failed|refused|undone", "reason":"…"}
```

`failed` is an install that reached apply, did not answer its probe and put the previous units back. Passes and failures are
never left out of the log; they are capped by the Rate rule. Refusals are capped at twenty rows an hour: past the
twentieth, a refusal appends no row and increments a counter in `autostart/refusals.json` (taken under `refusals.lock`).
The first attempt after that hour appends one `autostart.refusals-suppressed` row with `since`, `until` and `count`, then
clears the counter. `status --json` shows the live count, so every refusal is counted in the log or in the counter.

The rows are written with `authority.record(event, by="delegated", ...)`; a person's install keeps its `by: person` row.
`status --json` adds the ledger's `via` and `agent`.

## Wiring so an agent can use it

The Bash sandbox profile (`scripts/agent_sandbox_profile.py:29-58`) allows a sandboxed command to write
`workspace`, `logs`, `activity`, `requests`, `holds` and `hook-diagnostics` under the state root, and denies
`~/Library/LaunchAgents` and `~/.config/systemd`. `~/.poolhouse/autostart` (staging, ledger, backups, logs) and
`~/.poolhouse/bin` (the launchers) are in neither list, so a sandboxed `prepare` cannot write its staging directory today.

- `prepare`, `install` and `rollback` run outside the sandbox the way `DEPLOY_COMMANDS` do: a stable verb `poolhouse
  autostart prepare|install|rollback` calls `fleet.autostart.main`, and the profile's `excludedCommands` gains the three
  forms `poolhouse autostart prepare`, `poolhouse autostart install --manifest` and `poolhouse autostart rollback`.
  `prepare` prints the `install` form with `--manifest PATH --digest SHA`; the person's pasted line is that same line.
  `status` and `verify` stay inside the sandbox.
- How the harness matches an excluded string against a command with further arguments or a compound tail is unverified
  (docs/agent-sandbox.md:195). Slice 1 drives the harness before any entry is added and records, for each form, a run with
  extra arguments and a run with `; touch MARKER`, as VERIFIED or as the matching it found. If a trailing command runs
  outside the sandbox, the three entries are an owner's decision (A6), not a build step.
- `STATE_DENY_WRITE` gains `autostart`, `bin`, `authority.json` and `authority-audit.jsonl`, so `edit_rules` also refuses
  the Edit and Write tools there. The profile as written does not refuse the file tools at those paths.

## Findings in the current code

Each is fixed by this design; none is fixed by this document.

| Id | Finding | Fix |
| --- | --- | --- |
| F1 | `--report=MODULE:CALLABLE` makes the daemon import any module and call the named function with no arguments (`fleet/device.py:120`, `fleet/daemon.py:782`); `_POOL_ARG` accepts any text up to 512 characters for it (`autostart_verify.py:32`) | the agent path accepts no `--report`; the person path restricts it to `poolhouse.` modules in the same slice |
| F2 | `install` records its audit row through `authority.record` without `by`, which writes `by: person` | the agent path passes `by="delegated"` and the agent name; `record` already lets a caller set `by` |
| F3 | `_undo` takes `destination`, `name` and `sha256` from `backups/<id>/index.json`, and the label and destinations from `ledger["last"]`; a tampered file makes rollback write or delete any path the account owns and boot out any label | agent rollback re-derives all of them from the fixed table and compares (Rollback by an agent) |
| F4 | `launcher_problem` checks the head of the launcher and an assigned `root`; the rest of the file may be any code | whole-file equality with the text the runtime tooling writes (Exact launcher) |
| F5 | `~/.poolhouse/autostart`, `~/.poolhouse/bin` and `authority.json` are not in `STATE_DENY_WRITE`, so Edit and Write are not refused there | add them (Wiring) |
| F6 | `schtasks /Create /XML` reads the staged XML after the hash was checked | install from a private copy of the verified bytes, made with `O_EXCL` and mode 0600 |
| F7 | `allowed_dir` and `environment_for` read `$HOME` and `POOLHOUSE_HOME`; an agent that sets them installs under a home it controls | Admission steps 1 and 3 |
| F8 | the file write follows a directory check made earlier in the call | repeat the realpath check, open the unit directory with `O_DIRECTORY\|O_NOFOLLOW`, write the temporary file and rename through that descriptor |
| F9 | `_argv_problem` compares the launcher's file name with a path built from `argv[0]`'s own directory (`autostart_verify.py:59-68`); `prepare --launchers` takes any absolute directory (`autostart_prepare.py:181-183`); `launcher_problem` returns an empty string for any owned regular `.exe` outside a checkout (`autostart_manifest.py:216-217`) | Launcher path and Exact launcher rules; the verb takes no `--launchers` |
| F10 | `environment_for` copies `HF_HOME` from the caller's environment into the unit (`autostart_prepare.py:90`); `problems()` compares the manifest with the same call, so the caller's value passes | Admission step 1 and the Environment rule |
| F11 | `_undo` removes or overwrites `destination` without comparing it with the recorded sha256 (`autostart_apply.py:147-150`), and nothing undoes `loginctl enable-linger` (`autostart_apply.py:231`) | Rollback by an agent, steps 3 and 5 |
| F12 | `_backup` creates `backups/<id>` with `exist_ok=True` and writes unit-named files into it through any link already there (`autostart_apply.py:120-121,131`) | Backup rule |
| F13 | `install --manifest` takes any path and `problems()` treats `path.parent` as the staging directory, so any private directory the account owns passes (`autostart_apply.py:206`, `autostart_verify.py:35`) | Location rule |
| F14 | `_record` keeps the replaced entry as `previous` without saying who installed it (`autostart_apply.py:175-183`) | the ledger gains `via`; Overwrite and Rollback read it |

## Attack first

Author's pass, 2026-10-16, revised after review round 1. Each row is a defence and a test that goes red when the defence is
removed. Tests are in `tests/test_autostart_delegated.py` unless named. "Fixed" means fixed in this design; "Accepted" means
a named risk for the owner.

| Attack | Defence | Test that fails without it | Status |
| --- | --- | --- | --- |
| Copied or cloned manifest (another account, another device, a restored disk image) | `device`, `platform`, the recorded `HOME` and state root against `environment_for`, the Location rule, the staging directory owned by the account and mode 0700, Admission step 3 | `test_a_manifest_copied_from_another_account_is_refused`, `test_a_manifest_for_another_device_is_refused_to_an_agent`, `test_a_manifest_outside_the_staging_directory_is_refused_to_an_agent`, `test_a_manifest_whose_id_is_not_its_directory_name_is_refused_to_an_agent` | Fixed. A cloned disk image has the same device id: the manifest then installs the same two fixed units the clone's own `prepare` would, with the clone's launchers |
| Replay of an old manifest | the ledger `used` list, `backups/<id>` absent, the one-hour age and the Order rule | `test_an_installed_manifest_is_refused_again`, `test_a_manifest_older_than_an_hour_is_refused_to_an_agent`, `test_a_manifest_older_than_the_last_install_of_its_role_is_refused`, `test_an_existing_backup_folder_for_the_id_is_refused` | Bounded. The age and order rules read fields the manifest's writer sets; the `used` list (200 ids) is the only guard against reusing an id. What a replay installs is the two fixed units a fresh `prepare` stages |
| Forged or edited manifest or unit between `prepare` and `install` | re-rendering every unit from the fixed role (the defence against a forgery), each unit's sha256, the digest over the canonical manifest (detects a change after `prepare` printed it), one read held in memory, the 0700 staging directory, the profile's write denial | `test_a_manifest_and_units_edited_together_with_a_matching_digest_fail_the_re_render`, `test_a_manifest_edited_after_prepare_fails_the_digest`, `test_a_staged_unit_swapped_after_the_check_is_not_installed` | Fixed |
| A malicious agent crafts a unit for persistence (extra argv, `--report=os:system`, another role, a launcher with appended code, a copy of a real launcher in a directory it can write, `--system`, a destination outside the unit directory, a foreign label, a changed environment, a model cache directory it controls through `HF_HOME`) | Agent rules: fixed roles, exact argv, Launcher path, Exact launcher, Environment, exact destination; the service manager is called only with the fixed label | parametrized `test_a_crafted_manifest_is_refused_before_anything_runs` (asserts the recorder saw no command and the unit directory is unchanged), `test_a_launcher_with_appended_code_is_refused`, `test_a_byte_identical_launcher_in_a_writable_directory_is_refused_to_an_agent`, `test_a_windows_launcher_that_is_not_the_runtime_copy_is_refused_to_an_agent`, `test_an_hf_home_in_the_callers_environment_is_not_carried_into_the_unit`, `test_the_agent_prepare_verb_takes_no_launchers_argument` | Fixed. A runtime deployed under `runtime.deploy` is trusted code and the units run it: Accepted (A1) |
| A launcher replaced after the install | `~/.poolhouse/bin` is outside the sandbox's write list and in `STATE_DENY_WRITE`; `status` runs `launcher_problem` | `test_edit_rules_refuse_the_launcher_directory` (`tests/test_agent_sandbox.py`) | Accepted (A1): the launcher is compared once, at install; `runtime ensure` rewrites it; `status` does not repeat the byte comparison |
| An agent compromised by injected text (a board message, a page, a file) | the effect is the two fixed units on user scope, running the pinned launcher with the fixed environment (Launcher path, Exact launcher and Environment hold this); a widening (a role, a flag, `--system`, another path) is refused as a crafted manifest; no prompt exists to talk the person through; the audit row and `status` show it; one-hour age and three installs an hour; `rollback` undoes it | `test_an_injected_widening_changes_nothing`, `test_every_delegated_pass_and_refusal_is_audited_with_agent_and_digest` | Fixed for widening. Accepted (A2): injected text can cause the permitted effect, installing the two units |
| Downgrade, `prod` to `dev` | loosening is a person at a terminal typing `yes`; a lead agent may only tighten; `preset dev` over a stricter registry takes the same prompt; `POOLHOUSE_AUTHORITY_FLOOR` only tightens; `authority.json` is write-denied to the sandbox and the file tools | `test_prod_keeps_the_autostart_gate_a_persons` (extends `tests/test_authority.py::test_new_gate_honors_an_existing_prod_preset`), `test_an_agent_cannot_loosen_the_autostart_gate`, `test_edit_rules_refuse_authority_and_autostart_state` (`tests/test_agent_sandbox.py`) | Fixed. Unsandboxed code running as the account can edit `authority.json`: Accepted (A3) |
| A subagent or helper tries to pass the gate | `authority.require` accepts only a name the node recorded as a main session: `parent/name`, an unknown name and an unnamed process raise `HumanRequired`; the refusal is a row | `test_a_helper_identity_is_refused_install_and_rollback`, `test_an_unnamed_marked_process_is_refused` | Fixed for those three. The name comes from `POOLHOUSE_WORKSPACE_AGENT`: a helper whose environment carries its lead's name passes as the lead: Accepted (A7) |
| Approval fatigue | the delegated path asks nothing; the person path's typed id is unchanged; an agent cannot raise the loosening prompt (an agent-marked process is refused before it asks); repeated agent installs stop at three an hour counted over a time window; refusals past twenty an hour leave one counted summary row | `test_a_fourth_delegated_install_in_an_hour_is_refused`, `test_other_gates_audit_rows_do_not_reset_the_rate_count`, `test_refusals_past_the_cap_leave_one_counted_summary_row`, `test_an_agent_marked_process_cannot_raise_the_loosening_prompt` | Fixed in code. An agent asking the person in chat to delegate the gate again and again is a social act no code stops: Accepted (A4) |
| Takeover by whoever is merely present (a process that sets the markers, an unattended terminal) | the path needs the installed copy on the real home, a name the node holds a main-session token for, and the gate delegated; a terminal with no marker still needs the typed manifest id; `prod` leaves it a person's | `test_a_process_that_only_sets_the_markers_is_refused`, `test_the_delegated_path_needs_the_installed_copy_on_the_real_home`, `test_the_person_path_still_needs_the_typed_id` | Accepted (A3, A7): the gate is an environment test (docs/person-delegation.md, section 1), and code running as the account that can read the session records can write a login unit without it |
| Symlinked `LaunchAgents` (or `~/.config/systemd/user`, or a parent of either), or a symlink swapped in after the check | `destination_problem`'s realpath chain, repeated after the lock is taken, then a write through a directory descriptor opened with `O_NOFOLLOW`; a symlink or non-regular file at the target is refused | `test_a_symlinked_unit_directory_is_refused_to_an_agent`, `test_a_directory_swapped_for_a_symlink_after_the_check_is_not_followed` (a seam runs between the check and the write) | Fixed |
| An agent install over a person's customised install, which the agent then cannot undo | the Overwrite rule: an agent replaces only a unit whose ledger entry has `via` `delegated` and an unedited destination | `test_an_agent_install_over_a_persons_install_is_refused`, `test_an_agent_install_over_an_unrecorded_unit_is_refused` | Fixed |
| Rollback abuse to remove or overwrite the person's other units or the person's edits | agent rollback only for a `delegated` ledger entry; label, unit names and destinations re-derived from the fixed table and compared with the ledger; destination sha256 equal to the recorded sha256 before any removal or restore; backups restored only for a `delegated` `previous` that equals `render()` of the fixed role; `index.json` fields never used as paths | `test_a_tampered_ledger_naming_another_label_boots_out_nothing`, `test_a_tampered_backup_index_destination_is_never_written`, `test_a_backup_that_is_not_a_rendered_unit_is_not_restored`, `test_an_agent_cannot_roll_back_a_person_s_install`, `test_rollback_refuses_a_unit_edited_after_install`, `test_an_agent_does_not_restore_a_previous_record_a_person_made`, `test_rollback_leaves_every_other_unit_in_the_directory_alone` | Fixed |
| Rollback leaves the account's login lingering on | the install records `linger_before`; rollback runs `loginctl disable-linger` when it was `no` and nothing remains installed | `test_rollback_disables_the_linger_the_install_enabled`, `test_rollback_leaves_a_linger_that_was_already_on` | Fixed for a delegated rollback; the person's rollback leaves it on: Accepted (A5) |
| A sandboxed command matched by an excluded string runs more than the verb | none until the matching is verified (Wiring); each verb checks its own arguments and the Admission order whatever it is given | `test_prepare_prints_a_command_the_profile_excludes` (`tests/test_agent_sandbox.py`); the harness run of slice 1 | Accepted (A6) until slice 1 records the result |
| `--report` or another daemon option used to run code at login (F1) | no option but `--port=8770` on the agent path | covered by the crafted-manifest parametrization | Fixed |
| Audit forged as a person's (F2), or lost | `by: delegated` and the agent name; an exception writing the pass row stops the install | `test_a_delegated_install_is_not_recorded_as_a_person` | Fixed |

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
  sandbox, can already edit `authority.json`, read the session records or write a login unit directly; for it the gate adds
  nothing.
- **A4.** Nothing stops an agent pressing the person in chat to delegate the gate.
- **A5.** `loginctl enable-linger` on Linux user installs makes the units start at boot, before login. A delegated rollback
  disables it when the install turned it on. The person's rollback leaves it on, as today.
- **A6.** The harness's matching of an excluded string against a command with further arguments or a compound tail is
  unverified (docs/agent-sandbox.md:195). If it matches by prefix, a sandboxed command that starts with one of the three
  strings runs outside the sandbox in full. The three deploy commands have the same property today; this design adds three
  strings.
- **A7.** The lead identity is an environment assertion with a lexical guard. `authority.workspace_lead` reads the name from
  `POOLHOUSE_WORKSPACE_AGENT`, checks it against a name pattern, and finds a token file for it with no parent file; any
  process of the account that sets `CLAUDECODE` and a lead's name passes, and a helper whose environment carries its lead's
  name is the lead for this gate.

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

## Tests

New `tests/test_autostart_delegated.py` (the attack table above plus the happy path on each platform with the service
manager recorded: a lead agent installs both roles, `status` is `current`, `rollback` restores, each step audited).
`tests/test_authority.py` gains the gate and the `prod` default. `tests/test_agent_sandbox.py` gains the
excluded commands, the new deny paths (`autostart`, `bin`, `authority.json`, `authority-audit.jsonl`) and
`test_prepare_prints_a_command_the_profile_excludes`. The existing
`test_autostart_apply.py` agent-refusal tests keep passing under the person floor; the delegated tests clear
`POOLHOUSE_AUTHORITY_FLOOR`. No test calls the real service manager, the real keystore or an administrator prompt.

## Build slices

1. Gate, sandbox profile (deny paths, then the harness run for the excluded forms, A6) and `docs/agent-sandbox.md` (small).
2. `autostart_agent.py`, the digest in `prepare`, agent rules, descriptor writes, rollback re-derivation, the rate window,
   audit rows and the refusal counter, the linger record, the `poolhouse autostart` verb (the largest; stays under the
   900-line limit by living in its own module).
3. Documents: the AGENTS.md section 1 bullet, `docs/install.md` "Starting at login", `docs/person-commands.md`,
   `docs/pool.md` 2.5(c), the `autostart_cli.py` help text ("a person at a terminal only"), HANDOFF.md.

Replacement for the AGENTS.md section 1 sentence that begins "`python -m poolhouse.fleet.autostart prepare`":
"`python -m poolhouse.fleet.autostart prepare` is open to an agent. `install` and `rollback` for the roles `pool-daemon`
and `runtime-ensure`, at user scope, with the fixed arguments and state-directory launchers `prepare` writes, pass a lead
agent while the gate `autostart.install` is delegated (default under `dev`, a person's under `prod`); the manifest must be
the one `prepare` staged for this device, is checked against the digest `prepare` printed (an integrity check, not a
signature) and is re-rendered byte for byte from the fixed roles; every use is audited, and an agent overwrites or rolls
back only what an agent installed. `--system`, `llama-cpp-ensure`, any other argument and `install`'s typed confirmation by
a person stay as they were." That file is a guarded rule source, so the replacement goes in with the build under exact-SHA
independent approval.

## The person's journey

A person who is bad with tech tells an agent to start Poolhouse at login; the lead runs `prepare` and `install` and says
what it did. The terminal step that `docs/person-commands.md` lists as blocking the journey goes
away, and the person gains no prompt, flag or wait. The person path (`install` at a terminal with the typed id) is
unchanged, so a person who prefers it loses nothing. What the person can see afterwards: the device report's
`autostart: current` word, `status --json` naming who installed it, and the audit log.
