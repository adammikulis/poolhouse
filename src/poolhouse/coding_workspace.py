"""Parent-owned coding snapshots and isolated per-tool working trees."""
from __future__ import annotations

import os
import shutil
import threading
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path

from poolhouse import coding_workspace_state as state, files, lock, private_path, secretpaths
from poolhouse.activity import source_snapshot
from poolhouse.activity.source_snapshot import Capture, SourceSnapshot


def _free_bytes(directory: Path) -> int:
    return shutil.disk_usage(directory).free


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


def between(before: Capture, after: Capture) -> tuple[Change, ...]:
    """The changes from ``before`` to ``after``: each path whose entry differs, with the digests and modes on both sides."""
    left, right = _entry_map(before), _entry_map(after)
    return tuple(Change(path, left[path].sha256 if path in left else None,
                        right[path].sha256 if path in right else None,
                        left[path].mode if path in left else None,
                        right[path].mode if path in right else None)
                 for path in sorted(left.keys() | right.keys())
                 if left.get(path) != right.get(path))


class Workspace:
    """Keep canonical coding bytes outside every tool's writable roots."""

    def __init__(self, source: Path, *, free: Callable[[Path], int] = _free_bytes):
        self.free = free
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
        self.pinned: Path | None = None
        """The index a stage is read by in place of its own (`pin`)."""
        snapshot = SourceSnapshot(self.source, self.directory, strict=False)
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
            made.alias, made.lock, made.free = directory / "workspace", threading.Lock(), _free_bytes
            made.revision, made.persisted, made.pinned = receipt["revision"], receipt, None
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
        missing = state.paths(saved["manifest"]["missing"])
        snapshot = SourceSnapshot(target, self.directory, known_paths=missing, strict=False)
        try:
            if any(os.path.lexists(snapshot.path(path)) for path in missing):
                raise ValueError("An absent coding snapshot path appeared")
            proof = snapshot.capture(self.directory / f"result-{uuid.uuid4().hex}", excluded=self.excluded)
        finally:
            snapshot.close()
        if state.manifest(proof) != saved["manifest"]:
            raise ValueError("The coding snapshot manifest changed")
        self._index(proof.directory, proof)
        return proof

    def _excluded(self, snapshot: SourceSnapshot) -> tuple[bytes, ...]:
        credentials = (name for name in snapshot.entries if secretpaths.name_hit(os.fsdecode(name)))
        return tuple(sorted({*self.excluded, *credentials, *(os.fsencode(path) for path in state.CONTROL_PREFIXES)}))

    def _capture(self, root: Path, label: str, previous: Capture, index: Path | None = None) -> Capture:
        snapshot = SourceSnapshot(root, self.directory, known_paths=previous.missing, strict=False, index=index)
        try:
            return snapshot.capture(self.directory / f"{label}-{uuid.uuid4().hex}",
                                    excluded=self._excluded(snapshot))
        finally:
            snapshot.close()

    def _index(self, root: Path, capture: Capture, *, intent: bool = False) -> None:
        """Make ``root`` a throwaway repository of ``capture``: a full index for the stage the agent runs `git diff` in, and only the names
        (`git add --intent-to-add`) for a captured directory the parent alone reads the file list of."""
        if not (root / ".git").exists():
            source_snapshot.git_output(["init", "--quiet", str(root)], cwd=self.directory)
        names = self.directory / f"paths-{uuid.uuid4().hex}"
        try:
            names.write_bytes(b"\0".join(entry.path for entry in capture.entries) + b"\0")
            if capture.entries:
                source_snapshot.git_output(["--literal-pathspecs", "add", "--force", *(["--intent-to-add"] if intent else []),
                                            "--pathspec-from-file=" + str(names), "--pathspec-file-nul"], cwd=root)
        finally:
            names.unlink(missing_ok=True)

    def begin(self) -> Stage:
        """Create a unique writable copy and rotate the model's workspace alias."""
        if not self.current():
            raise ValueError("The coding workspace roots changed")
        before = self._capture(self.master, "before", self.last or self.initial)
        self._room(before)
        physical = self.directory / f"tool-{uuid.uuid4().hex}"
        if not files.clone_tree(before.directory, physical):
            shutil.copytree(before.directory, physical, symlinks=True)
        self._index(physical, before)
        temporary = self.directory / f"alias-{uuid.uuid4().hex}"
        temporary.symlink_to(physical)
        files.promote(temporary, self.alias)
        return Stage(physical, self.alias, before, self.revision)

    def finish(self, stage: Stage, guard: Callable[[Capture, int], AbstractContextManager], *, writable: bool) -> tuple[Change, ...]:
        """Publish verified bytes into the parent master under the current policy guard."""
        for prefix in state.CONTROL_PREFIXES:
            if os.path.lexists(stage.physical / prefix):
                raise ValueError("The tool created a trusted control path")
        captured = self._capture(stage.physical, "result", stage.before)
        if captured.excluded != stage.before.excluded:
            raise ValueError("The tool created a protected or credential path")
        if not writable and state.manifest(captured) != state.manifest(stage.before):
            raise ValueError("A read-only coding tool changed its input snapshot")
        changes = between(stage.before, captured)
        current = self._capture(self.master, "current", stage.before)
        if state.manifest(current) != state.manifest(stage.before):
            raise ValueError("The coding workspace changed while the tool was running")
        self._index(captured.directory, captured, intent=True)
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

    def observe(self, stage: Stage, base: Capture) -> tuple[Capture, tuple[bytes, ...]]:
        """The stage as it is now, for a per-launch session that publishes after each turn (docs/design-isolated-launcher.md, W6). A control path or a
        credential name the tool made is not carried and is returned by name, beside the capture, so the rest is published; `finish` is
        unchanged and still refuses the whole result for either. ``base`` is the capture the session last published."""
        present = {os.fsencode(prefix) for prefix in state.CONTROL_PREFIXES if os.path.lexists(stage.physical / prefix)}
        captured = self._capture(stage.physical, "result", base, self.pinned)
        return captured, tuple(sorted(present | {name for name in captured.excluded if name not in base.excluded}))

    def pin(self, stage: Stage) -> None:
        """Keep the index of ``stage`` as it is now, outside every folder a session writes, and read the stage by that copy from here on
        (`observe`): the index of a session that commits changes, and then decides nothing of what is captured."""
        held = self.directory / f"index-{uuid.uuid4().hex}"
        shutil.copyfile(stage.physical / ".git" / "index", held)
        held.chmod(0o600)
        self.pinned = held

    def adopt(self, kept: Path) -> Stage:
        """Take a stage an earlier launch of the same conversation kept (a directory of another private namespace of this account) as the working
        copy, moved and never rebuilt from the master, so what the agent already made is still there (4.4, step 6)."""
        if not self.current() or not kept.name.startswith("tool-") or kept.is_symlink() or not kept.is_dir() or private_path.problem(kept):
            raise ValueError("The kept coding stage is not a private directory of this account")
        physical = self.directory / f"tool-{uuid.uuid4().hex}"
        files.promote_new(kept, physical)
        temporary = self.directory / f"alias-{uuid.uuid4().hex}"
        temporary.symlink_to(physical)
        files.promote(temporary, self.alias)
        return Stage(physical, self.alias, self.last or self.initial, self.revision)

    def _room(self, capture: Capture) -> None:
        """Refuse to stage a project the volume cannot hold, with the figures. The one bound on a project is the free bytes of the stage's volume
        and it counts allocated blocks, so a sparse file stages by what it holds and not by its apparent size."""
        need = sum(os.lstat(capture.directory / os.fsdecode(entry.path)).st_blocks * 512 for entry in capture.entries if entry.mode != b"120000")
        have = self.free(self.directory)
        if need > have:
            raise ValueError(f"the stage needs {need} bytes and the volume of {self.directory} has {have} free: the project is staged where there is room "
                             "for it, or the launch is given a folder on a larger volume")

    def left_out(self) -> tuple[tuple[bytes, str], ...]:
        """The paths the working copy does not carry, each with why: a submodule, a link that leaves the tree, a special file, a hard-linked or
        foreign-owned file. The first line of a session names them (docs/design-isolated-launcher.md, W3)."""
        return (self.last or self.initial).left_out

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
            return self._capture(self.master, "proposal", self.last or self.initial)
