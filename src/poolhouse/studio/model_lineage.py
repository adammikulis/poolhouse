"""Attach checkpoint base provenance from successful server-resolved inputs."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from .resources import base_fingerprint, model_fingerprint


def bind_model_base(node: dict, outputs: dict, inputs: dict, output_dir: Path) -> None:
    """Replace worker metadata with the exact explicitly authorized training base."""
    if node["type"] != "train.tool-calls" or "model" not in outputs:
        return
    base = inputs.get("model")
    if not isinstance(base, dict) or base.get("kind") != "model":
        raise ValueError("Checkpoint training requires an explicit authorized base model.")
    fingerprint = model_fingerprint(base) if base.get("id") else base_fingerprint(base)
    root = Path(output_dir).resolve(strict=True)
    for name in ("model", "checkpoint"):
        if name not in outputs:
            continue
        value = outputs[name]
        path = Path(value["path"])
        if path.is_symlink() or not path.is_dir() or not path.resolve(strict=True).is_relative_to(root):
            raise ValueError("Checkpoint outputs must be materialized inside their execution directory.")
        sidecar = path / "studio-model.json"
        if sidecar.exists() and (sidecar.is_symlink() or not sidecar.is_file() or sidecar.stat().st_nlink != 1):
            raise ValueError("Checkpoint lineage requires an owned regular sidecar.")
        directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            descriptor = os.open("studio-model.json", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        finally:
            os.close(directory)
        with os.fdopen(descriptor, "w") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Checkpoint lineage requires an owned regular sidecar.")
            stream.truncate(0)
            json.dump({"version": 1, "base_digest": fingerprint}, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        outputs[name] = {**value, "base": dict(base), "base_digest": fingerprint}
