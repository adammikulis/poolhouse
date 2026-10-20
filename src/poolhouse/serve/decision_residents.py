"""Broker-owned pointer residency, admission and non-replayed record outcomes."""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from poolhouse import leasetable
from poolhouse.decide.pointer import MAX_TOKENS
from poolhouse.decide.types import MAX_NAME, MAX_OPTIONS, MAX_TEXT
from poolhouse.graph import GraphStore
from poolhouse.serve import admission
from poolhouse.serve.decision_process import DecisionProcess
from poolhouse.serve.process import pid_exists, started_at


def fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(b"poolhouse/decision-resident/v1\n" + encoded).hexdigest()


@dataclass(frozen=True)
class DecisionModel:
    """Canonical files and conservative peak residency returned by current authorization."""

    source: dict
    identity: str
    memory_bytes: int
    calibration: dict | None = None


@dataclass(frozen=True)
class DecisionOwner:
    """Authenticated request scope and the actual connecting process generation."""

    scope: str
    pid: int
    started: float


def peak_estimate(source: dict) -> int:
    """Estimate admitted peak for the supported dense Qwen pointer torso."""
    config = json.loads((Path(source["base_dir"]) / "config.json").read_text())
    if not isinstance(config, dict) or config.get("quantization") or config.get("quantization_config"):
        raise ValueError("Resident pointer inference does not load quantized weights.")
    config = config.get("text_config", config)
    if (not isinstance(config, dict) or config.get("model_type") not in {"qwen3_5", "qwen3_5_text"}
            or config.get("quantization") or config.get("quantization_config") or config.get("num_experts")
            or source.get("dtype") not in {"bfloat16", "float16", "float32"}):
        raise ValueError("Resident decision inference requires a supported dense Qwen pointer model.")
    dimensions = [config.get(key) for key in ("hidden_size", "num_hidden_layers", "num_attention_heads")]
    if any(type(value) is not int or value <= 0 for value in dimensions):
        raise ValueError("The decision model is missing its verified architecture dimensions.")
    hidden, layers, heads = dimensions
    files = set(Path(source["base_dir"]).rglob("*.safetensors")) | {Path(source["head"])}
    if source.get("lora_dir"):
        files.update(Path(source["lora_dir"]).rglob("*.safetensors"))
    weights = sum(path.stat().st_size for path in files)
    return 4 * weights + MAX_TOKENS ** 2 * heads * 8 + MAX_TOKENS * hidden * layers * 16 + 512 * admission.MIB


def validate_record(record: dict) -> None:
    if not isinstance(record, dict) or set(record) - {"question", "state", "options", "abstain_below"}:
        raise ValueError("Invalid decision record fields.")
    if any(not isinstance(record.get(name), str) or len(record[name]) > MAX_TEXT for name in ("question", "state")):
        raise ValueError("Decision questions and state must be bounded text.")
    options = record.get("options")
    if (not isinstance(options, dict) or not 2 <= len(options) <= MAX_OPTIONS
            or any(not isinstance(k, str) or not k.strip() or len(k) > MAX_NAME or not isinstance(v, str)
                   or len(v) > 4096 for k, v in options.items())):
        raise ValueError("Decision options must be bounded named descriptions.")
    threshold = record.get("abstain_below")
    if threshold is not None and (type(threshold) not in (int, float) or not 0 <= threshold <= 1):
        raise ValueError("The abstention threshold must be between zero and one.")
    if len(json.dumps(record, allow_nan=False).encode()) > 900_000:
        raise ValueError("The decision record is too large.")


def validate_result(result: dict, record: dict) -> None:
    if not isinstance(result, dict) or not isinstance(result.get("scores"), dict):
        raise ValueError("The decision host omitted its scores.")
    scores = result["scores"]
    if set(scores) != set(record["options"]) or result.get("choice") not in scores:
        raise ValueError("The decision host returned different options.")
    if any(type(v) not in (float, int) or not math.isfinite(v) or not 0 <= v <= 1 for v in scores.values()):
        raise ValueError("The decision host returned invalid probabilities.")
    if abs(sum(scores.values()) - 1) > 0.0001 or scores[result["choice"]] != max(scores.values()):
        raise ValueError("The decision host returned an invalid distribution.")
    fingerprint(result)


