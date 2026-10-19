"""Local and pool package-library providers backed by the graph store."""
from __future__ import annotations

import json
from threading import RLock
from typing import Protocol

from poolhouse.graph import GraphStore
from poolhouse.studio.packages import import_package


class LibraryProvider(Protocol):
    """Storage and discovery operations for inert workflow packages."""
    def publish(self, package: dict) -> dict: ...
    def get(self, package_id: str, version: str) -> dict | None: ...
    def search(self, query: str = "", tags: list[str] | None = None) -> list[dict]: ...


class LocalLibrary:
    """Persist immutable package versions in a scoped graph library."""
    def __init__(self, graph: GraphStore, scope: str = "local", *, lock=None):
        self.graph = graph
        self.scope = scope
        if lock is None:
            lock = getattr(graph, "_studio_library_lock", None)
            if lock is None:
                lock = RLock()
                graph._studio_library_lock = lock
        self.lock = lock

    def _key(self, package_id: str, version: str) -> str:
        return f"studio.library:{self.scope}:{package_id}@{version}"

    def publish(self, package: dict) -> dict:
        checked = import_package(package)
        key = self._key(checked["id"], checked["version"])
        with self.lock:
            existing = self.graph.get_doc(key)
            if existing and existing["digest"] != checked["digest"]:
                raise ValueError("This immutable package version already has different content")
            if not existing:
                self.graph.put_doc(key, checked)
        return checked

    def get(self, package_id: str, version: str) -> dict | None:
        with self.lock:
            result = self.graph.get_doc(self._key(package_id, version))
        return import_package(result) if result else None

    def search(self, query: str = "", tags: list[str] | None = None) -> list[dict]:
        prefix = f"studio.library:{self.scope}:"
        with self.lock:
            values = [import_package(value) for key, value in self.graph.docs().items() if key.startswith(prefix)]
        return [value for value in values if query.casefold() in " ".join(
            [value["name"], value["description"], *value["tags"]]).casefold()
            and set(tags or []).issubset(value["tags"])]


class PoolLibrary:
    """Publish inert packages through an authenticated pool board's replicated log."""
    def __init__(self, session, *, lock=None):
        if not session.board:
            raise ValueError("A pool library requires an authenticated board session")
        self.session = session
        self.lock = lock or RLock()

    def _packages(self) -> list[dict]:
        versions = {}
        self.conflicts = set()
        self.unavailable = []
        for entry in self.session.read_all(kind="note"):
            if entry.fields.get("nkind") != "studio-package":
                continue
            try:
                package = import_package(json.loads(entry.fields.get("body", "")))
            except (ValueError, TypeError, KeyError, RecursionError):
                self.unavailable.append({"reason": "Invalid package record"})
                continue
            key = (package["id"], package["version"])
            previous = versions.get(key)
            if previous and previous["digest"] != package["digest"]:
                self.conflicts.add(key)
            versions[key] = package
        self.unavailable.extend({"id": key[0], "version": key[1], "reason": "Conflicting immutable package version"}
                                for key in sorted(self.conflicts))
        return [package for key, package in versions.items() if key not in self.conflicts]

    def publish(self, package: dict) -> dict:
        checked = import_package(package)
        with self.lock:
            existing = self.get(checked["id"], checked["version"])
            if existing and existing["digest"] != checked["digest"]:
                raise ValueError("This immutable package version already has different content")
            if not existing:
                self.session.note_add("studio-package", f"{checked['id']}@{checked['version']}",
                                      json.dumps(checked, sort_keys=True, separators=(",", ":")))
        return checked

    def get(self, package_id: str, version: str) -> dict | None:
        with self.lock:
            packages = self._packages()
            if (package_id, version) in self.conflicts:
                raise ValueError("Conflicting immutable package version exists on this board")
            return next((package for package in packages
                         if package["id"] == package_id and package["version"] == version), None)

    def search(self, query: str = "", tags: list[str] | None = None) -> list[dict]:
        with self.lock:
            packages = self._packages()
        return [package for package in packages if query.casefold() in " ".join(
            [package["name"], package["description"], *package["tags"]]).casefold()
            and set(tags or []).issubset(package["tags"])]
