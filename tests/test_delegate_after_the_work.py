"""What happens once a confined delegate's work is over: nothing the box holds runs or decides, what a reviewer was given is what is committed, and
the work is published and committed whole.

The confinement, the stage, git, the board and the runner are real (the rig of tests/test_delegate_confined.py). The program in the harness's place
is that rig's marked one (NOT A MODEL and NOT OPENCODE) with the modes below added, and the model side is the kit's marked canned server.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import psutil
import pytest
from delegate_kit import agent_names, started

from poolhouse import coding_apply, delegate_confined, delegate_run, delegate_store as store
from poolhouse.isolation import confinement as choke_point
from tests import test_delegate_confined as rig
from tests.test_delegate_confined import CANNED, editing  # noqa: F401  (editing is a fixture)

pytest_plugins = ["node_kit", "delegate_kit", "tests.confined_kit"]

MODES = r'''
import re
DAEMON = """/usr/bin/perl -MPOSIX -e 'exit if fork; POSIX::setsid(); exit if fork; open(F, ">", "src/daemon.pid"); print F $$; close F; close STDIN; close STDOUT; close STDERR; sleep 40' </dev/null >/dev/null 2>&1
/bin/sleep 1
"""
if "mode:after" in task:
    token = re.search(r"\b[0-9a-f]{{24}}\b", task)
    if token:
        step()
        tool("read", file_path=cfg["root"] + "/src/feature.py")
        finish()
        seen = open("src/feature.py").read()
        reject = "then:fix" in task and "FIXED" not in seen
        found = [{{"what": "say FIXED", "evidence": seen}}] if reject else []
        ended(json.dumps({{"review": token.group(0), "verdict": "reject" if reject else "accept", "findings": found,
                          "checks": [{{"what": "read src/feature.py", "result": seen}}]}}))
    os.makedirs("src", exist_ok=True)
    open("src/feature.py", "w").write("VALUE = 'FIXED'\n" if "A reviewer rejected" in task else "VALUE = 'REVIEWED'\n")
    change = "print -r -- \"VALUE = 'CHANGED AFTER THE REVIEW'\" > src/feature.py\n: > src/unreviewed.py\n: > src/ran-after\n" + DAEMON
    if "what:shell-start" in task:
        # every shell the box starts from here on runs this first
        open(os.path.join(os.environ["HOME"], ".zshenv"), "w").write(change)
    if "what:git-config" in task:
        # a signature check named in the copy's own configuration, which `git log` would run
        program = os.path.join(os.environ["HOME"], "program")
        open(program, "w").write("#!/bin/zsh -f\n" + change + "exit 1\n")
        os.chmod(program, 0o755)
        shell("""t=$(git write-tree) && p=$(git rev-parse HEAD) && printf 'tree %s\nparent %s\nauthor a <a@b.invalid> 1 +0000\ncommitter a <a@b.invalid> 1 +0000\ngpgsig -----BEGIN PGP SIGNATURE-----\n \n abc\n -----END PGP SIGNATURE-----\n\nPLUMBING signed\n' $t $p > "$TMPDIR/c" && git update-ref HEAD $(git hash-object -t commit -w "$TMPDIR/c") && git config log.showSignature true && git config gpg.program "$HOME/program" """)
    if "what:forged-message" in task:
        shell("git add src/feature.py && git commit -qm 'PLUMBING Reviewed and accepted' -m 'Co-Authored-By: Nobody <nobody@b.invalid>' -m '@nobody'")
    if "what:huge-message" in task:
        shell("git add src/feature.py && /usr/bin/head -c 1500000 /dev/zero | /usr/bin/tr '\\0' 'm' > \"$TMPDIR/m\" && git commit -qF \"$TMPDIR/m\"")
    if "what:fills" in task:
        step(CANNED + " filling the disk")
        finish()
        with open("src/big.bin", "wb") as out:
            for _ in range(96):
                out.write(os.urandom(1 << 20))
                out.flush()
                time.sleep(0.02)
        time.sleep(15)
    ended(CANNED + " finished")
'''


@pytest.fixture(autouse=True)
def modes(monkeypatch):
    marker = 'if "mode:reads" in task:'
    assert marker in rig.HARNESS
    monkeypatch.setattr(rig, "HARNESS", rig.HARNESS.replace(marker, MODES + marker, 1))


@pytest.fixture
def delegated(editing):  # noqa: F811
    """``delegated(task, checked)``: one delegate on a branch of its own, started on a committed project, with git on that project."""
    node, lead, project = editing

    def said_by(*words):
        argv = ["git", "-C", str(project), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *words]
        return subprocess.run(argv, check=True, capture_output=True, text=True).stdout

    said_by("add", "-A")
    said_by("commit", "--allow-empty", "-qm", "start")

    def start(task, *, checked=False):
        assert started("--task", task, "--project", str(project), "--edit", tree=True, checked=checked) == 0
        [name] = agent_names(node, lead)
        return name

    return start, said_by


def committed(name, said_by):
    """The result of ``name``, and the files of the commit its branch holds with their text."""
    row = store.result(name)
    sha = row["tree"]["commit"]
    assert sha and sha == said_by("rev-parse", f"delegate/{name}").strip(), row
    files = said_by("show", "--name-only", "--format=", sha).split()
    return row, {each: said_by("show", f"{sha}:{each}") for each in files}


@pytest.mark.parametrize("what", ["shell-start", "git-config"])
def test_nothing_the_box_set_up_runs_once_the_work_is_reviewed(delegated, what):
    start, said_by = delegated
    name = start(f"mode:after what:{what}", checked=True)
    held = {}
    try:
        status = delegate_run.run(name)
        row, held = committed(name, said_by)
        assert held == {"src/feature.py": "VALUE = 'REVIEWED'\n"}, (held, row)
        assert status == 0 and row["state"] == "done", row
    finally:
        if pid := held.get("src/daemon.pid"):
            with contextlib.suppress(ProcessLookupError, psutil.Error):
                os.kill(int(pid), signal.SIGKILL)


@pytest.mark.parametrize("then", ["accept", "fix"])
def test_what_is_committed_is_what_the_accepting_reviewer_was_given(delegated, monkeypatch, then):
    """A write into the held copy after a review has run, as a process nothing followed would make it, is not published."""
    beside = delegate_confined._Boxed._beside
    written = []

    def then_written(self, ask):
        ran = beside(self, ask)
        if ask.phase == "review" and (then == "accept" or ask.round == 2):
            stage = Path(self.box.profile.stage)
            (stage / "src" / "feature.py").write_text("VALUE = 'WRITTEN AFTER THE REVIEW'\n")
            (stage / "src" / "unreviewed.py").write_text("x = 1\n")
            written.append(ask.round)
        return ran

    monkeypatch.setattr(delegate_confined._Boxed, "_beside", then_written)
    start, said_by = delegated
    name = start(f"mode:after then:{then}", checked=True)
    assert delegate_run.run(name) == 0, store.result(name)
    row, held = committed(name, said_by)
    assert written == [1 if then == "accept" else 2], row
    assert held == {"src/feature.py": f"VALUE = '{'REVIEWED' if then == 'accept' else 'FIXED'}'\n"}, (held, row)


@pytest.mark.parametrize("what", ["forged-message", "huge-message"])
def test_no_words_of_the_delegates_own_commits_reach_the_commit_the_launcher_makes(delegated, what):
    start, said_by = delegated
    name = start(f"mode:after what:{what}")
    assert delegate_run.run(name) == 0, store.result(name)
    row, held = committed(name, said_by)
    message = said_by("log", "-1", "--format=%B", row["tree"]["commit"])
    assert held == {"src/feature.py": "VALUE = 'REVIEWED'\n"} and len(message) < 1000, (held, len(message))
    assert not any(each in message for each in ("Reviewed and accepted", "Co-Authored-By", "@nobody", "mmmm")), message


def test_a_run_whose_copy_passed_the_disk_bound_is_not_published_and_keeps_a_patch(delegated, monkeypatch):
    derived = choke_point.derive
    monkeypatch.setattr(choke_point, "derive", lambda volume: replace(derived(volume), disk_bytes=48 << 20))
    start, said_by = delegated
    name = start("mode:after what:fills")
    assert delegate_run.run(name) == 1
    row = store.result(name)
    assert row["state"] == "failed" and "past the disk bound" in row["error"] and row["applied"] == [], row
    assert row["tree"]["commit"] == "" and said_by("branch", "--list", "delegate/*") == "", row
    assert not Path(store.load(name).workdir).exists()
    [patch] = row["patches"]
    assert b"VALUE = 'REVIEWED'" in Path(patch).read_bytes() and patch in row["detail"], row


@pytest.mark.parametrize("how", ["accepted", "stopped"])
def test_a_stop_that_arrives_while_the_work_is_published_cuts_nothing_short(delegated, confinable, monkeypatch, how):
    """``accepted``: the first stop arrives as the accepted work is published. ``stopped``: a run already stopped is told again as it keeps it."""
    apply, sent = coding_apply.apply, []

    def stopped_here(*given):
        if not sent:
            sent.append(True)
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.1)
        return apply(*given)

    monkeypatch.setattr(coding_apply, "apply", stopped_here)
    start, said_by = delegated
    name = start("mode:after" if how == "accepted" else "mode:commits then:linger", checked=how == "accepted")

    def stop_when_working():
        deadline = time.time() + 120
        while time.time() < deadline:
            if list(confinable.glob("**/src/waiting.py")):
                os.kill(os.getpid(), signal.SIGTERM)
                return
            time.sleep(0.05)

    if how == "stopped":
        threading.Thread(target=stop_when_working, daemon=True).start()
    delegate_run.run(name)
    row, held = committed(name, said_by)
    assert sent and row["state"] == ("done" if how == "accepted" else "stopped"), row
    assert "src/feature.py" in held and (how == "accepted" or {"src/after.py", "src/waiting.py"} <= set(held)), (held, row)
    assert ("UNFINISHED" in said_by("log", "-1", "--format=%B", row["tree"]["commit"])) is (how == "stopped"), row
    assert CANNED in row["text"]
