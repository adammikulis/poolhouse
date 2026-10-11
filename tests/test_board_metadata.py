"""Metadata-only project registration and the catalogue a device answers."""

import json
from types import SimpleNamespace

import pytest

from poolhouse.files import write_json
from poolhouse.fleet import project_client, project_source as source
from poolhouse.fleet.projects import ProjectRegistry, answer, identity
from poolhouse.net import git


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    git.run(["init"], cwd=root)
    git.run(["remote", "add", "origin", "https://code.example.invalid/team/project.git"], cwd=root)
    return root


def test_metadata_registration_never_exports_source(repository, tmp_path, monkeypatch):
    monkeypatch.setattr(source, "build", lambda *args: pytest.fail("metadata built source"))
    registry = ProjectRegistry(tmp_path / "registry", "device", (repository,))
    identifier = identity(repository)
    assert registry.candidates() == [{"id": identifier, "name": "project"}]
    assert registry.list() == []
    assert registry.catalogue()["projects"] == []
    assert registry.boards() == [{"id": identifier, "name": "project", "machine": "device"}]
    assert not (tmp_path / "registry" / "project-bundles").exists()
    with pytest.raises(source.ProjectError, match="not shared"):
        registry.snapshot(identifier, "")
    assert ProjectRegistry(tmp_path / "registry", "device").list() == []


def test_same_git_origin_has_same_board_identity(repository, tmp_path):
    other = tmp_path / "another-checkout"
    other.mkdir()
    git.run(["init"], cwd=other)
    git.run(["remote", "add", "origin", "git@code.example.invalid:team/project.git"], cwd=other)
    assert identity(other) == identity(repository)
    registry = ProjectRegistry(tmp_path / "registry", "device", (repository, other))
    assert len(registry.boards()) == 1


@pytest.mark.redteam
@pytest.mark.parametrize("project_id", [[], "../escape", "a" * 31, "A" * 32, 7, None, "x" * 2049])
def test_malformed_checkout_identity_is_not_registered(repository, tmp_path, project_id):
    write_json(repository / ".poolhouse-project.json", {"kind": "project-checkout", "project_id": project_id})
    registry = ProjectRegistry(tmp_path / "registry", "device", (repository,))
    assert registry.boards() == []
    assert registry.candidates() == []


def test_checkout_with_a_valid_identity_marker_is_registered_under_it(repository, tmp_path):
    identifier = "a" * 32
    write_json(repository / ".poolhouse-project.json", {"kind": "project-checkout", "project_id": identifier})
    registry = ProjectRegistry(tmp_path / "registry", "device", (repository,))
    assert registry.candidates() == [{"id": identifier, "name": "project"}]
    assert registry.boards() == [{"id": identifier, "name": "project", "machine": "device"}]


@pytest.mark.redteam
def test_checkout_without_an_identity_is_not_registered(repository, tmp_path):
    write_json(repository / ".poolhouse-project.json", {"kind": "project-checkout"})
    registry = ProjectRegistry(tmp_path / "registry", "device", (repository,))
    assert registry.boards() == []
    assert registry.candidates() == []


@pytest.mark.redteam
def test_catalogue_answers_shared_projects_without_checkout_paths_or_unshared_boards(repository, tmp_path):
    registry = ProjectRegistry(tmp_path / "registry", "device", (repository,))
    responses = []
    handler = SimpleNamespace(_send=lambda code, payload: responses.append((code, payload)))
    assert answer(handler, registry, SimpleNamespace(path="/workspace/v1/projects"))
    assert responses[0][0] == 200
    assert responses[0][1]["projects"] == []
    assert set(responses[0][1]) == {"projects", "machine", "capabilities", "bootstrap_sha256"}
    assert str(repository) not in json.dumps(responses[0][1])


