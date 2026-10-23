"""A server spec that names no context gets the largest one this machine holds safely.

`ServerSpec.context` of 0 means "fit": every entry that leases a server (`poolhouse-serve up`,
`serve()`, `public.up`, the broker's `lease`) answers it here, before anything reads the
number, so no caller carries a context of its own. A context given is never touched.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from poolhouse.hub import installed_for
from poolhouse.hub.probe import MachineMemory
from poolhouse.serve import estimate as est
from poolhouse.serve.backend import ServerSpec
from poolhouse.serve.suggest import Suggestion, largest_fit
from poolhouse.units import human_bytes

__all__ = ["fit_note", "fitted", "reusing_installed"]


def reusing_installed(spec: ServerSpec) -> ServerSpec:
    """``spec`` with each ``hf:owner/repo/file`` model, projector and draft that is already
    installed -- from the Hub cache, llama.cpp's cache, LM Studio or Ollama -- replaced by
    the file, so it is not downloaded again."""
    changes: dict[str, str] = {}
    for field in ("model", "mmproj", "draft"):
        value = getattr(spec, field)
        found = installed_for(value) if isinstance(value, str) and value.startswith("hf:") else None
        if found:
            changes[field] = str(found.path)
    return replace(spec, **changes) if changes else spec


def fitted(spec: ServerSpec, machine: MachineMemory | None = None) -> tuple[ServerSpec, str]:
    """``spec`` with its context answered, and one line saying how (empty when it was given).

    The context is per slot times the slots asked for (`ServerSpec.parallel`, one unless the
    caller said more), at the KV cache type the spec names. No number stands in for one that
    cannot be fitted: a model with no GGUF header, or a header with no ``context_length``,
    raises ValueError naming what is missing, and the caller passes a context.
    """
    if int(spec.context) > 0:
        return spec, ""
    slots = max(1, int(spec.parallel or 1))
    kv = f"{spec.cache_type_k or est.DEFAULT_KV}/{spec.cache_type_v or est.DEFAULT_KV}"
    path = Path(str(spec.model))
    if not path.is_file():
        raise ValueError(f"no context to fit for {path.name}: it is not a GGUF file on this machine "
                         "(an MLX folder or a model not yet downloaded has no header to read); "
                         "pass a context")
    head = str(spec.draft or "")
    got = largest_fit(path, machine, slots=slots, kv=kv, draft=head if Path(head).is_file() else "")
    return replace(spec, context=got.context * slots), fit_note(got, slots)


def fit_note(got: Suggestion, slots: int = 1) -> str:
    """The line that says what a fit chose: tokens a slot, the memory it needs, the rating."""
    per = f"{got.context:,} tokens a slot"
    each = f" x {slots} slots" if slots > 1 else ""
    return (f"context {per}{each}, needs {human_bytes(got.estimate.total_bytes)} "
            f"({got.verdict}): " + got.reasons[0])
