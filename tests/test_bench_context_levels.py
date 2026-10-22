"""`--context fit[:MAX]` and the thinking levels of `poolhouse-bench`.

Everything is invented and read at the file level: a fit record written into ``tmp_path``, a
minimal GGUF header beside it, the memory room fixed. No server starts and nothing is
measured.
"""

from __future__ import annotations

import argparse

import pytest
from conftest import write_gguf

from poolhouse.bench import fitcontext, thinking
from poolhouse.bench.serve import _suffix
from poolhouse.serve.estimate import Setup, estimate_meta, read_meta
from poolhouse.serve.fit import Fit
from poolhouse.serve.serving import Config, Serving

GIB = 1024**3
MODEL = "thornfield-8B-Q4_K_M.gguf"
HEADER = {
    "general.architecture": "testarch", "testarch.block_count": 8,
    "testarch.attention.head_count": 32, "testarch.attention.head_count_kv": 4,
    "testarch.attention.key_length": 128, "testarch.attention.value_length": 128,
    "testarch.embedding_length": 4096, "testarch.context_length": 2_000_000,
}
QWEN_TEMPLATE = ("{%- if enable_thinking is undefined or enable_thinking is true %}"
                 "{%- set resolved_reasoning_effort = reasoning_effort|default('xhigh') %}"
                 "{%- endif %}")
PLAIN_TEMPLATE = "{% for m in messages %}{{ m.content }}{% endfor %}"


def model_file(tmp_path, **header):
    return str(write_gguf(tmp_path / MODEL, {**HEADER, **header}))


def record(**over):
    base = {"model": MODEL, "weights": 1_000_000_000, "room": 11_000_000_000,
            "per_token": 10_000, "cache_type": "q8_0"}
    return Fit(**{**base, **over})


def test_fit_takes_the_fit_record(fit_files, tmp_path):
    fit_files([record()], room=11_000_000_000)
    got = fitcontext.resolve(model_file(tmp_path), kv="q8_0")
    assert got.source == "fit record"
    assert got.per_slot == 899_840, "a million tokens fit, a tenth is left over, in steps of 256"
    assert got.said() == "context fit: 899840 (fit record)"


def test_the_margin_is_a_tenth_of_what_fits(fit_files, tmp_path):
    fit_files([record()], room=11_000_000_000)
    got = fitcontext.resolve(model_file(tmp_path), kv="q8_0")
    assert 0.89 * 1_000_000 <= got.per_slot <= 0.9 * 1_000_000


def test_a_record_for_another_cache_type_is_not_used(fit_files, tmp_path):
    fit_files([record(cache_type="f16")], room=11_000_000_000)
    got = fitcontext.resolve(model_file(tmp_path), kv="q8_0")
    assert got.source == "estimated from the header"


def test_fit_falls_back_to_the_header_and_says_so(fit_files, tmp_path):
    room = 1 * GIB
    fit_files([], room=room)
    path = model_file(tmp_path)
    got = fitcontext.resolve(path, kv="q8_0")
    assert got.source == "estimated from the header"
    assert got.said() == f"context fit: {got.per_slot} (estimated from the header)"
    assert got.per_slot > 0 and got.per_slot % 256 == 0
    header, weights = read_meta(path), (tmp_path / MODEL).stat().st_size

    def total(context):
        return estimate_meta(header, weights, Setup(context=context, kv_cache_type="q8_0")
                             ).total_bytes
    assert total(got.per_slot) <= room
    assert total(int(got.per_slot / 0.9) + 1024) > room, "what is left over is a tenth, not more"


def test_fit_stops_at_the_models_own_context(fit_files, tmp_path):
    fit_files([record()], room=11_000_000_000)
    got = fitcontext.resolve(model_file(tmp_path, **{"testarch.context_length": 262_144}),
                             kv="q8_0")
    assert got.per_slot == 262_144


def test_fit_stops_at_the_estimates_native_context_too(fit_files, tmp_path):
    fit_files([], room=500 * GIB)
    got = fitcontext.resolve(model_file(tmp_path, **{"testarch.context_length": 131_072}),
                             kv="q8_0")
    assert got.per_slot == 131_072


def test_fit_with_a_maximum(fit_files, tmp_path):
    fit_files([record()], room=11_000_000_000)
    path = model_file(tmp_path)
    assert fitcontext.resolve(path, kv="q8_0", cap=65_536).per_slot == 65_536
    assert fitcontext.resolve(path, kv="q8_0", cap=10_000_000).per_slot == 899_840


