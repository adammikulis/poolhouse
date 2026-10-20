"""Actual private Unix/TLS boundaries dispatch only fixed bounded operations."""

import contextlib
import json
import os
import socket
import ssl
import tempfile
import threading
import time
from pathlib import Path

import pytest

from poolhouse.fleet import producer_bootstrap, producer_control as control, tls

NONCE = "1" * 64


@pytest.fixture
def factory():
    servers = []
    with tempfile.TemporaryDirectory(prefix="control-", dir="/tmp") as directory:
        root = Path(directory).resolve()
        root.chmod(0o700)
        secret = root / "app-identity"
        secret.write_text("ab" * 32)
        secret.chmod(0o600)
        identity = tls.identity(root / "tls", "control-test")

        def create(*, dispatch=None, bridge=None, current=None):
            server = control.ControlServer(
                root,
                identity=identity,
                current=current or (lambda: {"pool": "a" * 64, "runtime": "b" * 64}),
                dispatch=dispatch or (lambda action, request, process: {"action": action}),
                bridge=bridge or (lambda process: False),
            )
            servers.append(server)
            return server

        yield create, root, identity
        for server in servers:
            server.close()
            assert not server._connections and not server._workers


def connect(server, identity=None):
    channel = socket.socket(socket.AF_UNIX)
    channel.settimeout(2)
    channel.connect(server.control_path if identity else server.bootstrap_path)
    if identity:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.load_verify_locations(cafile=str(identity.certfile))
        channel = context.wrap_socket(channel, server_hostname="localhost")
    return channel


def raw_frame(raw):
    return len(raw).to_bytes(4, "big") + raw


def read(channel):
    def exact(size):
        result = b""
        while len(result) < size:
            part = channel.recv(size - len(result))
            if not part:
                raise EOFError("No producer response")
            result += part
        return result

    return json.loads(exact(int.from_bytes(exact(4), "big")))


def closed(channel):
    with contextlib.suppress(BrokenPipeError, ConnectionResetError, ssl.SSLEOFError):
        assert channel.recv(4) == b""


def request(server, identity, action="receivers", body=None):
    with connect(server, identity) as channel:
        channel.sendall(
            raw_frame(json.dumps({"version": 1, "action": action, "request": body or {}}).encode())
        )
        return read(channel)


def wait(check):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.005)
    pytest.fail("Private listener did not settle")


def test_real_bootstrap_then_exact_tls_control(factory):
    create, root, identity = factory
    calls = []
    server = create(
        dispatch=lambda action, body, process: calls.append((action, body, process))
        or {"receivers": []}
    )
    with connect(server) as channel:
        channel.sendall(raw_frame(json.dumps({"nonce": NONCE}).encode()))
        binding = producer_bootstrap.authenticate(root, NONCE, read(channel), channel)
    with connect(server, identity) as channel:
        assert (
            producer_bootstrap.verify_control(binding, channel)["control_path"]
            == server.control_path
        )
        channel.sendall(raw_frame(b'{"version":1,"action":"receivers","request":{}}'))
        assert read(channel) == {"ok": True, "result": {"receivers": []}}
    assert len(calls) == 1 and calls[0][2]["pid"] == os.getpid()
    assert Path(server.bootstrap_path).stat().st_mode & 0o777 == 0o600
    assert Path(server.control_path).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "raw",
    [
        b'{"nonce":"a","nonce":"b"}',
        b'{"nonce":NaN}',
        b'{"nonce":1e309}',
        b'{"nonce":"a"} trailing',
        b"[]",
        b'{"nonce":"a","extra":1}',
    ],
)
def test_bootstrap_rejects_ambiguous_or_extra_json(factory, raw):
    create, _root, _identity = factory
    server = create()
    with connect(server) as channel:
        channel.sendall(raw_frame(raw))
        assert read(channel)["ok"] is False


