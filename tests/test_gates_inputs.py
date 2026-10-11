"""A remembered checker result is kept under exactly the files that checker reads, so an edit it reads is never answered stale."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import gates  # noqa: E402
from gates import _pins, _util  # noqa: E402

BANNED = "me" + "sh"
"""A word the glossary bans, written in two pieces so that this file needs no entry in the allowlist."""
PROBE = """
import sys
from pathlib import Path
sys.path.insert(0, "scripts")
import gates
checker = next(c for c in gates.checkers() if c.NAME == sys.argv[1])
print(len(gates.findings(checker, Path.cwd().resolve())))
"""


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def mirror(tmp_path):
    """A one-off checkout holding the gate package, the glossary and a document, remembering into its own cache."""
    root = tmp_path / "repo"
    shutil.copytree(REPO / "scripts" / "gates", root / "scripts" / "gates",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(REPO / "scripts" / "nomenclature.py", root / "scripts" / "nomenclature.py")
    shutil.copy(REPO / "pyproject.toml", root / "pyproject.toml")
    (root / "contracts").mkdir()
    for name in ("glossary-allowlist.toml", "glossary-rules.toml"):
        shutil.copy(REPO / "contracts" / name, root / "contracts" / name)
    (root / "docs").mkdir()
    shutil.copy(REPO / "docs" / "glossary.md", root / "docs" / "glossary.md")
    (root / "docs" / "notes.md").write_text("A harmless sentence.\n", encoding="utf-8")
    (root / "packaging").mkdir()
    (root / "packaging" / "probe.py").write_text("def f(x=None):\n    return x\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "--", "docs", "contracts", "scripts", "packaging", "pyproject.toml")
    return root


def count(root: Path, name: str) -> int:
    """How many findings ``name`` reports over ``root``, asked in a fresh process sharing one cache directory."""
    env = {**os.environ, "TMPDIR": str(root.parent / "cache"), "HOME": str(root.parent / "home"),
           "PYTHONPATH": str(REPO / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("POOLHOUSE_GATES_FULL", None)
    (root.parent / "cache").mkdir(exist_ok=True)
    (root.parent / "home").mkdir(exist_ok=True)
    done = subprocess.run([sys.executable, "-c", PROBE, name], cwd=root, env=env, capture_output=True,
                          text=True, check=True)
    return int(done.stdout.strip().splitlines()[-1])


def test_a_banned_word_added_only_to_a_document_turns_glossary_words_red_after_a_green_run(mirror):
    assert count(mirror, "glossary-words") == 0
    assert count(mirror, "glossary-words") == 0
    with (mirror / "docs" / "notes.md").open("a", encoding="utf-8") as out:
        out.write(f"The {BANNED} is a word the glossary bans.\n")
    assert count(mirror, "glossary-words") > 0


def test_an_exclusion_added_only_to_the_allowlist_turns_a_red_glossary_scan_green(mirror):
    (mirror / "docs" / "notes.md").write_text(f"The {BANNED} is a word the glossary bans.\n", encoding="utf-8")
    assert count(mirror, "glossary-words") > 0
    allowlist = mirror / "contracts" / "glossary-allowlist.toml"
    allowlist.write_text(allowlist.read_text(encoding="utf-8")
                         + '\n[[exclude]]\npath = "docs/notes.md"\nreason = "a test"\n', encoding="utf-8")
    assert count(mirror, "glossary-words") == 0


def test_a_file_the_glossary_scan_reads_in_a_new_directory_turns_it_red(mirror):
    assert count(mirror, "glossary-words") == 0
    (mirror / "app").mkdir()
    (mirror / "app" / "page.html").write_text(f"<p>the {BANNED}</p>\n", encoding="utf-8")
    git(mirror, "add", "--", "app/page.html")
    assert count(mirror, "glossary-words") > 0


@pytest.mark.skipif(bool(_pins.skip("ruff")), reason="the pinned ruff is not installed")
def test_a_violation_added_only_under_packaging_changes_the_ruff_count_after_a_green_run(mirror):
    before = count(mirror, "ruff-bugbear")
    assert count(mirror, "ruff-bugbear") == before
    (mirror / "packaging" / "probe.py").write_text("def f(x=[]):\n    return x\n", encoding="utf-8")
    assert count(mirror, "ruff-bugbear") > before


def make_tree(root: Path) -> None:
    for name in ("src/a.py", "tests/test_a.py", "scripts/s.py", "pyproject.toml", "docs/d.md",
                 "contracts/c.toml", "packaging/p.py", "budgets.json"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("x\n", encoding="utf-8")


@pytest.mark.parametrize("name", ["src/a.py", "tests/test_a.py", "scripts/s.py", "pyproject.toml"])
def test_the_default_inputs_move_with_the_code_the_checkers_read(tmp_path, name):
    make_tree(tmp_path)
    before = _util.tree_fingerprint(tmp_path)
    (tmp_path / name).write_text("changed\n", encoding="utf-8")
    assert _util.tree_fingerprint(tmp_path) != before


@pytest.mark.parametrize("name", ["docs/d.md", "contracts/c.toml", "packaging/p.py", "budgets.json"])
def test_the_default_inputs_ignore_what_a_code_checker_does_not_read(tmp_path, name):
    make_tree(tmp_path)
    before = _util.tree_fingerprint(tmp_path)
    (tmp_path / name).write_text("changed\n", encoding="utf-8")
    assert _util.tree_fingerprint(tmp_path) == before


@pytest.mark.parametrize("name", ["docs/d.md", "contracts/c.toml", "packaging/p.py", "budgets.json", "src/a.py"])
@pytest.mark.parametrize("repository", [False, True])
def test_every_file_is_an_input_to_a_checker_that_reads_the_whole_tree(tmp_path, name, repository):
    make_tree(tmp_path)
    if repository:
        git(tmp_path, "init", "-q")
    before = _util.tree_fingerprint(tmp_path, (_util.EVERYTHING,))
    (tmp_path / name).write_text("changed\n", encoding="utf-8")
    assert _util.tree_fingerprint(tmp_path, (_util.EVERYTHING,)) != before


def test_a_new_file_git_does_not_ignore_is_an_input_and_an_ignored_one_is_not(tmp_path):
    make_tree(tmp_path)
    git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("scratch/\n", encoding="utf-8")
    before = _util.tree_fingerprint(tmp_path, (_util.EVERYTHING,))
    (tmp_path / "scratch").mkdir()
    (tmp_path / "scratch" / "x.md").write_text("ignored\n", encoding="utf-8")
    assert _util.tree_fingerprint(tmp_path, (_util.EVERYTHING,)) == before
    (tmp_path / "docs" / "new.md").write_text("new\n", encoding="utf-8")
    assert _util.tree_fingerprint(tmp_path, (_util.EVERYTHING,)) != before


def test_a_deleted_input_changes_the_fingerprint(tmp_path):
    make_tree(tmp_path)
    before = _util.tree_fingerprint(tmp_path, ("docs",))
    (tmp_path / "docs" / "d.md").unlink()
    assert _util.tree_fingerprint(tmp_path, ("docs",)) != before


def test_a_checker_held_whole_declares_what_it_reads():
    for checker in gates.checkers():
        if not getattr(checker, "INCREMENTAL", False):
            assert getattr(checker, "INPUTS", None), f"{checker.NAME} is remembered by tree and names no INPUTS"


def test_the_glossary_scan_is_keyed_by_the_whole_tree_and_the_ruff_ones_by_every_directory_ruff_reads():
    named = {c.NAME: set(c.INPUTS) for c in gates.checkers() if hasattr(c, "INPUTS")}
    assert named["glossary-words"] == {_util.EVERYTHING}
    for name in ("ruff-blind-except", "ruff-bugbear", "ruff-other", "ruff-security"):
        assert {"src", "tests", "scripts", "packaging", "pyproject.toml"} <= named[name]
