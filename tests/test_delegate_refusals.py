"""What the writes of a delegate's run come to (`delegate_refusals.Writes`) when the hook denies one call and lets a later one through, and when a
tool fails at a path the box holds for a reason of its own.
"""

from __future__ import annotations

import pytest

from poolhouse.delegate_refusals import Writes


@pytest.fixture
def seen(tmp_path):
    made = Writes(str(tmp_path))
    made.within(str(tmp_path))
    return made


def call(seen, number, what, *, denied=False, status=""):
    """One call (``what``: its tool and arguments) as the hook heard it, and how the harness then said it ended."""
    name, arguments = what
    seen.asked(str(number), name, arguments, denied)
    if status:
        seen.ran({"id": str(number), "tool": name.lower(), "state": {"status": status, "input": arguments}})


def paths(seen):
    return [each.path for each in seen.refused()]


@pytest.mark.parametrize("later", [
    ("Bash", {"command": "touch held && false"}, "completed"), ("Write", {"file_path": "held"}, "error"),
    ("Write", {"file_path": "held"}, ""), ("Bash", {"command": "rm held"}, "completed")],
    ids=["a-command-that-failed-after", "an-edit-that-failed", "an-edit-never-said-to-end", "a-shell-line"])
def test_a_denied_write_is_taken_back_by_nothing_but_an_edit_of_that_path_that_completed(seen, later):
    call(seen, 1, ("Write", {"file_path": "held"}), denied=True)
    name, arguments, status = later
    call(seen, 2, (name, arguments), status=status)
    assert paths(seen) == ["held"]


def test_an_unrelated_command_does_not_take_back_a_denied_patch(seen):
    call(seen, 1, ("Bash", {"command": "patch -p1 < one.diff"}), denied=True)
    before = paths(seen)
    call(seen, 2, ("Bash", {"command": "git apply two.diff"}), status="completed")
    assert before and paths(seen) == before


def test_an_edit_of_the_same_path_that_completed_takes_a_denied_write_back(seen):
    call(seen, 1, ("Write", {"file_path": "held"}), denied=True)
    call(seen, 2, ("Edit", {"file_path": "held"}), status="completed")
    assert paths(seen) == []


@pytest.mark.parametrize(("error", "refused"), [("Could not find oldString in the file", []), ("File not found: settings", []),
                                                ("EPERM: operation not permitted, open '{path}'", ["settings"]),
                                                ("Unable to edit settings: it is not UTF-8", []), ("Unable to edit settings", ["settings"]),
                                                ("Unable to write settings\n\na word from the lead", ["settings"])])
def test_an_edit_at_a_path_the_box_holds_counts_only_when_its_write_failed(tmp_path, error, refused):
    seen = Writes(str(tmp_path))
    seen.within(str(tmp_path), lambda _path: True)
    arguments = {"path": "settings"}
    seen.asked("1", "Edit", arguments, False)
    seen.ran({"id": "1", "tool": "edit", "state": {"status": "error", "input": arguments,
                                                    "error": error.format(path=tmp_path / "settings")}})
    assert paths(seen) == refused
