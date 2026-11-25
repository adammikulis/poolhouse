"""The capability report: what this computer can enforce for a coding agent, guarantee by guarantee, and what stays visible.

`capability()` replaces the unconditional refusal `harnessing.require_isolated_execution`. It reads the machine (the backend, the
operating system, the project's place) and runs nothing: no installer, no elevation, no prompt; the proof is the launch
(docs/design-isolated-launcher.md, 5 and 9). A guarantee is enforced, watched (only G10 and G11 may be) or unenforceable; a launch needs
every guarantee, and one that is not carries its own sentence. Each entry says how it is known: VERIFIED where this document's evidence ran on
that platform, PREDICTED where it is derived from documentation and code and no device ran it.
"""

from __future__ import annotations

import contextlib
import os
import platform
import shutil
import socket
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from poolhouse.isolation.refusals import IsolationRefused, sentence
from poolhouse.isolation.spec import Spec
from poolhouse.sandbox import bubblewrap, seatbelt
from poolhouse.sandbox.backend import Backend
from poolhouse.sandbox.confinement import confined
from poolhouse.sandbox.run import backend as platform_backend

__all__ = ["GUARANTEES", "WATCHABLE", "Entry", "Report", "Seen", "capability", "cause_of", "drop_cache", "lan_address", "require_capable"]

GUARANTEES = {
    "G1": "Nothing is readable outside the stage, the agent home, the harness's install tree, the interpreter, the system toolchain and the grants.",
    "G2": "Nothing is writable outside the stage and the agent home.",
    "G3": "A control name cannot be created or changed in the stage.",
    "G4": "The only network reachable is the model bridge, and the egress proxy for granted hosts.",
    "G5": "No unix, abstract or other socket is reachable except the session bridge.",
    "G6": "No process outside the session can be signalled or traced, or have its arguments, environment or memory read.",
    "G7": "The person's terminal and desktop cannot be driven.",
    "G8": "No credential store is reachable.",
    "G9": "No GPU, camera, microphone or input device.",
    "G10": "The session's processes end with the session, and nothing is scheduled to run after it.",
    "G11": "Process count, memory, descriptors, CPU and disk are bounded.",
    "G12": "No new user namespace or nested sandbox can be entered to regain capabilities.",
    "G13": "No hardware or machine identifier is readable.",
}
"""The thirteen guarantees of 5.1, by id."""
WATCHABLE = ("G10", "G11")
"""The only guarantees the parent may enforce by watching, when the watcher ends the session loudly on a limit derived from the machine."""
RESOURCES = {
    "darwin": {"processes": "enforced", "cpu": "enforced", "file size": "enforced", "descriptors": "enforced", "memory": "watched",
               "disk": "watched"},
    "linux": {"processes": "watched", "cpu": "enforced", "file size": "enforced", "descriptors": "enforced", "memory": "watched",
              "disk": "watched"},
}
"""G11 resource by resource (5.5): the process count is the kernel's on macOS (`RLIMIT_NPROC`), watched on Linux until the probe shows it
per namespace, and memory and disk are watched everywhere."""
ENFORCED_BY = {
    "darwin": ("Seatbelt", "VERIFIED"),
    "linux": ("bubblewrap namespaces", "PREDICTED"),
}
HOW = {
    "G1": "file reads are denied by default and granted by path", "G2": "file writes are denied by default and granted by path",
    "G3": "the control names are frozen in the stage, in every case and through every ancestor",
    "G4": "the network is denied except the model bridge's port", "G5": "no socket is reachable but the session bridge's",
    "G6": "signals, tracing and reads of other processes are refused", "G7": "no terminal device and no desktop service is reachable",
    "G8": "no home folder and no credential service is reachable", "G9": "no GPU or input device is reachable",
    "G10": "the parent ends the session by walking its processes",
    "G11": "limits are set by the kernel where it counts them and watched by the parent where it does not",
    "G12": "a nested sandbox or user namespace is refused", "G13": "the hardware identifier channels are refused",
}
FLAVOUR = {"G10": {"linux": "the pid namespace ends with the session"}}


@dataclass(frozen=True, slots=True)
class Entry:
    """One guarantee on this computer: its state, how it is held, why not and what would change it, and how that is known."""

    id: str
    state: str
    how: str
    reason: str = ""
    remedy: str = ""
    evidence: str = ""
    resources: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Seen:
    """An exposure: a fact a confined process can still learn here, named and pinned by the proof (5.7)."""

    id: str
    platform: str
    what: str
    visible: bool = True


