"""Cache costs, the 128k coding default and prompt-time estimates of the placement planner.

Synthetic headers only: the cache math reads metadata keys, the prompt math reads tensor shapes.
Expected numbers are worked out here from the shapes and the cache types.
"""

from __future__ import annotations

import argparse

import pytest
from plan_fixtures import Q4_K, flash_next, metadata

from poolhouse.hub.probe import Gpu, MachineMemory
from poolhouse.serve import lifecycle_cli, plan_cli
from poolhouse.serve.backend import ServerSpec
from poolhouse.serve.kvbudget import kv_layers, kv_row
from poolhouse.serve.placement import cache_bytes, part_of
from poolhouse.serve.plan import GIB, Hardware
from poolhouse.serve.plan_cli import Ask
from poolhouse.serve.preflight import read_gguf_header
from poolhouse.serve.tensors import tensors_of

BYTES = {"f16": 2, "q8_0": 34 / 32, "q4_0": 18 / 32}


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    return flash_next(tmp_path_factory.mktemp("flash"))


def card(vram_gb, ram_gb=64):
    return Hardware("discrete", int(vram_gb * GIB), int(ram_gb * GIB))


def planned(model, hw, context=131072):
    return plan_cli.plan_for(model, hw, Ask(context=context))


def meta(**extra):
    return {**metadata(), "qwen4exp.attention.compress_ratios": [0, 0, 0, 4] * 12, **extra}


class TestKvBudget:
    def test_the_layers_that_hold_a_cache_come_from_the_layer_pattern(self):
        held = kv_layers(meta())
        assert sum(held) == 12 and [n for n, h in enumerate(held) if h][:3] == [3, 7, 11]

    def test_twelve_is_not_assumed(self):
        small = {**metadata(), "qwen4exp.block_count": 12, "qwen4exp.full_attention_interval": 6}
        assert sum(kv_layers(small)) == 2
        assert kv_row(small, "f16", "f16").layers == 2

    def test_the_interval_gives_the_same_layers_as_the_ratios(self):
        assert kv_layers(meta()) == kv_layers(metadata())

    @pytest.mark.parametrize("kind", ["f16", "q8_0", "q4_0"])
    def test_bytes_a_token_are_layers_times_heads_times_dims_times_the_type(self, kind):
        row = kv_row(meta(), kind, kind)
        expected = int(12 * 2 * 256 * 2 * BYTES[kind])
        assert row.kv_per_token == expected
        assert row.indexer_per_token == expected

    def test_the_f16_figure_is_24576_bytes_a_token(self):
        assert kv_row(meta(), "f16", "f16").kv_per_token == 24576

    def test_the_indexer_floor_is_one_shared_key_head(self):
        assert kv_row(meta(), "f16", "f16").indexer_floor_per_token == 12 * 128 * 2

    def test_a_header_without_an_indexer_has_no_indexer_cache(self):
        bare = meta()
        del bare["qwen4exp.attention.indexer.key_length"]
        row = kv_row(bare, "f16", "f16")
        assert (row.indexer_per_token, row.indexer_floor_per_token) == (0, 0)
        assert row.per_token == 12 * 2 * 256 * 4

    def test_k_and_v_may_be_stored_differently(self):
        assert kv_row(meta(), "q8_0", "q4_0").kv_per_token == int(
            12 * 2 * 256 * (34 / 32 + 18 / 32))

    def test_the_recurrent_state_is_fixed_per_sequence_and_scales_with_slots(self):
        row = kv_row(meta(), "f16", "f16")
        assert row.recurrent_per_seq == 36 * (3 * (6144 + 2 * 16 * 128) + 128 * 6144) * 4
        assert row.total(131072, 2) - row.total(131072, 1) == row.recurrent_per_seq

    def test_128k_at_f16_is_the_hand_estimate_plus_the_allocated_indexer(self):
        row = kv_row(meta(), "f16", "f16")
        assert row.kv_per_token * 131072 == 3_221_225_472
        assert row.total(131072) == 2 * 3_221_225_472 + row.recurrent_per_seq

    def test_the_plan_charges_what_the_budget_says(self, model):
        found = read_gguf_header(model)
        assert cache_bytes(found, 131072, "q8_0", "q8_0", 1) == kv_row(
            found, "q8_0", "q8_0").total(131072)

    def test_the_plan_lists_every_cache_type_with_the_chosen_one_first(self, model):
        plan = plan_cli.plan_for(model, card(24), Ask(131072, 1, ("q8_0", "q4_0")))
        assert [(r.cache_k, r.cache_v) for r in plan.kv_rows] == [
            ("q8_0", "q4_0"), ("f16", "f16"), ("q8_0", "q8_0"), ("q4_0", "q4_0")]
        text = plan_cli.render(plan)
        assert "K f16" in text and "quantising K costs more quality" in text


