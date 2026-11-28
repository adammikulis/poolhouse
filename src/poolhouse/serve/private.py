"""A private model server: one that serves a single guest and keeps nothing of what it reads or writes.

``ServerSpec(private=NAME)`` is held to this module wherever a server is started: no log file (``--log-disable`` and its output
sent nowhere), no slot files (so no ``--slot-save-path`` and no escalation), no ``/slots``, ``/metrics`` or web page, no slot text in
answers (``LLAMA_SERVER_SLOTS_DEBUG``), no lookup file or n-gram table carried between requests, no extra flags, and no ``LLAMA_*``
variable of the caller's environment, which could turn any of those back on. The broker never shares it, never gives another lease
a server that was already up for it and never stops another server to make room for it.

What it holds is in memory only: the conversation in its slot and, up to ``cache_ram_mb``, others of the same guest. Ending the
process is how that memory is given back. Nothing here locks it against the operating system's paging or wipes it.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from poolhouse.serve.backend import ServerSpec

__all__ = ["FLAGS", "NOWHERE", "environment", "flags", "sealed", "sealed_argv"]

NOWHERE = Path(os.devnull)
"""Where a private server's output goes."""

FLAGS = ("--log-disable", "--no-slots", "--no-webui")


def sealed(spec: ServerSpec) -> ServerSpec:
    """``spec`` with everything a private server may not do taken out; an ordinary spec as it is."""
    if not spec.private:
        return spec
    if spec.engine:
        raise ValueError(f"only llama.cpp serves a private model; {spec.engine} would keep logs and caches of its own")
    ngram = spec.spec_type.startswith("ngram")
    return replace(spec, slot_save_path=None, lookup_static=None, lookup_dynamic=None, extra_args=(),
                   spec_type="" if ngram else spec.spec_type, api_key=spec.api_key)


def flags(spec: ServerSpec) -> list[str]:
    """What a private server's command line adds."""
    return list(FLAGS) if spec.private else []


def sealed_argv(argv: Sequence[str]) -> bool:
    """Whether ``argv`` is a private server's, which is given no ``--metrics``."""
    return all(flag in argv for flag in FLAGS)


def environment(env: dict[str, str], private: str) -> dict[str, str]:
    """``env``, for a private server without any variable llama.cpp reads its settings from."""
    return {name: value for name, value in env.items() if not name.upper().startswith("LLAMA_")} if private else env
