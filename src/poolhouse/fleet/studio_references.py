"""Model configuration references confined to explicitly staged content."""

from __future__ import annotations

import json
from pathlib import Path

PATH_FIELDS = frozenset({"base", "base_id", "base_model_name_or_path", "tokenizer_file", "directory",
                         "checkpoint_path", "resume_from_checkpoint", "model_path"})


def _references(value, root: Path, files: set[str]) -> None:
    if isinstance(value, list):
        for item in value:
            _references(item, root, files)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in {"trust_remote_code", "auto_map"} and item:
                raise ValueError("Device models use installed model implementations only.")
            if key in PATH_FIELDS and isinstance(item, str) and item:
                relative = Path(item)
                if (relative.is_absolute() or ".." in relative.parts or ":" in item or "\\" in item
                        or not any(name == relative.as_posix() or name.startswith(relative.as_posix().rstrip("/") + "/") for name in files)):
                    raise ValueError("Model references require an explicitly staged base resource; target paths and downloads are unavailable.")
                target = root / relative
                if not target.resolve().is_relative_to(root.resolve()):
                    raise ValueError("A model reference escapes its staged resource.")
            _references(item, root, files)


def validate_models(inputs: dict, manifests: list[dict], hydrated: dict) -> None:
    """Reject external loader references before any worker or compute admission."""
    by_id = {manifest["id"]: manifest for manifest in manifests}
    for name, value in inputs.items():
        if not isinstance(value, dict) or value.get("kind") != "model":
            continue
        manifest = by_id.get(value.get("resource"))
        if manifest is None:
            raise ValueError("Model inputs require an explicitly staged resource manifest.")
        path = Path(hydrated[name]["path"])
        root = path if manifest["shape"] == "directory" else path.parent
        files = {entry["name"] for entry in manifest["files"]}
        for filename in files:
            if Path(filename).name in {"state.json", "manifest.json", "config.json", "adapter_config.json",
                                      "tokenizer_config.json", "decider.json", "policy-manifest.json"}:
                with (root / filename).open() as source:
                    _references(json.load(source), root, files)
