"""Landing heartbeat ownership and current attempt binding."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import land_board

from poolhouse.board.client import Denied


@pytest.fixture
def active(tmp_path, monkeypatch):
    claims = [SimpleNamespace(kind=kind, key=key, owner=owner, lease=str(n)) for n, (kind, key, owner) in enumerate([
        ("branch", "dev", "own"), ("worktree", str(tmp_path), "own"),
        ("branch", "land/current", "own"), ("worktree", str(tmp_path / "integration"), "own"),
        ("area", "unrelated", "own"), ("branch", "land/current", "foreign")])]
    renewed = []
    session = SimpleNamespace(name="cached-label", whoami=lambda: SimpleNamespace(name="own"),
                              claims=lambda: claims, call=lambda verb, **args: renewed.append((verb, args)))
    monkeypatch.setattr(land_board.recover, "RunnerLock", lambda root: SimpleNamespace())
    runner = land_board.Runner(session, tmp_path, target="dev")
    runner.active_run_id = "fresh"
    state = {"run_id": "fresh", "target": "dev", "integration": "land/current",
             "worktree": str(tmp_path / "integration"), "source_shas": [{"branch": "one", "sha": "a" * 40}]}
    monkeypatch.setattr(land_board.land_finish, "load", lambda root: state)
    monkeypatch.setattr(land_board.lg, "tree_of", lambda root, branch: SimpleNamespace(path=tmp_path / "integration"))
    batch = [{"branch": "one", "sha": "a" * 40}, {"branch": "covered", "sha": "b" * 40}]
    return runner, state, claims, renewed, batch


def test_current_planned_subset_renews_only_exact_own_leases(active):
    runner, _, _, renewed, batch = active
    runner.heartbeat(batch)
    assert [args["id"] for _, args in renewed] == ["0", "1", "2", "3"]
    assert {verb for verb, _ in renewed} == {"lease_renew"}


@pytest.mark.parametrize("change", [{"run_id": "old"}, {"run_id": ""}, {"target": "main"},
    {"worktree": "/foreign"}, {"source_shas": None}, {"source_shas": [{}]},
    {"source_shas": [{"branch": "old", "sha": "a" * 40}]},
    {"source_shas": [{"branch": "one", "sha": "a" * 40}] * 2}])
def test_stale_or_malformed_state_preserves_only_primary_heartbeat(active, change):
    runner, state, _, renewed, batch = active
    state.update(change)
    runner.heartbeat(batch)
    assert [args["id"] for _, args in renewed] == ["0", "1"]


def test_malformed_json_skips_integration(active, monkeypatch):
    runner, _, _, renewed, batch = active
    def broken(root):
        raise ValueError("malformed")
    monkeypatch.setattr(land_board.land_finish, "load", broken)
    runner.heartbeat(batch)
    assert [args["id"] for _, args in renewed] == ["0", "1"]


def test_expired_target_is_not_recreated(active):
    runner, _, claims, renewed, batch = active
    claims.pop(0)
    with pytest.raises(Denied, match="expired"):
        runner.heartbeat(batch)
    assert not renewed


def test_renew_denial_is_not_replaced_by_claim(active):
    runner, _, _, renewed, batch = active
    def denied(*args, **kwargs):
        raise Denied("denied", "lease expired")
    runner.s.call = denied
    with pytest.raises(Denied, match="expired"):
        runner.heartbeat(batch)
    assert not renewed


def test_attempt_marker_is_fresh_and_cleared_after_failure(active, monkeypatch):
    runner, _, _, _, batch = active
    runner.env["POOLHOUSE_LAND_RUN_ID"] = "external"
    previous = runner.env
    def fail(rows, who):
        assert runner.active_run_id not in ("", "fresh", "external")
        assert runner.env["POOLHOUSE_LAND_RUN_ID"] == runner.active_run_id
        raise RuntimeError("child failed")
    monkeypatch.setattr(runner, "_run_batch", fail)
    with pytest.raises(RuntimeError, match="child failed"):
        runner.run_batch(batch, "own")
    assert runner.active_run_id == "" and runner.env is previous


def test_finish_keeps_current_attempt_marker_and_batch(active, monkeypatch):
    runner, state, _, _, batch = active
    calls = []
    def land(*args, batch=None):
        calls.append((args, batch, runner.active_run_id))
        state["run_id"] = runner.active_run_id
        runner.heartbeat(batch)
        return 0, {"status": "verified" if args[0] == "run" else "landed"}, ""
    monkeypatch.setattr(runner, "land", land)
    monkeypatch.setattr(runner, "report_ejected", lambda batch, summary: None)
    monkeypatch.setattr(runner, "landed", lambda batch, summary, who: {"status": "landed"})
    assert runner.run_batch(batch, "own")["status"] == "landed"
    assert [args[0] for args, _, _ in calls] == ["run", "finish"]
    assert all(rows == batch for _, rows, _ in calls)
    assert calls[0][2] == calls[1][2] != ""
    assert runner.active_run_id == ""