def test_parallel_users_share_what_fits(fit_files, tmp_path):
    fit_files([record()], room=11_000_000_000)
    got = fitcontext.resolve(model_file(tmp_path), kv="q8_0", parallel=2)
    assert got.per_slot == 449_792 and got.total == 2 * 449_792


def test_a_model_that_leaves_no_room_is_refused_in_a_sentence(fit_files, tmp_path):
    fit_files([record(weights=20_000_000_000)], room=11_000_000_000)
    with pytest.raises(fitcontext.DoesNotFit, match="leaves no room"):
        fitcontext.resolve(model_file(tmp_path), kv="q8_0")


@pytest.mark.parametrize("text, fit, cap", [
    ("fit", True, 0), ("fit:65536", True, 65_536), ("fit:64k", True, 65_536),
    ("32768", False, 0)])
def test_context_argument(text, fit, cap):
    got = fitcontext.context_arg(text)
    assert (got.fit, got.cap) == (fit, cap)
    assert int(got) == (0 if fit else int(text))


@pytest.mark.parametrize("text", ["fitt", "fit:", "fit:x", "-5", "lots"])
def test_context_argument_refuses_nonsense(text):
    with pytest.raises(argparse.ArgumentTypeError):
        fitcontext.context_arg(text)


def test_total_for_a_plain_number_is_that_number(tmp_path):
    args = argparse.Namespace(context=fitcontext.context_arg("65536"), parallel=2)
    assert fitcontext.total_for(args, "any", kv="q8_0", default=1) == (65_536, "")


def test_total_for_fit_names_the_context_in_the_label(fit_files, tmp_path, capsys):
    fit_files([record()], room=11_000_000_000)
    args = argparse.Namespace(context=fitcontext.context_arg("fit"), parallel=1)
    total, tag = fitcontext.total_for(args, model_file(tmp_path), kv="q8_0", default=1)
    assert (total, tag) == (899_840, "-ctx878k")
    assert "context fit: 899840 (fit record)" in capsys.readouterr().out


def test_queue_line_with_context_fit_passes_a_dry_run(tmp_path, capsys):
    from poolhouse.bench.queue import run_queue

    queue = tmp_path / "night.queue"
    queue.write_text("sweep --serve thornfield --context fit --kept k.ladybug\n")
    run_queue(queue, dry_run=True)
    out = capsys.readouterr().out
    assert "--context fit" in out and "nothing ran" in out


# ------------------------------------------------------------------ thinking levels

def test_levels_expand_to_budgets():
    got = thinking.parse_levels("off,low,medium,high,unlimited")
    assert [(lv.name, lv.budget) for lv in got] == [
        ("off", 0), ("low", 1024), ("medium", 4096), ("high", 16384), ("unlimited", -1)]


@pytest.mark.parametrize("text", ["", "loud", "off,,low", "off,off"])
def test_levels_refuse_nonsense(text):
    with pytest.raises(argparse.ArgumentTypeError):
        thinking.parse_levels(text)


def test_every_level_is_its_own_label():
    labels = set()
    for level in thinking.parse_levels("off,low,medium,high,unlimited"):
        config = Config(serving=Serving(model="m")).over(reasoning_budget=level.budget)
        labels.add(_suffix(config))
    assert labels == {"-rb0", "-rb1024", "-rb4096", "-rb16384", "-rb-1"}


def test_a_template_with_its_own_effort_names_it(tmp_path):
    path = write_gguf(tmp_path / "q.gguf", {**HEADER, "tokenizer.chat_template": QWEN_TEMPLATE})
    plan = thinking.plan(path, thinking.parse_levels("off,low,high,unlimited"))
    assert plan.note == "effort levels of the chat template"
    by = {lv.name: lv for lv in plan.levels}
    assert by["off"].kwargs == {"enable_thinking": False}
    assert by["low"].kwargs == {"enable_thinking": True, "reasoning_effort": "low"}
    assert by["high"].kwargs == {"enable_thinking": True, "reasoning_effort": "high"}
    assert by["unlimited"].kwargs == {"enable_thinking": True, "reasoning_effort": "xhigh"}
    assert by["low"].budget == 1024, "the budget still caps it"


