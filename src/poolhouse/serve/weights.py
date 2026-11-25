"""How big a model's weights are on disk, and how long a load of them may wait."""

from __future__ import annotations

from pathlib import Path

from poolhouse.serve.tensors import resident_bytes

__all__ = ["DEFAULT_TIMEOUT_S", "resident_of", "scaled_timeout", "weight_of"]

# The flat timeout this used to be, kept as the floor: a small model that always loaded in
# ten seconds must not suddenly wait less than 300 just because it is small.
DEFAULT_TIMEOUT_S = 300.0
_GB = 1024 ** 3


def scaled_timeout(weights_bytes: int, *, base: float = DEFAULT_TIMEOUT_S) -> float:
    """A load timeout that grows with the weights, so an 87G model is not raced against a
    timeout sized for a 4G one. 60s plus 1.5s per GB of weights, or ``base`` -- whichever is
    larger. ``weights_bytes`` is 0 for an `hf:` reference not yet on disk, and 0 leaves the
    floor untouched: an unknown size is not the same as an enormous one."""
    return max(base, 60.0 + 1.5 * (weights_bytes / _GB))


def weight_of(model: str | Path) -> int:
    """Roughly what a model will take, from the weights on disk. 0 when they are not here.

    A `hf:` reference has not been downloaded yet the first time, so its size is unknown and
    unknown is not the same as enormous — it is left to the load to find out.
    """
    if isinstance(model, str) and model.startswith("hf:"):
        return 0
    where = Path(model)
    if not where.exists():
        return 0
    # a sharded model names its first file; the others sit beside it
    shards = sorted(where.parent.glob(where.name.replace("00001", "*"))) or [where]
    return sum(s.stat().st_size for s in shards if s.is_file())


def resident_of(model: str | Path) -> int:
    """What a server must hold in memory for a model's weights: `weight_of` less the gathered
    lookup table its file carries. 0 when the weights are not here.

    The figure every memory admission and fit check counts -- the lease, the wired limit, the
    largest context, the preflight -- where `weight_of` is the file on disk, which is what a
    download, a copy and a load timeout are about.
    """
    on_disk = weight_of(model)
    return resident_bytes(on_disk, model) if on_disk else 0
