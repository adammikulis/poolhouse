"""The server's state: the one model, the jobs that use it, and the documents they produce."""

from __future__ import annotations

import datetime
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from .. import bundle, controls, scenarios
from ..bundle import ReplayAsker
from ..operator_model import (
    ABSTAIN_BELOW,
    CrewOperator,
    LiveAsker,
    ModelOperator,
    ModelStopped,
    NaiveOperator,
)
from . import recordings
from .jobs import Busy, Cancelled, Job
from .runner import ForcedOperator, WebRunner, check_forced, divergence, merged, resume

PINNED = "strands-decider-2B-hobson-v19"
MAX_REPEATS = 20


def _rod_layout(scn: scenarios.Scenario) -> list[dict[str, Any]]:
    return [{k: row[k] for k in ("id", "kind", "x", "y", "w")} for row in scn.build_core().rods.table()]


def _header(scn: scenarios.Scenario, controller: str, label: str) -> dict[str, Any]:
    return {"scenario": {"key": scn.key, "title": scn.title, "blurb": scn.blurb, "group": scn.group, "seed": scn.seed,
                         "ticks": scn.ticks, "defeated": list(scn.defeated)},
            "controller": controller, "label": label, "rods": _rod_layout(scn)}


def _operator_for(controller: str, scn: scenarios.Scenario) -> Any:
    if controller == "naive":
        return NaiveOperator()
    if controller == "crew":
        if not scn.crew:
            raise ValueError("the crew script only exists for the 1986 replay")
        return CrewOperator()
    raise ValueError(f"unknown controller {controller!r}")


