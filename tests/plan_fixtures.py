"""GGUF headers shaped like Qwen3.8-Flash-Next's: the real tensor names, shapes and types, with
no tensor data. Nothing here is a model; a shard is a header and, where a test needs the size of
a last tensor, a sparse tail."""

from __future__ import annotations

import struct
from pathlib import Path

from conftest import write_gguf

F32, BF16, Q4_K, Q6_K, Q8_0, IQ4_NL = 0, 30, 12, 14, 8, 20
UNKNOWN = 42
# ggml block sizes: (values per block, bytes per block)
BLOCK = {F32: (1, 4), BF16: (1, 2), Q4_K: (256, 144), Q6_K: (256, 210), Q8_0: (32, 34),
         IQ4_NL: (32, 18)}
EMBED, FF, LAYERS, VOCAB, ROWS = 2560, 640, 48, 248320, 320001536

Entry = tuple[str, int, tuple[int, ...], int]


def sized(name: str, kind: int, shape: tuple[int, ...], size: int | None = None) -> Entry:
    """A tensor row; the size is the ggml size of its type unless one is given."""
    count = 1
    for dim in shape:
        count *= dim
    block, each = BLOCK.get(kind, (1, 0))
    return (name, kind, shape, size if size is not None else count // block * each)


def layer_tensors(il: int, experts: int, exps_type: int,
                  unknown: dict[str, int] | None = None) -> list[Entry]:
    """One layer's tensors by the names llama.cpp's qwen4exp loader reads."""
    unknown = unknown or {}
    p = f"blk.{il}."
    out = [sized(p + "ffn_gate_inp.weight", F32, (EMBED, experts)),
           sized(p + "ffn_gate_shexp.weight", Q8_0, (EMBED, FF)),
           sized(p + "ffn_up_shexp.weight", Q8_0, (EMBED, FF)),
           sized(p + "ffn_down_shexp.weight", Q8_0, (FF, EMBED)),
           sized(p + "hc_attn_down.weight", BF16, (10240, 320)),
           sized(p + "hc_attn_norm.weight", F32, (10240,))]
    for part, shape in (("gate", (EMBED, FF, experts)), ("up", (EMBED, FF, experts)),
                        ("down", (FF, EMBED, experts))):
        name = f"{p}ffn_{part}_exps.weight"
        key = f"ffn_{part}_exps"
        out.append(sized(name, UNKNOWN if key in unknown else exps_type, shape,
                         unknown.get(key)))
    if (il + 1) % 4 == 0:
        out += [sized(p + "attn_q.weight", Q6_K, (EMBED, 12288)),
                sized(p + "attn_k.weight", Q6_K, (EMBED, 512)),
                sized(p + "attn_v.weight", Q6_K, (EMBED, 512)),
                sized(p + "attn_output.weight", Q6_K, (6144, EMBED)),
                sized(p + "indexer.q_proj.weight", BF16, (EMBED, 512)),
                sized(p + "attn_q_norm.weight", F32, (256,))]
    else:
        out += [sized(p + "attn_qkv.weight", Q6_K, (EMBED, 10240)),
                sized(p + "attn_gate.weight", Q6_K, (EMBED, 6144)),
                sized(p + "ssm_out.weight", Q6_K, (6144, EMBED)),
                sized(p + "ssm_alpha.weight", BF16, (EMBED, 48)),
                sized(p + "ssm_a", F32, (48,))]
    return out


def global_tensors() -> list[Entry]:
    return [sized("token_embd.weight", Q4_K, (EMBED, VOCAB)),
            sized("output.weight", Q6_K, (EMBED, VOCAB)),
            sized("output_norm.weight", F32, (EMBED,))]


def table_tensor() -> Entry:
    return sized("per_layer_token_embd.weight", IQ4_NL, (160, ROWS))


def metadata(experts: int = 512, **extra: object) -> dict:
    return {"general.architecture": "qwen4exp", "qwen4exp.block_count": LAYERS,
            "qwen4exp.context_length": 262144,
            "qwen4exp.expert_count": experts, "qwen4exp.expert_used_count": 10,
            "qwen4exp.embedding_length": EMBED, "qwen4exp.attention.head_count": 24,
            "qwen4exp.attention.head_count_kv": 2, "qwen4exp.attention.key_length": 256,
            "qwen4exp.attention.value_length": 256, "qwen4exp.full_attention_interval": 4,
            "qwen4exp.attention.indexer.key_length": 128, "qwen4exp.ssm.conv_kernel": 4,
            "qwen4exp.ssm.inner_size": 6144, "qwen4exp.ssm.state_size": 128,
            "qwen4exp.ssm.group_count": 16, "qwen4exp.ple.ngram_size": 3,
            "qwen4exp.ple.heads_per_ngram": 8,
            "qwen4exp.ple.head_vocab_sizes": [20000003 + 20 * i for i in range(16)], **extra}


def write_shard(path: Path, meta: dict, entries: list[Entry], *, fill: bool = False,
                align: int = 32) -> Path:
    """A GGUF with this metadata and tensor table, offsets laid end to end. ``fill`` extends
    the file to the end of the last tensor's data (sparse)."""
    write_gguf(path, meta, tensor_count=len(entries))
    table, offset = b"", 0
    for name, kind, shape, size in entries:
        raw = name.encode()
        table += struct.pack("<Q", len(raw)) + raw + struct.pack("<I", len(shape))
        table += b"".join(struct.pack("<Q", d) for d in shape)
        table += struct.pack("<IQ", kind, offset)
        offset += -(-size // align) * align
    with path.open("ab") as handle:
        handle.write(table)
    start = -(-path.stat().st_size // align) * align
    with path.open("ab") as handle:
        handle.write(b"\0" * (start - path.stat().st_size))
        if fill:
            handle.truncate(start + offset)
    return path


def flash_next(root: Path, *, experts: int = 512, exps_type: int = Q4_K, shards: int = 1,
               unknown: dict[int, dict[str, int]] | None = None) -> Path:
    """The first shard of a model laid out like Flash-Next. With two shards the n-gram table
    is alone in the second, as in the ISTA-DASLab release."""
    body = global_tensors()
    for il in range(LAYERS):
        body += layer_tensors(il, experts, exps_type, (unknown or {}).get(il))
    meta = metadata(experts)
    if shards == 1:
        return write_shard(root / "flash.gguf", meta, [*body, table_tensor()])
    first = root / "flash-00001-of-00002.gguf"
    write_shard(first, {**meta, "split.no": 0, "split.count": 2}, body)
    write_shard(root / "flash-00002-of-00002.gguf", {"split.no": 1, "split.count": 2},
                [table_tensor()])
    return first