@dataclass(frozen=True, slots=True)
class Report:
    """The typed report: one entry per guarantee and per exposure, what it was computed for, and the sentences of the launch itself."""

    platform: str
    backend: str
    backend_version: str
    os_build: str
    guarantees: tuple[Entry, ...]
    exposures: tuple[Seen, ...]
    launch: tuple[str, ...] = ()
    unchecked: tuple[str, ...] = ()
    """The attempts of the proof that this computer cannot make, so that the proof shows nothing of them: P08 on a computer with no address on a
    network (nothing there to listen on). It is never reported as passed."""

    @property
    def can_start(self) -> bool:
        """Whether every guarantee holds as the design allows and the launch itself has nothing to refuse; an exposure never changes it."""
        return not self.enforcement_error

    @property
    def enforcement_error(self) -> str:
        """The sentence of the first guarantee that is not held, or of the first fact of the launch that refuses it; empty when none."""
        for each in self.guarantees:
            if each.state == "unenforceable" or (each.state == "watched" and each.id not in WATCHABLE):
                return each.reason or sentence("service_failed", name=each.id)
        return self.launch[0] if self.launch else ""

    def to_dict(self) -> dict[str, Any]:
        """The whole report as JSON-ready data, for the catalogue and the details view."""
        return {"platform": self.platform, "backend": self.backend, "backend_version": self.backend_version, "os_build": self.os_build,
                "can_start": self.can_start, "enforcement_error": self.enforcement_error, "launch": list(self.launch),
                "unchecked": list(self.unchecked),
                "guarantees": [{"id": e.id, "state": e.state, "how": e.how, "reason": e.reason, "remedy": e.remedy,
                                "evidence": e.evidence, "resources": dict(e.resources)} for e in self.guarantees],
                "exposures": [{"id": x.id, "platform": x.platform, "what": x.what, "visible": x.visible} for x in self.exposures]}


_CACHE: dict[tuple[str, ...], Report] = {}


def lan_address() -> str:
    """This computer's address on a real interface, or empty when it has none: the proof listens there for P08, and where there is nothing to
    listen on it says so (`Report.unchecked`) and never says the attempt was denied."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp, contextlib.suppress(OSError):
        udp.connect(("192.0.2.1", 9))
        return udp.getsockname()[0]
    return ""


def drop_cache() -> None:
    """Forget what was computed, so the next call reads the machine again (the proof drops it when it finds a deviation)."""
    _CACHE.clear()


def _family() -> str:
    return "darwin" if sys.platform == "darwin" else "linux" if sys.platform.startswith("linux") else sys.platform


def _identity(name: str) -> tuple[str, ...]:
    """What names the backend binary: its resolved path, size and change time, so a replaced binary is read again."""
    found = seatbelt.BINARY if name == "seatbelt" else shutil.which("bwrap") if name == "bubblewrap" else ""
    try:
        info = Path(os.path.realpath(found)).stat() if found else None
    except OSError:
        info = None
    return (found or "", *((str(info.st_size), str(info.st_mtime_ns)) if info else ()))


def cause_of(reason: str, family: str) -> tuple[str, str]:
    """The sentence key of an unavailable backend on ``family`` and the step that would remove it, read from the reason the engine gave."""
    said = reason.lower()
    if family == "darwin":
        return "no_sandbox_tool", ""
    if family not in ("linux", "darwin"):
        return "windows_network", "Run the coding agent in WSL 2."
    if "not installed" in said:
        return "no_bubblewrap", "Poolhouse's installation adds bubblewrap."
    if "disable-userns" in said:
        return "no_disable_userns", "Poolhouse's installation brings a newer bubblewrap."
    if _apparmor_restricts():
        return "apparmor_restricts", "Poolhouse's installation adds the permission."
    return "userns_off", ""


def _apparmor_restricts() -> bool:
    """Whether the kernel restricts unprivileged user namespaces by AppArmor profile (Ubuntu 24.04 and later)."""
    try:
        return Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns").read_text().strip() == "1"
    except OSError:
        return False


def _entries(family: str, blocked: str, remedy: str) -> tuple[Entry, ...]:
    """The thirteen entries: the table of 5.5 for ``family`` when a backend can run, every one unenforceable with ``blocked`` when not."""
    if blocked or family not in ENFORCED_BY:
        return tuple(Entry(each, "unenforceable", "", blocked, remedy) for each in GUARANTEES)
    mechanism, evidence = ENFORCED_BY[family]
    out = []
    for each in GUARANTEES:
        resources = RESOURCES[family] if each == "G11" else {}
        state = ("watched" if "watched" in resources.values() else "enforced") if each == "G11" else \
            "watched" if each == "G10" and family == "darwin" else "enforced"
        how = FLAVOUR.get(each, {}).get(family, HOW[each])
        out.append(Entry(each, state, f"{mechanism}: {how}", evidence=evidence, resources=resources))
    return tuple(out)


def _exposures(family: str) -> tuple[Seen, ...]:
    held = seatbelt.EXPOSURES if family == "darwin" else bubblewrap.EXPOSURES if family == "linux" else ()
    return tuple(Seen(each.id, each.platform, each.what) for each in held)


def _wsl() -> str:
    """``wsl2``, ``wsl1`` or empty, from the kernel string the machine reports."""
    try:
        text = Path("/proc/version").read_text().lower()
    except OSError:
        return ""
    return "" if "microsoft" not in text else "wsl2" if "wsl2" in text else "wsl1"


def _trusted_git() -> bool:
    """Whether a git is found that is not Apple's `/usr/bin/git` shim, which asks a service the confinement refuses."""
    found = shutil.which("git")
    if not found:
        return False
    if sys.platform == "darwin" and os.path.realpath(found) == "/usr/bin/git":
        return Path("/Library/Developer/CommandLineTools/usr/bin/git").is_file()
    return True


