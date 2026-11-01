"""Inert pool packages travel only over authenticated sealed member requests."""

from __future__ import annotations

import http.client
import io
import json
import threading
from dataclasses import dataclass, replace
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit

import pytest
from service_kit import node_binary, two_nodes  # noqa: F401  (fixtures)

from poolhouse import macauth, sealing
from poolhouse.fleet import studio_library, tls
from poolhouse.fleet.api import Daemon, make_handler
from poolhouse.fleet.framing import LimitedServer
from poolhouse.fleet.jobs import JobRunner
from poolhouse.fleet.remote import Member
from poolhouse.fleet.service_pool import Pool
from poolhouse.http import ServerError, request_json
from poolhouse.studio.library import LocalLibrary
from poolhouse.studio.packages import export_package
from poolhouse.studio.store import StudioStore


@dataclass
class Served:
    """A daemon on the second node of a real pool, and the member this process (the first node) reaches it as."""

    member: Member
    runner: JobRunner
    ui: SimpleNamespace
    nodes: object

    @property
    def endpoint(self) -> str:
        return self.member.base_url + studio_library.ENDPOINT


@pytest.fixture
def pool(two_nodes, tmp_path):  # noqa: F811
    root = tmp_path / "traind"
    root.mkdir()
    runner = JobRunner(root, slots=1)
    ui = SimpleNamespace(root=root, runner=runner)
    members = Pool(two_nodes.b.state)
    daemon = Daemon(runner, runner.files_root, macauth.derive(b"owner"), name="served", members=members, ui=ui,
                    service_identity=lambda: two_nodes.idb)
    server = LimitedServer(("127.0.0.1", 0), make_handler(daemon), tls=tls.member_context(two_nodes.idb, members))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    two_nodes.serve(1, server.server_port)
    try:
        yield Served(Member.from_binding(Pool().authorize(two_nodes.idb.fingerprint)), runner, ui, two_nodes)
    finally:
        store = getattr(ui, "_studio_pool_store", None)
        if store:
            store.close()
        runner.shutdown()
        server.shutdown()
        server.server_close()


def _package(version="1", name="Example"):
    return export_package({"nodes": [{"id": "prompt", "type": "prompt", "version": 1}],
                           "edges": [], "groups": {}},
                          package_id="example", version=version, name=name)


def _request(served, package=None, options=None):
    return request_json(served.endpoint, method="GET" if package is None else "POST",
                        payload=None if package is None else {"package": package},
                        options=options or served.member.current_options())


def _call(served, headers, context=None):
    """One raw request to the daemon over TLS as the first node's service, with exactly ``headers``."""
    target = urlsplit(served.endpoint)
    context = context or served.member.current_options().tls_context
    connection = http.client.HTTPSConnection(target.hostname, target.port, context=context)
    try:
        connection.request("GET", studio_library.ENDPOINT, headers={"Host": f"{target.hostname}:{target.port}", **headers})
        return connection.getresponse().status
    finally:
        connection.close()


def test_member_package_roundtrip_is_inert_idempotent_and_immutable(pool):
    package = _package()
    assert _request(pool, package)["package"] == package
    assert _request(pool, package)["package"] == package
    assert _request(pool)["packages"] == [package]
    with pytest.raises(ServerError) as error:
        _request(pool, _package(name="Different content"))
    assert error.value.status == 400
    assert pool.runner.snapshot() == []


def test_non_pool_secret_unsealed_and_replayed_requests_are_refused(pool):
    member = pool.member
    with pytest.raises(ServerError) as error:
        _request(pool, options=replace(member.current_options(), token="not-a-pool-secret"))
    assert error.value.status == 401
    with pytest.raises(ServerError) as error:
        _request(pool, options=replace(member.current_options(), token=macauth.derive(b"owner")))
    assert error.value.status == 401
    headers = macauth.sign(member.token, "GET", pool.endpoint, None)
    assert _call(pool, headers) == 403
    assert _call(pool, headers) == 401
    sealed = {**macauth.sign(member.token, "GET", pool.endpoint, None), sealing.HEADER: "2"}
    assert _call(pool, sealed) == 200
    assert _call(pool, sealed) == 401


