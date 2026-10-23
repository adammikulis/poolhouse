"""Model servers on this machine, and reaching the ones on other machines."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from poolhouse import lock
from poolhouse.client.counters import read_speculative
from poolhouse.client.health import is_healthy, reported_models, serving_params
from poolhouse.client.settings import Transport
from poolhouse.files import stat_signature, write_json
from poolhouse.hub.capabilities import capabilities
from poolhouse.hub.probe import machine_memory
from poolhouse.serve import LlamaServerBackend, ServerManager, ServerSpec, free_port
from poolhouse.serve.estimate import DEFAULT_KV, Setup, estimate, verdict
from poolhouse.serve.preflight import read_gguf_header
from poolhouse.serve.process import every_server
from poolhouse.serve.suggest import largest_fit
from poolhouse.units import human_bytes

from . import model_components
from .models import draft_beside

__all__ = [
    "Endpoint",
    "Hosting",
    "NoRoom",
    "ServeSettings",
    "Served",
    "Serving",
    "Started",
    "context_settings",
    "discover_serving",
    "fitted_settings",
    "start_model",
    "stop_model",
]

# Each health path waits this long, and the beacon rebuilds the list every 10s.
PROBE_TIMEOUT = 1.0
CONNECT_TIMEOUT = 0.25
LIVE_CACHE_S = 5.0


@dataclass
class Served:
    """One model server running on this machine."""

    port: int
    models: list[str] = field(default_factory=list)
    slots: int = 1
    started_at: float = field(default_factory=time.time)
    context: int | None = None
    slot_context: int | None = None
    draft: str = ""
    spec_type: str = ""
    draft_status: str = "unknown"
    aliases: list[str] = field(default_factory=list)
    capabilities: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return asdict(self)


class Serving:
    """What this machine has loaded, and whether it is answering."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()
        self._live: tuple[float, list[Served]] = (0.0, [])

    def register(self, port: int, models: list[str] | None = None,
                 slots: int = 1) -> Served:
        served = Served(port=port, models=[_name(m) for m in (models or [])],
                        slots=slots)
        with lock.only_one(self.path.with_suffix(".lock"), timeout=5, announce=lambda text: None):
            current = [s for s in self.all() if s.port != port]
            self._write([*current, served])
        return served

    def unregister(self, port: int) -> None:
        with lock.only_one(self.path.with_suffix(".lock"), timeout=5, announce=lambda text: None):
            self._write([s for s in self.all() if s.port != port])

    def all(self) -> list[Served]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []
        out = []
        for row in raw if isinstance(raw, list) else []:
            try:
                port = row["port"]
                if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
                    continue
                out.append(Served(port=port,
                                  models=list(row.get("models") or []),
                                  slots=int(row.get("slots") or 1),
                                  started_at=float(row.get("started_at") or 0),
                                  context=row.get("context"), slot_context=row.get("slot_context"),
                                  draft=str(row.get("draft") or ""), spec_type=str(row.get("spec_type") or ""),
                                  draft_status=str(row.get("draft_status") or "unknown"),
                                  aliases=list(row.get("aliases") or []),
                                  capabilities=row.get('capabilities') if isinstance(row.get('capabilities'), dict) else {}))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def live(self, *, force: bool = False) -> list[Served]:
        """Only the ones answering, rechecked at most every ``LIVE_CACHE_S``.

        Registration is a claim; a server that died leaves its entry behind, and a
        beacon advertising a model nobody can reach sends work to a dead port.
        """
        age, cached = self._live
        if not force and time.time() - age < LIVE_CACHE_S:
            return cached
        snapshot = self.all()
        running = {row["port"]: row for row in every_server()}
        verified = {served.port: _identity(served, running.get(served.port, {})) for served in snapshot}
        found = [served for served, answering in verified.values() if answering]
        if any(verified[served.port][0] != served for served in snapshot):
            with lock.only_one(self.path.with_suffix(".lock"), timeout=5, announce=lambda text: None):
                old = {served.port: served for served in snapshot}
                current = self.all()
                reconciled = [verified[served.port][0] if served == old.get(served.port) else served for served in current]
                kept = [served for served in reconciled if served is not None]
                if kept != current:
                    self._write(kept)
        self._live = (time.time(), found)
        return found

    def port_for(self, model: str = "") -> int | None:
        for served in self.live():
            if not model or any(model.lower() in m.lower() for m in served.models):
                return served.port
        return None

    def public(self) -> list[dict[str, Any]]:
        return [s.public() for s in self.live()]

    def _write(self, rows: list[Served]) -> None:
        self._live = (0.0, [])
        write_json(self.path, [s.public() for s in rows])


