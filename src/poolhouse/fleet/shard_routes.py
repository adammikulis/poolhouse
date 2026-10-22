"""The daemon's routes for test shards, for a device that took part in the pool.

``GET /shards/v1/caps`` says whether this device takes tests. The tree arrives by ``PUT /files/shards/ID/tree.tgz``; ``POST
/shards/v1/start`` names a tier, test files and a time limit and runs the executor on it; ``GET /shards/v1/ID?since=N`` is its
state, its output from byte N and, once it has ended, its result; ``POST /shards/v1/ID/cancel`` stops it. The command is built here,
never taken from the request, and a shard belongs to the device that started it.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

from . import shard_exec, shard_host, shard_run, shard_spec
from .discovery import memberships
from .jobs import DaemonError
from .shard_tree import MOST_PACKED

__all__ = ["answer", "who"]

PREFIX = "/shards/v1/"
JOB = "shard-"
BOOT = "import sys; from poolhouse.fleet.shard_exec import run; raise SystemExit(run(sys.argv[1]))"
TREE, SPEC = "tree.tgz", "spec.json"
MOST_KEPT = 8
KEPT_S = 24 * 3600.0
MOST_RESULT = 900 << 10
MOST_CHUNK = 1 << 20
AGENT = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
ACTIVE = ("queued", "preparing", "launching", "running")
STATES = {"queued": "queued", "preparing": "queued", "launching": "queued", "running": "running", "stopped": "cancelled"}


def who(handler: Any) -> str:
    """The device that asked (its fingerprint) and, when it named one, the agent behind the request."""
    agent = handler.headers.get("X-Poolhouse-Agent", "")
    return "/".join(p for p in (handler._member or "local", agent if AGENT.match(agent) else "") if p)


def mode_of(daemon: Any) -> str:
    """The pool mode this daemon is in."""
    joined = memberships(daemon.pool_key_path)
    return daemon.pool_mode or (joined[0].mode if joined else "dev")


def folder_of(daemon: Any, shard: str) -> Path:
    """Where the shard ``shard`` keeps its tree, its spec and its result."""
    return daemon.files_root / "shards" / shard


def prune(daemon: Any) -> None:
    """Drop shard folders no job still needs: the oldest beyond `MOST_KEPT`, and any older than a day."""
    base = daemon.files_root / "shards"
    live = {j.id[len(JOB):] for j in daemon.runner.jobs.values() if j.id.startswith(JOB) and j.state in ACTIVE}
    folders = sorted((p for p in base.glob("*") if p.is_dir() and p.name not in live), key=lambda p: p.stat().st_mtime, reverse=True)
    for index, folder in enumerate(folders):
        if index >= MOST_KEPT or time.time() - folder.stat().st_mtime > KEPT_S:
            shutil.rmtree(folder, ignore_errors=True)


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


def start(handler: Any, daemon: Any, body: bytes) -> bool:
    """Run the executor on a tree this device holds for the sender; answers 201 with the job."""
    try:
        header = shard_spec.check_header(json.loads(body or b"{}"))
    except (shard_spec.Refused, ValueError, TypeError) as exc:
        return refuse(handler, 400, str(exc))
    if why := shard_host.refusal(mode_of(daemon), str(daemon.files_root.parent)):
        return refuse(handler, 403, f"this device takes no tests: {why}")
    folder = folder_of(daemon, header["id"])
    tree = folder / TREE
    if not tree.is_file() or tree.stat().st_size > MOST_PACKED:
        return refuse(handler, 400, "the tree has not been uploaded to this device (PUT /files/shards/ID/tree.tgz first)")
    if hashlib.sha256(tree.read_bytes()).hexdigest() != header["tree_sha256"]:
        return refuse(handler, 400, "the uploaded tree does not match its digest")
    python, why = shard_host.resolve()
    if not python:
        return refuse(handler, 503, why)
    prune(daemon)
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
    result = read_result(folder_of(daemon, shard))
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


def answer(handler: Any, daemon: Any, method: str, path: str, body: bytes | None) -> bool:
    """Handle a shard route for the caller `handler._guard` accepted; False for any other path."""
    route, _, query = path.partition("?")
    if not route.startswith(PREFIX):
        return False
    name = route[len(PREFIX):]
    if method == "GET" and name == "caps":
        handler._send(200, shard_host.capability(mode_of(daemon), str(daemon.files_root.parent), daemon.name() if callable(daemon.name) else daemon.name,
                                                 daemon.runner.status()))
        return True
    if not handler._member:
        return refuse(handler, 403, "only a device in the pool sends tests")
    if method == "POST" and name == "start":
        return start(handler, daemon, body or b"")
    shard, _, verb = name.partition("/")
    if not shard_spec.SHARD_ID.match(shard):
        return refuse(handler, 404, "no such shard route")
    if method == "GET" and not verb:
        since = urllib.parse.parse_qs(query).get("since", ["0"])[0]
        code, said = state_of(daemon, handler, shard, int(since) if since.isdigit() and len(since) < 12 else 0)
        handler._send(code, said)
        return True
    if method == "POST" and verb == "cancel":
        return cancel(handler, daemon, shard)
    return refuse(handler, 404, "no such shard route")
