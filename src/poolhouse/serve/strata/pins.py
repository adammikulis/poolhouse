"""What Strata is pinned to (`pins.json`): the engine's commit and digests, and the model and drafter files it serves, each by size and
SHA-256."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from poolhouse import home
from poolhouse.files import read_json, sha256_file, write_json

__all__ = ["Digests", "FilePin", "ModelSet", "check_files", "load", "model_sets", "recognised"]


@dataclass(frozen=True, slots=True)
class FilePin:
    name: str
    bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ModelSet:
    """One quantisation of Qwen3.8-Flash-Next in the files Strata reads, under Strata's own name for it."""

    key: str
    repo: str
    revision: str
    directory: str
    metal: bool
    cuda: bool
    files: tuple[FilePin, ...]
    experts_gb: float
    ram_gb: float

    @property
    def unsloth(self) -> bool:
        return self.key.startswith("unsloth-")

    @property
    def name(self) -> str:
        """The model name Strata's per-model defaults are keyed by."""
        return f"qwen3.8-flash-next-{self.key}"

    @property
    def reference(self) -> str:
        """The ``hf:`` reference of the first file, which `poolhouse-models fetch` takes."""
        return f"hf:{self.repo}/{self.directory}/{self.files[0].name}"

    @property
    def bytes(self) -> int:
        return sum(one.bytes for one in self.files)


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    return json.loads(Path(__file__).with_name("pins.json").read_text(encoding="utf-8"))


def model_sets() -> tuple[ModelSet, ...]:
    return tuple(ModelSet(key, one["repo"], one["revision"], one["directory"], one["metal"], one["cuda"],
                          tuple(FilePin(**file) for file in one["files"]), one["experts_gb"], one["ram_gb"])
                 for key, one in load()["models"].items())


def recognised(model: str | Path) -> tuple[ModelSet, tuple[Path, ...]] | None:
    """The set whose first file ``model`` is, with every file of it found beside it at its pinned size; None for any other file."""
    first = Path(str(model))
    for one in model_sets():
        if one.files[0].name != first.name:
            continue
        paths = tuple(first.with_name(file.name) for file in one.files)
        if all(path.is_file() and path.stat().st_size == file.bytes for path, file in zip(paths, one.files, strict=True)):
            return one, paths
    return None


class Digests:
    """The SHA-256 of files already read here, kept while a file's size, time and identity are unchanged."""

    def __init__(self, record: Path | None = None) -> None:
        self.record = record or home.state("strata", "digests.json")

    def digest(self, path: Path) -> str:
        """The hex SHA-256 of ``path``."""
        real = path.resolve()
        info = real.stat()
        seen = [info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino]
        held = read_json(self.record, {})
        entry = held.get(str(real))
        if entry and entry.get("seen") == seen:
            return entry["sha256"]
        digest = sha256_file(real)
        after = real.stat()
        if [after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino] != seen:
            raise ValueError(f"{real.name} changed while it was read")
        held = {**read_json(self.record, {}), str(real): {"seen": seen, "sha256": digest}}
        self.record.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_json(self.record, held)
        return digest


def check_files(files: tuple[Path, ...], pins: tuple[FilePin, ...], digests: Digests | None = None, say=lambda _line: None) -> None:
    """Raise `ValueError` for any of ``files`` whose bytes are not the pinned ones."""
    held = digests or Digests()
    for path, pin in zip(files, pins, strict=True):
        say(f"checking {pin.name} against its pinned digest")
        if path.stat().st_size != pin.bytes or held.digest(path) != pin.sha256:
            raise ValueError(f"{path} is not the pinned {pin.name}: its size or SHA-256 differs")
