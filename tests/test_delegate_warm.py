"""A delegation starts fast without setup: the server it leased stays up for the next one, the prompt every delegate of a lead shares
is the same text and carries no per-request line, and `warm` fills the server's prompt cache before the first task."""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import importlib
import socket
import time

import pytest

from poolhouse import (
    delegate,
    delegate_confined,
    delegate_store as store,
    harness,
    harnessing,
    hub,
)
from poolhouse.ownership import lease_file, recorded_servers
from poolhouse.serve import cli, holding, ops
from poolhouse.serve.backend import LlamaServerBackend, ServerFailed
from poolhouse.serve.broker import Broker
from poolhouse.serve.manager import ServerManager, serve
from poolhouse.testing.canned import canned_binary

pytest_plugins = ["serve_node"]

MIB = 1024 * 1024
iso_env = importlib.import_module("poolhouse.isolation.environment")


def a_delegate(name: str, lead: str = "claude-lead", edit: bool = False) -> store.Delegate:
    return store.Delegate(name, lead, "board", "read the tests", "/tmp/work", "Qwen3.8-Flash-Next-UD-IQ2_XS", "", "", edit)


def test_the_attribution_line_that_changes_every_request_is_turned_off():
    assert harness.environment("http://127.0.0.1:1", "model")["CLAUDE_CODE_ATTRIBUTION_HEADER"] == "0"
    assert iso_env.OFFLINE["CLAUDE_CODE_ATTRIBUTION_HEADER"] == "0"


@pytest.mark.parametrize("edit", [False, True])
def test_two_delegates_of_one_lead_start_from_the_same_system_prompt_and_differ_in_the_message(edit):
    one, two = a_delegate("claude-aaaaaa", edit=edit), a_delegate("claude-bbbbbb", edit=edit)
    assert delegate_confined.brief_of(one) == delegate_confined.brief_of(two)
    assert "claude-aaaaaa" not in delegate_confined.brief_of(one)
    assert one.message() != two.message() and "claude-aaaaaa" in one.message() and "read the tests" in one.message()


def test_a_sandboxed_editing_delegate_is_told_that_git_writes_are_refused_and_who_commits():
    here = a_delegate("claude-aaaaaa", edit=True)
    brief = delegate_confined.brief_of(here)
    assert "git commands that write to it (add, rm, mv and commit among them) are refused" in brief and "committed for you" not in brief
    treed = delegate_confined.brief_of(dataclasses.replace(here, branch="delegate/claude-aaaaaa"))
    assert "the files you change are committed for you" in treed and "claude-aaaaaa" not in treed


def test_warm_hands_a_delegate_the_trivial_task_and_nothing_else(monkeypatch):
    seen = []
    monkeypatch.setattr(delegate, "start", lambda args: seen.append(args) or 0)
    assert delegate.warm(argparse.Namespace(model="", edit=False)) == 0
    assert seen[0].task == delegate.WARM_TASK and seen[0].max_turns == 1 and not seen[0].edit and not seen[0].project


@pytest.fixture
def machine(tmp_path, monkeypatch, serve_leases, named_context):
    binary = canned_binary(tmp_path)
    made = {}

    def manager_for(binary_arg="", build=""):
        if "m" not in made:
            m = ServerManager(LlamaServerBackend(binary=binary), state_file=lease_file())
            m._broker = Broker(m, room=lambda: 65536 * MIB, scan=lambda: [])
            made["m"] = m
        return made["m"]

    monkeypatch.setattr(ops, "manager_for", manager_for)
    monkeypatch.setattr(hub, "room", lambda: 65536 * MIB)
    monkeypatch.setattr(hub, "total_memory", lambda: 64 * 1024 * MIB)
    yield manager_for()
    for hold in holding.holds():
        holding.down([hold], manager=manager_for(), wait_s=20)
    made["m"].stop_all()
    cli.main(["down", "--orphans"])


def listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def a_model(tmp_path):
    path = tmp_path / "Qwen3.8-27B-UD-Q4_K_XL.gguf"
    with path.open("wb") as out:
        out.write(b"GGUF" + b"\x00" * 64)
        out.truncate(600 * MIB)
    return str(path)


def test_a_server_a_delegate_started_is_up_for_the_next_one_and_a_plain_lease_still_ends_with_its_block(machine, tmp_path):
    model = a_model(tmp_path)
    with serve(model, manager=machine, context=4096, keep=True) as kept:
        port = kept.port
    assert listening(port) and port in recorded_servers(lease_file())
    assert cli.main(["down", str(port)]) == 0
    with serve(model, manager=machine, context=4096) as plain:
        gone = plain.port
    deadline = time.monotonic() + 15
    while listening(gone) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not listening(gone)


def test_a_lease_that_only_takes_a_server_already_up_starts_none(machine, tmp_path):
    model = a_model(tmp_path)
    with pytest.raises(ValueError, match="starts none"), delegate_confined.leased(model, lambda _line: None, adopt_only=True):
        pytest.fail("a server was leased")
    with pytest.raises(ServerFailed, match="starts none"), serve(model, manager=machine, context=4096, adopt_only=True):
        pytest.fail("a server was started")
    assert recorded_servers(lease_file()) == {}
    with serve(model, manager=machine, context=4096, keep=True) as kept:
        port = kept.port
    with serve(model, port=port, manager=machine, context=4096, adopt_only=True) as taken:
        assert (taken.port, taken.adopted) == (port, True)
    assert listening(port)
    assert cli.main(["down", str(port)]) == 0
    with pytest.raises(ServerFailed, match="starts none"), serve(model, port=port, manager=machine, context=4096, adopt_only=True):
        pytest.fail("the server its owner stopped was started again")
    assert not listening(port)


def test_only_a_session_cards_delegate_leases_a_server_already_up_and_nothing_else(monkeypatch):
    asked: list[dict] = []

    @contextlib.contextmanager
    def leased(_model, _say, **how):
        asked.append(how)
        raise RuntimeError("the lease was asked for")
        yield

    monkeypatch.setattr(delegate_confined, "leased", leased)
    monkeypatch.setattr(harnessing, "binary_for", lambda _name, pinned=False: "/bin/true")
    for name, card in (("claude-plain1", ""), ("claude-card01", "junk-card")):
        rec = dataclasses.replace(a_delegate(name, edit=True), card=card)
        store.save(rec)
        with pytest.raises(RuntimeError, match="the lease was asked for"):
            delegate_confined.converse(rec, None, None, lambda _line: None)
    assert asked == [{}, {"adopt_only": True}]
