"""A machine with models kept outside the hub cache, as the owner's has them: the pieces ``poolhouse-models adopt`` takes in."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

from poolhouse.hub import hfstore
from poolhouse.testing.fakehub import FakeHub

VIEW = "mlx-community/Qwen3.8-Flash-Next-4bit"
BASE = "Qwen/Qwen3.5-2B-Base"
DECIDER = "StrandsAgents/strands-decider-2B-hobson-v19"
GEMMA = "google/gemma-4-E2B"
DFLASH = "PixelML/Qwen3.8-Flash-Next-NVFP4-DFlash"
HEADS = "unsloth/Qwen3.8-Flash-Next-GGUF"
DFLASH_COMMIT = "9cd660f9050c92fedc88cbe547bd53af0392abe1"
HEADS_COMMITS = ("38bb39ee97821de2c9009abb7e93950eec396e66", "5d16c055a7c5cb276e721ee154f9c22420dde2a1")
HEAD_FILE = "MTP/mtp-Qwen3.8-Flash-Next-shared-BF16.gguf"
PLE_SHARDS = (2, 3, 4, 5, 6)
SMALL_VIEW = ("README.md", "chat_template.jinja", "generation_config.json", "tokenizer.json", "vocab.json")


def safetensors(tensor: str, fill: int = 0, padding: int = 0) -> bytes:
    """A whole safetensors file with one tensor, so each file is different and reads as one."""
    head = json.dumps({tensor: {"dtype": "F32", "shape": [2], "data_offsets": [0, 8]}}).encode()
    return struct.pack("<Q", len(head)) + head + bytes([fill]) * 8 + bytes(padding)


def gguf() -> bytes:
    """A file that starts the way a GGUF does; the adoption of a drafter reads only its name."""
    return b"GGUF" + struct.pack("<IQQ", 3, 0, 0) + bytes(range(64))


def sha1(body: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(body) + body, usedforsecurity=False).hexdigest()


def record_of(body: bytes, *, etag: str = "") -> dict:
    """The record a verified download left beside its file."""
    digest = hashlib.sha256(body).hexdigest()
    return {"sha256": digest, "expected_sha256": digest, "size": len(body), "outcome": "kept",
            "headers": {"etag": f'"{etag}"'} if etag else {}}


def write(root: Path, files: dict[str, bytes], records: dict[str, dict] | None = None) -> Path:
    """``files`` under ``root``, each with the record in ``records`` beside it."""
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        if records and rel in records:
            path.with_name(path.name + ".provenance.json").write_text(json.dumps(records[rel]))
    return root


def lay_in_hub(repo: str, revision: str, files: dict[str, bytes], *, plain: frozenset[str] = frozenset(),
               shared: frozenset[str] = frozenset()) -> None:
    """``files`` as the hub cache's own tooling leaves them: a blob named by its digest and a snapshot link. A name in
    ``shared`` is a blob that is itself a link into the cache's shared blob store."""
    root = hfstore.repo_root(repo)
    for rel, body in files.items():
        name = sha1(body) if rel in plain else hashlib.sha256(body).hexdigest()
        blob = root / "blobs" / name
        blob.parent.mkdir(parents=True, exist_ok=True)
        if rel in shared:
            store = root.parent / "blobs" / name[:2] / hashlib.sha256(name.encode()).hexdigest()
            store.parent.mkdir(parents=True, exist_ok=True)
            store.write_bytes(body)
            blob.symlink_to(Path("..") / ".." / "blobs" / name[:2] / store.name)
        else:
            blob.write_bytes(body)
        hfstore.link(root / "snapshots" / revision / rel, blob)


def hub_files() -> dict[str, dict[str, bytes]]:
    """What the Hub holds of each repository the scenario names, ``{repo: {path: bytes}}``."""
    view = {f"model-{n:05d}-of-00022.safetensors": safetensors(f"t{n}", n) for n in range(1, 23)}
    view |= {name: f"{name} of the checkpoint".encode() for name in SMALL_VIEW}
    view["config.json"] = b'{"model_type": "qwen4_exp", "quantization": {"bits": 4}}'
    view["model.safetensors.index.json"] = json.dumps(
        {"weight_map": {f"t{n}": f"model-{n:05d}-of-00022.safetensors" for n in range(1, 23)}}).encode()
    view[".gitattributes"] = b"*.safetensors filter=lfs\n"
    return {
        VIEW: view,
        BASE: {"config.json": b'{"model_type": "qwen3_5"}', "model.safetensors.index.json": b'{"weight_map": {}}',
               "model.safetensors-00001-of-00001.safetensors": safetensors("base", 7, 4096)},
        DECIDER: {"hobson_config.json": b'{"head_type": "pointer"}', "head.safetensors": safetensors("head", 1),
                  "lora/adapter_config.json": b'{"r": 8}', "lora/adapter_model.safetensors": safetensors("lora", 2),
                  "tokenizer.json": b'{"version": "1.0"}', "tokenizer_config.json": b'{"added": []}'},
    }


def gemma_files() -> dict[str, bytes]:
    return {"config.json": b'{"model_type": "gemma4"}', "model.safetensors": safetensors("gemma", 9, 2048),
            "tokenizer.json": b'{"version": "1.0", "gemma": true}', "tokenizer_config.json": b'{"added": [1]}'}


def served(server_repos: dict[str, dict[str, bytes]], server: FakeHub) -> None:
    """Make the small files of the scenario's repositories plain (non-LFS) ones on the fake Hub."""
    for repo, files in server_repos.items():
        server.plain.update(f"{repo}/{rel}" for rel in files if not rel.endswith((".safetensors", ".gguf")))
