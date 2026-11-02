"""Review commands resolve commit IDs and explain current-source review eligibility without granting it."""

import hashlib
import json
import subprocess
from argparse import Namespace

import pytest
from git_world import Project, git

from poolhouse.board.client import Denied
from poolhouse.workspace import landing, landing_cli

pytest_plugins = ["node_kit"]


@pytest.fixture
def scene(tmp_path, workspace_node):
    project = Project(tmp_path)
    actor = workspace_node.member("reviewer")
    sha = git(project.root, "rev-parse", "HEAD")
    return project, workspace_node, actor, sha


def command(scene, value, *, cwd=None, verdict="accept", env=None):
    project, node, actor, _ = scene
    return node.cli("land-review", "reviewed", value, "--verdict", verdict, "--json",
                    who=actor, cwd=cwd or project.root, env=env)


def rows(scene):
    _, node, actor, _ = scene
    return node.session(actor).read_all(kind="landing")


@pytest.mark.parametrize("upper", [False, True])
def test_short_commit_is_resolved_once_and_recorded_as_full_sha(scene, upper):
    _, _, _, sha = scene
    prefix = sha[:12].upper() if upper else sha[:12]
    done = command(scene, prefix)
    assert done.returncode == 0, done.stdout + done.stderr
    result = json.loads(done.stdout)
    assert result["sha"] == sha
    assert [row.fields["sha"] for row in rows(scene)] == [sha]
    assert "another independent review" in result["note"].lower()


@pytest.mark.parametrize("value", ["HEAD", "a^{}", "--not-a-commit", "", "abc", "abcd\n", "0000000"])
def test_non_commit_selectors_fail_before_a_review_is_recorded(scene, value):
    done = command(scene, value)
    assert done.returncode != 0
    assert rows(scene) == []


def test_blob_id_and_commit_blob_prefix_collision_are_refused(scene):
    project, _, _, sha = scene
    for number in range(1_000_000):
        data = f"collision {number}\n".encode()
        digest = hashlib.sha1(f"blob {len(data)}\0".encode() + data, usedforsecurity=False).hexdigest()
        if digest.startswith(sha[:4]):
            break
    else:
        pytest.fail("could not construct the hostile prefix collision")
    made = subprocess.run(["git", "-C", str(project.root), "hash-object", "-w", "--stdin"],
                          input=data, capture_output=True, check=True).stdout.decode().strip()
    assert made == digest
    for prefix in (made[:12], sha[:4]):
        done = command(scene, prefix)
        assert done.returncode != 0, done.stdout
    assert rows(scene) == []


def test_short_id_uses_actual_repository_despite_git_environment(scene, tmp_path):
    _, _, _, sha = scene
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    other = Project(foreign)
    done = command(scene, sha[:12], env={"GIT_DIR": str(other.root / ".git"),
                                      "GIT_WORK_TREE": str(other.root)})
    assert done.returncode == 0, done.stdout + done.stderr
    assert json.loads(done.stdout)["sha"] == sha


def test_short_id_outside_a_repository_fails_but_full_remote_id_still_records(scene, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    assert command(scene, scene[3][:12], cwd=outside).returncode != 0
    assert rows(scene) == []
    done = command(scene, "A" * 40, cwd=outside)
    assert done.returncode == 0, done.stdout + done.stderr
    result = json.loads(done.stdout)
    assert result["sha"] == "a" * 40 and "note" not in result


def test_review_from_another_worktree_does_not_add_a_deployment_note(scene):
    project, _, _, sha = scene
    tree = project.branch("review-tree", {})
    done = command(scene, sha[:12], cwd=tree)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "note" not in json.loads(done.stdout)


def test_existing_independent_accept_suppresses_note_and_reject_stays_explicit(scene):
    _, node, _, sha = scene
    other = node.member("independent")
    landing.review(node.session(other), "reviewed", sha, "accept")
    accepted = command(scene, sha)
    assert accepted.returncode == 0, accepted.stdout
    assert "note" not in json.loads(accepted.stdout)
    landing.review(node.session(other), "reviewed", sha, "reject")
    rejected = command(scene, sha, verdict="reject")
    assert rejected.returncode == 0, rejected.stdout
    assert "rejected" in json.loads(rejected.stdout)["note"]


def test_eligibility_outage_does_not_turn_recorded_review_into_failure(scene, monkeypatch):
    project, node, actor, sha = scene
    monkeypatch.chdir(project.root)
    def unavailable(*_args):
        raise OSError("untrusted backend details")
    monkeypatch.setattr(landing, "accepts", unavailable)
    result = landing_cli.review(Namespace(branch="reviewed", sha=sha, verdict="accept"), node.session(actor))
    assert "unavailable" in result["note"]
    assert "untrusted backend" not in result["note"]
    assert len(rows(scene)) == 1


def test_author_review_refusal_remains_an_error_without_recording_success(scene, monkeypatch):
    project, node, actor, sha = scene
    monkeypatch.chdir(project.root)
    node.session(actor).claim("branch", "reviewed")
    with pytest.raises(Denied):
        landing_cli.review(Namespace(branch="reviewed", sha=sha, verdict="accept"), node.session(actor))
    assert rows(scene) == []


def test_commit_resolution_happens_before_session_connection(scene, monkeypatch, capsys):
    from poolhouse.workspace import cli
    project, _, _, sha = scene
    monkeypatch.chdir(project.root)
    args = cli.COMMANDS.parser().parse_args(["land-review", "reviewed", sha[:12]])
    assert args.sha == sha
    with pytest.raises(SystemExit) as raised:
        cli.COMMANDS.parser().parse_args(["land-review", "reviewed", "HEAD"])
    assert raised.value.code == 2
    assert "HEAD" not in capsys.readouterr().err
    assert rows(scene) == []
