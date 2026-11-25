"""A real project, a real stage and a session over them, for the tests of the isolated launcher's workspace (no model, no board)."""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from poolhouse.activity import source_snapshot as source
from poolhouse.isolation.notices import Notice
from poolhouse.isolation.reports import Reporter
from poolhouse.isolation.workspace import Launch, Session

TEXT = b"x = 1\n"
FILES = {"src/app.py": TEXT, "src/lib.py": b"y = 2\n", "README.md": b"# project\n", "tests/test_app.py": b"def test_app():\n    assert True\n",
         "data/table.json": b'{"rows": []}\n', "scripts/build.sh": b"#!/bin/sh\necho build\n", "Makefile": b"all:\n\tsh scripts/build.sh\n",
         ".gitignore": b"scratch.log\n.venv\nvenv\nnode_modules\n", "CODEOWNERS": b"* @owner\n"}


def git(root: Path, *args: str) -> None:
    subprocess.run([str(source.actual_git()), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=root, check=True,
                   capture_output=True, env={**source.git_environment(), "HOME": str(root)})


@dataclass
class Sink:
    """What the launcher's board session and sentinel would have received."""

    posts: list = field(default_factory=list)
    events: list = field(default_factory=list)
    log: list = field(default_factory=list)

    def post(self, to: str, kind: str, body: str, subject: str = "") -> dict:
        self.posts.append((to, kind, subject, body))
        return {}


def private_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Where a stage is built: a private directory of the test, which a snapshot requires."""
    storage = tmp_path / "snapshot-storage"
    storage.mkdir(mode=0o700)
    monkeypatch.setattr(tempfile, "tempdir", str(storage))


def make_project(tmp_path: Path) -> Path:
    """A git project with ordinary source, a script, a data file, a build file, and one file git does not hold."""
    root = tmp_path / "project"
    for name, data in FILES.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)
    (root / "scripts" / "build.sh").chmod(0o755)
    git(root, "init", "-q")
    git(root, "add", "-A")
    (root / "notes").mkdir()
    (root / "notes" / "todo.md").write_bytes(b"todo\n")
    return root


def holds(root: Path, name: str) -> bool:
    """Whether the project's own git index holds ``name`` (the test of a file git can give back)."""
    return bool(source.git_output(["ls-files", "-z", "--", name], cwd=root))


def copies(session: Session) -> dict[str, bytes]:
    """The versions the session's publications displaced and kept first: the name each is kept under, and its bytes."""
    return {path.name: path.read_bytes() for path in session.restores.directory.iterdir()}


def launch(project: Path, tmp_path: Path, sink: Sink, **options) -> Launch:
    notice = Notice(Reporter(sink.post), "lead", options.pop("cap", 8), lambda name, fields: sink.events.append((name, fields)))
    return Launch(str(project), str(tmp_path / "state"), options.pop("session", "s1"), notice, log=lambda kind, **fields: sink.log.append((kind, fields)), **options)


def begin(project: Path, tmp_path: Path, sink: Sink, **options) -> Session:
    return Session.begin(launch(project, tmp_path, sink, **options))


def write(session: Session, name: str, data: bytes | str, mode: int | None = None) -> Path:
    """The agent writing a file in its stage."""
    path = session.stage.physical / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    if mode is not None:
        path.chmod(mode)
    return path
