"""The suite clears agent markers for every test, and the person-only floor still refuses a marker a test sets."""
from __future__ import annotations

import os

import pytest
from testagentenv_pytest import AGENT_VARIABLES

from poolhouse.person import HumanRequired, require_person


def test_no_agent_marker_reaches_a_test_by_default():
    assert [name for name in AGENT_VARIABLES if os.environ.get(name)] == []


def test_the_floor_passes_a_person_at_a_terminal_without_a_marker():
    assert require_person("grant", (True, True)) is None


@pytest.mark.parametrize("marker", ["CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE"])
def test_the_floor_still_refuses_a_marker_the_test_sets(monkeypatch, marker):
    monkeypatch.setenv(marker, "1")
    with pytest.raises(HumanRequired, match=marker):
        require_person("grant", (True, True))
