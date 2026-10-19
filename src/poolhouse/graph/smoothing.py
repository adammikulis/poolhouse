"""A graph's vectors spread over its neighbourhood: the one place the store's vectors go through the array backend."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def smooth(graph: Mapping[str, Any], vectors: Mapping[str, Sequence[float]], *,
           hops: int = 2, backend: Any = None) -> dict[str, list[float]]:
    """Every node's vector spread over its neighbourhood, as ``{id: vector}``.

    ``hops`` rounds of ``D^-1/2 (A + I) D^-1/2`` over the graph read as undirected, each
    result scaled to unit length. A node with no vector of its own starts at zero and ends
    with what its neighbours mean; one still at zero is left out.
    """
    from poolhouse.backend import get_backend
    from poolhouse.graph.data import Graph, _to_list
    from poolhouse.graph.message import normalize_by_degree, propagate

    held = {str(i): [float(x) for x in v] for i, v in vectors.items() if v is not None}
    ids = [str(n.get("id")) for n in graph.get("nodes") or () if n.get("id") is not None]
    if not held or not ids:
        return {}
    width = len(next(iter(held.values())))
    at = {node_id: i for i, node_id in enumerate(ids)}

    src = list(range(len(ids)))                       # the I of A + I
    dst = list(range(len(ids)))
    for edge in graph.get("edges") or ():
        a, b = at.get(str(edge.get("source"))), at.get(str(edge.get("target")))
        if a is None or b is None:
            continue
        src += [a, b]
        dst += [b, a]

    backend = backend or get_backend()
    ops = backend.ops
    joined = Graph(num_nodes=len(ids), src=ops.array(src, dtype=ops.int32),
                   dst=ops.array(dst, dtype=ops.int32))
    x = ops.array([held.get(i) or [0.0] * width for i in ids], dtype=ops.float32)
    for _ in range(max(0, int(hops))):
        x = normalize_by_degree(backend, joined, x)
        x = propagate(backend, joined, x, reduce="sum")
        x = normalize_by_degree(backend, joined, x)

    out: dict[str, list[float]] = {}
    for node_id, row in zip(ids, _to_list(x), strict=True):
        size = sum(v * v for v in row) ** 0.5
        if size > 0:
            out[node_id] = [v / size for v in row]
    return out
