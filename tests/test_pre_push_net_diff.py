"""The push check measures the net difference from the remote's tip, in real git repositories: a merge adds nothing to it, a generated
file selects only the tests that read it, and a remote that has moved on is turned away before any test runs."""

import subprocess
from pathlib import Path

from tests.test_pre_push import ZERO, commit, git, push
from tests.test_pre_push_affected import calls, landing, repo  # noqa: F401  (repo is the fixture)

PASSES = "def test_it():\n    assert True\n"
READS = "from pathlib import Path\n\n\ndef test_reads():\n    assert Path('docs/notes.md').is_file()\n"


def other_clone(work: Path, tmp_path: Path) -> Path:
    """A second checkout of the remote `work` pushes to, where someone else can push."""
    clone = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", git(work, "remote", "get-url", "origin"), str(clone)], check=True)
    return clone


def test_a_merge_of_the_remote_tip_leaves_only_the_pushed_prose_in_the_difference(repo, tmp_path):  # noqa: F811
    commit(repo, "docs/notes.md", "x\n")
    commit(repo, "tests/test_notes.py", READS)
    git(repo, "push", "-q", "origin", "0.9dev")
    commit(repo, "docs/notes.md", "y\n")
    other = other_clone(repo, tmp_path)
    commit(other, "src/poolhouse/mod.py", "VALUE = 1\nMORE = 11\n")
    git(other, "push", "-q", "origin", "0.9dev")
    git(repo, "fetch", "-q", "origin")
    git(repo, "merge", "-q", "--no-edit", "origin/0.9dev")
    assert len(git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 3    # a merge commit
    log = tmp_path / "log"
    done = landing(repo, log)
    assert done.returncode == 0, done.stderr
    assert [c["argv"] for c in calls(log)] == [["all", "tests/test_notes.py", "--redteam"]]


def test_a_generated_file_selects_the_tests_that_read_it_or_run_its_generator_and_no_test_that_only_reaches_it(repo, tmp_path):  # noqa: F811
    for name, text in {"scripts/reference": "#!/bin/sh\n", "docs/commands.md": "x\n",
                       "tests/test_commands_doc.py": "PAGE = 'docs/commands.md'\n\n\n" + PASSES,
                       "tests/test_runs_reference.py": "RUN = 'scripts/reference'\n\n\n" + PASSES,
                       "tests/helper.py": "RUN = 'scripts/reference'\n",
                       "tests/test_through_a_helper.py": "from tests import helper\n\n\n" + PASSES,
                       "tests/test_bare_word.py": "WHERE = 'scripts'\nNAME = 'reference'\n\n\n" + PASSES}.items():
        commit(repo, name, text)
    git(repo, "push", "-q", "origin", "0.9dev")
    commit(repo, "docs/commands.md", "y\n")
    log = tmp_path / "log"
    done = landing(repo, log)
    assert done.returncode == 0, done.stderr
    assert [c["argv"] for c in calls(log)] == [["all", "tests/test_commands_doc.py", "tests/test_runs_reference.py", "--redteam"]]


def test_a_generated_data_page_runs_no_tree_wide_guard_as_prose_does_not(repo, tmp_path):  # noqa: F811
    page = "docs/redteam/coverage.json"
    for name, text in {page: "{}\n", "tests/test_suite.py": PASSES, "tests/test_coverage_page.py": f"PAGE = '{page}'\n\n\n" + PASSES}.items():
        commit(repo, name, text)
    git(repo, "push", "-q", "origin", "0.9dev")
    commit(repo, page, '{"a": 1}\n')
    log = tmp_path / "log"
    done = landing(repo, log)
    assert done.returncode == 0, done.stderr
    assert [c["argv"] for c in calls(log)] == [["all", "tests/test_coverage_page.py", "--redteam"]]


def test_a_remote_that_took_commits_this_checkout_has_fetched_but_not_merged_is_a_refusal_before_any_test(repo, tmp_path):  # noqa: F811
    other = other_clone(repo, tmp_path)
    commit(other, "docs/elsewhere.md", "x\n")
    git(other, "push", "-q", "origin", "0.9dev")
    git(repo, "fetch", "-q", "origin")
    commit(repo, "src/poolhouse/mod.py", "VALUE = 1\nMORE = 12\n")
    log = tmp_path / "log"
    done = push(repo, "0.9dev", base=git(repo, "rev-parse", "origin/0.9dev"), CLAUDECODE="1", STUB_LOG=str(log))
    assert done.returncode != 0
    assert "is not in the history of" in done.stderr and "the remote will not take the push" in done.stderr
    assert "run it again with: git fetch origin && git merge origin/0.9dev && git push origin HEAD:refs/heads/0.9dev" in done.stderr
    assert calls(log) == []


def test_a_remote_tip_this_checkout_never_fetched_is_a_refusal_before_any_test(repo, tmp_path):  # noqa: F811
    other = other_clone(repo, tmp_path)
    moved = commit(other, "docs/elsewhere.md", "x\n")
    git(other, "push", "-q", "origin", "0.9dev")
    commit(repo, "src/poolhouse/mod.py", "VALUE = 1\nMORE = 13\n")
    log = tmp_path / "log"
    done = push(repo, "0.9dev", base=moved, CLAUDECODE="1", STUB_LOG=str(log))
    assert done.returncode != 0
    assert "is not in this repository" in done.stderr and "no test is run for it" in done.stderr
    assert calls(log) == []


def test_a_branch_the_remote_does_not_have_yet_is_measured_by_the_commits_no_remote_ref_holds(repo, tmp_path):  # noqa: F811
    commit(repo, "src/poolhouse/mod.py", "VALUE = 1\nMORE = 14\n")
    log = tmp_path / "log"
    done = push(repo, "0.9dev", base=ZERO, CLAUDECODE="1", STUB_LOG=str(log))
    assert done.returncode == 0, done.stderr
    assert [c["argv"] for c in calls(log)] == [["all", "tests/test_untouched.py", "--redteam"]]
