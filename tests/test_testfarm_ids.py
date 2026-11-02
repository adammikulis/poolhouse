"""The id grammar, the text a command prints, and the exit statuses: pure functions of the model."""
from __future__ import annotations

import json

import pytest

from poolhouse.testfarm.model import Config, ConfigError, ListSpec, exitcodes, ids, units_of
from poolhouse.testfarm.model.units import read_rows

HOSTILE = ["a\u2028--evil", "a\u2029--evil", "a\u0085--evil", "a\U000e0041b", "a\u200bb", "a\u202eb", "a\u2066b", "a\x00b", "a\x07b", "a\x1bb",
           "a\x7fb", "a\ue000b", "a\u0378b", "a\ud800b", "e\u0301", "-x", "--evil"]


@pytest.mark.parametrize("text", HOSTILE)
def test_ids_with_line_separators_tag_characters_prefixes_and_non_nfc_text_are_refused(text):
    assert ids.refusal(text) is not None


def test_a_prefix_the_project_names_is_refused_and_ordinary_ids_are_not():
    assert ids.refusal("@file", ["@", "+"]) and ids.refusal("+nightly", ["@", "+"]) and ids.refusal("@file") is None
    for good in ("tests/test_x.py::test_a[1-2]", "caf\u00e9", "with space", "tests/\u65e5\u672c.sh", "x" * 4096, "a-b"):
        assert ids.refusal(good) is None, good
    assert ids.refusal("") and ids.refusal(5) and ids.refusal(None) and ids.refusal(b"x")


@pytest.mark.parametrize("text", [text for text in HOSTILE if text != "a\ud800b"])
def test_a_list_never_splits_one_id_into_two_on_a_separator_the_id_grammar_refuses(text):
    with pytest.raises(ConfigError, match=r"^LIST line 2: "):
        read_rows(f"tests/a.sh\n{text}\ntests/b.sh\n".encode(), ListSpec())


def test_lines_end_at_a_newline_only_a_carriage_return_before_it_is_dropped_and_no_error_repeats_the_line():
    rows, _ = read_rows(b"one\r\ntwo\n\n  \nthree", ListSpec())
    assert [row.id for row in rows] == ["one", "two", "  ", "three"]
    with pytest.raises(ConfigError) as caught:
        read_rows("secret\u2028token\n".encode(), ListSpec())
    assert "secret" not in str(caught.value)
    with pytest.raises(ConfigError, match=r"repeats the id of line 1"):
        read_rows(b"one\none\n", ListSpec())
    with pytest.raises(ConfigError, match=r"not UTF-8"):
        read_rows(b"a\xff\n", ListSpec())


def test_list_starts_and_ends_filter_lines_and_the_count_is_returned():
    rows, ignored = read_rows(b"== banner ==\ntest tests/a ok\ntest tests/b ok\n3 passed\n", ListSpec(starts="test ", ends=" ok"))
    assert [row.id for row in rows] == ["test tests/a ok", "test tests/b ok"] and ignored == 2
    rows, ignored = read_rows(b"x\ny\n", ListSpec(starts="never"))
    assert rows == [] and ignored == 2


def test_jsonl_rows_carry_labels_deps_group_def_and_seconds_inside_ranges():
    line = json.dumps({"id": "tests/a.py::t", "labels": ["slow"], "deps": ["src/a.py"], "group": "tests/a.py", "def": "h1", "seconds": 1.5})
    (row,), _ = read_rows(line.encode(), ListSpec(format="jsonl"))
    assert (row.labels, row.deps, row.group, row.digest, row.seconds) == (("slow",), ("src/a.py",), "tests/a.py", "h1", 1.5)
    bad = ['{"id": "a", "seconds": NaN}', '{"id": "a", "seconds": -1}', '{"id": "a", "seconds": 1e999}', '{"id": "a", "seconds": true}',
           '{"id": "a", "labels": ["bad word!"]}', '{"id": "a", "deps": ["../x"]}', '{"id": "a", "deps": ["/etc/passwd"]}', '{"id": "a", "deps": "x"}',
           '{"id": "a", "extra": 1}', '{"id": 5}', '{"id": "-a"}', '[1]', 'not json', '{"id": "a", "group": ""}', "[" * 100000]
    for text in bad:
        with pytest.raises(ConfigError, match=r"^LIST line 1: "):
            read_rows(text.encode(), ListSpec(format="jsonl"))


def test_plain_text_loses_escapes_overwrites_and_invisible_characters():
    dirty = "ok\x1b[31mred\x1b[0m \x1b]0;title\x07done\rGONE\nline\u202etwo\u200b\x00\ttab"
    assert ids.plain(dirty, width=80) == "GONE\nlinetwo\ttab"
    assert ids.plain("x" * 100, width=10) == "x" * 9 + "…"


def test_the_status_is_one_when_anything_failed_and_otherwise_the_highest_cause():
    s = exitcodes
    assert s.status_of([]) == 0 and s.status_of([s.OK]) == 0
    assert s.status_of([s.NOT_STARTED, s.FAILED, s.NOT_RUN]) == 1 and s.status_of([s.NOT_RUN]) == 70
    assert s.status_of([s.NOT_RUN, s.NOT_STARTED]) == 75 and s.status_of([s.REFUSED, s.NOT_RUN]) == 70
    assert [s.OK, s.FAILED, s.REFUSED, s.BUSY, s.NOT_RUN, s.NOT_STARTED, s.TIMED_OUT, s.CANCELLED] == [0, 1, 4, 6, 70, 75, 124, 130]


@pytest.mark.parametrize("text", ["safe\nPASS forged\x1b[31m", "a\rforged", "a\u202eb", "e\u0301", "-option", "a\ud800b"])
def test_jsonl_group_names_cannot_bypass_the_id_grammar(text):
    line = json.dumps({"id": "ordinary", "group": text})
    with pytest.raises(ConfigError, match=r"^LIST (line 1|group): "):
        rows, _ = read_rows(line.encode(), ListSpec(format="jsonl"))
        units_of(rows, Config(sha256="review"))
