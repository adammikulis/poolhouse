"""The session wrapper of the isolated launcher: the barrier before the final capture, the kept stage of a re-launch, the environment links, the
patch directory under the real Seatbelt, and the Linux bind hook. Real processes, real files, real git; no model."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

from poolhouse import sandbox
from poolhouse.isolation import toolchain, workspace
from poolhouse.isolation.workspace import PATCHES_AT
from poolhouse.sandbox import Policy, bubblewrap, process_tree
from tests.isolation_world import Sink, begin, linked_worktree, make_project, private_storage, write
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
        # one open file overwritten in place: a writer ended between a truncation and its write would leave an empty file
        held = os.open(os.path.join(stage, "src", "grow.py"), os.O_WRONLY | os.O_CREAT, 0o644)
        os.pwrite(held, b"n = 0\\n", 0)
        print(os.getpid(), flush=True)
        count = 0
        while True:
            os.pwrite(held, b"n = %d\\n" % count, 0)
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


@pytest.mark.parametrize("how", ["a survivor", "written while read", "failed and unread"])
def test_a_publication_that_does_not_run_loses_no_work_of_a_session_no_relaunch_takes_up(project, tmp_path, monkeypatch, how):
    session = begin(project, tmp_path, Sink())
    write(session, "src/more.py", "m = 1\n")
    stage = session.stage.physical

    def moving(*_given):
        raise RuntimeError("PLUMBING: the stage was written while it was read")

    if how != "a survivor":
        monkeypatch.setattr(session.workspace, "observe", moving)
    if how == "failed and unread":
        turn = session.discard(None, stalled=1.0, keep_patch=True)
    else:
        survivors = (os.getpid(),) if how == "a survivor" else ()
        turn = session.finish(None, stalled=1.0, ended=process_tree.Ended(survivors=survivors))
    assert turn.deferred and not turn.applied and not (project / "src" / "more.py").exists()
    if how == "a survivor":
        assert turn.withheld == ("src/more.py",) and b"m = 1" in Path(turn.patch.path).read_bytes() and turn.patch.path in turn.text, turn
    else:
        assert (stage / "src" / "more.py").read_text() == "m = 1\n" and str(stage) in turn.text and str(stage) in turn.deferred, turn


def test_a_relaunch_of_the_same_conversation_reuses_the_kept_stage_and_a_file_refused_before_is_not_reported_again(project, tmp_path):
    first_sink = Sink()
    first = begin(project, tmp_path, first_sink, conversation="c1")
    write(first, "opencode.json", "{}\n")
    write(first, "data/table.json", '{"rows": [3]}\n')
    write(first, "scratch.log", "marker\n")
    turn = first.publish_turn()
    assert turn.applied == ("data/table.json",) and [each.path for each in turn.refused] == ["opencode.json"] and len(first_sink.posts) == 1
    first.finish(None, stalled=1.0, ended=process_tree.Ended())
    second_sink = Sink()
    second = begin(project, tmp_path, second_sink, session="s2", conversation="c1")
    assert (second.stage.physical / "scratch.log").read_bytes() == b"marker\n", "the kept stage is the working copy again, not a rebuild from the master"
    again = second.publish_turn()
    assert again.applied == () and [(each.path, each.klass) for each in again.refused] == [("opencode.json", "settings")]
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


def environments(root, home="/usr/bin"):
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "pyvenv.cfg").write_text(f"home = {home}\n")
    (root / "node_modules").mkdir()


def linked(project, tmp_path):
    """What a launch in ``project`` links, by name, with the folder each link leads to, and the session's first line."""
    session = begin(project, tmp_path, Sink())
    try:
        found = {name: held.path for name, held in session.links}
        assert set(session.reads) == set(found.values())
        assert {name: str((session.stage.physical / name).readlink()) for name in found} == found
        return found, session.first_line()
    finally:
        session.discard(None, stalled=1.0)


