"""The LAN device daemon and its lifecycle configuration."""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import secrets
import socket
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from poolhouse import home, lan as lan_policy, macauth, sentinel
from poolhouse.files import write_text
from poolhouse.fleet.onboard.requests import Devices
from poolhouse.hub import default_roots
from poolhouse.limits import read as limits_read
from poolhouse.lock import only_one
from poolhouse.log import say, warn
from poolhouse.platform import private_file, quitting
from poolhouse.serve import canaries, guarded
from poolhouse.serve.leases import lease_file
from poolhouse.serve.reclaim import watching
from poolhouse.speech import service as speech

from . import (
    automatic_pools,
    autostart,
    autostart_check,
    autostart_guard,
    invite_client,
    invite_routes,
    pool_modes,
    runtime_repair,
    tls,
    updates as updating,
)
from .api import Daemon, make_handler
from .availability import Availability, parse_window
from .conversations import Conversations
from .daemon_control import create as create_control
from .deciding import Deciding
from .device import device_report as default_report, resolve_report, stdlib_device_report
from .discovery import (
    Advertiser,
    Beacon,
    DiscoveryError,
    derive_token,
    key_path,
    load_pool_key,
    memberships,
)
from .environment import Environment
from .files import Fetcher
from .framing import LimitedServer
from .invites import Invitations
from .jobs import JobRunner
from .launch_secret import LaunchSecret
from .measuring import BenchHost, bench_home as bench_home_beside
from .membership_sync import admit, start as start_syncing
from .models import Downloads, Models
from .onboard.joining import PLAIN, Joining
from .pausing import ADOPT_S, adopt_pause, member_pause
from .pool_roster import Pool
from .projects import ProjectRegistry, lan_host, local_candidates
from .runtime_paths import announce_token, configure as configure_runtime_paths, default_root
from .serving import Hosting, Serving
from .settings import Settings
from .ui import UI

DEFAULT_PORT = 8770
LOOPBACK = "127.0.0.1"
ALL_INTERFACES = "0.0.0.0"  # noqa: S104 - what LAN mode means


def bind_address(host: str | None, *, lan: bool, joined: bool) -> str:
    """Return the configured loopback or LAN listening address."""
    if host:
        return host
    return ALL_INTERFACES if (lan or joined) and lan_policy.allowed() else LOOPBACK


def load_or_create_token(
    root: Path, pool_key: bytes | None = None, *, profile: Path | str | None = None,
    rotate: bool = False
) -> str:
    """Return the owner-only machine token for the selected pool profile."""
    p = root / "token"
    if profile is not None:
        identity = str(home.expand(profile).resolve()).encode()
        p = root / "machine-tokens" / hashlib.sha256(identity).hexdigest()
        p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if p.parent.is_symlink() or p.is_symlink():
            raise ValueError("machine token profile paths cannot be symbolic links")
        p.parent.chmod(0o700)
    if pool_key is not None:
        tok = derive_token(pool_key)
    elif not rotate and p.exists() and p.read_text().strip().startswith(macauth.PREFIX):
        private_file(p)
        return p.read_text().strip()
    else:
        tok = macauth.PREFIX + secrets.token_urlsafe(32)
    root.mkdir(parents=True, exist_ok=True)
    if not p.exists() or p.read_text().strip() != tok:
        write_text(p, tok)
    private_file(p)
    return tok


def identity_directory(root):
    """Return the installed device identity directory or an isolated daemon root."""
    return home.state("onboard", "tls") if root == default_root() else root / "tls"


@dataclass(frozen=True)
class DaemonOptions:
    root: Path | str | None = None
    host: str | None = None
    port: int = DEFAULT_PORT
    lan: bool = False
    ui_from_lan: bool = False
    name: str = ""
    announce: bool = True
    pool_key_path: Path | str | None = None
    pool_mode: str | None = None
    device_report: Callable[[], dict[str, Any]] | None = None
    slots: int = 1
    labels: Iterable[str] = ()
    fetch_slots: int = 2
    web: bool = True
    setup_from_lan: bool = False
    initial_setup: bool = False
    busy_hours: Iterable[str] = ()
    free_hours: Iterable[str] = ()
    on_paused: str = "stop"
    bench_home: Path | str | None = None
    track: str | None = None


