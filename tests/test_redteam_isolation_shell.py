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
from dataclasses import dataclass, field
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from poolhouse import coding_policy, harnesshook, isolation, sandbox
from poolhouse.isolation.notices import Notice
from poolhouse.isolation.reports import Reporter
from tests.isolation_kit import MODEL, Canned
from tests.isolation_world import Sink, make_project
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


@pytest.fixture
def box(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Canned)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield Box(tmp_path, make_project(tmp_path).resolve(), server.server_port)
    finally:
        server.shutdown()
        server.server_close()


def shelled(box: Box, script: str, *, look: Callable[[isolation.Confinement], object] = lambda _: None, conversation: str = ""):
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
        return isolation.Rendering((str(program),), {f"session/{event}": context.hook(event) + "\n" for event in ("pre", "stop")})

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


def test_a_kept_stage_that_is_launched_again_still_reads_the_environment(box):
    venv = with_environments(box)
    out, _, first = shelled(box, "printf 'all:\\n\\ttrue\\n' > Makefile\n", conversation="again")
    assert first.turn.refused, out
    out, _, second = shelled(box, "python -c 'import pytest, sys; print(sys.prefix)'\n", conversation="again")
    assert str(venv) in out and not [each for each in second.turn.refused if each.path != "Makefile"], out


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
            cp /bin/ls ./a-binary && chmod +x ./a-binary; tried stage-binary './a-binary'
            printf '#!/bin/sh\\necho ran\\n' > a-script && chmod +x a-script; tried stage-script './a-script'
            sh ./a-script
            """)
        assert "OPEN " not in out and out.count("shut ") == 12 and "NOT-A-REAL-KEY" not in out and "ran" in out, out
        assert outsider.poll() is None and boxed.poll() is None and siblings[0].process.poll() is None
        assert "a-binary" not in confinement.turn.applied and "a-script" not in confinement.turn.applied, confinement.turn
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
    assert "shut system-python" in out, f"Apple's /usr/bin/python3 is a shim that does not run in the box (docs/design-isolated-launcher.md): {out}"
    if Path("/opt/homebrew/bin/python3").exists():
        assert "python3=/opt/homebrew/bin/python3" in out, out
