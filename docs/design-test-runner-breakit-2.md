# Second break-it pass on the test runner and sharder design

Subject: `docs/design-test-runner.md` as it stands on `origin/0.3dev` at `e399a1d4a` (identical at `e97621879`, checked with `git diff`),
the revised sections 10, 5.3, 6 and 3.5 above all. Reviewer: a non-author agent told to defeat the design (AGENTS.md section 1, "Attack
first"; author of neither the design nor the first pass `docs/design-test-runner-breakit.md`). The design is not edited by this pass.
Finding numbers F1 to F30 are the first pass's; the findings of this pass are N1 to N18. Section numbers without a file name are the
design's.

Evidence tags: **[probe]** run in this pass on this Mac and its output quoted, **[code]** read in this tree, **[doc]** stated in a
document of this repository or in a manual page, **[reasoned]** follows from the text and was not executed, **[not run]** a probe that was
refused or could not be made, with the reason. The machine: macOS 26.6 arm64, Python 3.14.7, git 2.51.2, expat 2.7.4, load average 87 to
93 during every timing (other agents were running), so a time below is an upper bound and a byte count or a pass/fail is not affected.

## 0. Verdict

**Step 8 stays blocked until the text absorbs N1 to N8, N10 and N11; none of them needs a redesign.** The structure of section 3.5 held
every probe I could aim at it: the executor-owned `exec/` cannot be written, aliased or linked into by a confined child on macOS; the
held descriptor, the exclusive no-follow create and the descriptor permit all behaved as 6.6 says. What failed is what surrounds the
structure: the decision whether a project is named (N1, N2, N3), what a named child can reach (N4), what a confined run leaves behind
(N5), the numbers that are supposed to bound a run (N6, N7), and three parser and probe details (N8, N10, N11). Steps 3(b) and 3(c)
build `own`, the settings file and confined local runs, so N1 to N6 must be in the text before step 3 is built, not only before step 8.

Of the thirty first-pass findings, 19 are absorbed, 11 are absorbed in part, none is untouched (section 2). The partial ones are
partial because of the findings below, named per row.

### Ranked findings

| # | Severity | Finding | Breaks |
|---|---|---|---|
| N1 | Critical | A handed directory becomes "named" by pointing its `.git` at a named repository: the git common directory is read from attacker-controlled pointers | 4.8, 10.2 "Local runs", A22, A29 |
| N2 | Critical | The runner runs `git` unconfined in a repository it has not yet classified; the repository's own git configuration is code | 6.3, 5.4, 10.2, check J |
| N3 | High | The person's yes for `own`, `allow` and a wider `target` cannot be verified; the settings are an ordinary file; `target` and `--on` move source with no gate | 4.8, 6.2, A7, A11, A19, A24 |
| N4 | High | A named child reads the signing key, the store and the settings: signed `named` evidence is forgeable by the code it is evidence about, and naming binds a repository, not its content | 3.5 rule 5, 5.3, 5.9, A29, A32 |
| N5 | High | A confined local run may write the project directory (`.git`, `testfarm.toml`, hooks): the next unconfined action runs what it left | 10.2 "Local runs" |
| N6 | High | The hang-guard refusal is satisfied by `inf`, by one margin, and before the first unit finishes; a unit that prints a byte at a time is never ended | 5.10, 4.1 |
| N7 | High | The receiver owns no time, disk or memory budget for a sender: the clamp has no number, disk shares add to more than the disk, macOS cannot bound memory | 10.3, 10.6, 15, A4, A21 |
| N8 | Medium | The XML DOCTYPE check is bypassed by UTF-16 without a byte-order mark; the parser's other exceptions are not all caught | 6.6 items 4 and 5, 10.5, A14, A17 |
| N9 | Medium | The Parser zone has no stated output format, costs 62 ms a start, and the tar unpack of hostile bytes stays in the daemon | 3.5, 6.2, 6.6 |
| N10 | Medium | `[requires] module:NAME.sub` imports the parent package on the device: the sender picks which installed module's code runs | 6.5 |
| N11 | Medium | Sentinels carry no nonce field, are recognisable, do not test confinement, and `suspect` costs a liar nothing | 6.4, 10.9 item 5, A9 |
| N12 | Medium | Lease delegation by process id: the id the OS reports names a dead process, ids recycle, the model server can outlive the group; a descriptor the runner forgets is a capability | 5.5, 3.5 rule 3 |
| N13 | Medium | Untracked files travel when a unit's dependencies are "the whole tree"; the `own` sentence says "your devices", which in dev mode is anyone who joined | 6.3, 4.8, A19 |
| N14 | Low | The runner walks and hashes directories a child wrote, by name; the no-follow create is only as good as its directory descriptor | 3.5 rule 1, 4.4, 6.6 |
| N15 | Low | Loopback denial on macOS is a snapshot of ports and `sandbox.Net` has no deny-ports mode | 10.2, 10.9 item 4 |
| N16 | Low | A preset's `[confine]` names toolchain homes that hold credentials and forwards variables whose values are secrets | 7, 10.5 |
| N17 | Low | Numbers and repository knowledge left in or imported by the core | 15, 4.1, L0 |
| N18 | Low | Exit-code table gaps, a false failure that never makes a device suspect, an unnonced frame, an "or merge-base" | 11, 6.4, 10.5, 5.9 |

Two decisions are the owner's alone; each comes with the fix as its first option.

1. **How a person's yes reaches `own`, `allow` and a widening `target`** (N3). Option 1: the host supplies the answer as evidence the
   harness produced (for Poolhouse, the structured approval question that `scripts/hooks/person-consume` already consumes,
   `docs/person-delegation.md`), and without a host the command refuses and prints the one-line command for a person's terminal; a
   terminal answer is never accepted from a process that is not the person's own session. Option 2: accept a controlling-terminal
   yes (weaker: [probe] below shows an agent gives a child a terminal and types the answer).
2. **What `named` means for evidence** (N4). Option 1: every child, named or not, is denied the Owner zone by exact path (a narrow deny
   on an otherwise open profile, [probe] below), and landing evidence comes from a run of the exact tree by a session that did not author
   it. Option 2: keep named children unconfined, state in 10.9 that signed `named` evidence proves "the runner ran it", never "the
   author did not forge it", and drop the claims that A29 and A32 make for it (weaker).

## 1. Method

Read in full: the design, the first pass, `AGENTS.md`, `docs/agent-policy.md`. Read as code or documents to test the design against
what exists: `src/poolhouse/sandbox/{seatbelt,policy,backend}.py`, `docs/person-delegation.md`, `docs/glossary.md`, `git-config(1)`.
Each of F1 to F30 was attacked again with its original attack against the new text (section 2). Then the surfaces the rewrite created
were attacked: the `own` flow, the store key and domains, sentinels, descriptor permits, lease delegation, the Parser zone, the
no-follow create and `fstat` reads, exit-code precedence, the hang-guard refusal, confined local runs, and the numbers.

Probes were built on the repository's own profile generator (`poolhouse.sandbox.seatbelt.profile`, imported from this worktree's
`src/`), so a result is about the backend the design says it will use. All fixtures are in the session scratch directory.

