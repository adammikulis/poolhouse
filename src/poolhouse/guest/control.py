"""What a screen or command does with guest access: join, list, limit, remove. Every answer is plain data; none carries content,
because none is kept."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from poolhouse.fleet import tailnet
from poolhouse.fleet.onboard import lan
from poolhouse.guest import disclosure, wire
from poolhouse.guest.joining import TTL_S, Joins
from poolhouse.guest.store import Limits, Record, Store, fingerprint

__all__ = ["Control", "reach"]


def reach(found: Any = None) -> list[str]:
    """The addresses another computer can reach this one at privately: Tailscale's first (`poolhouse.fleet.tailnet`), then the
    local network's. Never a public one."""
    net = found if found is not None else tailnet.detect()
    candidates = [*sorted(net.addresses if net.up else (), key=lambda a: ":" in a), lan.primary_ip()]
    out = []
    for address in candidates:
        try:
            out.append(wire.private_address(address))
        except ValueError:
            continue
    return list(dict.fromkeys(out))


class Control:
    """One computer's guest access, for as long as the process showing it runs (a joining lives in that process)."""

    def __init__(self, store: Store | None = None, *, addresses: Any = reach, gateway_port: int = 7453) -> None:
        self.store = store or Store()
        self.joins = Joins(self.store)
        self.addresses, self.gateway_port = addresses, gateway_port

    def _where(self) -> list[str]:
        found = self.addresses()
        if not found:
            raise ValueError("this computer has no private network address; join the same network as the other computer, "
                             "or turn Tailscale on")
        return found

    def _gateway_at(self, found: list[str]) -> tuple[str, ...]:
        return tuple(f"[{a}]:{self.gateway_port}" if ":" in a else f"{a}:{self.gateway_port}" for a in found[:2])

    def show(self, role: str, name: str, ttl_s: int = TTL_S) -> dict[str, Any]:
        found = self._where()
        return self.joins.show(role, name, listen=found[0], at=self._gateway_at(found), ttl_s=ttl_s)

    def scan(self, code: str, name: str) -> dict[str, Any]:
        return self.joins.scan(code, name, at=self._gateway_at(self.addresses()))

    def status(self, join_id: str) -> dict[str, Any]:
        return self.joins.status(join_id)

    def confirm(self, join_id: str, match: bool) -> dict[str, Any]:
        return self.joins.confirm(join_id, match)

    def _row(self, record: Record) -> dict[str, Any]:
        state = "removed" if record.revoked else "active" if record.active() else "expired"
        return {"id": record.id, "name": record.name, "state": state, "since": int(record.created), "expires": int(record.expires),
                "key": fingerprint(record.der)[:12]}

    def overview(self) -> dict[str, Any]:
        """Guests of this computer (counts and limits only), the hosts it uses, and what each side must be told."""
        rows = self.store.records()
        return {"guests": [{**self._row(r), "limits": asdict(r.limits), "counts": self.store.counts(r.id)}
                           for r in rows if r.role == "guest"],
                "hosts": [self._row(r) for r in rows if r.role == "host"],
                "disclosure": list(disclosure.FOR_GUEST), "host_notes": list(disclosure.FOR_HOST)}

    def limit(self, record_id: str, changes: dict[str, int]) -> dict[str, Any]:
        found = self.store.record(record_id)
        if found is None:
            raise ValueError("no joined computer has that id")
        return asdict(self.store.change(record_id, limits=Limits.read(changes, found.limits)).limits)

    def revoke(self, record_id: str) -> dict[str, Any]:
        return self._row(self.store.revoke(record_id))

    def close(self) -> None:
        self.joins.close()
