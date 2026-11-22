"""What becomes of a llama-server that is not the one that was pinned. Nothing here holds a binary or asks a person.

A binary that changed since its pin is re-pinned and used when something vouches for the new bytes: the digest in a
``BUILD.json`` Poolhouse's own build wrote beside it, a code signature that chains to Apple (macOS), or a package
manager's own location (Homebrew's Cellar, owned by this user and writable by nobody else). A binary nobody vouches
for is never run: the newest earlier good build replaces it, and with none kept, a good one is fetched
(`poolhouse.serve.build_recover`) and pinned. Only when that also fails does `vet` raise `Untrusted`, naming what
was tried. Every step is logged. The pin store and the places builds live are the caller's.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Protocol

from poolhouse.files import read_json, sha256_file

__all__ = ["PACKAGE_PREFIXES", "Pins", "Untrusted", "good_build", "recorded", "vet", "vouched"]

logger = logging.getLogger(__name__)

PACKAGE_PREFIXES = ("/opt/homebrew/Cellar", "/usr/local/Cellar", "/home/linuxbrew/.linuxbrew/Cellar")
"""Where a package manager installs what it fetched; a binary whose real path is under one, owned by this user and
writable by no one else, is the manager's own."""

CODESIGN = "/usr/bin/codesign"
SIGNED_BY_APPLE = "-R=anchor apple generic"
"""The requirement a signature must satisfy: Apple's own or a Developer ID. An ad-hoc signature, which any
compiler run produces and any attacker can make, does not satisfy it."""


class Untrusted(RuntimeError):
    """A changed binary nobody vouches for, no earlier build to use instead, and no good build could be fetched."""


class Pins(Protocol):
    """What `vet` asks of the pin store it is given."""

    def watching(self) -> bool:
        """False when the owner switched the watch off: nothing is judged."""

    def pinned(self, binary: Path) -> str:
        """The digest recorded for ``binary``, or an empty string."""

    def pin(self, binary: Path, sha256: str, *, source: str, previous: str = "") -> None:
        """Record ``sha256`` as the digest of ``binary``."""


def recorded(binary: Path) -> str:
    """The digest the ``BUILD.json`` beside ``binary`` says the build was installed with, or ''."""
    info = read_json(binary.parent / "BUILD.json", {})
    return str(info.get("sha256") or "").lower() if isinstance(info, Mapping) else ""


def signed(binary: Path) -> bool:
    """Whether ``binary`` carries a signature that verifies and chains to Apple (macOS only)."""
    if sys.platform != "darwin":
        return False
    try:
        done = subprocess.run([CODESIGN, "--verify", "--strict", SIGNED_BY_APPLE, str(binary)],
                              capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def packaged(binary: Path) -> bool:
    """Whether ``binary`` is a package manager's own file: under a Cellar, owned by this user, writable by no one else."""
    real = Path(os.path.realpath(binary))
    if not hasattr(os, "getuid") or not any(real.is_relative_to(os.path.realpath(prefix)) for prefix in PACKAGE_PREFIXES):
        return False
    try:
        info = real.stat()
    except OSError:
        return False
    return info.st_uid == os.getuid() and not info.st_mode & 0o022


def vouched(binary: Path, digest: str) -> str:
    """How the bytes with ``digest`` at ``binary`` earned trust: ``build``, ``signed`` or ``package``; '' when nothing vouches."""
    if recorded(binary) == digest:
        return "build"
    if signed(binary):
        return "signed"
    return "package" if packaged(binary) else ""


def good_build(binary: Path, pinned: str = "") -> bool:
    """Whether ``binary`` is a build Poolhouse can name: it hashes to what its ``BUILD.json`` (or, with none, its pin) says."""
    if not binary.is_file():
        return False
    digest = sha256_file(binary)
    return digest == (recorded(binary) or pinned) and pinned in ("", digest)


def vet(binary: Path, policy: Pins, *, previous: Callable[[], Iterable[Path]], recover: Callable[[], Path]) -> Path:
    """The binary to run in place of ``binary``: itself, re-pinned or pinned for the first time, or the build that replaces it.

    ``previous`` lists the kept builds, newest first; ``recover`` fetches a good build and returns its binary.
    `Untrusted` only when ``binary`` changed, nothing vouches for it and recovery failed.
    """
    if not policy.watching():
        return binary
    digest = sha256_file(binary) if binary.is_file() else ""
    pinned, claimed = policy.pinned(binary), recorded(binary)
    if digest and digest == pinned:
        return binary
    if digest and not pinned and not claimed:   # nothing was ever said of this binary: it is what it is from now on
        logger.warning("TRUST ON FIRST USE: %s has no pin and no BUILD.json; %s is now what it must be", binary, digest)
        policy.pin(binary, digest, source="first-use")
        return binary
    if source := (vouched(binary, digest) if digest else ""):
        if pinned:
            logger.warning("RE-PINNED: %s changed from %s to %s (%s vouches for it)", binary, pinned, digest, source)
        policy.pin(binary, digest, source=source, previous=pinned)
        return binary
    return _replacement(binary, digest, policy, previous, recover)


def _replacement(binary: Path, digest: str, policy: Pins, previous: Callable[[], Iterable[Path]],
                 recover: Callable[[], Path]) -> Path:
    now = f"changed to {digest}" if digest else "is gone"
    logger.warning("NOT TRUSTED: %s %s and no BUILD.json, signature or package manager vouches for it", binary, now)
    for kept in previous():
        if good_build(kept, policy.pinned(kept)):
            logger.warning("FELL BACK to %s, the newest earlier good build, instead of %s", kept, binary)
            return kept
    logger.warning("no earlier good build is kept; fetching one to use instead of %s", binary)
    try:
        fetched = recover()
    except (OSError, RuntimeError, ValueError) as exc:   # a refused host, a failed download, a build or check that did not pass
        raise Untrusted(f"{binary} {now} and nothing vouches for it, no earlier good build is kept, and fetching "
                        f"a good one (as `poolhouse-serve build` does) failed: {exc}") from exc
    policy.pin(fetched, sha256_file(fetched), source="recovered")
    logger.warning("RECOVERED: %s fetched and pinned in place of %s", fetched, binary)
    return fetched