## 2. Verdicts on F1 to F30

"Absorbed" means the original attack, run against the new text, no longer works; "partly" means the named residue does.

| F | Verdict | The original attack against the new text |
|---|---|---|
| F1 executor follows links | **absorbed** | `{idfile}` and the config are in `exec/`, which no profile lets a child write: [probe] a child could not append through a symlink to `exec/ids` (EPERM), could not hard-link it into the tree (`ln` EPERM, with and without `deny_write`); an APFS clone of a file outside the profile was refused too (`cp -c` EPERM). The result file is created exclusive and no-follow and the executor reads its held descriptor: [probe] a name pre-planted as a dangling or live link is refused (EEXIST); after the child unlinked the name, made a FIFO and then a link, the held descriptor was still the original regular file; a child that had opened the path before the swap kept writing into it and the executor saw the bytes. Residue: N14 (an intermediate directory component is followed; walks of child-written trees). |
| F2 CI partitions | **absorbed** | `divide` reads the sorted LIST, the config and the seed only; the toy case (units of 4, 3, 2, 1 seconds, two shards) covers each unit once; `--verify-cover` fails on a missing list, a doubled unit or unequal digests. Nothing found. |
| F3 empty, failed, filtered LIST | **absorbed** | A failed or empty LIST is status 4, `ignored N lines` prints, a run of only skips is `nothing ran`. Nothing found; N6 notes that `[list] allow_empty = true` is the project's own switch, as intended. |
| F4 unsound presets, `exit` hides skips | **absorbed** | `files` and `npm` give the whole tree, `exit` passes are not stored, an empty glob is a `check` error. Residue: N13 (what "whole tree" sends) and the per-invocation stamp of the transported set costs a walk per serial invocation (N14). |
| F5 handed code runs unconfined | **partly** | The decision is in the text (confined by default, domains, signed entries) and the probes show the profile holds against a child. It is bypassed around the child: N1 (the project is "named" by a pointer), N2 (git runs first, unconfined), N5 (the child writes the directory the next run trusts), N4 (named children hold the key), N3 (the settings are writable). |
| F6 status ranks a failure first | **absorbed** | A unit that failed and a vanished device give 1; `not started` from a device is `not run (device refused)` and is reassigned. Rows stream, so a vanished device loses nothing already ended. Residue: N18 (statuses missing from the table). |
| F7 re-run erases a failure | **partly** | A device failure is final and a re-run only adds `reproduced` or `did not reproduce`. But a device whose failures never reproduce on the same-class sender is not `suspect` (only a sentinel, a canary or a second-source mismatch makes it so, 6.4): a hostile member turns every run red for free. N18. |
| F8 config read again, start fields | **absorbed** | The start fields are closed, the config is hashed and parsed once from the same bytes in memory, `{step:../x}` is refused. Nothing found (the one read of `tree/testfarm.toml` happens before any child exists). |
| F9 `{cache}` and `{shared}` | **absorbed** | Units get them read-only, the project id is the sender's opaque hash, the manifest is checked before reuse. Residue: how the manifest is built (N14). |
| F10 network and loopback | **partly** | Deny is the default on both platforms. [probe] with `Net.loopback()` plus `(deny network-outbound (remote ip "localhost:PORT"))` the daemon stand-in was refused, IPv6 too; a service started after the profile was built was reachable. N15. |
| F11 device claims, passes are advice | **partly** | Second source, pinned platform class, sender-observed capacity and cost, install-instance id, `agreed by 2 members`: all present. Not absorbed: the sentinel (N11) and a clone that copies the disk also copies the instance id (A1 below). |
| F12 sender bound, queue, stalls | **partly** | The sender's bound on a shard, queue time apart, a liveness count across completed waits: present. Not absorbed: the hang-guard refusal is bypassed (N6). |
| F13 hold-and-wait | **partly** | All-or-nothing `acquire`, `Grant.sub`, steps before units, single flight takes no slot: present. The delegation by process id has the holes of N12. |
| F14 project file and default choose the target | **partly** | The file has no target key, the default is `local`, a tree goes only for a named project. Not absorbed: naming is spoofable and unverifiable (N1, N3), `target` and `--on` need no yes (N3), "whole tree" sends untracked files (N13). |
| F15 parsing of sender bytes | **partly** | The Parser zone, literal patterns and `[requires]` grammar are in. Not absorbed: the UTF-16 DOCTYPE bypass and uncaught exceptions (N8), the unstated output format and cost (N9), the import in `module:` probes (N10), and no memory bound on macOS (N7). |
| F16 id grammar | **absorbed** | Categories Cc, Cf, Zl, Zp, Co, Cs, Cn, NFC, `ids_after`, NUL-separated `{idfile}`. `a --evil`, `a\u0085--evil` and a tag-character id are refused. Nothing found. |
| F17 text is data | **absorbed** | Sanitised and framed, local output included, summary after the frames, `untrusted_*` in `--json`. Residue: N18 (frame markers carry no per-run nonce; a prefix on every line makes a forged marker visible, so Low). |
| F18 numbers | **partly** | Numbers from LIST, rows and devices are range-checked. The same class survives in the project's own file: TOML parses `inf`, `nan` and `1e999` as floats, and `silence_margin = inf` satisfies the refusal (N6). |
| F19 the key holds every input | **absorbed** | The resolved unit table, typed arguments, mode bits, launcher, `junit_id`, `batch`, `tree.exclude`, device-claimed digests for `remote`. Nothing found. |
| F20 canary | **absorbed** | OS entropy, always a canary for the first `remote` reuse, one more fresh run before a mismatch, no marker. Residue: the project's `canary` rate has no range (N17). |
| F21 forgeable fail-first evidence | **partly** | Base-tree binding, `unverified` fixtures and an exempt line that names an id of the range: absorbed. The forgery by "the agent whose claim the check exists to test" is not: that agent's tests run as a named child and can read the key (N4). The "or the merge-base" in 5.9 lets the author pick the older base (N18). |
| F22 policy knob, `verify_list` | **absorbed** | Both are gone; the policy lists capability limits only. Nothing found. |
| F23 state inside the pool's secret root | **absorbed** | The state location is outside `~/.poolhouse` and named by exact path. Nothing found. |
| F24 environment | **absorbed** | Named children inherit the environment minus the runner's names; confined children and every device child get the allowlist plus the permit variable. Residue: what the allowlist forwards (N16). |
| F25 tree set, git configuration, hash | **partly** | System and global git configuration are disabled and the hash is length-prefixed. The repository's own configuration is not (N2), and the claim "`core.excludesFile` unset" cannot be done by environment alone. |
| F26 real-device proof | **absorbed** | Steps 8 and 10 prove it on this Mac and the WSL device with `scripts/pool-check`, the loopback harness stays the wire test. Nothing found. |
| F27 retention | **absorbed** | The idle rule covers store, history and evidence; `check` prints the share of unstable ids. Nothing found. |
| F28 several users | **absorbed** | A per-user 0700 slots directory and an accepted risk (10.9 item 2). Nothing found. |
| F29 two timeouts | **absorbed** | `test_timeout` and a unit `timeout` are two keys with two report lines; rows of a killed invocation are kept. Nothing found. |
| F30 text gaps | **absorbed** | All fourteen items are in the text. [probe] `scripts/test all tests/test_glossary.py`: 37 passed on this tree (item 14). Item 7 (one `allow` act) is consistent in words and not in mechanism: N3. |

