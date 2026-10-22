"""The sentinel scenarios of the red-team run (issue 29) that attack code, not a model: a changed
model file and a forging member, each with and without the defences, and the committed baseline the
run is gated on. The agent attacks, which a real model decides, are in
`test_redteam_sentinel_model.py`."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from poolhouse.redteam.lab import lab
from poolhouse.redteam.report import Report, gate, scenario_table
from poolhouse.redteam.scenarios import Options, sentinel

BASELINE = Path(__file__).resolve().parents[1] / "docs" / "redteam" / "baseline-real-model-2026-10-22.json"
AGENT_ATTACKS = 6


@pytest.fixture(scope="module")
def attacked() -> Report:
    report = Report()
    sentinel.code_attacks(report, Options())
    return report


def row(report: Report, attack_class: str, attack_id: str, arm: str) -> dict:
    found = [r for r in scenario_table(report)
             if (r["attack_class"], r["attack_id"], r["arm"]) == (attack_class, attack_id, arm)]
    assert len(found) == 1, (attack_class, attack_id, arm)
    return found[0]


@pytest.mark.parametrize("how", ["flipped-byte", "truncated", "replaced-same-size"])
def test_a_changed_model_file_loads_without_sentinel_and_is_refused_with_it(attacked, how):
    assert row(attacked, "tampered-model", how, "unwatched")["succeeded"]
    got = row(attacked, "tampered-model", how, "default")
    assert not got["succeeded"] and got["detected"] and got["ttd"] == 0


@pytest.mark.parametrize("how", ["wrong-key-signatures", "replayed-nonce"])
def test_a_forging_member_is_never_accepted_and_is_blocked_within_a_known_number_of_requests(attacked, how):
    got = row(attacked, "forged-member", how, "default")
    assert not got["succeeded"] and got["blocked_by"] == "sentinel" and got["ttd"] <= 12
    assert not row(attacked, "forged-member", how, "unwatched")["detected"]


def test_the_scenario_refuses_to_run_its_agent_attacks_without_a_model():
    """No scripted stand-in answers for the model: a lab with none stops the scenario and says why."""
    with lab() as bare, pytest.raises(SystemExit, match="attacks a real model and this lab has none"):
        asyncio.run(sentinel.run(bare, Report(), Options()))


def test_the_committed_baseline_was_recorded_on_a_real_model_and_holds_every_attack():
    base = Report.load(BASELINE)
    assert base.meta["model"].endswith(".gguf"), base.meta["model"]
    assert "stub" not in (base.meta["command"] + base.meta["model"]).lower()
    mine = [a for a in base.attempts if a.target == "sentinel"]
    assert len({a.key for a in mine}) == (AGENT_ATTACKS + 3 + 2) * 2, "every attack, on both arms"
    asked = [a for a in mine if a.attack_class == "injected-page-privileged-call" and a.arm == "unwatched"]
    assert any(a.succeeded for a in asked), "the recorded model fell for an injection, so the defences were tried"


def test_the_code_attacks_pass_the_committed_baseline_gate(attacked):
    assert gate(Report.load(BASELINE), attacked, success_tolerance=0.0, ttd_tolerance=1) == []


def test_the_gate_catches_a_sentinel_that_got_worse(attacked):
    """Negative control: the same run with sentinel's detections taken away fails the gate."""
    worse = Report(attempts=[
        replace(a, detected=False, ttd=None,
                succeeded=a.succeeded or a.attack_class == "tampered-model") if a.arm == "default" else a
        for a in attacked.attempts])
    problems = gate(Report.load(BASELINE), worse, success_tolerance=0.0, ttd_tolerance=1)
    assert any("no longer detected" in p for p in problems) and any("regressed" in p for p in problems)
