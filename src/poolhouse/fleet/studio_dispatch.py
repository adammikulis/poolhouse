"""Typed dispatch to authenticated devices with durable execution identities."""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor

from poolhouse.home import machine_id
from poolhouse.studio.catalog import node_spec
from poolhouse.studio.engine import _artifacts

from .discovery import derive_token, memberships
from .membership import fingerprint_of
from .project_client import members
from .studio_transport import MemberExecution


class DeviceDispatch:
    """Run fixed nodes on devices selected from current authenticated discovery."""

    def __init__(self, ui, store, authorize) -> None:
        self.ui, self.store, self.authorize = ui, store, authorize
        self._executor = ThreadPoolExecutor(thread_name_prefix="studio-device")
        self._futures = {}
        self._lock = threading.RLock()
        self._implementations = {}

    def devices(self) -> list[dict]:
        rows = [{"id": machine_id(), "name": self.ui.name or "This device", "is_self": True,
                 "available": True, "capabilities": []}]
        rows.extend({"id": fingerprint_of(item.beacon.cert), "name": item.name, "is_self": False,
                     "available": True, "capabilities": item.beacon.device.get("backends", [])}
                    for item in members(self.ui) if item.beacon and item.beacon.machine != machine_id())
        return list({row["id"]: row for row in rows}.values())

    def is_remote(self, node: dict) -> bool:
        return bool(node.get("execution", {}).get("certificate") or node.get("execution", {}).get("machine", machine_id()) != machine_id())

    def member(self, target):
        identity = target if isinstance(target, dict) else {"certificate": target}
        found = [item for item in members(self.ui) if item.beacon and fingerprint_of(item.beacon.cert) == identity.get("certificate")
                 and (not identity.get("machine") or item.beacon.machine == identity["machine"])
                 and (not identity.get("pool") or self._pool(item) == identity["pool"])]
        if len(found) != 1:
            raise PermissionError("The selected device is not a current authenticated pool member.")
        return found[0]

    def _pool(self, member) -> str:
        membership = next((item for item in memberships(self.ui.pool_key_path) if derive_token(item.key) == member.token), None)
        if membership is None:
            raise PermissionError("The selected device no longer belongs to this pool.")
        return hashlib.sha256(membership.key).hexdigest()

    def identity(self, node: dict) -> dict:
        selected = node["execution"]
        device = self.member(selected if "certificate" in selected else selected["machine"])
        return {"machine": device.beacon.machine, "certificate": fingerprint_of(device.beacon.cert), "pool": self._pool(device)}

    def validate(self, nodes: list[dict], principal: dict) -> list:
        errors = []
        for node in nodes:
            if node["type"] in {"dataset", "model", "prompt", "tools.call"}:
                errors.append({"node": node["id"], "code": "device", "message": "Choose a compute node for device execution."})
                continue
            device = self.member(node["execution"]["machine"])
            transport = MemberExecution(device, lambda node=node: self.authorize(principal, node, {}))
            reply = transport.request("validate", {"nodes": [self._node(node)]})
            if not isinstance(reply.get("errors"), list) or not isinstance(reply.get("implementations"), dict):
                raise ValueError("The selected device returned invalid dependency information.")
            errors.extend(reply.get("errors", []))
            self._implementations[node["execution"]["machine"], node["id"]] = reply["implementations"][node["id"]]
        return errors

    def fingerprint(self, node: dict) -> str:
        target = node["execution"].get("certificate", node["execution"]["machine"])
        return self._implementations.get((target, node["id"]), "")

    @staticmethod
    def _node(node: dict) -> dict:
        return {key: node[key] for key in ("id", "type", "version", "parameters") if key in node}

    def _allowed(self, run: dict, node: dict, inputs: dict) -> bool:
        current = self.store.get("run", run["id"], run["scope"])
        return current["state"] in {"running", "queued"} and self.authorize(current["principal"], node, inputs)

    def start(self, run: dict, node: dict, inputs: dict, context: dict, execution: dict) -> None:
        def submit():
            transport = MemberExecution(self.member(execution), lambda: self._allowed(run, node, inputs))
            if _artifacts(node, inputs, "inputs") != context["input_artifacts"]:
                raise ValueError("The selected resource changed after the run was snapshotted.")
            staged = transport.stage(execution["id"], inputs, node_spec(node["type"], node.get("version", 1)))
            if _artifacts(node, inputs, "inputs") != context["input_artifacts"]:
                raise ValueError("The selected resource changed while it was being staged.")
            reply = transport.request("submit", {"dispatch_id": execution["id"], "spec_digest": execution["digest"],
                                                 "implementation": execution["implementation"],
                                                 "run_id": run["id"], "node": self._node(node), **staged})
            if reply.get("dispatch_id") != execution["id"] or reply.get("spec_digest") != execution["digest"]:
                raise ValueError("The device acknowledged another execution snapshot.")
            return {"state": "queued"}

        with self._lock:
            if execution["id"] in self._futures:
                raise RuntimeError("This device execution has already been dispatched.")
            self._futures[execution["id"]] = self._executor.submit(submit)

    def _status(self, execution: dict, context: dict) -> dict:
        transport = MemberExecution(self.member(execution), lambda: self.authorize(context["principal"], None, {}))
        reply = transport.request("status", {"dispatch_id": execution["id"], "spec_digest": execution["digest"]})
        if reply.get("dispatch_id") != execution["id"] or reply.get("spec_digest") != execution["digest"]:
            raise ValueError("The device returned another execution snapshot.")
        if reply.get("state") == "done":
            result = transport.request("result", {"dispatch_id": execution["id"], "spec_digest": execution["digest"]})
            if result.get("dispatch_id") != execution["id"] or result.get("spec_digest") != execution["digest"]:
                raise ValueError("The device returned another result snapshot.")
            run = self.store.get("run", context["run_id"], context["principal"]["scope"])
            node = next((node for node in run["graph"]["nodes"] if node["id"] == context["node_id"]), None)
            if node is None:
                raise ValueError("The original node snapshot is unavailable.")
            if result["node"] != self._node(node):
                raise ValueError("The device result belongs to another node version.")
            spec = node_spec(node["type"], node.get("version", 1))
            return {"state": "done", "outputs": transport.collect(result, context["output_dir"], spec, dispatch_id=execution["id"])}
        return reply

    def poll(self, execution: dict, context: dict) -> dict | None:
        with self._lock:
            future = self._futures.get(execution["id"])
            if future is None:
                self._futures[execution["id"]] = self._executor.submit(self._status, execution, context)
                return None
            if not future.done():
                return None
            del self._futures[execution["id"]]
        try:
            reply = future.result()
        except (ValueError, OSError, RuntimeError, PermissionError, KeyError) as exc:
            return {"state": "interrupted", "error": "Device execution requires reconciliation: " + str(exc)}
        if not isinstance(reply.get("state"), str):
            return {"state": "interrupted", "error": "The selected device returned an invalid execution state."}
        if reply.get("state") in {"done", "failed", "interrupted", "cancelled"}:
            return {key: value for key, value in reply.items() if key in {"state", "outputs", "error"}}
        return None

    def cancel(self, execution: dict, principal: dict) -> None:
        transport = MemberExecution(self.member(execution), lambda: True)
        transport.request("cancel", {"dispatch_id": execution["id"], "spec_digest": execution["digest"]})

    def resume(self, execution: dict, principal: dict) -> None:
        transport = MemberExecution(self.member(execution), lambda: self.authorize(principal, None, {}))
        reply = transport.request("resume", {"dispatch_id": execution["id"], "spec_digest": execution["digest"]})
        if reply.get("state") not in {"done", "queued", "running", "preparing", "launching"}:
            raise ValueError("The original device could not reconcile this execution; no work was repeated.")

    def authorize_execution(self, source: str, request: dict) -> bool:
        certificate, pool, _ = source.split(":")
        found = [item for item in members(self.ui) if item.beacon and fingerprint_of(item.beacon.cert) == certificate and self._pool(item) == pool]
        if len(found) != 1:
            return False
        target = found[0].beacon.machine
        with self.store.lock:
            rows = self.store.graph.query("MATCH (n:Node) WHERE n.kind='studio.run' RETURN n.id AS id")
            runs = [self.store.graph.get_doc(row["id"]) for row in rows]
        for run in runs:
            if run["state"] not in {"running", "queued"}:
                continue
            for node in run["graph"]["nodes"]:
                execution = run["nodes"][node["id"]].get("execution", {})
                if execution.get("id") == request.get("dispatch_id") and execution.get("machine") == target:
                    inputs = {edge["target"]["port"]: run["nodes"][edge["source"]["node"]]["outputs"].get(edge["source"]["port"])
                              for edge in run["graph"]["edges"] if edge["target"]["node"] == node["id"]}
                    return (execution.get("certificate") == certificate and execution.get("pool") == pool
                            and request.get("spec_digest", execution["digest"]) == execution["digest"]
                            and self.authorize(run["principal"], node, inputs))
        return False

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)
