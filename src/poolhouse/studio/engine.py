"""Snapshot-based graph scheduling with durable child execution identities."""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from typing import Any

from .catalog import node_spec
from .contracts import validate_graph
from .store import StudioStore, digest

TERMINAL = frozenset({"done", "failed", "cancelled", "blocked", "interrupted"})


def artifact_fingerprints(value: Any) -> dict[str, str]:
    """Hash declared artifact bytes and refuse directory symlink escapes."""
    found = {}
    if isinstance(value, dict) and value.get("kind") in {"dataset", "model", "artifact"} and isinstance(value.get("path"), str):
        path = Path(value["path"]).resolve(strict=True)
        files = sorted(path.rglob("*")) if path.is_dir() else [path]
        root = path if path.is_dir() else path.parent
        entries = []
        for child in files:
            if not child.resolve().is_relative_to(root):
                raise ValueError("An artifact contains a link outside its declared directory.")
            checksum = hashlib.sha256()
            if child.is_file():
                with child.open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        checksum.update(chunk)
                entries.append([str(child.relative_to(root)), child.stat().st_size, checksum.hexdigest()])
            else:
                entries.append([str(child.relative_to(root)), "directory"])
        found[str(path)] = digest(entries)
    return found


def _artifacts(node: dict, values: dict, direction: str) -> dict:
    result = {}
    for port in node_spec(node["type"], node.get("version", 1))[direction]:
        if port["type"] in {"dataset", "model", "artifact"}:
            result.update(artifact_fingerprints(values.get(port["name"])))
    return result


