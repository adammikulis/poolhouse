"""Runs of the RBMK control room for tests, made by the app's own hub: plain-code controllers, and the real decision model.

A plain-code controller (the naive rule or the 1986 crew script) is declared as not a model, and a test that only needs rows to
draw uses one. A test of what the model says runs the real decision model through the hub while the test holds the GPU
(`real.pointer()`); when the model cannot answer the run ends in error and `finished_run` fails naming the cause. Nothing here
substitutes an answer.
"""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path
from typing import Any

from examples.rbmk.web.hub import Hub

RUN_TIMEOUT_S = 1200
HOLD_WAIT_S = 900


def already_held(*_args: Any, **_kwargs: Any) -> Any:
    """A hold for a hub inside a test that already holds the GPU itself."""
    return contextlib.nullcontext()


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


def model_run(tmp: Path, real: Any, scenario: str = "fixture_short") -> tuple[dict[str, Any], str]:
    """A run by the real decision model, queued behind any other holder of the GPU: its document and its file in the run store."""
    with real.pointer():
        doc = finished_run(Hub(tmp, wait_s=HOLD_WAIT_S, hold=already_held), scenario, "model")
    model = doc["meta"]["backend"]["model"]
    assert model, "the run names no model"
    return doc, f"{model}/{doc['meta']['memory']}/{scenario}.json"


def sync_recording(folder: Path, scenario: str = "fixture_short") -> str:
    """A recording of a run of the real model in simulated time, written to the run store (the caller holds the GPU); its file name."""
    from examples.rbmk import recorder, scenarios
    from examples.rbmk.decider.model import LiveAsker
    from examples.rbmk.decider.policy import ModelDecider
    from examples.rbmk.run import clocks
    from examples.rbmk.runner import Runner

    scn = scenarios.scenario(scenario)
    asker = LiveAsker()
    asker.warm()
    res = Runner(scn, ModelDecider(asker, scn.seed)).run()
    backend = {"mode": "recorded", "model": asker.model, "recorded_at": "2026-01-01", "source": "test"}
    name = f"{asker.model}/none/{scenario}.sync.json"
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(recorder.dumps(recorder.recording_of(res, scn, backend, clocks(scn, res))))
    return name


def feed_file(doc: dict[str, Any], path: Path) -> Path:
    """The rows and decisions of a run document as the node check reads them."""
    path.write_text(json.dumps({"trace": doc["trace"], "decisions": [r for r in doc["rows"] if r["kind"] == "decision"]}))
    return path
