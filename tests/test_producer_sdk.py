"""Real isolated holder/control/DATA sockets never export installation secrets."""

import contextlib
import copy
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import psutil
import pytest

from poolhouse.fleet import (
    producer_client as sdk,
    producer_control as control,
    producer_holder as holder,
    tls,
)

SOURCE = str(Path(__file__).resolve().parents[1] / "src")
OFFER = "a" * 32
NONCE = "1" * 64


def offer(state="connected"):
    return {
        "id": OFFER,
        "revision": 1,
        "label": "Record sender",
        "device_label": "Test workstation",
        "software_label": "Test process",
        "state": state,
    }


class DataServer:
    def __init__(self, root):
        self.path = str(root / "data.sock")
        self.process = {"pid": os.getpid(), "started": psutil.Process().create_time()}
        self.stop, self.received, self.raw, self.errors = threading.Event(), [], [], []
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(self.path)
        self.listener.listen()
        self.listener.settimeout(0.02)
        self.channel = None
        self.thread = threading.Thread(target=self.serve)
        self.thread.start()

    def serve(self):
        while not self.stop.is_set():
            try:
                channel, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with channel:
                self.channel = channel
                channel.settimeout(2)
                try:
                    header = channel.recv(4)
                    self.raw.append(header)
                    if not header:
                        continue
                    length = int.from_bytes(header, "big")
                    raw = b""
                    while len(raw) < length:
                        piece = channel.recv(length - len(raw))
                        if not piece:
                            raise OSError("Incomplete DATA record")
                        raw += piece
                    request = json.loads(raw)
                    self.received.append(request)
                    result = {"event_id": request["event_id"], "state": "queued"}
                    encoded = json.dumps({"ok": True, "result": result}).encode()
                    channel.sendall(len(encoded).to_bytes(4, "big") + encoded)
                    channel.recv(1)
                except (OSError, ValueError, KeyError) as exc:
                    if not self.stop.is_set():
                        self.errors.append(exc)
                finally:
                    self.channel = None

    def close(self):
        self.stop.set()
        self.listener.close()
        if self.channel is not None:
            self.channel.close()
        self.thread.join(3)
        assert not self.thread.is_alive()


@pytest.fixture
def setup(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="sdk-", dir="/tmp") as directory:
        root = Path(directory).resolve()
        root.chmod(0o700)
        secret = root / "app-identity"
        secret.write_text("ab" * 32)
        secret.chmod(0o600)
        identity = tls.identity(root / "tls", "sdk-test")
        data = DataServer(root)
        initial = {"pool": "b" * 64, "runtime": "c" * 64}
        state = {
            "current": dict(initial),
            "channel": {"path": data.path, "process": data.process},
            "offer": offer(),
            "calls": [],
        }

        def current():
            if state["current"] != initial:
                raise PermissionError("The current pool changed")
            return dict(initial)

        def dispatch(action, request, process):
            state["calls"].append((action, request, process))
            if action == "receivers":
                return {"receivers": [{"id": "receiver-one", "label": "Records"}]}
            if action in ("offer", "withdraw"):
                return {"offer": copy.deepcopy(state["offer"])}
            return {
                "offer": copy.deepcopy(state["offer"]),
                "channel": copy.deepcopy(state["channel"]),
            }

        server = control.ControlServer(
            root,
            identity=identity,
            current=current,
            dispatch=dispatch,
            bridge=lambda _process: False,
        )
        locator = root / holder.LOCATOR
        locator.write_text(json.dumps({"version": 1, "bootstrap_path": server.bootstrap_path}))
        locator.chmod(0o600)
        binding = holder.local_anchor(root, NONCE, deadline=time.monotonic() + 3)
        monkeypatch.setattr(holder, "launch_anchor", lambda: copy.deepcopy(binding))
        try:
            yield root, server, data, state, binding
        finally:
            server.close()
            data.close()
            assert not server._workers and not data.errors


def private_command(root):
    script = (
        f"import sys;sys.path.insert(0,{SOURCE!r});"
        "from pathlib import Path;import socket;"
        "from poolhouse.fleet.producer_holder import serve_bootstrap;"
        f"\nwith socket.socket(fileno=int(sys.argv[1])) as channel: serve_bootstrap(channel,root=Path({str(root)!r}))"
    )
    return [sys.executable, "-B", "-c", script]


def test_real_owned_holder_nonce_ack_returns_only_public_anchor(setup):
    root, _server, _data, _state, binding = setup
    result = holder._launch(private_command(root))
    assert result["daemon"] == binding["daemon"] and result["pool"] == binding["pool"]
    assert "ab" * 32 not in json.dumps(result)


