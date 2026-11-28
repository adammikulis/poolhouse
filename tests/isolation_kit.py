"""What the isolation tests share: a launch's directories, a plumbing bridge for the controls of the proof, and the hostile programs.

The bridge is a plain protocol server for the proof's controls (a route table and a refusal): it is not a model and says so in every reply,
with the id `CANNED-NOT-A-MODEL` and the marker `[CANNED RESPONSE, NOT A MODEL]` in the body and in the headers. No model behaviour is under
test here (docs/agent-policy.md, "Never a fake model").
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from poolhouse import coding_policy
from poolhouse.board import place
from poolhouse.isolation import Services, Spec, build, environment
from poolhouse.isolation.egress import Host
from poolhouse.isolation.environment import Inputs
from poolhouse.isolation.profile import Profile
from poolhouse.sandbox import run
from tests.sandbox_kit import policy

MARK = "[CANNED RESPONSE, NOT A MODEL]"
MODEL = "CANNED-NOT-A-MODEL"


class Canned(BaseHTTPRequestHandler):
    """A route table: `/health` answers, any other route is 404, and a request that names another model is refused."""

    def reply(self, status: int, body: dict) -> None:
        raw = (MARK + json.dumps({"model": MODEL, **body})).encode()
        self.send_response(status)
        self.send_header("X-Canned", MARK)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        self.reply(200 if self.path == "/health" else 404, {"path": self.path})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        asked = json.loads(self.rfile.read(length) or b"{}")
        self.reply(200 if asked.get("model") == MODEL else 400, {"asked": asked.get("model")})

    def log_message(self, *_args: object) -> None:
        return None


@dataclass(slots=True)
class World:
    """One launch's directories, its services and its profile, built for a test."""

    root: Path
    spec: Spec
    services: Services
    profile: Profile
    port: int
    socket_path: str


def _serve_unix(path: str, stop: threading.Event) -> None:
    server = socket.socket(socket.AF_UNIX)
    server.bind(path)
    server.listen(4)
    server.settimeout(0.2)
    while not stop.is_set():
        try:
            connection, _ = server.accept()
        except TimeoutError:
            continue
        with connection:
            connection.recv(4096)
            connection.sendall((json.dumps({"refused": "unknown frame", "note": MARK}) + "\n").encode())
    server.close()


@contextlib.contextmanager
def world(tmp_path: Path, *, plan: bool = False, harness: str = "claude") -> Iterator[World]:
    """A stage, an agent home, an install tree and the two bridges the proof's controls reach, and the profile over them."""
    base = Path(os.path.realpath(tmp_path))
    stage, agent, install = base / "stage", base / "home", base / "install"
    for each in (stage, agent / "bin", agent / "tmp", install):
        each.mkdir(parents=True, exist_ok=True)
    (stage / "main.py").write_text("print('hello')\n")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Canned)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    short = tempfile.mkdtemp(prefix="pp-sock-", dir="/tmp")
    path = os.path.realpath(Path(short) / "session")
    stop = threading.Event()
    threading.Thread(target=_serve_unix, args=(path, stop), daemon=True).start()
    withheld = socket.create_server(("127.0.0.1", 0))   # where the model server a real launch withholds from its box would listen
    spec = Spec(harness, str(stage), str(agent), str(install), server.server_port, session_socket=path,
                policy=coding_policy.Policy(coding_policy.READ_ONLY if plan else coding_policy.GUARDED), withheld=(withheld.getsockname()[1],))
    env = environment(Inputs(harness, str(agent), path=("/usr/bin", "/bin"), base_url=f"http://127.0.0.1:{server.server_port}", token="tok", alias=MODEL))
    profile = build(spec, (), env)
    try:
        yield World(base, spec, Services(server.server_port, path), profile, server.server_port, path)
    finally:
        withheld.close()
        stop.set()
        server.shutdown()
        server.server_close()
        with contextlib.suppress(OSError):
            Path(path).unlink()
            Path(short).rmdir()


SOURCE = str(Path(__file__).resolve().parent.parent / "src")


