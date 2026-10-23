"""Machine-wide, file-lock-backed CPU permits and subprocess test admission."""
from __future__ import annotations

import contextlib
import contextvars
import json
import math
import os
import secrets
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import testqueue
import testslots_policy as policy

from poolhouse.lock import release, take

HEAVY_MODULES = frozenset({
    "test_serve_real_llama", "test_sentinel_real_model", "test_sentinel_wiring_serve",
    "test_serve_broker",
    "test_serve_three_callers", "test_spec_serve", "test_fleet_daemon", "test_fleet_bind",
    "test_fleet_join", "test_fleet_bench", "test_graph_bench", "test_graph_bench_animate",
    "test_graph_store_scale", "test_real_model_uses", "test_redteam_judge_real", "test_redteam_loop",
    "test_redteam_pyrit", "test_sandbox_gpu", "test_serve_llamacpp_real",
    "test_agent_real_model", "test_rbmk_app", "test_rbmk_engine_real", "test_rbmk_room", "test_rbmk_web",
    "test_redteam_lab_home", "test_redteam_sentinel_model", "test_serve_fit_real",
})
"""Test modules that take a heavy lane (a real model server, a benchmark, a fleet daemon); every real_model module is in it."""

WAIT_S = 3600.0
ALLOCATION = "allocation.json"
"""The directory's record of the last admission pass: cap, workers in use, and the first requests in grant order."""
FAST_S, SLOW_S = 0.005, 0.1
"""Seconds between a waiter's looks at its own record: when the record is near the front of the queue, and when it is not."""
REFRESH_S = 0.3
"""The longest a waiter lets the admission pass go unrun while it waits (load, holders that died, aged requests)."""
ALIVE_EVERY_S, STABLE_S, SCAN = 0.25, 0.05, 64
"""Seconds between liveness checks of one record, the age at which a record may be cached, candidates tried per grant."""


class WaitExpired(TimeoutError):
    """A request waited as long as the wait limit and was not granted."""


EXPORT_LEASE = contextvars.ContextVar("export_test_lease", default=True)
CHECK_CANCELLED = contextvars.ContextVar("check_test_cancelled", default=lambda: None)

def slots_dir() -> Path:
    d = Path(os.environ.get("DEV_TEST_SLOTS_DIR") or Path.home() / ".cache" / "dev-test-slots")
    d.mkdir(parents=True, exist_ok=True)
    return d


def pin_slots_dir() -> None:
    """Set ``DEV_TEST_SLOTS_DIR`` to the machine-wide directory when it is unset; a set one is left alone and not made."""
    if "DEV_TEST_SLOTS_DIR" not in os.environ:
        os.environ["DEV_TEST_SLOTS_DIR"] = str(slots_dir())


def _cores() -> int:
    return os.cpu_count() or 4


def wait_limit(environment: dict[str, str] | None = None) -> float:
    """Seconds a run waits for workers or for its collection before it gives up: ``DEV_TEST_WAIT_S``, default `WAIT_S`."""
    return float((os.environ if environment is None else environment).get("DEV_TEST_WAIT_S", WAIT_S))


def reserved_cores() -> int:
    """Logical cores the budget leaves to the machine: ``DEV_TEST_RESERVED_CORES``, default 1."""
    return max(0, int(os.environ.get("DEV_TEST_RESERVED_CORES", "1")))


def base_budget(cores: int | None = None) -> int:
    return max(1, (cores or _cores()) - reserved_cores())


def budget_words() -> str:
    """Where the worker budget comes from: its value, the cores it is derived from and the variables that change it."""
    return (f"the worker budget is {budget()} (DEV_TEST_BUDGET when set, otherwise the {_cores()} logical cores less "
            f"{reserved_cores()} reserved: DEV_TEST_RESERVED_CORES)")


