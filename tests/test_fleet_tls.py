"""Fleet traffic off this machine is encrypted to a certificate the beacon vouched for."""

import contextlib
import json
import os
import socket
import ssl
import threading
import time

import pytest

from poolhouse import http, macauth
from poolhouse.fleet import membership, service_pool, tls
from poolhouse.fleet.api import Daemon, make_handler
from poolhouse.fleet.daemon import ALL_INTERFACES, load_or_create_token
from poolhouse.fleet.discovery import Beacon, discover, primary_ip
from poolhouse.fleet.framing import LimitedServer
from poolhouse.fleet.jobs import JobRunner
from poolhouse.fleet.remote import Member, MemberError
from poolhouse.fleet.service_pool import Pool
from poolhouse.windows_private import problem as windows_problem, restrict


@pytest.fixture(autouse=True)
def forget_pins():
    yield
    http._PINNED.clear()


def lan_address(*, real=False):
    """Where the daemon is reached: loopback, which pins and verifies a certificate the same; ``real`` is this machine's LAN address."""
    if not real:
        return "127.0.0.1"
    address = primary_ip()
    if not address or address.startswith("127."):
        pytest.skip("this machine has no address other than loopback")
    return address


@pytest.fixture
def service_authority(tmp_path, monkeypatch):
    client = tls.identity(tmp_path / "client", "client")
    monkeypatch.setattr(tls, "local", lambda: client)
    rows = [{"device": "1" * 64, "name": "client", "endpoint": "https://127.0.0.1:9444",
             "cert": client.der.hex(), "fingerprint": client.fingerprint, "generation": "1" * 32, "service": "compute"}]
    server_nodes = {}
    def node(state, method, params=None):
        if method == "pool_status":
            return {"pool": "a" * 32, "fingerprint": server_nodes.get(str(state), "1" * 64)}
        if method == "service_members":
            return {"pool": "a" * 32, "services": rows}
        selected = [row for row in rows if row["fingerprint"] == params["fingerprint"]]
        if not selected:
            raise ValueError("service not authorized")
        return {"pool": "a" * 32, **selected[0]}
    monkeypatch.setattr(service_pool.node_health, "call", node)
    return rows, server_nodes


def serve(tmp_path, ident, authority, *, name="d", host="127.0.0.1"):
    root = tmp_path / name
    (root / "files").mkdir(parents=True)
    runner = JobRunner(root)
    owner = load_or_create_token(root)
    pool = Pool(root / "node")
    handler = make_handler(Daemon(runner, root / "files", owner, name=name, members=pool,
                                 service_identity=lambda: ident))
    httpd = LimitedServer((host, 0), handler, tls=tls.member_context(ident, pool))
    rows, nodes = authority
    device = str(len(rows) + 1) * 64
    rows.append({"device": device, "name": name, "endpoint": f"https://127.0.0.1:{httpd.server_port}",
                 "cert": ident.der.hex(), "fingerprint": ident.fingerprint, "generation": "2" * 32, "service": "compute"})
    nodes[str(pool.state)] = device
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    token = Pool().secret(ident.fingerprint)
    return httpd, runner, token


def stop(httpd, runner):
    runner.shutdown()
    httpd.shutdown()
    httpd.server_close()


# -- the certificate -------------------------------------------------------------------


def test_a_daemon_makes_a_certificate_once_and_keeps_it(tmp_path):
    first = tls.identity(tmp_path / "tls", "box")
    again = tls.identity(tmp_path / "tls", "box")
    assert again.der == first.der and first.fingerprint == again.fingerprint
    for path in (first.keyfile, tmp_path / "tls"):
        if os.name == "nt":
            assert windows_problem(path) == ""
        else:
            assert path.stat().st_mode & 0o077 == 0


