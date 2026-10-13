"""The extra arguments a model server is given: only flags that tune it and name no file, URL or repository, and
none of the environment variables that make llama-server download."""

from __future__ import annotations

import re

__all__ = ["LLAMA_FLAGS", "PYTHON_ENGINE_FLAGS", "clean_env", "unlisted_flag"]

LLAMA_FLAGS = frozenset({
    "-b", "--batch-size", "-ub", "--ubatch-size", "--ubatch", "-t", "--threads", "-tb", "--threads-batch",
    "-fa", "--flash-attn", "-nkvo", "--no-kv-offload", "--mlock", "--no-mlock", "--mmap", "--no-mmap",
    "--warmup", "--no-warmup", "-cb", "--cont-batching", "-nocb", "--no-cont-batching", "-lv",
    "--log-verbosity", "-v", "--verbose", "--verbosity", "--cache-reuse", "--draft-max", "--draft-min",
    "--draft-p-min", "--spec-draft-n-max", "--spec-draft-n-min", "--spec-draft-p-min", "--spec-draft-ngl",
    "-ngl", "--n-gpu-layers", "-sm", "--split-mode", "-ts", "--tensor-split", "-mg", "--main-gpu",
    "--numa", "-np", "--parallel", "-c", "--ctx-size", "-n", "--predict", "--n-predict", "--temp",
    "--top-k", "--top-p", "--min-p", "--repeat-penalty", "-s", "--seed", "--reasoning-budget",
    "--swa-full", "-dt", "--defrag-thold", "--chat-template-kwargs", "--jinja", "--no-jinja",
    "--reasoning-format"})
"""The llama-server flags `ServerSpec.extra_args` may carry. None takes a file, a URL or a repository."""

PYTHON_ENGINE_FLAGS = frozenset({
    "--gpu-memory-utilization", "--max-num-batched-tokens", "--enforce-eager", "--swap-space", "--seed",
    "--kv-cache-dtype", "--mem-fraction-static", "--attention-backend"})
"""The same for vLLM and SGLang."""

_FLAG = re.compile(r"-{1,2}[A-Za-z]")


def unlisted_flag(argv: list[str], allowed: frozenset[str]) -> str:
    """The first argument of ``argv`` that is a flag not in ``allowed`` (read up to ``=``), or '';
    a value (``-1``, ``2048``, ``on``) is not a flag."""
    for token in argv:
        name = token.split("=", 1)[0]
        if _FLAG.match(name) and name not in allowed:
            return name
    return ""


def _downloads(name: str) -> bool:
    return name.startswith("LLAMA_ARG_") and any(word in name for word in ("HF", "URL", "DOCKER"))


def clean_env(env: dict[str, str]) -> dict[str, str]:
    """``env`` without the ``LLAMA_ARG_`` variables that name a repository, a URL or a token."""
    return {k: v for k, v in env.items() if not _downloads(k)}
