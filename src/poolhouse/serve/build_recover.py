"""The recovery of last resort for a binary nothing vouches for: build or fetch a good one and make it `current`."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from pathlib import Path

from poolhouse.lock import only_one
from poolhouse.serve.binary import managed_root
from poolhouse.serve.build_paths import BuildFailed
from poolhouse.serve.build_platform import can_build_from_source, server_name
from poolhouse.serve.build_release import build_from_release
from poolhouse.serve.build_source import build_from_source
from poolhouse.serve.build_verify import verify_and_switch

__all__ = ["fetch"]

logger = logging.getLogger(__name__)

WAIT_S = 1800.0
"""How long a second process waits for the one already fetching before it fetches too."""


def fetch() -> Path:
    """The binary of a good build made now and switched to as `current`, the way `poolhouse-serve build` makes one: from
    source when this machine has a compiler, else from the newest release, and from the other when that fails. A copy
    already in ``builds/`` is the one that was rejected, so it is built afresh. `BuildFailed` names every attempt."""
    tries: list[tuple[str, Callable[[argparse.Namespace], tuple[Path, str]], argparse.Namespace]] = []
    if can_build_from_source():
        tries.append(("from source", build_from_source, argparse.Namespace(
            source="", commit="", upstream_head=False, force=True, jobs=0)))
    tries.append(("from the newest release", build_from_release, argparse.Namespace(force=True)))
    failed = []
    with only_one(managed_root() / "recover.lock", wait=True, timeout=WAIT_S, announce=logger.info, note="fetching a build"):
        for how, build, args in tries:
            try:
                dest, commit = build(args)
                verify_and_switch(dest, commit)
            except (BuildFailed, OSError, RuntimeError, ValueError) as exc:
                logger.warning("fetching a good build %s failed: %s", how, exc)
                failed.append(f"{how}: {exc}")
                continue
            return dest / server_name()
    raise BuildFailed("; ".join(failed))
