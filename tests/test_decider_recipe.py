"""The trainer's defaults, which are the reference recipe's. A decider prompt has no length limit of ours.

The recipe's numbers are read from github.com/strands-labs/strands-decider at commit
d942e5515db750f80a51032629ec312455a87976 (configs/train.yaml, training/train.py,
skills/customize-decider/references/training.md); docs/decision-models.md cites each line."""

from __future__ import annotations

import pytest

from poolhouse.decide.pins import BASES, QWEN35_0_8B_BASE, QWEN35_2B_BASE, STRANDS_V19
from poolhouse.train.decider import Settings


def test_the_default_run_is_the_reference_recipe_on_the_released_base():
    s = Settings()
    assert s.base is QWEN35_2B_BASE and s.base.name == "qwen3.5-2b-base"
    assert (s.epochs, s.steps, s.batch_size, s.grad_accum) == (1.0, None, 8, 4)
    assert (s.lr, s.head_scale, s.weight_decay, s.warmup_ratio) == (1e-4, 10.0, 0.01, 0.03)
    assert s.gradient_checkpointing and s.dtype == "bfloat16"
    assert (s.lora.rank, s.lora.alpha, s.lora.dropout) == (16, 32, 0.05)
    assert {"in_proj_qkv", "in_proj_z", "in_proj_a", "in_proj_b", "out_proj"} <= set(s.lora.targets)


def test_the_trainer_has_no_prompt_limit_unless_the_person_sets_one():
    from poolhouse.decide import pointer_prompt
    assert Settings().max_tokens is None and Settings.smoke().max_tokens is None
    assert not hasattr(pointer_prompt, "MAX_PROMPT_TOKENS") and not hasattr(pointer_prompt, "too_long")


def test_continuing_the_released_head_trains_it_at_the_adapters_rate_unless_told_otherwise():
    assert Settings(init=STRANDS_V19).head_scale == 1.0
    assert Settings(init=STRANDS_V19, head_lr_scale=3.0).head_scale == 3.0


def test_the_smoke_preset_is_the_small_base_and_a_few_steps():
    s = Settings.smoke()
    assert s.base is QWEN35_0_8B_BASE and (s.steps, s.batch_size, s.grad_accum) == (60, 4, 1)
    assert not s.gradient_checkpointing
    assert s.max_tokens is None
    assert Settings.smoke(steps=3, name="x").steps == 3


def test_steps_follow_the_epochs_and_the_cases_a_step_takes():
    s = Settings()
    assert s.steps_for(32) == 1 and s.steps_for(33) == 2 and s.steps_for(1000) == 32
    assert Settings(epochs=3.0).steps_for(1000) == 94
    assert Settings(steps=7).steps_for(1000) == 7
    assert s.steps_for(1) == 1


def test_every_pinned_base_has_the_files_a_run_loads_a_tokenizer_among_them():
    for base in BASES.values():
        names = {p.filename for p in base.files}
        assert {"config.json", "tokenizer.json", "tokenizer_config.json"} <= names, base.name
        assert any(n.endswith(".safetensors") for n in names), base.name
    assert len({p.repo for p in QWEN35_2B_BASE.files}) == 1


def test_each_pass_visits_every_case_once_and_the_next_pass_goes_in_another_order():
    from poolhouse.train.decider import pass_order
    first, second = pass_order(40, 0, 0), pass_order(40, 0, 1)
    assert sorted(first) == sorted(second) == list(range(40))
    assert first != second and first == pass_order(40, 0, 0)
    assert pass_order(40, 1, 0) != first


def test_the_step_accumulates_the_gradient_of_its_micro_batches_as_one_batch():
    torch = pytest.importorskip("torch")
    from poolhouse.train.decider import DeciderStep

    def loss(model, batch):
        return ((model(batch) - 1.0) ** 2).mean()

    def weights(batches):
        torch.manual_seed(0)
        model = torch.nn.Linear(3, 1)
        step = DeciderStep(model, torch.optim.SGD(model.parameters(), lr=0.1), loss)
        value, applied = step(batches)
        assert applied
        return model.weight.detach().clone(), value

    data = torch.arange(24.0).reshape(8, 3) / 10
    whole, whole_loss = weights(data)
    parts, parts_loss = weights([data[:4], data[4:]])
    assert torch.allclose(whole, parts, atol=1e-6)
    assert abs(whole_loss - parts_loss) < 1e-5


def test_a_non_finite_micro_batch_is_not_applied():
    torch = pytest.importorskip("torch")
    from poolhouse.train.decider import DeciderStep
    model = torch.nn.Linear(2, 1)
    before = model.weight.detach().clone()
    step = DeciderStep(model, torch.optim.SGD(model.parameters(), lr=0.1),
                       lambda m, b: m(b).sum() * float("nan"))
    value, applied = step([torch.ones(2, 2)])
    assert not applied and value != value
    assert torch.equal(model.weight.detach(), before)


def test_only_the_heads_matrices_decay_and_the_head_trains_at_its_scale():
    torch = pytest.importorskip("torch")
    from types import SimpleNamespace

    from poolhouse.decide.pointer import build_head
    from poolhouse.train.decider import build_optimizer
    torso = torch.nn.Linear(4, 4)
    net = SimpleNamespace(torso=torso, head=build_head(torch, 4, 2))
    opt = build_optimizer(torch, net, Settings(lr=1e-4))
    rows = [(g["scale"], g["weight_decay"], {p.ndim for p in g["params"]}) for g in opt.param_groups]
    assert rows == [(1.0, 0.0, {1, 2}), (10.0, 0.01, {2}), (10.0, 0.0, {1})]
    assert opt.param_groups[0]["betas"] == (0.9, 0.95)
