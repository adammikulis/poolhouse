"""The RBMK experiment against the real Strands decider: a tiny run, recorded, replayed and checked. Run with --real-model.

Nothing is committed: the run is made here, in a temporary directory, and thrown away.
"""

from __future__ import annotations

import json

import pytest

pytest_plugins = ["real_model"]
pytestmark = pytest.mark.real_model


from examples.rbmk import recorder, scenarios  # noqa: E402
from examples.rbmk.decider.model import LiveAsker  # noqa: E402
from examples.rbmk.decider.policy import ModelDecider  # noqa: E402
from examples.rbmk.runner import Runner  # noqa: E402


def test_a_tiny_real_run_records_replays_and_every_answer_is_a_complete_argmax_distribution(real):
    scn = scenarios.variant(scenarios.scenario("full_power"), key="tiny_real", ticks=3)
    with real.pointer() as model:
        asker = LiveAsker()
        asker.warm()
        res = Runner(scn, ModelDecider(asker, scn.seed)).run()
        rec = recorder.recording_of(res, scn, {"mode": "recorded", "model": asker.model}, {})
    assert asker.model == model and res.decisions
    for call in (c for d in res.decisions for c in d["calls"]):
        for name, answer in call["answers"].items():
            assert sum(answer["scores"].values()) == pytest.approx(1.0, abs=1e-6), name
            assert answer["choice"] == max(answer["scores"], key=answer["scores"].get) and answer["tokens"] > 0
            assert list(answer["scores"]) == list(answer["options"]) and not answer["error"]
    again = recorder.replay(json.loads(json.dumps(rec)), scn)
    assert recorder.digest(again) == rec["digest"]
