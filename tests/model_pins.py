"""A model file pinned the way a download pins it, for a test that is about serving it."""

from __future__ import annotations

from pathlib import Path

from poolhouse import sentinel


def pinned(path: Path) -> Path:
    """``path`` with the pin a pull gives a model file."""
    sentinel.default().manifest.pin(path, "model", source="pull")
    return path