def confined_python(code: str, tmp_path: Path, **env: str):
    """The result of ``code`` run by this interpreter in the real Seatbelt confinement of the engine, which reads the library and ``tmp_path``."""
    held = policy(read=[SOURCE, tmp_path], python=True, env={"HOME": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1", **env})
    return run([os.path.realpath(sys.executable), "-I", "-c", f"import sys; sys.path.insert(0, {SOURCE!r})\n" + code], held, diagnose="never")


def interpreter() -> str:
    return os.path.realpath(sys.executable)


REGISTRY = {"pypi.org": Host("pypi.org", "python", "https://pypi.org/help/", 5000, "pip download, measured by the test kit", "2026-11-24", 12000)}
"""One host with every field a registry entry must carry, for the tests of the decisions; the file of the list has its own test."""


def person_home(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A person's home with the credential folders a hostile grant would name, and a state root beneath it; both are the tests' own."""
    house = root / "home"
    for name in (".ssh", ".aws", "Library/Keychains", ".poolhouse/node"):
        (house / name).mkdir(parents=True, exist_ok=True)
    (house / ".ssh" / "id_ed25519").write_text("not a key\n")
    monkeypatch.setenv("HOME", str(house))
    monkeypatch.setenv("POOLHOUSE_HOME", str(house / ".poolhouse"))
    return house


def registered_project(root: Path, node) -> str:
    """`hostile_project` made a git repository and registered with the real node ``node`` as a project, the way a session's first hook adds the
    repository it runs in; the node's state is the state root of this test."""
    project = hostile_project(root)
    subprocess.run(["git", "init", "-q", project], check=True, capture_output=True)
    place.add(node.client, Path(project))
    return project


def hostile_project(root: Path) -> str:
    """A project whose names are the ones a publish rule must never apply: build, CI, hook, instruction and review files, a wrapper and a
    jar, a script a build file names, links out, a FIFO and a folder that holds a key."""
    project = root / "project"
    for name in ("src", "docs", "scripts", "lib", ".github", "holds-a-key", "plain-dir"):
        (project / name).mkdir(parents=True, exist_ok=True)
    files = {"src/app.py": "print('app')\n", "src/.hidden.py": "x = 1\n", "docs/Notes.md": "# notes\n", "README.md": "readme\n",
             "Makefile": "all:\n\tpython scripts/build.py\n", "package.json": "{}\n", "pyproject.toml": "[project]\n", "CLAUDE.md": "x\n",
             "AGENTS.md": "x\n", "CODEOWNERS": "* @x\n", "docs/CODEOWNERS": "* @x\n", ".github/CODEOWNERS": "* @x\n", "gradlew": "#!/bin/sh\n",
             "lib/tool.jar": "PK\n", "scripts/build.py": "print('build')\n", "lefthook.yml": "x\n", "Dockerfile": "FROM x\n",
             "yarn.lock": "x\n", "id_rsa": "x\n", "server.pem": "x\n", ".env": "x=1\n", "holds-a-key/id_rsa": "x\n"}
    for name, text in files.items():
        (project / name).write_text(text)
    outside = root / "outside"
    outside.mkdir(exist_ok=True)
    (outside / "x.py").write_text("x = 1\n")
    (project / "link-out.py").symlink_to(outside / "x.py")
    (project / "linked-dir").symlink_to(outside)
    os.mkfifo(project / "special.fifo")
    return str(project.resolve())


def without_a_sandbox(monkeypatch: pytest.MonkeyPatch, backend: type) -> None:
    """Make ``backend`` the backend this computer has, for the capability report and for every start of a confined process: the one seam of the
    engine (`sandbox.run.backend`), so that a launch on a computer that cannot confine, or that confines nothing, is the real code path."""
    import sys

    monkeypatch.setattr(sys.modules["poolhouse.sandbox.run"], "backend", backend)
    monkeypatch.setattr(sys.modules["poolhouse.isolation.capability"], "platform_backend", backend)
    sys.modules["poolhouse.isolation.capability"].drop_cache()
