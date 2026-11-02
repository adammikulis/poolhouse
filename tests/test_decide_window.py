"""A decider prompt has no length limit of ours: the model's own window, read from its config, and
the memory free on the device are the only bounds, and each refusal says the measured reason."""

from __future__ import annotations

import json

import pytest

from poolhouse.decide import window
from poolhouse.decide.cases import Case
from poolhouse.decide.types import DecideError, options_of
from poolhouse.train.decider import encode

OPTS = options_of({"yes": "it is", "no": "it is not"})
REAL = {"model_type": "qwen3_5", "text_config": {
    "model_type": "qwen3_5_text", "max_position_embeddings": 262144, "hidden_size": 2048,
    "num_hidden_layers": 24, "num_attention_heads": 8}}


@pytest.fixture
def tokenizer():
    """A real fast tokenizer (the library the models use) with a one-token-per-word vocabulary."""
    tokenizers = pytest.importorskip("tokenizers")
    transformers = pytest.importorskip("transformers")
    from poolhouse.decide import pointer_prompt
    text = pointer_prompt.render("q", "word", OPTS).text
    vocab = {"[UNK]": 0}
    for found in tokenizers.pre_tokenizers.Whitespace().pre_tokenize_str(text + " word"):
        vocab.setdefault(found[0], len(vocab))
    tk = tokenizers.Tokenizer(tokenizers.models.WordLevel(vocab, unk_token="[UNK]"))
    tk.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    return transformers.PreTrainedTokenizerFast(tokenizer_object=tk, unk_token="[UNK]")


def test_the_window_is_read_from_the_models_own_config_text_config_first():
    dim = window.dimensions(REAL, "qwen config")
    assert (dim.window, dim.hidden, dim.layers, dim.heads) == (262144, 2048, 24, 8)
    assert window.dimensions({"max_position_embeddings": 32768, "hidden_size": 8,
                              "num_hidden_layers": 1, "num_attention_heads": 1}, "flat").window == 32768


@pytest.mark.parametrize("missing", ["max_position_embeddings", "hidden_size", "num_hidden_layers",
                                     "num_attention_heads"])
def test_a_model_that_reports_no_window_or_size_is_refused_by_the_missing_field(missing):
    text = {k: v for k, v in REAL["text_config"].items() if k != missing}
    with pytest.raises(DecideError, match=rf"my config has no usable {missing} .*no other number"):
        window.dimensions({"text_config": text}, "my config")


def test_a_directory_with_no_readable_config_is_refused_by_path(tmp_path):
    with pytest.raises(DecideError, match=r"config\.json cannot be read"):
        window.of_directory(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps(REAL))
    assert window.of_directory(tmp_path).window == 262144


def test_a_case_longer_than_anything_the_old_limits_allowed_encodes_with_a_real_tokenizer(tokenizer):
    dim = window.dimensions(REAL, "qwen config")
    case = Case("q", "word " * 6000, OPTS, "yes", id="six-thousand")
    row = encode(tokenizer, case, [0, 1], dim)
    assert len(row["ids"]) > 6000 and len(row["spots"]) == 2


def test_a_case_over_the_models_window_names_the_window_and_where_it_was_read(tokenizer):
    dim = window.Dimensions(window=5000, hidden=8, layers=1, heads=1, where="/models/base/config.json")
    case = Case("q", "word " * 6000, OPTS, "yes", id="six-thousand")
    with pytest.raises(DecideError, match=r"six-thousand.*context window is 5000.*max_position_embeddings in /models/base"):
        encode(tokenizer, case, [0, 1], dim)


def test_a_limit_the_person_sets_is_the_only_cap_below_the_window(tokenizer):
    dim = window.dimensions(REAL, "qwen config")
    case = Case("q", "word " * 600, OPTS, "yes", id="six-hundred")
    assert len(encode(tokenizer, case, [0, 1], dim)["ids"]) > 600
    with pytest.raises(DecideError, match=r"six-hundred.*max_tokens 300"):
        encode(tokenizer, case, [0, 1], dim, 300)


def test_memory_that_cannot_hold_a_prompt_is_refused_with_what_it_needs_and_what_is_free():
    huge = window.Dimensions(window=262144, hidden=2048, layers=24, heads=10 ** 9, where="x")
    with pytest.raises(DecideError, match=r"6000 tokens .*needs about [\d.]+ GiB.*[\d.]+ GiB is available on cpu"):
        window.require_room(huge, 6000, "cpu", "the prompt")


def test_a_prompt_that_fits_in_memory_reports_the_working_bytes_it_needs():
    dim = window.Dimensions(window=262144, hidden=64, layers=2, heads=2, where="x")
    need = window.require_room(dim, 6000, "cpu", "the prompt")
    assert need == window.working_bytes(dim, 6000) == 6000 ** 2 * 2 * 8 + 6000 * 64 * 2 * 16
