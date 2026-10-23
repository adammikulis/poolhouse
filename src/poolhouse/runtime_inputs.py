"""Compare committed runtime inputs without relabelling an existing build's source revision."""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

from poolhouse import runtime_git

DOCUMENTS = frozenset({"docs/native-plan-macos.md", "docs/native-plan-windows-linux.md", "docs/node-contract.md"})
FORCE_INCLUDE = {"contracts": "poolhouse/contracts/_data", "patches": "poolhouse/serve/_patches"}
# The independently reviewed fixed builder closure. Source changes disable this optimization
# until their closure is reviewed and this digest is updated; this is never learned from a runtime.
AUDITED_BUILD = "12990e5e11bee33cd9d185d5c78e04a78fc76eea59794194ace705d2e966c6b1"


def _documentation(path: str) -> bool:
    return path in DOCUMENTS or (path.startswith("docs/notes/") and path.endswith(".md"))


def _dependencies(table: dict) -> bool:
    for key, value in table.items():
        if key in {"patch", "replace"} and value:
            return False
        if key in {"dependencies", "build-dependencies", "dev-dependencies"}:
            if not isinstance(value, dict):
                return False
            for dependency in value.values():
                if not isinstance(dependency, str) and (not isinstance(dependency, dict)
                        or "version" not in dependency
                        or set(dependency) - {"version", "features", "default-features", "optional", "package", "registry"}):
                    return False
        elif isinstance(value, dict) and not _dependencies(value):
            return False
    return True


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
    workspace = (tomllib.loads(runtime_git.git(checkout, "show", f"{commit}:app/Cargo.toml"))
                 if "app/Cargo.toml" in paths else {})
    library, binaries = node.get("lib", {}), node.get("bin", [])
    return (system == {"requires": ["hatchling"], "build-backend": "hatchling.build"}
            and set(hatch) == {"build"} and set(build) == {"targets"}
            and not (set(wheel) - {"packages", "exclude", "force-include"})
            and wheel.get("packages") == ["src/poolhouse"] and wheel.get("force-include") == FORCE_INCLUDE
            and metadata.get("readme") == "README.md" and isinstance(metadata.get("license"), str)
            and not metadata.get("dynamic")
            and isinstance(licenses, list) and all(isinstance(path, str) and "/" not in path
                                                  and path in paths and not any(char in path for char in "*?[") for path in licenses)
            and {"README.md", "src/poolhouse/__init__.py"}.issubset(paths)
            and all(any(path.startswith(root + "/") for path in paths) for root in FORCE_INCLUDE)
            and "app/poolhouse-node/build.rs" not in paths and "build" not in node.get("package", {})
            and not (set(library) - {"name", "path"}) and library.get("path", "src/lib.rs") == "src/lib.rs"
            and isinstance(binaries, list) and all(isinstance(binary, dict) and not (set(binary) - {"name", "path"})
                    and binary.get("name") == "poolhouse-node" and binary.get("path", "src/main.rs") == "src/main.rs"
                    for binary in binaries)
            and _dependencies(node) and _dependencies(workspace))


def _tree(checkout: Path, commit: str) -> list[tuple[str, str, str]]:
    if not runtime_git.COMMIT.fullmatch(commit) or runtime_git.git(checkout, "cat-file", "-t", commit) != "commit":
        raise ValueError("runtime inputs require a full source commit")
    records = runtime_git.git(checkout, "ls-tree", "-r", "-z", "--full-tree", commit).split("\0")
    found, paths = [], set()
    for record in records:
        if not record:
            continue
        header, path = record.split("\t", 1)
        mode, kind, identifier = header.split(" ")
        if (mode not in {"100644", "100755"} or kind != "blob" or not runtime_git.COMMIT.fullmatch(identifier)
                or path in paths or path.startswith("/") or any(part in ("", ".", "..") for part in path.split("/"))):
            raise ValueError("runtime source has an unsupported tree record")
        paths.add(path)
        found.append((record, path, mode))
    return found


def _build_record(path: str) -> bool:
    return (path != "src/poolhouse/runtime_inputs.py"
            and (path.startswith(("src/poolhouse/", "app/", "packaging/", "scripts/", ".cargo/"))
                 or path in {"pyproject.toml", "Cargo.toml", "Cargo.lock", ".gitattributes", ".gitmodules"}))


def _build_digest(records: list[tuple[str, str, str]]) -> str:
    raw = b"\0".join(record.encode() for record, path, _mode in records if _build_record(path))
    return hashlib.sha256(b"poolhouse/runtime-builder/v1\0" + raw).hexdigest()


def builder_fingerprint(checkout: Path, commit: str) -> str:
    """The source digest an independent review compares with the explicit audited builder pin."""
    return _build_digest(_tree(checkout, commit))


def fingerprint(checkout: Path, commit: str) -> str:
    """Hash every committed record except reviewed prose; unfamiliar build closures refuse reuse."""
    records = _tree(checkout, commit)
    paths = {path for _record, path, _mode in records}
    if (not paths or _build_digest(records) != AUDITED_BUILD or not _packaging(checkout, commit, paths)):
        raise ValueError("runtime build input closure is not the maintained fixed builder")
    kept = [record.encode() for record, path, mode in records if mode != "100644" or not _documentation(path)]
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
