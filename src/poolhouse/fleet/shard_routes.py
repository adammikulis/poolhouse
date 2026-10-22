"""The daemon's routes for test shards, for a device that took part in the pool.

``GET /shards/v1/caps`` says whether this device takes tests from the pool the request was signed in. ``PUT /shards/v1/ID/tree``
brings the packed tree in order; ``POST /shards/v1/start`` names a tier, test files and a time limit and runs the executor on it;
``GET /shards/v1/ID?since=N`` is its state, its output from byte N and, once it has ended, its result; ``POST /shards/v1/ID/cancel``
stops it. The command is built here, never taken from the request. A shard belongs to the device that first sent to its id: no other
device writes to it, starts it, reads it or stops it, whether through these routes, the files of the daemon or its jobs.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

from . import shard_exec, shard_host, shard_run, shard_spec, shard_store
from .discovery import memberships
from .framing import Malformed, content_range
from .jobs import DaemonError
from .shard_tree import MOST_PACKED

__all__ = ["answer", "who"]

PREFIX = "/shards/v1/"
JOB = "shard-"
BOOT = "import sys; from poolhouse.fleet.shard_exec import run; raise SystemExit(run(sys.argv[1]))"
SPEC = "spec.json"
MOST_RESULT = 900 << 10
MOST_CHUNK = 1 << 20
AGENT = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
JOB_ROUTE = re.compile(rf"^/jobs/{JOB}(?P<id>[0-9a-f]{{32}})(?:/log|/stop|/metrics)?$")
ACTIVE = ("queued", "preparing", "launching", "running")
STATES = {"queued": "queued", "preparing": "queued", "launching": "queued", "running": "running", "stopped": "cancelled"}


def who(handler: Any) -> str:
    """The device that asked (its fingerprint) and, when it named one, the agent behind the request."""
    agent = handler.headers.get("X-Poolhouse-Agent", "")
    return "/".join(p for p in (handler._member or "local", agent if AGENT.match(agent) else "") if p)


def mode_of(handler: Any, daemon: Any) -> str:
    """The mode of the pool the request was signed in; empty when it was signed in none of this device's pools."""
    return next((m.mode for m in memberships(daemon.pool_key_path) if m.group == handler._group), "")


def jobs_of(daemon: Any) -> tuple[set[str], set[str]]:
    """The shards with a job queued or running, and those with any job."""
    held = {j.id[len(JOB):]: j.state in ACTIVE for j in daemon.runner.jobs.values() if j.id.startswith(JOB)}
    return {s for s, live in held.items() if live}, set(held)


