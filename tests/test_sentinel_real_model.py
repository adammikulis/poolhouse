"""Canaries and integrity against real GGUF files and a real llama-server, one server at a time.

Runs with ``--real-model`` (tests/real_model.py): two different pinned models, the machine's llama-server and no other
llama-server running. A model or binary that is missing fails the test and names the fetch command; the files in the
Hugging Face cache are only read, never changed.
"""

from __future__ import annotations

import shutil
import time

import pytest

from poolhouse.sentinel import Mode, Sentinel, State, canary

pytest_plugins = ["real_model"]

pytestmark = [pytest.mark.slow, pytest.mark.real_model, pytest.mark.timeout(300)]


def asker(served):
    """``ask(prompt)`` against one leased server."""
    client = served.client()

    def ask(prompt: str) -> str:
        reply = client.chat([{"role": "user", "content": prompt}], max_tokens=64, temperature=0.0)
        return reply.content or ""

    return ask


def test_a_real_model_passes_its_own_baseline_and_a_different_model_drifts(real, tmp_path):
    node = Sentinel(tmp_path / "s", mode=Mode.GUARDED, roots=[tmp_path])
    with real.serving("vision", context=512, mmproj=None) as served:
        ask = asker(served)
        baseline = node.baseline("real", ask, runs=3)
        again = canary.run(ask, runs=3)
        assert not canary.compare(baseline, again).drifted
        assert node.canary("real", ask, runs=3) is None
        shared = {i: baseline.passes[i] for i in baseline.runs}
        assert sum(shared.values()) >= 0.9 * sum(baseline.runs.values())
    time.sleep(3)
    with real.serving("other", context=512) as served:
        other = canary.run(asker(served), runs=3)
    drift = canary.compare(baseline, other)
    print(f"baseline passes {shared}; other passes {other.passes}; "
          f"drifted={drift.drifted} probes={drift.probes}")
    assert drift.drifted


def test_a_real_gguf_is_hashed_pinned_and_a_single_flipped_byte_is_caught(real, tmp_path):
    source = real.model("other")
    copy = tmp_path / "models" / source.name
    copy.parent.mkdir()
    shutil.copyfile(source.resolve(), copy)
    node = Sentinel(tmp_path / "s", roots=[tmp_path / "models"])
    started = time.perf_counter()
    node.manifest.pin(copy, "model", f"copy of {source.name}")
    seconds = time.perf_counter() - started
    size = copy.stat().st_size
    print(f"pinned {size / 1e6:.0f} MB in {seconds:.2f}s ({size / 1e6 / seconds:.0f} MB/s)")
    assert node.verify_before_load(copy)
    with copy.open("r+b") as handle:
        handle.seek(size // 2)
        byte = handle.read(1)
        handle.seek(size // 2)
        handle.write(bytes([byte[0] ^ 1]))
    assert node.verify_before_load(copy) is False
    assert node.store.state_of("model", str(copy)) == State.QUARANTINED
    assert not copy.exists()
    assert source.resolve().exists()
