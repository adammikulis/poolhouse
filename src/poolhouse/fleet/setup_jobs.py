"""Bounded, durable background installation jobs for the local UI."""
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from poolhouse.graph.store import GraphStore
from poolhouse.lock import Busy, only_one
from poolhouse.serve.process import pid_exists, started_at

from . import llama
from .session import parse_cookie

ACTIVE = {"queued", "installing"}
PENDING = ACTIVE | {"waiting"}
LIMIT = 32
PREPARATION = "first-run-prepare"
STAGES = frozenset({"runtime", "transfer", "verify", "serve", "probe", "ready"})
PROBES = ('chat', 'tool-call', 'tool-result')


class PreparationStopped(RuntimeError):
    """The current preparation may not cross another side-effect boundary."""


class Reporter:
    """Durable stage intents; trusted operations check between indivisible effects."""

    def __init__(self, jobs, row, current):
        self.jobs, self.row, self.current = jobs, row, current

    def check(self):
        row = self.jobs.get(self.row["id"])
        if row["cancel_requested"] or self.current() is not True:
            raise PreparationStopped("Preparation stopped before the next stage.")
        return True

    def local_check(self):
        """Cheap in-process cancellation; durable check remains a stage boundary.

        Another process's cancellation is observed by check(), not this RAM fast
        path. The current callback must be local and must not open a store or RPC.
        """
        with self.jobs._mutex:
            if (self.jobs._running_preparations.get(self.row['id']) is not self.row
                    or self.row['state'] != 'installing' or self.row['cancel_requested']
                    or self.current() is not True):
                raise PreparationStopped('Preparation stopped before the next chunk.')
            return True

    def __call__(self, note):
        self.check()
        self.jobs._update(self.row, note=str(note)[:1000])

    def stage(self, stage, *, done=0, total=0):
        if stage not in STAGES or any(type(n) is not int or n < 0 for n in (done, total)) or (total and done > total):
            raise ValueError("Invalid preparation progress.")
        self.check()
        self.jobs._update(self.row, stage=stage, progress={"done": done, "total": total})
        self.check()

    def probe(self, phase, request_digest, *, response_digest=None, accepted=None):
        """Claim one fixed request once; an existing intent never permits a resend."""
        if (phase not in PROBES or not isinstance(request_digest, str)
                or re.fullmatch(r'[a-f0-9]{64}', request_digest) is None):
            raise ValueError('Invalid preparation probe.')
        completion = None
        if response_digest is not None or accepted is not None:
            if (not isinstance(response_digest, str) or re.fullmatch(r'[a-f0-9]{64}', response_digest) is None
                    or type(accepted) is not bool):
                raise ValueError('Invalid preparation probe receipt.')
            completion = {'response_digest': response_digest, 'status': 'accepted' if accepted else 'refused'}
        return self.jobs._probe(self.row, phase, request_digest, completion, self.current)

    def serving(self, receipt):
        """Retain only fixed identifiers from an independently checked native startup."""
        if (not isinstance(receipt, dict) or set(receipt) != {
                'startup_id', 'artifact_digest', 'runtime_digest', 'parser', 'port', 'context'}
                or any(not isinstance(receipt[key], str) or re.fullmatch(r'[a-f0-9]{64}', receipt[key]) is None
                       for key in ('startup_id', 'artifact_digest', 'runtime_digest', 'parser'))
                or type(receipt['port']) is not int or not 0 < receipt['port'] < 65536
                or type(receipt['context']) is not int or not 0 < receipt['context'] < 2**31):
            raise ValueError('Invalid preparation serving receipt.')
        return self.jobs._serving(self.row, dict(receipt), self.current)


