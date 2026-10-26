"""The public pool command uses the node-backed pool API."""

import json
import tomllib
from pathlib import Path

from poolhouse import errors


def test_pool_entrypoint_uses_node_command():
    project = Path(__file__).resolve().parents[1] / "pyproject.toml"
    scripts = tomllib.loads(project.read_text())["project"]["scripts"]
    assert scripts["poolhouse-pool"] == "poolhouse.pool_cli:main"


def test_join_preserves_pending_result(monkeypatch, capsys):
    from poolhouse import pool_cli

    calls = []
    def join(host, code, **kwargs):
        calls.append((host, code, kwargs))
        return {"state": "pending", "request": "request-one"}
    monkeypatch.setattr(pool_cli.pool, "join", join)
    assert pool_cli.main(["join", "join-code", "--host", "device.invalid", "--port", "7449", "--json"]) == 0
    assert calls == [("device.invalid", "join-code", {"port": 7449, "fingerprint": ""})]
    assert json.loads(capsys.readouterr().out) == {"state": "pending", "request": "request-one"}


def test_remove_uses_node_pool_api(monkeypatch, capsys):
    from poolhouse import pool_cli

    calls = []
    monkeypatch.setattr(pool_cli.pool, "remove", lambda name: calls.append(name) or {"status": "revoked"})
    assert pool_cli.main(["remove", "device-one", "--json"]) == 0
    assert calls == ["device-one"]
    assert json.loads(capsys.readouterr().out) == {"status": "revoked"}


def test_node_refusal_does_not_create_a_pool(monkeypatch, capsys):
    from poolhouse import pool_cli
    from poolhouse.fleet import discovery

    def unavailable():
        raise errors.NotRunning("The pool node is unavailable.")
    def mint(*args, **kwargs):
        raise AssertionError("the pool command must not mint Python membership")
    monkeypatch.setattr(pool_cli.pool, "status", unavailable)
    monkeypatch.setattr(discovery, "mint_pool", mint)
    assert pool_cli.main(["status", "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "The pool node is unavailable."}


def test_leave_uses_registered_node_session(monkeypatch, capsys):
    from poolhouse import pool_cli

    calls = []
    class Session:
        token = "session-token"
    monkeypatch.setattr(pool_cli._node, "session", Session)
    monkeypatch.setattr(pool_cli._node, "call", lambda method, **kwargs: calls.append((method, kwargs)) or {"state": "pending"})
    assert pool_cli.main(["leave", "--json"]) == 0
    assert calls == [("pool_leave", {"token": "session-token"})]
    assert json.loads(capsys.readouterr().out) == {"state": "pending"}


def test_old_module_entry_uses_same_node_pool_command():
    from poolhouse import pool_cli
    from poolhouse.fleet import join

    assert join.main is pool_cli.main


def test_source_management_keeps_its_own_action(monkeypatch):
    from poolhouse import pool_cli

    calls = []
    monkeypatch.setattr(pool_cli, "run_service", lambda args: calls.append((args.cmd, args.action)) or 0)
    assert pool_cli.main(["model-sources", "list", "--json"]) == 0
    assert calls == [("model-sources", "list")]


def test_compute_plan_keeps_existing_handler(monkeypatch):
    from poolhouse import pool_cli
    from poolhouse.fleet import join

    calls = []
    monkeypatch.setattr(join, "cmd_plan", lambda args: calls.append((args.users, args.context, args.apply)) or 3)
    assert pool_cli.main(["plan", "--users", "2", "--context", "4096", "--apply"]) == 3
    assert calls == [(2, 4096, True)]