def test_a_template_with_a_switch_and_no_effort_uses_the_budgets(tmp_path):
    path = write_gguf(tmp_path / "q.gguf", {
        **HEADER, "tokenizer.chat_template": "{% if enable_thinking %}<think>{% endif %}"})
    plan = thinking.plan(path, thinking.parse_levels("off,low"))
    assert plan.note == "token budgets"
    assert [lv.kwargs for lv in plan.levels] == [{"enable_thinking": False}, {}]


def test_a_model_that_cannot_think_runs_once(tmp_path):
    path = write_gguf(tmp_path / "p.gguf", {**HEADER, "tokenizer.chat_template": PLAIN_TEMPLATE})
    plan = thinking.plan(path, thinking.parse_levels("off,low,medium,high,unlimited"))
    assert plan.note == "no thinking"
    assert len(plan.levels) == 1 and plan.levels[0].name == "" and plan.levels[0].budget is None


def test_a_level_is_laid_over_a_config(tmp_path):
    path = write_gguf(tmp_path / "q.gguf", {**HEADER, "tokenizer.chat_template": QWEN_TEMPLATE})
    low = thinking.plan(path, thinking.parse_levels("low")).levels[0]
    config = low.over(Config(serving=Serving(model="m", extra_args=("--no-warmup",))))
    assert config.serving.reasoning_budget == 1024
    assert config.serving.extra_args == (
        "--no-warmup", "--chat-template-kwargs",
        '{"enable_thinking":true,"reasoning_effort":"low"}')


# ------------------------------------------------------------------ through the sweep

def _sweep(monkeypatch, tmp_path, *flags, template=QWEN_TEMPLATE):
    """``sweep`` over one fake model with `bench.served` replaced: what each load was asked
    for, as ``(label, slot context, slots, budget, extra args)``."""
    from poolhouse import bench

    path = write_gguf(tmp_path / MODEL, {**HEADER, "tokenizer.chat_template": template})
    loads = []

    def note_load(run, questions, graph, loading, ways):
        s = run.serving
        loads.append((ways.label, s.slot_context, s.slots, s.reasoning_budget, s.extra_args))
        return []

    monkeypatch.setattr(bench, "served", note_load)
    monkeypatch.setattr(bench, "_kept", lambda kept: [])
    monkeypatch.setattr(bench, "runs", lambda *a, **k: [])
    monkeypatch.setattr(bench, "busy", lambda url: 0)
    monkeypatch.setattr(bench, "prepared", lambda: "")
    graph = tmp_path / "g.json"
    graph.write_text('{"nodes": [], "edges": []}')
    code = bench._main(["sweep", "--serve", str(path), "--plain-only", "--graph", str(graph),
                        "--kept", str(tmp_path / "runs.ladybug"), "--no-prefetch",
                        "--no-smoke", "--no-profile", "--serve-label", "tf",
                        *flags])
    return code, loads


def test_sweep_serves_at_the_context_that_fits(fit_files, monkeypatch, tmp_path, capsys):
    fit_files([record()], room=11_000_000_000)
    code, loads = _sweep(monkeypatch, tmp_path, "--context", "fit:128k")
    assert code == 0 and [(load[0], load[1]) for load in loads] == [
        ("tf-ctx128k", 131_072)]
    assert "context fit: 131072 (fit record)" in capsys.readouterr().out


def test_sweep_runs_each_thinking_level_as_its_own_load(monkeypatch, tmp_path, capsys):
    code, loads = _sweep(monkeypatch, tmp_path, "--thinking-levels", "off,low,unlimited")
    assert code == 0
    assert [(load[0], load[3]) for load in loads] == [
        ("tf-off", 0), ("tf-low", 1024), ("tf-unlimited", -1)]
    assert loads[1][4] == ("--chat-template-kwargs",
                           '{"enable_thinking":true,"reasoning_effort":"low"}')
    assert "effort levels of the chat template" in capsys.readouterr().out


def test_sweep_of_a_model_with_no_thinking_runs_once(monkeypatch, tmp_path, capsys):
    code, loads = _sweep(monkeypatch, tmp_path, "--thinking-levels", "off,low,high",
                         template=PLAIN_TEMPLATE)
    assert code == 0 and [(load[0], load[3]) for load in loads] == [("tf", None)]
    assert "no thinking" in capsys.readouterr().out
