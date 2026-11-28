"""The engines a spec may name beside llama.cpp, each by the backend that serves it."""
from __future__ import annotations

from poolhouse.serve.backend import ServerBackend, ServerFailed
from poolhouse.serve.python_engines import ENGINES as PYTHON_ENGINES
from poolhouse.serve.strata.backend import StrataBackend

__all__ = ["ENGINES", "named"]

ENGINES: dict[str, ServerBackend] = {**PYTHON_ENGINES, StrataBackend.name: StrataBackend()}
"""Each engine a spec may name, by that name."""


def named(engine: str) -> ServerBackend | None:
    """The backend of the engine a spec names; None when it names none (llama.cpp, or tree decoding for MLX weights).
    `ServerFailed` for a name there is no engine of."""
    if not engine:
        return None
    if engine not in ENGINES:
        raise ServerFailed(f"no serving engine {engine!r}; there are {', '.join(sorted(ENGINES))}")
    return ENGINES[engine]
