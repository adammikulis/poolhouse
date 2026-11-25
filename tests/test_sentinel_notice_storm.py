"""Many processes scanning one machine's pins never put a dialog on the screen, changed files or gone ones: a hold is handled by the scan
itself and nothing is asked. The desktop tools are shims that record when they start, so a dialog shows as a record."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from poolhouse import home, sentinel

SRC = str(Path(__file__).resolve().parents[1] / "src")
CHILD = (
    "from poolhouse import sentinel\n"
    "node = sentinel.default()\n"
    "for _ in range(SCANS):\n"
    "    node.scan(deep=True)\n")

@pytest.fixture
def desk(tmp_path):
    """Shims first on PATH that log ``start`` and ``end`` around a one second hold and answer
    with the first button; returns a function giving the child environment and the log."""
    if os.name == "nt":
        pytest.skip("the desktop shims are POSIX scripts")
    shims = tmp_path / "desk-shims"
    shims.mkdir()
    log = tmp_path / "desk.log"
    body = ('#!/bin/sh\necho "start $$ $*" >> "$DESK_LOG"\nsleep "${DESK_HOLD:-1}"\n'
            'echo "end $$" >> "$DESK_LOG"\n%s\n')
    for name, answer in (("osascript", "echo 'button returned:Later, gave up:false'"),
                         ("notify-send", "echo 0")):
        (shims / name).write_text(body % answer)
        (shims / name).chmod(0o755)
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE")}
    env.update(PATH=f"{shims}{os.pathsep}{env['PATH']}", DESK_LOG=str(log), POOLHOUSE_NOTIFY="system",
               PYTHONPATH=os.pathsep.join([SRC, str(Path(__file__).parent)]),
               POOLHOUSE_HOME=os.environ["POOLHOUSE_HOME"],
               PYTHON_KEYRING_BACKEND="onboard_support.FileKeyring",
               POOLHOUSE_TEST_KEYRING=str(tmp_path / "keyring.json"))
    return env, log


def lines(log: Path, word: str) -> list[str]:
    return [x for x in (log.read_text().splitlines() if log.exists() else [])
            if x.startswith(word)]


def scan_in_processes(env, processes: int, scans: int) -> None:
    code = CHILD.replace("SCANS", str(scans))
    running = [subprocess.Popen([sys.executable, "-c", code], env=env) for _ in range(processes)]
    assert [p.wait(timeout=120) for p in running] == [0] * processes


def pinned(count: int, kind: str = "model") -> list[Path]:
    node = sentinel.default()
    folder = home.home() / "runs" / "gemma-v1"
    folder.mkdir(parents=True)
    files = []
    for n in range(count):
        path = folder / f"part{n}.safetensors"
        path.write_bytes(b"weights %d" % n)
        node.manifest.pin(path, kind, source="trained:test")
        files.append(path)
    return files


def test_pinned_files_that_are_gone_raise_no_dialog_and_no_quarantine(desk):
    env, log = desk
    for path in pinned(4):
        path.unlink()
    scan_in_processes(env, processes=4, scans=3)
    node = sentinel.default()
    assert lines(log, "start") == []
    assert node.store.records() == []
    assert not [e for e in node.bus.log.read() if e.kind == "quarantine.move_refused"]
    assert node.manifest.pins() == {}


def test_four_changed_files_scanned_by_many_processes_raise_no_dialog_and_all_four_are_held(desk):
    env, log = desk
    for path in pinned(4):
        path.write_bytes(b"tampered")
    scan_in_processes(env, processes=6, scans=3)
    assert lines(log, "start") == [], "no process showed a dialog"
    assert len(sentinel.default().store.records(state=sentinel.State.QUARANTINED)) == 4


REVIEW = "import sys\nfrom poolhouse.sentinel.cli import command\nsys.exit(command(['review']))\n"


def review_without_a_terminal(env, **extra) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", REVIEW], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, env={**env, **extra}, timeout=120,
                          check=False)


@pytest.mark.parametrize("extra", [{}, {"CLAUDECODE": "1"}])
def test_review_with_no_terminal_prints_the_list_and_raises_no_dialog_terminal_or_editor(desk, extra):
    env, log = desk
    held = sentinel.default().store.quarantine(("member", "10.0.0.7"), "member.forged_traffic: x", {})
    done = review_without_a_terminal(env, **extra)
    assert done.returncode == 0 and "member 10.0.0.7" in done.stdout
    assert lines(log, "start") == []
    assert not log.exists() or not any(word in log.read_text() for word in ("Terminal", ".command", "TextEdit"))
    assert sentinel.default().store.get(held.id).state == sentinel.State.QUARANTINED
