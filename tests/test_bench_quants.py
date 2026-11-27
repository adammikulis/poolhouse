"""``bench quants``: the statistics, the logits files and the three parts that keep and report runs.

Plumbing only: every reply, log probability and speed cell here is CANNED, written by this file. No model is loaded
and none of these numbers says anything about a model.
"""

from __future__ import annotations

import contextlib
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse import bench, sandbox
from poolhouse.bench import intervals, logits, quants, quiet
from poolhouse.client.chat import Reply
from poolhouse.serve import llamacpp_compile
from poolhouse.testing.canned import CANNED_MARKER

VOCAB, CONTEXT, CHUNKS = 8, 16, 4
SCORED = CONTEXT - 1 - CONTEXT // 2


def canned_logits(path: Path, noise: float, *, seed: int = 1) -> None:
    """PLUMBING ONLY: a file in llama-perplexity's format holding invented numbers, ``noise`` away from noise 0."""
    np = pytest.importorskip("numpy")
    base, extra = np.random.default_rng(seed), np.random.default_rng(seed + 1)
    tokens = [int(t) for t in base.integers(0, VOCAB, CONTEXT * CHUNKS)]
    with path.open("wb") as out:
        out.write(b"_logits_" + struct.pack("<Iii", CONTEXT, VOCAB, CHUNKS) + struct.pack(f"<{len(tokens)}i", *tokens))
        for _ in range(SCORED * CHUNKS):
            raw = base.normal(0, 2, VOCAB) + noise * extra.normal(0, 1, VOCAB)
            logp = raw - np.log(np.exp(raw).sum())
            low = max(logp.min(), logp.max() - 16)
            scale = (logp.max() - low) / 65535
            words = np.clip(np.rint((logp - low) / scale), 0, 65535).astype("<u2")
            out.write(struct.pack("<ff", scale, low) + words.tobytes())


def args_for(tmp_path: Path, action: str, *more: str):
    models = []
    for name in ("REF", "IQ", "K"):
        (tmp_path / f"{name}.gguf").write_bytes(b"canned " + name.encode())
        models += ["--model", f"{name}={tmp_path / name}.gguf"]
    return bench._parser().parse_args(["quants", action, *models, "--reference", "REF", "--versus", "IQ,K", "--set",
                                       "canned", "--kept", str(tmp_path / "runs.ladybug"), *more])


def quiet_machine(monkeypatch, found=lambda: quiet.Quiet()):
    monkeypatch.setattr(quants.quiet, "look", found)


def test_a_difference_interval_leaves_out_zero_only_where_the_items_differ():
    assert intervals.difference([1.0, 0.0] * 20, [1.0, 0.0] * 20) == (0.0, 0.0, 0.0)
    mean, low, high = intervals.difference([1.0] * 30 + [0.0] * 10, [0.0] * 30 + [0.0] * 10)
    assert mean == 0.75 and 0 < low < mean < high
    mean, low, high = intervals.t_interval([10.0, 11.0, 12.0])
    assert mean == 11.0 and low == pytest.approx(11.0 - 4.303 / 3 ** 0.5) and high == pytest.approx(22.0 - low)
    assert intervals.difference([1.0], [0.0]) is None and intervals.t_interval([1.0]) is None
    with pytest.raises(ValueError, match="same items"):
        intervals.difference([1.0, 2.0], [1.0])


def test_a_difference_inside_the_noise_floor_is_not_distinguishable():
    words = ("points", "passes more than", "passes fewer than")
    inside = quants._verdict("IQ", "K", (-1.5, -2.0, -1.0), words, floor=2.2)
    assert inside.startswith("IQ and K are not distinguishable here") and "noise floor of 2.2" in inside
    outside = quants._verdict("IQ", "K", (-3.0, -4.0, -2.0), words, floor=2.2)
    assert outside.startswith("IQ passes fewer than K") and "noise floor 2.2" in outside
    assert "no noise floor measured" in quants._verdict("IQ", "K", (-3.0, -4.0, -2.0), words)


