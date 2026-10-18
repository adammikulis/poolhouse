# Worktrees: landed or work

Nothing is recorded about a worktree. Git is asked each time, and a tree is one of two things.
The development branch is the branch the primary checkout is on, and the primary checkout is never a row.
Code: `src/poolhouse/trees.py` and `scripts/worktrees`.

| state | meaning |
| --- | --- |
| landed | clean, and every commit is in the development branch: `git cherry` shows no patch the branch lacks and no merge commit is outside it |
| work | anything else: a commit development lacks, a changed or untracked file, an ignored file that is not rebuildable, a merge or rebase in progress |

Clean means `git status --porcelain --ignored` shows nothing except the ignored files a build or a test run
recreates: `dist/`, `build/`, `packaging/build/`, `.build-venv/`, `.build-work/`, `node_modules/`,
`__pycache__/*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.testmondata*` and `.coverage*`
(`REBUILDABLE` in `trees.py`). Any other ignored file, such as a key or a private note, and any nested repository
make the tree work.

A work tree is never removed automatically. A landed tree is removed, with its branch, by `sweep` once nothing in
it has changed for ten minutes (its directory, HEAD, index or HEAD log).

```
scripts/worktrees                                   # one line per tree: branch, ahead and behind, age, landed or work
scripts/worktrees sweep [--min-age SECONDS]         # remove landed trees untouched for 600 seconds
scripts/worktrees close TREE --landed               # a landed tree, now
scripts/worktrees close TREE --bundle               # unlanded commits go to a verified git bundle first
scripts/worktrees close TREE --abandon "reason"     # a clean tree whose commits stay on its branch
```

The report prints, for every tree, the branch, commits ahead of and behind development, the age of its newest
commit, the path, and `[landed|work unlanded=N dirty=N]`. It exits 1 when a tree is over a hygiene limit
(10 commits, 30 ahead, 20 behind, or behind and not merged within a day).

`close` runs from outside the tree and never on the primary checkout. `--landed` refuses a tree that is work.
`--bundle` refuses dirty files and a tree with nothing to bundle, writes the commits to
`poolhouse-bundles/` in the git directory, verifies that the bundle holds the tip, then removes the tree and sets
the branch description to `bundled: PATH`. `--abandon` needs a reason, refuses dirty files and sets the
description to `abandoned: REASON`. Removal never forces: a locked tree stays, and `landed` deletes the branch with
`git branch -d`, keeping it (and saying so in the result) when git calls it unmerged. `sweep` leaves locked trees
and the landing runner's `land/` trees, which the runner removes itself.

A tree removed some other way (`git worktree remove`, `rm -rf` then `git worktree prune`) leaves nothing to
reconcile.
