"""One array API over MLX and PyTorch, so model math is written once."""

from __future__ import annotations

from poolhouse.backend.determinism import SeedReport, set_seeds
from poolhouse.backend.ops import ArrayBackend, ArrayOps, bind_device
from poolhouse.backend.registry import (
    BACKENDS,
    REGISTRY_GROUP,
    BackendUnavailable,
    available,
    backends,
    detect_backend,
    get_backend,
    mlx_backend,
    register,
    torch_backend,
)
from poolhouse.backend.torch_ops import resolve_torch_device

__all__ = [
    "BACKENDS",
    "REGISTRY_GROUP",
    "ArrayBackend",
    "ArrayOps",
    "BackendUnavailable",
    "SeedReport",
    "available",
    "backends",
    "bind_device",
    "detect_backend",
    "get_backend",
    "mlx_backend",
    "register",
    "resolve_torch_device",
    "set_seeds",
    "torch_backend",
]
