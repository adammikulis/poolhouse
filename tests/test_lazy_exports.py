"""The packages whose names load on first use (`poolhouse.serve`, `poolhouse.hub`) still export everything they say they do."""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

from poolhouse import lazy

SRC = Path(__file__).resolve().parent.parent / "src" / "poolhouse"


@pytest.mark.parametrize("package", ["poolhouse.serve", "poolhouse.hub"])
def test_every_name_a_package_lists_resolves_to_what_the_checker_is_told_it_is(package):
    module = importlib.import_module(package)
    source = ast.parse((SRC / package.split(".")[1] / "__init__.py").read_text())
    declared = {(a.asname or a.name): (n.module, a.name) for block in source.body
                if isinstance(block, ast.If) for n in block.body if isinstance(n, ast.ImportFrom) for a in n.names}
    declared.update({(a.asname or a.name): (n.module, a.name) for n in source.body
                     if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("poolhouse.hub.discover")
                     for a in n.names})
    for name in module.__all__:
        assert getattr(module, name) is not None or name in declared
    assert module._HOME.items() <= declared.items(), sorted(set(module._HOME.items()) ^ set(declared.items()))[:5]


def test_an_unknown_name_is_an_attribute_error_and_the_name_is_bound_after_its_first_use():
    namespace: dict = {}
    getter, listing = lazy.exports("pkg", namespace, {"sep": ("os.path", "sep")})
    with pytest.raises(AttributeError, match="no attribute 'nope'"):
        getter("nope")
    assert getter("sep") and namespace["sep"] and "sep" in listing()


def test_importing_one_submodule_of_serve_does_not_import_the_manager():
    code = "import sys, poolhouse.serve.backend; print('poolhouse.serve.manager' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip() == "False"
