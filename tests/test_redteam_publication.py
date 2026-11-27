"""Publication, attacked: what a session leaves in its stage must never make a path of the checkout lead out of the project, write a credential
name under another spelling, or undo a mode the person set meanwhile. Real project, real stage, real git, real links and real files."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from tests.isolation_world import Sink, begin, make_project, private_storage, write

MARKER = "the person's file outside the project\n"
DEEP = "../q1/q2/q3/q4/.claude"


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    private_storage(tmp_path, monkeypatch)


@pytest.fixture
def project(tmp_path):
    root = make_project(tmp_path)
    (root / "s").mkdir()
    (root / "s" / "keep.py").write_text("k = 1\n")
    (tmp_path / "outside_marker").write_text(MARKER)
    return root


def link(root, name: str, target: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        path.unlink()
    path.symlink_to(target)


def leaving(project) -> list[str]:
    """The links of the checkout that the filesystem itself resolves to a place outside it."""
    real = os.path.realpath(project)
    return sorted(path.relative_to(project).as_posix() for path in project.rglob("*")
                  if path.is_symlink() and not (os.path.realpath(path) + "/").startswith(real + "/"))


def untouched(project, tmp_path, name: str) -> None:
    """A write through ``name`` of the checkout does not reach the file outside it."""
    if (project / name).is_symlink() and (project / name).exists():
        (project / name).write_text("written through the link\n")
    assert (tmp_path / "outside_marker").read_text() == MARKER


def test_a_refused_retarget_does_not_hide_where_another_new_link_leads(project, tmp_path):
    session = begin(project, tmp_path, Sink())
    link(session.stage.physical, "s/d", "..")
    assert session.publish_turn().applied == ("s/d",)
    link(session.stage.physical, "s/d", DEEP)
    link(session.stage.physical, "s/a", "d/../outside_marker")
    turn = session.publish_turn()
    assert {each.path for each in turn.refused} == {"s/a", "s/d"} and not turn.applied, turn
    assert leaving(project) == []
    untouched(project, tmp_path, "s/a")


def test_a_new_link_is_followed_through_the_persons_own_link_and_not_through_what_the_stage_holds(project, tmp_path):
    session = begin(project, tmp_path, Sink())
    link(project, "s/d", "..")
    link(session.stage.physical, "s/d", DEEP)
    link(session.stage.physical, "s/a", "d/../outside_marker")
    turn = session.publish_turn()
    assert "s/a" not in turn.applied and leaving(project) == [], turn
    untouched(project, tmp_path, "s/a")


def test_a_link_whose_change_conflicts_is_evaluated_as_the_person_left_it(project, tmp_path):
    (project / "s" / "deep" / "er").mkdir(parents=True)
    (project / "s" / "deep" / "er" / "f.py").write_text("f = 1\n")
    link(project, "s/d", "deep/er")
    session = begin(project, tmp_path, Sink())
    link(project, "s/d", "..")
    link(session.stage.physical, "s/d", "deep")
    link(session.stage.physical, "s/a", "d/../outside_marker")
    turn = session.publish_turn()
    assert "s/a" not in turn.applied and leaving(project) == [] and (project / "s" / "d").readlink() == Path(".."), turn
    untouched(project, tmp_path, "s/a")


def test_a_published_link_never_reroutes_a_link_of_the_checkout_out_of_the_project(project, tmp_path):
    session = begin(project, tmp_path, Sink())
    link(project, "s/a", "d/../outside_marker")
    assert leaving(project) == []
    link(session.stage.physical, "s/d", "..")
    turn = session.publish_turn()
    assert [(each.path, each.klass) for each in turn.refused] == [("s/d", "link")] and leaving(project) == [], turn
    untouched(project, tmp_path, "s/a")


def test_removing_a_link_never_lets_a_link_of_the_checkout_out_of_the_project(project, tmp_path):
    (project / "s" / "deep" / "er").mkdir(parents=True)
    (project / "s" / "deep" / "er" / "f.py").write_text("f = 1\n")
    link(project, "s/d", "deep/er")
    session = begin(project, tmp_path, Sink())
    link(project, "s/a", "d/../../../outside_marker")
    assert leaving(project) == []
    (session.stage.physical / "s" / "d").unlink()
    turn = session.publish_turn()
    assert [(each.path, each.klass) for each in turn.refused] == [("s/d", "link")] and leaving(project) == [], turn
    untouched(project, tmp_path, "s/a")


def test_a_link_through_another_link_that_stays_inside_in_every_outcome_is_applied(project, tmp_path):
    session = begin(project, tmp_path, Sink())
    link(session.stage.physical, "s/d", "../src")
    link(session.stage.physical, "s/a", "d/app.py")
    turn = session.publish_turn()
    assert sorted(turn.applied) == ["s/a", "s/d"] and not turn.refused and leaving(project) == []
    assert (project / "s" / "a").read_bytes() == b"x = 1\n"


@pytest.mark.parametrize("name", ["id_r\u017fa", ".\u017f\u017fh/config", "ID_R\u017fA"])
def test_a_credential_name_in_a_spelling_the_filesystem_folds_is_refused(project, tmp_path, name):
    session = begin(project, tmp_path, Sink())
    write(session, name, "SECRET\n")
    write(session, "src/new.py", "n = 1\n")
    turn = session.publish_turn()
    assert turn.applied == ("src/new.py",) and [each.klass for each in turn.refused] == ["credential"], turn
    assert not (project / name).exists() and b"SECRET" not in (session.kept.directory / turn.patch.name).read_bytes()


def test_a_link_to_a_credential_name_in_a_folded_spelling_is_refused(project, tmp_path):
    session = begin(project, tmp_path, Sink())
    link(session.stage.physical, "s/key", "../.\u017f\u017fh/config")
    turn = session.publish_turn()
    assert not turn.applied and [(each.path, each.klass) for each in turn.refused] == [("s/key", "link")], turn


def mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.parametrize("started, now", [(0o700, 0o644), (0o644, 0o755)])
def test_the_say_on_running_a_file_that_the_person_changed_meanwhile_is_kept(project, tmp_path, started, now):
    live = project / "src" / "lib.py"
    live.chmod(started)
    session = begin(project, tmp_path, Sink())
    live.chmod(now)
    write(session, "src/lib.py", "y = 'agent'\n")
    turn = session.publish_turn()
    assert turn.conflicts == ("src/lib.py",) and not turn.applied, turn
    assert mode(live) == now and live.read_bytes() == b"y = 2\n"


def test_the_permissions_the_person_set_meanwhile_stay_when_a_change_is_applied(project, tmp_path):
    private, script = project / "src" / "lib.py", project / "src" / "app.py"
    session = begin(project, tmp_path, Sink())
    private.chmod(0o600)
    script.chmod(0o600)
    write(session, "src/lib.py", "y = 'agent'\n")
    (session.stage.physical / "src" / "app.py").chmod(0o755)
    turn = session.publish_turn()
    assert sorted(turn.applied) == ["src/app.py", "src/lib.py"], turn
    assert mode(private) == 0o600 and private.read_bytes() == b"y = 'agent'\n"
    assert mode(script) == 0o700, "the agent's say to run it is applied to the person's permissions"
