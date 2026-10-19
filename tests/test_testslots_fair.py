"""The broker admits in queue order: the first run takes every worker it can use, and the runs behind it wait their turn."""
from __future__ import annotations

from test_testslots import _records, _spawn, _wait_for_slot


def test_a_big_run_takes_every_worker_and_the_small_run_queued_behind_it_waits_its_turn(tmp_path):
    release = tmp_path / "release"
    holder = _spawn(tmp_path, 8, 8, release, "holder")
    processes = [holder]
    try:
        _wait_for_slot(tmp_path, holder, "holder", 8)
        big = _spawn(tmp_path, 8, 8, 1.5, "big")
        processes.append(big)
        _wait_for_slot(tmp_path, big, "big", 0)
        small = _spawn(tmp_path, 8, 1, 0.1, "small")
        processes.append(small)
        _wait_for_slot(tmp_path, small, "small", 0)
        release.touch()
        assert all(process.wait(timeout=60) == 0 for process in processes)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
    runs = {record["label"]: record for record in _records(tmp_path)}
    assert runs["big"]["workers"] == 8
    assert runs["small"]["start"] >= runs["big"]["end"] - 0.05


def test_a_lone_run_still_takes_every_worker(tmp_path):
    only = _spawn(tmp_path, 8, 8, 0.1, "only")
    assert only.wait(timeout=60) == 0
    assert _records(tmp_path)[0]["workers"] == 8