class DecisionResidents:
    """Admit and retain fixed hosts behind current per-record authorization."""

    def __init__(self, broker, *, backend=DecisionProcess, ledger=None):
        self.broker, self.backend = broker, backend
        self.instance = uuid.uuid4().hex
        package = Path(__file__).parent.parent
        self.implementation = fingerprint({str(path.relative_to(package)): hashlib.sha256(path.read_bytes()).hexdigest()
                                           for path in package.rglob("*.py")})
        self.lock = threading.RLock()
        self.residents = {}
        self._ledger = ledger

    @property
    def ledger(self):
        if self._ledger is None:
            self._ledger = GraphStore(self.broker.manager.state_file.with_name("decision-outcomes.ladybug"))
        return self._ledger

    def request(self, record: dict, request_id: str, owner: DecisionOwner,
                resolve: Callable[[], DecisionModel], connected: Callable[[], bool]) -> dict:
        validate_record(record)
        if (not isinstance(owner, DecisionOwner) or not isinstance(owner.scope, str) or not owner.scope
                or len(owner.scope) > 1024 or type(owner.pid) is not int or owner.pid <= 0
                or type(owner.started) not in (int, float) or not math.isfinite(owner.started) or owner.started <= 0):
            raise PermissionError("An authenticated decision request scope is required.")
        if not isinstance(request_id, str) or not request_id or len(request_id) > 256:
            raise ValueError("A bounded decision request identity is required.")

        def current():
            if not connected() or not pid_exists(owner.pid) or started_at(owner.pid) != owner.started:
                raise InterruptedError("The decision requester ended or its process identity changed.")
            model = resolve()
            if not isinstance(model, DecisionModel) or not model.identity or model.memory_bytes <= 0:
                raise PermissionError("Current decision model authorization is unavailable.")
            return replace(model, identity=fingerprint({"model": model.identity, "source": model.source,
                                                       "calibration": model.calibration, "implementation": self.implementation}))

        model = current()
        key = "decision.request:" + fingerprint({"scope": owner.scope, "request": request_id})
        content = fingerprint({"owner": owner.__dict__, "model": model.identity, "record": record})
        with self.lock:
            previous = self.ledger.get_doc(key)
            if previous:
                if previous["digest"] != content:
                    raise ValueError("The decision request identity already names different work.")
                if previous["instance"] != self.instance or previous["state"] != "done":
                    raise InterruptedError("The prior decision outcome is uncertain; it was not repeated.")
                if current().identity != model.identity:
                    raise PermissionError("Decision authorization changed before returning the saved result.")
                return previous["result"]
            outcome = {"digest": content, "instance": self.instance, "state": "interrupted"}
            self.ledger.put_doc(key, outcome)
            resident = None
            completed = False
            try:
                resident = self._resident(model, current)
                if current().identity != model.identity:
                    raise PermissionError("The authorized decision model changed.")
                result = self._compute(resident, {"op": "decide", "record": record}, current, model)
                validate_result(result["result"], record)
                if current().identity != model.identity:
                    raise PermissionError("Decision authorization changed before the result was returned.")
                outcome.update(state="done", result=result["result"])
                self.ledger.put_doc(key, outcome)
                resident["used"] = time.monotonic()
                completed = True
                return result["result"]
            finally:
                if not completed and model.identity in self.residents:
                    self._evict(model.identity)

    def _resident(self, model: DecisionModel, current) -> dict:
        resident = self.residents.get(model.identity)
        if resident and resident["process"].alive():
            self._memory(resident, {})
            return resident
        if resident:
            self._evict(model.identity)
        if model.memory_bytes < peak_estimate(model.source):
            raise ValueError("The decision memory reservation is below its supported architecture estimate.")
        self.broker.table.budget(admission.budget_mb())
        got = self.broker.table.acquire([leasetable.memory(math.ceil(model.memory_bytes / admission.MIB))],
                  leasetable.Terms(tag="decision.resident:" + model.identity, pid=os.getpid(),
                                   ttl_s=admission.KEEP_S, wait=True))
        process = None
        ready = False
        try:
            if got["state"] == "queued":
                for key in list(self.residents):
                    self._evict(key, wait=False)
            while got["state"] == "queued":
                if current().identity != model.identity:
                    raise PermissionError("Decision authorization changed while waiting for memory.")
                for key in list(self.residents):
                    self._evict(key, wait=False)
                got = self.broker.table.wait(got["id"], seconds=0.5)
            if got["state"] != "held":
                raise RuntimeError("Decision residency could not be admitted against memory.")
            current()
            process = self.backend()
            resident = {"process": process, "lease": got["id"], "bytes": model.memory_bytes,
                        "used": time.monotonic()}
            self.residents[model.identity] = resident
            getattr(process, "start", lambda: None)()
            self.broker.table.renew(got["id"], pid=process.pid, ttl_s=admission.KEEP_S)
            self._compute(resident, {"op": "load", "source": model.source, "calibration": model.calibration}, current, model)
            ready = True
            return resident
        finally:
            if not ready:
                if model.identity in self.residents:
                    self._evict(model.identity)
                else:
                    if process:
                        process.close()
                    if got.get("id"):
                        self.broker.table.release(got["id"])

    def _compute(self, resident: dict, message: dict, current, model: DecisionModel) -> dict:
        renewed = time.monotonic()
        def allowed():
            nonlocal renewed
            try:
                if resident.get("lease_error"):
                    return False
                if time.monotonic() - renewed > admission.KEEP_S / 3:
                    self.broker.table.renew(resident["lease"], ttl_s=admission.KEEP_S)
                    renewed = time.monotonic()
                return current().identity == model.identity
            except (PermissionError, OSError, RuntimeError):
                return False
        process = resident["process"]
        got = self.broker.claim("gpu-training", process.pid, {"purpose": "resident decision"},
                                timeout=None, connected=allowed)
        if not got["granted"]:
            raise InterruptedError("Decision compute was not admitted.")
        resident["compute_held"] = True
        completed = False
        try:
            if not allowed():
                raise PermissionError("Decision authorization changed before compute.")
            result = process.exchange(message, allowed)
            self._memory(resident, result.get("memory", {}))
            completed = True
            return result
        finally:
            try:
                if not completed:
                    process.close()
                    if process.alive():
                        raise InterruptedError("The interrupted decision process is still stopping; its compute lease is retained.")
            finally:
                if completed or not process.alive():
                    self.broker.unclaim("gpu-training", process.pid)
                    resident["compute_held"] = False

    @staticmethod
    def _memory(resident: dict, report: dict) -> None:
        measured = max(resident["process"].memory(), report.get("rss", 0), report.get("device", 0))
        if measured > resident["bytes"]:
            raise RuntimeError("Decision residency exceeded its admitted peak memory; the host was stopped.")

    def _evict(self, key: str, *, wait: bool = True) -> None:
        resident = self.residents[key]
        process = resident["process"]
        try:
            if not process.alive():
                process.close()
            else:
                if not resident.get("compute_held"):
                    granted = self.broker.claim("gpu-training", process.pid, {"purpose": "decision resident eviction"},
                                                timeout=None if wait else 0)
                    if not granted["granted"]:
                        return
                    resident["compute_held"] = True
                try:
                    process.close()
                finally:
                    if not process.alive():
                        self.broker.unclaim("gpu-training", process.pid)
                        resident["compute_held"] = False
        finally:
            if not process.alive():
                self.broker.table.release(resident["lease"])
                self.residents.pop(key, None)

    def upkeep(self) -> None:
        for resident in list(self.residents.values()):
            if resident["process"].alive():
                try:
                    self.broker.table.renew(resident["lease"], ttl_s=admission.KEEP_S)
                except (ValueError, OSError, RuntimeError) as error:
                    resident["lease_error"] = str(error)
        if not self.lock.acquire(blocking=False):
            return
        try:
            for key, resident in list(self.residents.items()):
                if (not resident["process"].alive() or resident.get("lease_error")
                        or time.monotonic() - resident["used"] > self.broker.idle_s):
                    self._evict(key, wait=False)
                if key in self.residents:
                    self.broker.table.renew(resident["lease"], ttl_s=admission.KEEP_S)
        finally:
            self.lock.release()

    def snapshot(self) -> list[dict]:
        return [{"identity": key, "pid": value["process"].pid, "reserved_bytes": value["bytes"],
                 "idle_s": max(0, time.monotonic() - value["used"])} for key, value in list(self.residents.items())]

    def close(self) -> None:
        with self.lock:
            for key in list(self.residents):
                self._evict(key)
            if self._ledger is not None:
                self._ledger.close()
