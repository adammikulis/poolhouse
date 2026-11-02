"""The gate runs fresh checks and preserves parallel lanes and failures."""

from __future__ import annotations

import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import gaterun  # noqa: E402

SCAN = ("import sys, pathlib\n"
        "root, counter = sys.argv[1], sys.argv[2]\n"
        "open(counter, 'a').write('x')\n"
        "bad = [p for p in pathlib.Path(root, 'src').rglob('*.py') if 'VIOLATION' in p.read_text()]\n"
        "sys.exit(1 if bad else 0)\n")
MEET = ("import sys, time, pathlib\n"
        "mine, other = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])\n"
        "mine.write_text('here')\n"
        "end = time.monotonic() + float(sys.argv[3])\n"
        "while time.monotonic() < end:\n"
        "    if other.exists():\n"
        "        sys.exit(0)\n"
        "    time.sleep(0.02)\n"
        "sys.exit(1)\n")


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.invalid", *args],
                   cwd=root, check=True, capture_output=True)


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "tree"
    (root / "src").mkdir(parents=True)
    (root / "src" / "mod.py").write_text("VALUE = 1\n")
    git(root, "init", "-q", "-b", "0.3dev")
    git(root, "add", "src/mod.py")
    git(root, "commit", "-q", "-m", "base")
    return root


class Scan:
    """A step that scans the tree for a seeded violation and counts how often it really ran."""

    def __init__(self, root: Path, home: Path) -> None:
        self.root, self.counter = root, home / "ran"
        self.counter.write_text("")
        self.step = gaterun.Step("scan", self.run)

    def run(self) -> int:
        return subprocess.run([sys.executable, "-c", SCAN, str(self.root), str(self.counter)],
                              check=False).returncode

    def ran(self) -> int:
        return len(self.counter.read_text())

    def verdict(self) -> gaterun.Outcome:
        return gaterun.Gate().run([[self.step]])[0]

    def seed(self) -> None:
        (self.root / "src" / "mod.py").write_text("VALUE = 1  # VIOLATION\n")

    def fix(self) -> None:
        (self.root / "src" / "mod.py").write_text("VALUE = 1\n")


def test_each_run_executes_and_reports_seeded_violations(tree, tmp_path):
    scan = Scan(tree, tmp_path)
    assert scan.verdict().status == 0
    assert scan.verdict().status == 0
    scan.seed()
    assert scan.verdict().status == 1
    assert scan.verdict().status == 1
    scan.fix()
    assert scan.verdict().status == 0
    assert scan.ran() == 5


def test_gate_entry_point_ignores_old_passes_and_runs_every_time(tree, tmp_path, monkeypatch):
    from tests.test_activity_gate import script

    runner = script()
    monkeypatch.setattr(runner, "ROOT", tree)
    monkeypatch.setattr(runner, "build_node", lambda: 0)
    counter = tmp_path / "counter"
    old = tmp_path / "test-gate" / "scope" / ("a" * 64)
    old.parent.mkdir(parents=True)
    old.write_text('{"step": "scan"}')
    monkeypatch.setattr(runner, "gate_steps", lambda *_: [("scan", [sys.executable, "-c",
                        "from pathlib import Path; import sys; p=Path(sys.argv[1]); p.write_text(p.read_text()+'x' if p.exists() else 'x')",
                        str(counter)])])
    args = Namespace(full=True, base="HEAD", workers=1, artifact_output=False)
    assert runner.gate(args, []) == 0
    assert runner.gate(args, []) == 0
    assert counter.read_text() == "xx"


def meet(tmp_path: Path, mine: str, other: str, wait: float = 20.0) -> gaterun.Step:
    return gaterun.Step(mine, lambda: subprocess.run(
        [sys.executable, "-c", MEET, str(tmp_path / mine), str(tmp_path / other), str(wait)], check=False).returncode)


def test_independent_lanes_run_at_the_same_time_and_report_in_lane_order(tree, tmp_path):
    held = gaterun.Gate()
    outcomes = held.run([[meet(tmp_path, "a", "b")], [meet(tmp_path, "b", "a")]])
    assert [(o.name, o.status) for o in outcomes] == [("a", 0), ("b", 0)]


def test_a_failing_side_lane_fails_the_gate_as_it_does_run_in_one_lane(tree, tmp_path):
    steps = [gaterun.Step("ok", lambda: 0), gaterun.Step("bad", lambda: 3), gaterun.Step("after", lambda: 0)]
    held = gaterun.Gate()
    side = held.run([[steps[0]], [steps[1], steps[2]]])
    one = held.run([steps])
    assert sorted((o.name, o.status) for o in side) == sorted((o.name, o.status) for o in one)


def test_the_same_two_steps_in_one_lane_cannot_meet_so_the_concurrency_check_can_fail(tree, tmp_path):
    held = gaterun.Gate()
    outcomes = held.run([[meet(tmp_path, "a", "b", 1.0), meet(tmp_path, "b", "a", 1.0)]])
    assert [o.status for o in outcomes] == [1, 0]