class DaemonRuntime:
    def __init__(self, options: DaemonOptions):
        self.root = options.root
        self.host = options.host
        self.port = options.port
        self.lan = options.lan
        self.ui_from_lan = options.ui_from_lan
        self.name = options.name
        self.announce = options.announce and lan_policy.allowed()  # no beacon, no multicast, outside the real home or under a test
        self.pool_key_path = options.pool_key_path
        self.pool_mode = options.pool_mode
        self.device_report = options.device_report
        self.slots = options.slots
        self.labels = options.labels
        self.fetch_slots = options.fetch_slots
        self.web = options.web
        self.setup_from_lan = options.setup_from_lan
        self.initial_setup = options.initial_setup
        self.busy_hours = options.busy_hours
        self.free_hours = options.free_hours
        self.on_paused = options.on_paused
        self.bench_home = options.bench_home
        self.track = options.track

    def configure(self) -> None:
        self.root = home.expand(self.root) if self.root else default_root()
        configure_runtime_paths(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.files_root = self.root / "files"
        self.files_root.mkdir(exist_ok=True)
        self.settings_path = self.root / "settings.json"
        self.settings = Settings.load(self.settings_path)
        self.selected = memberships(self.pool_key_path)
        self.effective_mode = pool_modes.validate(
            self.pool_mode or (self.selected[0].mode if self.selected else self.settings.pool_mode or "dev")
        )
        if self.selected and self.selected[0].mode != self.effective_mode:
            raise DiscoveryError(
                "select a pool with the requested mode before starting this daemon"
            )
        pending = (self.web and self.initial_setup and not self.selected
                   and not self.settings.pool_mode and not self.pool_mode)
        saved_prod = not self.selected and self.settings.pool_mode == "prod" and not self.pool_mode
        if self.announce and not pending and not saved_prod:
            self.selected_member = automatic_pools.ensure(
                self.pool_key_path, mode=self.effective_mode
            )
            self.effective_mode = self.selected_member.mode
        say(pool_modes.notice(self.effective_mode))
        self.key = load_pool_key(self.pool_key_path)
        self.token = load_or_create_token(
            self.root,
            self.key,
            profile=self.pool_key_path or os.environ.get("POOLHOUSE_POOL_KEY"),
        )
        self.name = self.name or os.environ.get("POOLHOUSE_MEMBER_NAME") or self.settings.name or socket.gethostname()
        if self.slots == 1 and self.settings.slots != 1:
            self.slots = self.settings.slots
        if not self.labels and self.settings.labels:
            self.labels = self.settings.labels
        if self.on_paused == "stop" and self.settings.on_paused != "stop":
            self.on_paused = self.settings.on_paused
        self.schedule_path = self.root / "availability.json"
        self.schedule = Availability.load(self.schedule_path)
        for spec in self.busy_hours:
            self.schedule.windows.append(parse_window(spec))
        for spec in self.free_hours:
            self.schedule.windows.append(parse_window(spec, busy=False))
        if self.busy_hours or self.free_hours:
            self.schedule.save(self.schedule_path)
        self.taken = adopt_pause(
            self.schedule, member_pause(self.pool_key_path, timeout_s=ADOPT_S)
        )
        if self.taken is not None:
            self.schedule.save(self.schedule_path)
            say(f"  paused with the pool: {self.taken.said()}")
        self.settings.slots = self.slots
        self.settings.labels = [s.strip() for s in self.labels if s and s.strip()]
        self.settings.on_paused = self.on_paused
        if self.track is not None:
            self.wanted = self.track.strip()
            self.settings.track_branch = (
                "" if self.wanted.lower() in ("", "off", "none") else self.wanted
            )
            self.settings.save(self.settings_path)

    def services(self) -> None:
        self.environment = Environment(self.root)
        self.serving = Serving(self.root / "serving.json")
        self.hosting = Hosting(self.root, self.serving)
        self.models = Models(
            default_roots(self.root),
            self.root / "models",
            sources=lambda: self.settings.download_sources,
        )
        self.conversations = Conversations(self.root / "chats")
        self.downloads = Downloads(self.models)
        self.measuring_home = (
            Path(self.bench_home).expanduser()
            if self.bench_home is not None
            else bench_home_beside(self.root)
        )
        self.bench_host: BenchHost | None = None
        self.runner = JobRunner(
            self.root,
            self.files_root,
            slots=self.slots,
            gate=self.may_start,
            environment=self.environment,
        )
        self.bench_host = BenchHost(self.runner, home=self.measuring_home, name=self.name)
        self.fetcher = Fetcher(self.files_root, self.key, slots=self.fetch_slots)
        self.interface = None
        self.setup_token = ""
        if self.web:
            if self.key is None and self.setup_from_lan:
                self.setup_token = secrets.token_urlsafe(9)
            self.interface = UI(
                name=self.name,
                pool_key_path=self.pool_key_path,
                member_port=self.port,
                setup_token=self.setup_token,
            )
            self.interface.launch = LaunchSecret(self.root, self.port)
        self.base_report = self.device_report or partial(default_report, environment=self.environment)
        self.labels = sorted({s.strip() for s in self.labels if s and s.strip()})
        self.heard: list[str] = []
        threading.Thread(target=self.probe_speech, name="speech-probe", daemon=True).start()

    def initialize_listener(self) -> None:
        self.projects = ProjectRegistry(self.root, self.bench_host.machine, local_candidates())
        self.members = Pool(self.pool_key_path)
        self.daemon = Daemon(
                self.runner,
                self.files_root,
                lambda: self.token,
                name=lambda: self.name,
                report=self.report,
                fetcher=self.fetcher,
                ui=self.interface,
                projects=self.projects,
                schedule=self.schedule,
                on_paused=self.on_paused,
                schedule_path=self.schedule_path,
                serving=self.serving,
                models=self.models,
                pool_key_path=self.pool_key_path,
                pool_mode=self.effective_mode,
                tokens=self.every_token,
                devices=lambda: Devices(home.state("onboard", "devices.json")).all(),
                bench=self.bench_host,
                hosting=self.hosting,
                decide=Deciding(self.serving),
                ui_from_lan=self.ui_from_lan or self.setup_from_lan,
                launcher_control=lambda: self.control,
                joining=Joining(lambda: memberships(self.pool_key_path), self.fingerprint,
                                enrol=lambda group, cert, name: admit(self.members, group, cert, name)),
                members=self.members,
            )
        self.handler = make_handler(self.daemon)
        self.listening = bind_address(
            self.host, lan=self.lan or self.setup_from_lan, joined=self.key is not None
        )
        self.widen = threading.Event()
        self.cert: tls.Identity | None = None
        if self.interface is not None:
            self.interface.invitations = Invitations(
                lambda: memberships(self.pool_key_path),
                lambda: (lan_host(self.port), self.fingerprint()),
                enrol=lambda group, cert, name: admit(self.members, group, cert, name),
            )
            self.interface.join_invitation = lambda code: invite_routes.joined(
                self.interface, invite_client.redeem(code, self.name)
            )
        self.httpd = self.listen(self.listening)

    def updates(self) -> None:
        self.nothing_running = updating.quiet(
            jobs=self.background_busy,
            measuring=lambda: bool(self.bench_host.measuring()),
            leases=lambda: bool(self.serving.live()),
        )
        updating.follow_runtime(idle=self.nothing_running, admission=self.update_admission)
        self.control = create_control(self)
        self.tracked = str(getattr(self.settings, "track_branch", "") or "").strip()
        self.tracked_from = str(getattr(self.settings, "track_repo", "") or "") or updating.GIT_URL
        self.checkout = updating.checkout_here() if self.tracked else None
        if self.tracked and self.checkout is not None:
            say(f"  following {self.tracked} on {self.tracked_from} in {self.checkout}")
            updating.track(
                updating.TrackedBranch(self.tracked_from, self.tracked, self.checkout),
                idle=self.nothing_running,
                runtime=updating.UpdateRuntime(admission=self.update_admission),
            )
        else:
            if self.tracked:
                say(
                    f"  cannot follow '{self.tracked}': this copy is not a git checkout. Releases instead."
                )
            updating.watch(
                wanted=lambda: bool(getattr(self.settings, "auto_update", False)),
                idle=self.nothing_running,
                admission=self.update_admission,
            )

    def announcements(self) -> None:
        self.advertiser: Advertiser | None = None
        self.advertisers: dict[str, Advertiser] = {}
        self.announcement_lock = threading.RLock()

    def configure_interface(self) -> None:
        if self.interface is not None:
            self.interface.rename = self.rename
            self.interface.on_join = self.joined_a_pool
            self.interface.runner = self.runner
            self.interface.schedule = self.schedule
            self.interface.settings = self.settings
            self.interface.settings_path = self.settings_path
            self.interface.schedule_path = self.schedule_path
            self.interface.report = self.report
            self.interface.environment = self.environment
            self.interface.serving = self.serving
            self.interface.hosting = self.hosting
            self.interface.models = self.models
            self.interface.conversations = self.conversations
            self.interface.downloads = self.downloads
            self.interface.root = self.root
            self.interface.projects = self.projects
            self.interface.display_url = lan_host(self.port)

    def report_startup(self) -> None:
        if self.announce:
            self.start_announcing()
        say(
            f"poolhouse traind on {('http' if self.listening == LOOPBACK else 'https')}://{self.listening}:{self.port}"
            + ("" if self.listening != LOOPBACK else "  (this machine only; --lan opens it)")
        )
        say(f"  name  {self.name}")
        self.members.ensure_self(self.identity, self.name)
        for member in memberships(self.pool_key_path):
            alone = member.group in self.members.alone()
            say(f"  pool {member.group}" + ("  (no other device enrolled: join, or 'poolhouse-members roster adopt')" if alone else ""))
        say(f"  root  {self.root}")
        say(f"  bench {self.measuring_home}")
        say(f"  slots {self.slots}")
        self.state = self.schedule.public()
        for window in self.schedule.windows:
            say(f"  busy  {window.describe()}" if window.busy else f"  free  {window.describe()}")
        if not self.state["available"]:
            say(f"  NOT TAKING WORK -- {self.state['unavailable_because']}")
        if self.labels:
            say(f"  labels {' '.join(self.labels)}")
        if self.advertiser is not None:
            say(
                f"  members announcing on {self.advertiser.group}:{self.advertiser.port} (key {key_path(self.pool_key_path)})"
            )
            say("  token derived from the pool key -- members compute it themselves")
        elif self.key is None:
            announce_token(self.token)
            say(f"  discovery OFF: no pool key at {key_path(self.pool_key_path)}")
            if self.interface is None:
                say("  run 'poolhouse-members setup' to join one")
        say(f"  device {json.dumps(self.report())}")
        if self.slots > 1:
            say(
                f"  {self.slots} jobs will run at once. Correct for CPU work; on a GPU box this makes every job slower."
            )
        if self.interface is not None:
            self.shown = LOOPBACK if self.listening in (ALL_INTERFACES, "") else self.listening
            say(f"  open   http://{self.shown}:{self.port}/ui/")
            if self.key is None:
                say(
                    "  this machine has not joined a pool yet -- open the address above ON THIS MACHINE to set it up"
                )
                if self.setup_token:
                    say(f"  setup from the LAN with this one-time code: {self.setup_token}")
        say("  EVERY MACHINE IN THIS POOL CAN RUN COMMANDS HERE. Trusted LAN only.", flush=True)

    def run(self) -> None:
        with quitting(self._quit):
            self.loop()

    def loop(self) -> None:
        self.scanner = guarded.start(sentinel.armed(), canaries.lease_file_targets(lease_file()))
        self.idle_s = limits_read().idle_s
        self.reclaiming = contextlib.ExitStack()
        if self.idle_s:
            say(f"  reclaiming a server unused for {self.idle_s:.0f}s")
            self.reclaiming.enter_context(watching(older_than=self.idle_s, say=print))
        self.convergence_stop = threading.Event()
        self.convergence = None
        if self.announce and self.effective_mode == "dev":
            self.convergence = threading.Thread(
                target=automatic_pools.converge,
                args=(self.convergence_stop, self.start_announcing, self.pool_key_path),
                name="automatic-pool-convergence",
                daemon=True,
            )
            self.convergence.start()
        runtime_repair.resume_stored(self.interface, self.root)
        self.syncing = start_syncing(self.members)
        try:
            while True:
                self.httpd.serve_forever()
                if self.control.stopping or not self.widen.is_set():
                    break
                self.widen.clear()
                self.httpd.server_close()
                self.listening = ALL_INTERFACES
                self.httpd = self.listen(ALL_INTERFACES)
                for one in self.advertisers.values():
                    one.beacon.cert = self.served_cert()
                    one.announce()
                say(f"  listening on {ALL_INTERFACES}:{self.port}")
        except KeyboardInterrupt:
            pass
        finally:
            try:
                self.syncing.set()
                self.convergence_stop.set()
                if self.convergence is not None:
                    self.convergence.join(timeout=12.0)
                self.reclaiming.close()
                self.scanner.stop()
                _stop_advertisers(self.advertisers)
                if self.advertiser is not None:
                    self.advertiser.stop()
                self._shutdown_jobs()
            finally:
                try:
                    self.httpd.server_close()
                finally:
                    self.control.close()

    def _shutdown_jobs(self):
        studio = getattr(self.interface, "studio", None)
        if studio is not None:
            studio.close()
        executions = getattr(self.interface, "studio_executions", None)
        try:
            self.runner.shutdown()
        finally:
            if studio is not None:
                studio.store.close()
            if executions is not None:
                executions.close()
            pool_store = getattr(self.interface, "_studio_pool_store", None)
            if pool_store is not None:
                pool_store.close()

    def update_admission(self):
        return only_one(self.root / "runtime-install.lock", wait=False)

    def background_busy(self) -> bool:
        return (bool(self.web and self.initial_setup and not self.settings.setup_done) or bool(self.runner.status()["busy"]) or any(row.state == "getting" for row in self.downloads.active())
                or bool(self.interface and self.interface.setup_jobs and self.interface.setup_jobs.active()))

    def may_start(self) -> tuple[bool, str]:
        """A measurement holds the GPU and blocks new work."""
        if self.bench_host and self.bench_host.measuring():
            return (False, "a benchmark is measuring on this machine")
        return self.schedule.may_start()

    def probe_speech(self) -> None:
        self.heard[:] = speech.working()

    def report(self) -> dict[str, Any]:
        return {
            **self.base_report(),
            "labels": self.labels,
            **self.bench_host.report(),
            **updating.state(),
            "autostart": autostart_check.state_word(),
            "launcher_control": self.control.instance if hasattr(self, "control") else "",
            "speech": list(self.heard),
        }

    def every_token(self) -> set[str]:
        """Every token this machine answers to, one per pool it is in."""
        return {derive_token(m.key) for m in memberships(self.pool_key_path)}

    def fingerprint(self) -> str:
        offered = self.served_cert()
        return hashlib.sha256(base64.b64decode(offered)).hexdigest() if offered else PLAIN

    def identity(self) -> tls.Identity:
        """This daemon's certificate, made on first use."""
        if self.cert is None:
            self.cert = tls.identity(identity_directory(self.root), self.name)
            tls.present(self.cert)
        return self.cert

    def served_cert(self) -> str:
        """The certificate members should pin: this daemon's, if it listens beyond this machine."""
        return "" if self.listening == LOOPBACK else self.identity().beacon

    def listen(self, address: str) -> LimitedServer:
        """A server on ``address``: a machine-only one speaks plain HTTP, any other TLS."""
        if address in (LOOPBACK, "localhost", "::1"):
            return LimitedServer((address, self.port), self.handler)
        return LimitedServer((address, self.port), self.handler,
                             tls=tls.member_context(self.identity(), self.members))

    def refresh(self, b: Beacon) -> None:
        """Bring the beacon's mutable half up to date before it goes on the wire."""
        status = self.runner.status()
        available = self.schedule.public()
        b.busy, b.queued = (status["busy"], status["queued"])
        b.slots, b.free = (status["slots"], status["free"])
        if not available["available"]:
            b.free = 0
        b.device = {
            **self.report(),
            "availability": available,
            "serving": self.serving.public(),
            **self.models.beacon(),
        }

    def start_announcing(self) -> None:
        """Advertise on every pool this machine is in, and stop on any it left."""
        with self.announcement_lock:
            joined = {m.group: m for m in memberships(self.pool_key_path)}
            self.members.ensure_self(self.identity, self.name)
            for group in [g for g in self.advertisers if g not in joined]:
                with contextlib.suppress(Exception):
                    self.advertisers.pop(group).stop()
            for group, member in (joined.items() if self.announce else ()):
                if group in self.advertisers and self.advertisers[group].key != member.key:
                    self.advertisers.pop(group).stop()
                if group in self.advertisers:
                    self.advertisers[group].mode = member.mode
                    self.advertisers[group].joinable = member.mode == "dev" or bool(member.join)
                    continue
                try:
                    offered = self.served_cert()
                except tls.TlsUnavailable as exc:
                    say(f"  discovery OFF for {group}: {exc}")
                    continue
                beacon = Beacon(
                    name=self.name,
                    port=self.port,
                    device=self.report(),
                    cert=offered,
                    slots=self.runner.slots,
                    free=self.runner.slots,
                    machine=self.bench_host.machine,
                )
                try:
                    tell = Advertiser(beacon, member.key, pool=group, refresh=self.refresh)
                    tell.mode = member.mode
                    tell.joinable = member.mode == "dev" or bool(member.join)
                    self.advertisers[group] = tell.start()
                except DiscoveryError as exc:
                    say(f"  discovery OFF for {group}: {exc}")
            self.reconcile_pool(next(iter(joined.values()), None))

    def reconcile_pool(self, first) -> None:
        if first is not None:
            self.key = first.key
            self.fetcher.key = first.key
            self.token = load_or_create_token(
                self.root,
                first.key,
                profile=self.pool_key_path or os.environ.get("POOLHOUSE_POOL_KEY"),
            )
            self.advertiser = self.advertisers.get(first.group)
        else:
            self.token = load_or_create_token(
                self.root, profile=self.pool_key_path or os.environ.get("POOLHOUSE_POOL_KEY"),
                rotate=self.key is not None,
            )
            self.key = None
            self.fetcher.key = None
            self.advertiser = None

    def rename(self, called: str) -> str:
        """Give this machine a new name now, on the page and on the network."""
        called = called.strip()
        if not called or called == self.name:
            return self.name
        self.name = called
        self.bench_host.name = called
        if self.interface is not None:
            self.interface.name = called
        for one in self.advertisers.values():
            one.beacon.name = called
        return called

    def joined_a_pool(self) -> None:
        """Announce, and listen on the network now that members are meant to reach this."""
        selected = memberships(self.pool_key_path)
        self.effective_mode = selected[0].mode if selected else self.settings.pool_mode or "dev"
        self.daemon.pool_mode = self.effective_mode
        self.start_announcing()
        if not selected:
            return
        for member in selected:
            say(f"  joined pool {member.group!r}")
        if not self.host and self.listening == LOOPBACK and lan_policy.allowed():
            self.widen.set()
            self.httpd.shutdown()

    def _quit(self, signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt(f"signal {signum}")


def serve(options: DaemonOptions) -> None:
    runtime = DaemonRuntime(options)
    runtime.configure()
    with autostart_guard.running(runtime.root) as sole:
        if not sole:
            say("another poolhouse-traind already runs from this root; exiting")
            return
        runtime.services()
        runtime.initialize_listener()
        runtime.updates()
        runtime.announcements()
        runtime.configure_interface()
        runtime.report_startup()
        runtime.run()


def _stop_advertisers(advertisers: dict[str, Any]) -> None:
    """Stop every pool's advertiser and empty the mapping."""
    for one in list(advertisers.values()):
        with contextlib.suppress(Exception):
            one.stop()
    advertisers.clear()


def persist(*, slots: int = 1, labels: tuple[str, ...] = (), report: str = "") -> int:
    """Install the daemon at login and report its result."""
    done = autostart.install("login", slots=slots, labels=labels, report=report)
    if done.installed:
        say("installed to start at login")
        if done.path is not None:
            say(f"  {done.path}")
        if done.note:
            say(f"  {done.note}")
        say("  starting now; 'poolhouse-members ls' from another machine should list it")
        return 0
    warn("not installed")
    if done.note:
        warn(f"  {done.note}")
    if done.command:
        warn(f"  run this yourself: {done.command}")
    return 2


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="poolhouse-traind")
    ap.add_argument("--root", default=str(default_root()))
    ap.add_argument(
        "--gym-python",
        default=None,
        metavar="PYTHON",
        help="reuse this existing simulator interpreter and remember it under --root",
    )
    ap.add_argument(
        "--bench-home",
        default=None,
        metavar="DIR",
        help="where this machine's python -m poolhouse.bench keeps its measuring lock "
        "(default: the 'bench' beside --root). "
        "While that lock is held, queued training waits.",
    )
    ap.add_argument(
        "--host",
        default=None,
        help="the address to listen on (default: this machine only, or every "
        "interface when --lan is given or this machine is in a pool)",
    )
    ap.add_argument(
        "--lan",
        action="store_true",
        help="listen on every interface so other machines can reach this one",
    )
    ap.add_argument(
        "--ui-from-lan",
        action="store_true",
        help="let other machines open the web interface. It signs in with the "
        "passphrase over plain HTTP, so only on a network you trust",
    )
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument(
        "--name",
        default="",
        help="how this box identifies itself to members "
        "(default: $POOLHOUSE_MEMBER_NAME, else the hostname)",
    )
    ap.add_argument(
        "--pool-key",
        default=None,
        help="path to the pool key (default: ~/.poolhouse/pool.key)",
    )
    ap.add_argument(
        "--mode",
        choices=("dev", "prod"),
        default=None,
        help="pool admission mode (default: existing pool mode, otherwise dev)",
    )
    ap.add_argument(
        "--no-announce", action="store_true", help="serve, but stay invisible to member discovery"
    )
    ap.add_argument(
        "--busy",
        action="append",
        default=[],
        metavar="WHEN",
        help="when this machine is NOT available for work, e.g. "
        "'mon-fri 09:00-17:00' or '22:00-06:00'. Repeatable. Queued "
        "work waits for the window to close rather than failing.",
    )
    ap.add_argument(
        "--free",
        action="append",
        default=[],
        metavar="WHEN",
        help="carve an exception out of a busy window, e.g. 'mon-fri 12:00-13:00'",
    )
    ap.add_argument(
        "--on-paused",
        choices=("stop", "finish"),
        default="stop",
        help="what happens to work already running when the machine is "
        "paused: stop it (default -- SIGTERM, so a loop that "
        "checkpoints keeps its progress, and it is requeued) or let "
        "it finish",
    )
    ap.add_argument("--no-web", action="store_true", help="serve the API but not the web interface")
    ap.add_argument(
        "--setup-from-lan",
        action="store_true",
        help="on a machine that has not joined a pool, allow first-run "
        "setup from another machine using the one-time code printed "
        "at startup. For a headless box you cannot open a browser on.",
    )
    ap.add_argument(
        "--fetch-slots",
        type=int,
        default=2,
        help="concurrent device-to-device file transfers (default 2). Not job "
        "slots: a transfer is I/O against another box, not compute, "
        "and must not occupy a training slot.",
    )
    ap.add_argument(
        "--label",
        action="append",
        default=[],
        metavar="LABEL",
        help="a role this box declares, e.g. 'prep'. Repeatable. Work can "
        "require or exclude labels; nothing is inferred from them.",
    )
    ap.add_argument(
        "--report",
        action="append",
        default=[],
        metavar="MODULE:CALLABLE",
        help="a richer device probe, e.g. "
        "'poolhouse.fleet.accelerator:report' on a box with a card. "
        "Repeatable; later ones win. Without any, the daemon reports "
        "what the standard library and the accelerator probe can see, plus "
        "whatever is registered under the 'poolhouse.device_report' entry point.",
    )
    ap.add_argument(
        "--slots",
        type=int,
        default=int(os.environ.get("POOLHOUSE_SLOTS") or 1),
        help="how many jobs to run at once (default 1; raise it only on a "
        "box whose work does not contend for one accelerator)",
    )
    ap.add_argument(
        "--track",
        default=None,
        metavar="BRANCH",
        help="follow a branch instead of releases: this machine pulls "
        "BRANCH, installs its committed wheel and "
        "restarts, whenever no job, benchmark or model is running. "
        "Needs the tracked source checkout. Remembered, so "
        "it is asked for once; '--track off' goes back to releases. It "
        "runs code nobody reviewed -- only on a machine you trust to.",
    )
    ap.add_argument(
        "--persist",
        action="store_true",
        help="do not serve; install this daemon (with these --slots, --label "
        "and --report) to start when you log in -- a LaunchAgent on "
        "macOS, a user systemd unit on Linux, a Scheduled Task on "
        "Windows -- and start it now. 'poolhouse-traind --persist' twice "
        "replaces the first install.",
    )
    ap.add_argument("--initial-setup", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--agent-runtime-job", type=runtime_repair.job_id, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.agent_runtime_job:
        return runtime_repair.run(Path(a.root).expanduser(), a.agent_runtime_job)
    if a.persist:
        return persist(slots=a.slots, labels=tuple(a.label), report=a.report[-1] if a.report else "")
    if a.gym_python:
        python = Path(a.gym_python).expanduser().absolute()
        if not python.is_file():
            ap.error("--gym-python must name an existing Python executable")
        path = Path(a.root).expanduser() / "settings.json"
        selected = Settings.load(path)
        selected.gym_python = str(python)
        selected.save(path)
    probes = [resolve_report(spec) for spec in a.report]

    def report() -> dict[str, Any]:
        out = stdlib_device_report()
        for probe in probes:
            with contextlib.suppress(Exception):
                out.update(probe() or {})
        return out

    serve(
        DaemonOptions(
            root=a.root,
            host=a.host,
            port=a.port,
            name=a.name,
            lan=a.lan,
            ui_from_lan=a.ui_from_lan,
            announce=not a.no_announce,
            pool_key_path=a.pool_key,
            pool_mode=a.mode,
            slots=a.slots,
            device_report=report if probes else None,
            labels=a.label or os.environ.get("POOLHOUSE_LABELS", "").split(","),
            fetch_slots=a.fetch_slots,
            web=not a.no_web,
            setup_from_lan=a.setup_from_lan,
            initial_setup=a.initial_setup,
            busy_hours=a.busy,
            free_hours=a.free,
            on_paused=a.on_paused,
            bench_home=a.bench_home,
            track=a.track,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
