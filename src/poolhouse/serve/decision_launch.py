"""Daemon-owned broker generation and its private inherited launch proof.

The ordinary broker record locates a socket; it never establishes this authority.
Only a daemon retaining its actual child and the inherited secret can verify it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import select
import subprocess
import sys
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from poolhouse import home, maintenance
from poolhouse.fleet.environment import Environment
from poolhouse.fleet.managed_compute import process_environment
from poolhouse.log import warn
from poolhouse.platform import start_process
from poolhouse.serve import broker_wire
from poolhouse.serve.process import owns_connection, pid_exists, started_at

LIMIT = 32_768
DOMAIN = b"poolhouse/decision-issuer/v1\n"
STAGES = frozenset({"queue", "load", "compute", "result"})
FIELDS = frozenset({"permit_id", "request_id", "record_digest", "model_digest", "runtime_digest",
                    "worker", "broker", "stage", "issuer_instance"})


def _json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _frame(stream, alive, *, timeout: float = broker_wire.START_WAIT_S) -> bytes:
    """Read one complete private frame while its actual owning parent stays alive."""
    until = time.monotonic() + timeout
    data = bytearray()
    os.set_blocking(stream.fileno(), False)
    while b"\n" not in data:
        remaining = until - time.monotonic()
        if not alive() or remaining <= 0:
            raise InterruptedError("The owning daemon did not complete its broker proof.")
        if not select.select([stream], [], [], min(0.1, remaining))[0]:
            continue
        try:
            chunk = os.read(stream.fileno(), LIMIT + 1)
        except BlockingIOError:
            continue
        if not chunk:
            raise InterruptedError("The owning daemon closed its broker proof pipe.")
        data.extend(chunk)
        if len(data) > LIMIT:
            raise ValueError("The broker proof frame is oversized.")
    if data.count(b"\n") != 1 or not data.endswith(b"\n"):
        raise ValueError("The broker proof contains an unexpected extra frame.")
    return bytes(data)


def _generation(value) -> bool:
    return (isinstance(value, dict) and set(value) == {"pid", "started"}
            and type(value["pid"]) is int and value["pid"] > 0
            and type(value["started"]) in {int, float}
            and math.isfinite(value["started"]) and value["started"] > 0)


def issuer(value: dict) -> dict:
    """Accept a daemon-selected fixed loopback endpoint, never a record-selected URL."""
    if not isinstance(value, dict) or set(value) != {
            "endpoint", "certificate", "machine", "pool", "runtime", "issuer_instance"}:
        raise ValueError("The resident issuer bootstrap is incomplete.")
    if any(not isinstance(value[key], str) or not value[key] or len(value[key]) > 16_384
           for key in value):
        raise ValueError("The resident issuer identity is invalid.")
    parts = urlsplit(value["endpoint"])
    if (parts.scheme != "https" or parts.hostname not in {"127.0.0.1", "::1"}
            or not parts.port or parts.path or parts.query or parts.fragment
            or parts.username or parts.password):
        raise ValueError("The resident issuer must be the owning local daemon.")
    for key in ("pool", "runtime", "issuer_instance"):
        if not re.fullmatch(r"[a-f0-9]{64}", value[key]):
            raise ValueError("The resident issuer digest is invalid.")
    return dict(value)


def checked_body(value: dict) -> dict:
    """The entire signed authority request; there are no optional authority fields."""
    if not isinstance(value, dict) or set(value) != FIELDS or value["stage"] not in STAGES:
        raise ValueError("The resident authority request is invalid.")
    if not _generation(value["worker"]):
        raise ValueError("The decision worker process proof is invalid.")
    broker = value["broker"]
    if not isinstance(broker, dict) or set(broker) != {"pid", "started", "instance"}:
        raise ValueError("The decision broker process proof is invalid.")
    if not _generation({key: broker[key] for key in ("pid", "started")}):
        raise ValueError("The decision broker process proof is invalid.")
    for key in ("record_digest", "model_digest", "runtime_digest", "issuer_instance"):
        if not isinstance(value[key], str) or not re.fullmatch(r"[a-f0-9]{64}", value[key]):
            raise ValueError("The decision authority digest is invalid.")
    if not isinstance(broker["instance"], str) or not re.fullmatch(r"[a-f0-9]{64}", broker["instance"]):
        raise ValueError("The decision broker instance is invalid.")
    for key in ("permit_id", "request_id"):
        if not isinstance(value[key], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value[key]):
            raise ValueError("The decision request identity is invalid.")
    return json.loads(_json(value))


@dataclass(frozen=True)
class Bootstrap:
    parent: dict
    instance: str
    secret: bytes = field(repr=False)
    issuer: dict

    def sign(self, body: dict) -> dict:
        body = checked_body(body)
        if (body["broker"] != {"pid": os.getpid(), "started": started_at(os.getpid()),
                               "instance": self.instance}
                or body["runtime_digest"] != self.issuer["runtime"]
                or body["issuer_instance"] != self.issuer["issuer_instance"]
                or os.getppid() != self.parent["pid"]
                or started_at(self.parent["pid"]) != self.parent["started"]):
            raise PermissionError("The owning decision issuer is no longer current.")
        return {**body, "proof": hmac.new(self.secret, DOMAIN + _json(body), hashlib.sha256).hexdigest()}


def bootstrap(raw: bytes) -> Bootstrap:
    if len(raw) > LIMIT or not raw.endswith(b"\n"):
        raise ValueError("The broker launch proof is oversized or incomplete.")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"parent", "instance", "secret", "issuer"}:
        raise ValueError("The broker launch proof is invalid.")
    if (not _generation(value["parent"]) or value["parent"]["pid"] != os.getppid()
            or started_at(os.getppid()) != value["parent"]["started"]):
        raise PermissionError("The broker launch is not owned by its actual parent.")
    for key in ("instance", "secret"):
        if not isinstance(value[key], str) or not re.fullmatch(r"[a-f0-9]{64}", value[key]):
            raise ValueError("The broker launch identity is invalid.")
    return Bootstrap(value["parent"], value["instance"], bytes.fromhex(value["secret"]), issuer(value["issuer"]))


class BrokerBusy(BlockingIOError):
    """A verified existing broker declined idle retirement before any new launch."""


def _idle_boundary() -> None:
    """Ask an existing broker to retire itself, never adopt it or signal its PID."""
    held = broker_wire._record()
    if not held:
        return
    generation = {"pid": held.get("pid"), "started": held.get("pid_started")}
    if not _generation(generation):
        raise InterruptedError("The current broker generation cannot be verified; it was not replaced.")
    pid, started = generation["pid"], generation["started"]
    if not pid_exists(pid) or started_at(pid) != started:
        return  # A stale locator grants nothing; the new child must still obtain broker.lock.
    answering = broker_wire._answering()
    if (not answering or answering.get("pid") != pid or answering.get("pid_started") != started
            or answering.get("port") != held.get("port")):
        raise InterruptedError("The current broker is not answering with its verified generation.")
    reply = broker_wire._send(answering, {"op": "idle-retire", "pid": os.getpid()}, timeout=5, expected=generation)
    if reply == {"ok": True, "retiring": False}:
        raise BrokerBusy("The current broker has active work; resident launch waits for its idle boundary.")
    if reply != {"ok": True, "retiring": True}:
        raise InterruptedError("The current broker did not confirm idle retirement; it was not replaced.")
    until = time.monotonic() + broker_wire.START_WAIT_S
    while pid_exists(pid) and started_at(pid) == started:
        if time.monotonic() >= until:
            raise InterruptedError("The retiring broker has not ended; no replacement was started.")
        time.sleep(0.01)


class OwnedBroker:
    """A retained daemon child. Starting twice attaches; no unowned process is adopted."""

    def __init__(self, selected_issuer: dict):
        self.issuer = issuer(selected_issuer)
        self.secret = secrets.token_bytes(32)
        self.instance = secrets.token_hex(32)
        self.process = None
        self.started = None
        self.ready = None
        self.admission_possible = False

    @maintenance.reading
    def start(self) -> dict:
        if self.process is not None:
            if self.alive() and self.ready is not None:
                return dict(self.ready)
            raise InterruptedError("The owned broker launch requires reconciliation.")
        environment = Environment(home.state("traind"))
        environment.require_current_runtime()
        environment.require_compute()
        _idle_boundary()
        self.process = start_process(
            [str(environment.python), "-B", "-I", "-m", "poolhouse.serve.cli", "broker-owned"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=process_environment())
        try:
            self.started = started_at(self.process.pid)
            if self.started is None:
                raise InterruptedError("The owned broker process ended during launch.")
            raw = _json({"parent": {"pid": os.getpid(), "started": started_at(os.getpid())},
                         "instance": self.instance, "secret": self.secret.hex(), "issuer": self.issuer}) + b"\n"
            until = time.monotonic() + broker_wire.START_WAIT_S
            self._write(raw, until)
            data = bytearray()
            while b"\n" not in data:
                if not self.alive() or time.monotonic() >= until:
                    raise InterruptedError("The owned broker did not confirm its launch.")
                if select.select([self.process.stdout], [], [], 0.1)[0]:
                    data.extend(os.read(self.process.stdout.fileno(), LIMIT + 1))
                    if len(data) > LIMIT:
                        raise ValueError("The broker launch response is oversized.")
            answer = json.loads(bytes(data))
            if (not isinstance(answer, dict) or set(answer) != {"pid", "started", "instance", "port"}
                    or answer["pid"] != self.process.pid or answer["started"] != self.started
                    or answer["instance"] != self.instance or type(answer["port"]) is not int
                    or not 0 < answer["port"] < 65536):
                raise PermissionError("The broker launch response has a different identity.")
            # A partial/lost acknowledgement may already have admitted work. From
            # this point a failed launch is retained as uncertain, never killed.
            self.admission_possible = True
            self._write(_json({"accept": self.instance}) + b"\n", until)
            self.ready = answer
            self.process.stdin.close()
            return dict(answer)
        finally:
            if self.ready is None and not self.admission_possible:
                self._abort_start()

    def _write(self, raw: bytes, until: float) -> None:
        os.set_blocking(self.process.stdin.fileno(), False)
        pending = memoryview(raw)
        while pending:
            if not self.alive() or time.monotonic() >= until:
                raise InterruptedError("The broker launch pipe did not accept its proof.")
            if not select.select([], [self.process.stdin], [], 0.1)[1]:
                continue
            try:
                count = os.write(self.process.stdin.fileno(), pending)
            except BlockingIOError:
                continue
            if count <= 0:
                raise BrokenPipeError("The broker launch pipe stopped accepting its proof.")
            pending = pending[count:]

    def alive(self) -> bool:
        return (self.process is not None and self.process.poll() is None and self.started is not None
                and pid_exists(self.process.pid) and started_at(self.process.pid) == self.started)

    def verify(self, value: dict, remote: tuple, local: tuple) -> dict:
        """Verify this exact child's authenticated daemon connection, then return its body."""
        if not isinstance(value, dict) or set(value) != FIELDS | {"proof"}:
            raise PermissionError("The decision broker proof is missing.")
        body = checked_body({key: value[key] for key in FIELDS})
        proof = hmac.new(self.secret, DOMAIN + _json(body), hashlib.sha256).hexdigest()
        if (not isinstance(value["proof"], str) or not hmac.compare_digest(value["proof"], proof)
                or not self.alive() or self.ready is None
                or body["broker"] != {"pid": self.process.pid, "started": self.started, "instance": self.instance}
                or body["issuer_instance"] != self.issuer["issuer_instance"]
                or body["runtime_digest"] != self.issuer["runtime"]
                or not owns_connection(self.process.pid, remote, local)):
            raise PermissionError("The decision broker is not the current owned issuer client.")
        return body

    def _abort_start(self) -> None:
        """Stop an owned child only while admission is still provably impossible."""
        if self.admission_possible or self.ready is not None:
            raise RuntimeError("An admitted broker requires a coordinated idle retirement.")
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                stream.close()


def serve_owned(_args=None) -> int:
    parent = os.getppid()
    parent_started = started_at(parent)

    def alive():
        return (parent_started is not None and os.getppid() == parent and pid_exists(parent)
                and started_at(parent) == parent_started)

    owned = bootstrap(_frame(sys.stdin.buffer, alive))

    def ready(server):
        sys.stdout.buffer.write(_json({"pid": os.getpid(), "started": started_at(os.getpid()),
                                      "instance": owned.instance, "port": server.server_address[1]}) + b"\n")
        sys.stdout.buffer.flush()
        if json.loads(_frame(sys.stdin.buffer, alive)) != {"accept": owned.instance}:
            raise PermissionError("The owning daemon did not confirm this broker generation.")
        sys.stdin.close()

    return broker_wire.serve(owned=owned, ready=ready, say=lambda line, **_kwargs: warn(line, flush=True))
