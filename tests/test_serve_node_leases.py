"""The serve broker takes the memory, the model slot and the GPU from the node's lease table, and holds nothing of its own.

Every server is a real process on a real port (`canned_binary`), every holder a real process whose pid can end, and the lease
table is a real node's.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time

import pytest

from poolhouse import gate, home, leasetable, limits
from poolhouse.http import Server
from poolhouse.serve import LlamaServerBackend, ServerManager, admission
from poolhouse.serve.broker import Ask, Broker, BrokerError
from poolhouse.serve.process import kill_process_tree, pid_exists
from poolhouse.testing.canned import canned_binary

pytest_plugins = ["serve_node"]
pytestmark = pytest.mark.usefixtures("serve_leases")

GIB = 1024 ** 3


@pytest.fixture
def broker(tmp_path, serve_leases):
    manager = ServerManager(LlamaServerBackend(binary=canned_binary(tmp_path)), state_file=tmp_path / "servers.json")
    made = Broker(manager, idle_s=3600.0, scan=lambda: [])
    yield made
    for held in list(made.servers.values()):
        if held.pid and held.ours:
            kill_process_tree(held.pid)


@pytest.fixture
def models(tmp_path):
    out = []
    for name in ("first", "second", "third"):
        path = tmp_path / f"{name}-{tmp_path.name}.gguf"
        path.write_bytes(b"GGUF" + b"\x00" * 64)
        out.append(str(path))
    return out


@pytest.fixture
def holders():
    """Processes that stand in for clients: each one's pid holds a lease until it ends."""
    started: list[subprocess.Popen] = []

    def one() -> subprocess.Popen:
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        started.append(proc)
        return proc

    yield one
    for proc in started:
        proc.kill()
        proc.wait(timeout=10)


def ask(model: str, pid: int, purpose: str = "chat") -> Ask:
    return Ask(purpose=purpose, models=(model,), pid=pid, label=f"pid {pid}", spec={"context": 512})


def of(table: leasetable.Table, tag: str) -> list[dict]:
    return [row for row in table.listing()["leases"] if row["tag"] == tag]


