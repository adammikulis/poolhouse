"""The project's hashed lock: the exact packages, versions and sha256 digests a first-use install may fetch.

``requirements.lock`` beside this file is written by ``scripts/lock-requirements`` from ``pyproject.toml``,
for the extras in `EXTRAS`, from prebuilt wheels only (building a source distribution would fetch unpinned
build tools). `select` returns the lines of the lock that some requirements and everything they depend on
need; an installer given them with ``--require-hashes --no-deps`` fetches nothing else and fails on any file
that does not match. A requirement the lock does not hold is refused: it is not installed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

__all__ = ["EXTRAS", "FILE", "Entry", "Unpinned", "entries", "select", "text"]

FILE = Path(__file__).with_name("requirements.lock")
EXTRAS = ("agents", "arrays", "asr", "claude", "coordinator", "decide", "decide-pointer", "ffmpeg", "github-app", "graph",
          "gym-traffic", "gym-warehouse", "heif", "hub", "llama-build", "memory", "mlx", "pdf", "pdf-agpl", "pdf-render",
          "plot", "scrape", "serve-strata", "spec", "store", "torch", "train", "train-lora", "train-mlx", "transformers", "vision",
          "web")
"""The extras the lock covers, and so the only ones installed at first use."""
_VIA = re.compile(r"^\s*#\s*(?:via\b)?\s*([A-Za-z0-9][A-Za-z0-9._-]*)?")


class Unpinned(ValueError):
    """Something asked for that the lock holds no digest for, so it is not installed."""


@dataclass(frozen=True, slots=True)
class Entry:
    """One locked release: its requirement (with the marker saying where it applies), the lines an installer reads
    and the packages that asked for it."""

    requirement: Requirement
    lines: tuple[str, ...]
    via: frozenset[str]

    @property
    def name(self) -> str:
        return canonicalize_name(self.requirement.name)

    @property
    def version(self) -> str:
        return next(iter(self.requirement.specifier)).version

    def applies(self) -> bool:
        """Whether this release is the one for this interpreter and system."""
        return self.requirement.marker is None or self.requirement.marker.evaluate()


def entries(path: Path | None = None) -> list[Entry]:
    """Every release the lock holds. `Unpinned` when it cannot be read or a release has no digest."""
    path = path or FILE
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Unpinned(f"the lock {path} cannot be read ({exc}), so nothing is installed") from exc
    found: list[tuple[list[str], set[str]]] = []
    for line in content.splitlines():
        if line and not line[0].isspace() and not line.startswith("#"):
            found.append(([line], set()))
        elif found and line.lstrip().startswith("--hash="):
            found[-1][0].append(line)
        elif found and line.lstrip().startswith("#") and (named := _VIA.match(line)) and named.group(1):
            found[-1][1].add(canonicalize_name(named.group(1)))
    out = []
    for lines, via in found:
        want = Requirement(lines[0].rstrip().removesuffix("\\").strip())
        if len(lines) == 1 or [one.operator for one in want.specifier] != ["=="]:
            raise Unpinned(f"the lock holds {want.name} without one exact version and its digests, so nothing is installed")
        out.append(Entry(want, tuple(lines), frozenset(via)))
    return out


def select(requirements: Sequence[str], held: Iterable[Entry] | None = None) -> list[Entry]:
    """The releases that ``requirements`` and everything they depend on need, in the lock's order. `Unpinned` when a
    requirement is not in the lock, or the lock's version of it is outside the range asked for."""
    held = list(entries() if held is None else held)
    named: dict[str, list[Entry]] = {}
    children: dict[str, set[str]] = {}
    for entry in held:
        named.setdefault(entry.name, []).append(entry)
        for parent in entry.via:
            children.setdefault(parent, set()).add(entry.name)
    waiting = []
    for asked in requirements:
        want = Requirement(asked)
        name = canonicalize_name(want.name)
        if want.url:
            raise Unpinned(f"{want.name} comes from {want.url}, which the lock cannot hold a digest for, so it is not installed")
        if not any(want.specifier.contains(entry.version, prereleases=True) for entry in named.get(name, ())):
            raise Unpinned(f"{asked} is not in the project's hashed lock, so it is not installed")
        waiting.append(name)
    wanted: set[str] = set()
    while waiting:
        name = waiting.pop()
        if name not in wanted:
            wanted.add(name)
            waiting.extend(children.get(name, ()))
    return [entry for entry in held if entry.name in wanted]


def text(chosen: Iterable[Entry]) -> str:
    """``chosen`` as a requirements file."""
    return "".join(line.rstrip().removesuffix("\\").rstrip() + (" \\\n" if index < len(entry.lines) - 1 else "\n")
                   for entry in chosen for index, line in enumerate(entry.lines))