## 3. New findings

### N1. A handed directory becomes "named" by pointing its `.git` at a named repository (Critical)

**Attack.** 4.8 stores a project as "the realpath of a repository's git common directory" and 10.2 runs a named project unconfined as the
user. The common directory is read from files the repository supplies. [probe] with a named repository `named/` and its worktree:

- A directory `hostile1/` holding one file, `.git`, with the text `gitdir: <path>/named/.git`: `git rev-parse --git-common-dir` printed
  `<path>/named/.git`. The delivery is any archive, shared folder, `rsync` or download that keeps `.git`, or a relative path
  (`gitdir: ../../Documents/repos/<name>/.git`) a hostile archive can guess from a common layout.
- A directory `hostile2/` holding `.git/HEAD`, empty `.git/refs`, `.git/objects` and `.git/commondir` (text: the named `.git`): the
  same answer, with a `--git-dir` of its own.
- A directory with no repository below a parent repository (a dotfiles repository on the home directory, a stray `.git` in `/tmp`):
  the common directory and top-level printed were the parent's. A person who names `~/Downloads/app` while the home directory is a
  repository names `~/.git`, and every directory below the home without its own repository is then named.
- `GIT_DIR=<named>/.git` in the caller's environment overrode the working directory entirely.

The hostile project's `testfarm.toml`, LIST, steps, hooks and tests then run unconfined as the user, and its entries are written in the
`named` domain, which landing evidence accepts. Nothing else in the design is needed for this; it defeats F5, F14, A22 and A29 at the
first line. A linked worktree of a named repository is named by design (4.8), so any branch checked out in it is trusted whoever wrote
it: the identity is the repository, not the content (N4 treats this).

**Breaks.** 4.8 `named`, 10.2 "Local runs", A22, A29, F5.

**Change.** The identity of a project is what the person pointed at, read without git: the realpath of the root, the `(st_dev, st_ino)`
of the root directory, and, for a repository, the directory `<root>/.git` itself when it is a directory holding `HEAD`, `objects` and
no `commondir` file. A linked worktree (`.git` is a file) is accepted only when `<gitdir>/gitdir` names this `.git` file back (git
writes the backlink at `worktree add`) and `<gitdir>` lies under `<common>/worktrees/`; the common directory is then recorded with the
worktree's own admin directory. Anything that fails these checks is unnamed, not an error. The root must be the work-tree top-level
(`<root>/.git` exists): a root below a repository is its own project, named only by naming it, and the root of a run is found by
walking up only as far as the first `testfarm.toml` that sits at a top-level. When `git` itself is run (N2) every `GIT_*` variable is
removed and `GIT_CEILING_DIRECTORIES` is the root's parent. Test: the four fixtures above, each required to be unnamed.

### N2. The runner runs `git` unconfined in a repository it has not yet classified (Critical)

**Attack.** 10.2 says the commands that run an unnamed project's code execute under the sandbox, and 6.3 says `git` is run with the
system and global configuration disabled. The repository's own `.git/config` is neither. It is code: `core.fsmonitor` is "the pathname
of the fsmonitor hook command", run when git refreshes the index (`git status`, `git diff` and others) and, set to `true`, starts a
long-lived `git fsmonitor--daemon` for the work tree on macOS [doc: `git-config(1)` on this machine, `core.fsmonitor`: "If set to true, enable the built-in file system monitor daemon for this working directory" and "Otherwise, this variable contains the pathname of the fsmonitor hook command"]; `core.hooksPath`, filter
drivers and `diff.external` are the same class. The runner needs git before it can know the project is unnamed: to find the root, the
common directory (N1), the tracked file list (6.3), the changed paths (`affected`, 5.4) and the store scope (5.3). Those calls are made
by the Runner zone, which is unconfined, so `poolhouse-test list`, `check` and `affected` in a repository whose `.git` came from a
download run the repository's command as the user before the profile of 10.2 exists. A22's test (`check` on a fresh clone runs its
LIST confined) passes while this runs.

**Evidence.** [not run] I could not execute the `core.fsmonitor` case: `scripts/hooks/claude-bash-guard` refused `git config
core.fsmonitor` and `git -c core.fsmonitor=...`, and `claude-edit-guard` refused a write to a scratch repository's `.git/config`, each as
"changes where hooks run or what git runs". I did not route around either. That the repository's guards treat the same setting as code
execution is itself the evidence that it is. A fresh clone's `.git/config` is not delivered by `git clone`; it is delivered by an
archive, a copy, a shared folder or a container volume, and by `.git/modules` and `worktrees` entries a tool creates.

**Breaks.** 6.3 (what is disabled), 5.4 and 10.2 (the order: classify, then confine), A22, check J.

**Change.** (1) Classification reads files (N1) and runs no git. (2) For an unnamed project every git call the runner makes runs inside
the same read-only profile as its children and with `-c core.fsmonitor=false -c core.hooksPath=<an empty directory the runner owns>
-c core.untrackedCache=false -c protocol.allow=never`, a fixed environment with every `GIT_*` removed, and `GIT_CONFIG_NOSYSTEM=1`
and `GIT_CONFIG_GLOBAL=/dev/null`. (3) For a named project the same overrides apply, because a named repository's configuration is
as writable by its own tests as by anyone. (4) Check J gains a fixture whose `.git/config` sets `core.fsmonitor` and `core.hooksPath` to
programs that write a canary: `list`, `check`, `plan` and `affected` must leave it unwritten.

### N3. The person's yes cannot be verified; the settings are a file the agent can write; `target` moves source with no gate (High)

