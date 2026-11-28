"""The path map that says which review a change needs (`workspace.review_depth`): every tracked file takes one, a path it does not
name asks for an attack review, and a real git repository cannot hide a change from it."""

from __future__ import annotations

import re
import subprocess

import pytest
from git_world import DEV, ROOT, Project, git

from poolhouse.workspace import review_depth

TRACKED = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, text=True, check=True).stdout.split("\0")[:-1]


def test_every_tracked_file_is_named_by_the_map_or_falls_to_the_attack_review():
    assert len(TRACKED) > 1000, "the checkout lists its files"
    for path in TRACKED:
        why = review_depth.by_path(path)
        named_light = any(re.fullmatch(p, path) for p in review_depth.LIGHT)
        assert why == (review_depth.area(path) or ("" if named_light else review_depth.UNCLASSIFIED)), path


@pytest.mark.parametrize("kind", ["areas", "light"])
def test_every_pattern_of_the_map_names_a_tracked_file(kind):
    patterns = [(p, re.I) for group in review_depth.AREAS.values() for p in group] if kind == "areas" else [
        (p, 0) for p in review_depth.LIGHT]
    assert [p for p, flags in patterns if not any(re.fullmatch(p, path, flags) for path in TRACKED)] == []


@pytest.mark.parametrize("path,area", [
    ("scripts/lead-review", "merge machinery"), ("src/poolhouse/workspace/release_check.py", "merge machinery"),
    ("src/poolhouse/workspace/review_depth.py", "merge machinery"), (".github/workflows/ci.yml", "merge machinery"),
    ("scripts/hooks/claude-bash-guard", "guards and hooks"), ("src/poolhouse/protectedwrites.py", "guards and hooks"),
    (".claude/settings.json", "guards and hooks"), ("tests/conftest.py", "guards and hooks"),
    ("tests/test_lead_review.py", "tests of guards, access and isolation"),
    ("src/poolhouse/sandbox/run.py", "sandbox and isolation"), ("src/poolhouse/isolation/proof.py", "sandbox and isolation"),
    ("src/poolhouse/authority.py", "access control"), ("src/poolhouse/graph/access.py", "access control"),
    ("src/poolhouse/net/download.py", "network listeners and proxies"), ("src/poolhouse/graph/serve.py", "network listeners and proxies"),
    ("src/poolhouse/keystore.py", "keys and credentials"), ("src/poolhouse/credentials/__init__.py", "keys and credentials"),
    ("src/poolhouse/provision.py", "installation and provisioning"), ("packaging/install.sh", "installation and provisioning"),
    ("scripts/worktrees", "merge machinery"), ("src/poolhouse/maintenance.py", "file or branch deletion"),
])
def test_each_area_the_owner_named_asks_for_an_attack_review(path, area):
    assert path in TRACKED, "the map is checked against a file that exists"
    assert review_depth.by_path(path) == area


@pytest.mark.parametrize("path", ["docs/landing.md", "AGENTS.md", "README.md", "src/poolhouse/graph/tree.py",
                                  "src/poolhouse/ui/assets/board.js", "tests/test_mod.py", "budgets.json"])
def test_what_no_area_names_may_take_the_lighter_review(path):
    assert review_depth.by_path(path) == ""


@pytest.mark.parametrize("path", ["src/poolhouse/new_thing.py", "scripts/new-tool", "newfolder/a.py", "Docs/landing.md",
                                  "docs/verify_release.py", "src/poolhouse/Graph/tree.py"])
def test_a_path_the_map_does_not_name_asks_for_an_attack_review(path):
    assert review_depth.by_path(path)


@pytest.fixture
def repo(tmp_path):
    """A project and a function that reads why a branch of ``files`` (made by ``change``) needs an attack review."""
    project = Project(tmp_path)
    for rel in ("docs/guide.md", "scripts/hooks/a-guard", "src/poolhouse/graph/plain.py", "src/poolhouse/graph/cleanup.py"):
        project.write(project.root, rel, "import shutil\nshutil.rmtree(x)\n" if rel.endswith("cleanup.py") else "text\n")
    project.write(project.root, "tests/test_plain.py", "def test_a():\n    assert True\n")
    project.write(project.root, "tests/test_marked.py", "import pytest\n\npytestmark = pytest.mark.redteam\n")
    base = project.commit(project.root, "chore: files")

    def reasons(change):
        where = project.branch(f"work/c{len(list(tmp_path.iterdir()))}", {})
        change(where)
        head = project.commit(where, "feat: change")
        return review_depth.attack_reasons(project.root, base, head)

    reasons.project, reasons.base = project, base
    return reasons


