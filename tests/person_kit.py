"""The person's UI daemon in front of a real node: its Tasks route, and the person's session on the board."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from launch_support import signed
from test_fleet_ui import Serving

from poolhouse import node_launch
from poolhouse.board import person, place, session as board_session
from poolhouse.fleet import extension_routes
from poolhouse.fleet.project_board_routes import ProjectBoardRoutes
from poolhouse.fleet.routes import Base
from poolhouse.fleet.session import Sessions
from poolhouse.workspace import task_routes

PROJECT = "a" * 32
OTHER = "b" * 32


@pytest.fixture
def tasks_ui(workspace_node, monkeypatch, tmp_path):
    """A signed-in daemon serving ``/ui/tasks`` for the node's board, and the node: ``(server, node)``."""
    monkeypatch.setattr(node_launch, "default_state", lambda: workspace_node.state)
    entry = SimpleNamespace(name="tasks", load=lambda: task_routes.route)
    monkeypatch.setattr(extension_routes, "entry_points", lambda **kw: [entry])
    server = signed(Serving(tmp_path))
    try:
        yield server, workspace_node
    finally:
        server.close()


def person_seat(node):
    """The person's session on the node's board, as the daemon holds it."""
    return person.seat(Path.cwd(), node.client)


class Page(ProjectBoardRoutes, Base):
    """One request to the project routes of the UI, answered into ``handler``."""

    def send(self, status, payload, extra=None):
        self.handler.send_response(status)
        self.handler.wfile.write(json.dumps(payload).encode())


def member_on(node, root, name):
    """A session of its own on the board of the project at ``root``, as an agent would register: its typed session."""
    board = place.resolve(node.client, Path(root))
    made = board_session.register(node.client, board, board_session.Native("claude-sonnet-5-5", "claude-code", name))
    return board_session.Session(node.client, board, made.token, made.name)


def _repo(where: Path) -> Path:
    where.mkdir()
    subprocess.run(["git", "init", "-q", str(where)], check=True)
    return where


@pytest.fixture
def project_tasks(workspace_node, monkeypatch, tmp_path):
    """A signed-in person, two projects (two git checkouts, so two boards) and ``call(path, project=, method=, body=)``
    to the project task route; ``(call, node, projects)``."""
    monkeypatch.setattr(node_launch, "default_state", lambda: workspace_node.state)
    monkeypatch.delenv("POOLHOUSE_BOARD")
    projects = {ident: SimpleNamespace(name=name, root=str(_repo(tmp_path / name)), board_host="")
                for ident, name in ((PROJECT, "experiment"), (OTHER, "other"))}
    registry = SimpleNamespace(machine="local", hosts=lambda host: True, get=lambda ident: projects[ident])
    sessions = Sessions()
    session = sessions.open("fixture-person", "launch-ticket")
    cookie_value = f"poolhouse_ui={session.sid}"
    ui = SimpleNamespace(sessions=sessions, projects=registry, record=lambda *a, **k: None, workspaces=object(),
                         authed=lambda cookie: cookie == cookie_value,
                         credentialed=lambda cookie: cookie == cookie_value and session.credentialed,
                         host_ok=lambda host: host == "127.0.0.1:8770")

    def call(suffix="tasks", *, project=PROJECT, method="GET", body=None, **options):
        raw = options.get("raw", json.dumps(body).encode() if body is not None else b"")
        headers = {"Host": "127.0.0.1:8770", "Cookie": options.get("cookie", cookie_value),
                   "Origin": options.get("origin", "http://127.0.0.1:8770"), "Content-Type": "application/json",
                   "Content-Length": str(len(raw)), **options.get("headers", {})}
        codes = []
        handler = SimpleNamespace(path=options.get("path", f"/ui/projects/{project}/{suffix}"), command=method,
                                  client_address=(options.get("ip", "127.0.0.1"), 1000), headers=headers,
                                  rfile=io.BytesIO(raw), wfile=io.BytesIO(),
                                  server=SimpleNamespace(server_address=("127.0.0.1", 8770)),
                                  send_response=codes.append, send_header=lambda *args: None,
                                  end_headers=lambda: None)
        assert Page(ui, handler).route()
        return codes[0], json.loads(handler.wfile.getvalue())

    return call, workspace_node, projects
