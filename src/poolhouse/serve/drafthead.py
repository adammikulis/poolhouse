"""The copies of a draft head a mainline llama.cpp can be handed, the newest first, and why one cannot be."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from http.client import HTTPException
from pathlib import Path

from poolhouse import home, hub
from poolhouse.hub import hfstore, uploads
from poolhouse.serve.backend import ServerSpec
from poolhouse.serve.preflight import read_gguf_header

__all__ = ["copies", "outdated"]

logger = logging.getLogger(__name__)

FAILED_FETCH = (ValueError, OSError, RuntimeError, HTTPException)


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


def _fetched(reference: str, trust: hub.Trust | None = None) -> str:
    """The cached file ``reference`` names, fetched as any model file is, or '' with a warning when it cannot be had."""
    try:
        parts = ServerSpec.hf_parts(reference)
        return str(hub.fetch(reference, trust=trust) if parts and parts[1] else hub.pull(reference, trust=trust))
    except FAILED_FETCH as exc:
        logger.warning("the draft head %s could not be fetched: %s", reference, exc)
        return ""


def _held(repo: str, file: str) -> list[str]:
    """Every copy of ``file`` the Hub cache holds for ``repo``, the one ``refs/main`` names first, then the newest."""
    root = hfstore.repo_root(repo) / "snapshots"
    main = hfstore.main_commit(repo)
    found = [path for path in root.glob(f"*/{file}") if path.is_file()]
    found.sort(key=lambda path: (path.relative_to(root).parts[0] == main, path.stat().st_mtime), reverse=True)
    return [str(path) for path in found]


def _candidates(asked: str) -> Iterator[str]:
    """The files that may stand for ``asked``, before any is judged. An ``hf:`` reference is fetched, and a held copy
    of it follows. A cached file that its publisher replaced, or that cannot be loaded, is fetched as an agent's pull,
    only where the upload check may ask (`uploads.asks_about`); then the current copy, the file asked for and every
    other copy held here."""
    if asked.startswith("hf:"):
        if got := _fetched(asked):
            yield got
        parts = ServerSpec.hf_parts(asked)
        if parts and parts[1]:
            yield from _held(*parts)
        return
    found = hfstore.identify(home.expand(asked))
    if found is None or not found[2]:
        yield asked
        return
    repo, _commit, file = found
    held = _held(repo, file)
    current = Path(held[0] if held else asked)
    stale = uploads.asks_about(current) and (outdated(current) or uploads.replaced(current))
    if stale and (got := _fetched(f"hf:{repo}/{file}", hub.AGENT)):
        yield got
    yield str(current)
    yield asked
    yield from _held(repo, file)


def copies(asked: str) -> Iterator[str]:
    """The files a mainline server may be handed for the head ``asked``, best first, each once and none that
    `outdated` refuses. The caller takes the next one when a server will not start; a copy a running server holds
    stays on disk. Empty when no copy can be had."""
    seen: set[Path] = set()
    for one in _candidates(asked):
        path = home.expand(one)
        if not path.is_file() or path.resolve() in seen:
            continue
        seen.add(path.resolve())
        if why := outdated(path):
            logger.warning("%s", why)
            continue
        yield one
