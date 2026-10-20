"""Private, broker-owned pointer inference over inherited standard streams."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
from pathlib import Path

import psutil

from poolhouse.decide.calibrate import Calibration
from poolhouse.decide.pointer import PointerDecider
from poolhouse.decide.sources import Source
from poolhouse.files import read_json
from poolhouse.home import state
from poolhouse.redact.secrets import env_secrets, redact

MAX_FRAME = 1_000_000


def memory() -> dict[str, int]:
    """Return process and backend residency measurements without releasing caches."""
    import torch

    device = 0
    if torch.cuda.is_available():
        device = torch.cuda.memory_reserved()
    elif torch.backends.mps.is_available():
        device = torch.mps.driver_allocated_memory()
    return {"rss": psutil.Process().memory_info().rss, "device": int(device)}


class HostedPointer(PointerDecider):
    """Load only the explicit files supplied over the broker's inherited pipe."""

    def __init__(self, source: dict, calibration: dict | None):
        paths = {key: Path(value) if value is not None else None for key, value in source.items()
                 if key in {"base_dir", "tokenizer_dir", "head", "lora_dir"}}
        self.files = Source(**{**source, **paths})
        super().__init__(self.files.base_dir, download=False,
                         calibration=Calibration.from_public(calibration) if calibration else None)

    def resolve(self) -> Source:
        return self.files


def _parent() -> None:
    parent = os.getppid()
    record = read_json(state("broker.json"), {})
    if record.get("pid") != parent or record.get("pid_started") != psutil.Process(parent).create_time():
        raise PermissionError("A decision host can be started only by the running broker.")

    def watch():
        stopped = threading.Event()
        while not stopped.wait(0.2):
            if os.getppid() != parent:
                os._exit(1)

    threading.Thread(target=watch, daemon=True).start()


def run() -> None:
    _parent()
    model = None
    for raw in iter(lambda: sys.stdin.buffer.readline(MAX_FRAME + 1), b""):
        if len(raw) > MAX_FRAME or not raw.endswith(b"\n"):
            raise ValueError("The decision host frame is too large.")
        request = json.loads(raw)
        try:
            with contextlib.redirect_stdout(sys.stderr):
                if request.get("op") == "load" and model is None:
                    model = HostedPointer(request["source"], request.get("calibration"))
                    model.load()
                    response = {"ready": True, "memory": memory()}
                elif request.get("op") == "decide" and model is not None:
                    result = model.decide(**request["record"]).public()
                    response = {"result": result, "memory": memory()}
                else:
                    raise ValueError("Invalid decision host operation.")
        except (ValueError, OSError, RuntimeError) as error:
            message, _ = redact(str(error), env_secrets(os.environ))
            response = {"error": type(error).__name__ + ": " + message[:1000]}
        sys.stdout.write(json.dumps(response, allow_nan=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    run()
