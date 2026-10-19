"""Local and pool package-library providers backed by the graph store."""
from __future__ import annotations

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


class PoolLibrary(LocalLibrary):
    """Store packages in the authenticated caller's pool-board scope."""
    def __init__(self, graph: GraphStore, board: str, *, lock=None):
        if not board:
            raise ValueError("A pool library requires an authenticated board")
        super().__init__(graph, f"pool:{board}", lock=lock)