**Attack.** The design leans on one idea in several places: `own`, `allow` and (by 4.8's own words) naming are "one typed yes", asked "at
a terminal, and through the question tool when an agent ran the command" (4.8, 6.2, 12, A7, A11, A24). Three holes.

1. *The CLI cannot know a person answered.* 3.3 and check F require that the runner reads no agent marker, so `own` cannot tell an
   agent's shell from a person's. There is no return path from the question tool to the CLI, and `docs/person-delegation.md` records
   why: "an environment test, not evidence the owner said anything". [probe] A controlling terminal is not evidence either. The agent's
   own shell has none (`open('/dev/tty')` fails with ENXIO, `isatty` false on all three descriptors), but a child it starts under
   `script(1)` gets `/dev/ttys004` and read a "yes" the agent fed it: `Type yes to name this project: yes`, `child read: yes`.
2. *The settings are an ordinary file.* `named`, `target`, `confine.read`, `resources` and `events.sinks` live in
   `~/.config/poolhouse/testfarm-settings.toml` and the user presets in `testfarm-presets.toml` (4.8, 7). 3.5 says only the CLI after a
   person's yes writes them; nothing makes that so. An agent (or a prompt-injected one) with a file-write tool appends its project to
   `named`, or adds `[confine] read = ["~"]` to the `pytest` preset, and the next run is unconfined. A7's test `an_agent_cannot_name_a_project`
   passes because the CLI refuses, not because the file is safe.
3. *`target` and `--on` are not gated.* `poolhouse-test target auto` "asks nobody" (4.8) and `--on pool` is a flag. A prompt-injected agent
   in a named project runs one of them and the tracked source goes to every accepting member of a pool in dev mode, where "every
   device on the network that runs Poolhouse in dev mode joins when it asks" (`docs/fleet.md`). The first send prints a file count to the
   agent, not to the person. A19 and A24 are answered by "the default is `local`", which is the agent's to change.

**Breaks.** 4.8, 6.2, 12, A7, A11, A19, A24, F14, F30 item 7.

**Change.** (1) One function, `person_gate(question) -> yes | no | unavailable`, supplied by the host and used by `own`, `allow` and a
`target` or `--on` that names anything but `local` for a project and device set not yet agreed. For Poolhouse the host is the structured
approval question and its harness evidence (`scripts/hooks/person-consume`); the library's default with no host is `unavailable`, which
refuses with the exact one-line command for a person's own terminal, and never accepts a terminal answer from a process the person did
not start themselves. Nothing waits: unavailable keeps the safe default (confined, `local`). (2) Settings and user presets are written
only by those commands, and the host adds both paths (and the state location) to its edit and shell guards; `check` reports a settings
file whose owner, mode or modification does not match the last write the CLI made. A7 then reads "holds against a hostile repository and
against an agent whose host guards those paths", which is the truth. (3) The `own` sentence names the device set a later send may use
("and may be sent to: <list>") and a send to a set not agreed asks again, once. (4) The path in the sentence goes through `report.plain`
(a directory name may hold a newline, a quote or a right-to-left override; macOS allows all three) [reasoned].

### N4. A named child can read the key, the store and the settings; naming binds a repository, not its content (High)

**Attack.** 5.3: "the children of a named project run as the user and are trusted", and the signing key is "readable by the user's
account and denied to every confined child". So a named child can read the key, write any signed entry in any project's directory (a
directory per project and domain is a path, not a secret), and edit the settings (N3). Who runs as a named child? The author agent's own
new tests; a hostile pull request or fork checked out in any linked worktree of a named repository (the identity is the repository,
4.8); a dependency's `build.rs` or `conftest.py`. These are the parties the evidence exists to check:

- F21's adversary, "the agent whose claim the check exists to test", writes `tests/test_new.py` whose import-time code reads the key and
  writes a valid `named`/`local` fail-first entry and a valid pass for its own tree. `failfirst check` and `workspace.testruns.verified`
  accept it (A32 says "evidence is produced by the runner and signed", which is true of the signature and silent about who holds the key).
- A worktree of repository A forges `named` passes for repository B, because one key signs all named projects.
- The replaced mechanism did not have this property for nothing: `DEV_TEST_REUSE_DIR` separated an agent's store from the owner's. 3.3
  says the domain and key replace it; for a named run by an agent they do not, because the runner reads no agent marker and the domain
  is `named` either way.

[probe] The remedy is cheap. A profile `(allow default)` plus `(deny file-read* file-write* (subpath <owner-zone>))` let a child read
and write everything else and was refused the key (`Operation not permitted` on read and on append, the key unchanged); a hard link and
an APFS clone of the key into a writable place were refused too (`ln` and `cp -c` EPERM, even without `(deny file-link)`).

