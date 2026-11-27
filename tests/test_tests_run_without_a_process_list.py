"""A sandbox lists no processes; this project's test setup, and the helpers it uses, run there anyway."""

from __future__ import annotations

import psutil
import pytest
from node_attribution import run_tree
from state_attribution import session_tree

from poolhouse.serve import process


@pytest.fixture
def unlisted(monkeypatch):
    """What a Seatbelt box answers to any listing of processes."""

    def refused(*_args, **_kwargs):
        raise PermissionError(1, "Operation not permitted (originated from sysctl() malloc 1/3)")

    monkeypatch.setattr(psutil, "pids", refused)
    monkeypatch.setattr(psutil, "process_iter", refused)
    monkeypatch.setattr(psutil.Process, "parents", refused)
    monkeypatch.setattr(psutil.Process, "children", refused)


@pytest.mark.usefixtures("unlisted")
def test_no_process_is_listed_where_the_computer_will_not_list_them():
    assert process.command_lines() == []


@pytest.mark.usefixtures("unlisted")
@pytest.mark.parametrize("tree", [session_tree, run_tree])
def test_the_test_runs_own_processes_are_this_one_where_none_can_be_listed(tree):
    assert tree() == {psutil.Process().pid}
