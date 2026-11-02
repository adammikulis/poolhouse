"""No model is ever faked in a red-team run: the model under attack is a real installed one served
through the lease, and when it is not installed the run stops and says so."""

from __future__ import annotations

import dataclasses
import importlib.util

import pytest

from poolhouse.redteam import run
from poolhouse.redteam.cli import COMMANDS
from poolhouse.redteam.run import Plan, execute
from poolhouse.redteam.scenarios import Options

pytestmark = pytest.mark.redteam


def test_the_package_ships_no_stand_in_model():
    assert importlib.util.find_spec("poolhouse.redteam.stub") is None
    assert "stub_mode" not in {f.name for f in dataclasses.fields(Plan)}


def test_a_model_that_is_not_installed_stops_the_run_and_nothing_stands_in(monkeypatch):
    monkeypatch.setattr(run, "located", lambda name: None)

    def serving(*args, **kwargs):
        pytest.fail("a run for a model that is not installed served something")

    monkeypatch.setattr(run, "serve", serving)
    with pytest.raises(SystemExit) as stopped:
        execute(Plan(("extraction",), "stub", Options(limit=1)))
    assert "stub is not an installed model" in str(stopped.value)
    assert "no other model stands in for it" in str(stopped.value)


def test_the_command_takes_no_stub_mode():
    with pytest.raises(SystemExit) as refused:
        COMMANDS.run(["run", "--stub-mode", "gullible"])
    assert refused.value.code == 2