@pytest.mark.parametrize(
    "action,body",
    [
        ("unknown", {}),
        ("receivers", {"extra": 1}),
        ("offer", {"receiver_id": "x", "label": "x" * 257}),
        ("offer-status", {"offer_id": "../"}),
        ("withdraw", {"offer_id": "a" * 32, "extra": 1}),
        ("bridge-publish", {}),
        ("bridge-receipt", {}),
    ],
)
def test_ordinary_sdk_cannot_choose_operations_or_private_bridge(factory, action, body):
    create, _root, identity = factory
    calls = []
    server = create(dispatch=lambda *args: calls.append(args) or {})
    assert request(server, identity, action, body)["ok"] is False
    assert calls == []


def test_admitted_bridge_large_frame_and_revocation_before_reply(factory):
    create, _root, identity = factory
    enabled, calls = {"value": True}, []
    server = create(
        bridge=lambda _process: enabled["value"],
        dispatch=lambda action, body, process: calls.append(body) or {"accepted": True},
    )
    assert (
        request(server, identity, "bridge-publish", {"data": "x" * control.METADATA_BYTES})["ok"]
        is True
    )
    enabled["value"] = False
    assert request(server, identity, "bridge-publish")["ok"] is False
    assert len(calls) == 1


def test_oversize_sdk_frame_and_response_are_bounded(factory):
    create, _root, identity = factory
    server = create(dispatch=lambda *_: {"data": "x" * (control.METADATA_BYTES + 1)})
    assert request(server, identity)["code"] == "invalid"
    with connect(server, identity) as channel:
        channel.sendall((control.METADATA_BYTES + 1).to_bytes(4, "big"))
        assert read(channel)["code"] == "invalid"


def test_one_connection_never_dispatches_a_second_frame(factory):
    create, _root, identity = factory
    calls = []
    server = create(dispatch=lambda *args: calls.append(args) or {})
    frame = raw_frame(b'{"version":1,"action":"receivers","request":{}}')
    with connect(server, identity) as channel:
        channel.sendall(frame + frame)
        assert read(channel)["ok"] is True
    wait(lambda: not server._workers)
    assert len(calls) == 1


def test_current_change_after_dispatch_has_unknown_reply_without_repeat(factory):
    create, _root, identity = factory
    state, calls = {"pool": "a" * 64, "runtime": "b" * 64}, []

    def dispatch(*args):
        calls.append(args)
        state["pool"] = "c" * 64
        return {"private": "never delivered"}

    server = create(current=lambda: dict(state), dispatch=dispatch)
    with pytest.raises((EOFError, OSError)):
        request(server, identity)
    assert len(calls) == 1


def test_bridge_revocation_during_dispatch_suppresses_reply(factory):
    create, _root, identity = factory
    enabled = {"value": True}

    def dispatch(*args):
        enabled["value"] = False
        return {"private": "never delivered"}

    server = create(bridge=lambda _process: enabled["value"], dispatch=dispatch)
    with pytest.raises((EOFError, OSError)):
        request(server, identity, "bridge-receipt")


def test_partial_tls_and_frames_expire_and_close_unblocks_owned_threads(factory, monkeypatch):
    create, _root, _identity = factory
    monkeypatch.setattr(control, "SOCKET_S", 0.15)
    monkeypatch.setattr(control, "HANDSHAKE_S", 0.1)
    server = create()
    with connect(server) as partial:
        partial.sendall(b"\x00\x00")
        assert partial.recv(1) == b""
    with socket.socket(socket.AF_UNIX) as partial_tls:
        partial_tls.settimeout(2)
        partial_tls.connect(server.control_path)
        partial_tls.sendall(b"\x16")
        assert partial_tls.recv(1) == b""
    with connect(server) as blocked:
        wait(lambda: bool(server._workers))
        server.close()
        assert blocked.recv(1) == b""
    assert not server._workers


def test_connection_admission_is_bounded_before_threads(factory, monkeypatch):
    create, _root, _identity = factory
    monkeypatch.setattr(control, "MOST_CONNECTIONS", 2)
    server = create()
    with connect(server) as _one, connect(server) as _two:
        wait(lambda: len(server._workers) == 2)
        with connect(server) as excess:
            assert excess.recv(1) == b""
        assert len(server._workers) == 2
    wait(lambda: not server._workers)


