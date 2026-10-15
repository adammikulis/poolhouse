"""Receipt import analysis shares pure parsing without trusting stale source or another checkout."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import testreuse_key as keys


def project(root):
    package = root / "src/poolhouse"
    package.mkdir(parents=True)
    (root / "tests").mkdir()
    (package / "__init__.py").write_text("")
    (package / "helper.py").write_text("import json\nVALUE = 1\n")
    for name in ("a", "b", "c"):
        (root / f"tests/test_{name}.py").write_text("from poolhouse import helper\n")
    return keys.Closures(root)


def test_successive_manifests_parse_shared_source_once_per_context(tmp_path, monkeypatch):
    closures = project(tmp_path)
    parsed = []
    original = keys.ast.parse

    def parse(source, *args, **kwargs):
        if source == "import json\nVALUE = 1\n":
            parsed.append(source)
        return original(source, *args, **kwargs)

    monkeypatch.setattr(keys.ast, "parse", parse)
    manifests = [keys.build_manifest(tmp_path, f"tests/test_{name}.py", {}, closures) for name in ("a", "b", "c")]
    assert all("src/poolhouse/helper.py" in manifest["files"] for manifest in manifests)
    assert len(parsed) == 2  # module-relative closure and context-free external package analysis


def test_warm_edges_read_fresh_bytes_even_with_identical_stat_cache_key(tmp_path, monkeypatch):
    closures = project(tmp_path)
    source = tmp_path / "src/poolhouse/helper.py"
    source.write_text("import json #\n")
    stamp = source.stat()
    monkeypatch.setattr(keys, "_stamp", lambda path: (str(path), 1, 1, path.stat().st_size, 1))
    assert closures.deps("poolhouse.helper")[1] is False
    source.write_text("__import__(x)\n")
    os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert closures.deps("poolhouse.helper")[1] is True


def test_warm_edges_track_changed_same_size_import(tmp_path):
    closures = project(tmp_path)
    package = tmp_path / "src/poolhouse"
    (package / "first.py").write_text("")
    (package / "other.py").write_text("")
    closures = keys.Closures(tmp_path)
    source = package / "helper.py"
    source.write_text("from . import first\n")
    assert "poolhouse.first" in closures.deps("poolhouse.helper")[0]
    stamp = source.stat()
    source.write_text("from . import other\n")
    os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    names, _ = closures.deps("poolhouse.helper")
    assert "poolhouse.other" in names and "poolhouse.first" not in names


def test_analysis_keeps_relative_module_and_package_context(tmp_path):
    closures = project(tmp_path)
    rel = "src/poolhouse/helper.py"
    (tmp_path / rel).write_text("from . import child\n")
    first, _ = closures.imports(rel, "outer.first", False)
    second, _ = closures.imports(rel, "other.first", False)
    package, _ = closures.imports(rel, "outer.first", True)
    assert "outer.child" in first and "other.child" in second and "outer.first.child" in package
    assert isinstance(first, frozenset)


def test_context_free_manifest_analysis_rechecks_same_size_bytes(tmp_path, monkeypatch):
    closures = project(tmp_path)
    rel = "src/poolhouse/helper.py"
    source = tmp_path / rel
    source.write_text("import json\n")
    monkeypatch.setattr(keys, "_stamp", lambda path: (str(path), 1, 1, 12, 1))
    assert closures.imports(rel) == (frozenset({"json"}), False)
    source.write_text("import math\n")
    assert closures.imports(rel) == (frozenset({"math"}), False)


def test_same_source_in_different_checkouts_resolves_only_own_graph(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    project(left)
    project(right)
    for root in (left, right):
        (root / "src/poolhouse/helper.py").write_text("from . import child\n")
    (left / "src/poolhouse/child.py").write_text("")
    left_closures, right_closures = keys.Closures(left), keys.Closures(right)
    assert "poolhouse.child" in left_closures.deps("poolhouse.helper")[0]
    assert "poolhouse.child" not in right_closures.deps("poolhouse.helper")[0]


@pytest.mark.parametrize("source", ["def broken(:", "__import__(chosen)"])
def test_cached_unanalysable_source_stays_dynamic(tmp_path, source):
    closures = project(tmp_path)
    rel = "src/poolhouse/helper.py"
    (tmp_path / rel).write_text(source)
    assert closures.imports(rel)[1] is True
    assert closures.imports(rel)[1] is True
