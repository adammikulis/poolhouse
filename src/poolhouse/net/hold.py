"""Where downloads are staged, and what becomes of one that failed a check: it is deleted, never kept for a person."""

from __future__ import annotations

from pathlib import Path

from poolhouse import home

__all__ = ["discard", "staging_dir"]


def staging_dir() -> Path:
    """The private directory downloads are written to before they are checked (mode 0700)."""
    where = home.state("net", "staging")
    where.mkdir(parents=True, exist_ok=True, mode=0o700)
    where.chmod(0o700)
    return where


def discard(path: Path) -> None:
    """Delete the staged file ``path`` that failed a check; the failure is the caller's error, and nothing is kept
    that a person would have to release."""
    path.unlink(missing_ok=True)
