"""Runner entries as task evidence: the project scope of a store and the verified facts of an entry."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from poolhouse import home
from poolhouse.activity import reuse
from poolhouse.board.tasks import Tasks
from poolhouse.memory import project as repository
from poolhouse.net import git
from poolhouse.workspace import project

__all__ = ["STORE_BASE", "checkpoint", "evidence", "scope", "submit", "task_checkout", "task_scope", "verified"]

STORE_BASE = home.user_home() / ".cache" / "test-reuse"


def scope(root: Path) -> str:
    """A short stable name for the project that ``root`` belongs to, shared by every worktree of one
    repository (by origin url, else by the common git directory); a checkout that is no repository
    is its own scope, keyed by its resolved path."""
    found = project.describe(start=root)
    shared = repository.common_git_dir(root) if repository.at(root).ident.startswith("path:") else None
    if shared is not None:
        return "repo-" + hashlib.sha256(str(shared).encode()).hexdigest()[:12]
    if found.get("key"):
        return hashlib.sha256(found["key"].encode()).hexdigest()[:16]
    return "local-" + hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]


def task_checkout(held: dict[str, Any]) -> Path:
    """The checkout the task board assigned to the task whose execution scope is ``held``."""
    if not held:
        raise ValueError("the task has no assigned checkout, so its project is unknown")
    return Path(held.get("project") or held["source_project"])


def task_scope(held: dict[str, Any]) -> str:
    """The project scope of the checkout the task board assigned to the task whose execution scope is ``held``."""
    return scope(task_checkout(held))


def verified(entry_id: str, where: str, base: Path | None = None, commit: str = "",
             repo: Path | None = None) -> dict[str, Any]:
    """The facts of runner entry ``entry_id`` in project scope ``where``; ValueError unless it verifies,
    passed, ran on ``commit`` in a clean checkout, and holds the tree ``commit`` has in ``repo``."""
    found = reuse.find(entry_id, where, base or STORE_BASE)
    label = f"runner entry {entry_id[:24]!r}"
    if found is None:
        raise ValueError(f"no {label} verifies in this project's store")
    if found["outcome"] != "pass":
        raise ValueError(f"{label} did not pass")
    if not commit:
        raise ValueError(f"{label} is evidence for a named commit; give the commit it ran on")
    if found["commit"] != commit:
        raise ValueError(f"{label} ran on commit {found['commit'][:12] or 'unknown'}, not {commit[:12]}")
    if not found["clean"]:
        raise ValueError(f"{label} ran with uncommitted changes, so it is not evidence for a commit")
    try:
        tree = git.run(["rev-parse", f"{commit}^{{tree}}"], cwd=repo).stdout.strip() if repo else ""
    except git.GitFailed as error:
        raise ValueError(f"{label}: the commit's tree cannot be read ({error})") from error
    if tree != found["tree"]:
        raise ValueError(f"{label} ran on tree {found['tree'][:12]}, not the tree of commit {commit[:12]}")
    return {"id": entry_id, "file": found["file"], "key": found["lookup"], "tree": found["tree"],
            "commit": found["commit"], "outcome": found["outcome"], "junit_sha256": found["junit_sha256"],
            "counts": found["counts"], "agent": found["agent"].get("id", ""), "created_ms": int(found["created"] * 1000),
            "covers": f"{found['file']} only"}


def evidence(entry_ids: object, held: dict[str, Any], commit: str = "") -> list[dict[str, Any]]:
    """The verified facts of the runner entries a worker cites for a task whose execution scope is ``held``, from its project's store."""
    if not isinstance(entry_ids, list) or len(entry_ids) > 16 or not all(isinstance(i, str) for i in entry_ids):
        raise ValueError("test entries are a list of at most 16 runner entry ids")
    if not entry_ids:
        return []
    checkout = task_checkout(held)
    return [verified(entry_id, scope(checkout), None, commit, checkout) for entry_id in entry_ids]


def checkpoint(tasks: Tasks, ident: str, value: dict[str, Any]) -> dict[str, Any]:
    """Save a checkpoint that may cite one runner entry as ``test_entry``: the entry is verified before anything is written and
    its facts are kept with the task as evidence of the checkpoint."""
    if type(value) is not dict:
        raise ValueError("unsupported checkpoint fields")
    fields = {key: item for key, item in value.items() if key != "test_entry"}
    facts = evidence([value["test_entry"]] if "test_entry" in value else [], tasks.get(ident).get("scope") or {}, str(fields.get("commit", "")))
    saved = tasks.checkpoint(ident, fields)
    if facts:
        tasks.record(ident, "evidence", f"checkpoint:{saved['id']}", {"entries": facts, "commit": fields["commit"]})
    return {**saved, **({"test_evidence": facts} if facts else {})}


def submit(tasks: Tasks, ident: str, value: dict[str, Any]) -> dict[str, Any]:
    """Submit a proposal that may cite runner entries as ``test_entries``: each is verified against the commit the proposal names
    before anything is written, and their facts are kept with the task as evidence of the proposal."""
    if type(value) is not dict:
        raise ValueError("a proposal is an object")
    fields = {key: item for key, item in value.items() if key != "test_entries"}
    provenance = fields.get("provenance")
    commit = str(provenance.get("commit", "")) if isinstance(provenance, dict) else ""
    facts = evidence(value.get("test_entries", []), tasks.get(ident).get("scope") or {}, commit)
    saved = tasks.submit(ident, fields)
    if facts:
        tasks.record(ident, "evidence", f"proposal:{saved['id']}", {"entries": facts, "commit": commit})
    return {**saved, **({"test_evidence": facts} if facts else {})}
