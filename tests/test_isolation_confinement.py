"""The choke point, attacked: `isolation.prepare` and `Confinement.popen` against a real stage, the real Seatbelt, real sockets and real git.

The program the confinement runs is the attacker's: a shell script that stands in for a coding harness's tools and tries what a hostile model would
(it is not a model, and says so). The model server is the visibly marked plumbing server of the kit, `CANNED-NOT-A-MODEL`: no model behaviour is under
test here. Each test names the property: a plain file reaches the checkout, a governing file and a hook do not, the person's home and the network are
refused, the environment is the allow-list, a computer that cannot confine refuses before anything exists, and a harness that is not inside the
profile is ended.
"""

from __future__ import annotations

import ast
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
from dataclasses import dataclass, field
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from poolhouse import coding_policy, isolation
from poolhouse.isolation import confinement as launch
from poolhouse.isolation.environment import allowed
from poolhouse.isolation.grants import Grant
from poolhouse.isolation.notices import Notice
from poolhouse.isolation.proof import run as run_proof
from poolhouse.isolation.refusals import SENTENCES, IsolationFailed, IsolationRefused
from poolhouse.isolation.reports import Reporter
from poolhouse.sandbox.seatbelt import Seatbelt
from tests.isolation_kit import MODEL, Canned, without_a_sandbox
from tests.isolation_world import Sink, make_project, private_storage
from tests.sandbox_kit import AbsentBackend

pytest_plugins = ["tests.sandbox_kit"]
engine = sys.modules["poolhouse.sandbox.run"]
SRC = Path(__file__).resolve().parent.parent / "src" / "poolhouse"


@dataclass
class Rig:
    project: Path
    sink: Sink
    port: int
    person: Path
    bystander: socket.socket
    logs: list = field(default_factory=list)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A project, a person's home with a key in it, a loopback listener that is not the bridge, and the plumbing model server."""
    private_storage(tmp_path, monkeypatch)
    monkeypatch.setenv("POOLHOUSE_TEST_CANARY", "a launcher variable no harness may see")
    def prove(profile, services):
        """The proof's canary sockets need a short path, which the private storage of the stage, in a test, is not."""
        kept, tempfile.tempdir = tempfile.tempdir, None
        try:
            return run_proof(profile, services, lan=False)
        finally:
            tempfile.tempdir = kept

    monkeypatch.setattr(launch, "run_proof", prove)
    person = tmp_path / "person"
    (person / ".ssh").mkdir(parents=True)
    (person / ".ssh" / "id_ed25519").write_text("-----BEGIN NOT A REAL KEY-----\n")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Canned)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    bystander = socket.socket()
    bystander.bind(("127.0.0.1", 0))
    bystander.listen(4)
    try:
        yield Rig(make_project(tmp_path), Sink(), server.server_port, person, bystander)
    finally:
        server.shutdown()
        server.server_close()
        bystander.close()