def test_replaced_listener_is_refused_and_never_unlinked_on_close(factory):
    create, _root, identity = factory
    server = create()
    target = Path(server.bootstrap_path)
    target.unlink()
    target.write_text("replacement")
    with pytest.raises((EOFError, OSError)):
        request(server, identity)
    server.close()
    assert target.read_text() == "replacement"


def test_plain_control_and_nonfinite_callback_never_return_success(factory):
    create, _root, identity = factory
    server = create(dispatch=lambda *_: {"invalid": float("nan")})
    assert request(server, identity)["ok"] is False
    with socket.socket(socket.AF_UNIX) as plain:
        plain.settimeout(2)
        plain.connect(server.control_path)
        plain.sendall(raw_frame(b'{"version":1,"action":"receivers","request":{}}'))
        with pytest.raises((EOFError, OSError)):
            read(plain)


def test_response_retention_uses_original_deadline_and_stop(factory, monkeypatch):
    create, _root, identity = factory
    monkeypatch.setattr(control, "SOCKET_S", 0.2)
    server = create()
    with connect(server, identity) as channel:
        channel.sendall(raw_frame(b'{"version":1,"action":"receivers","request":{}}'))
        assert read(channel)["ok"] is True
        closed(channel)
    with connect(server, identity) as channel:
        channel.sendall(raw_frame(b'{"version":1,"action":"receivers","request":{}}'))
        assert read(channel)["ok"] is True
        server.close()
        closed(channel)
    assert not server._workers


def test_retention_error_never_appends_second_response(factory, monkeypatch):
    create, _root, identity = factory

    def interrupted(*_args):
        raise OSError("private implementation detail")

    monkeypatch.setattr(control, "_retain", interrupted)
    server = create()
    with connect(server, identity) as channel:
        channel.sendall(raw_frame(b'{"version":1,"action":"receivers","request":{}}'))
        assert read(channel)["ok"] is True
        closed(channel)


def test_current_change_during_tls_handshake_prevents_dispatch(factory):
    create, _root, identity = factory
    state, calls = {"pool": "a" * 64, "runtime": "b" * 64}, []

    def changed(_process):
        state["runtime"] = "c" * 64
        return False

    server = create(
        current=lambda: dict(state), bridge=changed, dispatch=lambda *args: calls.append(args) or {}
    )
    with pytest.raises((EOFError, OSError)):
        request(server, identity)
    assert calls == []


def test_partial_body_and_unread_large_reply_obey_deadline(factory, monkeypatch):
    create, _root, identity = factory
    monkeypatch.setattr(control, "SOCKET_S", 0.2)
    server = create(
        bridge=lambda _process: True, dispatch=lambda *_: {"data": "x" * control.MAX_BODY}
    )
    with connect(server) as partial:
        partial.sendall((100).to_bytes(4, "big") + b"{")
        assert partial.recv(1) == b""
    with connect(server, identity) as unread:
        unread.sendall(raw_frame(b'{"version":1,"action":"bridge-receipt","request":{}}'))
        wait(lambda: bool(server._workers))
        wait(lambda: not server._workers)
    assert not server._connections


@pytest.mark.parametrize(
    "raw",
    [
        b'{"version":true,"action":"receivers","request":{}}',
        b'{"version":1,"action":"receivers","request":{},"extra":1}',
        b'{"version":1,"action":"receivers","request":[]}',
        b'{"version":1,"action":"receivers","action":"offer","request":{}}',
    ],
)
def test_control_envelope_is_exact_before_fixed_dispatch(factory, raw):
    create, _root, identity = factory
    calls = []
    server = create(dispatch=lambda *args: calls.append(args) or {})
    with connect(server, identity) as channel:
        channel.sendall(raw_frame(raw))
        assert read(channel)["ok"] is False
    assert calls == []


def test_repeated_close_never_uses_recycled_directory_descriptor(factory, monkeypatch):
    create, root, _identity = factory
    server = create()
    directory_fd = server._location.directory
    server.close()
    unrelated = root / "unrelated"
    unrelated.mkdir()
    sentinel = unrelated / "bootstrap.sock"
    sentinel.write_text("not a listener")
    owned = os.open(unrelated, os.O_RDONLY | os.O_DIRECTORY)
    reused = os.dup2(owned, directory_fd)
    original_stat = os.stat

    def no_recycled_access(path, *args, **options):
        assert options.get("dir_fd") != reused, "Repeated close accessed a recycled descriptor"
        return original_stat(path, *args, **options)

    monkeypatch.setattr(os, "stat", no_recycled_access)
    try:
        server.close()
        assert os.fstat(reused).st_ino == unrelated.stat().st_ino
        assert sentinel.read_text() == "not a listener"
    finally:
        os.close(reused)
        if owned != reused:
            os.close(owned)


