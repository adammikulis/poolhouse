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
        self.graph.query("CREATE NODE TABLE IF NOT EXISTS StudioRunActive(id STRING, PRIMARY KEY(id))")
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
            if kind == "run":
                self._run_indexes(row)
            return copy.deepcopy(row)

    def _run_indexes(self, row: dict) -> None:
        index = row["id"]
        if row["state"] in {"queued", "running", "interrupted"}:
            self.graph.query("MERGE (a:StudioRunActive {id:$id})", {"id": index})
        else:
            self.graph.query("MATCH (a:StudioRunActive {id:$id}) DELETE a", {"id": index})
        for node, state in row["nodes"].items():
            if state.get("job_id"):
                self.graph.put_doc("studio.child:" + state["job_id"], {"run": row["id"], "node": node})

    def tracked_runs(self) -> list[dict]:
        """Read unresolved run pointers, independent of completed history size."""
        with self.lock:
            indexes = self.graph.query("MATCH (a:StudioRunActive) RETURN a.id AS id")
            runs = []
            for index in indexes:
                ident = index["id"]
                key = ident
                run = self.graph.get_doc("studio.run:" + key)
                if run and run.get("id") == key and run.get("state") in {"queued", "running", "interrupted"}:
                    runs.append(run)
                else:
                    self.graph.put_doc("studio.index_error:" + ident, {"error": "Run index requires reconciliation.", "run": key})
            return runs

    def child_run(self, job: str) -> tuple[dict, dict] | None:
        """A child must match its exact durable run/node ownership before admission."""
        with self.lock:
            index = self.graph.get_doc("studio.child:" + job)
            if not isinstance(index, dict) or set(index) != {"run", "node"}:
                return None
            run = self.graph.get_doc("studio.run:" + str(index["run"]))
            if not run or run.get("id") != index["run"] or run.get("state") not in {"queued", "running"}:
                return None
            node = next((node for node in run["graph"]["nodes"] if node["id"] == index["node"]), None)
            if node is None or run["nodes"].get(index["node"], {}).get("job_id") != job:
                return None
            active = self.graph.query("MATCH (a:StudioRunActive {id:$id}) RETURN a.id AS id",
                                      {"id": run["id"]})
            return (run, node) if active else None

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

    def submit(self, principal: dict, workflow: dict, graph: dict, request: dict) -> tuple[dict, bool]:
        outputs, key, event = request["outputs"], request["key"], request.get("event")
        if not isinstance(key, str) or not key.strip() or len(key) > 256:
            raise ValueError("A submission identity is required.")
        fingerprint = digest({"graph": graph, "workflow": workflow["id"],
                              "revision": workflow["revision"], "outputs": sorted(outputs), "event": event})
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
            if event is not None:
                row["event"] = copy.deepcopy(event)
            self.put("run", row)
            self.graph.put_doc(identity, {"digest": fingerprint, "run": row["id"]})
            return row, True
