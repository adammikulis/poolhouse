"""Content-bound import analysis and immutable cached facts."""
from __future__ import annotations

import pytest
import testreuse_key as keys


def test_cached_names_are_fresh_sets():
    source = "import json"
    names, _ = keys.import_names(source, "poolhouse.area", False)
    names.add("injected")
    assert keys.import_names(source, "poolhouse.area", False) == ({"json"}, False)


def test_cache_binds_source_and_relative_context():
    assert keys.import_names("from .helper import x", "poolhouse.first", True)[0] != keys.import_names(
        "from .helper import x", "poolhouse.second", True)[0]
    assert keys.import_names("from .helper import x", "poolhouse.first", True)[0] != keys.import_names(
        "from .helper import x", "poolhouse.first", False)[0]
    assert keys.import_names("import json", "poolhouse.first", True)[0] != keys.import_names(
        "import os", "poolhouse.first", True)[0]


@pytest.mark.parametrize("source,expected", [
    ("from ..helper import value", ({"poolhouse.helper", "poolhouse.helper.value"}, False)),
    ("def f(x: __import__('types')):\n import os\n return lambda: importlib.import_module(x)", ({"types", "os"}, True)),
    ("class C:\n value = [__import__('json') for x in []]", ({"json"}, False)),
    ("custom_import_module('poolhouse.foo'); obj.custom__import__(unknown)", ({"poolhouse.foo"}, True)),
    ("(lambda: import_module)('unused'); (obj.import_module)('json')", ({"unused", "json"}, False)),
    ("not valid python syntax!", (set(), True)),
])
def test_cached_analysis_preserves_all_scopes_and_dynamic_calls(source, expected):
    assert keys.import_names(source, "poolhouse.area.module", False) == expected
    assert keys.import_names(source, "poolhouse.area.module", False) == expected
