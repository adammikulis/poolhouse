"""The one answer behind every serving default: the largest safe context for a model on a machine.

Models are headers in ``tmp_path`` with a sparse tail for their weights; machines are
`MachineMemory` values. Expected numbers are checked against `estimate`, not read back from
the code under test: the context a fit chooses must rate within its level, and one step more
must not.
"""

from __future__ import annotations

import os
from dataclasses import replace
from types import SimpleNamespace

import pytest
from conftest import write_gguf

from poolhouse.hub.probe import GIB, Gpu, MachineMemory
from poolhouse.serve import estimate as est, largest, lifecycle_cli, suggest
from poolhouse.serve.backend import ServerSpec
from poolhouse.serve.fitted import fitted

TRAINED = 131072


def header(train: int = TRAINED, layers: int = 32, kv_heads: int = 8, dim: int = 128) -> dict:
    return {"general.architecture": "llama", "llama.block_count": layers,
            "llama.context_length": train, "llama.embedding_length": 4096,
            "llama.attention.head_count": 32, "llama.attention.head_count_kv": kv_heads,
            "llama.attention.key_length": dim, "llama.attention.value_length": dim}


def model_file(tmp_path, weights_gib: float, name: str = "model-Q4_K_M.gguf", **meta) -> object:
    path = write_gguf(tmp_path / name, header(**meta))
    os.truncate(path, int(weights_gib * GIB))
    return path


def mac(total_gib: int) -> MachineMemory:
    """Apple silicon: one pool, of which the GPU may wire three quarters (macOS's default)."""
    return MachineMemory(total_gib * GIB, total_gib * GIB // 2, True, total_gib * GIB * 3 // 4,
                         (), "Darwin")


def within(path, context: int, machine: MachineMemory, slots: int = 1,
           level: str = "yellow") -> bool:
    got = est.estimate(path, est.Setup(context=context, parallel=slots, flash_attn=True))
    return est.ORDER[est.verdict(got, largest.capacity(machine))] <= est.ORDER[level]


@pytest.fixture(autouse=True)
def no_limit(monkeypatch):
    monkeypatch.setattr("poolhouse.limits.read", lambda: SimpleNamespace(memory_bytes=0))


@pytest.mark.parametrize("total", [16, 32, 64, 128])
def test_the_context_is_the_largest_that_holds_on_each_size_of_machine(tmp_path, total):
    path = model_file(tmp_path, 4)
    machine = mac(total)
    got = suggest.largest_fit(path, machine)
    assert got.parallel == 1 and 0 < got.context <= TRAINED
    assert within(path, got.context, machine)
    if got.context < TRAINED:
        assert got.context % largest.PAD == 0
        assert not within(path, got.context + largest.PAD, machine)


def test_a_bigger_machine_gets_a_longer_context_until_the_trained_window(tmp_path):
    path = model_file(tmp_path, 4)
    seen = [suggest.largest_fit(path, mac(total)).context for total in (16, 32, 64, 128)]
    assert seen == sorted(seen) and seen[0] < seen[-1] == TRAINED


def test_the_trained_window_is_the_ceiling_however_much_memory_there_is(tmp_path):
    path = model_file(tmp_path, 1, train=8192)
    assert suggest.largest_fit(path, mac(512)).context == 8192


def test_a_header_with_no_context_length_is_refused_naming_the_field(tmp_path):
    meta = header()
    del meta["llama.context_length"]
    path = write_gguf(tmp_path / "none-Q4_K_M.gguf", meta)
    os.truncate(path, GIB)
    with pytest.raises(ValueError, match=r"llama\.context_length"):
        suggest.largest_fit(path, mac(512))
    with pytest.raises(ValueError, match=r"llama\.context_length"):
        fitted(ServerSpec(model=str(path)), mac(512))


def test_the_answer_does_not_depend_on_what_else_is_running(tmp_path):
    path = model_file(tmp_path, 4)
    machine = mac(64)
    busy = replace(machine, available_ram=GIB)
    assert suggest.largest_fit(path, busy).context == suggest.largest_fit(path, machine).context


def test_a_discrete_card_is_counted_whole_not_as_free_now(tmp_path):
    path = model_file(tmp_path, 4)
    card = MachineMemory(64 * GIB, 8 * GIB, False, 0, (Gpu("card", 24 * GIB, 2 * GIB, "nvidia"),),
                         "Linux")
    empty = replace(card, gpus=(Gpu("card", 24 * GIB, 24 * GIB, "nvidia"),))
    assert suggest.largest_fit(path, card).context == suggest.largest_fit(path, empty).context


def test_the_limit_poolhouse_was_given_is_honoured(tmp_path, monkeypatch):
    path = model_file(tmp_path, 4)
    free = suggest.largest_fit(path, mac(128)).context
    monkeypatch.setattr("poolhouse.limits.read", lambda: SimpleNamespace(memory_bytes=8 * GIB))
    held = suggest.largest_fit(path, mac(128)).context
    assert held < free == TRAINED