def test_public_metadata_publish_and_receipt_use_authenticated_status(setup, monkeypatch):
    _root, _server, data, state, _binding = setup
    client = sdk.ProducerClient()
    monkeypatch.setattr(
        holder.bootstrap, "read_existing", lambda *_: pytest.fail("SDK read a secret")
    )
    assert client.receivers()[0]["id"] == "receiver-one"
    assert client.offer("receiver-one", "Record sender")["id"] == OFFER
    assert client.status(OFFER)["channel"]["process"] == data.process
    assert client.publish(OFFER, "event-1", {"text": "inert data"}) == {
        "event_id": "event-1",
        "state": "queued",
    }
    assert client.receipt(OFFER, "event-1")["event_id"] == "event-1"
    assert data.received == [
        {"action": "publish", "event_id": "event-1", "record": {"text": "inert data"}},
        {"action": "receipt", "event_id": "event-1"},
    ]
    assert all(row[2]["pid"] == os.getpid() for row in state["calls"])


def test_wrong_data_process_receives_zero_record_bytes(setup):
    _root, _server, data, state, _binding = setup
    state["channel"]["process"]["started"] += 1
    with pytest.raises(sdk.ProducerUnavailable):
        sdk.ProducerClient().publish(OFFER, "event-1", {"secret_record": "must stay local"})
    deadline = time.monotonic() + 2
    while not data.raw and time.monotonic() < deadline:
        time.sleep(0.005)
    assert data.raw == [b""] and data.received == []


def test_changed_bridge_generation_or_mismatched_offer_cannot_be_adopted(setup):
    _root, _server, _data, state, _binding = setup
    client = sdk.ProducerClient()
    client.status(OFFER)
    state["channel"]["process"]["started"] += 1
    with pytest.raises(sdk.ProducerUnavailable):
        client.status(OFFER)
    state["offer"]["id"] = "b" * 32
    with pytest.raises(sdk.ProducerUnavailable):
        client.status(OFFER)


def test_anchor_stands_after_bootstrap_expiry_but_pool_change_refuses(setup, monkeypatch):
    _root, _server, _data, state, binding = setup
    client = sdk.ProducerClient()
    monkeypatch.setattr(holder.bootstrap.time, "time", lambda: binding["expires"] + 1)
    assert client.receivers()
    state["current"]["pool"] = "d" * 64
    with pytest.raises(sdk.ProducerUnavailable):
        client.receivers()


@pytest.mark.parametrize("event", ["", "../event", "event\n", "é", "x" * 129])
def test_event_identity_contract_is_ascii_and_bounded_before_data(setup, event):
    _root, _server, data, _state, _binding = setup
    with pytest.raises(ValueError):
        sdk.ProducerClient().publish(OFFER, event, {"text": "example"})
    assert not data.raw


@pytest.mark.parametrize("record", [None, [], {"number": float("nan")}])
def test_records_are_finite_objects_before_data(setup, record):
    _root, _server, data, _state, _binding = setup
    with pytest.raises(ValueError):
        sdk.ProducerClient().publish(OFFER, "event-1", record)
    assert not data.raw


def test_fork_invalidates_client_and_closes_inherited_owned_socket(setup):
    _root, _server, data, _state, _binding = setup
    client = sdk.ProducerClient()
    read_fd, write_fd = os.pipe()
    with client._connected(data.path, data.process, time.monotonic() + 2) as channel:
        descriptor = channel.fileno()
        child = os.fork()
        if child == 0:
            os.close(read_fd)
            refused = False
            try:
                client.receivers()
            except sdk.ProducerUnavailable:
                refused = True
            try:
                os.fstat(descriptor)
                closed = False
            except OSError:
                closed = True
            os.write(write_fd, json.dumps([refused, closed]).encode())
            os._exit(0)
        os.close(write_fd)
        try:
            assert json.loads(os.read(read_fd, 128)) == [True, True]
            assert os.waitpid(child, 0)[1] == 0
        finally:
            os.close(read_fd)
    assert client.receivers()


def test_holder_production_gate_runs_before_root_or_secret(monkeypatch):
    one, two = socket.socketpair()

    def denied(_what):
        raise PermissionError("Not installed")

    monkeypatch.setattr(holder.real_machine, "require", denied)
    monkeypatch.setattr(
        holder, "installed_root", lambda: pytest.fail("Read root before production gate")
    )
    with one:
        assert holder.child(two.detach()) == 1
        assert one.recv(1) == b""


