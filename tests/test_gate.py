"""Requests to servers poolhouse started are served one at a time per device, across processes.

The servers here are real HTTP servers in this process that record the window in which each
generation was running; the callers are separate Python processes sending through
`poolhouse.http`, the path every consumer uses, and the line is the lease table of a real node.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import NamedTuple

import pytest

from poolhouse import gate, home, leasetable
from poolhouse.http import Options, Server, ServerError, request_json
from poolhouse.serve.process import kill_process_tree

pytest_plugins = ["node_kit"]

SRC = str(Path(__file__).resolve().parent.parent / "src")
HOLD_S = 0.3


class Recorder:
    """A server that records when each generation began and ended."""

    def __init__(self, hold_s: float = HOLD_S) -> None:
        self.windows: list[tuple[float, float, str]] = []
        self.hold_s = hold_s
        self.lock = threading.Lock()
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: object) -> None:
                return

            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                began = time.time()
                time.sleep(recorder.hold_s)
                ended = time.time()
                with recorder.lock:
                    recorder.windows.append((began, ended, self.headers.get("X-Who", "")))
                body = b'{"choices": [{"message": {"content": "ok"}}]}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                body = b"{}"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = Server(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1/chat/completions"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def register(*servers: Recorder, device: str = "gpu") -> None:
    path = home.state("servers.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({str(s.port): {"port": s.port, "pid": os.getpid(), "device": device,
                                              "owner_pid": os.getpid()} for s in servers}))


@pytest.fixture
def servers(workspace_node):
    made: list[Recorder] = []

    def make(n: int = 2, hold_s: float = HOLD_S) -> list[Recorder]:
        made.extend(Recorder(hold_s) for _ in range(n))
        register(*made)
        return made

    yield make
    for one in made:
        one.close()


def worker(url: str, who: str, *, env: dict[str, str] | None = None) -> subprocess.Popen:
    code = ("import sys\nfrom poolhouse.http import Options, request_json\n"
            "request_json(sys.argv[1], payload={'messages': []}, options=Options(headers={'X-Who': sys.argv[2]}))\n")
    return subprocess.Popen([sys.executable, "-c", code, url, who],
                            env={**os.environ, "PYTHONPATH": SRC, **(env or {})},
                            stderr=subprocess.PIPE)


def together(url: str, who: str, at: float) -> subprocess.Popen:
    """A worker that sends its request at the clock time ``at``."""
    code = ("import sys, time\nfrom poolhouse.http import Options, request_json\n"
            "while time.time() < float(sys.argv[3]):\n    pass\n"
            "request_json(sys.argv[1], payload={}, options=Options(headers={'X-Who': sys.argv[2]}))\n")
    return subprocess.Popen([sys.executable, "-c", code, url, who, str(at)],
                            env={**os.environ, "PYTHONPATH": SRC}, stderr=subprocess.PIPE)


def overlap(windows: list[tuple[float, float, str]]) -> float:
    """The most time any two generation windows spent running at once."""
    worst = 0.0
    ordered = sorted(windows)
    for i, (_, end, _) in enumerate(ordered):
        for began, _, _ in ordered[i + 1:]:
            worst = max(worst, end - began)
    return worst


def run_all(procs: list[subprocess.Popen]) -> None:
    for proc in procs:
        _, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, err.decode()


def test_requests_from_separate_processes_to_two_servers_never_overlap(servers):
    first, second = servers(2)
    urls = [first.url, second.url, first.url, second.url, first.url, second.url]
    began = time.time()
    run_all([worker(url, str(i)) for i, url in enumerate(urls)])
    windows = first.windows + second.windows
    assert len(windows) == 6
    assert overlap(windows) <= 0.0
    assert time.time() - began >= 6 * HOLD_S


def test_requests_sent_at_the_same_instant_never_overlap(servers):
    first, second = servers(2, hold_s=0.05)
    at = time.time() + 3.0
    run_all([together([first.url, second.url][i % 2], str(i), at) for i in range(12)])
    windows = first.windows + second.windows
    assert len(windows) == 12 and overlap(windows) <= 0.0


def test_parallel_requests_are_an_explicit_opt_out_and_do_overlap(servers):
    first, second = servers(2)
    env = {gate.ENV_PARALLEL: "1"}
    run_all([worker(url, str(i), env=env) for i, url in enumerate([first.url, second.url] * 2)])
    assert overlap(first.windows + second.windows) > HOLD_S / 2


def test_requests_are_served_in_the_order_they_arrived(servers):
    first, second = servers(2, hold_s=1.0)
    base = time.time() + 3.0
    run_all([together([first.url, second.url][i % 2], str(i), base + 0.5 * i) for i in range(4)])
    order = [who for _, _, who in sorted(first.windows + second.windows)]
    assert order == ["0", "1", "2", "3"]


HOLDER = """
import os, sys, time
from poolhouse import gate
with gate.turn(sys.argv[1]):
    print(f'held {os.getpid()}', flush=True)
    time.sleep(60)
