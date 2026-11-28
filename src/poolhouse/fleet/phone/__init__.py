"""Linked phones on this computer (docs/phone-api.md): linking by a code the person reads here, the authenticated listener, every
agent as a session, and a phone's own model for the pool.

`PhoneService` is the one the daemon holds. It listens only while a code is open or a phone is linked. Linking, removing and connecting
projects are the person's: the page asks for them with a credentialed session of this computer (`fleet.phone_routes`).
"""

from __future__ import annotations

import contextlib
import os
import socket
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from poolhouse import agent_hooks, home
from poolhouse.log import warn
from poolhouse.phone_mailbox import Phone, Refused, Signed, Store

from .. import tls
from ..onboard.lan import in_tailnet
from . import qr
from .api import Api, project_id
from .link import Linking, link_uri
from .model import PREFIX, PhoneModels
from .server import PORT, Gatekeeper, Listener, local_addresses
from .sessions import Sessions
from .stream import Streams

__all__ = ["PORT", "PREFIX", "PhoneService"]

ONLINE_S = 90.0
"""A phone that sent a request this recently is online."""
WATCH_S = 5.0
RECENT_S = 7 * 24 * 3600


class PhoneService:
    """``ui`` is the daemon's page, whose operations the phone calls; ``actions`` the tools-layer operations the daemon's command
    handed in (`poolhouse.cli.phone_actions`), None without them."""

    def __init__(self, ui: Any, actions: Any = None, *, store: Store | None = None, port: int = PORT,
                 addresses: Callable[[], list[str]] = local_addresses) -> None:
        self.ui, self.actions, self.port, self.addresses = ui, actions, port, addresses
        self.store = store or Store()
        self.schedule: Any = None
        """The scheduled-job records, when a test hands its own; else the ones this build has."""
        self.models = PhoneModels(self.store)
        self.sessions = Sessions(self.store, lambda: self.ui.name, self._board_agents)
        self.streams = Streams(self)
        self.api = Api(self)
        self._seen: dict[str, float] = {}
        self._lock = threading.RLock()
        self._listener: Listener | None = None
        self._linking: Linking | None = None
        self._identity: tls.Identity | None = None
        self._closed = threading.Event()
        self._watching: threading.Thread | None = None
        self._agents: tuple[float, list[dict[str, Any]]] = (float("-inf"), [])

    def _board_agents(self) -> list[dict[str, Any]]:
        if self.actions is None:
            return []
        at, found = self._agents
        if time.monotonic() - at > 10:
            found = self.actions.board_agents([p for p, on in self.store.projects().items() if on][:8])
            self._agents = (time.monotonic(), found)
        return found

    # -- the listener ------------------------------------------------------------
    def identity(self) -> tls.Identity:
        if self._identity is None:
            self.store.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._identity = tls.identity(self.store.root / "tls", f"{socket.gethostname()} phones")
        return self._identity

    def linking(self) -> Linking:
        if self._linking is None:
            self._linking = Linking(self.store, self.identity().fingerprint, linked=self._welcome)
        return self._linking

    def _welcome(self, _phone: Phone) -> None:
        """A newly linked phone sees the person's sessions at once: connect the known projects, without holding up the answer."""
        threading.Thread(target=self.connect_known, daemon=True, name="phone-connect").start()

    def sync(self) -> None:
        """Listen while a code is open or a phone is linked, on the addresses this computer has now; else not at all."""
        with self._lock:
            needed = not self._closed.is_set() and (bool(self.store.phones())
                                                    or (self._linking is not None and self._linking.open() is not None))
            if not needed:
                if self._listener is not None:
                    self._listener.close()
                    self._listener = None
                return
            if self._listener is None:
                identity = self.identity()
                gate = Gatekeeper(self.store, identity.fingerprint)
                self._listener = Listener(gate, self.linking(), self._signed, self._streamed, self._hello)
            context = tls.server_context(self.identity())
            self._listener.listen(self.addresses(), self.port, lambda: context)
            if self._watching is None:
                self._watching = threading.Thread(target=self._watch, daemon=True, name="phone-watch")
                self._watching.start()

    def _watch(self) -> None:
        while not self._closed.wait(WATCH_S):
            try:
                self.sync()
            except (OSError, RuntimeError, ValueError) as why:
                warn(f"phones: the listener could not be kept: {why}")

    def close(self) -> None:
        self._closed.set()
        self.streams.closed.set()
        self.sync()

    @property
    def listening(self) -> dict[str, Any]:
        held = self._listener
        return {"addresses": held.addresses if held else [], "port": held.port if held else 0}

    def endpoints(self) -> list[str]:
        """Where a phone finds this computer: home network first, then Tailscale."""
        held = self.listening
        found = [a for a in held["addresses"] if not a.startswith("127.") and a != "::1"] or held["addresses"]
        ordered = sorted(found, key=lambda a: (in_tailnet(a), ":" in a, a))
        return [f"[{a}]:{held['port']}" if ":" in a else f"{a}:{held['port']}" for a in ordered][:4]

    def _hello(self) -> dict[str, Any]:
        return {"service": "poolhouse-phone", "api": 1, "computer": self.ui.name, "now_ms": int(time.time() * 1000),
                "linking": self.linking().open() is not None}

    def _signed(self, phone: Phone, signed: Signed) -> tuple[int, Any]:
        self._saw(phone)
        return self.api.answer(phone, signed)

    def _streamed(self, phone: Phone) -> Iterator[bytes]:
        self._saw(phone)
        return self.streams.open(phone.id)

    def _saw(self, phone: Phone) -> None:
        now = time.time()
        self._seen[phone.id] = now
        if now - phone.seen_at > 60:
            with contextlib.suppress(Refused):   # removed while this request was answered
                self.store.change(phone.id, seen_at=now)

    def online(self, phone: str) -> bool:
        return time.time() - self._seen.get(phone, 0.0) < ONLINE_S

    # -- the person's side, from this computer -----------------------------------
    def offer(self) -> dict[str, Any]:
        """Show a new link code: its QR code, the code to type and the addresses to type with it."""
        with self._lock:
            made = self.linking().offer()
            self.sync()
        endpoints = self.endpoints()
        if not endpoints:
            self.linking().cancel()
            raise Refused(f"This computer could not listen for phones on port {self.port}.")
        link = link_uri(self.identity().fingerprint, made.code, endpoints, self.ui.name, made.expires)
        self.store.audit("computer", "offer", "")
        return {"code": made.shown, "link": link, "expires": made.expires, "endpoints": endpoints, "qr_svg": qr.svg(link)}

    def cancel_offer(self) -> None:
        self.linking().cancel()
        self.sync()

    def remove(self, phone: str, by: str = "computer") -> bool:
        """Unlink ``phone``: its next request is refused, its streams end and nothing more is asked of it."""
        removed = self.store.unlink(phone, by)
        if removed:
            self.models.drop(phone, "The phone was removed.")
            self._seen.pop(phone, None)
        return removed

    def status(self) -> dict[str, Any]:
        phones = [{"id": p.id, "name": p.name, "words": p.words, "linked_at": p.linked_at, "seen_at": p.seen_at,
                   "online": self.online(p.id), **{k: v for k, v in self.models.state(p.id).items() if k != "enabled"},
                   "pool_use": p.pool_use} for p in sorted(self.store.phones().values(), key=lambda p: p.linked_at)]
        offer = self._linking.open() if self._linking else None
        projects = [{"id": project_id(path), "path": path, "connected": on} for path, on in sorted(self.store.projects().items())]
        return {"phones": phones, "offer": {"code": offer.shown, "expires": offer.expires} if offer else None,
                "listening": self.listening, "projects": projects}

    # -- projects ------------------------------------------------------------------
    def connect(self, project: str, on: bool = True) -> str:
        """Connect ``project`` to linked phones, or disconnect it; the folder as recorded."""
        folder = os.path.realpath(home.expand(project))
        agent_hooks.phone(Path(folder), connect=on)
        self.store.project(folder, on)
        self.store.audit("computer", "connect" if on else "disconnect", folder)
        return folder

    def known_projects(self) -> list[str]:
        """The project folders recent Claude Code sessions ran in: a linked worktree counts as its project, a temporary folder as none."""
        temporary = os.path.realpath(tempfile.gettempdir())
        found = []
        for entry in self.sessions.recent(time.time() - RECENT_S):
            folder = os.path.realpath(entry.split("/.claude/worktrees/")[0]) if entry else ""
            if (folder and folder not in found and Path(folder).is_dir() and not folder.startswith((temporary + os.sep, "/private/tmp/"))):
                found.append(folder)
        return found

    def connect_known(self) -> list[str]:
        """Connect every known project the person did not turn off, as linking a phone does."""
        held, made = self.store.projects(), []
        for folder in self.known_projects():
            if held.get(folder) is False:
                continue
            try:
                made.append(self.connect(folder))
            except (OSError, ValueError) as why:
                warn(f"phones: {folder} was not connected: {why}")
        return made
