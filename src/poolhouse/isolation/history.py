"""The git history of a stage: what lets git work in the box, in a worktree or not.

A stage is a repository of its own. The parent packs into it the newest `DEPTH` commits of the project's `HEAD` (shallow past them), on a branch
of the same name; the project's own git directory is never granted, and no blob a commit holds under a credential name is packed.

Git in the stage names nothing it lacks, so a tool can write the index's tree (`git write-tree`): a replace ref has each tree that holds a
credential name read as that tree without it. Commits keep their ids; status, diffs, the log and the index show neither the file nor its change or
deletion. Content `HEAD` also holds under an ordinary name, and the empty file's, are given. Publication reads only the working copy.

The index keeps the working copy's files, so `git diff HEAD` is the uncommitted change with the session's and `git diff` the session's alone.
Other files no session is given (`excluded`) stay as `HEAD` has them, skip-worktree. `.git/REVIEW` holds three views for one that only reads.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from poolhouse import secretpaths
from poolhouse.activity import source_snapshot

__all__ = ["DEPTH", "REVIEW", "committed", "give"]

DEPTH = 200
"""The commits of `HEAD`'s history a stage holds: enough to read a branch's log and a change's parents, packed in about a second."""
REVIEW = "poolhouse-review"
"""The folder of the stage's git directory that holds `status.txt`, `changes.diff` and `log.txt`, written once at the start."""
VIEWS = (("status.txt", ["status", "--short", "--branch"]), ("changes.diff", ["diff", "--no-ext-diff", "--no-textconv", "HEAD"]),
         ("log.txt", ["log", "-n", "30", "--name-status"]))
FOLDER, SUBMODULE = b"40000", b"160000"
"""The modes of the entries of a tree that are no file: a folder's tree and another repository's commit."""
Entry = tuple[bytes, bytes, str]
"""One entry of a tree: its mode, its name and its object."""


