"""The vision projector a model is served with: the one shipped with its own revision. A start loads it by default when it fits beside the memory
servers are admitted against, and never for a lease that asks for prompt reuse, which llama.cpp switches off for a server with a projector."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from poolhouse import home, hub
from poolhouse.serve.mlxref import is_mlx

if TYPE_CHECKING:
    from poolhouse.serve.backend import ServerSpec

__all__ = ["AUTO", "MMPROJ_FACTOR", "NONE", "REUSE_OFF", "Chosen", "Fetched", "chosen", "counted", "counted_as", "held_bytes", "kept", "on_disk",
           "reuses", "said", "shipped", "unlike", "without"]

AUTO = "auto"
"""What ``--mmproj`` takes to ask for pictures: the projector the model ships, loaded also when the lease asks for prompt reuse."""
NONE = "none"
"""What ``--mmproj`` takes to serve a model as text only."""
MMPROJ_FACTOR = 1.5
"""A projector's worst-case memory over its file size (784 MiB file, 1165 MiB estimated)."""
REUSE_OFF = "prompt reuse (--cache-reuse) and context shift are off: llama.cpp switches them off for a server with a vision projector"
ASK = "ask for pictures with --mmproj auto"


@dataclass(frozen=True, slots=True)
class Chosen:
    """A spec with its projector answered: ``picked`` when this added one nobody asked for, ``note`` when there is something to say."""

    spec: ServerSpec
    picked: bool = False
    note: str = ""


class Fetched(Exception):
    """A start fetched the model it was asked for and the model ships a projector: the start is made again with the model's file."""

    def __init__(self, model: str | Path) -> None:
        super().__init__(str(model))
        self.model = model


def shipped(model: str | Path) -> str:
    """The most precise projector that belongs to the local file ``model``, '' for none: in the Hub cache one of the model's own revision, in a
    plain folder one beside it, and for a store's blob the one `hub.discover` pairs with it."""
    from poolhouse.serve.ops import alongside

    path = home.expand(str(model))
    if str(model).startswith("hf:") or not path.is_file():
        return ""
    if path.suffix.lower() != ".gguf":
        seen = [one for one in hub.discover(formats=("gguf",)) if one.path == path]
        return str(seen[0].mmproj) if seen and seen[0].mmproj else ""
    found = alongside(str(path), AUTO, "mmproj-", best=True)
    return found if "snapshots" in path.parts[:-2] or Path(found).parent == path.parent else ""


def held_bytes(ref: str | Path | None) -> int:
    """The memory the projector file ``ref`` takes loaded; 0 for none on this machine."""
    path = home.expand(str(ref)) if ref else None
    return int(path.stat().st_size * MMPROJ_FACTOR) if path is not None and path.is_file() else 0


def _asked(spec: ServerSpec) -> str:
    return str(spec.mmproj or "").lower()


def reuses(spec: ServerSpec) -> bool:
    """Whether ``spec`` asks for prompt reuse."""
    return bool(spec.cache_reuse) or "--cache-reuse" in spec.extra_args


def _open(spec: ServerSpec) -> bool:
    """Whether ``spec`` leaves the projector to the default: it named none, declined none, and serves a llama.cpp model that generates."""
    return spec.vision and not spec.mmproj and not spec.embedding and not spec.reranking and not is_mlx(spec.model)


def counted(spec: ServerSpec) -> bool:
    """Whether a context fitted for ``spec`` leaves room for a projector."""
    return _asked(spec) not in ("", NONE) or (_open(spec) and not reuses(spec))


def _spare_mb(records: Mapping[int, Mapping[str, Any]], besides: int) -> int | None:
    """The MiB of this machine's room that no server is admitted against and no other default projector holds; None when the room is unknown."""
    from poolhouse.serve import admission

    room, budget = hub.room() // admission.MIB, admission.budget_mb()
    if not room or not budget:
        return None
    held = sum(int(entry.get("spare_mb") or 0) for port, entry in records.items() if port != besides and admission.live(entry))
    return max(0, room - budget - held)