def test_public_certificate_context_never_loads_a_client_identity(tmp_path, monkeypatch):
    ident = tls.identity(tmp_path / "tls", "box")

    def forbidden(*args, **kwargs):
        pytest.fail('Public certificate verification must not access a private identity')

    class PublicContext(ssl.SSLContext):
        load_cert_chain = forbidden

    monkeypatch.setattr(tls, 'local', forbidden)
    monkeypatch.setattr(tls, 'identity', forbidden)
    for _ in range(2):
        context = tls.certificate_context(ident.beacon, context_type=PublicContext)
        assert isinstance(context, PublicContext)
        assert context.minimum_version == ssl.TLSVersion.TLSv1_3
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is False
        assert len(context.get_ca_certs(binary_form=True)) == 1


def test_member_context_retains_explicit_private_identity_after_public_context(tmp_path, monkeypatch):
    ident = tls.identity(tmp_path / "tls", "box")
    loaded = []

    class MemberContext(ssl.SSLContext):
        def load_cert_chain(self, certfile, keyfile):
            loaded.append((certfile, keyfile))
            super().load_cert_chain(certfile, keyfile)

    monkeypatch.setattr(tls, 'local', lambda: pytest.fail('An explicit identity must not resolve another'))
    context = tls.pinned_context(ident.beacon, who=ident, context_type=MemberContext)
    assert isinstance(context, MemberContext)
    assert loaded == [(str(ident.certfile), str(ident.keyfile))]


@pytest.mark.skipif(os.name != "nt", reason="native Windows access control")
def test_a_private_key_granted_to_another_account_is_refused(tmp_path):
    import ntsecuritycon
    import win32security

    ident = tls.identity(tmp_path / "tls", "box")
    descriptor = win32security.GetNamedSecurityInfo(
        str(ident.keyfile), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION)
    acl = descriptor.GetSecurityDescriptorDacl()
    everyone = win32security.CreateWellKnownSid(win32security.WinWorldSid)
    acl.AddAccessAllowedAce(win32security.ACL_REVISION, ntsecuritycon.FILE_GENERIC_READ, everyone)
    try:
        win32security.SetNamedSecurityInfo(
            str(ident.keyfile), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION,
            None, None, acl, None)
        with pytest.raises(tls.TlsUnavailable, match="another account"):
            tls.identity(tmp_path / "tls", "box")
    finally:
        restrict(ident.keyfile)


@pytest.mark.skipif(os.name != "nt", reason="native Windows junctions")
def test_a_junction_identity_directory_is_refused_without_writing_a_key(tmp_path):
    import subprocess

    target = tmp_path / "target"
    target.mkdir()
    junction = tmp_path / "tls"
    created = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(target)],
                             capture_output=True, check=False)
    assert created.returncode == 0, created.stderr
    try:
        with pytest.raises(tls.TlsUnavailable, match="reparse point"):
            tls.identity(junction, "box")
        assert list(target.iterdir()) == []
    finally:
        junction.rmdir()


def test_a_certificate_close_to_its_end_is_made_again(tmp_path):
    now = time.time()
    old = tls.identity(tmp_path / "tls", "box", now=now - 70 * 86400, days=90)
    assert tls.identity(tmp_path / "tls", "box", now=now - 70 * 86400, days=90).der == old.der
    fresh = tls.identity(tmp_path / "tls", "box", now=now, days=90)
    assert fresh.der != old.der and fresh.not_after > now + 80 * 86400


def test_a_certificate_made_by_openssl_serves_just_as_well(tmp_path, monkeypatch, service_authority):
    import shutil

    if shutil.which("openssl") is None:
        pytest.skip("no openssl on this machine")
    monkeypatch.setattr(tls, "_with_cryptography", lambda *a: (_ for _ in ()).throw(ImportError()))
    ident = tls.identity(tmp_path / "tls", "viaopenssl")
    httpd, runner, token = serve(tmp_path, ident, service_authority)
    try:
        member = Member(f"https://127.0.0.1:{httpd.server_port}", token)
        http.pin(f"127.0.0.1:{httpd.server_port}", tls.pinned_context(ident.beacon))
        assert member.health()["ok"] is True
    finally:
        stop(httpd, runner)


