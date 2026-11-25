"""The session wrapper of the isolated launcher: the barrier before the final capture, the kept stage of a re-launch, the environment links, the
patch directory under the real Seatbelt, and the Linux bind hook. Real processes, real files, real git; no model."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time

import pytest

from poolhouse import sandbox
from poolhouse.isolation import workspace
from poolhouse.isolation.workspace import PATCHES_AT
from poolhouse.sandbox import Policy, bubblewrap, process_tree
from tests.isolation_world import Sink, begin, make_project, private_storage, write
from tests.sandbox_kit import policy as sandbox_policy, running, seatbelt  # noqa: F401


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    private_storage(tmp_path, monkeypatch)


@pytest.fixture
def project(tmp_path):
    return make_project(tmp_path)

WRITER = textwrap.dedent("""
    import os, sys, time
    stage = sys.argv[1]
    if os.fork() == 0:
        os.setsid()
        print(os.getpid(), flush=True)
        count = 0
        while True:
            with open(os.path.join(stage, "src", "grow.py"), "w") as stream:
                stream.write("n = %d\\n" % count)
            count += 1
            time.sleep(0.005)
    time.sleep(600)
""")


def start_harness(stage) -> tuple[subprocess.Popen, process_tree.Tree, int]:
    process = subprocess.Popen([sys.executable, "-c", WRITER, str(stage)], stdout=subprocess.PIPE, text=True)
    tree = process_tree.follow(process.pid)
    descendant = int(process.stdout.readline())
    deadline = time.monotonic() + 10
    while not (stage / "src" / "grow.py").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    return process, tree, descendant


def test_a_setsid_descendant_is_ended_before_the_final_capture(project, tmp_path):
    session = begin(project, tmp_path, Sink())
    process, tree, descendant = start_harness(session.stage.physical)
    process.kill()
    process.wait()
    assert running(descendant), "the harness is gone and a descendant that left its group still writes the stage"
    turn = session.finish(tree, stalled=5.0)
    assert not running(descendant), "the barrier walked the tree and ended it, which a group kill does not reach"
    assert turn.applied == ("src/grow.py",) and not turn.deferred, "the final capture was taken of a stage nothing was writing"
    published = (project / "src" / "grow.py").read_bytes()
    time.sleep(0.1)
    assert (project / "src" / "grow.py").read_bytes() == published and published.startswith(b"n = ")


def test_a_survivor_or_an_incomplete_tree_skips_publication_keeps_the_stage_and_reports_in_the_parents_words(project, tmp_path):
    sink = Sink()
    session = begin(project, tmp_path, sink, conversation="c1")
    write(session, "src/more.py", "m = 1\n")
    outsider = subprocess.Popen(["sleep", "60"])
    try:
        ended = process_tree.end(process_tree.Tree(outsider.pid), stalled=2.0)
        assert not ended.clean and ended.survivors == (outsider.pid,), "a live process whose start was never followed is a survivor"
        turn = session.finish(None, stalled=2.0, ended=ended)
    finally:
        outsider.kill()
        outsider.wait()
    assert not turn.applied and turn.deferred and not (project / "src" / "more.py").exists()
    [(to, kind, subject, body)] = sink.posts
    assert (to, kind, subject) == ("lead", "status", "isolation.report") and turn.text == body and "could not be ended" in body
    assert "?" not in body and str(outsider.pid) not in body
    assert (tmp_path / "state" / "kept").is_dir(), "the unpublished stage is kept for a re-launch of the conversation"
    unfollowed = begin(project, tmp_path, Sink(), session="s2").finish(None, stalled=1.0)
    assert unfollowed.deferred, "a tree nobody followed is never taken as ended"


def test_a_relaunch_of_the_same_conversation_reuses_the_kept_stage_and_a_rule_naming_the_refused_files_exactly_publishes_them_without_rework(project, tmp_path):
    from poolhouse.isolation import publish

    first_sink = Sink()
    first = begin(project, tmp_path, first_sink, conversation="c1")
    write(first, "data/table.json", '{"rows": [3]}\n')
    write(first, "other.json", "{}\n")
    write(first, "src/ok.py", "ok = 1\n")
    write(first, "scratch.log", "marker\n")
    turn = first.publish_turn()
    assert turn.applied == ("src/ok.py",) and {each.path for each in turn.refused} == {"data/table.json", "other.json"} and len(first_sink.posts) == 1
    first.finish(None, stalled=1.0, ended=process_tree.Ended())
    assert (project / "data" / "table.json").read_bytes() == b'{"rows": []}\n'
    second_sink = Sink()
    second = begin(project, tmp_path, second_sink, session="s2", conversation="c1", rules=(publish.bind(project, "data/table.json"),))
    assert (second.stage.physical / "scratch.log").read_bytes() == b"marker\n", "the kept stage is the working copy again, not a rebuild from the master"
    again = second.publish_turn()
    assert again.applied == ("data/table.json",) and (project / "data" / "table.json").read_bytes() == b'{"rows": [3]}\n'
    assert [(each.path, each.klass) for each in again.refused] == [("other.json", "data")], "the file the rule does not name stays refused"
    assert again.report == "folded" and second_sink.posts == [], "and is not reported again"
    assert not list((tmp_path / "state" / "kept").iterdir()), "a kept stage is used once"
    unrelated = begin(project, tmp_path, Sink(), session="s3", conversation="other")
    assert not (unrelated.stage.physical / "scratch.log").exists()


def test_the_environment_folders_are_linked_into_the_stage_and_read_only_by_exact_directory(project, tmp_path):
    (project / ".venv" / "bin").mkdir(parents=True)
    (project / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (project / "node_modules").mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (project / "venv").symlink_to(outside)
    session = begin(project, tmp_path, Sink())
    stage = session.stage.physical
    assert (stage / ".venv").readlink() == (project / ".venv").resolve() and (stage / "node_modules").readlink() == (project / "node_modules").resolve()
    assert not os.path.lexists(stage / "venv"), "a folder that is itself a link gets nothing"
    assert set(session.reads) == {str((project / ".venv").resolve()), str((project / "node_modules").resolve())}
    assert not session.publish_turn().applied, "a link to an ignored folder is not source content and no capture sees it"
    assert session.first_line() == ""


def test_the_refused_patch_directory_and_the_environment_links_are_read_only_to_the_session_in_the_stage_mode_and_across_epoch_rotation_under_the_real_seatbelt(project, tmp_path, seatbelt):  # noqa: F811
    (project / ".venv").mkdir()
    (project / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n")
    session = begin(project, tmp_path, Sink())
    write(session, "evil.json", "{}\n")
    turn = session.publish_turn()
    stage, patches = session.stage.physical, session.patches
    confined = sandbox_policy(read=[patches, *session.reads], write=[stage])
    script = (f"cat {patches}/{turn.patch.name} > /dev/null && echo READ_PATCH; echo x > {patches}/forged.patch || echo CANNOT_FORGE; "
              f"echo x > {patches}/{turn.patch.name} || echo CANNOT_REPLACE; cat {stage}/.venv/pyvenv.cfg > /dev/null && echo READ_ENV; "
              f"echo x > {stage}/.venv/planted || echo CANNOT_PLANT; echo y > {stage}/made.txt && echo WROTE_STAGE; ls {project} > /dev/null || echo NO_PROJECT")
    result = sandbox.run(["/bin/sh", "-c", script], confined, via=seatbelt)
    for expected in ("READ_PATCH", "CANNOT_FORGE", "CANNOT_REPLACE", "READ_ENV", "CANNOT_PLANT", "WROTE_STAGE", "NO_PROJECT"):
        assert expected in result.stdout, (expected, result.stdout, result.stderr)
    assert not (project / ".venv" / "planted").exists() and sorted(path.name for path in session.kept.directory.iterdir()) == [turn.patch.name]
    rotated = session.workspace.begin()
    assert rotated.alias.resolve() != stage.resolve() and os.path.realpath(session.patches) == patches, "the patch directory does not depend on the stage"
    assert sandbox.run(["/bin/sh", "-c", f"cat {patches}/{turn.patch.name}"], sandbox_policy(read=[patches]), via=seatbelt).ok


def test_the_linux_bind_hook_binds_the_stage_at_the_project_path_and_the_patches_read_only_at_a_fixed_path(project, tmp_path):
    session = begin(project, tmp_path, Sink())
    stage, project_path = str(session.stage.physical), str(project.resolve())
    assert session.binds() == ((stage, project_path, True), (session.patches, PATCHES_AT, False)) == workspace.binds(project, stage, session.patches)
    held = Policy("bound", binds=session.binds())
    arguments = bubblewrap.arguments(held, "/bin/true", "")
    at = arguments.index("--bind", arguments.index(stage) - 1)
    assert arguments[at:at + 3] == ["--bind", stage, project_path]
    assert arguments[arguments.index("--ro-bind", arguments.index(session.patches) - 1):][:3] == ["--ro-bind", session.patches, PATCHES_AT]
    if sys.platform != "linux":
        with pytest.raises(sandbox.PolicyError, match="Linux"):
            held.validated()