"""


class Held(NamedTuple):
    process: subprocess.Popen
    pid: int


def holder(url: str) -> Held:
    proc = subprocess.Popen([sys.executable, "-c", HOLDER, url],
                            env={**os.environ, "PYTHONPATH": SRC},
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    notice = proc.stdout.readline().strip().split()
    assert len(notice) == 2 and notice[0] == b"held", proc.stderr.read() if proc.stderr else b""
    return Held(proc, int(notice[1]))


def test_a_holder_that_is_killed_frees_the_line(servers):
    (one,) = servers(1, hold_s=0.01)
    dead = holder(one.url)
    queued = worker(one.url, "after")
    time.sleep(0.5)
    assert queued.poll() is None, "the request waits while the holder is alive"
    kill_process_tree(dead.process.pid, grace_s=0.1)
    dead.process.wait(timeout=10)
    began = time.time()
    run_all([queued])
    assert time.time() - began < 3.0
    assert [who for _, _, who in one.windows] == ["after"]


def test_a_request_that_waits_too_long_says_who_it_waited_for(servers, monkeypatch):
    (one,) = servers(1)
    held = holder(one.url)
    try:
        monkeypatch.setenv(gate.ENV_WAIT, "0.4")
        began = time.time()
        with pytest.raises(ServerError) as why:
            request_json(one.url, payload={})
        assert 0.4 <= time.time() - began < 3.0
        said = str(why.value)
        assert why.value.status == 429
        assert f"pid {held.pid}" in said and "gpu" in said
        assert gate.ENV_WAIT in said and gate.ENV_PARALLEL in said
        assert one.windows == []
        assert [r["pid"] for r in gate.snapshot()["gpu"]] == [held.pid]
    finally:
        kill_process_tree(held.process.pid, grace_s=0.1)
        held.process.wait(timeout=10)
    assert gate.snapshot() == {}, "a timed-out request leaves no ticket behind"


def test_only_generation_on_registered_local_servers_is_queued(servers):
    (one,) = servers(1)
    held = holder(one.url)
    try:
        assert request_json(f"http://127.0.0.1:{one.port}/props", options=Options(timeout=5)) == {}
        free = Recorder(0.0)
        try:
            request_json(free.url, payload={}, options=Options(timeout=5))
            assert len(free.windows) == 1, "a server nobody registered is not queued"
        finally:
            free.close()
    finally:
        kill_process_tree(held.process.pid, grace_s=0.1)
        held.process.wait(timeout=10)


def test_devices_queue_apart(servers):
    first, second = servers(2)
    entries = json.loads(home.state("servers.json").read_text())
    entries[str(second.port)]["device"] = "cpu"
    home.state("servers.json").write_text(json.dumps(entries))
    held = holder(first.url)
    try:
        request_json(second.url, payload={}, options=Options(timeout=5))
        assert len(second.windows) == 1
    finally:
        kill_process_tree(held.process.pid, grace_s=0.1)
        held.process.wait(timeout=10)


def test_a_thread_holding_the_device_can_send_a_nested_request(servers):
    (one,) = servers(1, hold_s=0.01)
    with gate.turn(one.url):
        request_json(one.url, payload={}, options=Options(timeout=5))
    assert len(one.windows) == 1


def test_the_turn_is_held_until_a_streamed_answer_is_read(servers):
    (one,) = servers(1, hold_s=0.01)
    from poolhouse.http import request_stream

    stream = request_stream(one.url, payload={}, timeout=5)
    with contextlib.suppress(ServerError):
        next(stream, None)
    stream.close()
    assert gate.snapshot() == {}


def test_a_streamed_request_waits_for_its_turn_like_any_other(servers, monkeypatch):
    from poolhouse.http import request_stream

    (one,) = servers(1, hold_s=0.01)
    held = holder(one.url)
    try:
        monkeypatch.setenv(gate.ENV_WAIT, "0.3")
        with pytest.raises(ServerError) as why:
            next(request_stream(one.url, payload={}, timeout=5))
        assert why.value.status == 429 and one.windows == []
    finally:
        kill_process_tree(held.process.pid, grace_s=0.1)
        held.process.wait(timeout=10)


def test_parallel_block_is_named_and_logged(servers, caplog):
    (one,) = servers(1, hold_s=0.01)
    held = holder(one.url)
    try:
        with caplog.at_level("WARNING", logger="poolhouse.gate"), gate.parallel("bench sweep"):
            request_json(one.url, payload={}, options=Options(timeout=5))
        assert len(one.windows) == 1
        assert "bench sweep" in caplog.text
    finally:
        kill_process_tree(held.process.pid, grace_s=0.1)
        held.process.wait(timeout=10)


def test_the_benchmarks_that_measure_streams_in_flight_together_send_them_in_parallel(
        servers, monkeypatch):
    from poolhouse.bench import speed
    from poolhouse.client import Client

    (one,) = servers(1, hold_s=0.01)
    held = holder(one.url)
    try:
        monkeypatch.setenv(gate.ENV_WAIT, "0.3")
        client = Client(f"http://127.0.0.1:{one.port}")
        assert speed._abreast(client, "hello", 4)["error"] == ""
        assert "for its turn" in speed._one(client, "hello", generate=4)["error"], (
            "the same request outside the benchmark waits in line")
    finally:
        kill_process_tree(held.process.pid, grace_s=0.1)
        held.process.wait(timeout=10)


def test_conversations_measured_in_flight_together_are_sent_in_parallel(servers, monkeypatch):
    from poolhouse.bench.measure import concurrent
    from poolhouse.client import Client

    (one,) = servers(1, hold_s=0.01)
    held = holder(one.url)
    client = Client(f"http://127.0.0.1:{one.port}")

    def ask(question, counting, **_):
        counting.chat([{"role": "user", "content": question}])
        return {"content": "ok"}

    try:
        monkeypatch.setenv(gate.ENV_WAIT, "0.3")
        rows, _ = concurrent(ask, [{"q": "a?"}, {"q": "b?"}], conversations=2, turns=1,
                             label="x", client=client)
        assert [row.error for row in rows] == ["", ""]
    finally:
        kill_process_tree(held.process.pid, grace_s=0.1)
        held.process.wait(timeout=10)


def test_a_turn_is_a_quiet_lease_of_the_nodes_table_that_belongs_to_its_process(servers):
    (one,) = servers(1, hold_s=0.01)
    held = holder(one.url)
    try:
        table = leasetable.default()
        turns = [row for row in table.listing()["leases"] if row["tag"].startswith("turn.")]
        assert [(row["state"], row["quiet"], row["pid"]) for row in turns] == [("held", True, held.pid)]
        assert turns[0]["resources"] == [{"type": "gpu", "device": "local"}]
        assert table.call("read", kind="lease")["entries"] == [], "a turn leaves nothing on the board"
    finally:
        kill_process_tree(held.process.pid, grace_s=0.1)
        held.process.wait(timeout=10)


def test_a_request_that_is_cancelled_while_it_waits_leaves_the_line(servers):
    (one,) = servers(1, hold_s=0.01)
    held = holder(one.url)
    try:
        stop = threading.Event()
        threading.Timer(0.5, stop.set).start()
        with pytest.raises(gate.QueueTimeout, match="cancelled"), gate.turn(one.url, cancelled=stop.is_set):
            pytest.fail("the turn was never granted")
        assert [row["pid"] for row in gate.snapshot()["gpu"]] == [held.pid], "the cancelled request is not left queued"
    finally:
        kill_process_tree(held.process.pid, grace_s=0.1)
        held.process.wait(timeout=10)


def test_a_device_the_table_cannot_be_asked_about_is_not_sent_to(servers, monkeypatch):
    (one,) = servers(1, hold_s=0.01)

    class Down(leasetable.Table):
        def acquire(self, *_args, **_kwargs):
            raise OSError("no socket")

    monkeypatch.setattr(leasetable, "default", lambda: Down())
    with pytest.raises(ServerError) as why:
        request_json(one.url, payload={}, options=Options(timeout=5))
    assert why.value.status == 429 and "gpu" in str(why.value) and one.windows == []


def test_a_turn_that_runs_longer_than_its_lease_lasts_keeps_it_by_renewing(servers, monkeypatch):
    (one,) = servers(1, hold_s=0.01)
    monkeypatch.setattr(gate, "LEASE_S", 2)
    monkeypatch.setattr(gate, "RENEW_S", 0.4)
    got: list[BaseException | None] = []

    def second() -> None:
        try:
            with gate.turn(one.url, wait_s=3.5):
                got.append(None)
        except gate.QueueTimeout as why:
            got.append(why)

    with gate.turn(one.url):
        waiter = threading.Thread(target=second)
        waiter.start()
        time.sleep(4.5)
    waiter.join(timeout=10)
    assert len(got) == 1 and isinstance(got[0], gate.QueueTimeout), "the lease did not run out under a turn that was still running"


def test_a_turn_that_cannot_be_given_back_ends_by_itself_and_the_line_goes_on(servers, monkeypatch, caplog):
    (one,) = servers(1, hold_s=0.01)
    monkeypatch.setattr(gate, "LEASE_S", 2)
    monkeypatch.setattr(gate, "RENEW_S", 60.0)
    tried: list[str] = []

    def refuses(self, lease):
        tried.append(lease)
        raise OSError("the node did not answer")

    with monkeypatch.context() as broken, caplog.at_level("WARNING", logger="poolhouse.gate"):
        broken.setattr(leasetable.Table, "release", refuses)
        with gate.turn(one.url):
            pass
    assert len(tried) == 3 and "was not given back" in caplog.text
    began = time.monotonic()
    with gate.turn(one.url, wait_s=10):
        waited = time.monotonic() - began
    assert 0.3 < waited < 6, "the next request had its turn when the lease ran out"


def test_a_turn_that_is_over_leaves_no_thread_renewing_its_lease(servers):
    (one,) = servers(1, hold_s=0.01)
    with gate.turn(one.url):
        pass
    time.sleep(0.3)
    before = threading.active_count()
    with gate.turn(one.url):
        assert threading.active_count() == before + 1, "the renewer of the turn's lease"
    deadline = time.monotonic() + 3
    while threading.active_count() > before and time.monotonic() < deadline:
        time.sleep(0.05)
    assert threading.active_count() == before, "the renewer stopped when the turn ended"
