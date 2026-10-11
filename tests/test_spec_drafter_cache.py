"""The quantized drafters are made once and kept in the hub cache, by the repository and commit they came from."""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest

from poolhouse.hub import derived

pytestmark = pytest.mark.slow

COMMIT = "d" * 40
SHAPE = {"hidden_size": 64, "num_hidden_layers": 2, "num_attention_heads": 4, "num_key_value_heads": 2,
         "head_dim": 16, "intermediate_size": 128, "vocab_size": 97, "rms_norm_eps": 1e-6,
         "max_position_embeddings": 4096, "block_size": 7, "target_layer_ids": [1, 5], "mask_token_id": 95,
         "rope_theta": 10000.0}


@pytest.fixture
def ml():
    """mlx and the drafter modules, imported when a test needs them; the test is skipped without mlx."""
    mx = pytest.importorskip("mlx.core", reason="poolhouse[spec]")
    pytest.importorskip("mlx_vlm.models.qwen4_exp", reason="poolhouse[spec]")
    from mlx.utils import tree_flatten

    from poolhouse.spec.drafters import dflash, mtp_qwen4
    from poolhouse.spec.drafters.dflash_model import DFlashConfig, DFlashDraftModel
    from poolhouse.spec.qwen4 import Qwen4ExpLayout
    return SimpleNamespace(mx=mx, tree_flatten=tree_flatten, dflash=dflash, mtp=mtp_qwen4, config=DFlashConfig,
                           model=DFlashDraftModel, layout=Qwen4ExpLayout,
                           tiny=importlib.import_module("tests.test_spec_qwen4").tiny_flash)


@pytest.fixture(autouse=True)
def hub_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    return tmp_path / "hub"


@pytest.fixture
def drafter_folder(hub_cache, ml):
    """A DFlash drafter as the Hub cache holds a snapshot: a config and a weights file."""
    folder = hub_cache / "models--maker--drafter" / "snapshots" / COMMIT
    folder.mkdir(parents=True)
    (folder / "config.json").write_text(json.dumps(SHAPE))
    model = ml.model(ml.config(**{**SHAPE, "target_layer_ids": tuple(SHAPE["target_layer_ids"])}))
    model.set_dtype(ml.mx.float32)
    ml.mx.save_safetensors(str(folder / "model.safetensors"), dict(ml.tree_flatten(model.parameters())))
    return folder


def same(ml, one, other) -> bool:
    left, right = dict(ml.tree_flatten(one.parameters())), dict(ml.tree_flatten(other.parameters()))
    return left.keys() == right.keys() and all(ml.mx.array_equal(left[k], right[k]).item() for k in left)


def test_a_drafter_is_quantized_once_and_then_read_from_the_derived_repository(ml, drafter_folder, monkeypatch):
    first, _ = ml.dflash.load_dflash(drafter_folder)
    kept = derived.lookup(derived.source_of(drafter_folder), "mlx-q4-g64")
    assert kept is not None and "models--poolhouse-derived--maker.drafter.mlx-q4-g64" in str(kept)

    (drafter_folder / "model.safetensors").unlink()
    monkeypatch.setattr(ml.mx, "save_safetensors", lambda *a, **k: pytest.fail("quantized a second time"))
    second, _ = ml.dflash.load_dflash(drafter_folder)
    assert same(ml, first, second)


def test_a_drafter_with_no_quantization_keeps_nothing(ml, drafter_folder, hub_cache):
    ml.dflash.load_dflash(drafter_folder, bits=0)
    assert not list(hub_cache.glob("models--poolhouse-derived--*"))


def test_a_head_is_quantized_once_and_then_read_from_the_derived_repository(ml, hub_cache, monkeypatch):
    gguf = hub_cache / "models--maker--heads" / "snapshots" / COMMIT / "MTP" / "head.gguf"
    gguf.parent.mkdir(parents=True)
    gguf.write_bytes(b"GGUF")
    args = ml.layout(ml.tiny()).args
    monkeypatch.setattr(ml.mtp.nn, "quantize", lambda *a, **k: None)
    reference = ml.mtp.Qwen4MtpHead(args)
    monkeypatch.setattr(ml.mtp, "gguf_weights", lambda _path: dict(ml.tree_flatten(reference.parameters())))

    first = ml.mtp.load_gguf_head(gguf, args)
    assert derived.lookup(derived.source_of(gguf), "mlx-q4-g32") is not None
    monkeypatch.setattr(ml.mtp, "gguf_weights", lambda _path: pytest.fail("read the GGUF a second time"))
    second = ml.mtp.load_gguf_head(gguf, args)
    assert same(ml, first, second)
