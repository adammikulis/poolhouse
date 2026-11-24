"""The handoff file is counted from line 500 and warned about from line 300."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from gates import handoff_size


def _write(root: Path, count: int) -> None:
    (root / "HANDOFF.md").write_text("\n".join(f"- item {n}" for n in range(count)) + "\n", encoding="utf-8")


def test_a_file_at_the_wall_has_no_finding_and_each_line_past_it_is_one(tmp_path):
    _write(tmp_path, 500)
    assert handoff_size.find(tmp_path) == []
    _write(tmp_path, 507)
    found = handoff_size.find(tmp_path)
    assert [f.line for f in found] == list(range(501, 508))


def test_the_note_starts_at_the_warning_line_and_not_before(tmp_path):
    _write(tmp_path, 299)
    assert handoff_size.notes(tmp_path) == []
    _write(tmp_path, 300)
    assert "300 lines" in handoff_size.notes(tmp_path)[0]


def test_a_short_file_of_very_long_lines_is_past_the_word_wall_from_the_line_that_crosses_it(tmp_path):
    per_line = handoff_size.WALL_WORDS // 10 + 1
    (tmp_path / "HANDOFF.md").write_text("\n".join(" ".join(["word"] * per_line) for _ in range(12)) + "\n", encoding="utf-8")
    found = handoff_size.find(tmp_path)
    assert [f.line for f in found] == [10, 11, 12]
    assert "words" in handoff_size.notes(tmp_path)[0]


def test_a_missing_file_has_no_finding_and_no_note(tmp_path):
    assert handoff_size.find(tmp_path) == [] and handoff_size.notes(tmp_path) == []
