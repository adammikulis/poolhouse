"""What a model server's launch asks of its surroundings: may this binary run, and what does the child see.

The pool answers both through the sentinel (`poolhouse.serve.llamacpp_trust.PoolPolicy`: pins sealed on disk,
quarantine, a person's release). The core, a caller that only wants a model served on its own machine, answers
them with `LocalPolicy`: the digest in the build's own ``BUILD.json``, a plain JSON pin file, and nothing else.
The launch code takes a policy by argument and imports neither.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from poolhouse import home, lazy
from poolhouse.files import sha256_file, write_json
from poolhouse.lock import only_one

__all__ = ["BinaryRefused", "LocalPolicy", "PinFileUnreadable", "Policy", "default"]

logger = logging.getLogger(__name__)

PIN_SCHEMA = 1


class Policy(Protocol):
    """The two decisions a launch takes from its surroundings."""

    def held(self, binary: Path) -> bool:
        """Whether ``binary`` is held back, so it is looked for even when its file is gone."""

    def problem(self, binary: Path) -> str:
        """Why ``binary`` may not be used, or an empty string."""

    def pin(self, binary: Path, sha256: str, *, source: str = "build") -> None:
        """Record ``sha256`` as the digest of ``binary``, which this machine just built."""

    def unpin(self, binary: Path) -> None:
        """Forget what was recorded for ``binary``, and release it if it was held."""

    def before_launch(self, argv: list[str]) -> None:
        """Called with the argv of a server the instant before it is spawned; raises to stop the launch."""

    def scrub_env(self, env: Mapping[str, str]) -> dict[str, str]:
        """``env`` as the child of a launch may have it."""


def default() -> Policy:
    """The pool's policy: the sentinel decides. A caller without a pool passes `LocalPolicy`."""
    return lazy.load("poolhouse.serve.llamacpp_trust", "PoolPolicy")()


def _recorded(binary: Path) -> str:
    """The digest ``BUILD.json`` beside ``binary`` says the build was installed with, or ''."""
    try:
        info = json.loads((binary.parent / "BUILD.json").read_text())
    except (OSError, ValueError):
        return ""
    return str(info.get("sha256") or "").lower() if isinstance(info, dict) else ""


class BinaryRefused(RuntimeError):
    """The binary about to be executed is not the one that was judged."""


class PinFileUnreadable(OSError):
    """The pin file is there and cannot be read, so it is not rewritten."""


class LocalPolicy:
    """Trust for a process that serves a model for itself, with no pool, no sentinel and no identity.

    A binary must hash to the digest its ``BUILD.json`` records. It is pinned in a plain JSON file the first time it is
    used (``{"schema_version": 1, "pins": {path: {"sha256", "source", "held"}}}``, read tolerantly: a key or an entry
    it does not know is ignored, and an entry that cannot be read is no pin), and from then on must hash to its pin. A
    pin that does not match, with no ``BUILD.json`` vouching for the new bytes, holds the binary until `unpin`. The
    child's environment is the one `binary.child_env` already built: nothing is quarantined here, so nothing more is
    removed.
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
        # An entry that is held stays held whatever else is wrong with it; one that is not and has no digest is no pin.
        return {str(path): {"sha256": str(one["sha256"]).lower() if isinstance(one.get("sha256"), str) else "",
                            "source": str(one.get("source") or ""), "held": bool(one.get("held")),
                            "previous": str(one.get("previous") or "")}
                for path, one in pins.items()
                if isinstance(one, dict) and (isinstance(one.get("sha256"), str) or one.get("held"))}

    def _write(self, pins: dict[str, dict[str, Any]]) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.file, {"schema_version": PIN_SCHEMA, "pins": dict(sorted(pins.items()))})
        self.file.chmod(0o600)

    def _update(self, change) -> None:
        with only_one(self.file.with_suffix(".lock"), wait=True, timeout=30.0, announce=logger.debug):
            pins = self._read()
            if pins is None:
                raise PinFileUnreadable(f"{self.file} cannot be read; it is left as it is so no hold in it is lost. "
                                        f"Move it aside once you know why.")
            change(pins)
            self._write(pins)

    def held(self, binary: Path) -> bool:
        return bool((self._read() or {}).get(str(binary), {}).get("held"))

    def pin(self, binary: Path, sha256: str, *, source: str = "build") -> None:
        self._update(lambda pins: pins.__setitem__(str(binary), {"sha256": sha256.lower(), "source": source,
                                                                 "held": False, "previous": ""}))

    def unpin(self, binary: Path) -> None:
        self._update(lambda pins: pins.pop(str(binary), None))

    def scrub_env(self, env: Mapping[str, str]) -> dict[str, str]:
        return dict(env)

    def before_launch(self, argv: list[str]) -> None:
        """Hash the binary being executed once more, the instant before it is: what passed `problem` is what runs."""
        if why := self.problem(Path(argv[0])):
            raise BinaryRefused(why)

    def problem(self, binary: Path) -> str:
        if not binary.is_file():
            return ""
        key = str(binary)
        pins = self._read()
        if pins is None:
            return (f"{self.file} is not a readable pin file; refusing {binary} until it is removed "
                    f"(the next use pins the binary again)")
        if pins.get(key, {}).get("held"):
            return f"{binary} is held: it did not match its pin; `unpin` it once you know why"
        digest = sha256_file(binary)
        recorded = _recorded(binary)
        if recorded and recorded != digest:
            return (f"{binary} is not the binary that was built: BUILD.json records another digest, so it is not "
                    f"used")
        pin = pins.get(key)
        if pin is None:
            self.pin(binary, digest, source="build" if recorded else "first-use")
            if not recorded:
                logger.warning("TRUST ON FIRST USE: %s has no pin and no BUILD.json; %s is now what it must always be",
                               binary, digest)
            return ""
        if pin["sha256"] == digest:
            return ""
        if recorded == digest:   # a rebuilt `current`: the build vouches for its own new bytes
            logger.warning("RE-PINNED: %s changed from %s to %s and BUILD.json vouches for it; if you did not rebuild it, "
                           "someone replaced both", binary, pin["sha256"], digest)
            self._update(lambda held: held.__setitem__(key, {"sha256": digest, "source": "build", "held": False,
                                                             "previous": pin["sha256"]}))
            return ""
        self._update(lambda held: held.__setitem__(key, {**pin, "held": True}))
        return f"{binary} does not match its pin and is held; `poolhouse-serve llama-cpp rollback` switches to the previous build"
