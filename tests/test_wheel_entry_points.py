"""The entry points the built wheel declares name the page extensions and modules the wheel holds."""

import importlib.metadata
import zipfile

import pytest
import wheel_cache


@pytest.fixture
def entries(tmp_path):
    """The entry points of the built wheel, and the directory it was unpacked into."""
    wheels = list(wheel_cache.built().glob("*.whl"))
    assert len(wheels) == 1
    installed = tmp_path / "installed"
    with zipfile.ZipFile(wheels[0]) as archive:
        archive.extractall(installed)
    found = [entry for distribution in importlib.metadata.distributions(path=[str(installed)])
             for entry in distribution.entry_points]
    return found, installed


def test_the_wheel_declares_the_page_extensions_the_daemon_loads_by_name(entries):
    found, _ = entries
    declared = {entry.name: entry.value for entry in found if entry.group == "poolhouse.ui_routes"}
    assert declared["tasks"] == "poolhouse.workspace.task_routes:route"
    assert declared["board"] == "poolhouse.workspace.fleet_routes:route"


def test_every_entry_point_of_the_wheel_names_a_module_the_wheel_holds(entries):
    found, installed = entries
    own = [entry for entry in found if entry.group.startswith("poolhouse.")]
    assert own
    for entry in own:
        path = installed / entry.module.replace(".", "/")
        assert path.with_suffix(".py").is_file() or (path / "__init__.py").is_file(), entry


def test_the_wheel_declares_no_project_hosting_group(entries):
    found, _ = entries
    groups = {entry.group for entry in found}
    assert "poolhouse.workspace_hosts" not in groups
    assert "poolhouse.member_routes" not in groups