def test_with_neither_cryptography_nor_openssl_the_error_says_what_to_install(tmp_path,
                                                                              monkeypatch):
    monkeypatch.setattr(tls, "_with_cryptography", lambda *a: (_ for _ in ()).throw(ImportError()))
    monkeypatch.setattr(tls.shutil, "which", lambda *_: None)
    with pytest.raises(tls.TlsUnavailable, match="fleet-tls"):
        tls.identity(tmp_path / "tls", "box")


# -- talking to it ----------------------------------------------------------------------


def test_a_pinned_member_is_served_over_tls_and_the_request_is_signed_inside_it(tmp_path, service_authority):
    ident = tls.identity(tmp_path / "tls", "d")
    httpd, runner, token = serve(tmp_path, ident, service_authority)
    try:
        port = httpd.server_port
        http.pin(f"{lan_address()}:{port}", tls.pinned_context(ident.beacon))
        member = Member(f"https://{lan_address()}:{port}", token)
        assert member.health()["name"] == "d"
        assert member.jobs() == []
    finally:
        stop(httpd, runner)


def test_a_certificate_other_than_the_pinned_one_is_refused(tmp_path, service_authority):
    ident = tls.identity(tmp_path / "real", "d")
    impostor = tls.identity(tmp_path / "impostor", "d")
    httpd, runner, token = serve(tmp_path, impostor, service_authority)
    try:
        port = httpd.server_port
        http.pin(f"{lan_address()}:{port}", tls.pinned_context(ident.beacon))
        with pytest.raises(http.ServerUnreachable):
            http.request_json(f"https://{lan_address()}:{port}/health", options=http.Options(token=token))
    finally:
        stop(httpd, runner)


def test_a_certificate_that_has_been_rotated_since_the_beacon_is_refused(tmp_path, service_authority):
    old = tls.identity(tmp_path / "tls", "d", now=time.time() - 70 * 86400, days=90)
    renewed = tls.identity(tmp_path / "tls", "d", days=90)
    assert renewed.der != old.der
    httpd, runner, token = serve(tmp_path, renewed, service_authority)
    try:
        port = httpd.server_port
        http.pin(f"{lan_address()}:{port}", tls.pinned_context(old.beacon))
        with pytest.raises(http.ServerUnreachable):
            http.request_json(f"https://{lan_address()}:{port}/health", options=http.Options(token=token))
        http.pin(f"{lan_address()}:{port}", tls.pinned_context(renewed.beacon))
        assert Member(f"https://{lan_address()}:{port}", token).health()["ok"]
    finally:
        stop(httpd, runner)


def test_an_expired_certificate_is_refused_even_when_it_is_the_pinned_one(tmp_path, service_authority):
    expired = tls.identity(tmp_path / "tls", "d", now=time.time() - 200 * 86400, days=90)
    httpd, runner, token = serve(tmp_path, expired, service_authority)
    try:
        port = httpd.server_port
        http.pin(f"{lan_address()}:{port}", tls.pinned_context(expired.beacon))
        with pytest.raises(MemberError, match="unreachable"):
            Member(f"https://{lan_address()}:{port}", token).health()
    finally:
        stop(httpd, runner)


def test_a_member_that_was_never_pinned_is_not_trusted_on_sight(tmp_path, service_authority):
    ident = tls.identity(tmp_path / "tls", "d")
    httpd, runner, token = serve(tmp_path, ident, service_authority)
    try:
        service_authority[0].pop()
        with pytest.raises(PermissionError, match="current node-authorized"):
            Member(f"https://{lan_address()}:{httpd.server_port}", token).health()
    finally:
        stop(httpd, runner)


@pytest.mark.lan_network
def test_plain_http_from_another_machine_is_dropped_but_this_machine_may_use_it(tmp_path, service_authority):
    ident = tls.identity(tmp_path / "tls", "d")
    httpd, runner, _ = serve(tmp_path, ident, service_authority, host=ALL_INTERFACES)
    try:
        port = httpd.server_port
        with socket.create_connection((lan_address(real=True), port), timeout=5) as sock:
            sock.sendall(b"GET /health HTTP/1.0\r\n\r\n")
            with contextlib.suppress(ConnectionResetError):
                assert sock.recv(100) == b""
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.sendall(b"GET /health HTTP/1.0\r\n\r\n")
            assert sock.recv(12).startswith(b"HTTP/1.")
    finally:
        stop(httpd, runner)


