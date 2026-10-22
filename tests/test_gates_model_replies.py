"""What model-replies counts: a reply shaped like a model's, built in the suite instead of served canned and marked."""

from __future__ import annotations

from pathlib import Path

import pytest
from gates import Finding, model_replies

REPO = Path(__file__).resolve().parent.parent
CHOICES, LOGPROBS, DELTA, SCORE = "choi" "ces", "top_" "logprobs", "de" "lta", "relevance_" "score"
"""Spelled in two pieces so this file, which the gate reads, holds none of the shapes it is testing."""


def _tree(tmp_path: Path, source: str, name: str = "tests/test_one.py", known: str = "") -> Path:
    where = tmp_path / name
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(source, encoding="utf-8")
    listing = tmp_path / model_replies.KNOWN_FILE
    listing.parent.mkdir(parents=True, exist_ok=True)
    listing.write_text(known, encoding="utf-8")
    return tmp_path


def _found(tmp_path: Path, source: str, **kw) -> list[str]:
    return [f.detail for f in model_replies.find(_tree(tmp_path, source, **kw))]


@pytest.mark.parametrize("line", [
    f'reply = {{"{CHOICES}": [{{"message": {{"role": "assistant", "content": "hi"}}}}]}}',
    f'frame = {{"{CHOICES}": [{{"index": 0, "{DELTA}": {{"content": "hi"}}}}]}}',
    f'scores = {{"{LOGPROBS}": [{{"token": "A", "logprob": -0.1}}]}}',
    f'row = {{"index": 0, "{SCORE}": 0.5}}',
    f'raw = \'{{"{CHOICES}": [{{"message": {{}}}}]}}\'',
    f'raw = b\'data: {{"{CHOICES}": []}}\'',
])
def test_a_reply_shaped_like_a_models_is_found(tmp_path, line: str) -> None:
    assert _found(tmp_path, line + "\n")


@pytest.mark.parametrize("line", [
    f'ask = {{"question": "which?", "{CHOICES}": ["a", "b"]}}',
    'body = {"messages": [{"role": "user", "content": "hi"}]}',
    'from poolhouse.testing.canned import canned_server',
    'doc = "a chat completion has choices, each with a message"',
])
def test_a_request_or_prose_naming_the_words_is_left_alone(tmp_path, line: str) -> None:
    assert not _found(tmp_path, line + "\n")


def test_a_file_is_allowed_the_replies_its_line_lists_and_no_more(tmp_path) -> None:
    source = f'a = {{"{SCORE}": 1}}\nb = {{"{SCORE}": 2}}\n'
    listed = "tests/test_one.py {}\n"
    assert _found(tmp_path / "exact", source, known=listed.format(2)) == []
    assert _found(tmp_path / "grown", source + f'c = {{"{SCORE}": 3}}\n', known=listed.format(2)) \
        == ['new: "relevance_score" reply']
    assert _found(tmp_path / "short", source, known=listed.format(1)) == ['new: "relevance_score" reply']


def test_a_line_that_lists_more_than_the_file_builds_is_a_finding_to_lower(tmp_path) -> None:
    found = model_replies.find(_tree(tmp_path, "x = 1\n", known="tests/test_one.py 2\n# a note\n"))
    assert [(f.path, f.line) for f in found] == [("tests/test_one.py", 0)]
    assert "lists 2, the file builds 0" in found[0].detail
    gone = model_replies.find(_tree(tmp_path, "x = 1\n", known="tests/test_gone.py 1\n"))
    assert [f.path for f in gone] == ["tests/test_gone.py"]


def test_over_counts_per_file_and_in_order() -> None:
    sites = [Finding("a.py", 3, "x"), Finding("a.py", 9, "y"), Finding("b.py", 1, "z")]
    assert model_replies.over(sites, {"a.py": 1, "b.py": 1}) == [Finding("a.py", 9, "new: y")]
    assert [f.detail for f in model_replies.over(sites, {})] == ["new: x", "new: y", "new: z"]


def test_the_canned_server_is_the_one_place_that_may_build_them() -> None:
    from poolhouse.testing import canned

    shipped = Path(canned.__file__)
    assert not shipped.is_relative_to(REPO / "tests"), "the gate reads the suite; the server is library code"
    assert model_replies.scan(shipped, "src/poolhouse/testing/canned.py"), "and it does build them"


def test_the_suite_holds_only_the_replies_it_lists() -> None:
    assert [(f.path, f.line, f.detail) for f in model_replies.find(REPO)] == []