def test_near_ties_are_counted_with_their_gap(tmp_path):
    pytest.importorskip("numpy")
    canned_logits(tmp_path / "a", 0.0)
    canned_logits(tmp_path / "b", 1.0)
    tokens = logits.compare(tmp_path / "a", tmp_path / "b")
    one = logits.blocks(tokens)[0]
    apart = [t for t in tokens if t["chunk"] == 0 and not t["agree"]]
    assert one["differ"] == len(apart) > 0 and all(t["margin"] >= 0 for t in tokens)
    assert one["differ_margin"] == pytest.approx(sum(t["margin"] for t in apart))
    assert logits.argv(Path("t"), Path("m"), Path("c"), Path("o"), logits.Scoring(1024, 8, ubatch=1))[10] == "1"


def test_the_items_needed_to_separate_a_few_points():
    assert quants.items_needed(3, 0.10) == 873
    assert quants.items_needed(5, 0.10) == 314
    assert quants.half_width(873, 0.10) == pytest.approx(2.1, abs=0.05)


def test_two_logits_files_are_compared_token_by_token_and_a_different_text_is_refused(tmp_path):
    for name, noise, seed in (("a", 0.0, 1), ("b", 0.0, 1), ("c", 1.0, 1), ("other", 0.0, 9)):
        canned_logits(tmp_path / name, noise, seed=seed)
    same = logits.compare(tmp_path / "a", tmp_path / "b")
    assert len(same) == SCORED * CHUNKS
    assert all(abs(r["kl"]) < 1e-9 and r["agree"] == 1 and r["dlogp"] == 0 for r in same)
    apart = logits.blocks(logits.compare(tmp_path / "a", tmp_path / "c"))
    assert [b["chunk"] for b in apart] == [0, 1, 2, 3] and all(b["kl"] > 0.01 for b in apart)
    assert [b["tokens"] for b in logits.blocks(same, size=3)][:2] == [3, 4]
    with pytest.raises(logits.ToolFailed, match="not over the same text"):
        logits.compare(tmp_path / "a", tmp_path / "other")
    (tmp_path / "cut").write_bytes((tmp_path / "a").read_bytes()[:-1])
    with pytest.raises(logits.ToolFailed, match="its header says"):
        logits.header(tmp_path / "cut")


def test_the_cpu_run_keeps_every_layer_and_operation_off_the_gpu():
    one = (Path("tool"), Path("m.gguf"), Path("t.txt"), Path("o.logits"))
    cpu = logits.argv(*one, logits.Scoring(1024, 8, cpu=True))
    metal = logits.argv(*one, logits.Scoring(1024, 8))
    assert cpu[len(metal):] == ["-ngl", "0", "--device", "none", "--no-op-offload"]
    assert "--save-all-logits" in metal and metal[metal.index("-ctk") + 1] == metal[metal.index("-ctv") + 1] == "q8_0"


def test_the_scoring_tool_is_built_from_the_active_commit_as_its_own_target(tmp_path, monkeypatch):
    monkeypatch.setattr(logits, "active_build", lambda: {"commit": "a" * 40, "build": 7})
    monkeypatch.setattr(llamacpp_compile, "checkout", lambda up, sha, dest, pipeline: dest.mkdir(parents=True))
    monkeypatch.setattr(llamacpp_compile, "toolchain",
                        lambda: llamacpp_compile.Toolchain("cmake", "cc", "c++", "", ()))
    ran = []

    def compiled(argv, policy, cwd):
        ran.append(list(argv))
        if "--target" in argv:
            built = Path(cwd) / "build" / "bin"
            built.mkdir(parents=True)
            (built / argv[argv.index("--target") + 1]).write_text("canned binary, never run")
        return SimpleNamespace(returncode=0, stdout="", stderr="", timed_out=False)

    monkeypatch.setattr(sandbox, "run", compiled)
    made = logits.build_tool(lambda _line: None)
    assert made == bench.home_dir() / "tools" / ("a" * 40) / logits.TOOL and made.is_file()
    assert ran[-1][ran[-1].index("--target") + 1] == logits.TOOL


HUMANEVAL = {"task_id": "HumanEval/canned", "entry_point": "double", "prompt": 'def double(x):\n    """Twice x."""\n',
             "test": "def check(candidate):\n    assert candidate(2) == 4\n"}
