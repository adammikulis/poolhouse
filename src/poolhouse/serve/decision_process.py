"""Managed interpreter and inherited-pipe transport for a decision resident."""

from __future__ import annotations

import contextlib
import json
import os
import select
import subprocess

import psutil

from poolhouse import home
from poolhouse.fleet.environment import Environment
from poolhouse.fleet.managed_compute import process_environment
from poolhouse.platform import start_process
from poolhouse.serve import grant

MAX_FRAME = 1_000_000


class DecisionProcess:
    """One fixed managed child whose input and output are private broker pipes."""

    def __init__(self):
        grant.require()
        environment = Environment(home.state("traind"))
        environment.require_current_runtime()
        environment.require_compute()
        self.executable = str(environment.python)
        self.process = None
        self.pid = 0
        self.started = None
        self.buffer = b""

    def start(self):
        grant.require()
        if self.process is not None:
            raise RuntimeError("The decision process was already started.")
        self.process = start_process(
            [self.executable, "-B", "-I", "-m", "poolhouse.serve.decision_host"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={**process_environment(), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
        self.pid = self.process.pid
        self.started = psutil.Process(self.pid).create_time()
        os.set_blocking(self.process.stdin.fileno(), False)

    def exchange(self, request: dict, connected) -> dict:
        if self.process is None:
            raise RuntimeError("The decision process was not started.")
        encoded = json.dumps(request, allow_nan=False).encode() + b"\n"
        if len(encoded) > MAX_FRAME:
            raise ValueError("The decision request is too large.")
        self._write(encoded, connected)
        while b"\n" not in self.buffer:
            if not connected():
                raise InterruptedError("The decision requester is no longer authorized or connected.")
            if self.process.poll() is not None:
                raise InterruptedError("The decision host ended without a confirmed result.")
            if not select.select([self.process.stdout], [], [], 0.1)[0]:
                continue
            self.buffer += os.read(self.process.stdout.fileno(), MAX_FRAME + 1)
            if len(self.buffer) > MAX_FRAME:
                raise ValueError("The decision host response is too large.")
        raw, self.buffer = self.buffer.split(b"\n", 1)
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("The decision host response must be an object.")
        if value.get("error"):
            raise RuntimeError(str(value["error"]))
        return value

    def _write(self, encoded: bytes, connected) -> None:
        if self.process is None:
            raise RuntimeError("The decision process was not started.")
        pending = memoryview(encoded)
        while pending:
            if not connected():
                raise InterruptedError("The decision requester ended while sending its record.")
            if self.process.poll() is not None:
                raise InterruptedError("The decision host ended before receiving its record.")
            if not select.select([], [self.process.stdin], [], 0.1)[1]:
                continue
            try:
                written = os.write(self.process.stdin.fileno(), pending[:65536])
            except BlockingIOError:
                continue
            pending = pending[written:]

    def memory(self) -> int:
        process = psutil.Process(self.pid)
        if process.create_time() != self.started:
            raise InterruptedError("The decision process identity changed.")
        return process.memory_info().rss

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def close(self) -> None:
        if self.process is None:
            return
        if self.alive():
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        for stream in (self.process.stdin, self.process.stdout):
            with contextlib.suppress(OSError):
                stream.close()
