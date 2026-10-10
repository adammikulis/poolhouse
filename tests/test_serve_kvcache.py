"""The KV cache is 8 bit (q8_0) for K and V unless a spec, a profile or a person says otherwise.

Everything here is argv, arithmetic over a hand-written GGUF header, and records in a lease
file: no server is started and no GPU is touched.
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import pytest
from conftest import fake_binary, write_gguf

from poolhouse.serve import admission, cli, holding, kvcache, lifecycle_cli, ops
from poolhouse.serve.backend import LlamaServerBackend, ServerSpec
from poolhouse.serve.kvcache import DEFAULT_KV_CACHE, effective
from poolhouse.serve.mlx_tree import MlxTreeBackend, kv_bytes
from poolhouse.serve.preflight import draft_kv_estimate_bytes
from poolhouse.serve.profile import Profile
from poolhouse.testing import FakeLlamaServer, Served
from poolhouse.testing.registry import record_server

META = {"general.architecture": "llama", "llama.block_count": 8,
        "llama.attention.head_count_kv": 2, "llama.attention.head_count": 8,
        "llama.attention.key_length": 128, "llama.embedding_length": 1024}


def argv_of(tmp_path, spec):
    return LlamaServerBackend(binary=fake_binary(tmp_path)).command(spec)


def value(argv, flag):
    return argv[argv.index(flag) + 1]


class TestTheDefault:
    def test_one_constant_is_q8_0(self):
        assert DEFAULT_KV_CACHE == "q8_0"
        assert ServerSpec(model="m.gguf").cache_type_k == "q8_0"
        assert ServerSpec(model="m.gguf").cache_type_v == "q8_0"

    def test_argv_asks_for_q8_0_and_flash_attention(self, tmp_path):
        argv = argv_of(tmp_path, ServerSpec(model="m.gguf"))
        assert value(argv, "--cache-type-k") == "q8_0"
        assert value(argv, "--cache-type-v") == "q8_0"
        assert argv[argv.index("-fa") + 1] == "on"

    def test_a_named_type_wins_over_the_default(self, tmp_path):
        argv = argv_of(tmp_path, ServerSpec(model="m.gguf", cache_type_k="f16",
                                            cache_type_v="f16"))
        assert value(argv, "--cache-type-k") == "f16"
        assert value(argv, "--cache-type-v") == "f16"
        mixed = argv_of(tmp_path, ServerSpec(model="m.gguf", cache_type_k="q8_0",
                                             cache_type_v="q4_0"))
        assert (value(mixed, "--cache-type-k"), value(mixed, "--cache-type-v")) == ("q8_0", "q4_0")

    def test_the_draft_cache_follows_the_default_unless_set(self, tmp_path):
        argv = argv_of(tmp_path, ServerSpec(model="m.gguf", draft="d.gguf"))
        assert value(argv, "--spec-draft-type-k") == value(argv, "--spec-draft-type-v") == "q8_0"
        said = argv_of(tmp_path, ServerSpec(model="m.gguf", draft="d.gguf",
                                            spec_draft_type_k="f16", spec_draft_type_v="f16"))
        assert value(said, "--spec-draft-type-k") == value(said, "--spec-draft-type-v") == "f16"

    def test_no_draft_means_no_draft_cache_flags(self, tmp_path):
        assert "--spec-draft-type-k" not in argv_of(tmp_path, ServerSpec(model="m.gguf"))


class TestFallbacks:
    def test_without_flash_attention_v_stays_f16_and_says_why(self, tmp_path):
        spec = ServerSpec(model="m.gguf", flash_attn=False)
        argv = argv_of(tmp_path, spec)
        assert value(argv, "--cache-type-k") == "q8_0"
        assert value(argv, "--cache-type-v") == "f16"
        assert "-fa" not in argv
        assert "flash attention is off" in effective(spec).notes[0]

    def test_a_draft_v_cache_falls_back_the_same_way(self, tmp_path):
        argv = argv_of(tmp_path, ServerSpec(model="m.gguf", draft="d.gguf", flash_attn=False))
        assert value(argv, "--spec-draft-type-v") == "f16"
        assert value(argv, "--spec-draft-type-k") == "q8_0"

    def test_a_head_size_with_no_flash_kernel_keeps_v_f16(self, tmp_path):
        meta = {**META, "llama.attention.key_length": 576}
        model = write_gguf(tmp_path / "mla.gguf", meta)
        got = effective(ServerSpec(model=str(model)))
        assert (got.k, got.v) == ("q8_0", "f16")
        assert "head size" in got.notes[0]

    def test_a_head_size_with_a_kernel_keeps_q8_0(self, tmp_path):
        model = write_gguf(tmp_path / "plain.gguf", META)
        got = effective(ServerSpec(model=str(model)))
        assert (got.k, got.v, got.notes) == ("q8_0", "q8_0", ())

    def test_an_f16_v_needs_no_fallback(self, tmp_path):
        got = effective(ServerSpec(model="m.gguf", flash_attn=False, cache_type_v="f16"))
        assert got.notes == ()

    def test_an_embedding_server_passes_no_cache_type(self, tmp_path):
        spec = ServerSpec(model="m.gguf", embedding=True)
        argv = argv_of(tmp_path, spec)
        assert "--cache-type-k" not in argv and "--cache-type-v" not in argv
        assert effective(spec).notes


class TestAdmissionUsesTheRealType:
    def test_a_default_spec_is_charged_q8_0_not_f16(self, tmp_path):
        model = write_gguf(tmp_path / "m.gguf", META)
        context = 4096
        f16 = replace(ServerSpec(model=str(model), context=context),
                      cache_type_k="f16", cache_type_v="f16")
        default = ServerSpec(model=str(model), context=context)
        # 2 (K and V) x 8 layers x 2 kv heads x 128 wide x tokens
        per_element = 8 * 2 * 2 * 128 * context
        saved = admission.estimate_bytes(f16) - admission.estimate_bytes(default)
        assert saved == pytest.approx(per_element * 2.0 - per_element * 34 / 32, abs=2)

    def test_q8_0_is_34_bytes_a_32_elements(self, tmp_path):
        model = write_gguf(tmp_path / "m.gguf", META)
        q8 = admission.estimate_bytes(ServerSpec(model=str(model), context=4096))
        q4 = admission.estimate_bytes(ServerSpec(model=str(model), context=4096,
                                                 cache_type_k="q4_0", cache_type_v="q4_0"))
        elements = 8 * 2 * 2 * 128 * 4096
        assert q8 - q4 == pytest.approx(elements * (34 - 18) / 32, abs=2)

    def test_a_draft_cache_is_charged_q8_0_unless_set(self, tmp_path):
        head = write_gguf(tmp_path / "draft.gguf", {**META, "llama.block_count": 4})
        base = ServerSpec(model="m.gguf", draft=str(head), context=4096)
        full = draft_kv_estimate_bytes(replace(base, spec_draft_type_k="f16",
                                               spec_draft_type_v="f16"))
        assert draft_kv_estimate_bytes(base) == pytest.approx(full * 34 / 64, abs=2)

    def test_a_server_that_fell_back_is_shared_by_the_same_ask(self):
        spec = ServerSpec(model="m.gguf", flash_attn=False)
        served = {"cache_type_k": "q8_0", "cache_type_v": "f16"}
        assert admission.compatible(spec, served, [])
        assert not admission.compatible(spec, {**served, "cache_type_v": "q8_0"}, [])


class TestProfiles:
    def test_a_profile_pinning_f16_keeps_f16(self):
        served = Profile(model="thornfield-8B.gguf", cache_type="f16").serving(resolve=False)
        assert served.cache_type == "f16"
        assert served.lease()["cache_type_k"] == "f16"

    def test_a_profile_naming_nothing_gets_q8_0(self):
        served = Profile(model="thornfield-8B.gguf").serving(resolve=False)
        assert served.cache_type == "q8_0"
        assert served.lease()["cache_type_v"] == "q8_0"

    def test_up_takes_the_profiles_f16_and_otherwise_asks_for_the_default(self, monkeypatch):
        pinned = Profile(model="thornfield-8B.gguf", cache_type="f16", draft_cache_type="f16")
        monkeypatch.setattr(lifecycle_cli, "profile_for", lambda *a, **k: pinned)
        args = argparse.Namespace(kv="", draft_kv="", parallel=1, port=1,
                                  context=lifecycle_cli.DEFAULT_CONTEXT,
                                  build="", draft="", spec="", spec_n_max=None, spec_p_min=None,
                                  mmproj="", reasoning_budget=None)
        lifecycle_cli.from_profile(args, "thornfield-8B.gguf")
        assert args.kv == "f16" and args.draft_kv == "f16"
        spec = lifecycle_cli._asked_spec(args, "thornfield-8B.gguf", ())
        assert (spec.cache_type_k, spec.cache_type_v) == ("f16", "f16")
        bare = argparse.Namespace(kv="", draft_kv="", port=1, context=4096, parallel=1)
        spec = lifecycle_cli._asked_spec(bare, "m.gguf", ())
        assert (spec.cache_type_k, spec.cache_type_v) == ("q8_0", "q8_0")


class TestMlx:
    def test_the_default_is_eight_bits(self, tmp_path):
        weights = tmp_path / "w"
        weights.mkdir()
        (weights / "config.json").write_text("{}")
        got = effective(ServerSpec(model=str(weights)))
        assert got.bits == 8 and got.mlx and got.k == got.v == ""
        assert got.said == "8-bit (MLX)"

    def test_f16_is_no_quantisation(self, tmp_path):
        weights = tmp_path / "w"
        weights.mkdir()
        (weights / "config.json").write_text("{}")
        got = effective(ServerSpec(model=str(weights), cache_type_k="f16", cache_type_v="f16"))
        assert got.bits is None and got.said == "f16 (MLX)"

    def test_k_and_v_that_differ_use_the_higher_and_say_so(self):
        assert kvcache.mlx_bits("q8_0", "q4_0") == (8, "MLX has one width for K and V "
                                                      "together; using the higher, 8-bit")
        assert kvcache.mlx_bits("f16", "q4_0")[0] == 4

    def test_a_type_mlx_has_no_width_for_falls_back_to_f16(self, tmp_path):
        weights = tmp_path / "w"
        weights.mkdir()
        (weights / "config.json").write_text("{}")
        got = effective(ServerSpec(model=str(weights), cache_type_k="q3_K", cache_type_v="q3_K"))
        assert got.bits is None and "f16" in got.notes[0]

    def test_the_tree_server_is_told_the_bits_and_the_estimate_is_smaller(self, tmp_path):
        weights = tmp_path / "w"
        weights.mkdir()
        (weights / "config.json").write_text("{}")
        argv = MlxTreeBackend().command(ServerSpec(model=str(weights)))
        assert value(argv, "--kv-bits") == "8"
        argv = MlxTreeBackend().command(ServerSpec(model=str(weights), cache_type_k="f16",
                                                   cache_type_v="f16"))
        assert value(argv, "--kv-bits") == "0"
        config = {"num_hidden_layers": 8, "full_attention_interval": 4, "num_key_value_heads": 2,
                  "head_dim": 64}
        full, attention, layers = kv_bytes(config, 1000, None)
        eight, _, _ = kv_bytes(config, 1000, 8)
        assert (attention, layers) == (2, 8)
        # only the 2 attention layers: 2 (K and V) x 2 layers x 2 heads x 64 x 1000 tokens
        assert full == 2 * 2 * 2 * 64 * 1000 * 2
        assert eight == int(2 * 2 * 2 * 64 * 1000 * (1 + 4 / 64))


class TestWhatIsSaid:
    @pytest.fixture
    def state(self, tmp_path, monkeypatch):
        path = tmp_path / "servers.json"
        monkeypatch.setattr(ops, "lease_file", lambda: path)
        return path

    def test_status_names_the_cache_the_server_was_started_with(self, state, capsys):
        fake = FakeLlamaServer(Served(model="/models/m.gguf", context=4096))
        try:
            record_server(state, fake.port, model="m.gguf", cache_type_k="q8_0",
                          cache_type_v="f16", kv_cache="q8_0/f16",
                          kv_notes=["flash attention is off: the V cache stays f16"])
            assert cli.main(["status", "--port", str(fake.port)]) == 0
        finally:
            fake.close()
        out = capsys.readouterr().out
        assert "kv cache q8_0/f16" in out and "flash attention is off" in out

    def test_up_prints_the_cache_it_chose(self, state, tmp_path, monkeypatch, capsys):
        fake = FakeLlamaServer(Served(model="/models/m.gguf", context=4096))
        monkeypatch.setattr(holding, "up", lambda spec, **kw: holding.Hold(
            id="lease", shape="s", model=str(spec.model), context=spec.context,
            parallel=spec.parallel, status="ready", port=fake.port,
            base_url=f"http://127.0.0.1:{fake.port}", adopted=True))
        try:
            assert cli.main(["up", "m.gguf", "--port", str(fake.port)]) == 0
            assert cli.main(["up", "m.gguf", "--port", str(fake.port), "--kv", "f16"]) == 0
        finally:
            fake.close()
        out = capsys.readouterr().out
        assert "KV cache q8_0" in out and "KV cache f16" in out
