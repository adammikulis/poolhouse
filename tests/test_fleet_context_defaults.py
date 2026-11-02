"""The Fleet app's context and output defaults are answers, not numbers: the context is the
largest this machine holds for the model (`suggest.largest_fit`) and a reply may run to the
model's window. A person's own choice still wins."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from conftest import write_gguf

from poolhouse.fleet import conversation_settings, serving, setup_preparation, startup_models
from poolhouse.fleet.settings import Settings, apply_preferences
from poolhouse.hub.probe import GIB, Gpu, MachineMemory
from poolhouse.serve import estimate as est, largest, suggest

MAC = MachineMemory(64 * GIB, 32 * GIB, True, 48 * GIB, (), "Darwin")


@pytest.fixture(autouse=True)
def the_machine(monkeypatch):
    monkeypatch.setattr(suggest, "machine_memory", lambda: MAC)
    monkeypatch.setattr("poolhouse.limits.read", lambda: SimpleNamespace(memory_bytes=0))


@pytest.fixture
def model(tmp_path):
    path = write_gguf(tmp_path / "chat-Q4_K_M.gguf", {
        "general.architecture": "llama", "llama.block_count": 32, "llama.context_length": 131072,
        "llama.attention.head_count": 32, "llama.attention.head_count_kv": 8,
        "llama.attention.key_length": 128, "llama.attention.value_length": 128})
    os.truncate(path, 6 * GIB)
    return path


def lease_into(captured):
    return SimpleNamespace(lease=lambda spec, **_kw: captured.append(spec) or SimpleNamespace(port=23456))


def start(tmp_path, model, monkeypatch, settings=None):
    captured = []
    monkeypatch.setattr(serving, "estimate", lambda *_a, **_k: object())
    monkeypatch.setattr(serving, "verdict", lambda *_a: "green")
    monkeypatch.setattr(serving, "machine_memory", lambda: None)
    serving.start_model(tmp_path, model, settings=settings, manager=lease_into(captured))
    return captured[0]


def test_the_defaults_name_no_number_of_their_own():
    assert serving.ServeSettings().context == 0
    assert Settings().context == 0 and Settings().chat_max_output_tokens is None
    assert conversation_settings.effective(Settings())["max_output_tokens"] is None
    assert conversation_settings.effective(Settings())["max_effort"] == "high"


def test_a_model_started_with_no_context_gets_the_largest_fit(tmp_path, model, monkeypatch):
    spec = start(tmp_path, model, monkeypatch)
    want = suggest.largest_fit(model, MAC)
    assert spec.context == want.context > 8192 and spec.parallel == 1
    assert est.ORDER[want.verdict] <= est.ORDER["yellow"]


def test_slots_share_the_server_and_each_has_the_fitted_context(tmp_path, model, monkeypatch):
    spec = start(tmp_path, model, monkeypatch, serving.ServeSettings(parallel=2))
    each = suggest.largest_fit(model, MAC, slots=2).context
    assert spec.parallel == 2 and spec.context == 2 * each


def test_a_context_chosen_is_used_as_chosen(tmp_path, model, monkeypatch):
    assert start(tmp_path, model, monkeypatch, serving.ServeSettings(context=6144)).context == 6144


def test_a_model_whose_header_cannot_be_read_is_not_started_at_a_guess(tmp_path):
    absent = tmp_path / "absent.gguf"
    with pytest.raises(ValueError, match="choose one"):
        serving.fitted_settings(absent, serving.ServeSettings())


@pytest.mark.parametrize("asked,saved", [(0, 0), (-5, 0), (100, 100), (8192, 8192)])
def test_the_saved_preference_is_automatic_at_zero_and_a_number_otherwise(asked, saved):
    settings = Settings()
    assert apply_preferences(settings, {"context": asked}) == ""
    assert settings.context == saved


def test_a_download_is_offered_by_what_its_weights_need_not_by_a_context_of_ours():
    pick = next(p for p in startup_models.SUGGESTED if p.params_b == 27)
    assert startup_models.memory_need(pick) < startup_models.memory_need(pick, 262144)
    assert not hasattr(startup_models, "CONTEXT")


def test_the_offered_27b_reports_the_largest_context_its_card_holds():
    card = MachineMemory(64 * GIB, 64 * GIB, False, 0, (Gpu("card", 32 * GIB, 32 * GIB, "nvidia"),),
                         "Linux")
    row = next(r for r in startup_models.choices(machine=card)["models"] if r["params_b"] == 27)
    pick = next(p for p in startup_models.SUGGESTED if p.file == row["file"])
    reported = row["estimated_context"]
    assert 0 < reported <= 262144
    capacity = 32 * GIB * est.RED_AT
    assert startup_models.memory_need(pick, reported) < capacity
    if reported < 262144:
        assert startup_models.memory_need(pick, reported + largest.PAD) >= capacity


def test_an_installed_model_is_offered_at_its_fit_not_at_a_fixed_context(model):
    context = setup_preparation._fitted_context({"path": str(model)}, 131072)
    assert context == suggest.largest_fit(model, MAC).context


def test_an_unreadable_installed_model_claims_no_context(tmp_path):
    assert setup_preparation._fitted_context({"path": str(tmp_path / "x.gguf")}, 4096) == 0
