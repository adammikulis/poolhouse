"""The server's state: the decision model, the jobs that use it, the run store and the documents.

A live model run is the engine's `LiveRunner` (the plant, the decider and the speed knob on separate clocks) under the GPU hold; a
plain-code run is the engine's `Runner` in simulated time. A `Feeder` turns either run's growing result into the events a browser
follows.
"""

from __future__ import annotations

import asyncio
import copy
import datetime
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import recorder, scenarios, starts, whatif
from ..decider.model import DECIDERS, MEMORIES, LiveAsker, ModelStopped
from ..decider.policy import CrewScript, ModelDecider, NaiveDecider
from ..display import export as display, text
from ..live import LiveRunner
from ..run import clocks, rods_of, scenario_of, store
from ..runner import Runner, RunOptions
from . import recordings
from .jobs import Busy, Cancelled, Job

PINNED = "strands-decider-2B-hobson-v19"
SPEEDS = (1.0, 10.0, 60.0, 300.0)
DEFAULT_SPEED = 60.0
TOY_NOTICE = "Educational toy, not a plant model. Constants are in the README with their sources and simplifications."
PUMP_S = 0.05
FLUSH_TICKS = 25


def scenario_named(key: str) -> scenarios.Scenario:
    """A named scenario, or the k-th start of a seeded batch (``start_<seed>_<k>``)."""
    if key.startswith("start_"):
        _, seed, k = key.split("_")
        return starts.draw(int(seed), int(k))
    return scenarios.scenario(key)


class Feeder:
    """Pushes what a run has produced since the last flush to its job: trace rows, decision rows, plant events, captions."""

    def __init__(self, job: Job, runner: Runner, header: dict[str, Any]) -> None:
        self.job, self.runner = job, runner
        self.trace = self.rows = 0
        self.open: set[int] = set()
        self.captioned: set[tuple[int, str, str]] = set()
        self.asked = 0
        job.emit(type="header", **header)

    def flush(self) -> None:
        """Emit every new row, the landing of each answer, new captions and a request in flight."""
        res, job = self.runner.res, self.job
        while self.trace < len(res.trace):
            job.emit(type="trace", row=res.trace[self.trace])
            self.trace += 1
        while self.rows < len(res.rows):
            row = res.rows[self.rows]
            job.emit(type="row", row=copy.deepcopy(row))
            if row["kind"] == "decision" and row["applied_tick"] is None:
                self.open.add(self.rows)
            self.rows += 1
        for i in sorted(self.open):
            row = res.rows[i]
            if row["applied_tick"] is not None:
                job.emit(type="landed", i=i, applied_tick=row["applied_tick"], applied_t_s=row["applied_t_s"], log_line=row["log_line"])
                self.open.discard(i)
        for cap in text.captions(res):
            key = (cap["tick"], cap["kind"], cap["text"])
            if key not in self.captioned:
                self.captioned.add(key)
                job.emit(type="caption", row=cap)
        if self.runner.waiting and self.runner.requests != self.asked:
            self.asked = self.runner.requests
            job.emit(type="asking", n=self.asked - 1, tick=self.runner.plant.tick)