@pytest.mark.redteam
def test_catalogue_answer_for_a_shared_project_has_no_checkout_path(repository, tmp_path):
    (repository / "run.sh").write_text("echo hello\n")
    git.run(["add", "--", "run.sh"], cwd=repository)
    git.run(["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
             "commit", "-m", "fixture"], cwd=repository)
    registry = ProjectRegistry(tmp_path / "registry", "device", (repository,))
    shared = registry.share(registry.candidates()[0]["id"])
    responses = []
    handler = SimpleNamespace(_send=lambda code, payload: responses.append((code, payload)))
    assert answer(handler, registry, SimpleNamespace(path="/workspace/v1/projects"))
    assert [row["id"] for row in responses[0][1]["projects"]] == [shared.id]
    assert "root" not in responses[0][1]["projects"][0]
    assert str(repository) not in json.dumps(responses[0][1])


def test_register_native_project_is_metadata_only_and_persistent(repository, tmp_path, monkeypatch):
    monkeypatch.setattr(source, "build", lambda *args: pytest.fail("metadata built source"))
    nested = repository / "nested"
    nested.mkdir()
    identifier = identity(repository)
    registry = ProjectRegistry(tmp_path / "registry", "device")
    assert registry.register(nested, identifier) == {"id": identifier, "name": "project", "machine": "device"}
    assert registry.get(identifier).root == str(repository)
    assert registry.list() == []
    assert not (registry.root / "project-bundles").exists()
    with pytest.raises(source.ProjectError, match="not shared"):
        registry.snapshot(identifier, "")
    assert registry.register(repository, identifier) == {"id": identifier, "name": "project", "machine": "device"}
    loaded = ProjectRegistry(registry.root, "device")
    assert loaded.get(identifier).root == str(repository)
    assert loaded.list() == []


@pytest.mark.redteam
def test_native_registration_refuses_identity_mismatch_before_mutation(repository, tmp_path):
    registry = ProjectRegistry(tmp_path / "registry", "device")
    with pytest.raises(source.ProjectError, match="does not match"):
        registry.register(repository, "a" * 32)
    assert registry.boards() == []
    assert registry.candidates() == []
    assert not registry.path.exists()


@pytest.mark.redteam
@pytest.mark.parametrize("root", ["relative", "/" + "x" * 2048, "/bad\npath"])
def test_native_registration_refuses_unbounded_or_relative_path(tmp_path, root):
    registry = ProjectRegistry(tmp_path / "registry", "device")
    with pytest.raises(source.ProjectError, match="absolute local project path"):
        registry.register(type(tmp_path)(root), "a" * 32)
    assert registry.boards() == []
    assert not registry.path.exists()


def test_person_workspace_catalogue_includes_every_unshared_local_project(repository, tmp_path, monkeypatch):
    registry = ProjectRegistry(tmp_path / 'registry', 'device', (repository,))
    identifier = identity(repository)
    monkeypatch.setattr(project_client, 'members', lambda ui: [])
    monkeypatch.setattr(source, 'build', lambda *args: pytest.fail('workspace catalogue published source'))
    result = project_client.available(SimpleNamespace(projects=registry))
    assert registry.get(identifier).shared is False
    assert result['projects'] == result['local'] == []
    assert registry.catalogue()['projects'] == []
    assert result['workspaces'] == [{'id': identifier, 'name': 'project', 'machine': 'device', 'is_self': True}]
    assert not (tmp_path / 'registry' / 'project-bundles').exists()


def test_registry_loads_projects_saved_with_fields_it_does_not_know(tmp_path):
    identifier = "a" * 32
    write_json(tmp_path / "projects.json", {"projects": [{
        "id": identifier, "name": "old", "root": str(tmp_path), "source_machine": "device",
        "authority_machine": "retired", "board_host": "https://device:8770"}]})
    registry = ProjectRegistry(tmp_path, "device")
    assert registry.get(identifier).name == "old"
    assert registry.boards() == [{"id": identifier, "name": "old", "machine": "device"}]
