"""The attestation of a live harness process: it is inside the confinement it was started in, the same boundary the proof's probe ran in.

The check is the operating system's own report about the process, never anything the process says (docs/design-isolated-launcher.md,
10.3). macOS asks the kernel whether a sandbox applies to the pid (VERIFIED for a `sandbox-exec` child). Linux compares the namespaces of
the pid with the launcher's and reads its capability and privilege facts (PREDICTED: no Linux device ran it). Windows asks for an
AppContainer token and a job, and is not built (the backend refuses to start anything).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from poolhouse.sandbox.confinement import seatbelt_state

__all__ = ["Attestation", "attest"]

NAMESPACES = ("mnt", "pid", "net", "user")


@dataclass(frozen=True, slots=True)
class Attestation:
    """Whether ``pid`` is confined, and the fact that says so or the one that shows it is not."""

    pid: int
    confined: bool
    fact: str


def _seatbelt(pid: int) -> Attestation:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return Attestation(pid, False, "no such process")
    except PermissionError:
        pass
    try:
        state = seatbelt_state(pid)
    except (OSError, AttributeError):
        return Attestation(pid, False, "the kernel could not be asked")
    facts = {"refuses": "a sandbox applies to the process", "none": "no sandbox applies to the process",
             "allows": "a sandbox applies to the process but allows everything it does not name, which confines nothing",
             "unknown": "the kernel could not be asked"}
    return Attestation(pid, state == "refuses", facts[state])


def _status(pid: int) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        key, _, value = line.partition(":")
        found[key] = value.strip()
    return found


def _namespaces(pid: int) -> Attestation:
    try:
        own = {name: str(Path(f"/proc/self/ns/{name}").readlink()) for name in NAMESPACES}
        theirs = {name: str(Path(f"/proc/{pid}/ns/{name}").readlink()) for name in NAMESPACES}
        status = _status(pid)
    except OSError:
        return Attestation(pid, False, "the process could not be read")
    shared = [name for name in NAMESPACES if own[name] == theirs[name]]
    if shared:
        return Attestation(pid, False, "shares the launcher's " + ", ".join(shared) + " namespace")
    if status.get("NoNewPrivs") != "1" or int(status.get("CapEff", "1"), 16) != 0:
        return Attestation(pid, False, "holds capabilities or may gain privileges")
    return Attestation(pid, True, "own namespaces, no capability, no new privileges")


def attest(pid: int) -> Attestation:
    """The operating system's report on the live process ``pid``. Where the check cannot be made the process is not attested."""
    if sys.platform == "darwin":
        return _seatbelt(pid)
    if sys.platform.startswith("linux"):
        return _namespaces(pid)
    return Attestation(pid, False, "no check of a confined process exists on this platform")