def gone(table: leasetable.Table, tag: str, seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not of(table, tag):
            return True
        time.sleep(0.1)
    return False


def test_a_server_runs_under_a_lease_of_the_node_that_ends_with_its_process(broker, models, holders, serve_leases):
    grant = broker.lease(ask(models[0], holders().pid), timeout=30)
    held = broker.servers[grant.port]
    (row,) = of(serve_leases, admission.tag_of(grant.port))
    by_type = {resource["type"]: resource for resource in row["resources"]}
    assert row["state"] == "held" and row["pid"] == held.pid
    assert by_type["memory_mb"]["mb"] >= 512, "the runtime allowance is in the estimate"
    assert by_type["model_slot"]["model"] == admission.slot_name(models[0], grant.port)
    assert (by_type["model_slot"]["context"], by_type["model_slot"]["parallel"]) == (512, 1)
    kill_process_tree(held.pid)
    assert gone(serve_leases, admission.tag_of(grant.port)), "the node dropped the lease of a server that died"


def test_a_start_that_fails_gives_its_memory_back_at_once(tmp_path, models, serve_leases):
    from poolhouse.serve.backend import ServerFailed, ServerSpec
    from poolhouse.serve.ports import free_port
    from poolhouse.testing.fakes import FakeBackend

    class Failing(FakeBackend):
        def start(self, spec, *, lease, **_starting):
            assert of(serve_leases, admission.tag_of(spec.port)), "the lease is held while the server loads"
            raise ServerFailed("it would not load")

    manager = ServerManager(Failing(), state_file=tmp_path / "servers.json")
    port = free_port()
    with pytest.raises(ServerFailed, match="would not load"):
        manager.lease(ServerSpec(model=models[0], port=port, context=512), preflight=False)
    assert of(serve_leases, admission.tag_of(port)) == []


def test_a_server_that_is_released_gives_its_lease_back(broker, models, holders, serve_leases):
    grant = broker.lease(ask(models[0], holders().pid), timeout=30)
    assert of(serve_leases, admission.tag_of(grant.port))
    broker.release(grant.lease)
    broker.stop(grant.port)
    assert gone(serve_leases, admission.tag_of(grant.port), seconds=3.0)


def test_a_server_that_does_not_fit_beside_the_ones_up_waits_for_the_node_and_is_refused(
        broker, models, holders, monkeypatch, serve_leases):
    limits.changed(memory_bytes=int(1.2 * GIB))
    monkeypatch.setenv(admission.ENV_WAIT, "1")
    first = broker.lease(ask(models[0], holders().pid, "one"), timeout=30)
    second = broker.lease(ask(models[1], holders().pid, "two"), timeout=30)
    began = time.monotonic()
    with pytest.raises(BrokerError) as why:
        broker.lease(ask(models[2], holders().pid, "three"), timeout=30)
    said = str(why.value)
    assert 0.9 <= time.monotonic() - began < 8.0, "it waited for memory before it gave up"
    assert "this server needs" in said and admission.slot_name(models[0], first.port) in said
    assert admission.slot_name(models[1], second.port) in said
    assert sorted(held.port for held in broker.servers.values() if not held.loading) == sorted([first.port, second.port])
    assert not [row for row in serve_leases.listing()["leases"] if row["state"] == "queued"], "the refused start left no request behind"


def test_a_start_that_is_waiting_for_memory_goes_ahead_when_a_server_dies(broker, models, holders, monkeypatch):
    limits.changed(memory_bytes=int(1.2 * GIB))
    monkeypatch.setenv(admission.ENV_WAIT, "30")
    first = broker.lease(ask(models[0], holders().pid, "one"), timeout=30)
    broker.lease(ask(models[1], holders().pid, "two"), timeout=30)
    first_pid = broker.servers[first.port].pid
    threading.Timer(1.0, lambda: kill_process_tree(first_pid)).start()
    began = time.monotonic()
    third = broker.lease(ask(models[2], holders().pid, "three"), timeout=60)
    assert time.monotonic() - began >= 0.9
    assert pid_exists(broker.servers[third.port].pid)


def test_a_lease_held_by_a_process_that_has_ended_takes_no_memory(broker, models, holders, monkeypatch, serve_leases):
    limits.changed(memory_bytes=int(1.2 * GIB))
    monkeypatch.setenv(admission.ENV_WAIT, "1")
    serve_leases.budget(admission.budget_mb())
    gone_soon = holders()
    serve_leases.acquire([leasetable.memory(1100)], leasetable.Terms(tag="server.1", pid=gone_soon.pid))
    with pytest.raises(BrokerError, match="this server needs"):
        broker.lease(ask(models[0], holders().pid), timeout=30)
    gone_soon.kill()
    gone_soon.wait(timeout=10)
    grant = broker.lease(ask(models[0], holders().pid, "again"), timeout=30)
    assert pid_exists(broker.servers[grant.port].pid)


def test_a_broker_that_starts_again_keeps_the_lease_of_a_server_it_finds_and_takes_one_that_is_gone(
        tmp_path, broker, models, holders, serve_leases):
    grant = broker.lease(ask(models[0], holders().pid), timeout=30)
    (row,) = of(serve_leases, admission.tag_of(grant.port))
    again = Broker(ServerManager(broker.manager.backend, state_file=tmp_path / "servers.json"), idle_s=3600.0, scan=lambda: [])
    again.adopt()
    assert [lease["id"] for lease in of(serve_leases, admission.tag_of(grant.port))] == [row["id"]]
    server_pid = broker.servers[grant.port].pid
    for retake in (again.adopt, again.keep_leases):
        serve_leases.release(of(serve_leases, admission.tag_of(grant.port))[0]["id"])
        assert of(serve_leases, admission.tag_of(grant.port)) == []
        retake()
        (retaken,) = of(serve_leases, admission.tag_of(grant.port))
        assert retaken["pid"] == server_pid, "taken again for the server's own process"


def test_keeping_the_leases_renews_the_servers_and_the_claims(broker, models, holders, serve_leases):
    grant = broker.lease(ask(models[0], holders().pid), timeout=30)
    trainer = holders()
    assert broker.claim("gpu-training", trainer.pid, {"purpose": "x"})["granted"] is False, "a held server keeps the GPU from a training run"
    broker.release(grant.lease)
    broker.stop(grant.port)
    assert broker.claim("gpu-training", trainer.pid, {"purpose": "x"})["granted"] is True
    before = {row["tag"]: row["expires_ms"] for row in serve_leases.listing()["leases"]}
    time.sleep(1.1)
    broker.keep_leases()
    after = {row["tag"]: row["expires_ms"] for row in serve_leases.listing()["leases"]}
    assert after["claim.gpu-training"] > before["claim.gpu-training"]


class Recorder:
    """A server on a loopback port that answers a generation request at once."""

    def __init__(self) -> None:
        from http.server import BaseHTTPRequestHandler

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: object) -> None:
                return

            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = b"{}"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = Server(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_port}/v1/chat/completions"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def served():
    """A server the lease registry of the state root lists on the GPU, so a request to it takes its turn."""
    one = Recorder()
    home.state("servers.json").write_text(json.dumps({str(one.httpd.server_port): {
        "port": one.httpd.server_port, "pid": 1, "device": "gpu", "owner_pid": 1}}))
    yield one
    one.close()


