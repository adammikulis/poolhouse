"""Publication, attacked through the person's own links: what a coding tool's settings or git read through a link or a gitdir file of the
checkout is theirs, so no change at or under it is applied. Real project, real stage, real git, real links and real files."""

from __future__ import annotations

import shutil

import pytest

from tests.isolation_world import Sink, begin, make_project, private_storage, write

OWN = "{\"hooks\": \"the person's\"}\n"
PLANTED = "{\"hooks\": \"planted\"}\n"


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    private_storage(tmp_path, monkeypatch)


@pytest.fixture
def project(tmp_path):
    return make_project(tmp_path)


def held(root, name: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(OWN)


def link(root, name: str, target: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        path.unlink()
    path.symlink_to(target)


def published(session, planted: list[str]):
    """The agent plants each of ``planted`` beside one ordinary change, and the turn is published."""
    for name in planted:
        write(session, name, PLANTED)
    write(session, "src/new.py", "n = 1\n")
    return session.publish_turn()


def only_the_ordinary_change_landed(project, turn, planted: list[str]) -> None:
    assert turn.applied == ("src/new.py",), turn
    assert {each.path: each.klass for each in turn.refused} == dict.fromkeys(planted, "reached"), turn
    for name in planted:
        assert not (project / name).exists() or (project / name).read_text() != PLANTED, name


def test_what_the_settings_links_of_the_checkout_reach_is_never_changed(project, tmp_path):
    for name in ("tooling/claude/settings.json", "tooling/mcp.json", "tooling/codex/config.toml", "tooling/oc.json", "ci/run.sh"):
        held(project, name)
    link(project, ".claude", "tooling/claude")
    link(project, ".mcp.json", "tooling/mcp.json")
    link(project, "sub/.codex", "../tooling/codex")
    link(project, "a/b/opencode.json", "../../tooling/oc.json")
    link(project, "tooling/claude/hooks", "../../ci")
    session = begin(project, tmp_path, Sink())
    planted = ["tooling/claude/settings.json", "tooling/claude/settings.local.json", "tooling/mcp.json", "tooling/codex/config.toml",
               "tooling/oc.json", "ci/run.sh"]
    only_the_ordinary_change_landed(project, published(session, planted), planted)
    assert (project / "tooling" / "claude" / "settings.json").read_text() == OWN


def test_a_chain_a_folded_spelling_and_an_absolute_target_are_followed_to_what_they_reach(project, tmp_path):
    held(project, "Tooling/Claude/settings.json")
    held(project, "abs/codex/config.toml")
    link(project, ".claude", "alias")
    link(project, "alias", "tooling/claude")
    link(project, ".codex", str(project.resolve() / "abs" / "codex"))
    link(project, ".pi", "not/yet")
    session = begin(project, tmp_path, Sink())
    planted = ["Tooling/Claude/settings.json", "abs/codex/config.toml", "not/yet/settings.json"]
    only_the_ordinary_change_landed(project, published(session, planted), planted)


def test_a_link_on_the_way_is_never_retargeted_and_what_it_would_reach_is_not_changed(project, tmp_path):
    held(project, "a/claude/settings.json")
    link(project, "shared", "a")
    link(project, ".claude", "shared/claude")
    session = begin(project, tmp_path, Sink())
    link(session.stage.physical, "shared", "b")
    link(session.stage.physical, "way", "a/claude")
    turn = published(session, ["b/claude/settings.json"])
    assert turn.applied == ("src/new.py",), turn
    assert {each.path: each.klass for each in turn.refused} == {"shared": "reached", "b/claude/settings.json": "reached", "way": "link"}, turn
    assert str((project / "shared").readlink()) == "a" and not (project / "b").exists()


def test_a_link_is_never_added_on_the_way_to_what_the_settings_would_reach(project, tmp_path):
    held(project, "real/settings.json")
    link(project, ".claude", "later/claude")
    session = begin(project, tmp_path, Sink())
    link(session.stage.physical, "later", "real")
    turn = session.publish_turn()
    assert [(each.path, each.klass) for each in turn.refused] == [("later", "reached")] and not turn.applied, turn


def test_what_git_reads_through_a_link_or_a_gitdir_file_is_never_changed(project, tmp_path):
    for name in ("githooks/pre-commit", "meta/sub.git/config", "meta/sub.git/hooks/pre-commit", "shared.git/config"):
        held(project, name)
    session = begin(project, tmp_path, Sink())
    shutil.rmtree(project / ".git" / "hooks", ignore_errors=True)
    link(project, ".git/hooks", "../githooks")
    (project / "pointed").mkdir()
    (project / "pointed" / ".git").write_text("gitdir: ../meta/sub.git\n")
    (project / "meta" / "sub.git" / "commondir").write_text("../../shared.git\n")
    planted = ["githooks/pre-commit", "meta/sub.git/config", "meta/sub.git/hooks/pre-commit", "shared.git/config"]
    only_the_ordinary_change_landed(project, published(session, planted), planted)


def test_a_link_to_a_place_outside_the_settings_reach_nothing_of_the_project(project, tmp_path):
    (tmp_path / "dotfiles").mkdir()
    link(project, ".claude", str(tmp_path / "dotfiles"))
    link(project, ".codex", "tooling/codex")
    session = begin(project, tmp_path, Sink())
    turn = published(session, ["tooling/other.json"])
    assert sorted(turn.applied) == ["src/new.py", "tooling/other.json"] and not turn.refused, turn
