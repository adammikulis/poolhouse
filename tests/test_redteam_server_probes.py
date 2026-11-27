"""Red-team: two probes that meet something an attacker may control, the key probe of a model server and the help probe of a harness binary."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from poolhouse import opencode
from poolhouse.client import refuses_key

pytestmark = pytest.mark.redteam


@contextmanager
def serving(status: int, headers: dict[str, str]) -> Iterator[tuple[str, list[str]]]:
    """A loopback server that answers every request with ``status`` and ``headers``: its address and the paths it was asked for."""
    asked: list[str] = []

    class Answer(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            asked.append(self.path)
            self.send_response(status)
            for name, value in {**headers, "Content-Length": "0"}.items():
                self.send_header(name, value)
            self.end_headers()

        def log_message(self, *_args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Answer)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", asked
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_the_key_probe_follows_no_redirect_away_from_the_server_asked():
    with serving(401, {}) as (elsewhere, reached), serving(302, {"Location": f"{elsewhere}/taken"}) as (hostile, asked):
        assert refuses_key(hostile) is False, "another address's answer says nothing about this server"
    assert asked == ["/props", "/v1/models"] and reached == []


def test_a_server_that_refuses_the_key_still_counts():
    with serving(403, {}) as (refusing, _asked):
        assert refuses_key(refusing) is True


def test_the_harness_help_probe_runs_the_one_binary_with_one_fixed_word(monkeypatch):
    ran = []

    def record(argv, **kwargs):  # plumbing only: no harness binary is run
        ran.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, b"  --standalone  run alone\n", b"")

    monkeypatch.setattr(opencode.subprocess, "run", record)
    binary = "/opt/open code; touch pwned $(touch also-pwned)"
    assert opencode._own_server(binary, ["$(touch from-rest)", "; touch from-rest-too"]) == ["--standalone"]
    [(argv, kwargs)] = ran
    assert argv == [binary, "--help"], "the binary is one argument and nothing of the caller's words reaches the process"
    assert not kwargs.get("shell")
