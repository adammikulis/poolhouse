"""A changed local model file is pinned again only when a person serves it by name with `poolhouse-serve up`."""

from __future__ import annotations

import argparse
import hashlib

import pytest

from poolhouse import caller, home, mcp, sentinel
from poolhouse.sentinel import State
from poolhouse.serve import guarded, lifecycle_cli
from poolhouse.serve.guarded import SentinelRefused


class Reached(Exception):
    """The command got past pinning and would have asked the broker."""


@pytest.fixture
def person(monkeypatch):
    for marker in caller.AGENT_MARKERS:
        monkeypatch.delenv(marker, raising=False)


@pytest.fixture
def changed(tmp_path):
    folder = home.home() / "models"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "mine.gguf"
    path.write_bytes(b"GGUF" + bytes(4096))
    check(path)
    path.write_bytes(b"GGUF" + b"\x01" * 5000)
    return path


def check(path):
    return guarded.verify(path, state_file=home.home() / "servers.json", stop=lambda _p: None)


def digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pinned(path) -> str:
    return sentinel.default().manifest.pins()[str(path)].sha256


def up(monkeypatch, path, **extra):
    def reached(*_args, **_kwargs):
        raise Reached

    monkeypatch.setattr(lifecycle_cli.quant_guard, "enforce", lambda *_a, **_k: None)
    monkeypatch.setattr(lifecycle_cli.holding, "check_fits", reached)
    args = argparse.Namespace(model=str(path), draft="", mmproj="", iq="", **extra)
    with pytest.raises(Reached):
        lifecycle_cli._lease(args, argparse.Namespace(model=str(path), n_gpu_layers=0), None, chosen="", profile=None)


def test_a_person_serving_a_changed_file_by_name_pins_it_again_and_says_so(person, changed, monkeypatch, caplog):
    up(monkeypatch, changed)
    assert pinned(changed) == digest(changed) and check(changed) == ""
    assert "pins it again" in caplog.text and sentinel.default().bus.recent(kind="model.repinned")


def test_a_start_nobody_named_refuses_the_changed_file_and_leaves_it_where_it_is(changed):
    with pytest.raises(SentinelRefused, match="poolhouse-serve up"):
        check(changed)
    assert changed.exists() and sentinel.default().store.state_of("model", str(changed)) == State.CLEAR


def test_a_scan_holds_nothing_for_a_changed_file_that_was_pinned_when_served(changed):
    assert sentinel.default().scan(deep=True) == []
    assert changed.exists() and sentinel.default().store.state_of("model", str(changed)) == State.CLEAR


@pytest.mark.parametrize("marker", caller.AGENT_MARKERS)
def test_a_run_marked_as_an_agents_pins_nothing_again(person, changed, monkeypatch, marker):
    was = pinned(changed)
    monkeypatch.setenv(marker, "1")
    up(monkeypatch, changed)
    assert pinned(changed) == was


def test_the_serve_up_tool_asks_for_no_pinning_again(person, changed, monkeypatch):
    asked: list[list[str]] = []
    monkeypatch.setattr(mcp, "detached", lambda module, argv, **_kw: asked.append(argv) or {})
    mcp.serve_up(str(changed))
    assert "--no-repin" in asked[0]
    was = pinned(changed)
    up(monkeypatch, changed, no_repin=True)
    assert pinned(changed) == was


def test_a_downloaded_file_is_never_pinned_again(person, changed, monkeypatch):
    sentinel.default().manifest.pin(changed, "model", source="pull")
    changed.write_bytes(b"GGUF" + b"\x02" * 6000)
    was = pinned(changed)
    up(monkeypatch, changed)
    assert pinned(changed) == was
