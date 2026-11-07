"""Exact reviewed zero-network node admission."""
from __future__ import annotations

import sys

import pytest
import test_kernel_isolation
from confinement import needs_confinement
from test_kernel_isolation import check_selectors
from test_kernel_selectors import TEMP_ONLY, admitted_node


def test_confined_options_validate_the_entire_invocation(monkeypatch):
    expected = object()
    monkeypatch.setattr(test_kernel_isolation.test_browser_admission, "source_plan", lambda *args: expected)
    node = "tests/test_sandbox_seatbelt.py"
    assert check_selectors([sys.executable, "-m", "pytest", node, "-p", "no:langsmith_plugin"]) is expected
    for arguments in (["-p", "no:langsmith_plugin_extra", node], [node, "-p", "foreign_plugin"]):
        with pytest.raises(RuntimeError, match="unadmitted"):
            check_selectors([sys.executable, "-m", "pytest", *arguments])


@needs_confinement
def test_reviewed_nodes_preserve_complete_invocation_validation():
    for node in TEMP_ONLY:
        assert admitted_node(node)
        check_selectors([sys.executable, "-m", "pytest", node])
        assert admitted_node(node + "[case]")
        with pytest.raises(RuntimeError, match="unadmitted"):
            check_selectors([sys.executable, "-m", "pytest", node, "-p", "foreign_plugin"])


@pytest.mark.parametrize("node", [
    "tests/test_task_coordinator.py",
    "tests/test_task_coordinator.py::test_sealed_task_creation_routes_exact_authenticated_coordinator_and_retries",
    "tests/test_task_coordinator.py::test_automatic_selection_uses_real_project_bound_board_quality",
    "tests/test_execution_profile.py::test_future_unreviewed_function",
    "tests/test_workspace_remote.py::test_remote_execution_profiles",
    "tests/test_task_native.py::test_native_task",
])
@needs_confinement
def test_unreviewed_files_and_resource_fixtures_remain_refused(node):
    assert not admitted_node(node)
    with pytest.raises(RuntimeError, match="fixture admission"):
        check_selectors([sys.executable, "-m", "pytest", node])