@pytest.mark.parametrize("failure", ["chmod", "post-chmod-stat"])
def test_bind_failure_removes_only_captured_listener_inode(factory, monkeypatch, failure):
    _create, root, _identity = factory
    location = control._Location(root)
    path = location.path
    original_chmod, original_stat = os.chmod, os.stat
    changed = {"value": False, "raised": False}

    def chmod(name, *args, **options):
        if name == "bootstrap.sock" and failure == "chmod":
            raise PermissionError("Injected chmod failure")
        result = original_chmod(name, *args, **options)
        if name == "bootstrap.sock":
            changed["value"] = True
        return result

    def stat_after_chmod(name, *args, **options):
        if name == "bootstrap.sock" and changed["value"] and not changed["raised"]:
            changed["raised"] = True
            raise OSError("Injected post-bind stat failure")
        return original_stat(name, *args, **options)

    monkeypatch.setattr(os, "chmod", chmod)
    monkeypatch.setattr(os, "stat", stat_after_chmod)
    try:
        with pytest.raises(OSError):
            location.bind("bootstrap.sock")
    finally:
        location.close()
    assert not path.exists()


def test_bind_failure_never_unlinks_substituted_inode(factory, monkeypatch):
    _create, root, _identity = factory
    location = control._Location(root)
    path = location.path / "bootstrap.sock"

    def substitute(*_args, **_options):
        path.unlink()
        path.write_text("replacement is not owned")
        raise PermissionError("Injected replacement")

    monkeypatch.setattr(os, "chmod", substitute)
    try:
        with pytest.raises(OSError):
            location.bind("bootstrap.sock")
    finally:
        location.close()
    assert path.read_text() == "replacement is not owned"


def test_concurrent_close_hands_off_each_descriptor_once(factory, monkeypatch):
    create, _root, _identity = factory
    server = create()
    descriptors = set(server._location.descriptors)
    counts, errors = {}, []
    original_close = os.close

    def close_once(fd):
        if threading.current_thread().name.startswith("owned-cleanup-") and fd in descriptors:
            counts[fd] = counts.get(fd, 0) + 1
        return original_close(fd)

    monkeypatch.setattr(os, "close", close_once)
    ready = threading.Barrier(2)

    def close():
        try:
            ready.wait(timeout=2)
            server.close()
        except (OSError, RuntimeError) as exc:
            errors.append(exc)

    threads = [threading.Thread(target=close, name=f"owned-cleanup-{index}") for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    assert not errors and counts == dict.fromkeys(descriptors, 1)
    assert server._location.directory is None and server._location.parent is None
    assert not server._location.anchors and not server._location.sockets


def test_partial_location_constructor_removes_captured_directory(factory, monkeypatch):
    _create, root, _identity = factory
    original = os.open

    def refuse_child(path, *args, **options):
        if str(path).startswith("producer-"):
            raise PermissionError("Injected private-directory open failure")
        return original(path, *args, **options)

    monkeypatch.setattr(os, "open", refuse_child)
    with pytest.raises(OSError):
        control._Location(root)
    assert not list(root.glob("producer-*"))


def test_successful_chmod_cannot_transfer_listener_ownership(factory, monkeypatch):
    _create, root, _identity = factory
    location = control._Location(root)
    path = location.path / "bootstrap.sock"
    original = os.chmod

    def substitute(name, *args, **options):
        path.unlink()
        path.write_text("replacement must remain")
        return original(name, *args, **options)

    monkeypatch.setattr(os, "chmod", substitute)
    try:
        with pytest.raises(OSError):
            location.bind("bootstrap.sock")
    finally:
        location.close()
    assert path.read_text() == "replacement must remain"
