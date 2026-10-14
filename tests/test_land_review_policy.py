"""The trusted runner classifies the pinned diff; different creator identities may review."""
from __future__ import annotations

import land_review
import pytest
from land_support import DEV, Project, git
from land_world import World, mod

from poolhouse.board.client import Denied, Invalid
from poolhouse.workspace import landing

pytest_plugins = ["node_kit"]


@pytest.fixture
def world(monkeypatch, tmp_path, workspace_node):
    return World(monkeypatch, tmp_path, workspace_node)


def test_ordinary_documentation_lands_without_a_review(world):
    sha = world.branch("ordinary", {"docs/notes/example.md": "A local example.\n"})
    rid = world.ask("alice", "ordinary", sha)
    assert world.runner.once()["status"] == "landed"
    assert world.status(rid) == "landed"
    assert world.origin_head() == world.local_head()
    assert git(world.proj.root, "merge-base", sha, DEV) == sha


def test_optional_rejection_still_blocks_ordinary_documentation(world):
    sha = world.branch("declined", {"docs/notes/example.md": "Example.\n"})
    rid = world.ask("alice", "declined", sha)
    landing.review(world.who["carol"], rid, sha, "reject")
    before = world.origin_head()
    assert world.runner.once()["status"] == "idle"
    assert world.status(rid) == "needs-review" and "rejected" in world.detail(rid)
    assert world.origin_head() == before


@pytest.mark.parametrize("path", ["src/poolhouse/credentials/example.py", "src/poolhouse/sealing.py", "src/poolhouse/serve/example.py", "scripts/land_review.py", "docs/landing-queue.md", "docs/node.md", "docs/notes/AGENTS.md", "new-executable.py", "pyproject.toml"])
def test_security_and_unknown_executable_changes_need_review(world, path):
    sha = world.branch("sensitive", {path: "VALUE = 1\n"})
    rid = world.ask("alice", "sensitive", sha)
    before = world.origin_head()
    assert world.runner.once()["status"] == "idle"
    assert world.status(rid) == "needs-review"
    evidence = world.requests()[rid]["evidence"]
    assert evidence["review_required"] is True and evidence["review_sha"] == sha
    assert evidence["review_path"] == path
    assert world.origin_head() == before


def test_requester_cannot_override_review_classification(world):
    sha = world.branch("sensitive", mod(31))
    who = world.who["alice"]
    who.claim("branch", "sensitive")
    with pytest.raises(Invalid, match="review_required"):
        who.call("land_request", branch="sensitive", sha=sha, target=DEV, selectors=["tests/test_mod.py"], review_required=False)


def test_a_request_needs_the_creators_authenticated_branch_claim(world):
    sha = world.branch("owned", mod(32))
    creator = world.who["alice"]
    with pytest.raises(Denied, match="authenticated claim"):
        creator.call("land_request", branch="owned", sha=sha, target=DEV, selectors=["tests/test_mod.py"])
    creator.claim("branch", "owned")
    with pytest.raises(Denied, match="authenticated claim"):
        world.who["bob"].call("land_request", branch="owned", sha=sha, target=DEV, selectors=["tests/test_mod.py"])


def test_a_renamed_security_path_cannot_hide_under_documentation(world):
    project = world.proj
    root = project.root
    Project.write(root, "src/poolhouse/sealing.py", "VALUE = 1\n")
    Project.commit(root, "feat: add a guarded module")
    git(root, "push", "-q", "origin", DEV)
    sha = world.branch("renamed", {"docs/start.md": "Example.\n"})
    tree = project.base / "renamed"
    (tree / "docs").mkdir(exist_ok=True)
    git(tree, "mv", "src/poolhouse/sealing.py", "docs/explanation.md")
    Project.commit(tree, "chore: move a module")
    sha = git(tree, "rev-parse", "HEAD")
    required, base, path = land_review.required(root, DEV, sha)
    assert required and path == "src/poolhouse/sealing.py"
    assert base == world.origin_head()


def test_comment_only_python_change_needs_no_review(world):
    sha = world.branch("comments", {"src/poolhouse/mod.py": "VALUE = 1\n# Example value.\n"})
    required, _, _ = land_review.required(world.proj.root, DEV, sha)
    assert not required


def test_a_document_symlink_requires_review(world):
    sha = world.branch("link", {"docs/example.md": "text\n"})
    tree = world.proj.base / "link"
    (tree / "docs/example.md").unlink()
    (tree / "docs/example.md").symlink_to("../AGENTS.md")
    Project.commit(tree, "chore: link a document")
    sha = git(tree, "rev-parse", "HEAD")
    assert land_review.required(world.proj.root, DEV, sha)[0]


def test_modifying_an_existing_document_symlink_requires_review(world):
    root = world.proj.root
    (root / "docs/linked.md").symlink_to("a.md")
    Project.commit(root, "chore: add a document link")
    git(root, "push", "-q", "origin", DEV)
    world.branch("changedlink", {"docs/note.md": "Example.\n"})
    tree = world.proj.base / "changedlink"
    (tree / "docs/linked.md").unlink()
    (tree / "docs/linked.md").symlink_to("../AGENTS.md")
    Project.commit(tree, "chore: change a link target")
    sha = git(tree, "rev-parse", "HEAD")
    assert land_review.required(root, DEV, sha)[0]