@dataclass(frozen=True, slots=True)
class Started:
    """A model server this process leased, and its entry in the registry."""

    port: int
    lease: Any
    manager: Any
    served: Served | None = None


@dataclass(frozen=True, slots=True)
class ServeSettings:
    """Model identity, context, slots, memory room and escalation preferences."""

    name: str = ''
    context: int = 0
    """Tokens a slot holds; 0 is the largest this machine holds for the model (`largest_fit`)."""
    parallel: int = 1
    room: int = 0
    port: int | None = None
    escalate: bool | None = None
    model_identity: tuple = ()
    current: Callable[[], bool] | None = field(default=None, repr=False, compare=False)



def context_settings(model_path, context, default=0):
    """Validate an explicit per-start context against the selected effective GGUF. No context
    asked gives ``default`` (0, the largest fit, unless the person's saved preference says more)."""
    if context is None:
        return ServeSettings(context=default)
    if type(context) is not int or context <= 0:
        raise ValueError('Context length must be a positive whole number.')
    path = model_components.effective(Path(model_path)).resolve()
    identity = (str(path), stat_signature(path.stat()))
    metadata = read_gguf_header(path)
    architecture = metadata.get('general.architecture')
    maximum = metadata.get(f'{architecture}.context_length') if isinstance(architecture, str) else None
    if type(maximum) is not int or maximum <= 0:
        raise ValueError('This model does not report a verified context limit.')
    if context > maximum:
        raise ValueError(f'This model supports at most {maximum} context tokens.')
    settings = ServeSettings(context=context, model_identity=identity)
    _check_context_model(path, settings)
    return settings


def _check_context_model(path, settings):
    if settings.current is not None and settings.current() is not True:
        raise PermissionError('Sign in again before starting this model.')
    if settings.model_identity and (str(Path(path).resolve()), stat_signature(Path(path).stat())) != settings.model_identity:
        raise ValueError('The selected model changed. Select it again before starting.')

def fitted_settings(model_path: Path | str, settings: ServeSettings) -> ServeSettings:
    """``settings`` with a context of 0 answered: the largest this machine holds for the model
    beside its draft head, for ``settings.parallel`` slots. A context given is kept."""
    if int(settings.context) > 0:
        return settings
    path = model_components.effective(Path(model_path))
    draft = draft_beside(path)
    try:
        got = largest_fit(path, slots=max(1, int(settings.parallel)), draft=draft or "")
    except (OSError, ValueError) as error:
        raise ValueError(f"{path.name}: no context could be fitted ({error}); choose one") from error
    return replace(settings, context=got.context)


