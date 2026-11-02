"""`questions.decide_batch`: many states and questions in one backend call, and the pointer backend's length groups.

Real rules backend, no model and no GPU. The batched pointer pass itself is measured on the real checkpoint by
`examples/rbmk_operator` (its README reports the equality and the timing).
"""

from __future__ import annotations

from poolhouse.decide import router
from poolhouse.decide.pointer import _length_groups
from poolhouse.decide.questions import choice, decide, decide_batch, noul
from poolhouse.decide.rules import Rule, RulesDecider

RULES = RulesDecider([Rule("yes", contains=("fire",)), Rule("red", contains=("alarm",))], default="no")
CFG = router.Config(backend="rules", rules=RULES)


def test_a_batch_gives_the_same_answers_as_asking_each_state_alone():
    rounds = [("there is a fire", {"urgent": noul("Is this urgent?")}),
              ("quiet", {"urgent": noul("Is this urgent?"), "colour": choice("Colour?", ["red", "green"])})]
    batched = decide_batch(rounds, config=CFG)
    for (state, qs), got in zip(rounds, batched, strict=True):
        alone = decide(state, qs, config=CFG)
        assert {n: a.scores for n, a in got.items()} == {n: a.scores for n, a in alone.items()}
    assert batched[0]["urgent"].choice == "yes" and batched[1]["urgent"].choice == "no"


def test_a_batch_that_cannot_be_answered_marks_every_question_with_its_error(tmp_path):
    bad = router.Config(backend="pointer", pointer=str(tmp_path / "missing"))
    got = decide_batch([("a", {"x": noul("Is it?")}), ("b", {"y": noul("Is it?")})], config=bad)
    assert [list(g) for g in got] == [["x"], ["y"]] and all(a.error and a.abstained for g in got for a in g.values())


def test_an_empty_batch_asks_nothing():
    assert decide_batch([], config=CFG) == []
    assert decide_batch([("state", {})], config=CFG) == [{}]


def test_prompts_are_grouped_by_length_so_padding_stays_small():
    lengths = [150, 160, 800, 830, 250, 820]
    groups = _length_groups(lengths)
    assert sorted(i for g in groups for i in g) == list(range(len(lengths)))
    for group in groups:
        assert max(lengths[i] for i in group) <= 1.3 * min(lengths[i] for i in group)
    assert len(groups) == 3
