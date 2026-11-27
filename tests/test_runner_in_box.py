"""What the test runner needs of a coding box: a `.git` it reads and does not write, and a collection run that ends well (the launch preflight's)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from poolhouse.activity.gate import tree_hash
from tests.isolation_world import git

ROOT = Path(__file__).resolve().parent.parent


def test_a_checkout_whose_object_store_cannot_be_written_is_still_hashed(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    git(project, "init", "-q")
    (project / "a.py").write_text("x = 1\n")
    expected = tree_hash(project)
    (project / "b.py").write_text("y = 2\n")
    (project / "a.py").write_text("x = 3\n")
    store = project / ".git" / "objects"
    folders = [store, *(each for each in store.iterdir() if each.is_dir())]
    for each in folders:
        each.chmod(0o500)
    try:
        changed = tree_hash(project)
    finally:
        for each in folders:
            each.chmod(0o700)
    assert expected and changed and changed != expected and changed == tree_hash(project)


def test_a_collection_run_ends_well_and_records_no_test_as_run():
    kept = {name: value for name, value in os.environ.items() if not name.startswith(("DEV_TEST", "PYTEST"))}
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "test"), "all", "tests/test_node_socket.py", "--collect-only"], cwd=ROOT,
                          env=kept, capture_output=True, text=True, timeout=300, check=False)
    assert done.returncode == 0 and "nothing is recorded as run" in done.stdout, (done.returncode, done.stdout[-1500:], done.stderr[-1500:])
