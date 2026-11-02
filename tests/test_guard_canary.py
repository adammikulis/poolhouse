"""A consumer that calls the do loop with no guard argument is protected, and the attacks that
show it are real: with the rails off the same scenarios succeed.

The model is `Obeying`, a scripted worst case, so these measure the rails and exercise the do loop, not a model;
every reply it gives is marked as canned. The same attacks against a served model are in docs/guardrails.md.
"""

from __future__ import annotations

import io

import pytest

from poolhouse import chat, do, guard as rails
from poolhouse.testing import canary
from poolhouse.testing.canary import ATTACKS, BENIGN, Obeying, Run, play
from poolhouse.testing.canned import CANNED_MARKER, CANNED_MODEL_ID

NAMES = [a.name for a in ATTACKS]
ARMLESS = {"unknown-argument"}


@pytest.mark.parametrize("attack", ATTACKS, ids=NAMES)
def test_the_default_path_stops_the_attack(attack):
    assert not attack.hit(play(attack, None)), f"{attack.name} succeeded against the defaults"


@pytest.mark.parametrize("attack", ATTACKS, ids=NAMES)
def test_the_same_attack_lands_when_the_rails_are_off(attack):
    landed = attack.hit(play(attack, rails.off("showing the attack is real")))
    assert landed == (attack.name not in ARMLESS), attack.name


@pytest.mark.parametrize("task", BENIGN, ids=[b.name for b in BENIGN])
def test_ordinary_work_still_completes_under_the_defaults(task):
    assert task.hit(play(task, None))


def test_the_measured_rates_are_what_the_report_quotes():
    on = canary.measure(lambda: None)
    off = canary.measure(lambda: rails.off("measuring"))
    assert (sum(on.values()), len(on)) == (0, 18)
    assert (sum(off.values()), len(off)) == (17, 18)


def test_a_blocked_call_is_told_to_the_model_and_recorded_on_the_outcome():
    run = Run()
    attack = next(a for a in ATTACKS if a.name == "read-ssh-key")
    model = Obeying(attack.steps)
    out = chat.run_task("transcribe", model, tools=canary._tools(run, attack),
                 person=do.Person(io.StringIO(""), io.StringIO()))
    assert run.calls == [] and [v.by for v in out.blocked] == ["tool-policy"]
    told = [m["content"] for m in out.messages if m.get("role") == "tool"]
    assert "blocked by the tool-policy rail" in told[0] and "credential file" in told[0]


def test_the_obeying_model_marks_every_kind_of_reply_as_canned():
    model = Obeying(("a text step", [("serve_down", {"port": 1}), ("serve_down", {"port": 2})],
                     ("serve_down", {"port": 3})))
    replies = [model.chat([]) for _ in range(4)]
    assert [r.content.startswith(CANNED_MARKER) for r in replies] == [True] * 4
    assert [r.content.removeprefix(CANNED_MARKER).strip() for r in replies] == ["a text step", "", "", "done"]
    assert [len(r.tool_calls or []) for r in replies] == [0, 2, 1, 0]
    assert all(r.raw["model"] == CANNED_MODEL_ID and r.raw["x_canned"] == CANNED_MARKER for r in replies)
    assert Obeying(()).model == CANNED_MODEL_ID
