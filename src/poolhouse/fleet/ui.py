"""The fleet's web interface: routes, and the guard on first-run setup."""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from poolhouse import jsonl, node_pool
from poolhouse.home import machine_id
from poolhouse.http import Options, Server, ServerError, open_stream
from poolhouse.optional import module

from . import pausing, roster
from .launch_secret import LaunchSecret
from .member_rows import named_apart
from .page import FIT_ONLY
from .remote import NO_SERVICES
from .routes import ASSETS, UI_HEADER, asset_bytes, routes, write, write_json
from .runtime_paths import default_root
from .serving import Hosting, context_settings
from .session import Sessions, SignInHeld, Throttle, parse_cookie
from .settings import apply_preferences
from .updates import current_version

__all__ = ["ASSETS", "UI", "UI_HEADER", "asset_bytes", "routes", "serve_page"]


FALLBACK_ROOM = 24 * 1024 ** 3
"""What the fit view is drawn for when nothing said how much room this machine has."""

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
DISCOVER_CACHE_S = 3.0
"""Long enough that three panels refreshing do not each ask every member's health, short enough that a new member shows."""
POOL_CACHE_S = 2.0
"""How long the node's answer about this device's pool is reused between requests."""


SCRAPE_TIMEOUT_S = 4.0
"""A page that is answering a question has one thread busy and this one waiting; four
seconds is long enough for a loopback JSON body and short enough that a view polling it
does not stack up."""


def _scraped(source: str) -> dict[str, Any]:
    """One ``/metrics`` body, fetched from here rather than from the browser.

    ``http`` and ``https`` only, and the URL is whatever a person typed into the view --
    which is the same trust as the address bar of the browser they typed it in, since this
    route is already behind the UI header and a session. A page that is not there, or is
    not JSON, is a note and not a 500: the view says what it could not read and keeps
    polling, because the usual reason is that the page has not been started yet.
    """
    if not source.startswith(("http://", "https://")):
        return {"error": "a /metrics address starts with http:// or https://"}
    try:
        with open_stream(source, options=Options(timeout=SCRAPE_TIMEOUT_S)) as got:
            raw = got.read(1_000_000)
        return {"serving": True, "metrics": json.loads(raw or b"{}")}
    except (ServerError, OSError, ValueError) as exc:
        return {"serving": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}



