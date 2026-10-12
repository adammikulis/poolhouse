"""Accuracy per generated token: the rates, the relative table, the frontier and the rank file.

Every run here is hand-built rows or the invented community through a temporary store.
Nothing serves a model or reads the real home.
"""

from __future__ import annotations

import pytest
from conftest import scored_rows

from poolhouse.bench import _parser, derived, invented_digest, pareto, ranking, rates, runs, save
from poolhouse.bench.efficiency import HALF_THE_TOKENS, relative
from poolhouse.bench.frontier import AXES, UNITS
from poolhouse.bench.score import COSTS


def _run(label: str, hits: int, generated: int | None, *, questions: int = 10) -> dict:
    """``questions`` questions, ``hits`` answered in full, each generating ``generated``
    tokens; None leaves the count off the rows, as a server that reports no usage does."""
    return {"label": label, "server": {}, "rows": [
        {"expected": ["a"], "shown": ["a"] if n < hits else [], "seconds": 1.0,
         "processed_tokens": 100, "completion_tokens": generated or 0}
        for n in range(questions)]}


def test_the_rates_over_generated_tokens_are_read_off_the_rows():
    d = derived(_run("half", 5, 100, questions=10))      # F1 0.5, 1000 tokens generated
    assert d["generated_tokens"] == 1000
    assert d["generated_per_question"] == pytest.approx(100.0)
    assert d["right_per_1k_generated"] == pytest.approx(0.5 * 1000 / 1000)
    assert d["generated_per_right"] == pytest.approx(1000 / (0.5 * 10))
    # paid tokens keep their own meaning: processed plus generated
    assert d["paid_tokens"] == 10 * 100 + 1000


def test_a_run_with_no_completion_count_has_no_generated_rates():
    d = derived(_run("silent", 5, None))
    assert not [k for k in d if "generated" in k], "absent, never 0"
    assert "right_per_1k" in d, "the paid-token rates are unchanged"


def test_a_run_that_got_nothing_right_has_no_generated_per_right():
    d = derived(_run("wrong", 0, 50))
    assert d["right_per_1k_generated"] == 0.0 and "generated_per_right" not in d


def _models(**each: tuple[float, float]) -> dict[str, dict[str, float]]:
    return {name: {"right": right, "generated_per_question": gen}
            for name, (right, gen) in each.items()}


def test_same_accuracy_half_the_tokens_fires_inside_the_noise_band_at_half_the_tokens():
    got = relative(_models(big=(0.80, 1000.0), small=(0.77, 500.0)), noise=0.05)
    assert got["small"].verdict == "same accuracy, half the tokens"
    assert got["small"].accuracy_share == pytest.approx(0.77 / 0.80)
    assert got["small"].tokens_share == pytest.approx(0.5)
    assert got["big"].reference and got["big"].verdict == ""


def test_the_verdict_does_not_fire_eight_points_apart_or_at_sixty_percent_of_the_tokens():
    apart = relative(_models(big=(0.80, 1000.0), small=(0.72, 400.0)), noise=0.05)
    assert apart["small"].verdict == ""
    heavy = relative(_models(big=(0.80, 1000.0), small=(0.78, 600.0)), noise=0.05)
    assert heavy["small"].verdict == ""


def test_the_verdict_edges_are_inclusive_and_follow_the_noise_band():
    edge = relative(_models(big=(0.80, 1000.0), small=(0.75, HALF_THE_TOKENS * 1000.0)),
                    noise=0.05)
    assert edge["small"].verdict == "same accuracy, half the tokens"
    wide = relative(_models(big=(0.80, 1000.0), small=(0.72, 400.0)), noise=0.10)
    assert wide["small"].verdict == "same accuracy, half the tokens"


def test_the_reference_is_the_most_accurate_model_and_a_tie_goes_to_fewer_tokens():
    got = relative(_models(a=(0.8, 900.0), b=(0.8, 300.0), c=(0.78, 120.0)), noise=0.05)
    assert got["b"].reference and not got["a"].reference
    assert got["c"].verdict == "same accuracy, half the tokens"
    assert got["a"].verdict == ""


