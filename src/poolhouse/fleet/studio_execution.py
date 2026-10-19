"""Target-owned admission and lifecycle for typed Studio device work."""

from __future__ import annotations

import hashlib
import re
import shutil
import threading
import time
from pathlib import Path

from poolhouse.studio.adapters import Adapters
from poolhouse.studio.catalog import is_model_type, node_spec
from poolhouse.studio.contracts import _finite, _parameters
from poolhouse.studio.store import Conflict, StudioStore, digest

from .managed_compute import process_environment
from .studio_references import validate_models
from .studio_transfer import TransferStore


def checked_node(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) - {"id", "type", "version", "parameters"} or not _finite(value):
        raise ValueError("Device work accepts a fixed node version and its declared parameters.")
    spec = node_spec(value.get("type"), value.get("version", 1))
    if spec["type"] in {"dataset", "model", "prompt", "tools.call"}:
        raise ValueError("This node runs in the originating workspace.")
    node = {"id": value.get("id", "node"), "type": spec["type"], "version": spec["version"],
            "parameters": _parameters(value.get("parameters", {}), spec["parameters"]), "bindings": {}}
    if not isinstance(node["id"], str) or not node["id"] or len(node["id"]) > 512:
        raise ValueError("Invalid node identity.")
    if ":" in str(node["parameters"].get("environment", "")):
        raise ValueError("Choose an installed registered Gym environment.")
    return node


def checked_inputs(spec: dict, inputs: dict) -> None:
    for port in spec["inputs"]:
        if port["name"] not in inputs:
            continue
        value = inputs[port["name"]]
        kind = "model" if is_model_type(port["type"]) else port["type"]
        if kind in {"model", "dataset", "artifact"}:
            if not isinstance(value, dict) or value.get("kind") != kind or not isinstance(value.get("resource"), str):
                raise ValueError("The staged resource kind must match its declared input port.")
        elif isinstance(value, dict) and ("resource" in value or ("path" in value and value.get("kind") in {"model", "dataset", "artifact"})):
            raise ValueError("Non-resource input ports cannot receive staged resource handles.")


