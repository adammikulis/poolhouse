"""A model leased with no context loads at the largest this machine holds, and answers.

Runs with ``--real-model`` through tests/real_model.py: the pinned Qwen3-4B-Instruct (the "judge" use), the machine's
llama-server, no other llama-server running. The context asked for is 0; the server must report the fit, per slot.
"""

from __future__ import annotations

import pytest

from poolhouse.client.health import serving_params
from poolhouse.serve import largest, suggest

pytest_plugins = ["real_model"]
pytestmark = [pytest.mark.slow, pytest.mark.real_model, pytest.mark.timeout(1800)]


def test_a_lease_with_no_context_loads_the_fit_and_answers(real):
    model = real.prepared("judge")
    want = suggest.largest_fit(model)
    assert want.context >= largest.PAD and want.verdict in ("green", "yellow")
    with real.serving("judge", context=0) as served:
        params = serving_params(served.url)
        assert params is not None and params.n_ctx == want.context and params.total_slots == 1
        reply = served.client().chat([{"role": "user", "content": "Reply with the word ready."}], max_tokens=16)
        assert reply.content.strip()
    print(f"fit: {want.context:,} tokens a slot, {want.estimate.total_bytes / 2**30:.1f} GiB estimated, "
          f"KV {want.estimate.kv_cache_bytes / 2**30:.1f} GiB, {want.verdict}")
