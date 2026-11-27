"""The graded coding items of ``bench quants tasks``: the two file formats and the confined run that passes or fails one.

Plumbing only: every reply is CANNED, written by this file. No model is loaded.
"""

from __future__ import annotations

import json

import pytest

from poolhouse.bench import codeitems
from poolhouse.testing.canned import CANNED_MARKER

pytest_plugins = ["tests.sandbox_kit"]

HUMANEVAL = {"task_id": "HumanEval/canned", "entry_point": "double", "prompt": 'def double(x):\n    """Twice x."""\n',
             "test": "def check(candidate):\n    assert candidate(2) == 4\n"}
MBPP = {"task_id": 11, "text": "Write triple.", "test_list": ["assert triple(2) == 6"], "test_setup_code": ""}


def reply(code: str) -> str:
    return f"{CANNED_MARKER}\n```python\n{code}\n```"


def test_both_formats_are_read_and_a_repeated_id_is_refused(tmp_path):
    path = tmp_path / "items.jsonl"
    path.write_text(json.dumps(HUMANEVAL) + "\n" + json.dumps(MBPP) + "\n")
    human, mbpp = codeitems.load([path])
    assert human.id == "HumanEval/canned" and mbpp.id == "MBPP/11" and "assert triple(2) == 6" in mbpp.ask
    assert codeitems.code_in("```python\nfirst\n```\ntext\n```\nlast\n```") == "last\n"
    with pytest.raises(ValueError, match="share an id"):
        codeitems.load([path, path])


def test_an_item_passes_only_when_its_own_tests_run_to_the_end(tmp_path, seatbelt):
    path = tmp_path / "items.jsonl"
    path.write_text(json.dumps(HUMANEVAL) + "\n" + json.dumps(MBPP) + "\n")
    human, mbpp = codeitems.load([path])
    assert codeitems.grade(human, reply("def double(x):\n    return 2 * x")) is True
    assert codeitems.grade(human, reply("def double(x):\n    return x")) is False
    assert codeitems.grade(human, reply("import sys\nsys.exit(0)")) is False
    assert codeitems.grade(mbpp, reply("def triple(x):\n    return 3 * x")) is True