def hostile(rig: Rig, tmp_path: Path) -> Path:
    """The attacker's program: it writes a plain file, a CI workflow, a build file and a git hook; reads the person's key; reaches the loopback
    listener that is not the bridge and the bridge; and prints what each did. NOT A MODEL, NOT A HARNESS."""
    script = tmp_path / "hostile-NOT-A-HARNESS.sh"
    python = os.path.realpath(sys.executable)
    script.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        echo 'def added():\\n    return 1' > src/added.py && echo "plain written"
        mkdir -p .github/workflows && echo 'name: x' > .github/workflows/x.yml && echo "workflow written"
        printf 'all:\\n\\tcurl evil\\n' > Makefile && echo "makefile written"
        mkdir -p .githooks && printf '#!/bin/sh\\necho hooked\\n' > .githooks/pre-commit && echo "hook written"
        cat '{rig.person}/.ssh/id_ed25519' > /dev/null 2>&1 && echo "KEY READ" || echo "key refused"
        {python} -I - 2>/dev/null <<'PY' && echo "NETWORK REACHED" || echo "network refused"
        import socket
        socket.create_connection(("127.0.0.1", {rig.bystander.getsockname()[1]}), 2)
        PY
        {python} -I - 2>/dev/null <<'PY' | sed 's/^/bridge /'
        import os, socket
        s = socket.create_connection(("127.0.0.1", int(os.environ["ANTHROPIC_BASE_URL"].rsplit(":", 1)[1])), 2)
        s.sendall(("GET /health HTTP/1.1\\r\\nHost: bridge\\r\\nAuthorization: Bearer " + os.environ["ANTHROPIC_AUTH_TOKEN"] + "\\r\\n\\r\\n").encode())
        print(s.recv(30).split(b" ")[1].decode())
        PY
        env | sed 's/^/ENV /'
        """))
    script.chmod(0o755)
    return script


def plan(rig: Rig, binary: Path, *, agent: str = "helper-7", render=None, grants=()) -> isolation.Plan:
    def default(context: isolation.Context) -> isolation.Rendering:
        return isolation.Rendering((str(binary),), {"session/note.txt": "written by the launcher\n"})

    events = rig.logs
    return isolation.Plan("claude", str(binary), str(rig.project), coding_policy.Policy(coding_policy.GUARDED),
                          f"http://127.0.0.1:{rig.port}", isolation.Lease(MODEL, 4096, 1), "", agent,
                          lambda stage: (lambda event, payload: {}), lambda verb, arguments: {},
                          render or default, Notice(Reporter(rig.sink.post), "lead", 8, lambda name, fields: events.append((name, fields))), grants)


def test_a_plain_file_reaches_the_checkout_and_a_workflow_a_build_file_and_a_hook_are_refused_with_a_patch_and_a_report(rig, tmp_path):
    """The whole launch on this Mac: the stage, the services, the proof, the harness under the one profile, publication at the end."""
    script = hostile(rig, tmp_path)
    with isolation.prepare(plan(rig, script)) as confinement:
        process = confinement.popen()
        out, _ = process.communicate(timeout=60)
        said = out.decode()
        assert process.returncode == 0, said
    assert "plain written" in said and "workflow written" in said and "key refused" in said and "network refused" in said, said
    assert "KEY READ" not in said and "NETWORK REACHED" not in said
    assert "bridge 200" in said, said
    assert (rig.project / "src" / "added.py").read_text().startswith("def added")
    assert not (rig.project / ".github").exists() and not (rig.project / ".githooks").exists()
    assert (rig.project / "Makefile").read_bytes() == b"all:\n\tsh scripts/build.sh\n"
    turn = confinement.turn
    assert "src/added.py" in turn.applied and len(turn.refused) >= 3 and turn.patch is not None
    assert Path(turn.patch.path).read_bytes() and rig.sink.posts, "the refusal is reported to the launching session"
    to, _kind, subject, body = rig.sink.posts[0]
    assert (to, subject) == ("lead", "isolation.report") and "evil" not in body and ".githooks" not in body and "workflow" not in body.replace("workflows", "")


def test_the_environment_is_exactly_the_allow_list_and_holds_no_variable_of_the_launcher(rig, tmp_path):
    script = hostile(rig, tmp_path)
    with isolation.prepare(plan(rig, script)) as confinement:
        assert set(confinement.env) <= allowed("claude") and "POOLHOUSE_TEST_CANARY" not in confinement.env
        assert confinement.env["HOME"] == str(confinement.home) and confinement.env["POOLHOUSE_AGENT"] == "1"
        out, _ = confinement.popen().communicate(timeout=60)
    seen = {line[4:].split("=", 1)[0] for line in out.decode().splitlines() if line.startswith("ENV ")}
    assert seen <= allowed("claude") | {"__CF_USER_TEXT_ENCODING", "PWD", "SHLVL", "_", "OLDPWD"}, seen
    assert "POOLHOUSE_TEST_CANARY" not in seen


def test_a_launch_with_a_granted_toolchain_folder_runs_its_program_in_the_proof_and_starts(rig, tmp_path):
    program = tmp_path / "toolchain" / "bin" / "node"
    program.parent.mkdir(parents=True)
    program.write_text("#!/bin/sh\necho v0 NOT NODE\n")
    program.chmod(0o755)
    grant = Grant("toolchain", str(tmp_path / "toolchain"), "this launch", "lead", "argument", "toolchain:test", "a toolchain for this test")
    with isolation.prepare(plan(rig, hostile(rig, tmp_path), grants=(grant,))) as confinement:
        assert confinement.popen().communicate(timeout=60)[0]


def test_a_computer_that_cannot_confine_refuses_before_a_stage_a_service_or_a_harness_exists(rig, tmp_path, monkeypatch):
    without_a_sandbox(monkeypatch, AbsentBackend)
    made = []
    monkeypatch.setattr(launch, "Session", type("NoStage", (), {"begin": staticmethod(lambda *_: made.append("a stage"))}))
    with pytest.raises(IsolationRefused, match=SENTENCES["no_sandbox_tool"]):
        isolation.prepare(plan(rig, hostile(rig, tmp_path)))
    assert made == [] and not list(tmp_path.glob("**/poolhouse-home-*"))


class Open(Seatbelt):
    """The real backend with the default turned to allow: a profile that holds nothing."""

    def wrap(self, argv, policy, **kept):
        made = super().wrap(argv, policy, **kept)
        made.argv[2] = made.argv[2].replace("(deny default", "(allow default", 1)
        return made


def test_a_profile_that_does_not_hold_stops_the_launch_before_any_harness_and_leaves_nothing(rig, tmp_path, monkeypatch):
    without_a_sandbox(monkeypatch, Open)
    script = hostile(rig, tmp_path)
    with pytest.raises(IsolationFailed) as failed:
        isolation.prepare(plan(rig, script))
    assert failed.value.attempt == "P01" and "was not started" in str(failed.value)
    assert not (rig.project / "src" / "added.py").exists(), "nothing ran"
    assert not list(tmp_path.glob("**/poolhouse-home-*"))


def test_a_harness_that_is_not_inside_the_profile_is_ended_by_the_attestation(rig, tmp_path, monkeypatch):
    """The mutation of the choke point: a start that bypasses the confinement (here, a plain process followed by the same tree) is caught by the
    operating system's own report, and the process does not outlive the refusal."""
    started = []

    def unconfined(argv, policy, *, cwd, stdin, stdout, stderr):  # noqa: PLR0913 - the signature of the engine spawn
        process = subprocess.Popen(["/bin/sleep", "30"], cwd=cwd, stdin=stdin, stdout=stdout, stderr=stderr, start_new_session=True)
        started.append(process)
        from poolhouse.sandbox import process_tree
        return engine.Started(process, process_tree.follow(process.pid), "", Seatbelt())

    monkeypatch.setattr(launch, "spawn", unconfined)
    with isolation.prepare(plan(rig, hostile(rig, tmp_path))) as confinement, pytest.raises(IsolationFailed) as failed:
        confinement.popen()
    assert failed.value.attempt == "attestation"
    assert started[0].wait(timeout=10) != 0 or started[0].poll() is not None, "the unconfined process was ended"


