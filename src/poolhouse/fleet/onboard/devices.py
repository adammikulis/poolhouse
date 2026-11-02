"""The machines this one trusts file requests from, and the ones the owner has revoked: a private file of `Device` rows."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from poolhouse.desktop import clean
from poolhouse.files import read_json, write_json
from poolhouse.lock import only_one
from poolhouse.platform import private_file

from .events import BUS, Bus
from .lan import in_tailnet

__all__ = ["Device", "Devices", "short"]

SCHEMA_VERSION = 1


def short(fingerprint: str) -> str:
    """A fingerprint a person can compare: the first 16 hex digits in groups of four."""
    return " ".join(fingerprint[i:i + 4] for i in range(0, 16, 4))


@dataclass(slots=True)
class Device:
    fingerprint: str
    name: str
    hostname: str
    address: str
    joined: float
    status: str = "active"
    revoked: float = 0.0
    mine: bool = False
    """Whether the owner said this device is theirs, as against another person's."""
    secret: str = ""
    """The key this device signs its file requests with (urlsafe base64); private file."""
    tailnet_address: str = ""
    """Where this device is on the tailnet, read from a Tailscale member whose certificate matched this device's."""


class Devices:
    """The machines this one trusts, and the ones the owner has revoked."""

    def __init__(self, path: Path, *, bus: Bus = BUS,
                 clock: Callable[[], float] = time.time) -> None:
        self.path, self.bus, self.clock = Path(path), bus, clock
        self._lock = threading.RLock()

    def _read(self) -> list[Device]:
        raw = read_json(self.path, {})
        rows = raw.get("devices", []) if isinstance(raw, dict) else []
        out = []
        for row in rows:
            try:
                out.append(Device(**row))
            except TypeError:
                continue
        return out

    def _write(self, rows: list[Device]) -> None:
        write_json(self.path, {"schema_version": SCHEMA_VERSION,
                               "devices": [asdict(d) for d in rows]})
        private_file(self.path)

    def all(self) -> list[Device]:
        with self._lock:
            return self._read()

    def add_accepter(self, fingerprint: str, name: str, address: str, *, secret: str,
                     mine: bool = False) -> Device:
        """Record a machine this one trusts. ``secret`` is the key that machine signs its file
        requests with; ``mine`` only if the person at this machine said it is theirs."""
        with self._lock, only_one(self.path.with_suffix(".lock"), announce=lambda _m: None):
            rows = [d for d in self._read() if d.fingerprint != fingerprint]
            device = Device(fingerprint, clean(name) or "unnamed", "", address, self.clock(),
                            mine=mine, secret=secret,
                            tailnet_address=address if in_tailnet(address) else "")
            rows.append(device)
            self._write(rows)
        return device

    def revoked(self, fingerprint: str) -> bool:
        return any(d.fingerprint == fingerprint and d.status == "revoked" for d in self.all())

    def revoke(self, which: str) -> Device:
        """Revoke by fingerprint (any prefix of four or more hex digits) or by name. Raises
        `KeyError` if nothing matches and `ValueError` if the words match several."""
        which = which.strip().lower().replace(" ", "")
        with self._lock, only_one(self.path.with_suffix(".lock"), announce=lambda _m: None):
            rows = self._read()
            hits = [d for d in rows if (len(which) >= 4 and d.fingerprint.startswith(which))
                    or d.name.lower() == which]
            if not hits:
                raise KeyError(which)
            if len(hits) > 1:
                raise ValueError(f"{which!r} matches {len(hits)} devices; give more of the "
                                 "fingerprint")
            hit = hits[0]
            hit.status, hit.revoked = "revoked", self.clock()
            self._write(rows)
        self.bus.emit("onboard.revoked", "notice", f"device:{hit.fingerprint}",
                      name=hit.name)
        return hit

    def allow_again(self, fingerprint: str) -> bool:
        with self._lock, only_one(self.path.with_suffix(".lock"), announce=lambda _m: None):
            rows = self._read()
            kept = [d for d in rows if d.fingerprint != fingerprint]
            if len(kept) == len(rows):
                return False
            self._write(kept)
            return True