class DeviceExecutions:
    """Persist source-scoped dispatches and use the target's maintained job runner."""

    def __init__(self, ui, admitted, origin_authorized) -> None:
        self.ui, self.admitted, self.origin_authorized = ui, admitted, origin_authorized
        self.store = StudioStore(Path(ui.root) / "studio-executions.ladybug")
        root = ui.runner.files_root / "studio-device-resources"
        root.mkdir(parents=True, exist_ok=True)
        self.transfer = TransferStore(root, max_bytes=shutil.disk_usage(root).free, authorize=admitted)
        self.adapters = Adapters(ui.runner, process_environment=process_environment)
        self.lock = threading.RLock()
        source = Path(__file__).parent.parent
        self.code_fingerprint = digest({str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
                                        for path in source.rglob("*.py")})
        ui.runner.studio_device_gate = self._gate

    def _get(self, source: str, request: dict) -> dict:
        if not self.admitted(source, request.get("dispatch_id", "")):
            raise PermissionError("This device is no longer authorized to run work here.")
        row = self.store.get("execution", request.get("dispatch_id", ""), source)
        if request.get("spec_digest") != row["spec_digest"]:
            raise Conflict("The dispatch identity names a different node snapshot.")
        return row

    def validate(self, nodes: list) -> dict:
        checked = [checked_node(node) for node in nodes]
        return {"errors": self.adapters.validate(checked),
                "implementations": {node["id"]: self._implementation(node) for node in checked}}

    def _implementation(self, node: dict) -> str:
        dependency = getattr(self.adapters, "dependency_fingerprint", lambda node: "")(node)
        return digest({"code": self.code_fingerprint, "dependency": dependency})

    def submit(self, source: str, request: dict) -> dict:
        if set(request) != {"dispatch_id", "spec_digest", "run_id", "node", "inputs", "resources", "implementation"} or not _finite(request):
            raise ValueError("Choose a typed Studio dispatch.")
        ident = request["dispatch_id"]
        if not re.fullmatch(r"[a-f0-9]{64}", ident) or not re.fullmatch(r"[a-f0-9]{64}", request["spec_digest"]):
            raise ValueError("Invalid dispatch identity or snapshot digest.")
        if not self.admitted(source, ident) or not self.origin_authorized(source, ident, request["spec_digest"]):
            raise PermissionError("This execution is no longer authorized.")
        node = checked_node(request["node"])
        if request["implementation"] != self._implementation(node):
            raise ValueError("The selected device runtime changed. Validate this workflow again before running it.")
        dependencies = self.adapters.validate([node])
        if dependencies:
            raise ValueError(dependencies)
        with self.lock:
            prior = self.store.records("execution", source)
            existing = next((row for row in prior if row["id"] == ident), None)
            fingerprint = digest(request)
            if existing:
                if existing["request_digest"] != fingerprint:
                    raise Conflict("The dispatch identity names different execution content.")
                return self.status(source, request)
            inputs = self._inputs(source, ident, request, node)
            context = self._context(source, ident)
            command = self.adapters.prepare(node, inputs, context)
            row = {"id": ident, "scope": source, "node": node, "inputs": request["inputs"],
                   "spec_digest": request["spec_digest"], "request_digest": fingerprint,
                   "state": "dispatching", "job_id": "studio-remote-" + digest({"source": source, "id": ident})[:40],
                   "implementation": request["implementation"],
                   "command_digest": digest(command), "created_at": time.time()}
            self.store.put("execution", row)
            self.ui.runner.submit(node["type"], command["argv"], command["cwd"], command.get("env"), job_id=row["job_id"])
            row["state"] = "queued"
            self.store.put("execution", row)
            return self._public(row)

    def _inputs(self, source: str, ident: str, request: dict, node: dict) -> dict:
        spec = node_spec(node["type"], node["version"])
        ports = {p["name"] for p in spec["inputs"]}
        required = {p["name"] for p in spec["inputs"] if p.get("required", True)}
        if not isinstance(request["inputs"], dict) or not required <= request["inputs"].keys() or not set(request["inputs"]) <= ports:
            raise ValueError("The node inputs do not match its declared interface.")
        checked_inputs(spec, request["inputs"])
        manifests = request["resources"]
        if not isinstance(manifests, list):
            raise ValueError("Resources require completed content manifests.")
        for manifest in manifests:
            completed = self.transfer.finish(source, ident, manifest["id"])
            if completed != manifest:
                raise ValueError("A staged resource differs from the dispatched manifest.")
        hydrated = self.transfer.inputs(source, ident, request["inputs"])
        validate_models(request["inputs"], manifests, hydrated)
        if node["type"] == "train.tool-calls" and "model" not in hydrated:
            raise ValueError("Select an explicit staged base model for device training.")
        return hydrated

    def _context(self, source: str, ident: str) -> dict:
        return {"run_id": ident, "node_id": "execution", "files_root": self.ui.runner.files_root,
                "output_dir": self.transfer.directory(source, ident), "bindings": {}}

    def _gate(self, job) -> bool:
        with self.store.lock:
            rows = self.store.graph.query("MATCH (n:Node) WHERE n.kind='studio.execution' RETURN n.id AS id")
            executions = [self.store.graph.get_doc(row["id"]) for row in rows]
        for row in executions:
            if row["job_id"] == job.id:
                try:
                    return (row["implementation"] == self._implementation(row["node"])
                            and self.admitted(row["scope"], row["id"])
                            and self.origin_authorized(row["scope"], row["id"], row["spec_digest"]))
                except (ValueError, OSError, RuntimeError):
                    return False
        return False

    def status(self, source: str, request: dict) -> dict:
        with self.lock:
            row = self._get(source, request)
            self.ui.runner.snapshot()
            job = self.ui.runner.jobs.get(row["job_id"])
            if job is None:
                row.update(state="interrupted", error="The execution outcome is unknown; it was not repeated.")
            else:
                row["state"] = {"stopped": "cancelled"}.get(job.state, job.state)
            self.store.put("execution", row)
            return self._public(row)

    def result(self, source: str, request: dict) -> dict:
        with self.lock:
            row = self._get(source, request)
            if not self.origin_authorized(source, row["id"], row["spec_digest"]):
                raise PermissionError("The originating session no longer permits reading this result.")
            if self.status(source, request)["state"] != "done":
                raise ValueError("The device execution has not completed.")
            outputs = self.adapters.collect(row["node"], self.ui.runner.jobs[row["job_id"]], self._context(source, row["id"]))
            result = self.transfer.result(source, row["id"], outputs, node_spec(row["node"]["type"], row["node"]["version"]))
            return {**self._public(row), **result, "node": {k: v for k, v in row["node"].items() if k != "bindings"}}

    def cancel(self, source: str, request: dict) -> dict:
        with self.lock:
            row = self._get(source, request)
            self.ui.runner.stop(row["job_id"])
            return self.status(source, request)

    def resume(self, source: str, request: dict) -> dict:
        with self.lock:
            row = self._get(source, request)
            if not self._gate(self.ui.runner.jobs[row["job_id"]]):
                raise PermissionError("This execution is no longer authorized on the selected runtime.")
            status = self.status(source, request)
            if status["state"] == "interrupted":
                self.ui.runner.reauthorize_studio(row["job_id"])
            return self.status(source, request)

    def authorize_read(self, source: str, dispatch: str) -> None:
        row = self.store.get("execution", dispatch, source)
        if not self.admitted(source, dispatch) or not self.origin_authorized(source, dispatch, row["spec_digest"]):
            raise PermissionError("The originating session no longer permits reading this resource.")

    @staticmethod
    def _public(row: dict) -> dict:
        return {"dispatch_id": row["id"], "spec_digest": row["spec_digest"], "state": row["state"], "error": row.get("error", "")}

    def close(self) -> None:
        self.store.close()