def test_more_slots_each_get_a_context_that_still_holds_together(tmp_path):
    path = model_file(tmp_path, 4)
    machine = mac(32)
    one = suggest.largest_fit(path, machine)
    three = suggest.largest_fit(path, machine, slots=3)
    assert three.parallel == 3 and 0 < three.context < one.context
    assert within(path, three.context, machine, slots=3)
    assert not within(path, three.context + largest.PAD, machine, slots=3)
    assert three.lease()["context"] == three.context * 3 and three.lease()["parallel"] == 3


def test_slots_that_fit_is_the_inverse_question(tmp_path):
    path = model_file(tmp_path, 4)
    machine = mac(64)
    most = suggest.slots_that_fit(path, 32768, machine)
    assert most > 1
    assert within(path, 32768, machine, slots=most)
    assert not within(path, 32768, machine, slots=most + 1)
    assert suggest.slots_that_fit(path, TRAINED, mac(4)) == 0


def test_a_draft_head_beside_the_model_takes_its_share_of_the_memory(tmp_path):
    path = model_file(tmp_path, 4)
    head = write_gguf(tmp_path / "head.gguf", {**header(layers=1, train=TRAINED),
                                               "llama.nextn_predict_layers": 1})
    os.truncate(head, 6 * GIB)
    machine = mac(24)
    assert (suggest.largest_fit(path, machine, draft=head).context
            < suggest.largest_fit(path, machine).context)


def test_nothing_that_fits_is_said_not_served_at_a_token_count(tmp_path):
    got = suggest.largest_fit(model_file(tmp_path, 40), mac(16))
    assert got.verdict == "red" and "Nothing fits" in got.reasons[0]


# -- what the defaults ask ---------------------------------------------------------------

def test_a_server_spec_asks_for_no_context_of_its_own():
    assert ServerSpec(model="m.gguf").context == 0 and ServerSpec(model="m.gguf").parallel == 1
    assert lifecycle_cli.DEFAULT_CONTEXT == 0


def test_a_spec_with_no_context_is_fitted_and_one_with_a_context_is_left(tmp_path):
    path = model_file(tmp_path, 4)
    machine = mac(32)
    spec, said = fitted(ServerSpec(model=str(path)), machine)
    assert spec.context == suggest.largest_fit(path, machine).context and "tokens a slot" in said
    spec, said = fitted(ServerSpec(model=str(path), parallel=2), machine)
    assert spec.context == 2 * suggest.largest_fit(path, machine, slots=2).context
    kept, said = fitted(ServerSpec(model=str(path), context=1234), machine)
    assert kept.context == 1234 and said == ""


def test_a_model_with_no_header_to_fit_is_refused_not_given_a_number(tmp_path):
    with pytest.raises(ValueError, match="not a GGUF file"):
        fitted(ServerSpec(model=str(tmp_path / "absent.gguf"), parallel=3))
    bad = tmp_path / "bad.gguf"
    bad.write_bytes(b"not a gguf at all")
    with pytest.raises(ValueError):
        fitted(ServerSpec(model=str(bad)))
    kept, _ = fitted(ServerSpec(model=str(bad), context=2048))
    assert kept.context == 2048


def test_up_with_no_context_serves_the_fit_and_one_given_wins(tmp_path, monkeypatch):
    from poolhouse.serve import ops
    from poolhouse.serve.cli import COMMANDS

    path = model_file(tmp_path, 4)
    machine = mac(48)
    leased = []
    monkeypatch.setattr(ops, "manager_for", lambda *_: SimpleNamespace())
    monkeypatch.setattr(lifecycle_cli, "_lease", lambda args, spec, *a, **k: leased.append(spec) or 0)
    monkeypatch.setattr("poolhouse.serve.suggest.machine_memory", lambda: machine)
    for more in ([], ["--context", "32768"]):
        args = COMMANDS.parser().parse_args(["up", str(path), "--no-profile", *more])
        assert lifecycle_cli.cmd_up(args) == 0
    assert leased[0].context == suggest.largest_fit(path, machine).context > 32768
    assert leased[1].context == 32768


def test_the_manager_answers_a_context_of_zero_before_anything_reads_it(tmp_path, monkeypatch):
    from poolhouse.serve import manager as manager_mod

    path = model_file(tmp_path, 4)
    seen = []
    monkeypatch.setattr("poolhouse.serve.suggest.machine_memory", lambda: mac(48))
    broker = SimpleNamespace(start=lambda spec, *a, **k: seen.append(spec) or SimpleNamespace(
        lease="", port=1))
    manager = manager_mod.ServerManager(broker=broker)
    manager.lease(ServerSpec(model=str(path)))
    assert seen[0].context == suggest.largest_fit(path, mac(48)).context > 0


def test_a_spec_never_fitted_tells_the_server_nothing_about_its_context(tmp_path):
    from poolhouse.serve.backend import LlamaServerBackend

    path = model_file(tmp_path, 1)
    assert "-c" not in LlamaServerBackend._model_source_argv(ServerSpec(model=str(path)))
    argv = LlamaServerBackend._model_source_argv(ServerSpec(model=str(path), context=8192))
    assert argv[argv.index("-c") + 1] == "8192"