def test_locator_links_and_public_permissions_are_refused(setup):
    root, _server, _data, _state, _binding = setup
    locator = root / holder.LOCATOR
    locator.chmod(0o644)
    with pytest.raises(holder.bootstrap.BootstrapUnavailable):
        holder._location(root)
    locator.unlink()
    target = root / "target"
    target.write_text("{}")
    locator.symlink_to(target)
    with pytest.raises(OSError):
        holder._location(root)


def untrusted_holder_command(binding, mode):
    script = f"""
import json,os,socket,sys,time,signal
sys.path.insert(0,{SOURCE!r})
from poolhouse.fleet.producer_control import receive_frame,send_frame
from poolhouse.fleet.job_records import process_started
with socket.socket(fileno=int(sys.argv[1])) as channel:
    deadline=time.monotonic()+10
    request=receive_frame(channel,deadline=deadline)
    binding=json.loads({json.dumps(binding)!r})
    binding.update(nonce=request['nonce'],issued=int(time.time()),expires=int(time.time())+30)
    reply={{'version':1,'nonce':request['nonce'],'binding':binding,'holder':{{'pid':os.getpid(),'started':process_started(os.getpid())}}}}
    mode={mode!r}
    if mode=='wrong-pid': reply['holder']['pid']+=1
    if mode=='float-pid': reply['holder']['pid']=float(reply['holder']['pid'])
    if mode=='wrong-nonce': reply['nonce']='0'*64
    if mode=='extra': reply['extra']='must refuse'
    if mode=='partial':
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        channel.sendall((1024).to_bytes(4,'big')+b'{{')
        time.sleep(10)
    else:
        send_frame(channel,reply,deadline=deadline)
        receive_frame(channel,deadline=deadline)
        if mode=='lost-ack': sys.exit(1)
"""
    return [sys.executable, "-B", "-c", script]


@pytest.mark.parametrize(
    "mode", ["wrong-pid", "float-pid", "wrong-nonce", "extra", "partial", "lost-ack"]
)
def test_actual_holder_forgery_partial_or_lost_ack_refuses_and_reaps(setup, monkeypatch, mode):
    _root, _server, _data, _state, binding = setup
    monkeypatch.setattr(holder, "DEADLINE", 0.5)
    original = holder.subprocess.Popen
    children = []

    def spawn(*args, **options):
        assert options["env"] == {"PATH": os.defpath}
        assert options["stderr"] == subprocess.DEVNULL
        child = original(*args, **options)
        children.append(child)
        return child

    monkeypatch.setattr(holder.subprocess, "Popen", spawn)
    try:
        with pytest.raises(holder.bootstrap.BootstrapUnavailable):
            holder._launch(untrusted_holder_command(binding, mode))
        assert children and all(child.poll() is not None for child in children)
        assert not holder._BOOTSTRAPS
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=2)


def test_missing_initial_birth_proof_still_reaps_retained_owned_child(setup, monkeypatch):
    _root, _server, _data, _state, _binding = setup
    original_spawn, original_started = holder.subprocess.Popen, holder.process_started
    children, inspections = [], []

    def spawn(*args, **options):
        child = original_spawn(*args, **options)
        children.append(child)
        return child

    def initially_unknown(pid):
        inspections.append(pid)
        return None if len(inspections) == 1 else original_started(pid)

    monkeypatch.setattr(holder.subprocess, "Popen", spawn)
    monkeypatch.setattr(holder, "process_started", initially_unknown)
    command = [sys.executable, "-c", "import time;time.sleep(10)"]
    try:
        with pytest.raises(holder.bootstrap.BootstrapUnavailable):
            holder._launch(command)
        assert children and children[0].poll() is not None
        assert not holder._BOOTSTRAPS
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=2)


def test_wrong_control_certificate_refuses_before_metadata_application(setup):
    root, server, _data, state, _binding = setup
    client = sdk.ProducerClient()
    server.context = tls.server_context(tls.identity(root / "other-tls", "other-sdk-test"))
    with pytest.raises(sdk.ProducerUnavailable):
        client.receivers()
    assert not state["calls"]


def test_socket_path_replacement_cannot_receive_record(setup):
    _root, _server, data, _state, _binding = setup
    client = sdk.ProducerClient()
    Path(data.path).unlink()
    Path(data.path).write_text("replacement is not a listener")
    with pytest.raises(sdk.ProducerUnavailable):
        client.publish(OFFER, "event-1", {"text": "must remain local"})
    assert Path(data.path).read_text() == "replacement is not a listener"
    assert not data.received