class UI:
    """Route handling for ``/ui/*``. Holds the session store and the login throttle."""

    def __init__(self, *, name: str = "", member_port: int = 8770, setup_token: str = "",
                 gated: bool = True) -> None:
        self.runner: Any = None
        self.parts: Any = None
        """The page's components, `page.COMPONENTS` unless a caller leaves some out."""
        self.schedule: Any = None
        self.settings: Any = None
        self.settings_path: Any = None
        self.schedule_path: Any = None
        self.report: Any = None
        self.environment: Any = None
        self.serving: Any = None
        self.models: Any = None
        self.conversations: Any = None
        self.downloads: Any = None
        self.root: Any = None
        self.projects: Any = None
        self.display_url = ""
        """The address other devices reach this one at, shown on its own member card."""
        self.hosting: Any = None
        self.detach: Any = None
        """How a sweep is started: the bench's own `detach` unless a test hands in a fake."""
        self.answers: Any = None
        """``() -> dict``: this process's own answering telemetry, when it answers anything.
        `AskRoutes.metrics` is that callable. None on a daemon that only serves models,
        which is most of them -- the Telemetry view is then pointed at a page that does."""
        self.pool_actions: Any = None
        """`poolhouse.api.pool`, handed in by the command that starts the daemon (this layer may not import it); None on a page that serves alone."""
        self.rename: Any = None
        """``(str) -> str``: renames this machine now. Set by the daemon that owns it."""
        self.name = name
        self.gated = gated
        """False on a page that answers this machine alone (`serve_page`): no founded pool asks it to sign in."""
        self.member_port = member_port
        self.setup_token = setup_token
        self.sessions = Sessions()
        self.launch_throttle = Throttle()
        self.redeem_throttle = Throttle()
        self.launch: LaunchSecret | None = None
        """The secret that lets this machine's own window or owner terminal ask for a ticket."""
        self.audit: Callable[..., None] | None = None
        """``(event, **fields)``: takes the rows `record` would write to the root's audit file."""
        self._members: tuple[float, list[dict[str, Any]]] = (0.0, [])
        self._pool: tuple[float, dict[str, Any] | None] = (0.0, None)
        self.setup_jobs = None
        self._setup_jobs_lock = threading.Lock()

    # -- guards ----------------------------------------------------------
    def host_ok(self, host_header: str) -> bool:
        host = (host_header or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in LOOPBACK or host == "" or not _looks_like_dns(host)

    def may_setup(self, client_ip: str, host_header: str, token: str) -> str:
        """Empty when first-run setup is allowed from here, else the reason it is not."""
        if not self.host_ok(host_header):
            return ("refused: this request arrived addressed to a hostname rather than "
                    "to the machine itself, which is how a web page tries to reach your "
                    "loopback. Open the address bar and type it yourself.")
        if client_ip in LOOPBACK:
            return ""
        if self.setup_token and token and _same(token, self.setup_token):
            return ""
        return ("refused: set this machine up on the machine itself, or over an ssh tunnel to it. "
                "A daemon that has not joined a pool has no "
                "password to check, so the first person to reach this page would own "
                "the box. Start it with --setup-from-lan to allow this deliberately.")

    def pool(self, *, force: bool = False) -> dict[str, Any] | None:
        """The pool status this device's node reports, briefly reused; None when no node answers."""
        age, held = self._pool
        if force or time.time() - age >= POOL_CACHE_S:
            held = node_pool.status()
            self._pool = (time.time(), held)
        return held

    def governance(self) -> dict[str, Any]:
        """What governs this device's pool: ``mode`` ``governed`` once a person founded it, with its ``name``."""
        held = self.pool()
        raw = held.get("governance") if isinstance(held, dict) else None
        return raw if isinstance(raw, dict) else {}

    def founded(self) -> bool:
        """Whether a person founded this device's pool, which is when the page asks for a signed-in session.

        A node that does not answer is not told apart from a founded pool: the page stays closed rather than open.
        """
        if not self.gated:
            return False
        held = self.pool()
        return held is None or self.governance().get("mode") != "unfounded"

    def group(self) -> str | None:
        """The name of the founded pool this device is in; None while the pool is unfounded."""
        name = self.governance().get("name") if self.governance().get("mode") == "governed" else None
        return str(name) if name else None

    def authed(self, cookie_header: str) -> bool:
        return self.sessions.get(parse_cookie(cookie_header)) is not None

    def credentialed(self, cookie_header: str) -> bool:
        """Whether the cookie names a session whose origin the server checked a credential for."""
        session = self.sessions.get(parse_cookie(cookie_header))
        return session is not None and session.credentialed

    def record(self, event: str, **fields: Any) -> None:
        """Append a session event to ``audit`` when a caller set one, else to the private ``ui-audit.jsonl`` under the root
        (nothing when there is neither); ticket and secret values are never passed."""
        if self.audit is not None:
            self.audit(event, **fields)
        elif self.root is not None:
            path = Path(self.root) / "ui-audit.jsonl"
            jsonl.append(path, [{"at": time.time(), "event": event, **fields}], default=str)
            path.chmod(0o600)

    # -- state -----------------------------------------------------------
    def state(self) -> dict[str, Any]:
        founded = self.founded()
        done = bool(self.settings and self.settings.setup_done)
        return {"version": current_version(),
                "experimental_notice_accepted": bool(self.settings and self.settings.experimental_notice_accepted is True),
                "in_pool": founded, "name": self.name, "group": self.group(),
                "needs_sign_in": founded,
                "needs_setup": not done,
                "needs_setup_theme": bool(self.settings and not done and self.settings.appearance_initialized is False)}

    def setup_finished(self) -> dict[str, Any]:
        """Remember that the wizard was finished, so it is not shown again."""
        if self.settings is not None:
            self.settings.setup_done = True
            if self.settings_path is not None:
                self.settings.save(self.settings_path)
        return {"ok": True, **self.state()}

    def refresh(self) -> None:
        """The pool changed: forget what was read of it and of its members."""
        self._members = (0.0, [])
        self._pool = (0.0, None)

    def itself(self) -> dict[str, Any]:
        """This machine as a member row, for a screen counting machines before any
        other one is in the pool."""
        report = self.report() if callable(self.report) else {}
        status = self.runner.status() if self.runner is not None else {}
        row = roster.describe(self.name, f"http://127.0.0.1:{self.member_port}", machine_id(), {**report, **status}, is_self=True)
        return {**row, "display_url": self.display_url}

    def members(self, *, force: bool = False) -> list[dict[str, Any]]:
        """This machine and every other compute device the node lists for the pool, cached briefly. The browser cannot do this itself."""
        age, cached = self._members
        if not force and time.time() - age < DISCOVER_CACHE_S:
            return cached
        group = self.group()
        rows = [self.itself()]
        with contextlib.suppress(*NO_SERVICES):  # the node lists no other compute device
            rows += [row for row in roster.members(timeout_s=2.0, port=self.member_port) if not row["is_self"]]
        for row in rows:
            row["pools"] = [group] if group else []
        found_rows = sorted(named_apart(rows), key=lambda r: (not r["is_self"], r["name"]))
        self._members = (time.time(), found_rows)
        return found_rows

    def set_name(self, name: str) -> str:
        """Set the device name through the active advertiser."""
        self.name = self.rename(name) if callable(self.rename) else name
        if self.settings is not None:
            self.settings.name = self.name
        return self.name

    def apply_prefs(self, req: dict[str, Any]) -> dict[str, Any]:
        """Apply the wizard's preference step. Everything takes effect now."""
        from . import autostart as auto

        out: dict[str, Any] = {"applied": [], "manual": ""}
        settings = self.settings
        if settings is None:
            return out
        if error := apply_preferences(settings, req):
            return {"error": error}

        if "slots" in req and self.runner is not None:
            settings.slots = self.runner.set_slots(1)
            out["applied"].append("one job at a time")
        if "name" in req and str(req["name"]).strip():
            called = str(req["name"]).strip()[:64]
            self.set_name(called)
            out["applied"].append(f"this machine is called {settings.name}")
        if "on_paused" in req and req["on_paused"] in ("stop", "finish"):
            settings.on_paused = req["on_paused"]
        if "on_close" in req and req["on_close"] in ("", "background", "quit"):
            settings.on_close = req["on_close"]
        for key in ("auto_update", "autodownload_models"):
            if key in req:
                setattr(settings, key, bool(req[key]))

        if req.get("work_hours") and self.schedule is not None:
            spec = str(req.get("work_hours_spec") or "mon-fri 09:00-17:00")
            from .availability import parse_window
            try:
                window = parse_window(spec)
                if window not in self.schedule.windows:
                    self.schedule.windows.append(window)
                out["applied"].append(f"not taking work {spec}")
            except ValueError as exc:
                out["error"] = str(exc)
        if self.schedule is not None and self.schedule_path is not None:
            self.schedule.save(self.schedule_path)

        mode = str(req.get("autostart") or "")
        if mode in auto.MODES:
            settings.autostart = mode
            done = auto.install(mode, slots=settings.slots,
                                labels=tuple(settings.labels))
            if done.installed:
                out["applied"].append({"boot": "starts with the computer",
                                       "login": "starts when you log in",
                                       "manual": "starts only when you open it"}[mode])
            else:
                out["manual"] = done.command
                out["manual_why"] = done.note

        if self.settings_path is not None:
            settings.save(self.settings_path)
        return out

    def _hosting(self) -> Any:
        if self.hosting is None:
            self.hosting = Hosting(self.root or default_root(), self.serving)
        return self.hosting

    def start_serving(self, model: Any, *, context=None, current=None) -> Any:
        """Run this model with a checked per-start context and current request proof."""
        settings = context_settings(model.path, context, int(getattr(self.settings, "context", 0) or 0))
        if current is not None and current() is not True:
            raise PermissionError('Sign in again before starting this model.')
        return self._hosting().start(model.path, replace(settings, name=model.name, current=current))

    def stop_serving(self, port: int) -> None:
        """Stop a model server this machine started."""
        self._hosting().stop(port)

    # -- the fleet, and a sweep over it ----------------------------------
    def fleet(self) -> dict[str, Any]:
        """The members as `roster.describe` rows -- serving, room, busy, commit -- and the
        models they hold between them, which is what a sweep can be built from."""
        rows = self.members()
        models = sorted({m for r in rows for m in r["models"]})
        return {"members": rows, "models": models, "self": self.name,
                "group": self.group(), "bench": self.bench_state()}

    def pause_fleet(self, *, resume: bool = False, minutes: float | None = None,
                    reason: str = "") -> dict[str, Any]:
        """Pause or resume every machine this one can see, and what each of them said."""
        answers = pausing.pause_fleet(
            pausing.Fanout(self.root or default_root(), resume=resume, minutes=minutes, reason=reason),
            self.members(force=True))
        self._members = (0.0, [])
        return {"machines": [a.public() for a in answers],
                "reached": sum(1 for a in answers if a.ok), "total": len(answers)}

    def bench_state(self) -> dict[str, Any]:
        """What ``python -m poolhouse.bench status`` says, for the page. The bench's home is
        `poolhouse.bench.home_dir`, the one the command reads."""
        try:
            status = module("poolhouse.bench.progress").status
            measuring = module("poolhouse.bench.underway").measuring
        except ImportError as exc:
            return {"available": False, "text": f"the bench is not installed here: {exc}",
                    "measuring": None}
        return {"available": True, "text": status(), "measuring": measuring()}

    def bench_history(self, limit: int = 20) -> list[dict[str, Any]]:
        from dataclasses import asdict

        history = module("poolhouse.bench.history").history
        return [asdict(e) for e in history(module("poolhouse.bench").home_dir())][::-1][:limit]

    def fit(self, room: int = 0, users: int = 1) -> dict[str, Any]:
        """The measured fit records, worked out for a room of this size.

        Everything the Fit view draws, worked out here: `fit.records()` as it comes off
        disk -- measured at load, never estimated -- each with what `Fit.loaded`, `Fit.cost`,
        `Fit.users` and `Fit.longest` say about a machine with ``room`` bytes and ``users``
        on it. ``room`` defaults to `hub.room()`, which is what a model may actually use
        here rather than the installed RAM.
        """
        try:
            from poolhouse.hub import room as room_here
            from poolhouse.serve import charts as charts_mod, fit as fit_mod
        except ImportError as exc:                        # a device-tier install has no serve
            return {"error": f"this install cannot measure or read fits: {exc}",
                    "records": [], "room": 0, "at_room": 0, "name": self.name}
        here = room_here()
        asked = int(room) or here or FALLBACK_ROOM
        people = max(1, int(users))
        rows = []
        for one in fit_mod.records():
            at = one.at_room(asked)
            rows.append({**one.as_dict(), "loaded": at.loaded(), "free": at.free(),
                         "longest": at.longest(people),
                         "slots": [at.users(c) for c in charts_mod.READ_CONTEXTS],
                         "costs": [at.cost(c) for c in charts_mod.READ_CONTEXTS]})
        return {
            "records": rows,
            "room": here,
            "at_room": asked,
            "users": people,
            "name": self.name,
            "vram_gb": list(charts_mod.COMMON_VRAM_GB),
            "contexts": list(charts_mod.PLOT_CONTEXTS),
            "steps": list(charts_mod.SLIDER_CONTEXTS),
            "ladder": list(charts_mod.READ_CONTEXTS),
        }

    def rates(self) -> dict[str, Any]:
        """Every kept bench run as a point: what it scored, what it cost, and where the
        frontier runs -- ``python -m poolhouse.bench show --rates`` as data rather than as a table.

        Nothing here measures anything. `keep._kept` reads the store the command reads,
        `score.derived` turns one run into the rates it already prints, `score.composed`
        adds each model once more as the command does, and `frontier.pareto` marks the runs
        nothing beats on both axes. The frontier is worked out for *all three* costs and
        sent with each point, so switching the axis on the page costs no round trip -- the
        same reason the fit records carry their two composing numbers rather than answers.
        """
        try:
            home_dir = module("poolhouse.bench").home_dir
            frontier = module("poolhouse.bench.frontier")
            _kept = module("poolhouse.bench.keep")._kept
            score = module("poolhouse.bench.score")
        except ImportError as exc:                       # a device-tier install has no bench
            return {"error": f"the bench is not installed here: {exc}",
                    "runs": [], "axes": {}, "keys": {}, "store": ""}
        AXES, pareto = frontier.AXES, frontier.pareto
        COSTS, NOISE, composed, derived, host_of = (
            score.COSTS, score.NOISE, score.composed, score.derived, score.host_of)
        store = home_dir() / "runs.ladybug"
        kept = _kept(store)
        points = list(kept) + composed(kept)
        # by identity, the way `rates` marks them: two runs can agree on every number and
        # still be two runs
        front = {cost: {id(one) for one in pareto(points, cost=cost)} for cost in AXES}
        rows = []
        for one in points:
            got = derived(one)
            if not got:
                continue
            rows.append({
                "label": str(one.get("label") or ""),
                "host": host_of(one),
                "composed": bool(one.get("composed")),
                "from": str(one.get("from") or ""),
                "front": [cost for cost in AXES if id(one) in front[cost]],
                **dict(got.items()),
            })
        rows.sort(key=lambda r: -r.get("right", 0.0))
        return {"runs": rows, "axes": dict(AXES), "keys": dict(COSTS), "store": str(store),
                "noise": NOISE}

    def telemetry(self, source: str = "") -> dict[str, Any]:
        """What has been answered: this daemon's own record, or another page's ``/metrics``.

        The fleet daemon serves models and does not usually answer questions itself, so
        there is normally nothing local to show -- a host that does answer hangs its
        handler's ``metrics`` on ``ui.answers`` and it appears here. Either way the view
        can be pointed at a page that does answer, by its ``/metrics`` URL, and *this*
        process fetches it: a page on loopback has no reason to allow a cross-origin read,
        and asking the browser to do it would fail silently in exactly the case it is
        wanted. Nothing here measures anything; it reads a number someone else wrote down.
        """
        if source:
            return {"source": source, **_scraped(source)}
        answers = getattr(self, "answers", None)
        if callable(answers):
            try:
                return {"source": "", "serving": True, "metrics": answers()}
            except Exception as exc:  # noqa: BLE001 - a broken counter is not a broken page
                return {"source": "", "serving": True, "error": str(exc)[:200]}
        return {"source": "", "serving": False,
                "note": ("this daemon answers no questions itself, so it has spent nothing. "
                         "Point this at a page that does: its address and /metrics.")}

    def start_sweep(self, argv: list[str]) -> dict[str, Any]:
        """Start ``python -m poolhouse.bench argv`` detached and hand back its log and pid."""
        import shlex

        underway = module("poolhouse.bench.underway")
        log = (self.detach or underway.detach)(argv)
        try:
            held = json.loads(underway.measuring_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            held = {}
        return {"log": str(log), "pid": held.get("pid"), "argv": list(argv),
                "command": "python -m poolhouse.bench " + shlex.join(argv)}

    def stop_sweep(self, pid: int) -> str:
        """Stop the detached measurement, but only the one the page was shown."""
        stop = module("poolhouse.bench.progress").stop
        held = module("poolhouse.bench.underway").measuring()
        if held is None or int(held.get("pid") or 0) != int(pid):
            return "nothing is measuring under that pid"
        return stop()

    def install_update(self) -> dict[str, Any]:
        """Put the newest release in place and start it. Returns what happened."""
        from .updates import apply_if_newer, relaunch

        got = apply_if_newer()
        if got.get("installed"):
            got["restarting"] = relaunch()
        return got

    def login(self, source: str, *, ticket: str = "") -> str | None:
        """A session id for a launch ticket, or None. Raises ``SignInHeld`` when held off."""
        if not ticket:
            return None
        return self._redeem(source, ticket)

    def _redeem(self, source: str, ticket: str) -> str | None:
        held = self.redeem_throttle.blocked_for(source)
        if held:
            raise SignInHeld(f"too many attempts -- wait {held:.0f}s")
        kind = self.sessions.spend_ticket(ticket)
        if not kind:
            self.redeem_throttle.failed(source)
            self.record("session.refused", reason="ticket", source=source)
            return None
        self.redeem_throttle.succeeded(source)
        self.record("session.open", who="ticket", origin="launch-ticket", source=source)
        return self.sessions.open("ticket", "launch-ticket").sid


def _same(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a.strip(), b.strip())


def _looks_like_dns(host: str) -> bool:
    """Whether a Host header names something that had to be resolved."""
    if not host or host.replace(".", "").isdigit():
        return False
    return "." in host and not host.endswith(".local")




# ------------------------------------------------------------------ a page on its own

class _Loopback(BaseHTTPRequestHandler):
    """The handler `serve_page` mounts: `routes` and nothing else."""

    ui: UI
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if self.path in ("/", ""):
            write(self, 302, b"", "application/octet-stream", {"Location": "/ui/fit"})
            return
        if not routes(self.ui, self):
            write_json(self, 404, {"error": "no such route"})

    do_POST = do_GET

    def log_message(self, *args: Any) -> None:   # a served page is not a log line
        return


def serve_page(*, port: int = 0, name: str = "", host: str = "127.0.0.1") -> Any:
    """A loopback-only server over `routes` alone -- what ``poolhouse-serve fit --ui`` puts up.

    The same ``UI`` object and the same route table the daemon mounts, with nothing else
    on it: no jobs, no models, no members, and a page that is never gated, so a machine in a founded pool is not
    asked to sign in to look at its own measurements. It binds loopback, which is what makes that safe: `may_setup` lets this
    machine in and there is no address anyone else could reach.

    Returns the server, not yet serving; the caller decides whether that is a thread or
    this one. ``port=0`` takes whatever is free, and ``server.server_port`` says which.
    """
    import platform as _platform

    ui = UI(name=name or _platform.node() or "this machine", gated=False)
    ui.parts = FIT_ONLY
    handler = type("FitHandler", (_Loopback,), {"ui": ui})
    return Server((host, port), handler)