def start_model(root: Path | str, model_path: Path | str, *, settings: ServeSettings | None = None,
                manager: Any = None, serving: Serving | None = None) -> Started:
    """Lease the configured model and optionally register its serving endpoint."""
    settings = fitted_settings(model_path, settings or ServeSettings())
    port = free_port() if settings.port is None else settings.port
    parallel = max(1, int(settings.parallel))
    context = int(settings.context)
    original_path = Path(model_path)
    model_path = str(model_components.effective(original_path))
    projector = (model_components.linked(original_path, "vision")
                 or model_components.linked(Path(model_path), "vision"))
    draft = draft_beside(Path(model_path))
    kind = ""
    if draft is not None:
        found = read_gguf_header(draft)
        arch = found.get("general.architecture")
        if found.get(f"{arch}.nextn_predict_layers"):
            kind = "draft-mtp"
    _check_context_model(model_path, settings)
    if manager is None:
        from .llama import ensure_server
        manager = ServerManager(
            backend=LlamaServerBackend(binary=ensure_server(root)))

    sizing = estimate(model_path, Setup(context=int(context), parallel=parallel, draft=draft, mmproj=projector,
                                       draft_cache_type=DEFAULT_KV))
    if verdict(sizing, machine_memory()) == "red":
        raise NoRoom(f"{Path(model_path).name}: not enough free memory for the model, head and context")
    _check_context_model(model_path, settings)
    lease = manager.lease(ServerSpec(model=model_path, port=port, context=int(context) * parallel,
                                     parallel=parallel, draft=str(draft) if draft else None,
                                     mmproj=str(projector) if projector else None,
                                     cache_type_k=DEFAULT_KV, cache_type_v=DEFAULT_KV,
                                     spec_draft_type_k=DEFAULT_KV if draft else "",
                                     spec_draft_type_v=DEFAULT_KV if draft else "",
                                     spec_type=kind, spec_draft_ngl=99 if draft else None),
                          escalate=bool(settings.escalate),
                          reason=f"fleet serving of {Path(model_path).name}")
    retained = False
    try:
        _check_context_model(model_path, settings)
        port = lease.port
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('The model broker did not return a valid serving endpoint.')
        served = serving.register(port, [settings.name or Path(model_path).name], slots=parallel) if serving else None
        started = Started(port=port, lease=lease, manager=manager, served=served)
        retained = True
        return started
    finally:
        if not retained:
            manager.release(lease)


class NoRoom(RuntimeError):
    """The model and its slots do not fit in this machine's room."""


class Hosting:
    """The model servers this process started, by port."""

    def __init__(self, root: Path | str, serving: Serving, *, manager: Any = None,
                 fits: Callable[[], Sequence[Any]] | None = None) -> None:
        self.root = Path(root).expanduser()
        self.serving = serving
        self.manager = manager
        self.fits = fits
        self.leases: dict[int, list[Any]] = {}
        self._lock = threading.RLock()

    def already(self, name: str) -> Served | None:
        """The live server holding ``name``, or None."""
        wanted = _name(name).lower()
        for served in self.serving.live(force=True):
            if any(_name(m).lower() == wanted for m in served.models):
                return served
        return None

    def fits_in(self, name: str, *, context: int, parallel: int, room: int) -> str:
        """"" when the model with ``parallel`` slots fits in ``room`` bytes by its memory
        record, or a line saying what it needs. A model with no record passes."""
        from poolhouse.serve.fit import records

        from .plan import fit_for

        if room <= 0:
            return ""
        fit = fit_for(name, list(self.fits() if self.fits else records()))
        if fit is None:
            return ""
        loaded, each = fit.at_room(room).line(context)
        need = loaded + max(1, int(parallel)) * each
        if need <= room:
            return ""
        return (f"{human_bytes(need)} for {parallel} slot(s) at {context} tokens; "
                f"this machine has {human_bytes(room)}")

    def start(self, model_path: Path | str, settings: ServeSettings | None = None) -> Served:
        """Serve the configured model, admitting its memory and slot requirements."""
        with self._lock:
            settings = fitted_settings(model_path, settings or ServeSettings())
            name = settings.name or Path(model_path).name
            why = self.fits_in(name, context=int(settings.context), parallel=int(settings.parallel), room=int(settings.room))
            if why:
                raise NoRoom(f"{name} does not fit: {why}")
            configured = replace(settings, name=name, escalate=True if settings.escalate is None else settings.escalate)
            started = start_model(self.root, model_path, settings=configured,
                                  manager=self.manager, serving=self.serving)
            self.manager = started.manager
            held = self.leases.setdefault(started.port, [])
            if not any(lease is started.lease for lease in held):
                held.append(started.lease)
            while len(held) > 1:
                self.manager.release(held[0])
                del held[0]
            observed = self.serving.live(force=True) if self.serving is not None else []
            return next((row for row in observed if row.port == started.port), None) or started.served or Served(
                port=started.port, models=[name], slots=max(1, int(settings.parallel)))

    def stop(self, port: int) -> None:
        """Release only this Hosting's retained handles, then remove its registry entry."""
        with self._lock:
            held = self.leases.get(port, [])
            while held and self.manager is not None:
                self.manager.release(held[0])
                del held[0]
            if not held:
                self.leases.pop(port, None)
                self.serving.unregister(port)