def test_a_worktree_with_no_environment_of_its_own_borrows_the_main_checkouts_and_declares_its_interpreter(project, tmp_path, monkeypatch):
    account = tmp_path / "account"
    interpreter = account / ".pyenv" / "versions" / "3.99.0"
    (interpreter / "bin").mkdir(parents=True)
    (interpreter / "bin" / "python3").write_text("#!/bin/sh\n")
    (interpreter / "bin" / "python3").chmod(0o755)
    monkeypatch.setattr(toolchain.home, "user_home", lambda: account.resolve())
    environments(project, home=str(interpreter.resolve() / "bin"))
    worktree = linked_worktree(project, tmp_path / "worktree")
    found, _ = linked(worktree, tmp_path)
    assert found == {".venv": str((project / ".venv").resolve()), "node_modules": str((project / "node_modules").resolve())}
    assert toolchain.declared(worktree) == [str(interpreter.resolve())], "the borrowed environment's interpreter is declared"
    assert not os.path.lexists(worktree / ".venv") and not os.path.lexists(worktree / "node_modules"), "nothing is made in the worktree"
    (worktree / "venv").mkdir()
    found, _ = linked(worktree, tmp_path)
    assert found == {"venv": str(worktree / "venv"), "node_modules": str((project / "node_modules").resolve())}, "its own Python environment is used"


def test_a_worktrees_own_link_never_chooses_the_folder_it_borrows(project, tmp_path):
    environments(project)
    worktree = linked_worktree(project, tmp_path / "worktree")
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (worktree / ".venv").symlink_to(foreign)
    found, _ = linked(worktree, tmp_path)
    assert found[".venv"] == str((project / ".venv").resolve()), "the link is not followed: git's records choose the folder"


@pytest.mark.parametrize("forgery", ["a pointer into another worktree's records", "no record back", "a record back to another folder",
                                     "another common directory", "a linked git directory", "a linked environment",
                                     "an environment that holds a credential", "a repository inside the folder"])
def test_only_a_worktree_git_shows_both_ways_is_given_the_main_checkouts_environment(project, tmp_path, forgery):
    environments(project)
    worktree = linked_worktree(project, tmp_path / "worktree")
    assert linked(worktree, tmp_path)[0], "the genuine worktree borrows"
    records = project / ".git" / "worktrees" / "worktree"
    target = worktree
    if forgery == "a pointer into another worktree's records":
        target = tmp_path / "attacker"
        target.mkdir()
        (target / ".git").write_text(f"gitdir: {records.resolve()}\n")
        (target / "README.md").write_text("# forged\n")
    elif forgery == "no record back":
        (records / "gitdir").unlink()
    elif forgery == "a record back to another folder":
        (records / "gitdir").write_text(f"{tmp_path.resolve() / 'elsewhere' / '.git'}\n")
    elif forgery == "another common directory":
        other = make_project(tmp_path / "other")
        (records / "commondir").write_text(f"{(other / '.git').resolve()}\n")
    elif forgery == "a linked git directory":
        (tmp_path / "alias").symlink_to(project / ".git")
        (worktree / ".git").write_text(f"gitdir: {tmp_path.resolve() / 'alias' / 'worktrees' / 'worktree'}\n")
    elif forgery == "a linked environment":
        shutil.move(project / ".venv", tmp_path / "moved-venv")
        (project / ".venv").symlink_to(tmp_path / "moved-venv")
        shutil.rmtree(project / "node_modules")
    elif forgery == "an environment that holds a credential":
        (project / ".venv" / "credentials.json").write_text('{"canary": "NOT-A-REAL-CREDENTIAL"}\n')
        shutil.rmtree(project / "node_modules")
    else:
        inner = worktree / "inner"
        records = inner / ".git" / "worktrees" / "forged"
        records.mkdir(parents=True)
        (records / "gitdir").write_text(f"{worktree / '.git'}\n")
        (records / "commondir").write_text("../..\n")
        environments(inner)
        (worktree / ".git").write_text(f"gitdir: {records}\n")
    try:
        found, line = linked(target, tmp_path)
    except (OSError, RuntimeError):
        return   # a folder whose git records are forged may not be staged at all, which gives it nothing either
    assert found == {}, f"{forgery}: {found}"
    if forgery == "an environment that holds a credential":
        assert ".venv (holds a credential file" in line, line


