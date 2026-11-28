"""The pieces both Seatbelt profiles are written from: quoting, filters, the ancestors a path is reached through and the rules of a window of
loopback ports. Used by `sandbox.seatbelt` and `sandbox.seatbelt_open` and by nothing else."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path

from poolhouse.sandbox.policy import PolicyError, checked_path

__all__ = ["BATCH", "SYMLINKS", "ProfileError", "accepted", "ancestors", "executable_links", "filters", "literal_rules", "quote", "trees"]

SYMLINKS = ("/etc", "/var", "/tmp")  # noqa: S108 - the system symlinks, not a temp file
BATCH = 128
"""The most filters one rule holds, so no compiler expression grows without bound."""
_BAD = re.compile(r"[\x00-\x1f\x7f]")


class ProfileError(PolicyError):
    """A string that cannot be put in a profile."""


def quote(text: str) -> str:
    """``text`` as a Seatbelt string literal: quote and backslash escaped, control characters
    refused."""
    if _BAD.search(text):
        raise ProfileError(f"{text!r} holds a control character")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


SPECIAL = re.compile(r"[.^$*+?()\[\]{}|\\]")
"""The characters a regular expression of a profile reads as more than themselves."""
MANY = 8
"""From this many names in one folder, expressions name them together: a filter for each would make a profile longer than a command line holds."""
EXPRESSION = 800
"""The most characters of one expression's alternation: the profile's reader ends a longer string with `Error reading string`."""


def trees(paths: Sequence[str]) -> list[str]:
    """Filters that together hold each of ``paths`` and what is inside it: a ``subpath`` for each, and one expression for the names of a folder
    that holds many of them."""
    folders: dict[str, list[str]] = {}
    for path in dict.fromkeys(paths):
        folder, _, name = path.rpartition("/")
        folders.setdefault(folder, []).append(name)
    out = []
    for folder, names in folders.items():
        plain = [name for name in names if name and '"' not in name and not _BAD.search(name)] if len(names) >= MANY else []
        out += [f'(regex #"^{_escaped(folder)}/({either})(/|$)")' for either in _either(plain)]
        left = set(names) - set(plain)
        out += [f"(subpath {quote(f'{folder}/{name}' if name else '/')})" for name in names if name in left]
    return out


def _escaped(text: str) -> str:
    return SPECIAL.sub(r"\\\g<0>", text)


def _either(names: Sequence[str]) -> list[str]:
    """Alternations that together name each of ``names``, each short enough for the profile's reader."""
    out: list[str] = []
    for name in map(_escaped, names):
        if out and len(out[-1]) + len(name) < EXPRESSION:
            out[-1] += "|" + name
        else:
            out.append(name)
    return out


def ancestors(paths: Sequence[str]) -> list[str]:
    """Every folder above each of ``paths``, the root first."""
    seen: dict[str, None] = {"/": None}
    for path in paths:
        parts = path.strip("/").split("/")
        for i in range(1, len(parts)):
            seen["/" + "/".join(parts[:i])] = None
    return list(seen)


def filters(kind: str, paths: Sequence[str]) -> str:
    """One filter of ``kind`` for each path, as the union a rule takes."""
    return " ".join(f"({kind} {quote(p)})" for p in paths)


def literal_rules(operation: str, paths: Sequence[str]) -> list[str]:
    """Keep the same literal union without an unbounded compiler expression."""
    unique = list(dict.fromkeys(paths))
    return [f"(allow {operation} {filters('literal', unique[index:index + BATCH])})"
            for index in range(0, len(unique), BATCH)]


def executable_links(program: str) -> list[str]:
    """Each link of the chain from ``program`` to the file it runs."""
    links = []
    path = Path(program)
    while path.is_symlink():
        if str(path) in links or len(links) >= 16:
            raise ProfileError("command: executable symlink chain exceeds bound or loops")
        links.append(checked_path(os.fspath(path), what="executable link", link=True))
        target = path.readlink()
        path = Path(os.path.normpath(target if target.is_absolute() else path.parent / target))
    return links


def accepted(ports: Sequence[int]) -> list[str]:
    """TCP to ``ports`` of this machine and serving on a socket handed in, with every bind of them refused. The refusal follows the inbound rule,
    which would otherwise carry the bind with it. UDP is left refused: it receives on a port its sender names."""
    if not ports:
        return []

    def each(side: str, kind: str) -> str:
        return " ".join(f'({side} {kind} "localhost:{port}")' for port in ports)

    return [f"(allow network-outbound {each('remote', 'tcp')})", f"(allow network-inbound {each('local', 'tcp')})",
            f"(deny network-bind {each('local', 'ip')})"]
