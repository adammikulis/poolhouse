"""ph.test.config and ph.test.units: a program reads a project's description and units."""
from __future__ import annotations

import pytest
from testfarm_project import SHELL_CONFIG, project, shell_project

import poolhouse as ph


def test_config_returns_the_validated_file_and_units_run_the_projects_list(tmp_path):
    root = shell_project(tmp_path, SHELL_CONFIG + '\n[label.slow]\nmatch = ["tests/t_render.sh"]\n', tests=("parse", "render", "emit"))
    config = ph.test.config(root)
    assert isinstance(config, ph.test.Config) and config.project_name == "widgets" and config.split_mode == "by name"
    found = ph.test.units(root)
    assert all(isinstance(unit, ph.test.Unit) for unit in found)
    assert [unit.name for unit in found] == ["tests/t_emit.sh", "tests/t_parse.sh", "tests/t_render.sh"]
    assert [unit.name for unit in ph.test.units(root, ["tests/t_r*"])] == ["tests/t_render.sh"]
    assert found[2].labels == ("slow",)


def test_a_wrong_or_missing_file_and_an_empty_selection_raise_error_with_one_sentence(tmp_path):
    with pytest.raises(ph.Error, match=r"poolhouse-test init"):
        ph.test.config(project(tmp_path / "none", None))
    with pytest.raises(ph.Error, match=r"'argvv' is not a key; did you mean 'argv'"):
        ph.test.config(project(tmp_path / "typo", "schema = 1\n[run]\nargvv = ['sh']\n"))
    with pytest.raises(ph.Error, match=r"matches no unit"):
        ph.test.units(shell_project(tmp_path / "ok"), ["nothing"])
