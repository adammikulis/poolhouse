"""``warm.json``: which byte ranges of a model's mmapped tensors a run left in the page cache,
and reading them ahead the next time the model loads.

The manifest sits beside the first shard as ``<file>.warm.json``. A recording asks the OS
which pages of each expert and n-gram tensor are resident (`prefault.resident_runs`), so it
needs no change to llama.cpp; the pages a run touched are the ones the router and the gather
reached, plus whatever read-ahead pulled in beside them. Per-expert and per-row counts need
llama.cpp to count them and are listed under ``planned``.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from poolhouse.files import read_json, versioned, write_json
from poolhouse.serve import prefault
from poolhouse.serve.placement import part_of
from poolhouse.serve.tensors import shard_paths, tensors_by_shard

__all__ = ["Manifest", "hot_ranges", "path_for", "prefault_ranges", "read", "record", "start",
           "write"]

VERSION = 1
GAP = 256 * 1024
MOST_RUNS = 20_000
CHUNK = 256 * 1024 * 1024
ROLES = ("experts", "table")
PLANNED = {
    "per_expert_counts": "how often the router chose each expert in each layer; needs a "
                         "llama.cpp patch that counts router outputs",
    "ngram_row_counts": "how often each row of the n-gram table was gathered; needs a "
                        "llama.cpp patch that counts gathered indices",
}


@dataclass(slots=True)
class Manifest:
    """What a recording kept: the shards it was taken from and, per tensor, the resident ranges."""

    shards: list[dict[str, object]]
    recorded_at: str = ""
    runs: int = 0
    tensors: dict[str, dict[str, object]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return versioned({"shards": self.shards, "recorded_at": self.recorded_at,
                          "runs": self.runs, "observed": self.tensors,
                          "planned": PLANNED}, VERSION)


def path_for(model: str | Path) -> Path:
    """Where the manifest of ``model`` lives: beside its first shard."""
    first = shard_paths(model)[0]
    return first.with_name(first.name + ".warm.json")


def _identity(model: str | Path) -> list[dict[str, object]]:
    out = []
    for shard in shard_paths(model):
        seen = shard.stat()
        out.append({"name": shard.name, "size": seen.st_size, "mtime_ns": seen.st_mtime_ns})
    return out


def read(model: str | Path) -> Manifest | None:
    """The manifest of ``model``, or None when there is none, it is not this version, or the
    files it was taken from have changed."""
    found = read_json(path_for(model), None)
    if not isinstance(found, dict) or found.get("version") != VERSION:
        return None
    try:
        if found.get("shards") != _identity(model):
            return None
    except OSError:
        return None
    seen = found.get("observed")
    return Manifest(found["shards"], str(found.get("recorded_at") or ""),
                    int(found.get("runs") or 0), seen if isinstance(seen, dict) else {})


def write(model: str | Path, manifest: Manifest) -> Path:
    """Write ``manifest`` beside ``model``."""
    where = path_for(model)
    write_json(where, manifest.as_dict())
    return where


def _merge(runs: list[tuple[int, int]], gap: int = GAP) -> list[tuple[int, int]]:
    """Sorted runs with those closer than ``gap`` bytes joined."""
    out: list[tuple[int, int]] = []
    for start, end in sorted(runs):
        if out and start - out[-1][1] <= gap:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def _thinned(runs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    gap = GAP
    runs = _merge(runs, gap)
    while len(runs) > MOST_RUNS:
        gap *= 2
        runs = _merge(runs, gap)
    return runs


Observe = Callable[[Path, int, int], list[tuple[int, int]]]


def record(model: str | Path, *, observe: Observe = prefault.resident_runs,
           now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> Manifest:
    """Ask which pages of every expert and n-gram tensor are resident and merge them into the
    manifest already beside ``model``. ``observe`` is `prefault.resident_runs`."""
    before = read(model)
    kept = dict(before.tensors) if before else {}
    for index, (path, held) in enumerate(tensors_by_shard(model)):
        for one in held:
            role = part_of(one).role
            if role not in ROLES or not one.bytes:
                continue
            seen = observe(path, one.offset, one.offset + one.bytes)
            old = kept.get(one.name, {}).get("ranges", [])
            joined = _thinned([*(tuple(r) for r in old), *seen])  # type: ignore[misc]
            if not joined:
                continue
            kept[one.name] = {"shard": index, "role": role, "layer": part_of(one).layer,
                              "bytes": one.bytes, "offset": one.offset,
                              "resident": sum(b - a for a, b in joined),
                              "ranges": [list(r) for r in joined]}
    return Manifest(_identity(model), now().isoformat(timespec="seconds"),
                    (before.runs if before else 0) + 1, kept)


def hot_ranges(model: str | Path, manifest: Manifest, budget: int) -> list[tuple[Path, int, int]]:
    """The ranges to read ahead, ``(file, start, end)``, tensors with the larger resident share
    first, stopping at ``budget`` bytes."""
    files = shard_paths(model)
    ordered = sorted(manifest.tensors.values(),
                     key=lambda t: -int(t["resident"]) / max(int(t["bytes"]), 1))  # type: ignore[call-overload]
    out: list[tuple[Path, int, int]] = []
    left = budget
    for tensor in ordered:
        for start, end in tensor["ranges"]:  # type: ignore[attr-defined]
            take = min(end - start, left)
            if take <= 0:
                return out
            out.append((files[int(tensor["shard"])], start, start + take))  # type: ignore[call-overload]
            left -= take
    return out


def prefault_ranges(ranges: list[tuple[Path, int, int]], advise: prefault.Advise | None = None,
                    stop: threading.Event | None = None) -> int:
    """Ask the OS to read each range ahead, a chunk at a time. Returns the bytes asked for."""
    call = advise if advise is not None else prefault.advise_for_platform()
    asked = 0
    for path, start, end in ranges:
        fd = os.open(path, os.O_RDONLY)
        try:
            for at in range(start, end, CHUNK):
                if stop is not None and stop.is_set():
                    return asked
                call(fd, at, min(CHUNK, end - at))
                asked += min(CHUNK, end - at)
        finally:
            os.close(fd)
    return asked


def start(model: str | Path, budget: int, advise: prefault.Advise | None = None
          ) -> threading.Thread | None:
    """Read the hot ranges of ``model`` ahead in a background thread, up to ``budget`` bytes.
    None when there is no manifest or no budget."""
    manifest = read(model)
    if manifest is None or budget <= 0:
        return None
    ranges = hot_ranges(model, manifest, budget)
    worker = threading.Thread(target=prefault_ranges, args=(ranges, advise),
                              name="poolhouse-warm", daemon=True)
    worker.start()
    return worker
