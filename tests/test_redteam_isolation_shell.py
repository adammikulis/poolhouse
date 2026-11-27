"""A shell in the box, attacked: what an editing agent can do with a shell of its own, from a real shell in the real confinement.

The launch is the real one (`isolation.prepare`: the stage, both bridges, the proof, the Seatbelt profile, publication). The program in the harness's
place is a shell script that stands in for a hostile agent's commands (it is NOT A MODEL and NOT A HARNESS), and the model server is the visibly
marked plumbing server of the kit, `CANNED-NOT-A-MODEL`: no model behaviour is under test here.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from poolhouse import coding_policy, harnesshook, isolation, opencode, sandbox
from poolhouse.isolation import history, toolchain
from poolhouse.isolation.notices import Notice
from poolhouse.isolation.reports import Reporter
from tests.isolation_kit import MODEL, Canned
from tests.isolation_world import Sink, git, linked_worktree, make_project
from tests.sandbox_kit import running

pytest_plugins = ["tests.confined_kit", "tests.sandbox_kit"]
pytestmark = pytest.mark.usefixtures("confinable", "seatbelt")
TRIED = """tried() { if /bin/sh -c "$2" >/dev/null 2>&1; then echo "OPEN $1"; else echo "shut $1"; fi; }\n"""


@dataclass
class Box:
    """One project, the plumbing model server and what a launch over it says."""

    root: Path
    project: Path
    port: int
    sink: Sink = field(default_factory=Sink)
    asked: list = field(default_factory=list)
    servers: list = field(default_factory=list)
    """The port each launch with OpenCode's rendering names for the harness's own server."""


@pytest.fixture
def box(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Canned)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield Box(tmp_path, make_project(tmp_path).resolve(), server.server_port)
    finally:
        server.shutdown()
        server.server_close()


def shelled(box: Box, script: str, *, look: Callable[[isolation.Confinement], object] = lambda _: None, conversation: str = "",
            brief: str = ""):
    """``script`` run by a real shell as the program of a real launch: what it printed, what ``look`` saw before and after it ran, and the
    closed confinement."""
    program = box.root / "shell-NOT-A-HARNESS.sh"
    program.write_text("#!/bin/sh\n" + TRIED + textwrap.dedent(script))
    program.chmod(0o755)

    def decide(stage: str):
        rail = harnesshook.Rail(coding_policy.Policy(coding_policy.GUARDED), "helper", roots=(stage,))

        def answer(event: str, payload: dict) -> dict:
            box.asked.append((event, payload))
            return harnesshook.confined_pre(payload, rail) if event == "pre" else {}

        return answer

    def render(context: isolation.Context) -> isolation.Rendering:
        files = {f"session/{event}": context.hook(event) + "\n" for event in ("pre", "stop")}
        if not brief:
            return isolation.Rendering((str(program),), files)
        # OpenCode's own config of a delegate, around the shell script that stands in for the harness
        setup = opencode.Setup((context.base_url, MODEL, 4096), "", "", (), agent={"mode": "primary", "system": brief})
        made = opencode.rendering(context, setup, (str(program),), {}, files)
        box.servers.append(int(made.argv[5]))
        return replace(made, argv=(str(program),))   # everything else the box gets for OpenCode, its server's grant included

    plan = isolation.Plan("claude", str(program), str(box.project), coding_policy.Policy(coding_policy.GUARDED), f"http://127.0.0.1:{box.port}",
                          isolation.Lease(MODEL, 4096, 1), "", "helper-sh", decide, lambda verb, arguments: {}, render,
                          Notice(Reporter(box.sink.post), "lead", 8, lambda name, fields: None), conversation=conversation)
    with isolation.prepare(plan) as confinement:
        before = look(confinement)
        process = confinement.popen()
        said: list[bytes] = []
        readers = [threading.Thread(target=lambda pipe=pipe: said.append(pipe.read()), daemon=True) for pipe in (process.stdout, process.stderr)]
        for reader in readers:
            reader.start()
        process.wait(timeout=60)
        confinement.end_processes()   # as the delegate does when its harness has exited: a descendant that holds a pipe ends here
        for reader in readers:
            reader.join(30)
        assert not any(reader.is_alive() for reader in readers), "a process of the session still holds a pipe"
        after = look(confinement)
    return b"".join(said).decode(errors="replace"), (before, after), confinement


def tree_digest(directory: Path) -> dict[str, str]:
    """Every file of ``directory`` by name with a digest of its bytes (a link by its target)."""
    return {str(path.relative_to(directory)): str(path.readlink()) if path.is_symlink() else hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.rglob("*")) if path.is_symlink() or path.is_file()}


def test_the_box_reads_the_stages_git_directory_and_can_not_write_remove_rename_link_or_redirect_it(box):
    out, (before, after), _ = shelled(box, """\
        git status --short >/dev/null && echo "status works"
        echo "changed = 1" >> src/app.py
        git diff --stat | grep -q src/app.py && echo "diff works"
        tried add 'git add src/app.py'
        tried commit 'git -c user.name=a -c user.email=a@example.invalid commit -qm x'
        tried write 'echo x >> .git/config'
        tried create 'echo x > .git/planted'
        tried other-case 'echo x >> .GIT/config'
        tried remove 'rm .git/index'
        tried remove-tree 'rm -r .git'
        tried rename 'mv .git moved'
        tried rename-out 'mv .git/index index.moved'
        tried rename-over 'echo x > other && mv -f other .git/index'
        tried hard-link 'ln .git/index index.link'
        tried link-in 'ln -s /etc/hosts .git/planted-link'
        tried link-over 'ln -sfh src .git/HEAD'
        tried mode 'chmod 666 .git/index'
        tried stage 'mv "$PWD" "$PWD.moved"'
        git status --short | grep -q "src/app.py" && echo "status still works"
        """, look=lambda confinement: tree_digest(confinement.session.stage.physical / ".git"))
    assert "status works" in out and "diff works" in out and "status still works" in out, out
    assert "OPEN " not in out, out
    assert out.count("shut ") == 15, out
    assert before == after and "index" in before, "the git directory the launcher reads after the run is the one it wrote"


def with_environments(box: Box) -> Path:
    """The project given a real virtual environment that holds this interpreter's own pytest (copied: nothing is downloaded), a `node_modules` and
    a second environment folder that holds a credential file, all ignored the way projects ignore them (a folder pattern, which a link is not)."""
    root = box.project
    (root / ".gitignore").write_text("scratch.log\n.venv/\nvenv/\nnode_modules/\n")
    venv = root / ".venv"
    subprocess.run([sys._base_executable, "-m", "venv", "--without-pip", str(venv)], check=True, capture_output=True)
    site = next((venv / "lib").glob("python*")) / "site-packages"
    for name in ("pytest", "_pytest", "py", "pluggy", "iniconfig", "packaging", "pygments"):
        found = Path(importlib.import_module(name).__file__)
        if found.name == "__init__.py":
            shutil.copytree(found.parent, site / name, ignore=shutil.ignore_patterns("__pycache__"))
        else:   # pytest ships `py` as one module at the top of site-packages
            shutil.copy(found, site / found.name)
    (root / "node_modules" / ".bin").mkdir(parents=True)
    (root / "node_modules" / "index.js").write_text("// a module\n")
    (root / "node_modules" / ".bin" / "module-tool").write_text("#!/bin/sh\necho module-tool ran\n")
    (root / "node_modules" / ".bin" / "module-tool").chmod(0o755)
    (root / "venv").mkdir()
    (root / "venv" / "credentials.json").write_text('{"canary": "NOT-A-REAL-CREDENTIAL"}\n')
    return venv


def test_the_projects_environments_are_read_run_and_on_the_path_never_written_and_their_links_are_not_refused_files(box):
    venv = with_environments(box)
    before = tree_digest(venv), tree_digest(box.project / "node_modules")
    script = f"""\
        echo "python=$(command -v python)"
        python -c 'import sys, pytest; print("prefix=" + sys.prefix)'
        python -m pytest tests/test_app.py 2>&1 | tail -n 3
        .venv/bin/python -c 'print("through the link")'
        module-tool
        cat node_modules/index.js
        tried venv-write 'echo x > .venv/planted'
        tried venv-write-real 'echo x > {shlex.quote(str(venv))}/planted'
        tried venv-change 'echo x >> {shlex.quote(str(venv))}/pyvenv.cfg'
        tried venv-remove 'rm {shlex.quote(str(venv))}/pyvenv.cfg'
        tried venv-rename 'mv {shlex.quote(str(venv))} {shlex.quote(str(venv))}.moved'
        tried module-write 'echo x >> node_modules/index.js'
        tried credential 'cat venv/credentials.json'
        tried credential-real 'cat {shlex.quote(str(box.project))}/venv/credentials.json'
        tried project 'cat {shlex.quote(str(box.project))}/README.md'
        """
    out, _, confinement = shelled(box, script)
    assert f"python={venv}/bin/python" in out and f"prefix={venv}" in out, out
    assert "1 passed" in out and "through the link" in out and "module-tool ran" in out and "// a module" in out, out
    assert "OPEN " not in out and out.count("shut ") == 9, out
    assert (tree_digest(venv), tree_digest(box.project / "node_modules")) == before
    turn = confinement.turn
    assert turn.refused == () and turn.applied == () and not box.sink.posts, f"neither a link nor the test run's own files are changes: {turn}"
    assert "venv (holds a credential file" in confinement.session.first_line(), confinement.session.first_line()


def test_a_worktree_runs_the_main_checkouts_environment_read_only_and_imports_its_own_source(box):
    venv = with_environments(box)
    main = box.project
    (main / "src" / "pkg").mkdir()
    (main / "src" / "pkg" / "__init__.py").write_text("WHERE = 'the main checkout'\n")
    git(main, "add", "src/pkg")
    site = next((venv / "lib").glob("python*")) / "site-packages"
    (site / "pkg").mkdir()
    (site / "pkg" / "__init__.py").write_text("WHERE = 'a stale installed copy'\n")
    (site / "editable.pth").write_text(f"{main / 'src'}\n")
    box.project = linked_worktree(main, box.root / "worktree")
    (box.project / "src" / "pkg" / "__init__.py").write_text("WHERE = 'the worktree'\n")
    assert not os.path.lexists(box.project / ".venv")
    before = tree_digest(venv), tree_digest(main / "node_modules")
    script = f"""\
        echo "python=$(command -v python)"
        python -c 'import pkg; print("imports " + pkg.WHERE + " at " + pkg.__file__)'
        .venv/bin/python -c 'import pkg; print("link imports " + pkg.WHERE)'
        (cd tests && python -c 'import pkg; print("elsewhere imports " + pkg.WHERE)')
        python -m pytest tests/test_app.py 2>&1 | tail -n 3
        module-tool
        tried venv-write 'echo x > .venv/planted'
        tried venv-write-real 'echo x > {shlex.quote(str(venv))}/planted'
        tried venv-remove 'rm {shlex.quote(str(venv))}/pyvenv.cfg'
        tried module-write 'echo x >> node_modules/index.js'
        tried credential 'cat {shlex.quote(str(main))}/venv/credentials.json'
        tried main-readme 'cat {shlex.quote(str(main))}/README.md'
        tried main-source 'cat {shlex.quote(str(main))}/src/pkg/__init__.py'
        tried main-git 'cat {shlex.quote(str(main))}/.git/config'
        tried main-list 'ls {shlex.quote(str(main))}'
        tried worktree 'cat {shlex.quote(str(box.project))}/README.md'
        """
    out, (stage, _), confinement = shelled(box, script, look=lambda confinement: str(confinement.session.stage.physical))
    assert f"python={venv}/bin/python" in out, out
    assert f"imports the worktree at {stage}/src/pkg/__init__.py" in out, out
    assert "link imports the worktree" in out and "elsewhere imports the worktree" in out and "1 passed" in out and "module-tool ran" in out, out
    assert "OPEN " not in out and out.count("shut ") == 10, out
    assert (tree_digest(venv), tree_digest(main / "node_modules")) == before
    assert confinement.turn.refused == () and confinement.turn.applied == () and not box.sink.posts, confinement.turn
    assert not os.path.lexists(box.project / ".venv") and not os.path.lexists(box.project / "node_modules"), "the links are never published"


def fresh_worktree(box: Box) -> Path:
    """This kind of project as a delegate finds it: the main checkout has the environment, and a worktree just made from it holds committed
    files and nothing else. Nothing in it is prepared. Returns the main checkout's environment."""
    venv = with_environments(box)
    main = box.project
    (main / "pyproject.toml").write_text('[project]\nname = "pkg"\nversion = "0"\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    (main / "src" / "pkg").mkdir()
    (main / "src" / "pkg" / "__init__.py").write_text("WHERE = 'committed'\n")
    (main / "tests" / "test_where.py").write_text("import pkg\n\n\ndef test_where():\n    assert pkg.WHERE == 'the worktree'\n")
    (main / "docs" / "credentials").mkdir(parents=True)   # an ordinary folder of a name no file is given under
    (main / "docs" / "credentials" / "README.md").write_text("# how credentials are kept\n")
    (main / ".env").write_text("NOT-A-REAL-CREDENTIAL=canary-in-history\n")
    git(main, "add", "-f", ".env")
    git(main, "commit", "-qm", "an older commit that holds a credential file")
    git(main, "add", "pyproject.toml", "src/pkg", "tests/test_where.py", ".gitignore", "docs/credentials")
    git(main, "commit", "-qm", "base")
    box.project = linked_worktree(main, box.root / "worktree")
    (box.project / "src" / "pkg" / "__init__.py").write_text("WHERE = 'the worktree'\n")
    return venv


def test_a_fresh_worktree_with_nothing_in_it_has_its_environment_its_own_source_git_and_its_tests(box):
    """The one test of the whole automatic setup, in the real box: any part of it that regresses fails here."""
    venv = fresh_worktree(box)
    assert sorted(each.name for each in box.project.iterdir() if each.name.startswith((".venv", "venv", "node_modules"))) == []
    script = """\
        python -c 'import pkg; print("imports " + pkg.WHERE)'
        python -m pytest -q -p no:cacheprovider tests/test_where.py 2>&1 | tail -n 3
        git status --short | grep -q 'src/pkg/__init__.py' && echo "status shows the change"
        git diff HEAD | grep -q "+WHERE = 'the worktree'" && echo "diff shows the change"
        git log --format=%s -1 | grep -qx base && echo "log works"
        git show --stat HEAD | grep -q 'src/pkg/__init__.py' && echo "show works"
        git status --short | grep -q '^ D\\|^D ' || echo "nothing looks deleted"
        grep -q "+WHERE = 'the worktree'" .git/poolhouse-review/changes.diff && echo "the review files show it"
        tried commit 'git -c user.name=a -c user.email=a@example.invalid commit -qm x'
        tried git-write 'echo x >> .git/config'
        tried history-credential 'git cat-file -p HEAD:.env'
        """
    out, _, confinement = shelled(box, script)
    assert "canary-in-history" not in out, out
    for expected in ("imports the worktree", "1 passed", "status shows the change", "diff shows the change", "log works", "show works",
                     "nothing looks deleted", "the review files show it", "shut commit", "shut git-write", "shut history-credential"):
        assert expected in out, (expected, out)
    assert confinement.said() == [] and confinement.untested == "", ("the preflight found nothing lacking", confinement.said())
    assert confinement.turn.refused == () and confinement.turn.applied == () and not box.sink.posts, confinement.turn
    assert venv.is_dir() and not os.path.lexists(box.project / ".venv")


def test_the_preflight_reports_at_once_in_the_brief_and_in_the_result_what_the_box_cannot_do(box):
    venv = fresh_worktree(box)
    shutil.rmtree(next((venv / "lib").glob("python*")) / "site-packages" / "_pytest")
    config: list[str] = []

    def look(confinement):
        found = list(confinement.home.rglob(opencode.CONFIG))
        config.extend(each.read_text() for each in found)
        return list(box.sink.posts)

    out, (posted, _), confinement = shelled(box, "true\n", look=look, brief="THE BRIEF\n")
    said = "The preflight could not start this project's tests in the sandbox, so work here is untested"
    assert said in confinement.untested + " ".join(confinement.said()) and "python -m pytest --collect-only" in confinement.untested, (out, confinement.said())
    assert posted and said in posted[0][3], "the launching session is told before the harness starts"
    assert said in config[0] and "THE BRIEF" in config[0], config


def test_the_preflight_finds_an_index_that_names_an_object_the_stage_lacks_which_status_and_diff_do_not_show(box, monkeypatch):
    """The stage as it was before its trees left out credential names: the index keeps `HEAD`'s entry for one, and its bytes are not there."""
    monkeypatch.setattr(history, "_leave_out", lambda *_: None)
    fresh_worktree(box)
    out, _, confinement = shelled(box, "git status --short >/dev/null && git diff HEAD --stat >/dev/null && echo 'status and diff work'\n")
    said = " ".join(confinement.said())
    assert "status and diff work" in out and "git does not show the project" in said and "write-tree" in said and ".env" in said, (out, said)


def test_a_project_with_no_usable_environment_is_told_so_in_the_brief_and_in_the_result(box):
    (box.project / "pyproject.toml").write_text('[project]\nname = "pkg"\nversion = "0"\n')
    shutil.rmtree(box.project / "tests")
    config: list[str] = []

    def look(confinement):
        config.extend(each.read_text() for each in confinement.home.rglob(opencode.CONFIG))

    _, _, confinement = shelled(box, "true\n", look=look, brief="THE BRIEF\n")
    [note] = confinement.said()
    assert note.startswith("This working copy has no Python environment (.venv or venv) the sandbox can use") and note.count(". ") == 0, note
    assert note in confinement.said(), confinement.said()
    assert f"THE BRIEF\\n{note}" in config[0], config
    with_environments(box)
    _, _, confinement = shelled(box, "true\n")
    assert not confinement.said(), confinement.said()


def test_a_kept_stage_that_is_launched_again_still_reads_the_environment(box):
    venv = with_environments(box)
    out, _, first = shelled(box, "echo '{}' > opencode.json\n", conversation="again")
    assert first.turn.refused, out
    out, _, second = shelled(box, "python -c 'import pytest, sys; print(sys.prefix)'\n", conversation="again")
    assert str(venv) in out and not [each for each in second.turn.refused if each.path != "opencode.json"], out


def test_an_environment_link_the_box_replaced_is_reported_and_nothing_of_it_is_written_into_the_environment(box):
    venv = with_environments(box)
    before = tree_digest(venv)
    out, _, confinement = shelled(box, "rm .venv && mkdir -p .venv/bin && echo 'x = 1' > .venv/bin/planted.py && echo replaced\n")
    assert "replaced" in out and tree_digest(venv) == before, out
    assert confinement.turn.applied == () and confinement.turn.refused, confinement.turn


def test_a_shell_in_the_box_reaches_nothing_it_was_not_given(box):
    house = box.root / "person" / ".ssh"
    house.mkdir(parents=True)
    (house / "id_ed25519").write_text("NOT-A-REAL-KEY canary\n")
    bystander = socket.socket()
    bystander.bind(("127.0.0.1", 0))
    bystander.listen(4)
    outsider = subprocess.Popen(["/bin/sleep", "60"])
    boxed = subprocess.Popen(["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)", "/bin/sleep", "60"])
    python = shlex.quote(os.path.realpath(sys.executable))
    siblings: list = []

    def beside(confinement) -> None:
        """Another confinement of the very same policy, as a second delegate of this lead would have: its pid is left in the stage."""
        if not siblings:
            siblings.append(sandbox.spawn(["/bin/sleep", "60"], confinement.profile.policy, cwd=confinement.cwd))
            (confinement.session.stage.physical / "sibling.pid").write_text(str(siblings[0].process.pid))

    try:
        out, _, confinement = shelled(box, look=beside, script=f"""\
            cat {shlex.quote(str(house / "id_ed25519"))} 2>/dev/null; tried canary 'cat {house / "id_ed25519"}'
            tried connect '{python} -I -c "import socket; socket.create_connection((\\"127.0.0.1\\", {bystander.getsockname()[1]}), 2)"'
            tried bind '{python} -I -c "import socket; socket.socket().bind((\\"127.0.0.1\\", 0))"'
            tried outbound '{python} -I -c "import socket; socket.create_connection((\\"192.0.2.1\\", 443), 2)"'
            tried nested "/usr/bin/sandbox-exec -p '(version 1)(allow default)' /usr/bin/true"
            tried signal-outside 'kill -0 {outsider.pid}'
            tried kill-outside 'kill -9 {outsider.pid}'
            tried kill-other-box 'kill -9 {boxed.pid}'
            tried signal-parent 'kill -0 {os.getpid()}'
            tried kill-sibling 'kill -9 "$(cat sibling.pid)"'
            cp {python} ./a-binary && chmod +x ./a-binary; ./a-binary -V >/dev/null && echo "binary ran"
            printf '#!/bin/sh\\ncat "$1"\\n' > a-script && chmod +x a-script; ./a-script README.md >/dev/null && echo "script ran"
            cp a-script "$HOME/a-script" && "$HOME/a-script" README.md >/dev/null && echo "home script ran"
            tried binary-reads-outside 'PYTHONHOME={sys.base_prefix} ./a-binary -c "open(\\"{house / "id_ed25519"}\\").read()"'
            tried script-reads-outside './a-script {shlex.quote(str(house / "id_ed25519"))}'
            tried script-writes-outside 'printf "#!/bin/sh\\necho x > %s\\n" {shlex.quote(str(house / "planted"))} > w && chmod +x w && ./w'
            """)
        assert "OPEN " not in out and out.count("shut ") == 13 and "NOT-A-REAL-KEY" not in out, out
        assert "binary ran" in out and "script ran" in out and "home script ran" in out and not (house / "planted").exists(), out
        assert outsider.poll() is None and boxed.poll() is None and siblings[0].process.poll() is None
        later = {path for path, _ in confinement.turn.runs_later}
        assert {"a-binary", "a-script"} <= set(confinement.turn.applied) & later, "applied, and named as of the kind that runs later"
    finally:
        for each in siblings:
            each.process.kill()
            each.process.wait()
            each.release()
        for each in (outsider, boxed):
            each.kill()
            each.wait()
        bystander.close()


SLEEPER = """\
import os, signal, subprocess, sys, time
def sleeper():
    subprocess.run(["/bin/sh", "-c", f"{sys.executable} -c 'import os, time; os.setsid(); print(os.getpid(), flush=True); time.sleep(90)' > pid &"])
    while not os.path.exists("pid") or not open("pid").read().strip():
        time.sleep(0.05)
    return int(open("pid").read())
grand = sleeper()
try:
    os.kill(grand, signal.SIGKILL)
    print("grandchild ended")
except PermissionError:
    print("grandchild NOT ended")
held = subprocess.Popen([sys.executable, "-c", "import os, time; os.setsid(); print(os.getpid(), flush=True); time.sleep(90)"], stdout=subprocess.PIPE)
print("left", int(held.stdout.readline()), flush=True)
"""


def test_the_box_ends_its_own_grandchildren_and_a_setsid_sleeper_it_leaves_ends_with_the_session(box):
    (box.project / "sleeper.py").write_text(SLEEPER)
    began = time.monotonic()
    out, _, _ = shelled(box, f"{shlex.quote(os.path.realpath(sys.executable))} sleeper.py\n")
    assert "grandchild ended" in out, out
    left = int(out.split("left ", 1)[1].split()[0])
    assert not running(left) and time.monotonic() - began < 80, f"the session's end reaches a sleeper that left its group and holds the pipe: {out}"


def test_a_hook_and_a_tool_result_from_the_box_start_nothing_outside_it(box):
    marker = box.root / "started-outside"
    payload = {"tool_name": "Bash", "tool_input": {"command": "git status"}, "cwd": "EVIL", "hook_event_name": "PreToolUse"}
    out, _, _ = shelled(box, f"""\
        git init -q evil && git -C evil config core.fsmonitor 'touch {marker}' && git -C evil config core.hooksPath hooks && echo armed
        mkdir -p evil/hooks && printf '#!/bin/sh\\ntouch {marker}\\n' > evil/hooks/post-index-change && chmod +x evil/hooks/post-index-change
        echo 'x = 1' > evil/x.py
        printf '%s' {shlex.quote(json.dumps(payload))} | sed "s|EVIL|$PWD/evil|" | /bin/sh "$HOME/session/pre"
        echo '{{}}' | /bin/sh "$HOME/session/stop"
        """)
    assert "armed" in out and "permissionDecision" in out, out
    assert [event for event, _ in box.asked] == ["pre", "stop"], box.asked
    assert not marker.exists(), "nothing the box wrote was run by the launcher"


def test_the_box_looks_up_its_user_and_groups_and_reaches_no_other_directory_service_or_resolver(box):
    """What a test suite asks the system (`getpwuid`, `getgrnam`) answers in the box; the administrative directory service, the keychain and name
    resolution stay refused."""
    python = shlex.quote(os.path.realpath(sys.executable))
    out, _, _ = shelled(box, f"""\
        {python} -I -c 'import grp, os, pwd; print("user", pwd.getpwuid(os.getuid()).pw_uid, "group", grp.getgrnam("staff").gr_name)'
        tried directory '/usr/bin/dscl . -list /Users'
        tried keychain '/usr/bin/security list-keychains'
        tried keychain-item '/usr/bin/security find-generic-password -s poolhouse-proof'
        tried resolver '{python} -I -c "import socket; socket.getaddrinfo(\\"localhost.localdomain.invalid\\", 80)"'
        tried mdns '{python} -I -c "import socket; s = socket.socket(socket.AF_UNIX); s.connect(\\"/private/var/run/mDNSResponder\\")"'
        """)
    assert f"user {os.getuid()} group staff" in out, out
    assert "OPEN " not in out and out.count("shut ") == 5, out


def test_apples_git_runs_for_every_program_of_the_box_whatever_environment_it_is_given(box):
    """A program that names `/usr/bin/git`, or runs `git` with its own short `PATH` or no environment at all, as a test suite does: Apple's
    selector finds the command-line developer tools, with nothing set for it."""
    python = shlex.quote(os.path.realpath(sys.executable))
    out, _, confinement = shelled(box, f"""\
        /usr/bin/env -i /usr/bin/git init -q "$TMPDIR/made" && echo "absolute path runs"
        /usr/bin/env -i PATH=/usr/bin:/bin git -C "$TMPDIR/made" status --short && echo "short path runs"
        {python} -I -c 'import os, subprocess; subprocess.run(["git", "--version"], env={{"PATH": os.defpath}}, check=True)' && echo "subprocess runs"
        /usr/bin/xcrun --find git
        tried selected 'ls /var/db/xcode_select_link/usr'
        tried write-selection 'ln -sfh /tmp /var/db/xcode_select_link'
        """)
    assert all(each in out for each in ("absolute path runs", "short path runs", "subprocess runs", "git version")), out
    assert "/Library/Developer/CommandLineTools/usr/bin/git" in out and "shut write-selection" in out, out
    assert confinement.said() == [], confinement.said()


def test_the_preflight_finds_a_git_that_a_program_with_no_environment_cannot_run(box, monkeypatch):
    monkeypatch.setattr(toolchain, "developer_selection", lambda: ())
    _, _, confinement = shelled(box, "true\n")
    said = " ".join(confinement.said())
    assert "git does not show the project" in said and "env -i" in said and "xcode-select" in said, said


def test_openssl_and_node_start_in_the_box_and_of_the_systems_settings_it_reads_only_openssls_public_files(box):
    """Both end at once when they may not open OpenSSL's configuration, so no certificate was made and no node program ran."""
    files, _ = toolchain.system_settings()
    script = """\
        command -v openssl >/dev/null && openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -keyout "$TMPDIR/k.pem" \\
            -out "$TMPDIR/c.pem" -days 1 -subj /CN=box 2>/dev/null && grep -q CERTIFICATE "$TMPDIR/c.pem" && echo "a certificate is made"
        command -v node >/dev/null && node -e 'console.log("node runs")'
        """
    for each in ("/opt/homebrew/etc", "/usr/local/etc"):
        script += f"""
        tried listing-{each.split('/')[1]} 'ls {each}'
        for folder in {each}/openssl*; do [ -d "$folder/private" ] && tried keys "ls $folder/private"; done
        tried write "echo x >> {files[0] if files else each + '/x'}"
        """
    out, _, confinement = shelled(box, script)
    for program, expected in (("openssl", "a certificate is made"), ("node", "node runs")):
        assert expected in out or not shutil.which(program), out
    assert "OPEN " not in out, out
    assert confinement.said() == [], confinement.said()


def test_what_the_box_has_of_the_systems_programs(box):
    out, _, _ = shelled(box, """\
        tried system-python '/usr/bin/python3 -c pass'
        tried python3 'python3 -c pass'
        echo "python3=$(command -v python3)"
        tried zsh '"$SHELL" -c true'
        echo "quiet-shell=[$("$SHELL" -c -l 'echo ok' 2>&1)]"
        git init -q probe && git -C probe status >/dev/null && echo "git runs"
        """)
    assert "git runs" in out and "OPEN zsh" in out and "OPEN python3" in out and "quiet-shell=[ok]" in out, out
    selected = Path("/Library/Developer/CommandLineTools/usr/bin/python3").is_file()
    assert ("OPEN system-python" in out) == selected, f"Apple's /usr/bin/python3 runs the command-line tools' own (docs/design-isolated-launcher.md): {out}"
    if Path("/opt/homebrew/bin/python3").exists():
        assert "python3=/opt/homebrew/bin/python3" in out, out
