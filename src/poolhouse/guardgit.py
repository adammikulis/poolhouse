"""Whether a git command puts guard code no review has landed into a checkout whose files run as the guards: a merge, pull, switch,
checkout, restore, reset, rebase, pick or patch that would change it to content that `origin` holds on no reviewed branch. Standard library
only: `protectedwrites` loads it by file and hands it `guardpaths`."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from types import ModuleType

__all__ = ["incoming"]

SKIPPED = {"merge": {"-m", "--message", "-s", "--strategy", "-X", "--strategy-option", "-F", "--file"},
           "rebase": {"-s", "--strategy", "-X", "--strategy-option", "-x", "--exec"},
           "switch": {"-c", "-C", "--create", "--force-create", "--orphan"}, "checkout": {"-b", "-B", "--orphan"},
           "cherry-pick": {"-m", "--mainline", "-X", "--strategy", "--strategy-option"}, "revert": {"-m", "--mainline", "-X", "--strategy"}}
"""The options of a command whose next word is not a revision or a path."""
VERBS = frozenset({*SKIPPED, "pull", "restore", "reset", "am", "apply", "bisect", "read-tree", "checkout-index", "update-index"})
REPLAYS = frozenset({"merge", "rebase", "cherry-pick", "revert"})
"""The commands that add the changes of a revision to what is checked out; the others put a revision's content there."""
GLOBALS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})
UNREAD = ("$", "SUB", "`")
FOLDERS = ("scripts", "src/poolhouse/credentials", "src/poolhouse/workspace")
"""The folders that hold the tracked guard code."""
TIMEOUT_S = 10