def tail_of(log: Path, most: int = 2000) -> str:
    """The last ``most`` bytes of ``log``, lossy and trimmed."""
    try:
        with log.open("rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - most))
            return fh.read().decode("utf-8", "replace").strip()
    except OSError:
        return ""


def refuse(handler: Any, code: int, why: str) -> bool:
    """Send ``why`` as the error of a request; True, because the route is answered."""
    handler._send(code, {"error": why})
    return True


def read_result(folder: Path) -> dict | None:
    """The executor's result in ``folder``, when it wrote one that a reply can carry."""
    path = folder / shard_run.RESULT
    try:
        if not path.is_file() or path.stat().st_size > MOST_RESULT:
            return None
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def upload(handler: Any, daemon: Any, shard: str, body: bytes) -> bool:
    """Take the next bytes of the shard's tree from the device that owns the id (the first to send to it)."""
    try:
        offset, total = content_range(handler.headers.get("Content-Range", ""), len(body))
    except Malformed as bad:
        return refuse(handler, bad.status, bad.message)
    folder = shard_store.folder_of(daemon.files_root, shard)
    if why := shard_host.refusal(mode_of(handler, daemon), str(daemon.files_root.parent)):
        return refuse(handler, 403, f"this device takes no tests: {why}")
    if not folder.is_dir() and (why := shard_store.room(daemon.files_root, handler._member, *jobs_of(daemon))):
        return refuse(handler, 429, why)
    if not shard_store.claim(folder, handler._member):
        return refuse(handler, 403, "that shard belongs to another device")
    status, said = shard_store.append(folder, offset, total or 0, body)
    if status != 200:
        return refuse(handler, status, said)
    handler._send(200, {"ok": True, "complete": (folder / shard_store.TREE).exists()})
    return True


def start(handler: Any, daemon: Any, body: bytes) -> bool:
    """Run the executor on a tree this device holds for the sender; answers 201 with the job."""
    try:
        header = shard_spec.check_header(json.loads(body or b"{}"))
    except (shard_spec.Refused, ValueError, TypeError) as exc:
        return refuse(handler, 400, str(exc))
    if why := shard_host.refusal(mode_of(handler, daemon), str(daemon.files_root.parent)):
        return refuse(handler, 403, f"this device takes no tests: {why}")
    folder = shard_store.folder_of(daemon.files_root, header["id"])
    tree = folder / shard_store.TREE
    if shard_store.owner(folder) != handler._member:
        return refuse(handler, 403, "that shard belongs to another device")
    if not tree.is_file() or tree.stat().st_size > MOST_PACKED:
        return refuse(handler, 400, "the tree has not been sent to this device (PUT /shards/v1/ID/tree first)")
    if hashlib.sha256(tree.read_bytes()).hexdigest() != header["tree_sha256"]:
        return refuse(handler, 400, "the uploaded tree does not match its digest")
    python, why = shard_host.resolve()
    if not python:
        return refuse(handler, 503, why)
    (folder / SPEC).write_text(json.dumps(header), encoding="utf-8")
    env = {shard_host.PYTHON_ENV: python, shard_run.STREAM_ENV: "1"}
    try:
        job = daemon.runner.label(daemon.runner.submit(
            f"tests {header['tier']}", [sys.executable, "-c", BOOT, str(folder)], str(daemon.files_root), env,
            job_id=JOB + header["id"]), who(handler))
    except DaemonError as exc:
        return refuse(handler, 409, str(exc))
    handler._send(201, {"id": header["id"], "job": job.public()})
    return True


def state_of(daemon: Any, handler: Any, shard: str, since: int) -> tuple[int, dict]:
    """The reply to a status request: the state, the output from byte ``since``, and the result once there is one."""
    job = daemon.runner.jobs.get(JOB + shard)
    if job is None or job.by.split("/")[0] != (handler._member or "local"):
        return 404, {"error": "no such shard"}
    log = daemon.runner.log_path(job.id)
    chunk = b""
    if log.is_file():
        with log.open("rb") as fh:
            fh.seek(since)
            chunk = fh.read(MOST_CHUNK)
    out = {"output": chunk.decode("utf-8", "replace"), "next": since + len(chunk), "job": job.public()}
    result = read_result(shard_store.folder_of(daemon.files_root, shard))
    if result is not None and job.state != "stopped":
        failed = result.get("exit") == shard_exec.REFUSED
        return 200, {**out, "state": "failed" if failed else "done", **({"error": result.get("output_tail", "")} if failed else {"result": result})}
    if job.state in STATES:
        return 200, {**out, "state": STATES[job.state]}
    return 200, {**out, "state": "failed", "error": f"the run ended with status {job.returncode} and left no result: {tail_of(log)}"}


def cancel(handler: Any, daemon: Any, shard: str) -> bool:
    """Stop the shard and what it started."""
    code, said = state_of(daemon, handler, shard, 0)
    if code != 200:
        handler._send(code, said)
        return True
    try:
        job = daemon.runner.stop(JOB + shard)
    except DaemonError as exc:
        return refuse(handler, 409, str(exc))
    handler._send(200, {"id": shard, "state": STATES.get(job.state, job.state)})
    return True


def not_mine(handler: Any, daemon: Any, shard: str) -> bool:
    """Whether the job of ``shard`` belongs to a device other than the one asking; this machine's own user owns every one."""
    job = daemon.runner.jobs.get(JOB + shard)
    return bool(handler._member) and job is not None and job.by.split("/")[0] != handler._member


def answer(handler: Any, daemon: Any, method: str, path: str, body: bytes | None) -> bool:
    """Handle a shard route for the caller `handler._guard` accepted; False for any other path."""
    route, _, query = path.partition("?")
    if route == "/files/shards" or route.startswith("/files/shards/"):
        return refuse(handler, 403, "a shard's files go through /shards/v1/ID/tree and come back in its result")
    if (job := JOB_ROUTE.fullmatch(route)) and not_mine(handler, daemon, job["id"]):
        return refuse(handler, 404, "unknown job")
    if not route.startswith(PREFIX):
        return False
    name = route[len(PREFIX):]
    if method == "GET" and name == "caps":
        handler._send(200, shard_host.capability(mode_of(handler, daemon), str(daemon.files_root.parent),
                                                 daemon.name() if callable(daemon.name) else daemon.name, daemon.runner.status()))
        return True
    if not handler._member:
        return refuse(handler, 403, "only a device in the pool sends tests")
    if method == "POST" and name == "start":
        return start(handler, daemon, body or b"")
    shard, _, verb = name.partition("/")
    if not shard_spec.SHARD_ID.match(shard):
        return refuse(handler, 404, "no such shard route")
    if method == "PUT" and verb == "tree":
        return upload(handler, daemon, shard, body or b"")
    if method == "GET" and not verb:
        since = urllib.parse.parse_qs(query).get("since", ["0"])[0]
        code, said = state_of(daemon, handler, shard, int(since) if since.isdigit() and len(since) < 12 else 0)
        handler._send(code, said)
        return True
    if method == "POST" and verb == "cancel":
        return cancel(handler, daemon, shard)
    return refuse(handler, 404, "no such shard route")
