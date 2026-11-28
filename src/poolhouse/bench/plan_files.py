"""A campaign's model files: where they are, what identifies them, what is still to be fetched and what disk it takes."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from poolhouse import hub
from poolhouse.log import say, warn

__all__ = ["commit", "digest_of", "found", "identity", "needed", "required", "show"]


def _cache() -> Path:
    from huggingface_hub import constants

    return Path(constants.HF_HUB_CACHE)


def found(repo: str, path: str, cache: Path | None = None) -> Path | None:
    """``path`` of ``repo`` in the Hub cache, from whichever snapshot holds it, or None."""
    snapshots = (cache or _cache()) / f"models--{repo.replace('/', '--')}" / "snapshots"
    held = sorted((p for p in snapshots.glob(f"*/{path}") if p.is_file()), key=lambda p: p.stat().st_mtime)
    return held[-1] if held else None


def needed(config: Mapping[str, Any], key: str, cache: Path | None = None) -> list[dict[str, Any]]:
    """Every shard of the campaign file ``key``: its repository, path, bytes, blob and whether it is here. A shard
    whose blob its repository already holds under another name counts as held, since fetching it moves no bytes."""
    entry = config["files"][key]
    home_ = cache or _cache()
    out = []
    for shard in entry["shards"]:
        path = found(entry["repo"], shard["path"], home_)
        blob = home_ / f"models--{entry['repo'].replace('/', '--')}" / "blobs" / str(shard.get("sha256") or "-")
        out.append({"repo": entry["repo"], "path": shard["path"], "bytes": int(shard["bytes"]),
                    "sha256": shard.get("sha256"), "here": path, "download": 0 if path or blob.is_file()
                    else int(shard["bytes"])})
    return out


def required(config: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Path]:
    """The first shard of each file in ``keys``, fetched first when it is not here."""
    out = {}
    for key in dict.fromkeys(keys):
        shards = needed(config, key)
        for shard in shards:
            if shard["here"] is None:
                say(f"fetching {shard['repo']}/{shard['path']} ({shard['download'] / 1e9:.1f} GB to download)")
                hub.fetch(f"hf:{shard['repo']}/{shard['path']}")
        first = found(config["files"][key]["repo"], config["files"][key]["shards"][0]["path"])
        if first is None:
            raise ValueError(f"{key} is not in the Hub cache after fetching it")
        out[key] = first
    return out


def identity(first: Path) -> dict[str, Any]:
    """What names a served file: its name, its snapshot, and each sibling shard's bytes and blob."""
    shards = sorted(first.parent.glob(first.name.replace("00001-of", "*-of"))) or [first]
    return {"name": first.name, "snapshot": next((p.name for p in first.parents if len(p.name) == 40), ""),
            "shards": [{"name": s.name, "bytes": s.stat().st_size,
                        "blob": s.resolve().name if s.is_symlink() else ""} for s in shards]}


def commit(root: Path) -> str:
    """The commit the checkout at ``root`` is on, read from its files; "" when it cannot be read."""
    try:
        dot = root / ".git"
        git = Path(dot.read_text(encoding="utf-8").split(":", 1)[1].strip()) if dot.is_file() else dot
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: "):
            return head
        name = head[5:]
        common = git / "commondir"
        shared = (git / common.read_text(encoding="utf-8").strip()) if common.is_file() else git
        loose = shared / name
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip()
        packed = (shared / "packed-refs").read_text(encoding="utf-8")
        return next((line.split()[0] for line in packed.splitlines() if line.endswith(" " + name)), "")
    except (OSError, IndexError):
        return ""


def digest_of(text: str) -> str:
    """The hash two machines compare to know they sent the same text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def show(config: Mapping[str, Any], arms: Sequence[Any], free: int) -> int:
    """``plan``: each arm, each file with what is here and what would be downloaded, against ``free`` bytes."""
    keys = dict.fromkeys(k for arm in arms for k in (arm.model, arm.head, *(
        load.get("head", "") for load in (arm.tune or {}).get("loads", []))) if k)
    for arm in arms:
        say(f"arm {arm.name}: {arm.engine} {arm.build}, {arm.model}, drafter {arm.head or '(engine default)'}, "
            f"{arm.draft_n_max} ahead, KV {arm.kv}{', ' + ' '.join(arm.extra_args) if arm.extra_args else ''}")
    total, counted = 0, set()
    for key in keys:
        for shard in needed(config, key):
            fresh = (shard["repo"], shard["sha256"] or shard["path"]) not in counted
            counted.add((shard["repo"], shard["sha256"] or shard["path"]))
            total += shard["download"] if fresh else 0
            state = "here" if shard["here"] else "held under another name" if not shard["download"] else "to download"
            say(f"  {shard['bytes'] / 1e9:7.1f} GB  {state:<23} {shard['repo']}/{shard['path']}")
    say(f"to download: {total / 1e9:.1f} GB; free: {free / 1e9:.1f} GB")
    if total > free:
        warn("the downloads do not fit the free disk")
        return 1
    return 0