def _git(top: str, *args: str) -> str:
    """What ``git args`` prints in the checkout at ``top``, empty when it fails."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    done = subprocess.run(["git", "--no-replace-objects", "-C", top, *args], capture_output=True, text=True, timeout=TIMEOUT_S, check=False,
                          env=env)
    return done.stdout if done.returncode == 0 else ""


class Tree:
    """The guard code of one checkout: what a revision, the index and the files on disk hold of it."""

    def __init__(self, rules: ModuleType, top: str, common: str) -> None:
        self.rules, self.top = rules, top
        names = [f"refs/remotes/origin/{name}" for name in rules.published(common)]
        self.published = [self.at(ref) for ref in names if self.commit(ref)]

    def commit(self, word: str) -> str:
        return _git(self.top, "rev-parse", "--verify", "-q", "--end-of-options", f"{word}^{{commit}}").strip()

    def target(self, word: str) -> str:
        """The commit a switch to ``word`` checks out: its own, else that of the remote branch git would make it from."""
        remote = _git(self.top, "for-each-ref", "--format=%(objectname)", f"refs/remotes/*/{word}").split()
        return self.commit(word) or (remote[0] if remote else "")

    def _listed(self, *args: str) -> dict[str, str]:
        """``path: object`` of the guard code that `ls-tree` or `ls-files -s` lists."""
        found = {}
        for row in _git(self.top, *args, "--", *FOLDERS).split("\0"):
            meta, _, name = row.partition("\t")
            words = meta.split()
            if name and self.rules.CODE.fullmatch(name):
                found[name] = words[2] if words[1] in ("blob", "commit") else words[1]
        return found

    def at(self, revision: str) -> dict[str, str]:
        return self._listed("ls-tree", "-r", "-z", revision)

    def index(self) -> dict[str, str]:
        return self._listed("ls-files", "-s", "-z")

    def disk(self, name: str) -> str:
        return _git(self.top, "hash-object", "--", name).strip() if os.path.lexists(Path(self.top) / name) else ""

    def unlanded(self, new: Mapping[str, str], names: set[str]) -> list[str]:
        """Which of ``names`` would change, on disk, to content (``new``; absent: removed) that no reviewed branch holds."""
        return sorted(name for name in names
                      if new.get(name, "") != self.disk(name) and all(new.get(name, "") != held.get(name, "") for held in self.published))


def _words(verb: str, rest: list[str]) -> tuple[list[str], list[str]]:
    """The words of ``rest`` that may name a revision or a path, and the paths after `--`."""
    paths = rest[rest.index("--") + 1:] if "--" in rest else []
    before = rest[:rest.index("--")] if "--" in rest else rest
    words: list[str] = []
    i = 0
    while i < len(before):
        word = before[i]
        if verb == "restore":
            if word in ("-s", "--source"):
                words += [f"--source={before[i + 1]}"] if i + 1 < len(before) else []
                i += 1
            elif word.startswith("-s") and not word.startswith("--"):
                words.append(f"--source={word[2:]}")
            elif word.startswith("--so") and "=" in word:
                words.append(f"--source={word.split('=', 1)[1]}")
            elif not word.startswith("-"):
                words.append(word)
        elif word in SKIPPED.get(verb, ()):
            i += 1
        elif word == "-":
            words.append("@{-1}")
        elif word.startswith("--onto="):
            words.append(word.split("=", 1)[1])
        elif not word.startswith("-"):
            words.append(word)
        i += 1
    return words, paths


def _under(names: set[str], paths: list[str], moved: str) -> set[str]:
    """The ``names`` that the pathspecs ``paths``, given in the folder ``moved`` of the checkout, may reach."""
    if not paths or any(any(mark in path for mark in "*?[:") for path in paths):
        return names
    specs = [os.path.normpath(Path(moved) / path).casefold() for path in paths]
    return {name for name in names if any(spec == "." or name.casefold() == spec or name.casefold().startswith(spec + "/") for spec in specs)}


def _replayed(tree: Tree, verb: str, revisions: list[str]) -> list[str]:
    """The unlanded guard code that adding the changes of ``revisions`` would bring."""
    found: list[str] = []
    for word in revisions:
        first, dots, last = word.partition("..")
        revision = tree.commit(last.lstrip(".") or "HEAD") if dots else tree.commit(word)
        if not revision:
            continue
        if dots:
            base = tree.commit(first or "HEAD")
        elif verb in ("cherry-pick", "revert"):
            base = tree.commit(f"{revision}^")
        else:
            base = _git(tree.top, "merge-base", "HEAD", revision).strip()
        old, new = tree.at(base) if base else {}, tree.at(revision)
        if verb == "revert":
            old, new = new, old
        found += tree.unlanded(new, {name for name in {*old, *new} if old.get(name) != new.get(name)})
    return found


def _written(tree: Tree, verb: str, rest: list[str], moved: str) -> list[str]:
    """The unlanded guard code that a switch, checkout, restore, reset or read of a tree would write."""
    words, paths = _words(verb, rest)
    if verb == "restore":
        sources = [word.split("=", 1)[1] for word in words if word.startswith("--source=")]
        paths = [*paths, *(word for word in words if not word.startswith("--source="))]
    else:
        sources = [word for word in words if tree.target(word)][:None if verb == "read-tree" else 1]
        paths = [*paths, *(word for word in words if word not in sources)]
    if verb == "reset":
        if not any(word.startswith(("--h", "--me", "--k")) for word in rest):
            return []
        sources = sources or ["HEAD"]
    if verb in ("switch", "checkout") and not sources and not paths:
        return []
    found: list[str] = []
    for source in sources or [""]:
        new = tree.at(tree.target(source)) if source else tree.index()
        names = {*new, *(tree.at("HEAD") if source else ())}
        found += tree.unlanded(new, names if verb in ("reset", "switch", "read-tree") else _under(names, paths, moved))
    return found


def _patched(rules: ModuleType, rest: list[str], folder: Path) -> list[str]:
    """What a patch the command applies names of the guard code; a patch that cannot be read counts."""
    if set(rest) & {"--check", "--stat", "--numstat", "--summary"} and "--apply" not in rest:
        return []
    files = [folder / word for word in rest if not word.startswith("-")]
    if not files or not all(file.is_file() for file in files):
        return ["a patch the guard cannot read"]
    named = (word.removeprefix("a/").removeprefix("b/") for file in files for word in file.read_text(errors="replace").split())
    return sorted({word for word in named if rules.CODE.fullmatch(word)})


def _brought(tree: Tree, verb: str, rest: list[str], folder: Path) -> list[str]:
    rules = tree.rules
    if verb in ("am", "apply"):
        return _patched(rules, rest, folder)
    if verb == "bisect":
        return [] if rest[:1] in (["log"], ["view"], ["visualize"], ["reset"]) else ["the revisions a bisection checks out"]
    if verb == "update-index":
        if "--index-info" in rest:
            return ["an index entry the guard cannot read"]
        if not any(word.startswith("--cacheinfo") for word in rest):
            return []
        return [part for word in rest for part in word.split(",") if rules.CODE.fullmatch(part)]
    words, _ = _words(verb, rest)
    if any(mark in word for word in words for mark in UNREAD):
        return ["a revision the guard cannot read"]
    if verb == "pull":
        if words[:1] == ["."]:
            return _replayed(tree, "merge", words[1:])
        names = rules.published(rules.checkout(tree.top)[2])
        if words[:1] not in ([], ["origin"]) or any(spec not in names for spec in words[1:]):
            return ["what another remote or branch holds"]
        if words[1:]:
            return []
        upstream = _git(tree.top, "rev-parse", "--abbrev-ref", "@{upstream}").strip().removeprefix("refs/remotes/")
        return [] if upstream.removeprefix("origin/") in names else ["what the upstream of this branch holds"]
    if verb in REPLAYS:
        resumed = any(word in ("--abort", "--continue", "--quit", "--skip") for word in rest)
        return _replayed(tree, verb, words or ([] if resumed or verb not in ("merge", "rebase") else ["@{upstream}"]))
    moved = os.path.relpath(os.path.realpath(folder), tree.top)
    return _written(tree, verb, rest, "" if moved == "." else moved)


def incoming(rules: ModuleType, args: list[str], here: Path, expand: Callable[[str], str], work_tree: str = "") -> str:
    """Why the git command ``args``, run from ``here`` (``work_tree``: what `GIT_WORK_TREE` names), may not run because it would put
    unlanded guard code into a checkout whose files run as the guards; an empty string when it would not."""
    folder, i = here, 0
    while i < len(args) and args[i].startswith("-"):
        name, equals, value = args[i].partition("=")
        if not equals and name in GLOBALS:
            value, i = (args[i + 1] if i + 1 < len(args) else ""), i + 1
        if name == "-C":
            folder = folder / expand(value)
        elif name == "--work-tree":
            work_tree = value
        elif name == "-c" and value.lower().startswith("core.worktree="):
            work_tree = value.split("=", 1)[1]
        i += 1
    if i >= len(args) or args[i] not in VERBS:
        return ""
    verb, rest = args[i], [expand(word) for word in args[i + 1:]]
    folder = folder / expand(work_tree) if work_tree else folder
    found = _running(rules, folder)
    if found is None:
        return ""
    top, common = found
    try:
        brought = _brought(Tree(rules, top, common), verb, rest, folder)
    except (OSError, subprocess.TimeoutExpired):
        brought = ["what git did not answer about"]
    return _refused(f"git {verb}", top, brought) if brought else ""


def pull_request_checkout(rules: ModuleType, here: Path) -> str:
    """Why `gh pr checkout`, run from ``here``, may not run: it checks out a branch no review has landed."""
    found = _running(rules, here)
    return _refused("gh pr checkout", found[0], ["what the pull request holds"]) if found else ""


def _running(rules: ModuleType, folder: Path) -> tuple[str, str] | None:
    """(top, common git folder) of the checkout of an enforced repository that holds ``folder``, when its files run as the guards."""
    found = rules.checkout(folder)
    if found is None:
        return None
    top, _, common = found
    enforced = (Path(common).parent / "scripts" / "hooks" / "primary-only").exists()
    return (top, common) if enforced and rules.live(top, common) else None


def _refused(command: str, top: str, brought: list[str]) -> str:
    return (f"`{command}` would put guard code that has not landed into {top}, whose files run as the guards "
            f"({', '.join(dict.fromkeys(brought))}): refused to an agent. Open a pull request and bring it in from origin's development "
            "branch once it has landed, or work in a linked worktree that runs no guard (git worktree add -b <branch> "
            "../poolhouse-<branch>).")
