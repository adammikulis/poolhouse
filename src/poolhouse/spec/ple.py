"""The mapped n-gram table of an MLX checkpoint (docs/serving.md, "The n-gram table").

`interleave` stores a view's table as one record a row; `ReadAhead` faults the wanted pages in
from a pool of threads before mlx-vlm gathers them.
"""

from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import mlx.nn as nn
import numpy as np
from mlx_vlm.models.qwen4_exp.ple_storage import (
    QuantizedMMapNGramEmbedding,
    materialize_interleaved_ple_store,
)

from poolhouse.files import read_json

__all__ = ["ReadAhead", "interleave", "read_ahead"]

MANIFEST = "ple-store.json"
ROWS_FILE = "ple-q4.rows"
THREADS = 16
#: rows below which one thread gathers as quickly as many
FEW = 256
#: bytes of disk left free beside a rewritten table
SPARE = 20 * 1024**3


def interleave(source: Path, view: Path) -> bool:
    """Whether ``view``'s table is stored as one record a row, rewriting it from ``source``
    when the disk has room for as many bytes again as the table plus `SPARE`."""
    manifest = read_json(view / MANIFEST, {})
    if manifest.get("layout") == "interleaved_q4":
        return True
    width, rows = int(manifest.get("row_width") or 0), int(manifest.get("row_count") or 0)
    group = int((manifest.get("quantization") or {}).get("group_size") or 0)
    if not width or not rows or not group:
        return False
    # packed 4-bit weights and at most a 16-bit scale and a 16-bit bias a group
    most = rows * (width // 2 + width // group * 4)
    (view / ROWS_FILE).unlink(missing_ok=True)  # one an interrupted rewrite left
    if shutil.disk_usage(view).free < most + SPARE:
        return False
    materialize_interleaved_ple_store(source, view / MANIFEST, data_file=ROWS_FILE)
    return True


class ReadAhead(QuantizedMMapNGramEmbedding):
    """An opened table whose gather finds its pages already read, many at a time."""

    _pool: ThreadPoolExecutor

    @classmethod
    def of(cls, table: QuantizedMMapNGramEmbedding, threads: int = THREADS) -> ReadAhead:
        """``table``'s open mappings, cache and counters, read ahead on ``threads`` threads."""
        made = cls.__new__(cls)
        made.__dict__.update(table.__dict__)
        made._pool = ThreadPoolExecutor(threads, thread_name_prefix="poolhouse-ple")
        return made

    def _read_rows(self, row_ids: Any) -> Any:
        ids = np.asarray(row_ids, dtype=np.int64)
        if ids.ndim == 1 and ids.size >= FEW:
            list(self._pool.map(self._touch, np.array_split(ids, 4 * THREADS)))
        return super()._read_rows(ids)

    def _touch(self, ids: np.ndarray) -> None:
        """Fault in the first and last byte of every row of ``ids`` this table holds."""
        if self._interleaved is not None:
            held = [(0, self.row_count, [self._interleaved])]
        else:
            held = [(start, end, list(arrays.values())) for start, end, arrays in self._shards]
        for start, end, arrays in held:
            local = ids[(ids >= start) & (ids < end)] - start
            if local.size:
                for array in arrays:
                    array[local, 0].sum()
                    array[local, -1].sum()


def read_ahead(model: nn.Module) -> int:
    """Give every mapped table in ``model`` the read-ahead gather; how many there were."""
    changed = 0
    for _, module in model.named_modules():
        table = getattr(module, "ngram_embedding", None)
        if type(table) is QuantizedMMapNGramEmbedding:
            module.ngram_embedding = ReadAhead.of(table)
            changed += 1
    return changed
