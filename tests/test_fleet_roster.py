"""The devices a node lists for the pool: who ``poolhouse-pool plan | pause | resume`` and the fleet bench reach.

Real daemons answer over TLS pinned to the certificate the node vouches for. The node's own answer about
the pool is the one stand-in, at its wire (`service_pool.node_health.call`), as in tests/test_fleet_tls.py.
"""

from __future__ import annotations

import json
import socket
import threading

import pytest
from test_fleet_tls import stop

from poolhouse import pool_cli
from poolhouse.fleet import roster, service_pool, tls
from poolhouse.fleet.api import Daemon, make_handler
from poolhouse.fleet.availability import Availability
from poolhouse.fleet.daemon import load_or_create_token
from poolhouse.fleet.framing import LimitedServer
from poolhouse.fleet.jobs import JobRunner
from poolhouse.fleet.remote import Member
from poolhouse.fleet.service_pool import Pool

pytest_plugins = ["test_fleet_tls"]


def serve(tmp_path, ident, authority, *, name):
    """A real daemon on TLS that the node's answer lists, with a schedule that ``/availability`` changes."""
    root = tmp_path / name
    (root / "files").mkdir(parents=True)
    runner = JobRunner(root)
    pool = Pool(root / "node")
    handler = make_handler(Daemon(runner, root / "files", load_or_create_token(root), name=name, members=pool,
                                 service_identity=lambda: ident, schedule=Availability()))
    httpd = LimitedServer(("127.0.0.1", 0), handler, tls=tls.member_context(ident, pool))
    rows, nodes = authority
    device = str(len(rows) + 1) * 64
    rows.append({"device": device, "name": name, "endpoint": f"https://127.0.0.1:{httpd.server_port}",
                 "cert": ident.der.hex(), "fingerprint": ident.fingerprint, "generation": "2" * 32, "service": "compute"})
    nodes[str(pool.state)] = device
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, runner


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_the_listed_devices_are_the_ones_the_node_vouches_for_that_answer(tmp_path, service_authority):
    rows, _nodes = service_authority
    one, two = tls.identity(tmp_path / "one", "one"), tls.identity(tmp_path / "two", "two")
    first = serve(tmp_path, one, service_authority, name="one")
    second = serve(tmp_path, two, service_authority, name="two")
    try:
        found = roster.members(port=free_port())
        assert [row["name"] for row in found] == ["one", "two"]
        assert not any(row["is_self"] for row in found)
        assert found[0]["base_url"] == rows[1]["endpoint"] and found[0]["device"]["name"] == "one"
        rows.pop(2)
        assert [row["name"] for row in roster.members(port=free_port())] == ["one"], "a device the node dropped is not listed"
    finally:
        stop(*first[:2])
        stop(*second[:2])


def test_a_device_that_does_not_answer_is_not_listed(tmp_path, service_authority):
    rows, _nodes = service_authority
    live = tls.identity(tmp_path / "live", "live")
    gone = tls.identity(tmp_path / "gone", "gone")
    first = serve(tmp_path, live, service_authority, name="live")
    dead = serve(tmp_path, gone, service_authority, name="gone")
    stop(*dead[:2])
    try:
        assert rows[2]["name"] == "gone"
        assert [row["name"] for row in roster.members(port=free_port(), timeout_s=1.0)] == ["live"]
    finally:
        stop(*first[:2])


def test_this_machines_own_daemon_leads_the_list_and_is_reached_under_the_owner_token(tmp_path, service_authority):
    _rows, _nodes = service_authority
    root = tmp_path / "here"
    root.mkdir()
    ident = tls.identity(tmp_path / "other", "other")
    other = serve(tmp_path, ident, service_authority, name="other")
    token = load_or_create_token(root)
    assert roster.owner_token(root) == token and roster.owner_token(tmp_path / "nowhere") == ""
    try:
        found = roster.members(port=free_port())
        assert [row["name"] for row in found] == ["other"]
        mine = roster.client_for({"name": "here", "base_url": "http://127.0.0.1:1", "is_self": True}, root=root)
        assert isinstance(mine, Member) and mine.token == token
        assert roster.client_for({"name": "here", "base_url": "http://127.0.0.1:1", "is_self": True}, root=tmp_path / "nowhere") is None
    finally:
        stop(*other[:2])


def test_a_client_for_another_device_is_signed_by_the_node_derived_secret_and_a_stranger_has_none(tmp_path, service_authority):
    ident = tls.identity(tmp_path / "larch", "larch")
    larch = serve(tmp_path, ident, service_authority, name="larch")
    try:
        row = roster.members(port=free_port())[0]
        client = roster.client_for(row)
        assert client is not None and client.token == service_pool.Pool().secret(ident.fingerprint)
        assert client.health()["name"] == "larch"
        stranger = {**row, "base_url": "https://127.0.0.1:9"}
        assert roster.client_for(stranger) is None, "an endpoint the node does not list gets no credential"
    finally:
        stop(*larch[:2])


def test_no_node_answering_means_no_list(tmp_path, monkeypatch):
    def dead(*_args):
        raise OSError("node disconnected")

    monkeypatch.setattr(service_pool.node_health, "call", dead)
    with pytest.raises(PermissionError, match="node must answer"):
        roster.members(port=free_port())


def test_pause_and_resume_reach_each_device_of_the_pool(tmp_path, service_authority, capsys):
    ident = tls.identity(tmp_path / "larch", "larch")
    larch = serve(tmp_path, ident, service_authority, name="larch")
    argv = ["--root", str(tmp_path / "root"), "--port", str(free_port()), "--timeout", "1", "--json"]
    try:
        assert pool_cli.main(["pause", "--for", "30m", "--reason", "away", *argv]) == 0
        paused = json.loads(capsys.readouterr().out)
        assert [(one["name"], one["ok"]) for one in paused] == [("larch", True)]
        assert "paused until" in paused[0]["said"]
        assert pool_cli.main(["resume", *argv]) == 0
        resumed = json.loads(capsys.readouterr().out)
        assert [(one["name"], one["ok"], one["said"]) for one in resumed] == [("larch", True, "taking work again")]
        assert pool_cli.main(["pause", "--for", "soon-ish", *argv]) == 2
    finally:
        stop(*larch[:2])


def test_pause_names_a_device_that_stopped_answering_since_it_was_last_met(tmp_path, service_authority, capsys):
    root = tmp_path / "root"
    argv = ["--root", str(root), "--port", str(free_port()), "--timeout", "1", "--json"]
    first = tls.identity(tmp_path / "first", "first")
    second = tls.identity(tmp_path / "second", "second")
    kept = serve(tmp_path, first, service_authority, name="first")
    leaving = serve(tmp_path, second, service_authority, name="second")
    try:
        assert pool_cli.main(["pause", *argv]) == 0
        capsys.readouterr()
        stop(*leaving[:2])
        assert pool_cli.main(["pause", *argv]) == 1
        said = {one["name"]: one for one in json.loads(capsys.readouterr().out)}
        assert said["first"]["ok"] and said["second"]["ok"] is False and "did not answer" in said["second"]["said"]
    finally:
        stop(*kept[:2])
