"""scripts/github_status.py reads a githubstatus.com summary."""

from __future__ import annotations

import github_status
import pytest


def test_an_open_incident_naming_actions_is_an_actions_finding():
    said = github_status.read({"status": {"description": "Minor"}, "components": [],
                               "incidents": [{"name": "Delayed Actions runs", "components": []}, {"name": "Slow pushes"}]})
    assert said.actions == ("Delayed Actions runs",) and said.incidents == ("Delayed Actions runs", "Slow pushes")


def test_a_degraded_actions_component_is_an_actions_finding():
    said = github_status.read({"status": {}, "incidents": [], "components": [{"name": "Actions", "status": "degraded_performance"}]})
    assert said.actions == ("Actions degraded performance",)


def test_an_operational_page_has_no_actions_finding():
    said = github_status.read({"status": {"description": "All Systems Operational"}, "incidents": [],
                               "components": [{"name": "Actions", "status": "operational"}]})
    assert said.actions == () and said.line() == "github reports: All Systems Operational"


def test_a_page_that_is_not_a_summary_is_refused():
    with pytest.raises(TypeError):
        github_status.read([])


def test_a_page_that_does_not_answer_is_a_summary_with_an_error(monkeypatch):
    monkeypatch.setenv("POOLHOUSE_GITHUB_STATUS_URL", "http://127.0.0.1:1/summary.json")
    said = github_status.fetch(timeout=1)
    assert said.actions == () and "did not answer" in said.line()
