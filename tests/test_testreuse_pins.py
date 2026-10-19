"""Recording a passing file's inputs asks the installed distributions once, however many files are recorded."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

PROGRAM = """
import sys
from importlib import metadata
sys.path.insert(0, "scripts")
import testreuse_key as keys

scans = []
real = metadata.packages_distributions
metadata.packages_distributions = lambda: scans.append(1) or real()
first = keys.pins_for({"pytest"})
assert all(keys.pins_for({"pytest"}) == first for _ in range(20))
print(len(scans), first)
"""


def test_the_installed_distributions_are_scanned_once_for_any_number_of_manifests():
    done = subprocess.run([sys.executable, "-c", PROGRAM], cwd=REPO, capture_output=True, text=True, check=False,
                          env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO / "src")})
    assert done.returncode == 0, done.stderr
    scans, pins = done.stdout.split(" ", 1)
    assert int(scans) <= 1 and "pytest==" in pins, done.stdout