def test_a_model_with_no_generated_count_is_left_out_of_the_table():
    got = relative({**_models(big=(0.8, 1000.0)), "silent": {"right": 0.8}}, noise=0.05)
    assert "silent" not in got
    assert relative({"silent": {"right": 0.8}}, noise=0.05) == {}


def test_efficient_marks_the_frontier_of_accuracy_against_generated_tokens():
    got = relative(_models(big=(0.80, 1000.0), lean=(0.70, 200.0), waste=(0.60, 900.0),
                           mid=(0.75, 600.0)), noise=0.05)
    assert {m for m, r in got.items() if r.efficient} == {"big", "lean", "mid"}


def test_the_frontier_can_be_drawn_against_generated_tokens():
    thrifty, lavish, sharp = (_run("thrifty", 8, 100), _run("lavish", 8, 900),
                              _run("sharp", 9, 400))
    on = [o["label"] for o in pareto([thrifty, lavish, sharp], cost="generated_tokens")]
    assert on == ["thrifty", "sharp"]
    silent = _run("silent", 10, None)
    assert "silent" not in [o["label"] for o in pareto([thrifty, silent],
                                                      cost="generated_tokens")]


def test_the_cost_is_named_wherever_a_cost_is_chosen():
    assert COSTS["generated_tokens"] == "generated_per_question"
    assert "generated_tokens" in AXES and "generated_tokens" in UNITS
    args = _parser().parse_args(["show", "--rates", "--cost", "generated_tokens"])
    assert args.cost == "generated_tokens"


def test_show_rates_has_the_generated_columns_and_a_dash_for_a_run_with_no_count(capsys):
    rates([_run("counted", 5, 100), _run("silent", 5, None)], cost="generated_tokens")
    lines = capsys.readouterr().out.splitlines()
    head = lines[0]
    assert "F1/1k gen" in head and "gen per right" in head
    counted = next(ln for ln in lines if ln.startswith("counted"))
    silent = next(ln for ln in lines if ln.startswith("silent"))
    assert counted.split()[-2:] == ["0.5000", "200"]
    assert silent.split()[-1] == "-" and silent.split()[-2] == "-"
    assert "*" in counted and "*" not in silent


def _kept(store, label, *, model, hits, generated):
    rows = scored_rows(label, questions=30, hits=hits, seconds=60.0,
                       tokens=(500, generated or 0))
    return save(store, rows, server={"graph": invented_digest(), "binary": "", "model": model})


def test_the_rank_file_carries_generated_tokens_and_the_verdict(tmp_path):
    store = tmp_path / "runs.ladybug"
    _kept(store, "big-run", model="big.gguf", hits=20, generated=2000)     # 67%
    _kept(store, "small-run", model="small.gguf", hits=19, generated=900)  # 63%, 45% tokens
    _kept(store, "mute-run", model="mute.gguf", hits=19, generated=None)
    said = ranking(runs(store))
    head = next(ln for ln in said.splitlines() if ln.startswith("| model |"))
    assert "| s/question | gen/q | acc vs best | tok vs best | verdict | load |" in head
    big = next(ln for ln in said.splitlines() if ln.startswith("| `big.gguf`"))
    small = next(ln for ln in said.splitlines() if ln.startswith("| `small.gguf`"))
    mute = next(ln for ln in said.splitlines() if ln.startswith("| `mute.gguf`"))
    assert "| 2000 | 100% | 100% | reference, efficient |" in big
    assert "| 900 | 95% | 45% | same accuracy, half the tokens, efficient |" in small
    assert "| - | - | - | - |" in mute, "no completion count is a dash, never 0"


def test_the_rank_file_without_any_count_still_writes_dashes(tmp_path):
    store = tmp_path / "runs.ladybug"
    _kept(store, "only", model="only.gguf", hits=15, generated=None)
    row = next(ln for ln in ranking(runs(store)).splitlines() if ln.startswith("| `only.gguf`"))
    assert "| - | - | - | - |" in row
