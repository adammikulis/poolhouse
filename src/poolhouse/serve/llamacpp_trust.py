"""What sentinel pins for a managed llama-server, and whether a kept build still is the one that was pinned."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from poolhouse import sentinel
from poolhouse.serve.binary_trust import good_build

__all__ = ["PoolPolicy", "pin", "unpin", "verified"]


def pin(binary: Path, sha256: str, *, source: str = "build") -> None:
    """Record ``sha256`` as the digest of ``binary``, a binary this machine just built."""
    node = sentinel.default()
    node.manifest.pin_verified(binary, "binary", sha256, source=source, digest_from="computed",
                               real=os.path.realpath(binary))


def unpin(binary: Path) -> None:
    sentinel.default().manifest.unpin(binary)


def verified(binary: Path) -> bool:
    """Whether ``binary`` is a build Poolhouse can name and still hashes to its pin (always, with the watch off)."""
    policy = PoolPolicy()
    return not policy.watching() or good_build(binary, policy.pinned(binary))


class PoolPolicy:
    """The pool's `poolhouse.serve.policy.Policy`: what the sentinel pins and scrubs."""

    def watching(self) -> bool:
        return sentinel.default().mode != sentinel.Mode.OFF

    def pinned(self, binary: Path) -> str:
        found = sentinel.default().manifest.pin_of(binary)
        return found.sha256 if found is not None else ""

    def pinned_real(self, binary: Path) -> str:
        found = sentinel.default().manifest.pin_of(binary)
        return found.real if found is not None else ""

    def pin(self, binary: Path, sha256: str, *, source: str, previous: str = "") -> None:
        pin(binary, sha256, source=source)

    def scrub_env(self, env: Mapping[str, str]) -> dict[str, str]:
        return sentinel.default().scrub_env(env)