def test_the_bridge_acts_as_the_one_session_it_was_given_and_never_as_the_launcher(rig, tmp_path):
    """Attack finding M2: the tool handler and the rail are the helper made for this launch; a call through the bridge is run as that helper."""
    seen = []
    given = plan(rig, hostile(rig, tmp_path), agent="helper-9-fresh")
    object.__setattr__(given, "workspace", lambda verb, arguments: seen.append(("helper-9-fresh", verb, dict(arguments))) or {"ok": 1})
    with isolation.prepare(given) as confinement:
        client = confinement.home / "bin" / launch.CLIENT
        socket_path = next(each for each in confinement.made.services if hasattr(each, "path")).path
        done = subprocess.run([os.path.realpath(sys.executable), "-I", str(client), "--socket", socket_path, "--pid", str(os.getpid()), "workspace", "note", "--text", "halfway"],
                              capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert seen and seen[0][0] == "helper-9-fresh" and seen[0][0] != "lead" and seen[0][1] == "note"


def _calls(tree: ast.AST) -> list[str]:
    """The dotted names of every call in a module."""
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            parts, target = [], node.func
            while isinstance(target, ast.Attribute):
                parts.append(target.attr)
                target = target.value
            if isinstance(target, ast.Name):
                parts.append(target.id)
            names.append(".".join(reversed(parts)))
    return names


STARTS = ("subprocess.Popen", "subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output", "start_process",
          "platform.start_process", "os.execv", "os.execve", "os.execvp", "os.posix_spawn", "os.system", "spawn", "sandbox.spawn", "run.spawn")
LAUNCHERS = ("claude.py", "codex.py", "pi.py", "coding.py", "harnessing.py", "workspace/coding_turns.py", "workspace/task_coding.py")


def reaches_spawn(tree: ast.AST) -> bool:
    """Whether a module imports the engine's `spawn` or calls it through the package."""
    imported = any(isinstance(node, ast.ImportFrom) and (node.module or "").startswith("poolhouse.sandbox") and any(a.name == "spawn" for a in node.names)
                   for node in ast.walk(tree))
    return imported or any(call in ("sandbox.spawn", "run.spawn") for call in _calls(tree))


def starts_a_process(source: str) -> list[str]:
    return [name for name in _calls(ast.parse(source)) if name in STARTS]


def test_the_launcher_is_the_only_path_that_starts_a_harness():
    """No launcher, no caller of one and no turn manager starts a process of its own, and `sandbox.spawn` is called from `Confinement.popen` and
    nowhere else in the library: the one argv that exists is the one the proof ran under."""
    for name in LAUNCHERS:
        assert starts_a_process((SRC / name).read_text()) == [], f"{name} starts a process outside the confinement"
    callers = [path.relative_to(SRC).as_posix() for path in SRC.rglob("*.py")
               if path.relative_to(SRC).parts[0] != "sandbox" and reaches_spawn(ast.parse(path.read_text(encoding="utf-8")))]
    assert callers == ["isolation/confinement.py"], callers


@pytest.mark.parametrize("bypass", ["subprocess.call(cmd, env=env)", "subprocess.Popen(cmd)", "start_process(cmd)", "os.execv(cmd[0], cmd)",
                                    "sandbox.spawn(cmd, policy)"])
def test_a_launch_that_bypasses_the_choke_point_is_caught_by_the_check(bypass):
    """Mutation of the check itself: a launcher module that starts its harness by any of these calls fails it."""
    mutated = f"import subprocess\n\ndef launch(cmd, env, policy):\n    return {bypass}\n"
    assert starts_a_process(mutated), bypass