def budget() -> int:
    """The worker budget: ``DEV_TEST_BUDGET`` when set, else the logical CPU count less the reserved cores."""
    raw = os.environ.get("DEV_TEST_BUDGET")
    if raw and raw.isdigit() and int(raw) > 0:
        return int(raw)
    return base_budget()


@dataclass
class Slot:
    path: Path
    data: dict

    @property
    def granted(self) -> int:
        return int(self.data.get("granted", 0))


@contextlib.contextmanager
def _mutex(d: Path, cancellable: bool = True) -> Iterator[None]:
    """Hold the directory mutex; a wait for it stops with the caller's cancellation unless ``cancellable`` is false."""
    with (d / "mutex.lock").open("a+") as fh:
        while not take(fh):
            if cancellable:
                CHECK_CANCELLED.get()()
            time.sleep(0.01)
        try:
            yield
        finally:
            release(fh)


def _alive(path: Path) -> bool:
    """True while another process holds the slot's file lock."""
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        if not take(fd):
            return True
        release(fd)
        return False
    finally:
        os.close(fd)


def _owns_inode(fd: int, path: Path) -> bool:
    try:
        held, published = os.fstat(fd), path.stat()
        return (held.st_dev, held.st_ino) == (published.st_dev, published.st_ino)
    except OSError:
        return False


