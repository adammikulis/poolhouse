"""Hostile replies and redirects at the node socket, the pool's project source and the WSL bridge."""

import io
import json
import shutil
import socket
import struct
import tempfile
import threading
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse import node_health, node_launch, sealing
from poolhouse.board.client import Client
from poolhouse.fleet import project_client, wsl_network
from poolhouse.http import Sealed, ServerError

PROJECT = "a" * 32


@pytest.fixture
def squatter():
    """A server on the socket a node would own, answering the first request with what the test says."""
    root = Path(tempfile.mkdtemp(prefix="ml", dir="/tmp"))
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(root / node_health.SOCKET))
    listener.listen(1)

    def answer(reply: bytes) -> None:
        def serve() -> None:
            connection, _ = listener.accept()
            with connection:
                (size,) = struct.unpack(">I", connection.recv(4))
                connection.recv(size)
                connection.sendall(reply)
        threading.Thread(target=serve, daemon=True).start()

    try:
        yield root, answer
    finally:
        listener.close()
        shutil.rmtree(root, ignore_errors=True)


def framed(body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + body


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="needs a Unix socket")
def test_a_reply_longer_than_a_frame_is_refused_without_waiting_for_its_body(squatter):
    root, answer = squatter
    answer(struct.pack(">I", node_health.MAX_FRAME + 1))
    with pytest.raises(ValueError, match="oversized"):
        node_health.exchange(root, b"{}", 5)


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="needs a Unix socket")
def test_a_reply_that_ends_before_its_length_is_a_closed_connection(squatter):
    root, answer = squatter
    answer(struct.pack(">I", 100) + b"short")
    with pytest.raises(ConnectionError):
        node_health.exchange(root, b"{}", 5)


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="needs a Unix socket")
@pytest.mark.parametrize("raw", [b"not JSON", b"[]", b"null", b'"ok"'], ids=["malformed", "array", "null", "string"])
def test_the_client_takes_only_a_json_object_from_a_node(squatter, monkeypatch, raw):
    root, answer = squatter
    monkeypatch.setattr(node_launch, "ensure_node", lambda state: None)
    answer(framed(raw))
    with pytest.raises(ValueError):
        Client(root).call("hello")


@pytest.mark.parametrize("sent,error", [(struct.pack(">I", node_health.MAX_FRAME + 1), ValueError), (b"", ConnectionError),
                                        (struct.pack(">I", 8) + b"half", ConnectionError)], ids=["oversized", "closed", "cut"])
def test_a_pipe_or_socket_reader_stops_at_the_frame_limit_and_at_a_closed_connection(sent, error):
    with pytest.raises(error):
        node_health._reply(io.BytesIO(sent).read)


@pytest.mark.parametrize("raw", [b"x" * (wsl_network.LIMIT + 1), b"not JSON\n", b"[]\n", b"null\n", b""],
                         ids=["oversized", "malformed", "array", "null", "closed"])
def test_wsl_control_replies_reject_oversized_and_malformed_frames(raw):
    with pytest.raises((OSError, ValueError)):
        wsl_network._read(io.BytesIO(raw))


@pytest.mark.parametrize("target", ["https://8.8.8.8/steal", "http://127.0.0.1:8771/steal",
                                    "http://127.0.0.1:8770/other"])
def test_project_source_redirect_stays_on_exact_authority(monkeypatch, target):
    followed = []

    @contextmanager
    def redirect(_url, **kwargs):
        kwargs["guard"](target)
        followed.append(target)
        yield None

    monkeypatch.setattr(project_client, "open_stream", redirect)
    with pytest.raises(project_client.ProjectError, match="cannot redirect"):
        project_client.read(SimpleNamespace(base_url="http://127.0.0.1:8770", token="secret", timeout=30), "/workspace/v1/projects", 100)
    assert not followed


@pytest.mark.parametrize("length", [63, 64, 65])
def test_project_source_sealed_wire_and_plaintext_boundary(monkeypatch, length):
    key, nonce, limit = bytes(range(32)), "request-nonce", 64
    raw = sealing.seal(key, b"x" * length, sealing.response_data(nonce, 200))
    sizes = []

    @contextmanager
    def reply(_url, **_kwargs):
        def read(size):
            sizes.append(size)
            return raw[:size]
        yield SimpleNamespace(read=read, status=200, headers={sealing.HEADER: "2"}, sealed=Sealed(key, nonce))

    monkeypatch.setattr(project_client, "open_stream", reply)
    member = SimpleNamespace(base_url="http://127.0.0.1:8770", token="secret", timeout=30)
    if length > limit:
        with pytest.raises(project_client.ProjectError, match="size limit"):
            project_client.read(member, "/workspace/v1/projects", limit)
    else:
        assert project_client.read(member, "/workspace/v1/projects", limit) == b"x" * length
    assert sizes == [limit + sealing.NONCE_BYTES + 16 + 1]


