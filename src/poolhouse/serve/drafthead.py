"""Which copy of a draft head a starting server is handed: the newest one its llama.cpp loads."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

from poolhouse import home, hub
from poolhouse.hub import hfstore, uploads
from poolhouse.serve.binary import borrows
from poolhouse.serve.preflight import read_gguf_header

__all__ = ["fitting", "loadable", "outdated"]

logger = logging.getLogger(__name__)


def outdated(head: str | Path) -> str:
    """Why mainline llama.cpp cannot load ``head``, from its header, or ''.

    The draft block of a ``qwen4exp`` head is a sparse (QSA) attention layer to mainline. A head
    exported before that gives the block a compress ratio of 0, and llama-server aborts while it
    creates the draft context (``GGML_ASSERT`` under ``set_input_k_idxs``), on any build since
    mainline gained this head's graph.
    """
    path = home.expand(head)
    try:
        got = read_gguf_header(path)
    except (OSError, ValueError):
        return ""
    if got.get("general.architecture") != "qwen4exp":
        return ""
    ratios = got.get("qwen4exp.attention.compress_ratios")
    blocks = got.get("qwen4exp.block_count")
    ahead = int(got.get("qwen4exp.nextn_predict_layers") or 0)
    if not ahead or not isinstance(ratios, list) or len(ratios) != blocks:
        return ""
    if not any(ratios[:-ahead]) or any(ratios[-ahead:]):
        return ""
    return (f"{path.name} is an export mainline llama.cpp cannot load "
            "(its draft block has compress ratio 0)")


def fitting(spec: Any, binary: str | Path | None) -> Any:
    """``spec`` (a ``ServerSpec``) with its draft head replaced by what `loadable` answers for it."""
    return replace(spec, draft=loadable(spec.draft, binary)) if spec.draft else spec


def loadable(head: str | Path, binary: str | Path | None) -> str:
    """What to serve for ``head`` on ``binary``: the newest copy of it that a mainline build loads.

    A head in a Hub cache that its publisher replaced (`uploads.replaced`), or that the build
    cannot load (`outdated`), is fetched as any model file is, now that a server is about to start
    with it. The copy a running server holds is left on disk. Where nothing newer can be had or
    the newer one does not load, the held copy is served. An ``hf:`` reference, a head outside a
    Hub cache and a fork build's heads are served as asked.
    """
    text = str(head)
    if text.startswith("hf:") or borrows(binary) or hub.borrowed_head(Path(text).name):
        return text
    found = hfstore.identify(home.expand(text))
    if found is None or not found[2]:
        return text
    repo, _commit, file = found
    snapshot = hub.held_snapshot(repo)
    main = snapshot / file if snapshot is not None else None
    held = str(main) if main is not None and main.is_file() and not outdated(main) else text
    why = outdated(held)
    if why or uploads.replaced(Path(held)):
        why = why or f"{repo} replaced {file}"
        try:
            got = str(hub.fetch(f"hf:{repo}/{file}"))
        except (ValueError, OSError, RuntimeError) as exc:
            logger.warning("%s, and its current copy could not be fetched: %s", why, exc)
        else:
            if not outdated(got):
                logger.warning("%s; serving the copy the repository holds now, %s", why, got)
                return got
            logger.warning("%s, and the copy the repository holds now does not load either", why)
    if held != text:
        logger.warning("serving the newer copy of %s held here, %s", Path(text).name, held)
    return held
