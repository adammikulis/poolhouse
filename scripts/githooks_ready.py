"""Compatibility for old test callers; Git hook installation is explicit."""

from pathlib import Path


def ensure(repo: Path) -> None:
    """Leave hooks and configuration unchanged, including when called by older test code."""
