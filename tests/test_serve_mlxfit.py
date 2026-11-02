"""An MLX model is fitted from its own config.json, as a GGUF is from its header: the window is the
ceiling, the machine's memory the floor, and a window the config does not report is refused by name."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from poolhouse.hub import remote
from poolhouse.hub.probe import GIB, MachineMemory
from poolhouse.serve import estimate as est, largest, mlxfit
from poolhouse.serve.backend import ServerSpec
from poolhouse.serve.fitted import fitted

WINDOW = 131072


def config(window: int | None = WINDOW, **text) -> dict:
    body = {"model_type": "qwen3", "hidden_size": 4096, "num_hidden_layers": 32, "num_attention_heads": 32,
            "num_key_value_heads": 8, "head_dim": 128, **text}
    if window is not None:
        body["max_position_embeddings"] = window
    return body


def folder(tmp_path, body: dict, weights_gib: float = 4, name: str = "model-4bit"):
    path = tmp_path / name
    path.mkdir()
    (path / "config.json").write_text(json.dumps(body), encoding="utf-8")
    weights = path / "model.safetensors"
    weights.write_bytes(b"")
    os.truncate(weights, int(weights_gib * GIB))
    return path


def mac(total_gib: int) -> MachineMemory:
    return MachineMemory(total_gib * GIB, total_gib * GIB // 2, True, total_gib * GIB * 3 // 4, (), "Darwin")


@pytest.fixture(autouse=True)
def no_limit(monkeypatch):
    monkeypatch.setattr("poolhouse.limits.read", lambda: SimpleNamespace(memory_bytes=0))


def holds(body: dict, context: int, machine: MachineMemory, weights_gib: float = 4, kv: str = "q8_0") -> bool:
    got = est.estimate_meta(mlxfit.header_of(body, "test"), int(weights_gib * GIB),
                            est.Setup(context=context, kv_cache_type=kv, flash_attn=True))
    return est.ORDER[est.verdict(got, largest.capacity(machine))] <= est.ORDER["yellow"]


def test_an_mlx_folder_is_fitted_from_its_config_and_one_step_more_would_not_hold(tmp_path):
    body, machine = config(), mac(16)
    spec, said = fitted(ServerSpec(model=str(folder(tmp_path, body)), cache_type_k="q8_0", cache_type_v="q8_0"), machine)
    assert 0 < spec.context < WINDOW and spec.context % largest.PAD == 0 and "tokens a slot" in said
    assert holds(body, spec.context, machine) and not holds(body, spec.context + largest.PAD, machine)


def test_the_window_in_the_config_is_the_ceiling_however_much_memory_there_is(tmp_path):
    spec, _ = fitted(ServerSpec(model=str(folder(tmp_path, config(window=8192), weights_gib=1))), mac(512))
    assert spec.context == 8192


def test_a_bigger_machine_and_a_narrower_cache_each_give_a_longer_context(tmp_path):
    path = str(folder(tmp_path, config()))
    wide = fitted(ServerSpec(model=path, cache_type_k="f16", cache_type_v="f16"), mac(16))[0].context
    narrow = fitted(ServerSpec(model=path, cache_type_k="q4_0", cache_type_v="q4_0"), mac(16))[0].context
    big = fitted(ServerSpec(model=path, cache_type_k="f16", cache_type_v="f16"), mac(64))[0].context
    assert wide < narrow and wide < big


def test_slots_share_the_memory_and_the_server_is_asked_for_the_total(tmp_path):
    path = str(folder(tmp_path, config()))
    one = fitted(ServerSpec(model=path, cache_type_k="q8_0", cache_type_v="q8_0"), mac(16))[0].context
    two = fitted(ServerSpec(model=path, parallel=2, cache_type_k="q8_0", cache_type_v="q8_0"), mac(16))[0].context
    assert 0 < two // 2 < one, "each of two slots gets less than the one slot did; the server is asked for both"


def test_the_text_config_of_a_multimodal_model_is_read(tmp_path):
    nested = {"model_type": "qwen3_vl", "text_config": config(window=32768)}
    spec, _ = fitted(ServerSpec(model=str(folder(tmp_path, nested, weights_gib=1))), mac(512))
    assert spec.context == 32768


def test_a_config_with_no_window_is_refused_naming_the_field(tmp_path):
    with pytest.raises(ValueError, match="max_position_embeddings"):
        fitted(ServerSpec(model=str(folder(tmp_path, config(window=None)))), mac(64))


def test_a_config_that_does_not_size_its_cache_is_refused_naming_what_is_missing(tmp_path):
    body = config()
    del body["num_key_value_heads"], body["head_dim"]
    del body["num_attention_heads"]
    with pytest.raises(ValueError, match="num_attention_heads"):
        fitted(ServerSpec(model=str(folder(tmp_path, body))), mac(64))


def test_a_model_not_yet_fetched_is_fitted_from_its_hub_config_before_anything_downloads(monkeypatch):
    asked = []
    monkeypatch.setattr(mlxfit.hub, "held_snapshot", lambda repo: None)
    monkeypatch.setattr(remote, "text_file", lambda repo, path, revision="main": asked.append((repo, path)) or json.dumps(config(window=65536)))
    monkeypatch.setattr(remote, "listing", lambda repo, *a, **k: [remote.RemoteFile("model.safetensors", 2 * GIB),
                                                                  remote.RemoteFile("README.md", 10)])
    spec, said = fitted(ServerSpec(model="mlx:maker/thing-4bit"), mac(512))
    assert asked == [("maker/thing-4bit", "config.json")] and spec.context == 65536 and "tokens a slot" in said


def test_a_hub_cache_snapshot_is_read_without_the_network(tmp_path, monkeypatch):
    snapshot = folder(tmp_path, config(window=16384), weights_gib=1, name="snapshot")
    monkeypatch.setattr(mlxfit.hub, "held_snapshot", lambda repo: snapshot)
    monkeypatch.setattr(remote, "text_file", lambda *a, **k: pytest.fail("the network was asked"))
    assert fitted(ServerSpec(model="mlx:maker/thing-4bit"), mac(512))[0].context == 16384


def test_a_model_whose_hub_config_cannot_be_read_says_its_window_is_not_known_until_fetched(monkeypatch):
    monkeypatch.setattr(mlxfit.hub, "held_snapshot", lambda repo: None)

    def down(*args, **kwargs):
        raise remote.RemoteError("the Hub did not answer")

    monkeypatch.setattr(remote, "text_file", down)
    with pytest.raises(ValueError, match="not known until it is fetched"):
        fitted(ServerSpec(model="mlx:maker/thing-4bit"), mac(64))


def test_a_gguf_not_yet_fetched_says_its_window_is_not_known_until_fetched(tmp_path):
    with pytest.raises(ValueError, match="not known until it is fetched"):
        fitted(ServerSpec(model=str(tmp_path / "absent.gguf")), mac(64))