def test_runtime_location_override_refuses_before_selection(monkeypatch, tmp_path):
    monkeypatch.setattr(
        holder.home, "account_roots", lambda: (tmp_path / "fixed", tmp_path / "cache")
    )
    monkeypatch.setattr(holder.runtime, "directory", lambda: tmp_path / "caller-selected")
    monkeypatch.setattr(
        holder.runtime, "selected", lambda: pytest.fail("Read caller runtime selection")
    )
    with pytest.raises(holder.bootstrap.BootstrapUnavailable):
        holder.launch_anchor()


def test_fork_during_holder_construction_closes_private_channels(setup, monkeypatch):
    monkeypatch.setattr(holder, "DEADLINE", 0.4)
    errors = []

    def launch():
        try:
            holder._launch([sys.executable, "-c", "import time;time.sleep(10)"])
        except holder.bootstrap.BootstrapUnavailable as exc:
            errors.append(exc)

    worker = threading.Thread(target=launch)
    worker.start()
    deadline = time.monotonic() + 2
    while not holder._BOOTSTRAPS and time.monotonic() < deadline:
        time.sleep(0.002)
    descriptors = [channel.fileno() for channel in holder._BOOTSTRAPS if channel.fileno() >= 0]
    assert descriptors
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        closed = []
        for descriptor in descriptors:
            try:
                os.fstat(descriptor)
                closed.append(False)
            except OSError:
                closed.append(True)
        os.write(write_fd, json.dumps(closed).encode())
        os._exit(0)
    os.close(write_fd)
    try:
        assert all(json.loads(os.read(read_fd, 128)))
        assert os.waitpid(child, 0)[1] == 0
    finally:
        os.close(read_fd)
        worker.join(3)
    assert not worker.is_alive() and errors and not holder._BOOTSTRAPS


def test_production_launcher_uses_only_fixed_verified_runtime_entry(monkeypatch, tmp_path):
    fixed = tmp_path / "account-state"
    expected = fixed / "runtimes" / holder.runtime.identity()
    chosen = holder.runtime.Runtime(
        expected / ("a" * 40) / ("b" * 32), "a" * 40, "0", holder.runtime.identity()
    )
    monkeypatch.setattr(holder.home, "account_roots", lambda: (fixed, tmp_path / "cache"))
    monkeypatch.setattr(holder.runtime, "directory", lambda: expected)
    monkeypatch.setattr(holder.runtime, "selected", lambda: chosen)
    monkeypatch.setattr(holder.maintenance, "read", contextlib.nullcontext)
    commands = []
    monkeypatch.setattr(
        holder, "_launch", lambda command: commands.append(command) or {"public": True}
    )
    assert holder.launch_anchor() == {"public": True}
    assert commands == [[str(chosen.python), "-I", "-B", "-c", holder.ENTRY]]
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "untrusted"))
    assert holder.installed_root() == fixed / "traind"


def test_explicit_retry_uses_fresh_authenticated_data_and_preserves_command(setup):
    _root, _server, data, state, _binding = setup
    client = sdk.ProducerClient()
    assert client.retry(OFFER, 'event-1', '5' * 32)['event_id'] == 'event-1'
    assert client.retry(OFFER, 'event-1', '5' * 32)['event_id'] == 'event-1'
    assert data.received == [{'action': 'retry', 'event_id': 'event-1', 'retry_key': '5' * 32}] * 2
    assert len([item for item in state['calls'] if item[0] == 'offer-status']) == 2


@pytest.mark.parametrize('key', [None, True, '', '5' * 31, 'Z' * 32, {'record': {}}])
def test_invalid_retry_never_opens_control_or_data(setup, key):
    _root, _server, data, state, _binding = setup
    with pytest.raises(ValueError):
        sdk.ProducerClient().retry(OFFER, 'event-1', key)
    assert not state['calls'] and not data.received


def test_retry_wrong_data_process_gets_zero_application_bytes(setup):
    _root, _server, data, state, _binding = setup
    state['channel']['process']['started'] += 1
    with pytest.raises(sdk.ProducerUnavailable):
        sdk.ProducerClient().retry(OFFER, 'event-1', '5' * 32)
    deadline = time.monotonic() + 2
    while not data.raw and time.monotonic() < deadline:
        time.sleep(0.005)
    assert data.raw == [b''] and not data.received
