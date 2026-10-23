"""A server that is starting ends with the start that was cut short: a test's timer, an interrupt or an error while it loads."""

import sys

import pytest

from poolhouse.serve import backend, grant
from poolhouse.serve.backend import Lease, ServerFailed, launch

SLEEPER = [sys.executable, "-c", "import time; time.sleep(120)"]


def started(tmp_path):
    """`launch` of a sleeping process, on a lease made the way the broker's grant makes one."""
    with grant.broker_grant():
        return launch(SLEEPER, Lease(port=1, owner_pid=1, state_file="x"), log_path=tmp_path / "log", timeout=30.0, env={})


@pytest.mark.parametrize("cut", [KeyboardInterrupt(), OverflowError("timestamp out of range"), TimeoutError("test timer")])
def test_a_start_cut_short_while_the_server_loads_ends_the_server(tmp_path, monkeypatch, cut):
    alive = []

    def health(url, *, timeout, is_alive):
        alive.append(is_alive)
        raise cut

    monkeypatch.setattr(backend, "wait_for_health", health)
    with pytest.raises(type(cut)):
        started(tmp_path)
    assert alive and alive[0]() is False, "the server is still running after the start that began it was cut short"


def test_a_server_that_never_answers_still_ends_and_is_named(tmp_path, monkeypatch):
    alive = []

    def health(url, *, timeout, is_alive):
        alive.append(is_alive)
        return False

    monkeypatch.setattr(backend, "wait_for_health", health)
    with pytest.raises(ServerFailed, match="did not become healthy"):
        started(tmp_path)
    assert alive[0]() is False