class Hub:
    """Owns the decision model (loaded on the first run), the compute slot and every job."""

    def __init__(self, folder: Path = recordings.RECORDINGS, wait_s: float = 0.0) -> None:
        self.folder, self.wait_s = folder, wait_s
        self.jobs: dict[str, Job] = {}
        self.slot = threading.Lock()
        self.working: str | None = None
        self.asker: LiveAsker | None = None
        self.model_id = ""
        self.replays: dict[str, Any] = {}
        self.rendered: dict[str, dict[str, Any]] = {}

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

    def _model(self) -> LiveAsker:
        if self.asker is None:
            self.asker = LiveAsker(abstain_below=ABSTAIN_BELOW)
        return self.asker

    def _backend(self, mode: str) -> dict[str, Any]:
        asker = self.asker
        return {"mode": mode, "model": asker.model if asker else None, "abstain_below": ABSTAIN_BELOW,
                "recorded_at": datetime.date.today().isoformat() if asker else None,
                "source": "live run of poolhouse.decide, backend pointer" if asker else "no decision model"}

    # ---- jobs -------------------------------------------------------------------------------------
    def _new(self, kind: str, scenario: str, controller: str, label: str) -> Job:
        job = Job(uuid.uuid4().hex[:10], kind, scenario, controller, label)
        self.jobs[job.id] = job
        return job

    def job(self, job_id: str) -> Job:
        """The job called ``job_id``; KeyError when there is none."""
        return self.jobs[job_id]

    def _spawn(self, job: Job, body: Any, needs_model: bool) -> None:
        if needs_model:
            if not self.slot.acquire(blocking=False):
                self.jobs.pop(job.id, None)
                raise Busy(f"the model is busy with job {self.working}; stop it or wait for it to finish")
            self.working = job.id
        threading.Thread(target=self._work, args=(job, body, needs_model), daemon=True, name=f"job-{job.id}").start()

    def _work(self, job: Job, body: Any, needs_model: bool) -> None:
        try:
            if needs_model:
                from poolhouse.serve import gpu

                job.emit(type="waiting", seconds=self.wait_s)
                with gpu.hold(f"RBMK operator web, job {job.id}", wait_s=self.wait_s):
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

    def _document(self, job: Job, runner: WebRunner, mode: str) -> dict[str, Any]:
        scn = runner.scn
        doc = bundle.export(runner.res, scn, self._backend(mode), with_whatif=False)
        failure = sys.exc_info()[1]
        doc["trace"] = merged(doc["trace"], runner.sink)
        doc["meta"]["error"] = job.error or (f"{type(failure).__name__}: {failure}" if failure else None)
        return doc

    def start_run(self, key: str, controller: str, seed: int | None = None) -> Job:
        """Begin a live run of scenario ``key``; the model controller needs the model slot."""
        scn = scenarios.scenario(key)
        if seed is not None:
            scn = scenarios.variant(scn, seed=int(seed))
        label = f"live: {controller}" + (f" {self.model_id or PINNED}" if controller == "model" else "")
        plain = None if controller == "model" else _operator_for(controller, scn)
        job = self._new("run", key, controller, label)
        job.scn = scn

        def body(j: Job) -> None:
            j.emit(type="header", **_header(scn, controller, label))
            op = ModelOperator(self._model()) if plain is None else plain
            j.runner = WebRunner(scn, op, j)
            try:
                j.runner.run()
            finally:
                if j.runner.res.trace:
                    j.docs = [self._document(j, j.runner, "live")]
                    j.emit(type="document", index=0)

        self._spawn(job, body, controller == "model")
        return job

    def start_batch(self, keys: list[str], repeats: int) -> Job:
        """Run each scenario in ``keys`` ``repeats`` times with the model, one after another."""
        if not 1 <= repeats <= MAX_REPEATS:
            raise ValueError(f"repeats must be 1 to {MAX_REPEATS}")
        plan = [(scenarios.scenario(k), n) for k in keys for n in range(repeats)]
        if not plan:
            raise ValueError("no scenarios named")
        job = self._new("batch", ",".join(keys), "model", f"live batch: {len(plan)} runs")

        def body(j: Job) -> None:
            j.emit(type="plan", items=[{"scenario": s.key, "seed": s.seed + 1000 * n, "title": s.title} for s, n in plan])
            op = ModelOperator(self._model())
            for index, (scn, n) in enumerate(plan):
                scn = scenarios.variant(scn, seed=scn.seed + 1000 * n)
                j.emit(type="item_start", index=index, scenario=scn.key, seed=scn.seed)
                runner = WebRunner(scn, op, j, tag=index)
                runner.quiet = True
                try:
                    runner.run()
                finally:
                    doc = self._document(j, runner, "live")
                    j.docs.append(doc)
                    j.emit(type="item_done", index=index, scenario=scn.key, seed=scn.seed, outcome=doc["outcome"],
                           decisions=sum(1 for d in doc["decisions"] if d["kind"] == "decision"),
                           abstained=sum(1 for d in doc["decisions"] if d["abstained"]), model=j.model)

        self._spawn(job, body, True)
        return job

    # ---- what-if ------------------------------------------------------------------------------------
    def _fork(self, source: dict[str, Any], index: int) -> tuple[Any, scenarios.Scenario, list[dict[str, Any]]]:
        if "job" in source:
            job = self.job(str(source["job"]))
            if job.controller != "model":
                raise ValueError("only a decision-model run can be re-asked")
            if job.runner is None or index not in job.runner.res.forks:
                raise KeyError(f"job {job.id} has no state saved for decision {index}")
            return job.runner.res.forks[index], job.scn, job.runner.res.trace
        name = str(source["recording"])
        doc = self.recording(name)
        runner, scn = self.replays[name]
        if doc["meta"]["controller"] != "model":
            raise ValueError("only a decision-model recording can be re-asked")
        if index not in runner.res.forks:
            raise KeyError(f"the recording has no state saved for decision {index}")
        return runner.res.forks[index], scn, doc["trace"]

    def render(self, rec: dict[str, Any], name: str = "") -> dict[str, Any]:
        """The full run document of a checked recording or run document; a recording is replayed and must reproduce its digest."""
        if not recordings.is_compact(rec):
            return rec
        meta = rec["meta"]
        scn = scenarios.scenario(meta["scenario"]["key"])
        scn = scenarios.variant(scn, seed=meta["scenario"].get("seed", scn.seed), ticks=meta["scenario"].get("ticks", scn.ticks))
        runner = WebRunner(scn, ModelOperator(ReplayAsker(rec)))
        try:
            runner.run()
        except ModelStopped as exc:
            raise recordings.BadDocument(f"the recording does not replay on this simulator: {exc}") from exc
        doc = bundle.export(runner.res, scn, meta["backend"], with_whatif=False)
        if bundle.digest(doc) != rec["digest"]:
            raise recordings.BadDocument("the recording does not reproduce: the replay differs from the run it was made from")
        doc["trace"] = merged(doc["trace"], runner.sink)
        if name:
            self.replays[name] = (runner, scn)
        return doc

    def recording(self, name: str) -> dict[str, Any]:
        """The rendered run document of the recording called ``name``."""
        if name not in self.rendered:
            self.rendered[name] = self.render(recordings.read(self.folder, name), name)
        return self.rendered[name]

    def candidates(self, source: dict[str, Any], index: int, cls: str) -> dict[str, str]:
        """The named targets for ``cls`` in the state at decision ``index`` of a run or recording."""
        fork, _, _ = self._fork(source, index)
        if cls not in controls.CLASSES:
            raise ValueError(f"unknown action class {cls!r}")
        return controls.candidates(cls, fork)

    def start_whatif(self, source: dict[str, Any], index: int, forced: dict[str, Any], horizon: int | None) -> Job:
        """Ask the real model about decision ``index`` again, apply ``forced`` instead, and run on."""
        fork, scn, base = self._fork(source, index)
        choice = check_forced(fork, forced)
        label = f"what-if at tick {fork.tick}: {choice['cls']} {choice['target']}"
        job = self._new("whatif", scn.key, "model", label)

        def body(j: Job) -> None:
            asker = self._model()
            j.emit(type="header", **_header(scn, "model", label), fork_tick=fork.tick, forced=choice, source=source)
            runner = resume(fork, scn, ForcedOperator(ModelOperator(asker), choice), j, horizon)
            j.runner, j.scn = runner, runner.scn
            try:
                runner.run()
            finally:
                doc = self._document(j, runner, "live")
                doc["branch"] = {"from_tick": fork.tick, "forced": choice, "source": source,
                                 "divergence": divergence(base, runner.res.trace)}
                j.docs = [doc]
                j.emit(type="document", index=0)

        self._spawn(job, body, True)
        return job

    # ---- documents ------------------------------------------------------------------------------------
    def document(self, job_id: str, index: int = 0) -> dict[str, Any]:
        """The run document of a job (partial and marked so while the job runs or after it stopped)."""
        job = self.job(job_id)
        if index < len(job.docs):
            return job.docs[index]
        if job.runner is not None and job.runner.res.trace and job.status == "running":
            return self._document(job, job.runner, "live")
        raise KeyError(f"job {job_id} has no document yet")

    def info(self) -> dict[str, Any]:
        """Everything the page needs before the first run."""
        scn = [{"key": s.key, "title": s.title, "blurb": s.blurb, "group": s.group, "ticks": s.ticks, "crew": s.crew}
               for s in scenarios.SCENARIOS.values()]
        return {"scenarios": scn, "model": self.model_status(), "working": self.working,
                "classes": controls.CLASSES, "steps": controls.STEPS, "interlocks": controls.INTERLOCKS,
                "recordings_dir": str(self.folder), "schema": bundle.SCHEMA, "toy_notice": bundle.TOY_NOTICE,
                "jobs": [j.public() for j in self.jobs.values()]}