def test_the_restore_directory_is_in_no_grant_of_the_launchers_profile(project, tmp_path):
    from poolhouse.isolation.profile import build
    from poolhouse.isolation.spec import Spec

    session = begin(project, tmp_path, Sink())
    write(session, "notes/todo.md", "changed\n")
    assert session.publish_turn().restored, "a restore copy exists"
    profile = build(Spec("claude", str(session.stage.physical), str(tmp_path / "home"), str(tmp_path / "install"), 1, patches=session.patches))
    granted = (*profile.policy.read, *profile.policy.write)
    restored = str(session.restores.directory)
    assert session.patches in granted, "the session reads its refused patches"
    assert not [path for path in granted if restored == path or restored.startswith(path.rstrip("/") + "/") or path.startswith(restored)], "and never the kept versions"


COMMAND_LINE_PYTHON = Path("/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/Current")
"""Apple's own framework build of Python, which the command-line developer tools install."""


def test_the_proof_passes_with_a_framework_interpreter_outside_the_system_trees(tmp_path, monkeypatch, seatbelt):  # noqa: F811
    """A framework build's `python` hands on to the app binary beside it, as the Python of python.org's installer and of a CI runner does. The one
    used here is Apple's, with the system toolchain taken out of the profile, so only the interpreter's own grants reach it."""
    from poolhouse.isolation import profile, proof
    from tests.isolation_kit import world

    if not COMMAND_LINE_PYTHON.is_dir():
        pytest.skip("this computer has no command-line developer tools with a framework build of Python")
    version = os.path.realpath(COMMAND_LINE_PYTHON)
    for name, value in (("executable", f"{version}/bin/python3"), ("prefix", version), ("base_prefix", version)):
        monkeypatch.setattr(sys, name, value)
    monkeypatch.setattr(profile, "system_toolchain", lambda: ())
    monkeypatch.setattr(tempfile, "tempdir", None)   # the proof's socket needs a shorter path than this module's private storage
    with world(tmp_path) as w:
        done = proof.run(w.profile, w.services, lan=False)
    assert all(line.ok for line in done.lines), done


def test_the_refused_patch_directory_and_the_environment_links_are_read_only_to_the_session_in_the_stage_mode_and_across_epoch_rotation_under_the_real_seatbelt(project, tmp_path, seatbelt):  # noqa: F811
    (project / ".venv").mkdir()
    (project / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n")
    session = begin(project, tmp_path, Sink())
    write(session, "opencode.json", "{}\n")
    write(session, "notes/todo.md", "changed\n")
    turn = session.publish_turn()
    [(_, name)] = turn.restored
    stage, patches, restored = session.stage.physical, session.patches, session.restores.directory
    confined = sandbox_policy(read=[patches, *session.reads], write=[stage])
    script = (f"cat {patches}/{turn.patch.name} > /dev/null && echo READ_PATCH; echo x > {patches}/forged.patch || echo CANNOT_FORGE; "
              f"echo x > {patches}/{turn.patch.name} || echo CANNOT_REPLACE; cat {stage}/.venv/pyvenv.cfg > /dev/null && echo READ_ENV; "
              f"echo x > {stage}/.venv/planted || echo CANNOT_PLANT; echo y > {stage}/made.txt && echo WROTE_STAGE; ls {project} > /dev/null || echo NO_PROJECT; "
              f"cat {restored}/{name} > /dev/null || echo CANNOT_READ_RESTORED; echo x > {restored}/{name} || echo CANNOT_REPLACE_RESTORED")
    result = sandbox.run(["/bin/sh", "-c", script], confined, via=seatbelt)
    for expected in ("READ_PATCH", "CANNOT_FORGE", "CANNOT_REPLACE", "READ_ENV", "CANNOT_PLANT", "WROTE_STAGE", "NO_PROJECT", "CANNOT_READ_RESTORED", "CANNOT_REPLACE_RESTORED"):
        assert expected in result.stdout, (expected, result.stdout, result.stderr)
    assert (restored / name).read_bytes() == b"todo\n"
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
