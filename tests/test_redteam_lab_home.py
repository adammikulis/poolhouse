"""The red-team lab attacks with forged traffic, plants decoys and rotates keys: none of that may reach the real home."""

from __future__ import annotations

import os

from poolhouse import home
from poolhouse.redteam.lab import lab, own_home


def test_the_lab_runs_in_its_own_home_and_gives_the_old_one_back(tmp_path, monkeypatch):
    mine = tmp_path / "mine"
    monkeypatch.setenv(home.ROOT_ENV, str(mine))
    with lab() as made:
        inside = home.home()
        assert inside != mine and made.root in inside.parents
    assert home.home() == mine and os.environ[home.ROOT_ENV] == str(mine)


def test_own_home_removes_the_variable_it_set_when_there_was_none(tmp_path, monkeypatch):
    monkeypatch.delenv(home.ROOT_ENV, raising=False)
    with own_home(tmp_path / "x") as inside:
        assert os.environ[home.ROOT_ENV] == str(inside)
    assert home.ROOT_ENV not in os.environ
