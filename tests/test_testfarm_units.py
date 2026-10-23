"""Units: the group rule, labels from matches, dependency globs, and what a command-line selector names."""
from __future__ import annotations

import pytest

from poolhouse.testfarm.core import select
from poolhouse.testfarm.model import ConfigError, globs, parse, units_of
from poolhouse.testfarm.model.units import Row

CONFIG = parse(b'''schema = 1
[list]
run = ["sh", "list.sh"]
group = "::"
timeout = 5
[run]
argv = ["sh", "{ids}"]
timeout = 5
[label.slow]
match = ["tests/test_big.py::*"]
[label.quick]
match = ["tests/test_small.py"]
[deps]
globs = { "tests/**" = ["src/**"] }
[set.fast]
not_labels = ["slow"]
[set.slow]
labels = ["slow"]
[set.big]
ids = ["tests/test_big.py::*"]
''')

ROWS = [Row("tests/test_big.py::test_a"), Row("tests/test_big.py::test_b", labels=("heavy",)), Row("tests/test_small.py::test_c"),
        Row("tests/test_small.py::test_d", seconds=1.5), Row("other/test_e.py::test_e", deps=("other/x.py",))]


def test_ids_with_one_group_form_one_unit_and_units_are_sorted_by_name():
    units = units_of(ROWS, CONFIG)
    assert [unit.name for unit in units] == ["other/test_e.py", "tests/test_big.py", "tests/test_small.py"]
    assert units[1].ids == ("tests/test_big.py::test_a", "tests/test_big.py::test_b")


def test_labels_come_from_rows_and_from_matches_and_deps_from_rows_and_globs():
    other, big, small = units_of(ROWS, CONFIG)
    assert big.labels == ("heavy", "slow") and small.labels == () and other.labels == ()
    assert big.deps == ("src/**",) and other.deps == ("other/x.py",) and small.deps == ("src/**",)
    assert units_of([Row("lone")], CONFIG)[0].deps is None


def test_a_unit_takes_seconds_only_when_every_id_gives_them():
    assert units_of([Row("a::1", seconds=1.0), Row("a::2", seconds=2.0)], CONFIG)[0].seconds == 3.0
    assert units_of([Row("a::1", seconds=1.0), Row("a::2")], CONFIG)[0].seconds is None


def test_a_row_group_wins_over_the_separator():
    assert [unit.name for unit in units_of([Row("x::1", group="g"), Row("y::1", group="g")], CONFIG)] == ["g"]


def test_selectors_name_sets_ids_globs_and_units_and_a_set_gives_the_pool_the_others_narrow():
    units = units_of(ROWS, CONFIG)

    def names(*selectors):
        return [unit.name for unit in select.select(units, CONFIG, selectors)]
    assert names() == ["other/test_e.py", "tests/test_big.py", "tests/test_small.py"]
    assert names("@fast") == ["other/test_e.py", "tests/test_small.py"] and names("@slow") == ["tests/test_big.py"]
    assert names("@big") == ["tests/test_big.py"] and names("tests/test_small.py") == ["tests/test_small.py"]
    assert names("tests/test_small.py::test_d") == ["tests/test_small.py"] and names("tests/**") == ["tests/test_big.py", "tests/test_small.py"]
    assert names("@fast", "tests/**") == ["tests/test_small.py"] and names("@fast", "other/**", "tests/test_small.py") == ["other/test_e.py", "tests/test_small.py"]


def test_a_selection_that_matches_nothing_and_a_set_that_does_not_exist_are_errors():
    units = units_of(ROWS, CONFIG)
    with pytest.raises(ConfigError, match=r"matches no unit"):
        select.select(units, CONFIG, ["nothing/**"])
    with pytest.raises(ConfigError, match=r"matches no unit"):
        select.select(units, CONFIG, ["@slow", "other/**"])
    with pytest.raises(ConfigError, match=r"no set named 'gone'; the sets are big, fast, slow"):
        select.select(units, CONFIG, ["@gone"])
    assert select.select([], CONFIG, []) == ()


def test_a_glob_has_star_double_star_and_question_mark_and_nothing_else():
    cases = [("tests/*.py", "tests/a.py", True), ("tests/*.py", "tests/sub/a.py", False), ("tests/**", "tests/sub/a.py", True),
             ("tests/**/a.py", "tests/a.py", True), ("tests/**/a.py", "tests/x/y/a.py", True), ("**/a.py", "a.py", True), ("a?c", "abc", True),
             ("a?c", "a/c", False), ("a?c", "ac", False), ("*", "a/b", False), ("**", "a/b", True), ("a.b", "axb", False), ("", "", True),
             ("tests/test_x.py::t[1]", "tests/test_x.py::t[1]", True), ("a**b", "a/x/b", True), ("***", "x/y", True)]
    for pattern, text, expected in cases:
        assert globs.match(pattern, text) is expected, (pattern, text)
