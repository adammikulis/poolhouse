"""The core of Poolhouse: serve one local model on this machine, with nothing of the pool.

    path = core.resolve("Qwen3.8-27B-UD-Q3_K_XL.gguf")   # a name or a path to an installed GGUF
    core.fit(path, 32768).said()                         # would it load, and if not why
    with core.serve(path, 32768) as server:              # admitted, started, healthy
        server.base_url, server.model_id, server.api_key

No session, token, board, workspace, approval or app is imported or asked for (`tests/test_core.py` reads
``sys.modules`` after importing this module). A binary that changed since its pin (`LocalPolicy`) is re-pinned when its
``BUILD.json``, a code signature or a package manager vouches for it, else replaced by an earlier good build or a fetched
one (`poolhouse.serve.binary_trust`); nothing holds it and nobody is asked. A server starts only inside the grant, opened
in `_start` alone; memory is admitted against the machine's ledger (`poolhouse.serve.ledger`).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from poolhouse import errors, lazy, serverkeys
from poolhouse.client.health import reported_models
from poolhouse.hub import local, room
from poolhouse.serve import admission, grant, ledger, stopping
from poolhouse.serve.backend import Lease, LlamaServerBackend, ServerFailed, ServerInfo, ServerSpec
from poolhouse.serve.binary import require_binary
from poolhouse.serve.children import Children
from poolhouse.serve.leases import lease_file
from poolhouse.serve.policy import LocalPolicy, Policy
from poolhouse.serve.ports import DEFAULT_HOST, free_port, port_is_free
from poolhouse.serve.preflight import Preflight, Report
from poolhouse.serve.weights import scaled_timeout, weight_of

__all__ = ["AdmissionRefused", "Handle", "ModelNotFound", "fit", "resolve", "serve", "stop_all"]

logger = logging.getLogger(__name__)

AdmissionRefused = admission.AdmissionRefused
"""The servers up, and this one, would not fit in the memory this machine allows."""

POLL_S = 0.5
HOLDER = "core"
"""What the core's records say of themselves, so a pool broker in the same process leaves them alone."""

_LOCK = threading.RLock()
_MINE: dict[str, dict[str, Any]] = {}
"""The records of the servers this process started, by port, as the machine's server file holds them."""
_HANDLES: dict[int, Handle] = {}
_CHILDREN = Children()


class ModelNotFound(LookupError, errors.Error):
    """No installed GGUF stands for the name."""


def resolve(model: str | Path) -> Path:
    """The installed GGUF file ``model`` stands for: a path that exists, or a file name (with or without
    ``.gguf``) found in the places models are installed. Raises `ModelNotFound`; nothing is downloaded."""
    found = local.located(model)
    if found is None:
        raise ModelNotFound(f"no installed GGUF stands for {str(model)!r}; `poolhouse-models list` shows what is "
                            f"installed, `poolhouse-models pull` fetches one")
    if found.suffix.lower() != ".gguf":
        raise ModelNotFound(f"{found} is not a .gguf file; the core serves GGUF files and nothing else")
    return found


def _spec(model: str | Path, context: int, port: int, options: dict[str, Any]) -> ServerSpec:
    return ServerSpec(model=str(resolve(model)), port=port, context=int(context), **options)


def fit(model: str | Path, context: int = 0, *, policy: Policy | None = None, **spec: Any) -> Report:
    """Would ``model`` load at ``context`` on this machine: the preflight's report (``.ok``, ``.said()``).
    Starts nothing. ``spec`` is any other `ServerSpec` field (``parallel``, ``cache_type_k``, ...)."""
    policy = policy or LocalPolicy()
    made = _fitted(_spec(model, context, 0, spec))
    binary = require_binary(policy=policy, strict=True)
    return Preflight(made, binary=binary, limit_bytes=room())


def _fitted(spec: ServerSpec) -> ServerSpec:
    """``spec`` with a context of 0 answered with the largest this machine holds (the fit code is loaded for that alone)."""
    if int(spec.context) > 0:
        return spec
    return lazy.load("poolhouse.serve.fitted", "fitted")(spec)[0]


