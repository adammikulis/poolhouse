"""A missing or malformed governance response must not look like no pool."""
import pytest

from poolhouse import errors
from poolhouse.api import pool


def governed(dispute=None):
    return {"mode": "governed", "name": "home", "epoch": 1, "charter_hash": "a" * 64,
            "admins": [], "weak_keys_only": False, "thresholds": {}, "holds": {}, "pending": None,
            "dispute": dispute, "waiting_for_signers": [], "delegates": [], "refused": []}


@pytest.mark.parametrize("governance", [None, {}, [], {"mode": "unexpected"},
                                       {"mode": "unfounded"}, {"mode": "governed"},
                                       {"mode": "unfounded", "say": "none", "old_format_file": "", "admins": []},
                                       {"mode": "unfounded", "say": "  ", "old_format_file": ""},
                                       governed({"say": ""}), governed({"say": "  "})])
def test_invalid_governance_is_not_reported_as_unfounded(monkeypatch, governance):
    monkeypatch.setattr(pool._node, "call", lambda *args, **kwargs: {
        "pool": "pool", "policy": "secure", "fingerprint": "device", "governance": governance,
    })
    with pytest.raises(errors.Error):
        pool.status()


def test_explicit_unfounded_and_disputed_governance_are_distinct(monkeypatch):
    raw = {"pool": "pool", "policy": "secure", "fingerprint": "device",
           "governance": {"mode": "unfounded", "say": "No pool yet.", "old_format_file": ""}}
    monkeypatch.setattr(pool._node, "call", lambda *args, **kwargs: raw)
    assert pool.status().governance == "unfounded"
    raw["governance"] = governed({"say": "The pool is disputed."})
    status = pool.status()
    assert status.governance == "governed"
    assert status.missing == "The pool is disputed."
