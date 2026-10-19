"""Which commits origin can gain without making the checks that passed for a branch stale, against real repositories (no node, no board)."""

from __future__ import annotations

import land_exec as ex
import land_stale
import pytest
from land_support import DEV, Project, git

LONG = "".join(f"line {i}\n" for i in range(1, 10))
TEST_A = {"tests/test_a.py": "import poolhouse.units\n"}
UNITS = {"src/poolhouse/units.py": "X = 1\n"}
LAYERS = {"tests/test_layers.py": "import poolhouse.mod\n"}


def advance(proj: Project, changes: dict[str, str | None]) -> None:
    """One commit on origin's development branch that writes the files of ``changes`` and removes those that map to None."""
    for rel, text in changes.items():
        if text is None:
            git(proj.root, "rm", "-q", rel)
        else:
            proj.write(proj.root, rel, text)
    proj.commit(proj.root, "chore: upstream")
    git(proj.root, "push", "-q", "origin", DEV)


def verdict(tmp_path, branch_files, upstream, before=None, edit=None) -> str:
    """What ``stale`` says about a branch of ``branch_files`` that passed its checks when origin then gained ``upstream``.

    ``before`` is on origin when the branch is cut; ``edit`` maps files to the text an amend gives the branch after its checks passed.
    """
    (tmp_path / "git").mkdir()
    proj = Project(tmp_path / "git")
    if before:
        advance(proj, before)
    tree = proj.branch("b", branch_files)
    sha, base = git(tree, "rev-parse", "HEAD"), proj.origin_head()
    green = land_stale.green_of(sha, base, ex.plan(tree, base, sha), [])
    if edit:
        for rel, text in edit.items():
            proj.write(tree, rel, text)
        git(tree, "add", "-A")
        git(tree, "commit", "-q", "--amend", "-m", "feat: b")
    if upstream:
        advance(proj, upstream)
    git(tree, "fetch", "-q", "origin", DEV)
    git(tree, "rebase", "-q", proj.origin_head())
    now, moved = git(tree, "rev-parse", "HEAD"), proj.origin_head()
    return land_stale.stale(tree, green, now, moved, ex.plan(tree, moved, now))


@pytest.mark.parametrize(("branch", "upstream", "before"), [
    pytest.param(TEST_A, {"docs/b.md": "b\n"}, UNITS, id="a document no test names"),
    pytest.param(TEST_A, {"src/poolhouse/other.py": "X = 1\n"}, UNITS, id="source no test reaches"),
    pytest.param(TEST_A, {"src/poolhouse/mod.py": "VALUE = 2\n"}, {**UNITS, **LAYERS}, id="code only a tree-wide guard test reaches"),
    pytest.param(TEST_A, {}, UNITS, id="no new paths"),
])
def test_what_origin_gained_cannot_change_the_green_result(tmp_path, branch, upstream, before):
    assert verdict(tmp_path, branch, upstream, before) == ""


@pytest.mark.parametrize(("branch", "upstream", "before", "says"), [
    pytest.param({"docs/long.md": LONG.replace("line 1\n", "mine\n")}, {"docs/long.md": LONG.replace("line 9\n", "theirs\n")},
                 {"docs/long.md": LONG}, "docs/long.md, which the branch changes too", id="a file the branch changes"),
    pytest.param(TEST_A, {"src/poolhouse/units.py": "X = 2\n"}, UNITS, "tests/test_a.py reaches", id="code a selected test reaches"),
    pytest.param(TEST_A, {"tests/conftest.py": "x = 1\n"}, UNITS, "tests/conftest.py, which every test depends on", id="shared test infrastructure"),
    pytest.param(TEST_A, {"pyproject.toml": "[project]\n"}, UNITS, "pyproject.toml, which every test depends on", id="the project file"),
    pytest.param(TEST_A, {"scripts/land_extra.py": "X = 1\n"}, UNITS, "scripts/land_extra.py, which every test depends on", id="a lander file"),
    pytest.param(TEST_A, {"config/x.yaml": "k: v\n"}, UNITS, "no selector maps config/x.yaml", id="a path no selector maps"),
    pytest.param(TEST_A, {"src/poolhouse/gone.py": None}, {**UNITS, "src/poolhouse/gone.py": "X = 1\n"}, "no selector maps src/poolhouse/gone.py",
                 id="a deleted module"),
    pytest.param(TEST_A, {"tests/test_layers.py": "import poolhouse.units\n"}, {**UNITS, **LAYERS}, "tests/test_layers.py reaches",
                 id="a changed tree-wide guard test"),
    pytest.param({"tests/conftest.py": "x = 1\n"}, {"docs/b.md": "b\n"}, None, "the whole suite ran", id="a branch that ran the whole suite"),
    pytest.param({**TEST_A, "src/poolhouse/units.py": "X = 5\n"}, {"tests/test_up.py": "import poolhouse.units\n"}, UNITS,
                 "tests/test_up.py reaches the branch", id="a new test that reaches the branch's module"),
    pytest.param({"src/poolhouse/units.py": "X = 5\n"}, {"tests/test_up.py": "import poolhouse.units\n"}, UNITS,
                 "the checks they call for differ", id="a new test that makes the branch need the affected check"),
])
def test_what_origin_gained_can_change_the_green_result(tmp_path, branch, upstream, before, says):
    assert says in verdict(tmp_path, branch, upstream, before)


def test_a_branch_edited_after_its_checks_passed_is_not_the_one_that_was_tested(tmp_path):
    why = verdict(tmp_path, TEST_A, {"docs/b.md": "b\n"}, UNITS, edit={"tests/test_a.py": "import poolhouse.units\nimport os\n"})
    assert "tests/test_a.py is not the file that was tested" in why


def test_an_origin_that_does_not_descend_from_the_tested_base_is_stale(tmp_path):
    (tmp_path / "git").mkdir()
    proj = Project(tmp_path / "git")
    tree = proj.branch("b", TEST_A)
    sha, older = git(tree, "rev-parse", "HEAD"), proj.origin_head()
    advance(proj, {"docs/b.md": "b\n"})
    newer = proj.origin_head()
    green = land_stale.green_of(sha, newer, ex.plan(tree, older, sha), [])
    assert "no longer descends" in land_stale.stale(tree, green, sha, older, ex.plan(tree, older, sha))
