"""Serving an MLX model with tree speculative decoding, behind the same lease as llama-server.

A model named ``mlx:owner/repo``, or a local directory of MLX weights, is served by
`MlxTreeBackend`: a Python process running `poolhouse.serve.mlx_tree_server` with the weights
and a drafter. The spec's ``draft`` names the drafter -- a DFlash or DSpark head, an MTP
head, ``ngram``, or nothing -- and ``spec_draft_max`` the most tree nodes a pass verifies.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from poolhouse import home, hub
from poolhouse.hub import room
from poolhouse.hub.modelfile import safetensors_header
from poolhouse.serve import kvcache, python_runtime
from poolhouse.serve.backend import (
    Lease,
    ServerBackend,
    ServerFailed,
    ServerInfo,
    ServerSpec,
    claim_port,
    launch,
    server_log,
)
from poolhouse.serve.binary import hub_environment
from poolhouse.serve.mlxref import PREFIX
from poolhouse.serve.preflight import Check, Report
from poolhouse.spec import LAYOUTS

__all__ = ["MlxTreeBackend", "drafter_of", "located", "report_for", "resident_bytes"]

#: the node cap when a spec names none
MAX_NODES = 32


def located(model: str | Path) -> Path | None:
    """The weights directory ``model`` names, when it is on this machine."""
    text = str(model).removeprefix(PREFIX)
    where = home.expand(text)
    if where.is_dir():
        return where
    return hub.held_snapshot(text)


def drafter_of(draft: str | Path | None) -> str:
    """The drafter kind a spec's ``draft`` names: ``dflash``, ``mtp``, ``ngram`` or ``none``."""
    text = str(draft or "").strip()
    if not text:
        return "none"
    if text == "ngram":
        return "ngram"
    name = Path(text).name.lower()
    if "dflash" in name or "dspark" in name:
        return "dflash"
    if "mtp" in name:
        return "mtp"
    raise ServerFailed(f"no tree drafter reads {text!r}: name a DFlash, DSpark or MTP head, "
                       "or 'ngram'")


#: the tensors a load memory-maps from disk instead of holding in memory
MAPPED = ".ple.ple_embedding.ngram_embedding."


def resident_bytes(where: Path | None) -> int:
    """Bytes of the safetensors under ``where`` a load holds in memory."""
    if where is None:
        return 0
    held = 0
    for shard in where.glob("*.safetensors"):
        header = safetensors_header(shard)
        held += sum(entry["data_offsets"][1] - entry["data_offsets"][0]
                    for name, entry in header.items()
                    if name != "__metadata__" and MAPPED not in name)
    return held


def kv_bytes(config: Mapping[str, Any], context: int, bits: int | None) -> tuple[int, int, int]:
    """``(bytes, attention layers, layers)`` of the KV cache for ``context`` tokens.

    Only attention layers keep a KV cache; a recurrent layer's state is not quantised and not
    counted here. A ``bits``-bit cache stores ``bits / 8`` bytes a value plus a 16-bit scale
    and bias per ``GROUP_SIZE`` values; ``None`` is f16, 2 bytes. 0 bytes when the config does
    not say how wide the heads are.
    """
    text = config.get("text_config") or config
    layers = int(text.get("num_hidden_layers") or 0)
    kinds = text.get("layer_types")
    if kinds:
        each = sum(kind == "full_attention" for kind in kinds)
    elif text.get("full_attention_interval"):
        each = layers // int(text["full_attention_interval"])
    else:
        each = layers
    heads = int(text.get("num_key_value_heads") or text.get("num_attention_heads") or 0)
    width = int(text.get("head_dim") or 0)
    if not width and text.get("num_attention_heads"):
        width = int(text.get("hidden_size") or 0) // int(text["num_attention_heads"])
    per_value = 2.0 if not bits else bits / 8 + 4 / 64
    return int(2 * each * heads * width * context * per_value), each, layers


def report_for(spec: ServerSpec, *, limit_bytes: int = 0) -> Report:
    """What can be known before the load: weights present, a layout for them, and the fit."""
    report = Report()
    weights = located(spec.model)
    report.checks.append(Check("weights", True,
                               str(weights) if weights else
                               f"{spec.model} is not on this machine; the load fetches it"))
    if weights is not None:
        kind = json.loads((weights / "config.json").read_text(encoding="utf-8")).get(
            "model_type", "")
        known = kind in LAYOUTS
        report.checks.append(Check("layout", known, f"model_type {kind!r}"
                                   + ("" if known else " has no tree-verification layout")))
        kept = kvcache.effective(spec)
        held, each, layers = kv_bytes(
            json.loads((weights / "config.json").read_text(encoding="utf-8")),
            int(spec.context), kept.bits)
        report.kv_estimate_bytes = held
        report.checks.append(Check(
            "kv cache", True, f"{kept.said}: {held / 2**30:.2f}G at {spec.context:,} tokens on "
            f"{each} of {layers} layers (recurrent state is not quantised and not counted)"))
    try:
        kind = drafter_of(spec.draft)
        report.checks.append(Check("drafter", True, kind))
    except ServerFailed as why:
        report.checks.append(Check("drafter", False, str(why)))
        kind = "none"
    report.weights_bytes = resident_bytes(weights)
    drafted = resident_bytes(located(spec.draft)) if kind in ("dflash", "mtp") else 0
    if limit_bytes and report.weights_bytes:
        wanted = report.weights_bytes + drafted + report.kv_estimate_bytes
        report.checks.append(Check("fit", wanted <= limit_bytes,
                                   f"{wanted / 2**30:.1f}G of weights and cache against "
                                   f"{limit_bytes / 2**30:.1f}G this machine allows"))
    return report


class MlxTreeBackend(ServerBackend):
    """`poolhouse.serve.mlx_tree_server` in a child process: one model, one drafter, one slot."""

    name = "mlx-tree"

    def command(self, spec: ServerSpec) -> list[str]:
        kind = drafter_of(spec.draft)
        argv = [*python_runtime.command(self.name), str(spec.model),
                "--port", str(spec.port), "--context", str(spec.context),
                "--drafter", kind,
                "--max-nodes", str(spec.spec_draft_max or MAX_NODES),
                "--kv-bits", str(kvcache.effective(spec).bits or 0)]
        if kind in ("dflash", "mtp"):
            argv += ["--drafter-model", str(spec.draft).removeprefix(PREFIX)]
        return argv

    def start(self, spec: ServerSpec, *, lease: Lease, timeout: float = 300.0,
              **starting: bool) -> ServerInfo:
        """Launch and wait until the model has loaded and answers. Raises ``ServerFailed``.

        Of the lease's ``starting`` switches only ``preflight`` applies; the load measures
        its own cost curve, which is the warm-up.
        """
        argv = self.command(spec)
        claim_port(spec, lease)
        if starting.get("preflight", True):
            report = report_for(spec, limit_bytes=room())
            if not report.ok:
                raise ServerFailed(report.said())
        log_path = server_log("mlx-tree", spec.port)
        process, base_url, load_s = launch(argv, lease, log_path=log_path,
                                           timeout=timeout, env=python_runtime.environment_for_child(hub_environment()))
        return ServerInfo(base_url=base_url, port=spec.port, pid=process.pid, backend=self.name,
                          log_path=log_path, load_s=load_s, process=process)
