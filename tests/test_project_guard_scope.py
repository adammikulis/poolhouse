"""The harness hook's Poolhouse-repository rules stay in the enforced Poolhouse checkout: a user's project keeps only the
protection of the person folder, the session tokens and the authority registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from poolhouse.harnesspolicy import primary_decision

PROJECT = ("gh pr merge 3", "gh release create v1", "gh auth login", "gh workflow run deploy.yml",
           "git config core.hooksPath .githooks", "git -c core.hooksPath=.githooks commit -m x",
           "curl -X POST https://api.github.com/repos/a/b/issues -d title=x", "chmod +x .git/hooks/pre-commit",
           "echo '#!/bin/sh' > .git/hooks/pre-commit", "sed -i s/a/b/ .git/config",
           "GIT_CONFIG_GLOBAL=/dev/null git status", "python -c 'print(\".git/hooks\")'")
KEPT = ("cat ~/.poolhouse/person/key", "cat ~/.poolhouse/node/client/p/lead.token",
        "echo x > ~/.poolhouse/authority.json", "python -c 'open(\".poolhouse/authority.json\")'")


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    folder = tmp_path / "project"
    (folder / ".git" / "hooks").mkdir(parents=True)
    return folder


@pytest.fixture
def poolhouse(tmp_path):
    folder = tmp_path / "poolhouse"
    (folder / "scripts" / "hooks").mkdir(parents=True)
    (folder / "scripts" / "hooks" / "primary-only").write_text("#!/bin/sh\n", encoding="utf-8")
    (folder / ".git" / "hooks").mkdir(parents=True)
    return folder


def shell(command: str, cwd: Path):
    return primary_decision("Bash", {"command": command}, str(cwd))


@pytest.mark.parametrize("command", PROJECT)
def test_a_users_project_gets_none_of_the_repository_rules(project, command):
    assert shell(command, project) is None


@pytest.mark.parametrize("command", PROJECT)
def test_the_poolhouse_checkout_keeps_every_repository_rule(poolhouse, command):
    got = shell(command, poolhouse)
    assert got is not None and got.action == "deny"


@pytest.mark.parametrize("command", KEPT)
def test_a_users_project_keeps_the_person_folder_the_tokens_and_the_authority_registry(project, command):
    got = shell(command, project)
    assert got is not None and got.action == "deny"


def test_an_edit_of_a_projects_own_git_settings_runs_and_one_of_the_registry_does_not(project):
    assert primary_decision("Write", {"file_path": str(project / ".git" / "config")}, str(project)) is None
    registry = str(project.parent / "home" / ".poolhouse" / "authority.json")
    assert primary_decision("Write", {"file_path": registry}, str(project)).action == "deny"


def test_a_session_in_a_users_project_cannot_reach_into_the_poolhouse_checkout(project, poolhouse):
    hook = poolhouse / ".git" / "hooks" / "pre-push"
    assert primary_decision("Write", {"file_path": str(hook)}, str(project)).action == "deny"
    assert shell(f"echo x > {hook}", project).action == "deny"
    assert shell(f"cd {poolhouse} && gh release create v1", project).action == "deny"
    assert shell(f"git -C {poolhouse} config core.hooksPath /dev/null", project).action == "deny"