RIGHT = f"{CANNED_MARKER}\n```python\ndef double(x):\n    return 2 * x\n```"
WRONG = f"{CANNED_MARKER}\n```python\ndef double(x):\n    return x\n```"


def test_fidelity_keeps_blocks_per_backend_and_says_which_build_is_further_off(tmp_path, monkeypatch, capsys):
    pytest.importorskip("numpy")
    (tmp_path / "tool").write_text("canned tool, never run")
    texts = []
    for domain in ("code", "prose"):
        (tmp_path / f"{domain}.txt").write_text(domain)
        texts += ["--corpus", str(tmp_path / f"{domain}.txt")]
    # canned: (metal, cpu, one token at a time); K moves no further than REF does when only the path changes
    noise = {"REF": (0.0, 0.02, 0.01), "IQ": (1.5, 0.4, 1.5), "K": (0.2, 0.2, 0.2)}
    asked = []

    def wrote(command, *, model_files, corpus, out):
        name = model_files[0].stem
        single = command[command.index("-ub") + 1] == "1"
        route = "cpu" if "--no-op-offload" in command else "single" if single else "metal"
        asked.append((name, route))
        canned_logits(out, noise[name][("metal", "cpu", "single").index(route)])
        return 0.0

    monkeypatch.setattr(quants.logits, "run", wrote)
    monkeypatch.setattr(quants.gpu, "hold", lambda purpose: contextlib.nullcontext())
    quiet_machine(monkeypatch)
    args = args_for(tmp_path, "fidelity", "--tool", str(tmp_path / "tool"), "--ctx", str(CONTEXT), "--chunks",
                    str(CHUNKS), "--logits-dir", str(tmp_path / "logits"), *texts)
    assert quants.run(args) == 0
    said = capsys.readouterr().out
    assert asked.count(("REF", "metal")) == 2 and asked.count(("IQ", "cpu")) == 2
    assert asked.count(("REF", "single")) == 2 and asked.count(("K", "single")) == 2
    assert not list((tmp_path / "logits").glob("*.logits")), "compared files are removed"
    kept = [r for r in bench.runs(args.kept) if r["kind"] == quants.FIDELITY]
    assert len(kept) == 6 and kept[0]["server"]["draft"] == "none" and kept[0]["server"]["corpus_sha256"]
    assert {r["server"]["cache_type"] for r in kept} == {"q8_0"}
    assert {(one["server"]["quant"] == "REF", r["against"], r["backend"]) for one in kept for r in one["rows"]} == {
        (True, "self", "cpu"), (True, "self", "single"), (False, "reference", "metal"), (False, "reference", "cpu"),
        (False, "self", "cpu"), (False, "self", "single")}
    assert "IQ, against itself, on the CPU" in said and "near ties: top tokens differ on" in said
    assert "[metal, 8 blocks] IQ is further from the reference than K" in said
    assert "IQ on Metal is further from the reference than the same file on the CPU" in said
    assert "K on Metal and the same file on the CPU cannot be told apart" in said
    assert "IQ is further from the reference than K: difference" in said and "noise floor" in said


def canned_served(replies):
    """PLUMBING ONLY: stands in for a leased server; every reply is canned and marked."""
    @contextlib.contextmanager
    def served(args, model, *, context, parallel, cache=""):
        client = SimpleNamespace(
            props=lambda: {"chat_template": CANNED_MARKER},
            chat=lambda messages, think: Reply(content=replies[model.stem], finish_reason="stop",
                                               raw={"usage": {"prompt_tokens": len(messages[0]["content"])}}))
        # the keys a real lease's record holds that the comparison's own record also names
        held = {"build": CANNED_MARKER, "cache_type": "q8_0", "reasoning_budget": 0}
        yield client, SimpleNamespace(base_url="canned", port=args.serve_port), held
    return served


