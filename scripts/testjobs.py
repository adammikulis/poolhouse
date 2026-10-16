"""Background test jobs: records, a detached runner, status, wait, result and cancel."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path

import testreuse_store as storage
import testscope

KEEP_S = 3 * 86400.0
POLL_S = 0.4
WATCH_S = 1.0
ENDS_S = 60.0
KILLED_S = 10.0
STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP)
RESEND_S = 10.0
"""How long a job recorded ``cancelling`` is left to its cancel before another cancel stops its runner again."""
LIVE = ("queued", "running", "cancelling")
TERMINAL = ("done", "failed", "cancelled")
FULL_TIERS = ("fast", "full", "slow", "all", "quick", "gate")


class Refused(Exception):
    """A request the job table will not take."""


class Busy(Refused):
    """A whole-tier job is requested while another is live."""


def is_full(argv: list[str], root: Path) -> bool:
    """Whether a job runs a whole tier: a tier name with no test path in its arguments."""
    return bool(argv) and argv[0] in FULL_TIERS and not any(
        not a.startswith("-") and (root / a.split("::")[0]).exists() for a in argv[1:])


def node_id(classname: str, name: str, root: Path) -> str:
    """The pytest node id of a junit test case: file, then any classes, then the test name."""
    parts = classname.split(".")
    for size in range(len(parts), 0, -1):
        path = "/".join(parts[:size]) + ".py"
        if (root / path).is_file():
            return "::".join([path, *parts[size:], name])
    return "::".join([classname.replace(".", "/") + ".py", name])


def interrupt(*_: object) -> None:
    """Turn a stop signal into a KeyboardInterrupt in the waiting code."""
    raise KeyboardInterrupt


@contextlib.contextmanager
def stop_as_interrupt() -> Iterator[None]:
    """Within the block, SIGTERM and SIGHUP raise KeyboardInterrupt; the handlers are restored after."""
    previous = [(number, signal.signal(number, interrupt)) for number in STOP_SIGNALS]
    try:
        yield
    finally:
        for number, handler in previous:
            signal.signal(number, handler)


def follower() -> dict:
    """This process as the follower of a foreground job."""
    return {"pid": os.getpid(), "started": storage.process_start(os.getpid())}


class Jobs:
    """The jobs of one project, under ``folder``."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder / "jobs"

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        """Hold the job table's lock."""
        self.folder.mkdir(parents=True, exist_ok=True)
        with (self.folder / "lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def path(self, job: str) -> Path:
        """The directory of ``job``."""
        if not job.replace("-", "").isalnum():
            raise Refused(f"no job {job!r}")
        return self.folder / job

    def read(self, job: str, name: str = "status.json") -> dict:
        """A job's status or spec; ``{}`` when absent, and ``{}`` with a line on stderr when the file is there and unreadable."""
        try:
            return json.loads((self.path(job) / name).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            print(f"test: job {job}: {name} is unreadable ({type(error).__name__}); it is treated as missing", file=sys.stderr)
            return {}

    def write(self, job: str, name: str, data: dict) -> None:
        """Replace a job's status or spec."""
        storage.write_atomic(self.path(job) / name, json.dumps(data))

    def ids(self) -> list[str]:
        """Every job id, oldest first."""
        return sorted(p.name for p in self.folder.iterdir() if p.is_dir()) if self.folder.is_dir() else []

    def state(self, job: str) -> dict:
        """The job's status with a dead runner reported as failed."""
        status = self.read(job)
        if status.get("state") in LIVE and not storage.alive(
                int(status.get("pid", 0)), float(status.get("started", 0))) and time.time() - status.get("at", 0) > 5:
            status = {**status, "state": "failed", "detail": "the runner process is gone"}
            self.write(job, "status.json", status)
        return status

    def live_full(self) -> list[str]:
        """Ids of unfinished jobs that run a whole tier."""
        held = []
        for job in self.ids():
            if self.state(job).get("state") not in ("queued", "running"):
                continue
            spec = self.read(job, "spec.json")
            if not isinstance(spec, dict):
                held.append(job)
                continue
            directory, argv = spec.get("root"), spec.get("argv")
            valid = (isinstance(directory, str) and Path(directory).is_absolute()
                     and Path(directory).is_dir() and isinstance(argv, list) and bool(argv)
                     and all(isinstance(argument, str) and argument for argument in argv))
            if not valid or is_full(argv, Path(directory)):
                held.append(job)
        return held

    def prune(self) -> None:
        """Remove finished jobs older than three days."""
        for job in self.ids():
            status = self.state(job)
            if status.get("state") in TERMINAL and time.time() - status.get("finished", time.time()) > KEEP_S:
                for child in self.path(job).iterdir():
                    child.unlink(missing_ok=True)
                self.path(job).rmdir()

    def submit(self, argv: list[str], owner: dict, root: Path, script: Path, extra: dict) -> str:
        """Record a job and start its detached runner; returns the id."""
        return self.start(argv, owner, root, script, extra)[0]

    def start(self, argv: list[str], owner: dict, root: Path, script: Path, extra: dict) -> tuple[str, bool]:
        """Record a job and start its detached runner, or join the queued or running job that has the same ``extra['key']``; the id and whether it joined.

        A whole-tier job (``extra['exclusive']``, true unless given) is refused while another whole-tier job is live; the check and the
        record are made under one lock. A foreground run is not exclusive and queues in the broker. A ``extra['follower']`` (the process
        that shows the job's output) is counted, and the job is stopped by its runner once its last follower has gone.
        An agent's whole-suite run, one naming every test file, and a run with no owner are refused before anything is recorded.
        """
        key, follower = extra.get("key"), extra.get("follower")
        extra = {k: v for k, v in extra.items() if k not in ("key", "follower")}
        if refusal := testscope.tier_refusal(argv[0], argv[1:], root) if argv else "":
            raise Refused(refusal)
        if not (isinstance(owner, dict) and isinstance(owner.get("id"), str) and owner["id"]):
            raise Refused("this run has no workspace identity, so it is not queued: run it as the session the harness gave you "
                          "(`poolhouse-workspace whoami` names it)")
        self.prune()
        job = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        with self.locked():
            if key and key.get("tree") and (joined := self.matching(key)):
                if follower:
                    self.write(joined, "followers.json", [*self.followers(joined), follower])
                return joined, True
            held = self.live_full() if extra.get("exclusive", True) and is_full(argv, root) else []
            if held:
                spec = self.read(held[0], "spec.json")
                owner = spec.get("owner") if isinstance(spec, dict) else None
                identity = owner.get("id") if isinstance(owner, dict) else None
                who = identity if isinstance(identity, str) and identity else "unknown"
                raise Busy(f"job {held[0]} ({who}) already runs a whole tier; wait for it or cancel it")
            self.write(job, "spec.json", {"id": job, "argv": argv, "owner": owner, "created": time.time(),
                                          "secret": secrets.token_hex(16), **extra, **({"key": key} if key else {}),
                                          "root": str(root.resolve())})
            self.write(job, "status.json", {"state": "queued", "at": time.time()})
            if follower:
                self.write(job, "followers.json", [follower])
        log = (self.path(job) / "log").open("ab")
        child = subprocess.Popen([sys.executable, str(script), "_job", job, *self.passthrough(extra)], cwd=root,
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True, env={**os.environ, "DEV_TEST_JOB": job})
        with self.locked():
            current = self.read(job)
            if not current.get("pid"):
                self.write(job, "status.json", {**current, "pid": child.pid, "started": storage.process_start(child.pid), "at": time.time()})
        return job, False

    def matching(self, key: dict) -> str:
        """The id of a queued or running job whose key is ``key``, or an empty string."""
        for job in self.ids():
            spec = self.read(job, "spec.json")
            if spec.get("key") == key and self.state(job).get("state") in ("queued", "running"):
                return job
        return ""

    def followers(self, job: str) -> list[dict]:
        """The processes that are showing the job's output and are still running."""
        found = self.read(job, "followers.json")
        return [f for f in (found if isinstance(found, list) else []) if storage.alive(int(f.get("pid", 0)), float(f.get("started", 0)))]

    def candidates(self, argv: list[str]) -> list[str]:
        """Ids of queued or running jobs with these arguments, whose tree may be the same."""
        return [job for job in self.ids() if self.state(job).get("state") in ("queued", "running")
                and self.read(job, "spec.json").get("argv") == argv]

    def record_tree(self, job: str, tree: str) -> None:
        """Record the tree the job runs in as part of its key, so a run with the same arguments and tree can join it."""
        with self.locked():
            spec = self.read(job, "spec.json")
            if tree and isinstance(spec.get("key"), dict):
                self.write(job, "spec.json", {**spec, "key": {**spec["key"], "tree": tree}})

    def mark_running(self, job: str, fields: dict) -> None:
        """Record that the job's runner has started, keeping a ``cancelling`` mark that arrived first."""
        with self.locked():
            cancelling = self.read(job).get("state") == "cancelling"
            self.write(job, "status.json", {**fields, "state": "cancelling" if cancelling else "running", "at": time.time()})

    @staticmethod
    def passthrough(extra: dict) -> list[str]:
        """Arguments the detached runner needs besides its environment."""
        return [x for k in ("task",) if extra.get(k) for x in (f"--{k}", extra[k])]

    def wait(self, job: str, timeout: float, tail: bool = False) -> dict:
        """Block until the job ends or ``timeout`` seconds pass; optionally copy its log to stdout."""
        deadline, sent = time.monotonic() + timeout, 0
        while True:
            status = self.state(job)
            if tail:
                sent = self.copy_log(job, sent)
            if status.get("state") in TERMINAL or time.monotonic() >= deadline:
                return status
            time.sleep(POLL_S)

    def copy_log(self, job: str, offset: int) -> int:
        """Write the job's log from ``offset`` to stdout and return the new offset."""
        try:
            data = (self.path(job) / "log").read_bytes()
        except OSError:
            return offset
        sys.stdout.write(data[offset:].decode("utf-8", errors="replace"))
        sys.stdout.flush()
        return len(data)

    def follow(self, argv: list[str], owner: dict, root: Path, script: Path, extra: dict) -> int:
        """Run ``argv`` as a job, show its log until it ends and return its exit status.

        A run with the same arguments and tree as a queued or running job follows that job and says so. A SIGTERM, SIGHUP or Ctrl-C leaves
        the job running while another run follows it, and otherwise cancels it and waits for its runner to end; the status is then 130.
        A run that joined a job is told, with status 4, when the job's tree is not the tree it joined.
        """
        job, key = "", extra.get("key") or {}
        with stop_as_interrupt():
            try:
                job, joined = self.start(argv, owner, root, script, {**extra, "exclusive": False, "follower": follower()})
                if joined:
                    print(f"test: following job {job}, same tree and selectors", flush=True)
                status = self.wait(job, float("inf"), tail=True)
            except KeyboardInterrupt:
                if job:
                    print(f"\n{self.cancel(job, owner.get('id', ''), leaving=os.getpid())}", flush=True)
                return 130
        if joined and status.get("state") == "done" and status.get("exit") == 0 and status.get("tree") != key.get("tree"):
            print("test: the job's checkout changed after this run joined it; its pass does not describe this run's tree", file=sys.stderr)
            return 4
        return int(status.get("exit", 1))

    def watch_follower(self, job: str) -> None:
        """Stop this process when every foreground run that follows ``job`` has gone; a job nobody follows is left alone."""
        if not self.followers(job):
            return

        def watch() -> None:
            while self.followers(job):
                time.sleep(WATCH_S)
            with contextlib.suppress(OSError), (self.path(job) / "log").open("a", encoding="utf-8") as log:
                log.write("test: cancelled because the run that was following it ended\n")
            os.kill(os.getpid(), signal.SIGTERM)

        threading.Thread(target=watch, daemon=True).start()

    def cancel(self, job: str, who: str, leaving: int = 0) -> str:
        """Stop a job whoever owns it, log who did and wait for its runner to end; returns what happened.

        The process ``leaving`` stops following the job, which keeps running while other runs follow it. The job is recorded
        ``cancelling`` while its runner ends, so no run joins it, and goes back to its earlier state when the runner does not end.
        """
        if not self.read(job, "spec.json"):
            raise Refused(f"no job {job}")
        with self.locked():
            before = self.state(job)
            if before.get("state") in TERMINAL:
                return f"job {job} already {before['state']}"
            if before.get("state") == "cancelling":
                if time.time() - float(before.get("at", 0)) <= RESEND_S:
                    return f"job {job} is already being cancelled"
                before = {**before, "state": before.get("was", "running")}
            if leaving:
                left = [f for f in self.followers(job) if f.get("pid") != leaving]
                self.write(job, "followers.json", left)
                if left:
                    return f"left job {job} running for {len(left)} other follower(s)"
            self.write(job, "status.json", {**before, "state": "cancelling", "at": time.time(), "was": before.get("state")})
        status = {k: v for k, v in before.items() if k != "was"}
        with_pid = int(status.get("pid", 0))
        try:
            if storage.alive(with_pid, float(status.get("started", 0))):
                with (self.path(job) / "log").open("a", encoding="utf-8") as log:
                    log.write(f"test: cancelled by {who or 'a caller with no workspace identity'} running in {Path.cwd()}\n")
                os.kill(with_pid, signal.SIGTERM)
                self.reap(job, with_pid, float(status.get("started", 0)))
        except OSError:
            self.undo(job, status)
            raise
        self.write(job, "status.json", {**status, "state": "cancelled", "exit": 130, "finished": time.time()})
        return f"job {job} cancelled"

    def undo(self, job: str, before: dict) -> None:
        """Put the job's status back as it was when a cancel could not end its runner."""
        with self.locked():
            if self.read(job).get("state") == "cancelling":
                self.write(job, "status.json", before)

    def reap(self, job: str, pid: int, started: float) -> None:
        """Wait for the runner ``pid`` to end, kill its process group after ``ENDS_S``, and raise OSError if it stays."""
        for limit, kill in ((ENDS_S, False), (KILLED_S, True)):
            if kill:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(pid, signal.SIGKILL)
            deadline = time.monotonic() + limit
            while storage.alive(pid, started):
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
            else:
                return
        raise OSError(f"job {job}: the runner (pid {pid}) is still running after it was cancelled")

    def result(self, job: str, root: Path) -> dict:
        """The job's status, summary, failing node ids and junit files."""
        status = self.state(job)
        junits = sorted(self.path(job).glob("junit-*.xml"))
        failing = []
        for junit in junits:
            for case in ET.parse(junit).getroot().iter("testcase"):  # noqa: S314 - written by pytest
                if any(child.tag in ("failure", "error") for child in case):
                    failing.append(node_id(case.get("classname", ""), case.get("name", ""), root))
        return {**status, "failing": failing, "junit": [str(j) for j in junits]}