def _git(where: Path, words: Sequence[str], given: bytes = b"") -> bytes:
    """Git's output for ``words`` in ``where`` with ``given`` on its input, with no configuration of the account's and no hook."""
    done = subprocess.run([str(source_snapshot.actual_git()), "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull, *words],
                          cwd=where, env=source_snapshot.git_environment(), input=given, capture_output=True, timeout=120, check=False)
    if done.returncode:
        raise RuntimeError(f"git {words[0]} failed: {done.stderr.decode(errors='replace').strip()[:300]}")
    return done.stdout


def _under(path: bytes, names: Sequence[bytes]) -> bool:
    return any(path == name or path.startswith(name + b"/") for name in names)


def _read(source: Path, names: Sequence[str]) -> dict[str, list[Entry]]:
    """The entries of each tree of ``names``, in the order git holds them."""
    said = _git(source, ["cat-file", "--batch"], "".join(f"{name}\n" for name in names).encode())
    found, at = {}, 0
    for name in names:
        head = said.index(b"\n", at)
        told = said[at:head].split()
        if len(told) != 3 or told[1] != b"tree":
            raise RuntimeError(f"git cat-file failed: {name} is no tree")
        body, at = said[head + 1:head + 1 + int(told[2])], head + 2 + int(told[2])
        entries, here, width = [], 0, len(name) // 2
        while here < len(body):
            end = body.index(b"\0", here)
            mode, _, entry = body[here:end].partition(b" ")
            entries.append((mode, entry, body[end + 1:end + 1 + width].hex()))
            here = end + 1 + width
        found[name] = entries
    return found


def _walked(source: Path, roots: Sequence[str]) -> tuple[dict[str, list[Entry]], dict[str, set[bytes]]]:
    """Every tree under ``roots`` with its entries, and for each the names of the files in it that some path to it names as a credential."""
    trees: dict[str, list[Entry]] = {}
    named: dict[str, set[bytes]] = {}
    hits: dict[bytes, bool] = {}
    seen: set[tuple[str, bytes]] = set()
    level = [(root, b"") for root in roots]
    while level := [each for each in dict.fromkeys(level) if each not in seen]:
        seen.update(level)
        trees.update(_read(source, list(dict.fromkeys(tree for tree, _ in level if tree not in trees))))
        deeper = []
        for tree, folder in level:
            for mode, name, child in trees[tree]:
                path = folder + name
                if mode == FOLDER:
                    deeper.append((child, path + b"/"))
                    continue
                if path not in hits:
                    hits[path] = mode != SUBMODULE and bool(secretpaths.name_hit(os.fsdecode(path)))
                if hits[path]:
                    named.setdefault(tree, set()).add(name)
        level = deeper
    return trees, named


def _held_back(source: Path, head: str, trees: dict[str, list[Entry]], named: dict[str, set[bytes]]) -> set[str]:
    """The blobs the stage is not given: each a commit holds under a credential name, but the empty one and those a file of an ordinary name
    holds under ``head``, the tree of `HEAD`."""
    held = {child for tree, names in named.items() for _, name, child in trees[tree] if name in names}
    held.discard(_git(source, ["hash-object", "-t", "blob", "--stdin"]).decode().strip())
    ahead, seen = [head], set()
    while ahead:
        tree = ahead.pop()
        if tree in seen:
            continue
        seen.add(tree)
        for mode, name, child in trees[tree]:
            if mode == FOLDER:
                ahead.append(child)
            elif name not in named.get(tree, ()):
                held.discard(child)
    return held


def _leave_out(stage: Path, roots: Sequence[str], trees: dict[str, list[Entry]], named: dict[str, set[bytes]], held: set[str]) -> None:
    """Have the stage read each tree that names a credential, or a blob held back, as that tree without it (`git replace`). A folder left with
    nothing in it goes too, as no index holds one; the tree of a commit stays, empty."""
    made: dict[str, bytes] = {}
    replaced: dict[str, str] = {}
    read: dict[str, str] = {}

    def without(tree: str) -> str:
        """The tree the stage reads for ``tree``: itself or another; empty text where nothing is left of it."""
        if tree not in read:
            kept = []
            for mode, name, child in trees[tree]:
                if mode == FOLDER:
                    child = without(child)
                elif name in named.get(tree, ()) or child in held:
                    continue
                if child:
                    kept.append((mode, name, child))
            read[tree] = tree
            if kept != trees[tree]:
                body = b"".join(mode + b" " + name + b"\0" + bytes.fromhex(child) for mode, name, child in kept)
                other = hashlib.new("sha1" if len(tree) == 40 else "sha256", b"tree %d\0" % len(body) + body).hexdigest()
                made[other], replaced[tree] = body, other
                read[tree] = other if kept else ""
        return read[tree]

    for root in roots:
        without(root)
    if not replaced:
        return
    with tempfile.TemporaryDirectory(dir=stage / ".git") as folder:
        for name, body in made.items():
            (Path(folder) / name).write_bytes(body)
        written = _git(stage, ["hash-object", "-w", "-t", "tree", "--stdin-paths"], "".join(f"{folder}/{name}\n" for name in made).encode())
    if written.decode().split() != list(made):
        raise RuntimeError("git hash-object failed: a tree without its credential names was not written as it was made")
    _git(stage, ["update-ref", "--stdin"], "".join(f"create refs/replace/{tree} {other}\n" for tree, other in replaced.items()).encode())


def _commits(source: Path, stage: Path) -> str:
    """Pack the newest commits of the project's `HEAD` into the stage and point a branch of the same name at it; the commit."""
    head = _git(source, ["rev-parse", "--verify", "HEAD"]).decode().strip()
    rows = [line.split() for line in _git(source, ["rev-list", "--parents", f"--max-count={DEPTH}", head]).decode().splitlines()]
    held_commits = {row[0] for row in rows}
    shallow = [row[0] for row in rows if not set(row[1:]) <= held_commits]
    roots = _git(source, ["log", "--format=%T", f"--max-count={DEPTH}", head]).decode().split()
    trees, named = _walked(source, roots)
    held = _held_back(source, roots[0], trees, named)
    listed = [line.partition(b" ")[0] for line in _git(source, ["rev-list", "--objects", f"--max-count={DEPTH}", head]).split(b"\n") if line]
    wanted = [name for name in listed if name.decode() not in held]
    packs = stage / ".git" / "objects" / "pack"
    packs.mkdir(parents=True, exist_ok=True)
    _git(source, ["pack-objects", "--quiet", str(packs / "history")], b"\n".join(wanted) + b"\n")
    if shallow:
        (stage / ".git" / "shallow").write_text("".join(f"{each}\n" for each in shallow), encoding="ascii")
    try:
        branch = _git(source, ["symbolic-ref", "--quiet", "HEAD"]).decode().strip()
    except RuntimeError:
        branch = ""
    _leave_out(stage, roots, trees, named, held)
    if branch.startswith("refs/heads/"):
        _git(stage, ["update-ref", branch, head])
        _git(stage, ["symbolic-ref", "HEAD", branch])
    else:
        _git(stage, ["update-ref", "--no-deref", "HEAD", head])
    return head


def committed(source: Path) -> bool:
    """Whether the project has a commit, so that there is a history to show."""
    try:
        return bool(_git(source, ["rev-parse", "--verify", "--quiet", "HEAD"]))
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return False


def give(source: Path, stage: Path, excluded: Sequence[bytes]) -> str:
    """Give the stage the project's history (see the module). Empty when it did or the project has none, else why git in the box will not
    show it."""
    if not committed(source):
        return ""
    try:
        head = _commits(source, stage)
        kept = [row for row in _git(stage, ["ls-tree", "-r", "-z", head]).split(b"\0") if row and _under(row.partition(b"\t")[2], excluded)]
        if kept:
            entries = (b"%s %s\t%s" % (row.split()[0], row.split()[2], row.partition(b"\t")[2]) for row in kept)
            _git(stage, ["update-index", "-z", "--index-info"], b"\0".join(entries) + b"\0")
            _git(stage, ["update-index", "-z", "--skip-worktree", "--stdin"], b"\0".join(row.partition(b"\t")[2] for row in kept) + b"\0")
        views = stage / ".git" / REVIEW
        views.mkdir(exist_ok=True)
        for name, words in VIEWS:
            (views / name).write_bytes(_git(stage, words))
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as why:
        return str(why)
    return ""