@dataclass
class Handle:
    """A server `serve` started. Closing it, leaving its ``with`` block or ending this process stops the server."""

    base_url: str
    model_id: str
    port: int
    pid: int | None
    info: ServerInfo = field(repr=False, compare=False)
    closed: bool = field(default=False, repr=False, compare=False)

    @property
    def api_key(self) -> str:
        """The bearer key the server was started with, for a caller that speaks HTTP to it directly."""
        return serverkeys.for_url(self.base_url)

    def close(self) -> None:
        """Stop the server and give its memory and port back. Safe to call twice."""
        with _LOCK:
            if self.closed:
                return
            self.closed = True
            _HANDLES.pop(self.port, None)
            stopping.stop_server(_CHILDREN, self.port, self.pid, proc=self.info.process)
            _MINE.pop(str(self.port), None)
            _save()

    def __enter__(self) -> Handle:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def serve(  # noqa: PLR0913 - what to serve, where, under which trust, and how to start it
    model: str | Path, context: int = 0, *, port: int | None = None, policy: Policy | None = None,
    preflight: bool = True, timeout: float | None = None, **spec: Any,
) -> Handle:
    """Admit, start and return a server for ``model`` at ``context`` tokens (0: the largest that fits).

    ``port`` is chosen under the admission lock when not given. ``policy`` is `LocalPolicy` unless a caller passes
    the pool's. ``spec`` is any other `ServerSpec` field (``parallel``, ``reasoning_budget=0`` to switch thinking
    off, ...). ``preflight=False`` skips the load check and is for plumbing tests alone. Raises `ModelNotFound`,
    `AdmissionRefused`, `BinaryNotFound` (also `BinaryTampered`, when a changed binary could be neither vouched for nor replaced), `PreflightFailed` or `ServerFailed`;
    nothing is left running when it raises.
    """
    policy = policy or LocalPolicy()
    made = _fitted(_spec(model, context, port or 0, spec))
    wanted = admission.estimate_bytes(made)
    admitted = _reserved(made, wanted)
    info = None
    try:
        info = _start(LlamaServerBackend(policy=policy), admitted, preflight=preflight,
                      timeout=timeout if timeout is not None else scaled_timeout(weight_of(admitted.model)))
    finally:
        if info is None:
            _forget(admitted.port)
    handle = Handle(info.base_url, "", info.port, info.pid, info)
    with _LOCK:
        _HANDLES[info.port] = handle
        _CHILDREN.hold(info.port, info.process)
        _record(made, info, -(-wanted // admission.MIB))
    ready = False
    try:
        handle.model_id = _model_id(info.base_url, made)
        ready = True
    finally:
        if not ready:
            handle.close()
    return handle


def stop_all() -> list[int]:
    """Stop every server this process started; the pids stopped."""
    with _LOCK:
        pids = [h.pid for h in _HANDLES.values() if h.pid]
        for handle in list(_HANDLES.values()):
            handle.close()
    return pids


def _start(backend: LlamaServerBackend, spec: ServerSpec, *, preflight: bool, timeout: float) -> ServerInfo:
    """The one place this module opens the grant: after admission has written the record the start is on.

    No warm-up request is sent: it would queue on the pool's node, which a core caller has not got, and what it
    would compile is paid by the caller's first request instead."""
    with grant.broker_grant():
        lease = Lease(port=spec.port, owner_pid=os.getpid(), state_file=str(lease_file()), stop_on_exit=True)
        return backend.start(spec, lease=lease, timeout=timeout, preflight=preflight, warmup_request=False)


def _model_id(base_url: str, spec: ServerSpec) -> str:
    try:
        reported = reported_models(base_url)
    except OSError:
        reported = []
    return str(reported[0]) if reported else Path(str(spec.model)).name


def _reserved(spec: ServerSpec, wanted: int) -> ServerSpec:
    """The spec with its port, once its memory is admitted and a pending record names it.

    The look at the machine's ledger and the write of the pending record are one step under the lock the pool's
    broker takes for the same two, so a pool broker and any number of cores see each other's records. A start that
    must wait for memory waits outside the lock, and is refused at once when it could never fit.
    """
    state = lease_file()
    need = -(-wanted // admission.MIB)
    ledger.never_fits(spec.model, need)
    until = time.monotonic() + admission.wait_s()
    while True:
        with ledger.reserving(state):
            records = ledger.recorded(state)
            room_left, said = ledger.fits(records, need)
            if room_left:
                port = spec.port or _free(records)
                if port in records:
                    raise ServerFailed(f"port {port} is recorded as another server's")
                if not port_is_free(port):
                    raise ServerFailed(f"port {port} is held by a process that is not one of ours; refusing to use it")
                _pend(replace(spec, port=port), need)
                break
            if time.monotonic() >= until:
                raise AdmissionRefused(f"{Path(str(spec.model)).name}: {said}; no "
                                       f"memory came free in {admission.wait_s():.0f}s ({admission.ENV_WAIT} sets the wait)")
        time.sleep(POLL_S)
    return replace(spec, port=port)


def _free(records: dict[int, dict]) -> int:
    for _ in range(50):
        port = free_port(DEFAULT_HOST)
        if port not in records:
            return port
    raise ServerFailed("no free port")


def _pend(spec: ServerSpec, need_mb: int) -> None:
    with _LOCK:
        _MINE[str(spec.port)] = ledger.entry(spec, backend=LlamaServerBackend.name, lease="", est_mb=need_mb, holder=HOLDER)
    _save()


def _record(spec: ServerSpec, info: ServerInfo, need_mb: int) -> None:
    pending = _MINE.get(str(info.port)) or {}
    _MINE[str(info.port)] = ledger.entry(spec, backend=LlamaServerBackend.name, lease="",
                                         est_mb=pending.get("est_mb") or need_mb, info=info, holder=HOLDER)
    _save()


def _forget(port: int) -> None:
    with _LOCK:
        _MINE.pop(str(port), None)
    serverkeys.forget(port)
    _save()


def _save() -> None:
    """Write this process's core records over the live records of every other owner, under the file's own lock."""
    with _LOCK:
        ledger.save(lease_file(), _MINE, holder=HOLDER)
