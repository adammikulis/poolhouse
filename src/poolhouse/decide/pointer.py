"""The pointer backend: a language-model torso with its output head removed and a small
pointer head that scores each option against the answer position.

One forward pass over a prompt that lists the options; the hidden state at ``<answer>`` is the
query and the hidden state at the end of each option's line is its key. Runs the released
checkpoints in `poolhouse.decide.pins`, or one trained by `poolhouse.train.decider`.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from poolhouse.decide import pointer_prompt
from poolhouse.decide.base import Asked, BaseDecider, Request, text_of
from poolhouse.decide.calibrate import Calibration
from poolhouse.decide.pins import STRANDS_V19, Checkpoint
from poolhouse.decide.sources import Source, local_source, strands_source, temperature_for
from poolhouse.decide.types import (
    BackendUnavailable,
    DecideError,
    Decision,
    Stamp,
    clip,
    decision_from,
    options_of,
)
from poolhouse.hub import admission

MAX_TOKENS = 4096


def _length_groups(lengths: list[int], spread: float = 1.3) -> list[list[int]]:
    """Indices grouped so that the longest prompt in a group is at most ``spread`` times the shortest: padding stays small."""
    order = sorted(range(len(lengths)), key=lambda i: lengths[i])
    groups: list[list[int]] = []
    for i in order:
        if groups and lengths[i] <= spread * lengths[groups[-1][0]]:
            groups[-1].append(i)
        else:
            groups.append([i])
    return groups


def _torch() -> Any:
    try:
        import torch
    except ImportError as exc:
        raise BackendUnavailable("the pointer backend needs torch, transformers and peft: "
                                 "pip install 'poolhouse[decide-pointer]'") from exc
    return torch


def device_name(requested: str = "auto") -> str:
    """``cuda`` or ``mps`` if present, else ``cpu``, unless a device is named."""
    if requested != "auto":
        return requested
    torch = _torch()
    if torch.cuda.is_available():
        return "cuda"
    return "mps" if torch.backends.mps.is_available() else "cpu"


def build_head(torch: Any, hidden: int, dim: int) -> Any:
    """The pointer readout: a shared LayerNorm, then a query and a key projection."""
    nn = torch.nn

    class Pointer(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.norm = nn.LayerNorm(hidden)
            self.q = nn.Linear(hidden, dim)
            self.k = nn.Linear(hidden, dim)
            self.scale = dim ** -0.5

        def forward(self, decide: Any, options: Any) -> Any:
            """decide [B, d], options [B, K, d] -> logits [B, K]."""
            q = self.q(self.norm(decide)).unsqueeze(-1)
            k = self.k(self.norm(options))
            return (k @ q).squeeze(-1) * self.scale

    return Pointer()


def load_torso(base_dir: Path, lora_dir: Path | None, dtype: str, device: str, *,
               trainable: bool = False) -> Any:
    """The base model's decoder with its adapter applied, from safetensors only."""
    admission.vetted(base_dir)
    torch = _torch()
    import transformers
    config = json.loads((base_dir / "config.json").read_text())
    if "auto_map" in config:
        raise DecideError(f"{base_dir}/config.json asks for custom code; it is not loaded")
    cfg = transformers.AutoConfig.from_pretrained(base_dir, local_files_only=True,
                                                  trust_remote_code=False)
    cls = (transformers.Qwen3_5ForCausalLM if cfg.model_type in ("qwen3_5", "qwen3_5_text")
           else None)
    if cls is None:
        torso = transformers.AutoModel.from_pretrained(
            base_dir, dtype=getattr(torch, dtype), local_files_only=True,
            trust_remote_code=False, use_safetensors=True)
    else:
        lm = cls.from_pretrained(base_dir, config=cfg.get_text_config(),
                                 dtype=getattr(torch, dtype), local_files_only=True,
                                 trust_remote_code=False, use_safetensors=True)
        torso = lm.model
    if lora_dir is not None:
        from peft import PeftModel
        torso = PeftModel.from_pretrained(torso, str(lora_dir), is_trainable=trainable,
                                          use_safetensors=True)
    torso = torso.to(device)
    return torso if trainable else torso.eval()


