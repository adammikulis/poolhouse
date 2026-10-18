"""The review requirement is a positive list of security-adjacent paths; a path nobody classified fails closed."""

from __future__ import annotations

import land_review
import pytest
from land_support import DEV, ROOT, Project, git


@pytest.fixture
def proj(tmp_path):
    return Project(tmp_path)


def tracked(prefix: str, depth: int) -> set[str]:
    """The names ``depth`` components down in the tracked files under ``prefix`` of this repository."""
    names = git(ROOT, "ls-files", *([prefix] if prefix else [])).splitlines()
    return {"/".join(name.split("/")[:depth]) for name in names if name.count("/") >= depth - 1}


def test_every_top_level_path_of_the_repository_is_classified():
    known = land_review.SENSITIVE_ROOTS | land_review.ORDINARY_ROOTS | land_review.SPLIT_ROOTS
    assert not land_review.SENSITIVE_ROOTS & land_review.ORDINARY_ROOTS
    assert tracked("", 1) <= known, f"classify in scripts/land_review.py: {sorted(tracked('', 1) - known)}"


def test_every_module_and_package_of_the_library_is_classified_once():
    listed = {p.split("/", 2)[2] for p in tracked("src/poolhouse", 3)}
    classified = land_review.SRC_ORDINARY | land_review.SRC_SENSITIVE
    assert not land_review.SRC_ORDINARY & land_review.SRC_SENSITIVE
    assert listed <= classified, f"classify in scripts/land_review.py: {sorted(listed - classified)}"


@pytest.mark.parametrize("path", [
    "newarea/example.py", "src/poolhouse/newarea/example.py", "src/poolhouse/newmodule.py", "scripts/brand-new-tool", "src/other/x.py",
    "docs/diagram.png", "contracts/new_contract.json"])
def test_a_path_nobody_classified_needs_review(path):
    assert land_review.sensitive(path)


@pytest.mark.parametrize("path", [
    "pyproject.toml", "budgets.json", "tests/conftest.py", "scripts/affected.py", "scripts/affected_rules.py", "scripts/test",
    "scripts/testslots.py", "scripts/gates/duplicates.py", "scripts/land", "scripts/land_steps.py", "scripts/hooks/pushed",
    ".claude/agents/branch-worker.md", ".github/workflows/ci.yml", "AGENTS.md", "docs/node.md", "docs/notes/AGENTS.md",
    "docs/landing.md", "docs/redteam/coverage.json", "contracts/model_tiers.json", "src/poolhouse/credentials/example.py", "src/poolhouse/sealing.py",
    "src/poolhouse/serve/example.py", "src/poolhouse/workspace/landing.py", "app/poolhouse-node/src/landing.rs", "packaging/build.py",
    ".gitattributes", ".gitignore", "version.txt"])
def test_security_test_infrastructure_and_rule_paths_need_review(path):
    assert land_review.sensitive(path)


@pytest.mark.parametrize("path", [
    "docs/notes/example.md", "HANDOFF.md", "CHANGELOG.md", "tests/test_example.py", "src/poolhouse/graph/example.py",
    "src/poolhouse/bench/example.py", "scripts/nomenclature.py", "contracts/glossary-allowlist.toml"])
def test_ordinary_paths_need_no_review(path):
    assert not land_review.sensitive(path)


def test_ordinary_files_land_without_a_review(proj):
    proj.branch("ordinary", {"docs/notes/example.md": "A local example.\n", "tests/test_other.py": "import os\n"})
    required, base, path = land_review.required(proj.root, DEV, git(proj.root, "rev-parse", "ordinary"))
    assert not required and path == "" and base == proj.origin_head()


def test_the_first_sensitive_path_is_named(proj):
    proj.branch("mixed", {"docs/notes/example.md": "x\n", "src/poolhouse/sealing.py": "VALUE = 1\n", "pyproject.toml": "[project]\n"})
    required, _, path = land_review.required(proj.root, DEV, git(proj.root, "rev-parse", "mixed"))
    assert required and path == "pyproject.toml"


def test_deleting_a_test_needs_review_and_changing_one_does_not(proj):
    proj.advance("tests/test_gone.py", "def test_x():\n    assert 1\n")
    kept = proj.branch("changed", {"tests/test_gone.py": "def test_x():\n    assert 2\n"})
    assert not land_review.required(proj.root, DEV, git(kept, "rev-parse", "HEAD"))[0]
    tree = proj.branch("deleted", {})
    git(tree, "rm", "-q", "tests/test_gone.py")
    proj.commit(tree, "chore: drop a test")
    required, _, path = land_review.required(proj.root, DEV, git(tree, "rev-parse", "HEAD"))
    assert required and path == "tests/test_gone.py"


def test_a_renamed_security_path_cannot_hide_under_documentation(proj):
    proj.advance("src/poolhouse/sealing.py", "VALUE = 1\n")
    tree = proj.branch("renamed", {"docs/start.md": "Example.\n"})
    git(tree, "mv", "src/poolhouse/sealing.py", "docs/explanation.md")
    proj.commit(tree, "chore: move a module")
    required, base, path = land_review.required(proj.root, DEV, git(tree, "rev-parse", "HEAD"))
    assert required and path == "src/poolhouse/sealing.py" and base == proj.origin_head()


def test_a_document_symlink_requires_review(proj):
    tree = proj.branch("link", {"docs/example.md": "text\n"})
    (tree / "docs/example.md").unlink()
    (tree / "docs/example.md").symlink_to("../AGENTS.md")
    proj.commit(tree, "chore: link a document")
    assert land_review.required(proj.root, DEV, git(tree, "rev-parse", "HEAD"))[0]


def test_modifying_an_existing_document_symlink_requires_review(proj):
    (proj.root / "docs/linked.md").symlink_to("a.md")
    proj.commit(proj.root, "chore: add a document link")
    git(proj.root, "push", "-q", "origin", DEV)
    proj.branch("changedlink", {"docs/note.md": "Example.\n"})
    tree = proj.base / "changedlink"
    (tree / "docs/linked.md").unlink()
    (tree / "docs/linked.md").symlink_to("../AGENTS.md")
    proj.commit(tree, "chore: change a link target")
    assert land_review.required(proj.root, DEV, git(tree, "rev-parse", "HEAD"))[0]