**Breaks.** 3.5 rule 5 and the Owner/Own-child rows, 5.3 "Signed entries", 5.9, A29, A32, F21. Also AGENTS.md section 1 ("Tests never
reach the real machine"): a named child reading the real state root is the thing that rule forbids.

**Change.** Option 1 (stronger): the Owner zone (state location, key, settings, user presets, the pool's home, `~/.ssh`) is denied by
exact path to every child, named or not, by a narrow deny on an otherwise open profile (the Own child keeps the user's account for
everything else); the key is per project (derived from the master key and the project identity of N1 by the runner, which is the only
reader); the evidence a task is credited on is a run of the exact tree by a session that did not author it. Test: a named unit that
reads the key file, appends to the store and edits the settings fails on each; a worktree's unit cannot write an entry for another
project. Option 2 (weaker): leave it, add to 10.9 "named code can forge named evidence", and remove "proves" from A29, A32 and 5.9.
The narrow deny is a speed bump, not a wall: [not run] a child that asks LaunchServices (`open x.command`) to start an unconfined
program escapes it; that probe would open a Terminal window on the owner's screen. Name that residue in 10.9 either way.

### N5. A confined local run may write the project directory (High)

**Attack.** 10.2: the confined profile has "the project directory and the scratch tree writable". The project directory holds `.git`
(hooks, configuration), `testfarm.toml`, `conftest.py`, editor and shell-integration files. [probe] With `write=[project]` a confined
child wrote an executable `hooks/pre-commit` into a stand-in for the repository's metadata directory, appended to `testfarm.toml`, and
exited 0. (The stand-in has a neutral name because the bash guard refuses shell code that names `.git/hooks`.) The next unconfined
action then runs what it left: the person's `git commit`, a shell prompt that runs `git status`, the next `poolhouse-test run` after
`own`, or a different tool. A person who names the project after reading it has approved a different file than the one that will run.

**Breaks.** 10.2 "Local runs", 3.5 Child row ("the scratch tree, the step directory it owns" is what a device child may write; a local
child may write more), A22.

**Change.** An unnamed project's steps and units run in a scratch copy of the transported set, exactly as a device's do (the local
transport already runs "the same executor code a device runs"); the checkout is read-only or absent in the profile. The Runner,
being unconfined, can clone the tree cheaply (`clonefile` on APFS). Results and a step's declared products come back through the
runner's descriptors. If a copy is refused for cost, the minimum is `deny_write` on `testfarm.toml` and on the small files of the
repository metadata that execute (configuration, hooks, `info`), not the whole metadata directory: [probe] `Policy(deny_write=[<local
clone>/.git]).validated()` raises `exclusion: protected files must not have symlink or hardlink aliases` because a `git clone` from a
local path hard-links its objects, so denying the whole directory fails closed on a common repository. [probe] with `deny_write` on the
stand-in and on `testfarm.toml` the same child's writes were refused (`hook_rc=1`, `cfg_rc=1`) and an ordinary build file was written.

### N6. The hang-guard refusal is bypassed four ways (High)

**Attack.** 5.10: "A run with no hang guard is not allowed", enforced as "neither `[schedule] silence_margin` nor an explicit `timeout`
for every command the runner starts is set".

1. *A number is not a guard.* [probe] `tomllib.loads` parses `silence_margin = inf`, `total_margin = nan`, `timeout = +inf` and `a = 1e999`
   (`inf`) as floats. The key is "set", the type is right (4: "a wrong type is an error"), and no guard ever fires. `0` and negative
   values are not stated to be refused either. The design validates numbers from LIST and devices (10.4); this one is the project's own.
2. *One margin gives half a guard.* `silence_margin` makes only the no-progress guard. The total guard needs `total_margin`, which the
   refusal does not require, so with one key set a unit that prints a byte every few seconds runs for ever (local runs; a unit sent to a
   device is covered by "a unit with neither is not sent").
3. *The first unit has none.* "applied after at least one unit has finished": until then a hung first unit has no bound, and `init`'s
   measured first run (the run that writes `silence_margin`, 12) is exactly that run.
4. *The list of guarded commands is short.* LIST, `deps.command`, hooks, steps and units. Not named: `prune`, the Parser, the
   `[requires]` probes, git (N2), sentinels, the `launcher`.

**Breaks.** 5.10, 4.1, 12, A30, F12, F18.

**Change.** (1) Every number in the project's file (margins, `timeout`, `test_timeout`, `slots`, `queue_wait`, `canary`, `estimate`) is
finite and positive, a margin is at least 1, `canary` is in (0, 1]; anything else is status 4 naming the key. (2) The refusal needs
both margins, or a `timeout` on every command the runner starts, and "every command the runner starts" is a list the code builds from
one spawn function, with a test that fails when a spawn bypasses it. (3) Before the first unit has finished the run is allowed only
attached to a terminal or with `--timeout S` on the command line (a number whose source is the caller), and the report says `guard:
bootstrapping, <source>`; an unattended first run with neither is status 4. (4) The no-progress guard treats a byte of unit output as
progress only up to the total guard, which therefore always exists.

### N7. The receiver owns no time, disk or memory budget for a sender (High)

**Attack.** 10.3 "clamps `slots`, `timeout` and `env` to its policy", and 15 says a shard has "no deadline of its own". There is no
number or rule for the clamp. A compromised or merely hostile member sends a unit with `timeout = 1e9` (valid by N6) and a loop; it
holds the device's free background slots, its heat and its battery until the owner's own work is requested (the only thing that ends
it, 10.7) and, in a pool in dev mode, any device on the LAN that joined can be that member (A24). A4 answers "hogs slots" with the
background class, which orders work and does not bound it.

Disk and memory are worse:

- *Shares add to more than the disk.* 10.6 and 15 give the packed tree, unpacked tree, log, tee, result, junit, member and output bounds
  each "a share of the free scratch disk divided by the shards held". With one shard each is the whole disk. No sum is stated, so one
  shard may legally hold several times the free space, and a measurement taken "when it takes a shard" is stale when a unit writes.
- *A unit's own writes have no quota.* The scratch tree is the child's write set. Seatbelt cannot limit total bytes and `RLIMIT_FSIZE`
  is per file. A21's "bounded log" is the executor's log, not what a unit writes.
- *macOS cannot bound memory.* [probe] `setrlimit(RLIMIT_AS, 4 GiB)` and `RLIMIT_DATA` both fail with `current limit exceeds maximum
  limit` (EINVAL), and a 6 GiB anonymous map was not stopped. `sandbox.Limits` offers only wall, CPU, file and open-file ceilings
  [code]. 6.6 item 4 and 15 give the Parser zone a memory limit "derived from the device", which has no mechanism on the platform the
  owner uses. `RLIMIT_CPU` does work [probe: a 1-second limit ended a busy loop with SIGXCPU].
- *The pre-parse size bound has no source.* 6.6 "checks the byte size first" against nothing stated. [probe] `tomllib` costs about 20
  bytes of resident memory per input byte on tables (10 MB of `[[t]]` used 229 MB) and, under this machine's load, 16 s of CPU for 10
  MB; a 10 MB hostile config pins a parser for tens of seconds.

**Breaks.** 10.3, 10.6, 15, A4, A21, 6.6 item 4, F15.

**Change.** (1) The receiver's budget for a sender is a number the person gives at the `allow` act (the third source the policy accepts)
and stores with the sender: unit seconds, shard seconds, slot-seconds; with none recorded the device accepts a unit only with a bound it
measured itself on an earlier shard from that sender, and the first shard of a project is bounded by the executor's bootstrapping rule
(N6 item 3 with the device as the party). (2) 10.6 states one allocation of the free scratch disk per shard whose parts sum to at most
the shard's share, and the unit's scratch is a volume of that size (Linux `tmpfs size=`, macOS a sized sparse image) or, where it cannot
be, the executor ends the group when `statfs` shows the volume below the reserve it measured at the start, polled at the guard's
cadence. (3) The Parser's input cap is its memory budget divided by an amplification the build measures once per release (about 20 for
TOML here) and its time is `RLIMIT_CPU`; for a child's memory on macOS 10.9 gains the item "memory of a confined child is not bounded
by the platform". (4) A test sends a unit that writes until stopped and one that allocates until stopped, on both backends.

### N8. The XML check is bypassed by UTF-16; parser exceptions are not all caught (Medium)

**Attack.** 6.6 item 5: a junit document "is refused before parsing unless it decodes as UTF-8 and holds neither `<!DOCTYPE` nor
`<!ENTITY`". [probe] a UTF-16LE document without a byte-order mark (`<?xml ...?><!DOCTYPE t [<!ENTITY a "...">...]>` encoded
`utf-16-le`) decodes as valid UTF-8 (the NUL bytes are valid), contains no `<!DOCTYPE` string, passes the check, and
`ElementTree.fromstring` auto-detected UTF-16, expanded nested entities (a 1000-character value from three nested definitions) and
returned the tree. (With a byte-order mark the check refused it, by luck: 0xFF is not UTF-8.) expat 2.7.4 limits amplification; the
check is the design's only stated defence and a device with an older system expat has less. Separately 6.6 item 4 "catches
`RecursionError` and `MemoryError`": [probe] a 5000-digit TOML integer raises `ValueError` (the 4300-digit limit), not
`TOMLDecodeError`, `RecursionError` or `MemoryError`.