class TestCodingContext:
    def test_the_default_target_is_the_most_the_placement_holds(self):
        default = next(o for o in plan_cli.OPTIONS if "--context" in o.flags).kwargs["default"]
        assert default == 0 and not hasattr(plan_cli, "DEFAULT_CONTEXT")

    def test_the_default_context_is_the_largest_one_the_placement_holds(self, model):
        got = plan_cli.plan_for(model, card(24), Ask())
        assert 0 < got.context <= 262144       # never past the trained window the header names
        if got.context < 262144:
            with pytest.raises(plan_cli.Shortfall):
                plan_cli.plan_for(model, card(24), Ask(context=got.context + 256))
        assert plan_cli.plan_for(model, card(12), Ask()).context <= got.context

    def test_up_plan_auto_fits_an_unset_context_and_keeps_a_chosen_one(self, model,
                                                                       monkeypatch):
        monkeypatch.setattr(plan_cli, "machine_memory", lambda: MachineMemory(
            total_ram=128 * GIB, gpus=(Gpu("c", 24 * GIB, 24 * GIB, "nvidia"),)))
        args = argparse.Namespace(on_cpu=[], cpu_moe=False, n_cpu_moe=None, plan="auto")
        default = lifecycle_cli._planned(args, ServerSpec(
            model=str(model), cache_type_k="q8_0", cache_type_v="q8_0"))
        assert default.context == plan_cli.plan_for(model, card(24, 128), Ask()).context
        chosen = lifecycle_cli._planned(args, ServerSpec(
            model=str(model), context=65536, cache_type_k="q8_0", cache_type_v="q8_0"))
        assert chosen.context == 65536

    def test_more_context_leaves_fewer_experts_on_the_card(self, model):
        assert planned(model, card(24), 131072).fast_experts < planned(
            model, card(24), 32768).fast_experts

    def test_a_32g_card_at_128k_needs_fewer_experts_in_ram_with_q8_than_f16(self, tmp_path):
        pruned = flash_next(tmp_path, experts=256, exps_type=Q4_K)
        f16 = plan_cli.plan_for(pruned, card(32), Ask(131072, 1, ("f16", "f16")))
        q8 = plan_cli.plan_for(pruned, card(32), Ask(131072, 1, ("q8_0", "q8_0")))
        assert f16.kv_bytes > q8.kv_bytes
        assert f16.n_cpu_moe > q8.n_cpu_moe


class TestPromptTime:
    def estimates(self, plan):
        return {e.name: e for e in plan.estimates if e.name.startswith("prompt")}

    def test_the_two_bounds_follow_their_formulas(self, model):
        plan = planned(model, card(24))
        got = self.estimates(plan)
        batches = 131072 // 512
        fast = plan.facts.always_bytes + plan.fast_experts
        per_batch = fast / 300e9 + plan.host_in_ram / 50e9 + plan.paged / 3e9
        assert got["prompt of 131,072 tokens, bandwidth-bound time"].value == pytest.approx(
            batches * per_batch)
        every = tensors_of(model)
        elements = (sum(t.elements for t in every if part_of(t).role not in ("experts", "table"))
                    - 2560 * 248320
                    + sum(t.elements for t in every if part_of(t).role == "experts") * 10 // 512)
        assert got["prompt of 131,072 tokens, compute-bound time"].value == pytest.approx(
            131072 * 2 * elements / 20e12)

    def test_the_speed_is_the_prompt_over_the_larger_bound(self, model):
        got = self.estimates(planned(model, card(24)))
        bound = max(v.value for k, v in got.items() if k.endswith("time"))
        assert got["prompt processing speed upper bound"].value == pytest.approx(131072 / bound)

    def test_paging_from_disk_makes_the_prompt_bandwidth_bound(self, model):
        got = self.estimates(planned(model, card(12, 16)))
        assert got["prompt of 131,072 tokens, bandwidth-bound time"].value > got[
            "prompt of 131,072 tokens, compute-bound time"].value

    def test_the_listing_flags_prompt_processing_and_marks_the_vendor_figure_as_a_claim(self, model):
        text = plan_cli.render(planned(model, card(24)))
        assert "practical cost" in text and "a claim" in text
