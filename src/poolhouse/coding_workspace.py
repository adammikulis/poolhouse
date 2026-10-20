"""Parent-owned coding snapshots and isolated per-tool working trees."""
from __future__ import annotations

import os
import shutil
import threading
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from poolhouse import coding_workspace_state as state, files, lock, private_path, secretpaths
from poolhouse.activity import source_snapshot
from poolhouse.activity.source_snapshot import Capture, SourceSnapshot


@dataclass(frozen=True)
class Stage:
    physical: Path
    alias: Path
    before: Capture
    revision: int


@dataclass(frozen=True)
class Change:
    path: bytes
    before: bytes | None
    after: bytes | None
    before_mode: bytes | None
    after_mode: bytes | None


def _entry_map(capture: Capture) -> dict:
    return {entry.path: entry for entry in capture.entries}


def _changes(before: Capture, after: Capture) -> tuple[Change, ...]:
    left, right = _entry_map(before), _entry_map(after)
    return tuple(Change(path, left[path].sha256 if path in left else None,
                        right[path].sha256 if path in right else None,
                        left[path].mode if path in left else None,
                        right[path].mode if path in right else None)
                 for path in sorted(left.keys() | right.keys())
                 if left.get(path) != right.get(path))


class Workspace:
    """Keep canonical coding bytes outside every tool's writable roots."""

    def __init__(self, source: Path):
        self.source = source_snapshot.validate_storage(source, source_snapshot.protected_roots(dict(os.environ)))
        self.directory = source_snapshot.private_namespace(dict(os.environ), "poolhouse-coding-")
        self.source_identity, self.directory_identity = state.identity(self.source), state.identity(self.directory)
        self.master = self.directory / "master"
        self.alias = self.directory / "workspace"
        self.excluded: tuple[bytes, ...] = ()
        self.revision = 0
        self.lock = threading.Lock()
        self.last: Capture | None = None
        self.changes: tuple[Change, ...] = ()
        self.persisted: dict | None = None
        snapshot = SourceSnapshot(self.source, self.directory)
        try:
            self.initial = snapshot.capture(self.master, excluded=self._excluded(snapshot))
        finally:
            snapshot.close()
        self.excluded = self.initial.excluded
        self._index(self.master, self.initial)

    def current(self) -> bool:
        """Check retained local roots without reading a registry or calling a service."""
        try:
            return (self.source.resolve(strict=True) == self.source and self.directory.resolve(strict=True) == self.directory
                    and self.source.is_dir() and self.directory.is_dir()
                    and not private_path.problem(self.directory) and state.identity(self.source) == self.source_identity
                    and state.identity(self.directory) == self.directory_identity)
        except OSError:
            return False

    def receipt(self) -> dict:
        """Describe parent-owned snapshots for the existing coding conversation receipt."""
        if not self.current():
            raise ValueError("The coding workspace roots changed")
        master = self.last or self.initial
        return {"version": 1, "source": str(self.source), "source_identity": list(self.source_identity),
                "directory": str(self.directory), "directory_identity": list(self.directory_identity),
                "revision": self.revision, "initial": {"name": self.initial.directory.name, "manifest": state.manifest(self.initial)},
                "master": {"name": self.master.name, "manifest": state.manifest(master)}}

    def next_receipt(self, capture: Capture, revision: int) -> dict:
        """Describe a verified publication before entering the graph transaction."""
        if type(revision) is not int or revision != self.revision:
            raise ValueError("The coding session changed while the tool was running")
        return {**self.receipt(), "revision": revision + 1,
                "master": {"name": capture.directory.name, "manifest": state.manifest(capture)}}

    @classmethod
    @contextmanager
    def reopened(cls, source: Path, conversations, expected, *, current):
        """Read the latest existing receipt under the workspace's process lock."""
        if current() is not True:
            raise ValueError("The original coding session is no longer current")
        earlier = conversations.workspace_state(expected)
        directory = state.namespace(source, earlier)
        with lock.only_one(directory / "workspace.lock", timeout=30, announce=lambda _text: None):
            if current() is not True:
                raise ValueError("The original coding session is no longer current")
            receipt = conversations.workspace_state(expected)
            state.namespace(source, receipt)
            if (any(receipt[key] != earlier[key] for key in state.BOUND_FIELDS)
                    or receipt["revision"] < earlier["revision"]):
                raise ValueError("The coding workspace changed while waiting for its lock")
            state.checked(source, receipt)
            made = cls.__new__(cls)
            made.source, made.directory = source.resolve(strict=True), directory
            made.source_identity = tuple(receipt["source_identity"])
            made.directory_identity = tuple(receipt["directory_identity"])
            made.alias, made.lock = directory / "workspace", threading.Lock()
            made.revision, made.persisted = receipt["revision"], receipt
            made.excluded = state.paths(receipt["initial"]["manifest"]["excluded"])
            made.initial = made._restore_capture(receipt["initial"])
            made.last = made._restore_capture(receipt["master"])
            made.master, made.changes = made.last.directory, ()
            if not made.current() or current() is not True:
                raise ValueError("The coding workspace roots changed")
            if conversations.workspace_state(expected) != receipt:
                raise ValueError("The coding workspace changed while capturing its snapshots")
            yield made

    def _restore_capture(self, saved: dict) -> Capture:
        name = saved["name"]
        target = self.directory / name
        if target.resolve(strict=True) != target or private_path.problem(target):
            raise ValueError("The coding snapshot directory changed")
        snapshot = SourceSnapshot(target, self.directory)
        try:
            missing = state.paths(saved["manifest"]["missing"])
            if any(os.path.lexists(snapshot.path(path)) for path in missing):
                raise ValueError("An absent coding snapshot path appeared")
            proof = snapshot.capture(self.directory / f"result-{uuid.uuid4().hex}", excluded=self.excluded)
        finally:
            snapshot.close()
        proof = replace(proof, missing=missing)
        if state.manifest(proof) != saved["manifest"]:
            raise ValueError("The coding snapshot manifest changed")
        self._index(proof.directory, proof)
        return proof

    def _excluded(self, snapshot: SourceSnapshot) -> tuple[bytes, ...]:
        credentials = (name for name in snapshot.entries
                       if secretpaths.path_hit(os.fsdecode(snapshot.root / os.fsdecode(name))))
        return tuple(sorted({*self.excluded, *credentials, *(os.fsencode(path) for path in state.CONTROL_PREFIXES)}))

    def _capture(self, root: Path, label: str) -> Capture:
        snapshot = SourceSnapshot(root, self.directory)
        try:
            return snapshot.capture(self.directory / f"{label}-{uuid.uuid4().hex}",
                                    excluded=self._excluded(snapshot))
        finally:
            snapshot.close()

    def _index(self, root: Path, capture: Capture) -> None:
        if not (root / ".git").exists():
            source_snapshot.git_output(["init", "--quiet", str(root)], cwd=self.directory)
        names = self.directory / f"paths-{uuid.uuid4().hex}"
        try:
            names.write_bytes(b"\0".join(entry.path for entry in capture.entries) + b"\0")
            if capture.entries:
                source_snapshot.git_output(["add", "--pathspec-from-file=" + str(names),
                                            "--pathspec-file-nul"], cwd=root)
        finally:
            names.unlink(missing_ok=True)

    def begin(self) -> Stage:
        """Create a unique writable copy and rotate the model's workspace alias."""
        if not self.current():
            raise ValueError("The coding workspace roots changed")
        before = self._capture(self.master, "before")
        physical = self.directory / f"tool-{uuid.uuid4().hex}"
        shutil.copytree(before.directory, physical)
        self._index(physical, before)
        temporary = self.directory / f"alias-{uuid.uuid4().hex}"
        temporary.symlink_to(physical)
        files.promote(temporary, self.alias)
        return Stage(physical, self.alias, before, self.revision)

    def finish(self, stage: Stage, guard: Callable[[Capture, int], AbstractContextManager], *, writable: bool) -> tuple[Change, ...]:
        """Publish verified bytes into the parent master under the current policy guard."""
        if not writable:
            return ()
        for prefix in state.CONTROL_PREFIXES:
            if os.path.lexists(stage.physical / prefix):
                raise ValueError("The tool created a trusted control path")
        captured = self._capture(stage.physical, "result")
        if captured.excluded != stage.before.excluded:
            raise ValueError("The tool created a protected or credential path")
        changes = _changes(stage.before, captured)
        current = self._capture(self.master, "current")
        if current.tree_hash != stage.before.tree_hash or current.entries != stage.before.entries:
            raise ValueError("The coding workspace changed while the tool was running")
        self._index(captured.directory, captured)
        previous = self.master, self.last, self.changes, self.revision, self.persisted
        publication = self.next_receipt(captured, stage.revision)
        published = False
        committed = False
        try:
            with guard(captured, stage.revision):
                if self.revision != stage.revision or not self.current():
                    raise ValueError("The coding session changed while the tool was running")
                self.master = captured.directory
                self.last, self.changes = captured, changes
                self.revision += 1
                self.persisted = publication
                published = True
            committed = True
        finally:
            if published and not committed:
                if self.master == captured.directory:
                    self.master = previous[0]
                if self.last is captured:
                    self.last = previous[1]
                if self.changes is changes:
                    self.changes = previous[2]
                if self.revision == stage.revision + 1:
                    self.revision = previous[3]
                if self.persisted is publication:
                    self.persisted = previous[4]
        return changes

    def run(self, execute: Callable[[Stage], dict], guard: Callable[[Capture, int], AbstractContextManager], *, writable: bool) -> dict:
        """Run one tool without exposing the parent master to its children."""
        with self.lock:
            stage = self.begin()
            answer = execute(stage)
            if not answer.get("isError"):
                self.finish(stage, guard, writable=writable)
            return answer

    def result(self) -> Capture:
        """Capture the current parent master for the existing task proposal pipeline."""
        with self.lock:
            return self._capture(self.master, "proposal")