**Breaks.** 6.6 items 4 and 5, 10.5 "Sender bytes", A14, A17, F15.

**Change.** Refuse in the parser, not before it: [probe] setting `StartDoctypeDeclHandler` and `EntityDeclHandler` on a
`xml.parsers.expat` parser refused the UTF-8, UTF-16LE-without-BOM and UTF-16-with-BOM forms alike, and a NUL byte anywhere in a
document is refused (valid XML decoded from any encoding has none). The invariant is not "catch these two exceptions": any end of the
Parser other than a complete row stream followed by a clean exit (an exception, a signal, a timeout, a short read) is status 4 naming
the rule. Tests: the three XML forms; a TOML integer of 5000 digits.

### N9. The Parser zone has no stated output format, costs a process a parse, and the tar unpack stays in the daemon (Medium)

**Attack.** 3.5 and 6.6: the Parser "returns rows of a closed schema" on one descriptor. (a) The Runner then reads bytes from a process
that has just chewed hostile input. If the carrier is JSON, `json.loads` of nested data repeats the RecursionError class in the Runner;
if it is `pickle` or `marshal` it is code execution. The format is not stated. (b) [probe] a confined Python start that imports
`tomllib`, `json` and `xml.etree` took a median 62 ms (best 48 ms) under `sandbox-exec` against 36 ms unconfined, at load 90. One
confined process per result file is 600 invocations x 62 ms = 37 s, against the 5-second fixed-overhead rule (AGENTS.md section 5) and
`tests/test_runner_overhead.py`. (c) The tar and gzip handling of the sent tree (A13: "the checks of `shard_tree.py`, inflation before
any header") is the largest parser of hostile bytes in the design and runs in the Runner zone, inside the daemon that holds the pool's
keys.

**Breaks.** 3.5 Parser row, 6.2 "One server, one executor", 6.6, 10.5, A13, 5-second rule.

**Change.** State the wire: a flat tag-length-value stream with fixed tags and bounds, decoded by a function with no recursion and no
`loads`; the Runner never imports a deserialiser for it. Use one long-lived confined worker per shard (or per local run), restarted on
any error, with the cost measured in `test_runner_overhead.py`. Unpack the tar in that worker with write access to `tree/` only, and have
the Runner hash the unpacked files through descriptors it opens afterwards.

### N10. `[requires] module:NAME.sub` imports the parent package on the device (Medium)

**Attack.** 6.5 evaluates `module:NAME` with `importlib.util.find_spec` under `python -I`. The name grammar `[A-Za-z0-9_.-]+` allows a
dot. [probe] `find_spec('this.x')` printed "The Zen of Python" (the module ran), and `find_spec('xml.dom.minidom')` loaded `xml`,
`xml.dom` and `xml.dom.domreg`: `find_spec` of a dotted name imports every parent. The sender picks which installed package's
import-time code runs on the device, before any tree, any `allow` beyond membership, any confinement. A module whose import opens a
browser, starts a daemon or writes a file is a one-line request in dev mode from any member.

**Breaks.** 6.5, F15, A4.

**Change.** Refuse a dot in `module:` names, or look the name up with `importlib.machinery.PathFinder.find_spec(name)` on a top-level name
([probe]: it found the spec and loaded nothing), and run every `[requires]` probe inside the same profile as a unit.

### N11. Sentinels are recognisable, carry no nonce field, do not test confinement and cost a liar nothing (Medium)

**Attack.** 6.4: two generated units "the executor runs itself", one that must fail with a message "holding a nonce", one that must pass
echoing it; a failing device is `suspect` and "planned only generated sentinel units until one sentinel shard comes back right".

- *The nonce has no field.* 6.1 closes the start fields to `{id, project, config_sha256, tree_sha256, set, units}`; a nonce the device
  generates proves nothing and one the sender generates must travel.
- *They are recognisable.* The units are ids in the shard; a device that cheats on the project's units and answers the two generated
  ones correctly is indistinguishable from an honest one. The design says so (10.9 item 5) and then makes `suspect` depend on it.
- *Recovery is free.* A liar that is caught once leaves `suspect` by answering one sentinel-only shard, which it can always do. There is
  no memory, no expiry and no escalation, so the state has no cost against a deliberate liar and only the second source matters.
- *They do not test the confinement.* "The executor runs itself" says nothing about the profile: a device whose sandbox failed open
  (A25: "the device's statement that it can confine is its own build's") passes both.
- *Vocabulary.* `docs/glossary.md` defines **sentinel** as the open-join safety; the generated units need another word.

**Breaks.** 6.1, 6.4, A9, A25, F11, 10.9 item 5.

**Change.** Put the nonce in the start request (a listed field). Add a third control run inside the unit profile: a canary file the
executor creates outside the scratch tree, read by a generated unit that must be refused, which turns "a sandbox that fails open" from a
trusted statement into a measured result. A mismatch in a canary, a second-source comparison or a sentinel is sticky: the device is out
of every plan until a person's `allow` again (nothing waits: the rest of the pool runs); a sentinel only ends a *bad executor* state,
never a *mismatch* state. Rename the units "control units".

### N12. Lease delegation, the permit descriptor and the nested-run refusal (Medium)

**Attack.** 5.5 delegates a lease to "the process group of the invocation", verified by "the process tree it reads from the operating
system, or the operating system's record of which process owns the other end of the local connection".

- [probe] On macOS `LOCAL_PEERPID` is the process that made the connection: after process A connected and forked B (which holds the
  connection) and exited, the server read A's pid, and A was dead (`ProcessLookupError`), while B kept speaking. A broker that walks
  ancestors of that id finds nothing for a dead process (it fails closed, good) or, if it keys on the recorded id and the id has been
  recycled, credits an unrelated process. The text names neither the start-time check nor the exit notification.
- The model server that `serve up` starts under a delegated `gpu` lease can be a daemon that outlives the invocation; "the delegation
  ends with the group" ends the lease while the server keeps the GPU, and the next unit's lease is a second piece of work on the
  compute (AGENTS.md section 1).
- The nested-run refusal ("a `poolhouse-test` started inside an environment with that variable refuses") keys on an environment
  variable the child can unset; the result is a nested run that queues behind its parent until a guard ends it (a self-inflicted stall,
  Low).
- [probe] An inherited connected socket is usable inside the profile and a connect by path is refused (`PermissionError`), so the permit
  descriptor design works. But a descriptor the runner leaves open is a capability: with `pass_fds` including one opened on a file that
  is outside the profile, the confined child read it (`b'ORIGINAL\n'`), and `fcntl` showed fds `[0, 1, 2, 3, 4, 6]` open where the
  design intends fewer.

**Breaks.** 5.5 "Delegation, not inheritance", 3.5 rule 3, A26.

**Change.** Key a delegation on `(pid, start time)` taken from the OS, drop it on the exit notification (`kqueue` `NOTE_EXIT` on macOS,
`pidfd` on Linux) and refuse a request whose connecting process is dead; end the model server a delegated lease started with the group, or keep the
lease held until the server exits; make the nested-run refusal the broker's ancestor test (the same code), not an environment variable.
Check H gains: list the descriptors of a confined child and require exactly `0, 1, 2`, the permit and the result descriptor; a leaked
descriptor to a file outside the profile is a test failure.

### N13. What "the whole tree" sends, and what "your devices" means (Medium)

**Attack.** 6.3: the set is the tracked files "plus the untracked files a unit's DEPS or `[tree] include` names". A unit with no DEPS, a
`files` or `npm` unit, depends on "the whole tree" (1, 5.4). Whether the whole tree names every untracked file is not said. If it does,
`notes.txt`, `token.txt` and `config.local.yml` (untracked, not ignored) travel to each device; A19's test, "untracked files are not
sent unless named", cannot say what "named" means for `**`. And the `own` sentence says the tests "may be sent to your devices" (4.8,
12), which in a pool in dev mode is any device on the LAN that joined.

