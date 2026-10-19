"""Durable workflows, execution snapshots and submission identities."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from poolhouse.graph import GraphStore


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


class Conflict(ValueError):
    def __init__(self, message: str, copy: dict | None = None) -> None:
        super().__init__(message)
        self.copy = copy


class StudioStore:
    """Persist Studio records in the graph under one transaction lock."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.graph = GraphStore(path)
        self.lock = threading.RLock()

    def close(self) -> None:
        with self.lock:
            self.graph.close()

    def records(self, kind: str, scope: str) -> list[dict]:
        with self.lock:
            rows = self.graph.query("MATCH (n:Node) WHERE n.kind=$kind RETURN n.id AS id",
                                    {"kind": "studio." + kind})
            values = [self.graph.get_doc(row["id"]) for row in rows]
            return [copy.deepcopy(row) for row in values if row and row.get("scope") == scope]

    def get(self, kind: str, ident: str, scope: str) -> dict:
        with self.lock:
            row = self.graph.get_doc(f"studio.{kind}:{ident}")
            if not row or row.get("scope") != scope:
                raise KeyError("No such Studio " + kind + ".")
            return copy.deepcopy(row)

    def put(self, kind: str, row: dict) -> dict:
        with self.lock, self.graph.transaction():
            key = f"studio.{kind}:{row['id']}"
            self.graph.upsert_node({"id": key, "kind": "studio." + kind,
                                    "label": row.get("title", row["id"])})
            self.graph.put_doc(key, row)
            return copy.deepcopy(row)

    def save_workflow(self, scope: str, title: str, graph: dict, *,
                      ident: str | None = None, revision: int | None = None) -> dict:
        with self.lock, self.graph.transaction():
            current = self.get("workflow", ident, scope) if ident else None
            conflict = current is not None and revision != current["revision"]
            row = {"id": uuid.uuid4().hex if conflict or not ident else ident,
                   "scope": scope, "title": title + (" (recovered copy)" if conflict else ""),
                   "graph": copy.deepcopy(graph), "revision": 1 if conflict or not current else current["revision"] + 1,
                   "updated_at": time.time()}
            self.put("workflow", row)
        if conflict:
            raise Conflict("This workflow changed elsewhere. Your edits were saved as a recoverable copy.", row)
        return row

    def submit(self, principal: dict, workflow: dict, graph: dict, outputs: list[str], key: str) -> tuple[dict, bool]:
        if not isinstance(key, str) or not key.strip() or len(key) > 256:
            raise ValueError("A submission identity is required.")
        fingerprint = digest({"graph": graph, "workflow": workflow["id"],
                              "revision": workflow["revision"], "outputs": sorted(outputs)})
        identity = "studio.submission:" + digest({"principal": principal, "key": key})
        with self.lock, self.graph.transaction():
            prior = self.graph.get_doc(identity)
            if prior:
                if prior["digest"] != fingerprint:
                    raise Conflict("This submission identity already names a different experiment.")
                return self.get("run", prior["run"], principal["scope"]), False
            row = {"id": uuid.uuid4().hex, "scope": principal["scope"],
                   "principal": copy.deepcopy(principal), "workflow_id": workflow["id"],
                   "revision": workflow["revision"], "graph": copy.deepcopy(graph),
                   "output_nodes": outputs, "state": "queued", "created_at": time.time(),
                   "nodes": {node["id"]: {"state": "pending", "outputs": {}}
                             for node in graph["nodes"]}}
            self.put("run", row)
            self.graph.put_doc(identity, {"digest": fingerprint, "run": row["id"]})
            return row, True
