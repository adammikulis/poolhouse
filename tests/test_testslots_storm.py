"""A deep queue costs the broker bounded work and a freed worker is granted at once."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "testslots.py"
WAITERS = 296
HOLDERS = 4


def load():
    spec = importlib.util.spec_from_file_location("testslots", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["testslots"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def broker(tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_TEST_SLOTS_DIR", str(tmp_path / "slots"))
    monkeypatch.setenv("DEV_TEST_BUDGET", str(HOLDERS))
    monkeypatch.setenv("DEV_TEST_WAIT_S", "60")
    for name in ("DEV_TEST_LEASE", "DEV_TEST_REMOTE_LEASE", "DEV_TEST_CLASS"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "slots").mkdir()
    return load()


def quiet(_message):
    return None


def test_three_hundred_waiters_take_the_mutex_a_bounded_number_of_times_and_a_freed_worker_is_granted_at_once(broker, tmp_path, monkeypatch):
    taken = []
    real = broker._mutex

    @contextlib.contextmanager
    def counted(directory, cancellable=True):
        taken.append(1)
        with real(directory, cancellable):
            yield

    monkeypatch.setattr(broker, "_mutex", counted)
    release, holding, granted = threading.Event(), [], []

    def holder():
        broker.EXPORT_LEASE.set(False)
        with broker.lease(1, 1, say=quiet):
            holding.append(time.monotonic())
            release.wait(60)

    def waiter():
        broker.EXPORT_LEASE.set(False)
        with broker.lease(1, 1, say=quiet):
            granted.append(time.monotonic())
            time.sleep(0.01)

    threads = [threading.Thread(target=holder) for _ in range(HOLDERS)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 20
    while len(holding) < HOLDERS:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    waiting = [threading.Thread(target=waiter) for _ in range(WAITERS)]
    for thread in waiting:
        thread.start()
    while len(list((tmp_path / "slots").glob("*.slot"))) < HOLDERS + WAITERS:
        assert time.monotonic() < deadline + 20
        time.sleep(0.05)
    time.sleep(0.5)
    before = len(taken)
    time.sleep(1.5)
    during = len(taken) - before
    freed = time.monotonic()
    release.set()
    for thread in (*threads, *waiting):
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in (*threads, *waiting)) and len(granted) == WAITERS
    storm = WAITERS * 100 * 1.5
    assert during < storm / 10, f"{during} mutex acquisitions in 1.5 s with {WAITERS} waiting (a 100 Hz poll takes {storm:.0f})"
    assert min(granted) - freed < 2.0, f"first waiter granted {min(granted) - freed:.2f}s after the workers were freed"
    assert max(granted) - freed < 30.0
    assert broker.status()["in_use"] == 0


def test_a_holder_that_died_after_being_seen_alive_frees_its_workers_at_the_next_grant(broker, tmp_path):
    from poolhouse.lock import take

    holder = tmp_path / "slots" / "0-dies.slot"
    descriptor = os.open(holder, os.O_RDWR | os.O_CREAT, 0o600)
    assert take(descriptor)
    holder.write_text(json.dumps({"label": "dies", "pid": os.getpid(), "want": HOLDERS, "minimum": HOLDERS, "granted": HOLDERS, "since": time.time(),
                                  "version": 2, "class": "interactive", "estimate": None, "token": "d" * 48, "agent": {}}))
    with broker._mutex(tmp_path / "slots"):
        assert [slot.data["label"] for slot in broker._read(tmp_path / "slots")] == ["dies"]
    os.close(descriptor)
    started = time.monotonic()
    with broker.lease(1, 1, say=quiet):
        assert time.monotonic() - started < 3
    assert not holder.exists()


def test_status_names_the_request_first_in_line_and_why_it_waits(broker):
    release, held = threading.Event(), threading.Event()

    def hold():
        broker.EXPORT_LEASE.set(False)
        with broker.lease(HOLDERS, HOLDERS, label="holds everything", say=quiet):
            held.set()
            release.wait(30)

    def queue_one():
        broker.EXPORT_LEASE.set(False)
        with broker.lease(1, 1, label="queued request", say=quiet):
            pass

    holder, queued = threading.Thread(target=hold), threading.Thread(target=queue_one)
    holder.start()
    assert held.wait(10)
    queued.start()
    deadline = time.monotonic() + 10
    while not broker.status()["first"]:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    line = broker.status()["first"]
    release.set()
    holder.join(10)
    queued.join(10)
    assert "first in line" in line and "queued request" in line and f"0 of {HOLDERS} are free" in line


def test_a_cancelled_wait_for_the_mutex_stops(broker, tmp_path):
    directory = tmp_path / "slots"
    inside, leave = threading.Event(), threading.Event()

    def hold_mutex():
        with broker._mutex(directory):
            inside.set()
            leave.wait(10)

    holder = threading.Thread(target=hold_mutex)
    holder.start()
    assert inside.wait(5)

    def cancel():
        raise ConnectionAbortedError("worker disconnected")

    token = broker.CHECK_CANCELLED.set(cancel)
    try:
        with pytest.raises(ConnectionAbortedError), broker._mutex(directory):
            pass
    finally:
        broker.CHECK_CANCELLED.reset(token)
        leave.set()
        holder.join()


def test_a_lease_whose_connection_closed_still_removes_its_record_when_the_mutex_is_busy(broker, tmp_path):
    directory = tmp_path / "slots"
    inside = threading.Event()

    def hold_mutex():
        with broker._mutex(directory):
            inside.set()
            time.sleep(0.4)

    def gone():
        raise ConnectionAbortedError("worker disconnected")

    broker.EXPORT_LEASE.set(False)
    with broker.lease(1, 1, say=quiet):
        token = broker.CHECK_CANCELLED.set(gone)
        holder = threading.Thread(target=hold_mutex)
        holder.start()
        assert inside.wait(5)
    broker.CHECK_CANCELLED.reset(token)
    holder.join()
    assert not list(directory.glob("*.slot")) and broker.status()["in_use"] == 0
