"""The hosts a coding session may be granted a route to: package registries, each with its source and its measured bound.

`contracts/egress-hosts.json` is the only list. A host the file does not name is refused for every caller, and an entry that names no
source or no measured bound is a damaged list, not a host (docs/design-isolated-launcher.md, 6.3 and 8.3, AF5). The bound is the most
bytes a real client sent to the host while it installed a package, measured on the date and with the command the entry records; a
download counts nothing against it.
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
    """One registry host: what it is for, the document that names it, and the bytes a client was measured to send to it."""

    name: str
    ecosystem: str
    source: str
    bytes_out: int
    command: str
    date: str


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
    bound = raw.get("bytes_out_bound")
    if not isinstance(bound, int) or isinstance(bound, bool) or bound < int(measured["bytes_out"]):
        raise ContractError(f"{FILE}: the bound of {name} is not a number of bytes at least the measured one")
    return Host(name, str(raw["ecosystem"]), str(raw["source"]), bound, str(measured["command"]), str(measured["date"]))


def hosts() -> dict[str, Host]:
    """Every host of the list by name, or `ContractError` when the file is missing or an entry lacks its source or its bound."""
    entries = load(FILE).get("hosts")
    if not isinstance(entries, list) or not entries:
        raise ContractError(f"{FILE}: no hosts")
    found = [_entry(each) for each in entries]
    if len({each.name for each in found}) != len(found):
        raise ContractError(f"{FILE}: a host is listed twice")
    return {each.name: each for each in found}
