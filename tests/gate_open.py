"""PLUMBING ONLY: the launch wiring (settings files, leases, sessions, turns) tested with the isolation gate open.

`harnessing.require_isolated_execution()` refuses every coding launch until the isolated launcher exists
(docs/design-isolated-launcher.md); the refusal itself is asserted with the real function in
tests/test_coding_admission.py. A test of what happens after a launch is admitted names this plugin
(``pytest_plugins = ["gate_open"]``) and uses the ``gate_open`` fixture, which swaps the function for one that admits,
so its result says nothing about isolation.
"""

from __future__ import annotations

import pytest

from poolhouse import harnessing


@pytest.fixture
def gate_open(monkeypatch) -> None:
    """Admit a coding launch for the test: the isolation gate is stood in for, never passed."""
    monkeypatch.setattr(harnessing, "require_isolated_execution", lambda: None)
