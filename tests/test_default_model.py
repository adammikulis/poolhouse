"""The model a coding session or delegate gets when none is named.

Models are headers in ``tmp_path`` with a sparse tail for their weights, found by the real
installed-model discovery pointed at that folder; machines are `MachineMemory` values. No
model is loaded and no server is started: the answer is the library's own fit verdict.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from conftest import write_gguf

from poolhouse import delegate, harnessing, hub
from poolhouse.hub.probe import GIB, MachineMemory

FLASH = "Qwen3.8-Flash-Next-UD-IQ2_XS-00001-of-00003.gguf"


def mac(total_gib: int) -> MachineMemory:
    """Apple silicon: one pool, of which the GPU may wire three quarters (macOS's default)."""
    return MachineMemory(total_gib * GIB, total_gib * GIB // 2, True, total_gib * GIB * 3 // 4, (), "Darwin")


def model_file(folder, name: str, weights_gib: float):
    meta = {"general.architecture": "llama", "llama.block_count": 12, "llama.context_length": 262144,
            "llama.embedding_length": 2048, "llama.attention.head_count": 16, "llama.attention.head_count_kv": 2,
            "llama.attention.key_length": 128, "llama.attention.value_length": 128}
    path = write_gguf(folder / name, meta)
    os.truncate(path, int(weights_gib * GIB))
    return path


@pytest.fixture(autouse=True)
def installed_models_are_this_folder(monkeypatch, tmp_path):
    """Discovery looks in ``tmp_path/models`` only; the real caches are not read."""
    folder = tmp_path / "models"
    folder.mkdir()
    real = hub.discover
    monkeypatch.setattr(hub, "discover", lambda roots=None, **kw: real([folder], **kw))
    monkeypatch.setattr("poolhouse.limits.read", lambda: SimpleNamespace(memory_bytes=0))
    return folder


def test_the_preferred_model_is_the_default_when_it_is_installed_and_the_machine_holds_it_at_256k(installed_models_are_this_folder):
    model_file(installed_models_are_this_folder, FLASH, 40)
    model_file(installed_models_are_this_folder, harnessing.DEFAULT_MODEL, 17)
    assert harnessing.default_model(mac(128)) == FLASH


def test_a_machine_that_cannot_hold_it_gets_the_27b(installed_models_are_this_folder):
    model_file(installed_models_are_this_folder, FLASH, 40)
    assert harnessing.default_model(mac(32)) == harnessing.DEFAULT_MODEL


def test_without_it_installed_the_default_is_the_27b_and_a_larger_quant_is_never_taken(installed_models_are_this_folder):
    assert harnessing.default_model(mac(128)) == harnessing.DEFAULT_MODEL
    model_file(installed_models_are_this_folder, "Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf", 60)
    assert harnessing.default_model(mac(128)) == harnessing.DEFAULT_MODEL


def test_a_delegate_with_nothing_served_takes_the_same_default(installed_models_are_this_folder, monkeypatch):
    model_file(installed_models_are_this_folder, FLASH, 40)
    monkeypatch.setattr("poolhouse.serve.suggest.machine_memory", lambda: mac(128))
    assert delegate.standing_model() == FLASH
    assert delegate.standing_model("named-by-the-lead.gguf") == "named-by-the-lead.gguf"
