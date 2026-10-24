"""Decide whether automatic refresh may omit coordination-document changes.

This is a notification policy, never an assertion about artifact or build-input equivalence.
"""

from __future__ import annotations

from pathlib import Path

from poolhouse import runtime_git

DOCUMENTS = frozenset({"docs/native-plan-macos.md", "docs/native-plan-windows-linux.md", "docs/node-contract.md"})


def _documentation(path: str) -> bool:
    return path in DOCUMENTS or (path.startswith("docs/notes/") and path.endswith(".md"))


def _tree(checkout: Path, commit: str) -> list[tuple[str, str, str]]:
    if not runtime_git.COMMIT.fullmatch(commit) or runtime_git.git(checkout, "cat-file", "-t", commit) != "commit":
        raise ValueError("documentation comparison requires a full source commit")
    records = runtime_git.git(checkout, "ls-tree", "-r", "-z", "--full-tree", commit).split("\0")
    found, paths = [], set()
    for record in records:
        if not record:
            continue
        header, path = record.split("\t", 1)
        mode, kind, identifier = header.split(" ")
        if (mode not in {"100644", "100755"} or kind != "blob" or not runtime_git.COMMIT.fullmatch(identifier)
                or path in paths or path.startswith("/") or any(part in ("", ".", "..") for part in path.split("/"))):
            raise ValueError("runtime source has an unsupported tree record")
        paths.add(path)
        found.append((record, path, mode))
    return found


def documentation_only(checkout: Path, previous: str, wanted: str) -> bool:
    """Whether only regular coordination Markdown changed since the actual selected build."""
    try:
        if (not runtime_git.COMMIT.fullmatch(previous) or not runtime_git.COMMIT.fullmatch(wanted)
                or runtime_git.git(checkout, "merge-base", previous, wanted) != previous):
            return False
        before, after = _tree(checkout, previous), _tree(checkout, wanted)
        def retained(records):
            return [record for record, path, mode in records if mode != "100644" or not _documentation(path)]
        return before != after and retained(before) == retained(after)
    except (runtime_git.DeployError, OSError, ValueError, TypeError, AttributeError):
        return False
