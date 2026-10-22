"""Runs of the RBMK control room for tests, made by the app's own hub: plain-code controllers, and the real decision model.

A plain-code controller (the naive rule or the 1986 crew script) is declared as not a model, and a test that only needs rows
to draw uses one. A test of what the model says runs the real decision model through the hub's GPU hold; when the model cannot
answer the run ends in error and `finished_run` fails naming the cause. Nothing here substitutes an answer.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from examples.rbmk.decider import bundle
from examples.rbmk.web import recordings
from examples.rbmk.web.hub import Hub

RUN_TIMEOUT_S = 1200
HOLD_WAIT_S = 900


def finished_run(hub: Hub, scenario: str, controller: str) -> dict[str, Any]:
    """The run document of ``scenario`` run to the end by ``controller``; an error or a stop fails with its reason."""
    job = hub.start_run(scenario, controller)
    end = time.monotonic() + RUN_TIMEOUT_S
    while job.status == "running" and time.monotonic() < end:
        time.sleep(0.2)
    if job.status != "done":
        raise AssertionError(f"the {controller} run of {scenario} ended {job.status}: {job.error or 'it did not finish'}")
    return hub.document(job.id)


def plain_run(tmp: Path, scenario: str = "pump_trip", controller: str = "naive") -> dict[str, Any]:
    """A run by a plain-code controller."""
    return finished_run(Hub(tmp), scenario, controller)


def machine_models(real: Any, monkeypatch: Any) -> None:
    """Point the decision checkpoint's lookup at the machine's own Hugging Face cache and wait for the compute to be idle."""
    cache = real.roots[-1]
    assert cache.is_dir(), f"REAL MODEL MISSING: no Hugging Face cache at {cache}; fetch the checkpoint once: poolhouse-decide fetch"
    monkeypatch.setenv("HF_HUB_CACHE", str(cache))
    real.idle()


def model_run(tmp: Path, scenario: str = "fixture_short") -> dict[str, Any]:
    """A run by the real decision model, queued behind any other holder of the GPU."""
    doc = finished_run(Hub(tmp, wait_s=HOLD_WAIT_S), scenario, "model")
    assert doc["meta"]["backend"]["model"], "the run names no model"
    return doc


def feed_file(doc: dict[str, Any], path: Path) -> Path:
    """The rows and decisions of a run document as the node check reads them."""
    path.write_text(json.dumps({"trace": doc["trace"], "decisions": [d for d in doc["decisions"] if d["kind"] in ("decision", "trip")]}))
    return path


def save_recording(doc: dict[str, Any], folder: Path) -> str:
    """Store a model run in ``folder`` as a recording the app's Recorded list can open; returns the file name."""
    doc["meta"]["backend"]["mode"] = "recorded"
    rec = bundle.recording_of(doc)
    name = f"{doc['meta']['scenario']['key']}-{doc['meta']['backend']['model']}-{doc['meta']['backend']['recorded_at']}.json"
    (folder / name).write_text(bundle.dumps(rec) + "\n")
    assert recordings.check(json.loads((folder / name).read_text()))
    return name


@pytest.fixture
def short_recording(tmp_path: Path, serve_leases: Any, real: Any, monkeypatch: Any) -> tuple[Path, str]:
    """A recording made now from a real run of the decision model on the seven-tick scenario: its folder and file name."""
    machine_models(real, monkeypatch)
    name = save_recording(model_run(tmp_path), tmp_path)
    return tmp_path, name
