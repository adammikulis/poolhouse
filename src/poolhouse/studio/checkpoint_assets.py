"""Validate checkpoint assets and prepare an isolated copy with explicit base bindings."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path, PurePosixPath

from poolhouse.files import promote
from poolhouse.json_objects import unique_object
from poolhouse.studio.catalog import node_spec

SHA = re.compile(r"[a-f0-9]{64}")
JSON_LIMIT = 1024 * 1024
CHUNK = 1024 * 1024
_REFERENCES = frozenset({"base", "base_id", "base_config", "base_identity", "model", "model_path",
                         "model_name_or_path", "pretrained_model_name_or_path", "_name_or_path", "name_or_path",
                         "tokenizer", "tokenizer_class", "tokenizer_name", "tokenizer_name_or_path", "tokenizer_file",
                         "auto_map", "custom_pipelines", "trust_remote_code", "code", "code_revision", "module",
                         "cache_dir", "revision", "data", "out", "directory"})
_TOKENIZER_FILES = frozenset({"tokenizer_file", "vocab_file", "merges_file", "added_tokens_file",
                              "special_tokens_map_file", "chat_template_file"})
_JSON_FILES = frozenset({"state.json", "studio-model.json", "manifest.json", "adapter_config.json",
                         "config.json", "tokenizer_config.json", "special_tokens_map.json", "generation_config.json"})
_COPY_JSON = frozenset({"state.json", "studio-model.json", "manifest.json", "adapter_config.json"})


def _name(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("Checkpoint assets need declared relative filenames.")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in {".", ".."} for part in path.parts):
        raise ValueError("Checkpoint assets cannot leave their declared directory.")
    return value


@contextmanager
def _directory(path: Path, *, create: bool = False):
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Checkpoint directories require canonical absolute paths.")
    with ExitStack() as cleanup:
        descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        cleanup.callback(os.close, descriptor)
        for part in path.parts[1:]:
            if create:
                with suppress(FileExistsError):
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
            descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            cleanup.callback(os.close, descriptor)
        yield descriptor


@contextmanager
def _asset(root: Path, name: str):
    relative = PurePosixPath(_name(name))
    with _directory(root.joinpath(*relative.parts[:-1])) as directory:
        descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ValueError("Checkpoint assets must be regular files without links.")
            yield stream
            after = os.fstat(descriptor)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("A checkpoint asset changed while it was read.")


@contextmanager
def _new_asset(root: Path, name: str):
    relative = PurePosixPath(_name(name))
    with _directory(root.joinpath(*relative.parts[:-1]), create=True) as directory:
        descriptor = os.open(relative.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        with os.fdopen(descriptor, "wb") as stream:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())


def _finite(value) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite(item) for item in value)
    return True


def _json(data: bytes) -> dict:
    if len(data) > JSON_LIMIT:
        raise ValueError("Checkpoint metadata exceeds its supported size.")
    value = json.loads(data, object_pairs_hook=unique_object)
    if not isinstance(value, dict) or not _finite(value):
        raise ValueError("Checkpoint metadata must contain finite object values.")
    return value


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _inventory(root: Path) -> set[str]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Choose a complete regular checkpoint directory.")
    names = set()
    for path in root.rglob("*"):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Checkpoint assets cannot contain links or special files.")
        names.add(path.relative_to(root).as_posix())
    return names


def _verified(root: Path, declared: list[dict]) -> tuple[dict, dict]:
    if not isinstance(declared, list) or not declared:
        raise ValueError("Checkpoint assets require a declared file manifest.")
    files, metadata = {}, {}
    for entry in declared:
        if not isinstance(entry, dict) or set(entry) != {"name", "size", "sha256"}:
            raise ValueError("Invalid checkpoint file manifest fields.")
        name = _name(entry["name"])
        if name in files or type(entry["size"]) is not int or entry["size"] < 0 or not isinstance(entry["sha256"], str) or not SHA.fullmatch(entry["sha256"]):
            raise ValueError("Invalid checkpoint file identity or size.")
        checksum, size, chunks = hashlib.sha256(), 0, []
        parse = PurePosixPath(name).name in _JSON_FILES or name.endswith(".safetensors.index.json")
        if parse and entry["size"] > JSON_LIMIT:
            raise ValueError("Checkpoint metadata exceeds its supported size.")
        with _asset(root, name) as source:
            while data := source.read(CHUNK):
                size += len(data)
                checksum.update(data)
                if parse:
                    chunks.append(data)
        if size != entry["size"] or checksum.hexdigest() != entry["sha256"]:
            raise ValueError("Checkpoint asset content does not match its declared digest.")
        files[name] = dict(entry)
        if parse:
            metadata[name] = _json(b"".join(chunks))
    if _inventory(root) != set(files):
        raise ValueError("The checkpoint contains undeclared or missing assets.")
    _indices(files, metadata)
    return files, metadata


def _indices(files: dict, metadata: dict) -> None:
    for name, value in metadata.items():
        if not name.endswith(".safetensors.index.json"):
            continue
        mapping = value.get("weight_map")
        if set(value) - {"metadata", "weight_map"} or not isinstance(mapping, dict) or not mapping:
            raise ValueError("Invalid safetensors weight-map metadata.")
        extra = value.get("metadata", {})
        if not isinstance(extra, dict) or set(extra) - {"total_size"} or ("total_size" in extra and (type(extra["total_size"]) is not int or extra["total_size"] < 0)):
            raise ValueError("Invalid safetensors size metadata.")
        for tensor, filename in mapping.items():
            if not isinstance(tensor, str) or not tensor or not isinstance(filename, str):
                raise ValueError("Invalid safetensors shard reference.")
            _name(filename)
            target = (PurePosixPath(name).parent / filename).as_posix()
            if target not in files or not target.endswith(".safetensors"):
                raise ValueError("Safetensors shards must be declared regular tensor assets.")


def _base(handle: dict | None) -> tuple[dict | None, dict]:
    if handle is None:
        return None, {}
    if not isinstance(handle, dict) or set(handle) != {"kind", "path", "digest", "files"} or handle["kind"] != "model":
        raise ValueError("Select an explicit finalized base model handle.")
    if not isinstance(handle["path"], str) or not Path(handle["path"]).is_absolute() or ".." in Path(handle["path"]).parts:
        raise ValueError("The explicit base must be a staged absolute directory.")
    files, metadata = _verified(Path(handle["path"]), handle["files"])
    rows = [[name, entry["size"], entry["sha256"]] for name, entry in sorted(files.items())]
    if not isinstance(handle["digest"], str) or not SHA.fullmatch(handle["digest"]) or _digest(rows) != handle["digest"]:
        raise ValueError("The explicit base content identity does not match.")
    for name, value in metadata.items():
        if PurePosixPath(name).name in {"config.json", "tokenizer_config.json"}:
            _base_references(name, value, files)
    return dict(handle), files


def _base_references(name: str, value: dict, files: dict) -> None:
    for key, item in value.items():
        if key in {"auto_map", "custom_pipelines", "code", "code_revision"} and item:
            raise ValueError("Staged base models cannot load external code.")
        if key == "trust_remote_code" and item is not False:
            raise ValueError("Staged base models cannot trust external code.")
        if key in _TOKENIZER_FILES and item is not None:
            if not isinstance(item, str):
                raise ValueError("Invalid tokenizer asset reference.")
            _name(item)
            if (PurePosixPath(name).parent / item).as_posix() not in files:
                raise ValueError("Tokenizer references must name declared base assets.")
        if isinstance(item, dict):
            _base_references(name, item, files)


def _mlx_identity(value: dict, files: dict) -> dict:
    if not isinstance(value, dict) or set(value) != {"files"} or not isinstance(value["files"], dict) or not value["files"]:
        raise ValueError("MLX checkpoints require the recorded content-only frozen-base identity.")
    expected = {name: file["sha256"] for name, file in files.items()
                if "/" not in name and (name.endswith(".safetensors") or name in {"config.json", "tokenizer.json", "tokenizer_config.json"})}
    if value["files"] != expected or not any(name.endswith(".safetensors") for name in expected):
        raise ValueError("The recorded MLX frozen-base identity does not match the explicit base.")
    return {"files": dict(value["files"])}


def _reference(key: str) -> bool:
    return key in _REFERENCES or key.startswith(("tokenizer_", "code_")) or key.endswith(("_path", "_url", "_dir"))


def _sanitized(value, base_files: dict):
    if isinstance(value, list):
        return [_sanitized(item, base_files) for item in value]
    if not isinstance(value, dict):
        return value
    return {key: _mlx_identity(item, base_files) if key == "mlx_base_identity" else _sanitized(item, base_files)
            for key, item in value.items() if not _reference(key)}


def _configuration(metadata: dict, base: dict | None, base_files: dict) -> None:
    state = metadata.get("state.json", {})
    config = state.get("config", {})
    if not isinstance(config, dict):
        raise ValueError("A complete checkpoint requires recipe configuration.")
    recipe, framework = config.get("recipe"), config.get("framework")
    if base is None:
        if recipe not in {"classify-text", "text-lm"} or framework not in {"mlx", "torch"}:
            raise ValueError("This checkpoint requires an explicit authorized base model.")
        if metadata.get("studio-model.json") or any(_reference(key) or key.startswith("lora") or key in {"mlx_base_identity", "mlx_lora_parameters"} for key in config):
            raise ValueError("Standalone checkpoints cannot declare external model dependencies.")
        allowed = set(node_spec("train." + recipe)["parameters"]["properties"]) | {
            "recipe", "classes", "rows", "train_rows", "holdout_rows", "documents", "train_bytes", "holdout_bytes",
            "seed", "eval_every", "checkpoint_every", "max_minutes", "parameters"}
        if set(config) - allowed:
            raise ValueError("Standalone checkpoints must use the fixed recipe configuration schema.")
        return
    sidecar = metadata.get("studio-model.json")
    if not isinstance(sidecar, dict) or set(sidecar) != {"version", "base_digest"} or type(sidecar["version"]) is not int or sidecar["version"] != 1 or sidecar["base_digest"] != base["digest"]:
        raise ValueError("The checkpoint producer's base binding is missing or does not match.")
    if recipe != "tool-calls" or framework not in {"mlx", "torch"}:
        raise ValueError("The checkpoint does not name a supported external-base recipe.")
    if framework == "mlx":
        _mlx_identity(config.get("mlx_base_identity"), base_files)


def _copied(name: str) -> bool:
    return name.endswith((".safetensors", ".safetensors.index.json")) or PurePosixPath(name).name in _COPY_JSON


def _copy(source: Path, destination: Path, files: dict, metadata: dict, base_files: dict) -> dict:
    expected = {}
    for name, entry in files.items():
        if not _copied(name):
            continue
        if name in metadata:
            value = metadata[name] if name.endswith(".safetensors.index.json") else _sanitized(metadata[name], base_files)
            data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            with _new_asset(destination, name) as output:
                output.write(data)
            expected[name] = hashlib.sha256(data).hexdigest()
        else:
            checksum = hashlib.sha256()
            with _asset(source, name) as incoming, _new_asset(destination, name) as output:
                while data := incoming.read(CHUNK):
                    checksum.update(data)
                    output.write(data)
            if checksum.hexdigest() != entry["sha256"]:
                raise ValueError("Checkpoint weights changed while they were copied.")
            expected[name] = entry["sha256"]
    return expected


def prepare_checkpoint(model_path: Path, base_handle: dict | None, declared_files: list[dict], output_dir: Path) -> dict:
    """Validate explicit dependencies and return an isolated checkpoint with inert metadata."""
    source, destination = Path(model_path).absolute(), Path(output_dir).absolute()
    if ".." in source.parts or ".." in destination.parts or destination == source or destination.is_relative_to(source):
        raise ValueError("Prepared checkpoints need a separate owned output directory.")
    base, base_files = _base(base_handle)
    if base is not None and (destination == Path(base["path"]) or destination.is_relative_to(base["path"])):
        raise ValueError("Prepared checkpoints cannot overwrite their base model.")
    files, metadata = _verified(source, declared_files)
    if "state.json" not in files or not any(name.endswith(".safetensors") for name in files):
        raise ValueError("Select a complete checkpoint with declared tensor weights and state.")
    _configuration(metadata, base, base_files)
    identity = {"version": 1, "checkpoint_digest": _digest([files[name] for name in sorted(files)]),
                "base_digest": base["digest"] if base else None}
    staging = destination.with_name(destination.name + ".partial")
    with _directory(destination.parent, create=True) as directory:
        try:
            os.mkdir(staging.name, mode=0o700, dir_fd=directory)
        except FileExistsError:
            raise ValueError("An incomplete checkpoint copy must be reconciled before preparing it.") from None
        try:
            expected = _copy(source, staging, files, metadata, base_files)
            marker = json.dumps({**identity, "files": expected}, sort_keys=True, separators=(",", ":")).encode()
            with _new_asset(staging, "studio-prepared.json") as output:
                output.write(marker)
            try:
                existing = os.stat(destination.name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                promote(staging.name, destination.name, directory_fd=directory)
            else:
                if not stat.S_ISDIR(existing.st_mode):
                    raise ValueError("The prepared checkpoint destination is not an owned directory.")
                expected["studio-prepared.json"] = hashlib.sha256(marker).hexdigest()
                entries = [{"name": name, "size": (destination / name).lstat().st_size, "sha256": sha}
                           for name, sha in expected.items()]
                _verified(destination, entries)
        finally:
            if not shutil.rmtree.avoids_symlink_attacks:
                raise ValueError("Checkpoint cleanup requires descriptor-safe directory removal.")
            with suppress(FileNotFoundError):
                shutil.rmtree(staging.name, dir_fd=directory)
    return {"path": str(destination), "base": base, "base_digest": base["digest"] if base else None}
