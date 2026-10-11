"""Where models are kept outside the hub cache: the decider's pinned files, the weights views and the drafters.

``<cache>/decide/snapshots/<owner--repo>/<commit>/`` holds the decider's pinned files, ``<cache>/spec/weights/
models--<owner>--<repo>-<commit>/`` the view of a checkpoint whose PLE table is read in place, and
``<cache>/spec/dflash`` and ``<cache>/spec/mtp`` the quantized drafters, named ``<source>-<bits>bit.safetensors``.
"""

from __future__ import annotations

import glob
import re
from dataclasses import dataclass
from pathlib import Path

from poolhouse import home, hub
from poolhouse.hub import adopt, derived, hfstore

__all__ = ["Draft", "decide_folders", "drafters", "weight_views"]

VIEW = re.compile(r"models--(?P<owner>.+?)--(?P<name>.+)-(?P<commit>[0-9a-f]{40})")
QUANTIZED = re.compile(r"(?P<stem>.+)-(?P<bits>\d+)bit\.safetensors")
GROUPS = {"dflash": 64, "mtp": 32}
"""The quantization group size each drafter family is saved with."""


def decide_folders() -> tuple[list[adopt.Folder], list[str]]:
    """The folders of the decider's pinned files, and the entries that are not one."""
    root = home.cache("decide", "snapshots")
    found: list[adopt.Folder] = []
    odd: list[str] = []
    for repo_dir in sorted(root.iterdir()) if root.is_dir() else ():
        owner, _, name = repo_dir.name.partition("--")
        for folder in sorted(repo_dir.iterdir()) if repo_dir.is_dir() else ():
            if owner and name and folder.is_dir() and hfstore.COMMIT.fullmatch(folder.name):
                found.append(adopt.Folder(f"{owner}/{name}", folder.name, folder))
            else:
                odd.append(f"{folder}: not a <owner--repo>/<commit> folder of a model")
    return found, odd


def weight_views() -> tuple[list[adopt.Folder], list[str]]:
    """The folders under ``spec/weights``, each the view of one commit of a repository, its names kept."""
    root = home.cache("spec", "weights")
    found: list[adopt.Folder] = []
    odd: list[str] = []
    for folder in sorted(root.iterdir()) if root.is_dir() else ():
        named = VIEW.fullmatch(folder.name)
        if named and folder.is_dir():
            found.append(adopt.Folder(f"{named['owner']}/{named['name']}", named["commit"], folder, keep=True))
        else:
            odd.append(f"{folder}: not a models--<owner>--<repo>-<commit> view")
    return found, odd


@dataclass(frozen=True, slots=True)
class Draft:
    """A quantized drafter file, and what it was made from when the cache can say."""

    path: Path
    recipe: str
    source: derived.Source | None
    why: str = ""


def _holders(stem: str, kind: str) -> list[derived.Source]:
    """What a drafter named ``stem`` can have been made from: the snapshots named by that commit (DFlash) or
    holding a GGUF of that name (MTP)."""
    cache = hub.hub_cache()
    pattern = f"models--*/snapshots/{stem}" if kind == "dflash" else f"models--*/snapshots/*/**/{glob.escape(stem)}.gguf"
    return [derived.Source(*got) for path in sorted(cache.glob(pattern)) if (got := hfstore.identify(path))]


def _where(source: derived.Source) -> Path:
    return hfstore.repo_root(source.repo) / "snapshots" / source.revision / source.file


def _source_of(stem: str, kind: str) -> tuple[derived.Source | None, str]:
    found = _holders(stem, kind)
    if not found:
        return None, f"no snapshot in the hub cache holds {stem}"
    if kind == "mtp" and any(not hfstore.same_bytes(_where(found[0]), _where(one)) for one in found[1:]):
        return None, f"{len(found)} snapshots hold different bytes named {stem}; which one this was made from is not known"
    found.sort(key=lambda one: (one.revision != hfstore.main_commit(one.repo), one.repo, one.revision))
    return found[0], ""


def drafters() -> list[Draft]:
    """The quantized drafter files in the old cache folders, each with its source when it can be named."""
    found: list[Draft] = []
    for kind, group in GROUPS.items():
        for path in sorted(home.cache("spec", kind).glob("*.safetensors")):
            named = QUANTIZED.fullmatch(path.name)
            if not named:
                found.append(Draft(path, "", None, "its name is not <source>-<bits>bit.safetensors"))
                continue
            source, why = _source_of(named["stem"], kind)
            found.append(Draft(path, f"mlx-q{named['bits']}-g{group}", source, why))
    return found
