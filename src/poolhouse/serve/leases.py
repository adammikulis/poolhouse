"""The file recording which model servers this machine is running, and reading it back."""

from __future__ import annotations

from pathlib import Path

from poolhouse.ownership import lease_file, recorded_servers, same_process
from poolhouse.serve.process import pid_exists

__all__ = ["already_up", "lease_file", "merge_state", "orphaned", "recorded_servers"]


def merge_state(on_disk: dict, mine: dict, owner_pid: int, *, holder: str = "") -> dict:
    """The servers of one holder in this process (the manager's, or the core's), merged over every other record whose owner
    or server is alive. Two holders in one process keep each other's records."""
    merged = {
        key: entry
        for key, entry in on_disk.items()
        if isinstance(entry, dict)
        and (entry.get("owner_pid") != owner_pid or str(entry.get("holder") or "") != holder)
        and (pid_exists(entry.get("owner_pid")) or pid_exists(entry.get("pid")))
    }
    merged.update(mine)
    return merged


def orphaned(entry: dict, *, strict: bool = False) -> bool:
    """Whether a record's server is running on after the process that leased it has gone."""
    owner, pid = entry.get("owner_pid"), entry.get("pid")
    return (isinstance(owner, int) and isinstance(pid, int) and owner != pid
            and not pid_exists(owner) and same_process(entry, strict=strict))


def already_up(model: str, port: int, *, state_file: Path | None = None) -> dict | None:
    """The recorded server on ``port`` if it serves ``model`` (by file name) and its
    process is alive -- whatever its settings. A conversation that would lease one slot on a
    port already holding the same weights with other settings uses what is up rather than
    reloading them: the reload is the cost, the settings are not (Adam, 2026-09-03)."""
    entry = recorded_servers(state_file).get(int(port))
    if not entry:
        return None
    if Path(str(entry.get("model") or "")).name != Path(str(model)).name:
        return None
    return entry if pid_exists(int(entry.get("pid") or 0)) else None
