# Worktrees: landed or work

Nothing is recorded about a worktree. Git is asked each time, and a tree is one of two things.
The development branch is the branch the primary checkout is on, and the primary checkout is never a row.
Code: `src/poolhouse/trees.py`, `src/poolhouse/gitstate.py` and `scripts/worktrees`.

| state | meaning |
| --- | --- |
| landed | clean, and every commit is in the development branch: `git cherry` shows no patch the branch lacks and no merge commit is outside it |
| work | anything else: a commit development lacks, a changed or untracked file, an ignored file that is not rebuildable, an edit the index hides, a merge or rebase in progress, a tree git cannot read |

Clean means `git status --porcelain --ignored` (submodule edits included, whatever `submodule.*.ignore` says) shows
nothing except the ignored files a build or a test run recreates: `dist/`, `build/`, `packaging/build/`,
`.build-venv/`, `.build-work/`, `node_modules/`, `__pycache__/*.pyc`, `.pytest_cache/`, `.mypy_cache/`,
`.ruff_cache/`, `.testmondata*` and `.coverage*` (`REBUILDABLE` in `gitstate.py`). **Those ignored files are
deleted with the tree by `close` and `sweep`**; nothing keeps them. Any other ignored file, such as a key or a
private note, and any nested repository make the tree work.

Clean also means no index entry hides an edit: a file marked `--assume-unchanged`, `--skip-worktree` or
fsmonitor-valid (`git ls-files -v -f` shows it in lowercase or as `S`) makes the tree work, because `git status`
does not show the file. `gitstate.hidden` is the one place that decides this; the test-result hash
(`poolhouse.activity.gate.tree_hash`) asks it too. In a sparse checkout, a skip-worktree entry whose file is absent is
the commit's own content and does not count.

A work tree is never removed automatically. A landed tree is removed by `sweep` once nothing in it has changed for
ten minutes (its directory, HEAD, index or HEAD log) and no process has its working directory inside it. `sweep`
deletes the branch too, unless the branch was created less than a day ago, when the tree goes and the branch stays.
`sweep` ends with `scripts/primary_sync.py`, which fast-forwards the primary checkout to `origin/<dev>` (docs/landing.md,
"The primary checkout follows origin").
A tree git cannot read (a corrupt `.git` file, a git command that stalls) is listed as unreadable work, flagged by the
report, and never breaks the listing.

```
scripts/worktrees                                   # one line per tree: branch, ahead and behind, age, landed or work
scripts/worktrees sweep [--min-age SECONDS]         # remove landed trees untouched for 600 seconds, then primary_sync
scripts/worktrees close TREE --landed               # a landed tree, now
scripts/worktrees close TREE --bundle               # unlanded commits go to a verified git bundle first
scripts/worktrees close TREE --abandon "reason"     # saves the uncommitted changes, removes the tree, keeps the branch
```

The report prints, for every tree, the branch, commits ahead of and behind development, the age of its newest
commit, the path, and `[landed|work unlanded=N dirty=N]`. It exits 1 when a tree is over a hygiene limit
(10 commits, 30 ahead, 20 behind, or behind and not merged within a day).

`close` runs from outside the tree and never on the primary checkout. `--landed` refuses a tree that is work.
`--bundle` refuses dirty files and a tree with nothing to bundle, writes the commits to
`poolhouse-bundles/` in the git directory, verifies that the bundle holds the tip, then removes the tree and sets
the branch description to `bundled: PATH`. `--abandon` needs a reason and is the explicit discard: a dirty tree is accepted, after
`git diff HEAD` (`NAME-HEAD.dirty.diff`) and a tar of every changed, untracked, hidden and non-rebuildable ignored file
(`NAME-HEAD.dirty.tar`) are written next to the bundles and the removal refuses if they cannot be written; it then sets the
description to `abandoned: REASON`. Removal forces only `--abandon` of a dirty tree, after the files are saved: a locked tree stays, and `landed` deletes the branch with
`git branch -d`, keeping it (and saying so in the result) when git calls it unmerged. `sweep` leaves locked trees
and the landing runner's `land/` trees, which the runner removes itself.

A tree removed some other way (`git worktree remove`, `rm -rf` then `git worktree prune`) leaves nothing to
reconcile.

## A subagent's stop

The Claude `SubagentStop` hook (`scripts/hooks/claude-subagent-stop`, `scripts/hooks/stop_landed.py`) refuses to let a
subagent stop while a tree of its own is work: the worktree the harness made for it (`agent-ID`, ID being the agent id)
and the trees named by the worktree and branch claims it holds. A tree it only stopped in, and the primary checkout, are
not its own. The test is the one in the table above, against `origin/<development>` (the local development branch when
origin has none): a commit that branch lacks, a changed or untracked file, an unfinished merge or rebase, or a tree git
cannot read. A subagent whose directory is already gone, or that stopped outside any checkout, is let through.

The refusal is one sentence that names the tree and says to land it with `scripts/land`, or, when the work is
superseded or cannot land, to run `scripts/worktrees close TREE --bundle` (`--abandon 'reason'` for uncommitted files).
The second refusal of the same subagent also posts `announce blocked`; the third stop goes through, posts a milestone
naming the branch and tree, and the tree stays as work for `scripts/worktrees` and its cleanup. That stop announces no
`done` and does not retire the subagent: its identity and token stay, so the lead can resume it to claim and land after a
review accept. A stop that finds everything landed (or the tree closed or gone) forgets the count, announces `done`
and retires the subagent. The count lives in the hooks' state directory (`POOLHOUSE_HOOK_STATE`).
