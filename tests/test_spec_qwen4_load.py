"""A Qwen4-Exp checkpoint on disk, loaded the way `Engine` loads Flash-Next.

The checkpoint is a tiny random ``qwen4_exp`` with a 4-bit PLE n-gram table, written as a
sharded Hub download is: safetensors, an index and a config. The load reads the table from
disk through a hard-linked view instead of holding it, and decodes as the resident model does.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

mx = pytest.importorskip("mlx.core", reason="poolhouse[spec]")
pytest.importorskip("mlx_vlm.models.qwen4_exp", reason="poolhouse[spec]")
pytest.importorskip("tokenizers", reason="pip install tokenizers")

import mlx.nn as nn  # noqa: E402
import numpy as np  # noqa: E402
from mlx.utils import tree_flatten  # noqa: E402
from mlx_vlm.models.qwen4_exp import Model, ModelConfig, ple_storage  # noqa: E402
from mlx_vlm.utils import load_model  # noqa: E402
from test_spec_qwen4 import plain_greedy, tiny_flash  # noqa: E402
from tokenizers import Tokenizer, decoders, models, pre_tokenizers  # noqa: E402

from poolhouse.serve.backend import ServerSpec  # noqa: E402
from poolhouse.serve.mlx_tree import report_for  # noqa: E402
from poolhouse.spec import ple  # noqa: E402
from poolhouse.spec.decode import Asked, decode  # noqa: E402
from poolhouse.spec.engine import Engine, EngineConfig, load_target  # noqa: E402

EOS = 96
PLE = ".ple.ple_embedding.ngram_embedding."
TEMPLATE = ("{% for m in messages %}<|im_start|> {{ m['content'] }} <|im_end|> {% endfor %}"
            "{% if add_generation_prompt %}<|im_start|>{% endif %}")
VISION = {"depth": 1, "hidden_size": 16, "intermediate_size": 32, "num_heads": 2,
          "out_hidden_size": 64, "patch_size": 2, "spatial_merge_size": 1,
          "temporal_patch_size": 1, "num_position_embeddings": 16,
          "deepstack_visual_indexes": []}


@pytest.fixture(scope="module")
def checkpoint(tmp_path_factory: pytest.TempPathFactory) -> Path:
    where = tmp_path_factory.mktemp("tiny-flash")
    text = {**vars(tiny_flash().args), "ple_embed_dim": 512}
    raw = {"model_type": "qwen4_exp", "text_config": text, "vision_config": VISION,
           "eos_token_id": EOS}
    model = Model(ModelConfig.from_dict(raw))
    model.set_dtype(mx.bfloat16)
    nn.quantize(model, group_size=32, bits=4,
                class_predicate=lambda path, module: PLE[:-1] in path + ".")
    mx.eval(model.parameters())
    params = {name: value if PLE in name or not mx.issubdtype(value.dtype, mx.floating)
              else value.astype(mx.float32)
              for name, value in tree_flatten(model.parameters())}
    mx.save_safetensors(str(where / "model.safetensors"), params, metadata={"format": "mlx"})
    (where / "model.safetensors.index.json").write_text(json.dumps(
        {"metadata": {}, "weight_map": dict.fromkeys(params, "model.safetensors")}))
    raw["quantization"] = {"group_size": 32, "bits": 4, "mode": "affine"}
    (where / "config.json").write_text(json.dumps(raw))
    vocab = {f"w{i}": i for i in range(94)}
    vocab.update({"<|im_start|>": 94, "<|im_end|>": 95, "</think>": 96})
    tokenizer = Tokenizer(models.WordLevel(vocab, unk_token="w0"))
    tokenizer.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    tokenizer.decoder = decoders.WordPiece(prefix="##")
    tokenizer.save(str(where / "tokenizer.json"))
    (where / "tokenizer_config.json").write_text(json.dumps({
        "tokenizer_class": "PreTrainedTokenizerFast", "eos_token": "<|im_end|>",
        "chat_template": TEMPLATE}))
    return where


@pytest.fixture
def cache_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("POOLHOUSE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "home"))
    return tmp_path


def test_the_loaded_model_reads_its_ngram_table_from_disk_and_decodes_as_the_resident_one(
        checkpoint: Path, cache_home: Path) -> None:
    resident = load_model(checkpoint).language_model
    model, tokenizer = load_target(checkpoint)
    text = model.language_model
    table = text.model.layers[1].ple.ple_embedding.ngram_embedding
    assert not any(PLE in name for name, _ in tree_flatten(text.parameters()))
    assert type(table) is ple.ReadAhead
    prompt = [(7 * i + 2) % 90 for i in range(24)]
    assert table(mx.array([[3, 5]])).shape == (1, 2, 32)
    assert plain_greedy(text, prompt, 24) == plain_greedy(resident, prompt, 24)
    assert EOS in tokenizer.eos_token_ids


def test_a_default_load_writes_no_one_record_table(
        checkpoint: Path, cache_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ple, "SPARE", 0)  # room is not what keeps the store unwritten
    engine = Engine(EngineConfig(model=str(checkpoint), drafter="none"))
    assert not list(cache_home.rglob(ple.ROWS_FILE))
    table = engine.model.language_model.model.layers[1].ple.ple_embedding.ngram_embedding
    assert type(table) is ple.ReadAhead and table._interleaved is None


def test_a_load_asked_for_it_writes_the_one_record_table_and_decodes_the_same(
        checkpoint: Path, cache_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ple, "SPARE", 0)
    engine = Engine(EngineConfig(model=str(checkpoint), drafter="none", ngram_rows=True))
    assert len(list(cache_home.rglob(ple.ROWS_FILE))) == 1
    text = engine.model.language_model
    assert text.model.layers[1].ple.ple_embedding.ngram_embedding._interleaved is not None
    prompt = [(7 * i + 2) % 90 for i in range(24)]
    out = decode(engine.session, prompt, Asked(24))
    assert out.tokens == plain_greedy(load_model(checkpoint).language_model, prompt, 24)


def test_a_tree_engine_on_the_checkpoint_writes_what_plain_greedy_decoding_writes(
        checkpoint: Path, cache_home: Path) -> None:
    engine = Engine(EngineConfig(model=str(checkpoint), drafter="ngram", max_nodes=8))
    prompt = [(5 * i + 3) % 90 for i in range(30)]
    out = decode(engine.session, prompt, Asked(32))
    assert out.tokens == plain_greedy(engine.model.language_model, prompt, 32)


def test_the_fit_charges_the_weights_a_load_holds_and_not_the_mapped_table(
        checkpoint: Path, cache_home: Path) -> None:
    report = report_for(ServerSpec(model=f"mlx:{checkpoint}", draft="ngram"))
    held = sum(v.nbytes for k, v in mx.load(str(checkpoint / "model.safetensors")).items()
               if PLE not in k)
    assert report.ok
    assert report.weights_bytes == held


# The n-gram table's rewrite and read-ahead gather, through mlx-vlm's own storage class.
# PLUMBING ONLY: the table is a few thousand rows of random bytes. It says nothing about a model
# and times no disk; the measured costs are in docs/serving.md.

SHARDS, ROWS, WIDTH = 3, 1500, 64


@pytest.fixture
def source(tmp_path: Path) -> Path:
    where = tmp_path / "plumbing-only-table"
    where.mkdir()
    rng = np.random.default_rng(3)
    tensors = {}
    for shard in range(SHARDS):
        key = f"model{PLE}shards.{shard}"
        words = rng.integers(0, 2**32, (ROWS, WIDTH // 8), dtype=np.uint32)
        tensors[f"{key}.weight"] = mx.array(words)
        for name in ("scales", "biases"):
            values = rng.random((ROWS, WIDTH // 32)).astype(np.float32)
            tensors[f"{key}.{name}"] = mx.array(values).astype(mx.bfloat16)
    mx.save_safetensors(str(where / "model.safetensors"), tensors)
    (where / "model.safetensors.index.json").write_text(json.dumps(
        {"weight_map": dict.fromkeys(tensors, "model.safetensors")}))
    (where / "config.json").write_text(json.dumps(
        {"quantization": {"bits": 4, "group_size": 32, "mode": "affine"}}))
    return where


@pytest.fixture
def view(source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(ple, "SPARE", 0)
    where = tmp_path / "view"
    ple_storage.build_quantized_ple_manifest(source, where / ple.MANIFEST)
    return where


def opened(view: Path, cache_rows: int = 0) -> ple_storage.QuantizedMMapNGramEmbedding:
    return ple_storage.QuantizedMMapNGramEmbedding(view / ple.MANIFEST, cache_rows=cache_rows)


def wanted() -> mx.array:
    """More than `ple.FEW` distinct rows from every shard, the edges and repeats among them."""
    ids = np.random.default_rng(11).integers(0, SHARDS * ROWS, 4 * ple.FEW)
    edges = [0, ROWS - 1, ROWS, 2 * ROWS - 1, 2 * ROWS, SHARDS * ROWS - 1]
    return mx.array(np.concatenate([ids, edges, ids[:40]]).reshape(-1, 2))


def same(a: mx.array, b: mx.array) -> bool:
    return a.shape == b.shape and bool(mx.array_equal(a, b))


@pytest.mark.parametrize("layout", ["ranges", "interleaved"])
def test_the_read_ahead_table_returns_what_the_plain_one_returns(
        source: Path, view: Path, layout: str) -> None:
    plain = opened(view)(wanted())
    if layout == "interleaved":
        ple.interleave(source, view)
    assert same(ple.ReadAhead.of(opened(view))(wanted()), plain)


@pytest.mark.parametrize("layout", ["ranges", "interleaved"])
def test_every_wanted_row_is_touched_before_the_gather_and_none_outside_the_table(
        source: Path, view: Path, layout: str, monkeypatch: pytest.MonkeyPatch) -> None:
    if layout == "interleaved":
        ple.interleave(source, view)
    table = ple.ReadAhead.of(opened(view))
    touched: list[int] = []
    order: list[str] = []
    real_touch, real_rows = table._touch, ple_storage.QuantizedMMapNGramEmbedding._read_rows

    def touch(ids: np.ndarray) -> None:
        real_touch(ids)  # raises if a shard is indexed outside itself
        touched.extend(ids.tolist())
        order.append("touch")

    def rows(self: object, ids: np.ndarray) -> object:
        order.append("gather")
        return real_rows(self, ids)

    monkeypatch.setattr(table, "_touch", touch)
    monkeypatch.setattr(ple_storage.QuantizedMMapNGramEmbedding, "_read_rows", rows)
    table(wanted())
    assert sorted(touched) == np.unique(np.asarray(wanted())).tolist()
    assert order[-1] == "gather" and order.count("gather") == 1 and "touch" in order


def test_a_few_rows_are_gathered_on_the_calling_thread(
        view: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    table = ple.ReadAhead.of(opened(view))
    monkeypatch.setattr(table, "_touch", lambda ids: pytest.fail("read ahead for a few rows"))
    assert same(table(mx.array([[1, ROWS + 2]])), opened(view)(mx.array([[1, ROWS + 2]])))


def test_it_keeps_the_row_cache_the_table_was_opened_with(view: Path) -> None:
    table = ple.ReadAhead.of(opened(view, cache_rows=10_000))
    table(wanted())
    table(wanted())
    distinct = np.unique(np.asarray(wanted())).size
    assert table.cache_rows == 10_000 and table.stats.cache_misses == distinct


def test_a_row_outside_the_table_is_still_refused(view: Path) -> None:
    with pytest.raises(IndexError):
        ple.ReadAhead.of(opened(view))(mx.array([SHARDS * ROWS]))


def test_a_table_with_room_is_rewritten_once_as_one_record_a_row(source: Path, view: Path) -> None:
    ple.interleave(source, view)
    manifest = json.loads((view / ple.MANIFEST).read_text())
    assert manifest["layout"] == "interleaved_q4" and manifest["data_file"] == ple.ROWS_FILE
    stride = WIDTH // 2 + 2 * (WIDTH // 32) * 2
    assert (view / ple.ROWS_FILE).stat().st_size == SHARDS * ROWS * stride
    before = (view / ple.ROWS_FILE).stat().st_mtime_ns
    ple.interleave(source, view)
    assert (view / ple.ROWS_FILE).stat().st_mtime_ns == before


def test_a_full_disk_refuses_the_rewrite_and_keeps_the_checkpoint_layout(
        source: Path, view: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: SimpleNamespace(free=1000))
    with pytest.raises(OSError, match="GB free"):
        ple.interleave(source, view)
    assert not (view / ple.ROWS_FILE).exists()
    assert json.loads((view / ple.MANIFEST).read_text())["layout"] == "safetensors_ranges"
    assert opened(view)(mx.array([3])).shape == (1, WIDTH)


def test_a_rows_file_an_interrupted_rewrite_left_is_written_again(
        source: Path, view: Path) -> None:
    plain = opened(view)(wanted())
    (view / ple.ROWS_FILE).write_bytes(b"half a table")
    ple.interleave(source, view)
    assert same(opened(view)(wanted()), plain)


def test_only_mapped_tables_in_a_model_are_given_the_read_ahead(view: Path) -> None:
    class Holder(nn.Module):
        def __init__(self, table: object) -> None:
            super().__init__()
            self.ngram_embedding = table

    class Two(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.mapped = Holder(opened(view))
            self.resident = Holder(nn.Embedding(8, 4))

    model = Two()
    assert ple.read_ahead(model) == 1
    assert type(model.mapped.ngram_embedding) is ple.ReadAhead
    assert isinstance(model.resident.ngram_embedding, nn.Embedding)
    assert ple.read_ahead(model) == 0
