"""The machine's memory ledger: `servers.json`, the one place every model server of every process is written down, and
the arithmetic that admits a new one against it.

Two kinds of caller share it. The core (`poolhouse.core`) admits a server against this ledger alone. The pool's broker
(`poolhouse.serve.admitting`) asks the node's lease table as well, for the queue and for the memory leased to work that
is not a server, but it writes its reservation here first and counts every record here, so a server the core started and
a server the pool started see each other's memory whichever asked first.

A reservation is a record with ``pending`` set, written under `lock_file` in the same step as the look at the records;
`live` keeps it counted for as long as its owner process exists, and `merge_state` drops it with its owner.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from poolhouse.files import read_json, write_json
from poolhouse.lock import only_one
from poolhouse.serve import admission
from poolhouse.serve.backend import ServerInfo, ServerSpec
from poolhouse.serve.leases import merge_state, recorded_servers
from poolhouse.serve.process import cmdline_digest, started_at

__all__ = ["STATE_LOCK_S", "entry", "fits", "lock_file", "never_fits", "recorded", "reserving", "save", "taken"]

logger = logging.getLogger(__name__)

STATE_LOCK_S = 30.0


def lock_file(state: Path) -> Path:
    """The lock under which the records are looked at and a reservation is written in one step."""
    return state.with_name("servers.start.lock")


def taken(records: Mapping[int, Mapping[str, Any]], *, besides: int | None = None) -> int:
    """The MiB the live records hold, leaving out the record of port ``besides`` (the one being replaced)."""
    return sum(admission.entry_mb(one) for port, one in records.items() if port != besides and admission.live(one))


def fits(records: Mapping[int, Mapping[str, Any]], need_mb: int, *, besides: int | None = None) -> tuple[bool, str]:
    """Whether ``need_mb`` fits in the machine's budget beside the live records, and why not in words (the node's own wording,
    each server held named as the node names its slot)."""
    budget, held = admission.budget_mb(), taken(records, besides=besides)
    lines = [f"{admission.slot_name(one.get('model') or '', port)} {admission.entry_mb(one) / 1024:.1f} GiB"
             for port, one in sorted(records.items()) if port != besides and admission.live(one)]
    said = (f"this server needs {need_mb / 1024:.1f} GiB beside {held / 1024:.1f} GiB already taken, of the "
            f"{budget / 1024:.1f} GiB this machine allows ({'; '.join(lines) or 'nothing else'})")
    return (not budget or held + need_mb <= budget), said


def never_fits(model: str | Path, need_mb: int) -> None:
    """`AdmissionRefused` when ``need_mb`` is more than the machine allows a model at all, which no wait would change."""
    budget = admission.budget_mb()
    if budget and need_mb > budget:
        raise admission.AdmissionRefused(f"{Path(str(model)).name} needs {need_mb / 1024:.1f} GiB, more than the "
                                         f"{budget / 1024:.1f} GiB this machine allows a model")


def entry(spec: ServerSpec, *, backend: str, lease: str, est_mb: int,  # noqa: PLR0913 - what a record says of a server
          info: ServerInfo | None = None, holder: str = "", extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The record of a server in the ledger: written when its memory is reserved (no ``info``: ``pending``) and
    again, with the process and where it answers, once it is up. ``extra`` is what only the caller knows (cache settings, notes)."""
    base = {
        "port": spec.port, "pid": None, "backend": backend, "model": str(spec.model), "owner_pid": os.getpid(),
        "pending": info is None, "device": admission.device_of(spec), "lease": lease, "est_mb": est_mb,
        "embedding": bool(spec.embedding), "mmproj": bool(spec.mmproj or (info is not None and info.mmproj)),
        "context": int(spec.context), "parallel": int(spec.parallel or 1),
    }
    if info is not None:
        base.update(pid=info.pid, port=info.port, backend=info.backend or backend, base_url=info.base_url,
                    load_s=info.load_s, warmup_s=info.warmup_s, started=started_at(info.pid),
                    cmdline=cmdline_digest(info.pid), **({"log": str(info.log_path)} if info.log_path else {}))
    return {**base, **({"holder": holder} if holder else {}), **(extra or {})}


def recorded(state: Path) -> dict[int, dict]:
    """The live records of the ledger by port."""
    return {port: one for port, one in recorded_servers(state).items() if admission.live(one)}


def save(state: Path, mine: Mapping[str, Mapping[str, Any]], *, holder: str = "") -> None:
    """Write ``mine`` over the live records of every other holder, under the file's own lock."""
    with only_one(state.with_suffix(".lock"), wait=True, timeout=STATE_LOCK_S, announce=logger.debug):
        found = read_json(state, {})
        write_json(state, merge_state(found if isinstance(found, dict) else {}, dict(mine), os.getpid(), holder=holder))


def reserving(state: Path):
    """The lock to hold while the records are looked at and a reservation is written."""
    return only_one(lock_file(state), wait=True, timeout=STATE_LOCK_S, announce=logger.debug)
