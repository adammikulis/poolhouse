"""Test result reuse stores a clean pass when the run only built into `dist/` or wrote to `/tmp`, and still refuses an input that changed."""

# ruff: noqa: F811
from __future__ import annotations

import pytest
import testreuse_plugin as plugin
import testreuse_store as storage
from test_testreuse import FILE, hows, project  # noqa: F401  (fixture)
from test_testreuse_review import attempt, kinds

pytestmark = pytest.mark.slow

READS_THE_WHEEL = "from pathlib import Path\n\n\ndef test_a():\n    assert Path(__file__).parents[1].joinpath('dist/pkg.whl').read_bytes()\n"
WRITES_THE_WHEEL = (
    "from pathlib import Path\n\n\ndef test_a():\n    out = Path(__file__).parents[1] / 'dist'\n    out.mkdir(exist_ok=True)\n"
    "    (out / 'pkg.whl').write_bytes(b'w')\n    assert (out / 'pkg.whl').read_bytes()\n")
USES_TMP = (
    "import os\nimport shutil\nimport tempfile\n\n\n"
    "def test_a():\n"
    "    root = tempfile.mkdtemp(prefix='ml', dir='/tmp')\n"
    "    os.chmod(root, 0o700)\n"
    "    os.mkdir(os.path.join(root, 'node'), 0o700)\n"
    "    shutil.rmtree(root)\n")


def test_a_wheel_built_into_dist_during_the_run_does_not_move_the_tree(project, tmp_path):
    store = storage.Store(tmp_path / "store")
    (project / FILE).write_text(READS_THE_WHEEL)

    def build(real):
        def launch(command):
            (project / "dist").mkdir(exist_ok=True)
            (project / "dist/pkg.whl").write_bytes(b"built")
            (project / "dist/.built-from").write_text("digest\n")
            return real(command)
        return launch

    report, _ = attempt(project, store, launch=build)
    assert report.status == 0 and "pass" in kinds(store)


def test_a_test_that_builds_and_reads_its_own_wheel_in_dist_is_stored(project, tmp_path):
    store = storage.Store(tmp_path / "store")
    (project / FILE).write_text(WRITES_THE_WHEEL)
    report, _ = attempt(project, store)
    assert report.status == 0 and "pass" in kinds(store)


def test_a_write_to_a_directory_other_than_dist_in_the_checkout_is_still_a_violation(project, tmp_path):
    store = storage.Store(tmp_path / "store")
    (project / FILE).write_text(WRITES_THE_WHEEL.replace("'dist'", "'out'"))
    attempt(project, store)
    assert "pass" not in kinds(store)


def test_a_dist_file_a_test_read_and_the_run_then_rewrote_is_not_stored(project, tmp_path):
    store = storage.Store(tmp_path / "store")
    (project / "dist").mkdir()
    (project / "dist/pkg.whl").write_bytes(b"built")
    (project / FILE).write_text(READS_THE_WHEEL)

    def rewrite_after(real):
        def launch(command):
            status = real(command)
            (project / "dist/pkg.whl").write_bytes(b"other")
            return status
        return launch

    attempt(project, store, launch=rewrite_after)
    assert "pass" not in kinds(store)


def test_a_source_file_edited_during_the_run_still_moves_the_tree(project, tmp_path):
    store = storage.Store(tmp_path / "store")

    def edit(real):
        def launch(command):
            (project / "src/poolhouse/other.py").write_text("OTHER = 2\n")
            return real(command)
        return launch

    report, _ = attempt(project, store, launch=edit)
    assert report.status == 4 and "pass" not in kinds(store)


@pytest.mark.parametrize("path", ["/tmp/mlabc/node", "/private/tmp/mlabc/node", "/var/tmp/x"])
def test_the_shared_temporary_directories_are_writable(path):
    assert plugin._write_ok(path)


def test_a_test_that_makes_and_removes_a_directory_under_tmp_is_stored(project, tmp_path):
    store = storage.Store(tmp_path / "store")
    (project / FILE).write_text(USES_TMP)
    report, _ = attempt(project, store)
    assert report.status == 0 and "pass" in kinds(store)


LISTS_THE_ROOT = "import os\nfrom pathlib import Path\n\n\ndef test_a():\n    assert 'src' in os.listdir(Path(__file__).parents[1])\n"


def test_a_pass_that_listed_the_root_is_reused_after_a_wheel_appears_in_dist(project, tmp_path):
    store = storage.Store(tmp_path / "store")
    (project / FILE).write_text(LISTS_THE_ROOT)
    assert hows(attempt(project, store)[0]) == ["ran"]
    (project / "dist").mkdir()
    (project / "dist/pkg.whl").write_bytes(b"built")
    assert hows(attempt(project, store)[0]) == ["reused"]


def test_a_pass_that_listed_the_root_is_not_reused_after_another_directory_appears(project, tmp_path):
    store = storage.Store(tmp_path / "store")
    (project / FILE).write_text(LISTS_THE_ROOT)
    attempt(project, store)
    (project / "docs").mkdir()
    assert hows(attempt(project, store)[0]) == ["ran"]