class Hub:
    """Owns the decision model (loaded on the first model run), the compute slot, every job and the run store."""

    def __init__(self, folder: Path | None = None, wait_s: float = 0.0, hold: Callable[..., Any] | None = None) -> None:
        self.folder = store() if folder is None else folder
        self.wait_s, self.hold = wait_s, hold
        self.jobs: dict[str, Job] = {}
        self.slot = threading.Lock()
        self.working: str | None = None
        self.askers: dict[str, LiveAsker] = {}
        self.model_id = ""
        self.rendered: dict[tuple[str, float], dict[str, Any]] = {}
        self.outcomes: dict[tuple[str, float], dict[str, Any]] = {}
        self.sweeper: threading.Thread | None = None
        self.lock = threading.Lock()

    # ---- the model ------------------------------------------------------------------------------
    def model_status(self) -> dict[str, Any]:
        """The pinned decider, whether it can run here, and the id it reported once it has answered."""
        why = ""
        try:
            from poolhouse.decide import router

            why = router.unavailable("pointer", router.Config(backend="pointer"))
        except (ImportError, OSError, ValueError) as exc:
            why = f"{type(exc).__name__}: {exc}"
        return {"pinned": PINNED, "id": self.model_id, "available": not why, "why": why}

    def _asker(self, decider: str) -> LiveAsker:
        if decider not in self.askers:
            self.askers[decider] = LiveAsker(pointer=decider)
        return self.askers[decider]

    def _hold(self, label: str) -> Any:
        if self.hold is not None:
            return self.hold(label, wait_s=self.wait_s)
        from poolhouse.serve import gpu

        return gpu.hold(label, wait_s=self.wait_s)

    # ---- jobs -------------------------------------------------------------------------------------
    def job(self, job_id: str) -> Job:
        """The job called ``job_id``; KeyError when there is none."""
        return self.jobs[job_id]

    def _spawn(self, job: Job, body: Callable[[Job], None], needs_model: bool) -> None:
        if needs_model:
            if not self.slot.acquire(blocking=False):
                self.jobs.pop(job.id, None)
                raise Busy(f"the model is busy with job {self.working}; stop it or wait for it to finish")
            self.working = job.id
        threading.Thread(target=self._work, args=(job, body, needs_model), daemon=True, name=f"job-{job.id}").start()

    def _work(self, job: Job, body: Callable[[Job], None], needs_model: bool) -> None:
        try:
            if needs_model:
                job.emit(type="waiting", seconds=self.wait_s)
                with self._hold(f"RBMK control room, job {job.id}"):
                    body(job)
            else:
                body(job)
            job.finish("done")
        except Cancelled as exc:
            job.finish("stopped", str(exc))
        except Exception as exc:  # noqa: BLE001 - every failure must reach the browser, not die in the thread
            job.finish("error", f"{type(exc).__name__}: {exc}")
        finally:
            if needs_model:
                self.working = None
                self.slot.release()

    def _backend(self, mode: str, asker: LiveAsker | None, warm_s: float = 0.0) -> dict[str, Any]:
        if asker is None:
            return {"mode": "none", "model": None, "recorded_at": None, "source": "plain policy code, no decision model"}
        return {"mode": mode, "model": asker.model, "recorded_at": datetime.date.today().isoformat(),
                "source": "live run of poolhouse.decide, backend pointer", "warm_up_s": round(warm_s, 1)}

    def _document(self, job: Job, with_whatif: bool) -> dict[str, Any]:
        runner = job.runner
        scn, res = runner.scn, runner.res
        meta = {"backend": job.backend, "clocks": clocks(scn, res)}
        doc = display.export(res, scn, meta, rods_of(scn), whatif.whatifs(res, scn) if with_whatif and res.trace else None)
        doc["meta"]["error"] = job.error or None
        return doc

    def start_run(self, key: str, controller: str, decider: str = "", memory: str = "none", speed: float = DEFAULT_SPEED) -> Job:
        """Begin a live run of scenario ``key``; the model controller needs the model slot."""
        if decider not in {d["id"] for d in DECIDERS}:
            raise ValueError(f"unknown decider {decider!r}")
        if memory not in MEMORIES:
            raise ValueError(f"memory variant {memory!r} is not available")
        if not speed > 0:
            raise ValueError("speed must be positive")
        scn = scenario_named(key)
        if controller == "crew" and not scn.crew:
            raise ValueError("the crew script only exists for the 1986 replay")
        if controller not in recordings.CONTROLLERS:
            raise ValueError(f"unknown controller {controller!r}")
        label = f"live: {controller}" + (f" {self.model_id or PINNED}" if controller == "model" else "")
        job = Job(uuid.uuid4().hex[:10], "run", key, controller, label)
        self.jobs[job.id] = job
        job.scn = scn
        header = {"scenario": {"key": scn.key, "title": text.TITLES.get(scn.key, scn.key), "description": text.description(scn),
                               "group": scn.group, "seed": scn.seed, "ticks": scn.ticks}, "controller": controller, "memory": memory, "label": label,
                  "rods": rods_of(scn)}
        body = (lambda j: self._model_run(j, scn, header, decider, memory, speed)) if controller == "model" \
            else (lambda j: self._plain_run(j, scn, header, controller))
        self._spawn(job, body, controller == "model")
        return job

    def _plain_run(self, job: Job, scn: scenarios.Scenario, header: dict[str, Any], controller: str) -> None:
        runner = Runner(scn, CrewScript() if controller == "crew" else NaiveDecider())
        job.runner, job.backend = runner, self._backend("none", None)
        feeder = Feeder(job, runner, header)
        try:
            while not runner.plant.done():
                if job.cancel.is_set():
                    raise Cancelled("stopped by the person")
                runner.begin_tick()
                if runner.chain:
                    runner.chain()
                runner.finish_tick()
                if runner.plant.tick % FLUSH_TICKS == 0:
                    feeder.flush()
            runner.finish_run()
        finally:
            self._close(job, feeder)

    def _model_run(self, job: Job, scn: scenarios.Scenario, header: dict[str, Any], decider: str, memory: str, speed: float) -> None:
        asker = self._asker(decider)
        job.emit(type="warming")
        warm = asker.warm()
        self.model_id = asker.model or self.model_id
        runner = Runner(scn, ModelDecider(asker, scn.seed), RunOptions(speed=speed, memory=memory))
        live = LiveRunner(runner, speed=speed, timeout=asker.timeout)
        job.runner, job.backend = runner, self._backend("live", asker, warm)
        feeder = Feeder(job, runner, header)

        async def drive() -> None:
            async def pump() -> None:
                while True:
                    feeder.flush()
                    if asker.model and asker.model != job.model:
                        job.model = asker.model
                        job.emit(type="model", id=asker.model)
                    if job.cancel.is_set():
                        live.stop()
                    await asyncio.sleep(PUMP_S)

            task = asyncio.create_task(pump())
            try:
                await live.run()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        try:
            asyncio.run(drive())
            job.backend = self._backend("live", asker, warm)
            if job.cancel.is_set():
                raise Cancelled("stopped by the person")
        finally:
            self._close(job, feeder)
            if job.status == "running" and runner.res.finished and not job.cancel.is_set():
                self._save(job, scn, memory)

    def _close(self, job: Job, feeder: Feeder) -> None:
        feeder.flush()
        if job.runner.res.trace:
            job.docs = [self._document(job, True)]
            job.emit(type="document", index=0)

    def _save(self, job: Job, scn: scenarios.Scenario, memory: str) -> None:
        """A finished model run goes to the local run store as a recording, the way `run.py record` writes it."""
        res = job.runner.res
        rec = recorder.recording_of(res, scn, {**job.backend, "mode": "recorded"}, clocks(scn, res))
        path = self.folder / job.backend["model"] / memory / f"{scn.key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(recorder.dumps(rec) + "\n")

    # ---- documents ------------------------------------------------------------------------------------
    def document(self, job_id: str, index: int = 0) -> dict[str, Any]:
        """The run document of a job (partial and marked so while the job runs)."""
        job = self.job(job_id)
        if index < len(job.docs):
            return job.docs[index]
        if job.runner is not None and job.runner.res.trace and job.status == "running":
            return self._document(job, False)
        raise KeyError(f"job {job_id} has no document yet")

    def render(self, doc: dict[str, Any]) -> dict[str, Any]:
        """The full run document of a checked recording or run document; a recording is replayed and must reproduce its digest."""
        if not recordings.is_compact(doc):
            return doc
        scn = scenario_of(doc)
        try:
            res = recorder.replay(doc, scn)
        except recorder.ReplayMismatch as exc:
            raise recordings.BadDocument(f"the recording does not replay on this simulator: {exc}") from exc
        return display.export(res, scn, doc["meta"], rods_of(scn), whatif.whatifs(res, scn))

    def recording(self, name: str) -> dict[str, Any]:
        """The rendered run document of the file called ``name`` in the run store."""
        path = self.folder.joinpath(*name.split("/"))
        stamp = (name, path.stat().st_mtime if path.is_file() else 0.0)
        if stamp not in self.rendered:
            self.rendered[stamp] = self.render(recordings.read(self.folder, name))
        return self.rendered[stamp]

    # ---- the poisoned starts ----------------------------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        """The measured end state of each recording of the run store that starts from a poisoned state, and how many are still being replayed."""
        files = [r for r in recordings.listing(self.folder) if r["ok"] and r["kind"] == "recording"
                 and (r["scenario"].startswith(("start_", "poisoned_")))]
        todo = [(r["name"], (self.folder / r["name"]).stat().st_mtime) for r in files]
        with self.lock:
            missing = [s for s in todo if s not in self.outcomes]
            if missing and (self.sweeper is None or not self.sweeper.is_alive()):
                self.sweeper = threading.Thread(target=self._sweep, args=(missing,), daemon=True, name="summary")
                self.sweeper.start()
            rows = [{**r, **self.outcomes[(r["name"], m)]} for r, (_, m) in zip(files, todo, strict=True) if (r["name"], m) in self.outcomes]
        return {"rows": rows, "pending": len(todo) - len(rows)}

    def _sweep(self, missing: list[tuple[str, float]]) -> None:
        for name, mtime in missing:
            try:
                doc = recordings.read(self.folder, name)
                res = recorder.replay(doc, scenario_of(doc))
                out = text.outcome(res)
            except (recordings.BadDocument, recorder.ReplayMismatch, ModelStopped, KeyError, OSError) as exc:
                out = {"state": "unreadable", "error": str(exc)}
            with self.lock:
                self.outcomes[(name, mtime)] = out

    def info(self) -> dict[str, Any]:
        """Everything the page needs before the first run."""
        scn = [{"key": s.key, "title": text.TITLES.get(s.key, s.key), "description": text.description(s), "group": s.group,
                "ticks": s.ticks, "crew": s.crew} for s in scenarios.SCENARIOS.values()]
        return {"scenarios": scn, "model": self.model_status(), "working": self.working, "deciders": list(DECIDERS),
                "memories": list(MEMORIES), "speeds": list(SPEEDS), "default_speed": DEFAULT_SPEED,
                "classes": text_classes(), "store_dir": str(self.folder), "schema": display.SCHEMA, "toy_notice": TOY_NOTICE,
                "jobs": [j.public() for j in self.jobs.values()]}


def text_classes() -> dict[str, str]:
    """The desk's action classes and what each means."""
    from ..controls import options as desk

    return dict(desk.CLASSES)

