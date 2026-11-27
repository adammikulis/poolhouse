"""A good llama.cpp build made when one is needed and made `current`: on a device that has none (`ensure`), and as the recovery
of last resort for a binary nothing vouches for (`fetch`)."""

from __future__ import annotations

import argparse
import contextlib
import logging
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

from poolhouse import provision
from poolhouse.lock import only_one
from poolhouse.log import warn
from poolhouse.serve.binary import managed_current, managed_root
from poolhouse.serve.build_paths import BuildFailed
from poolhouse.serve.build_platform import can_build_from_source, server_name
from poolhouse.serve.build_release import build_from_release
from poolhouse.serve.build_source import build_from_source
from poolhouse.serve.build_verify import verify_and_switch

__all__ = ["ensure", "fetch"]

logger = logging.getLogger(__name__)

WAIT_S = 1800.0
"""How long a second process waits for the one already fetching before it fetches too."""


@contextlib.contextmanager
def _one_at_a_time() -> Iterator[None]:
    """The one process making a build; the others wait for it. What the build says goes to standard error, so that a
    command whose standard output is read by a program keeps it clean."""
    with only_one(managed_root() / "recover.lock", wait=True, timeout=WAIT_S, announce=warn, note="fetching a build"), \
            contextlib.redirect_stdout(sys.stderr):
        yield


def _make(*, force: bool) -> Path:
    """The binary of a good build made now and switched to as `current`, the way `poolhouse-serve build` makes one: from
    source when this machine has a compiler, else from the newest release, and from the other when that fails. ``force``
    builds afresh over a copy already in ``builds/``. `BuildFailed` names every attempt."""
    tries: list[tuple[str, Callable[[argparse.Namespace], tuple[Path, str]], argparse.Namespace]] = []
    if can_build_from_source():
        tries.append(("from source", build_from_source, argparse.Namespace(
            source="", commit="", upstream_head=False, force=force, jobs=0)))
    tries.append(("from the newest release", build_from_release, argparse.Namespace(force=force)))
    failed = []
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


def fetch() -> Path:
    """A good build in the place of one that was rejected: the copy already in ``builds/`` is that one, so it is built afresh."""
    with _one_at_a_time():
        return _make(force=True)


def ensure() -> Path:
    """The `current` build, made first on a device that has none; a build another process made while this one waited is
    taken as it is, and a finished build in ``builds/`` is switched to rather than made again."""
    present = managed_current() / server_name()
    if present.is_file():
        return present
    if refused := provision.download_refusal():
        raise BuildFailed(f"llama.cpp was not built or downloaded: {refused}")
    with _one_at_a_time():
        if present.is_file():
            return present
        warn("llama.cpp is needed for the first time: building or downloading it once")
        return _make(force=False)
