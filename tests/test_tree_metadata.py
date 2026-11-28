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