def chosen(spec: ServerSpec, records: Mapping[int, Mapping[str, Any]] | None = None) -> Chosen:
    """``spec`` with its projector answered for a start beside the servers in ``records``. A projector asked for is kept; the default one is added
    only from memory no server is admitted against, so no server that fits without it is turned away by it."""
    asked = _asked(spec)
    if asked == NONE:
        return Chosen(replace(spec, mmproj=None, vision=False))
    if asked == AUTO:
        if str(spec.model).startswith("hf:"):
            return Chosen(spec)   # answered once the model is on this machine
        if not (found := shipped(spec.model)):
            return Chosen(replace(spec, mmproj=None), note="no vision projector is shipped with the model, so the server reads no pictures")
        spec = replace(spec, mmproj=found)
    if spec.mmproj:
        return Chosen(spec, note=REUSE_OFF if reuses(spec) else "")
    if not _open(spec) or not (found := shipped(spec.model)):
        return Chosen(spec)
    leaving = f"serving without the vision projector {Path(found).name}, so it reads no pictures"
    if reuses(spec):
        return Chosen(replace(spec, vision=False), note=f"{leaving}: this lease asks for prompt reuse, which a projector switches off; {ASK}")
    spare, need = _spare_mb(records or {}, spec.port), -(-held_bytes(found) // (1 << 20))
    if spare is not None and need > spare:
        return Chosen(replace(spec, vision=False), note=(
            f"{leaving}: it takes {need} MiB and {spare} MiB is left beside the memory servers are admitted against; {ASK} to count it as "
            "this server's own"))
    return Chosen(replace(spec, mmproj=found), picked=True)


def counted_as(asked: ServerSpec, seen: Chosen, spare: bool) -> ServerSpec:
    """The spec a start is admitted and compared by: without a projector held beside its estimate (picked by default, or ``spare`` on the server
    being relaunched), else the one it starts; ``asked`` when it starts none."""
    if not seen.spec.mmproj:
        return asked
    return replace(seen.spec, mmproj=None) if seen.picked or spare else seen.spec


def on_disk(spec: ServerSpec, *, asked: str | Path, fetch: Callable[[str | Path, str], str]) -> ServerSpec:
    """``spec`` as a backend starts it, its projector a file here. `Fetched` when this start has just fetched the model ``asked`` named and a
    projector is still to be chosen for it."""
    if spec.model != asked and (_asked(spec) == AUTO or _open(spec)) and shipped(spec.model):
        raise Fetched(spec.model)
    if _asked(spec) in (AUTO, NONE):
        spec = replace(spec, mmproj=None)
    return replace(spec, mmproj=fetch(spec.mmproj, "projector")) if spec.mmproj else spec


def kept(spec: ServerSpec, entry: Mapping[str, Any]) -> ServerSpec:
    """``spec`` with the projector as the server recorded as ``entry`` runs it, for a relaunch of that server: its conversations may hold
    pictures, so one it runs is never left out and one it does not run is never added."""
    if not entry.get("mmproj"):
        return replace(spec, mmproj=None, vision=False)
    return replace(spec, mmproj=entry.get("projector") or spec.mmproj, vision=True, cache_reuse=None)


def unlike(spec: ServerSpec, entry: Mapping[str, Any]) -> list[str]:
    """How the pictures ``spec`` asks for differ from the server recorded as ``entry``; empty when it can share it."""
    asked, held = _asked(spec), bool(entry.get("mmproj"))
    if held and (asked == NONE or not spec.vision):
        return ["mmproj: none was asked for and the server runs a vision projector"]
    if not held and asked not in ("", NONE) and (asked != AUTO or shipped(spec.model)):
        return ["mmproj: a vision projector was asked for and the server runs none"]
    return []


def without(spec: ServerSpec, entry: Mapping[str, Any] | None, *, adopted: bool) -> str:
    """The line saying the server recorded as ``entry`` reads no pictures although the model ships a projector ``spec`` did not decline; '' otherwise."""
    if not entry or entry.get("mmproj") or not _open(spec) or not (found := shipped(spec.model)):
        return ""
    if reuses(spec):
        how = f"this lease asks for prompt reuse, which a projector switches off; {ASK}"
    elif adopted:
        how = f"it was already up that way and is left running; {ASK} for a server of your own"
    else:
        how = f"the projector was not loaded with it; {ASK}"
    return f"this server runs without the vision projector {Path(found).name} shipped with the model, so it reads no pictures: {how}"


def said(spec: ServerSpec, entry: Mapping[str, Any] | None, *, adopted: bool) -> str:
    """What `up` says about pictures for the server recorded as ``entry``; '' for a model that reads none."""
    seeing = (entry.get("projector") if entry.get("mmproj") else "") if entry else spec.mmproj
    if seeing:
        return f"reading pictures with {Path(str(seeing)).name}" + (f"; {REUSE_OFF}" if reuses(spec) else "")
    return without(spec, entry, adopted=adopted)
