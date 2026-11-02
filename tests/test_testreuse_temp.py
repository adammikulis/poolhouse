"""Which writes the reuse plugin counts as outside the temporary root, and the slot directory a run does not make again."""

# ruff: noqa: F811
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
import testreuse_key as keys
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
        "    os.chmod(os.path.join(root, 'node'), 0o700)\n    os.rmdir(os.path.join(root, 'node'))\n    os.rmdir(root)\n")
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


def shared_violations(*steps):
    """What the plugin records for these (event, arguments) steps taken in one test body."""
    plugin.FILES.pop("probe", None)
    plugin.MADE.clear()
    plugin.CURRENT.append("probe")
    try:
        for event, arguments in steps:
            plugin._audit(event, arguments)
        return set(plugin.FILES["probe"]["violations"])
    finally:
        plugin.CURRENT.pop()
        plugin.FILES.pop("probe", None)
        plugin.MADE.clear()


def test_a_read_or_a_listing_in_the_shared_tmp_of_what_the_run_did_not_make_is_a_violation():
    path = f"/tmp/mlforeign-{os.getpid()}.txt"
    assert shared_violations(("open", (path, "r", 0))) == {f"read {path}, in the shared /tmp and not made by this run"}
    assert shared_violations(("os.listdir", ("/tmp",))) == {"listed /tmp, in the shared /tmp and not made by this run"}


def test_what_the_run_made_in_the_shared_tmp_is_its_own_to_read_and_list():
    root = f"/tmp/mlown-{os.getpid()}"
    assert shared_violations(("os.mkdir", (root, 0o700, -1)), ("open", (f"{root}/a.txt", "w", 0)), ("open", (f"{root}/a.txt", "r", 0)),
                             ("os.listdir", (root,)), ("open", (f"{root}/made-by-a-child/b.txt", "r", 0))) == set()
    assert shared_violations(("open", (f"{root}-other/a.txt", "r", 0))) != set()


def test_a_folder_made_while_no_test_runs_is_still_the_runs_own():
    root = f"/tmp/mlsession-{os.getpid()}"
    plugin.MADE.clear()
    plugin._audit("os.mkdir", (root, 0o700, -1))
    try:
        assert Path(root).resolve() in plugin.MADE
        assert shared_violations(("open", (f"{root}/x", "r", 0))) == {f"read {root}/x, in the shared /tmp and not made by this run"}
    finally:
        plugin.MADE.clear()


@pytest.mark.parametrize("path", ["/tmpx/y", "/tmp/../etc/hosts", "/var/tmp/q", "/etc/hosts", "/Users/someone/tmp/x"])
def test_a_place_that_only_looks_like_the_shared_tmp_is_not_it(path):
    assert shared_violations(("open", (path, "r", 0))) == set()


@pytest.mark.slow
def test_a_run_that_reads_a_file_in_the_shared_tmp_it_did_not_make_is_not_stored_and_one_that_reads_its_own_is(project, tmp_path):
    descriptor, name = tempfile.mkstemp(prefix="mlforeign", dir="/tmp")
    os.close(descriptor)
    store = storage.Store(tmp_path / "store")
    (project / "tests/test_foreign.py").write_text(f"def test_it():\n    open({name!r}).read()\n")
    (project / "tests/test_own.py").write_text(
        "import os\nimport shutil\nimport tempfile\n\n\ndef test_it():\n    root = tempfile.mkdtemp(prefix='ml', dir='/tmp')\n"
        "    open(os.path.join(root, 'x'), 'w').write('1')\n    open(os.path.join(root, 'x')).read()\n    os.listdir(root)\n"
        "    os.unlink(os.path.join(root, 'x'))\n    os.rmdir(root)\n")
    try:
        report, _ = attempt(project, store, "tests/test_foreign.py", "tests/test_own.py")
    finally:
        Path(name).unlink(missing_ok=True)
    details = {outcome.file: outcome.detail for outcome in report.outcomes}
    assert f"read {name}, in the shared /tmp and not made by this run" in details["tests/test_foreign.py"]
    assert "not stored" not in details["tests/test_own.py"]
    assert [reuse_file(store, row) for row in storage.reuse.rows(store.folder)] == ["tests/test_own.py"]
    shutil.rmtree(tmp_path / "store", ignore_errors=True)


def reuse_file(store, row):
    return storage.reuse.entry(store.folder, row["id"])["file"]


def test_the_process_that_owns_a_runs_nodes_is_not_part_of_a_files_key(project, monkeypatch):
    (project / FILE).write_text("import os\n\n\ndef test_a():\n    os.environ.get('POOLHOUSE_NODE_PARENT_PID')\n    os.environ.get('POOLHOUSE_NODE_ANOTHER')\n")
    monkeypatch.setenv("POOLHOUSE_NODE_PARENT_PID", "111")
    monkeypatch.setenv("POOLHOUSE_NODE_ANOTHER", "1")
    first = keys.lookup(project, FILE, ["-q", FILE]).key
    monkeypatch.setenv("POOLHOUSE_NODE_PARENT_PID", "222")
    assert keys.lookup(project, FILE, ["-q", FILE]).key == first
    monkeypatch.setenv("POOLHOUSE_NODE_ANOTHER", "2")
    assert keys.lookup(project, FILE, ["-q", FILE]).key != first
