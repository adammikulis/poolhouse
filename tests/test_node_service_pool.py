"""Live node binding replies constrain subordinate HTTPS service credentials."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from poolhouse import macauth
from poolhouse.fleet import callers, service_pool, tls

POOL = "a" * 32


@pytest.fixture
def services(tmp_path, monkeypatch):
    identities = [tls.identity(tmp_path / name, name) for name in ("one", "two", "three")]
    rows = [{"device": str(number) * 64, "name": f"device-{number}", "endpoint": f"https://127.0.0.1:{9000 + number}",
             "cert": identity.der.hex(), "fingerprint": identity.fingerprint, "generation": str(number) * 32, "service": "compute"}
            for number, identity in enumerate(identities, 1)]
    def node(_state, method, params=None):
        if method == "pool_status":
            return {"pool": POOL, "fingerprint": rows[0]["device"]}
        if method == "service_members":
            return {"pool": POOL, "services": rows}
        found = [row for row in rows if row["fingerprint"] == params["fingerprint"]]
        if not found:
            raise ValueError("not authorized")
        return {"pool": POOL, **found[0]}
    monkeypatch.setattr(service_pool.node_health, "call", node)
    monkeypatch.setattr(tls, "local", lambda: identities[0])
    return service_pool.Pool(tmp_path / "node"), identities, rows


def test_pairwise_secret_agrees_and_binds_pool_device_service_and_generation(services):
    pool, identities, rows = services
    left = pool.secret(rows[1]["fingerprint"], identities[0])
    first, second = [service_pool.binding(POOL, row) for row in rows[:2]]
    right = service_pool.request_secret(identities[1], second, first)
    assert left == right and left.startswith(macauth.PREFIX)
    assert pool.secret(rows[2]["fingerprint"], identities[0]) != left
    first, second = [service_pool.binding(POOL, row) for row in rows[:2]]
    for changed in (replace(second, generation="f" * 32), replace(second, device="f" * 64)):
        assert service_pool.request_secret(identities[0], first, changed) != left
    assert service_pool.request_secret(identities[0], replace(first, pool="f" * 32),
                                       replace(second, pool="f" * 32)) != left


def test_unknown_and_removed_service_and_dead_node_fail_closed(services, monkeypatch):
    pool, identities, rows = services
    with pytest.raises(PermissionError):
        pool.authorize("f" * 64)
    rows.pop(1)
    with pytest.raises(PermissionError):
        pool.secret(identities[1].fingerprint)
    def dead(*_args):
        raise OSError("node disconnected")
    monkeypatch.setattr(service_pool.node_health, "call", dead)
    for action in (pool.services, pool.stamp, pool.certs_pem, lambda: pool.is_active(identities[0].fingerprint)):
        with pytest.raises(PermissionError, match="node must answer"):
            action()


def test_conflicting_certificates_and_wrong_fingerprints_are_refused(services):
    pool, _identities, rows = services
    rows[1]["fingerprint"] = rows[0]["fingerprint"]
    rows[1]["cert"] = rows[0]["cert"]
    with pytest.raises(PermissionError, match="conflicting"):
        pool.services()
    rows.pop(1)
    rows[0]["fingerprint"] = "f" * 64
    with pytest.raises(PermissionError, match="certificate"):
        pool.services()


def test_own_private_key_must_match_current_bound_certificate(services):
    pool, identities, rows = services
    forged = replace(identities[0], keyfile=identities[2].keyfile)
    with pytest.raises(PermissionError, match="key does not match"):
        pool.secret(rows[1]["fingerprint"], forged)
    with pytest.raises(PermissionError, match="distinct"):
        pool.secret(rows[0]["fingerprint"], identities[0])


@pytest.mark.parametrize("field,value", [("endpoint", "https://user:pass@127.0.0.1:9443"),
                                         ("endpoint", "http://127.0.0.1:9443"),
                                         ("endpoint", "https://127.0.0.1:9443/ui"),
                                         ("service", "owner"), ("generation", "invalid"),
                                         ("cert", "not-hex")])
def test_invalid_node_binding_fields_are_refused(services, field, value):
    pool, _identities, rows = services
    rows[1][field] = value
    with pytest.raises(PermissionError):
        pool.services()


def test_screen_preserves_nonce_replay_and_isolates_members_pairwise(services, monkeypatch):
    pool, identities, rows = services
    screen = callers.Screen(lambda: [], lambda: [], pool, lambda value: value)
    monkeypatch.setattr(callers, "identify", lambda _connection, _pool: (callers.MEMBER, identities[1].fingerprint))
    url = "https://127.0.0.1:9001/jobs"
    secret = pool.secret(identities[1].fingerprint)
    headers = {"Host": "127.0.0.1:9001", **macauth.sign(secret, "POST", url, b"{}")}
    handler = SimpleNamespace(connection=None, command="POST", path="/jobs", headers=headers,
                              client_address=("127.0.0.1", 1234))
    assert screen.check(handler, b"{}").verdict.ok
    assert not screen.check(handler, b"{}").verdict.ok
    wrong_secret = pool.secret(identities[2].fingerprint)
    handler.headers = {"Host": "127.0.0.1:9001", **macauth.sign(wrong_secret, "POST", url, b"{}")}
    assert not screen.check(handler, b"{}").verdict.ok
    rows[1]["generation"] = "f" * 32
    handler.headers = {"Host": "127.0.0.1:9001", **macauth.sign(secret, "POST", url, b"{}")}
    assert not screen.check(handler, b"{}").verdict.ok


def test_member_refuses_mid_request_binding_replacement(services):
    from poolhouse.fleet.remote import Member
    pool, _identities, rows = services
    member = Member(rows[1]["endpoint"], "ignored-shared-key")
    member.check_current()
    options = member.current_options()
    assert options.token == pool.secret(rows[1]["fingerprint"])
    assert options.follow_redirects is False
    with pytest.raises(PermissionError, match="endpoint"):
        options.guard("https://127.0.0.1:9999/jobs")
    rows[1]["generation"] = "f" * 32
    with pytest.raises(PermissionError, match="binding changed"):
        member.check_current()


def test_own_binding_cannot_belong_to_another_member(services):
    pool, _identities, rows = services
    expected = pool.own()
    assert expected.device == rows[0]["device"]
    rows[0]["device"] = rows[1]["device"]
    with pytest.raises(PermissionError, match="conflicting"):
        pool.services()


@pytest.mark.parametrize("address,port", [("192.0.2.1", 9001), ("127.0.0.1", 9999)])
def test_self_service_owner_branch_requires_actual_loopback_socket_and_bound_port(services, monkeypatch, address, port):
    pool, identities, _rows = services
    owner = macauth.derive(b"local-owner")
    screen = callers.Screen(lambda: [owner], lambda: [], pool, lambda value: value)
    monkeypatch.setattr(callers, "identify", lambda _connection, _pool: (callers.MEMBER, identities[0].fingerprint))
    handler = SimpleNamespace(connection=SimpleNamespace(getsockname=lambda: ("127.0.0.1", port)), command="POST",
                              path="/jobs", headers={"Host": "127.0.0.1:9001", **macauth.sign(owner, "POST", "https://127.0.0.1:9001/jobs", b"{}")},
                              client_address=(address, 1234))
    assert screen.check(handler, b"{}").refusal is not None


def test_self_service_keeps_owner_mac_separate_from_member_secret(services, monkeypatch):
    pool, identities, rows = services
    owner = macauth.derive(b"local-owner")
    screen = callers.Screen(lambda: [owner], lambda: [], pool, lambda value: value)
    current = [identities[0].fingerprint]
    monkeypatch.setattr(callers, "identify", lambda _connection, _pool: (callers.MEMBER, current[0]))
    handler = SimpleNamespace(connection=SimpleNamespace(getsockname=lambda: ("127.0.0.1", 9001)), command="POST",
                              path="/jobs", headers={}, client_address=("127.0.0.1", 1234))
    def signed(secret):
        handler.headers = {"Host": "127.0.0.1:9001", **macauth.sign(secret, "POST", "https://127.0.0.1:9001/jobs", b"{}")}
        return screen.check(handler, b"{}")
    answer = signed(owner)
    assert answer.verdict.ok and answer.owner_service and answer.group == POOL
    assert not signed(pool.secret(rows[1]["fingerprint"])).verdict.ok
    current[0] = identities[1].fingerprint
    assert not signed(owner).verdict.ok


def test_local_self_member_accepts_only_exact_own_cert_and_current_bound_port(services):
    from poolhouse.fleet.discovery import Beacon
    from poolhouse.fleet.remote import Member
    _pool, identities, rows = services
    owner = macauth.derive(b"local-owner")
    beacon = Beacon(name="own", host="127.0.0.1", port=9001, cert=identities[0].beacon)
    member = Member("https://127.0.0.1:9001", owner, beacon=beacon)
    assert member.token == owner
    member.check_current()
    with pytest.raises(PermissionError):
        Member("https://127.0.0.1:9999", owner, beacon=beacon)
    rows[0]["generation"] = "f" * 32
    with pytest.raises(PermissionError):
        member.check_current()


@pytest.mark.parametrize("endpoint", ["http://192.0.2.1:8770", "ftp://127.0.0.1:8770", "file:///tmp/service"])
def test_non_tls_remote_and_unsupported_member_endpoints_are_refused(endpoint):
    from poolhouse.fleet.remote import Member
    with pytest.raises(ValueError):
        Member(endpoint, "stale-shared-key")


def test_real_tls_member_removal_is_refused_on_existing_connection(services, tmp_path):
    import http.client
    import json
    import threading

    from remote_client_support import RemoteClientServer

    from poolhouse.fleet.api import Daemon, make_handler
    from poolhouse.fleet.jobs import JobRunner

    pool, identities, rows = services
    root = tmp_path / "daemon"
    (root / "files").mkdir(parents=True)
    runner = JobRunner(root)
    owner = macauth.derive(b"owner")
    daemon = Daemon(runner, root / "files", owner, name="one", members=pool,
                    service_identity=lambda: identities[0])
    handler = make_handler(daemon)
    handler.protocol_version = "HTTP/1.1"
    server = RemoteClientServer(handler, tls=tls.member_context(identities[0], pool))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPSConnection("127.0.0.1", server.server_port,
                                             context=tls.pinned_context(identities[0].beacon, who=identities[1]), timeout=5)
    secret = pool.secret(identities[1].fingerprint, identities[0])
    url = f"https://127.0.0.1:{server.server_port}/jobs"
    try:
        connection.request("GET", "/jobs", headers=macauth.sign(secret, "GET", url, None))
        reply = connection.getresponse()
        assert reply.status == 200 and json.loads(reply.read()) == {"jobs": []}
        assert connection.sock is not None
        rows.pop(1)
        connection.request("GET", "/jobs", headers=macauth.sign(secret, "GET", url, None))
        reply = connection.getresponse()
        assert reply.status == 403
        assert "member" in json.loads(reply.read())["error"]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        runner.shutdown()