@pytest.mark.parametrize("headers,raw", [({}, b"not authenticated"), ({sealing.HEADER: "2"}, b"forged seal")])
def test_project_source_requires_authenticated_sealed_response(monkeypatch, headers, raw):
    @contextmanager
    def reply(_url, **_kwargs):
        yield SimpleNamespace(read=lambda _size: raw, status=200, headers=headers,
                              sealed=Sealed(bytes(range(32)), "request-nonce"))

    monkeypatch.setattr(project_client, "open_stream", reply)
    with pytest.raises((project_client.ProjectError, ServerError)):
        project_client.read(SimpleNamespace(base_url="http://127.0.0.1:8770", token="secret", timeout=30), "/workspace/v1/projects", 100)


@pytest.mark.parametrize("override", [{"source_hash": "../../escape"}, {"archive_sha256": "wrong"},
    {"id": "../../etc"}, {"name": []}, {"source_machine": {}},
    {"files": True}, {"files": 10001}, {"size_bytes": -1}, {"size_bytes": "128"}])
def test_project_catalogue_schema_is_checked_before_snapshot_url(monkeypatch, override):
    project = {"id": "a" * 32, "name": "App", "source_machine": "source-a",
               "board_host": "", "source_hash": "b" * 64,
               "archive_sha256": "c" * 64, "size_bytes": 10, "files": 1, **override}
    monkeypatch.setattr(project_client, "read", lambda *_args, **_kwargs: json.dumps({"machine": "source-a", "projects": [project]}).encode())
    with pytest.raises((project_client.ProjectError, OSError)):
        project_client.catalogue(SimpleNamespace())


@pytest.mark.parametrize("projects", [[None], [1], [{}], ["project"], "projects"])
def test_project_catalogue_rejects_invalid_members_without_type_crash(monkeypatch, projects):
    monkeypatch.setattr(project_client, "read", lambda *_args, **_kwargs: json.dumps({"machine": "source-a", "projects": projects}).encode())
    with pytest.raises(project_client.ProjectError):
        project_client.catalogue(SimpleNamespace())


@pytest.mark.redteam
@pytest.mark.parametrize("attack", ["oversized", "unsealed", "tampered", "invalid-json", "wrong-project", "redirect"])
def test_local_registration_refuses_hostile_response(tmp_path, monkeypatch, attack):
    endpoint = "http://127.0.0.1:8770/workspace/v1/local-project"
    sealed = Sealed(b"s" * 32, "registration-nonce")
    plain = b"not JSON" if attack == "invalid-json" else b'{"id":"' + PROJECT.encode() + b'"}'
    if attack == "wrong-project":
        plain = b'{"id":"' + b"b" * 32 + b'"}'
    raw = sealing.seal(sealed.key, plain, sealing.response_data(sealed.nonce, 200))
    if attack == "tampered":
        raw = raw[:-1] + bytes([raw[-1] ^ 1])
    if attack == "oversized":
        raw = b"x" * (65536 + sealing.NONCE_BYTES + 17)
    response = SimpleNamespace(status=200, headers={} if attack == "unsealed" else {sealing.HEADER: "2"},
                               sealed=sealed, read=lambda limit: raw[:limit])
    calls = []

    def transport(url, **options):
        calls.append((url, options))
        assert options["guard"](url) == endpoint
        assert options["timeout"] == 2
        assert options["headers"][sealing.HEADER] == "2"
        if attack == "redirect":
            options["guard"]("https://foreign.invalid/workspace/v1/local-project")
        return nullcontext(response)

    monkeypatch.setattr(project_client, "open_stream", transport)
    with pytest.raises((project_client.ProjectError, ValueError)):
        project_client.register_local(tmp_path, b"fixture-dev-key", PROJECT, 8770)
    assert len(calls) == 1 and calls[0][0] == endpoint
