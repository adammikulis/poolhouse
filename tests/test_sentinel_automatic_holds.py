"""A quarantine ends by a fixed policy and nothing waits for a release: no request is raised, no dialog opens, and the hold ends by itself
when the subject verifies again or when it lapses."""

from __future__ import annotations

import pytest

from poolhouse import desktop
from poolhouse.sentinel import Sentinel, State
from poolhouse.sentinel.core import HOLD_S

GOOD = b"GGUF" + b"g" * 4096
BAD = b"GGUF" + b"b" * 4096


@pytest.fixture
def world(tmp_path, monkeypatch):
    models = tmp_path / "models"
    models.mkdir()
    now = [1_000_000.0]
    node = Sentinel(tmp_path / "s", roots=[models], clock=lambda: now[0])
    monkeypatch.setattr(desktop, "choose", lambda *a, **k: pytest.fail("a dialog was opened"))
    path = models / "m.gguf"
    path.write_bytes(GOOD)
    node.manifest.pin(path, "model", source="hf://owner/repo/m.gguf")
    return node, path, now


def held(node, path):
    return [r for r in node.store.records() if r.key == str(path)]


def test_a_changed_model_is_refused_and_held_and_nobody_is_asked(world):
    node, path, _ = world
    path.write_bytes(BAD)
    assert node.verify_before_load(path) is False
    [record] = held(node, path)
    assert record.state == State.QUARANTINED and not path.exists(), "the changed file is moved aside, never loaded"
    node.scan(deep=True)
    assert node.verify_before_load(path) is False, "still refused while the file is not what was pinned"


def test_a_model_fetched_again_is_released_at_the_next_scan_and_the_held_copy_is_deleted(world):
    node, path, _ = world
    path.write_bytes(BAD)
    node.verify_before_load(path)
    [record] = held(node, path)
    moved = record.action
    path.write_bytes(GOOD)                       # the pull fetched the pinned bytes again
    node.scan(deep=True)
    [again] = held(node, path)
    assert again.state == State.RELEASED and again.purged, "the bad copy was deleted, not put back over the good file"
    assert path.read_bytes() == GOOD and moved
    assert node.verify_before_load(path) is True


def test_a_hold_on_a_changed_file_lapses_by_itself_after_the_week(world):
    node, path, now = world
    path.write_bytes(BAD)
    node.verify_before_load(path)
    now[0] += HOLD_S["file"] - 10
    node.scan()
    assert held(node, path)[0].state == State.QUARANTINED
    now[0] += 20
    node.scan()
    assert held(node, path)[0].state == State.RELEASED, "nobody released it"
    assert node.verify_before_load(path) is False, "ending the hold lets nothing unverified run: a load still checks the file against its pin"


def test_a_hold_on_a_member_lapses_after_an_hour_and_is_held_again_if_it_misbehaves_again(world):
    node, _, now = world
    record = node.store.quarantine(("member", "10.2.2.2"), "forged traffic", None)
    now[0] += HOLD_S["other"] - 10
    node.scan()
    assert node.store.get(record.id).state == State.QUARANTINED
    now[0] += 20
    node.scan()
    assert node.store.get(record.id).state == State.RELEASED
    assert [e.kind for e in node.bus.recent(kind="sentinel.hold_ended")] == ["sentinel.hold_ended"]


def test_a_hold_starts_no_background_thread_so_nothing_can_raise_a_request_in_a_later_tests_home(world):
    """The isolation leak: a hold used to start a daemon thread that raised its request when it ran, which could be in the next test's state root."""
    import threading

    node, path, _ = world
    before = {t.ident for t in threading.enumerate()}
    path.write_bytes(BAD)
    node.verify_before_load(path)
    node.scan(deep=True)
    started = [t.name for t in threading.enumerate() if t.ident not in before]
    assert started == [], "a hold works inline and ends by itself"
