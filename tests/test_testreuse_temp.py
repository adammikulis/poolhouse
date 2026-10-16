"""Which writes the reuse plugin counts as outside the temporary root, and the slot directory a run does not make again."""

# ruff: noqa: F811
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import testreuse_plugin as plugin
import testreuse_store as storage
import testslots
from test_testreuse import FILE, project  # noqa: F401  (fixture)
from test_testreuse_review import attempt, kinds


def test_the_short_temp_root_the_node_fixtures_use_is_a_temporary_place():
    assert plugin._write_ok("/tmp/mlabc123/node") and plugin._write_ok(str(Path("/tmp").resolve() / "mlabc123" / "node"))


@pytest.mark.parametrize("path", ["/tmpx/y", "/tmp/../etc/hosts", "/var/tmp/../../etc/hosts", "/Users/someone/.cache/dev-test-slots"])
def test_a_place_that_only_looks_like_a_temporary_root_is_not_one(path):
    assert not plugin._write_ok(path)


@pytest.mark.slow
def test_a_test_that_makes_its_short_temp_folder_under_the_literal_tmp_is_stored(project, tmp_path):
    store = storage.Store(tmp_path / "store")
    (project / FILE).write_text(
        "import os\nimport shutil\nimport tempfile\n\n\ndef test_a():\n"
        "    root = tempfile.mkdtemp(prefix='ml', dir='/tmp')\n    os.mkdir(os.path.join(root, 'node'), 0o700)\n"
        "    os.chmod(os.path.join(root, 'node'), 0o700)\n    shutil.rmtree(root)\n")
    report, _ = attempt(project, store)
    assert report.status == 0 and "pass" in kinds(store)


def test_a_slot_directory_that_is_set_is_not_made(tmp_path, monkeypatch):
    slots = tmp_path / "slots"
    monkeypatch.setenv("DEV_TEST_SLOTS_DIR", str(slots))
    testslots.pin_slots_dir()
    assert not slots.exists() and os.environ["DEV_TEST_SLOTS_DIR"] == str(slots)


def test_an_unset_slot_directory_is_the_one_under_the_home_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_TEST_SLOTS_DIR", "placeholder")
    monkeypatch.delenv("DEV_TEST_SLOTS_DIR")
    monkeypatch.setenv("HOME", str(tempfile.mkdtemp(dir=tmp_path)))
    testslots.pin_slots_dir()
    pinned = Path(os.environ["DEV_TEST_SLOTS_DIR"])
    assert pinned.name == "dev-test-slots" and pinned.parent.name == ".cache" and pinned.is_dir()