class PointerDecider(BaseDecider):
    """Runs a pointer-head checkpoint on this machine's best device.

    ``source`` is the pinned released checkpoint (default), another pinned `Checkpoint`, or
    the directory a training run wrote. Files load lazily on first use; nothing is fetched
    unless ``download`` is set.
    """

    name = "pointer"

    def __init__(self, source: Checkpoint | Source | Path | str = STRANDS_V19, *, device: str = "auto",
                 download: bool = False, calibration: Calibration | None = None, batched: bool = False) -> None:
        self.source = source
        self.batched = batched
        self.model = source.name if isinstance(source, (Checkpoint, Source)) else Path(source).name
        self.device_request = device
        self.download = download
        self.calibration = calibration
        self._lock = threading.Lock()
        self._loaded: tuple[Any, ...] | None = None

    def resolve(self) -> Source:
        """The verified local files."""
        if isinstance(self.source, Source):
            return self.source
        if isinstance(self.source, Checkpoint):
            return strands_source(self.source, download=self.download)
        return local_source(self.source, download=self.download)

    def load(self) -> None:
        """Load the tokenizer, torso and head if they are not loaded."""
        with self._lock:
            if self._loaded is not None:
                return
            torch = _torch()
            from safetensors.torch import load_file
            from transformers import AutoTokenizer
            files = self.resolve()
            device = device_name(self.device_request)
            tok = AutoTokenizer.from_pretrained(files.tokenizer_dir, local_files_only=True,
                                                trust_remote_code=False)
            torso = load_torso(files.base_dir, files.lora_dir, files.dtype, device)
            state = load_file(str(files.head))
            head = build_head(torch, state["q.weight"].shape[1], files.pointer_dim)
            head.load_state_dict(state)
            head = head.to(device).float().eval()
            self._loaded = (tok, torso, head, device, files,
                            {"device": device, "dtype": files.dtype})

    def _encode(self, asked: Asked) -> tuple[Any, list[int], float]:
        """The token ids of a prompt, the positions of its option keys and its temperature."""
        tok, files = self._loaded[0], self._loaded[4]
        rendered = pointer_prompt.render(asked.question, asked.state, asked.options)
        enc = tok(rendered.text, return_offsets_mapping=True, add_special_tokens=False, return_tensors="pt")
        length = int(enc["input_ids"].shape[1])
        if length > MAX_TOKENS:
            raise DecideError(f"the prompt is {length} tokens; the limit is {MAX_TOKENS}")
        spots = pointer_prompt.positions([tuple(o) for o in enc["offset_mapping"][0].tolist()], rendered.spans)
        return enc["input_ids"][0], spots, temperature_for(files, asked.question)

    def _probs(self, hidden: Any, spots: list[int], temperature: float) -> list[float]:
        head = self._loaded[2]
        torch = _torch()
        logits = head(hidden[-1:].float(), hidden[spots].float().unsqueeze(0))[0]
        return torch.softmax(logits / temperature, dim=-1).tolist()

    def probabilities(self, asked: Asked) -> tuple[list[float], dict[str, Any]]:
        self.load()
        torch = _torch()
        if self._loaded is None:
            raise DecideError("the pointer model did not load")
        torso, device, info = self._loaded[1], self._loaded[3], self._loaded[5]
        ids, spots, temperature = self._encode(asked)
        with self._lock, torch.inference_mode():
            hidden = torso(input_ids=ids.unsqueeze(0).to(device), attention_mask=torch.ones_like(ids).unsqueeze(0).to(device),
                           use_cache=False).last_hidden_state[0]
            probs = self._probs(hidden, spots, temperature)
        return probs, {"tokens": int(ids.shape[0]), **info}

    def decide_many(self, requests: Sequence[Request]) -> list[Decision]:
        """The decisions for ``requests``: one forward pass each, or with ``batched`` set one padded pass per group of
        similar-length prompts (answers within bf16 rounding of the one-by-one ones; not faster on MPS with the
        reference kernels)."""
        if not requests:
            return []
        if not self.batched:
            return super().decide_many(requests)
        self.load()
        torch = _torch()
        if self._loaded is None:
            raise DecideError("the pointer model did not load")
        torso, device, info = self._loaded[1], self._loaded[3], self._loaded[5]
        began = time.perf_counter()
        opts = [options_of(r.options, r.descriptions) for r in requests]
        asks = [Asked(clip(r.question.strip(), "question"), clip(text_of(r.state), "state"), r.state, o)
                for r, o in zip(requests, opts, strict=True)]
        encoded = [self._encode(a) for a in asks]
        rows: list[list[float] | None] = [None] * len(encoded)
        with self._lock, torch.inference_mode():
            for group in _length_groups([int(ids.shape[0]) for ids, _, _ in encoded]):
                longest = max(int(encoded[i][0].shape[0]) for i in group)
                pad = getattr(self._loaded[0], "pad_token_id", None) or 0
                batch = torch.full((len(group), longest), pad, dtype=encoded[0][0].dtype)
                mask = torch.zeros((len(group), longest), dtype=torch.long)
                for row, i in enumerate(group):
                    n = int(encoded[i][0].shape[0])
                    batch[row, :n] = encoded[i][0]
                    mask[row, :n] = 1
                hidden = torso(input_ids=batch.to(device), attention_mask=mask.to(device), use_cache=False).last_hidden_state
                for row, i in enumerate(group):
                    ids, spots, temp = encoded[i]
                    rows[i] = self._probs(hidden[row, :int(ids.shape[0])], spots, temp)
        total = (time.perf_counter() - began) * 1000.0
        out = []
        for r, o, probs, (ids, _, _) in zip(requests, opts, rows, encoded, strict=True):
            if probs is None:
                raise DecideError("a batched prompt was not scored")
            if self.calibration is not None:
                probs = self.calibration.apply(probs)
            stamp = Stamp(self.name, self.model, total / len(requests),
                          {"tokens": int(ids.shape[0]), "batch": len(requests), "batch_ms": round(total, 3), **info})
            out.append(decision_from(probs, o, abstain_below=r.abstain_below, stamp=stamp))
        return out
