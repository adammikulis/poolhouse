"""The recovery of last resort for a binary nothing vouches for: fetch the newest release and make it `current`."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from poolhouse.lock import only_one
from poolhouse.serve.binary import managed_root
from poolhouse.serve.build_platform import server_name
from poolhouse.serve.build_release import build_from_release
from poolhouse.serve.build_verify import verify_and_switch

__all__ = ["fetch"]

logger = logging.getLogger(__name__)

WAIT_S = 1800.0
"""How long a second process waits for the one already fetching before it fetches too."""


def fetch() -> Path:
    """The binary of the newest ggml-org/llama.cpp release for this machine, downloaded afresh (a copy already in
    ``builds/`` is the one that was rejected), checked to answer ``--help`` and read what the current build reads,
    and made `current`. `BuildFailed`, `net.NeedsApproval` or an OSError when it cannot be fetched."""
    with only_one(managed_root() / "recover.lock", wait=True, timeout=WAIT_S, announce=logger.info, note="fetching a build"):
        dest, commit = build_from_release(argparse.Namespace(force=True))
        verify_and_switch(dest, commit)
    return dest / server_name()
