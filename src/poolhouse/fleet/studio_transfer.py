"""Digest-verified immutable resource staging for Studio device execution."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import threading
from pathlib import Path, PurePosixPath
from typing import Any

from poolhouse.files import promote
from poolhouse.studio.catalog import is_model_type

CHUNK_BYTES = 256 * 1024
MANIFEST_BYTES = 1024 * 1024
_DIGEST = re.compile(r"[a-f0-9]{64}")
_DISPATCH = re.compile(r"[a-f0-9]{32}(?:[a-f0-9]{32})?")
_METADATA = {"recipe": str, "framework": str, "quantization": str, "decision": bool,
             "gym": bool, "rows": int, "count": int, "format": str}


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _name(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("Choose a declared resource filename.")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in (".", "..") for part in path.parts):
        raise ValueError("Choose a declared relative resource filename.")
    return path.parts


def _directory(path: Path, *, create: bool = False) -> int:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Resource directories require canonical absolute paths.")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        return descriptor
    except OSError:
        os.close(descriptor)
        raise


def _open(root: Path, parts: tuple[str, ...], flags: int, *, parents: bool = False) -> int:
    directory = _directory(root)
    try:
        for part in parts[:-1]:
            if parents:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, mode=0o700, dir_fd=directory)
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = following
        descriptor = os.open(parts[-1], flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(descriptor)
            raise ValueError("Resource files must be regular files without links.")
        return descriptor
    finally:
        os.close(directory)


def _checksum(root: Path, name: str) -> tuple[int, str]:
    descriptor = _open(root, _name(name), os.O_RDONLY)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        digest = hashlib.sha256()
        while chunk := stream.read(CHUNK_BYTES):
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("The resource changed while it was being read.")
    return after.st_size, digest.hexdigest()


def manifest(path: Path, kind: str, *, gym: bool = False) -> dict:
    """Describe explicit regular resource files with canonical sizes and content digests."""
    path = Path(path).absolute()
    if ".." in path.parts:
        raise ValueError("Resource paths cannot traverse parent directories.")
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or kind not in {"dataset", "model", "artifact"}:
        raise ValueError("Choose a declared regular resource.")
    directory = stat.S_ISDIR(info.st_mode)
    root = path if directory else path.parent
    names = [child.relative_to(root).as_posix() for child in sorted(root.rglob("*"))
             if not child.is_dir() or child.is_symlink()] if directory else [path.name]
    if gym:
        if directory:
            raise ValueError("Choose a recorded Gym policy file.")
        names.append("policy-manifest.json")
    files = []
    for name in sorted(set(names)):
        size, digest = _checksum(root, name)
        files.append({"name": name, "size": size, "sha256": digest})
    value = {"kind": kind, "shape": "directory" if directory else "file", "entrypoint": "" if directory else path.name,
             "bytes": sum(file["size"] for file in files), "files": files}
    return {"id": _digest(value), **value}


def checked_manifest(value: Any, max_bytes: int) -> dict:
    """Validate the exact immutable resource manifest and declared transfer total."""
    fields = {"id", "kind", "shape", "entrypoint", "bytes", "files"}
    if not isinstance(value, dict) or set(value) != fields or len(json.dumps(value).encode()) > MANIFEST_BYTES:
        raise ValueError("Invalid resource manifest fields or size.")
    if value["kind"] not in {"dataset", "model", "artifact"} or value["shape"] not in {"file", "directory"}:
        raise ValueError("Invalid resource type.")
    if type(value["bytes"]) is not int or not 0 <= value["bytes"] <= max_bytes or not isinstance(value["files"], list):
        raise ValueError("The declared resource exceeds available transfer space.")
    names, total = [], 0
    for file in value["files"]:
        if not isinstance(file, dict) or set(file) != {"name", "size", "sha256"}:
            raise ValueError("Invalid resource file fields.")
        _name(file["name"])
        if type(file["size"]) is not int or file["size"] < 0 or not isinstance(file["sha256"], str) or not _DIGEST.fullmatch(file["sha256"]):
            raise ValueError("Invalid resource file size or digest.")
        names.append(file["name"])
        total += file["size"]
    if any(after.startswith(before + "/") for before, after in zip(sorted(names), sorted(names)[1:], strict=False)):
        raise ValueError("Resource files cannot also be parent directories.")
    if names != sorted(set(names)) or total != value["bytes"]:
        raise ValueError("Resource filenames and total must match the declared manifest.")
    if value["shape"] == "file" and value["entrypoint"] not in names:
        raise ValueError("The resource entrypoint is missing.")
    if value["shape"] == "directory" and value["entrypoint"] != "":
        raise ValueError("Directory resources have no file entrypoint.")
    identity = {key: item for key, item in value.items() if key != "id"}
    if not isinstance(value["id"], str) or value["id"] != _digest(identity):
        raise ValueError("Resource manifest digest does not match.")
    return value


def resource_handle(value: dict, resource: dict) -> dict:
    """Return the declared resource identity and typed scalar execution metadata."""
    result = {"kind": resource["kind"], "resource": resource["id"]}
    for name, kind in _METADATA.items():
        if name in value:
            item = value[name]
            if type(item) is not kind:
                raise ValueError("Invalid resource metadata.")
            result[name] = item
    return result


def resource_ports(spec: dict, direction: str) -> set[str]:
    """Return declared ports carrying datasets, models or artifacts."""
    return {port["name"] for port in spec[direction]
            if port["type"] in {"dataset", "artifact"} or is_model_type(port["type"])}


class TransferStore:
    """Stage bounded resources within one authenticated source and dispatch namespace."""
    def __init__(self, root: Path, *, max_bytes: int, authorize):
        if type(max_bytes) is not int or max_bytes < 0 or not callable(authorize):
            raise ValueError("Resource staging requires current authorization and available space.")
        self.root = Path(root).absolute()
        os.close(_directory(self.root, create=True))
        self.max_bytes, self.authorize = max_bytes, authorize
        self.lock = threading.RLock()

    def _prefix(self, source: str, dispatch: str) -> tuple[str, str]:
        if not isinstance(source, str) or not source or not isinstance(dispatch, str) or not _DISPATCH.fullmatch(dispatch):
            raise ValueError("Invalid authenticated transfer identity.")
        if self.authorize(source, dispatch) is False:
            raise PermissionError("This resource transfer is no longer authorized.")
        return hashlib.sha256(source.encode()).hexdigest(), dispatch

    def directory(self, source: str, dispatch: str) -> Path:
        prefix = self._prefix(source, dispatch)
        directory = self.root.joinpath(*prefix, "out")
        os.close(_directory(directory, create=True))
        return directory

    def _parts(self, source: str, dispatch: str, resource: str) -> tuple[str, ...]:
        if not isinstance(resource, str) or not _DIGEST.fullmatch(resource):
            raise ValueError("Invalid resource identity.")
        return (*self._prefix(source, dispatch), "resources", resource)

    def _manifest(self, parts: tuple[str, ...]) -> dict:
        descriptor = _open(self.root, (*parts, "manifest.json"), os.O_RDONLY)
        with os.fdopen(descriptor, "rb") as stream:
            data = stream.read(MANIFEST_BYTES + 1)
        if len(data) > MANIFEST_BYTES:
            raise ValueError("Resource manifest exceeds its size limit.")
        return checked_manifest(json.loads(data), self.max_bytes)

    def begin(self, source: str, dispatch: str, value: dict) -> dict:
        checked = checked_manifest(value, self.max_bytes)
        with self.lock:
            parts = self._parts(source, dispatch, checked["id"])
            try:
                existing = self._manifest(parts)
                if existing != checked:
                    raise ValueError("A resource identity already has different content.")
            except FileNotFoundError:
                reserved = sum(self._manifest(tuple(path.relative_to(self.root).parts[:-1]))["bytes"]
                               for path in self.root.glob("*/*/resources/*/manifest.json"))
                if reserved + checked["bytes"] > self.max_bytes or checked["bytes"] > shutil.disk_usage(self.root).free:
                    raise ValueError("Declared transfers exceed available staging space.") from None
                descriptor = _open(self.root, (*parts, "manifest.part"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, parents=True)
                with os.fdopen(descriptor, "w") as stream:
                    json.dump(checked, stream, sort_keys=True, separators=(",", ":"))
                    stream.flush()
                    os.fsync(stream.fileno())
                directory = _directory(self.root.joinpath(*parts))
                try:
                    promote("manifest.part", "manifest.json", directory_fd=directory)
                finally:
                    os.close(directory)
            return {"id": checked["id"], "bytes": checked["bytes"]}

    def put(self, source: str, dispatch: str, resource: str, **chunk) -> dict:
        name, offset, data = chunk["name"], chunk["offset"], chunk["data"]
        if type(offset) is not int or offset < 0 or not isinstance(data, bytes) or len(data) > CHUNK_BYTES:
            raise ValueError("Invalid resource chunk offset or size.")
        with self.lock:
            parts = self._parts(source, dispatch, resource)
            declared = self._manifest(parts)
            file = next((file for file in declared["files"] if file["name"] == name), None)
            if file is None or offset + len(data) > file["size"]:
                raise ValueError("This chunk is outside the declared resource.")
            complete = self.root.joinpath(*parts, "files")
            if complete.exists():
                descriptor = _open(self.root, (*parts, "files", *_name(name)), os.O_RDONLY)
                with os.fdopen(descriptor, "rb") as stream:
                    stream.seek(offset)
                    if stream.read(len(data)) != data:
                        raise ValueError("A completed resource cannot be changed.")
                return {"offset": file["size"]}
            filename = (*parts, "incoming", *_name(name))
            descriptor = _open(self.root, filename, os.O_RDWR | os.O_CREAT, parents=True)
            with os.fdopen(descriptor, "r+b") as stream:
                size = os.fstat(stream.fileno()).st_size
                if offset > size:
                    raise ValueError("Resource chunks must arrive in order.")
                if offset < size:
                    overlap = min(len(data), size - offset)
                    stream.seek(offset)
                    if stream.read(overlap) != data[:overlap]:
                        raise ValueError("A repeated resource chunk has different content.")
                    if overlap == len(data):
                        return {"offset": size}
                    data = data[overlap:]
                stream.seek(size)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                return {"offset": stream.tell()}

    def finish(self, source: str, dispatch: str, resource: str) -> dict:
        with self.lock:
            parts = self._parts(source, dispatch, resource)
            value = self._manifest(parts)
            base = self.root.joinpath(*parts)
            final = base / "files"
            staging = final if final.exists() else base / "incoming"
            if staging.is_symlink():
                raise ValueError("Resource staging cannot follow links.")
            for file in value["files"]:
                if _checksum(staging, file["name"]) != (file["size"], file["sha256"]):
                    raise ValueError("Resource content does not match its declared size and digest.")
            if staging != final:
                if not value["files"]:
                    os.close(_directory(staging, create=True))
                descriptor = _directory(base)
                try:
                    promote("incoming", "files", directory_fd=descriptor)
                finally:
                    os.close(descriptor)
            return value

    def inputs(self, source: str, dispatch: str, inputs: dict) -> dict:
        self._prefix(source, dispatch)
        hydrated = {}
        for name, value in inputs.items():
            if isinstance(value, dict) and "resource" not in value and ("path" in value or value.get("kind") in {"dataset", "model", "artifact"}):
                raise ValueError("Remote resource inputs require a verified staged handle.")
            if not isinstance(value, dict) or "resource" not in value:
                hydrated[name] = value
                continue
            if set(value) - {"kind", "resource", *_METADATA}:
                raise ValueError("Resource handles contain only declared execution metadata.")
            resource = self.finish(source, dispatch, value["resource"])
            checked = resource_handle(value, resource)
            if value.get("kind") != resource["kind"]:
                raise ValueError("Resource handle type does not match its manifest.")
            path = self.root.joinpath(*self._parts(source, dispatch, resource["id"]), "files")
            checked.pop("resource")
            checked["path"] = str(path / resource["entrypoint"] if resource["shape"] == "file" else path)
            hydrated[name] = checked
        return hydrated

    def result(self, source: str, dispatch: str, outputs: dict, spec: dict) -> dict:
        own = self.directory(source, dispatch)
        if set(outputs) - {port["name"] for port in spec["outputs"]}:
            raise ValueError("Remote outputs must match the declared node ports.")
        resources, safe = [], dict(outputs)
        for name in resource_ports(spec, "outputs"):
            value = outputs.get(name)
            if value is None:
                continue
            path = Path(value["path"]).absolute()
            if ".." in path.parts or not path.is_relative_to(own):
                raise ValueError("Remote outputs must belong to this dispatch.")
            resource = manifest(path, value["kind"], gym=value.get("gym", False))
            self.begin(source, dispatch, resource)
            for file in resource["files"]:
                root = path if resource["shape"] == "directory" else path.parent
                descriptor = _open(root, _name(file["name"]), os.O_RDONLY)
                with os.fdopen(descriptor, "rb") as stream:
                    offset = 0
                    while data := stream.read(CHUNK_BYTES):
                        self.put(source, dispatch, resource["id"], name=file["name"], offset=offset, data=data)
                        offset += len(data)
                    if offset == 0:
                        self.put(source, dispatch, resource["id"], name=file["name"], offset=0, data=b"")
            self.finish(source, dispatch, resource["id"])
            safe[name] = resource_handle(value, resource)
            resources.append(resource)
        return {"outputs": safe, "resources": resources}

    def discard(self, source: str, dispatch: str) -> dict:
        prefix = self._prefix(source, dispatch)
        with self.lock:
            try:
                directory = _directory(self.root.joinpath(*prefix))
            except FileNotFoundError:
                return {"discarded": True}
            try:
                if not shutil.rmtree.avoids_symlink_attacks:
                    raise ValueError("Resource cleanup requires descriptor-safe directory removal.")
                with contextlib.suppress(FileNotFoundError):
                    shutil.rmtree("resources", dir_fd=directory)
            finally:
                os.close(directory)
        return {"discarded": True}

    def read(self, source: str, dispatch: str, resource: str, **request) -> bytes:
        name, offset, size = request["name"], request.get("offset", 0), request.get("size", CHUNK_BYTES)
        if type(offset) is not int or offset < 0 or type(size) is not int or not 0 < size <= CHUNK_BYTES:
            raise ValueError("Invalid resource read range.")
        parts = self._parts(source, dispatch, resource)
        value = self._manifest(parts)
        file = next((file for file in value["files"] if file["name"] == name), None)
        if file is None or offset > file["size"]:
            raise ValueError("Choose a declared resource range.")
        descriptor = _open(self.root, (*parts, "files", *_name(name)), os.O_RDONLY)
        with os.fdopen(descriptor, "rb") as stream:
            stream.seek(offset)
            return stream.read(min(size, file["size"] - offset))
