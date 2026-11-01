"""The devices of the pool as rows a screen draws, and the bench sweep line built from a pick.

A row is a plain dict: the node-authorized compute service of one device (`remote.Member`, from the node's service list) or this
machine, with what its health report says it serves, its room, whether it is busy and its commit. Rows carry no secret; a caller that
needs a request secret asks `service_pool.Pool.secret` for the row's ``fingerprint``.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterable, Sequence
from typing import Any

from poolhouse.units import human_bytes

__all__ = ["describe", "member_row", "named_apart", "sweep_argv"]


def member_row(member: Any) -> dict[str, Any]:
    """One `remote.Member` as the row every screen shares; ``fingerprint`` names its certificate."""
    beacon = member.beacon
    cert = base64.b64decode(beacon.cert) if beacon.cert else b""
    return {"name": beacon.name, "port": beacon.port, "machine": beacon.machine, "host": beacon.host, "hostname": "",
            "base_url": member.base_url, "device": beacon.device, "busy": beacon.busy, "queued": beacon.queued,
            "slots": beacon.slots, "free": beacon.free, "instance": beacon.instance,
            "fingerprint": hashlib.sha256(cert).hexdigest() if cert else "", "is_self": False, "pools": []}


def named_apart(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``rows``, each ``name`` two machines share followed by ``#`` and the shortest prefix of four or more
    characters of its ``machine`` that tells them apart."""
    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_name.setdefault(str(row.get("name") or ""), []).append(row)
    for name, same in by_name.items():
        machines = {str(row.get("machine") or "") for row in same}
        if len(machines) < 2:
            continue
        width = 4
        while len({m[:width] for m in machines}) < len(machines):
            width += 1
        for row in same:
            row["name"] = f"{name}#{str(row.get('machine') or '')[:width] or '?'}"
    return rows


def describe(row: dict[str, Any], *, pools: Iterable[str] = ()) -> dict[str, Any]:
    """One member as the fleet screen's row: what it serves, its room, whether it is busy, its commit.

    Read out of the row's device report, defensively: a daemon on an older build reports fewer fields, and a row with a '?'
    in it beats no row.
    """
    d = row.get("device") or {}
    served = []
    for one in d.get("serving") or []:
        for model in one.get("models") or []:
            served.append(f"{model}:{one.get('port', '?')}")
    vram_total, vram_free = d.get("vram_total_gb"), d.get("vram_free_gb")
    if d.get("room_bytes"):
        room = human_bytes(int(d["room_bytes"]))
    elif vram_total:
        room = f"{vram_free if vram_free is not None else '?'}/{vram_total} GB"
    elif d.get("ram_gb"):
        room = f"{d.get('ram_gb')} GB ram"
    else:
        room = "?"
    lock = d.get("lock") or ("measuring" if d.get("measuring") else "")
    said = d.get("availability") or {}
    return {
        "name": row["name"], "called": str(row.get("called") or row["name"]), "machine": str(row.get("machine") or ""),
        "host": str(row.get("host") or ""), "base_url": str(row.get("base_url") or ""),
        "port": int(row.get("port") or 0), "busy": bool(row.get("busy")), "free": int(row.get("free", 1)),
        "slots": int(row.get("slots") or 1), "queued": int(row.get("queued") or 0),
        "room": room, "serving": served,
        "models": [str(m.get("name")) for m in (d.get("models") or []) if m.get("name")],
        "speech": [str(one) for one in (d.get("speech") or [])],
        "lock": str(lock) if lock else "",
        "paused": bool(said.get("paused")),
        "paused_because": (str(said.get("unavailable_because") or "paused") if said.get("paused") else ""),
        "commit": str(d.get("bench_commit") or d.get("commit") or "?"),
        "commit_age_s": float(d.get("commit_age_s") or 0.0),
        "version": str(d.get("version") or ""),
        "tracking": str(d.get("tracking") or ""),
        "checked_at": float(d.get("update_checked_at") or 0.0),
        "update_error": str(d.get("update_error") or ""),
        "gpu": str(d.get("gpu") or ""),
        "pools": list(pools),
        "is_self": bool(row.get("is_self")),
        "display_url": str(row.get("display_url") or ""),
        "device": d,
    }


def sweep_argv(models: Sequence[str], *, members: Sequence[str] = (), sample: int = 0,
               label: str = "", extra: Sequence[str] = ()) -> list[str]:
    """The ``python -m poolhouse.bench`` line that spreads ``models`` over the fleet, one job each.

    Built here rather than in the page, so the command the page shows is the command the daemon runs and the one a person
    could paste. ``--detach`` is not on it: the caller detaches, and hands the log back.
    """
    if not models:
        raise ValueError("a sweep needs at least one model")
    argv = ["sweep", "--fleet"]
    for m in models:
        argv += ["--serve", str(m)]
    if members:
        argv += ["--members", ",".join(members)]
    if sample:
        argv += ["--sample", str(int(sample))]
    if label:
        argv += ["--label", label]
    argv += [str(a) for a in extra]
    return argv
