"""Launch proof is actual child ownership, never writable broker discovery state."""
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse.serve import decision_launch as launch


def selected():
    return {"endpoint": "https://127.0.0.1:7443", "certificate": "installed-certificate",
            "machine": "device-test", "pool": "a" * 64, "runtime": "b" * 64,
            "issuer_instance": "c" * 64}


def body(instance):
    return {"permit_id": "permit-test", "request_id": "record-1", "record_digest": "d" * 64,
            "model_digest": "e" * 64, "runtime_digest": "b" * 64,
            "worker": {"pid": 81, "started": 11.0},
            "broker": {"pid": 82, "started": 12.0, "instance": instance},
            "stage": "compute", "issuer_instance": "c" * 64}


class Stream(io.BytesIO):
    def fileno(self):
        return 42


class Process:
    pid = 82

    def __init__(self):
        self.stdin, self.stdout = Stream(), Stream()
        self.dead = False
        self.terminated = 0

    def poll(self):
        return 0 if self.dead else None

    def terminate(self):
        self.terminated += 1
        self.dead = True

    def wait(self, timeout):
        assert self.dead
        return 0


@pytest.fixture
def owned(monkeypatch):
    monkeypatch.setattr(launch, "pid_exists", lambda pid: pid == 82)
    monkeypatch.setattr(launch, "started_at", lambda pid: {81: 11.0, 82: 12.0}.get(pid))
    monkeypatch.setattr(launch, "owns_connection", lambda pid, remote, local:
                        (pid, remote, local) == (82, ("127.0.0.1", 1234), ("127.0.0.1", 7443)))
    owner = launch.OwnedBroker(selected())
    owner.process, owner.started = Process(), 12.0
    owner.ready = {"pid": 82, "started": 12.0, "instance": owner.instance, "port": 7222}
    return owner


def signed(owner, value=None):
    value = value or body(owner.instance)
    proof = hmac.new(owner.secret, launch.DOMAIN + launch._json(value), hashlib.sha256).hexdigest()
    return {**value, "proof": proof}


def test_actual_owned_generation_and_socket_required(owned, monkeypatch):
    value = signed(owned)
    assert owned.verify(value, ("127.0.0.1", 1234), ("127.0.0.1", 7443)) == body(owned.instance)
    # Repeat verifies the same immutable request; it creates no new launch.
    assert owned.verify(value, ("127.0.0.1", 1234), ("127.0.0.1", 7443)) == body(owned.instance)
    with pytest.raises(PermissionError):
        owned.verify(value, ("127.0.0.1", 9999), ("127.0.0.1", 7443))
    monkeypatch.setattr(launch, "started_at", lambda _pid: 99.0)
    with pytest.raises(PermissionError):
        owned.verify(value, ("127.0.0.1", 1234), ("127.0.0.1", 7443))


@pytest.mark.parametrize("key,new", [("model_digest", "f" * 64), ("record_digest", "f" * 64),
                                      ("stage", "result"), ("request_id", "record-2"),
                                      ("runtime_digest", "f" * 64), ("issuer_instance", "f" * 64)])
def test_signature_binds_every_request_field(owned, key, new):
    value = signed(owned)
    value[key] = new
    with pytest.raises(PermissionError):
        owned.verify(value, ("127.0.0.1", 1234), ("127.0.0.1", 7443))


def test_new_issuer_secret_and_process_death_refuse_replay(owned):
    value = signed(owned)
    replacement = launch.OwnedBroker(selected())
    replacement.process, replacement.started, replacement.ready = owned.process, 12.0, owned.ready
    with pytest.raises(PermissionError):
        replacement.verify(value, ("127.0.0.1", 1234), ("127.0.0.1", 7443))
    owned.process.dead = True
    with pytest.raises(PermissionError):
        owned.verify(value, ("127.0.0.1", 1234), ("127.0.0.1", 7443))


@pytest.mark.parametrize("endpoint", ["http://127.0.0.1:7443", "https://example.invalid:7443",
                                     "https://127.0.0.1:7443/path", "https://x@127.0.0.1:7443",
                                     "https://127.0.0.1:7443?url=other", "https://localhost:7443"])
def test_bootstrap_never_selects_an_arbitrary_endpoint(endpoint):
    with pytest.raises(ValueError):
        launch.OwnedBroker({**selected(), "endpoint": endpoint})


@pytest.mark.parametrize("worker", [{"pid": 0, "started": None}, {"pid": True, "started": 1},
                                   {"pid": 81, "started": float("nan")}])
def test_invalid_process_proof_refused(worker):
    with pytest.raises(ValueError):
        launch.checked_body({**body("a" * 64), "worker": worker})