class Jobs:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._mutex = threading.RLock()
        self._pending = []
        self._running_preparations = {}
        self._worker = None
        self._recover()

    def _store(self):
        return GraphStore(self.root / "setup-jobs.db")

    def _recover(self):
        with self._mutex, only_one(self.root / "setup-jobs.lock"), self._store() as graph:
            for node in graph.nodes("setup-job"):
                row = node["attrs"]
                if row["state"] in ACTIVE and (not pid_exists(row["pid"]) or started_at(row["pid"]) != row["born"]):
                    row.update(state="interrupted" if row["kind"] == PREPARATION else "failed",
                               error="Preparation interrupted; check its current state before continuing." if row["kind"] == PREPARATION else "Installation interrupted; retry to continue.",
                               note="Preparation interrupted" if row["kind"] == PREPARATION else "Installation interrupted")
                    self._save(graph, row)

    @staticmethod
    def _save(graph, row):
        graph.upsert_node({"id": row["id"], "kind": "setup-job", "label": row["kind"], "attrs": row})

    def all(self):
        with self._mutex, only_one(self.root / "setup-jobs.lock"), self._store() as graph:
            return sorted((node["attrs"] for node in graph.nodes("setup-job")), key=lambda row: row["created"])

    def get(self, ident):
        return next((row for row in self.all() if row["id"] == ident), None)

    def prepare(self, request, operation, *, current, provenance=None):
        """Trusted preparation only; terminal commands remain immutable dedup tombstones."""
        if (not isinstance(request, dict) or set(request) != {"idempotency_key", "binding", "selection"}
                or not isinstance(request["idempotency_key"], str)
                or re.fullmatch(r"[a-f0-9]{32}", request["idempotency_key"]) is None
                or not isinstance(request["binding"], dict) or not request["binding"]
                or not isinstance(request["selection"], dict) or not callable(operation) or not callable(current)):
            raise ValueError("Invalid preparation request.")
        encoded = json.dumps(request, allow_nan=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode()) > 65536:
            raise ValueError("Preparation request is too large.")
        if current() is not True:
            raise PermissionError("Current setup access is required.")
        try:
            with self._mutex, only_one(self.root / "runtime-install.lock", wait=False):
                return self._enqueue(PREPARATION, json.loads(encoded), operation, provenance=provenance, current=current)
        except Busy:
            raise ValueError("A runtime update is in progress; retry preparation shortly.") from None

    def cancel_preparation(self, ident, *, binding, current):
        if current() is not True:
            raise PermissionError("Current setup access is required.")
        with self._mutex:
            with only_one(self.root / "setup-jobs.lock"), self._store() as graph, graph.transaction():
                row = next((node["attrs"] for node in graph.nodes("setup-job") if node["id"] == ident), None)
                if row is None or row["kind"] != PREPARATION or row["request"]["binding"] != binding:
                    raise KeyError(ident)
                if row["state"] in ACTIVE:
                    row.update(cancel_requested=True)
                    self._save(graph, row)
                if current() is not True:
                    raise PermissionError("Current setup access is required.")
            active = self._running_preparations.get(ident)
            if active is not None:
                active['cancel_requested'] = row['cancel_requested']
            return row

    def active(self):
        return any(row["state"] in ACTIVE for row in self.all())

    def start(self, kind, request, operation, *, provenance=None):
        try:
            with only_one(self.root / "runtime-install.lock", wait=False):
                return self._enqueue(kind, request, operation, provenance=provenance)
        except Busy:
            raise ValueError("A runtime update is in progress; retry the installation shortly.") from None

    def _enqueue(self, kind, request, operation, *, provenance=None, current=None):
        with self._mutex, only_one(self.root / "setup-jobs.lock"), self._store() as graph, graph.transaction():
            if current is not None and current() is not True:
                raise PermissionError("Current setup access is required.")
            rows = [node["attrs"] for node in graph.nodes("setup-job")]
            if kind == PREPARATION:
                prior = next((row for row in rows if row["kind"] == kind
                              and row["request"]["binding"] == request["binding"]
                              and row["request"]["idempotency_key"] == request["idempotency_key"]), None)
                if prior is not None:
                    if prior["request"] != request:
                        raise ValueError("This preparation key already identifies a different selection.")
                    if current() is not True:
                        raise PermissionError("Current setup access is required.")
                    return prior
            existing = next((row for row in rows if row["kind"] == kind and row["request"] == request and row["state"] in PENDING), None)
            if existing:
                return existing
            active = [row for row in rows if row["state"] in PENDING]
            if len(active) >= LIMIT:
                raise ValueError("The setup installation queue is full; wait for a job to finish.")
            finished = sorted((row for row in rows if row["state"] not in PENDING and row["kind"] != PREPARATION), key=lambda row: row["created"])
            for row in finished[:max(0, len(rows) - LIMIT + 1)]:
                graph.query("MATCH (n:Node {id:$id}) DETACH DELETE n", {"id": row["id"]})
            row = {"id": uuid.uuid4().hex, "kind": kind, "request": request, "provenance": provenance or {}, "state": "queued" if operation else "waiting", "note": "Waiting to install", "error": "", "result": {}, "created": time.time(), "pid": os.getpid(), "born": started_at(os.getpid())}
            if kind == PREPARATION:
                row.update(stage="", progress={"done": 0, "total": 0}, cancel_requested=False)
            self._save(graph, row)
            if current is not None and current() is not True:
                raise PermissionError("Current setup access is required.")
        if operation:
            self._schedule(row, operation, current)
        return dict(row)

    def _schedule(self, row, operation, current):
        with self._mutex:
            entry = (row, operation, current)
            self._pending.append(entry)
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, daemon=True, name="setup-installations")
                started = False
                try:
                    self._worker.start()
                    started = True
                finally:
                    if not started:
                        self._pending.remove(entry)
                        self._worker = None
                        self._update(row, state="interrupted" if row["kind"] == PREPARATION else "failed",
                                     error="The setup worker could not start.", note="Setup stopped")

    def _update(self, row, **changes):
        with self._mutex, only_one(self.root / "setup-jobs.lock"), self._store() as graph:
            latest = next((node["attrs"] for node in graph.nodes("setup-job") if node["id"] == row["id"]), None)
            if latest is not None:
                row.update(latest)
            row.update(changes)
            self._save(graph, row)

    def _finish_preparation(self, row, result, current):
        with self._mutex, only_one(self.root / "setup-jobs.lock"), self._store() as graph, graph.transaction():
            latest = next(node["attrs"] for node in graph.nodes("setup-job") if node["id"] == row["id"])
            if latest["cancel_requested"] or current() is not True:
                raise PreparationStopped("Preparation stopped before completion.")
            latest.update(state="done", note="Preparation complete", result=result)
            self._save(graph, latest)
            if current() is not True:
                raise PreparationStopped("Preparation stopped before completion.")
            row.update(latest)

    def _probe(self, row, phase, request_digest, completion, current):
        with self._mutex, only_one(self.root / "setup-jobs.lock"), self._store() as graph, graph.transaction():
            latest = next(node['attrs'] for node in graph.nodes('setup-job') if node['id'] == row['id'])
            if (latest['kind'] != PREPARATION or latest['state'] != 'installing'
                    or latest['cancel_requested'] or current() is not True):
                raise PreparationStopped('Preparation stopped before probe receipt.')
            if not latest.get('serving_receipt'):
                raise ValueError('A checked serving receipt is required before probing.')
            receipts = latest.setdefault('probes', {})
            old = receipts.get(phase)
            fresh = old is None
            if old is None:
                if completion is not None or latest.get('stage') != 'probe':
                    raise ValueError('A probe intent is required before its receipt.')
                previous = PROBES.index(phase) - 1
                if previous >= 0 and receipts.get(PROBES[previous], {}).get('status') != 'accepted':
                    raise ValueError('The previous fixed probe has not succeeded.')
                old = {'id': hashlib.sha256(f'setup-probe/v1:{row["id"]}:{phase}'.encode()).hexdigest(),
                       'request_digest': request_digest, 'startup_id': latest['serving_receipt']['startup_id'],
                       'status': 'intent'}
                receipts[phase] = old
            elif (old['request_digest'] != request_digest
                  or old.get('startup_id') != latest['serving_receipt']['startup_id']):
                raise ValueError('This fixed probe already has a different request.')
            if completion is not None:
                if old['status'] != 'intent' and any(old.get(key) != value for key, value in completion.items()):
                    raise ValueError('This fixed probe already has a different receipt.')
                old.update(completion)
            self._save(graph, latest)
            if current() is not True:
                raise PreparationStopped('Preparation stopped before probe receipt.')
            result = {**old, 'fresh': fresh}
            row.update(latest)
        return result

    def _serving(self, row, receipt, current):
        with self._mutex, only_one(self.root / "setup-jobs.lock"), self._store() as graph, graph.transaction():
            latest = next(node['attrs'] for node in graph.nodes('setup-job') if node['id'] == row['id'])
            if (latest['kind'] != PREPARATION or latest['state'] != 'installing'
                    or latest['cancel_requested'] or current() is not True):
                raise PreparationStopped('Preparation stopped before serving receipt.')
            old = latest.get('serving_receipt')
            if old is not None and old != receipt:
                raise ValueError('Preparation already has a different serving receipt.')
            latest['serving_receipt'] = dict(receipt)
            self._save(graph, latest)
            if current() is not True:
                raise PreparationStopped('Preparation stopped before serving receipt.')
            row.update(latest)
        return dict(receipt)

    def _run(self):
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="setup-operation") as executor:
            while True:
                with self._mutex:
                    if not self._pending:
                        self._worker = None
                        return
                    row, operation, current = self._pending.pop(0)
                outcome = executor.submit(self._install, row, operation, current)
                failure = outcome.exception()
                if failure is None:
                    continue
                if not isinstance(failure, Exception):
                    raise failure
                if row["kind"] == PREPARATION:
                    self._update(row, state="cancelled" if self.get(row["id"])["cancel_requested"] else "interrupted",
                                 error="Preparation stopped; check its current state before continuing.", note="Preparation stopped")
                    continue
                logging.getLogger(__name__).error("Setup installation failed", exc_info=(type(failure), failure, failure.__traceback__))
                self._update(row, state="failed", error=str(failure)[:2000], note="Installation failed")

    def _install(self, row, operation, current=None):
        with only_one(self.root / "setup-install.lock"):
            self._update(row, state="installing", note="Preparing installation")
            if row["kind"] == PREPARATION:
                with self._mutex:
                    self._running_preparations[row['id']] = row
                try:
                    progress = Reporter(self, row, current)
                    progress.check()
                    result = operation(progress)
                    progress.check()
                    self._finish_preparation(row, result, current)
                finally:
                    with self._mutex:
                        self._running_preparations.pop(row['id'], None)
                return
            result = operation(lambda note, current=row: self._update(current, note=str(note)[:1000]))
            errors = [str(value.get("error") or "Installation failed") for value in result.get("changed", {}).values() if not value.get("ok")]
            if errors:
                self._update(row, state="failed", error="; ".join(errors)[:2000], note="Installation failed", result=result)
            else:
                self._update(row, state="done", note="Installation complete", result=result)


def jobs(ui):
    with ui._setup_jobs_lock:
        if ui.setup_jobs is None:
            ui.setup_jobs = Jobs(ui.root)
        return ui.setup_jobs


def libraries(ui, vendor, add, drop, progress):
    changed = {}
    if drop:
        progress("Removing selected libraries")
        changed.update(ui.environment.uninstall(drop))
    if add:
        settings = ui.settings
        if settings is not None and settings.download_sources not in ("internet", "both"):
            raise RuntimeError("Installing libraries requires Internet only or Both download sources.")
        changed.update(ui.environment.install(add, on_progress=progress))
    return {"changed": changed, **ui.environment.state(vendor)}


def server(ui, progress):
    got = llama.ensure_server(ui.root, on_progress=progress, sources=ui.settings.download_sources if ui.settings else "both")
    return {"ok": True, "server": str(got)}


def provenance(route):
    sessions = getattr(route.ui, "sessions", None)
    session = sessions.get(parse_cookie(getattr(route, "cookie", ""))) if sessions else None
    return {"channel": "local-ui", "authentication": session.who if session else "loopback-setup", "client": getattr(route, "client_ip", "")}