def stop_model(started: Started, serving: Serving | None = None) -> None:
    """Release the lease. Takes the port out of the registry when given one."""
    if started.manager is not None:
        started.manager.release(started.lease)
    if serving is not None:
        serving.unregister(started.port)


def _identity(served: Served, process: dict) -> tuple[Served | None, bool]:
    try:
        with socket.create_connection(("127.0.0.1", served.port), timeout=CONNECT_TIMEOUT):
            pass
    except OSError:
        return None, False
    def local_only(url: str) -> str:
        target = urlsplit(url)
        if target.scheme != "http" or target.hostname != "127.0.0.1" or target.port != served.port:
            raise ValueError("model metadata must stay on its registered local endpoint")
        return url

    try:
        names = reported_models(f"http://127.0.0.1:{served.port}", timeout=PROBE_TIMEOUT, guard=local_only)
    except (OSError, ValueError, HTTPException):
        return served, False
    if not names:
        return served, False
    base = f"http://127.0.0.1:{served.port}"
    try:
        params = serving_params(base, timeout=PROBE_TIMEOUT, guard=local_only)
        counts = read_speculative(base, timeout=PROBE_TIMEOUT, guard=local_only)
    except (OSError, ValueError, HTTPException):
        params, counts = None, None
    slots = params.total_slots if params and type(params.total_slots) is int and params.total_slots > 0 else served.slots
    slot_context = params.n_ctx if params and type(params.n_ctx) is int and params.n_ctx > 0 else None
    context = slot_context * slots if slot_context else None
    spec_type = str(process.get("spec_type") or "")
    draft = str(process.get("draft") or "")
    state = "active" if counts and counts.drafted > 0 else "configured" if spec_type else "unknown"
    model = next((Path(name) for name in names if Path(name).is_absolute()), None)
    if model is None and params and params.model and Path(params.model).is_absolute():
        model = Path(params.model)
    observed = capabilities(model, embedding=process.get('embedding'))
    listed = [_name(name) for name in names]
    if params and params.model and _name(params.model) not in listed:
        listed.append(_name(params.model))  # a reported id is only a label (--alias): the file it loaded is a name too
    return Served(served.port, listed, slots, served.started_at,
                  context, slot_context, Path(draft).name if draft else "", spec_type, state, names, observed), True


def answers(port: int, *, timeout: float = PROBE_TIMEOUT) -> bool:
    """Whether a model server on ``port`` of this machine is up."""
    try:
        with socket.create_connection(("127.0.0.1", port),
                                      timeout=min(timeout, CONNECT_TIMEOUT)):
            pass
    except OSError:
        return False
    return is_healthy(f"http://127.0.0.1:{port}", timeout=timeout)


def _name(model: str) -> str:
    return Path(str(model)).name


@dataclass(frozen=True, slots=True)
class Endpoint:
    """A model server on some machine, reachable through that machine's daemon."""

    member: str
    base_url: str
    token: str
    models: tuple[str, ...] = ()
    slots: int = 1
    free: int = 1

    def client_kwargs(self) -> dict[str, Any]:
        """Straight into ``poolhouse.client.Client(**endpoint.client_kwargs())``."""
        return {"base_url": f"{self.base_url}/infer",
                "transport": Transport(api_key=self.token)}


def discover_serving(key: bytes, *, model: str = "", timeout_s: float = 2.0,
                     group: str | None = None, port: int | None = None
                     ) -> list[Endpoint]:
    """Every machine on the network with a model loaded."""
    from .discovery import derive_token, discover

    token = derive_token(key)
    out = []
    for beacon in discover(key, timeout_s=timeout_s, group=group, port=port):
        for served in (beacon.device.get("serving") or []):
            models = tuple(served.get("models") or ())
            if model and not any(model.lower() in m.lower() for m in models):
                continue
            out.append(Endpoint(member=beacon.name, base_url=beacon.base_url,
                                token=token, models=models,
                                slots=int(served.get("slots") or 1),
                                free=beacon.free))
    return out