def _launch_facts(spec: Spec, stage_free: int, need: int) -> tuple[str, ...]:
    """The sentences of facts of this launch that refuse it whatever the platform guarantees."""
    out = []
    if spec.interactive:
        out.append(sentence("interactive"))
    if _wsl() == "wsl1":
        out.append(sentence("wsl1"))
    project = os.path.realpath(spec.project) if spec.project else ""
    if _wsl() and project.startswith("/mnt/") and len(project.split("/")) > 2 and len(project.split("/")[2]) == 1:
        out.append(sentence("windows_drive"))
    if need and stage_free < need:
        out.append(sentence("stage_too_small", free=stage_free >> 20, need=need >> 20))
    if not _trusted_git():
        out.append(sentence("no_git"))
    return tuple(out)


def _computed(via: Backend | None) -> Report:
    family, chosen = _family(), via or platform_backend()
    state = chosen.available()
    if confined():
        key, remedy = "nested", "Start the command from a terminal or from a session that is not sandboxed."
    elif state.ok:
        key, remedy = "", ""
    else:
        key, remedy = cause_of(state.reason, family)
    blocked = sentence(key) if key else ""
    build = f"{platform.mac_ver()[0] or platform.release()} {platform.version()}"
    return Report(family, chosen.name, " ".join(_identity(chosen.name)[1:]) or chosen.name, build, _entries(family, blocked, remedy),
                  _exposures(family), unchecked=() if lan_address() else ("P08",))


def capability(spec: Spec | None = None, *, via: Backend | None = None, stage_free: int = 0, need: int = 0) -> Report:
    """The report for this computer, and for ``spec`` when given. ``via`` is a backend the caller names in place of the platform's (the
    engine's own seam, used to read the sentences of a backend that cannot run); a report made through it is not kept. The report is kept
    per boot, operating system build and backend binary, and `drop_cache` forgets it."""
    chosen = via or platform_backend()
    key = (_family(), platform.version(), chosen.name, *_identity(chosen.name))
    base = _computed(via) if via is not None else _CACHE.setdefault(key, _computed(None))
    if spec is None:
        return base
    return Report(base.platform, base.backend, base.backend_version, base.os_build, base.guarantees, base.exposures,
                  _launch_facts(spec, stage_free, need), base.unchecked)


def require_capable(spec: Spec | None = None, *, via: Backend | None = None) -> Report:
    """The report, or `IsolationRefused` carrying the sentence of the first guarantee that is not held: the early refusal, before the model
    is leased, that every site that refused before asks for."""
    report = capability(spec, via=via)
    if not report.can_start:
        raise IsolationRefused(report.enforcement_error)
    return report