def test_bootstrap_checks_actual_parent_and_bounded_private_frame(monkeypatch):
    monkeypatch.setattr(launch.os, "getppid", lambda: 81)
    monkeypatch.setattr(launch, "started_at", lambda pid: 11.0 if pid == 81 else None)
    value = {"parent": {"pid": 81, "started": 11.0}, "instance": "f" * 64,
             "secret": "e" * 64, "issuer": selected()}
    raw = launch._json(value) + b"\n"
    assert launch.bootstrap(raw).secret == bytes.fromhex("e" * 64)
    with pytest.raises(PermissionError):
        launch.bootstrap(launch._json({**value, "parent": {"pid": 82, "started": 11.0}}) + b"\n")
    with pytest.raises(ValueError):
        launch.bootstrap(raw[:-1])
    with pytest.raises(ValueError):
        launch.bootstrap(b" " * (launch.LIMIT + 1) + b"\n")


def test_start_uses_fixed_managed_entrypoint_and_attaches(tmp_path, monkeypatch):
    import subprocess
    import venv

    virtual = tmp_path / "compute"
    venv.EnvBuilder().create(virtual)
    python = virtual / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = subprocess.check_output([str(python), "-I", "-c",
                                    "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip()
    package = Path(site) / "poolhouse"
    for name in ("__init__.py", "serve/__init__.py", "graph/__init__.py",
                 "graph/loaded.py", "serve/cli.py"):
        target = package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("import poolhouse.graph.loaded\n" if name == "serve/cli.py" else "")
    owner = launch.OwnedBroker(selected())
    process = Process()
    checks, writes, starts = [], [], []
    real_read = os.read
    environment = SimpleNamespace(python=python, require_current_runtime=lambda: checks.append("runtime"),
                                  require_compute=lambda: checks.append("compute"))
    monkeypatch.setattr(launch, "Environment", lambda root: environment)
    def start(argv, **kwargs):
        starts.append((argv, kwargs))
        # Execute the exact fixed launch argv against an isolated CPU-only installed fixture.
        with monkeypatch.context() as child:
            child.setattr(os, "read", real_read)
            subprocess.run(argv, env=kwargs["env"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert not list(package.rglob("*.pyc"))
        return process
    monkeypatch.setattr(launch, "start_process", start)
    monkeypatch.setattr(launch, "started_at", lambda pid: 12.0)
    monkeypatch.setattr(launch, "pid_exists", lambda pid: True)
    monkeypatch.setattr(launch.select, "select", lambda *args: ([process.stdout], [], []))
    monkeypatch.setattr(launch.os, "read", lambda *_args: launch._json({"pid": 82, "started": 12.0,
                       "instance": owner.instance, "port": 7222}) + b"\n")
    monkeypatch.setattr(owner, "_write", lambda raw, until: writes.append(json.loads(raw)))
    assert owner.start() == owner.start()
    assert checks == ["runtime", "compute"]
    assert len(starts) == 1
    assert starts[0][0] == [str(python), "-B", "-I", "-m", "poolhouse.serve.cli", "broker-owned"]
    assert owner.secret.hex() not in repr(starts)
    assert writes[0]["secret"] == owner.secret.hex()
    assert writes[1] == {"accept": owner.instance}
    with pytest.raises(RuntimeError, match="idle retirement"):
        owner._abort_start()
    assert process.terminated == 0
    process.dead = True
    with pytest.raises(InterruptedError):
        owner.start()


def test_failed_readiness_stops_retained_child_before_admission(monkeypatch):
    owner = launch.OwnedBroker(selected())
    process = Process()
    monkeypatch.setattr(launch, "Environment", lambda root: SimpleNamespace(
        python="/managed/python", require_current_runtime=lambda: None, require_compute=lambda: None))
    monkeypatch.setattr(launch, "start_process", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(launch, "started_at", lambda pid: 12.0)
    monkeypatch.setattr(launch, "pid_exists", lambda pid: True)
    monkeypatch.setattr(owner, "_write", lambda *_args: None)
    monkeypatch.setattr(launch.select, "select", lambda *args: ([process.stdout], [], []))
    monkeypatch.setattr(launch.os, "read", lambda *_args: b'{"pid": 999}\n')
    with pytest.raises(PermissionError):
        owner.start()
    assert process.terminated == 1 and owner.ready is None


def test_lost_parent_ack_keeps_possible_admitted_child_tracked(monkeypatch):
    owner = launch.OwnedBroker(selected())
    process = Process()
    monkeypatch.setattr(launch, "Environment", lambda root: SimpleNamespace(
        python="/managed/python", require_current_runtime=lambda: None, require_compute=lambda: None))
    monkeypatch.setattr(launch, "start_process", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(launch, "started_at", lambda pid: 12.0)
    monkeypatch.setattr(launch, "pid_exists", lambda pid: True)
    monkeypatch.setattr(launch.select, "select", lambda *args: ([process.stdout], [], []))
    monkeypatch.setattr(launch.os, "read", lambda *_args: launch._json({"pid": 82, "started": 12.0,
                       "instance": owner.instance, "port": 7222}) + b"\n")
    def write(raw, until):
        if "accept" in json.loads(raw):
            raise BrokenPipeError("lost acknowledgement")
    monkeypatch.setattr(owner, "_write", write)
    with pytest.raises(BrokenPipeError):
        owner.start()
    assert owner.process is process and owner.admission_possible and not process.dead
    with pytest.raises(InterruptedError):
        owner.start()
    with pytest.raises(RuntimeError):
        owner._abort_start()


def test_partial_ack_with_open_writer_is_bounded_and_never_admits():
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "rb") as reader, os.fdopen(write_fd, "wb", buffering=0) as writer:
        writer.write(b'{"accept":')
        with pytest.raises(InterruptedError, match="complete"):
            launch._frame(reader, lambda: True, timeout=0.02)


def test_private_frame_checks_parent_generation_and_refuses_extra_data():
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "rb") as reader, os.fdopen(write_fd, "wb", buffering=0) as writer:
        writer.write(b'{"accept": "x"}\n')
        with pytest.raises(InterruptedError):
            launch._frame(reader, lambda: False)
        assert launch._frame(reader, lambda: True) == b'{"accept": "x"}\n'
        writer.write(b'{}\n{}\n')
        with pytest.raises(ValueError, match="extra frame"):
            launch._frame(reader, lambda: True)


@pytest.fixture
def current_broker(monkeypatch):
    state = {"live": True, "reply": {"ok": True, "retiring": True}, "calls": []}
    record = {"pid": 83, "pid_started": 13.0, "port": 7443, "token": "local-token"}
    monkeypatch.setattr(launch.broker_wire, "_record", lambda: dict(record))
    monkeypatch.setattr(launch.broker_wire, "_answering", lambda: dict(record))
    monkeypatch.setattr(launch, "pid_exists", lambda _pid: state["live"])
    monkeypatch.setattr(launch, "started_at", lambda _pid: 13.0 if state["live"] else None)
    def send(found, request, *, timeout, expected):
        assert expected == {"pid": 83, "started": 13.0}
        state["calls"].append((found, request, timeout))
        if state["reply"] == {"ok": True, "retiring": True}:
            state["live"] = False
        return state["reply"]
    monkeypatch.setattr(launch.broker_wire, "_send", send)
    return state, record


def test_existing_idle_broker_retires_itself_before_owned_launch(current_broker):
    state, record = current_broker
    launch._idle_boundary()
    assert not state["live"]
    assert state["calls"] == [(record, {"op": "idle-retire", "pid": launch.os.getpid()}, 5)]


@pytest.mark.parametrize("reply", [{"ok": True, "retiring": False}, {"ok": False}, {"retiring": True}])
def test_busy_or_uncertain_broker_is_never_replaced(current_broker, reply):
    state, _record = current_broker
    state["reply"] = reply
    with pytest.raises((BlockingIOError, InterruptedError)):
        launch._idle_boundary()
    assert state["live"]


def test_live_unanswering_broker_or_unknown_generation_is_preserved(current_broker, monkeypatch):
    state, record = current_broker
    monkeypatch.setattr(launch.broker_wire, "_answering", lambda: None)
    with pytest.raises(InterruptedError, match="not answering"):
        launch._idle_boundary()
    assert not state["calls"] and state["live"]
    record.pop("pid_started")
    with pytest.raises(InterruptedError, match="generation"):
        launch._idle_boundary()
    assert not state["calls"]


def test_managed_runtime_verified_before_touching_any_existing_broker(monkeypatch):
    calls = []
    def missing():
        calls.append("runtime")
        raise OSError("managed runtime incomplete")
    monkeypatch.setattr(launch, "Environment", lambda _root: SimpleNamespace(
        require_current_runtime=missing, require_compute=lambda: calls.append("compute")))
    monkeypatch.setattr(launch, "_idle_boundary", lambda: calls.append("retire"))
    owner = launch.OwnedBroker(selected())
    with pytest.raises(OSError, match="incomplete"):
        owner.start()
    assert calls == ["runtime"] and owner.process is None