def test_removed_member_is_refused_package_access(pool):
    package = _package()
    _request(pool, package)
    assert _request(pool)["packages"] == [package]
    context, token = pool.member.current_options().tls_context, pool.member.token
    sealed = {**macauth.sign(token, "GET", pool.endpoint, None), sealing.HEADER: "2"}
    assert _call(pool, sealed, context) == 200
    pool.nodes.b.person.remove_member(pool.nodes.b, pool.nodes.fingerprint(pool.nodes.a))
    with pytest.raises((ServerError, PermissionError)):
        _request(pool)
    again = {**macauth.sign(token, "GET", pool.endpoint, None), sealing.HEADER: "2"}
    try:
        status = _call(pool, again, context)
    except OSError:
        status = 403  # the handshake itself was refused
    assert status == 403


def test_pool_library_preserves_local_publish_when_devices_are_offline(tmp_path):
    store = StudioStore(tmp_path / "library.ladybug")
    try:
        local = LocalLibrary(store.graph, lock=store.lock)

        def unavailable():
            raise OSError("software-token=private-value")

        library = studio_library.MemberLibrary(local, unavailable)
        package = _package()
        assert library.publish(package) == package
        assert library.search() == [package]
        assert library.unavailable and "private-value" not in json.dumps(library.unavailable)
    finally:
        store.close()


def test_conflicting_member_version_does_not_replace_or_hide_unrelated_packages(tmp_path, monkeypatch):
    store = StudioStore(tmp_path / "library.ladybug")
    try:
        local = LocalLibrary(store.graph, lock=store.lock)
        local.publish(_package())
        safe = _package(version="2")
        local.publish(safe)
        library = studio_library.MemberLibrary(local, lambda: [object()])
        monkeypatch.setattr(library, "_request", lambda member: [_package(name="Conflict")])
        assert library.search() == [safe]
        assert local.get("example", "1") == _package()
        assert library.unavailable
    finally:
        store.close()


def test_member_discovery_pages_packages_without_loading_the_whole_library(pool, monkeypatch):
    monkeypatch.setattr(studio_library, "MAX_BYTES", 10_000)
    first = _package(name="a" * 5500)
    second = _package(version="2", name="b" * 5500)
    _request(pool, first)
    _request(pool, second)
    page = _request(pool)
    assert page["packages"] == [first] and page["next"] == ["example", "1"]
    next_page = request_json(pool.endpoint + "?" + urlencode({"after": json.dumps(page["next"])}),
                             options=pool.member.current_options())
    assert next_page["packages"] == [second] and next_page["next"] is None


def test_rejected_oversized_package_cannot_poison_pool_discovery(pool, monkeypatch):
    monkeypatch.setattr(studio_library, "MAX_BYTES", 10_000)
    package = _package(name="a" * 7500)
    assert len(json.dumps({"package": package}).encode()) < studio_library.MAX_BYTES
    with pytest.raises(ServerError) as error:
        _request(pool, package)
    assert error.value.status == 400
    assert _request(pool)["packages"] == []


@pytest.mark.parametrize("failure", [None, "seal", "oversize", "package", "cursor"])
def test_member_client_checks_sealing_byte_limits_and_page_integrity(monkeypatch, failure):
    package = _package()
    response = {"packages": [package], "next": None}
    if failure == "package":
        package["runtime"] = {"token": "private-value"}
    if failure == "cursor":
        response["next"] = ["different", "1"]
    raw = json.dumps(response).encode()
    if failure == "oversize":
        raw = b"x" * (studio_library.MAX_BYTES + sealing.NONCE_BYTES + 17)
    calls = []

    class Response(io.BytesIO):
        status = 200
        headers = {} if failure == "seal" else {sealing.HEADER: "1"}
        sealed = SimpleNamespace(open=lambda status, headers, body: body)

    def opened(url, *, options, **kwargs):
        calls.append(url)
        assert options.error_body_limit == studio_library.MAX_BYTES
        assert options.guard(url) == url
        with pytest.raises(ValueError, match="redirect"):
            options.guard("https://another.example/packages")
        return Response(raw)

    monkeypatch.setattr(studio_library, "open_stream", opened)
    member = SimpleNamespace(base_url="https://127.0.0.1:4141", token="invented-software-key",
                           beacon=SimpleNamespace(cert="enrolled"))
    library = studio_library.MemberLibrary(None, lambda: [])
    if failure:
        with pytest.raises(ValueError):
            library._request(member)
    else:
        assert library._request(member) == [package]
    assert len(calls) == 1
