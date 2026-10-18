"""The review requirement is a positive list of security-adjacent paths; a path nobody classified fails closed."""

from __future__ import annotations

import ast
import re

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


def test_every_name_the_library_lists_is_a_module_or_package():
    listed = {p.split("/", 2)[2] for p in tracked("src/poolhouse", 3)}
    assert not (land_review.SRC_ORDINARY | land_review.SRC_SENSITIVE) - listed, "no longer in src/poolhouse"


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


GUARDED = [
    "AGENTS.md", "CLAUDE.md", ".claude/settings.json", ".claude/agents/worker.md", "scripts/hooks/pre-push",
    "scripts/hooks/weakened-assertions", "scripts/hooks/claude-bash-guard", "src/poolhouse/workspace/person_store.py",
    "src/poolhouse/worktreerules.py", "scripts/land_board.py", "src/poolhouse/authority.py", "src/poolhouse/person.py",
    "src/poolhouse/real_machine.py", "src/poolhouse/board/credentials.py", "src/poolhouse/workspace/authority_cli.py",
    "scripts/agent_sandbox_profile.py", "scripts/github-protection", ".github/workflows/ci.yml"]
NAMED_IN_REVIEW = [
    "src/poolhouse/memory/vault.py", "src/poolhouse/memory/store.py", "src/poolhouse/hub/admission.py", "src/poolhouse/hub/members.py",
    "src/poolhouse/hub/memberbook.py", "src/poolhouse/hub/transfer.py", "src/poolhouse/hub/remote.py", "src/poolhouse/api/leases.py",
    "src/poolhouse/api/pool.py", "src/poolhouse/api/_node.py", "src/poolhouse/cli/daemon.py", "src/poolhouse/cli/wsl_daemon.py",
    "src/poolhouse/data/redteam/styles.json", "src/poolhouse/data/profiles.json", "src/poolhouse/trees.py",
    "src/poolhouse/home.py", "src/poolhouse/paths.py", "src/poolhouse/decide/guards/scope.py",
    "src/poolhouse/decide/guard.py", "src/poolhouse/testing/injection_corpus.py", "src/poolhouse/features.py",
    "src/poolhouse/platform.py", "tests/test_redteam_board.py", "tests/test_bash_guard.py", "tests/test_authority.py",
    "tests/authority_kit.py", "tests/network_guard.py", "tests/test_keystore.py"]


@pytest.mark.parametrize("path", GUARDED)
def test_a_guarded_path_needs_review(path):
    assert land_review.sensitive(path)


@pytest.mark.parametrize("path", NAMED_IN_REVIEW)
def test_security_code_listed_as_ordinary_in_review_needs_review(path):
    assert land_review.sensitive(path)


@pytest.mark.parametrize("path", [
    "docs/notes/example.md", "HANDOFF.md", "CHANGELOG.md", ".task-report.md", ".task.patch", "tests/test_example.py", "src/poolhouse/graph/example.py",
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


AREA_STEMS = ("keystore", "keyring", "keychain", "crypt", "vault", "secret", "credential", "grant", "authorit", "lease", "broker",
              "guard", "redteam", "sentinel", "sandbox", "admission", "identit", "seal", "inject", "token", "verif")
AREA_WORDS = frozenset({"aes", "sign", "signed", "signing", "signature", "signatures", "serve", "server", "servers", "serving"})
PRIMITIVES = frozenset({"keyring", "hmac", "cryptography", "nacl"})
FLOOR_MODULES = frozenset({"authority", "keystore", "keystore_guard", "credentials", "real_machine", "person", "guard", "redteam",
                           "sealing", "secretpaths"})


def area_word(rest: str) -> str:
    """The word of AGENTS.md section 1 (keystore, crypto, grant, authority, identity, lease, serve, guard, red team) that ``rest`` carries."""
    for segment in re.split(r"[^a-z0-9]+", rest.casefold()):
        if segment in AREA_WORDS or segment.startswith(AREA_STEMS):
            return segment
    return ""


def imported_areas(path: str) -> set[str]:
    """The cryptography and floor modules that the Python file ``path`` imports."""
    found: set[str] = set()
    for node in ast.walk(ast.parse((ROOT / path).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            modules = [node.module or "", *[f"{node.module}.{alias.name}" for alias in node.names]]
        else:
            continue
        for module in modules:
            parts = module.split(".")
            found |= {parts[0]} & PRIMITIVES
            if parts[0] == "poolhouse" and len(parts) > 1:
                found |= {parts[1]} & FLOOR_MODULES
    return found


def test_no_library_file_named_for_a_security_area_is_ordinary():
    names = [n for n in git(ROOT, "ls-files", "src/poolhouse").splitlines() if not land_review.sensitive(n)]
    assert not [n for n in names if area_word(n.removeprefix("src/poolhouse/"))]


def test_no_library_module_that_imports_cryptography_or_a_floor_module_is_ordinary():
    names = [n for n in git(ROOT, "ls-files", "src/poolhouse").splitlines() if n.endswith(".py") and not land_review.sensitive(n)]
    assert not {n: imported_areas(n) for n in names if imported_areas(n)}


def test_no_test_file_named_for_a_security_area_is_ordinary():
    names = [n for n in git(ROOT, "ls-files", "tests").splitlines() if not land_review.sensitive(n)]
    assert not [n for n in names if area_word(n.rpartition("/")[2])]
