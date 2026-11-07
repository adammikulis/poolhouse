"""The local client's root credential never follows a redirected key path."""

import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from poolhouse import node_health

pytest_plugins = ["node_kit"]


@pytest.mark.skipif(os.name == "nt", reason="Unix socket server credentials")
def test_a_socket_exposes_the_kernel_observed_server_process():
    first, second = socket.socketpair()
    with first, second:
        assert node_health._socket_server_pid(first) == os.getpid()


@pytest.mark.skipif(os.name == "nt", reason="Unix descriptor-relative custody")
def test_the_root_credential_reader_refuses_a_symlinked_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (outside / "root.key").write_text("11" * 32)
    (outside / "root.key").chmod(0o600)
    (tmp_path / "hook-credentials").symlink_to(outside, target_is_directory=True)
    body = node_health.request_body("register", {"model": "", "harness": "test", "session": "one"}, board="demo")
    frame = struct.pack(">I", len(body)) + body
    sent = []
    assert node_health._root_frame(tmp_path, frame, sent.append, lambda _count: b"", os.getpid) == frame
    assert not sent, "a redirected key must not be used to answer a challenge"
    (tmp_path / "hook-credentials").unlink()
    (tmp_path / "hook-credentials").mkdir(mode=0o700)
    (tmp_path / "hook-credentials" / "root.key").symlink_to(outside / "root.key")
    assert node_health._root_frame(tmp_path, frame, sent.append, lambda _count: b"", os.getpid) == frame
    assert not sent, "a redirected key file must not be used either"


@pytest.mark.skipif(os.name == "nt", reason="Unix socket transport")
def test_the_client_resolves_the_state_before_connecting_and_reading_its_key(tmp_path, monkeypatch):
    fake, victim = tmp_path / "fake", tmp_path / "victim"
    fake.mkdir()
    victim.mkdir()
    state = tmp_path / "state"
    state.symlink_to(fake, target_is_directory=True)
    seen = []
    reply = json.dumps({"ok": True, "result": {}}).encode()
    data = bytearray(struct.pack(">I", len(reply)) + reply)

    class Socket:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def settimeout(self, _timeout): pass
        def connect(self, _path):
            state.unlink()
            state.symlink_to(victim, target_is_directory=True)
        def sendall(self, _frame): pass
        def recv(self, count):
            result = bytes(data[:count])
            del data[:count]
            return result

    def check_state(path, frame, _send, _recv, _server_pid):
        seen.append(path.resolve())
        return frame

    monkeypatch.setattr(node_health.socket, "socket", lambda *_args: Socket())
    monkeypatch.setattr(node_health, "_root_frame", check_state)
    node_health.exchange(state, node_health.request_body("hello"), timeout=1)
    assert seen == [fake.resolve()], "the connected state and credential state must use one resolved path"


@pytest.mark.skipif(os.name == "nt", reason="Unix FIFO and descriptor-relative custody")
def test_a_fifo_in_place_of_the_root_key_does_not_hang_the_client(tmp_path):
    folder = tmp_path / "hook-credentials"
    folder.mkdir(mode=0o700)
    os.mkfifo(folder / "root.key", 0o600)
    code = ("import sys; from pathlib import Path; from poolhouse import node_health; "
            "body=node_health.request_body('register', {'harness':'test','session':'s'}, board='demo'); "
            "import struct; frame=struct.pack('>I',len(body))+body; "
            "assert node_health._root_frame(Path(sys.argv[1]),frame,lambda _:None,lambda _:b'',lambda:1) == frame")
    done = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=5, check=False)
    assert done.returncode == 0, done.stderr


@pytest.mark.skipif(os.name == "nt", reason="simulated Windows pipe with a Unix symlink")
def test_windows_pipe_keeps_the_state_spelling_used_to_name_the_server(tmp_path, monkeypatch):
    target = tmp_path / "actual"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    seen = []
    monkeypatch.setattr(node_health, "is_windows", lambda: True)
    monkeypatch.setattr(node_health, "_exchange_pipe", lambda state, _frame, _timeout: seen.append(state) or b"{}")
    node_health.exchange(alias, b"{}")
    assert seen == [alias], "the pipe name must match the path the node used"


@pytest.mark.skipif(os.name == "nt", reason="Unix socket transport")
def test_python_client_proof_registers_unicode_fields_against_the_node(workspace_node):
    body = node_health.request_body("register", {"model": "", "harness": "test-é", "session": "会話-😀"}, board="demo")
    frame = struct.pack(">I", len(body)) + body
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(5)
        stream.connect(str(node_health.socket_path(workspace_node.state)))
        signed = node_health._root_frame(workspace_node.state, frame, stream.sendall, stream.recv, lambda: node_health._socket_server_pid(stream))
        assert signed != frame, "the client must answer the node's challenge"
        stream.sendall(signed)
        reply = json.loads(node_health._reply(stream.recv))
    assert reply["ok"] is True, "the node must accept the Python client's Unicode proof"


@pytest.mark.skipif(os.name == "nt", reason="Unix socket transport")
def test_a_separate_fake_server_cannot_relay_a_root_proof_to_the_node(workspace_node):
    code = """
import socket, struct, sys
def frame(source):
    def read(n):
        data = b''
        while len(data) < n:
            block = source.recv(n-len(data))
            if not block: raise EOFError()
            data += block
        return data
    header = read(4)
    return header + read(struct.unpack('>I', header)[0])
listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
listener.bind(sys.argv[1]); listener.listen(1)
print('ready', flush=True)
client, _ = listener.accept()
node = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); node.connect(sys.argv[2])
node.sendall(frame(client)); client.sendall(frame(node))
node.sendall(frame(client)); client.sendall(frame(node))
client.close(); node.close(); listener.close()
"""
    with tempfile.TemporaryDirectory(prefix="root-relay-", dir="/tmp") as scratch:
        fake = str(Path(scratch) / "socket")
        process = subprocess.Popen([sys.executable, "-c", code, fake, str(node_health.socket_path(workspace_node.state))],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            import select
            assert process.stdout is not None
            assert select.select([process.stdout], [], [], 5)[0], "the fake endpoint did not start"
            assert process.stdout.readline().strip() == "ready"
            body = node_health.request_body("register", {"model": "", "harness": "test", "session": "relay"}, board="demo")
            frame = struct.pack(">I", len(body)) + body
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
                stream.settimeout(5)
                stream.connect(fake)
                signed = node_health._root_frame(workspace_node.state, frame, stream.sendall, stream.recv, lambda: node_health._socket_server_pid(stream))
                stream.sendall(signed)
                reply = json.loads(node_health._reply(stream.recv))
            assert reply["ok"] is False, "a different process cannot relay the response to the real node"
            assert process.wait(timeout=5) == 0
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
