"""Parent-owned coding snapshots and isolated per-tool working trees."""
from __future__ import annotations

import os
import shutil
import threading
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

from poolhouse import files, secretpaths
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

    def __init__(self, source: Path, *, excluded: tuple[bytes, ...] = ()):
        self.source = source_snapshot.validate_storage(source, source_snapshot.protected_roots(dict(os.environ)))
        self.directory = source_snapshot.private_namespace(dict(os.environ), "poolhouse-coding-")
        self.master = self.directory / "master"
        self.alias = self.directory / "workspace"
        self.excluded = excluded
        self.revision = 0
        self.lock = threading.Lock()
        self.last: Capture | None = None
        self.changes: tuple[Change, ...] = ()
        snapshot = SourceSnapshot(self.source, self.directory)
        try:
            self.initial = snapshot.capture(self.master, excluded=self._excluded(snapshot))
        finally:
            snapshot.close()
        self.excluded = self.initial.excluded
        self._index(self.master, self.initial)

    def _excluded(self, snapshot: SourceSnapshot) -> tuple[bytes, ...]:
        credentials = (name for name in snapshot.entries
                       if secretpaths.path_hit(os.fsdecode(snapshot.root / os.fsdecode(name))))
        return tuple(sorted({*self.excluded, *credentials}))

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
        captured = self._capture(stage.physical, "result")
        if captured.excluded != stage.before.excluded:
            raise ValueError("The tool created a protected or credential path")
        changes = _changes(stage.before, captured)
        current = self._capture(self.master, "current")
        if current.tree_hash != stage.before.tree_hash or current.entries != stage.before.entries:
            raise ValueError("The coding workspace changed while the tool was running")
        self._index(captured.directory, captured)
        previous = self.master, self.last, self.changes, self.revision
        published = False
        committed = False
        try:
            with guard(captured, stage.revision):
                if self.revision != stage.revision:
                    raise ValueError("The coding session changed while the tool was running")
                self.master = captured.directory
                self.last, self.changes = captured, changes
                self.revision += 1
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