def test_a_training_claim_is_the_gpu_of_the_table_and_requests_wait_behind_it(broker, holders, serve_leases, served, monkeypatch):
    trainer = holders()
    got = broker.claim("gpu-training", trainer.pid, {"purpose": "lora"})
    assert got["granted"] is True
    (row,) = of(serve_leases, "claim.gpu-training")
    assert row["pid"] == trainer.pid and row["resources"] == [{"type": "gpu", "device": "local"}]
    assert broker.claim("gpu-training", holders().pid, {"purpose": "other"})["granted"] is False
    monkeypatch.setenv(gate.ENV_WAIT, "0.5")
    with pytest.raises(gate.QueueTimeout), gate.turn(served.url):
        pytest.fail("a request took its turn while a training run held the GPU")
    assert broker.unclaim("gpu-training", trainer.pid)
    with gate.turn(served.url):
        assert [turn["pid"] for turn in gate.snapshot()["gpu"]] != []


def test_a_training_run_that_ends_frees_the_gpu(broker, holders, serve_leases):
    trainer = holders()
    assert broker.claim("gpu-training", trainer.pid, {"purpose": "lora"})["granted"] is True
    trainer.kill()
    trainer.wait(timeout=10)
    assert gone(serve_leases, "claim.gpu-training")
    assert broker.claim("gpu-training", holders().pid, {"purpose": "again"})["granted"] is True


def test_a_request_in_flight_keeps_a_training_run_from_the_gpu(broker, holders, served):
    inside, release = threading.Event(), threading.Event()

    def request() -> None:
        with gate.turn(served.url):
            inside.set()
            release.wait(timeout=30)

    worker = threading.Thread(target=request)
    worker.start()
    assert inside.wait(timeout=10)
    try:
        got = broker.claim("gpu-training", holders().pid, {"purpose": "lora"})
        assert got["granted"] is False and "GPU" in got["info"]["purpose"]
    finally:
        release.set()
        worker.join(timeout=10)


def test_an_unmanaged_server_takes_memory_from_the_budget_until_its_process_ends(tmp_path, broker, holders, serve_leases):
    unmanaged = holders()
    proc = {"port": 59999, "pid": unmanaged.pid, "rss": 700 * 1024 ** 2, "model": "m.gguf", "defunct": False}
    broker.scan = lambda: [proc]
    broker.adopt()
    (row,) = [lease for lease in serve_leases.listing()["leases"] if lease["tag"].startswith("unmanaged.")]
    assert row["pid"] == unmanaged.pid and row["resources"] == [{"type": "memory_mb", "device": "local", "mb": 768}]
    unmanaged.kill()
    unmanaged.wait(timeout=10)
    assert not [lease for lease in serve_leases.listing()["leases"] if lease["tag"].startswith("unmanaged.")]


def test_each_test_starts_on_a_state_root_that_holds_only_the_node(serve_leases):
    assert [path.name for path in home.home().iterdir()] == ["node"]
