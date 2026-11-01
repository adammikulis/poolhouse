"""The devices of this device's pool that serve compute, as its Rust node lists them.

The node says which compute services belong to the pool (`service_pool.Pool.services`); each one is
asked for its ``/health`` over a connection pinned to the certificate the node vouches for. A device
that does not answer is not listed. This machine's own daemon is on loopback, under the owner's
token. Everything that works on "the members" -- ``poolhouse-pool plan | pause | resume``, the
fleet bench, the MCP ``fleet_members`` tool, the setup findings -- reads this one list.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from poolhouse import macauth
from poolhouse.units import human_bytes

from . import service_pool, tls
from .launch import already_running
from .onboard.lan import DEFAULT_HTTP_PORT
from .remote import Member, MemberError
from .runtime_paths import default_root

__all__ = ["client_for", "describe", "members", "owner_token"]


def describe(name: str, base_url: str, machine: str, report: dict[str, Any], *, is_self: bool = False) -> dict[str, Any]:
    """One device as a row: what it serves, its room, whether it is busy, its commit.

    Read out of the device's ``/health`` report, defensively: a daemon on an older build reports
    fewer fields, and a row with a '?' in it beats no row."""
    served = [f"{model}:{one.get('port', '?')}" for one in report.get("serving") or []
              for model in one.get("models") or []]
    total, free = report.get("vram_total_gb"), report.get("vram_free_gb")
    if report.get("room_bytes"):
        room = human_bytes(int(report["room_bytes"]))
    elif total:
        room = f"{free if free is not None else '?'}/{total} GB"
    elif report.get("ram_gb"):
        room = f"{report.get('ram_gb')} GB ram"
    else:
        room = "?"
    lock = report.get("lock") or ("measuring" if report.get("measuring") else "")
    said = report.get("availability") or {}
    slots = int(report.get("slots") or 1)
    return {
        "name": name, "called": name, "machine": machine, "host": base_url.split("//", 1)[-1].rsplit(":", 1)[0],
        "base_url": base_url, "port": int(base_url.rsplit(":", 1)[-1]) if base_url.rsplit(":", 1)[-1].isdigit() else 0,
        "busy": bool(report.get("busy")), "free": int(report.get("free", slots)), "slots": slots,
        "queued": int(report.get("queued") or 0), "room": room, "serving": served,
        "models": [str(m.get("name")) for m in (report.get("models") or []) if m.get("name")],
        "speech": [str(one) for one in (report.get("speech") or [])],
        "lock": str(lock) if lock else "",
        "paused": bool(said.get("paused")),
        "paused_because": (str(said.get("unavailable_because") or "paused") if said.get("paused") else ""),
        "commit": str(report.get("bench_commit") or report.get("commit") or "?"),
        "commit_age_s": float(report.get("commit_age_s") or 0.0),
        "version": str(report.get("version") or ""),
        "tracking": str(report.get("tracking") or ""),
        "checked_at": float(report.get("update_checked_at") or 0.0),
        "update_error": str(report.get("update_error") or ""),
        "gpu": str(report.get("gpu") or ""),
        "is_self": is_self,
        "device": report,
    }


def owner_token(root: Path | str | None = None) -> str:
    """The owner credential of this machine's daemon under ``root``, or '' when it has not made one."""
    try:
        held = (Path(root) if root else default_root()) / "token"
        text = held.read_text().strip()
    except OSError:
        return ""
    return text if text.startswith(macauth.PREFIX) else ""


def _asked(row: service_pool.Binding, timeout_s: float) -> dict[str, Any] | None:
    try:
        member = Member(row.endpoint, "", timeout=timeout_s)
        return describe(row.name, member.base_url, row.device, member.health())
    except (MemberError, OSError, ValueError, PermissionError):
        return None


def members(*, timeout_s: float = 2.0, port: int = DEFAULT_HTTP_PORT) -> list[dict[str, Any]]:
    """Every compute device of this node's pool that answers now, this machine first.

    Raises `PermissionError` when the node does not answer: there is no pool to list without it."""
    services = service_pool.Pool().services()
    own = tls.local().fingerprint
    found: list[dict[str, Any]] = []
    local = already_running(port)
    if local is not None:
        found.append(describe(str(local.get("name") or "this machine"), f"http://127.0.0.1:{port}",
                              str(local.get("machine") or ""), local, is_self=True))
    others = [row for row in services if row.fingerprint != own]
    with ThreadPoolExecutor(max_workers=max(1, len(others))) as asking:
        answers = list(asking.map(lambda row: _asked(row, timeout_s), others))
    found.extend(sorted((a for a in answers if a), key=lambda a: a["name"]))
    return found


def client_for(row: dict[str, Any], *, root: Path | str | None = None, timeout: float = 60.0) -> Member | None:
    """The client that reaches the device of ``row``, or None when this machine holds no credential for it.

    This machine's own daemon is reached on loopback under the owner token; any other device over
    the node-authorized connection `Member` derives."""
    url = str(row.get("base_url") or "")
    try:
        if row.get("is_self"):
            token = owner_token(root)
            return Member(url, token, timeout=timeout) if token else None
        return Member(url, "", timeout=timeout)
    except (PermissionError, ValueError):
        return None
