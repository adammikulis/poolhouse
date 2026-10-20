"""Validated parent-owned coding snapshot receipts, without a separate state store."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from poolhouse import private_path, secretpaths
from poolhouse.activity import source_snapshot
from poolhouse.activity.source_snapshot import Capture

CONTROL_PREFIXES = (".claude", ".codex", ".pi", ".mcp.json", ".poolhouse-project.json")
BOUND_FIELDS = ("version", "source", "source_identity", "directory", "directory_identity")


def identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    return info.st_dev, info.st_ino


def manifest(capture: Capture) -> dict:
    return {"entries": [[entry.path.hex(), entry.mode.decode("ascii"), entry.sha256.hex(), entry.size]
                         for entry in sorted(capture.entries, key=lambda item: item.path)], "tree_hash": capture.tree_hash,
            "missing": [name.hex() for name in capture.missing], "excluded": [name.hex() for name in capture.excluded]}


def paths(encoded) -> tuple[bytes, ...]:
    if not isinstance(encoded, list) or any(not isinstance(value, str) for value in encoded):
        raise ValueError("Invalid coding snapshot paths")
    decoded = tuple(bytes.fromhex(value) for value in encoded)
    if (list(encoded) != [value.hex() for value in decoded] or decoded != tuple(sorted(set(decoded)))
            or any(b"\0" in value or any(part in (b"", b".", b"..") for part in value.split(b"/")) for value in decoded)):
        raise ValueError("Coding snapshot paths must be canonical")
    return decoded


def snapshot(saved: dict) -> dict:
    if (not isinstance(saved, dict) or set(saved) != {"name", "manifest"} or not isinstance(saved["name"], str)
            or re.fullmatch(r"master|result-[a-f0-9]{32}", saved["name"]) is None
            or not isinstance(saved["manifest"], dict)
            or set(saved["manifest"]) != {"entries", "tree_hash", "missing", "excluded"}):
        raise ValueError("Invalid coding snapshot receipt")
    value = saved["manifest"]
    entries = value["entries"]
    if (not isinstance(value["tree_hash"], str) or re.fullmatch(r"[a-f0-9]{40}", value["tree_hash"]) is None
            or not isinstance(entries, list) or any(not isinstance(row, list) or len(row) != 4
                or row[1] not in ("100644", "100755") or not isinstance(row[2], str)
                or re.fullmatch(r"[a-f0-9]{64}", row[2]) is None or type(row[3]) is not int
                or not 0 <= row[3] <= source_snapshot.FILE_LIMIT for row in entries)):
        raise ValueError("Invalid coding snapshot manifest")
    paths([row[0] for row in entries])
    paths(value["missing"])
    paths(value["excluded"])
    if sum(row[3] for row in entries) > source_snapshot.TOTAL_LIMIT:
        raise ValueError("The coding snapshot manifest exceeds its source limit")
    return saved


def namespace(source: Path, receipt: dict) -> Path:
    """Check retained namespace identity before acquiring its process lock."""
    if (not isinstance(receipt, dict) or set(receipt) != {*BOUND_FIELDS, "revision", "initial", "master"}
            or type(receipt["version"]) is not int or receipt["version"] != 1
            or type(receipt["revision"]) is not int or receipt["revision"] < 0
            or not isinstance(receipt["directory"], str) or not Path(receipt["directory"]).is_absolute()
            or any(not isinstance(receipt[key], list) or len(receipt[key]) != 2
                   or any(type(value) is not int or value < 0 for value in receipt[key])
                   for key in ("source_identity", "directory_identity"))
            or len(json.dumps(receipt, allow_nan=False).encode()) > source_snapshot.INDEX_LIMIT):
        raise ValueError("Invalid coding workspace receipt")
    canonical = source_snapshot.validate_storage(source, source_snapshot.protected_roots(dict(os.environ)))
    directory = Path(receipt["directory"])
    if (not canonical.is_dir() or str(canonical) != receipt["source"] or tuple(receipt["source_identity"]) != identity(canonical)
            or directory.resolve(strict=True) != directory or private_path.problem(directory) or not directory.is_dir()
            or tuple(receipt["directory_identity"]) != identity(directory)):
        raise ValueError("The coding workspace roots changed")
    source_snapshot.validate_storage(directory, source_snapshot.protected_roots(dict(os.environ)))
    if directory.is_relative_to(canonical) or canonical.is_relative_to(directory):
        raise ValueError("The coding workspace overlaps the selected repository")
    return directory


def checked(source: Path, receipt: dict) -> Path:
    """Validate receipt manifests without capturing or executing repository data."""
    directory = namespace(source, receipt)
    canonical = source.resolve(strict=True)
    initial, master = snapshot(receipt["initial"]), snapshot(receipt["master"])
    excluded = paths(initial["manifest"]["excluded"])
    fixed = {os.fsencode(path) for path in CONTROL_PREFIXES}
    if (not fixed.issubset(excluded) or any(path not in fixed
            and not secretpaths.path_hit(os.fsdecode(canonical / os.fsdecode(path))) for path in excluded)
            or master["manifest"]["excluded"] != initial["manifest"]["excluded"]):
        raise ValueError("The coding snapshot is missing protected control exclusions")
    for saved in (initial, master):
        target = directory / saved["name"]
        if target.resolve(strict=True) != target or private_path.problem(target) or not target.is_dir():
            raise ValueError("The coding snapshot directory changed")
    return directory


def transition(previous: dict, replacement: dict) -> None:
    """Require one epoch advance without changing the retained workspace or baseline."""
    if not isinstance(previous, dict) or not isinstance(previous.get("source"), str):
        raise ValueError("Invalid coding workspace receipt")
    checked(Path(previous["source"]), previous)
    checked(Path(previous["source"]), replacement)
    if (replacement["revision"] != previous["revision"] + 1
            or any(replacement[key] != previous[key] for key in BOUND_FIELDS)
            or replacement["initial"]["manifest"] != previous["initial"]["manifest"]):
        raise ValueError("The coding workspace publication changed its scope or baseline")
