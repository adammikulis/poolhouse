"""Import facts of source bytes are kept between runs, keyed by the bytes and the analysis code, never by file metadata."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import testreuse_imports as imports
import testreuse_key as keys


def project(root: Path) -> None:
    package = root / "src/poolhouse"
    package.mkdir(parents=True)
    (root / "tests").mkdir()
    (package / "__init__.py").write_text("")
    (package / "helper.py").write_text("import json\nVALUE = 1\n")
    (root / "tests/test_a.py").write_text("from poolhouse import helper\n")


def closures(root: Path, folder: Path, analysis: str = "v1") -> keys.Closures:
    return keys.Closures(root, imports.ImportFacts(folder / "facts.json", analysis))


@pytest.fixture
def parses(monkeypatch):
    seen = []
    original = keys.ast.parse

    def parse(source, *args, **kwargs):
        seen.append(source)
        return original(source, *args, **kwargs)

    monkeypatch.setattr(keys.ast, "parse", parse)
    return seen


def test_a_later_run_parses_no_source_it_has_seen_before(tmp_path, parses):
    project(tmp_path)
    first = closures(tmp_path, tmp_path)
    keys.build_manifest(tmp_path, "tests/test_a.py", {}, first)
    first.save()
    assert parses
    parses.clear()
    second = closures(tmp_path, tmp_path)
    again = keys.build_manifest(tmp_path, "tests/test_a.py", {}, second)
    assert parses == []
    assert "src/poolhouse/helper.py" in again["files"]


def test_changed_bytes_are_parsed_again_whatever_the_file_metadata_says(tmp_path, parses):
    project(tmp_path)
    first = closures(tmp_path, tmp_path)
    keys.build_manifest(tmp_path, "tests/test_a.py", {}, first)
    first.save()
    (tmp_path / "src/poolhouse/helper.py").write_text("__import__(x)\n")
    second = closures(tmp_path, tmp_path)
    assert second.deps("poolhouse.helper")[1] is True
    assert "__import__(x)\n" in parses


def test_facts_made_by_other_analysis_code_are_dropped(tmp_path, parses):
    project(tmp_path)
    first = closures(tmp_path, tmp_path, "v1")
    keys.build_manifest(tmp_path, "tests/test_a.py", {}, first)
    first.save()
    parses.clear()
    keys.build_manifest(tmp_path, "tests/test_a.py", {}, closures(tmp_path, tmp_path, "v2"))
    assert parses


def test_an_unreadable_facts_file_is_ignored(tmp_path, parses):
    project(tmp_path)
    (tmp_path / "facts.json").write_text("{not json")
    keys.build_manifest(tmp_path, "tests/test_a.py", {}, closures(tmp_path, tmp_path))
    assert parses


def test_facts_unused_for_two_weeks_are_dropped_on_save(tmp_path):
    path = tmp_path / "facts.json"
    long_ago = time.time() - imports.KEEP_SECONDS - 10
    path.write_text(json.dumps({"analysis": "v1", "rows": {"stale": [["a"], False, long_ago]}}))
    facts = imports.ImportFacts(path, "v1")
    facts.put("fresh", frozenset({"b"}), True)
    facts.save()
    assert set(json.loads(path.read_text())["rows"]) == {"fresh"}


def test_two_writers_keep_each_others_facts(tmp_path):
    path = tmp_path / "facts.json"
    one, other = imports.ImportFacts(path, "v1"), imports.ImportFacts(path, "v1")
    one.put("a", frozenset({"x"}), False)
    other.put("b", frozenset({"y"}), False)
    one.save()
    other.save()
    assert set(json.loads(path.read_text())["rows"]) == {"a", "b"}


def test_the_analysis_version_follows_the_bytes_of_its_sources(tmp_path):
    source = tmp_path / "a.py"
    source.write_text("one")
    first = imports.version(source)
    source.write_text("two")
    assert imports.version(source) != first
