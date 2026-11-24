"""The `needs` mark skips a test per test, so a missing extra shows in the count of what was left out."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import extras
import pytest

TESTS = Path(__file__).resolve().parent


def item_marked(*modules: str, reason: str = "poolhouse[nothing]"):
    marks = [pytest.mark.needs(*modules, reason=reason).mark]
    return SimpleNamespace(iter_markers=lambda name: marks if name == "needs" else [])


def test_a_package_that_is_not_installed_skips_the_test_and_names_it_and_its_extra():
    with pytest.raises(pytest.skip.Exception, match=r"poolhouse_no_such_package is not installed: poolhouse\[nothing\]"):
        extras.pytest_runtest_setup(item_marked("json", "poolhouse_no_such_package"))


def test_an_installed_package_leaves_the_test_to_run():
    extras.pytest_runtest_setup(item_marked("json"))
    assert extras.absent(("json", "os")) == ""


def test_a_module_that_needs_a_missing_package_still_collects_every_test_and_skips_only_the_marked(tmp_path):
    (tmp_path / "test_sample.py").write_text(
        "import pytest\n"
        "pytest_plugins = ['extras']\n"
        "pytestmark = pytest.mark.needs('poolhouse_no_such_package', reason='poolhouse[nothing]')\n\n"
        "def test_needs_it():\n    raise AssertionError('ran')\n\n"
        "@pytest.mark.parametrize('n', [1, 2])\ndef test_also_needs_it(n):\n    raise AssertionError('ran')\n")
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider", "-o", "addopts=",
                           "--rootdir", str(tmp_path), str(tmp_path)],
                          capture_output=True, text=True, cwd=tmp_path, check=False, timeout=120,
                          env={**os.environ, "PYTHONPATH": str(TESTS)})
    assert "3 skipped" in done.stdout and "passed" not in done.stdout and "failed" not in done.stdout, done.stdout
    assert "poolhouse_no_such_package is not installed: poolhouse[nothing]" in done.stdout
