"""Session cards: their place comes from the backlog, each is checked against the tree, sized from it, briefed with the project's rules and split."""

from __future__ import annotations

from pathlib import Path

import pytest
from cards_kit import card, project

from poolhouse import cards

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def root(tmp_path):
    return project(tmp_path / "project")


def wrong(root: Path, ident: str) -> str:
    return " | ".join(cards.problems(root).get(ident, []))


def test_a_cards_place_is_its_rows_tier_and_step_and_a_paused_or_later_tier_is_paused(root):
    card(root, "later-step", step=2)
    card(root, "paused-card", row="A paused row", write='["left.txt"]', check="test ! -e left.txt")
    card(root, "first-step", step=1)
    assert [each.id for each in cards.ordered(root)] == ["first-step", "later-step", "paused-card"]
    found = {row.item: row.paused for row in cards.backlog(root)}
    assert found == {"Make the junk go away, one step at a time": False, "A paused row about leftovers": True}
    assert cards.rows("## 6. Later\n\n| Item | Where |\n|---|---|\n| Someday | x |\n")[0].paused
    assert cards.problems(root) == {}


def test_every_card_of_this_repository_names_one_row_of_its_backlog():
    found, broken = cards.load(REPO)
    rows = cards.backlog(REPO)
    assert broken == {}
    assert [each.id for each in found if cards.row_of(each, rows) is None] == []


@pytest.mark.parametrize(("fields", "said"), [
    ({"row": "No such row"}, "begins no one row"),
    ({"write": '["src/poolhouse/guard/secrets.py"]', "creates": '["src/poolhouse/guard/secrets.py"]'}, "not for the local model"),
    ({"write": '["AGENTS.md"]'}, "not for the local model"),
    ({"write": '["missing.py"]'}, "does not exist"),
    ({"check": "test ! -e gone.txt"}, "already holds"),
    ({"check": "pytest tests/test_x.py"}, "a check is"),
    ({"check": "scripts/test all tests/test_x.py"}, "does not exist"),
    ({"after": '["no-such-card"]'}, "no card"),
    ({"read": '["junk.txt:5-9"]'}, "lines are first-last"),
])
def test_a_card_that_does_not_hold_against_the_tree_is_refused_with_why(root, fields, said):
    card(root, "bad-card", **fields)
    assert said in wrong(root, "bad-card")


@pytest.mark.parametrize(("text", "listed", "said"), [
    ("def test_one(workspace_node): pass\npytest_plugins = ['node_kit']\n", "", "node binary"),
    ("def test_one(): pass\n", "test_x.py\n", "a sandbox of its own"),
    ("def test_one(): pass\n", "", "is not defined"),
])
def test_a_check_that_cannot_run_in_the_delegates_sandbox_is_refused(root, text, listed, said):
    (root / "tests").mkdir()
    (root / "tests" / "test_x.py").write_text(text, encoding="utf-8")
    (root / "tests" / "seatbelt-modules.txt").write_text(listed, encoding="utf-8")
    selector = "tests/test_x.py::test_two" if said == "is not defined" else "tests/test_x.py"
    card(root, "bad-card", check=f"scripts/test all {selector}")
    assert said in wrong(root, "bad-card")


def test_a_card_is_sized_from_what_it_reads_and_one_past_the_cap_has_to_be_split(root):
    card(root, "small-card")
    small = cards.estimate(root, cards.load(root)[0][0])
    (root / "junk.txt").write_text("x" * 400_000, encoding="utf-8")
    large = cards.estimate(root, cards.load(root)[0][0])
    assert small.total < cards.CAP < large.total and large.reading > small.reading
    assert "too large for one session" in wrong(root, "small-card")
    card(root, "small-card", read='["junk.txt:1-1"]')
    assert cards.CAP * 2 <= cards.COMPACTS_AT


def test_a_card_that_waits_for_itself_is_refused(root):
    card(root, "one-card", after='["two-card"]')
    card(root, "two-card", after='["one-card"]')
    assert "leads back" in wrong(root, "one-card")


def test_a_brief_says_how_to_reach_the_lead_quotes_the_rules_as_they_are_now_and_removes_what_it_lands(root):
    card(root, "junk-card", closes=True)
    card(root, "next-card", row="A paused row", after='["junk-card"]', write='["left.txt"]', check="test ! -e left.txt")
    found = next(each for each in cards.load(root)[0] if each.id == "junk-card")
    said = cards.brief(root, found, "lead-main", ["wrong: the file was kept"])
    for part in ("Your lead is lead-main", "lead_ask(question)", "lead_note(text)", "lead_remember(fact)", "test ! -e junk.txt",
                 "- **Fail first.** (as this project says it).", "fail-first: <test id>", "pull request work\nare lead-main's",
                 "wrong: the file was kept", "delete docs/cards/junk-card.toml", "docs/cards/next-card.toml",
                 "delete the row of HANDOFF.md that begins \"Make the junk go away\""):
        assert part in said, part
    (root / "AGENTS.md").write_text("- Another rule altogether.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="has no rule that begins"):
        cards.brief(root, found, "lead-main")
    assert "has no rule that begins" in wrong(root, "rules")


def test_splitting_a_card_makes_one_for_each_test_selector_in_a_chain_and_moves_what_waited(root):
    (root / "tests").mkdir()
    (root / "tests" / "test_x.py").write_text("def test_one(): pass\ndef test_two(): pass\n", encoding="utf-8")
    card(root, "big-card", check="scripts/test all tests/test_x.py::test_one tests/test_x.py::test_two && test ! -e junk.txt", closes=True)
    card(root, "waiting-card", after='["big-card"]')
    assert cards.split(root, "big-card") == ["big-card-1", "big-card-2"]
    made = {each.id: each for each in cards.load(root)[0]}
    assert sorted(made) == ["big-card-1", "big-card-2", "waiting-card"]
    assert made["big-card-1"].check == "scripts/test all tests/test_x.py::test_one" and not made["big-card-1"].closes
    assert made["big-card-2"].after == ("big-card-1",) and made["big-card-2"].check.endswith("test_two && test ! -e junk.txt")
    assert made["waiting-card"].after == ("big-card-2",) and cards.problems(root) == {}
    with pytest.raises(ValueError, match="by hand"):
        cards.split(root, "waiting-card")