def writes(files):
    return lambda where: [Project.write(where, rel, text) for rel, text in files.items()]


def test_a_change_of_documents_and_plain_code_takes_the_lighter_review(repo):
    assert repo(writes({"docs/guide.md": "more rm -rf\n", "src/poolhouse/graph/plain.py": "VALUE = 1\n",
                        "tests/test_plain.py": "def test_a():\n    assert 1\n"})) == []


@pytest.mark.parametrize("text,reason", [
    ("import shutil\nshutil.rmtree(path)\n", "file or branch deletion"),
    ("import os\nos.remove(path)\n", "file or branch deletion"),
    ("run(['git', 'branch', '-D', name])\n", "file or branch deletion"),
    ("server.serve_forever()\n", "network listeners and proxies"),
    ("sock.bind(('', 0))\n", "network listeners and proxies"),
    ("import keyring\nkeyring.get_password(a, b)\n", "keys and credentials"),
])
def test_what_a_file_holds_makes_it_an_attack_review_wherever_it_lies(repo, text, reason):
    assert repo(writes({"src/poolhouse/graph/plain.py": text})) == [f"{reason}: src/poolhouse/graph/plain.py"]


def test_removing_the_dangerous_line_or_the_file_asks_for_the_review_too(repo):
    assert repo(writes({"src/poolhouse/graph/cleanup.py": "VALUE = 1\n"})) == [
        "file or branch deletion: src/poolhouse/graph/cleanup.py"]
    assert repo(lambda where: (where / "src/poolhouse/graph/cleanup.py").unlink()) == [
        "file or branch deletion: src/poolhouse/graph/cleanup.py"]


def test_a_red_team_test_is_one_by_its_name_or_by_its_marker_before_or_after(repo):
    marked = "guard, authority or red-team test: tests/test_marked.py"
    assert repo(writes({"tests/test_marked.py": "def test_a():\n    pass\n"})) == [marked]
    assert repo(lambda where: (where / "tests/test_marked.py").unlink()) == [marked]
    assert repo(writes({"tests/test_plain.py": "import pytest\npytestmark = pytest.mark.redteam\n"})) == [
        "guard, authority or red-team test: tests/test_plain.py"]


def test_a_guard_moved_under_a_lighter_name_is_still_seen(repo):
    def move(where):
        (where / "scripts/hooks/a-guard").rename(where / "docs/a-guard.md")

    assert repo(move) == ["guards and hooks: scripts/hooks/a-guard"]


def test_a_link_asks_for_the_review(repo):
    assert repo(lambda where: (where / "docs/link.md").symlink_to("../scripts/hooks/a-guard")) == [
        "a link or submodule: docs/link.md"]


def test_one_guard_among_hundreds_of_files_is_found(repo):
    files = {f"docs/page-{n}.md": f"{n}\n" for n in range(150)} | {"scripts/hooks/a-guard": "off\n"}
    assert repo(writes(files)) == ["guards and hooks: scripts/hooks/a-guard"]


def test_a_replace_ref_does_not_hide_a_change(repo):
    project = repo.project
    where = project.branch("work/hidden", {"scripts/hooks/a-guard": "off\n"})
    head = git(where, "rev-parse", "HEAD")
    git(project.root, "replace", head, repo.base)
    assert review_depth.attack_reasons(project.root, repo.base, head) == ["guards and hooks: scripts/hooks/a-guard"]


def test_a_change_after_a_merge_of_the_base_is_measured_from_where_it_left_it(repo):
    project = repo.project
    where = project.branch("work/merged", {"docs/guide.md": "branch\n"})
    project.advance("scripts/hooks/a-guard", "newer on development\n")
    git(where, "merge", "-q", "--no-edit", DEV)
    head = git(where, "rev-parse", "HEAD")
    assert review_depth.attack_reasons(project.root, git(project.root, "rev-parse", DEV), head) == []


def test_a_commit_that_is_not_there_is_an_error_and_not_a_lighter_review(repo):
    with pytest.raises(review_depth.Unreadable):
        review_depth.attack_reasons(repo.project.root, repo.base, "1" * 40)


def test_a_repository_asks_for_tiers_by_the_marker_on_its_base(repo):
    project = repo.project
    assert not review_depth.tiered(project.root, repo.base)
    marked = project.advance(review_depth.MARKER, "")
    (project.root / review_depth.MARKER).unlink()
    assert review_depth.tiered(project.root, marked)
