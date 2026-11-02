"""The track flow against the real llama.cpp: the real smoke test on this machine's llama-server, and a
real sandboxed build of a pinned upstream tag. Runs with ``--real-model`` (tests/real_model.py). Needs the
build tools, the network and the small pinned model; whichever is missing fails the test and says what to install."""

from __future__ import annotations

import pytest

from poolhouse import net
from poolhouse.serve import (
    binary,
    llamacpp_compile,
    llamacpp_smoke,
    llamacpp_state,
    llamacpp_update,
)
from poolhouse.serve.build_paths import BuildFailed, builds_dir

pytest_plugins = ["real_model"]
pytestmark = [pytest.mark.slow, pytest.mark.real_model, pytest.mark.timeout(1200),
              pytest.mark.usefixtures("serve_leases")]

PINNED_TAG = "b11379"
"""A llama.cpp build tag (its release is a prerelease cut from master); the real build test builds this one."""


def test_the_smoke_test_passes_on_this_machines_llama_server(real):
    server, model = real.binary(), real.model("chat")
    real.idle()
    got = llamacpp_smoke.run(server, model, timeout=300)
    assert got.passed, got.checks
    assert [name for name, ok, _ in got.checks] == ["lease", "health", "chat completion", "top_logprobs",
                                                    "slot save and restore"]


@pytest.mark.live_net
def test_a_pinned_upstream_tag_builds_in_the_sandbox_and_passes_the_smoke_test(real):
    """Fetches llama.cpp from GitHub, a public endpoint: it runs only when a person sets POOLHOUSE_LIVE_NET=1 and has
    approved the host, and fails, naming the approval, when they have not."""
    model = real.model("chat")
    real.idle()
    try:
        tools = llamacpp_compile.toolchain()
    except BuildFailed as exc:
        pytest.fail(f"NO TOOLCHAIN: {exc}", pytrace=False)
    try:
        got = llamacpp_update.update(PINNED_TAG, llamacpp_update.Env(tools=tools, say=print), model=model)
    except net.NeedsApproval as exc:
        pytest.fail(f"NETWORK NOT APPROVED: the build fetches llama.cpp {PINNED_TAG} from the network: {exc}",
                    pytrace=False)
    assert got.status == "built", got
    assert got.build.startswith("b11379-")
    active = llamacpp_state.active()
    assert active is not None and active.name == got.build and active.good
    assert (builds_dir() / got.build / "llama-server").is_file()
    assert binary.find_binary("llama-server") == (builds_dir() / got.build / "llama-server").resolve()
    assert "build 11379" in active.info["version"]
