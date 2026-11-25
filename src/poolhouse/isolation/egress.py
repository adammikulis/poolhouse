"""The hosts a coding session may be granted a route to: package registries, each with its source and its measured bound.

`contracts/egress-hosts.json` is the only list. A host the file does not name is refused for every caller, and an entry that names no
source or no measured bound is a damaged list, not a host (docs/design-isolated-launcher.md, 6.3 and 8.3, AF5). Two bounds are recorded
with the measurement of each, on the date and with the command the entry records: what one tunnel may send (the most one tunnel sent while a real
client installed packages) and what a session may send over all its tunnels (the larger install and one more of the small one); a download
counts nothing against either.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from poolhouse.contracts.loader import ContractError, load

__all__ = ["Host", "hosts", "valid_name"]

NAME = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]")
"""A DNS name in lower case with at least two labels: never an address, a wildcard, a port or a name with a path."""
FILE = "egress-hosts.json"


@dataclass(frozen=True, slots=True)
class Host:
    """One registry host: what it is for, the document that names it, the bytes one tunnel may send to it (``bytes_out``), the bytes a session
    may send over all its tunnels (``session_bytes``) and the measurement of both."""

    name: str
    ecosystem: str
    source: str
    bytes_out: int
    command: str
    date: str
    session_bytes: int = 0


def valid_name(text: str) -> bool:
    """Whether ``text`` is a lower-case DNS name and nothing else."""
    return isinstance(text, str) and bool(NAME.fullmatch(text)) and not text.replace(".", "").isdigit()


def _entry(raw: Any) -> Host:
    if not isinstance(raw, Mapping):
        raise ContractError(f"{FILE}: an entry is not an object")
    measured = raw.get("measured")
    name = raw.get("host")
    if not valid_name(name if isinstance(name, str) else ""):
        raise ContractError(f"{FILE}: {name!r} is not a host name")
    if not str(raw.get("source", "")).startswith("https://") or not str(raw.get("ecosystem", "")).strip():
        raise ContractError(f"{FILE}: {name} names no source document or no ecosystem")
    if not isinstance(measured, Mapping) or not all(measured.get(key) for key in ("bytes_out", "command", "date")):
        raise ContractError(f"{FILE}: {name} names no measured bound")
    bound, session = raw.get("bytes_out_bound"), raw.get("session_bytes_out_bound")
    largest = int(measured.get("max_tunnel_out") or measured["bytes_out"])
    if not isinstance(bound, int) or isinstance(bound, bool) or bound < largest:
        raise ContractError(f"{FILE}: the bound of {name} is not a number of bytes at least the most one measured tunnel sent")
    wide = raw.get("measured_session")
    if (not isinstance(wide, Mapping) or not all(wide.get(key) for key in ("bytes_out", "command", "date"))
            or not isinstance(session, int) or isinstance(session, bool) or session < int(wide["bytes_out"]) + int(measured["bytes_out"])
            or session < bound):
        raise ContractError(f"{FILE}: {name} names no measured budget for a session of the larger install and one more of the small one")
    return Host(name, str(raw["ecosystem"]), str(raw["source"]), bound, str(measured["command"]), str(measured["date"]), session)


def hosts() -> dict[str, Host]:
    """Every host of the list by name, or `ContractError` when the file is missing or an entry lacks its source or its bound."""
    entries = load(FILE).get("hosts")
    if not isinstance(entries, list) or not entries:
        raise ContractError(f"{FILE}: no hosts")
    found = [_entry(each) for each in entries]
    if len({each.name for each in found}) != len(found):
        raise ContractError(f"{FILE}: a host is listed twice")
    return {each.name: each for each in found}
