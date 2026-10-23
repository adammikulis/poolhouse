# Third break-it pass on the test runner design, for build step 3

Subject: `docs/design-test-runner.md` on `origin/0.3dev` at `620a7c589`. The pass started at `19b40866d`; the one commit between them edits
the receiver's time bound (4.8, 6.1, 6.6, 10.3, 10.6, 10.8, 14, 15), which step 3 does not build, and its text was re-read. Reviewer: an agent
other than the author of the design and other than the authors of `docs/design-test-runner-breakit.md` (F1 to F30) and
`docs/design-test-runner-breakit-2.md` (N1 to N18). The design is not edited by this pass. Scope, as briefed: the gate seam (`PersonGate`), the one
spawn function, the confined Parser worker, scratch copies for unnamed projects, `own`, the settings file, project identity (4.9), the
`owner/` and `work/` split and the control units; the original attack of N1 to N6 (and N10 to N12 where step 3 touches them) against the new text,
then the structures the rewrite added. Findings of this pass are S1 to S16.

Evidence tags: **[probe]** run in this pass, **[code]** read in this tree, **[doc]** stated in a document of the repository or a manual page,
**[reasoned]** follows from the text and was not executed, **[not run]** could not or must not be run, with the reason (section 7). Machine for
every [probe] unless stated: macOS 26.6 arm64, Python 3.14.7 (Homebrew) and 3.13.5, git 2.51.2, Docker Desktop for the two Linux probes; load average
30 to 42 during the timings, so a time is an upper bound and a count or a pass or fail is not affected. Probe scripts and fixtures are in the
session scratch directory, not in the repository.

## 0. Verdict

**Build step 3 may not start as written. Step 3(a) may start now** (divide, assign, schedule, merge, report, the exit-status table, the `pytest`
and `files` presets: nothing in them crosses a trust boundary). Commits (b) and (c) need S1 to S4 absorbed in the text first, and S5 to S12 absorbed
in the commit that builds the thing each one names (section 8). None of them needs a change of the zone structure of 3.5: the zones held every
direct attack (section 5). What fails is the layer under them: the identity that decides who is named, the profile that is supposed to deny the Owner
zone, the seam that stands for the person, and a launcher that cannot be built on macOS.

Of the original attacks, N2, N10, N11 and N12 are absorbed in the text; N1, N3, N4, N5 and N6 are absorbed in part; none is untouched (section 2).

### Ranked findings

| # | Severity | Finding | Breaks |
|---|---|---|---|
| S1 | High | The identity record cannot tell another volume, a recreated directory (Linux) or a differently spelled path from the named project, and classification follows pointers the project wrote | 4.9, 4.8, 10.2, A29, A35 |
| S2 | High | The narrow-deny profile as written loses the Owner zone to a rename of its parent, to a symlinked config directory and to paths taken from the environment | 3.5 rule 4, 10.2, 4.8, A29, A38 |
| S3 | High | `PersonGate` is a seam without a protocol: nobody renders the question the person reads, a yes is a bare word, the approved record is not the written record, and other writers skip the gate | 4.8, 3.2, 10.2, A7, A11, A24 |
| S4 | High | The `launcher` cannot be built on macOS: one Seatbelt profile per process, so a Seatbelt-based launcher or a test that applies its own profile cannot run inside the runner's profile | 4.7, 3.3, 10.2, 8 |
| S5 | Medium | The spawn function has no base: `sandbox.run` passes only FIFOs, accepts `Limits` of `None`, holds output in memory, offers an unsandboxed fallback, leaves the terminal to the caller, and starts processes of its own | 3.1, 3.5 rule 3, 5.5, A39, A44 |
| S6 | Medium | A named child reaches the Owner zone one command later (start-up files, LaunchAgents); the claims that no child can forge `named` evidence are stronger than the profile | 3.5, 5.3, 10.1, 10.9 item 7, A29, A32, A38 |
| S7 | Medium | Step products made while a project was confined are consumed after it is named | 4.4, 5.3, 4.8 |
| S8 | Medium | 5.1 copies before it parses, the copy needs the parse, and the first spawns need margins the parse supplies | 5.1, 5.10, 6.3, 4.9 |
| S9 | Medium | A confined `git` cannot run on this Mac without a resolved binary and a read set; the Apple shim fails closed, which invites an unconfined fallback | 4.9, 10.2, A36 |
| S10 | Medium | The Parser worker: a lifetime CPU limit ends it after the third large input, state is shared across inputs, per-tag bounds have no source, the stream has no end record | 6.6 item 4, 3.5, 15, 10.4 |
| S11 | Medium | `clonefile` of a directory copies links, FIFOs, flags and attributes, fails on one unreadable directory, and a path-based call follows an intermediate link; the removal of the copy fails | 10.2, 6.2, 6.3 |
| S12 | Medium | The Own child's profile breaks `cargo build` (the first consumer's native build step) and does not say whether it has a network | 3.5 rule 4, 10.2, 8 |
| S13 | Low | Control units: reserved ids, one canary location, and nothing measures the confinement of local runs | 6.1, 6.4, 10.9 item 5 |
| S14 | Low | Root discovery climbs to a planted `testfarm.toml`; a project outside git has no transported set; `init` writes through a link | 4, 4.9, 6.3, A19 |
| S15 | Low | `owner_paths()` names things the core may not name, must be total, and must not depend on the environment | 3.1, 4.8, 3.5 |
| S16 | Low | Text conflicts a builder will meet | various |

Two decisions are the owner's alone; each comes with the fix as its first option.

