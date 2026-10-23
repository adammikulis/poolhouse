"""Check C: every test of the runner builds its project with the generator and names nothing of this repository."""
from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
REASONS = {"test_testfarm_layers.py": "reads the package's own source", "test_testfarm_config.py": "reads the design's key table",
           "test_testfarm_projects.py": "is this check", "test_testfarm_dogfood.py": "loads this repository's own file"}
"""The runner tests that may name this repository, each with why."""
LEGACY = frozenset({"test_testfarm_nodes.py"})
"""The tests of the carriers that step 8 of the design deletes with their code."""
NAMES = re.compile(r"(__file__|\.parents\[|\bREPO\b|\bROOT\b|\"scripts/|'scripts/|\bscripts/test|Path\.cwd\(\)|os\.getcwd\(\)|expanduser|\"~|'~)")


def test_runner_tests_build_their_projects_with_the_generator_and_name_nothing_of_this_repository():
    own = sorted(path for path in HERE.glob("test_testfarm_*.py") if path.name not in REASONS and path.name not in LEGACY)
    assert len(own) >= 4, own
    for path in own:
        assert not (found := NAMES.findall(path.read_text(encoding="utf-8"))), f"{path.name} names {sorted(set(found))}"


def test_the_generator_names_only_invented_things():
    text = (HERE / "testfarm_project.py").read_text(encoding="utf-8")
    assert not NAMES.findall(text) and "widgets" in text


def test_a_test_that_names_the_repository_would_be_found():
    assert NAMES.search("root = Path(__file__).resolve().parents[1]") and NAMES.search("subprocess.run(['scripts/test'])")
