"""The runner builds the node binary only for a selection that can start one, so a Python-only run never asks cargo for anything."""

from __future__ import annotations

from pathlib import Path

import pytest
import testnode
from test_gatescope import load_runner

NODE_KIT = "def runner_binary():\n    return None\n"
PLAIN = "def test_a():\n    assert True\n"


@pytest.fixture
def toy(tmp_path):
    root = tmp_path / "toy"
    for folder in ("src/poolhouse", "tests"):
        (root / folder).mkdir(parents=True)
    (root / "src/poolhouse/__init__.py").write_text("")
    (root / "tests/node_kit.py").write_text(NODE_KIT)
    (root / "tests/helper.py").write_text("import node_kit\n\nVALUE = node_kit\n")
    (root / "tests/test_plain.py").write_text(PLAIN)
    (root / "tests/test_direct.py").write_text("import node_kit\n\n\ndef test_a():\n    assert node_kit\n")
    (root / "tests/test_indirect.py").write_text("from helper import VALUE\n\n\ndef test_a():\n    assert VALUE\n")
    (root / "tests/test_named.py").write_text("import importlib\n\n\ndef test_a():\n    importlib.import_module('node_kit')\n")
    (root / "src/poolhouse/node_binary.py").write_text("NAME = 'node_binary'\n")
    (root / "tests/test_product.py").write_text("from poolhouse import node_binary\n\n\ndef test_a():\n    assert node_binary.NAME\n")
    (root / "tests/test_env.py").write_text("import os\n\n\ndef test_a():\n    os.environ['DEV_TEST_NODE_BINARY']\n")
    return root


def test_a_selection_of_plain_python_files_needs_no_node(toy):
    assert not testnode.needed(toy, ["tests/test_plain.py"])


@pytest.mark.parametrize("name", ["test_direct", "test_indirect", "test_named", "test_env"])
def test_a_selection_that_can_reach_the_node_kit_needs_the_node(toy, name):
    assert testnode.needed(toy, ["tests/test_plain.py", f"tests/{name}.py"])


def test_a_test_of_the_product_module_named_node_binary_needs_no_node(toy):
    assert not testnode.needed(toy, ["tests/test_product.py"])


def test_a_selection_that_names_no_file_needs_the_node(toy):
    assert testnode.needed(toy, [])


def test_the_runner_asks_cargo_for_nothing_when_the_selection_needs_no_node(monkeypatch):
    runner = load_runner()
    monkeypatch.setattr(runner.testnode, "ensure", lambda *a, **k: pytest.fail("asked for the node"))
    monkeypatch.setattr(runner.testnode, "needed", lambda root, files: False)
    assert runner.build_node(["tests/test_plain.py"]) == 0


def test_the_runner_builds_the_node_when_the_selection_needs_it(monkeypatch, tmp_path):
    runner = load_runner()
    built = []
    monkeypatch.setattr(runner.testnode, "ensure", lambda *a, **k: built.append(1) or Path(tmp_path / "node"))
    monkeypatch.setattr(runner.testnode, "needed", lambda root, files: True)
    assert runner.build_node(["tests/test_direct.py"]) == 0 and built
