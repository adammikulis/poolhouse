"""Dependency import analysis across Python syntax and relative module contexts."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import testreuse_key as keys


@pytest.mark.parametrize("source,names,dynamic", [
    ("import alpha as renamed\nfrom beta import gamma as delta", {"alpha", "beta", "beta.gamma"}, False),
    ('myimport_module("suffix")\nobj.other__import__("nested")', {"suffix", "nested"}, False),
    ('import_module(value)\n__import__(b"bytes")', set(), True),
    ('import_module(name="keyword")\n__import__()', set(), True),
    ('(lambda: import_module)("lambda_target")', {"lambda_target"}, False),
    ('(obj if flag else import_module)("conditional_target")', {"conditional_target"}, False),
    ('__import__("loader").import_module("target")', {"loader", "target"}, False),
    ('[__import__(name) for name in names if import_module("filter")]', {"filter"}, True),
    ('f"value {__import__(module)} {import_module(\'formatted\')}"', {"formatted"}, True),
    ('"import_module(\'quoted\')"\n# __import__(computed)', set(), False),
    ("def broken(:", set(), True),
])
def test_import_analysis_keeps_call_targets_and_dynamic_refusals(source, names, dynamic):
    assert keys.import_names(source, "package.module", False) == (names, dynamic)


def test_import_analysis_visits_nested_bodies_decorators_defaults_and_annotations():
    source = '''
@decorate(import_module("decorator"))
class Example(metaclass=__import__("metaclass")):
    def method(self, value=__import__("default")) -> import_module("annotation"):
        import body
        return lambda: __import__(computed)
'''
    assert keys.import_names(source, "package.module", False) == (
        {"decorator", "metaclass", "default", "annotation", "body"}, True)


@pytest.mark.parametrize("module,is_package,names", [
    ("outer.inner.module", False, {"outer.helper", "outer.helper.item", "outer.inner", "outer.inner.sibling"}),
    ("outer.inner", True, {"outer.helper", "outer.helper.item", "outer.inner", "outer.inner.sibling"}),
    ("outer.inner", False, {"helper", "helper.item", "outer", "outer.sibling"}),
])
def test_import_analysis_resolves_relative_imports_with_each_context(module, is_package, names):
    assert keys.import_names("from ..helper import item\nfrom . import sibling", module, is_package) == (names, False)


def test_import_analysis_parses_untrusted_text_without_executing_it():
    source = 'raise RuntimeError("untrusted")\nimport actual\n__import__(computed)'
    assert keys.import_names(source, "package.module", False) == ({"actual"}, True)


def test_import_analysis_walks_deep_expression_trees_without_recursion():
    source = "value = " + "+".join("item" for _ in range(1200)) + '\n__import__(computed)'
    assert keys.import_names(source, "package.module", False) == (set(), True)