def _valid_record(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    numbers = ("pid", "want", "minimum", "granted")
    if any(type(data.get(key)) is not int for key in numbers):
        return False
    return (data["pid"] > 0 and data["want"] >= 0 and data["minimum"] > 0
            and data["granted"] >= 0 and (not data["want"] or max(data["minimum"], data["granted"]) <= data["want"])
            and isinstance(data.get("label"), str) and isinstance(data.get("token"), str)
            and type(data.get("backfills", 0)) is int and data.get("backfills", 0) >= 0
            and data.get("class", policy.INTERACTIVE) in policy.CLASSES)


def _delete_shared_descriptor(path: Path) -> int:
    """Open a Windows record handle allowing replacement while its lock is held."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    create = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    handle = create(str(path), 0xC0000000, 7, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return msvcrt.open_osfhandle(handle, os.O_RDWR)


def _publish(path: Path, record: dict) -> int:
    """Publish a complete record whose inode already holds its liveness lock."""
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".pending")
    try:
        if sys.platform == "win32":
            os.close(fd)
            fd = _delete_shared_descriptor(Path(name))
        if not take(fd):
            raise RuntimeError("testslots: unpublished lease already locked")
        os.lseek(fd, 0, os.SEEK_SET)
        with os.fdopen(fd, "w", closefd=False) as stream:
            json.dump(record, stream)
        Path(name).replace(path)
        return fd
    except BaseException:
        os.close(fd)
        raise
    finally:
        Path(name).unlink(missing_ok=True)


_RECORDS: dict[str, tuple[tuple[int, int, int], dict]] = {}
_VERIFIED: dict[str, float] = {}


def _record(path: Path, wall: float) -> dict | None:
    """The record in ``path``, parsed again only when the file changed; a record written in the last moments is never cached."""
    try:
        stat = path.stat()
    except OSError:
        return None
    signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
    cached = _RECORDS.get(str(path))
    if cached is not None and cached[0] == signature:
        return cached[1]
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if wall - stat.st_mtime >= STABLE_S and isinstance(data, dict):
        _RECORDS[str(path)] = (signature, data)
    return data


def _read(d: Path, mine: Path | None = None) -> list[Slot]:
    """The valid records of live leases; a record whose holder was last seen alive over ``ALIVE_EVERY_S`` ago is checked again and removed when its holder is gone."""
    out: list[Slot] = []
    now, wall = time.monotonic(), time.time()
    seen = set()
    for p in sorted(d.glob("*.slot")):
        key = str(p)
        seen.add(key)
        if p != mine and now - _VERIFIED.get(key, -ALIVE_EVERY_S * 2) >= ALIVE_EVERY_S:
            if not _alive(p):
                with contextlib.suppress(OSError):
                    p.unlink()
                _RECORDS.pop(key, None)
                _VERIFIED.pop(key, None)
                continue
            _VERIFIED[key] = now
        data = _record(p, wall)
        if data is not None and _valid_record(data):
            out.append(Slot(p, dict(data)))
    for table in (_RECORDS, _VERIFIED):
        for key in table.keys() - seen:
            del table[key]
    return out


def status() -> dict:
    d = slots_dir()
    with _mutex(d):
        slots = _read(d)
    running = [s for s in slots if s.granted > 0]
    now = time.time()
    return {"budget": budget(), "base": base_budget(), "in_use": sum(s.granted for s in running),
            "running": [s.data for s in running], "waiting": [s.data for s in slots if s.granted == 0],
            "runs": testqueue.read_runs(d), "first": _blocker(_plan(slots, budget(), now), now)}


def _blocker(plan: Plan, now: float) -> str:
    """Who is first in line and why it is not running, or an empty string when nothing waits."""
    if not plan.waiting:
        return ""
    head, free = plan.waiting[0].data, plan.cap - plan.used
    if free < head["minimum"]:
        why = f"it needs {head['minimum']} workers and {free} of {plan.cap} are free"
    else:
        why = f"{free} of {plan.cap} workers are free and the next admission pass grants it"
    return f"first in line: pid {head['pid']} {head.get('label')}, waiting {now - float(head.get('since', now)):.0f}s: {why}"


@dataclass
class Lease:
    workers: int
    waited: float


def _reject_nested() -> None:
    if (os.environ.get("DEV_TEST_REMOTE_LEASE")
            and os.environ.get("DEV_TEST_REMOTE_BROKER") == str(slots_dir().resolve())):
        import testslots_rpc
        with testslots_rpc.request("acquire", label="nested CPU preflight", phase="collection"):
            pass
    inherited = os.environ.get("DEV_TEST_LEASE")
    if inherited:
        try:
            descriptor = json.loads(inherited)
            inherited_path = Path(descriptor["path"])
            record = json.loads(inherited_path.read_text())
            if (inherited_path.parent.resolve() == slots_dir().resolve()
                    and record.get("token") == descriptor["token"] and _alive(inherited_path)):
                raise RuntimeError("testslots: nested CPU run inside a live test lease; run it after releasing the parent permit")
        except (OSError, ValueError, KeyError, TypeError):
            pass


@contextlib.contextmanager
def _lease_environment(path: Path, token: str) -> Iterator[None]:
    if not EXPORT_LEASE.get():
        yield
        return
    previous = os.environ.get("DEV_TEST_LEASE")
    os.environ["DEV_TEST_LEASE"] = json.dumps({"path": str(path.resolve()), "token": token})
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DEV_TEST_LEASE", None)
        else:
            os.environ["DEV_TEST_LEASE"] = previous


def _grant(me: Slot, waiting: list[Slot], capacity: tuple[int, int]) -> int:
    """The workers the first waiting request takes (all that are free, or its ``want``); a later one takes the free workers the first cannot use, at most twice per first request."""
    path, want, minimum = me.path, me.data["want"], me.data["minimum"]
    cap, used = capacity
    free = cap - used
    if not waiting or free < minimum:
        return 0
    first = waiting[0]
    if first.path != path:
        if free >= first.data["minimum"] or policy.reserved(first.data, time.time()):
            return 0
        first.data["backfills"] = first.data.get("backfills", 0) + 1
        first.path.write_text(json.dumps(first.data))
    return free if not want else min(want, free)


@dataclass
class Held:
    """The descriptor that holds a request's lock; it is replaced, and the old one closed, whenever the record is published again."""

    fd: int

    def replace(self, path: Path, record: dict) -> None:
        """Publish ``record`` at ``path`` again and hold its lock instead."""
        replacement = _publish(path, record)
        old, self.fd = self.fd, replacement
        os.close(old)


def _restore_inode(path: Path, record: dict, held: Held) -> None:
    if not _owns_inode(held.fd, path):
        held.replace(path, record)


def _own_records(directory: Path, path: Path, record: dict) -> list[Slot] | None:
    slots = _read(directory, mine=path)
    owned = next((slot for slot in slots if slot.path == path), None)
    if owned is None:
        return None
    if owned.data["token"] != record["token"] or (record["granted"] and owned.granted != record["granted"]):
        raise RuntimeError("testslots: lease record ownership changed")
    return slots


def estimate_of(environment: dict[str, str]) -> float | None:
    """The run's estimated seconds of work (``DEV_TEST_ESTIMATE_S``), or None when it has none."""
    raw = environment.get("DEV_TEST_ESTIMATE_S", "")
    return float(raw) if raw.replace(".", "", 1).isdigit() else None


def _env_estimate() -> float | None:
    return estimate_of(os.environ)


def pool_for(estimate: float | None, ceiling: int) -> int:
    """Workers worth starting for ``estimate`` seconds of work: one more worker pays for its
    start-up while it saves a second of wall time, which holds up to about the square root of the work."""
    if estimate is None:
        return ceiling
    return max(1, min(ceiling, int(math.sqrt(estimate))))


def _agent_from_env() -> dict[str, str]:
    """The workspace agent the runner exported for this process, as plain strings."""
    try:
        data = json.loads(os.environ.get("DEV_TEST_AGENT", ""))
    except ValueError:
        return {}
    return {k: str(data[k]) for k in ("id", "parent", "job") if isinstance(data, dict) and data.get(k)}


def _request(want: int, minimum: int | None, label: str, run_class: str) -> tuple[int, int, dict]:
    """The clamped worker range of a lease request and the record that publishes it."""
    auto = int(want) <= 0
    want = 0 if auto else max(1, int(want))
    minimum = max(1, int(minimum if minimum is not None else 1)) if auto \
        else max(1, min(int(minimum if minimum is not None else 1), want))
    if minimum > (int(os.environ["DEV_TEST_BUDGET"]) if os.environ.get("DEV_TEST_BUDGET", "").isdigit() else base_budget()):
        raise ValueError(f"testslots: this run needs at least {minimum} workers and {budget_words()}; set DEV_TEST_BUDGET to raise it")
    record = {"label": label, "pid": os.getpid(), "want": want, "minimum": minimum, "granted": 0, "since": time.time(),
              "version": policy.VERSION, "class": run_class, "estimate": _env_estimate(), "token": secrets.token_hex(24),
              "agent": _agent_from_env()}
    return want, minimum, record


@dataclass
class Plan:
    """The queue at one moment: requests in grant order, the workers in use and the cap."""

    waiting: list[Slot]
    used: int
    cap: int


def _plan(slots: list[Slot], cap: int, now: float) -> Plan:
    """The grant order of the waiting requests and the capacity in use."""
    queue = [s for s in slots if s.granted == 0]
    waiting = sorted(queue, key=lambda s: s.path) if policy.has_legacy(slots) else policy.order(queue, now)
    return Plan(waiting, sum(s.granted for s in slots), cap)


def _store(slot: Slot) -> None:
    """Write the slot's record into its file, in place so the holder's lock stays on the same file."""
    slot.path.write_text(json.dumps(slot.data))


def _allocate(slots: list[Slot], cap: int, now: float) -> Plan:
    """Grant, in grant order, every request that fits and may be granted by any process; the plan of what still waits.

    The nearest requests are marked ``front`` so they look at their records often and the rest rarely."""
    plan = _plan(slots, cap, now)
    pool = list(plan.waiting)
    while pool and cap - plan.used >= 1:
        for candidate in pool[:SCAN]:
            workers = _grant(candidate, pool, (cap, plan.used))
            if workers and not _alive(candidate.path):
                with contextlib.suppress(OSError):
                    candidate.path.unlink()
                pool.remove(candidate)
                break
            if workers:
                candidate.data["granted"] = workers
                _store(candidate)
                plan.used += workers
                pool.remove(candidate)
                break
        else:
            break
    plan.waiting = pool
    for index, slot in enumerate(pool):
        near = index < cap + 12
        if bool(slot.data.get("front")) != near:
            slot.data["front"] = near
            _store(slot)
    return plan


def _write_allocation(d: Path, plan: Plan) -> None:
    """Record the pass: its cap, the workers in use and the first requests in grant order."""
    document = {"cap": plan.cap, "used": plan.used, "queued": len(plan.waiting),
                "order": [[slot.path.name, slot.data.get("label")] for slot in plan.waiting[:40]]}
    temporary = d / f".{ALLOCATION}.{os.getpid()}.tmp"
    temporary.write_text(json.dumps(document))
    temporary.replace(d / ALLOCATION)


def _pass(d: Path, cap: int) -> Plan:
    """One admission pass under the directory mutex: read the records, grant what fits, record the pass."""
    with _mutex(d):
        plan = _allocate(_read(d), cap, time.time())
        _write_allocation(d, plan)
    return plan


def _stale(d: Path, slack: float) -> bool:
    """Whether the last admission pass is more than ``REFRESH_S`` plus ``slack`` seconds old."""
    try:
        return time.time() - (d / ALLOCATION).stat().st_mtime > REFRESH_S + slack
    except OSError:
        return True


def _saturated(d: Path) -> bool:
    """Whether a recent admission pass left every worker in use, so a request that has just arrived cannot be granted yet."""
    try:
        document = json.loads((d / ALLOCATION).read_text())
        return document["used"] >= document["cap"] and not _stale(d, 0.0)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _own(path: Path) -> tuple[int, bool] | None:
    """The workers granted to the record at ``path`` and whether it is near the front, or None when it cannot be read."""
    try:
        data = json.loads(path.read_text())
        return int(data["granted"]), bool(data.get("front"))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _view(d: Path, name: str) -> tuple[int, list]:
    """The workers in use and the labels of the requests ahead of the one in ``name``, from the last admission pass."""
    try:
        document = json.loads((d / ALLOCATION).read_text())
        order = document["order"]
        place = next((i for i, (found, _) in enumerate(order) if found == name), None)
        return int(document["used"]), [label for _, label in (order if place is None else order[:place])]
    except (OSError, ValueError, KeyError, TypeError):
        return 0, []


@dataclass
class Outcome:
    """What one admission pass found for a waiting request."""

    granted: int = 0
    used: int = 0
    ahead: list = field(default_factory=list)
    republished: bool = False
    skipped: bool = False


def _attempt(path: Path, me: dict, held: Held, cap: int, skip: bool) -> Outcome:
    """Run an admission pass for the record at ``path``; with ``skip`` a pass that another waiter has just run stands."""
    d = path.parent
    with _mutex(d):
        _restore_inode(path, me, held)
        if skip and not _stale(d, 0.0):
            return Outcome(skipped=True)
        slots = _own_records(d, path, me)
        if slots is None:
            held.replace(path, me)
            return Outcome(republished=True)
        plan = _allocate(slots, cap, time.time())
        _write_allocation(d, plan)
        mine = next(slot for slot in slots if slot.path == path)
        place = next((i for i, slot in enumerate(plan.waiting) if slot.path == path), 0)
        return Outcome(mine.granted, plan.used, [slot.data.get("label") for slot in plan.waiting[:place]])


def _release(d: Path, path: Path, fd: int) -> None:
    """Remove the record at ``path``, close its lock and grant what the freed workers allow; never stopped by a cancelled caller."""
    cap = budget()
    with _mutex(d, cancellable=False):
        if sys.platform == "win32":
            os.close(fd)
        with contextlib.suppress(OSError):
            path.unlink()
        if sys.platform != "win32":
            os.close(fd)
        with contextlib.suppress(OSError, ValueError):
            _write_allocation(d, _allocate(_read(d), cap, time.time()))


@dataclass
class Notes:
    """When a waiting request last said it was queued, and what it said."""

    at: float
    state: tuple | None = None

    def due(self, now: float, state: tuple) -> bool:
        """Whether to say it again: after twenty seconds, or on a changed state after one."""
        if now - self.at >= 20 or (state != self.state and now - self.at >= 1):
            self.at, self.state = now, state
            return True
        return False


@contextlib.contextmanager
def lease(want: int, minimum: int | None = None, label: str = "tests", say=lambda m: print(m, file=sys.stderr, flush=True),
          run_class: str = policy.INTERACTIVE) -> Iterator[Lease]:
    """Acquire a bounded CPU lease (shortest estimated work first, aged requests ahead), released on context exit or process death.

    Each pass of admission grants every request that fits, so a waiter looks at its own record, often when it is near the front of
    the queue and rarely when it is not, and runs a pass itself only when it arrives or when none has run for ``REFRESH_S``."""
    _reject_nested()
    want, minimum, me = _request(want, minimum, label, run_class)
    d = slots_dir()
    path = d / f"{time.time_ns()}-{os.getpid()}-{secrets.token_hex(8)}.slot"
    t0 = time.monotonic()
    deadline = t0 + wait_limit()
    with _mutex(d):
        held = Held(_publish(path, me))
    notes = Notes(t0)
    slack = int(me["token"][:4], 16) / 0xFFFF * REFRESH_S
    arriving, used, ahead, granted, cap = True, 0, [], 0, budget()
    try:
        while True:
            CHECK_CANCELLED.get()()
            owned = None if arriving else _own(path)
            if owned is not None and owned[0]:
                granted = me["granted"] = owned[0]
                break
            first = arriving
            if arriving:
                run, owned = not _saturated(d), _own(path)
            else:
                run = owned is None or _stale(d, slack)
            arriving = False
            if run:
                cap = budget()
                found = _attempt(path, me, held, cap, skip=not first and owned is not None)
                arriving = found.republished
                if found.granted:
                    granted = me["granted"] = found.granted
                    break
                if arriving:
                    continue
                if not found.skipped:
                    used, ahead = found.used, found.ahead
                owned = _own(path)
            now = time.monotonic()
            if now > deadline:
                raise WaitExpired(f"testslots: waited {now - t0:.0f}s for {minimum} of {cap} workers ({used} in use); the wait limit is "
                                   f"{wait_limit():.0f} s (DEV_TEST_WAIT_S, default {WAIT_S:.0f}); see `python scripts/testslots.py status`")
            if now - notes.at >= 1:
                used, ahead = _view(d, path.name)
            if notes.due(now, (minimum, want, cap, used, tuple(ahead))):
                say(f"testslots: queued for {minimum}-{want or 'auto'} workers: {used}/{cap} permits in use, "
                    f"{len(ahead)} run(s) ahead{': ' + ', '.join(map(str, ahead)) if ahead else ''}")
            time.sleep(FAST_S if owned is not None and owned[1] else SLOW_S)
        with _lease_environment(path, me["token"]):
            yield Lease(granted, time.monotonic() - t0)
    finally:
        _release(d, path, held.fd)


def _lane_count() -> int:
    raw = os.environ.get("DEV_TEST_HEAVY_LANES")
    if raw and raw.isdigit() and int(raw) > 0:
        return int(raw)
    return max(2, _cores() // 4)


@contextlib.contextmanager
def heavy_lane(label: str = "heavy test", say=lambda m: print(m, file=sys.stderr, flush=True)) -> Iterator[None]:
    """Hold one of the machine-wide HEAVY lanes while a test that spawns a multi-threaded tool runs."""
    d = slots_dir()
    n = _lane_count()
    deadline = time.time() + float(os.environ.get("DEV_TEST_LANE_WAIT_S", "600"))
    fd = -1
    noted = False
    while fd < 0:
        CHECK_CANCELLED.get()()
        for i in range(n):
            f = os.open(d / f"heavy-{i}.lane", os.O_RDWR | os.O_CREAT, 0o600)
            try:
                if not take(f):
                    os.close(f)
                    continue
            except OSError:
                os.close(f)
                raise
            fd = f
            break
        if fd >= 0:
            break
        if time.time() > deadline:
            raise TimeoutError(f"testslots: no heavy lane free for {label} before the wait limit")
        if not noted:
            noted = True
            say(f"testslots: {label} waits for one of {n} heavy lanes")
        time.sleep(0.2)
    try:
        yield
    finally:
        if fd >= 0:
            os.close(fd)                                  # closing releases the lock


def _run_command(argv: list[str], elastic: bool = False) -> int:
    import argparse
    import subprocess
    ap = argparse.ArgumentParser(prog="testslots run")
    ap.add_argument("--want", type=lambda value: 0 if value == "auto" else int(value), default=0, help="workers wanted as a ceiling (0 = auto: every worker the machine can spare)")
    ap.add_argument("--min", dest="minimum", type=int, default=None)
    ap.add_argument("--label", default="command")
    ap.add_argument("--container", action="store_true")
    ap.add_argument("--class", dest="run_class", choices=policy.CLASSES, default=policy.environment_class(os.environ))
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    if not cmd:
        ap.error("give a command after --")
    if elastic:
        import testscope
        import testslots_runner
        try:
            return testslots_runner.run_pytest(cmd, a.want, a.label, {**os.environ, "DEV_TEST_CLASS": a.run_class}, container=a.container)
        except testscope.Refused as refusal:
            print(f"testslots: {refusal}", file=sys.stderr)
            return 4
    with lease(a.want, a.minimum, label=a.label, run_class=a.run_class) as got:
        print(f"testslots: running with {got.workers} worker(s) (waited {got.waited:.0f}s)", file=sys.stderr, flush=True)
        return subprocess.run(cmd, env={**os.environ, "DEV_TEST_WORKERS": str(got.workers)}).returncode


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["pytest"]:
        return _run_command(argv[1:], elastic=True)
    if argv[:1] == ["run"]:
        return _run_command(argv[1:])
    if argv[:1] != ["status"]:
        print(__doc__)
        return 2
    queue_doctor_pass()
    st = status()
    print(testqueue.longest(st["runs"], time.time()))
    try:
        load = f"{os.getloadavg()[0]:.1f}"
    except (OSError, AttributeError):
        load = "?"
    print(f"budget {st['budget']} workers (base {st['base']}; load {load} on {_cores()} cores), {st['in_use']} in use, "
          f"{_lane_count()} heavy lane(s)")
    if st["first"]:
        print(f"  {st['first']}")
    for s in st["running"]:
        print(f"  running  {s['granted']:>2}  pid {s['pid']:<7} {policy.run_class(s):<11} {_who(s)}")
    for s in st["waiting"]:
        print(f"  waiting  {s['minimum']}-{s['want'] or 'auto'}  pid {s['pid']:<7} {policy.run_class(s):<11} {_who(s)}")
    views = testqueue.forecast(st["runs"], st["budget"], time.time())
    for run in st["runs"]:
        print(testqueue.describe(run, views[run["id"]]))
    return 0


def queue_doctor_pass() -> None:
    """Run the queue doctor for this checkout's jobs; skipped where its modules cannot be imported."""
    try:
        import queue_doctor
    except ImportError:
        return
    queue_doctor.check(Path(__file__).resolve().parent.parent, say=lambda text: print(text, file=sys.stderr, flush=True))


def _who(slot: dict) -> str:
    """A status line's owner: the workspace agent when the lease names one, then its label."""
    agent = slot.get("agent")
    if not agent:
        return slot["label"]
    return f"{agent['id']} ({slot['label']})"


if __name__ == "__main__":
    sys.exit(main())