**Breaks.** 6.3, 4.8, 12, A19, F14, F25.

**Change.** DEPS or `include` pull in an untracked file only through a glob that is not the whole tree (`**`, `*`, the empty set), and
the first send lists them. The sentence names the devices a send may use (N3 item 3).

### N14. The runner walks and hashes directories a child wrote, by name (Low)

**Attack.** 3.5 rule 1 says the Runner "never reads by name a path a Child can change". 4.4 has the Runner build a manifest of
`{cache}` (path, mode, size, content hash) and re-hash entries before every reuse, and 5.3 hashes the files named by DEPS in the
checkout, which on a local run a test has been writing. A step can leave a symlink to a private file, a FIFO (an `open` blocks the
Runner for ever; the guard list of N6 does not name it) or a link to `/dev/zero` in `{cache}`. [probe] `O_NOFOLLOW` covers only the last
component: `os.open('out/dirlink/r2', O_CREAT|O_EXCL|O_NOFOLLOW)` was created through a symlinked directory component; `openat(dirfd,
'r3', ...)` is the safe form. The cost: [probe] an `lstat` walk of this repository's cargo target directory (10,942 entries, 4.2 GB)
took 2.3 to 3.6 s at load 90, and `os.walk` plus `lstat` of its files 1.2 s; before every reuse that is a measured fixed cost against
the 5-second rule once `{shared}` is covered.

**Breaks.** 3.5 rule 1, 4.4, 6.6 item 2, F1, F9.

**Change.** State that the manifest walk uses `openat` with `O_NOFOLLOW|O_NONBLOCK`, `fstat` regular file or records the entry's type
without opening it (a link, a FIFO, a device is data in the manifest, not read), with a size and entry count taken from the
descriptors; result files are always created with `openat` on a held directory descriptor. Say which of `{cache}` and `{shared}` the
manifest covers; bound the cost by recording a digest only of what a unit reads (its `reads`). Rule 1 gains "local checkouts are read
as data and never followed out of the root" for DEPS hashing.

### N15. Loopback denial on macOS is a snapshot (Low)

**Attack.** 10.2 denies "the device's own service ports ... by number". [probe] It works (the deny rule refused the daemon
stand-in on IPv4 and IPv6). But the list is made when the profile is built: [probe] a listener started after the profile was built was
reached (`CONNECTED b'OWNER-SERVICE'`). On a Poolhouse device the owner starts model servers at any time, and a sent test that prompts
one is a second piece of work on the GPU. `poolhouse.sandbox.Net` has `deny`, `loopback` and `only(ports)` and no deny-ports mode
[code], so the rule is a change to an L0 module the design does not list.

**Breaks.** 10.2, 10.9 item 4, A26, F10.

**Change.** On a macOS device `net = "loopback"` is refused for a sent unit unless the receiver's policy lists the ports to allow (the
stricter unit already named in 10.9 item 4 becomes the default, and such labels go to a Linux or WSL device); add the new mode to
`sandbox.Net` with its test.

### N16. A preset's `[confine]` and the forwarded environment (Low)

**Attack.** 7 and 10.5: a shipped preset's `[confine] read` and `env` name toolchain homes and variables (`CARGO_HOME`, `RUSTUP_HOME`,
`JAVA_HOME`, proxy and certificate variables). If `read` is the home itself, `~/.cargo/credentials.toml` (the registry publish token)
is inside it; the proxy variables' values can hold `user:password@`. A hostile, confined project has no network, but it can write what
it read into the project directory (N5) or into its output frame, and an agent then commits or pastes it.

**Change.** A shipped preset names subpaths (`registry`, `toolchains`), the profile adds exact-path denies for known credential files
inside a toolchain home, and proxy values are forwarded only as the host and port.

### N17. Numbers and repository knowledge left in or imported by the core (Low)

The design is clean of invented timing and size constants; what remains:

- `poolhouse.sandbox.policy.Limits` [code], an L0 module the runner imports, has defaults `wall_seconds = 60.0` and `output_bytes =
  1_000_000`; a runner call that forgets to pass limits inherits two numbers with no source (AGENTS.md section 2, "A number comes from a
  source"). The layering test should require explicit limits on every executor call.
- `[run] slice_base` defaults to `1` "because it is the common reading of 'slice 1 of N'" (4.1): a guess about third-party tools in the
  core (`pytest-shard` counts from 0). It should be required with no default, like `silence_margin`.
- Section 15 lists "model server slots | 1 (`--parallel 1`)" as a runner number and 4.3/5.5 say the runner "never raises it". That is
  `poolhouse.serve`'s limit and product knowledge; the row belongs to `serve`'s table and the runner only delegates.
- `canary` has no range (N6 item 1): `canary = 1e-12` is reuse on with detection off.
- Small counts are stated with a reason but no source: "reassigned once", "suspect after more than once", "one more fresh run". Each
  is a rule, not a measurement; say so in 15 as "a rule, no number" the way the first-reuse canary is.
- 6.3 "mode normalised" does not say to what.

### N18. Smaller things

- *Exit-code table (11).* I attacked the precedence (a failure is 1 first, then the numerically highest of 4, 6, 70, 75, 124, 130) with
  a hostile device and a hostile unit and found no way to hide a failure behind another cause or to turn one into a retry code. Gaps:
  the status of a `canary mismatch` (it "fails"; add it to the causes of 1), of a `flaky` unit (a pass; say so), and of a shard
  refused for a failed control unit (70 as `not run (device refused)`; say so). A device that returns false failures keeps the run red
  and never becomes `suspect` unless a same-class sender re-run that does not reproduce counts as a mismatch (F7); it should.
- *Frames (10.5).* The delimiters `--- end ---` carry no per-run nonce; a prefix on every line makes a forged one visible, a nonce
  makes it unforgeable at no cost.
- *5.9.* "the tree of the range's start (or the merge-base)" lets the author choose the older base when the two differ; pick one.
- *Root discovery (4).* "the project root" is not defined; a search upward must stop at a top-level (N1).
- *The A1 answer.* An install-instance id stored beside the key is copied by a disk clone, a snapshot restore or a backup restore, so a
  full clone is the same instance and gets shards; the id separates a stolen key from the original only when the original contacts the
  sender too. A1 should say "a copy of the key" and not "a clone".

## 4. Rows of 10.8 whose answer changes

| Row | Now | Because |
|---|---|---|
| A1 | partly | disk clone copies the instance id (N18) |
| A4 | partly | no receiver time, disk or memory budget (N7) |
| A7 | not held against a file-writing agent | N3 |
| A9 | partly | sentinels (N11) |
| A12 | held | exec/, manifest; manifest by name is N14 |
| A21 | partly | disk quota and macOS memory (N7) |
| A22 | broken as tested | git runs first and the identity is spoofable (N1, N2) |
| A24 | partly | `target` is the agent's to change (N3) |
| A29 | partly | named code holds the key (N4) |
| A32 | partly | same (N4) |

## 5. What held

- `exec/`: [probe] no write, alias or clone into or out of it from a confined child; a hard link of a file outside the write set is
  EPERM on this OS even with no deny list, so the unconditional `(deny file-link)` the repository adds when a deny list is set is belt
  and braces, and the design should still say the profile has it.
- The held-descriptor result channel and exclusive create (N14 aside).
- The descriptor permit as a mechanism: it works under Seatbelt and a path socket does not (N12 aside).
- RLIMIT_CPU as the Parser's time bound on macOS.
- The divide and cover rule, the status ranking, the id grammar, the key, the retention, the state location, the glossary gate.
- The zones themselves: nothing I found requires moving a boundary.

## 6. Numbers and repository knowledge: summary

No invented timing or size constant in the design text. Found: the two inherited `Limits` defaults, `slice_base = 1`, the model-server
row, the unranged `canary`, and the unsourced clamp of 10.3 (N7). The core text names no repository's file, marker or script; it names
Poolhouse's `serve`, `leases` and `workspace` modules only in plugin and provider positions (3.1, 3.3, 5.5), which the layering test
covers.

## 7. What was run, and what was not

Run [probe], with the output quoted where it supports a finding:

1. `git rev-parse` on a hostile `.git` file, a hostile `.git` directory with `commondir`, a directory below a parent repository and a
   `GIT_DIR` override (N1).
2. Hard link, clone and symlink attempts from a confined child, under the repository's `seatbelt.profile`, with and without
   `deny_read` and `deny_write` (F1: held).
3. Exclusive no-follow create, intermediate-component symlink, FIFO and link swaps with a held descriptor, a child writing through a
   pre-opened descriptor (F1: held; N14).
4. `tomllib` with `inf`, `nan`, `1e999`, a 5000-digit integer and deep nesting (N6, N8); its memory and time per input byte (N7).
5. A UTF-16LE DOCTYPE document through the design's pre-check and `ElementTree`; two fixes at the expat level (N8).
6. `find_spec` of dotted names (N10).
7. `RLIMIT_AS`, `RLIMIT_DATA`, `RLIMIT_CPU` on macOS (N7).
8. Confined and unconfined Python start cost (N9).
9. Loopback with and without a deny-by-number rule, IPv4 and IPv6, a late listener (N15).
10. An inherited connected socket, a connect by path, a leaked descriptor, the descriptors a child holds (N12).
11. A confined child writing the project directory; `deny_write` on a hard-linked `.git` raising (N5).
12. A controlling terminal for the agent shell and for a child under `script(1)` (N3).
13. A narrow-deny profile over an otherwise open one, with hard link and clone attempts (N4).
14. `LOCAL_PEERPID` after the connecting process exits (N12).
15. An `lstat` walk of a cargo target directory (N14).
16. `scripts/test all tests/test_glossary.py`: 37 passed (F30 item 14).

Not run:

- **`core.fsmonitor` and `core.hooksPath` execution** (N2): refused by the bash guard (`git config core.fsmonitor`, `git -c`) and the
  edit guard (a write to a scratch `.git/config`); not worked around. Evidence is `git-config(1)` and the guards' own refusals.
- **Linux**: bubblewrap, the PID namespace, `--die-with-parent`, a new network namespace, `tmpfs size=`, `pidfd`. No WSL device was
  used. N7, N12 and N15 are stated for macOS and reasoned for Linux.
- **LaunchServices escape from an open-profile child** (N4): it would open an application on the owner's display.
- **Process-id recycling time** (N12) and **submodule and linked-worktree behaviour** beyond git's documented layout (N1, N4).
- **Real devices and the pool wire**: nothing is built.

## 8. Step 8, and what each step must absorb

**Step 8 may unblock when the design text has absorbed N1 to N8, N10 and N11 and a reviewer other than the author has re-read the
edited 4.8, 6.3, 6.4, 6.6, 10.2, 10.3, 10.6 and 5.10.** The edits are of the text and its attack tests; the zone structure stands. Sub-steps
(a) and (b) (the receiver, the wire) depend on N7, N8, N9, N10; (c) (trust) on N11 and N4; (d) (sending) on N1, N3, N13.

| Step | Absorb before it is built |
|---|---|
| 1 model and config | N6 item 1 (finite positive numbers, `canary` range), N17 (`slice_base` required), N8 TOML integer |
| 2 admission | N12 (delegation keyed on start time, server ends with the group, nested refusal by the broker) |
| 3 local run core (a to d) | N1, N2, N3, N5, N6, N4 option chosen, N9 (worker, wire), N17 (explicit `Limits`); `own` and the settings are built here |
| 5 reuse and selection | N4 (per-project key), N18 (statuses), N14 (manifest by descriptor) |
| 6 steps | N14 |
| 8 pool, receiver, wire | N7, N8, N9 (unpack in the worker), N10, N11, N13, N15, N16 |
| 9 fail-first | N4, N18 (one base) |
| 10 second-repo test | N1 fixtures as part of check J |

Tests to write first, each watched failing against the naive implementation named: the four identity fixtures of N1; a repository
whose configuration sets `core.fsmonitor` and `core.hooksPath` (N2); a settings file edited by a file write (N3); a named unit that reads
the key (N4); a confined unit that writes `testfarm.toml` (N5); `silence_margin = inf`, one margin alone, and a unit that prints forever
(N6); a unit that writes until stopped and one that allocates until stopped (N7); the three XML forms and a 5000-digit integer (N8);
`module:this.x` (N10); a control unit that must be refused a canary read (N11); a dead connecting-process id (N12).