1. **How units that apply their own Seatbelt profile run under the runner** (S4). Option 1: the person lists them in the settings (`own_profile`,
   written through the gate); they run without the outer profile and the report says so; the runner hands `owner_paths()` to `poolhouse.sandbox`
   so the tests that build profiles keep the Owner zone out of them. Option 2: they run only in the container class. Option 3: no such unit runs
   under the runner on macOS (Poolhouse's 27 modules stay on the old pass).
2. **What `named` evidence is worth against a hostile named child** (S6). Option 1: it is advice against honest mistakes and against a child that does
   not persist; the reader's own re-run of one unit of the exact tree credits a task; the text says so. Option 2: keep the claim and add the deny
   list of start-up files, which is a snapshot (weaker).

## 1. Method

Read in full: the design at `19b40866d` and the diff to `620a7c589`, both earlier passes, `AGENTS.md`, `docs/agent-policy.md`. Read as code to test the text against what
exists: `src/poolhouse/sandbox/{seatbelt,policy,run,io,denial_log}.py`, `src/poolhouse/platform.py`, `src/poolhouse/home.py`,
`src/poolhouse/workspace/{person_release,person_auth}.py`, `docs/person-delegation.md`, `scripts/hooks/{claude-bash-guard,claude-edit-guard}`,
`scripts/testwritedeny.py`, `tests/seatbelt-modules.txt`, `docs/limits.md`.

Each of N1 to N6 and N10 to N12 was attacked again with its original attack against the new text (section 2). A literal reading of 4.9 was written
as a classifier (about 70 lines) and run on the four fixtures of N1 and on copies, a move and a replacement; the Owner-zone profile was written as
the text says and attacked with about twenty operations; the spawn function's base was run for the cases the text relies on. All profile probes use real
`sandbox-exec` on this Mac, with fixtures under the scratch directory and nothing under the real home, the real state root or the keystore.

## 2. Verdicts on N1 to N6 and N10 to N12

"Absorbed" means the original attack, run against the new text, no longer works; "partly" means the named residue does.

| N | Verdict | The original attack against the new text |
|---|---|---|
| N1 identity from pointers | **partly** | The four fixtures (a `.git` file reading `gitdir: <named>/.git`, a `.git` directory with a `commondir` naming it, a directory below a parent repository, `GIT_DIR`) are each unnamed [probe: the literal classifier of 4.9 gave `unnamed` for all four, for a worktree admin directory without a back-pointer, for `cp -R` copies of the repository and of a linked worktree, for the repository moved away and for another repository created at the old path on APFS; the real worktree and the repository are `named`]. Residue: S1 (another volume at the same path, a recreated directory on Linux, spelling, relative worktree pointers, classification that follows pointers). |
| N2 git is code | **absorbed** | Classification runs no git and every later git call is confined, overridden and run with a fixed environment (4.9). The override attack itself was [not run] (the bash guard refused the strings). The residue is a build hazard, not a hole: S9 (the confined git does not run on a stock Apple install, which will tempt an unconfined fallback). |
| N3 the person's yes | **partly** | The terminal answer is refused by construction; `target` and `--on` that widen ask the gate. The seam is specified, the protocol behind it is not: S3. The guards are literal text, [probe] a path assembled at run time passed the bash guard (S3 item 8). |
| N4 Owner zone denied to every child | **partly** | [probe] Direct read, append, create, rename out, hard link out, clone and four alternate spellings of the key are refused under the profile text of the design. The same profile lets the child rename the key's parent directory and read the key in its new place, and lets it replace a symlinked config directory and write the settings (S2); a named child reaches the zone one command later (S6). |
| N5 a confined run cannot write what the next action runs | **partly** | The scratch copy removes the checkout from the child's reach, which is the whole of the original attack. The copy as specified is not buildable (S11), and the products of a confined step reach the named run after `own` (S7). |
| N6 the guard cannot be bypassed | **partly** | `inf`, `nan`, `1e999`, one margin, the unattended first run and a bypassing spawn are each refused by the text. Residue: `Limits(wall_seconds=None)` is "explicit" (S5); the first spawns run before any margin is known (S8); in-process blocking calls are outside the spawn function (S1 item 5, S3). |
| N10 `module:NAME.sub` | **absorbed** | [probe] `importlib.machinery.PathFinder.find_spec('this')` returns a spec and `this` is not in `sys.modules`; the dotted name is refused by the grammar. Edge: a built-in module (`sys`, `_thread`) returns `None`, so `module:sys` is reported missing; harmless. Built in step 8. |
| N11 control units | **absorbed in the text** | The nonce is a start field, the confinement unit is present, a mismatch is sticky, the word is "control unit". Residue: S13. Built in step 8(c). |
| N12 handles, descriptors, nested runs | **absorbed in the text** | A handle is the process id and start time with an exit registration; the descriptor list is exact; the nested refusal is the broker's test. Residue: (a) 5.5 says the exit registration is made "after the start time is checked"; the start time must be read again after the registration, otherwise a process that exited and whose id was recycled between the two reads is registered; (b) the permit descriptor is a capability by itself, and the identity the socket reports for its other end (the creator of a socketpair) is not the child, so the text must say that per-invocation permits are not checked by process id and carry only the verbs `sub` and `return`, not `release` or `delegate`; (c) the descriptor the spawn function must pass is a socket, which `sandbox.run` cannot pass (S5); (d) a nested `poolhouse-test` in a unit cannot read `owner/` (S2), so it fails with a permission error and not with the sentence of 5.5. Built in steps 2 and 3(b). |

## 3. New findings

### S1. The identity record cannot tell another volume, a recreated directory or a differently spelled path from the named project (High)

**Attack.**

1. *Another volume at the same mount point.* [probe] Two different disk images with one volume label, mounted one after the other at one mount point,
   gave the same `(st_dev, st_ino)` for the mount root on all three filesystems: exFAT `(16777248, 5)`, HFS+ `(16777248, 2)`, APFS `(16777250, 2)`.
   The device number is not a volume identity in either direction: image X had `st_dev` 16777244; after X was detached, image Y mounted at once got
   16777244; X attached again while Y was mounted got 16777247. The first directory made on a fresh volume has the same inode on two fresh images
   (6 on exFAT, 18 on HFS+, 16 on APFS in the probe), so an image built with the same layout reproduces a sub-directory too. The birth time of the
   root differed between the images on HFS+ and APFS and is 0 on exFAT; `getattrlist(ATTR_VOL_UUID)` differed on all three. A person who names a
   project at the root of an external drive names `(/Volumes/Work, <st_dev>, 2)`; a hostile drive labelled `Work` with a `.git` directory, attached
   after the first is ejected, reproduces it, and the project runs unconfined as the user.
2. *A recreated directory on Linux.* [probe, in an Ubuntu 24.04 and an Alpine container on Docker Desktop's VM, overlay root; not the WSL device]
   `mkdir proj; rmdir proj` fifty times returned the same inode fifty times (49 times the same as the one before); the `statx` birth time differed
   each time at nanosecond resolution; the generation ioctl (`lsattr -v`) answers `ENOTTY` on overlay. On this Mac's APFS 200 recreations never
   reused an inode. So `rm -rf proj && git clone <url> proj`, the ordinary repair of a broken checkout, leaves a named project on Linux whatever `<url>`
   served, and an unnamed one on macOS.
3. *Spelling.* [probe] `os.path.realpath` keeps the case the caller typed (`casedir/myapp` for the on-disk `CaseDir/MyApp` on the default
   case-insensitive APFS) and the normalization form (NFD `e` + U+0301 for the on-disk `é`); `fcntl(F_GETPATH)` on a descriptor returns the
   on-disk spelling. The record compares realpath strings, so a project named as `~/Code/MyApp` is unnamed when the shell completes
   `~/code/myapp`. That is the safe direction, but the person is told nothing, the project runs confined in a copy, and the scope digests and keys of
   the two spellings differ.
4. *Relative back-pointers.* [probe, git 2.51.2] A linked worktree made with `--relative-paths` (opt in, the default is absolute; the same as the config option
   `worktree.useRelativePaths` [doc: `git-worktree(1)`]; it sets `extensions.relativeworktrees`) holds `<gitdir>/gitdir` = `../../../../wt2/.git` and a `.git` file reading `gitdir: ../main/.git/worktrees/wt2`.
   4.9 does not say how a relative value is resolved: a string comparison unnames every such worktree, a resolution against the working directory is
   an attack surface. The literal classifier accepted it only once the value was resolved against the directory of the file that holds it.
5. *Classification reads what the project wrote, in the unconfined Runner.* It opens `<root>/.git`, `HEAD`, `objects`, `commondir` and
   `<gitdir>/gitdir` and follows `gitdir:` to a path the project chose. A `.git` that is a FIFO, a link to `/dev/zero`, or a file reading
   `gitdir: /Volumes/<an unreachable network share>/x` blocks the Runner in `open` or `lstat`; no spawn guard covers an in-process call (3.1 guards
   processes). [reasoned]
6. *Bind mounts and symlinked parents.* [reasoned; a bind mount needs privileges and was [not run]] The record holds the resolved path, so a symlinked
   parent retargeted elsewhere changes it (safe); a bind mount of the named repository at another path keeps `(st_dev, st_ino)` and changes the path
   (safe); a bind mount of another directory over the named path changes the inode, or the device (safe). The cases that hold are items 1 and 2.

**Breaks.** 4.9 "The identity record", 4.8 `[[named]]`, 10.2 "Local runs", A29, A35; the sentence of 4.9 that a restored directory "has another realpath or
inode".

**Change.**

- The record holds, for the root directory, for the entry `<root>/.git` and, for a linked worktree, for the admin and common directories: the
  canonical path from `fcntl(F_GETPATH)` (Linux `readlink /proc/self/fd/N`) on a descriptor opened `O_DIRECTORY|O_NOFOLLOW`; the volume UUID
  (macOS `getattrlist` `ATTR_VOL_UUID`; Linux the filesystem UUID of the mount); `st_ino`; and the birth time in nanoseconds (`st_birthtime`,
  `statx` `stx_btime`). `st_dev` is compared only inside one boot and never stored. A volume that gives no UUID and no birth time cannot be named:
  `own` refuses with one sentence. Binding the identity of the `.git` entry makes `rm -rf * && git clone <url> .` into a named directory an unnamed
  project.
- Verify, do not discover. Classification starts from the settings: it takes the `[[named]]` record whose canonical path equals the candidate root and
  checks, by `openat` and `fstat` with no following, that the recorded entries still have the recorded identities. It never follows a path it read
  from the project; a pointer text is compared with the recorded text, a relative value resolved against the directory of the file that holds it and
  compared by identity.
- Every read of classification is `openat` with `O_NOFOLLOW|O_NONBLOCK` on a recorded path, `fstat` regular file, size taken from the operating
  system's path limit, and runs under a deadline like a spawn.
- Tests, each watched failing against the naive record: two same-label images attached in turn (macOS), the recreated directory (Linux, on the WSL
  device or in a container), a case spelling and an NFD spelling, a relative-pointer worktree, a FIFO as `.git`.

### S2. The narrow-deny profile loses the Owner zone to a rename of its parent, to a symlinked config directory and to paths from the environment (High)

**Attack.** 3.5 rule 4 and 10.2 prescribe, for a named child, an open profile with the Owner paths denied by exact path and `(deny file-link)`. [probe]
The profile below (the design's text, plus the `(deny file-clone)` that `poolhouse.sandbox.seatbelt.profile` emits with `(deny file-link)`), on a
fixture shaped like the state location (`<dir>/testfarm/owner/master.key`, the settings file beside `owner/`):

```
(version 1)(allow default)
(deny file-read* file-write* (subpath "<dir>/testfarm/owner") (subpath "<dir>/testfarm/testfarm-settings.toml"))
(deny file-link)(deny file-clone)
```

- Refused (EPERM): read, `stat`, append, create inside `owner/`, rename the key out, hard link it out, symlink to it then read, `clonefile`, `cp -c`; the
  key read through a lower-case, an upper-case, an NFD, a `/System/Volumes/Data` and a `/tmp` spelling (Seatbelt matches the resolved path). A literal
  deny on a settings file that does not exist yet refuses its creation.
- **Allowed: `rename(<dir>/testfarm, <dir>/testfarm.moved)`.** The child then read `testfarm.moved/owner/master.key` (`SECRET-KEY`). The runner's
  `owner/` no longer exists at its path; a Runner that is still running keeps signing with a key the child now holds [reasoned].
- With `(deny file-write-unlink (literal A))` for every ancestor A of each owner path up to `/` (what `poolhouse.sandbox.seatbelt.profile` emits today
  whenever `deny_read` or `deny_write` is set [code]) the parent rename is refused [probe].
- **Allowed even then, with a symlinked config directory.** `~/.config/poolhouse` is a symlink into a dotfiles directory (`stow` makes it). With the
  resolved owner paths denied and the unlink of all their ancestors denied, the child unlinked the link (it is not an ancestor of the resolved path),
  made a real directory in its place and wrote `testfarm-settings.toml` with a `[[named]]` entry through it. The file is owned by the user and
  regular, made with the default umask: the tests `check` makes (owner, not a link, not writable by others) pass [reasoned]. The next run reads it.
  With, in addition, `(deny file-write* (literal L))` and the same unlink deny for the symlink L, the unlink, the replacement, a rename onto L and the
  write through it are all refused [probe].
- *Paths from the environment.* [probe] `Path.home()` and `os.path.expanduser('~')` follow `$HOME` (`HOME=/tmp/fake-home` gives `/tmp/fake-home`);
  `poolhouse.home.state()` follows `POOLHOUSE_HOME` [code]; the account's directory comes from `pwd` (`poolhouse.home.account_roots()`). A Runner started
  with `HOME`, `XDG_CONFIG_HOME`, `XDG_STATE_HOME` or `POOLHOUSE_HOME` set elsewhere (a wrapper, `direnv`, an agent writing `HOME=... poolhouse-test run`)
  denies the moved directories and leaves the account's real settings, keys and pool home open to the named child. The text does not say how `~` is derived.

**Breaks.** 3.5 rule 4, 10.2 "Local runs", 4.8 ("no child reads or writes them"), A29, A32, A38, N4's remedy.

**Change.**

- State the Own child's profile as: deny read and write of each owner path in both spellings (as the account spells it and resolved); deny
  `file-write-unlink` of every ancestor up to `/`, for both spellings; deny `file-write*` and unlink of every symlink component of the unresolved
  spelling; no global `(deny file-link)` and no `(deny file-clone)` (S12).
- `owner_paths()` returns the union of the paths the current settings and state override name and the account defaults from `pwd`, so a moved
  environment never removes the account's own files from the deny (S15).
- Tests, each a child under the Own profile: rename the parent and read the moved key; replace the link and write the settings; create the missing
  settings file; read through the four spellings; hard link and clone the key; and a Runner started with `HOME` moved still denies the account's
  directories. Mutants: drop the ancestors deny, drop the link literal, compute `~` from `$HOME`.
- On Linux, bubblewrap can mask only a path that exists: the runner creates the settings and presets files empty before the first child, or masks
  their parent. [not run]

### S3. `PersonGate` is a seam without a protocol (High)

The seam is the right shape: one function, three answers, no terminal, no environment. What stands behind it is stated by analogy to `release-main`
and the analogy does not carry over without work. `person_release.approved` works because the hook re-renders the question from git at answer time
and compares it byte for byte with what the model asked [code]; there is no git for `test-own`.

**Attack and gaps.**

1. *Nobody renders the sentence.* 4.8 says a `question` carries "the sentence exactly as the person will read it", supplied by the caller. A
   caller-supplied sentence is a sentence the model can word: the agent asks a benign question with the approving label, and the hook has nothing to
   re-derive it from. The host needs a pure function that makes the sentence and the digest from facts (the identity record, the device set).
2. *A yes is a bare word.* The library cannot tell that a `yes` belongs to the question it asked; a gate that consumed an authorization for another
   subject answers `yes` all the same.
3. *The record approved is not the record written.* `own` computes the record, asks, and writes. A directory swapped between the answer and the write
   is written as the new directory's record unless the write re-checks the digest.
4. *The first call.* The flow of `release-main` is two calls: the agent asks the rendered question, the person answers, the hook creates the
   authorization, the agent acts, the guard consumes it. 4.8 says the gate "returns yes, no or unavailable" in one call and "nothing waits", which
   the host can satisfy only by answering `unavailable` the first time. The text does not say what the CLI prints on that first call.
5. *The gate is an import string.* `person_gate` is "named in the user's settings" and loaded by name through `names.py`. An exception, a hang or a
   truthy non-word from that object must be `unavailable`, and the name should resolve only among declared entry points.
6. *Other writers of the Owner files.* `python -m poolhouse.device_setup --yes` exists ("agree to the listed changes without being asked", for an
   agent or script) [code]; 4.8 gives it two settings writes (it names Poolhouse's own checkout, 10.2, and adds `board` to `events.sinks`). A flag
   is "never accepted as a person's yes" (4.8). `confine.read`, `resources`, `events.sinks` and `person_gate` have no command at all, so they are
   written by hand or by setup.
7. *The refusal hands over the act.* With no gate, the CLI prints "the exact block ... and the one-line shell command that appends it" to whoever ran
   it, which is the agent. On a host with no guard the agent runs it.
8. *The guards are literal text.* [code] `claude-bash-guard` matches the command text with regular expressions; `claude-edit-guard` resolves links
   (`Path(path).resolve()`). [probe] `python3 -I -c "import os; d=os.path.expanduser('~')+'/.pool'+'house'+'/per'+'son'; print(os.path.isdir(d))"`
   passed the bash guard and printed `True` for the person store (existence check only; nothing was read or written). A7 and 10.9 item 9 hold against an
   agent that does not try to evade the guard, and the text says "an agent whose host guards those paths". The guard's own wiring note says "a missing
   guard must not block every Bash call", so what a guard that cannot import `ph.test` does is not specified.
9. *Nothing is checked at use.* A `[[named]]` entry in the file is believed. A forged entry (S6, or an agent that evades item 8) looks like a real one.

**Breaks.** 4.8 "The person's yes" and "Naming a project", 3.2, 10.2, A7, A11, A24, 10.9 item 9.

**Change.**

- `ph.test` exports `question(act, subject)`, a pure function that returns a frozen `Question(act, subject, sentence, digest)`; the library builds
  every question with it and the host's `propose` and `approved` call the same function, as `person_release` calls `facts` and `question`. The gate
  never takes a sentence from a caller.
- The gate returns `Approval(digest, evidence_id)`, `No` or `Unavailable`, never a bare word. `Approval` has no public constructor. The one function
  that writes the settings, `settings.write(change, approval)`, checks `approval.digest == digest(change)` and re-verifies the root identity of
  `change` (S1) immediately before it renames the file into place; it is imported only by `own`, `allow`, `target` and `policy`, and a layering test
  pins that.
- The flow is written down: first call with no live authorization prints the rendered question, the registry kind and the command to run again,
  and exits 4; the agent asks the question word for word; `own` run again consumes the authorization (kind `test-own`, `test-allow` or `test-widen`,
  target the digest, one use, 15 minutes as for `release-main`) and writes.
- `person_gate` and the sinks and providers are named by an entry-point group the installed packages declare; an exception, a deadline or an
  unlisted name is `Unavailable`.
- Device setup writes through the same function, with its yes from the gate, and `--yes` is not a yes for any Owner file. The settings keys that
  have no command get one (`poolhouse-test settings`), each through the gate.
- The refusal prints the path and the block and nothing that completes the act.
- A `[[named]]` and an `[[agree]]` entry carry the id of the authorization that wrote it. The host's gate has `verify(entry)`; `check` and the first
  line of every run print `named: <path> (gate evidence <id>)` or `named: <path> (no gate evidence)`, so a forged entry is visible. Prevention is not
  claimed: a same-user agent can still edit both files (10.9 item 9).
- The Owner-path refusals of both guards are hard rules, ignore `POOLHOUSE_GUARD=off`, and fail closed when `owner_paths()` raises (S15).
- Tests: a question asked with another sentence is not approved; a yes for another digest is refused; a directory swapped between the answer and the
  write is not named; an exception in the gate is `Unavailable`; a layering test on the writer; `device_setup --yes` writes no Owner file.

### S4. The `launcher` cannot be built on macOS (High)

**Attack.** A process on macOS takes one Seatbelt profile. [probe] Under `sandbox-exec -p '(version 1)(allow default)(deny file-read* (subpath ...))'`,
a second `sandbox-exec` (rc 71, `sandbox_apply: Operation not permitted`) and a `sandbox_init` call from Python (`-1`, `Operation not permitted`) both
fail, with profiles in files as well as inline, and with an outer `(allow default)` and an inner `(deny default)`; only two byte-identical
`(allow default)` profiles nested. The repository knows this: `scripts/testwritedeny.py` says "a process that is already sandboxed cannot apply a second
`sandbox-exec` profile" and `tests/seatbelt-modules.txt` lists 27 test modules that run `sandbox-exec` themselves and "run in a pass of their own
without the denial, and only the after-the-fact check watches them" [code].

The design wraps every child, named or not, in the runner's profile (3.5 rule 4) and puts the project's launcher "inside the profile it builds, never
outside it" (4.7). So (a) Poolhouse's Seatbelt kernel launcher, an argument vector whose program is `sandbox-exec`, cannot run in either order; (b) the 27
modules fail with the sentence above inside an Own child; (c) the only handle the design offers for them is the label `seatbelt = { local = true }` (8), which keeps a unit on
this machine and says nothing about the profile. If a builder resolves the failure by letting a project label opt its units out of the outer
profile, the project has chosen its own profile, which 10.2 forbids ("a launcher ... never choose[s] the profile"). Playwright's Chromium, SwiftPM plugins and Homebrew builds apply their own profile too.

**Breaks.** 4.7, 3.3 (the row for the Seatbelt kernel), 10.2, 8, check H; step 3(d), the first consumer.

**Change.**

- State the one-profile rule in 3.5 and 4.7. On macOS a `launcher` may name only wrappers that are not Seatbelt (a container); a request for more
  confinement is data merged into the runner's profile (`[run.sandbox] deny_write = [...]`, narrowing only), not an argument vector.
- A unit that must apply its own profile is a user's decision, not a project label: the settings carry `[project."ID"] own_profile = [ids]`, written
  through the gate (S3). Such a unit runs without the outer profile, the runner passes `owner_paths()` in a file that `poolhouse.sandbox` adds to every
  profile it builds, and the report says `ran without the Owner-zone deny (own_profile)`. Or those units run in the container class.
- The spawn function recognises rc 71 with that stderr sentence and reports `this unit applies its own sandbox profile; see own_profile` instead of a
  failed test.
- `testwritedeny.py` is subsumed on macOS: the pool's home is in the Owner zone, so the runner's deny already stops the writes it exists to stop.
- Test: a unit that runs `sandbox-exec` in an Own child is reported with the sentence; a unit listed in `own_profile` runs and is reported.

### S5. The spawn function has no base to stand on (Medium)

3.1 says one function in L2 starts every process, taking the hang guard, the profile, "the exact list of descriptors the child keeps" and explicit
`sandbox.Limits`. The only existing code that does the rest of the work is `poolhouse.sandbox.run` [code]. Against the text:

1. *Descriptors.* `sandbox.run` passes descriptors only through `Pipes`, and `_pipe` accepts only a FIFO owned by the user, close-on-exec, on
   macOS. The permit is a socket and `{result}` is a regular file (5.5, 6.6). Step 3(b) needs an L0 change that 3.1 does not list. [probe] The leaked-descriptor
   result of N12 holds under the Own profile: a descriptor opened on a denied file and passed with `pass_fds` read the key while the path was refused,
   and without `pass_fds` (`close_fds=True`) the descriptor was not there.
2. *Terminal.* [probe] A child with the Own profile that shares the runner's session and controlling terminal injected `echo INJECTED-BY-CHILD`
   into the terminal's input queue by `TIOCSTI` on `/dev/tty` and on descriptor 0; the person's shell would run it after the run, outside any profile.
   Started with `start_new_session=True` (what `poolhouse.platform.start_process` does [code]) both fail (`Device not configured`, `Permission
   denied`). The repository's deny-default profile refuses the ioctl even with `/dev/tty` allowed (`strict=False`) and refuses the open (`strict=True`)
   [probe]. The text says the child keeps "0, 1, 2" and does not say what they are or that the child has no controlling terminal.
3. *`Limits` can be `None`.* `Limits(wall_seconds=None, output_bytes=None)` is explicit and unbounded [code]. `run` keeps stdout and stderr in a
   `bytearray` until the cap, so a `None` cap is unbounded memory in the Runner. The check "every call passes explicit limits" is satisfied by it.
4. *Fallback.* `sandbox.run(..., unsandboxed=AllowUnsandboxed(reason))` runs without a backend [code]. The text does not forbid the parameter, and on a
   machine with no Seatbelt or bubblewrap an Own child has no way to deny the Owner zone at all.
5. *Process count.* `Limits` has no process ceiling; the polled group kill (10.6) is slower than a fork storm. [probe] `RLIMIT_NPROC` works on this
   macOS: a child with the limit set to the user's current process count plus 25 failed its 27th fork with `EAGAIN` (617 user processes,
   `kern.maxprocperuid` 10666). A fork storm itself was [not run].
6. *Other starts.* [code] `denial_log` starts `/usr/bin/log stream` and runs a `sandbox-exec` probe through `subprocess`, from L0, outside the spawn
   function and outside any guard. The static test "no code under `testfarm` starts a process another way" cannot see them; the invariant "every process
   the runner starts" is false in effect.
7. *Environment.* `run` builds the child environment as `HOOKS.environment(policy.env)`, a global that the sentinel installs [code]; the allowlist
   of 10.5 is then not the only input.

**Change.** List the L0 changes of step 3(b) in 3.1 with their tests: descriptors of any kind from an explicit list; `start_new_session`, descriptor 0
`/dev/null`, 1 and 2 pipes, and a test from a pty child that `/dev/tty` and `TIOCSTI` fail in both profiles; `Limits` fields required, `None` only
for the bootstrap wall (5.10) and reported as `no wall bound: bootstrapping`; the output cap derived from the unit's share and streamed to the
descriptor, never held; a process ceiling `RLIMIT_NPROC` = current user processes + (`kern.maxprocperuid` - current) / (children admitted + 1) on
macOS and the pids limit on Linux; no `unsandboxed` parameter, a missing backend is status 4 for every project; the allowlist asserted after the
hook; and either the L0 helpers are routed through the spawn function or they are named exceptions in 3.1 with their own bounds.

### S6. A named child reaches the Owner zone one command later (Medium)

**Attack.** A named child runs as the user and may write anything outside the Owner zone: `~/.zshenv` (zsh reads it for every shell, interactive or
not [doc: `zsh(1)`]), `~/.zshrc`, `~/Library/LaunchAgents/<x>.plist` (loaded at the next login, outside any profile), `~/.gitconfig`, a program earlier
on `PATH`. What starts next as the user, the agent's own shell included, reads `owner/` unconfined. [probe] `launchctl bootstrap` and `launchctl submit`
fail for a process under any `sandbox-exec` profile, `(allow default)` included (rc 5 and 1; the same commands ran their jobs unsandboxed in this
session), so launchd is not a same-turn route; the LaunchServices route is [not run]. The persistence routes need no escape at all.

3.5 rule 4, A29, A32, A38 and 10.1 say no child "reads or writes" the Owner zone; 5.3 says "no test, build script or `conftest.py` of any project
reads" the key; 10.9 item 7 names only `open x.command`. A hostile named child can forge a `named` entry or a settings change one command later.

**Change.** Say in 3.5, 5.3, 10.1 and 10.9 item 7 that the deny is a same-turn boundary and name persistence. Add the usual start-up files and
`~/Library/LaunchAgents` to the Own child's write deny as a speed bump, with its test, and say a deny list is a snapshot. Keep the real control where
5.3 already puts it, the reader: a task is credited from a run of the exact tree by a session other than the author's, and the reader re-runs one
unit of that tree itself (a canary of its own) before it credits. S3's evidence ids make a forged setting visible.

### S7. Step products made while a project was confined are consumed after it is named (Medium)

**Attack** [reasoned]. The journey of 12 is: run the handed repository confined, read it, name it, run again. 4.4 scopes `{cache}` and `{shared}` by project (and by
sender on a device) with no domain, and a step whose key has a verified entry is skipped. A hostile `build.rs` or build step that detects its scratch
copy writes a payload into `{shared}` or `{cache}` during the confined run; the source the person reads is clean; after `own`, the step key is
unchanged, the step is skipped, and the Own child executes the product unconfined. The manifest proves the directory is what the step left, not that
the step left what its source would produce.

**Breaks.** 4.4 "Scoped to a project", 5.3 (the store has the domain, the step directories do not), 4.8 "Naming a project".

**Change.** Key `{cache}`, `{shared}`, history and evidence by `(project scope, domain)`; `own` leaves the confined directories unread and removes
them with the removal procedure of S11; a `named` run never consumes a `confined` product. Test: a confined step plants a product; after `own` the
next run rebuilds and the planted file is absent.

### S8. 5.1 copies before it parses, and the first spawns need margins the parse supplies (Medium)

**Attack.** 5.1 step 0 makes the scratch copy of the transported set; step 1 loads the config. The transported set is "the tracked files plus the
untracked files a unit's DEPS or `[tree] include` names, minus `[tree] exclude`" (6.3): it needs the config. A builder who reads the config twice
breaks rule 2 of 3.5 (parse once). Separately, the copy needs `git` (a spawn with a hang guard), the config is parsed by the Parser worker (a
spawn), and 5.10 requires both margins before any spawn can be guarded and refuses the run otherwise. Applied literally, an unattended run with no
`--timeout` is refused at its first spawn, before it can read the margins that would have satisfied the rule; the bootstrap exception covers "the
first unit", not these.

**Change.** Reorder 5.1: classify (files only, S1) -> read `testfarm.toml` bytes through a descriptor with a derived size cap -> the worker parses
once -> validate (margins, S10) -> the transported set (confined git, S9) -> the copy (S11) -> LIST. State the bound of the spawns that precede the
margins: the worker's `RLIMIT_CPU` is the input size times a seconds-per-byte figure measured once per release, as the memory amplification already
is, and it needs no wall guard because its input is complete before it starts. Test: an unattended run in a project that sets both margins starts.

### S9. A confined `git` needs a resolved binary and a read set (Medium)

**Attack.** [probe, repository profile, deny-default, `Limits(wall 30 s)`, `git ls-files --cached` in a three-file repository]

| git | read set | result |
|---|---|---|
| `/opt/homebrew/bin/git` | the repository | `rc=-6`, `dyld: Library not loaded: /opt/homebrew/opt/pcre2/lib/libpcre2-8.0.dylib` |
| the same | the repository, the Cellar prefix, `/opt/homebrew/opt`, `/opt/homebrew/lib` | `rc=0`, `.gitignore` and `a.txt` listed |
| `/usr/bin/git` (the Apple shim) | the repository | `rc=1`, `xcode-select: error: unable to read data link at '/var/db/xcode_select_link'`; denials include the mach lookup `com.apple.dt.CommandLineTools.installondemand` |
| the same | the repository, the Command Line Tools and Xcode | `rc=71`, `git: error: couldn't create cache file '/var/folders/.../T/xcrun_db-...'` (a write outside any scratch) |

(The override list of 4.9 itself was [not run]: the bash guard refused the script that named `core.hooksPath`, I removed the guarded override names
and kept the rest; the overrides do not change the profile a git needs.) 4.9 never says how the runner finds the real binary behind the shim or
which paths it reads, and a first run on a stock Mac fails closed, which is the moment a builder adds an unconfined fallback, the hole N2 closed.

**Change.** Resolve the real executable (the shim's target, `xcrun -f git` run once outside, or `git --exec-path`) and take its install prefix and
dylib directories as the read set of a built-in `git` confine (the same shape as a preset's `[confine]`); give the process no `$TMPDIR` write. When
that profile does not run, every command that needs git is status 4 `git cannot run confined here: <first refusal>`, `check --tools` prints the
refused paths as lines for `confine.read`, and the project is treated as outside git; there is no unconfined path. Test on both a Homebrew and an
Apple-shim install.

### S10. The Parser worker (Medium)

**Attack and gaps (6.6 item 4, 3.5, 15, 10.4).**

1. *A lifetime CPU limit.* "Its time is `RLIMIT_CPU`" for a worker that lives for a shard. [probe] The limit is cumulative: with 2 s, a process that
   spends 0.89 s per input was killed (SIGXCPU) during its third input. Under 6.6 item 4 that is status 4 naming the rule, a spurious refusal once the
   run's total parse time passes one input's budget (600 result files). [probe] The limit is also not a wall: a `while True: pass` loop was
   killed at 2.0 s, a loop that polls `time.process_time()` survived 5 s of CPU under a 2 s limit. Neither is reachable by a hostile input to an
   honest parser, and neither makes the limit the per-input bound the text calls it.
2. *State across inputs.* One long-lived worker parses the config, then every child-written result file. A parse-time compromise by one unit's
   file (expat, `tomllib`) lets the worker report other units' results, and the worker keeps its write access to `tree/` (needed only for the unpack)
   after the unpack.
3. *Constants without a source.* "A fixed bound per tag" is a set of numbers the section 15 table does not list, and it conflicts with 10.4 ("an
   id ... has no length bound of its own") and 3.5 rule 8.
4. *No end record.* "A complete record stream followed by a clean exit" has no definition of complete; a worker that catches an exception and exits 0
   leaves a truncated stream that looks finished.
5. *Where validation runs.* L1 holds "parser and validator"; the text does not say whether range checks (`inf`, `nan`, margins) run in the worker or in
   the Runner on the decoded records. In the worker, a compromise bypasses all of them.

**A measured alternative.** [probe, confined interpreter on `sandbox-exec -p <repository strict profile>`, Python 3.14, load about 30] One new
confined interpreter per input: median 40 ms, best 36 ms (n = 15), so 600 files cost about 24 s. One confined zygote that has imported `tomllib`, `json`
and `xml.etree` once and **forks a child per input** (the child parses and exits; the zygote never parses): median 1.6 ms, p95 2.5 ms (n = 300), so 600
files cost about 1 s. A 5000-digit TOML integer raised `ValueError` in the forked child only; the zygote lived and answered the next input.

**Change.** Replace the long-lived worker by a zygote that forks per input: each child gets its own `RLIMIT_CPU` and memory share, holds no state from
another input, and has no write access (the unpack is a separate one-shot child with `tree/` and exits before any result is parsed). The stream is
`(tag, length, value)` with `length` at most the remaining input cap, no per-tag constant, ends with an `END` record that carries the record count and
byte count, and the Runner decodes it with the one non-recursive function and validates every range on the decoded values. The cap on the stream is
the input cap times a measured expansion, listed in section 15. Tests: the 5000-digit integer, a UTF-16 DOCTYPE, a truncated stream with a clean exit,
600 small inputs inside the overhead test.

### S11. The scratch copy as specified is not buildable (Medium)

**Attack.** 10.2 says "APFS `clonefile` where the volume has it, a copy otherwise, from regular files it opens without following links". [probe] on
this Mac:

- `clonefile(dir)` copies the whole directory: links (as links), FIFOs, extended attributes (`com.apple.metadata:test`, `com.apple.provenance`) and the
  BSD flags (`UF_IMMUTABLE`, flags `0x2`). It cannot select the transported set, so `.git`, ignored build output and untracked secrets travel.
- One unreadable (mode 0) sub-directory fails the whole call with `EACCES` and leaves nothing.
- The path-based call follows an **intermediate** link even with `CLONE_NOFOLLOW`: `clonefile('src/sub/escape/secret.txt', ...)` copied a file from outside
  the tree. `fclonefileat` exists in libSystem; Python has no binding.
- A clone is copy-on-write: writing the clone left the source file unchanged (the shared block is not writable through the clone).
- A confined child with the scratch tree writable ran `chflags uchg` on a file and a directory and `chmod 000` on a directory. `shutil.rmtree` then
  failed with `PermissionError` and the tree stayed on disk; so does a copy made from a source that carries the flags. A removal must clear the flags
  and modes top-down first. Each run of a hostile project leaves an undeletable copy of itself.
- On a case-insensitive or normalization-insensitive volume two tracked paths that differ only in case or form collide in the copy (A13 names the
  check for devices only).

**Change.** Build the copy file by file from the transported list: `openat` each component with `O_NOFOLLOW`, `fstat` regular file, then
`fclonefileat(srcfd, dstdirfd, name, CLONE_NOFOLLOW)` (a fallback copies from the descriptor), strip flags and extended attributes, normalise the mode
to the two modes of 6.3; never `clonefile` a directory. Refuse a path that collides under case folding or NFC on the destination. Remove the copy
with a descriptor walk that clears `UF_IMMUTABLE`, `SF_*` flags and sets `u+rwx` before it unlinks; or make the copy inside the sized image of 10.6 and
remove it by detaching and deleting the image. Tests: a source with a FIFO, a link, a flagged file and a mode-0 directory; a child that sets flags and
modes in its copy; the removal leaves nothing.

### S12. The Own child's profile breaks `cargo build` and does not say whether it has a network (Medium)

**Attack.** [probe] `cargo build --offline` of a one-crate project under `sandbox-exec`: no profile `rc=0`; the narrow deny alone `rc=0`; the narrow deny
with `(deny file-link)` and `(deny file-clone)` **`rc=101`, `Operation not permitted (os error 1)`**. Separated: `(deny file-link)` alone builds (cargo falls
back to a copy); `(deny file-clone)` alone fails (cargo clones). `poolhouse.sandbox.seatbelt.profile` emits both denies whenever `deny_read` or
`deny_write` is set [code], so the new open mode, if built on it, fails Poolhouse's own native build step as an Own child in step 3(d). The key needs
neither deny: its read is denied, and the hard link and the clone of it were refused with both denies removed [probe: ancestors profile without
them].

10.2 says a named run on macOS "gets loopback with the device's own service ports denied" and unix sockets denied; it does not say whether the child
has the rest of the network. Loopback only breaks every project whose steps fetch (`cargo`, `npm install`, `pip install`); the whole network gives
a hostile named child an exfiltration path that the Owner-zone deny does not address (it already runs as the user, 10.1).

**Change.** The Own profile carries no `(deny file-clone)` and no global `(deny file-link)` (S2). State the Own child's network in 10.2 and test it:
open network with the service ports denied by number, or loopback only with the consequence named. Add `cargo build` of a neutral crate as an Own
child to the tests of step 3(b).

### S13. Control units (Low)

- *Rows.* 6.4 says the sender checks "that every assigned id has exactly one row and no other id appears", and the control units are not assigned. A
  reserved id grammar for control rows, outside the project's id grammar (10.4), makes both rules hold; a project id inside it is refused.
- *One canary.* A single canary file tests one path. A sandbox that fails open for the home directory or the state location and holds for the canary's
  location passes. Plant one canary per class of path the profile denies (the home directory, beside the state location, the pool's home), and add a
  loopback listener the unit must fail to reach.
- *Local confinement is unmeasured.* The control units run in shards for devices (6.4). The principal defence of 10.2 is the confinement of handed
  code on this machine, and a profile that fails open there (a text error that does not compile closed, a path list that is `/`) is invisible. Run the
  confinement control once per confined local run and per profile shape (unit, git, worker) before any project code; a failure is status 4 `the
  sandbox did not confine`. `Seatbelt.available()` checks only that the binary exists [code].
- *The nonce is not a secret* (the device sees it in the start request); it proves freshness only, as 6.4 says. No change.

### S14. Root discovery, a project outside git, `init` (Low)

- *The walk.* 4 walks up to the first directory with a `testfarm.toml` or a `.git`. A zip extracted without its wrapping directory leaves a
  `testfarm.toml` in `~/Downloads`; any project below it without a marker takes `~/Downloads` as its root.
- *A project outside git has no transported set.* 4.9 requires `<root>/.git` to exist, and the scope text and 5.4 define a "project outside git". For the
  copy of the unnamed root above, the set would be the whole directory: the personal files beside it are readable by the hostile confined `LIST`,
  whose output frames reach the terminal (no network is needed for that). For a named one it is what is sent to a device (A19).
- *`init` writes into the checkout.* "Never overwrites a file" is an existence test; a dangling link `testfarm.toml` -> a start-up file passes
  `os.path.exists` and is written through. The runner never writes through a path a project can change (3.5 rule 1) applies to the checkout too.
- *Listing.* `git ls-files --cached` without `-z` quotes unusual paths [doc: `git-ls-files(1)`]; parsing quoted output is a place to inject a path.

**Change.** The walk stops at the working directory unless a marker is found within the same repository root; a project outside git has a transported
set only from `[tree] include` and DEPS globs, never the directory; `init` and `own` create files with `O_CREAT|O_EXCL|O_NOFOLLOW` through a held
directory descriptor; the git lists use `-z`.

### S15. `owner_paths()` (Low)

3.1 says the core names "no pool home, no state root and no credential module", and 3.5 says `owner_paths()` returns "the pool's home and allow list
and `~/.ssh`". Either L2 imports the pool home (breaks check A) or the host supplies it. If the host supplies it through the settings, a process
that does not load the host denies nothing of the pool's home.

**Change.** `owner_paths()` is the union of what the core owns and what installed packages contribute through an entry-point group, independent of the
settings, and no package can remove a path. It is total (it reads no file, creates nothing, never raises) and the guards treat an exception as
"refuse". It lists paths that do not exist yet. Test: the Own child of a process that never loaded the host still cannot read the pool's home.

### S16. Text conflicts a builder will meet

- 4.9 bullet 2 ("the root is a work-tree top-level, `<root>/.git` exists") against the scope bullet and 5.4 ("a project outside git").
- 5.1 order (S8).
- 6.4 "no other id appears" against control unit rows (S13).
- 6.6 item 4 "a fixed bound per tag" against 10.4 and 3.5 rule 8 (S10).
- 3.1 "names no pool home" against `owner_paths()` (S15).
- 5.5: the permit is a capability by descriptor, and the broker "refuses a request whose connecting process cannot be turned into a live handle";
  for a socketpair the connecting process is the creator. Say which model each channel uses (N12 b).
- 4.8: `check` reports a settings file that is a link, not owned or group-writable; the run should treat it as absent and print why.
- 5.5 names `poolhouse.serve`'s default of one slot (`--parallel 1`) in a core section; 15 says it is not a runner number. Keep the sentence in the
  provider's text only.
- 10.2 "Its children get the allowlist environment of 10.5" and `HOOKS.environment` (S5 item 7).

## 4. Rows of 10.8 whose answer changes

| Row | Now | Because |
|---|---|---|
| A7 | partly | S3 (guards are literal text; no verification at use) |
| A21 | partly | S5 (no process ceiling on macOS; the polled kill is slower than a fork storm) |
| A22 | held | S1 and S9 are build conditions of it |
| A29, A32 | partly | S2 and S6 |
| A35 | partly | S1 (another volume, a recreated directory) |
| A36 | held | S9 (the confined git must exist before the claim is true) |
| A37 | held | S7 (the copy is not the only thing a confined run leaves) |
| A38 | partly | S2, S6 |
| A39 | held | S5 (`None` limits), S8 |
| A44 | partly | S5 (the descriptor kinds, the terminal) |

## 5. What held

- The zones themselves: every direct attack on the Owner zone (read, write, rename out, link out, clone out, alternate spellings) was refused; nothing
  required moving a boundary.
- `(allow default)` plus a literal deny on a path that does not exist yet refuses its creation, so the settings file can be denied before it exists.
- Seatbelt matches the resolved path: lower case, upper case, NFD, `/System/Volumes/Data` and `/tmp` spellings of a denied path were refused.
- A clone is copy-on-write: writing the clone did not change the source.
- `launchctl bootstrap` and `submit` fail inside any `sandbox-exec` profile on this macOS; the repository's deny-default profile refuses `TIOCSTI` on
  `/dev/tty`; `start_process` starts a new session.
- `RLIMIT_NPROC` stops a child's fork storm on macOS (bounded probe).
- APFS does not reuse inodes (200 recreations, monotonic).
- `PathFinder.find_spec` of a top-level name loads nothing.
- A deny emitted after an allow of its parent wins: a deny-default profile that reads `<zone>` with `<zone>/owner` denied later refused the key
  [probe]; the repository's generator emits denies last [code]. This is the structural answer to "does an allow rule lie inside a deny".
- A link in the copy that points at a file outside the profile is refused to a confined child (`escape` -> a file beside the copy: EPERM); one that
  points at a system path the profile allows (`/etc/hosts`) is read, as the path itself is [probe].
- `person_auth.consume` takes one use under a file lock, bound to kind, target and session; a second consume of the same authorization finds none
  [code]. A replay of the evidence fails; the weakness is binding (S3 items 2 and 3), not replay.
- The literal classifier of 4.9: the four fixtures of N1, the worktree back-pointer, the copies, the move and the replacement on APFS.
- `docs/limits.md` holds the rows the design cites for the 128-bit and 256-bit values (shard id, run id, install-instance id, store master key, control
  nonce, frame nonce).

## 6. Numbers and repository knowledge

- *Invented or unsourced numbers.* "A fixed bound per tag" (6.6 item 4, S10); `Limits` carries `60.0` and `1_000_000` as defaults [code] and an explicit
  `None` is also a choice with no source (S5). The ceiling of the process count has no number in the text (S5 derives it from `kern.maxprocperuid`).
  No other invented number was found in the step-3 sections.
- *The measured claim that is too strong.* "`RLIMIT_CPU`, which works on macOS [probe]" (6.6 item 4, 10.6): it stops a loop that does not poll the CPU
  clock and does not stop one that does; it is cumulative (S10).
- *Repository knowledge in the core.* `owner_paths()` listing the pool's home and `~/.ssh` (S15); 5.5 stating `poolhouse.serve`'s slot default;
  the config path `~/.config/poolhouse/` is the product's own and is fine. Nothing else in 3, 4.8, 4.9, 6.6 or 10.2 names a repository's file, marker or
  script.

## 7. What was run, and what was not

Run [probe]:

1. Inode reuse on APFS (200 recreations) and in a Linux container (50 recreations, `statx` birth time, `lsattr -v`).
2. Two disk images per filesystem (exFAT, HFS+, APFS) mounted in turn at one mount point: `st_dev`, `st_ino`, birth time, `ATTR_VOL_UUID`; device
   number reuse and change across mounts.
3. The Owner-zone profile of the design (plain), with the ancestors unlink deny, with a symlinked config directory, and with the link literals: about
   twenty operations each (S2).
4. `TIOCSTI` from a sandboxed child on a pty: shared session, new session with the terminal as descriptor 0, new session with `/dev/null`; the
   repository's deny-default profile with `strict` false and true.
5. `launchctl bootstrap` and `submit` unsandboxed and under `sandbox-exec`.
6. Nested `sandbox-exec` and `sandbox_init`.
7. A confined `git ls-files` under the repository's profile, four read sets.
8. A confined interpreter per input against a fork per input from a confined zygote; the 5000-digit integer.
9. `RLIMIT_CPU` (loop kinds, cumulative); `RLIMIT_NPROC` (bounded).
10. `clonefile` of a tree, intermediate link, mode 0 directory, flags, attributes, copy-on-write; the removal of a copy a child made undeletable.
11. `cargo build` under four profiles; `(deny file-link)` and `(deny file-clone)` separately.
12. `realpath` against `F_GETPATH` for case and normalization; `Path.home()` with `HOME` moved; relative worktree back-pointers.
13. The literal classifier of 4.9 on 13 cases.
14. `PathFinder.find_spec`; the bash guard against a path built at run time (existence check).
15. An allow of a parent with a later deny of its child; an inherited descriptor on a denied file; links in the copy that point out.

Not run:

- **The `core.fsmonitor` and `core.hooksPath` override attack** (N2): the bash guard refused a script that named `core.hooksPath` and the earlier pass met
  the same refusal. I removed the guarded names from the probe and did not rebuild them at run time.
- **Linux sandbox probes on the WSL device**: bubblewrap, the PID namespace, `pidfd`, a mask of a missing path, a bind mount over a named path (needs
  privileges). The Linux claims of S1 (inode reuse) come from a container's overlay filesystem, which is not the WSL device's ext4; they are labelled.
- **LaunchServices `open` of a script** (S6): it opens a window on the owner's screen.
- **A fork storm** (S5): unsafe on a shared machine; only the bounded limit was run.
- **The PersonGate flow** (S3): not built. The analysis reads `person_release`, `person_auth` and `docs/person-delegation.md`.
- **A live `fclonefileat`**: only its presence in libSystem; the per-file walk is the proposal, unbuilt.
- **Touch ID or any keystore use**: out of scope and person-only.

## 8. Step 3, and what each commit must absorb

**Step 3 may start when the text has absorbed S1, S2, S3 and S4 and a reviewer other than the author has re-read the edited 3.1, 3.5, 4.7, 4.8,
4.9, 5.1, 6.6 item 4 and 10.2.** The edits are of the text and its tests; the structure of 3.5 stands. Step 3(a) is not blocked by anything here.

| Commit | Absorb before it is built |
|---|---|
| 3(a) divide, assign, schedule, merge, report, presets | nothing from this pass |
| 3(b) local transport, executor, spawn function, Parser, git, scratch copy, Owner deny | S1 (record and classification reads), S2, S4, S5, S7, S8, S9, S10, S11, S12 |
| 3(c) settings, identity, `owner_paths()`, `PersonGate`, `own`, `target`, guards | S1, S3, S6, S15; S14 (`init` and `own` file creation) |
| 3(d) Poolhouse's `testfarm.toml` | S4 (launcher, the 27 Seatbelt modules, `own_profile`), S12 (`cargo build` as an Own child) |
| 8(c) control units | S13 |

Tests to write first, each watched failing against the naive implementation named: the two same-label images and the recreated directory (S1, naive
record `(realpath, st_dev, st_ino)`); the rename of the parent and the link replacement (S2, profile without the ancestors and link denies); the
rendered-question and wrong-digest tests (S3, a gate that returns `yes`); a unit that runs `sandbox-exec` (S4); a pty child with `TIOCSTI`, `Limits(None)`
(S5); a confined step that plants a product before `own` (S7); an unattended run with both margins (S8); git on an Apple-shim install (S9); 600 inputs
through the worker and a truncated stream (S10); a source with a FIFO, a flag and a mode-0 directory, and a child that makes its copy undeletable (S11);
`cargo build` as an Own child (S12).
