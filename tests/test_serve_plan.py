"""Placement of a Flash-Next-shaped model on each class of machine.

Every model here is a header written into ``tmp_path`` with the real tensor names, shapes and
types and no tensor data; no server starts and no GPU is touched. Expected numbers are worked
out in the tests from the shapes, not read back from the code under test.
"""

from __future__ import annotations

import argparse

import pytest
from plan_fixtures import (
    BF16,
    EMBED,
    F32,
    FF,
    IQ4_NL,
    LAYERS,
    Q4_K,
    Q8_0,
    UNKNOWN,
    flash_next,
    layer_tensors,
    metadata,
    sized,
    table_tensor,
    write_shard,
)

from poolhouse.hub.probe import Gpu, MachineMemory
from poolhouse.serve import lifecycle_cli, plan_cli
from poolhouse.serve.backend import LlamaServerBackend, ServerSpec
from poolhouse.serve.placement import cache_bytes, facts_of, part_of
from poolhouse.serve.plan import DEFAULTS, GIB, Hardware, Shortfall
from poolhouse.serve.plan_cli import Ask
from poolhouse.serve.preflight import read_gguf_header, recurrent_state_bytes
from poolhouse.serve.tensors import Tensor, tensors_by_shard, tensors_of
from poolhouse.units import human_bytes

