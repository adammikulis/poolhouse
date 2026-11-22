"""What a model server's launch asks of its surroundings: which digest is recorded for this binary, and what does the child see.

The pool answers both through the sentinel (`poolhouse.serve.llamacpp_trust.PoolPolicy`: pins sealed on disk). The core,
a caller that only wants a model served on its own machine, answers them with `LocalPolicy`: a plain JSON pin file and
nothing else. What is done with a binary that no longer matches its pin is not the policy's to decide: it is
`poolhouse.serve.binary_trust.vet`, the same for both. The launch code takes a policy by argument and imports neither.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from poolhouse import home, lazy
from poolhouse.files import promote, write_json
from poolhouse.lock import only_one
from poolhouse.tolerant import known_only

__all__ = ["LocalPolicy", "Policy", "default"]

logger = logging.getLogger(__name__)

PIN_SCHEMA = 1
_PIN_KEYS = frozenset({"sha256", "source", "previous", "real"})


class Policy(Protocol):
    """The decisions a launch takes from its surroundings: `binary_trust.Pins`, and the child's environment."""

    def watching(self) -> bool:
        """False when the owner switched the watch off: no binary is judged."""

    def pinned(self, binary: Path) -> str:
        """The digest recorded for ``binary``, or an empty string."""

    def pinned_real(self, binary: Path) -> str:
        """The real path ``binary`` led to when it was pinned, or an empty string."""

    def pin(self, binary: Path, sha256: str, *, source: str, previous: str = "") -> None:
        """Record ``sha256`` as the digest of ``binary``, and the real path it leads to now."""

    def scrub_env(self, env: Mapping[str, str]) -> dict[str, str]:
        """``env`` as the child of a launch may have it."""


def default() -> Policy:
    """The pool's policy: the sentinel's pins. A caller without a pool passes `LocalPolicy`."""
    return lazy.load("poolhouse.serve.llamacpp_trust", "PoolPolicy")()


class LocalPolicy:
    """Pins for a process that serves a model for itself, with no pool, no sentinel and no identity.

    A binary is pinned in a plain JSON file the first time it is used (``{"schema_version": 1, "pins": {path: {"sha256",
    "source", "previous"}}}``, read tolerantly: a key or an entry it does not know is ignored, and an entry that cannot
    be read is no pin). A file that cannot be read at all is moved aside under a timestamped name and the pins start
    again. The child's environment is the one `binary.child_env` already built: nothing is quarantined here, so
    nothing more is removed.
    """

    def __init__(self, pins: Path | None = None) -> None:
        self._where = pins

    @property
    def file(self) -> Path:
        return self._where or home.state("core", "pins.json")

    def _read(self) -> dict[str, dict[str, Any]] | None:
        """The pins, ``{}`` when there is no file, ``None`` when the file is there and cannot be read."""
        try:
            text = self.file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError:
            return None
        try:
            parsed = json.loads(text)
        except ValueError:
            return None
        pins = parsed.get("pins") if isinstance(parsed, dict) else None
        if not isinstance(pins, dict):
            return None
        pins = {str(path): known_only(one, _PIN_KEYS, what="a pin-file entry")
                for path, one in pins.items() if isinstance(one, dict)}
        return {path: {"sha256": str(one["sha256"]).lower(), "source": str(one.get("source") or ""),
                       "previous": str(one.get("previous") or ""), "real": str(one.get("real") or "")}
                for path, one in pins.items() if isinstance(one.get("sha256"), str)}

    def _write(self, pins: dict[str, dict[str, Any]]) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.file, {"schema_version": PIN_SCHEMA, "pins": dict(sorted(pins.items()))})
        self.file.chmod(0o600)

    def _locked(self):
        return only_one(self.file.with_suffix(".lock"), wait=True, timeout=30.0, announce=logger.debug)

    def _readable(self) -> dict[str, dict[str, Any]]:
        """The pins; a file that cannot be read is first moved aside so there is a file to write again. Call under the lock."""
        pins = self._read()
        if pins is not None:
            return pins
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        aside = self.file.with_name(f"{self.file.name}.unreadable-{stamp}")
        promote(self.file, aside)
        logger.warning("PIN FILE REBUILT: %s could not be read; it was moved to %s and binaries are pinned again as they "
                       "are used", self.file, aside)
        return {}

    def watching(self) -> bool:
        return True

    def _entry(self, binary: Path) -> dict[str, Any]:
        pins = self._read()
        if pins is None:
            with self._locked():
                pins = self._readable()
        return pins.get(str(binary), {})

    def pinned(self, binary: Path) -> str:
        return self._entry(binary).get("sha256", "")

    def pinned_real(self, binary: Path) -> str:
        return self._entry(binary).get("real", "")

    def pin(self, binary: Path, sha256: str, *, source: str, previous: str = "") -> None:
        with self._locked():
            pins = self._readable()
            pins[str(binary)] = {"sha256": sha256.lower(), "source": source, "previous": previous,
                                 "real": os.path.realpath(binary)}
            self._write(pins)

    def scrub_env(self, env: Mapping[str, str]) -> dict[str, str]:
        return dict(env)