def test_a_request_signed_for_one_pool_is_refused_by_another_over_tls(tmp_path, service_authority):
    ident = tls.identity(tmp_path / "tls", "d")
    httpd, runner, token = serve(tmp_path, ident, service_authority)
    try:
        port = httpd.server_port
        context = tls.pinned_context(ident.beacon)
        other = macauth.derive(b"another-pools-key-of-32-bytes!")
        http.pin(f"{lan_address()}:{port}", context)
        with pytest.raises(http.ServerError, match="401"):
            http.request_json(f"https://{lan_address()}:{port}/jobs", options=http.Options(token=other))
        # the daemon's own request, replayed byte for byte on a second connection
        url = f"https://{lan_address()}:{port}/jobs"
        sent = macauth.sign(token, "GET", url, None)
        raw = (f"GET /jobs HTTP/1.1\r\nHost: {lan_address()}:{port}\r\nAuthorization: "
               f"{sent['Authorization']}\r\nConnection: close\r\n\r\n").encode()
        replies = []
        for _ in range(2):
            with socket.create_connection((lan_address(), port), timeout=5) as sock, \
                    context.wrap_socket(sock) as secure:
                secure.sendall(raw)
                replies.append(int(secure.recv(20).split()[1]))
        assert replies == [200, 401]
    finally:
        stop(httpd, runner)


# -- the beacon -------------------------------------------------------------------------


def test_a_beacon_carries_the_certificate_and_names_an_https_address(tmp_path):
    ident = tls.identity(tmp_path / "tls", "d")
    beacon = Beacon(name="d", port=8770, host="10.1.2.3", cert=ident.beacon)
    assert beacon.base_url == "https://10.1.2.3:8770" and beacon.public()["cert"] == ident.beacon
    assert Beacon(name="d", port=8770, host="10.1.2.3").base_url == "http://10.1.2.3:8770"


def test_a_machine_that_offers_no_certificate_is_not_talked_to(tmp_path):
    from poolhouse.fleet import discovery

    ident = tls.identity(tmp_path / "tls", "d")
    bare = Beacon(name="bare", port=1, host="192.0.2.9")
    sealed = Beacon(name="sealed", port=2, host="192.0.2.10", cert=ident.beacon)
    here = Beacon(name="here", port=3, host="127.0.0.1")
    assert discovery._trusted(bare) is False
    assert discovery._trusted(sealed) is True and "192.0.2.10:2" in http._PINNED
    assert discovery._trusted(here) is True


def test_an_advertised_certificate_is_pinned_by_discovery_over_real_sockets(tmp_path):
    from poolhouse.fleet.discovery import Advertiser, create_pool_key

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        udp = probe.getsockname()[1]
    ident = tls.identity(tmp_path / "tls", "d")
    key = create_pool_key(tmp_path / "k", group="poolhouse").encode()
    membership.roster(key).enrol(ident.beacon, "d", "test")
    tell = Advertiser(Beacon(name="d", port=8770, cert=ident.beacon), key, port=udp,
                      interval_s=30).start()
    try:
        found = discover(key, timeout_s=3.0, port=udp)
        assert [b.name for b in found] == ["d"] and found[0].cert == ident.beacon
        assert any(k.endswith(":8770") for k in http._PINNED)
    finally:
        tell.stop()


def test_json_of_a_beacon_never_holds_the_key_the_secret_or_the_private_half(tmp_path):
    ident = tls.identity(tmp_path / "tls", "d")
    text = json.dumps(Beacon(name="d", port=1, cert=ident.beacon).public())
    assert ident.keyfile.read_text().splitlines()[1] not in text
