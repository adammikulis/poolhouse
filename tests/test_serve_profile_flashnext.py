"""Qwen3.8-Flash-Next UD-IQ2_XS is served by default in the settings it was measured in: `poolhouse-serve up`,
a harness start and a delegate all read the record that ships.

The records read are the shipped ones; this machine's own file, the Hub cache and the server are stood in for,
so nothing is loaded and nothing real is written."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse import harnessing
from poolhouse.serve import cli as serve_cli, holding, ops as serve_ops

pytestmark = pytest.mark.usefixtures("named_context")

MODEL = "Qwen3.8-Flash-Next-UD-IQ2_XS-00001-of-00003.gguf"
HEAD = "mtp-Qwen3.8-Flash-Next-Q4_K_M.gguf"


@pytest.fixture(autouse=True)
def _shipped_records_only(tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_PROFILES_FILE", str(tmp_path / "local" / "profiles.json"))
    monkeypatch.setattr("poolhouse.hub.located", lambda name, **k: Path("/models") / Path(name).name)


@pytest.fixture
def leases(monkeypatch):
    """`up` with nothing to start: the spec it would have leased."""
    seen: list = []

    class Manager:
        def __init__(self, backend=None, state_file=None):
            self.backend = backend

    def up(spec, *, manager, **kw):
        seen.append(spec)
        return holding.Hold(id="lease", shape="s", model=str(spec.model), context=spec.context,
                            parallel=spec.parallel, status="ready", port=1,
                            base_url="http://127.0.0.1:1", adopted=True)

    monkeypatch.setattr(serve_ops, "ServerManager", Manager)
    monkeypatch.setattr(holding, "up", up)
    return seen


def upped(root: Path, *argv: str) -> list[str]:
    return ["up", f"/models/{MODEL}", "--root", str(root / "no-fleet"), *argv]


def test_a_default_up_serves_what_was_measured(leases, tmp_path):
    assert serve_cli.main(upped(tmp_path)) == 0

    spec = leases[0]
    assert (Path(str(spec.draft)).name, spec.spec_type, spec.spec_draft_max) == (HEAD, "draft-mtp", 4)
    assert spec.extra_args == ("-ub", "2048")
    assert spec.spec_p_min is None, "no draft p-min was part of the measurement"
    assert (spec.cache_type_k, spec.cache_type_v, spec.parallel) == ("q8_0", "q8_0", 1)
    assert spec.reasoning_budget is None and spec.mmproj is None


def test_up_for_the_measured_workload_takes_the_measured_context(leases, tmp_path):
    assert serve_cli.main(upped(tmp_path, "--workload", "ask")) == 0

    assert (leases[0].context, leases[0].parallel) == (262144, 1)


@pytest.mark.parametrize("ctx", [0, 262144], ids=["fitted-context", "named-context"])
def test_a_harness_start_and_a_delegate_serve_what_was_measured(monkeypatch, ctx):
    """Both take `harnessing.config_for`; a fitted context also takes the fit's own ``-ub``, a named one the record's."""
    fit = SimpleNamespace(context=262144, verdict="green", n_gpu_layers="auto", kv_cache_type="q8_0",
                          flash_attn=True, batch=2048)
    monkeypatch.setattr(harnessing.suggest, "largest_fit", lambda *a, **k: fit)

    config = harnessing.config_for(f"/models/{MODEL}", harnessing.Want(ctx=ctx), lambda _: None)

    serving = config.serving
    assert (Path(serving.draft).name, serving.spec_type, serving.draft_n_max) == (HEAD, "draft-mtp", 4)
    assert config.talking.spec_draft_max == 4
    assert serving.extra_args == ("-ub", "2048")
    assert serving.draft_p_min is None
    assert (serving.slots, serving.slot_context, serving.cache_type, serving.build) == (1, 262144, "q8_0", "")