def test_tasks_compares_the_same_items_and_does_not_ask_them_again(tmp_path, monkeypatch, capsys):
    items = tmp_path / "items.jsonl"
    items.write_text("".join(json.dumps({**HUMANEVAL, "task_id": f"HumanEval/{n}"}) + "\n" for n in range(6)))
    monkeypatch.setattr(quants, "served", canned_served({"REF": RIGHT, "IQ": WRONG, "K": RIGHT}))
    # the confined run is `tests/test_bench_codeitems.py`'s; here the verdict is canned from the reply
    monkeypatch.setattr(quants.codeitems, "grade", lambda item, reply: reply == RIGHT)
    args = args_for(tmp_path, "tasks", "--items", str(items))
    assert quants.run(args) == 0
    said = capsys.readouterr().out
    assert "IQ: 0/6 passed" in said and "K: 6/6 passed" in said
    assert "[6 shared items, 6 differ] IQ passes fewer than K: difference -100" in said
    assert "[6 shared items, 0 differ] K and REF cannot be told apart" in said
    kept = bench.runs(args.kept)
    assert {r["server"]["settings"]["thinking"] for r in kept} == {"off"} and kept[0]["server"]["template"]
    assert quants.run(args) == 0
    assert "all 6 items are kept already" in capsys.readouterr().out and len(bench.runs(args.kept)) == len(kept)
    # canned: the reference, asked another way, changes every verdict, so no difference clears the floor
    monkeypatch.setattr(quants, "served", canned_served({"REF": WRONG}))
    assert quants.run(args_for(tmp_path, "tasks", "--items", str(items), "--floor")) == 0
    said = capsys.readouterr().out
    assert "6 of 6 items change verdict (100.00 points)" in said
    assert "IQ and K are not distinguishable here: difference -100" in said
    alone = quants.lease_config(SimpleNamespace(**{**vars(args), "single": True}), tmp_path / "IQ.gguf",
                                context=4096, parallel=1)
    assert alone.serving.extra_args == ("-ub", "1")


