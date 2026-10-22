"""A process that exits or turns unreadable mid-scan never ends the scan.

The seam is psutil's process iterator, faithful to its shape: with ``attrs`` the attributes
are read inside the generator (an error there ends the generator), without them each
process is read by the caller. No model behaviour is under test.
"""

from __future__ import annotations

import types

import psutil
import pytest

from poolhouse.serve import ports, process


class _Row:
    def __init__(self, pid, argv, error=None, listening=None):
        self.pid, self.argv, self.error, self.listening = pid, argv, error, listening
        self.info = {}

    def as_dict(self, attrs=None, ad_value=None):
        if self.error is not None:
            raise self.error
        known = {"pid": self.pid, "name": self.argv[0].rsplit("/", 1)[-1], "cmdline": self.argv,
                 "memory_info": types.SimpleNamespace(rss=1 << 20)}
        return {name: known.get(name, ad_value) for name in attrs}

    def net_connections(self, kind="inet"):
        if self.error is not None:
            raise self.error
        laddr = types.SimpleNamespace(port=self.listening, ip="127.0.0.1")
        return [types.SimpleNamespace(status=psutil.CONN_LISTEN, laddr=laddr)] if self.listening else []

    def status(self):
        return "running"


_MACOS = SystemError("<built-in function proc_cmdline> returned a result with an exception set")


@pytest.fixture
def machine(monkeypatch):
    rows = [_Row(1, ["/x/llama-server", "--port", "8081"], listening=8081),
            _Row(2, ["/bin/vanishing"], error=_MACOS),
            _Row(3, ["/bin/gone"], error=psutil.NoSuchProcess(3)),
            _Row(4, ["/bin/zombie"], error=psutil.ZombieProcess(4)),
            _Row(5, ["/bin/locked"], error=psutil.AccessDenied(5)),
            _Row(6, ["/x/llama-server", "--port", "8082"], listening=8082)]

    def process_iter(attrs=None, ad_value=None):
        for row in rows:
            if attrs:
                row.info = row.as_dict(attrs, ad_value)
            yield row

    monkeypatch.setattr(psutil, "process_iter", process_iter)
    monkeypatch.setattr(process, "footprint_of", lambda _proc: 0)
    return rows


def test_every_server_skips_a_process_that_vanished_mid_scan(machine):
    assert [one["port"] for one in process.every_server()] == [8081, 8082]


def test_machine_memory_skips_a_process_that_vanished_mid_scan(machine, monkeypatch):
    monkeypatch.setattr(psutil, "virtual_memory",
                        lambda: types.SimpleNamespace(total=8 << 30, available=4 << 30, wired=0))
    assert process.machine_memory()["servers"] == 2 << 20


def test_server_pids_on_port_skips_a_process_that_vanished_mid_scan(machine):
    assert ports.server_pids_on_port(8082) == [6]


def test_connections_of_a_process_that_vanished_are_none(machine):
    from poolhouse import procscan

    assert procscan.connections(machine[1]) == [] and len(procscan.connections(machine[0])) == 1


def test_ollama_listeners_skip_a_process_that_vanished_mid_scan(machine):
    from poolhouse.client import ollama

    assert [row.pid for row in ollama.listeners(8082)] == [6]


def test_a_real_failure_is_not_hidden(monkeypatch):
    def broken(attrs=None, ad_value=None):
        raise RuntimeError("the machine is on fire")
        yield

    monkeypatch.setattr(psutil, "process_iter", broken)
    with pytest.raises(RuntimeError, match="on fire"):
        process.every_server()
