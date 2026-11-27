"""The vision projector a model is served with: the one shipped with its own revision, loaded by default when the memory holds it beside the model."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from poolhouse import home, hub
from poolhouse.serve.mlxref import is_mlx

if TYPE_CHECKING:
    from poolhouse.serve.backend import ServerSpec

__all__ = ["NONE", "Chosen", "chosen", "on_disk", "shipped", "without"]

NONE = "none"
"""What ``--mmproj`` takes to serve a model as text only."""


@dataclass(frozen=True, slots=True)
class Chosen:
    """A spec with its default projector answered: ``picked`` when this added one, ``note`` when it goes without one that is shipped."""

    spec: ServerSpec
    picked: bool = False
    note: str = ""


def shipped(model: str | Path) -> str:
    """The most precise projector shipped with the local file ``model``, '' for none: in the Hub cache one of the model's own revision, elsewhere
    the one `hub.discover` pairs with it."""
    from poolhouse.serve.ops import alongside

    path = home.expand(str(model))
    if str(model).startswith("hf:") or not path.is_file():
        return ""
    if "snapshots" in path.parts[:-2]:
        return alongside(str(path), "auto", "mmproj-", best=True)
    seen = [one for one in hub.discover(formats=("gguf",)) if one.path == path]
    return str(seen[0].mmproj) if seen and seen[0].mmproj else ""


def _wants(spec: ServerSpec) -> bool:
    """Whether ``spec`` takes the shipped projector: it named none, declined none, and serves a llama.cpp model that generates."""
    return spec.vision and not spec.mmproj and not spec.embedding and not spec.reranking and not is_mlx(spec.model)


def chosen(spec: ServerSpec, held_mb: int = 0) -> Chosen:
    """``spec`` with the shipped projector when the model and it fit what the machine lets a model use beside the ``held_mb`` other servers take; else
    ``spec`` declining it, with the line that says so."""
    from poolhouse.serve import admission

    found = shipped(spec.model) if _wants(spec) else ""
    if not found:
        return Chosen(spec)
    seeing = replace(spec, mmproj=found)
    budget, room = admission.budget_mb(), hub.room()
    limits = ([max(0, budget - held_mb) * admission.MIB] if budget else []) + ([room] if room else [])
    need = admission.estimate_bytes(seeing)
    if limits and need > min(limits):
        return Chosen(replace(spec, vision=False), note=(
            f"serving without the vision projector {Path(found).name}, so it reads no pictures: with it the server needs "
            f"{need / admission.MIB / 1024:.1f} GiB and {min(limits) / admission.MIB / 1024:.1f} GiB is free for it"))
    return Chosen(seeing, picked=True)


def on_disk(spec: ServerSpec, *, asked: str | Path, fetch: Callable[[str | Path, str], str]) -> ServerSpec:
    """``spec`` as a start takes it, its projector a file here: the default is answered when this start fetched the model ``asked`` named."""
    if spec.model != asked:
        spec = chosen(spec).spec
    return replace(spec, mmproj=fetch(spec.mmproj, "projector")) if spec.mmproj else spec


def without(spec: ServerSpec, entry: dict | None, *, adopted: bool) -> str:
    """The line saying the server recorded as ``entry`` reads no pictures although the model ships a projector ``spec`` did not decline; '' otherwise."""
    if not entry or entry.get("mmproj") or not _wants(spec) or not (found := shipped(spec.model)):
        return ""
    how = ("it was already up that way; stop it and lease it again to load the projector" if adopted
           else "the projector did not fit beside the model, or the server would not start with it")
    return f"this server runs without the vision projector {Path(found).name} shipped with the model, so it reads no pictures: {how}"