def test_speed_labels_a_contended_repeat_and_leaves_it_out_of_the_interval(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(quants, "served", canned_served({}))
    rate = {"IQ": 100.0, "K": 200.0, "REF": 50.0}                     # canned tokens per second
    current = []
    monkeypatch.setattr(quants, "residency", lambda path, quant: current.append(path.stem) or 0.5)
    count = iter(range(100))
    monkeypatch.setattr(quants.bench_speed, "grid", lambda client, cells, generate, log: [
        {"prompt_tokens": size, "prefill_tps": rate[current[-1]] + next(count) % 3, "decode_tps": 10.0, "errors": 0}
        for size, _ in cells])
    looks = iter(range(100))
    # the first look is before the run, then two per load: the eleventh is before the second repeat's second load
    quiet_machine(monkeypatch, lambda: quiet.Quiet(reasons=("canned: busy",) if next(looks) == 9 else ()))
    args = args_for(tmp_path, "speed", "--repeats", "3", "--prompts", "512", "--settle", "0")
    assert quants.run(args) == 0
    said = capsys.readouterr().out
    assert "CONTENDED" in said and "1 contended or failed cell(s) left out" in said
    assert "IQ is slower than K" in said and "50% to 50%" in said
    rows = [row for r in bench.runs(args.kept) for row in r["rows"]]
    assert len(rows) == 9 and sum(row["contended"] for row in rows) == 1


def test_the_lease_has_no_draft_head_and_thinking_off(tmp_path):
    args = args_for(tmp_path, "tasks", "--max-tokens", "99", "--serve-port", "8123")
    config = quants.lease_config(args, tmp_path / "IQ.gguf", context=8192, parallel=2)
    lease = config.lease()
    assert not lease.get("draft") and lease["reasoning_budget"] == 0 and lease["parallel"] == 2
    assert lease["mtp"] is False and lease["port"] == 8123 and lease["cache_type_k"] == "q8_0" and lease["context"] == 8192
    assert config.talking.sampling["temperature"] == 0.0 and config.talking.n_predict == 99


def test_one_file_under_two_cache_types_is_scored_and_leased_with_each(tmp_path, monkeypatch, capsys):
    pytest.importorskip("numpy")
    (tmp_path / "tool").write_text("canned tool, never run")
    (tmp_path / "code.txt").write_text("canned text")
    commands = []

    def wrote(command, *, model_files, corpus, out):
        commands.append(command)
        canned_logits(out, 0.5 if command[command.index("-ctk") + 1] == "q8_0" else 0.0)
        return 0.0

    monkeypatch.setattr(quants.logits, "run", wrote)
    monkeypatch.setattr(quants.gpu, "hold", lambda purpose: contextlib.nullcontext())
    quiet_machine(monkeypatch)
    args = args_for(tmp_path, "fidelity", "--cache-of", "IQ", "--no-cpu", "--no-single", "--tool", str(tmp_path / "tool"), "--ctx",
                    str(CONTEXT), "--chunks", str(CHUNKS), "--corpus", str(tmp_path / "code.txt"),
                    "--logits-dir", str(tmp_path / "logits"))
    assert quants.run(args) == 0
    assert [(c[c.index("-ctk") + 1], c[c.index("-ctv") + 1], c[c.index("-m") + 1].endswith("IQ.gguf"))
            for c in commands] == [("f16", "f16", True), ("q8_0", "q8_0", True)]
    assert "IQ@q8_0, metal against IQ@f16" in capsys.readouterr().out
    plan = quants.load_comparison(args)
    assert plan.id == "canned+cache-IQ" and plan.versus == (("IQ@q8_0", "IQ@f16"),)
    config = quants.lease_config(args, tmp_path / "IQ.gguf", context=4096, parallel=1, cache="f16")
    assert config.lease()["cache_type_k"] == "f16" and config.serving.draft_cache_type == "f16"


def test_items_asked_at_long_context_are_kept_apart_from_short_ones(tmp_path, monkeypatch):
    items = tmp_path / "items.jsonl"
    items.write_text(json.dumps(HUMANEVAL) + "\n")
    (tmp_path / "text.txt").write_text("canned background " * 100)
    monkeypatch.setattr(quants, "served", canned_served({"REF": RIGHT, "IQ": RIGHT, "K": RIGHT}))
    monkeypatch.setattr(quants.codeitems, "grade", lambda item, reply: True)
    short = args_for(tmp_path, "tasks", "--items", str(items))
    long = args_for(tmp_path, "tasks", "--items", str(items), "--long-context", "100", "--corpus",
                    str(tmp_path / "text.txt"))
    assert quants.run(short) == 0 and quants.run(long) == 0
    runs = bench.runs(short.kept)
    assert len(runs) == 6 and len({r["server"]["settings"]["before"] for r in runs}) == 2
    asked = {bool(r["server"]["settings"]["before"]): r["rows"][0]["prompt_tokens"] for r in runs}
    assert asked[True] > asked[False] + 300


def test_the_frozen_text_is_as_long_as_the_context_needs(tmp_path):
    args = args_for(tmp_path, "fidelity")
    made = quants.corpus(args, 300_000)
    assert {d: len(p.read_text()) for d, p in made.items()} == {"code": 300_000, "prose": 300_000}


def test_a_published_build_of_another_revision_is_called_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    repo = tmp_path / "models--org--name"
    (repo / "blobs").mkdir(parents=True)
    (repo / "snapshots" / "one").mkdir(parents=True)
    (repo / "blobs" / ("b" * 64)).write_bytes(b"old")
    (repo / "snapshots" / "one" / "m.gguf").symlink_to(repo / "blobs" / ("b" * 64))
    new = quants.Quant("new", "org/name", ({"path": "m.gguf", "bytes": 3, "sha256": "c" * 12},))
    old = quants.Quant("old", "org/name", ({"path": "m.gguf", "bytes": 3, "sha256": "b" * 12},))
    assert new.found() == (None, "stale") and new.missing() == {"c" * 12: 3}
    assert old.found()[1] == "present" and old.missing() == {}


def test_the_shipped_comparisons_name_builds_they_hold_and_shared_shards_once(capsys):
    config = json.loads(quants.CONFIG.read_text())
    assert set(config["sets"]) == {"qwen3.8-27b", "qwen3.8-flash-next", "qwen3.8-flash-next-round2"}
    for one in config["sets"].values():
        held = set(one["quants"])
        assert one["reference"] in held and all(set(two) <= held for two in one["versus"])
    quants4 = config["sets"]["qwen3.8-flash-next"]["quants"].values()
    tables = {s["sha256"] for shards in quants4 for s in shards if s["bytes"] == 28800138432}
    assert tables == {"5de8b5db", "05716382"}
