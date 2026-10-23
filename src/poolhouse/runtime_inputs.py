"""Compare committed runtime inputs without relabelling an existing build's source revision."""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

from poolhouse import runtime_git

DOCUMENTS = frozenset({"docs/native-plan-macos.md", "docs/native-plan-windows-linux.md", "docs/node-contract.md"})
FORCE_INCLUDE = {"contracts": "poolhouse/contracts/_data", "patches": "poolhouse/serve/_patches"}


def _documentation(path: str) -> bool:
    return path in DOCUMENTS or (path.startswith("docs/notes/") and path.endswith(".md"))


def _packaging(checkout: Path, commit: str, paths: set[str]) -> bool:
    """Only the audited fixed Hatch/compute/node builders have a documentation-free input closure."""
    project = tomllib.loads(runtime_git.git(checkout, "show", f"{commit}:pyproject.toml"))
    system = project.get("build-system", {})
    hatch = project.get("tool", {}).get("hatch", {})
    build = hatch.get("build", {})
    wheel = build.get("targets", {}).get("wheel", {})
    metadata = project.get("project", {})
    licenses = metadata.get("license-files", [])
    node = tomllib.loads(runtime_git.git(checkout, "show", f"{commit}:app/poolhouse-node/Cargo.toml"))
    return (system == {"requires": ["hatchling"], "build-backend": "hatchling.build"}
            and set(hatch) == {"build"} and set(build) == {"targets"}
            and not (set(wheel) - {"packages", "exclude", "force-include"})
            and wheel.get("packages") == ["src/poolhouse"] and wheel.get("force-include") == FORCE_INCLUDE
            and metadata.get("readme") == "README.md" and not metadata.get("dynamic")
            and isinstance(licenses, list) and all(isinstance(path, str) and "/" not in path
                                                  and path in paths and not any(char in path for char in "*?[") for path in licenses)
            and {"README.md", "src/poolhouse/__init__.py"}.issubset(paths)
            and all(any(path.startswith(root + "/") for path in paths) for root in FORCE_INCLUDE)
            and "app/poolhouse-node/build.rs" not in paths and "build" not in node.get("package", {})
            and all(not isinstance(value, dict) or "path" not in value
                    for key in ("dependencies", "build-dependencies") for value in node.get(key, {}).values()))


def fingerprint(checkout: Path, commit: str) -> str:
    """Hash every committed record except reviewed prose; unfamiliar build declarations refuse reuse."""
    if not runtime_git.COMMIT.fullmatch(commit) or runtime_git.git(checkout, "cat-file", "-t", commit) != "commit":
        raise ValueError("runtime inputs require a full source commit")
    records = runtime_git.git(checkout, "ls-tree", "-r", "-z", "--full-tree", commit).split("\0")
    kept, paths = [], set()
    for record in records:
        if not record:
            continue
        header, path = record.split("\t", 1)
        mode, kind, identifier = header.split(" ")
        if (mode not in {"100644", "100755"} or kind != "blob" or not runtime_git.COMMIT.fullmatch(identifier)
                or path in paths or path.startswith("/") or any(part in ("", ".", "..") for part in path.split("/"))):
            raise ValueError("runtime source has an unsupported tree record")
        paths.add(path)
        if mode != "100644" or not _documentation(path):
            kept.append(record.encode())
    if not paths or not _packaging(checkout, commit, paths):
        raise ValueError("runtime build input closure is not the maintained fixed builder")
    return hashlib.sha256(b"poolhouse/runtime-inputs/v1\0" + b"\0".join(kept)).hexdigest()


def same(checkout: Path, previous: str, wanted: str) -> bool:
    """Whether an ancestor and wanted revision have the same reviewed committed build inputs."""
    try:
        if (not runtime_git.COMMIT.fullmatch(previous) or not runtime_git.COMMIT.fullmatch(wanted)
                or runtime_git.git(checkout, "merge-base", previous, wanted) != previous):
            return False
        return fingerprint(checkout, previous) == fingerprint(checkout, wanted)
    except (runtime_git.DeployError, OSError, ValueError, TypeError, AttributeError):
        return False