LAYER_EXPERTS = 3 * (2560 * 640 * 512 // 256 * 144)
CONTEXT = 32768
KV_PER_TOKEN = 12 * 2 * 256 * 2 * (34 / 32) * 2


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    return flash_next(tmp_path_factory.mktemp("flash"))


def planned(model, hw, context=CONTEXT):
    return plan_cli.plan_for(model, hw, Ask(context=context))


def card(vram_gb, ram_gb=64):
    return Hardware("discrete", int(vram_gb * GIB), int(ram_gb * GIB))


class TestWhatATensorIsFor:
    @pytest.mark.parametrize("name,role,layer", [
        ("per_layer_token_embd.weight", "table", None),
        ("blk.3.ffn_gate_exps.weight", "experts", 3),
        ("blk.12.ffn_down_exps.weight", "experts", 12),
        ("blk.3.ffn_gate_inp.weight", "router", 3),
        ("blk.3.ffn_gate_inp_shexp.weight", "router", 3),
        ("blk.3.ffn_up_shexp.weight", "shared", 3),
        ("blk.0.ssm_out.weight", "attention", 0),
        ("blk.3.indexer.q_proj.weight", "attention", 3),
        ("blk.0.attn_qkv.weight", "attention", 0),
        ("token_embd.weight", "embedding", None),
        ("output.weight", "embedding", None),
        ("output_norm.weight", "other", None),
        ("blk.0.hc_attn_down.weight", "other", 0),
        ("blk.48.nextn.eh_proj.weight", "mtp", 48),
    ])
    def test_the_role_and_layer_come_from_the_name(self, name, role, layer):
        got = part_of(Tensor(name, "q4_k", (1,), 1))
        assert (got.role, got.layer) == (role, layer)


class TestHeaderSizes:
    def test_a_type_the_table_lacks_is_sized_from_the_next_offset(self, tmp_path):
        entries = [sized("a.weight", UNKNOWN, (64, 64), 1000), sized("b.weight", F32, (8,))]
        path = write_shard(tmp_path / "u.gguf", metadata(), entries)
        a, b = sorted(tensors_by_shard(path)[0][1], key=lambda t: t.offset)
        assert (a.type, a.bytes) == ("unknown type 42", 1024)
        assert b.type == "f32" and b.bytes == 32

    def test_a_last_tensor_of_an_unknown_type_is_sized_to_the_end_of_the_file(self, tmp_path):
        entries = [sized("a.weight", F32, (8,)), sized("b.weight", UNKNOWN, (4, 4), 4096)]
        path = write_shard(tmp_path / "u.gguf", metadata(), entries, fill=True)
        b = next(t for t in tensors_of(path) if t.name == "b.weight")
        assert (b.type, b.bytes) == ("unknown type 42", 4096)

    def test_the_offset_is_where_the_data_starts_in_the_file(self, tmp_path):
        path = write_shard(tmp_path / "o.gguf", {**metadata(), "general.alignment": 64},
                           [sized("a.weight", F32, (8,)), sized("b.weight", F32, (8,))], align=64)
        a, b = sorted(tensors_of(path), key=lambda t: t.offset)
        assert a.offset % 64 == 0 and b.offset - a.offset == 64


class TestModelFacts:
    def facts(self, model):
        return facts_of(tensors_by_shard(model), read_gguf_header(model), "flash")

    def test_expert_reads_per_token_are_used_over_experts_of_each_layer(self, model):
        facts = self.facts(model)
        assert facts.experts == 512 and facts.used == 10
        assert facts.expert_layer_bytes == (LAYER_EXPERTS,) * LAYERS
        assert sum(facts.expert_read(n) for n in range(LAYERS)) == LAYERS * LAYER_EXPERTS * 10 // 512
        assert sum(facts.expert_read(n) for n in range(LAYERS)) == 1_327_104_000

    def test_a_pruned_model_reads_its_expert_count_from_the_tensors(self, tmp_path):
        path = flash_next(tmp_path, experts=256)
        meta = {**read_gguf_header(path), "qwen4exp.expert_count": 512}
        facts = facts_of(tensors_by_shard(path), meta, "pruned")
        assert facts.experts == 256
        assert any("512 experts and the tensors hold 256" in n for n in facts.notes)
        assert sum(facts.expert_read(n) for n in range(LAYERS)) == LAYERS * (LAYER_EXPERTS // 2) * 10 // 256

    def test_the_table_is_rows_by_the_head_count(self, model):
        facts = self.facts(model)
        assert (facts.table_rows, facts.table_rows_per_token) == (320001536, 16)
        assert facts.table_row_bytes == 160 // 32 * 18

    def test_always_needed_is_everything_but_experts_and_the_table(self, model):
        facts = self.facts(model)
        total = sum(t.bytes for t in tensors_of(model))
        assert facts.always_bytes + facts.expert_bytes + facts.table == total
        assert facts.read_per_token() == facts.always_bytes - 2560 * 248320 // 256 * 144


class TestTwoShards:
    def test_the_shard_holding_the_table_is_the_disk_candidate(self, tmp_path):
        first = flash_next(tmp_path, shards=2, experts=256)
        facts = facts_of(tensors_by_shard(first), read_gguf_header(first), "two")
        resident, ngram = facts.shards
        assert not resident.holds_table and ngram.holds_table
        assert ngram.roles == {"table": 28_800_138_240}
        assert resident.roles["experts"] == facts.expert_bytes
        assert facts.table == ngram.bytes

    def test_a_card_plan_names_both_shards_and_serves_the_table_from_disk(self, tmp_path):
        first = flash_next(tmp_path, shards=2, experts=256)
        plan = planned(first, card(12, 32))
        assert plan.spec_fields()["override_tensor"] == ("per_layer_token_embd=CPU",)
        assert plan.disk_bytes == 28_800_138_240 + plan.paged
        text = plan_cli.render(plan)
        assert "00001-of-00002" in text and "00002-of-00002" in text
        assert "holds the n-gram table" in text

    def test_unknown_types_in_the_experts_still_count_toward_the_residency(self, tmp_path):
        gap = 150_000_000
        first = flash_next(tmp_path, shards=2, experts=256, unknown={1: {"ffn_down_exps": gap}})
        facts = facts_of(tensors_by_shard(first), read_gguf_header(first), "u")
        per = 3 * (2560 * 640 * 256 // 256 * 144)
        down = 640 * 2560 * 256 // 256 * 144
        assert facts.expert_layer_bytes[1] == per - down + gap // 32 * 32 + (32 if gap % 32 else 0)
        assert any("unknown type 42" in n for n in facts.notes)


class TestDiscreteCards:
    @pytest.mark.parametrize("vram", [8, 12, 16, 24])
    def test_the_card_takes_the_most_experts_that_fit(self, model, vram):
        plan = planned(model, card(vram))
        always = plan.facts.always_bytes
        room = int(vram * GIB) - DEFAULTS["device_reserve"] - always - plan.kv_bytes - plan.allowance
        keep = min(LAYERS, room // LAYER_EXPERTS)
        assert plan.n_cpu_moe == LAYERS - keep
        assert plan.fast_experts == keep * LAYER_EXPERTS
        assert plan.fast_resident + DEFAULTS["device_reserve"] <= vram * GIB
        if keep:
            assert plan.spec_fields()["n_cpu_moe"] == LAYERS - keep
        else:
            assert plan.spec_fields()["cpu_moe"] is True
        assert plan.spec_fields()["override_tensor"] == ("per_layer_token_embd=CPU",)

    def test_a_bigger_card_never_moves_more_experts_to_the_cpu(self, model):
        moved = [planned(model, card(v)).n_cpu_moe for v in (8, 12, 16, 24, 48, 96)]
        assert moved == sorted(moved, reverse=True) and moved[0] > moved[-1] == 0

    def test_a_card_only_the_always_needed_set_fits_runs_every_expert_on_the_cpu(self, model):
        facts = facts_of(tensors_by_shard(model), read_gguf_header(model), "m")
        kv = cache_bytes(read_gguf_header(model), CONTEXT, "q8_0", "q8_0", 1)
        vram = facts.always_bytes + kv + DEFAULTS["compute_buffers"] + DEFAULTS["device_reserve"] + 1
        plan = planned(model, Hardware("discrete", vram, 128 * GIB))
        assert plan.cpu_moe and plan.spec_fields()["cpu_moe"] is True
        assert "n_cpu_moe" not in plan.spec_fields()

    def test_no_expert_flag_when_every_expert_fits(self, model):
        plan = planned(model, card(96))
        assert "n_cpu_moe" not in plan.spec_fields() and not plan.cpu_moe

    def test_the_kv_cache_counts_only_the_layers_that_hold_one(self, model):
        plan = planned(model, card(24))
        assert plan.kv_bytes - recurrent_state_bytes(read_gguf_header(model)) == int(KV_PER_TOKEN * CONTEXT)

    def test_the_explicit_flags_say_the_same_as_the_count(self, model):
        plan = planned(model, card(12))
        explicit = plan.explicit_override()
        assert explicit[0] == "per_layer_token_embd=CPU"
        moved = plan.n_cpu_moe
        assert explicit[1] == r"blk\.(" + "|".join(str(n) for n in range(moved)) + r")\.ffn_.*_exps\.=CPU"


class TestRamAndDisk:
    def test_experts_beyond_ram_are_paged_and_the_speed_is_disk_bound(self, model):
        plan = planned(model, card(8, 16))
        host = plan.host_experts
        held = int(16 * GIB) - DEFAULTS["ram_reserve"]
        assert (plan.host_in_ram, plan.paged) == (held, host - held)
        bound = next(e for e in plan.estimates if e.name.endswith("overlapped"))
        per = LAYER_EXPERTS * 10 // 512
        host_read = plan.n_cpu_moe * per
        disk = host_read * (host - held) / host
        ram = host_read - disk
        fast = plan.facts.read_per_token() + (LAYERS - plan.n_cpu_moe) * per
        expect = 1 / max(fast / (300e9), ram / (50e9), disk / (3e9))
        assert bound.value == pytest.approx(expect)
        assert "bytes read per token / bandwidth" in bound.formula

    def test_a_faster_disk_raises_the_bound(self, model):
        slow = planned(model, Hardware("discrete", 8 * GIB, 16 * GIB, disk_gbps=1.0))
        quick = planned(model, Hardware("discrete", 8 * GIB, 16 * GIB, disk_gbps=7.0))
        pick = lambda p: next(e for e in p.estimates if e.name.endswith("overlapped")).value  # noqa: E731
        assert pick(quick) > pick(slow)

    def test_no_card_runs_on_the_cpu_and_pages_what_ram_cannot_hold(self, model):
        plan = planned(model, Hardware("cpu", 0, 32 * GIB))
        assert plan.spec_fields() == {"n_gpu_layers": 0}
        assert plan.paged > 0 and plan.host_in_ram + plan.paged == 48 * LAYER_EXPERTS

    def test_the_table_is_counted_as_disk_and_never_as_ram(self, model):
        plan = planned(model, card(12, 64))
        assert plan.disk_bytes >= plan.facts.table
        assert plan.ram_resident == plan.host_in_ram


class TestUnifiedMemory:
    def test_no_override_is_set_and_the_reason_is_given(self, model):
        plan = planned(model, Hardware("unified", 110 * GIB, 128 * GIB))
        assert plan.spec_fields() == {}
        assert any("unified memory" in why and "no override" in why for why in plan.reasons)
        assert plan.paged == 0

    def test_a_pool_too_small_for_the_experts_pages_the_rest_from_disk(self, model):
        plan = planned(model, Hardware("unified", 32 * GIB, 32 * GIB))
        assert plan.spec_fields() == {} and plan.paged > 0
        assert any("paged from disk" in why for why in plan.reasons)


class TestRefusal:
    def test_the_message_names_the_exact_shortfall(self, model):
        facts = facts_of(tensors_by_shard(model), read_gguf_header(model), "flash.gguf")
        kv = cache_bytes(read_gguf_header(model), CONTEXT, "q8_0", "q8_0", 1)
        need = facts.always_bytes + kv + DEFAULTS["compute_buffers"]
        have = 4 * GIB - DEFAULTS["device_reserve"]
        with pytest.raises(Shortfall) as raised:
            planned(model, card(4))
        first = raised.value.lines[0]
        assert f"needs {human_bytes(need)}" in first and f"short by {human_bytes(need - have)}" in first
        assert human_bytes(have) in first

    def test_a_shorter_context_is_what_the_second_line_offers(self, model):
        with pytest.raises(Shortfall) as raised:
            planned(model, card(4))
        assert "shorter context" in raised.value.lines[1]

    def test_ram_that_cannot_hold_the_always_needed_set_is_refused_for_the_cpu(self, model):
        with pytest.raises(Shortfall) as raised:
            planned(model, Hardware("cpu", 0, 8 * GIB))
        assert "in RAM" in raised.value.lines[0]


class TestEstimatesAreLabelled:
    def test_every_figure_carries_its_formula(self, model):
        plan = planned(model, card(12))
        assert plan.estimates and all(e.formula for e in plan.estimates)
        text = plan_cli.render(plan)
        assert "ESTIMATES" in text and "not measured" in text

    def test_the_expert_read_comes_from_the_header_types(self, model):
        plan = planned(model, card(24))
        got = next(e for e in plan.estimates if e.name.startswith("routed-expert"))
        assert got.value == 1_327_104_000


class TestUpPlanAuto:
    def spec(self, model):
        return ServerSpec(model=str(model), context=CONTEXT, cache_type_k="q8_0", cache_type_v="q8_0")

    def args(self, **given):
        return argparse.Namespace(on_cpu=[], cpu_moe=False, n_cpu_moe=None, plan="auto", **given)

    def machine(self, monkeypatch, found):
        monkeypatch.setattr(plan_cli, "machine_memory", lambda: found)

    def test_a_12g_card_gets_the_overrides_in_the_launch_argv(self, model, monkeypatch):
        card12 = Gpu("test card", 12 * GIB, 12 * GIB, "nvidia")
        self.machine(monkeypatch, MachineMemory(total_ram=64 * GIB, gpus=(card12,)))
        spec = lifecycle_cli._planned(self.args(), self.spec(model))
        argv = LlamaServerBackend._placement_argv(spec)
        assert argv[:2] == ["--override-tensor", "per_layer_token_embd=CPU"]
        assert argv[2] == "--n-cpu-moe" and int(argv[3]) == planned(model, card(12)).n_cpu_moe

    def test_a_mac_gets_no_placement_flag(self, model, monkeypatch):
        self.machine(monkeypatch, MachineMemory(total_ram=128 * GIB, unified=True,
                                                gpu_limit_bytes=110 * GIB))
        spec = lifecycle_cli._planned(self.args(), self.spec(model))
        assert LlamaServerBackend._placement_argv(spec) == []

    def test_an_explicit_flag_wins_and_nothing_is_set(self, model, monkeypatch):
        card12 = Gpu("test card", 12 * GIB, 12 * GIB, "nvidia")
        self.machine(monkeypatch, MachineMemory(total_ram=64 * GIB, gpus=(card12,)))
        args = self.args()
        args.n_cpu_moe = 5
        asked = self.spec(model)
        assert lifecycle_cli._planned(args, asked) is asked

    def test_nothing_that_fits_is_the_exit_code(self, model, monkeypatch):
        tiny = Gpu("tiny card", 4 * GIB, 4 * GIB, "nvidia")
        self.machine(monkeypatch, MachineMemory(total_ram=64 * GIB, gpus=(tiny,)))
        assert lifecycle_cli._planned(self.args(), self.spec(model)) == 2

    def test_a_model_not_on_disk_is_left_as_asked(self, tmp_path):
        asked = self.spec(tmp_path / "absent.gguf")
        assert lifecycle_cli._planned(self.args(), asked) is asked


class TestPlanCommand:
    def test_the_command_prints_a_plan_and_json(self, model, capsys):
        args = argparse.Namespace(model=str(model), vram=12.0, ram=64.0, unified=None,
                                  disk_read=3.0, ram_gbps=50.0, vram_gbps=300.0,
                                  context=CONTEXT, parallel=1, cache_type_k="q8_0", cache_type_v="q8_0",
                                  compute=2.0, ubatch=512, tflops=20.0, json=True)
        assert plan_cli.cmd_plan(args) == 0
        out = capsys.readouterr().out
        assert '"spec_fields"' in out and '"measured": false' in out

    def test_a_refusal_is_exit_two(self, model):
        args = argparse.Namespace(model=str(model), vram=4.0, ram=64.0, unified=None,
                                  disk_read=3.0, ram_gbps=50.0, vram_gbps=300.0,
                                  context=CONTEXT, parallel=1, cache_type_k="q8_0", cache_type_v="q8_0",
                                  compute=2.0, ubatch=512, tflops=20.0, json=False)
        assert plan_cli.cmd_plan(args) == 2


def test_the_pieces_used_by_the_fixture_are_the_real_names():
    names = {e[0] for il in (0, 3) for e in layer_tensors(il, 512, Q4_K)}
    assert {"blk.0.ssm_out.weight", "blk.3.attn_q.weight", "blk.3.ffn_gate_exps.weight",
            "blk.0.ffn_gate_inp.weight"} <= names
    assert table_tensor()[0] == "per_layer_token_embd.weight" and (EMBED, FF, BF16, Q8_0, IQ4_NL)
