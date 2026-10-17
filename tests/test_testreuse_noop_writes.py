"""Calls that change nothing, and writes to the temporary directory, are not violations of a recorded run."""

from __future__ import annotations

import pytest
import testreuse_plugin as plugin

ELSEWHERE = "/usr"


def violations(event: str, *args) -> set[str]:
    """What the plugin's audit records for ``event`` as a test body would raise it."""
    plugin.FILES.pop("probe", None)
    plugin.CURRENT.append("probe")
    try:
        plugin._audit(event, args)
        return set(plugin.FILES["probe"]["violations"])
    finally:
        plugin.CURRENT.pop()
        plugin.FILES.pop("probe", None)


def test_making_a_directory_that_exists_changes_nothing():
    assert violations("os.mkdir", ELSEWHERE, 0o777, -1) == set()


def test_making_a_directory_that_does_not_exist_outside_the_tree_is_a_violation():
    missing = "/nonexistent-site-packages/newdir"
    assert violations("os.mkdir", missing, 0o777, -1) == {f"mkdir {missing}"}


def test_removing_a_directory_outside_the_tree_is_still_a_violation():
    assert violations("os.rmdir", ELSEWHERE, -1) == {f"rmdir {ELSEWHERE}"}


@pytest.mark.parametrize("event", ["os.chmod", "os.mkdir", "os.remove"])
def test_changing_a_path_under_the_short_temporary_directory_is_allowed(event):
    assert violations(event, "/tmp/ml0abcd/node", 0o755) == set()


def test_changing_a_path_beside_the_temporary_directory_is_a_violation():
    assert violations("os.chmod", "/tmpfoo/node", 0o755) == {"chmod /tmpfoo/node"}


def test_a_dotdot_path_that_leaves_the_temporary_directory_is_a_violation():
    escaping = "/tmp/../usr/node"
    assert violations("os.chmod", escaping, 0o755) == {f"chmod {escaping}"}


def test_a_symlink_under_the_temporary_directory_to_somewhere_else_is_a_violation(tmp_path):
    (tmp_path / "home").symlink_to(ELSEWHERE)
    through = str(tmp_path / "home" / "node")
    assert violations("os.chmod", through, 0o755) == {f"chmod {through}"}