class WorkflowEngine:
    """Advance independent graph branches through fixed execution adapters."""

    def __init__(self, store: StudioStore, runner: Any, adapters: Any, authorize: Any) -> None:
        self.store, self.runner, self.adapters, self.authorize = store, runner, adapters, authorize
        self.lock = threading.RLock()
        source = Path(__file__).parent.parent
        self.implementation = digest({str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in source.rglob("*.py")})
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.runner.studio_gate = self._admit_child
        self._recover()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="studio-scheduler")
        self._thread.start()

    def _admit_child(self, job: Any) -> bool:
        for run in self._all_runs():
            if run["state"] not in {"queued", "running"}:
                continue
            for node in run["graph"]["nodes"]:
                state = run["nodes"][node["id"]]
                if state.get("job_id") == job.id:
                    edges = [e for e in run["graph"]["edges"] if e["target"]["node"] == node["id"]]
                    inputs = {e["target"]["port"]: run["nodes"][e["source"]["node"]]["outputs"].get(e["source"]["port"])
                              for e in edges}
                    return self.authorize(run["principal"], node, inputs)
        return False

    def _recover(self) -> None:
        with self.store.lock:
            rows = self.store.graph.query("MATCH (n:Node) WHERE n.kind='studio.run' RETURN n.id AS id")
            for row in rows:
                run = self.store.graph.get_doc(row["id"])
                if run["state"] in {"queued", "running"}:
                    for node in run["nodes"].values():
                        if node["state"] == "running" and not self.runner.jobs.get(node.get("job_id")):
                            node.update(state="interrupted", error="The prior execution outcome is unknown; it was not replayed.")
                    self.store.put("run", run)

    def submit(self, principal: dict, workflow: dict, output_nodes: list[str], key: str) -> dict:
        if not self.authorize(principal, None, {}):
            raise PermissionError("Sign in again before running this workflow.")
        checked = validate_graph(workflow["graph"])
        if not checked["valid"]:
            raise ValueError(checked["errors"])
        graph = checked["graph"]
        graph = self._selected(graph, output_nodes)
        requirements = getattr(self.adapters, "validate", lambda nodes: [])(graph["nodes"])
        if requirements:
            raise ValueError(requirements)
        for node in graph["nodes"]:
            if not self.authorize(principal, node, {}):
                raise PermissionError("A workflow resource is no longer authorized.")
        run, _ = self.store.submit(principal, workflow, graph, output_nodes, key)
        self._wake.set()
        return run

    @staticmethod
    def _selected(graph: dict, outputs: list[str]) -> dict:
        nodes = {n["id"] for n in graph["nodes"]}
        if not outputs:
            return graph
        outputs = [expanded for ident in outputs for expanded in graph.get("variants", {}).get(ident, [ident])]
        if any(ident not in nodes and not any(n.startswith(ident + "/") for n in nodes) for ident in outputs):
            raise ValueError("Choose outputs that belong to this workflow.")
        outputs = [expanded for ident in outputs for expanded in
                   ([n for n in nodes if n.startswith(ident + "/")] if ident not in nodes else [ident])]
        if not set(outputs) <= nodes:
            raise ValueError("Choose outputs that belong to this workflow.")
        selected = set(outputs)
        while True:
            prior = set(selected)
            selected.update(e["source"]["node"] for e in graph["edges"] if e["target"]["node"] in selected)
            if prior == selected:
                break
        return {**graph, "nodes": [n for n in graph["nodes"] if n["id"] in selected],
                "edges": [e for e in graph["edges"] if e["target"]["node"] in selected]}

    def _all_runs(self) -> list[dict]:
        with self.store.lock:
            rows = self.store.graph.query("MATCH (n:Node) WHERE n.kind='studio.run' RETURN n.id AS id")
            return [self.store.graph.get_doc(row["id"]) for row in rows]

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(0.25)
            self._wake.clear()
            with self.lock:
                for run in self._all_runs():
                    if run["state"] in {"queued", "running"}:
                        try:
                            self.advance(run)
                        except (ValueError, OSError, RuntimeError) as exc:
                            run.update(state="interrupted", error=str(exc))
                            self.store.put("run", run)

    def advance(self, run: dict) -> dict:
        graph = run["graph"]
        run["state"] = "running"
        for node in graph["nodes"]:
            ident = node["id"]
            state = run["nodes"][ident]
            if state["state"] in TERMINAL:
                continue
            edges = [e for e in graph["edges"] if e["target"]["node"] == ident]
            predecessors = [run["nodes"][e["source"]["node"]] for e in edges]
            if any(p["state"] in TERMINAL - {"done"} for p in predecessors):
                state.update(state="blocked", error="An input did not complete.")
                continue
            if any(p["state"] != "done" for p in predecessors):
                continue
            if any(e["source"]["port"] not in run["nodes"][e["source"]["node"]]["outputs"] for e in edges):
                state.update(state="failed", error="An input node omitted its declared output.")
                continue
            inputs = {e["target"]["port"]: run["nodes"][e["source"]["node"]]["outputs"][e["source"]["port"]]
                      for e in edges}
            context = {"run_id": run["id"], "node_id": ident, "files_root": self.runner.files_root,
                       "output_dir": self.runner.files_root / "studio" / run["id"] / hashlib.sha256(ident.encode()).hexdigest()[:24],
                       "bindings": node.get("bindings", {}), "principal": run["principal"]}
            if not self.authorize(run["principal"], node, inputs):
                if state["state"] == "running" and state.get("job_id"):
                    self.runner.stop(state["job_id"])
                state.update(state="blocked", error="Authorization changed. Sign in and review this run.")
                continue
            try:
                self._node(run, node, state, inputs, context)
            except (ValueError, OSError, RuntimeError) as exc:
                state.update(state="failed", error=str(exc))
            self.store.put("run", run)
        states = {n["state"] for n in run["nodes"].values()}
        if states <= TERMINAL:
            run["state"] = ("done" if states <= {"done"} else "interrupted" if "interrupted" in states
                            else "failed")
            run["finished_at"] = time.time()
        self.store.put("run", run)
        return run

    def _node(self, run: dict, node: dict, state: dict, inputs: dict, context: dict) -> None:
        if state["state"] == "running":
            self.runner.snapshot()
            job = self.runner.jobs.get(state["job_id"])
            if job is None or job.state == "interrupted":
                state.update(state="interrupted", error="The execution outcome is unknown; no work was repeated.")
            elif job.state == "done":
                state.update(state="done", outputs=self.adapters.collect(node, job, context))
                self._completed(run, node, state, inputs)
            elif job.state in {"failed", "stopped"}:
                state.update(state="failed" if job.state == "failed" else "cancelled",
                             error=f"Child job {job.state}.")
            return
        context["output_dir"].mkdir(parents=True, exist_ok=True)
        spec = node_spec(node["type"], node.get("version", 1))
        state["fingerprint"] = digest({"node": node, "inputs": inputs, "implementation": [spec, self.implementation],
                                       "artifacts": _artifacts(node, inputs, "inputs")})
        if not spec.get("side_effects"):
            with self.store.lock:
                cache = self.store.graph.get_doc("studio.cache:" + digest({"principal": run["principal"], "key": state["fingerprint"]}))
            try:
                cache_valid = cache and cache["artifacts"] == _artifacts(node, cache["outputs"], "outputs")
            except OSError:
                cache_valid = False
            if cache_valid:
                state.update(state="done", outputs=cache["outputs"], reused_from=cache["run"])
                self._completed(run, node, state, inputs)
                return
        immediate = self.adapters.immediate(node, inputs, context) if node["type"] in {"dataset", "model", "prompt"} else None
        if immediate is not None:
            if spec.get("side_effects"):
                raise ValueError("Side effects require a durable child job.")
            state.update(state="done", outputs=immediate, output_digest=digest(immediate))
            self._completed(run, node, state, inputs)
            return
        supports = getattr(self.adapters, "supports_authorized", lambda node: False)
        if supports(node):
            state.update(state="running", job_id=None, dispatch_digest=digest({"node": node, "inputs": inputs}))
            self.store.put("run", run)
            try:
                outputs = self.adapters.execute_authorized(node, inputs, context)
            except (ValueError, OSError, RuntimeError) as exc:
                state.update(state="interrupted", error=str(exc))
                return
            state.update(state="done", outputs=outputs)
            self._completed(run, node, state, inputs)
            return
        command = self.adapters.prepare(node, inputs, context)
        job_id = "studio-" + digest({"run": run["id"], "node": node["id"]})[:40]
        state.update(state="running", job_id=job_id, dispatch_digest=digest(command),
                     started_at=time.time())
        self.store.put("run", run)
        self.runner.submit(node["type"], command["argv"], command["cwd"],
                           command.get("env"), job_id=job_id)

    def _completed(self, run: dict, node: dict, state: dict, inputs: dict) -> None:
        state["output_digest"] = digest(state["outputs"])
        expected = {port["name"] for port in node_spec(node["type"], node.get("version", 1))["outputs"]}
        if node.get("parameters", {}).get("mode") == "smoke":
            expected -= {"model", "checkpoint"}
        if not expected <= state["outputs"].keys():
            raise ValueError("The worker omitted a declared output.")
        state["artifacts"] = _artifacts(node, state["outputs"], "outputs")
        state["lineage"] = {"run": run["id"], "node": node["id"], "inputs": digest(inputs),
                            "implementation": node_spec(node["type"], node.get("version", 1))["implementation"]}
        if not node_spec(node["type"], node.get("version", 1)).get("side_effects"):
            with self.store.lock:
                self.store.graph.put_doc("studio.cache:" + digest({"principal": run["principal"], "key": state["fingerprint"]}),
                                         {"outputs": state["outputs"], "artifacts": state["artifacts"], "run": run["id"]})

    def cancel(self, principal: dict, ident: str) -> dict:
        with self.lock:
            run = self.store.get("run", ident, principal["scope"])
            if not principal.get("owner") and run["principal"]["id"] != principal["id"]:
                raise PermissionError("This run belongs to another session.")
            if not self.authorize(principal, None, {}):
                raise PermissionError("Sign in again before stopping this run.")
            if run["state"] in TERMINAL:
                return run
            for state in run["nodes"].values():
                if state["state"] == "running" and state.get("job_id"):
                    self.runner.stop(state["job_id"])
                if state["state"] not in TERMINAL:
                    state["state"] = "cancelled"
            run.update(state="cancelled", finished_at=time.time())
            return self.store.put("run", run)

    def resume(self, principal: dict, ident: str) -> dict:
        with self.lock:
            run = self.store.get("run", ident, principal["scope"])
            if not principal.get("owner") and run["principal"]["id"] != principal["id"]:
                raise PermissionError("This run belongs to another session.")
            if not self.authorize(principal, None, {}):
                raise PermissionError("Sign in again before resuming this run.")
            for node in run["graph"]["nodes"]:
                state = run["nodes"][node["id"]]
                if state["state"] == "interrupted":
                    child = self.runner.jobs.get(state.get("job_id"))
                    if not child or child.pid is not None or child.recovery_note != "Studio execution requires renewed authorization after restart.":
                        raise ValueError("Reconcile the interrupted child job before resuming; its side effects will not be repeated.")
                    if not self.authorize(principal, node, {}):
                        raise PermissionError("A resource in this run is no longer authorized.")
            run["principal"] = principal
            self.store.put("run", run)
            for state in run["nodes"].values():
                if state["state"] == "interrupted":
                    self.runner.reauthorize_studio(state["job_id"])
                    state["state"] = "running"
            for state in run["nodes"].values():
                if state["state"] == "blocked":
                    state.update(state="pending", error="")
            run.update(principal=principal, state="queued")
            self.store.put("run", run)
            self._wake.set()
            return run

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join()
