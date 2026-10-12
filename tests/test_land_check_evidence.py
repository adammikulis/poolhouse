"""The gate runs every check, and records nothing, when the activity log's key cannot be read from a checkout."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import land_check
import pytest

from poolhouse.keystore import KeystoreUnavailable
from poolhouse.real_machine import Refused


def _check(tmp_path, code: int) -> land_check.Check:
    return land_check.Check("probe", "land:probe", [sys.executable, "-c", f"raise SystemExit({code})"])


def _locked(*_a, **_k):
    raise KeystoreUnavailable("this computer's own keystore is not used here: this copy is not installed")


@pytest.mark.parametrize("error", [KeystoreUnavailable("x"), Refused("x")])
def test_a_check_still_runs_and_passes_when_the_activity_log_cannot_be_read(tmp_path, monkeypatch, error):
    def locked(*_a, **_k):
        raise error

    monkeypatch.setattr(land_check, "evidence", locked)
    monkeypatch.setattr(land_check, "record_run", locked)
    got = land_check.execute(tmp_path, _check(tmp_path, 0), "treehash")
    assert got.status == "pass"


def test_a_failing_check_still_fails_when_the_activity_log_cannot_be_read(tmp_path, monkeypatch):
    monkeypatch.setattr(land_check, "evidence", _locked)
    monkeypatch.setattr(land_check, "record_run", _locked)
    got = land_check.execute(tmp_path, _check(tmp_path, 3), "treehash")
    assert got.status == "fail"


def test_an_earlier_pass_is_still_reused_when_the_log_can_be_read(tmp_path, monkeypatch):
    class Seen:
        id = "e-1"

    monkeypatch.setattr(land_check, "evidence", lambda tree, tier: Seen())
    got = land_check.execute(tmp_path, _check(tmp_path, 3), "treehash")
    assert got.status == "reused" and "e-1" in got.detail
