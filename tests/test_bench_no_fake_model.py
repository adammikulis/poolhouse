"""A measuring command is checked by the real smoke on the real model, never by a dry run on a
scripted one: the self-check module and its flag are gone."""

from __future__ import annotations

import importlib.util

import pytest

from poolhouse import bench


def test_there_is_no_scripted_dry_run_in_the_package():
    assert importlib.util.find_spec("poolhouse.bench.selfcheck") is None


def test_a_measuring_command_takes_no_flag_to_skip_a_dry_run(capsys):
    with pytest.raises(SystemExit) as refused:
        bench._parser().parse_args(["run", "tiny", "--no-selfcheck"])
    assert refused.value.code == 2
    assert "unrecognized arguments: --no-selfcheck" in capsys.readouterr().err
