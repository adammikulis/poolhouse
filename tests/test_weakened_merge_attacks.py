"""weakened-assertions against what a repository can plant to blind it: merge drivers, attributes, marker-like lines, extra merge heads."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_weakened_merge_bases import GUARD, against, criss_cross, git, sh, spread, staged, write

WEAK = "def test_a():\n    pass\n"
NONE = ("ours_extra", "theirs_extra", "shared_extra", "a_extra", "b_extra")


def lost(done) -> None:
    assert done.returncode == 1 and "fewer assertions" in done.stderr, (done.returncode, done.stderr)


def two_sides(tmp_path: Path, base: str, left: str, right: str) -> Path:
    """`left` (checked out) and `right`, each one commit past ``base``."""
    repo = tmp_path / "sides"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "left")
    (repo / "tests").mkdir()
    write(repo, base, "base")
    git(repo, "branch", "right")
    write(repo, left, "left")
    git(repo, "checkout", "-q", "right")
    write(repo, right, "right")
    git(repo, "checkout", "-q", "left")
    return repo


def added_by_both(tmp_path: Path) -> Path:
    return two_sides(tmp_path, spread(*NONE), spread(*[n for n in NONE if n != "ours_extra"]),
                     spread(*[n for n in NONE if n != "theirs_extra"]))


@pytest.mark.redteam
def test_a_merge_driver_named_by_the_repository_does_not_decide_what_a_merge_keeps(tmp_path) -> None:
    repo = added_by_both(tmp_path)
    weak = repo / ".git" / "weak"
    weak.write_text(WEAK)
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("tests/** merge=w\n")
    git(repo, "config", "merge.w.driver", f"cp {weak} %A")
    git(repo, "merge", "--no-commit", "--no-ff", "right")
    assert (repo / GUARD).read_text() == WEAK
    lost(staged(repo))


@pytest.mark.redteam
def test_a_default_merge_driver_given_on_the_command_line_does_not_decide_what_a_merge_keeps(tmp_path, monkeypatch) -> None:
    repo = added_by_both(tmp_path)
    weak = repo / ".git" / "weak"
    weak.write_text(WEAK)
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", f"'merge.default=w' 'merge.w.driver=cp {weak} %A'")
    git(repo, "merge", "--no-commit", "--no-ff", "right")
    assert (repo / GUARD).read_text() == WEAK
    lost(staged(repo))


@pytest.mark.redteam
def test_a_committed_binary_merge_attribute_does_not_keep_one_base_unseen(tmp_path) -> None:
    kept = ("ours_extra", "theirs_extra", "shared_extra")
    repo = criss_cross(tmp_path, spread(*NONE), spread(*kept, "b_extra"), spread(*kept, "a_extra"), spread(*kept))
    git(repo, "checkout", "-q", "left")
    found = git(repo, "merge-base", "--all", "left", "right").split()
    (repo / ".gitattributes").write_text("tests/** merge=binary\n")
    git(repo, "add", ".gitattributes")
    git(repo, "commit", "-qm", "attributes", "--no-verify")
    write(repo, git(repo, "show", f"{found[0]}:{GUARD}"), "keeps the first base's file")
    lost(against(repo, "right"))


@pytest.mark.redteam
@pytest.mark.parametrize("attribute", ["-diff", "diff=quiet"])
def test_an_attribute_that_hides_a_diff_does_not_hide_a_weakening(tmp_path, attribute) -> None:
    repo = tmp_path / "hidden"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "dev")
    (repo / "tests").mkdir()
    write(repo, spread(), "base")
    git(repo, "config", "diff.quiet.command", "true")
    git(repo, "checkout", "-qb", "branch")
    (repo / ".gitattributes").write_text(f"tests/** {attribute}\n")
    git(repo, "add", ".gitattributes")
    (repo / GUARD).write_text(spread("shared_kept"))
    git(repo, "add", GUARD)
    lost(staged(repo))
    git(repo, "commit", "-qm", "weakens", "--no-verify")
    lost(against(repo, "dev"))


@pytest.mark.redteam
@pytest.mark.parametrize("setting", ["diff.noprefix", "color.diff"])
def test_a_diff_setting_of_the_repository_does_not_hide_a_weakening(tmp_path, setting) -> None:
    repo = tmp_path / "configured"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "dev")
    (repo / "tests").mkdir()
    write(repo, spread(), "base")
    git(repo, "config", setting, "always" if setting == "color.diff" else "true")
    (repo / GUARD).write_text(spread("shared_kept"))
    git(repo, "add", GUARD)
    lost(staged(repo))


@pytest.mark.redteam
def test_a_line_ending_inside_an_added_line_does_not_start_another_file_in_the_diff(tmp_path) -> None:
    repo = tmp_path / "forged"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "dev")
    (repo / "tests").mkdir()
    write(repo, spread(), "base")
    forged = "# x\rdiff --git a/docs/x b/docs/x\n" + spread("shared_kept")
    (repo / GUARD).write_bytes(forged.encode())
    git(repo, "add", GUARD)
    lost(staged(repo))


@pytest.mark.redteam
def test_marker_like_lines_inside_a_conflict_do_not_excuse_a_loss(tmp_path) -> None:
    base = "def test_a():\n    assert 1\n"
    padded = 'def test_a():\n    x = """\n||||||| fake\n' + "    assert 'padding'\n" * 6 + '    """\n    assert 1\n'
    other = "def test_a():\n    assert 'b'\n    assert 'c'\n    assert 'd'\n"
    repo = two_sides(tmp_path, base, padded, other)
    assert sh(repo, "git", "merge", "--no-commit", "--no-ff", "right").returncode == 1
    (repo / GUARD).write_text("def test_a():\n    pass\n")
    git(repo, "add", GUARD)
    lost(staged(repo))


def three_heads(tmp_path: Path) -> Path:
    repo = tmp_path / "octopus"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "dev")
    (repo / "tests").mkdir()
    write(repo, spread(*NONE), "base")
    git(repo, "branch", "first")
    git(repo, "branch", "second")
    for branch, name in (("dev", "dev.txt"), ("first", "first.txt")):
        git(repo, "checkout", "-q", branch)
        (repo / name).write_text(name)
        git(repo, "add", name)
        git(repo, "commit", "-qm", name, "--no-verify")
    git(repo, "checkout", "-q", "second")
    write(repo, spread(), "second adds")
    git(repo, "checkout", "-q", "dev")
    git(repo, "merge", "--no-commit", "--no-ff", "first", "second")
    return repo


def test_an_octopus_merge_that_keeps_every_head_is_accepted(tmp_path) -> None:
    done = staged(three_heads(tmp_path))
    assert done.returncode == 0, done.stderr


@pytest.mark.redteam
@pytest.mark.parametrize("kept", [spread(*NONE) + "# this side\n", spread(*NONE)], ids=["edited", "this side whole"])
def test_an_octopus_merge_that_drops_the_second_heads_additions_is_reported(tmp_path, kept) -> None:
    repo = three_heads(tmp_path)
    (repo / GUARD).write_text(kept)
    git(repo, "add", GUARD)
    lost(staged(repo))


@pytest.mark.redteam
def test_a_guard_file_one_side_added_and_the_merge_deletes_is_reported(tmp_path) -> None:
    repo = two_sides(tmp_path, spread(), "# left\n" + spread(), spread() + "# right\n")
    (repo / "tests" / "test_y_guard.py").write_text(spread())
    git(repo, "add", "tests/test_y_guard.py")
    git(repo, "commit", "-qm", "adds a guard", "--no-verify")
    git(repo, "merge", "--no-commit", "--no-ff", "right")
    git(repo, "rm", "-qf", "tests/test_y_guard.py")
    lost(staged(repo))


@pytest.mark.redteam
def test_a_file_marked_red_team_only_on_the_incoming_side_and_weakened_in_the_merge_is_reported(tmp_path) -> None:
    repo = two_sides(tmp_path, spread(), "# left\n" + spread(), spread() + "# right\n")
    git(repo, "checkout", "-q", "right")
    marked = "import pytest\npytestmark = pytest.mark.redteam\n\n\ndef test_m():\n    assert 1\n    assert 2\n"
    (repo / "tests" / "test_plain.py").write_text(marked)
    git(repo, "add", "tests/test_plain.py")
    git(repo, "commit", "-qm", "adds a marked file", "--no-verify")
    git(repo, "checkout", "-q", "left")
    sh(repo, "git", "merge", "--no-commit", "--no-ff", "right")
    (repo / "tests" / "test_plain.py").write_text("def test_m():\n    pass\n")
    git(repo, "add", "tests/test_plain.py")
    lost(staged(repo))
