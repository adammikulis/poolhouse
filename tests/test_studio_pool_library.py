"""Inert pool packages travel only over authenticated sealed member requests."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from test_fleet_ui import Serving

from poolhouse import macauth, sealing
from poolhouse.fleet import discovery, studio_library
from poolhouse.fleet.daemon import load_or_create_token
from poolhouse.http import Options, ServerError, request_json
from poolhouse.studio.library import LocalLibrary
from poolhouse.studio.packages import export_package
from poolhouse.studio.store import StudioStore


@pytest.fixture
def pool(tmp_path):
    served = Serving(tmp_path)
    served.ui.root = tmp_path / "traind"
    member = discovery.mint_pool("experiments", served.keyfile)
    try:
        yield served, member
    finally:
        store = getattr(served.ui, "_studio_pool_store", None)
        if store:
            store.close()
        served.close()


def _package(version="1", name="Example"):
    return export_package({"nodes": [{"id": "prompt", "type": "prompt", "version": 1}],
                           "edges": [], "groups": {}},
                          package_id="example", version=version, name=name)


def _request(served, member, package=None):
    return request_json(f"http://127.0.0.1:{served.port}{studio_library.ENDPOINT}",
                        method="GET" if package is None else "POST",
                        payload=None if package is None else {"package": package},
                        options=Options(token=discovery.derive_token(member.key)))


def test_member_package_roundtrip_is_inert_idempotent_and_immutable(pool):
    served, member = pool
    package = _package()
    assert _request(served, member, package)["package"] == package
    assert _request(served, member, package)["package"] == package
    assert _request(served, member)["packages"] == [package]
    with pytest.raises(ServerError) as error:
        _request(served, member, _package(name="Different content"))
    assert error.value.status == 400
    assert served.runner.snapshot() == []


def test_non_pool_secret_unsealed_and_replayed_requests_are_refused(pool):
    served, member = pool
    endpoint = f"http://127.0.0.1:{served.port}{studio_library.ENDPOINT}"
    with pytest.raises(ServerError) as error:
        request_json(endpoint, options=Options(token="not-a-pool-secret"))
    assert error.value.status == 401
    with pytest.raises(ServerError) as error:
        request_json(endpoint, options=Options(token=load_or_create_token(served.ui.root)))
    assert error.value.status == 403
    headers = macauth.sign(discovery.derive_token(member.key), "GET", endpoint, None)
    status, _, _ = served.call(studio_library.ENDPOINT, headers=headers)
    assert status == 403
    status, _, _ = served.call(studio_library.ENDPOINT, headers=headers)
    assert status == 401
    sealed = {**macauth.sign(discovery.derive_token(member.key), "GET", endpoint, None), sealing.HEADER: "2"}
    status, _, _ = served.call(studio_library.ENDPOINT, headers=sealed)
    assert status == 200
    status, _, _ = served.call(studio_library.ENDPOINT, headers=sealed)
    assert status == 401


def test_matched_secondary_pool_has_separate_library_and_revocation_wins(pool):
    served, secondary = pool
    primary = discovery.mint_pool("another-pool", served.keyfile)
    package = _package()
    _request(served, secondary, package)
    assert _request(served, primary)["packages"] == []
    assert _request(served, secondary)["packages"] == [package]
    discovery.leave(secondary.group, served.keyfile)
    with pytest.raises(ServerError) as error:
        _request(served, secondary)
    assert error.value.status in {401, 403}


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
    served, member = pool
    monkeypatch.setattr(studio_library, "MAX_BYTES", 10_000)
    first = _package(name="a" * 5500)
    second = _package(version="2", name="b" * 5500)
    _request(served, member, first)
    _request(served, member, second)
    page = _request(served, member)
    assert page["packages"] == [first] and page["next"] == ["example", "1"]
    url = f"http://127.0.0.1:{served.port}{studio_library.ENDPOINT}?" + urlencode({"after": json.dumps(page["next"])})
    next_page = request_json(url, options=Options(token=discovery.derive_token(member.key)))
    assert next_page["packages"] == [second] and next_page["next"] is None


def test_rejected_oversized_package_cannot_poison_pool_discovery(pool, monkeypatch):
    served, member = pool
    monkeypatch.setattr(studio_library, "MAX_BYTES", 10_000)
    package = _package(name="a" * 7500)
    assert len(json.dumps({"package": package}).encode()) < studio_library.MAX_BYTES
    with pytest.raises(ServerError) as error:
        _request(served, member, package)
    assert error.value.status == 400
    assert _request(served, member)["packages"] == []


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
