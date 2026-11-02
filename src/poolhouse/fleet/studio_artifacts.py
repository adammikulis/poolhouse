"""Manifest-backed downloads of completed run artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from pathlib import Path
from urllib.parse import quote, urlencode

from poolhouse.studio.catalog import is_model_type, node_spec

WEIGHTS = frozenset({"model.safetensors", "optimizer.safetensors", "state.json",
                     "adapter_config.json", "adapter_model.safetensors", "adapters.safetensors",
                     "config.json", "head.safetensors", "lora/adapter_model.safetensors",
                     "lora/adapter_config.json", "tokenizer.json", "tokenizer_config.json"})


def _open(root: Path, relative: Path):
    if relative.is_absolute() or not relative.parts or any(p in {"..", "."} for p in relative.parts):
        raise ValueError("Choose a declared artifact file.")
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in relative.parts[:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = following
        fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(fd)
            raise ValueError("Artifact files must be regular files owned by this run.")
        return os.fdopen(fd, "rb")
    finally:
        os.close(directory)


def _relative(files_root: Path, run: dict, node: dict, path: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", run["id"]):
        raise ValueError("Invalid run identity.")
    owned = files_root.absolute() / "studio" / run["id"] / hashlib.sha256(node["id"].encode()).hexdigest()[:24]
    absolute = Path(path).absolute()
    if ".." in absolute.parts or not absolute.is_relative_to(owned):
        raise ValueError("This artifact does not belong to the selected run node.")
    return absolute.relative_to(files_root.absolute())


def _manifest(root: Path, folder: Path, name: str) -> dict | None:
    try:
        with _open(root, folder / name) as source:
            value = json.load(source)
    except FileNotFoundError:
        return None
    if not isinstance(value, dict):
        raise ValueError("Invalid artifact manifest.")
    return value


def _declared_files(root: Path, run: dict, node: dict, output: dict) -> list[tuple[str, Path]]:
    relative = _relative(root, run, node, output["path"])
    if relative.name == "report.json" and node["type"] == "report":
        return [(relative.name, relative)]
    if relative.name == "policy.zip" and node["type"] == "gym.train":
        _manifest(root, relative.parent, "policy-manifest.json")
        return [(relative.name, relative), ("policy-manifest.json", relative.parent / "policy-manifest.json")]
    if node['type'] == 'data.hf-preview':
        _manifest(root, relative, 'dataset-import.json')
        return [(name, relative / name) for name in ('dataset.jsonl', 'dataset-import.json')]
    export = _manifest(root, relative, "export.json")
    if export is not None:
        names = export.get("files")
        if not isinstance(names, list) or not names or any(not isinstance(n, str) or n not in WEIGHTS for n in names):
            raise ValueError("Invalid export file manifest.")
        return [(name, relative / name) for name in ["export.json", *names]]
    decision = _manifest(root, relative, "decider.json")
    if decision is not None:
        names = decision.get("sha256", {})
        if not isinstance(names, dict) or not names or not set(names) <= WEIGHTS:
            raise ValueError("Invalid decision model manifest.")
        return [(name, relative / name) for name in ["decider.json", "manifest.json", *names]]
    if _manifest(root, relative, "state.json") is not None:
        return [(name, relative / name) for name in ["state.json", "model.safetensors", "optimizer.safetensors"]
                if _exists(root, relative / name)]
    return []


def _exists(root: Path, relative: Path) -> bool:
    try:
        with _open(root, relative):
            return True
    except FileNotFoundError:
        return False


def _candidates(run: dict, files_root: Path, authorize):
    for node in run["graph"]["nodes"]:
        state = run["nodes"][node["id"]]
        if state["state"] != "done" or state.get("reused_from"):
            continue
        if not authorize(node, state["outputs"]):
            raise PermissionError("This artifact is no longer authorized.")
        for port in node_spec(node["type"], node.get("version", 1))["outputs"]:
            if port["type"] != "artifact" and not is_model_type(port["type"]):
                continue
            output = state["outputs"].get(port["name"])
            if not isinstance(output, dict) or output.get("kind") not in {"artifact", "model"} or not isinstance(output.get("path"), str):
                continue
            if node["type"] in {"dataset", "model"}:
                continue
            for name, path in _declared_files(files_root, run, node, output):
                yield node, port["name"], name, path


def artifact_files(run: dict, files_root: Path, authorize) -> list[dict]:
    """Return authorized download links from fixed node output manifests."""
    rows = []
    for node, port, name, relative in _candidates(run, files_root, authorize):
        with _open(files_root, relative) as source:
            size = os.fstat(source.fileno()).st_size
        query = urlencode({"node": node["id"], "port": port, "name": name})
        rows.append({"node": node["id"], "port": port, "name": name, "bytes": size,
                     "href": f"/ui/studio/runs/{run['id']}/artifacts/file?{query}"})
    return rows


def open_artifact(run: dict, files_root: Path, selected: dict, authorize):
    """Open one declared artifact after checking current access and file ownership."""
    for node, declared_port, declared_name, relative in _candidates(run, files_root, authorize):
        if (node["id"], declared_port, declared_name) == (selected.get("node"), selected.get("port"), selected.get("name")):
            if not authorize(node, run["nodes"][node["id"]]["outputs"]):
                raise PermissionError("This artifact is no longer authorized.")
            return _open(files_root, relative), Path(declared_name).name
    raise KeyError("No such declared artifact file.")


def stream_artifact(handler, opened, filename: str) -> None:
    """Stream an opened artifact as an attachment and close its file handle."""
    with opened:
        handler.send_response(200)
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Content-Length", str(os.fstat(opened.fileno()).st_size))
        handler.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(filename, safe=""))
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()
        if handler.command != "HEAD":
            shutil.copyfileobj(opened, handler.wfile, length=1024 * 1024)
