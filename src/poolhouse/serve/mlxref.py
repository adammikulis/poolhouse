"""Whether a model reference names MLX weights, kept apart from the backend that serves them so
the cache-type rules can ask without importing it."""

from __future__ import annotations

from pathlib import Path

from poolhouse import home

__all__ = ["PREFIX", "is_mlx"]

PREFIX = "mlx:"


def is_mlx(model: str | Path) -> bool:
    """Whether ``model`` names MLX weights: ``mlx:owner/repo`` or a directory with a config."""
    text = str(model)
    if text.startswith(PREFIX):
        return True
    where = home.expand(text)
    return where.is_dir() and (where / "config.json").is_file()
