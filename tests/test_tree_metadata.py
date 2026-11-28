"""A tree under test states its own version and entry points, from its own ``pyproject.toml``, whatever the interpreter holds.

Nothing is installed to run the suite, so a copy of Poolhouse elsewhere (a development link to another checkout, an older install) must
never answer for this tree.
"""

import json
import subprocess
import sys
import tomllib
from importlib.metadata import distribution, entry_points, requires
from pathlib import Path

from packaging.version import Version

REPO = Path(__file__).resolve().parent.parent
PROJECT = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
ASK = ("import json\n"
       "from importlib.metadata import distribution, entry_points\n"
       "print(json.dumps([str(distribution('poolhouse').locate_file('')), distribution('poolhouse').version,\n"
       "                  {e.name: e.value for e in entry_points(group='poolhouse.ui_routes')}]))\n")


def declared() -> list:
    return [str(Version(PROJECT["version"])), PROJECT["entry-points"]["poolhouse.ui_routes"]]


def test_writing_the_metadata_during_a_run_does_not_count_as_a_changed_tree(tmp_path):
    """The runner writes it again after a merge changes the project file, between the two looks at the tree."""
    import testreuse_key

    folder = tmp_path / "tests" / "_generated_metadata"
    folder.mkdir(parents=True)
    (tmp_path / "tests" / "kept.py").write_text("")
    before = testreuse_key.snapshot(tmp_path)
    (folder / "METADATA").write_text("Name: poolhouse\n")
    assert testreuse_key.snapshot(tmp_path) == before and "tests/kept.py" in before


def test_this_process_reads_the_trees_own_metadata():
    found = distribution("poolhouse")
    assert Path(str(found.locate_file(""))).resolve().is_relative_to(REPO), "another copy answers for this tree"
    routes = {entry.name: entry.value for entry in entry_points(group="poolhouse.ui_routes")}
    assert [found.version, routes] == declared()
    commands = {entry.name: entry.value for entry in found.entry_points if entry.group == "console_scripts"}
    assert commands == PROJECT["scripts"]
    assert any(line.endswith('extra == "test"') for line in requires("poolhouse"))


def test_a_process_a_test_starts_reads_the_same(tmp_path):
    done = subprocess.run([sys.executable, "-c", ASK], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert not done.returncode, done.stderr
    where, *said = json.loads(done.stdout)
    assert Path(where).resolve().is_relative_to(REPO), where
    assert said == declared()
