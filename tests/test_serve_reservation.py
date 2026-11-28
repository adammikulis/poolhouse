"""A scheduled job's reservation of the device in the real broker, over the real lease table of a test's own node: granted only
while nothing is in use, kept from every other requester while held, passed to what runs under its holder, and ended with it.

PLUMBING ONLY: where a server must be resident it is the canned protocol server (`poolhouse.testing.canned`), which marks every
reply `[CANNED RESPONSE, NOT A MODEL]`; no model runs.
"""

from __future__ import annotations

import os
import threading
import time

import pytest
from reservation_kit import Wire

from poolhouse.schedule.device import Device
from poolhouse.serve import broker_holds, gpu
from poolhouse.serve.broker import Ask, BrokerError

pytest_plugins = ["serve_node", "reservation_kit"]
pytestmark = [pytest.mark.usefixtures("serve_leases"), pytest.mark.slow]
JOB = broker_holds.JOB_CLAIM
PARENT = ("import subprocess,sys,time;c=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(120)']);"
          "print(c.pid,flush=True);time.sleep(120)")


def family(process) -> tuple[int, int]:
    """A process and one it runs."""
    parent = process(PARENT)
    return parent.pid, int(parent.stdout.readline())


def device(broker, pid, said=None) -> Device:
    return Device("s-test", wire=Wire(broker, pid), measuring=lambda: None, say=(said if said is not None else []).append)


def ask(tmp_path, pid: int) -> Ask:
    model = tmp_path / "plumbing.gguf"
    model.write_bytes(b"GGUF")
    return Ask(purpose="plumbing", models=(str(model),), pid=pid, label="plumbing")


def test_a_reservation_waits_for_a_gpu_claim_and_takes_nothing_of_the_nodes_table(device_broker):
    broker, process = device_broker
    job, other = process().pid, process().pid
    broker.claim(gpu.CLAIM, other, {"purpose": "a training run"})
    refused = broker.claim(JOB, job, {"purpose": "scheduled job s-test"})
    assert not refused["granted"] and refused["pid"] == other
    broker.unclaim(gpu.CLAIM, other)
    held = broker.claim(JOB, job, {"purpose": "scheduled job s-test"})
    assert held["granted"] and held["reserves_device"]
    assert broker.claim(JOB, job, {"purpose": "scheduled job s-test"})["granted"], "asking again keeps it"
    assert not [lease for lease in broker.table.listing()["leases"] if lease["resources"][0]["type"] == "gpu"]
    assert not broker.claim(JOB, other, {"purpose": "another job"})["granted"]


def test_while_reserved_only_what_runs_under_the_holder_claims_the_gpu_or_starts_a_model(device_broker, tmp_path):
    broker, process = device_broker
    job, step = family(process)
    other = process().pid
    broker.claim(JOB, job, {"purpose": "scheduled job s-test"})
    refused = broker.claim(gpu.CLAIM, other, {"purpose": "somebody else"})
    assert not refused["granted"] and "scheduled job s-test" in refused["info"]["purpose"]
    with pytest.raises(BrokerError, match="model start timed out"):
        broker._reserve_model_start(other, "m", 0.2)
    with pytest.raises(BrokerError, match="scheduled job s-test"):
        broker.lease(ask(tmp_path, other), timeout=0.5)
    assert broker.claim(gpu.CLAIM, step, {"purpose": "the step"})["granted"]
    broker.unclaim(gpu.CLAIM, step)
    mine = broker._reserve_model_start(step, "m", 1.0)
    broker.unclaim(mine, step)
    granted = broker.lease(ask(tmp_path, step), timeout=30.0)
    broker.release(granted.lease)


def test_what_waits_for_the_reservation_to_end_never_stands_in_front_of_the_jobs_own_steps(device_broker, tmp_path):
    broker, process = device_broker
    job, step = family(process)
    other = process().pid
    broker.claim(JOB, job, {"purpose": "scheduled job s-test"})
    ended: list[object] = []

    def wait(call) -> threading.Thread:
        def run() -> None:
            try:
                ended.append(call())
            except (BrokerError, RuntimeError, ValueError) as why:
                ended.append(why)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    outside = [wait(lambda: broker.claim(gpu.CLAIM, other, {}, timeout=20.0)),
               wait(lambda: broker.lease(ask(tmp_path, other), timeout=20.0))]
    try:
        deadline = time.monotonic() + 10
        while not (broker.claim_waiters and broker.queue):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert broker.claim(gpu.CLAIM, step, {"purpose": "the step"})["granted"]
        broker.unclaim(gpu.CLAIM, step)
        granted = broker.lease(ask(tmp_path, step), timeout=10.0)
        assert not ended, "what is outside the job still waits"
        broker.release(granted.lease)
    finally:
        broker.unclaim(JOB, job)
        for thread in outside:
            thread.join(30)
    assert len(ended) == 2


def test_the_reservation_ends_with_its_process(device_broker):
    broker, process = device_broker
    holder = process()
    other = process().pid
    broker.claim(JOB, holder.pid, {"purpose": "scheduled job s-test"})
    holder.kill()
    holder.wait()
    assert broker.claim(gpu.CLAIM, other, {}, timeout=5.0)["granted"]


def test_a_server_in_use_is_never_stopped_and_the_job_waits_saying_who_holds_it(device_broker, tmp_path):
    broker, process = device_broker
    job, user = process().pid, process().pid
    granted = broker.lease(ask(tmp_path, user), timeout=30.0)
    why = device(broker, job).take(alone=True)
    assert f"pid {user}" in why and "in use" in why, why
    assert granted.port in broker.servers and JOB not in broker.claims


def test_a_job_that_measures_stops_a_server_nobody_holds_and_one_that_does_not_leaves_it(device_broker, tmp_path):
    broker, process = device_broker
    job, user = process().pid, process().pid
    granted = broker.lease(ask(tmp_path, user), timeout=30.0)
    broker.release(granted.lease)
    said: list[str] = []
    one = device(broker, job, said)
    assert one.take(alone=False) == "" and granted.port in broker.servers
    one.give_back()
    assert JOB not in broker.claims
    deadline = time.monotonic() + 30
    while (why := one.take(alone=True)) and time.monotonic() < deadline:
        time.sleep(1.0)
    assert why == "" and granted.port not in broker.servers and broker.claims[JOB]["pid"] == job
    assert any(f"port {granted.port}" in line for line in said)


def test_a_broker_that_does_not_know_reservations_is_not_trusted_and_is_asked_to_hand_over():
    calls: list[str] = []

    class Old:
        def claim(self, name, info, *, timeout=0.0):
            return {"granted": True, "pid": os.getpid(), "info": info}

        def unclaim(self, name):
            calls.append("unclaim")

        def replace_stale(self):
            calls.append("replace")

    why = Device("s-test", wire=Old(), measuring=lambda: None).take(alone=False)
    assert "older than scheduled jobs" in why and calls == ["unclaim", "replace"]


def test_a_measurement_under_way_and_a_broker_that_cannot_be_asked_are_reasons_to_wait(device_broker):
    broker, process = device_broker
    busy = Device("s-test", wire=Wire(broker, process().pid), measuring=lambda: {"pid": 1, "argv": ["speed"]})
    assert "a measurement holds the device" in busy.take(alone=True) and not broker.claims

    class Down:
        def claim(self, *_args, **_kwargs):
            raise BrokerError("no broker")

    assert "could not be asked: no broker" in Device("s-test", wire=Down(), measuring=lambda: None).take(alone=False)
